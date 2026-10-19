"""Network-neutral RPC preflight behavior for intentional final-node removal."""

from __future__ import annotations

from pathlib import Path

import pytest

import mother_mutate_harness
from tools import mother_preflight_rpc_paranoia as rpc


SOLO = [{"node": "testnetb-super1", "controller_id": "coolify-b", "service_uuid": "b-uuid"}]
MULTI = SOLO + [{"node": "mainnetc-super1", "controller_id": "coolify-c", "service_uuid": "c-uuid"}]


def _evaluate(services, **kwargs):
    return rpc.evaluate_rpc_remove_boundary(
        services=services,
        node="testnetb-super1",
        rpc_will_work_post_remove=kwargs.get("rpc_will_work_post_remove", False),
        acknowledge_final_node_shutdown=kwargs.get("acknowledge_final_node_shutdown", False),
    )


@pytest.mark.parametrize("network", ["testnet", "mainnet"])
def test_last_network_node_passes_without_extra_override(network):
    # This boundary is intentionally network-neutral; service membership is
    # already scoped to the selected network by the topology loader.
    node = "testnetb-super1" if network == "testnet" else "mainneta-super1"
    service = {"node": node, "controller_id": "coolify-b", "service_uuid": "b-uuid"}
    result = rpc.evaluate_rpc_remove_boundary(
        services=[service], node=node, rpc_will_work_post_remove=False,
    )
    assert result["status"] == "pass"
    assert result["clean"] is True
    assert result["final_node_shutdown_required"] is True
    assert result["final_node_shutdown_acknowledged"] is False
    assert result["post_remove_rpc_probe_required"] is False
    assert result["rpc_continuity_check_required"] is False


def test_explicit_last_node_decommission_passes_without_claiming_rpc_continuity():
    result = _evaluate(SOLO, acknowledge_final_node_shutdown=True)
    assert result["status"] == "pass"
    assert result["clean"] is True
    assert result["survivors"] == []
    assert result["post_remove_rpc_probe_required"] is False
    assert result["rpc_will_work_post_remove_acknowledged"] is False
    assert "unavailable" in result["message"]


def test_last_node_ack_does_not_exempt_multinode_removal():
    result = _evaluate(MULTI, acknowledge_final_node_shutdown=True)
    assert result["status"] == "final-node-shutdown-ack-misapplied"
    assert result["clean"] is False


@pytest.mark.parametrize("shutdown_ack", [False, True])
def test_final_network_node_rejects_false_continuity_claim(shutdown_ack):
    result = _evaluate(
        SOLO, acknowledge_final_node_shutdown=shutdown_ack, rpc_will_work_post_remove=True
    )
    assert result["clean"] is False
    assert result["code"] == "MOTHER_PREFLIGHT_RPC_PARANOIA_FINAL_NODE_ACK_CONFLICT"


def test_existing_multinode_continuity_semantics_unchanged():
    result = _evaluate(MULTI)
    assert result["status"] == "rpc-post-remove-ack-required"
    assert result["clean"] is False
    result = _evaluate(MULTI, rpc_will_work_post_remove=True)
    assert result["status"] == "pass"
    assert result["clean"] is True
    assert result["post_remove_rpc_probe_required"] is True


def test_same_controller_survivor_keeps_existing_pass():
    second = {"node": "testnetb-super2", "controller_id": "coolify-b", "service_uuid": "b2"}
    result = _evaluate(SOLO + [second])
    assert result["status"] == "pass"
    assert result["acknowledgement_required"] is False


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_rpc_output_explicitly_records_shutdown_and_no_mutation(monkeypatch, network):
    fake = {
        "path": Path("topology.json"),
        "sha256": "a" * 64,
        "discovered": False,
        "services": SOLO,
    }
    def fake_load(root, *, network: str, topology_evidence, acknowledged_sha256):
        assert network == requested_network
        assert acknowledged_sha256 == "a" * 64
        return fake
    requested_network = network
    monkeypatch.setattr(rpc, "_load_topology", fake_load)
    result = rpc.run_preflight_rpc_paranoia(
        runtime_state_root=".",
        network=network,
        node="testnetb-super1",
        topology_evidence="topology.json",
        acknowledged_topology_evidence_sha256="a" * 64,
    )
    assert result["status"] == "pass"
    assert result["rpc_continuity"]["node_hosted_rpc_unavailable_post_remove"] is True
    assert result["rpc_continuity"]["rpc_continuity_check_required"] is False
    assert result["rpc_continuity"]["final_node_shutdown_acknowledged"] is False
    assert result["rpc_continuity"]["dns_or_upstream_routing_verified_by_this_script"] is False
    assert result["policy"]["network_mutation_performed"] is False


def test_harness_passes_explicit_flag_without_changing_existing_command():
    args = mother_mutate_harness.build_parser().parse_args([
        "remove-node", "--network", "testnet", "--node", "testnetb-super1", "--host", "coolify-b",
    ])
    harness = object.__new__(mother_mutate_harness.Harness)
    harness.repo_root = Path("C:/repo")
    harness.args = args
    harness.state = {"baseline_evidence": "topology.json", "baseline_evidence_sha256": "a" * 64}
    original = harness.preflight_rpc_paranoia_cmd()
    assert "--acknowledge-final-node-shutdown" not in original
    args.acknowledge_final_node_shutdown = True
    amended = harness.preflight_rpc_paranoia_cmd()
    assert amended[:-1] == original
    assert amended[-1] == "--acknowledge-final-node-shutdown"


def test_cli_flag_is_explicit_not_default():
    argv = ["--runtime-state-root", ".", "--network", "testnet", "--node", "testnetb-super1"]
    assert rpc._build_parser().parse_args(argv).acknowledge_final_node_shutdown is False
    assert rpc._build_parser().parse_args(argv + ["--acknowledge-final-node-shutdown"]).acknowledge_final_node_shutdown is True
