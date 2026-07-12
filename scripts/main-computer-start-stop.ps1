[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("start", "stop", "status", "dev-hub-start")]
  [string]$Action,

  [string]$Root = (Get-Location).Path,

  [string]$StartedBy = "",

  [switch]$NoDocker,

  [switch]$NoDevHub
)

$ErrorActionPreference = "Stop"

function Get-UtcNowText {
  return [DateTime]::UtcNow.ToString("o")
}

function Resolve-MainComputerRoot([string]$Value) {
  return (Resolve-Path -LiteralPath $Value).Path
}

function Ensure-Directory([string]$Path) {
  New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

function Write-JsonFile([string]$Path, [object]$Payload) {
  $parent = Split-Path -Parent $Path
  if ($parent) {
    Ensure-Directory $parent
  }
  $Payload | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Read-JsonFile([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return $null
  }
  try {
    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
  } catch {
    Write-Warning "Could not read JSON file ${Path}: $($_.Exception.Message)"
    return $null
  }
}

function Join-CommandLine([string[]]$Arguments) {
  $quoted = foreach ($arg in $Arguments) {
    $text = [string]$arg
    if ($text -match '[\s"]') {
      '"' + ($text -replace '"', '\"') + '"'
    } else {
      $text
    }
  }
  return ($quoted -join " ")
}

function Get-EnvFirstValue([string[]]$Names, [string]$Default) {
  foreach ($name in $Names) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if (-not [string]::IsNullOrWhiteSpace($value)) {
      return $value
    }
  }
  return $Default
}

function Get-SafeDockerName([string]$Value, [string]$Fallback = "main-computer") {
  $candidate = ([regex]::Replace([string]$Value, "[^a-zA-Z0-9_.-]+", "-")).Trim("-_.").ToLowerInvariant()
  if ([string]::IsNullOrWhiteSpace($candidate)) {
    $candidate = $Fallback
  }
  if ($candidate.Length -gt 63) {
    $candidate = $candidate.Substring(0, 63).Trim("-_.")
  }
  if ([string]::IsNullOrWhiteSpace($candidate)) {
    return $Fallback
  }
  return $candidate
}

function Get-StartStopRuntime([string]$RootPath) {
  return Join-Path $RootPath "runtime\start_stop"
}

function Get-StartSessionPath([string]$RootPath) {
  return Join-Path (Get-StartStopRuntime $RootPath) "start-session.json"
}

function Get-DevHubPidPath([string]$RootPath) {
  return Join-Path $RootPath ".main_computer_dev_hub.pid"
}

function Get-LauncherConfigPath([string]$RootPath) {
  return Join-Path (Get-StartStopRuntime $RootPath) "main-computer-launcher.json"
}

function Get-ObjectPropertyValue([object]$Object, [string]$Name, [object]$Default = $null) {
  if ($null -eq $Object) {
    return $Default
  }

  if ($Object -is [System.Collections.IDictionary]) {
    if ($Object.Contains($Name)) {
      return $Object[$Name]
    }
    return $Default
  }

  $property = $Object.PSObject.Properties[$Name]
  if ($null -eq $property) {
    return $Default
  }
  return $property.Value
}

function ConvertTo-StringValue([object]$Value, [string]$Default = "") {
  if ($null -eq $Value) {
    return $Default
  }
  $text = [string]$Value
  if ([string]::IsNullOrWhiteSpace($text)) {
    return $Default
  }
  return $text
}

function Resolve-MainComputerPythonCommand([string]$RootPath, [string]$Candidate = "") {
  $text = ConvertTo-StringValue $Candidate ""
  $generic = [string]::IsNullOrWhiteSpace($text) -or @("python", "python.exe", "python3", "python3.exe", "py", "py.exe") -contains $text.Trim().ToLowerInvariant()

  if (-not $generic) {
    return $text
  }

  foreach ($candidatePath in @(
      (Join-Path $RootPath ".venv\Scripts\python.exe"),
      (Join-Path (Split-Path -Parent $RootPath) ".venv\Scripts\python.exe")
    )) {
    if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
      return (Resolve-Path -LiteralPath $candidatePath).Path
    }
  }

  try {
    $command = Get-Command $text -CommandType Application -ErrorAction Stop
    if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
      return $command.Source
    }
  } catch {}

  return $(if ([string]::IsNullOrWhiteSpace($text)) { "python" } else { $text })
}


function Get-ModeDefaultEnvironment([string]$RootPath, [string]$Mode) {
  $normalized = $Mode.Trim().ToLowerInvariant()
  if ([string]::IsNullOrWhiteSpace($normalized)) {
    $normalized = "unleashed"
  }

  switch ($normalized) {
    "safe" {
      $label = "Safe Mode"
      $guidance = "guided"
      $safe = "1"
      $port = "38865"
      $heartbeat = "38866"
      $mainLogPort = "38867"
    }
    "debug" {
      $label = "Debug"
      $guidance = "debug"
      $safe = "0"
      $port = "28865"
      $heartbeat = "28866"
      $mainLogPort = "28867"
    }
    default {
      $normalized = "unleashed"
      $label = "Unleashed Mode"
      $guidance = "developer"
      $safe = "0"
      $port = "8765"
      $heartbeat = "8766"
      $mainLogPort = "8767"
    }
  }

  return [ordered]@{
    MAIN_COMPUTER_INSTALL_ROOT = $RootPath
    MAIN_COMPUTER_WORKSPACE = $RootPath
    MAIN_COMPUTER_INSTALL_MODE = $normalized
    MAIN_COMPUTER_MODE_LABEL = $label
    MAIN_COMPUTER_GUIDANCE_LEVEL = $guidance
    MAIN_COMPUTER_SAFE_MODE = $safe
    MAIN_COMPUTER_CONTROL_PORT = $port
    MAIN_COMPUTER_HEARTBEAT_PORT = $heartbeat
    MAIN_COMPUTER_MAIN_LOG_HOST = "127.0.0.1"
    MAIN_COMPUTER_MAIN_LOG_PORT = $mainLogPort
    MAIN_COMPUTER_MAIN_LOG_URL = "http://127.0.0.1:$mainLogPort"
    MAIN_COMPUTER_PATH_MODE = "local"
    MAIN_COMPUTER_HOST_OS = "windows"
    MAIN_COMPUTER_GITEA_SCOPE = "shared-machine"
    MAIN_COMPUTER_GITEA_ROOT_URL = "http://127.0.0.1:3000/"
    MAIN_COMPUTER_GITEA_HTTP_PORT = "3000"
    MAIN_COMPUTER_GITEA_COMPOSE_PROJECT = "main-computer-gitea"
    MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT = "main-computer-applications"
    MAIN_COMPUTER_ONLYOFFICE_ENABLED = "1"
    MAIN_COMPUTER_ONLYOFFICE_MODE = "docker"
    MAIN_COMPUTER_ONLYOFFICE_PORT = "18085"
    MAIN_COMPUTER_ONLYOFFICE_PROJECT = "main-computer-onlyoffice"
    MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME = "main-computer-onlyoffice-documentserver"
    MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL = "http://127.0.0.1:18085"
    MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL = "http://127.0.0.1:18085"
    MAIN_COMPUTER_ONLYOFFICE_BROWSER_PUBLIC_URL = "http://127.0.0.1:18085"
    MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL = "http://host.docker.internal:$port"
    MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED = "false"
    MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET = ""
    MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS = "true"
    MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS = "true"
    MAIN_COMPUTER_DEV_COMPOSE_PROJECT = "main-computer-unleashed"
    MAIN_COMPUTER_EXECUTOR_COMPOSE_PROJECT = "main-computer-unleashed"
    MAIN_COMPUTER_DOCKER_VIEWPORT_PORT = "18765"
    MAIN_COMPUTER_HUB_PORT = "8871"
    MAIN_COMPUTER_HUB_WORKER_PORT = "8771"
    MAIN_COMPUTER_HUB_URL = "http://127.0.0.1:8871"
    MAIN_COMPUTER_HUB_NETWORK = "dev"
    MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK = "1"
    OLLAMA_BASE_URL = "http://127.0.0.1:11434"
    MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL = "http://127.0.0.1:18545"
    MAIN_COMPUTER_ENERGY_CHAIN_ID = "42424242"
  }
}

function Merge-Environment([System.Collections.Specialized.OrderedDictionary]$Base, [object]$Overlay) {
  if ($null -eq $Overlay) {
    return $Base
  }

  if ($Overlay -is [System.Collections.IDictionary]) {
    foreach ($key in $Overlay.Keys) {
      $name = [string]$key
      $value = ConvertTo-StringValue $Overlay[$key] ""
      if (-not [string]::IsNullOrWhiteSpace($name) -and -not [string]::IsNullOrWhiteSpace($value)) {
        $Base[$name] = $value
      }
    }
    return $Base
  }

  foreach ($property in $Overlay.PSObject.Properties) {
    $name = [string]$property.Name
    $value = ConvertTo-StringValue $property.Value ""
    if (-not [string]::IsNullOrWhiteSpace($name) -and -not [string]::IsNullOrWhiteSpace($value)) {
      $Base[$name] = $value
    }
  }
  return $Base
}

function Read-MainComputerRuntimeEnvFile([string]$Path) {
  $values = [ordered]@{}
  if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return $values
  }

  $lineNumber = 0
  foreach ($line in Get-Content -LiteralPath $Path) {
    $lineNumber += 1
    $trimmed = ([string]$line).Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
      continue
    }
    if ($trimmed.StartsWith("export ")) {
      $trimmed = $trimmed.Substring(7).Trim()
    }
    $equals = $trimmed.IndexOf("=")
    if ($equals -le 0) {
      throw "Invalid Hub runtime env line ${Path}:${lineNumber}; expected KEY=VALUE."
    }
    $name = $trimmed.Substring(0, $equals).Trim()
    if ($name -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
      throw "Invalid Hub runtime env key ${name} at ${Path}:${lineNumber}."
    }
    $value = $trimmed.Substring($equals + 1).Trim()
    if ($value.Length -ge 2) {
      $first = $value.Substring(0, 1)
      $last = $value.Substring($value.Length - 1, 1)
      if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
        $value = $value.Substring(1, $value.Length - 2)
      }
    }
    $values[$name] = $value
  }
  return $values
}

function Ensure-MainComputerRuntimeEnvFile([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) {
    return [ordered]@{ path = $Path; created = $false; exists = $false; skipped = $true }
  }
  if (Test-Path -LiteralPath $Path -PathType Leaf) {
    return [ordered]@{ path = $Path; created = $false; exists = $true; skipped = $false }
  }

  $parent = Split-Path -Parent $Path
  if (-not [string]::IsNullOrWhiteSpace($parent)) {
    Ensure-Directory $parent
  }

  $defaultText = @(
    "# Main Computer dev Hub runtime overrides.",
    "# Add KEY=VALUE lines here to override the generated dev Hub environment.",
    "# This file is intentionally host-local and may be empty.",
    ""
  ) -join [Environment]::NewLine

  Set-Content -LiteralPath $Path -Value $defaultText -Encoding UTF8
  return [ordered]@{ path = $Path; created = $true; exists = $true; skipped = $false }
}

function Merge-MainComputerRuntimeEnvFile([object]$LaunchContext, [string]$Path) {
  $environment = Get-ObjectPropertyValue $LaunchContext "environment" $null
  if ($null -eq $environment) {
    return [ordered]@{ path = $Path; loaded = $false; count = 0 }
  }

  $values = Read-MainComputerRuntimeEnvFile $Path
  if ($values.Count -gt 0) {
    Merge-Environment $environment $values | Out-Null
  }
  return [ordered]@{
    path = $Path
    loaded = ($values.Count -gt 0)
    count = $values.Count
  }
}


function New-LaunchContextFromManifest([string]$RootPath, [object]$Manifest, [string]$ManifestPath) {
  $mode = ConvertTo-StringValue (Get-ObjectPropertyValue $Manifest "mode" "unleashed") "unleashed"
  $environment = Get-ModeDefaultEnvironment $RootPath $mode

  $profile = $null
  $modes = Get-ObjectPropertyValue $Manifest "modes" $null
  if ($null -ne $modes) {
    $modeProperty = $modes.PSObject.Properties[$mode]
    if ($null -ne $modeProperty) {
      $profile = $modeProperty.Value
    }
  }

  if ($null -ne $profile) {
    $environment["MAIN_COMPUTER_INSTALL_MODE"] = ConvertTo-StringValue (Get-ObjectPropertyValue $profile "key" $mode) $mode
    $environment["MAIN_COMPUTER_MODE_LABEL"] = ConvertTo-StringValue (Get-ObjectPropertyValue $profile "label" $environment["MAIN_COMPUTER_MODE_LABEL"]) $environment["MAIN_COMPUTER_MODE_LABEL"]
    $environment["MAIN_COMPUTER_GUIDANCE_LEVEL"] = ConvertTo-StringValue (Get-ObjectPropertyValue $profile "guidance_level" $environment["MAIN_COMPUTER_GUIDANCE_LEVEL"]) $environment["MAIN_COMPUTER_GUIDANCE_LEVEL"]
    $environment["MAIN_COMPUTER_SAFE_MODE"] = $(if ($mode -eq "safe") { "1" } else { "0" })
    $environment["MAIN_COMPUTER_CONTROL_PORT"] = ConvertTo-StringValue (Get-ObjectPropertyValue $profile "port" $environment["MAIN_COMPUTER_CONTROL_PORT"]) $environment["MAIN_COMPUTER_CONTROL_PORT"]
    $environment["MAIN_COMPUTER_HEARTBEAT_PORT"] = ConvertTo-StringValue (Get-ObjectPropertyValue $profile "heartbeat_port" $environment["MAIN_COMPUTER_HEARTBEAT_PORT"]) $environment["MAIN_COMPUTER_HEARTBEAT_PORT"]
    $environment["MAIN_COMPUTER_STATE_ROOT"] = ConvertTo-StringValue (Get-ObjectPropertyValue $profile "state_root" "") ""
    $environment["MAIN_COMPUTER_CONTROL_ROOT"] = ConvertTo-StringValue (Get-ObjectPropertyValue $profile "control_root" "") ""
    $environment["MAIN_COMPUTER_EXECUTOR_ENABLED"] = "1"
    $environment["MAIN_COMPUTER_EXECUTOR_BACKEND"] = "wsl"
    $environment["MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION"] = ConvertTo-StringValue (Get-ObjectPropertyValue $profile "distribution" "") ""
    $environment["MAIN_COMPUTER_EXECUTOR_ROOT"] = ConvertTo-StringValue (Get-ObjectPropertyValue $profile "executor_root" "") ""
  }

  $workspace = ConvertTo-StringValue (Get-ObjectPropertyValue $Manifest "workspace" $RootPath) $RootPath
  $environment["MAIN_COMPUTER_WORKSPACE"] = $workspace

  return [pscustomobject]@{
    tree_kind = "installed"
    mode = $mode
    mode_label = ConvertTo-StringValue (Get-ObjectPropertyValue $Manifest "mode_label" $environment["MAIN_COMPUTER_MODE_LABEL"]) $environment["MAIN_COMPUTER_MODE_LABEL"]
    python = Resolve-MainComputerPythonCommand $RootPath (ConvertTo-StringValue (Get-ObjectPropertyValue $Manifest "venv_python" "python") "python")
    environment = $environment
    context_file = $ManifestPath
  }
}

function Resolve-MainComputerLaunchContext([string]$RootPath) {
  $launcherPath = Get-LauncherConfigPath $RootPath
  if (Test-Path -LiteralPath $launcherPath -PathType Leaf) {
    $launcherConfig = Read-JsonFile $launcherPath
    if ($null -ne $launcherConfig) {
      $mode = ConvertTo-StringValue (Get-ObjectPropertyValue $launcherConfig "mode" "unleashed") "unleashed"
      $environment = Get-ModeDefaultEnvironment $RootPath $mode
      Merge-Environment $environment (Get-ObjectPropertyValue $launcherConfig "environment" $null) | Out-Null
      $pythonPath = Resolve-MainComputerPythonCommand $RootPath (ConvertTo-StringValue (Get-ObjectPropertyValue $launcherConfig "python" "python") "python")
      $environment["MAIN_COMPUTER_PYTHON_COMMAND"] = $pythonPath
      return [pscustomobject]@{
        tree_kind = ConvertTo-StringValue (Get-ObjectPropertyValue $launcherConfig "tree_kind" "installed") "installed"
        mode = $mode
        mode_label = ConvertTo-StringValue (Get-ObjectPropertyValue $launcherConfig "mode_label" $environment["MAIN_COMPUTER_MODE_LABEL"]) $environment["MAIN_COMPUTER_MODE_LABEL"]
        python = $pythonPath
        environment = $environment
        context_file = $launcherPath
      }
    }
  }

  foreach ($manifestPath in @(
      (Join-Path $RootPath "runtime\main-computer-install.json"),
      (Join-Path $RootPath "main-computer-install.json")
    )) {
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
      $manifest = Read-JsonFile $manifestPath
      if ($null -ne $manifest) {
        return New-LaunchContextFromManifest $RootPath $manifest $manifestPath
      }
    }
  }

  $defaultPython = Resolve-MainComputerPythonCommand $RootPath "python"
  $defaultEnvironment = Get-ModeDefaultEnvironment $RootPath "unleashed"
  $defaultEnvironment["MAIN_COMPUTER_PYTHON_COMMAND"] = $defaultPython
  return [pscustomobject]@{
    tree_kind = "source"
    mode = "unleashed"
    mode_label = "Unleashed Mode"
    python = $defaultPython
    environment = $defaultEnvironment
    context_file = $null
  }
}

function Set-MainComputerLaunchEnvironment([object]$LaunchContext) {
  $environment = Get-ObjectPropertyValue $LaunchContext "environment" $null
  if ($null -eq $environment) {
    return
  }

  $treeKind = ConvertTo-StringValue (Get-ObjectPropertyValue $LaunchContext "tree_kind" "source") "source"
  if ($treeKind -eq "installed") {
    foreach ($name in @("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHON_BASIC_REPL", "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT")) {
      [Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
  }

  # The applications/Coolify core stack is owned by the applications service.
  # Legacy standalone/publish variables must not leak from the user's shell into
  # start_v2 or the supervisor process, otherwise Docker Compose sees multiple
  # project names for the same services.
  foreach ($name in @("MAIN_COMPUTER_COOLIFY_PROJECT", "COOLIFY_COMPOSE_PROJECT", "COMPOSE_PROJECT_NAME")) {
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
  }

  if ($environment -is [System.Collections.IDictionary]) {
    foreach ($key in $environment.Keys) {
      [Environment]::SetEnvironmentVariable([string]$key, [string]$environment[$key], "Process")
    }
  } else {
    foreach ($property in $environment.PSObject.Properties) {
      [Environment]::SetEnvironmentVariable([string]$property.Name, [string]$property.Value, "Process")
    }
  }

  $pythonCommand = ConvertTo-StringValue (Get-ObjectPropertyValue $LaunchContext "python" "") ""
  if (-not [string]::IsNullOrWhiteSpace($pythonCommand)) {
    [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_PYTHON_COMMAND", $pythonCommand, "Process")
  }
}



function Get-LaunchEnvironmentValue([object]$LaunchContext, [string]$Name, [string]$Default = "") {
  $environment = Get-ObjectPropertyValue $LaunchContext "environment" $null
  if ($null -eq $environment) {
    return $Default
  }

  if ($environment -is [System.Collections.IDictionary]) {
    if ($environment.Contains($Name)) {
      return ConvertTo-StringValue $environment[$Name] $Default
    }
    return $Default
  }

  $property = $environment.PSObject.Properties[$Name]
  if ($null -ne $property) {
    return ConvertTo-StringValue $property.Value $Default
  }

  return $Default
}

function Get-MainComputerCallerContainerRuntimeOverride {
  $value = [Environment]::GetEnvironmentVariable("MAIN_COMPUTER_CONTAINER_RUNTIME")
  if ([string]::IsNullOrWhiteSpace($value)) {
    return ""
  }

  $normalized = $value.Trim().ToLowerInvariant()
  if ($normalized -in @("podman", "podman-desktop")) {
    return "podman"
  }
  if ($normalized -in @("docker", "docker-desktop")) {
    return "docker"
  }

  return ""
}

function Set-MainComputerLaunchEnvironmentValue([object]$LaunchContext, [string]$Name, [string]$Value) {
  $environment = Get-ObjectPropertyValue $LaunchContext "environment" $null
  if ($null -eq $environment -or [string]::IsNullOrWhiteSpace($Name)) {
    return
  }

  if ($environment -is [System.Collections.IDictionary]) {
    $environment[$Name] = $Value
    return
  }

  $property = $environment.PSObject.Properties[$Name]
  if ($null -ne $property) {
    $property.Value = $Value
    return
  }

  Add-Member -InputObject $environment -NotePropertyName $Name -NotePropertyValue $Value -Force
}

function Restore-MainComputerCallerContainerRuntimeOverride([object]$LaunchContext, [string]$RuntimeOverride) {
  if ([string]::IsNullOrWhiteSpace($RuntimeOverride)) {
    return
  }

  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_CONTAINER_RUNTIME", $RuntimeOverride, "Process")
  Set-MainComputerLaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_CONTAINER_RUNTIME" $RuntimeOverride
  Write-Host ("Caller container runtime override preserved: MAIN_COMPUTER_CONTAINER_RUNTIME={0}" -f $RuntimeOverride)
}


function Get-ControlRoot([string]$RootPath, [object]$LaunchContext) {
  $controlRoot = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_CONTROL_ROOT" ""
  if ([string]::IsNullOrWhiteSpace($controlRoot)) {
    return $RootPath
  }
  return $controlRoot
}

function Test-MainComputerTcpPortOpen([int]$Port) {
  try {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
      $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
      if (-not $async.AsyncWaitHandle.WaitOne(500, $false)) {
        return $false
      }
      $client.EndConnect($async)
      return $true
    } finally {
      $client.Close()
    }
  } catch {
    return $false
  }
}


$script:MainComputerContainerRuntimeCache = @{}

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

function Get-MainComputerContainerRuntime([string]$RootPath, [string]$PythonCommand) {
  $cacheKey = @(
    $RootPath,
    $PythonCommand,
    [string]$env:MAIN_COMPUTER_CONTAINER_RUNTIME,
    [string]$env:MAIN_COMPUTER_CONTAINER_COMMAND,
    [string]$env:MAIN_COMPUTER_CONTAINER_COMPOSE_COMMAND,
    [string]$env:MAIN_COMPUTER_DOCKER_COMMAND,
    [string]$env:MAIN_COMPUTER_DOCKER,
    [string]$env:MAIN_COMPUTER_DOCKER_COMPOSE,
    [string]$env:MAIN_COMPUTER_DOCKER_COMPOSE_COMMAND
  ) -join "`n"

  if ($script:MainComputerContainerRuntimeCache.ContainsKey($cacheKey)) {
    return $script:MainComputerContainerRuntimeCache[$cacheKey]
  }

  if ([string]::IsNullOrWhiteSpace($PythonCommand)) {
    throw "Cannot resolve the Main Computer container runtime because the Python command is empty."
  }

  $arguments = @(
    "-m",
    "main_computer.container_runtime",
    "--check",
    "--cwd",
    $RootPath,
    "--json"
  )

  $output = @(& $PythonCommand @arguments 2>&1)
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
    $runtime = $json | ConvertFrom-Json
  } catch {
    throw "Main Computer container runtime JSON could not be parsed: $json"
  }

  $script:MainComputerContainerRuntimeCache[$cacheKey] = $runtime
  return $runtime
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

function Get-MainComputerRequestedContainerRuntime([object]$LaunchContext) {
  $requested = (Get-EnvFirstValue @("MAIN_COMPUTER_CONTAINER_RUNTIME") (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_CONTAINER_RUNTIME" "")).Trim().ToLowerInvariant()
  if ($requested -in @("podman", "podman-desktop")) {
    return "podman"
  }
  if ($requested -in @("docker", "docker-desktop")) {
    return "docker"
  }
  return ""
}

function Resolve-MainComputerPodmanCommand {
  $candidates = @()
  if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Podman\podman.exe")
  }
  if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
    $candidates += (Join-Path $env:ProgramFiles "RedHat\Podman\podman.exe")
    $candidates += (Join-Path $env:ProgramFiles "Podman\podman.exe")
  }
  $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
  if (-not [string]::IsNullOrWhiteSpace($programFilesX86)) {
    $candidates += (Join-Path $programFilesX86 "RedHat\Podman\podman.exe")
    $candidates += (Join-Path $programFilesX86 "Podman\podman.exe")
  }

  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      return $candidate
    }
  }

  $command = Get-Command "podman" -ErrorAction SilentlyContinue
  if ($command) {
    return $command.Source
  }

  return ""
}

function Invoke-MainComputerPodmanTool {
  param(
    [Parameter(Mandatory = $true)][string]$PodmanCommand,
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [int]$TimeoutSeconds = 90
  )

  $psi = [System.Diagnostics.ProcessStartInfo]::new()
  $psi.FileName = $PodmanCommand
  $psi.Arguments = Join-CommandLine $Arguments
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true

  $workdir = $env:USERPROFILE
  if ([string]::IsNullOrWhiteSpace($workdir) -or -not (Test-Path -LiteralPath $workdir -PathType Container)) {
    $workdir = [System.IO.Path]::GetTempPath()
  }
  $psi.WorkingDirectory = $workdir

  $process = [System.Diagnostics.Process]::new()
  $process.StartInfo = $psi
  try {
    [void]$process.Start()
  } catch {
    return [ordered]@{
      ok = $false
      exit_code = $null
      stdout = ""
      stderr = $_.Exception.Message
      command = @($PodmanCommand) + $Arguments
    }
  }

  $stdoutTask = $process.StandardOutput.ReadToEndAsync()
  $stderrTask = $process.StandardError.ReadToEndAsync()

  if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
    try { $process.Kill() } catch {}
    try { $process.WaitForExit() } catch {}
    return [ordered]@{
      ok = $false
      exit_code = 124
      stdout = $stdoutTask.Result
      stderr = "Timed out after $TimeoutSeconds seconds."
      command = @($PodmanCommand) + $Arguments
    }
  }

  return [ordered]@{
    ok = ($process.ExitCode -eq 0)
    exit_code = $process.ExitCode
    stdout = $stdoutTask.Result
    stderr = $stderrTask.Result
    command = @($PodmanCommand) + $Arguments
  }
}

function Get-MainComputerPodmanMachineNames([string]$PodmanCommand) {
  $result = Invoke-MainComputerPodmanTool -PodmanCommand $PodmanCommand -Arguments @("machine", "list", "--format", "{{.Name}}") -TimeoutSeconds 30
  if (-not $result.ok) {
    return @()
  }

  return @(
    $result.stdout -split "(`r`n|`n|`r)" |
      ForEach-Object { ([string]$_).Trim() } |
      Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
  )
}

function Get-MainComputerPodmanFailureText([object]$Result) {
  if ($null -eq $Result) {
    return ""
  }

  $parts = @()
  $exitCode = Get-ObjectPropertyValue $Result "exit_code" $null
  if ($null -ne $exitCode) {
    $parts += "exit code $exitCode"
  }
  $stderr = ConvertTo-StringValue (Get-ObjectPropertyValue $Result "stderr" "") ""
  $stdout = ConvertTo-StringValue (Get-ObjectPropertyValue $Result "stdout" "") ""
  if (-not [string]::IsNullOrWhiteSpace($stderr)) {
    $parts += ($stderr.Trim())
  }
  if (-not [string]::IsNullOrWhiteSpace($stdout)) {
    $parts += ($stdout.Trim())
  }
  return ($parts -join "; ")
}

function Ensure-MainComputerPodmanMachineStarted([object]$LaunchContext) {
  $requested = Get-MainComputerRequestedContainerRuntime $LaunchContext
  if ($requested -ne "podman") {
    return
  }

  $podman = Resolve-MainComputerPodmanCommand
  if ([string]::IsNullOrWhiteSpace($podman)) {
    throw "Podman was selected for Main Computer startup, but podman.exe was not found."
  }

  Write-Host "Podman runtime selected; ensuring the Podman machine is running."

  $probe = Invoke-MainComputerPodmanTool -PodmanCommand $podman -Arguments @("ps") -TimeoutSeconds 30
  if ($probe.ok) {
    Write-Host "Podman runtime is reachable."
    return
  }

  Write-Host "Podman is installed but not reachable; attempting 'podman machine start'."
  $start = Invoke-MainComputerPodmanTool -PodmanCommand $podman -Arguments @("machine", "start") -TimeoutSeconds 180
  $probe = Invoke-MainComputerPodmanTool -PodmanCommand $podman -Arguments @("ps") -TimeoutSeconds 30
  if ($probe.ok) {
    Write-Host "Podman machine started and runtime is reachable."
    return
  }

  $machineNames = @(Get-MainComputerPodmanMachineNames $podman)
  if ($machineNames.Count -eq 0) {
    Write-Host "No Podman machine was found; attempting 'podman machine init' then 'podman machine start'."
    $init = Invoke-MainComputerPodmanTool -PodmanCommand $podman -Arguments @("machine", "init") -TimeoutSeconds 180
    if (-not $init.ok) {
      throw ("Podman was selected, but 'podman machine init' failed: {0}" -f (Get-MainComputerPodmanFailureText $init))
    }

    $start = Invoke-MainComputerPodmanTool -PodmanCommand $podman -Arguments @("machine", "start") -TimeoutSeconds 180
    $probe = Invoke-MainComputerPodmanTool -PodmanCommand $podman -Arguments @("ps") -TimeoutSeconds 30
    if ($probe.ok) {
      Write-Host "Podman machine initialized, started, and runtime is reachable."
      return
    }
  }

  $detail = Get-MainComputerPodmanFailureText $probe
  $startDetail = Get-MainComputerPodmanFailureText $start
  if (-not [string]::IsNullOrWhiteSpace($startDetail)) {
    $detail = "$detail; start detail: $startDetail"
  }
  throw ("Podman was selected, but the Podman runtime is still not reachable after attempting to start its machine: {0}" -f $detail)
}

function Assert-MainComputerExplicitContainerRuntimeAvailable([string]$RootPath, [object]$LaunchContext, [string]$PythonCommand) {
  $requested = (Get-EnvFirstValue @("MAIN_COMPUTER_CONTAINER_RUNTIME") (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_CONTAINER_RUNTIME" "")).Trim().ToLowerInvariant()
  if ([string]::IsNullOrWhiteSpace($requested) -or @("auto", "default", "detect", "container", "containers") -contains $requested) {
    return
  }

  if ($requested -notin @("docker", "docker-desktop", "podman", "podman-desktop")) {
    return
  }

  if ((Get-MainComputerRequestedContainerRuntime $LaunchContext) -eq "podman") {
    Ensure-MainComputerPodmanMachineStarted $LaunchContext
  }

  $runtime = Get-MainComputerContainerRuntime $RootPath $PythonCommand
  Write-Host ("Container runtime requested: {0}; direct={1}; compose={2}" -f [string]$runtime.runtime, (Get-MainComputerCommandDisplay $runtime.container_command), (Get-MainComputerCommandDisplay $runtime.compose_command))
}


function Get-MainComputerGiteaPort([object]$LaunchContext) {
  $rootUrl = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_GITEA_ROOT_URL" "http://127.0.0.1:3000/"
  try {
    $uri = [uri]$rootUrl
    if ($uri.Port -gt 0) {
      return [int]$uri.Port
    }
  } catch {}

  $portText = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_GITEA_HTTP_PORT" "3000"
  try {
    return [int]$portText
  } catch {
    return 3000
  }
}

function Start-MainComputerGiteaIfMissing([string]$RootPath, [object]$LaunchContext, [string]$PythonCommand) {
  $giteaPort = Get-MainComputerGiteaPort $LaunchContext
  $projectName = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_GITEA_COMPOSE_PROJECT" "main-computer-gitea"
  $composePath = Join-Path $RootPath "docker-compose.gitea.yml"

  if (Test-MainComputerTcpPortOpen -Port $giteaPort) {
    Write-Host ("Shared Gitea already present on port {0}; installer/start path will not recreate it." -f $giteaPort)
    return [ordered]@{
      ok = $true
      state = "already-present"
      installed = $false
      compose_project = $projectName
      compose_file = $composePath
      port = $giteaPort
    }
  }

  if ($NoDocker) {
    return [ordered]@{
      ok = $false
      state = "docker-disabled"
      installed = $false
      compose_project = $projectName
      compose_file = $composePath
      port = $giteaPort
      message = "Docker startup was disabled; shared Gitea cannot be prepared."
    }
  }

  if (-not (Test-Path -LiteralPath $composePath -PathType Leaf)) {
    return [ordered]@{
      ok = $false
      state = "missing-compose"
      installed = $false
      compose_project = $projectName
      compose_file = $composePath
      port = $giteaPort
      message = "docker-compose.gitea.yml is missing."
    }
  }

  try {
    $containerRuntime = Get-MainComputerContainerRuntime $RootPath $PythonCommand
  } catch {
    return [ordered]@{
      ok = $false
      state = "missing-container-runtime"
      installed = $false
      compose_project = $projectName
      compose_file = $composePath
      port = $giteaPort
      message = $_.Exception.Message
    }
  }

  $composeCommand = ConvertTo-MainComputerStringArray $containerRuntime.compose_command
  $containerIds = @(Invoke-MainComputerRuntimeCommand -Command $composeCommand -Arguments @("--project-name", $projectName, "-f", $composePath, "ps", "-a", "-q", "gitea") -CaptureOutput -SuppressErrors)
  $containerExists = ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace(($containerIds -join "").Trim()))

  if ($containerExists) {
    Write-Host ("Shared Gitea container already exists but is not reachable on port {0}; starting existing container without reinstalling." -f $giteaPort)
    $arguments = @("--project-name", $projectName, "-f", $composePath, "start", "gitea")
    $stateOnSuccess = "started-existing"
    $stateOnFailure = "start-existing-failed"
    $installed = $false
  } else {
    Write-Host ("Shared Gitea not found on this machine; installing machine-wide Gitea with docker-compose.gitea.yml.")
    $arguments = @("--project-name", $projectName, "-f", $composePath, "up", "-d", "gitea")
    $stateOnSuccess = "installed-missing"
    $stateOnFailure = "install-missing-failed"
    $installed = $true
  }

  Invoke-MainComputerRuntimeCommand -Command $composeCommand -Arguments $arguments
  $exitCode = $LASTEXITCODE
  return [ordered]@{
    ok = ($exitCode -eq 0)
    state = $(if ($exitCode -eq 0) { $stateOnSuccess } else { $stateOnFailure })
    installed = ($installed -and $exitCode -eq 0)
    container_exists = $containerExists
    compose_project = $projectName
    compose_file = $composePath
    port = $giteaPort
    command = @($composeCommand) + $arguments
    exit_code = $exitCode
  }
}

function Write-MainComputerGiteaWarning([object]$GiteaStart) {
  $state = ConvertTo-StringValue (Get-ObjectPropertyValue $GiteaStart "state" "unknown") "unknown"
  Write-Warning ("Shared Gitea preparation failed ({0}). Continuing Main Computer startup; Local Gitea publishing may be unavailable." -f $state)
  $message = ConvertTo-StringValue (Get-ObjectPropertyValue $GiteaStart "message" "") ""
  if (-not [string]::IsNullOrWhiteSpace($message)) {
    Write-Warning ("Shared Gitea error: {0}" -f $message)
  }
  $exitCode = Get-ObjectPropertyValue $GiteaStart "exit_code" $null
  if ($null -ne $exitCode) {
    Write-Warning ("Shared Gitea command exit code: {0}" -f $exitCode)
  }
}


function Invoke-MainComputerPythonTool {
  param(
    [Parameter(Mandatory = $true)][string]$PythonCommand,
    [Parameter(Mandatory = $true)][string]$RootPath,
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [int]$TimeoutSeconds = 240
  )

  $psi = [System.Diagnostics.ProcessStartInfo]::new()
  $psi.FileName = $PythonCommand
  $psi.Arguments = Join-CommandLine $Arguments
  $psi.WorkingDirectory = $RootPath
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true

  $process = [System.Diagnostics.Process]::new()
  $process.StartInfo = $psi
  [void]$process.Start()

  $stdoutTask = $process.StandardOutput.ReadToEndAsync()
  $stderrTask = $process.StandardError.ReadToEndAsync()

  if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
    try {
      $process.Kill()
    } catch {}
    try {
      $process.WaitForExit()
    } catch {}
    return [ordered]@{
      ok = $false
      state = "timeout"
      command = @($PythonCommand) + $Arguments
      exit_code = 124
      stdout = $stdoutTask.Result
      stderr = $stderrTask.Result
    }
  }

  return [ordered]@{
    ok = ($process.ExitCode -eq 0)
    state = $(if ($process.ExitCode -eq 0) { "completed" } else { "failed" })
    command = @($PythonCommand) + $Arguments
    exit_code = $process.ExitCode
    stdout = $stdoutTask.Result
    stderr = $stderrTask.Result
  }
}

function Write-MainComputerLocalPlatformWarning([object]$LocalPlatformStart) {
  $state = ConvertTo-StringValue (Get-ObjectPropertyValue $LocalPlatformStart "state" "unknown") "unknown"
  Write-Warning ("Local platform startup failed ({0}). Continuing Main Computer startup; local hub/blog website containers may be unavailable." -f $state)

  $site = ConvertTo-StringValue (Get-ObjectPropertyValue $LocalPlatformStart "site" "") ""
  $lane = ConvertTo-StringValue (Get-ObjectPropertyValue $LocalPlatformStart "lane" "") ""
  if (-not [string]::IsNullOrWhiteSpace($site)) {
    if (-not [string]::IsNullOrWhiteSpace($lane)) {
      Write-Warning ("Local platform target: {0} lane {1}" -f $site, $lane)
    } else {
      Write-Warning ("Local platform target: {0}" -f $site)
    }
  }

  $message = ConvertTo-StringValue (Get-ObjectPropertyValue $LocalPlatformStart "message" "") ""
  if (-not [string]::IsNullOrWhiteSpace($message)) {
    Write-Warning ("Local platform error: {0}" -f $message)
  }

  $results = @(Get-ObjectPropertyValue $LocalPlatformStart "results" @())
  if ($results.Count -gt 0) {
    $lastResult = $results[$results.Count - 1]
    $exitCode = Get-ObjectPropertyValue $lastResult "exit_code" $null
    if ($null -ne $exitCode) {
      Write-Warning ("Last local platform command exit code: {0}" -f $exitCode)
    }

    $detail = ConvertTo-StringValue (Get-ObjectPropertyValue $lastResult "stderr" "") ""
    if ([string]::IsNullOrWhiteSpace($detail)) {
      $detail = ConvertTo-StringValue (Get-ObjectPropertyValue $lastResult "stdout" "") ""
    }
    if (-not [string]::IsNullOrWhiteSpace($detail)) {
      $line = @($detail -split "(`r`n|`n|`r)" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1)
      if ($line.Count -gt 0) {
        Write-Warning ("Last local platform output: {0}" -f [string]$line[0])
      }
    }
  }
}

function Write-MainComputerDevHubWarning([object]$DevHubStart) {
  $state = ConvertTo-StringValue (Get-ObjectPropertyValue $DevHubStart "state" "unknown") "unknown"
  Write-Warning ("Dev Hub startup failed ({0}). Continuing Main Computer startup; Hub-dependent features may be unavailable." -f $state)

  $message = ConvertTo-StringValue (Get-ObjectPropertyValue $DevHubStart "message" "") ""
  if (-not [string]::IsNullOrWhiteSpace($message)) {
    Write-Warning ("Dev Hub error: {0}" -f $message)
  }

  $prerequisites = Get-ObjectPropertyValue $DevHubStart "prerequisites" $null
  if ($null -ne $prerequisites) {
    $prerequisiteState = ConvertTo-StringValue (Get-ObjectPropertyValue $prerequisites "state" "") ""
    if (-not [string]::IsNullOrWhiteSpace($prerequisiteState)) {
      Write-Warning ("Dev Hub prerequisite state: {0}" -f $prerequisiteState)
    }
  }
}

function Start-MainComputerLocalPlatform([string]$RootPath, [string]$PythonCommand) {
  if ($env:MAIN_COMPUTER_LOCAL_SERVER_ENABLED -ne "1") {
    return [ordered]@{
      ok = $true
      state = "disabled"
      compose_project = $env:MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT
      compose_file = $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH
    }
  }

  $tool = Join-Path $RootPath "tools\local-platform\website-docker.py"
  if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    return [ordered]@{
      ok = $false
      state = "missing-tool"
      tool = $tool
      compose_project = $env:MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT
      compose_file = $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH
    }
  }

  $results = @()

  foreach ($site in @("hub-site", "blog-site")) {
    $result = Invoke-MainComputerPythonTool `
      -PythonCommand $PythonCommand `
      -RootPath $RootPath `
      -Arguments @($tool, "install", $site, "--repo-root", $RootPath) `
      -TimeoutSeconds 120
    $results += $result
    if (-not $result.ok) {
      return [ordered]@{
        ok = $false
        state = "install-failed"
        site = $site
        compose_project = $env:MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT
        compose_file = $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH
        results = $results
      }
    }
  }

  foreach ($target in @(
    @("hub-site", "local"),
    @("blog-site", "local"),
    @("hub-site", "dev"),
    @("blog-site", "dev")
  )) {
    $result = Invoke-MainComputerPythonTool `
      -PythonCommand $PythonCommand `
      -RootPath $RootPath `
      -Arguments @($tool, "publish", $target[0], "--lane", $target[1], "--repo-root", $RootPath, "--timeout", "180", "--no-verify") `
      -TimeoutSeconds 240
    $results += $result
    if (-not $result.ok) {
      return [ordered]@{
        ok = $false
        state = "publish-failed"
        site = $target[0]
        lane = $target[1]
        compose_project = $env:MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT
        compose_file = $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH
        results = $results
      }
    }
  }

  return [ordered]@{
    ok = $true
    state = "ready"
    compose_project = $env:MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT
    compose_file = $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH
    results = $results
  }
}

function ConvertTo-MainComputerChainIdHex([string]$ChainId) {
  $text = ConvertTo-StringValue $ChainId ""
  if ([string]::IsNullOrWhiteSpace($text)) {
    return ""
  }

  $trimmed = $text.Trim()
  if ($trimmed.StartsWith("0x", [System.StringComparison]::OrdinalIgnoreCase)) {
    return $trimmed.ToLowerInvariant()
  }

  try {
    return ("0x" + [Convert]::ToString([int64]$trimmed, 16)).ToLowerInvariant()
  } catch {
    return $trimmed.ToLowerInvariant()
  }
}

function Test-MainComputerDevChainRpc([string]$RpcUrl, [string]$ExpectedChainId) {
  $expectedHex = ConvertTo-MainComputerChainIdHex $ExpectedChainId
  if ([string]::IsNullOrWhiteSpace($RpcUrl)) {
    return [ordered]@{
      ok = $false
      state = "missing-rpc-url"
      rpc_url = $RpcUrl
      expected_chain_id = $ExpectedChainId
      expected_chain_id_hex = $expectedHex
    }
  }

  $body = @{
    jsonrpc = "2.0"
    id = 1
    method = "eth_chainId"
    params = @()
  } | ConvertTo-Json -Compress

  try {
    $response = Invoke-RestMethod `
      -Method Post `
      -Uri $RpcUrl `
      -ContentType "application/json" `
      -Body $body `
      -TimeoutSec 5
  } catch {
    return [ordered]@{
      ok = $false
      state = "unreachable"
      rpc_url = $RpcUrl
      expected_chain_id = $ExpectedChainId
      expected_chain_id_hex = $expectedHex
      message = $_.Exception.Message
    }
  }

  $actualHex = ConvertTo-StringValue (Get-ObjectPropertyValue $response "result" "") ""
  $actualHex = $actualHex.Trim().ToLowerInvariant()

  if ([string]::IsNullOrWhiteSpace($actualHex)) {
    return [ordered]@{
      ok = $false
      state = "missing-chain-id"
      rpc_url = $RpcUrl
      expected_chain_id = $ExpectedChainId
      expected_chain_id_hex = $expectedHex
      actual_chain_id_hex = $actualHex
    }
  }

  return [ordered]@{
    ok = ($actualHex -eq $expectedHex)
    state = $(if ($actualHex -eq $expectedHex) { "healthy" } else { "wrong-chain-id" })
    rpc_url = $RpcUrl
    expected_chain_id = $ExpectedChainId
    expected_chain_id_hex = $expectedHex
    actual_chain_id_hex = $actualHex
  }
}

function Wait-MainComputerDevChainRpc([string]$RpcUrl, [string]$ExpectedChainId, [int]$TimeoutSeconds = 120) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $last = $null

  do {
    $last = Test-MainComputerDevChainRpc $RpcUrl $ExpectedChainId
    if ($last.ok) {
      return $last
    }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)

  return [ordered]@{
    ok = $false
    state = "timeout"
    rpc_url = $RpcUrl
    expected_chain_id = $ExpectedChainId
    last_health_check = $last
  }
}

function Get-MainComputerDevHubStartTimeoutSeconds([object]$LaunchContext, [string]$HubKind) {
  $defaultTimeout = $(if ($HubKind -eq "exp-fdb") { "180" } else { "30" })
  $raw = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS" $defaultTimeout
  try {
    $value = [int]$raw
    if ($value -gt 0) {
      return $value
    }
  } catch {}
  return [int]$defaultTimeout
}

function Get-MainComputerFdbClusterEndpoint([string]$ClusterFile) {
  if ([string]::IsNullOrWhiteSpace($ClusterFile) -or -not (Test-Path -LiteralPath $ClusterFile -PathType Leaf)) {
    return [ordered]@{
      ok = $false
      state = "missing-cluster-file"
      cluster_file = $ClusterFile
    }
  }

  $text = ""
  try {
    $text = (Get-Content -LiteralPath $ClusterFile -Raw).Trim()
  } catch {
    return [ordered]@{
      ok = $false
      state = "read-cluster-file-failed"
      cluster_file = $ClusterFile
      message = $_.Exception.Message
    }
  }

  $match = [regex]::Match($text, "([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)@([^:,\s]+):([0-9]+)")
  if (-not $match.Success) {
    return [ordered]@{
      ok = $false
      state = "malformed-cluster-file"
      cluster_file = $ClusterFile
      content = $text
    }
  }

  return [ordered]@{
    ok = $true
    state = "parsed"
    cluster_file = $ClusterFile
    host = [string]$match.Groups[3].Value
    port = [int]$match.Groups[4].Value
  }
}

function Test-MainComputerFdbClusterReady([string]$ClusterFile) {
  $endpoint = Get-MainComputerFdbClusterEndpoint $ClusterFile
  if (-not $endpoint.ok) {
    return $endpoint
  }

  if (-not (Test-MainComputerTcpPortOpen -Port ([int]$endpoint.port))) {
    return [ordered]@{
      ok = $false
      state = "fdb-port-unreachable"
      cluster_file = $ClusterFile
      host = $endpoint.host
      port = $endpoint.port
      message = "FoundationDB coordinator port is not reachable yet; the resident executor service should start or reuse the default Docker/FDB container after Docker is ready."
    }
  }

  return [ordered]@{
    ok = $true
    state = "ready"
    cluster_file = $ClusterFile
    host = $endpoint.host
    port = $endpoint.port
  }
}

function Wait-MainComputerFdbClusterReady([string]$ClusterFile, [int]$TimeoutSeconds = 120) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $last = $null

  do {
    $last = Test-MainComputerFdbClusterReady $ClusterFile
    if ($last.ok) {
      return $last
    }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)

  return [ordered]@{
    ok = $false
    state = "timeout"
    cluster_file = $ClusterFile
    last_health_check = $last
    message = "FoundationDB did not become ready before timeout; the resident executor service should bootstrap the default Docker/FDB container after Docker is ready."
  }
}

function Test-MainComputerExpFdbHubPrerequisites(
  [string]$RootPath,
  [object]$LaunchContext,
  [object]$Endpoint,
  [string]$TopologyPath,
  [string]$ClusterFile,
  [string]$DevChainDeploymentPath,
  [string]$ContractsPath,
  [string]$BridgeBackend,
  [int]$TimeoutSeconds
) {
  if (-not (Test-Path -LiteralPath $TopologyPath -PathType Leaf)) {
    return [ordered]@{
      ok = $false
      state = "missing-topology"
      topology = $TopologyPath
      message = "The exp/FDB Hub dev topology must exist before the dev Hub can start."
    }
  }

  Write-Host ("Checking dev Hub prerequisite: FoundationDB cluster file {0}; waiting up to {1}s." -f $ClusterFile, $TimeoutSeconds)
  $fdb = Wait-MainComputerFdbClusterReady $ClusterFile $TimeoutSeconds
  if (-not $fdb.ok) {
    return [ordered]@{
      ok = $false
      state = "foundationdb-not-ready"
      fdb = $fdb
      message = "FoundationDB is not ready; run start.bat first or wait for the resident executor service to bootstrap Docker/FDB, then run dev-hub-start.bat again."
    }
  }

  $backend = ([string]$BridgeBackend).Trim().ToLowerInvariant()
  if ($backend -in @("dev-chain", "credit-bridge-contract")) {
    Write-Host ("Checking dev Hub prerequisite: dev-chain deployment manifest {0}." -f $DevChainDeploymentPath)
    if (-not (Test-Path -LiteralPath $DevChainDeploymentPath -PathType Leaf)) {
      return [ordered]@{
        ok = $false
        state = "missing-dev-chain-deployment"
        dev_chain_deployment_path = $DevChainDeploymentPath
        message = "The dev-chain deployment manifest must exist before the exp/FDB Hub starts; run start.bat first so the resident blockchain service can prepare it."
      }
    }

    Write-Host ("Checking dev Hub prerequisite: dev-chain RPC {0} chainId={1}; waiting up to {2}s." -f ([string]$Endpoint.chain_rpc_url), ([string]$Endpoint.chain_id), $TimeoutSeconds)
    $chain = Wait-MainComputerDevChainRpc ([string]$Endpoint.chain_rpc_url) ([string]$Endpoint.chain_id) $TimeoutSeconds
    if (-not $chain.ok) {
      return [ordered]@{
        ok = $false
        state = "dev-chain-not-ready"
        rpc_url = [string]$Endpoint.chain_rpc_url
        expected_chain_id = [string]$Endpoint.chain_id
        health_check = $chain
        message = "The dev-chain RPC is not healthy yet; run start.bat first or wait for the resident blockchain service to prepare it, then run dev-hub-start.bat again."
      }
    }
  }

  return [ordered]@{
    ok = $true
    state = "ready"
    topology = $TopologyPath
    fdb = $fdb
    dev_chain_deployment_path = $DevChainDeploymentPath
    contracts_path = $ContractsPath
  }
}


function Test-MainComputerDisabledFlag([string]$Value) {
  $text = ConvertTo-StringValue $Value ""
  if ([string]::IsNullOrWhiteSpace($text)) {
    return $false
  }

  return @("0", "false", "no", "off", "disabled") -contains $text.Trim().ToLowerInvariant()
}

function Invoke-MainComputerDevChainResetNoDeploy([string]$RootPath, [object]$LaunchContext, [string]$PythonCommand) {
  $tool = Join-Path $RootPath "tools\dev-chain-reset.py"
  if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    return [ordered]@{
      ok = $false
      state = "missing-tool"
      tool = $tool
      command = @($PythonCommand, $tool)
    }
  }

  $runId = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_DEV_CHAIN_RUN_ID" "test-machine-dev"
  $environment = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_DEV_CHAIN_ENVIRONMENT" "dev"
  $portStrategy = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_DEV_CHAIN_PORT_STRATEGY" "replace-project"
  $waitTimeout = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_DEV_CHAIN_WAIT_TIMEOUT_S" "30"

  $arguments = @(
    $tool,
    "--yes",
    "--run-id", $runId,
    "--environment", $environment,
    "--no-deploy",
    "--port-strategy", $portStrategy,
    "--wait-timeout-s", $waitTimeout
  )

  return Invoke-MainComputerPythonTool `
    -PythonCommand $PythonCommand `
    -RootPath $RootPath `
    -Arguments $arguments `
    -TimeoutSeconds 180
}

function Start-MainComputerDevChainIfNeeded([string]$RootPath, [object]$LaunchContext, [string]$PythonCommand) {
  $treeKind = ConvertTo-StringValue (Get-ObjectPropertyValue $LaunchContext "tree_kind" "source") "source"
  if ($treeKind -ne "source") {
    return [ordered]@{
      ok = $true
      state = "skipped-non-source-tree"
      tree_kind = $treeKind
    }
  }

  $autoStart = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_DEV_CHAIN_AUTO_START" "1"
  if (Test-MainComputerDisabledFlag $autoStart) {
    return [ordered]@{
      ok = $true
      state = "disabled"
      setting = "MAIN_COMPUTER_DEV_CHAIN_AUTO_START"
      value = $autoStart
    }
  }

  $rpcUrl = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL" "http://127.0.0.1:18545"
  $expectedChainId = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ENERGY_CHAIN_ID" "42424242"

  $probe = Test-MainComputerDevChainRpc $rpcUrl $expectedChainId
  if ($probe.ok) {
    Write-Host ("Dev chain RPC is already healthy at {0} chainId={1}; start path will not reset it." -f $rpcUrl, $probe.actual_chain_id_hex)
    return [ordered]@{
      ok = $true
      state = "already-running"
      rpc_url = $rpcUrl
      expected_chain_id = $expectedChainId
      health_check = $probe
    }
  }

  Write-Host ("Dev chain RPC health check failed ({0}); startup will continue and blockchain service will retry dev-chain reset after executor Docker readiness." -f $probe.state)
  return [ordered]@{
    ok = $true
    state = "deferred-to-blockchain-service"
    rpc_url = $rpcUrl
    expected_chain_id = $expectedChainId
    health_check = $probe
    retry_owner = "main_computer.blockchain_service"
    message = "Dev-chain reset is handled by the resident blockchain service so transient Docker startup does not break Main Computer startup."
  }
}


function Resolve-MainComputerRepoPath([string]$RootPath, [string]$Value) {
  $text = ConvertTo-StringValue $Value ""
  if ([string]::IsNullOrWhiteSpace($text)) {
    return ""
  }
  if ([System.IO.Path]::IsPathRooted($text)) {
    return $text
  }
  return Join-Path $RootPath $text
}

function New-MainComputerDevHubEndpointFromUrl(
  [string]$HubUrl,
  [string]$HubId,
  [string]$Network,
  [string]$ChainRpcUrl,
  [string]$ChainId
) {
  $hubBindHost = "127.0.0.1"
  $hubBindPort = 8871
  $cleanUrl = ConvertTo-StringValue $HubUrl ""

  try {
    $uri = [uri]$cleanUrl
    if (-not [string]::IsNullOrWhiteSpace($uri.Host)) {
      $hubBindHost = $uri.Host
    }
    if ($uri.Port -gt 0) {
      $hubBindPort = [int]$uri.Port
    }
  } catch {}

  if ([string]::IsNullOrWhiteSpace($cleanUrl)) {
    $cleanUrl = ("http://{0}:{1}" -f $hubBindHost, $hubBindPort)
  }

  return [ordered]@{
    hub_id = $HubId
    hub_url = $cleanUrl
    status_url = ($cleanUrl.TrimEnd([char[]]"/") + "/api/hub/status")
    host = $hubBindHost
    port = $hubBindPort
    network = $Network
    chain_rpc_url = $ChainRpcUrl
    chain_id = $ChainId
  }
}

function Resolve-MainComputerDevHubEndpoints([string]$RootPath, [object]$LaunchContext) {
  $network = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_NETWORK" "dev"
  $chainRpcUrl = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL" "http://127.0.0.1:18545"
  $chainId = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ENERGY_CHAIN_ID" "42424242"
  $topologyMode = (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_DEV_HUB_TOPOLOGY_MODE" "cluster").Trim().ToLowerInvariant()
  if ([string]::IsNullOrWhiteSpace($topologyMode)) {
    $topologyMode = "cluster"
  }

  $topologyPath = Resolve-MainComputerRepoPath `
    $RootPath `
    (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_TOPOLOGY" "deploy\hub-topology\dev-topology.json")

  if ($topologyMode -ne "single" -and -not [string]::IsNullOrWhiteSpace($topologyPath) -and (Test-Path -LiteralPath $topologyPath -PathType Leaf)) {
    $topology = Read-JsonFile $topologyPath
    if ($null -ne $topology -and $null -ne $topology.hubs) {
      $topologyNetwork = Get-ObjectPropertyValue $topology "network" $null
      if ($null -ne $topologyNetwork) {
        $network = ConvertTo-StringValue (Get-ObjectPropertyValue $topologyNetwork "network_key" $network) $network
        $chainRpcUrl = ConvertTo-StringValue (Get-ObjectPropertyValue $topologyNetwork "chain_rpc_url" $chainRpcUrl) $chainRpcUrl
        $chainId = ConvertTo-StringValue (Get-ObjectPropertyValue $topologyNetwork "chain_id" $chainId) $chainId
      }

      $endpoints = @()
      foreach ($hub in @($topology.hubs)) {
        $hubId = ConvertTo-StringValue (Get-ObjectPropertyValue $hub "hub_id" "") ""
        $hubUrl = ConvertTo-StringValue (Get-ObjectPropertyValue $hub "hub_url" (Get-ObjectPropertyValue $hub "public_url" "")) ""
        if ([string]::IsNullOrWhiteSpace($hubId) -or [string]::IsNullOrWhiteSpace($hubUrl)) {
          continue
        }
        $endpoints += New-MainComputerDevHubEndpointFromUrl $hubUrl $hubId $network $chainRpcUrl $chainId
      }
      if ($endpoints.Count -gt 0) {
        return @($endpoints)
      }
    }
  }

  $hubUrl = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_URL" "http://127.0.0.1:8871"
  $hubBindHost = "127.0.0.1"
  $hubBindPort = 8871

  try {
    $uri = [uri]$hubUrl
    if (-not [string]::IsNullOrWhiteSpace($uri.Host)) {
      $hubBindHost = $uri.Host
    }
    if ($uri.Port -gt 0) {
      $hubBindPort = [int]$uri.Port
    }
  } catch {}

  $hubBindHost = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_BIND_HOST" (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_HOST" $hubBindHost)
  $hubPortText = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_BIND_PORT" (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_PORT" ([string]$hubBindPort))
  try {
    $hubBindPort = [int]$hubPortText
  } catch {
    $hubBindPort = 8871
  }

  $hubUrl = ("http://{0}:{1}" -f $hubBindHost, $hubBindPort)
  $hubId = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_ID" "dev-hub1"

  return @((New-MainComputerDevHubEndpointFromUrl $hubUrl $hubId $network $chainRpcUrl $chainId))
}

function Resolve-MainComputerDevHubEndpoint([string]$RootPath, [object]$LaunchContext) {
  $endpoints = @(Resolve-MainComputerDevHubEndpoints $RootPath $LaunchContext)
  if ($endpoints.Count -eq 0) {
    return New-MainComputerDevHubEndpointFromUrl "http://127.0.0.1:8871" "dev-hub1" "dev" "http://127.0.0.1:18545" "42424242"
  }
  return $endpoints[0]
}

function Test-MainComputerDevHubStatus([string]$StatusUrl) {
  if ([string]::IsNullOrWhiteSpace($StatusUrl)) {
    return [ordered]@{
      ok = $false
      state = "missing-status-url"
      status_url = $StatusUrl
    }
  }

  try {
    $response = Invoke-RestMethod `
      -Method Get `
      -Uri $StatusUrl `
      -TimeoutSec 5
  } catch {
    return [ordered]@{
      ok = $false
      state = "unreachable"
      status_url = $StatusUrl
      message = $_.Exception.Message
    }
  }

  $ok = $false
  try {
    $ok = [bool](Get-ObjectPropertyValue $response "ok" $false)
  } catch {
    $ok = $false
  }

  return [ordered]@{
    ok = $ok
    state = $(if ($ok) { "healthy" } else { "not-ok" })
    status_url = $StatusUrl
    response = $response
  }
}

function Wait-MainComputerDevHubStatus([string]$StatusUrl, [int]$TimeoutSeconds = 30) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $last = $null

  do {
    $last = Test-MainComputerDevHubStatus $StatusUrl
    if ($last.ok) {
      return $last
    }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)

  return [ordered]@{
    ok = $false
    state = "timeout"
    status_url = $StatusUrl
    last_health_check = $last
  }
}


function Wait-MainComputerDevHubEndpointsStatus([object[]]$Endpoints, [int]$TimeoutSeconds = 30) {
  $checks = @()
  $allOk = $true
  foreach ($endpoint in @($Endpoints)) {
    $health = Wait-MainComputerDevHubStatus ([string]$endpoint.status_url) ([int]$TimeoutSeconds)
    $checks += [ordered]@{
      hub_id = [string]$endpoint.hub_id
      hub_url = [string]$endpoint.hub_url
      status_url = [string]$endpoint.status_url
      health_check = $health
      ok = [bool]$health.ok
    }
    if (-not $health.ok) {
      $allOk = $false
    }
  }

  return [ordered]@{
    ok = $allOk
    state = $(if ($allOk) { "healthy" } else { "not-all-healthy" })
    checks = $checks
  }
}


function Stop-MainComputerDevHubForRestart([string]$RootPath, [object]$LaunchContext) {
  $endpoints = @(Resolve-MainComputerDevHubEndpoints $RootPath $LaunchContext)
  if ($endpoints.Count -eq 0) {
    $endpoints = @((Resolve-MainComputerDevHubEndpoint $RootPath $LaunchContext))
  }
  $entryEndpoint = $endpoints[0]
  $pidPath = Get-DevHubPidPath $RootPath
  $candidates = @{}

  Add-PidCandidate $candidates (Get-PidFromFile $pidPath) "dev-hub" $pidPath 15 $true

  $ports = @()
  foreach ($endpoint in @($endpoints)) {
    try {
      $port = [int]$endpoint.port
      if ($port -gt 0 -and $ports -notcontains $port) {
        $ports += $port
      }
    } catch {}
  }

  $portText = ($ports | ForEach-Object { [string]$_ }) -join ","
  Write-Host ("Scanning for existing dev Hub listeners on ports {0}." -f $portText)
  foreach ($row in @(Get-NetstatListenRows -Ports $ports)) {
    $processId = [int]$row.OwningProcess
    if ($processId -le 0 -or $processId -eq $PID) {
      continue
    }

    $commandLine = Get-ProcessCommandLine $processId
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    $processName = ""
    if ($null -ne $process) {
      $processName = [string]$process.ProcessName
    }

    if ((Test-MainComputerServiceCommandLine $commandLine $RootPath) -or ([string]::IsNullOrWhiteSpace($commandLine) -and $processName -like "python*")) {
      Add-PidCandidate $candidates $processId "dev-hub-port-listener" ("listener on dev Hub topology port " + [string]$row.LocalPort) 15 $true
    }
  }

  $results = @()
  $candidateList = @($candidates.Values | Sort-Object -Property @{ Expression = { [int]$_['order'] } }, @{ Expression = { [int]$_['pid'] } })
  if ($candidateList.Count -eq 0) {
    Write-Host "No existing dev Hub process was found."
  } else {
    Write-Host ("Stopping {0} existing dev Hub process candidate(s)." -f $candidateList.Count)
  }
  foreach ($candidate in $candidateList) {
    Write-Host ("Stopping dev Hub candidate PID {0} ({1})." -f ([int]$candidate.pid), ([string]$candidate.role))
    $results += Stop-OnePid $candidate $RootPath
  }

  try {
    if (Test-Path -LiteralPath $pidPath -PathType Leaf) {
      Remove-Item -LiteralPath $pidPath -Force
    }
  } catch {
    Write-Warning "Could not remove dev Hub PID file ${pidPath}: $($_.Exception.Message)"
  }

  return [ordered]@{
    endpoint = $entryEndpoint
    endpoints = $endpoints
    ports = $ports
    pid_file = $pidPath
    process_results = $results
  }
}


function Start-MainComputerDevHubFresh([string]$RootPath, [object]$LaunchContext, [string]$PythonCommand) {
  $treeKind = ConvertTo-StringValue (Get-ObjectPropertyValue $LaunchContext "tree_kind" "source") "source"
  if ($treeKind -ne "source") {
    return [ordered]@{
      ok = $true
      state = "skipped-non-source-tree"
      tree_kind = $treeKind
    }
  }

  $autoStart = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_DEV_HUB_AUTO_START" "1"
  if (Test-MainComputerDisabledFlag $autoStart) {
    return [ordered]@{
      ok = $true
      state = "disabled"
      setting = "MAIN_COMPUTER_DEV_HUB_AUTO_START"
      value = $autoStart
    }
  }

  $endpoints = @(Resolve-MainComputerDevHubEndpoints $RootPath $LaunchContext)
  if ($endpoints.Count -eq 0) {
    $endpoints = @((Resolve-MainComputerDevHubEndpoint $RootPath $LaunchContext))
  }
  $endpoint = $endpoints[0]
  $hubPorts = @()
  foreach ($item in @($endpoints)) {
    try {
      $port = [int]$item.port
      if ($port -gt 0 -and $hubPorts -notcontains $port) {
        $hubPorts += $port
      }
    } catch {}
  }
  if ($hubPorts.Count -eq 0) {
    $hubPorts = @([int]$endpoint.port)
  }
  $hubPortsText = ($hubPorts | ForEach-Object { [string]$_ }) -join ","

  $hubRuntime = Join-Path $RootPath ("runtime\hub\" + [string]$endpoint.network)
  $runtimeEnvFile = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_RUNTIME_ENV_FILE" (Join-Path $hubRuntime "hub-runtime.env")
  $runtimeEnvEnsure = Ensure-MainComputerRuntimeEnvFile $runtimeEnvFile
  if ($runtimeEnvEnsure.created) {
    Write-Host ("Created default dev Hub runtime env file at {0}." -f ([string]$runtimeEnvFile))
  }
  $runtimeEnvStatus = Merge-MainComputerRuntimeEnvFile $LaunchContext $runtimeEnvFile
  $runtimeEnvStatus["created"] = [bool]$runtimeEnvEnsure.created
  $runtimeEnvStatus["exists"] = [bool]$runtimeEnvEnsure.exists
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_RUNTIME_ENV_FILE", [string]$runtimeEnvFile, "Process")

  $pidPath = Get-DevHubPidPath $RootPath
  Write-Host ("Resetting dev Hub topology on start path at {0}; any previous dev Hub process on ports {1} will be stopped first." -f $endpoint.hub_url, $hubPortsText)
  $stopResult = Stop-MainComputerDevHubForRestart $RootPath $LaunchContext
  Write-Host "Previous dev Hub stop check completed."

  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK", "1", "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_NETWORK", [string]$endpoint.network, "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_URL", [string]$endpoint.hub_url, "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_ENTRY_URLS", (($endpoints | ForEach-Object { [string]$_.hub_url }) -join ","), "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL", [string]$endpoint.chain_rpc_url, "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_ENERGY_CHAIN_ID", [string]$endpoint.chain_id, "Process")
  $hubKind = (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_DEV_HUB_KIND" "exp-fdb").Trim().ToLowerInvariant()
  if ([string]::IsNullOrWhiteSpace($hubKind)) {
    $hubKind = "exp-fdb"
  }
  $smokeBridgeSetting = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE" "1"
  $missingBridgeSignerSetting = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER" "0"
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_DEV_HUB_KIND", $hubKind, "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE", $smokeBridgeSetting, "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER", $missingBridgeSignerSetting, "Process")
  $bridgeBackend = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_BRIDGE_BACKEND" "dev-chain"
  if ([string]::IsNullOrWhiteSpace($bridgeBackend)) {
    $bridgeBackend = "dev-chain"
  }
  $devChainDeploymentPath = Get-LaunchEnvironmentValue `
    $LaunchContext `
    "MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH" `
    (Join-Path $RootPath ("runtime\deployments\" + [string]$endpoint.network + "\latest.json"))
  $contractsPath = Get-LaunchEnvironmentValue `
    $LaunchContext `
    "MAIN_COMPUTER_HUB_CONTRACTS_PATH" `
    (Join-Path $RootPath ("main_computer\config\" + [string]$endpoint.network + "_contracts.json"))
  $clusterFile = Get-LaunchEnvironmentValue `
    $LaunchContext `
    "MAIN_COMPUTER_HUB_FDB_CLUSTER_FILE" `
    (Join-Path $RootPath ".foundationdb\docker.cluster")
  $topologyPath = Resolve-MainComputerRepoPath `
    $RootPath `
    (Get-LaunchEnvironmentValue `
      $LaunchContext `
      "MAIN_COMPUTER_HUB_TOPOLOGY" `
      (Join-Path $RootPath "deploy\hub-topology\dev-topology.json"))
  $hubId = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_ID" "dev-hub1"
  $hubStartTimeoutSeconds = Get-MainComputerDevHubStartTimeoutSeconds $LaunchContext $hubKind

  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_BRIDGE_BACKEND", [string]$bridgeBackend, "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH", [string]$devChainDeploymentPath, "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_CONTRACTS_PATH", [string]$contractsPath, "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_TOPOLOGY", [string]$topologyPath, "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_ID", [string]$hubId, "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_HUB_FDB_CLUSTER_FILE", [string]$clusterFile, "Process")
  [Environment]::SetEnvironmentVariable("MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS", ([string]$hubStartTimeoutSeconds), "Process")

  if ($hubKind -eq "exp-fdb") {
    $prerequisites = Test-MainComputerExpFdbHubPrerequisites `
      $RootPath `
      $LaunchContext `
      $endpoint `
      ([string]$topologyPath) `
      ([string]$clusterFile) `
      ([string]$devChainDeploymentPath) `
      ([string]$contractsPath) `
      ([string]$bridgeBackend) `
      ([int]$hubStartTimeoutSeconds)
    if (-not $prerequisites.ok) {
      return [ordered]@{
        ok = $false
        state = "dependency-not-ready"
        endpoint = $endpoint
        endpoints = $endpoints
        ports = $hubPorts
        prerequisites = $prerequisites
        message = $prerequisites.message
      }
    }
  }

  $hubRuntime = Join-Path $RootPath ("runtime\hub\" + [string]$endpoint.network)
  Ensure-Directory $hubRuntime
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $stdout = Join-Path $hubRuntime ("dev-hub-topology-" + $stamp + ".stdout.log")
  $stderr = Join-Path $hubRuntime ("dev-hub-topology-" + $stamp + ".stderr.log")

  if ($hubKind -eq "legacy") {
    $arguments = @(
      "-m", "main_computer.cli",
      "hub",
      "--host", [string]$endpoint.host,
      "--port", [string]$endpoint.port,
      "--network", [string]$endpoint.network,
      "--chain-rpc-url", [string]$endpoint.chain_rpc_url,
      "--chain-id", [string]$endpoint.chain_id,
      "--bridge-backend", [string]$bridgeBackend,
      "--dev-chain-deployment-path", [string]$devChainDeploymentPath,
      "--contracts-path", [string]$contractsPath,
      "--allow-missing-bridge-signer",
      "-noverbose"
    )
  } else {
    $arguments = @(
      "exp-fdb-hub.py",
      "--runtime-env-file", [string]$runtimeEnvFile,
      "--host", [string]$endpoint.host,
      "-ports", [string]$hubPortsText,
      "--hub-url", [string]$endpoint.hub_url,
      "--cluster-file", [string]$clusterFile,
      "--no-fdb-autostart",
      "--network-key", [string]$endpoint.network,
      "--network-display-name", "Main Computer Local Dev",
      "--network-kind", "dev",
      "--chain-rpc-url", [string]$endpoint.chain_rpc_url,
      "--chain-id", [string]$endpoint.chain_id,
      "--bridge-backend", [string]$bridgeBackend,
      "--dev-chain-deployment-path", [string]$devChainDeploymentPath,
      "--contracts-path", [string]$contractsPath,
      "--topology", [string]$topologyPath,
      "--require-multisession-auth",
      "-noverbose"
    )
    if ($endpoints.Count -le 1 -and -not [string]::IsNullOrWhiteSpace($hubId)) {
      $arguments += @("--hub-id", [string]$hubId)
    }
    if (-not (Test-MainComputerDisabledFlag $smokeBridgeSetting)) {
      $arguments += "--enable-smoke-bridge"
    }
    if (-not (Test-MainComputerDisabledFlag $missingBridgeSignerSetting)) {
      $arguments += "--allow-missing-bridge-signer"
    }
  }
  $argString = Join-CommandLine $arguments

  $process = Start-Process `
    -FilePath $PythonCommand `
    -ArgumentList $argString `
    -WorkingDirectory $RootPath `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru

  Set-Content -LiteralPath $pidPath -Value ([string]$process.Id) -Encoding ASCII

  if ($hubKind -eq "legacy") {
    Write-Host ("Waiting for dev Hub health at {0}; timeout {1}s." -f ([string]$endpoint.status_url), ([int]$hubStartTimeoutSeconds))
    $health = Wait-MainComputerDevHubStatus ([string]$endpoint.status_url) ([int]$hubStartTimeoutSeconds)
  } else {
    Write-Host ("Waiting for dev Hub topology health on ports {0}; timeout {1}s." -f $hubPortsText, ([int]$hubStartTimeoutSeconds))
    $health = Wait-MainComputerDevHubEndpointsStatus $endpoints ([int]$hubStartTimeoutSeconds)
  }
  if (-not $health.ok) {
    return [ordered]@{
      ok = $false
      state = "unhealthy-after-start"
      endpoint = $endpoint
      endpoints = $endpoints
      ports = $hubPorts
      pid = $process.Id
      pid_file = $pidPath
      stdout = $stdout
      stderr = $stderr
      command = @($PythonCommand) + $arguments
      stop_before_start = $stopResult
      health_check = $health
      runtime_env_file = $runtimeEnvStatus
    }
  }

  Write-Host ("Dev Hub topology is running at {0} as PID {1}; ports {2}." -f $endpoint.hub_url, $process.Id, $hubPortsText)
  return [ordered]@{
    ok = $true
    state = $(if ($endpoints.Count -gt 1) { "started-topology" } else { "started" })
    endpoint = $endpoint
    endpoints = $endpoints
    ports = $hubPorts
    pid = $process.Id
    pid_file = $pidPath
    stdout = $stdout
    stderr = $stderr
    command = @($PythonCommand) + $arguments
    stop_before_start = $stopResult
    runtime_env_file = $runtimeEnvStatus
    health_check = $health
  }
}

function New-StartSession(
  [string]$RootPath,
  [object]$LaunchContext,
  [int]$LauncherPid,
  [string]$StdoutPath,
  [string]$StderrPath,
  [string[]]$LauncherArgs,
  [string]$StartedByName,
  [object]$GiteaStart,
  [object]$LocalPlatformStart,
  [object]$DevChainStart,
  [object]$DevHubStart
) {
  $applicationsProject = Get-SafeDockerName `
    (Get-EnvFirstValue @("MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT") "main-computer-applications") `
    "main-computer-applications"

  $devComposeProject = Get-SafeDockerName `
    (Get-EnvFirstValue @("MAIN_COMPUTER_DEV_COMPOSE_PROJECT", "MAIN_COMPUTER_EXECUTOR_COMPOSE_PROJECT") "main-computer-unleashed") `
    "main-computer-unleashed"

  $localPlatformProject = Get-SafeDockerName `
    (Get-EnvFirstValue @("MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT") "main-computer-local-platform-unleashed") `
    "main-computer-local-platform-unleashed"

  $applicationsEnv = Join-Path $RootPath "runtime\applications_service\applications.env"
  $devCompose = Join-Path $RootPath "docker-compose.dev.yml"
  $applicationsCompose = Join-Path $RootPath "docker-compose.applications.yml"
  $localPlatformCompose = Get-EnvFirstValue @("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH") ""
  $launcherName = $(if ([string]::IsNullOrWhiteSpace($StartedByName)) { "start_v2.bat" } else { $StartedByName })
  $controlRoot = Get-ControlRoot $RootPath $LaunchContext

  return [ordered]@{
    schema_version = 2
    mode = [string]$LaunchContext.mode
    tree_kind = [string]$LaunchContext.tree_kind
    started_by = $launcherName
    started_at = Get-UtcNowText
    root = $RootPath
    control_root = $controlRoot
    launcher = [ordered]@{
      file = [string]$LaunchContext.python
      arguments = $LauncherArgs
      pid = $LauncherPid
      stdout = $StdoutPath
      stderr = $StderrPath
      context_file = [string]$LaunchContext.context_file
    }
    environment = $LaunchContext.environment
    gitea = $GiteaStart
    local_platform = $LocalPlatformStart
    dev_chain = $DevChainStart
    dev_hub = $DevHubStart
    managed_pid_files = @(
      (Join-Path $RootPath ".main_computer_service_supervisor.pid"),
      (Join-Path $RootPath ".main_computer_main_log_service.pid"),
      (Get-DevHubPidPath $RootPath),
      (Join-Path $controlRoot ".main_computer_viewport.pid"),
      (Join-Path $controlRoot ".main_computer_heartbeat.pid"),
      (Join-Path $RootPath ".main_computer_executor_service.pid"),
      (Join-Path $RootPath ".main_computer_applications_service.pid")
    )
    managed_state_files = @(
      (Join-Path $RootPath "runtime\service_supervisor\state.json"),
      (Join-Path $RootPath "runtime\main_log\state.json"),
      (Join-Path $RootPath "runtime\executor_service\state.json"),
      (Join-Path $RootPath "runtime\applications_service\state.json"),
      (Join-Path $RootPath "runtime\blockchain_service\state.json")
    )
    managed_python_services = @(
      [ordered]@{
        name = "supervisor"
        module = "main_computer.app_control bootstrap"
        pid_file = Join-Path $RootPath ".main_computer_service_supervisor.pid"
        state_file = Join-Path $RootPath "runtime\service_supervisor\state.json"
      },
      [ordered]@{
        name = "main-log"
        module = "main_computer.main_log_service serve"
        pid_file = Join-Path $RootPath ".main_computer_main_log_service.pid"
        state_file = Join-Path $RootPath "runtime\main_log\state.json"
        health_url = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_MAIN_LOG_URL" "http://127.0.0.1:8767"
        follow_url = ((Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_MAIN_LOG_URL" "http://127.0.0.1:8767") + "/v1/log/follow")
        surprise_url = ((Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_MAIN_LOG_URL" "http://127.0.0.1:8767") + "/v1/log/surprise")
      },
      [ordered]@{
        name = "viewport"
        module = "main_computer.app_control run"
        pid_file = Join-Path $controlRoot ".main_computer_viewport.pid"
      },
      [ordered]@{
        name = "heartbeat"
        module = "main_computer.cli heartbeat"
        pid_file = Join-Path $controlRoot ".main_computer_heartbeat.pid"
      },
      [ordered]@{
        name = "executor"
        module = "main_computer.executor_service boot --watch"
        pid_file = Join-Path $RootPath ".main_computer_executor_service.pid"
        state_file = Join-Path $RootPath "runtime\executor_service\state.json"
      },
      [ordered]@{
        name = "applications"
        module = "main_computer.applications_service boot --watch"
        pid_file = Join-Path $RootPath ".main_computer_applications_service.pid"
        state_file = Join-Path $RootPath "runtime\applications_service\state.json"
      },
      [ordered]@{
        name = "dev-hub"
        module = "main_computer.cli hub --network dev"
        pid_file = Get-DevHubPidPath $RootPath
        state_file = Join-Path $RootPath "runtime\hub\dev"
      },
      [ordered]@{
        name = "blockchain"
        module = "main_computer.blockchain_service boot --watch"
        state_file = Join-Path $RootPath "runtime\blockchain_service\state.json"
      }
    )
    docker_stacks = @(
      [ordered]@{
        name = "executor-unleashed"
        docker_command = "docker"
        compose_file = $devCompose
        project_name = $devComposeProject
        env_file = $null
        started_by = @("main_computer.executor_service")
        start_commands = @(
          @("docker", "compose", "--project-name", $devComposeProject, "-f", $devCompose, "--profile", "executor", "build", "executor-image")
        )
        stop_command = @("docker", "compose", "--project-name", $devComposeProject, "-f", $devCompose, "down", "--remove-orphans")
      },
      [ordered]@{
        name = "local-platform"
        docker_command = "docker"
        compose_file = $localPlatformCompose
        project_name = $localPlatformProject
        env_file = $null
        started_by = @("start_v2.bat", "tools/local-platform/website-docker.py")
        start_commands = @(
          @("docker", "compose", "--project-name", $localPlatformProject, "-f", $localPlatformCompose, "up", "-d", "--build")
        )
        stop_command = @("docker", "compose", "--project-name", $localPlatformProject, "-f", $localPlatformCompose, "down", "--remove-orphans")
      },
      [ordered]@{
        name = "applications"
        docker_command = "docker"
        compose_file = $applicationsCompose
        project_name = $applicationsProject
        env_file = $applicationsEnv
        started_by = @("main_computer.applications_service")
        start_commands = @(
          @("docker", "compose", "--project-name", $applicationsProject, "--env-file", $applicationsEnv, "-f", $applicationsCompose, "up", "-d", "--remove-orphans", "postgres", "redis", "soketi", "coolify")
        )
        stop_command = @("docker", "compose", "--project-name", $applicationsProject, "--env-file", $applicationsEnv, "-f", $applicationsCompose, "down", "--remove-orphans")
      }
    )
  }
}

function Invoke-MainComputerOnlyOfficeControl {
  param(
    [Parameter(Mandatory = $true)][string]$RootPath,
    [Parameter(Mandatory = $true)][object]$LaunchContext,
    [Parameter(Mandatory = $true)][string]$OnlyOfficeAction
  )

  $enabled = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_ENABLED" "1"
  if ($enabled -eq "0" -or $enabled -eq "false" -or $enabled -eq "disabled") {
    Write-Host "ONLYOFFICE Docker startup skipped because MAIN_COMPUTER_ONLYOFFICE_ENABLED=$enabled."
    return
  }

  $mode = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_MODE" "docker"
  if ([string]::IsNullOrWhiteSpace($mode)) {
    $mode = "docker"
  }
  if ($mode -ne "docker" -and $mode -ne "disabled") {
    Write-Warning "MAIN_COMPUTER_ONLYOFFICE_MODE=$mode is unsupported; using Docker ONLYOFFICE mode instead."
    $mode = "docker"
  }
  if ($mode -eq "disabled") {
    Write-Host "ONLYOFFICE Docker startup skipped because MAIN_COMPUTER_ONLYOFFICE_MODE=disabled."
    return
  }

  $onlyOfficePortText = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_PORT" "18085"
  $appPortText = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_CONTROL_PORT" "8765"
  $projectName = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_PROJECT" "main-computer-onlyoffice"
  $jwtEnabled = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED" "false"
  $jwtSecret = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET" $(if ($jwtEnabled -eq "false" -or $jwtEnabled -eq "0" -or $jwtEnabled -eq "disabled") { "" } else { "main-computer-onlyoffice-local-secret" })
  $containerName = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME" "main-computer-onlyoffice-documentserver"
  $onlyOfficePort = 18085
  $appPort = 8765
  try { $onlyOfficePort = [int]$onlyOfficePortText } catch { $onlyOfficePort = 18085 }
  try { $appPort = [int]$appPortText } catch { $appPort = 8765 }

  $controlScript = Join-Path $RootPath "tools\onlyoffice\onlyoffice-control.ps1"
  if (-not (Test-Path -LiteralPath $controlScript -PathType Leaf)) {
    Write-Warning "ONLYOFFICE control script not found: $controlScript"
    return
  }

  $env:MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME = $containerName
  $env:MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED = $jwtEnabled
  $env:MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS" "true"
  $env:MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS" "true"

  $runtimeDisplay = Get-MainComputerRequestedContainerRuntime $LaunchContext
  if ([string]::IsNullOrWhiteSpace($runtimeDisplay)) {
    $runtimeDisplay = ConvertTo-StringValue ([Environment]::GetEnvironmentVariable("MAIN_COMPUTER_CONTAINER_RUNTIME")) "auto"
  }

  Write-Host ""
  Write-Host ("ONLYOFFICE container control: {0} mode={1} runtime={2} port={3} appPort={4}" -f $OnlyOfficeAction, $mode, $runtimeDisplay, $onlyOfficePort, $appPort)
  try {
    $controlArgs = @(
      "-NoProfile",
      "-ExecutionPolicy", "Bypass",
      "-File", $controlScript,
      $OnlyOfficeAction,
      "-Mode", $mode,
      "-Port", $onlyOfficePort,
      "-AppPort", $appPort,
      "-ProjectName", $projectName,
      "-JwtEnabled", $jwtEnabled
    )
    if (-not [string]::IsNullOrWhiteSpace($jwtSecret)) {
      $controlArgs += @("-JwtSecret", $jwtSecret)
    }

    & powershell @controlArgs

    if ($LASTEXITCODE -ne 0) {
      Write-Warning "ONLYOFFICE control action '$OnlyOfficeAction' returned exit code $LASTEXITCODE."
    }
  } catch {
    Write-Warning "ONLYOFFICE control action '$OnlyOfficeAction' failed: $($_.Exception.Message)"
  }
}

function Start-MainComputer([string]$RootPath, [string]$StartedByName, [bool]$NoDevHubRequested) {
  $callerContainerRuntimeOverride = Get-MainComputerCallerContainerRuntimeOverride

  Write-Host "Force-stopping current Main Computer app processes before launch; Docker stacks are left alone..."
  Stop-MainComputer $RootPath $true | Out-Null

  $launchContext = Resolve-MainComputerLaunchContext $RootPath
  Set-MainComputerLaunchEnvironment $launchContext
  Restore-MainComputerCallerContainerRuntimeOverride $launchContext $callerContainerRuntimeOverride
  $controlRoot = Get-ControlRoot $RootPath $launchContext
  $pythonCommand = [string]$launchContext.python

  Assert-MainComputerExplicitContainerRuntimeAvailable $RootPath $launchContext $pythonCommand

  $devChainStart = Start-MainComputerDevChainIfNeeded $RootPath $launchContext $pythonCommand
  if ($null -eq $devChainStart) {
    throw "Dev chain startup returned no status."
  }
  if (-not $devChainStart.ok) {
    $message = ConvertTo-StringValue (Get-ObjectPropertyValue $devChainStart "state" "unknown") "unknown"
    throw ("Dev chain startup failed: {0}" -f $message)
  }

  $devHubStart = [ordered]@{
    ok = $true
    state = "pending"
    requested = $true
    message = "Dev Hub startup is enabled by default. Run start.bat --no-dev-hub to skip it."
  }
  if ($NoDevHubRequested) {
    $devHubStart = [ordered]@{
      ok = $true
      state = "skipped-disabled"
      requested = $false
      message = "Dev Hub startup disabled by --no-dev-hub."
    }
  }

  $serviceRuntime = Join-Path $RootPath "runtime\service_supervisor"
  Ensure-Directory $serviceRuntime
  Ensure-Directory (Get-StartStopRuntime $RootPath)

  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $stdout = Join-Path $serviceRuntime ("service_supervisor-" + $stamp + ".stdout.log")
  $stderr = Join-Path $serviceRuntime ("service_supervisor-" + $stamp + ".stderr.log")
  $launcherArgs = @("-m", "main_computer.app_control", "--root", $RootPath)
  $controlPort = Get-LaunchEnvironmentValue $launchContext "MAIN_COMPUTER_CONTROL_PORT" ""
  if (-not [string]::IsNullOrWhiteSpace($controlPort)) {
    $launcherArgs += @("--port", $controlPort)
  }
  if (-not [string]::IsNullOrWhiteSpace($pythonCommand)) {
    $launcherArgs += @("--python-command", $pythonCommand)
  }
  $launcherArgs += @("bootstrap")
  $argString = Join-CommandLine $launcherArgs

  try {
    $giteaStart = Start-MainComputerGiteaIfMissing $RootPath $launchContext $pythonCommand
  } catch {
    $giteaStart = [ordered]@{
      ok = $false
      state = "exception"
      message = $_.Exception.Message
      compose_project = $env:MAIN_COMPUTER_GITEA_COMPOSE_PROJECT
      compose_file = (Join-Path $RootPath "docker-compose.gitea.yml")
    }
  }
  if ($null -eq $giteaStart) {
    $giteaStart = [ordered]@{
      ok = $false
      state = "unknown"
      message = "Start-MainComputerGiteaIfMissing returned no status."
      compose_project = $env:MAIN_COMPUTER_GITEA_COMPOSE_PROJECT
      compose_file = (Join-Path $RootPath "docker-compose.gitea.yml")
    }
  }
  if (-not $giteaStart.ok) {
    Write-MainComputerGiteaWarning $giteaStart
  }

  try {
    $localPlatformStart = Start-MainComputerLocalPlatform $RootPath $pythonCommand
  } catch {
    $localPlatformStart = [ordered]@{
      ok = $false
      state = "exception"
      message = $_.Exception.Message
      compose_project = $env:MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT
      compose_file = $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH
    }
  }
  if ($null -eq $localPlatformStart) {
    $localPlatformStart = [ordered]@{
      ok = $false
      state = "unknown"
      message = "Start-MainComputerLocalPlatform returned no status."
      compose_project = $env:MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT
      compose_file = $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH
    }
  }
  if (-not $localPlatformStart.ok) {
    Write-MainComputerLocalPlatformWarning $localPlatformStart
  }

  $process = Start-Process `
    -FilePath $pythonCommand `
    -ArgumentList $argString `
    -WorkingDirectory $RootPath `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru

  if (-not $NoDevHubRequested) {
    try {
      $devHubStart = Start-MainComputerDevHubFresh $RootPath $launchContext $pythonCommand
    } catch {
      $devHubStart = [ordered]@{
        ok = $false
        state = "exception"
        requested = $true
        message = $_.Exception.Message
      }
    }
    if ($null -eq $devHubStart) {
      $devHubStart = [ordered]@{
        ok = $false
        state = "unknown"
        requested = $true
        message = "Start-MainComputerDevHubFresh returned no status."
      }
    }
    if (-not $devHubStart.ok) {
      Write-MainComputerDevHubWarning $devHubStart
    }
  }

  $session = New-StartSession $RootPath $launchContext $process.Id $stdout $stderr $launcherArgs $StartedByName $giteaStart $localPlatformStart $devChainStart $devHubStart
  $sessionPath = Get-StartSessionPath $RootPath
  Write-JsonFile $sessionPath $session

  Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "start"

  Write-Host ("Main Computer {0} supervisor launch requested, detached as PID {1}." -f [string]$launchContext.tree_kind, $process.Id)
  Write-Host ("Mode:             " + [string]$launchContext.mode)
  Write-Host ("Python:           " + $pythonCommand)
  if (-not [string]::IsNullOrWhiteSpace([string]$launchContext.context_file)) {
    Write-Host ("Launcher config:  " + [string]$launchContext.context_file)
  }
  Write-Host ("Start session:    " + $sessionPath)
  Write-Host ("Control root:     " + $controlRoot)
  Write-Host ("Supervisor state: " + (Join-Path $serviceRuntime "state.json"))
  Write-Host ("Supervisor PID:   " + (Join-Path $RootPath ".main_computer_service_supervisor.pid"))
  Write-Host ("Main Log state:   " + (Join-Path $RootPath "runtime\main_log\state.json"))
  Write-Host ("Main Log PID:     " + (Join-Path $RootPath ".main_computer_main_log_service.pid"))
  Write-Host ("Main Log API:     " + (Get-LaunchEnvironmentValue $launchContext "MAIN_COMPUTER_MAIN_LOG_URL" "http://127.0.0.1:8767"))
  Write-Host ("App PID:          " + (Join-Path $controlRoot ".main_computer_viewport.pid"))
  Write-Host ("Heartbeat PID:    " + (Join-Path $controlRoot ".main_computer_heartbeat.pid"))
  Write-Host ("Executor state:   " + (Join-Path $RootPath "runtime\executor_service\state.json"))
  Write-Host ("Applications:     " + (Join-Path $RootPath "runtime\applications_service\state.json"))
  Write-Host ("Dev Hub PID:      " + (Get-DevHubPidPath $RootPath))
  Write-Host ("Dev Hub runtime:  " + (Join-Path $RootPath "runtime\hub\dev"))
  Write-Host ("Blockchain:       " + (Join-Path $RootPath "runtime\blockchain_service\state.json"))
  Write-Host ("stdout:           " + $stdout)
  Write-Host ("stderr:           " + $stderr)
}


function Start-MainComputerDevHubOnly([string]$RootPath, [string]$StartedByName) {
  Write-Host "Starting only the Main Computer dev Hub; app/supervisor startup is not requested."

  $callerContainerRuntimeOverride = Get-MainComputerCallerContainerRuntimeOverride
  $launchContext = Resolve-MainComputerLaunchContext $RootPath
  $environment = Get-ObjectPropertyValue $launchContext "environment" $null
  $callerTimeout = [Environment]::GetEnvironmentVariable("MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS")
  $configuredTimeout = Get-LaunchEnvironmentValue $launchContext "MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS" ""
  if (-not [string]::IsNullOrWhiteSpace($callerTimeout)) {
    Merge-Environment $environment ([ordered]@{ "MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS" = $callerTimeout }) | Out-Null
  } elseif ([string]::IsNullOrWhiteSpace($configuredTimeout)) {
    Merge-Environment $environment ([ordered]@{ "MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS" = "20" }) | Out-Null
    Write-Host "Dev-Hub-only startup uses MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS=20 by default; set it before launch to wait longer."
  }
  Set-MainComputerLaunchEnvironment $launchContext
  Restore-MainComputerCallerContainerRuntimeOverride $launchContext $callerContainerRuntimeOverride
  $pythonCommand = [string]$launchContext.python

  Assert-MainComputerExplicitContainerRuntimeAvailable $RootPath $launchContext $pythonCommand

  try {
    $devHubStart = Start-MainComputerDevHubFresh $RootPath $launchContext $pythonCommand
  } catch {
    $devHubStart = [ordered]@{
      ok = $false
      state = "exception"
      requested = $true
      message = $_.Exception.Message
    }
  }
  if ($null -eq $devHubStart) {
    $devHubStart = [ordered]@{
      ok = $false
      state = "unknown"
      requested = $true
      message = "Start-MainComputerDevHubFresh returned no status."
    }
  }

  $runtime = Get-StartStopRuntime $RootPath
  Ensure-Directory $runtime
  $statusPath = Join-Path $runtime "dev-hub-start.json"
  Write-JsonFile $statusPath ([ordered]@{
      schema_version = 1
      action = "dev-hub-start"
      started_by = $(if ([string]::IsNullOrWhiteSpace($StartedByName)) { "dev-hub-start.bat" } else { $StartedByName })
      started_at = Get-UtcNowText
      root = $RootPath
      tree_kind = [string]$launchContext.tree_kind
      python = $pythonCommand
      dev_hub = $devHubStart
    })

  if (-not $devHubStart.ok) {
    Write-MainComputerDevHubWarning $devHubStart
    $state = ConvertTo-StringValue (Get-ObjectPropertyValue $devHubStart "state" "unknown") "unknown"
    throw ("Dev Hub startup failed: {0}" -f $state)
  }

  Write-Host ("Dev Hub startup requested by {0}." -f $(if ([string]::IsNullOrWhiteSpace($StartedByName)) { "dev-hub-start.bat" } else { $StartedByName }))
  Write-Host ("Dev Hub PID:      " + (Get-DevHubPidPath $RootPath))
  Write-Host ("Dev Hub runtime:  " + (Join-Path $RootPath "runtime\hub\dev"))
  Write-Host ("Dev Hub status:   " + $statusPath)
}


function Show-MainComputerStatus([string]$RootPath) {
  $callerContainerRuntimeOverride = Get-MainComputerCallerContainerRuntimeOverride
  $launchContext = Resolve-MainComputerLaunchContext $RootPath
  Set-MainComputerLaunchEnvironment $launchContext
  Restore-MainComputerCallerContainerRuntimeOverride $launchContext $callerContainerRuntimeOverride

  $statusArgs = @(
    "-m",
    "main_computer.service_supervisor",
    "--root",
    $RootPath,
    "status",
    "--summary",
    "--wait-s",
    "30",
    "--interval-s",
    "2"
  )

  $pythonCommand = [string]$launchContext.python
  Write-Host ("Status via Python: " + $pythonCommand)
  & $pythonCommand @statusArgs
  $statusExitCode = $LASTEXITCODE
  Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "status"
  return $statusExitCode
}

function Get-PidFromPayload([object]$Payload) {
  if ($null -eq $Payload) {
    return $null
  }
  if ($Payload -is [int] -or $Payload -is [long]) {
    if ([int64]$Payload -gt 0) {
      return [int]$Payload
    }
    return $null
  }
  $pidValue = $null
  try {
    $pidValue = $Payload.pid
  } catch {
    $pidValue = $null
  }
  if ($null -eq $pidValue) {
    return $null
  }
  try {
    $processId = [int]$pidValue
    if ($processId -gt 0) {
      return $processId
    }
  } catch {
    return $null
  }
  return $null
}

function Get-PidFromFile([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return $null
  }

  $raw = ""
  try {
    $raw = (Get-Content -LiteralPath $Path -Raw).Trim()
  } catch {
    Write-Warning "Could not read PID file ${Path}: $($_.Exception.Message)"
    return $null
  }

  if ([string]::IsNullOrWhiteSpace($raw)) {
    return $null
  }

  if ($raw.StartsWith("{")) {
    try {
      $payload = $raw | ConvertFrom-Json
      return Get-PidFromPayload $payload
    } catch {
      return $null
    }
  }

  try {
    $processId = [int]$raw.Split([Environment]::NewLine)[0].Trim()
    if ($processId -gt 0) {
      return $processId
    }
  } catch {
    return $null
  }
  return $null
}

function Get-PidFileCandidateMetadata([string]$PidFile) {
  $fileName = [System.IO.Path]::GetFileName($PidFile)

  switch -Exact ($fileName) {
    ".main_computer_service_supervisor.pid" {
      return [pscustomobject]@{ role = "supervisor"; order = 90 }
    }
    ".main_computer_main_log_service.pid" {
      return [pscustomobject]@{ role = "main-log"; order = 80 }
    }
    ".main_computer_executor_service.pid" {
      return [pscustomobject]@{ role = "executor"; order = 20 }
    }
    ".main_computer_applications_service.pid" {
      return [pscustomobject]@{ role = "applications"; order = 20 }
    }
    ".main_computer_viewport.pid" {
      return [pscustomobject]@{ role = "viewport"; order = 20 }
    }
    ".main_computer_heartbeat.pid" {
      return [pscustomobject]@{ role = "heartbeat"; order = 20 }
    }
    ".main_computer_dev_hub.pid" {
      return [pscustomobject]@{ role = "dev-hub"; order = 20 }
    }
    default {
      return [pscustomobject]@{ role = "pid-file"; order = 30 }
    }
  }
}

function Add-PidCandidate([hashtable]$Candidates, [object]$PidValue, [string]$Role, [string]$Source, [int]$Order, [bool]$AllowForeignMainComputer = $false) {
  if ($null -eq $PidValue) {
    return
  }
  try {
    $processId = [int]$PidValue
  } catch {
    return
  }
  if ($processId -le 0 -or $processId -eq $PID) {
    return
  }

  $key = [string]$processId
  if (-not $Candidates.ContainsKey($key)) {
    $Candidates[$key] = [ordered]@{
      pid = $processId
      role = $Role
      sources = @($Source)
      order = $Order
      allow_foreign_main_computer = $AllowForeignMainComputer
    }
    return
  }

  $existing = $Candidates[$key]
  $existing.sources = @($existing.sources + $Source | Select-Object -Unique)
  if ($AllowForeignMainComputer) {
    $existing.allow_foreign_main_computer = $true
  }

  # A duplicate PID can arrive from the generic managed_pid_files list before
  # the supervisor-specific state/session source. Keep supervisor ownership and
  # its later stop order so child services are stopped first.
  if ($Role -eq "supervisor") {
    $existing.role = $Role
    $existing.order = $Order
    return
  }
  if ([string]$existing.role -eq "supervisor") {
    return
  }

  if ($Order -lt [int]$existing.order) {
    $existing.order = $Order
    $existing.role = $Role
  }
}

function Add-PidsFromSupervisorState([hashtable]$Candidates, [object]$State) {
  if ($null -eq $State) {
    return
  }

  try {
    Add-PidCandidate $Candidates $State.service.pid "supervisor" "runtime/service_supervisor/state.json service.pid" 90
  } catch {}

  $children = $null
  try {
    $children = $State.children
  } catch {
    $children = $null
  }
  if ($null -eq $children) {
    return
  }

  foreach ($property in $children.PSObject.Properties) {
    $name = [string]$property.Name
    $child = $property.Value
    try {
      $order = $(if ($name -eq "main-log") { 80 } else { 20 })
      Add-PidCandidate $Candidates $child.pid $name ("runtime/service_supervisor/state.json children." + $name + ".pid") $order
    } catch {}
  }
}


function Get-MainComputerManagedPorts([object]$LaunchContext) {
  $ports = New-Object System.Collections.Generic.List[int]

  # Only inspect ports assigned to the launch context being started/stopped.
  # Debug and Safe installs must not kill an Unleashed/dev server just because
  # it also looks like Main Computer. Unleashed still owns 8765/8766 and may
  # replace a dev listener on those ports by design.
  foreach ($value in @(
      (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_CONTROL_PORT" "8765"),
      (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HEARTBEAT_PORT" "8766"),
      (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_MAIN_LOG_PORT" "8767"),
      (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_DOCKER_VIEWPORT_PORT" ""),
      (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_HUB_PORT" "8871")
    )) {
    try {
      $port = [int]$value
      if ($port -gt 0 -and -not $ports.Contains($port)) {
        $ports.Add($port)
      }
    } catch {}
  }

  try {
    $rootPath = ConvertTo-StringValue (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_INSTALL_ROOT" "") ""
    if (-not [string]::IsNullOrWhiteSpace($rootPath)) {
      foreach ($endpoint in @(Resolve-MainComputerDevHubEndpoints $rootPath $LaunchContext)) {
        try {
          $port = [int]$endpoint.port
          if ($port -gt 0 -and -not $ports.Contains($port)) {
            $ports.Add($port)
          }
        } catch {}
      }
    }
  } catch {}

  return @($ports)
}

function Test-MainComputerServiceCommandLine([string]$CommandLine, [string]$RootPath) {
  if ([string]::IsNullOrWhiteSpace($CommandLine)) {
    return $false
  }

  $normalizedCommand = $CommandLine.ToLowerInvariant()
  $normalizedRoot = $RootPath.ToLowerInvariant()
  $normalizedRootAlt = $normalizedRoot.Replace("\", "/")

  $hasRootMarker = (
    $normalizedCommand.Contains($normalizedRoot) -or
    $normalizedCommand.Contains($normalizedRootAlt)
  )

  $serviceMarkers = @(
    "main_computer.app_control",
    "main_computer.service_supervisor",
    "main_computer.main_log_service",
    "main_computer.executor_service",
    "main_computer.applications_service",
    "main_computer.blockchain_service",
    "main_computer.cli",
    "main_computer.exp_fdb_hub",
    "exp-fdb-hub.py"
  )

  $hasServiceMarker = $false
  foreach ($marker in $serviceMarkers) {
    if ($normalizedCommand.Contains($marker)) {
      $hasServiceMarker = $true
      break
    }
  }

  $hasPidFileMarker = $normalizedCommand.Contains(".main_computer_")

  return ($hasServiceMarker -or ($hasPidFileMarker -and $hasRootMarker))
}

function Get-NetstatListenRows {
  param([int[]]$Ports)

  $wantedPorts = @{}
  foreach ($port in $Ports) {
    if ($port -gt 0) {
      $wantedPorts[[string]$port] = $true
    }
  }

  if ($wantedPorts.Count -eq 0) {
    return @()
  }

  try {
    $lines = @(netstat -ano -p tcp 2>$null)
  } catch {
    return @()
  }

  $rows = @()
  foreach ($line in $lines) {
    if ($line -notmatch '^\s*TCP\s+(\S+)\s+\S+\s+LISTENING\s+(\d+)\s*$') {
      continue
    }

    $localEndpoint = [string]$matches[1]
    $pidText = [string]$matches[2]
    $portText = ""

    if ($localEndpoint -match '^\[(.*)\]:(\d+)$') {
      $portText = [string]$matches[2]
    } elseif ($localEndpoint -match '^(.*):(\d+)$') {
      $portText = [string]$matches[2]
    } else {
      continue
    }

    if (-not $wantedPorts.ContainsKey($portText)) {
      continue
    }

    try {
      $rows += [pscustomobject]@{
        LocalPort = [int]$portText
        OwningProcess = [int]$pidText
      }
    } catch {
      continue
    }
  }

  return $rows
}

function Add-CurrentMainComputerProcessCandidates([hashtable]$Candidates, [string]$RootPath, [object]$LaunchContext) {
  # start_v2/stop_v2 are intended to clear the app that is actually running now,
  # even when stale PID/session files no longer point at it.  Add live
  # Main Computer service processes and known app-port listeners as force-kill
  # candidates before launch or shutdown.
  try {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
  } catch {
    $processes = @()
  }

  foreach ($process in $processes) {
    $commandLine = [string]$process.CommandLine
    if ((Test-MainComputerServiceCommandLine $commandLine $RootPath) -and (Test-OwnedMainComputerPid ([int]$process.ProcessId) $RootPath $commandLine)) {
      $processName = [string]$process.Name
      if ($processName -match '^(python|pythonw)\.exe$') {
        Add-PidCandidate $Candidates $process.ProcessId "current-main-computer-service" "live Main Computer service command line for this tree" 10 $true
      }
    }
  }

  foreach ($row in @(Get-NetstatListenRows -Ports (Get-MainComputerManagedPorts $LaunchContext))) {
    $processId = [int]$row.OwningProcess
    if ($processId -le 0 -or $processId -eq $PID) {
      continue
    }

    $commandLine = Get-ProcessCommandLine $processId
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    $processName = ""
    if ($null -ne $process) {
      $processName = [string]$process.ProcessName
    }

    if ((Test-MainComputerServiceCommandLine $commandLine $RootPath) -or ([string]::IsNullOrWhiteSpace($commandLine) -and $processName -like "python*")) {
      Add-PidCandidate $Candidates $processId "current-main-computer-port-listener" ("listener on Main Computer port " + [string]$row.LocalPort) 5 $true
    }
  }
}

function Get-ProcessCommandLine([int]$ProcessId) {
  try {
    $proc = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $ProcessId) -ErrorAction Stop
    if ($null -ne $proc) {
      return [string]$proc.CommandLine
    }
  } catch {
    return $null
  }
  return $null
}

function Test-OwnedMainComputerPid([int]$ProcessId, [string]$RootPath, [string]$CommandLine) {
  if ([string]::IsNullOrWhiteSpace($CommandLine)) {
    return $true
  }

  $normalizedCommand = $CommandLine.ToLowerInvariant()
  $normalizedRoot = $RootPath.ToLowerInvariant()
  $normalizedRootAlt = $normalizedRoot.Replace("\", "/")

  $hasMainComputerMarker = (
    $normalizedCommand.Contains("main_computer") -or
    $normalizedCommand.Contains("main-computer") -or
    $normalizedCommand.Contains(".main_computer_")
  )
  $hasRootMarker = (
    $normalizedCommand.Contains($normalizedRoot) -or
    $normalizedCommand.Contains($normalizedRootAlt)
  )

  return ($hasMainComputerMarker -and $hasRootMarker)
}

function Test-ProcessStillRunning([int]$ProcessId) {
  return ($null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue))
}

function Wait-ProcessGone([int]$ProcessId, [int]$TimeoutSeconds = 5) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    if (-not (Test-ProcessStillRunning $ProcessId)) {
      return $true
    }
    Start-Sleep -Milliseconds 250
  } while ((Get-Date) -lt $deadline)

  return (-not (Test-ProcessStillRunning $ProcessId))
}

function Invoke-ProcessKill([int]$ProcessId) {
  $output = ""
  $exitCode = $null

  if (($IsWindows) -or ($env:OS -eq "Windows_NT")) {
    try {
      $output = (& taskkill.exe /PID $ProcessId /T /F 2>&1 | Out-String).Trim()
      $exitCode = $LASTEXITCODE
    } catch {
      $output = $_.Exception.Message
      $exitCode = 1
    }
  } else {
    try {
      Stop-Process -Id $ProcessId -Force -ErrorAction Stop
      $output = "Stop-Process succeeded"
      $exitCode = 0
    } catch {
      $output = $_.Exception.Message
      $exitCode = 1
    }
  }

  return [ordered]@{
    exit_code = $exitCode
    output = $output
  }
}

function Stop-OnePid([object]$Candidate, [string]$RootPath) {
  $processId = [int]$Candidate.pid
  $role = [string]$Candidate.role
  $sourceText = ([string[]]$Candidate.sources) -join "; "

  $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
  if ($null -eq $process) {
    return [ordered]@{
      pid = $processId
      role = $role
      state = "not-running"
      sources = $Candidate.sources
    }
  }

  $commandLine = Get-ProcessCommandLine $processId
  $allowForeignMainComputer = $false
  try {
    $allowForeignMainComputer = [bool]$Candidate.allow_foreign_main_computer
  } catch {
    $allowForeignMainComputer = $false
  }

  if (-not $allowForeignMainComputer -and -not (Test-OwnedMainComputerPid $processId $RootPath $commandLine)) {
    Write-Warning "Skipping PID $processId for ${role}; it no longer looks like this Main Computer tree. Source: $sourceText"
    return [ordered]@{
      pid = $processId
      role = $role
      state = "skipped-not-owned"
      sources = $Candidate.sources
      command_line = $commandLine
    }
  }

  $attempts = @()
  $lastResult = $null

  for ($attempt = 1; $attempt -le 2; $attempt++) {
    $lastResult = Invoke-ProcessKill $processId
    $gone = Wait-ProcessGone $processId 5
    $attempts += [ordered]@{
      attempt = $attempt
      exit_code = $lastResult.exit_code
      output = $lastResult.output
      process_gone_after_attempt = $gone
    }

    if ($gone) {
      return [ordered]@{
        pid = $processId
        role = $role
        state = $(if ($lastResult.exit_code -eq 0) { "terminated" } else { "terminated-after-check" })
        exit_code = $lastResult.exit_code
        sources = $Candidate.sources
        command_line = $commandLine
        allow_foreign_main_computer = $allowForeignMainComputer
        output = $lastResult.output
        attempts = $attempts
      }
    }

    if ($attempt -lt 2) {
      Start-Sleep -Seconds 1
    }
  }

  return [ordered]@{
    pid = $processId
    role = $role
    state = "termination-attempted"
    exit_code = $lastResult.exit_code
    sources = $Candidate.sources
    command_line = $commandLine
    allow_foreign_main_computer = $allowForeignMainComputer
    output = $lastResult.output
    attempts = $attempts
  }
}

function Remove-ManagedRuntimeFiles([string]$RootPath, [object]$Session, [string]$ControlRoot = "") {
  $paths = New-Object System.Collections.Generic.List[string]

  foreach ($relative in @(
      ".main_computer_service_supervisor.pid",
      ".main_computer_main_log_service.pid",
      ".main_computer_viewport.pid",
      ".main_computer_heartbeat.pid",
      ".main_computer_executor_service.pid",
      ".main_computer_applications_service.pid",
      ".main_computer_dev_hub.pid"
    )) {
    $paths.Add((Join-Path $RootPath $relative))
  }

  if (-not [string]::IsNullOrWhiteSpace($ControlRoot) -and $ControlRoot -ne $RootPath) {
    foreach ($relative in @(
        ".main_computer_viewport.pid",
        ".main_computer_heartbeat.pid"
      )) {
      $paths.Add((Join-Path $ControlRoot $relative))
    }
  }

  if ($null -ne $Session) {
    try {
      foreach ($path in $Session.managed_pid_files) {
        if (-not [string]::IsNullOrWhiteSpace([string]$path)) {
          $paths.Add([string]$path)
        }
      }
    } catch {}
  }

  $removed = @()
  foreach ($path in ($paths | Select-Object -Unique)) {
    try {
      if (Test-Path -LiteralPath $path -PathType Leaf) {
        Remove-Item -LiteralPath $path -Force
        $removed += $path
      }
    } catch {
      Write-Warning "Could not remove runtime PID file ${path}: $($_.Exception.Message)"
    }
  }
  return $removed
}

function Get-DockerStacks([string]$RootPath, [object]$Session) {
  $stacks = @()
  if ($null -ne $Session) {
    try {
      foreach ($stack in $Session.docker_stacks) {
        $stacks += $stack
      }
    } catch {}
  }

  if ($stacks.Count -gt 0) {
    return $stacks
  }

  $applicationsProject = Get-SafeDockerName `
    (Get-EnvFirstValue @("MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT") "main-computer-applications") `
    "main-computer-applications"

  $devComposeProject = Get-SafeDockerName `
    (Get-EnvFirstValue @("MAIN_COMPUTER_DEV_COMPOSE_PROJECT", "MAIN_COMPUTER_EXECUTOR_COMPOSE_PROJECT") "main-computer-unleashed") `
    "main-computer-unleashed"

  $localPlatformProject = Get-SafeDockerName `
    (Get-EnvFirstValue @("MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT") "main-computer-local-platform-unleashed") `
    "main-computer-local-platform-unleashed"
  $localPlatformCompose = Get-EnvFirstValue @("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH") ""

  return @(
    [pscustomobject]@{
      name = "executor-and-blockchain-unleashed"
      docker_command = "docker"
      compose_file = Join-Path $RootPath "docker-compose.dev.yml"
      project_name = $devComposeProject
      env_file = $null
    },
    [pscustomobject]@{
      name = "local-platform"
      docker_command = "docker"
      compose_file = $localPlatformCompose
      project_name = $localPlatformProject
      env_file = $null
    },
    [pscustomobject]@{
      name = "applications"
      docker_command = "docker"
      compose_file = Join-Path $RootPath "docker-compose.applications.yml"
      project_name = $applicationsProject
      env_file = Join-Path $RootPath "runtime\applications_service\applications.env"
    }
  )
}

function Invoke-DockerCommand([string]$DockerCommand, [string[]]$Arguments) {
  $output = ""
  $exitCode = $null
  try {
    $output = (& $DockerCommand @Arguments 2>&1 | Out-String).Trim()
    $exitCode = $LASTEXITCODE
  } catch {
    $output = $_.Exception.Message
    $exitCode = 1
  }

  return [ordered]@{
    exit_code = $exitCode
    output = $output
  }
}

function Get-DockerComposeBaseArgs([object]$Stack) {
  $args = @("compose")

  $projectName = [string]$Stack.project_name
  if (-not [string]::IsNullOrWhiteSpace($projectName)) {
    $args += @("--project-name", $projectName)
  }

  $envFile = [string]$Stack.env_file
  if (-not [string]::IsNullOrWhiteSpace($envFile) -and (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    $args += @("--env-file", $envFile)
  }

  $args += @("-f", [string]$Stack.compose_file)
  return $args
}

function Test-DockerComposeStackGone([string]$DockerCommand, [string[]]$BaseArgs) {
  $psArgs = @($BaseArgs + @("ps", "-a", "-q"))
  $result = Invoke-DockerCommand $DockerCommand $psArgs
  $containerIds = @()
  if (-not [string]::IsNullOrWhiteSpace([string]$result.output)) {
    $containerIds = @(
      ([string]$result.output -split "\r?\n") |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -match "^[0-9a-fA-F]{12,64}$" }
    )
  }

  return [ordered]@{
    state = $(if (($result.exit_code -eq 0) -and ($containerIds.Count -eq 0)) { "gone" } else { "present" })
    exit_code = $result.exit_code
    output = $result.output
    container_ids = $containerIds
  }
}

function Wait-DockerComposeStackGone([string]$DockerCommand, [string[]]$BaseArgs, [int]$TimeoutSeconds) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $lastCheck = $null

  do {
    $lastCheck = Test-DockerComposeStackGone $DockerCommand $BaseArgs
    if ([string]$lastCheck.state -eq "gone") {
      return [ordered]@{
        state = "gone"
        timeout_seconds = $TimeoutSeconds
        last_check = $lastCheck
      }
    }

    Start-Sleep -Seconds 2
  } while ((Get-Date) -lt $deadline)

  return [ordered]@{
    state = "containers-remain"
    timeout_seconds = $TimeoutSeconds
    last_check = $lastCheck
  }
}

function Invoke-DockerComposeDown([object]$Stack) {
  $name = [string]$Stack.name
  $dockerCommand = [string]$Stack.docker_command
  if ([string]::IsNullOrWhiteSpace($dockerCommand)) {
    $dockerCommand = "docker"
  }

  $composeFile = [string]$Stack.compose_file
  if ([string]::IsNullOrWhiteSpace($composeFile) -or -not (Test-Path -LiteralPath $composeFile -PathType Leaf)) {
    return [ordered]@{
      name = $name
      state = "skipped-missing-compose-file"
      compose_file = $composeFile
    }
  }

  $baseArgs = Get-DockerComposeBaseArgs $Stack
  $downArgs = @($baseArgs + @("down", "--remove-orphans", "--timeout", "30"))
  $attempts = @()
  $lastWait = $null

  for ($attempt = 1; $attempt -le 3; $attempt++) {
    $result = Invoke-DockerCommand $dockerCommand $downArgs
    $attempts += [ordered]@{
      attempt = $attempt
      exit_code = $result.exit_code
      output = $result.output
    }

    $waitSeconds = $(if ($result.exit_code -eq 0) { 60 } else { 20 })
    $lastWait = Wait-DockerComposeStackGone $dockerCommand $baseArgs $waitSeconds
    if ([string]$lastWait.state -eq "gone") {
      return [ordered]@{
        name = $name
        state = $(if ($result.exit_code -eq 0) { "down" } else { "down-after-wait" })
        docker_command = $dockerCommand
        arguments = $downArgs
        exit_code = $result.exit_code
        attempts = $attempts
        final_check = $lastWait
      }
    }

    if ($attempt -lt 3) {
      Start-Sleep -Seconds 3
    }
  }

  return [ordered]@{
    name = $name
    state = "down-failed"
    docker_command = $dockerCommand
    arguments = $downArgs
    exit_code = $attempts[-1].exit_code
    attempts = $attempts
    final_check = $lastWait
  }
}

function Stop-MainComputer([string]$RootPath, [bool]$SkipDocker = $false) {
  $callerContainerRuntimeOverride = Get-MainComputerCallerContainerRuntimeOverride
  $runtime = Get-StartStopRuntime $RootPath
  Ensure-Directory $runtime

  $launchContext = Resolve-MainComputerLaunchContext $RootPath
  Set-MainComputerLaunchEnvironment $launchContext
  Restore-MainComputerCallerContainerRuntimeOverride $launchContext $callerContainerRuntimeOverride
  $controlRoot = Get-ControlRoot $RootPath $launchContext

  $stopOnlyOfficeOnStop = Get-LaunchEnvironmentValue $launchContext "MAIN_COMPUTER_ONLYOFFICE_STOP_ON_STOP" "0"
  if ($stopOnlyOfficeOnStop -eq "1" -or $stopOnlyOfficeOnStop -eq "true" -or $stopOnlyOfficeOnStop -eq "yes") {
    Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "stop"
  } else {
    Write-Host "Leaving ONLYOFFICE Docker container running for faster next startup."
    Write-Host "Set MAIN_COMPUTER_ONLYOFFICE_STOP_ON_STOP=1 or run tools\onlyoffice\onlyoffice-control.ps1 stop to stop it."
  }

  $sessionPath = Get-StartSessionPath $RootPath
  $session = Read-JsonFile $sessionPath
  $candidates = @{}

  if ($null -ne $session) {
    try {
      Add-PidCandidate $candidates $session.launcher.pid "supervisor" "runtime/start_stop/start-session.json launcher.pid" 90
    } catch {}
    try {
      foreach ($pidFile in $session.managed_pid_files) {
        $metadata = Get-PidFileCandidateMetadata ([string]$pidFile)
        Add-PidCandidate $candidates (Get-PidFromFile ([string]$pidFile)) $metadata.role ([string]$pidFile) $metadata.order
      }
    } catch {}
  }

  foreach ($relative in @(
      ".main_computer_service_supervisor.pid",
      ".main_computer_main_log_service.pid",
      ".main_computer_viewport.pid",
      ".main_computer_heartbeat.pid",
      ".main_computer_executor_service.pid",
      ".main_computer_applications_service.pid",
      ".main_computer_dev_hub.pid"
    )) {
    $path = Join-Path $RootPath $relative
    $metadata = Get-PidFileCandidateMetadata $path
    Add-PidCandidate $candidates (Get-PidFromFile $path) $metadata.role $path $metadata.order
  }

  if (-not [string]::IsNullOrWhiteSpace($controlRoot) -and $controlRoot -ne $RootPath) {
    foreach ($relative in @(
        ".main_computer_viewport.pid",
        ".main_computer_heartbeat.pid"
      )) {
      $path = Join-Path $controlRoot $relative
      $metadata = Get-PidFileCandidateMetadata $path
      Add-PidCandidate $candidates (Get-PidFromFile $path) $metadata.role $path $metadata.order
    }
  }

  Add-PidsFromSupervisorState $candidates (Read-JsonFile (Join-Path $RootPath "runtime\service_supervisor\state.json"))
  Add-CurrentMainComputerProcessCandidates $candidates $RootPath $launchContext

  $processResults = @()
  foreach ($candidate in ($candidates.Values | Sort-Object -Property @{ Expression = { [int]$_['order'] } }, @{ Expression = { [int]$_['pid'] } })) {
    $processResults += Stop-OnePid $candidate $RootPath
  }

  Start-Sleep -Milliseconds 500

  $dockerResults = @()
  $skipDockerStacks = ($SkipDocker -or [bool]$NoDocker)
  if ($skipDockerStacks) {
    $dockerResults += [ordered]@{
      state = "skipped"
      reason = $(if ($SkipDocker) { "Docker stacks are left alone for app-only stop" } else { "NoDocker was requested" })
    }
  } else {
    foreach ($stack in Get-DockerStacks $RootPath $session) {
      Write-Host ("Stopping Docker Compose stack: " + [string]$stack.name)
      $dockerResults += Invoke-DockerComposeDown $stack
    }
  }

  $removedPidFiles = Remove-ManagedRuntimeFiles $RootPath $session $controlRoot

  $report = [ordered]@{
    schema_version = 1
    action = "stop"
    stopped_at = Get-UtcNowText
    root = $RootPath
    session_path = $sessionPath
    had_start_session = ($null -ne $session)
    process_results = $processResults
    docker_results = $dockerResults
    removed_pid_files = $removedPidFiles
  }

  $reportPath = Join-Path $runtime ("stop-report-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
  Write-JsonFile $reportPath $report

  Write-Host ""
  Write-Host "Main Computer stop completed."
  Write-Host ("Stop report: " + $reportPath)
  return $report
}

$resolvedRoot = Resolve-MainComputerRoot $Root

switch ($Action) {
  "start" {
    Start-MainComputer $resolvedRoot $StartedBy ([bool]$NoDevHub)
  }
  "dev-hub-start" {
    Start-MainComputerDevHubOnly $resolvedRoot $StartedBy
  }
  "status" {
    exit (Show-MainComputerStatus $resolvedRoot)
  }
  "stop" {
    Stop-MainComputer $resolvedRoot ([bool]$NoDocker) | Out-Null
  }
}
