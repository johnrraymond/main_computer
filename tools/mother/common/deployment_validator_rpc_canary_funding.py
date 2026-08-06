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


_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_transaction.v7"
_SCHEMA_VERSION = 7
_DIRECTORY = ("actions", "deployment-validator-rpc-canary-funding-transactions")
_RELEASE_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_release.v2"
_CLAIM_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_validator_rpc_canary_funding_evidence.v2"
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






def _assert_int_comparison_script(left_env: str, operator: str, right: int) -> str:
    if operator not in {"eq", "le", "ge"}:
        raise _error(
            "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_INVALID",
            "unsupported integer comparison operator",
        )
    return (
        "python - <<'PY'\n"
        "import os, re\n"
        f"left = os.environ.get('{left_env}', '')\n"
        "if not re.fullmatch(r'[0-9]+', left):\n"
        "    raise SystemExit(f'non-decimal wei value: {left!r}')\n"
        "left_int = int(left)\n"
        f"right_int = {right}\n"
        f"op = '{operator}'\n"
        "ok = (left_int == right_int if op == 'eq' else left_int <= right_int if op == 'le' else left_int >= right_int)\n"
        "if not ok:\n"
        "    raise SystemExit(f'integer comparison failed: {left_int} {op} {right_int}')\n"
        "PY"
    )


def _cast_balance_line(destination: str, env_name: str) -> str:
    # Foundry's `cast balance` returns wei by default. Do not pass
    # `--ether=false`; `--ether` is a flag, and the false-valued form turns the
    # classifier into a tooling failure instead of a balance proof.
    return f'{env_name}=$(cast balance --rpc-url "$RPC" {destination})'


def _balance_classifier_script(host: str, destination: str, expected_balance: int) -> str:
    return f"""set -eu
RPC=http://{host}:8545
{_cast_balance_line(destination, "BAL")}
export BAL
{_assert_int_comparison_script("BAL", "eq", expected_balance)}
exec sleep 900
"""


def _funder_script(source: str, destination: str, amount: int) -> str:
    return f"""set -eu
test -n "${{{_CAPTAIN_SECRET_ENV}:-}}"
RPC=http://{_A}:8545
FROM=$(cast wallet address --private-key "${{{_CAPTAIN_SECRET_ENV}}}" | tr '[:upper:]' '[:lower:]')
test "$FROM" = "{source}"
{_cast_balance_line(destination, "DEST_BAL")}
export DEST_BAL
{_assert_int_comparison_script("DEST_BAL", "eq", 0)}
BASE=$(cast rpc --rpc-url "$RPC" eth_getBlockByNumber latest false | python -c 'import json,sys; v=json.load(sys.stdin).get("baseFeePerGas"); assert isinstance(v,str) and v.startswith("0x"); print(int(v,16))')
export BASE
{_assert_int_comparison_script("BASE", "le", _MAX_FEE_PER_GAS_WEI)}
{_cast_balance_line(source, "SOURCE_BAL")}
export SOURCE_BAL
{_assert_int_comparison_script("SOURCE_BAL", "ge", amount + _FUNDING_TX_MAX_FEE_WEI)}
TX=$(cast send --json --rpc-url "$RPC" --private-key "${{{_CAPTAIN_SECRET_ENV}}}" --gas-limit {_FUNDING_GAS_LIMIT} --gas-price {_MAX_FEE_PER_GAS_WEI} --priority-gas-price {_MAX_PRIORITY_FEE_PER_GAS_WEI} --value {amount} {destination})
printf '%s' "$TX" | python -c 'import json,sys; v=json.load(sys.stdin); s=v.get("status"); n=int(s,0) if isinstance(s,str) else int(s); assert n==1'
{_cast_balance_line(destination, "POST_BAL")}
export POST_BAL
{_assert_int_comparison_script("POST_BAL", "eq", amount)}
exec sleep 900
"""



def _funded_verifier_script(source: str, destination: str, amount: int) -> str:
    return f"""set -eu
python - <<'PY'
import json
import urllib.request

RPC = "http://{_C}:8545"
SOURCE = "{source}"
DESTINATION = "{destination}"
AMOUNT = {amount}
WINDOW = 256


def rpc(method, params):
    body = json.dumps({{"jsonrpc": "2.0", "id": 1, "method": method, "params": params}}).encode("utf-8")
    request = urllib.request.Request(RPC, data=body, headers={{"Content-Type": "application/json"}})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    assert "error" not in payload
    return payload["result"]


latest = int(rpc("eth_blockNumber", []), 16)
matches = []
for number in range(max(0, latest - WINDOW), latest + 1):
    block = rpc("eth_getBlockByNumber", [hex(number), True])
    if not isinstance(block, dict):
        continue
    for transaction in block.get("transactions", []):
        if not isinstance(transaction, dict):
            continue
        sender = str(transaction.get("from", "")).lower()
        recipient = str(transaction.get("to", "")).lower()
        value = transaction.get("value")
        if (
            sender == SOURCE
            and recipient == DESTINATION
            and isinstance(value, str)
            and int(value, 16) == AMOUNT
        ):
            matches.append(transaction)

assert len(matches) == 1
transaction = matches[0]
transaction_hash = str(transaction["hash"]).lower()
receipt = rpc("eth_getTransactionReceipt", [transaction_hash])
assert isinstance(receipt, dict)
assert str(receipt.get("transactionHash", "")).lower() == transaction_hash
assert int(receipt.get("status", "0x0"), 16) == 1
assert int(rpc("eth_getBalance", [DESTINATION, "latest"]), 16) == AMOUNT
PY
exec sleep 900
"""


def _exact_balance_verifier_script(host: str, destination: str, amount: int) -> str:
    return f"""set -eu
RPC=http://{host}:8545
{_cast_balance_line(destination, "BAL")}
export BAL
{_assert_int_comparison_script("BAL", "eq", amount)}
exec sleep 900
"""


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
        "staged_scope": "offline-exact-capped-validator-rpc-canary-funding-status-health-v1",
        "coolify_transport": {
            "resource_api": "services",
            "create_endpoint": "/api/v1/services",
            "compose_encoding": "base64",
            "environment_uuid_resolution": "read-only-exact-name-before-create",
            "service_start_endpoint_template": "/api/v1/services/{service_uuid}/start",
            "service_start_method": "POST",
            "service_detail_endpoint_template": "/api/v1/services/{service_uuid}",
            "healthy_running_statuses": ["running:healthy", "running:healthy:excluded"],
            "result_channel": "service-detail-health",
            "deployment_uuid_required": False,
            "deployment_inventory_endpoint_authorized": False,
            "deployment_result_endpoint_authorized": False,
            "service_log_endpoints_authorized": False,
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
            "runtime_log_result_channel_authorized": False,
            "deployment_uuid_required": False,
            "generic_deploy_endpoint_authorized": False,
            "transfer_value_wei": amount,
            "funding_value_cap_wei": amount,
            "source_maximum_total_debit_wei": amount + _FUNDING_TX_MAX_FEE_WEI,
            "maximum_service_mutation_count": 16,
            "minimum_service_mutation_count": 6,
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
            "a_exact_balance_classifier",
            "a_zero_balance_classifier",
            "a_funder",
            "a_post_funding_verifier",
            "c_funded_verifier",
            "c_reconciled_verifier",
        }
        and transport.get("result_channel") == "service-detail-health"
        and transport.get("deployment_uuid_required") is False
        and transport.get("deployment_inventory_endpoint_authorized") is False
        and transport.get("deployment_result_endpoint_authorized") is False
        and transport.get("service_log_endpoints_authorized") is False
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
        "runtime_log_result_channel_authorized": False,
        "deployment_uuid_required": False,
        "deployment_inventory_resolution_required": False,
        "generic_deploy_endpoint_authorized": False,
        "minimum_service_mutation_count": 6,
        "maximum_service_mutation_count": 16,
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
        and document.get("schema_version") == 2
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
            "runtime_log_result_channel_authorized": False,
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
        and policy.get("runtime_log_result_channel_authorized") is False
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
        "minimum_service_mutation_count": 6,
        "maximum_service_mutation_count": 16,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "public_endpoint_count": 0,
        "funding_authorized": True,
        "canary_execution_authorized": False,
        "live_execution_authorized": True,
        "validator_vote_authorized": False,
        "result_channel": "service-detail-health",
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
        transport.get("result_channel") == "service-detail-health"
        and transport.get("service_detail_endpoint_template")
        == "/api/v1/services/{service_uuid}"
        and transport.get("service_start_endpoint_template")
        == "/api/v1/services/{service_uuid}/start"
        and transport.get("service_start_method") == "POST"
        and transport.get("deployment_uuid_required") is False
        and transport.get("deployment_inventory_endpoint_authorized") is False
        and transport.get("deployment_result_endpoint_authorized") is False
        and transport.get("service_log_endpoints_authorized") is False
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
    funding_mode: str | None = None
    funding_start_acknowledged = False
    a_funder_health_proven = False
    a_post_funding_balance_proven = False
    cross_validator_proof: dict[str, Any] | None = None
    chain_state = "unchanged-before-funder-start"
    started_at = _timestamp()
    expected_amount = int(release["funding_policy"]["transfer_value_wei"])
    destination = str(release["destination"]["address"]).lower()

    def run_service(spec_key: str, *, bind_captain_secret: bool = False) -> Mapping[str, Any]:
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
        exact_proof = run_service("a_exact_balance_classifier")
        if exact_proof.get("healthy") is True:
            funding_mode = "already-funded"
            chain_state = "exact-on-A-not-yet-verified-on-C"
        else:
            zero_proof = run_service("a_zero_balance_classifier")
            if zero_proof.get("healthy") is not True:
                raise _error(
                    "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_FUNDING_BALANCE_PRECONDITION_FAILED",
                    "A did not positively prove either the zero or exact destination balance",
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
        c_proof = run_service(c_key)
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
            "result_channel": "service-detail-health",
            "balance_verified": True,
            "receipt_verified": funding_mode == "funded",
            "transaction_hash_recorded": False,
            "proof": (
                "bounded-recent-transfer-discovery-and-receipt"
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
        "schema_version": 2,
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
        "funding_transaction_hash": None,
        "transaction_hash_recorded": False,
        "chain_state": chain_state,
        "cross_validator_verification": cross_validator_proof,
        "mutation_receipts": receipts,
        "service_observations": observations,
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
            "transaction_hash_recorded": False,
            "service_health_result_channel_used": True,
            "runtime_log_result_channel_used": False,
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
        "funding_transaction_hash": None,
        "transaction_hash_recorded": False,
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
        and document.get("schema_version") == 2
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
            "a_exact_balance_classifier",
            "a_zero_balance_classifier",
            "a_funder",
            "a_post_funding_verifier",
            "c_funded_verifier",
        ]
        required_healthy = {
            "a_zero_balance_classifier",
            "a_funder",
            "a_post_funding_verifier",
            "c_funded_verifier",
        }
        required_nonhealthy = {"a_exact_balance_classifier"}
    elif funding_mode == "already-funded":
        spec_keys = [
            "a_exact_balance_classifier",
            "c_reconciled_verifier",
        ]
        required_healthy = {
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
            "/logs" not in str(item.get("endpoint", ""))
            and "/deployments" not in str(item.get("endpoint", ""))
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

    c_result = document.get("cross_validator_verification")
    summary = _mapping(document.get("summary"), "evidence.summary")
    common = (
        isinstance(c_result, Mapping)
        and c_result.get("mode") == funding_mode
        and c_result.get("controller_id") == _C_CONTROLLER
        and c_result.get("result_channel") == "service-detail-health"
        and _healthy_service_status(c_result.get("service_status"))
        and c_result.get("balance_verified") is True
        and c_result.get("transaction_hash_recorded") is False
        and document.get("funding_transaction_hash") in {None, ""}
        and document.get("transaction_hash_recorded") is False
        and document.get("chain_state") == "exact-cross-validator-verified"
        and document["transfer_value_wei"] == release["funding_policy"]["transfer_value_wei"]
        and document["canary_address"] == release["destination"]["address"]
        and summary.get("clean") is True
        and summary.get("complete") is True
        and summary.get("funding_complete") is True
        and summary.get("canary_balance_verified_on_A") is True
        and summary.get("canary_balance_verified_on_C") is True
        and summary.get("exact_transfer_value_verified") is True
        and summary.get("transaction_hash_recorded") is False
        and summary.get("service_health_result_channel_used") is True
        and summary.get("runtime_log_result_channel_used") is False
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
        and c_result.get("proof") == "bounded-recent-transfer-discovery-and-receipt"
        and summary.get("funding_performed") is True
        and summary.get("funding_reconciled_from_prior_execution") is False
        and summary.get("funding_receipt_verified_on_C") is True
    )
    reconciled = (
        funding_mode == "already-funded"
        and c_result.get("receipt_verified") is False
        and c_result.get("proof") == "exact-balance-reconciliation"
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
        "funding_transaction_hash": None,
        "transaction_hash_recorded": False,
        "funding_source_address": document["funding_source_address"],
        "canary_address": document["canary_address"],
        "transfer_value_wei": document["transfer_value_wei"],
        "funding_receipt_verified_on_C": funded,
        "canary_balance_verified_on_A": True,
        "canary_balance_verified_on_C": True,
        "funding_reconciled_from_prior_execution": reconciled,
        "result_channel": "service-detail-health",
        "temporary_applications_deleted": True,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "public_endpoint_count": 0,
        "validator_vote_performed": False,
        "canary_execution_performed": False,
        "next_phase": "validator-rpc-canary-execution-release-not-yet-authorized",
    }


