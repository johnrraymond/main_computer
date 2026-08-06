from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_soft_replica_sync import execute_soft_replica_sync_release
from tools.mother.common.deployment_validator_admission import (
    MotherDeploymentValidatorAdmissionError,
    build_validator_admission_transaction,
    verify_validator_admission_transaction,
    write_validator_admission_transaction,
)
from tools.mother.common.deployment_validator_admission_release import (
    MotherDeploymentValidatorAdmissionReleaseError,
    _admission_script,
    _historical_order_sensitive_admission_script,
    build_validator_admission_release,
    verify_validator_admission_release,
    write_validator_admission_release,
)
from tools.mother.common.deployment_validator_admission_executor import (
    MotherDeploymentValidatorAdmissionExecutorError,
    execute_validator_admission_release,
    inspect_validator_admission_release,
    verify_validator_admission_evidence,
)
from tests.test_mother_deployment_executor import TOKEN_A, TOKEN_C, _operation
from tests.test_mother_deployment_soft_replica_sync import _SyncOpener, _fixture as _sync_fixture


def _evidence(tmp_path: Path):
    paths, private_state, _, _, release, release_path, release_digest = _sync_fixture(tmp_path)
    result = execute_soft_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=_SyncOpener(release),
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-admission-sync-evidence"),
    )
    assert result["status"] == "pass"
    return paths, private_state, Path(result["evidence"]["path"])


def test_admission_transaction_compiles_exact_vote_without_authority(tmp_path: Path) -> None:
    paths, private_state, evidence_path = _evidence(tmp_path)
    transaction = build_validator_admission_transaction(
        paths,
        private_state,
        evidence_path,
        selected_nodes=("mainnetc-super1",),
    )
    admission = transaction["admission"]
    assert transaction["staged_scope"] == "stage-validator-admission-without-casting-vote"
    assert transaction["current_chain"]["replica_synchronized"] is True
    assert len(admission["current_validator_set"]) == 1
    assert len(admission["desired_validator_set"]) == 2
    assert admission["desired_validator_set"][0] == admission["current_validator_set"][0]
    assert admission["desired_validator_set"][1] == admission["candidate_validator_address"]
    assert admission["rpc_request"] == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "qbft_proposeValidatorVote",
        "params": [admission["candidate_validator_address"], True],
    }
    assert admission["vote_cast"] is False
    assert transaction["authority"]["validator_vote_authorized"] is False
    assert transaction["authority"]["validator_activation_authorized"] is False
    assert transaction["policy"]["network_access_performed"] is False
    assert transaction["policy"]["manual_ssh_required"] is False
    assert transaction["policy"]["public_http_endpoint_created"] is False
    assert transaction["summary"]["persisted_secret_value_count"] == 0
    rendered = json.dumps(transaction, sort_keys=True)
    state = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    network = state["networks"]["mainnet"]
    for node in ("mainneta-super1", "mainnetc-super1"):
        assert network["validators"][node]["private_key"] not in rendered
        assert network["node_seed_material"][node]["wallets"]["hub_admin"]["private_key"] not in rendered
    assert "api_token" not in rendered


def test_admission_transaction_persists_and_verifies(tmp_path: Path) -> None:
    paths, private_state, evidence_path = _evidence(tmp_path)
    transaction = build_validator_admission_transaction(paths, private_state, evidence_path)
    path, digest = write_validator_admission_transaction(
        paths,
        transaction,
        operation=_operation("validator-admission-write"),
    )
    verified = verify_validator_admission_transaction(
        paths,
        private_state,
        path,
        selected_nodes=("mainnetc-super1",),
    )
    assert verified["clean"] is True
    assert verified["validator_admission_transaction_sha256"] == digest
    assert verified["rpc_method"] == "qbft_proposeValidatorVote"
    assert verified["current_validator_set"] == transaction["admission"]["current_validator_set"]
    assert verified["desired_validator_set"] == transaction["admission"]["desired_validator_set"]
    assert verified["validator_vote_authorized"] is False
    assert verified["live_execution_authorized"] is False
    assert verified["network_access_performed"] is False


def test_admission_transaction_rejects_tamper(tmp_path: Path) -> None:
    paths, private_state, evidence_path = _evidence(tmp_path)
    transaction = build_validator_admission_transaction(paths, private_state, evidence_path)
    path, _ = write_validator_admission_transaction(
        paths,
        transaction,
        operation=_operation("validator-admission-tamper"),
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    document["admission"]["rpc_request"]["params"][1] = False
    path.write_bytes(canonical_json(document))
    with pytest.raises(MotherDeploymentValidatorAdmissionError) as caught:
        verify_validator_admission_transaction(paths, private_state, path)
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_ADMISSION_TRANSACTION_INVALID"


def test_admission_transaction_rejects_wrong_selection(tmp_path: Path) -> None:
    paths, private_state, evidence_path = _evidence(tmp_path)
    with pytest.raises(MotherDeploymentValidatorAdmissionError) as caught:
        build_validator_admission_transaction(
            paths,
            private_state,
            evidence_path,
            selected_nodes=("mainneta-super1",),
        )
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_ADMISSION_SELECTION_MISMATCH"


def test_cli_stages_and_verifies_validator_admission(tmp_path: Path, capsys) -> None:
    paths, _, evidence_path = _evidence(tmp_path)
    runtime_root = paths.root.parent
    code = mother_deploy.main([
        "stage-validator-admission",
        "--runtime-state-root", str(runtime_root),
        "--sync-evidence", str(evidence_path),
        "--node", "mainnetc-super1",
        "--write-transaction",
    ])
    assert code == 0
    staged = json.loads(capsys.readouterr().out)
    assert staged["summary"]["validator_vote_authorized"] is False
    assert staged["summary"]["next_phase"] == "release-and-execute-validator-admission"
    transaction_path = staged["transaction_artifact"]["path"]

    code = mother_deploy.main([
        "verify-validator-admission-transaction",
        "--runtime-state-root", str(runtime_root),
        "--transaction", transaction_path,
        "--node", "mainnetc-super1",
    ])
    assert code == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True
    assert verified["validator_vote_authorized"] is False
    assert verified["next_phase"] == "release-and-execute-validator-admission"


class _AdmissionResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def close(self) -> None:
        return None


def _transaction_fixture(tmp_path: Path):
    paths, private_state, evidence_path = _evidence(tmp_path)
    transaction = build_validator_admission_transaction(
        paths,
        private_state,
        evidence_path,
        selected_nodes=("mainnetc-super1",),
    )
    transaction_path, transaction_digest = write_validator_admission_transaction(
        paths,
        transaction,
        operation=_operation("validator-admission-transaction-fixture"),
    )
    return paths, private_state, transaction, transaction_path, transaction_digest


def _release_fixture(tmp_path: Path):
    paths, private_state, transaction, transaction_path, transaction_digest = _transaction_fixture(tmp_path)
    release = build_validator_admission_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=transaction_digest,
        selected_nodes=("mainnetc-super1",),
    )
    release_path, release_digest = write_validator_admission_release(
        paths,
        release,
        operation=_operation("validator-admission-release-fixture"),
    )
    return paths, private_state, transaction, transaction_path, transaction_digest, release, release_path, release_digest


def _failed_admission_evidence_fixture(
    tmp_path: Path,
    *,
    tamper_body_sha: bool = False,
):
    (
        paths,
        private_state,
        transaction,
        transaction_path,
        transaction_digest,
        release,
        release_path,
        release_digest,
    ) = _release_fixture(tmp_path)
    mutation = release["execution_plan"]["mutations"][0]
    body_sha = "0" * 64 if tamper_body_sha else mutation["body_sha256"]
    evidence = {
        "kind": "main_computer.mother.deployment_validator_admission_evidence.v1",
        "schema_version": 1,
        "status": "failed",
        "network": "mainnet",
        "service_uuid": release["execution_plan"]["service_uuid"],
        "validator_admission_transaction_sha256": transaction_digest,
        "release": {
            "locator": release_path.relative_to(paths.root).as_posix(),
            "sha256": release_digest,
        },
        "failure": {
            "code": "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_HEALTHY",
            "message": "guardian remained unhealthy after two acknowledged mutations",
        },
        "mutation_receipts": [
            {
                "mutation_id": "mainneta-super1.install-validator-admission-guardian",
                "body_sha256": body_sha,
                "live_write_acknowledged": True,
                "status": "succeeded",
            },
            {
                "mutation_id": "mainneta-super1.deploy-validator-admission-guardian",
                "body_sha256": None,
                "live_write_acknowledged": True,
                "status": "succeeded",
            },
        ],
        "summary": {
            "live_mutation_performed": True,
            "succeeded_mutation_count": 2,
            "failed_mutation_count": 0,
        },
    }
    evidence_root = paths.root / "evidence" / "deployment-validator-admission"
    evidence_root.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_root / "failed.json"
    evidence_path.write_bytes(canonical_json(evidence))
    return (
        paths,
        private_state,
        transaction,
        transaction_path,
        transaction_digest,
        release,
        release_path,
        release_digest,
        evidence_path,
    )


class _AdmissionOpener:
    def __init__(
        self,
        release: dict[str, Any],
        *,
        c_healthy: bool = True,
        a_statuses: list[str] | None = None,
        fail_patch: bool = False,
        recovery_mode: bool = False,
        recovery_compose_override: str | None = None,
        c_besu_component_healthy: bool = False,
    ) -> None:
        self.release = release
        self.c_healthy = c_healthy
        self.c_besu_component_healthy = c_besu_component_healthy
        self.recovery_mode = recovery_mode
        self.recovery_compose_override = recovery_compose_override
        default_statuses = ["degraded:unhealthy", "running:healthy"] if recovery_mode else ["running:healthy"]
        self.a_statuses = list(a_statuses or default_statuses)
        self.fail_patch = fail_patch
        self.patched = False
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
        initial = self.release["initial_chain_precondition"]
        replica = self.release["replica_precondition"]
        plan = self.release["execution_plan"]
        if host == "coolify-a.invalid":
            if method == "GET" and path == "/api/v1/services":
                status = self.a_statuses.pop(0) if len(self.a_statuses) > 1 else self.a_statuses[0]
                return _AdmissionResponse([{
                    "uuid": initial["service_uuid"],
                    "name": initial["node"],
                    "status": status,
                }])
            if method == "GET" and path == f"/api/v1/services/{initial['service_uuid']}":
                if self.patched:
                    compose = plan["admission_compose"]["canonical_text"]
                elif self.recovery_mode:
                    compose = self.recovery_compose_override or self.release["known_failed_guardian_recovery"]["broken_admission_compose"]["canonical_text"]
                else:
                    compose = initial["proof_compose"]["canonical_text"]
                return _AdmissionResponse({
                    "service": {
                        "uuid": initial["service_uuid"],
                        "name": initial["node"],
                        "docker_compose_raw": compose,
                    }
                })
            if method == "PATCH" and path == f"/api/v1/services/{initial['service_uuid']}":
                if self.fail_patch:
                    return _AdmissionResponse({"message": "rejected"}, status=500)
                body = json.loads(request.data.decode("utf-8"))
                compose = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
                assert compose == plan["admission_compose"]["canonical_text"]
                self.patched = True
                return _AdmissionResponse({"uuid": initial["service_uuid"]}, status=200)
            if method == "GET" and path == "/api/v1/deploy":
                assert self.patched
                return _AdmissionResponse({"deployment_uuid": "validator-admission"}, status=200)
        if host == "coolify-c.invalid":
            assert method == "GET"
            if path == "/api/v1/services":
                return _AdmissionResponse([{
                    "uuid": replica["service_uuid"],
                    "name": replica["node"],
                    "status": "running:healthy" if self.c_healthy else "running:unhealthy",
                }])
            if path == f"/api/v1/services/{replica['service_uuid']}":
                service = {
                    "uuid": replica["service_uuid"],
                    "name": replica["node"],
                    "docker_compose_raw": replica["proof_compose"]["canonical_text"],
                }
                if self.c_besu_component_healthy:
                    service["applications"] = [
                        {
                            "uuid": "replica-init",
                            "name": "mother-replica-init",
                            "image": "alpine:3.20",
                            "status": "exited",
                        },
                        {
                            "uuid": "replica-besu",
                            "name": replica["node"],
                            "image": "hyperledger/besu:latest",
                            "status": "running:healthy",
                        },
                        {
                            "uuid": "replica-sync-guardian",
                            "name": "mother-replica-sync-guardian",
                            "image": "python:3.12-alpine",
                            "status": "running:unhealthy",
                        },
                    ]
                return _AdmissionResponse({"service": service})
        raise AssertionError(f"unexpected request {method} {request.full_url}")


def test_admission_release_is_exact_internal_vote_and_c_read_only(tmp_path: Path) -> None:
    paths, private_state, transaction, _, transaction_digest, release, release_path, release_digest = _release_fixture(tmp_path)
    plan = release["execution_plan"]
    assert release["transaction"]["sha256"] == transaction_digest
    assert release["authority"]["validator_vote_authorized"] is True
    assert release["authority"]["validator_activation_authorized"] is True
    assert release["replica_precondition"]["read_only"] is True
    assert plan["rpc_request"] == transaction["admission"]["rpc_request"]
    assert plan["rpc_request_sha256"] == transaction["admission"]["rpc_request_sha256"]
    assert [item["controller_id"] for item in plan["mutations"]] == ["coolify-a", "coolify-a"]
    compose = plan["admission_compose"]["canonical_text"]
    guardian = compose.split("  mother-validator-admission-guardian:", 1)[1].split("\nvolumes:\n", 1)[0]
    assert "mother-genesis-proof-guardian" not in compose
    assert "qbft_proposeValidatorVote" in compose
    assert plan["rpc_request_sha256"] in compose
    assert "ports:" not in guardian
    assert "expose:" not in guardian
    assert "traefik." not in guardian
    assert "8545:8545" not in compose
    verified = verify_validator_admission_release(
        paths,
        private_state,
        release_path,
        selected_nodes=("mainnetc-super1",),
    )
    assert verified["clean"] is True
    assert verified["validator_admission_release_sha256"] == release_digest
    assert verified["validator_vote_authorized"] is True
    assert verified["manual_ssh_required"] is False
    assert verified["public_endpoint_created"] is False


def test_admission_guardian_decodes_json_boolean_before_any_rpc() -> None:
    script = _admission_script(
        node="mainneta-super1",
        chain_id=42424240,
        genesis_sha256="1" * 64,
        initial_validator="0x" + "2" * 40,
        candidate_validator="0x" + "3" * 40,
        candidate_node_id="4" * 128,
        rpc_request_sha256="5" * 64,
    )
    prefix = script.split("PROOF =", 1)[0]
    namespace: dict[str, Any] = {}
    exec(compile(prefix, "<guardian-prefix>", "exec"), namespace)
    assert namespace["REQUEST"]["params"][1] is True
    assert "REQUEST = json.loads(" in script

    legacy = _admission_script(
        node="mainneta-super1",
        chain_id=42424240,
        genesis_sha256="1" * 64,
        initial_validator="0x" + "2" * 40,
        candidate_validator="0x" + "3" * 40,
        candidate_node_id="4" * 128,
        rpc_request_sha256="5" * 64,
        legacy_json_boolean_bug=True,
    )
    with pytest.raises(NameError):
        exec(compile(legacy.split("PROOF =", 1)[0], "<legacy-guardian-prefix>", "exec"), {})


def test_admission_release_binds_exact_known_broken_guardian_recovery(tmp_path: Path) -> None:
    _, _, _, _, _, release, _, _ = _release_fixture(tmp_path)
    recovery = release["known_failed_guardian_recovery"]
    assert recovery["allowed"] is True
    assert recovery["failure_occurs_before_rpc"] is True
    assert recovery["bug_code"] == "json-boolean-literal-in-python-source"
    broken = recovery["broken_admission_compose"]["canonical_text"]
    fixed = release["execution_plan"]["admission_compose"]["canonical_text"]
    assert "REQUEST = json.loads(" not in broken
    assert "true" in broken
    assert "REQUEST = json.loads(" in fixed
    assert broken != fixed


def test_admission_release_rejects_wrong_transaction_digest(tmp_path: Path) -> None:
    paths, private_state, _, transaction_path, _ = _transaction_fixture(tmp_path)
    with pytest.raises(MotherDeploymentValidatorAdmissionReleaseError) as caught:
        build_validator_admission_release(
            paths,
            private_state,
            transaction_path,
            acknowledged_transaction_sha256="0" * 64,
            selected_nodes=("mainnetc-super1",),
        )
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_ACKNOWLEDGEMENT_MISMATCH"


def test_admission_inspection_is_network_free(tmp_path: Path) -> None:
    paths, private_state, _, _, _, _, release_path, release_digest = _release_fixture(tmp_path)
    result = inspect_validator_admission_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
    )
    assert result["clean"] is True
    assert result["network_access_performed"] is False
    assert result["replica_node_read_only"] is True
    assert result["validator_vote_proven"] is False
    assert result["validator_activation_proven"] is False


def test_admission_executor_votes_from_a_and_proves_exact_final_set(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release, release_path, release_digest = _release_fixture(tmp_path)
    opener = _AdmissionOpener(release)
    result = execute_validator_admission_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-admission-live"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["validator_vote_proven"] is True
    assert result["summary"]["validator_activation_proven"] is True
    assert result["summary"]["final_validator_set_verified"] is True
    assert result["summary"]["replica_node_read_only"] is True
    assert result["summary"]["next_phase"] == "stage-post-admission-steady-state"
    writes = [(host, method) for host, method, _ in opener.requests if method in {"PATCH", "POST", "PUT", "DELETE"}]
    assert writes == [("coolify-a.invalid", "PATCH")]
    assert all(method == "GET" for host, method, _ in opener.requests if host == "coolify-c.invalid")
    verified = verify_validator_admission_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        selected_nodes=("mainnetc-super1",),
    )
    assert verified["validator_vote_proven"] is True
    assert verified["validator_activation_proven"] is True
    assert verified["final_validator_set"] == release["execution_plan"]["desired_validator_set"]


def test_admission_executor_accepts_unhealthy_c_aggregate_when_besu_component_is_healthy(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release, release_path, release_digest = _release_fixture(tmp_path)
    opener = _AdmissionOpener(release, c_healthy=False, c_besu_component_healthy=True)
    result = execute_validator_admission_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-admission-c-component-healthy"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["validator_vote_proven"] is True
    assert result["summary"]["validator_activation_proven"] is True
    assert result["summary"]["replica_node_running_healthy"] is False
    assert result["summary"]["replica_node_reachable_via_initial_peer"] is True
    assert result["summary"]["replica_component_health_used"] is True
    assert result["summary"]["replica_precondition_mode"] == "normal-replica-proof-compose-component-health"
    assert all(method == "GET" for host, method, _ in opener.requests if host == "coolify-c.invalid")
    assert any(
        receipt.get("component_health_mode") == "besu-application-running-healthy"
        for receipt in result["precondition_receipts"]
    )


def test_admission_executor_fails_before_mutation_when_c_unhealthy_and_consumes_release(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release, release_path, release_digest = _release_fixture(tmp_path)
    opener = _AdmissionOpener(release, c_healthy=False)
    result = execute_validator_admission_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-admission-c-unhealthy"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_SERVICE_UNHEALTHY"
    assert result["summary"]["live_mutation_performed"] is False
    assert all(method != "PATCH" for _, method, _ in opener.requests)
    with pytest.raises(MotherDeploymentValidatorAdmissionExecutorError) as caught:
        execute_validator_admission_release(
            paths,
            private_state,
            release_path,
            acknowledged_release_sha256=release_digest,
            selected_nodes=("mainnetc-super1",),
            opener=opener,
            operation=_operation("validator-admission-replay"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_ALREADY_CONSUMED"


def test_admission_executor_records_indeterminate_state_after_live_health_failure(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release, release_path, release_digest = _release_fixture(tmp_path)
    opener = _AdmissionOpener(release, a_statuses=["running:healthy", "running:unhealthy"])
    result = execute_validator_admission_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-admission-health-failure"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_HEALTHY"
    assert result["summary"]["live_mutation_performed"] is True
    assert result["summary"]["validator_vote_state"] == "indeterminate-after-live-mutation"
    assert result["summary"]["validator_activation_proven"] is False


def test_admission_executor_recovers_exact_known_broken_guardian_before_vote(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release, release_path, release_digest = _release_fixture(tmp_path)
    opener = _AdmissionOpener(release, recovery_mode=True)
    result = execute_validator_admission_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-admission-known-bug-recovery"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["known_guardian_recovery_used"] is True
    assert result["summary"]["initial_precondition_mode"] == "known-json-boolean-guardian-recovery"
    assert result["summary"]["validator_activation_proven"] is True
    assert any(
        receipt.get("precondition_mode") == "known-json-boolean-guardian-recovery"
        for receipt in result["precondition_receipts"]
    )


def test_admission_executor_rejects_unrecognized_degraded_compose_before_mutation(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release, release_path, release_digest = _release_fixture(tmp_path)
    opener = _AdmissionOpener(
        release,
        recovery_mode=True,
        recovery_compose_override=release["initial_chain_precondition"]["proof_compose"]["canonical_text"],
    )
    result = execute_validator_admission_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-admission-recovery-mismatch"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RECOVERY_COMPOSE_MISMATCH"
    assert result["summary"]["live_mutation_performed"] is False
    assert all(method != "PATCH" for _, method, _ in opener.requests)


def test_cli_releases_verifies_and_inspects_validator_admission(tmp_path: Path, capsys) -> None:
    paths, _, _, transaction_path, transaction_digest = _transaction_fixture(tmp_path)
    runtime_root = paths.root.parent
    code = mother_deploy.main([
        "release-validator-admission",
        "--runtime-state-root", str(runtime_root),
        "--transaction", str(transaction_path),
        "--acknowledge-validator-admission-transaction-sha256", transaction_digest,
        "--node", "mainnetc-super1",
        "--write-release",
    ])
    assert code == 0
    staged = json.loads(capsys.readouterr().out)
    release_path = staged["release_artifact"]["path"]
    release_digest = staged["release_artifact"]["sha256"]
    assert staged["authority"]["validator_vote_authorized"] is True

    code = mother_deploy.main([
        "verify-validator-admission-release",
        "--runtime-state-root", str(runtime_root),
        "--release", release_path,
        "--node", "mainnetc-super1",
    ])
    assert code == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True
    assert verified["validator_vote_authorized"] is True

    code = mother_deploy.main([
        "apply-validator-admission",
        "--runtime-state-root", str(runtime_root),
        "--release", release_path,
        "--acknowledge-release-sha256", release_digest,
        "--node", "mainnetc-super1",
    ])
    assert code == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["execute_requested"] is False
    assert inspected["network_access_performed"] is False
    assert inspected["manual_ssh_required"] is False
    assert inspected["public_endpoint_created"] is False


def test_admission_guardian_canonicalizes_validator_addresses_and_logs_failures() -> None:
    initial = "0x" + "c" * 40
    candidate = "0x" + "9" * 40
    script = _admission_script(
        node="mainneta-super1",
        chain_id=42424240,
        genesis_sha256="1" * 64,
        initial_validator=initial,
        candidate_validator=candidate,
        candidate_node_id="4" * 128,
        rpc_request_sha256="5" * 64,
    )
    namespace: dict[str, Any] = {}
    exec(compile(script.split("def load_proof():", 1)[0], "<guardian-validator-set>", "exec"), namespace)
    namespace["rpc"] = lambda method, params: [initial.upper(), candidate, initial]
    assert namespace["validator_set"]() == sorted([initial, candidate])
    assert "desired = sorted([INITIAL_VALIDATOR, CANDIDATE_VALIDATOR])" in script
    assert "activation_reconciled" in script
    assert "traceback.print_exc()" in script


def test_historical_order_sensitive_guardian_is_byte_exact() -> None:
    common = {
        "node": "mainneta-super1",
        "chain_id": 42424240,
        "genesis_sha256": "1" * 64,
        "initial_validator": "0x" + "c" * 40,
        "candidate_validator": "0x" + "9" * 40,
        "candidate_node_id": "4" * 128,
        "rpc_request_sha256": "5" * 64,
    }
    boolean_fixed = _historical_order_sensitive_admission_script(
        **common, legacy_json_boolean_bug=False
    )
    boolean_broken = _historical_order_sensitive_admission_script(
        **common, legacy_json_boolean_bug=True
    )
    assert hashlib.sha256(boolean_fixed.encode("utf-8")).hexdigest() == (
        "66ffb71c09e72e56445c0fe9ee61b801c898891d0adbf334e97dacaddb98a184"
    )
    assert hashlib.sha256(boolean_broken.encode("utf-8")).hexdigest() == (
        "47d22ee543672a6912144da7d7c7ea5d882d041a1b3a255d4123cb3e77f1bbf4"
    )


def test_admission_release_binds_exact_order_sensitive_guardian_recovery(tmp_path: Path) -> None:
    _, _, _, _, _, release, _, _ = _release_fixture(tmp_path)
    recovery = release["known_order_sensitive_guardian_recovery"]
    assert recovery["allowed"] is True
    assert recovery["bug_code"] == "validator-set-order-sensitive-comparison"
    assert recovery["vote_may_have_been_cast"] is True
    assert recovery["historical_guardian_lineage"] == "boolean-fix-before-order-recovery"
    broken = recovery["broken_admission_compose"]["canonical_text"]
    fixed = release["execution_plan"]["admission_compose"]["canonical_text"]
    assert "REQUEST = json.loads(" in broken
    assert "return [str(item).lower()" in broken
    assert "desired = [INITIAL_VALIDATOR, CANDIDATE_VALIDATOR]" in broken
    assert "validator already active without this release proof" in broken
    assert "activation_reconciled" not in broken
    assert "starting_validator_set" not in broken
    assert "traceback.print_exc()" not in broken
    assert "return sorted(set(" in fixed
    assert "desired = sorted([INITIAL_VALIDATOR, CANDIDATE_VALIDATOR])" in fixed
    assert "traceback.print_exc()" in fixed
    assert broken != fixed


def test_admission_executor_reconciles_exact_order_sensitive_guardian(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release, release_path, release_digest = _release_fixture(tmp_path)
    opener = _AdmissionOpener(
        release,
        recovery_mode=True,
        recovery_compose_override=release["known_order_sensitive_guardian_recovery"]["broken_admission_compose"]["canonical_text"],
    )
    result = execute_validator_admission_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-admission-order-recovery"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["known_guardian_recovery_used"] is True
    assert result["summary"]["validator_set_order_recovery_used"] is True
    assert result["summary"]["validator_admission_reconciled"] is True
    assert result["summary"]["initial_precondition_mode"] == "known-validator-set-order-recovery"
    assert result["summary"]["validator_vote_state"] == "reconciled-active-after-order-sensitive-guardian"


def test_admission_release_binds_exact_post_admission_replica_guardian_drift(tmp_path: Path) -> None:
    _, _, _, _, _, release, _, _ = _release_fixture(tmp_path)
    recovery = release["known_replica_post_admission_guardian_recovery"]
    replica = release["replica_precondition"]
    assert recovery["allowed"] is True
    assert recovery["cause_code"] == "sole-validator-sync-guardian-invalidated-by-candidate-activation"
    assert recovery["requires_initial_precondition_mode"] == "known-validator-set-order-recovery"
    assert recovery["read_only"] is True
    assert recovery["stale_replica_compose"]["canonical_text"] == replica["proof_compose"]["canonical_text"]
    assert recovery["expected_pre_admission_validator_set"] == release["execution_plan"]["current_validator_set"]
    assert recovery["expected_post_admission_validator_set"] == release["execution_plan"]["desired_validator_set"]


def test_admission_executor_accepts_exact_c_guardian_drift_only_during_order_recovery(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release, release_path, release_digest = _release_fixture(tmp_path)
    opener = _AdmissionOpener(
        release,
        c_healthy=False,
        recovery_mode=True,
        recovery_compose_override=release["known_order_sensitive_guardian_recovery"]["broken_admission_compose"]["canonical_text"],
    )
    result = execute_validator_admission_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-admission-replica-guardian-drift"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["validator_activation_proven"] is True
    assert result["summary"]["replica_node_running_healthy"] is False
    assert result["summary"]["replica_node_reachable_via_initial_peer"] is True
    assert result["summary"]["replica_guardian_drift_recovery_used"] is True
    assert result["summary"]["replica_precondition_mode"] == "known-post-admission-replica-guardian-drift"
    assert any(
        receipt.get("precondition_mode") == "known-post-admission-replica-guardian-drift"
        for receipt in result["precondition_receipts"]
    )


def test_admission_release_binds_exact_failed_release_evidence(tmp_path: Path) -> None:
    (
        paths,
        private_state,
        _,
        transaction_path,
        transaction_digest,
        prior_release,
        _,
        _,
        failed_evidence_path,
    ) = _failed_admission_evidence_fixture(tmp_path)
    release = build_validator_admission_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=transaction_digest,
        selected_nodes=("mainnetc-super1",),
        failed_evidence_path=failed_evidence_path,
    )
    recovery = release["exact_failed_release_recovery"]
    assert recovery["allowed"] is True
    assert recovery["cause_code"] == "exact-prior-failed-release-post-mutation-unhealthy"
    assert recovery["failed_admission_compose"]["body_sha256"] == (
        prior_release["execution_plan"]["mutations"][0]["body_sha256"]
    )
    assert recovery["failed_admission_compose"]["canonical_text"] == (
        prior_release["execution_plan"]["admission_compose"]["canonical_text"]
    )
    release_path, _ = write_validator_admission_release(
        paths,
        release,
        operation=_operation("validator-admission-exact-failed-release"),
    )
    verified = verify_validator_admission_release(
        paths,
        private_state,
        release_path,
        selected_nodes=("mainnetc-super1",),
    )
    assert verified["clean"] is True
    assert verified["exact_failed_release_recovery_allowed"] is True


def test_admission_release_rejects_failed_evidence_body_mismatch(tmp_path: Path) -> None:
    (
        paths,
        private_state,
        _,
        transaction_path,
        transaction_digest,
        _,
        _,
        _,
        failed_evidence_path,
    ) = _failed_admission_evidence_fixture(tmp_path, tamper_body_sha=True)
    with pytest.raises(MotherDeploymentValidatorAdmissionReleaseError) as caught:
        build_validator_admission_release(
            paths,
            private_state,
            transaction_path,
            acknowledged_transaction_sha256=transaction_digest,
            selected_nodes=("mainnetc-super1",),
            failed_evidence_path=failed_evidence_path,
        )
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_FAILED_EVIDENCE_INVALID"


def test_admission_executor_reconciles_exact_failed_release_compose(tmp_path: Path) -> None:
    (
        paths,
        private_state,
        _,
        transaction_path,
        transaction_digest,
        prior_release,
        _,
        _,
        failed_evidence_path,
    ) = _failed_admission_evidence_fixture(tmp_path)
    release = build_validator_admission_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=transaction_digest,
        selected_nodes=("mainnetc-super1",),
        failed_evidence_path=failed_evidence_path,
    )
    release_path, release_digest = write_validator_admission_release(
        paths,
        release,
        operation=_operation("validator-admission-exact-failed-release-executor"),
    )
    opener = _AdmissionOpener(
        release,
        c_healthy=False,
        recovery_mode=True,
        recovery_compose_override=(
            prior_release["execution_plan"]["admission_compose"]["canonical_text"]
        ),
    )
    result = execute_validator_admission_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-admission-exact-failed-release-live"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["initial_precondition_mode"] == "known-exact-failed-release-recovery"
    assert result["summary"]["known_guardian_recovery_used"] is True
    assert result["summary"]["validator_set_order_recovery_used"] is True
    assert result["summary"]["validator_admission_reconciled"] is True
    assert result["summary"]["replica_guardian_drift_recovery_used"] is True
