from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_validator_rpc_canary_funding import (
    MotherDeploymentValidatorRpcCanaryFundingError,
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
    assert funding["destination"]["pre_funding_balance_must_equal_wei"] == 0
    policy = funding["funding_policy"]
    assert policy["transfer_value_wei"] == 742_000_000_000_000
    assert policy["transfer_value_cap_wei"] == 742_000_000_000_000
    assert policy["funding_transaction_max_fee_wei"] == 42_000_000_000_000
    assert policy["source_maximum_total_debit_wei"] == 784_000_000_000_000
    assert policy["cross_validator_receipt_and_balance_verification_required"] is True
    assert funding["coolify_transport"] == {
        "resource_api": "services",
        "create_endpoint": "/api/v1/services",
        "deprecated_application_create_endpoint_authorized": False,
        "compose_encoding": "base64",
        "environment_uuid_resolution": "read-only-exact-name-before-create",
    }
    assert funding["schema_version"] == 3
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

    def __init__(self, destination: str, *, bad_c_balance: bool = False) -> None:
        self.destination = destination
        self.bad_c_balance = bad_c_balance
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
                    {"deployments": [{"resource_uuid": "fund-a-uuid", "deployment_uuid": "dep-a"}]}
                )
            if method == "GET" and path == "/api/v1/services/fund-a-uuid/logs":
                return _AdmissionResponse({
                    "logs": (
                        "MOTHER_VALIDATOR_RPC_CANARY_FUNDING_A_RESULT="
                        + json.dumps({"transactionHash": self.tx_hash})
                    )
                })
            if method == "DELETE" and path == "/api/v1/services/fund-a-uuid":
                self.a_deleted = True
                return _AdmissionResponse({"message": "Application deleted."})

        if host == "coolify-c.invalid":
            if method == "GET" and path == "/api/v1/projects/project-c/environments":
                return _AdmissionResponse({"data": [{"name": "mainnet", "uuid": "mainnet-env-c"}]})
            if method == "POST" and path == "/api/v1/services":
                body = json.loads(request.data.decode("utf-8"))
                assert body["name"] == "mainnet-canary1-fund-c"
                compose = base64.b64decode(body["docker_compose_raw"], validate=True).decode("utf-8")
                assert "mainnet-canary1-fund-c:" in compose
                assert body["environment_uuid"] == "mainnet-env-c"
                return _AdmissionResponse({"uuid": "fund-c-uuid"}, status=201)
            if method == "PATCH" and path == "/api/v1/services/fund-c-uuid/envs/bulk":
                body = json.loads(request.data.decode("utf-8"))
                assert body["data"][0]["key"] == "MC_MOTHER_CANARY_FUNDING_TX_HASH"
                assert body["data"][0]["value"] == self.tx_hash
                return _AdmissionResponse([{"uuid": "env-c-uuid"}], status=201)
            if method == "GET" and path == "/api/v1/deploy":
                assert parsed.query == "uuid=fund-c-uuid&force=false"
                return _AdmissionResponse(
                    {"deployments": [{"resource_uuid": "fund-c-uuid", "deployment_uuid": "dep-c"}]}
                )
            if method == "GET" and path == "/api/v1/services/fund-c-uuid/logs":
                balance = "1" if self.bad_c_balance else "742000000000000"
                return _AdmissionResponse({
                    "logs": (
                        "MOTHER_VALIDATOR_RPC_CANARY_FUNDING_C_RESULT="
                        + json.dumps({
                            "transaction_hash": self.tx_hash,
                            "destination": self.destination,
                            "balance_wei": balance,
                        })
                    )
                })
            if method == "DELETE" and path == "/api/v1/services/fund-c-uuid":
                self.c_deleted = True
                return _AdmissionResponse({"message": "Application deleted."})

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
    assert any(path.startswith("/api/v1/services/fund-a-uuid/logs") for _, _, path in opener.requests)
    assert any(path.startswith("/api/v1/services/fund-c-uuid/logs") for _, _, path in opener.requests)

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
