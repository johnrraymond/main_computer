from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import yaml

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

    assert result["status"] == "cleanup-required"
    assert result["cleanup_required"] is True
    assert result["summary"]["active_cleanup_helper_count"] == 1
    assert result["summary"]["blocking_conflict_count"] == 1
    assert result["active_cleanup_helpers"][0]["helper_service"] == "mother-node-remove-voter-mainnetc_super1"
    assert "mother_helper_cleanup2_yagni.py execute" in result["cleanup_command"]
    assert f'--topology-evidence {topology_path}' in result["cleanup_command"]
    assert "--acknowledge-topology-evidence-sha256 " + ("a" * 64) in result["cleanup_command"]
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


def test_preflight_recommends_cleanup_for_unhealthy_parent_with_retired_helpers(monkeypatch, tmp_path: Path) -> None:
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
    assert result["summary"]["cleanup_command_emitted"] is True
    assert "mother_helper_cleanup2_yagni.py execute" in result["cleanup_command"]
    assert f"--topology-evidence {topology_path}" in result["cleanup_command"]


def test_preflight_cli_prints_recommended_cleanup_command_as_final_line(monkeypatch, tmp_path: Path, capsys) -> None:
    cleanup_command = "python mother_helper_cleanup2_yagni.py execute --write-evidence"

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
            "cleanup_command": cleanup_command,
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
                "cleanup_command_emitted": True,
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
    assert output.rstrip().endswith(cleanup_command)


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
