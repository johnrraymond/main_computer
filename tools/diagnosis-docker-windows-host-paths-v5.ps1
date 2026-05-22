# diagnosis-docker-windows-host-paths-v5.ps1
#
# Diagnostic + smoke test for Docker-on-Windows host-drive display behavior.
#
# v5 avoids PowerShell/Docker quoting problems by writing Python probe files
# into .tmp and running those files inside the container instead of using
# python -c with quoted Python source.
#
# Run from the repository root:
#   powershell -ExecutionPolicy Bypass -File .\diagnosis-docker-windows-host-paths-v5.ps1

param(
    [string]$RepoRoot = (Get-Location).Path,
    [int]$Port = 8875,
    [string]$ImageName = "main-computer-stage2a-smoke:latest",
    [string]$ContainerName = "main-computer-stage2a-smoke",
    [int]$TimeoutSeconds = 90,
    [switch]$KeepContainer
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Fail {
    param([string]$Message)
    throw "[FAIL] $Message"
}

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        Fail $Message
    }
}

function Invoke-JsonGet {
    param([string]$Uri)
    return Invoke-RestMethod -Method GET -Uri $Uri -TimeoutSec 10
}

function Invoke-JsonPost {
    param(
        [string]$Uri,
        [hashtable]$Body
    )
    $json = $Body | ConvertTo-Json -Depth 20
    return Invoke-RestMethod -Method POST -Uri $Uri -ContentType "application/json" -Body $json -TimeoutSec 10
}

function Get-SmokeContainerIds {
    param([string]$Name)
    $rawIds = & docker ps -aq --filter "name=^/$Name$"
    if ($LASTEXITCODE -ne 0) {
        Fail "docker ps failed while checking for existing smoke container"
    }

    $ids = @()
    foreach ($id in @($rawIds)) {
        if (-not [string]::IsNullOrWhiteSpace([string]$id)) {
            $ids += [string]$id
        }
    }
    return $ids
}

function Remove-SmokeContainerIfPresent {
    param([string]$Name)
    $ids = @(Get-SmokeContainerIds -Name $Name)
    if ($ids.Count -eq 0) {
        Write-Host "No existing smoke container named $Name"
        return
    }

    Write-Host "Removing existing smoke container: $($ids -join ', ')"
    & docker rm -f $ids | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail "docker rm -f failed for existing smoke container"
    }
}

function Convert-JsonOutput {
    param(
        [object]$RawOutput,
        [string]$Label
    )
    $text = (@($RawOutput) -join "`n").Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        Fail "$Label produced no JSON output"
    }
    try {
        return $text | ConvertFrom-Json
    }
    catch {
        Write-Host "$Label raw output:" -ForegroundColor Yellow
        Write-Host $text
        throw
    }
}

$RepoRoot = (Resolve-Path $RepoRoot).Path
$Dockerfile = Join-Path $RepoRoot "docker\dev\app.Dockerfile"
$MountedPathsFile = Join-Path $RepoRoot "main_computer\mounted_windows_paths.py"
$ConfigFile = Join-Path $RepoRoot "main_computer\config.py"
$CliFile = Join-Path $RepoRoot "main_computer\cli.py"

Write-Step "Preflight checks"
Assert-True (Test-Path $Dockerfile) "Missing docker/dev/app.Dockerfile. Run this from the repo root or pass -RepoRoot."
Assert-True (Test-Path $MountedPathsFile) "Missing main_computer/mounted_windows_paths.py. Apply the Stage 2A patch first."
Assert-True (Test-Path $ConfigFile) "Missing main_computer/config.py."
Assert-True (Test-Path $CliFile) "Missing main_computer/cli.py."

$MountedPathsText = Get-Content $MountedPathsFile -Raw
$ConfigText = Get-Content $ConfigFile -Raw
$CliText = Get-Content $CliFile -Raw

Assert-True ($MountedPathsText -match "discover_host_drive_mounts") "Stage 2A function discover_host_drive_mounts was not found. Apply the Stage 2A patch first."
Assert-True ($ConfigText -match "MAIN_COMPUTER_HOST_DRIVE_ROOT") "MAIN_COMPUTER_HOST_DRIVE_ROOT was not found in config.py. Apply the Stage 2A patch first."
Assert-True ($CliText -match "path_mode=base\.path_mode") "cli.py does not preserve path_mode from MainComputerConfig.from_env(). Apply the CLI env fix patch."
Assert-True ($CliText -match "host_os=base\.host_os") "cli.py does not preserve host_os from MainComputerConfig.from_env(). Apply the CLI env fix patch."
Assert-True ($CliText -match "host_drive_root=base\.host_drive_root") "cli.py does not preserve host_drive_root from MainComputerConfig.from_env(). Apply the CLI env fix patch."

$dockerVersion = & docker version --format "{{.Server.Version}}"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($dockerVersion)) {
    Fail "Docker does not appear to be running. Start Docker Desktop and try again."
}
Write-Host "Docker server version: $dockerVersion"
Write-Host "Repo root: $RepoRoot"
Write-Host "Host test URL will be: http://127.0.0.1:$Port"

$SmokeRoot = Join-Path $RepoRoot ".tmp\stage2a-docker-host-path-smoke"
$FakeC = Join-Path $SmokeRoot "fake-c"
$FixtureUser = "fixture-user"
$DesktopRelativePath = "Users/$FixtureUser/Desktop"
$DesktopNoteRelativePath = "$DesktopRelativePath/notes.txt"
$DesktopWindowsPath = "C:\Users\$FixtureUser\Desktop"
$DesktopNoteWindowsPath = "$DesktopWindowsPath\notes.txt"
$DesktopContainerPath = "/host/c/Users/$FixtureUser/Desktop"
$DesktopNoteContainerPath = "$DesktopContainerPath/notes.txt"
$DesktopDir = Join-Path $FakeC ("Users\{0}\Desktop" -f $FixtureUser)
$NoteFile = Join-Path $DesktopDir "notes.txt"
$ExpectedContent = "hello from docker host path smoke $(Get-Date -Format o)"
$ProbeFile = Join-Path $SmokeRoot "config_probe.py"
$ApiProbeFile = Join-Path $SmokeRoot "api_probe.py"

Write-Step "Creating Windows-side fake C: drive content"
if (Test-Path $SmokeRoot) {
    Remove-Item $SmokeRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $DesktopDir -Force | Out-Null
Set-Content -Path $NoteFile -Value $ExpectedContent -Encoding UTF8
Write-Host "Windows-side source file: $NoteFile"

@'
import json
from main_computer.cli import build_parser, _config_from_args

parser = build_parser()
args = parser.parse_args(["viewport", "--host", "0.0.0.0", "--port", "8765", "-noverbose"])
cfg = _config_from_args(args)
print(json.dumps({
    "path_mode": cfg.path_mode,
    "host_os": cfg.host_os,
    "host_drive_root": str(cfg.host_drive_root),
    "windows_drive_mounts": cfg.windows_drive_mounts,
    "workspace": str(cfg.workspace),
}))
'@ | Set-Content -Path $ProbeFile -Encoding UTF8

@'
import urllib.request

print(urllib.request.urlopen("http://127.0.0.1:8765/api/path-mounts", timeout=10).read().decode())
'@ | Set-Content -Path $ApiProbeFile -Encoding UTF8

$ContainerProbePath = "/workspace/.tmp/stage2a-docker-host-path-smoke/config_probe.py"
$ContainerApiProbePath = "/workspace/.tmp/stage2a-docker-host-path-smoke/api_probe.py"

Write-Step "Removing any old smoke container"
Remove-SmokeContainerIfPresent -Name $ContainerName

Write-Step "Building Docker image $ImageName"
$buildArgs = @(
    "build",
    "--pull=false",
    "-f", $Dockerfile,
    "-t", $ImageName,
    $RepoRoot
)
& docker @buildArgs
if ($LASTEXITCODE -ne 0) {
    Fail "docker build failed"
}

Write-Step "One-shot Docker config probe"
$probeArgs = @(
    "run",
    "--rm",
    "-e", "MAIN_COMPUTER_PATH_MODE=mounted-windows",
    "-e", "MAIN_COMPUTER_HOST_OS=windows",
    "-e", "MAIN_COMPUTER_HOST_DRIVE_ROOT=/host",
    "-e", "MAIN_COMPUTER_WORKSPACE=/workspace",
    "-e", "MAIN_COMPUTER_EXECUTOR_ENABLED=0",
    "--mount", "type=bind,source=$RepoRoot,target=/workspace",
    "--mount", "type=bind,source=$FakeC,target=/host/c,readonly",
    $ImageName,
    "python", $ContainerProbePath
)
$probeRaw = & docker @probeArgs
if ($LASTEXITCODE -ne 0) {
    Fail "One-shot Docker config probe failed"
}
$probe = Convert-JsonOutput -RawOutput $probeRaw -Label "One-shot Docker config probe"
$probe | ConvertTo-Json -Depth 20 | Write-Host
Assert-True ($probe.path_mode -eq "mounted-windows") "One-shot config probe did not preserve MAIN_COMPUTER_PATH_MODE."
Assert-True ($probe.host_os -eq "windows") "One-shot config probe did not preserve MAIN_COMPUTER_HOST_OS."
Assert-True ($probe.host_drive_root -eq "/host") "One-shot config probe did not preserve MAIN_COMPUTER_HOST_DRIVE_ROOT."

$ContainerStarted = $false

try {
    Write-Step "Starting viewport server container"
    $runArgs = @(
        "run",
        "-d",
        "--rm",
        "--name", $ContainerName,
        "-p", "$Port`:8765",
        "-e", "MAIN_COMPUTER_PATH_MODE=mounted-windows",
        "-e", "MAIN_COMPUTER_HOST_OS=windows",
        "-e", "MAIN_COMPUTER_HOST_DRIVE_ROOT=/host",
        "-e", "MAIN_COMPUTER_WORKSPACE=/workspace",
        "-e", "MAIN_COMPUTER_EXECUTOR_ENABLED=0",
        "--mount", "type=bind,source=$RepoRoot,target=/workspace",
        "--mount", "type=bind,source=$FakeC,target=/host/c,readonly",
        $ImageName,
        "python", "-m", "main_computer.cli", "viewport", "--host", "0.0.0.0", "--port", "8765", "-noverbose"
    )
    $ContainerId = (& docker @runArgs).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ContainerId)) {
        Fail "docker run failed"
    }
    $ContainerStarted = $true
    Write-Host "Container: $ContainerId"

    Write-Step "Running-container config probe"
    $runningProbeRaw = & docker exec $ContainerName python $ContainerProbePath
    if ($LASTEXITCODE -ne 0) {
        Fail "Running-container config probe failed"
    }
    $runningProbe = Convert-JsonOutput -RawOutput $runningProbeRaw -Label "Running-container config probe"
    $runningProbe | ConvertTo-Json -Depth 20 | Write-Host
    Assert-True ($runningProbe.path_mode -eq "mounted-windows") "Running container does not preserve MAIN_COMPUTER_PATH_MODE."
    Assert-True ($runningProbe.host_os -eq "windows") "Running container does not preserve MAIN_COMPUTER_HOST_OS."
    Assert-True ($runningProbe.host_drive_root -eq "/host") "Running container does not preserve MAIN_COMPUTER_HOST_DRIVE_ROOT."

    $BaseUrl = "http://127.0.0.1:$Port"
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $LastErr = $null
    $mounts = $null

    Write-Step "Waiting for server at $BaseUrl"
    while ((Get-Date) -lt $Deadline) {
        try {
            $mounts = Invoke-JsonGet "$BaseUrl/api/path-mounts"
            if ($null -ne $mounts -and $mounts.ok) {
                break
            }
        }
        catch {
            $LastErr = $_
            Start-Sleep -Seconds 2
        }
    }

    if ($null -eq $mounts -or -not $mounts.ok) {
        Write-Host ""
        Write-Host "Container logs:" -ForegroundColor Yellow
        & docker logs $ContainerName
        Fail "Server did not become ready before timeout. Last error: $LastErr"
    }

    Write-Step "Internal container API probe"
    $internalRaw = & docker exec $ContainerName python $ContainerApiProbePath
    if ($LASTEXITCODE -ne 0) {
        Fail "Internal container API probe failed"
    }
    $internalMounts = Convert-JsonOutput -RawOutput $internalRaw -Label "Internal container API probe"
    $internalMounts | ConvertTo-Json -Depth 20 | Write-Host

    Write-Step "Host API probe"
    $mounts = Invoke-JsonGet "$BaseUrl/api/path-mounts"
    $mounts | ConvertTo-Json -Depth 20 | Write-Host

    if ($internalMounts.path_mode -ne $mounts.path_mode -or $internalMounts.host_os -ne $mounts.host_os) {
        Fail "Host API response differs from container-internal API response. Host port may be hitting a different server."
    }

    Assert-True ($mounts.enabled -eq $true) "Expected mounted-windows resolver to be enabled."
    Assert-True ($mounts.path_mode -eq "mounted-windows") "Expected path_mode=mounted-windows."
    Assert-True ($mounts.host_os -eq "windows") "Expected host_os=windows."
    Assert-True ($mounts.count -ge 1) "Expected at least one discovered mounted drive."

    $driveC = $mounts.mounts | Where-Object { $_.id -eq "drive-c" } | Select-Object -First 1
    Assert-True ($null -ne $driveC) "Expected /host/c to be discovered as drive-c."
    Assert-True ($driveC.path_display -eq "C:\") "Expected drive-c display path to be C:\."
    Assert-True ($driveC.container_path -eq "/host/c") "Expected drive-c backend container path to be /host/c."

    Write-Step "Checking File Explorer roots"
    $roots = Invoke-JsonPost "$BaseUrl/api/applications/file-explorer/roots" @{}
    $rootC = $roots.roots | Where-Object { $_.id -eq "drive-c" } | Select-Object -First 1
    Assert-True ($null -ne $rootC) "Expected drive-c in file explorer roots."
    Assert-True ($rootC.path_display -eq "C:\") "Expected file explorer root display C:\."
    Write-Host "drive-c root:" ($rootC | ConvertTo-Json -Depth 20)

    Write-Step "Checking File Explorer list"
    $listed = Invoke-JsonPost "$BaseUrl/api/applications/file-explorer/list" @{
        root_id = "drive-c"
        relative_path = $DesktopRelativePath
    }
    $noteEntry = $listed.entries | Where-Object { $_.name -eq "notes.txt" } | Select-Object -First 1
    Assert-True ($null -ne $noteEntry) "Expected notes.txt in $DesktopWindowsPath."
    Assert-True ($noteEntry.path_display -eq $DesktopNoteWindowsPath) "Expected Windows display path for notes.txt, got: $($noteEntry.path_display)"
    Assert-True ($noteEntry.mounted_windows_drive -eq $true) "Expected mounted_windows_drive=true for notes.txt."
    Write-Host "notes.txt entry:" ($noteEntry | ConvertTo-Json -Depth 20)

    Write-Step "Checking File Explorer read"
    $read = Invoke-JsonPost "$BaseUrl/api/applications/file-explorer/read" @{
        root_id = "drive-c"
        relative_path = $DesktopNoteRelativePath
    }
    Assert-True ($read.readable -eq $true) "Expected notes.txt to be readable."
    Assert-True (($read.content -join "`n") -match [regex]::Escape($ExpectedContent)) "Expected read content to contain smoke text."
    Assert-True ($read.entry.path_display -eq $DesktopNoteWindowsPath) "Expected read entry display path $DesktopNoteWindowsPath."
    Assert-True ($read.entry.mounted_windows_drive -eq $true) "Expected read entry mounted_windows_drive=true."

    Write-Step "SUCCESS"
    Write-Host "Docker backend source mounted at: $DesktopNoteContainerPath" -ForegroundColor Green
    Write-Host "Windows/API display path:       $DesktopNoteWindowsPath" -ForegroundColor Green
}
finally {
    if ($ContainerStarted -and -not $KeepContainer) {
        Write-Step "Cleaning up smoke container"
        Remove-SmokeContainerIfPresent -Name $ContainerName
    }
    elseif ($ContainerStarted -and $KeepContainer) {
        Write-Host ""
        Write-Host "Keeping container because -KeepContainer was provided: $ContainerName"
        Write-Host "Stop it later with: docker rm -f $ContainerName"
    }
}
