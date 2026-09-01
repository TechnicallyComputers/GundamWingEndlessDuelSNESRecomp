# Render text strips through GDI/Uniscribe for the localization generators.
#
# WHY THIS EXISTS.  Complex-script shaping.  Pillow only does complex-script
# layout when it was built against libraqm, and the build in this tree was not
# (PIL.features.check("raqm") is False) -- without it Thai vowel and tone marks
# advance as spacing glyphs instead of stacking over their base consonant, which
# is not a cosmetic difference, it is unreadable.  GDI's Uniscribe path shapes
# Thai correctly and needs no new Python dependency, so the Thai dialogue
# renderer shells out here.
#
# This runs at GENERATION time only.  Everything it produces is baked into
# translations/endless_duel.toml as tile bytes, so the shipped artifact has no
# dependency on Windows, on GDI, or on the font.
#
# Usage:
#   powershell -File scripts/render_text_gdi.ps1 -JobsJson jobs.json -OutDir dir
#
# jobs.json is a UTF-8 JSON array of objects:
#   { "name": "row_007700", "text": "...", "font_file": "LeelawUI.ttf",
#     "size": 16, "origin_x": 4, "origin_y": 8, "width": 400, "height": 48 }
# and each job writes <OutDir>/<name>.png: white text on black, no padding,
# single line, drawn with the text cell's top-left at (origin_x, origin_y).
param(
  [Parameter(Mandatory = $true)][string]$JobsJson,
  [Parameter(Mandatory = $true)][string]$OutDir
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

$jobs = (Get-Content -LiteralPath $JobsJson -Raw -Encoding UTF8) | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# One PrivateFontCollection per font file, reused across jobs.
$collections = @{}
$fonts = @{}

foreach ($job in $jobs) {
  $key = "$($job.font_file)|$($job.size)"
  if (-not $fonts.ContainsKey($key)) {
    if (-not $collections.ContainsKey($job.font_file)) {
      $path = Join-Path $env:WINDIR "Fonts\$($job.font_file)"
      if (-not (Test-Path -LiteralPath $path)) {
        throw "font not installed: $path"
      }
      $pfc = New-Object System.Drawing.Text.PrivateFontCollection
      $pfc.AddFontFile($path)
      $collections[$job.font_file] = $pfc
    }
    $fam = $collections[$job.font_file].Families[0]
    $fonts[$key] = New-Object System.Drawing.Font($fam, [single]$job.size,
      [System.Drawing.FontStyle]::Regular,
      [System.Drawing.GraphicsUnit]::Pixel)
  }
  $font = $fonts[$key]

  $bmp = New-Object System.Drawing.Bitmap([int]$job.width, [int]$job.height,
    [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.Clear([System.Drawing.Color]::Black)
  [System.Windows.Forms.TextRenderer]::DrawText($g, [string]$job.text, $font,
    (New-Object System.Drawing.Point([int]$job.origin_x, [int]$job.origin_y)),
    [System.Drawing.Color]::White, [System.Drawing.Color]::Black,
    ([System.Windows.Forms.TextFormatFlags]::NoPadding -bor
     [System.Windows.Forms.TextFormatFlags]::SingleLine -bor
     [System.Windows.Forms.TextFormatFlags]::NoPrefix))
  $g.Dispose()
  $bmp.Save((Join-Path $OutDir "$($job.name).png"),
    [System.Drawing.Imaging.ImageFormat]::Png)
  $bmp.Dispose()
}

foreach ($f in $fonts.Values) { $f.Dispose() }
foreach ($c in $collections.Values) { $c.Dispose() }
Write-Output "rendered $($jobs.Count)"
