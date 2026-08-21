@echo off
title KHOJ - Digital Sniff Mission Launcher
color 0B
cls

echo.
echo  ██╗  ██╗██╗  ██╗ ██████╗      ██╗
echo  ██║ ██╔╝██║  ██║██╔═══██╗     ██║
echo  █████╔╝ ███████║██║   ██║     ██║
echo  ██╔═██╗ ██╔══██║██║   ██║██   ██║
echo  ██║  ██╗██║  ██║╚██████╔╝╚█████╔╝
echo  ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝  ╚════╝
echo.
echo  SEARCH ^& RESCUE SWARM  ^|  DIGITAL SNIFF v2
echo  ─────────────────────────────────────────────
echo.

REM ── Set COM ports here ────────────────────────────────────────────────────
set PORTS=COM3 COM13 COM15 COM16
set N_VICTIMS=3
set N_DECOYS=2
set SPEED=2

REM ── Working directory = folder that contains this .bat ────────────────────
cd /d "%~dp0"

REM ── Kill anything already sitting on port 8000 or 3000 ────────────────────
echo  [1/5] Clearing old processes on ports 8000 and 3000 ...
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8000 "') do (
    taskkill /PID %%a /F >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":3000 "') do (
    taskkill /PID %%a /F >nul 2>&1
)
ping -n 2 127.0.0.1 >nul

REM ── Backend (uvicorn, HARDWARE mode) ──────────────────────────────────────
echo  [2/5] Starting backend on :8000 ...
start "KHOJ-Backend" /min cmd /c "cd /d "%~dp0" && set KHOJ_ENGINE=hardware && py -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"
ping -n 3 127.0.0.1 >nul

REM ── Frontend HTTP server ───────────────────────────────────────────────────
echo  [3/5] Starting frontend on :3000 ...
start "KHOJ-Frontend" /min cmd /c "py -m http.server 3000 --bind 127.0.0.1 --directory "%~dp0frontend""
ping -n 2 127.0.0.1 >nul

REM ── Open browser ──────────────────────────────────────────────────────────
echo  [4/5] Opening dashboard ...
start "" "http://127.0.0.1:3000/dashboard_sniff.html"
ping -n 2 127.0.0.1 >nul

REM ── Feeder (resets ESP boards via DTR, then runs mission) ─────────────────
echo  [5/5] Starting feeder — will soft-reset ESP32 boards via DTR ...
echo.
echo  ══════════════════════════════════════════════════════════
echo   KHOJ feeder output (Ctrl+C to stop everything):
echo  ══════════════════════════════════════════════════════════
echo.
py khoj\sim\feeder_sniff.py --ports %PORTS% --n-victims %N_VICTIMS% --n-decoys %N_DECOYS% --speed %SPEED% --reset

REM ── If feeder exits, pause so the window stays open ───────────────────────
echo.
echo  Feeder stopped. Press any key to close.
pause >nul
