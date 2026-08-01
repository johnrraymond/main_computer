from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_soft_replica_executor import execute_released_soft_replica
from tools.mother.common.deployment_soft_replica_sync import (
    MotherDeploymentSoftReplicaSyncError,
    build_soft_replica_sync_release,
    execute_soft_replica_sync_release,
    inspect_soft_replica_sync_release,
    verify_soft_replica_sync_evidence,
    verify_soft_replica_sync_release,
    write_soft_replica_sync_release,
)
from tests.test_mother_deployment_executor import TOKEN_A, TOKEN_C, _operation
from tests.test_mother_deployment_soft_replica_executor import _ReplicaOpener, _fixture as _replica_fixture


class _Response:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self._body = json.dumps(payload).encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def close(self) -> None:
        return None


def _fixture(tmp_path: Path):
    paths, private_state, _, _, _, replica_release, replica_release_path, replica_release_digest = _replica_fixture(tmp_path)
    replica_opener = _ReplicaOpener(private_state, replica_release)
    replica_execution = execute_released_soft_replica(
        paths,
        private_state,
        replica_release_path,
        acknowledged_release_sha256=replica_release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=replica_opener,
        operation=_operation("sync-fixture-replica-execution"),
    )
    execution_path = Path(replica_execution["result_artifact"]["path"])
    execution_digest = replica_execution["result_artifact"]["sha256"]
    release = build_soft_replica_sync_release(
        paths,
        private_state,
        execution_path,
        acknowledged_soft_replica_execution_sha256=execution_digest,
        selected_nodes=("mainnetc-super1",),
    )
    release_path, release_digest = write_soft_replica_sync_release(
        paths,
        release,
        operation=_operation("sync-release-fixture"),
    )
    return paths, private_state, execution_path, execution_digest, release, release_path, release_digest


class _SyncOpener:
    def __init__(
        self,
        release: dict[str, Any],
        *,
        a_healthy: bool = True,
        statuses: list[str] | None = None,
        fail_patch: bool = False,
    ) -> None:
        self.release = release
        self.a_healthy = a_healthy
        self.statuses = list(statuses or ["starting:unhealthy", "running:healthy"])
        self.fail_patch = fail_patch
        self.requests: list[tuple[str, str, str]] = []
        self.patched = False

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host, method, path = parsed.hostname or "", request.get_method(), parsed.path
        self.requests.append((host, method, path))
        assert timeout > 0
        expected_token = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"
        initial = self.release["initial_chain_precondition"]
        plan = self.release["proof_plan"]
        if host == "coolify-a.invalid":
            assert method == "GET"
            if path == "/api/v1/services":
                return _Response([{
                    "uuid": initial["service_uuid"],
                    "name": initial["node"],
                    "status": "running:healthy" if self.a_healthy else "running:unhealthy",
                }])
            assert path == f"/api/v1/services/{initial['service_uuid']}"
            return _Response({
                "service": {
                    "uuid": initial["service_uuid"],
                    "name": initial["node"],
                    "docker_compose_raw": initial["proof_compose"]["canonical_text"],
                }
            })
        assert host == "coolify-c.invalid"
        if method == "GET" and path == f"/api/v1/services/{plan['service_uuid']}":
            compose = plan["proof_compose"]["canonical_text"] if self.patched else plan["original_compose"]["canonical_text"]
            return _Response({
                "service": {
                    "uuid": plan["service_uuid"],
                    "name": plan["replica_node"],
                    "docker_compose_raw": compose,
                }
            })
        if method == "PATCH" and path == f"/api/v1/services/{plan['service_uuid']}":
            if self.fail_patch:
                return _Response({"message": "rejected"}, status=500)
            body = json.loads(request.data.decode("utf-8"))
            compose = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
            assert compose == plan["proof_compose"]["canonical_text"]
            self.patched = True
            return _Response({"uuid": plan["service_uuid"]}, status=200)
        if method == "GET" and path == "/api/v1/deploy":
            assert self.patched
            return _Response({"deployment_uuid": "sync-proof-deploy"}, status=200)
        if method == "GET" and path == "/api/v1/services":
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return _Response([{
                "uuid": plan["service_uuid"],
                "name": plan["replica_node"],
                "status": status,
            }])
        raise AssertionError(f"unexpected request {method} {request.full_url}")


def test_sync_release_is_internal_c_only_and_vote_blocked(tmp_path: Path) -> None:
    paths, private_state, _, execution_digest, release, release_path, release_digest = _fixture(tmp_path)
    assert release["soft_replica_execution"]["sha256"] == execution_digest
    assert release["proof_plan"]["replica_node"] == "mainnetc-super1"
    assert release["proof_plan"]["controller_id"] == "coolify-c"
    assert release["authority"]["synchronization_proof_authorized"] is True
    assert release["authority"]["validator_vote_authorized"] is False
    assert release["initial_chain_precondition"]["read_only"] is True
    assert [item["controller_id"] for item in release["proof_plan"]["mutations"]] == ["coolify-c", "coolify-c"]
    compose = release["proof_plan"]["proof_compose"]["canonical_text"]
    guardian = compose.split("  mother-replica-sync-guardian:", 1)[1].split("\nvolumes:\n", 1)[0]
    assert "ports:" not in guardian
    assert "expose:" not in guardian
    assert "traefik." not in guardian
    assert "8545:8545" not in compose
    assert "eth_syncing" in compose
    assert "admin_peers" in compose
    assert "admin_nodeInfo" in compose
    verified = verify_soft_replica_sync_release(
        paths, private_state, release_path, selected_nodes=("mainnetc-super1",)
    )
    assert verified["clean"] is True
    assert verified["soft_replica_sync_release_sha256"] == release_digest
    assert verified["manual_ssh_required"] is False
    assert verified["public_http_endpoint_created"] is False
    assert verified["validator_vote_authorized"] is False


def test_sync_release_rejects_wrong_execution_digest(tmp_path: Path) -> None:
    paths, private_state, execution_path, _, _, _, _ = _fixture(tmp_path)
    with pytest.raises(MotherDeploymentSoftReplicaSyncError) as caught:
        build_soft_replica_sync_release(
            paths,
            private_state,
            execution_path,
            acknowledged_soft_replica_execution_sha256="0" * 64,
            selected_nodes=("mainnetc-super1",),
        )
    assert caught.value.code == "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_ACKNOWLEDGEMENT_MISMATCH"


def test_sync_inspection_is_network_free(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release_path, release_digest = _fixture(tmp_path)
    result = inspect_soft_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
    )
    assert result["clean"] is True
    assert result["network_access_performed"] is False
    assert result["initial_node_read_only"] is True
    assert result["guardian_internal_only"] is True
    assert result["validator_vote_authorized"] is False


def test_sync_executor_rechecks_a_and_proves_c_without_public_endpoint(tmp_path: Path) -> None:
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _SyncOpener(release)
    result = execute_soft_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("sync-live"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["initial_chain_reverified"] is True
    assert result["summary"]["replica_synchronized"] is True
    assert result["summary"]["sync_complete"] is True
    assert result["summary"]["blocks_advancing"] is True
    assert result["summary"]["validator_vote_authorized"] is False
    assert result["summary"]["next_phase"] == "stage-validator-admission-transaction"
    assert result["proof"]["guardian_internal_only"] is True
    assert result["proof"]["public_endpoint_created"] is False
    assert all(method == "GET" for host, method, _ in opener.requests if host == "coolify-a.invalid")
    writes = [(host, method) for host, method, _ in opener.requests if method in {"PATCH", "POST", "PUT", "DELETE"}]
    assert writes == [("coolify-c.invalid", "PATCH")]
    verified = verify_soft_replica_sync_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        selected_nodes=("mainnetc-super1",),
    )
    assert verified["replica_synchronized"] is True
    assert verified["validator_vote_authorized"] is False


def test_sync_executor_fails_before_c_mutation_when_a_unhealthy_and_consumes_release(tmp_path: Path) -> None:
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _SyncOpener(release, a_healthy=False)
    result = execute_soft_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("sync-unhealthy-a"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INITIAL_CHAIN_UNHEALTHY"
    assert result["summary"]["live_mutation_performed"] is False
    assert all(host != "coolify-c.invalid" for host, _, _ in opener.requests)
    with pytest.raises(MotherDeploymentSoftReplicaSyncError) as caught:
        execute_soft_replica_sync_release(
            paths,
            private_state,
            release_path,
            acknowledged_release_sha256=release_digest,
            selected_nodes=("mainnetc-super1",),
            opener=opener,
            operation=_operation("sync-replay"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_RELEASE_ALREADY_CONSUMED"


def test_sync_executor_persists_failed_health_evidence_without_authorizing_vote(tmp_path: Path) -> None:
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _SyncOpener(release, statuses=["running:unhealthy"])
    result = execute_soft_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("sync-health-failure"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_NOT_HEALTHY"
    assert result["summary"]["live_mutation_performed"] is True
    assert result["summary"]["replica_synchronized"] is False
    assert result["summary"]["validator_vote_authorized"] is False
    artifact = json.loads(Path(result["evidence"]["path"]).read_text(encoding="utf-8"))
    assert artifact["authority"]["validator_vote_authorized"] is False
    assert artifact["policy"]["public_endpoint_created"] is False


def test_cli_releases_inspects_and_verifies_sync(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths, _, execution_path, execution_digest, _, _, _ = _fixture(tmp_path)
    runtime_root = paths.root.parent
    code = mother_deploy.main([
        "release-soft-replica-sync",
        "--runtime-state-root", str(runtime_root),
        "--execution", str(execution_path),
        "--acknowledge-soft-replica-execution-sha256", execution_digest,
        "--node", "mainnetc-super1",
        "--write-release",
    ])
    assert code == 0
    staged = json.loads(capsys.readouterr().out)
    release_path = staged["release_artifact"]["path"]
    release_digest = staged["release_artifact"]["sha256"]
    code = mother_deploy.main([
        "verify-soft-replica-sync-release",
        "--runtime-state-root", str(runtime_root),
        "--release", release_path,
        "--node", "mainnetc-super1",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["clean"] is True
    code = mother_deploy.main([
        "apply-soft-replica-sync",
        "--runtime-state-root", str(runtime_root),
        "--release", release_path,
        "--acknowledge-release-sha256", release_digest,
        "--node", "mainnetc-super1",
    ])
    assert code == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["execute_requested"] is False
    assert inspected["manual_ssh_required"] is False
    assert inspected["public_http_endpoint_created"] is False
