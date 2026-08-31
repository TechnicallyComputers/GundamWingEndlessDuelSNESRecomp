param(
    [string]$Source = "translations\endless_duel_cjk_candidates.toml",
    [string]$Out = "translations\endless_duel_cjk_glyphs.toml",
    [string]$VramJson = "",
    [int]$TileStart = 0x0120
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing

function Resolve-RepoPath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Path))
}

function Read-LinesSection([string[]]$Lines, [string]$SectionName) {
    $inSection = $false
    $inLines = $false
    $result = New-Object System.Collections.Generic.List[string]
    foreach ($line in $Lines) {
        $trim = $line.Trim()
        if ($trim -match '^\[(.+)\]$') {
            $inSection = ($Matches[1] -eq $SectionName)
            $inLines = $false
            continue
        }
        if (-not $inSection) {
            continue
        }
        if ($trim -eq 'lines = [') {
            $inLines = $true
            continue
        }
        if ($inLines -and $trim -eq ']') {
            break
        }
        if ($inLines -and $trim -match '^"(.*)",?$') {
            $result.Add($Matches[1]) | Out-Null
        }
    }
    return [string[]]$result
}

function Get-GlyphChars([string[]]$Lines) {
    $chars = New-Object System.Collections.Generic.SortedSet[string]
    foreach ($line in $Lines) {
        foreach ($ch in $line.ToCharArray()) {
            $s = [string]$ch
            if ([int][char]$ch -lt 128) {
                continue
            }
            if ([char]::IsPunctuation($ch) -or [char]::IsSeparator($ch) -or [char]::IsWhiteSpace($ch)) {
                continue
            }
            $chars.Add($s) | Out-Null
        }
    }
    return [string[]]$chars
}

function Encode-Snes2bppTile([int[,]]$Pixels, [int]$Y0) {
    $bytes = New-Object byte[] 16
    for ($y = 0; $y -lt 8; $y++) {
        $p0 = 0
        $p1 = 0
        for ($x = 0; $x -lt 8; $x++) {
            $bit = 7 - $x
            $v = $Pixels[$x, ($Y0 + $y)]
            if (($v -band 1) -ne 0) { $p0 = $p0 -bor (1 -shl $bit) }
            if (($v -band 2) -ne 0) { $p1 = $p1 -bor (1 -shl $bit) }
        }
        $bytes[$y * 2] = [byte]$p0
        $bytes[$y * 2 + 1] = [byte]$p1
    }
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Render-Glyph([string]$Char, [string]$FontName, [int]$FontSize, [int]$GlyphWidth) {
    $bmp = [System.Drawing.Bitmap]::new(16, 16, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::Black)
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::SingleBitPerPixelGridFit
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
    $font = [System.Drawing.Font]::new($FontName, $FontSize, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
    $format = [System.Drawing.StringFormat]::new()
    $format.Alignment = [System.Drawing.StringAlignment]::Center
    $format.LineAlignment = [System.Drawing.StringAlignment]::Center
    $rect = [System.Drawing.RectangleF]::new(0.0, 0.0, 16.0, 16.0)
    $g.DrawString($Char, $font, [System.Drawing.Brushes]::White, $rect, $format)

    $pixels = New-Object 'int[,]' 16,16
    for ($y = 0; $y -lt 16; $y++) {
        for ($x = 0; $x -lt 16; $x++) {
            $c = $bmp.GetPixel($x, $y)
            $lum = [int](($c.R + $c.G + $c.B) / 3)
            $pixels[$x, $y] = if ($lum -gt 48) { 3 } else { 0 }
        }
    }

    $leftPixels = New-Object 'int[,]' 8,16
    $rightPixels = New-Object 'int[,]' 8,16
    for ($y = 0; $y -lt 16; $y++) {
        for ($x = 0; $x -lt 8; $x++) {
            if ($GlyphWidth -eq 1) {
                $srcX0 = $x * 2
                $srcX1 = $srcX0 + 1
                $sample0 = $pixels[$srcX0, $y]
                $sample1 = $pixels[$srcX1, $y]
                $leftPixels[$x, $y] = [Math]::Max($sample0, $sample1)
                $rightPixels[$x, $y] = 0
            } else {
                $leftPixels[$x, $y] = $pixels[$x, $y]
                $rightPixels[$x, $y] = $pixels[($x + 8), $y]
            }
        }
    }
    $topLeft = Encode-Snes2bppTile $leftPixels 0
    $topRight = Encode-Snes2bppTile $rightPixels 0
    $bottomLeft = Encode-Snes2bppTile $leftPixels 8
    $bottomRight = Encode-Snes2bppTile $rightPixels 8
    $font.Dispose()
    $format.Dispose()
    $g.Dispose()
    $bmp.Dispose()
    return [pscustomobject]@{
        TopLeft = $topLeft
        TopRight = $topRight
        BottomLeft = $bottomLeft
        BottomRight = $bottomRight
    }
}

function Read-VramSourceHex([byte[]]$Vram, [int]$TileBaseWord, [int]$Tile) {
    if ($null -eq $Vram -or $Vram.Length -eq 0) {
        return ""
    }
    $offset = (($TileBaseWord + ($Tile * 8)) -band 0x7fff) * 2
    if ($offset -lt 0 -or $offset + 16 -gt $Vram.Length) {
        throw ("tile 0x{0:x4}: source VRAM offset out of range" -f $Tile)
    }
    $bytes = $Vram[$offset..($offset + 15)]
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

$sourcePath = Resolve-RepoPath $Source
$outPath = Resolve-RepoPath $Out
$sourceLines = Get-Content -LiteralPath $sourcePath -Encoding utf8
$tileBaseWord = 0x3000
$vramBytes = [byte[]]@()
if ($VramJson) {
    $vramPath = Resolve-RepoPath $VramJson
    $vramData = [System.IO.File]::ReadAllText($vramPath) | ConvertFrom-Json
    $vramHex = [string]$vramData.hex
    $vramBytes = New-Object byte[] ($vramHex.Length / 2)
    for ($i = 0; $i -lt $vramBytes.Length; $i++) {
        $vramBytes[$i] = [Convert]::ToByte($vramHex.Substring($i * 2, 2), 16)
    }
}
$langSpecs = @(
    [pscustomobject]@{ Lang = "ko"; Section = "pending.ko"; Font = "Malgun Gothic"; FontSize = 12; GlyphWidth = 2 }
)

$outLines = New-Object System.Collections.Generic.List[string]
$outLines.Add("# Generated CJK glyph tiles for the Endless Duel BG3 crawl.") | Out-Null
$outLines.Add("# Regenerate with:") | Out-Null
$outLines.Add("#   powershell -ExecutionPolicy Bypass -File scripts\generate_cjk_glyph_tiles.ps1 -VramJson <crawl vram.json>") | Out-Null
$outLines.Add("") | Out-Null
$outLines.Add("schema = 1") | Out-Null
$outLines.Add(("tile_start = 0x{0:x4}" -f $TileStart)) | Out-Null
$outLines.Add(("tile_base_word = 0x{0:x4}" -f $tileBaseWord)) | Out-Null
$outLines.Add("") | Out-Null

foreach ($spec in $langSpecs) {
    $lines = Read-LinesSection $sourceLines $spec.Section
    $chars = Get-GlyphChars $lines
    $tile = $TileStart
    $outLines.Add(("[languages.{0}]" -f $spec.Lang)) | Out-Null
    $outLines.Add(("font = ""{0}""" -f $spec.Font)) | Out-Null
    $outLines.Add(("font_size = {0}" -f $spec.FontSize)) | Out-Null
    $outLines.Add(("glyph_width = {0}" -f $spec.GlyphWidth)) | Out-Null
    $outLines.Add(("chars = ""{0}""" -f (-join $chars))) | Out-Null
    $outLines.Add("") | Out-Null
    foreach ($char in $chars) {
        $glyph = Render-Glyph $char $spec.Font $spec.FontSize $spec.GlyphWidth
        $outLines.Add(("[glyph.{0}.""{1}""]" -f $spec.Lang, $char.Replace("\", "\\").Replace("""", "\"""))) | Out-Null
        $outLines.Add(("top_left_tile = 0x{0:x4}" -f $tile)) | Out-Null
        $outLines.Add(("top_right_tile = 0x{0:x4}" -f ($tile + 1))) | Out-Null
        $outLines.Add(("bottom_left_tile = 0x{0:x4}" -f ($tile + 2))) | Out-Null
        $outLines.Add(("bottom_right_tile = 0x{0:x4}" -f ($tile + 3))) | Out-Null
        $outLines.Add(("top_left_source_hex = ""{0}""" -f (Read-VramSourceHex $vramBytes $tileBaseWord $tile))) | Out-Null
        $outLines.Add(("top_right_source_hex = ""{0}""" -f (Read-VramSourceHex $vramBytes $tileBaseWord ($tile + 1)))) | Out-Null
        $outLines.Add(("bottom_left_source_hex = ""{0}""" -f (Read-VramSourceHex $vramBytes $tileBaseWord ($tile + 2)))) | Out-Null
        $outLines.Add(("bottom_right_source_hex = ""{0}""" -f (Read-VramSourceHex $vramBytes $tileBaseWord ($tile + 3)))) | Out-Null
        $outLines.Add(("top_left_hex = ""{0}""" -f $glyph.TopLeft)) | Out-Null
        $outLines.Add(("top_right_hex = ""{0}""" -f $glyph.TopRight)) | Out-Null
        $outLines.Add(("bottom_left_hex = ""{0}""" -f $glyph.BottomLeft)) | Out-Null
        $outLines.Add(("bottom_right_hex = ""{0}""" -f $glyph.BottomRight)) | Out-Null
        $outLines.Add("") | Out-Null
        $tile += 4
    }
}

$outDir = Split-Path -Parent $outPath
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($outPath, (($outLines -join "`n") + "`n"), $utf8NoBom)

Write-Host "Wrote CJK glyph tiles to $outPath"
