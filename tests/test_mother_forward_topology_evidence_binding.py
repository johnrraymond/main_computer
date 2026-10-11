from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_topology_rectification import _digest_without

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "mother_forward_topology_evidence_binding.py"
spec = importlib.util.spec_from_file_location("mother_forward_binding_under_test", SCRIPT)
assert spec and spec.loader
forwarder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forwarder)

OLD = {"generation": 7, "content_sha256": "a" * 64, "manifest_sha256": "b" * 64}
CURRENT = {"generation": 15, "content_sha256": "c" * 64, "manifest_sha256": "d" * 64}


def _source(root: Path, *, broken_digest: bool = False):
    path = root / "mother/evidence/deployment-live-current-topology/20261008T225508Z-mainnet-live-current-topology-example.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "kind": forwarder.harness.LIVE_CURRENT_TOPOLOGY_EVIDENCE_KIND,
        "schema_version": 1,
        "completed_at": "2026-10-08T22:55:08Z",
        "status": "pass",
        "network": "mainnet",
        "next_phase": "topology-baseline-ready-mainnet",
        "mother_binding": dict(OLD),
        "summary": {"clean": True, "complete": True, "live_topology_sealed": True},
        "final_topology": {
            "nodes": ["mainneta-super1"],
            "validator_set": ["0x" + "1" * 40],
            "genesis_sha256": "e" * 64,
        },
        "observed_at": "2026-10-08T22:55:07Z",
    }
    document["live_current_topology_sha256"] = _digest_without(document, "live_current_topology_sha256")
    if broken_digest:
        document["live_current_topology_sha256"] = "0" * 64
    original = canonical_json(document)
    path.write_bytes(original)
    return path, document, original


def _now():
    return datetime(2026, 10, 10, 18, 45, tzinfo=timezone.utc)


def test_forward_is_minimal_canonical_and_auto_selected(tmp_path):
    source, old, original = _source(tmp_path)
    result = forwarder.forward_evidence(tmp_path, binding=CURRENT, now=_now())
    assert result["action"] == "forwarded"
    assert result["auto_baseline_selected"] is True
    assert result["live_topology_reverified"] is False
    assert source.read_bytes() == original
    destination = Path(result["evidence_path"])
    assert destination != source
    assert destination.exists()
    new, raw, sha = forwarder._canonical_file(destination)
    assert raw == canonical_json(new)
    assert sha == result["evidence_sha256"]
    assert new["mother_binding"] == CURRENT
    assert new["completed_at"] > old["completed_at"]
    assert new["live_current_topology_sha256"] == _digest_without(new, "live_current_topology_sha256")
    assert {key for key in old if old[key] != new[key]} == {
        "mother_binding", "completed_at", "live_current_topology_sha256"
    }
    assert forwarder._latest_add_baseline(tmp_path) == destination
    # A second run should not produce another redundant evidence file.
    same = forwarder.forward_evidence(tmp_path, binding=CURRENT, now=_now())
    assert same["action"] == "unchanged"
    assert len(list(destination.parent.glob("*.json"))) == 2


def test_forward_rejects_tampered_source(tmp_path):
    _source(tmp_path, broken_digest=True)
    with pytest.raises(ValueError, match="internal SHA-256"):
        forwarder.forward_evidence(tmp_path, binding=CURRENT, now=_now())


def test_forward_rejects_non_live_latest_evidence(tmp_path):
    source, _old, _raw = _source(tmp_path)
    competing = tmp_path / "mother/evidence/deployment-node-remove-finalize/20261009T000000Z.json"
    competing.parent.mkdir(parents=True, exist_ok=True)
    competing.write_bytes(canonical_json({"kind": "test", "completed_at": "2026-10-09T00:00:00Z"}))
    with pytest.raises(ValueError, match="not a live-current-topology"):
        forwarder.forward_evidence(tmp_path, binding=CURRENT, now=_now())
    assert len(list(source.parent.glob("*.json"))) == 1


def test_forward_rejects_unselectable_timestamp(tmp_path):
    _source(tmp_path)
    with pytest.raises(ValueError, match="current time"):
        forwarder.forward_evidence(tmp_path, binding=CURRENT, now=datetime(2026, 10, 7, tzinfo=timezone.utc))


def test_forwarded_evidence_reaches_existing_detector_and_reseal(tmp_path):
    """Integration: rebind -> stale detection OR current -> live reseal."""
    from tests.test_mother_deployment_executor import _install, _operation
    from tests.test_mother_topology_rectification import (
        _write_single_node_topology_evidence,
        _PresentServicesOpener,
        _MissingServiceOpener,
        _ControllerScopedServicesOpener,
    )
    from tests.test_mother_deployment_node_add_prep import A_NODE, _binding_for_test
    from tools.mother.common.deployment_topology_rectification import (
        seal_live_current_topology,
        detect_topology_staleness,
    )

    runtime, paths, state = _install(tmp_path)
    base, _base_sha = _write_single_node_topology_evidence(paths, state)
    base_doc, _, _ = forwarder._canonical_file(base)
    service_uuid = "stale-service-uuid"
    # A faithfully shaped live seal built from the existing one-node topology.
    # We retain its topology while deliberately leaving an obsolete state binding.
    source_doc = dict(base_doc)
    source_doc["kind"] = forwarder.harness.LIVE_CURRENT_TOPOLOGY_EVIDENCE_KIND
    source_doc["completed_at"] = "2026-08-20T00:00:00Z"
    source_doc["summary"] = {
        **base_doc["summary"],
        "live_topology_sealed": True,
    }
    source_doc["next_phase"] = "topology-baseline-ready-mainnet"
    source_doc["mother_binding"] = OLD
    source_doc["live_current_topology_sha256"] = _digest_without(source_doc, "live_current_topology_sha256")
    source = paths.root / "evidence/deployment-live-current-topology/20260820T000000Z-mainnet-live-current-topology-old.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(canonical_json(source_doc))

    forwarded = forwarder.forward_evidence(
        runtime,
        binding=_binding_for_test(state),
        now=_now(),
    )
    new_file = Path(forwarded["evidence_path"])
    new_sha = forwarded["evidence_sha256"]

    # The old binding failure is gone, and absent services still demand rectification.
    missing = detect_topology_staleness(
        paths, state, new_file,
        network="mainnet", acknowledged_topology_evidence_sha256=new_sha,
        now=_now(), opener=_MissingServiceOpener(),
    )
    assert missing["summary"]["rectification_required"] is True
    assert missing["status"] == "stale"

    # If live services really exist, the existing reseal operates on forwarded evidence.
    resealed = seal_live_current_topology(
        paths, state, new_file,
        network="mainnet", acknowledged_topology_evidence_sha256=new_sha,
        use_live_topology=True, write_evidence=False,
        now=_now(), opener=_ControllerScopedServicesOpener({"coolify-a": {service_uuid: (A_NODE, "running:healthy")}, "coolify-c": {}}),
        operation=_operation("test-forwarded-reseal"),
    )
    assert resealed["status"] == "pass"
    assert resealed["summary"]["live_topology_sealed"] is True


def test_cli_reads_actual_private_binding_and_prints_new_auto_baseline(tmp_path):
    import subprocess
    import sys
    from tests.test_mother_deployment_executor import _install

    runtime, _paths, state = _install(tmp_path)
    old_path, _document, old_bytes = _source(runtime)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--runtime-state-root", str(runtime), "--network", "mainnet"],
        cwd=str(ROOT), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "MOTHER_FORWARD_TOPOLOGY_EVIDENCE: forwarded" in result.stdout
    assert "auto_baseline_selected=True" in result.stdout
    assert old_path.read_bytes() == old_bytes
    selected = forwarder._latest_add_baseline(runtime)
    assert selected is not None and selected != old_path
    new, _data, _sha = forwarder._canonical_file(selected)
    from tests.test_mother_deployment_node_add_prep import _binding_for_test
    assert new["mother_binding"] == _binding_for_test(state)
