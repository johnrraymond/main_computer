"""Offline compiler for bounded validator-RPC canary funding.

This phase consumes a verified fee-hardened validator-RPC canary transaction and
binds the genesis-funded Mother captain wallet to one exact EIP-1559 transfer.
It performs no network access, signs nothing, and authorizes no release or live
execution.
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
import urllib.parse

import yaml

from . import atomic_files
from .canonical import canonical_json
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
from .deployment_private_rpc import _controller_config
from .deployment_validator_admission_executor import _http
from .deployment_validator_rpc_canary import (
    _TRANSACTION_DIRECTORY as _CANARY_TRANSACTION_DIRECTORY,
    verify_validator_rpc_canary_transaction,
)
from .ethereum_identity import is_private_key, private_key_to_address
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_transaction.v3"
_SCHEMA_VERSION = 3
_DIRECTORY = ("actions", "deployment-validator-rpc-canary-funding-transactions")
_RELEASE_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_evidence.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-validator-rpc-canary-funding-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-validator-rpc-canary-funding-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-validator-rpc-canary-funding")
_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900
_A = "mainneta-super1"
_C = "mainnetc-super1"
_A_CONTROLLER = "coolify-a"
_C_CONTROLLER = "coolify-c"
_IMAGE = "ghcr.io/foundry-rs/foundry:latest"
_CAPTAIN_SECRET_ENV = "MC_MOTHER_CAPTAIN_PRIVATE_KEY"
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


def _compose(name: str, command: str) -> str:
    return (
        f"name: {name}\n\n"
        "services:\n"
        f"  {name}:\n"
        f"    image: {_IMAGE}\n"
        "    restart: \"no\"\n"
        "    read_only: true\n"
        "    command:\n"
        "      - sh\n"
        "      - -ec\n"
        "      - |\n"
        + "\n".join(f"        {line}" for line in command.splitlines())
        + "\n"
        "    labels:\n"
        "      main_computer.mother.stage: validator-rpc-canary-funding\n"
        f"      main_computer.mother.canary: {name}\n"
    )


def _funder_script(source: str, destination: str, amount: int) -> str:
    return f"""set -eu
test -n "${{{_CAPTAIN_SECRET_ENV}:-}}"
RPC=http://{_A}:8545
BASE=$(cast rpc --rpc-url "$RPC" eth_getBlockByNumber latest false | python -c 'import json,sys; v=json.load(sys.stdin).get("baseFeePerGas"); assert isinstance(v,str) and v.startswith("0x"); print(int(v,16))')
test "$BASE" -le {_MAX_FEE_PER_GAS_WEI}
FROM=$(cast wallet address --private-key "${{{_CAPTAIN_SECRET_ENV}}}" | tr '[:upper:]' '[:lower:]')
test "$FROM" = "{source}"
DEST_BAL=$(cast balance --rpc-url "$RPC" --ether=false {destination})
test "$DEST_BAL" -eq 0
SOURCE_BAL=$(cast balance --rpc-url "$RPC" --ether=false {source})
test "$SOURCE_BAL" -ge {amount + _FUNDING_TX_MAX_FEE_WEI}
TX=$(cast send --json --rpc-url "$RPC" --private-key "${{{_CAPTAIN_SECRET_ENV}}}" --gas-limit {_FUNDING_GAS_LIMIT} --gas-price {_MAX_FEE_PER_GAS_WEI} --priority-gas-price {_MAX_PRIORITY_FEE_PER_GAS_WEI} --value {amount} {destination})
printf 'MOTHER_VALIDATOR_RPC_CANARY_FUNDING_A_RESULT=%s\n' "$TX"
"""


def _verifier_script(destination: str, amount: int) -> str:
    return f"""set -eu
RPC=http://{_C}:8545
test -n "${{MC_MOTHER_CANARY_FUNDING_TX_HASH:-}}"
RECEIPT=$(cast receipt --json --rpc-url "$RPC" "$MC_MOTHER_CANARY_FUNDING_TX_HASH")
printf '%s' "$RECEIPT" | python -c 'import json,sys; v=json.load(sys.stdin); assert int(v["status"],16)==1'
BAL=$(cast balance --rpc-url "$RPC" --ether=false {destination})
test "$BAL" -eq {amount}
printf 'MOTHER_VALIDATOR_RPC_CANARY_FUNDING_C_RESULT={{"transaction_hash":"%s","destination":"{destination}","balance_wei":"%s"}}\n' "$MC_MOTHER_CANARY_FUNDING_TX_HASH" "$BAL"
"""


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
    a_controller = _controller(private_state, _A_CONTROLLER)
    c_controller = _controller(private_state, _C_CONTROLLER)
    name = str(identity.get("canary_name"))
    a_name = f"{name}-fund-a"
    c_name = f"{name}-fund-c"
    a_compose = _compose(a_name, _funder_script(captain["address"], destination, amount))
    c_compose = _compose(c_name, _verifier_script(destination, amount))
    a_body = _application_body(a_controller, a_name, a_compose)
    c_body = _application_body(c_controller, c_name, c_compose)
    transaction: dict[str, Any] = {
        "kind": _KIND,
        "schema_version": _SCHEMA_VERSION,
        "created_at": _timestamp(created_at),
        "network": "mainnet",
        "mother_binding": _binding(private_state),
        "staged_scope": "offline-exact-capped-validator-rpc-canary-funding",
        "coolify_transport": {
            "resource_api": "services",
            "create_endpoint": "/api/v1/services",
            "deprecated_application_create_endpoint_authorized": False,
            "compose_encoding": "base64",
            "environment_uuid_resolution": "read-only-exact-name-before-create",
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
            "pre_funding_balance_must_equal_wei": 0,
        },
        "funding_policy": {
            "transaction_type": "eip1559",
            "transfer_value_wei": amount,
            "transfer_value_cap_wei": amount,
            "base_fee_per_gas_required": True,
            "base_fee_ceiling_wei": _MAX_FEE_PER_GAS_WEI,
            "max_fee_per_gas_wei": _MAX_FEE_PER_GAS_WEI,
            "max_priority_fee_per_gas_wei": _MAX_PRIORITY_FEE_PER_GAS_WEI,
            "gas_limit": _FUNDING_GAS_LIMIT,
            "funding_transaction_max_fee_wei": _FUNDING_TX_MAX_FEE_WEI,
            "source_maximum_total_debit_wei": amount + _FUNDING_TX_MAX_FEE_WEI,
            "source_balance_preflight_required": True,
            "destination_zero_balance_precondition_required": True,
            "cross_validator_receipt_and_balance_verification_required": True,
        },
        "applications": {
            "a_funder": {
                "controller_id": _A_CONTROLLER,
                "application_name": a_name,
                "compose": _compose_record(a_compose),
                "create_request_body": a_body,
                "create_request_body_sha256": hashlib.sha256(canonical_json(a_body)).hexdigest(),
                "environment_resolution_endpoint": (
                    f"/api/v1/projects/{a_controller['project_uuid']}/environments"
                ),
                "environment_name": "mainnet",
                "result_marker": "MOTHER_VALIDATOR_RPC_CANARY_FUNDING_A_RESULT=",
                "rpc_url": f"http://{_A}:8545",
            },
            "c_verifier": {
                "controller_id": _C_CONTROLLER,
                "application_name": c_name,
                "compose": _compose_record(c_compose),
                "create_request_body": c_body,
                "create_request_body_sha256": hashlib.sha256(canonical_json(c_body)).hexdigest(),
                "environment_resolution_endpoint": (
                    f"/api/v1/projects/{c_controller['project_uuid']}/environments"
                ),
                "environment_name": "mainnet",
                "result_marker": "MOTHER_VALIDATOR_RPC_CANARY_FUNDING_C_RESULT=",
                "rpc_url": f"http://{_C}:8545",
            },
        },
        "future_execution_plan": {
            "read_only_preconditions": [
                {
                    "phase": "A-mainnet-environment-resolution",
                    "controller_id": _A_CONTROLLER,
                    "method": "GET",
                    "endpoint": f"/api/v1/projects/{a_controller['project_uuid']}/environments",
                    "expected_name": "mainnet",
                    "bind_result": "environment_uuid",
                },
                {
                    "phase": "C-mainnet-environment-resolution",
                    "controller_id": _C_CONTROLLER,
                    "method": "GET",
                    "endpoint": f"/api/v1/projects/{c_controller['project_uuid']}/environments",
                    "expected_name": "mainnet",
                    "bind_result": "environment_uuid",
                },
            ],
            "mutations": [
                {
                    "ordinal": 1,
                    "mutation_id": f"{a_name}.create-application",
                    "controller_id": _A_CONTROLLER,
                    "method": "POST",
                    "endpoint": "/api/v1/services",
                    "canonical_request_body": a_body,
                    "body_materialization": "add-exact-read-only-environment-uuid",
                    "success_statuses": [200, 201, 202],
                    "bind_result": "application_uuid",
                },
                {
                    "ordinal": 2,
                    "mutation_id": f"{a_name}.bind-captain-secret",
                    "controller_id": _A_CONTROLLER,
                    "method": "POST",
                    "endpoint_template": f"/api/v1/services/${{result.{a_name}.create-application.application_uuid}}/envs",
                    "secret_source_field": captain["private_state_field"],
                    "environment_key": _CAPTAIN_SECRET_ENV,
                    "value_in_transaction": False,
                    "success_statuses": [200, 201, 202],
                },
                {
                    "ordinal": 3,
                    "mutation_id": f"{a_name}.deploy",
                    "controller_id": _A_CONTROLLER,
                    "method": "GET",
                    "endpoint_template": f"/api/v1/deploy?uuid=${{result.{a_name}.create-application.application_uuid}}&force=false",
                    "success_statuses": [200, 201, 202],
                },
                {
                    "ordinal": 4,
                    "mutation_id": f"{a_name}.delete",
                    "controller_id": _A_CONTROLLER,
                    "method": "DELETE",
                    "endpoint_template": f"/api/v1/services/${{result.{a_name}.create-application.application_uuid}}",
                    "success_statuses": [200, 204],
                    "cleanup_required": True,
                },
                {
                    "ordinal": 5,
                    "mutation_id": f"{c_name}.create-application",
                    "controller_id": _C_CONTROLLER,
                    "method": "POST",
                    "endpoint": "/api/v1/services",
                    "canonical_request_body": c_body,
                    "success_statuses": [200, 201, 202],
                    "bind_result": "application_uuid",
                },
                {
                    "ordinal": 6,
                    "mutation_id": f"{c_name}.bind-public-funding-result",
                    "controller_id": _C_CONTROLLER,
                    "method": "PATCH",
                    "endpoint_template": f"/api/v1/services/${{result.{c_name}.create-application.application_uuid}}/envs/bulk",
                    "value_source": "validated A funding transaction hash",
                    "secret_material": False,
                    "success_statuses": [200, 201, 202],
                },
                {
                    "ordinal": 7,
                    "mutation_id": f"{c_name}.deploy",
                    "controller_id": _C_CONTROLLER,
                    "method": "GET",
                    "endpoint_template": f"/api/v1/deploy?uuid=${{result.{c_name}.create-application.application_uuid}}&force=false",
                    "success_statuses": [200, 201, 202],
                },
                {
                    "ordinal": 8,
                    "mutation_id": f"{c_name}.delete",
                    "controller_id": _C_CONTROLLER,
                    "method": "DELETE",
                    "endpoint_template": f"/api/v1/services/${{result.{c_name}.create-application.application_uuid}}",
                    "success_statuses": [200, 204],
                    "cleanup_required": True,
                },
            ],
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
            "public_endpoint_authorized": False,
            "ssh_authorized": False,
        },
        "summary": {
            "clean": True,
            "funding_transaction_compiled": True,
            "funding_source_genesis_allocated": True,
            "transfer_value_wei": amount,
            "funding_value_cap_wei": amount,
            "source_maximum_total_debit_wei": amount + _FUNDING_TX_MAX_FEE_WEI,
            "application_mutation_count": 8,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "public_endpoint_count": 0,
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
        "destination_zero_balance_precondition_required": True,
        "source_balance_preflight_required": True,
        "cross_validator_receipt_and_balance_verification_required": True,
        "application_mutation_count": 8,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "public_endpoint_count": 0,
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


def _safe_no_write_retry_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
) -> dict[str, Any]:
    resolved = Path(evidence_path).resolve(strict=False)
    document, _, file_sha = _canonical_under(
        paths,
        resolved,
        _EVIDENCE_DIRECTORY,
        "validator-RPC canary funding recovery evidence",
    )
    digest = _digest_without(document, "validator_rpc_canary_funding_evidence_sha256")
    receipts = document.get("mutation_receipts")
    summary = document.get("summary")
    failure = document.get("failure")
    if not (
        document.get("kind") == _EVIDENCE_KIND
        and document.get("schema_version") == 1
        and document.get("status") == "manual-review-required"
        and document.get("validator_rpc_canary_funding_evidence_sha256") == digest
        and document.get("mother_binding") == _binding(private_state)
        and not _contains_sensitive(document)
        and type(receipts) is list
        and len(receipts) == 1
        and isinstance(summary, Mapping)
        and isinstance(failure, Mapping)
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RECOVERY_INVALID",
            "recovery evidence is not a canonical failed funding execution",
        )
    receipt = receipts[0]
    message = str(failure.get("message", ""))
    if not (
        isinstance(receipt, Mapping)
        and failure.get("code") == "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_MUTATION_FAILED"
        and re.fullmatch(
            r"mainnet-canary1-fund-a\.create-application failed with HTTP [0-9]{3}",
            message,
        )
        and receipt.get("mutation_id") == "mainnet-canary1-fund-a.create-application"
        and receipt.get("controller_id") == _A_CONTROLLER
        and receipt.get("method") == "POST"
        and receipt.get("endpoint") == "/api/v1/services"
        and receipt.get("status") == "failed"
        and receipt.get("live_write_acknowledged") is False
        and receipt.get("application_uuid") in {None, ""}
        and document.get("funding_transaction_hash") in {None, ""}
        and document.get("cross_validator_verification") is None
        and summary.get("funding_performed") is False
        and summary.get("application_mutation_count") == 1
        and summary.get("temporary_A_application_deleted") is False
        and summary.get("temporary_C_application_deleted") is False
        and summary.get("validator_mutation_count") == 0
        and summary.get("validator_restart_count") == 0
        and summary.get("validator_vote_performed") is False
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RECOVERY_INVALID",
            "recovery evidence is not the exact safe pre-create no-write failure",
        )
    return {
        "mode": "safe-pre-create-rejection-no-write",
        "locator": _relative(paths, resolved, "validator-RPC canary funding recovery evidence"),
        "file_sha256": file_sha,
        "sha256": digest,
        "failed_mutation_id": receipt["mutation_id"],
        "http_status": receipt.get("http_status"),
        "live_write_acknowledged": False,
        "cleanup_authorized": False,
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
        _safe_no_write_retry_evidence(paths, private_state, Path(recovery_evidence_path))
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
            "public_endpoint_authorized": False,
            "ssh_authorized": False,
        },
        "policy": {
            "exact_transfer_only": True,
            "destination_zero_balance_precondition_required": True,
            "source_balance_preflight_required": True,
            "cross_validator_receipt_and_balance_verification_required": True,
            "temporary_applications_must_be_deleted": True,
            "canary_execution_authorized": False,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "public_endpoint_count": 0,
        },
    }
    release["validator_rpc_canary_funding_release_sha256"] = _digest_without(
        release, "validator_rpc_canary_funding_release_sha256"
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
        and document.get("schema_version") == 1
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
        and document.get("schema_version") == 1
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
        recovery_valid = dict(recovery) == _safe_no_write_retry_evidence(
            paths, private_state, recovery_path
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
        and document.get("execution_plan") == transaction.get("future_execution_plan")
        and recovery_valid
        and authority.get("requested_use_limit") == 1
        and authority.get("funding_authorized") is True
        and authority.get("live_execution_authorized") is True
        and authority.get("funding_value_cap_wei") == 742_000_000_000_000
        and authority.get("validator_mutation_authorized") is False
        and authority.get("validator_restart_authorized") is False
        and authority.get("validator_vote_authorized") is False
        and authority.get("public_endpoint_authorized") is False
        and policy.get("canary_execution_authorized") is False
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
        "application_mutation_count": 8,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "public_endpoint_count": 0,
        "funding_authorized": True,
        "canary_execution_authorized": False,
        "live_execution_authorized": True,
        "validator_vote_authorized": False,
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
    values: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key).lower() in {"uuid", "application_uuid", "resource_uuid"} and type(nested) is str:
                    clean = nested.strip()
                    if re.fullmatch(r"[A-Za-z0-9_-]{8,96}", clean):
                        values.add(clean)
                elif isinstance(nested, (Mapping, list)):
                    walk(nested)
        elif type(value) is list:
            for nested in value:
                walk(nested)

    walk(payload)
    if len(values) != 1:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_APPLICATION_INVALID",
            "Coolify did not return exactly one service UUID",
        )
    return next(iter(values))


def _logs_text(payload: Any) -> str:
    if isinstance(payload, Mapping):
        logs = payload.get("logs")
        if type(logs) is str:
            return logs
        for key in ("data", "application", "resource"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                found = _logs_text(nested)
                if found:
                    return found
    if type(payload) is str:
        return payload
    return ""


def _marker_json(logs: str, marker: str) -> Mapping[str, Any] | None:
    candidates: list[Mapping[str, Any]] = []
    decoder = json.JSONDecoder()
    start = 0
    while True:
        index = logs.find(marker, start)
        if index < 0:
            break
        raw = logs[index + len(marker):].lstrip()
        try:
            value, consumed = decoder.raw_decode(raw)
        except json.JSONDecodeError:
            start = index + len(marker)
            continue
        if isinstance(value, Mapping):
            candidates.append(value)
        start = index + len(marker) + max(1, consumed)
    if not candidates:
        return None
    canonical = {canonical_json(dict(item)) for item in candidates}
    if len(canonical) != 1:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_LOG_INVALID",
            "application logs contain contradictory result markers",
        )
    return candidates[-1]


def _transaction_hash(value: Mapping[str, Any]) -> str:
    found: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if str(key) in {"transactionHash", "transaction_hash"} and type(nested) is str:
                    clean = nested.strip().lower()
                    if re.fullmatch(r"0x[0-9a-f]{64}", clean):
                        found.add(clean)
                elif isinstance(nested, (Mapping, list)):
                    walk(nested)
        elif type(item) is list:
            for nested in item:
                walk(nested)

    walk(value)
    if len(found) != 1:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RESULT_INVALID",
            "funding result did not contain exactly one transaction hash",
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


def _wait_for_marker(
    *,
    controller: Any,
    controller_id: str,
    application_uuid: str,
    service_name: str,
    marker: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    observations: list[dict[str, Any]],
    phase: str,
) -> Mapping[str, Any]:
    if max_wait_seconds < 0 or poll_interval_seconds < 0:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "wait and poll intervals must be non-negative",
        )
    quoted_uuid = urllib.parse.quote(application_uuid, safe="")
    quoted_name = urllib.parse.quote(service_name, safe="")
    endpoints = [
        (
            f"/api/v1/services/{quoted_uuid}/logs"
            f"?sub_service_name={quoted_name}&lines=500&show_timestamps=true"
        ),
        f"/api/v1/services/{quoted_uuid}/logs?lines=500",
        f"/api/v1/services/{quoted_uuid}/docker/logs?lines=500",
        f"/api/v1/services/{quoted_uuid}/applications/logs?lines=500",
    ]
    started = time.monotonic()
    while True:
        for endpoint in endpoints:
            response = _http(
                controller,
                "GET",
                endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            logs = _logs_text(response["payload"]) if response["ok"] else ""
            parsed = _marker_json(logs, marker) if logs else None
            observations.append({
                "phase": phase,
                "controller_id": controller_id,
                "method": "GET",
                "endpoint": endpoint,
                "http_status": response["status"],
                "response_sha256": response["response_sha256"],
                "marker_present": parsed is not None,
                "observed_at": _timestamp(),
            })
            if parsed is not None:
                return parsed
        elapsed = time.monotonic() - started
        if elapsed >= max_wait_seconds:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RESULT_TIMEOUT",
                f"{phase} did not produce its committed result marker",
            )
        time.sleep(min(poll_interval_seconds, max(0.0, max_wait_seconds - elapsed)))

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
            "locator": _relative(paths, resolved_release, "validator-RPC canary funding release"),
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
    a_app = _mapping(applications.get("a_funder"), "release.applications.a_funder")
    c_app = _mapping(applications.get("c_verifier"), "release.applications.c_verifier")
    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None
    a_uuid: str | None = None
    c_uuid: str | None = None
    a_deleted = False
    c_deleted = False
    tx_hash: str | None = None
    c_result: Mapping[str, Any] | None = None
    started_at = _timestamp()

    try:
        a_name = str(a_app["application_name"])
        a_environment_uuid = _resolve_environment_uuid(
            controller=controllers[_A_CONTROLLER],
            controller_id=_A_CONTROLLER,
            endpoint=str(a_app["environment_resolution_endpoint"]),
            expected_name=str(a_app["environment_name"]),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            observations=observations,
            phase="A-mainnet-environment-resolution",
        )
        a_create_body = dict(_mapping(a_app.get("create_request_body"), "a_funder.create_request_body"))
        a_create_body["environment_uuid"] = a_environment_uuid
        a_create = _request_mutation(
            controller=controllers[_A_CONTROLLER],
            mutation_id=f"{a_name}.create-application",
            controller_id=_A_CONTROLLER,
            method="POST",
            endpoint="/api/v1/services",
            body=a_create_body,
            success_statuses=(200, 201, 202),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            receipts=receipts,
        )
        a_uuid = _application_uuid(a_create["payload"])
        receipts[-1]["application_uuid"] = a_uuid

        secret_body = {
            "key": _CAPTAIN_SECRET_ENV,
            "value": captain["private_key"],
            "is_preview": False,
            "is_literal": True,
            "is_multiline": False,
            "is_shown_once": True,
        }
        _request_mutation(
            controller=controllers[_A_CONTROLLER],
            mutation_id=f"{a_name}.bind-captain-secret",
            controller_id=_A_CONTROLLER,
            method="POST",
            endpoint=f"/api/v1/services/{a_uuid}/envs",
            body=secret_body,
            success_statuses=(200, 201, 202),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            receipts=receipts,
            application_uuid=a_uuid,
        )
        _request_mutation(
            controller=controllers[_A_CONTROLLER],
            mutation_id=f"{a_name}.deploy",
            controller_id=_A_CONTROLLER,
            method="GET",
            endpoint=f"/api/v1/deploy?uuid={a_uuid}&force=false",
            body=None,
            success_statuses=(200, 201, 202),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            receipts=receipts,
            application_uuid=a_uuid,
        )
        a_result = _wait_for_marker(
            controller=controllers[_A_CONTROLLER],
            controller_id=_A_CONTROLLER,
            application_uuid=a_uuid,
            service_name=a_name,
            marker=str(a_app["result_marker"]),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            opener=opener,
            observations=observations,
            phase="A-capped-funding-result",
        )
        tx_hash = _transaction_hash(a_result)

        _request_mutation(
            controller=controllers[_A_CONTROLLER],
            mutation_id=f"{a_name}.delete",
            controller_id=_A_CONTROLLER,
            method="DELETE",
            endpoint=f"/api/v1/services/{a_uuid}",
            body=None,
            success_statuses=(200, 204),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            receipts=receipts,
            application_uuid=a_uuid,
        )
        a_deleted = True

        c_name = str(c_app["application_name"])
        c_environment_uuid = _resolve_environment_uuid(
            controller=controllers[_C_CONTROLLER],
            controller_id=_C_CONTROLLER,
            endpoint=str(c_app["environment_resolution_endpoint"]),
            expected_name=str(c_app["environment_name"]),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            observations=observations,
            phase="C-mainnet-environment-resolution",
        )
        c_create_body = dict(_mapping(c_app.get("create_request_body"), "c_verifier.create_request_body"))
        c_create_body["environment_uuid"] = c_environment_uuid
        c_create = _request_mutation(
            controller=controllers[_C_CONTROLLER],
            mutation_id=f"{c_name}.create-application",
            controller_id=_C_CONTROLLER,
            method="POST",
            endpoint="/api/v1/services",
            body=c_create_body,
            success_statuses=(200, 201, 202),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            receipts=receipts,
        )
        c_uuid = _application_uuid(c_create["payload"])
        receipts[-1]["application_uuid"] = c_uuid
        public_env_body = {
            "data": [{
                "key": "MC_MOTHER_CANARY_FUNDING_TX_HASH",
                "value": tx_hash,
                "is_preview": False,
                "is_literal": True,
                "is_multiline": False,
                "is_shown_once": False,
            }]
        }
        _request_mutation(
            controller=controllers[_C_CONTROLLER],
            mutation_id=f"{c_name}.bind-public-funding-result",
            controller_id=_C_CONTROLLER,
            method="PATCH",
            endpoint=f"/api/v1/services/{c_uuid}/envs/bulk",
            body=public_env_body,
            success_statuses=(200, 201, 202),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            receipts=receipts,
            application_uuid=c_uuid,
        )
        _request_mutation(
            controller=controllers[_C_CONTROLLER],
            mutation_id=f"{c_name}.deploy",
            controller_id=_C_CONTROLLER,
            method="GET",
            endpoint=f"/api/v1/deploy?uuid={c_uuid}&force=false",
            body=None,
            success_statuses=(200, 201, 202),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            receipts=receipts,
            application_uuid=c_uuid,
        )
        c_result = _wait_for_marker(
            controller=controllers[_C_CONTROLLER],
            controller_id=_C_CONTROLLER,
            application_uuid=c_uuid,
            service_name=c_name,
            marker=str(c_app["result_marker"]),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            opener=opener,
            observations=observations,
            phase="C-funding-receipt-and-balance-verification",
        )
        expected_amount = int(release["funding_policy"]["transfer_value_wei"])
        if not (
            str(c_result.get("transaction_hash", "")).lower() == tx_hash
            and str(c_result.get("destination", "")).lower() == release["destination"]["address"]
            and str(c_result.get("balance_wei", "")) == str(expected_amount)
        ):
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RESULT_INVALID",
                "C verifier result does not match the exact released transfer",
            )
        _request_mutation(
            controller=controllers[_C_CONTROLLER],
            mutation_id=f"{c_name}.delete",
            controller_id=_C_CONTROLLER,
            method="DELETE",
            endpoint=f"/api/v1/services/{c_uuid}",
            body=None,
            success_statuses=(200, 204),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            receipts=receipts,
            application_uuid=c_uuid,
        )
        c_deleted = True
    except Exception as exc:
        failure = {
            "code": str(getattr(exc, "code", "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EXECUTION_FAILED")),
            "message": str(exc).replace("\r", " ").replace("\n", " ").strip()[:300],
        }
    finally:
        cleanup_targets = [
            (_C_CONTROLLER, c_uuid, c_deleted, "emergency-delete-c-verifier"),
            (_A_CONTROLLER, a_uuid, a_deleted, "emergency-delete-a-funder"),
        ]
        for controller_id, app_uuid, already_deleted, mutation_id in cleanup_targets:
            if app_uuid is None or already_deleted:
                continue
            try:
                response = _http(
                    controllers[controller_id],
                    "DELETE",
                    f"/api/v1/services/{app_uuid}",
                    body=None,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                receipts.append(_receipt(
                    mutation_id=mutation_id,
                    controller_id=controller_id,
                    method="DELETE",
                    endpoint=f"/api/v1/services/{app_uuid}",
                    response=response,
                    succeeded=response["status"] in {200, 204, 404},
                    application_uuid=app_uuid,
                ))
                if response["status"] not in {200, 204, 404} and failure is None:
                    failure = {
                        "code": "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_CLEANUP_FAILED",
                        "message": f"temporary application cleanup failed on {controller_id}",
                    }
            except Exception as cleanup_exc:
                receipts.append({
                    "mutation_id": mutation_id,
                    "controller_id": controller_id,
                    "method": "DELETE",
                    "endpoint": f"/api/v1/services/{app_uuid}",
                    "status": "failed",
                    "live_write_acknowledged": False,
                })
                if failure is None:
                    failure = {
                        "code": str(getattr(cleanup_exc, "code", "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_CLEANUP_FAILED")),
                        "message": str(cleanup_exc).replace("\r", " ").replace("\n", " ").strip()[:300],
                    }

    success = bool(
        failure is None
        and tx_hash is not None
        and c_result is not None
        and a_deleted
        and c_deleted
        and len(receipts) == 8
        and all(item.get("status") == "succeeded" for item in receipts)
    )
    completed_at = _timestamp()
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "pass" if success else "manual-review-required",
        "network": "mainnet",
        "mother_binding": _binding(private_state),
        "release": {
            "locator": _relative(paths, resolved_release, "validator-RPC canary funding release"),
            "sha256": release_sha,
        },
        "execution_claim": {
            "locator": _relative(paths, claim_path, "validator-RPC canary funding execution claim"),
        },
        "chain": dict(release["chain"]),
        "funding_source_address": release["funding_source"]["address"],
        "canary_address": release["destination"]["address"],
        "transfer_value_wei": release["funding_policy"]["transfer_value_wei"],
        "funding_transaction_hash": tx_hash,
        "cross_validator_verification": dict(c_result) if c_result is not None else None,
        "mutation_receipts": receipts,
        "log_observations": observations,
        "failure": failure,
        "summary": {
            "clean": success,
            "complete": success,
            "funding_performed": tx_hash is not None,
            "funding_receipt_verified_on_C": c_result is not None,
            "canary_balance_verified_on_C": c_result is not None,
            "exact_transfer_value_verified": success,
            "temporary_A_application_deleted": a_deleted,
            "temporary_C_application_deleted": c_deleted,
            "application_mutation_count": len(receipts),
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "public_endpoint_count": 0,
            "validator_vote_performed": False,
            "canary_execution_performed": False,
            "next_phase": (
                "validator-rpc-canary-execution-release-not-yet-authorized"
                if success else "manual-review-required"
            ),
        },
    }
    evidence_path, evidence_sha = _write_funding_evidence(paths, evidence, operation=operation)
    return {
        "status": evidence["status"],
        "network": "mainnet",
        "chain_id": release["chain"]["chain_id"],
        "canary_address": release["destination"]["address"],
        "transfer_value_wei": release["funding_policy"]["transfer_value_wei"],
        "funding_transaction_hash": tx_hash,
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
        and document.get("schema_version") == 1
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
    release_sha = _digest_without(release, "validator_rpc_canary_funding_release_sha256")
    if not (
        release_ref.get("sha256") == release_sha
        and release.get("validator_rpc_canary_funding_release_sha256") == release_sha
        and release.get("mother_binding") == _binding(private_state)
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
    release_applications = _mapping(release.get("applications"), "release.applications")
    a_name = str(_mapping(release_applications.get("a_funder"), "release.applications.a_funder").get("application_name"))
    c_name = str(_mapping(release_applications.get("c_verifier"), "release.applications.c_verifier").get("application_name"))
    expected_names = [
        f"{a_name}.create-application",
        f"{a_name}.bind-captain-secret",
        f"{a_name}.deploy",
        f"{a_name}.delete",
        f"{c_name}.create-application",
        f"{c_name}.bind-public-funding-result",
        f"{c_name}.deploy",
        f"{c_name}.delete",
    ]
    c_result = document.get("cross_validator_verification")
    summary = _mapping(document.get("summary"), "evidence.summary")
    if not (
        type(receipts) is list
        and [item.get("mutation_id") for item in receipts] == expected_names
        and [item.get("method") for item in receipts]
        == ["POST", "POST", "GET", "DELETE", "POST", "PATCH", "GET", "DELETE"]
        and all(item.get("status") == "succeeded" for item in receipts)
        and all(item.get("live_write_acknowledged") is True for item in receipts)
        and type(document.get("funding_transaction_hash")) is str
        and re.fullmatch(r"0x[0-9a-f]{64}", document["funding_transaction_hash"]) is not None
        and isinstance(c_result, Mapping)
        and str(c_result.get("transaction_hash", "")).lower() == document["funding_transaction_hash"]
        and str(c_result.get("destination", "")).lower() == document["canary_address"]
        and str(c_result.get("balance_wei", "")) == str(document["transfer_value_wei"])
        and document["transfer_value_wei"] == release["funding_policy"]["transfer_value_wei"]
        and document["canary_address"] == release["destination"]["address"]
        and summary.get("clean") is True
        and summary.get("complete") is True
        and summary.get("funding_performed") is True
        and summary.get("funding_receipt_verified_on_C") is True
        and summary.get("canary_balance_verified_on_C") is True
        and summary.get("exact_transfer_value_verified") is True
        and summary.get("temporary_A_application_deleted") is True
        and summary.get("temporary_C_application_deleted") is True
        and summary.get("validator_mutation_count") == 0
        and summary.get("validator_restart_count") == 0
        and summary.get("public_endpoint_count") == 0
        and summary.get("validator_vote_performed") is False
        and summary.get("canary_execution_performed") is False
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
            "funding evidence receipts or proof facts are contradictory",
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
        "funding_transaction_hash": document["funding_transaction_hash"],
        "funding_source_address": document["funding_source_address"],
        "canary_address": document["canary_address"],
        "transfer_value_wei": document["transfer_value_wei"],
        "funding_receipt_verified_on_C": True,
        "canary_balance_verified_on_C": True,
        "temporary_applications_deleted": True,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "public_endpoint_count": 0,
        "validator_vote_performed": False,
        "canary_execution_performed": False,
        "next_phase": "validator-rpc-canary-execution-release-not-yet-authorized",
    }
