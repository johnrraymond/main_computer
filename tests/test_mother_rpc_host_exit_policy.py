"""One network-scoped RPC host-exit rule across preflight, do, and resume."""
from __future__ import annotations

from pathlib import Path
import pytest

import mother_mutate_harness
from tools import mother_deploy
from tools import mother_preflight_rpc_paranoia as pre
from tools.mother.common import deployment_node_remove_do_v2 as executor

B1 = {"node": "testnetb-super1", "controller_id": "coolify-b", "service_uuid": "b1"}
B2 = {"node": "testnetb-super2", "controller_id": "coolify-b", "service_uuid": "b2"}
C1 = {"node": "testnetc-super1", "controller_id": "coolify-c", "service_uuid": "c1"}

@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_final_network_node_has_no_rpc_continuity_check(network):
    decision = pre.evaluate_rpc_remove_boundary(
        services=[B1], node=B1["node"], rpc_will_work_post_remove=False,
    )
    assert decision["clean"]
    assert decision["acknowledgement_required"] is False
    assert decision["post_remove_rpc_probe_required"] is False
    assert decision["final_node_shutdown_required"] is True
    policy = executor._rpc_removal_boundary(
        B1, [], rpc_will_work_post_remove=False, acknowledge_final_node_shutdown=False,
    )
    assert policy["case"] == "network-shutdown"
    assert policy["network_shutdown_acknowledged"] is False

@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_host_exit_with_remote_survivor_requires_override(network):
    failed = pre.evaluate_rpc_remove_boundary(
        services=[B1,C1], node=B1["node"], rpc_will_work_post_remove=False,
    )
    assert not failed["clean"] and failed["acknowledgement_required"] is True
    with pytest.raises(executor.MotherDeploymentNodeRemoveDoError, match="explicit post-remove continuity override required"):
        executor._rpc_removal_boundary(
            B1, [C1], rpc_will_work_post_remove=False, acknowledge_final_node_shutdown=False,
        )
    passing = pre.evaluate_rpc_remove_boundary(
        services=[B1,C1], node=B1["node"], rpc_will_work_post_remove=True,
    )
    assert passing["clean"]
    policy = executor._rpc_removal_boundary(
        B1, [C1], rpc_will_work_post_remove=True, acknowledge_final_node_shutdown=False,
    )
    assert policy["case"] == "remote-controller-survivor"
    assert policy["rpc_continuity_verified_by_live_probe"] is False

@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_same_host_survivor_requires_no_override(network):
    d = pre.evaluate_rpc_remove_boundary(
        services=[B1,B2,C1], node=B1["node"], rpc_will_work_post_remove=False,
    )
    assert d["clean"] and d["acknowledgement_required"] is False
    p = executor._rpc_removal_boundary(
        B1,[B2,C1],rpc_will_work_post_remove=False,acknowledge_final_node_shutdown=False,
    )
    assert p["case"] == "same-controller-survivor"

@pytest.mark.parametrize("case,services,continuity,shutdown", [
    ("invalid-shutdown", [B1,C1], False, True),
    ("contradictory", [B1], True, True),
])
def test_bad_override_combinations_fail_preflight_and_execution(case,services,continuity,shutdown):
    decision = pre.evaluate_rpc_remove_boundary(
        services=services, node=B1["node"],
        rpc_will_work_post_remove=continuity,
        acknowledge_final_node_shutdown=shutdown,
    )
    assert not decision["clean"]
    with pytest.raises(executor.MotherDeploymentNodeRemoveDoError):
        executor._rpc_removal_boundary(
            B1, services[1:], rpc_will_work_post_remove=continuity,
            acknowledge_final_node_shutdown=shutdown,
        )

def test_execution_cli_and_harness_forward_same_override_flags():
    cli = mother_deploy._parser().parse_args([
        "remove-node", "do", "testnet", "--release", "r.json",
        "--acknowledge-release-sha256", "a" * 64,
        "--rpc-will-work-post-remove", "--execute",
    ])
    assert cli.rpc_will_work_post_remove
    args = mother_mutate_harness.build_parser().parse_args([
        "remove-node", "--network", "testnet", "--node", "testnetb-super1",
        "--host", "coolify-b", "--rpc-will-work-post-remove",
    ])
    h = object.__new__(mother_mutate_harness.Harness)
    h.repo_root = Path("C:/repo")
    h.args=args
    h.state={"baseline_evidence":"t.json","baseline_evidence_sha256":"a"*64,
             "remove_do_release":"r.json","remove_do_release_sha256":"b"*64}
    cmd = h.preflight_rpc_paranoia_cmd()
    assert "--rpc-will-work-post-remove" in cmd
    # Unlike old code, the do stage explicitly forwards the same authorization.
    calls = []
    def fake_run(label, cmd, **kwargs):
        calls.append((label,cmd))
        return {"evidence":{"path":"e.json","sha256":"c"*64}}
    h.run = fake_run
    h.cmd=lambda *args: list(args)
    h.step_execute_remove_do()
    assert "--rpc-will-work-post-remove" in calls[0][1]


def test_redundant_operator_continuity_flag_does_not_change_existing_local_flow():
    decision = pre.evaluate_rpc_remove_boundary(
        services=[B1, B2], node=B1["node"], rpc_will_work_post_remove=True,
    )
    assert decision["clean"] and not decision["acknowledgement_required"]
    policy = executor._rpc_removal_boundary(
        B1, [B2], rpc_will_work_post_remove=True, acknowledge_final_node_shutdown=False,
    )
    assert policy["case"] == "same-controller-survivor"
