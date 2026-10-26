@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  pause
  exit /b 1
)
set "SHIFT_HOST=127.0.0.1"
set "SHIFT_DATA_DIR=%~dp0beta-data"
set "SHIFT_PUBLIC_URL="
set "RENDER_EXTERNAL_URL="
echo BETA TEST ONLY - use fake names and a test Excel file.
echo Open http://127.0.0.1:8002 in a browser on this PC.
echo For the daily kitchen/hall beta, open http://127.0.0.1:8002/daily
.venv\Scripts\python.exe app.py --port 8002
pause
