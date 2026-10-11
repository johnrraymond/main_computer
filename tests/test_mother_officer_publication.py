"""Mother's verified fresh genesis is the sole automatic officer-publication boundary."""
from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest

import mother_mutate_harness as mh


def baseline(root: Path, *, fresh: bool) -> Path:
    target = root / ("fresh.json" if fresh else "retained.json")
    target.write_text(json.dumps({"current_topology": {
        "nodes": [],
        **({"fresh_genesis_required": True, "genesis_lineage": "fresh-required"}
           if fresh else {"genesis_sha256": "a" * 64}),
    }}), encoding="utf-8")
    return target


def instance(tmp_path: Path, *, fresh: bool = True, network: str = "mainnet"):
    args = mh.build_parser().parse_args([
        "add-node", "--network", network,
        "--runtime-state-root", str(tmp_path),
        "--run-dir", str(tmp_path / "runs"),
        "--execute-mutations", "--yes-i-know-this-mutates-target-host",
    ])
    args.baseline_evidence = str(baseline(tmp_path, fresh=fresh))
    run = mh.Harness(args)
    run.repo_root = tmp_path
    run.state["single_node_proof_evidence"] = str(tmp_path / "proof.json")
    run.state["single_node_proof_evidence_sha256"] = "c" * 64
    return run


def test_publication_is_between_verified_genesis_proof_and_rpc_setup():
    steps = mh.SINGLE_NODE_STEPS
    assert steps.index("verify-single-node-proof") + 1 == steps.index("publish-officer-addresses")
    assert steps.index("publish-officer-addresses") + 1 == steps.index("ensure-rpc-route")
    assert "publish-officer-addresses" in mh.MUTATION_STEPS
    assert "publish-officer-addresses" not in mh.REPLICA_ADMISSION_STEPS
    assert "publish-officer-addresses" not in mh.REMOVE_STEPS


def test_fresh_genesis_invokes_exact_rectifier_command_after_verified_proof(monkeypatch, tmp_path):
    run = instance(tmp_path)
    rectifier = tmp_path / "tools" / "wallet_address_rectifier.py"
    rectifier.parent.mkdir(parents=True)
    rectifier.write_text("# present", encoding="utf-8")
    sequence = []

    def verify():
        sequence.append("verify")
        run.state["single_node_proof_verified"] = True

    def execute(step, argv, *, allow_failure=False):
        sequence.append("publish")
        assert step == "publish-officer-addresses"
        assert argv == [mh.sys.executable, str(rectifier), "--network", "mainnet", "--apply"]
        return {"status": "UPDATED", "changed": True, "write_performed": True}

    monkeypatch.setattr(run, "step_verify_single_node_proof", verify)
    monkeypatch.setattr(run, "run", execute)
    run.step_publish_officer_addresses()
    assert sequence == ["verify", "publish"]
    assert run.state["officer_address_publication"]["status"] == "UPDATED"


def test_preverified_proof_does_not_repeat_verification(monkeypatch, tmp_path):
    run = instance(tmp_path, network="testnet")
    run.state["single_node_proof_verified"] = True
    path = tmp_path / "tools" / "wallet_address_rectifier.py"
    path.parent.mkdir(parents=True)
    path.write_text("# present", encoding="utf-8")
    monkeypatch.setattr(run, "step_verify_single_node_proof", lambda: pytest.fail("proof already verified"))
    commands = []
    monkeypatch.setattr(run, "run", lambda step, argv: commands.append(argv) or {
        "status": "PASS", "changed": False, "write_performed": False,
    })
    run.step_publish_officer_addresses()
    assert commands == [[mh.sys.executable, str(path), "--network", "testnet", "--apply"]]


def test_existing_genesis_rebootstrap_does_not_publish(monkeypatch, tmp_path):
    run = instance(tmp_path, fresh=False)
    monkeypatch.setattr(run, "run", lambda *args, **kwargs: pytest.fail("must not write"))
    monkeypatch.setattr(run, "step_verify_single_node_proof", lambda: pytest.fail("must not run"))
    run.step_publish_officer_addresses()
    assert run.state["officer_address_publication"] is None


def test_development_network_never_publishes(monkeypatch, tmp_path):
    run = instance(tmp_path, network="dev")
    monkeypatch.setattr(run, "run", lambda *args, **kwargs: pytest.fail("must not write"))
    run.step_publish_officer_addresses()


def test_resume_never_publishes_without_verified_proof(monkeypatch, tmp_path):
    run = instance(tmp_path)
    path = tmp_path / "tools" / "wallet_address_rectifier.py"
    path.parent.mkdir(parents=True)
    path.write_text("# present", encoding="utf-8")
    def refusal():
        raise SystemExit("proof rejected")
    monkeypatch.setattr(run, "step_verify_single_node_proof", refusal)
    monkeypatch.setattr(run, "run", lambda *args, **kwargs: pytest.fail("publication before proof"))
    with pytest.raises(SystemExit, match="proof rejected"):
        run.step_publish_officer_addresses()


def test_missing_rectifier_fails_closed(tmp_path):
    run = instance(tmp_path)
    run.state["single_node_proof_verified"] = True
    with pytest.raises(SystemExit, match="MOTHER_OFFICER_PUBLISHER_MISSING"):
        run.step_publish_officer_addresses()


def test_publish_exit_status_is_constrained():
    check = lambda doc: mh.is_clean_or_pass("publish-officer-addresses", doc)
    assert check({"status": "UPDATED", "changed": True, "write_performed": True})
    assert check({"status": "PASS", "changed": False, "write_performed": False})
    assert not check({"status": "DRIFT", "changed": True, "write_performed": False})
    assert not check({"status": "UPDATED", "changed": True, "write_performed": False})
    assert not check({"status": "UNKNOWN", "changed": False, "write_performed": False})


def test_run_all_fresh_route_includes_publication():
    # An ordinary add-node choosing the first-validator path runs the new step.
    steps = mh.SINGLE_NODE_STEPS
    assert steps.count("publish-officer-addresses") == 1
    assert "publish-officer-addresses" in mh.STEP_ORDER


def test_remove_and_replica_routes_unaffected():
    assert "publish-officer-addresses" not in mh.COMMON_STEPS
    assert "publish-officer-addresses" not in mh.REPLICA_ADMISSION_STEPS
    assert "publish-officer-addresses" not in mh.REMOVE_STEPS


def test_operator_authorization_is_required_to_publish(monkeypatch, tmp_path):
    run = instance(tmp_path)
    run.args.execute_mutations = False
    run.state["single_node_proof_verified"] = True
    path = tmp_path / "tools" / "wallet_address_rectifier.py"
    path.parent.mkdir(parents=True)
    path.write_text("# exists", encoding="utf-8")
    monkeypatch.setattr(mh.subprocess, "run", lambda *a, **k: pytest.fail("unauthorized mutation"))
    with pytest.raises(SystemExit) as exc:
        run.step_publish_officer_addresses()
    assert exc.value.code == 3


def test_rectifier_failure_stops_harness_before_rpc(monkeypatch, tmp_path):
    run = instance(tmp_path)
    run.state["single_node_proof_verified"] = True
    path = tmp_path / "tools" / "wallet_address_rectifier.py"
    path.parent.mkdir(parents=True)
    path.write_text("# exists", encoding="utf-8")
    monkeypatch.setattr(mh.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="", stderr="cannot publish offices", returncode=1,
    ))
    with pytest.raises(SystemExit):
        run.step_publish_officer_addresses()
    assert run.state["officer_address_publication"] is None
