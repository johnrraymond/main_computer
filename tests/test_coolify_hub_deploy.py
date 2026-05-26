from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERIGNORE = ROOT / ".dockerignore"
HUB_DIR = ROOT / "deploy" / "coolify" / "hub"
COMPOSE = HUB_DIR / "docker-compose.yml"
DOCKERFILE = HUB_DIR / "Dockerfile"
DOCKERFILE_DOCKERIGNORE = HUB_DIR / "Dockerfile.dockerignore"
ENV_EXAMPLE = HUB_DIR / ".env.example"
README = HUB_DIR / "README.md"


def test_coolify_hub_compose_uses_no_required_env_defaults() -> None:
    text = COMPOSE.read_text(encoding="utf-8")

    assert "${" in text
    assert ":?" not in text
    assert "MAIN_COMPUTER_HUB_ADMIN_TOKEN" not in text
    assert "MAIN_COMPUTER_HUB_WORKER_TOKEN" not in text
    assert "MAIN_COMPUTER_HUB_API_TOKEN" not in text


def test_coolify_hub_compose_keeps_only_container_facts_in_environment() -> None:
    text = COMPOSE.read_text(encoding="utf-8")

    assert "MAIN_COMPUTER_WORKSPACE: /workspace" in text
    assert "MAIN_COMPUTER_HUB_ROOT: /runtime/hub" in text
    assert "MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK" not in text
    assert "MAIN_COMPUTER_MODEL" not in text
    assert "MAIN_COMPUTER_HUB_CREDITS_PER_REQUEST" not in text


def test_coolify_hub_dockerfile_does_not_copy_whole_repo() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert "COPY . /workspace" not in text
    assert "COPY ./ /workspace" not in text
    assert "COPY pyproject.toml README.md /workspace/" in text
    assert "COPY main_computer /workspace/main_computer" in text


def test_coolify_hub_has_dockerfile_specific_context_allowlist() -> None:
    text = DOCKERFILE_DOCKERIGNORE.read_text(encoding="utf-8")

    assert text.splitlines()[0].startswith("# Dockerfile-specific")
    assert "*" in text.splitlines()
    assert "!pyproject.toml" in text
    assert "!README.md" in text
    assert "!main_computer/" in text
    assert "!main_computer/**" in text
    assert "tools/" in text
    assert "runtime/" in text
    assert "release_reports/" in text


def test_root_dockerignore_excludes_large_local_artifacts() -> None:
    text = DOCKERIGNORE.read_text(encoding="utf-8")

    assert "release_reports/" in text
    assert "diagnostics_output*/" in text
    assert "runtime/" in text
    assert "revision_control/" in text
    assert ".proto-dev/" in text
    assert ".main_computer_browser_profile/" in text
    assert "*.zip" in text


def test_coolify_hub_docs_make_env_file_optional_and_explain_slim_context() -> None:
    env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    assert "not required for local Docker testing" in env_text
    assert "no `.env` file is required for local Docker use" in readme
    assert "docker compose -f deploy/coolify/hub/docker-compose.yml up --build" in readme
    assert "does **not** copy the whole repository" in readme
    assert "Dockerfile.dockerignore" in readme
