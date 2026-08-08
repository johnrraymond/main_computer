from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
import types
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common import deployment_validator_rpc_canary_funding as funding_module
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


@pytest.fixture(autouse=True)
def _patch_local_python_funder(monkeypatch):
    def fake_local_python_funding(**kwargs):
        opener = kwargs["opener"]
        kwargs["on_transaction_sent"]()
        if getattr(opener, "bad_funder", False):
            raise MotherDeploymentValidatorRpcCanaryFundingError(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RECEIPT_FAILED",
                "synthetic local funding receipt failure",
            )
        tx_hash = getattr(opener, "tx_hash", "0x" + ("1" * 64))
        amount = int(kwargs["amount"])
        opener.already_funded = True
        return {
            "phase": "a_funder-local-json-rpc-result",
            "healthy": True,
            "classification": "funded",
            "result_channel": "local-json-rpc-eth-account",
            "rpc_url": kwargs["rpc_url"],
            "from_address": kwargs["source"],
            "destination": kwargs["destination"],
            "tx_hash": tx_hash,
            "nonce": 0,
            "gas_limit": 21_000,
            "transaction_type": "eip1559",
            "base_fee_per_gas_wei": 1,
            "max_fee_per_gas_wei": 2_000_000_000,
            "max_priority_fee_per_gas_wei": 0,
            "source_balance_before_wei": amount + 42_000_000_000_000,
            "source_minimum_required_wei": amount + 42_000_000_000_000,
            "destination_balance_before_wei": 0,
            "destination_balance_after_wei": amount,
            "receipt_status": "0x1",
            "receipt_poll_count": 1,
            "observation_count": 1,
            "observations": [],
            "proof": "synthetic local eth_account funding proof",
        }

    monkeypatch.setattr(
        funding_module,
        "_execute_local_python_funding",
        fake_local_python_funding,
    )


class _StatusHealthFundingOpener:
    UUIDS = {
        "mother-mainnet-rpc-route-a": "mother-mainnet-rpc-route-a-uuid",
        "mother-mainnet-rpc-route-c": "mother-mainnet-rpc-route-c-uuid",
        "mainnet-canary1-probe-balance-rpc-a": "probe-balance-rpc-a-uuid",
        "mainnet-canary1-classify-exact-a": "classify-exact-a-uuid",
        "mainnet-canary1-classify-zero-a": "classify-zero-a-uuid",
        "mainnet-canary1-verify-funded-a": "verify-funded-a-uuid",
        "mainnet-canary1-verify-funded-c": "verify-funded-c-uuid",
        "mainnet-canary1-verify-reconciled-c": "verify-reconciled-c-uuid",
    }

    def __init__(
        self,
        *,
        already_funded: bool = False,
        bad_c: bool = False,
        bad_funder: bool = False,
        bad_probe: bool = False,
        unexpected_balance: bool = False,
        reject_first_create: bool = False,
        cast_probe_terminal_with_marker: bool = False,
        empty_application_logs: bool = False,
        c_runtime_marker_missing: bool = False,
        bad_rpc_route: bool = False,
        wrong_rpc_chain: bool = False,
        bad_rpc_balance: bool = False,
        delayed_route_writer_health_polls: int = 0,
    ) -> None:
        self.already_funded = already_funded
        self.bad_c = bad_c
        self.bad_funder = bad_funder
        self.bad_probe = bad_probe
        self.unexpected_balance = unexpected_balance
        self.reject_first_create = reject_first_create
        self.cast_probe_terminal_with_marker = cast_probe_terminal_with_marker
        self.empty_application_logs = empty_application_logs
        self.c_runtime_marker_missing = c_runtime_marker_missing
        self.bad_rpc_route = bad_rpc_route
        self.wrong_rpc_chain = wrong_rpc_chain
        self.bad_rpc_balance = bad_rpc_balance
        self.delayed_route_writer_health_polls = delayed_route_writer_health_polls
        self.status_poll_counts: dict[str, int] = {}
        self.requests: list[tuple[str, str, str]] = []
        self.names_by_uuid: dict[str, str] = {}
        self.started: set[str] = set()
        self.deleted: set[str] = set()
        self.secret_bound = False
        self.create_count = 0
        self.tx_hash = "0x" + ("1" * 64)

    def _status(self, name: str) -> str:
        if name not in self.started:
            return "exited"
        if name.startswith("mother-mainnet-rpc-route-"):
            self.status_poll_counts[name] = self.status_poll_counts.get(name, 0) + 1
            if self.status_poll_counts[name] <= self.delayed_route_writer_health_polls:
                return "exited"
            return "running:healthy:excluded"
        if name.endswith("probe-balance-rpc-a"):
            if self.cast_probe_terminal_with_marker:
                return "exited"
            return "exited" if self.bad_probe else "running:healthy:excluded"
        if name.endswith("classify-exact-a"):
            return "running:healthy:excluded" if self.already_funded else "exited"
        if name.endswith("classify-zero-a"):
            return (
                "running:healthy"
                if not self.already_funded and not self.unexpected_balance
                else "exited"
            )
        if name.endswith("verify-funded-a"):
            return "running:healthy:excluded"
        if name.endswith("verify-funded-c") or name.endswith("verify-reconciled-c"):
            return "running:unhealthy" if self.bad_c else "running:healthy:excluded"
        raise AssertionError(name)


    def _balance_for_canary(self) -> int:
        if self.already_funded:
            return 742_000_000_000_000
        if self.unexpected_balance:
            return 123
        return 0

    def _runtime_logs(self, name: str) -> str:
        marker = "MOTHER_VALIDATOR_RPC_CANARY_FUNDING_RESULT"
        balance = self._balance_for_canary()
        if name.endswith("probe-balance-rpc-a"):
            classification = "rpc-error" if self.bad_probe else "rpc-ok"
            return (
                f"{marker} step=a_balance_rpc_probe classification={classification} "
                f"rpc_url=http://mainneta-super1:8545 block_number=100 balance_wei={balance}\n"
            )
        if name.endswith("classify-exact-a"):
            classification = "match" if self.already_funded else "nonmatch"
            return (
                f"{marker} step=a_exact_balance_classifier classification={classification} "
                f"rpc_url=http://mainneta-super1:8545 block_number=100 "
                f"balance_wei={balance} expected_balance_wei=742000000000000\n"
            )
        if name.endswith("classify-zero-a"):
            classification = (
                "match"
                if not self.already_funded and not self.unexpected_balance
                else "nonmatch"
            )
            return (
                f"{marker} step=a_zero_balance_classifier classification={classification} "
                f"rpc_url=http://mainneta-super1:8545 block_number=100 "
                f"balance_wei={balance} expected_balance_wei=0\n"
            )
        if name.endswith("verify-funded-a"):
            return (
                f"{marker} step=a_post_funding_verifier classification=match "
                "rpc_url=http://mainneta-super1:8545 block_number=101 "
                "balance_wei=742000000000000 expected_balance_wei=742000000000000\n"
            )
        if name.endswith("verify-funded-c"):
            if self.c_runtime_marker_missing:
                return ""
            classification = "verifier-error" if self.bad_c else "verified"
            return (
                f"{marker} step=c_funded_verifier classification={classification} "
                f"rpc_url=http://mainnetc-super1:8545 tx_hash={self.tx_hash} "
                "balance_wei=742000000000000 expected_balance_wei=742000000000000\n"
            )
        if name.endswith("verify-reconciled-c"):
            if self.c_runtime_marker_missing:
                return ""
            return (
                f"{marker} step=c_reconciled_verifier classification=match "
                "rpc_url=http://mainnetc-super1:8545 block_number=101 "
                "balance_wei=742000000000000 expected_balance_wei=742000000000000\n"
            )
        raise AssertionError(name)

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        method = request.get_method()
        path = parsed.path
        self.requests.append((host, method, path))
        assert timeout > 0

        if host == "mainnet-rpc.greatlibrary.io":
            assert method == "POST"
            assert request.headers.get("Authorization") is None
            payload = json.loads(request.data.decode("utf-8"))
            if self.bad_rpc_route:
                return _AdmissionResponse("no available server", status=503)
            if payload["method"] == "eth_chainId":
                chain_id = 1 if self.wrong_rpc_chain else 42424240
                return _AdmissionResponse({"jsonrpc": "2.0", "id": payload["id"], "result": hex(chain_id)})
            if payload["method"] == "eth_blockNumber":
                if self.bad_probe:
                    return _AdmissionResponse({"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32000, "message": "synthetic probe failure"}})
                return _AdmissionResponse({"jsonrpc": "2.0", "id": payload["id"], "result": hex(100)})
            if payload["method"] == "eth_getBalance":
                if self.bad_rpc_balance:
                    return _AdmissionResponse({"jsonrpc": "2.0", "id": payload["id"], "result": "not-a-hex-quantity"})
                return _AdmissionResponse({
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": hex(self._balance_for_canary()),
                })
            raise AssertionError(payload["method"])

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
            if name.startswith("mother-mainnet-rpc-route-"):
                assert "python:3.12-alpine" in compose
                assert "/var/run/docker.sock:/var/run/docker.sock" in compose
                assert "mainnet-rpc.greatlibrary.io" in compose
                assert "mother-mainnet-rpc-route-" in compose
                assert "ghcr.io/foundry-rs/foundry" not in compose
            else:
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
            if path == "/api/v1/services/verify-funded-c-uuid/envs":
                assert body["key"] == "MC_MOTHER_CANARY_FUNDING_TX_HASH"
                assert body["value"] == self.tx_hash
                assert body["is_shown_once"] is False
                return _AdmissionResponse({"uuid": "tx-hash-env-uuid"}, status=201)
            raise AssertionError(path)

        if method == "POST" and path.endswith("/start"):
            service_uuid = path.split("/")[-2]
            assert service_uuid in self.names_by_uuid
            self.started.add(self.names_by_uuid[service_uuid])
            return _AdmissionResponse({"message": "Service starting request queued."})


        if method == "GET" and "/logs" in path:
            if "/services/" in path and "/applications/" in path:
                service_uuid = path.split("/services/", 1)[1].split("/applications/", 1)[0]
                name = self.names_by_uuid[service_uuid]
                if self.empty_application_logs:
                    return _AdmissionResponse({"logs": ""})
                return _AdmissionResponse({"logs": self._runtime_logs(name)})
            if path.startswith("/api/v1/applications/"):
                app_uuid = path.rsplit("/", 2)[-2]
                service_uuid = app_uuid.removesuffix("-application")
                name = self.names_by_uuid[service_uuid]
                return _AdmissionResponse({"logs": self._runtime_logs(name)})
            if path.startswith("/api/v1/services/"):
                service_uuid = path.split("/services/", 1)[1].split("/logs", 1)[0]
                name = self.names_by_uuid[service_uuid]
                return _AdmissionResponse({"logs": self._runtime_logs(name)})
            raise AssertionError(path)

        if method == "GET" and path.startswith("/api/v1/services/"):
            service_uuid = path.rsplit("/", 1)[-1]
            name = self.names_by_uuid[service_uuid]
            status = self._status(name)
            return _AdmissionResponse({
                "uuid": service_uuid,
                "name": name,
                "status": status,
                "applications": [
                    {
                        "uuid": f"{service_uuid}-application",
                        "name": name,
                        "status": status,
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
    assert funding["schema_version"] == 15
    assert funding["kind"].endswith(".v15")
    assert funding["funding_source"]["role"] == "captain"
    assert funding["funding_source"]["private_key_material_in_transaction"] is False
    assert funding["destination"]["allowed_pre_execution_balances_wei"] == [
        0,
        742_000_000_000_000,
    ]
    transport = funding["coolify_transport"]
    assert transport["result_channel"] == "service-detail-health+runtime-result-marker"
    assert transport["deployment_uuid_required"] is False
    assert transport["deployment_inventory_endpoint_authorized"] is False
    assert transport["deployment_result_endpoint_authorized"] is False
    assert transport["service_log_endpoints_authorized"] is True
    assert transport["generic_deploy_endpoint_authorized"] is False
    assert transport["service_start_endpoint_template"] == (
        "/api/v1/services/{service_uuid}/start"
    )
    assert set(funding["applications"]) == {
        "a_balance_rpc_probe",
        "a_exact_balance_classifier",
        "a_zero_balance_classifier",
        "a_post_funding_verifier",
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
        assert "--ether=false" not in compose
    assert "python:3.12-alpine" in funding["applications"]["a_balance_rpc_probe"]["compose"]["canonical_text"]
    assert b"cast " not in canonical_json(funding["applications"])
    assert "eth_getBalance" in funding["applications"]["a_balance_rpc_probe"]["compose"]["canonical_text"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in funding["applications"]["c_funded_verifier"]["compose"]["canonical_text"]
    assert "proxy_rpc_verifier.py" in funding["applications"]["c_funded_verifier"]["compose"]["canonical_text"]
    assert (
        funding["applications"]["c_funded_verifier"]["captain_secret_binding_required"]
        is False
    )
    assert funding["summary"]["service_health_result_channel_compiled"] is True
    assert funding["summary"]["runtime_log_result_channel_authorized"] is True
    assert funding["summary"]["deployment_uuid_required"] is False
    assert funding["summary"]["maximum_service_mutation_count"] == 10
    assert funding["summary"]["minimum_service_mutation_count"] == 9


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
    assert verified["runtime_log_result_channel_authorized"] is True
    assert verified["deployment_uuid_required"] is False
    assert verified["deployment_inventory_resolution_required"] is False
    assert verified["maximum_service_mutation_count"] == 10


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
    assert release["policy"]["runtime_log_result_channel_authorized"] is True
    assert release["policy"]["deployment_uuid_required"] is False
    verified = verify_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        operation=_operation("validator-rpc-canary-funding-release-verify"),
    )
    assert verified["clean"] is True
    assert verified["release_sha256"] == release_digest
    assert verified["result_channel"] == "service-detail-health+runtime-result-marker"
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
    assert result["funding_transaction_hash"] == opener.tx_hash
    assert result["transaction_hash_recorded"] is True
    assert result["chain_state"] == "exact-cross-validator-verified"
    assert result["summary"]["funding_receipt_verified_on_C"] is True
    assert result["summary"]["allfather_rpc_route_preflight_used"] is True
    assert result["summary"]["allfather_rpc_route_preflight_complete"] is True
    assert result["summary"]["canary_balance_verified_on_A"] is True
    assert result["summary"]["canary_balance_verified_on_C"] is True
    assert result["summary"]["temporary_services_deleted"] is True
    assert result["summary"]["temporary_service_count"] == 3
    assert result["summary"]["application_mutation_count"] == 10
    assert opener.secret_bound is False
    assert opener.deleted == {
        "mother-mainnet-rpc-route-a",
        "mother-mainnet-rpc-route-c",
        "mainnet-canary1-verify-funded-c",
    }
    assert any("/logs" in path for _, _, path in opener.requests)
    assert not any("/deployments" in path for _, _, path in opener.requests)

    verified = verify_validator_rpc_canary_funding_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        operation=_operation("validator-rpc-canary-funding-evidence-verify"),
    )
    assert verified["clean"] is True
    assert verified["funding_mode"] == "funded"
    assert verified["funding_transaction_hash"] == opener.tx_hash
    assert verified["funding_receipt_verified_on_C"] is True
    assert verified["canary_balance_verified_on_A"] is True
    assert verified["result_channel"] == "service-detail-health+runtime-result-marker"

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


def test_route_writer_waits_through_early_exited_statuses(
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
    opener = _StatusHealthFundingOpener(delayed_route_writer_health_polls=4)

    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("validator-rpc-canary-funding-delayed-route-writer"),
    )

    assert result["status"] == "pass"
    evidence = json.loads(Path(result["evidence"]["path"]).read_text())
    route_wiring = evidence["allfather_rpc_route_wiring"]
    assert route_wiring["clean"] is True

    for route_key in ("a", "c"):
        proof = route_wiring["results"][route_key]
        assert proof["healthy"] is True
        assert proof["first_status"] == "exited"
        assert proof["final_status"] == "running:healthy:excluded"
        assert proof["observed_statuses"][:4] == ["exited"] * 4
        assert proof["observed_statuses"][-1] == "running:healthy:excluded"
        assert proof["observation_count"] == 5


def test_rpc_route_preflight_stops_before_temporary_services(
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
    opener = _StatusHealthFundingOpener(bad_rpc_route=True)
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-rpc-canary-funding-route-failed"),
    )
    assert result["status"] == "manual-review-required"
    assert result["chain_state"] == "unchanged-before-funder-start"
    assert result["summary"]["funding_performed"] is False
    assert result["summary"]["allfather_rpc_route_preflight_used"] is True
    assert result["summary"]["allfather_rpc_route_preflight_complete"] is False
    assert result["summary"]["temporary_service_count"] == 2
    assert result["summary"]["application_mutation_count"] == 6
    assert opener.secret_bound is False
    assert opener.deleted == {"mother-mainnet-rpc-route-a", "mother-mainnet-rpc-route-c"}
    assert any("/api/v1/services" in path for _, _, path in opener.requests)

    evidence = json.loads(Path(result["evidence"]["path"]).read_text())
    assert evidence["failure"]["code"] == "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_NO_BACKEND"
    preflight = evidence["allfather_rpc_route_preflight"]
    assert preflight["mode"] == "mother-shared-rpc-route-direct-json-rpc"
    assert preflight["routes"]["shared"]["rpc_url"] == "https://mainnet-rpc.greatlibrary.io"
    assert preflight["routes"]["shared"]["ok"] is False




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
    assert result["summary"]["temporary_service_count"] == 3
    assert result["summary"]["application_mutation_count"] == 9
    assert opener.secret_bound is False
    assert opener.deleted == {
        "mother-mainnet-rpc-route-a",
        "mother-mainnet-rpc-route-c",
        "mainnet-canary1-verify-reconciled-c",
    }
    verified = verify_validator_rpc_canary_funding_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        operation=_operation("validator-rpc-canary-funding-reconcile-verify"),
    )
    assert verified["funding_reconciled_from_prior_execution"] is True


def test_reconciled_c_verifier_accepts_service_health_without_runtime_marker(
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
    opener = _StatusHealthFundingOpener(already_funded=True, c_runtime_marker_missing=True)
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-rpc-canary-funding-reconcile-health-only"),
    )
    assert result["status"] == "pass"
    assert result["funding_mode"] == "already-funded"
    assert result["summary"]["funding_reconciled_from_prior_execution"] is True

    evidence = json.loads(Path(result["evidence"]["path"]).read_text())
    c_proof = evidence["runtime_proofs"]["c_reconciled_verifier"]
    assert c_proof["healthy"] is True
    assert c_proof["service_status"] == "running:healthy:excluded"
    assert c_proof["runtime_result_marker_observed"] is False
    assert c_proof["result_channel"] == "service-detail-health"
    assert evidence["cross_validator_verification"]["balance_verified"] is True
    assert evidence["cross_validator_verification"]["runtime_result_marker_observed"] is False


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
        "mother-mainnet-rpc-route-a",
        "mother-mainnet-rpc-route-c",
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



def test_reachable_rpc_with_unexpected_balance_stops_before_funder(
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
    opener = _StatusHealthFundingOpener(unexpected_balance=True)
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-rpc-canary-funding-unexpected-balance"),
    )
    assert result["status"] == "manual-review-required"
    assert result["chain_state"] == "unchanged-before-funder-start"
    assert result["summary"]["funding_performed"] is False
    assert result["summary"]["temporary_services_deleted"] is True
    assert result["summary"]["temporary_service_count"] == 2
    assert result["summary"]["application_mutation_count"] == 6
    assert opener.secret_bound is False
    assert opener.deleted == {
        "mother-mainnet-rpc-route-a",
        "mother-mainnet-rpc-route-c",
    }
    evidence = json.loads(Path(result["evidence"]["path"]).read_text())
    assert evidence["failure"]["code"] == (
        "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_UNEXPECTED_BALANCE"
    )


def test_rpc_probe_failure_stops_before_balance_classification(
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
    opener = _StatusHealthFundingOpener(bad_probe=True)
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-rpc-canary-funding-rpc-failed"),
    )
    assert result["status"] == "manual-review-required"
    assert result["chain_state"] == "unchanged-before-funder-start"
    assert result["summary"]["funding_performed"] is False
    assert result["summary"]["temporary_services_deleted"] is True
    assert result["summary"]["temporary_service_count"] == 2
    assert result["summary"]["application_mutation_count"] == 6
    assert opener.secret_bound is False
    assert opener.deleted == {
        "mother-mainnet-rpc-route-a",
        "mother-mainnet-rpc-route-c",
    }
    evidence = json.loads(Path(result["evidence"]["path"]).read_text())
    assert evidence["failure"]["code"] == (
        "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RPC_UNAVAILABLE"
    )



def test_runtime_marker_can_prove_success_when_healthcheck_does_not(
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
    opener = _StatusHealthFundingOpener(cast_probe_terminal_with_marker=True)
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-rpc-canary-funding-marker-first"),
    )
    assert result["status"] == "pass"
    evidence = json.loads(Path(result["evidence"]["path"]).read_text())
    balance_proof = evidence["runtime_proofs"]["a_balance_rpc_probe"]
    assert balance_proof["healthy"] is True
    assert balance_proof["result_channel"] == "local-json-rpc-shared-route"
    assert evidence["runtime_results"]["a_balance_rpc_probe"]["classification"] == "rpc-ok"


def test_runtime_log_fetch_tries_fallback_endpoints_when_first_logs_are_empty(
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
    opener = _StatusHealthFundingOpener(empty_application_logs=True)
    result = execute_validator_rpc_canary_funding_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("validator-rpc-canary-funding-log-fallback"),
    )
    assert result["status"] == "pass"
    evidence = json.loads(Path(result["evidence"]["path"]).read_text())
    c_log_observation = next(
        item
        for item in evidence["service_observations"]
        if item.get("phase") == "c_funded_verifier-runtime-result-marker"
    )
    assert c_log_observation["runtime_result_marker_observed"] is True
    assert c_log_observation["endpoint_kind"] in {
        "application-resource",
        "parent-service-fallback",
    }
    assert c_log_observation["attempts"][0]["http_status"] == 200
    assert c_log_observation["attempts"][0]["runtime_result_marker_count"] == 0



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


def test_sign_capped_transfer_falls_back_to_legacy_when_dynamic_fee_signing_is_unsupported(monkeypatch):
    calls = []

    class _FakeHash:
        def hex(self):
            return "0x" + ("2" * 64)

    class _FakeSigned:
        raw_transaction = b"\x12\x34"
        hash = _FakeHash()

    class _FakeAccount:
        address = "0x8333950bb66ab7e8b3bb4a56f8641b66849ecd05"

        @staticmethod
        def from_key(private_key):
            return _FakeAccount()

        @staticmethod
        def sign_transaction(transaction, private_key):
            calls.append(dict(transaction))
            if "maxFeePerGas" in transaction:
                raise TypeError("dynamic fee transaction type unsupported")
            return _FakeSigned()

    monkeypatch.setitem(sys.modules, "eth_account", types.SimpleNamespace(Account=_FakeAccount))
    monkeypatch.setitem(
        sys.modules,
        "eth_utils",
        types.SimpleNamespace(to_checksum_address=lambda value: value),
    )

    raw_transaction, tx_hash, transaction_type = funding_module._sign_capped_transfer(
        private_key="0x" + ("1" * 64),
        expected_source="0x8333950bb66ab7e8b3bb4a56f8641b66849ecd05",
        chain_id=42_424_240,
        nonce=7,
        destination="0xd0c503abb1e598ce155cd9c3c659f4733a6915a0",
        amount=742_000_000_000_000,
        gas_limit=21_000,
        max_fee_per_gas_wei=2_000_000_000,
        max_priority_fee_per_gas_wei=0,
    )

    assert raw_transaction == "0x1234"
    assert tx_hash == "0x" + ("2" * 64)
    assert transaction_type == "legacy-capped-gas-price"
    assert any(call.get("type") == "0x2" for call in calls)
    assert calls[-1]["gasPrice"] == 2_000_000_000
    assert "maxFeePerGas" not in calls[-1]



def test_sign_capped_transfer_accepts_bytes_transaction_hash_without_0x(monkeypatch):
    class _FakeSigned:
        raw_transaction = b"\x12\x34"
        hash = bytes.fromhex("3" * 64)

    class _FakeAccount:
        address = "0x8333950bb66ab7e8b3bb4a56f8641b66849ecd05"

        @staticmethod
        def from_key(private_key):
            return _FakeAccount()

        @staticmethod
        def sign_transaction(transaction, private_key):
            return _FakeSigned()

    monkeypatch.setitem(sys.modules, "eth_account", types.SimpleNamespace(Account=_FakeAccount))
    monkeypatch.setitem(
        sys.modules,
        "eth_utils",
        types.SimpleNamespace(to_checksum_address=lambda value: value),
    )

    raw_transaction, tx_hash, transaction_type = funding_module._sign_capped_transfer(
        private_key="0x" + ("1" * 64),
        expected_source="0x8333950bb66ab7e8b3bb4a56f8641b66849ecd05",
        chain_id=42_424_240,
        nonce=7,
        destination="0xd0c503abb1e598ce155cd9c3c659f4733a6915a0",
        amount=742_000_000_000_000,
        gas_limit=21_000,
        max_fee_per_gas_wei=2_000_000_000,
        max_priority_fee_per_gas_wei=0,
    )

    assert raw_transaction == "0x1234"
    assert tx_hash == "0x" + ("3" * 64)
    assert transaction_type == "eip1559-type-0x2"
