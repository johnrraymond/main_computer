"""The same route-writing and node-removal code must bind both Mother networks."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools import mother_rpc_route_ensure as cli
from tools.mother.common import deployment_validator_rpc_canary_funding as writer
from tools.mother.common import deployment_node_remove_do_v2 as remove


def mother(network, *, published=True, host=None):
    chain = 42424240 if network == "mainnet" else 42424241
    controller = "coolify-a" if network == "mainnet" else "coolify-b"
    node = "mainneta-super1" if network == "mainnet" else "testnetb-super1"
    record = {
        "chain_id": chain,
        "nodes": {node: {"host": controller}},
        "deployment": {"targets": {node: {"controller_ref": f"networks.{network}.coolify.controllers.{controller}"}}},
        "coolify": {"mutation_authority": "observe-only", "controllers": {controller: {
            "enabled": True, "project_uuid": "project-uuid", "server_uuid": "server-uuid",
        }}},
    }
    if published:
        record["rpc_route"] = {"host": host or f"{network}-rpc.example.org"}
    payload = json.dumps({"networks": {network: record}}).encode()
    return SimpleNamespace(canonical_object_bytes=payload, document_bytes=payload)


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_explicit_route_uses_exact_network_contract_for_ensure_and_remove(network):
    private = mother(network)
    node = f"{network}{'a' if network == 'mainnet' else 'b'}-super1"
    controller = "coolify-a" if network == "mainnet" else "coolify-b"
    route = writer.resolve_network_rpc_route(private, network)
    plan = cli.route_plan(private, network=network, node=node, controller_id=controller)
    assert route["chain_id"] == plan["expected_chain_id"]
    assert route["host"] == plan["host"]
    assert plan["backend_url"] == f"http://{node}:8545"
    assert remove._rpc_route_operation(private, network) == "rewire-shared"
    with pytest.raises(cli.RpcRouteEnsureError):
        cli.route_plan(private, network=network, node=node, controller_id="coolify-unrelated")


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_implicit_route_uses_same_writer_and_network_specific_host(network, monkeypatch):
    private = mother(network, published=False)
    node = "mainneta-super1" if network == "mainnet" else "testnetb-super1"
    controller = "coolify-a" if network == "mainnet" else "coolify-b"
    contract = writer.resolve_network_rpc_route(private, network)
    assert contract["host"] == f"{network}-rpc.greatlibrary.io"
    assert contract["implicit"] is True
    assert remove._rpc_route_operation(private, network) == "rewire-shared"
    seen = []
    def stub(_private, **kwargs):
        seen.append(kwargs)
        return {"status": "pass", "proof": {"healthy": True}, "cleanup": {"deleted": True}}
    monkeypatch.setattr(cli, "execute_shared_rpc_route_rewire", stub)
    result = cli.ensure_route(private, network=network, node=node, controller_id=controller, execute=True)
    assert result["ensured"] is True
    assert result["route"]["host"] == contract["host"]
    assert seen[0]["controller_id"] == controller
    assert seen[0]["target_node"] == node
    if network == "testnet":
        assert seen[0]["network"] == "testnet"
    else:
        assert "network" not in seen[0]  # Preserve legacy mainnet executor arguments.


def test_explicit_unpublished_is_not_silent_add_node_success(monkeypatch):
    private = mother("testnet")
    document = json.loads(private.canonical_object_bytes)
    document["networks"]["testnet"]["rpc_route"] = {"published": False}
    private.canonical_object_bytes = json.dumps(document).encode()
    private.document_bytes = private.canonical_object_bytes
    assert writer.resolve_network_rpc_route(private, "testnet") is None
    assert remove._rpc_route_operation(private, "testnet") == "already-unpublished"
    monkeypatch.setattr(cli, "execute_shared_rpc_route_rewire", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("no write")))
    with pytest.raises(cli.RpcRouteEnsureError, match="explicitly unpublished"):
        cli.ensure_route(private, network="testnet", node="testnetb-super1", controller_id="coolify-b", execute=True)


def test_mainnet_legacy_implicit_route_retains_existing_host_and_writer_bytes():
    private = mother("mainnet", published=False)
    route = writer.resolve_network_rpc_route(private, "mainnet")
    assert route["host"] == "mainnet-rpc.greatlibrary.io"
    assert route["implicit"] is True
    assert remove._rpc_route_operation(private, "mainnet") == "rewire-shared"
    target = {
        "controller_id": "coolify-a", "route_host": "mainnet-rpc.greatlibrary.io",
        "target_host": "mainneta-super1", "target_port": 8545,
        "target_url": "http://mainneta-super1:8545",
        "expected_chain_id": 42424240,
        "dynamic_file": "mother-mainnet-rpc-route-coolify-a.yml",
        "legacy_dynamic_file": "main-computer-mainnet-rpc-public-entry-a.yml",
    }
    unchanged = writer._route_writer_script(target)
    explicit_mainnet = writer._route_writer_script({**target, "network": "mainnet"})
    assert unchanged == explicit_mainnet
    assert writer._route_writer_application_body({"project_uuid": "p", "server_uuid": "s"}, "n", "compose")["environment_name"] == "mainnet"
    assert "mother-mainnet-rpc-coolify-a-http:" in writer._render_shared_rpc_traefik_dynamic_config(target)


def test_testnet_route_writer_configuration_is_network_isolated():
    target = {
        "network": "testnet", "controller_id": "coolify-b", "route_host": "testnet-rpc.example.org",
        "target_host": "testnetb-super1", "target_port": 8545,
        "target_url": "http://testnetb-super1:8545", "expected_chain_id": 42424241,
        "dynamic_file": "mother-testnet-rpc-route-coolify-b.yml",
        "legacy_dynamic_file": None,
    }
    dynamic = writer._render_shared_rpc_traefik_dynamic_config(target)
    script = writer._route_writer_script(target)
    assert "mother-testnet-rpc-coolify-b-http:" in dynamic
    assert "mother-mainnet" not in dynamic and "mainnet-rpc" not in script
    assert repr(hex(42424241)) in script
    compile(script, "<testnet-rpc-route-writer>", "exec")
    assert "mother-testnet-rpc-route-coolify-b.yml" in script
    assert "rm -f '{LEGACY_TARGET_PATH}'" not in script
    assert "main-computer-mainnet" not in script
    body = writer._route_writer_application_body({"project_uuid": "p", "server_uuid": "s"}, "n", "compose", network="testnet")
    assert body["environment_name"] == "testnet"
    assert body["project_uuid"] == "p"
    assert body["server_uuid"] == "s"


def test_published_testnet_invokes_same_writer_with_network(monkeypatch):
    private = mother("testnet")
    seen = []
    def stub(_private, **kwargs):
        seen.append(kwargs)
        return {"status": "pass", "proof": {"healthy": True}, "cleanup": {"deleted": True}}
    monkeypatch.setattr(cli, "execute_shared_rpc_route_rewire", stub)
    result = cli.ensure_route(private, network="testnet", node="testnetb-super1", controller_id="coolify-b", execute=True)
    assert result["ensured"] is True
    assert seen[0]["network"] == "testnet"
    assert seen[0]["controller_id"] == "coolify-b"
    assert seen[0]["target_node"] == "testnetb-super1"


def test_published_route_rejects_unknown_or_conflicting_network_identity():
    private = mother("testnet")
    with pytest.raises(writer.MotherDeploymentValidatorRpcCanaryFundingError):
        writer.resolve_network_rpc_route(private, "mainnet")
    with pytest.raises(writer.MotherDeploymentValidatorRpcCanaryFundingError):
        writer.resolve_network_rpc_node_controller(private, network="testnet", node="mainneta-super1")
    invalid = mother("testnet", host="bad.example.org;rm")
    with pytest.raises(writer.MotherDeploymentValidatorRpcCanaryFundingError):
        writer.resolve_network_rpc_route(invalid, "testnet")

@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_same_writer_binds_live_coolify_environment_and_isolated_files(monkeypatch, network):
    private = mother(network)
    node = f"{network}{'a' if network == 'mainnet' else 'b'}-super1"
    controller_id = "coolify-a" if network == "mainnet" else "coolify-b"
    calls = {}
    monkeypatch.setattr(writer, "resolve_coolify_controller", lambda _p, n, c: (calls.setdefault("controller_resolution", (n,c)) or object()))
    monkeypatch.setattr(writer, "_resolve_environment_uuid", lambda **kwargs: (calls.setdefault("environment", kwargs["expected_name"]) or "test-env-uuid"))
    def fake_request_mutation(**kwargs):
        calls.setdefault("mutations", []).append(kwargs)
        kwargs["receipts"].append({"method":kwargs["method"]})
        return {"payload": {"uuid": "temporary-route-writer"}}
    monkeypatch.setattr(writer, "_request_mutation", fake_request_mutation)
    monkeypatch.setattr(writer, "_application_uuid", lambda payload: payload["uuid"])
    monkeypatch.setattr(writer, "_wait_for_service_health", lambda **kwargs: {"healthy":True, "status":"running:healthy"})
    monkeypatch.setattr(writer, "_http", lambda *_args, **_kwargs: {"status":200})
    monkeypatch.setattr(writer, "_receipt", lambda **kwargs: {"method":kwargs["method"]})
    # The same executor handles both route configurations and cleans up its temporary writer.
    result = writer.execute_shared_rpc_route_rewire(
        private, network=network, controller_id=controller_id, target_node=node,
    )
    assert result["status"] == "pass"
    assert result["cleanup"]["deleted"]
    assert calls["controller_resolution"] == (network, controller_id)
    assert calls["environment"] == network
    import base64
    create = calls["mutations"][0]["body"]
    assert create["environment_name"] == network
    compose = base64.b64decode(create["docker_compose_raw"]).decode()
    if network == "testnet":
        assert "mainnet-rpc" not in compose
        assert "testnet-rpc.example.org" in compose
    else:
        assert "mother-mainnet-rpc-route-a" in compose
    assert [m["method"] for m in calls["mutations"]] == ["POST", "POST"]


def test_invalid_controller_id_does_not_write_mixed_network_routes():
    private = mother("testnet")
    with pytest.raises(writer.MotherDeploymentValidatorRpcCanaryFundingError):
        writer.execute_shared_rpc_route_rewire(private, network="testnet", controller_id="coolify-a", target_node="testnetb-super1")
