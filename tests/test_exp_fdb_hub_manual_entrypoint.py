from __future__ import annotations

from pathlib import Path


def test_exp_fdb_hub_entrypoint_is_manual_and_declares_fdb_options() -> None:
    repo = Path(__file__).resolve().parents[1]
    entrypoint = (repo / "exp-fdb-hub.py").read_text(encoding="utf-8")
    module = (repo / "main_computer" / "exp_fdb_hub.py").read_text(encoding="utf-8")

    assert "main_computer.exp_fdb_hub" in entrypoint
    assert "Manual-only" in module
    assert "--cluster-file" in module
    assert "--namespace" in module
    assert "--no-fdb-autostart" in module
    assert "main-computer-foundationdb-smoke" in module
    assert "smoke_foundationdb_credit_ledger_primitives.py" in module
    assert "--keep-container" in module
    assert "--reuse-container" in module
    assert "-ports" in module
    assert "--docker" in module
    assert "--docker-compose-file" in module
    assert "--docker-ports" in module
    assert "--nodes" in module
    assert "--worktime" in module
    assert "--funded" in module
    assert "--request-startup-mode" in module
    assert "--request-startup-spread-seconds" in module
    assert "--lease-seconds" in module
    assert "--warm" in module
    assert "--b2bfailures" in module
    assert "--forced-alive" in module
    assert "--lab-execution" in module
    assert "--http-timeout-seconds" in module
    assert "ExperimentalFoundationDbHubServerHandler" in module
    assert "flush=True" in module
    assert "FoundationDB Docker cluster" in module


def test_standard_hub_module_does_not_import_experimental_fdb_hub() -> None:
    repo = Path(__file__).resolve().parents[1]
    hub_text = (repo / "main_computer" / "hub.py").read_text(encoding="utf-8")
    cli_text = (repo / "main_computer" / "cli.py").read_text(encoding="utf-8")

    assert "exp_fdb_hub" not in hub_text
    assert "exp_fdb_hub" not in cli_text


def test_exp_fdb_hub_launcher_coordinates_ports_with_docker_lab() -> None:
    repo = Path(__file__).resolve().parents[1]
    module = (repo / "main_computer" / "exp_fdb_hub.py").read_text(encoding="utf-8")

    assert "parse_ports" in module
    assert "docker_hub_base_urls" in module
    assert "HUB_BASE_URLS" in module
    assert "LAB_NODES" in module
    assert "LAB_WORKTIME" in module
    assert "LAB_FUNDED" in module
    assert "LAB_REQUEST_STARTUP_MODE" in module
    assert "LAB_REQUEST_STARTUP_SPREAD_SECONDS" in module
    assert "LEASE_SECONDS" in module
    assert "LAB_EXECUTION_MODE" in module
    assert "LAB_WARM" in module
    assert "B2B_FAILURES" in module
    assert "FORCED_ALIVE_SECONDS" in module
    assert "HTTP_TIMEOUT_SECONDS" in module
    assert "docker-compose.worker-lab.yml" in module
    assert "docker-compose.dev.yml" not in module
    assert "\"main-computer\"" not in module
    assert "May include intentionally dead ports" in module


def test_exp_fdb_hub_uses_lightweight_scheduler_lab_docker_stack() -> None:
    repo = Path(__file__).resolve().parents[1]
    compose = (repo / "deploy" / "scheduler-lab" / "docker-compose.worker-lab.yml").read_text(encoding="utf-8")
    dockerfile = (repo / "tools" / "scheduler_lab" / "Dockerfile.worker-lab").read_text(encoding="utf-8")
    dockerignore = (repo / "tools" / ".dockerignore").read_text(encoding="utf-8")

    assert "context: ../../tools" in compose
    assert "worker-lab" in compose
    assert "LAB_OUTPUT_DIR_HOST" in compose
    assert "LAB_NODES" in compose
    assert "LAB_WORKTIME" in compose
    assert "LAB_FUNDED" in compose
    assert "LAB_REQUEST_STARTUP_MODE" in compose
    assert "LAB_REQUEST_STARTUP_SPREAD_SECONDS" in compose
    assert "LEASE_SECONDS" in compose
    assert "LAB_EXECUTION_MODE" in compose
    assert "LAB_WARM" in compose
    assert "B2B_FAILURES" in compose
    assert "FORCED_ALIVE_SECONDS" in compose
    assert "HTTP_TIMEOUT_SECONDS" in compose
    assert "docker-compose.dev.yml" not in compose
    assert "playwright" not in dockerfile.lower()
    assert "chromium" not in dockerfile.lower()
    assert "pip install" not in dockerfile.lower()
    assert "!scheduler_lab/**" in dockerignore
