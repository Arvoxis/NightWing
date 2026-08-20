# ============================================================================
#  KHOJ — rasterise the deck diagrams (SVG -> PNG) using headless Edge.
#  ---------------------------------------------------------------------------
#  Why Edge and not a Python library: every Python SVG rasteriser on Windows
#  (cairosvg, rlPyCairo) needs the native Cairo DLL, which Windows does not
#  ship. Edge is already installed and renders SVG properly, including text.
#
#  Usage (from docs/ppt):
#      powershell -ExecutionPolicy Bypass -File .\svg_to_png.ps1
#
#  Produces a 2x-scale PNG next to each SVG. PowerPoint can insert the .svg
#  directly (Office 2016+) which stays sharp at any zoom; the PNGs exist for
#  older PowerPoint, Google Slides and for dropping into chat or a document.
# ============================================================================
param([double]$Scale = 2.0)

$edge = @(
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $edge) { Write-Host "Microsoft Edge not found." -ForegroundColor Red; exit 1 }

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$svgs = Get-ChildItem -Path $dir -Filter *.svg | Sort-Object Name
if ($svgs.Count -eq 0) { Write-Host "No .svg files here. Run make_diagrams.py first." -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "Rasterising $($svgs.Count) diagram(s) at ${Scale}x ..." -ForegroundColor Cyan

foreach ($svg in $svgs) {
    # read declared size straight off the root <svg> element
    $head = Get-Content $svg.FullName -TotalCount 1
    $w = 1600; $h = 900
    if ($head -match 'width="(\d+)"')  { $w = [int]$Matches[1] }
    if ($head -match 'height="(\d+)"') { $h = [int]$Matches[1] }
    $ow = [int]($w * $Scale); $oh = [int]($h * $Scale)

    $png = [System.IO.Path]::ChangeExtension($svg.FullName, ".png")
    Remove-Item $png -ErrorAction SilentlyContinue

    # a wrapper page pins the SVG to an exact pixel box with no margin, so the
    # screenshot has no white gutter and no scrollbars
    $html = [System.IO.Path]::ChangeExtension($svg.FullName, ".render.html")
    $uri = ([System.Uri]$svg.FullName).AbsoluteUri
@"
<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;background:#fff;overflow:hidden}
img{display:block;width:${ow}px;height:${oh}px}
</style></head><body><img src="$uri"></body></html>
"@ | Out-File -FilePath $html -Encoding utf8

    $htmlUri = ([System.Uri]$html).AbsoluteUri
    & $edge --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 `
            --default-background-color=FFFFFFFF `
            --screenshot="$png" --window-size="$ow,$oh" $htmlUri 2>$null | Out-Null

    Remove-Item $html -ErrorAction SilentlyContinue

    if (Test-Path $png) {
        $kb = [math]::Round((Get-Item $png).Length / 1KB)
        Write-Host ("  OK  {0,-34} {1}x{2}  {3} KB" -f $svg.Name, $ow, $oh, $kb) -ForegroundColor Green
    } else {
        Write-Host ("  FAILED  {0}" -f $svg.Name) -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Done. PNGs are next to the SVGs in $dir" -ForegroundColor Cyan
