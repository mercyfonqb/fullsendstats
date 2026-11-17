import json
import logging
import re
import os
from pathlib import Path

import discord

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GUILD_ID = 1569558622956945488
STATS_FILE = Path(__file__).with_name("fsc_stats.json")
PLAYERS_FILE = Path(__file__).with_name("discord_players.json")
THREAD_ID = 1569558624508841986

RANK_ROLE_NAMES = {
    "GOD OF FULLSENDING": "GOD OF FULLSENDING",
    "GRANDFULLSENDER": "GRANDFULLSENDER",
    "MASTER OF FULLSENDING": "MASTER OF FULLSENDING",
    "FULLSEND TRAINER": "FULLSEND TRAINER",
    "ELITE FULLSENDER": "ELITE FULLSENDER",
    "TRUE FULLSENDER": "TRUE FULLSENDER",
    "FULLSENDER": "FULLSENDER",
    "MINI FULLSENDER": "MINI FULLSENDER",
    "SENDER": "SENDER",
    "HALF SENDER": "HALF SENDER",
}
RANKS = list(RANK_ROLE_NAMES)

# This role is removed as soon as a member holds any of the 10 rank roles
MITOCHONDRIA_ROLE_NAME = "MITOCHONDRIA"

IGNORED_RANKS = {"NOT RATED"}

# Embed colors for thread notifications
COLOR_FIRST_RANK = discord.Color.gold()
COLOR_PROMOTION = discord.Color.green()
COLOR_DEMOTION = discord.Color.red()


def clean_role_name(name: str) -> str:
    return re.sub(r"^[^A-Za-z0-9]+", "", name).strip().upper()


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rank_bot")


def load_verified() -> dict[int, str]:
    data = json.loads(PLAYERS_FILE.read_text(encoding="utf-8"))
    verified: dict[int, str] = {}
    for entry in data.get("verified", []):
        try:
            verified[int(entry["discord_id"])] = str(entry["openfront_id"]).strip()
        except (KeyError, ValueError, TypeError):
            log.warning("Invalid entry in %s: %r - skipping", PLAYERS_FILE.name, entry)
    return verified


def load_ranks() -> dict[str, str]:
    data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    ranks: dict[str, str] = {}

    for p in data.get("players", []):
        rank = str(p.get("rank", "")).strip().upper()
        if rank in RANKS:
            ranks[p["id"]] = rank
        elif rank in IGNORED_RANKS or not rank:
            continue
        else:
            log.warning("Unknown rank %r for player %s - skipping", p.get("rank"), p.get("name"))

    for p in data.get("tracked_players", []):
        if p["id"] not in ranks:
            rank = str(p.get("table_rank", "")).strip().upper()
            if rank in RANKS:
                ranks[p["id"]] = rank

    return ranks


def load_places() -> tuple[dict[str, int], int | None]:
    """Clan leaderboard place per OpenFront player id, plus the table size."""
    data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    places: dict[str, int] = {}

    for p in data.get("players", []):
        if isinstance(p.get("place"), int):
            places[p["id"]] = p["place"]

    for p in data.get("tracked_players", []):
        if p["id"] not in places and p.get("in_table") and isinstance(p.get("table_place"), int):
            places[p["id"]] = p["table_place"]

    total = data.get("players_in_table")
    return places, total if isinstance(total, int) else None


def get_rank_roles(guild: discord.Guild) -> dict[str, discord.Role]:
    by_name = {clean_role_name(r.name): r for r in guild.roles}
    roles: dict[str, discord.Role] = {}
    for rank, role_name in RANK_ROLE_NAMES.items():
        role = by_name.get(role_name)
        if role is None:
            log.warning("Role for rank %s (%s) not found on the server", rank, role_name)
        else:
            roles[rank] = role
    return roles


def find_role(guild: discord.Guild, role_name: str) -> discord.Role | None:
    for r in guild.roles:
        if clean_role_name(r.name) == role_name:
            return r
    return None


def tier_text(rank: str) -> str:
    """Human-friendly tier, e.g. 'Tier 10 / 10' for the top rank."""
    return f"Tier {len(RANKS) - RANKS.index(rank)} / {len(RANKS)}"


def rank_change_text(old_name: str, new_name: str) -> str:
    """Rank change inside a plain code block (default text color)."""
    return f"```\n{old_name}  ➜  {new_name}\n```"


def build_embed(
    member: discord.Member,
    new_rank: str,
    new_role: discord.Role,
    old_rank: str | None,
    old_role: discord.Role | None,
    kind: str,
    place: int | None = None,
    total: int | None = None,
) -> discord.Embed:
    """Build the thread notification. kind is 'first', 'promotion' or 'demotion'."""
    if kind == "first":
        embed = discord.Embed(
            title="🏅  New Rank Awarded",
            description=(
                f"Welcome to the ladder, {member.mention}!\n"
                f"Your journey starts here - show everyone what you can do.\n"
                f"Check your stats: https://fullsendstats.com\n"
                f"-# Ranks are updated every hour."
            ),
            color=COLOR_FIRST_RANK,
        )
        embed.add_field(name="Rank", value=f"**{new_role.name}**", inline=True)

    elif kind == "promotion":
        embed = discord.Embed(
            title="︽  Rank Up!",
            description=(
                f"{member.mention} just climbed higher on the leaderboard!\n"
                f"Check your stats: https://fullsendstats.com\n"
                f"-# Ranks are updated every hour."
            ),
            color=COLOR_PROMOTION,
        )
        embed.add_field(
            name="Promotion",
            value=rank_change_text(old_role.name, new_role.name),
            inline=False,
        )

    else:  # demotion
        embed = discord.Embed(
            title="︾  Rank Update",
            description=(
                f"{member.mention}'s rank was lowered because someone else took that spot on the leaderboard.\n"
                f"Check your stats: https://fullsendstats.com\n"
                f"-# Ranks are updated every hour."
            ),
            color=COLOR_DEMOTION,
        )
        embed.add_field(
            name="Change",
            value=rank_change_text(old_role.name, new_role.name),
            inline=False,
        )

    embed.add_field(name="Level", value=tier_text(new_rank), inline=True)
    if place is not None:
        place_text = f"#{place}" + (f" of {total}" if total else "")
        embed.add_field(name="Clan Place", value=place_text, inline=True)

    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="[FSC] Ranking Bot")
    embed.timestamp = discord.utils.utcnow()
    return embed


async def notify(thread, embed: discord.Embed) -> None:
    if thread is None:
        return
    try:
        if getattr(thread, "archived", False):
            await thread.edit(archived=False)
        # the mention is shown in the embed text, but the player gets no notification
        mentions = discord.AllowedMentions.none()
        try:
            await thread.send(embed=embed, allowed_mentions=mentions, silent=True)
        except TypeError:
            # old discord.py (before 2.2) doesn't support `silent`
            await thread.send(embed=embed, allowed_mentions=mentions)
    except discord.HTTPException as e:
        log.error("Could not post to the thread: %s", e)


async def run(client: discord.Client) -> None:
    guild = client.get_guild(GUILD_ID) or await client.fetch_guild(GUILD_ID)

    try:
        thread = await client.fetch_channel(THREAD_ID)
    except discord.HTTPException as e:
        log.error("Thread %s is unavailable: %s", THREAD_ID, e)
        thread = None

    verified = load_verified()
    ranks = load_ranks()
    places, places_total = load_places()
    rank_roles = get_rank_roles(guild)
    rank_role_ids = {r.id for r in rank_roles.values()}
    role_id_to_rank = {r.id: rank for rank, r in rank_roles.items()}
    # RANKS goes from highest to lowest: the lower the index, the higher the rank
    rank_order = {rank: i for i, rank in enumerate(RANKS)}
    mito_role = find_role(guild, MITOCHONDRIA_ROLE_NAME)
    if mito_role is None:
        log.warning("Role %s not found on the server - will not remove it", MITOCHONDRIA_ROLE_NAME)

    for discord_id, player_id in verified.items():
        rank = ranks.get(player_id)
        target_role = rank_roles.get(rank) if rank else None

        if rank is None:
            log.warning(
                "Player %s (discord %s): no rank in JSON (not found or not rated) - only cleaning up extra rank roles",
                player_id, discord_id,
            )
        elif target_role is None:
            log.warning("No role for rank %s - only cleaning up extra rank roles for %s", rank, discord_id)

        try:
            member = await guild.fetch_member(discord_id)
        except discord.NotFound:
            log.warning("Member %s not found on the server - skipping", discord_id)
            continue

        if target_role is not None:
            keep_role = target_role
        else:
            # rank from JSON is unknown: keep the highest rank role the member already has
            held = [r for r in member.roles if r.id in rank_role_ids]
            if not held:
                continue
            keep_role = min(held, key=lambda r: rank_order[role_id_to_rank[r.id]])

        # only roles from the rank list (RANK_ROLE_NAMES), everything except the one to keep
        to_remove = [r for r in member.roles if r.id in rank_role_ids and r.id != keep_role.id]
        need_add = keep_role not in member.roles

        # member has (or is getting) one of the 10 ranks -> MITOCHONDRIA is removed
        extra = [mito_role] if mito_role is not None and mito_role in member.roles else []

        if target_role is None and not to_remove and not extra:
            continue  # rank unknown in JSON and nothing to clean up

        try:
            for r in to_remove:
                try:
                    await member.remove_roles(r, reason="FSC rank bot: only the highest rank is kept")
                except discord.Forbidden:
                    log.error("Missing permission to remove role %s from %s (the bot's role must be above it)", r.name, member)
            for r in extra:
                try:
                    await member.remove_roles(r, reason="FSC rank bot: member now has a rank role")
                except discord.Forbidden:
                    log.error("Missing permission to remove role %s from %s (the bot's role must be above it)", r.name, member)
            if need_add:
                await member.add_roles(keep_role, reason="FSC rank bot: rank assigned")
            log.info("%s -> %s (removed: %s)", member, keep_role.name,
                     ", ".join(r.name for r in to_remove + extra) or "-")

            if need_add:
                # previous rank = highest of the removed rank roles (None if there was no rank at all)
                old_ranks = [role_id_to_rank[r.id] for r in to_remove]
                old_rank = min(old_ranks, key=rank_order.get) if old_ranks else None
                old_role = rank_roles[old_rank] if old_rank else None

                if old_rank is None:
                    kind = "first"       # no previous rank - awarded for the first time
                elif rank_order[rank] < rank_order[old_rank]:
                    kind = "promotion"   # new rank is higher
                else:
                    kind = "demotion"    # new rank is lower

                embed = build_embed(member, rank, target_role, old_rank, old_role, kind,
                                    places.get(player_id), places_total)
                await notify(thread, embed)
        except discord.Forbidden:
            log.error("Missing permissions for %s (the bot's role must be above the rank roles)", member)
        except discord.HTTPException as e:
            log.error("Discord error for %s: %s", member, e)

    log.info("Done.")


class RankBot(discord.Client):
    async def on_ready(self):
        try:
            await run(self)
        except Exception:
            log.exception("Error while assigning ranks")
        finally:
            await self.close()


def main() -> None:
    client = RankBot(intents=discord.Intents.default())
    client.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()