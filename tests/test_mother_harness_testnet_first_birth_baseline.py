"""Network-isolated Mother harness baseline selection and first-birth proof."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mother_harness_testnet_first_birth_under_test", ROOT / "mother_mutate_harness.py")
assert spec and spec.loader
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def _args(root, *, network="testnet", operation="add-node"):
    return argparse.Namespace(
        runtime_state_root=str(root), network=network, operation=operation,
        node="testnetb-super1" if network == "testnet" else "mainneta-super1",
        host="coolify-b" if network == "testnet" else "coolify-a",
        mode="soft", baseline_evidence=None, baseline_evidence_sha256=None,
        timeout=2.0, max_response_bytes=4096,
    )


def _write(root, directory, filename, *, network="mainnet", nodes=None, status="pass", kind=None, next_phase=None):
    path = root / "mother" / "evidence" / directory / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    kind = kind or (harness.LIVE_CURRENT_TOPOLOGY_EVIDENCE_KIND if directory == harness.LIVE_CURRENT_TOPOLOGY_EVIDENCE_DIRECTORY else "main_computer.mother.add_node_post_admission_topology_evidence.v1")
    payload = {
        "network": network, "kind": kind, "schema_version": 1,
        "status": status, "completed_at": "2026-10-18T15:00:00Z",
        "next_phase": next_phase or f"topology-baseline-ready-{network}",
        "summary": {"clean": True, "complete": True, "live_topology_sealed": True},
        "final_topology": {"nodes": nodes if nodes is not None else [], "validator_set": []},
    }
    path.write_bytes(harness.canonical_json(payload))
    return path


def test_testnet_ignores_newer_mainnet_topology_and_selects_testnet(tmp_path):
    _write(tmp_path, "deployment-live-current-topology", "20261018T180000Z-mainnet.json", network="mainnet", nodes=["mainneta-super1"])
    testnet = _write(tmp_path, "deployment-live-current-topology", "20261018T170000Z-testnet.json", network="testnet")
    args = _args(tmp_path)
    harness.resolve_baseline_arguments(args)
    assert args.baseline_evidence == str(testnet)
    assert args.internal_add_prep_mode == "initial"


def test_mainnet_never_selects_newer_testnet_topology(tmp_path):
    mainnet = _write(tmp_path, "deployment-live-current-topology", "20261018T170000Z-mainnet.json", network="mainnet")
    _write(tmp_path, "deployment-live-current-topology", "20261018T180000Z-testnet.json", network="testnet")
    args = _args(tmp_path, network="mainnet")
    harness.resolve_baseline_arguments(args)
    assert args.baseline_evidence == str(mainnet)


@pytest.mark.parametrize("operation", ["add-node", "remove-node"])
def test_explicit_mainnet_baseline_is_rejected_for_testnet(tmp_path, operation):
    mainnet = _write(tmp_path, "deployment-live-current-topology", "mainnet.json")
    args = _args(tmp_path, operation=operation)
    args.baseline_evidence = str(mainnet)
    with pytest.raises(SystemExit, match="BASELINE_NETWORK_MISMATCH"):
        harness.resolve_baseline_arguments(args)
    assert args.baseline_evidence_sha256 is None


def test_unlabelled_legacy_evidence_is_never_promoted_to_testnet(tmp_path):
    path = tmp_path / "mother" / "evidence" / "deployment-node-add-post-admission-observe" / "no-network.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"final_topology":{"nodes":[]}}')
    assert harness.select_auto_baseline_file(_args(tmp_path), harness.auto_baseline_patterns(_args(tmp_path))[1]) is None
    assert harness.select_auto_baseline_file(_args(tmp_path, network="mainnet"), harness.auto_baseline_patterns(_args(tmp_path, network="mainnet"))[1]) == path


def test_mainnet_backup_cannot_seed_testnet_fresh_chain(tmp_path):
    path = tmp_path / "mother-local-audit-reset-backups" / "old" / "evidence" / "deployment-node-remove-finalize" / "mainnet.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"network": "mainnet", "chain_id": 42424240, "genesis_sha256": "a" * 64}))
    assert harness._find_reset_chain_identity_source(_args(tmp_path)) is None


def _mock_virgin_state(monkeypatch, *, live_rows=False, incomplete=False, changed=False):
    from tools.mother.common import private_state, coolify_state
    import tools.mother_detect_topology_v2 as detector

    raw = {
        "networks": {
            "testnet": {
                "chain_id": 42424241,
                "deployment": {
                    "mode": "clean-start", "status": "identity-reserved-awaiting-executor",
                    "targets": {
                        "testnetb-super1": {
                            "status": "absent-awaiting-redeployment",
                            "live_resource_uuid": None,
                            "controller_ref": "networks.testnet.coolify.controllers.coolify-b",
                        }
                    },
                },
            }
        }
    }
    binding = SimpleNamespace(generation=23, content_hash=SimpleNamespace(digest="a" * 64), recovery_manifest_hash=SimpleNamespace(digest="b" * 64))
    state = SimpleNamespace(binding=binding, canonical_object_bytes=json.dumps(raw).encode())
    replacement = SimpleNamespace(binding=SimpleNamespace(generation=24), canonical_object_bytes=state.canonical_object_bytes)
    count = [0]
    def read_state(*args, **kwargs):
        count[0] += 1
        return replacement if changed and count[0] > 1 else state
    monkeypatch.setattr(private_state, "read_private_state", read_state)
    controller = SimpleNamespace(network="testnet", enabled=True, controller_id="coolify-b", api_token="test-token")
    monkeypatch.setattr(coolify_state, "list_coolify_controllers", lambda state: [controller])

    def query(controllers, **kwargs):
        assert kwargs["network"] == "testnet"
        probes = [SimpleNamespace(
            controller_id="coolify-b", endpoint_label=label, ok=not incomplete,
            live_node_hints=["testnetb-super1"] if live_rows else [],
            exact_service_nodes=["testnetb-super1"] if live_rows else [],
            matches=[{"name": "testnetb-super1"}] if live_rows else [],
        ) for label, _ in detector.ENDPOINTS]
        return probes, False
    monkeypatch.setattr(detector, "query_live_coolify_inventory", query)


def test_first_birth_baseline_requires_complete_empty_live_inventory_and_verified_state(tmp_path, monkeypatch):
    _mock_virgin_state(monkeypatch)
    args = _args(tmp_path)
    harness.resolve_baseline_arguments(args)
    document = json.loads(Path(args.baseline_evidence).read_text())
    assert document["network"] == "testnet"
    assert document["mother_binding"]["generation"] == 23
    assert document["final_topology"]["chain_id"] == 42424241
    assert document["final_topology"]["genesis_sha256"] is None
    assert document["final_topology"]["nodes"] == []
    assert document["fresh_genesis_required"] is True
    assert document["summary"]["inventory_probe_count"] == 5
    # Prove that both existing Mother consumers accept the new canonical
    # first-node topology, not just the harness's own selection machinery.
    from tools.mother.common.deployment_node_add_prep import (
        _baseline_clean, _baseline_kind_supported, _topology_from_baseline,
    )
    from tools.mother.common.deployment_topology_rectification import _nodes_and_services
    assert _baseline_kind_supported(document) is True
    assert _baseline_clean(document) is True
    assert _topology_from_baseline(document) == ([], [], 42424241, None, {})
    assert _nodes_and_services(document) == ([], [], 42424241, None, {})
    assert args.internal_add_prep_mode == "initial"
    assert args.baseline_evidence_sha256 == harness.canonical_sha256_file(Path(args.baseline_evidence))
    assert len(list((tmp_path / "mother" / "evidence" / harness.EMPTY_RECTIFICATION_EVIDENCE_DIRECTORY).glob("*.json"))) == 1


@pytest.mark.parametrize("flag", ["live_rows", "incomplete", "changed"])
def test_first_birth_fails_closed_on_live_resources_incomplete_inventory_or_state_change(tmp_path, monkeypatch, flag):
    _mock_virgin_state(monkeypatch, **{flag: True})
    args = _args(tmp_path)
    if flag == "changed":
        with pytest.raises(SystemExit, match="FIRST_BIRTH_STATE_CHANGED"):
            harness.initialize_verified_first_birth_baseline(args)
    else:
        with pytest.raises(SystemExit, match="FIRST_BIRTH_(OCCUPIED|UNPROVEN)"):
            harness.initialize_verified_first_birth_baseline(args)
    assert not list((tmp_path / "mother" / "evidence").rglob("*.json"))


def test_failed_prior_bootstrap_blocks_automatic_first_birth(tmp_path, monkeypatch):
    _mock_virgin_state(monkeypatch)
    _write(tmp_path, "deployment-node-add-single-node-chain-and-hub-proof", "failed-testnet.json", network="testnet", status="failed", kind="main_computer.mother.deployment_node_add_single_node_chain_and_hub_proof_evidence.v1")
    with pytest.raises(SystemExit, match="FIRST_BIRTH_PRIOR_EVIDENCE"):
        harness.initialize_verified_first_birth_baseline(_args(tmp_path))


def test_chain_only_testnet_skips_officer_manifest_publication(tmp_path, capsys):
    args = _args(tmp_path)
    h = object.__new__(harness.Harness)
    h.args = args
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"fresh_genesis_required":true}')
    h.state = {"baseline_evidence": str(baseline)}
    h.repo_root = ROOT
    h.step_publish_officer_addresses()
    assert "skipped (testnet chain-only release)" in capsys.readouterr().out


def test_mainnet_officer_publication_path_not_skipped(tmp_path, monkeypatch):
    args = _args(tmp_path, network="mainnet")
    h = object.__new__(harness.Harness)
    h.args = args
    h.repo_root = ROOT
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"fresh_genesis_required":true}')
    h.state = {"baseline_evidence": str(baseline), "single_node_proof_verified": True}
    calls = []
    h.run = lambda step, argv: calls.append((step, argv)) or {"status": "pass"}
    h.step_publish_officer_addresses()
    assert calls and calls[0][0] == "publish-officer-addresses"


def test_first_birth_reports_clean_start_gate_without_exposing_state(tmp_path, monkeypatch):
    _mock_virgin_state(monkeypatch)
    from tools.mother.common import private_state
    original_reader = private_state.read_private_state
    def bad_mode_reader(*args, **kwargs):
        state = original_reader(*args, **kwargs)
        doc = json.loads(state.canonical_object_bytes)
        doc["networks"]["testnet"]["deployment"]["status"] = "active"
        return SimpleNamespace(binding=state.binding, canonical_object_bytes=json.dumps(doc).encode())
    monkeypatch.setattr(private_state, "read_private_state", bad_mode_reader)
    with pytest.raises(SystemExit, match="FIRST_BIRTH_STATE_NOT_CLEAN_START.*deployment.status"):
        harness.resolve_baseline_arguments(_args(tmp_path))
    assert not list((tmp_path / "mother" / "evidence").rglob("*.json"))


def test_first_birth_rejects_unreserved_status_before_inventory(tmp_path, monkeypatch):
    _mock_virgin_state(monkeypatch)
    from tools.mother.common import private_state
    reader = private_state.read_private_state
    def before_reservation(*args, **kwargs):
        state = reader(*args, **kwargs)
        document = json.loads(state.canonical_object_bytes)
        document["networks"]["testnet"]["deployment"]["status"] = "awaiting-offline-plan"
        return SimpleNamespace(binding=state.binding, canonical_object_bytes=json.dumps(document).encode())
    monkeypatch.setattr(private_state, "read_private_state", before_reservation)
    import tools.mother_detect_topology_v2 as detector
    monkeypatch.setattr(detector, "query_live_coolify_inventory", lambda *a, **kw: pytest.fail("unreserved state should not query Coolify"))
    with pytest.raises(SystemExit, match="FIRST_BIRTH_STATE_NOT_CLEAN_START.*deployment.status"):
        harness.resolve_baseline_arguments(_args(tmp_path))
    assert not list((tmp_path / "mother" / "evidence").rglob("*.json"))


def test_registration_reservation_produces_first_birth_eligible_status():
    from tools.mother.common.starter_identity import reserve_starter_identity
    source = {
        "kind": "main_computer.mother.private_state.v1", "schema_version": 1,
        "networks": {"testnet": {
            "chain_id": 42424241,
            "deployment": {"mode": "clean-start", "status": "awaiting-offline-plan", "targets": {
                "testnetb-super1": {
                    "controller_ref": "networks.testnet.coolify.controllers.coolify-b",
                    "status": "absent-awaiting-redeployment",
                    "hub_admin_private_key_path": "networks.testnet.node_seed_material.testnetb-super1.wallets.hub_admin.private_key",
                    "live_resource_uuid": None,
                },
            }},
            "wallets": {}, "validators": {}, "nodes": {}, "node_seed_material": {},
        }},
    }
    keys = iter("0x" + f"{value:064x}" for value in range(1, 40))
    reserved = reserve_starter_identity(
        source, generated_at="2026-10-18T00:00:00Z", network="testnet",
        key_factory=lambda: next(keys),
    ).document["networks"]["testnet"]
    assert reserved["deployment"]["status"] == "identity-reserved-awaiting-executor"
    assert reserved["deployment"]["targets"]["testnetb-super1"]["status"] == "absent-awaiting-redeployment"
    assert "testnetb-super1" in reserved["validators"]
    assert reserved["deployment"]["targets"]["testnetb-super1"]["live_resource_uuid"] is None


def test_first_birth_allows_project_server_testnet_metadata_but_no_services(tmp_path, monkeypatch):
    _mock_virgin_state(monkeypatch)
    import tools.mother_detect_topology_v2 as detector
    original = detector.query_live_coolify_inventory
    def query(*args, **kwargs):
        probes, timed_out = original(*args, **kwargs)
        for probe in probes:
            if probe.endpoint_label in {"projects", "servers"}:
                probe.matches.append({"name": "testnet"})
        return probes, timed_out
    monkeypatch.setattr(detector, "query_live_coolify_inventory", query)
    args = _args(tmp_path)
    harness.resolve_baseline_arguments(args)
    assert args.internal_add_prep_mode == "initial"


def test_first_birth_requires_absent_services_even_with_empty_node_hints(tmp_path, monkeypatch):
    _mock_virgin_state(monkeypatch)
    import tools.mother_detect_topology_v2 as detector
    original = detector.query_live_coolify_inventory
    def query(*args, **kwargs):
        probes, timed_out = original(*args, **kwargs)
        for probe in probes:
            if probe.endpoint_label == "services":
                probe.matches.append({"name": "testnet-unrecognized-service"})
        return probes, timed_out
    monkeypatch.setattr(detector, "query_live_coolify_inventory", query)
    with pytest.raises(SystemExit, match="FIRST_BIRTH_OCCUPIED"):
        harness.resolve_baseline_arguments(_args(tmp_path))


def test_testnet_first_birth_never_falls_back_to_reset_baseline(tmp_path, monkeypatch):
    _mock_virgin_state(monkeypatch)
    def refuses(_):
        raise SystemExit("MOTHER_MUTATE_HARNESS_TESTNET_FIRST_BIRTH_PRIOR_EVIDENCE")
    def reset(_):
        raise AssertionError("legacy reset reconstruction must not be called")
    monkeypatch.setattr(harness, "initialize_verified_first_birth_baseline", refuses)
    monkeypatch.setattr(harness, "rebuild_pristine_empty_add_baseline", reset)
    with pytest.raises(SystemExit, match="FIRST_BIRTH_PRIOR_EVIDENCE"):
        harness.resolve_baseline_arguments(_args(tmp_path))


def test_mainnet_still_uses_existing_pristine_empty_fallback(tmp_path, monkeypatch):
    path = _write(tmp_path, "deployment-live-current-topology", "empty-mainnet.json", network="mainnet")
    # Hide regular live topology evidence from the auto-selector to exercise fallback.
    def select_none(*args):
        return None
    def reset(args):
        assert args.network == "mainnet"
        return path
    monkeypatch.setattr(harness, "select_auto_baseline_file", select_none)
    monkeypatch.setattr(harness, "rebuild_pristine_empty_add_baseline", reset)
    args = _args(tmp_path, network="mainnet")
    harness.resolve_baseline_arguments(args)
    assert args.baseline_evidence == str(path)
    assert args.internal_add_prep_mode == "initial"
