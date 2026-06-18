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


def test_default_testnet_plan_uses_one_besu_validator_as_rpc_for_low_resource_host() -> None:
    module = _load_module()

    plan = module.build_plan("testnet")
    services = list(plan.services)

    assert plan.environment == "testnet"
    assert plan.chain_id == 42424241
    assert len([service for service in services if service.role == "validator"]) == 1
    assert len([service for service in services if service.role == "rpc"]) == 0
    assert module.rpc_target_service(plan).id == "validator-1"
    assert module.rpc_target_service(plan).rpc_host_port == 30010
    assert plan.topology_policy.minimum_validators == 1
    assert plan.topology_policy.minimum_rpc_nodes == 0
    assert "single-Besu bring-up mode" in "\n".join(plan.warnings)
    assert "No dedicated non-validator RPC node" in "\n".join(plan.warnings)


def test_default_plan_assigns_globally_unique_host_ports() -> None:
    module = _load_module()

    plan = module.build_plan("testnet")
    ports = []
    for service in plan.services:
        ports.extend([service.rpc_host_port, service.p2p_host_port])

    assert len(ports) == len(set(ports))
    assert set(ports) == {30010, 30311}


def test_compose_render_contains_single_testnet_besu_and_managed_volume() -> None:
    module = _load_module()

    plan = module.build_plan("testnet", besu_image="hyperledger/besu:24.7.0")
    compose = module.render_compose_for_host(plan, "testnet-a")

    assert "name: main-computer-qbft-testnet-testnet-a" in compose
    assert "qbft-bootstrap:" in compose
    assert "operator generate-blockchain-config" in compose
    assert '"127.0.0.1:30010:8545"' in compose
    assert '"main-computer-qbft-testnet-testnet-a-runtime:/smoke"' in compose
    assert "--genesis-file=/smoke/genesis.json" in compose
    assert "EXPECTED_QBFT_VALIDATOR_COUNT=1" in compose
    assert "EXPECTED_QBFT_RPC_COUNT=0" in compose
    assert "waiting for QBFT bootstrap files for validator-1" in compose
    assert "missing required QBFT bootstrap file: /smoke/genesis.json" in compose
    assert "      - -ec" in compose
    assert "    command: |" not in compose
    assert "mc-qbft-rpc" in compose
    assert "rpc-1:" not in compose
    assert "validator-2:" not in compose
    assert "validator-3:" not in compose
    assert "validator-4:" not in compose
    assert "--data-path=/smoke/validator-1/data" in compose


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
    assert "if [ \"$$#\" -ne 1 ]" in bootstrap_entrypoint_script
    assert "pub=$$(tr -d" in bootstrap_entrypoint_script
    assert "pub=$${pub#0x}" in bootstrap_entrypoint_script
    assert "$${#pub}" in bootstrap_entrypoint_script
    assert "$$ENODE_1" in bootstrap_entrypoint_script
    assert "$$ENODE_4" not in bootstrap_entrypoint_script


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
    services = list(plan.services)
    assert len([service for service in services if service.role == "validator"]) == 1
    assert len([service for service in services if service.role == "rpc"]) == 1
    assert plan.topology_policy.minimum_validators == 1
    assert "single-validator bring-up mode" in "\n".join(plan.warnings)


def test_topology_policy_controls_validator_minimum() -> None:
    module = _load_module()
    seed = json.loads(json.dumps(module.NETWORK_SEEDS["testnet"]))
    seed["services"] = [
        service
        for service in seed["services"]
        if service["role"] != "validator" or service["id"] == "validator-1"
    ]
    seed["topology_policy"]["minimum_validators"] = 2
    module.NETWORK_SEEDS["policy-min-validator-test"] = seed

    try:
        module.build_plan("policy-min-validator-test")
    except module.PlanError as exc:
        message = str(exc)
        assert "topology_policy.minimum_validators=2" in message
        assert "found 1 validators" in message
    else:
        raise AssertionError("seed violating configured validator minimum should fail")


def test_topology_policy_controls_rpc_minimum() -> None:
    module = _load_module()
    seed = json.loads(json.dumps(module.NETWORK_SEEDS["mainnet"]))
    seed["requires_mainnet_ack"] = False
    seed["services"] = [service for service in seed["services"] if service["role"] != "rpc"]
    seed["topology_policy"]["minimum_rpc_nodes"] = 1
    module.NETWORK_SEEDS["policy-min-rpc-test"] = seed

    try:
        module.build_plan("policy-min-rpc-test")
    except module.PlanError as exc:
        message = str(exc)
        assert "topology_policy.minimum_rpc_nodes=1" in message
        assert "found 0 rpc nodes" in message
    else:
        raise AssertionError("seed violating configured RPC minimum should fail")


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
    assert rpc.id == "validator-1"
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
    assert "--generate-offices" in command


def test_testnet_deploy_contracts_can_opt_out_of_generated_offices() -> None:
    module = _load_module()

    plan = module.build_plan("testnet", public_rpc=True, single_host="root@157.245.92.74")
    args = module.parse_args([
        "deploy-contracts",
        "testnet",
        "--single-host",
        "root@157.245.92.74",
        "--public-rpc",
        "--no-generate-offices",
        "--dry-run",
    ])

    result = module.deploy_contracts(plan, args)

    assert result["ok"] is True
    assert "--generate-offices" not in result["command"]


def test_mainnet_does_not_generate_offices_by_default_but_can_opt_in() -> None:
    module = _load_module()

    plan = module.build_plan("mainnet", allow_mainnet=True, public_rpc=True, single_host="root@203.0.113.10")
    default_args = module.parse_args([
        "deploy-contracts",
        "mainnet",
        "--allow-mainnet",
        "--single-host",
        "root@203.0.113.10",
        "--public-rpc",
        "--dry-run",
    ])
    opt_in_args = module.parse_args([
        "deploy-contracts",
        "mainnet",
        "--allow-mainnet",
        "--single-host",
        "root@203.0.113.10",
        "--public-rpc",
        "--generate-offices",
        "--dry-run",
    ])

    default_result = module.deploy_contracts(plan, default_args)
    opt_in_result = module.deploy_contracts(plan, opt_in_args)

    assert default_result["ok"] is True
    assert "--generate-offices" not in default_result["command"]
    assert opt_in_result["ok"] is True
    assert "--generate-offices" in opt_in_result["command"]


def test_generate_offices_flags_conflict() -> None:
    module = _load_module()

    plan = module.build_plan("testnet", public_rpc=True, single_host="root@157.245.92.74")
    args = module.parse_args([
        "deploy-contracts",
        "testnet",
        "--single-host",
        "root@157.245.92.74",
        "--public-rpc",
        "--generate-offices",
        "--no-generate-offices",
        "--dry-run",
    ])

    try:
        module.deploy_contracts(plan, args)
    except module.PlanError as exc:
        assert "--generate-offices and --no-generate-offices" in str(exc)
    else:
        raise AssertionError("conflicting office generation flags should fail")


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
    assert "rpc-1:" not in decoded_compose
    assert "validator-2:" not in decoded_compose


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

    plan = module.build_plan("testnet-split-example", public_rpc=True)
    args = module.parse_args(
        [
            "wait-rpc",
            "testnet-split-example",
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
    assert "7. Deploy the selected QBFT network" in captured.out
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



def test_local_test_seed_targets_local_coolify_and_test_manifest() -> None:
    module = _load_module()

    plan = module.build_plan("test")

    assert plan.environment == "test"
    assert plan.chain_id == 42424241
    assert plan.compose_project == "main-computer-qbft-test"
    assert plan.docker_network == "mc-qbft-test-network"
    assert plan.docker_subnet == "10.241.0.0/24"
    assert plan.hosts[0].coolify_url == "http://127.0.0.1:8000"
    assert module.rpc_target_service(plan).rpc_url_on_host == "http://127.0.0.1:30010"
    assert len([service for service in plan.services if service.role == "validator"]) == 4
    assert len([service for service in plan.services if service.role == "rpc"]) == 1


def test_local_test_subnet_repair_moves_static_ips_and_metadata_when_default_overlaps(monkeypatch) -> None:
    module = _load_module()
    plan = module.build_plan("test")
    args = module.parse_args(["apply", "test", "--all", "--quiet"])

    monkeypatch.setattr(
        module,
        "docker_network_ipv4_subnets",
        lambda: [module.ipaddress.ip_network("10.241.0.0/24")],
    )

    repaired, result = module.prepare_local_qbft_subnet(plan, args)
    compose = module.render_compose_for_host(repaired, "local-coolify")

    assert result["ok"] is True
    assert result["changed"] is True
    assert result["source"] == "auto-repair"
    assert result["previous_subnet"] == "10.241.0.0/24"
    assert repaired.docker_subnet == "10.242.0.0/24"
    assert {service.container_ip for service in repaired.services} == {
        "10.242.0.11",
        "10.242.0.12",
        "10.242.0.13",
        "10.242.0.14",
        "10.242.0.20",
    }
    assert "subnet: 10.242.0.0/24" in compose
    assert "\"docker_subnet\": \"10.242.0.0/24\"" in compose
    assert "grep -q '\"docker_subnet\": \"10.242.0.0/24\"'" in compose


def test_local_test_subnet_override_reports_overlap(monkeypatch) -> None:
    module = _load_module()
    plan = module.build_plan("test")
    args = module.parse_args(["apply", "test", "--all", "--quiet", "--docker-subnet", "172.30.241.0/24"])

    monkeypatch.setattr(
        module,
        "docker_network_ipv4_subnets",
        lambda: [module.ipaddress.ip_network("172.30.0.0/16")],
    )

    repaired, result = module.prepare_local_qbft_subnet(plan, args)

    assert repaired.docker_subnet == "172.30.241.0/24"
    assert result["ok"] is False
    assert result["requested_subnet"] == "172.30.241.0/24"
    assert result["overlaps"] == ["172.30.0.0/16"]


def test_local_test_apply_dry_run_uses_local_coolify_defaults_without_token_env() -> None:
    module = _load_module()
    plan = module.build_plan("test")
    args = module.parse_args(["apply", "test", "--all", "--dry-run", "--quiet"])

    result = module.apply_network(plan, args)

    assert result["ok"] is True
    assert result["dry_run"] is True
    sync = next(phase["result"] for phase in result["phases"] if phase["phase"] == "coolify-sync")
    assert sync["service_name"] == "main-computer-qbft-test"
    assert sync["local_coolify"]["coolify_url"] == "http://127.0.0.1:8000"
    assert sync["local_coolify"]["coolify_environment"] == "production"
    assert sync["local_coolify"]["foundry_docker_network"] == "mc-qbft-test-network"
    assert getattr(args, "coolify_token_env") == ""
    assert str(getattr(args, "coolify_token_file")).endswith("runtime/coolify-local-docker/api-token.txt")


def test_local_test_deploy_contracts_dry_run_uses_coolify_qbft_network_from_foundry() -> None:
    module = _load_module()
    plan = module.build_plan("test")
    args = module.parse_args(["deploy-contracts", "test", "--dry-run", "--quiet"])

    result = module.deploy_contracts(plan, args)
    command = result["command"]

    assert result["ok"] is True
    assert result["rpc_url"] == "http://127.0.0.1:30010"
    assert result["container_rpc_url"] == "http://mc-qbft-rpc:8545"
    assert command[command.index("--environment") + 1] == "test"
    assert command[command.index("--source-kind") + 1] == "coolify-qbft-test-deploy"
    assert command[command.index("--host-rpc-url") + 1] == "http://127.0.0.1:30010"
    assert command[command.index("--container-rpc-url") + 1] == "http://mc-qbft-rpc:8545"
    assert command[command.index("--external-docker-network") + 1] == "mc-qbft-test-network"
    assert command[command.index("--deployment-output-dir") + 1] == "runtime/deployments"
    assert "--generate-offices" in command


def test_local_test_context_bootstraps_repo_local_coolify_contract(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("test")
    args = module.parse_args(["coolify-sync", "test", "--quiet"])

    token_path = tmp_path / "runtime" / "coolify-local-docker" / "api-token.txt"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("local-token\n", encoding="utf-8")

    class FakeLocalCoolify:
        LOCAL_PROJECT_ENVIRONMENT = "production"

        def env_file(self, root: Path) -> Path:
            return tmp_path / ".env"

        def write_initial_state(self, root: Path):
            (tmp_path / ".env").write_text("ok=1\n", encoding="utf-8")
            return tmp_path / ".env", []

        def api_token_file(self, root: Path) -> Path:
            return token_path

        def ensure_infra_status(self, root: Path):
            return True, "infra ready"

        def ensure_api_token(self, root: Path):
            return True, "token ready", "local-token"

        def local_deploy_target_from_db(self, root: Path):
            return True, "target ready", {
                "server_uuid": "server-local",
                "destination_uuid": "destination-local",
            }

        def find_local_project_uuid_via_api(self, root: Path, token: str):
            assert token == "local-token"
            return True, "project ready", "project-local"

        def ensure_project_environment_via_api_or_db(self, root: Path, token: str, project_uuid: str):
            assert project_uuid == "project-local"
            return True, "environment ready"

    monkeypatch.setattr(module, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(module, "load_local_coolify_helper", lambda root: FakeLocalCoolify())

    context = module.ensure_local_coolify_context(plan, args)

    assert context["infra"] == "infra ready"
    assert args.coolify_url == "http://127.0.0.1:8000"
    assert args.coolify_token_env == ""
    assert args.coolify_token_file == str(token_path.resolve())
    assert args.coolify_project_uuid == "project-local"
    assert args.coolify_server_uuid == "server-local"
    assert args.coolify_destination_uuid == "destination-local"
    assert args.coolify_environment == "production"




def test_local_test_context_hands_helper_token_to_coolify_client_without_env(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("test")
    args = module.parse_args(["coolify-check", "test", "--quiet"])

    token_path = tmp_path / "runtime" / "startup-coolify" / "api-token.txt"

    class FakeLocalCoolify:
        LOCAL_PROJECT_ENVIRONMENT = "production"
        _RUNTIME_CONFIG: dict[str, object] = {}

        def api_token_file(self, root: Path) -> Path:
            return token_path

        def dashboard_url(self, root: Path) -> str:
            return "http://127.0.0.1:8123"

        def ensure_infra_status(self, root: Path):
            return True, "startup local Coolify infra ready"

        def ensure_api_token(self, root: Path):
            assert not token_path.exists()
            return True, "startup helper returned API token", "local-helper-token"

        def local_deploy_target_from_db(self, root: Path):
            return True, "startup target ready", {
                "server_uuid": "server-startup",
                "destination_uuid": "destination-startup",
            }

        def find_local_project_uuid_via_api(self, root: Path, token: str):
            assert token == "local-helper-token"
            return True, "startup project ready", "project-startup"

        def ensure_project_environment_via_api_or_db(self, root: Path, token: str, project_uuid: str):
            return True, "startup environment ready"

    monkeypatch.delenv(module.DEFAULT_COOLIFY_TOKEN_ENV, raising=False)
    monkeypatch.setattr(module, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(module, "load_local_coolify_helper", lambda root: FakeLocalCoolify())

    context = module.ensure_local_coolify_context(plan, args)
    token, token_source = module.resolve_coolify_token(args)

    assert context["token"] == "startup helper returned API token"
    assert token == "local-helper-token"
    assert token_source.startswith("local-helper:")
    assert args.coolify_token_env == ""
    assert args.coolify_token_file == str(token_path.resolve())

def test_local_test_context_reuses_startup_managed_local_coolify_runtime(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("test")
    args = module.parse_args(["coolify-sync", "test", "--quiet"])

    applications_env = tmp_path / "runtime" / "applications_service" / "applications.env"
    applications_env.parent.mkdir(parents=True)
    applications_env.write_text(
        "\n".join(
            [
                "COOLIFY_COMPOSE_PROJECT=main-computer-coolify-startup",
                "COOLIFY_LOCAL_STATE=runtime/startup-coolify",
                "APP_PORT=8123",
                "SOKETI_PORT=17123",
                "SOKETI_TERMINAL_PORT=17223",
                "COOLIFY_NETWORK_NAME=main-computer-coolify-startup_default",
                "COOLIFY_CONTAINER_NAME=mc-coolify-startup",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeLocalCoolify:
        LOCAL_PROJECT_ENVIRONMENT = "production"
        _RUNTIME_CONFIG: dict[str, object] = {}

        def api_token_file(self, root: Path) -> Path:
            state_dir = Path(str(self._RUNTIME_CONFIG.get("state_dir") or "runtime/coolify-local-docker"))
            if not state_dir.is_absolute():
                state_dir = root / state_dir
            return state_dir / "api-token.txt"

        def dashboard_url(self, root: Path) -> str:
            return f"http://127.0.0.1:{self._RUNTIME_CONFIG.get('app_port')}"

        def ensure_infra_status(self, root: Path):
            return True, "startup local Coolify infra ready"

        def ensure_api_token(self, root: Path):
            return True, "startup token ready", "local-token"

        def local_deploy_target_from_db(self, root: Path):
            return True, "startup target ready", {
                "server_uuid": "server-startup",
                "destination_uuid": "destination-startup",
            }

        def find_local_project_uuid_via_api(self, root: Path, token: str):
            return True, "startup project ready", "project-startup"

        def ensure_project_environment_via_api_or_db(self, root: Path, token: str, project_uuid: str):
            return True, "startup environment ready"

    fake = FakeLocalCoolify()
    monkeypatch.setattr(module, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(module, "load_local_coolify_helper", lambda root: fake)

    context = module.ensure_local_coolify_context(plan, args)

    assert fake._RUNTIME_CONFIG["project_name"] == "main-computer-coolify-startup"
    assert fake._RUNTIME_CONFIG["state_dir"] == "runtime/startup-coolify"
    assert args.coolify_url == "http://127.0.0.1:8123"
    assert args.coolify_token_file == str((tmp_path / "runtime" / "startup-coolify" / "api-token.txt").resolve())
    assert args.coolify_project_uuid == "project-startup"
    assert args.coolify_server_uuid == "server-startup"
    assert args.coolify_destination_uuid == "destination-startup"
    assert context["applications_runtime"]["app_port"] == "8123"
    assert context["infra"] == "startup local Coolify infra ready"


def test_local_test_context_does_not_start_fallback_coolify_stack_when_startup_stack_is_down(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("test")
    args = module.parse_args(["coolify-sync", "test", "--quiet"])

    applications_env = tmp_path / "runtime" / "applications_service" / "applications.env"
    applications_env.parent.mkdir(parents=True)
    applications_env.write_text(
        "COOLIFY_COMPOSE_PROJECT=main-computer-coolify-startup\n"
        "COOLIFY_LOCAL_STATE=runtime/startup-coolify\n"
        "APP_PORT=8123\n",
        encoding="utf-8",
    )

    class FakeLocalCoolify:
        _RUNTIME_CONFIG: dict[str, object] = {}
        up_called = False

        def api_token_file(self, root: Path) -> Path:
            return root / "runtime" / "startup-coolify" / "api-token.txt"

        def dashboard_url(self, root: Path) -> str:
            return "http://127.0.0.1:8123"

        def ensure_infra_status(self, root: Path):
            return False, "service \"coolify\" is not running"

        def up(self, root: Path, *, force_init: bool = False) -> int:
            self.up_called = True
            raise AssertionError("QBFT local test deploy must not start a second fallback local Coolify stack")

    fake = FakeLocalCoolify()
    monkeypatch.setattr(module, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(module, "load_local_coolify_helper", lambda root: fake)

    try:
        module.ensure_local_coolify_context(plan, args)
    except module.CoolifyError as exc:
        message = str(exc)
    else:
        raise AssertionError("missing startup-managed local Coolify should be reported as a blocking error")

    assert fake.up_called is False
    assert "startup-managed Coolify stack" in message
    assert "dashboard=http://127.0.0.1:8123" in message
    assert "service \"coolify\" is not running" in message
