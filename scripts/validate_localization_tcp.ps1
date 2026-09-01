param(
    [string]$RomPath = $env:SNESRECOMP_ROM,
    [string]$BuildDir = "build-local-xlate-trace",
    [string[]]$Languages = @("en", "es", "fr", "it", "pt", "tl", "id", "zh", "ko", "th"),
    [string]$OutDir = "",
    [int]$BasePort = 4370,
    [switch]$SkipOptionScreens,
    [switch]$Visible
)

$ErrorActionPreference = "Stop"

if ($Languages.Count -eq 1 -and $Languages[0].Contains(",")) {
    $Languages = $Languages[0].Split(",") |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
}

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

function Start-Game([string]$ExePath, [string]$WorkDir, [string]$Rom, [int]$Port, [bool]$ShowWindow) {
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $ExePath
    $psi.WorkingDirectory = $WorkDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = -not $ShowWindow
    $psi.Arguments = ('--no-launcher "{0}"' -f $Rom.Replace('"', '\"'))
    if (-not $ShowWindow) {
        $psi.Environment["SDL_VIDEODRIVER"] = "dummy"
    }
    $psi.Environment["SDL_AUDIODRIVER"] = "dummy"
    $psi.Environment["SNESRECOMP_DEBUG_PORT"] = [string]$Port
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

function Tap-Button($Conn, [string]$Button, [int]$HoldMs = 140, [int]$AfterMs = 450) {
    Invoke-TcpLine $Conn "set_controller $Button" | Out-Null
    Start-Sleep -Milliseconds $HoldMs
    Invoke-TcpLine $Conn "clear_controller" | Out-Null
    Start-Sleep -Milliseconds $AfterMs
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

function Add-ContactRow($Rows, [string]$Language, [string]$Step, [string]$RelativePath) {
    $Rows.Add(('<tr><td>{0}</td><td>{1}</td><td><img src="{2}" width="512"></td></tr>' -f $Language, $Step, $RelativePath.Replace('\', '/'))) | Out-Null
}

function New-ContactPng([string]$Root, [string[]]$LanguageList) {
    try {
        Add-Type -AssemblyName System.Drawing
    } catch {
        return ""
    }

    $entries = New-Object System.Collections.Generic.List[object]
    foreach ($lang in $LanguageList) {
        $langDir = Join-Path $Root $lang
        if (-not (Test-Path -LiteralPath $langDir)) {
            continue
        }
        Get-ChildItem -LiteralPath $langDir -File -Filter "*.bmp" |
            Sort-Object Name |
            ForEach-Object {
                $entries.Add([pscustomobject]@{
                    Language = $lang.ToUpperInvariant()
                    Name = $_.Name
                    Path = $_.FullName
                }) | Out-Null
            }
    }

    if ($entries.Count -eq 0) {
        return ""
    }

    $first = [System.Drawing.Bitmap]::new($entries[0].Path)
    $w = $first.Width
    $h = $first.Height
    $labelH = 26
    $cols = 3
    $rows = [int][Math]::Ceiling($entries.Count / $cols)
    $font = [System.Drawing.Font]::new("Segoe UI", 10)
    $canvas = [System.Drawing.Bitmap]::new($w * $cols, ($h + $labelH) * $rows)
    $g = [System.Drawing.Graphics]::FromImage($canvas)
    $g.Clear([System.Drawing.Color]::FromArgb(17, 17, 17))

    for ($i = 0; $i -lt $entries.Count; $i++) {
        $bmp = [System.Drawing.Bitmap]::new($entries[$i].Path)
        $x = ($i % $cols) * $w
        $y = [Math]::Floor($i / $cols) * ($h + $labelH)
        $label = "{0} {1}" -f $entries[$i].Language, $entries[$i].Name
        $g.DrawString($label, $font, [System.Drawing.Brushes]::White, $x + 4, $y + 4)
        $g.DrawImage($bmp, $x, $y + $labelH, $w, $h)
        $bmp.Dispose()
    }

    $pngPath = Join-Path $Root "contact.png"
    $g.Dispose()
    $font.Dispose()
    $first.Dispose()
    $canvas.Save($pngPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $canvas.Dispose()
    return $pngPath
}

if (-not $RomPath) {
    throw "Pass -RomPath or set SNESRECOMP_ROM to an extracted .sfc/.smc ROM path."
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
    $OutDir = Join-Path ([System.IO.Path]::GetTempPath()) "gwed_localization_tcp_$stamp"
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
        $proc = Start-Game $exePath $buildPath $romFull $port ([bool]$Visible)
        $conn = Connect-Tcp $port
        Start-Sleep -Milliseconds 2200
        $stats = Invoke-TcpLine $conn "xlate_stats"
        $statsPath = Join-Path $langDir "xlate_stats.json"
        $stats | Set-Content -LiteralPath $statsPath -Encoding ascii

        $shots = @(
            @{ Name = "00_story_or_boot.bmp"; Action = { Start-Sleep -Milliseconds 1 } },
            @{ Name = "01_title_after_skip.bmp"; Action = { Tap-Button $conn "start" 150 3200 } },
            @{ Name = "02_after_title_start.bmp"; Action = { Tap-Button $conn "start" 150 900 } },
            @{ Name = "03_after_mode_accept.bmp"; Action = { Tap-Button $conn "a" 150 900 } },
            @{ Name = "04_after_character_accept.bmp"; Action = { Tap-Button $conn "a" 150 1500 } },
            @{ Name = "05_menu_after_start.bmp"; Action = { Tap-Button $conn "start" 150 2600 } },
            @{ Name = "06_after_menu_accept.bmp"; Action = { Tap-Button $conn "a" 150 1200 } },
            @{ Name = "07_after_character_accept.bmp"; Action = { Tap-Button $conn "a" 150 1500 } },
            @{ Name = "08_fight_or_vs.bmp"; Action = { Tap-Button $conn "start" 150 3000 } }
        )

        foreach ($shot in $shots) {
            & $shot.Action
            $shotPath = Join-Path $langDir $shot.Name
            Take-Shot $conn $shotPath
            $rel = Join-Path $lang $shot.Name
            Add-ContactRow $rows $lang $shot.Name $rel
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

    if (-not $SkipOptionScreens) {
        $optionProc = $null
        $optionConn = $null
        try {
            Write-StateFile $statePath $lang
            $optionProc = Start-Game $exePath $buildPath $romFull ($port + 1000) ([bool]$Visible)
            $optionConn = Connect-Tcp ($port + 1000)
            Start-Sleep -Milliseconds 2200
            $optionStats = Invoke-TcpLine $optionConn "xlate_stats"
            $optionStatsPath = Join-Path $langDir "option_xlate_stats.json"
            $optionStats | Set-Content -LiteralPath $optionStatsPath -Encoding ascii

            Tap-Button $optionConn "start" 150 3200
            Tap-Button $optionConn "start" 150 900
            Tap-Button $optionConn "a" 150 900
            Tap-Button $optionConn "a" 150 1500
            Tap-Button $optionConn "start" 150 2600
            Tap-Button $optionConn "down" 150 300
            Tap-Button $optionConn "down" 150 300
            Tap-Button $optionConn "down" 150 500

            $optionShots = @(
                @{ Name = "09_option_selected.bmp"; Action = { Start-Sleep -Milliseconds 1 } },
                @{ Name = "10_option_screen.bmp"; Action = { Tap-Button $optionConn "start" 150 900; Tap-Button $optionConn "a" 150 2500 } },
                @{ Name = "11_option_level_right.bmp"; Action = { Tap-Button $optionConn "right" 150 900 } },
                @{ Name = "12_option_controls_selected.bmp"; Action = { Tap-Button $optionConn "left" 150 900; Tap-Button $optionConn "down" 150 500; Tap-Button $optionConn "down" 150 500; Tap-Button $optionConn "down" 150 500 } },
                @{ Name = "13_option_key_config.bmp"; Action = { Tap-Button $optionConn "start" 150 1800 } }
            )

            foreach ($shot in $optionShots) {
                & $shot.Action
                $shotPath = Join-Path $langDir $shot.Name
                Take-Shot $optionConn $shotPath
                $rel = Join-Path $lang $shot.Name
                Add-ContactRow $rows $lang $shot.Name $rel
            }

            $summary.Add(("{0}: option route ok, stats {1}" -f $lang, $optionStatsPath)) | Out-Null
        } finally {
            if ($null -ne $optionConn) {
                $optionConn.Reader.Dispose()
                $optionConn.Writer.Dispose()
                $optionConn.Client.Dispose()
            }
            Stop-Game $optionProc
        }
    }
}

$htmlPath = Join-Path $outFull "contact.html"
@"
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Endless Duel Localization TCP Validation</title>
<style>
body { font: 14px/1.4 Segoe UI, sans-serif; margin: 24px; background: #111; color: #eee; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 1px solid #444; padding: 8px; vertical-align: top; }
img { image-rendering: pixelated; background: #000; }
</style>
</head>
<body>
<h1>Endless Duel Localization TCP Validation</h1>
<p>ROM: $([System.Net.WebUtility]::HtmlEncode($romFull))</p>
<table>
<thead><tr><th>Language</th><th>Step</th><th>Screenshot</th></tr></thead>
<tbody>
$($rows -join "`n")
</tbody>
</table>
</body>
</html>
"@ | Set-Content -LiteralPath $htmlPath -Encoding utf8

$summary | Set-Content -LiteralPath (Join-Path $outFull "summary.txt") -Encoding ascii
$pngPath = New-ContactPng $outFull $Languages
Write-Host "Wrote TCP localization screenshots to $outFull"
Write-Host "Open contact sheet: $htmlPath"
if ($pngPath) {
    Write-Host "PNG contact sheet: $pngPath"
}
