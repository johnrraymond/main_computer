# bootstrap-main-computer-python-windows.ps1
#
# Boring Windows stage-0 launcher for the Python-owned Main Computer bootstrap.
#
# Responsibilities:
#   * resolve the repository root
#   * provision or locate the managed NuGet CPython payload
#   * verify that exact python.exe with a tiny sys probe
#   * invoke tools\bootstrap_main_computer.py with that python.exe
#   * return the Python driver's exit code
#
# The installer brain lives in Python, not here.

[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $MyInvocation.MyCommand.Path),

    [ValidateSet("test", "prod")]
    [string]$RuntimeProfile = "test",

    [ValidateSet("Unleashed", "Unleashed Mode", "Debug", "Safe", "Safe Mode")]
    [string]$Mode = "Unleashed",

    [string]$InstallRoot = "",

    [string]$RunnerName = "run-main-computer.ps1",

    [string]$InstanceName = "",

    [string]$InstanceStoreRoot = "",

    [string]$VenvPath = "",

    [string]$PythonCommand = "",

    [string]$ManagedPythonRoot = "",

    [string]$PythonDownloadRoot = "",

    [string]$PythonNuGetVersion = "3.12.10",

    [string]$PipWheelVersion = "25.0.1",

    [switch]$NoPythonDownload,

    [switch]$NoReHome,

    [switch]$AllowWindowsAppsPython,

    [switch]$ProvisionPythonInPrecheck,

    [string]$WslCommand = "wsl.exe",

    [string]$ExecutorDistribution = "",

    [int]$Port = 8765,

    [int]$HeartbeatPort = 0,

    [int]$SafePort = 38865,

    [int]$SafeHeartbeatPort = 38866,

    [string]$BindHost = "0.0.0.0",

    [string]$Workspace = "",

    [int]$StartTimeoutSeconds = 90,

    [int]$PrecheckCommandTimeoutSeconds = 15,

    [int]$PrecheckFirewallTimeoutSeconds = 20,

    [ValidateSet("auto", "disabled", "wsl", "docker")]
    [string]$OnlyOfficeMode = "auto",

    [ValidateSet("auto", "disabled", "required")]
    [string]$WslFirewallMode = "auto",

    [ValidateSet("auto", "disabled", "required")]
    [string]$LocalServerMode = "auto",

    [ValidateSet("auto", "disabled", "required")]
    [string]$LocalCoolifyMode = "auto",

    [int]$OnlyOfficePort = 18084,

    [switch]$InstallOnlyOffice,

    [switch]$EnsureRenderer,

    [switch]$SkipDependencyInstall,

    [switch]$SkipWslRuntimeInstall,

    [switch]$BuildWslRuntimeIfMissing,

    [switch]$ResetWslRuntime,

    [switch]$SkipAppStart,

    [switch]$SkipExecutorSmoke,

    [switch]$SkipMathicsCheck,

    [ValidateSet("disabled", "auto", "required")]
    [string]$MathicsInstallMode = "disabled",

    [switch]$AllowForeignPortListener,

    [switch]$SkipInstallRootCopy,

    [switch]$SkipRunnerCreation,

    [switch]$PrecheckOnly,

    [switch]$SkipWslFirewallRule,

    [switch]$SkipOllamaCheck,

    [Alias("AutoForce", "auto-force")]
    [switch]$AutoForceInstall,

    [switch]$VerboseBootstrap,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingBootstrapArgs = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    throw "[FAIL] $Message"
}

function Resolve-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
}

function Resolve-ExplicitPythonCommand {
    param([Parameter(Mandatory = $true)][string]$Command)

    if ([string]::IsNullOrWhiteSpace($Command)) {
        return ""
    }

    if ([System.IO.Path]::IsPathRooted($Command) -or $Command.Contains("\") -or $Command.Contains("/")) {
        $candidate = Resolve-FullPath $Command
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            Fail "Explicit -PythonCommand does not exist: $candidate"
        }
        if (-not (Test-ManagedPython -PythonPath $candidate)) {
            Fail "Explicit -PythonCommand failed the managed Python validation probe: $candidate"
        }
        return $candidate
    }

    $command = Get-Command $Command -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command -or [string]::IsNullOrWhiteSpace($command.Source)) {
        Fail "Explicit -PythonCommand could not be resolved: $Command"
    }

    $candidate = Resolve-FullPath $command.Source
    if (-not (Test-ManagedPython -PythonPath $candidate)) {
        Fail "Explicit -PythonCommand failed the managed Python validation probe: $candidate"
    }
    return $candidate
}

function Resolve-UserToolRoot {
    $userProfileRoot = [Environment]::GetFolderPath("UserProfile")
    if ([string]::IsNullOrWhiteSpace($userProfileRoot)) {
        $userProfileRoot = $env:USERPROFILE
    }
    if ([string]::IsNullOrWhiteSpace($userProfileRoot)) {
        Fail "Could not determine the user profile directory for the Main Computer tool cache."
    }
    return (Join-Path (Resolve-FullPath $userProfileRoot) ".main-computer-tools")
}

function Invoke-InstallerReHome {
    param([Parameter(Mandatory = $true)][string]$SourceRoot)

    $sourceFull = Resolve-FullPath $SourceRoot
    $exportScript = Join-Path $sourceFull "export-main-computer-test.ps1"
    if (-not (Test-Path -LiteralPath $exportScript -PathType Leaf)) {
        Fail "Clean export script is missing; cannot perform installer rehome: $exportScript"
    }

    Write-Host ""
    Write-Host "Installer rehome: creating a clean exported repo before bootstrap."
    Write-Host "  source: $sourceFull"
    Write-Host "  export: $exportScript"

    $output = @(& $exportScript -SourceRoot $sourceFull -InstallerReHome)
    $reHomeRoot = @(
        $output |
            ForEach-Object { [string]$_ } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    ) | Select-Object -Last 1

    if ([string]::IsNullOrWhiteSpace($reHomeRoot)) {
        Fail "Clean export script did not return an installer rehome root."
    }

    $resolvedReHomeRoot = Resolve-FullPath $reHomeRoot
    if (-not (Test-Path -LiteralPath $resolvedReHomeRoot -PathType Container)) {
        Fail "Installer rehome root does not exist: $resolvedReHomeRoot"
    }

    $driverPath = Join-Path $resolvedReHomeRoot "tools\bootstrap_main_computer.py"
    if (-not (Test-Path -LiteralPath $driverPath -PathType Leaf)) {
        Fail "Installer rehome root is missing the Python bootstrap driver: $driverPath"
    }

    Write-Host "Installer rehome: continuing from clean exported repo."
    Write-Host "  rehome root: $resolvedReHomeRoot"
    Write-Host ""

    return $resolvedReHomeRoot
}

function Resolve-CpythonRegistryRoot {
    return (Join-Path (Resolve-UserToolRoot) "cpython")
}

function Resolve-PythonDownloadRoot {
    if (-not [string]::IsNullOrWhiteSpace($PythonDownloadRoot)) {
        return (Resolve-FullPath $PythonDownloadRoot)
    }
    return (Join-Path (Resolve-CpythonRegistryRoot) "downloads")
}

function Get-ManagedPythonCurrentPointerPath {
    if (-not [string]::IsNullOrWhiteSpace($ManagedPythonRoot)) {
        $parent = Split-Path -Parent (Resolve-FullPath $ManagedPythonRoot)
        if ([string]::IsNullOrWhiteSpace($parent)) {
            $parent = Resolve-CpythonRegistryRoot
        }
        return (Join-Path $parent "current-python.txt")
    }

    return (Join-Path (Resolve-CpythonRegistryRoot) "current-python.txt")
}

function Read-ManagedPythonCurrentPointer {
    $pointerPath = Get-ManagedPythonCurrentPointerPath
    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
        return ""
    }

    try {
        return ([string](Get-Content -LiteralPath $pointerPath -Raw -ErrorAction Stop)).Trim()
    }
    catch {
        return ""
    }
}

function Write-ManagedPythonCurrentPointer {
    param([Parameter(Mandatory = $true)][string]$PythonPath)

    $pointerPath = Get-ManagedPythonCurrentPointerPath
    $parent = Split-Path -Parent $pointerPath
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }

    Set-Content -LiteralPath $pointerPath -Value $PythonPath -Encoding ASCII
    return $pointerPath
}

function Get-PythonNuGetPackageCachePath {
    return (Join-Path (Resolve-PythonDownloadRoot) "python.$PythonNuGetVersion.nupkg")
}

function Download-File {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [int]$TimeoutSeconds = 180
    )

    $parent = Split-Path -Parent $OutFile
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }

    if (Test-Path -LiteralPath $OutFile -PathType Leaf) {
        $existing = Get-Item -LiteralPath $OutFile
        if ($existing.Length -gt 100000) {
            Write-Host "Using cached download: $OutFile"
            return
        }

        if ($NoPythonDownload) {
            Fail "Python download is disabled by -NoPythonDownload, and the cached file is invalid: $OutFile"
        }

        Remove-Item -LiteralPath $OutFile -Force
    }

    if ($NoPythonDownload) {
        Fail "Python download is disabled by -NoPythonDownload, but a required file is not cached: $OutFile"
    }

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    }
    catch {
        # Best effort for older Windows PowerShell hosts.
    }

    Write-Host "Downloading: $Uri"
    Write-Host "       to: $OutFile"

    $request = [System.Net.HttpWebRequest]::Create($Uri)
    $request.Method = "GET"
    $request.Timeout = $TimeoutSeconds * 1000
    $request.ReadWriteTimeout = $TimeoutSeconds * 1000
    $request.AllowAutoRedirect = $true
    $request.UserAgent = "main-computer-python-bootstrap/1.0"

    $response = $null
    $inputStream = $null
    $outputStream = $null
    $timer = [System.Diagnostics.Stopwatch]::StartNew()

    try {
        $response = $request.GetResponse()
        $inputStream = $response.GetResponseStream()
        $outputStream = [System.IO.File]::Open(
            $OutFile,
            [System.IO.FileMode]::Create,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )

        $buffer = New-Object byte[] 1048576
        while ($true) {
            if ($timer.Elapsed.TotalSeconds -gt $TimeoutSeconds) {
                throw "Download timed out after $TimeoutSeconds seconds: $Uri"
            }

            $read = $inputStream.Read($buffer, 0, $buffer.Length)
            if ($read -le 0) {
                break
            }

            $outputStream.Write($buffer, 0, $read)
        }
    }
    finally {
        if ($outputStream) { $outputStream.Dispose() }
        if ($inputStream) { $inputStream.Dispose() }
        if ($response) { $response.Dispose() }
        $timer.Stop()
    }

    if (-not (Test-Path -LiteralPath $OutFile -PathType Leaf)) {
        Fail "Download did not create expected file: $OutFile"
    }

    $item = Get-Item -LiteralPath $OutFile
    if ($item.Length -lt 100000) {
        Fail "Downloaded file looks too small: $OutFile ($($item.Length) bytes)"
    }
}

function Expand-ZipArchiveToDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$ZipPath,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $Destination)
}

function Copy-DirectoryChildren {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        Fail "Source directory does not exist: $Source"
    }

    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $Destination $item.Name) -Recurse -Force
    }
}

function Test-ManagedPython {
    param([Parameter(Mandatory = $true)][string]$PythonPath)

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $false
    }

    Write-Host "Verifying managed Python:"
    Write-Host "  $PythonPath"
    & $PythonPath -c "import sys; print(sys.executable); print(sys.version); raise SystemExit(0 if sys.version_info >= (3, 10) and sys.prefix == sys.base_prefix else 1)"
    return ($LASTEXITCODE -eq 0)
}

function Install-ManagedPythonFromNuGet {
    $registryRoot = Resolve-CpythonRegistryRoot
    if (-not (Test-Path -LiteralPath $registryRoot -PathType Container)) {
        New-Item -ItemType Directory -Force -Path $registryRoot | Out-Null
    }

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stageExtractRoot = Join-Path (Resolve-PythonDownloadRoot) ("python-nuget-extract-$PythonNuGetVersion-$timestamp-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
    if (-not [string]::IsNullOrWhiteSpace($ManagedPythonRoot)) {
        $versionedRoot = Resolve-FullPath $ManagedPythonRoot
    }
    else {
        $versionedRoot = Join-Path $registryRoot "$PythonNuGetVersion-nuget-amd64-$timestamp"
    }

    $packagePath = Get-PythonNuGetPackageCachePath
    $packageUrl = "https://www.nuget.org/api/v2/package/python/$PythonNuGetVersion"

    try {
        Write-Host "Provisioning managed CPython from official NuGet package."
        Write-Host "Python package URL: $packageUrl"
        Write-Host "Python package cache: $packagePath"
        Write-Host "Managed Python target: $versionedRoot"

        Download-File -Uri $packageUrl -OutFile $packagePath -TimeoutSeconds 180
        Expand-ZipArchiveToDirectory -ZipPath $packagePath -Destination $stageExtractRoot

        $toolsRoot = Join-Path $stageExtractRoot "tools"
        if (-not (Test-Path -LiteralPath $toolsRoot -PathType Container)) {
            Fail "Python NuGet package did not contain a tools directory: $toolsRoot"
        }

        $packagePython = Join-Path $toolsRoot "python.exe"
        if (-not (Test-Path -LiteralPath $packagePython -PathType Leaf)) {
            Fail "Python NuGet package did not contain tools\python.exe: $packagePython"
        }

        Copy-DirectoryChildren -Source $toolsRoot -Destination $versionedRoot

        $pythonExe = Join-Path $versionedRoot "python.exe"
        if (-not (Test-ManagedPython -PythonPath $pythonExe)) {
            Fail "Managed CPython was provisioned but failed validation: $pythonExe"
        }

        $pointerPath = Write-ManagedPythonCurrentPointer -PythonPath $pythonExe
        Write-Host "Managed Python pointer: $pointerPath"
        return $pythonExe
    }
    finally {
        if (Test-Path -LiteralPath $stageExtractRoot) {
            Remove-Item -LiteralPath $stageExtractRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Resolve-ManagedPython {
    if (-not [string]::IsNullOrWhiteSpace($PythonCommand)) {
        $explicitPython = Resolve-ExplicitPythonCommand -Command $PythonCommand
        Write-Host "Using explicit Python command for stage-0 handoff: $explicitPython"
        return $explicitPython
    }

    $currentPointer = Read-ManagedPythonCurrentPointer
    if (-not [string]::IsNullOrWhiteSpace($currentPointer)) {
        try {
            $candidate = Resolve-FullPath $currentPointer
            if (Test-ManagedPython -PythonPath $candidate) {
                return $candidate
            }
        }
        catch {
            Write-Host "Ignoring stale managed Python pointer: $currentPointer"
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($ManagedPythonRoot)) {
        $candidate = Join-Path (Resolve-FullPath $ManagedPythonRoot) "python.exe"
        if (Test-ManagedPython -PythonPath $candidate) {
            Write-ManagedPythonCurrentPointer -PythonPath $candidate | Out-Null
            return $candidate
        }
    }

    return (Install-ManagedPythonFromNuGet)
}

if ($RemainingBootstrapArgs.Count -gt 0) {
    foreach ($arg in $RemainingBootstrapArgs) {
        if ($arg -eq "--auto-force") {
            $AutoForceInstall = $true
        }
        else {
            Fail "Unknown bootstrap argument: $arg"
        }
    }
}

$bootstrapExitCode = 0
$reHomeLocationPushed = $false

try {
    $resolvedRepoRoot = Resolve-FullPath $RepoRoot

    if ($NoReHome) {
        Write-Host "Installer rehome disabled by -NoReHome; using requested repo root."
    }
    else {
        $resolvedRepoRoot = Invoke-InstallerReHome -SourceRoot $resolvedRepoRoot
        Push-Location -LiteralPath $resolvedRepoRoot
        $reHomeLocationPushed = $true
    }

    $driverPath = Join-Path $resolvedRepoRoot "tools\bootstrap_main_computer.py"
    if (-not (Test-Path -LiteralPath $driverPath -PathType Leaf)) {
        Fail "Python bootstrap driver is missing: $driverPath"
    }

    $pythonExe = Resolve-ManagedPython

    $installTargetSource = "managed default selected by Python"
    $installTargetDisplay = ""
    if (-not [string]::IsNullOrWhiteSpace($InstallRoot)) {
        $installTargetDisplay = Resolve-FullPath $InstallRoot
        $installTargetSource = "-InstallRoot"
    }
    elseif (-not [string]::IsNullOrWhiteSpace($env:MC_INSTALL)) {
        $installTargetDisplay = Resolve-FullPath $env:MC_INSTALL
        $installTargetSource = "env:MC_INSTALL"
    }
    elseif (-not [string]::IsNullOrWhiteSpace($env:MAIN_COMPUTER_INSTALL_ROOT)) {
        $installTargetDisplay = Resolve-FullPath $env:MAIN_COMPUTER_INSTALL_ROOT
        $installTargetSource = "env:MAIN_COMPUTER_INSTALL_ROOT"
    }

    $driverArgs = @(
        $driverPath,
        "--repo-root", $resolvedRepoRoot,
        "--runtime-profile", $RuntimeProfile,
        "--mode", $Mode,
        "--runner-name", $RunnerName,
        "--wsl-command", $WslCommand,
        "--safe-port", ([string]$SafePort),
        "--safe-heartbeat-port", ([string]$SafeHeartbeatPort),
        "--bind-host", $BindHost,
        "--start-timeout-seconds", ([string]$StartTimeoutSeconds),
        "--onlyoffice-mode", $OnlyOfficeMode,
        "--local-server-mode", $LocalServerMode,
        "--local-coolify-mode", $LocalCoolifyMode,
        "--wsl-firewall-mode", $WslFirewallMode,
        "--mathics-install-mode", $MathicsInstallMode,
        "--managed-python", $pythonExe,
        "--python-nuget-version", $PythonNuGetVersion,
        "--pip-wheel-version", $PipWheelVersion
    )

    if (-not [string]::IsNullOrWhiteSpace($InstallRoot)) {
        $driverArgs += @("--install-root", (Resolve-FullPath $InstallRoot))
    }
    if (-not [string]::IsNullOrWhiteSpace($InstanceName)) {
        $driverArgs += @("--instance-name", $InstanceName)
    }
    if (-not [string]::IsNullOrWhiteSpace($InstanceStoreRoot)) {
        $driverArgs += @("--instance-store-root", (Resolve-FullPath $InstanceStoreRoot))
    }
    if (-not [string]::IsNullOrWhiteSpace($VenvPath)) {
        $driverArgs += @("--venv-path", (Resolve-FullPath $VenvPath))
    }
    if (-not [string]::IsNullOrWhiteSpace($ExecutorDistribution)) {
        $driverArgs += @("--executor-distribution", $ExecutorDistribution)
    }
    if ($PSBoundParameters.ContainsKey("Port")) {
        $driverArgs += @("--port", ([string]$Port))
    }
    if ($HeartbeatPort -gt 0) {
        $driverArgs += @("--heartbeat-port", ([string]$HeartbeatPort))
    }
    if (-not [string]::IsNullOrWhiteSpace($Workspace)) {
        $driverArgs += @("--workspace", (Resolve-FullPath $Workspace))
    }
    if ($PSBoundParameters.ContainsKey("OnlyOfficePort")) {
        $driverArgs += @("--onlyoffice-port", ([string]$OnlyOfficePort))
    }
    if ($PrecheckOnly) {
        $driverArgs += "--precheck-only"
    }
    if ($AutoForceInstall) {
        $driverArgs += "--auto-force-install"
    }
    if ($VerboseBootstrap) {
        $driverArgs += "--verbose"
    }
    if ($NoPythonDownload) {
        $driverArgs += "--no-python-download"
    }
    if ($InstallOnlyOffice) {
        $driverArgs += "--install-onlyoffice"
    }
    if ($SkipWslRuntimeInstall) {
        $driverArgs += "--skip-wsl-runtime-install"
    }
    if ($BuildWslRuntimeIfMissing) {
        $driverArgs += "--build-wsl-runtime-if-missing"
    }
    if ($ResetWslRuntime) {
        $driverArgs += "--reset-wsl-runtime"
    }
    if ($SkipExecutorSmoke) {
        $driverArgs += "--skip-executor-smoke"
    }
    if ($SkipInstallRootCopy) {
        $driverArgs += "--skip-install-root-copy"
    }
    if ($SkipRunnerCreation) {
        $driverArgs += "--skip-runner-creation"
    }
    if ($SkipAppStart) {
        $driverArgs += "--skip-app-start"
    }
    if ($SkipMathicsCheck) {
        $driverArgs += "--skip-mathics-check"
    }
    if ($AllowForeignPortListener) {
        $driverArgs += "--allow-foreign-port-listener"
    }

    Write-Host ""
    Write-Host "Handing off to Python bootstrap driver:"
    Write-Host "  python: $pythonExe"
    Write-Host "  driver: $driverPath"
    Write-Host "  repo root: $resolvedRepoRoot"
    Write-Host "  cwd: $(Get-Location)"
    if ([string]::IsNullOrWhiteSpace($installTargetDisplay)) {
        Write-Host "  install target: $installTargetSource"
    }
    else {
        Write-Host "  install target: $installTargetDisplay"
        Write-Host "  target source: $installTargetSource"
    }
    Write-Host ""

    & $pythonExe @driverArgs
    $driverExitCode = $LASTEXITCODE
    if ($null -eq $driverExitCode) {
        $driverExitCode = 0
    }

    $bootstrapExitCode = [int]$driverExitCode
}
finally {
    if ($reHomeLocationPushed) {
        Pop-Location
    }
}

exit $bootstrapExitCode
