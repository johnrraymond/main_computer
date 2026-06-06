[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("start", "stop", "status")]
  [string]$Action,

  [string]$Root = (Get-Location).Path,

  [string]$StartedBy = "",

  [switch]$NoDocker
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

  $onlyOfficeWslDistro = $env:MAIN_COMPUTER_ONLYOFFICE_WSL_DISTRO
  if ([string]::IsNullOrWhiteSpace($onlyOfficeWslDistro)) {
    $onlyOfficeWslDistro = "Ubuntu"
  }

  switch ($normalized) {
    "safe" {
      $label = "Safe Mode"
      $guidance = "guided"
      $safe = "1"
      $port = "38865"
      $heartbeat = "38866"
    }
    "debug" {
      $label = "Debug"
      $guidance = "debug"
      $safe = "0"
      $port = "28865"
      $heartbeat = "28866"
    }
    default {
      $normalized = "unleashed"
      $label = "Unleashed Mode"
      $guidance = "developer"
      $safe = "0"
      $port = "8765"
      $heartbeat = "8766"
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
    MAIN_COMPUTER_PATH_MODE = "local"
    MAIN_COMPUTER_HOST_OS = "windows"
    MAIN_COMPUTER_GITEA_SCOPE = "shared-machine"
    MAIN_COMPUTER_GITEA_ROOT_URL = "http://127.0.0.1:3000/"
    MAIN_COMPUTER_GITEA_HTTP_PORT = "3000"
    MAIN_COMPUTER_GITEA_COMPOSE_PROJECT = "main-computer-gitea"
    MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT = "main-computer-applications"
    MAIN_COMPUTER_ONLYOFFICE_ENABLED = "1"
    MAIN_COMPUTER_ONLYOFFICE_MODE = "wsl"
    MAIN_COMPUTER_ONLYOFFICE_PORT = "18084"
    MAIN_COMPUTER_ONLYOFFICE_PROJECT = "main-computer-onlyoffice"
    MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL = "http://127.0.0.1:18084"
    MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL = "http://127.0.0.1:18084"
    MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET = "main-computer-onlyoffice-local-secret"
    MAIN_COMPUTER_ONLYOFFICE_WSL_DISTRO = $onlyOfficeWslDistro
    MAIN_COMPUTER_DEV_COMPOSE_PROJECT = "main-computer-unleashed"
    MAIN_COMPUTER_EXECUTOR_COMPOSE_PROJECT = "main-computer-unleashed"
    MAIN_COMPUTER_DOCKER_VIEWPORT_PORT = "18765"
    MAIN_COMPUTER_HUB_PORT = "8770"
    MAIN_COMPUTER_HUB_WORKER_PORT = "8771"
    MAIN_COMPUTER_HUB_URL = "http://127.0.0.1:8770"
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

function Start-MainComputerGiteaIfMissing([string]$RootPath, [object]$LaunchContext) {
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

  $docker = Get-Command "docker" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $docker) {
    return [ordered]@{
      ok = $false
      state = "missing-docker"
      installed = $false
      compose_project = $projectName
      compose_file = $composePath
      port = $giteaPort
      message = "Docker CLI is not available; shared Gitea cannot be prepared."
    }
  }

  $containerIds = @(& $docker.Source compose --project-name $projectName -f $composePath ps -a -q gitea 2>$null)
  $containerExists = ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace(($containerIds -join "").Trim()))

  if ($containerExists) {
    Write-Host ("Shared Gitea container already exists but is not reachable on port {0}; starting existing container without reinstalling." -f $giteaPort)
    $arguments = @("compose", "--project-name", $projectName, "-f", $composePath, "start", "gitea")
    $stateOnSuccess = "started-existing"
    $stateOnFailure = "start-existing-failed"
    $installed = $false
  } else {
    Write-Host ("Shared Gitea not found on this machine; installing machine-wide Gitea with docker-compose.gitea.yml.")
    $arguments = @("compose", "--project-name", $projectName, "-f", $composePath, "up", "-d", "gitea")
    $stateOnSuccess = "installed-missing"
    $stateOnFailure = "install-missing-failed"
    $installed = $true
  }

  & $docker.Source @arguments
  $exitCode = $LASTEXITCODE
  return [ordered]@{
    ok = ($exitCode -eq 0)
    state = $(if ($exitCode -eq 0) { $stateOnSuccess } else { $stateOnFailure })
    installed = ($installed -and $exitCode -eq 0)
    container_exists = $containerExists
    compose_project = $projectName
    compose_file = $composePath
    port = $giteaPort
    command = @($docker.Source) + $arguments
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
function New-StartSession(
  [string]$RootPath,
  [object]$LaunchContext,
  [int]$LauncherPid,
  [string]$StdoutPath,
  [string]$StderrPath,
  [string[]]$LauncherArgs,
  [string]$StartedByName,
  [object]$GiteaStart,
  [object]$LocalPlatformStart
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
    managed_pid_files = @(
      (Join-Path $RootPath ".main_computer_service_supervisor.pid"),
      (Join-Path $controlRoot ".main_computer_viewport.pid"),
      (Join-Path $controlRoot ".main_computer_heartbeat.pid"),
      (Join-Path $RootPath ".main_computer_executor_service.pid"),
      (Join-Path $RootPath ".main_computer_applications_service.pid")
    )
    managed_state_files = @(
      (Join-Path $RootPath "runtime\service_supervisor\state.json"),
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
    Write-Host "ONLYOFFICE startup bridge management skipped because MAIN_COMPUTER_ONLYOFFICE_ENABLED=$enabled."
    return
  }

  $mode = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_MODE" "wsl"
  if ([string]::IsNullOrWhiteSpace($mode) -or $mode -eq "wsl-native") {
    $mode = "wsl"
  }
  if ($mode -eq "disabled") {
    Write-Host "ONLYOFFICE startup bridge management skipped because MAIN_COMPUTER_ONLYOFFICE_MODE=disabled."
    return
  }

  $onlyOfficePortText = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_PORT" "18084"
  $appPortText = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_CONTROL_PORT" "8765"
  $projectName = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_PROJECT" "main-computer-onlyoffice"
  $jwtSecret = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET" "main-computer-onlyoffice-local-secret"
  $distro = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_WSL_DISTRO" "Ubuntu"
  if ([string]::IsNullOrWhiteSpace($distro)) {
    $distro = "Ubuntu"
  }

  $onlyOfficePort = 18084
  $appPort = 8765
  try { $onlyOfficePort = [int]$onlyOfficePortText } catch { $onlyOfficePort = 18084 }
  try { $appPort = [int]$appPortText } catch { $appPort = 8765 }

  $controlScript = Join-Path $RootPath "tools\onlyoffice\onlyoffice-control.ps1"
  if (-not (Test-Path -LiteralPath $controlScript -PathType Leaf)) {
    Write-Warning "ONLYOFFICE control script not found: $controlScript"
    return
  }

  Write-Host ""
  Write-Host ("ONLYOFFICE startup control: {0} mode={1} port={2} appPort={3} distro={4}" -f $OnlyOfficeAction, $mode, $onlyOfficePort, $appPort, $distro)
  try {
    & powershell -NoProfile -ExecutionPolicy Bypass `
      -File $controlScript `
      $OnlyOfficeAction `
      -Mode $mode `
      -Port $onlyOfficePort `
      -AppPort $appPort `
      -Distro $distro `
      -ProjectName $projectName `
      -JwtSecret $jwtSecret

    if ($LASTEXITCODE -ne 0) {
      if ($OnlyOfficeAction -eq "bridge-status") {
        Write-Host "ONLYOFFICE bridge-status reported not-ready diagnostics; see output above. Exit code: $LASTEXITCODE"
      } else {
        Write-Warning "ONLYOFFICE control action '$OnlyOfficeAction' returned exit code $LASTEXITCODE."
      }
    }
  } catch {
    Write-Warning "ONLYOFFICE control action '$OnlyOfficeAction' failed: $($_.Exception.Message)"
  }
}

function Start-MainComputer([string]$RootPath, [string]$StartedByName) {
  Write-Host "Force-stopping current Main Computer app processes before launch; Docker stacks are left alone..."
  Stop-MainComputer $RootPath $true | Out-Null

  $launchContext = Resolve-MainComputerLaunchContext $RootPath
  Set-MainComputerLaunchEnvironment $launchContext
  $controlRoot = Get-ControlRoot $RootPath $launchContext

  $serviceRuntime = Join-Path $RootPath "runtime\service_supervisor"
  Ensure-Directory $serviceRuntime
  Ensure-Directory (Get-StartStopRuntime $RootPath)

  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $stdout = Join-Path $serviceRuntime ("service_supervisor-" + $stamp + ".stdout.log")
  $stderr = Join-Path $serviceRuntime ("service_supervisor-" + $stamp + ".stderr.log")
  $pythonCommand = [string]$launchContext.python
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
    $giteaStart = Start-MainComputerGiteaIfMissing $RootPath $launchContext
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

  $session = New-StartSession $RootPath $launchContext $process.Id $stdout $stderr $launcherArgs $StartedByName $giteaStart $localPlatformStart
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
  Write-Host ("App PID:          " + (Join-Path $controlRoot ".main_computer_viewport.pid"))
  Write-Host ("Heartbeat PID:    " + (Join-Path $controlRoot ".main_computer_heartbeat.pid"))
  Write-Host ("Executor state:   " + (Join-Path $RootPath "runtime\executor_service\state.json"))
  Write-Host ("Applications:     " + (Join-Path $RootPath "runtime\applications_service\state.json"))
  Write-Host ("Blockchain:       " + (Join-Path $RootPath "runtime\blockchain_service\state.json"))
  Write-Host ("stdout:           " + $stdout)
  Write-Host ("stderr:           " + $stderr)
}

function Show-MainComputerStatus([string]$RootPath) {
  $launchContext = Resolve-MainComputerLaunchContext $RootPath
  Set-MainComputerLaunchEnvironment $launchContext

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
  Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "bridge-status"
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
      Add-PidCandidate $Candidates $child.pid $name ("runtime/service_supervisor/state.json children." + $name + ".pid") 20
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
      (Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_DOCKER_VIEWPORT_PORT" "")
    )) {
    try {
      $port = [int]$value
      if ($port -gt 0 -and -not $ports.Contains($port)) {
        $ports.Add($port)
      }
    } catch {}
  }

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
    "main_computer.executor_service",
    "main_computer.applications_service",
    "main_computer.blockchain_service",
    "main_computer.cli"
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
      ".main_computer_viewport.pid",
      ".main_computer_heartbeat.pid",
      ".main_computer_executor_service.pid",
      ".main_computer_applications_service.pid"
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
  $runtime = Get-StartStopRuntime $RootPath
  Ensure-Directory $runtime

  $launchContext = Resolve-MainComputerLaunchContext $RootPath
  Set-MainComputerLaunchEnvironment $launchContext
  $controlRoot = Get-ControlRoot $RootPath $launchContext

  $removeOnlyOfficeBridgesOnStop = Get-LaunchEnvironmentValue $launchContext "MAIN_COMPUTER_ONLYOFFICE_REMOVE_BRIDGES_ON_STOP" "0"
  if ($removeOnlyOfficeBridgesOnStop -eq "1" -or $removeOnlyOfficeBridgesOnStop -eq "true" -or $removeOnlyOfficeBridgesOnStop -eq "yes") {
    Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "bridge-stop"
  } else {
    Write-Host "Leaving ONLYOFFICE WSL bridge portproxies installed so the next start_v2.bat does not require elevation."
    Write-Host "Set MAIN_COMPUTER_ONLYOFFICE_REMOVE_BRIDGES_ON_STOP=1 or run tools\onlyoffice\onlyoffice-control.ps1 bridge-stop to remove them."
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
      ".main_computer_viewport.pid",
      ".main_computer_heartbeat.pid",
      ".main_computer_executor_service.pid",
      ".main_computer_applications_service.pid"
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
    Start-MainComputer $resolvedRoot $StartedBy
  }
  "status" {
    exit (Show-MainComputerStatus $resolvedRoot)
  }
  "stop" {
    Stop-MainComputer $resolvedRoot ([bool]$NoDocker) | Out-Null
  }
}
