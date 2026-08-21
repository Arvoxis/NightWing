# ============================================================================
#  KHOJ - start the NightWing dashboard bound to the REAL ESP32 boards.
#  ---------------------------------------------------------------------------
#  Why this exists: the backend defaults to KHOJ_ENGINE=real (the pure-python
#  SIMULATION), which runs with no hardware at all - so it looks alive even with
#  every board unplugged. To drive it from the boards you must set the engine to
#  'hardware'. In PowerShell `set KHOJ_ENGINE=hardware` silently does NOTHING
#  (that's cmd.exe syntax); the correct form is `$env:KHOJ_ENGINE="hardware"`.
#  This script does it for you so that footgun can't happen.
#
#  Usage (from anywhere):
#      .\khoj\run_dashboard.ps1
#
#  Then, in two more terminals:
#      py -m http.server 3000 --bind 127.0.0.1 --directory frontend
#      py khoj\sim\feeder_real.py --ports COM3 COM13 COM14 COM15 COM16
#  and open http://127.0.0.1:3000  (MODE will read HARDWARE, empty until the
#  feeder feeds real boards).
# ============================================================================
$env:KHOJ_ENGINE = "hardware"
Set-Location (Split-Path $PSScriptRoot -Parent)   # repo root, so backend.* imports

Write-Host ""
Write-Host "ENGINE_MODE = hardware" -ForegroundColor Cyan
Write-Host "The dashboard stays EMPTY until khoj\sim\feeder_real.py feeds real boards." -ForegroundColor DarkGray
Write-Host "That is correct - no ESP32s, no drones on screen." -ForegroundColor DarkGray
Write-Host ""
py -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
