from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "coolify_qbft_network.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("coolify_qbft_network", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_default_testnet_plan_has_four_validators_and_dedicated_rpc() -> None:
    module = _load_module()

    plan = module.build_plan("testnet")
    services = list(plan.services)

    assert plan.environment == "testnet"
    assert plan.chain_id == 42424241
    assert len([service for service in services if service.role == "validator"]) == 4
    assert len([service for service in services if service.role == "rpc"]) == 1
    assert module.rpc_target_service(plan).id == "rpc-1"
    assert module.rpc_target_service(plan).rpc_host_port == 30010


def test_default_plan_assigns_globally_unique_host_ports() -> None:
    module = _load_module()

    plan = module.build_plan("testnet")
    ports = []
    for service in plan.services:
        ports.extend([service.rpc_host_port, service.p2p_host_port])

    assert len(ports) == len(set(ports))
    assert {30001, 30002, 30003, 30004, 30010, 30311, 30312, 30313, 30314, 30320}.issubset(set(ports))


def test_compose_render_contains_bootstrap_dedicated_rpc_and_managed_volume() -> None:
    module = _load_module()

    plan = module.build_plan("testnet", besu_image="hyperledger/besu:24.7.0")
    compose = module.render_compose_for_host(plan, "testnet-a")

    assert "name: main-computer-qbft-testnet-testnet-a" in compose
    assert "qbft-bootstrap:" in compose
    assert "operator generate-blockchain-config" in compose
    assert '"127.0.0.1:30001:8545"' in compose
    assert '"127.0.0.1:30010:8545"' in compose
    assert '"main-computer-qbft-testnet-testnet-a-runtime:/smoke"' in compose
    assert "--genesis-file=/smoke/genesis.json" in compose
    assert "waiting for QBFT bootstrap files for validator-1" in compose
    assert "missing required QBFT bootstrap file: /smoke/genesis.json" in compose
    assert "      - -ec" in compose
    assert "    command: |" not in compose
    assert "mc-qbft-rpc" in compose
    assert "rpc-1:" in compose
    assert "validator-2:" in compose
    assert "validator-3:" in compose
    assert "validator-4:" in compose
    assert "waiting for QBFT bootstrap files for rpc-1" in compose
    assert "--data-path=/smoke/rpc-node/data" in compose


def test_bootstrap_command_escapes_shell_dollars_for_docker_compose() -> None:
    module = _load_module()

    plan = module.build_plan("testnet", besu_image="hyperledger/besu:24.7.0")
    compose = module.render_compose_for_host(plan, "testnet-a")
    bootstrap_entrypoint_script = compose.split("      - |-", 1)[1].split("\n    volumes:", 1)[0]

    assert "      - -ec" in compose
    assert "    command: |" not in compose
    assert "$${QBFT_RESET_CHAIN:-false}" in bootstrap_entrypoint_script
    assert "\"$$BESU\" operator generate-blockchain-config" in bootstrap_entrypoint_script
    assert "set -- $$(find /tmp/qbft-networkFiles/keys" in bootstrap_entrypoint_script
    assert "if [ \"$$#\" -ne 4 ]" in bootstrap_entrypoint_script
    assert "pub=$$(tr -d" in bootstrap_entrypoint_script
    assert "pub=$${pub#0x}" in bootstrap_entrypoint_script
    assert "$${#pub}" in bootstrap_entrypoint_script
    assert "$$ENODE_1" in bootstrap_entrypoint_script
    assert "$$ENODE_4" in bootstrap_entrypoint_script


def test_bind_runtime_root_mode_uses_coolify_directory_hint() -> None:
    module = _load_module()

    plan = module.build_plan("testnet", besu_image="hyperledger/besu:24.7.0")
    compose = module.render_compose_for_host(plan, "testnet-a", managed_volume=False)

    assert "type: bind" in compose
    assert "source: \"/srv/main-computer/qbft-testnet/runtime\"" in compose
    assert "is_directory: true" in compose


def test_split_seed_preserves_single_file_layout_model() -> None:
    module = _load_module()

    plan = module.build_plan("testnet-split-example")

    assert {host.id for host in plan.hosts} == {"validator-a", "validator-b", "rpc-a"}
    assert any("Services span multiple hosts" in warning for warning in plan.warnings)
    assert all(service.rpc_host_port for service in plan.services)
    assert all(service.p2p_host_port for service in plan.services)


def test_duplicate_port_seed_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    seed = json.loads(json.dumps(module.NETWORK_SEEDS["testnet"]))
    duplicate = dict(seed["services"][0])
    duplicate["id"] = "validator-duplicate"
    duplicate["container_ip"] = "172.28.241.30"
    duplicate["p2p_host_port"] = 30311
    seed["services"].append(duplicate)
    seed_path = tmp_path / "bad-seed.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")

    try:
        module.build_plan(str(seed_path))
    except module.PlanError as exc:
        assert "Host port" in str(exc)
    else:
        raise AssertionError("duplicate host port should have been rejected")


def test_mainnet_seed_requires_acknowledgement() -> None:
    module = _load_module()

    try:
        module.build_plan("mainnet")
    except module.PlanError as exc:
        assert "--allow-mainnet" in str(exc)
    else:
        raise AssertionError("mainnet seed should require acknowledgement")

    plan = module.build_plan("mainnet", allow_mainnet=True)
    assert plan.environment == "mainnet"


def test_single_host_override_updates_address_and_coolify_url() -> None:
    module = _load_module()

    plan = module.build_plan(
        "testnet",
        single_host="root@157.245.92.74",
        coolify_url="http://157.245.92.74:8000",
        public_rpc=True,
    )
    host = plan.hosts[0]
    rpc = module.rpc_target_service(plan)

    assert host.ssh == "root@157.245.92.74"
    assert host.address == "157.245.92.74"
    assert host.coolify_url == "http://157.245.92.74:8000"
    assert rpc.id == "rpc-1"
    assert rpc.rpc_bind_host == "0.0.0.0"
    assert rpc.rpc_url_on_host == "http://157.245.92.74:30010"


def test_coolify_sync_dry_run_includes_redacted_bootstrap_compose() -> None:
    module = _load_module()

    plan = module.build_plan("testnet", public_rpc=True, single_host="root@157.245.92.74")
    args = module.parse_args([
        "coolify-sync",
        "testnet",
        "--single-host",
        "root@157.245.92.74",
        "--coolify-url",
        "http://157.245.92.74:8000",
        "--coolify-token",
        "2|abcdefghijklmnopqrstuvwxyz",
        "--dry-run",
    ])
    result = module.coolify_sync(plan, args, deploy=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "qbft-bootstrap:" in result["compose"]
    assert "abcdefghijklmnopqrstuvwxyz" not in str(result)


def test_deploy_contracts_dry_run_uses_public_rpc_without_remote_ssh() -> None:
    module = _load_module()

    plan = module.build_plan("testnet", public_rpc=True, single_host="root@157.245.92.74")
    args = module.parse_args([
        "deploy-contracts",
        "testnet",
        "--single-host",
        "root@157.245.92.74",
        "--public-rpc",
        "--dry-run",
    ])
    result = module.deploy_contracts(plan, args)

    assert result["ok"] is True
    command = result["command"]
    assert "--external-chain" in command
    assert "--host-rpc-url" in command
    assert command[command.index("--host-rpc-url") + 1] == "http://157.245.92.74:30010"
    assert command[command.index("--environment") + 1] == "testnet"
    assert command[command.index("--wait-timeout-s") + 1] == "0.0"
    assert command[command.index("--deploy-timeout-s") + 1] == "0.0"
    assert command[command.index("--external-docker-network") + 1] == "bridge"


def test_coolify_client_timeout_returns_structured_failure(monkeypatch) -> None:
    module = _load_module()

    def timeout_urlopen(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003 - pytest stub
        raise TimeoutError("timed out")

    monkeypatch.setattr(module.urllib.request, "urlopen", timeout_urlopen)
    client = module.CoolifyClient("http://coolify.example.test:8000", "secret-token", timeout_s=0.01, retries=1, retry_sleep_s=0)

    response = client.request("GET", "/api/v1/version")

    assert response.ok is False
    assert response.status == 0
    assert response.body["error"] == "request_failed"
    assert response.body["error_type"] == "TimeoutError"
    assert response.body["attempts"] == 2
    assert "secret-token" not in str(response.body)


def test_apply_dry_run_emits_operator_progress_to_stdout(capsys) -> None:
    module = _load_module()

    plan = module.build_plan("testnet", public_rpc=True, single_host="root@157.245.92.74")
    args = module.parse_args([
        "apply",
        "testnet",
        "--single-host",
        "root@157.245.92.74",
        "--coolify-url",
        "http://157.245.92.74:8000",
        "--public-rpc",
        "--dry-run",
    ])

    result = module.apply_network(plan, args)
    stdout = capsys.readouterr().out

    assert result["ok"] is True
    assert "[coolify-qbft]" in stdout
    assert "apply start" in stdout
    assert "coolify-sync dry-run" in stdout


def test_coolify_sync_create_autodiscovers_single_project_and_server_and_uses_base64(monkeypatch) -> None:
    import base64

    module = _load_module()
    plan = module.build_plan("testnet", public_rpc=True, single_host="root@157.245.92.74")

    class FakeClient:
        base_url = "http://coolify.example.test:8000"

        def __init__(self) -> None:
            self.payloads = []

        def request(self, method: str, path: str, payload=None):  # noqa: ANN001
            self.payloads.append((method, path, payload))
            if path == "/api/v1/version":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, "4.1.2")
            if method == "GET" and path == "/api/v1/services":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [])
            if path == "/api/v1/projects":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [{"uuid": "project-1", "name": "Main Computer"}])
            if path == "/api/v1/servers":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [{"uuid": "server-1", "name": "localhost"}])
            if method == "GET" and path == "/api/v1/projects/project-1/environments":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [])
            if method == "POST" and path == "/api/v1/projects/project-1/environments":
                return module.CoolifyResponse(True, 201, method, self.base_url, path, {"uuid": "environment-1"})
            if method == "POST" and path == "/api/v1/services":
                return module.CoolifyResponse(True, 201, method, self.base_url, path, {"uuid": "service-1"})
            if method == "PATCH" and path == "/api/v1/services/service-1":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, {"uuid": "service-1"})
            raise AssertionError((method, path, payload))

    fake = FakeClient()
    monkeypatch.setattr(module, "coolify_client_from_args", lambda args, plan: (fake, "secret-token", "direct"))

    args = module.parse_args([
        "coolify-sync",
        "testnet",
        "--single-host",
        "root@157.245.92.74",
        "--coolify-url",
        "http://coolify.example.test:8000",
        "--coolify-token",
        "secret-token",
        "--no-deploy",
    ])
    result = module.coolify_sync(plan, args, deploy=False)

    assert result["ok"] is True
    assert result["service_uuid"] == "service-1"
    create_payload = next(payload for method, path, payload in fake.payloads if method == "POST" and path == "/api/v1/services")
    assert create_payload["project_uuid"] == "project-1"
    assert create_payload["server_uuid"] == "server-1"
    assert create_payload["environment_name"] == "testnet"
    assert create_payload["environment_uuid"] == "environment-1"
    assert create_payload["instant_deploy"] is False
    assert "docker_compose" not in create_payload
    assert "is_raw_compose_deployment_enabled" not in create_payload
    decoded_compose = base64.b64decode(create_payload["docker_compose_raw"]).decode("utf-8")
    assert "qbft-bootstrap:" in decoded_compose
    assert "validator-1:" in decoded_compose
    assert "rpc-1:" in decoded_compose


def test_coolify_sync_uses_existing_environment_uuid_without_creating(monkeypatch) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", public_rpc=True, single_host="root@157.245.92.74")

    class FakeClient:
        base_url = "http://coolify.example.test:8000"

        def __init__(self) -> None:
            self.payloads = []

        def request(self, method: str, path: str, payload=None):  # noqa: ANN001
            self.payloads.append((method, path, payload))
            if path == "/api/v1/version":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, "4.1.2")
            if method == "GET" and path == "/api/v1/services":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [])
            if path == "/api/v1/projects":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [{"uuid": "project-1", "name": "Main Computer"}])
            if path == "/api/v1/servers":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [{"uuid": "server-1", "name": "localhost"}])
            if method == "GET" and path == "/api/v1/projects/project-1/environments":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [{"uuid": "environment-1", "name": "testnet"}])
            if method == "POST" and path == "/api/v1/services":
                return module.CoolifyResponse(True, 201, method, self.base_url, path, {"uuid": "service-1"})
            if method == "PATCH" and path == "/api/v1/services/service-1":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, {"uuid": "service-1"})
            raise AssertionError((method, path, payload))

    fake = FakeClient()
    monkeypatch.setattr(module, "coolify_client_from_args", lambda args, plan: (fake, "secret-token", "direct"))

    args = module.parse_args([
        "coolify-sync",
        "testnet",
        "--single-host",
        "root@157.245.92.74",
        "--coolify-url",
        "http://coolify.example.test:8000",
        "--coolify-token",
        "secret-token",
        "--no-deploy",
    ])
    result = module.coolify_sync(plan, args, deploy=False)

    assert result["ok"] is True
    create_payload = next(payload for method, path, payload in fake.payloads if method == "POST" and path == "/api/v1/services")
    assert create_payload["environment_uuid"] == "environment-1"
    assert ("POST", "/api/v1/projects/project-1/environments", {"name": "testnet"}) not in fake.payloads


def test_coolify_sync_reports_multiple_discovered_projects(monkeypatch) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", public_rpc=True, single_host="root@157.245.92.74")

    class FakeClient:
        base_url = "http://coolify.example.test:8000"

        def request(self, method: str, path: str, payload=None):  # noqa: ANN001
            if path == "/api/v1/version":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, "4.1.2")
            if method == "GET" and path == "/api/v1/services":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [])
            if path == "/api/v1/projects":
                return module.CoolifyResponse(
                    True,
                    200,
                    method,
                    self.base_url,
                    path,
                    [{"uuid": "project-1", "name": "A"}, {"uuid": "project-2", "name": "B"}],
                )
            if path == "/api/v1/servers":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [{"uuid": "server-1", "name": "localhost"}])
            raise AssertionError((method, path, payload))

    monkeypatch.setattr(module, "coolify_client_from_args", lambda args, plan: (FakeClient(), "secret-token", "direct"))

    args = module.parse_args([
        "coolify-sync",
        "testnet",
        "--single-host",
        "root@157.245.92.74",
        "--coolify-url",
        "http://coolify.example.test:8000",
        "--coolify-token",
        "secret-token",
    ])
    result = module.coolify_sync(plan, args, deploy=False)

    assert result["ok"] is False
    assert result["stage"] == "missing-create-context"
    assert "candidates" in result["context"]["project_selection"]
    assert "coolify-project-uuid" in result["message"]


def test_coolify_sync_reuses_existing_service_name_without_creating(monkeypatch) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", public_rpc=True, single_host="root@157.245.92.74")
    service_name = "main-computer-qbft-testnet-testnet-a"

    class FakeClient:
        base_url = "http://coolify.example.test:8000"

        def __init__(self) -> None:
            self.payloads = []

        def request(self, method: str, path: str, payload=None):  # noqa: ANN001
            self.payloads.append((method, path, payload))
            if path == "/api/v1/version":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, "4.1.2")
            if method == "GET" and path == "/api/v1/services":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [{"uuid": "service-existing", "name": service_name}])
            if method == "PATCH" and path == "/api/v1/services/service-existing":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, {"uuid": "service-existing"})
            raise AssertionError((method, path, payload))

    fake = FakeClient()
    monkeypatch.setattr(module, "coolify_client_from_args", lambda args, plan: (fake, "secret-token", "direct"))

    args = module.parse_args([
        "coolify-sync",
        "testnet",
        "--single-host",
        "root@157.245.92.74",
        "--coolify-url",
        "http://coolify.example.test:8000",
        "--coolify-token",
        "secret-token",
        "--no-deploy",
    ])
    result = module.coolify_sync(plan, args, deploy=False)

    assert result["ok"] is True
    assert result["service_uuid"] == "service-existing"
    assert ("POST", "/api/v1/services", None) not in fake.payloads
    assert not any(method == "POST" and path == "/api/v1/services" for method, path, _ in fake.payloads)


def test_coolify_sync_refuses_duplicate_existing_service_names(monkeypatch) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", public_rpc=True, single_host="root@157.245.92.74")
    service_name = "main-computer-qbft-testnet-testnet-a"

    class FakeClient:
        base_url = "http://coolify.example.test:8000"

        def __init__(self) -> None:
            self.payloads = []

        def request(self, method: str, path: str, payload=None):  # noqa: ANN001
            self.payloads.append((method, path, payload))
            if path == "/api/v1/version":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, "4.1.2")
            if method == "GET" and path == "/api/v1/services":
                return module.CoolifyResponse(
                    True,
                    200,
                    method,
                    self.base_url,
                    path,
                    [{"uuid": "service-a", "name": service_name}, {"uuid": "service-b", "name": service_name}],
                )
            raise AssertionError((method, path, payload))

    fake = FakeClient()
    monkeypatch.setattr(module, "coolify_client_from_args", lambda args, plan: (fake, "secret-token", "direct"))

    args = module.parse_args([
        "coolify-sync",
        "testnet",
        "--single-host",
        "root@157.245.92.74",
        "--coolify-url",
        "http://coolify.example.test:8000",
        "--coolify-token",
        "secret-token",
    ])
    result = module.coolify_sync(plan, args, deploy=False)

    assert result["ok"] is False
    assert result["stage"] == "duplicate-service-name"
    assert "Multiple Coolify services named" in result["message"]
    assert not any(method == "POST" and path == "/api/v1/services" for method, path, _ in fake.payloads)


def test_coolify_sync_create_path_uses_api_only_and_does_not_require_ssh(monkeypatch) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", public_rpc=True, single_host="root@157.245.92.74")

    class FakeClient:
        base_url = "http://coolify.example.test:8000"

        def __init__(self) -> None:
            self.payloads = []

        def request(self, method: str, path: str, payload=None):  # noqa: ANN001
            self.payloads.append((method, path, payload))
            if path == "/api/v1/version":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, "4.1.2")
            if method == "GET" and path == "/api/v1/services":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [])
            if path == "/api/v1/projects":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [{"uuid": "project-1", "name": "Main Computer"}])
            if path == "/api/v1/servers":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [{"uuid": "server-1", "name": "localhost"}])
            if method == "GET" and path == "/api/v1/projects/project-1/environments":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, [])
            if method == "POST" and path == "/api/v1/projects/project-1/environments":
                return module.CoolifyResponse(True, 201, method, self.base_url, path, {"uuid": "environment-1"})
            if method == "POST" and path == "/api/v1/services":
                return module.CoolifyResponse(True, 201, method, self.base_url, path, {"uuid": "service-1"})
            if method == "PATCH" and path == "/api/v1/services/service-1":
                return module.CoolifyResponse(True, 200, method, self.base_url, path, {"uuid": "service-1"})
            raise AssertionError((method, path, payload))

    fake = FakeClient()
    monkeypatch.setattr(module, "coolify_client_from_args", lambda args, plan: (fake, "secret-token", "direct"))

    args = module.parse_args([
        "coolify-sync",
        "testnet",
        "--single-host",
        "root@157.245.92.74",
        "--coolify-url",
        "http://coolify.example.test:8000",
        "--coolify-token",
        "secret-token",
    ])
    result = module.coolify_sync(plan, args, deploy=False)

    assert result["ok"] is True
    assert result["service_uuid"] == "service-1"
    assert any(method == "GET" and path == "/api/v1/services" for method, path, _ in fake.payloads)
    assert any(method == "POST" and path == "/api/v1/services" for method, path, _ in fake.payloads)
    assert any(
        item.get("operation") == "create-service-safety-policy"
        and item.get("mode") == "coolify-api-service-name-only"
        for item in result["tried"]
    )




def test_wait_for_rpc_requires_peer_and_block_advancement(monkeypatch) -> None:
    module = _load_module()

    plan = module.build_plan("testnet", public_rpc=True)
    args = module.parse_args(
        [
            "wait-rpc",
            "testnet",
            "--rpc-url",
            "http://127.0.0.1:30010",
            "--rpc-timeout-s",
            "20",
            "--rpc-poll-interval-s",
            "0",
            "--quiet",
        ]
    )
    blocks = iter(["0x5", "0x5", "0x6"])
    peers = iter(["0x0", "0x1", "0x1"])
    calls: list[str] = []

    def fake_json_rpc(url: str, method: str, params=None, *, timeout_s: float = 8.0):  # noqa: ANN001
        calls.append(method)
        assert url == "http://127.0.0.1:30010"
        if method == "eth_chainId":
            return "0x28757b1"
        if method == "eth_blockNumber":
            return next(blocks)
        if method == "net_peerCount":
            return next(peers)
        raise AssertionError(method)

    monkeypatch.setattr(module, "json_rpc", fake_json_rpc)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    result = module.wait_for_rpc(plan, args)

    assert result["ok"] is True
    assert result["block_number"] == 6
    assert result["peer_count"] == 1
    assert result["first_observed_block"] == 5
    assert result["block_advanced"] is True
    assert result["min_peers"] == 1
    assert calls.count("eth_chainId") == 3


def test_wait_for_rpc_allows_zero_peers_for_single_besu_plan(monkeypatch) -> None:
    module = _load_module()

    seed = {
        "description": "single besu test",
        "environment": "testnet",
        "chain_id": 42424241,
        "compose_project": "single-besu-test",
        "docker_network": "single-besu-network",
        "docker_subnet": "172.28.250.0/24",
        "besu_image": "hyperledger/besu:latest",
        "runtime_root": "/tmp/single-besu",
        "public_rpc": True,
        "hosts": {
            "host-a": {
                "ssh": "root@198.51.100.10",
                "address": "198.51.100.10",
                "coolify_url": "http://198.51.100.10:8000",
                "runtime_root": "/tmp/single-besu",
            }
        },
        "services": [
            {
                "id": "validator-1",
                "role": "validator",
                "host": "host-a",
                "container_ip": "172.28.250.11",
                "rpc_host_port": 30010,
                "p2p_host_port": 30311,
            }
        ],
    }
    module.NETWORK_SEEDS["single-besu-test"] = seed
    plan = module.build_plan("single-besu-test", public_rpc=True)
    args = module.parse_args(
        [
            "wait-rpc",
            "testnet",
            "--rpc-url",
            "http://127.0.0.1:30010",
            "--rpc-timeout-s",
            "20",
            "--rpc-poll-interval-s",
            "0",
            "--quiet",
        ]
    )
    blocks = iter(["0x0", "0x2"])

    def fake_json_rpc(url: str, method: str, params=None, *, timeout_s: float = 8.0):  # noqa: ANN001
        if method == "eth_chainId":
            return "0x28757b1"
        if method == "eth_blockNumber":
            return next(blocks)
        if method == "net_peerCount":
            return "0x0"
        raise AssertionError(method)

    monkeypatch.setattr(module, "json_rpc", fake_json_rpc)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    result = module.wait_for_rpc(plan, args)

    assert result["ok"] is True
    assert result["block_number"] == 2
    assert result["peer_count"] == 0
    assert result["min_peers"] == 0


def test_main_without_args_prints_operator_runbook(capsys) -> None:
    module = _load_module()

    code = module.main([])
    captured = capsys.readouterr()

    assert code == 0
    assert "Main Computer Coolify QBFT network runbook" in captured.out
    assert "1. Prepare the remote Linux server" in captured.out
    assert "2. Install Coolify on the remote server" in captured.out
    assert "3. Create a Coolify API token" in captured.out
    assert "7. Deploy the four-validator testnet" in captured.out
    assert "python .\\tools\\coolify_qbft_network.py apply testnet --all" in captured.out
    assert "the following arguments are required" not in captured.err


def test_docs_action_prints_same_operator_runbook(capsys) -> None:
    module = _load_module()

    code = module.main(["docs"])
    captured = capsys.readouterr()

    assert code == 0
    assert "curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash" in captured.out
    assert "http://<SERVER_IP>:30010" in captured.out


def test_apply_defaults_wait_indefinitely_for_rpc_and_contract_deploy() -> None:
    module = _load_module()

    args = module.parse_args(["apply", "testnet", "--all"])

    assert args.rpc_timeout_s == 0.0
    assert args.deploy_contracts_timeout_s == 0.0

