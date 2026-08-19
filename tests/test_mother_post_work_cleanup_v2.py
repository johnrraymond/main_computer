from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tools.mother_post_work_cleanup_v2 import (
    MotherPostWorkCleanupV2Error,
    _build_parser,
    _operation,
    run_post_work_cleanup_v2,
)


def _write_json(path: Path, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(body, encoding="utf-8")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _evidence_tree(tmp_path: Path, *, clean: bool = True) -> tuple[Path, Path, str]:
    runtime = tmp_path / "runtime" / "state"
    mother = runtime / "mother"
    admission = mother / "evidence" / "deployment-node-add-validator-admission" / "admission.json"
    admission_sha = _write_json(
        admission,
        {
            "kind": "main_computer.mother.deployment_node_add_validator_admission_evidence.v1",
            "network": "mainnet",
            "status": "pass",
            "summary": {
                "complete": True,
                "final_validator_set_verified": True,
                "target_node": "mainneta-super1",
            },
            "transient_voter_guardian_nodes": ["mainnetc-super1"],
        },
    )
    topology = mother / "evidence" / "deployment-node-add-post-admission-observe" / "topology.json"
    topology_sha = _write_json(
        topology,
        {
            "kind": "main_computer.mother.deployment_node_add_post_admission_topology_evidence.v1",
            "schema_version": 1,
            "completed_at": "2026-08-19T00:49:28Z",
            "network": "mainnet",
            "status": "pass",
            "summary": {
                "clean": clean,
                "complete": True,
                "final_nodes": ["mainnetc-super1", "mainneta-super1"],
                "final_validator_set": [
                    "0x9b809f05f8d68da17e697cd6ab040d4320494611",
                    "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                ],
                "topology_current": True,
            },
            "source_validator_admission_evidence": {
                "locator": "evidence/deployment-node-add-validator-admission/admission.json",
                "sha256": admission_sha,
            },
            "final_topology": {
                "chain_id": 42424240,
                "nodes": ["mainnetc-super1", "mainneta-super1"],
                "validator_set": [
                    "0x9b809f05f8d68da17e697cd6ab040d4320494611",
                    "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                ],
                "services": {
                    "mainnetc-super1": {
                        "controller_id": "coolify-c",
                        "service_uuid": "lacn9nce720smwusl4wyqhyt",
                        "validator_route": {"vpn_ip": "10.116.0.2", "p2p_port": 30303, "p2p_endpoint": "10.116.0.2:30303"},
                    },
                    "mainneta-super1": {
                        "controller_id": "coolify-a",
                        "service_uuid": "hndpvx15ciqpqpxptnrslibt",
                        "validator_route": {"vpn_ip": "10.116.0.3", "p2p_port": 30303, "p2p_endpoint": "10.116.0.3:30303"},
                    },
                },
            },
            # Refreshed Coolify observations may omit route data.  The orchestrator
            # must still keep the route metadata from final_topology.services.
            "service_observations": [
                {
                    "controller_id": "coolify-c",
                    "node": "mainnetc-super1",
                    "service_uuid": "lacn9nce720smwusl4wyqhyt",
                },
                {
                    "controller_id": "coolify-a",
                    "node": "mainneta-super1",
                    "service_uuid": "hndpvx15ciqpqpxptnrslibt",
                },
            ],
        },
    )
    return runtime, topology, topology_sha


def test_execute_finds_latest_evidence_and_rpc_urls_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, topology, topology_sha = _evidence_tree(tmp_path)
    calls: list[tuple[str, str | None]] = []

    def admission_execute(private_state, **kwargs):  # noqa: ANN001
        calls.append(("admission-voter", kwargs["node"]))
        assert kwargs["node"] == "mainnetc-super1"
        assert kwargs["allow_retired_admission_voter_shim"] is True
        assert kwargs["acknowledged_service_uuid"] == "lacn9nce720smwusl4wyqhyt"
        assert str(kwargs["admission_evidence"]).endswith("admission.json")
        return {"status": "pass", "coolify_touched": True}

    def activation_execute(private_state, **kwargs):  # noqa: ANN001
        calls.append(("activation-guardian", kwargs["node"]))
        assert kwargs["allow_retired_activation_guardian_shim"] is True
        return {"status": "pass", "coolify_touched": True}

    def genesis_execute(private_state, **kwargs):  # noqa: ANN001
        calls.append(("genesis-proof-guardian", kwargs["node"]))
        assert kwargs["allow_retired_genesis_proof_guardian_shim"] is True
        return {"status": "pass", "coolify_touched": True}

    def chain_run(args):  # noqa: ANN001
        calls.append(("chain-rpc-preflight" if args.mode == "inspect" else "chain-cleanup", None))
        assert args.rpc_url == [
            "mainnetc-super1=http://10.116.0.2:8545",
            "mainneta-super1=http://10.116.0.3:8545",
        ]
        assert args.committed_validator_address == [
            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        ]
        if args.mode == "execute":
            assert args.allow_discard_satisfied_pending_votes is True
            assert args.acknowledge_chain_cleanup_only is True
        else:
            assert args.allow_discard_satisfied_pending_votes is False
            assert args.acknowledge_chain_cleanup_only is False
        return {"status": "pass", "mode": args.mode, "summary": {"cleared_count": 0}}

    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.execute_admission_voter_cleanup", admission_execute)
    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.execute_activation_guardian_cleanup", activation_execute)
    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.execute_genesis_proof_guardian_cleanup", genesis_execute)
    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.run_chain_cleanup", chain_run)

    result = run_post_work_cleanup_v2(
        object(),
        runtime_state_root=runtime,
        network="mainnet",
        mode="execute",
    )

    assert result["status"] == "pass"
    assert result["topology_evidence"]["path"] == str(topology)
    assert result["topology_evidence"]["sha256"] == topology_sha
    assert result["topology_evidence"]["discovered_from_disk"] is True
    assert calls == [
        ("chain-rpc-preflight", None),
        ("chain-cleanup", None),
        ("admission-voter", "mainnetc-super1"),
        ("activation-guardian", "mainnetc-super1"),
        ("activation-guardian", "mainneta-super1"),
        ("genesis-proof-guardian", "mainnetc-super1"),
        ("genesis-proof-guardian", "mainneta-super1"),
    ]
    assert result["summary"]["service_cleanup_order"] == [
        "admission-voter",
        "activation-guardian",
        "genesis-proof-guardian",
    ]
    assert result["summary"]["chain_rpc_preflight_order"] == "first"
    assert result["summary"]["chain_cleanup_order"] == "before-service-cleanup"
    assert result["summary"]["coolify_parent_redeploy_allowed"] is False
    assert result["summary"]["coolify_parent_redeploy_performed"] is False


def test_private_state_operation_identity_matches_repo_model() -> None:
    operation = _operation("mainnet", "execute")

    assert operation.network == "mainnet"
    assert operation.operation_kind == "MOTHER-OP-ADD-NODE"
    assert operation.operation_id.startswith("mother-post-work-cleanup-v2-execute-mainnet-")
    assert operation.request_id == f"{operation.operation_id}-request"


def test_cli_only_requires_runtime_state_root_and_network() -> None:
    args = _build_parser().parse_args(
        [
            "execute",
            "--runtime-state-root",
            r"C:\Users\subsi\main_computer\runtime\state",
            "--network",
            "mainnet",
            "--instant-deploy",
        ]
    )

    assert args.topology_evidence is None
    assert args.acknowledge_topology_evidence_sha256 is None
    assert args.rpc_url == []
    assert args.cleanup_all is False


def test_rejects_instant_deploy_before_cleanup(tmp_path: Path) -> None:
    runtime, topology, topology_sha = _evidence_tree(tmp_path)

    with pytest.raises(MotherPostWorkCleanupV2Error) as exc:
        run_post_work_cleanup_v2(
            object(),
            runtime_state_root=runtime,
            network="mainnet",
            topology_evidence=topology,
            acknowledged_topology_evidence_sha256=topology_sha,
            mode="execute",
            instant_deploy=True,
        )

    assert exc.value.code == "MOTHER_POST_WORK_CLEANUP_V2_PARENT_REDEPLOY_FORBIDDEN"


def test_chain_preflight_failure_stops_before_service_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, topology, topology_sha = _evidence_tree(tmp_path)
    calls: list[str] = []

    def chain_run(args):  # noqa: ANN001
        calls.append(args.mode)
        raise TimeoutError("rpc path timed out")

    def service_cleanup(private_state, **kwargs):  # noqa: ANN001
        raise AssertionError("service cleanup should not run after chain preflight failure")

    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.run_chain_cleanup", chain_run)
    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.execute_admission_voter_cleanup", service_cleanup)
    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.execute_activation_guardian_cleanup", service_cleanup)
    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.execute_genesis_proof_guardian_cleanup", service_cleanup)

    result = run_post_work_cleanup_v2(
        object(),
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology,
        acknowledged_topology_evidence_sha256=topology_sha,
        mode="execute",
    )

    assert result["status"] == "failed"
    assert calls == ["inspect"]
    assert result["summary"]["service_cleanup_started"] is False
    assert result["steps"][0]["step"] == "chain-rpc-preflight"
    assert result["steps"][0]["failed_before_service_cleanup"] is True
    assert "host_probe_commands" in result["steps"][0]


def test_explicit_rpc_urls_override_disk_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, topology, topology_sha = _evidence_tree(tmp_path)

    monkeypatch.setattr(
        "tools.mother_post_work_cleanup_v2.execute_admission_voter_cleanup",
        lambda private_state, **kwargs: {"status": "pass"},
    )
    monkeypatch.setattr(
        "tools.mother_post_work_cleanup_v2.execute_activation_guardian_cleanup",
        lambda private_state, **kwargs: {"status": "pass"},
    )
    monkeypatch.setattr(
        "tools.mother_post_work_cleanup_v2.execute_genesis_proof_guardian_cleanup",
        lambda private_state, **kwargs: {"status": "pass"},
    )

    def chain_run(args):  # noqa: ANN001
        assert args.rpc_url == ["mainnetc-super1=http://127.0.0.1:8545"]
        return {"status": "pass", "mode": args.mode, "summary": {"cleared_count": 0}}

    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.run_chain_cleanup", chain_run)

    result = run_post_work_cleanup_v2(
        object(),
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology,
        acknowledged_topology_evidence_sha256=topology_sha,
        mode="execute",
        rpc_urls=["mainnetc-super1=http://127.0.0.1:8545"],
    )

    assert result["status"] == "pass"
    assert result["topology_evidence"]["discovered_from_disk"] is False



def test_cleanup_all_runs_local_cleanup1_before_service_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, topology, topology_sha = _evidence_tree(tmp_path)
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.run_chain_cleanup", lambda args: (_ for _ in ()).throw(AssertionError("remote chain cleanup should not run")))

    def admission_execute(private_state, **kwargs):  # noqa: ANN001
        calls.append(("admission-voter", kwargs["node"]))
        return {"status": "pass"}

    def activation_execute(private_state, **kwargs):  # noqa: ANN001
        calls.append(("activation-guardian", kwargs["node"]))
        return {"status": "pass"}

    def genesis_execute(private_state, **kwargs):  # noqa: ANN001
        calls.append(("genesis-proof-guardian", kwargs["node"]))
        return {"status": "pass"}

    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.execute_admission_voter_cleanup", admission_execute)
    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.execute_activation_guardian_cleanup", activation_execute)
    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.execute_genesis_proof_guardian_cleanup", genesis_execute)

    def fake_run(command, **kwargs):  # noqa: ANN001
        if command[:4] == ["docker", "ps", "-q", "--filter"]:
            if "mainnetc-super1-lacn9nce720smwusl4wyqhyt" in command[4]:
                return subprocess.CompletedProcess(command, 0, stdout="container-c1\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:2] == ["docker", "run"]:
            calls.append(("cleanup1", None))
            payload = {
                "kind": "cleanup1.qbft_pending_vote_cleanup.v1",
                "node": "mainnetc-super1",
                "status": "pass",
                "summary": {"cleared_count": 0},
                "cleanup": {"status": "not_needed", "pending_votes_before": {}, "pending_votes_after": {}},
            }
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload) + "\n", stderr="")
        raise AssertionError(f"unexpected subprocess command: {command!r}")

    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.subprocess.run", fake_run)

    result = run_post_work_cleanup_v2(
        object(),
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology,
        acknowledged_topology_evidence_sha256=topology_sha,
        mode="execute",
        cleanup_all=True,
    )

    assert result["status"] == "pass"
    assert result["step_order"][0] == "cleanup1"
    assert calls == [
        ("cleanup1", None),
        ("admission-voter", "mainnetc-super1"),
        ("activation-guardian", "mainnetc-super1"),
        ("activation-guardian", "mainneta-super1"),
        ("genesis-proof-guardian", "mainnetc-super1"),
        ("genesis-proof-guardian", "mainneta-super1"),
    ]
    assert result["summary"]["cleanup_all"] is True
    assert result["summary"]["cleanup1_performed"] is True
    assert result["summary"]["cleanup1_found_count"] == 1
    assert result["summary"]["cleanup1_skipped_count"] == 1
    assert result["summary"]["service_cleanup_started"] is True
    assert result["summary"]["docker_touched"] is True
    assert result["summary"]["besu_restarted"] is False


def test_cleanup_all_fails_fast_when_no_local_besu_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, topology, topology_sha = _evidence_tree(tmp_path)

    def service_cleanup(private_state, **kwargs):  # noqa: ANN001
        raise AssertionError("service cleanup should not run when cleanup1 finds no local Besu container")

    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.execute_admission_voter_cleanup", service_cleanup)
    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.execute_activation_guardian_cleanup", service_cleanup)
    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.execute_genesis_proof_guardian_cleanup", service_cleanup)
    monkeypatch.setattr(
        "tools.mother_post_work_cleanup_v2.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )

    result = run_post_work_cleanup_v2(
        object(),
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology,
        acknowledged_topology_evidence_sha256=topology_sha,
        mode="execute",
        cleanup_all=True,
    )

    assert result["status"] == "failed"
    assert result["step_order"] == ["cleanup1"]
    assert result["summary"]["service_cleanup_started"] is False
    assert result["summary"]["cleanup1_found_count"] == 0
    assert result["steps"][0]["failed_before_service_cleanup"] is True


def test_rejects_unclean_topology_evidence(tmp_path: Path) -> None:
    runtime, topology, topology_sha = _evidence_tree(tmp_path, clean=False)

    with pytest.raises(MotherPostWorkCleanupV2Error) as exc:
        run_post_work_cleanup_v2(
            object(),
            runtime_state_root=runtime,
            network="mainnet",
            topology_evidence=topology,
            acknowledged_topology_evidence_sha256=topology_sha,
            mode="inspect",
            rpc_urls=["mainnetc-super1=http://127.0.0.1:8545"],
        )

    assert exc.value.code == "MOTHER_POST_WORK_CLEANUP_V2_TOPOLOGY_NOT_ACCEPTED"
