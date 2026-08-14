from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from tools.mother.common.canonical import canonical_json


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = ROOT / "mother_mutate_harness.py"

spec = importlib.util.spec_from_file_location("mother_mutate_harness_under_test", HARNESS_PATH)
assert spec is not None and spec.loader is not None
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def _write_json(path: Path, document: dict) -> tuple[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, sort_keys=True).encode("utf-8")
    path.write_bytes(payload)
    return str(path), hashlib.sha256(canonical_json(document)).hexdigest()


def _touch(path: str, timestamp: int) -> None:
    os.utime(path, (timestamp, timestamp))


def _parser_args(tmp_path: Path, argv: list[str] | None = None):
    base = [
        "add-node",
        "--runtime-state-root",
        str(tmp_path),
        "--network",
        "mainnet",
        "--node",
        "mainnetc-super2",
        "--host",
        "coolify-c",
        "--run-dir",
        str(tmp_path / "runs"),
    ]
    return harness.build_parser().parse_args(base if argv is None else argv)


def test_operation_word_is_required(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        harness.build_parser().parse_args([
            "--runtime-state-root",
            str(tmp_path),
            "--node",
            "mainnetc-super2",
            "--host",
            "coolify-c",
        ])

    assert exc.value.code == 2


def test_reactivate_is_not_a_harness_cli_mode(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        harness.build_parser().parse_args([
            "add-node",
            "--runtime-state-root",
            str(tmp_path),
            "--node",
            "mainnetc-super2",
            "--host",
            "coolify-c",
            "--mode",
            "reactivate",
        ])

    assert exc.value.code == 2


def test_add_node_without_mode_is_valid(tmp_path: Path) -> None:
    args = _parser_args(tmp_path)

    assert args.operation == "add-node"
    assert not hasattr(args, "mode")


def test_add_node_auto_baseline_infers_internal_reactivate_from_target_remove_finalize(tmp_path: Path) -> None:
    old_path, _old_sha = _write_json(
        tmp_path / "mother" / "evidence" / "deployment-node-remove-finalize" / "20260813T010000Z-mainnetc-super2.json",
        {
            "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
            "summary": {"target_node": "mainnetc-super2"},
            "final_topology": {"nodes": ["mainneta-super1"]},
        },
    )
    new_path, new_sha = _write_json(
        tmp_path / "mother" / "evidence" / "deployment-node-remove-finalize" / "20260813T020000Z-mainnetc-super2.json",
        {
            "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
            "summary": {"target_node": "mainnetc-super2"},
            "final_topology": {"nodes": ["mainneta-super1", "mainnetc-super1"]},
        },
    )
    wrong_node_path, _wrong_sha = _write_json(
        tmp_path / "mother" / "evidence" / "deployment-node-remove-finalize" / "20260813T030000Z-mainneta-super1.json",
        {
            "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
            "summary": {"target_node": "mainneta-super1"},
            "final_topology": {"nodes": ["mainnetc-super1"]},
        },
    )
    _touch(old_path, 100)
    _touch(wrong_node_path, 200)
    _touch(new_path, 300)

    args = _parser_args(tmp_path)
    harness.resolve_baseline_arguments(args)

    assert args.baseline_evidence == new_path
    assert args.baseline_evidence_sha256 == new_sha
    assert args.internal_add_prep_mode == "reactivate"


def test_add_node_auto_baseline_uses_latest_topology_and_internal_soft_when_target_not_removed(tmp_path: Path) -> None:
    remove_path, _remove_sha = _write_json(
        tmp_path / "mother" / "evidence" / "deployment-node-remove-finalize" / "20260813T010000Z-mainneta-super1.json",
        {
            "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
            "summary": {"target_node": "mainneta-super1"},
            "final_topology": {"nodes": ["mainnetc-super1"]},
        },
    )
    topology_path, topology_sha = _write_json(
        tmp_path / "mother" / "evidence" / "deployment-node-add-post-admission-observe" / "20260813T020000Z-mainnet-topology-finalize.json",
        {
            "kind": "main_computer.mother.add_node_post_admission_topology_evidence.v1",
            "summary": {"current_nodes": ["mainneta-super1", "mainnetc-super1"]},
        },
    )
    _touch(remove_path, 100)
    _touch(topology_path, 200)

    args = _parser_args(tmp_path)
    harness.resolve_baseline_arguments(args)

    assert args.baseline_evidence == topology_path
    assert args.baseline_evidence_sha256 == topology_sha
    assert args.internal_add_prep_mode == "soft"


def test_add_node_auto_baseline_infers_internal_initial_from_empty_topology(tmp_path: Path) -> None:
    topology_path, topology_sha = _write_json(
        tmp_path / "mother" / "evidence" / "deployment-node-add-single-node-chain-and-hub-proof" / "20260813T010000Z-empty.json",
        {
            "kind": "main_computer.mother.deployment_node_add_single_node_chain_and_hub_proof_evidence.v1",
            "final_topology": {"nodes": [], "validator_count": 0},
        },
    )
    _touch(topology_path, 100)

    args = _parser_args(tmp_path)
    harness.resolve_baseline_arguments(args)

    assert args.baseline_evidence == topology_path
    assert args.baseline_evidence_sha256 == topology_sha
    assert args.internal_add_prep_mode == "initial"


def test_supplied_baseline_gets_canonical_sha_and_internal_mode(tmp_path: Path) -> None:
    document = {
        "z": ["kept out of file order"],
        "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
        "summary": {"target_node": "mainnetc-super2"},
        "final_topology": {"nodes": ["mainneta-super1"]},
    }
    path, expected_sha = _write_json(
        tmp_path / "mother" / "evidence" / "deployment-node-remove-finalize" / "20260813T010000Z-mainnetc-super2.json",
        document,
    )

    args = _parser_args(tmp_path)
    args.baseline_evidence = path
    harness.resolve_baseline_arguments(args)

    assert args.baseline_evidence == path
    assert args.baseline_evidence_sha256 == expected_sha
    assert args.internal_add_prep_mode == "reactivate"


def test_step_prep_passes_only_internal_mode_to_lower_mother_command(tmp_path: Path) -> None:
    args = _parser_args(tmp_path)
    args.baseline_evidence = str(tmp_path / "baseline.json")
    args.baseline_evidence_sha256 = "a" * 64
    args.internal_add_prep_mode = "reactivate"

    captured: dict[str, list[str]] = {}

    def fake_run(step: str, argv: list[str], *, allow_failure: bool = False):  # noqa: ARG001
        captured["argv"] = argv
        return {
            "summary": {
                "transaction_valid": True,
                "live_mutation_performed": False,
            },
            "transaction_artifact": {
                "path": str(tmp_path / "prep.json"),
                "sha256": "b" * 64,
            },
        }

    instance = harness.Harness(args)
    instance.run = fake_run
    instance.step_prep()

    mode_index = captured["argv"].index("--mode")
    assert captured["argv"][mode_index + 1] == "reactivate"
    assert "add-node" in captured["argv"]
    assert "--baseline-evidence" in captured["argv"]
