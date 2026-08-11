"""Prep-only Mother node-removal transaction builder.

This module implements the documented ``remove-node prep`` contract without
performing any live mutation.  It consumes a canonical baseline evidence artifact
that already binds the current topology, freezes an explicit target node, derives
survivors from that baseline, and records the ordered removal plan that a later
``do`` phase must execute.

It intentionally does not withdraw routing, vote a validator out, delete a
service, call Coolify, or touch private keys.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from . import atomic_files
from .canonical import canonical_json
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_TRANSACTION_KIND = "main_computer.mother.deployment_node_remove_prep_transaction.v1"
_TRANSACTION_DIRECTORY = ("actions", "deployment-node-remove-prep-transactions")
_T3_BASELINE_KIND = "main_computer.mother.deployment_t3_post_admission_steady_state_evidence.v1"
_SUPPORTED_BASELINE_KINDS = frozenset({_T3_BASELINE_KIND})
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MotherDeploymentNodeRemovePrepError(RuntimeError):
    """Node-removal prep failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentNodeRemovePrepError:
    return MotherDeploymentNodeRemovePrepError(code, message)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_INVALID", f"{label} is missing")
    if not _IDENTIFIER_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_INVALID", f"{label} is not a valid node name")
    return value


def _address(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ADDRESS_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_INVALID", f"{label} is not a validator address")
    return value.lower()


def _sha256(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if not _SHA256_RE.fullmatch(text):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_INVALID", f"{label} is not a SHA-256 digest")
    return text


def _timestamp(value: str | None = None) -> str:
    if value is not None:
        parsed = _parse_utc(value, "created_at")
        return parsed.isoformat().replace("+00:00", "Z")
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TIME_INVALID", f"{label} is missing")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any, *, now: datetime | None = None) -> int:
    observed = _parse_utc(value, "baseline completed_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - observed).total_seconds())
    if age < -60:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TIME_INVALID", "baseline evidence timestamp is in the future")
    return max(age, 0)


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _canonical_file(path: Path) -> tuple[dict[str, Any], bytes, str]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_PATH_INVALID", f"cannot read {path}") from exc
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_JSON_INVALID", f"{path} is not valid JSON") from exc
    if not isinstance(document, dict):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_JSON_INVALID", f"{path} is not a JSON object")
    canonical = canonical_json(document)
    return document, canonical, hashlib.sha256(canonical).hexdigest()


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_PATH_INVALID", f"{label} is outside the runtime state root") from exc


def _resolve_under(paths: PrivateStatePaths, locator: Any, directory: tuple[str, ...], *, label: str) -> Path:
    if not isinstance(locator, str) or not locator:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_PATH_INVALID", f"{label} locator is missing")
    candidate = (paths.root / Path(locator)).resolve(strict=False)
    allowed = (paths.root / Path(*directory)).resolve(strict=False)
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_PATH_INVALID", f"{label} is outside its directory") from exc
    return candidate


def _ensure_directory(paths: PrivateStatePaths, parts: tuple[str, ...], *, operation: OperationIdentity) -> Path:
    current = paths.root
    current.mkdir(parents=True, exist_ok=True)
    for part in parts:
        current = current / part
    current.mkdir(parents=True, exist_ok=True)
    _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    body = {key: value for key, value in document.items() if key != field}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def _contains_sensitive(value: Any) -> bool:
    sensitive_markers = (
        "private_key",
        "private-key",
        "password",
        "bearer ",
        "api_token",
        "api-token",
    )
    if isinstance(value, str):
        text = value.lower()
        return any(marker in text for marker in sensitive_markers)
    if isinstance(value, Mapping):
        return any(_contains_sensitive(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive(item) for item in value)
    return False


def _dedupe(values: list[str], label: str) -> None:
    if len(set(values)) != len(values):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_INVALID", f"{label} contains duplicates")


def _latest_service_records(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    observations = document.get("service_observations")
    if not isinstance(observations, list):
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
            "baseline evidence does not contain service observations",
        )
    latest: dict[str, dict[str, Any]] = {}
    for item in observations:
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        if not isinstance(node, str):
            continue
        observed_at = item.get("observed_at")
        previous = latest.get(node)
        if previous is None or str(observed_at or "") >= str(previous.get("observed_at") or ""):
            latest[node] = dict(item)
    return latest


def _load_baseline(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    baseline_evidence_path: Path,
    *,
    network: str,
    expected_sha256: str,
    max_age_seconds: int,
    now: datetime | None = None,
) -> tuple[dict[str, Any], str, int, dict[str, dict[str, Any]]]:
    resolved = Path(baseline_evidence_path).resolve(strict=False)
    document, _payload, digest = _canonical_file(resolved)
    expected = _sha256(expected_sha256, "baseline evidence SHA-256")
    if digest != expected:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_MISMATCH", "baseline evidence SHA-256 mismatch")
    if document.get("kind") not in _SUPPORTED_BASELINE_KINDS:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_UNSUPPORTED", "baseline evidence kind is not supported for node removal prep")
    if document.get("schema_version") != 1 or document.get("network") != network:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "baseline evidence schema or network is invalid")
    if document.get("mother_binding") != _binding(private_state):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_CHANGED", "current Mother private-state binding no longer matches baseline evidence")
    if document.get("status") != "pass":
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "baseline evidence did not pass")

    completed_at = document.get("completed_at")
    age = _age_seconds(completed_at, now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_STALE", "baseline evidence is outside the freshness window")

    summary = document.get("summary")
    policy = document.get("policy")
    if not isinstance(summary, Mapping) or not isinstance(policy, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", "baseline evidence summary or policy is missing")
    required_truths = (
        summary.get("clean") is True,
        summary.get("final_validator_set_bound") is True,
        summary.get("live_mutation_performed") is False,
        summary.get("mutation_count") == 0,
        summary.get("validator_vote_performed") is False,
        summary.get("validator_activation_performed") is False,
        summary.get("routing_or_topology_published") is False,
        summary.get("public_endpoint_created") is False,
        policy.get("read_only") is True,
        policy.get("live_mutation_performed") is False,
        policy.get("routing_or_topology_published") is False,
        policy.get("public_http_endpoint_created") is False,
        document.get("routing_or_topology_published") is not True,
        document.get("public_endpoint_created") is not True,
        document.get("live_mutation_performed") is not True,
    )
    if not all(required_truths):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "baseline evidence is not a clean pre-removal topology proof")

    nodes_raw = document.get("nodes")
    validators_raw = document.get("validator_set")
    if not isinstance(nodes_raw, list) or not isinstance(validators_raw, list):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", "baseline nodes or validator set is missing")
    nodes = [_identifier(item, "baseline node") for item in nodes_raw]
    validators = [_address(item, "baseline validator address") for item in validators_raw]
    _dedupe(nodes, "baseline nodes")
    _dedupe(validators, "baseline validator set")
    if len(nodes) != len(validators) or len(nodes) < 2:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "baseline topology must contain matching nodes and validators")
    if int(document.get("validator_count", len(validators))) != len(validators):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "baseline validator count is inconsistent")

    services = _latest_service_records(document)
    missing = [node for node in nodes if node not in services]
    if missing:
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
            f"baseline evidence is missing latest service observations for: {', '.join(missing)}",
        )
    return document, digest, age, services


def _service_record(node: str, record: Mapping[str, Any]) -> dict[str, Any]:
    service_uuid = record.get("service_uuid")
    controller_id = record.get("controller_id")
    if not isinstance(service_uuid, str) or not service_uuid:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", f"{node} service UUID is missing")
    if not isinstance(controller_id, str) or not controller_id:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", f"{node} controller id is missing")
    return {
        "node": node,
        "controller_id": controller_id,
        "service_uuid": service_uuid,
        "service_status": record.get("service_status"),
        "readiness_source": record.get("proof_source"),
        "last_observed_at": record.get("observed_at"),
    }


def build_node_remove_prep_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    baseline_evidence_path: Path,
    *,
    network: str = "mainnet",
    target_node: str,
    mode: str = "soft",
    baseline_evidence_sha256: str,
    baseline_max_age_seconds: int = 86400,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if network != "mainnet":
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_NETWORK_INVALID", "node removal prep is currently restricted to mainnet")
    target = _identifier(target_node, "target node")
    if mode != "soft":
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_MODE_INVALID", "only soft node-removal prep is implemented")
    baseline, baseline_sha, baseline_age, services = _load_baseline(
        paths,
        private_state,
        Path(baseline_evidence_path),
        network=network,
        expected_sha256=baseline_evidence_sha256,
        max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    nodes = [_identifier(item, "baseline node") for item in baseline["nodes"]]
    validators = [_address(item, "baseline validator address") for item in baseline["validator_set"]]
    if target not in nodes:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TARGET_INVALID", "target node is not present in baseline topology")
    target_index = nodes.index(target)
    target_validator = validators[target_index]
    survivors = [node for node in nodes if node != target]
    post_validators = [validator for index, validator in enumerate(validators) if index != target_index]
    if not survivors or not post_validators:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TARGET_INVALID", "node removal would leave no surviving validator")

    service_topology = {node: _service_record(node, services[node]) for node in nodes}
    created_text = _timestamp(created_at)
    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": mode,
        "target": {
            "node": target,
            "validator_address": target_validator,
            "controller_id": service_topology[target]["controller_id"],
            "service_uuid": service_topology[target]["service_uuid"],
        },
        "survivors": [
            {
                "node": node,
                "validator_address": post_validators[index],
                "controller_id": service_topology[node]["controller_id"],
                "service_uuid": service_topology[node]["service_uuid"],
            }
            for index, node in enumerate(survivors)
        ],
        "source_baseline_evidence": {
            "kind": baseline.get("kind"),
            "locator": _relative(paths, Path(baseline_evidence_path), label="baseline evidence"),
            "sha256": baseline_sha,
            "completed_at": baseline.get("completed_at"),
            "age_seconds": baseline_age,
            "next_phase": baseline.get("next_phase"),
        },
        "current_topology": {
            "nodes": nodes,
            "validator_set": validators,
            "validator_count": len(validators),
            "services": service_topology,
            "chain_id": baseline.get("chain_id"),
            "genesis_sha256": baseline.get("genesis_sha256"),
        },
        "post_removal_topology": {
            "nodes": survivors,
            "validator_set": post_validators,
            "validator_count": len(post_validators),
            "removed_node": target,
            "removed_validator_address": target_validator,
        },
        "ordered_removal_plan": [
            {
                "ordinal": 1,
                "phase": "withdraw-hub-fdb-topology",
                "description": "withdraw the target from Hub/FDB topology before service deletion",
                "required_before_service_deletion": True,
            },
            {
                "ordinal": 2,
                "phase": "withdraw-rpc-routing",
                "description": "withdraw the target from RPC routing before service deletion",
                "required_before_service_deletion": True,
            },
            {
                "ordinal": 3,
                "phase": "remove-qbft-validator",
                "description": "remove the target validator from the QBFT validator set before service deletion",
                "required_before_service_deletion": True,
            },
            {
                "ordinal": 4,
                "phase": "detach-disable-archive-or-delete-service",
                "description": "detach, disable, archive, or delete exactly the prepared target service",
                "required_before_service_deletion": False,
            },
            {
                "ordinal": 5,
                "phase": "verify-surviving-network",
                "description": "verify the surviving topology and validator set after removal",
                "required_before_service_deletion": False,
            },
        ],
        "execution_plan": {
            "kind": "mother-remove-node-prep-only",
            "prep_mutation_count": 0,
            "do_mutation_required": True,
            "service_deletion_is_first": False,
            "routing_topology_withdrawal_required_before_service_deletion": True,
            "rpc_withdrawal_required_before_service_deletion": True,
            "qbft_validator_removal_required_before_service_deletion": True,
            "allowed_next_command": f"remove-node do {network}",
        },
        "policy": {
            "compiler": "mother-native-remove-node-prep-v1",
            "read_only_preparation": True,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "mutation_count": 0,
            "service_deletion_performed": False,
            "service_deletion_is_first": False,
            "routing_or_topology_published": False,
            "public_http_endpoint_created": False,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "manual_ssh_required": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "authority": {
            "live_execution_authorized": False,
            "service_deletion_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "operator_release_required_for_do": True,
        },
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_NODE_REMOVE_DO_REQUIRED",
                "message": "prep only records the exact removal plan; do/finalize are not executed by this transaction",
            }
        ],
    }
    transaction["summary"] = {
        "transaction_valid": True,
        "target_node": target,
        "target_validator_address": target_validator,
        "survivor_nodes": survivors,
        "current_validator_count": len(validators),
        "post_removal_validator_count": len(post_validators),
        "routing_topology_withdrawal_required_before_service_deletion": True,
        "service_deletion_is_first": False,
        "prep_mutation_count": 0,
        "live_mutation_performed": False,
        "service_deletion_performed": False,
        "next_phase": f"remove-node-do-{network}",
    }
    transaction["node_remove_prep_transaction_sha256"] = _digest_without(
        transaction,
        "node_remove_prep_transaction_sha256",
    )
    if _contains_sensitive(transaction):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_SENSITIVE", "node-removal prep transaction contains sensitive material")
    return transaction


def write_node_remove_prep_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(transaction)
    if document.get("kind") != _TRANSACTION_KIND or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_INVALID", "node-removal prep transaction is malformed or sensitive")
    digest = _digest_without(document, "node_remove_prep_transaction_sha256")
    if document.get("node_remove_prep_transaction_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_INVALID", "node-removal prep transaction digest mismatch")
    payload = canonical_json(document)
    root = _ensure_directory(paths, _TRANSACTION_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "noderemoveprep"
    network = str(document.get("network") or "network")
    target = str(document.get("target", {}).get("node") or "node")
    destination = root / f"{stamp}-{network}-{target}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_CONFLICT", "transaction destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_node_remove_prep_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = Path(transaction_path).resolve(strict=False)
    allowed = (paths.root / Path(*_TRANSACTION_DIRECTORY)).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_PATH_INVALID", "node-removal prep transaction is outside its directory") from exc
    document, _payload, _file_sha = _canonical_file(resolved)
    digest = _digest_without(document, "node_remove_prep_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("mother_binding") != _binding(private_state)
        or document.get("node_remove_prep_transaction_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_INVALID", "node-removal prep transaction is invalid")
    age = _age_seconds(document.get("created_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_STALE", "node-removal prep transaction is outside the freshness window")
    source = document.get("source_baseline_evidence")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_INVALID", "baseline evidence binding is missing")
    baseline_path = _resolve_under(
        paths,
        source.get("locator"),
        ("evidence", "deployment-t3-post-admission-steady-state"),
        label="baseline evidence",
    )
    baseline, baseline_sha, _baseline_age, _services = _load_baseline(
        paths,
        private_state,
        baseline_path,
        network=document.get("network"),
        expected_sha256=source.get("sha256"),
        max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if baseline_sha != source.get("sha256"):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_INVALID", "baseline evidence digest changed")
    current = document.get("current_topology")
    post = document.get("post_removal_topology")
    target = document.get("target")
    if not isinstance(current, Mapping) or not isinstance(post, Mapping) or not isinstance(target, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_INVALID", "node-removal prep topology is incomplete")
    if list(current.get("nodes", [])) != list(baseline.get("nodes", [])) or list(current.get("validator_set", [])) != [_address(item, "baseline validator address") for item in baseline.get("validator_set", [])]:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_INVALID", "current topology no longer matches baseline evidence")
    if target.get("node") in post.get("nodes", []):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_INVALID", "target node is still present in post-removal topology")
    if target.get("validator_address") in post.get("validator_set", []):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_INVALID", "target validator is still present in post-removal topology")
    if document.get("execution_plan", {}).get("service_deletion_is_first") is not False:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_INVALID", "service deletion must not be first")
    return {
        "clean": True,
        "transaction_path": str(resolved),
        "node_remove_prep_transaction_sha256": digest,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "target_node": target["node"],
        "target_validator_address": target["validator_address"],
        "survivor_nodes": list(post["nodes"]),
        "current_validator_set": list(current["validator_set"]),
        "post_removal_validator_set": list(post["validator_set"]),
        "routing_topology_withdrawal_required_before_service_deletion": True,
        "service_deletion_is_first": False,
        "live_mutation_performed": False,
        "service_deletion_performed": False,
        "source_baseline_evidence_sha256": baseline_sha,
        "next_phase": document["summary"]["next_phase"],
    }
