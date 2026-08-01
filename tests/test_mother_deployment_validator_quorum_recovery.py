from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_validator_quorum_recovery import (
    MotherDeploymentValidatorQuorumRecoveryError,
    build_validator_quorum_recovery_release,
    diagnose_validator_quorum_runtime,
    execute_validator_quorum_recovery_release,
    inspect_validator_quorum_recovery_release,
    reconcile_validator_quorum_recovery,
    verify_validator_quorum_recovery_evidence,
    verify_validator_quorum_recovery_reconciliation,
    verify_validator_quorum_recovery_release,
    write_validator_quorum_recovery_release,
)
from tests.test_mother_deployment_executor import TOKEN_A, TOKEN_C, _operation
from tests.test_mother_deployment_validator_admission import _AdmissionResponse, _transaction_fixture


def _fixture(tmp_path: Path):
    paths, private_state, transaction, transaction_path, transaction_digest = _transaction_fixture(tmp_path)
    release = build_validator_quorum_recovery_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=transaction_digest,
        selected_nodes=("mainnetc-super1",),
    )
    release_path, release_digest = write_validator_quorum_recovery_release(
        paths,
        release,
        operation=_operation("validator-quorum-recovery-release-fixture"),
    )
    return paths, private_state, transaction_path, transaction_digest, release, release_path, release_digest


class _QuorumRecoveryOpener:
    def __init__(
        self,
        release: dict[str, Any],
        *,
        bad_a_compose: bool = False,
        bad_c_compose: bool = False,
        c_already_recovery: bool = False,
        fail_c_patch: bool = False,
        never_healthy: bool = False,
        aggregate_degraded_components_healthy: bool = False,
    ) -> None:
        self.release = release
        self.bad_a_compose = bad_a_compose
        self.bad_c_compose = bad_c_compose
        self.c_already_recovery = c_already_recovery
        self.fail_c_patch = fail_c_patch
        self.never_healthy = never_healthy
        self.aggregate_degraded_components_healthy = aggregate_degraded_components_healthy
        self.c_patched = False
        self.c_deployed = False
        self.a_patched = False
        self.a_deployed = False
        self.c_polls = 0
        self.a_polls = 0
        self.requests: list[tuple[str, str, str]] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        method = request.get_method()
        path = parsed.path
        self.requests.append((host, method, path))
        assert timeout > 0
        expected_token = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"
        pre = self.release["preconditions"]
        plan = self.release["execution_plan"]

        if host == "coolify-c.invalid":
            service = pre["replica"]
            if method == "GET" and path == "/api/v1/services":
                if not self.c_deployed:
                    status = "degraded:unhealthy"
                else:
                    self.c_polls += 1
                    status = "degraded:unhealthy" if (self.never_healthy or self.aggregate_degraded_components_healthy) else ("starting:unhealthy" if self.c_polls == 1 else "running:healthy")
                return _AdmissionResponse([{"uuid": service["service_uuid"], "name": service["node"], "status": status}])
            if method == "GET" and path == f"/api/v1/services/{service['service_uuid']}":
                if self.c_patched or self.c_already_recovery:
                    compose = plan["replica_readiness_compose"]["canonical_text"]
                elif self.bad_c_compose:
                    compose = service["compose"]["canonical_text"].replace(
                        "main_computer.mother.node: mainnetc-super1",
                        "main_computer.mother.node: wrong-node",
                        1,
                    )
                else:
                    compose = service["compose"]["canonical_text"]
                payload = {"service": {"uuid": service["service_uuid"], "name": service["node"], "docker_compose_raw": compose}}
                if self.aggregate_degraded_components_healthy and self.c_deployed:
                    payload["service"]["applications"] = [
                        {"uuid": "c-besu", "name": service["node"], "status": "running:healthy", "image": "hyperledger/besu:latest"},
                        {"uuid": "c-guardian", "name": "mother-validator-quorum-recovery-replica-guardian", "status": "running:healthy", "image": "python:3.12-alpine"},
                        {"uuid": "c-old", "name": "mother-replica-sync-guardian", "status": "exited", "image": "python:3.12-alpine"},
                    ]
                return _AdmissionResponse(payload)
            if method == "PATCH" and path == f"/api/v1/services/{service['service_uuid']}":
                if self.fail_c_patch:
                    return _AdmissionResponse({"message": "rejected"}, status=500)
                body = json.loads(request.data.decode("utf-8"))
                compose = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
                assert compose == plan["replica_readiness_compose"]["canonical_text"]
                self.c_patched = True
                return _AdmissionResponse({"uuid": service["service_uuid"]}, status=200)
            if method == "GET" and path == "/api/v1/deploy":
                assert self.c_patched
                self.c_deployed = True
                return _AdmissionResponse({"deployment_uuid": "quorum-c"}, status=200)

        if host == "coolify-a.invalid":
            service = pre["initial"]
            if method == "GET" and path == "/api/v1/services":
                if not self.a_deployed:
                    status = "degraded:unhealthy"
                else:
                    self.a_polls += 1
                    status = "degraded:unhealthy" if (self.never_healthy or self.aggregate_degraded_components_healthy) else ("starting:unhealthy" if self.a_polls == 1 else "running:healthy")
                return _AdmissionResponse([{"uuid": service["service_uuid"], "name": service["node"], "status": status}])
            if method == "GET" and path == f"/api/v1/services/{service['service_uuid']}":
                if self.a_patched:
                    compose = plan["initial_quorum_compose"]["canonical_text"]
                elif self.bad_a_compose:
                    compose = service["compose"]["canonical_text"].replace("main_computer.mother.node: mainneta-super1", "main_computer.mother.node: wrong-node", 1)
                else:
                    compose = service["compose"]["canonical_text"]
                payload = {"service": {"uuid": service["service_uuid"], "name": service["node"], "docker_compose_raw": compose}}
                if self.aggregate_degraded_components_healthy and self.a_deployed:
                    payload["service"]["applications"] = [
                        {"uuid": "a-besu", "name": service["node"], "status": "running:healthy", "image": "hyperledger/besu:latest"},
                        {"uuid": "a-guardian", "name": "mother-validator-quorum-recovery-initial-guardian", "status": "running:healthy", "image": "python:3.12-alpine"},
                        {"uuid": "a-old", "name": "mother-validator-admission-guardian", "status": "exited", "image": "python:3.12-alpine"},
                    ]
                return _AdmissionResponse(payload)
            if method == "PATCH" and path == f"/api/v1/services/{service['service_uuid']}":
                body = json.loads(request.data.decode("utf-8"))
                compose = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
                assert compose == plan["initial_quorum_compose"]["canonical_text"]
                self.a_patched = True
                return _AdmissionResponse({"uuid": service["service_uuid"]}, status=200)
            if method == "GET" and path == "/api/v1/deploy":
                assert self.a_patched
                self.a_deployed = True
                return _AdmissionResponse({"deployment_uuid": "quorum-a"}, status=200)

        raise AssertionError(f"unexpected request {method} {request.full_url}")


def test_quorum_recovery_release_restarts_c_then_a_without_vote_or_exposure(tmp_path: Path) -> None:
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    plan = release["execution_plan"]
    assert [item["controller_id"] for item in plan["mutations"]] == ["coolify-c", "coolify-a", "coolify-c", "coolify-a"]
    assert [item["method"] for item in plan["mutations"]] == ["PATCH", "PATCH", "GET", "GET"]
    assert release["authority"]["validator_vote_authorized"] is False
    assert release["policy"]["restart_all_validators"] is True
    assert release["policy"]["restart_mode"] == "back-to-back-without-intermediate-health-wait"
    assert release["policy"]["static_peers_symmetric"] is True
    assert release["policy"]["partial_replica_recovery_lineage_allowed"] is True
    lineages = release["preconditions"]["replica"]["accepted_compose_lineages"]
    assert [item["mode"] for item in lineages] == [
        "stale-synchronization-compose",
        "already-installed-quorum-recovery-readiness",
    ]
    assert lineages[1]["semantic_sha256"] == plan["replica_readiness_compose"]["semantic_sha256"]
    c_compose = plan["replica_readiness_compose"]["canonical_text"]
    a_compose = plan["initial_quorum_compose"]["canonical_text"]
    assert "--static-nodes-file=/config/static-nodes.json" in c_compose
    assert "--static-nodes-file=/config/static-nodes.json" in a_compose
    assert plan["replica_readiness_compose"]["static_peer_enode"] == plan["bootnode_enode"]
    assert plan["initial_quorum_compose"]["static_peer_enode"] == plan["candidate_enode"]
    assert "mother-validator-quorum-recovery-replica-guardian" in c_compose
    assert "mother-validator-quorum-recovery-initial-guardian" in a_compose
    assert "qbft_proposeValidatorVote" not in a_compose
    assert "8545:8545" not in c_compose
    assert "8545:8545" not in a_compose
    for compose, guardian in (
        (c_compose, "mother-validator-quorum-recovery-replica-guardian"),
        (a_compose, "mother-validator-quorum-recovery-initial-guardian"),
    ):
        section = compose.split(f"  {guardian}:", 1)[1].split("\nvolumes:\n", 1)[0]
        assert "ports:" not in section
        assert "expose:" not in section
        assert "traefik." not in section
    verified = verify_validator_quorum_recovery_release(
        paths, private_state, release_path, selected_nodes=("mainnetc-super1",)
    )
    assert verified["validator_quorum_recovery_release_sha256"] == release_digest
    assert verified["quorum_recovery_authorized"] is True
    assert verified["partial_replica_recovery_lineage_allowed"] is True


def test_quorum_recovery_executor_resets_c_then_a_and_proves_chain(tmp_path: Path) -> None:
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _QuorumRecoveryOpener(release)
    result = execute_validator_quorum_recovery_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-quorum-recovery-live"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["quorum_recovered"] is True
    assert result["summary"]["validator_vote_performed"] is False
    assert result["summary"]["replica_precondition_mode"] == "stale-synchronization-compose"
    assert result["summary"]["replica_recovery_compose_already_installed"] is False
    assert result["summary"]["initial_static_peer_installed"] is True
    assert result["summary"]["validators_restarted_back_to_back"] is True
    mutation_requests = [
        (host, method, path)
        for host, method, path in opener.requests
        if method == "PATCH" or path == "/api/v1/deploy"
    ]
    assert mutation_requests == [
        ("coolify-c.invalid", "PATCH", f"/api/v1/services/{release['preconditions']['replica']['service_uuid']}"),
        ("coolify-a.invalid", "PATCH", f"/api/v1/services/{release['preconditions']['initial']['service_uuid']}"),
        ("coolify-c.invalid", "GET", "/api/v1/deploy"),
        ("coolify-a.invalid", "GET", "/api/v1/deploy"),
    ]
    deploy_indexes = [index for index, (_, method, path) in enumerate(opener.requests) if method == "GET" and path == "/api/v1/deploy"]
    assert len(deploy_indexes) == 2
    assert deploy_indexes[1] == deploy_indexes[0] + 1
    verified = verify_validator_quorum_recovery_evidence(
        paths, private_state, Path(result["evidence"]["path"]), selected_nodes=("mainnetc-super1",)
    )
    assert verified["quorum_recovered"] is True
    assert verified["validator_vote_performed"] is False



def test_quorum_recovery_accepts_required_components_when_aggregate_is_degraded(tmp_path: Path) -> None:
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _QuorumRecoveryOpener(release, aggregate_degraded_components_healthy=True)
    result = execute_validator_quorum_recovery_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-quorum-recovery-component-health"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["component_scoped_health_accepted"] is True
    assert any(item.get("health_mode") == "required-components" for item in result["health_observations"])
    assert any(
        item.get("health_mode") == "required-components"
        for item in result["precondition_receipts"]
        if item.get("name") in {"initial-recovered-status", "replica-recovered-status"}
    )


def test_quorum_recovery_accepts_exact_partially_applied_c_lineage(tmp_path: Path) -> None:
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _QuorumRecoveryOpener(release, c_already_recovery=True)
    result = execute_validator_quorum_recovery_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-quorum-recovery-partial-lineage"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["replica_precondition_mode"] == "already-installed-quorum-recovery-readiness"
    assert result["summary"]["replica_recovery_compose_already_installed"] is True
    compose_receipts = [
        item for item in result["precondition_receipts"]
        if item.get("name") == "replica-recovery-lineage-compose"
    ]
    assert len(compose_receipts) == 1
    assert compose_receipts[0]["precondition_mode"] == "already-installed-quorum-recovery-readiness"
    assert result["summary"]["quorum_recovered"] is True


def test_quorum_recovery_rejects_unrecognized_c_compose_before_mutation(tmp_path: Path) -> None:
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _QuorumRecoveryOpener(release, bad_c_compose=True)
    result = execute_validator_quorum_recovery_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-quorum-recovery-c-drift"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_COMPOSE_MISMATCH"
    assert result["summary"]["replica_precondition_mode"] == "not-checked"
    assert result["summary"]["live_mutation_performed"] is False
    assert all(method != "PATCH" for _, method, _ in opener.requests)

def test_quorum_recovery_rejects_compose_drift_before_mutation(tmp_path: Path) -> None:
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _QuorumRecoveryOpener(release, bad_a_compose=True)
    result = execute_validator_quorum_recovery_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-quorum-recovery-drift"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_COMPOSE_MISMATCH"
    assert result["summary"]["live_mutation_performed"] is False
    assert all(method != "PATCH" for _, method, _ in opener.requests)


def test_quorum_recovery_consumes_release_before_network(tmp_path: Path) -> None:
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _QuorumRecoveryOpener(release, fail_c_patch=True)
    first = execute_validator_quorum_recovery_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-quorum-recovery-first"),
    )
    assert first["status"] == "failed"
    with pytest.raises(MotherDeploymentValidatorQuorumRecoveryError) as caught:
        execute_validator_quorum_recovery_release(
            paths,
            private_state,
            release_path,
            acknowledged_release_sha256=release_digest,
            selected_nodes=("mainnetc-super1",),
            opener=opener,
            operation=_operation("validator-quorum-recovery-replay"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_RELEASE_ALREADY_CONSUMED"


def test_cli_releases_verifies_and_inspects_quorum_recovery(tmp_path: Path, capsys) -> None:
    paths, _, _, transaction_path, transaction_digest = _transaction_fixture(tmp_path)
    runtime_root = paths.root.parent
    code = mother_deploy.main([
        "release-validator-quorum-recovery",
        "--runtime-state-root", str(runtime_root),
        "--transaction", str(transaction_path),
        "--acknowledge-validator-admission-transaction-sha256", transaction_digest,
        "--node", "mainnetc-super1",
        "--write-release",
    ])
    assert code == 0
    released = json.loads(capsys.readouterr().out)
    release_path = released["release_artifact"]["path"]
    release_digest = released["release_artifact"]["sha256"]
    assert released["summary"]["mutation_count"] == 4

    code = mother_deploy.main([
        "verify-validator-quorum-recovery-release",
        "--runtime-state-root", str(runtime_root),
        "--release", release_path,
        "--node", "mainnetc-super1",
    ])
    assert code == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True

    code = mother_deploy.main([
        "apply-validator-quorum-recovery",
        "--runtime-state-root", str(runtime_root),
        "--release", release_path,
        "--acknowledge-release-sha256", release_digest,
        "--node", "mainnetc-super1",
    ])
    assert code == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["network_access_performed"] is False
    assert inspected["validator_vote_performed"] is False


class _QuorumReconciliationOpener:
    def __init__(self, release: dict[str, Any], *, unhealthy_guardian: bool = False, compose_drift: bool = False) -> None:
        self.release = release
        self.unhealthy_guardian = unhealthy_guardian
        self.compose_drift = compose_drift
        self.requests: list[tuple[str, str, str]] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        method = request.get_method()
        path = parsed.path
        self.requests.append((host, method, path))
        assert method == "GET"
        expected_token = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"
        pre = self.release["preconditions"]
        plan = self.release["execution_plan"]
        if host == "coolify-a.invalid":
            service = pre["initial"]
            guardian = "mother-validator-quorum-recovery-initial-guardian"
            compose = plan["initial_quorum_compose"]["canonical_text"]
        else:
            service = pre["replica"]
            guardian = "mother-validator-quorum-recovery-replica-guardian"
            compose = plan["replica_readiness_compose"]["canonical_text"]
        if path == "/api/v1/services":
            return _AdmissionResponse([{
                "uuid": service["service_uuid"], "name": service["node"], "status": "degraded:unhealthy"
            }])
        if path == f"/api/v1/services/{service['service_uuid']}":
            if self.compose_drift:
                compose = compose.replace(service["node"], "wrong-node", 1)
            return _AdmissionResponse({
                "service": {
                    "uuid": service["service_uuid"],
                    "name": service["node"],
                    "status": "degraded:unhealthy",
                    "docker_compose_raw": compose,
                    "applications": [
                        {"uuid": service["node"] + "-besu", "name": service["node"], "status": "running:healthy", "image": "hyperledger/besu:latest"},
                        {"uuid": guardian + "-uuid", "name": guardian, "status": "running:unhealthy" if self.unhealthy_guardian else "running:healthy", "image": "python:3.12-alpine"},
                        {"uuid": "legacy", "name": "legacy-proof", "status": "exited", "image": "python:3.12-alpine"},
                    ],
                }
            })
        raise AssertionError(f"unexpected request {method} {request.full_url}")


def _failed_recovery_for_reconciliation(tmp_path: Path):
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    failed = execute_validator_quorum_recovery_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=_QuorumRecoveryOpener(release, never_healthy=True),
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-quorum-recovery-failed-for-reconciliation"),
    )
    assert failed["status"] == "failed"
    return paths, private_state, release, failed


def test_quorum_reconciliation_accepts_exact_healthy_components_get_only(tmp_path: Path) -> None:
    paths, private_state, release, failed = _failed_recovery_for_reconciliation(tmp_path)
    opener = _QuorumReconciliationOpener(release)
    result = reconcile_validator_quorum_recovery(
        paths,
        private_state,
        Path(failed["evidence"]["path"]),
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        operation=_operation("validator-quorum-reconciliation"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["quorum_recovered"] is True
    assert result["summary"]["component_scoped_health_reconciled"] is True
    assert result["summary"]["aggregate_services_degraded_by_legacy_exited_components"] is True
    assert result["summary"]["live_mutation_performed"] is False
    assert result["summary"]["next_phase"] == "stage-post-admission-steady-state"
    assert all(method == "GET" for _, method, _ in opener.requests)
    stored = json.loads(Path(result["reconciliation_artifact"]["path"]).read_text("utf-8"))
    assert stored["kind"] == "main_computer.mother.deployment_validator_quorum_recovery_reconciliation.v1"
    assert stored["source_failed_evidence"]["sha256"] == failed["evidence"]["sha256"]
    verified = verify_validator_quorum_recovery_reconciliation(
        paths, private_state, Path(result["reconciliation_artifact"]["path"]),
        selected_nodes=("mainnetc-super1",), max_age_seconds=300,
    )
    assert verified["clean"] is True
    assert verified["quorum_recovered"] is True


def test_quorum_reconciliation_rejects_unhealthy_guardian(tmp_path: Path) -> None:
    paths, private_state, release, failed = _failed_recovery_for_reconciliation(tmp_path)
    with pytest.raises(MotherDeploymentValidatorQuorumRecoveryError) as caught:
        reconcile_validator_quorum_recovery(
            paths, private_state, Path(failed["evidence"]["path"]),
            selected_nodes=("mainnetc-super1",), opener=_QuorumReconciliationOpener(release, unhealthy_guardian=True),
            operation=_operation("validator-quorum-reconciliation-unhealthy"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECONCILIATION_COMPONENT_UNHEALTHY"


def test_quorum_reconciliation_rejects_compose_drift(tmp_path: Path) -> None:
    paths, private_state, release, failed = _failed_recovery_for_reconciliation(tmp_path)
    with pytest.raises(MotherDeploymentValidatorQuorumRecoveryError) as caught:
        reconcile_validator_quorum_recovery(
            paths, private_state, Path(failed["evidence"]["path"]),
            selected_nodes=("mainnetc-super1",), opener=_QuorumReconciliationOpener(release, compose_drift=True),
            operation=_operation("validator-quorum-reconciliation-drift"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECONCILIATION_COMPOSE_MISMATCH"


def test_cli_dispatches_quorum_reconciliation(tmp_path: Path, capsys, monkeypatch) -> None:
    paths, _, _, transaction_path, transaction_digest = _transaction_fixture(tmp_path)
    runtime_root = paths.root.parent
    monkeypatch.setattr(
        mother_deploy,
        "reconcile_validator_quorum_recovery",
        lambda *args, **kwargs: {"status": "pass", "summary": {"quorum_recovered": True}},
    )
    code = mother_deploy.main([
        "reconcile-validator-quorum-recovery",
        "--runtime-state-root", str(runtime_root),
        "--evidence", str(paths.root / "evidence" / "deployment-validator-quorum-recovery" / "x.json"),
        "--node", "mainnetc-super1",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["summary"]["quorum_recovered"] is True


def test_cli_dispatches_quorum_reconciliation_verifier(tmp_path: Path, capsys, monkeypatch) -> None:
    paths, _, _, _, _ = _transaction_fixture(tmp_path)
    runtime_root = paths.root.parent
    monkeypatch.setattr(
        mother_deploy,
        "verify_validator_quorum_recovery_reconciliation",
        lambda *args, **kwargs: {"clean": True, "quorum_recovered": True},
    )
    code = mother_deploy.main([
        "verify-validator-quorum-recovery-reconciliation",
        "--runtime-state-root", str(runtime_root),
        "--reconciliation", str(paths.root / "evidence" / "deployment-validator-quorum-recovery-reconciliations" / "x.json"),
        "--node", "mainnetc-super1",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["clean"] is True


class _QuorumDiagnosticOpener:
    def __init__(self, release: dict[str, Any]) -> None:
        self.release = release
        self.requests: list[tuple[str, str]] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        query = parsed.query
        endpoint = path + (("?" + query) if query else "")
        self.requests.append((host, endpoint))
        expected_token = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"
        pre = self.release["preconditions"]
        if host == "coolify-a.invalid":
            service = pre["initial"]
            app_uuid = "app-a-uuid"
            node = service["node"]
            if path == "/api/v1/services":
                return _AdmissionResponse([{"uuid": service["service_uuid"], "name": node, "status": "degraded:unhealthy"}])
            if path == f"/api/v1/services/{service['service_uuid']}":
                return _AdmissionResponse({
                    "service": {
                        "uuid": service["service_uuid"],
                        "name": node,
                        "status": "degraded:unhealthy",
                        "applications": [{"uuid": app_uuid, "name": "mainneta-super1"}],
                    }
                })
            if path == f"/api/v1/services/{service['service_uuid']}/applications":
                return _AdmissionResponse([{"uuid": app_uuid, "name": "mainneta-super1"}])
            if path == f"/api/v1/services/{service['service_uuid']}/logs":
                query_values = parse_qs(query)
                if query_values.get("sub_service_name") == ["mainneta-super1"]:
                    return _AdmissionResponse({
                        "logs": "besu | ERROR | Unknown option --static-nodes-file\nAuthorization: Bearer "
                        + TOKEN_A
                    })
            if path == f"/api/v1/applications/{app_uuid}/logs":
                return _AdmissionResponse({"logs": ""})
        if host == "coolify-c.invalid":
            service = pre["replica"]
            app_uuid = "app-c-uuid"
            node = service["node"]
            if path == "/api/v1/services":
                return _AdmissionResponse([{"uuid": service["service_uuid"], "name": node, "status": "degraded:unhealthy"}])
            if path == f"/api/v1/services/{service['service_uuid']}":
                return _AdmissionResponse({
                    "service": {
                        "uuid": service["service_uuid"],
                        "name": node,
                        "status": "degraded:unhealthy",
                        "applications": [{"uuid": app_uuid, "name": "mainnetc-super1"}],
                    }
                })
            if path == f"/api/v1/services/{service['service_uuid']}/applications":
                return _AdmissionResponse([{"uuid": app_uuid, "name": "mainnetc-super1"}])
            if path == f"/api/v1/services/{service['service_uuid']}/logs":
                query_values = parse_qs(query)
                if query_values.get("sub_service_name") == ["mainnetc-super1"]:
                    return _AdmissionResponse({"logs": "besu | ERROR | static nodes parse failed"})
            if path == f"/api/v1/applications/{app_uuid}/logs":
                return _AdmissionResponse({"logs": ""})
        return _AdmissionResponse({"message": "not found"}, status=404)


def test_quorum_runtime_diagnostic_collects_redacted_get_only_logs(tmp_path: Path) -> None:
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    failed = execute_validator_quorum_recovery_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=_QuorumRecoveryOpener(release, never_healthy=True),
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-quorum-recovery-failed-for-diagnostic"),
    )
    assert failed["status"] == "failed"
    opener = _QuorumDiagnosticOpener(release)
    result = diagnose_validator_quorum_runtime(
        paths,
        private_state,
        Path(failed["evidence"]["path"]),
        opener=opener,
        operation=_operation("validator-quorum-runtime-diagnostic"),
    )
    assert result["summary"]["diagnostic_complete"] is True
    assert result["summary"]["live_mutation_performed"] is False
    assert result["policy"]["allowed_http_methods"] == ["GET"]
    assert len(result["targets"]) == 2
    rendered = json.dumps(result, sort_keys=True)
    assert TOKEN_A not in rendered
    assert TOKEN_C not in rendered
    assert "<redacted>" in rendered
    assert "Unknown option --static-nodes-file" in rendered
    assert any(
        "/logs?sub_service_name=mainneta-super1&lines=500&show_timestamps=true" in endpoint
        for _, endpoint in opener.requests
    )
    assert any(target["applications_index"]["ok"] for target in result["targets"])
    assert all(request[1].startswith("/api/v1/") for request in opener.requests)
    stored = json.loads(Path(result["diagnostic_artifact"]["path"]).read_text("utf-8"))
    assert stored["kind"] == "main_computer.mother.deployment_validator_quorum_runtime_diagnostic.v1"
    assert stored["source_evidence"]["sha256"] == failed["evidence"]["sha256"]


def test_quorum_runtime_diagnostic_rejects_passing_evidence(tmp_path: Path) -> None:
    paths, private_state, _, _, release, release_path, release_digest = _fixture(tmp_path)
    passed = execute_validator_quorum_recovery_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=_QuorumRecoveryOpener(release),
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-quorum-recovery-pass-for-diagnostic"),
    )
    with pytest.raises(MotherDeploymentValidatorQuorumRecoveryError) as caught:
        diagnose_validator_quorum_runtime(
            paths,
            private_state,
            Path(passed["evidence"]["path"]),
            opener=_QuorumDiagnosticOpener(release),
            operation=_operation("validator-quorum-runtime-diagnostic-reject-pass"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_QUORUM_DIAGNOSTIC_NOT_REQUIRED"


def test_cli_dispatches_quorum_runtime_diagnostic(tmp_path: Path, capsys, monkeypatch) -> None:
    paths, _, _, transaction_path, transaction_digest = _transaction_fixture(tmp_path)
    runtime_root = paths.root.parent
    release = build_validator_quorum_recovery_release(
        paths,
        mother_deploy.read_private_state(paths, operation=_operation("diagnostic-cli-load")),
        transaction_path,
        acknowledged_transaction_sha256=transaction_digest,
        selected_nodes=("mainnetc-super1",),
    )
    release_path, release_digest = write_validator_quorum_recovery_release(
        paths, release, operation=_operation("diagnostic-cli-release")
    )
    failed = execute_validator_quorum_recovery_release(
        paths,
        mother_deploy.read_private_state(paths, operation=_operation("diagnostic-cli-load-2")),
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=_QuorumRecoveryOpener(release, never_healthy=True),
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("diagnostic-cli-failed"),
    )
    monkeypatch.setattr(
        mother_deploy,
        "diagnose_validator_quorum_runtime",
        lambda *args, **kwargs: {"summary": {"diagnostic_complete": True}, "diagnostic_artifact": {"path": "x", "sha256": "a" * 64}},
    )
    code = mother_deploy.main([
        "diagnose-validator-quorum-runtime",
        "--runtime-state-root", str(runtime_root),
        "--evidence", failed["evidence"]["path"],
    ])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["diagnostic_complete"] is True
