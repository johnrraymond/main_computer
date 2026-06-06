from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_dev_verify():
    spec = importlib.util.spec_from_file_location("dev_diagnosis", ROOT / "tools" / "dev-diagnosis.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_expected_energy_chain_id_uses_project_default_and_app_env() -> None:
    dev_verify = load_dev_verify()

    compose_config = {
        "services": {
            "main-computer": {
                "environment": {
                    "MAIN_COMPUTER_ENERGY_CHAIN_ID": "42424242",
                }
            },
        }
    }

    expected, sources = dev_verify.expected_energy_chain_id(ROOT, compose_config)

    assert expected == 42424242
    assert any("DEFAULT_ENERGY_CHAIN_ID" in source for source in sources)
    assert any("main-computer" in source and "42424242" in source for source in sources)


def test_eth_chain_id_probe_accepts_expected_chain(monkeypatch) -> None:
    dev_verify = load_dev_verify()

    def fake_http_post_json(url, payload, timeout):
        return {"jsonrpc": "2.0", "id": 1, "result": "0x28757b2"}, 200, None, "test"

    monkeypatch.setattr(dev_verify, "http_post_json", fake_http_post_json)

    result = dev_verify.eth_chain_id_probe("http://127.0.0.1:8545", 42424242, 0.1)

    assert result.ok
    assert "expected chain 42424242" in result.status
    assert result.detail["eth_chainId_decimal"] == 42424242


def test_local_listener_without_process_command_is_host_local() -> None:
    dev_verify = load_dev_verify()
    role = next(role for role in dev_verify.ROLES if role.key == "viewport")
    probe = dev_verify.ProbeResult(ok=True, status="up")
    listeners = [{"address": "127.0.0.1", "port": 8765, "pid": 12345}]

    runtime = dev_verify.classify_runtime(
        role=role,
        probe=probe,
        listeners=listeners,
        docker_record=None,
        matching_processes=[],
    )

    assert runtime == "HOST LOCAL"


def test_anvil_log_parser_reads_chain_id_and_bind_address() -> None:
    dev_verify = load_dev_verify()
    log_text = """
dev-chain-1  | Chain ID
dev-chain-1  | ==================
dev-chain-1  |
dev-chain-1  | 42424242
dev-chain-1  |
dev-chain-1  | Listening on 0.0.0.0:8545
"""

    info = dev_verify.parse_anvil_logs(log_text)

    assert info["chain_id"] == 42424242
    assert info["listening_on"] == "0.0.0.0:8545"


def test_gitea_role_is_declared_as_single_shared_standalone_stack() -> None:
    dev_verify = load_dev_verify()

    roles = {role.key: role for role in dev_verify.ROLES}

    role = roles["gitea"]
    assert role.label == "shared Gitea"
    assert role.port == 3000
    assert role.url == "http://127.0.0.1:3000/"
    assert role.docker_service is None
    assert any("docker-compose.gitea.yml service gitea" in item for item in role.declared)

def test_viewport_role_uses_docker_published_host_port_when_container_is_running() -> None:
    dev_verify = load_dev_verify()
    roles = dev_verify.resolve_runtime_roles(
        dev_verify.ROLES,
        {
            "main-computer": {
                "State": "running",
                "Name": "main-computer-dev-main-computer-1",
                "Publishers": [
                    {"PublishedPort": 18765, "TargetPort": 8765, "Protocol": "tcp"},
                ],
            }
        },
    )

    role = next(role for role in roles if role.key == "viewport")

    assert role.port == 18765
    assert role.url == "http://127.0.0.1:18765/api/path-mounts"
    assert any("host port 18765" in item for item in role.declared)


def test_viewport_role_keeps_host_local_port_without_docker_publish() -> None:
    dev_verify = load_dev_verify()
    roles = dev_verify.resolve_runtime_roles(dev_verify.ROLES, {})

    role = next(role for role in roles if role.key == "viewport")

    assert role.port == 8765
    assert role.url == "http://127.0.0.1:8765/api/path-mounts"
    assert role.probe_kind == "path_mounts"

def test_path_mounts_probe_reports_runtime_path_mode(monkeypatch) -> None:
    dev_verify = load_dev_verify()

    def fake_http_get_json(url, timeout):
        return dev_verify.ProbeResult(
            ok=True,
            status="up",
            http_status=200,
            data={
                "ok": True,
                "path_mode": "local",
                "host_os": "auto",
                "enabled": False,
                "count": 0,
            },
            detail={"http_status": 200},
        )

    monkeypatch.setattr(dev_verify, "http_get_json", fake_http_get_json)

    result = dev_verify.path_mounts_probe("http://127.0.0.1:8765/api/path-mounts", 0.1)

    assert result.ok
    assert "path_mode=local" in result.status
    assert result.detail["path_mode"] == "local"
    assert result.detail["host_os"] == "auto"
    assert result.detail["enabled"] is False
