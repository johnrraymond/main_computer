from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

from tools import mother_deploy
import tools.mother.common.deployment_genesis_birth as birth_module
from tools.mother.common.deployment_genesis_birth import (
    MotherDeploymentGenesisBirthError,
    build_genesis_birth_release,
    execute_genesis_birth_release,
    inspect_genesis_birth_release,
    verify_genesis_birth_evidence,
    verify_genesis_birth_release,
    write_genesis_birth_release,
)
from tools.mother.common.deployment_genesis_executor import execute_released_genesis
from tools.mother.common.deployment_genesis_release import (
    build_deployment_genesis_release,
    write_deployment_genesis_release,
)
from tools.mother.common.deployment_genesis_rollback import (
    execute_genesis_mutation_rollback,
    verify_genesis_mutation_rollback,
    write_genesis_mutation_rollback_verification,
)
from tests.test_mother_deployment_executor import TOKEN_A, _operation
from tests.test_mother_deployment_genesis_executor import (
    HUB_GIT_COMMIT_SHA,
    HUB_GIT_REPOSITORY,
    _GenesisOpener,
    _genesis_release,
)


def _successful_execution(tmp_path: Path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, _, release_path, release_digest, release = _genesis_release(tmp_path, now=now)
    result = execute_released_genesis(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1",),
        opener=_GenesisOpener(),
        operation=_operation("birth-fixture-genesis"),
    )
    return paths, private_state, Path(result["result_artifact"]["path"]), result, release


def _successful_reapplication(tmp_path: Path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    (
        paths,
        private_state,
        transaction_path,
        transaction_digest,
        _,
        first_release_path,
        first_release_digest,
        _,
    ) = _genesis_release(tmp_path, now=now)
    live = _GenesisOpener()
    first = execute_released_genesis(
        paths,
        private_state,
        first_release_path,
        acknowledged_release_sha256=first_release_digest,
        selected_nodes=("mainneta-super1",),
        opener=live,
        operation=_operation("birth-cycle-first-genesis"),
    )
    rolled_back = execute_genesis_mutation_rollback(
        paths,
        private_state,
        Path(first["result_artifact"]["path"]),
        acknowledged_execution_sha256=first["result_artifact"]["sha256"],
        opener=live,
        max_wait_seconds=1.0,
        poll_interval_seconds=0.0,
        operation=_operation("birth-cycle-genesis-rollback"),
    )
    verification = verify_genesis_mutation_rollback(
        paths,
        private_state,
        Path(rolled_back["result_artifact"]["path"]),
        opener=live,
    )
    evidence_path, _ = write_genesis_mutation_rollback_verification(
        paths,
        verification,
        operation=_operation("birth-cycle-genesis-rollback-evidence"),
    )

    second_now = now + timedelta(seconds=1)
    second_release = build_deployment_genesis_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_genesis_transaction_sha256=transaction_digest,
        hub_git_repository=HUB_GIT_REPOSITORY,
        hub_git_commit_sha=HUB_GIT_COMMIT_SHA,
        selected_nodes=("mainneta-super1",),
        created_at=second_now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        now=second_now,
    )
    second_release_path, second_release_digest = write_deployment_genesis_release(
        paths,
        second_release,
        operation=_operation("birth-cycle-second-genesis-release"),
    )
    second = execute_released_genesis(
        paths,
        private_state,
        second_release_path,
        acknowledged_release_sha256=second_release_digest,
        selected_nodes=("mainneta-super1",),
        opener=live,
        operation=_operation("birth-cycle-second-genesis"),
    )
    return (
        paths,
        private_state,
        Path(second["result_artifact"]["path"]),
        second,
        second_release,
        evidence_path,
    )


def _birth_release(
    tmp_path: Path,
    *,
    superseded_service_uuid: str | None = None,
):
    (
        paths,
        private_state,
        execution_path,
        execution,
        genesis_release,
        rollback_evidence_path,
    ) = _successful_reapplication(tmp_path)
    release = build_genesis_birth_release(
        paths,
        private_state,
        execution_path,
        acknowledged_genesis_execution_sha256=execution["result_artifact"]["sha256"],
        genesis_rollback_verification_path=rollback_evidence_path,
        selected_nodes=("mainneta-super1",),
        superseded_service_uuid=superseded_service_uuid,
        acknowledged_superseded_service_removal=(
            f"REMOVE:mainneta-super1:{superseded_service_uuid}"
            if superseded_service_uuid is not None
            else None
        ),
    )
    path, digest = write_genesis_birth_release(
        paths, release, operation=_operation("birth-release")
    )
    return paths, private_state, execution_path, execution, genesis_release, path, digest, release


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


class _BirthOpener:
    def __init__(
        self,
        original_compose: str,
        proof_compose: str,
        *,
        healthy: bool = True,
        normalized_readback: bool = False,
        wrapped_readback: bool = False,
        omit_compose: bool = False,
        already_proof: bool = False,
        active_deployment: bool = False,
        superseded_service_uuid: str | None = None,
        initial_compose: str | None = None,
        cleanup_failure_log: bool = False,
    ) -> None:
        self.original_compose = original_compose
        self.initial_compose = (
            initial_compose if initial_compose is not None else original_compose
        )
        self.proof_compose = proof_compose
        self.healthy = healthy
        self.normalized_readback = normalized_readback
        self.wrapped_readback = wrapped_readback
        self.omit_compose = omit_compose
        self.requests: list[tuple[str, str]] = []
        self.patched = already_proof
        self.stopped = False
        self.deployed = False
        self.active_deployment = active_deployment
        self.superseded_service_uuid = superseded_service_uuid
        self.superseded_service_present = superseded_service_uuid is not None
        self.cleanup_failure_log = cleanup_failure_log

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        assert parsed.hostname == "coolify-a.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"
        assert timeout > 0
        if method == "GET" and path == "/api/v1/services":
            if self.deployed and self.healthy:
                status = "running:healthy"
            elif self.stopped:
                status = "exited"
            else:
                status = "running:unknown"
            return _Response([{"uuid": "svc-mainneta-super1", "name": "mainneta-super1", "status": status}])
        if method == "GET" and path == "/api/v1/deployments":
            if not self.active_deployment:
                return _Response([])
            return _Response([
                {
                    "deployment_uuid": "active-proof-deployment",
                    "service_uuid": "svc-mainneta-super1",
                    "status": "in_progress",
                }
            ])
        if method == "POST" and path == "/api/v1/deployments/active-proof-deployment/cancel":
            assert self.active_deployment is True
            self.active_deployment = False
            return _Response({"message": "cancelled"}, status=200)
        superseded_path = (
            f"/api/v1/services/{self.superseded_service_uuid}"
            if self.superseded_service_uuid is not None
            else None
        )
        if superseded_path is not None and path == superseded_path:
            if method == "GET":
                if not self.superseded_service_present:
                    return _Response({}, status=404)
                return _Response({
                    "uuid": self.superseded_service_uuid,
                    "name": "mainneta-super1",
                    "status": "running:healthy",
                })
            if method == "DELETE":
                self.superseded_service_present = False
                return _Response({}, status=204)
        if method == "GET" and path in {
            "/api/v1/services/svc-mainneta-super1/logs",
            "/api/v1/services/svc-mainneta-super1/docker/logs",
            "/api/v1/services/svc-mainneta-super1/applications/logs",
        }:
            if self.cleanup_failure_log:
                return _Response({
                    "logs": (
                        'Container mother-superseded-service-cleanup-lmjwoglwv7ryvrfsbfuu4o7k Error '
                        'service "mother-superseded-service-cleanup" did not complete successfully: exit 1\n'
                        'refusing container outside acknowledged cleanup boundary'
                    )
                })
            return _Response({
                "logs": "mother-superseded-service-cleanup completed successfully"
            })
        if method == "GET" and path == "/api/v1/services/svc-mainneta-super1":
            compose = self.proof_compose if self.patched else self.initial_compose
            if self.omit_compose:
                payload: dict[str, Any] = {"uuid": "svc-mainneta-super1", "name": "mainneta-super1"}
            else:
                if self.normalized_readback:
                    compose = yaml.safe_dump(yaml.safe_load(compose), sort_keys=True)
                payload = {"uuid": "svc-mainneta-super1", "name": "mainneta-super1", "docker_compose_raw": compose}
            return _Response({"service": payload} if self.wrapped_readback else payload)
        if method == "GET" and path == "/api/v1/services/svc-mainneta-super1/stop":
            self.stopped = True
            return _Response({"message": "stopped"}, status=200)
        if method == "PATCH" and path == "/api/v1/services/svc-mainneta-super1":
            assert self.stopped is True
            body = json.loads(request.data.decode("utf-8"))
            import base64
            assert base64.b64decode(body["docker_compose_raw"]).decode("utf-8") == self.proof_compose
            self.patched = True
            return _Response({"uuid": "svc-mainneta-super1"}, status=200)
        if method == "GET" and path == "/api/v1/deploy":
            assert self.stopped is True
            self.deployed = True
            self.stopped = False
            return _Response({"deployment_uuid": "proof-deploy"}, status=200)
        raise AssertionError(f"unexpected request {method} {request.full_url}")


def test_birth_release_is_internal_only_and_removes_host_rpc_mapping(tmp_path: Path) -> None:
    paths, private_state, _, execution, _, release_path, digest, release = _birth_release(tmp_path)
    compose = release["proof_plan"]["proof_compose"]["canonical_text"]
    guardian = compose.split("  mother-genesis-proof-guardian:", 1)[1].split("\nvolumes:\n", 1)[0]
    compose_document = yaml.safe_load(compose)
    services = compose_document["services"]
    assert services["mother-genesis-init"]["pull_policy"] == "missing"
    assert services["mother-genesis-init"]["exclude_from_hc"] is True
    assert services["mainneta-super1"]["pull_policy"] == "missing"
    assert services["mother-super-node-fdb"]["pull_policy"] == "missing"
    assert services["mother-genesis-proof-guardian"]["pull_policy"] == "missing"
    assert services["mother-super-node-hub"]["pull_policy"] == "build"
    fdb_healthcheck = services["mother-super-node-fdb"]["healthcheck"]["test"]
    assert "fdbcli" in " ".join(fdb_healthcheck)
    assert "status" in " ".join(fdb_healthcheck)
    assert (
        release["proof_plan"]["proof_compose"]["coolify_health_model"][
            "foundationdb_healthcheck"
        ]
        is True
    )
    assert (
        release["proof_plan"]["proof_compose"]["coolify_health_model"][
            "init_excluded_from_hc"
        ]
        is True
    )
    assert "127.0.0.1:8545:8545" not in compose
    assert "ports:" not in guardian
    assert "expose:" not in guardian
    assert "traefik." not in guardian
    assert release["proof_plan"]["proof"]["manual_ssh_required"] is False
    assert release["proof_plan"]["proof"]["public_endpoint_created"] is False
    assert release["proof_plan"]["hub"]["service"] == "mother-super-node-hub"
    assert release["proof_plan"]["hub"]["local_rpc_url"] == "http://mainneta-super1:8545"
    assert "hub-health" in release["proof_plan"]["proof"]["predicates"]
    assert "hub-local-rpc-binding" in release["proof_plan"]["proof"]["predicates"]
    assert "HUB = 'http://mother-super-node-hub:8790'" in guardian
    assert "hub('/api/hub/v1/health')" in guardian
    assert "hub local RPC binding mismatch" in guardian
    verified = verify_genesis_birth_release(
        paths, private_state, release_path, selected_nodes=("mainneta-super1",)
    )
    assert verified["clean"] is True
    assert verified["genesis_birth_release_sha256"] == digest
    assert verified["genesis_execution_sha256"] == execution["result_artifact"]["sha256"]


def test_birth_release_rejects_wrong_execution_digest(tmp_path: Path) -> None:
    paths, private_state, execution_path, _, _, rollback_evidence_path = _successful_reapplication(tmp_path)
    with pytest.raises(MotherDeploymentGenesisBirthError) as caught:
        build_genesis_birth_release(
            paths,
            private_state,
            execution_path,
            acknowledged_genesis_execution_sha256="0" * 64,
            genesis_rollback_verification_path=rollback_evidence_path,
            selected_nodes=("mainneta-super1",),
        )
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_BIRTH_ACKNOWLEDGEMENT_MISMATCH"



def test_birth_release_requires_exact_superseded_service_removal_acknowledgement(
    tmp_path: Path,
) -> None:
    (
        paths,
        private_state,
        execution_path,
        execution,
        _,
        rollback_evidence_path,
    ) = _successful_reapplication(tmp_path)
    with pytest.raises(
        MotherDeploymentGenesisBirthError,
        match="--acknowledge-superseded-service-removal must equal",
    ):
        build_genesis_birth_release(
            paths,
            private_state,
            execution_path,
            acknowledged_genesis_execution_sha256=execution[
                "result_artifact"
            ]["sha256"],
            genesis_rollback_verification_path=rollback_evidence_path,
            selected_nodes=("mainneta-super1",),
            superseded_service_uuid="pc20bsxvq3ykjnpzque08l63",
            acknowledged_superseded_service_removal="REMOVE:wrong:value",
        )


def test_birth_release_rejects_rolled_back_execution_without_reapplication(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    (
        paths,
        private_state,
        _,
        _,
        _,
        release_path,
        release_digest,
        _,
    ) = _genesis_release(tmp_path, now=now)
    live = _GenesisOpener()
    first = execute_released_genesis(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1",),
        opener=live,
        operation=_operation("birth-reject-rolled-back-genesis"),
    )
    rolled_back = execute_genesis_mutation_rollback(
        paths,
        private_state,
        Path(first["result_artifact"]["path"]),
        acknowledged_execution_sha256=first["result_artifact"]["sha256"],
        opener=live,
        max_wait_seconds=1.0,
        poll_interval_seconds=0.0,
        operation=_operation("birth-reject-rolled-back-genesis-rollback"),
    )
    verification = verify_genesis_mutation_rollback(
        paths,
        private_state,
        Path(rolled_back["result_artifact"]["path"]),
        opener=live,
    )
    evidence_path, _ = write_genesis_mutation_rollback_verification(
        paths,
        verification,
        operation=_operation("birth-reject-rolled-back-genesis-evidence"),
    )

    with pytest.raises(MotherDeploymentGenesisBirthError) as caught:
        build_genesis_birth_release(
            paths,
            private_state,
            Path(first["result_artifact"]["path"]),
            acknowledged_genesis_execution_sha256=first["result_artifact"]["sha256"],
            genesis_rollback_verification_path=evidence_path,
            selected_nodes=("mainneta-super1",),
        )
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_ROLLBACK_REAPPLICATION_REQUIRED"


def test_birth_inspection_is_network_free(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release_path, digest, _ = _birth_release(tmp_path)
    result = inspect_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
    )
    assert result["clean"] is True
    assert result["manual_ssh_required"] is False
    assert result["public_endpoint_created"] is False
    assert result["network_access_performed"] is False
    assert result["release_already_claimed"] is False


def test_birth_executor_proves_chain_through_internal_guardian_and_coolify(tmp_path: Path) -> None:
    paths, private_state, _, _, genesis_release, release_path, digest, release = _birth_release(tmp_path)
    original = genesis_release["execution_plan"]["compose"]["canonical_text"]
    proof = release["proof_plan"]["proof_compose"]["canonical_text"]
    opener = _BirthOpener(original, proof)
    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        opener=opener,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("birth-live"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["initial_chain_proven"] is True
    assert result["summary"]["manual_ssh_required"] is False
    assert result["summary"]["public_endpoint_created"] is False
    assert result["summary"]["hub_healthy"] is True
    assert result["summary"]["hub_local_rpc_verified"] is True
    assert result["summary"]["complete_super_node_proven"] is True
    assert result["proof"]["service_status"] == "running:healthy"
    assert result["proof"]["hub_service"] == "mother-super-node-hub"
    assert result["proof"]["hub_local_rpc_url"] == "http://mainneta-super1:8545"
    assert result["summary"]["service_stopped_before_deploy"] is True
    assert len(result["mutation_receipts"]) == 3
    assert result["mutation_receipts"][0]["endpoint"].endswith("/stop")
    assert all("coolify-c" not in path for _, path in opener.requests)
    verified = verify_genesis_birth_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        selected_nodes=("mainneta-super1",),
    )
    assert verified["initial_chain_proven"] is True
    assert verified["hub_healthy"] is True
    assert verified["hub_local_rpc_verified"] is True
    assert verified["complete_super_node_proven"] is True
    assert verified["super_node_components"] == [
        "hub",
        "local-rpc",
        "besu",
        "qbft-validator",
        "foundationdb",
    ]
    assert verified["next_phase"] == "stage-soft-replica-configuration"


def test_birth_executor_quiesces_active_deployment_and_recovers_partial_retry(
    tmp_path: Path,
) -> None:
    paths, private_state, _, _, genesis_release, release_path, digest, release = _birth_release(tmp_path)
    opener = _BirthOpener(
        genesis_release["execution_plan"]["compose"]["canonical_text"],
        release["proof_plan"]["proof_compose"]["canonical_text"],
        already_proof=True,
        active_deployment=True,
    )
    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        opener=opener,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("birth-partial-retry"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["observed_active_deployment_count"] == 1
    assert result["summary"]["cancelled_active_deployment_count"] == 1
    assert result["summary"]["service_stopped_before_deploy"] is True
    assert result["deployment_cancellation_receipts"] == [
        {
            "deployment_uuid": "active-proof-deployment",
            "status": "cancelled",
            "request_accepted": True,
            "response": {
                "status": 200,
                "response_sha256": result["deployment_cancellation_receipts"][0]["response"]["response_sha256"],
                "elapsed_ms": result["deployment_cancellation_receipts"][0]["response"]["elapsed_ms"],
            },
            "observed_status": "in_progress",
        }
    ]
    compose_receipt = next(
        item
        for item in result["precondition_receipts"]
        if item["name"] == "executed-compose-binding"
    )
    assert compose_receipt["compose_state"] == "proof-compose-already-installed"
    cancel_index = opener.requests.index(
        ("POST", "/api/v1/deployments/active-proof-deployment/cancel")
    )
    stop_index = opener.requests.index(
        ("GET", "/api/v1/services/svc-mainneta-super1/stop")
    )
    patch_index = opener.requests.index(
        ("PATCH", "/api/v1/services/svc-mainneta-super1")
    )
    deploy_index = opener.requests.index(("GET", "/api/v1/deploy"))
    assert cancel_index < stop_index < patch_index < deploy_index


def test_birth_executor_removes_exact_acknowledged_superseded_service_before_deploy(
    tmp_path: Path,
) -> None:
    superseded_uuid = "pc20bsxvq3ykjnpzque08l63"
    (
        paths,
        private_state,
        _,
        _,
        genesis_release,
        release_path,
        digest,
        release,
    ) = _birth_release(
        tmp_path,
        superseded_service_uuid=superseded_uuid,
    )
    verified_release = verify_genesis_birth_release(
        paths,
        private_state,
        release_path,
        selected_nodes=("mainneta-super1",),
    )
    assert verified_release[
        "exact_superseded_service_removal_authorized"
    ] is True
    assert verified_release["superseded_service_uuid"] == superseded_uuid

    opener = _BirthOpener(
        genesis_release["execution_plan"]["compose"]["canonical_text"],
        release["proof_plan"]["proof_compose"]["canonical_text"],
        already_proof=True,
        superseded_service_uuid=superseded_uuid,
    )
    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        opener=opener,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("birth-remove-superseded-service"),
    )

    assert result["status"] == "pass"
    assert result["superseded_service_removal"]["status"] == "pass"
    assert result["superseded_service_removal"]["service_uuid"] == superseded_uuid
    assert result["superseded_service_removal"]["already_absent"] is False
    assert result["summary"]["superseded_service_removed_before_deploy"] is True
    assert result["policy"]["exact_superseded_service_removed_before_deploy"] is True
    verified_evidence = verify_genesis_birth_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        selected_nodes=("mainneta-super1",),
    )
    assert verified_evidence["superseded_service_uuid"] == superseded_uuid
    assert verified_evidence["superseded_service_removed_before_deploy"] is True

    stale_endpoint = f"/api/v1/services/{superseded_uuid}"
    delete_index = opener.requests.index(("DELETE", stale_endpoint))
    target_stop_index = opener.requests.index(
        ("GET", "/api/v1/services/svc-mainneta-super1/stop")
    )
    target_patch_index = opener.requests.index(
        ("PATCH", "/api/v1/services/svc-mainneta-super1")
    )
    target_deploy_index = opener.requests.index(("GET", "/api/v1/deploy"))
    assert delete_index < target_stop_index < target_patch_index < target_deploy_index


def test_birth_executor_recovers_api_absent_orphan_with_exact_host_cleanup_gate(
    tmp_path: Path,
) -> None:
    superseded_uuid = "pc20bsxvq3ykjnpzque08l63"
    (
        paths,
        private_state,
        _,
        _,
        genesis_release,
        release_path,
        digest,
        release,
    ) = _birth_release(
        tmp_path,
        superseded_service_uuid=superseded_uuid,
    )
    final_compose = release["proof_plan"]["proof_compose"]["canonical_text"]
    precleanup = release["proof_plan"]["precleanup_proof_compose"]
    assert isinstance(precleanup, dict)
    precleanup_compose = precleanup["canonical_text"]

    document = yaml.safe_load(final_compose)
    cleanup = document["services"]["mother-superseded-service-cleanup"]
    assert cleanup["image"] == "docker:27-cli"
    assert cleanup["pull_policy"] == "missing"
    assert cleanup["exclude_from_hc"] is True
    assert document["services"]["mother-genesis-init"]["pull_policy"] == "missing"
    assert document["services"]["mother-genesis-init"]["exclude_from_hc"] is True
    assert document["services"]["mainneta-super1"]["pull_policy"] == "missing"
    assert document["services"]["mother-super-node-fdb"]["pull_policy"] == "missing"
    assert document["services"]["mother-super-node-fdb"]["healthcheck"]["test"] == [
        "CMD-SHELL",
        "fdbcli --exec status >/dev/null 2>&1 || exit 1",
    ]
    assert document["services"]["mother-genesis-proof-guardian"]["pull_policy"] == "missing"
    assert cleanup["restart"] == "no"
    assert cleanup["read_only"] is True
    assert cleanup["network_mode"] == "none"
    assert cleanup["volumes"] == [
        "/var/run/docker.sock:/var/run/docker.sock",
        "mother-proof:/proof",
    ]
    cleanup_script = cleanup["command"][-1]
    assert f"project='{superseded_uuid}'" in cleanup_script
    assert "node='mainneta-super1'" in cleanup_script
    assert (
        "recovery_guardian='mother-validator-quorum-recovery-initial-guardian'"
        in cleanup_script
    )
    assert "refusing container outside acknowledged cleanup boundary" in cleanup_script
    assert 'docker rm -f "$$id"' in cleanup_script
    assert "/proof/superseded-host-cleanup.json" in cleanup_script
    assert "port 30303 still owned after superseded cleanup" in cleanup_script
    assert "docker volume" not in cleanup_script
    assert "docker system prune" not in cleanup_script
    assert "docker compose down" not in cleanup_script
    assert (
        document["services"]["mainneta-super1"]["depends_on"][
            "mother-superseded-service-cleanup"
        ]["condition"]
        == "service_completed_successfully"
    )
    assert "mother-superseded-service-cleanup" not in precleanup_compose
    assert "/var/run/docker.sock" not in precleanup_compose

    opener = _BirthOpener(
        genesis_release["execution_plan"]["compose"]["canonical_text"],
        final_compose,
        initial_compose=precleanup_compose,
        superseded_service_uuid=superseded_uuid,
    )
    opener.superseded_service_present = False
    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        opener=opener,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("birth-remove-api-absent-orphan"),
    )

    assert result["status"] == "pass"
    assert result["superseded_service_removal"]["already_absent"] is True
    assert result["summary"][
        "superseded_host_containers_removed_before_besu"
    ] is True
    assert result["policy"][
        "exact_superseded_host_container_cleanup_authorized"
    ] is True
    assert result["proof"]["host_cleanup_guardian_proof_required"] is True
    assert result["host_cleanup_log_snapshots"]
    assert result["summary"]["host_cleanup_logs_observed"] is True
    compose_receipt = next(
        item
        for item in result["precondition_receipts"]
        if item["name"] == "executed-compose-binding"
    )
    assert (
        compose_receipt["compose_state"]
        == "precleanup-proof-compose-already-installed"
    )
    stale_endpoint = f"/api/v1/services/{superseded_uuid}"
    assert ("GET", stale_endpoint) in opener.requests
    assert ("DELETE", stale_endpoint) not in opener.requests
    target_patch_index = opener.requests.index(
        ("PATCH", "/api/v1/services/svc-mainneta-super1")
    )
    target_deploy_index = opener.requests.index(("GET", "/api/v1/deploy"))
    assert target_patch_index < target_deploy_index


def test_birth_executor_accepts_pre_coolify_health_model_retry_state(
    tmp_path: Path,
) -> None:
    superseded_uuid = "pc20bsxvq3ykjnpzque08l63"
    (
        paths,
        private_state,
        _,
        _,
        genesis_release,
        release_path,
        digest,
        release,
    ) = _birth_release(
        tmp_path,
        superseded_service_uuid=superseded_uuid,
    )
    final_compose = release["proof_plan"]["proof_compose"]["canonical_text"]
    legacy_live_compose = birth_module._without_coolify_health_model(final_compose)
    legacy_document = yaml.safe_load(legacy_live_compose)
    assert "healthcheck" not in legacy_document["services"]["mother-super-node-fdb"]
    assert (
        "exclude_from_hc"
        not in legacy_document["services"]["mother-superseded-service-cleanup"]
    )
    assert "exclude_from_hc" not in legacy_document["services"]["mother-genesis-init"]

    opener = _BirthOpener(
        genesis_release["execution_plan"]["compose"]["canonical_text"],
        final_compose,
        initial_compose=legacy_live_compose,
        superseded_service_uuid=superseded_uuid,
    )
    opener.superseded_service_present = False
    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        opener=opener,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("birth-pre-coolify-health-model-retry-state"),
    )

    assert result["status"] == "pass"
    compose_receipt = next(
        item
        for item in result["precondition_receipts"]
        if item["name"] == "executed-compose-binding"
    )
    assert (
        compose_receipt["compose_state"]
        == "proof-compose-without-coolify-health-model-already-installed"
    )
    target_patch_index = opener.requests.index(
        ("PATCH", "/api/v1/services/svc-mainneta-super1")
    )
    target_deploy_index = opener.requests.index(("GET", "/api/v1/deploy"))
    assert target_patch_index < target_deploy_index


def test_birth_executor_accepts_pre_pull_policy_internal_proof_retry_state(
    tmp_path: Path,
) -> None:
    superseded_uuid = "pc20bsxvq3ykjnpzque08l63"
    (
        paths,
        private_state,
        _,
        _,
        genesis_release,
        release_path,
        digest,
        release,
    ) = _birth_release(
        tmp_path,
        superseded_service_uuid=superseded_uuid,
    )
    final_compose = release["proof_plan"]["proof_compose"]["canonical_text"]
    legacy_live_compose = birth_module._without_pull_policy_missing(final_compose)
    legacy_document = yaml.safe_load(legacy_live_compose)
    assert "pull_policy" not in legacy_document["services"]["mother-super-node-fdb"]
    assert (
        legacy_document["services"]["mother-super-node-hub"]["pull_policy"]
        == "build"
    )

    opener = _BirthOpener(
        genesis_release["execution_plan"]["compose"]["canonical_text"],
        final_compose,
        initial_compose=legacy_live_compose,
        superseded_service_uuid=superseded_uuid,
    )
    opener.superseded_service_present = False
    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        opener=opener,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("birth-pre-pull-policy-retry-state"),
    )

    assert result["status"] == "pass"
    compose_receipt = next(
        item
        for item in result["precondition_receipts"]
        if item["name"] == "executed-compose-binding"
    )
    assert (
        compose_receipt["compose_state"]
        == "proof-compose-without-runtime-pull-policy-already-installed"
    )
    target_patch_index = opener.requests.index(
        ("PATCH", "/api/v1/services/svc-mainneta-super1")
    )
    target_deploy_index = opener.requests.index(("GET", "/api/v1/deploy"))
    assert target_patch_index < target_deploy_index


def test_birth_executor_accepts_semantically_equivalent_normalized_compose_readback(tmp_path: Path) -> None:
    paths, private_state, _, _, genesis_release, release_path, digest, release = _birth_release(tmp_path)
    opener = _BirthOpener(
        genesis_release["execution_plan"]["compose"]["canonical_text"],
        release["proof_plan"]["proof_compose"]["canonical_text"],
        normalized_readback=True,
        wrapped_readback=True,
    )
    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        opener=opener,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("birth-normalized-compose"),
    )
    assert result["status"] == "pass"
    bindings = {
        receipt["name"]: receipt.get("binding_mode")
        for receipt in result["precondition_receipts"]
    }
    assert bindings["executed-compose-binding"] == "canonical-compose-semantics"
    assert bindings["proof-compose-binding"] == "canonical-compose-semantics"


def test_birth_executor_fails_closed_when_compose_fields_are_unavailable(tmp_path: Path) -> None:
    paths, private_state, _, _, genesis_release, release_path, digest, release = _birth_release(tmp_path)
    opener = _BirthOpener(
        genesis_release["execution_plan"]["compose"]["canonical_text"],
        release["proof_plan"]["proof_compose"]["canonical_text"],
        omit_compose=True,
    )
    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        opener=opener,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("birth-compose-unavailable"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_UNAVAILABLE"
    assert result["mutation_receipts"] == []
    assert result["summary"]["live_mutation_performed"] is False



def test_birth_cleanup_log_404_snapshots_are_not_failure_or_observed_logs() -> None:
    snapshots = [
        {
            "classification": "coolify-log-endpoint-unavailable",
            "ok": False,
            "status": 404,
            "log_excerpt": (
                'Not found. service "mother-superseded-service-cleanup" '
                "did not complete successfully: exit 1"
            ),
        }
    ]

    assert birth_module._host_cleanup_failed_from_logs(snapshots) is None
    assert (
        birth_module._cleanup_log_snapshot_has_runtime_text(snapshots[0])
        is False
    )


def test_birth_executor_reports_superseded_cleanup_failure_logs(
    tmp_path: Path,
) -> None:
    superseded_uuid = "pc20bsxvq3ykjnpzque08l63"
    (
        paths,
        private_state,
        _,
        _,
        genesis_release,
        release_path,
        digest,
        release,
    ) = _birth_release(
        tmp_path,
        superseded_service_uuid=superseded_uuid,
    )
    opener = _BirthOpener(
        genesis_release["execution_plan"]["compose"]["canonical_text"],
        release["proof_plan"]["proof_compose"]["canonical_text"],
        initial_compose=release["proof_plan"]["precleanup_proof_compose"][
            "canonical_text"
        ],
        superseded_service_uuid=superseded_uuid,
        healthy=False,
        cleanup_failure_log=True,
    )
    opener.superseded_service_present = False

    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        opener=opener,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("birth-cleanup-failure-logs"),
    )

    assert result["status"] == "failed"
    assert (
        result["failure"]["code"]
        == "MOTHER_DEPLOY_GENESIS_BIRTH_HOST_CLEANUP_FAILED"
    )
    assert result["host_cleanup_log_snapshots"]
    assert any(
        "mother-superseded-service-cleanup" in item.get("log_excerpt", "")
        for item in result["host_cleanup_log_snapshots"]
    )
    assert result["summary"]["host_cleanup_logs_observed"] is True
    assert result["summary"]["complete"] is False
    assert result["summary"]["next_phase"] == "manual-review-required"


def test_birth_executor_fails_closed_when_guardian_never_becomes_healthy(tmp_path: Path) -> None:
    paths, private_state, _, _, genesis_release, release_path, digest, release = _birth_release(tmp_path)
    opener = _BirthOpener(
        genesis_release["execution_plan"]["compose"]["canonical_text"],
        release["proof_plan"]["proof_compose"]["canonical_text"],
        healthy=False,
    )
    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        opener=opener,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("birth-unhealthy"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_GENESIS_BIRTH_NOT_HEALTHY"
    assert result["summary"]["initial_chain_proven"] is False
    inspected = inspect_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
    )
    assert inspected["release_already_claimed"] is True


def test_birth_executor_writes_evidence_when_interrupted_after_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, private_state, _, _, _genesis_release, release_path, digest, _release = _birth_release(tmp_path)

    def interrupt_after_claim(*_args: Any, **_kwargs: Any):  # noqa: ANN401
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        birth_module,
        "resolve_coolify_controller",
        interrupt_after_claim,
    )

    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("birth-interrupted-after-claim"),
    )

    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_GENESIS_BIRTH_INTERRUPTED"
    assert result["mutation_receipts"] == []
    assert result["summary"]["live_mutation_performed"] is False
    evidence_path = Path(result["evidence"]["path"])
    assert evidence_path.is_file()
    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert persisted["release"]["sha256"] == digest
    assert persisted["failure"]["code"] == "MOTHER_DEPLOY_GENESIS_BIRTH_INTERRUPTED"

    inspected = inspect_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
    )
    assert inspected["release_already_claimed"] is True


def test_birth_cli_release_verify_and_dry_apply(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths, _, execution_path, execution, _, rollback_evidence_path = _successful_reapplication(tmp_path)
    runtime_root = paths.root.parent
    code = mother_deploy.main([
        "release-genesis-birth",
        "--execution", str(execution_path),
        "--acknowledge-genesis-execution-sha256", execution["result_artifact"]["sha256"],
        "--genesis-rollback-verification", str(rollback_evidence_path),
        "--node", "mainneta-super1",
        "--runtime-state-root", str(runtime_root),
        "--write-release",
    ])
    assert code == 0
    release = json.loads(capsys.readouterr().out)
    release_path = release["release_artifact"]["path"]
    release_sha = release["release_artifact"]["sha256"]
    code = mother_deploy.main([
        "verify-genesis-birth-release",
        "--release", release_path,
        "--node", "mainneta-super1",
        "--runtime-state-root", str(runtime_root),
    ])
    assert code == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["manual_ssh_required"] is False
    assert verified["public_endpoint_created"] is False
    code = mother_deploy.main([
        "apply-genesis-birth",
        "--release", release_path,
        "--acknowledge-release-sha256", release_sha,
        "--node", "mainneta-super1",
        "--runtime-state-root", str(runtime_root),
    ])
    assert code == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["execute_requested"] is False
    assert inspected["network_access_performed"] is False
