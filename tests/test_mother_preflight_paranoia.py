from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import yaml
import pytest

import tools.mother_preflight_paranoia as paranoia


C1 = "0x9b809f05f8d68da17e697cd6ab040d4320494611"
A1 = "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"


def _private_state() -> SimpleNamespace:
    document = {
        "networks": {
            "mainnet": {
                "validators": {
                    "mainneta-super1": {"address": A1},
                    "mainnetc-super1": {"address": C1},
                }
            }
        }
    }
    return SimpleNamespace(document_bytes=yaml.safe_dump(document).encode("utf-8"))


def _install_fakes(monkeypatch, tmp_path: Path, compose_text: str, *, service_status: str = "running:healthy") -> Path:
    topology_path = tmp_path / "topology.json"
    topology_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paranoia, "_discover_current_topology_evidence", lambda *args, **kwargs: topology_path)
    monkeypatch.setattr(paranoia, "_load_private_state", lambda *args, **kwargs: _private_state())
    monkeypatch.setattr(
        paranoia,
        "_load_topology",
        lambda *args, **kwargs: {
            "path": topology_path,
            "sha256": "a" * 64,
            "discovered": True,
            "services": [
                {
                    "node": "mainnetc-super1",
                    "controller_id": "coolify-c",
                    "service_uuid": "trc0ohnjoobollafcx1i6w5x",
                }
            ],
        },
    )
    monkeypatch.setattr(paranoia, "_controller", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        paranoia,
        "_service_detail",
        lambda *args, **kwargs: {
            "missing": False,
            "receipt": {"status": 200},
            "payload": {"status": service_status, "docker_compose_raw": compose_text},
        },
    )
    return topology_path


def test_add_node_preflight_emits_cleanup_for_stale_remove_voter(monkeypatch, tmp_path: Path) -> None:
    compose = f"""
services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-node-remove-voter-mainnetc_super1:
    image: python:3.12-alpine
    command:
      - python
      - -c
      - |
        TARGET_VALIDATOR = '{A1}'
        REQUEST = json.loads('{{"id":1,"jsonrpc":"2.0","method":"qbft_proposeValidatorVote","params":["{A1}",false]}}')
"""
    topology_path = _install_fakes(monkeypatch, tmp_path, compose)

    result = paranoia.run_preflight_paranoia(
        operation="add-node",
        runtime_state_root=tmp_path,
        network="mainnet",
        node="mainneta-super1",
        python_executable="python.exe",
    )

    assert result["status"] == "stranded-remove-voter-cleanup-required"
    assert result["cleanup_required"] is True
    assert result["stranded_remove_voter_cleanup_required"] is True
    assert result["summary"]["active_cleanup_helper_count"] == 1
    assert result["summary"]["blocking_conflict_count"] == 1
    assert result["active_cleanup_helpers"][0]["helper_service"] == "mother-node-remove-voter-mainnetc_super1"
    assert "mother_helper_cleanup2_yagni.py execute" in result["cleanup_command"]
    assert f'--topology-evidence {topology_path}' in result["cleanup_command"]
    assert "--acknowledge-topology-evidence-sha256=" + ("a" * 64) in result["cleanup_command"]
    assert "--write-evidence" in result["cleanup_command"]


def test_preflight_ignores_cleanup2_retired_mimic(monkeypatch, tmp_path: Path) -> None:
    compose = """
services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-node-remove-voter-mainnetc_super1:
    image: alpine:3.20
    labels:
      main_computer.mother.post_work_shim: "true"
      main_computer.mother.retired_helper_mimic: "true"
      main_computer.mother.cleanup_scope: helper-cleanup2-yagni
      main_computer.mother.not_a_validator_voter: "true"
    command:
      - sh
      - -lc
      - while true; do sleep 30; done
"""
    _install_fakes(monkeypatch, tmp_path, compose)

    result = paranoia.run_preflight_paranoia(
        operation="add-node",
        runtime_state_root=tmp_path,
        network="mainnet",
        node="mainneta-super1",
    )

    assert result["status"] == "pass"
    assert result["cleanup_required"] is False
    assert result["cleanup_command"] is None
    assert result["summary"]["active_cleanup_helper_count"] == 0
    assert result["summary"]["retired_cleanup_helper_count"] == 1
    assert result["summary"]["blocking_conflict_count"] == 0


def test_remove_node_preflight_detects_stale_add_voter(monkeypatch, tmp_path: Path) -> None:
    compose = f"""
services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-add-node-validator-admission-voter-mainnetc_super1:
    image: python:3.12-alpine
    command:
      - python
      - -c
      - |
        CANDIDATE_VALIDATOR = '{A1}'
        REQUEST = json.loads('{{"id":1,"jsonrpc":"2.0","method":"qbft_proposeValidatorVote","params":["{A1}",true]}}')
"""
    _install_fakes(monkeypatch, tmp_path, compose)

    result = paranoia.run_preflight_paranoia(
        operation="remove-node",
        runtime_state_root=tmp_path,
        network="mainnet",
        node="mainneta-super1",
    )

    assert result["status"] == "cleanup-required"
    assert result["cleanup_required"] is True
    assert result["summary"]["active_cleanup_helper_count"] == 1
    assert result["summary"]["blocking_conflict_count"] == 1
    assert result["blocking_conflicts"][0]["reason"] == "opposite-add-node-voter-for-target"


def test_add_node_preflight_blocks_unhealthy_current_validator(monkeypatch, tmp_path: Path) -> None:
    compose = """
services:
  mainnetc-super1:
    image: hyperledger/besu:latest
"""
    _install_fakes(monkeypatch, tmp_path, compose, service_status="running:unhealthy")

    result = paranoia.run_preflight_paranoia(
        operation="add-node",
        runtime_state_root=tmp_path,
        network="mainnet",
        node="mainneta-super1",
    )

    assert result["status"] == "required-validator-unhealthy"
    assert result["cleanup_required"] is False
    assert result["required_validator_unhealthy"] is True
    assert result["summary"]["required_validator_unhealthy_count"] == 1
    assert result["required_validator_health_failures"][0]["node"] == "mainnetc-super1"
    assert result["required_validator_health_failures"][0]["service_status"] == "running:unhealthy"


def test_preflight_recommends_broken_validator_cleanup_for_unhealthy_parent_with_retired_helpers(monkeypatch, tmp_path: Path) -> None:
    compose = """
services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-node-remove-voter-mainnetc_super1:
    image: alpine:3.20
    labels:
      main_computer.mother.post_work_shim: "true"
      main_computer.mother.retired_helper_mimic: "true"
      main_computer.mother.cleanup_scope: helper-cleanup2-yagni
      main_computer.mother.not_a_validator_voter: "true"
    command:
      - sh
      - -lc
      - while true; do sleep 30; done
"""
    topology_path = _install_fakes(
        monkeypatch,
        tmp_path,
        compose,
        service_status="degraded:unhealthy",
    )

    result = paranoia.run_preflight_paranoia(
        operation="add-node",
        runtime_state_root=tmp_path,
        network="mainnet",
        node="mainneta-super1",
        python_executable="python.exe",
    )

    assert result["status"] == "required-validator-unhealthy"
    assert result["cleanup_required"] is False
    assert result["required_validator_unhealthy"] is True
    assert result["summary"]["retired_cleanup_helper_count"] == 1
    assert result["summary"]["cleanup_command_emitted"] is False
    assert result["cleanup_command"] is None
    assert result["summary"]["broken_validator_cleanup_command_emitted"] is True
    assert result["broken_validator_cleanup_target"] == "mainnetc-super1"
    assert "mother_broken_validator_cleanup.py --dry-run" in result["broken_validator_cleanup_command"]
    assert f"--topology-evidence {topology_path}" in result["broken_validator_cleanup_command"]
    assert "--acknowledge-topology-evidence-sha256=" + ("a" * 64) in result["broken_validator_cleanup_command"]


def test_preflight_cli_prints_recommended_broken_validator_cleanup_as_final_line(monkeypatch, tmp_path: Path, capsys) -> None:
    broken_command = "python mother_broken_validator_cleanup.py --dry-run --node mainnetc-super1"

    def fake_run_preflight_paranoia(**kwargs):  # noqa: ARG001
        return {
            "kind": paranoia.KIND,
            "schema_version": 1,
            "status": "required-validator-unhealthy",
            "operation": "add-node",
            "network": "mainnet",
            "target_node": "mainneta-super1",
            "target_validator": A1,
            "read_only": True,
            "cleanup_required": False,
            "required_validator_unhealthy": True,
            "cleanup_command": None,
            "broken_validator_cleanup_required": True,
            "broken_validator_cleanup_target": "mainnetc-super1",
            "broken_validator_cleanup_command": broken_command,
            "topology_evidence": {"path": str(tmp_path / "topology.json"), "sha256": "a" * 64},
            "active_cleanup_helpers": [],
            "retired_cleanup_helpers": [{"helper_service": "mother-node-remove-voter-mainnetc_super1"}],
            "blocking_conflicts": [],
            "required_validator_health_failures": [{"node": "mainnetc-super1"}],
            "service_observations": [],
            "summary": {
                "active_cleanup_helper_count": 0,
                "retired_cleanup_helper_count": 1,
                "blocking_conflict_count": 0,
                "required_validator_unhealthy_count": 1,
                "cleanup_command_emitted": False,
                "broken_validator_cleanup_command_emitted": True,
                "network_mutation_performed": False,
                "clean": False,
            },
        }

    monkeypatch.setattr(paranoia, "run_preflight_paranoia", fake_run_preflight_paranoia)

    code = paranoia.main(
        [
            "add-node",
            "--runtime-state-root",
            str(tmp_path),
            "--network",
            "mainnet",
            "--node",
            "mainneta-super1",
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "MOTHER_PREFLIGHT_PARANOIA_REQUIRED_VALIDATOR_UNHEALTHY" in output
    assert "Run this broken-validator cleanup dry-run before the mutation:" in output
    assert output.rstrip().endswith(broken_command)


def test_add_node_preflight_accepts_acknowledged_empty_topology_evidence(monkeypatch, tmp_path: Path) -> None:
    evidence_dir = tmp_path / "mother" / "evidence" / "deployment-live-topology-empty-rectification"
    evidence_dir.mkdir(parents=True)
    topology = {
        "kind": "main_computer.mother.live_topology_empty_rectification_evidence.v1",
        "status": "pass",
        "network": "mainnet",
        "completed_at": "2026-08-24T20:58:13Z",
        "next_phase": "add-node-prep-mainnet",
        "summary": {
            "complete": True,
            "clean": True,
            "topology_rectified": True,
            "empty_topology_marked_by_evidence": True,
            "current_topology_marked_by_evidence": True,
            "final_nodes": [],
            "next_phase": "add-node-prep-mainnet",
        },
        "final_topology": {
            "nodes": [],
            "services": {},
            "validator_set": [],
        },
    }
    topology_path = evidence_dir / "20260824T205813Z-empty.json"
    topology_path.write_text(json.dumps(topology, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    topology_sha256 = hashlib.sha256(topology_path.read_bytes()).hexdigest()

    monkeypatch.setattr(paranoia, "_load_private_state", lambda *args, **kwargs: _private_state())
    monkeypatch.setattr(
        paranoia,
        "_load_topology",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("_load_topology should not be called")),
    )
    monkeypatch.setattr(
        paranoia,
        "_service_detail",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no live service should be inspected")),
    )

    result = paranoia.run_preflight_paranoia(
        operation="add-node",
        runtime_state_root=tmp_path,
        network="mainnet",
        node="mainneta-super1",
        topology_evidence=topology_path,
        acknowledged_topology_evidence_sha256=topology_sha256,
    )

    assert result["status"] == "pass"
    assert result["cleanup_required"] is False
    assert result["service_observations"] == []
    assert result["summary"]["empty_topology_accepted_for_add_node"] is True
    assert result["topology_evidence"]["path"] == str(topology_path.resolve(strict=False))
    assert result["topology_evidence"]["sha256"] == topology_sha256



def test_discovery_prefers_newer_live_current_topology(tmp_path: Path) -> None:
    evidence_root = tmp_path / "mother" / "evidence"
    old_dir = evidence_root / "deployment-node-add-post-admission-observe"
    live_dir = evidence_root / "deployment-live-current-topology"
    old_dir.mkdir(parents=True)
    live_dir.mkdir(parents=True)

    base = {
        "network": "mainnet",
        "status": "pass",
        "summary": {
            "complete": True,
            "clean": True,
            "topology_current": True,
        },
    }
    old = dict(base)
    old["completed_at"] = "2026-08-22T21:11:11Z"
    old_path = old_dir / "old.json"
    old_path.write_text(__import__("json").dumps(old), encoding="utf-8")

    live = dict(base)
    live["completed_at"] = "2026-08-22T22:49:16Z"
    live_path = live_dir / "live.json"
    live_path.write_text(__import__("json").dumps(live), encoding="utf-8")

    assert paranoia._discover_current_topology_evidence(tmp_path, network="mainnet") == live_path

def test_preflight_is_advisory_zero_exit_when_cleanup_required(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        paranoia,
        "run_preflight_paranoia",
        lambda **kwargs: {
            "status": "cleanup-required",
            "operation": "add-node",
            "cleanup_required": True,
            "cleanup_command": "python cleanup.py execute",
            "blocking_conflicts": [{"reason": "test"}],
            "summary": {"active_cleanup_helper_count": 1},
        },
    )

    rc = paranoia.main(
        [
            "add-node",
            "--runtime-state-root",
            "runtime/state",
            "--network",
            "mainnet",
            "--node",
            "mainneta-super1",
        ]
    )

    output = capsys.readouterr().out
    assert rc == 0
    assert "MOTHER_PREFLIGHT_PARANOIA_CLEANUP_REQUIRED" in output
    assert "MOTHER_PREFLIGHT_PARANOIA_BLOCKING_CONFLICTS" in output
    assert "python cleanup.py execute" in output
    assert output.rstrip().endswith("python cleanup.py execute")
    assert output.index('"cleanup_command": "python cleanup.py execute"') < output.rindex(
        "Run this cleanup command before the mutation:"
    )


def test_unfinished_broken_validator_operation_takes_precedence_over_cleanup(monkeypatch, tmp_path: Path) -> None:
    compose = f"""
services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-node-remove-voter-mainnetc_super1:
    image: python:3.12-alpine
    command:
      - python
      - -c
      - |
        TARGET_VALIDATOR = '{A1}'
        REQUEST = json.loads('{{"id":1,"jsonrpc":"2.0","method":"qbft_proposeValidatorVote","params":["{A1}",false]}}')
"""
    topology_path = _install_fakes(
        monkeypatch,
        tmp_path,
        compose,
        service_status="degraded:unhealthy",
    )
    operation_path = (
        tmp_path
        / "mother"
        / "evidence"
        / paranoia.BROKEN_VALIDATOR_OPERATION_DIR
        / "mainnet-mainnetc-super1.json"
    )
    operation_path.parent.mkdir(parents=True, exist_ok=True)
    resume_command = (
        "python mother_broken_validator_cleanup.py --execute --node mainnetc-super1 "
        "--topology-evidence C:\\stale-topology.json --topology-max-age-seconds 900 "
        "--assessment-max-age-seconds 300"
    )
    assessment_path = tmp_path / "mother" / "evidence" / "deployment-broken-validator-cleanup-assessment" / "assessment.json"
    assessment_sha = "b" * 64
    operation_path.write_text(
        json.dumps(
            {
                "kind": paranoia.BROKEN_VALIDATOR_OPERATION_KIND,
                "schema_version": 1,
                "operation_id": "mother-broken-validator-mainnet-mainnetc-super1-test",
                "network": "mainnet",
                "target": {"node": "mainnetc-super1", "validator_address": C1},
                "status": "authorized",
                "assessment_evidence": {"path": str(assessment_path), "sha256": assessment_sha},
                "resume_command": resume_command,
            }
        ),
        encoding="utf-8",
    )

    result = paranoia.run_preflight_paranoia(
        operation="add-node",
        runtime_state_root=tmp_path,
        network="mainnet",
        node="mainneta-super1",
        python_executable="python.exe",
    )

    assert result["status"] == "stranded-remove-voter-cleanup-required"
    assert result["cleanup_required"] is True
    assert result["stranded_remove_voter_cleanup_required"] is True
    assert result["cleanup_deferred_to_broken_validator_operation"] is False
    assert result["broken_validator_operation_precedence"] is False
    assert result["broken_validator_operation_bootstrap"] is False
    assert result["broken_validator_operation"]["operation_id"] == "mother-broken-validator-mainnet-mainnetc-super1-test"
    assert result["broken_validator_cleanup_command"] is None
    command = result["cleanup_command"]
    assert "mother_helper_cleanup2_yagni.py execute" in command
    assert str(topology_path) in command
    assert result["topology_evidence"]["path"] == str(topology_path)


def test_unhealthy_validator_with_only_remove_voter_helpers_bootstraps_broken_validator_ownership(monkeypatch, tmp_path: Path) -> None:
    compose = f"""
services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-node-remove-voter-mainnetc_super1:
    image: python:3.12-alpine
    command:
      - python
      - -c
      - |
        TARGET_VALIDATOR = '{A1}'
        REQUEST = json.loads('{{"id":1,"jsonrpc":"2.0","method":"qbft_proposeValidatorVote","params":["{A1}",false]}}')
"""
    _install_fakes(
        monkeypatch,
        tmp_path,
        compose,
        service_status="degraded:unhealthy",
    )

    result = paranoia.run_preflight_paranoia(
        operation="add-node",
        runtime_state_root=tmp_path,
        network="mainnet",
        node="mainneta-super1",
        python_executable="python.exe",
    )

    assert result["status"] == "stranded-remove-voter-cleanup-required"
    assert result["cleanup_required"] is True
    assert result["stranded_remove_voter_cleanup_required"] is True
    assert result["cleanup_deferred_to_broken_validator_operation"] is False
    assert result["broken_validator_operation"] is None
    assert result["broken_validator_operation_bootstrap"] is True
    assert result["broken_validator_operation_precedence"] is False
    assert result["broken_validator_cleanup_command"] is None
    assert "mother_helper_cleanup2_yagni.py execute" in result["cleanup_command"]


def _testnet_first_birth_receipt() -> dict:
    """Exact structural contract emitted by the verified-first-birth writer."""
    chain = {
        "source": "mother-clean-start-live-inventory",
        "chain_id": 42424241,
        "genesis_sha256": None,
        "genesis_lineage": "fresh-required",
        "fresh_genesis_required": True,
        "nodes": [], "services": {}, "validator_count": 0, "validator_set": [],
        "baseline_topology_used_as_live": False,
    }
    return {
        "kind": "main_computer.mother.live_topology_empty_rectification_evidence.v1",
        "schema_version": 1,
        "status": "pass", "network": "testnet",
        "mode": "verified-first-birth-clean-start",
        "next_phase": "add-node-prep-testnet", "chain_id": 42424241,
        "genesis_sha256": None, "fresh_genesis_required": True,
        "mother_binding": {"generation": 23, "content_sha256": "a" * 64, "manifest_sha256": "b" * 64},
        "current_topology": chain, "final_topology": dict(chain),
        "authority": {"empty_topology_marked_by_evidence": True, "live_mutation_performed": False},
        "policy": {"allowed_http_methods": ["GET"], "network_access_performed": True, "live_mutation_performed": False},
        "summary": {
            "clean": True, "complete": True, "fresh_genesis_required": True,
            "empty_topology_marked_by_evidence": True,
            "final_nodes": [], "final_validator_count": 0, "final_validator_set": [],
            "next_phase": "add-node-prep-testnet",
            "inventory_controller_count": 1, "inventory_probe_count": 5,
        },
    }


def _testnet_private_state():
    content = {"networks": {"testnet": {
        "chain_id": 42424241,
        "validators": {"testnetb-super1": {"address": A1}},
    }}}
    return SimpleNamespace(
        document_bytes=yaml.safe_dump(content).encode(),
        binding=SimpleNamespace(
            generation=23,
            content_hash=SimpleNamespace(digest="a" * 64),
            recovery_manifest_hash=SimpleNamespace(digest="b" * 64),
        ),
    )


def test_testnet_first_birth_receipt_without_legacy_marker_passes_preflight(monkeypatch, tmp_path: Path) -> None:
    evidence = _testnet_first_birth_receipt()
    assert "current_topology_marked_by_evidence" not in evidence["summary"]
    directory = tmp_path / "mother" / "evidence" / "deployment-live-topology-empty-rectification"
    directory.mkdir(parents=True)
    path = directory / "20261018-first-birth.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(paranoia, "_load_private_state", lambda *args, **kwargs: _testnet_private_state())
    monkeypatch.setattr(paranoia, "_load_topology", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("nonempty cleanup2 parser invoked")))
    monkeypatch.setattr(paranoia, "_service_detail", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("empty topology has no services")))

    result = paranoia.run_preflight_paranoia(
        operation="add-node", runtime_state_root=tmp_path, network="testnet",
        node="testnetb-super1", topology_evidence=path,
        acknowledged_topology_evidence_sha256=sha,
    )
    assert result["status"] == "pass"
    assert result["summary"]["empty_topology_accepted_for_add_node"] is True
    assert result["service_observations"] == []
    assert result["cleanup_command"] is None


def test_first_birth_refuses_wrong_binding_incomplete_inventory_or_nonempty_chain(monkeypatch, tmp_path: Path) -> None:
    import pytest
    monkeypatch.setattr(paranoia, "_load_private_state", lambda *args, **kwargs: _testnet_private_state())
    directory = tmp_path / "mother" / "evidence" / "deployment-live-topology-empty-rectification"
    directory.mkdir(parents=True)
    for case in ("wrong-binding", "missing-probes", "nonempty-validator", "wrong-chain"):
        evidence = _testnet_first_birth_receipt()
        if case == "wrong-binding":
            evidence["mother_binding"]["generation"] = 22
        elif case == "missing-probes":
            evidence["summary"]["inventory_probe_count"] = 4
        elif case == "nonempty-validator":
            evidence["final_topology"]["validator_set"] = [A1]
        else:
            evidence["chain_id"] = 42424240
        path = directory / f"{case}.json"
        path.write_text(json.dumps(evidence), encoding="utf-8")
        with pytest.raises(paranoia.MotherPreflightParanoiaError, match="verified first-birth"):
            paranoia.run_preflight_paranoia(
                operation="add-node", runtime_state_root=tmp_path, network="testnet",
                node="testnetb-super1", topology_evidence=path,
                acknowledged_topology_evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )


def test_first_birth_refuses_wrong_network_and_unauthenticated_sha(monkeypatch, tmp_path: Path) -> None:
    import pytest
    evidence = _testnet_first_birth_receipt()
    path = tmp_path / "mother" / "evidence" / "deployment-live-topology-empty-rectification" / "first-birth.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(evidence), encoding="utf-8")
    monkeypatch.setattr(paranoia, "_load_private_state", lambda *args, **kwargs: _testnet_private_state())
    kwargs = dict(operation="add-node", runtime_state_root=tmp_path, network="testnet",
                  node="testnetb-super1", topology_evidence=path)
    with pytest.raises(paranoia.MotherPreflightParanoiaError, match="SHA-256"):
        paranoia.run_preflight_paranoia(**kwargs, acknowledged_topology_evidence_sha256="0" * 64)
    evidence["network"] = "mainnet"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(paranoia.MotherPreflightParanoiaError, match="network"):
        paranoia.run_preflight_paranoia(
            **kwargs, acknowledged_topology_evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )


def _final_node_removal_baseline(network: str, node: str) -> dict:
    """The finalization writer's shape (including its historical route bindings)."""
    return {
        "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
        "status": "pass", "network": network,
        "next_phase": f"remove-node-finalized-{network}",
        "target": {"node": node, "service_uuid": "old-service", "controller_id": "coolify-b"},
        "target_service_observation": {"node": node, "service_uuid": "old-service", "absent": True},
        "survivors": [],
        "pre_removal_topology": {"nodes": [node], "services": {node: {"service_uuid": "old-service"}},
                                 "validator_set": [A1]},
        "final_topology": {"nodes": [], "services": {}, "validator_count": 0, "validator_set": [],
                           "validator_route_bindings": {node: {"p2p_endpoint": "10.124.0.3:30303"}}},
        "authority": {"single_node_decommission": True, "target_service_absence_proven": True},
        "summary": {
            "clean": True, "complete": True, "topology_current": True,
            "current_topology_marked_by_evidence": True,
            "single_node_decommission": True, "target_service_absent": True,
            "service_deletion_performed": True,
            "final_validator_count": 0, "final_validator_set": [],
            "survivor_nodes": [], "survivor_nodes_observed": [],
            "validator_removal_vote_required": False,
        },
    }


def _write_final_node_removal_baseline(tmp_path: Path, baseline: dict) -> tuple[Path, str]:
    directory = tmp_path / "mother" / "evidence" / "deployment-node-remove-finalize"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "removed-final-node.json"
    path.write_text(json.dumps(baseline, sort_keys=True) + "\n", encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("network,node", [
    ("mainnet", "mainneta-super1"), ("testnet", "testnetb-super1"),
])
def test_add_node_accepts_completed_final_node_decommission_as_empty_topology(
    monkeypatch, tmp_path: Path, network: str, node: str,
) -> None:
    baseline = _final_node_removal_baseline(network, node)
    path, digest = _write_final_node_removal_baseline(tmp_path, baseline)
    monkeypatch.setattr(paranoia, "_load_private_state", lambda *args, **kwargs: _testnet_private_state() if network == "testnet" else _private_state())
    monkeypatch.setattr(paranoia, "_load_topology", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("nonempty parser used")))
    monkeypatch.setattr(paranoia, "_service_detail", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("empty topology has no live nodes")))

    result = paranoia.run_preflight_paranoia(
        operation="add-node", runtime_state_root=tmp_path, network=network,
        node=node, topology_evidence=path,
        acknowledged_topology_evidence_sha256=digest,
    )
    assert result["status"] == "pass"
    assert result["summary"]["clean"] is True
    assert result["summary"]["empty_topology_accepted_for_add_node"] is True
    assert result["service_observations"] == []
    assert result["topology_evidence"]["sha256"] == digest


@pytest.mark.parametrize("change", [
    "nonempty-survivor", "nonempty-validator", "unconfirmed-service-absence",
    "wrong-kind", "missing-single-node-proof", "pre-removal-multiple-nodes",
    "mismatched-target", "mismatched-final-validator-count",
])
def test_add_node_rejects_invalid_final_node_decommission_baseline(
    monkeypatch, tmp_path: Path, change: str,
) -> None:
    baseline = _final_node_removal_baseline("testnet", "testnetb-super1")
    if change == "nonempty-survivor":
        baseline["final_topology"]["nodes"] = ["testneta-super1"]
    elif change == "nonempty-validator":
        baseline["final_topology"]["validator_set"] = [A1]
    elif change == "unconfirmed-service-absence":
        baseline["target_service_observation"]["absent"] = False
    elif change == "wrong-kind":
        baseline["kind"] = "generic-topology"
    elif change == "missing-single-node-proof":
        baseline["summary"]["single_node_decommission"] = False
    elif change == "pre-removal-multiple-nodes":
        baseline["pre_removal_topology"]["nodes"] = ["testneta-super1", "testnetb-super1"]
    elif change == "mismatched-target":
        baseline["target_service_observation"]["node"] = "testneta-super1"
    elif change == "mismatched-final-validator-count":
        baseline["summary"]["final_validator_count"] = 1
    path, digest = _write_final_node_removal_baseline(tmp_path, baseline)
    monkeypatch.setattr(paranoia, "_load_private_state", lambda *args, **kwargs: _testnet_private_state())
    with pytest.raises(paranoia.MotherPreflightParanoiaError, match="(does not list current nodes|lacks service records)"):
        paranoia.run_preflight_paranoia(
            operation="add-node", runtime_state_root=tmp_path, network="testnet",
            node="testnetb-super1", topology_evidence=path,
            acknowledged_topology_evidence_sha256=digest,
        )


def test_final_node_decommission_is_not_an_acceptable_remove_node_baseline(monkeypatch, tmp_path: Path) -> None:
    baseline = _final_node_removal_baseline("testnet", "testnetb-super1")
    path, digest = _write_final_node_removal_baseline(tmp_path, baseline)
    monkeypatch.setattr(paranoia, "_load_private_state", lambda *args, **kwargs: _testnet_private_state())
    with pytest.raises(paranoia.MotherPreflightParanoiaError, match="does not list current nodes"):
        paranoia.run_preflight_paranoia(
            operation="remove-node", runtime_state_root=tmp_path, network="testnet",
            node="testnetb-super1", topology_evidence=path,
            acknowledged_topology_evidence_sha256=digest,
        )


def test_final_node_decommission_rejects_wrong_acknowledged_hash_and_network(monkeypatch, tmp_path: Path) -> None:
    baseline = _final_node_removal_baseline("testnet", "testnetb-super1")
    path, digest = _write_final_node_removal_baseline(tmp_path, baseline)
    monkeypatch.setattr(paranoia, "_load_private_state", lambda *args, **kwargs: _testnet_private_state())
    with pytest.raises(paranoia.MotherPreflightParanoiaError, match="SHA-256 does not match"):
        paranoia.run_preflight_paranoia(
            operation="add-node", runtime_state_root=tmp_path, network="testnet",
            node="testnetb-super1", topology_evidence=path,
            acknowledged_topology_evidence_sha256="0" * 64,
        )
    with pytest.raises(paranoia.MotherPreflightParanoiaError, match="network does not match"):
        paranoia.run_preflight_paranoia(
            operation="add-node", runtime_state_root=tmp_path, network="mainnet",
            node="mainneta-super1", topology_evidence=path,
            acknowledged_topology_evidence_sha256=digest,
        )
