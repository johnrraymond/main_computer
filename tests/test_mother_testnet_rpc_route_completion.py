"""First testnet node has the same mandatory RPC publication gate as mainnet."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import mother_mutate_harness as harness
from tools import mother_rpc_route_ensure as route


def _state(*, rpc_route=None, registered=True):
    testnet = {
        "chain_id": 42424241,
        "nodes": {"testnetb-super1": {"host": "coolify-b"}},
        "deployment": {
            "targets": {
                "testnetb-super1": {
                    "controller_ref": "networks.testnet.coolify.controllers.coolify-b",
                },
            },
        },
    }
    if registered:
        testnet["coolify"] = {"controllers": {"coolify-b": {"enabled": True}}}
    if rpc_route is not None:
        testnet["rpc_route"] = rpc_route
    return SimpleNamespace(canonical_object_bytes=json.dumps({"networks": {"testnet": testnet}}).encode())


def test_testnet_implicit_route_invokes_shared_writer(monkeypatch):
    seen = []
    def stub(_state, **kwargs):
        seen.append(kwargs)
        return {"status": "pass", "proof": {"healthy": True, "chain_id": 42424241},
                "cleanup": {"deleted": True}}
    monkeypatch.setattr(route, "execute_shared_rpc_route_rewire", stub)
    result = route.ensure_route(_state(), network="testnet", node="testnetb-super1", controller_id="coolify-b", execute=True)
    assert result["status"] == "pass"
    assert result["mode"] == "traefik-route-ensure"
    assert result["ensured"] is True
    assert result["route"]["url"] == "https://testnet-rpc.greatlibrary.io"
    assert result["route"]["expected_chain_id"] == 42424241
    assert seen[0]["network"] == "testnet"
    assert seen[0]["controller_id"] == "coolify-b"
    assert seen[0]["target_node"] == "testnetb-super1"


def test_implicit_testnet_route_dry_run_never_writes(monkeypatch):
    monkeypatch.setattr(route, "execute_shared_rpc_route_rewire", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unexpected write")))
    result = route.ensure_route(_state(), network="testnet", node="testnetb-super1", controller_id="coolify-b", execute=False)
    assert result["mode"] == "plan-only"
    assert result["route"]["host"] == "testnet-rpc.greatlibrary.io"
    assert result["ensured"] is False


def test_testnet_unregistered_controller_fails_closed():
    with pytest.raises(route.RpcRouteEnsureError, match="unverified testnet RPC backend/controller"):
        route.ensure_route(_state(registered=False), network="testnet", node="testnetb-super1", controller_id="coolify-b", execute=True)


def test_explicitly_unpublished_route_refused_during_add_node(monkeypatch):
    monkeypatch.setattr(route, "execute_shared_rpc_route_rewire", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unexpected write")))
    with pytest.raises(route.RpcRouteEnsureError, match="explicitly unpublished"):
        route.ensure_route(_state(rpc_route={"published": False}), network="testnet", node="testnetb-super1", controller_id="coolify-b", execute=True)


def test_testnet_controller_and_node_mismatch_rejected():
    with pytest.raises(route.RpcRouteEnsureError):
        route.route_plan(_state(), network="testnet", node="testnetb-super1", controller_id="coolify-a")
    with pytest.raises(route.RpcRouteEnsureError):
        route.route_plan(_state(), network="testnet", node="mainneta-super1", controller_id="coolify-b")


def test_harness_never_accepts_unpublished_route_as_success(tmp_path, monkeypatch):
    args = harness.build_parser().parse_args(["add-node", "--network", "testnet", "--node", "testnetb-super1", "--host", "coolify-b", "--run-dir", str(tmp_path / "run")])
    instance = harness.Harness(args)
    old = {
        "status": "pass", "clean": True, "ensured": False,
        "mode": "verified-unpublished-route", "route": {"network": "testnet"},
        "proof": {"published_route_absent_in_verified_mother_state": True},
    }
    monkeypatch.setattr(instance, "run", lambda step, cmd: old)
    with pytest.raises(SystemExit, match="RPC_ROUTE_NOT_ENSURED"):
        instance.step_ensure_rpc_route()
    valid = {"status": "pass", "clean": True, "ensured": True}
    monkeypatch.setattr(instance, "run", lambda step, cmd: valid)
    instance.step_ensure_rpc_route()
    assert instance.state["rpc_route_ensure"] == valid
    for invalid in (dict(valid, clean=False), dict(valid, status="failed"), dict(valid, ensured=False)):
        monkeypatch.setattr(instance, "run", lambda step, cmd, value=invalid: value)
        with pytest.raises(SystemExit, match="RPC_ROUTE_NOT_ENSURED"):
            instance.step_ensure_rpc_route()
