
'''Release and execute the Mother validator-RPC operations canary.'''

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_post_admission_steady_state import (
    _binding,
    _canonical_under,
    _ensure_root,
    _mapping,
    _parse_utc,
    _relative,
    _resolve,
    _timestamp,
)
from .deployment_validator_admission_executor import _http
from .deployment_validator_rpc_canary import (
    _ADDRESS_RE,
    _C as _C_NODE,
    _C_CONTROLLER,
    _CONTRACT_INIT,
    _CONTRACT_RUNTIME,
    _EXPECTED_VALUE,
    _IDENTITY_DIRECTORY,
    _MAXIMUM_FUNDING_REQUIREMENT_WEI,
    _SECRET_ENV,
    _TRANSACTION_DIRECTORY,
    verify_validator_rpc_canary_transaction,
)
from .deployment_validator_rpc_canary_funding import (
    _application_uuid,
    _controller as _funding_controller,
    _hex_quantity_to_int,
    _network_state,
    _receipt,
    _request_mutation,
    _resolve_environment_uuid,
    _rpc_required_result,
    _shared_rpc_route_url,
    _signed_transaction_parts,
    _wait_for_service_health,
    verify_validator_rpc_canary_funding_evidence,
)
from .ethereum_identity import is_private_key, private_key_to_address
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path

_RELEASE_KIND = "main_computer.mother.deployment_validator_rpc_canary_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_validator_rpc_canary_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_validator_rpc_canary_evidence.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-validator-rpc-canary-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-validator-rpc-canary-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-validator-rpc-canary")
_FUNDING_EVIDENCE_DIRECTORY = ("evidence", "deployment-validator-rpc-canary-funding")
_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


class MotherDeploymentValidatorRpcCanaryExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error(code: str, message: str) -> MotherDeploymentValidatorRpcCanaryExecutionError:
    return MotherDeploymentValidatorRpcCanaryExecutionError(code, message)


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({k: v for k, v in document.items() if k != field})).hexdigest()


def _root_for(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return paths.root / parts[0] / parts[1]


def _release_duration(value: int) -> int:
    seconds = int(value)
    if seconds < 30 or seconds > 3600:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RELEASE_INVALID", "release TTL must be between 30 and 3600 seconds")
    return seconds


def _address(value: Any, path: str) -> str:
    if type(value) is not str or _ADDRESS_RE.fullmatch(value) is None:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_INVALID", f"{path} must be a 20-byte Ethereum address")
    return value.lower()


def _transaction_address(value: str) -> str:
    try:
        from eth_utils import to_checksum_address  # type: ignore
    except Exception:
        return value
    try:
        return str(to_checksum_address(value))
    except Exception:
        return value


def _normalize_tx_hash(value: Any, *, code: str, message: str) -> str:
    if type(value) is str and _TX_HASH_RE.fullmatch(value):
        return value.lower()
    raise _error(code, message)


def _load_canary_identity(paths: PrivateStatePaths, canary_transaction: Mapping[str, Any]) -> dict[str, Any]:
    identity = _mapping(canary_transaction.get("identity"), "canary.identity")
    identity_path = _resolve(paths, identity.get("identity_locator"), _IDENTITY_DIRECTORY, "validator-RPC canary identity")
    document, _, file_sha = _canonical_under(paths, identity_path, _IDENTITY_DIRECTORY, "validator-RPC canary identity")
    private_key = document.get("private_key")
    address = _address(document.get("address"), "identity.address")
    expected = _address(identity.get("address"), "canary.identity.address")
    if address != expected or not is_private_key(private_key) or private_key_to_address(private_key).lower() != address:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_IDENTITY_INVALID", "validator-RPC canary identity private key is missing or contradictory")
    return {"path": identity_path, "file_sha256": file_sha, "address": address, "private_key": private_key}


def _funding_evidence_binding(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    funding_evidence_path: Path,
    *,
    selected_nodes: Iterable[str],
    max_age_seconds: int,
    transaction_max_age_seconds: int,
    canary_transaction_max_age_seconds: int,
    soak_max_age_seconds: int,
    operation: OperationIdentity,
) -> dict[str, Any]:
    verified = verify_validator_rpc_canary_funding_evidence(
        paths,
        private_state,
        funding_evidence_path,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        canary_transaction_max_age_seconds=canary_transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
        operation=operation,
    )
    if not (
        verified.get("clean") is True
        and verified.get("canary_balance_verified_on_A") is True
        and verified.get("canary_balance_verified_on_C") is True
        and verified.get("funding_receipt_verified_on_C") is True
        and verified.get("transaction_hash_recorded") is True
        and verified.get("validator_mutation_count") == 0
        and verified.get("validator_restart_count") == 0
        and verified.get("validator_vote_performed") is False
    ):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID", "validator-RPC canary execution requires clean completed funding evidence")
    document, _, file_sha = _canonical_under(paths, Path(funding_evidence_path), _FUNDING_EVIDENCE_DIRECTORY, "validator-RPC canary funding evidence")
    return {
        "locator": _relative(paths, Path(funding_evidence_path).resolve(strict=False), "validator-RPC canary funding evidence"),
        "file_sha256": file_sha,
        "sha256": verified["evidence_sha256"],
        "canary_address": verified["canary_address"],
        "funding_transaction_hash": verified["funding_transaction_hash"],
        "transfer_value_wei": verified["transfer_value_wei"],
        "chain_state": document.get("chain_state"),
        "next_phase": verified.get("next_phase"),
    }


def build_validator_rpc_canary_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    funding_evidence_path: Path,
    *,
    acknowledged_transaction_sha256: str,
    selected_nodes: Iterable[str] = (),
    transaction_max_age_seconds: int = 86400,
    funding_evidence_max_age_seconds: int = 86400,
    funding_transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    verified_tx = verify_validator_rpc_canary_transaction(
        paths,
        private_state,
        transaction_path,
        selected_nodes=selected_nodes,
        max_age_seconds=transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
        operation=operation,
    )
    if acknowledged_transaction_sha256 != verified_tx["transaction_sha256"]:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_ACK_MISMATCH", "acknowledged validator-RPC canary transaction digest does not match")
    canary_transaction, _, tx_file_sha = _canonical_under(paths, Path(transaction_path), _TRANSACTION_DIRECTORY, "validator-RPC canary transaction")
    funding = _funding_evidence_binding(
        paths,
        private_state,
        Path(funding_evidence_path),
        selected_nodes=selected_nodes,
        max_age_seconds=funding_evidence_max_age_seconds,
        transaction_max_age_seconds=funding_transaction_max_age_seconds,
        canary_transaction_max_age_seconds=transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
        operation=operation,
    )
    identity = _mapping(canary_transaction.get("identity"), "canary.identity")
    if _address(identity.get("address"), "canary.identity.address") != funding["canary_address"] or funding["transfer_value_wei"] != _MAXIMUM_FUNDING_REQUIREMENT_WEI:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_BINDING_INVALID", "funding evidence does not bind the current canary identity and funding amount")
    created = _parse_utc(_timestamp(created_at), "release.created_at")
    ttl = _release_duration(expires_in_seconds)
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expires_at": (created + timedelta(seconds=ttl)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "network": "mainnet",
        "mother_binding": _binding(private_state),
        "transaction": {
            "locator": _relative(paths, Path(transaction_path).resolve(strict=False), "validator-RPC canary transaction"),
            "file_sha256": tx_file_sha,
            "sha256": verified_tx["transaction_sha256"],
        },
        "funding_evidence": dict(funding),
        "chain": dict(canary_transaction["chain"]),
        "identity": dict(identity),
        "canary_contract": dict(canary_transaction["canary_contract"]),
        "fee_policy": dict(canary_transaction["fee_policy"]),
        "execution": {
            "mode": "mother-local-python-shared-rpc-with-c-proxy-verifier",
            "shared_rpc_url": _shared_rpc_route_url(_network_state(private_state)),
            "a_result_channel": "local-json-rpc-eth-account",
            "c_result_channel": "service-detail-health",
            "c_backend_rpc_url": f"http://{_C_NODE}:8545",
            "canary_execution_authorized": True,
            "funding_authorized": False,
            "resend_funding_authorized": False,
        },
        "authority": {
            "requested_use_limit": 1,
            "network_access_authorized": True,
            "live_execution_authorized": True,
            "canary_execution_authorized": True,
            "funding_authorized": False,
            "validator_vote_authorized": False,
            "validator_mutation_authorized": False,
            "validator_restart_authorized": False,
            "public_endpoint_authorized": False,
            "ssh_authorized": False,
        },
        "policy": {
            "completed_funding_evidence_required": True,
            "shared_rpc_route_required": True,
            "local_python_eth_account_required": True,
            "zero_value_self_transfer_required": True,
            "minimal_contract_deploy_required": True,
            "minimal_contract_storage_write_required": True,
            "cross_validator_receipt_code_state_verification_required": True,
            "temporary_C_application_must_be_deleted": True,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_authorized": False,
        },
    }
    release["validator_rpc_canary_release_sha256"] = _digest_without(release, "validator_rpc_canary_release_sha256")
    return release


def write_validator_rpc_canary_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(document, "validator_rpc_canary_release_sha256")
    if not (document.get("kind") == _RELEASE_KIND and document.get("schema_version") == 1 and document.get("validator_rpc_canary_release_sha256") == digest):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RELEASE_INVALID", "validator-RPC canary release is malformed")
    root = _ensure_root(paths, _RELEASE_DIRECTORY, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "release"
    destination = root / f"{stamp}-{digest[:16]}.json"
    payload = canonical_json(document)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_CONFLICT", "validator-RPC canary release destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_validator_rpc_canary_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    funding_evidence_max_age_seconds: int = 86400,
    funding_transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    document, _, file_sha = _canonical_under(paths, Path(release_path), _RELEASE_DIRECTORY, "validator-RPC canary release")
    digest = _digest_without(document, "validator_rpc_canary_release_sha256")
    if not (document.get("kind") == _RELEASE_KIND and document.get("schema_version") == 1 and document.get("validator_rpc_canary_release_sha256") == digest and document.get("mother_binding") == _binding(private_state)):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RELEASE_INVALID", "validator-RPC canary release is modified, stale, or contradictory")
    created = _parse_utc(document.get("created_at"), "release.created_at")
    expires = _parse_utc(document.get("expires_at"), "release.expires_at")
    current = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    age = int((current - created).total_seconds())
    if age < -15 or age > max_age_seconds or current > expires:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RELEASE_EXPIRED", "validator-RPC canary release is outside its active window")
    tx_ref = _mapping(document.get("transaction"), "release.transaction")
    tx_path = _resolve(paths, tx_ref.get("locator"), _TRANSACTION_DIRECTORY, "validator-RPC canary transaction")
    verified_tx = verify_validator_rpc_canary_transaction(paths, private_state, tx_path, selected_nodes=selected_nodes, max_age_seconds=transaction_max_age_seconds, soak_max_age_seconds=soak_max_age_seconds, operation=operation)
    canary_transaction, _, tx_file_sha = _canonical_under(paths, tx_path, _TRANSACTION_DIRECTORY, "validator-RPC canary transaction")
    funding_ref = _mapping(document.get("funding_evidence"), "release.funding_evidence")
    funding_path = _resolve(paths, funding_ref.get("locator"), _FUNDING_EVIDENCE_DIRECTORY, "validator-RPC canary funding evidence")
    funding = _funding_evidence_binding(paths, private_state, funding_path, selected_nodes=selected_nodes, max_age_seconds=funding_evidence_max_age_seconds, transaction_max_age_seconds=funding_transaction_max_age_seconds, canary_transaction_max_age_seconds=transaction_max_age_seconds, soak_max_age_seconds=soak_max_age_seconds, operation=operation)
    authority = _mapping(document.get("authority"), "release.authority")
    execution = _mapping(document.get("execution"), "release.execution")
    policy = _mapping(document.get("policy"), "release.policy")
    if not (
        tx_ref.get("sha256") == verified_tx["transaction_sha256"]
        and tx_ref.get("file_sha256") == tx_file_sha
        and funding_ref.get("sha256") == funding["sha256"]
        and funding_ref.get("file_sha256") == funding["file_sha256"]
        and document.get("chain") == canary_transaction.get("chain")
        and document.get("identity") == canary_transaction.get("identity")
        and document.get("canary_contract") == canary_transaction.get("canary_contract")
        and document.get("fee_policy") == canary_transaction.get("fee_policy")
        and execution.get("mode") == "mother-local-python-shared-rpc-with-c-proxy-verifier"
        and execution.get("shared_rpc_url") == _shared_rpc_route_url(_network_state(private_state))
        and authority.get("requested_use_limit") == 1
        and authority.get("canary_execution_authorized") is True
        and authority.get("funding_authorized") is False
        and authority.get("validator_mutation_authorized") is False
        and authority.get("validator_restart_authorized") is False
        and authority.get("validator_vote_authorized") is False
        and policy.get("completed_funding_evidence_required") is True
        and policy.get("cross_validator_receipt_code_state_verification_required") is True
        and policy.get("validator_mutation_count") == 0
    ):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RELEASE_INVALID", "validator-RPC canary release does not match its verified transaction and funding proof")
    return {
        "clean": True,
        "network": "mainnet",
        "release_path": str(Path(release_path).resolve(strict=False)),
        "release_file_sha256": file_sha,
        "release_sha256": digest,
        "age_seconds": max(0, age),
        "expires_at": document["expires_at"],
        "chain_id": document["chain"]["chain_id"],
        "canary_address": funding["canary_address"],
        "funding_transaction_hash": funding["funding_transaction_hash"],
        "execution_mode": execution["mode"],
        "shared_rpc_url": execution["shared_rpc_url"],
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_authorized": False,
        "canary_execution_authorized": True,
        "funding_authorized": False,
        "result_channel": "local-json-rpc-eth-account+service-detail-health",
    }


def inspect_validator_rpc_canary_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    funding_evidence_max_age_seconds: int = 86400,
    funding_transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    operation: OperationIdentity,
) -> dict[str, Any]:
    verified = verify_validator_rpc_canary_release(paths, private_state, release_path, selected_nodes=selected_nodes, max_age_seconds=max_age_seconds, transaction_max_age_seconds=transaction_max_age_seconds, funding_evidence_max_age_seconds=funding_evidence_max_age_seconds, funding_transaction_max_age_seconds=funding_transaction_max_age_seconds, soak_max_age_seconds=soak_max_age_seconds, operation=operation)
    if acknowledged_release_sha256 != verified["release_sha256"]:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_ACK_MISMATCH", "acknowledged validator-RPC canary release digest does not match")
    claim_path = _root_for(paths, _CLAIM_DIRECTORY) / f"{verified['release_sha256']}.json"
    return {**verified, "release_already_claimed": claim_path.exists(), "network_access_performed": False, "live_mutation_performed": False, "canary_execution_performed": False, "funding_performed": False, "validator_vote_performed": False}


def _sign_transaction(*, private_key: str, expected_source: str, transaction: Mapping[str, Any]) -> tuple[str, str, str]:
    try:
        from eth_account import Account  # type: ignore
    except Exception as exc:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_SIGNER_UNAVAILABLE", "Python eth_account is unavailable in the Mother runtime") from exc
    account = Account.from_key(private_key)
    if str(account.address).lower() != expected_source.lower():
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_IDENTITY_INVALID", "canary private key does not derive the expected canary address")
    variants = [
        ("eip1559-type-0x2", {"type": "0x2", **dict(transaction)}),
        ("eip1559-type-2", {"type": 2, **dict(transaction)}),
        ("eip1559-inferred", dict(transaction)),
        ("legacy-capped-gas-price", {k: v for k, v in dict(transaction).items() if k not in {"maxFeePerGas", "maxPriorityFeePerGas", "accessList"}} | {"gasPrice": int(transaction["maxFeePerGas"])}),
    ]
    failures: list[str] = []
    for transaction_type, tx in variants:
        try:
            signed = Account.sign_transaction(tx, private_key)
            return _signed_transaction_parts(signed, transaction_type=transaction_type)
        except Exception as exc:
            failures.append(f"{transaction_type}:{type(exc).__name__}:{str(exc)[:120]}")
    raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_SIGNING_FAILED", "eth_account could not sign the validator-RPC canary transactions: " + "; ".join(failures[:4]))


def _wait_for_receipt(*, rpc_url: str, tx_hash: str, timeout: float, max_response_bytes: int, opener: Any, max_wait_seconds: float, poll_interval_seconds: float, observations: list[dict[str, Any]]) -> Mapping[str, Any]:
    started = time.monotonic()
    while True:
        receipt = _rpc_required_result(rpc_url=rpc_url, method="eth_getTransactionReceipt", params=[tx_hash], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECEIPT_RPC_FAILED", message="shared RPC did not answer an execution receipt query", observations=observations)
        if isinstance(receipt, Mapping):
            if str(receipt.get("status")).lower() != "0x1":
                raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECEIPT_FAILED", "validator-RPC canary transaction receipt was not successful")
            return receipt
        if time.monotonic() - started >= max_wait_seconds:
            raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECEIPT_TIMEOUT", "validator-RPC canary transaction receipt did not appear before the deadline")
        time.sleep(min(poll_interval_seconds, max(0.0, max_wait_seconds - (time.monotonic() - started))))


def _send_signed_transaction(*, rpc_url: str, raw: str, expected_hash: str, timeout: float, max_response_bytes: int, opener: Any, observations: list[dict[str, Any]]) -> str:
    result = _rpc_required_result(rpc_url=rpc_url, method="eth_sendRawTransaction", params=[raw], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_SEND_FAILED", message="shared RPC rejected the validator-RPC canary raw transaction", observations=observations)
    returned = _normalize_tx_hash(result, code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_SEND_HASH_INVALID", message="shared RPC returned an invalid validator-RPC canary transaction hash")
    if returned != expected_hash.lower():
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_SEND_HASH_MISMATCH", "shared RPC returned a different validator-RPC canary transaction hash")
    return returned


def _execute_local_python_canary(
    *,
    rpc_url: str,
    private_key: str,
    canary_address: str,
    contract: Mapping[str, Any],
    fee_policy: Mapping[str, Any],
    chain_id: int,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    chain_hex = _rpc_required_result(rpc_url=rpc_url, method="eth_chainId", params=[], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_CHAIN_ID_RPC_FAILED", message="shared RPC did not answer eth_chainId", observations=observations)
    if _hex_quantity_to_int(chain_hex, field="chain id") != chain_id:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_CHAIN_ID_MISMATCH", "shared RPC chain ID does not match the canary release")
    block_before = _rpc_required_result(rpc_url=rpc_url, method="eth_getBlockByNumber", params=["latest", False], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_BLOCK_RPC_FAILED", message="shared RPC did not return latest block before canary execution", observations=observations)
    if not isinstance(block_before, Mapping):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_BLOCK_INVALID", "latest block before canary execution is not an object")
    base_fee = _hex_quantity_to_int(block_before.get("baseFeePerGas"), field="baseFeePerGas")
    if base_fee > int(fee_policy["base_fee_ceiling_wei"]):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_BASE_FEE_TOO_HIGH", "latest block base fee exceeds the canary release ceiling")
    balance_before = _hex_quantity_to_int(_rpc_required_result(rpc_url=rpc_url, method="eth_getBalance", params=[canary_address, "latest"], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_BALANCE_RPC_FAILED", message="shared RPC did not answer canary balance before execution", observations=observations), field="canary balance")
    if balance_before < int(fee_policy["maximum_funding_requirement_wei"]):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_BALANCE_TOO_LOW", "funded canary balance is below the exact maximum execution requirement")
    nonce = _hex_quantity_to_int(_rpc_required_result(rpc_url=rpc_url, method="eth_getTransactionCount", params=[canary_address, "pending"], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_NONCE_RPC_FAILED", message="shared RPC did not answer canary nonce before execution", observations=observations), field="canary nonce")
    common = {"chainId": chain_id, "maxFeePerGas": int(fee_policy["max_fee_per_gas_wei"]), "maxPriorityFeePerGas": int(fee_policy["max_priority_fee_per_gas_wei"]), "accessList": []}
    self_raw, self_hash, self_type = _sign_transaction(private_key=private_key, expected_source=canary_address, transaction={**common, "nonce": nonce, "to": _transaction_address(canary_address), "value": 0, "gas": int(fee_policy["gas_limits"]["signed_zero_value_self_transfer"]), "data": "0x"})
    _send_signed_transaction(rpc_url=rpc_url, raw=self_raw, expected_hash=self_hash, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, observations=observations)
    self_receipt = _wait_for_receipt(rpc_url=rpc_url, tx_hash=self_hash, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, max_wait_seconds=max_wait_seconds, poll_interval_seconds=poll_interval_seconds, observations=observations)
    deploy_raw, deploy_hash, deploy_type = _sign_transaction(private_key=private_key, expected_source=canary_address, transaction={**common, "nonce": nonce + 1, "value": 0, "gas": int(fee_policy["gas_limits"]["minimal_contract_deployment"]), "data": str(contract["init_code"])})
    _send_signed_transaction(rpc_url=rpc_url, raw=deploy_raw, expected_hash=deploy_hash, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, observations=observations)
    deploy_receipt = _wait_for_receipt(rpc_url=rpc_url, tx_hash=deploy_hash, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, max_wait_seconds=max_wait_seconds, poll_interval_seconds=poll_interval_seconds, observations=observations)
    contract_address = _address(deploy_receipt.get("contractAddress"), "deploy_receipt.contractAddress")
    code = str(_rpc_required_result(rpc_url=rpc_url, method="eth_getCode", params=[contract_address, "latest"], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_CODE_RPC_FAILED", message="shared RPC did not return canary contract code", observations=observations)).lower()
    if code != str(contract["runtime_code"]).lower():
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_CODE_MISMATCH", "deployed canary runtime bytecode does not match the release commitment")
    read0 = str(_rpc_required_result(rpc_url=rpc_url, method="eth_call", params=[{"to": contract_address, "data": "0x"}, "latest"], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_CALL_RPC_FAILED", message="shared RPC did not return initial canary storage", observations=observations)).lower()
    if read0 != str(contract["initial_storage_word"]).lower():
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_INITIAL_STORAGE_MISMATCH", "deployed canary initial storage does not match the release commitment")
    write_raw, write_hash, write_type = _sign_transaction(private_key=private_key, expected_source=canary_address, transaction={**common, "nonce": nonce + 2, "to": _transaction_address(contract_address), "value": 0, "gas": int(fee_policy["gas_limits"]["minimal_contract_storage_write"]), "data": str(contract["written_storage_word"])})
    _send_signed_transaction(rpc_url=rpc_url, raw=write_raw, expected_hash=write_hash, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, observations=observations)
    write_receipt = _wait_for_receipt(rpc_url=rpc_url, tx_hash=write_hash, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, max_wait_seconds=max_wait_seconds, poll_interval_seconds=poll_interval_seconds, observations=observations)
    read1 = str(_rpc_required_result(rpc_url=rpc_url, method="eth_call", params=[{"to": contract_address, "data": "0x"}, "latest"], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_CALL_RPC_FAILED", message="shared RPC did not return written canary storage", observations=observations)).lower()
    if read1 != str(contract["written_storage_word"]).lower():
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_WRITTEN_STORAGE_MISMATCH", "written canary storage does not match the release commitment")
    block_after = _hex_quantity_to_int(_rpc_required_result(rpc_url=rpc_url, method="eth_blockNumber", params=[], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_BLOCK_RPC_FAILED", message="shared RPC did not answer block number after canary execution", observations=observations), field="block_after")
    block_before_number = _hex_quantity_to_int(block_before.get("number"), field="block_before.number")
    return {"phase": "a_validator_rpc_canary_execution-local-json-rpc-result", "healthy": True, "classification": "executed", "result_channel": "local-json-rpc-eth-account", "rpc_url": rpc_url, "chain_id": chain_id, "canary_address": canary_address, "self_tx_hash": self_hash, "deploy_tx_hash": deploy_hash, "write_tx_hash": write_hash, "contract_address": contract_address, "stored_value": read1, "block_before": block_before_number, "block_after": block_after, "base_fee_wei": base_fee, "balance_before_wei": balance_before, "transaction_types": {"self": self_type, "deploy": deploy_type, "write": write_type}, "receipt_statuses": {"self": self_receipt.get("status"), "deploy": deploy_receipt.get("status"), "write": write_receipt.get("status")}, "observation_count": len(observations), "observations": observations, "proof": "Mother signed, sent, mined, and locally verified the validator-RPC canary through the shared RPC route"}


_C_PROXY_VERIFIER_PY = r'''
from __future__ import annotations
import http.client, json, os, re, shlex, socket, urllib.parse
TARGET_HOST = "mainnetc-super1"
TARGET_PORT = 8545
RUNTIME = os.environ["MC_MOTHER_CANARY_RUNTIME_CODE"].lower()
VALUE = os.environ["MC_MOTHER_CANARY_WRITTEN_STORAGE_WORD"].lower()
SELF_TX = os.environ["MC_MOTHER_CANARY_SELF_TX_HASH"].lower()
DEPLOY_TX = os.environ["MC_MOTHER_CANARY_DEPLOY_TX_HASH"].lower()
WRITE_TX = os.environ["MC_MOTHER_CANARY_WRITE_TX_HASH"].lower()
CONTRACT = os.environ["MC_MOTHER_CANARY_CONTRACT_ADDRESS"].lower()
SOURCE_BLOCK = int(os.environ["MC_MOTHER_CANARY_SOURCE_BLOCK_AFTER"])
MARKER = "MOTHER_VALIDATOR_RPC_CANARY_C_RESULT"
class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("localhost"); self.socket_path = socket_path
    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); sock.connect(self.socket_path); self.sock = sock
def docker(method: str, path: str, body=None):
    payload = None; headers = {}
    if body is not None:
        payload = json.dumps(body).encode("utf-8"); headers["Content-Type"] = "application/json"
    conn = UnixHTTPConnection("/var/run/docker.sock"); conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse(); raw = resp.read(); status = int(resp.status); text = raw.decode("utf-8", "replace") if raw else ""
    if status >= 400: raise SystemExit(f"docker-api-{method}-{path}-status-{status}:{text[:300]}")
    return json.loads(text) if text else None
def find_proxy_id() -> str:
    payload = docker("GET", "/containers/json?all=1")
    if not isinstance(payload, list): raise SystemExit("docker-containers-json-not-list")
    candidates = []
    for item in payload:
        if not isinstance(item, dict): continue
        cid = str(item.get("Id") or ""); names = [str(name or "").lstrip("/") for name in item.get("Names") or []]; state = str(item.get("State") or "")
        if any(name == "coolify-proxy" or name.endswith("_coolify-proxy") or "coolify-proxy" in name for name in names): candidates.append({"id": cid, "state": state})
    if not candidates: raise SystemExit("coolify-proxy-not-found")
    running = [item for item in candidates if item["state"] == "running"]
    return str((running[0] if running else candidates[0])["id"])
def docker_exec(container_id: str, script: str) -> int:
    payload = docker("POST", f"/containers/{urllib.parse.quote(container_id, safe='')}/exec", {"AttachStdout": True, "AttachStderr": True, "Cmd": ["sh", "-lc", script]})
    if not isinstance(payload, dict) or not payload.get("Id"): raise SystemExit("docker-exec-create-no-id")
    exec_id = str(payload["Id"]); docker("POST", f"/exec/{urllib.parse.quote(exec_id, safe='')}/start", {"Detach": False, "Tty": False})
    detail = docker("GET", f"/exec/{urllib.parse.quote(exec_id, safe='')}/json")
    if not isinstance(detail, dict): raise SystemExit("docker-exec-inspect-not-object")
    code = detail.get("ExitCode"); return int(code) if code is not None else 999
def q(value: str) -> str: return shlex.quote(value)
def payload(method: str, params: list[object]) -> str: return json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
def receipt_payload(tx_hash: str) -> str: return payload("eth_getTransactionReceipt", [tx_hash])
def marker(classification: str, **fields: object) -> None:
    parts = [MARKER, "step=c_validator_rpc_canary_verifier", f"classification={classification}"]
    for key, value in fields.items():
        text = str(value)
        if re.fullmatch(r"[A-Za-z0-9_.:/-]{0,256}", text): parts.append(f"{key}={text}")
    print(" ".join(parts), flush=True)
for tx in (SELF_TX, DEPLOY_TX, WRITE_TX):
    if not re.fullmatch(r"0x[0-9a-f]{64}", tx): marker("bad-tx-hash"); raise SystemExit(70)
if not re.fullmatch(r"0x[0-9a-f]{40}", CONTRACT): marker("bad-contract"); raise SystemExit(71)
backend_url = f"http://{TARGET_HOST}:{int(TARGET_PORT)}"; proxy_id = find_proxy_id()
checks = []
checks.append("chain=$(wget -q -T 8 -O- --header='Content-Type: application/json' --post-data=" + q(payload("eth_chainId", [])) + " " + q(backend_url) + " 2>/dev/null || true); printf '%s' \"$chain\" | grep -Eq '\"result\"[[:space:]]*:[[:space:]]*\"0x28757b0\"'")
for tx in (SELF_TX, DEPLOY_TX, WRITE_TX):
    checks.append("ok=0; deadline=$(( $(date +%s) + 120 )); while [ \"$(date +%s)\" -le \"$deadline\" ]; do out=$(wget -q -T 8 -O- --header='Content-Type: application/json' --post-data=" + q(receipt_payload(tx)) + " " + q(backend_url) + " 2>/dev/null || true); if printf '%s' \"$out\" | grep -Eq '\"status\"[[:space:]]*:[[:space:]]*\"0x1\"'; then ok=1; break; fi; sleep 3; done; test \"$ok\" = \"1\"")
checks.append("code=$(wget -q -T 8 -O- --header='Content-Type: application/json' --post-data=" + q(payload("eth_getCode", [CONTRACT, "latest"])) + " " + q(backend_url) + " 2>/dev/null || true); printf '%s' \"$code\" | grep -F '\"" + RUNTIME + "\"'")
checks.append("slot=$(wget -q -T 8 -O- --header='Content-Type: application/json' --post-data=" + q(payload("eth_call", [{"to": CONTRACT, "data": "0x"}, "latest"])) + " " + q(backend_url) + " 2>/dev/null || true); printf '%s' \"$slot\" | grep -F '\"" + VALUE + "\"'")
script = "set -eu\n" + "\n".join(checks)
code = docker_exec(proxy_id, script)
if code != 0:
    marker("proxy-rpc-error", rpc_url=backend_url, exit_code=code); raise SystemExit(code)
marker("verified", rpc_url=backend_url, self_tx_hash=SELF_TX, deploy_tx_hash=DEPLOY_TX, write_tx_hash=WRITE_TX, contract_address=CONTRACT, stored_value=VALUE, observed_block=SOURCE_BLOCK)
raise SystemExit(0)
'''


def _c_verifier_compose(service_name: str, env: Mapping[str, str]) -> str:
    required = {
        "MC_MOTHER_CANARY_SELF_TX_HASH",
        "MC_MOTHER_CANARY_DEPLOY_TX_HASH",
        "MC_MOTHER_CANARY_WRITE_TX_HASH",
        "MC_MOTHER_CANARY_CONTRACT_ADDRESS",
        "MC_MOTHER_CANARY_SOURCE_BLOCK_AFTER",
        "MC_MOTHER_CANARY_RUNTIME_CODE",
        "MC_MOTHER_CANARY_WRITTEN_STORAGE_WORD",
    }
    missing = sorted(required.difference(env))
    if missing:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_C_VERIFIER_ENV_INVALID", "C verifier compose is missing committed execution values")
    command = "cat > /run/mother-canary/c_proxy_verifier.py <<'PY'\n" + _C_PROXY_VERIFIER_PY + "\nPY\npython /run/mother-canary/c_proxy_verifier.py\ntouch /run/mother-canary/verified\nexec sleep 900\n"
    document = {
        "name": service_name,
        "services": {
            service_name: {
                "image": "python:3.12-alpine",
                "restart": "no",
                "read_only": True,
                "tmpfs": ["/run/mother-canary"],
                "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
                "environment": {key: str(env[key]) for key in sorted(required)},
                "entrypoint": ["/bin/sh", "-ec"],
                "command": command,
                "healthcheck": {"test": ["CMD-SHELL", "test -f /run/mother-canary/verified"], "interval": "5s", "timeout": "3s", "retries": 30, "start_period": "5s"},
                "labels": {"main_computer.mother.stage": "validator-rpc-canary-execution-c-proxy-verifier", "main_computer.mother.role": "c-proxy-verifier"},
            }
        },
    }
    return yaml.safe_dump(document, sort_keys=False, default_flow_style=False, width=4096)


def _service_body(controller: Mapping[str, Any], name: str, compose: str) -> dict[str, Any]:
    return {"project_uuid": controller["project_uuid"], "server_uuid": controller["server_uuid"], "environment_name": "mainnet", "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"), "name": name, "description": "Ephemeral Mother validator-RPC canary execution verifier", "instant_deploy": False}


def _run_c_proxy_verifier(*, private_state: PrivateStateReadResult, canary_name: str, a_result: Mapping[str, Any], timeout: float, max_response_bytes: int, max_wait_seconds: float, poll_interval_seconds: float, opener: Any, receipts: list[dict[str, Any]], observations: list[dict[str, Any]]) -> dict[str, Any]:
    controller_id = _C_CONTROLLER
    controller = resolve_coolify_controller(private_state, "mainnet", controller_id)
    controller_config = _funding_controller(private_state, controller_id)
    service_name = f"{canary_name}-execute-verify-c"
    service_uuid: str | None = None
    try:
        environment_uuid = _resolve_environment_uuid(controller=controller, controller_id=controller_id, endpoint=f"/api/v1/projects/{controller_config['project_uuid']}/environments", expected_name="mainnet", timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, observations=observations, phase="c_validator_rpc_canary_verifier-mainnet-environment-resolution")
        env = {
            "MC_MOTHER_CANARY_SELF_TX_HASH": str(a_result["self_tx_hash"]),
            "MC_MOTHER_CANARY_DEPLOY_TX_HASH": str(a_result["deploy_tx_hash"]),
            "MC_MOTHER_CANARY_WRITE_TX_HASH": str(a_result["write_tx_hash"]),
            "MC_MOTHER_CANARY_CONTRACT_ADDRESS": str(a_result["contract_address"]),
            "MC_MOTHER_CANARY_SOURCE_BLOCK_AFTER": str(a_result["block_after"]),
            "MC_MOTHER_CANARY_RUNTIME_CODE": "0x" + _CONTRACT_RUNTIME,
            "MC_MOTHER_CANARY_WRITTEN_STORAGE_WORD": "0x" + _EXPECTED_VALUE,
        }
        create_body = _service_body(controller_config, service_name, _c_verifier_compose(service_name, env))
        create_body["environment_uuid"] = environment_uuid
        create_response = _request_mutation(controller=controller, mutation_id=f"{service_name}.create-service", controller_id=controller_id, method="POST", endpoint="/api/v1/services", body=create_body, success_statuses=(200, 201, 202), timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, receipts=receipts)
        service_uuid = _application_uuid(create_response["payload"])
        receipts[-1].update({"application_uuid": service_uuid, "service_name": service_name, "request_body_sha256": hashlib.sha256(canonical_json(create_body)).hexdigest()})
        _request_mutation(controller=controller, mutation_id=f"{service_name}.start", controller_id=controller_id, method="POST", endpoint=f"/api/v1/services/{service_uuid}/start", body=None, success_statuses=(200, 201, 202), timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, receipts=receipts, application_uuid=service_uuid)
        receipts[-1]["service_name"] = service_name
        proof = _wait_for_service_health(controller=controller, controller_id=controller_id, service_uuid=service_uuid, service_name=service_name, timeout=timeout, max_response_bytes=max_response_bytes, max_wait_seconds=max_wait_seconds, poll_interval_seconds=poll_interval_seconds, opener=opener, observations=observations, phase="c_validator_rpc_canary_verifier-status-health-result")
        return {**dict(proof), "controller_id": controller_id, "result_channel": "service-detail-health", "proof": "C proxy verifier service reached healthy after checking canary receipts, bytecode, and storage through mainnetc-super1:8545"}
    finally:
        if service_uuid is not None:
            response = _http(controller, "DELETE", f"/api/v1/services/{service_uuid}", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            deleted = int(response.get("status", 0)) in {200, 204, 404}
            receipt = _receipt(mutation_id=f"{service_name}.delete", controller_id=controller_id, method="DELETE", endpoint=f"/api/v1/services/{service_uuid}", response=response, succeeded=deleted, application_uuid=service_uuid)
            receipt["service_name"] = service_name
            receipt["cleanup_absent"] = response.get("status") == 404
            receipts.append(receipt)
            if not deleted:
                raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_CLEANUP_FAILED", "temporary C verifier service cleanup failed")



def _recover_local_execution_from_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    recovery_evidence_path: Path,
    release: Mapping[str, Any],
    *,
    max_age_seconds: int,
    operation: OperationIdentity,
) -> dict[str, Any]:
    document, _, _ = _canonical_under(paths, recovery_evidence_path, _EVIDENCE_DIRECTORY, "validator-RPC canary recovery evidence")
    digest = _digest_without(document, "validator_rpc_canary_evidence_sha256")
    if not (
        document.get("kind") == _EVIDENCE_KIND
        and document.get("schema_version") == 1
        and document.get("validator_rpc_canary_evidence_sha256") == digest
        and document.get("mother_binding") == _binding(private_state)
    ):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_INVALID", "validator-RPC canary recovery evidence is not a canonical Mother evidence document")
    completed = _parse_utc(document.get("completed_at"), "recovery_evidence.completed_at")
    age = int((datetime.now(timezone.utc) - completed).total_seconds())
    if age < -15 or age > max_age_seconds:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_EXPIRED", "validator-RPC canary recovery evidence is outside the accepted age window")
    if document.get("chain_state") not in {"exact-on-A-not-yet-verified-on-C", "exact-cross-validator-verified"}:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_INVALID", "validator-RPC canary recovery evidence does not contain an A-side execution proof")
    summary = _mapping(document.get("summary"), "recovery_evidence.summary")
    if not (
        summary.get("canary_execution_performed") is True
        and summary.get("funding_performed") is False
        and summary.get("validator_mutation_count") == 0
        and summary.get("validator_restart_count") == 0
        and summary.get("validator_vote_performed") is False
    ):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_INVALID", "validator-RPC canary recovery evidence is not a safe zero-validator-mutation execution proof")
    release_ref = _mapping(document.get("release"), "recovery_evidence.release")
    previous_release_path = _resolve(paths, release_ref.get("locator"), _RELEASE_DIRECTORY, "validator-RPC canary recovery release")
    previous_release, _, _ = _canonical_under(paths, previous_release_path, _RELEASE_DIRECTORY, "validator-RPC canary recovery release")
    current_transaction = _mapping(release.get("transaction"), "release.transaction")
    previous_transaction = _mapping(previous_release.get("transaction"), "recovery_release.transaction")
    if current_transaction.get("sha256") != previous_transaction.get("sha256"):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_INVALID", "validator-RPC canary recovery evidence does not bind the same staged canary transaction")
    current_funding = _mapping(release.get("funding_evidence"), "release.funding_evidence")
    previous_funding = _mapping(previous_release.get("funding_evidence"), "recovery_release.funding_evidence")
    if current_funding.get("evidence_sha256") != previous_funding.get("evidence_sha256") or current_funding.get("funding_transaction_hash") != previous_funding.get("funding_transaction_hash"):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_INVALID", "validator-RPC canary recovery evidence does not bind the same completed funding proof")
    if _address(document.get("canary_address"), "recovery_evidence.canary_address") != _address(_mapping(release.get("identity"), "release.identity").get("address"), "release.identity.address"):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_INVALID", "validator-RPC canary recovery evidence does not bind the current canary identity")
    proofs = _mapping(document.get("runtime_proofs"), "recovery_evidence.runtime_proofs")
    a_result = dict(_mapping(proofs.get("a_validator_rpc_canary_execution"), "recovery_evidence.runtime_proofs.a_validator_rpc_canary_execution"))
    if not (a_result.get("healthy") is True and a_result.get("classification") == "executed" and a_result.get("result_channel") == "local-json-rpc-eth-account"):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_INVALID", "validator-RPC canary recovery evidence does not contain a healthy local execution result")
    a_result["self_tx_hash"] = _normalize_tx_hash(a_result.get("self_tx_hash"), code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_INVALID", message="validator-RPC canary recovery evidence has an invalid self transaction hash")
    a_result["deploy_tx_hash"] = _normalize_tx_hash(a_result.get("deploy_tx_hash"), code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_INVALID", message="validator-RPC canary recovery evidence has an invalid deploy transaction hash")
    a_result["write_tx_hash"] = _normalize_tx_hash(a_result.get("write_tx_hash"), code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_INVALID", message="validator-RPC canary recovery evidence has an invalid write transaction hash")
    a_result["contract_address"] = _address(a_result.get("contract_address"), "recovery_evidence.contract_address")
    expected_value = str(_mapping(release.get("canary_contract"), "release.canary_contract")["written_storage_word"]).lower()
    if str(a_result.get("stored_value")).lower() != expected_value:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_INVALID", "validator-RPC canary recovery evidence does not bind the expected storage write")
    statuses = _mapping(a_result.get("receipt_statuses"), "recovery_evidence.receipt_statuses")
    if not all(str(statuses.get(key)).lower() == "0x1" for key in ("self", "deploy", "write")):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RECOVERY_EVIDENCE_INVALID", "validator-RPC canary recovery evidence does not prove successful A-side receipts")
    a_result["block_after"] = int(a_result["block_after"])
    a_result["recovered_from_evidence"] = _relative(paths, Path(recovery_evidence_path).resolve(strict=False), "validator-RPC canary recovery evidence")
    a_result["recovery_evidence_sha256"] = digest
    a_result["proof"] = "Mother recovered the already-executed A-side validator-RPC canary result from prior canonical evidence and did not resend canary transactions"
    return a_result

def _write_execution_evidence(paths: PrivateStatePaths, evidence: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(evidence)
    digest = _digest_without(document, "validator_rpc_canary_evidence_sha256")
    document["validator_rpc_canary_evidence_sha256"] = digest
    root = _ensure_root(paths, _EVIDENCE_DIRECTORY, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "evidence"
    destination = root / f"{stamp}-{digest[:16]}.json"
    payload = canonical_json(document)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_CONFLICT", "validator-RPC canary evidence destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_validator_rpc_canary_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    funding_evidence_max_age_seconds: int = 86400,
    funding_transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    recovery_evidence_path: Path | None = None,
    recovery_evidence_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_validator_rpc_canary_release(paths, private_state, release_path, acknowledged_release_sha256=acknowledged_release_sha256, selected_nodes=selected_nodes, max_age_seconds=max_age_seconds, transaction_max_age_seconds=transaction_max_age_seconds, funding_evidence_max_age_seconds=funding_evidence_max_age_seconds, funding_transaction_max_age_seconds=funding_transaction_max_age_seconds, soak_max_age_seconds=soak_max_age_seconds, operation=operation)
    if inspected["release_already_claimed"]:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RELEASE_ALREADY_CONSUMED", "validator-RPC canary release already has an execution claim")
    release, _, _ = _canonical_under(paths, Path(release_path), _RELEASE_DIRECTORY, "validator-RPC canary release")
    release_sha = inspected["release_sha256"]
    claim = {"kind": _CLAIM_KIND, "schema_version": 1, "claimed_at": _timestamp(), "release": {"locator": _relative(paths, Path(release_path).resolve(strict=False), "validator-RPC canary release"), "sha256": release_sha}, "requested_use_limit": 1, "operation_id": operation.operation_id}
    claim_path = _ensure_root(paths, _CLAIM_DIRECTORY, operation) / f"{release_sha}.json"
    if claim_path.exists():
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RELEASE_ALREADY_CONSUMED", "validator-RPC canary release already has an execution claim")
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)

    started_at = _timestamp()
    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    runtime_proofs: dict[str, Mapping[str, Any]] = {}
    runtime_results: dict[str, Mapping[str, Any]] = {}
    cross_validator_proof: dict[str, Any] | None = None
    failure: dict[str, str] | None = None
    chain_state = "unchanged-before-canary-start"
    local_execution_started = False
    tx_path = _resolve(paths, _mapping(release["transaction"], "release.transaction").get("locator"), _TRANSACTION_DIRECTORY, "validator-RPC canary transaction")
    canary_transaction, _, _ = _canonical_under(paths, tx_path, _TRANSACTION_DIRECTORY, "validator-RPC canary transaction")
    canary_name = str(_mapping(canary_transaction.get("identity"), "canary.identity")["canary_name"])
    identity = _load_canary_identity(paths, canary_transaction)
    try:
        if recovery_evidence_path is not None:
            a_result = _recover_local_execution_from_evidence(paths, private_state, Path(recovery_evidence_path), release, max_age_seconds=recovery_evidence_max_age_seconds, operation=operation)
        else:
            local_execution_started = True
            a_result = _execute_local_python_canary(rpc_url=str(_mapping(release["execution"], "release.execution")["shared_rpc_url"]), private_key=str(identity["private_key"]), canary_address=str(identity["address"]), contract=_mapping(release["canary_contract"], "release.canary_contract"), fee_policy=_mapping(release["fee_policy"], "release.fee_policy"), chain_id=int(release["chain"]["chain_id"]), timeout=timeout, max_response_bytes=max_response_bytes, max_wait_seconds=max_wait_seconds, poll_interval_seconds=poll_interval_seconds, opener=opener)
        runtime_proofs["a_validator_rpc_canary_execution"] = a_result
        runtime_results["a_validator_rpc_canary_execution"] = {"step": "a_validator_rpc_canary_execution", "classification": "recovered-executed" if recovery_evidence_path is not None else "executed", "rpc_url": a_result["rpc_url"], "self_tx_hash": a_result["self_tx_hash"], "deploy_tx_hash": a_result["deploy_tx_hash"], "write_tx_hash": a_result["write_tx_hash"], "contract_address": a_result["contract_address"], "stored_value": a_result["stored_value"], "block_after": str(a_result["block_after"])}
        chain_state = "exact-on-A-not-yet-verified-on-C"
        c_proof = _run_c_proxy_verifier(private_state=private_state, canary_name=canary_name, a_result=a_result, timeout=timeout, max_response_bytes=max_response_bytes, max_wait_seconds=max_wait_seconds, poll_interval_seconds=poll_interval_seconds, opener=opener, receipts=receipts, observations=observations)
        runtime_proofs["c_validator_rpc_canary_verifier"] = c_proof
        if c_proof.get("healthy") is not True:
            raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_RESULT_INVALID", "C verifier did not reach its committed healthy verification state")
        cross_validator_proof = {"controller_id": _C_CONTROLLER, "service_name": c_proof["service_name"], "service_uuid": c_proof["service_uuid"], "service_status": c_proof["service_status"], "result_channel": "service-detail-health", "receipt_verified": True, "bytecode_verified": True, "storage_verified": True, "self_tx_hash": a_result["self_tx_hash"], "deploy_tx_hash": a_result["deploy_tx_hash"], "write_tx_hash": a_result["write_tx_hash"], "contract_address": a_result["contract_address"], "proof": "cross-validator canary receipts, bytecode, and storage verified from C"}
        chain_state = "exact-cross-validator-verified"
    except Exception as exc:
        failure = {"code": str(getattr(exc, "code", "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_EXECUTION_FAILED")), "message": str(exc).replace("\r", " ").replace("\n", " ").strip()[:300]}
        if local_execution_started and chain_state == "unchanged-before-canary-start":
            chain_state = "potentially-unknown-after-canary-start"
    delete_receipts = [item for item in receipts if str(item.get("method")).upper() == "DELETE"]
    temporary_services_deleted = bool(delete_receipts) and all(item.get("status") == "succeeded" for item in delete_receipts)
    success = bool(failure is None and chain_state == "exact-cross-validator-verified" and cross_validator_proof is not None and temporary_services_deleted and all(item.get("status") == "succeeded" for item in receipts))
    evidence: dict[str, Any] = {"kind": _EVIDENCE_KIND, "schema_version": 1, "status": "pass" if success else "manual-review-required", "started_at": started_at, "completed_at": _timestamp(), "network": "mainnet", "mother_binding": _binding(private_state), "release": {"locator": _relative(paths, Path(release_path).resolve(strict=False), "validator-RPC canary release"), "sha256": release_sha}, "claim": {"locator": _relative(paths, claim_path, "validator-RPC canary execution claim")}, "chain": dict(release["chain"]), "canary_address": identity["address"], "funding_evidence": dict(release["funding_evidence"]), "chain_state": chain_state, "cross_validator_verification": cross_validator_proof, "mutation_receipts": receipts, "service_observations": observations, "runtime_proofs": runtime_proofs, "runtime_results": runtime_results, "failure": failure, "summary": {"clean": success, "complete": success, "canary_execution_complete": success, "canary_execution_performed": bool(runtime_proofs.get("a_validator_rpc_canary_execution")), "canary_self_transaction_verified_on_A": success, "canary_contract_deployed_verified_on_A": success, "canary_storage_write_verified_on_A": success, "canary_receipts_verified_on_C": success, "canary_bytecode_verified_on_C": success, "canary_storage_verified_on_C": success, "funding_performed": False, "funding_evidence_consumed": True, "canary_execution_recovered_from_prior_execution": recovery_evidence_path is not None, "service_health_result_channel_used": bool(runtime_proofs.get("c_validator_rpc_canary_verifier")), "runtime_log_result_channel_used": False, "temporary_C_application_deleted": temporary_services_deleted, "temporary_services_deleted": temporary_services_deleted, "temporary_service_count": 1 if receipts else 0, "application_mutation_count": len(receipts), "validator_mutation_count": 0, "validator_restart_count": 0, "public_endpoint_count": 0, "validator_vote_performed": False, "next_phase": "validator-rpc-canary-execution-complete" if success else "manual-review-required"}}
    evidence_path, evidence_sha = _write_execution_evidence(paths, evidence, operation=operation)
    return {"status": evidence["status"], "network": "mainnet", "chain_id": release["chain"]["chain_id"], "canary_address": identity["address"], "chain_state": chain_state, "summary": evidence["summary"], "evidence": {"path": str(evidence_path), "sha256": evidence_sha}}


def verify_validator_rpc_canary_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 86400,
    release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    funding_evidence_max_age_seconds: int = 86400,
    funding_transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    operation: OperationIdentity,
) -> dict[str, Any]:
    document, _, file_sha = _canonical_under(paths, Path(evidence_path), _EVIDENCE_DIRECTORY, "validator-RPC canary execution evidence")
    digest = _digest_without(document, "validator_rpc_canary_evidence_sha256")
    if not (document.get("kind") == _EVIDENCE_KIND and document.get("schema_version") == 1 and document.get("status") == "pass" and document.get("validator_rpc_canary_evidence_sha256") == digest and document.get("mother_binding") == _binding(private_state)):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_EVIDENCE_INVALID", "validator-RPC canary execution evidence is not a passing canonical document")
    completed = _parse_utc(document.get("completed_at"), "evidence.completed_at")
    age = int((datetime.now(timezone.utc) - completed).total_seconds())
    if age < -15 or age > max_age_seconds:
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_EVIDENCE_EXPIRED", "validator-RPC canary execution evidence is outside the accepted age window")
    release_ref = _mapping(document.get("release"), "evidence.release")
    release_path = _resolve(paths, release_ref.get("locator"), _RELEASE_DIRECTORY, "validator-RPC canary release")
    verified_release = verify_validator_rpc_canary_release(paths, private_state, release_path, selected_nodes=selected_nodes, max_age_seconds=release_max_age_seconds, transaction_max_age_seconds=transaction_max_age_seconds, funding_evidence_max_age_seconds=funding_evidence_max_age_seconds, funding_transaction_max_age_seconds=funding_transaction_max_age_seconds, soak_max_age_seconds=soak_max_age_seconds, operation=operation)
    summary = _mapping(document.get("summary"), "evidence.summary")
    cross = _mapping(document.get("cross_validator_verification"), "evidence.cross_validator_verification")
    if not (release_ref.get("sha256") == verified_release["release_sha256"] and document.get("chain_state") == "exact-cross-validator-verified" and summary.get("clean") is True and summary.get("canary_execution_performed") is True and summary.get("canary_receipts_verified_on_C") is True and summary.get("validator_mutation_count") == 0 and summary.get("validator_restart_count") == 0 and summary.get("validator_vote_performed") is False and cross.get("receipt_verified") is True and cross.get("bytecode_verified") is True and cross.get("storage_verified") is True):
        raise _error("MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_EVIDENCE_INVALID", "validator-RPC canary execution evidence is not a clean cross-validator proof")
    return {"clean": True, "network": "mainnet", "evidence_path": str(Path(evidence_path).resolve(strict=False)), "evidence_file_sha256": file_sha, "evidence_sha256": digest, "age_seconds": max(0, age), "chain_id": document["chain"]["chain_id"], "canary_address": document["canary_address"], "chain_state": document["chain_state"], "canary_execution_performed": True, "canary_receipts_verified_on_C": True, "canary_bytecode_verified_on_C": True, "canary_storage_verified_on_C": True, "validator_mutation_count": 0, "validator_restart_count": 0, "validator_vote_performed": False, "next_phase": summary.get("next_phase")}


__all__ = ["MotherDeploymentValidatorRpcCanaryExecutionError", "build_validator_rpc_canary_release", "execute_validator_rpc_canary_release", "inspect_validator_rpc_canary_release", "verify_validator_rpc_canary_evidence", "verify_validator_rpc_canary_release", "write_validator_rpc_canary_release"]
