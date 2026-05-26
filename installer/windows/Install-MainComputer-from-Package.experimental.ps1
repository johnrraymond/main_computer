# Experimental native-installer package launcher.
#
# This file is intentionally separate from the repository-root Python installer
# scripts while the native installer is being proven out. The setup EXE installs
# a payload under its own application directory, then this wrapper delegates to
# the existing Python-owned bootstrap script inside that payload.

[CmdletBinding()]
param(
    [ValidateSet("test", "prod")]
    [string]$RuntimeProfile = "prod",

    [string]$Mode = "Unleashed",

    [string]$InstallRoot = "",

    [switch]$AllowReHome,

    [switch]$SkipAppStart,

    [switch]$PrecheckOnly,

    [switch]$NoPythonDownload,

    [switch]$VerboseBootstrap,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingBootstrapArgs = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    throw "[main-computer-package-installer] $Message"
}

function Resolve-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
}

$packageRoot = Resolve-FullPath (Split-Path -Parent $MyInvocation.MyCommand.Path)
$payloadRoot = Join-Path $packageRoot "payload\main_computer_test"
$bootstrapScript = Join-Path $payloadRoot "bootstrap-main-computer-python-windows.ps1"

if (-not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    Fail "Packaged payload directory was not found: $payloadRoot"
}
if (-not (Test-Path -LiteralPath $bootstrapScript -PathType Leaf)) {
    Fail "Packaged Python bootstrap script was not found: $bootstrapScript"
}

$bootstrapArgs = @(
    "-RepoRoot", $payloadRoot,
    "-RuntimeProfile", $RuntimeProfile,
    "-Mode", $Mode
)

if (-not [string]::IsNullOrWhiteSpace($InstallRoot)) {
    $bootstrapArgs += @("-InstallRoot", $InstallRoot)
}
if (-not $AllowReHome) {
    $bootstrapArgs += "-NoReHome"
}
if ($SkipAppStart) {
    $bootstrapArgs += "-SkipAppStart"
}
if ($PrecheckOnly) {
    $bootstrapArgs += "-PrecheckOnly"
}
if ($NoPythonDownload) {
    $bootstrapArgs += "-NoPythonDownload"
}
if ($VerboseBootstrap) {
    $bootstrapArgs += "-VerboseBootstrap"
}
if ($RemainingBootstrapArgs.Count -gt 0) {
    $bootstrapArgs += $RemainingBootstrapArgs
}

Write-Host "Running packaged Main Computer Python installer from:"
Write-Host "  $payloadRoot"
Write-Host ""

& $bootstrapScript @bootstrapArgs
exit $LASTEXITCODE
