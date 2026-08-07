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
import urllib.parse

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


_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_transaction.v10"
_SCHEMA_VERSION = 10
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
_A_CONTROLLER = "coolify-a"
_C_CONTROLLER = "coolify-c"
_IMAGE = "ghcr.io/foundry-rs/foundry:latest"
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


def _cast_cli_probe_script() -> str:
    return f"""set -eu
if cast --version >/dev/null 2>&1; then
  printf '%s\n' "{_marker_line("a_cast_cli_probe", "cast-ok")}"
  exec sleep 900
fi
printf '%s\n' "{_marker_line("a_cast_cli_probe", "cast-unavailable")}"
exit 40
"""


def _cast_balance_script(
    *,
    step: str,
    host: str,
    destination: str,
    expected_balance: int | None,
) -> str:
    rpc = f"http://{host}:8545"
    expected = "" if expected_balance is None else str(expected_balance)
    match_block = (
        """
if [ "$BALANCE_WEI" = "$EXPECTED_BALANCE" ]; then
  printf '%s step=%s classification=match rpc_url=%s block_number=%s balance_wei=%s expected_balance_wei=%s\\n' "$MARKER" "$STEP" "$RPC" "$BLOCK_NUMBER" "$BALANCE_WEI" "$EXPECTED_BALANCE"
  exec sleep 900
fi
printf '%s step=%s classification=nonmatch rpc_url=%s block_number=%s balance_wei=%s expected_balance_wei=%s\\n' "$MARKER" "$STEP" "$RPC" "$BLOCK_NUMBER" "$BALANCE_WEI" "$EXPECTED_BALANCE"
exit 45
"""
        if expected_balance is not None
        else """
printf '%s step=%s classification=rpc-ok rpc_url=%s block_number=%s balance_wei=%s\\n' "$MARKER" "$STEP" "$RPC" "$BLOCK_NUMBER" "$BALANCE_WEI"
exec sleep 900
"""
    )
    return f"""set -eu
MARKER="{_RESULT_MARKER}"
STEP="{step}"
RPC="{rpc}"
DESTINATION="{destination}"
EXPECTED_BALANCE="{expected}"

if ! cast --version >/dev/null 2>&1; then
  printf '%s step=%s classification=cast-unavailable rpc_url=%s\\n' "$MARKER" "$STEP" "$RPC"
  exit 40
fi
if ! BLOCK_NUMBER="$(cast block-number --rpc-url "$RPC" 2>&1)"; then
  printf '%s step=%s classification=rpc-error command=block-number rpc_url=%s\\n' "$MARKER" "$STEP" "$RPC"
  exit 41
fi
BLOCK_NUMBER="$(printf '%s' "$BLOCK_NUMBER" | tr -d '\\r\\n')"
case "$BLOCK_NUMBER" in
  ''|*[!0-9]*) printf '%s step=%s classification=bad-block-number rpc_url=%s\\n' "$MARKER" "$STEP" "$RPC"; exit 42 ;;
esac
if ! BALANCE_WEI="$(cast balance "$DESTINATION" --rpc-url "$RPC" 2>&1)"; then
  printf '%s step=%s classification=rpc-error command=balance rpc_url=%s\\n' "$MARKER" "$STEP" "$RPC"
  exit 43
fi
BALANCE_WEI="$(printf '%s' "$BALANCE_WEI" | tr -d '\\r\\n')"
case "$BALANCE_WEI" in
  ''|*[!0-9]*) printf '%s step=%s classification=bad-balance rpc_url=%s block_number=%s\\n' "$MARKER" "$STEP" "$RPC" "$BLOCK_NUMBER"; exit 44 ;;
esac
{match_block}"""


def _rpc_balance_probe_script(host: str, destination: str) -> str:
    return _cast_balance_script(
        step="a_balance_rpc_probe",
        host=host,
        destination=destination,
        expected_balance=None,
    )


def _rpc_balance_equals_script(host: str, destination: str, expected_balance: int) -> str:
    return _cast_balance_script(
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


def _decimal_ge_function() -> str:
    return """decimal_ge() {
  a="$(printf '%s' "$1" | sed 's/^0*//')"
  b="$(printf '%s' "$2" | sed 's/^0*//')"
  [ -n "$a" ] || a=0
  [ -n "$b" ] || b=0
  if [ "${#a}" -gt "${#b}" ]; then return 0; fi
  if [ "${#a}" -lt "${#b}" ]; then return 1; fi
  [ "$a" = "$b" ] || [ "$a" \\> "$b" ]
}"""


def _funder_script(source: str, destination: str, amount: int) -> str:
    tx_hash_pattern = "0x" + ("?" * 64)
    return f"""set -eu
{_decimal_ge_function()}
MARKER="{_RESULT_MARKER}"
STEP="a_funder"
RPC="http://{_A}:8545"
SOURCE="{source}"
DESTINATION="{destination}"
AMOUNT="{amount}"
MAX_FEE_PER_GAS_WEI="{_MAX_FEE_PER_GAS_WEI}"
MAX_PRIORITY_FEE_PER_GAS_WEI="{_MAX_PRIORITY_FEE_PER_GAS_WEI}"
SOURCE_MINIMUM_WEI="{amount + _FUNDING_TX_MAX_FEE_WEI}"
KEY="${{{_CAPTAIN_SECRET_ENV}:?missing {_CAPTAIN_SECRET_ENV}}}"

FROM="$(cast wallet address "$KEY" | tr '[:upper:]' '[:lower:]')"
test "$FROM" = "$SOURCE"
DESTINATION_BALANCE_WEI="$(cast balance "$DESTINATION" --rpc-url "$RPC" | tr -d '\\r\\n')"
case "$DESTINATION_BALANCE_WEI" in ''|*[!0-9]*) printf '%s step=%s classification=bad-destination-balance rpc_url=%s\\n' "$MARKER" "$STEP" "$RPC"; exit 50 ;; esac
if [ "$DESTINATION_BALANCE_WEI" != "0" ]; then
  printf '%s step=%s classification=destination-not-zero rpc_url=%s balance_wei=%s\\n' "$MARKER" "$STEP" "$RPC" "$DESTINATION_BALANCE_WEI"
  exit 51
fi
LATEST_BLOCK="$(cast rpc eth_getBlockByNumber latest false --rpc-url "$RPC")"
case "$LATEST_BLOCK" in *baseFeePerGas*) ;; *) printf '%s step=%s classification=missing-base-fee rpc_url=%s\\n' "$MARKER" "$STEP" "$RPC"; exit 52 ;; esac
BASE_FEE_WEI="$(cast base-fee latest --rpc-url "$RPC" | tr -d '\\r\\n')"
case "$BASE_FEE_WEI" in ''|*[!0-9]*) printf '%s step=%s classification=bad-base-fee rpc_url=%s\\n' "$MARKER" "$STEP" "$RPC"; exit 53 ;; esac
decimal_ge "$MAX_FEE_PER_GAS_WEI" "$BASE_FEE_WEI" || {{ printf '%s step=%s classification=base-fee-exceeds-cap rpc_url=%s base_fee_wei=%s\\n' "$MARKER" "$STEP" "$RPC" "$BASE_FEE_WEI"; exit 54; }}
SOURCE_BALANCE_WEI="$(cast balance "$SOURCE" --rpc-url "$RPC" | tr -d '\\r\\n')"
case "$SOURCE_BALANCE_WEI" in ''|*[!0-9]*) printf '%s step=%s classification=bad-source-balance rpc_url=%s\\n' "$MARKER" "$STEP" "$RPC"; exit 55 ;; esac
decimal_ge "$SOURCE_BALANCE_WEI" "$SOURCE_MINIMUM_WEI" || {{ printf '%s step=%s classification=source-balance-too-low rpc_url=%s source_balance_wei=%s required_wei=%s\\n' "$MARKER" "$STEP" "$RPC" "$SOURCE_BALANCE_WEI" "$SOURCE_MINIMUM_WEI"; exit 56; }}
TX_HASH="$(cast send "$DESTINATION" --value "$AMOUNT" --gas-limit {_FUNDING_GAS_LIMIT} --gas-price "$MAX_FEE_PER_GAS_WEI" --priority-gas-price "$MAX_PRIORITY_FEE_PER_GAS_WEI" --private-key "$KEY" --rpc-url "$RPC" --async | tr -d '\\r\\n')"
case "$TX_HASH" in {tx_hash_pattern}) ;; *) printf '%s step=%s classification=bad-tx-hash rpc_url=%s\\n' "$MARKER" "$STEP" "$RPC"; exit 57 ;; esac
STATUS="$(cast receipt "$TX_HASH" status --rpc-url "$RPC" | tr -d '\\r\\n')"
test "$STATUS" = "1" || test "$STATUS" = "0x1" || {{ printf '%s step=%s classification=receipt-failed rpc_url=%s tx_hash=%s status=%s\\n' "$MARKER" "$STEP" "$RPC" "$TX_HASH" "$STATUS"; exit 58; }}
POST_BALANCE_WEI="$(cast balance "$DESTINATION" --rpc-url "$RPC" | tr -d '\\r\\n')"
test "$POST_BALANCE_WEI" = "$AMOUNT" || {{ printf '%s step=%s classification=post-balance-not-exact rpc_url=%s tx_hash=%s balance_wei=%s expected_balance_wei=%s\\n' "$MARKER" "$STEP" "$RPC" "$TX_HASH" "$POST_BALANCE_WEI" "$AMOUNT"; exit 59; }}
printf '%s step=%s classification=funded rpc_url=%s tx_hash=%s balance_wei=%s expected_balance_wei=%s\\n' "$MARKER" "$STEP" "$RPC" "$TX_HASH" "$POST_BALANCE_WEI" "$AMOUNT"
exec sleep 900
"""


def _funded_verifier_script(source: str, destination: str, amount: int) -> str:
    tx_hash_pattern = "0x" + ("?" * 64)
    return f"""set -eu
MARKER="{_RESULT_MARKER}"
STEP="c_funded_verifier"
RPC="http://{_C}:8545"
SOURCE="{source}"
DESTINATION="{destination}"
AMOUNT="{amount}"
TX_HASH="${{{_TX_HASH_ENV}:?missing {_TX_HASH_ENV}}}"

case "$TX_HASH" in {tx_hash_pattern}) ;; *) printf '%s step=%s classification=bad-tx-hash rpc_url=%s\\n' "$MARKER" "$STEP" "$RPC"; exit 70 ;; esac
STATUS="$(cast receipt "$TX_HASH" status --rpc-url "$RPC" | tr -d '\\r\\n')"
test "$STATUS" = "1" || test "$STATUS" = "0x1" || {{ printf '%s step=%s classification=receipt-failed rpc_url=%s tx_hash=%s status=%s\\n' "$MARKER" "$STEP" "$RPC" "$TX_HASH" "$STATUS"; exit 71; }}
BALANCE_WEI="$(cast balance "$DESTINATION" --rpc-url "$RPC" | tr -d '\\r\\n')"
case "$BALANCE_WEI" in ''|*[!0-9]*) printf '%s step=%s classification=bad-balance rpc_url=%s tx_hash=%s\\n' "$MARKER" "$STEP" "$RPC" "$TX_HASH"; exit 72 ;; esac
test "$BALANCE_WEI" = "$AMOUNT" || {{ printf '%s step=%s classification=balance-not-exact rpc_url=%s tx_hash=%s balance_wei=%s expected_balance_wei=%s\\n' "$MARKER" "$STEP" "$RPC" "$TX_HASH" "$BALANCE_WEI" "$AMOUNT"; exit 73; }}
printf '%s step=%s classification=verified rpc_url=%s tx_hash=%s balance_wei=%s expected_balance_wei=%s\\n' "$MARKER" "$STEP" "$RPC" "$TX_HASH" "$BALANCE_WEI" "$AMOUNT"
exec sleep 900
"""


def _exact_balance_verifier_script(host: str, destination: str, amount: int) -> str:
    return _cast_balance_script(
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
    ) -> tuple[str, dict[str, Any]]:
        compose = _compose(name, command)
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
            "a_cast_cli_probe",
            controller_id=_A_CONTROLLER,
            name=f"{canary_name}-probe-cast-cli-a",
            command=_cast_cli_probe_script(),
            proof=(
                "the Foundry helper image can execute cast before any RPC or funding "
                "boundary is evaluated"
            ),
        ),
        spec(
            "a_balance_rpc_probe",
            controller_id=_A_CONTROLLER,
            name=f"{canary_name}-probe-balance-rpc-a",
            command=_rpc_balance_probe_script(_A, destination),
            proof=(
                "A private RPC answers cast balance with a parseable decimal quantity "
                "before any balance classification or funding service is started"
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
            "a_funder",
            controller_id=_A_CONTROLLER,
            name=f"{canary_name}-fund-a",
            command=_funder_script(captain["address"], destination, amount),
            proof=(
                "the exact capped transfer receipt succeeded on A and the destination "
                "balance became exact before PID 1 transitioned to sleep"
            ),
            secret_binding=True,
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
                "C independently finds exactly one recent matching captain transfer, "
                "verifies its successful receipt, and verifies the exact destination balance"
            ),
        ),
        spec(
            "c_reconciled_verifier",
            controller_id=_C_CONTROLLER,
            name=f"{canary_name}-verify-reconciled-c",
            command=_verifier_script(destination, amount),
            proof="C independently verifies the exact pre-existing destination balance",
        ),
    ])

    future_mutations: list[dict[str, Any]] = []
    ordinal = 0
    for key in (
        "a_cast_cli_probe",
        "a_balance_rpc_probe",
        "a_exact_balance_classifier",
        "a_zero_balance_classifier",
        "a_funder",
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
        if key == "a_funder":
            ordinal += 1
            future_mutations.append({
                "ordinal": ordinal,
                "conditional_service": key,
                "mutation_id": f"{name}.bind-captain-secret",
                "controller_id": app["controller_id"],
                "method": "POST",
                "endpoint_template": "/api/v1/services/${result.service_uuid}/envs",
                "secret_source_field": captain["private_state_field"],
                "environment_key": _CAPTAIN_SECRET_ENV,
                "value_in_transaction": False,
                "success_statuses": [200, 201, 202],
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
        "staged_scope": "offline-exact-capped-validator-rpc-canary-funding-runtime-marker-first-v4",
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
            "source_balance_preflight_required_when_transfer_required": True,
            "destination_zero_or_exact_balance_precondition_required": True,
            "idempotent_exact_balance_reconciliation_supported": True,
            "cross_validator_receipt_verification_required_when_new_transfer": True,
            "cross_validator_balance_verification_required": True,
            "transaction_hash_result_transport_required": False,
            "failed_started_funder_without_health_proof_is_chain_state_unknown": True,
        },
        "applications": applications,
        "future_execution_plan": {
            "classification": [
                "prove exact balance on A through one healthy classifier",
                "otherwise prove zero balance on A through one healthy classifier",
                "fail closed if neither classifier becomes healthy",
            ],
            "funded_path": [
                "start the exact capped A funder only after zero classification",
                "accept A funder completion only from the committed healthy service state",
                "require a separate A exact-balance verifier after the funder completes",
                "accept C completion only after independent transfer discovery, receipt verification, and exact balance verification",
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
                "bound_only_after_zero_balance_health_proof": True,
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
            "idempotent_balance_reconcile_or_fund_compiled": True,
            "service_start_transport_compiled": True,
            "service_health_result_channel_compiled": True,
            "runtime_log_result_channel_authorized": True,
            "deployment_uuid_required": False,
            "generic_deploy_endpoint_authorized": False,
            "transfer_value_wei": amount,
            "funding_value_cap_wei": amount,
            "source_maximum_total_debit_wei": amount + _FUNDING_TX_MAX_FEE_WEI,
            "maximum_service_mutation_count": 23,
            "minimum_service_mutation_count": 3,
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
    transport = _mapping(document.get("coolify_transport"), "funding.coolify_transport")
    applications = _mapping(document.get("applications"), "funding.applications")
    if not (
        set(applications)
        == {
            "a_cast_cli_probe",
            "a_balance_rpc_probe",
            "a_exact_balance_classifier",
            "a_zero_balance_classifier",
            "a_funder",
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
        "minimum_service_mutation_count": 3,
        "maximum_service_mutation_count": 23,
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
        and summary.get("public_endpoint_count") == 0
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
            "public_endpoint_count": 0,
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
        "minimum_service_mutation_count": 3,
        "maximum_service_mutation_count": 23,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "public_endpoint_count": 0,
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
        "a_cast_cli_probe": {"cast-ok"},
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
    observation_count = 0
    consecutive_terminal = 0
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
        last_status = status or last_status
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
                "service_uuid": service_uuid,
                "service_name": service_name,
                "phase": phase,
                "observation_count": observation_count,
            }

        base_status = status.split(":", 1)[0] if status else ""
        if base_status in {"exited", "stopped", "dead", "error", "failed"}:
            consecutive_terminal += 1
        else:
            consecutive_terminal = 0

        elapsed = time.monotonic() - started
        terminal_grace = min(10.0, max_wait_seconds)
        if consecutive_terminal >= 2 and elapsed >= terminal_grace:
            return {
                "healthy": False,
                "service_status": status,
                "service_uuid": service_uuid,
                "service_name": service_name,
                "phase": phase,
                "observation_count": observation_count,
                "reason": "terminal-nonhealthy",
            }
        if elapsed >= max_wait_seconds:
            return {
                "healthy": False,
                "service_status": last_status or None,
                "service_uuid": service_uuid,
                "service_name": service_name,
                "phase": phase,
                "observation_count": observation_count,
                "reason": "health-timeout",
            }
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

    try:
        cast_proof = run_service("a_cast_cli_probe")
        if cast_proof.get("healthy") is not True:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_CAST_UNAVAILABLE",
                "A helper image did not prove that cast is available before RPC probing",
            )
        probe_proof = run_service("a_balance_rpc_probe")
        if probe_proof.get("healthy") is not True:
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RPC_UNAVAILABLE",
                "A did not prove that private RPC can answer cast balance before funding",
            )
        exact_proof = run_service("a_exact_balance_classifier")
        if exact_proof.get("healthy") is True:
            funding_mode = "already-funded"
            chain_state = "exact-on-A-not-yet-verified-on-C"
        else:
            zero_proof = run_service("a_zero_balance_classifier")
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
                    "A RPC is reachable, but the destination balance is neither zero nor the exact funded amount" + suffix,
                )
            funding_mode = "funded"
            funder_proof = run_service(
                "a_funder",
                bind_captain_secret=True,
            )
            if funder_proof.get("healthy") is not True:
                raise _error(
                    "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_RESULT_UNAVAILABLE",
                    "A funder did not reach its committed healthy completion state",
                )
            funder_result = runtime_results.get("a_funder", {})
            candidate_hash = (
                funder_result.get("tx_hash")
                if isinstance(funder_result, Mapping)
                else None
            )
            if not (type(candidate_hash) is str and re.fullmatch(r"0x[0-9a-fA-F]{64}", candidate_hash)):
                raise _error(
                    "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_TRANSACTION_HASH_UNAVAILABLE",
                    "A funder completed without a structured funding transaction hash",
                )
            funding_transaction_hash = candidate_hash.lower()
            a_post_funding_proof = run_service("a_post_funding_verifier")
            if a_post_funding_proof.get("healthy") is not True:
                raise _error(
                    "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_A_BALANCE_NOT_VERIFIED",
                    "A did not independently prove the exact destination balance after funding",
                )
            chain_state = "exact-on-A-not-yet-verified-on-C"

        c_key = (
            "c_funded_verifier"
            if funding_mode == "funded"
            else "c_reconciled_verifier"
        )
        c_extra_env = (
            {_TX_HASH_ENV: funding_transaction_hash}
            if funding_mode == "funded" and funding_transaction_hash is not None
            else None
        )
        c_proof = run_service(c_key, extra_env=c_extra_env)
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
            "result_channel": "service-detail-health+runtime-result-marker",
            "runtime_result": runtime_results.get(c_key),
            "balance_verified": True,
            "receipt_verified": funding_mode == "funded",
            "transaction_hash_recorded": funding_mode == "funded",
            "funding_transaction_hash": funding_transaction_hash if funding_mode == "funded" else None,
            "proof": (
                "tx-hash-bound-cross-validator-receipt-and-balance"
                if funding_mode == "funded"
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
        and funding_mode == "funded"
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
            "service_health_result_channel_used": True,
            "runtime_log_result_channel_used": True,
            "runtime_result_marker_count": len(runtime_results),
            "deployment_uuid_required": False,
            "temporary_A_application_deleted": a_deleted,
            "temporary_C_application_deleted": c_deleted,
            "temporary_services_deleted": all_deleted,
            "temporary_service_count": len(created_services),
            "application_mutation_count": len(receipts),
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "public_endpoint_count": 0,
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
    if funding_mode == "funded":
        spec_keys = [
            "a_cast_cli_probe",
            "a_balance_rpc_probe",
            "a_exact_balance_classifier",
            "a_zero_balance_classifier",
            "a_funder",
            "a_post_funding_verifier",
            "c_funded_verifier",
        ]
        required_healthy = {
            "a_cast_cli_probe",
            "a_balance_rpc_probe",
            "a_zero_balance_classifier",
            "a_funder",
            "a_post_funding_verifier",
            "c_funded_verifier",
        }
        required_nonhealthy = {"a_exact_balance_classifier"}
    elif funding_mode == "already-funded":
        spec_keys = [
            "a_cast_cli_probe",
            "a_balance_rpc_probe",
            "a_exact_balance_classifier",
            "c_reconciled_verifier",
        ]
        required_healthy = {
            "a_cast_cli_probe",
            "a_balance_rpc_probe",
            "a_exact_balance_classifier",
            "c_reconciled_verifier",
        }
        required_nonhealthy = set()
    else:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
            "funding evidence mode is invalid",
        )

    expected_ids: list[str] = []
    expected_methods: list[str] = []
    for key in spec_keys:
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

    for key in required_healthy:
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
    for key in required_nonhealthy:
        found = phase_observations(key)
        if not (
            found
            and all(item.get("healthy") is False for item in found)
        ):
            raise _error(
                "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_EVIDENCE_INVALID",
                f"{key} unexpectedly contains a healthy proof",
            )

    runtime_results = _mapping(document.get("runtime_results"), "evidence.runtime_results")
    for key in spec_keys:
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
    summary = _mapping(document.get("summary"), "evidence.summary")
    common = (
        isinstance(c_result, Mapping)
        and c_result.get("mode") == funding_mode
        and c_result.get("controller_id") == _C_CONTROLLER
        and c_result.get("result_channel") == "service-detail-health+runtime-result-marker"
        and _healthy_service_status(c_result.get("service_status"))
        and c_result.get("balance_verified") is True
        and document.get("transaction_hash_recorded") == (funding_mode == "funded")
        and document.get("chain_state") == "exact-cross-validator-verified"
        and document["transfer_value_wei"] == release["funding_policy"]["transfer_value_wei"]
        and document["canary_address"] == release["destination"]["address"]
        and summary.get("clean") is True
        and summary.get("complete") is True
        and summary.get("funding_complete") is True
        and summary.get("canary_balance_verified_on_A") is True
        and summary.get("canary_balance_verified_on_C") is True
        and summary.get("exact_transfer_value_verified") is True
        and summary.get("transaction_hash_recorded") == (funding_mode == "funded")
        and summary.get("service_health_result_channel_used") is True
        and summary.get("runtime_log_result_channel_used") is True
        and type(summary.get("runtime_result_marker_count")) is int
        and summary.get("runtime_result_marker_count") >= len(spec_keys)
        and summary.get("deployment_uuid_required") is False
        and summary.get("temporary_A_application_deleted") is True
        and summary.get("temporary_C_application_deleted") is True
        and summary.get("temporary_services_deleted") is True
        and summary.get("temporary_service_count") == len(spec_keys)
        and summary.get("application_mutation_count") == len(expected_ids)
        and summary.get("validator_mutation_count") == 0
        and summary.get("validator_restart_count") == 0
        and summary.get("public_endpoint_count") == 0
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
    reconciled = (
        funding_mode == "already-funded"
        and c_result.get("receipt_verified") is False
        and c_result.get("proof") == "exact-balance-reconciliation"
        and c_result.get("transaction_hash_recorded") is False
        and document.get("funding_transaction_hash") in {None, ""}
        and summary.get("funding_performed") is False
        and summary.get("funding_reconciled_from_prior_execution") is True
        and summary.get("funding_receipt_verified_on_C") is False
    )
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
        "funding_receipt_verified_on_C": funded,
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


