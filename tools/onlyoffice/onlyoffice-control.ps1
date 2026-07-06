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
$script:MainComputerOnlyOfficeNeedsFinalStatus = $false
$script:MainComputerOnlyOfficeLastComposeExitCode = 0

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
  foreach ($explicit in @($env:MAIN_COMPUTER_PYTHON_COMMAND, $env:MAIN_COMPUTER_PYTHON)) {
    if (-not [string]::IsNullOrWhiteSpace($explicit)) {
      return $explicit
    }
  }

  $py = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -ne $py -and -not [string]::IsNullOrWhiteSpace($py.Source)) {
    return $py.Source
  }

  return "python"
}

function Test-LocalTcpPortOpen {
  param([Parameter(Mandatory = $true)][int]$Port)

  try {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
      $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
      if (-not $async.AsyncWaitHandle.WaitOne(500, $false)) {
        return $false
      }
      $client.EndConnect($async)
      return $true
    }
    finally {
      $client.Close()
    }
  }
  catch {
    return $false
  }
}


$script:MainComputerContainerRuntime = $null

function ConvertTo-MainComputerStringArray([object]$Value) {
  if ($null -eq $Value) {
    return @()
  }

  if ($Value -is [System.Array]) {
    return @($Value | ForEach-Object { [string]$_ })
  }

  return @([string]$Value)
}

function Get-MainComputerCommandDisplay([object]$Command) {
  $parts = ConvertTo-MainComputerStringArray $Command
  return ($parts -join " ")
}

function Get-MainComputerContainerRuntime {
  if ($null -ne $script:MainComputerContainerRuntime) {
    return $script:MainComputerContainerRuntime
  }

  $python = Resolve-PythonCommand
  $arguments = @(
    "-m",
    "main_computer.container_runtime",
    "--check",
    "--cwd",
    $repoRoot,
    "--json"
  )

  $output = @(& $python @arguments 2>&1)
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    $detail = ($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
    throw "Main Computer container runtime resolution failed with exit code $exitCode.$([Environment]::NewLine)$detail"
  }

  $json = ($output | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Last 1)
  if ([string]::IsNullOrWhiteSpace($json)) {
    throw "Main Computer container runtime resolution returned no JSON output."
  }

  try {
    $script:MainComputerContainerRuntime = $json | ConvertFrom-Json
  } catch {
    throw "Main Computer container runtime JSON could not be parsed: $json"
  }

  return $script:MainComputerContainerRuntime
}

function Invoke-MainComputerRuntimeCommand {
  param(
    [Parameter(Mandatory = $true)][object]$Command,
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [switch]$CaptureOutput,
    [switch]$SuppressErrors
  )

  $parts = ConvertTo-MainComputerStringArray $Command
  if ($parts.Count -eq 0 -or [string]::IsNullOrWhiteSpace($parts[0])) {
    throw "Cannot invoke an empty container runtime command."
  }

  $executable = [string]$parts[0]
  $prefix = @()
  if ($parts.Count -gt 1) {
    $prefix = @($parts[1..($parts.Count - 1)])
  }
  $allArgs = @($prefix + $Arguments)

  if ($CaptureOutput) {
    if ($SuppressErrors) {
      return @(& $executable @allArgs 2>$null)
    }
    return @(& $executable @allArgs 2>&1)
  }

  if ($SuppressErrors) {
    & $executable @allArgs 2>$null
    return
  }
  & $executable @allArgs
}

function Invoke-MainComputerContainerCommand {
  param(
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [switch]$CaptureOutput,
    [switch]$SuppressErrors
  )

  $runtime = Get-MainComputerContainerRuntime
  return Invoke-MainComputerRuntimeCommand -Command $runtime.container_command -Arguments $Arguments -CaptureOutput:$CaptureOutput -SuppressErrors:$SuppressErrors
}

function Invoke-MainComputerComposeCommand {
  param(
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [switch]$CaptureOutput,
    [switch]$SuppressErrors
  )

  $runtime = Get-MainComputerContainerRuntime
  return Invoke-MainComputerRuntimeCommand -Command $runtime.compose_command -Arguments $Arguments -CaptureOutput:$CaptureOutput -SuppressErrors:$SuppressErrors
}

function Get-DockerOnlyOfficeNamedContainerId {
  $containerName = [string]$env:MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME
  if ([string]::IsNullOrWhiteSpace($containerName)) {
    return ""
  }

  $nameFilter = "name=^/$containerName$"
  $namedContainerId = (Invoke-MainComputerContainerCommand -Arguments @("ps", "-aq", "--filter", $nameFilter) -CaptureOutput -SuppressErrors | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Select-Object -First 1)
  if (-not [string]::IsNullOrWhiteSpace([string]$namedContainerId)) {
    return [string]$namedContainerId
  }

  return ""
}

function Test-DockerOnlyOfficeNamedContainerRunning {
  $containerName = [string]$env:MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME
  if ([string]::IsNullOrWhiteSpace($containerName)) {
    return $false
  }

  $running = (Invoke-MainComputerContainerCommand -Arguments @("inspect", "--format", "{{.State.Running}}", $containerName) -CaptureOutput -SuppressErrors | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Select-Object -First 1)
  return (([string]$running).Trim().ToLowerInvariant() -eq "true")
}

function Start-DockerOnlyOfficeNamedContainerIfPresent {
  $containerName = [string]$env:MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME
  if ([string]::IsNullOrWhiteSpace($containerName)) {
    return $false
  }

  $containerId = Get-DockerOnlyOfficeNamedContainerId
  if ([string]::IsNullOrWhiteSpace([string]$containerId)) {
    return $false
  }

  if (Test-DockerOnlyOfficeNamedContainerRunning) {
    Write-Host "Shared ONLYOFFICE named container already exists: $containerName ($containerId). Compose up will not recreate it."
    return $true
  }

  Write-Host "Shared ONLYOFFICE named container exists but is stopped: $containerName ($containerId). Starting it without recreating."
  Invoke-MainComputerContainerCommand -Arguments @("start", $containerName) | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Existing shared ONLYOFFICE container '$containerName' could not be started; remove or repair it before rerunning startup."
  }

  return $true
}

function Invoke-DockerComposeOnlyOffice {
  param(
    [Parameter(Mandatory = $true)][string[]]$ComposeArgs,
    [switch]$AllowFailure
  )

  $composePath = Join-Path $repoRoot $ComposeFile
  if (-not (Test-Path -LiteralPath $composePath -PathType Leaf)) {
    throw "Compose file not found: $composePath"
  }

  Invoke-MainComputerComposeCommand -Arguments (@("-f", $composePath, "-p", $ProjectName) + $ComposeArgs)
  $exitCode = [int]$LASTEXITCODE
  $script:MainComputerOnlyOfficeLastComposeExitCode = $exitCode
  if ($exitCode -ne 0 -and -not $AllowFailure) {
    throw "docker compose $($ComposeArgs -join ' ') failed with exit code $exitCode."
  }
}

function Get-DockerOnlyOfficeContainerId {
  $composePath = Join-Path $repoRoot $ComposeFile

  $composeContainerId = (Invoke-MainComputerComposeCommand -Arguments @("-f", $composePath, "-p", $ProjectName, "ps", "-q", "onlyoffice") -CaptureOutput -SuppressErrors | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Select-Object -First 1)
  if (-not [string]::IsNullOrWhiteSpace([string]$composeContainerId)) {
    return [string]$composeContainerId
  }

  $containerName = [string]$env:MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME
  if ([string]::IsNullOrWhiteSpace($containerName)) {
    return ""
  }

  $nameFilter = "name=^/$containerName$"
  $namedContainerId = (Invoke-MainComputerContainerCommand -Arguments @("ps", "-q", "--filter", $nameFilter) -CaptureOutput -SuppressErrors | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Select-Object -First 1)
  if (-not [string]::IsNullOrWhiteSpace([string]$namedContainerId)) {
    return [string]$namedContainerId
  }

  $inspectLine = (Invoke-MainComputerContainerCommand -Arguments @("inspect", "--format", "{{.State.Running}} {{.Id}}", $containerName) -CaptureOutput -SuppressErrors | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Select-Object -First 1)
  if ([string]$inspectLine -match '^true\s+(?<id>\S+)') {
    return [string]$Matches["id"]
  }

  return ""
}

function Wait-DockerOnlyOfficeContainer {
  $timeout = [Math]::Max(0, $ReadyTimeoutSeconds)
  $pollSeconds = [Math]::Max(1, $ReadyPollSeconds)
  $deadline = [DateTime]::UtcNow.AddSeconds($timeout)
  $attempt = 0

  while ($true) {
    $attempt += 1
    $containerId = Get-DockerOnlyOfficeContainerId
    if (-not [string]::IsNullOrWhiteSpace([string]$containerId)) {
      Write-Host "ONLYOFFICE container is visible after $attempt docker/container inspection attempt(s): $containerId"
      return
    }

    if ([DateTime]::UtcNow -ge $deadline) {
      break
    }

    Write-Host "[wait attempt $attempt] ONLYOFFICE container is not visible yet; retrying in ${pollSeconds}s."
    Start-Sleep -Seconds $pollSeconds
  }

  throw "Docker ONLYOFFICE container did not appear within $timeout seconds."
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

  $runtime = Get-MainComputerContainerRuntime
  $containerCommandDisplay = Get-MainComputerCommandDisplay $runtime.container_command
  $composeCommandDisplay = Get-MainComputerCommandDisplay $runtime.compose_command

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

  Write-Host "container runtime: $([string]$runtime.runtime)"
  Write-Host "container command: $containerCommandDisplay"
  Write-Host "compose command: $composeCommandDisplay"
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
      if (Test-LocalTcpPortOpen -Port $Port) {
        Write-Host "Shared ONLYOFFICE already reachable on port $Port; start path will not recreate it."
        $script:MainComputerOnlyOfficeNeedsFinalStatus = $true
        return
      }

      if (Start-DockerOnlyOfficeNamedContainerIfPresent) {
        Wait-DockerOnlyOfficeContainer
        $script:MainComputerOnlyOfficeNeedsFinalStatus = $true
        return
      }

      Invoke-DockerComposeOnlyOffice @("up", "-d", "onlyoffice") -AllowFailure
      $composeExitCode = $script:MainComputerOnlyOfficeLastComposeExitCode
      if ($composeExitCode -ne 0) {
        Write-Warning "docker compose up -d onlyoffice returned exit code $composeExitCode; waiting to see whether ONLYOFFICE still appears."
      }
      Wait-DockerOnlyOfficeContainer
      $script:MainComputerOnlyOfficeNeedsFinalStatus = $true
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

Write-Section "ONLYOFFICE container control"
Write-Host "mode: $Mode"
Write-Host "port: $Port"
Write-Host "project: $ProjectName"
Write-Host "document server URL: http://127.0.0.1:$Port"
Write-Host "Main Computer callback app port: $AppPort"

switch ($Action) {
  "install" {
    Write-Host "Container mode does not need a native install step; pulling/starting ONLYOFFICE service instead."
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

if ($script:MainComputerOnlyOfficeNeedsFinalStatus) {
  Write-Section "ONLYOFFICE final readiness recheck"
  Invoke-DockerOnlyOfficeStatus
}

Write-Host ""
Write-Host "Use these Main Computer env vars for local container ONLYOFFICE mode:"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_MODE=docker"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_PORT=$Port"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL=http://127.0.0.1:$Port"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL=http://127.0.0.1:$Port"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_BROWSER_PUBLIC_URL=http://127.0.0.1:$Port"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL=http://host.docker.internal:$AppPort"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED=$JwtEnabled"
