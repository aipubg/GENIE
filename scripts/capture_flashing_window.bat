@echo off
rem One-click capture for the flashing-terminal incident.
rem
rem Run this the moment you see the window flashing, and leave the machine
rem alone for the five minutes it runs. It only WATCHES - nothing is killed,
rem disabled or deleted.
rem
rem It writes artifacts\p0_owner_capture.jsonl and prints a summary.
rem Send both back.

setlocal
cd /d "%~dp0\.."

set "PY=C:\Users\ghostt\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -c "import psutil" >nul 2>&1
if errorlevel 1 (
    echo.
    echo psutil is not installed for: %PY%
    echo Install it once with:  "%PY%" -m pip install psutil
    echo.
    pause
    exit /b 1
)

echo.
echo Watching process activity for 5 minutes...
echo Please avoid running other programs while this is going.
echo.
"%PY%" scripts\p0_process_capture.py --seconds 300 --out artifacts\p0_owner_capture.jsonl

echo.
echo ================= SUMMARY ================
"%PY%" scripts\p0_process_capture.py --summary-only artifacts\p0_owner_capture.jsonl
echo ==========================================
echo.
echo Full log: artifacts\p0_owner_capture.jsonl
echo Summary : artifacts\p0_owner_capture.jsonl.summary.txt
echo.
pause
