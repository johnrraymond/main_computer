[CmdletBinding()]
param(
    [ValidateSet("test", "prod")]
    [string]$Profile = "test",

    [string]$BaseImage = "ubuntu:24.04",

    [string]$ContainerTool = "",

    [string]$OutputPath = "",

    [string]$DistributionName = "",

    [string]$RuntimeRoot = "",

    [switch]$NoPull
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host $Title
    Write-Host ("-" * $Title.Length)
}

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    throw "[main-computer-runtime-build] $Message"
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
            ImageTag = "main-computer-executor-runtime:prod"
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
        ImageTag = "main-computer-executor-runtime:test"
        RootfsTar = Get-RuntimeImagePath -RuntimeRoot $runtimeRoot -DistributionName $distributionName
    }
}

function Resolve-CommandPath {
    param([string]$CommandName)

    if ([string]::IsNullOrWhiteSpace($CommandName)) {
        return $null
    }

    $hasPathSeparator = $CommandName.Contains("\") -or $CommandName.Contains("/") -or [System.IO.Path]::IsPathRooted($CommandName)
    if (-not $hasPathSeparator) {
        $command = Get-Command $CommandName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
            return $command.Source
        }
        return $null
    }

    if (Test-Path -LiteralPath $CommandName -PathType Leaf) {
        return (Resolve-Path -LiteralPath $CommandName).Path
    }

    return $null
}

function Resolve-ContainerTool {
    param([string]$RequestedTool)

    if (-not [string]::IsNullOrWhiteSpace($RequestedTool)) {
        $resolved = Resolve-CommandPath $RequestedTool
        if ($null -eq $resolved) {
            throw "Container tool executable not found: $RequestedTool"
        }
        return $resolved
    }

    foreach ($candidate in @("docker", "podman")) {
        $resolved = Resolve-CommandPath $candidate
        if ($null -ne $resolved) {
            return $resolved
        }
    }

    $wellKnownTools = @()
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $wellKnownTools += Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe"
        $wellKnownTools += Join-Path $env:ProgramFiles "RedHat\Podman\podman.exe"
    }
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $wellKnownTools += Join-Path ${env:ProgramFiles(x86)} "Docker\Docker\resources\bin\docker.exe"
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $wellKnownTools += Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\podman.exe"
    }

    foreach ($candidate in $wellKnownTools) {
        $resolved = Resolve-CommandPath $candidate
        if ($null -ne $resolved) {
            return $resolved
        }
    }

    throw "No supported container tool executable found. Install Docker or Podman, add it to PATH, or pass -ContainerTool <path-to-exe>."
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

function Write-Utf8NoBomFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )

    $normalized = $Text -replace "`r`n", "`n" -replace "`r", "`n"
    if ($normalized.Length -gt 0 -and $normalized[0] -eq [char]0xFEFF) {
        $normalized = $normalized.Substring(1)
    }

    $encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllText($Path, $normalized, $encoding)
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

$RepoRoot = Get-RepoRoot
$profileConfig = Get-RuntimeProfile -Name $Profile
if ([string]::IsNullOrWhiteSpace($DistributionName)) {
    $DistributionName = $profileConfig.DistributionName
}
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = $profileConfig.RuntimeRoot
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Get-RuntimeImagePath -RuntimeRoot $RuntimeRoot -DistributionName $DistributionName
}

$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$outputDir = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$containerToolPath = Resolve-ContainerTool -RequestedTool $ContainerTool
$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("main-computer-runtime-build-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

Write-Section "Building Main Computer executor rootfs"
Write-Host "Profile:       $($profileConfig.Name)"
Write-Host "Distribution:  $DistributionName"
Write-Host "RuntimeRoot:   $RuntimeRoot"
Write-Host "ContainerTool: $containerToolPath"
Write-Host "BaseImage:     $BaseImage"
Write-Host "ImageTag:      $($profileConfig.ImageTag)"
Write-Host "OutputPath:    $OutputPath"

try {
    $dockerfile = @'
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    coreutils \
    curl \
    git \
    procps \
    python3 \
    python3-venv \
    util-linux \
  && rm -rf /var/lib/apt/lists/*

RUN groupadd --system maincomputer \
  && useradd --system --create-home --shell /bin/bash --gid maincomputer maincomputer \
  && mkdir -p /workspace /artifacts /outputs /inputs \
  && chown -R maincomputer:maincomputer /workspace /artifacts /outputs /inputs

COPY main-computer-exec /usr/local/bin/main-computer-exec
RUN chmod 0755 /usr/local/bin/main-computer-exec

WORKDIR /workspace
'@

    $entrypointSource = Join-Path $RepoRoot "docker\executor\main-computer-exec"
    if (-not (Test-Path -LiteralPath $entrypointSource -PathType Leaf)) {
        Fail "Executor entrypoint source not found: $entrypointSource"
    }
    $entrypoint = Get-Content -LiteralPath $entrypointSource -Raw

    Write-Utf8NoBomFile -Path (Join-Path $tempDir "Dockerfile") -Text $dockerfile
    Write-Utf8NoBomFile -Path (Join-Path $tempDir "main-computer-exec") -Text $entrypoint

    if (-not $NoPull) {
        Write-Section "Pulling base image"
        Invoke-NativeChecked -FilePath $containerToolPath -Arguments @("pull", $BaseImage) | Out-Null
    }

    Write-Section "Building side-by-side runtime image"
    Invoke-NativeChecked -FilePath $containerToolPath -Arguments @(
        "build",
        "--build-arg", "BASE_IMAGE=$BaseImage",
        "-t", $profileConfig.ImageTag,
        $tempDir
    ) | Out-Null

    Write-Section "Validating runtime image"
    Invoke-NativeChecked -FilePath $containerToolPath -Arguments @(
        "run",
        "--rm",
        $profileConfig.ImageTag,
        "/bin/sh",
        "-lc",
        "echo main-computer-executor-ok && uname -a && test -x /usr/local/bin/main-computer-exec && /usr/local/bin/main-computer-exec run --cwd /workspace --timeout-ms 5000 --artifact-dir /outputs -- 'printf main-computer-exec-ready' | grep -q main-computer-exec-ready && echo main-computer-exec-contract-ok"
    ) | Out-Null

    Write-Section "Exporting rootfs tar"
    $cidFile = Join-Path $tempDir "container.cid"
    $createResult = Invoke-NativeChecked -FilePath $containerToolPath -Arguments @(
        "create",
        "--cidfile", $cidFile,
        $profileConfig.ImageTag,
        "/bin/sh"
    ) -Quiet

    $containerId = ""
    if (Test-Path -LiteralPath $cidFile) {
        $containerId = (Get-Content -LiteralPath $cidFile -Raw).Trim()
    }
    if ([string]::IsNullOrWhiteSpace($containerId)) {
        $containerId = ($createResult.Stdout).Trim()
    }
    if ([string]::IsNullOrWhiteSpace($containerId)) {
        throw "Container image was built, but no temporary container id was captured."
    }

    try {
        Invoke-NativeChecked -FilePath $containerToolPath -Arguments @("export", "-o", $OutputPath, $containerId) | Out-Null
    }
    finally {
        Invoke-Native -FilePath $containerToolPath -Arguments @("rm", "-f", $containerId) -Quiet | Out-Null
    }

    $hashPath = "$OutputPath.sha256"
    $hash = (Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $hashPath -Value "$hash  $(Split-Path -Leaf $OutputPath)" -Encoding ASCII

    Write-Section "Build complete"
    Write-Host "Profile:      $($profileConfig.Name)"
    Write-Host "Distribution: $DistributionName"
    Write-Host "Rootfs:       $OutputPath"
    Write-Host "SHA256:       $hashPath"
    Write-Host ""
    Write-Host "Next:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\windows\doctor-main-computer-runtime.ps1 -Profile $($profileConfig.Name)"
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\windows\install-main-computer-runtime.ps1 -Profile $($profileConfig.Name)"
}
finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}
