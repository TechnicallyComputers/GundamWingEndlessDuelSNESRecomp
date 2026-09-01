param(
    [string]$RomPath = $env:SNESRECOMP_ROM,
    [string]$BuildDir = "build-local-xlate-trace",
    [string[]]$Languages = @("en", "es", "fr", "it", "pt", "tl", "id", "zh", "ko"),
    [string[]]$CaptureSeconds = @("105", "120"),
    [string]$OutDir = "",
    [int]$BasePort = 4670,
    [switch]$Turbo,
    [int]$TurboFrames = 6
)

$ErrorActionPreference = "Stop"

if ($Languages.Count -eq 1 -and $Languages[0].Contains(",")) {
    $Languages = $Languages[0].Split(",") |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
}
$CaptureSeconds = $CaptureSeconds |
    ForEach-Object { ([string]$_).Split(",") } |
    ForEach-Object { [int]$_.Trim() } |
    Sort-Object -Unique

function Resolve-RepoPath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Path))
}

function Write-StateFile([string]$StatePath, [string]$Language) {
    $dir = Split-Path -Parent $StatePath
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    @"
format_version = 1

[[package]]
id = "gwed.localization"
version = "1.0.0"

[[feature]]
package_id = "gwed.localization"
id = "localization"
enabled = true

[feature.values]
language = "$Language"
"@ | Set-Content -LiteralPath $StatePath -NoNewline -Encoding ascii
}

function Start-Game([string]$ExePath, [string]$WorkDir, [string]$Rom, [int]$Port,
                    [int]$FastForwardFrames) {
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $ExePath
    $psi.WorkingDirectory = $WorkDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.Arguments = ('--no-launcher "{0}"' -f $Rom.Replace('"', '\"'))
    $psi.Environment["SDL_VIDEODRIVER"] = "dummy"
    $psi.Environment["SDL_AUDIODRIVER"] = "dummy"
    $psi.Environment["SNESRECOMP_DEBUG_PORT"] = [string]$Port
    if ($FastForwardFrames -gt 1) {
        $psi.Environment["SNESRECOMP_TURBO"] = "1"
        $psi.Environment["SNESRECOMP_TURBO_FRAMES"] = [string]$FastForwardFrames
    }
    return [System.Diagnostics.Process]::Start($psi)
}

function Connect-Tcp([int]$Port) {
    $deadline = (Get-Date).AddSeconds(12)
    while ((Get-Date) -lt $deadline) {
        try {
            $client = [System.Net.Sockets.TcpClient]::new()
            $client.Connect("127.0.0.1", $Port)
            $stream = $client.GetStream()
            $reader = [System.IO.StreamReader]::new($stream)
            $writer = [System.IO.StreamWriter]::new($stream)
            $writer.NewLine = "`n"
            $writer.AutoFlush = $true
            return [pscustomobject]@{
                Client = $client
                Reader = $reader
                Writer = $writer
            }
        } catch {
            Start-Sleep -Milliseconds 150
        }
    }
    throw "Timed out waiting for debug TCP port $Port"
}

function Invoke-TcpLine($Conn, [string]$Line) {
    $Conn.Writer.WriteLine($Line)
    return $Conn.Reader.ReadLine()
}

function Take-Shot($Conn, [string]$Path) {
    $json = Invoke-TcpLine $Conn ("screenshot {0}" -f $Path)
    try {
        $parsed = $json | ConvertFrom-Json
        if (-not $parsed.ok) {
            throw "screenshot failed: $json"
        }
    } catch {
        if ($json -notmatch '"ok":true') {
            throw "screenshot command returned unexpected response: $json"
        }
    }
}

function Stop-Game($Proc) {
    if ($null -ne $Proc -and -not $Proc.HasExited) {
        $Proc.CloseMainWindow() | Out-Null
        Start-Sleep -Milliseconds 250
    }
    if ($null -ne $Proc -and -not $Proc.HasExited) {
        $Proc.Kill()
        $Proc.WaitForExit()
    }
}

function New-ContactPng([string]$Root, [string[]]$Langs, [int[]]$Seconds) {
    try {
        Add-Type -AssemblyName System.Drawing
    } catch {
        Write-Warning "System.Drawing is unavailable; skipping PNG contact sheet."
        return ""
    }

    $scale = 2
    $labelH = 28
    $firstShot = Join-Path $Root (Join-Path $Langs[0] ("crawl_{0}s.bmp" -f $Seconds[0]))
    $probe = [System.Drawing.Bitmap]::FromFile($firstShot)
    $srcW = $probe.Width
    $srcH = $probe.Height
    $probe.Dispose()

    $cellW = $srcW * $scale
    $cellH = ($srcH * $scale) + $labelH
    $sheet = New-Object System.Drawing.Bitmap ($cellW * $Seconds.Count), ($cellH * $Langs.Count)
    $g = [System.Drawing.Graphics]::FromImage($sheet)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
    $g.Clear([System.Drawing.Color]::FromArgb(17, 17, 17))
    $font = New-Object System.Drawing.Font "Segoe UI", 12

    for ($row = 0; $row -lt $Langs.Count; $row++) {
        for ($col = 0; $col -lt $Seconds.Count; $col++) {
            $lang = $Langs[$row]
            $second = $Seconds[$col]
            $x = $col * $cellW
            $y = $row * $cellH
            $g.DrawString(("{0} {1}s" -f $lang.ToUpper(), $second),
                          $font, [System.Drawing.Brushes]::White, $x + 8, $y + 6)
            $bmp = [System.Drawing.Bitmap]::FromFile(
                (Join-Path $Root (Join-Path $lang ("crawl_{0}s.bmp" -f $second))))
            $g.DrawImage($bmp, $x, $y + $labelH, $srcW * $scale, $srcH * $scale)
            $bmp.Dispose()
        }
    }

    $pngPath = Join-Path $Root "contact.png"
    $sheet.Save($pngPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose()
    $sheet.Dispose()
    $font.Dispose()
    return $pngPath
}

if (-not $RomPath) {
    throw "Pass -RomPath or set SNESRECOMP_ROM to an extracted .sfc/.smc ROM path."
}
if (-not $CaptureSeconds -or $CaptureSeconds.Count -eq 0) {
    throw "Pass at least one capture second."
}
if ($TurboFrames -lt 1) {
    throw "TurboFrames must be at least 1."
}

$waitScale = 1.0
if ($Turbo) {
    $waitScale = [double]$TurboFrames
}

$buildPath = Resolve-RepoPath $BuildDir
$exePath = Join-Path $buildPath "GundamWingEndlessDuelSNESRecomp.exe"
$romFull = [System.IO.Path]::GetFullPath($RomPath)
if (-not (Test-Path -LiteralPath $exePath)) {
    throw "Trace executable not found: $exePath"
}
if (-not (Test-Path -LiteralPath $romFull)) {
    throw "ROM not found: $romFull"
}
if (-not $OutDir) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutDir = Join-Path ([System.IO.Path]::GetTempPath()) "gwed_localization_crawl_tcp_$stamp"
}
$outFull = [System.IO.Path]::GetFullPath($OutDir)
New-Item -ItemType Directory -Force -Path $outFull | Out-Null

$statePath = Join-Path $buildPath "mods\preloaded\state.toml"
$rows = New-Object System.Collections.Generic.List[string]
$summary = New-Object System.Collections.Generic.List[string]

foreach ($lang in $Languages) {
    $port = $BasePort
    if ($Languages.Count -gt 1) {
        $port = $BasePort + [Array]::IndexOf($Languages, $lang)
    }
    $langDir = Join-Path $outFull $lang
    New-Item -ItemType Directory -Force -Path $langDir | Out-Null
    Write-StateFile $statePath $lang

    $proc = $null
    $conn = $null
    try {
        $fastForwardFrames = 1
        if ($Turbo) {
            $fastForwardFrames = $TurboFrames
        }
        $start = Get-Date
        $proc = Start-Game $exePath $buildPath $romFull $port `
            $fastForwardFrames
        $conn = Connect-Tcp $port
        $stats = Invoke-TcpLine $conn "xlate_stats"
        $statsPath = Join-Path $langDir "xlate_stats.json"
        $stats | Set-Content -LiteralPath $statsPath -Encoding ascii

        foreach ($second in $CaptureSeconds) {
            $elapsed = ((Get-Date) - $start).TotalSeconds
            $remaining = ([double]$second / $waitScale) - $elapsed
            if ($remaining -gt 0) {
                Start-Sleep -Milliseconds ([int]($remaining * 1000.0))
            }
            $shotName = "crawl_{0:000}s.bmp" -f $second
            $shotPath = Join-Path $langDir $shotName
            Take-Shot $conn $shotPath
            $rel = Join-Path $lang $shotName
            $rows.Add(('<tr><td>{0}</td><td>{1}s</td><td><img src="{2}" width="512"></td></tr>' -f $lang, $second, $rel.Replace('\', '/'))) | Out-Null
        }
        $summary.Add(("{0}: ok, stats {1}" -f $lang, $statsPath)) | Out-Null
    } finally {
        if ($null -ne $conn) {
            $conn.Reader.Dispose()
            $conn.Writer.Dispose()
            $conn.Client.Dispose()
        }
        Stop-Game $proc
    }
}

$htmlPath = Join-Path $outFull "contact.html"
@"
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Endless Duel Localization Crawl TCP Validation</title>
<style>
body { font: 14px/1.4 Segoe UI, sans-serif; margin: 24px; background: #111; color: #eee; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 1px solid #444; padding: 8px; vertical-align: top; }
img { image-rendering: pixelated; background: #000; }
</style>
</head>
<body>
<h1>Endless Duel Localization Crawl TCP Validation</h1>
<p>ROM: $([System.Net.WebUtility]::HtmlEncode($romFull))</p>
<table>
<thead><tr><th>Language</th><th>Time</th><th>Screenshot</th></tr></thead>
<tbody>
$($rows -join "`n")
</tbody>
</table>
</body>
</html>
"@ | Set-Content -LiteralPath $htmlPath -Encoding utf8

$summary | Set-Content -LiteralPath (Join-Path $outFull "summary.txt") -Encoding ascii
$pngPath = New-ContactPng $outFull $Languages $CaptureSeconds
Write-Host "Wrote TCP crawl screenshots to $outFull"
Write-Host "Open contact sheet: $htmlPath"
if ($pngPath) {
    Write-Host "PNG contact sheet: $pngPath"
}
