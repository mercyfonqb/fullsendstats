@echo off
cd /d "%~dp0"
echo Server started: http://localhost:8000/index.html
echo Close this window to stop the server.
start "" "http://localhost:8000/index.html"
python -m http.server 8000 2>nul || py -m http.server 8000
pause