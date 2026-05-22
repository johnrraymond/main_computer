# run-main-computer-test.ps1
#
# Development checkout runner for Main Computer.
#
# This mirrors the installed run-main-computer.ps1 action/mode surface without
# requiring bootstrap-main-computer-windows.ps1 to populate an install root first.
# Run it from the repo checkout:
#
#   .\run-main-computer-test.ps1 -Action check -Mode Unleashed
#   .\run-main-computer-test.ps1 -Action start -Mode Debug
#   .\run-main-computer-test.ps1 Debug
#
# The last form is a quick mode check shorthand.

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Action = "start",

    [Parameter(Position = 1)]
    [string]$Mode = "",

    [string]$Workspace = "",

    [string]$PythonPath = "",

    [string]$WslCommand = "wsl.exe",

    [string]$StateStoreRoot = "",

    [string]$BindHost = "127.0.0.1",

    [int]$StartTimeoutSeconds = 90,

    [switch]$SkipMathicsCheck,

    [switch]$AllowForeignPortListener,

    [switch]$BuildWslRuntimeIfMissing,

    [switch]$SkipWslRuntimeInstall,

    [switch]$ResetWslRuntime,

    [switch]$SkipExecutorSmoke
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:RepoRoot = Split-Path -Parent $PSCommandPath
$script:ValidRunnerActions = @("start", "run", "restart", "status", "stop", "shutdown", "install", "install-run", "smoke", "check")
$script:ValidRunnerModes = @("unleashed", "unleashed-mode", "debug", "safe", "safe-mode")

function Test-RunnerActionName {
    param([Parameter(Mandatory = $true)][string]$Name)
    return ($script:ValidRunnerActions -contains $Name.Trim().ToLowerInvariant())
}

function Test-RunnerModeName {
    param([Parameter(Mandatory = $true)][string]$Name)
    return ($script:ValidRunnerModes -contains (($Name.Trim().ToLowerInvariant()) -replace "\s+", "-"))
}

function Resolve-RunnerArguments {
    if (Test-RunnerModeName -Name $Action) {
        if (-not [string]::IsNullOrWhiteSpace($Mode)) {
            throw "Ambiguous arguments. Use either '.\run-main-computer-test.ps1 Debug' or '.\run-main-computer-test.ps1 -Action check -Mode Debug'."
        }
        $script:Mode = $Action
        $script:Action = "check"
    }

    $script:Action = $script:Action.Trim().ToLowerInvariant()
    if (-not (Test-RunnerActionName -Name $script:Action)) {
        throw "Unknown action '$Action'. Valid actions: $($script:ValidRunnerActions -join ', ')."
    }

    if ([string]::IsNullOrWhiteSpace($script:Mode)) {
        $script:Mode = "Unleashed"
    }

    if (-not (Test-RunnerModeName -Name $script:Mode)) {
        throw "Unknown mode '$Mode'. Valid modes: Unleashed, Debug, Safe."
    }
}

function ConvertTo-MainComputerInstanceSegment {
    param([Parameter(Mandatory = $true)][string]$Value)

    $segment = $Value.Trim().ToLowerInvariant() -replace "[^a-z0-9]+", "-"
    $segment = $segment.Trim("-")
    if ([string]::IsNullOrWhiteSpace($segment)) {
        $segment = "default"
    }
    if ($segment.Length -gt 32) {
        $segment = $segment.Substring(0, 32).Trim("-")
    }
    if ([string]::IsNullOrWhiteSpace($segment)) {
        $segment = "default"
    }
    return $segment
}

function Resolve-DevStateStoreRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$InstanceName
    )

    if (-not [string]::IsNullOrWhiteSpace($StateStoreRoot)) {
        return [System.IO.Path]::GetFullPath($StateStoreRoot).TrimEnd([char[]]@('\', '/'))
    }

    if (-not [string]::IsNullOrWhiteSpace($env:MAIN_COMPUTER_TEST_STATE_ROOT)) {
        return [System.IO.Path]::GetFullPath($env:MAIN_COMPUTER_TEST_STATE_ROOT).TrimEnd([char[]]@('\', '/'))
    }

    $userProfileRoot = [Environment]::GetFolderPath("UserProfile")
    if ([string]::IsNullOrWhiteSpace($userProfileRoot)) {
        $userProfileRoot = $env:USERPROFILE
    }

    if (-not [string]::IsNullOrWhiteSpace($userProfileRoot)) {
        return (Join-Path (Join-Path (Join-Path ([System.IO.Path]::GetFullPath($userProfileRoot)) ".main-computer-tools") "instances") $InstanceName)
    }

    return (Join-Path $Root "runtime\run-main-computer-test\$InstanceName")
}

function Resolve-DevPythonPath {
    $candidates = New-Object System.Collections.Generic.List[string]

    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        $candidates.Add($PythonPath)
    }

    if (-not [string]::IsNullOrWhiteSpace($env:MAIN_COMPUTER_PYTHON)) {
        $candidates.Add($env:MAIN_COMPUTER_PYTHON)
    }

    if (-not [string]::IsNullOrWhiteSpace($env:VIRTUAL_ENV)) {
        $candidates.Add((Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"))
    }

    $candidates.Add((Join-Path $script:RepoRoot ".venv\Scripts\python.exe"))
    $candidates.Add((Join-Path (Split-Path -Parent $script:RepoRoot) ".venv\Scripts\python.exe"))

    $pythonCommand = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $pythonCommand -and -not [string]::IsNullOrWhiteSpace($pythonCommand.Source) -and $pythonCommand.Source -notlike "*\WindowsApps\*") {
        $candidates.Add($pythonCommand.Source)
    }

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
        $command = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
            return $command.Source
        }
    }

    return "python.exe"
}

function New-DevModeProfile {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$DistributionSuffix,
        [Parameter(Mandatory = $true)][string]$GuidanceLevel,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][int]$HeartbeatPort,
        [Parameter(Mandatory = $true)][int]$OnlyOfficePort,
        [Parameter(Mandatory = $true)][int]$LocalServerPortStart,
        [Parameter(Mandatory = $true)][int]$LocalServerGeneratedPortStart,
        [Parameter(Mandatory = $true)][int]$LocalServerGeneratedPortEnd,
        [Parameter(Mandatory = $true)][int]$CoolifyPort,
        [Parameter(Mandatory = $true)][int]$CoolifySoketiPort,
        [Parameter(Mandatory = $true)][int]$CoolifySoketiTerminalPort
    )

    $leaf = Split-Path -Leaf ([System.IO.Path]::GetFullPath($script:RepoRoot).TrimEnd([char[]]@('\', '/')))
    $instanceName = ConvertTo-MainComputerInstanceSegment -Value $leaf
    $storeRoot = Resolve-DevStateStoreRoot -Root $script:RepoRoot -InstanceName $instanceName
    $stateRoot = Join-Path $storeRoot $Key
    $python = Resolve-DevPythonPath
    $localPlatformPrefix = "main-computer-local-platform-"
    $localPlatformSuffix = "-$Key"
    $localPlatformMaxInstanceLength = 63 - $localPlatformPrefix.Length - $localPlatformSuffix.Length
    if ($localPlatformMaxInstanceLength -lt 1) {
        $localPlatformMaxInstanceLength = 1
    }
    $localPlatformInstance = $instanceName
    if ($localPlatformInstance.Length -gt $localPlatformMaxInstanceLength) {
        $localPlatformInstance = $localPlatformInstance.Substring(0, $localPlatformMaxInstanceLength).Trim("-")
    }
    if ([string]::IsNullOrWhiteSpace($localPlatformInstance)) {
        $localPlatformInstance = "main-computer"
    }
    $localPlatformProject = "$localPlatformPrefix$localPlatformInstance$localPlatformSuffix"

    return [pscustomobject]@{
        Key = $Key
        Label = $Label
        GuidanceLevel = $GuidanceLevel
        InstanceName = $instanceName
        StateStoreRoot = $storeRoot
        StateRoot = $stateRoot
        PythonPath = $python
        Port = $Port
        HeartbeatPort = $HeartbeatPort
        Distribution = "MainComputer-$instanceName-$DistributionSuffix"
        ControlRoot = Join-Path $stateRoot "control"
        ExecutorRoot = Join-Path $stateRoot "executor"
        WslRuntimeRoot = Join-Path $stateRoot "wsl"
        OnlyOfficePort = $OnlyOfficePort
        OnlyOfficeProject = "main-computer-onlyoffice-$Key"
        LocalServerProject = $localPlatformProject
        LocalServerRegistry = Join-Path $stateRoot "local-platform\sites.json"
        LocalServerCompose = Join-Path $stateRoot "local-platform\docker-compose.websites.yml"
        LocalServerPortStart = $LocalServerPortStart
        LocalServerGeneratedPortStart = $LocalServerGeneratedPortStart
        LocalServerGeneratedPortEnd = $LocalServerGeneratedPortEnd
        CoolifyProject = "main-computer-coolify-$instanceName-$Key"
        CoolifyStateRoot = Join-Path $stateRoot "coolify-local-docker"
        CoolifyPort = $CoolifyPort
        CoolifySoketiPort = $CoolifySoketiPort
        CoolifySoketiTerminalPort = $CoolifySoketiTerminalPort
    }
}

function Resolve-RunnerMode {
    param([Parameter(Mandatory = $true)][string]$ModeName)

    $normalized = ($ModeName.Trim().ToLowerInvariant() -replace "\s+", "-")
    switch ($normalized) {
        "unleashed" {
            return (New-DevModeProfile -Key "unleashed" -Label "Unleashed Mode" -DistributionSuffix "unleashed" -GuidanceLevel "developer" -Port 8765 -HeartbeatPort 8766 -OnlyOfficePort 18084 -LocalServerPortStart 18080 -LocalServerGeneratedPortStart 18100 -LocalServerGeneratedPortEnd 18199 -CoolifyPort 17056 -CoolifySoketiPort 17156 -CoolifySoketiTerminalPort 17256)
        }
        "unleashed-mode" { return (Resolve-RunnerMode -ModeName "Unleashed") }
        "debug" {
            return (New-DevModeProfile -Key "debug" -Label "Debug" -DistributionSuffix "debug" -GuidanceLevel "debug" -Port 28865 -HeartbeatPort 28866 -OnlyOfficePort 28084 -LocalServerPortStart 28080 -LocalServerGeneratedPortStart 28100 -LocalServerGeneratedPortEnd 28199 -CoolifyPort 27056 -CoolifySoketiPort 27156 -CoolifySoketiTerminalPort 27256)
        }
        "safe" {
            return (New-DevModeProfile -Key "safe" -Label "Safe Mode" -DistributionSuffix "safe" -GuidanceLevel "guided" -Port 38865 -HeartbeatPort 38866 -OnlyOfficePort 38084 -LocalServerPortStart 38080 -LocalServerGeneratedPortStart 38100 -LocalServerGeneratedPortEnd 38199 -CoolifyPort 37056 -CoolifySoketiPort 37156 -CoolifySoketiTerminalPort 37256)
        }
        "safe-mode" { return (Resolve-RunnerMode -ModeName "Safe") }
    }

    throw "Unknown Main Computer runner mode: $ModeName"
}

function Set-RunnerEnvironment {
    param([Parameter(Mandatory = $true)]$SelectedMode)

    $workspaceRoot = if ([string]::IsNullOrWhiteSpace($Workspace)) { $script:RepoRoot } else { [System.IO.Path]::GetFullPath($Workspace) }

    $env:MAIN_COMPUTER_PYTHON = $SelectedMode.PythonPath
    $env:MAIN_COMPUTER_WORKSPACE = $workspaceRoot
    $env:MAIN_COMPUTER_INSTALL_MODE = $SelectedMode.Key
    $env:MAIN_COMPUTER_MODE_LABEL = $SelectedMode.Label
    $env:MAIN_COMPUTER_GUIDANCE_LEVEL = $SelectedMode.GuidanceLevel
    $env:MAIN_COMPUTER_SAFE_MODE = if ($SelectedMode.Key -eq "safe") { "1" } else { "0" }
    $env:MAIN_COMPUTER_INSTANCE_NAME = $SelectedMode.InstanceName
    $env:MAIN_COMPUTER_STATE_ROOT = $SelectedMode.StateRoot
    $env:MAIN_COMPUTER_CONTROL_ROOT = $SelectedMode.ControlRoot
    $env:MAIN_COMPUTER_CONTROL_PORT = "$($SelectedMode.Port)"
    $env:MAIN_COMPUTER_HEARTBEAT_PORT = "$($SelectedMode.HeartbeatPort)"
    $env:MAIN_COMPUTER_EXECUTOR_ENABLED = "1"
    $env:MAIN_COMPUTER_EXECUTOR_BACKEND = "wsl"
    $env:MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION = $SelectedMode.Distribution
    $env:MAIN_COMPUTER_EXECUTOR_WSL_COMMAND = $WslCommand
    $env:MAIN_COMPUTER_EXECUTOR_WSL_RUNTIME_ROOT = $SelectedMode.WslRuntimeRoot
    $env:MAIN_COMPUTER_EXECUTOR_ROOT = $SelectedMode.ExecutorRoot
    $env:MAIN_COMPUTER_PATH_MODE = "local"
    $env:MAIN_COMPUTER_HOST_OS = "windows"
    $env:MAIN_COMPUTER_GITEA_SCOPE = "shared-machine"
    $env:MAIN_COMPUTER_GITEA_ROOT_URL = "http://127.0.0.1:3000/"
    $env:MAIN_COMPUTER_GITEA_HTTP_PORT = "3000"
    $env:MAIN_COMPUTER_GITEA_COMPOSE_PROJECT = "main-computer-gitea"

    $env:MAIN_COMPUTER_LOCAL_SERVER_ENABLED = "1"
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_MODE = $SelectedMode.Key
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT = $SelectedMode.LocalServerProject
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH = $SelectedMode.LocalServerRegistry
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH = $SelectedMode.LocalServerCompose
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_BUILTIN_PORT_START = "$($SelectedMode.LocalServerPortStart)"
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START = "$($SelectedMode.LocalServerGeneratedPortStart)"
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END = "$($SelectedMode.LocalServerGeneratedPortEnd)"
    $env:MAIN_COMPUTER_LOCAL_SERVER_URL = "http://127.0.0.1:$($SelectedMode.LocalServerPortStart)/"

    $env:MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED = "1"
    $env:MAIN_COMPUTER_COOLIFY_LOCAL_URL = "http://127.0.0.1:$($SelectedMode.CoolifyPort)"
    $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_REF = "MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN"
    $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE = Join-Path $SelectedMode.CoolifyStateRoot "api-token.txt"
    $env:MAIN_COMPUTER_COOLIFY_PROJECT = $SelectedMode.CoolifyProject
    $env:MAIN_COMPUTER_COOLIFY_STATE_DIR = $SelectedMode.CoolifyStateRoot
    $env:MAIN_COMPUTER_COOLIFY_APP_PORT = "$($SelectedMode.CoolifyPort)"
    $env:MAIN_COMPUTER_COOLIFY_SOKETI_PORT = "$($SelectedMode.CoolifySoketiPort)"
    $env:MAIN_COMPUTER_COOLIFY_SOKETI_TERMINAL_PORT = "$($SelectedMode.CoolifySoketiTerminalPort)"
    if (Test-Path -LiteralPath $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE -PathType Leaf) {
        $tokenValue = ""
        foreach ($line in (Get-Content -LiteralPath $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE)) {
            if ($line -match '^\s*token\s*=\s*(.+?)\s*$') {
                $tokenValue = $Matches[1].Trim()
                break
            }
        }
        if ([string]::IsNullOrWhiteSpace($tokenValue)) {
            $rawTokenFile = (Get-Content -LiteralPath $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE -Raw).Trim()
            if (
                -not [string]::IsNullOrWhiteSpace($rawTokenFile) -and
                $rawTokenFile -notmatch "`n" -and
                $rawTokenFile -notmatch "=" -and
                $rawTokenFile -notmatch '^\s*#'
            ) {
                $tokenValue = $rawTokenFile
            }
        }
        if (-not [string]::IsNullOrWhiteSpace($tokenValue)) {
            $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN = $tokenValue
        }
    }

    $env:MAIN_COMPUTER_ONLYOFFICE_ENABLED = "1"
    $env:MAIN_COMPUTER_ONLYOFFICE_MODE = "auto"
    $env:MAIN_COMPUTER_ONLYOFFICE_PORT = "$($SelectedMode.OnlyOfficePort)"
    $env:MAIN_COMPUTER_ONLYOFFICE_PROJECT = $SelectedMode.OnlyOfficeProject
    $env:MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL = "http://127.0.0.1:$($SelectedMode.OnlyOfficePort)"
    $env:MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL = "http://127.0.0.1:$($SelectedMode.OnlyOfficePort)"
    $env:MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL = "http://host.docker.internal:$($SelectedMode.Port)"
    if ([string]::IsNullOrWhiteSpace($env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET)) {
        $env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET = "main-computer-onlyoffice-local-secret"
    }
}

function Resolve-ControlAction {
    param([Parameter(Mandatory = $true)][string]$RequestedAction)
    switch ($RequestedAction) {
        "run" { return "start" }
        "stop" { return "shutdown" }
        "install" { return "start" }
        "install-run" { return "start" }
        "smoke" { return "status" }
        default { return $RequestedAction }
    }
}

function Add-CommonControlSwitches {
    param([Parameter(Mandatory = $true)][hashtable]$Params)

    if ($SkipMathicsCheck) {
        $Params.SkipMathicsCheck = $true
    }
    if ($AllowForeignPortListener) {
        $Params.AllowForeignPortListener = $true
    }
}

function Test-CommandOrFileAvailable {
    param([Parameter(Mandatory = $true)][string]$CommandOrPath)

    if ([string]::IsNullOrWhiteSpace($CommandOrPath)) {
        return $false
    }

    if (Test-Path -LiteralPath $CommandOrPath -PathType Leaf) {
        return $true
    }

    $command = Get-Command $CommandOrPath -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    return ($null -ne $command)
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

function Test-QuickHttpGet {
    param([Parameter(Mandatory = $true)][string]$Uri)

    try {
        Invoke-WebRequest -UseBasicParsing -Method GET -Uri $Uri -TimeoutSec 2 | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Test-WslDistributionKnown {
    param([Parameter(Mandatory = $true)][string]$Distribution)

    try {
        $listed = & $WslCommand --list --quiet 2>$null
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        foreach ($line in @($listed)) {
            if (($line.Trim()) -eq $Distribution) {
                return $true
            }
        }
    }
    catch {
        return $false
    }
    return $false
}

function Test-DockerResponding {
    $docker = Get-Command "docker" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $docker) {
        return $false
    }
    try {
        & $docker.Source ps --format "{{.Names}}" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Get-ConfiguredGiteaPort {
    $rootUrl = $env:MAIN_COMPUTER_GITEA_ROOT_URL
    if ([string]::IsNullOrWhiteSpace($rootUrl)) {
        $rootUrl = "http://127.0.0.1:3000/"
    }
    try {
        $uri = [uri]$rootUrl
        if ($uri.Port -gt 0) {
            return [int]$uri.Port
        }
    }
    catch {
    }
    return 3000
}

function Add-ModeCheckResult {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$State,
        [string]$Details = ""
    )

    if ($State -eq "FAIL") {
        $script:ModeCheckFailures += 1
    }
    elseif ($State -eq "WARN") {
        $script:ModeCheckWarnings += 1
    }

    if ([string]::IsNullOrWhiteSpace($Details)) {
        Write-Host ("{0}: {1}" -f $Name, $State)
    }
    else {
        Write-Host ("{0}: {1} - {2}" -f $Name, $State, $Details)
    }
}

function Invoke-DevModeCheck {
    param(
        [Parameter(Mandatory = $true)]$SelectedMode,
        [switch]$Soft
    )

    $script:ModeCheckFailures = 0
    $script:ModeCheckWarnings = 0

    Write-Host ""
    Write-Host "Main Computer quick development environment check"
    Write-Host ("Mode: {0} [{1}]" -f $SelectedMode.Label, $SelectedMode.Key)
    Write-Host ("Repo root: {0}" -f $script:RepoRoot)
    Write-Host ("State root: {0}" -f $SelectedMode.StateRoot)
    Write-Host "Shared services: Ollama and Gitea are machine-wide. Mode services: WSL executor, ONLYOFFICE, Local Server, and Local Coolify."

    if ((Test-Path -LiteralPath (Join-Path $script:RepoRoot "pyproject.toml") -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $script:RepoRoot "main_computer") -PathType Container)) {
        Add-ModeCheckResult "Development checkout" "OK" "repo files found"
    }
    else {
        Add-ModeCheckResult "Development checkout" "FAIL" "run this script from a Main Computer checkout root"
    }

    if (Test-CommandOrFileAvailable -CommandOrPath $SelectedMode.PythonPath) {
        Add-ModeCheckResult "Python for checkout" "OK" $SelectedMode.PythonPath
    }
    else {
        Add-ModeCheckResult "Python for checkout" "FAIL" ("not found: {0}; pass -PythonPath or create .venv" -f $SelectedMode.PythonPath)
    }

    $modeScript = switch ($SelectedMode.Key) {
        "unleashed" { Join-Path $script:RepoRoot "dev-control.ps1" }
        "debug" { Join-Path $script:RepoRoot "proto-dev\proto-dev.ps1" }
        "safe" { Join-Path $script:RepoRoot "control-main-computer.ps1" }
        default { "" }
    }
    if (-not [string]::IsNullOrWhiteSpace($modeScript) -and (Test-Path -LiteralPath $modeScript -PathType Leaf)) {
        Add-ModeCheckResult "Mode runner dependency" "OK" $modeScript
    }
    else {
        Add-ModeCheckResult "Mode runner dependency" "FAIL" ("missing mode control script: {0}" -f $modeScript)
    }

    if (Test-CommandOrFileAvailable -CommandOrPath $WslCommand) {
        if (Test-WslDistributionKnown -Distribution $SelectedMode.Distribution) {
            Add-ModeCheckResult "WSL distro for mode" "OK" $SelectedMode.Distribution
        }
        else {
            Add-ModeCheckResult "WSL distro for mode" "FAIL" ("missing expected development distro: {0}" -f $SelectedMode.Distribution)
        }
    }
    else {
        Add-ModeCheckResult "WSL command" "FAIL" ("not found: {0}" -f $WslCommand)
    }

    if (Test-DockerResponding) {
        Add-ModeCheckResult "Docker" "OK" "docker is responding"
    }
    else {
        Add-ModeCheckResult "Docker" "FAIL" "docker is missing or not responding; mode-scoped Docker services cannot be checked"
    }

    $ollamaBase = $env:OLLAMA_BASE_URL
    if ([string]::IsNullOrWhiteSpace($ollamaBase)) {
        $ollamaBase = "http://127.0.0.1:11434"
    }
    $ollamaBase = $ollamaBase.TrimEnd("/")
    if (Test-QuickHttpGet -Uri "$ollamaBase/api/tags") {
        Add-ModeCheckResult "Ollama shared service" "OK" "$ollamaBase/api/tags"
    }
    else {
        Add-ModeCheckResult "Ollama shared service" "WARN" "not reachable; local model features will wait for the machine-wide Ollama service"
    }

    $giteaPort = Get-ConfiguredGiteaPort
    $giteaRoot = $env:MAIN_COMPUTER_GITEA_ROOT_URL
    if ([string]::IsNullOrWhiteSpace($giteaRoot)) {
        $giteaRoot = "http://127.0.0.1:3000/"
    }
    $giteaHealth = $giteaRoot.TrimEnd("/") + "/api/healthz"
    if ((Test-QuickHttpGet -Uri $giteaHealth) -or (Test-LocalTcpPortOpen -Port $giteaPort)) {
        Add-ModeCheckResult "Gitea shared service" "OK" ("machine-wide Gitea is reachable on port {0}" -f $giteaPort)
    }
    else {
        Add-ModeCheckResult "Gitea shared service" "FAIL" ("machine-wide Gitea is not reachable on port {0}; this is shared across modes, not one Gitea per mode" -f $giteaPort)
    }

    $onlyOfficePort = [int]$env:MAIN_COMPUTER_ONLYOFFICE_PORT
    if (Test-LocalTcpPortOpen -Port $onlyOfficePort) {
        Add-ModeCheckResult "ONLYOFFICE for mode" "OK" ("{0} on port {1}" -f $SelectedMode.OnlyOfficeProject, $onlyOfficePort)
    }
    else {
        Add-ModeCheckResult "ONLYOFFICE for mode" "FAIL" ("not reachable for {0}; expected project {1} on port {2}" -f $SelectedMode.Label, $SelectedMode.OnlyOfficeProject, $onlyOfficePort)
    }

    $coolifyPort = [int]$env:MAIN_COMPUTER_COOLIFY_APP_PORT
    if (Test-LocalTcpPortOpen -Port $coolifyPort) {
        Add-ModeCheckResult "Local Coolify for mode" "OK" ("{0} on port {1}" -f $SelectedMode.CoolifyProject, $coolifyPort)
    }
    else {
        Add-ModeCheckResult "Local Coolify for mode" "FAIL" ("not reachable for {0}; expected project {1} on port {2}" -f $SelectedMode.Label, $SelectedMode.CoolifyProject, $coolifyPort)
    }

    if (Test-Path -LiteralPath $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE -PathType Leaf) {
        Add-ModeCheckResult "Local Coolify token" "OK" $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE
    }
    else {
        Add-ModeCheckResult "Local Coolify token" "WARN" ("missing token file: {0}" -f $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE)
    }

    $registryParent = Split-Path -Parent $env:MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH
    if (-not [string]::IsNullOrWhiteSpace($registryParent) -and (Test-Path -LiteralPath $registryParent -PathType Container)) {
        Add-ModeCheckResult "Local Server state for mode" "OK" $registryParent
    }
    else {
        Add-ModeCheckResult "Local Server state for mode" "WARN" ("state directory not present yet: {0}" -f $registryParent)
    }

    Write-Host ("Quick check summary: {0} failure(s), {1} warning(s)" -f $script:ModeCheckFailures, $script:ModeCheckWarnings)
    if ($script:ModeCheckFailures -gt 0 -and $Soft) {
        Write-Warning "Quick check found missing prerequisites. Start will continue so dev/start helpers can repair services that are designed to be brought up on demand."
    }

    return ($script:ModeCheckFailures -eq 0)
}

function Invoke-UnleashedMode {
    param([Parameter(Mandatory = $true)]$SelectedMode)

    $controlAction = Resolve-ControlAction -RequestedAction $Action
    $devControl = Join-Path $script:RepoRoot "dev-control.ps1"
    if (-not (Test-Path -LiteralPath $devControl -PathType Leaf)) {
        throw "dev-control.ps1 is missing from repo root: $devControl"
    }

    New-Item -ItemType Directory -Force -Path $SelectedMode.ControlRoot | Out-Null
    $devControlParams = @{
        Action = $controlAction
        Mode = "local"
        PythonPath = $SelectedMode.PythonPath
        BindHost = $BindHost
        LocalPort = [int]$SelectedMode.Port
        HeartbeatPort = [int]$SelectedMode.HeartbeatPort
        Workspace = $env:MAIN_COMPUTER_WORKSPACE
        ControlRoot = $SelectedMode.ControlRoot
        StartTimeoutSeconds = [int]$StartTimeoutSeconds
    }
    Add-CommonControlSwitches -Params $devControlParams
    & $devControl @devControlParams
    exit $LASTEXITCODE
}

function Invoke-DebugMode {
    param([Parameter(Mandatory = $true)]$SelectedMode)

    $debugAction = switch ($Action) {
        "start" { if (Test-WslDistributionKnown -Distribution $SelectedMode.Distribution) { "run" } else { "install-run" } }
        "run" { if (Test-WslDistributionKnown -Distribution $SelectedMode.Distribution) { "run" } else { "install-run" } }
        "stop" { "stop" }
        "shutdown" { "stop" }
        default { $Action }
    }

    $proto = Join-Path $script:RepoRoot "proto-dev\proto-dev.ps1"
    if (-not (Test-Path -LiteralPath $proto -PathType Leaf)) {
        throw "proto-dev\proto-dev.ps1 is missing from repo root: $proto"
    }

    New-Item -ItemType Directory -Force -Path $SelectedMode.StateRoot | Out-Null
    $protoParams = @{
        Action = $debugAction
        RepoRoot = $script:RepoRoot
        StateRoot = $SelectedMode.StateRoot
        PythonCommand = $SelectedMode.PythonPath
        Workspace = $env:MAIN_COMPUTER_WORKSPACE
        BindHost = "127.0.0.1"
        Port = [int]$SelectedMode.Port
        HeartbeatPort = [int]$SelectedMode.HeartbeatPort
        WslCommand = $WslCommand
        ExecutorDistribution = $SelectedMode.Distribution
        WslRuntimeRoot = $SelectedMode.WslRuntimeRoot
        StartTimeoutSeconds = [int]$StartTimeoutSeconds
    }
    if ($BuildWslRuntimeIfMissing) {
        $protoParams.BuildWslRuntimeIfMissing = $true
    }
    if ($SkipWslRuntimeInstall) {
        $protoParams.SkipWslRuntimeInstall = $true
    }
    if ($ResetWslRuntime) {
        $protoParams.ResetWslRuntime = $true
    }
    if ($SkipExecutorSmoke) {
        $protoParams.SkipExecutorSmoke = $true
    }
    Add-CommonControlSwitches -Params $protoParams
    & $proto @protoParams
    exit $LASTEXITCODE
}

function Invoke-SafeMode {
    param([Parameter(Mandatory = $true)]$SelectedMode)

    $controlAction = Resolve-ControlAction -RequestedAction $Action
    $control = Join-Path $script:RepoRoot "control-main-computer.ps1"
    if (-not (Test-Path -LiteralPath $control -PathType Leaf)) {
        throw "control-main-computer.ps1 is missing from repo root: $control"
    }

    New-Item -ItemType Directory -Force -Path $SelectedMode.ControlRoot | Out-Null
    $controlParams = @{
        Action = $controlAction
        AutoAllow = $true
        BindHost = $BindHost
        Port = [int]$SelectedMode.Port
        HeartbeatPort = [int]$SelectedMode.HeartbeatPort
        Workspace = $env:MAIN_COMPUTER_WORKSPACE
        PythonPath = $SelectedMode.PythonPath
        ControlRoot = $SelectedMode.ControlRoot
        StartTimeoutSeconds = [int]$StartTimeoutSeconds
    }
    Add-CommonControlSwitches -Params $controlParams
    & $control @controlParams
    exit $LASTEXITCODE
}

Resolve-RunnerArguments
$selectedMode = Resolve-RunnerMode -ModeName $Mode
Set-RunnerEnvironment -SelectedMode $selectedMode

Write-Host ("Main Computer test runner {0}: {1} on http://127.0.0.1:{2} [{3}]" -f $Action, $selectedMode.Label, $selectedMode.Port, $selectedMode.InstanceName)

if ($Action -eq "check") {
    $checkOk = Invoke-DevModeCheck -SelectedMode $selectedMode
    if ($checkOk) { exit 0 }
    exit 2
}

if (@("start", "run", "restart", "install", "install-run") -contains $Action) {
    Invoke-DevModeCheck -SelectedMode $selectedMode -Soft | Out-Null
}

switch ($selectedMode.Key) {
    "unleashed" { Invoke-UnleashedMode -SelectedMode $selectedMode }
    "debug" { Invoke-DebugMode -SelectedMode $selectedMode }
    "safe" { Invoke-SafeMode -SelectedMode $selectedMode }
}
