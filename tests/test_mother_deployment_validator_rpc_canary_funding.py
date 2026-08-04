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


def _release_fixture(tmp_path: Path, monkeypatch):
    paths, private_state, _, funding, funding_path, funding_digest = _funding_fixture(
        tmp_path,
        monkeypatch,
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


class _StatusHealthFundingOpener:
    UUIDS = {
        "mainnet-canary1-classify-exact-a": "classify-exact-a-uuid",
        "mainnet-canary1-classify-zero-a": "classify-zero-a-uuid",
        "mainnet-canary1-fund-a": "fund-a-service-uuid",
        "mainnet-canary1-verify-funded-c": "verify-funded-c-uuid",
        "mainnet-canary1-verify-reconciled-c": "verify-reconciled-c-uuid",
    }

    def __init__(
        self,
        *,
        already_funded: bool = False,
        bad_c: bool = False,
        bad_funder: bool = False,
        reject_first_create: bool = False,
    ) -> None:
        self.already_funded = already_funded
        self.bad_c = bad_c
        self.bad_funder = bad_funder
        self.reject_first_create = reject_first_create
        self.requests: list[tuple[str, str, str]] = []
        self.names_by_uuid: dict[str, str] = {}
        self.started: set[str] = set()
        self.deleted: set[str] = set()
        self.secret_bound = False
        self.create_count = 0

    def _status(self, name: str) -> str:
        if name not in self.started:
            return "exited"
        if name.endswith("classify-exact-a"):
            return "running:healthy:excluded" if self.already_funded else "exited"
        if name.endswith("classify-zero-a"):
            return "exited" if self.already_funded else "running:healthy"
        if name.endswith("fund-a"):
            return "running:unhealthy" if self.bad_funder else "running:healthy"
        if name.endswith("verify-funded-c") or name.endswith("verify-reconciled-c"):
            return "running:unhealthy" if self.bad_c else "running:healthy:excluded"
        raise AssertionError(name)

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        method = request.get_method()
        path = parsed.path
        self.requests.append((host, method, path))
        assert timeout > 0
        expected_token = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"

        if method == "GET" and path.endswith("/environments"):
            uuid = "mainnet-env-a" if host == "coolify-a.invalid" else "mainnet-env-c"
            return _AdmissionResponse({"environments": [{"name": "mainnet", "uuid": uuid}]})

        if method == "POST" and path == "/api/v1/services":
            self.create_count += 1
            if self.reject_first_create and self.create_count == 1:
                return _AdmissionResponse({"message": "Validation error."}, status=422)
            body = json.loads(request.data.decode("utf-8"))
            name = body["name"]
            assert name in self.UUIDS
            compose = base64.b64decode(
                body["docker_compose_raw"],
                validate=True,
            ).decode("utf-8")
            assert "entrypoint:" in compose
            assert "- /bin/sh" in compose
            assert "healthcheck:" in compose
            assert 'test "$(cat /proc/1/comm)" = "sleep"' in compose
            assert "exec sleep 900" in compose
            assert "/logs" not in compose
            assert "/deployments" not in compose
            assert "ports:" not in compose
            assert body["instant_deploy"] is False
            assert body["environment_name"] == "mainnet"
            assert body["environment_uuid"] in {"mainnet-env-a", "mainnet-env-c"}
            service_uuid = self.UUIDS[name]
            self.names_by_uuid[service_uuid] = name
            return _AdmissionResponse({"uuid": service_uuid}, status=201)

        if method == "POST" and path.endswith("/envs"):
            body = json.loads(request.data.decode("utf-8"))
            assert path == "/api/v1/services/fund-a-service-uuid/envs"
            assert body["key"] == "MC_MOTHER_CAPTAIN_PRIVATE_KEY"
            assert isinstance(body["value"], str) and body["value"].startswith("0x")
            assert body["is_shown_once"] is True
            self.secret_bound = True
            return _AdmissionResponse({"uuid": "env-a-uuid"}, status=201)

        if method == "POST" and path.endswith("/start"):
            service_uuid = path.split("/")[-2]
            assert service_uuid in self.names_by_uuid
            self.started.add(self.names_by_uuid[service_uuid])
            return _AdmissionResponse({"message": "Service starting request queued."})

        if method == "GET" and path.startswith("/api/v1/services/"):
            service_uuid = path.rsplit("/", 1)[-1]
            name = self.names_by_uuid[service_uuid]
            return _AdmissionResponse({
                "uuid": service_uuid,
                "name": name,
                "status": self._status(name),
                "applications": [
                    {
                        "uuid": f"{service_uuid}-application",
                        "name": name,
                        "status": self._status(name),
                    }
                ],
                "databases": [],
            })

        if method == "DELETE" and path.startswith("/api/v1/services/"):
            service_uuid = path.rsplit("/", 1)[-1]
            name = self.names_by_uuid[service_uuid]
            self.deleted.add(name)
            return _AdmissionResponse({"message": "Service deleted."})

        raise AssertionError(f"unexpected request {method} {request.full_url}")


def test_funding_compiler_binds_status_health_result_channel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, _, _, funding, _, _ = _funding_fixture(tmp_path, monkeypatch)
    assert funding["schema_version"] == 6
    assert funding["kind"].endswith(".v6")
    assert funding["funding_source"]["role"] == "captain"
    assert funding["funding_source"]["private_key_material_in_transaction"] is False
    assert funding["destination"]["allowed_pre_execution_balances_wei"] == [
        0,
        742_000_000_000_000,
    ]
    transport = funding["coolify_transport"]
    assert transport["result_channel"] == "service-detail-health"
    assert transport["deployment_uuid_required"] is False
    assert transport["deployment_inventory_endpoint_authorized"] is False
    assert transport["deployment_result_endpoint_authorized"] is False
    assert transport["service_log_endpoints_authorized"] is False
    assert transport["generic_deploy_endpoint_authorized"] is False
    assert transport["service_start_endpoint_template"] == (
        "/api/v1/services/{service_uuid}/start"
    )
    assert set(funding["applications"]) == {
        "a_exact_balance_classifier",
        "a_zero_balance_classifier",
        "a_funder",
        "c_funded_verifier",
        "c_reconciled_verifier",
    }
    for app in funding["applications"].values():
        compose = app["compose"]["canonical_text"]
        assert "entrypoint:" in compose
        assert "healthcheck:" in compose
        assert "exec sleep 900" in compose
        assert "ports:" not in compose
        assert "traefik." not in compose
    assert (
        funding["applications"]["a_funder"]["captain_secret_binding_required"]
        is True
    )
    assert (
        funding["applications"]["c_funded_verifier"]["captain_secret_binding_required"]
        is False
    )
    assert funding["summary"]["service_health_result_channel_compiled"] is True
    assert funding["summary"]["runtime_log_result_channel_authorized"] is False
    assert funding["summary"]["deployment_uuid_required"] is False
    assert funding["summary"]["maximum_service_mutation_count"] == 13
    assert funding["summary"]["minimum_service_mutation_count"] == 6


def test_funding_transaction_persists_and_rebuild_verifies(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, _, _, funding_path, funding_digest = _funding_fixture(
        tmp_path,
        monkeypatch,
    )
    verified = verify_validator_rpc_canary_funding_transaction(
        paths,
        private_state,
        funding_path,
        operation=_operation("validator-rpc-canary-funding-verify"),
    )
    assert verified["clean"] is True
    assert verified["transaction_sha256"] == funding_digest
    assert verified["service_health_result_channel_required"] is True
    assert verified["runtime_log_result_channel_authorized"] is False
    assert verified["deployment_uuid_required"] is False
    assert verified["deployment_inventory_resolution_required"] is False
    assert verified["maximum_service_mutation_count"] == 13


def test_funding_verifier_rejects_tampered_cap(tmp_path: Path, monkeypatch) -> None:
    paths, private_state, _, funding, funding_path, _ = _funding_fixture(
        tmp_path,
        monkeypatch,
    )
    tampered = dict(funding)
    tampered["funding_policy"] = dict(funding["funding_policy"])
    tampered["funding_policy"]["transfer_value_cap_wei"] += 1
    tampered["validator_rpc_canary_funding_transaction_sha256"] = __import__(
        "hashlib"
    ).sha256(
        canonical_json({
            key: value
            for key, value in tampered.items()
            if key != "validator_rpc_canary_funding_transaction_sha256"
        })
    ).hexdigest()
    funding_path.write_bytes(canonical_json(tampered))
    with pytest.raises(MotherDeploymentValidatorRpcCanaryFundingError):
        verify_validator_rpc_canary_funding_transaction(
            paths,
            private_state,
            funding_path,
            operation=_operation("validator-rpc-canary-funding-tamper"),
        )


def test_funding_release_is_one_use_exact_cap_and_inspection_is_offline(
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
    assert release["schema_version"] == 2
    assert release["authority"]["requested_use_limit"] == 1
    assert release["authority"]["funding_authorized"] is True
    assert release["policy"]["service_health_result_channel_required"] is True
    assert release["policy"]["runtime_log_result_channel_authorized"] is False
    assert release["policy"]["deployment_uuid_required"] is False
    verified = verify_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        operation=_operation("validator-rpc-canary-funding-release-verify"),
    )
    assert verified["clean"] is True
    assert verified["release_sha256"] == release_digest
    assert verified["result_channel"] == "service-detail-health"
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


def test_funded_path_uses_positive_classifiers_and_cross_validator_health_proof(
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
    opener = _StatusHealthFundingOpener()
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-rpc-canary-funding-live"),
    )
    assert result["status"] == "pass"
    assert result["funding_mode"] == "funded"
    assert result["funding_transaction_hash"] is None
    assert result["transaction_hash_recorded"] is False
    assert result["chain_state"] == "exact-cross-validator-verified"
    assert result["summary"]["funding_receipt_verified_on_C"] is True
    assert result["summary"]["canary_balance_verified_on_C"] is True
    assert result["summary"]["temporary_services_deleted"] is True
    assert result["summary"]["temporary_service_count"] == 4
    assert result["summary"]["application_mutation_count"] == 13
    assert opener.secret_bound is True
    assert opener.deleted == {
        "mainnet-canary1-classify-exact-a",
        "mainnet-canary1-classify-zero-a",
        "mainnet-canary1-fund-a",
        "mainnet-canary1-verify-funded-c",
    }
    assert not any("/logs" in path for _, _, path in opener.requests)
    assert not any("/deployments" in path for _, _, path in opener.requests)

    verified = verify_validator_rpc_canary_funding_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        operation=_operation("validator-rpc-canary-funding-evidence-verify"),
    )
    assert verified["clean"] is True
    assert verified["funding_mode"] == "funded"
    assert verified["funding_transaction_hash"] is None
    assert verified["funding_receipt_verified_on_C"] is True
    assert verified["result_channel"] == "service-detail-health"

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
    assert (
        caught.value.code
        == "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RELEASE_ALREADY_CONSUMED"
    )


def test_exact_balance_reconciles_without_binding_captain_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        _,
        _,
        _,
        release_path,
        release_digest,
    ) = _release_fixture(tmp_path, monkeypatch)
    opener = _StatusHealthFundingOpener(already_funded=True)
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-rpc-canary-funding-reconcile"),
    )
    assert result["status"] == "pass"
    assert result["funding_mode"] == "already-funded"
    assert result["summary"]["funding_performed"] is False
    assert result["summary"]["funding_reconciled_from_prior_execution"] is True
    assert result["summary"]["funding_receipt_verified_on_C"] is False
    assert result["summary"]["temporary_service_count"] == 2
    assert result["summary"]["application_mutation_count"] == 6
    assert opener.secret_bound is False
    assert opener.deleted == {
        "mainnet-canary1-classify-exact-a",
        "mainnet-canary1-verify-reconciled-c",
    }
    verified = verify_validator_rpc_canary_funding_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        operation=_operation("validator-rpc-canary-funding-reconcile-verify"),
    )
    assert verified["funding_reconciled_from_prior_execution"] is True


def test_failed_started_funder_marks_chain_unknown_and_cleans_every_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        _,
        _,
        _,
        release_path,
        release_digest,
    ) = _release_fixture(tmp_path, monkeypatch)
    opener = _StatusHealthFundingOpener(bad_funder=True)
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-rpc-canary-funding-unknown"),
    )
    assert result["status"] == "manual-review-required"
    assert result["chain_state"] == "potentially-unknown-after-funder-start"
    assert result["summary"]["temporary_services_deleted"] is True
    assert result["summary"]["canary_execution_performed"] is False
    assert opener.deleted == {
        "mainnet-canary1-classify-exact-a",
        "mainnet-canary1-classify-zero-a",
        "mainnet-canary1-fund-a",
    }
    with pytest.raises(MotherDeploymentValidatorRpcCanaryFundingError):
        verify_validator_rpc_canary_funding_evidence(
            paths,
            private_state,
            Path(result["evidence"]["path"]),
            operation=_operation("validator-rpc-canary-funding-unknown-verify"),
        )


def test_failed_c_health_proof_is_manual_review_and_retry_can_reclassify(
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
    opener = _StatusHealthFundingOpener(bad_c=True)
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-rpc-canary-funding-bad-c"),
    )
    assert result["status"] == "manual-review-required"
    assert result["chain_state"] == "exact-on-A-not-yet-verified-on-C"
    assert result["summary"]["temporary_services_deleted"] is True

    retry = build_validator_rpc_canary_funding_release(
        paths,
        private_state,
        funding_path,
        acknowledged_transaction_sha256=funding_digest,
        recovery_evidence_path=Path(result["evidence"]["path"]),
        operation=_operation("validator-rpc-canary-funding-retry-release"),
    )
    assert retry["recovery"]["mode"] == "idempotent-status-health-reclassification"
    assert retry["recovery"]["prior_chain_state"] == (
        "exact-on-A-not-yet-verified-on-C"
    )


def test_create_rejection_records_no_live_write_and_is_safe_to_restage(
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
    opener = _StatusHealthFundingOpener(reject_first_create=True)
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-rpc-canary-funding-create-rejected"),
    )
    assert result["status"] == "manual-review-required"
    assert result["chain_state"] == "unchanged-before-funder-start"
    assert result["summary"]["temporary_services_deleted"] is True
    assert result["summary"]["temporary_service_count"] == 0
    assert opener.deleted == set()

    retry = build_validator_rpc_canary_funding_release(
        paths,
        private_state,
        funding_path,
        acknowledged_transaction_sha256=funding_digest,
        recovery_evidence_path=Path(result["evidence"]["path"]),
        operation=_operation("validator-rpc-canary-funding-create-rejected-retry"),
    )
    assert retry["recovery"]["prior_chain_state"] == "unchanged-before-funder-start"


def test_funding_cli_exposes_stage_release_apply_and_verification(capsys) -> None:
    for command, expected in (
        ("stage-validator-rpc-canary-funding-transaction", "--canary-transaction"),
        ("verify-validator-rpc-canary-funding-transaction", "--transaction"),
        ("release-validator-rpc-canary-funding", "--transaction"),
        ("verify-validator-rpc-canary-funding-release", "--release"),
        ("apply-validator-rpc-canary-funding", "--execute"),
        ("verify-validator-rpc-canary-funding-evidence", "--evidence"),
    ):
        with pytest.raises(SystemExit) as caught:
            mother_deploy.main([command, "--help"])
        assert caught.value.code == 0
        assert expected in capsys.readouterr().out
