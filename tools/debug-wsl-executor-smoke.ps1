param(
    [string]$InstallRoot = "",
    [string]$RepoRoot = "",
    [int]$Port = 28865,
    [switch]$RestartIfWrongBackend
)

$ErrorActionPreference = "Stop"

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

function Test-MainComputerInstallRoot {
    param([AllowEmptyString()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    if (-not (Test-Path -LiteralPath $expanded -PathType Container)) {
        return $false
    }

    $markers = @(
        "run-main-computer.ps1",
        "main-computer-install.json",
        "runtime\main-computer-install.json"
    )

    foreach ($marker in $markers) {
        if (Test-Path -LiteralPath (Join-Path $expanded $marker)) {
            return $true
        }
    }

    return $false
}

function Resolve-MainComputerInstallRoot {
    param(
        [AllowEmptyString()][string]$Path,
        [AllowEmptyString()][string]$FallbackRoot
    )

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($Path)) { $candidates.Add($Path) }
    if (-not [string]::IsNullOrWhiteSpace($env:MC_INSTALL)) { $candidates.Add($env:MC_INSTALL) }
    if (-not [string]::IsNullOrWhiteSpace($env:MAIN_COMPUTER_INSTALL_ROOT)) { $candidates.Add($env:MAIN_COMPUTER_INSTALL_ROOT) }
    if (-not [string]::IsNullOrWhiteSpace($env:MAIN_COMPUTER_ROOT)) { $candidates.Add($env:MAIN_COMPUTER_ROOT) }
    if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        $candidates.Add($PSScriptRoot)
        $parent = Split-Path -Parent $PSScriptRoot
        if (-not [string]::IsNullOrWhiteSpace($parent)) { $candidates.Add($parent) }
    }
    if (-not [string]::IsNullOrWhiteSpace($FallbackRoot)) { $candidates.Add($FallbackRoot) }
    $candidates.Add((Get-Location).Path)

    foreach ($candidate in $candidates) {
        if (Test-MainComputerInstallRoot -Path $candidate) {
            return (Resolve-Path -LiteralPath ([Environment]::ExpandEnvironmentVariables($candidate))).Path
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($Path)) {
        return [Environment]::ExpandEnvironmentVariables($Path)
    }
    if (-not [string]::IsNullOrWhiteSpace($FallbackRoot)) {
        return $FallbackRoot
    }
    return (Get-Location).Path
}

$RepoRoot = Resolve-MainComputerRoot -Path $RepoRoot
$InstallRoot = Resolve-MainComputerInstallRoot -Path $InstallRoot -FallbackRoot $RepoRoot

function Section($Name) {
    Write-Host ""
    Write-Host $Name
    Write-Host ("-" * $Name.Length)
}

function Pass($Message) {
    Write-Host "[PASS] $Message" -ForegroundColor Green
}

function Warn($Message) {
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Fail($Message) {
    Write-Host "[FAIL] $Message" -ForegroundColor Red
    throw $Message
}

function Get-Json($Uri, [int]$TimeoutSec = 15) {
    Invoke-RestMethod `
        -Method GET `
        -Uri $Uri `
        -TimeoutSec $TimeoutSec `
        -Headers @{ Accept = "application/json" }
}

function Post-Json($Uri, $Body, [int]$TimeoutSec = 40) {
    Invoke-RestMethod `
        -Method POST `
        -Uri $Uri `
        -TimeoutSec $TimeoutSec `
        -Headers @{ Accept = "application/json" } `
        -ContentType "application/json" `
        -Body ($Body | ConvertTo-Json -Depth 8)
}

function Show-PortOwner([int]$Port) {
    Section "Debug app port owner"

    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Warn "Nothing is listening on 127.0.0.1:$Port / port $Port."
        return $null
    }

    foreach ($conn in $conns) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($conn.OwningProcess)" -ErrorAction SilentlyContinue
        Write-Host "Port:      $($conn.LocalAddress):$($conn.LocalPort)"
        Write-Host "PID:       $($conn.OwningProcess)"
        if ($proc) {
            Write-Host "Process:   $($proc.Name)"
            Write-Host "Command:   $($proc.CommandLine)"
        }
        else {
            Warn "Could not read process command line for PID $($conn.OwningProcess)."
        }
    }

    return $conns
}

function Check-SourcePatchPresence {
    Section "Source patch presence"

    $protoDev = Join-Path $RepoRoot "proto-dev\proto-dev.ps1"

    if (-not (Test-Path -LiteralPath $protoDev -PathType Leaf)) {
        Warn "Could not find $protoDev. Static patch check skipped."
        return
    }

    $text = Get-Content -LiteralPath $protoDev -Raw

    $needles = @(
        "Ensure-ProtoDevWslExecutorBackend",
        "app reported a non-WSL executor backend",
        "MAIN_COMPUTER_EXECUTOR_BACKEND=wsl",
        "Debug viewport still is not using the WSL executor after restart"
    )

    $missing = @()
    foreach ($needle in $needles) {
        if ($text -notlike "*$needle*") {
            $missing += $needle
        }
    }

    if ($missing.Count -eq 0) {
        Pass "proto-dev.ps1 contains the stale docker-backend restart guard."
    }
    else {
        Warn "proto-dev.ps1 does not appear to contain the stale backend restart guard."
        Write-Host "Missing strings:"
        $missing | ForEach-Object { Write-Host "  - $_" }
    }
}

function Get-ExecutorStatus([int]$Port) {
    $url = "http://127.0.0.1:$Port/api/executor/status"
    Get-Json -Uri $url -TimeoutSec 20
}

function Check-ExecutorStatus([int]$Port) {
    Section "Executor status endpoint"

    $status = Get-ExecutorStatus -Port $Port
    $json = $status | ConvertTo-Json -Depth 10

    Write-Host $json

    if ($status.ok -ne $true) {
        Fail "App status endpoint returned ok != true."
    }

    if ($null -eq $status.executor) {
        Fail "Status response did not include executor details."
    }

    if ($status.executor.ok -ne $true) {
        Fail "Executor object exists but executor.ok is not true."
    }

    if ($status.executor.backend -eq "wsl") {
        Pass "Executor backend is WSL."
        return $status
    }

    if ($status.executor.backend -eq "docker") {
        Warn "This reproduces the current bug: app is reachable, executor is healthy, but backend is docker instead of wsl."
        return $status
    }

    Warn "Executor backend is '$($status.executor.backend)', expected 'wsl'."
    return $status
}

function Run-ExecutorCommandSmoke([int]$Port) {
    Section "Executor command smoke"

    $run = Post-Json `
        -Uri "http://127.0.0.1:$Port/api/executor/run" `
        -TimeoutSec 40 `
        -Body @{
            command = "printf main-computer-debug-wsl-smoke-ok"
            cwd = "/workspace"
            timeout_s = 15
            network = $false
            description = "manual Debug WSL executor smoke test"
        }

    $json = $run | ConvertTo-Json -Depth 10
    Write-Host $json

    if ($run.ok -ne $true) {
        Fail "Executor run returned ok != true."
    }

    if ($run.backend -ne "wsl") {
        Fail "Executor run used backend '$($run.backend)', expected 'wsl'."
    }

    if ([string]$run.stdout -notlike "*main-computer-debug-wsl-smoke-ok*") {
        Fail "Executor stdout did not contain expected smoke marker."
    }

    Pass "Command ran through Windows app -> WSL executor."
}

function Restart-DebugRunner {
    Section "Optional Debug restart"

    $runner = Join-Path $InstallRoot "run-main-computer.ps1"

    if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
        Fail "Could not find generated runner: $runner"
    }

    Write-Host "Running:"
    Write-Host "  $runner restart -Mode Debug"
    & $runner restart -Mode Debug

    if ($LASTEXITCODE -ne 0) {
        Fail "Generated runner restart failed with exit code $LASTEXITCODE."
    }

    Pass "Generated runner restart completed."
}

Check-SourcePatchPresence
Show-PortOwner -Port $Port | Out-Null

Section "App reachability"
try {
    $status = Check-ExecutorStatus -Port $Port
}
catch {
    Fail "Could not query http://127.0.0.1:$Port/api/executor/status. Start Debug first, then rerun this script. Error: $($_.Exception.Message)"
}

if ($status.executor.backend -ne "wsl") {
    if ($RestartIfWrongBackend) {
        Restart-DebugRunner
        Start-Sleep -Seconds 3
        Show-PortOwner -Port $Port | Out-Null
        $status = Check-ExecutorStatus -Port $Port
    }
    else {
        Warn "Not restarting because -RestartIfWrongBackend was not supplied."
        Write-Host ""
        Write-Host "To test the proposed self-heal behavior after applying the patch, run:"
        Write-Host "  .\debug-wsl-executor-smoke.ps1 -RestartIfWrongBackend"
        exit 2
    }
}

Run-ExecutorCommandSmoke -Port $Port

Section "Summary"
Pass "Debug WSL executor smoke passed."