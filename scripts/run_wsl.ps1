[CmdletBinding()]
param(
  [ValidateSet("daily", "weekly", "monthly")]
  [string]$Job = "daily",

  [string]$Date,

  [string]$Distro,

  [switch]$DryRun,

  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$ExtraArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$wsl = (Get-Command wsl.exe -ErrorAction Stop).Source
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRootWin = (Resolve-Path (Join-Path $scriptDir "..")).Path

function ConvertTo-WslPathFallback {
  param([Parameter(Mandatory = $true)][string]$WindowsPath)

  if ($WindowsPath -match "^([A-Za-z]):\\(.*)$") {
    $drive = $Matches[1].ToLowerInvariant()
    $rest = $Matches[2] -replace "\\", "/"
    return "/mnt/$drive/$rest"
  }

  throw "Unable to convert Windows path to WSL path: $WindowsPath"
}

$wslPrefixArgs = @()
if (-not [string]::IsNullOrWhiteSpace($Distro)) {
  $wslPrefixArgs += @("-d", $Distro)
}

$repoRootWsl = $null
if ($repoRootWin -match "^[A-Za-z]:\\") {
  $repoRootWsl = ConvertTo-WslPathFallback -WindowsPath $repoRootWin
} else {
  $wslPathArgs = @()
  $wslPathArgs += $wslPrefixArgs
  $wslPathArgs += @("wslpath", "-u", $repoRootWin)
  $repoRootWslOutput = & $wsl @wslPathArgs 2>$null
  $repoRootWsl = ($repoRootWslOutput | Select-Object -First 1)
  if ($repoRootWsl) {
    $repoRootWsl = $repoRootWsl.Trim()
  }
  if ([string]::IsNullOrWhiteSpace($repoRootWsl)) {
    throw "Failed to convert repository path to a WSL path: $repoRootWin"
  }
}

$pipelineScript = "scripts/run_$Job.sh"
$pipelineArgs = @()
if (-not [string]::IsNullOrWhiteSpace($Date)) {
  $pipelineArgs += @("--date", $Date)
}
if ($ExtraArgs) {
  $pipelineArgs += $ExtraArgs
}

$wslArgs = @()
$wslArgs += $wslPrefixArgs
$wslArgs += @(
  "bash",
  "-lc",
  'cd "$1" && bash "$2" "${@:3}"',
  "--",
  $repoRootWsl,
  $pipelineScript
)
$wslArgs += $pipelineArgs

Write-Host "Running AI Researcher via WSL: $Job"
Write-Host "Repository: $repoRootWsl"
if (-not [string]::IsNullOrWhiteSpace($Distro)) {
  Write-Host "Distro: $Distro"
}

if ($DryRun) {
  Write-Host "Dry run only. The pipeline was not started."
  Write-Host ("Command: " + $wsl + " " + ($wslArgs -join " "))
  exit 0
}

& $wsl @wslArgs
exit $LASTEXITCODE
