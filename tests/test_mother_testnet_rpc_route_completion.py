"""Testnet RPC-route completion is a verified no-op unless Mother publishes a route."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import mother_mutate_harness as harness
from tools import mother_rpc_route_ensure as route


def _state(*, rpc_route=None):
    testnet = {
        "chain_id": 42424241,
        "deployment": {
            "targets": {
                "testnetb-super1": {
                    "controller_ref": "networks.testnet.coolify.controllers.coolify-b",
                },
            },
        },
    }
    if rpc_route is not None:
        testnet["rpc_route"] = rpc_route
    return SimpleNamespace(canonical_object_bytes=json.dumps({"networks": {"testnet": testnet}}).encode())


def test_testnet_no_published_route_never_invokes_mainnet_writer(monkeypatch):
    monkeypatch.setattr(route, "execute_shared_rpc_route_rewire", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("mainnet writer used")))
    result = route.ensure_route(_state(), network="testnet", node="testnetb-super1", controller_id="coolify-b", execute=True)
    assert result["status"] == "pass"
    assert result["mode"] == "verified-unpublished-route"
    assert result["ensured"] is False
    assert result["route"]["backend_url"] == "http://testnetb-super1:8545"


def test_testnet_published_route_must_fail_closed():
    with pytest.raises(route.RpcRouteEnsureError, match="unverified testnet RPC backend/controller"):
        route.ensure_route(_state(rpc_route={"host": "testnet-rpc.example.invalid"}), network="testnet", node="testnetb-super1", controller_id="coolify-b", execute=True)


def test_testnet_controller_and_node_mismatch_rejected():
    with pytest.raises(route.RpcRouteEnsureError):
        route.route_plan(_state(), network="testnet", node="testnetb-super1", controller_id="coolify-a")
    with pytest.raises(route.RpcRouteEnsureError):
        route.route_plan(_state(), network="testnet", node="mainneta-super1", controller_id="coolify-b")


def test_harness_accepts_only_attested_testnet_unpublished_route(tmp_path, monkeypatch):
    args = harness.build_parser().parse_args(["add-node", "--network", "testnet", "--node", "testnetb-super1", "--host", "coolify-b", "--run-dir", str(tmp_path / "run")])
    instance = harness.Harness(args)
    safe = {
        "status": "pass", "clean": True, "ensured": False,
        "mode": "verified-unpublished-route", "route": {"network": "testnet"},
        "proof": {"published_route_absent_in_verified_mother_state": True},
    }
    monkeypatch.setattr(instance, "run", lambda step, cmd: safe)
    instance.step_ensure_rpc_route()
    assert instance.state["rpc_route_ensure"] == safe
    monkeypatch.setattr(instance, "run", lambda step, cmd: dict(safe, proof={}))
    with pytest.raises(SystemExit, match="RPC_ROUTE_NOT_ENSURED"):
        instance.step_ensure_rpc_route()
