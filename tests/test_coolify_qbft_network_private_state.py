from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "coolify_qbft_network.py"
FAKE_PRIVATE_STATE_FIXTURE = ROOT / "tests" / "fixtures" / "fake-main-computer.private.yaml"


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



def write_one_node_private_state(tmp_path: Path) -> Path:
    state_path = tmp_path / "main_computer.private.yaml"
    state_path.write_text(
        """
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
    rpc: https://testnet-rpc.greatlibrary.io
    qbft:
      instances:
        validator-rpc-1:
          coolify_host: A
          roles: [rpc, validator]
          rpc_host_port: 30010
          p2p_host_port: 30321
        validator-2:
          coolify_host: B
          roles: [validator]
          p2p_host_port: 30312
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

    def fake_json_rpc(url: str, method: str, params: list[Any] | None = None, *, timeout_s: float = 8.0, user_agent: str = "") -> Any:
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

    def fake_json_rpc(url: str, method: str, params: list[Any] | None = None, *, timeout_s: float = 8.0, user_agent: str = "") -> Any:
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


def test_instances_selection_infers_single_coolify_host_from_private_state(tmp_path: Path) -> None:
    module = _load_module()
    state_path = write_one_node_private_state(tmp_path)

    args = module.parse_args(
        [
            "plan",
            "testnet",
            "--private-state",
            str(state_path),
            "--instances",
            "validator-rpc-1",
        ]
    )
    plan = module.build_plan_from_args(args)

    assert [host.id for host in plan.hosts] == ["a"]
    assert [service.id for service in plan.services] == ["validator-rpc-1"]
    service = plan.services[0]
    assert service.host == "a"
    assert service.role == "validator"
    assert service.roles == ("rpc", "validator")
    assert service.rpc_host_port == 30010
    assert plan.external_rpc_url == "https://testnet-rpc.greatlibrary.io"
    assert plan.public_rpc is True
    assert service.rpc_bind_host == "0.0.0.0"
    assert service.rpc_url_on_host == "http://198.51.100.10:30010"
    assert module.single_host_id(plan) == "a"
    assert module.infer_external_rpc_url(plan, args) == "https://testnet-rpc.greatlibrary.io"
    assert module.rpc_probe_url_candidates(plan, args) == [
        {"url": "http://198.51.100.10:30010", "source": "direct-host-port"},
        {"url": "https://testnet-rpc.greatlibrary.io", "source": "configured-network-rpc"},
    ]
    assert module.contract_deployment_rpc_candidates(plan, args) == [
        {"url": "http://198.51.100.10:30010", "source": "direct-host-port"},
        {"url": "https://testnet-rpc.greatlibrary.io:443", "source": "configured-network-rpc"},
    ]


def test_contract_deployment_rpc_selection_falls_back_to_canonical_port(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_module()
    state_path = write_one_node_private_state(tmp_path)

    args = module.parse_args(
        [
            "deploy-contracts",
            "testnet",
            "--private-state",
            str(state_path),
            "--instances",
            "validator-rpc-1",
        ]
    )
    plan = module.build_plan_from_args(args)
    calls: list[str] = []

    def fake_json_rpc(url: str, method: str, *args: Any, **kwargs: Any) -> str:
        assert method == "eth_chainId"
        calls.append(url)
        if url == "http://198.51.100.10:30010":
            raise RuntimeError("blocked direct host-port")
        if url == "https://testnet-rpc.greatlibrary.io:443":
            return hex(plan.chain_id)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(module, "json_rpc", fake_json_rpc)

    assert module.select_contract_deployment_rpc_url(plan, args) == "https://testnet-rpc.greatlibrary.io:443"
    assert calls == ["http://198.51.100.10:30010", "https://testnet-rpc.greatlibrary.io:443"]


def test_private_state_external_rpc_publishes_selected_rpc_host_port_by_default(tmp_path: Path) -> None:
    module = _load_module()
    state_path = write_one_node_private_state(tmp_path)

    plan = module.build_plan("testnet", private_state_path=state_path, instances="validator-rpc-1")
    compose = module.render_compose_for_host(plan, "a", include_bootstrap=False)

    assert '"0.0.0.0:30010:8545"' in compose


def test_private_state_external_rpc_renders_local_public_entry_sidecar(tmp_path: Path) -> None:
    module = _load_module()
    state_path = write_one_node_private_state(tmp_path)

    plan = module.build_plan("testnet", private_state_path=state_path, instances="validator-rpc-1")
    compose = module.render_compose_for_host(plan, "a", include_bootstrap=False)

    assert "testnet-rpc-public-entry-config-a:" in compose
    assert "/data/coolify/proxy/dynamic/main-computer-testnet-rpc-public-entry-a.yml" in compose
    assert 'rule: "Host(`testnet-rpc.greatlibrary.io`)"' in compose
    assert '- url: "http://198.51.100.10:30010"' in compose
    assert "http://198.51.100.11:30010" not in compose


def test_rpc_public_entry_sidecar_uses_only_local_rpc_topology(tmp_path: Path) -> None:
    module = _load_module()
    state_path = write_one_node_private_state(tmp_path)

    plan = module.build_plan("testnet", private_state_path=state_path)
    compose_a = module.render_compose_for_host(plan, "a", include_bootstrap=False)
    compose_b = module.render_compose_for_host(plan, "b", include_bootstrap=False)

    assert "testnet-rpc-public-entry-config-a:" in compose_a
    assert '- url: "http://198.51.100.10:30010"' in compose_a
    assert "http://198.51.100.11" not in compose_a

    assert "testnet-rpc-public-entry-config-b:" in compose_b
    assert "Removed stale RPC Traefik dynamic config" in compose_b
    assert 'Host(`testnet-rpc.greatlibrary.io`)' not in compose_b
    assert "http://198.51.100.10:30010" not in compose_b


def test_wait_rpc_uses_direct_host_port_before_configured_public_rpc(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_module()
    state_path = write_one_node_private_state(tmp_path)
    args = module.parse_args(
        [
            "wait-rpc",
            "testnet",
            "--private-state",
            str(state_path),
            "--instances",
            "validator-rpc-1",
            "--no-rpc-require-block-advance",
        ]
    )
    plan = module.build_plan_from_args(args)
    calls: list[str] = []

    def fake_json_rpc(url: str, method: str, params: list[Any] | None = None, *, timeout_s: float = 8.0, user_agent: str = "") -> Any:
        calls.append(url)
        assert url == "http://198.51.100.10:30010"
        if method == "eth_chainId":
            return hex(plan.chain_id)
        if method == "eth_blockNumber":
            return "0x2a"
        if method == "net_peerCount":
            return "0x0"
        raise AssertionError(method)

    monkeypatch.setattr(module, "json_rpc", fake_json_rpc)

    result = module.wait_for_rpc(plan, args)

    assert result["ok"] is True
    assert result["rpc_url"] == "http://198.51.100.10:30010"
    assert result["rpc_url_source"] == "direct-host-port"
    assert "https://testnet-rpc.greatlibrary.io" not in calls


def test_coolify_sync_dry_run_infers_host_from_selected_instance_without_host_flag(tmp_path: Path) -> None:
    module = _load_module()
    state_path = write_one_node_private_state(tmp_path)
    args = module.parse_args(
        [
            "coolify-sync",
            "testnet",
            "--private-state",
            str(state_path),
            "--instances",
            "validator-rpc-1",
            "--dry-run",
        ]
    )
    plan = module.build_plan_from_args(args)

    result = module.coolify_sync(plan, args, deploy=False)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["service_name"] == "main-computer-qbft-testnet-a"
    assert "validator-rpc-1:" in result["compose"]
    assert "validator-2:" not in result["compose"]


def test_qbft_operator_runbook_does_not_recommend_ssh_deploy_path() -> None:
    module = _load_module()

    runbook = module.render_operator_runbook()

    assert "--instances validator-rpc-1" in runbook
    assert "apply testnet --all" not in runbook
    assert "--host A" not in runbook
    assert "--single-host root@" not in runbook
    assert "ssh root@" not in runbook


def write_one_node_private_state_without_server_uuid(tmp_path: Path) -> Path:
    state_path = tmp_path / "main_computer.private.yaml"
    state_path.write_text(
        """
coolify:
  project_name: Main Computer
  hosts:
    A:
      name: coolify-a
      public_ip: 198.51.100.10
      url: http://198.51.100.10:8000/
      api_token: secret-a

networks:
  testnet:
    display_name: Main Computer Testnet
    kind: testnet
    chain_id: 42424241
    remote_coolify_hosts: [A]
    rpc: https://testnet-rpc.greatlibrary.io
    qbft:
      instances:
        validator-rpc-1:
          coolify_host: A
          roles: [rpc, validator]
          rpc_host_port: 30010
          p2p_host_port: 30321
""".lstrip(),
        encoding="utf-8",
    )
    return state_path


class _CreateServiceFakeCoolifyClient:
    def __init__(self, module: Any) -> None:
        self.module = module
        self.base_url = "http://fake-coolify"
        self.created_payload: dict[str, Any] | None = None
        self.requests: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, payload: Any | None = None) -> Any:
        self.requests.append((method.upper(), path, payload))
        method = method.upper()
        if path == "/api/v1/version":
            return self.module.CoolifyResponse(True, 200, method, "http://fake-coolify", path, "4.1.2")
        if path == "/api/v1/services" and method == "GET":
            return self.module.CoolifyResponse(True, 200, method, "http://fake-coolify", path, [])
        if path == "/api/v1/projects":
            return self.module.CoolifyResponse(
                True,
                200,
                method,
                "http://fake-coolify",
                path,
                [{"uuid": "project-1", "name": "Main Computer"}],
            )
        if path == "/api/v1/servers":
            return self.module.CoolifyResponse(
                True,
                200,
                method,
                "http://fake-coolify",
                path,
                [{"uuid": "server-1", "name": "localhost", "settings": {"sentinel_token": "must-not-leak"}}],
            )
        if path == "/api/v1/projects/project-1/environments":
            return self.module.CoolifyResponse(
                True,
                200,
                method,
                "http://fake-coolify",
                path,
                [{"uuid": "env-1", "name": "testnet"}],
            )
        if path == "/api/v1/services" and method == "POST":
            assert payload is not None
            self.created_payload = dict(payload)
            return self.module.CoolifyResponse(True, 201, method, "http://fake-coolify", path, {"uuid": "service-1"})
        if path in {"/api/v1/services/service-1", "/api/v1/services/service-1/compose"} and method in {"PATCH", "PUT"}:
            return self.module.CoolifyResponse(True, 200, method, "http://fake-coolify", path, {"uuid": "service-1"})
        return self.module.CoolifyResponse(False, 404, method, "http://fake-coolify", path, {"message": "unknown"})


def _contains_exact_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_exact_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_exact_key(item, key) for item in value)
    return False


def test_coolify_sync_uses_singleton_coolify_server_when_private_host_name_is_not_server_name(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    module = _load_module()
    state_path = write_one_node_private_state_without_server_uuid(tmp_path)
    args = module.parse_args(
        [
            "coolify-sync",
            "testnet",
            "--private-state",
            str(state_path),
            "--instances",
            "validator-rpc-1",
            "--no-deploy",
        ]
    )
    plan = module.build_plan_from_args(args)

    assert plan.hosts[0].id == "a"
    assert plan.hosts[0].server_name == ""

    fake_client = _CreateServiceFakeCoolifyClient(module)

    def fake_client_from_args(call_args: Any, call_plan: Any, *, host_id: str | None = None) -> tuple[Any, str, str]:
        assert host_id in {None, "a"}
        return fake_client, "token", "fake-token"

    monkeypatch.setattr(module, "coolify_client_from_args", fake_client_from_args)

    result = module.coolify_sync(plan, args, deploy=False)

    assert result["ok"] is True
    assert result["service_uuid"] == "service-1"
    assert fake_client.created_payload is not None
    assert fake_client.created_payload["server_uuid"] == "server-1"
    assert fake_client.created_payload["project_uuid"] == "project-1"
    assert fake_client.created_payload["environment_uuid"] == "env-1"
    assert fake_client.created_payload["name"] == "main-computer-qbft-testnet-a"
    assert not _contains_exact_key(result, "body")


def test_fake_private_state_fixture_drives_testnet_and_mainnet_qbft_plans() -> None:
    module = _load_module()

    testnet = module.build_plan("testnet", private_state_path=FAKE_PRIVATE_STATE_FIXTURE)
    testnet_services = {service.id: service for service in testnet.services}
    assert testnet.environment == "testnet"
    assert {host.id for host in testnet.hosts} == {"a", "b"}
    assert set(testnet_services) == {"validator-rpc-1", "validator-1", "validator-2", "rpc-1"}
    assert testnet_services["validator-rpc-1"].rpc_host_port == 30110
    assert testnet_services["validator-rpc-1"].p2p_host_port == 30410
    assert testnet_services["rpc-1"].rpc_host_port == 30120
    assert module.rpc_target_service(testnet).id == "rpc-1"
    assert "single-Besu" not in "\n".join(testnet.warnings)
    assert "Topology has 3 validators" in "\n".join(testnet.warnings)

    try:
        module.build_plan("mainnet", private_state_path=FAKE_PRIVATE_STATE_FIXTURE)
    except module.PlanError as exc:
        assert "--allow-mainnet" in str(exc)
    else:
        raise AssertionError("mainnet fixture must still require --allow-mainnet")

    mainnet = module.build_plan(
        "mainnet",
        private_state_path=FAKE_PRIVATE_STATE_FIXTURE,
        allow_mainnet=True,
    )
    mainnet_services = {service.id: service for service in mainnet.services}
    assert mainnet.environment == "mainnet"
    assert mainnet.chain_id == 42424240
    assert {host.id for host in mainnet.hosts} == {"a", "b"}
    assert set(mainnet_services) == {"validator-rpc-1", "validator-1", "validator-2", "rpc-1"}
    assert mainnet_services["validator-rpc-1"].rpc_host_port == 31110
    assert mainnet_services["validator-rpc-1"].p2p_host_port == 31410
    assert mainnet_services["rpc-1"].rpc_host_port == 31120
    assert mainnet_services["rpc-1"].host == "b"
    assert module.rpc_target_service(mainnet).id == "rpc-1"
    assert "single-validator" not in "\n".join(mainnet.warnings)
    assert "Topology has 3 validators" in "\n".join(mainnet.warnings)



def test_mainnet_private_state_wallets_prefund_qbft_genesis(tmp_path: Path) -> None:
    module = _load_module()
    state_path = tmp_path / "main_computer.private.yaml"
    state_path.write_text(
        """
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

networks:
  mainnet:
    display_name: Main Computer Mainnet
    kind: mainnet
    chain_id: 42424240
    rpc: https://mainnet-rpc.greatlibrary.io
    wallets:
      deployer:
        address: "0x2000000000000000000000000000000000000001"
        private_key: "0x1111111111111111111111111111111111111111111111111111111111111111"
      captain:
        address: "0x2000000000000000000000000000000000000002"
        private_key: "0x2222222222222222222222222222222222222222222222222222222222222222"
      o1:
        address: "0x2000000000000000000000000000000000000003"
        private_key: "0x3333333333333333333333333333333333333333333333333333333333333333"
      o2:
        address: "0x2000000000000000000000000000000000000004"
        private_key: "0x4444444444444444444444444444444444444444444444444444444444444444"
      o3:
        address: "0x2000000000000000000000000000000000000005"
        private_key: "0x5555555555555555555555555555555555555555555555555555555555555555"
      hub_admin:
        address: "0x2000000000000000000000000000000000000006"
        private_key: "0x6666666666666666666666666666666666666666666666666666666666666666"
      escrow_owner:
        address: "0x2000000000000000000000000000000000000007"
        private_key: "0x7777777777777777777777777777777777777777777777777777777777777777"
    qbft:
      instances:
        validator-rpc-1:
          coolify_host: A
          roles: [rpc, validator]
          rpc_host_port: 40010
          p2p_host_port: 40321
""".lstrip(),
        encoding="utf-8",
    )

    plan = module.build_plan(
        "mainnet",
        private_state_path=state_path,
        allow_mainnet=True,
        instances="validator-rpc-1",
    )
    alloc = module.qbft_config(plan)["genesis"]["alloc"]

    expected = {
        "2000000000000000000000000000000000000001",
        "2000000000000000000000000000000000000002",
        "2000000000000000000000000000000000000003",
        "2000000000000000000000000000000000000004",
        "2000000000000000000000000000000000000005",
        "2000000000000000000000000000000000000006",
        "2000000000000000000000000000000000000007",
    }
    assert set(alloc) == expected
    assert all(entry["balance"] == module.DEFAULT_FUNDED_ACCOUNT_BALANCE for entry in alloc.values())
    assert "f39fd6e51aad88f6f4ce6ab8827279cfffb92266" not in alloc
    assert plan.funded_accounts == (
        "0x2000000000000000000000000000000000000001",
        "0x2000000000000000000000000000000000000002",
        "0x2000000000000000000000000000000000000003",
        "0x2000000000000000000000000000000000000004",
        "0x2000000000000000000000000000000000000005",
        "0x2000000000000000000000000000000000000006",
        "0x2000000000000000000000000000000000000007",
    )

def test_filtered_mainnet_validator_rpc_instance_satisfies_rpc_topology_policy() -> None:
    module = _load_module()

    plan = module.build_plan(
        "mainnet",
        private_state_path=FAKE_PRIVATE_STATE_FIXTURE,
        allow_mainnet=True,
        instances="validator-rpc-1",
    )

    assert [service.id for service in plan.services] == ["validator-rpc-1"]
    service = plan.services[0]
    assert service.role == "validator"
    assert service.roles == ("rpc", "validator")
    assert service.rpc_host_port == 31110
    assert module.rpc_target_service(plan).id == "validator-rpc-1"
