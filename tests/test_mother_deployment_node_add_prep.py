from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_node_add_prep import (
    MotherDeploymentNodeAddPrepError,
    build_node_add_prep_transaction,
    verify_node_add_prep_transaction,
    write_node_add_prep_transaction,
)
from tools.mother.common.deployment_node_add_do import (
    build_node_add_do_release,
    execute_node_add_do_release,
    verify_node_add_do_evidence,
    verify_node_add_do_release,
    write_node_add_do_release,
)
from tools.mother.common.deployment_node_add_identity import (
    build_node_add_identity_release,
    execute_node_add_identity_release,
    verify_node_add_identity_evidence,
    verify_node_add_identity_release,
    write_node_add_identity_release,
)
from tests.test_mother_deployment_executor import TOKEN_A, _Response, _install, _operation


A_NODE = "mainneta-super1"
C1_NODE = "mainnetc-super1"
C2_NODE = "mainnetc-super2"
A_VALIDATOR = "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"
C1_VALIDATOR = "0x9b809f05f8d68da17e697cd6ab040d4320494611"
C2_VALIDATOR = "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876"


def _binding_for_test(private_state) -> dict:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _write_remove_finalize_baseline(paths, private_state) -> tuple[Path, str]:
    evidence = {
        "authority": {
            "finalize_live_mutation_authorized": False,
            "network_access_performed": True,
            "service_deletion_previously_performed": True,
            "survivor_services_observed": True,
            "target_service_absence_proven": True,
            "validator_removal_vote_previously_performed": True,
        },
        "completed_at": "2026-08-11T20:29:15Z",
        "failure": None,
        "final_topology": {
            "nodes": [C1_NODE, C2_NODE],
            "removed_node": A_NODE,
            "removed_validator_address": A_VALIDATOR,
            "validator_count": 2,
            "validator_set": [C1_VALIDATOR, C2_VALIDATOR],
        },
        "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
        "live_mutation_performed": False,
        "mode": "soft",
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "next_phase": "remove-node-finalized-mainnet",
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "finalize_mutation_performed": False,
            "manual_ssh_required": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "public_http_endpoint_created": False,
            "routing_or_topology_published": False,
            "secrets_in_output": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
        },
        "pre_removal_topology": {
            "chain_id": 42424240,
            "genesis_sha256": "364df17daf2dfa428bd486e9c4e8b46c70317f65b23b55aaf78f749e15de6c92",
            "nodes": [A_NODE, C1_NODE, C2_NODE],
            "validator_count": 3,
            "validator_set": [A_VALIDATOR, C1_VALIDATOR, C2_VALIDATOR],
        },
        "public_endpoint_created": False,
        "routing_or_topology_published": False,
        "schema_version": 1,
        "service_already_absent": False,
        "service_deletion_performed": True,
        "source_do_evidence": {
            "completed_at": "2026-08-11T20:10:04Z",
            "locator": "evidence/deployment-node-remove-do/example.json",
            "sha256": "1" * 64,
        },
        "source_prep_transaction": {
            "locator": "actions/deployment-node-remove-prep-transactions/example.json",
            "sha256": "2" * 64,
        },
        "status": "pass",
        "summary": {
            "clean": True,
            "complete": True,
            "final_validator_count": 2,
            "final_validator_set": [C1_VALIDATOR, C2_VALIDATOR],
            "live_mutation_performed": False,
            "next_phase": "remove-node-finalized-mainnet",
            "pre_removal_validator_count": 3,
            "public_endpoint_created": False,
            "removed_validator_absent_from_final_set": True,
            "routing_or_topology_published": False,
            "service_deletion_performed": True,
            "survivor_nodes": [C1_NODE, C2_NODE],
            "survivor_nodes_observed": [C1_NODE, C2_NODE],
            "target_node": A_NODE,
            "target_service_absent": True,
            "target_validator_address": A_VALIDATOR,
            "validator_removal_vote_performed": True,
        },
        "survivor_service_observations": [
            {
                "controller_id": "coolify-c",
                "endpoint": "/api/v1/services/svcc1xxxx",
                "method": "GET",
                "node": C1_NODE,
                "node_observed": True,
                "observed_at": "2026-08-11T20:29:15Z",
                "service_status": "degraded:unhealthy",
                "service_uuid": "svcc1xxxx",
            },
            {
                "controller_id": "coolify-c",
                "endpoint": "/api/v1/services/svcc2xxxx",
                "method": "GET",
                "node": C2_NODE,
                "node_observed": True,
                "observed_at": "2026-08-11T20:29:15Z",
                "service_status": "running:unhealthy",
                "service_uuid": "svcc2xxxx",
            },
        ],
        "survivors": [
            {"controller_id": "coolify-c", "node": C1_NODE, "service_uuid": "svcc1xxxx", "validator_address": C1_VALIDATOR},
            {"controller_id": "coolify-c", "node": C2_NODE, "service_uuid": "svcc2xxxx", "validator_address": C2_VALIDATOR},
        ],
        "target": {
            "controller_id": "coolify-a",
            "node": A_NODE,
            "service_uuid": "svca1xxxx",
            "validator_address": A_VALIDATOR,
        },
        "target_service_observation": {
            "absent": True,
            "controller_id": "coolify-a",
            "endpoint": "/api/v1/services/svca1xxxx",
            "method": "GET",
            "node": A_NODE,
            "service_uuid": "svca1xxxx",
        },
        "validator_removal_vote": {
            "current_validator_set": [A_VALIDATOR, C1_VALIDATOR, C2_VALIDATOR],
            "desired_validator_set": [C1_VALIDATOR, C2_VALIDATOR],
            "method": "qbft_proposeValidatorVote",
            "params": [A_VALIDATOR, False],
            "voter_nodes": [C1_NODE, C2_NODE],
        },
    }
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "deployment-node-remove-finalize" / "20260811T202915Z-test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()



def _write_empty_topology_baseline(paths, private_state) -> tuple[Path, str]:
    evidence = {
        "completed_at": "2026-08-11T20:40:00Z",
        "failure": None,
        "kind": "main_computer.mother.topology_evidence.v1",
        "live_mutation_performed": False,
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "next_phase": "add-node-prep-mainnet",
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "public_http_endpoint_created": False,
            "routing_or_topology_published": False,
            "secrets_in_output": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
        },
        "public_endpoint_created": False,
        "routing_or_topology_published": False,
        "schema_version": 1,
        "status": "pass",
        "summary": {
            "clean": True,
            "complete": True,
            "live_mutation_performed": False,
            "next_phase": "add-node-prep-mainnet",
            "public_endpoint_created": False,
            "routing_or_topology_published": False,
        },
        "current_topology": {
            "chain_id": 42424240,
            "genesis_sha256": "364df17daf2dfa428bd486e9c4e8b46c70317f65b23b55aaf78f749e15de6c92",
            "nodes": [],
            "validator_count": 0,
            "validator_set": [],
        },
    }
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "topology" / "20260811T204000Z-empty.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def test_add_node_prep_derives_topology_diff_from_finalize_baseline(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_remove_finalize_baseline(paths, private_state)

    transaction = build_node_add_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=A_NODE,
        target_host="coolify-a",
        mode="reactivate",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-11T20:45:00Z",
        now=__import__("datetime").datetime(2026, 8, 11, 20, 45, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert transaction["target"]["node"] == A_NODE
    assert transaction["target"]["controller_id"] == "coolify-a"
    assert transaction["target"]["validator_address"] == A_VALIDATOR
    assert transaction["target"]["validator_address_source"] == "baseline-removed-target"
    assert transaction["target"]["previous_service_uuid"] == "svca1xxxx"
    assert transaction["current_topology"]["nodes"] == [C1_NODE, C2_NODE]
    assert transaction["post_add_topology"]["nodes"] == [A_NODE, C1_NODE, C2_NODE]
    assert transaction["post_add_topology"]["validator_set"] == [A_VALIDATOR, C1_VALIDATOR, C2_VALIDATOR]
    assert transaction["topology_diff"] == {
        "operation": "add-node",
        "added_nodes": [A_NODE],
        "removed_nodes": [],
        "unchanged_nodes": [C1_NODE, C2_NODE],
        "pre_validator_count": 2,
        "post_validator_count": 3,
    }
    assert transaction["execution_plan"]["generic_topology_diff"] is True
    assert transaction["execution_plan"]["hardcoded_stage_target"] is False
    assert transaction["policy"]["live_mutation_performed"] is False



def test_add_node_prep_starts_from_generic_empty_topology(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_empty_topology_baseline(paths, private_state)

    transaction = build_node_add_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=A_NODE,
        target_host="coolify-a",
        mode="initial",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-11T20:45:00Z",
        now=__import__("datetime").datetime(2026, 8, 11, 20, 45, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert transaction["target"]["node"] == A_NODE
    assert transaction["target"]["validator_address_source"] == "mother-private-state"
    assert transaction["current_topology"]["nodes"] == []
    assert transaction["current_topology"]["validator_set"] == []
    assert transaction["post_add_topology"]["nodes"] == [A_NODE]
    assert transaction["post_add_topology"]["validator_count"] == 1
    assert transaction["topology_diff"]["added_nodes"] == [A_NODE]
    assert transaction["execution_plan"]["generic_topology_diff"] is True
    assert transaction["execution_plan"]["hardcoded_stage_target"] is False


def test_add_node_prep_write_and_verify_is_local_only(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_remove_finalize_baseline(paths, private_state)
    transaction = build_node_add_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=A_NODE,
        target_host="coolify-a",
        mode="reactivate",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-11T20:45:00Z",
        now=__import__("datetime").datetime(2026, 8, 11, 20, 45, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    path, digest = write_node_add_prep_transaction(paths, transaction, operation=_operation("write-add-prep"))

    verified = verify_node_add_prep_transaction(
        paths,
        private_state,
        path,
        now=__import__("datetime").datetime(2026, 8, 11, 20, 46, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert verified["clean"] is True
    assert verified["node_add_prep_transaction_sha256"] == digest
    assert verified["target_node"] == A_NODE
    assert verified["target_host"] == "coolify-a"
    assert verified["current_nodes"] == [C1_NODE, C2_NODE]
    assert verified["post_add_nodes"] == [A_NODE, C1_NODE, C2_NODE]
    assert verified["generic_topology_diff"] is True
    assert verified["hardcoded_stage_target"] is False
    assert verified["live_mutation_performed"] is False
    assert verified["service_creation_performed"] is False
    assert verified["validator_admission_performed"] is False


def test_add_node_prep_rejects_target_already_in_baseline(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_remove_finalize_baseline(paths, private_state)

    with pytest.raises(MotherDeploymentNodeAddPrepError, match="already present"):
        build_node_add_prep_transaction(
            paths,
            private_state,
            baseline_path,
            network="mainnet",
            target_node=C1_NODE,
            target_host="coolify-c",
            mode="soft",
            baseline_evidence_sha256=baseline_sha,
            now=__import__("datetime").datetime(2026, 8, 11, 20, 45, 0, tzinfo=__import__("datetime").timezone.utc),
        )


def test_add_node_prep_cli_exposes_generic_surface() -> None:
    parser = mother_deploy._parser()
    args = parser.parse_args(
        [
            "add-node",
            "prep",
            "mainnet",
            "--node",
            A_NODE,
            "--host",
            "coolify-a",
            "--mode",
            "reactivate",
            "--baseline-evidence",
            "evidence/deployment-node-remove-finalize/example.json",
            "--baseline-evidence-sha256",
            "0" * 64,
        ]
    )
    assert args.command == "add-node"
    assert args.add_node_phase == "prep"
    assert args.node == A_NODE
    assert args.host == "coolify-a"

    verify_args = parser.parse_args(
        [
            "verify-add-node-prep-transaction",
            "--transaction",
            "actions/deployment-node-add-prep-transactions/example.json",
        ]
    )
    assert verify_args.command == "verify-add-node-prep-transaction"


def test_add_node_prep_verify_dispatch_reaches_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_load(args):  # noqa: ANN001
        return {"private": "state"}

    def fake_verify(args, private_state):  # noqa: ANN001
        calls.append((args.command, private_state))
        return 0

    monkeypatch.setattr(mother_deploy, "_load", fake_load)
    monkeypatch.setattr(mother_deploy, "_cmd_verify_node_add_prep_transaction", fake_verify)

    rc = mother_deploy.main(
        [
            "verify-add-node-prep-transaction",
            "--transaction",
            "actions/deployment-node-add-prep-transactions/example.json",
        ]
    )

    assert rc == 0
    assert calls == [("verify-add-node-prep-transaction", {"private": "state"})]


class _AddNodeDoOpener:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.services: dict[str, dict] = {}

    def open(self, request, timeout: float):  # noqa: ANN001
        from urllib.parse import urlsplit

        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": parsed.hostname, "path": path, "body": body})
        assert parsed.hostname == "coolify-a.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"

        if method == "GET" and path == "/api/v1/projects/project-a/environments":
            return _Response([{"uuid": "env-a", "name": "mainnet"}])

        if method == "GET" and path in {"/api/v1/services", "/api/v1/resources"}:
            return _Response(list(self.services.values()))

        if method == "POST" and path == "/api/v1/services":
            assert body["name"] == A_NODE
            assert body["environment_uuid"] == "env-a"
            assert body["server_uuid"] == "server-a"
            service = {"uuid": "svc-a1", "name": A_NODE, "status": "running:unhealthy"}
            self.services[service["uuid"]] = service
            return _Response({"uuid": service["uuid"]}, status=201)

        if method == "GET" and path == "/api/v1/services/svc-a1":
            return _Response(self.services["svc-a1"])

        raise AssertionError(f"unexpected request: {method} {path}")


def _write_verified_add_node_prep(tmp_path: Path):
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_remove_finalize_baseline(paths, private_state)
    transaction = build_node_add_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=A_NODE,
        target_host="coolify-a",
        mode="reactivate",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-11T21:03:46Z",
        now=None,
    )
    transaction_path, transaction_sha = write_node_add_prep_transaction(
        paths,
        transaction,
        operation=_operation("write-add-prep-for-do"),
    )
    return paths, private_state, transaction_path, transaction_sha


def test_add_node_do_release_is_generic_and_one_use(tmp_path: Path) -> None:
    paths, private_state, transaction_path, transaction_sha = _write_verified_add_node_prep(tmp_path)
    release = build_node_add_do_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_prep_transaction_sha256=transaction_sha,
        created_at="2026-08-11T21:05:00Z",
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
    )
    assert release["target"]["node"] == A_NODE
    assert release["summary"]["generic_topology_diff"] is True
    assert release["summary"]["hardcoded_stage_target"] is False
    assert release["authority"]["service_creation_authorized"] is True
    assert release["authority"]["validator_admission_authorized"] is False
    release_path, release_sha = write_node_add_do_release(
        paths,
        release,
        operation=_operation("write-add-do-release"),
    )

    verified = verify_node_add_do_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=900,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["release_already_claimed"] is False
    assert verified["node_add_do_release_sha256"] == release_sha
    assert verified["target_host"] == "coolify-a"
    assert verified["next_phase"] == "add-node-do-mainnet"


def test_add_node_do_executes_only_standby_service_creation(tmp_path: Path) -> None:
    paths, private_state, transaction_path, transaction_sha = _write_verified_add_node_prep(tmp_path)
    release = build_node_add_do_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_prep_transaction_sha256=transaction_sha,
        created_at="2026-08-11T21:05:00Z",
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
    )
    release_path, release_sha = write_node_add_do_release(
        paths,
        release,
        operation=_operation("write-add-do-release-exec"),
    )
    opener = _AddNodeDoOpener()
    result = execute_node_add_do_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        opener=opener,
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
        operation=_operation("execute-add-do-release"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["service_creation_performed"] is True
    assert result["summary"]["service_creation_proven"] is True
    assert result["summary"]["validator_admission_performed"] is False
    assert result["summary"]["generic_topology_diff"] is True
    assert result["summary"]["hardcoded_stage_target"] is False
    assert result["next_phase"] == "add-node-identity-mainnet"
    assert result["target"]["created_service_uuid"] == "svc-a1"
    assert [request["method"] for request in opener.requests].count("POST") == 1

    verified = verify_node_add_do_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=86400,
        release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["created_service_uuid"] == "svc-a1"
    assert verified["validator_admission_performed"] is False
    assert verified["next_phase"] == "add-node-identity-mainnet"


def test_add_node_do_cli_exposes_release_execute_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths, private_state, transaction_path, transaction_sha = _write_verified_add_node_prep(tmp_path)
    runtime_root = str(paths.root.parent)
    assert mother_deploy.main([
        "release-add-node-do",
        "--runtime-state-root",
        runtime_root,
        "--transaction",
        str(transaction_path),
        "--acknowledge-node-add-prep-transaction-sha256",
        transaction_sha,
        "--write-release",
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["generic_topology_diff"] is True
    assert out["release_artifact"]["sha256"] == out["node_add_do_release_sha256"]

    assert mother_deploy.main([
        "verify-add-node-do-release",
        "--runtime-state-root",
        runtime_root,
        "--release",
        out["release_artifact"]["path"],
        "--max-age-seconds",
        "86400",
    ]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True


class _AddNodeIdentityOpener:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.envs: list[dict] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        from urllib.parse import urlsplit

        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": parsed.hostname, "path": path, "body": body})
        assert parsed.hostname == "coolify-a.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"

        if path == "/api/v1/services/svc-a1/envs" and method == "GET":
            return _Response(list(self.envs))
        if path == "/api/v1/services/svc-a1/envs" and method == "POST":
            assert body["key"] in {"MC_MOTHER_VALIDATOR_PRIVATE_KEY", "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY"}
            assert isinstance(body["value"], str) and body["value"].startswith("0x") and len(body["value"]) == 66
            created = {"uuid": f"env-{len(self.envs)+1}", "key": body["key"], "value": body["value"]}
            self.envs.append(created)
            return _Response(created, status=201)

        raise AssertionError(f"unexpected request: {method} {path}")


def _write_verified_add_node_do_evidence(tmp_path: Path):
    paths, private_state, transaction_path, transaction_sha = _write_verified_add_node_prep(tmp_path)
    release = build_node_add_do_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_prep_transaction_sha256=transaction_sha,
        created_at="2026-08-11T21:05:00Z",
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
    )
    release_path, release_sha = write_node_add_do_release(
        paths,
        release,
        operation=_operation("write-add-do-release-for-identity"),
    )
    do_result = execute_node_add_do_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        opener=_AddNodeDoOpener(),
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
        operation=_operation("execute-add-do-for-identity"),
    )
    return paths, private_state, Path(do_result["evidence"]["path"]), do_result["evidence"]["sha256"]


def test_add_node_identity_release_authorizes_identity_only(tmp_path: Path) -> None:
    paths, private_state, add_do_evidence_path, add_do_evidence_sha = _write_verified_add_node_do_evidence(tmp_path)

    release = build_node_add_identity_release(
        paths,
        private_state,
        add_do_evidence_path,
        acknowledged_add_do_evidence_sha256=add_do_evidence_sha,
        created_at="2026-08-11T21:30:00Z",
        now=datetime(2026, 8, 11, 21, 30, 1, tzinfo=timezone.utc),
    )

    assert release["target"]["node"] == A_NODE
    assert release["target"]["created_service_uuid"] == "svc-a1"
    assert release["authority"]["identity_install_authorized"] is True
    assert release["authority"]["validator_admission_authorized"] is False
    assert release["summary"]["generic_topology_diff"] is True
    assert release["summary"]["hardcoded_stage_target"] is False
    assert release["identity_commitments"][0]["environment_key"] == "MC_MOTHER_VALIDATOR_PRIVATE_KEY"
    assert release["identity_commitments"][1]["environment_key"] == "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY"

    release_path, release_sha = write_node_add_identity_release(
        paths,
        release,
        operation=_operation("write-add-identity-release"),
    )
    verified = verify_node_add_identity_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=900,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 11, 21, 30, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["node_add_identity_release_sha256"] == release_sha
    assert verified["release_already_claimed"] is False
    assert verified["identity_install_authorized"] is True
    assert verified["validator_admission_authorized"] is False


def test_add_node_identity_executes_only_identity_install(tmp_path: Path) -> None:
    paths, private_state, add_do_evidence_path, add_do_evidence_sha = _write_verified_add_node_do_evidence(tmp_path)
    release = build_node_add_identity_release(
        paths,
        private_state,
        add_do_evidence_path,
        acknowledged_add_do_evidence_sha256=add_do_evidence_sha,
        created_at="2026-08-11T21:30:00Z",
        now=datetime(2026, 8, 11, 21, 30, 1, tzinfo=timezone.utc),
    )
    release_path, release_sha = write_node_add_identity_release(
        paths,
        release,
        operation=_operation("write-add-identity-release-exec"),
    )
    opener = _AddNodeIdentityOpener()
    result = execute_node_add_identity_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        opener=opener,
        now=datetime(2026, 8, 11, 21, 30, 1, tzinfo=timezone.utc),
        operation=_operation("execute-add-identity-release"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["identity_install_performed"] is True
    assert result["summary"]["identity_install_proven"] is True
    assert result["summary"]["replica_sync_performed"] is False
    assert result["summary"]["validator_admission_performed"] is False
    assert result["summary"]["generic_topology_diff"] is True
    assert result["summary"]["hardcoded_stage_target"] is False
    assert result["next_phase"] == "add-node-replica-sync-mainnet"
    assert [request["method"] for request in opener.requests].count("POST") == 2
    assert {env["key"] for env in opener.envs} == {
        "MC_MOTHER_VALIDATOR_PRIVATE_KEY",
        "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY",
    }

    verified = verify_node_add_identity_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=86400,
        release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 11, 21, 30, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["identity_install_performed"] is True
    assert verified["validator_admission_performed"] is False
    assert verified["next_phase"] == "add-node-replica-sync-mainnet"


def test_add_node_identity_cli_exposes_release_execute_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths, private_state, add_do_evidence_path, add_do_evidence_sha = _write_verified_add_node_do_evidence(tmp_path)
    runtime_root = str(paths.root.parent)

    assert mother_deploy.main([
        "release-add-node-identity",
        "--runtime-state-root",
        runtime_root,
        "--add-do-evidence",
        str(add_do_evidence_path),
        "--acknowledge-add-node-do-evidence-sha256",
        add_do_evidence_sha,
        "--write-release",
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["generic_topology_diff"] is True
    assert out["release_artifact"]["sha256"] == out["node_add_identity_release_sha256"]

    assert mother_deploy.main([
        "verify-add-node-identity-release",
        "--runtime-state-root",
        runtime_root,
        "--release",
        out["release_artifact"]["path"],
        "--max-age-seconds",
        "86400",
    ]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True

    parser = mother_deploy._parser()
    args = parser.parse_args([
        "add-node",
        "identity",
        "mainnet",
        "--release",
        out["release_artifact"]["path"],
        "--acknowledge-release-sha256",
        out["node_add_identity_release_sha256"],
        "--execute",
    ])
    assert args.command == "add-node"
    assert args.add_node_phase == "identity"

    verify_args = parser.parse_args([
        "verify-add-node-identity-evidence",
        "--evidence",
        "evidence/deployment-node-add-identity/example.json",
    ])
    assert verify_args.command == "verify-add-node-identity-evidence"
