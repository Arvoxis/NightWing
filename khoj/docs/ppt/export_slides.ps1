# ============================================================================
#  Export every slide of a .pptx to PNG using the installed PowerPoint.
#  ---------------------------------------------------------------------------
#  True-fidelity rendering (real fonts, real layout) — unlike LibreOffice, which
#  substitutes fonts and can show overflow that will not exist in the real deck.
#
#  Usage:
#      powershell -ExecutionPolicy Bypass -File .\export_slides.ps1 -Deck "C:\path\deck.pptx" -Out "C:\path\render"
# ============================================================================
param(
    [Parameter(Mandatory = $true)][string]$Deck,
    [Parameter(Mandatory = $true)][string]$Out,
    [int]$Width = 1920
)

if (-not (Test-Path $Deck)) { Write-Host "Deck not found: $Deck" -ForegroundColor Red; exit 1 }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
Get-ChildItem $Out -Filter *.png -ErrorAction SilentlyContinue | Remove-Item -Force

$ppt = $null
$pres = $null
try {
    $ppt = New-Object -ComObject PowerPoint.Application
    # NOTE: do NOT set $ppt.Visible = $false — PowerPoint's COM server refuses it
    # and throws. Leaving it default is fine; the window is transient.
    $pres = $ppt.Presentations.Open($Deck, $true, $false, $false)   # readonly, untitled, no window

    $h = [int]($Width * $pres.PageSetup.SlideHeight / $pres.PageSetup.SlideWidth)
    $n = $pres.Slides.Count
    Write-Host "Exporting $n slide(s) at ${Width}x${h} ..." -ForegroundColor Cyan

    foreach ($i in 1..$n) {
        $p = Join-Path $Out ("slide-{0:D2}.png" -f $i)
        $pres.Slides.Item($i).Export($p, "PNG", $Width, $h)
    }
    Write-Host "Wrote $n PNG(s) to $Out" -ForegroundColor Green
}
catch {
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    if ($pres) { $pres.Close() }
    if ($ppt) { $ppt.Quit() }
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($ppt) | Out-Null
}
