# start-main-computer-docker-windows.ps1
#
# Start the Main Computer viewport in Docker on a Windows host while preserving
# Windows-display paths such as C:\Users\... in the UI.
#
# Example alternate port:
#   powershell -ExecutionPolicy Bypass -File .\start-main-computer-docker-windows.ps1 -HostPort 8875

param(
    [string]$RepoRoot = (Get-Location).Path,
    [int]$HostPort = 8765,
    [int]$TimeoutSeconds = 90,
    [string[]]$IncludeDrives = @(),
    [string[]]$ExcludeDrives = @(),
    [switch]$ReadOnlyDrives,
    [switch]$SkipVerify,
    [switch]$NoForceRecreate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Fail {
    param([string]$Message)
    throw "[FAIL] $Message"
}

function Normalize-DriveLetter {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }
    $text = $Value.Trim()
    if ($text.Length -ge 1) {
        return $text.Substring(0, 1).ToUpperInvariant()
    }
    return ""
}

function Escape-YamlSingleQuoted {
    param([string]$Value)
    return $Value.Replace("'", "''")
}

function Invoke-JsonGet {
    param(
        [string]$Url,
        [int]$TimeoutSec = 10
    )
    return Invoke-RestMethod -Method Get -Uri $Url -TimeoutSec $TimeoutSec -Headers @{ Accept = "application/json" }
}

function Wait-ForMountedWindowsPathMode {
    param(
        [string]$BaseUrl,
        [int]$TimeoutSec
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lastError = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $mounts = Invoke-JsonGet -Url "$BaseUrl/api/path-mounts" -TimeoutSec 5
            if ($mounts.path_mode -eq "mounted-windows" -and $mounts.host_os -eq "windows" -and $mounts.enabled -eq $true) {
                return $mounts
            }
            $lastError = "path_mode=$($mounts.path_mode) host_os=$($mounts.host_os) enabled=$($mounts.enabled)"
        }
        catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }

    Fail "docker app did not report mounted-windows/windows before timeout; last state was $lastError. If it still reports local/auto, check generated compose.host-drives.yml and container environment."
}

function Write-InternalPathMountProbe {
    param([string]$ProbeFile)

@'
import json
import urllib.request

data = json.loads(urllib.request.urlopen("http://127.0.0.1:8765/api/path-mounts", timeout=10).read().decode("utf-8"))
print(json.dumps(data, sort_keys=True))
if data.get("path_mode") != "mounted-windows" or data.get("host_os") != "windows" or data.get("enabled") is not True:
    raise SystemExit("container internal /api/path-mounts did not report mounted-windows/windows enabled=true")
'@ | Set-Content -Path $ProbeFile -Encoding UTF8
}

function Invoke-ContainerPathMountProbe {
    param(
        [string]$ContainerId,
        [string]$ContainerProbePath
    )

    $command = "docker exec $ContainerId python $ContainerProbePath 2>&1"
    $raw = @(cmd.exe /d /s /c $command)
    return [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Output = ($raw -join "`n")
    }
}

function Wait-ForContainerInternalPathMode {
    param(
        [string]$ContainerId,
        [string]$ContainerProbePath,
        [int]$TimeoutSec,
        [string]$ComposeFile,
        [string]$OverrideFile
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lastOutput = ""
    while ((Get-Date) -lt $deadline) {
        $result = Invoke-ContainerPathMountProbe -ContainerId $ContainerId -ContainerProbePath $ContainerProbePath
        $lastOutput = $result.Output
        if ($result.ExitCode -eq 0) {
            return $lastOutput
        }
        Start-Sleep -Seconds 2
    }

    docker compose -f $ComposeFile -f $OverrideFile logs --tail 120 main-computer
    Fail "Container-internal /api/path-mounts probe did not pass before timeout. Last output: $lastOutput"
}

$IsWindowsHost = $false
if (Get-Variable IsWindows -ErrorAction SilentlyContinue) {
    $IsWindowsHost = [bool]$IsWindows
}
else {
    $IsWindowsHost = $env:OS -eq "Windows_NT"
}
if (-not $IsWindowsHost) {
    Fail "This launcher is intended for Docker Desktop on Windows hosts."
}

$RepoRoot = (Resolve-Path $RepoRoot).Path
$ComposeFile = Join-Path $RepoRoot "docker-compose.dev.yml"
if (-not (Test-Path $ComposeFile)) {
    Fail "Missing docker-compose.dev.yml. Run this from the repository root or pass -RepoRoot."
}

$TmpDir = Join-Path $RepoRoot ".tmp"
New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null
$OverrideFile = Join-Path $TmpDir "compose.host-drives.yml"
$ProbeFile = Join-Path $TmpDir "docker-windows-path-mount-probe.py"
$ContainerProbePath = "/workspace/.tmp/docker-windows-path-mount-probe.py"

$includeSet = @{}
foreach ($item in $IncludeDrives) {
    $includeLetter = Normalize-DriveLetter $item
    if ($includeLetter) {
        $includeSet[$includeLetter] = $true
    }
}

$excludeSet = @{}
foreach ($item in $ExcludeDrives) {
    $excludeLetter = Normalize-DriveLetter $item
    if ($excludeLetter) {
        $excludeSet[$excludeLetter] = $true
    }
}

$drives = @()
foreach ($psDrive in Get-PSDrive -PSProvider FileSystem) {
    $letter = Normalize-DriveLetter $psDrive.Name
    if (-not $letter) {
        continue
    }
    if ($includeSet.Count -gt 0 -and -not $includeSet.ContainsKey($letter)) {
        continue
    }
    if ($excludeSet.ContainsKey($letter)) {
        continue
    }
    $rootPath = $psDrive.Root
    if (-not $rootPath -or -not (Test-Path $rootPath)) {
        continue
    }
    $target = "/host/$($letter.ToLowerInvariant())"
    $drives += [pscustomobject]@{
        Letter = $letter
        Source = $rootPath
        Target = $target
    }
}

if ($drives.Count -eq 0) {
    Fail "No Windows filesystem drives were found to mount. Check IncludeDrives/ExcludeDrives."
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("services:")
$lines.Add("  main-computer:")
$lines.Add("    environment:")
$lines.Add("      MAIN_COMPUTER_PATH_MODE: mounted-windows")
$lines.Add("      MAIN_COMPUTER_HOST_OS: windows")
$lines.Add("      MAIN_COMPUTER_HOST_DRIVE_ROOT: /host")
$lines.Add("    volumes:")
foreach ($drive in $drives) {
    $source = Escape-YamlSingleQuoted $drive.Source
    $lines.Add("      - type: bind")
    $lines.Add("        source: '$source'")
    $lines.Add("        target: $($drive.Target)")
    if ($ReadOnlyDrives) {
        $lines.Add("        read_only: true")
    }
}
$lines | Set-Content -Path $OverrideFile -Encoding UTF8

$env:MAIN_COMPUTER_PATH_MODE = "mounted-windows"
$env:MAIN_COMPUTER_HOST_OS = "windows"
$env:MAIN_COMPUTER_HOST_DRIVE_ROOT = "/host"
$env:MAIN_COMPUTER_DOCKER_VIEWPORT_PORT = [string]$HostPort

Write-InternalPathMountProbe -ProbeFile $ProbeFile

Write-Host "Generated host-drive override: $OverrideFile"
foreach ($drive in $drives) {
    Write-Host "Mounting $($drive.Source) -> $($drive.Target)"
}
Write-Host "Starting Docker viewport on http://127.0.0.1:$HostPort"

$Arguments = @(
    "-f", $ComposeFile,
    "-f", $OverrideFile,
    "--profile", "app",
    "up", "-d"
)
if (-not $NoForceRecreate) {
    $Arguments += "--force-recreate"
}
$Arguments += "main-computer"

docker compose @Arguments
if ($LASTEXITCODE -ne 0) {
    Fail "docker compose up failed"
}

if ($SkipVerify) {
    Write-Host "SkipVerify was set; not checking /api/path-mounts."
    return
}

$PsArguments = @(
    "-f", $ComposeFile,
    "-f", $OverrideFile,
    "ps", "-q", "main-computer"
)
$containerId = (docker compose @PsArguments).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($containerId)) {
    Fail "Unable to locate main-computer container id after docker compose up."
}

$internalRaw = Wait-ForContainerInternalPathMode -ContainerId $containerId -ContainerProbePath $ContainerProbePath -TimeoutSec $TimeoutSeconds -ComposeFile $ComposeFile -OverrideFile $OverrideFile

$BaseUrl = "http://127.0.0.1:$HostPort"
$hostMounts = Wait-ForMountedWindowsPathMode -BaseUrl $BaseUrl -TimeoutSec $TimeoutSeconds

if ($internalRaw -notmatch '"path_mode": "mounted-windows"' -and $internalRaw -notmatch '"path_mode":"mounted-windows"') {
    Fail "Container-internal /api/path-mounts is correct, but host API is not."
}

Write-Host "Container-internal /api/path-mounts:"
Write-Host $internalRaw
Write-Host "Host /api/path-mounts:"
$hostMounts | ConvertTo-Json -Depth 20 | Write-Host
Write-Host "Docker Windows viewport is ready at $BaseUrl"
