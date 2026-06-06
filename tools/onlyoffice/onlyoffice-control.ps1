param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("install", "start", "stop", "status", "doctor")]
  [string]$Action,

  [ValidateSet("docker")]
  [string]$Mode = "docker",

  [int]$Port = 18085,

  [int]$AppPort = 8765,

  [string]$JwtSecret = "",

  [string]$JwtEnabled = "",

  [string]$ComposeFile = "docker-compose.onlyoffice.yml",

  [string]$ProjectName = "",

  [int]$ReadyTimeoutSeconds = 300,

  [int]$ReadyPollSeconds = 5
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptRoot)

function Write-Section {
  param([Parameter(Mandatory = $true)][string]$Title)

  Write-Host ""
  Write-Host $Title
  Write-Host ("-" * $Title.Length)
}

function ConvertTo-MainComputerBoolText {
  param(
    [string]$Value,
    [bool]$Default
  )

  $normalized = ([string]$Value).Trim().ToLowerInvariant()
  if ([string]::IsNullOrWhiteSpace($normalized)) {
    return $(if ($Default) { "true" } else { "false" })
  }
  if (@("1", "true", "yes", "on", "enabled") -contains $normalized) {
    return "true"
  }
  if (@("0", "false", "no", "off", "disabled") -contains $normalized) {
    return "false"
  }

  return $(if ($Default) { "true" } else { "false" })
}

function Resolve-PythonCommand {
  $explicit = $env:MAIN_COMPUTER_PYTHON
  if (-not [string]::IsNullOrWhiteSpace($explicit)) {
    return $explicit
  }

  $py = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -ne $py -and -not [string]::IsNullOrWhiteSpace($py.Source)) {
    return $py.Source
  }

  return "python"
}

function Invoke-DockerComposeOnlyOffice {
  param([Parameter(Mandatory = $true)][string[]]$ComposeArgs)

  $composePath = Join-Path $repoRoot $ComposeFile
  if (-not (Test-Path -LiteralPath $composePath -PathType Leaf)) {
    throw "Compose file not found: $composePath"
  }

  & docker compose -f $composePath -p $ProjectName @ComposeArgs
  if ($LASTEXITCODE -ne 0) {
    throw "docker compose $($ComposeArgs -join ' ') failed with exit code $LASTEXITCODE."
  }
}

function Invoke-DockerOnlyOfficeStatus {
  Invoke-DockerComposeOnlyOffice @("ps", "onlyoffice")

  $python = Resolve-PythonCommand
  $checkScript = Join-Path $repoRoot "tools/onlyoffice/check-onlyoffice.py"
  & $python $checkScript --url "http://127.0.0.1:$Port" --wait-seconds $ReadyTimeoutSeconds --poll-seconds $ReadyPollSeconds
  if ($LASTEXITCODE -ne 0) {
    throw "Docker ONLYOFFICE status check failed with exit code $LASTEXITCODE."
  }
}

function Invoke-DockerOnlyOffice {
  param([Parameter(Mandatory = $true)][string]$DockerAction)

  if (-not (Get-Command docker -CommandType Application -ErrorAction SilentlyContinue)) {
    throw "docker was not found on PATH. Install/start Docker Desktop, then rerun Main Computer startup."
  }

  $composePath = Join-Path $repoRoot $ComposeFile
  if (-not (Test-Path -LiteralPath $composePath -PathType Leaf)) {
    throw "Compose file not found: $composePath"
  }

  $env:MAIN_COMPUTER_ONLYOFFICE_PORT = [string]$Port
  $env:MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED = $JwtEnabled
  $env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET = $JwtSecret

  if (-not $env:MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME) {
    $env:MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME = "main-computer-onlyoffice-documentserver"
  }
  if (-not $env:MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS) {
    $env:MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS = "true"
  }
  if (-not $env:MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS) {
    $env:MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS = "true"
  }

  $env:MAIN_COMPUTER_ONLYOFFICE_PROJECT = $ProjectName
  $env:COMPOSE_PROJECT_NAME = $ProjectName

  Write-Host "compose file: $composePath"
  Write-Host "compose project: $ProjectName"
  Write-Host "container name: $env:MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME"
  Write-Host "published URL: http://127.0.0.1:$Port"
  Write-Host "callback/file URL base for container: http://host.docker.internal:$AppPort"
  Write-Host "JWT enabled: $JwtEnabled"
  Write-Host "allow private IP downloads: $env:MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS"
  Write-Host "allow meta IP downloads: $env:MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS"

  switch ($DockerAction) {
    "start" {
      Invoke-DockerComposeOnlyOffice @("up", "-d", "onlyoffice")
      Invoke-DockerOnlyOfficeStatus
    }
    "stop" {
      Invoke-DockerComposeOnlyOffice @("stop", "onlyoffice")
    }
    "status" {
      Invoke-DockerOnlyOfficeStatus
    }
    default {
      throw "Unsupported Docker action: $DockerAction"
    }
  }
}

$JwtEnabled = ConvertTo-MainComputerBoolText $JwtEnabled $false

if (-not $JwtSecret) {
  if ($env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET) {
    $JwtSecret = $env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET
  } elseif ($JwtEnabled -eq "true") {
    $JwtSecret = "main-computer-onlyoffice-local-secret"
  } else {
    $JwtSecret = ""
  }
}

if (-not $ProjectName) {
  if ($env:MAIN_COMPUTER_ONLYOFFICE_PROJECT) {
    $ProjectName = $env:MAIN_COMPUTER_ONLYOFFICE_PROJECT
  } elseif ($env:COMPOSE_PROJECT_NAME) {
    $ProjectName = $env:COMPOSE_PROJECT_NAME
  } else {
    $ProjectName = "main-computer-onlyoffice"
  }
}

Write-Section "ONLYOFFICE Docker control"
Write-Host "mode: docker"
Write-Host "port: $Port"
Write-Host "project: $ProjectName"
Write-Host "document server URL: http://127.0.0.1:$Port"
Write-Host "Main Computer callback app port: $AppPort"

switch ($Action) {
  "install" {
    Write-Host "Docker mode does not need a native install step; pulling/starting ONLYOFFICE service instead."
    Invoke-DockerOnlyOffice "start"
  }
  "start" {
    Invoke-DockerOnlyOffice "start"
  }
  "stop" {
    Invoke-DockerOnlyOffice "stop"
  }
  "status" {
    Invoke-DockerOnlyOffice "status"
  }
  "doctor" {
    Invoke-DockerOnlyOffice "status"
  }
}

Write-Host ""
Write-Host "Use these Main Computer env vars for local Docker ONLYOFFICE mode:"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_MODE=docker"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_PORT=$Port"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL=http://127.0.0.1:$Port"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL=http://127.0.0.1:$Port"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_BROWSER_PUBLIC_URL=http://127.0.0.1:$Port"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL=http://host.docker.internal:$AppPort"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED=$JwtEnabled"
