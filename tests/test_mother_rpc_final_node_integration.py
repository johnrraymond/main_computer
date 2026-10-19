"""End-to-end command boundaries for final-node Mother removal (no network calls)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import mother_mutate_harness as harness_module
from tools import mother_deploy
from tools import mother_preflight_rpc_paranoia as preflight
from tools.mother.common import deployment_node_remove_do_v2 as removal


@pytest.mark.parametrize(
    "network,node,controller",
    [
        ("testnet", "testnetb-super1", "coolify-b"),
        ("mainnet", "mainneta-super1", "coolify-a"),
    ],
)
def test_final_node_command_exits_success_without_extra_flag(monkeypatch, capsys, network, node, controller):
    """The executable preflight must return 0 and preserve shutdown evidence."""
    sha = "b" * 64
    monkeypatch.setattr(preflight, "_load_topology", lambda *args, **kwargs: {
        "path": Path("current-topology.json"),
        "sha256": sha,
        "discovered": False,
        "services": [{"node": node, "controller_id": controller, "service_uuid": "uuid123"}],
    })
    code = preflight.main([
        "--runtime-state-root", ".", "--network", network,
        "--node", node,
        "--topology-evidence", "current-topology.json",
        "--acknowledge-topology-evidence-sha256", sha,
    ])
    output = capsys.readouterr().out
    assert code == 0, output
    assert "MOTHER_PREFLIGHT_RPC_PARANOIA_FINAL_NODE:" in output
    record = json.loads(output[output.index("{"):])
    assert record["status"] == "pass"
    assert record["summary"]["clean"] is True
    assert record["summary"]["survivor_count"] == 0
    assert record["summary"]["post_remove_rpc_probe_required"] is False
    assert record["summary"]["rpc_continuity_check_required"] is False
    assert record["rpc_continuity"]["node_hosted_rpc_unavailable_post_remove"] is True
    assert record["rpc_continuity"]["final_node_shutdown_acknowledged"] is False
    assert record["policy"]["network_mutation_performed"] is False


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
@pytest.mark.parametrize(
    "location,override,shutdown,expected",
    [
        ("empty", False, False, "network-shutdown"),
        ("empty", False, True, "network-shutdown"),  # Legacy acknowledgement accepted.
        ("local", False, False, "same-controller-survivor"),
        ("local", True, False, "same-controller-survivor"),
        ("remote", True, False, "remote-controller-survivor"),
        ("local-and-remote", False, False, "same-controller-survivor"),
    ],
)
def test_preflight_executor_agree_on_every_allowed_topology(network, location, override, shutdown, expected):
    target = {"node": f"{network}b-super1", "controller_id": "coolify-b", "service_uuid": "b1"}
    local = {"node": f"{network}b-super2", "controller_id": "coolify-b", "service_uuid": "b2"}
    remote = {"node": f"{network}c-super1", "controller_id": "coolify-c", "service_uuid": "c1"}
    survivors = {
        "empty": [], "local": [local], "remote": [remote],
        "local-and-remote": [local, remote],
    }[location]
    check = preflight.evaluate_rpc_remove_boundary(
        services=[target, *survivors], node=target["node"],
        rpc_will_work_post_remove=override,
        acknowledge_final_node_shutdown=shutdown,
    )
    assert check["clean"] is True
    execution = removal._rpc_removal_boundary(
        target, survivors, rpc_will_work_post_remove=override,
        acknowledge_final_node_shutdown=shutdown,
    )
    assert execution["case"] == expected
    assert check["post_remove_node_count"] == execution["network_survivor_count"]
    assert check["final_node_shutdown_required"] is (expected == "network-shutdown")
    assert check["acknowledgement_required"] is (expected == "remote-controller-survivor")
    assert check["post_remove_rpc_probe_required"] is (expected == "remote-controller-survivor")
    assert execution["network_shutdown_acknowledged"] is shutdown


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
@pytest.mark.parametrize("location,override,shutdown", [
    ("empty", True, False),  # Can't promise RPC on a network with no nodes.
    ("empty", True, True),
    ("remote", False, False),  # Can't leave a host without route override.
    ("local", False, True),  # Can't claim a network shutdown with surviving nodes.
    ("remote", True, True),
])
def test_preflight_executor_both_reject_unsafe_topology_claims(network, location, override, shutdown):
    target = {"node": f"{network}b-super1", "controller_id": "coolify-b", "service_uuid": "b1"}
    survivors = {
        "empty": [],
        "local": [{"node": f"{network}b-super2", "controller_id": "coolify-b", "service_uuid": "b2"}],
        "remote": [{"node": f"{network}c-super1", "controller_id": "coolify-c", "service_uuid": "c1"}],
    }[location]
    check = preflight.evaluate_rpc_remove_boundary(
        services=[target, *survivors], node=target["node"],
        rpc_will_work_post_remove=override,
        acknowledge_final_node_shutdown=shutdown,
    )
    assert check["clean"] is False
    with pytest.raises(removal.MotherDeploymentNodeRemoveDoError):
        removal._rpc_removal_boundary(
            target, survivors, rpc_will_work_post_remove=override,
            acknowledge_final_node_shutdown=shutdown,
        )


@pytest.mark.parametrize("network,node,controller", [
    ("mainnet", "mainneta-super1", "coolify-a"),
    ("testnet", "testnetb-super1", "coolify-b"),
])
def test_harness_runs_passed_rpc_preflight_and_forwards_no_override(network, node, controller):
    args = harness_module.build_parser().parse_args([
        "remove-node", "--network", network, "--node", node, "--host", controller,
    ])
    h = object.__new__(harness_module.Harness)
    h.args = args
    h.repo_root = Path("C:/repo")
    h.state = {
        "baseline_evidence": "current-topology.json",
        "baseline_evidence_sha256": "a" * 64,
        "remove_do_release": "release.json", "remove_do_release_sha256": "b" * 64,
    }
    calls = []
    def fake_run(name, cmd, **kwargs):
        calls.append((name, cmd, kwargs))
        if name == "preflight-rpc-paranoia":
            return {"status": "pass", "summary": {"clean": True, "survivor_count": 0}}
        return {"evidence": {"path": "evidence.json", "sha256": "c" * 64}}
    h.run = fake_run
    h.cmd = lambda *args: list(args)
    h.step_preflight_rpc_paranoia()
    h.step_execute_remove_do()
    assert [item[0] for item in calls] == ["preflight-rpc-paranoia", "execute-remove-do"]
    assert "--network" in calls[0][1] and network in calls[0][1]
    assert "--acknowledge-final-node-shutdown" not in calls[0][1]
    assert "--rpc-will-work-post-remove" not in calls[0][1]
    assert "--acknowledge-final-node-shutdown" not in calls[1][1]
    assert "--rpc-will-work-post-remove" not in calls[1][1]
    assert "--execute" in calls[1][1]
    assert mother_deploy._parser().parse_args([
        "remove-node", "do", network, "--release", "r.json",
        "--acknowledge-release-sha256", "a" * 64, "--execute",
    ]).acknowledge_final_node_shutdown is False
