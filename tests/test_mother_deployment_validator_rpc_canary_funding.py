from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_validator_rpc_canary_funding import (
    MotherDeploymentValidatorRpcCanaryFundingError,
    _deployment_uuid,
    build_validator_rpc_canary_funding_release,
    build_validator_rpc_canary_funding_transaction,
    execute_validator_rpc_canary_funding_release,
    inspect_validator_rpc_canary_funding_release,
    verify_validator_rpc_canary_funding_evidence,
    verify_validator_rpc_canary_funding_release,
    verify_validator_rpc_canary_funding_transaction,
    write_validator_rpc_canary_funding_release,
    write_validator_rpc_canary_funding_transaction,
)
from tests.test_mother_deployment_executor import TOKEN_A, TOKEN_C, _operation
from tests.test_mother_deployment_validator_admission import _AdmissionResponse
from tests.test_mother_deployment_validator_rpc_canary import _fixture


def _funding_fixture(tmp_path: Path, monkeypatch):
    (
        paths,
        private_state,
        _soak_path,
        _soak,
        _identity,
        _canary,
        canary_path,
        _canary_digest,
    ) = _fixture(tmp_path, monkeypatch)
    funding = build_validator_rpc_canary_funding_transaction(
        paths,
        private_state,
        canary_path,
        operation=_operation("validator-rpc-canary-funding"),
    )
    funding_path, funding_digest = write_validator_rpc_canary_funding_transaction(
        paths,
        funding,
        operation=_operation("validator-rpc-canary-funding-write"),
    )
    return paths, private_state, canary_path, funding, funding_path, funding_digest


def test_funding_compiler_binds_exact_cap_and_genesis_captain(tmp_path: Path, monkeypatch) -> None:
    _, _, _, funding, _, _ = _funding_fixture(tmp_path, monkeypatch)
    assert funding["funding_source"]["role"] == "captain"
    assert funding["funding_source"]["genesis_allocated"] is True
    assert funding["funding_source"]["private_key_material_in_transaction"] is False
    assert funding["destination"]["allowed_pre_execution_balances_wei"] == [
        0,
        742_000_000_000_000,
    ]
    policy = funding["funding_policy"]
    assert policy["transfer_value_wei"] == 742_000_000_000_000
    assert policy["transfer_value_cap_wei"] == 742_000_000_000_000
    assert policy["funding_transaction_max_fee_wei"] == 42_000_000_000_000
    assert policy["source_maximum_total_debit_wei"] == 784_000_000_000_000
    assert policy["cross_validator_receipt_verification_required_when_new_transfer"] is True
    assert funding["coolify_transport"] == {
        "resource_api": "services",
        "create_endpoint": "/api/v1/services",
        "deprecated_application_create_endpoint_authorized": False,
        "compose_encoding": "base64",
        "environment_uuid_resolution": "read-only-exact-name-before-create",
        "deployment_result_endpoint_template": "/api/v1/deployments/{deployment_uuid}",
        "service_log_endpoints_authorized": False,
    }
    assert policy["destination_zero_or_exact_balance_precondition_required"] is True
    assert policy["idempotent_exact_balance_reconciliation_supported"] is True
    assert policy["cross_validator_balance_verification_required"] is True
    assert funding["schema_version"] == 4
    assert funding["authority"]["funding_authorized"] is False
    assert funding["authority"]["live_execution_authorized"] is False
    assert funding["summary"]["validator_mutation_count"] == 0
    assert funding["summary"]["validator_restart_count"] == 0
    rendered = json.dumps(funding)
    assert "ports:" not in rendered
    assert "traefik." not in rendered
    assert "private_key" not in rendered or "private_key_material_in_transaction" in rendered


def test_funding_transaction_persists_and_rebuild_verifies(tmp_path: Path, monkeypatch) -> None:
    paths, private_state, _, _, funding_path, funding_digest = _funding_fixture(
        tmp_path, monkeypatch
    )
    verified = verify_validator_rpc_canary_funding_transaction(
        paths,
        private_state,
        funding_path,
        operation=_operation("validator-rpc-canary-funding-verify"),
    )
    assert verified["clean"] is True
    assert verified["transaction_sha256"] == funding_digest
    assert verified["transfer_value_wei"] == 742_000_000_000_000
    assert verified["source_maximum_total_debit_wei"] == 784_000_000_000_000
    assert verified["validator_mutation_count"] == 0
    assert verified["next_phase"] == "validator-rpc-canary-funding-release-not-yet-authorized"


def test_deployment_uuid_accepts_one_nested_uuid_when_resource_binding_differs() -> None:
    payload = {
        "data": {
            "deployments": [
                {
                    "resource_uuid": "compose-child-service-uuid",
                    "deployment_uuid": "deployment-a-uuid",
                }
            ]
        }
    }
    assert _deployment_uuid(payload, "fund-a-uuid") == "deployment-a-uuid"


def test_funding_verifier_rejects_tampered_cap(tmp_path: Path, monkeypatch) -> None:
    paths, private_state, _, funding, funding_path, _ = _funding_fixture(
        tmp_path, monkeypatch
    )
    tampered = dict(funding)
    tampered["funding_policy"] = dict(funding["funding_policy"])
    tampered["funding_policy"]["transfer_value_cap_wei"] += 1
    tampered["validator_rpc_canary_funding_transaction_sha256"] = __import__(
        "hashlib"
    ).sha256(
        canonical_json(
            {
                key: value
                for key, value in tampered.items()
                if key != "validator_rpc_canary_funding_transaction_sha256"
            }
        )
    ).hexdigest()
    funding_path.write_bytes(canonical_json(tampered))
    with pytest.raises(MotherDeploymentValidatorRpcCanaryFundingError):
        verify_validator_rpc_canary_funding_transaction(
            paths,
            private_state,
            funding_path,
            operation=_operation("validator-rpc-canary-funding-tamper"),
        )


def test_funding_cli_exposes_stage_and_verify(capsys) -> None:
    with pytest.raises(SystemExit) as stage_exit:
        mother_deploy.main(["stage-validator-rpc-canary-funding-transaction", "--help"])
    assert stage_exit.value.code == 0
    assert "--canary-transaction" in capsys.readouterr().out

    with pytest.raises(SystemExit) as verify_exit:
        mother_deploy.main(["verify-validator-rpc-canary-funding-transaction", "--help"])
    assert verify_exit.value.code == 0
    assert "--transaction" in capsys.readouterr().out


def _release_fixture(tmp_path: Path, monkeypatch):
    paths, private_state, _, funding, funding_path, funding_digest = _funding_fixture(
        tmp_path, monkeypatch
    )
    release = build_validator_rpc_canary_funding_release(
        paths,
        private_state,
        funding_path,
        acknowledged_transaction_sha256=funding_digest,
        operation=_operation("validator-rpc-canary-funding-release"),
    )
    release_path, release_digest = write_validator_rpc_canary_funding_release(
        paths,
        release,
        operation=_operation("validator-rpc-canary-funding-release-write"),
    )
    return (
        paths,
        private_state,
        funding,
        funding_path,
        funding_digest,
        release,
        release_path,
        release_digest,
    )


class _FundingOpener:
    tx_hash = "0x" + "ab" * 32

    def __init__(
        self,
        destination: str,
        *,
        bad_c_balance: bool = False,
        already_funded: bool = False,
    ) -> None:
        self.destination = destination
        self.bad_c_balance = bad_c_balance
        self.already_funded = already_funded
        self.requests: list[tuple[str, str, str]] = []
        self.a_deleted = False
        self.c_deleted = False

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        method = request.get_method()
        path = parsed.path
        query = parsed.query
        self.requests.append((host, method, path + (f"?{query}" if query else "")))
        assert timeout > 0
        expected_token = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"

        if host == "coolify-a.invalid":
            if method == "GET" and path == "/api/v1/projects/project-a/environments":
                return _AdmissionResponse({"environments": [{"name": "mainnet", "uuid": "mainnet-env-a"}]})
            if method == "POST" and path == "/api/v1/services":
                body = json.loads(request.data.decode("utf-8"))
                assert body["name"] == "mainnet-canary1-fund-a"
                compose = base64.b64decode(body["docker_compose_raw"], validate=True).decode("utf-8")
                assert "mainnet-canary1-fund-a:" in compose
                assert "already-funded" in compose
                assert body["instant_deploy"] is False
                assert body["environment_name"] == "mainnet"
                assert body["environment_uuid"] == "mainnet-env-a"
                assert "connect_to_docker_network" not in body
                return _AdmissionResponse({"uuid": "fund-a-uuid"}, status=201)
            if method == "POST" and path == "/api/v1/services/fund-a-uuid/envs":
                body = json.loads(request.data.decode("utf-8"))
                assert body["key"] == "MC_MOTHER_CAPTAIN_PRIVATE_KEY"
                assert isinstance(body["value"], str) and body["value"].startswith("0x")
                assert body["is_shown_once"] is True
                return _AdmissionResponse({"uuid": "env-a-uuid"}, status=201)
            if method == "GET" and path == "/api/v1/deploy":
                assert parsed.query == "uuid=fund-a-uuid&force=false"
                return _AdmissionResponse(
                    {"deployments": [{"resource_uuid": "fund-a-uuid", "deployment_uuid": "deployment-a-uuid"}]}
                )
            if method == "GET" and path == "/api/v1/deployments/deployment-a-uuid":
                if self.already_funded:
                    result = {
                        "mode": "already-funded",
                        "transaction_hash": None,
                        "destination": self.destination,
                        "balance_wei": "742000000000000",
                    }
                else:
                    result = {"transactionHash": self.tx_hash}
                return _AdmissionResponse({
                    "deployment_uuid": "deployment-a-uuid",
                    "status": "finished",
                    "logs": (
                        "MOTHER_VALIDATOR_RPC_CANARY_FUNDING_A_RESULT="
                        + json.dumps(result)
                    ),
                })
            if method == "DELETE" and path == "/api/v1/services/fund-a-uuid":
                self.a_deleted = True
                return _AdmissionResponse({"message": "Service deleted."})

        if host == "coolify-c.invalid":
            if method == "GET" and path == "/api/v1/projects/project-c/environments":
                return _AdmissionResponse({"data": [{"name": "mainnet", "uuid": "mainnet-env-c"}]})
            if method == "POST" and path == "/api/v1/services":
                body = json.loads(request.data.decode("utf-8"))
                assert body["name"] == "mainnet-canary1-fund-c"
                compose = base64.b64decode(body["docker_compose_raw"], validate=True).decode("utf-8")
                assert "mainnet-canary1-fund-c:" in compose
                assert "MC_MOTHER_CANARY_FUNDING_MODE" in compose
                assert body["environment_uuid"] == "mainnet-env-c"
                return _AdmissionResponse({"uuid": "fund-c-uuid"}, status=201)
            if method == "PATCH" and path == "/api/v1/services/fund-c-uuid/envs/bulk":
                body = json.loads(request.data.decode("utf-8"))
                values = {item["key"]: item["value"] for item in body["data"]}
                expected_mode = "already-funded" if self.already_funded else "funded"
                assert values["MC_MOTHER_CANARY_FUNDING_MODE"] == expected_mode
                assert values["MC_MOTHER_CANARY_FUNDING_TX_HASH"] == (
                    "" if self.already_funded else self.tx_hash
                )
                return _AdmissionResponse([{"uuid": "env-c-uuid"}], status=201)
            if method == "GET" and path == "/api/v1/deploy":
                assert parsed.query == "uuid=fund-c-uuid&force=false"
                return _AdmissionResponse(
                    {"deployments": [{"resource_uuid": "fund-c-uuid", "deployment_uuid": "deployment-c-uuid"}]}
                )
            if method == "GET" and path == "/api/v1/deployments/deployment-c-uuid":
                balance = "1" if self.bad_c_balance else "742000000000000"
                mode = "already-funded" if self.already_funded else "funded"
                return _AdmissionResponse({
                    "deployment_uuid": "deployment-c-uuid",
                    "status": "finished",
                    "logs": (
                        "MOTHER_VALIDATOR_RPC_CANARY_FUNDING_C_RESULT="
                        + json.dumps({
                            "mode": mode,
                            "transaction_hash": None if self.already_funded else self.tx_hash,
                            "destination": self.destination,
                            "balance_wei": balance,
                            "receipt_verified": not self.already_funded,
                        })
                    ),
                })
            if method == "DELETE" and path == "/api/v1/services/fund-c-uuid":
                self.c_deleted = True
                return _AdmissionResponse({"message": "Service deleted."})

        raise AssertionError(f"unexpected request {method} {request.full_url}")

def test_funding_release_is_one_use_exact_cap_and_inspect_is_offline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        _,
        _,
        release,
        release_path,
        release_digest,
    ) = _release_fixture(tmp_path, monkeypatch)
    assert release["authority"]["requested_use_limit"] == 1
    assert release["authority"]["funding_authorized"] is True
    assert release["authority"]["funding_value_cap_wei"] == 742_000_000_000_000
    assert release["authority"]["validator_mutation_authorized"] is False
    assert release["policy"]["canary_execution_authorized"] is False
    verified = verify_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        operation=_operation("validator-rpc-canary-funding-release-verify"),
    )
    assert verified["clean"] is True
    assert verified["release_sha256"] == release_digest
    inspected = inspect_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        operation=_operation("validator-rpc-canary-funding-release-inspect"),
    )
    assert inspected["release_already_claimed"] is False
    assert inspected["network_access_performed"] is False
    assert inspected["funding_performed"] is False


def test_funding_executor_transfers_exact_amount_verifies_on_c_and_deletes_apps(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        _,
        _,
        release,
        release_path,
        release_digest,
    ) = _release_fixture(tmp_path, monkeypatch)
    opener = _FundingOpener(release["destination"]["address"])
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-rpc-canary-funding-live"),
    )
    assert result["status"] == "pass"
    assert result["transfer_value_wei"] == 742_000_000_000_000
    assert result["funding_transaction_hash"] == opener.tx_hash
    assert result["summary"]["funding_receipt_verified_on_C"] is True
    assert result["summary"]["canary_balance_verified_on_C"] is True
    assert result["summary"]["temporary_A_application_deleted"] is True
    assert result["summary"]["temporary_C_application_deleted"] is True
    assert result["summary"]["canary_execution_performed"] is False
    assert opener.a_deleted is True
    assert opener.c_deleted is True
    assert not any("/api/v1/applications/dockercompose" in path for _, _, path in opener.requests)
    assert ("coolify-a.invalid", "GET", "/api/v1/projects/project-a/environments") in opener.requests
    assert ("coolify-c.invalid", "GET", "/api/v1/projects/project-c/environments") in opener.requests
    assert ("coolify-a.invalid", "GET", "/api/v1/deployments/deployment-a-uuid") in opener.requests
    assert ("coolify-c.invalid", "GET", "/api/v1/deployments/deployment-c-uuid") in opener.requests
    assert not any("/logs" in path for _, _, path in opener.requests)

    verified = verify_validator_rpc_canary_funding_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        operation=_operation("validator-rpc-canary-funding-evidence-verify"),
    )
    assert verified["clean"] is True
    assert verified["funding_transaction_hash"] == opener.tx_hash
    assert verified["temporary_applications_deleted"] is True
    assert verified["next_phase"] == "validator-rpc-canary-execution-release-not-yet-authorized"

    with pytest.raises(MotherDeploymentValidatorRpcCanaryFundingError) as caught:
        execute_validator_rpc_canary_funding_release(
            paths,
            private_state,
            release_path,
            acknowledged_release_sha256=release_digest,
            opener=opener,
            poll_interval_seconds=0,
            max_wait_seconds=0,
            operation=_operation("validator-rpc-canary-funding-reuse"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RELEASE_ALREADY_CONSUMED"


def test_funding_executor_persists_manual_review_and_cleans_up_on_bad_c_proof(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        _,
        _,
        release,
        release_path,
        release_digest,
    ) = _release_fixture(tmp_path, monkeypatch)
    opener = _FundingOpener(
        release["destination"]["address"],
        bad_c_balance=True,
    )
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-rpc-canary-funding-bad-c"),
    )
    assert result["status"] == "manual-review-required"
    assert result["summary"]["complete"] is False
    assert result["summary"]["canary_execution_performed"] is False
    assert opener.a_deleted is True
    assert opener.c_deleted is True
    with pytest.raises(MotherDeploymentValidatorRpcCanaryFundingError):
        verify_validator_rpc_canary_funding_evidence(
            paths,
            private_state,
            Path(result["evidence"]["path"]),
            operation=_operation("validator-rpc-canary-funding-bad-c-verify"),
        )


def test_funding_cli_exposes_release_apply_and_evidence_verification(capsys) -> None:
    for command, expected in (
        ("release-validator-rpc-canary-funding", "--transaction"),
        ("verify-validator-rpc-canary-funding-release", "--release"),
        ("apply-validator-rpc-canary-funding", "--execute"),
        ("verify-validator-rpc-canary-funding-evidence", "--evidence"),
    ):
        with pytest.raises(SystemExit) as caught:
            mother_deploy.main([command, "--help"])
        assert caught.value.code == 0
        assert expected in capsys.readouterr().out


class _CreateRejectedOpener:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str]] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        method = request.get_method()
        path = parsed.path
        self.requests.append((host, method, path))
        expected_token = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"
        if host == "coolify-a.invalid" and method == "GET" and path == "/api/v1/projects/project-a/environments":
            return _AdmissionResponse({"environments": [{"name": "mainnet", "uuid": "mainnet-env-a"}]})
        if host == "coolify-a.invalid" and method == "POST" and path == "/api/v1/services":
            body = json.loads(request.data.decode("utf-8"))
            assert body["environment_uuid"] == "mainnet-env-a"
            return _AdmissionResponse({"message": "Validation error."}, status=422)
        raise AssertionError(f"unexpected request {method} {request.full_url}")


def test_no_write_create_rejection_can_bind_one_fresh_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        funding_path,
        funding_digest,
        _,
        release_path,
        release_digest,
    ) = _release_fixture(tmp_path, monkeypatch)
    failed = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=_CreateRejectedOpener(),
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-rpc-canary-funding-create-rejected"),
    )
    assert failed["status"] == "manual-review-required"
    assert failed["funding_transaction_hash"] is None
    assert failed["summary"]["funding_performed"] is False
    assert failed["summary"]["application_mutation_count"] == 1
    recovery = build_validator_rpc_canary_funding_release(
        paths,
        private_state,
        funding_path,
        acknowledged_transaction_sha256=funding_digest,
        recovery_evidence_path=Path(failed["evidence"]["path"]),
        operation=_operation("validator-rpc-canary-funding-safe-retry"),
    )
    assert recovery["recovery"]["mode"] == "safe-pre-create-rejection-no-write"
    assert recovery["recovery"]["live_write_acknowledged"] is False
    assert recovery["recovery"]["cleanup_authorized"] is False

def test_post_deploy_log_timeout_recovery_is_idempotent_and_reconciles_exact_balance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        funding_path,
        funding_digest,
        release,
        release_path,
        release_digest,
    ) = _release_fixture(tmp_path, monkeypatch)
    app_uuid = "t105jo45pvgt54lcycq2jjt6"
    failure_document = {
        "kind": "main_computer.mother.deployment_validator_rpc_canary_funding_evidence.v1",
        "schema_version": 1,
        "started_at": "2026-08-03T01:51:56Z",
        "completed_at": "2026-08-03T01:56:56Z",
        "status": "manual-review-required",
        "network": "mainnet",
        "mother_binding": release["mother_binding"],
        "release": {
            "locator": str(release_path.relative_to(paths.root)).replace("\\\\", "/"),
            "sha256": release_digest,
        },
        "execution_claim": {
            "locator": (
                "actions/deployment-validator-rpc-canary-funding-execution-claims/"
                f"{release_digest}.json"
            )
        },
        "chain": release["chain"],
        "funding_source_address": release["funding_source"]["address"],
        "canary_address": release["destination"]["address"],
        "transfer_value_wei": release["funding_policy"]["transfer_value_wei"],
        "funding_transaction_hash": None,
        "cross_validator_verification": None,
        "mutation_receipts": [
            {
                "mutation_id": "mainnet-canary1-fund-a.create-application",
                "controller_id": "coolify-a",
                "method": "POST",
                "endpoint": "/api/v1/services",
                "http_status": 201,
                "status": "succeeded",
                "live_write_acknowledged": True,
                "application_uuid": app_uuid,
            },
            {
                "mutation_id": "mainnet-canary1-fund-a.bind-captain-secret",
                "controller_id": "coolify-a",
                "method": "POST",
                "endpoint": f"/api/v1/services/{app_uuid}/envs",
                "http_status": 201,
                "status": "succeeded",
                "live_write_acknowledged": True,
                "application_uuid": app_uuid,
            },
            {
                "mutation_id": "mainnet-canary1-fund-a.deploy",
                "controller_id": "coolify-a",
                "method": "GET",
                "endpoint": f"/api/v1/deploy?uuid={app_uuid}&force=false",
                "http_status": 200,
                "status": "succeeded",
                "live_write_acknowledged": True,
                "application_uuid": app_uuid,
            },
            {
                "mutation_id": "emergency-delete-a-funder",
                "controller_id": "coolify-a",
                "method": "DELETE",
                "endpoint": f"/api/v1/services/{app_uuid}",
                "http_status": 200,
                "status": "succeeded",
                "live_write_acknowledged": True,
                "application_uuid": app_uuid,
            },
        ],
        "log_observations": [
            {
                "phase": "A-mainnet-environment-resolution",
                "controller_id": "coolify-a",
                "method": "GET",
                "endpoint": "/api/v1/projects/project-a/environments",
                "http_status": 200,
                "verified": True,
            },
            *[
                {
                    "phase": "A-capped-funding-result",
                    "controller_id": "coolify-a",
                    "method": "GET",
                    "endpoint": endpoint,
                    "http_status": 404,
                    "marker_present": False,
                }
                for endpoint in (
                    f"/api/v1/services/{app_uuid}/logs?sub_service_name=mainnet-canary1-fund-a&lines=500&show_timestamps=true",
                    f"/api/v1/services/{app_uuid}/logs?lines=500",
                    f"/api/v1/services/{app_uuid}/docker/logs?lines=500",
                    f"/api/v1/services/{app_uuid}/applications/logs?lines=500",
                )
            ],
        ],
        "failure": {
            "code": "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RESULT_TIMEOUT",
            "message": "A-capped-funding-result did not produce its committed result marker",
        },
        "summary": {
            "clean": False,
            "complete": False,
            "funding_performed": False,
            "funding_receipt_verified_on_C": False,
            "canary_balance_verified_on_C": False,
            "exact_transfer_value_verified": False,
            "temporary_A_application_deleted": False,
            "temporary_C_application_deleted": False,
            "application_mutation_count": 4,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "public_endpoint_count": 0,
            "validator_vote_performed": False,
            "canary_execution_performed": False,
            "next_phase": "manual-review-required",
        },
    }
    digest = hashlib.sha256(canonical_json(failure_document)).hexdigest()
    failure_document["validator_rpc_canary_funding_evidence_sha256"] = digest
    evidence_dir = (
        paths.root / "evidence" / "deployment-validator-rpc-canary-funding"
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    failure_path = evidence_dir / f"20260803T015656Z-{digest[:16]}.json"
    failure_path.write_bytes(canonical_json(failure_document))

    recovery_release = build_validator_rpc_canary_funding_release(
        paths,
        private_state,
        funding_path,
        acknowledged_transaction_sha256=funding_digest,
        recovery_evidence_path=failure_path,
        operation=_operation("validator-rpc-canary-funding-idempotent-release"),
    )
    assert recovery_release["recovery"]["mode"] == (
        "idempotent-post-deploy-balance-reconcile-or-fund"
    )
    assert recovery_release["recovery"]["prior_cleanup_acknowledged"] is True
    recovery_path, recovery_digest = write_validator_rpc_canary_funding_release(
        paths,
        recovery_release,
        operation=_operation("validator-rpc-canary-funding-idempotent-release-write"),
    )
    opener = _FundingOpener(
        recovery_release["destination"]["address"],
        already_funded=True,
    )
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        recovery_path,
        acknowledged_release_sha256=recovery_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-rpc-canary-funding-idempotent-live"),
    )
    assert result["status"] == "pass"
    assert result["funding_mode"] == "already-funded"
    assert result["funding_transaction_hash"] is None
    assert result["summary"]["funding_performed"] is False
    assert result["summary"]["funding_reconciled_from_prior_execution"] is True
    assert result["summary"]["funding_receipt_verified_on_C"] is False
    assert result["summary"]["canary_balance_verified_on_C"] is True
    verified = verify_validator_rpc_canary_funding_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        operation=_operation("validator-rpc-canary-funding-idempotent-evidence"),
    )
    assert verified["clean"] is True
    assert verified["funding_mode"] == "already-funded"
    assert verified["funding_reconciled_from_prior_execution"] is True



def test_deployment_uuid_failure_recovery_accepts_acknowledged_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        funding_path,
        funding_digest,
        release,
        release_path,
        release_digest,
    ) = _release_fixture(tmp_path, monkeypatch)
    app_uuid = "h2mgukma99iakoxq1xm6v39t"
    failure_document = {
        "kind": "main_computer.mother.deployment_validator_rpc_canary_funding_evidence.v1",
        "schema_version": 1,
        "started_at": "2026-08-03T02:20:30Z",
        "completed_at": "2026-08-03T02:20:31Z",
        "status": "manual-review-required",
        "network": "mainnet",
        "mother_binding": release["mother_binding"],
        "release": {
            "locator": str(release_path.relative_to(paths.root)).replace("\\", "/"),
            "sha256": release_digest,
        },
        "execution_claim": {
            "locator": (
                "actions/deployment-validator-rpc-canary-funding-execution-claims/"
                f"{release_digest}.json"
            )
        },
        "chain": release["chain"],
        "funding_source_address": release["funding_source"]["address"],
        "canary_address": release["destination"]["address"],
        "transfer_value_wei": release["funding_policy"]["transfer_value_wei"],
        "funding_transaction_hash": None,
        "cross_validator_verification": None,
        "mutation_receipts": [
            {
                "mutation_id": "mainnet-canary1-fund-a.create-application",
                "controller_id": "coolify-a",
                "method": "POST",
                "endpoint": "/api/v1/services",
                "http_status": 201,
                "status": "succeeded",
                "live_write_acknowledged": True,
                "application_uuid": app_uuid,
            },
            {
                "mutation_id": "mainnet-canary1-fund-a.bind-captain-secret",
                "controller_id": "coolify-a",
                "method": "POST",
                "endpoint": f"/api/v1/services/{app_uuid}/envs",
                "http_status": 201,
                "status": "succeeded",
                "live_write_acknowledged": True,
                "application_uuid": app_uuid,
            },
            {
                "mutation_id": "mainnet-canary1-fund-a.deploy",
                "controller_id": "coolify-a",
                "method": "GET",
                "endpoint": f"/api/v1/deploy?uuid={app_uuid}&force=false",
                "http_status": 200,
                "status": "succeeded",
                "live_write_acknowledged": True,
                "application_uuid": app_uuid,
                "deployment_uuid": "",
            },
            {
                "mutation_id": "emergency-delete-a-funder",
                "controller_id": "coolify-a",
                "method": "DELETE",
                "endpoint": f"/api/v1/services/{app_uuid}",
                "http_status": 200,
                "status": "succeeded",
                "live_write_acknowledged": True,
                "application_uuid": app_uuid,
            },
        ],
        "log_observations": [
            {
                "phase": "A-mainnet-environment-resolution",
                "controller_id": "coolify-a",
                "method": "GET",
                "endpoint": "/api/v1/projects/project-a/environments",
                "http_status": 200,
                "verified": True,
            }
        ],
        "failure": {
            "code": "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_DEPLOYMENT_INVALID",
            "message": (
                "Coolify deployment request did not return exactly one usable "
                "deployment UUID"
            ),
        },
        "summary": {
            "clean": False,
            "complete": False,
            "funding_performed": False,
            "funding_complete": False,
            "funding_receipt_verified_on_C": False,
            "canary_balance_verified_on_C": False,
            "exact_transfer_value_verified": False,
            "temporary_A_application_deleted": True,
            "temporary_C_application_deleted": False,
            "application_mutation_count": 4,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "public_endpoint_count": 0,
            "validator_vote_performed": False,
            "canary_execution_performed": False,
            "next_phase": "manual-review-required",
        },
    }
    digest = hashlib.sha256(canonical_json(failure_document)).hexdigest()
    failure_document["validator_rpc_canary_funding_evidence_sha256"] = digest
    evidence_dir = paths.root / "evidence" / "deployment-validator-rpc-canary-funding"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    failure_path = evidence_dir / f"20260803T022030Z-{digest[:16]}.json"
    failure_path.write_bytes(canonical_json(failure_document))

    recovery_release = build_validator_rpc_canary_funding_release(
        paths,
        private_state,
        funding_path,
        acknowledged_transaction_sha256=funding_digest,
        recovery_evidence_path=failure_path,
        operation=_operation("validator-rpc-canary-funding-deployment-uuid-recovery"),
    )
    assert recovery_release["recovery"]["mode"] == (
        "idempotent-post-deploy-balance-reconcile-or-fund"
    )
    assert recovery_release["recovery"]["failed_phase"] == "A-deployment-uuid-resolution"
    assert recovery_release["recovery"]["prior_cleanup_acknowledged"] is True
    assert recovery_release["recovery"]["prior_funding_state"] == "unknown"
