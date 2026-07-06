# install-main-computer-python-target.ps1
#
# Thin target launcher for the Python-owned Windows installer.
#
# This script is intentionally boring:
#   * choose a named install slot such as debug1, debug2, debug3
#   * set MC_INSTALL / MC_ENV / MC_RUN for that target
#   * call bootstrap-main-computer-python-windows.ps1
#   * remind the operator how to check status afterward

[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $MyInvocation.MyCommand.Path),

    [ValidateSet("test", "prod")]
    [string]$RuntimeProfile = "test",

    [ValidateSet("Unleashed", "Unleashed Mode", "Debug", "Safe", "Safe Mode")]
    [string]$Mode = "Debug",

    [string]$Slot = "debug1",

    [Alias("Target", "TargetRoot", "TargetInstallRoot")]
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

    [string]$WslCommand = "wsl.exe",

    [string]$ExecutorDistribution = "",

    [int]$Port = 8765,

    [int]$HeartbeatPort = 0,

    [int]$SafePort = 38865,

    [int]$SafeHeartbeatPort = 38866,

    [string]$BindHost = "0.0.0.0",

    [string]$Workspace = "",

    [int]$StartTimeoutSeconds = 90,

    [ValidateSet("auto", "disabled", "docker")]
    [string]$OnlyOfficeMode = "auto",

    [ValidateSet("docker", "podman")]
    [string]$ContainerRuntime = "docker",

    [ValidateSet("auto", "disabled", "required")]
    [string]$WslFirewallMode = "auto",

    [ValidateSet("auto", "disabled", "required")]
    [string]$LocalServerMode = "auto",

    [ValidateSet("auto", "disabled", "required")]
    [string]$LocalCoolifyMode = "auto",

    [int]$OnlyOfficePort = 18085,

    [ValidateSet("disabled", "auto", "required")]
    [string]$MathicsInstallMode = "disabled",

    [switch]$InstallOnlyOffice,

    [switch]$SkipWslRuntimeInstall,

    [switch]$BuildWslRuntimeIfMissing,

    [switch]$ResetWslRuntime,

    [switch]$SkipExecutorSmoke,

    [switch]$SkipInstallRootCopy,

    [switch]$SkipRunnerCreation,

    [switch]$SkipAppStart,

    [switch]$SkipMathicsCheck,

    [switch]$AllowForeignPortListener,

    [switch]$PrecheckOnly,

    [Alias("AutoForce", "Replace")]
    [switch]$AutoForceInstall,

    [switch]$NoPythonDownload,

    [switch]$NoReHome,

    [switch]$VerboseBootstrap,

    [Alias("Help")]
    [switch]$HelpRun
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

function Convert-ToSafeName {
    param([Parameter(Mandatory = $true)][string]$Value)

    $builder = New-Object System.Text.StringBuilder
    foreach ($char in $Value.Trim().ToCharArray()) {
        if ([char]::IsLetterOrDigit($char) -or $char -eq "-" -or $char -eq "_") {
            [void]$builder.Append([char]::ToLowerInvariant($char))
        }
        elseif ([char]::IsWhiteSpace($char)) {
            [void]$builder.Append("-")
        }
    }

    $safe = $builder.ToString().Trim("-", "_")
    if ([string]::IsNullOrWhiteSpace($safe)) {
        return "main-computer"
    }

    return $safe
}

function Assert-SafeSlot {
    param([Parameter(Mandatory = $true)][string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        Fail "Slot cannot be empty. Use a name such as debug1, debug2, safe1, or unleashed1."
    }

    if ($Value -match '[\\/:*?"<>|]') {
        Fail "Slot must be a simple name, not a path: $Value"
    }

    if ($Value -eq "." -or $Value -eq "..") {
        Fail "Slot cannot be '.' or '..'."
    }
}

function Convert-ToCommandToken {
    param([Parameter(Mandatory = $true)]$Value)

    $text = [string]$Value
    if ($text -notmatch '[\s"`$&|<>;]') {
        return $text
    }

    return "'" + $text.Replace("'", "''") + "'"
}

function Convert-ToCommandPreview {
    param(
        [Parameter(Mandatory = $true)][string]$CommandPath,
        [Parameter(Mandatory = $true)][hashtable]$Parameters
    )

    $tokens = @((Convert-ToCommandToken $CommandPath))
    foreach ($key in ($Parameters.Keys | Sort-Object)) {
        $value = $Parameters[$key]
        if ($value -is [System.Management.Automation.SwitchParameter] -or $value -is [bool]) {
            if ([bool]$value) {
                $tokens += "-$key"
            }
            continue
        }

        if ($null -ne $value -and -not [string]::IsNullOrWhiteSpace([string]$value)) {
            $tokens += "-$key"
            $tokens += (Convert-ToCommandToken $value)
        }
    }

    return ($tokens -join " ")
}

function Resolve-TargetInstallRoot {
    param(
        [Parameter(Mandatory = $true)][string]$ResolvedRepoRoot,
        [Parameter(Mandatory = $true)][string]$SlotName,
        [string]$ExplicitInstallRoot = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitInstallRoot)) {
        return (Resolve-FullPath $ExplicitInstallRoot)
    }

    $repoName = Convert-ToSafeName (Split-Path -Leaf $ResolvedRepoRoot)
    $safeSlot = Convert-ToSafeName $SlotName
    return (Join-Path (Join-Path (Resolve-UserToolRoot) "installs") "$repoName-$safeSlot")
}

function Resolve-InstanceName {
    param(
        [Parameter(Mandatory = $true)][string]$ResolvedRepoRoot,
        [Parameter(Mandatory = $true)][string]$SlotName,
        [string]$ExplicitInstanceName = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitInstanceName)) {
        return $ExplicitInstanceName
    }

    $repoName = (Convert-ToSafeName (Split-Path -Leaf $ResolvedRepoRoot)).Replace("_", "-")
    $safeSlot = (Convert-ToSafeName $SlotName).Replace("_", "-")
    return "$repoName-$safeSlot"
}

function Show-RunHelp {
    param([string]$ScriptName)

    Write-Host ""
    Write-Host "Main Computer Python target install launcher"
    Write-Host ""
    Write-Host "Install a named debug slot:"
    Write-Host "  .\$ScriptName -Slot debug1 -Mode Debug"
    Write-Host ""
    Write-Host "Install a second slot:"
    Write-Host "  .\$ScriptName -Slot debug2 -Mode Debug"
    Write-Host ""
    Write-Host "Preview target without installing:"
    Write-Host "  .\$ScriptName -Slot debug1 -Mode Debug -PrecheckOnly"
    Write-Host ""
    Write-Host "Use any explicit install directory:"
    Write-Host "  .\$ScriptName -Slot debug1 -Mode Debug -InstallRoot `"D:\mc-targets\debug1`""
    Write-Host "  .\$ScriptName -Slot debug12 -Mode Debug -InstallRoot `"`$env:USERPROFILE\dsl\main_computer_debug12`""
    Write-Host ""
    Write-Host "Or keep the command compact with an env target:"
    Write-Host '  $env:MC_INSTALL = "$env:USERPROFILE\dsl\main_computer_debug12"'
    Write-Host "  .\$ScriptName -Slot debug12 -Mode Debug -InstallRoot `$env:MC_INSTALL"
    Write-Host ""
    Write-Host "Regular-script-compatible knobs are accepted and passed through:"
    Write-Host "  -VenvPath, -WslCommand, -ExecutorDistribution, -Port, -HeartbeatPort"
    Write-Host "  -SafePort, -SafeHeartbeatPort, -BindHost, -Workspace, -StartTimeoutSeconds"
    Write-Host "  -OnlyOfficeMode, -LocalServerMode, -LocalCoolifyMode, -OnlyOfficePort"
    Write-Host "  -InstallOnlyOffice, -SkipWslRuntimeInstall, -BuildWslRuntimeIfMissing"
    Write-Host "  -ResetWslRuntime, -SkipExecutorSmoke, -SkipMathicsCheck"
    Write-Host "  -AllowForeignPortListener, -SkipRunnerCreation, -NoReHome"
    Write-Host ""
    Write-Host "Aliases for -InstallRoot:"
    Write-Host "  -Target, -TargetRoot, -TargetInstallRoot"
    Write-Host ""
    Write-Host "After install, status is:"
    Write-Host "  . `$env:MC_ENV"
    Write-Host "  & `$env:MC_RUN -Action status"
    Write-Host ""
    Write-Host "Show this reminder:"
    Write-Host "  .\$ScriptName -HelpRun"
    Write-Host ""
}

$scriptName = Split-Path -Leaf $MyInvocation.MyCommand.Path
if ($HelpRun) {
    Show-RunHelp -ScriptName $scriptName
    exit 0
}

Assert-SafeSlot -Value $Slot

$resolvedRepoRoot = Resolve-FullPath $RepoRoot
if (-not (Test-Path -LiteralPath $resolvedRepoRoot -PathType Container)) {
    Fail "Repo root does not exist: $resolvedRepoRoot"
}

$bootstrapScript = Join-Path $resolvedRepoRoot "bootstrap-main-computer-python-windows.ps1"
if (-not (Test-Path -LiteralPath $bootstrapScript -PathType Leaf)) {
    Fail "Python Windows installer is missing: $bootstrapScript"
}

$targetInstallRoot = Resolve-TargetInstallRoot `
    -ResolvedRepoRoot $resolvedRepoRoot `
    -SlotName $Slot `
    -ExplicitInstallRoot $InstallRoot

$targetSource = "slot:$Slot"
if (-not [string]::IsNullOrWhiteSpace($InstallRoot)) {
    $targetSource = "-InstallRoot"
}

$resolvedInstanceName = Resolve-InstanceName `
    -ResolvedRepoRoot $resolvedRepoRoot `
    -SlotName $Slot `
    -ExplicitInstanceName $InstanceName

$env:MC_INSTALL = $targetInstallRoot
$env:MC_ENV = Join-Path $env:MC_INSTALL "main-computer-env.ps1"
$env:MC_RUN = Join-Path $env:MC_INSTALL $RunnerName
$env:MAIN_COMPUTER_INSTALL_ROOT = $env:MC_INSTALL

$installerParams = @{
    RepoRoot = $resolvedRepoRoot
    RuntimeProfile = $RuntimeProfile
    Mode = $Mode
    InstallRoot = $targetInstallRoot
    RunnerName = $RunnerName
    InstanceName = $resolvedInstanceName
    WslCommand = $WslCommand
    SafePort = $SafePort
    SafeHeartbeatPort = $SafeHeartbeatPort
    BindHost = $BindHost
    StartTimeoutSeconds = $StartTimeoutSeconds
    OnlyOfficeMode = $OnlyOfficeMode
    ContainerRuntime = $ContainerRuntime
    WslFirewallMode = $WslFirewallMode
    LocalServerMode = $LocalServerMode
    LocalCoolifyMode = $LocalCoolifyMode
    MathicsInstallMode = $MathicsInstallMode
    PythonNuGetVersion = $PythonNuGetVersion
    PipWheelVersion = $PipWheelVersion
}

if (-not [string]::IsNullOrWhiteSpace($InstanceStoreRoot)) {
    $installerParams.InstanceStoreRoot = Resolve-FullPath $InstanceStoreRoot
}
if (-not [string]::IsNullOrWhiteSpace($VenvPath)) {
    $installerParams.VenvPath = Resolve-FullPath $VenvPath
}
if (-not [string]::IsNullOrWhiteSpace($PythonCommand)) {
    $installerParams.PythonCommand = $PythonCommand
}
if (-not [string]::IsNullOrWhiteSpace($ManagedPythonRoot)) {
    $installerParams.ManagedPythonRoot = Resolve-FullPath $ManagedPythonRoot
}
if (-not [string]::IsNullOrWhiteSpace($PythonDownloadRoot)) {
    $installerParams.PythonDownloadRoot = Resolve-FullPath $PythonDownloadRoot
}
if (-not [string]::IsNullOrWhiteSpace($ExecutorDistribution)) {
    $installerParams.ExecutorDistribution = $ExecutorDistribution
}
if ($PSBoundParameters.ContainsKey("Port")) {
    $installerParams.Port = $Port
}
if ($HeartbeatPort -gt 0) {
    $installerParams.HeartbeatPort = $HeartbeatPort
}
if (-not [string]::IsNullOrWhiteSpace($Workspace)) {
    $installerParams.Workspace = Resolve-FullPath $Workspace
}
if ($PSBoundParameters.ContainsKey("OnlyOfficePort")) {
    $installerParams.OnlyOfficePort = $OnlyOfficePort
}
if ($PrecheckOnly) {
    $installerParams.PrecheckOnly = $true
}
if ($AutoForceInstall) {
    $installerParams.AutoForceInstall = $true
}
if ($NoPythonDownload) {
    $installerParams.NoPythonDownload = $true
}
if ($NoReHome) {
    $installerParams.NoReHome = $true
}
if ($InstallOnlyOffice) {
    $installerParams.InstallOnlyOffice = $true
}
if ($SkipWslRuntimeInstall) {
    $installerParams.SkipWslRuntimeInstall = $true
}
if ($BuildWslRuntimeIfMissing) {
    $installerParams.BuildWslRuntimeIfMissing = $true
}
if ($ResetWslRuntime) {
    $installerParams.ResetWslRuntime = $true
}
if ($SkipExecutorSmoke) {
    $installerParams.SkipExecutorSmoke = $true
}
if ($VerboseBootstrap) {
    $installerParams.VerboseBootstrap = $true
}
if ($SkipInstallRootCopy) {
    $installerParams.SkipInstallRootCopy = $true
}
if ($SkipRunnerCreation) {
    $installerParams.SkipRunnerCreation = $true
}
if ($SkipAppStart) {
    $installerParams.SkipAppStart = $true
}
if ($SkipMathicsCheck) {
    $installerParams.SkipMathicsCheck = $true
}
if ($AllowForeignPortListener) {
    $installerParams.AllowForeignPortListener = $true
}

Write-Host ""
Write-Host "Main Computer Python install target prepared:"
Write-Host "  slot:          $Slot"
Write-Host "  mode:          $Mode"
Write-Host "  repo root:     $resolvedRepoRoot"
Write-Host "  install dir:   $env:MC_INSTALL"
Write-Host "  target source: $targetSource"
Write-Host "  env header:    $env:MC_ENV"
Write-Host "  runner:        $env:MC_RUN"
Write-Host "  instance name: $resolvedInstanceName"
Write-Host ""
Write-Host "Calling installer:"
Write-Host "  $(Convert-ToCommandPreview -CommandPath $bootstrapScript -Parameters $installerParams)"
Write-Host ""
Write-Host "Reminder:"
Write-Host "  . $env:MC_ENV"
Write-Host "  & `$env:MC_RUN -Action status"
Write-Host ""

& $bootstrapScript @installerParams
$installerExitCode = $LASTEXITCODE
if ($null -eq $installerExitCode) {
    $installerExitCode = 0
}

if ([int]$installerExitCode -eq 0) {
    Write-Host ""
    if ($PrecheckOnly) {
        Write-Host "Precheck complete. Install was not run."
    }
    else {
        Write-Host "Install command completed."
    }
    Write-Host "Target env remains set in this PowerShell session:"
    Write-Host "  `$env:MC_INSTALL = $env:MC_INSTALL"
    Write-Host "  `$env:MC_ENV     = $env:MC_ENV"
    Write-Host "  `$env:MC_RUN     = $env:MC_RUN"
    Write-Host ""
    Write-Host "Status:"
    Write-Host "  . `$env:MC_ENV"
    Write-Host "  & `$env:MC_RUN -Action status"
    Write-Host ""
    Write-Host "Help reminder:"
    Write-Host "  .\$scriptName -HelpRun"
}

exit ([int]$installerExitCode)
