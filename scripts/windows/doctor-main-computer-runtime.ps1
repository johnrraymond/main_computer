[CmdletBinding()]
param(
    [ValidateSet("test", "prod", "all")]
    [string]$Profile = "test",

    [string]$WslCommand = "wsl.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-State {
    param(
        [string]$Name,
        [string]$State,
        [string]$Details = ""
    )

    if ([string]::IsNullOrWhiteSpace($Details)) {
        Write-Host ("{0}: {1}" -f $Name, $State)
    }
    else {
        Write-Host ("{0}: {1} - {2}" -f $Name, $State, $Details)
    }
}

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
        [switch]$Quiet
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

function Get-WslDefaultDistribution {
    param([string]$StatusText)

    foreach ($line in ($StatusText -split "(`r`n|`n|`r)")) {
        $clean = ($line -replace "`0", "").Trim()
        if ($clean -match "^Default Distribution:\s*(.+)$") {
            return $Matches[1].Trim()
        }
    }

    return ""
}

function Test-HashFile {
    param(
        [string]$ImagePath,
        [string]$HashPath
    )

    if (-not (Test-Path -LiteralPath $HashPath)) {
        return "missing"
    }

    $expectedLine = (Get-Content -LiteralPath $HashPath -Raw).Trim()
    if ([string]::IsNullOrWhiteSpace($expectedLine)) {
        return "empty"
    }

    $expected = ($expectedLine -split "\s+")[0].ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $ImagePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($expected -eq $actual) {
        return "ok"
    }

    return "mismatch"
}

function Show-ProfileDoctor {
    param(
        [string]$Name,
        [string]$WslPath,
        [string[]]$Distributions,
        [string]$DefaultDistribution
    )

    $profileConfig = Get-RuntimeProfile -Name $Name

    Write-Section "Profile: $Name"
    Write-State "DistributionName" "INFO" $profileConfig.DistributionName
    Write-State "RuntimeRoot" "INFO" $profileConfig.RuntimeRoot
    Write-State "ConfigPath" "INFO" $profileConfig.ConfigPath

    $repoRoot = Get-RepoRoot
    $manifestPath = Join-Path $repoRoot "runtime\main-computer-runtime.json"
    $builderPath = Join-Path $repoRoot "scripts\windows\build-main-computer-runtime.ps1"
    $installerPath = Join-Path $repoRoot "scripts\windows\install-main-computer-runtime.ps1"

    if (Test-Path -LiteralPath $manifestPath) {
        Write-State "Manifest" "OK" $manifestPath
    }
    else {
        Write-State "Manifest" "WARN" "Missing $manifestPath"
    }

    if (Test-Path -LiteralPath $builderPath) {
        Write-State "RuntimeBuilder" "OK" $builderPath
    }
    else {
        Write-State "RuntimeBuilder" "WARN" "Missing $builderPath"
    }

    if (Test-Path -LiteralPath $installerPath) {
        Write-State "RuntimeInstaller" "OK" $installerPath
    }
    else {
        Write-State "RuntimeInstaller" "WARN" "Missing $installerPath"
    }

    if (Test-Path -LiteralPath $profileConfig.RootfsTar) {
        Write-State "RuntimeImage" "OK" $profileConfig.RootfsTar
        $hashState = Test-HashFile -ImagePath $profileConfig.RootfsTar -HashPath "$($profileConfig.RootfsTar).sha256"
        if ($hashState -eq "ok") {
            Write-State "RuntimeImageSHA256" "OK" "$($profileConfig.RootfsTar).sha256"
        }
        elseif ($hashState -eq "missing") {
            Write-State "RuntimeImageSHA256" "WARN" "No .sha256 file found."
        }
        else {
            Write-State "RuntimeImageSHA256" "WARN" $hashState
        }
    }
    else {
        Write-State "RuntimeImage" "WARN" "Missing. Build with: powershell -ExecutionPolicy Bypass -File .\scripts\windows\build-main-computer-runtime.ps1 -Profile $Name"
    }

    if ($Distributions -contains $profileConfig.DistributionName) {
        Write-State $profileConfig.DistributionName "OK" "Distribution is installed."
        if (-not [string]::IsNullOrWhiteSpace($DefaultDistribution) -and $DefaultDistribution -ne $profileConfig.DistributionName) {
            Write-State "Default separation" "OK" "$($profileConfig.DistributionName) is separate from default distro '$DefaultDistribution'."
        }
        elseif ($DefaultDistribution -eq $profileConfig.DistributionName) {
            Write-State "Default separation" "WARN" "$($profileConfig.DistributionName) is currently the default WSL distro."
        }

        Write-Section "Executor verification: $Name"
        $verify = Invoke-Native -FilePath $WslPath -Arguments @(
            "--distribution",
            $profileConfig.DistributionName,
            "--exec",
            "/bin/sh",
            "-lc",
            "echo main-computer-executor-ok && uname -a && test -x /usr/local/bin/main-computer-exec && /usr/local/bin/main-computer-exec"
        )

        if ($verify.ExitCode -eq 0 -and ($verify.Stdout -match "main-computer-executor-ok")) {
            Write-State "Verification marker" "OK" "main-computer-executor-ok"
        }
        else {
            $details = (($verify.Stderr, $verify.Stdout) -join "`n").Trim()
            Write-State "Verification marker" "WARN" $details
        }
    }
    else {
        Write-State $profileConfig.DistributionName "WARN" "Distribution is not installed yet. Run install-main-computer-runtime.ps1 -Profile $Name after building the rootfs."
    }
}

Write-Section "Main Computer WSL runtime doctor"
Write-State "RequestedProfile" "INFO" $Profile

$wslPath = Resolve-CommandPath $WslCommand
if ($null -eq $wslPath) {
    Write-State "wsl.exe" "ERROR" "$WslCommand was not found."
    exit 1
}

Write-State "wsl.exe" "OK" $wslPath

$statusResult = Invoke-Native -FilePath $wslPath -Arguments @("--status")
if ($statusResult.ExitCode -eq 0) {
    Write-State "wsl --status" "OK"
    if (-not [string]::IsNullOrWhiteSpace($statusResult.Stdout)) {
        Write-Host ($statusResult.Stdout.TrimEnd())
    }
}
else {
    Write-State "wsl --status" "WARN" (($statusResult.Stderr, $statusResult.Stdout) -join "`n").Trim()
}

$versionResult = Invoke-Native -FilePath $wslPath -Arguments @("--version")
if ($versionResult.ExitCode -eq 0) {
    Write-State "wsl --version" "OK"
    if (-not [string]::IsNullOrWhiteSpace($versionResult.Stdout)) {
        Write-Host ($versionResult.Stdout.TrimEnd())
    }
}
else {
    Write-State "wsl --version" "WARN" "This WSL version may not support --version."
}

$distributions = @(Get-WslDistributions -CommandPath $wslPath)
if ($distributions.Count -gt 0) {
    Write-State "WSL distributions" "OK" ($distributions -join ", ")
}
else {
    Write-State "WSL distributions" "WARN" "No WSL distributions reported."
}

$defaultDistribution = Get-WslDefaultDistribution -StatusText $statusResult.Stdout
if (-not [string]::IsNullOrWhiteSpace($defaultDistribution)) {
    Write-State "Default WSL distro" "INFO" $defaultDistribution
}

if ($Profile -eq "all") {
    Show-ProfileDoctor -Name "test" -WslPath $wslPath -Distributions $distributions -DefaultDistribution $defaultDistribution
    Show-ProfileDoctor -Name "prod" -WslPath $wslPath -Distributions $distributions -DefaultDistribution $defaultDistribution
}
else {
    Show-ProfileDoctor -Name $Profile -WslPath $wslPath -Distributions $distributions -DefaultDistribution $defaultDistribution
}

Write-Section "Doctor completed"
