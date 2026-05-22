# bootstrap-main-computer-windows.ps1
#
# Windows-first Main Computer bootstrapper.
#
# Primary runtime: native Windows Python process.
# Preferred executor: WSL.
# Docker: only for explicitly requested service containers, never the default app runtime.

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

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingBootstrapArgs = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$script:BootstrapStatus = New-Object System.Collections.Generic.List[object]
$script:PrecheckFailed = $false
$script:PrecheckActive = $false
$script:PrecheckStepCounter = 0
$script:PrecheckPython = ""
$script:WslHostGatewayIp = ""

function Write-Section {
    param([Parameter(Mandatory = $true)][string]$Title)

    Write-Host ""
    Write-Host $Title
    Write-Host ("-" * $Title.Length)
}

function Add-BootstrapStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$State,
        [string]$Details = ""
    )

    $script:BootstrapStatus.Add([pscustomobject]@{
        Name = $Name
        State = $State
        Details = $Details
    }) | Out-Null
}

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    throw "[FAIL] $Message"
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

function Resolve-CommandPaths {
    param([string]$CommandName)

    if ([string]::IsNullOrWhiteSpace($CommandName)) {
        return @()
    }

    $paths = @()
    $hasPathSeparator = $CommandName.Contains("\") -or $CommandName.Contains("/") -or [System.IO.Path]::IsPathRooted($CommandName)
    if (-not $hasPathSeparator) {
        $commands = @(Get-Command $CommandName -CommandType Application -All -ErrorAction SilentlyContinue)
        foreach ($command in $commands) {
            if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
                $paths += $command.Source
            }
        }
        return @($paths | Select-Object -Unique)
    }

    if (Test-Path -LiteralPath $CommandName -PathType Leaf) {
        return @((Resolve-Path -LiteralPath $CommandName).Path)
    }

    return @()
}

function Resolve-CommandPath {
    param([string]$CommandName)

    $paths = @(Resolve-CommandPaths $CommandName)
    if (@($paths).Count -gt 0) {
        return $paths[0]
    }

    return $null
}

function ConvertTo-NativeArgument {
    param([AllowNull()][string]$Argument)

    if ($null -eq $Argument -or $Argument.Length -eq 0) {
        return '""'
    }

    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }

    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0

    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes += 1
            continue
        }

        if ($character -eq '"') {
            if ($backslashes -gt 0) {
                [void]$builder.Append('\' * ($backslashes * 2))
                $backslashes = 0
            }
            [void]$builder.Append('\"')
            continue
        }

        if ($backslashes -gt 0) {
            [void]$builder.Append('\' * $backslashes)
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }

    if ($backslashes -gt 0) {
        [void]$builder.Append('\' * ($backslashes * 2))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Join-NativeArgumentList {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    return (($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join " ")
}

function Format-NativeCommandLine {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    return "$FilePath $(Join-NativeArgumentList -Arguments $Arguments)"
}

function Write-NativeCommandPreview {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = "",
        [int]$TimeoutSeconds = 0
    )

    Write-Host "$Label command:"
    Write-Host "  $(Format-NativeCommandLine -FilePath $FilePath -Arguments $Arguments)"
    if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
        Write-Host "Working directory:"
        Write-Host "  $WorkingDirectory"
    }
    if ($TimeoutSeconds -gt 0) {
        Write-Host "Timeout seconds: $TimeoutSeconds"
    }
}

function Invoke-NativeCheckedWithPreview {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = "",
        [int]$TimeoutSeconds = 0
    )

    Write-NativeCommandPreview -Label $Label -FilePath $FilePath -Arguments $Arguments -WorkingDirectory $WorkingDirectory -TimeoutSeconds $TimeoutSeconds
    return (Invoke-NativeChecked -FilePath $FilePath -Arguments $Arguments -WorkingDirectory $WorkingDirectory -TimeoutSeconds $TimeoutSeconds)
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$Quiet,
        [string]$WorkingDirectory = "",
        [int]$TimeoutSeconds = 0
    )

    $commandLine = Format-NativeCommandLine -FilePath $FilePath -Arguments $Arguments
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $FilePath
    $psi.Arguments = (Join-NativeArgumentList -Arguments $Arguments)
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
        $psi.WorkingDirectory = $WorkingDirectory
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $psi

    $stdoutBuilder = [System.Text.StringBuilder]::new()
    $stderrBuilder = [System.Text.StringBuilder]::new()
    $stdoutLock = [object]::new()
    $stderrLock = [object]::new()
    $stdoutHandler = $null
    $stderrHandler = $null

    try {
        $stdoutHandler = [System.Diagnostics.DataReceivedEventHandler]{
            param($sender, $eventArgs)

            if ($null -ne $eventArgs.Data) {
                [System.Threading.Monitor]::Enter($stdoutLock)
                try {
                    [void]$stdoutBuilder.AppendLine([string]$eventArgs.Data)
                }
                finally {
                    [System.Threading.Monitor]::Exit($stdoutLock)
                }

                if (-not $Quiet) {
                    Write-Host $eventArgs.Data
                }
            }
        }

        $stderrHandler = [System.Diagnostics.DataReceivedEventHandler]{
            param($sender, $eventArgs)

            if ($null -ne $eventArgs.Data) {
                [System.Threading.Monitor]::Enter($stderrLock)
                try {
                    [void]$stderrBuilder.AppendLine([string]$eventArgs.Data)
                }
                finally {
                    [System.Threading.Monitor]::Exit($stderrLock)
                }

                if (-not $Quiet) {
                    Write-Host $eventArgs.Data
                }
            }
        }

        $process.add_OutputDataReceived($stdoutHandler)
        $process.add_ErrorDataReceived($stderrHandler)

        if (-not $process.Start()) {
            return [pscustomobject]@{
                ExitCode = 127
                Stdout = ""
                Stderr = "Process did not start: $commandLine"
                TimedOut = $false
            }
        }

        $process.BeginOutputReadLine()
        $process.BeginErrorReadLine()

        $timedOut = $false
        if ($TimeoutSeconds -gt 0) {
            $timeoutMilliseconds = [Math]::Max(1, $TimeoutSeconds) * 1000
            if (-not $process.WaitForExit($timeoutMilliseconds)) {
                $timedOut = $true
                try {
                    $process.Kill()
                }
                catch {
                    # The process may already have exited between timeout detection and Kill().
                }
                try {
                    $process.WaitForExit(5000) | Out-Null
                }
                catch {
                    # Best effort only.
                }
            }
            else {
                try {
                    $process.WaitForExit() | Out-Null
                }
                catch {
                    # Best effort only.
                }
            }
        }
        else {
            $process.WaitForExit()
            try {
                $process.WaitForExit() | Out-Null
            }
            catch {
                # Best effort only.
            }
        }

        [System.Threading.Monitor]::Enter($stdoutLock)
        try {
            $stdout = [string]$stdoutBuilder.ToString()
        }
        finally {
            [System.Threading.Monitor]::Exit($stdoutLock)
        }

        [System.Threading.Monitor]::Enter($stderrLock)
        try {
            $stderr = [string]$stderrBuilder.ToString()
        }
        finally {
            [System.Threading.Monitor]::Exit($stderrLock)
        }

        if ($timedOut) {
            $timeoutMessage = "Timed out after $TimeoutSeconds seconds: $commandLine"
            if ([string]::IsNullOrWhiteSpace($stderr)) {
                $stderr = $timeoutMessage
            }
            else {
                $stderr = "$stderr`n$timeoutMessage"
            }
        }

        if ($timedOut) {
            return [pscustomobject]@{
                ExitCode = 124
                Stdout = $stdout
                Stderr = $stderr
                TimedOut = $true
            }
        }

        $exitCode = 0
        try {
            $exitCode = [int]$process.ExitCode
        }
        catch {
            $exitCode = 125
            if ([string]::IsNullOrWhiteSpace($stderr)) {
                $stderr = "Process exited but PowerShell could not read ExitCode for: $commandLine"
            }
            else {
                $stderr = "$stderr`nProcess exited but PowerShell could not read ExitCode for: $commandLine"
            }
        }

        return [pscustomobject]@{
            ExitCode = $exitCode
            Stdout = $stdout
            Stderr = $stderr
            TimedOut = $false
        }
    }
    catch {
        return [pscustomobject]@{
            ExitCode = 127
            Stdout = ""
            Stderr = "Failed to launch native command: $commandLine`n$($_.Exception.Message)"
            TimedOut = $false
        }
    }
    finally {
        if ($null -ne $process) {
            if ($null -ne $stdoutHandler) {
                try { $process.remove_OutputDataReceived($stdoutHandler) } catch { }
            }
            if ($null -ne $stderrHandler) {
                try { $process.remove_ErrorDataReceived($stderrHandler) } catch { }
            }
            $process.Dispose()
        }
    }
}


function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$Quiet,
        [string]$WorkingDirectory = "",
        [int]$TimeoutSeconds = 0
    )

    $result = Invoke-Native -FilePath $FilePath -Arguments $Arguments -Quiet:$Quiet -WorkingDirectory $WorkingDirectory -TimeoutSeconds $TimeoutSeconds
    if ($result.ExitCode -ne 0) {
        $commandLine = Format-NativeCommandLine -FilePath $FilePath -Arguments $Arguments
        $details = (($result.Stderr, $result.Stdout) -join "`n").Trim()
        if ([string]::IsNullOrWhiteSpace($details)) {
            $details = "No output was captured. Try running this command directly: $commandLine"
        }
        throw "Command failed with exit code $($result.ExitCode): $commandLine`n$details"
    }
    return $result
}

function Test-WindowsHost {
    if (Get-Variable IsWindows -ErrorAction SilentlyContinue) {
        return [bool]$IsWindows
    }
    return $env:OS -eq "Windows_NT"
}

function Write-Utf8NoBomFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )

    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

function ConvertTo-PowerShellSingleQuotedLiteral {
    param([AllowNull()][string]$Value)

    if ($null -eq $Value) {
        $Value = ""
    }
    return "'" + ($Value -replace "'", "''") + "'"
}

function ConvertTo-ShellSingleQuotedLiteral {
    param([AllowNull()][string]$Value)

    if ($null -eq $Value) {
        $Value = ""
    }
    return "'" + ($Value -replace "'", "'\''") + "'"
}

function ConvertTo-WslHostPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if ($fullPath -notmatch "^([A-Za-z]):[\\/]*(.*)$") {
        throw "Cannot convert non-drive Windows path to a WSL /mnt path: $Path"
    }

    $drive = $Matches[1].ToLowerInvariant()
    $rest = $Matches[2] -replace "\\", "/"
    if ([string]::IsNullOrWhiteSpace($rest)) {
        return "/mnt/$drive"
    }
    return "/mnt/$drive/$rest"
}

function Resolve-MainComputerUserMode {
    param([Parameter(Mandatory = $true)][string]$ModeName)

    $normalized = ($ModeName.Trim().ToLowerInvariant() -replace "\s+", "-")
    switch ($normalized) {
        "unleashed" {
            return [pscustomobject]@{
                Key = "unleashed"
                Label = "Unleashed Mode"
                RuntimeProfile = "test"
                DefaultExecutorDistribution = "MainComputerExecutorTest"
                DefaultPort = 8765
                DefaultHeartbeatPort = 8766
                GuidanceLevel = "developer"
            }
        }
        "unleashed-mode" {
            return (Resolve-MainComputerUserMode -ModeName "Unleashed")
        }
        "debug" {
            return [pscustomobject]@{
                Key = "debug"
                Label = "Debug"
                RuntimeProfile = "test"
                DefaultExecutorDistribution = "MainComputerExecutorProtoDev"
                DefaultPort = 28865
                DefaultHeartbeatPort = 28866
                GuidanceLevel = "debug"
            }
        }
        "safe" {
            return [pscustomobject]@{
                Key = "safe"
                Label = "Safe Mode"
                RuntimeProfile = "prod"
                DefaultExecutorDistribution = "MainComputerExecutor"
                DefaultPort = $SafePort
                DefaultHeartbeatPort = $SafeHeartbeatPort
                GuidanceLevel = "guided"
            }
        }
        "safe-mode" {
            return (Resolve-MainComputerUserMode -ModeName "Safe")
        }
    }

    Fail "Unknown Main Computer mode: $ModeName"
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

function Resolve-MainComputerInstanceName {
    param([Parameter(Mandatory = $true)][string]$Root)

    if (-not [string]::IsNullOrWhiteSpace($InstanceName)) {
        return (ConvertTo-MainComputerInstanceSegment -Value $InstanceName)
    }

    $leaf = Split-Path -Leaf ([System.IO.Path]::GetFullPath($Root).TrimEnd([char[]]@('\', '/')))
    if ([string]::IsNullOrWhiteSpace($leaf)) {
        $leaf = "main-computer"
    }
    return (ConvertTo-MainComputerInstanceSegment -Value $leaf)
}

function Resolve-MainComputerInstanceStoreRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$InstallInstanceName,
        [string]$RequestedRoot = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        return [System.IO.Path]::GetFullPath($RequestedRoot).TrimEnd([char[]]@('\', '/'))
    }

    $safeInstance = ConvertTo-MainComputerInstanceSegment -Value $InstallInstanceName
    if ($safeInstance.Length -gt 24) {
        $safeInstance = $safeInstance.Substring(0, 24).Trim("-")
    }
    if ([string]::IsNullOrWhiteSpace($safeInstance)) {
        $safeInstance = "default"
    }

    $storeLeaf = ".main-computer-$safeInstance"
    $userProfileRoot = [Environment]::GetFolderPath("UserProfile")
    if ([string]::IsNullOrWhiteSpace($userProfileRoot)) {
        $userProfileRoot = $env:USERPROFILE
    }

    if (-not [string]::IsNullOrWhiteSpace($userProfileRoot)) {
        return (Join-Path (Join-Path ([System.IO.Path]::GetFullPath($userProfileRoot)) $storeLeaf) "instances")
    }

    $installParent = Split-Path -Parent ([System.IO.Path]::GetFullPath($Root).TrimEnd([char[]]@('\', '/')))
    return (Join-Path (Join-Path $installParent $storeLeaf) "instances")
}

function New-ModeIsolationProfile {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$InstallInstanceName,
        [Parameter(Mandatory = $true)][string]$InstanceStoreRoot,
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$RuntimeProfileName,
        [Parameter(Mandatory = $true)][string]$DistributionSuffix,
        [Parameter(Mandatory = $true)][int]$DefaultPort,
        [Parameter(Mandatory = $true)][int]$DefaultHeartbeatPort,
        [Parameter(Mandatory = $true)][int]$DefaultOnlyOfficePort,
        [Parameter(Mandatory = $true)][int]$DefaultLocalServerPortStart,
        [Parameter(Mandatory = $true)][int]$LocalServerGeneratedPortStart,
        [Parameter(Mandatory = $true)][int]$LocalServerGeneratedPortEnd,
        [Parameter(Mandatory = $true)][string]$GuidanceLevel
    )

    $instanceRoot = Join-Path $InstanceStoreRoot $InstallInstanceName
    $stateRoot = Join-Path $instanceRoot $Key
    $coolifyInstance = ConvertTo-MainComputerInstanceSegment -Value $InstallInstanceName
    $coolifyProjectName = "main-computer-coolify-$coolifyInstance-$Key"
    $localPlatformInstance = ConvertTo-MainComputerInstanceSegment -Value $InstallInstanceName
    $localPlatformPrefix = "main-computer-local-platform-"
    $localPlatformSuffix = "-$Key"
    $localPlatformMaxInstanceLength = 63 - $localPlatformPrefix.Length - $localPlatformSuffix.Length
    if ($localPlatformMaxInstanceLength -lt 1) {
        $localPlatformMaxInstanceLength = 1
    }
    if ($localPlatformInstance.Length -gt $localPlatformMaxInstanceLength) {
        $localPlatformInstance = $localPlatformInstance.Substring(0, $localPlatformMaxInstanceLength).Trim("-")
    }
    if ([string]::IsNullOrWhiteSpace($localPlatformInstance)) {
        $localPlatformInstance = "main-computer"
    }
    $localPlatformProjectName = "$localPlatformPrefix$localPlatformInstance$localPlatformSuffix"
    $coolifyPort = switch ($Key) {
        "debug" { 27056 }
        "safe" { 37056 }
        default { 17056 }
    }
    $coolifySoketiPort = switch ($Key) {
        "debug" { 27156 }
        "safe" { 37156 }
        default { 17156 }
    }
    $coolifySoketiTerminalPort = switch ($Key) {
        "debug" { 27256 }
        "safe" { 37256 }
        default { 17256 }
    }
    return [pscustomobject]@{
        Key = $Key
        Label = $Label
        RuntimeProfile = $RuntimeProfileName
        DefaultExecutorDistribution = "MainComputer-$InstallInstanceName-$DistributionSuffix"
        DefaultPort = $DefaultPort
        DefaultHeartbeatPort = $DefaultHeartbeatPort
        DefaultOnlyOfficePort = $DefaultOnlyOfficePort
        OnlyOfficeProjectName = "main-computer-onlyoffice-$Key"
        CoolifyProjectName = $coolifyProjectName
        CoolifyStateRoot = Join-Path $stateRoot "coolify-local-docker"
        CoolifyPort = $coolifyPort
        CoolifySoketiPort = $coolifySoketiPort
        CoolifySoketiTerminalPort = $coolifySoketiTerminalPort
        LocalServerProjectName = $localPlatformProjectName
        LocalServerRegistryPath = Join-Path $stateRoot "local-platform\sites.json"
        LocalServerComposePath = Join-Path $stateRoot "local-platform\docker-compose.websites.yml"
        DefaultLocalServerPortStart = $DefaultLocalServerPortStart
        LocalServerGeneratedPortStart = $LocalServerGeneratedPortStart
        LocalServerGeneratedPortEnd = $LocalServerGeneratedPortEnd
        GuidanceLevel = $GuidanceLevel
        InstanceName = $InstallInstanceName
        InstanceStoreRoot = $InstanceStoreRoot
        InstanceRoot = $instanceRoot
        StateRoot = $stateRoot
        VenvRoot = Join-Path $stateRoot "venv"
        VenvPython = Join-Path $stateRoot "venv\Scripts\python.exe"
        ControlRoot = Join-Path $stateRoot "control"
        ExecutorRoot = Join-Path $stateRoot "executor"
        WslRuntimeRoot = Join-Path $stateRoot "wsl"
        FirewallRuleName = "MainComputer-$InstallInstanceName-$Key-WslOnly"
        WslHostGatewayIp = ""
        WslGuestIp = ""
        SharedDependencies = @("Ollama", "Gitea", "Windows host services", "WSL host feature")
    }
}

function Get-MainComputerModeIsolationProfiles {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$InstallInstanceName,
        [Parameter(Mandatory = $true)][string]$InstanceStoreRoot
    )

    return @(
        (New-ModeIsolationProfile -Root $Root -InstallInstanceName $InstallInstanceName -InstanceStoreRoot $InstanceStoreRoot -Key "unleashed" -Label "Unleashed Mode" -RuntimeProfileName "test" -DistributionSuffix "unleashed" -DefaultPort 8765 -DefaultHeartbeatPort 8766 -DefaultOnlyOfficePort 18084 -DefaultLocalServerPortStart 18080 -LocalServerGeneratedPortStart 18100 -LocalServerGeneratedPortEnd 18199 -GuidanceLevel "developer"),
        (New-ModeIsolationProfile -Root $Root -InstallInstanceName $InstallInstanceName -InstanceStoreRoot $InstanceStoreRoot -Key "debug" -Label "Debug" -RuntimeProfileName "test" -DistributionSuffix "debug" -DefaultPort 28865 -DefaultHeartbeatPort 28866 -DefaultOnlyOfficePort 28084 -DefaultLocalServerPortStart 28080 -LocalServerGeneratedPortStart 28100 -LocalServerGeneratedPortEnd 28199 -GuidanceLevel "debug"),
        (New-ModeIsolationProfile -Root $Root -InstallInstanceName $InstallInstanceName -InstanceStoreRoot $InstanceStoreRoot -Key "safe" -Label "Safe Mode" -RuntimeProfileName "prod" -DistributionSuffix "safe" -DefaultPort $SafePort -DefaultHeartbeatPort $SafeHeartbeatPort -DefaultOnlyOfficePort 38084 -DefaultLocalServerPortStart 38080 -LocalServerGeneratedPortStart 38100 -LocalServerGeneratedPortEnd 38199 -GuidanceLevel "guided")
    )
}

function Select-MainComputerModeIsolationProfile {
    param(
        [Parameter(Mandatory = $true)]$ModeProfile,
        [Parameter(Mandatory = $true)][object[]]$ModeProfiles
    )

    foreach ($candidate in $ModeProfiles) {
        if ($candidate.Key -eq $ModeProfile.Key) {
            return $candidate
        }
    }
    Fail "No isolation profile exists for mode: $($ModeProfile.Key)"
}

function Apply-MainComputerModeIsolation {
    param(
        [Parameter(Mandatory = $true)]$ModeProfile,
        [Parameter(Mandatory = $true)]$IsolationProfile
    )

    foreach ($name in @(
        "InstanceName",
        "InstanceStoreRoot",
        "InstanceRoot",
        "StateRoot",
        "VenvRoot",
        "VenvPython",
        "ControlRoot",
        "ExecutorRoot",
        "WslRuntimeRoot",
        "FirewallRuleName",
        "DefaultExecutorDistribution",
        "RuntimeProfile",
        "DefaultPort",
        "DefaultHeartbeatPort",
        "DefaultOnlyOfficePort",
        "OnlyOfficeProjectName",
        "CoolifyProjectName",
        "CoolifyStateRoot",
        "CoolifyPort",
        "CoolifySoketiPort",
        "CoolifySoketiTerminalPort",
        "GuidanceLevel",
        "LocalServerProjectName",
        "LocalServerRegistryPath",
        "LocalServerComposePath",
        "DefaultLocalServerPortStart",
        "LocalServerGeneratedPortStart",
        "LocalServerGeneratedPortEnd",
        "WslHostGatewayIp",
        "WslGuestIp",
        "SharedDependencies"
    )) {
        $ModeProfile | Add-Member -NotePropertyName $name -NotePropertyValue $IsolationProfile.PSObject.Properties[$name].Value -Force
    }

    return $ModeProfile
}

function Write-PrecheckTrace {
    param([Parameter(Mandatory = $true)][string]$Message)

    if ($script:PrecheckActive -or $PrecheckOnly) {
        Write-Host ("[precheck {0:HH:mm:ss}] {1}" -f (Get-Date), $Message)
    }
}

function Add-PrecheckStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$State,
        [string]$Details = ""
    )

    Add-BootstrapStatus $Name $State $Details
    if ($State -eq "FAIL") {
        $script:PrecheckFailed = $true
    }

    if ($script:PrecheckActive) {
        if ([string]::IsNullOrWhiteSpace($Details)) {
            Write-PrecheckTrace ("STATUS {0}: {1}" -f $Name, $State)
        }
        else {
            Write-PrecheckTrace ("STATUS {0}: {1} - {2}" -f $Name, $State, $Details)
        }
    }
}

function Invoke-PrecheckStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$ScriptBlock,
        [ValidateSet("warn", "fail")][string]$OnError = "warn"
    )

    $script:PrecheckStepCounter += 1
    $stepNumber = $script:PrecheckStepCounter
    $started = Get-Date
    Write-PrecheckTrace ("START {0}. {1}" -f $stepNumber, $Name)

    try {
        & $ScriptBlock
        $elapsed = ((Get-Date) - $started).TotalSeconds
        Write-PrecheckTrace ("DONE  {0}. {1} ({2:n1}s)" -f $stepNumber, $Name, $elapsed)
    }
    catch {
        $elapsed = ((Get-Date) - $started).TotalSeconds
        $state = if ($OnError -eq "fail") { "FAIL" } else { "WARN" }
        Add-PrecheckStatus $Name $state "Precheck step failed after $([Math]::Round($elapsed, 1))s: $($_.Exception.Message)"
        Write-PrecheckTrace ("ERROR {0}. {1} ({2:n1}s)" -f $stepNumber, $Name, $elapsed)
    }
}

function Invoke-PrecheckTimedJob {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$ScriptBlock,
        [object[]]$ArgumentList = @(),
        [int]$TimeoutSeconds = 20
    )

    Write-PrecheckTrace ("START timed probe: {0} (timeout {1}s)" -f $Name, $TimeoutSeconds)
    $job = $null
    try {
        $job = Start-Job -ScriptBlock $ScriptBlock -ArgumentList $ArgumentList
        if (-not (Wait-Job -Job $job -Timeout $TimeoutSeconds)) {
            Stop-Job -Job $job -ErrorAction SilentlyContinue | Out-Null
            Write-PrecheckTrace ("TIMEOUT timed probe: {0}" -f $Name)
            return [pscustomobject]@{
                TimedOut = $true
                Output = @()
                ErrorText = "Timed out after $TimeoutSeconds seconds."
            }
        }

        $output = @(Receive-Job -Job $job -ErrorAction SilentlyContinue)
        $jobErrors = @($job.ChildJobs | ForEach-Object { $_.Error } | ForEach-Object { $_.ToString() })
        $errorText = ($jobErrors -join "`n").Trim()
        Write-PrecheckTrace ("DONE timed probe: {0}" -f $Name)
        return [pscustomobject]@{
            TimedOut = $false
            Output = $output
            ErrorText = $errorText
        }
    }
    finally {
        if ($null -ne $job) {
            Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        }
    }
}


function Invoke-BasePythonPrecheck {
    param(
        [Parameter(Mandatory = $true)][string]$BasePython,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = ""
    )

    return Invoke-Native -FilePath $BasePython -Arguments $Arguments -Quiet -WorkingDirectory $WorkingDirectory -TimeoutSeconds $PrecheckCommandTimeoutSeconds
}


function Get-PythonVersionTupleFromText {
    param([AllowNull()][string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $null
    }

    $match = [regex]::Match($Text, "(?<!\d)(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)(?!\d)")
    if (-not $match.Success) {
        return $null
    }

    return [pscustomobject]@{
        Major = [int]$match.Groups["major"].Value
        Minor = [int]$match.Groups["minor"].Value
        Patch = [int]$match.Groups["patch"].Value
        Text = $match.Value
    }
}

function Test-PythonVersionAtLeast {
    param(
        [Parameter(Mandatory = $true)]$Version,
        [int]$MinimumMajor = 3,
        [int]$MinimumMinor = 10
    )

    if ([int]$Version.Major -gt $MinimumMajor) {
        return $true
    }
    if ([int]$Version.Major -eq $MinimumMajor -and [int]$Version.Minor -ge $MinimumMinor) {
        return $true
    }
    return $false
}

function Test-PowerShellPrecheck {
    $version = $PSVersionTable.PSVersion
    if ($version.Major -ge 5) {
        Add-PrecheckStatus "PowerShell" "OK" "$($version.ToString())"
    }
    else {
        Add-PrecheckStatus "PowerShell" "FAIL" "PowerShell 5.1 or newer is required; found $($version.ToString())."
    }
}

function Test-GitPrecheck {
    param([Parameter(Mandatory = $true)][string]$SourceRoot)

    $git = Resolve-CommandPath "git"
    if ($null -eq $git) {
        Add-PrecheckStatus "Git" "SKIP" "Git is not required for bootstrap install-root population; the clean export script is used instead."
        return
    }

    $version = Invoke-Native -FilePath $git -Arguments @("--version") -Quiet -TimeoutSeconds $PrecheckCommandTimeoutSeconds
    $versionText = (($version.Stdout, $version.Stderr) -join " ").Trim()
    if ($version.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($versionText)) {
        Add-PrecheckStatus "Git" "OK" "$git ($versionText)"
    }
    else {
        Add-PrecheckStatus "Git" "WARN" "$git was found, but 'git --version' did not complete cleanly."
    }

    if (Test-Path -LiteralPath (Join-Path $SourceRoot "export-main-computer-test.ps1") -PathType Leaf) {
        Add-PrecheckStatus "Install-root copy source" "OK" "Clean export script is present; bootstrap does not require git archive or raw repository copy."
    }
    else {
        Add-PrecheckStatus "Install-root copy source" "FAIL" "export-main-computer-test.ps1 is missing; install-root population requires the clean export script."
    }
}

function Test-InstallRootPrecheck {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$PlannedInstallRoot
    )

    $source = [System.IO.Path]::GetFullPath($SourceRoot).TrimEnd([char[]]@('\', '/'))
    $destination = [System.IO.Path]::GetFullPath($PlannedInstallRoot).TrimEnd([char[]]@('\', '/'))
    if ($source -ine $destination -and (
            $destination.StartsWith($source + "\", [System.StringComparison]::OrdinalIgnoreCase) -or
            $destination.StartsWith($source + "/", [System.StringComparison]::OrdinalIgnoreCase))) {
        Add-PrecheckStatus "Install root" "FAIL" "InstallRoot cannot be inside RepoRoot: $destination"
        return
    }

    Add-PrecheckStatus "Install root" "OK" $destination

    $parent = Split-Path -Parent $destination
    if ([string]::IsNullOrWhiteSpace($parent)) {
        Add-PrecheckStatus "Install root parent" "WARN" "Could not determine parent directory for $destination."
    }
    elseif (Test-Path -LiteralPath $parent -PathType Container) {
        Add-PrecheckStatus "Install root parent" "OK" $parent
    }
    else {
        Add-PrecheckStatus "Install root parent" "WARN" "$parent does not exist yet; bootstrap will need permission to create it."
    }
}

function Test-BasePythonCapabilityPrecheck {
    param(
        [Parameter(Mandatory = $true)][string]$BasePython,
        [Parameter(Mandatory = $true)][string]$SourceRoot
    )

    if ([string]::IsNullOrWhiteSpace($BasePython)) {
        Add-PrecheckStatus "Python capability" "WARN" "No supported base Python is currently available; normal bootstrap will provision managed CPython from the pinned NuGet package."
        return
    }

    $versionCheck = Invoke-BasePythonPrecheck -BasePython $BasePython -Arguments @(
        "-c",
        "import sys; print(f'{sys.executable} {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    ) -WorkingDirectory $SourceRoot
    $versionText = (($versionCheck.Stdout, $versionCheck.Stderr) -join " ").Trim()
    $versionInfo = Get-PythonVersionTupleFromText -Text $versionText
    if ($null -ne $versionInfo -and (Test-PythonVersionAtLeast -Version $versionInfo -MinimumMajor 3 -MinimumMinor 10)) {
        if ($versionCheck.ExitCode -eq 0) {
            Add-PrecheckStatus "Python version" "OK" $versionText
        }
        else {
            Add-PrecheckStatus "Python version" "OK" "$versionText (accepted from probe output even though the native launcher returned exit code $($versionCheck.ExitCode))"
        }
    }
    elseif ($versionCheck.TimedOut) {
        Add-PrecheckStatus "Python version" "FAIL" "Timed out after $PrecheckCommandTimeoutSeconds seconds while checking Python version."
    }
    else {
        Add-PrecheckStatus "Python version" "FAIL" "Main Computer requires Python >= 3.10; probe result: $versionText"
    }

    $venvCheck = Invoke-BasePythonPrecheck -BasePython $BasePython -Arguments @(
        "-c",
        "import venv; print('venv import ok')"
    ) -WorkingDirectory $SourceRoot
    $venvText = (($venvCheck.Stdout, $venvCheck.Stderr) -join " ").Trim()
    if ($venvCheck.ExitCode -eq 0 -or $venvText -match "(?i)venv import ok") {
        Add-PrecheckStatus "Python venv module" "OK" "venv module is importable."
    }
    elseif ($venvCheck.TimedOut) {
        Add-PrecheckStatus "Python venv module" "FAIL" "Timed out after $PrecheckCommandTimeoutSeconds seconds while importing venv."
    }
    else {
        Add-PrecheckStatus "Python venv module" "FAIL" "venv module is unavailable. Probe result: $venvText"
    }

    Test-PythonWheelPipPrecheck -BasePython $BasePython -SourceRoot $SourceRoot

    if (Test-Path -LiteralPath (Join-Path $SourceRoot "pyproject.toml") -PathType Leaf) {
        Add-PrecheckStatus "Python project metadata" "OK" "pyproject.toml is present."
    }
    else {
        Add-PrecheckStatus "Python project metadata" "FAIL" "pyproject.toml is missing from the source root."
    }
}



function Test-PythonWheelPipPrecheck {
    param(
        [Parameter(Mandatory = $true)][string]$BasePython,
        [Parameter(Mandatory = $true)][string]$SourceRoot
    )

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("main-computer-precheck-venv-" + [System.Guid]::NewGuid().ToString("N"))
    try {
        $createCheck = Invoke-BasePythonPrecheck -BasePython $BasePython -Arguments @("-m", "venv", "--without-pip", $tempRoot) -WorkingDirectory $SourceRoot
        $createText = (($createCheck.Stdout, $createCheck.Stderr) -join " ").Trim()
        $tempPython = Join-Path $tempRoot "Scripts\python.exe"
        if ($createCheck.TimedOut) {
            Add-PrecheckStatus "Python venv create" "FAIL" "Timed out after $PrecheckCommandTimeoutSeconds seconds while creating a temporary venv without pip."
            return
        }
        if ($createCheck.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $tempPython -PathType Leaf)) {
            Add-PrecheckStatus "Python venv create" "FAIL" "Could not create a temporary venv without pip. Probe result: $createText"
            return
        }
        Add-PrecheckStatus "Python venv create" "OK" "Temporary venv creation without pip works."

        $wheelPath = Get-PipWheelCachePath
        if (-not (Test-Path -LiteralPath $wheelPath -PathType Leaf)) {
            if ($NoPythonDownload) {
                Add-PrecheckStatus "Python pip wheel" "FAIL" "Pinned pip wheel is not cached and -NoPythonDownload was set: $wheelPath"
            }
            else {
                Add-PrecheckStatus "Python pip wheel" "WARN" "Pinned pip wheel is not cached yet; normal bootstrap will download it: $wheelPath"
            }
            return
        }

        $seedResult = Seed-VenvPipFromWheel -VenvPython $tempPython -VenvRoot $tempRoot -Root $SourceRoot -PipWheelPath $wheelPath -ReturnResult
        if ($seedResult.Ok) {
            Add-PrecheckStatus "Python pip seed" "OK" "Pinned pip wheel can be extracted into a --without-pip venv."
            return
        }

        Add-PrecheckStatus "Python pip seed" "FAIL" "Could not seed pip into a temporary --without-pip venv from the pinned wheel. $($seedResult.Message)"
    }
    finally {
        if (Test-Path -LiteralPath $tempRoot) {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}


function Test-FirewallPrecheck {
    param(
        [Parameter(Mandatory = $true)][object[]]$ModeProfiles,
        [Parameter(Mandatory = $true)]$SelectedMode,
        [string]$WslPath = "",
        [string]$PythonPath = "",
        [string[]]$InstalledDistributions = @()
    )

    if ($SkipWslFirewallRule -or $WslFirewallMode -eq "disabled") {
        Add-PrecheckStatus "Firewall policy" "SKIP" "WSL-scoped firewall management is disabled by parameter."
        return
    }

    if (-not (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue)) {
        Add-PrecheckStatus "Firewall cmdlets" "WARN" "NetSecurity cmdlets are unavailable; cannot inspect Windows Defender Firewall rules."
        return
    }

    Add-PrecheckStatus "Firewall cmdlets" "OK" "NetSecurity cmdlets are available."

    if (Test-RunningAsAdministrator) {
        Add-PrecheckStatus "Firewall admin rights" "OK" "Current shell can create or replace scoped firewall rules."
    }
    else {
        Add-PrecheckStatus "Firewall admin rights" "WARN" "Current shell is not elevated. Precheck can inspect rules, but creating a missing scoped WSL rule may require Administrator PowerShell."
    }

    $broadExposureChecks = @()
    foreach ($profile in $ModeProfiles) {
        $rule = Get-NetFirewallRule -Name $profile.FirewallRuleName -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $rule) {
            Add-PrecheckStatus "Firewall scoped rule: $($profile.Label)" "OK" "$($profile.FirewallRuleName) already exists."
        }
        else {
            Add-PrecheckStatus "Firewall scoped rule: $($profile.Label)" "WARN" "$($profile.FirewallRuleName) does not exist yet; bootstrap can create it after the WSL distro endpoint exists."
        }

        $broadExposureChecks += [pscustomobject]@{
            LocalPort = [int]$profile.DefaultPort
            Label = "$($profile.Label) app"
        }
        $broadExposureChecks += [pscustomobject]@{
            LocalPort = [int]$profile.DefaultHeartbeatPort
            Label = "$($profile.Label) heartbeat"
        }
    }
    Test-BroadFirewallExposureSet -PortChecks $broadExposureChecks -PythonPath $PythonPath | Out-Null

    if (-not [string]::IsNullOrWhiteSpace($WslPath) -and $InstalledDistributions -contains $SelectedMode.DefaultExecutorDistribution) {
        $endpoint = Resolve-WslNetworkEndpoint -WslPath $WslPath -Distribution $SelectedMode.DefaultExecutorDistribution
        if ($null -ne $endpoint) {
            Add-PrecheckStatus "Firewall WSL endpoint" "OK" "Selected mode can scope firewall access from WSL $($endpoint.GuestIp) to Windows gateway $($endpoint.HostGatewayIp)."
        }
        else {
            Add-PrecheckStatus "Firewall WSL endpoint" "WARN" "Selected WSL distro exists, but its host/guest network endpoint could not be resolved."
        }
    }
    else {
        Add-PrecheckStatus "Firewall WSL endpoint" "WARN" "Selected WSL distro is not installed yet; endpoint-scoped rule creation will be deferred until runtime install."
    }
}

function Test-UniqueModeValue {
    param(
        [Parameter(Mandatory = $true)][object[]]$ModeProfiles,
        [Parameter(Mandatory = $true)][string]$PropertyName,
        [Parameter(Mandatory = $true)][string]$DisplayName
    )

    $seen = @{}
    foreach ($profile in $ModeProfiles) {
        $value = [string]$profile.PSObject.Properties[$PropertyName].Value
        if ($seen.ContainsKey($value)) {
            Add-PrecheckStatus "Isolation: $DisplayName" "FAIL" "$($profile.Label) collides with $($seen[$value]) at $value"
            return
        }
        $seen[$value] = $profile.Label
    }
    Add-PrecheckStatus "Isolation: $DisplayName" "OK" "Unique across Unleashed, Debug, and Safe."
}

function Get-PortListenersFromNetstat {
    param(
        [AllowEmptyCollection()][AllowEmptyString()][string[]]$NetstatLines = @(),
        [Parameter(Mandatory = $true)][int]$Port
    )

    $listeners = @()
    $escapedPort = [regex]::Escape([string]$Port)
    foreach ($line in @($NetstatLines)) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        if ($line -match ("^\s*TCP\s+(.+):" + $escapedPort + "\s+\S+\s+LISTENING\s+(\d+)\s*$")) {
            $listeners += [pscustomobject]@{
                LocalAddress = $Matches[1]
                LocalPort = $Port
                OwningProcess = [int]$Matches[2]
            }
        }
    }
    return $listeners
}

function Test-ModePortAvailability {
    param([Parameter(Mandatory = $true)][object[]]$ModeProfiles)

    $ports = @()
    foreach ($profile in $ModeProfiles) {
        $ports += [pscustomobject]@{ Label = $profile.Label; Kind = "app"; Port = [int]$profile.DefaultPort }
        $ports += [pscustomobject]@{ Label = $profile.Label; Kind = "heartbeat"; Port = [int]$profile.DefaultHeartbeatPort }
    }

    $scanTimeoutSeconds = [Math]::Max(3, [Math]::Min([int]$PrecheckCommandTimeoutSeconds, 10))
    $netstatProbe = Invoke-PrecheckTimedJob -Name "Mode lane TCP listener scan via netstat" -TimeoutSeconds $scanTimeoutSeconds -ScriptBlock {
        cmd.exe /c "netstat -ano -p tcp" 2>&1 | ForEach-Object { $_.ToString() }
    }

    if ($netstatProbe.TimedOut) {
        foreach ($portInfo in $ports) {
            Add-PrecheckStatus "Port $($portInfo.Port)" "WARN" "TCP listener scan timed out after $scanTimeoutSeconds seconds while checking $($portInfo.Label) $($portInfo.Kind) port; skipping this advisory check so bootstrap can continue."
        }
        return
    }

    if (-not [string]::IsNullOrWhiteSpace($netstatProbe.ErrorText)) {
        foreach ($portInfo in $ports) {
            Add-PrecheckStatus "Port $($portInfo.Port)" "WARN" "Could not inspect listeners for $($portInfo.Label) $($portInfo.Kind) port via netstat: $($netstatProbe.ErrorText)"
        }
        return
    }

    $netstatLines = @($netstatProbe.Output | ForEach-Object { $_.ToString() })
    foreach ($portInfo in $ports) {
        $listeners = @(Get-PortListenersFromNetstat -NetstatLines $netstatLines -Port $portInfo.Port)
        if ($listeners.Count -gt 0) {
            $pids = (($listeners | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique) -join ", ")
            Add-PrecheckStatus "Port $($portInfo.Port)" "WARN" "$($portInfo.Label) $($portInfo.Kind) lane already has a listener (PID(s): $pids). By default Main Computer supports one active install per mode lane; stop the existing same-mode runner or intentionally supply custom ports."
        }
        else {
            Add-PrecheckStatus "Port $($portInfo.Port)" "OK" "$($portInfo.Label) $($portInfo.Kind) port is available."
        }
    }
}

function Test-RunningAsAdministrator {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = [Security.Principal.WindowsPrincipal]::new($identity)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    catch {
        return $false
    }
}

function Test-IPv4Literal {
    param([AllowNull()][string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }

    $candidate = $Value.Trim()
    if ($candidate -notmatch "^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$") {
        return $false
    }

    foreach ($part in $candidate.Split(".")) {
        $number = 0
        if (-not [int]::TryParse($part, [ref]$number)) {
            return $false
        }
        if ($number -lt 0 -or $number -gt 255) {
            return $false
        }
    }

    return $true
}

function ConvertFrom-WslRouteGatewayHex {
    param([Parameter(Mandatory = $true)][string]$GatewayHex)

    $hex = $GatewayHex.Trim()
    if ($hex -notmatch "^[0-9A-Fa-f]{8}$") {
        return $null
    }

    # /proc/net/route stores the gateway as little-endian hex.
    # Example: 010715AC => 172.21.7.1
    $bytes = @(
        [Convert]::ToInt32($hex.Substring(6, 2), 16)
        [Convert]::ToInt32($hex.Substring(4, 2), 16)
        [Convert]::ToInt32($hex.Substring(2, 2), 16)
        [Convert]::ToInt32($hex.Substring(0, 2), 16)
    )

    return ($bytes -join ".")
}

function Resolve-WslNetworkEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$WslPath,
        [Parameter(Mandatory = $true)][string]$Distribution
    )

    if (-not (Test-WslDistributionInstalled -CommandPath $WslPath -Distribution $Distribution)) {
        return $null
    }

    $hostGatewayIp = $null
    $hostGateway = Invoke-Native -FilePath $WslPath -Arguments @(
        "--distribution", $Distribution,
        "--exec", "/bin/sh", "-lc",
        "command -v ip >/dev/null 2>&1 && ip -4 route show default 2>/dev/null | awk '{print `$3; exit}'"
    ) -Quiet -TimeoutSeconds $PrecheckCommandTimeoutSeconds

    if ($hostGateway.ExitCode -eq 0 -and (Test-IPv4Literal $hostGateway.Stdout)) {
        $hostGatewayIp = $hostGateway.Stdout.Trim()
    }
    else {
        $routeGateway = Invoke-Native -FilePath $WslPath -Arguments @(
            "--distribution", $Distribution,
            "--exec", "/bin/sh", "-lc",
            "awk '`$2 == `"00000000`" { print `$3; exit }' /proc/net/route 2>/dev/null"
        ) -Quiet -TimeoutSeconds $PrecheckCommandTimeoutSeconds

        if ($routeGateway.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($routeGateway.Stdout)) {
            $hostGatewayIp = ConvertFrom-WslRouteGatewayHex -GatewayHex $routeGateway.Stdout
        }
    }

    $guestIp = Invoke-Native -FilePath $WslPath -Arguments @(
        "--distribution", $Distribution,
        "--exec", "/bin/sh", "-lc",
        "hostname -I 2>/dev/null | tr ' ' '\n' | awk '/^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$/ {print; exit}'"
    ) -Quiet -TimeoutSeconds $PrecheckCommandTimeoutSeconds

    $guestIpValue = $null
    if ($guestIp.ExitCode -eq 0 -and (Test-IPv4Literal $guestIp.Stdout)) {
        $guestIpValue = $guestIp.Stdout.Trim()
    }

    if (-not (Test-IPv4Literal $hostGatewayIp) -or -not (Test-IPv4Literal $guestIpValue)) {
        return $null
    }

    return [pscustomobject]@{
        HostGatewayIp = $hostGatewayIp
        GuestIp = $guestIpValue
    }
}

function Test-BroadFirewallExposureSet {
    param(
        [Parameter(Mandatory = $true)][object[]]$PortChecks,
        [string]$PythonPath = ""
    )

    $checks = @($PortChecks | Where-Object { $null -ne $_ })
    if ($checks.Count -eq 0) {
        return 0
    }

    if (-not (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue)) {
        foreach ($check in $checks) {
            Add-PrecheckStatus "Firewall broad exposure: $($check.LocalPort)" "WARN" "NetSecurity cmdlets are unavailable; cannot inspect broad inbound allow rules for $($check.Label) port $($check.LocalPort)."
        }
        return 0
    }

    $probe = Invoke-PrecheckTimedJob -Name "Firewall broad exposure scan for Main Computer mode ports" -TimeoutSeconds $PrecheckFirewallTimeoutSeconds -ArgumentList @($checks, $PythonPath) -ScriptBlock {
        param([object[]]$JobPortChecks, [string]$JobPythonPath)

        Import-Module NetSecurity -ErrorAction SilentlyContinue | Out-Null

        function Test-JobPortSpecMatches {
            param(
                [AllowNull()][string]$Spec,
                [Parameter(Mandatory = $true)][int]$Port
            )

            if ([string]::IsNullOrWhiteSpace($Spec)) {
                return $false
            }

            $text = $Spec.Trim()
            if ($text -in @("Any", "*")) {
                return $true
            }

            foreach ($part in ($text -split ",")) {
                $piece = $part.Trim()
                if ([string]::IsNullOrWhiteSpace($piece)) {
                    continue
                }

                if ($piece -eq ([string]$Port)) {
                    return $true
                }

                $range = [regex]::Match($piece, "^(?<start>\d+)\s*-\s*(?<end>\d+)$")
                if ($range.Success) {
                    $start = [int]$range.Groups["start"].Value
                    $end = [int]$range.Groups["end"].Value
                    if ($Port -ge $start -and $Port -le $end) {
                        return $true
                    }
                }
            }

            return $false
        }

        $metadataByPort = @{}
        foreach ($check in @($JobPortChecks)) {
            $port = [int]$check.LocalPort
            $metadataByPort[[string]$port] = [pscustomobject]@{
                LocalPort = $port
                Label = [string]$check.Label
            }
        }
        $watchedPorts = @($metadataByPort.Keys | ForEach-Object { [int]$_ } | Sort-Object -Unique)
        $findingsByPort = @{}
        foreach ($port in $watchedPorts) {
            $findingsByPort[[string]$port] = 0
        }

        $results = New-Object System.Collections.Generic.List[object]
        try {
            $portFilters = @(Get-NetFirewallPortFilter -ErrorAction SilentlyContinue | Where-Object {
                $protocol = [string]$_.Protocol
                $matchesWatchedPort = $false
                if ($protocol -in @("TCP", "Any")) {
                    foreach ($candidatePort in $watchedPorts) {
                        if (Test-JobPortSpecMatches -Spec ([string]$_.LocalPort) -Port $candidatePort) {
                            $matchesWatchedPort = $true
                            break
                        }
                    }
                }
                $matchesWatchedPort
            })

            foreach ($filter in $portFilters) {
                $matchedPorts = @()
                foreach ($candidatePort in $watchedPorts) {
                    if (Test-JobPortSpecMatches -Spec ([string]$filter.LocalPort) -Port $candidatePort) {
                        $matchedPorts += $candidatePort
                    }
                }
                if ($matchedPorts.Count -eq 0) {
                    continue
                }

                $rules = @($filter | Get-NetFirewallRule -ErrorAction SilentlyContinue)
                foreach ($rule in $rules) {
                    if ([string]$rule.Name -like "MainComputer-*") {
                        continue
                    }

                    $addressFilters = @($rule | Get-NetFirewallAddressFilter -ErrorAction SilentlyContinue)
                    $appFilters = @($rule | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue)

                    $broadAddress = $false
                    foreach ($addressFilter in $addressFilters) {
                        if (([string]$addressFilter.LocalAddress -in @("Any", "*")) -and ([string]$addressFilter.RemoteAddress -in @("Any", "*", "LocalSubnet"))) {
                            $broadAddress = $true
                            break
                        }
                    }

                    $programMatches = $false
                    foreach ($appFilter in $appFilters) {
                        $program = [string]$appFilter.Program
                        if ($program -eq "Any") {
                            $programMatches = $true
                            break
                        }
                        if (-not [string]::IsNullOrWhiteSpace($JobPythonPath) -and $program -ieq $JobPythonPath) {
                            $programMatches = $true
                            break
                        }
                        if ($program -match "(?i)\\python(\.exe)?$") {
                            $programMatches = $true
                            break
                        }
                    }

                    if ($broadAddress -or $programMatches) {
                        foreach ($matchedPort in $matchedPorts) {
                            $findingsByPort[[string]$matchedPort] = [int]$findingsByPort[[string]$matchedPort] + 1
                            $metadata = $metadataByPort[[string]$matchedPort]
                            $display = if ([string]::IsNullOrWhiteSpace($metadata.Label)) { "port $matchedPort" } else { "$($metadata.Label) port $matchedPort" }
                            $results.Add([pscustomobject]@{
                                LocalPort = $matchedPort
                                State = "WARN"
                                Details = "Inbound allow rule '$($rule.DisplayName)' may expose $display beyond the intended WSL/local lane. Remove it or narrow it to the scoped MainComputer rule."
                            }) | Out-Null
                        }
                    }
                }
            }

            foreach ($port in $watchedPorts) {
                if ([int]$findingsByPort[[string]$port] -eq 0) {
                    $metadata = $metadataByPort[[string]$port]
                    $display = if ([string]::IsNullOrWhiteSpace($metadata.Label)) { "port $port" } else { "$($metadata.Label) port $port" }
                    $results.Add([pscustomobject]@{
                        LocalPort = $port
                        State = "OK"
                        Details = "No obvious broad inbound allow rule was found for $display."
                    }) | Out-Null
                }
            }

            return [pscustomobject]@{
                Findings = (($findingsByPort.Values | Measure-Object -Sum).Sum)
                Statuses = @($results)
            }
        }
        catch {
            $fallback = New-Object System.Collections.Generic.List[object]
            foreach ($port in $watchedPorts) {
                $fallback.Add([pscustomobject]@{
                    LocalPort = $port
                    State = "WARN"
                    Details = "Could not inspect firewall rules for port ${port}: $($_.Exception.Message)"
                }) | Out-Null
            }
            return [pscustomobject]@{
                Findings = 0
                Statuses = @($fallback)
            }
        }
    }

    if ($probe.TimedOut) {
        Add-PrecheckStatus "Firewall broad exposure scan" "WARN" "Timed out after $PrecheckFirewallTimeoutSeconds seconds while inspecting broad inbound allow rules for all Main Computer mode ports. Rerun in an elevated shell or use -WslFirewallMode disabled to skip firewall inspection temporarily."
        foreach ($check in $checks) {
            Add-PrecheckStatus "Firewall broad exposure: $($check.LocalPort)" "WARN" "Broad inbound firewall scan did not complete for $($check.Label) port $($check.LocalPort)."
        }
        return 0
    }

    if (-not [string]::IsNullOrWhiteSpace($probe.ErrorText)) {
        Add-PrecheckStatus "Firewall broad exposure scan" "WARN" "Firewall inspection emitted errors: $($probe.ErrorText)"
    }

    $findings = 0
    $emittedPorts = New-Object System.Collections.Generic.HashSet[int]
    foreach ($item in @($probe.Output)) {
        if ($null -eq $item) {
            continue
        }
        if ($item.PSObject.Properties.Name -contains "Findings") {
            $findings += [int]$item.Findings
            foreach ($status in @($item.Statuses)) {
                $port = [int]$status.LocalPort
                [void]$emittedPorts.Add($port)
                Add-PrecheckStatus "Firewall broad exposure: $port" $status.State $status.Details
            }
        }
        elseif ($item.PSObject.Properties.Name -contains "State") {
            $port = [int]$item.LocalPort
            [void]$emittedPorts.Add($port)
            Add-PrecheckStatus "Firewall broad exposure: $port" $item.State $item.Details
        }
    }

    foreach ($check in $checks) {
        $port = [int]$check.LocalPort
        if (-not $emittedPorts.Contains($port)) {
            Add-PrecheckStatus "Firewall broad exposure: $port" "WARN" "Firewall broad-exposure inspection returned no result for $($check.Label) port $port."
        }
    }

    return $findings
}

function Test-BroadFirewallExposure {
    param(
        [Parameter(Mandatory = $true)][int]$LocalPort,
        [string]$PythonPath = "",
        [string]$Label = ""
    )

    return Test-BroadFirewallExposureSet -PortChecks @([pscustomobject]@{
        LocalPort = $LocalPort
        Label = $Label
    }) -PythonPath $PythonPath
}

function Ensure-WslScopedFirewallRule {
    param(
        [Parameter(Mandatory = $true)]$ModeProfile,
        [Parameter(Mandatory = $true)][string]$WslPath,
        [string]$PythonPath = "",
        [switch]$CheckOnly
    )

    if ($SkipWslFirewallRule -or $WslFirewallMode -eq "disabled") {
        Add-BootstrapStatus "WSL scoped firewall" "SKIP" "Disabled by parameter."
        return $false
    }

    if (-not (Get-Command New-NetFirewallRule -ErrorAction SilentlyContinue)) {
        $message = "NetSecurity cmdlets are unavailable; cannot manage a WSL-scoped firewall rule."
        if ($WslFirewallMode -eq "required") {
            Add-BootstrapStatus "WSL scoped firewall" "FAIL" $message
            Fail $message
        }
        Add-BootstrapStatus "WSL scoped firewall" "WARN" $message
        return $false
    }

    $endpoint = Resolve-WslNetworkEndpoint -WslPath $WslPath -Distribution $ModeProfile.DefaultExecutorDistribution
    if ($null -eq $endpoint) {
        Add-BootstrapStatus "WSL scoped firewall" "WARN" "Could not resolve WSL host/guest IPs for $($ModeProfile.DefaultExecutorDistribution). Rule will be installed after the distro exists."
        return $false
    }

    $ModeProfile | Add-Member -NotePropertyName "WslHostGatewayIp" -NotePropertyValue $endpoint.HostGatewayIp -Force
    $ModeProfile | Add-Member -NotePropertyName "WslGuestIp" -NotePropertyValue $endpoint.GuestIp -Force
    $script:WslHostGatewayIp = $endpoint.HostGatewayIp

    Test-BroadFirewallExposure -LocalPort ([int]$ModeProfile.DefaultPort) -PythonPath $PythonPath -Label "$($ModeProfile.Label) app" | Out-Null

    if ($CheckOnly) {
        Add-BootstrapStatus "WSL scoped firewall" "OK" "Would allow TCP $($ModeProfile.DefaultPort) only from WSL $($endpoint.GuestIp) to Windows WSL gateway $($endpoint.HostGatewayIp)."
        return $true
    }

    if (-not (Test-RunningAsAdministrator)) {
        $message = "Administrator PowerShell is required to install firewall rule '$($ModeProfile.FirewallRuleName)'."
        if ($WslFirewallMode -eq "required") {
            Add-BootstrapStatus "WSL scoped firewall" "FAIL" $message
            Fail $message
        }
        Add-BootstrapStatus "WSL scoped firewall" "WARN" $message
        return $false
    }

    Get-NetFirewallRule -Name $ModeProfile.FirewallRuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule

    New-NetFirewallRule `
        -Name $ModeProfile.FirewallRuleName `
        -DisplayName "Main Computer $($ModeProfile.Label) $($ModeProfile.DefaultPort) - WSL only" `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort ([int]$ModeProfile.DefaultPort) `
        -LocalAddress $endpoint.HostGatewayIp `
        -RemoteAddress $endpoint.GuestIp `
        -Profile Domain,Private,Public `
        -EdgeTraversalPolicy Block | Out-Null

    Add-BootstrapStatus "WSL scoped firewall" "OK" "$($ModeProfile.FirewallRuleName): $($endpoint.GuestIp) -> $($endpoint.HostGatewayIp):$($ModeProfile.DefaultPort)"
    return $true
}

function Set-OnlyOfficeWslCallbackEnvironment {
    param([Parameter(Mandatory = $true)]$ModeProfile)

    if ($script:EffectiveOnlyOfficeMode -eq "disabled") {
        return
    }

    if (-not [string]::IsNullOrWhiteSpace($ModeProfile.WslHostGatewayIp)) {
        $env:MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL = "http://$($ModeProfile.WslHostGatewayIp):$Port"
        Add-BootstrapStatus "ONLYOFFICE callback" "OK" $env:MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL
    }
    elseif (-not [string]::IsNullOrWhiteSpace($script:WslHostGatewayIp)) {
        $env:MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL = "http://$($script:WslHostGatewayIp):$Port"
        Add-BootstrapStatus "ONLYOFFICE callback" "OK" $env:MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL
    }
    else {
        Add-BootstrapStatus "ONLYOFFICE callback" "WARN" "Could not resolve WSL gateway callback URL; WSL-hosted ONLYOFFICE may not reach Main Computer."
    }
}

function Resolve-OnlyOfficeRuntimeMode {
    param([Parameter(Mandatory = $true)][string]$RequestedMode)

    if ($RequestedMode -eq "disabled") {
        return "disabled"
    }

    if ($RequestedMode -eq "wsl") {
        return "wsl"
    }

    $docker = Resolve-CommandPath "docker"
    if ($RequestedMode -eq "docker") {
        if ($null -eq $docker) {
            Add-BootstrapStatus "Docker" "FAIL" "ONLYOFFICE mode 'docker' requires Docker, but docker was not found."
            Fail "ONLYOFFICE mode 'docker' requires Docker, but docker was not found."
        }
        return "docker"
    }

    if ($null -ne $docker) {
        return "docker"
    }

    return "disabled"
}

function Add-OnlyOfficePrecheckStatus {
    if ($OnlyOfficeMode -eq "disabled") {
        Add-PrecheckStatus "Docker" "SKIP" "ONLYOFFICE was explicitly disabled."
        return
    }

    $docker = Resolve-CommandPath "docker"
    if ($OnlyOfficeMode -eq "auto") {
        if ($null -eq $docker) {
            Add-PrecheckStatus "Docker" "SKIP" "Docker was not found; ONLYOFFICE auto mode will skip Docker service installation."
        }
        else {
            Add-PrecheckStatus "Docker" "OK" "$docker; ONLYOFFICE auto mode will use Docker."
        }
        return
    }

    if ($OnlyOfficeMode -eq "docker") {
        if ($null -eq $docker) {
            Add-PrecheckStatus "Docker" "FAIL" "ONLYOFFICE mode 'docker' requires Docker, but docker was not found."
        }
        else {
            Add-PrecheckStatus "Docker" "OK" "$docker; ONLYOFFICE docker mode requested."
        }
        return
    }

    Add-PrecheckStatus "Docker" "SKIP" "ONLYOFFICE mode '$OnlyOfficeMode' does not require Docker."
}

function Add-LocalCoolifyPrecheckStatus {
    param([Parameter(Mandatory = $true)]$SelectedMode)

    if ($LocalCoolifyMode -eq "disabled") {
        Add-PrecheckStatus "Local Coolify" "SKIP" "Disabled by -LocalCoolifyMode disabled."
        return
    }

    $docker = Resolve-CommandPath "docker"
    if ($null -eq $docker) {
        if ($LocalCoolifyMode -eq "required") {
            Add-PrecheckStatus "Local Coolify" "FAIL" "Local Coolify requires Docker, but docker was not found."
        }
        else {
            Add-PrecheckStatus "Local Coolify" "WARN" "Docker was not found; Website Builder local Coolify will be skipped unless Docker is installed."
        }
        return
    }

    Add-PrecheckStatus "Local Coolify" "OK" "$docker; install-scoped project '$($SelectedMode.CoolifyProjectName)' on http://127.0.0.1:$($SelectedMode.CoolifyPort)."
}

function Invoke-MainComputerBootstrapPrecheck {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$PlannedInstallRoot,
        [Parameter(Mandatory = $true)]$SelectedMode,
        [Parameter(Mandatory = $true)][object[]]$ModeProfiles
    )

    Write-Section "Bootstrap dependency, Git, Python, WSL, firewall, Ollama, and isolation precheck"
    Write-Host "Precheck progress is printed as each probe starts and finishes. If the run stops, the last START line is the stalled probe."
    Write-Host "Native command timeout: $PrecheckCommandTimeoutSeconds seconds; firewall inspection timeout: $PrecheckFirewallTimeoutSeconds seconds."

    $script:PrecheckActive = $true
    $script:PrecheckStepCounter = 0

    try {
        Invoke-PrecheckStep -Name "Windows host" -OnError fail -ScriptBlock {
            if (-not (Test-WindowsHost)) {
                Add-PrecheckStatus "Windows host" "FAIL" "This bootstrapper is intended for Windows hosts."
            }
            else {
                Add-PrecheckStatus "Windows host" "OK" $env:OS
            }
        }

        Invoke-PrecheckStep -Name "PowerShell" -ScriptBlock {
            Test-PowerShellPrecheck
        }

        Invoke-PrecheckStep -Name "Git and checkout" -ScriptBlock {
            Test-GitPrecheck -SourceRoot $SourceRoot
        }

        Invoke-PrecheckStep -Name "Source repository root shape" -OnError fail -ScriptBlock {
            if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot "pyproject.toml") -PathType Leaf) -or
                -not (Test-Path -LiteralPath (Join-Path $SourceRoot "main_computer") -PathType Container) -or
                -not (Test-Path -LiteralPath (Join-Path $SourceRoot "dev-control.ps1") -PathType Leaf)) {
                Add-PrecheckStatus "Source repository root" "FAIL" "Does not look like Main Computer: $SourceRoot"
            }
            else {
                Add-PrecheckStatus "Source repository root" "OK" $SourceRoot
            }
        }

        Invoke-PrecheckStep -Name "Install root safety" -ScriptBlock {
            Test-InstallRootPrecheck -SourceRoot $SourceRoot -PlannedInstallRoot $PlannedInstallRoot
        }

        Invoke-PrecheckStep -Name "Base Python and Python capabilities" -ScriptBlock {
            try {
                $script:PrecheckPython = Resolve-BasePython -AllowProvision:$ProvisionPythonInPrecheck -SoftFail
                if ([string]::IsNullOrWhiteSpace($script:PrecheckPython)) {
                    if ($NoPythonDownload) {
                        Add-PrecheckStatus "Base Python" "FAIL" "No supported CPython runtime is available and -NoPythonDownload was set."
                    }
                    else {
                        Add-PrecheckStatus "Base Python" "WARN" "No supported CPython runtime is currently available; normal bootstrap will provision managed CPython from the pinned NuGet package."
                    }
                    Test-BasePythonCapabilityPrecheck -BasePython $script:PrecheckPython -SourceRoot $SourceRoot
                }
                else {
                    Add-PrecheckStatus "Base Python" "OK" $script:PrecheckPython
                    Test-BasePythonCapabilityPrecheck -BasePython $script:PrecheckPython -SourceRoot $SourceRoot
                }
            }
            catch {
                Add-PrecheckStatus "Base Python" "FAIL" $_.Exception.Message
                $script:PrecheckPython = ""
                Add-PrecheckStatus "Python capability" "FAIL" "Python probes were skipped because no usable base Python was found."
            }
        }

        Invoke-PrecheckStep -Name "WSL availability, distro inventory, and firewall sanity" -ScriptBlock {
            $wslPath = Resolve-CommandPath $WslCommand
            if ($null -eq $wslPath) {
                Add-PrecheckStatus "wsl.exe" "FAIL" "$WslCommand was not found. Enable/install WSL before using the Windows-first bootstrap."
            }
            else {
                Add-PrecheckStatus "wsl.exe" "OK" $wslPath
                try {
                    $installedDistributions = @(Get-WslDistributions -CommandPath $wslPath)
                    foreach ($profile in $ModeProfiles) {
                        if ($installedDistributions -contains $profile.DefaultExecutorDistribution) {
                            Add-PrecheckStatus "WSL distro: $($profile.Label)" "OK" "$($profile.DefaultExecutorDistribution) already exists for this instance namespace."
                        }
                        else {
                            Add-PrecheckStatus "WSL distro: $($profile.Label)" "WARN" "$($profile.DefaultExecutorDistribution) is not installed yet; bootstrap can install it when the runtime image is available."
                        }
                    }
                    Test-FirewallPrecheck -ModeProfiles $ModeProfiles -SelectedMode $SelectedMode -WslPath $wslPath -PythonPath $script:PrecheckPython -InstalledDistributions $installedDistributions
                }
                catch {
                    Add-PrecheckStatus "WSL distro inventory" "WARN" "Could not list WSL distributions or inspect WSL-scoped firewall path: $($_.Exception.Message)"
                }
            }
        }

        Invoke-PrecheckStep -Name "Optional Docker services" -ScriptBlock {
            Add-OnlyOfficePrecheckStatus
            Add-LocalCoolifyPrecheckStatus -SelectedMode $SelectedMode
        }

        Invoke-PrecheckStep -Name "Ollama shared dependency" -ScriptBlock {
            Test-OllamaAvailability
        }

        Invoke-PrecheckStep -Name "Mode isolation values" -ScriptBlock {
            Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "VenvRoot" -DisplayName "venv roots"
            Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "ControlRoot" -DisplayName "control roots"
            Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "StateRoot" -DisplayName "state roots"
            Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "ExecutorRoot" -DisplayName "executor roots"
            Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "DefaultExecutorDistribution" -DisplayName "WSL distro names"
            Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "DefaultPort" -DisplayName "app ports (mode lanes)"
            Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "DefaultHeartbeatPort" -DisplayName "heartbeat ports (mode lanes)"
            Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "CoolifyProjectName" -DisplayName "local Coolify Compose project names"
            Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "CoolifyStateRoot" -DisplayName "local Coolify state roots"
            Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "CoolifyPort" -DisplayName "local Coolify ports"
            Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "CoolifySoketiPort" -DisplayName "local Coolify Soketi ports"
            Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "CoolifySoketiTerminalPort" -DisplayName "local Coolify Soketi terminal ports"
        }

        Invoke-PrecheckStep -Name "Fixed three-lane policy and lane ports" -ScriptBlock {
            Add-PrecheckStatus "Side-by-side policy" "OK" "Main Computer uses three fixed concurrent lanes: Unleashed, Debug, and Safe. A machine can hold many installs on disk, but only one active install per mode lane should run at a time unless custom ports are deliberately supplied."
            Test-ModePortAvailability -ModeProfiles $ModeProfiles
        }

        Invoke-PrecheckStep -Name "Shared dependency and selected mode summary" -ScriptBlock {
            Add-PrecheckStatus "Shared dependencies" "OK" "Ollama and host WSL are intentionally shared. Mode state, control roots, venvs, executor roots, WSL firewall rules, and WSL distro names are isolated by instance '$($SelectedMode.InstanceName)' under '$($SelectedMode.InstanceStoreRoot)'. Ports are fixed by mode lane, so same-mode installs share a lane and should run one at a time."
            Add-PrecheckStatus "Selected mode" "OK" "$($SelectedMode.Label) uses venv '$($SelectedMode.VenvRoot)', control root '$($SelectedMode.ControlRoot)', WSL distro '$($SelectedMode.DefaultExecutorDistribution)', Coolify project '$($SelectedMode.CoolifyProjectName)', and firewall rule '$($SelectedMode.FirewallRuleName)'."
        }
    }
    finally {
        $script:PrecheckActive = $false
    }
}

function Write-PrecheckSummary {
    param([Parameter(Mandatory = $true)]$SelectedMode)

    Write-Section "Precheck summary"
    foreach ($item in $script:BootstrapStatus) {
        if ([string]::IsNullOrWhiteSpace($item.Details)) {
            Write-Host ("{0}: {1}" -f $item.Name, $item.State)
        }
        else {
            Write-Host ("{0}: {1} - {2}" -f $item.Name, $item.State, $item.Details)
        }
    }

    Write-Host ""
    Write-Host "Selected mode: $($SelectedMode.Label) ($($SelectedMode.Key))"
    Write-Host "Instance namespace: $($SelectedMode.InstanceName)"
    Write-Host "Instance store root: $($SelectedMode.InstanceStoreRoot)"
    Write-Host "Precheck only: no install files, venvs, WSL distros, firewall rules, or runners were created."
    if ($script:PrecheckFailed) {
        Write-Host "Precheck result: FAIL"
    }
    else {
        Write-Host "Precheck result: OK/WARN"
    }
}


function Get-InstallRootArchiveRoot {
    param([Parameter(Mandatory = $true)][string]$DestinationRoot)

    $destination = [System.IO.Path]::GetFullPath($DestinationRoot).TrimEnd([char[]]@('\', '/'))
    $parent = Split-Path -Parent $destination
    $leaf = Split-Path -Leaf $destination
    return (Join-Path (Join-Path $parent ".main-computer-install-archives") $leaf)
}

function Get-InstallTreeFileSummary {
    param([Parameter(Mandatory = $true)][string]$Root)

    $fileCount = 0
    [int64]$totalBytes = 0
    foreach ($file in Get-ChildItem -LiteralPath $Root -Force -Recurse -File) {
        $fileCount += 1
        $totalBytes += [int64]$file.Length
    }

    return [pscustomobject]@{
        FileCount = $fileCount
        TotalBytes = $totalBytes
    }
}

function Test-InstallRootArchiveZip {
    param(
        [Parameter(Mandatory = $true)][string]$ZipPath,
        [Parameter(Mandatory = $true)][int]$ExpectedFileCount,
        [Parameter(Mandatory = $true)][int64]$ExpectedTotalBytes
    )

    Add-Type -AssemblyName System.IO.Compression.FileSystem

    $zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $entries = @($zip.Entries | Where-Object { -not [string]::IsNullOrEmpty($_.Name) })
        [int64]$entryBytes = 0
        foreach ($entry in $entries) {
            $entryBytes += [int64]$entry.Length
        }

        if ($entries.Count -ne $ExpectedFileCount) {
            return [pscustomobject]@{
                Ok = $false
                Details = "archive contains $($entries.Count) file entries, expected $ExpectedFileCount"
            }
        }

        if ($entryBytes -ne $ExpectedTotalBytes) {
            return [pscustomobject]@{
                Ok = $false
                Details = "archive reports $entryBytes uncompressed bytes, expected $ExpectedTotalBytes"
            }
        }

        $buffer = New-Object byte[] 1048576
        [int64]$readBytes = 0
        foreach ($entry in $entries) {
            $stream = $entry.Open()
            try {
                while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $readBytes += [int64]$read
                }
            }
            finally {
                $stream.Dispose()
            }
        }

        if ($readBytes -ne $ExpectedTotalBytes) {
            return [pscustomobject]@{
                Ok = $false
                Details = "archive stream read returned $readBytes bytes, expected $ExpectedTotalBytes"
            }
        }

        return [pscustomobject]@{
            Ok = $true
            Details = "verified $ExpectedFileCount files and $ExpectedTotalBytes bytes"
        }
    }
    finally {
        $zip.Dispose()
    }
}


function Test-InstallTreeSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][int]$ExpectedFileCount,
        [Parameter(Mandatory = $true)][int64]$ExpectedTotalBytes
    )

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return [pscustomobject]@{
            Ok = $false
            Details = "tree does not exist: $Root"
        }
    }

    $summary = Get-InstallTreeFileSummary -Root $Root
    if ($summary.FileCount -ne $ExpectedFileCount) {
        return [pscustomobject]@{
            Ok = $false
            Details = "tree contains $($summary.FileCount) files, expected $ExpectedFileCount"
        }
    }

    if ($summary.TotalBytes -ne $ExpectedTotalBytes) {
        return [pscustomobject]@{
            Ok = $false
            Details = "tree contains $($summary.TotalBytes) bytes, expected $ExpectedTotalBytes"
        }
    }

    return [pscustomobject]@{
        Ok = $true
        Details = "verified $ExpectedFileCount files and $ExpectedTotalBytes bytes"
    }
}

function Test-InstallFileReadableForArchive {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = $null
    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Wait-InstallRootVirtualDisksForArchive {
    param(
        [Parameter(Mandatory = $true)][string]$DestinationRoot,
        [int]$TimeoutSeconds = 60
    )

    if (-not (Test-Path -LiteralPath $DestinationRoot -PathType Container)) {
        return
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $locked = @()
        $virtualDisks = @(Get-ChildItem -LiteralPath $DestinationRoot -Force -Recurse -File -Include "*.vhd", "*.vhdx" -ErrorAction SilentlyContinue)
        foreach ($disk in $virtualDisks) {
            if (-not (Test-InstallFileReadableForArchive -Path $disk.FullName)) {
                $locked += $disk.FullName
            }
        }

        if ($locked.Count -eq 0) {
            return
        }

        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    Fail "Install archive blockers did not release virtual disk(s): $($locked -join '; ')"
}

function Get-WslDistributionExists {
    param(
        [Parameter(Mandatory = $true)][string]$WslPath,
        [Parameter(Mandatory = $true)][string]$Distribution
    )

    if ([string]::IsNullOrWhiteSpace($Distribution)) {
        return $false
    }

    $output = @(& $WslPath --list --quiet 2>$null)
    foreach ($line in $output) {
        $name = ([string]$line -replace "`0", "").Trim()
        if ($name -ieq $Distribution) {
            return $true
        }
    }

    return $false
}

function Stop-InstallRefreshBlockers {
    param(
        [Parameter(Mandatory = $true)][string]$DestinationRoot,
        [Parameter(Mandatory = $true)]$ModeProfile,
        [Parameter(Mandatory = $true)][string]$WslCommand,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

    $distribution = [string]$ModeProfile.DefaultExecutorDistribution
    if ([string]::IsNullOrWhiteSpace($distribution)) {
        Add-BootstrapStatus "Install refresh blockers" "SKIP" "No WSL distribution was selected for $Purpose."
        return
    }

    $wslPath = Resolve-CommandPath $WslCommand
    if ($null -eq $wslPath) {
        Fail "Cannot $Purpose because $WslCommand was not found."
    }

    if (-not (Get-WslDistributionExists -WslPath $wslPath -Distribution $distribution)) {
        Add-BootstrapStatus "Install refresh blockers" "SKIP" "WSL distro '$distribution' does not exist for $Purpose."
        return
    }

    Write-Host "Stopping selected WSL distro to $Purpose`: $distribution"
    & $wslPath --terminate $distribution | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail "Could not terminate WSL distro '$distribution' to $Purpose."
    }

    Wait-InstallRootVirtualDisksForArchive -DestinationRoot $DestinationRoot
    Add-BootstrapStatus "Install refresh blockers" "OK" "Stopped WSL distro '$distribution' to $Purpose."
}

function Get-MainComputerInstallRootProcesses {
    param(
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )

    # Bootstrap install refresh intentionally does not use CIM/WMI process
    # enumeration.  Those APIs can hang on damaged Windows hosts before the
    # real bootstrap issue is reported.  File/archive operations below remain
    # the source of truth: if a runner still has the tree locked, archive or
    # move verification will fail without relying on global process inventory.
    return @()
}

function Format-MainComputerInstallRootProcessDetails {
    param([object[]]$Processes)

    if ($null -eq $Processes -or $Processes.Count -eq 0) {
        return ""
    }

    return (($Processes | ForEach-Object {
        "PID $($_.ProcessId) $($_.Name): $($_.CommandLine)"
    }) -join "`n")
}

function Stop-MainComputerInstallRootProcesses {
    param(
        [Parameter(Mandatory = $true)][string]$DestinationRoot,
        [Parameter(Mandatory = $true)]$ModeProfile,
        [int]$TimeoutSeconds = 10
    )

    if (-not (Test-Path -LiteralPath $DestinationRoot -PathType Container)) {
        return
    }

    $root = [System.IO.Path]::GetFullPath($DestinationRoot).TrimEnd([char[]]@('\', '/'))
    $label = [string]$ModeProfile.Label
    if ([string]::IsNullOrWhiteSpace($label)) {
        $label = "selected mode"
    }

    Add-BootstrapStatus "Install root active runners" "WARN" "Skipped process enumeration for $label install root '$root'; bootstrap does not use CIM/WMI. Stop any running Main Computer runner before refreshing this install root."
}


function Unregister-WslDistributionForInstallRefresh {
    param(
        [Parameter(Mandatory = $true)]$ModeProfile,
        [Parameter(Mandatory = $true)][string]$WslCommand,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    $distribution = [string]$ModeProfile.DefaultExecutorDistribution
    if ([string]::IsNullOrWhiteSpace($distribution)) {
        Add-BootstrapStatus "WSL refresh unregister" "SKIP" "No WSL distribution was selected for $Reason."
        return
    }

    $wslPath = Resolve-CommandPath $WslCommand
    if ($null -eq $wslPath) {
        Fail "Cannot unregister old WSL distro for $Reason because $WslCommand was not found."
    }

    if (-not (Get-WslDistributionExists -WslPath $wslPath -Distribution $distribution)) {
        Add-BootstrapStatus "WSL refresh unregister" "SKIP" "WSL distro '$distribution' does not exist for $Reason."
        return
    }

    Write-Host "Unregistering old WSL distro for $Reason`: $distribution"
    & $wslPath --unregister $distribution | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail "Could not unregister old WSL distro '$distribution' for $Reason."
    }

    Add-BootstrapStatus "WSL refresh unregister" "OK" "Unregistered old WSL distro '$distribution' for $Reason."
}

function Protect-ExistingInstallRoot {
    param(
        [Parameter(Mandatory = $true)][string]$DestinationRoot,
        [Parameter(Mandatory = $true)]$ModeProfile,
        [Parameter(Mandatory = $true)][string]$WslCommand
    )

    $destination = [System.IO.Path]::GetFullPath($DestinationRoot).TrimEnd([char[]]@('\', '/'))

    if (-not (Test-Path -LiteralPath $destination)) {
        return $null
    }

    if (-not (Test-Path -LiteralPath $destination -PathType Container)) {
        Fail "InstallRoot exists but is not a directory: $destination"
    }

    Stop-MainComputerInstallRootProcesses -DestinationRoot $destination -ModeProfile $ModeProfile

    $archiveRoot = Get-InstallRootArchiveRoot -DestinationRoot $destination
    New-Item -ItemType Directory -Force -Path $archiveRoot | Out-Null

    $leaf = Split-Path -Leaf $destination
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $attempt = 0
    do {
        if ($attempt -eq 0) {
            $archiveBaseName = "$leaf-$timestamp"
        }
        else {
            $archiveBaseName = "$leaf-$timestamp-$attempt"
        }
        $zipPath = Join-Path $archiveRoot "$archiveBaseName.zip"
        $movedPath = Join-Path $archiveRoot "$archiveBaseName.moved"
        $attempt += 1
    } while ((Test-Path -LiteralPath $zipPath) -or (Test-Path -LiteralPath $movedPath))

    $summary = Get-InstallTreeFileSummary -Root $destination

    Add-Type -AssemblyName System.IO.Compression.FileSystem

    Write-Host "Existing install root found. Preserving before fresh install:"
    Write-Host "  Source:  $destination"
    Write-Host "  Archive: $zipPath"
    Write-Host "  Move to: $movedPath"

    try {
        [System.IO.Compression.ZipFile]::CreateFromDirectory(
            $destination,
            $zipPath,
            [System.IO.Compression.CompressionLevel]::Fastest,
            $false
        )
    }
    catch {
        if (Test-Path -LiteralPath $zipPath) {
            Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
        }
        Fail "Could not archive existing install root; leaving it in place: $($_.Exception.Message)"
    }

    $verification = Test-InstallRootArchiveZip -ZipPath $zipPath -ExpectedFileCount $summary.FileCount -ExpectedTotalBytes $summary.TotalBytes
    if (-not $verification.Ok) {
        Fail "Archive verification failed; leaving existing install root in place: $($verification.Details)"
    }

    Write-Host "  Zip verified: $($verification.Details)"

    try {
        Move-Item -LiteralPath $destination -Destination $movedPath -Force
    }
    catch {
        Fail "Archive zip was verified, but the existing install root could not be moved out of the way; leaving it in place: $($_.Exception.Message)"
    }

    if (Test-Path -LiteralPath $destination) {
        Fail "Old install root still exists after preserved refresh move; aborting before fresh install: $destination"
    }

    if (-not (Test-Path -LiteralPath $movedPath -PathType Container)) {
        Fail "Old install root move reported success, but the moved directory is missing: $movedPath"
    }

    Add-BootstrapStatus "Install root archive" "OK" "Archived existing install to '$zipPath', verified archive stream, and moved active old tree to '$movedPath'."
    return [pscustomobject]@{
        ZipPath = $zipPath
        MovedPath = $movedPath
        FileCount = $summary.FileCount
        TotalBytes = $summary.TotalBytes
    }
}

function Resolve-WindowsPowerShellExecutable {
    $candidate = Join-Path $PSHOME "powershell.exe"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        return $candidate
    }

    $resolved = Resolve-CommandPath "powershell.exe"
    if ($null -ne $resolved) {
        return $resolved
    }

    Fail "Could not locate powershell.exe to run the clean export script."
}

function Copy-CleanExportToInstallRoot {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )

    $source = (Resolve-Path -LiteralPath $SourceRoot).Path.TrimEnd([char[]]@('\', '/'))
    $destination = [System.IO.Path]::GetFullPath($DestinationRoot).TrimEnd([char[]]@('\', '/'))
    $exportScript = Join-Path $source "export-main-computer-test.ps1"

    if (-not (Test-Path -LiteralPath $exportScript -PathType Leaf)) {
        Fail "Clean install-root copy requires export-main-computer-test.ps1, but it was not found at: $exportScript"
    }

    $projectName = Split-Path -Leaf $source
    if ([string]::IsNullOrWhiteSpace($projectName)) {
        $projectName = "main_computer_test"
    }

    # Keep export staging next to the selected install root with very short path
    # components.  PowerShell 5.1/.NET Framework zip extraction is still prone
    # to MAX_PATH failures when a long %TEMP% prefix is combined with deep
    # frontend vendor filenames.  The final install root is normally shorter
    # than %TEMP%, so staging under its parent avoids false extraction failures
    # while still using the clean export archive as the source of truth.
    $destinationParent = Split-Path -Parent $destination
    if ([string]::IsNullOrWhiteSpace($destinationParent)) {
        $destinationParent = [System.IO.Path]::GetTempPath()
    }
    $tempRoot = Join-Path $destinationParent (".mcx-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
    $archiveRoot = Join-Path $tempRoot "a"
    $expandRoot = Join-Path $tempRoot "x"

    try {
        New-Item -ItemType Directory -Force -Path $archiveRoot | Out-Null
        New-Item -ItemType Directory -Force -Path $expandRoot | Out-Null
        Write-Host "Export staging root: $tempRoot"

        $powerShellExe = Resolve-WindowsPowerShellExecutable
        Invoke-NativeChecked `
            -FilePath $powerShellExe `
            -Arguments @(
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                $exportScript,
                "-SourceRoot",
                $source,
                "-ArchiveRoot",
                $archiveRoot,
                "-ProjectName",
                $projectName
            ) `
            -WorkingDirectory $source `
            -TimeoutSeconds 600 | Out-Null

        $archives = @(Get-ChildItem -LiteralPath $archiveRoot -Filter "*.zip" -File -Force | Sort-Object LastWriteTimeUtc -Descending)
        if ($archives.Count -lt 1) {
            Fail "Clean export script completed but did not create an archive under: $archiveRoot"
        }

        $zipPath = $archives[0].FullName
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $expandRoot)

        $exportRoot = Join-Path $expandRoot $projectName
        if (-not (Test-Path -LiteralPath $exportRoot -PathType Container)) {
            $topLevelDirs = @(Get-ChildItem -LiteralPath $expandRoot -Force -Directory)
            if ($topLevelDirs.Count -eq 1) {
                $exportRoot = $topLevelDirs[0].FullName
            }
        }

        if (-not (Test-Path -LiteralPath $exportRoot -PathType Container)) {
            Fail "Clean export archive did not contain the expected repository root '$projectName'."
        }

        New-Item -ItemType Directory -Force -Path $destination | Out-Null

        foreach ($child in Get-ChildItem -LiteralPath $exportRoot -Force) {
            $target = Join-Path $destination $child.Name
            if ($child.PSIsContainer) {
                Copy-Item -LiteralPath $child.FullName -Destination $target -Recurse -Force
            }
            else {
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
                Copy-Item -LiteralPath $child.FullName -Destination $target -Force
            }
        }

        $summary = Get-InstallTreeFileSummary -Root $destination
        Add-BootstrapStatus "Install root export" "OK" "Populated install root from clean export archive '$zipPath' ($($summary.FileCount) files, $($summary.TotalBytes) bytes)."
    }
    finally {
        if (Test-Path -LiteralPath $tempRoot) {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Copy-RepositoryToInstallRoot {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$DestinationRoot,
        [Parameter(Mandatory = $true)]$ModeProfile,
        [Parameter(Mandatory = $true)][string]$WslCommand
    )

    $source = (Resolve-Path -LiteralPath $SourceRoot).Path.TrimEnd([char[]]@('\', '/'))
    $destination = [System.IO.Path]::GetFullPath($DestinationRoot).TrimEnd([char[]]@('\', '/'))

    if ($source -ieq $destination) {
        Add-BootstrapStatus "Install root" "OK" "Using repository root: $source"
        return $source
    }

    if ($destination.StartsWith($source + "\", [System.StringComparison]::OrdinalIgnoreCase) -or
        $destination.StartsWith($source + "/", [System.StringComparison]::OrdinalIgnoreCase)) {
        Fail "InstallRoot cannot be inside RepoRoot. Choose a sibling or external install location."
    }

    if ($SkipInstallRootCopy) {
        if (-not (Test-Path -LiteralPath (Join-Path $destination "pyproject.toml") -PathType Leaf)) {
            Fail "-SkipInstallRootCopy was set, but InstallRoot does not already look like a Main Computer checkout: $destination"
        }
        Add-BootstrapStatus "Install root" "OK" "Using existing install root without copying: $destination"
        return $destination
    }

    Write-Section "Create/update selected install root"
    Write-Host "Source repo:  $source"
    Write-Host "Install root: $destination"
    Write-Host "Copy mode:    clean export script (not git, not a raw recursive repository copy)"

    if (Test-Path -LiteralPath $destination) {
        if ($AutoForceInstall) {
            Write-Host "-AutoForceInstall was set. Destroying existing install root without archive: $destination"
            Remove-Item -LiteralPath $destination -Recurse -Force
            Add-BootstrapStatus "Install root archive" "SKIP" "-AutoForceInstall destroyed existing install root without preserving an archive. Persistent runtime state was not reset."
        }
        else {
            Protect-ExistingInstallRoot -DestinationRoot $destination -ModeProfile $ModeProfile -WslCommand $WslCommand | Out-Null
        }
    }

    Copy-CleanExportToInstallRoot -SourceRoot $source -DestinationRoot $destination

    Add-BootstrapStatus "Install root" "OK" "Created fresh install root at $destination from clean export output."
    return $destination
}

function Test-OllamaAvailability {
    if ($SkipOllamaCheck) {
        Add-PrecheckStatus "Ollama" "SKIP" "-SkipOllamaCheck was set."
        return
    }

    $ollamaCommand = Resolve-CommandPath "ollama"
    $baseUrl = $env:OLLAMA_BASE_URL
    if ([string]::IsNullOrWhiteSpace($baseUrl)) {
        $baseUrl = "http://localhost:11434"
    }
    $baseUrl = $baseUrl.TrimEnd("/")

    $apiOk = $false
    try {
        Invoke-RestMethod -Method GET -Uri "$baseUrl/api/tags" -TimeoutSec 5 | Out-Null
        $apiOk = $true
    }
    catch {
        $apiOk = $false
    }

    if ($apiOk) {
        if ($ollamaCommand) {
            Add-PrecheckStatus "Ollama" "OK" "ollama CLI found at $ollamaCommand; API reachable at $baseUrl"
        }
        else {
            Add-PrecheckStatus "Ollama" "OK" "API reachable at $baseUrl; CLI was not found on PATH."
        }
        return
    }

    if ($ollamaCommand) {
        Add-PrecheckStatus "Ollama" "WARN" "ollama CLI found at $ollamaCommand, but $baseUrl/api/tags was not reachable. Start Ollama before using local model features."
        return
    }

    Add-PrecheckStatus "Ollama" "WARN" "ollama CLI was not found and $baseUrl/api/tags was not reachable. Install/start Ollama or set OLLAMA_BASE_URL before using local model features."
}

function Write-ModeRunner {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]$ModeProfile,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$WslPath,
        [Parameter(Mandatory = $true)][string]$Distribution,
        [Parameter(Mandatory = $true)][string]$InstallInstanceName,
        [Parameter(Mandatory = $true)][object[]]$ModeProfiles
    )

    if ($SkipRunnerCreation) {
        Add-BootstrapStatus "Mode runner" "SKIP" "-SkipRunnerCreation was set."
        return ""
    }

    $runtimeDir = Join-Path $Root "runtime"
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

    $runnerPath = Join-Path $Root $RunnerName
    $manifestPath = Join-Path $runtimeDir "main-computer-install.json"
    $workspaceForRunner = if ([string]::IsNullOrWhiteSpace($Workspace)) { $Root } else { $Workspace }

    function Find-RunnerModeProfile {
        param([Parameter(Mandatory = $true)][string]$Key)
        foreach ($candidate in $ModeProfiles) {
            if ($candidate.Key -eq $Key) {
                return $candidate
            }
        }
        Fail "Runner mode profile not found: $Key"
    }

    $unleashedProfile = Find-RunnerModeProfile -Key "unleashed"
    $debugProfile = Find-RunnerModeProfile -Key "debug"
    $safeProfile = Find-RunnerModeProfile -Key "safe"

    $modesManifest = [ordered]@{}
    foreach ($profile in $ModeProfiles) {
        $meaning = switch ($profile.Key) {
            "unleashed" { "default developer-capable mode with the most power over the system" }
            "debug" { "proto-dev isolation for cases where the system should behave one way and does not" }
            "safe" { "prod-shaped guided mode where code can detect the mode and offer user-facing guidance" }
            default { "Main Computer mode" }
        }
        $modesManifest[$profile.Key] = [ordered]@{
            label = $profile.Label
            meaning = $meaning
            command = ".\$RunnerName start -Mode $($profile.Label)"
            app_port = $profile.DefaultPort
            heartbeat_port = $profile.DefaultHeartbeatPort
            onlyoffice_port = $profile.DefaultOnlyOfficePort
            onlyoffice_project = $profile.OnlyOfficeProjectName
            local_server_project = $profile.LocalServerProjectName
            local_server_registry = $profile.LocalServerRegistryPath
            local_server_compose = $profile.LocalServerComposePath
            local_server_builtin_port_start = $profile.DefaultLocalServerPortStart
            local_server_generated_port_start = $profile.LocalServerGeneratedPortStart
            local_server_generated_port_end = $profile.LocalServerGeneratedPortEnd
            coolify_project = $profile.CoolifyProjectName
            coolify_state_root = $profile.CoolifyStateRoot
            coolify_port = $profile.CoolifyPort
            coolify_soketi_port = $profile.CoolifySoketiPort
            coolify_soketi_terminal_port = $profile.CoolifySoketiTerminalPort
            instance_root = $profile.InstanceRoot
            venv_root = $profile.VenvRoot
            control_root = $profile.ControlRoot
            state_root = $profile.StateRoot
            executor_root = $profile.ExecutorRoot
            wsl_distro = $profile.DefaultExecutorDistribution
            wsl_firewall_rule = $profile.FirewallRuleName
            shared_dependencies = $profile.SharedDependencies
        }
    }

    $manifest = [ordered]@{
        format = 3
        active_mode = $ModeProfile.Key
        active_mode_label = $ModeProfile.Label
        instance_name = $InstallInstanceName
        instance_store_root = $ModeProfile.InstanceStoreRoot
        install_root = $Root
        workspace = $workspaceForRunner
        runner = $runnerPath
        isolation_model = [ordered]@{
            per_install_namespace = $InstallInstanceName
            instance_store_root = $ModeProfile.InstanceStoreRoot
            shared_dependencies = @("Ollama", "Gitea", "Windows host services", "WSL host feature")
            note = "Install roots are replaceable code trees. Mode state, control roots, venvs, executor roots, WSL distro names, and WSL-scoped firewall rules live under the external instance store and are isolated within this install namespace. Ports are fixed by mode lane rather than namespaced per install."
        }
        concurrency_model = [ordered]@{
            model = "fixed-three-lane"
            mode_lanes = @("Unleashed Mode", "Debug", "Safe Mode")
            max_concurrent_active_lanes = 3
            one_active_install_per_mode_lane = $true
            same_mode_installs_on_disk_allowed = $true
            same_mode_concurrent_run = "not supported by default; stop the existing same-mode runner or deliberately supply custom ports"
            default_ports = [ordered]@{
                unleashed_app = $unleashedProfile.DefaultPort
                unleashed_heartbeat = $unleashedProfile.DefaultHeartbeatPort
                debug_app = $debugProfile.DefaultPort
                debug_heartbeat = $debugProfile.DefaultHeartbeatPort
                safe_app = $safeProfile.DefaultPort
                safe_heartbeat = $safeProfile.DefaultHeartbeatPort
                unleashed_onlyoffice = $unleashedProfile.DefaultOnlyOfficePort
                debug_onlyoffice = $debugProfile.DefaultOnlyOfficePort
                safe_onlyoffice = $safeProfile.DefaultOnlyOfficePort
                unleashed_coolify = $unleashedProfile.CoolifyPort
                debug_coolify = $debugProfile.CoolifyPort
                safe_coolify = $safeProfile.CoolifyPort
                unleashed_local_server = $unleashedProfile.DefaultLocalServerPortStart
                debug_local_server = $debugProfile.DefaultLocalServerPortStart
                safe_local_server = $safeProfile.DefaultLocalServerPortStart
            }
        }
        precheck_model = [ordered]@{
            prerequisite_ladder = @("Windows", "PowerShell", "Git", "source checkout", "install root", "Python", "WSL", "firewall", "Ollama")
            firewall_policy = "prefer WSL-scoped allow rules; warn on broad Python/port allow rules and lane-port allow rules"
        }
        modes = $modesManifest
    }
    Write-Utf8NoBomFile -Path $manifestPath -Text (($manifest | ConvertTo-Json -Depth 10) + "`n")

    $runnerTemplate = @'
# Generated by bootstrap-main-computer-windows.ps1.
# This runner is intentionally pinned to a user-facing Main Computer mode by default.
# Main Computer uses three fixed concurrent lanes by default: run at most one active Unleashed, one Debug, and one Safe runner per machine.
# Override with -Mode Unleashed, -Mode Debug, or -Mode Safe when you intentionally want a different mode.

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "run", "restart", "status", "stop", "shutdown", "install", "install-run", "smoke", "check")]
    [string]$Action = "start",

    [ValidateSet("", "Unleashed", "Unleashed Mode", "Debug", "Safe", "Safe Mode")]
    [string]$Mode = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$InstallRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PinnedMode = __PINNED_MODE__
$InstanceName = __INSTANCE_NAME__
$ConfiguredWsl = __WSL_PATH__
$ConfiguredWorkspace = __WORKSPACE__
$BindHost = __BIND_HOST__
$StartTimeoutSeconds = __START_TIMEOUT__

$UnleashedPort = __UNLEASHED_PORT__
$UnleashedHeartbeatPort = __UNLEASHED_HEARTBEAT_PORT__
$UnleashedPython = __UNLEASHED_PYTHON__
$UnleashedDistribution = __UNLEASHED_DISTRIBUTION__
$UnleashedControlRoot = __UNLEASHED_CONTROL_ROOT__
$UnleashedStateRoot = __UNLEASHED_STATE_ROOT__
$UnleashedExecutorRoot = __UNLEASHED_EXECUTOR_ROOT__
$UnleashedWslRuntimeRoot = __UNLEASHED_WSL_RUNTIME_ROOT__
$UnleashedOnlyOfficePort = __UNLEASHED_ONLYOFFICE_PORT__
$UnleashedOnlyOfficeProject = __UNLEASHED_ONLYOFFICE_PROJECT__
$UnleashedLocalServerProject = __UNLEASHED_LOCAL_SERVER_PROJECT__
$UnleashedLocalServerRegistry = __UNLEASHED_LOCAL_SERVER_REGISTRY__
$UnleashedLocalServerCompose = __UNLEASHED_LOCAL_SERVER_COMPOSE__
$UnleashedLocalServerPortStart = __UNLEASHED_LOCAL_SERVER_PORT_START__
$UnleashedLocalServerGeneratedPortStart = __UNLEASHED_LOCAL_SERVER_GENERATED_PORT_START__
$UnleashedLocalServerGeneratedPortEnd = __UNLEASHED_LOCAL_SERVER_GENERATED_PORT_END__

$DebugPort = __DEBUG_PORT__
$DebugHeartbeatPort = __DEBUG_HEARTBEAT_PORT__
$DebugPython = __DEBUG_PYTHON__
$DebugDistribution = __DEBUG_DISTRIBUTION__
$DebugControlRoot = __DEBUG_CONTROL_ROOT__
$DebugStateRoot = __DEBUG_STATE_ROOT__
$DebugExecutorRoot = __DEBUG_EXECUTOR_ROOT__
$DebugWslRuntimeRoot = __DEBUG_WSL_RUNTIME_ROOT__
$DebugOnlyOfficePort = __DEBUG_ONLYOFFICE_PORT__
$DebugOnlyOfficeProject = __DEBUG_ONLYOFFICE_PROJECT__
$DebugLocalServerProject = __DEBUG_LOCAL_SERVER_PROJECT__
$DebugLocalServerRegistry = __DEBUG_LOCAL_SERVER_REGISTRY__
$DebugLocalServerCompose = __DEBUG_LOCAL_SERVER_COMPOSE__
$DebugLocalServerPortStart = __DEBUG_LOCAL_SERVER_PORT_START__
$DebugLocalServerGeneratedPortStart = __DEBUG_LOCAL_SERVER_GENERATED_PORT_START__
$DebugLocalServerGeneratedPortEnd = __DEBUG_LOCAL_SERVER_GENERATED_PORT_END__

$SafePort = __SAFE_PORT__
$SafeHeartbeatPort = __SAFE_HEARTBEAT_PORT__
$SafePython = __SAFE_PYTHON__
$SafeDistribution = __SAFE_DISTRIBUTION__
$SafeControlRoot = __SAFE_CONTROL_ROOT__
$SafeStateRoot = __SAFE_STATE_ROOT__
$SafeExecutorRoot = __SAFE_EXECUTOR_ROOT__
$SafeWslRuntimeRoot = __SAFE_WSL_RUNTIME_ROOT__
$SafeOnlyOfficePort = __SAFE_ONLYOFFICE_PORT__
$SafeOnlyOfficeProject = __SAFE_ONLYOFFICE_PROJECT__
$SafeLocalServerProject = __SAFE_LOCAL_SERVER_PROJECT__
$SafeLocalServerRegistry = __SAFE_LOCAL_SERVER_REGISTRY__
$SafeLocalServerCompose = __SAFE_LOCAL_SERVER_COMPOSE__
$SafeLocalServerPortStart = __SAFE_LOCAL_SERVER_PORT_START__
$SafeLocalServerGeneratedPortStart = __SAFE_LOCAL_SERVER_GENERATED_PORT_START__
$SafeLocalServerGeneratedPortEnd = __SAFE_LOCAL_SERVER_GENERATED_PORT_END__
$UnleashedCoolifyProject = __UNLEASHED_COOLIFY_PROJECT__
$UnleashedCoolifyStateRoot = __UNLEASHED_COOLIFY_STATE_ROOT__
$UnleashedCoolifyPort = __UNLEASHED_COOLIFY_PORT__
$UnleashedCoolifySoketiPort = __UNLEASHED_COOLIFY_SOKETI_PORT__
$UnleashedCoolifySoketiTerminalPort = __UNLEASHED_COOLIFY_SOKETI_TERMINAL_PORT__
$DebugCoolifyProject = __DEBUG_COOLIFY_PROJECT__
$DebugCoolifyStateRoot = __DEBUG_COOLIFY_STATE_ROOT__
$DebugCoolifyPort = __DEBUG_COOLIFY_PORT__
$DebugCoolifySoketiPort = __DEBUG_COOLIFY_SOKETI_PORT__
$DebugCoolifySoketiTerminalPort = __DEBUG_COOLIFY_SOKETI_TERMINAL_PORT__
$SafeCoolifyProject = __SAFE_COOLIFY_PROJECT__
$SafeCoolifyStateRoot = __SAFE_COOLIFY_STATE_ROOT__
$SafeCoolifyPort = __SAFE_COOLIFY_PORT__
$SafeCoolifySoketiPort = __SAFE_COOLIFY_SOKETI_PORT__
$SafeCoolifySoketiTerminalPort = __SAFE_COOLIFY_SOKETI_TERMINAL_PORT__
$OnlyOfficeEnabled = __ONLYOFFICE_ENABLED__
$OnlyOfficeMode = __ONLYOFFICE_MODE__
$OnlyOfficeJwtSecret = __ONLYOFFICE_JWT_SECRET__
$LocalServerEnabled = __LOCAL_SERVER_ENABLED__
$LocalCoolifyEnabled = __LOCAL_COOLIFY_ENABLED__

function Resolve-RunnerMode {
    param([Parameter(Mandatory = $true)][string]$ModeName)

    $normalized = ($ModeName.Trim().ToLowerInvariant() -replace "\s+", "-")
    switch ($normalized) {
        "unleashed" { return [pscustomobject]@{ Key = "unleashed"; Label = "Unleashed Mode"; GuidanceLevel = "developer"; Port = $UnleashedPort; HeartbeatPort = $UnleashedHeartbeatPort; PythonPath = $UnleashedPython; Distribution = $UnleashedDistribution; ControlRoot = $UnleashedControlRoot; StateRoot = $UnleashedStateRoot; ExecutorRoot = $UnleashedExecutorRoot; WslRuntimeRoot = $UnleashedWslRuntimeRoot; OnlyOfficePort = $UnleashedOnlyOfficePort; OnlyOfficeProject = $UnleashedOnlyOfficeProject; LocalServerProject = $UnleashedLocalServerProject; LocalServerRegistry = $UnleashedLocalServerRegistry; LocalServerCompose = $UnleashedLocalServerCompose; LocalServerPortStart = $UnleashedLocalServerPortStart; LocalServerGeneratedPortStart = $UnleashedLocalServerGeneratedPortStart; LocalServerGeneratedPortEnd = $UnleashedLocalServerGeneratedPortEnd; CoolifyProject = $UnleashedCoolifyProject; CoolifyStateRoot = $UnleashedCoolifyStateRoot; CoolifyPort = $UnleashedCoolifyPort; CoolifySoketiPort = $UnleashedCoolifySoketiPort; CoolifySoketiTerminalPort = $UnleashedCoolifySoketiTerminalPort } }
        "unleashed-mode" { return (Resolve-RunnerMode -ModeName "Unleashed") }
        "debug" { return [pscustomobject]@{ Key = "debug"; Label = "Debug"; GuidanceLevel = "debug"; Port = $DebugPort; HeartbeatPort = $DebugHeartbeatPort; PythonPath = $DebugPython; Distribution = $DebugDistribution; ControlRoot = $DebugControlRoot; StateRoot = $DebugStateRoot; ExecutorRoot = $DebugExecutorRoot; WslRuntimeRoot = $DebugWslRuntimeRoot; OnlyOfficePort = $DebugOnlyOfficePort; OnlyOfficeProject = $DebugOnlyOfficeProject; LocalServerProject = $DebugLocalServerProject; LocalServerRegistry = $DebugLocalServerRegistry; LocalServerCompose = $DebugLocalServerCompose; LocalServerPortStart = $DebugLocalServerPortStart; LocalServerGeneratedPortStart = $DebugLocalServerGeneratedPortStart; LocalServerGeneratedPortEnd = $DebugLocalServerGeneratedPortEnd; CoolifyProject = $DebugCoolifyProject; CoolifyStateRoot = $DebugCoolifyStateRoot; CoolifyPort = $DebugCoolifyPort; CoolifySoketiPort = $DebugCoolifySoketiPort; CoolifySoketiTerminalPort = $DebugCoolifySoketiTerminalPort } }
        "safe" { return [pscustomobject]@{ Key = "safe"; Label = "Safe Mode"; GuidanceLevel = "guided"; Port = $SafePort; HeartbeatPort = $SafeHeartbeatPort; PythonPath = $SafePython; Distribution = $SafeDistribution; ControlRoot = $SafeControlRoot; StateRoot = $SafeStateRoot; ExecutorRoot = $SafeExecutorRoot; WslRuntimeRoot = $SafeWslRuntimeRoot; OnlyOfficePort = $SafeOnlyOfficePort; OnlyOfficeProject = $SafeOnlyOfficeProject; LocalServerProject = $SafeLocalServerProject; LocalServerRegistry = $SafeLocalServerRegistry; LocalServerCompose = $SafeLocalServerCompose; LocalServerPortStart = $SafeLocalServerPortStart; LocalServerGeneratedPortStart = $SafeLocalServerGeneratedPortStart; LocalServerGeneratedPortEnd = $SafeLocalServerGeneratedPortEnd; CoolifyProject = $SafeCoolifyProject; CoolifyStateRoot = $SafeCoolifyStateRoot; CoolifyPort = $SafeCoolifyPort; CoolifySoketiPort = $SafeCoolifySoketiPort; CoolifySoketiTerminalPort = $SafeCoolifySoketiTerminalPort } }
        "safe-mode" { return (Resolve-RunnerMode -ModeName "Safe") }
    }

    throw "Unknown Main Computer runner mode: $ModeName"
}

function Set-RunnerEnvironment {
    param([Parameter(Mandatory = $true)]$SelectedMode)

    $workspace = if ([string]::IsNullOrWhiteSpace($ConfiguredWorkspace)) { $InstallRoot } else { $ConfiguredWorkspace }

    $env:MAIN_COMPUTER_PYTHON = $SelectedMode.PythonPath
    $env:MAIN_COMPUTER_WORKSPACE = $workspace
    $env:MAIN_COMPUTER_INSTALL_MODE = $SelectedMode.Key
    $env:MAIN_COMPUTER_MODE_LABEL = $SelectedMode.Label
    $env:MAIN_COMPUTER_GUIDANCE_LEVEL = $SelectedMode.GuidanceLevel
    $env:MAIN_COMPUTER_SAFE_MODE = if ($SelectedMode.Key -eq "safe") { "1" } else { "0" }
    $env:MAIN_COMPUTER_INSTANCE_NAME = $InstanceName
    $env:MAIN_COMPUTER_STATE_ROOT = $SelectedMode.StateRoot
    $env:MAIN_COMPUTER_CONTROL_ROOT = $SelectedMode.ControlRoot
    $env:MAIN_COMPUTER_CONTROL_PORT = "$($SelectedMode.Port)"
    $env:MAIN_COMPUTER_HEARTBEAT_PORT = "$($SelectedMode.HeartbeatPort)"
    $env:MAIN_COMPUTER_EXECUTOR_ENABLED = "1"
    $env:MAIN_COMPUTER_EXECUTOR_BACKEND = "wsl"
    $env:MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION = $SelectedMode.Distribution
    $env:MAIN_COMPUTER_EXECUTOR_WSL_COMMAND = $ConfiguredWsl
    $env:MAIN_COMPUTER_EXECUTOR_ROOT = $SelectedMode.ExecutorRoot
    $env:MAIN_COMPUTER_PATH_MODE = "local"
    $env:MAIN_COMPUTER_HOST_OS = "windows"
    $env:MAIN_COMPUTER_GITEA_SCOPE = "shared-machine"
    $env:MAIN_COMPUTER_GITEA_ROOT_URL = "http://127.0.0.1:3000/"
    $env:MAIN_COMPUTER_GITEA_HTTP_PORT = "3000"
    $env:MAIN_COMPUTER_GITEA_COMPOSE_PROJECT = "main-computer-gitea"

    if ($LocalServerEnabled -eq "1") {
        $env:MAIN_COMPUTER_LOCAL_SERVER_ENABLED = "1"
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_MODE = $SelectedMode.Key
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT = $SelectedMode.LocalServerProject
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH = $SelectedMode.LocalServerRegistry
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH = $SelectedMode.LocalServerCompose
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_BUILTIN_PORT_START = "$($SelectedMode.LocalServerPortStart)"
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START = "$($SelectedMode.LocalServerGeneratedPortStart)"
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END = "$($SelectedMode.LocalServerGeneratedPortEnd)"
        $env:MAIN_COMPUTER_LOCAL_SERVER_URL = "http://127.0.0.1:$($SelectedMode.LocalServerPortStart)/"
    }
    else {
        $env:MAIN_COMPUTER_LOCAL_SERVER_ENABLED = "0"
    }

    if ($LocalCoolifyEnabled -eq "1") {
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
            if (-not [string]::IsNullOrWhiteSpace($tokenValue)) {
                $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN = $tokenValue
            }
        }
    }
    else {
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED = "0"
    }

    if ($OnlyOfficeEnabled -eq "1") {
        $env:MAIN_COMPUTER_ONLYOFFICE_ENABLED = "1"
        $env:MAIN_COMPUTER_ONLYOFFICE_MODE = $OnlyOfficeMode
        $env:MAIN_COMPUTER_ONLYOFFICE_PORT = "$($SelectedMode.OnlyOfficePort)"
        $env:MAIN_COMPUTER_ONLYOFFICE_PROJECT = $SelectedMode.OnlyOfficeProject
        $env:MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL = "http://127.0.0.1:$($SelectedMode.OnlyOfficePort)"
        $env:MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL = "http://127.0.0.1:$($SelectedMode.OnlyOfficePort)"
        $env:MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL = "http://host.docker.internal:$($SelectedMode.Port)"
        if (-not [string]::IsNullOrWhiteSpace($OnlyOfficeJwtSecret)) {
            $env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET = $OnlyOfficeJwtSecret
        }
    }
    else {
        $env:MAIN_COMPUTER_ONLYOFFICE_ENABLED = "0"
    }
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
        $listed = & $ConfiguredWsl --list --quiet 2>$null
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

function Invoke-InstalledModeCheck {
    param(
        [Parameter(Mandatory = $true)]$SelectedMode,
        [switch]$Soft
    )

    $script:ModeCheckFailures = 0
    $script:ModeCheckWarnings = 0

    Write-Host ""
    Write-Host "Main Computer quick installed environment check"
    Write-Host ("Mode: {0} [{1}]" -f $SelectedMode.Label, $SelectedMode.Key)
    Write-Host ("Install root: {0}" -f $InstallRoot)
    Write-Host "Shared services: Ollama and Gitea are machine-wide. Mode services: WSL executor, ONLYOFFICE, Local Server, and Local Coolify."

    $manifest = Join-Path $InstallRoot "main-computer-install.json"
    $runtimeManifest = Join-Path $InstallRoot "runtime\main-computer-install.json"
    if ((Test-Path -LiteralPath $manifest -PathType Leaf) -or (Test-Path -LiteralPath $runtimeManifest -PathType Leaf)) {
        Add-ModeCheckResult "Install manifest" "OK" "installed manifest found"
    }
    else {
        Add-ModeCheckResult "Install manifest" "FAIL" "main-computer-install.json is missing from the installed location"
    }

    if (Test-Path -LiteralPath $SelectedMode.PythonPath -PathType Leaf) {
        Add-ModeCheckResult "Python for mode" "OK" $SelectedMode.PythonPath
    }
    else {
        Add-ModeCheckResult "Python for mode" "FAIL" ("missing expected venv Python: {0}" -f $SelectedMode.PythonPath)
    }

    $modeScript = switch ($SelectedMode.Key) {
        "unleashed" { Join-Path $InstallRoot "dev-control.ps1" }
        "debug" { Join-Path $InstallRoot "proto-dev\proto-dev.ps1" }
        "safe" { Join-Path $InstallRoot "control-main-computer.ps1" }
        default { "" }
    }
    if (-not [string]::IsNullOrWhiteSpace($modeScript) -and (Test-Path -LiteralPath $modeScript -PathType Leaf)) {
        Add-ModeCheckResult "Mode runner dependency" "OK" $modeScript
    }
    else {
        Add-ModeCheckResult "Mode runner dependency" "FAIL" ("missing mode control script: {0}" -f $modeScript)
    }

    $wslCommand = Get-Command $ConfiguredWsl -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $wslCommand -and (Test-Path -LiteralPath $ConfiguredWsl -PathType Leaf)) {
        $wslCommand = [pscustomobject]@{ Source = $ConfiguredWsl }
    }
    if ($null -eq $wslCommand) {
        Add-ModeCheckResult "WSL command" "FAIL" ("not found: {0}" -f $ConfiguredWsl)
    }
    elseif (Test-WslDistributionKnown -Distribution $SelectedMode.Distribution) {
        Add-ModeCheckResult "WSL distro for mode" "OK" $SelectedMode.Distribution
    }
    else {
        Add-ModeCheckResult "WSL distro for mode" "FAIL" ("missing expected distro after install: {0}" -f $SelectedMode.Distribution)
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
        Add-ModeCheckResult "Gitea shared service" "FAIL" ("machine-wide Gitea is not reachable on port {0}; this should be one shared install, not one per Main Computer mode" -f $giteaPort)
    }

    if ($env:MAIN_COMPUTER_ONLYOFFICE_ENABLED -eq "1") {
        $onlyOfficePort = [int]$env:MAIN_COMPUTER_ONLYOFFICE_PORT
        if (Test-LocalTcpPortOpen -Port $onlyOfficePort) {
            Add-ModeCheckResult "ONLYOFFICE for mode" "OK" ("{0} on port {1}" -f $SelectedMode.OnlyOfficeProject, $onlyOfficePort)
        }
        else {
            Add-ModeCheckResult "ONLYOFFICE for mode" "FAIL" ("not reachable for {0}; expected project {1} on port {2}" -f $SelectedMode.Label, $SelectedMode.OnlyOfficeProject, $onlyOfficePort)
        }
    }
    else {
        Add-ModeCheckResult "ONLYOFFICE for mode" "SKIP" "disabled for this install"
    }

    if ($env:MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED -eq "1") {
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
    }
    else {
        Add-ModeCheckResult "Local Coolify for mode" "SKIP" "disabled for this install"
    }

    if ($env:MAIN_COMPUTER_LOCAL_SERVER_ENABLED -eq "1") {
        $registryParent = Split-Path -Parent $env:MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH
        if (-not [string]::IsNullOrWhiteSpace($registryParent) -and (Test-Path -LiteralPath $registryParent -PathType Container)) {
            Add-ModeCheckResult "Local Server state for mode" "OK" $registryParent
        }
        else {
            Add-ModeCheckResult "Local Server state for mode" "WARN" ("state directory not present yet: {0}" -f $registryParent)
        }
    }
    else {
        Add-ModeCheckResult "Local Server state for mode" "SKIP" "disabled for this install"
    }

    Write-Host ("Quick check summary: {0} failure(s), {1} warning(s)" -f $script:ModeCheckFailures, $script:ModeCheckWarnings)
    if ($script:ModeCheckFailures -gt 0 -and $Soft) {
        Write-Warning "Quick check found missing prerequisites. Start will continue so install/start can repair services that are designed to be brought up on demand."
    }

    return ($script:ModeCheckFailures -eq 0)
}

function Invoke-UnleashedMode {
    param([Parameter(Mandatory = $true)]$SelectedMode)
    if ($Action -in @("install", "install-run", "smoke")) { throw "Use bootstrap-main-computer-windows.ps1 for install/update work." }
    $controlAction = switch ($Action) { "run" { "start" } "stop" { "shutdown" } default { $Action } }
    $devControl = Join-Path $InstallRoot "dev-control.ps1"
    $devControlParams = @{
        Action = $controlAction
        Mode = "local"
        PythonPath = $SelectedMode.PythonPath
        BindHost = $BindHost
        LocalPort = [int]$SelectedMode.Port
        HeartbeatPort = [int]$SelectedMode.HeartbeatPort
        Workspace = $env:MAIN_COMPUTER_WORKSPACE
        ControlRoot = $SelectedMode.ControlRoot
    }
    & $devControl @devControlParams
    exit $LASTEXITCODE
}

function Invoke-DebugMode {
    param([Parameter(Mandatory = $true)]$SelectedMode)
    $debugAction = switch ($Action) { "start" { "run" } "run" { "run" } "stop" { "stop" } "shutdown" { "stop" } default { $Action } }
    $proto = Join-Path $InstallRoot "proto-dev\proto-dev.ps1"
    $protoParams = @{
        Action = $debugAction
        RepoRoot = $InstallRoot
        StateRoot = $SelectedMode.StateRoot
        Workspace = $env:MAIN_COMPUTER_WORKSPACE
        BindHost = "127.0.0.1"
        Port = [int]$SelectedMode.Port
        HeartbeatPort = [int]$SelectedMode.HeartbeatPort
        WslCommand = $ConfiguredWsl
        ExecutorDistribution = $SelectedMode.Distribution
        WslRuntimeRoot = $SelectedMode.WslRuntimeRoot
        StartTimeoutSeconds = [int]$StartTimeoutSeconds
    }
    & $proto @protoParams
    exit $LASTEXITCODE
}

function Invoke-SafeMode {
    param([Parameter(Mandatory = $true)]$SelectedMode)
    if ($Action -in @("install", "install-run", "smoke")) { throw "Use bootstrap-main-computer-windows.ps1 for install/update work." }
    $controlAction = switch ($Action) { "run" { "start" } "stop" { "shutdown" } default { $Action } }
    New-Item -ItemType Directory -Force -Path $SelectedMode.ControlRoot | Out-Null
    $control = Join-Path $InstallRoot "control-main-computer.ps1"
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
    & $control @controlParams
    exit $LASTEXITCODE
}

$modeToUse = if ([string]::IsNullOrWhiteSpace($Mode)) { $PinnedMode } else { $Mode }
$selectedMode = Resolve-RunnerMode -ModeName $modeToUse
Set-RunnerEnvironment -SelectedMode $selectedMode

Write-Host ("Main Computer {0}: {1} on http://127.0.0.1:{2} [{3}]" -f $Action, $selectedMode.Label, $selectedMode.Port, $InstanceName)

if ($Action -eq "check") {
    $checkOk = Invoke-InstalledModeCheck -SelectedMode $selectedMode
    if ($checkOk) { exit 0 }
    exit 2
}

if (@("start", "run", "restart", "install", "install-run") -contains $Action) {
    Invoke-InstalledModeCheck -SelectedMode $selectedMode -Soft | Out-Null
}

switch ($selectedMode.Key) {
    "unleashed" { Invoke-UnleashedMode -SelectedMode $selectedMode }
    "debug" { Invoke-DebugMode -SelectedMode $selectedMode }
    "safe" { Invoke-SafeMode -SelectedMode $selectedMode }
}
'@

    $runnerText = $runnerTemplate
    $runnerText = $runnerText.Replace("__PINNED_MODE__", (ConvertTo-PowerShellSingleQuotedLiteral $ModeProfile.Label))
    $runnerText = $runnerText.Replace("__INSTANCE_NAME__", (ConvertTo-PowerShellSingleQuotedLiteral $InstallInstanceName))
    $runnerText = $runnerText.Replace("__WSL_PATH__", (ConvertTo-PowerShellSingleQuotedLiteral $WslPath))
    $runnerText = $runnerText.Replace("__WORKSPACE__", (ConvertTo-PowerShellSingleQuotedLiteral $workspaceForRunner))
    $runnerText = $runnerText.Replace("__BIND_HOST__", (ConvertTo-PowerShellSingleQuotedLiteral $BindHost))
    $runnerText = $runnerText.Replace("__START_TIMEOUT__", ([string]$StartTimeoutSeconds))
    $runnerText = $runnerText.Replace("__ONLYOFFICE_ENABLED__", (ConvertTo-PowerShellSingleQuotedLiteral $(if ($script:EffectiveOnlyOfficeMode -eq "disabled") { "0" } else { "1" })))
    $runnerText = $runnerText.Replace("__ONLYOFFICE_MODE__", (ConvertTo-PowerShellSingleQuotedLiteral $script:EffectiveOnlyOfficeMode))
    $runnerText = $runnerText.Replace("__ONLYOFFICE_JWT_SECRET__", (ConvertTo-PowerShellSingleQuotedLiteral $(if ($env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET) { $env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET } else { "main-computer-onlyoffice-local-secret" })))
    $runnerText = $runnerText.Replace("__LOCAL_SERVER_ENABLED__", (ConvertTo-PowerShellSingleQuotedLiteral $(if ($LocalServerMode -eq "disabled") { "0" } else { "1" })))
    $runnerText = $runnerText.Replace("__LOCAL_COOLIFY_ENABLED__", (ConvertTo-PowerShellSingleQuotedLiteral $(if ($LocalCoolifyMode -eq "disabled") { "0" } else { "1" })))

    foreach ($entry in @(
        [pscustomobject]@{ Prefix = "UNLEASHED"; Profile = $unleashedProfile },
        [pscustomobject]@{ Prefix = "DEBUG"; Profile = $debugProfile },
        [pscustomobject]@{ Prefix = "SAFE"; Profile = $safeProfile }
    )) {
        $prefix = [string]$entry.Prefix
        $profile = $entry.Profile
        $runnerText = $runnerText.Replace("__${prefix}_PORT__", ([string]$profile.DefaultPort))
        $runnerText = $runnerText.Replace("__${prefix}_HEARTBEAT_PORT__", ([string]$profile.DefaultHeartbeatPort))
        $runnerText = $runnerText.Replace("__${prefix}_PYTHON__", (ConvertTo-PowerShellSingleQuotedLiteral $profile.VenvPython))
        $runnerText = $runnerText.Replace("__${prefix}_DISTRIBUTION__", (ConvertTo-PowerShellSingleQuotedLiteral $profile.DefaultExecutorDistribution))
        $runnerText = $runnerText.Replace("__${prefix}_CONTROL_ROOT__", (ConvertTo-PowerShellSingleQuotedLiteral $profile.ControlRoot))
        $runnerText = $runnerText.Replace("__${prefix}_STATE_ROOT__", (ConvertTo-PowerShellSingleQuotedLiteral $profile.StateRoot))
        $runnerText = $runnerText.Replace("__${prefix}_EXECUTOR_ROOT__", (ConvertTo-PowerShellSingleQuotedLiteral $profile.ExecutorRoot))
        $runnerText = $runnerText.Replace("__${prefix}_WSL_RUNTIME_ROOT__", (ConvertTo-PowerShellSingleQuotedLiteral $profile.WslRuntimeRoot))
        $runnerText = $runnerText.Replace("__${prefix}_ONLYOFFICE_PORT__", ([string]$profile.DefaultOnlyOfficePort))
        $runnerText = $runnerText.Replace("__${prefix}_ONLYOFFICE_PROJECT__", (ConvertTo-PowerShellSingleQuotedLiteral $profile.OnlyOfficeProjectName))
        $runnerText = $runnerText.Replace("__${prefix}_LOCAL_SERVER_PROJECT__", (ConvertTo-PowerShellSingleQuotedLiteral $profile.LocalServerProjectName))
        $runnerText = $runnerText.Replace("__${prefix}_LOCAL_SERVER_REGISTRY__", (ConvertTo-PowerShellSingleQuotedLiteral $profile.LocalServerRegistryPath))
        $runnerText = $runnerText.Replace("__${prefix}_LOCAL_SERVER_COMPOSE__", (ConvertTo-PowerShellSingleQuotedLiteral $profile.LocalServerComposePath))
        $runnerText = $runnerText.Replace("__${prefix}_LOCAL_SERVER_PORT_START__", ([string]$profile.DefaultLocalServerPortStart))
        $runnerText = $runnerText.Replace("__${prefix}_LOCAL_SERVER_GENERATED_PORT_START__", ([string]$profile.LocalServerGeneratedPortStart))
        $runnerText = $runnerText.Replace("__${prefix}_LOCAL_SERVER_GENERATED_PORT_END__", ([string]$profile.LocalServerGeneratedPortEnd))
        $runnerText = $runnerText.Replace("__${prefix}_COOLIFY_PROJECT__", (ConvertTo-PowerShellSingleQuotedLiteral $profile.CoolifyProjectName))
        $runnerText = $runnerText.Replace("__${prefix}_COOLIFY_STATE_ROOT__", (ConvertTo-PowerShellSingleQuotedLiteral $profile.CoolifyStateRoot))
        $runnerText = $runnerText.Replace("__${prefix}_COOLIFY_PORT__", ([string]$profile.CoolifyPort))
        $runnerText = $runnerText.Replace("__${prefix}_COOLIFY_SOKETI_PORT__", ([string]$profile.CoolifySoketiPort))
        $runnerText = $runnerText.Replace("__${prefix}_COOLIFY_SOKETI_TERMINAL_PORT__", ([string]$profile.CoolifySoketiTerminalPort))
    }

    Write-Utf8NoBomFile -Path $runnerPath -Text $runnerText
    Add-BootstrapStatus "Mode runner" "OK" "$runnerPath -> $($ModeProfile.Label) [$InstallInstanceName]"
    $script:GeneratedRunnerPath = $runnerPath
    return $runnerPath
}


function Get-DefaultExecutorDistribution {
    param([Parameter(Mandatory = $true)][string]$Profile)

    if ($Profile -eq "prod") {
        return "MainComputerExecutor"
    }
    return "MainComputerExecutorTest"
}

function ConvertTo-RuntimeImageFileName {
    param([Parameter(Mandatory = $true)][string]$DistributionName)

    $name = $DistributionName.Trim()
    foreach ($character in [System.IO.Path]::GetInvalidFileNameChars()) {
        $name = $name.Replace([string]$character, "-")
    }
    $name = ($name -replace "\s+", "-").Trim("-")
    if ([string]::IsNullOrWhiteSpace($name)) {
        $name = "main-computer-executor"
    }
    return "$name-rootfs.tar"
}

function Get-RuntimeImagePath {
    param(
        [Parameter(Mandatory = $true)][string]$Profile,
        [Parameter(Mandatory = $true)][string]$Root,
        [string]$Distribution = "",
        [string]$WslRuntimeRoot = ""
    )

    if ([string]::IsNullOrWhiteSpace($Distribution)) {
        $Distribution = Get-DefaultExecutorDistribution -Profile $Profile
    }
    if ([string]::IsNullOrWhiteSpace($WslRuntimeRoot)) {
        if ($Profile -eq "prod") {
            $WslRuntimeRoot = Join-Path $env:LOCALAPPDATA "MainComputer\wsl"
        }
        else {
            $WslRuntimeRoot = Join-Path $env:LOCALAPPDATA "MainComputer\wsl-test"
        }
    }

    return Join-Path (Join-Path $WslRuntimeRoot "images") (ConvertTo-RuntimeImageFileName -DistributionName $Distribution)
}

function Get-RuntimeConfigPath {
    param([Parameter(Mandatory = $true)][string]$Profile)

    if ($Profile -eq "prod") {
        return Join-Path $env:LOCALAPPDATA "MainComputer\main-computer-runtime.json"
    }
    return Join-Path $env:LOCALAPPDATA "MainComputer\main-computer-runtime.test.json"
}

function Read-RuntimeConfig {
    param([Parameter(Mandatory = $true)][string]$Profile)

    $configPath = Get-RuntimeConfigPath -Profile $Profile
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        return $null
    }

    try {
        return Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    }
    catch {
        Write-Warning "Could not read runtime config $configPath`: $($_.Exception.Message)"
        return $null
    }
}

function Get-WslDistributions {
    param([Parameter(Mandatory = $true)][string]$CommandPath)

    $result = Invoke-Native -FilePath $CommandPath -Arguments @("--list", "--quiet") -Quiet -TimeoutSeconds $PrecheckCommandTimeoutSeconds
    if ($result.ExitCode -ne 0) {
        return @()
    }

    $text = (($result.Stdout, $result.Stderr) -join "`n") -replace "`0", ""
    return @(
        $text -split "(`r`n|`n|`r)" |
            ForEach-Object { $_.Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
}

function Test-WslDistributionInstalled {
    param(
        [Parameter(Mandatory = $true)][string]$CommandPath,
        [Parameter(Mandatory = $true)][string]$Distribution
    )

    return @(Get-WslDistributions -CommandPath $CommandPath) -contains $Distribution
}


function Test-WindowsAppsPythonPath {
    param([AllowNull()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }

    return (
        $Path -like "*\WindowsApps\*" -or
        $Path -like "*\Microsoft\WindowsApps\*"
    )
}

function Resolve-UserToolRoot {
    $userProfileRoot = [Environment]::GetFolderPath("UserProfile")
    if ([string]::IsNullOrWhiteSpace($userProfileRoot)) {
        $userProfileRoot = $env:USERPROFILE
    }
    if ([string]::IsNullOrWhiteSpace($userProfileRoot)) {
        Fail "Could not determine the user profile directory for the Main Computer tool cache."
    }
    return (Join-Path ([System.IO.Path]::GetFullPath($userProfileRoot)) ".main-computer-tools")
}

function Resolve-ManagedPythonRoot {
    if (-not [string]::IsNullOrWhiteSpace($ManagedPythonRoot)) {
        return [System.IO.Path]::GetFullPath($ManagedPythonRoot).TrimEnd([char[]]@('\', '/'))
    }

    return (Join-Path (Join-Path (Resolve-UserToolRoot) "cpython") "3.12-amd64")
}

function Resolve-PythonDownloadRoot {
    if (-not [string]::IsNullOrWhiteSpace($PythonDownloadRoot)) {
        return [System.IO.Path]::GetFullPath($PythonDownloadRoot).TrimEnd([char[]]@('\', '/'))
    }

    return (Join-Path (Resolve-UserToolRoot) "downloads")
}

function Get-ManagedPythonCurrentPointerPath {
    $managedRoot = Resolve-ManagedPythonRoot
    $parent = Split-Path -Parent $managedRoot
    if ([string]::IsNullOrWhiteSpace($parent)) {
        $parent = $managedRoot
    }
    return (Join-Path $parent "current-python.txt")
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

function Get-PythonExecutableVirtualEnvironmentRoot {
    param([AllowNull()][string]$PythonPath)

    if ([string]::IsNullOrWhiteSpace($PythonPath)) {
        return ""
    }

    try {
        $fullPath = [System.IO.Path]::GetFullPath($PythonPath)
    }
    catch {
        return ""
    }

    $scriptsDir = Split-Path -Parent $fullPath
    if ([string]::IsNullOrWhiteSpace($scriptsDir)) {
        return ""
    }

    if ((Split-Path -Leaf $scriptsDir) -ine "Scripts") {
        return ""
    }

    $venvRoot = Split-Path -Parent $scriptsDir
    if ([string]::IsNullOrWhiteSpace($venvRoot)) {
        return ""
    }

    if (Test-Path -LiteralPath (Join-Path $venvRoot "pyvenv.cfg") -PathType Leaf) {
        return $venvRoot
    }

    return ""
}

function Resolve-BasePythonFromVirtualEnvironment {
    param([Parameter(Mandatory = $true)][string]$VirtualEnvPython)

    $venvRoot = Get-PythonExecutableVirtualEnvironmentRoot -PythonPath $VirtualEnvPython
    if ([string]::IsNullOrWhiteSpace($venvRoot)) {
        return ""
    }

    $configPath = Join-Path $venvRoot "pyvenv.cfg"
    try {
        $configLines = @(Get-Content -LiteralPath $configPath -ErrorAction Stop)
    }
    catch {
        $configLines = @()
    }

    $candidates = @()
    foreach ($line in $configLines) {
        if ($line -match '^\s*executable\s*=\s*(.+?)\s*$') {
            $candidates += $matches[1].Trim().Trim('"')
        }
    }
    foreach ($line in $configLines) {
        if ($line -match '^\s*home\s*=\s*(.+?)\s*$') {
            $homePath = $matches[1].Trim().Trim('"')
            if (-not [string]::IsNullOrWhiteSpace($homePath)) {
                $candidates += (Join-Path $homePath "python.exe")
            }
        }
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        try {
            $resolvedCandidate = [System.IO.Path]::GetFullPath($candidate.Trim().Trim('"'))
        }
        catch {
            continue
        }
        if ($resolvedCandidate -ieq ([System.IO.Path]::GetFullPath($VirtualEnvPython))) {
            continue
        }
        if (-not (Test-Path -LiteralPath $resolvedCandidate -PathType Leaf)) {
            continue
        }
        if (-not [string]::IsNullOrWhiteSpace((Get-PythonExecutableVirtualEnvironmentRoot -PythonPath $resolvedCandidate))) {
            continue
        }
        if (Test-BasePythonCandidateLaunches -PythonPath $resolvedCandidate) {
            return $resolvedCandidate
        }
    }

    return ""
}

function Get-WindowsPythonInstallCandidates {
    $candidates = @()

    $registryRoots = @(
        "Registry::HKEY_CURRENT_USER\Software\Python\PythonCore",
        "Registry::HKEY_LOCAL_MACHINE\Software\Python\PythonCore",
        "Registry::HKEY_LOCAL_MACHINE\Software\Wow6432Node\Python\PythonCore"
    )

    foreach ($registryRoot in $registryRoots) {
        if (-not (Test-Path -LiteralPath $registryRoot)) {
            continue
        }

        foreach ($versionKey in @(Get-ChildItem -LiteralPath $registryRoot -ErrorAction SilentlyContinue)) {
            $installPathKey = Join-Path $versionKey.PSPath "InstallPath"
            $installKey = Get-Item -LiteralPath $installPathKey -ErrorAction SilentlyContinue
            if ($null -eq $installKey) {
                continue
            }

            $executablePath = $installKey.GetValue("ExecutablePath")
            if (-not [string]::IsNullOrWhiteSpace($executablePath)) {
                $candidates += [string]$executablePath
            }

            $homePath = $installKey.GetValue("")
            if (-not [string]::IsNullOrWhiteSpace($homePath)) {
                $candidates += (Join-Path ([string]$homePath) "python.exe")
            }
        }
    }

    $commonPatterns = @()
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $commonPatterns += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python*\python.exe")
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $commonPatterns += (Join-Path $env:ProgramFiles "Python*\python.exe")
    }
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $commonPatterns += (Join-Path ${env:ProgramFiles(x86)} "Python*\python.exe")
    }

    foreach ($pattern in $commonPatterns) {
        foreach ($match in @(Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue)) {
            if ($null -ne $match -and -not [string]::IsNullOrWhiteSpace($match.FullName)) {
                $candidates += $match.FullName
            }
        }
    }

    return @($candidates | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)
}

function Test-BasePythonCandidateLaunches {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [switch]$SkipVenvCreateProbe
    )

    if ([string]::IsNullOrWhiteSpace($PythonPath)) {
        return $false
    }

    if ((Split-Path -Leaf $PythonPath) -ieq "py.exe") {
        return $false
    }

    try {
        $resolved = [System.IO.Path]::GetFullPath($PythonPath.Trim().Trim('"'))
    }
    catch {
        return $false
    }

    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        return $false
    }

    if ((Test-WindowsAppsPythonPath -Path $resolved) -and -not $AllowWindowsAppsPython) {
        return $false
    }

    if (-not [string]::IsNullOrWhiteSpace((Get-PythonExecutableVirtualEnvironmentRoot -PythonPath $resolved))) {
        return $false
    }

    $probeCode = "import sys; print(sys.executable); print(sys.version); print(sys.prefix); print(sys.base_prefix); raise SystemExit(0 if sys.version_info >= (3, 10) and sys.prefix == sys.base_prefix else 1)"
    $probe = Invoke-Native -FilePath $resolved -Arguments @("-c", $probeCode) -Quiet -TimeoutSeconds 15
    if ($probe.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($probe.Stdout)) {
        return $false
    }

    if ($SkipVenvCreateProbe) {
        return $true
    }

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("main-computer-base-python-probe-" + [System.Guid]::NewGuid().ToString("N"))
    try {
        $venvProbe = Invoke-Native -FilePath $resolved -Arguments @("-m", "venv", "--without-pip", $tempRoot) -Quiet -TimeoutSeconds 30
        $tempPython = Join-Path $tempRoot "Scripts\python.exe"
        return ($venvProbe.ExitCode -eq 0 -and (Test-Path -LiteralPath $tempPython -PathType Leaf))
    }
    finally {
        if (Test-Path -LiteralPath $tempRoot) {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Invoke-BasePython {
    param(
        [Parameter(Mandatory = $true)][string]$BasePython,
        [string[]]$Arguments = @(),
        [switch]$Quiet,
        [string]$WorkingDirectory = "",
        [int]$TimeoutSeconds = 0
    )

    return Invoke-Native -FilePath $BasePython -Arguments $Arguments -Quiet:$Quiet -WorkingDirectory $WorkingDirectory -TimeoutSeconds $TimeoutSeconds
}

function Invoke-BasePythonChecked {
    param(
        [Parameter(Mandatory = $true)][string]$BasePython,
        [string[]]$Arguments = @(),
        [switch]$Quiet,
        [string]$WorkingDirectory = "",
        [int]$TimeoutSeconds = 0
    )

    return Invoke-NativeChecked -FilePath $BasePython -Arguments $Arguments -Quiet:$Quiet -WorkingDirectory $WorkingDirectory -TimeoutSeconds $TimeoutSeconds
}

function Download-FileBounded {
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

    $request = [System.Net.HttpWebRequest]::Create($Uri)
    $request.Method = "GET"
    $request.Timeout = $TimeoutSeconds * 1000
    $request.ReadWriteTimeout = $TimeoutSeconds * 1000
    $request.AllowAutoRedirect = $true
    $request.UserAgent = "main-computer-bootstrap/1.0"

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

function Download-TextBounded {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$TimeoutSeconds = 180
    )

    if ($NoPythonDownload) {
        Fail "Python metadata download is disabled by -NoPythonDownload: $Uri"
    }

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    }
    catch {
        # Best effort for older Windows PowerShell hosts.
    }

    $request = [System.Net.HttpWebRequest]::Create($Uri)
    $request.Method = "GET"
    $request.Timeout = $TimeoutSeconds * 1000
    $request.ReadWriteTimeout = $TimeoutSeconds * 1000
    $request.AllowAutoRedirect = $true
    $request.UserAgent = "main-computer-bootstrap/1.0"

    $response = $null
    $stream = $null
    $reader = $null
    try {
        $response = $request.GetResponse()
        $stream = $response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        return $reader.ReadToEnd()
    }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($stream) { $stream.Dispose() }
        if ($response) { $response.Dispose() }
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

function Get-PythonNuGetPackageCachePath {
    return (Join-Path (Resolve-PythonDownloadRoot) "python.$PythonNuGetVersion.nupkg")
}

function Get-PipWheelCachePath {
    return (Join-Path (Resolve-PythonDownloadRoot) "pip-$PipWheelVersion-py3-none-any.whl")
}

function Get-PipWheelUrl {
    $metadataUrl = "https://pypi.org/pypi/pip/$PipWheelVersion/json"
    $metadataText = Download-TextBounded -Uri $metadataUrl -TimeoutSeconds 180
    $metadata = $metadataText | ConvertFrom-Json

    foreach ($urlInfo in $metadata.urls) {
        if ($urlInfo.packagetype -eq "bdist_wheel" -and $urlInfo.filename -eq "pip-$PipWheelVersion-py3-none-any.whl") {
            return [string]$urlInfo.url
        }
    }

    Fail "Could not find pip wheel URL in PyPI metadata for pip $PipWheelVersion."
}

function Ensure-PipWheel {
    $wheelPath = Get-PipWheelCachePath
    if (Test-Path -LiteralPath $wheelPath -PathType Leaf) {
        $existing = Get-Item -LiteralPath $wheelPath
        if ($existing.Length -gt 100000) {
            return $wheelPath
        }
        Remove-Item -LiteralPath $wheelPath -Force
    }

    Write-Host "Downloading pinned pip wheel: pip $PipWheelVersion"
    $wheelUrl = Get-PipWheelUrl
    Download-FileBounded -Uri $wheelUrl -OutFile $wheelPath -TimeoutSeconds 180
    return $wheelPath
}

function Get-PythonStdoutLines {
    param(
        [Parameter(Mandatory = $true)]$Result
    )

    if ([string]::IsNullOrWhiteSpace($Result.Stdout)) {
        return @()
    }
    return @($Result.Stdout -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Seed-VenvPipFromWheel {
    param(
        [Parameter(Mandatory = $true)][string]$VenvPython,
        [Parameter(Mandatory = $true)][string]$VenvRoot,
        [Parameter(Mandatory = $true)][string]$Root,
        [string]$PipWheelPath = "",
        [switch]$ReturnResult
    )

    try {
        if ([string]::IsNullOrWhiteSpace($PipWheelPath)) {
            $PipWheelPath = Ensure-PipWheel
        }

        if (-not (Test-Path -LiteralPath $PipWheelPath -PathType Leaf)) {
            throw "Pinned pip wheel was not found: $PipWheelPath"
        }

        $targetPurelib = Join-Path $VenvRoot "Lib\site-packages"
        if (-not (Test-Path -LiteralPath $targetPurelib -PathType Container)) {
            throw "Venv site-packages directory was not found: $targetPurelib"
        }

        $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("main-computer-pip-wheel-" + [System.Guid]::NewGuid().ToString("N"))
        try {
            Expand-ZipArchiveToDirectory -ZipPath $PipWheelPath -Destination $tempRoot

            foreach ($child in Get-ChildItem -LiteralPath $tempRoot -Force) {
                $target = Join-Path $targetPurelib $child.Name
                if (Test-Path -LiteralPath $target) {
                    Remove-Item -LiteralPath $target -Recurse -Force
                }
                Copy-Item -LiteralPath $child.FullName -Destination $target -Recurse -Force
            }
        }
        finally {
            if (Test-Path -LiteralPath $tempRoot) {
                Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
            }
        }

        $pipCheck = Invoke-Native -FilePath $VenvPython -Arguments @("-m", "pip", "--version") -Quiet -WorkingDirectory $Root -TimeoutSeconds 30
        $pipText = (($pipCheck.Stderr, $pipCheck.Stdout) -join "`n").Trim()
        if ($pipCheck.TimedOut) {
            throw "Timed out after 30 seconds while verifying pip after wheel extraction."
        }
        if ($pipCheck.ExitCode -ne 0) {
            if ([string]::IsNullOrWhiteSpace($pipText)) {
                $pipText = "No output was captured from venv pip after wheel extraction."
            }
            throw "Extracted the pip wheel into the venv, but venv python -m pip still failed.`n$pipText"
        }

        $message = "Seeded pip into venv site-packages from pinned wheel: $PipWheelPath"
        if ($ReturnResult) {
            return [pscustomobject]@{ Ok = $true; Message = $message }
        }
        return
    }
    catch {
        $message = $_.Exception.Message
        if ($ReturnResult) {
            return [pscustomobject]@{ Ok = $false; Message = $message }
        }
        Fail "Could not seed pip into Windows virtual environment from the pinned pip wheel. $message"
    }
}

function Ensure-VenvPip {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$VenvRoot,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $pipCheck = Invoke-Native -FilePath $PythonPath -Arguments @("-m", "pip", "--version") -Quiet -WorkingDirectory $Root -TimeoutSeconds 15
    if ($pipCheck.ExitCode -eq 0) {
        return
    }

    Write-Host "Seeding pip in Windows virtual environment from pinned wheel."
    Seed-VenvPipFromWheel -VenvPython $PythonPath -VenvRoot $VenvRoot -Root $Root
}

function Install-ManagedPythonFromNuGet {
    $managedRoot = Resolve-ManagedPythonRoot
    $parentRoot = Split-Path -Parent $managedRoot
    if ([string]::IsNullOrWhiteSpace($parentRoot)) {
        $parentRoot = $managedRoot
    }
    if (-not (Test-Path -LiteralPath $parentRoot -PathType Container)) {
        New-Item -ItemType Directory -Force -Path $parentRoot | Out-Null
    }

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stageExtractRoot = Join-Path (Resolve-PythonDownloadRoot) ("python-nuget-extract-$PythonNuGetVersion-$timestamp-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
    $versionedRoot = Join-Path $parentRoot "$PythonNuGetVersion-nuget-amd64-$timestamp"
    $packagePath = Get-PythonNuGetPackageCachePath
    $packageUrl = "https://www.nuget.org/api/v2/package/python/$PythonNuGetVersion"

    try {
        if (Test-Path -LiteralPath $versionedRoot) {
            Remove-Item -LiteralPath $versionedRoot -Recurse -Force
        }

        Write-Host "Provisioning managed CPython from official NuGet package."
        Write-Host "Python package URL: $packageUrl"
        Write-Host "Python package cache: $packagePath"
        Write-Host "Managed Python target: $versionedRoot"

        Download-FileBounded -Uri $packageUrl -OutFile $packagePath -TimeoutSeconds 180
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
        if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
            Fail "Managed CPython copy completed but python.exe is missing: $pythonExe"
        }

        if (-not (Test-BasePythonCandidateLaunches -PythonPath $pythonExe)) {
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

function Resolve-BasePython {
    param(
        [switch]$AllowProvision,
        [switch]$SoftFail
    )

    if (-not [string]::IsNullOrWhiteSpace($PythonCommand)) {
        $hasPathSeparator = $PythonCommand.Contains("\") -or $PythonCommand.Contains("/") -or [System.IO.Path]::IsPathRooted($PythonCommand)
        if (-not $hasPathSeparator) {
            Fail "-PythonCommand must be an exact python.exe path. PATH aliases such as '$PythonCommand' are not supported for bootstrap."
        }

        $resolved = Resolve-CommandPath $PythonCommand
        if ($null -eq $resolved) {
            Fail "Python command was not found: $PythonCommand"
        }

        if ((Test-WindowsAppsPythonPath -Path $resolved) -and -not $AllowWindowsAppsPython) {
            Fail "Microsoft Store / WindowsApps Python is not supported for Main Computer bootstrap: $resolved"
        }

        $virtualEnvRoot = Get-PythonExecutableVirtualEnvironmentRoot -PythonPath $resolved
        if (-not [string]::IsNullOrWhiteSpace($virtualEnvRoot)) {
            $baseFromVirtualEnv = Resolve-BasePythonFromVirtualEnvironment -VirtualEnvPython $resolved
            if (-not [string]::IsNullOrWhiteSpace($baseFromVirtualEnv)) {
                return $baseFromVirtualEnv
            }
            Fail "Python command points inside a virtual environment and no supported base executable could be resolved from it: $resolved (venv root: $virtualEnvRoot). Pass -PythonCommand with a real CPython executable, not a virtual environment Python."
        }

        if (-not (Test-BasePythonCandidateLaunches -PythonPath $resolved)) {
            Fail "Python command could not launch a supported CPython interpreter: $resolved."
        }

        return $resolved
    }

    $candidates = @()
    $currentPointer = Read-ManagedPythonCurrentPointer
    if (-not [string]::IsNullOrWhiteSpace($currentPointer)) {
        $candidates += $currentPointer
    }

    $managedRootPython = Join-Path (Resolve-ManagedPythonRoot) "python.exe"
    $candidates += $managedRootPython

    foreach ($installedPython in @(Get-WindowsPythonInstallCandidates)) {
        $candidates += $installedPython
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }

        try {
            $resolved = [System.IO.Path]::GetFullPath($candidate.Trim().Trim('"'))
        }
        catch {
            continue
        }

        if (Test-BasePythonCandidateLaunches -PythonPath $resolved) {
            return $resolved
        }
    }

    if ($AllowProvision) {
        if ($NoPythonDownload) {
            Fail "No supported CPython runtime found, and -NoPythonDownload was set. Microsoft Store / WindowsApps Python is not supported."
        }
        return (Install-ManagedPythonFromNuGet)
    }

    if ($SoftFail) {
        return ""
    }

    Fail "No supported CPython runtime found. Microsoft Store / WindowsApps Python, PATH aliases, py.exe, and active virtualenv Python are not supported for Main Computer bootstrap."
}


function ConvertTo-ComparablePath {
    param([AllowNull()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return ""
    }

    try {
        return ([System.IO.Path]::GetFullPath($Path.Trim().Trim('"'))).TrimEnd([char[]]@('\\', '/')).ToLowerInvariant()
    }
    catch {
        return $Path.Trim().Trim('"').TrimEnd([char[]]@('\\', '/')).ToLowerInvariant()
    }
}

function Get-PythonRuntimeIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [string]$Root = "",
        [int]$TimeoutSeconds = 30
    )

    $identityCode = "import sys; print('sys.executable=' + sys.executable); print('sys.prefix=' + sys.prefix); print('sys.base_prefix=' + sys.base_prefix); print('sys._base_executable=' + str(getattr(sys, '_base_executable', '')))"
    $result = Invoke-Native -FilePath $PythonPath -Arguments @("-c", $identityCode) -Quiet -WorkingDirectory $Root -TimeoutSeconds $TimeoutSeconds
    if ($result.ExitCode -ne 0) {
        $details = (($result.Stderr, $result.Stdout) -join "`n").Trim()
        if ([string]::IsNullOrWhiteSpace($details)) {
            $details = "No output captured."
        }
        throw "Python identity probe failed for $PythonPath.`n$details"
    }

    $identity = @{}
    foreach ($line in @(Get-PythonStdoutLines -Result $result)) {
        if ($line -match "^([^=]+)=(.*)$") {
            $identity[$matches[1]] = $matches[2]
        }
    }
    return $identity
}

function Write-PythonRuntimeIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$Label,
        [string]$Root = ""
    )

    Write-Host "${Label}:"
    Write-Host "  $PythonPath"
    $identity = Get-PythonRuntimeIdentity -PythonPath $PythonPath -Root $Root -TimeoutSeconds 30
    foreach ($key in @("sys.executable", "sys.prefix", "sys.base_prefix", "sys._base_executable")) {
        if ($identity.ContainsKey($key)) {
            Write-Host "$key=$($identity[$key])"
        }
    }
    return $identity
}

function Test-VenvPythonMatchesBasePython {
    param(
        [Parameter(Mandatory = $true)][string]$VenvPython,
        [Parameter(Mandatory = $true)][string]$BasePython,
        [Parameter(Mandatory = $true)][string]$Root
    )

    try {
        $identity = Get-PythonRuntimeIdentity -PythonPath $VenvPython -Root $Root -TimeoutSeconds 30
    }
    catch {
        Write-Host "Existing Windows virtual environment identity check failed; it will be rebuilt."
        Write-Host $_.Exception.Message
        return $false
    }

    $expectedBase = ConvertTo-ComparablePath -Path $BasePython
    $actualBase = ""
    if ($identity.ContainsKey("sys._base_executable")) {
        $actualBase = ConvertTo-ComparablePath -Path $identity["sys._base_executable"]
    }

    if (-not [string]::IsNullOrWhiteSpace($actualBase) -and $actualBase -eq $expectedBase) {
        return $true
    }

    Write-Host "Existing Windows virtual environment uses a different base Python; it will be rebuilt."
    Write-Host "Expected base Python:"
    Write-Host "  $BasePython"
    if ($identity.ContainsKey("sys._base_executable")) {
        Write-Host "Existing venv sys._base_executable:"
        Write-Host "  $($identity["sys._base_executable"])"
    }
    if ($identity.ContainsKey("sys.base_prefix")) {
        Write-Host "Existing venv sys.base_prefix:"
        Write-Host "  $($identity["sys.base_prefix"])"
    }
    return $false
}


function Ensure-VenvPython {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$BasePython
    )

    if ([string]::IsNullOrWhiteSpace($VenvPath)) {
        $VenvPath = Join-Path $Root ".venv"
    }

    $resolvedVenv = [System.IO.Path]::GetFullPath($VenvPath)
    $venvPython = Join-Path $resolvedVenv "Scripts\python.exe"

    $needsCreate = $false
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $needsCreate = $true
        if (Test-Path -LiteralPath $resolvedVenv -PathType Container) {
            Write-Host "Removing incomplete Windows virtual environment: $resolvedVenv"
            Remove-Item -LiteralPath $resolvedVenv -Recurse -Force -ErrorAction Stop
        }
    }
    elseif (-not (Test-VenvPythonMatchesBasePython -VenvPython $venvPython -BasePython $BasePython -Root $Root)) {
        Remove-Item -LiteralPath $resolvedVenv -Recurse -Force -ErrorAction Stop
        $needsCreate = $true
    }
    else {
        Write-Host "Using existing Windows virtual environment: $resolvedVenv"
    }

    if ($needsCreate) {
        $venvParent = Split-Path -Parent $resolvedVenv
        if (-not (Test-Path -LiteralPath $venvParent -PathType Container)) {
            New-Item -ItemType Directory -Path $venvParent -Force | Out-Null
        }
        Write-Host "Creating Windows virtual environment without pip: $resolvedVenv"
        Write-Host "Base Python for venv:"
        Write-Host "  $BasePython"
        Invoke-NativeCheckedWithPreview -Label "Venv creation" -FilePath $BasePython -Arguments @("-m", "venv", "--without-pip", $resolvedVenv) -TimeoutSeconds 60 | Out-Null
    }

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        Fail "Virtual environment did not create python.exe at $venvPython"
    }

    $resolvedVenvPython = (Resolve-Path -LiteralPath $venvPython).Path
    Write-PythonRuntimeIdentity -PythonPath $resolvedVenvPython -Label "Windows venv Python path" -Root $Root | Out-Null

    if (-not $SkipDependencyInstall) {
        Ensure-VenvPip -PythonPath $resolvedVenvPython -VenvRoot $resolvedVenv -Root $Root
    }
    return $resolvedVenvPython
}

function Install-MathicsOptionalDependency {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$Root
    )

    if ($MathicsInstallMode -eq "disabled") {
        Write-Host "Skipping Mathics optional dependency install by default. Pass -MathicsInstallMode auto or -MathicsInstallMode required to attempt it."
        Add-BootstrapStatus "Mathics optional dependency" "SKIP" "MathicsInstallMode is disabled."
        return
    }

    Write-Host "Installing Mathics optional dependency separately with binary wheels only."
    Write-Host "This avoids source-build stalls during bootstrap."

    $mathicsArgs = @(
        "-m", "pip", "install",
        "--disable-pip-version-check",
        "--no-input",
        "--progress-bar", "off",
        "--timeout", "30",
        "--retries", "2",
        "--only-binary", ":all:",
        "Mathics3==10.0.0"
    )

    try {
        Invoke-NativeCheckedWithPreview -Label "Mathics optional dependency install" -FilePath $PythonPath -Arguments $mathicsArgs -WorkingDirectory $Root -TimeoutSeconds 300 | Out-Null
        Add-BootstrapStatus "Mathics optional dependency" "OK" "Installed Mathics3==10.0.0 from binary wheels."
    }
    catch {
        $message = $_.Exception.Message
        if ($MathicsInstallMode -eq "required") {
            Fail "Mathics optional dependency is required but could not be installed without source builds. $message"
        }

        Write-Host "WARN - Mathics optional dependency could not be installed without source builds."
        Write-Host $message
        Add-BootstrapStatus "Mathics optional dependency" "WARN" "MathicsInstallMode=auto; continuing without Mathics because binary-wheel install failed."
    }
}

function Install-PythonDependencies {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$Root
    )

    if ($SkipDependencyInstall) {
        Add-BootstrapStatus "Python dependencies" "SKIP" "-SkipDependencyInstall was set."
        return
    }

    Write-Host "Verifying package install Python before dependency install."
    Write-PythonRuntimeIdentity -PythonPath $PythonPath -Label "Package install Python path" -Root $Root | Out-Null

    Write-Host "Checking pip in Windows venv."
    Invoke-NativeCheckedWithPreview -Label "Pip check" -FilePath $PythonPath -Arguments @("-m", "pip", "--version") -WorkingDirectory $Root -TimeoutSeconds 30 | Out-Null

    Write-Host "Skipping pip self-upgrade in Windows venv; seeded pip is already validated."
    Write-Host "Installing Main Computer package without Mathics optional dependency."
    Write-Host "Mathics is handled as a separate optional dependency so it cannot block the core bootstrap."

    $coreInstallArgs = @(
        "-m", "pip", "install",
        "-vvv",
        "--disable-pip-version-check",
        "--no-input",
        "--progress-bar", "off",
        "--timeout", "30",
        "--retries", "2",
        "-e", "."
    )

    Invoke-NativeCheckedWithPreview -Label "Main Computer package install" -FilePath $PythonPath -Arguments $coreInstallArgs -WorkingDirectory $Root -TimeoutSeconds 300 | Out-Null
    Add-BootstrapStatus "Python dependencies" "OK" "Installed Main Computer package without Mathics optional dependency."

    Install-MathicsOptionalDependency -PythonPath $PythonPath -Root $Root
}

function Test-PythonImport {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$Root
    )

    Invoke-NativeChecked -FilePath $PythonPath -Arguments @(
        "-c",
        "import main_computer.config; print('main-computer-python-ok')"
    ) -WorkingDirectory $Root | Out-Null
    Add-BootstrapStatus "Python import" "OK" $PythonPath
}

function Set-LocalServerPublishingEnvironment {
    param([Parameter(Mandatory = $true)]$ModeProfile)

    if ($LocalServerMode -eq "disabled") {
        $env:MAIN_COMPUTER_LOCAL_SERVER_ENABLED = "0"
        return
    }

    $env:MAIN_COMPUTER_LOCAL_SERVER_ENABLED = "1"
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_MODE = $ModeProfile.Key
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT = $ModeProfile.LocalServerProjectName
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH = $ModeProfile.LocalServerRegistryPath
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH = $ModeProfile.LocalServerComposePath
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_BUILTIN_PORT_START = "$($ModeProfile.DefaultLocalServerPortStart)"
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START = "$($ModeProfile.LocalServerGeneratedPortStart)"
    $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END = "$($ModeProfile.LocalServerGeneratedPortEnd)"
    $env:MAIN_COMPUTER_LOCAL_SERVER_URL = "http://127.0.0.1:$($ModeProfile.DefaultLocalServerPortStart)/"
}


function Configure-AppEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$WslPath,
        [Parameter(Mandatory = $true)][string]$Distribution,
        [Parameter(Mandatory = $true)]$ModeProfile
    )

    $env:MAIN_COMPUTER_PYTHON = $PythonPath
    $env:MAIN_COMPUTER_WORKSPACE = $Workspace
    $env:MAIN_COMPUTER_INSTALL_MODE = $ModeProfile.Key
    $env:MAIN_COMPUTER_MODE_LABEL = $ModeProfile.Label
    $env:MAIN_COMPUTER_GUIDANCE_LEVEL = $ModeProfile.GuidanceLevel
    $env:MAIN_COMPUTER_SAFE_MODE = if ($ModeProfile.Key -eq "safe") { "1" } else { "0" }
    $env:MAIN_COMPUTER_INSTANCE_NAME = $ModeProfile.InstanceName
    $env:MAIN_COMPUTER_STATE_ROOT = $ModeProfile.StateRoot
    $env:MAIN_COMPUTER_CONTROL_ROOT = $ModeProfile.ControlRoot
    $env:MAIN_COMPUTER_CONTROL_PORT = "$Port"
    $env:MAIN_COMPUTER_HEARTBEAT_PORT = "$HeartbeatPort"
    $env:MAIN_COMPUTER_EXECUTOR_ENABLED = "1"
    $env:MAIN_COMPUTER_EXECUTOR_BACKEND = "wsl"
    $env:MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION = $Distribution
    $env:MAIN_COMPUTER_EXECUTOR_WSL_COMMAND = $WslPath
    $env:MAIN_COMPUTER_EXECUTOR_ROOT = $ModeProfile.ExecutorRoot
    $env:MAIN_COMPUTER_PATH_MODE = "local"
    $env:MAIN_COMPUTER_HOST_OS = "windows"
    Set-LocalServerPublishingEnvironment -ModeProfile $ModeProfile

    if ($LocalCoolifyMode -eq "disabled") {
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED = "0"
    }
    else {
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED = "1"
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_URL = "http://127.0.0.1:$($ModeProfile.CoolifyPort)"
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_REF = "MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN"
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE = Join-Path $ModeProfile.CoolifyStateRoot "api-token.txt"
        $env:MAIN_COMPUTER_COOLIFY_PROJECT = $ModeProfile.CoolifyProjectName
        $env:MAIN_COMPUTER_COOLIFY_STATE_DIR = $ModeProfile.CoolifyStateRoot
        $env:MAIN_COMPUTER_COOLIFY_APP_PORT = "$($ModeProfile.CoolifyPort)"
        $env:MAIN_COMPUTER_COOLIFY_SOKETI_PORT = "$($ModeProfile.CoolifySoketiPort)"
        $env:MAIN_COMPUTER_COOLIFY_SOKETI_TERMINAL_PORT = "$($ModeProfile.CoolifySoketiTerminalPort)"
    }

    if ($script:EffectiveOnlyOfficeMode -eq "disabled") {
        $env:MAIN_COMPUTER_ONLYOFFICE_ENABLED = "0"
    }
    else {
        $env:MAIN_COMPUTER_ONLYOFFICE_ENABLED = "1"
        $env:MAIN_COMPUTER_ONLYOFFICE_MODE = $script:EffectiveOnlyOfficeMode
        $env:MAIN_COMPUTER_ONLYOFFICE_PORT = "$OnlyOfficePort"
        $env:MAIN_COMPUTER_ONLYOFFICE_PROJECT = $ModeProfile.OnlyOfficeProjectName
        $env:MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL = "http://127.0.0.1:$OnlyOfficePort"
        $env:MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL = "http://127.0.0.1:$OnlyOfficePort"
        if (-not $env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET) {
            $env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET = "main-computer-onlyoffice-local-secret"
        }
        if ($script:EffectiveOnlyOfficeMode -eq "docker") {
            $env:MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL = "http://host.docker.internal:$Port"
            Add-BootstrapStatus "ONLYOFFICE callback" "OK" $env:MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL
        }
        else {
            Set-OnlyOfficeWslCallbackEnvironment -ModeProfile $ModeProfile
        }
    }
}

function Ensure-WslRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$WslPath,
        [Parameter(Mandatory = $true)][string]$Distribution,
        [string]$WslRuntimeRoot = ""
    )

    $installed = Test-WslDistributionInstalled -CommandPath $WslPath -Distribution $Distribution
    if ($installed) {
        Add-BootstrapStatus "WSL executor distro" "OK" "$Distribution is installed."
    }
    elseif ($SkipWslRuntimeInstall) {
        Add-BootstrapStatus "WSL executor distro" "WARN" "$Distribution is not installed; -SkipWslRuntimeInstall was set."
        return $false
    }
    else {
        $runtimeImage = Get-RuntimeImagePath -Profile $RuntimeProfile -Root $Root -Distribution $Distribution -WslRuntimeRoot $WslRuntimeRoot
        if (-not (Test-Path -LiteralPath $runtimeImage -PathType Leaf)) {
            if ($BuildWslRuntimeIfMissing) {
                Write-Section "Build WSL executor runtime"
                $builder = Join-Path $Root "scripts\windows\build-main-computer-runtime.ps1"
                if (-not (Test-Path -LiteralPath $builder -PathType Leaf)) {
                    Fail "Runtime builder script not found: $builder"
                }
                $buildArgs = @{
                    Profile = $RuntimeProfile
                    OutputPath = $runtimeImage
                    DistributionName = $Distribution
                }
                if (-not [string]::IsNullOrWhiteSpace($WslRuntimeRoot)) {
                    $buildArgs.RuntimeRoot = $WslRuntimeRoot
                }
                & $builder @buildArgs
                if ($LASTEXITCODE -ne 0) {
                    Fail "WSL runtime build failed with exit code $LASTEXITCODE."
                }
            }
            else {
                Add-BootstrapStatus "WSL executor distro" "WARN" "$Distribution is missing and runtime image is missing. Build with scripts\windows\build-main-computer-runtime.ps1 -Profile $RuntimeProfile or rerun with -BuildWslRuntimeIfMissing."
                return $false
            }
        }

        $installer = Join-Path $Root "scripts\windows\install-main-computer-runtime.ps1"
        if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
            Fail "Runtime installer script not found: $installer"
        }

        Write-Section "Install WSL executor runtime"
        $installArgs = @{
            Profile = $RuntimeProfile
            WslCommand = $WslPath
            DistributionName = $Distribution
            RuntimeImagePath = $runtimeImage
        }
        if (-not [string]::IsNullOrWhiteSpace($WslRuntimeRoot)) {
            $installArgs.RuntimeRoot = $WslRuntimeRoot
        }
        if ($ResetWslRuntime) {
            $installArgs.Reset = $true
        }
        & $installer @installArgs
        if ($LASTEXITCODE -ne 0) {
            Fail "WSL runtime install failed with exit code $LASTEXITCODE."
        }
        Add-BootstrapStatus "WSL executor distro" "OK" "$Distribution installed."
    }

    Write-Section "Verify WSL executor runtime"
    $contractScript = @'
set -e
echo main-computer-executor-ok
test -x /usr/local/bin/main-computer-exec
/usr/local/bin/main-computer-exec run --cwd /workspace --timeout-ms 5000 --artifact-dir /outputs -- 'echo main-computer-exec-ready' | grep -q main-computer-exec-ready
echo main-computer-exec-contract-ok
'@

    $verify = Invoke-Native -FilePath $WslPath -Arguments @(
        "--distribution",
        $Distribution,
        "--exec",
        "/bin/sh",
        "-lc",
        $contractScript
    )
    if ($verify.ExitCode -ne 0 -or $verify.Stdout -notmatch "main-computer-exec-contract-ok") {
        $details = (($verify.Stderr, $verify.Stdout) -join "`n").Trim()
        $sourceEntrypoint = Join-Path $Root "docker\executor\main-computer-exec"

        if (-not (Test-Path -LiteralPath $sourceEntrypoint -PathType Leaf)) {
            Add-BootstrapStatus "WSL executor runtime" "FAIL" "Entrypoint contract failed and replacement entrypoint was not found at $sourceEntrypoint. Details: $details"
            return $false
        }

        Add-BootstrapStatus "WSL executor runtime" "REPAIR" "Entrypoint contract failed; refreshing /usr/local/bin/main-computer-exec from $sourceEntrypoint."
        $sourceEntrypointWsl = ConvertTo-WslHostPath -Path $sourceEntrypoint
        $quotedSourceEntrypointWsl = ConvertTo-ShellSingleQuotedLiteral $sourceEntrypointWsl
        $repairScript = @"
set -e
test -r $quotedSourceEntrypointWsl
cp $quotedSourceEntrypointWsl /usr/local/bin/main-computer-exec
sed -i 's/\r$//' /usr/local/bin/main-computer-exec
chmod 0755 /usr/local/bin/main-computer-exec
"@
        $repair = Invoke-Native -FilePath $WslPath -Arguments @(
            "--distribution",
            $Distribution,
            "--exec",
            "/bin/sh",
            "-lc",
            $repairScript
        )
        if ($repair.ExitCode -ne 0) {
            $repairDetails = (($repair.Stderr, $repair.Stdout) -join "`n").Trim()
            Add-BootstrapStatus "WSL executor runtime" "FAIL" "Could not refresh WSL executor entrypoint. Original verification: $details. Repair output: $repairDetails"
            return $false
        }

        $verify = Invoke-Native -FilePath $WslPath -Arguments @(
            "--distribution",
            $Distribution,
            "--exec",
            "/bin/sh",
            "-lc",
            $contractScript
        )
        if ($verify.ExitCode -ne 0 -or $verify.Stdout -notmatch "main-computer-exec-contract-ok") {
            $afterDetails = (($verify.Stderr, $verify.Stdout) -join "`n").Trim()
            Add-BootstrapStatus "WSL executor runtime" "FAIL" "Entrypoint contract still failed after refresh. Before: $details. After: $afterDetails"
            return $false
        }
    }

    Add-BootstrapStatus "WSL executor runtime" "OK" "$Distribution entrypoint contract verified."
    return $true
}

function Start-OnlyOfficeIfRequested {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]$ModeProfile
    )

    if ($script:EffectiveOnlyOfficeMode -eq "disabled") {
        if ($OnlyOfficeMode -eq "auto") {
            Add-BootstrapStatus "ONLYOFFICE" "SKIP" "Docker was not found; ONLYOFFICE auto mode skipped."
        }
        else {
            Add-BootstrapStatus "ONLYOFFICE" "SKIP" "Disabled by -OnlyOfficeMode disabled."
        }
        return
    }

    $control = Join-Path $Root "tools\onlyoffice\onlyoffice-control.ps1"
    if (-not (Test-Path -LiteralPath $control -PathType Leaf)) {
        Fail "ONLYOFFICE control script not found: $control"
    }

    Write-Section "ONLYOFFICE service"
    Add-BootstrapStatus "ONLYOFFICE lane" "OK" "$($ModeProfile.Label) uses fixed ONLYOFFICE port $OnlyOfficePort and compose project $($ModeProfile.OnlyOfficeProjectName)."

    $commonParams = @{
        Mode = $script:EffectiveOnlyOfficeMode
        Port = [int]$OnlyOfficePort
        ProjectName = $ModeProfile.OnlyOfficeProjectName
    }

    if ($InstallOnlyOffice) {
        & $control install @commonParams
        if ($LASTEXITCODE -ne 0) {
            Fail "ONLYOFFICE install failed with exit code $LASTEXITCODE."
        }
    }

    & $control start @commonParams
    if ($LASTEXITCODE -ne 0) {
        Fail "ONLYOFFICE start failed with exit code $LASTEXITCODE."
    }

    & $control status @commonParams
    if ($LASTEXITCODE -ne 0) {
        Fail "ONLYOFFICE status failed with exit code $LASTEXITCODE."
    }

    Add-BootstrapStatus "ONLYOFFICE" "OK" "$script:EffectiveOnlyOfficeMode service at http://127.0.0.1:$OnlyOfficePort [$($ModeProfile.OnlyOfficeProjectName)]"
}

function Initialize-LocalServerPublishingIfRequested {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)]$ModeProfile
    )

    if ($LocalServerMode -eq "disabled") {
        Add-BootstrapStatus "Local Server publishing" "SKIP" "Disabled by -LocalServerMode disabled."
        return
    }

    $scriptPath = Join-Path $Root "tools\local-platform\website-docker.py"
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        Fail "Local Server publishing tool not found: $scriptPath"
    }

    Write-Section "Local Server publishing target"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ModeProfile.LocalServerRegistryPath) | Out-Null
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ModeProfile.LocalServerComposePath) | Out-Null

    Invoke-NativeChecked -FilePath $PythonPath -Arguments @(
        $scriptPath,
        "install",
        "hub-site",
        "--repo-root",
        $Root
    ) -WorkingDirectory $Root -Quiet | Out-Null

    Invoke-NativeChecked -FilePath $PythonPath -Arguments @(
        $scriptPath,
        "publish",
        "hub-site",
        "--lane",
        "local",
        "--repo-root",
        $Root,
        "--dry-run"
    ) -WorkingDirectory $Root -Quiet | Out-Null

    Add-BootstrapStatus "Local Server publishing" "OK" "$($ModeProfile.Label) uses project $($ModeProfile.LocalServerProjectName), registry $($ModeProfile.LocalServerRegistryPath), compose $($ModeProfile.LocalServerComposePath), built-in ports $($ModeProfile.DefaultLocalServerPortStart)-$($ModeProfile.DefaultLocalServerPortStart + 3)."
}


function Start-LocalCoolifyIfRequested {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)]$ModeProfile
    )

    if ($LocalCoolifyMode -eq "disabled") {
        Add-BootstrapStatus "Local Coolify" "SKIP" "Disabled by -LocalCoolifyMode disabled."
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED = "0"
        return
    }

    $docker = Resolve-CommandPath "docker"
    if ($null -eq $docker) {
        $message = "Docker was not found; install-scoped Local Coolify cannot be started."
        if ($LocalCoolifyMode -eq "required") {
            Add-BootstrapStatus "Local Coolify" "FAIL" $message
            Fail $message
        }
        Add-BootstrapStatus "Local Coolify" "WARN" "$message Website Builder deploys that require Local Coolify will not work until Docker is installed."
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED = "0"
        return
    }

    $scriptPath = Join-Path $Root "tools\local-prod\setup-local-coolify.py"
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        Fail "Local Coolify setup tool not found: $scriptPath"
    }

    Write-Section "Install-scoped Local Coolify"
    New-Item -ItemType Directory -Force -Path $ModeProfile.CoolifyStateRoot | Out-Null

    $setupArgs = @(
        $scriptPath,
        "setup",
        "--project-name", $ModeProfile.CoolifyProjectName,
        "--state-dir", $ModeProfile.CoolifyStateRoot,
        "--app-port", "$($ModeProfile.CoolifyPort)",
        "--soketi-port", "$($ModeProfile.CoolifySoketiPort)",
        "--soketi-terminal-port", "$($ModeProfile.CoolifySoketiTerminalPort)"
    )
    Invoke-NativeChecked -FilePath $PythonPath -Arguments $setupArgs -WorkingDirectory $Root | Out-Null

    $tokenFile = Join-Path $ModeProfile.CoolifyStateRoot "api-token.txt"
    if (Test-Path -LiteralPath $tokenFile -PathType Leaf) {
        $tokenValue = (Get-Content -LiteralPath $tokenFile -Raw).Trim()
        if (-not [string]::IsNullOrWhiteSpace($tokenValue)) {
            $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN = $tokenValue
        }
    }

    Add-BootstrapStatus "Local Coolify" "OK" "Install-scoped project '$($ModeProfile.CoolifyProjectName)' at http://127.0.0.1:$($ModeProfile.CoolifyPort); state $($ModeProfile.CoolifyStateRoot)"
}


function Start-WindowsApp {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)]$ModeProfile
    )

    if ($SkipAppStart) {
        Add-BootstrapStatus "Main Computer app" "SKIP" "-SkipAppStart was set."
        return $false
    }

    if ($ModeProfile.Key -eq "debug") {
        $proto = Join-Path $Root "proto-dev\proto-dev.ps1"
        if (-not (Test-Path -LiteralPath $proto -PathType Leaf)) {
            Fail "proto-dev runner not found: $proto"
        }

        $protoParams = @{
            Action = "run"
            RepoRoot = $Root
            StateRoot = $ModeProfile.StateRoot
            Workspace = $Workspace
            BindHost = "127.0.0.1"
            Port = [int]$Port
            HeartbeatPort = [int]$HeartbeatPort
            WslCommand = $wslPath
            ExecutorDistribution = $ExecutorDistribution
            WslRuntimeRoot = $ModeProfile.WslRuntimeRoot
            StartTimeoutSeconds = [int]$StartTimeoutSeconds
        }
        if ($BuildWslRuntimeIfMissing) {
            $protoParams.BuildWslRuntimeIfMissing = $true
        }
        if ($SkipDependencyInstall) {
            $protoParams.SkipDependencyInstall = $true
        }
        if ($SkipMathicsCheck) {
            $protoParams.SkipMathicsCheck = $true
        }
        if ($SkipWslRuntimeInstall) {
            $protoParams.SkipWslRuntimeInstall = $true
        }
        if ($SkipExecutorSmoke) {
            $protoParams.SkipExecutorSmoke = $true
        }
        if ($AllowForeignPortListener) {
            $protoParams.AllowForeignPortListener = $true
        }

        Write-Section "Start Main Computer Debug"
        & $proto @protoParams
        if ($LASTEXITCODE -ne 0) {
            Fail "Main Computer Debug start failed with exit code $LASTEXITCODE."
        }

        Add-BootstrapStatus "Main Computer app" "OK" "$($ModeProfile.Label) at http://127.0.0.1:$Port"
        return $true
    }

    if ($ModeProfile.Key -eq "safe") {
        $control = Join-Path $Root "control-main-computer.ps1"
        if (-not (Test-Path -LiteralPath $control -PathType Leaf)) {
            Fail "control-main-computer.ps1 not found: $control"
        }

        $controlRoot = $ModeProfile.ControlRoot
        New-Item -ItemType Directory -Force -Path $controlRoot | Out-Null

        $controlParams = @{
            Action = "start"
            AutoAllow = $true
            BindHost = $BindHost
            Port = [int]$Port
            HeartbeatPort = [int]$HeartbeatPort
            Workspace = $Workspace
            PythonPath = $PythonPath
            ControlRoot = $controlRoot
            StartTimeoutSeconds = [int]$StartTimeoutSeconds
            MathicsApiTimeoutSeconds = 180
        }
        if ($SkipMathicsCheck) {
            $controlParams.SkipMathicsCheck = $true
        }
        if ($AllowForeignPortListener) {
            $controlParams.AllowForeignPortListener = $true
        }

        Write-Section "Start Main Computer Safe Mode"
        & $control @controlParams
        if ($LASTEXITCODE -ne 0) {
            Fail "Main Computer Safe Mode start failed with exit code $LASTEXITCODE."
        }

        Add-BootstrapStatus "Main Computer app" "OK" "$($ModeProfile.Label) at http://127.0.0.1:$Port"
        return $true
    }

    $devControl = Join-Path $Root "dev-control.ps1"
    if (-not (Test-Path -LiteralPath $devControl -PathType Leaf)) {
        Fail "dev-control.ps1 not found: $devControl"
    }

    $devControlParams = @{
        Action = "start"
        Mode = "local"
        PythonPath = $PythonPath
        BindHost = $BindHost
        LocalPort = [int]$Port
        HeartbeatPort = [int]$HeartbeatPort
        Workspace = $Workspace
        ControlRoot = $ModeProfile.ControlRoot
        StartTimeoutSeconds = [int]$StartTimeoutSeconds
    }
    if ($EnsureRenderer) {
        $devControlParams.EnsureRenderer = $true
    }
    if ($SkipMathicsCheck) {
        $devControlParams.SkipMathicsCheck = $true
    }
    if ($AllowForeignPortListener) {
        $devControlParams.AllowForeignPortListener = $true
    }

    Write-Section "Start Main Computer Unleashed Mode"
    & $devControl @devControlParams
    if ($LASTEXITCODE -ne 0) {
        Fail "Main Computer local app start failed with exit code $LASTEXITCODE."
    }

    $appUrl = "http://127.0.0.1:$Port"
    Add-BootstrapStatus "Main Computer app" "OK" "$($ModeProfile.Label) at $appUrl"
    return $true
}


function Invoke-JsonRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [object]$Body = $null,
        [int]$TimeoutSec = 15
    )

    $parameters = @{
        Method = $Method
        Uri = $Uri
        TimeoutSec = $TimeoutSec
        Headers = @{ Accept = "application/json" }
    }

    if ($null -ne $Body) {
        $parameters.ContentType = "application/json"
        $parameters.Body = ($Body | ConvertTo-Json -Depth 8)
    }

    return Invoke-RestMethod @parameters
}

function Test-ExecutorThroughApp {
    if ($SkipExecutorSmoke) {
        Add-BootstrapStatus "WSL executor smoke" "SKIP" "-SkipExecutorSmoke was set."
        return
    }

    if ($SkipAppStart) {
        Add-BootstrapStatus "WSL executor smoke" "SKIP" "App was not started."
        return
    }

    $baseUrl = "http://127.0.0.1:$Port"
    Write-Section "WSL executor app smoke"

    $status = Invoke-JsonRequest -Method "GET" -Uri "$baseUrl/api/executor/status" -TimeoutSec 20
    if ($status.ok -ne $true -or $status.executor.backend -ne "wsl" -or $status.executor.ok -ne $true) {
        $details = $status | ConvertTo-Json -Depth 10
        throw "Executor status is not healthy for WSL backend: $details"
    }

    $run = Invoke-JsonRequest -Method "POST" -Uri "$baseUrl/api/executor/run" -TimeoutSec 40 -Body @{
        command = "printf main-computer-bootstrap-wsl-ok"
        cwd = "/workspace"
        timeout_s = 15
        network = $false
        description = "bootstrap WSL executor smoke test"
    }

    if ($run.ok -ne $true -or $run.backend -ne "wsl" -or ([string]$run.stdout) -notlike "*main-computer-bootstrap-wsl-ok*") {
        $details = $run | ConvertTo-Json -Depth 10
        throw "Executor run smoke failed: $details"
    }

    Add-BootstrapStatus "WSL executor smoke" "OK" "Ran command through Windows app -> WSL executor."
}

function Write-FinalSummary {
    param(
        [Parameter(Mandatory = $true)][bool]$ExecutorReady,
        [Parameter(Mandatory = $true)]$ModeProfile,
        [string]$InstallRootPath = ""
    )

    Write-Section "Bootstrap summary"
    foreach ($item in $script:BootstrapStatus) {
        if ([string]::IsNullOrWhiteSpace($item.Details)) {
            Write-Host ("{0}: {1}" -f $item.Name, $item.State)
        }
        else {
            Write-Host ("{0}: {1} - {2}" -f $item.Name, $item.State, $item.Details)
        }
    }

    Write-Host ""
    Write-Host "Main Computer app URL: http://127.0.0.1:$Port"
    Write-Host "Active mode: $($ModeProfile.Label) ($($ModeProfile.Key))"
    if (-not [string]::IsNullOrWhiteSpace($InstallRootPath)) {
        Write-Host "Install root: $InstallRootPath"
    }
    if (-not [string]::IsNullOrWhiteSpace($ModeProfile.InstanceStoreRoot)) {
        Write-Host "Instance store root: $($ModeProfile.InstanceStoreRoot)"
    }
    if ($LocalCoolifyMode -ne "disabled") {
        Write-Host "Local Coolify URL: http://127.0.0.1:$($ModeProfile.CoolifyPort)"
        Write-Host "Local Coolify project: $($ModeProfile.CoolifyProjectName)"
    }
    if (-not [string]::IsNullOrWhiteSpace($script:GeneratedRunnerPath)) {
        Write-Host "Mode runner: $script:GeneratedRunnerPath"
        Write-Host "Switch modes intentionally with: .\$RunnerName start -Mode Unleashed | Debug | Safe"
    }
    Write-Host "Runtime shape: Windows native app + WSL executor + install-scoped Docker services for Website Builder Local Coolify/optional integrations."
    Write-Host "Executor distribution: $ExecutorDistribution"
    if (-not $ExecutorReady) {
        Write-Host ""
        Write-Host "WSL executor is not fully ready. The app can still start, but executor smoke will be skipped or fail until the runtime image/distro is installed."
    }
}

$script:GeneratedRunnerPath = ""
$selectedMode = Resolve-MainComputerUserMode -ModeName $Mode
$script:SelectedMainComputerMode = $selectedMode

Write-Section "Windows bootstrap preflight"

$SourceRepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $SourceRepoRoot "pyproject.toml") -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $SourceRepoRoot "main_computer") -PathType Container) -or
    -not (Test-Path -LiteralPath (Join-Path $SourceRepoRoot "dev-control.ps1") -PathType Leaf)) {
    Fail "Repo root does not look like Main Computer: $SourceRepoRoot"
}

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $plannedInstallRoot = $SourceRepoRoot
}
else {
    $plannedInstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
}

$resolvedInstanceName = Resolve-MainComputerInstanceName -Root $plannedInstallRoot
$resolvedInstanceStoreRoot = Resolve-MainComputerInstanceStoreRoot -Root $plannedInstallRoot -InstallInstanceName $resolvedInstanceName -RequestedRoot $InstanceStoreRoot
$modeProfiles = @(Get-MainComputerModeIsolationProfiles -Root $plannedInstallRoot -InstallInstanceName $resolvedInstanceName -InstanceStoreRoot $resolvedInstanceStoreRoot)
$selectedIsolation = Select-MainComputerModeIsolationProfile -ModeProfile $selectedMode -ModeProfiles $modeProfiles
$selectedMode = Apply-MainComputerModeIsolation -ModeProfile $selectedMode -IsolationProfile $selectedIsolation
$script:SelectedMainComputerMode = $selectedMode

if (-not $PSBoundParameters.ContainsKey("RuntimeProfile")) {
    $RuntimeProfile = $selectedMode.RuntimeProfile
}

if (-not $PSBoundParameters.ContainsKey("Port")) {
    $Port = [int]$selectedMode.DefaultPort
}
if ($HeartbeatPort -le 0) {
    $HeartbeatPort = [int]$selectedMode.DefaultHeartbeatPort
}

if (-not $PSBoundParameters.ContainsKey("OnlyOfficePort")) {
    $OnlyOfficePort = [int]$selectedMode.DefaultOnlyOfficePort
}
$selectedMode.DefaultOnlyOfficePort = [int]$OnlyOfficePort
foreach ($profile in $modeProfiles) {
    if ($profile.Key -eq $selectedMode.Key) {
        $profile.DefaultOnlyOfficePort = [int]$OnlyOfficePort
    }
}
$script:EffectiveOnlyOfficeMode = Resolve-OnlyOfficeRuntimeMode -RequestedMode $OnlyOfficeMode

$selectedMode.DefaultPort = [int]$Port
$selectedMode.DefaultHeartbeatPort = [int]$HeartbeatPort
foreach ($profile in $modeProfiles) {
    if ($profile.Key -eq $selectedMode.Key) {
        $profile.DefaultPort = [int]$Port
        $profile.DefaultHeartbeatPort = [int]$HeartbeatPort
    }
}

if ([string]::IsNullOrWhiteSpace($ExecutorDistribution)) {
    if (-not [string]::IsNullOrWhiteSpace($selectedMode.DefaultExecutorDistribution)) {
        $ExecutorDistribution = [string]$selectedMode.DefaultExecutorDistribution
    }
    else {
        $runtimeConfig = Read-RuntimeConfig -Profile $RuntimeProfile
        if ($null -ne $runtimeConfig -and -not [string]::IsNullOrWhiteSpace($runtimeConfig.executor_wsl_distribution)) {
            $ExecutorDistribution = [string]$runtimeConfig.executor_wsl_distribution
        }
        else {
            $ExecutorDistribution = Get-DefaultExecutorDistribution -Profile $RuntimeProfile
        }
    }
}
$selectedMode.DefaultExecutorDistribution = $ExecutorDistribution
foreach ($profile in $modeProfiles) {
    if ($profile.Key -eq $selectedMode.Key) {
        $profile.DefaultExecutorDistribution = $ExecutorDistribution
    }
}

if ([string]::IsNullOrWhiteSpace($VenvPath)) {
    $VenvPath = $selectedMode.VenvRoot
}
else {
    $resolvedVenv = [System.IO.Path]::GetFullPath($VenvPath)
    $selectedMode.VenvRoot = $resolvedVenv
    $selectedMode.VenvPython = Join-Path $resolvedVenv "Scripts\python.exe"
    foreach ($profile in $modeProfiles) {
        if ($profile.Key -eq $selectedMode.Key) {
            $profile.VenvRoot = $selectedMode.VenvRoot
            $profile.VenvPython = $selectedMode.VenvPython
        }
    }
}

if ([string]::IsNullOrWhiteSpace($Workspace)) {
    $Workspace = $plannedInstallRoot
}
else {
    $Workspace = [System.IO.Path]::GetFullPath($Workspace)
}

Add-BootstrapStatus "Selected mode" "OK" "$($selectedMode.Label) ($($selectedMode.Key))"
Add-BootstrapStatus "Instance namespace" "OK" $resolvedInstanceName
Add-BootstrapStatus "Instance store root" "OK" $resolvedInstanceStoreRoot

Invoke-MainComputerBootstrapPrecheck -SourceRoot $SourceRepoRoot -PlannedInstallRoot $plannedInstallRoot -SelectedMode $selectedMode -ModeProfiles $modeProfiles

if ($PrecheckOnly) {
    Write-PrecheckSummary -SelectedMode $selectedMode
    if ($script:PrecheckFailed) {
        exit 1
    }
    exit 0
}

if ($script:PrecheckFailed) {
    Write-PrecheckSummary -SelectedMode $selectedMode
    Fail "Bootstrap precheck failed. Fix the failed items above or run with -PrecheckOnly to diagnose without changing the install."
}

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $RepoRoot = $SourceRepoRoot
    Add-BootstrapStatus "Install root" "OK" "No -InstallRoot supplied; using source repo root."
}
else {
    $RepoRoot = Copy-RepositoryToInstallRoot -SourceRoot $SourceRepoRoot -DestinationRoot $InstallRoot -ModeProfile $selectedMode -WslCommand $WslCommand
}

$RepoRoot = [System.IO.Path]::GetFullPath($RepoRoot)
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "pyproject.toml") -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $RepoRoot "main_computer") -PathType Container) -or
    -not (Test-Path -LiteralPath (Join-Path $RepoRoot "dev-control.ps1") -PathType Leaf)) {
    Fail "Install root does not look like Main Computer after preparation: $RepoRoot"
}
Add-BootstrapStatus "Repository root" "OK" $RepoRoot
Add-BootstrapStatus "Workspace" "OK" $Workspace

$wslPath = Resolve-CommandPath $WslCommand
if ($null -eq $wslPath) {
    Add-BootstrapStatus "wsl.exe" "FAIL" "$WslCommand was not found."
    Fail "wsl.exe was not found. Enable/install WSL before using the Windows-first bootstrap."
}
Add-BootstrapStatus "wsl.exe" "OK" $wslPath

$basePython = Resolve-BasePython -AllowProvision
Add-BootstrapStatus "Base Python" "OK" $basePython
Write-PythonRuntimeIdentity -PythonPath $basePython -Label "Selected base Python path" -Root $RepoRoot | Out-Null

Write-Section "Windows Python environment"
$venvPython = Ensure-VenvPython -Root $RepoRoot -BasePython $basePython
Add-BootstrapStatus "Windows venv Python" "OK" $venvPython

$actualVenvRoot = Split-Path -Parent (Split-Path -Parent $venvPython)
$selectedMode.VenvRoot = $actualVenvRoot
$selectedMode.VenvPython = $venvPython
foreach ($profile in $modeProfiles) {
    if ($profile.Key -eq $selectedMode.Key) {
        $profile.VenvRoot = $actualVenvRoot
        $profile.VenvPython = $venvPython
    }
}

Install-PythonDependencies -PythonPath $venvPython -Root $RepoRoot
Test-PythonImport -PythonPath $venvPython -Root $RepoRoot

Write-Section "WSL executor setup"
$executorReady = Ensure-WslRuntime -Root $RepoRoot -WslPath $wslPath -Distribution $ExecutorDistribution -WslRuntimeRoot $selectedMode.WslRuntimeRoot

Ensure-WslScopedFirewallRule -ModeProfile $selectedMode -WslPath $wslPath -PythonPath $venvPython | Out-Null

Configure-AppEnvironment -PythonPath $venvPython -Root $RepoRoot -WslPath $wslPath -Distribution $ExecutorDistribution -ModeProfile $selectedMode

Initialize-LocalServerPublishingIfRequested -Root $RepoRoot -PythonPath $venvPython -ModeProfile $selectedMode

Start-LocalCoolifyIfRequested -Root $RepoRoot -PythonPath $venvPython -ModeProfile $selectedMode

$runnerPath = Write-ModeRunner -Root $RepoRoot -ModeProfile $selectedMode -PythonPath $venvPython -WslPath $wslPath -Distribution $ExecutorDistribution -InstallInstanceName $resolvedInstanceName -ModeProfiles $modeProfiles

Start-OnlyOfficeIfRequested -Root $RepoRoot -ModeProfile $selectedMode

$appStarted = Start-WindowsApp -Root $RepoRoot -PythonPath $venvPython -ModeProfile $selectedMode
if ($appStarted -and $executorReady) {
    Test-ExecutorThroughApp
}
elseif (-not $executorReady) {
    Add-BootstrapStatus "WSL executor smoke" "SKIP" "Executor runtime is not fully ready."
}

Write-FinalSummary -ExecutorReady:$executorReady -ModeProfile $selectedMode -InstallRootPath $RepoRoot

