param(
  [string]$Root = ""
)

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

$Root = Resolve-MainComputerRoot -Path $Root

Write-Host "=== Main Computer v2 Docker naming twiddle ==="
Write-Host "Root: $Root"
Write-Host ""

$paths = @{
  launcher = Join-Path $Root "runtime\start_stop\main-computer-launcher.json"
  session = Join-Path $Root "runtime\start_stop\start-session.json"
  appEnv = Join-Path $Root "runtime\applications_service\applications.env"
  appState = Join-Path $Root "runtime\applications_service\state.json"
  startV2 = Join-Path $Root "start_v2.bat"
  stopV2 = Join-Path $Root "stop_v2.bat"
  startStop = Join-Path $Root "scripts\main-computer-start-stop.ps1"
}

Write-Host "=== Files ==="
$paths.GetEnumerator() | Sort-Object Name | ForEach-Object {
  "{0}: {1} exists={2}" -f $_.Key, $_.Value, (Test-Path -LiteralPath $_.Value)
}
Write-Host ""

function Show-JsonKeys($Path, $Label) {
  Write-Host "=== $Label ==="
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    Write-Host "missing"
    Write-Host ""
    return
  }
  try {
    $json = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    $json | ConvertTo-Json -Depth 8
  } catch {
    Write-Host "failed to parse: $($_.Exception.Message)"
    Get-Content -LiteralPath $Path -Raw
  }
  Write-Host ""
}

function Show-EnvFile($Path, $Label) {
  Write-Host "=== $Label ==="
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    Write-Host "missing"
    Write-Host ""
    return
  }

  $wanted = @(
    "MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT",
    "MAIN_COMPUTER_COOLIFY_PROJECT",
    "COMPOSE_PROJECT_NAME",
    "COOLIFY_COMPOSE_PROJECT",
    "COOLIFY_LOCAL_STATE",
    "COOLIFY_CONTAINER_NAME",
    "COOLIFY_POSTGRES_CONTAINER_NAME",
    "COOLIFY_REDIS_CONTAINER_NAME",
    "COOLIFY_SOKETI_CONTAINER_NAME",
    "COOLIFY_NETWORK_NAME",
    "APP_PORT",
    "SOKETI_PORT",
    "SOKETI_TERMINAL_PORT"
  )

  $lines = Get-Content -LiteralPath $Path
  foreach ($key in $wanted) {
    $match = $lines | Where-Object { $_ -match "^\s*$([regex]::Escape($key))=" } | Select-Object -First 1
    if ($match) {
      Write-Host $match
    }
  }
  Write-Host ""
}

Write-Host "=== Process environment in this shell ==="
@(
  "MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT",
  "MAIN_COMPUTER_COOLIFY_PROJECT",
  "COMPOSE_PROJECT_NAME",
  "COOLIFY_COMPOSE_PROJECT",
  "COOLIFY_CONTAINER_NAME",
  "MAIN_COMPUTER_COOLIFY_APP_PORT"
) | ForEach-Object {
  "{0}={1}" -f $_, [Environment]::GetEnvironmentVariable($_)
}
Write-Host ""

Show-JsonKeys $paths.launcher "launcher config"
Show-JsonKeys $paths.session "last start session"
Show-EnvFile $paths.appEnv "applications.env selected keys"
Show-JsonKeys $paths.appState "applications service state"

Write-Host "=== Docker compose projects ==="
docker compose ls 2>$null
Write-Host ""

Write-Host "=== Coolify/application containers with compose labels ==="
docker ps -a --format '{{.Names}}{{.Label "com.docker.compose.project"}}{{.Label "com.docker.compose.service"}}{{.Image}}{{.Status}}{{.Ports}}' |
  Select-String -Pattern 'coolify|applications|soketi|postgres|redis|onlyoffice|main-computer' |
  ForEach-Object { $_.Line }

Write-Host ""
Write-Host "=== Expected invariant ==="
Write-Host "Applications compose project must be exactly: main-computer-applications"
Write-Host "Coolify core container names must be exactly:"
Write-Host "  mc-applications-coolify"
Write-Host "  mc-applications-coolify-db"
Write-Host "  mc-applications-coolify-redis"
Write-Host "  mc-applications-coolify-realtime"
Write-Host "No start_v2 path should create: main-computer-coolify-main-computer-test-unleashed"
