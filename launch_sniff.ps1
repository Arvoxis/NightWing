# ============================================================================
#  KHOJ Digital-Sniff experiment — one-command launcher
#  Starts: backend, feeder, frontend HTTP server, then opens the browser.
# ============================================================================
param(
    [string[]] $Ports  = @("COM3","COM13","COM15","COM16"),
    [int]      $Speed  = 2,
    [int]      $NVicts = 3,
    [int]      $NDecoy = 2
)

Set-Location (Split-Path $PSScriptRoot -Parent)   # repo root (NightWing\)
$env:KHOJ_ENGINE = "hardware"

# ── detect ports if caller didn't pass them ──────────────────────────────
$auto = [System.IO.Ports.SerialPort]::GetPortNames() | Where-Object {
    try {
        $sp = [System.IO.Ports.SerialPort]::new($_, 115200)
        $sp.Open(); $sp.Close(); $true
    } catch { $false }
}
if ($auto -and -not $PSBoundParameters.ContainsKey('Ports')) {
    $Ports = $auto
    Write-Host "  Auto-detected ports: $($Ports -join ', ')" -ForegroundColor Cyan
}
$portStr = $Ports -join ' '

Write-Host ""
Write-Host "  KHOJ DIGITAL SNIFF LAUNCHER" -ForegroundColor Cyan
Write-Host "  ─────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  Ports  : $portStr"
Write-Host "  Victims: $NVicts   Decoys: $NDecoy   Speed: ${Speed} c/s"
Write-Host ""

# ── 1. backend ──────────────────────────────────────────────────────────
Write-Host "  [1/3] Starting backend on :8000 …" -ForegroundColor DarkCyan
$backend = Start-Process powershell -PassThru -WindowStyle Minimized -ArgumentList @(
    "-NoProfile", "-Command",
    "cd '$PWD'; `$env:KHOJ_ENGINE='hardware'; py -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"
)

Start-Sleep -Seconds 2

# ── 2. feeder ───────────────────────────────────────────────────────────
Write-Host "  [2/3] Starting feeder (digital-sniff) …" -ForegroundColor DarkCyan
$feederCmd = "cd '$PWD'; py khoj/sim/feeder_sniff.py --ports $portStr --speed $Speed --n-victims $NVicts --n-decoys $NDecoy"
$feeder = Start-Process powershell -PassThru -ArgumentList @(
    "-NoProfile", "-Command", $feederCmd
)

Start-Sleep -Seconds 1

# ── 3. frontend HTTP server ──────────────────────────────────────────────
Write-Host "  [3/3] Serving frontend on :3000 …" -ForegroundColor DarkCyan
$frontend = Start-Process powershell -PassThru -WindowStyle Minimized -ArgumentList @(
    "-NoProfile", "-Command",
    "cd '$PWD'; py -m http.server 3000 --bind 127.0.0.1 --directory frontend"
)

Start-Sleep -Seconds 1

# ── 4. browser ──────────────────────────────────────────────────────────
Write-Host "  Opening dashboard …" -ForegroundColor Green
Start-Process "http://127.0.0.1:3000/dashboard_sniff.html"

Write-Host ""
Write-Host "  ✓ All components running." -ForegroundColor Green
Write-Host "  Dashboard : http://127.0.0.1:3000/dashboard_sniff.html"
Write-Host "  Backend   : http://127.0.0.1:8000"
Write-Host ""
Write-Host "  Press Ctrl+C here to stop the feeder (backend/frontend keep running)." -ForegroundColor DarkGray
Write-Host ""

# keep this window alive so user can see feeder output
$feeder.WaitForExit()
