param(
    [string]$RomPath = $env:SNESRECOMP_ROM,
    [string]$BuildDir = "build-local-xlate-trace",
    [string[]]$Languages = @("en", "es", "fr", "it", "pt"),
    [string[]]$CaptureSeconds = @("55", "60"),
    [string]$OutDir = "",
    [int]$BasePort = 4670
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

function Start-Game([string]$ExePath, [string]$WorkDir, [string]$Rom, [int]$Port) {
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $ExePath
    $psi.WorkingDirectory = $WorkDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.Arguments = ('--no-launcher "{0}"' -f $Rom.Replace('"', '\"'))
    $psi.Environment["SDL_VIDEODRIVER"] = "dummy"
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

if (-not $RomPath) {
    throw "Pass -RomPath or set SNESRECOMP_ROM to an extracted .sfc/.smc ROM path."
}
if (-not $CaptureSeconds -or $CaptureSeconds.Count -eq 0) {
    throw "Pass at least one capture second."
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
        $start = Get-Date
        $proc = Start-Game $exePath $buildPath $romFull $port
        $conn = Connect-Tcp $port
        $stats = Invoke-TcpLine $conn "xlate_stats"
        $statsPath = Join-Path $langDir "xlate_stats.json"
        $stats | Set-Content -LiteralPath $statsPath -Encoding ascii

        foreach ($second in $CaptureSeconds) {
            $elapsed = ((Get-Date) - $start).TotalSeconds
            $remaining = [double]$second - $elapsed
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
Write-Host "Wrote TCP crawl screenshots to $outFull"
Write-Host "Open contact sheet: $htmlPath"
