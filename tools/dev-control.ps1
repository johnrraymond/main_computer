param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("status", "start", "shutdown", "restart", "doctor", "setup-renderer")]
  [string]$Action,

  [ValidateSet("", "local", "docker")]
  [string]$Mode = "",

  [switch]$Polite,
  [switch]$EnsureRenderer,

  [string]$Workspace = "",
  [string]$PythonPath = "",
  [string]$BindHost = "0.0.0.0",
  [int]$LocalPort = 8765,
  [int]$HeartbeatPort = 8766,
  [string]$ControlRoot = "",
  [int]$DockerHostPort = 18765,
  [int]$StartTimeoutSeconds = 45,
  [int]$MathicsApiTimeoutSeconds = 30,
  [switch]$SkipMathicsCheck,
  [switch]$AllowForeignPortListener,
  [string]$DockerComposeFile = "docker-compose.dev.yml",
  [string]$DockerService = "main-computer"
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Workspace) {
  $Workspace = $scriptRoot
}

if (-not $PSBoundParameters.ContainsKey("DockerHostPort") -and $env:MAIN_COMPUTER_DOCKER_VIEWPORT_PORT) {
  try {
    $DockerHostPort = [int]$env:MAIN_COMPUTER_DOCKER_VIEWPORT_PORT
  } catch {
    throw "MAIN_COMPUTER_DOCKER_VIEWPORT_PORT must be an integer; got '$env:MAIN_COMPUTER_DOCKER_VIEWPORT_PORT'."
  }
}

$controlScript = Join-Path $scriptRoot "control-main-computer.ps1"
$composePath = Join-Path $scriptRoot $DockerComposeFile
$localOpenHost = if ($BindHost -eq "0.0.0.0" -or $BindHost -eq "::") { "127.0.0.1" } else { $BindHost }
$localUrl = "http://${localOpenHost}:${LocalPort}"
$dockerUrl = "http://127.0.0.1:${DockerHostPort}"
if ([string]::IsNullOrWhiteSpace($ControlRoot)) {
  $localPidFile = Join-Path $scriptRoot ".main_computer_viewport.pid"
} else {
  $ControlRoot = [System.IO.Path]::GetFullPath($ControlRoot)
  New-Item -ItemType Directory -Force -Path $ControlRoot | Out-Null
  $localPidFile = Join-Path $ControlRoot ".main_computer_viewport.pid"
}

function Write-Section {
  param([string]$Title)
  Write-Host ""
  Write-Host $Title
  Write-Host ("-" * $Title.Length)
}

function Resolve-MainComputerPython {
  $candidates = New-Object System.Collections.Generic.List[string]

  if ($PythonPath) {
    $candidates.Add($PythonPath)
  }

  if ($env:MAIN_COMPUTER_PYTHON) {
    $candidates.Add($env:MAIN_COMPUTER_PYTHON)
  }

  if ($env:VIRTUAL_ENV) {
    $candidates.Add((Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"))
  }

  $parentRoot = Split-Path -Parent $scriptRoot
  $userDslRoot = Join-Path ([Environment]::GetFolderPath("UserProfile")) "dsl"
  $candidates.Add((Join-Path $parentRoot ".venv\Scripts\python.exe"))
  $candidates.Add((Join-Path $userDslRoot ".venv\Scripts\python.exe"))
  $candidates.Add((Join-Path $scriptRoot ".venv\Scripts\python.exe"))

  $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($pythonCommand -and $pythonCommand.Source -and ($pythonCommand.Source -notlike "*\WindowsApps\*")) {
    $candidates.Add($pythonCommand.Source)
  }

  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }

  throw "Could not find the intended Python. Expected a .venv under the repo parent, user dsl folder, or script root. Pass -PythonPath to be explicit."
}

function Test-CommandLineHasPort {
  param(
    [string]$CommandLine,
    [int]$Port
  )

  if (-not $CommandLine) {
    return $false
  }

  $portPattern = [regex]::Escape([string]$Port)
  return ($CommandLine -match "(^|\s|`")--port(`"|\s|=)+$portPattern(\s|`"|$)")
}

function Get-SafeProcessById {
  param([int]$TargetProcessId)

  if ($TargetProcessId -le 0) {
    return $null
  }

  try {
    $process = Get-Process -Id $TargetProcessId -ErrorAction Stop
  } catch {
    return $null
  }

  $processPath = ""
  try {
    if ($process.Path) {
      $processPath = [string]$process.Path
    }
  } catch {
    $processPath = ""
  }

  [pscustomobject]@{
    ProcessId = [int]$process.Id
    Name = [string]$process.ProcessName
    ExecutablePath = $processPath
    CommandLine = ""
  }
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
    $localAddress = ""
    $portText = ""

    if ($localEndpoint -match '^\[(.*)\]:(\d+)$') {
      $localAddress = [string]$matches[1]
      $portText = [string]$matches[2]
    } elseif ($localEndpoint -match '^(.*):(\d+)$') {
      $localAddress = [string]$matches[1]
      $portText = [string]$matches[2]
    } else {
      continue
    }

    if (-not $wantedPorts.ContainsKey($portText)) {
      continue
    }

    try {
      $rows += [pscustomobject]@{
        LocalAddress = $localAddress
        LocalPort = [int]$portText
        OwningProcess = [int]$pidText
      }
    } catch {
      continue
    }
  }

  return $rows
}

function Get-ProcessIdsListeningOnPort {
  param([int]$Port)

  @(Get-NetstatListenRows -Ports @($Port) |
    Select-Object -ExpandProperty OwningProcess -Unique)
}

function Test-ProcessLooksLikePython {
  param([object]$Process)

  if (-not $Process) {
    return $false
  }

  $name = [string]$Process.Name
  $path = [string]$Process.ExecutablePath
  return ($name -like "python*" -or $path -like "*\python*.exe")
}

function Add-LocalViewportMetadata {
  param(
    [Parameter(Mandatory = $true)]
    [object]$Process,
    [string]$ExpectedPython = "",
    [string]$Source = ""
  )

  $exe = [string]$Process.ExecutablePath
  $isExpectedPython = $false
  if ($ExpectedPython -and $exe) {
    $resolvedExe = $null
    try {
      $resolvedExe = (Resolve-Path -LiteralPath $exe -ErrorAction Stop).Path
    } catch {
      $resolvedExe = $exe
    }
    $isExpectedPython = ($resolvedExe -eq $ExpectedPython)
  }

  if (-not $Process.CommandLine) {
    $Process.CommandLine = "unavailable without CIM/WMI; identified by $Source"
  }
  $Process | Add-Member -NotePropertyName ExpectedPython -NotePropertyValue $ExpectedPython -Force
  $Process | Add-Member -NotePropertyName IsExpectedPython -NotePropertyValue $isExpectedPython -Force
  $Process | Add-Member -NotePropertyName DetectionSource -NotePropertyValue $Source -Force
  return $Process
}

function Get-LocalViewportProcesses {
  $expectedPython = $null
  try {
    $expectedPython = Resolve-MainComputerPython
  } catch {
    $expectedPython = $null
  }

  $processes = @()

  if (Test-Path -LiteralPath $localPidFile) {
    try {
      $viewportPid = [int](Get-Content -LiteralPath $localPidFile -Raw).Trim()
      $process = Get-SafeProcessById -TargetProcessId $viewportPid
      if ($process) {
        $processes += (Add-LocalViewportMetadata -Process $process -ExpectedPython $expectedPython -Source ".main_computer_viewport.pid")
      } else {
        Remove-Item -LiteralPath $localPidFile -ErrorAction SilentlyContinue
      }
    } catch {
      Remove-Item -LiteralPath $localPidFile -ErrorAction SilentlyContinue
    }
  }

  foreach ($listenerPid in @(Get-ProcessIdsListeningOnPort -Port $LocalPort)) {
    $process = Get-SafeProcessById -TargetProcessId ([int]$listenerPid)
    if ($process -and (Test-ProcessLooksLikePython -Process $process)) {
      $processes += (Add-LocalViewportMetadata -Process $process -ExpectedPython $expectedPython -Source "listener on port $LocalPort")
    }
  }

  $processes |
    Where-Object { $_ -and $_.ProcessId } |
    Sort-Object ProcessId -Unique
}

function Get-PortListenerDiagnostics {
  param([int[]]$Ports)

  $listeners = @()
  $connections = @(Get-NetstatListenRows -Ports $Ports)

  foreach ($connection in $connections) {
    $process = Get-SafeProcessById -TargetProcessId ([int]$connection.OwningProcess)

    $processName = ""
    $processPath = ""
    $processCommandLine = ""
    if ($process) {
      $processName = [string]$process.Name
      $processPath = [string]$process.ExecutablePath
      $processCommandLine = [string]$process.CommandLine
    }

    $listeners += [pscustomobject]@{
      LocalAddress = $connection.LocalAddress
      LocalPort = $connection.LocalPort
      ProcessId = $connection.OwningProcess
      Name = $processName
      ExecutablePath = $processPath
      CommandLine = $processCommandLine
    }
  }

  $listeners | Sort-Object LocalPort, LocalAddress, ProcessId -Unique
}


function Invoke-DockerRaw {
  param([string[]]$Arguments)

  $docker = Get-Command docker -ErrorAction SilentlyContinue
  if (-not $docker) {
    throw "Docker CLI was not found on PATH."
  }

  & docker @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
  }
}

function Invoke-DockerCompose {
  param([string[]]$Arguments)

  if (-not (Test-Path -LiteralPath $composePath)) {
    throw "Docker Compose file was not found: $composePath"
  }

  Invoke-DockerRaw -Arguments (@("compose", "-f", $composePath, "--profile", "app") + $Arguments)
}

function Invoke-ExternalCommandWithTimeout {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [string[]]$Arguments = @(),
    [int]$TimeoutSeconds = 5
  )

  $stdoutPath = [System.IO.Path]::GetTempFileName()
  $stderrPath = [System.IO.Path]::GetTempFileName()

  try {
    $process = Start-Process -FilePath $FilePath `
      -ArgumentList $Arguments `
      -RedirectStandardOutput $stdoutPath `
      -RedirectStandardError $stderrPath `
      -WindowStyle Hidden `
      -PassThru

    if (-not (Wait-Process -Id $process.Id -Timeout $TimeoutSeconds -ErrorAction SilentlyContinue)) {
      Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
      return [pscustomobject]@{
        TimedOut = $true
        ExitCode = $null
        Output = @()
        Error = @("timed out after $TimeoutSeconds seconds")
      }
    }

    $outputLines = @()
    $errorLines = @()
    if (Test-Path -LiteralPath $stdoutPath) {
      $outputLines = @(Get-Content -LiteralPath $stdoutPath -ErrorAction SilentlyContinue)
    }
    if (Test-Path -LiteralPath $stderrPath) {
      $errorLines = @(Get-Content -LiteralPath $stderrPath -ErrorAction SilentlyContinue)
    }

    return [pscustomobject]@{
      TimedOut = $false
      ExitCode = $process.ExitCode
      Output = $outputLines
      Error = $errorLines
    }
  } catch {
    return [pscustomobject]@{
      TimedOut = $false
      ExitCode = 1
      Output = @()
      Error = @($_.Exception.Message)
    }
  } finally {
    Remove-Item -LiteralPath $stdoutPath -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stderrPath -ErrorAction SilentlyContinue
  }
}

function Get-DockerViewportContainers {
  $docker = Get-Command docker -ErrorAction SilentlyContinue
  if (-not $docker) {
    return @()
  }

  $result = Invoke-ExternalCommandWithTimeout -FilePath $docker.Source -Arguments @(
    "ps",
    "--filter", "label=com.docker.compose.project=main-computer-dev",
    "--filter", "label=com.docker.compose.service=$DockerService",
    "--format", "{{.ID}}`t{{.Names}}`t{{.Image}}`t{{.Ports}}`t{{.Status}}"
  ) -TimeoutSeconds 5

  if ($result.TimedOut -or $result.ExitCode -ne 0) {
    return @()
  }

  $containers = @()
  foreach ($line in @($result.Output)) {
    if (-not $line) {
      continue
    }
    $parts = $line -split "`t", 5
    if ($parts.Count -lt 5) {
      continue
    }
    $containers += [pscustomobject]@{
      Id = $parts[0]
      Name = $parts[1]
      Image = $parts[2]
      Ports = $parts[3]
      Status = $parts[4]
    }
  }

  $containers
}

function Get-DetectedModes {
  $local = @(Get-LocalViewportProcesses | Where-Object { $_.IsExpectedPython })
  $localOtherPython = @(Get-LocalViewportProcesses | Where-Object { -not $_.IsExpectedPython })
  $docker = @(Get-DockerViewportContainers)
  $listeners = @(Get-PortListenerDiagnostics -Ports @($LocalPort, $DockerHostPort))
  $foreign = @($listeners | Where-Object {
    $cmd = [string]$_.CommandLine
    $name = [string]$_.Name
    -not ($cmd -like "*main_computer.cli*" -and $cmd -like "* viewport *") -and
    -not ($name -like "com.docker.backend*" -or $name -like "wslrelay*")
  })

  [pscustomobject]@{
    Local = $local
    LocalOtherPython = $localOtherPython
    Docker = $docker
    Listeners = $listeners
    Foreign = $foreign
  }
}

function Select-ModeIfNeeded {
  param([string]$RequestedAction)

  if ($Mode) {
    return $Mode
  }

  if ($RequestedAction -eq "status") {
    return ""
  }

  Write-Host "Choose how to run Main Computer:"
  Write-Host "  [1] local  - Windows venv at http://127.0.0.1:$LocalPort"
  Write-Host "  [2] docker - Docker app container at http://127.0.0.1:$DockerHostPort"
  Write-Host "  [c] cancel"
  $choice = Read-Host "Mode"

  switch -Regex ($choice.Trim().ToLowerInvariant()) {
    "^(1|l|local)$" { return "local" }
    "^(2|d|docker)$" { return "docker" }
    default { throw "Cancelled." }
  }
}

function Write-LocalDetails {
  param([object[]]$Processes)

  if (-not $Processes -or $Processes.Count -eq 0) {
    Write-Host "  status: stopped"
    Write-Host "  url: $localUrl"
    return
  }

  Write-Host "  status: running"
  Write-Host "  url: $localUrl"
  foreach ($process in $Processes) {
    Write-Host ("  pid: {0}" -f $process.ProcessId)
    Write-Host ("  python: {0}" -f $process.ExecutablePath)
    if ($process.ExpectedPython -and -not $process.IsExpectedPython) {
      Write-Host ("  warning: python is not the intended venv Python: {0}" -f $process.ExpectedPython)
    }
    Write-Host ("  command: {0}" -f $process.CommandLine)
  }
}

function Write-DockerDetails {
  param([object[]]$Containers)

  if (-not $Containers -or $Containers.Count -eq 0) {
    Write-Host "  status: stopped"
    Write-Host "  url: $dockerUrl"
    return
  }

  Write-Host "  status: running"
  Write-Host "  url: $dockerUrl"
  foreach ($container in $Containers) {
    Write-Host ("  container: {0} ({1})" -f $container.Name, $container.Id)
    Write-Host ("  published ports: {0}" -f $container.Ports)
    Write-Host ("  image: {0}" -f $container.Image)
    Write-Host ("  status text: {0}" -f $container.Status)
    if ($container.Ports -like "*:8765->8765/tcp*") {
      Write-Host "  warning: Docker is publishing host port 8765. Prefer MAIN_COMPUTER_DOCKER_VIEWPORT_PORT=18765."
    }
  }
}

function Write-PortListenerDetails {
  param([object[]]$Listeners)

  foreach ($port in @($LocalPort, $DockerHostPort)) {
    Write-Host ("  {0}:" -f $port)
    $matches = @($Listeners | Where-Object { $_.LocalPort -eq $port })
    if ($matches.Count -eq 0) {
      Write-Host "    no listeners"
      continue
    }

    foreach ($listener in $matches) {
      Write-Host ("    {0}:{1} pid {2} {3}" -f $listener.LocalAddress, $listener.LocalPort, $listener.ProcessId, $listener.Name)
      if ($listener.ExecutablePath) {
        Write-Host ("      exe: {0}" -f $listener.ExecutablePath)
      }
      if ($listener.CommandLine) {
        Write-Host ("      cmd: {0}" -f $listener.CommandLine)
      }
      if ($listener.LocalAddress -eq "::1" -or $listener.Name -like "wslrelay*" -or $listener.Name -like "com.docker.backend*") {
        Write-Host "      note: localhost can route differently from 127.0.0.1 when IPv6, WSL, or Docker listeners exist."
      }
    }
  }
}

function Show-Status {
  $detected = Get-DetectedModes

  Write-Section "Local Windows viewport"
  Write-LocalDetails -Processes (@($detected.Local) + @($detected.LocalOtherPython))

  Write-Section "Docker viewport"
  Write-DockerDetails -Containers $detected.Docker

  Write-Section "Port listeners"
  Write-PortListenerDetails -Listeners $detected.Listeners

  Write-Host ""
  Write-Host "Open local mode with:  http://127.0.0.1:$LocalPort"
  Write-Host "Open docker mode with: http://127.0.0.1:$DockerHostPort"
  Write-Host "Avoid localhost for this app; use 127.0.0.1 so Docker, WSL, and IPv6 do not steal the request."

  if ($detected.Docker.Count -gt 0 -or $detected.Local.Count -gt 0) {
    return 0
  }
  return 1
}

function Refuse-MismatchedModeIfPolite {
  param(
    [string]$RequestedMode,
    [object]$Detected
  )

  if (-not $Polite) {
    return $false
  }

  if ($RequestedMode -eq "local" -and $Detected.Docker.Count -gt 0) {
    Write-Host "Refusing because -Polite was passed and a different app server is already running."
    Write-Host "detected running mode: Docker"
    Write-Host "requested mode: local"
    Write-DockerDetails -Containers $Detected.Docker
    Write-Host "current URL: $dockerUrl"
    Write-Host "cleanup command: .\dev-control.ps1 shutdown -Mode docker"
    return $true
  }

  if ($RequestedMode -eq "docker" -and ($Detected.Local.Count -gt 0 -or $Detected.LocalOtherPython.Count -gt 0)) {
    Write-Host "Refusing because -Polite was passed and a different app server is already running."
    Write-Host "detected running mode: local"
    Write-Host "requested mode: docker"
    Write-LocalDetails -Processes (@($Detected.Local) + @($Detected.LocalOtherPython))
    Write-Host "current URL: $localUrl"
    Write-Host "cleanup command: .\dev-control.ps1 shutdown -Mode local"
    return $true
  }

  return $false
}

function Confirm-StopOtherMode {
  param(
    [string]$RequestedMode,
    [object]$Detected
  )

  if ($RequestedMode -eq "local" -and $Detected.Docker.Count -gt 0) {
    Write-Host "Docker mode is already serving Main Computer:"
    Write-DockerDetails -Containers $Detected.Docker
    $answer = Read-Host "Stop Docker mode before starting local? [y/N]"
    if ($answer.Trim().ToLowerInvariant() -notin @("y", "yes")) {
      throw "Cancelled; Docker mode was left running."
    }
    Stop-DockerMode
  }

  if ($RequestedMode -eq "docker" -and ($Detected.Local.Count -gt 0 -or $Detected.LocalOtherPython.Count -gt 0)) {
    Write-Host "Local mode is already serving Main Computer:"
    Write-LocalDetails -Processes (@($Detected.Local) + @($Detected.LocalOtherPython))
    $answer = Read-Host "Stop local mode before starting Docker? [y/N]"
    if ($answer.Trim().ToLowerInvariant() -notin @("y", "yes")) {
      throw "Cancelled; local mode was left running."
    }
    Stop-LocalMode
  }
}

function Wait-HttpReady {
  param([string]$Url)

  $deadline = (Get-Date).AddSeconds($StartTimeoutSeconds)
  do {
    try {
      $response = Invoke-RestMethod -Uri "$Url/api/projects" -Method GET -TimeoutSec 3
      if ($response) {
        Write-Host "viewport api ready: $Url/api/projects"
        return $true
      }
    } catch {
      Start-Sleep -Milliseconds 500
    }
  } while ((Get-Date) -lt $deadline)

  Write-Warning "Viewport did not answer within $StartTimeoutSeconds seconds: $Url/api/projects"
  return $false
}

function Invoke-LegacyLocalControl {
  param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("status", "shutdown", "start", "restart")]
    [string]$LegacyAction,

    [string]$ResolvedPythonPath = ""
  )

  if (-not (Test-Path -LiteralPath $controlScript)) {
    throw "Native control script not found: $controlScript"
  }

  $legacyArgs = @(
    $LegacyAction,
    "-BindHost", $BindHost,
    "-Port", ([string]$LocalPort),
    "-HeartbeatPort", ([string]$HeartbeatPort),
    "-Workspace", $Workspace,
    "-StartTimeoutSeconds", ([string]$StartTimeoutSeconds),
    "-MathicsApiTimeoutSeconds", ([string]$MathicsApiTimeoutSeconds)
  )

  if ($ResolvedPythonPath) {
    $legacyArgs += @("-PythonPath", $ResolvedPythonPath)
  }

  if (-not [string]::IsNullOrWhiteSpace($ControlRoot)) {
    $legacyArgs += @("-ControlRoot", $ControlRoot)
  }

  if ($SkipMathicsCheck) {
    $legacyArgs += "-SkipMathicsCheck"
  }

  if ($AllowForeignPortListener) {
    $legacyArgs += "-AllowForeignPortListener"
  }

  # Keep --auto-allow as an explicit legacy-helper contract, but append it after
  # named parameters so it cannot be bound as another positional argument.
  $legacyArgs += "--auto-allow"

  & $controlScript @legacyArgs
  return $LASTEXITCODE
}

function Stop-LocalMode {
  return (Invoke-LegacyLocalControl -LegacyAction "shutdown")
}

function Start-LocalMode {
  Write-Host "Checking existing local/Docker mode state..."
  $detected = Get-DetectedModes
  if (Refuse-MismatchedModeIfPolite -RequestedMode "local" -Detected $detected) {
    return 2
  }
  Confirm-StopOtherMode -RequestedMode "local" -Detected $detected

  if ($EnsureRenderer) {
    Setup-LocalRenderer
  }

  $python = Resolve-MainComputerPython
  Write-Host "Starting local Windows viewport."
  Write-Host "python: $python"
  Write-Host "open: $localUrl"
  Write-Host "bind: $BindHost"
  return (Invoke-LegacyLocalControl -LegacyAction "start" -ResolvedPythonPath $python)
}

function Stop-DockerMode {
  Write-Host "Stopping Docker viewport service only; unrelated containers are not stopped."
  Invoke-DockerCompose -Arguments @("stop", $DockerService)
}

function Start-DockerMode {
  $detected = Get-DetectedModes
  if (Refuse-MismatchedModeIfPolite -RequestedMode "docker" -Detected $detected) {
    return 2
  }
  Confirm-StopOtherMode -RequestedMode "docker" -Detected $detected

  if ($EnsureRenderer) {
    Setup-DockerRenderer
  }

  Write-Host "Starting Docker viewport."
  Write-Host "open: $dockerUrl"
  Write-Host "MAIN_COMPUTER_DOCKER_VIEWPORT_PORT=$DockerHostPort"
  $oldPort = $env:MAIN_COMPUTER_DOCKER_VIEWPORT_PORT
  try {
    $env:MAIN_COMPUTER_DOCKER_VIEWPORT_PORT = [string]$DockerHostPort
    Invoke-DockerCompose -Arguments @("up", "-d", $DockerService)
  } finally {
    $env:MAIN_COMPUTER_DOCKER_VIEWPORT_PORT = $oldPort
  }
  Wait-HttpReady -Url $dockerUrl | Out-Null
  return 0
}

function Test-LocalRenderer {
  $python = Resolve-MainComputerPython
  Write-Host "checking local renderer with: $python"
  & $python -c "import sys; import playwright; print('playwright import ok:', sys.executable)"
  if ($LASTEXITCODE -ne 0) {
    throw "Playwright import failed for local Python: $python"
  }

  $launchScript = @"
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    print("chromium launch ok:", browser.version)
    browser.close()
"@
  & $python -c $launchScript
  if ($LASTEXITCODE -ne 0) {
    throw "Chromium launch failed for local Python: $python"
  }
}

function Setup-LocalRenderer {
  $python = Resolve-MainComputerPython
  if ($python -like "*\WindowsApps\*") {
    throw "Refusing to use WindowsApps Python. Pass -PythonPath for the intended venv Python."
  }

  Write-Host "Installing Playwright package into local venv:"
  Write-Host "  $python"
  & $python -m pip install "playwright>=1.40.0"
  if ($LASTEXITCODE -ne 0) {
    throw "pip install playwright failed."
  }

  Write-Host "Installing Playwright Chromium for local venv."
  & $python -m playwright install chromium
  if ($LASTEXITCODE -ne 0) {
    throw "playwright install chromium failed."
  }

  Test-LocalRenderer
  Write-Host "local PDF renderer ready: $localUrl"
}

function Test-DockerRenderer {
  Write-Host "checking Docker renderer in Compose service '$DockerService'"
  $launchScript = @"
import sys
from playwright.sync_api import sync_playwright
print("playwright import ok:", sys.executable)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    print("chromium launch ok:", browser.version)
    browser.close()
"@
  Invoke-DockerCompose -Arguments @("run", "--rm", "--no-deps", $DockerService, "python", "-c", $launchScript)
}

function Setup-DockerRenderer {
  Write-Host "Docker renderer support is built into docker/dev/app.Dockerfile."
  Write-Host "Rebuilding the app image so Playwright and Chromium are present:"
  Write-Host "  docker compose -f docker-compose.dev.yml --profile app build main-computer"
  Invoke-DockerCompose -Arguments @("build", $DockerService)
  Test-DockerRenderer
  Write-Host "docker PDF renderer ready: $dockerUrl"
}

function Invoke-Doctor {
  param([string]$DoctorMode)

  switch ($DoctorMode) {
    "local" {
      $python = Resolve-MainComputerPython
      Write-Host "local mode URL: $localUrl"
      Write-Host "intended Python: $python"
      if ($python -like "*\WindowsApps\*") {
        throw "The resolved Python is WindowsApps. Use the venv Python instead."
      }
      Test-LocalRenderer
      Write-Host "local renderer doctor passed."
      return 0
    }
    "docker" {
      Write-Host "docker mode URL: $dockerUrl"
      Write-Host "compose service: $DockerService"
      Write-Host "rebuild command after Dockerfile changes:"
      Write-Host "  docker compose -f docker-compose.dev.yml --profile app build main-computer"
      Test-DockerRenderer
      Write-Host "docker renderer doctor passed."
      return 0
    }
    default {
      Invoke-Doctor -DoctorMode "local"
      Invoke-Doctor -DoctorMode "docker"
      return 0
    }
  }
}

$selectedMode = Select-ModeIfNeeded -RequestedAction $Action

switch ($Action) {
  "status" {
    exit (Show-Status)
  }
  "doctor" {
    exit (Invoke-Doctor -DoctorMode $selectedMode)
  }
  "setup-renderer" {
    switch ($selectedMode) {
      "local" { Setup-LocalRenderer; exit 0 }
      "docker" { Setup-DockerRenderer; exit 0 }
      default { throw "Mode is required for setup-renderer." }
    }
  }
  "shutdown" {
    switch ($selectedMode) {
      "local" { Stop-LocalMode; exit $LASTEXITCODE }
      "docker" { Stop-DockerMode; exit 0 }
      default { throw "Mode is required for shutdown." }
    }
  }
  "start" {
    switch ($selectedMode) {
      "local" { exit (Start-LocalMode) }
      "docker" { exit (Start-DockerMode) }
      default { throw "Mode is required for start." }
    }
  }
  "restart" {
    switch ($selectedMode) {
      "local" {
        Write-Host "Checking existing local/Docker mode state..."
        $detected = Get-DetectedModes
        if (Refuse-MismatchedModeIfPolite -RequestedMode "local" -Detected $detected) { exit 2 }
        Confirm-StopOtherMode -RequestedMode "local" -Detected $detected
        Stop-LocalMode
        Start-Sleep -Seconds 1
        exit (Start-LocalMode)
      }
      "docker" {
        Write-Host "Checking existing local/Docker mode state..."
        $detected = Get-DetectedModes
        if (Refuse-MismatchedModeIfPolite -RequestedMode "docker" -Detected $detected) { exit 2 }
        Confirm-StopOtherMode -RequestedMode "docker" -Detected $detected
        Stop-DockerMode
        Start-Sleep -Seconds 1
        exit (Start-DockerMode)
      }
      default { throw "Mode is required for restart." }
    }
  }
}
