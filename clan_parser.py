#!/usr/bin/env python3
"""
FSC: combined bot.

Step 1 (formerly fsc_bot):    openfront.io -> clan member table (printed to the console)
Step 2 (formerly fsc_parser): ofstats.io -> detailed stats
                           * ALL players from the table
                           * + players from TRACKED_PLAYERS (always, regardless of the leaderboard)
Everything is saved to ONE file: fsc_stats.json

    pip install requests
    python clan_parser.py
    python clan_parser.py -o other.json --workers 6 --delay 0.5
    python clan_parser.py --no-stats          # table only, no requests to ofstats
    python clan_parser.py --browser           # step 2 via Playwright, if requests get blocked
                                               # (pip install playwright;
                                               #  python -m playwright install chromium)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

import requests

# ═══════════════════════════════════════════════════════════════════════════════
#   PLAYERS TO ALWAYS PARSE (even if they're not on the leaderboard/table)
# ═══════════════════════════════════════════════════════════════════════════════

TRACKED_PLAYERS = []   # e.g. {"id": "abc12345", "name": "nickname"}

# ═════════════════════════════ SETTINGS: TABLE ═════════════════════════════

CLAN = "FSC"
MIN_WINS = 20
MAX_WORKERS = 8
OUTPUT_JSON = "fsc_stats.json"           # the only output file

BASE = "https://api.openfront.io/public"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

RANKS = [
    (0.05, "GOD OF FULLSENDING"),
    (0.10, "GRANDFULLSENDER"),
    (0.15, "MASTER OF FULLSENDING"),
    (0.20, "FULLSEND TRAINER"),
    (0.30, "ELITE FULLSENDER"),
    (0.40, "TRUE FULLSENDER"),
    (0.50, "FULLSENDER"),
    (0.60, "MINI FULLSENDER"),
    (0.70, "SENDER"),
    (0.80, "HALF SENDER"),
]
LOWEST_RANK = "not rated"

# ═════════════════════════════ SETTINGS: STATS ═════════════════════════════

OFS_API = "https://api.ofstats.io"
OFS_SITE = "https://ofstats.io"

OFS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": OFS_SITE,
    "Referer": OFS_SITE + "/",
}


# ═══════════════════════════════════════════════════════════════════════════════
#                                STEP 1: TABLE
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_json(url, params=None, retries=6):
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
        except requests.RequestException:
            time.sleep(2 * attempt)
            continue
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 2 * attempt)))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"failed to load {url}")


def fetch_all_members():
    members, seen = [], set()
    page = 1
    while True:
        data = fetch_json(f"{BASE}/clan/{CLAN}/members", {"page": page})
        results = data.get("results", [])
        total = data.get("total", 0)

        for m in results:
            pid = m.get("publicId")
            if pid and pid not in seen:
                seen.add(pid)
                members.append(m)

        print(f"Players loaded: {len(members)}/{total} (page {page})", end="\r", flush=True)

        if not results or len(members) >= total:
            break
        page += 1
        time.sleep(0.1)

    print(" " * 60, end="\r")
    return members, total


def clean_name(name):
    return re.sub(r"[.#]\d{4}$", "", " ".join(str(name).split()))


def stat(m, key, field):
    return ((m.get("stats") or {}).get(key) or {}).get(field) or 0


def total_wins(m):
    return max(0, stat(m, "total", "wins") - stat(m, "hvn", "wins"))


def total_losses(m):
    return max(0, stat(m, "total", "losses") - stat(m, "hvn", "losses"))


def resolve_name(m):
    name = (m.get("username") or "").strip()
    if name:
        return clean_name(name)

    pid = m["publicId"]
    try:
        prof = fetch_json(f"{BASE}/player/{pid}")
        name = (prof.get("username") or "").strip()
        if name:
            return clean_name(name)
    except Exception:
        pass

    try:
        games = fetch_json(f"{BASE}/player/{pid}/games")
        for g in games.get("results", []):
            u = (g.get("username") or "").strip()
            if u:
                return clean_name(u)
    except Exception:
        pass

    return pid


def rank_for(share):
    for limit, name in RANKS:
        if share <= limit:
            return name
    return LOWEST_RANK


def disp_len(s):
    w = 0
    for ch in s:
        if unicodedata.combining(ch) or ch in ("\ufe0f", "\u200d"):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s, width):
    return s + " " * (width - disp_len(s))


def build_table():
    """Step 1. Returns (players, members, total) and prints the table."""
    print("")
    members, total = fetch_all_members()

    selected = [m for m in members if total_wins(m) > MIN_WINS]

    need_lookup = sum(1 for m in selected if not (m.get("username") or "").strip())
    if need_lookup:
        print(f"Looking up nicknames for {need_lookup} players with no nickname...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        names = list(pool.map(resolve_name, selected))

    players = []
    for name, m in zip(names, selected):
        w, l = total_wins(m), total_losses(m)
        ffa = stat(m, "ffa", "wins")
        team = stat(m, "team", "wins")
        ranked = stat(m, "ranked", "wins")

        wr_value = round(w / (w + l) * 100, 1) if (w + l) else 0.0
        wr = f"{wr_value:.1f}%" if (w + l) else "-"

        points = round((ffa * 2.0 + team + ranked * 1.5) * wr_value / 10, 1)

        players.append({"id": m["publicId"], "name": name,
                        "ffa": ffa, "team": team, "ranked": ranked,
                        "w": w, "l": l, "wr": wr, "wr_value": wr_value, "points": points})

    players.sort(key=lambda p: (p["points"], p["w"]), reverse=True)

    n = len(players)
    for p in players:
        above = sum(1 for q in players if q["points"] > p["points"])
        p["share"] = (above + 1) / n
        p["rank"] = rank_for(p["share"])

    headers = ["#", "Player", "FFA wins", "Teams wins", "Ranked wins",
               "W total", "L total", "Winrate %", "Top %", "Rank", "Points"]
    table = [headers] + [
        [str(i), p["name"], str(p["ffa"]), str(p["team"]), str(p["ranked"]),
         str(p["w"]), str(p["l"]), p["wr"], f"{p['share'] * 100:.1f}%",
         p["rank"], f"{p['points']:.0f}"]
        for i, p in enumerate(players, 1)
    ]
    widths = [max(disp_len(row[i]) for row in table) for i in range(len(headers))]

    print()
    for i, row in enumerate(table):
        print(" | ".join(pad(c, widths[j]) for j, c in enumerate(row)))
        if i == 0:
            print("-+-".join("-" * w for w in widths))

    print(f"\nPlayers in the table: {len(players)} out of {len(members)} ({total} in the clan)\n")
    return players, members, total


# ═══════════════════════════════════════════════════════════════════════════════
#                       STEP 2: PLAYER STATS (ofstats.io)
# ═══════════════════════════════════════════════════════════════════════════════

def pct(part, whole, nd=1):
    return round(100.0 * part / whole, nd) if whole else None


def rnd(x, nd=1):
    return None if x is None else round(x, nd)


def fmt_duration(secs):
    if secs is None:
        return None
    h, rem = divmod(int(secs), 3600)
    return f"{h}h {rem // 60}m"


def tier_from_rating(rating, tiers):
    """Tier by rating: tiers go from highest to lowest, the last one has cutoff = None."""
    if rating is None:
        return None
    for t in tiers:
        if t.get("cutoffRating") is None or rating >= t["cutoffRating"]:
            return t["tier"]
    return None


def season_blocks(rt: dict) -> list[dict]:
    """All season blocks: season0, season1, ... (seasonN keys holding a dict)."""
    return [v for k, v in rt.items() if re.fullmatch(r"season\d+", k) and isinstance(v, dict)]


def build_stats(pl: dict, rt: dict) -> dict:
    """pl — response of /players/{id}; rt — response of /rating/player/of:{id}."""
    pl, rt = pl or {}, rt or {}
    tiers = rt.get("tiers") or []
    order = [t["tier"] for t in tiers]                      # highest -> lowest
    seasons = season_blocks(rt)

    # best leaderboard place: lower number = higher place
    places = [s.get("peakRank") for s in seasons] + [rt.get("peakRank"), rt.get("rank")]
    places = [p for p in places if p]
    highest_place = min(places) if places else None

    # peak rating across all seasons
    ratings = [s.get("peakRating") for s in seasons] + [rt.get("peakRating"), rt.get("rating")]
    ratings = [r for r in ratings if r is not None]
    peak_rating = max(ratings) if ratings else None

    # best tier across all seasons
    cand = [rt.get("tier"), rt.get("lastHeldTier")]
    for s in seasons:
        cand += [s.get("tier"), s.get("lastHeldTier"), tier_from_rating(s.get("peakRating"), tiers)]
    cand += [h.get("tier") for h in rt.get("history") or []]
    cand += [tier_from_rating(rt.get("peakRating"), tiers)]
    cand = [t for t in cand if t in order]
    highest_tier = min(cand, key=order.index) if cand else None

    # average lobby size over the latest (up to 100) ranked games
    games_list = (rt.get("games") or [])[:100]
    lobbies = [g["lobbyHumans"] for g in games_list if g.get("lobbyHumans") is not None]

    # overall stats
    games, wins = pl.get("gamesPlayed"), pl.get("wins")
    secs = pl.get("timePlayedSecs")

    # per mode
    by_mode = {m["mode"]: m for m in pl.get("modes") or []}

    def mode_block(key):
        m = by_mode.get(key) or {}
        return {"games": m.get("games", 0), "win_rate_pct": pct(m.get("wins", 0), m.get("games"))}

    return {
        "highest_leaderboard_place": highest_place,
        "highest_tier": highest_tier,
        "peak_rating": rnd(peak_rating, 0),
        "avg_lobby_size": rnd(sum(lobbies) / len(lobbies), 0) if lobbies else None,
        "avg_lobby_size_games": len(lobbies),
        "all_modes": {
            "win_rate_pct": pct(wins, games),
            "games_played": games,
            "wins": wins,
            "losses": pl.get("losses"),
        },
        "time_played": fmt_duration(secs),
        "time_played_hours": rnd(secs / 3600, 1) if secs is not None else None,
        "ffa": mode_block("Free For All"),
        "team": mode_block("Team"),
        "ranked": mode_block("Ranked"),
    }


class Fetcher:
    """Regular requests (requests) or, with --browser, requests via Playwright."""

    def __init__(self, use_browser: bool, timeout: int = 30):
        self.timeout = timeout
        self.use_browser = use_browser
        self._local = threading.local()          # separate requests session per thread
        if use_browser:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._ctx = self._browser.new_context(user_agent=OFS_HEADERS["User-Agent"])
            page = self._ctx.new_page()
            page.goto(OFS_SITE, wait_until="domcontentloaded", timeout=timeout * 1000)
            time.sleep(1.5)

    def _session(self):
        s = getattr(self._local, "s", None)
        if s is None:
            s = requests.Session()
            s.headers.update(OFS_HEADERS)
            self._local.s = s
        return s

    def get_json(self, url: str, retries: int = 4):
        last = None
        for attempt in range(1, retries + 1):
            wait = 1.5 * attempt
            try:
                if self.use_browser:
                    r = self._ctx.request.get(url, timeout=self.timeout * 1000)
                    ok, status, headers = r.ok, r.status, r.headers
                else:
                    r = self._session().get(url, timeout=self.timeout)
                    ok, status, headers = r.ok, r.status_code, r.headers
                if ok:
                    return r.json()
                last = f"HTTP {status}"
                if status == 429:
                    try:
                        wait = max(wait, float(headers.get("retry-after", wait)))
                    except (TypeError, ValueError):
                        pass
                elif status in (400, 401, 403, 404):
                    break
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
            time.sleep(wait)
        raise RuntimeError(f"{url} -> {last}")

    def close(self):
        if self.use_browser:
            self._browser.close()
            self._pw.stop()


def collect_stats(ids, use_browser, workers, delay):
    """Fetch stats from ofstats.io for a list of ids. Returns {id: stats | {"error": ...}}."""
    total = len(ids)
    done = [0]
    lock = threading.Lock()
    fetcher = Fetcher(use_browser)

    def work(pid):
        try:
            pl = fetcher.get_json(f"{OFS_API}/players/{pid}")
            time.sleep(delay)
            rt = fetcher.get_json(f"{OFS_API}/rating/player/{quote('of:' + pid, safe='')}?absent=null")
            res = build_stats(pl, rt)
        except Exception as e:  # noqa: BLE001
            res = {"error": str(e)}
        time.sleep(delay)
        with lock:
            done[0] += 1
            print(f"[*] Stats: {done[0]}/{total}", end="\r", file=sys.stderr, flush=True)
        return pid, res

    try:
        # Playwright (sync) is not thread-safe -> strictly 1 thread in browser mode
        w = 1 if use_browser else max(1, workers)
        with ThreadPoolExecutor(max_workers=w) as pool:
            results = dict(pool.map(work, ids))
    finally:
        fetcher.close()
    print(" " * 60, end="\r", file=sys.stderr)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
#                         BUILDING THE SINGLE JSON AND MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def save_all(players, members, total, stats, path):
    """Single file: table + stats + a separate tracked_players block."""
    tracked_ids = {t["id"] for t in TRACKED_PLAYERS}
    place_by_id = {p["id"]: i for i, p in enumerate(players, 1)}
    by_id = {p["id"]: p for p in players}

    table_players = [
        {
            "place": i,
            "id": p["id"],
            "name": p["name"],
            "tracked": p["id"] in tracked_ids,
            "ffa_wins": p["ffa"],
            "team_wins": p["team"],
            "ranked_wins": p["ranked"],
            "wins_total": p["w"],
            "losses_total": p["l"],
            "winrate_percent": p["wr_value"] if (p["w"] + p["l"]) else None,
            "top_percent": round(p["share"] * 100, 1),
            "rank": p["rank"],
            "points": round(p["points"]),
            "stats": stats.get(p["id"]),
        }
        for i, p in enumerate(players, 1)
    ]

    tracked = []
    for t in TRACKED_PLAYERS:
        p = by_id.get(t["id"])
        tracked.append({
            "id": t["id"],
            "name": t["name"],
            "in_table": p is not None,
            "table_place": place_by_id.get(t["id"]),
            "table_rank": p["rank"] if p else None,
            "table_points": round(p["points"]) if p else None,
            "table_top_percent": round(p["share"] * 100, 1) if p else None,
            "stats": stats.get(t["id"]),
        })

    errors = sum(1 for s in stats.values() if "error" in s)
    data = {
        "clan": CLAN,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "clan_total_members": total,          # total number of people in the clan (per the API)
        "members_loaded": len(members),       # how many were actually loaded
        "players_in_table": len(players),     # how many made it into the table (after the MIN_WINS filter)
        "min_wins_filter": MIN_WINS,
        "stats_source": OFS_SITE,
        "stats_parsed": len(stats) - errors,
        "stats_failed": errors,
        "tracked_players": tracked,           # TRACKED_PLAYERS — always included, regardless of the leaderboard
        "players": table_players,             # everyone from the table
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="FSC: clan table (openfront) + player stats (ofstats)")
    ap.add_argument("-o", "--out", default=OUTPUT_JSON, help=f"output JSON (default {OUTPUT_JSON})")
    ap.add_argument("--browser", action="store_true", help="step 2 via Playwright")
    ap.add_argument("--delay", type=float, default=0.7, help="pause between ofstats requests, sec")
    ap.add_argument("--workers", type=int, default=4, help="threads for ofstats (default 4)")
    ap.add_argument("--no-stats", action="store_true", help="table only, skip step 2")
    args = ap.parse_args()

    # ── step 1: table ──
    players, members, total = build_table()

    # ── step 2: stats = TRACKED_PLAYERS + everyone from the table (no duplicates) ──
    stats = {}
    if not args.no_stats:
        ids = list(dict.fromkeys([t["id"] for t in TRACKED_PLAYERS] + [p["id"] for p in players]))
        print(f"Parsing ofstats.io stats: {len(ids)} players "
              f"({len(TRACKED_PLAYERS)} from TRACKED_PLAYERS + {len(players)} from the table)...",
              file=sys.stderr)
        stats = collect_stats(ids, args.browser, args.workers, args.delay)

    errors = save_all(players, members, total, stats, args.out)

    print(f"Members: {len(players)} in table / {len(members)} loaded / {total} in the clan")
    if not args.no_stats:
        print(f"Stats parsed: {len(stats) - errors}/{len(stats)}" + (f" (errors: {errors})" if errors else ""))
    print(f"Saved to {args.out}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())