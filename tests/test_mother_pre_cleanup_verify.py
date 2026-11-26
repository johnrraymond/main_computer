from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import mother_mutate_harness as harness
from tools import mother_pre_cleanup_verify as gate
from tools import wallet_address_rectifier as rectifier


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root = tmp_path
    cfg = root / "main_computer/config"
    cfg.mkdir(parents=True)
    for name in ("hub_networks.json", "testnet_contracts.json", "mainnet_contracts.json"):
        (cfg / name).write_bytes((gate.ROOT / "main_computer/config" / name).read_bytes())
    paths = {}
    for network, chain_id in (("mainnet", 42424240), ("testnet", 42424241)):
        path = root / "runtime/deployments" / network / "latest.json"
        path.parent.mkdir(parents=True)
        records = {k: {"address": "0x" + "a" * 40} for k in gate.EXPECTED}
        path.write_text(json.dumps({"schema": "main-computer.deployment.v1", "dry_run": False,
            "environment": network, "chain": {"chain_id": chain_id, "rpc_url": "http://old.invalid",
                "host_rpc_url": "http://old.invalid", "container_rpc_url": "http://internal.invalid"},
            "contracts": records, "deployments": records, "offices": [{"address": "preserve"}],
            "run_id": "preserve"}), encoding="utf-8")
        paths[network] = path
    monkeypatch.delenv(gate.ROUTES_ENV, raising=False)
    monkeypatch.delenv(gate.REQUIRE_ENV, raising=False)
    calls = []
    def rpc(url, method, params=None, *, timeout):
        calls.append((url, method, params))
        if method == "eth_chainId":
            return hex(42424241 if "testnet" in url else 42424240)
        assert method == "eth_getCode" and params[0] in gate.EXPECTED.values()
        return "0x6000"
    monkeypatch.setattr(gate, "rpc_json", rpc)
    # The existing shared verifier performs the actual eth_getCode parsing.
    monkeypatch.setattr("scripts.smoke_hub_network_client.rpc_json", rpc)
    return root, paths, cfg, calls


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_real_rectifier_repairs_only_selected_network(fixture, network):
    root, paths, _, calls = fixture
    other = "testnet" if network == "mainnet" else "mainnet"
    other_before = paths[other].read_bytes()
    result = gate.verify_and_rectify(root, network)
    assert result["status"] == "pass" and result["manifest_verified"] is True
    assert result["manifest_reconciliation"]["status"] == "UPDATED"
    assert [method for _, method, _ in calls] == ["eth_chainId", "eth_getCode", "eth_getCode"]
    assert all(url == f"https://{network}-rpc.greatlibrary.io" for url, _, _ in calls)
    assert paths[other].read_bytes() == other_before
    current = json.loads(paths[network].read_text())
    assert current["chain"]["rpc_url"] == current["chain"]["host_rpc_url"] == f"https://{network}-rpc.greatlibrary.io"
    assert current["chain"]["container_rpc_url"] == "http://internal.invalid"
    assert current["offices"] == [{"address": "preserve"}] and current["run_id"] == "preserve"
    for section in ("contracts", "deployments"):
        assert {k: v["address"] for k, v in current[section].items()} == gate.EXPECTED
    before = paths[network].read_bytes()
    assert gate.verify_and_rectify(root, network)["manifest_reconciliation"]["status"] == "PASS"
    assert paths[network].read_bytes() == before


@pytest.mark.parametrize("defect", ["stale-registry", "wrong-chain", "missing-code", "private-route", "missing-manifest", "registry-change", "rpc-error"])
def test_unsafe_state_never_writes_manifest(fixture, monkeypatch, defect):
    root, paths, cfg, calls = fixture
    path = paths["testnet"]
    registry = cfg / "testnet_contracts.json"
    if defect == "stale-registry":
        registry.write_text(json.dumps({**gate.EXPECTED, "hub_credit_bridge_escrow": "0x" + "b" * 40}))
    elif defect == "wrong-chain":
        monkeypatch.setattr(gate, "rpc_json", lambda *a, **kw: hex(42424240))
    elif defect == "missing-code":
        monkeypatch.setattr("scripts.smoke_hub_network_client.rpc_json", lambda *a, **kw: "0x")
    elif defect == "private-route":
        monkeypatch.setenv(gate.REQUIRE_ENV, "true")
    elif defect == "missing-manifest":
        path.unlink()
    elif defect == "registry-change":
        original = gate.verify_public_contracts
        def changed(*a, **kw):
            result = original(*a, **kw)
            registry.write_text(json.dumps({**gate.EXPECTED, "hub_credit_bridge_escrow": "0x" + "b" * 40}))
            return result
        monkeypatch.setattr(gate, "verify_public_contracts", changed)
    elif defect == "rpc-error":
        def refused(*a, **kw):
            raise RuntimeError("RPC unavailable")
        monkeypatch.setattr(gate, "rpc_json", refused)
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(Exception):
        gate.verify_and_rectify(root, "testnet")
    assert (path.read_bytes() if path.exists() else None) == before


def test_write_failure_is_not_success(fixture, monkeypatch):
    root, paths, _, _ = fixture
    before = paths["testnet"].read_bytes()
    def failed(*a, **kw):
        raise OSError("write failed")
    monkeypatch.setattr(rectifier, "_atomic_replace_if_unchanged", failed)
    with pytest.raises(OSError, match="write failed"):
        gate.verify_and_rectify(root, "testnet")
    assert paths["testnet"].read_bytes() == before


@pytest.mark.parametrize("steps", [harness.SINGLE_NODE_STEPS, harness.REPLICA_ADMISSION_STEPS])
@pytest.mark.parametrize("start", ["ensure-rpc-route", harness.PRE_CLEANUP_VERIFY_STEP, harness.POST_WORK_CLEANUP_STEP, harness.PRISTINE_CLEANUP_STEP])
@pytest.mark.parametrize("fail", [False, True])
def test_add_execution_and_cleanup_resume_cannot_bypass_gate(steps, start, fail):
    instance = harness.Harness.__new__(harness.Harness)
    instance.args = SimpleNamespace(operation="add-node")
    seen = []
    def invoke(step):
        seen.append(step)
        if step == harness.PRE_CLEANUP_VERIFY_STEP and fail:
            raise SystemExit("verification refused")
    instance.methods = lambda: {step: (lambda step=step: invoke(step)) for step in steps}
    if fail:
        with pytest.raises(SystemExit, match="verification refused"):
            instance.run_steps(steps, start)
        assert harness.POST_WORK_CLEANUP_STEP not in seen and harness.PRISTINE_CLEANUP_STEP not in seen
    else:
        instance.run_steps(steps, start)
        assert seen.count(harness.PRE_CLEANUP_VERIFY_STEP) == 1
        assert seen.index(harness.PRE_CLEANUP_VERIFY_STEP) < seen.index(harness.PRISTINE_CLEANUP_STEP)
        assert (harness.POST_WORK_CLEANUP_STEP in seen) == (start != harness.PRISTINE_CLEANUP_STEP)


def test_removal_unchanged():
    assert harness.PRE_CLEANUP_VERIFY_STEP not in harness.REMOVE_STEPS
    assert harness.PRE_CLEANUP_VERIFY_STEP in harness.MUTATION_STEPS
    instance = harness.Harness.__new__(harness.Harness)
    instance.args = SimpleNamespace(operation="remove-node", network="testnet")
    instance.run = lambda *a, **kw: pytest.fail("remove-node must not reconcile")
    instance.step_pre_cleanup_verify()


def test_harness_rejects_invalid_result_and_uses_existing_mutation_gate(tmp_path, monkeypatch):
    args = harness.build_parser().parse_args(["add-node", "--network", "testnet", "--run-dir", str(tmp_path)])
    instance = harness.Harness(args)
    with pytest.raises(SystemExit) as exc:
        instance.step_pre_cleanup_verify()
    assert exc.value.code == 3
    for response in ({"status": "fail"}, {"status": "pass", "network": "mainnet", "manifest_verified": True},
                     {"status": "pass", "network": "testnet", "manifest_verified": False}):
        monkeypatch.setattr(instance, "run", lambda *a, **kw: response)
        with pytest.raises(SystemExit, match="PRE_CLEANUP_VERIFY_FAILED"):
            instance.step_pre_cleanup_verify()
    calls = []
    def success(step, argv):
        calls.append((step, argv))
        return {"status": "pass", "network": "testnet", "manifest_verified": True}
    monkeypatch.setattr(instance, "run", success)
    instance.step_pre_cleanup_verify()
    assert calls[0][0] == harness.PRE_CLEANUP_VERIFY_STEP
    assert calls[0][1][1].endswith("mother_pre_cleanup_verify.py")
    assert "--apply" in calls[0][1]
    assert instance.state["pre_cleanup_verify"]["manifest_verified"] is True
