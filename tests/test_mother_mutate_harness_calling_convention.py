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
    current_path, current_sha = _write_json(
        tmp_path / "mother" / "evidence" / "deployment-node-remove-finalize" / "20260813T030000Z-mainneta-super1.json",
        {
            "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
            "summary": {"target_node": "mainneta-super1"},
            "final_topology": {"nodes": ["mainnetc-super1"]},
        },
    )
    _touch(old_path, 100)
    _touch(current_path, 200)
    _touch(new_path, 300)

    args = _parser_args(tmp_path)
    harness.resolve_baseline_arguments(args)

    assert args.baseline_evidence == current_path
    assert args.baseline_evidence_sha256 == current_sha
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


def _cleanup_completion_evidence(tmp_path: Path, *, operation: str) -> tuple[str, str]:
    if operation == "remove-node":
        document = {
            "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
            "status": "pass",
            "failure": None,
            "network": "mainnet",
            "next_phase": "remove-node-finalized-mainnet",
            "summary": {
                "clean": True,
                "topology_current": True,
                "topology_stale": False,
                "next_phase": "remove-node-finalized-mainnet",
            },
            "final_topology": {
                "nodes": ["mainnetc-super1"],
                "services": {
                    "mainnetc-super1": {
                        "node": "mainnetc-super1",
                        "controller_id": "coolify-c",
                        "service_uuid": "j1445405xyjkbeld0se5j8i8",
                    },
                },
            },
            "target": {
                "node": "mainnetc-super2",
                "controller_id": "coolify-c",
                "service_uuid": "deletedservice123",
            },
        }
        return _write_json(tmp_path / "remove-finalize.json", document)

    document = {
        "kind": "main_computer.mother.add_node_post_admission_topology_evidence.v1",
        "status": "pass",
        "failure": None,
        "network": "mainnet",
        "next_phase": "add-node-prep-mainnet",
        "summary": {
            "clean": True,
            "topology_current": True,
            "topology_stale": False,
            "next_phase": "add-node-prep-mainnet",
        },
        "final_topology": {
            "nodes": ["mainneta-super1", "mainnetc-super1"],
            "services": {
                "mainneta-super1": {
                    "node": "mainneta-super1",
                    "controller_id": "coolify-a",
                    "service_uuid": "mzg0ttmqt4yqsurwjkv5un4a",
                },
                "mainnetc-super1": {
                    "node": "mainnetc-super1",
                    "controller_id": "coolify-c",
                    "service_uuid": "j1445405xyjkbeld0se5j8i8",
                },
            },
        },
    }
    return _write_json(tmp_path / "post-admission.json", document)


def test_add_node_harness_runs_cleanup1_then_cleanup2_once(tmp_path: Path) -> None:
    args = _parser_args(tmp_path)
    evidence_path, evidence_sha = _cleanup_completion_evidence(tmp_path, operation="add-node")
    args.post_admission_topology_evidence = evidence_path
    args.post_admission_topology_evidence_sha256 = evidence_sha

    captured: list[tuple[str, list[str]]] = []

    def fake_run(step: str, argv: list[str], *, allow_failure: bool = False):  # noqa: ARG001
        captured.append((step, argv))
        return {
            "status": "pass",
            "summary": {"clean": True},
            "evidence_path": str(tmp_path / f"{step}.json"),
            "evidence_sha256": "c" * 64,
        }

    instance = harness.Harness(args)
    instance.run = fake_run
    instance.step_post_work_cleanup()

    assert [step for step, _ in captured] == [
        "post-work-cleanup-cleanup1",
        "post-work-cleanup-cleanup2",
    ]

    cleanup1_argv = captured[0][1]
    assert cleanup1_argv[1].endswith("tools/mother_post_work_cleanup_v2.py")
    assert cleanup1_argv[2] == "execute"
    assert "--cleanup-all" in cleanup1_argv
    assert cleanup1_argv[cleanup1_argv.index("--topology-evidence") + 1] == evidence_path
    assert cleanup1_argv[cleanup1_argv.index("--acknowledge-topology-evidence-sha256") + 1] == evidence_sha

    cleanup2_argv = captured[1][1]
    assert cleanup2_argv[1].endswith("tools/mother_helper_cleanup2_yagni.py")
    assert cleanup2_argv[2] == "execute"
    assert "--cleanup-all" not in cleanup2_argv
    assert cleanup2_argv[cleanup2_argv.index("--topology-evidence") + 1] == evidence_path
    assert cleanup2_argv[cleanup2_argv.index("--acknowledge-topology-evidence-sha256") + 1] == evidence_sha

    forbidden = {
        "--workflow",
        "--controller-id",
        "--service-uuid",
        "--node-name",
        "--completion-evidence",
        "--completion-evidence-sha256",
        "--allow-compose-rewrite",
        "--instant-deploy-compose-rewrite",
        "--allow-service-redeploy-refresh",
        "--allow-retired-genesis-proof-guardian-shim",
    }
    assert forbidden.isdisjoint(cleanup1_argv)
    assert forbidden.isdisjoint(cleanup2_argv)
    assert instance.state["post_work_cleanup_results"][0]["step"] == "cleanup1"
    assert instance.state["post_work_cleanup_results"][1]["step"] == "cleanup2"


def test_remove_node_harness_post_work_cleanup_uses_topology_wide_cleanup_scripts(tmp_path: Path) -> None:
    args = _parser_args(
        tmp_path,
        [
            "remove-node",
            "--runtime-state-root",
            str(tmp_path),
            "--network",
            "mainnet",
            "--node",
            "mainnetc-super2",
            "--run-dir",
            str(tmp_path / "runs"),
        ],
    )
    evidence_path, evidence_sha = _cleanup_completion_evidence(tmp_path, operation="remove-node")
    args.remove_finalize_evidence = evidence_path
    args.remove_finalize_evidence_sha256 = evidence_sha

    captured: list[tuple[str, list[str]]] = []

    def fake_run(step: str, argv: list[str], *, allow_failure: bool = False):  # noqa: ARG001
        captured.append((step, argv))
        return {
            "status": "pass",
            "summary": {"clean": True},
            "evidence_path": str(tmp_path / f"{step}.json"),
            "evidence_sha256": "d" * 64,
        }

    instance = harness.Harness(args)
    instance.run = fake_run
    instance.step_post_work_cleanup()

    assert [step for step, _ in captured] == [
        "post-work-cleanup-cleanup1",
        "post-work-cleanup-cleanup2",
    ]
    assert captured[0][1][1].endswith("tools/mother_post_work_cleanup_v2.py")
    assert captured[1][1][1].endswith("tools/mother_helper_cleanup2_yagni.py")
    assert captured[0][1][captured[0][1].index("--topology-evidence") + 1] == evidence_path
    assert captured[1][1][captured[1][1].index("--topology-evidence") + 1] == evidence_path
    assert "deletedservice123" not in captured[0][1]
    assert "deletedservice123" not in captured[1][1]


def test_post_work_cleanup_is_terminal_in_add_and_remove_harness_step_order() -> None:
    assert harness.SINGLE_NODE_STEPS[-1] == harness.POST_WORK_CLEANUP_STEP
    assert harness.REPLICA_ADMISSION_STEPS[-1] == harness.POST_WORK_CLEANUP_STEP
    assert harness.REMOVE_STEPS[-1] == harness.POST_WORK_CLEANUP_STEP
    assert harness.STEP_ORDER[-1] == harness.POST_WORK_CLEANUP_STEP
    assert harness.STEP_ORDER.count(harness.POST_WORK_CLEANUP_STEP) == 1
    assert harness.POST_WORK_CLEANUP_STEP not in harness.COMMON_STEPS
    assert harness.POST_WORK_CLEANUP_STEP not in harness.MUTATION_STEPS


def test_dynamic_post_work_cleanup_substeps_are_mutation_gated(tmp_path: Path) -> None:
    args = _parser_args(tmp_path)
    instance = harness.Harness(args)

    with pytest.raises(SystemExit) as exc:
        instance.run("post-work-cleanup-cleanup1", ["python", "-c", "raise SystemExit(99)"])

    assert exc.value.code == 3


def test_preflight_paranoia_is_in_add_and_remove_before_mutation_planning() -> None:
    assert harness.COMMON_STEPS[0:2] == ["detect-topology", "preflight-paranoia"]
    assert harness.REMOVE_STEPS[0:2] == ["detect-topology", "preflight-paranoia"]
    assert harness.COMMON_STEPS.index("preflight-paranoia") < harness.COMMON_STEPS.index("prep")
    assert harness.REMOVE_STEPS.index("preflight-paranoia") < harness.REMOVE_STEPS.index("remove-prep")
    assert "preflight-paranoia" not in harness.MUTATION_STEPS


def test_preflight_paranoia_cmd_uses_harness_selected_baseline(tmp_path: Path) -> None:
    args = _parser_args(tmp_path)
    args.baseline_evidence = str(tmp_path / "selected-topology.json")
    args.baseline_evidence_sha256 = "a" * 64

    instance = harness.Harness(args)
    argv = instance.preflight_paranoia_cmd()

    assert argv[1].endswith("tools/mother_preflight_paranoia.py")
    assert argv[2] == "add-node"
    assert argv[argv.index("--topology-evidence") + 1] == args.baseline_evidence
    assert argv[argv.index("--acknowledge-topology-evidence-sha256") + 1] == args.baseline_evidence_sha256
    assert argv[argv.index("--node") + 1] == "mainnetc-super2"


def test_preflight_paranoia_blocks_with_cleanup_command_as_final_line(tmp_path: Path, capsys) -> None:
    args = _parser_args(tmp_path)
    args.baseline_evidence = str(tmp_path / "selected-topology.json")
    args.baseline_evidence_sha256 = "a" * 64

    instance = harness.Harness(args)

    def fake_run(step: str, argv: list[str], *, allow_failure: bool = False):  # noqa: ARG001
        assert step == "preflight-paranoia"
        assert allow_failure is True
        return {
            "status": "cleanup-required",
            "cleanup_required": True,
            "cleanup_command": "python cleanup2.py execute",
            "summary": {
                "clean": False,
                "active_cleanup_helper_count": 1,
                "network_mutation_performed": False,
            },
        }

    instance.run = fake_run

    with pytest.raises(SystemExit) as exc:
        instance.step_preflight_paranoia()

    assert exc.value.code == 3
    output = capsys.readouterr().out
    assert "MOTHER_MUTATE_HARNESS_PREFLIGHT_PARANOIA_BLOCKED" in output
    assert output.rstrip().endswith("python cleanup2.py execute")
