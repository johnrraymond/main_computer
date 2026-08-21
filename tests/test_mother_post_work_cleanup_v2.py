from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import tools.mother_post_work_cleanup_v2 as post_work_cleanup_v2
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
    ]
    assert result["summary"]["service_cleanup_order"] == []
    assert result["summary"]["helper_cleanup_removed_from_v2"] is True
    assert result["summary"]["helper_cleanup_replacement"] == "tools/mother_helper_cleanup2_yagni.py"
    assert result["summary"]["chain_rpc_preflight_order"] == "first"
    assert result["summary"]["chain_cleanup_order"] == "chain-cleanup-only"
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

    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.run_chain_cleanup", chain_run)

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



def test_cleanup1_exited_temporary_service_counts_as_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        post_work_cleanup_v2,
        "resolve_coolify_controller",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        post_work_cleanup_v2,
        "_controller_config",
        lambda *args, **kwargs: {"project_uuid": "project-uuid"},
    )
    monkeypatch.setattr(
        post_work_cleanup_v2,
        "_resolve_environment_uuid",
        lambda **kwargs: "environment-uuid",
    )
    monkeypatch.setattr(
        post_work_cleanup_v2,
        "_cleanup1_compose",
        lambda **kwargs: "services:\n  cleanup1: {}\n",
    )
    monkeypatch.setattr(
        post_work_cleanup_v2,
        "_temporary_service_body",
        lambda controller_config, service_name, compose: {
            "name": service_name,
            "docker_compose_raw": compose,
        },
    )
    monkeypatch.setattr(
        post_work_cleanup_v2,
        "_application_uuid",
        lambda payload: "cleanup-service-uuid",
    )

    def http(_controller, method, endpoint, **_kwargs):  # noqa: ANN001
        if method == "POST" and endpoint == "/api/v1/services":
            return {
                "status": 201,
                "ok": True,
                "payload": {"uuid": "cleanup-service-uuid"},
                "response_sha256": "create-sha",
                "byte_length": 48,
                "elapsed_ms": 1,
            }
        if method == "POST" and endpoint.endswith("/start"):
            return {
                "status": 200,
                "ok": True,
                "payload": {},
                "response_sha256": "start-sha",
                "byte_length": 46,
                "elapsed_ms": 1,
            }
        if method == "DELETE":
            return {
                "status": 200,
                "ok": True,
                "payload": {},
                "response_sha256": "delete-sha",
                "byte_length": 46,
                "elapsed_ms": 1,
            }
        raise AssertionError(f"unexpected request {method} {endpoint}")

    monkeypatch.setattr(post_work_cleanup_v2, "_http", http)
    monkeypatch.setattr(
        post_work_cleanup_v2,
        "_wait_for_temporary_service_health",
        lambda **kwargs: {
            "healthy": False,
            "service_status": "exited",
            "final_status": "exited",
            "reason": "health-timeout",
        },
    )

    result = post_work_cleanup_v2._run_cleanup1_coolify(
        object(),
        network="mainnet",
        service={
            "node": "mainnetc-super1",
            "controller_id": "coolify-c",
            "service_uuid": "lacn9nce720smwusl4wyqhyt",
        },
        final_validator_set=[
            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        ],
        chain_id=42424240,
        mode="execute",
        timeout=1.0,
        max_response_bytes=1024,
        max_wait_seconds=40.0,
        poll_interval_seconds=1.0,
        opener=None,
        progress=None,
    )

    assert result["status"] == "pass"
    assert result["reason"] is None
    assert result["health"]["reason"] == "temporary-service-exited"
    assert result["health"]["service_status"] == "exited"

def test_cleanup_all_runs_cleanup1_through_coolify_and_stops_before_helper_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, topology, topology_sha = _evidence_tree(tmp_path)
    calls: list[tuple[str, str | None]] = []

    def cleanup1(private_state, **kwargs):  # noqa: ANN001
        calls.append(("cleanup1", kwargs["service"]["node"]))
        assert kwargs["mode"] == "execute"
        assert kwargs["final_validator_set"] == [
            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        ]
        assert kwargs["chain_id"] == 42424240
        return {
            "step": "cleanup1",
            "node": kwargs["service"]["node"],
            "status": "pass",
            "coolify_touched": True,
            "docker_touched": False,
            "parent_redeploy_performed": False,
            "besu_restarted": False,
        }

    def chain_run(args):  # noqa: ANN001
        raise AssertionError("cleanup_all should not use Windows/direct RPC chain cleanup")

    monkeypatch.setattr("tools.mother_post_work_cleanup_v2._run_cleanup1_coolify", cleanup1)
    monkeypatch.setattr("tools.mother_post_work_cleanup_v2.run_chain_cleanup", chain_run)

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
    assert calls == [
        ("cleanup1", "mainnetc-super1"),
        ("cleanup1", "mainneta-super1"),
    ]
    assert result["summary"]["cleanup_all"] is True
    assert result["summary"]["cleanup1_execution"] == "temporary-coolify-service"
    assert result["summary"]["chain_rpc_preflight_order"] == "not_used_with_cleanup1"
    assert result["summary"]["chain_cleanup_order"] == "cleanup1-only"
    assert result["summary"]["service_cleanup_started"] is False


def test_cleanup_all_cleanup1_failure_stops_before_service_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, topology, topology_sha = _evidence_tree(tmp_path)
    calls: list[str] = []

    def cleanup1(private_state, **kwargs):  # noqa: ANN001
        calls.append(kwargs["service"]["node"])
        return {
            "step": "cleanup1",
            "node": kwargs["service"]["node"],
            "status": "failed",
            "reason": "cleanup1 refused unsafe pending vote",
        }

    monkeypatch.setattr("tools.mother_post_work_cleanup_v2._run_cleanup1_coolify", cleanup1)

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
    assert calls == ["mainnetc-super1"]
    assert result["summary"]["service_cleanup_started"] is False
    assert result["steps"][0]["step"] == "cleanup1"
    assert result["steps"][0]["failed_before_service_cleanup"] is True


def test_cleanup_all_rejects_rpc_url_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, topology, topology_sha = _evidence_tree(tmp_path)


    result = run_post_work_cleanup_v2(
        object(),
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology,
        acknowledged_topology_evidence_sha256=topology_sha,
        mode="execute",
        rpc_urls=["mainnetc-super1=http://127.0.0.1:8545"],
        cleanup_all=True,
    )

    assert result["status"] == "failed"
    assert result["steps"][0]["step"] == "cleanup1"
    assert "--rpc-url is not used with --cleanup-all" in result["steps"][0]["reason"]
    assert result["summary"]["service_cleanup_started"] is False
