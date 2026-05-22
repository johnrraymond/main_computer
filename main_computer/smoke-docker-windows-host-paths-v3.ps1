# smoke-docker-windows-host-paths-v3.ps1
#
# Builds the Main Computer dev Docker image, runs the viewport server in Linux,
# mounts a Windows-side fake C: drive root into /host/c, and verifies that the
# API/UI-facing file explorer paths display as Windows paths (C:\...) while the
# container backend reads from /host/c/...
#
# Run from the repository root after applying the Stage 2A patch:
#   powershell -ExecutionPolicy Bypass -File .\smoke-docker-windows-host-paths-v3.ps1
#
# Optional:
#   powershell -ExecutionPolicy Bypass -File .\smoke-docker-windows-host-paths-v3.ps1 -Port 8875 -KeepContainer

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

# PowerShell 7 can turn native non-zero exits into terminating errors. For this
# smoke script we explicitly check $LASTEXITCODE after Docker commands instead.
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

    # Important: wrap function output in @(...). With StrictMode, a no-output
    # function call assigned directly may become $null, and $null.Count fails.
    $ids = @(Get-SmokeContainerIds -Name $Name)

    if ($ids.Count -eq 0) {
        Write-Host "No existing smoke container named $Name"
        return
    }

    Write-Host "Removing existing smoke container: $($ids -join ', ')"
    & docker rm -f @ids | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail "docker rm -f failed for existing smoke container"
    }
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

$dockerVersion = & docker version --format "{{.Server.Version}}"
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

Write-Step "Checking /api/path-mounts"
$mounts = Invoke-JsonGet "$BaseUrl/api/path-mounts"
$mounts | ConvertTo-Json -Depth 20 | Write-Host
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
$listBody = @{
    root_id = "drive-c"
    relative_path = "Users/you/Desktop"
}
$listed = Invoke-JsonPost "$BaseUrl/api/applications/file-explorer/list" $listBody
$noteEntry = $listed.entries | Where-Object { $_.name -eq "notes.txt" } | Select-Object -First 1
Assert-True ($null -ne $noteEntry) "Expected notes.txt in C:\Users\you\Desktop."
Assert-True ($noteEntry.path_display -eq "C:\Users\you\Desktop\notes.txt") "Expected Windows display path for notes.txt, got: $($noteEntry.path_display)"
Assert-True ($noteEntry.mounted_windows_drive -eq $true) "Expected mounted_windows_drive=true for notes.txt."
Write-Host "notes.txt entry:" ($noteEntry | ConvertTo-Json -Depth 20)

Write-Step "Checking File Explorer read"
$readBody = @{
    root_id = "drive-c"
    relative_path = "Users/you/Desktop/notes.txt"
}
$read = Invoke-JsonPost "$BaseUrl/api/applications/file-explorer/read" $readBody
Assert-True ($read.readable -eq $true) "Expected notes.txt to be readable."
Assert-True (($read.content -join "`n") -match [regex]::Escape($ExpectedContent)) "Expected read content to contain smoke text."
Assert-True ($read.entry.path_display -eq "C:\Users\you\Desktop\notes.txt") "Expected read entry display path C:\Users\you\Desktop\notes.txt."
Assert-True ($read.entry.mounted_windows_drive -eq $true) "Expected read entry mounted_windows_drive=true."

Write-Step "SUCCESS"
Write-Host "Docker backend source mounted at: /host/c/Users/you/Desktop/notes.txt" -ForegroundColor Green
Write-Host "Windows/API display path:       C:\Users\you\Desktop\notes.txt" -ForegroundColor Green

if ($KeepContainer) {
    Write-Host ""
    Write-Host "Keeping container because -KeepContainer was provided: $ContainerName"
    Write-Host "Stop it later with: docker rm -f $ContainerName"
}
else {
    Write-Step "Cleaning up smoke container"
    Remove-SmokeContainerIfPresent -Name $ContainerName
}
