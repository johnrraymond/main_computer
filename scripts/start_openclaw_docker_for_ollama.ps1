[CmdletBinding()]
param(
    [string]$Model = "gemma4:26b",
    [int]$Port = 18789,
    [string]$AgentId = "main",
    [string]$StateRoot,
    [string]$GatewayToken,
    [string]$Image = "ghcr.io/openclaw/openclaw:latest",
    [string]$ProjectName = "main-computer-openclaw",
    [int]$SmokeTimeoutSeconds = 300,
    [int]$ContextWindow = 8192,
    [int]$MaxTokens = 512,
    [int]$OllamaNumPredict = 128,
    [switch]$AgentSmoke,
    [switch]$FullSmoke,
    [switch]$SkipRestartProof,
    [switch]$ExtractMemory,
    [string]$ExtractOutDir,
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

function Invoke-DirectMemorySmoke([string]$SmokePath, [string]$WorkspaceDir) {
    Write-Info "running deterministic direct OpenClaw Markdown memory smoke"
    $directArgs = @(
        $SmokePath,
        "--direct-memory",
        "--memory-root", $WorkspaceDir,
        "--memory-poll-s", "5",
        "--json"
    )
    $directOutput = @(& python @directArgs 2>&1)
    $directExitCode = $LASTEXITCODE
    $directOutput | ForEach-Object { Write-Host $_ }
    if ($directExitCode -ne 0) {
        throw "direct Markdown memory smoke failed with exit code $directExitCode"
    }

    $directText = $directOutput -join "`n"
    $jsonStart = $directText.IndexOf("{")
    $jsonEnd = $directText.LastIndexOf("}")
    if ($jsonStart -lt 0 -or $jsonEnd -lt $jsonStart) {
        throw "direct Markdown memory smoke did not return a JSON object"
    }

    try {
        $directResult = $directText.Substring($jsonStart, $jsonEnd - $jsonStart + 1) | ConvertFrom-Json
    } catch {
        throw "direct Markdown memory smoke did not return parseable JSON: $($_.Exception.Message)"
    }

    if (-not $directResult.ok) {
        throw "direct Markdown memory smoke returned ok=false"
    }
    if ([string]::IsNullOrWhiteSpace([string]$directResult.marker)) {
        throw "direct Markdown memory smoke did not return a marker"
    }
    return [string]$directResult.marker
}

function Invoke-ContainerMemoryProbe([string]$Marker, [string]$Label) {
    # Use a single-quoted here-string so PowerShell does not try to expand the
    # JavaScript template literal variables (${root}, ${marker}) under
    # Set-StrictMode.
    $memoryProbeJs = @'
const fs = require('fs');
const path = require('path');

const marker = process.env.MAIN_COMPUTER_DIRECT_MEMORY_MARKER;
const root = '/home/node/.openclaw/workspace';

function scan(dir) {
  if (!fs.existsSync(dir)) {
    return null;
  }
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      const found = scan(full);
      if (found) {
        return found;
      }
    } else if (entry.isFile() && entry.name.toLowerCase().endsWith('.md')) {
      const text = fs.readFileSync(full, 'utf8');
      if (text.includes(marker)) {
        return full;
      }
    }
  }
  return null;
}

const found = scan(root);
if (!found) {
  console.error(`marker not found in ${root}: ${marker}`);
  process.exit(2);
}
console.log(found);
'@

    Write-Info "probing OpenClaw container memory mount ($Label)"
    & docker @composeArgs exec -T `
        -e "MAIN_COMPUTER_DIRECT_MEMORY_MARKER=$Marker" `
        openclaw-gateway node -e $memoryProbeJs
    if ($LASTEXITCODE -ne 0) {
        throw "OpenClaw container could not read the direct memory marker during $Label."
    }
}


function Invoke-HighFidelityMemoryExtract([string]$ExtractPath, [string]$WorkspaceDir, [string]$OutputDir) {
    if (-not (Test-Path $ExtractPath)) {
        throw "OpenClaw persistence extractor not found: $ExtractPath"
    }

    if ([string]::IsNullOrWhiteSpace($OutputDir)) {
        $OutputDir = Join-Path $StateRoot "exports"
    }
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $jsonOut = Join-Path $OutputDir "openclaw-persistence-$stamp.json"
    $jsonlOut = Join-Path $OutputDir "openclaw-persistence-$stamp.jsonl"
    $markdownOut = Join-Path $OutputDir "openclaw-persistence-$stamp.md"

    Write-Info "extracting high-fidelity OpenClaw Markdown persistence"
    $extractArgs = @(
        $ExtractPath,
        "--memory-root", $WorkspaceDir,
        "--out", $jsonOut,
        "--jsonl-out", $jsonlOut,
        "--markdown-out", $markdownOut,
        "--summary-json"
    )
    & python @extractArgs
    if ($LASTEXITCODE -ne 0) {
        throw "OpenClaw persistence extraction failed with exit code $LASTEXITCODE"
    }

    Write-Info "high-fidelity OpenClaw persistence export written to $OutputDir"
    return $jsonOut
}

function ConvertTo-OpenClawModelEntries($OllamaTags, [int]$ContextWindow, [int]$MaxTokens, [int]$OllamaNumPredict) {
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
            contextWindow = $ContextWindow
            maxTokens = $MaxTokens
            params = [ordered]@{
                keep_alive = "15m"
                num_ctx = $ContextWindow
                num_predict = $OllamaNumPredict
                temperature = 0
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
if ($AgentId -notmatch '^[A-Za-z0-9._-]+$') {
    throw "AgentId contains unsupported characters for this smoke helper: $AgentId"
}
if ($SmokeTimeoutSeconds -lt 1) {
    throw "SmokeTimeoutSeconds must be at least 1."
}
if ($ContextWindow -lt 1024) {
    throw "ContextWindow must be at least 1024."
}
if ($MaxTokens -lt 1) {
    throw "MaxTokens must be at least 1."
}
if ($OllamaNumPredict -lt 1) {
    throw "OllamaNumPredict must be at least 1."
}
$providerTimeoutSeconds = [Math]::Max($SmokeTimeoutSeconds, 300)

Write-Info "checking Docker"
& docker version | Out-Null

$hostOllamaUrl = "http://127.0.0.1:11434"
$containerOllamaUrl = "http://host.docker.internal:11434"
$containerGatewayPort = 18789
Write-Info "checking host Ollama at $hostOllamaUrl/api/tags"
$ollamaTags = Invoke-JsonGet "$hostOllamaUrl/api/tags"
$modelEntries = @(ConvertTo-OpenClawModelEntries $ollamaTags $ContextWindow $MaxTokens $OllamaNumPredict)
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
                timeoutSeconds = $providerTimeoutSeconds
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
    "OPENCLAW_WORKSPACE=$workspaceDir",
    "MAIN_COMPUTER_OPENCLAW_BASE_URL=http://127.0.0.1:$Port",
    "MAIN_COMPUTER_OPENCLAW_TOKEN=$GatewayToken",
    "MAIN_COMPUTER_OPENCLAW_AGENT_ID=$AgentId",
    "MAIN_COMPUTER_OPENCLAW_BACKEND_MODEL=ollama/$Model",
    "MAIN_COMPUTER_OPENCLAW_CONTEXT_WINDOW=$ContextWindow",
    "MAIN_COMPUTER_OPENCLAW_MAX_TOKENS=$MaxTokens",
    "MAIN_COMPUTER_OPENCLAW_OLLAMA_NUM_PREDICT=$OllamaNumPredict"
)
Set-Content -Path $runtimeEnvPath -Value $envLines -Encoding UTF8

$env:OPENCLAW_IMAGE = $Image
$env:OPENCLAW_GATEWAY_HOST = "127.0.0.1"
$env:OPENCLAW_GATEWAY_PORT = [string]$Port
$env:OPENCLAW_GATEWAY_TOKEN = $GatewayToken
$env:OPENCLAW_CONFIG_DIR = $configDir
$env:OPENCLAW_WORKSPACE_DIR = $workspaceDir
$env:OPENCLAW_AUTH_PROFILE_SECRET_DIR = $authSecretDir
$env:OPENCLAW_WORKSPACE = $workspaceDir

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

$probeJs = @"
const model = process.env.MAIN_COMPUTER_PROBE_MODEL;
const tagsUrl = process.env.MAIN_COMPUTER_PROBE_OLLAMA_TAGS_URL;
fetch(tagsUrl)
  .then(async (response) => {
    const payload = await response.json();
    const names = (payload.models || []).map((entry) => entry.name).filter(Boolean);
    for (const name of names) {
      console.log(name);
    }
    if (!names.includes(model)) {
      console.error(model);
      process.exit(2);
    }
  })
  .catch((error) => {
    console.error(error && error.message ? error.message : error);
    process.exit(1);
  });
"@

Write-Info "probing host Ollama from inside the OpenClaw container"
$probeTagsUrl = "$containerOllamaUrl/api/tags"
& docker @composeArgs exec -T `
    -e "MAIN_COMPUTER_PROBE_MODEL=$Model" `
    -e "MAIN_COMPUTER_PROBE_OLLAMA_TAGS_URL=$probeTagsUrl" `
    openclaw-gateway node -e $probeJs
if ($LASTEXITCODE -ne 0) {
    throw "OpenClaw container could not confirm model '$Model' through $probeTagsUrl."
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
$env:MAIN_COMPUTER_OPENCLAW_AGENT_ID = $AgentId
$env:MAIN_COMPUTER_OPENCLAW_BACKEND_MODEL = "ollama/$Model"

$smokePath = Join-Path $repoRoot "scripts\smoke_openclaw_persistence.py"
if ((-not $NoSmoke) -and (Test-Path $smokePath)) {
    try {
        $directMarker = Invoke-DirectMemorySmoke $smokePath $workspaceDir
        Invoke-ContainerMemoryProbe $directMarker "before restart"

        if (-not $SkipRestartProof) {
            Write-Info "restarting OpenClaw container to prove memory survives container restart"
            & docker @composeArgs restart openclaw-gateway
            if ($LASTEXITCODE -ne 0) {
                Show-DockerDiagnostics
                exit $LASTEXITCODE
            }
            Wait-HttpOk "$baseUrl/healthz" 120
            Invoke-ContainerMemoryProbe $directMarker "after restart"
        }

        if ($AgentSmoke -or $FullSmoke) {
            Write-Info "running optional /v1/responses agent smoke through Docker OpenClaw"
            $smokeArgs = @(
                $smokePath,
                "--agent-id", $AgentId,
                "--backend-model", "ollama/$Model",
                "--memory-root", $workspaceDir,
                "--timeout", ([string]$SmokeTimeoutSeconds),
                "--max-output-tokens", ([string]$OllamaNumPredict),
                "--json"
            )
            if (-not $FullSmoke) {
                $smokeArgs += "--skip-recall-turns"
            }
            & python @smokeArgs
            if ($LASTEXITCODE -ne 0) {
                Show-DockerDiagnostics
                exit $LASTEXITCODE
            }
        } else {
            Write-Info "skipping /v1/responses agent smoke by default; pass -AgentSmoke or -FullSmoke to run it."
        }
    } catch {
        Show-DockerDiagnostics
        throw
    }
} elseif (-not (Test-Path $smokePath)) {
    Write-Info "persistence smoke script not found; skipping optional smoke: $smokePath"
}

$extractPath = Join-Path $repoRoot "scripts\extract_openclaw_persistence.py"
if ($ExtractMemory) {
    [void](Invoke-HighFidelityMemoryExtract $extractPath $workspaceDir $ExtractOutDir)
}

Write-Host ""
Write-Host "Docker OpenClaw is ready for Main Computer persistence work."
Write-Host "Gateway URL: $baseUrl"
Write-Host "Agent id: $AgentId"
Write-Host "Backend model: ollama/$Model"
Write-Host "Workspace: $workspaceDir"
Write-Host ""
Write-Host "For this PowerShell session:"
Write-Host "`$env:MAIN_COMPUTER_OPENCLAW_BASE_URL = `"$baseUrl`""
Write-Host "`$env:MAIN_COMPUTER_OPENCLAW_TOKEN = `"$GatewayToken`""
Write-Host "python scripts\smoke_openclaw_persistence.py --direct-memory --memory-root `"$workspaceDir`" --json"
Write-Host ""
Write-Host "High-fidelity persistence extraction:"
Write-Host "python scripts\extract_openclaw_persistence.py --memory-root `"$workspaceDir`" --out `"$StateRoot\exports\openclaw-persistence.json`" --jsonl-out `"$StateRoot\exports\openclaw-persistence.jsonl`" --markdown-out `"$StateRoot\exports\openclaw-persistence.md`" --summary-json"
Write-Host ""
Write-Host "Optional one-shot extraction from the helper:"
Write-Host ".\scripts\start_openclaw_docker_for_ollama.ps1 -Model $Model -Port $Port -NoSmoke -ExtractMemory"
Write-Host ""
Write-Host "Optional agent smoke, after direct memory is proven:"
Write-Host ".\scripts\start_openclaw_docker_for_ollama.ps1 -Model $Model -Port $Port -AgentSmoke"
