[CmdletBinding(PositionalBinding = $false)]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("status", "shutdown", "start", "restart")]
  [string]$Action,
  [string]$BindHost = "0.0.0.0",
  [int]$Port = 8765,
  [string]$Workspace = "",
  [string]$PythonPath = "",
  [string]$ControlRoot = "",
  [int]$StartTimeoutSeconds = 30,
  [int]$MathicsApiTimeoutSeconds = 180,
  [int]$HeartbeatPort = 0,
  [string]$MathicsProbeExpression = "2+2",
  [string]$MathicsProbeExpected = "4",
  [switch]$SkipMathicsCheck,
  [switch]$AllowForeignPortListener,
  [Alias("auto-allow")]
  [switch]$AutoAllow,

  [Parameter(ValueFromRemainingArguments = $true)]
  [object[]]$RemainingArguments
)

$ErrorActionPreference = "Stop"

$remainingArgs = @()
if ($null -ne $RemainingArguments) {
  $remainingArgs = @($RemainingArguments | ForEach-Object { [string]$_ })
}

# PowerShell can route arguments that follow the legacy --auto-allow marker into
# ValueFromRemainingArguments instead of binding otherwise-valid named parameters.
# Keep accepting the explicit marker for automated callers, then recover the
# helper's real parameter set from any remaining tokens before deciding whether
# the call is allowed.
$unexpectedRemainingArgs = @()
for ($i = 0; $i -lt $remainingArgs.Count; $i++) {
  $arg = [string]$remainingArgs[$i]
  $argKey = $arg.ToLowerInvariant()

  if ($argKey -eq "--auto-allow" -or $argKey -eq "-auto-allow" -or $argKey -eq "-autoallow") {
    $AutoAllow = $true
    continue
  }

  if ($argKey -eq "-skipmathicscheck") {
    $SkipMathicsCheck = $true
    continue
  }

  if ($argKey -eq "-allowforeignportlistener") {
    $AllowForeignPortListener = $true
    continue
  }

  if ($argKey -in @(
      "-bindhost",
      "-port",
      "-workspace",
      "-pythonpath",
      "-controlroot",
      "-starttimeoutseconds",
      "-mathicsapitimeoutseconds",
      "-heartbeatport",
      "-mathicsprobeexpression",
      "-mathicsprobeexpected"
    )) {
    if (($i + 1) -ge $remainingArgs.Count) {
      $unexpectedRemainingArgs += $arg
      continue
    }

    $i += 1
    $value = [string]$remainingArgs[$i]

    try {
      switch ($argKey) {
        "-bindhost" { $BindHost = $value }
        "-port" { $Port = [int]$value }
        "-workspace" { $Workspace = $value }
        "-pythonpath" { $PythonPath = $value }
        "-controlroot" { $ControlRoot = $value }
        "-starttimeoutseconds" { $StartTimeoutSeconds = [int]$value }
        "-mathicsapitimeoutseconds" { $MathicsApiTimeoutSeconds = [int]$value }
        "-heartbeatport" { $HeartbeatPort = [int]$value }
        "-mathicsprobeexpression" { $MathicsProbeExpression = $value }
        "-mathicsprobeexpected" { $MathicsProbeExpected = $value }
      }
    } catch {
      $unexpectedRemainingArgs += ("{0} {1}" -f $arg, $value)
    }
    continue
  }

  $unexpectedRemainingArgs += $arg
}
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Workspace) {
  $Workspace = $scriptRoot
}

if (-not $ControlRoot) {
  $ControlRoot = $scriptRoot
}
$controlRoot = [System.IO.Path]::GetFullPath($ControlRoot)

function Show-DevControlRedirect {
  param([string]$RequestedAction)

  $devControl = Join-Path $scriptRoot "dev-control.ps1"
  $displayAction = if ($RequestedAction) { $RequestedAction } else { "status" }

  Write-Host ""
  Write-Host "control-main-computer.ps1 is now an internal local-mode helper."
  Write-Host "Use dev-control.ps1 instead so the run mode and port are explicit:"
  if ($displayAction -eq "status") {
    Write-Host "  .\dev-control.ps1 status"
  } else {
    Write-Host ("  .\dev-control.ps1 {0} -Mode local" -f $displayAction)
  }
  Write-Host ""
  Write-Host "Useful user-facing commands:"
  Write-Host "  .\dev-control.ps1 status"
  Write-Host "  .\dev-control.ps1 start -Mode local"
  Write-Host "  .\dev-control.ps1 start -Mode docker"
  Write-Host "  .\dev-control.ps1 shutdown -Mode local"
  Write-Host "  .\dev-control.ps1 shutdown -Mode docker"
  Write-Host ""
  Write-Host "Open 127.0.0.1 URLs printed by dev-control.ps1; avoid localhost because Docker/WSL/IPv6 listeners can route differently."
  Write-Host ""
  Write-Host "Automated callers that intentionally depend on this legacy helper must pass --auto-allow."
  Write-Host ("legacy helper path: {0}" -f $PSCommandPath)
  Write-Host ("dev controller path: {0}" -f $devControl)
}

if (-not $AutoAllow) {
  Show-DevControlRedirect -RequestedAction $Action
  exit 2
}

if ($unexpectedRemainingArgs.Count -gt 0) {
  throw "Unexpected control-main-computer.ps1 argument(s): $($unexpectedRemainingArgs -join ' '). Use dev-control.ps1 for user-facing server control."
}

$pidFile = Join-Path $controlRoot ".main_computer_viewport.pid"
$outLog = Join-Path $controlRoot "main_computer_viewport.out.log"
$errLog = Join-Path $controlRoot "main_computer_viewport.err.log"
$heartbeatPidFile = Join-Path $controlRoot ".main_computer_heartbeat.pid"
$heartbeatOutLog = Join-Path $controlRoot "main_computer_heartbeat.out.log"
$heartbeatErrLog = Join-Path $controlRoot "main_computer_heartbeat.err.log"
$heartbeatPort = if ($HeartbeatPort -gt 0) { $HeartbeatPort } else { $Port + 1 }
$ProbeHost = if ($BindHost -eq "0.0.0.0" -or $BindHost -eq "::") { "127.0.0.1" } else { $BindHost }
$healthUrl = "http://${ProbeHost}:${Port}/api/projects"
$heartbeatHealthUrl = "http://${ProbeHost}:${heartbeatPort}/healthz"
$heartbeatControlUrl = "http://${ProbeHost}:${heartbeatPort}/api/heartbeat/control"
$mathicsUrl = "http://${ProbeHost}:${Port}/api/applications/calculator/mathics/evaluate"

$env:MAIN_COMPUTER_CONTROL_ROOT = $controlRoot
$env:MAIN_COMPUTER_CONTROL_PORT = "$Port"
$env:MAIN_COMPUTER_HEARTBEAT_PORT = "$heartbeatPort"

function Resolve-MainComputerPython {
  if ($PythonPath) {
    if (Test-Path -LiteralPath $PythonPath) {
      return (Resolve-Path -LiteralPath $PythonPath).Path
    }
    throw "PythonPath was provided but was not found: $PythonPath"
  }

  $candidates = New-Object System.Collections.Generic.List[string]

  if ($env:VIRTUAL_ENV) {
    $candidates.Add((Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"))
  }

  $parentRoot = Split-Path -Parent $scriptRoot
  $candidates.Add((Join-Path $parentRoot ".venv\Scripts\python.exe"))
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

  throw "Could not find a usable Python. Expected a venv at '$parentRoot\.venv' or '$scriptRoot\.venv'. Pass -PythonPath if needed."
}


function Test-CommandLineHasViewportPort {
  param(
    [string]$CommandLine
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

function Get-ProcessFromPidFile {
  param([string]$PidPath)

  if (-not (Test-Path -LiteralPath $PidPath)) {
    return $null
  }

  try {
    $savedPid = [int](Get-Content -LiteralPath $PidPath -Raw).Trim()
    $process = Get-SafeProcessById -TargetProcessId $savedPid
    if ($process) {
      return $process
    }
    Remove-Item -LiteralPath $PidPath -ErrorAction SilentlyContinue
  } catch {
    Remove-Item -LiteralPath $PidPath -ErrorAction SilentlyContinue
  }

  return $null
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
  param([int]$LocalPort)

  @(Get-NetstatListenRows -Ports @($LocalPort) |
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

function Add-ProcessDetectionMetadata {
  param(
    [Parameter(Mandatory = $true)]
    [object]$Process,
    [string]$Source = ""
  )

  if (-not $Process.CommandLine) {
    $Process.CommandLine = "unavailable without CIM/WMI; identified by $Source"
  }
  $Process | Add-Member -NotePropertyName DetectionSource -NotePropertyValue $Source -Force
  return $Process
}

function Get-ViewportProcesses {
  $found = @()

  $pidFileProcess = Get-ProcessFromPidFile -PidPath $pidFile
  if ($pidFileProcess) {
    $found += (Add-ProcessDetectionMetadata -Process $pidFileProcess -Source ".main_computer_viewport.pid")
  }

  foreach ($listenerPid in @(Get-ProcessIdsListeningOnPort -LocalPort $Port)) {
    $process = Get-SafeProcessById -TargetProcessId ([int]$listenerPid)
    if ($process -and (Test-ProcessLooksLikePython -Process $process)) {
      $found += (Add-ProcessDetectionMetadata -Process $process -Source "listener on port $Port")
    }
  }

  $found |
    Where-Object { $_ -and $_.ProcessId } |
    Sort-Object ProcessId -Unique
}

function Get-HeartbeatProcesses {
  $found = @()

  $pidFileProcess = Get-ProcessFromPidFile -PidPath $heartbeatPidFile
  if ($pidFileProcess) {
    $found += (Add-ProcessDetectionMetadata -Process $pidFileProcess -Source ".main_computer_heartbeat.pid")
  }

  foreach ($listenerPid in @(Get-ProcessIdsListeningOnPort -LocalPort $heartbeatPort)) {
    $process = Get-SafeProcessById -TargetProcessId ([int]$listenerPid)
    if ($process -and (Test-ProcessLooksLikePython -Process $process)) {
      $found += (Add-ProcessDetectionMetadata -Process $process -Source "listener on port $heartbeatPort")
    }
  }

  $found |
    Where-Object { $_ -and $_.ProcessId } |
    Sort-Object ProcessId -Unique
}

function Stop-DetectedProcesses {
  param(
    [Parameter(Mandatory = $true)]
    [object[]]$Processes,
    [string]$Label = "process"
  )

  $stopped = 0
  foreach ($process in $Processes) {
    if (-not $process -or -not $process.ProcessId) {
      continue
    }

    try {
      Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
      Write-Host ("stopped {0} pid {1}" -f $Label, $process.ProcessId)
      $stopped += 1
    } catch {
      Write-Host ("could not stop {0} pid {1}: {2}" -f $Label, $process.ProcessId, $_.Exception.Message)
    }
  }

  return $stopped
}


function Get-PortListenerDiagnostics {
  $listeners = @()
  $connections = @(Get-NetstatListenRows -Ports @($Port))

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

  $listeners |
    Where-Object { $_ -and $_.ProcessId } |
    Sort-Object LocalAddress, ProcessId -Unique
}


function Test-IsExpectedViewportListener {
  param(
    [Parameter(Mandatory = $true)]
    [object]$Listener
  )

  if ($Listener.CommandLine -and
      $Listener.CommandLine -like "*main_computer.cli*" -and
      $Listener.CommandLine -like "* viewport *" -and
      (Test-CommandLineHasViewportPort -CommandLine $Listener.CommandLine)) {
    return $true
  }

  if ((Test-Path -LiteralPath $pidFile) -and $Listener.ProcessId) {
    try {
      $viewportPid = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
      if ($viewportPid -eq [int]$Listener.ProcessId) {
        return $true
      }
    } catch {
      # Ignore stale or malformed PID files here; cleanup happens in Get-ViewportProcesses.
    }
  }

  # When CIM/WMI is unavailable, command-line text cannot be read safely. A Python
  # process already listening on the configured viewport port is treated as the
  # local viewport so start/status/restart remain usable on WMI-broken systems.
  if ($Listener.LocalPort -eq $Port -and $Listener.Name -like "python*") {
    return $true
  }

  return $false
}


function Format-PortListenerDiagnostics {
  param(
    [object[]]$Listeners
  )

  if (-not $Listeners -or $Listeners.Count -eq 0) {
    return "no listeners found on port $Port"
  }

  $lines = New-Object System.Collections.Generic.List[string]
  foreach ($listener in $Listeners) {
    $lines.Add(("{0}:{1} pid {2} {3}" -f $listener.LocalAddress, $listener.LocalPort, $listener.ProcessId, $listener.Name))
    if ($listener.ExecutablePath) {
      $lines.Add(("  exe: {0}" -f $listener.ExecutablePath))
    }
    if ($listener.CommandLine) {
      $lines.Add(("  cmd: {0}" -f $listener.CommandLine))
    }
  }

  return ($lines -join "`n")
}

function Get-DockerPortHint {
  # Avoid docker ps here because this script must remain usable when Docker Desktop
  # or Windows process providers are unhealthy. Port diagnostics above already show
  # the owning PID when a listener exists.
  return ""
}

function Assert-NoForeignPortListeners {
  param(
    [string]$Context = "starting viewport"
  )

  $listeners = @(Get-PortListenerDiagnostics)
  if ($listeners.Count -eq 0) {
    return
  }

  $foreignListeners = @($listeners | Where-Object { -not (Test-IsExpectedViewportListener -Listener $_) })
  if ($foreignListeners.Count -eq 0) {
    return
  }

  $diagnostics = Format-PortListenerDiagnostics -Listeners $listeners
  $dockerHint = Get-DockerPortHint
  $hint = @"
Foreign listener(s) are already bound to port $Port while $Context.

This creates ambiguous routing: localhost may resolve to IPv6 (::1), Docker may publish 0.0.0.0:$Port, and 127.0.0.1 may reach a different process. PDF export can then hit Docker/WSL instead of the Windows viewport.

Current listeners:
$diagnostics
"@

  if ($dockerHint) {
    $hint = "$hint`n$dockerHint`n"
  }

  $hint = "$hint`nCleanup commands to run after confirming the listed processes are not needed:`n"
  $hint = "$hint  docker stop main-computer-dev-main-computer-1`n"
  $hint = "$hint  .\dev-control.ps1 shutdown -Mode local`n"
  $hint = "$hint  Stop-Process -Id <pid> -Force`n`n"
  $hint = "$hint To intentionally bypass this guard, rerun with -AllowForeignPortListener."

  if ($AllowForeignPortListener) {
    Write-Warning $hint
    return
  }

  throw $hint
}

function Stop-Viewport {
  $processes = @(Get-ViewportProcesses)
  if ($processes.Count -eq 0) {
    Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
    Write-Host "viewport already stopped"
    return 0
  }

  Stop-DetectedProcesses -Processes $processes -Label "viewport" | Out-Null
  Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
  return 0
}

function Stop-Heartbeat {
  $processes = @(Get-HeartbeatProcesses)
  if ($processes.Count -eq 0) {
    Remove-Item -LiteralPath $heartbeatPidFile -ErrorAction SilentlyContinue
    return 0
  }

  Stop-DetectedProcesses -Processes $processes -Label "heartbeat" | Out-Null
  Remove-Item -LiteralPath $heartbeatPidFile -ErrorAction SilentlyContinue
  return 0
}

function Wait-ViewportReady {
  $deadline = (Get-Date).AddSeconds($StartTimeoutSeconds)
  do {
    try {
      $response = Invoke-RestMethod -Uri $healthUrl -Method GET -TimeoutSec 3
      if ($response) {
        Write-Host ("viewport api ready: {0}" -f $healthUrl)
        return $true
      }
    } catch {
      Start-Sleep -Milliseconds 500
    }
  } while ((Get-Date) -lt $deadline)

  throw "Viewport did not become ready within $StartTimeoutSeconds seconds. Check $errLog"
}

function Test-HeartbeatReady {
  try {
    $response = Invoke-RestMethod -Uri $heartbeatHealthUrl -Method GET -TimeoutSec 2
    if ($response) {
      return $true
    }
  } catch {
    return $false
  }

  return $false
}

function Ensure-HeartbeatReady {
  if (Test-HeartbeatReady) {
    Write-Host ("heartbeat api ready: {0}" -f $heartbeatControlUrl)
    return $true
  }

  $existingHeartbeat = @(Get-HeartbeatProcesses)
  if ($existingHeartbeat.Count -gt 0) {
    Write-Host "heartbeat process exists but is not reachable; restarting heartbeat sidecar"
    Stop-Heartbeat | Out-Null
  }

  if (-not (Test-Path -LiteralPath $Workspace)) {
    throw "Workspace not found: $Workspace"
  }

  $python = Resolve-MainComputerPython
  Write-Host ("starting heartbeat sidecar: {0}" -f $heartbeatControlUrl)
  $process = Start-Process -FilePath $python `
    -ArgumentList @("-B", "-m", "main_computer.cli", "heartbeat", "--host", $BindHost, "--port", "$heartbeatPort", "--server-port", "$Port", "--workspace", $Workspace, "--noverbose") `
    -WorkingDirectory $Workspace `
    -RedirectStandardOutput $heartbeatOutLog `
    -RedirectStandardError $heartbeatErrLog `
    -PassThru

  Set-Content -LiteralPath $heartbeatPidFile -Value $process.Id

  $deadline = (Get-Date).AddSeconds([Math]::Min($StartTimeoutSeconds, 15))
  do {
    if (Test-HeartbeatReady) {
      Write-Host ("heartbeat api ready: {0}" -f $heartbeatControlUrl)
      return $true
    }
    Start-Sleep -Milliseconds 250
  } while ((Get-Date) -lt $deadline)

  throw "Heartbeat did not become ready at $heartbeatHealthUrl. Check $heartbeatErrLog"
}


function Test-MathicsApi {
  if ($SkipMathicsCheck) {
    Write-Host "Mathics API verification skipped."
    return $true
  }

  $timeout = [Math]::Max(30, $MathicsApiTimeoutSeconds)
  $body = @{
    expression = $MathicsProbeExpression
    timeout_s = $timeout
  } | ConvertTo-Json

  Write-Host ("verifying Mathics API: {0} (timeout {1}s)" -f $mathicsUrl, $timeout)
  Write-Host "first Mathics request can be slow while Mathics3 loads built-ins"

  try {
    $response = Invoke-RestMethod -Uri $mathicsUrl -Method POST -ContentType "application/json" -Body $body -TimeoutSec $timeout
  } catch {
    $message = $_.Exception.Message
    $hint = "If this was a first-run timeout, wait a few seconds and run '.\dev-control.ps1 status' or '.\dev-control.ps1 restart -Mode local'."
    throw "Mathics API verification request failed after ${timeout}s: $message`n$hint"
  }

  if ($response.ok -ne $true) {
    $detail = $response | ConvertTo-Json -Depth 8
    throw "Mathics API verification failed: $detail"
  }

  $resultText = [string]$response.result_text
  if ($MathicsProbeExpected -and ($resultText -notlike "*$MathicsProbeExpected*")) {
    $detail = $response | ConvertTo-Json -Depth 8
    throw "Mathics API verification returned an unexpected result. Expected '$MathicsProbeExpected'. Response: $detail"
  }

  Write-Host ("Mathics API verified: {0} => {1}" -f $MathicsProbeExpression, $resultText)
  return $true
}

function Start-Viewport {
  $existing = @(Get-ViewportProcesses)
  if ($existing.Count -gt 0) {
    Write-Host ("viewport already running pid {0}" -f $existing[0].ProcessId)
    Wait-ViewportReady | Out-Null
    Ensure-HeartbeatReady | Out-Null
    Test-MathicsApi | Out-Null
    return 0
  }

  Assert-NoForeignPortListeners -Context "starting local Windows viewport"

  if (-not (Test-Path -LiteralPath $Workspace)) {
    throw "Workspace not found: $Workspace"
  }

  $python = Resolve-MainComputerPython
  Write-Host ("python: {0}" -f $python)
  Write-Host ("workspace: {0}" -f $Workspace)
  Write-Host ("bind: {0}" -f $BindHost)
  Write-Host ("open: http://{0}:{1}" -f $ProbeHost, $Port)

  New-Item -ItemType Directory -Path $controlRoot -Force | Out-Null
  $process = Start-Process -FilePath $python `
    -ArgumentList @("-B", "-m", "main_computer.cli", "viewport", "--host", $BindHost, "--port", "$Port", "--workspace", $Workspace) `
    -WorkingDirectory $Workspace `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru

  Set-Content -LiteralPath $pidFile -Value $process.Id
  Write-Host ("started viewport pid {0}" -f $process.Id)

  Wait-ViewportReady | Out-Null
  Ensure-HeartbeatReady | Out-Null
  Test-MathicsApi | Out-Null
  return 0
}

function Show-Status {
  $processes = @(Get-ViewportProcesses)
  $heartbeat = @(Get-HeartbeatProcesses)

  if ($processes.Count -gt 0) {
    $process = $processes[0]
    Write-Host "running"
    Write-Host ("pid: {0}" -f $process.ProcessId)
    Write-Host ("command: {0}" -f $process.CommandLine)
    Write-Host ("port: {0}" -f $Port)
    $listeners = @(Get-PortListenerDiagnostics)
    if ($listeners.Count -gt 0) {
      Write-Host "listeners:"
      Write-Host (Format-PortListenerDiagnostics -Listeners $listeners)
    }

    try {
      Test-MathicsApi | Out-Null
    } catch {
      Write-Host ("mathics: failed - {0}" -f $_.Exception.Message)
    }
  } else {
    Write-Host "stopped"
    Write-Host ("port: {0}" -f $Port)
    $listeners = @(Get-PortListenerDiagnostics)
    if ($listeners.Count -gt 0) {
      Write-Host "foreign listeners still present:"
      Write-Host (Format-PortListenerDiagnostics -Listeners $listeners)
    }
  }

  if (Test-HeartbeatReady) {
    Write-Host ("heartbeat: ready at {0}" -f $heartbeatControlUrl)
    if ($heartbeat.Count -gt 0) {
      Write-Host ("heartbeat pid: {0}" -f (($heartbeat | ForEach-Object { $_.ProcessId }) -join ", "))
    }
  } elseif ($heartbeat.Count -gt 0) {
    Write-Host ("heartbeat: process exists but is not reachable: {0}" -f (($heartbeat | ForEach-Object { $_.ProcessId }) -join ", "))
    Write-Host ("heartbeat url: {0}" -f $heartbeatControlUrl)
  } else {
    Write-Host ("heartbeat: missing at {0}" -f $heartbeatControlUrl)
  }

  Write-Host ("control root: {0}" -f $controlRoot)
  Write-Host ("out: {0}" -f $outLog)
  Write-Host ("err: {0}" -f $errLog)
  Write-Host ("heartbeat out: {0}" -f $heartbeatOutLog)
  Write-Host ("heartbeat err: {0}" -f $heartbeatErrLog)

  if ($processes.Count -gt 0) {
    return 0
  }
  return 1
}

switch ($Action) {
  "status" { exit (Show-Status) }
  "shutdown" {
    Stop-Heartbeat | Out-Null
    exit (Stop-Viewport)
  }
  "start" { exit (Start-Viewport) }
  "restart" {
    Stop-Heartbeat | Out-Null
    Stop-Viewport | Out-Null
    Start-Sleep -Seconds 1
    exit (Start-Viewport)
  }
}
