"""Offline compiler for an ephemeral validator-RPC operations canary.

This module does not contact Coolify and authorizes no live mutation.  It
reserves a dedicated non-validator canary wallet under protected Mother state,
consumes canonical successful A/C soak evidence, and compiles two ephemeral
Docker Compose application templates:

* an A-side runner that uses mainneta-super1's internal JSON-RPC to require a
  bounded EIP-1559 base fee and sufficient wallet balance before submitting a
  zero-value signed transaction, deploying a deterministic tiny storage
  contract, writing the value 42, and emitting one compact result line; and
* a C-side read-only verifier that consumes only the A result's public hashes
  and address, then verifies the receipts, bytecode, and stored value through
  mainnetc-super1's internal JSON-RPC.

Neither template exposes a port, URL, FQDN, or Traefik route.  The transaction
contains no private key material and leaves release/execution authority false.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import yaml

from . import atomic_files
from .canonical import canonical_json
from .deployment_private_rpc import _controller_config, _load_soak
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
from .deployment_mainnet_soak import _EVIDENCE_DIRECTORY as _SOAK_EVIDENCE_DIRECTORY
from .ethereum_identity import generate_private_key, is_private_key, private_key_to_address
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_IDENTITY_KIND = "main_computer.mother.deployment_validator_rpc_canary_identity.v1"
_TRANSACTION_KIND = "main_computer.mother.deployment_validator_rpc_canary_transaction.v1"
_IDENTITY_SCHEMA_VERSION = 1
_TRANSACTION_SCHEMA_VERSION = 2
_IDENTITY_DIRECTORY = ("secrets", "deployment-validator-rpc-canary-identities")
_TRANSACTION_DIRECTORY = ("actions", "deployment-validator-rpc-canary-transactions")
_SECRET_ENV = "MC_MOTHER_VALIDATOR_RPC_CANARY_PRIVATE_KEY"
_A = "mainneta-super1"
_C = "mainnetc-super1"
_A_CONTROLLER = "coolify-a"
_C_CONTROLLER = "coolify-c"
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_DEFAULT_IMAGE = "ghcr.io/foundry-rs/foundry:latest"

# 12-byte init code followed by a 24-byte runtime.  With empty calldata the
# runtime returns storage slot 0.  With non-empty calldata it stores the first
# 32-byte word in slot 0 and stops.
_CONTRACT_RUNTIME = "3615600c57600035600055005b60005460005260206000f3"
_CONTRACT_INIT = "6018600c60003960186000f3" + _CONTRACT_RUNTIME
_EXPECTED_VALUE = "0" * 62 + "2a"

# Fixed fail-closed EIP-1559 policy.  The A-side runner must read the latest
# block before signing, require baseFeePerGas, and refuse to proceed if the
# chain's base fee exceeds this ceiling.  Explicit gas limits make the maximum
# wallet exposure deterministic.
_BASE_FEE_CEILING_WEI = 2_000_000_000
_MAX_FEE_PER_GAS_WEI = 2_000_000_000
_MAX_PRIORITY_FEE_PER_GAS_WEI = 0
_SELF_TRANSFER_GAS_LIMIT = 21_000
_CONTRACT_DEPLOY_GAS_LIMIT = 250_000
_CONTRACT_WRITE_GAS_LIMIT = 100_000
_TOTAL_GAS_LIMIT = (
    _SELF_TRANSFER_GAS_LIMIT
    + _CONTRACT_DEPLOY_GAS_LIMIT
    + _CONTRACT_WRITE_GAS_LIMIT
)
_MAXIMUM_FUNDING_REQUIREMENT_WEI = _TOTAL_GAS_LIMIT * _MAX_FEE_PER_GAS_WEI


class MotherDeploymentValidatorRpcCanaryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error(code: str, message: str) -> MotherDeploymentValidatorRpcCanaryError:
    return MotherDeploymentValidatorRpcCanaryError(code, message)


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(
        canonical_json({key: value for key, value in document.items() if key != field})
    ).hexdigest()


def _name(value: Any) -> str:
    if type(value) is not str or _NAME_RE.fullmatch(value) is None:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_INVALID",
            "canary_name must be a lowercase DNS-safe label",
        )
    if not value.startswith("mainnet-canary"):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_INVALID",
            "canary_name must begin with mainnet-canary",
        )
    return value


def _address(value: Any, path: str = "address") -> str:
    if type(value) is not str or _ADDRESS_RE.fullmatch(value) is None:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_INVALID",
            f"{path} must be a 20-byte Ethereum address",
        )
    return value.lower()


def _identity_root(paths: PrivateStatePaths) -> Path:
    return paths.root / _IDENTITY_DIRECTORY[0] / _IDENTITY_DIRECTORY[1]


def _identity_destination(paths: PrivateStatePaths, canary_name: str) -> Path:
    return _identity_root(paths) / f"{canary_name}.json"


def _identity_relative(paths: PrivateStatePaths, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_PATH_UNSAFE",
            "canary identity is outside Mother state",
        ) from exc


def _ensure_identity_root(
    paths: PrivateStatePaths,
    *,
    operation: OperationIdentity,
) -> Path:
    current = paths.root
    for part in _IDENTITY_DIRECTORY:
        current /= part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _read_identity(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    identity_path: Path,
    *,
    network: str,
    canary_name: str | None,
    operation: OperationIdentity,
) -> tuple[dict[str, Any], Path, str]:
    if network != "mainnet":
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_NETWORK_REJECTED",
            "validator-RPC canary identity currently accepts mainnet only",
        )
    candidate = Path(identity_path).resolve(strict=False)
    expected_root = _identity_root(paths).resolve(strict=False)
    try:
        candidate.relative_to(expected_root)
    except ValueError as exc:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_PATH_UNSAFE",
            "canary identity is outside its canonical protected directory",
        ) from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_IDENTITY_NOT_FOUND",
            "canary identity artifact is missing or unsafe",
        )
    _secure_private_path(candidate, is_directory=False, operation=operation)
    payload = candidate.read_bytes()
    try:
        value = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_IDENTITY_INVALID",
            "canary identity is not canonical JSON",
        ) from exc
    if type(value) is not dict or canonical_json(value) != payload:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_IDENTITY_INVALID",
            "canary identity is not canonical",
        )
    private_key = value.get("private_key")
    if not is_private_key(private_key):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_IDENTITY_INVALID",
            "canary identity private key is invalid",
        )
    derived = private_key_to_address(private_key).lower()
    actual_name = _name(value.get("canary_name"))
    digest = _digest_without(value, "validator_rpc_canary_identity_sha256")
    if not (
        value.get("kind") == _IDENTITY_KIND
        and value.get("schema_version") == _IDENTITY_SCHEMA_VERSION
        and value.get("network") == network
        and value.get("mother_binding") == _binding(private_state)
        and value.get("secret_environment_variable") == _SECRET_ENV
        and value.get("validator_identity") is False
        and value.get("private_rpc_node_identity") is False
        and value.get("address") == derived
        and value.get("validator_rpc_canary_identity_sha256") == digest
        and (canary_name is None or actual_name == _name(canary_name))
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_IDENTITY_INVALID",
            "canary identity is modified, stale, or contradictory",
        )
    return value, candidate, hashlib.sha256(payload).hexdigest()


def inspect_validator_rpc_canary_identity_reservation(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    *,
    canary_name: str = "mainnet-canary1",
    network: str = "mainnet",
    operation: OperationIdentity,
) -> dict[str, Any]:
    name = _name(canary_name)
    destination = _identity_destination(paths, name)
    if destination.exists():
        result = verify_validator_rpc_canary_identity(
            paths,
            private_state,
            destination,
            network=network,
            canary_name=name,
            operation=operation,
        )
        return {
            "status": "pass",
            "identity_exists": True,
            "write_performed": False,
            **result,
        }
    if network != "mainnet":
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_NETWORK_REJECTED",
            "validator-RPC canary identity currently accepts mainnet only",
        )
    return {
        "status": "pass",
        "network": network,
        "canary_name": name,
        "identity_exists": False,
        "would_generate_private_key": True,
        "write_performed": False,
        "private_key_printed": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_mutation_performed": False,
        "next_phase": "reserve-validator-rpc-canary-identity-with-write",
    }


def reserve_validator_rpc_canary_identity(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    *,
    canary_name: str = "mainnet-canary1",
    network: str = "mainnet",
    created_at: str | None = None,
    operation: OperationIdentity,
    key_factory: Callable[[], str] = generate_private_key,
) -> dict[str, Any]:
    name = _name(canary_name)
    if network != "mainnet":
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_NETWORK_REJECTED",
            "validator-RPC canary identity currently accepts mainnet only",
        )
    destination = _identity_destination(paths, name)
    if destination.exists():
        verified = verify_validator_rpc_canary_identity(
            paths,
            private_state,
            destination,
            network=network,
            canary_name=name,
            operation=operation,
        )
        return {
            "status": "pass",
            "identity_exists": True,
            "identity_created": False,
            "write_performed": False,
            **verified,
        }
    private_key = key_factory()
    if not is_private_key(private_key):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_IDENTITY_INVALID",
            "key factory returned an invalid secp256k1 private key",
        )
    address = private_key_to_address(private_key).lower()
    document: dict[str, Any] = {
        "kind": _IDENTITY_KIND,
        "schema_version": _IDENTITY_SCHEMA_VERSION,
        "created_at": _timestamp(created_at),
        "network": network,
        "canary_name": name,
        "mother_binding": _binding(private_state),
        "private_key": private_key,
        "address": address,
        "secret_environment_variable": _SECRET_ENV,
        "validator_identity": False,
        "private_rpc_node_identity": False,
    }
    document["validator_rpc_canary_identity_sha256"] = _digest_without(
        document,
        "validator_rpc_canary_identity_sha256",
    )
    payload = canonical_json(document)
    root = _ensure_identity_root(paths, operation=operation)
    destination = root / f"{name}.json"
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    verified = verify_validator_rpc_canary_identity(
        paths,
        private_state,
        destination,
        network=network,
        canary_name=name,
        operation=operation,
    )
    return {
        "status": "pass",
        "identity_exists": True,
        "identity_created": True,
        "write_performed": True,
        **verified,
    }


def verify_validator_rpc_canary_identity(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    identity_path: Path,
    *,
    network: str = "mainnet",
    canary_name: str | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    value, candidate, file_sha = _read_identity(
        paths,
        private_state,
        identity_path,
        network=network,
        canary_name=canary_name,
        operation=operation,
    )
    locator = _identity_relative(paths, candidate)
    return {
        "clean": True,
        "kind": _IDENTITY_KIND,
        "network": value["network"],
        "canary_name": value["canary_name"],
        "identity_path": str(candidate),
        "identity_locator": locator,
        "identity_artifact": {
            "path": str(candidate),
            "locator": locator,
            "sha256": file_sha,
        },
        "identity_file_sha256": file_sha,
        "identity_sha256": value["validator_rpc_canary_identity_sha256"],
        "address": value["address"],
        "secret_environment_variable": _SECRET_ENV,
        "validator_identity": False,
        "private_rpc_node_identity": False,
        "private_key_present": True,
        "private_key_printed": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_mutation_performed": False,
        "next_phase": "stage-validator-rpc-canary-transaction",
    }


def _validate_image(value: Any) -> str:
    if type(value) is not str or not value or any(ch.isspace() for ch in value):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_INVALID",
            "foundry_image must be one non-empty image reference",
        )
    if "@sha256:" not in value and ":" not in value:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_INVALID",
            "foundry_image must contain a tag or digest",
        )
    return value


def _shell_a(address: str) -> str:
    return f"""set -eu
set +x
RPC=http://{_A}:8545
EXPECTED_CHAIN_ID=42424240
EXPECTED_ADDRESS={address}
BYTECODE=0x{_CONTRACT_INIT}
RUNTIME=0x{_CONTRACT_RUNTIME}
VALUE=0x{_EXPECTED_VALUE}
BASE_FEE_CEILING_WEI={_BASE_FEE_CEILING_WEI}
MAX_FEE_PER_GAS_WEI={_MAX_FEE_PER_GAS_WEI}
MAX_PRIORITY_FEE_PER_GAS_WEI={_MAX_PRIORITY_FEE_PER_GAS_WEI}
SELF_GAS_LIMIT={_SELF_TRANSFER_GAS_LIMIT}
DEPLOY_GAS_LIMIT={_CONTRACT_DEPLOY_GAS_LIMIT}
WRITE_GAS_LIMIT={_CONTRACT_WRITE_GAS_LIMIT}
MAXIMUM_FUNDING_REQUIREMENT_WEI={_MAXIMUM_FUNDING_REQUIREMENT_WEI}
KEY="${{{_SECRET_ENV}:?missing {_SECRET_ENV}}}"
ADDR="$(cast wallet address "$KEY" | tr '[:upper:]' '[:lower:]')"
test "$ADDR" = "$EXPECTED_ADDRESS"
CHAIN_ID="$(cast chain-id --rpc-url "$RPC")"
test "$CHAIN_ID" = "$EXPECTED_CHAIN_ID"

# Mandatory EIP-1559 and funding preflight.  The raw latest-block read proves
# the execution is bound to an actual block response; cast base-fee then
# normalizes baseFeePerGas to decimal for fail-closed arithmetic.
LATEST_BLOCK="$(cast rpc eth_getBlockByNumber latest false --rpc-url "$RPC")"
case "$LATEST_BLOCK" in
  *baseFeePerGas*) ;;
  *) echo "latest block has no baseFeePerGas" >&2; exit 31 ;;
esac
BASE_FEE_WEI="$(cast base-fee latest --rpc-url "$RPC")"
case "$BASE_FEE_WEI" in
  ''|*[!0-9]*) echo "latest base fee is not a decimal integer" >&2; exit 32 ;;
esac
test "$BASE_FEE_WEI" -le "$BASE_FEE_CEILING_WEI"
test "$MAX_FEE_PER_GAS_WEI" -ge "$BASE_FEE_WEI"
BALANCE_BEFORE_WEI="$(cast balance "$ADDR" --rpc-url "$RPC")"
case "$BALANCE_BEFORE_WEI" in
  ''|*[!0-9]*) echo "canary balance is not a decimal integer" >&2; exit 33 ;;
esac
test "$BALANCE_BEFORE_WEI" -ge "$MAXIMUM_FUNDING_REQUIREMENT_WEI"

BLOCK_BEFORE="$(cast block-number --rpc-url "$RPC")"
SELF_TX="$(cast send "$ADDR" --value 0 --gas-limit "$SELF_GAS_LIMIT" --gas-price "$MAX_FEE_PER_GAS_WEI" --priority-gas-price "$MAX_PRIORITY_FEE_PER_GAS_WEI" --private-key "$KEY" --rpc-url "$RPC" --async)"
SELF_STATUS="$(cast receipt "$SELF_TX" status --rpc-url "$RPC")"
test "$SELF_STATUS" = "1" || test "$SELF_STATUS" = "0x1"
DEPLOY_TX="$(cast send --create "$BYTECODE" --gas-limit "$DEPLOY_GAS_LIMIT" --gas-price "$MAX_FEE_PER_GAS_WEI" --priority-gas-price "$MAX_PRIORITY_FEE_PER_GAS_WEI" --private-key "$KEY" --rpc-url "$RPC" --async)"
DEPLOY_STATUS="$(cast receipt "$DEPLOY_TX" status --rpc-url "$RPC")"
test "$DEPLOY_STATUS" = "1" || test "$DEPLOY_STATUS" = "0x1"
CONTRACT="$(cast receipt "$DEPLOY_TX" contractAddress --rpc-url "$RPC" | tr '[:upper:]' '[:lower:]')"
case "$CONTRACT" in 0x????????????????????????????????????????) ;; *) exit 21 ;; esac
CODE="$(cast code "$CONTRACT" --rpc-url "$RPC" | tr '[:upper:]' '[:lower:]')"
test "$CODE" = "$RUNTIME"
READ0="$(cast call "$CONTRACT" --data 0x --rpc-url "$RPC" | tr '[:upper:]' '[:lower:]')"
test "$READ0" = "0x{'0'*64}"
WRITE_TX="$(cast send "$CONTRACT" --data "$VALUE" --gas-limit "$WRITE_GAS_LIMIT" --gas-price "$MAX_FEE_PER_GAS_WEI" --priority-gas-price "$MAX_PRIORITY_FEE_PER_GAS_WEI" --private-key "$KEY" --rpc-url "$RPC" --async)"
WRITE_STATUS="$(cast receipt "$WRITE_TX" status --rpc-url "$RPC")"
test "$WRITE_STATUS" = "1" || test "$WRITE_STATUS" = "0x1"
READ1="$(cast call "$CONTRACT" --data 0x --rpc-url "$RPC" | tr '[:upper:]' '[:lower:]')"
test "$READ1" = "$VALUE"
BLOCK_AFTER="$(cast block-number --rpc-url "$RPC")"
ATTEMPTS=0
while [ "$BLOCK_AFTER" -le "$BLOCK_BEFORE" ] && [ "$ATTEMPTS" -lt 15 ]; do
  sleep 2
  BLOCK_AFTER="$(cast block-number --rpc-url "$RPC")"
  ATTEMPTS=$((ATTEMPTS+1))
done
test "$BLOCK_AFTER" -gt "$BLOCK_BEFORE"
printf 'MOTHER_VALIDATOR_RPC_CANARY_A_RESULT={{"schema_version":2,"chain_id":%s,"canary_address":"%s","self_tx_hash":"%s","deploy_tx_hash":"%s","contract_address":"%s","write_tx_hash":"%s","stored_value":"%s","block_before":%s,"block_after":%s,"base_fee_wei":%s,"max_fee_per_gas_wei":%s,"max_priority_fee_per_gas_wei":%s,"balance_before_wei":%s,"maximum_funding_requirement_wei":%s}}\\n' "$CHAIN_ID" "$ADDR" "$SELF_TX" "$DEPLOY_TX" "$CONTRACT" "$WRITE_TX" "$READ1" "$BLOCK_BEFORE" "$BLOCK_AFTER" "$BASE_FEE_WEI" "$MAX_FEE_PER_GAS_WEI" "$MAX_PRIORITY_FEE_PER_GAS_WEI" "$BALANCE_BEFORE_WEI" "$MAXIMUM_FUNDING_REQUIREMENT_WEI"
"""

def _shell_c() -> str:
    return f"""set -eu
set +x
RPC=http://{_C}:8545
EXPECTED_CHAIN_ID=42424240
RUNTIME=0x{_CONTRACT_RUNTIME}
VALUE=0x{_EXPECTED_VALUE}
SELF_TX="${{MC_MOTHER_CANARY_SELF_TX_HASH:?missing self tx hash}}"
DEPLOY_TX="${{MC_MOTHER_CANARY_DEPLOY_TX_HASH:?missing deploy tx hash}}"
WRITE_TX="${{MC_MOTHER_CANARY_WRITE_TX_HASH:?missing write tx hash}}"
CONTRACT="${{MC_MOTHER_CANARY_CONTRACT_ADDRESS:?missing contract address}}"
SOURCE_BLOCK="${{MC_MOTHER_CANARY_SOURCE_BLOCK_AFTER:?missing source block}}"
CHAIN_ID="$(cast chain-id --rpc-url "$RPC")"
test "$CHAIN_ID" = "$EXPECTED_CHAIN_ID"
for TX in "$SELF_TX" "$DEPLOY_TX" "$WRITE_TX"; do
  STATUS="$(cast receipt "$TX" status --rpc-url "$RPC")"
  test "$STATUS" = "1" || test "$STATUS" = "0x1"
done
CODE="$(cast code "$CONTRACT" --rpc-url "$RPC" | tr '[:upper:]' '[:lower:]')"
test "$CODE" = "$RUNTIME"
READ1="$(cast call "$CONTRACT" --data 0x --rpc-url "$RPC" | tr '[:upper:]' '[:lower:]')"
test "$READ1" = "$VALUE"
BLOCK="$(cast block-number --rpc-url "$RPC")"
test "$BLOCK" -ge "$SOURCE_BLOCK"
printf 'MOTHER_VALIDATOR_RPC_CANARY_C_RESULT={{"schema_version":2,"chain_id":%s,"self_tx_hash":"%s","deploy_tx_hash":"%s","contract_address":"%s","write_tx_hash":"%s","stored_value":"%s","observed_block":%s}}\\n' "$CHAIN_ID" "$SELF_TX" "$DEPLOY_TX" "$CONTRACT" "$WRITE_TX" "$READ1" "$BLOCK"
"""


def _compose(application_name: str, image: str, command: str, *, a_side: bool) -> str:
    environment: dict[str, str] = {}
    if a_side:
        environment[_SECRET_ENV] = "${" + _SECRET_ENV + "}"
    else:
        for name in (
            "MC_MOTHER_CANARY_SELF_TX_HASH",
            "MC_MOTHER_CANARY_DEPLOY_TX_HASH",
            "MC_MOTHER_CANARY_WRITE_TX_HASH",
            "MC_MOTHER_CANARY_CONTRACT_ADDRESS",
            "MC_MOTHER_CANARY_SOURCE_BLOCK_AFTER",
        ):
            environment[name] = "${" + name + "}"
    document = {
        "name": application_name,
        "services": {
            application_name: {
                "image": image,
                "restart": "no",
                "read_only": True,
                "environment": environment,
                "command": ["sh", "-ec", command],
                "labels": {
                    "main_computer.mother.stage": "validator-rpc-operations-canary",
                    "main_computer.mother.role": "a-runner" if a_side else "c-verifier",
                },
            }
        },
    }
    return yaml.safe_dump(document, sort_keys=False, default_flow_style=False, width=4096)


def _compose_record(text: str) -> dict[str, Any]:
    parsed = yaml.safe_load(text)
    semantic = hashlib.sha256(canonical_json(parsed)).hexdigest()
    return {
        "canonical_text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "semantic_sha256": semantic,
        "public_endpoint": None,
        "host_ports_published": False,
    }


def _application_body(
    controller: Mapping[str, Any],
    *,
    environment_name: str,
    application_name: str,
    compose: str,
) -> dict[str, Any]:
    return {
        "project_uuid": controller["project_uuid"],
        "server_uuid": controller["server_uuid"],
        "environment_name": environment_name,
        "name": application_name,
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "instant_deploy": False,
        "connect_to_docker_network": True,
    }


def _targets_from_release(release: Mapping[str, Any]) -> dict[str, Any]:
    targets = _mapping(release.get("targets"), "continuation release targets")
    result: dict[str, Any] = {}
    for node, controller in ((_A, _A_CONTROLLER), (_C, _C_CONTROLLER)):
        target = _mapping(targets.get(node), f"{node} target")
        steady = _mapping(target.get("steady_state_compose"), f"{node} steady Compose")
        service_uuid = target.get("service_uuid")
        if not (
            target.get("controller_id") == controller
            and type(service_uuid) is str
            and bool(service_uuid)
            and type(steady.get("semantic_sha256")) is str
            and type(steady.get("sha256")) is str
        ):
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_LINEAGE_INVALID",
                f"{node} target lineage is incomplete or contradictory",
            )
        result[node] = {
            "controller_id": controller,
            "service_uuid": service_uuid,
            "compose_sha256": steady["sha256"],
            "compose_semantic_sha256": steady["semantic_sha256"],
            "rpc_url": f"http://{node}:8545",
        }
    return result


def build_validator_rpc_canary_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    soak_evidence_path: Path,
    identity_path: Path,
    *,
    canary_name: str = "mainnet-canary1",
    environment_name: str = "mainnet",
    foundry_image: str = _DEFAULT_IMAGE,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    soak_max_age_seconds: int = 86400,
    created_at: str | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    if network != "mainnet":
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_NETWORK_REJECTED",
            "validator-RPC canary currently accepts mainnet only",
        )
    name = _name(canary_name)
    image = _validate_image(foundry_image)
    identity, identity_candidate, identity_file_sha = _read_identity(
        paths,
        private_state,
        Path(identity_path),
        network=network,
        canary_name=name,
        operation=operation,
    )
    soak, release, soak_path, soak_file_sha = _load_soak(
        paths,
        private_state,
        Path(soak_evidence_path),
        network=network,
        selected_nodes=selected_nodes,
        max_age_seconds=soak_max_age_seconds,
    )
    validator_set = sorted(_address(item, "validator address") for item in soak["validator_set"])
    canary_address = _address(identity["address"], "canary address")
    if canary_address in validator_set:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_IDENTITY_REJECTED",
            "canary wallet must not reuse a validator identity",
        )
    a_controller = _controller_config(
        private_state,
        network=network,
        controller_id=_A_CONTROLLER,
    )
    c_controller = _controller_config(
        private_state,
        network=network,
        controller_id=_C_CONTROLLER,
    )
    targets = _targets_from_release(release)
    a_name = f"{name}-a"
    c_name = f"{name}-c"
    a_compose = _compose(a_name, image, _shell_a(canary_address), a_side=True)
    c_compose = _compose(c_name, image, _shell_c(), a_side=False)
    a_body = _application_body(
        a_controller,
        environment_name=environment_name,
        application_name=a_name,
        compose=a_compose,
    )
    c_body = _application_body(
        c_controller,
        environment_name=environment_name,
        application_name=c_name,
        compose=c_compose,
    )
    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": _TRANSACTION_SCHEMA_VERSION,
        "created_at": _timestamp(created_at),
        "network": network,
        "mother_binding": _binding(private_state),
        "staged_scope": "offline-existing-validator-internal-rpc-canary-compiler",
        "soak_evidence": {
            "locator": _relative(paths, soak_path, "mainnet soak evidence"),
            "file_sha256": soak_file_sha,
            "evidence_sha256": soak_file_sha,
        },
        "chain": {
            "chain_id": soak["chain_id"],
            "genesis_sha256": soak["genesis_sha256"],
            "validator_set": validator_set,
            "blocks_advancing": True,
            "latest_block_fresh": True,
        },
        "identity": {
            "canary_name": name,
            "address": canary_address,
            "identity_locator": _identity_relative(paths, identity_candidate),
            "identity_file_sha256": identity_file_sha,
            "identity_sha256": identity["validator_rpc_canary_identity_sha256"],
            "secret_environment_variable": _SECRET_ENV,
            "private_key_material_in_transaction": False,
            "validator_identity": False,
            "private_rpc_node_identity": False,
        },
        "validator_services": targets,
        "applications": {
            "a_runner": {
                "controller_id": _A_CONTROLLER,
                "application_name": a_name,
                "compose": _compose_record(a_compose),
                "create_request_body": a_body,
                "create_request_body_sha256": hashlib.sha256(canonical_json(a_body)).hexdigest(),
                "result_marker": "MOTHER_VALIDATOR_RPC_CANARY_A_RESULT=",
                "rpc_url": f"http://{_A}:8545",
            },
            "c_verifier": {
                "controller_id": _C_CONTROLLER,
                "application_name": c_name,
                "compose": _compose_record(c_compose),
                "create_request_body": c_body,
                "create_request_body_sha256": hashlib.sha256(canonical_json(c_body)).hexdigest(),
                "result_marker": "MOTHER_VALIDATOR_RPC_CANARY_C_RESULT=",
                "rpc_url": f"http://{_C}:8545",
            },
        },
        "canary_contract": {
            "init_code": "0x" + _CONTRACT_INIT,
            "runtime_code": "0x" + _CONTRACT_RUNTIME,
            "initial_storage_word": "0x" + "0" * 64,
            "written_storage_word": "0x" + _EXPECTED_VALUE,
            "value_transfer_wei": 0,
        },
        "fee_policy": {
            "transaction_type": "eip1559",
            "latest_block_rpc_method": "eth_getBlockByNumber",
            "latest_block_rpc_params": ["latest", False],
            "base_fee_per_gas_required": True,
            "base_fee_ceiling_wei": _BASE_FEE_CEILING_WEI,
            "max_fee_per_gas_wei": _MAX_FEE_PER_GAS_WEI,
            "max_priority_fee_per_gas_wei": _MAX_PRIORITY_FEE_PER_GAS_WEI,
            "gas_limits": {
                "signed_zero_value_self_transfer": _SELF_TRANSFER_GAS_LIMIT,
                "minimal_contract_deployment": _CONTRACT_DEPLOY_GAS_LIMIT,
                "minimal_contract_storage_write": _CONTRACT_WRITE_GAS_LIMIT,
            },
            "total_gas_limit": _TOTAL_GAS_LIMIT,
            "maximum_funding_requirement_wei": _MAXIMUM_FUNDING_REQUIREMENT_WEI,
            "balance_preflight_required": True,
            "execution_refuses_base_fee_above_ceiling": True,
            "execution_refuses_insufficient_balance": True,
        },
        "future_execution_plan": {
            "mutations": [
                {
                    "ordinal": 1,
                    "mutation_id": f"{a_name}.create-application",
                    "controller_id": _A_CONTROLLER,
                    "method": "POST",
                    "endpoint": "/api/v1/applications/dockercompose",
                    "canonical_request_body": a_body,
                    "success_statuses": [200, 201, 202],
                    "bind_result": "application_uuid",
                },
                {
                    "ordinal": 2,
                    "mutation_id": f"{a_name}.bind-canary-secret",
                    "controller_id": _A_CONTROLLER,
                    "method": "POST",
                    "endpoint_template": f"/api/v1/applications/${{result.{a_name}.create-application.application_uuid}}/envs",
                    "secret_source_locator": _identity_relative(paths, identity_candidate),
                    "secret_source_field": "private_key",
                    "environment_key": _SECRET_ENV,
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
                    "endpoint_template": f"/api/v1/applications/${{result.{a_name}.create-application.application_uuid}}",
                    "success_statuses": [200, 204],
                    "cleanup_required": True,
                },
                {
                    "ordinal": 5,
                    "mutation_id": f"{c_name}.create-application",
                    "controller_id": _C_CONTROLLER,
                    "method": "POST",
                    "endpoint": "/api/v1/applications/dockercompose",
                    "canonical_request_body": c_body,
                    "success_statuses": [200, 201, 202],
                    "bind_result": "application_uuid",
                },
                {
                    "ordinal": 6,
                    "mutation_id": f"{c_name}.bind-public-a-result",
                    "controller_id": _C_CONTROLLER,
                    "method": "PATCH",
                    "endpoint_template": f"/api/v1/applications/${{result.{c_name}.create-application.application_uuid}}/envs/bulk",
                    "value_source": "validated A result marker",
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
                    "endpoint_template": f"/api/v1/applications/${{result.{c_name}.create-application.application_uuid}}",
                    "success_statuses": [200, 204],
                    "cleanup_required": True,
                },
            ],
            "read_operations": [
                "A eth_getBlockByNumber latest false before signing",
                "A latest baseFeePerGas preflight",
                "A canary wallet balance preflight",
                "GET A application logs",
                "GET C application logs",
                "GET A and C service details before and after canary",
            ],
            "validator_mutations": [],
            "validator_restarts": [],
        },
        "required_secret_bindings": [
            {
                "name": _SECRET_ENV,
                "purpose": "ephemeral zero-value validator-RPC operations canary signer",
                "source_locator": _identity_relative(paths, identity_candidate),
                "source_field": "private_key",
                "expected_address": canary_address,
                "value_in_transaction": False,
            }
        ],
        "authority": {
            "offline_compilation_only": True,
            "network_access_authorized": False,
            "live_execution_authorized": False,
            "release_authorized": False,
            "validator_vote_authorized": False,
            "validator_identity_authorized": False,
            "validator_mutation_authorized": False,
            "validator_restart_authorized": False,
            "public_endpoint_authorized": False,
            "ssh_authorized": False,
            "requested_use_limit": 0,
        },
        "summary": {
            "clean": True,
            "application_mutation_count": 8,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "public_endpoint_count": 0,
            "host_port_count": 0,
            "signed_zero_value_transaction_compiled": True,
            "minimal_contract_canary_compiled": True,
            "cross_validator_receipt_state_verifier_compiled": True,
            "eip1559_fee_policy_compiled": True,
            "base_fee_preflight_required": True,
            "balance_preflight_required": True,
            "maximum_funding_requirement_wei": _MAXIMUM_FUNDING_REQUIREMENT_WEI,
            "live_mutation_performed": False,
            "next_phase": "verify-validator-rpc-canary-transaction",
        },
    }
    transaction["validator_rpc_canary_transaction_sha256"] = _digest_without(
        transaction,
        "validator_rpc_canary_transaction_sha256",
    )
    return transaction


def write_validator_rpc_canary_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(transaction)
    digest = _digest_without(document, "validator_rpc_canary_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("validator_rpc_canary_transaction_sha256") != digest
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "validator-RPC canary transaction is malformed",
        )
    root = _ensure_root(paths, _TRANSACTION_DIRECTORY, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "transaction"
    destination = root / f"{stamp}-{digest[:16]}.json"
    payload = canonical_json(document)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_CONFLICT",
                "transaction destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_WRITE_FAILED",
            "transaction reread mismatch",
        )
    return destination, digest


def _validate_compose_record(record: Mapping[str, Any], *, expected_service: str) -> None:
    text = record.get("canonical_text")
    if type(text) is not str:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "application Compose text is missing",
        )
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != record.get("sha256"):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "application Compose byte commitment mismatch",
        )
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, Mapping):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "application Compose is not an object",
        )
    semantic = hashlib.sha256(canonical_json(parsed)).hexdigest()
    services = parsed.get("services")
    if (
        semantic != record.get("semantic_sha256")
        or not isinstance(services, Mapping)
        or set(services) != {expected_service}
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "application Compose service set or semantic commitment is invalid",
        )
    lowered = text.lower()
    if any(
        forbidden in lowered
        for forbidden in (
            "\n    ports:",
            "\n    expose:",
            "traefik.",
            "qbft_proposevalidatorvote",
            "--host-allowlist=*",
            "--rpc-http-cors-origins=*",
        )
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_PUBLIC_EXPOSURE",
            "application Compose contains public exposure or validator-vote material",
        )
    for service in services.values():
        if isinstance(service, Mapping) and ("ports" in service or "expose" in service):
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_PUBLIC_EXPOSURE",
                "application Compose publishes a port",
            )


def verify_validator_rpc_canary_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    candidate = Path(transaction_path).resolve(strict=False)
    root = (paths.root / _TRANSACTION_DIRECTORY[0] / _TRANSACTION_DIRECTORY[1]).resolve(
        strict=False
    )
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_PATH_UNSAFE",
            "transaction is outside its canonical directory",
        ) from exc
    document, _, file_sha = _canonical_under(
        paths,
        candidate,
        _TRANSACTION_DIRECTORY,
        "validator-RPC canary transaction",
    )
    digest = _digest_without(document, "validator_rpc_canary_transaction_sha256")
    if not (
        document.get("kind") == _TRANSACTION_KIND
        and document.get("schema_version") == _TRANSACTION_SCHEMA_VERSION
        and document.get("validator_rpc_canary_transaction_sha256") == digest
        and document.get("mother_binding") == _binding(private_state)
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "transaction is modified, stale, or contradictory",
        )
    created = _parse_utc(document.get("created_at"), "transaction.created_at")
    current = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    age = int((current - created).total_seconds())
    if age < -15 or age > max_age_seconds:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_EXPIRED",
            "transaction age is outside the accepted window",
        )
    soak_ref = _mapping(document.get("soak_evidence"), "transaction.soak_evidence")
    soak_path = _resolve(
        paths,
        soak_ref.get("locator"),
        _SOAK_EVIDENCE_DIRECTORY,
        "mainnet soak evidence",
    )
    soak, release, _, soak_file_sha = _load_soak(
        paths,
        private_state,
        soak_path,
        network=document.get("network"),
        selected_nodes=selected_nodes,
        max_age_seconds=soak_max_age_seconds,
    )
    if soak_ref.get("file_sha256") != soak_file_sha:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "soak evidence file binding mismatch",
        )
    identity = _mapping(document.get("identity"), "transaction.identity")
    identity_path = _resolve(
        paths,
        identity.get("identity_locator"),
        _IDENTITY_DIRECTORY,
        "validator-RPC canary identity",
    )
    verified_identity = verify_validator_rpc_canary_identity(
        paths,
        private_state,
        identity_path,
        network=document["network"],
        canary_name=identity.get("canary_name"),
        operation=operation,
    )
    if not (
        identity.get("identity_file_sha256") == verified_identity["identity_file_sha256"]
        and identity.get("identity_sha256") == verified_identity["identity_sha256"]
        and identity.get("address") == verified_identity["address"]
        and identity.get("secret_environment_variable") == _SECRET_ENV
        and identity.get("private_key_material_in_transaction") is False
        and identity.get("validator_identity") is False
        and identity.get("private_rpc_node_identity") is False
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "canary identity binding mismatch",
        )
    chain = _mapping(document.get("chain"), "transaction.chain")
    validator_set = sorted(_address(item, "validator address") for item in soak["validator_set"])
    if not (
        chain.get("chain_id") == soak.get("chain_id") == 42424240
        and chain.get("genesis_sha256") == soak.get("genesis_sha256")
        and chain.get("validator_set") == validator_set
        and chain.get("blocks_advancing") is True
        and chain.get("latest_block_fresh") is True
        and identity["address"] not in validator_set
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "chain or identity separation binding mismatch",
        )
    expected_targets = _targets_from_release(release)
    if document.get("validator_services") != expected_targets:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "validator service lineage mismatch",
        )
    applications = _mapping(document.get("applications"), "transaction.applications")
    a = _mapping(applications.get("a_runner"), "transaction.applications.a_runner")
    c = _mapping(applications.get("c_verifier"), "transaction.applications.c_verifier")
    a_name = f"{identity['canary_name']}-a"
    c_name = f"{identity['canary_name']}-c"
    _validate_compose_record(_mapping(a.get("compose"), "A Compose"), expected_service=a_name)
    _validate_compose_record(_mapping(c.get("compose"), "C Compose"), expected_service=c_name)
    for app, controller_id, app_name in (
        (a, _A_CONTROLLER, a_name),
        (c, _C_CONTROLLER, c_name),
    ):
        controller = _controller_config(
            private_state,
            network=document["network"],
            controller_id=controller_id,
        )
        body = _mapping(app.get("create_request_body"), f"{app_name} create body")
        try:
            compose_from_body = base64.b64decode(
                str(body.get("docker_compose_raw") or ""),
                validate=True,
            ).decode("utf-8")
        except Exception as exc:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
                f"{app_name} create body Compose is invalid",
            ) from exc
        if not (
            app.get("controller_id") == controller_id
            and app.get("application_name") == app_name
            and body.get("project_uuid") == controller["project_uuid"]
            and body.get("server_uuid") == controller["server_uuid"]
            and body.get("name") == app_name
            and body.get("instant_deploy") is False
            and body.get("connect_to_docker_network") is True
            and "domains" not in body
            and "fqdn" not in body
            and "ports_mappings" not in body
            and compose_from_body == app["compose"]["canonical_text"]
            and hashlib.sha256(canonical_json(body)).hexdigest()
            == app.get("create_request_body_sha256")
        ):
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
                f"{app_name} create body is not exact and private",
            )
    contract = _mapping(document.get("canary_contract"), "transaction.canary_contract")
    if contract != {
        "init_code": "0x" + _CONTRACT_INIT,
        "runtime_code": "0x" + _CONTRACT_RUNTIME,
        "initial_storage_word": "0x" + "0" * 64,
        "written_storage_word": "0x" + _EXPECTED_VALUE,
        "value_transfer_wei": 0,
    }:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "canary contract commitment is not exact",
        )
    fee_policy = _mapping(document.get("fee_policy"), "transaction.fee_policy")
    expected_fee_policy = {
        "transaction_type": "eip1559",
        "latest_block_rpc_method": "eth_getBlockByNumber",
        "latest_block_rpc_params": ["latest", False],
        "base_fee_per_gas_required": True,
        "base_fee_ceiling_wei": _BASE_FEE_CEILING_WEI,
        "max_fee_per_gas_wei": _MAX_FEE_PER_GAS_WEI,
        "max_priority_fee_per_gas_wei": _MAX_PRIORITY_FEE_PER_GAS_WEI,
        "gas_limits": {
            "signed_zero_value_self_transfer": _SELF_TRANSFER_GAS_LIMIT,
            "minimal_contract_deployment": _CONTRACT_DEPLOY_GAS_LIMIT,
            "minimal_contract_storage_write": _CONTRACT_WRITE_GAS_LIMIT,
        },
        "total_gas_limit": _TOTAL_GAS_LIMIT,
        "maximum_funding_requirement_wei": _MAXIMUM_FUNDING_REQUIREMENT_WEI,
        "balance_preflight_required": True,
        "execution_refuses_base_fee_above_ceiling": True,
        "execution_refuses_insufficient_balance": True,
    }
    if fee_policy != expected_fee_policy:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "EIP-1559 fee and funding policy is not exact",
        )
    plan = _mapping(document.get("future_execution_plan"), "transaction.future_execution_plan")
    mutations = plan.get("mutations")
    authority = _mapping(document.get("authority"), "transaction.authority")
    expected_authority = {
        "offline_compilation_only": True,
        "network_access_authorized": False,
        "live_execution_authorized": False,
        "release_authorized": False,
        "validator_vote_authorized": False,
        "validator_identity_authorized": False,
        "validator_mutation_authorized": False,
        "validator_restart_authorized": False,
        "public_endpoint_authorized": False,
        "ssh_authorized": False,
        "requested_use_limit": 0,
    }
    if not (
        isinstance(mutations, list)
        and len(mutations) == 8
        and [item.get("ordinal") for item in mutations] == list(range(1, 9))
        and [item.get("controller_id") for item in mutations]
        == [_A_CONTROLLER] * 4 + [_C_CONTROLLER] * 4
        and [item.get("method") for item in mutations]
        == ["POST", "POST", "GET", "DELETE", "POST", "PATCH", "GET", "DELETE"]
        and plan.get("validator_mutations") == []
        and plan.get("validator_restarts") == []
        and authority == expected_authority
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "future execution scope or authority is not exact",
        )
    secret_bindings = document.get("required_secret_bindings")
    if not (
        isinstance(secret_bindings, list)
        and len(secret_bindings) == 1
        and secret_bindings[0].get("name") == _SECRET_ENV
        and secret_bindings[0].get("source_locator") == identity["identity_locator"]
        and secret_bindings[0].get("expected_address") == identity["address"]
        and secret_bindings[0].get("value_in_transaction") is False
    ):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "required secret binding is not exact",
        )

    a_parsed = yaml.safe_load(a["compose"]["canonical_text"])
    a_service = _mapping(
        _mapping(a_parsed.get("services"), "A Compose services").get(a_name),
        "A Compose service",
    )
    image = a_service.get("image")
    environment_name = a["create_request_body"].get("environment_name")
    expected = build_validator_rpc_canary_transaction(
        paths,
        private_state,
        soak_path,
        identity_path,
        canary_name=identity["canary_name"],
        environment_name=environment_name,
        foundry_image=image,
        network=document["network"],
        selected_nodes=selected_nodes,
        soak_max_age_seconds=soak_max_age_seconds,
        created_at=document["created_at"],
        operation=operation,
    )
    if canonical_json(expected) != canonical_json(document):
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID",
            "transaction does not exactly rebuild from its verified lineage",
        )
    return {
        "clean": True,
        "network": document["network"],
        "transaction_path": str(candidate),
        "transaction_sha256": digest,
        "transaction_file_sha256": file_sha,
        "age_seconds": age,
        "chain_id": chain["chain_id"],
        "genesis_sha256": chain["genesis_sha256"],
        "validator_set": validator_set,
        "canary_name": identity["canary_name"],
        "canary_address": identity["address"],
        "application_mutation_count": 8,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "public_endpoint_count": 0,
        "host_port_count": 0,
        "signed_zero_value_transaction_compiled": True,
        "minimal_contract_canary_compiled": True,
        "cross_validator_receipt_state_verifier_compiled": True,
        "eip1559_fee_policy_compiled": True,
        "base_fee_preflight_required": True,
        "balance_preflight_required": True,
        "base_fee_ceiling_wei": _BASE_FEE_CEILING_WEI,
        "max_fee_per_gas_wei": _MAX_FEE_PER_GAS_WEI,
        "max_priority_fee_per_gas_wei": _MAX_PRIORITY_FEE_PER_GAS_WEI,
        "maximum_funding_requirement_wei": _MAXIMUM_FUNDING_REQUIREMENT_WEI,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
        "next_phase": "validator-rpc-canary-funding-not-yet-authorized",
    }


__all__ = [
    "MotherDeploymentValidatorRpcCanaryError",
    "build_validator_rpc_canary_transaction",
    "inspect_validator_rpc_canary_identity_reservation",
    "reserve_validator_rpc_canary_identity",
    "verify_validator_rpc_canary_identity",
    "verify_validator_rpc_canary_transaction",
    "write_validator_rpc_canary_transaction",
]
