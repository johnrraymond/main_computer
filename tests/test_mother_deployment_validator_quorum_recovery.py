from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_validator_quorum_recovery import (
    MotherDeploymentValidatorQuorumRecoveryError,
    build_validator_quorum_recovery_release,
    execute_validator_quorum_recovery_release,
    inspect_validator_quorum_recovery_release,
    verify_validator_quorum_recovery_evidence,
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
    def __init__(self, release: dict[str, Any], *, bad_a_compose: bool = False, fail_c_patch: bool = False) -> None:
        self.release = release
        self.bad_a_compose = bad_a_compose
        self.fail_c_patch = fail_c_patch
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
                    status = "starting:unhealthy" if self.c_polls == 1 else "running:healthy"
                return _AdmissionResponse([{"uuid": service["service_uuid"], "name": service["node"], "status": status}])
            if method == "GET" and path == f"/api/v1/services/{service['service_uuid']}":
                compose = (
                    plan["replica_readiness_compose"]["canonical_text"]
                    if self.c_patched
                    else service["compose"]["canonical_text"]
                )
                return _AdmissionResponse({"service": {"uuid": service["service_uuid"], "name": service["node"], "docker_compose_raw": compose}})
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
                    status = "starting:unhealthy" if self.a_polls == 1 else "running:healthy"
                return _AdmissionResponse([{"uuid": service["service_uuid"], "name": service["node"], "status": status}])
            if method == "GET" and path == f"/api/v1/services/{service['service_uuid']}":
                if self.a_patched:
                    compose = plan["initial_quorum_compose"]["canonical_text"]
                elif self.bad_a_compose:
                    compose = service["compose"]["canonical_text"].replace("main_computer.mother.node: mainneta-super1", "main_computer.mother.node: wrong-node", 1)
                else:
                    compose = service["compose"]["canonical_text"]
                return _AdmissionResponse({"service": {"uuid": service["service_uuid"], "name": service["node"], "docker_compose_raw": compose}})
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
    assert [item["controller_id"] for item in plan["mutations"]] == ["coolify-c", "coolify-c", "coolify-a", "coolify-a"]
    assert release["authority"]["validator_vote_authorized"] is False
    assert release["policy"]["restart_all_validators"] is True
    c_compose = plan["replica_readiness_compose"]["canonical_text"]
    a_compose = plan["initial_quorum_compose"]["canonical_text"]
    assert "--static-nodes-file=/config/static-nodes.json" in c_compose
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
    writes = [(host, method) for host, method, _ in opener.requests if method == "PATCH"]
    assert writes == [("coolify-c.invalid", "PATCH"), ("coolify-a.invalid", "PATCH")]
    verified = verify_validator_quorum_recovery_evidence(
        paths, private_state, Path(result["evidence"]["path"]), selected_nodes=("mainnetc-super1",)
    )
    assert verified["quorum_recovered"] is True
    assert verified["validator_vote_performed"] is False


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
