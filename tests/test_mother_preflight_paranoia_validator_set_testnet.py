"""Unpublished testnet sole-node decommission and mainnet RPC isolation."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import mother_preflight_paranoia_validator_set as mod

NODE = "testnetb-super1"
VALIDATOR = "0xcef717b0331b7583f247d876b80ad3b4c3392adb"
UUID = "x9amaa6trwtrsu1m8dcce7fd"


def _state():
    return SimpleNamespace(binding=SimpleNamespace(
        generation=23,
        content_hash=SimpleNamespace(digest="1" * 64),
        recovery_manifest_hash=SimpleNamespace(digest="2" * 64),
    ))


def _receipt():
    return {
        "kind": "main_computer.mother.live_current_topology_evidence.v1",
        "network": "testnet", "status": "pass",
        "mother_binding": {"generation": 23, "content_sha256": "1" * 64,
                           "manifest_sha256": "2" * 64},
        "next_phase": "topology-baseline-ready-testnet", "failure": None,
        "live_mutation_performed": False, "chain_mutation_performed": False,
        "routing_or_topology_published": False, "public_endpoint_created": False,
        "summary": {
            "clean": True, "complete": True, "live_topology_sealed": True,
            "use_live_topology": True, "current_topology_marked_by_evidence": True,
            "topology_current": True, "topology_stale": False,
            "node_identity_preserved": True,
            "validator_identities_selected_from_source_topology": True,
            "live_coolify_primary_services_verified": True,
            "final_validator_count": 1,
        },
        "policy": {key: False for key in (
            "live_mutation_performed", "finalize_mutation_performed",
            "chain_mutation_performed", "validator_admission_performed",
            "validator_vote_performed", "routing_or_topology_published",
            "public_http_endpoint_created", "private_keys_materialized", "private_keys_persisted",
        )},
        "final_topology": {
            "chain_id": 42424241, "genesis_sha256": "a" * 64,
            "nodes": [NODE], "validator_set": [VALIDATOR],
            "services": {NODE: {"service_uuid": UUID, "controller_id": "coolify-b"}},
        },
    }


def _prove(monkeypatch, *, receipt=None, state=None, live_nodes=None, expected=None, nodes=None, net=None):
    monkeypatch.setattr(mod, "list_coolify_controllers", lambda _state: [
        SimpleNamespace(network="testnet", enabled=True, controller_id="coolify-b")])
    monkeypatch.setattr(mod, "_validator_node_map", lambda doc, network: {VALIDATOR: NODE})
    monkeypatch.setattr(mod, "_live_primary_nodes", lambda *a, **kw: [NODE] if live_nodes is None else live_nodes)
    return mod._sole_unpublished_node_preflight(
        private_state=state or _state(), network=net or "testnet",
        network_doc={"chain_id": 42424241}, topology_doc=receipt or _receipt(),
        topology_nodes=[NODE] if nodes is None else nodes,
        expected=[VALIDATOR] if expected is None else expected,
        topology_path=Path("/tmp/current-topology.json"), topology_sha256="3" * 64,
        auto_selected=False, timeout=3.0, max_response_bytes=100000,
        overall_timeout=10.0,
    )


def test_cli_supports_both_networks_without_changing_default():
    parser = mod.build_parser()
    assert parser.parse_args([]).network == "mainnet"
    assert parser.parse_args(["--network", "mainnet"]).network == "mainnet"
    assert parser.parse_args(["--network", "testnet"]).network == "testnet"


def test_no_testnet_route_is_not_mainnet_route():
    assert mod._network_rpc_route_url({"chain_id": 42424241}, "testnet") is None
    assert mod._network_rpc_route_url({"rpc_route": {}}, "testnet") is None
    assert mod._network_rpc_route_url({"rpc_route": {"hostname": "testnet-rpc.example.net"}}, "testnet") == "https://testnet-rpc.example.net"
    with pytest.raises(mod.ValidatorSetParanoiaError, match="valid host"):
        mod._network_rpc_route_url({"rpc_route": {"hostname": "mainnet-rpc.example.net/path"}}, "testnet")


def test_mainnet_shared_route_fallback_is_unchanged():
    assert mod._network_rpc_route_url({}, "mainnet") == mod._shared_rpc_route_url({})


def test_unpublished_testnet_rpc_never_queries_mainnet(monkeypatch):
    monkeypatch.setattr(mod, "_rpc", lambda *a, **kw: pytest.fail("RPC must not be queried"))
    with pytest.raises(mod.ValidatorSetParanoiaError, match="no published RPC route"):
        mod._observe_live_chain(object(), network="testnet", network_doc={"chain_id": 42424241},
                                explicit_rpc_url=None, timeout=1, max_response_bytes=1000)


def test_testnet_direct_override_has_no_mainnet_fallback(monkeypatch):
    methods = []
    def fake_rpc(url, method, *args, **kwargs):
        methods.append((url, method))
        return {"eth_chainId": "0x28757b1", "qbft_getValidatorsByBlockNumber": [VALIDATOR],
                "eth_blockNumber": "0x10"}[method]
    monkeypatch.setattr(mod, "_rpc", fake_rpc)
    result = mod._observe_live_chain(object(), network="testnet", network_doc={"chain_id": 42424241},
                                     explicit_rpc_url="http://127.0.0.1:8545", timeout=1,
                                     max_response_bytes=1000)
    assert result["authority_source"] == "explicit"
    assert result["public_rpc_url"] is None
    assert all(url == "http://127.0.0.1:8545" for url, _ in methods)


def test_sole_node_read_only_decommission_is_explicitly_not_rpc_verification(monkeypatch):
    evidence = _prove(monkeypatch)
    assert evidence["status"] == "pass" and evidence["summary"]["clean"]
    assert evidence["preflight_mode"] == "unpublished-route-sole-node-decommission"
    assert evidence["rpc_observation_performed"] is False
    assert evidence["live_validator_set"] is None
    assert evidence["sole_service_uuid"] == UUID
    assert evidence["live_primary_nodes"] == [NODE]


@pytest.mark.parametrize("mutate,match", [
    (lambda e: e.update(kind="other"), "finalized live-current-topology"),
    (lambda e: e["mother_binding"].update(generation=22), "current verified Mother"),
    (lambda e: e["summary"].update(clean=False), "finalized-topology contract"),
    (lambda e: e["policy"].update(validator_vote_performed=True), "finalized-topology contract"),
    (lambda e: e.update(status="failed"), "finalized-topology contract"),
    (lambda e: e["final_topology"].update(chain_id=42424240), "chain ID"),
    (lambda e: e["final_topology"].update(genesis_sha256=""), "genesis hash"),
    (lambda e: e["final_topology"]["services"].clear(), "missing service"),
    (lambda e: e["final_topology"]["services"][NODE].update(controller_id="coolify-a"), "controller"),
])
def test_sole_node_rejects_invalid_current_state(monkeypatch, mutate, match):
    evidence = _receipt()
    mutate(evidence)
    with pytest.raises(mod.ValidatorSetParanoiaError, match=match):
        _prove(monkeypatch, receipt=evidence)


def test_sole_node_rejects_multi_node_and_mainnet(monkeypatch):
    with pytest.raises(mod.ValidatorSetParanoiaError, match="multi-node"):
        _prove(monkeypatch, nodes=[NODE, "testnetb-super2"], expected=[VALIDATOR, "0x" + "3" * 40])
    with pytest.raises(mod.ValidatorSetParanoiaError, match="mainnet requires"):
        _prove(monkeypatch, net="mainnet")


def test_sole_node_rejects_missing_or_extra_coolify_primaries(monkeypatch):
    for nodes in ([], [NODE, "testnetb-super2"], ["testnetb-super2"]):
        with pytest.raises(mod.ValidatorSetParanoiaError, match="live Coolify primary nodes"):
            _prove(monkeypatch, live_nodes=nodes)


def test_run_testnet_sole_node_does_not_invoke_shared_mainnet_rpc(monkeypatch, tmp_path):
    receipt = _receipt()
    private_state = _state()
    path = tmp_path / "live-topology.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mod, "_resolve_topology_arguments", lambda args: (path, "a" * 64, False))
    monkeypatch.setattr(mod, "_load_topology", lambda *args, **kwargs: (
        path, "a" * 64, receipt, [NODE], [VALIDATOR],
    ))
    monkeypatch.setattr(mod, "read_private_state", lambda *args, **kwargs: private_state)
    monkeypatch.setattr(mod, "network_document", lambda *args: {
        "chain_id": 42424241,
    })
    monkeypatch.setattr(mod, "_validator_node_map", lambda *a, **kw: {VALIDATOR: NODE})
    monkeypatch.setattr(mod, "_live_primary_nodes", lambda *a, **kw: [NODE])
    monkeypatch.setattr(mod, "list_coolify_controllers", lambda _state: [
        SimpleNamespace(network="testnet", enabled=True, controller_id="coolify-b")
    ])
    monkeypatch.setattr(mod, "_rpc", lambda *a, **kw: pytest.fail("no public RPC must be probed"))
    args = mod.build_parser().parse_args([
        "--network", "testnet", "--runtime-state-root", str(tmp_path),
        "--topology-evidence", str(path), "--acknowledge-topology-evidence-sha256", "a" * 64,
    ])
    output = mod.run(args)
    assert output["status"] == "pass"
    assert output["rpc_observation_performed"] is False
    assert output["single_node_decommission"] is True
