from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dev_docker_compose_defines_core_services() -> None:
    compose = (ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

    for service in (
        "main-computer:",
        "hub:",
        "hub-worker:",
        "ollama:",
        "ethereum-dev:",
        "executor-image:",
        "executor-smoke:",
    ):
        assert service in compose

    assert "docker/dev/app.Dockerfile" in compose
    assert "docker/executor/Dockerfile" in compose
    assert "MAIN_COMPUTER_EXECUTOR_ENABLED: \"0\"" in compose
    assert "MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL: http://ethereum-dev:8545" in compose
    assert compose.count('MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK: "1"') >= 3
    assert "git-server:" not in compose
    assert "git-server-prod:" not in compose
    assert "gitea/gitea:1.24" not in compose
    assert "profiles: [\"git\"]" not in compose
    assert "entrypoint:\n      - anvil" in compose
    assert "- --chain-id\n      - \"42424242\"" in compose


def test_gitea_has_standalone_shared_http_compose_stack() -> None:
    applications = (ROOT / "docker-compose.applications.yml").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.gitea.yml").read_text(encoding="utf-8")

    assert "\n  gitea:" not in applications
    assert "name: main-computer-gitea" in compose
    assert "\n  gitea:" in compose
    assert "gitea/gitea:${MAIN_COMPUTER_GITEA_IMAGE_TAG:-1.24}" in compose
    assert "GITEA__server__ROOT_URL: ${MAIN_COMPUTER_GITEA_ROOT_URL:-http://localhost:3000/}" in compose
    assert "GITEA__server__DISABLE_SSH: \"true\"" in compose
    assert "GITEA__server__START_SSH_SERVER: \"false\"" in compose
    assert "\"127.0.0.1:${MAIN_COMPUTER_GITEA_HTTP_PORT:-3000}:3000\"" in compose
    assert "MAIN_COMPUTER_GITEA_DATA_VOLUME:-main-computer-applications_gitea-data" in compose
    assert "2222" not in compose
    assert ":22" not in compose


def test_dev_executor_dockerfile_matches_current_executor_contract() -> None:
    dockerfile = (ROOT / "docker" / "executor" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:" in dockerfile
    assert "bash" in dockerfile
    assert "/inputs" in dockerfile
    assert "/outputs" in dockerfile
    assert "/workspace" in dockerfile
    assert "WORKDIR /workspace" in dockerfile
    assert "main-computer-exec run" in dockerfile
    assert "--cwd /workspace" in dockerfile
    assert "--timeout-ms 5000" in dockerfile
    assert "--artifact-dir /outputs" in dockerfile
    assert "main-computer-exec-ready" in dockerfile


def test_dev_app_dockerfile_runs_source_tree_directly() -> None:
    dockerfile = (ROOT / "docker" / "dev" / "app.Dockerfile").read_text(encoding="utf-8")

    assert "PYTHONPATH=/workspace" in dockerfile
    assert "COPY . /workspace" in dockerfile
    assert "main_computer.cli" in dockerfile
    assert "EXPOSE 8765 8767 8770 8771" in dockerfile


def test_export_includes_dev_docker_assets() -> None:
    script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

    assert "\".dockerignore\"" in script
    assert "\"docker-compose.dev.yml\"" in script
    assert "\"docker-compose.gitea.yml\"" in script
    assert "\"docker\"" in script


def test_readme_points_to_dev_docker_stack() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "## Dev Docker Stack" in readme
    assert "docker compose -f docker-compose.dev.yml up --build hub ollama ethereum-dev" in readme
    assert "docker compose -f docker-compose.gitea.yml up -d gitea" in readme
    assert "Ctrl+Shift+G" in readme
    assert "docker build -t main-computer-executor:latest -f docker/executor/Dockerfile ." in readme
