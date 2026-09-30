[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Manifest,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [string]$YtDlp = "yt-dlp",
    [string]$FfmpegLocation,
    [string]$CookiesFromBrowser,
    [string]$Proxy,
    [ValidateRange(144,4320)][int]$MinHeight = 720,
    [ValidateRange(144,4320)][int]$MaxHeight = 1080,
    [switch]$PlanOnly,
    [ValidateRange(1,8)][int]$Parallel = 1,
    [switch]$UseAria2,
    [ValidateRange(1,16)][int]$Aria2Connections = 8,
    [switch]$Trace,
    [string]$JsRuntimes
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$srcRoot = Join-Path $repoRoot "src"
if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $env:PYTHONPATH = $srcRoot
} else {
    $env:PYTHONPATH = "$srcRoot$([IO.Path]::PathSeparator)$($env:PYTHONPATH)"
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "Python not found. AutoYY requires Python 3.11+."
    exit 2
}

$argsList = @(
    "-m", "autoyy", "download",
    "--manifest", $Manifest,
    "--output-root", $OutputRoot,
    "--yt-dlp", $YtDlp,
    "--min-height", [string]$MinHeight,
    "--max-height", [string]$MaxHeight,
    "--parallel", [string]$Parallel,
    "--aria2-connections", [string]$Aria2Connections
)
if ($FfmpegLocation) { $argsList += @("--ffmpeg-location", $FfmpegLocation) }
if ($CookiesFromBrowser) { $argsList += @("--cookies-from-browser", $CookiesFromBrowser) }
if ($Proxy) { $argsList += @("--proxy", $Proxy) }
if ($JsRuntimes) { $argsList += @("--js-runtimes", $JsRuntimes) }
if ($PlanOnly) { $argsList += "--plan-only" }
if ($UseAria2) { $argsList += "--use-aria2" }
if ($Trace) { $argsList += "--trace" }

& $python.Source @argsList
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 2 }
exit $code
