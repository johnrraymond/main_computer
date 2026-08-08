
from __future__ import annotations

import base64
import hashlib
import sys
import types
from pathlib import Path

import pytest
import yaml

from tools import mother_deploy
from tools.mother.common import deployment_validator_rpc_canary_execution as execution_module
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_validator_rpc_canary_execution import (
    build_validator_rpc_canary_release,
    execute_validator_rpc_canary_release,
    inspect_validator_rpc_canary_release,
    verify_validator_rpc_canary_evidence,
    verify_validator_rpc_canary_release,
    write_validator_rpc_canary_release,
)
from tests.test_mother_deployment_executor import _operation
from tests.test_mother_deployment_validator_rpc_canary import _fixture


def _fake_funding_evidence(paths, monkeypatch, canary_address: str):
    root = paths.root / "evidence" / "deployment-validator-rpc-canary-funding"
    root.mkdir(parents=True, exist_ok=True)
    document = {
        "kind": "synthetic-funding-evidence-for-execution-release-test",
        "chain_state": "exact-cross-validator-verified",
    }
    path = root / "synthetic-funding-evidence.json"
    payload = canonical_json(document)
    path.write_bytes(payload)
    file_sha = hashlib.sha256(payload).hexdigest()
    evidence_sha = "0" * 64
    funding_tx_hash = "0x" + "9" * 64

    def fake_verify(*args, **kwargs):
        return {
            "clean": True,
            "evidence_sha256": evidence_sha,
            "canary_address": canary_address,
            "canary_balance_verified_on_A": True,
            "canary_balance_verified_on_C": True,
            "funding_receipt_verified_on_C": True,
            "funding_reconciled_from_prior_execution": True,
            "funding_transaction_hash": funding_tx_hash,
            "funding_mode": "already-funded",
            "transaction_hash_recorded": True,
            "transfer_value_wei": 742_000_000_000_000,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_performed": False,
            "next_phase": "validator-rpc-canary-execution-release-not-yet-authorized",
        }

    monkeypatch.setattr(
        execution_module,
        "verify_validator_rpc_canary_funding_evidence",
        fake_verify,
    )
    return path, file_sha, evidence_sha, funding_tx_hash


def _execution_release_fixture(tmp_path: Path, monkeypatch):
    (
        paths,
        private_state,
        _soak_path,
        _soak,
        _identity,
        canary,
        canary_path,
        canary_digest,
    ) = _fixture(tmp_path, monkeypatch)
    canary_address = canary["identity"]["address"]
    funding_evidence_path, _file_sha, _evidence_sha, funding_tx_hash = _fake_funding_evidence(
        paths,
        monkeypatch,
        canary_address,
    )
    release = build_validator_rpc_canary_release(
        paths,
        private_state,
        canary_path,
        funding_evidence_path,
        acknowledged_transaction_sha256=canary_digest,
        operation=_operation("validator-rpc-canary-execution-release"),
    )
    release_path, release_digest = write_validator_rpc_canary_release(
        paths,
        release,
        operation=_operation("validator-rpc-canary-execution-release-write"),
    )
    return (
        paths,
        private_state,
        canary,
        canary_path,
        canary_digest,
        funding_evidence_path,
        funding_tx_hash,
        release,
        release_path,
        release_digest,
    )


def test_execution_release_consumes_clean_funding_without_authorizing_funding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        canary,
        _canary_path,
        _canary_digest,
        _funding_evidence_path,
        funding_tx_hash,
        release,
        release_path,
        release_digest,
    ) = _execution_release_fixture(tmp_path, monkeypatch)

    assert release["authority"]["canary_execution_authorized"] is True
    assert release["authority"]["funding_authorized"] is False
    assert release["execution"]["mode"] == "mother-local-python-shared-rpc-with-c-proxy-verifier"
    assert release["execution"]["shared_rpc_url"] == "https://mainnet-rpc.greatlibrary.io"
    assert release["funding_evidence"]["funding_transaction_hash"] == funding_tx_hash
    assert release["identity"]["address"] == canary["identity"]["address"]
    assert release["policy"]["validator_mutation_count"] == 0

    verified = verify_validator_rpc_canary_release(
        paths,
        private_state,
        release_path,
        operation=_operation("validator-rpc-canary-execution-release-verify"),
    )
    assert verified["clean"] is True
    assert verified["release_sha256"] == release_digest
    assert verified["canary_execution_authorized"] is True
    assert verified["funding_authorized"] is False
    assert verified["validator_mutation_count"] == 0

    inspected = inspect_validator_rpc_canary_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        operation=_operation("validator-rpc-canary-execution-inspect"),
    )
    assert inspected["release_already_claimed"] is False


def test_execution_apply_writes_cross_validator_evidence_without_validator_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _canary,
        _canary_path,
        _canary_digest,
        _funding_evidence_path,
        _funding_tx_hash,
        _release,
        release_path,
        release_digest,
    ) = _execution_release_fixture(tmp_path, monkeypatch)

    def fake_execute_local_python_canary(**kwargs):
        return {
            "phase": "a_validator_rpc_canary_execution-local-json-rpc-result",
            "healthy": True,
            "classification": "executed",
            "result_channel": "local-json-rpc-eth-account",
            "rpc_url": kwargs["rpc_url"],
            "chain_id": kwargs["chain_id"],
            "canary_address": kwargs["canary_address"],
            "self_tx_hash": "0x" + "1" * 64,
            "deploy_tx_hash": "0x" + "2" * 64,
            "write_tx_hash": "0x" + "3" * 64,
            "contract_address": "0x" + "4" * 40,
            "stored_value": "0x" + "0" * 62 + "2a",
            "block_before": 10,
            "block_after": 12,
            "base_fee_wei": 7,
            "balance_before_wei": 742_000_000_000_000,
            "observation_count": 1,
            "observations": [],
        }

    def fake_c_proxy_verifier(**kwargs):
        kwargs["receipts"].extend(
            [
                {
                    "mutation_id": "mainnet-canary1-execute-verify-c.create-service",
                    "controller_id": "coolify-c",
                    "method": "POST",
                    "endpoint": "/api/v1/services",
                    "status": "succeeded",
                    "live_write_acknowledged": True,
                },
                {
                    "mutation_id": "mainnet-canary1-execute-verify-c.delete",
                    "controller_id": "coolify-c",
                    "method": "DELETE",
                    "endpoint": "/api/v1/services/synthetic",
                    "status": "succeeded",
                    "live_write_acknowledged": True,
                },
            ]
        )
        return {
            "healthy": True,
            "service_name": "mainnet-canary1-execute-verify-c",
            "service_uuid": "synthetic",
            "service_status": "running:healthy:excluded",
            "result_channel": "service-detail-health",
        }

    monkeypatch.setattr(
        execution_module,
        "_execute_local_python_canary",
        fake_execute_local_python_canary,
    )
    monkeypatch.setattr(
        execution_module,
        "_run_c_proxy_verifier",
        fake_c_proxy_verifier,
    )

    result = execute_validator_rpc_canary_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        operation=_operation("validator-rpc-canary-execution-apply"),
    )
    assert result["status"] == "pass"
    assert result["chain_state"] == "exact-cross-validator-verified"
    assert result["summary"]["canary_execution_performed"] is True
    assert result["summary"]["canary_receipts_verified_on_C"] is True
    assert result["summary"]["temporary_services_deleted"] is True
    assert result["summary"]["funding_performed"] is False
    assert result["summary"]["validator_mutation_count"] == 0
    assert result["summary"]["validator_restart_count"] == 0
    assert result["summary"]["validator_vote_performed"] is False

    verified = verify_validator_rpc_canary_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        operation=_operation("validator-rpc-canary-execution-evidence"),
    )
    assert verified["clean"] is True
    assert verified["chain_state"] == "exact-cross-validator-verified"
    assert verified["canary_execution_performed"] is True
    assert verified["validator_mutation_count"] == 0


def test_local_python_canary_execution_uses_keyword_hex_quantity_fields_and_checksum_transaction_to(monkeypatch) -> None:
    calls: list[str] = []
    signed_to_values: list[str] = []
    call_reads = 0

    monkeypatch.setitem(
        sys.modules,
        "eth_utils",
        types.SimpleNamespace(to_checksum_address=lambda value: "0x" + str(value)[2:].upper()),
    )

    def fake_rpc_required_result(**kwargs):
        nonlocal call_reads
        method = kwargs["method"]
        calls.append(method)
        if method == "eth_chainId":
            return hex(42424240)
        if method == "eth_getBlockByNumber":
            return {"baseFeePerGas": "0x7", "number": "0x15092"}
        if method == "eth_getBalance":
            return hex(742_000_000_000_000)
        if method == "eth_getTransactionCount":
            return "0x0"
        if method == "eth_getCode":
            return "0x6001"
        if method == "eth_call":
            call_reads += 1
            return "0x" + ("0" * 63) + ("0" if call_reads == 1 else "1")
        if method == "eth_blockNumber":
            return "0x15096"
        raise AssertionError(method)

    def fake_sign_transaction(**kwargs):
        nonce = int(kwargs["transaction"]["nonce"])
        if "to" in kwargs["transaction"]:
            signed_to_values.append(kwargs["transaction"]["to"])
        return f"0xraw{nonce}", "0x" + str(nonce + 1) * 64, "eip1559-type-0x2"

    def fake_send_signed_transaction(**kwargs):
        return kwargs["expected_hash"]

    def fake_wait_for_receipt(**kwargs):
        tx_hash = kwargs["tx_hash"]
        if tx_hash == "0x" + "2" * 64:
            return {"status": "0x1", "contractAddress": "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"}
        return {"status": "0x1"}

    monkeypatch.setattr(execution_module, "_rpc_required_result", fake_rpc_required_result)
    monkeypatch.setattr(execution_module, "_sign_transaction", fake_sign_transaction)
    monkeypatch.setattr(execution_module, "_send_signed_transaction", fake_send_signed_transaction)
    monkeypatch.setattr(execution_module, "_wait_for_receipt", fake_wait_for_receipt)

    proof = execution_module._execute_local_python_canary(
        rpc_url="https://mainnet-rpc.greatlibrary.io",
        private_key="0x" + "1" * 64,
        canary_address="0xd0c503abb1e598ce155cd9c3c659f4733a6915a0",
        contract={
            "init_code": "0x60006000",
            "runtime_code": "0x6001",
            "initial_storage_word": "0x" + "0" * 64,
            "written_storage_word": "0x" + "0" * 63 + "1",
        },
        fee_policy={
            "base_fee_ceiling_wei": 2_000_000_000,
            "maximum_funding_requirement_wei": 742_000_000_000_000,
            "max_fee_per_gas_wei": 2_000_000_000,
            "max_priority_fee_per_gas_wei": 0,
            "gas_limits": {
                "signed_zero_value_self_transfer": 21_000,
                "minimal_contract_deployment": 60_000,
                "minimal_contract_storage_write": 45_000,
            },
        },
        chain_id=42424240,
        timeout=1.0,
        max_response_bytes=100_000,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=object(),
    )

    assert proof["healthy"] is True
    assert proof["classification"] == "executed"
    assert proof["block_before"] == int("0x15092", 16)
    assert proof["block_after"] == int("0x15096", 16)
    assert "eth_getTransactionCount" in calls
    assert signed_to_values == [
        "0xD0C503ABB1E598CE155CD9C3C659F4733A6915A0",
        "0xABCDEFABCDEFABCDEFABCDEFABCDEFABCDEFABCD",
    ]


def test_canary_execution_cli_exposes_release_apply_and_evidence(capsys) -> None:
    commands = {
        "release-validator-rpc-canary": "--funding-evidence",
        "verify-validator-rpc-canary-release": "--release",
        "apply-validator-rpc-canary": "--acknowledge-release-sha256",
        "verify-validator-rpc-canary-evidence": "--evidence",
    }
    for command, expected in commands.items():
        with pytest.raises(SystemExit) as caught:
            mother_deploy._parser().parse_args([command, "--help"])
        assert caught.value.code == 0
        assert expected in capsys.readouterr().out

def test_c_proxy_verifier_inlines_environment_and_avoids_conflicting_env_binds(monkeypatch) -> None:
    create_bodies: list[dict[str, object]] = []
    endpoints: list[str] = []

    monkeypatch.setattr(
        execution_module,
        "resolve_coolify_controller",
        lambda private_state, network, controller_id: {"controller": controller_id},
    )
    monkeypatch.setattr(
        execution_module,
        "_funding_controller",
        lambda private_state, controller_id: {"project_uuid": "project", "server_uuid": "server"},
    )
    monkeypatch.setattr(
        execution_module,
        "_resolve_environment_uuid",
        lambda **kwargs: "environment",
    )
    monkeypatch.setattr(
        execution_module,
        "_application_uuid",
        lambda payload: "service-uuid",
    )

    def fake_request_mutation(**kwargs):
        endpoints.append(kwargs["endpoint"])
        if kwargs["endpoint"] == "/api/v1/services":
            create_bodies.append(kwargs["body"])
            kwargs["receipts"].append(
                {
                    "mutation_id": kwargs["mutation_id"],
                    "controller_id": kwargs["controller_id"],
                    "method": kwargs["method"],
                    "endpoint": kwargs["endpoint"],
                    "status": "succeeded",
                    "live_write_acknowledged": True,
                }
            )
            return {"payload": {"uuid": "service-uuid"}}
        assert kwargs["endpoint"] == "/api/v1/services/service-uuid/start"
        kwargs["receipts"].append(
            {
                "mutation_id": kwargs["mutation_id"],
                "controller_id": kwargs["controller_id"],
                "method": kwargs["method"],
                "endpoint": kwargs["endpoint"],
                "status": "succeeded",
                "live_write_acknowledged": True,
            }
        )
        return {"payload": {}}

    monkeypatch.setattr(execution_module, "_request_mutation", fake_request_mutation)
    monkeypatch.setattr(
        execution_module,
        "_wait_for_service_health",
        lambda **kwargs: {
            "healthy": True,
            "service_name": kwargs["service_name"],
            "service_uuid": kwargs["service_uuid"],
            "service_status": "running:healthy:excluded",
        },
    )
    monkeypatch.setattr(execution_module, "_http", lambda *args, **kwargs: {"status": 204})

    receipts: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    result = execution_module._run_c_proxy_verifier(
        private_state=object(),
        canary_name="mainnet-canary1",
        a_result={
            "self_tx_hash": "0x" + "1" * 64,
            "deploy_tx_hash": "0x" + "2" * 64,
            "write_tx_hash": "0x" + "3" * 64,
            "contract_address": "0x" + "4" * 40,
            "block_after": 12,
        },
        timeout=1.0,
        max_response_bytes=100_000,
        max_wait_seconds=0.0,
        poll_interval_seconds=0.0,
        opener=object(),
        receipts=receipts,
        observations=observations,
    )

    assert result["healthy"] is True
    assert "/envs" not in " ".join(endpoints)
    assert endpoints == ["/api/v1/services", "/api/v1/services/service-uuid/start"]
    compose = base64.b64decode(str(create_bodies[0]["docker_compose_raw"])).decode("utf-8")
    document = yaml.safe_load(compose)
    environment = document["services"]["mainnet-canary1-execute-verify-c"]["environment"]
    assert environment["MC_MOTHER_CANARY_SELF_TX_HASH"] == "0x" + "1" * 64
    assert environment["MC_MOTHER_CANARY_CONTRACT_ADDRESS"] == "0x" + "4" * 40
    assert "${MC_MOTHER_CANARY_SELF_TX_HASH}" not in compose


def test_canary_execution_recovers_prior_A_result_without_resending_transactions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        canary,
        _canary_path,
        _canary_digest,
        _funding_evidence_path,
        _funding_tx_hash,
        release,
        release_path,
        release_digest,
    ) = _execution_release_fixture(tmp_path, monkeypatch)

    a_result = {
        "phase": "a_validator_rpc_canary_execution-local-json-rpc-result",
        "healthy": True,
        "classification": "executed",
        "result_channel": "local-json-rpc-eth-account",
        "rpc_url": release["execution"]["shared_rpc_url"],
        "chain_id": release["chain"]["chain_id"],
        "canary_address": canary["identity"]["address"],
        "self_tx_hash": "0x" + "1" * 64,
        "deploy_tx_hash": "0x" + "2" * 64,
        "write_tx_hash": "0x" + "3" * 64,
        "contract_address": "0x" + "4" * 40,
        "stored_value": release["canary_contract"]["written_storage_word"],
        "block_before": 10,
        "block_after": 12,
        "base_fee_wei": 7,
        "balance_before_wei": 742_000_000_000_000,
        "receipt_statuses": {"self": "0x1", "deploy": "0x1", "write": "0x1"},
        "observation_count": 1,
        "observations": [],
    }
    evidence = {
        "kind": execution_module._EVIDENCE_KIND,
        "schema_version": 1,
        "status": "manual-review-required",
        "started_at": execution_module._timestamp(),
        "completed_at": execution_module._timestamp(),
        "network": "mainnet",
        "mother_binding": execution_module._binding(private_state),
        "release": {
            "locator": execution_module._relative(paths, release_path, "validator-RPC canary release"),
            "sha256": release_digest,
        },
        "chain": dict(release["chain"]),
        "canary_address": canary["identity"]["address"],
        "funding_evidence": dict(release["funding_evidence"]),
        "chain_state": "exact-on-A-not-yet-verified-on-C",
        "cross_validator_verification": None,
        "mutation_receipts": [],
        "service_observations": [],
        "runtime_proofs": {"a_validator_rpc_canary_execution": a_result},
        "runtime_results": {},
        "failure": {"code": "synthetic", "message": "C env bind failed"},
        "summary": {
            "clean": False,
            "complete": False,
            "canary_execution_performed": True,
            "funding_performed": False,
            "funding_evidence_consumed": True,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_performed": False,
        },
    }
    evidence["validator_rpc_canary_evidence_sha256"] = execution_module._digest_without(
        evidence,
        "validator_rpc_canary_evidence_sha256",
    )
    recovery_path = paths.root / "evidence" / "deployment-validator-rpc-canary" / "synthetic-recovery.json"
    recovery_path.parent.mkdir(parents=True, exist_ok=True)
    recovery_path.write_bytes(canonical_json(evidence))

    def fail_execute_local_python_canary(**kwargs):
        raise AssertionError("recovery path must not resend validator-RPC canary transactions")

    def fake_c_proxy_verifier(**kwargs):
        kwargs["receipts"].extend(
            [
                {
                    "mutation_id": "mainnet-canary1-execute-verify-c.create-service",
                    "controller_id": "coolify-c",
                    "method": "POST",
                    "endpoint": "/api/v1/services",
                    "status": "succeeded",
                    "live_write_acknowledged": True,
                },
                {
                    "mutation_id": "mainnet-canary1-execute-verify-c.start",
                    "controller_id": "coolify-c",
                    "method": "POST",
                    "endpoint": "/api/v1/services/synthetic/start",
                    "status": "succeeded",
                    "live_write_acknowledged": True,
                },
                {
                    "mutation_id": "mainnet-canary1-execute-verify-c.delete",
                    "controller_id": "coolify-c",
                    "method": "DELETE",
                    "endpoint": "/api/v1/services/synthetic",
                    "status": "succeeded",
                    "live_write_acknowledged": True,
                },
            ]
        )
        return {
            "healthy": True,
            "service_name": "mainnet-canary1-execute-verify-c",
            "service_uuid": "synthetic",
            "service_status": "running:healthy:excluded",
            "result_channel": "service-detail-health",
        }

    monkeypatch.setattr(
        execution_module,
        "_execute_local_python_canary",
        fail_execute_local_python_canary,
    )
    monkeypatch.setattr(
        execution_module,
        "_run_c_proxy_verifier",
        fake_c_proxy_verifier,
    )

    result = execute_validator_rpc_canary_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        recovery_evidence_path=recovery_path,
        operation=_operation("validator-rpc-canary-execution-apply-recovery"),
    )

    assert result["status"] == "pass"
    assert result["chain_state"] == "exact-cross-validator-verified"
    assert result["summary"]["canary_execution_performed"] is True
    assert result["summary"]["canary_execution_recovered_from_prior_execution"] is True
    assert result["summary"]["funding_performed"] is False
    assert result["summary"]["validator_mutation_count"] == 0

