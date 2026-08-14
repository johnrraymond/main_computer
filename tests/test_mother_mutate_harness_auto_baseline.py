from __future__ import annotations

import argparse
import json
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = ROOT / "mother_mutate_harness.py"

spec = importlib.util.spec_from_file_location("mother_mutate_harness_under_test", HARNESS_PATH)
assert spec is not None and spec.loader is not None
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def _args(tmp_path: Path, *, operation: str = "add-node", node: str = "mainnetc-super2", mode: str = "reactivate") -> argparse.Namespace:
    return argparse.Namespace(
        operation=operation,
        runtime_state_root=str(tmp_path),
        node=node,
        mode=mode,
        baseline_evidence=None,
        baseline_evidence_sha256=None,
    )


def _write(path: Path, payload: bytes) -> tuple[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return str(path), harness.canonical_sha256_file(path)


def test_add_reactivate_auto_selects_latest_matching_remove_finalize(tmp_path: Path) -> None:
    old_path, _old_sha = _write(
        tmp_path / "mother" / "evidence" / "deployment-node-remove-finalize" / "20260813T010000Z-mainnetc-super2.json",
        b'{"old":true}\n',
    )
    new_path, new_sha = _write(
        tmp_path / "mother" / "evidence" / "deployment-node-remove-finalize" / "20260813T020000Z-mainnetc-super2.json",
        b'{"new":true}\n',
    )
    _write(
        tmp_path / "mother" / "evidence" / "deployment-node-remove-finalize" / "20260813T030000Z-mainneta-super1.json",
        b'{"wrong_node":true}\n',
    )
    Path(old_path).touch()
    Path(new_path).touch()

    args = _args(tmp_path, operation="add-node", node="mainnetc-super2", mode="reactivate")
    harness.resolve_baseline_arguments(args)

    assert args.baseline_evidence == new_path
    assert args.baseline_evidence_sha256 == new_sha


def test_add_soft_auto_selects_latest_finalized_topology(tmp_path: Path) -> None:
    old_path, _old_sha = _write(
        tmp_path / "mother" / "evidence" / "deployment-node-add-post-admission-observe" / "20260813T010000Z-mainnet-topology-finalize-from-c1.json",
        b'{"old_topology":true}\n',
    )
    new_path, new_sha = _write(
        tmp_path / "mother" / "evidence" / "deployment-node-add-post-admission-observe" / "20260813T020000Z-mainnet-topology-finalize-from-c2.json",
        b'{"new_topology":true}\n',
    )
    Path(old_path).touch()
    Path(new_path).touch()

    args = _args(tmp_path, operation="add-node", node="mainnetc-super3", mode="soft")
    harness.resolve_baseline_arguments(args)

    assert args.baseline_evidence == new_path
    assert args.baseline_evidence_sha256 == new_sha


def test_supplied_baseline_gets_sha_when_only_path_is_supplied(tmp_path: Path) -> None:
    path, sha = _write(
        tmp_path / "mother" / "evidence" / "deployment-node-remove-finalize" / "20260813T010000Z-mainnetc-super2.json",
        b'{"baseline":true}\n',
    )

    args = _args(tmp_path, operation="add-node", node="mainnetc-super2", mode="reactivate")
    args.baseline_evidence = path

    harness.resolve_baseline_arguments(args)

    assert args.baseline_evidence == path
    assert args.baseline_evidence_sha256 == sha


def test_remove_node_auto_selects_latest_current_topology_candidate(tmp_path: Path) -> None:
    add_path, _add_sha = _write(
        tmp_path / "mother" / "evidence" / "deployment-node-add-post-admission-observe" / "20260813T010000Z-mainnet-topology-finalize-from-c2.json",
        b'{"add_topology":true}\n',
    )
    remove_path, remove_sha = _write(
        tmp_path / "mother" / "evidence" / "deployment-node-remove-finalize" / "20260813T020000Z-mainneta-super1.json",
        b'{"remove_topology":true}\n',
    )
    Path(add_path).touch()
    Path(remove_path).touch()

    args = _args(tmp_path, operation="remove-node", node="mainnetc-super2", mode="reactivate")
    harness.resolve_baseline_arguments(args)

    assert args.baseline_evidence == remove_path
    assert args.baseline_evidence_sha256 == remove_sha


def test_add_auto_selects_empty_rectification_baseline(tmp_path: Path) -> None:
    path, sha = _write(
        tmp_path / "mother" / "evidence" / "deployment-live-topology-empty-rectification" / "20260814T201500Z-empty.json",
        b'{"final_topology":{"nodes":[]},"network":"mainnet","schema_version":1}\n',
    )

    args = _args(tmp_path, operation="add-node", node="mainneta-super1", mode="soft")
    harness.resolve_baseline_arguments(args)

    assert args.baseline_evidence == path
    assert args.baseline_evidence_sha256 == sha
    assert args.internal_add_prep_mode == "initial"


def test_add_auto_rebuilds_pristine_empty_baseline_from_reset_backup(tmp_path: Path, monkeypatch) -> None:
    genesis = "1" * 64
    source_payload = {
        "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
        "schema_version": 1,
        "network": "mainnet",
        "completed_at": "2026-08-14T18:09:02Z",
        "next_phase": "add-node-prep-mainnet",
        "final_topology": {
            "chain_id": 42424240,
            "genesis_sha256": genesis,
            "nodes": [],
            "services": {},
            "validator_count": 0,
            "validator_set": [],
        },
    }
    source_path = (
        tmp_path
        / "mother-local-audit-reset-backups"
        / "20260814T201108Z"
        / "evidence"
        / "deployment-node-remove-finalize"
        / "20260814T180902Z-mainneta-super1.json"
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(harness.canonical_json(source_payload))

    monkeypatch.setattr(
        harness,
        "_current_private_state_binding",
        lambda args: {
            "generation": 3,
            "content_sha256": "2" * 64,
            "manifest_sha256": "3" * 64,
        },
    )

    args = _args(tmp_path, operation="add-node", node="mainneta-super1", mode="soft")
    harness.resolve_baseline_arguments(args)

    assert args.internal_add_prep_mode == "initial"
    assert args.baseline_evidence is not None
    baseline = Path(args.baseline_evidence)
    assert baseline.parent == tmp_path / "mother" / "evidence" / "deployment-live-topology-empty-rectification"
    document = json.loads(baseline.read_text(encoding="utf-8"))
    assert document["kind"] == "main_computer.mother.live_topology_empty_rectification_evidence.v1"
    assert document["mode"] == "operator-declared-pristine-start-over"
    assert document["mother_binding"]["generation"] == 3
    assert document["final_topology"]["nodes"] == []
    assert document["final_topology"]["chain_id"] == 42424240
    assert document["final_topology"]["genesis_sha256"] == genesis
    assert document["source_previous_topology_evidence"]["path"] == str(source_path)
    assert args.baseline_evidence_sha256 == harness.canonical_sha256_file(baseline)
