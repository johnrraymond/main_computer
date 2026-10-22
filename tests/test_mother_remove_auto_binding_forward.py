"""Mother's authorized remove-node harness owns stale private-binding recovery."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import mother_mutate_harness as harness_mod
from tools import mother_forward_topology_evidence_binding as forwarder
from tests.test_mother_forward_topology_evidence_binding import CURRENT, OLD, _source


ERROR = "MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_PRIVATE_STATE_CHANGED: obsolete private-state binding\n"


def _setup(tmp_path: Path, network: str, *, explicit: bool = False, authorized: bool = True):
    state = tmp_path / "runtime" / "state"
    source, _doc, original = _source(state, network=network)
    argv = ["remove-node", "--network", network, "--node", "testnetb-super1" if network == "testnet" else "mainneta-super1", "--runtime-state-root", str(state)]
    if explicit:
        argv += ["--baseline-evidence", str(source), "--baseline-evidence-sha256", harness_mod.canonical_sha256_file(source)]
    if authorized:
        argv += ["--execute-mutations", "--yes-i-know-this-mutates-target-host"]
    args = harness_mod.build_parser().parse_args(argv)
    harness_mod.resolve_baseline_arguments(args)
    return harness_mod.Harness(args), source, original


def _inject_detector(monkeypatch, *, outcome: str = "pass", second_binding_error: bool = False):
    calls = []

    def fake_run(argv, **kwargs):
        if "detect-mother-topology-staleness" not in argv:
            raise AssertionError("unexpected subprocess")
        calls.append(argv)
        if len(calls) == 1 or second_binding_error:
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr=ERROR)
        if outcome == "stale":
            payload = {"status": "stale", "network": "testnet", "summary": {"rectification_required": True, "topology_stale": True}}
        else:
            payload = {"status": "pass", "network": "testnet", "summary": {"topology_current": True, "topology_stale": False, "clean": True}}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(harness_mod.subprocess, "run", fake_run)
    return calls


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_remove_auto_binding_recovery_is_bound_to_exact_seal_and_retries_once(tmp_path, monkeypatch, network):
    h, source, original = _setup(tmp_path, network)
    monkeypatch.setattr(forwarder, "_binding", lambda *_: dict(CURRENT))
    calls = _inject_detector(monkeypatch)
    assert h.args.baseline_auto_selected is True
    h.step_detect_topology()
    assert len(calls) == 2
    assert source.read_bytes() == original
    assert h.state["baseline_binding_forward_attempted"] is True
    evidence = Path(h.state["baseline_evidence"])
    assert evidence != source and evidence.exists()
    assert h.state["baseline_evidence_sha256"] == harness_mod.canonical_sha256_file(evidence)
    assert h.state["baseline_binding_forward"]["source_sha256"] == harness_mod.canonical_sha256_file(source)
    assert "--topology-evidence" in calls[1]
    assert calls[1][calls[1].index("--topology-evidence")+1] == str(evidence)
    assert (h.run_dir / "00-detect-topology.stale-binding.stderr.txt").is_file()
    assert (h.run_dir / "00-detect-topology.stderr.txt").is_file()


def test_explicit_baseline_never_auto_forwarded(tmp_path, monkeypatch):
    h, source, original = _setup(tmp_path, "testnet", explicit=True)
    calls = _inject_detector(monkeypatch)
    with pytest.raises(SystemExit) as err:
        h.step_detect_topology()
    assert err.value.code == 2
    assert len(calls) == 1
    assert h.args.baseline_auto_selected is False
    assert source.read_bytes() == original
    assert len(list(source.parent.glob("*.json"))) == 1


def test_read_only_invocation_does_not_forward(tmp_path, monkeypatch):
    h, source, original = _setup(tmp_path, "testnet", authorized=False)
    calls = _inject_detector(monkeypatch)
    with pytest.raises(SystemExit) as err:
        h.step_detect_topology()
    assert err.value.code == 2
    assert len(calls) == 1
    assert source.read_bytes() == original
    assert len(list(source.parent.glob("*.json"))) == 1


def test_second_binding_failure_is_not_automatically_retried(tmp_path, monkeypatch):
    h, source, original = _setup(tmp_path, "testnet")
    monkeypatch.setattr(forwarder, "_binding", lambda *_: dict(CURRENT))
    calls = _inject_detector(monkeypatch, second_binding_error=True)
    with pytest.raises(SystemExit) as err:
        h.step_detect_topology()
    assert err.value.code == 2
    assert len(calls) == 2
    assert len(list(source.parent.glob("*.json"))) == 2
    assert source.read_bytes() == original


def test_actual_topology_drift_after_forward_fails_closed(tmp_path, monkeypatch):
    h, source, original = _setup(tmp_path, "testnet")
    monkeypatch.setattr(forwarder, "_binding", lambda *_: dict(CURRENT))
    calls = _inject_detector(monkeypatch, outcome="stale")
    with pytest.raises(SystemExit) as err:
        h.step_detect_topology()
    assert err.value.code == 3
    assert len(calls) == 2
    assert source.read_bytes() == original


def test_forwarder_rejects_wrong_source_sha_and_preserves_source(tmp_path):
    state = tmp_path / "runtime" / "state"
    source, _doc, original = _source(state, network="testnet")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        forwarder.forward_evidence(state, network="testnet", operation="remove-node", source_path=source, source_sha256="0"*64, binding=CURRENT)
    assert source.read_bytes() == original
    assert len(list(source.parent.glob("*.json"))) == 1


def test_forwarder_rejects_wrong_network_and_noncanonical_source(tmp_path):
    state = tmp_path / "runtime" / "state"
    main, _doc, original = _source(state, network="mainnet")
    with pytest.raises(ValueError, match="currently selected baseline|no remove-node baseline"):
        forwarder.forward_evidence(state, network="testnet", operation="remove-node", source_path=main, source_sha256=harness_mod.canonical_sha256_file(main), binding=CURRENT)
    assert main.read_bytes() == original
