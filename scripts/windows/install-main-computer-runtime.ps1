[CmdletBinding()]
param(
    [ValidateSet("test", "prod")]
    [string]$Profile = "test",

    [string]$RuntimeImagePath = "",

    [string]$DistributionName = "",

    [string]$RuntimeRoot = "",

    [string]$WslCommand = "wsl.exe",

    [switch]$Reset
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host $Title
    Write-Host ("-" * $Title.Length)
}

function Get-RepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
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
        [Parameter(Mandatory = $true)][string]$RuntimeRoot,
        [Parameter(Mandatory = $true)][string]$DistributionName
    )

    return Join-Path (Join-Path $RuntimeRoot "images") (ConvertTo-RuntimeImageFileName -DistributionName $DistributionName)
}

function Get-RuntimeProfile {
    param([string]$Name)

    if ($Name -eq "prod") {
        $runtimeRoot = Join-Path $env:LOCALAPPDATA "MainComputer\wsl"
        $distributionName = "MainComputerExecutor"
        return [pscustomobject]@{
            Name = "prod"
            DistributionName = $distributionName
            RuntimeRoot = $runtimeRoot
            ConfigPath = Join-Path $env:LOCALAPPDATA "MainComputer\main-computer-runtime.json"
            RootfsTar = Get-RuntimeImagePath -RuntimeRoot $runtimeRoot -DistributionName $distributionName
        }
    }

    $runtimeRoot = Join-Path $env:LOCALAPPDATA "MainComputer\wsl-test"
    $distributionName = "MainComputerExecutorTest"
    return [pscustomobject]@{
        Name = "test"
        DistributionName = $distributionName
        RuntimeRoot = $runtimeRoot
        ConfigPath = Join-Path $env:LOCALAPPDATA "MainComputer\main-computer-runtime.test.json"
        RootfsTar = Get-RuntimeImagePath -RuntimeRoot $runtimeRoot -DistributionName $distributionName
    }
}

function Resolve-CommandPath {
    param([string]$CommandName)

    if ([string]::IsNullOrWhiteSpace($CommandName)) {
        return $null
    }
    if (Test-Path -LiteralPath $CommandName) {
        return (Resolve-Path -LiteralPath $CommandName).Path
    }

    $command = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
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

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$Quiet,
        [int]$SuccessExitCode = 0
    )

    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()
    try {
        $process = Start-Process `
            -FilePath $FilePath `
            -ArgumentList (Join-NativeArgumentList -Arguments $Arguments) `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath

        $stdout = ""
        $stderr = ""
        if (Test-Path -LiteralPath $stdoutPath) {
            $stdout = Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $stderrPath) {
            $stderr = Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
        }

        if (-not $Quiet) {
            if (-not [string]::IsNullOrEmpty($stdout)) {
                Write-Host ($stdout.TrimEnd())
            }
            if (-not [string]::IsNullOrEmpty($stderr)) {
                Write-Host ($stderr.TrimEnd())
            }
        }

        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = $stdout
            Stderr = $stderr
        }
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$Quiet
    )

    $result = Invoke-Native -FilePath $FilePath -Arguments $Arguments -Quiet:$Quiet
    if ($result.ExitCode -ne 0) {
        $commandLine = "$FilePath $(Join-NativeArgumentList -Arguments $Arguments)"
        $details = (($result.Stderr, $result.Stdout) -join "`n").Trim()
        if ([string]::IsNullOrWhiteSpace($details)) {
            $details = "No output was captured."
        }
        throw "Command failed with exit code $($result.ExitCode): $commandLine`n$details"
    }
    return $result
}

function Get-WslDistributions {
    param([string]$CommandPath)

    $result = Invoke-Native -FilePath $CommandPath -Arguments @("--list", "--quiet") -Quiet
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

function Test-WslDistribution {
    param(
        [string]$CommandPath,
        [string]$Name
    )

    return @(Get-WslDistributions -CommandPath $CommandPath) -contains $Name
}

$profileConfig = Get-RuntimeProfile -Name $Profile

if ([string]::IsNullOrWhiteSpace($DistributionName)) {
    $DistributionName = $profileConfig.DistributionName
}
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = $profileConfig.RuntimeRoot
}
if ([string]::IsNullOrWhiteSpace($RuntimeImagePath)) {
    $RuntimeImagePath = Get-RuntimeImagePath -RuntimeRoot $RuntimeRoot -DistributionName $DistributionName
}

$wslPath = Resolve-CommandPath $WslCommand
if ($null -eq $wslPath) {
    throw "wsl.exe was not found. Install/enable WSL2 before installing the Main Computer runtime."
}

$RuntimeImagePath = [System.IO.Path]::GetFullPath($RuntimeImagePath)
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$installPath = Join-Path $RuntimeRoot $DistributionName

if (-not (Test-Path -LiteralPath $RuntimeImagePath)) {
    throw "Runtime image not found: $RuntimeImagePath`nBuild it first with: powershell -ExecutionPolicy Bypass -File .\scripts\windows\build-main-computer-runtime.ps1 -Profile $Profile"
}

$imageExtension = [System.IO.Path]::GetExtension($RuntimeImagePath).ToLowerInvariant()
if ($imageExtension -notin @(".tar", ".vhdx")) {
    throw "Unsupported runtime image extension '$imageExtension'. Expected .tar rootfs or .vhdx."
}

$exists = Test-WslDistribution -CommandPath $wslPath -Name $DistributionName

if ($Reset -and $exists) {
    Write-Section "Resetting $DistributionName"
    Invoke-Native -FilePath $wslPath -Arguments @("--terminate", $DistributionName) -Quiet | Out-Null
    Invoke-NativeChecked -FilePath $wslPath -Arguments @("--unregister", $DistributionName) | Out-Null
    $exists = $false
}

if (-not $exists) {
    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
    if ((Test-Path -LiteralPath $installPath) -and -not $Reset) {
        $children = @(Get-ChildItem -LiteralPath $installPath -Force -ErrorAction SilentlyContinue)
        if ($children.Count -gt 0) {
            throw "Install path already exists and is not empty: $installPath`nUse -Reset if this is an old or failed $DistributionName install."
        }
    }

    Write-Section "Importing $DistributionName"
    Write-Host "Profile:       $Profile"
    Write-Host "Runtime image: $RuntimeImagePath"
    Write-Host "Install path:  $installPath"

    $importArgs = @("--import", $DistributionName, $installPath, $RuntimeImagePath, "--version", "2")
    if ($imageExtension -eq ".vhdx") {
        $importArgs = @("--import", $DistributionName, $installPath, $RuntimeImagePath, "--vhd", "--version", "2")
    }
    Invoke-NativeChecked -FilePath $wslPath -Arguments $importArgs | Out-Null
}
else {
    Write-Section "$DistributionName already exists"
    Write-Host "Use -Reset to unregister and recreate this $Profile runtime."
}

Write-Section "Verifying $DistributionName"
Invoke-NativeChecked -FilePath $wslPath -Arguments @(
    "--distribution",
    $DistributionName,
    "--exec",
    "/bin/sh",
    "-lc",
    "echo main-computer-executor-ok && uname -a && test -x /usr/local/bin/main-computer-exec && /usr/local/bin/main-computer-exec"
) | Out-Null

$configDir = Split-Path -Parent $profileConfig.ConfigPath
New-Item -ItemType Directory -Force -Path $configDir | Out-Null

$config = [ordered]@{
    format = 1
    profile = $Profile
    executor_backend = "wsl"
    executor_wsl_distribution = $DistributionName
    executor_wsl_command = $wslPath
    runtime_root = $RuntimeRoot
    install_path = $installPath
    runtime_image = $RuntimeImagePath
    installed_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    environment = [ordered]@{
        MAIN_COMPUTER_EXECUTOR_BACKEND = "wsl"
        MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION = $DistributionName
        MAIN_COMPUTER_EXECUTOR_WSL_COMMAND = $wslPath
    }
}

$config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $profileConfig.ConfigPath -Encoding UTF8

Write-Host "Wrote runtime config: $($profileConfig.ConfigPath)"
Write-Host "Installed and verified WSL runtime: $DistributionName"
Write-Host ""
Write-Host "To use this executor backend in the app/test process:"
Write-Host "  `$env:MAIN_COMPUTER_EXECUTOR_BACKEND='wsl'"
Write-Host "  `$env:MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION='$DistributionName'"
