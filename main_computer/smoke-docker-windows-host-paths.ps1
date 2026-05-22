# smoke-docker-windows-host-paths.ps1
#
# Builds the Main Computer dev Docker image, runs the viewport server in Linux,
# mounts a Windows-side fake C: drive root into /host/c, and verifies that the
# API/UI-facing file explorer paths display as Windows paths (C:\...) while the
# container backend reads from /host/c/...
#
# Run from the repository root after applying the Stage 2A patch:
#   powershell -ExecutionPolicy Bypass -File .\smoke-docker-windows-host-paths.ps1
#
# Optional:
#   powershell -ExecutionPolicy Bypass -File .\smoke-docker-windows-host-paths.ps1 -Port 8875 -KeepContainer

param(
    [string]$RepoRoot = (Get-Location).Path,
    [int]$Port = 8765,
    [string]$ImageName = "main-computer-stage2a-smoke:latest",
    [string]$ContainerName = "main-computer-stage2a-smoke",
    [int]$TimeoutSeconds = 90,
    [switch]$KeepContainer
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

$RepoRoot = (Resolve-Path $RepoRoot).Path
$Dockerfile = Join-Path $RepoRoot "docker\dev\app.Dockerfile"
$MountedPathsFile = Join-Path $RepoRoot "main_computer\mounted_windows_paths.py"
$ConfigFile = Join-Path $RepoRoot "main_computer\config.py"

Write-Step "Preflight checks"
Assert-True (Test-Path $Dockerfile) "Missing docker/dev/app.Dockerfile. Run this from the repo root or pass -RepoRoot."
Assert-True (Test-Path $MountedPathsFile) "Missing main_computer/mounted_windows_paths.py. Apply the Stage 2A patch first."
Assert-True (Test-Path $ConfigFile) "Missing main_computer/config.py."

$MountedPathsText = Get-Content $MountedPathsFile -Raw
$ConfigText = Get-Content $ConfigFile -Raw
Assert-True ($MountedPathsText -match "discover_host_drive_mounts") "Stage 2A function discover_host_drive_mounts was not found. Apply the Stage 2A patch first."
Assert-True ($ConfigText -match "MAIN_COMPUTER_HOST_DRIVE_ROOT") "MAIN_COMPUTER_HOST_DRIVE_ROOT was not found in config.py. Apply the Stage 2A patch first."

$dockerVersion = & docker version --format "{{.Server.Version}}" 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($dockerVersion)) {
    Fail "Docker does not appear to be running. Start Docker Desktop and try again."
}
Write-Host "Docker server version: $dockerVersion"
Write-Host "Repo root: $RepoRoot"

# This is a fake C: root, created on the Windows side. It is mounted into the
# Linux container at /host/c. The server should read from /host/c but report
# display paths as C:\...
$SmokeRoot = Join-Path $RepoRoot ".tmp\stage2a-docker-host-path-smoke"
$FakeC = Join-Path $SmokeRoot "fake-c"
$DesktopDir = Join-Path $FakeC "Users\you\Desktop"
$NoteFile = Join-Path $DesktopDir "notes.txt"
$ExpectedContent = "hello from docker host path smoke $(Get-Date -Format o)"

Write-Step "Creating Windows-side fake C: drive content"
if (Test-Path $SmokeRoot) {
    Remove-Item $SmokeRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $DesktopDir -Force | Out-Null
Set-Content -Path $NoteFile -Value $ExpectedContent -Encoding UTF8
Write-Host "Windows-side source file: $NoteFile"

Write-Step "Removing any old smoke container"
& docker rm -f $ContainerName 2>$null | Out-Null

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
Write-Host "Container: $ContainerId"

$BaseUrl = "http://127.0.0.1:$Port"
$Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$LastError = $null

Write-Step "Waiting for server at $BaseUrl"
do {
    try {
        $mounts = Invoke-JsonGet "$BaseUrl/api/path-mounts"
        $LastError = $null
        break
    } catch {
        $LastError = $_.Exception.Message
        Start-Sleep -Milliseconds 750
    }
} while ((Get-Date) -lt $Deadline)

if ($LastError) {
    Write-Host ""
    Write-Host "Container logs:" -ForegroundColor Yellow
    & docker logs $ContainerName
    Fail "Server did not become ready before timeout. Last error: $LastError"
}

try {
    Write-Step "Checking /api/path-mounts"
    $mounts = Invoke-JsonGet "$BaseUrl/api/path-mounts"
    $mountsJson = $mounts | ConvertTo-Json -Depth 20
    Write-Host $mountsJson

    Assert-True ($mounts.enabled -eq $true) "Expected path mounts to be enabled."
    Assert-True ($mounts.path_mode -eq "mounted-windows") "Expected path_mode=mounted-windows."
    Assert-True ($mounts.host_os -eq "windows") "Expected host_os=windows."

    $driveC = @($mounts.mounts | Where-Object { $_.id -eq "drive-c" })[0]
    Assert-True ($null -ne $driveC) "Expected /api/path-mounts to include drive-c."
    Assert-True ($driveC.path_display -eq "C:\") "Expected drive-c path_display to be C:\ but got '$($driveC.path_display)'."
    Assert-True ($driveC.container_path -eq "/host/c") "Expected drive-c container_path to be /host/c but got '$($driveC.container_path)'."

    Write-Step "Checking file explorer roots"
    $roots = Invoke-JsonPost "$BaseUrl/api/applications/file-explorer/roots" @{}
    $rootC = @($roots.roots | Where-Object { $_.id -eq "drive-c" })[0]
    Assert-True ($null -ne $rootC) "Expected file explorer roots to include drive-c."
    Assert-True ($rootC.path_display -eq "C:\") "Expected root path_display C:\ but got '$($rootC.path_display)'."

    Write-Step "Checking file explorer list path display"
    $listed = Invoke-JsonPost "$BaseUrl/api/applications/file-explorer/list" @{
        root_id = "drive-c"
        relative_path = "Users/you/Desktop"
    }
    $entry = @($listed.entries | Where-Object { $_.name -eq "notes.txt" })[0]
    Assert-True ($null -ne $entry) "Expected notes.txt in C:\Users\you\Desktop listing."
    Assert-True ($entry.path_display -eq "C:\Users\you\Desktop\notes.txt") "Expected Windows display path. Got '$($entry.path_display)'."

    Write-Step "Checking file explorer read path display and content"
    $read = Invoke-JsonPost "$BaseUrl/api/applications/file-explorer/read" @{
        root_id = "drive-c"
        relative_path = "Users/you/Desktop/notes.txt"
    }
    Assert-True ($read.readable -eq $true) "Expected notes.txt to be readable."
    Assert-True ($read.entry.path_display -eq "C:\Users\you\Desktop\notes.txt") "Expected read entry display path C:\Users\you\Desktop\notes.txt. Got '$($read.entry.path_display)'."
    Assert-True (($read.content -join "`n") -match [regex]::Escape($ExpectedContent)) "Read content did not match Windows-side source file."

    Write-Step "PASS"
    Write-Host "Docker backend path mounted at: /host/c/Users/you/Desktop/notes.txt" -ForegroundColor Green
    Write-Host "UI/API display path returned as: C:\Users\you\Desktop\notes.txt" -ForegroundColor Green
    Write-Host "Windows-side source file was: $NoteFile" -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "Container logs:" -ForegroundColor Yellow
    & docker logs $ContainerName
    throw
}
finally {
    if ($KeepContainer) {
        Write-Host ""
        Write-Host "Keeping container running because -KeepContainer was passed: $ContainerName" -ForegroundColor Yellow
    } else {
        Write-Step "Cleaning up container"
        & docker rm -f $ContainerName 2>$null | Out-Null
    }
}
