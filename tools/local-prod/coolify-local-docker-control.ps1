param(
    [Parameter(Position = 0)]
    [ValidateSet("preflight", "init", "up", "down", "status", "wait", "migrate", "bootstrap", "onboard", "auth-smoke", "api-smoke", "ensure-infra", "deploy-smoke", "logs", "reset")]
    [string]$Action = "status",

    [switch]$Force,
    [switch]$Yes,
    [string]$ProjectName = "",
    [string]$StateDir = "",
    [int]$AppPort = 0,
    [int]$SoketiPort = 0,
    [int]$SoketiTerminalPort = 0
)

$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "coolify-local-docker.py"
if (-not (Test-Path $scriptPath)) {
    throw "Missing local Docker Coolify smoke script: $scriptPath"
}

$python = "python"
$arguments = @($scriptPath, $Action)
if ($Force) {
    $arguments += "--force"
}
if ($Yes) {
    $arguments += "--yes"
}
if (-not [string]::IsNullOrWhiteSpace($ProjectName)) {
    $arguments += @("--project-name", $ProjectName)
}
if (-not [string]::IsNullOrWhiteSpace($StateDir)) {
    $arguments += @("--state-dir", $StateDir)
}
if ($AppPort -gt 0) {
    $arguments += @("--app-port", "$AppPort")
}
if ($SoketiPort -gt 0) {
    $arguments += @("--soketi-port", "$SoketiPort")
}
if ($SoketiTerminalPort -gt 0) {
    $arguments += @("--soketi-terminal-port", "$SoketiTerminalPort")
}

& $python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Local Docker Coolify smoke command failed with exit code $LASTEXITCODE."
}
