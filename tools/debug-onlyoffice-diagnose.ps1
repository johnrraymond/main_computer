param(
    [int]$Port = 28084,
    [string]$Project = "main-computer-onlyoffice-debug",
    [string]$ComposeFile = "",
    [int]$WaitSeconds = 180,
    [int]$PollSeconds = 5
)

$ErrorActionPreference = "Continue"

function Test-MainComputerRoot {
    param([AllowEmptyString()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    if (-not (Test-Path -LiteralPath $expanded -PathType Container)) {
        return $false
    }

    $markers = @(
        "pyproject.toml",
        "bootstrap-main-computer-windows.ps1",
        "main_computer",
        "runtime\main-computer-install.json",
        "main-computer-install.json",
        "run-main-computer.ps1"
    )

    foreach ($marker in $markers) {
        if (Test-Path -LiteralPath (Join-Path $expanded $marker)) {
            return $true
        }
    }

    return $false
}

function Resolve-MainComputerRoot {
    param([AllowEmptyString()][string]$Path)

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($Path)) { $candidates.Add($Path) }
    if (-not [string]::IsNullOrWhiteSpace($env:MAIN_COMPUTER_ROOT)) { $candidates.Add($env:MAIN_COMPUTER_ROOT) }
    if (-not [string]::IsNullOrWhiteSpace($env:MC_INSTALL)) { $candidates.Add($env:MC_INSTALL) }
    if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        $candidates.Add($PSScriptRoot)
        $parent = Split-Path -Parent $PSScriptRoot
        if (-not [string]::IsNullOrWhiteSpace($parent)) { $candidates.Add($parent) }
    }
    $candidates.Add((Get-Location).Path)

    foreach ($candidate in $candidates) {
        if (Test-MainComputerRoot -Path $candidate) {
            return (Resolve-Path -LiteralPath ([Environment]::ExpandEnvironmentVariables($candidate))).Path
        }
    }

    return (Get-Location).Path
}

function Resolve-OnlyOfficeComposeFile {
    param([AllowEmptyString()][string]$Path)

    if (-not [string]::IsNullOrWhiteSpace($Path)) {
        return [Environment]::ExpandEnvironmentVariables($Path)
    }

    if (-not [string]::IsNullOrWhiteSpace($env:MAIN_COMPUTER_ONLYOFFICE_COMPOSE_FILE)) {
        return [Environment]::ExpandEnvironmentVariables($env:MAIN_COMPUTER_ONLYOFFICE_COMPOSE_FILE)
    }

    $root = Resolve-MainComputerRoot -Path ""
    return (Join-Path $root "docker-compose.onlyoffice.yml")
}

$ComposeFile = Resolve-OnlyOfficeComposeFile -Path $ComposeFile

function Section($Name) {
    Write-Host ""
    Write-Host $Name
    Write-Host ("-" * $Name.Length)
}

function Run($Label, [scriptblock]$Block) {
    Write-Host ""
    Write-Host ">>> $Label"
    try {
        & $Block
    }
    catch {
        Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Try-Http($Name, $Url, [int]$TimeoutSec = 10) {
    $result = [ordered]@{
        name = $Name
        url = $Url
        ok = $false
        status = $null
        error = $null
        bytes = 0
    }

    try {
        $resp = Invoke-WebRequest `
            -Uri $Url `
            -Method GET `
            -TimeoutSec $TimeoutSec `
            -UseBasicParsing `
            -ErrorAction Stop

        $result.ok = $true
        $result.status = [int]$resp.StatusCode
        if ($null -ne $resp.Content) {
            $result.bytes = $resp.Content.Length
        }
    }
    catch {
        $result.error = $_.Exception.Message

        if ($_.Exception.Response) {
            try {
                $result.status = [int]$_.Exception.Response.StatusCode
            }
            catch {}
        }
    }

    [pscustomobject]$result
}

function Test-Tcp($HostName, [int]$Port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(3000, $false)

        if (-not $ok) {
            $client.Close()
            return $false
        }

        $client.EndConnect($iar)
        $client.Close()
        return $true
    }
    catch {
        return $false
    }
}

Section "ONLYOFFICE diagnostic"
Write-Host "Port:         $Port"
Write-Host "Project:      $Project"
Write-Host "Compose file: $ComposeFile"
Write-Host "WaitSeconds:  $WaitSeconds"

Section "Host port owner"
Run "Get-NetTCPConnection for $Port" {
    $conns = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Host "No host listener found on port $Port."
    }
    foreach ($conn in $conns) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($conn.OwningProcess)" -ErrorAction SilentlyContinue
        Write-Host "Local:   $($conn.LocalAddress):$($conn.LocalPort)"
        Write-Host "State:   $($conn.State)"
        Write-Host "PID:     $($conn.OwningProcess)"
        if ($proc) {
            Write-Host "Name:    $($proc.Name)"
            Write-Host "Command: $($proc.CommandLine)"
        }
        Write-Host ""
    }
}

Section "Docker compose status"
Run "docker compose ps" {
    docker compose -f $ComposeFile -p $Project ps
}

Section "Matching containers"
$containerIds = @()
try {
    $containerIds = docker ps -a `
        --filter "label=com.docker.compose.project=$Project" `
        --format "{{.ID}}" 2>$null
}
catch {}

if (-not $containerIds) {
    Write-Host "[FAIL] No containers found for compose project '$Project'." -ForegroundColor Red
}
else {
    foreach ($id in $containerIds) {
        Run "docker inspect $id" {
            docker inspect $id `
                --format "name={{.Name}} status={{.State.Status}} running={{.State.Running}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} started={{.State.StartedAt}} finished={{.State.FinishedAt}}"
        }

        Run "docker port $id" {
            docker port $id
        }

        Run "docker logs --tail 120 $id" {
            docker logs --tail 120 $id
        }
    }
}

Section "Initial HTTP checks"
$urls = @(
    @{ name = "root"; url = "http://127.0.0.1:$Port/" },
    @{ name = "healthcheck"; url = "http://127.0.0.1:$Port/healthcheck" },
    @{ name = "editor_api"; url = "http://127.0.0.1:$Port/web-apps/apps/api/documents/api.js" }
)

Write-Host "TCP 127.0.0.1:$Port = $(Test-Tcp -HostName "127.0.0.1" -Port $Port)"

foreach ($u in $urls) {
    Try-Http -Name $u.name -Url $u.url | Format-List
}

Section "Readiness polling"
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$ready = $false

while ((Get-Date) -lt $deadline) {
    $health = Try-Http -Name "healthcheck" -Url "http://127.0.0.1:$Port/healthcheck" -TimeoutSec 10
    $api = Try-Http -Name "editor_api" -Url "http://127.0.0.1:$Port/web-apps/apps/api/documents/api.js" -TimeoutSec 10

    $stamp = Get-Date -Format "HH:mm:ss"
    Write-Host "[$stamp] healthcheck ok=$($health.ok) status=$($health.status) error=$($health.error)"
    Write-Host "[$stamp] editor_api  ok=$($api.ok) status=$($api.status) bytes=$($api.bytes) error=$($api.error)"

    if ($health.ok -and $api.ok) {
        $ready = $true
        break
    }

    Start-Sleep -Seconds $PollSeconds
}

Section "Final result"

if ($ready) {
    Write-Host "[PASS] ONLYOFFICE became ready on http://127.0.0.1:$Port" -ForegroundColor Green
    exit 0
}

Write-Host "[FAIL] ONLYOFFICE did not become ready within $WaitSeconds seconds." -ForegroundColor Red
Write-Host ""
Write-Host "Likely causes:"
Write-Host "  1. The bootstrap/control script is checking too soon."
Write-Host "  2. The container is crash-looping or unhealthy."
Write-Host "  3. ONLYOFFICE needs a longer first-start timeout after image pull/volume creation."
Write-Host "  4. Something inside the container cannot initialize; check the docker logs printed above."
exit 1