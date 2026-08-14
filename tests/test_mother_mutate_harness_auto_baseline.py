from __future__ import annotations

import argparse
import hashlib
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
    return str(path), hashlib.sha256(payload).hexdigest()


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
