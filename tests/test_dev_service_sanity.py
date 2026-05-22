from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "dev-diagnosis.py"


def load_module():
    spec = importlib.util.spec_from_file_location("dev_diagnosis_for_tests", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_static_contract_matches_current_dev_ports_and_heartbeat_rules() -> None:
    module = load_module()

    roles = {role.key: role for role in module.ROLES}

    assert module.DEFAULT_PORTS["viewport"] == 8765
    assert module.DEFAULT_PORTS["heartbeat"] == 8766
    assert roles["viewport"].port == 8765
    assert roles["viewport"].url == "http://127.0.0.1:8765/api/path-mounts"
    assert roles["heartbeat"].port == 8766
    assert roles["heartbeat"].url == "http://127.0.0.1:8766/api/heartbeat/status"
    assert roles["viewport"].docker_service == "main-computer"
    assert roles["heartbeat"].docker_service is None


def test_compose_port_parser_honors_main_computer_docker_viewport_env_override() -> None:
    compose_text = (PROJECT_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

    assert "${MAIN_COMPUTER_DOCKER_VIEWPORT_PORT:-18765}:8765" in compose_text
    assert '"8765:8765"' not in compose_text


def test_findings_flag_docker_viewport_when_frontend_heartbeat_urls_are_unreachable() -> None:
    module = load_module()
    role = next(role for role in module.ROLES if role.key == "viewport")
    probe = module.ProbeResult(ok=True, status="up")
    docker_record = {
        "State": "running",
        "Publishers": [
            {"PublishedPort": 18765, "TargetPort": 8765, "URL": "0.0.0.0"},
        ],
    }

    resolved = module.resolve_runtime_roles((role,), {"main-computer": docker_record})[0]
    runtime = module.classify_runtime(
        role=resolved,
        probe=probe,
        listeners=[],
        docker_record=docker_record,
        matching_processes=[],
    )

    assert resolved.port == 18765
    assert resolved.url == "http://127.0.0.1:18765/api/path-mounts"
    assert runtime == "DOCKER"


def test_no_active_viewport_is_informational_unless_required() -> None:
    module = load_module()
    role = next(role for role in module.ROLES if role.key == "viewport")
    probe = module.ProbeResult(ok=False, status="connection refused")

    runtime = module.classify_runtime(
        role=role,
        probe=probe,
        listeners=[],
        docker_record=None,
        matching_processes=[],
    )

    assert runtime == "NOT FOUND"
