[CmdletBinding()]
param(
    [string]$Model = "gemma4:26b",
    [int]$Port = 18789,
    [string]$StateRoot,
    [string]$GatewayToken,
    [string]$Image = "ghcr.io/openclaw/openclaw:latest",
    [string]$ProjectName = "main-computer-openclaw",
    [switch]$Down,
    [switch]$NoSmoke
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

function New-MainComputerToken {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        if ($null -ne $rng) {
            $rng.Dispose()
        }
    }
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Write-Info([string]$Message) {
    Write-Host "[openclaw-docker] $Message"
}

function Invoke-JsonGet([string]$Uri) {
    try {
        return Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 20
    } catch {
        throw "GET $Uri failed: $($_.Exception.Message)"
    }
}

function Wait-HttpOk([string]$Uri, [int]$TimeoutSeconds = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = $null

    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
            if ([int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 500) {
                return
            }
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }

    throw "Timed out waiting for $Uri. Last error: $lastError"
}

function Show-DockerDiagnostics {
    Write-Info "Docker Compose status"
    try {
        & docker @composeArgs ps
    } catch {
        Write-Warning "Unable to collect Docker Compose status: $($_.Exception.Message)"
    }

    Write-Info "recent OpenClaw container logs"
    try {
        & docker @composeArgs logs --tail 120 openclaw-gateway
    } catch {
        Write-Warning "Unable to collect Docker logs: $($_.Exception.Message)"
    }
}

function ConvertTo-OpenClawModelEntries($OllamaTags) {
    $models = New-Object System.Collections.ArrayList
    foreach ($entry in @($OllamaTags.models)) {
        $name = [string]$entry.name
        if ([string]::IsNullOrWhiteSpace($name)) {
            continue
        }

        # OpenClaw's config schema requires models.providers.<id>.models to be
        # a JSON array. Use ArrayList + ToArray so a host with only one Ollama
        # model still serializes as `[ { ... } ]`, not `{ ... }`.
        [void]$models.Add([ordered]@{
            id = $name
            name = $name
            input = @("text")
            cost = [ordered]@{
                input = 0
                output = 0
                cacheRead = 0
                cacheWrite = 0
            }
            contextWindow = 128000
            maxTokens = 8192
            params = [ordered]@{
                keep_alive = "15m"
            }
        })
    }
    return @($models.ToArray())
}

$repoRoot = Resolve-RepoRoot
$composeFile = Join-Path $repoRoot "deploy\openclaw-docker\docker-compose.yml"
if (-not (Test-Path $composeFile)) {
    throw "Compose file not found: $composeFile"
}

if ([string]::IsNullOrWhiteSpace($StateRoot)) {
    $baseState = $env:LOCALAPPDATA
    if ([string]::IsNullOrWhiteSpace($baseState)) {
        $baseState = Join-Path $HOME ".main-computer"
    } else {
        $baseState = Join-Path $baseState "MainComputer"
    }
    $StateRoot = Join-Path $baseState "openclaw-docker"
}

$configDir = Join-Path $StateRoot "config"
$workspaceDir = Join-Path $StateRoot "workspace"
$authSecretDir = Join-Path $StateRoot "auth"
foreach ($path in @($configDir, $workspaceDir, $authSecretDir)) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}

$composeArgs = @("compose", "-f", $composeFile, "--project-name", $ProjectName)

if ($Down) {
    Write-Info "stopping Docker OpenClaw stack"
    & docker @composeArgs down
    exit $LASTEXITCODE
}

if ([string]::IsNullOrWhiteSpace($GatewayToken)) {
    $tokenFile = Join-Path $StateRoot "gateway-token.txt"
    if (Test-Path $tokenFile) {
        $GatewayToken = (Get-Content -Raw -Path $tokenFile).Trim()
    }
    if ([string]::IsNullOrWhiteSpace($GatewayToken)) {
        $GatewayToken = New-MainComputerToken
        Set-Content -Path $tokenFile -Value $GatewayToken -Encoding UTF8
    }
}

if ($Model -notmatch '^[A-Za-z0-9._:/-]+$') {
    throw "Model contains unsupported characters for this smoke helper: $Model"
}

Write-Info "checking Docker"
& docker version | Out-Null

$hostOllamaUrl = "http://127.0.0.1:11434"
$containerOllamaUrl = "http://host.docker.internal:11434"
$containerGatewayPort = 18789
Write-Info "checking host Ollama at $hostOllamaUrl/api/tags"
$ollamaTags = Invoke-JsonGet "$hostOllamaUrl/api/tags"
$modelEntries = @(ConvertTo-OpenClawModelEntries $ollamaTags)
$modelNames = @($modelEntries | ForEach-Object { [string]$_.id })

if ($modelNames.Count -eq 0) {
    throw "Ollama is reachable, but /api/tags returned no model names."
}
if ($modelNames -notcontains $Model) {
    throw "Ollama is reachable, but model '$Model' was not in /api/tags. Available models: $($modelNames -join ', ')"
}

$openClawConfig = [ordered]@{
    gateway = [ordered]@{
        mode = "local"
        bind = "lan"
        # This is the port OpenClaw listens on inside the container. The script's
        # -Port option is the host-published port in docker-compose.yml.
        port = $containerGatewayPort
        auth = [ordered]@{
            mode = "token"
            token = $GatewayToken
        }
        http = [ordered]@{
            endpoints = [ordered]@{
                responses = [ordered]@{
                    enabled = $true
                }
                chatCompletions = [ordered]@{
                    enabled = $true
                }
            }
        }
    }
    models = [ordered]@{
        providers = [ordered]@{
            ollama = [ordered]@{
                api = "ollama"
                apiKey = "ollama-local"
                baseUrl = $containerOllamaUrl
                timeoutSeconds = 300
                models = @($modelEntries)
            }
        }
    }
    agents = [ordered]@{
        defaults = [ordered]@{
            model = [ordered]@{
                primary = "ollama/$Model"
            }
        }
    }
}

$configPath = Join-Path $configDir "openclaw.json"
$openClawConfig | ConvertTo-Json -Depth 20 | Set-Content -Path $configPath -Encoding UTF8

$runtimeEnvPath = Join-Path $StateRoot "main-computer-openclaw.env"
$envLines = @(
    "OPENCLAW_IMAGE=$Image",
    "OPENCLAW_GATEWAY_HOST=127.0.0.1",
    "OPENCLAW_GATEWAY_PORT=$Port",
    "OPENCLAW_GATEWAY_TOKEN=$GatewayToken",
    "OPENCLAW_CONFIG_DIR=$configDir",
    "OPENCLAW_WORKSPACE_DIR=$workspaceDir",
    "OPENCLAW_AUTH_PROFILE_SECRET_DIR=$authSecretDir",
    "MAIN_COMPUTER_OPENCLAW_BASE_URL=http://127.0.0.1:$Port",
    "MAIN_COMPUTER_OPENCLAW_TOKEN=$GatewayToken",
    "MAIN_COMPUTER_OPENCLAW_BACKEND_MODEL=ollama/$Model"
)
Set-Content -Path $runtimeEnvPath -Value $envLines -Encoding UTF8

$env:OPENCLAW_IMAGE = $Image
$env:OPENCLAW_GATEWAY_HOST = "127.0.0.1"
$env:OPENCLAW_GATEWAY_PORT = [string]$Port
$env:OPENCLAW_GATEWAY_TOKEN = $GatewayToken
$env:OPENCLAW_CONFIG_DIR = $configDir
$env:OPENCLAW_WORKSPACE_DIR = $workspaceDir
$env:OPENCLAW_AUTH_PROFILE_SECRET_DIR = $authSecretDir

Write-Info "wrote OpenClaw config: $configPath"
Write-Info "wrote smoke env: $runtimeEnvPath"
Write-Info "starting Docker OpenClaw Gateway"
& docker @composeArgs up -d
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$baseUrl = "http://127.0.0.1:$Port"
try {
    Wait-HttpOk "$baseUrl/healthz" 120
    Write-Info "Gateway health endpoint is reachable: $baseUrl/healthz"
} catch {
    Show-DockerDiagnostics
    throw
}

$modelJson = $Model | ConvertTo-Json -Compress
$probeJs = @"
const model = $modelJson;
fetch("$containerOllamaUrl/api/tags")
  .then(async (response) => {
    const payload = await response.json();
    const names = (payload.models || []).map((entry) => entry.name).filter(Boolean);
    console.log(names.join("\\n"));
    if (!names.includes(model)) {
      console.error("container can reach Ollama, but model is missing: " + model);
      process.exit(2);
    }
  })
  .catch((error) => {
    console.error(error && error.message ? error.message : String(error));
    process.exit(1);
  });
"@

Write-Info "probing host Ollama from inside the OpenClaw container"
& docker @composeArgs exec -T openclaw-gateway node -e $probeJs
if ($LASTEXITCODE -ne 0) {
    throw "OpenClaw container could not confirm model '$Model' through $containerOllamaUrl/api/tags."
}

Write-Info "probing OpenClaw /v1/models"
try {
    $headers = @{ Authorization = "Bearer $GatewayToken" }
    $modelsResponse = Invoke-RestMethod -Uri "$baseUrl/v1/models" -Headers $headers -Method Get -TimeoutSec 30
    if ($modelsResponse) {
        Write-Info "/v1/models responded"
    }
} catch {
    throw "OpenClaw /v1/models probe failed: $($_.Exception.Message)"
}

$env:MAIN_COMPUTER_OPENCLAW_BASE_URL = $baseUrl
$env:MAIN_COMPUTER_OPENCLAW_TOKEN = $GatewayToken
$env:MAIN_COMPUTER_OPENCLAW_BACKEND_MODEL = "ollama/$Model"

$smokePath = Join-Path $repoRoot "scripts\smoke_openclaw_persistence.py"
if ((-not $NoSmoke) -and (Test-Path $smokePath)) {
    Write-Info "running persistence smoke through Docker OpenClaw"
    & python $smokePath --backend-model "ollama/$Model" --json
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} elseif (-not (Test-Path $smokePath)) {
    Write-Info "persistence smoke script not found; skipping optional smoke: $smokePath"
}

Write-Host ""
Write-Host "Docker OpenClaw is ready for Main Computer persistence smoke."
Write-Host "Gateway URL: $baseUrl"
Write-Host "Backend model: ollama/$Model"
Write-Host ""
Write-Host "For this PowerShell session:"
Write-Host "`$env:MAIN_COMPUTER_OPENCLAW_BASE_URL = `"$baseUrl`""
Write-Host "`$env:MAIN_COMPUTER_OPENCLAW_TOKEN = `"$GatewayToken`""
Write-Host "python scripts\smoke_openclaw_persistence.py --backend-model ollama/$Model --json"
