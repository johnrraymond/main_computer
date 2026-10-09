"""RPC route publication is a deployment invariant, not an RPC canary dependency."""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

import mother_mutate_harness as harness
from tools import mother_rpc_route_ensure as routes


PRIVATE = SimpleNamespace(
    canonical_object_bytes=(
        b'{"networks":{"mainnet":{"chain_id":42424240,'
        b'"nodes":{"mainneta-super1":{"host":"coolify-a"},'
        b'"mainnetc-super2":{"host":"coolify-c"}}}}}'
    )
)


def test_route_plan_is_independent_of_paranoia_or_canary() -> None:
    plan = routes.route_plan(PRIVATE, network="mainnet", node="mainnetc-super2", controller_id="coolify-c")
    assert plan["backend_url"] == "http://mainnetc-super2:8545"
    assert plan["url"] == "https://mainnet-rpc.greatlibrary.io"
    assert plan["expected_chain_id"] == 42424240
    with pytest.raises(routes.RpcRouteEnsureError):
        routes.route_plan(PRIVATE, network="mainnet", node="mainnetc-super2", controller_id="coolify-a")
    with pytest.raises(routes.RpcRouteEnsureError):
        routes.route_plan(PRIVATE, network="mainnet", node="dangerous;command", controller_id="coolify-c")


def test_dry_run_does_not_write_route(monkeypatch) -> None:
    def unexpected(*args, **kwargs):
        raise AssertionError("no writer should run")
    monkeypatch.setattr(routes, "execute_shared_rpc_route_rewire", unexpected)
    result = routes.ensure_route(PRIVATE, network="mainnet", node="mainneta-super1", controller_id="coolify-a", execute=False)
    assert result["clean"] is True and result["ensured"] is False


def test_execute_requires_verified_route_and_ephemeral_helper_cleanup(monkeypatch) -> None:
    seen = []
    def fake_writer(private_state, **kwargs):
        seen.append(kwargs)
        return {"status": "pass", "proof": {"healthy": True, "chain_id": 42424240},
                "cleanup": {"deleted": True}, "service_uuid": "ephemeral-writer"}
    monkeypatch.setattr(routes, "execute_shared_rpc_route_rewire", fake_writer)
    result = routes.ensure_route(PRIVATE, network="mainnet", node="mainnetc-super2", controller_id="coolify-c", execute=True)
    assert result["ensured"] is True
    assert seen[0]["target_node"] == "mainnetc-super2"
    assert seen[0]["controller_id"] == "coolify-c"

    monkeypatch.setattr(routes, "execute_shared_rpc_route_rewire", lambda *_a, **_kw: {
        "status": "pass", "proof": {"healthy": True}, "cleanup": {"deleted": False}
    })
    with pytest.raises(routes.RpcRouteEnsureError):
        routes.ensure_route(PRIVATE, network="mainnet", node="mainneta-super1", controller_id="coolify-a", execute=True)


def test_route_step_runs_after_both_successful_add_paths_before_cleanup() -> None:
    assert harness.SINGLE_NODE_STEPS.index("ensure-rpc-route") == harness.SINGLE_NODE_STEPS.index("verify-single-node-proof") + 1
    assert harness.REPLICA_ADMISSION_STEPS.index("ensure-rpc-route") == harness.REPLICA_ADMISSION_STEPS.index("finalize-post-admission-topology") + 1
    assert harness.SINGLE_NODE_STEPS.index("ensure-rpc-route") < harness.SINGLE_NODE_STEPS.index(harness.POST_WORK_CLEANUP_STEP)
    assert "ensure-rpc-route" in harness.MUTATION_STEPS


def test_harness_ensure_passes_node_controller_and_execute(tmp_path: Path, monkeypatch) -> None:
    args = harness.build_parser().parse_args([
        "add-node", "--node", "mainnetc-super2", "--host", "coolify-c",
        "--runtime-state-root", str(tmp_path), "--run-dir", str(tmp_path / "runs"),
        "--execute-mutations", "--yes-i-know-this-mutates-target-host",
    ])
    h = harness.Harness(args)
    recorded = []
    def fake_run(step, cmd, **kwargs):
        recorded.append((step, cmd))
        return {"status": "pass", "clean": True, "ensured": True}
    monkeypatch.setattr(h, "run", fake_run)
    h.step_ensure_rpc_route()
    step, cmd = recorded[0]
    assert step == "ensure-rpc-route"
    assert "--execute" in cmd and "--controller-id" in cmd
    assert cmd[cmd.index("--node") + 1] == "mainnetc-super2"
    assert cmd[cmd.index("--controller-id") + 1] == "coolify-c"
    assert not any("canary" in arg or "paranoia" in arg for arg in cmd)
