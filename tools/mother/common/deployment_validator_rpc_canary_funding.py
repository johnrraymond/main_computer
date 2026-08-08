"""Bounded validator-RPC canary funding compiler, release, and executor.

The compiler binds the genesis-funded Mother captain wallet to one exact EIP-1559
transfer. Live execution is one-use and idempotent: a zero canary balance permits
the exact capped transfer, the exact target balance is reconciled without sending,
and every other balance fails closed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import base64
import hashlib
from pathlib import Path
import json
import re
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import atomic_files
from .canonical import canonical_json
from .deployment_coolify_context import load_controller_config
from .deployment_post_admission_steady_state import (
    _binding,
    _contains_sensitive,
    _canonical_under,
    _ensure_root,
    _mapping,
    _parse_utc,
    _relative,
    _resolve,
    _timestamp,
)
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_validator_admission_executor import _http
from .deployment_validator_rpc_canary import (
    _TRANSACTION_DIRECTORY as _CANARY_TRANSACTION_DIRECTORY,
    verify_validator_rpc_canary_transaction,
)
from .ethereum_identity import is_private_key, private_key_to_address
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_transaction.v15"
_SCHEMA_VERSION = 15
_DIRECTORY = ("actions", "deployment-validator-rpc-canary-funding-transactions")
_RELEASE_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_release.v2"
_CLAIM_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_evidence.v4"
_RELEASE_DIRECTORY = ("actions", "deployment-validator-rpc-canary-funding-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-validator-rpc-canary-funding-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-validator-rpc-canary-funding")
_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900
_A = "mainneta-super1"
_C = "mainnetc-super1"
_SHARED_RPC_ROUTE_DOMAIN = "greatlibrary.io"
_SHARED_RPC_ROUTE_HOST = "mainnet-rpc.greatlibrary.io"
_SHARED_RPC_ROUTE_URL = f"https://{_SHARED_RPC_ROUTE_HOST}"
_A_CONTROLLER = "coolify-a"
_C_CONTROLLER = "coolify-c"
_SHARED_RPC_ROUTE_TARGETS = {
    "a": ("mainneta-super1", _A_CONTROLLER),
    "c": ("mainnetc-super1", _C_CONTROLLER),
}
_IMAGE = "python:3.12-alpine"
_CAPTAIN_SECRET_ENV = "MC_MOTHER_CAPTAIN_PRIVATE_KEY"
_TX_HASH_ENV = "MC_MOTHER_CANARY_FUNDING_TX_HASH"
_RESULT_MARKER = "MOTHER_VALIDATOR_RPC_CANARY_FUNDING_RESULT"
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_MAX_FEE_PER_GAS_WEI = 2_000_000_000
_MAX_PRIORITY_FEE_PER_GAS_WEI = 0
_FUNDING_GAS_LIMIT = 21_000
_FUNDING_TX_MAX_FEE_WEI = _FUNDING_GAS_LIMIT * _MAX_FEE_PER_GAS_WEI
_GENESIS_CAPTAIN_BALANCE_WEI = 10_000_000_000_000_000_000_000


class MotherDeploymentValidatorRpcCanaryFundingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error(code: str, message: str) -> MotherDeploymentValidatorRpcCanaryFundingError:
    return MotherDeploymentValidatorRpcCanaryFundingError(code, message)


def _controller_config(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
) -> dict[str, Any]:
    return load_controller_config(
        private_state,
        network=network,
        controller_id=controller_id,
        allowed_controllers={_A_CONTROLLER, _C_CONTROLLER},
        error_factory=_error,
        rejected_code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_CONTROLLER_REJECTED",
        invalid_code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_PRIVATE_STATE_INVALID",
        placement_description="validator RPC canary funding placement",
    )


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(
        canonical_json({key: value for key, value in document.items() if key != field})
    ).hexdigest()


def _address(value: Any, path: str) -> str:
    if type(value) is not str or _ADDRESS_RE.fullmatch(value) is None:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            f"{path} must be a 20-byte Ethereum address",
        )
    return value.lower()


def _captain(private_state: PrivateStateReadResult) -> dict[str, Any]:
    document = yaml.safe_load(private_state.document_bytes)
    if not isinstance(document, Mapping):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SOURCE_INVALID",
            "Mother private state is malformed",
        )
    network = _mapping(
        _mapping(document.get("networks"), "private_state.networks").get("mainnet"),
        "private_state.networks.mainnet",
    )
    wallets = _mapping(network.get("wallets"), "private_state.networks.mainnet.wallets")
    captain = _mapping(wallets.get("captain"), "private_state.networks.mainnet.wallets.captain")
    private_key = captain.get("private_key")
    address = _address(captain.get("address"), "captain.address")
    if not is_private_key(private_key) or private_key_to_address(private_key).lower() != address:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SOURCE_INVALID",
            "captain wallet identity is missing or contradictory",
        )
    genesis = _mapping(network.get("genesis"), "private_state.networks.mainnet.genesis")
    alloc = genesis.get("alloc_accounts")
    expected_alloc = [{"ref": "networks.mainnet.wallets.captain"}]
    if alloc != expected_alloc:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SOURCE_INVALID",
            "captain wallet is not the exact canonical genesis allocation source",
        )
    return {
        "role": "captain",
        "address": address,
        "private_key": private_key,
        "private_state_field": "networks.mainnet.wallets.captain.private_key",
        "genesis_allocated": True,
        "genesis_allocated_balance_wei": _GENESIS_CAPTAIN_BALANCE_WEI,
    }


def _private_state_document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        document = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_UNRESOLVED",
            "Mother private state is not valid canonical JSON",
        ) from exc
    if not isinstance(document, dict):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_UNRESOLVED",
            "Mother private state root is not an object",
        )
    return document


def _network_state(private_state: PrivateStateReadResult) -> Mapping[str, Any]:
    networks = _private_state_document(private_state).get("networks")
    mainnet = networks.get("mainnet") if isinstance(networks, Mapping) else None
    if not isinstance(mainnet, Mapping):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_UNRESOLVED",
            "Mother private state does not contain networks.mainnet",
        )
    return mainnet


def _target_controller_id(mainnet: Mapping[str, Any], node: str) -> str:
    nodes = mainnet.get("nodes")
    node_record = nodes.get(node) if isinstance(nodes, Mapping) else None
    host = node_record.get("host") if isinstance(node_record, Mapping) else None
    if isinstance(host, str) and host:
        return host

    deployment = mainnet.get("deployment")
    targets = deployment.get("targets") if isinstance(deployment, Mapping) else None
    target = targets.get(node) if isinstance(targets, Mapping) else None
    controller_ref = target.get("controller_ref") if isinstance(target, Mapping) else None
    if isinstance(controller_ref, str) and controller_ref:
        controller_id = controller_ref.rsplit(".", 1)[-1]
        if controller_id in {_A_CONTROLLER, _C_CONTROLLER}:
            return controller_id

    fallback = {_A: _A_CONTROLLER, _C: _C_CONTROLLER}.get(node)
    if fallback is not None:
        return fallback

    raise _error(
        "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_UNRESOLVED",
        f"cannot resolve Coolify controller for validator RPC node {node}",
    )



def _shared_rpc_route_host(mainnet: Mapping[str, Any]) -> str:
    """Return the Mother-owned aggregate validator-RPC hostname.

    Mother owns the post-All-Father route.  All Father's per-node route names are
    reference-only and must not be synthesized for live Mother canary funding.
    """
    rpc = mainnet.get("rpc_route") if isinstance(mainnet.get("rpc_route"), Mapping) else None
    for field in ("host", "hostname", "public_host"):
        value = rpc.get(field) if isinstance(rpc, Mapping) else None
        if isinstance(value, str) and re.fullmatch(r"[a-z0-9.-]+", value):
            return value
    allfather = mainnet.get("allfather")
    domain = allfather.get("public_domain") if isinstance(allfather, Mapping) else None
    if not isinstance(domain, str) or not re.fullmatch(r"[a-z0-9.-]+", domain):
        domain = _SHARED_RPC_ROUTE_DOMAIN
    return f"mainnet-rpc.{domain}"


def _shared_rpc_route_url(mainnet: Mapping[str, Any]) -> str:
    return f"https://{_shared_rpc_route_host(mainnet)}"


def _shared_rpc_route_contracts(private_state: PrivateStateReadResult) -> dict[str, dict[str, Any]]:
    mainnet = _network_state(private_state)
    chain_id = mainnet.get("chain_id")
    if chain_id != 42424240:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_UNRESOLVED",
            "Mother private state does not bind mainnet chain_id 42424240",
        )

    rpc_url = _shared_rpc_route_url(mainnet)
    return {
        "shared": {
            "node": "mainnet-shared-rpc",
            "controller_id": "aggregate",
            "rpc_url": rpc_url,
            "route_source": "mother-owned-shared-network-rpc-route",
            "expected_chain_id": chain_id,
            "required_methods": ["eth_chainId", "eth_getBalance"],
        }
    }


def _shared_rpc_route_targets(private_state: PrivateStateReadResult) -> dict[str, dict[str, Any]]:
    mainnet = _network_state(private_state)
    chain_id = mainnet.get("chain_id")
    if chain_id != 42424240:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_UNRESOLVED",
            "Mother private state does not bind mainnet chain_id 42424240",
        )
    host = _shared_rpc_route_host(mainnet)
    result: dict[str, dict[str, Any]] = {}
    for key, (node, fallback_controller_id) in _SHARED_RPC_ROUTE_TARGETS.items():
        controller_id = _target_controller_id(mainnet, node)
        if controller_id not in {_A_CONTROLLER, _C_CONTROLLER}:
            controller_id = fallback_controller_id
        result[key] = {
            "key": key,
            "node": node,
            "controller_id": controller_id,
            "route_host": host,
            "target_host": node,
            "target_port": 8545,
            "target_url": f"http://{node}:8545",
            "expected_chain_id": chain_id,
            "dynamic_file": f"mother-mainnet-rpc-route-{controller_id}.yml",
        }
    return result


def _open_url(opener: Any, request: urllib.request.Request, timeout: float):
    return opener.open(request, timeout=timeout) if hasattr(opener, "open") else opener(request, timeout=timeout)


def _rpc_post(
    *,
    rpc_url: str,
    method: str,
    params: list[Any],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    body = canonical_json({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    })
    request = urllib.request.Request(
        rpc_url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "main-computer-mother-validator-rpc-route-preflight/1",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        try:
            response = _open_url(opener, request, float(timeout))
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {
            "method": method,
            "rpc_url": rpc_url,
            "ok": False,
            "status": None,
            "error": type(exc).__name__,
            "message": str(exc)[:200],
            "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
        }
    if len(raw) > max_response_bytes:
        return {
            "method": method,
            "rpc_url": rpc_url,
            "ok": False,
            "status": status,
            "error": "response-too-large",
            "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
        }
    try:
        payload: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = raw.decode("utf-8", errors="replace")
    record = {
        "method": method,
        "rpc_url": rpc_url,
        "ok": 200 <= status < 300,
        "status": status,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
    }
    if isinstance(payload, Mapping):
        if "error" in payload:
            record["json_rpc_error"] = payload.get("error")
        if "result" in payload:
            record["result"] = payload.get("result")
    else:
        text = raw.decode("utf-8", errors="replace")
        record["payload_type"] = type(payload).__name__
        record["body_preview"] = text[:200]
        if "no available server" in text.lower():
            record["ok"] = False
            record["route_no_backend"] = True
    return record


def _hex_quantity_to_int(value: Any, *, field: str) -> int:
    if not (type(value) is str and re.fullmatch(r"0x[0-9a-fA-F]+", value)):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_BALANCE_UNREADABLE",
            f"{field} is not a JSON-RPC hex quantity",
        )
    return int(value, 16)


def _probe_rpc_route(
    route: Mapping[str, Any],
    *,
    canary_address: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    rpc_url = str(route["rpc_url"])
    expected_chain_id = int(route["expected_chain_id"])
    observations: list[dict[str, Any]] = []

    chain = _rpc_post(
        rpc_url=rpc_url,
        method="eth_chainId",
        params=[],
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    observations.append(chain)
    if chain.get("ok") is not True:
        no_backend = chain.get("route_no_backend") is True
        return {
            **dict(route),
            "ok": False,
            "failure_code": (
                "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_NO_BACKEND"
                if no_backend
                else "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_UNAVAILABLE"
            ),
            "failure_message": (
                f"Mother shared RPC route {rpc_url} reached Traefik but no healthy backend was available"
                if no_backend
                else f"Mother shared RPC route {rpc_url} did not answer eth_chainId"
            ),
            "observations": observations,
        }
    try:
        chain_id = _hex_quantity_to_int(chain.get("result"), field="eth_chainId.result")
    except MotherDeploymentValidatorRpcCanaryFundingError:
        return {
            **dict(route),
            "ok": False,
            "failure_code": "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_UNAVAILABLE",
            "failure_message": f"Mother shared RPC route {rpc_url} returned an invalid eth_chainId result",
            "observations": observations,
        }
    if chain_id != expected_chain_id:
        return {
            **dict(route),
            "ok": False,
            "chain_id": chain_id,
            "failure_code": "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_WRONG_CHAIN",
            "failure_message": f"Mother shared RPC route {rpc_url} reported chain_id {chain_id}, not {expected_chain_id}",
            "observations": observations,
        }

    balance = _rpc_post(
        rpc_url=rpc_url,
        method="eth_getBalance",
        params=[canary_address, "latest"],
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    observations.append(balance)
    if balance.get("ok") is not True:
        return {
            **dict(route),
            "ok": False,
            "chain_id": chain_id,
            "failure_code": "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_BALANCE_UNREADABLE",
            "failure_message": f"Mother shared RPC route {rpc_url} did not answer eth_getBalance",
            "observations": observations,
        }
    try:
        balance_wei = _hex_quantity_to_int(balance.get("result"), field="eth_getBalance.result")
    except MotherDeploymentValidatorRpcCanaryFundingError:
        return {
            **dict(route),
            "ok": False,
            "chain_id": chain_id,
            "failure_code": "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_BALANCE_UNREADABLE",
            "failure_message": f"Mother shared RPC route {rpc_url} returned an invalid eth_getBalance result",
            "observations": observations,
        }
    return {
        **dict(route),
        "ok": True,
        "chain_id": chain_id,
        "canary_balance_wei": balance_wei,
        "observations": observations,
    }




def _funding_quantity_to_int(value: Any, *, field: str, code: str) -> int:
    if not (type(value) is str and re.fullmatch(r"0x[0-9a-fA-F]+", value)):
        raise _error(code, f"{field} is not a JSON-RPC hex quantity")
    return int(value, 16)


def _rpc_required_result(
    *,
    rpc_url: str,
    method: str,
    params: list[Any],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    code: str,
    message: str,
    observations: list[dict[str, Any]],
) -> Any:
    record = _rpc_post(
        rpc_url=rpc_url,
        method=method,
        params=params,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    observations.append(record)
    if (
        record.get("ok") is not True
        or "json_rpc_error" in record
        or "result" not in record
    ):
        raise _error(code, message)
    return record.get("result")


def _normalize_tx_hash(value: Any, *, code: str, message: str) -> str:
    if type(value) is str and re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
        return value.lower()
    raise _error(code, message)


def _raw_transaction_hex(raw: Any) -> str:
    if isinstance(raw, (bytes, bytearray)):
        return "0x" + bytes(raw).hex()
    if type(raw) is str and re.fullmatch(r"0x[0-9a-fA-F]+", raw):
        return raw
    hex_method = getattr(raw, "hex", None)
    if callable(hex_method):
        value = str(hex_method())
        return value if value.startswith("0x") else f"0x{value}"
    raise _error(
        "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SIGNING_FAILED",
        "eth_account returned an unsupported raw transaction type",
    )


def _redacted_exception_summary(exc: BaseException) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ")
    text = re.sub(r"0x[0-9a-fA-F]{64}", "0x<redacted-64-hex>", text)
    if len(text) > 180:
        text = text[:177] + "..."
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _transaction_address(value: str) -> str:
    try:
        from eth_utils import to_checksum_address  # type: ignore
    except Exception:
        return value
    try:
        return str(to_checksum_address(value))
    except Exception:
        return value


def _transaction_hash_hex(value: Any, *, transaction_type: str) -> str:
    if isinstance(value, (bytes, bytearray)):
        tx_hash_hex = "0x" + bytes(value).hex()
    elif type(value) is str:
        tx_hash_hex = value
    else:
        hex_method = getattr(value, "hex", None)
        if callable(hex_method):
            tx_hash_hex = str(hex_method())
        else:
            tx_hash_hex = str(value)

    tx_hash_hex = tx_hash_hex.lower()
    if re.fullmatch(r"[0-9a-f]{64}", tx_hash_hex):
        tx_hash_hex = f"0x{tx_hash_hex}"
    if not re.fullmatch(r"0x[0-9a-f]{64}", tx_hash_hex):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SIGNING_FAILED",
            f"eth_account did not return a valid transaction hash for {transaction_type}",
        )
    return tx_hash_hex


def _signed_transaction_parts(signed: Any, *, transaction_type: str) -> tuple[str, str, str]:
    raw = getattr(signed, "raw_transaction", None)
    if raw is None:
        raw = getattr(signed, "rawTransaction", None)
    return _raw_transaction_hex(raw), _transaction_hash_hex(
        getattr(signed, "hash", None),
        transaction_type=transaction_type,
    ), transaction_type


def _sign_capped_transfer(
    *,
    private_key: str,
    expected_source: str,
    chain_id: int,
    nonce: int,
    destination: str,
    amount: int,
    gas_limit: int,
    max_fee_per_gas_wei: int,
    max_priority_fee_per_gas_wei: int,
) -> tuple[str, str, str]:
    try:
        from eth_account import Account  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on operator venv
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SIGNER_UNAVAILABLE",
            "Python eth_account is unavailable in the Mother runtime",
        ) from exc

    account = Account.from_key(private_key)
    source = str(account.address).lower()
    if source != expected_source.lower():
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SOURCE_INVALID",
            "captain private key does not derive the expected funding source address",
        )

    destination_for_tx = _transaction_address(destination)
    dynamic_fee_base = {
        "chainId": chain_id,
        "nonce": nonce,
        "to": destination_for_tx,
        "value": amount,
        "gas": gas_limit,
        "maxFeePerGas": max_fee_per_gas_wei,
        "maxPriorityFeePerGas": max_priority_fee_per_gas_wei,
        "accessList": [],
        "data": "0x",
    }
    legacy = {
        "chainId": chain_id,
        "nonce": nonce,
        "to": destination_for_tx,
        "value": amount,
        "gas": gas_limit,
        "gasPrice": max_fee_per_gas_wei,
        "data": "0x",
    }
    variants: list[tuple[str, dict[str, Any]]] = [
        ("eip1559-type-0x2", {"type": "0x2", **dynamic_fee_base}),
        ("eip1559-type-2", {"type": 2, **dynamic_fee_base}),
        ("eip1559-inferred", dict(dynamic_fee_base)),
        ("legacy-capped-gas-price", legacy),
    ]
    failures: list[str] = []
    for transaction_type, transaction in variants:
        try:
            signed = Account.sign_transaction(transaction, private_key)
            return _signed_transaction_parts(signed, transaction_type=transaction_type)
        except MotherDeploymentValidatorRpcCanaryFundingError:
            raise
        except Exception as exc:
            failures.append(f"{transaction_type}={_redacted_exception_summary(exc)}")

    raise _error(
        "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SIGNING_FAILED",
        "eth_account could not sign any capped funding transaction variant; "
        + "; ".join(failures[:4]),
    )



def _execute_local_python_funding(
    *,
    private_key: str,
    source: str,
    destination: str,
    amount: int,
    chain_id: int,
    rpc_url: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    on_transaction_sent: Any,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []

    destination_balance_before = _funding_quantity_to_int(
        _rpc_required_result(
            rpc_url=rpc_url,
            method="eth_getBalance",
            params=[destination, "latest"],
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RPC_UNAVAILABLE",
            message="shared RPC did not return the destination balance before funding",
            observations=observations,
        ),
        field="eth_getBalance(destination).result",
        code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_BALANCE_UNREADABLE",
    )
    if destination_balance_before != 0:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_UNEXPECTED_BALANCE",
            f"destination balance before local funding is not zero observed_balance_wei={destination_balance_before}",
        )

    latest_block = _rpc_required_result(
        rpc_url=rpc_url,
        method="eth_getBlockByNumber",
        params=["latest", False],
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RPC_UNAVAILABLE",
        message="shared RPC did not return the latest block before funding",
        observations=observations,
    )
    if not isinstance(latest_block, Mapping):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_BASE_FEE_UNAVAILABLE",
            "latest block result is not an object",
        )
    base_fee = _funding_quantity_to_int(
        latest_block.get("baseFeePerGas"),
        field="eth_getBlockByNumber.latest.baseFeePerGas",
        code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_BASE_FEE_UNAVAILABLE",
    )
    if base_fee > _MAX_FEE_PER_GAS_WEI:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_BASE_FEE_EXCEEDS_CAP",
            "latest base fee exceeds the capped funding maxFeePerGas",
        )

    source_balance = _funding_quantity_to_int(
        _rpc_required_result(
            rpc_url=rpc_url,
            method="eth_getBalance",
            params=[source, "latest"],
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SOURCE_BALANCE_UNREADABLE",
            message="shared RPC did not return the source balance before funding",
            observations=observations,
        ),
        field="eth_getBalance(source).result",
        code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SOURCE_BALANCE_UNREADABLE",
    )
    source_minimum = amount + _FUNDING_TX_MAX_FEE_WEI
    if source_balance < source_minimum:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SOURCE_BALANCE_TOO_LOW",
            "captain source balance is below the capped transfer plus maximum fee",
        )

    nonce = _funding_quantity_to_int(
        _rpc_required_result(
            rpc_url=rpc_url,
            method="eth_getTransactionCount",
            params=[source, "pending"],
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_NONCE_UNAVAILABLE",
            message="shared RPC did not return the funding source nonce",
            observations=observations,
        ),
        field="eth_getTransactionCount.result",
        code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_NONCE_UNAVAILABLE",
    )

    raw_transaction, expected_tx_hash, transaction_type = _sign_capped_transfer(
        private_key=private_key,
        expected_source=source,
        chain_id=chain_id,
        nonce=nonce,
        destination=destination,
        amount=amount,
        gas_limit=_FUNDING_GAS_LIMIT,
        max_fee_per_gas_wei=_MAX_FEE_PER_GAS_WEI,
        max_priority_fee_per_gas_wei=_MAX_PRIORITY_FEE_PER_GAS_WEI,
    )

    sent_hash = _normalize_tx_hash(
        _rpc_required_result(
            rpc_url=rpc_url,
            method="eth_sendRawTransaction",
            params=[raw_transaction],
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SEND_FAILED",
            message="shared RPC rejected the locally signed funding transaction",
            observations=observations,
        ),
        code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_TRANSACTION_HASH_UNAVAILABLE",
        message="shared RPC did not return a valid funding transaction hash",
    )
    on_transaction_sent()
    if sent_hash != expected_tx_hash:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_TRANSACTION_HASH_MISMATCH",
            "shared RPC returned a funding transaction hash that does not match the local signature",
        )

    receipt: Mapping[str, Any] | None = None
    receipt_poll_count = 0
    deadline = time.monotonic() + max(0.0, float(max_wait_seconds))
    while True:
        receipt_poll_count += 1
        candidate = _rpc_required_result(
            rpc_url=rpc_url,
            method="eth_getTransactionReceipt",
            params=[sent_hash],
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RECEIPT_UNAVAILABLE",
            message="shared RPC did not answer eth_getTransactionReceipt for the funding transaction",
            observations=observations,
        )
        if isinstance(candidate, Mapping):
            receipt = candidate
            break
        if candidate is not None:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RECEIPT_INVALID",
                "funding transaction receipt result is neither null nor an object",
            )
        if time.monotonic() >= deadline:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RECEIPT_TIMEOUT",
                "funding transaction receipt did not appear before the wait deadline",
            )
        time.sleep(max(0.0, float(poll_interval_seconds)))

    receipt_status = receipt.get("status") if isinstance(receipt, Mapping) else None
    if receipt_status not in {"0x1", "0X1", 1, "1"}:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RECEIPT_FAILED",
            "funding transaction receipt did not report success",
        )

    destination_balance_after = _funding_quantity_to_int(
        _rpc_required_result(
            rpc_url=rpc_url,
            method="eth_getBalance",
            params=[destination, "latest"],
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_BALANCE_UNREADABLE",
            message="shared RPC did not return the destination balance after funding",
            observations=observations,
        ),
        field="eth_getBalance(destination after funding).result",
        code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_BALANCE_UNREADABLE",
    )
    if destination_balance_after != amount:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_A_BALANCE_NOT_VERIFIED",
            "destination balance after funding does not equal the exact transfer amount",
        )

    return {
        "phase": "a_funder-local-json-rpc-result",
        "healthy": True,
        "classification": "funded",
        "result_channel": "local-json-rpc-eth-account",
        "rpc_url": rpc_url,
        "from_address": source,
        "destination": destination,
        "tx_hash": sent_hash,
        "nonce": nonce,
        "gas_limit": _FUNDING_GAS_LIMIT,
        "transaction_type": transaction_type,
        "base_fee_per_gas_wei": base_fee,
        "max_fee_per_gas_wei": _MAX_FEE_PER_GAS_WEI,
        "max_priority_fee_per_gas_wei": _MAX_PRIORITY_FEE_PER_GAS_WEI,
        "source_balance_before_wei": source_balance,
        "source_minimum_required_wei": source_minimum,
        "destination_balance_before_wei": destination_balance_before,
        "destination_balance_after_wei": destination_balance_after,
        "receipt_status": str(receipt_status),
        "receipt_poll_count": receipt_poll_count,
        "observation_count": len(observations),
        "observations": observations,
        "proof": "Mother signed and sent the exact capped canary funding transfer through the shared RPC route using local eth_account",
    }



def _local_shared_balance_proof(
    *,
    spec_key: str,
    rpc_url: str,
    destination: str,
    expected_balance: int | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    """Classify canary balance from the already-proven shared RPC route.

    This replaces stale temporary-service direct-private-RPC probes. The direct
    helper network is not equivalent to the proven coolify-proxy backend network.
    """
    observations: list[dict[str, Any]] = []
    block_number = _funding_quantity_to_int(
        _rpc_required_result(
            rpc_url=rpc_url,
            method="eth_blockNumber",
            params=[],
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RPC_UNAVAILABLE",
            message="shared RPC did not return a block number for canary balance classification",
            observations=observations,
        ),
        field="eth_blockNumber.result",
        code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RPC_UNAVAILABLE",
    )
    balance_wei = _funding_quantity_to_int(
        _rpc_required_result(
            rpc_url=rpc_url,
            method="eth_getBalance",
            params=[destination, "latest"],
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RPC_UNAVAILABLE",
            message="shared RPC did not return the destination balance for canary balance classification",
            observations=observations,
        ),
        field="eth_getBalance(destination).result",
        code="MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_BALANCE_UNREADABLE",
    )
    classification = "rpc-ok" if expected_balance is None else (
        "match" if balance_wei == expected_balance else "nonmatch"
    )
    healthy = expected_balance is None or balance_wei == expected_balance
    proof: dict[str, Any] = {
        "phase": f"{spec_key}-local-shared-rpc-result",
        "healthy": healthy,
        "classification": classification,
        "result_channel": "local-json-rpc-shared-route",
        "rpc_url": rpc_url,
        "destination": destination,
        "block_number": block_number,
        "balance_wei": balance_wei,
        "observation_count": len(observations),
        "observations": observations,
        "proof": "Mother classified the canary balance through the proven shared RPC route instead of a stale direct private-RPC helper network",
    }
    if expected_balance is not None:
        proof["expected_balance_wei"] = expected_balance
    if not healthy:
        proof["reason"] = "balance-nonmatch"
    return proof


def _runtime_result_from_balance_proof(spec_key: str, proof: Mapping[str, Any]) -> dict[str, str]:
    result = {
        "step": spec_key,
        "classification": str(proof.get("classification")),
        "rpc_url": str(proof.get("rpc_url")),
        "block_number": str(proof.get("block_number")),
        "balance_wei": str(proof.get("balance_wei")),
    }
    if "expected_balance_wei" in proof:
        result["expected_balance_wei"] = str(proof.get("expected_balance_wei"))
    return result


def _preflight_allfather_rpc_routes(
    private_state: PrivateStateReadResult,
    *,
    canary_address: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    try:
        contracts = _shared_rpc_route_contracts(private_state)
    except MotherDeploymentValidatorRpcCanaryFundingError as exc:
        return {
            "mode": "mother-shared-rpc-route-direct-json-rpc",
            "clean": False,
            "routes": {},
            "failure": {"code": exc.code, "message": str(exc)},
            "summary": {
                "clean": False,
                "route_count": 0,
                "successful_route_count": 0,
            },
        }

    routes = {
        key: _probe_rpc_route(
            contract,
            canary_address=canary_address,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        for key, contract in contracts.items()
    }
    failed = next((item for item in routes.values() if item.get("ok") is not True), None)
    failure = (
        {
            "code": str(failed["failure_code"]),
            "message": str(failed["failure_message"]),
        }
        if isinstance(failed, Mapping)
        else None
    )
    clean = failure is None and set(routes) == {"shared"}
    return {
        "mode": "mother-shared-rpc-route-direct-json-rpc",
        "clean": clean,
        "routes": routes,
        "failure": failure,
        "summary": {
            "clean": clean,
            "route_count": len(routes),
            "successful_route_count": sum(1 for item in routes.values() if item.get("ok") is True),
            "routes": {
                key: {
                    "node": item.get("node"),
                    "controller_id": item.get("controller_id"),
                    "rpc_url": item.get("rpc_url"),
                    "ok": item.get("ok") is True,
                    "chain_id": item.get("chain_id"),
                    "canary_balance_wei": item.get("canary_balance_wei"),
                }
                for key, item in routes.items()
            },
        },
    }






def _yaml_string(value: str) -> str:
    return json.dumps(str(value))


def _traefik_resource_id(*parts: str) -> str:
    raw = "-".join(str(part) for part in parts if str(part))
    clean = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", clean):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_UNRESOLVED",
            f"unsafe Traefik resource id from {raw!r}",
        )
    return clean


def _render_shared_rpc_traefik_dynamic_config(target: Mapping[str, Any]) -> str:
    route_host = str(target["route_host"])
    target_url = str(target["target_url"])
    controller_id = str(target["controller_id"])
    router_base = _traefik_resource_id("mother", "mainnet", "rpc", controller_id)
    service_id = _traefik_resource_id("mother", "mainnet", "rpc", "svc", controller_id)
    lines = [
        "# Generated by Mother validator-RPC route wiring.",
        "# Source of truth: Mother-owned shared network RPC route.",
        "http:",
        "  routers:",
    ]
    for entrypoint in ("http", "https"):
        router_id = f"{router_base}-{entrypoint}"
        lines.extend(
            [
                f"    {router_id}:",
                "      entryPoints:",
                f"        - {entrypoint}",
                f"      rule: \"Host(`{route_host}`)\"",
                f"      service: {service_id}",
            ]
        )
        if entrypoint == "https":
            lines.extend(["      tls:", "        certResolver: letsencrypt"])
    lines.extend(
        [
            "  services:",
            f"    {service_id}:",
            "      loadBalancer:",
            "        passHostHeader: false",
            "        servers:",
            f"          - url: {_yaml_string(target_url)}",
        ]
    )
    return "\n".join(lines) + "\n"


_ROUTE_WRITER_PY = r"""
from __future__ import annotations

import http.client
import json
import socket
import urllib.parse

TARGET_HOST = __TARGET_HOST__
TARGET_PORT = __TARGET_PORT__
EXPECTED_CHAIN_ID_HEX = __EXPECTED_CHAIN_ID_HEX__
DYNAMIC_CONFIG_B64 = __DYNAMIC_CONFIG_B64__
TARGET_PATH = __TARGET_PATH__

class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        self.sock = sock

def docker(method: str, path: str, body=None):
    payload = None
    headers = {}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn = UnixHTTPConnection("/var/run/docker.sock")
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    status = int(resp.status)
    text = raw.decode("utf-8", "replace") if raw else ""
    if status >= 400:
        raise SystemExit(f"docker-api-{method}-{path}-status-{status}:{text[:300]}")
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text

def find_proxy_id() -> str:
    payload = docker("GET", "/containers/json?all=1")
    if not isinstance(payload, list):
        raise SystemExit("docker-containers-json-not-list")
    candidates = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("Id") or "")
        names = [str(name or "").lstrip("/") for name in item.get("Names") or []]
        state = str(item.get("State") or "")
        if any(name == "coolify-proxy" or name.endswith("_coolify-proxy") or "coolify-proxy" in name for name in names):
            candidates.append({"id": cid, "state": state})
    if not candidates:
        raise SystemExit("coolify-proxy-not-found")
    running = [item for item in candidates if item["state"] == "running"]
    return str((running[0] if running else candidates[0])["id"])

def docker_exec(container_id: str, script: str) -> int:
    payload = docker(
        "POST",
        f"/containers/{urllib.parse.quote(container_id, safe='')}/exec",
        {
            "AttachStdout": True,
            "AttachStderr": True,
            "Cmd": ["sh", "-lc", script],
        },
    )
    if not isinstance(payload, dict) or not payload.get("Id"):
        raise SystemExit("docker-exec-create-no-id")
    exec_id = str(payload["Id"])
    docker(
        "POST",
        f"/exec/{urllib.parse.quote(exec_id, safe='')}/start",
        {
            "Detach": False,
            "Tty": False,
        },
    )
    detail = docker("GET", f"/exec/{urllib.parse.quote(exec_id, safe='')}/json")
    if not isinstance(detail, dict):
        raise SystemExit("docker-exec-inspect-not-object")
    code = detail.get("ExitCode")
    return int(code) if code is not None else 999

proxy_id = find_proxy_id()
rpc_payload = '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'
backend_url = f"http://{TARGET_HOST}:{int(TARGET_PORT)}"
script = (
    "set -eu\n"
    "test -d /traefik/dynamic && test -w /traefik/dynamic\n"
    f"out=$(wget -q -T 8 -O- --header='Content-Type: application/json' --post-data='{rpc_payload}' '{backend_url}' 2>/dev/null || true)\n"
    "printf '%s' \"$out\" | grep -q '\"result\"[[:space:]]*:[[:space:]]*\""
    + EXPECTED_CHAIN_ID_HEX
    + "\"'\n"
    f"printf '%s' '{DYNAMIC_CONFIG_B64}' | base64 -d > '{TARGET_PATH}.tmp'\n"
    f"mv '{TARGET_PATH}.tmp' '{TARGET_PATH}'\n"
    f"chmod 0644 '{TARGET_PATH}'\n"
    f"test -s '{TARGET_PATH}'\n"
)
code = docker_exec(proxy_id, script)
if code != 0:
    raise SystemExit(f"mother-rpc-route-write-failed-exit-{code}")
raise SystemExit(0)
"""


def _route_writer_script(target: Mapping[str, Any]) -> str:
    dynamic_config = _render_shared_rpc_traefik_dynamic_config(target)
    dynamic_config_b64 = base64.b64encode(dynamic_config.encode("utf-8")).decode("ascii")
    target_path = f"/traefik/dynamic/{target['dynamic_file']}"
    return (
        _ROUTE_WRITER_PY
        .replace("__TARGET_HOST__", repr(str(target["target_host"])))
        .replace("__TARGET_PORT__", repr(int(target["target_port"])))
        .replace("__EXPECTED_CHAIN_ID_HEX__", repr(hex(int(target["expected_chain_id"]))))
        .replace("__DYNAMIC_CONFIG_B64__", repr(dynamic_config_b64))
        .replace("__TARGET_PATH__", repr(target_path))
    )


def _route_writer_compose(name: str, target: Mapping[str, Any]) -> str:
    script = _route_writer_script(target)
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    command = f"""mkdir -p /run/mother-helper
printf '%s' '{encoded}' | base64 -d > /run/mother-helper/route_writer.py
chmod 0700 /run/mother-helper/route_writer.py
sleep 900
"""
    healthcheck = "python /run/mother-helper/route_writer.py"
    text = (
        f"name: {name}\n\n"
        "services:\n"
        f"  {name}:\n"
        "    image: python:3.12-alpine\n"
        '    restart: "no"\n'
        "    read_only: true\n"
        "    tmpfs:\n"
        "      - /run/mother-helper:size=256k,mode=0700\n"
        "    volumes:\n"
        "      - /var/run/docker.sock:/var/run/docker.sock\n"
        "    entrypoint:\n"
        "      - /bin/sh\n"
        "      - -ec\n"
        "    command:\n"
        "      - |\n"
        + "\n".join(f"        {line}" for line in command.splitlines())
        + "\n"
        "    healthcheck:\n"
        "      test:\n"
        "        - CMD-SHELL\n"
        f"        - {healthcheck}\n"
        "      interval: 3s\n"
        "      timeout: 12s\n"
        "      retries: 20\n"
        "      start_period: 4s\n"
        "    labels:\n"
        "      main_computer.mother.stage: shared-rpc-route-wiring\n"
        f"      main_computer.mother.route_host: {target['route_host']}\n"
    )
    parsed = yaml.safe_load(text)
    services = parsed.get("services") if isinstance(parsed, Mapping) else None
    service = services.get(name) if isinstance(services, Mapping) else None
    if not (
        isinstance(service, Mapping)
        and list(services) == [name]
        and service.get("image") == "python:3.12-alpine"
        and service.get("entrypoint") == ["/bin/sh", "-ec"]
        and service.get("healthcheck", {}).get("test") == ["CMD-SHELL", healthcheck]
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_UNRESOLVED",
            "compiled route writer Compose is malformed",
        )
    if "ports:" in text or "ghcr.io/foundry-rs/foundry" in text:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_UNRESOLVED",
            "compiled route writer Compose attempts a forbidden capability",
        )
    return text


def _route_writer_application_body(controller: Mapping[str, Any], name: str, compose: str) -> dict[str, Any]:
    return {
        "project_uuid": controller["project_uuid"],
        "server_uuid": controller["server_uuid"],
        "environment_name": "mainnet",
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "name": name,
        "description": "Ephemeral Mother shared validator-RPC Traefik route writer",
        "instant_deploy": False,
    }



_PROXY_RPC_VERIFIER_PY = r"""
from __future__ import annotations

import http.client
import json
import os
import re
import shlex
import socket
import time
import urllib.parse

MARKER = __MARKER__
STEP = __STEP__
MODE = __MODE__
TARGET_HOST = __TARGET_HOST__
TARGET_PORT = __TARGET_PORT__
DESTINATION = __DESTINATION__
AMOUNT = __AMOUNT__
TX_HASH_ENV = __TX_HASH_ENV__

class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        self.sock = sock

def docker(method: str, path: str, body=None):
    payload = None
    headers = {}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn = UnixHTTPConnection("/var/run/docker.sock")
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    status = int(resp.status)
    text = raw.decode("utf-8", "replace") if raw else ""
    if status >= 400:
        raise SystemExit(f"docker-api-{method}-{path}-status-{status}:{text[:300]}")
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text

def find_proxy_id() -> str:
    payload = docker("GET", "/containers/json?all=1")
    if not isinstance(payload, list):
        raise SystemExit("docker-containers-json-not-list")
    candidates = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("Id") or "")
        names = [str(name or "").lstrip("/") for name in item.get("Names") or []]
        state = str(item.get("State") or "")
        if any(name == "coolify-proxy" or name.endswith("_coolify-proxy") or "coolify-proxy" in name for name in names):
            candidates.append({"id": cid, "state": state})
    if not candidates:
        raise SystemExit("coolify-proxy-not-found")
    running = [item for item in candidates if item["state"] == "running"]
    return str((running[0] if running else candidates[0])["id"])

def docker_exec(container_id: str, script: str) -> int:
    payload = docker(
        "POST",
        f"/containers/{urllib.parse.quote(container_id, safe='')}/exec",
        {
            "AttachStdout": True,
            "AttachStderr": True,
            "Cmd": ["sh", "-lc", script],
        },
    )
    if not isinstance(payload, dict) or not payload.get("Id"):
        raise SystemExit("docker-exec-create-no-id")
    exec_id = str(payload["Id"])
    docker(
        "POST",
        f"/exec/{urllib.parse.quote(exec_id, safe='')}/start",
        {
            "Detach": False,
            "Tty": False,
        },
    )
    detail = docker("GET", f"/exec/{urllib.parse.quote(exec_id, safe='')}/json")
    if not isinstance(detail, dict):
        raise SystemExit("docker-exec-inspect-not-object")
    code = detail.get("ExitCode")
    return int(code) if code is not None else 999

def q(value: str) -> str:
    return shlex.quote(value)

def marker(classification: str, **fields: object) -> None:
    parts = [MARKER, f"step={STEP}", f"classification={classification}"]
    for key, value in fields.items():
        text = str(value)
        if re.fullmatch(r"[A-Za-z0-9_.:/-]{0,256}", text):
            parts.append(f"{key}={text}")
    print(" ".join(parts), flush=True)

def balance_payload() -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [DESTINATION, "latest"]})

def receipt_payload(tx_hash: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [tx_hash]})

proxy_id = find_proxy_id()
backend_url = f"http://{TARGET_HOST}:{int(TARGET_PORT)}"
expected_balance_hex = hex(int(AMOUNT))

tx_hash = os.environ.get(TX_HASH_ENV, "").lower()
if MODE == "funded" and not re.fullmatch(r"0x[0-9a-f]{64}", tx_hash):
    marker("bad-tx-hash", rpc_url=backend_url)
    raise SystemExit(70)

receipt_check = ""
if MODE == "funded":
    receipt = q(receipt_payload(tx_hash))
    receipt_check = f'''
receipt_ok=0
deadline=$(( $(date +%s) + 120 ))
while [ "$(date +%s)" -le "$deadline" ]; do
  out=$(wget -q -T 8 -O- --header='Content-Type: application/json' --post-data={receipt} {q(backend_url)} 2>/dev/null || true)
  if printf '%s' "$out" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"0x1"'; then
    receipt_ok=1
    break
  fi
  sleep 3
done
test "$receipt_ok" = "1"
'''

balance = q(balance_payload())
script = f'''
set -eu
{receipt_check}
out=$(wget -q -T 8 -O- --header='Content-Type: application/json' --post-data={balance} {q(backend_url)} 2>/dev/null || true)
printf '%s' "$out" | grep -Eq '"result"[[:space:]]*:[[:space:]]*"{expected_balance_hex}"'
'''
code = docker_exec(proxy_id, script)
if code != 0:
    marker("proxy-rpc-error", rpc_url=backend_url, exit_code=code)
    raise SystemExit(code)

if MODE == "funded":
    marker("verified", rpc_url=backend_url, tx_hash=tx_hash, balance_wei=AMOUNT, expected_balance_wei=AMOUNT)
else:
    marker("match", rpc_url=backend_url, balance_wei=AMOUNT, expected_balance_wei=AMOUNT)
raise SystemExit(0)
"""


def _proxy_rpc_verifier_command(
    *,
    step: str,
    mode: str,
    target_host: str,
    target_port: int,
    destination: str,
    amount: int,
) -> str:
    script = (
        _PROXY_RPC_VERIFIER_PY
        .replace("__MARKER__", repr(_RESULT_MARKER))
        .replace("__STEP__", repr(step))
        .replace("__MODE__", repr(mode))
        .replace("__TARGET_HOST__", repr(target_host))
        .replace("__TARGET_PORT__", repr(int(target_port)))
        .replace("__DESTINATION__", repr(destination))
        .replace("__AMOUNT__", repr(int(amount)))
        .replace("__TX_HASH_ENV__", repr(_TX_HASH_ENV))
    )
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return f"""mkdir -p /run/mother-helper
printf '%s' '{encoded}' | base64 -d > /run/mother-helper/proxy_rpc_verifier.py
chmod 0700 /run/mother-helper/proxy_rpc_verifier.py
python /run/mother-helper/proxy_rpc_verifier.py
exec sleep 900
"""


def _proxy_verifier_compose(name: str, command: str) -> str:
    healthcheck = 'test "$(cat /proc/1/comm)" = "sleep"'
    text = (
        f"name: {name}\n\n"
        "services:\n"
        f"  {name}:\n"
        "    image: python:3.12-alpine\n"
        '    restart: "no"\n'
        "    read_only: true\n"
        "    tmpfs:\n"
        "      - /run/mother-helper:size=256k,mode=0700\n"
        "    volumes:\n"
        "      - /var/run/docker.sock:/var/run/docker.sock\n"
        "    entrypoint:\n"
        "      - /bin/sh\n"
        "      - -ec\n"
        "    command:\n"
        "      - |\n"
        + "\n".join(f"        {line}" for line in command.splitlines())
        + "\n"
        "    healthcheck:\n"
        "      test:\n"
        "        - CMD-SHELL\n"
        f"        - {healthcheck}\n"
        "      interval: 2s\n"
        "      timeout: 5s\n"
        "      retries: 20\n"
        "      start_period: 2s\n"
        "    labels:\n"
        "      main_computer.mother.stage: validator-rpc-canary-funding-proxy-verifier\n"
        f"      main_computer.mother.canary: {name}\n"
    )
    parsed = yaml.safe_load(text)
    services = parsed.get("services") if isinstance(parsed, Mapping) else None
    service = services.get(name) if isinstance(services, Mapping) else None
    if not (
        isinstance(service, Mapping)
        and list(services) == [name]
        and service.get("image") == "python:3.12-alpine"
        and service.get("entrypoint") == ["/bin/sh", "-ec"]
        and service.get("volumes") == ["/var/run/docker.sock:/var/run/docker.sock"]
        and type(service.get("command")) is list
        and len(service["command"]) == 1
        and "exec sleep " in str(service["command"][0])
        and isinstance(service.get("healthcheck"), Mapping)
        and service["healthcheck"].get("test") == ["CMD-SHELL", healthcheck]
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "compiled proxy verifier Compose does not provide the exact status-health result channel",
        )
    forbidden = ("ports:", "secrets:", "traefik.", "0.0.0.0:")
    if any(item in text for item in forbidden):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "compiled proxy verifier Compose attempts a forbidden capability",
        )
    return text



def _compose(name: str, command: str) -> str:
    """Compile one no-port service whose healthy state proves command completion."""
    linger_seconds = 900
    healthcheck = 'test "$(cat /proc/1/comm)" = "sleep"'
    text = (
        f"name: {name}\n\n"
        "services:\n"
        f"  {name}:\n"
        f"    image: {_IMAGE}\n"
        '    restart: "no"\n'
        "    read_only: true\n"
        "    entrypoint:\n"
        "      - /bin/sh\n"
        "      - -ec\n"
        "    command:\n"
        "      - |\n"
        + "\n".join(f"        {line}" for line in command.splitlines())
        + "\n"
        "    healthcheck:\n"
        "      test:\n"
        "        - CMD-SHELL\n"
        f"        - {healthcheck}\n"
        "      interval: 2s\n"
        "      timeout: 5s\n"
        "      retries: 20\n"
        "      start_period: 2s\n"
        "    labels:\n"
        "      main_computer.mother.stage: validator-rpc-canary-funding\n"
        f"      main_computer.mother.canary: {name}\n"
    )
    parsed = yaml.safe_load(text)
    services = parsed.get("services") if isinstance(parsed, Mapping) else None
    service = services.get(name) if isinstance(services, Mapping) else None
    if not (
        isinstance(service, Mapping)
        and list(services) == [name]
        and service.get("entrypoint") == ["/bin/sh", "-ec"]
        and type(service.get("command")) is list
        and len(service["command"]) == 1
        and "exec sleep " in str(service["command"][0])
        and isinstance(service.get("healthcheck"), Mapping)
        and service["healthcheck"].get("test") == ["CMD-SHELL", healthcheck]
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "compiled Compose does not provide the exact status-health result channel",
        )
    forbidden = ("ports:", "volumes:", "secrets:", "traefik.", "0.0.0.0:")
    if any(item in text for item in forbidden):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "compiled Compose attempts a forbidden capability",
        )
    return text






def _marker_line(step: str, classification: str, **fields: object) -> str:
    parts = [
        _RESULT_MARKER,
        f"step={step}",
        f"classification={classification}",
    ]
    for key, value in fields.items():
        clean_key = str(key).strip()
        clean_value = str(value).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", clean_key):
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
                "runtime result marker key is unsafe",
            )
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]{0,256}", clean_value):
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
                "runtime result marker value is unsafe",
            )
        parts.append(f"{clean_key}={clean_value}")
    return " ".join(parts)



def _python_rpc_balance_script(
    *,
    step: str,
    host: str,
    destination: str,
    expected_balance: int | None,
) -> str:
    rpc = f"http://{host}:8545"
    expected = "None" if expected_balance is None else str(expected_balance)
    return f"""set -eu
python - <<'PY'
import json
import sys
import urllib.request

MARKER = {json.dumps(_RESULT_MARKER)}
STEP = {json.dumps(step)}
RPC = {json.dumps(rpc)}
DESTINATION = {json.dumps(destination)}
EXPECTED_BALANCE = {expected}

def rpc(method, params):
    body = json.dumps({{"jsonrpc": "2.0", "id": 1, "method": method, "params": params}}).encode("utf-8")
    request = urllib.request.Request(
        RPC,
        data=body,
        headers={{"Content-Type": "application/json", "Accept": "application/json"}},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(str(payload["error"]))
    return payload.get("result")

def quantity(value):
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError("not a hex quantity")
    return int(value, 16)

try:
    block_number = quantity(rpc("eth_blockNumber", []))
    balance_wei = quantity(rpc("eth_getBalance", [DESTINATION, "latest"]))
except Exception:
    print("%s step=%s classification=rpc-error rpc_url=%s" % (MARKER, STEP, RPC), flush=True)
    raise SystemExit(41)

if EXPECTED_BALANCE is None:
    print("%s step=%s classification=rpc-ok rpc_url=%s block_number=%s balance_wei=%s" % (MARKER, STEP, RPC, block_number, balance_wei), flush=True)
    raise SystemExit(0)

if balance_wei == EXPECTED_BALANCE:
    print("%s step=%s classification=match rpc_url=%s block_number=%s balance_wei=%s expected_balance_wei=%s" % (MARKER, STEP, RPC, block_number, balance_wei, EXPECTED_BALANCE), flush=True)
    raise SystemExit(0)

print("%s step=%s classification=nonmatch rpc_url=%s block_number=%s balance_wei=%s expected_balance_wei=%s" % (MARKER, STEP, RPC, block_number, balance_wei, EXPECTED_BALANCE), flush=True)
raise SystemExit(45)
PY
exec sleep 900
"""


def _rpc_balance_probe_script(host: str, destination: str) -> str:
    return _python_rpc_balance_script(
        step="a_balance_rpc_probe",
        host=host,
        destination=destination,
        expected_balance=None,
    )


def _rpc_balance_equals_script(host: str, destination: str, expected_balance: int) -> str:
    return _python_rpc_balance_script(
        step=(
            "a_zero_balance_classifier"
            if expected_balance == 0
            else "a_exact_balance_classifier"
        ),
        host=host,
        destination=destination,
        expected_balance=expected_balance,
    )


def _balance_classifier_script(host: str, destination: str, expected_balance: int) -> str:
    return _rpc_balance_equals_script(host, destination, expected_balance)


def _funded_verifier_script(source: str, destination: str, amount: int) -> str:
    del source  # source is bound in the release; C proof only needs receipt + exact balance.
    return _proxy_rpc_verifier_command(
        step="c_funded_verifier",
        mode="funded",
        target_host=_C,
        target_port=8545,
        destination=destination,
        amount=amount,
    )


def _exact_balance_verifier_script(host: str, destination: str, amount: int) -> str:
    if host == _C:
        return _proxy_rpc_verifier_command(
            step="c_reconciled_verifier",
            mode="reconciled",
            target_host=_C,
            target_port=8545,
            destination=destination,
            amount=amount,
        )
    return _python_rpc_balance_script(
        step=(
            "a_post_funding_verifier"
            if host == _A
            else "c_reconciled_verifier"
        ),
        host=host,
        destination=destination,
        expected_balance=amount,
    )


def _verifier_script(destination: str, amount: int) -> str:
    """Exact-balance reconciliation proof used when no transfer was required."""
    return _exact_balance_verifier_script(_C, destination, amount)



def _compose_record(text: str) -> dict[str, Any]:
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, Mapping):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "compiled Compose is malformed",
        )
    services = parsed.get("services")
    if not isinstance(services, Mapping) or len(services) != 1:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "compiled Compose must contain exactly one service",
        )
    if "ports" in text or "traefik." in text or "http://" in text.replace(f"http://{_A}:8545", "").replace(f"http://{_C}:8545", ""):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "compiled Compose attempts public exposure",
        )
    return {
        "canonical_text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "service_names": sorted(str(item) for item in services),
    }


def _application_body(controller: Mapping[str, Any], name: str, compose: str) -> dict[str, Any]:
    """Build a deterministic service body before live environment UUID binding."""
    return {
        "project_uuid": controller["project_uuid"],
        "server_uuid": controller["server_uuid"],
        "environment_name": "mainnet",
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "name": name,
        "description": "Ephemeral Mother validator-RPC canary funding service",
        "instant_deploy": False,
    }


def _controller(private_state: PrivateStateReadResult, controller_id: str) -> dict[str, Any]:
    return _controller_config(
        private_state,
        network="mainnet",
        controller_id=controller_id,
    )


def _read_canary_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    selected_nodes: Iterable[str],
    transaction_max_age_seconds: int,
    soak_max_age_seconds: int,
    operation: OperationIdentity,
) -> tuple[dict[str, Any], Path, str]:
    verified = verify_validator_rpc_canary_transaction(
        paths,
        private_state,
        transaction_path,
        selected_nodes=selected_nodes,
        max_age_seconds=transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
        operation=operation,
    )
    if not (
        verified.get("clean") is True
        and verified.get("eip1559_fee_policy_compiled") is True
        and verified.get("maximum_funding_requirement_wei") == 742_000_000_000_000
        and verified.get("validator_mutation_count") == 0
        and verified.get("validator_restart_count") == 0
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SOURCE_INVALID",
            "canary transaction is not the exact fee-hardened authority",
        )
    candidate = Path(transaction_path)
    document, _, file_sha = _canonical_under(
        paths,
        candidate,
        _CANARY_TRANSACTION_DIRECTORY,
        "validator-RPC canary transaction",
    )
    return document, candidate.resolve(strict=False), file_sha



def build_validator_rpc_canary_funding_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    canary_transaction_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    created_at: str | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    canary, canary_path, canary_file_sha = _read_canary_transaction(
        paths,
        private_state,
        Path(canary_transaction_path),
        selected_nodes=selected_nodes,
        transaction_max_age_seconds=transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
        operation=operation,
    )
    fee_policy = _mapping(canary.get("fee_policy"), "canary.fee_policy")
    amount = fee_policy.get("maximum_funding_requirement_wei")
    if amount != 742_000_000_000_000:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "canary funding requirement is not the exact approved ceiling",
        )
    identity = _mapping(canary.get("identity"), "canary.identity")
    destination = _address(identity.get("address"), "canary.address")
    captain = _captain(private_state)
    validator_set = sorted(
        _address(item, "validator address")
        for item in _mapping(canary.get("chain"), "canary.chain").get("validator_set", [])
    )
    if captain["address"] in validator_set or captain["address"] == destination:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_SOURCE_INVALID",
            "captain funding source overlaps a validator or canary identity",
        )

    controllers = {
        _A_CONTROLLER: _controller(private_state, _A_CONTROLLER),
        _C_CONTROLLER: _controller(private_state, _C_CONTROLLER),
    }
    canary_name = str(identity.get("canary_name"))

    def spec(
        key: str,
        *,
        controller_id: str,
        name: str,
        command: str,
        proof: str,
        secret_binding: bool = False,
        proxy_verifier: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        compose = _proxy_verifier_compose(name, command) if proxy_verifier else _compose(name, command)
        controller = controllers[controller_id]
        body = _application_body(controller, name, compose)
        return key, {
            "controller_id": controller_id,
            "application_name": name,
            "compose": _compose_record(compose),
            "create_request_body": body,
            "create_request_body_sha256": hashlib.sha256(canonical_json(body)).hexdigest(),
            "environment_resolution_endpoint": (
                f"/api/v1/projects/{controller['project_uuid']}/environments"
            ),
            "environment_name": "mainnet",
            "result_channel": "coolify-service-detail-health",
            "success_statuses": ["running:healthy", "running:healthy:excluded"],
            "proof": proof,
            "captain_secret_binding_required": secret_binding,
        }

    applications = dict([
        spec(
            "a_balance_rpc_probe",
            controller_id=_A_CONTROLLER,
            name=f"{canary_name}-probe-balance-rpc-a",
            command=_rpc_balance_probe_script(_A, destination),
            proof=(
                "A private RPC answers a Python JSON-RPC balance probe with a parseable quantity "
                "before any balance classification or local funding is started"
            ),
        ),
        spec(
            "a_exact_balance_classifier",
            controller_id=_A_CONTROLLER,
            name=f"{canary_name}-classify-exact-a",
            command=_balance_classifier_script(_A, destination, amount),
            proof="A private RPC reports the exact target balance before any funding service is started",
        ),
        spec(
            "a_zero_balance_classifier",
            controller_id=_A_CONTROLLER,
            name=f"{canary_name}-classify-zero-a",
            command=_balance_classifier_script(_A, destination, 0),
            proof="A private RPC reports a zero destination balance immediately before funding",
        ),
        spec(
            "a_post_funding_verifier",
            controller_id=_A_CONTROLLER,
            name=f"{canary_name}-verify-funded-a",
            command=_exact_balance_verifier_script(_A, destination, amount),
            proof=(
                "A independently verifies the exact destination balance after the "
                "funding service completes"
            ),
        ),
        spec(
            "c_funded_verifier",
            controller_id=_C_CONTROLLER,
            name=f"{canary_name}-verify-funded-c",
            command=_funded_verifier_script(captain["address"], destination, amount),
            proof=(
                "C independently verifies the funding receipt and exact destination balance "
                "through coolify-proxy against the local C Besu RPC backend"
            ),
            proxy_verifier=True,
        ),
        spec(
            "c_reconciled_verifier",
            controller_id=_C_CONTROLLER,
            name=f"{canary_name}-verify-reconciled-c",
            command=_verifier_script(destination, amount),
            proof=(
                "C independently verifies the exact pre-existing destination balance "
                "through coolify-proxy against the local C Besu RPC backend"
            ),
            proxy_verifier=True,
        ),
    ])

    future_mutations: list[dict[str, Any]] = []
    ordinal = 0
    for key in (
        "a_balance_rpc_probe",
        "a_exact_balance_classifier",
        "a_zero_balance_classifier",
        "a_post_funding_verifier",
        "c_funded_verifier",
        "c_reconciled_verifier",
    ):
        app = applications[key]
        name = app["application_name"]
        ordinal += 1
        future_mutations.append({
            "ordinal": ordinal,
            "conditional_service": key,
            "mutation_id": f"{name}.create-service",
            "controller_id": app["controller_id"],
            "method": "POST",
            "endpoint": "/api/v1/services",
            "canonical_request_body": app["create_request_body"],
            "body_materialization": "add-exact-read-only-environment-uuid",
            "success_statuses": [200, 201, 202],
            "bind_result": "service_uuid",
        })
        if key == "c_funded_verifier":
            ordinal += 1
            future_mutations.append({
                "ordinal": ordinal,
                "conditional_service": key,
                "mutation_id": f"{name}.bind-{_TX_HASH_ENV.lower()}",
                "controller_id": app["controller_id"],
                "method": "POST",
                "endpoint_template": "/api/v1/services/${result.service_uuid}/envs",
                "runtime_result_source": "a_funder.tx_hash",
                "environment_key": _TX_HASH_ENV,
                "value_in_transaction": False,
                "success_statuses": [200, 201, 202],
            })
        ordinal += 1
        future_mutations.append({
            "ordinal": ordinal,
            "conditional_service": key,
            "mutation_id": f"{name}.start",
            "controller_id": app["controller_id"],
            "method": "POST",
            "endpoint_template": "/api/v1/services/${result.service_uuid}/start",
            "success_statuses": [200, 201, 202],
        })
        ordinal += 1
        future_mutations.append({
            "ordinal": ordinal,
            "conditional_service": key,
            "mutation_id": f"{name}.delete",
            "controller_id": app["controller_id"],
            "method": "DELETE",
            "endpoint_template": "/api/v1/services/${result.service_uuid}",
            "success_statuses": [200, 204, 404],
            "cleanup_required": True,
        })

    transaction: dict[str, Any] = {
        "kind": _KIND,
        "schema_version": _SCHEMA_VERSION,
        "created_at": _timestamp(created_at),
        "network": "mainnet",
        "mother_binding": _binding(private_state),
        "staged_scope": "offline-exact-capped-validator-rpc-canary-funding-local-python-v7",
        "coolify_transport": {
            "resource_api": "services",
            "create_endpoint": "/api/v1/services",
            "compose_encoding": "base64",
            "environment_uuid_resolution": "read-only-exact-name-before-create",
            "service_start_endpoint_template": "/api/v1/services/{service_uuid}/start",
            "service_start_method": "POST",
            "service_detail_endpoint_template": "/api/v1/services/{service_uuid}",
            "healthy_running_statuses": ["running:healthy", "running:healthy:excluded"],
            "result_channel": "service-detail-health+runtime-result-marker",
            "deployment_uuid_required": False,
            "deployment_inventory_endpoint_authorized": False,
            "deployment_result_endpoint_authorized": False,
            "service_log_endpoints_authorized": True,
            "generic_deploy_endpoint_authorized": False,
        },
        "canary_transaction": {
            "locator": _relative(paths, canary_path, "validator-RPC canary transaction"),
            "file_sha256": canary_file_sha,
            "transaction_sha256": canary["validator_rpc_canary_transaction_sha256"],
        },
        "chain": dict(canary["chain"]),
        "funding_source": {
            "role": "captain",
            "address": captain["address"],
            "private_state_field": captain["private_state_field"],
            "private_key_material_in_transaction": False,
            "genesis_allocated": True,
            "genesis_allocated_balance_wei": captain["genesis_allocated_balance_wei"],
            "validator_identity": False,
            "canary_identity": False,
        },
        "destination": {
            "canary_name": identity["canary_name"],
            "address": destination,
            "identity_locator": identity["identity_locator"],
            "required_post_funding_balance_wei": amount,
            "allowed_pre_execution_balances_wei": [0, amount],
        },
        "funding_policy": {
            "transaction_type": "capped-local-python-signing",
            "transfer_value_wei": amount,
            "transfer_value_cap_wei": amount,
            "base_fee_per_gas_required": True,
            "base_fee_ceiling_wei": _MAX_FEE_PER_GAS_WEI,
            "max_fee_per_gas_wei": _MAX_FEE_PER_GAS_WEI,
            "max_priority_fee_per_gas_wei": _MAX_PRIORITY_FEE_PER_GAS_WEI,
            "gas_limit": _FUNDING_GAS_LIMIT,
            "funding_transaction_max_fee_wei": _FUNDING_TX_MAX_FEE_WEI,
            "source_maximum_total_debit_wei": amount + _FUNDING_TX_MAX_FEE_WEI,
            "source_balance_preflight_required_when_transfer_required": True,
            "destination_zero_or_exact_balance_precondition_required": True,
            "idempotent_exact_balance_reconciliation_supported": True,
            "cross_validator_receipt_verification_required_when_new_transfer": True,
            "cross_validator_balance_verification_required": True,
            "transaction_hash_result_transport_required": False,
            "failed_started_funder_without_health_proof_is_chain_state_unknown": True,
        },
        "applications": applications,
        "rpc_route": {
            "mode": "mother-owned-shared-network-rpc-route",
            "hostname": _shared_rpc_route_host(_network_state(private_state)),
            "https_url": _shared_rpc_route_url(_network_state(private_state)),
            "controller_local_backends": _shared_rpc_route_targets(private_state),
            "route_wiring_authorized": True,
            "proof_required_before_funding": True,
        },
        "future_execution_plan": {
            "route_wiring": [
                "write the Mother-owned shared RPC route on A through coolify-proxy",
                "write the Mother-owned shared RPC route on C through coolify-proxy",
                "prove the shared public RPC route answers before any funding helper starts",
            ],
            "classification": [
                "prove exact balance on A through one healthy classifier",
                "otherwise prove zero balance on A through one healthy classifier",
                "fail closed if neither classifier becomes healthy",
            ],
            "funded_path": [
                "locally sign and send the exact capped EIP-1559 transfer only after zero classification",
                "accept local funder completion only after sendRawTransaction, receipt success, and exact shared-route balance proof",
                "require a separate A exact-balance verifier after the local funder completes",
                "accept C completion only after independent receipt and exact balance verification",
            ],
            "reconciled_path": [
                "do not bind the captain key or start the funder",
                "accept completion only after C independently verifies the exact balance",
            ],
            "mutations": future_mutations,
            "validator_mutations": [],
            "validator_restarts": [],
        },
        "required_secret_bindings": [
            {
                "name": _CAPTAIN_SECRET_ENV,
                "purpose": "one exact capped transfer to the protected validator-RPC canary wallet",
                "source": "Mother private state",
                "source_field": captain["private_state_field"],
                "expected_address": captain["address"],
                "value_in_transaction": False,
                "used_only_after_zero_balance_health_proof": True,
            }
        ],
        "authority": {
            "offline_compilation_only": True,
            "network_access_authorized": False,
            "live_execution_authorized": False,
            "release_authorized": False,
            "funding_authorized": False,
            "funding_value_cap_wei": amount,
            "requested_use_limit": 0,
            "validator_vote_authorized": False,
            "validator_mutation_authorized": False,
            "validator_restart_authorized": False,
            "public_endpoint_authorized": True,
            "public_endpoint_scope": "mainnet-rpc.greatlibrary.io Traefik backend wiring only",
            "ssh_authorized": False,
        },
        "summary": {
            "clean": True,
            "funding_transaction_compiled": True,
            "funding_source_genesis_allocated": True,
            "idempotent_balance_reconcile_or_fund_compiled": True,
            "service_start_transport_compiled": True,
            "service_health_result_channel_compiled": True,
            "runtime_log_result_channel_authorized": True,
            "deployment_uuid_required": False,
            "generic_deploy_endpoint_authorized": False,
            "transfer_value_wei": amount,
            "funding_value_cap_wei": amount,
            "source_maximum_total_debit_wei": amount + _FUNDING_TX_MAX_FEE_WEI,
            "maximum_service_mutation_count": 10,
            "minimum_service_mutation_count": 9,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "public_endpoint_count": 1,
            "host_port_count": 0,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "next_phase": "verify-validator-rpc-canary-funding-transaction",
        },
    }
    transaction["validator_rpc_canary_funding_transaction_sha256"] = _digest_without(
        transaction,
        "validator_rpc_canary_funding_transaction_sha256",
    )
    return transaction



def write_validator_rpc_canary_funding_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(transaction)
    digest = _digest_without(document, "validator_rpc_canary_funding_transaction_sha256")
    if not (
        document.get("kind") == _KIND
        and document.get("schema_version") == _SCHEMA_VERSION
        and document.get("validator_rpc_canary_funding_transaction_sha256") == digest
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "funding transaction is malformed",
        )
    root = _ensure_root(paths, _DIRECTORY, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "transaction"
    destination = root / f"{stamp}-{digest[:16]}.json"
    payload = canonical_json(document)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_CONFLICT",
                "funding transaction destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest



def verify_validator_rpc_canary_funding_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 86400,
    canary_transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    candidate = Path(transaction_path).resolve(strict=False)
    document, _, file_sha = _canonical_under(
        paths,
        candidate,
        _DIRECTORY,
        "validator-RPC canary funding transaction",
    )
    digest = _digest_without(document, "validator_rpc_canary_funding_transaction_sha256")
    if not (
        document.get("kind") == _KIND
        and document.get("schema_version") == _SCHEMA_VERSION
        and document.get("validator_rpc_canary_funding_transaction_sha256") == digest
        and document.get("mother_binding") == _binding(private_state)
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "funding transaction is modified, stale, or contradictory",
        )
    created = _parse_utc(document.get("created_at"), "funding_transaction.created_at")
    current = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    age = int((current - created).total_seconds())
    if age < -15 or age > max_age_seconds:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EXPIRED",
            "funding transaction age is outside the accepted window",
        )
    source = _mapping(document.get("canary_transaction"), "funding.canary_transaction")
    canary_path = _resolve(
        paths,
        source.get("locator"),
        _CANARY_TRANSACTION_DIRECTORY,
        "validator-RPC canary transaction",
    )
    rebuilt = build_validator_rpc_canary_funding_transaction(
        paths,
        private_state,
        canary_path,
        selected_nodes=selected_nodes,
        transaction_max_age_seconds=canary_transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
        created_at=document["created_at"],
        operation=operation,
    )
    if canonical_json(rebuilt) != canonical_json(document):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "funding transaction does not rebuild exactly from canonical authority",
        )
    transport = _mapping(document.get("coolify_transport"), "funding.coolify_transport")
    applications = _mapping(document.get("applications"), "funding.applications")
    if not (
        set(applications)
        == {
            "a_balance_rpc_probe",
            "a_exact_balance_classifier",
            "a_zero_balance_classifier",
            "a_post_funding_verifier",
            "c_funded_verifier",
            "c_reconciled_verifier",
        }
        and transport.get("result_channel") == "service-detail-health+runtime-result-marker"
        and transport.get("deployment_uuid_required") is False
        and transport.get("deployment_inventory_endpoint_authorized") is False
        and transport.get("deployment_result_endpoint_authorized") is False
        and transport.get("service_log_endpoints_authorized") is True
        and transport.get("generic_deploy_endpoint_authorized") is False
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "funding transaction does not bind the exact status-health transport",
        )
    return {
        "clean": True,
        "network": "mainnet",
        "age_seconds": age,
        "transaction_path": str(candidate),
        "transaction_file_sha256": file_sha,
        "transaction_sha256": digest,
        "canary_transaction_sha256": source["transaction_sha256"],
        "funding_source_address": document["funding_source"]["address"],
        "canary_address": document["destination"]["address"],
        "transfer_value_wei": document["funding_policy"]["transfer_value_wei"],
        "funding_value_cap_wei": document["funding_policy"]["transfer_value_cap_wei"],
        "funding_transaction_max_fee_wei": document["funding_policy"]["funding_transaction_max_fee_wei"],
        "source_maximum_total_debit_wei": document["funding_policy"]["source_maximum_total_debit_wei"],
        "destination_zero_or_exact_balance_precondition_required": True,
        "idempotent_exact_balance_reconciliation_supported": True,
        "source_balance_preflight_required_when_transfer_required": True,
        "cross_validator_receipt_verification_required_when_new_transfer": True,
        "cross_validator_balance_verification_required": True,
        "service_start_transport_required": True,
        "service_health_result_channel_required": True,
        "runtime_log_result_channel_authorized": True,
        "deployment_uuid_required": False,
        "deployment_inventory_resolution_required": False,
        "generic_deploy_endpoint_authorized": False,
        "minimum_service_mutation_count": 9,
        "maximum_service_mutation_count": 10,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "public_endpoint_count": 1,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
        "next_phase": "validator-rpc-canary-funding-release-not-yet-authorized",
    }



def _release_duration(value: int) -> int:
    if type(value) is not int or isinstance(value, bool) or not _MIN_RELEASE_SECONDS <= value <= _MAX_RELEASE_SECONDS:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RELEASE_INVALID",
            f"expires_in_seconds must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}",
        )
    return value




def _safe_retry_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
) -> dict[str, Any]:
    """Bind one prior v2 failure only when cleanup and idempotent reclassification are safe."""
    resolved = Path(evidence_path).resolve(strict=False)
    document, _, file_sha = _canonical_under(
        paths,
        resolved,
        _EVIDENCE_DIRECTORY,
        "validator-RPC canary funding recovery evidence",
    )
    digest = _digest_without(document, "validator_rpc_canary_funding_evidence_sha256")
    summary = document.get("summary")
    failure = document.get("failure")
    if not (
        document.get("kind") == _EVIDENCE_KIND
        and document.get("schema_version") == 4
        and document.get("status") == "manual-review-required"
        and document.get("validator_rpc_canary_funding_evidence_sha256") == digest
        and document.get("mother_binding") == _binding(private_state)
        and not _contains_sensitive(document)
        and isinstance(summary, Mapping)
        and isinstance(failure, Mapping)
        and summary.get("temporary_services_deleted") is True
        and summary.get("canary_execution_performed") is False
        and summary.get("validator_mutation_count") == 0
        and summary.get("validator_restart_count") == 0
        and summary.get("public_endpoint_count") in {0, 1}
        and document.get("chain_state") in {
            "unchanged-before-funder-start",
            "potentially-unknown-after-funder-start",
            "exact-on-A-not-yet-verified-on-C",
        }
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RECOVERY_INVALID",
            "recovery evidence is not a canonical cleaned-up status-health failure",
        )
    funding_transaction_hash = document.get("funding_transaction_hash")
    if funding_transaction_hash is not None:
        if type(funding_transaction_hash) is not str or re.fullmatch(r"0x[0-9a-f]{64}", funding_transaction_hash.lower()) is None:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RECOVERY_INVALID",
                "recovery evidence contains an invalid funding transaction hash",
            )
        funding_transaction_hash = funding_transaction_hash.lower()

    return {
        "mode": "idempotent-status-health-reclassification",
        "locator": _relative(
            paths,
            resolved,
            "validator-RPC canary funding recovery evidence",
        ),
        "file_sha256": file_sha,
        "sha256": digest,
        "prior_chain_state": document.get("chain_state"),
        "prior_failure_code": failure.get("code"),
        "prior_cleanup_acknowledged": True,
        "funding_source_address": document.get("funding_source_address"),
        "canary_address": document.get("canary_address"),
        "transfer_value_wei": document.get("transfer_value_wei"),
        "chain": document.get("chain"),
        "funding_mode": document.get("funding_mode"),
        "funding_transaction_hash": funding_transaction_hash,
        "transaction_hash_recorded": funding_transaction_hash is not None,
    }



def build_validator_rpc_canary_funding_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_transaction_sha256: str,
    selected_nodes: Iterable[str] = (),
    transaction_max_age_seconds: int = 86400,
    canary_transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    recovery_evidence_path: Path | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    recovery = (
        _safe_retry_evidence(paths, private_state, Path(recovery_evidence_path))
        if recovery_evidence_path is not None
        else None
    )
    verified = verify_validator_rpc_canary_funding_transaction(
        paths,
        private_state,
        Path(transaction_path),
        selected_nodes=selected_nodes,
        max_age_seconds=transaction_max_age_seconds,
        canary_transaction_max_age_seconds=canary_transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
        operation=operation,
    )
    digest = verified["transaction_sha256"]
    if acknowledged_transaction_sha256 != digest:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_ACK_MISMATCH",
            "acknowledged funding transaction digest does not match",
        )
    transaction, _, file_sha = _canonical_under(
        paths,
        Path(transaction_path),
        _DIRECTORY,
        "validator-RPC canary funding transaction",
    )
    resolved = Path(transaction_path).resolve(strict=False)
    if recovery is not None and not (
        recovery.get("funding_source_address") == transaction["funding_source"]["address"]
        and recovery.get("canary_address") == transaction["destination"]["address"]
        and recovery.get("transfer_value_wei") == transaction["funding_policy"]["transfer_value_wei"]
        and recovery.get("chain") == transaction["chain"]
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RECOVERY_INVALID",
            "recovery evidence does not bind the exact current funding source, destination, amount, and chain",
        )
    created = _parse_utc(_timestamp(created_at), "release.created_at")
    ttl = _release_duration(expires_in_seconds)
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 2,
        "created_at": created.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expires_at": (created + timedelta(seconds=ttl)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "network": "mainnet",
        "mother_binding": _binding(private_state),
        "transaction": {
            "locator": _relative(paths, resolved, "validator-RPC canary funding transaction"),
            "file_sha256": file_sha,
            "sha256": digest,
        },
        "coolify_transport": dict(transaction["coolify_transport"]),
        "chain": dict(transaction["chain"]),
        "funding_source": dict(transaction["funding_source"]),
        "destination": dict(transaction["destination"]),
        "funding_policy": dict(transaction["funding_policy"]),
        "applications": dict(transaction["applications"]),
        "rpc_route": dict(transaction["rpc_route"]),
        "execution_plan": dict(transaction["future_execution_plan"]),
        "recovery": recovery,
        "authority": {
            "requested_use_limit": 1,
            "network_access_authorized": True,
            "live_execution_authorized": True,
            "funding_authorized": True,
            "funding_value_cap_wei": transaction["funding_policy"]["transfer_value_cap_wei"],
            "validator_vote_authorized": False,
            "validator_mutation_authorized": False,
            "validator_restart_authorized": False,
            "public_endpoint_authorized": True,
            "public_endpoint_scope": "mainnet-rpc.greatlibrary.io Traefik backend wiring only",
            "ssh_authorized": False,
        },
        "policy": {
            "exact_transfer_only": True,
            "destination_zero_or_exact_balance_precondition_required": True,
            "idempotent_exact_balance_reconciliation_supported": True,
            "source_balance_preflight_required_when_transfer_required": True,
            "cross_validator_receipt_verification_required_when_new_transfer": True,
            "cross_validator_balance_verification_required": True,
            "service_health_result_channel_required": True,
            "runtime_log_result_channel_authorized": True,
            "deployment_uuid_required": False,
            "failed_started_funder_without_health_proof_is_chain_state_unknown": True,
            "temporary_applications_must_be_deleted": True,
            "canary_execution_authorized": False,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "public_endpoint_count": 1,
        },
    }
    release["validator_rpc_canary_funding_release_sha256"] = _digest_without(
        release,
        "validator_rpc_canary_funding_release_sha256",
    )
    return release




def write_validator_rpc_canary_funding_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(document, "validator_rpc_canary_funding_release_sha256")
    if not (
        document.get("kind") == _RELEASE_KIND
        and document.get("schema_version") == 2
        and document.get("validator_rpc_canary_funding_release_sha256") == digest
        and not _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RELEASE_INVALID",
            "funding release is malformed",
        )
    root = _ensure_root(paths, _RELEASE_DIRECTORY, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "release"
    destination = root / f"{stamp}-{digest[:16]}.json"
    payload = canonical_json(document)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_CONFLICT",
                "funding release destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest




def verify_validator_rpc_canary_funding_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    canary_transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    document, _, file_sha = _canonical_under(
        paths,
        Path(release_path),
        _RELEASE_DIRECTORY,
        "validator-RPC canary funding release",
    )
    resolved = Path(release_path).resolve(strict=False)
    digest = _digest_without(document, "validator_rpc_canary_funding_release_sha256")
    if not (
        document.get("kind") == _RELEASE_KIND
        and document.get("schema_version") == 2
        and document.get("validator_rpc_canary_funding_release_sha256") == digest
        and document.get("mother_binding") == _binding(private_state)
        and not _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RELEASE_INVALID",
            "funding release is modified, stale, or contradictory",
        )
    created = _parse_utc(document.get("created_at"), "release.created_at")
    expires = _parse_utc(document.get("expires_at"), "release.expires_at")
    current = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    age = int((current - created).total_seconds())
    if age < -15 or age > max_age_seconds or current > expires:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RELEASE_EXPIRED",
            "funding release is outside its active window",
        )
    transaction_ref = _mapping(document.get("transaction"), "release.transaction")
    transaction_path = _resolve(
        paths,
        transaction_ref.get("locator"),
        _DIRECTORY,
        "validator-RPC canary funding transaction",
    )
    verified = verify_validator_rpc_canary_funding_transaction(
        paths,
        private_state,
        transaction_path,
        selected_nodes=selected_nodes,
        max_age_seconds=transaction_max_age_seconds,
        canary_transaction_max_age_seconds=canary_transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
        operation=operation,
    )
    transaction, _, transaction_file_sha = _canonical_under(
        paths,
        transaction_path,
        _DIRECTORY,
        "validator-RPC canary funding transaction",
    )
    authority = _mapping(document.get("authority"), "release.authority")
    policy = _mapping(document.get("policy"), "release.policy")
    recovery = document.get("recovery")
    recovery_valid = recovery is None
    if isinstance(recovery, Mapping):
        recovery_path = _resolve(
            paths,
            recovery.get("locator"),
            _EVIDENCE_DIRECTORY,
            "validator-RPC canary funding recovery evidence",
        )
        recovery_valid = dict(recovery) == _safe_retry_evidence(
            paths,
            private_state,
            recovery_path,
        )
    if not (
        transaction_ref.get("sha256") == verified["transaction_sha256"]
        and transaction_ref.get("file_sha256") == transaction_file_sha
        and document.get("coolify_transport") == transaction.get("coolify_transport")
        and document.get("chain") == transaction.get("chain")
        and document.get("funding_source") == transaction.get("funding_source")
        and document.get("destination") == transaction.get("destination")
        and document.get("funding_policy") == transaction.get("funding_policy")
        and document.get("applications") == transaction.get("applications")
        and document.get("rpc_route") == transaction.get("rpc_route")
        and document.get("execution_plan") == transaction.get("future_execution_plan")
        and recovery_valid
        and authority.get("requested_use_limit") == 1
        and authority.get("funding_authorized") is True
        and authority.get("live_execution_authorized") is True
        and authority.get("funding_value_cap_wei") == 742_000_000_000_000
        and authority.get("validator_mutation_authorized") is False
        and authority.get("validator_restart_authorized") is False
        and authority.get("validator_vote_authorized") is False
        and authority.get("public_endpoint_authorized") is True
        and authority.get("public_endpoint_scope") == "mainnet-rpc.greatlibrary.io Traefik backend wiring only"
        and policy.get("canary_execution_authorized") is False
        and policy.get("destination_zero_or_exact_balance_precondition_required") is True
        and policy.get("idempotent_exact_balance_reconciliation_supported") is True
        and policy.get("cross_validator_balance_verification_required") is True
        and policy.get("service_health_result_channel_required") is True
        and policy.get("runtime_log_result_channel_authorized") is True
        and policy.get("deployment_uuid_required") is False
        and policy.get("temporary_applications_must_be_deleted") is True
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RELEASE_INVALID",
            "funding release does not match its verified transaction",
        )
    return {
        "clean": True,
        "network": "mainnet",
        "release_path": str(resolved),
        "release_file_sha256": file_sha,
        "release_sha256": digest,
        "age_seconds": max(0, age),
        "expires_at": document["expires_at"],
        "funding_source_address": document["funding_source"]["address"],
        "canary_address": document["destination"]["address"],
        "transfer_value_wei": document["funding_policy"]["transfer_value_wei"],
        "funding_value_cap_wei": document["funding_policy"]["transfer_value_cap_wei"],
        "minimum_service_mutation_count": 9,
        "maximum_service_mutation_count": 10,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "public_endpoint_count": 1,
        "funding_authorized": True,
        "canary_execution_authorized": False,
        "live_execution_authorized": True,
        "validator_vote_authorized": False,
        "result_channel": "service-detail-health+runtime-result-marker",
        "recovery_mode": (
            document["recovery"]["mode"] if isinstance(document.get("recovery"), Mapping) else None
        ),
    }



def inspect_validator_rpc_canary_funding_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    canary_transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    operation: OperationIdentity,
) -> dict[str, Any]:
    verified = verify_validator_rpc_canary_funding_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        canary_transaction_max_age_seconds=canary_transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
        operation=operation,
    )
    if acknowledged_release_sha256 != verified["release_sha256"]:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_ACK_MISMATCH",
            "acknowledged funding release digest does not match",
        )
    claim_path = _root_for(paths, _CLAIM_DIRECTORY) / f"{verified['release_sha256']}.json"
    return {
        **verified,
        "release_already_claimed": claim_path.exists(),
        "network_access_performed": False,
        "live_mutation_performed": False,
        "funding_performed": False,
        "validator_vote_performed": False,
    }


def _root_for(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return paths.root / parts[0] / parts[1]


def _environment_uuid(payload: Any, expected_name: str) -> str:
    matches: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            name = value.get("name")
            uuid = value.get("uuid")
            if type(name) is str and name.strip() == expected_name and type(uuid) is str:
                clean = uuid.strip()
                if re.fullmatch(r"[A-Za-z0-9._-]{8,96}", clean):
                    matches.add(clean)
            for nested in value.values():
                if isinstance(nested, (Mapping, list)):
                    walk(nested)
        elif type(value) is list:
            for nested in value:
                walk(nested)

    walk(payload)
    if len(matches) != 1:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_ENVIRONMENT_INVALID",
            f"Coolify did not return exactly one {expected_name!r} environment UUID",
        )
    return next(iter(matches))


def _resolve_environment_uuid(
    *,
    controller: Any,
    controller_id: str,
    endpoint: str,
    expected_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
    phase: str,
) -> str:
    response = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    record = {
        "phase": phase,
        "controller_id": controller_id,
        "method": "GET",
        "endpoint": endpoint,
        "http_status": response.get("status"),
        "response_sha256": response.get("response_sha256"),
        "byte_length": response.get("byte_length"),
        "verified": False,
    }
    observations.append(record)
    if response.get("ok") is not True:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_ENVIRONMENT_INVALID",
            f"{controller_id} environment inventory failed with HTTP {response.get('status')}",
        )
    uuid = _environment_uuid(response.get("payload"), expected_name)
    record.update({"verified": True, "environment_name": expected_name, "environment_uuid": uuid})
    return uuid



def _application_uuid(payload: Any) -> str:
    found: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, value in item.items():
                if str(key) in {"uuid", "service_uuid", "application_uuid"} and type(value) is str:
                    clean = value.strip()
                    if re.fullmatch(r"[A-Za-z0-9_-]{8,96}", clean):
                        found.add(clean)
                elif isinstance(value, (Mapping, list)):
                    walk(value)
        elif type(item) is list:
            for value in item:
                walk(value)

    walk(payload)
    if len(found) != 1:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_APPLICATION_INVALID",
            "Coolify service creation did not return exactly one usable UUID",
        )
    return next(iter(found))


def _receipt(
    *,
    mutation_id: str,
    controller_id: str,
    method: str,
    endpoint: str,
    response: Mapping[str, Any],
    succeeded: bool,
    application_uuid: str | None = None,
) -> dict[str, Any]:
    result = {
        "mutation_id": mutation_id,
        "controller_id": controller_id,
        "method": method,
        "endpoint": endpoint,
        "http_status": response.get("status"),
        "response_sha256": response.get("response_sha256"),
        "byte_length": response.get("byte_length"),
        "elapsed_ms": response.get("elapsed_ms"),
        "status": "succeeded" if succeeded else "failed",
        "live_write_acknowledged": bool(succeeded),
    }
    if application_uuid is not None:
        result["application_uuid"] = application_uuid
    return result


def _request_mutation(
    *,
    controller: Any,
    mutation_id: str,
    controller_id: str,
    method: str,
    endpoint: str,
    body: Mapping[str, Any] | None,
    success_statuses: Iterable[int],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    receipts: list[dict[str, Any]],
    application_uuid: str | None = None,
) -> Mapping[str, Any]:
    response = _http(
        controller,
        method,
        endpoint,
        body=body,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    succeeded = int(response["status"]) in set(int(item) for item in success_statuses)
    receipts.append(
        _receipt(
            mutation_id=mutation_id,
            controller_id=controller_id,
            method=method,
            endpoint=endpoint,
            response=response,
            succeeded=succeeded,
            application_uuid=application_uuid,
        )
    )
    if not succeeded:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_MUTATION_FAILED",
            f"{mutation_id} failed with HTTP {response['status']}",
        )
    return response




def _healthy_service_status(value: Any) -> bool:
    status = str(value or "").strip().lower()
    return status in {"running:healthy", "running:healthy:excluded"}


def _service_detail_status(payload: Any, service_uuid: str, expected_name: str) -> str:
    candidates: list[Mapping[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            if (
                value.get("uuid") == service_uuid
                or value.get("name") == expected_name
            ):
                candidates.append(value)
            for nested in value.values():
                if isinstance(nested, (Mapping, list)):
                    walk(nested)
        elif type(value) is list:
            for nested in value:
                walk(nested)

    walk(payload)
    exact_uuid = [
        item for item in candidates
        if item.get("uuid") == service_uuid and type(item.get("status")) is str
    ]
    selected = exact_uuid if exact_uuid else [
        item for item in candidates
        if item.get("name") == expected_name and type(item.get("status")) is str
    ]
    statuses = {str(item.get("status", "")).strip().lower() for item in selected}
    statuses.discard("")
    if len(statuses) != 1:
        return ""
    return next(iter(statuses))



def _collection_shape(value: Any) -> str:
    if value is None:
        return "missing-or-null"
    if type(value) is list:
        return "list"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


def _service_log_subresource_diagnostics(payload: Any, expected_name: str) -> dict[str, Any]:
    inventories: dict[str, list[dict[str, Any]]] = {"applications": [], "databases": []}
    shapes: dict[str, str] = {}
    if isinstance(payload, Mapping):
        for key in inventories:
            values = payload.get(key)
            shapes[key] = _collection_shape(values)
            if type(values) is not list:
                continue
            for item in values:
                if not isinstance(item, Mapping):
                    continue
                safe: dict[str, Any] = {}
                for field in ("name", "uuid", "status", "type"):
                    value = item.get(field)
                    if type(value) is not str:
                        continue
                    clean = value.strip()
                    if not clean or len(clean) > 255:
                        continue
                    if field == "name" and not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", clean):
                        continue
                    if field == "uuid" and not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", clean):
                        continue
                    if field == "status" and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", clean):
                        continue
                    if field == "type" and not re.fullmatch(r"[A-Za-z0-9_.\\:-]{1,255}", clean):
                        continue
                    safe[field] = clean
                inventories[key].append(safe)
    else:
        shapes = {key: "service-detail-not-object" for key in inventories}

    named = [
        item for item in inventories["applications"]
        if type(item.get("name")) is str
    ]
    exact = [item for item in named if item.get("name") == expected_name]
    selected: Mapping[str, Any] | None = None
    reason = "no-candidates"
    if len(exact) == 1:
        selected = exact[0]
        reason = "expected-name-match"
    elif len(named) == 1:
        selected = named[0]
        reason = "single-candidate"
    elif named:
        reason = "ambiguous-candidates"

    return {
        "service_detail_shape": "object" if isinstance(payload, Mapping) else _collection_shape(payload),
        "subresource_collection_shapes": shapes,
        "subresources": inventories,
        "candidate_sub_service_names": sorted({str(item["name"]) for item in named}),
        "selected_sub_service_name": (
            selected.get("name") if isinstance(selected, Mapping) and type(selected.get("name")) is str else None
        ),
        "selected_application_uuid": (
            selected.get("uuid") if isinstance(selected, Mapping) and type(selected.get("uuid")) is str else None
        ),
        "selection_reason": reason,
    }


def _runtime_log_response_classification(response: Mapping[str, Any]) -> str:
    status = response.get("status")
    payload = response.get("payload")
    message = payload.get("message") if isinstance(payload, Mapping) else None
    normalized = message.strip().lower() if type(message) is str else ""
    if status == 200:
        logs = payload.get("logs") if isinstance(payload, Mapping) else None
        return "ok-logs" if type(logs) is str else "ok-without-logs-field"
    if status == 400:
        if "not running" in normalized or "stopped" in normalized or "exited" in normalized:
            return "container-not-running"
        return "bad-request"
    if status == 401:
        return "unauthenticated"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not-found"
    if status == 422:
        return "validation-error"
    if type(status) is int and status >= 500:
        return "server-error"
    return "http-error"


def _parse_runtime_result_markers(logs: str, expected_step: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    prefix = _RESULT_MARKER + " "
    for raw_line in logs.splitlines():
        line = raw_line.strip()
        if prefix not in line:
            continue
        marker = line[line.find(prefix) + len(prefix):]
        fields: dict[str, str] = {}
        for part in marker.split():
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key):
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.:/-]{0,256}", value):
                continue
            fields[key] = value
        if fields.get("step") == expected_step:
            results.append(fields)
    return results[-5:]


def _runtime_marker_proves_success(spec_key: str, marker: Mapping[str, str] | None) -> bool:
    if not isinstance(marker, Mapping):
        return False
    if marker.get("step") != spec_key:
        return False
    classification = marker.get("classification")
    expected = {
        "a_balance_rpc_probe": {"rpc-ok"},
        "a_exact_balance_classifier": {"match"},
        "a_zero_balance_classifier": {"match"},
        "a_funder": {"funded"},
        "a_post_funding_verifier": {"match"},
        "c_funded_verifier": {"verified"},
        "c_reconciled_verifier": {"match"},
    }.get(spec_key, set())
    return classification in expected


def _fetch_runtime_result_markers(
    *,
    controller: Any,
    controller_id: str,
    service_uuid: str,
    service_name: str,
    expected_step: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    phase: str,
) -> dict[str, Any]:
    detail_response = _http(
        controller,
        "GET",
        f"/api/v1/services/{service_uuid}",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    diagnostics = _service_log_subresource_diagnostics(
        detail_response.get("payload"),
        service_name,
    )
    sub_service_name = diagnostics["selected_sub_service_name"]
    application_uuid = diagnostics["selected_application_uuid"]
    record: dict[str, Any] = {
        "phase": phase,
        "controller_id": controller_id,
        "method": "GET",
        "channel": "service-runtime-logs",
        "service_uuid": service_uuid,
        "service_name": service_name,
        "expected_step": expected_step,
        "service_detail_http_status": detail_response.get("status"),
        "service_detail_response_sha256": detail_response.get("response_sha256"),
        "service_detail_byte_length": detail_response.get("byte_length"),
        "service_detail_shape": diagnostics["service_detail_shape"],
        "subresource_collection_shapes": diagnostics["subresource_collection_shapes"],
        "subresources": diagnostics["subresources"],
        "candidate_sub_service_names": diagnostics["candidate_sub_service_names"],
        "selected_sub_service_name": sub_service_name,
        "selected_application_uuid": application_uuid,
        "selection_reason": diagnostics["selection_reason"],
        "attempts": [],
        "runtime_result_markers": [],
        "runtime_result_marker_count": 0,
        "runtime_result_marker_observed": False,
        "observed_at": _timestamp(),
    }
    if sub_service_name is None:
        record["response_classification"] = "sub-service-name-unresolved"
        return record

    candidates: list[tuple[str, str, dict[str, str]]] = []
    if application_uuid is not None:
        service_q = urllib.parse.quote(service_uuid, safe="")
        app_q = urllib.parse.quote(application_uuid, safe="")
        candidates.extend([
            (
                "service-application",
                f"/api/v1/services/{service_q}/applications/{app_q}/logs?lines=100&show_timestamps=false",
                {"lines": "100", "show_timestamps": "false"},
            ),
            (
                "application-resource",
                f"/api/v1/applications/{app_q}/logs?lines=100",
                {"lines": "100"},
            ),
        ])
    candidates.append(
        (
            "parent-service-fallback",
            (
                f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}/logs"
                f"?sub_service_name={urllib.parse.quote(sub_service_name, safe='')}"
                "&lines=100&show_timestamps=false"
            ),
            {
                "sub_service_name": sub_service_name,
                "lines": "100",
                "show_timestamps": "false",
            },
        )
    )

    selected: Mapping[str, Any] | None = None
    selected_logs: str | None = None
    for endpoint_kind, endpoint, query in candidates:
        response = _http(
            controller,
            "GET",
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        payload = response.get("payload")
        logs = payload.get("logs") if isinstance(payload, Mapping) else None
        markers = _parse_runtime_result_markers(logs, expected_step) if type(logs) is str else []
        attempt = {
            "endpoint_kind": endpoint_kind,
            "endpoint": endpoint.split("?", 1)[0],
            "query_parameters": query,
            "http_status": response.get("status"),
            "response_sha256": response.get("response_sha256"),
            "byte_length": response.get("byte_length"),
            "elapsed_ms": response.get("elapsed_ms"),
            "response_classification": _runtime_log_response_classification(response),
            "logs_field_present": type(logs) is str,
            "runtime_result_marker_count": len(markers),
            "runtime_result_marker_observed": bool(markers),
        }
        record["attempts"].append(attempt)
        selected = response
        selected_logs = logs if type(logs) is str else None
        if markers:
            record["runtime_result_markers"] = markers
            record["runtime_result_marker_count"] = len(markers)
            record["runtime_result_marker_observed"] = True
            record["endpoint"] = attempt["endpoint"]
            record["endpoint_kind"] = endpoint_kind
            record["query_parameters"] = query
            record["http_status"] = response.get("status")
            record["response_sha256"] = response.get("response_sha256")
            record["byte_length"] = response.get("byte_length")
            record["elapsed_ms"] = response.get("elapsed_ms")
            record["response_classification"] = attempt["response_classification"]
            return record
        if response.get("status") == 200:
            # A successful log response with no marker may be the wrong Coolify
            # subresource or an empty log slice. Continue through every
            # authorized log endpoint before declaring the runtime result absent.
            continue
        if response.get("status") in {400, 404, 405, 422}:
            continue
        break

    if selected is not None:
        record["http_status"] = selected.get("status")
        record["response_sha256"] = selected.get("response_sha256")
        record["byte_length"] = selected.get("byte_length")
        record["elapsed_ms"] = selected.get("elapsed_ms")
        record["response_classification"] = _runtime_log_response_classification(selected)
        record["logs_field_present"] = type(selected_logs) is str
    return record


def _wait_for_service_health(
    *,
    controller: Any,
    controller_id: str,
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    observations: list[dict[str, Any]],
    phase: str,
) -> dict[str, Any]:
    if max_wait_seconds < 0 or poll_interval_seconds < 0:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "wait and poll intervals must be non-negative",
        )
    endpoint = f"/api/v1/services/{service_uuid}"
    started = time.monotonic()
    last_status = ""
    first_status = ""
    observed_statuses: list[str] = []
    observation_count = 0
    while True:
        response = _http(
            controller,
            "GET",
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        status = (
            _service_detail_status(response.get("payload"), service_uuid, service_name)
            if response.get("ok")
            else ""
        )
        observation_count += 1
        if status and not first_status:
            first_status = status
        if status:
            observed_statuses.append(status)
        last_status = status or last_status
        elapsed = time.monotonic() - started
        record = {
            "phase": phase,
            "controller_id": controller_id,
            "method": "GET",
            "endpoint": endpoint,
            "http_status": response.get("status"),
            "response_sha256": response.get("response_sha256"),
            "byte_length": response.get("byte_length"),
            "service_uuid": service_uuid,
            "service_name": service_name,
            "service_status": status or None,
            "healthy": _healthy_service_status(status),
            "result_channel": "service-detail-health",
            "observed_at": _timestamp(),
        }
        observations.append(record)
        if record["healthy"]:
            return {
                "healthy": True,
                "service_status": status,
                "first_status": first_status or None,
                "final_status": status or None,
                "observed_statuses": observed_statuses,
                "service_uuid": service_uuid,
                "service_name": service_name,
                "phase": phase,
                "observation_count": observation_count,
                "wait_seconds": int(elapsed),
                "wait_milliseconds": int(round(elapsed * 1000)),
            }

        if elapsed >= max_wait_seconds:
            return {
                "healthy": False,
                "service_status": last_status or None,
                "first_status": first_status or None,
                "final_status": last_status or None,
                "observed_statuses": observed_statuses,
                "service_uuid": service_uuid,
                "service_name": service_name,
                "phase": phase,
                "observation_count": observation_count,
                "wait_seconds": int(elapsed),
                "wait_milliseconds": int(round(elapsed * 1000)),
                "reason": "health-timeout",
            }
        time.sleep(min(poll_interval_seconds, max(0.0, max_wait_seconds - elapsed)))


def _recovered_funding_transaction_hash(release: Mapping[str, Any]) -> str | None:
    recovery = release.get("recovery")
    if not isinstance(recovery, Mapping):
        return None
    tx_hash = recovery.get("funding_transaction_hash")
    if type(tx_hash) is not str:
        return None
    tx_hash = tx_hash.lower()
    if re.fullmatch(r"0x[0-9a-f]{64}", tx_hash) is None:
        return None
    if recovery.get("prior_chain_state") != "exact-on-A-not-yet-verified-on-C":
        return None
    if recovery.get("prior_failure_code") != "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RESULT_INVALID":
        return None
    return tx_hash


def _write_funding_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(evidence)
    if _contains_sensitive(document):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
            "funding evidence contains sensitive material",
        )
    digest = _digest_without(document, "validator_rpc_canary_funding_evidence_sha256")
    document["validator_rpc_canary_funding_evidence_sha256"] = digest
    root = _ensure_root(paths, _EVIDENCE_DIRECTORY, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "evidence"
    destination = root / f"{stamp}-{digest[:16]}.json"
    payload = canonical_json(document)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_CONFLICT",
                "funding evidence destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest




def execute_validator_rpc_canary_funding_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    canary_transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_validator_rpc_canary_funding_release(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        canary_transaction_max_age_seconds=canary_transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
        operation=operation,
    )
    if inspected["release_already_claimed"]:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RELEASE_ALREADY_CONSUMED",
            "funding release already has an execution claim",
        )
    release, _, _ = _canonical_under(
        paths,
        Path(release_path),
        _RELEASE_DIRECTORY,
        "validator-RPC canary funding release",
    )
    resolved_release = Path(release_path).resolve(strict=False)
    release_sha = inspected["release_sha256"]
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {
            "locator": _relative(
                paths,
                resolved_release,
                "validator-RPC canary funding release",
            ),
            "sha256": release_sha,
        },
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_path = _ensure_root(paths, _CLAIM_DIRECTORY, operation) / f"{release_sha}.json"
    if claim_path.exists():
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RELEASE_ALREADY_CONSUMED",
            "funding release already has an execution claim",
        )
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)

    controllers = {
        _A_CONTROLLER: resolve_coolify_controller(private_state, "mainnet", _A_CONTROLLER),
        _C_CONTROLLER: resolve_coolify_controller(private_state, "mainnet", _C_CONTROLLER),
    }
    captain = _captain(private_state)
    applications = _mapping(release.get("applications"), "release.applications")
    transport = _mapping(release.get("coolify_transport"), "release.coolify_transport")
    if not (
        transport.get("result_channel") == "service-detail-health+runtime-result-marker"
        and transport.get("service_detail_endpoint_template")
        == "/api/v1/services/{service_uuid}"
        and transport.get("service_start_endpoint_template")
        == "/api/v1/services/{service_uuid}/start"
        and transport.get("service_start_method") == "POST"
        and transport.get("deployment_uuid_required") is False
        and transport.get("deployment_inventory_endpoint_authorized") is False
        and transport.get("deployment_result_endpoint_authorized") is False
        and transport.get("service_log_endpoints_authorized") is True
        and transport.get("generic_deploy_endpoint_authorized") is False
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RELEASE_INVALID",
            "funding release does not authorize the exact service-health transport",
        )

    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None
    environment_uuids: dict[str, str] = {}
    created_services: dict[str, str] = {}
    deleted_services: dict[str, bool] = {}
    service_controller_ids: dict[str, str] = {}
    proofs: dict[str, Mapping[str, Any]] = {}
    runtime_results: dict[str, Mapping[str, str]] = {}
    rpc_route_preflight: dict[str, Any] | None = None
    rpc_route_wiring: dict[str, Any] | None = None
    funding_mode: str | None = None
    funding_start_acknowledged = False
    a_funder_health_proven = False
    a_post_funding_balance_proven = False
    cross_validator_proof: dict[str, Any] | None = None
    funding_transaction_hash: str | None = None
    chain_state = "unchanged-before-funder-start"
    started_at = _timestamp()
    expected_amount = int(release["funding_policy"]["transfer_value_wei"])
    destination = str(release["destination"]["address"]).lower()
    recovered_funding_transaction_hash = _recovered_funding_transaction_hash(release)

    def run_service(
        spec_key: str,
        *,
        bind_captain_secret: bool = False,
        extra_env: Mapping[str, str] | None = None,
        require_runtime_marker: bool = True,
    ) -> Mapping[str, Any]:
        nonlocal funding_start_acknowledged, a_funder_health_proven, a_post_funding_balance_proven
        spec = _mapping(applications.get(spec_key), f"release.applications.{spec_key}")
        controller_id = str(spec["controller_id"])
        controller = controllers[controller_id]
        service_name = str(spec["application_name"])
        service_uuid: str | None = None
        proof: Mapping[str, Any] | None = None
        service_controller_ids[service_name] = controller_id
        try:
            if controller_id not in environment_uuids:
                environment_uuids[controller_id] = _resolve_environment_uuid(
                    controller=controller,
                    controller_id=controller_id,
                    endpoint=str(spec["environment_resolution_endpoint"]),
                    expected_name=str(spec["environment_name"]),
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                    observations=observations,
                    phase=f"{spec_key}-mainnet-environment-resolution",
                )
            create_body = dict(
                _mapping(
                    spec.get("create_request_body"),
                    f"{spec_key}.create_request_body",
                )
            )
            create_body["environment_uuid"] = environment_uuids[controller_id]
            create_response = _request_mutation(
                controller=controller,
                mutation_id=f"{service_name}.create-service",
                controller_id=controller_id,
                method="POST",
                endpoint="/api/v1/services",
                body=create_body,
                success_statuses=(200, 201, 202),
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                receipts=receipts,
            )
            service_uuid = _application_uuid(create_response["payload"])
            created_services[service_name] = service_uuid
            receipts[-1].update({
                "application_uuid": service_uuid,
                "service_name": service_name,
                "request_body_sha256": hashlib.sha256(
                    canonical_json(create_body)
                ).hexdigest(),
            })

            if bind_captain_secret:
                secret_body = {
                    "key": _CAPTAIN_SECRET_ENV,
                    "value": captain["private_key"],
                    "is_preview": False,
                    "is_literal": True,
                    "is_multiline": False,
                    "is_shown_once": True,
                }
                _request_mutation(
                    controller=controller,
                    mutation_id=f"{service_name}.bind-captain-secret",
                    controller_id=controller_id,
                    method="POST",
                    endpoint=f"/api/v1/services/{service_uuid}/envs",
                    body=secret_body,
                    success_statuses=(200, 201, 202),
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                    receipts=receipts,
                    application_uuid=service_uuid,
                )
                receipts[-1]["service_name"] = service_name

            for env_key, env_value in (extra_env or {}).items():
                if not re.fullmatch(r"[A-Z0-9_]{1,96}", str(env_key)):
                    raise _error(
                        "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
                        "temporary service environment key is invalid",
                    )
                if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", str(env_value)):
                    raise _error(
                        "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
                        "temporary service environment value is invalid",
                    )
                _request_mutation(
                    controller=controller,
                    mutation_id=f"{service_name}.bind-{env_key.lower()}",
                    controller_id=controller_id,
                    method="POST",
                    endpoint=f"/api/v1/services/{service_uuid}/envs",
                    body={
                        "key": str(env_key),
                        "value": str(env_value),
                        "is_preview": False,
                        "is_literal": True,
                        "is_multiline": False,
                        "is_shown_once": False,
                    },
                    success_statuses=(200, 201, 202),
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                    receipts=receipts,
                    application_uuid=service_uuid,
                )
                receipts[-1]["service_name"] = service_name

            _request_mutation(
                controller=controller,
                mutation_id=f"{service_name}.start",
                controller_id=controller_id,
                method="POST",
                endpoint=f"/api/v1/services/{service_uuid}/start",
                body=None,
                success_statuses=(200, 201, 202),
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                receipts=receipts,
                application_uuid=service_uuid,
            )
            receipts[-1]["service_name"] = service_name
            if spec_key == "a_funder":
                funding_start_acknowledged = True

            proof = _wait_for_service_health(
                controller=controller,
                controller_id=controller_id,
                service_uuid=service_uuid,
                service_name=service_name,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                opener=opener,
                observations=observations,
                phase=f"{spec_key}-status-health-result",
            )
            log_record = _fetch_runtime_result_markers(
                controller=controller,
                controller_id=controller_id,
                service_uuid=service_uuid,
                service_name=service_name,
                expected_step=spec_key,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                phase=f"{spec_key}-runtime-result-marker",
            )
            observations.append(log_record)
            markers = log_record.get("runtime_result_markers")
            marker = markers[-1] if type(markers) is list and markers else None
            if isinstance(marker, Mapping):
                runtime_results[spec_key] = marker
            marker_success = _runtime_marker_proves_success(spec_key, marker)
            proof = dict(proof)
            if proof.get("healthy") is True and not require_runtime_marker:
                proof.setdefault("result_channel", "service-detail-health")
            proof["runtime_result_marker_observed"] = bool(markers)
            proof["runtime_result"] = runtime_results.get(spec_key)
            proof["runtime_result_classification"] = (
                marker.get("classification") if isinstance(marker, Mapping) else None
            )
            proof["runtime_result_proves_success"] = marker_success
            if marker_success:
                proof["healthy"] = True
                proof["reason"] = "runtime-result-marker-proved-success"
                proof["result_channel"] = "runtime-result-marker"
            elif require_runtime_marker:
                proof["healthy"] = False
                proof["reason"] = (
                    "runtime-result-marker-failed"
                    if isinstance(marker, Mapping)
                    else "runtime-result-marker-missing"
                )
            proofs[spec_key] = proof
            if spec_key == "a_funder" and proof.get("healthy") is True:
                a_funder_health_proven = True
            if spec_key == "a_post_funding_verifier" and proof.get("healthy") is True:
                a_post_funding_balance_proven = True
            return proof
        finally:
            if service_uuid is not None:
                response = _http(
                    controller,
                    "DELETE",
                    f"/api/v1/services/{service_uuid}",
                    body=None,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                deleted = int(response.get("status", 0)) in {200, 204, 404}
                receipt = _receipt(
                    mutation_id=f"{service_name}.delete",
                    controller_id=controller_id,
                    method="DELETE",
                    endpoint=f"/api/v1/services/{service_uuid}",
                    response=response,
                    succeeded=deleted,
                    application_uuid=service_uuid,
                )
                receipt["service_name"] = service_name
                receipt["cleanup_absent"] = response.get("status") == 404
                receipts.append(receipt)
                deleted_services[service_name] = deleted
                if not deleted:
                    raise _error(
                        "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_CLEANUP_FAILED",
                        f"temporary service cleanup failed for {service_name}",
                    )

    def run_route_writer(route_key: str, target: Mapping[str, Any]) -> Mapping[str, Any]:
        controller_id = str(target["controller_id"])
        controller = controllers[controller_id]
        service_name = f"mother-mainnet-rpc-route-{route_key}"
        service_uuid: str | None = None
        service_controller_ids[service_name] = controller_id
        compose = _route_writer_compose(service_name, target)
        proof: Mapping[str, Any] | None = None
        try:
            if controller_id not in environment_uuids:
                environment_uuids[controller_id] = _resolve_environment_uuid(
                    controller=controller,
                    controller_id=controller_id,
                    endpoint=f"/api/v1/projects/{_controller(private_state, controller_id)['project_uuid']}/environments",
                    expected_name="mainnet",
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                    observations=observations,
                    phase=f"{route_key}_shared_rpc_route_writer-mainnet-environment-resolution",
                )
            create_body = _route_writer_application_body(
                _controller(private_state, controller_id),
                service_name,
                compose,
            )
            create_body["environment_uuid"] = environment_uuids[controller_id]
            create_response = _request_mutation(
                controller=controller,
                mutation_id=f"{service_name}.create-service",
                controller_id=controller_id,
                method="POST",
                endpoint="/api/v1/services",
                body=create_body,
                success_statuses=(200, 201, 202),
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                receipts=receipts,
            )
            service_uuid = _application_uuid(create_response["payload"])
            created_services[service_name] = service_uuid
            receipts[-1].update({
                "application_uuid": service_uuid,
                "service_name": service_name,
                "request_body_sha256": hashlib.sha256(canonical_json(create_body)).hexdigest(),
            })
            _request_mutation(
                controller=controller,
                mutation_id=f"{service_name}.start",
                controller_id=controller_id,
                method="POST",
                endpoint=f"/api/v1/services/{service_uuid}/start",
                body=None,
                success_statuses=(200, 201, 202),
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                receipts=receipts,
                application_uuid=service_uuid,
            )
            receipts[-1]["service_name"] = service_name
            proof = _wait_for_service_health(
                controller=controller,
                controller_id=controller_id,
                service_uuid=service_uuid,
                service_name=service_name,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                opener=opener,
                observations=observations,
                phase=f"{route_key}_shared_rpc_route_writer-status-health-result",
            )
            proof = {
                **dict(proof),
                "route_host": target.get("route_host"),
                "target_host": target.get("target_host"),
                "target_port": target.get("target_port"),
                "dynamic_file": target.get("dynamic_file"),
                "result_channel": "coolify-service-detail-health",
                "proof": "coolify-proxy wrote Mother-owned shared RPC dynamic route after proving local Besu eth_chainId",
            }
            proofs[f"{route_key}_shared_rpc_route_writer"] = proof
            if proof.get("healthy") is not True:
                raise _error(
                    "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_WIRING_FAILED",
                    f"{controller_id} did not prove shared RPC route wiring for {target.get('target_host')}:{target.get('target_port')}",
                )
            return proof
        finally:
            if service_uuid is not None:
                response = _http(
                    controller,
                    "DELETE",
                    f"/api/v1/services/{service_uuid}",
                    body=None,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                deleted = int(response.get("status", 0)) in {200, 204, 404}
                receipt = _receipt(
                    mutation_id=f"{service_name}.delete",
                    controller_id=controller_id,
                    method="DELETE",
                    endpoint=f"/api/v1/services/{service_uuid}",
                    response=response,
                    succeeded=deleted,
                    application_uuid=service_uuid,
                )
                receipt["service_name"] = service_name
                receipt["cleanup_absent"] = response.get("status") == 404
                receipts.append(receipt)
                deleted_services[service_name] = deleted
                if not deleted:
                    raise _error(
                        "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_CLEANUP_FAILED",
                        f"temporary service cleanup failed for {service_name}",
                    )

    try:
        route_targets = _mapping(
            _mapping(release.get("rpc_route"), "release.rpc_route").get("controller_local_backends"),
            "release.rpc_route.controller_local_backends",
        )
        route_wiring_results = {
            key: run_route_writer(key, _mapping(route_targets.get(key), f"release.rpc_route.controller_local_backends.{key}"))
            for key in ("a", "c")
        }
        rpc_route_wiring = {
            "mode": "mother-owned-shared-rpc-route-wiring",
            "route_host": _mapping(release.get("rpc_route"), "release.rpc_route").get("hostname"),
            "clean": all(item.get("healthy") is True for item in route_wiring_results.values()),
            "results": route_wiring_results,
            "summary": {
                "clean": all(item.get("healthy") is True for item in route_wiring_results.values()),
                "controller_count": len(route_wiring_results),
                "successful_controller_count": sum(1 for item in route_wiring_results.values() if item.get("healthy") is True),
            },
        }
        if rpc_route_wiring.get("clean") is not True:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_ROUTE_WIRING_FAILED",
                "Mother did not prove shared RPC route wiring on every controller",
            )
        rpc_route_preflight = _preflight_allfather_rpc_routes(
            private_state,
            canary_address=destination,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        if rpc_route_preflight.get("clean") is not True:
            preflight_failure = _mapping(
                rpc_route_preflight.get("failure"),
                "allfather_rpc_route_preflight.failure",
            )
            raise _error(
                str(preflight_failure["code"]),
                str(preflight_failure["message"]),
            )

        shared_rpc_url = _shared_rpc_route_url(_network_state(private_state))
        probe_proof = _local_shared_balance_proof(
            spec_key="a_balance_rpc_probe",
            rpc_url=shared_rpc_url,
            destination=destination,
            expected_balance=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        proofs["a_balance_rpc_probe"] = probe_proof
        runtime_results["a_balance_rpc_probe"] = _runtime_result_from_balance_proof(
            "a_balance_rpc_probe",
            probe_proof,
        )
        if probe_proof.get("healthy") is not True:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RPC_UNAVAILABLE",
                "shared RPC did not answer a local Python JSON-RPC balance probe before funding",
            )

        exact_proof = _local_shared_balance_proof(
            spec_key="a_exact_balance_classifier",
            rpc_url=shared_rpc_url,
            destination=destination,
            expected_balance=expected_amount,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        proofs["a_exact_balance_classifier"] = exact_proof
        runtime_results["a_exact_balance_classifier"] = _runtime_result_from_balance_proof(
            "a_exact_balance_classifier",
            exact_proof,
        )
        if exact_proof.get("healthy") is True:
            funding_mode = "already-funded"
            if recovered_funding_transaction_hash is not None:
                funding_transaction_hash = recovered_funding_transaction_hash
            chain_state = "exact-on-A-not-yet-verified-on-C"
        else:
            zero_proof = _local_shared_balance_proof(
                spec_key="a_zero_balance_classifier",
                rpc_url=shared_rpc_url,
                destination=destination,
                expected_balance=0,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            proofs["a_zero_balance_classifier"] = zero_proof
            runtime_results["a_zero_balance_classifier"] = _runtime_result_from_balance_proof(
                "a_zero_balance_classifier",
                zero_proof,
            )
            if zero_proof.get("healthy") is not True:
                observed = runtime_results.get("a_zero_balance_classifier") or runtime_results.get("a_exact_balance_classifier") or {}
                observed_balance = observed.get("balance_wei") if isinstance(observed, Mapping) else None
                suffix = (
                    f" observed_balance_wei={observed_balance}"
                    if type(observed_balance) is str
                    else ""
                )
                raise _error(
                    "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_UNEXPECTED_BALANCE",
                    "shared RPC is reachable, but the destination balance is neither zero nor the exact funded amount" + suffix,
                )
            funding_mode = "funded"

            def _mark_local_funding_sent() -> None:
                nonlocal funding_start_acknowledged
                funding_start_acknowledged = True

            funder_proof = _execute_local_python_funding(
                private_key=captain["private_key"],
                source=str(release["funding_source"]["address"]).lower(),
                destination=destination,
                amount=expected_amount,
                chain_id=int(release["chain"]["chain_id"]),
                rpc_url=_shared_rpc_route_url(_network_state(private_state)),
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                opener=opener,
                on_transaction_sent=_mark_local_funding_sent,
            )
            proofs["a_funder"] = funder_proof
            runtime_results["a_funder"] = {
                "step": "a_funder",
                "classification": "funded",
                "rpc_url": str(funder_proof["rpc_url"]),
                "tx_hash": str(funder_proof["tx_hash"]),
                "balance_wei": str(funder_proof["destination_balance_after_wei"]),
                "expected_balance_wei": str(expected_amount),
            }
            a_funder_health_proven = True
            funding_transaction_hash = str(funder_proof["tx_hash"]).lower()

            a_post_funding_proof = _local_shared_balance_proof(
                spec_key="a_post_funding_verifier",
                rpc_url=shared_rpc_url,
                destination=destination,
                expected_balance=expected_amount,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            proofs["a_post_funding_verifier"] = a_post_funding_proof
            runtime_results["a_post_funding_verifier"] = _runtime_result_from_balance_proof(
                "a_post_funding_verifier",
                a_post_funding_proof,
            )
            if a_post_funding_proof.get("healthy") is not True:
                raise _error(
                    "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_A_BALANCE_NOT_VERIFIED",
                    "shared RPC did not prove the exact destination balance after local funding",
                )
            a_post_funding_balance_proven = True
            chain_state = "exact-on-A-not-yet-verified-on-C"

        c_key = (
            "c_funded_verifier"
            if funding_transaction_hash is not None
            else "c_reconciled_verifier"
        )
        c_extra_env = (
            {_TX_HASH_ENV: funding_transaction_hash}
            if funding_transaction_hash is not None
            else None
        )
        c_proof = run_service(c_key, extra_env=c_extra_env, require_runtime_marker=False)
        if c_proof.get("healthy") is not True:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RESULT_INVALID",
                "C verifier did not reach its committed healthy verification state",
            )
        cross_validator_proof = {
            "mode": funding_mode,
            "controller_id": _C_CONTROLLER,
            "service_name": c_proof["service_name"],
            "service_uuid": c_proof["service_uuid"],
            "service_status": c_proof["service_status"],
            "result_channel": str(c_proof.get("result_channel") or "service-detail-health"),
            "runtime_result": runtime_results.get(c_key),
            "runtime_result_marker_observed": c_proof.get("runtime_result_marker_observed") is True,
            "balance_verified": True,
            "receipt_verified": funding_transaction_hash is not None,
            "transaction_hash_recorded": funding_transaction_hash is not None,
            "funding_transaction_hash": funding_transaction_hash,
            "proof": (
                "tx-hash-bound-cross-validator-receipt-and-balance"
                if funding_transaction_hash is not None
                else "exact-balance-reconciliation"
            ),
        }
        chain_state = "exact-cross-validator-verified"
    except Exception as exc:
        failure = {
            "code": str(
                getattr(
                    exc,
                    "code",
                    "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EXECUTION_FAILED",
                )
            ),
            "message": str(exc).replace("\r", " ").replace("\n", " ").strip()[:300],
        }
        if funding_start_acknowledged and not a_funder_health_proven:
            chain_state = "potentially-unknown-after-funder-start"
        elif a_funder_health_proven and cross_validator_proof is None:
            chain_state = "exact-on-A-not-yet-verified-on-C"

    all_deleted = all(
        deleted_services.get(name) is True for name in created_services
    )
    a_names = [
        name for name, controller_id in service_controller_ids.items()
        if controller_id == _A_CONTROLLER and name in created_services
    ]
    c_names = [
        name for name, controller_id in service_controller_ids.items()
        if controller_id == _C_CONTROLLER and name in created_services
    ]
    a_deleted = bool(a_names) and all(deleted_services.get(name) is True for name in a_names)
    c_deleted = bool(c_names) and all(deleted_services.get(name) is True for name in c_names)
    success = bool(
        failure is None
        and funding_mode in {"funded", "already-funded"}
        and (funding_mode == "already-funded" or a_post_funding_balance_proven)
        and cross_validator_proof is not None
        and chain_state == "exact-cross-validator-verified"
        and all_deleted
        and all(item.get("status") == "succeeded" for item in receipts)
    )
    completed_at = _timestamp()
    funding_performed = success and funding_mode == "funded"
    funding_reconciled = success and funding_mode == "already-funded"
    receipt_verified = bool(
        success
        and cross_validator_proof
        and cross_validator_proof.get("receipt_verified") is True
    )
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 4,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "pass" if success else "manual-review-required",
        "network": "mainnet",
        "mother_binding": _binding(private_state),
        "release": {
            "locator": _relative(
                paths,
                resolved_release,
                "validator-RPC canary funding release",
            ),
            "sha256": release_sha,
        },
        "execution_claim": {
            "locator": _relative(
                paths,
                claim_path,
                "validator-RPC canary funding execution claim",
            ),
        },
        "chain": dict(release["chain"]),
        "allfather_rpc_route_wiring": rpc_route_wiring,
        "allfather_rpc_route_preflight": rpc_route_preflight,
        "funding_source_address": release["funding_source"]["address"],
        "canary_address": destination,
        "transfer_value_wei": expected_amount,
        "funding_mode": funding_mode,
        "funding_transaction_hash": funding_transaction_hash,
        "transaction_hash_recorded": funding_transaction_hash is not None,
        "chain_state": chain_state,
        "cross_validator_verification": cross_validator_proof,
        "mutation_receipts": receipts,
        "service_observations": observations,
        "runtime_proofs": proofs,
        "runtime_results": runtime_results,
        "failure": failure,
        "summary": {
            "clean": success,
            "complete": success,
            "funding_complete": success,
            "funding_performed": funding_performed,
            "funding_reconciled_from_prior_execution": funding_reconciled,
            "funding_receipt_verified_on_C": receipt_verified,
            "canary_balance_verified_on_A": success,
            "canary_balance_verified_on_C": success,
            "exact_transfer_value_verified": success,
            "transaction_hash_recorded": funding_transaction_hash is not None,
            "allfather_rpc_route_wiring_used": rpc_route_wiring is not None,
            "allfather_rpc_route_wiring_complete": bool(
                isinstance(rpc_route_wiring, Mapping)
                and rpc_route_wiring.get("clean") is True
            ),
            "allfather_rpc_route_preflight_used": rpc_route_preflight is not None,
            "allfather_rpc_route_preflight_complete": bool(
                isinstance(rpc_route_preflight, Mapping)
                and rpc_route_preflight.get("clean") is True
            ),
            "service_health_result_channel_used": bool(created_services),
            "runtime_log_result_channel_used": bool(created_services),
            "runtime_result_marker_count": len(runtime_results),
            "deployment_uuid_required": False,
            "temporary_A_application_deleted": a_deleted,
            "temporary_C_application_deleted": c_deleted,
            "temporary_services_deleted": all_deleted,
            "temporary_service_count": len(created_services),
            "application_mutation_count": len(receipts),
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "public_endpoint_count": 1,
            "validator_vote_performed": False,
            "canary_execution_performed": False,
            "next_phase": (
                "validator-rpc-canary-execution-release-not-yet-authorized"
                if success
                else "manual-review-required"
            ),
        },
    }
    evidence_path, evidence_sha = _write_funding_evidence(
        paths,
        evidence,
        operation=operation,
    )
    return {
        "status": evidence["status"],
        "network": "mainnet",
        "chain_id": release["chain"]["chain_id"],
        "canary_address": destination,
        "transfer_value_wei": expected_amount,
        "funding_mode": funding_mode,
        "funding_transaction_hash": funding_transaction_hash,
        "transaction_hash_recorded": funding_transaction_hash is not None,
        "chain_state": chain_state,
        "summary": evidence["summary"],
        "evidence": {"path": str(evidence_path), "sha256": evidence_sha},
    }




def verify_validator_rpc_canary_funding_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    canary_transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    operation: OperationIdentity,
) -> dict[str, Any]:
    document, _, file_sha = _canonical_under(
        paths,
        Path(evidence_path),
        _EVIDENCE_DIRECTORY,
        "validator-RPC canary funding evidence",
    )
    resolved = Path(evidence_path).resolve(strict=False)
    digest = _digest_without(document, "validator_rpc_canary_funding_evidence_sha256")
    if not (
        document.get("kind") == _EVIDENCE_KIND
        and document.get("schema_version") == 4
        and document.get("status") == "pass"
        and document.get("validator_rpc_canary_funding_evidence_sha256") == digest
        and document.get("mother_binding") == _binding(private_state)
        and not _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
            "funding evidence is not a passing canonical document",
        )
    completed = _parse_utc(document.get("completed_at"), "evidence.completed_at")
    age = int((datetime.now(timezone.utc) - completed).total_seconds())
    if age < -15 or age > max_age_seconds:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_EXPIRED",
            "funding evidence is outside the accepted verification window",
        )
    release_ref = _mapping(document.get("release"), "evidence.release")
    release_path = _resolve(
        paths,
        release_ref.get("locator"),
        _RELEASE_DIRECTORY,
        "validator-RPC canary funding release",
    )
    release, _, _ = _canonical_under(
        paths,
        release_path,
        _RELEASE_DIRECTORY,
        "validator-RPC canary funding release",
    )
    release_sha = _digest_without(
        release,
        "validator_rpc_canary_funding_release_sha256",
    )
    if not (
        release_ref.get("sha256") == release_sha
        and release.get("validator_rpc_canary_funding_release_sha256") == release_sha
        and release.get("mother_binding") == _binding(private_state)
        and release.get("schema_version") == 2
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
            "funding evidence release binding is invalid",
        )
    transaction_ref = _mapping(release.get("transaction"), "release.transaction")
    transaction_path = _resolve(
        paths,
        transaction_ref.get("locator"),
        _DIRECTORY,
        "validator-RPC canary funding transaction",
    )
    verified_transaction = verify_validator_rpc_canary_funding_transaction(
        paths,
        private_state,
        transaction_path,
        selected_nodes=selected_nodes,
        max_age_seconds=transaction_max_age_seconds,
        canary_transaction_max_age_seconds=canary_transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
        operation=operation,
    )
    if transaction_ref.get("sha256") != verified_transaction["transaction_sha256"]:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
            "funding evidence transaction binding is invalid",
        )
    claim_ref = _mapping(document.get("execution_claim"), "evidence.execution_claim")
    claim_path = _resolve(
        paths,
        claim_ref.get("locator"),
        _CLAIM_DIRECTORY,
        "validator-RPC canary funding execution claim",
    )
    claim, _, _ = _canonical_under(
        paths,
        claim_path,
        _CLAIM_DIRECTORY,
        "validator-RPC canary funding execution claim",
    )
    if not (
        claim.get("kind") == _CLAIM_KIND
        and claim.get("requested_use_limit") == 1
        and claim.get("release", {}).get("sha256") == release_sha
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
            "funding execution claim does not bind the release",
        )

    receipts = document.get("mutation_receipts")
    observations = document.get("service_observations")
    applications = _mapping(release.get("applications"), "release.applications")
    funding_mode = document.get("funding_mode")
    recorded_tx_hash = document.get("funding_transaction_hash")
    recorded_tx_hash_valid = (
        type(recorded_tx_hash) is str
        and re.fullmatch(r"0x[0-9a-fA-F]{64}", recorded_tx_hash) is not None
    )
    if funding_mode == "funded":
        spec_keys = [
            "a_balance_rpc_probe",
            "a_exact_balance_classifier",
            "a_zero_balance_classifier",
            "a_post_funding_verifier",
            "c_funded_verifier",
        ]
        runtime_result_keys = [
            "a_balance_rpc_probe",
            "a_exact_balance_classifier",
            "a_zero_balance_classifier",
            "a_post_funding_verifier",
            "a_funder",
        ]
        required_healthy = {
            "a_balance_rpc_probe",
            "a_zero_balance_classifier",
            "a_post_funding_verifier",
            "c_funded_verifier",
        }
        required_nonhealthy = {"a_exact_balance_classifier"}
    elif funding_mode == "already-funded":
        c_reconciliation_key = (
            "c_funded_verifier"
            if recorded_tx_hash_valid
            else "c_reconciled_verifier"
        )
        spec_keys = [
            "a_balance_rpc_probe",
            "a_exact_balance_classifier",
            c_reconciliation_key,
        ]
        runtime_result_keys = [
            "a_balance_rpc_probe",
            "a_exact_balance_classifier",
        ]
        required_healthy = {
            "a_balance_rpc_probe",
            "a_exact_balance_classifier",
            c_reconciliation_key,
        }
        required_nonhealthy = set()
    else:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
            "funding evidence mode is invalid",
        )

    service_spec_keys = [key for key in spec_keys if key.startswith("c_")]
    service_required_healthy = {key for key in required_healthy if key.startswith("c_")}
    service_required_nonhealthy = {key for key in required_nonhealthy if key.startswith("c_")}
    local_required_healthy = set(required_healthy) - service_required_healthy
    local_required_nonhealthy = set(required_nonhealthy) - service_required_nonhealthy

    expected_ids: list[str] = []
    expected_methods: list[str] = []
    for route_key in ("a", "c"):
        route_name = f"mother-mainnet-rpc-route-{route_key}"
        expected_ids.extend([
            f"{route_name}.create-service",
            f"{route_name}.start",
            f"{route_name}.delete",
        ])
        expected_methods.extend(["POST", "POST", "DELETE"])
    for key in service_spec_keys:
        spec = _mapping(applications.get(key), f"release.applications.{key}")
        name = str(spec["application_name"])
        expected_ids.append(f"{name}.create-service")
        expected_methods.append("POST")
        if key == "a_funder":
            expected_ids.append(f"{name}.bind-captain-secret")
            expected_methods.append("POST")
        if key == "c_funded_verifier":
            expected_ids.append(f"{name}.bind-{_TX_HASH_ENV.lower()}")
            expected_methods.append("POST")
        expected_ids.extend([f"{name}.start", f"{name}.delete"])
        expected_methods.extend(["POST", "DELETE"])

    if not (
        type(receipts) is list
        and [item.get("mutation_id") for item in receipts] == expected_ids
        and [item.get("method") for item in receipts] == expected_methods
        and all(item.get("status") == "succeeded" for item in receipts)
        and all(item.get("live_write_acknowledged") is True for item in receipts)
        and type(observations) is list
        and all(
            "/deployments" not in str(item.get("endpoint", ""))
            for item in observations
            if isinstance(item, Mapping)
        )
        and all(
            "/logs" not in str(item.get("endpoint", ""))
            and "/deployments" not in str(item.get("endpoint", ""))
            for item in receipts
            if isinstance(item, Mapping)
        )
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
            "funding evidence mutation or transport receipts are contradictory",
        )

    def phase_observations(key: str) -> list[Mapping[str, Any]]:
        phase = f"{key}-status-health-result"
        return [
            item
            for item in observations
            if isinstance(item, Mapping) and item.get("phase") == phase
        ]

    for key in service_required_healthy:
        found = phase_observations(key)
        if not (
            found
            and any(
                item.get("healthy") is True
                and _healthy_service_status(item.get("service_status"))
                and item.get("result_channel") == "service-detail-health"
                for item in found
            )
        ):
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
                f"{key} lacks its committed healthy service proof",
            )
    for key in service_required_nonhealthy:
        found = phase_observations(key)
        if not (
            found
            and all(item.get("healthy") is False for item in found)
        ):
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
                f"{key} unexpectedly contains a healthy proof",
            )

    runtime_proofs = _mapping(document.get("runtime_proofs"), "evidence.runtime_proofs")
    for key in local_required_healthy:
        proof = _mapping(runtime_proofs.get(key), f"evidence.runtime_proofs.{key}")
        if not (
            proof.get("healthy") is True
            and str(proof.get("result_channel")).startswith("local-json-rpc-")
        ):
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
                f"{key} lacks its committed local shared-route proof",
            )
    for key in local_required_nonhealthy:
        proof = _mapping(runtime_proofs.get(key), f"evidence.runtime_proofs.{key}")
        if proof.get("healthy") is not False:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
                f"{key} unexpectedly contains a healthy local proof",
            )

    if funding_mode == "funded":
        local_funder = _mapping(runtime_proofs.get("a_funder"), "evidence.runtime_proofs.a_funder")
        if not (
            local_funder.get("healthy") is True
            and local_funder.get("result_channel") == "local-json-rpc-eth-account"
            and type(local_funder.get("tx_hash")) is str
            and re.fullmatch(r"0x[0-9a-fA-F]{64}", local_funder["tx_hash"])
            and local_funder.get("destination_balance_after_wei") == document.get("transfer_value_wei")
        ):
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
                "local a_funder proof is missing or contradictory",
            )

    runtime_results = _mapping(document.get("runtime_results"), "evidence.runtime_results")
    for key in runtime_result_keys:
        result = _mapping(runtime_results.get(key), f"evidence.runtime_results.{key}")
        if not (
            result.get("step") == key
            and type(result.get("classification")) is str
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", result["classification"])
        ):
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
                f"{key} lacks its structured runtime result marker",
            )

    c_result = document.get("cross_validator_verification")
    rpc_route_preflight = _mapping(
        document.get("allfather_rpc_route_preflight"),
        "evidence.allfather_rpc_route_preflight",
    )
    rpc_route_summary = _mapping(
        rpc_route_preflight.get("summary"),
        "evidence.allfather_rpc_route_preflight.summary",
    )
    summary = _mapping(document.get("summary"), "evidence.summary")
    common = (
        isinstance(c_result, Mapping)
        and c_result.get("mode") == funding_mode
        and c_result.get("controller_id") == _C_CONTROLLER
        and c_result.get("result_channel") in {"service-detail-health", "runtime-result-marker", "service-detail-health+runtime-result-marker"}
        and _healthy_service_status(c_result.get("service_status"))
        and c_result.get("balance_verified") is True
        and document.get("transaction_hash_recorded") == recorded_tx_hash_valid
        and document.get("chain_state") == "exact-cross-validator-verified"
        and document["transfer_value_wei"] == release["funding_policy"]["transfer_value_wei"]
        and document["canary_address"] == release["destination"]["address"]
        and summary.get("clean") is True
        and summary.get("complete") is True
        and summary.get("funding_complete") is True
        and summary.get("canary_balance_verified_on_A") is True
        and summary.get("canary_balance_verified_on_C") is True
        and summary.get("exact_transfer_value_verified") is True
        and summary.get("transaction_hash_recorded") == recorded_tx_hash_valid
        and rpc_route_preflight.get("mode") == "mother-shared-rpc-route-direct-json-rpc"
        and rpc_route_preflight.get("clean") is True
        and rpc_route_summary.get("successful_route_count") == 1
        and summary.get("allfather_rpc_route_wiring_used") is True
        and summary.get("allfather_rpc_route_wiring_complete") is True
        and summary.get("allfather_rpc_route_preflight_used") is True
        and summary.get("allfather_rpc_route_preflight_complete") is True
        and summary.get("service_health_result_channel_used") is True
        and summary.get("runtime_log_result_channel_used") is True
        and type(summary.get("runtime_result_marker_count")) is int
        and summary.get("runtime_result_marker_count") >= len(runtime_result_keys)
        and summary.get("deployment_uuid_required") is False
        and summary.get("temporary_A_application_deleted") is True
        and summary.get("temporary_C_application_deleted") is True
        and summary.get("temporary_services_deleted") is True
        and summary.get("temporary_service_count") == len(service_spec_keys) + 2
        and summary.get("application_mutation_count") == len(expected_ids)
        and summary.get("validator_mutation_count") == 0
        and summary.get("validator_restart_count") == 0
        and summary.get("public_endpoint_count") == 1
        and summary.get("validator_vote_performed") is False
        and summary.get("canary_execution_performed") is False
    )
    funded = (
        funding_mode == "funded"
        and c_result.get("receipt_verified") is True
        and c_result.get("proof") == "tx-hash-bound-cross-validator-receipt-and-balance"
        and c_result.get("transaction_hash_recorded") is True
        and type(c_result.get("funding_transaction_hash")) is str
        and c_result.get("funding_transaction_hash") == document.get("funding_transaction_hash")
        and type(document.get("funding_transaction_hash")) is str
        and re.fullmatch(r"0x[0-9a-fA-F]{64}", document.get("funding_transaction_hash", "")) is not None
        and summary.get("funding_performed") is True
        and summary.get("funding_reconciled_from_prior_execution") is False
        and summary.get("funding_receipt_verified_on_C") is True
    )
    reconciled_balance_only = (
        funding_mode == "already-funded"
        and c_result.get("receipt_verified") is False
        and c_result.get("proof") == "exact-balance-reconciliation"
        and c_result.get("transaction_hash_recorded") is False
        and document.get("funding_transaction_hash") in {None, ""}
        and summary.get("funding_performed") is False
        and summary.get("funding_reconciled_from_prior_execution") is True
        and summary.get("funding_receipt_verified_on_C") is False
    )
    reconciled_with_recovered_tx = (
        funding_mode == "already-funded"
        and c_result.get("receipt_verified") is True
        and c_result.get("proof") == "tx-hash-bound-cross-validator-receipt-and-balance"
        and c_result.get("transaction_hash_recorded") is True
        and c_result.get("funding_transaction_hash") == document.get("funding_transaction_hash")
        and recorded_tx_hash_valid
        and summary.get("funding_performed") is False
        and summary.get("funding_reconciled_from_prior_execution") is True
        and summary.get("funding_receipt_verified_on_C") is True
    )
    reconciled = reconciled_balance_only or reconciled_with_recovered_tx
    receipt_verified = funded or reconciled_with_recovered_tx
    if not (common and (funded or reconciled)):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
            "funding evidence status-health proof facts are contradictory",
        )
    return {
        "clean": True,
        "network": "mainnet",
        "age_seconds": max(0, age),
        "evidence_path": str(resolved),
        "evidence_file_sha256": file_sha,
        "evidence_sha256": digest,
        "release_sha256": release_sha,
        "transaction_sha256": verified_transaction["transaction_sha256"],
        "funding_mode": funding_mode,
        "funding_transaction_hash": document.get("funding_transaction_hash"),
        "transaction_hash_recorded": document.get("transaction_hash_recorded") is True,
        "funding_source_address": document["funding_source_address"],
        "canary_address": document["canary_address"],
        "transfer_value_wei": document["transfer_value_wei"],
        "funding_receipt_verified_on_C": receipt_verified,
        "canary_balance_verified_on_A": True,
        "canary_balance_verified_on_C": True,
        "funding_reconciled_from_prior_execution": reconciled,
        "result_channel": "service-detail-health+runtime-result-marker",
        "temporary_applications_deleted": True,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "public_endpoint_count": 0,
        "validator_vote_performed": False,
        "canary_execution_performed": False,
        "next_phase": "validator-rpc-canary-execution-release-not-yet-authorized",
    }


