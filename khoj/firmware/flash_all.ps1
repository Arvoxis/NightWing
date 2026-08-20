# ============================================================================
#  KHOJ — flash every connected ESP32, one after another.
#  ---------------------------------------------------------------------------
#  Why this exists: flashing 5 boards by hand means 5 commands with 5 different
#  COM ports, and the failure mode is silent — you think all 5 took the new
#  firmware, but two didn't, and you only find out from a weird peer list. This
#  finds every USB-serial board, flashes each, and prints a pass/fail table at
#  the end so a missed board is impossible to overlook.
#
#  Usage (from the firmware/ directory):
#      powershell -ExecutionPolicy Bypass -File .\flash_all.ps1
#      powershell -ExecutionPolicy Bypass -File .\flash_all.ps1 -Env mesh
#
#  It only touches real USB-UART bridges (CP210x / CH340 / FTDI). Bluetooth
#  serial ports are ignored, so your headphones never get flashed with drone
#  firmware.
# ============================================================================
param(
    [string]$Env = "mesh"
)

$ErrorActionPreference = "Continue"

# Windows consoles default to a legacy codepage (cp1252). PlatformIO prints
# non-ASCII, and when it cannot encode it, it dies mid-run with
# UnicodeEncodeError: 'charmap' codec can't encode characters — which looks
# like a flashing fault but is purely a console encoding problem.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host ""
Write-Host "KHOJ flash_all  ->  env '$Env'" -ForegroundColor Cyan
Write-Host ""

# --- find real USB-serial boards (skip Bluetooth virtual ports) -------------
$ports = Get-CimInstance Win32_PnPEntity |
    Where-Object { $_.Name -match '\(COM\d+\)' -and $_.DeviceID -like 'USB\*' } |
    ForEach-Object {
        if ($_.Name -match '\((COM\d+)\)') {
            [PSCustomObject]@{ Port = $Matches[1]; Name = $_.Name }
        }
    } | Sort-Object Port -Unique

if (-not $ports -or $ports.Count -eq 0) {
    Write-Host "No USB serial boards found." -ForegroundColor Red
    Write-Host "Check: boards plugged in? data cable (not charge-only)? CP210x driver installed?"
    exit 1
}

Write-Host "Found $($ports.Count) board(s):" -ForegroundColor Green
foreach ($p in $ports) { Write-Host "   $($p.Port)  $($p.Name)" }
Write-Host ""

# --- build once up front so a compile error costs one board, not five -------
Write-Host "Building '$Env' ..." -ForegroundColor Cyan
& py -m platformio run -e $Env | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "BUILD FAILED - fix the code before flashing anything." -ForegroundColor Red
    exit 1
}
Write-Host "Build OK." -ForegroundColor Green
Write-Host ""

# --- flash each board -------------------------------------------------------
$results = @()
foreach ($p in $ports) {
    Write-Host "--> flashing $($p.Port) ..." -ForegroundColor Yellow
    # capture rather than discard: a swallowed error looks like a successful
    # flash right up until the board behaves like it was never flashed
    $log = & py -m platformio run -e $Env -t upload --upload-port $p.Port 2>&1
    $ok = ($LASTEXITCODE -eq 0)
    $results += [PSCustomObject]@{ Port = $p.Port; Result = $(if ($ok) { "OK" } else { "FAILED" }) }
    if ($ok) {
        Write-Host "    $($p.Port) OK" -ForegroundColor Green
    } else {
        # Surface the line that actually explains it. The tail of a PlatformIO
        # run is just a summary table, which tells you nothing about the cause.
        $pat = 'could not open|Access is denied|PermissionError|Wrong boot mode|' +
               'Failed to connect|No serial data|FileNotFoundError|A fatal error'
        $why = $log | Select-String -Pattern $pat | Select-Object -First 3
        Write-Host "    $($p.Port) FAILED" -ForegroundColor Red
        if ($why) {
            $why | ForEach-Object { Write-Host "      $_" -ForegroundColor Yellow }
        } else {
            $log | Select-Object -Last 5 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
        }
    }
}

# --- the summary that makes a missed board impossible to miss ---------------
Write-Host ""
Write-Host "================ RESULT ================" -ForegroundColor Cyan
$results | Format-Table -AutoSize
$failed = @($results | Where-Object { $_.Result -eq "FAILED" })
if ($failed.Count -gt 0) {
    Write-Host "$($failed.Count) board(s) FAILED - reflash those before testing." -ForegroundColor Red
    Write-Host "Common cause: the serial monitor is holding the port. Close it and retry."
    exit 1
}
Write-Host "All $($results.Count) board(s) flashed." -ForegroundColor Green
Write-Host "Now check every board reports a real id (1..5), not 200+." -ForegroundColor Green
