"""Final-node removal cleanup: do not seal a deliberately empty network."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("harness_final_cleanup", ROOT / "mother_mutate_harness.py")
assert SPEC is not None and SPEC.loader is not None
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def _setup(tmp_path: Path, network: str, *, malformed: bool = False):
    doc = {
        "kind": harness.REMOVE_FINALIZE_EVIDENCE_KIND,
        "network": network,
        "status": "pass",
        "final_topology": {"nodes": [], "services": {}, "validator_set": []},
        "summary": {
            "clean": True,
            "complete": True,
            "topology_current": True,
            "target_service_absent": True,
            "single_node_decommission": True,
            "final_validator_count": 0,
            "survivor_nodes": [],
            "target_node": "target-super1",
        },
    }
    if malformed:
        doc["final_topology"]["services"] = {"target-super1": {"service_uuid": "still-here"}}
    path = tmp_path / "remove-finalize.json"
    data = json.dumps(doc).encode()
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    args = harness.build_parser().parse_args([
        "remove-node", "--network", network,
        "--node", "target-super1", "--host", "coolify-b",
        "--runtime-state-root", str(tmp_path),
        "--run-dir", str(tmp_path / "runs"),
        "--remove-finalize-evidence", str(path),
        "--remove-finalize-evidence-sha256", sha,
        "--start-at", "post-work-cleanup",
    ])
    return harness.Harness(args)


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_final_node_cleanup_proves_current_empty_network_without_seal_or_rpc(tmp_path: Path, network: str):
    instance = _setup(tmp_path, network)
    calls = []

    def fake_run(step, argv, **kwargs):
        calls.append((step, argv))
        assert "seal-live-current-topology" not in argv
        assert "adopt-fresh-empty-topology" not in argv
        assert "--network" in argv and argv[argv.index("--network") + 1] == network
        if step.endswith("verify-final-empty"):
            assert "verify-remove-node-finalize-evidence" in argv
            return {"clean": True, "single_node_decommission": True,
                    "target_service_absent": True, "final_validator_set": []}
        if step.endswith("verify-live-empty"):
            assert "detect-mother-topology-staleness" in argv
            return {"status": "pass", "summary": {"clean": True, "topology_current": True},
                    "expected_nodes": [], "observed_live_primary_nodes": [],
                    "observed_live_node_hints": []}
        pytest.fail(f"unexpected step: {step}")

    instance.run = fake_run
    instance.step_post_work_cleanup()
    assert [step for step, _ in calls] == [
        "post-work-cleanup-verify-final-empty", "post-work-cleanup-verify-live-empty"
    ]
    assert instance.state["post_work_cleanup_results"] == []


def test_inconsistent_final_node_evidence_fails_closed_before_calls(tmp_path: Path):
    instance = _setup(tmp_path, "testnet", malformed=True)
    instance.run = lambda *a, **k: pytest.fail("must fail before reaching command")
    with pytest.raises(SystemExit, match="EMPTY_REMOVAL_EVIDENCE_CONTRADICTORY"):
        instance.step_post_work_cleanup()


def test_rogue_live_node_blocks_empty_cleanup(tmp_path: Path):
    instance = _setup(tmp_path, "testnet")

    def fake_run(step, argv, **kwargs):
        if step.endswith("verify-final-empty"):
            return {"clean": True, "single_node_decommission": True,
                    "target_service_absent": True, "final_validator_set": []}
        return {"status": "pass", "summary": {"clean": False, "topology_current": False},
                "expected_nodes": [], "observed_live_primary_nodes": ["rogue-super1"],
                "observed_live_node_hints": ["rogue-super1"]}

    instance.run = fake_run
    with pytest.raises(SystemExit, match="EMPTY_REMOVAL_LIVE_TOPOLOGY_NOT_VERIFIED"):
        instance.step_post_work_cleanup()


def test_final_node_resume_does_not_attempt_predeletion_validator_rpc(tmp_path: Path):
    instance = _setup(tmp_path, "testnet")
    instance.step_detect_topology = lambda: pytest.fail("must not detect pre-removal topology")
    instance.step_preflight_paranoia_validator_set = lambda: pytest.fail("must not contact deleted validator")
    instance.ensure_validator_set_preflight_for_resume()


def test_resume_without_finalization_evidence_still_runs_preflight(tmp_path: Path):
    instance = _setup(tmp_path, "testnet")
    instance.state["remove_finalize_evidence"] = None
    calls = []
    instance.step_detect_topology = lambda: calls.append("detect")
    instance.step_preflight_paranoia_validator_set = lambda: calls.append("validator")
    instance.ensure_validator_set_preflight_for_resume()
    assert calls == ["detect", "validator"]
