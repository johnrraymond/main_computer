from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from tools import mother_deploy
from tools.mother.common.deployment_node_add_do import (
    build_node_add_do_release,
    execute_node_add_do_release,
    write_node_add_do_release,
)
from tools.mother.common.deployment_node_add_identity import (
    build_node_add_identity_release,
    execute_node_add_identity_release,
    write_node_add_identity_release,
)
from tools.mother.common.deployment_node_add_prep import (
    build_node_add_prep_transaction,
    write_node_add_prep_transaction,
)
from tools.mother.common.deployment_node_add_single_node_bootstrap import (
    build_node_add_single_node_bootstrap_release,
    execute_node_add_single_node_bootstrap_release,
    verify_node_add_single_node_bootstrap_evidence,
    verify_node_add_single_node_bootstrap_release,
    write_node_add_single_node_bootstrap_release,
)
from tests.test_mother_deployment_executor import _Response, _install, _operation, TOKEN_A
from tests.test_mother_deployment_node_add_prep import (
    A_NODE,
    A_VALIDATOR,
    _AddNodeDoOpener,
    _AddNodeIdentityOpener,
    _write_remove_finalize_baseline,
)


def _write_empty_topology_identity_evidence(tmp_path: Path):
    _unused, paths, private_state = _install(tmp_path)
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
        created_at="2026-08-12T00:51:53Z",
        now=datetime(2026, 8, 12, 0, 51, 53, tzinfo=timezone.utc),
    )
    assert transaction["current_topology"]["nodes"] == []
    transaction_path, transaction_sha = write_node_add_prep_transaction(
        paths,
        transaction,
        operation=_operation("write-empty-add-prep"),
    )

    do_release = build_node_add_do_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_prep_transaction_sha256=transaction_sha,
        created_at="2026-08-12T00:54:29Z",
        now=datetime(2026, 8, 12, 0, 54, 30, tzinfo=timezone.utc),
    )
    do_release_path, do_release_sha = write_node_add_do_release(
        paths,
        do_release,
        operation=_operation("write-empty-add-do-release"),
    )
    do_result = execute_node_add_do_release(
        paths,
        private_state,
        do_release_path,
        acknowledged_release_sha256=do_release_sha,
        max_age_seconds=900,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        opener=_AddNodeDoOpener(),
        now=datetime(2026, 8, 12, 0, 56, 29, tzinfo=timezone.utc),
        operation=_operation("execute-empty-add-do-release"),
    )

    identity_release = build_node_add_identity_release(
        paths,
        private_state,
        Path(do_result["evidence"]["path"]),
        acknowledged_add_do_evidence_sha256=do_result["evidence"]["sha256"],
        created_at="2026-08-12T01:01:03Z",
        expires_in_seconds=900,
        now=datetime(2026, 8, 12, 1, 1, 4, tzinfo=timezone.utc),
    )
    identity_release_path, identity_release_sha = write_node_add_identity_release(
        paths,
        identity_release,
        operation=_operation("write-empty-identity-release"),
    )
    identity_result = execute_node_add_identity_release(
        paths,
        private_state,
        identity_release_path,
        acknowledged_release_sha256=identity_release_sha,
        max_age_seconds=900,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        opener=_AddNodeIdentityOpener(),
        now=datetime(2026, 8, 12, 1, 2, 47, tzinfo=timezone.utc),
        operation=_operation("execute-empty-identity-release"),
    )
    assert identity_result["summary"]["single_node_bootstrap_required"] is True
    assert identity_result["summary"]["replica_sync_required"] is False
    assert identity_result["next_phase"] == "add-node-single-node-bootstrap-mainnet"
    return paths, private_state, Path(identity_result["evidence"]["path"]), identity_result["evidence"]["sha256"]


class _SingleNodeBootstrapOpener:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.envs = [
            {"uuid": "env-validator", "key": "MC_MOTHER_VALIDATOR_PRIVATE_KEY", "value": "0x" + "1" * 64},
            {"uuid": "env-hub", "key": "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY", "value": "0x" + "2" * 64},
        ]
        self.service = {
            "uuid": "svc-a1",
            "name": A_NODE,
            "status": "exited",
            "docker_compose_raw": "name: mainneta-super1\nservices:\n  mainneta-super1:\n    image: alpine:3.20\n",
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        from urllib.parse import urlsplit

        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": parsed.hostname, "path": path, "query": parsed.query, "body": body})
        assert parsed.hostname == "coolify-a.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"

        if method == "GET" and path == "/api/v1/services/svc-a1":
            return _Response(dict(self.service))
        if method == "GET" and path == "/api/v1/services/svc-a1/envs":
            return _Response(list(self.envs))
        if method == "GET" and path == "/api/v1/services/svc-a1/stop":
            self.service["status"] = "exited"
            return _Response({"message": "stopped"})
        if method == "PATCH" and path == "/api/v1/services/svc-a1":
            decoded = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
            assert body["name"] == A_NODE
            assert "mother-super-node-hub:" in decoded
            assert "mother-genesis-proof-guardian:" in decoded
            assert "qbft_proposeValidatorVote" not in decoded
            assert "mainnetc-super1" not in decoded
            assert "mainnetc-super2" not in decoded
            assert "8545:8545" not in decoded
            assert A_VALIDATOR in decoded
            self.service["docker_compose_raw"] = decoded
            self.service["status"] = "running:unhealthy"
            return _Response({"uuid": "svc-a1", "status": self.service["status"]})
        if method == "GET" and path == "/api/v1/deploy":
            assert parsed.query == "uuid=svc-a1&force=true"
            self.service["status"] = "running:healthy"
            return _Response({"message": "deploy queued"})
        if method == "GET" and path == "/api/v1/services":
            return _Response([dict(self.service)])
        raise AssertionError(f"unexpected request: {method} {path}")


def test_single_node_bootstrap_release_is_not_replica_sync_or_admission(tmp_path: Path) -> None:
    paths, private_state, identity_evidence_path, identity_evidence_sha = _write_empty_topology_identity_evidence(tmp_path)

    release = build_node_add_single_node_bootstrap_release(
        paths,
        private_state,
        identity_evidence_path,
        acknowledged_add_node_identity_evidence_sha256=identity_evidence_sha,
        created_at="2026-08-12T01:10:00Z",
        now=datetime(2026, 8, 12, 1, 10, 1, tzinfo=timezone.utc),
    )

    assert release["summary"]["serves_chain"] is True
    assert release["summary"]["serves_hub"] is True
    assert release["summary"]["old_baseline_topology_used_as_live"] is False
    assert release["summary"]["coolify_c_required"] is False
    assert release["summary"]["replica_sync_required"] is False
    assert release["summary"]["validator_admission_required"] is False
    assert release["bootstrap_plan"]["compose"]["hub_service_present"] is True
    assert release["bootstrap_plan"]["compose"]["guardian_service_present"] is True
    assert release["bootstrap_plan"]["compose"]["host_rpc_mapping_present"] is False
    assert "mainnetc-super1" not in release["bootstrap_plan"]["compose"]["canonical_text"]
    assert "mainnetc-super2" not in release["bootstrap_plan"]["compose"]["canonical_text"]
    assert "qbft_proposeValidatorVote" not in release["bootstrap_plan"]["compose"]["canonical_text"]

    release_path, release_sha = write_node_add_single_node_bootstrap_release(
        paths,
        release,
        operation=_operation("write-single-node-bootstrap-release"),
    )
    verified = verify_node_add_single_node_bootstrap_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=900,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 12, 1, 10, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["node_add_single_node_bootstrap_release_sha256"] == release_sha
    assert verified["serves_chain"] is True
    assert verified["serves_hub"] is True
    assert verified["replica_sync_required"] is False
    assert verified["validator_admission_required"] is False


def test_single_node_bootstrap_executes_chain_and_hub_proof_path(tmp_path: Path) -> None:
    paths, private_state, identity_evidence_path, identity_evidence_sha = _write_empty_topology_identity_evidence(tmp_path)
    release = build_node_add_single_node_bootstrap_release(
        paths,
        private_state,
        identity_evidence_path,
        acknowledged_add_node_identity_evidence_sha256=identity_evidence_sha,
        created_at="2026-08-12T01:10:00Z",
        now=datetime(2026, 8, 12, 1, 10, 1, tzinfo=timezone.utc),
    )
    release_path, release_sha = write_node_add_single_node_bootstrap_release(
        paths,
        release,
        operation=_operation("write-single-node-bootstrap-release-exec"),
    )
    opener = _SingleNodeBootstrapOpener()
    result = execute_node_add_single_node_bootstrap_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        max_wait_seconds=0.0,
        poll_interval_seconds=0.0,
        opener=opener,
        now=datetime(2026, 8, 12, 1, 11, 0, tzinfo=timezone.utc),
        operation=_operation("execute-single-node-bootstrap-release"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["single_node_bootstrap_performed"] is True
    assert result["summary"]["single_node_bootstrap_proven"] is True
    assert result["summary"]["serves_chain"] is True
    assert result["summary"]["serves_hub"] is True
    assert result["summary"]["replica_sync_performed"] is False
    assert result["summary"]["validator_admission_performed"] is False
    assert result["summary"]["old_baseline_topology_used_as_live"] is False
    assert result["summary"]["coolify_c_required"] is False
    assert [request["method"] for request in opener.requests].count("PATCH") == 1
    assert not any(
        request["host"] == "coolify-c.invalid"
        for request in opener.requests
    )

    verified = verify_node_add_single_node_bootstrap_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=86400,
        release_max_age_seconds=86400,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 12, 1, 11, 0, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["serves_chain"] is True
    assert verified["serves_hub"] is True
    assert verified["replica_sync_performed"] is False
    assert verified["validator_admission_performed"] is False


def test_single_node_bootstrap_cli_exposes_release_execute_and_verify() -> None:
    parser = mother_deploy._parser()

    release_args = parser.parse_args([
        "release-add-node-single-node-bootstrap",
        "--identity-evidence",
        "evidence/deployment-node-add-identity/example.json",
        "--acknowledge-add-node-identity-evidence-sha256",
        "a" * 64,
    ])
    assert release_args.command == "release-add-node-single-node-bootstrap"

    execute_args = parser.parse_args([
        "add-node",
        "single-node-bootstrap",
        "mainnet",
        "--release",
        "actions/deployment-node-add-single-node-bootstrap-releases/example.json",
        "--acknowledge-release-sha256",
        "b" * 64,
        "--execute",
    ])
    assert execute_args.command == "add-node"
    assert execute_args.add_node_phase == "single-node-bootstrap"

    verify_args = parser.parse_args([
        "verify-add-node-single-node-bootstrap-evidence",
        "--evidence",
        "evidence/deployment-node-add-single-node-bootstrap/example.json",
    ])
    assert verify_args.command == "verify-add-node-single-node-bootstrap-evidence"
