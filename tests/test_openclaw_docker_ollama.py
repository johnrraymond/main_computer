from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_openclaw_docker_ollama_artifacts_exist() -> None:
    expected = [
        "deploy/openclaw-docker/docker-compose.yml",
        "deploy/openclaw-docker/README.md",
        "scripts/start_openclaw_docker_for_ollama.ps1",
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
    assert "http://host.docker.internal:11434" in script
    assert "/api/tags" in script
    assert 'api = "ollama"' in script
    assert 'apiKey = "ollama-local"' in script
    assert 'baseUrl = $containerOllamaUrl' in script
    assert '$modelEntries = @(ConvertTo-OpenClawModelEntries $ollamaTags)' in script
    assert "New-Object System.Collections.ArrayList" in script
    assert "return @($models.ToArray())" in script
    assert "input = @(\"text\")" in script
    assert "cacheRead = 0" in script
    assert "keep_alive = \"15m\"" in script
    assert 'models = @($modelEntries)' in script
    assert 'primary = "ollama/$Model"' in script
    assert "responses" in script
    assert "chatCompletions" in script
    assert "docker @composeArgs exec -T openclaw-gateway node -e" in script
    assert "--backend-model \"ollama/$Model\"" in script
    assert "[System.Security.Cryptography.RandomNumberGenerator]::Create()" in script
    assert ".GetBytes($bytes)" in script
    assert "RandomNumberGenerator]::Fill" not in script
    assert "$containerGatewayPort = 18789" in script
    assert "port = $containerGatewayPort" in script
    assert "port = $Port" not in script
    assert "Show-DockerDiagnostics" in script
    assert "docker @composeArgs logs --tail 120 openclaw-gateway" in script

    # Guard against the original failure mode: using the container's loopback or
    # Ollama's OpenAI-compatible endpoint for OpenClaw's native Ollama provider.
    assert 'baseUrl = "http://127.0.0.1:11434"' not in script
    assert re.search(r"baseUrl\s*=.*11434/v1", script) is None


def test_openclaw_docker_readme_documents_generated_state_outside_repo() -> None:
    readme = (REPO_ROOT / "deploy/openclaw-docker/README.md").read_text(encoding="utf-8")
    assert "host.docker.internal:11434" in readme
    assert "%LOCALAPPDATA%\\MainComputer\\openclaw-docker" in readme
    assert "models.providers.ollama.models" in readme
    assert "smoke_openclaw_persistence.py" in readme
    assert "host-published Gateway port" in readme
    assert "container OpenClaw still listens on `18789`" in readme
