"""P5.3c: JSON HubRegistry extraction preserves the FDB base contract."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from main_computer import hub, hub_registry_json, hub_registry_shared
from main_computer.exp_fdb_hub_state import ExperimentalFoundationDbRegistry


# Exact HubRegistry class source fingerprint from the P5.3b hub.py. This is
# portable between Python 3.12 and 3.13, unlike the version-specific AST dump.
PREEXTRACTION_CLASS_SOURCE_SHA256 = "bd3cc7bb5cb1dbc975d8435842f722985dfd44beb5a17c24bd6973805a39b2e4"


def test_extracted_class_body_matches_prior_registry_implementation_exactly():
    source = Path(hub_registry_json.__file__).read_text(encoding="utf-8")
    module = ast.parse(source)
    klass = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "HubRegistry")
    observed = hashlib.sha256(ast.get_source_segment(source, klass).encode("utf-8")).hexdigest()
    assert observed == PREEXTRACTION_CLASS_SOURCE_SHA256


def test_exact_legacy_class_identity_and_fdb_subclass():
    assert hub.HubRegistry is hub_registry_json.HubRegistry
    assert hub._utc_now is hub_registry_json._utc_now
    assert ExperimentalFoundationDbRegistry.__bases__[0] is hub_registry_json.HubRegistry
    assert hub_registry_json.HubRegistry.__module__ == "main_computer.hub_registry_json"
    assert hub.HubWorker is hub_registry_shared.HubWorker


def test_registry_module_does_not_reference_http_fdb_stable_or_legacy_server():
    source = Path(hub_registry_json.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"main_computer.hub", "main_computer.exp_fdb_hub", "main_computer.exp_fdb_hub_state", "main_computer.stable_hub"}
        if isinstance(node, ast.Import):
            assert not any(item.name in {"main_computer.hub", "fdb", "main_computer.stable_hub"} for item in node.names)
    assert "HTTPServer" not in source
    assert "urlopen" not in source


def test_importing_json_registry_does_not_load_legacy_hub_server():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import main_computer.hub_registry_json; print(int('main_computer.hub' in sys.modules))"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


def test_persisted_worker_registration_and_offer_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_registry_json, "_utc_now", lambda: "2026-11-08T00:00:00+00:00")
    first = hub_registry_json.HubRegistry(tmp_path)
    worker = first.register_worker(
        node_id="worker-1", endpoint="", models=["model-a", "model-b"],
        capabilities={"execution":{"mode":"worker_pull_v0"},"pricing":{"pricing_type":"fixed_per_call_v0"}},
        credits_per_request="1.024", worker_instance_id="worker-1-instance", max_concurrency=42,
    )
    assert worker.max_concurrency == 1
    assert worker.credits_per_request == "1.024"
    assert worker.offer["credits_per_request_wei"] == "1024000000000000000"
    path = tmp_path / "hub_workers.json"
    assert path.exists()
    record = json.loads(path.read_text(encoding="utf-8"))
    assert list(record) == ["hub", "workers", "upstream_hubs"]
    assert record["hub"]["settlement"] == "batched-worker-claim"
    assert record["workers"][0]["worker_instance_id"] == "worker-1-instance"
    assert record["workers"][0]["max_concurrency"] == 1
    second = hub.HubRegistry(tmp_path)
    loaded = second.get_worker("worker-1", worker_instance_id="worker-1-instance")
    assert loaded is not None
    assert loaded.offer == worker.offer
    assert second.status()["worker_count"] == 1


def test_register_heartbeat_lease_release_preserve_semantics(tmp_path):
    reg = hub_registry_json.HubRegistry(tmp_path)
    reg.register_worker(
        node_id="wk", endpoint="", models=["m"], worker_instance_id="wk-1",
        capabilities={"execution":{"mode":"worker_pull_v0"}},
    )
    assert reg.get_worker("wk", worker_instance_id="wk-1") is not None
    heartbeat = reg.heartbeat_worker("wk", worker_instance_id="wk-1", queue_depth=2)
    assert heartbeat.queue_depth == 2
    lease = reg.lease_worker("m", request_id="req", preferred_worker_instance_id="wk-1")
    assert lease is not None
    assert lease.active_requests == 1
    assert reg.lease_worker("m", preferred_worker_instance_id="wk-1") is None
    reg.release_worker("wk", worker_instance_id="wk-1", success=True, request_id="req")
    assert reg.get_worker("wk",worker_instance_id="wk-1").active_requests == 0
    reg.mark_worker("wk", status="offline", worker_instance_id="wk-1")
    assert reg.select_worker("m") is None


def test_upstream_registration_and_audit_are_unchanged(tmp_path):
    registry = hub_registry_json.HubRegistry(tmp_path)
    upstream = registry.register_upstream_hub(node_id="up-1", endpoint="https://example.org", credits_per_request="0.2")
    assert upstream.credits_per_request == "0.2"
    assert registry.select_upstream_hub().node_id == "up-1"
    registry.mark_upstream_hub("up-1", status="offline")
    assert registry.select_upstream_hub() is None
    event = registry.record_ring_admission_rejection({"reason": "test-rejection", "node_id":"wk"})
    assert event["event_id"].startswith("ring-admission-")
    assert registry.ring_admission_audit_count() == 1
    assert registry.list_ring_admission_audit()[0] == event


def test_unencrypted_public_registration_remains_blocked(tmp_path):
    registry = hub_registry_json.HubRegistry(tmp_path)
    with pytest.raises(ValueError,match="HTTPS"):
        registry.register_worker(node_id="remote",endpoint="http://example.org",models=["x"])
    assert registry.status()["worker_count"] == 0
