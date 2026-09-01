param(
    [string]$RomPath = $env:SNESRECOMP_ROM,
    [string]$BuildDir = "build-local-xlate-trace",
    [string[]]$Languages = @("off", "en", "es", "fr", "it", "pt", "tl", "id", "zh", "ko"),
    [string]$OutDir = "",
    [int]$BasePort = 4870,
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
    if ($Language -eq "off") {
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
language = "off"
"@ | Set-Content -LiteralPath $StatePath -NoNewline -Encoding ascii
        return
    }
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
        return $parsed
    } catch {
        if ($json -notmatch '"ok":true') {
            throw "screenshot command returned unexpected response: $json"
        }
        $fallback = [pscustomobject]@{
            ok = $true
            raw = $json
        }
        if ($json -match '"frame":([0-9]+)') {
            $fallback | Add-Member -MemberType NoteProperty -Name frame -Value ([int]$Matches[1])
        }
        return $fallback
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

function Save-TcpJson($Conn, [string]$Command, [string]$Path) {
    $json = Invoke-TcpLine $Conn $Command
    $json | Set-Content -LiteralPath $Path -Encoding ascii
    return $json
}

function Add-ContactRow($Rows, [string]$Language, [string]$RelativePath) {
    $Rows.Add(('<tr><td>{0}</td><td><img src="{1}" width="512"></td></tr>' -f $Language, $RelativePath.Replace('\', '/'))) | Out-Null
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
    $OutDir = Join-Path ([System.IO.Path]::GetTempPath()) "gwed_title_menu_vram_$stamp"
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
        Tap-Button $conn "start" 150 3200
        Tap-Button $conn "start" 150 900
        Tap-Button $conn "a" 150 900
        Tap-Button $conn "a" 150 1500
        Tap-Button $conn "start" 150 2600

        $shotPath = Join-Path $langDir "mode_menu.bmp"
        $shot = Take-Shot $conn $shotPath
        $shot | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $langDir "screenshot_response.json") -Encoding ascii
        $frame = $null
        if ($shot -isnot [string] -and $null -ne $shot.frame) {
            $frame = [int]$shot.frame
            $presentedFrame = $frame - 1
            if ($presentedFrame -lt 0) {
                $presentedFrame = $frame
            }
            Save-TcpJson $conn ("dump_frame_vram {0} 0 65536" -f $presentedFrame) (Join-Path $langDir "vram.json") | Out-Null
            Save-TcpJson $conn ("dump_frame_vram {0} 0 65536" -f $frame) (Join-Path $langDir "next_vram.json") | Out-Null
        } else {
            Save-TcpJson $conn "dump_vram 0 65536" (Join-Path $langDir "vram.json") | Out-Null
        }
        Save-TcpJson $conn "dump_vram 0 65536" (Join-Path $langDir "live_vram.json") | Out-Null
        Save-TcpJson $conn "xlate_stats" (Join-Path $langDir "xlate_stats.json") | Out-Null
        Save-TcpJson $conn "get_ppu_state" (Join-Path $langDir "ppu_state.json") | Out-Null
        Save-TcpJson $conn "raster_journal" (Join-Path $langDir "raster_journal.json") | Out-Null
        Save-TcpJson $conn "ppu_lines 120 205" (Join-Path $langDir "ppu_lines_menu.json") | Out-Null
        Save-TcpJson $conn "dump_cgram" (Join-Path $langDir "cgram.json") | Out-Null
        Save-TcpJson $conn "dump_oam" (Join-Path $langDir "oam.json") | Out-Null
        Add-ContactRow $rows $lang (Join-Path $lang "mode_menu.bmp")
        if ($null -ne $frame) {
            $summary.Add(("{0}: wrote mode menu screenshot from presented frame {1} and next frame {2} VRAM/PPU dumps" -f $lang, ($frame - 1), $frame)) | Out-Null
        } else {
            $summary.Add(("{0}: wrote mode menu screenshot and live VRAM/PPU dumps" -f $lang)) | Out-Null
        }
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
<title>Endless Duel Title Menu VRAM Probe</title>
<style>
body { font: 14px/1.4 Segoe UI, sans-serif; margin: 24px; background: #111; color: #eee; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 1px solid #444; padding: 8px; vertical-align: top; }
img { image-rendering: pixelated; background: #000; }
</style>
</head>
<body>
<h1>Endless Duel Title Menu VRAM Probe</h1>
<p>ROM: $([System.Net.WebUtility]::HtmlEncode($romFull))</p>
<table>
<thead><tr><th>Language</th><th>Mode Menu</th></tr></thead>
<tbody>
$($rows -join "`n")
</tbody>
</table>
</body>
</html>
"@ | Set-Content -LiteralPath $htmlPath -Encoding utf8

$summary | Set-Content -LiteralPath (Join-Path $outFull "summary.txt") -Encoding ascii
Write-Host "Wrote title/menu VRAM probe to $outFull"
Write-Host "Open contact sheet: $htmlPath"
