<#
.SYNOPSIS
  Boot the build, load a banked savestate, let the scene redraw, capture.

.DESCRIPTION
  The fast A/B harness for localization work. Instead of replaying the game to
  reach a text surface, load a state banked just BEFORE that surface draws its
  text, let the game redraw it, and screenshot. Because the loading process
  applied its own language's patches to its own in-memory cart image at boot,
  the redraw comes out in that language -- so the same state validates every
  language.

  IMPORTANT (cross-language semantics): a state carries the VRAM of the
  process that SAVED it. Immediately after a load, on-screen text is still the
  saving language's. Only the next redraw of the surface is in the loading
  language. Always bank states BEFORE the text draws, and always allow
  -WaitSeconds for the redraw before trusting the capture.

.EXAMPLE
  scripts\validate_from_state.ps1 -State pre_quote -Language ko -WaitSeconds 12
  scripts\validate_from_state.ps1 -State pre_ending -Language it -Advance 6
#>
[CmdletBinding()]
param(
    # State name (without .state) under -StateDir, or a full path to a file.
    [Parameter(Mandatory = $true)][string]$State,
    [string]$Language = "en",
    [string]$BuildDir = "build-agent",
    [int]$Port = 6650,
    [double]$BootSeconds = 12,
    [double]$WaitSeconds = 10,
    # Number of A presses after the load, to walk a text sequence forward.
    [int]$Advance = 0,
    # Seconds to hold the PAR HP freezes after the load (P1 invincible, P2 at
    # 1 HP, infinite time). Use with a mid-fight state to force the round to
    # end so the victory-quote screen draws.
    [double]$ParFreezeSeconds = 0,
    [string]$OutDir = "",
    [string]$StateDir = "tools/validation_states",
    [switch]$DumpVram
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$build = Join-Path $repo $BuildDir
$exe = Join-Path $build "GundamWingEndlessDuelSNESRecomp.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "no exe: $exe" }

$rom = Get-ChildItem -LiteralPath $repo -Filter "*.smc" | Select-Object -First 1
if ($null -eq $rom) { throw "no .smc ROM at the repo root" }

$statePath = $State
if (-not (Test-Path -LiteralPath $statePath)) {
    $statePath = Join-Path $repo (Join-Path $StateDir "$State.state")
}
if (-not (Test-Path -LiteralPath $statePath)) { throw "no state: $statePath" }

if ([string]::IsNullOrEmpty($OutDir)) {
    $label = [System.IO.Path]::GetFileNameWithoutExtension($statePath)
    $OutDir = Join-Path $repo "analysis/state_validation/$label`_$Language"
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# The mod runtime reads the selected language from the build's preloaded state.
$stateToml = Join-Path $build "mods/preloaded/state.toml"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $stateToml) | Out-Null
$body = @"
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
"@
[System.IO.File]::WriteAllText($stateToml, $body, (New-Object System.Text.UTF8Encoding($false)))

$env:SNESRECOMP_DEBUG_PORT = "$Port"
$env:SDL_AUDIODRIVER = "dummy"
$env:SDL_VIDEODRIVER = "dummy"
# The ROM filename contains spaces: Start-Process joins ArgumentList with
# spaces and does NOT quote for you, so quote it here or the game sees three
# separate arguments and never opens the cart.
$proc = Start-Process -FilePath $exe -ArgumentList @("--no-launcher", ('"' + $rom.FullName + '"')) `
    -WorkingDirectory $build -PassThru

function Invoke-Dbg([System.IO.StreamWriter]$w, [System.IO.StreamReader]$r, [string]$cmd) {
    $w.WriteLine($cmd); $w.Flush(); return $r.ReadLine()
}

try {
    $client = $null
    $deadline = (Get-Date).AddSeconds(40)
    while ((Get-Date) -lt $deadline) {
        try { $client = New-Object System.Net.Sockets.TcpClient("127.0.0.1", $Port); break }
        catch { Start-Sleep -Milliseconds 400 }
    }
    if ($null -eq $client) { throw "debug server never answered on port $Port" }
    $stream = $client.GetStream()
    $reader = New-Object System.IO.StreamReader($stream)
    $writer = New-Object System.IO.StreamWriter($stream)

    Start-Sleep -Seconds $BootSeconds
    Write-Host (Invoke-Dbg $writer $reader "xlate_stats")
    $lp = $statePath.Replace("\", "/")
    Write-Host (Invoke-Dbg $writer $reader "load_state $lp")

    if ($ParFreezeSeconds -gt 0) {
        # PAR codes (GameFAQs, Darth_Nemesis), verified live on the JP ROM:
        #   7E1B70 P1 health  7E1B74 P2 health
        #   7E1B80 P1 energy  7E1B84 P2 energy   7E060C timer
        $stop = (Get-Date).AddSeconds($ParFreezeSeconds)
        while ((Get-Date) -lt $stop) {
            foreach ($w in @("1b70 ff70", "1b80 2c01", "1b74 0100", "1b84 0100", "060c 99")) {
                Invoke-Dbg $writer $reader "write_ram $w" | Out-Null
            }
            Invoke-Dbg $writer $reader "set_controller a" | Out-Null
            Start-Sleep -Milliseconds 120
            Invoke-Dbg $writer $reader "clear_controller" | Out-Null
            Start-Sleep -Milliseconds 400
        }
    }

    for ($i = 0; $i -lt $Advance; $i++) {
        Invoke-Dbg $writer $reader "set_controller a" | Out-Null
        Start-Sleep -Milliseconds 140
        Invoke-Dbg $writer $reader "clear_controller" | Out-Null
        Start-Sleep -Milliseconds 700
    }
    Start-Sleep -Seconds $WaitSeconds

    $shot = (Join-Path $OutDir "shot.bmp").Replace("\", "/")
    Write-Host (Invoke-Dbg $writer $reader "screenshot $shot")
    Invoke-Dbg $writer $reader "get_ppu_state" | Set-Content -Encoding utf8 (Join-Path $OutDir "ppu.json")
    Invoke-Dbg $writer $reader "dump_cgram"    | Set-Content -Encoding utf8 (Join-Path $OutDir "cgram.json")
    if ($DumpVram) {
        Invoke-Dbg $writer $reader "dump_vram 0 65536" | Set-Content -Encoding utf8 (Join-Path $OutDir "vram.json")
    }
    Write-Host "captured to $OutDir"
}
finally {
    if ($null -ne $client) { $client.Close() }
    if (-not $proc.HasExited) { $proc.Kill() }
}
