from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_openclaw_docker_ollama_artifacts_exist() -> None:
    expected = [
        "deploy/openclaw-docker/docker-compose.yml",
        "deploy/openclaw-docker/README.md",
        "scripts/start_openclaw_docker_for_ollama.ps1",
        "scripts/extract_openclaw_persistence.py",
        "scripts/apply_openclaw_persistence.py",
        "scripts/smoke_openclaw_persistence_pushback.py",
    ]
    for relative in expected:
        assert (REPO_ROOT / relative).is_file(), relative


def test_openclaw_docker_compose_routes_container_to_host_ollama() -> None:
    compose = (REPO_ROOT / "deploy/openclaw-docker/docker-compose.yml").read_text(encoding="utf-8")
    assert "ghcr.io/openclaw/openclaw:latest" in compose
    assert '"${OPENCLAW_GATEWAY_HOST:-127.0.0.1}:${OPENCLAW_GATEWAY_PORT:-18789}:18789"' in compose
    assert "host.docker.internal:host-gateway" in compose
    assert "OPENCLAW_GATEWAY_TOKEN" in compose
    assert "OLLAMA_API_KEY: ollama-local" in compose
    assert "target: /home/node/.openclaw" in compose
    assert "target: /home/node/.openclaw/workspace" in compose
    assert "target: /home/node/.config/openclaw" in compose
    assert "/v1" not in compose


def test_openclaw_docker_runner_writes_gateway_config_for_host_ollama() -> None:
    script = (REPO_ROOT / "scripts/start_openclaw_docker_for_ollama.ps1").read_text(encoding="utf-8")

    assert "http://127.0.0.1:11434" in script
    assert '[string]$AgentId = "main"' in script
    assert "http://host.docker.internal:11434" in script
    assert "[ValidateSet(\"auto\", \"docker\", \"podman\")]" in script
    assert "[string]$ContainerRuntime = \"auto\"" in script
    assert "MAIN_COMPUTER_CONTAINER_RUNTIME" in script
    assert "MAIN_COMPUTER_CONTAINER_COMMAND" in script
    assert "MAIN_COMPUTER_CONTAINER_COMPOSE_COMMAND" in script
    assert "Resolve-OpenClawContainerRuntime" in script
    assert "$containerRuntimeName -eq \"podman\"" in script
    assert "http://host.containers.internal:11434" in script
    assert "OPENCLAW_CONTAINER_OLLAMA_URL" in script
    assert "/api/tags" in script
    assert 'api = "ollama"' in script
    assert 'apiKey = "ollama-local"' in script
    assert 'baseUrl = $containerOllamaUrl' in script
    assert '$modelEntries = @(ConvertTo-OpenClawModelEntries $ollamaTags $ContextWindow $MaxTokens $OllamaNumPredict)' in script
    assert "New-Object System.Collections.ArrayList" in script
    assert "return @($models.ToArray())" in script
    assert "input = @(\"text\")" in script
    assert "cacheRead = 0" in script
    assert "keep_alive = \"15m\"" in script
    assert "[int]$ContextWindow = 8192" in script
    assert "[int]$MaxTokens = 512" in script
    assert "[int]$OllamaNumPredict = 128" in script
    assert "contextWindow = $ContextWindow" in script
    assert "maxTokens = $MaxTokens" in script
    assert "num_ctx = $ContextWindow" in script
    assert "num_predict = $OllamaNumPredict" in script
    assert "temperature = 0" in script
    assert 'models = @($modelEntries)' in script
    assert 'primary = "ollama/$Model"' in script
    assert 'timeoutSeconds = $providerTimeoutSeconds' in script
    assert "responses" in script
    assert "chatCompletions" in script
    assert "process.env.MAIN_COMPUTER_PROBE_MODEL" in script
    assert "process.env.MAIN_COMPUTER_PROBE_OLLAMA_TAGS_URL" in script
    assert "MAIN_COMPUTER_OPENCLAW_AGENT_ID=$AgentId" in script
    assert "OPENCLAW_WORKSPACE=$workspaceDir" in script
    assert "$env:OPENCLAW_WORKSPACE = $workspaceDir" in script
    assert "Invoke-DirectMemorySmoke" in script
    assert "Invoke-ContainerMemoryProbe" in script
    assert "$jsonStart = $directText.IndexOf(\"{\")" in script
    assert "MAIN_COMPUTER_DIRECT_MEMORY_MARKER" in script
    assert '"--direct-memory"' in script
    assert 'Invoke-ContainerRuntimeCommand $composeCommand ($composeArgs + @("restart", "openclaw-gateway"))' in script
    assert "if ($AgentSmoke -or $FullSmoke)" in script
    assert "[int]$SmokeTimeoutSeconds = 300" in script
    assert "[switch]$AgentSmoke" in script
    assert "[switch]$FullSmoke" in script
    assert "[switch]$SkipRestartProof" in script
    assert "[switch]$ExtractMemory" in script
    assert "[string]$ExtractOutDir" in script
    assert "[string]$ApplyMemoryExport" in script
    assert "[switch]$ApplyMemoryDryRun" in script
    assert "[switch]$ApplyMemoryForce" in script
    assert "[string]$ApplyMemoryBackupDir" in script
    assert "[switch]$PushbackSmoke" in script
    assert "[string]$PushbackSmokeOutDir" in script
    assert "Invoke-HighFidelityMemoryExtract" in script
    assert "extract_openclaw_persistence.py" in script
    assert "Invoke-OpenClawMemoryApply" in script
    assert "Invoke-OpenClawPushbackSmoke" in script
    assert "apply_openclaw_persistence.py" in script
    assert "smoke_openclaw_persistence_pushback.py" in script
    assert '"--restart-container"' in script
    assert '"--container", "main-computer-openclaw-gateway"' in script
    assert '"--jsonl-out", $jsonlOut' in script
    assert '"--markdown-out", $markdownOut' in script
    assert '"--verify-after"' in script
    assert '"--skip-current-sha-check"' in script
    assert "restarting OpenClaw container so the runtime sees the pushed-back memory files" in script
    assert '"--memory-root", $WorkspaceDir' in script
    assert '"--memory-root", $workspaceDir' in script
    assert '"--timeout", ([string]$SmokeTimeoutSeconds)' in script
    assert '"--max-output-tokens", ([string]$OllamaNumPredict)' in script
    assert "--skip-recall-turns" in script
    assert "if (-not $FullSmoke)" in script
    assert '"-e", "MAIN_COMPUTER_PROBE_MODEL=$Model"' in script
    assert '"-e", "MAIN_COMPUTER_PROBE_OLLAMA_TAGS_URL=$probeTagsUrl"' in script
    assert '"openclaw-gateway", "node", "-e", $probeJs' in script
    assert "const model = $modelJson;" not in script
    assert '"--agent-id", $AgentId' in script
    assert '"--backend-model", "ollama/$Model"' in script
    assert "[System.Security.Cryptography.RandomNumberGenerator]::Create()" in script
    assert ".GetBytes($bytes)" in script
    assert "RandomNumberGenerator]::Fill" not in script
    assert "$containerGatewayPort = 18789" in script
    assert "port = $containerGatewayPort" in script
    assert "port = $Port" not in script
    assert "Show-DockerDiagnostics" in script
    assert "& docker @composeArgs" not in script
    assert 'Invoke-ContainerRuntimeCommand $composeCommand ($composeArgs + @("logs", "--tail", "120", "openclaw-gateway"))' in script

    assert "$memoryProbeJs = @'" in script
    memory_probe = script.split("function Invoke-ContainerMemoryProbe", 1)[1].split("function ConvertTo-OpenClawModelEntries", 1)[0]
    assert "$memoryProbeJs = @\"" not in memory_probe
    assert "`${root}" not in memory_probe

    # Guard against the original failure mode: using the container's loopback or
    # Ollama's OpenAI-compatible endpoint for OpenClaw's native Ollama provider.
    assert 'baseUrl = "http://127.0.0.1:11434"' not in script
    assert re.search(r"baseUrl\s*=.*11434/v1", script) is None



def test_openclaw_persistence_smoke_uses_main_agent_by_default() -> None:
    smoke = (REPO_ROOT / "scripts/smoke_openclaw_persistence.py").read_text(encoding="utf-8")
    assert 'DEFAULT_AGENT_ID = "main"' in smoke
    assert 'DEFAULT_AGENT_ID = "default"' not in smoke


def test_openclaw_persistence_smoke_default_timeout_allows_large_local_models() -> None:
    smoke = (REPO_ROOT / "scripts/smoke_openclaw_persistence.py").read_text(encoding="utf-8")
    assert "DEFAULT_TIMEOUT_S = 300.0" in smoke
    assert "DEFAULT_TIMEOUT_S = 60.0" not in smoke


def test_openclaw_docker_readme_documents_generated_state_outside_repo() -> None:
    readme = (REPO_ROOT / "deploy/openclaw-docker/README.md").read_text(encoding="utf-8")
    assert "host.docker.internal:11434" in readme
    assert "%LOCALAPPDATA%\\MainComputer\\openclaw-docker" in readme
    assert "models.providers.ollama.models" in readme
    assert "smoke_openclaw_persistence.py" in readme
    assert "host-published Gateway port" in readme
    assert "container OpenClaw still listens on `18789`" in readme
    assert "direct memory smoke" in readme
    assert "-AgentSmoke" in readme
    assert "High-fidelity memory extraction" in readme
    assert "extract_openclaw_persistence.py" in readme
    assert "SHA-256 hashes" in readme
    assert "High-fidelity memory pushback" in readme
    assert "Automated pushback smoke" in readme
    assert "smoke_openclaw_persistence_pushback.py" in readme
    assert "-PushbackSmoke" in readme
    assert "apply_openclaw_persistence.py" in readme
    assert "-ApplyMemoryExport" in readme
    assert "expected-current" in readme

def test_openclaw_persistence_smoke_can_run_single_write_turn() -> None:
    smoke = (REPO_ROOT / "scripts/smoke_openclaw_persistence.py").read_text(encoding="utf-8")
    assert "DEFAULT_MAX_OUTPUT_TOKENS = 128" in smoke
    assert "--max-output-tokens" in smoke
    assert "--skip-recall-turns" in smoke
    assert '"max_output_tokens"' in smoke
    assert "ok = bool(durable_found) if skip_recall_turns else" in smoke
    assert '"same_session_checked": not skip_recall_turns' in smoke


def test_openclaw_persistence_smoke_can_run_direct_memory_mode() -> None:
    smoke = (REPO_ROOT / "scripts/smoke_openclaw_persistence.py").read_text(encoding="utf-8")
    assert "--direct-memory" in smoke
    assert "def write_direct_memory_fact" in smoke
    assert "def run_direct_memory_smoke" in smoke
    assert "openclaw-persistence-direct-memory" in smoke
    assert "Main Computer persistence smoke" in smoke
