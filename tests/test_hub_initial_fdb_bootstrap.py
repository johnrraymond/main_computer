"""First Hub owns initial FoundationDB birth and its evidence gates."""

import argparse
import json
import subprocess
from pathlib import Path

import pytest

import hub_mutate_harness as hub


def args(root: Path, *, execute=False, network="testnet", identity="testnetb-hub1"):
    private = root / "runtime/state/mother/identity.private.yaml"
    if not private.exists():
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text(
            "networks:\n  testnet:\n    hubs: {}\n  mainnet:\n    hubs: {}\n",
            encoding="utf-8",
        )
    return argparse.Namespace(
        operation="add-hub", network=network, hub=identity,
        allow_full_deletion=False, force_git=True, repo_root=root,
        execute_mutations=execute, yes_i_know_this_mutates_hub=execute,
    )


def unknown(network):
    return {
        "network": network, "status": "UNKNOWN",
        "errors": [{"code": "HUB_MOTHER_STATE_UNVERIFIED", "message":
                    f"HubControlError: no accepted FDB state exists for '{network}'"}],
    }


def fdb(status="unborn", **extra):
    return {"ok": True, "result": {"network": "testnet", "status": status, **extra}}


def fake_run_factory(events, snapshots):
    def fake_run(argv, **kwargs):
        if argv[1] == "-m":
            assert argv[2] == "tools.fdb_control"
            events.append("fdb-inspect")
            return subprocess.CompletedProcess(argv, 0, json.dumps(fdb(**snapshots.pop(0))), "")
        assert str(argv[1]).endswith("hub_topology_check.py")
        events.append("topology")
        if events.count("topology") == 1:
            return subprocess.CompletedProcess(argv, 2, json.dumps(unknown("testnet")), "")
        return subprocess.CompletedProcess(argv, 0, json.dumps({"network":"testnet", "status":"PASS"}), "")
    return fake_run


def test_first_hub_bootstraps_fdb_before_hub_prep(tmp_path, monkeypatch):
    h = hub.Harness(args(tmp_path, execute=True))
    events = []
    snapshots = [{"status":"unborn", "accepted":None}, {
        "status":"accepted", "accepted_generation":1,
        "services":[{"service_id":"testnetb-fdb1"}], "coordinators":["10.124.0.3:4550"],
        "cluster_verification":{"verified":True},
    }]
    monkeypatch.setattr(hub.subprocess, "run", fake_run_factory(events, snapshots))
    monkeypatch.setattr(h, "_run_fdb_bootstrap", lambda operation, service: events.append((operation, service)))
    monkeypatch.setattr(h, "_run_step", lambda step, argv: (_ for _ in ()).throw(RuntimeError(f"entered {step}")))
    with pytest.raises(RuntimeError, match="entered pre-inspect"):
        h.run()
    assert events == ["topology", "fdb-inspect", ("create-cluster", "testnetb-fdb1"), "fdb-inspect", "topology"]
    assert h.state["fdb_bootstrap"]["status"] == "verified"
    assert h.state["last_completed_step"] is None


def test_first_hub_dry_run_plans_fdb_without_mutation(tmp_path, monkeypatch):
    h = hub.Harness(args(tmp_path))
    events = []
    monkeypatch.setattr(hub.subprocess, "run", fake_run_factory(events, [{"status":"unborn", "accepted": None}]))
    monkeypatch.setattr(h, "_run_fdb_bootstrap", lambda *a: pytest.fail("read-only must not deploy"))
    monkeypatch.setattr(h, "_run_step", lambda *a: pytest.fail("missing FDB must not prep Hub"))
    assert h.run() == 0
    assert events == ["topology", "fdb-inspect"]
    assert h.state["fdb_bootstrap"]["service"] == "testnetb-fdb1"
    assert h.state["last_completed_step"] is None


def test_first_hub_with_accepted_empty_fdb_uses_add_service(tmp_path, monkeypatch):
    h = hub.Harness(args(tmp_path, execute=True))
    events=[]
    snapshots = [
        {"status":"accepted-empty", "accepted_generation":4, "empty_topology_verification":{"verified": True}},
        {"status":"accepted", "accepted_generation":5, "services":[{"service_id":"testnetb-fdb1"}],
         "coordinators":["10.124.0.3:4550"], "cluster_verification":{"verified": True}},
    ]
    monkeypatch.setattr(hub.subprocess, "run", fake_run_factory(events, snapshots))
    monkeypatch.setattr(h, "_run_fdb_bootstrap", lambda operation, service: events.append((operation, service)))
    monkeypatch.setattr(h, "_run_step", lambda step, argv: (_ for _ in ()).throw(RuntimeError(step)))
    with pytest.raises(RuntimeError, match="pre-inspect"):
        h.run()
    assert ("add-service", "testnetb-fdb1") in events


def test_birth_success_without_independent_proof_blocks_hub(tmp_path, monkeypatch):
    h = hub.Harness(args(tmp_path, execute=True))
    events=[]
    snapshots = [{"status":"unborn", "accepted": None},
                 {"status":"accepted", "services":[{}], "coordinators":["x"],
                  "birth_verification":{"verified": False}}]
    monkeypatch.setattr(hub.subprocess, "run", fake_run_factory(events, snapshots))
    monkeypatch.setattr(h, "_run_fdb_bootstrap", lambda operation, service: events.append((operation, service)))
    with pytest.raises(hub.HarnessError, match="not independently verified"):
        h.run()
    assert events.count("topology") == 1


def test_accepted_fdb_unverified_never_reborn_automatically(tmp_path, monkeypatch):
    h = hub.Harness(args(tmp_path, execute=True))
    events=[]
    monkeypatch.setattr(hub.subprocess, "run", fake_run_factory(events, [
        {"status":"accepted", "services":[{}], "coordinators":["x"],
         "cluster_verification":{"verified": False}}]))
    monkeypatch.setattr(h, "_run_fdb_bootstrap", lambda *a: pytest.fail("do not overwrite accepted state"))
    with pytest.raises(hub.HarnessError, match="must be repaired"):
        h.run()


def test_fdb_name_is_derived_from_hub_placement_without_mainnet_special_case(tmp_path):
    assert hub.Harness(args(tmp_path))._first_fdb_service() == "testnetb-fdb1"
    assert hub.Harness(args(tmp_path, network="mainnet", identity="mainnetc-hub4"))._first_fdb_service() == "mainnetc-fdb1"


def test_unborn_fdb_does_not_authorize_rebirth_over_accepted_hubs(tmp_path, monkeypatch):
    h = hub.Harness(args(tmp_path, execute=True))
    state_path = tmp_path / "runtime/state/hub/testnet/accepted.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "schema": "main-computer.hub-accepted.v1", "network": "testnet",
        "generation": 3, "hubs": [{"hub_id": "testnetb-hub1"}],
    }))
    events=[]
    monkeypatch.setattr(hub.subprocess, "run", fake_run_factory(events, [{"status":"unborn", "accepted":None}]))
    monkeypatch.setattr(h, "_run_fdb_bootstrap", lambda *a: pytest.fail("must not recreate FDB"))
    with pytest.raises(hub.HarnessError, match="accepted Hub topology already contains Hubs"):
        h.run()


def test_unborn_fdb_does_not_override_active_mother_hubs(tmp_path, monkeypatch):
    h = hub.Harness(args(tmp_path, execute=True))
    (tmp_path / "runtime/state/mother/identity.private.yaml").write_text(
        "networks:\n  testnet:\n    hubs:\n      testnetb-hub1:\n        status: active\n"
    )
    events=[]
    monkeypatch.setattr(hub.subprocess, "run", fake_run_factory(events, [{"status":"unborn", "accepted":None}]))
    monkeypatch.setattr(h, "_run_fdb_bootstrap", lambda *a: pytest.fail("must not recreate FDB"))
    with pytest.raises(hub.HarnessError, match="Mother already has active Hub members"):
        h.run()
