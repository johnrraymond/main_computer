"""Mother-native offline compiler for first-genesis and replica admission.

The compiler consumes a successful reserved-identity execution receipt and the
current committed Mother private state.  It produces one deterministic Besu
QBFT genesis for the exact ``initial`` node plus an explicit ``soft`` admission
specification for every later starter validator.  It performs no network access,
does not update Coolify, does not start a service, and never persists private
key values.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any

import yaml

from . import atomic_files
from .canonical import canonical_json
from .deployment_plan import build_starter_deployment_plan
from .deployment_identity_rollback import verify_identity_rollback_cycle_evidence
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_TRANSACTION_KIND = "main_computer.mother.deployment_genesis_transaction.v1"
_IDENTITY_EXECUTION_KIND = "main_computer.mother.deployment_identity_execution_result.v1"
_TRANSACTION_DIRECTORY = ("actions", "deployment-genesis-transactions")
_IDENTITY_EXECUTION_DIRECTORY = ("actions", "deployment-identity-executions")
_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_PRIVATE_KEY_LITERAL_RE = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SERVICE_ENV_ENDPOINT_RE = re.compile(r"/api/v1/services/([A-Za-z0-9._-]+)/envs\Z")
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 4
_DEFAULT_FUNDED_ACCOUNT_BALANCE = "0x21e19e0c9bab2400000"
_DEFAULT_GENESIS_BASE_FEE_PER_GAS = "0x3b9aca00"
_DEFAULT_SHANGHAI_TIME = 0


class MotherDeploymentGenesisError(RuntimeError):
    """First-genesis staging failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            f"{path} must be a non-empty string",
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            f"{path} is not a safe identifier",
        )
    return text


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            f"{path} must be a lowercase SHA-256 digest",
        )
    return value


def _utc_timestamp(value: Any, path: str) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if type(value) is not str or not value:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            f"{path} must be a UTC timestamp",
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            f"{path} is malformed",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            f"{path} must be UTC",
        )
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            f"{path} must be a mapping",
        )
    return value


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        value = yaml.safe_load(private_state.document_bytes)
    except yaml.YAMLError as exc:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            "committed Mother private state is malformed",
        ) from exc
    if type(value) is not dict:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            "committed Mother private state must be a mapping",
        )
    return value


def _contains_sensitive_field(value: Any) -> bool:
    forbidden = {
        "access_token",
        "api_token",
        "credential",
        "mnemonic",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "seed",
    }
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in forbidden or _contains_sensitive_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_field(item) for item in value)
    return False


def _contains_private_key_literal(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_private_key_literal(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_private_key_literal(item) for item in value)
    return type(value) is str and _PRIVATE_KEY_LITERAL_RE.fullmatch(value) is not None


def _digest_without(value: Mapping[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _canonical_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            f"{label} could not be read as canonical JSON",
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            f"{label} is not canonical JSON",
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _relative_locator(paths: PrivateStatePaths, candidate: Path, *, label: str) -> str:
    root = paths.root.resolve(strict=False)
    resolved = Path(candidate).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_PATH_UNSAFE",
            f"{label} must be beneath the canonical Mother root",
        ) from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            f"{label} locator must be a relative POSIX path",
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_PATH_UNSAFE",
            f"{label} locator is unsafe",
        )
    resolved = (paths.root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_PATH_UNSAFE",
            f"{label} locator escapes Mother state",
        ) from exc
    return resolved


def _address(value: Any, path: str) -> str:
    if type(value) is not str or _ADDRESS_RE.fullmatch(value) is None:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_IDENTITY_INVALID",
            f"{path} must be a 20-byte Ethereum address",
        )
    return "0x" + value[2:].lower()


def _positive_chain_id(value: Any) -> int:
    if type(value) is bool:
        value = None
    if type(value) is str and value.isdigit():
        value = int(value)
    if type(value) is not int or value <= 0:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_POLICY_INVALID",
            "networks.<network>.chain_id must be a positive integer",
        )
    return value


def _resolve_dotted(document: Mapping[str, Any], dotted: Any, *, path: str) -> Any:
    if type(dotted) is not str or not dotted or ".." in dotted:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_POLICY_INVALID",
            f"{path} is not a valid private-state reference",
        )
    current: Any = document
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise MotherDeploymentGenesisError(
                "MOTHER_DEPLOY_GENESIS_POLICY_INVALID",
                f"{path} does not resolve: {dotted!r}",
            )
        current = current[part]
    return current


def _rlp_encode_bytes(raw: bytes) -> bytes:
    if len(raw) == 1 and raw[0] < 0x80:
        return raw
    if len(raw) <= 55:
        return bytes([0x80 + len(raw)]) + raw
    length_bytes = len(raw).to_bytes((len(raw).bit_length() + 7) // 8, "big")
    return bytes([0xB7 + len(length_bytes)]) + length_bytes + raw


def _rlp_encode_list(items: Sequence[bytes]) -> bytes:
    payload = b"".join(items)
    if len(payload) <= 55:
        return bytes([0xC0 + len(payload)]) + payload
    length_bytes = len(payload).to_bytes((len(payload).bit_length() + 7) // 8, "big")
    return bytes([0xF7 + len(length_bytes)]) + length_bytes + payload


def qbft_genesis_extra_data(validators: Sequence[str]) -> str:
    normalized: list[str] = []
    for index, value in enumerate(validators):
        address = _address(value, f"validators[{index}]")
        if address not in normalized:
            normalized.append(address)
    if not normalized:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_POLICY_INVALID",
            "first genesis requires exactly one or more validators",
        )
    vanity = b"\x00" * 32
    validator_items = [_rlp_encode_bytes(bytes.fromhex(address[2:])) for address in normalized]
    encoded = _rlp_encode_list(
        [
            _rlp_encode_bytes(vanity),
            _rlp_encode_list(validator_items),
            _rlp_encode_list([]),
            _rlp_encode_bytes(b""),
            _rlp_encode_list([]),
        ]
    )
    return "0x" + encoded.hex()


def _identity_execution(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    network: str,
    selected_nodes: tuple[str, ...],
) -> tuple[dict[str, Any], str, tuple[str, ...], dict[str, dict[str, Any]]]:
    root = (paths.root / _IDENTITY_EXECUTION_DIRECTORY[0] / _IDENTITY_EXECUTION_DIRECTORY[1]).resolve(strict=False)
    candidate = Path(execution_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_PATH_UNSAFE",
            "identity execution must be beneath the canonical execution root",
        ) from exc
    execution, raw, byte_sha256 = _canonical_file(candidate, label="identity execution")
    if execution.get("kind") != _IDENTITY_EXECUTION_KIND or execution.get("status") != "pass":
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID",
            "identity execution is not a successful canonical result",
        )
    if execution.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_STALE_BINDING",
            "identity execution does not bind the current Mother generation",
        )
    if execution.get("network") != network:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_SELECTION_MISMATCH",
            "identity execution is for a different network",
        )
    actual_nodes = tuple(_identifier(item, "identity execution node") for item in execution.get("nodes", []))
    if selected_nodes and selected_nodes != actual_nodes:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_SELECTION_MISMATCH",
            "identity execution does not cover the requested node sequence",
        )
    if not actual_nodes:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID",
            "identity execution contains no nodes",
        )
    expected_keys = {"MC_MOTHER_VALIDATOR_PRIVATE_KEY", "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY"}
    expected_commitment_count = len(actual_nodes) * len(expected_keys)
    summary = execution.get("summary")
    if not isinstance(summary, Mapping) or not all(
        [
            summary.get("complete") is True,
            summary.get("planned_mutation_count") == expected_commitment_count,
            summary.get("attempted_mutation_count") == expected_commitment_count,
            summary.get("succeeded_mutation_count") == expected_commitment_count,
            summary.get("failed_mutation_count") == 0,
            summary.get("commitment_verified_count") == expected_commitment_count,
            summary.get("persisted_secret_value_count") == 0,
            summary.get("next_phase") == "prove-identity-rollback-cycle-before-genesis",
            summary.get("genesis_blocked_pending_identity_rollback_cycle") is True,
        ]
    ):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID",
            (
                "identity execution is incomplete or does not prove exactly "
                f"{expected_commitment_count} commitments for {len(actual_nodes)} selected node(s)"
            ),
        )
    receipts = execution.get("mutation_receipts")
    if type(receipts) is not list or len(receipts) != expected_commitment_count:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID",
            (
                "identity execution must contain exactly "
                f"{expected_commitment_count} mutation receipts for {len(actual_nodes)} selected node(s)"
            ),
        )
    node_bindings: dict[str, dict[str, Any]] = {
        node: {"service_uuid": None, "controller_id": None, "commitments": {}}
        for node in actual_nodes
    }
    for index, raw_receipt in enumerate(receipts):
        receipt = _mapping(raw_receipt, f"mutation_receipts[{index}]")
        node = _identifier(receipt.get("node"), f"mutation_receipts[{index}].node")
        if node not in node_bindings:
            raise MotherDeploymentGenesisError(
                "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID",
                "identity receipt names an unexpected node",
            )
        env_key = _identifier(receipt.get("environment_key"), f"mutation_receipts[{index}].environment_key")
        if env_key not in expected_keys:
            raise MotherDeploymentGenesisError(
                "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID",
                f"unexpected installed environment key: {env_key}",
            )
        endpoint = receipt.get("endpoint")
        match = _SERVICE_ENV_ENDPOINT_RE.fullmatch(str(endpoint or ""))
        if match is None:
            raise MotherDeploymentGenesisError(
                "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID",
                "identity receipt does not bind one exact Coolify service",
            )
        postcondition = receipt.get("postcondition")
        if not isinstance(postcondition, Mapping) or not all(
            [
                receipt.get("status") == "succeeded",
                receipt.get("live_write_acknowledged") is True,
                postcondition.get("commitment_verified") is True,
                postcondition.get("key_unique") is True,
                postcondition.get("proof_mode") == "readback-value-sha256",
            ]
        ):
            raise MotherDeploymentGenesisError(
                "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID",
                "identity receipt lacks a successful readback commitment proof",
            )
        service_uuid = _identifier(match.group(1), f"mutation_receipts[{index}].service_uuid")
        controller_id = _identifier(receipt.get("controller_id"), f"mutation_receipts[{index}].controller_id")
        binding = node_bindings[node]
        if binding["service_uuid"] not in (None, service_uuid) or binding["controller_id"] not in (None, controller_id):
            raise MotherDeploymentGenesisError(
                "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID",
                "identity receipts disagree about the node service binding",
            )
        if env_key in binding["commitments"]:
            raise MotherDeploymentGenesisError(
                "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID",
                "identity execution repeats an environment-key receipt",
            )
        binding["service_uuid"] = service_uuid
        binding["controller_id"] = controller_id
        binding["commitments"][env_key] = {
            "value_sha256": _sha256(receipt.get("value_sha256"), f"mutation_receipts[{index}].value_sha256"),
            "environment_variable_uuid": _identifier(
                receipt.get("environment_variable_uuid"),
                f"mutation_receipts[{index}].environment_variable_uuid",
            ),
            "source_ref": _identifier(
                receipt.get("source_ref"),
                f"mutation_receipts[{index}].source_ref",
            ),
        }
    for node, binding in node_bindings.items():
        if set(binding["commitments"]) != expected_keys or not binding["service_uuid"] or not binding["controller_id"]:
            raise MotherDeploymentGenesisError(
                "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID",
                f"identity execution does not prove both reserved identities for {node}",
            )
    if _contains_sensitive_field(execution) or _contains_private_key_literal(execution):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID",
            "identity execution contains persisted secret material",
        )
    return execution, byte_sha256, actual_nodes, node_bindings


def _genesis_policy(
    document: Mapping[str, Any],
    *,
    network: str,
    initial_validator_address: str,
) -> tuple[dict[str, Any], list[str]]:
    networks = _mapping(document.get("networks"), "networks")
    network_state = _mapping(networks.get(network), f"networks.{network}")
    chain_id = _positive_chain_id(network_state.get("chain_id"))
    descriptor = _mapping(network_state.get("genesis"), f"networks.{network}.genesis")
    if descriptor.get("source") != "mother-private" or descriptor.get("first_topology_mode") != "initial":
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_POLICY_INVALID",
            "Mother first-genesis descriptor is missing or not initial",
        )
    qbft = _mapping(descriptor.get("qbft"), f"networks.{network}.genesis.qbft")
    block_period = qbft.get("blockperiodseconds")
    epoch_length = qbft.get("epochlength")
    if type(block_period) is not int or block_period <= 0 or type(epoch_length) is not int or epoch_length <= 0:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_POLICY_INVALID",
            "Mother QBFT genesis settings must be positive integers",
        )
    raw_alloc = descriptor.get("alloc_accounts")
    if type(raw_alloc) is not list or not raw_alloc:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_POLICY_INVALID",
            "Mother first genesis must declare at least one allocation reference",
        )
    alloc_addresses: list[str] = []
    alloc: dict[str, dict[str, str]] = {}
    for index, raw_item in enumerate(raw_alloc):
        item = _mapping(raw_item, f"networks.{network}.genesis.alloc_accounts[{index}]")
        ref = item.get("ref")
        identity = _mapping(
            _resolve_dotted(document, ref, path=f"networks.{network}.genesis.alloc_accounts[{index}].ref"),
            str(ref),
        )
        address = _address(identity.get("address"), f"{ref}.address")
        if address not in alloc_addresses:
            alloc_addresses.append(address)
            alloc[address[2:]] = {"balance": _DEFAULT_FUNDED_ACCOUNT_BALANCE}
    genesis = {
        "config": {
            "chainId": chain_id,
            "berlinBlock": 0,
            "londonBlock": 0,
            "shanghaiTime": _DEFAULT_SHANGHAI_TIME,
            "qbft": {
                "blockperiodseconds": block_period,
                "epochlength": epoch_length,
                "requesttimeoutseconds": _DEFAULT_REQUEST_TIMEOUT_SECONDS,
            },
        },
        "nonce": "0x0",
        "timestamp": "0x58ee40ba",
        "gasLimit": "0x47b760",
        "difficulty": "0x1",
        "baseFeePerGas": _DEFAULT_GENESIS_BASE_FEE_PER_GAS,
        "mixHash": "0x63746963616c2062797a616e74696e65206661756c7420746f6c6572616e6365",
        "coinbase": "0x0000000000000000000000000000000000000000",
        "extraData": qbft_genesis_extra_data([initial_validator_address]),
        "alloc": alloc,
    }
    return genesis, alloc_addresses


def build_deployment_genesis_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    identity_execution_path: Path,
    *,
    identity_rollback_verification_path: Path,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    created_at: str | None = None,
) -> dict[str, Any]:
    network = _identifier(network, "network")
    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    execution, execution_byte_sha256, actual_nodes, live_bindings = _identity_execution(
        paths,
        private_state,
        identity_execution_path,
        network=network,
        selected_nodes=requested_nodes,
    )
    nodes = requested_nodes or actual_nodes
    profile_sha256 = _sha256(
        execution.get("identity_profile_sha256"),
        "identity execution profile SHA-256",
    )
    rollback_cycle = verify_identity_rollback_cycle_evidence(
        paths,
        private_state,
        identity_rollback_verification_path,
        identity_profile_sha256_value=profile_sha256,
        network=network,
        nodes=nodes,
        before_execution_started_at=execution.get("started_at"),
        current_execution_sha256=execution_byte_sha256,
    )
    plan = build_starter_deployment_plan(
        private_state,
        network=network,
        selected_nodes=nodes,
    )
    sequence = plan.get("sequence")
    if type(sequence) is not list or len(sequence) != len(nodes):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_PLAN_INVALID",
            "starter deployment plan does not match the identity execution node set",
        )
    initial_steps = [item for item in sequence if isinstance(item, Mapping) and item.get("mode") == "initial"]
    if len(initial_steps) != 1:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_PLAN_INVALID",
            "starter sequence must contain exactly one initial node",
        )
    if any(not isinstance(item, Mapping) or item.get("mode") not in {"initial", "soft"} for item in sequence):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_PLAN_INVALID",
            "first-genesis compiler supports only the committed initial-to-soft starter sequence",
        )
    document = _document(private_state)
    network_state = _mapping(_mapping(document.get("networks"), "networks").get(network), f"networks.{network}")
    validators = _mapping(network_state.get("validators"), f"networks.{network}.validators")
    reservations = _mapping(network_state.get("nodes"), f"networks.{network}.nodes")

    node_addresses: dict[str, str] = {}
    for node in nodes:
        reservation = _mapping(reservations.get(node), f"networks.{network}.nodes.{node}")
        expected_ref = f"networks.{network}.validators.{node}"
        if reservation.get("validator_ref") != expected_ref:
            raise MotherDeploymentGenesisError(
                "MOTHER_DEPLOY_GENESIS_IDENTITY_INVALID",
                f"{node} does not reference its canonical reserved validator",
            )
        validator = _mapping(validators.get(node), expected_ref)
        node_addresses[node] = _address(validator.get("address"), f"{expected_ref}.address")

    initial_node = _identifier(initial_steps[0].get("node"), "initial node")
    genesis, alloc_addresses = _genesis_policy(
        document,
        network=network,
        initial_validator_address=node_addresses[initial_node],
    )
    genesis_bytes = canonical_json(genesis)
    genesis_sha256 = hashlib.sha256(genesis_bytes).hexdigest()

    service_targets: list[dict[str, Any]] = []
    admissions: list[dict[str, Any]] = []
    desired_validator_set: list[str] = [node_addresses[initial_node]]
    for item in sequence:
        node = _identifier(item.get("node"), "sequence node")
        mode = _identifier(item.get("mode"), f"sequence.{node}.mode")
        live = live_bindings[node]
        target = {
            "node": node,
            "mode": mode,
            "controller_id": live["controller_id"],
            "service_uuid": live["service_uuid"],
            "validator_address": node_addresses[node],
            "genesis_sha256": genesis_sha256,
            "identity_commitments": live["commitments"],
            "service_start_authorized": False,
            "validator_activation_authorized": False,
        }
        if mode == "initial":
            target.update(
                {
                    "phase": "install-mother-owned-first-genesis",
                    "role": "initial-validator",
                    "expected_genesis_validator_set": [node_addresses[node]],
                }
            )
        else:
            current = list(desired_validator_set)
            desired_validator_set.append(node_addresses[node])
            admission = {
                "node": node,
                "mode": "soft",
                "validator_address": node_addresses[node],
                "current_validator_set": current,
                "desired_validator_set": list(desired_validator_set),
                "requires_initial_chain_proof": True,
                "live_vote_authorized": False,
            }
            admissions.append(admission)
            target.update(
                {
                    "phase": "admit-replica-after-birth",
                    "role": "prospective-validator",
                    "admission": admission,
                }
            )
        service_targets.append(target)

    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": _utc_timestamp(created_at, "created_at"),
        "network": network,
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "mother_binding": _binding(private_state),
        "identity_execution": {
            "locator": _relative_locator(paths, identity_execution_path, label="identity execution"),
            "sha256": hashlib.sha256(canonical_json(execution)).hexdigest(),
            "byte_sha256": execution_byte_sha256,
            "started_at": execution.get("started_at"),
            "completed_at": execution.get("completed_at"),
            "identity_profile_sha256": profile_sha256,
        },
        "identity_rollback_cycle": {
            "locator": _relative_locator(
                paths,
                identity_rollback_verification_path,
                label="identity rollback verification",
            ),
            "sha256": rollback_cycle["verification_sha256"],
            "identity_rollback_verification_sha256": rollback_cycle[
                "identity_rollback_verification_sha256"
            ],
            "identity_profile_sha256": profile_sha256,
            "verified_absent_at": rollback_cycle["observed_at"],
            "reapplied_after_verified_rollback": True,
        },
        "authority": {
            "transaction_apply_authorized": False,
            "live_execution_authorized": False,
            "operator_release_required": True,
        },
        "policy": {
            "compiler": "mother-native-qbft-first-genesis-v1",
            "legacy_allfather_executor_invoked": False,
            "legacy_qbft_executor_invoked": False,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "service_deploy_or_start_performed": False,
            "validator_activation_performed": False,
            "private_state_updated": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "identity_rollback_cycle_proven": True,
            "identity_reapplication_proven_after_rollback": True,
        },
        "staged_scope": "compile-first-genesis-and-replica-admission",
        "genesis": {
            "format": "besu-qbft-genesis-json",
            "chain_id": genesis["config"]["chainId"],
            "initial_node": initial_node,
            "initial_validator_address": node_addresses[initial_node],
            "validator_set": [node_addresses[initial_node]],
            "alloc_addresses": alloc_addresses,
            "canonical_json": genesis,
            "canonical_json_sha256": genesis_sha256,
            "canonical_json_bytes": len(genesis_bytes),
        },
        "service_targets": service_targets,
        "replica_admissions": admissions,
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_GENESIS_RELEASE_REQUIRED",
                "message": "an explicit expiring operator release is required for this exact genesis transaction",
            },
            {
                "code": "MOTHER_DEPLOY_GENESIS_EXECUTOR_NOT_IMPLEMENTED",
                "message": "the genesis/service configuration executor is not implemented in this patch",
            },
        ],
        "summary": {
            "transaction_valid": True,
            "apply_ready": False,
            "target_count": len(service_targets),
            "genesis_count": 1,
            "initial_validator_count": 1,
            "replica_admission_count": len(admissions),
            "identity_commitment_count": sum(len(item["identity_commitments"]) for item in service_targets),
            "identity_rollback_cycle_proven": True,
            "identity_reapplication_proven_after_rollback": True,
            "persisted_secret_value_count": 0,
            "next_phase_after_apply": "activate-initial-validator-and-prove-network-birth",
            "blocker_codes": [
                "MOTHER_DEPLOY_GENESIS_EXECUTOR_NOT_IMPLEMENTED",
                "MOTHER_DEPLOY_GENESIS_RELEASE_REQUIRED",
            ],
        },
    }
    if _contains_sensitive_field(transaction):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            "genesis transaction contains sensitive material",
        )
    transaction["genesis_transaction_sha256"] = _digest_without(
        transaction,
        "genesis_transaction_sha256",
    )
    return transaction


def _transaction_root(paths: PrivateStatePaths) -> Path:
    return paths.root / _TRANSACTION_DIRECTORY[0] / _TRANSACTION_DIRECTORY[1]


def write_deployment_genesis_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    document = dict(transaction)
    digest = _digest_without(document, "genesis_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("genesis_transaction_sha256") != digest
        or _contains_sensitive_field(document)
    ):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            "genesis transaction is malformed, unbound, or sensitive",
        )
    payload = canonical_json(document)
    current = paths.root
    for part in _TRANSACTION_DIRECTORY:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "genesis"
    network = _identifier(document.get("network"), "network")
    destination = _transaction_root(paths) / f"{stamp}-{network}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentGenesisError(
                "MOTHER_DEPLOY_GENESIS_TRANSACTION_CONFLICT",
                "genesis transaction destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_WRITE_FAILED",
            "genesis transaction reread mismatch",
        )
    return destination, digest


def verify_deployment_genesis_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
) -> dict[str, Any]:
    root = _transaction_root(paths).resolve(strict=False)
    candidate = Path(transaction_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_PATH_UNSAFE",
            "genesis transaction must be beneath the canonical transaction root",
        ) from exc
    transaction, _raw, byte_sha256 = _canonical_file(candidate, label="genesis transaction")
    digest = _digest_without(transaction, "genesis_transaction_sha256")
    if (
        transaction.get("kind") != _TRANSACTION_KIND
        or transaction.get("genesis_transaction_sha256") != digest
        or _contains_sensitive_field(transaction)
    ):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            "genesis transaction is modified, unbound, or sensitive",
        )
    if transaction.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_STALE_BINDING",
            "genesis transaction does not bind the current Mother generation",
        )
    execution_binding = transaction.get("identity_execution")
    if not isinstance(execution_binding, Mapping):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID",
            "identity execution binding is missing",
        )
    execution_path = _resolve_locator(paths, execution_binding.get("locator"), label="identity execution")
    rollback_binding = transaction.get("identity_rollback_cycle")
    if not isinstance(rollback_binding, Mapping):
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_IDENTITY_ROLLBACK_REQUIRED",
            "identity rollback-cycle binding is missing",
        )
    rollback_verification_path = _resolve_locator(
        paths,
        rollback_binding.get("locator"),
        label="identity rollback verification",
    )
    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    actual_nodes = tuple(_identifier(item.get("node"), "service target node") for item in transaction.get("service_targets", []))
    if requested_nodes and requested_nodes != actual_nodes:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_SELECTION_MISMATCH",
            "genesis transaction does not cover the requested node sequence",
        )
    rebuilt = build_deployment_genesis_transaction(
        paths,
        private_state,
        execution_path,
        identity_rollback_verification_path=rollback_verification_path,
        network=transaction.get("network", "mainnet"),
        selected_nodes=actual_nodes,
        created_at=transaction.get("created_at"),
    )
    if rebuilt != transaction:
        raise MotherDeploymentGenesisError(
            "MOTHER_DEPLOY_GENESIS_TRANSACTION_MISMATCH",
            "genesis transaction no longer matches Mother state and identity evidence",
        )
    genesis = transaction["genesis"]
    return {
        "clean": True,
        "transaction_path": str(candidate),
        "genesis_transaction_sha256": digest,
        "byte_sha256": byte_sha256,
        "mother_binding": _binding(private_state),
        "network": transaction["network"],
        "nodes": list(actual_nodes),
        "staged_scope": transaction["staged_scope"],
        "genesis_sha256": genesis["canonical_json_sha256"],
        "chain_id": genesis["chain_id"],
        "initial_node": genesis["initial_node"],
        "initial_validator_count": transaction["summary"]["initial_validator_count"],
        "replica_admission_count": transaction["summary"]["replica_admission_count"],
        "identity_commitment_count": transaction["summary"]["identity_commitment_count"],
        "persisted_secret_value_count": 0,
        "transaction_apply_authorized": False,
        "live_execution_authorized": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
    }


__all__ = [
    "MotherDeploymentGenesisError",
    "build_deployment_genesis_transaction",
    "qbft_genesis_extra_data",
    "verify_deployment_genesis_transaction",
    "write_deployment_genesis_transaction",
]
