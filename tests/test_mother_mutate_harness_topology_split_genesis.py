from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = ROOT / "mother_mutate_harness.py"

spec = importlib.util.spec_from_file_location("mother_mutate_harness_topology_split_under_test", HARNESS_PATH)
assert spec is not None and spec.loader is not None
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def _write(path: Path, document: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    return path


def _baseline(tmp_path: Path, *, genesis_sha256: str | None = None) -> Path:
    return _write(
        tmp_path / "mother" / "evidence" / "deployment-node-add-post-admission-observe" / "baseline.json",
        {
            "kind": "main_computer.mother.add_node_identity_reservation_topology_refresh_evidence.v1",
            "network": "mainnet",
            "current_topology": {
                "chain_id": 42424240,
                "genesis_sha256": genesis_sha256,
                "nodes": [],
                "validator_set": [],
            },
        },
    )


def _detection(node: str, service_uuid: str) -> dict:
    return {
        "observed_live_node_hints": [node],
        "observed_service_hints": [
            {
                "name": node,
                "uuid": service_uuid,
                "status": "running:healthy",
                "node_hints": [node],
            }
        ],
    }


def _bootstrap_evidence(
    tmp_path: Path,
    *,
    name: str,
    node: str,
    service_uuid: str,
    genesis_sha256: str,
    status: str = "failed",
) -> Path:
    return _write(
        tmp_path
        / "mother"
        / "evidence"
        / harness.SINGLE_NODE_BOOTSTRAP_EVIDENCE_DIRECTORY
        / name,
        {
            "kind": harness.SINGLE_NODE_BOOTSTRAP_EVIDENCE_KIND,
            "network": "mainnet",
            "status": status,
            "target": {
                "node": node,
                "created_service_uuid": service_uuid,
            },
            "bootstrap_plan": {
                "genesis_sha256": genesis_sha256,
            },
        },
    )


def test_recovers_missing_genesis_from_exact_live_bootstrap_service_lineage(tmp_path: Path) -> None:
    node = "mainneta-super1"
    service_uuid = "ofyg4js07psjwqwycofgv5jp"
    genesis = "a" * 64
    baseline = _baseline(tmp_path)
    source = _bootstrap_evidence(
        tmp_path,
        name="failed-a1.json",
        node=node,
        service_uuid=service_uuid,
        genesis_sha256=genesis,
        status="failed",
    )
    _bootstrap_evidence(
        tmp_path,
        name="wrong-service.json",
        node=node,
        service_uuid="different-service",
        genesis_sha256="b" * 64,
    )

    recovered = harness.recover_topology_split_genesis_from_bootstrap(
        runtime_state_root=tmp_path,
        network="mainnet",
        baseline_evidence=baseline,
        detection=_detection(node, service_uuid),
        reseal_nodes=[node],
    )

    assert recovered == {
        "genesis_sha256": genesis,
        "source_evidence": str(source),
        "service_uuid": service_uuid,
        "node": node,
    }


def test_existing_baseline_genesis_does_not_require_bootstrap_recovery(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path, genesis_sha256="c" * 64)

    recovered = harness.recover_topology_split_genesis_from_bootstrap(
        runtime_state_root=tmp_path,
        network="mainnet",
        baseline_evidence=baseline,
        detection={},
        reseal_nodes=["mainneta-super1"],
    )

    assert recovered is None


def test_recovery_fails_closed_when_exact_live_service_has_no_matching_bootstrap_evidence(tmp_path: Path) -> None:
    node = "mainneta-super1"
    service_uuid = "ofyg4js07psjwqwycofgv5jp"
    baseline = _baseline(tmp_path)
    _bootstrap_evidence(
        tmp_path,
        name="wrong-service.json",
        node=node,
        service_uuid="old-service-uuid",
        genesis_sha256="d" * 64,
    )

    with pytest.raises(SystemExit, match="GENESIS_LINEAGE_UNAVAILABLE"):
        harness.recover_topology_split_genesis_from_bootstrap(
            runtime_state_root=tmp_path,
            network="mainnet",
            baseline_evidence=baseline,
            detection=_detection(node, service_uuid),
            reseal_nodes=[node],
        )


def test_recovery_fails_closed_on_conflicting_genesis_for_same_live_service(tmp_path: Path) -> None:
    node = "mainneta-super1"
    service_uuid = "ofyg4js07psjwqwycofgv5jp"
    baseline = _baseline(tmp_path)
    _bootstrap_evidence(
        tmp_path,
        name="one.json",
        node=node,
        service_uuid=service_uuid,
        genesis_sha256="e" * 64,
    )
    _bootstrap_evidence(
        tmp_path,
        name="two.json",
        node=node,
        service_uuid=service_uuid,
        genesis_sha256="f" * 64,
    )

    with pytest.raises(SystemExit, match="GENESIS_CONFLICT"):
        harness.recover_topology_split_genesis_from_bootstrap(
            runtime_state_root=tmp_path,
            network="mainnet",
            baseline_evidence=baseline,
            detection=_detection(node, service_uuid),
            reseal_nodes=[node],
        )


def test_reseal_builder_command_receives_recovered_genesis_without_changing_source_evidence(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    instance = object.__new__(harness.Harness)
    instance.repo_root = ROOT
    instance.args = SimpleNamespace(
        runtime_state_root=str(tmp_path),
        network="mainnet",
        timeout=30.0,
        max_response_bytes=4194304,
    )
    instance.state = {"baseline_evidence": str(baseline)}

    argv = instance.out_of_band_reseal_input_cmd(
        ["mainneta-super1"],
        genesis_sha256="1" * 64,
    )

    assert argv[argv.index("--source-evidence") + 1] == str(baseline)
    assert argv[argv.index("--genesis-sha256") + 1] == "1" * 64
    assert argv[argv.index("--node") + 1] == "mainneta-super1"
