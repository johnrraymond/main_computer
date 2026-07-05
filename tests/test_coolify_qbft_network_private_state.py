from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "coolify_qbft_network.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("coolify_qbft_network_private_state", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_private_state(tmp_path: Path, *, duplicate_cross_host_port: bool = False) -> Path:
    validator_2_port = 30311 if duplicate_cross_host_port else 30312
    state_path = tmp_path / "main_computer.private.yaml"
    state_path.write_text(
        f"""
coolify:
  project_name: Main Computer
  hosts:
    A:
      name: coolify-a
      public_ip: 198.51.100.10
      url: http://198.51.100.10:8000/
      api_token: secret-a
      server_uuid: server-a
      destination_uuid: destination-a
    B:
      name: coolify-b
      public_ip: 198.51.100.11
      url: http://198.51.100.11:8000/
      api_token: secret-b
      server_uuid: server-b
      destination_uuid: destination-b

networks:
  testnet:
    display_name: Main Computer Testnet
    kind: testnet
    chain_id: 42424241
    remote_coolify_hosts: [A, B]
    qbft:
      instances:
        validator-1:
          coolify_host: A
          roles: [validator]
          p2p_host_port: 30311
        validator-2:
          coolify_host: B
          roles: [validator]
          p2p_host_port: {validator_2_port}
        rpc-1:
          coolify_host: A
          roles: [rpc]
          rpc_host_port: 30010
          p2p_host_port: 30321
""".lstrip(),
        encoding="utf-8",
    )
    return state_path


def test_private_state_qbft_instances_drive_testnet_plan(tmp_path: Path) -> None:
    module = _load_module()
    state_path = write_private_state(tmp_path)

    plan = module.build_plan("testnet", private_state_path=state_path)
    services = {service.id: service for service in plan.services}

    assert {host.id for host in plan.hosts} == {"a", "b"}
    assert services["validator-1"].host == "a"
    assert services["validator-1"].role == "validator"
    assert services["validator-1"].roles == ("validator",)
    assert services["validator-1"].rpc_host_port is None
    assert services["validator-1"].p2p_host_port == 30311

    assert services["validator-2"].host == "b"
    assert services["validator-2"].p2p_host_port == 30312

    assert services["rpc-1"].host == "a"
    assert services["rpc-1"].role == "rpc"
    assert services["rpc-1"].roles == ("rpc",)
    assert services["rpc-1"].rpc_host_port == 30010
    assert module.rpc_target_service(plan).id == "rpc-1"

    rendered = plan.to_dict()
    assert rendered["hosts"][0]["api_token"] == "<redacted>"
    assert rendered["operator_checks"]["first_rpc_url"] == "http://127.0.0.1:30010"


def test_private_state_multi_host_compose_groups_instances_by_coolify_host(tmp_path: Path) -> None:
    module = _load_module()
    state_path = write_private_state(tmp_path)

    plan = module.build_plan("testnet", private_state_path=state_path)
    compose_a = module.render_compose_for_host(plan, "A", include_bootstrap=False)

    assert "validator-1:" in compose_a
    assert "rpc-1:" in compose_a
    assert "validator-2:" not in compose_a
    assert "\"127.0.0.1:30010:8545\"" in compose_a
    assert "\"0.0.0.0:30311:30303\"" in compose_a


def test_private_state_allows_same_host_port_on_different_coolify_hosts(tmp_path: Path) -> None:
    module = _load_module()
    state_path = write_private_state(tmp_path, duplicate_cross_host_port=True)

    plan = module.build_plan("testnet", private_state_path=state_path)

    services = {service.id: service for service in plan.services}
    assert services["validator-1"].p2p_host_port == services["validator-2"].p2p_host_port == 30311
    assert services["validator-1"].host != services["validator-2"].host
