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


class _FakeCoolifyClient:
    def __init__(self, module: Any, *, services: list[dict[str, Any]], details: dict[str, dict[str, Any]] | None = None) -> None:
        self.module = module
        self.services = services
        self.details = details or {}
        self.requests: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, payload: Any | None = None) -> Any:
        self.requests.append((method, path, payload))
        method = method.upper()
        if path == "/api/v1/version":
            return self.module.CoolifyResponse(True, 200, method, "http://fake-coolify", path, {"version": "fake"})
        if path == "/api/v1/services":
            return self.module.CoolifyResponse(True, 200, method, "http://fake-coolify", path, {"services": self.services})
        if path.startswith("/api/v1/services/"):
            uuid = path.split("/")[4]
            body = self.details.get(uuid)
            if body is None:
                return self.module.CoolifyResponse(False, 404, method, "http://fake-coolify", path, {"message": "missing"})
            return self.module.CoolifyResponse(True, 200, method, "http://fake-coolify", path, body)
        return self.module.CoolifyResponse(False, 404, method, "http://fake-coolify", path, {"message": "unknown"})


def test_discover_topology_checks_each_private_state_coolify_host_without_mutation(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_module()
    state_path = write_private_state(tmp_path)
    args = module.parse_args(
        [
            "discover-topology",
            "testnet",
            "--private-state",
            str(state_path),
            "--rpc-url",
            "https://rpc.example:8545",
        ]
    )
    plan = module.build_plan_from_args(args)

    service_name_a = module.project_service_name(plan, "a")
    clients = {
        "a": _FakeCoolifyClient(
            module,
            services=[{"uuid": "service-a", "name": service_name_a, "status": "running"}],
            details={
                "service-a": {
                    "docker_compose": """
services:
  validator-1:
    image: hyperledger/besu:latest
""".lstrip()
                }
            },
        ),
        "b": _FakeCoolifyClient(module, services=[]),
    }

    def fake_client_from_args(call_args: Any, call_plan: Any, *, host_id: str | None = None) -> tuple[Any, str, str]:
        assert host_id in clients
        return clients[str(host_id)], "token", f"fake:{host_id}"

    def fake_json_rpc(url: str, method: str, params: list[Any] | None = None, *, timeout_s: float = 8.0) -> Any:
        assert url == "https://rpc.example:8545"
        if method == "eth_chainId":
            return hex(plan.chain_id)
        if method == "eth_blockNumber":
            return "0x2a"
        if method == "net_peerCount":
            return "0x0"
        if method == "qbft_getValidatorsByBlockNumber":
            return ["0x1111111111111111111111111111111111111111"]
        raise AssertionError(method)

    monkeypatch.setattr(module, "coolify_client_from_args", fake_client_from_args)
    monkeypatch.setattr(module, "json_rpc", fake_json_rpc)

    result = module.discover_topology(plan, args)

    assert result["ok"] is True
    assert result["observed_deployed_instances"] == ["validator-1"]
    assert result["observed_missing_instances"] == ["rpc-1", "validator-2"]
    assert result["coolify_topology"]["hosts"]["a"]["found"] is True
    assert result["coolify_topology"]["hosts"]["b"]["found"] is False
    assert "body" not in result["coolify_topology"]["hosts"]["a"]["services_response"]
    assert "body" not in result["coolify_topology"]["hosts"]["a"]["version"]
    assert all("result" not in stage for stage in result["stages"])
    assert result["rpc_topology"]["chain_id_matches"] is True
    assert result["consensus_topology"]["validator_addresses"] == ["0x1111111111111111111111111111111111111111"]


def test_discover_topology_can_run_optional_hub_verification_as_separate_stage(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_module()
    state_path = write_private_state(tmp_path)
    args = module.parse_args(
        [
            "discover-topology",
            "testnet",
            "--private-state",
            str(state_path),
            "--rpc-url",
            "https://rpc.example:8545",
            "--verify-hub",
            "--hub-rpc-check",
            "skip",
            "--hub-health-check",
            "skip",
        ]
    )
    plan = module.build_plan_from_args(args)

    clients = {
        "a": _FakeCoolifyClient(module, services=[]),
        "b": _FakeCoolifyClient(module, services=[]),
    }

    def fake_client_from_args(call_args: Any, call_plan: Any, *, host_id: str | None = None) -> tuple[Any, str, str]:
        assert host_id in clients
        return clients[str(host_id)], "token", f"fake:{host_id}"

    def fake_json_rpc(url: str, method: str, params: list[Any] | None = None, *, timeout_s: float = 8.0) -> Any:
        if method == "eth_chainId":
            return hex(plan.chain_id)
        if method == "eth_blockNumber":
            return "0x2a"
        if method == "net_peerCount":
            return "0x0"
        if method == "qbft_getValidatorsByBlockNumber":
            return []
        raise AssertionError(method)

    class FakeHubModule:
        @staticmethod
        def parse_args(argv: list[str]) -> Any:
            return {"argv": argv}

        @staticmethod
        def verify(hub_args: Any) -> dict[str, Any]:
            return {"ok": True, "hub_args": hub_args}

    monkeypatch.setattr(module, "coolify_client_from_args", fake_client_from_args)
    monkeypatch.setattr(module, "json_rpc", fake_json_rpc)
    monkeypatch.setattr(module, "_load_hub_service_module", lambda: FakeHubModule)

    result = module.discover_topology(plan, args)

    assert result["ok"] is True
    assert result["hub_verification"]["skipped"] is False
    assert result["hub_verification"]["result"]["hub_args"]["argv"][0:2] == ["verify", "testnet"]
    assert "--verify-chain-rpc-url" in result["hub_verification"]["result"]["hub_args"]["argv"]
    assert any(stage["phase"] == "verify-hub" for stage in result["stages"])
