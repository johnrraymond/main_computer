"""Generic Mother add-node prep transaction builder.

This module implements the documented ``add-node prep`` contract without
performing live mutation. It consumes canonical topology evidence, derives a
topology diff for one explicit absent target node, and records the ordered
add-node plan a later ``do`` phase must execute.

The golden test path is operator-directed add/delete evidence. The
implementation does not know a fixed fixture topology: sample node names may
appear in tests or operator input, but the builder works from identity/history
evidence, fresh topology evidence when supplied, private-state node metadata,
and an explicit operator-selected target.
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


_TRANSACTION_KIND = "main_computer.mother.deployment_node_add_prep_transaction.v1"
_TRANSACTION_DIRECTORY = ("actions", "deployment-node-add-prep-transactions")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NODE_ORDER_RE = re.compile(r"^mainnet([a-z]+)-super([0-9]+)$")


class MotherDeploymentNodeAddPrepError(RuntimeError):
    """Node-add prep failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentNodeAddPrepError:
    return MotherDeploymentNodeAddPrepError(code, message)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_INVALID", f"{label} is missing")
    if not _IDENTIFIER_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_INVALID", f"{label} is not a valid identifier")
    return value


def _address(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ADDRESS_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_INVALID", f"{label} is not a validator address")
    return value.lower()


def _sha256(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if not _SHA256_RE.fullmatch(text):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_INVALID", f"{label} is not a SHA-256 digest")
    return text


def _timestamp(value: str | None = None) -> str:
    if value is not None:
        parsed = _parse_utc(value, "created_at")
        return parsed.isoformat().replace("+00:00", "Z")
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TIME_INVALID", f"{label} is missing")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any, *, now: datetime | None = None) -> int:
    observed = _parse_utc(value, "baseline completed_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - observed).total_seconds())
    if age < -60:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TIME_INVALID", "baseline evidence timestamp is in the future")
    return max(age, 0)


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _private_document(private_state: PrivateStateReadResult) -> Mapping[str, Any]:
    try:
        value = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_PRIVATE_STATE_INVALID", "private state is not canonical JSON") from exc
    if not isinstance(value, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_PRIVATE_STATE_INVALID", "private state root is not a JSON object")
    return value


def _private_network(private_state: PrivateStateReadResult, network: str) -> Mapping[str, Any]:
    document = _private_document(private_state)
    networks = document.get("networks")
    if not isinstance(networks, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_PRIVATE_STATE_INVALID", "private state networks are missing")
    item = networks.get(network)
    if not isinstance(item, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_PRIVATE_STATE_INVALID", f"private state network is missing: {network}")
    return item


def _canonical_file(path: Path) -> tuple[dict[str, Any], bytes, str]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_PATH_INVALID", f"cannot read {path}") from exc
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_JSON_INVALID", f"{path} is not valid JSON") from exc
    if not isinstance(document, dict):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_JSON_INVALID", f"{path} is not a JSON object")
    canonical = canonical_json(document)
    return document, canonical, hashlib.sha256(canonical).hexdigest()


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_PATH_INVALID", f"{label} is outside the runtime state root") from exc


def _resolve_under_evidence(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if not isinstance(locator, str) or not locator:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_PATH_INVALID", f"{label} locator is missing")
    candidate = (paths.root / Path(locator)).resolve(strict=False)
    allowed = (paths.root / "evidence").resolve(strict=False)
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_PATH_INVALID", f"{label} is outside evidence") from exc
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
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_INVALID", f"{label} contains duplicates")


def _topology_sort_key(node: str) -> tuple[int, str, int, str]:
    match = _NODE_ORDER_RE.fullmatch(node)
    if match is None:
        return (1, node, 0, node)
    host_key, ordinal_text = match.groups()
    return (0, host_key, int(ordinal_text), node)


def _sorted_topology_nodes(nodes: list[str]) -> list[str]:
    return sorted(nodes, key=_topology_sort_key)


def _chain_identity(document: Mapping[str, Any]) -> tuple[Any, Any]:
    chain_id = document.get("chain_id")
    genesis_sha256 = document.get("genesis_sha256")
    if chain_id is None or genesis_sha256 is None:
        for key in ("rollback_baseline_topology", "final_topology", "current_topology", "post_add_topology", "post_removal_topology", "pre_removal_topology"):
            candidate = document.get(key)
            if isinstance(candidate, Mapping):
                chain_id = chain_id if chain_id is not None else candidate.get("chain_id")
                genesis_sha256 = genesis_sha256 if genesis_sha256 is not None else candidate.get("genesis_sha256")
    if chain_id is None or genesis_sha256 is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_BASELINE_INCOMPLETE", "baseline chain identity is missing")
    return chain_id, genesis_sha256


def _service_record_from_observation(item: Mapping[str, Any]) -> dict[str, Any] | None:
    node = item.get("node")
    service_uuid = item.get("service_uuid")
    controller_id = item.get("controller_id")
    if not isinstance(node, str) or not isinstance(service_uuid, str) or not service_uuid or not isinstance(controller_id, str) or not controller_id:
        return None
    return {
        "node": _identifier(node, "service observation node"),
        "controller_id": _identifier(controller_id, "service observation controller id"),
        "service_uuid": service_uuid,
        "service_status": item.get("service_status"),
        "readiness_source": item.get("proof_source") or item.get("readiness_source"),
        "last_observed_at": item.get("observed_at"),
    }


def _service_records(document: Mapping[str, Any], nodes: list[str]) -> dict[str, dict[str, Any]]:
    services: dict[str, dict[str, Any]] = {}

    for topology_key in ("rollback_baseline_topology", "current_topology", "final_topology", "post_add_topology", "post_removal_topology"):
        topology = document.get(topology_key)
        if not isinstance(topology, Mapping) or not isinstance(topology.get("services"), Mapping):
            continue
        for node, record in topology["services"].items():
            if not isinstance(record, Mapping):
                continue
            service_uuid = record.get("service_uuid")
            controller_id = record.get("controller_id")
            if isinstance(node, str) and isinstance(service_uuid, str) and service_uuid and isinstance(controller_id, str) and controller_id:
                node_id = _identifier(node, "service topology node")
                previous = services.get(node_id)
                candidate = {
                    "node": node_id,
                    "controller_id": _identifier(controller_id, "service topology controller id"),
                    "service_uuid": service_uuid,
                    "service_status": record.get("service_status"),
                    "readiness_source": record.get("readiness_source"),
                    "last_observed_at": record.get("last_observed_at"),
                }
                if previous is None or topology_key == "final_topology":
                    services[node_id] = candidate

    for key in ("survivors",):
        raw = document.get(key)
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                node = item.get("node")
                service_uuid = item.get("service_uuid")
                controller_id = item.get("controller_id")
                if isinstance(node, str) and isinstance(service_uuid, str) and service_uuid and isinstance(controller_id, str) and controller_id:
                    services[_identifier(node, "survivor node")] = {
                        "node": _identifier(node, "survivor node"),
                        "controller_id": _identifier(controller_id, "survivor controller id"),
                        "service_uuid": service_uuid,
                        "service_status": item.get("service_status"),
                        "readiness_source": item.get("readiness_source"),
                        "last_observed_at": item.get("last_observed_at"),
                    }

    for key in ("service_observations", "survivor_service_observations"):
        raw = document.get(key)
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                record = _service_record_from_observation(item)
                if record is not None:
                    previous = services.get(record["node"])
                    if previous is None or str(record.get("last_observed_at") or "") >= str(previous.get("last_observed_at") or ""):
                        services[record["node"]] = record

    missing = [node for node in nodes if node not in services]
    if missing:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_PREP_BASELINE_INCOMPLETE",
            f"baseline evidence is missing service records for: {', '.join(missing)}",
        )
    return {node: services[node] for node in nodes}


_IDENTITY_HISTORY_ONLY_BASELINE_KINDS = {
    "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
}


def _rollback_baseline_usable_as_live(document: Mapping[str, Any]) -> bool:
    if document.get("kind") != "main_computer.mother.deployment_node_add_rollback_evidence.v1":
        return False
    summary = document.get("summary")
    if not isinstance(summary, Mapping) or summary.get("rollback_baseline_usable_by_add_node_prep") is not True:
        return False
    if document.get("chain_mutation_count") not in (0, None):
        return False
    if document.get("validator_mutation_count") not in (0, None):
        return False
    if document.get("validator_vote_performed") is not False or document.get("validator_admission_performed") is not False:
        return False
    if document.get("routing_or_topology_published") is True or document.get("public_endpoint_created") is True:
        return False
    topology = document.get("rollback_baseline_topology")
    if not isinstance(topology, Mapping):
        topology = document.get("current_topology")
    return isinstance(topology, Mapping)


def _baseline_topology_role(document: Mapping[str, Any]) -> str:
    if _rollback_baseline_usable_as_live(document):
        return "live-topology-source"
    if document.get("kind") in _IDENTITY_HISTORY_ONLY_BASELINE_KINDS:
        return "identity-history-only"
    if document.get("kind") == "main_computer.mother.deployment_node_add_rollback_evidence.v1":
        return "identity-history-only"
    return "live-topology-source"


def _historical_topology_from_baseline(document: Mapping[str, Any]) -> tuple[list[str], list[str], Any, Any, dict[str, dict[str, Any]]]:
    topology = document.get("rollback_baseline_topology")
    if not isinstance(topology, Mapping):
        topology = document.get("final_topology")
    if not isinstance(topology, Mapping):
        topology = document.get("current_topology")
    if not isinstance(topology, Mapping):
        topology = document.get("post_add_topology")
    if not isinstance(topology, Mapping):
        topology = document.get("post_removal_topology")
    if not isinstance(topology, Mapping):
        topology = document

    nodes_raw = topology.get("nodes")
    validators_raw = topology.get("validator_set")
    if not isinstance(nodes_raw, list) or not isinstance(validators_raw, list):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_BASELINE_INCOMPLETE", "baseline topology nodes or validator set is missing")
    nodes = [_identifier(item, "baseline node") for item in nodes_raw]
    validators = [_address(item, "baseline validator address") for item in validators_raw]
    _dedupe(nodes, "baseline nodes")
    _dedupe(validators, "baseline validator set")
    if len(nodes) != len(validators):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_BASELINE_INVALID", "baseline nodes and validators are not aligned")
    validator_count = topology.get("validator_count", len(validators))
    if int(validator_count) != len(validators):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_BASELINE_INVALID", "baseline validator count is inconsistent")
    chain_id, genesis_sha256 = _chain_identity(document)
    services = _service_records(document, nodes)
    return nodes, validators, chain_id, genesis_sha256, services


def _topology_from_baseline(document: Mapping[str, Any]) -> tuple[list[str], list[str], Any, Any, dict[str, dict[str, Any]]]:
    if _baseline_topology_role(document) == "identity-history-only":
        chain_id, genesis_sha256 = _chain_identity(document)
        return [], [], chain_id, genesis_sha256, {}
    return _historical_topology_from_baseline(document)


def _baseline_kind_supported(document: Mapping[str, Any]) -> bool:
    kind = document.get("kind")
    if not isinstance(kind, str):
        return False
    return (
        kind.startswith("main_computer.mother.")
        and kind.endswith(".v1")
        and ("evidence" in kind or "topology" in kind)
    )


def _baseline_clean(document: Mapping[str, Any]) -> bool:
    summary = document.get("summary")
    policy = document.get("policy")
    if not isinstance(summary, Mapping) or not isinstance(policy, Mapping):
        return False
    if document.get("status") != "pass" or summary.get("clean") is not True:
        return False
    if document.get("failure") not in (None,):
        return False
    if summary.get("complete") is False:
        return False
    rollback_baseline = (
        document.get("kind") == "main_computer.mother.deployment_node_add_rollback_evidence.v1"
        and summary.get("rollback_baseline_usable_by_add_node_prep") is True
        and document.get("chain_mutation_count") == 0
        and document.get("validator_mutation_count") == 0
        and document.get("validator_vote_performed") is False
        and document.get("validator_admission_performed") is False
        and document.get("routing_or_topology_published") is False
        and document.get("public_endpoint_created") is False
    )
    if not rollback_baseline:
        if document.get("live_mutation_performed") is True:
            return False
        if summary.get("live_mutation_performed") is True:
            return False
    if document.get("routing_or_topology_published") is True or summary.get("routing_or_topology_published") is True:
        return False
    if document.get("public_endpoint_created") is True or summary.get("public_endpoint_created") is True:
        return False
    if policy.get("routing_or_topology_published") is True or policy.get("public_http_endpoint_created") is True:
        return False
    if policy.get("private_keys_materialized") is True or policy.get("private_keys_persisted") is True:
        return False
    if policy.get("chain_mutation_performed") is True or policy.get("validator_admission_performed") is True:
        return False
    try:
        _topology_from_baseline(document)
    except MotherDeploymentNodeAddPrepError:
        return False
    return True


def _load_baseline(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    baseline_evidence_path: Path,
    *,
    network: str,
    expected_sha256: str,
    max_age_seconds: int,
    now: datetime | None = None,
) -> tuple[dict[str, Any], str, int, list[str], list[str], Any, Any, dict[str, dict[str, Any]]]:
    resolved = Path(baseline_evidence_path).resolve(strict=False)
    document, _payload, digest = _canonical_file(resolved)
    expected = _sha256(expected_sha256, "baseline evidence SHA-256")
    if digest != expected:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_BASELINE_MISMATCH", "baseline evidence SHA-256 mismatch")
    if not _baseline_kind_supported(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_BASELINE_UNSUPPORTED", "baseline evidence kind is not a canonical Mother topology/evidence document")
    if document.get("schema_version") != 1 or document.get("network") != network:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_BASELINE_INVALID", "baseline evidence schema or network is invalid")
    if document.get("mother_binding") != _binding(private_state):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_BASELINE_CHANGED", "current Mother private-state binding no longer matches baseline evidence")
    if not _baseline_clean(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_BASELINE_INVALID", "baseline evidence is not a clean topology proof for add-node prep")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_BASELINE_STALE", "baseline evidence is outside the freshness window")
    nodes, validators, chain_id, genesis_sha256, services = _topology_from_baseline(document)
    return document, digest, age, nodes, validators, chain_id, genesis_sha256, services


def _private_host_for_node(network_doc: Mapping[str, Any], node: str) -> str | None:
    nodes = network_doc.get("nodes")
    if isinstance(nodes, Mapping):
        record = nodes.get(node)
        if isinstance(record, Mapping) and isinstance(record.get("host"), str):
            return _identifier(record["host"], "private-state node host")
    targets = network_doc.get("deployment", {}).get("targets") if isinstance(network_doc.get("deployment"), Mapping) else None
    if isinstance(targets, Mapping):
        target = targets.get(node)
        if isinstance(target, Mapping):
            controller_ref = target.get("controller_ref")
            if isinstance(controller_ref, str) and controller_ref:
                return _identifier(controller_ref.rsplit(".", 1)[-1], "private-state controller ref")
    return None


def _private_validator_for_node(network_doc: Mapping[str, Any], node: str) -> str | None:
    validators = network_doc.get("validators")
    if isinstance(validators, Mapping):
        record = validators.get(node)
        if isinstance(record, Mapping) and isinstance(record.get("address"), str):
            return _address(record["address"], "private-state validator address")
    return None


def _controller_exists(network_doc: Mapping[str, Any], host: str) -> bool:
    coolify = network_doc.get("coolify")
    if not isinstance(coolify, Mapping):
        return False
    controllers = coolify.get("controllers")
    if not isinstance(controllers, Mapping):
        return False
    record = controllers.get(host)
    return isinstance(record, Mapping) and record.get("enabled") is not False


def _removed_target_from_baseline(document: Mapping[str, Any], target_node: str) -> dict[str, Any] | None:
    target = document.get("target")
    if isinstance(target, Mapping) and target.get("node") == target_node:
        validator = target.get("validator_address")
        controller = target.get("controller_id")
        service_uuid = target.get("service_uuid")
        return {
            "node": target_node,
            "validator_address": _address(validator, "removed target validator address"),
            "previous_controller_id": _identifier(controller, "removed target controller id") if isinstance(controller, str) and controller else None,
            "previous_service_uuid": service_uuid if isinstance(service_uuid, str) and service_uuid else None,
        }
    final = document.get("final_topology")
    if isinstance(final, Mapping) and final.get("removed_node") == target_node:
        return {
            "node": target_node,
            "validator_address": _address(final.get("removed_validator_address"), "removed target validator address"),
            "previous_controller_id": None,
            "previous_service_uuid": None,
        }
    return None


def _resolve_target_validator(network_doc: Mapping[str, Any], baseline: Mapping[str, Any], target_node: str) -> tuple[str, str]:
    removed = _removed_target_from_baseline(baseline, target_node)
    if removed is not None:
        return str(removed["validator_address"]), "baseline-removed-target"
    private_validator = _private_validator_for_node(network_doc, target_node)
    if private_validator is not None:
        return private_validator, "mother-private-state"
    raise _fail(
        "MOTHER_DEPLOY_NODE_ADD_PREP_TARGET_INVALID",
        "target validator address could not be derived from baseline evidence or private state",
    )


def _target_previous_service(baseline: Mapping[str, Any], target_node: str) -> tuple[str | None, str | None]:
    removed = _removed_target_from_baseline(baseline, target_node)
    if removed is None:
        return None, None
    return removed.get("previous_controller_id"), removed.get("previous_service_uuid")


def build_node_add_prep_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    baseline_evidence_path: Path,
    *,
    network: str = "mainnet",
    target_node: str,
    target_host: str,
    mode: str = "soft",
    baseline_evidence_sha256: str,
    baseline_max_age_seconds: int = 86400,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if network != "mainnet":
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_NETWORK_INVALID", "node-add prep is currently restricted to mainnet")
    target = _identifier(target_node, "target node")
    host = _identifier(target_host, "target host")
    if mode not in {"initial", "soft", "reactivate"}:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_MODE_INVALID", "mode must be initial, soft, or reactivate")

    baseline, baseline_sha, baseline_age, nodes, validators, chain_id, genesis_sha256, services = _load_baseline(
        paths,
        private_state,
        Path(baseline_evidence_path),
        network=network,
        expected_sha256=baseline_evidence_sha256,
        max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    baseline_topology_role = _baseline_topology_role(baseline)
    historical_nodes: list[str] = []
    historical_validators: list[str] = []
    historical_services: dict[str, dict[str, Any]] = {}
    if baseline_topology_role == "identity-history-only":
        historical_nodes, historical_validators, _hist_chain, _hist_genesis, historical_services = _historical_topology_from_baseline(baseline)

    if target in nodes:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TARGET_INVALID", "target node is already present in current live topology source")

    network_doc = _private_network(private_state, network)
    if not _controller_exists(network_doc, host):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TARGET_INVALID", "target host is not an enabled Mother Coolify controller")
    private_host = _private_host_for_node(network_doc, target)
    if private_host is not None and private_host != host:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TARGET_INVALID", "target host does not match Mother private-state node metadata")

    target_validator, validator_source = _resolve_target_validator(network_doc, baseline, target)
    if target_validator in validators:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TARGET_INVALID", "target validator is already present in baseline validator set")
    previous_controller, previous_service_uuid = _target_previous_service(baseline, target)

    post_nodes = _sorted_topology_nodes([*nodes, target])
    validators_by_node = {node: validators[index] for index, node in enumerate(nodes)}
    validators_by_node[target] = target_validator
    post_validators = [validators_by_node[node] for node in post_nodes]

    current_services = {node: dict(services[node]) for node in nodes}
    created_text = _timestamp(created_at)
    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": mode,
        "source_baseline_evidence": {
            "kind": baseline.get("kind"),
            "locator": _relative(paths, Path(baseline_evidence_path), label="baseline evidence"),
            "sha256": baseline_sha,
            "completed_at": baseline.get("completed_at"),
            "age_seconds": baseline_age,
            "next_phase": baseline.get("next_phase"),
            "topology_role": baseline_topology_role,
            "identity_history_only": baseline_topology_role == "identity-history-only",
            "historical_nodes": historical_nodes,
            "historical_validator_set": historical_validators,
            "historical_services": historical_services,
        },
        "target": {
            "node": target,
            "controller_id": host,
            "desired_service_name": target,
            "validator_address": target_validator,
            "validator_address_source": validator_source,
            "previous_controller_id": previous_controller,
            "previous_service_uuid": previous_service_uuid,
            "existing_service_uuid": None,
        },
        "current_topology": {
            "source": (
                "operator-directed-empty-current-topology"
                if baseline_topology_role == "identity-history-only"
                else "baseline-live-topology-source"
            ),
            "baseline_topology_used_as_live": baseline_topology_role != "identity-history-only",
            "nodes": nodes,
            "validator_set": validators,
            "validator_count": len(validators),
            "services": current_services,
            "chain_id": chain_id,
            "genesis_sha256": genesis_sha256,
        },
        "post_add_topology": {
            "nodes": post_nodes,
            "validator_set": post_validators,
            "validator_count": len(post_validators),
            "added_node": target,
            "added_validator_address": target_validator,
            "target_host": host,
        },
        "topology_diff": {
            "operation": "add-node",
            "added_nodes": [target],
            "removed_nodes": [],
            "unchanged_nodes": nodes,
            "pre_validator_count": len(validators),
            "post_validator_count": len(post_validators),
        },
        "ordered_add_plan": [
            {
                "ordinal": 1,
                "phase": "prospective-host-readiness",
                "description": "prove the target host can admit the prepared complete super-node",
                "live_mutation_required": False,
            },
            {
                "ordinal": 2,
                "phase": "reserve-or-bind-node-identity",
                "description": "bind the target validator identity from Mother private state or prior finalized removal evidence",
                "live_mutation_required": False,
            },
            {
                "ordinal": 3,
                "phase": "create-standby-service",
                "description": "create exactly one complete super-node standby service for the target",
                "live_mutation_required": True,
            },
            {
                "ordinal": 4,
                "phase": "install-identity",
                "description": "install the prepared validator and Hub identities into the target service",
                "live_mutation_required": True,
            },
            {
                "ordinal": 5,
                "phase": "sync-replica",
                "description": "synchronize the target as a non-validator replica before admission",
                "live_mutation_required": True,
            },
            {
                "ordinal": 6,
                "phase": "admit-validator",
                "description": "admit the target validator through the existing validator set after clean replica sync",
                "live_mutation_required": True,
            },
            {
                "ordinal": 7,
                "phase": "post-admission-observe",
                "description": "observe the complete post-add topology and write evidence",
                "live_mutation_required": False,
            },
        ],
        "execution_plan": {
            "kind": "mother-add-node-prep-only",
            "prep_mutation_count": 0,
            "do_mutation_required": True,
            "generic_topology_diff": True,
            "hardcoded_stage_target": False,
            "operator_directed_testing_path": True,
            "baseline_topology_role": baseline_topology_role,
            "old_baseline_topology_used_as_live": baseline_topology_role != "identity-history-only",
            "allowed_next_command": f"add-node do {network}",
        },
        "policy": {
            "compiler": "mother-native-add-node-prep-v1",
            "read_only_preparation": True,
            "operator_directed_testing_path": True,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "mutation_count": 0,
            "service_creation_performed": False,
            "validator_admission_performed": False,
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
            "service_creation_authorized": False,
            "validator_admission_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "operator_release_required_for_do": True,
        },
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_NODE_ADD_DO_REQUIRED",
                "message": "prep only records the exact generic add-node topology plan; do/finalize are not executed by this transaction",
            }
        ],
    }
    transaction["summary"] = {
        "transaction_valid": True,
        "target_node": target,
        "target_host": host,
        "target_validator_address": target_validator,
        "baseline_topology_role": baseline_topology_role,
        "old_baseline_topology_used_as_live": baseline_topology_role != "identity-history-only",
        "current_nodes": nodes,
        "post_add_nodes": post_nodes,
        "current_validator_count": len(validators),
        "post_add_validator_count": len(post_validators),
        "generic_topology_diff": True,
        "hardcoded_stage_target": False,
        "prep_mutation_count": 0,
        "live_mutation_performed": False,
        "service_creation_performed": False,
        "validator_admission_performed": False,
        "next_phase": f"add-node-do-{network}",
    }
    transaction["node_add_prep_transaction_sha256"] = _digest_without(
        transaction,
        "node_add_prep_transaction_sha256",
    )
    if _contains_sensitive(transaction):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_SENSITIVE", "node-add prep transaction contains sensitive material")
    return transaction


def write_node_add_prep_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(transaction)
    if document.get("kind") != _TRANSACTION_KIND or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "node-add prep transaction is malformed or sensitive")
    digest = _digest_without(document, "node_add_prep_transaction_sha256")
    if document.get("node_add_prep_transaction_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "node-add prep transaction digest mismatch")
    payload = canonical_json(document)
    root = _ensure_directory(paths, _TRANSACTION_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "nodeaddprep"
    network = str(document.get("network") or "network")
    target = str(document.get("target", {}).get("node") or "node")
    destination = root / f"{stamp}-{network}-{target}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_CONFLICT", "transaction destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_node_add_prep_transaction(
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
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_PATH_INVALID", "node-add prep transaction is outside its directory") from exc
    document, _payload, _file_sha = _canonical_file(resolved)
    digest = _digest_without(document, "node_add_prep_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("mother_binding") != _binding(private_state)
        or document.get("node_add_prep_transaction_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "node-add prep transaction is invalid")
    age = _age_seconds(document.get("created_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_STALE", "node-add prep transaction is outside the freshness window")

    source = document.get("source_baseline_evidence")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "baseline evidence binding is missing")
    baseline_path = _resolve_under_evidence(paths, source.get("locator"), label="baseline evidence")
    baseline, baseline_sha, _baseline_age, nodes, validators, _chain_id, _genesis_sha, _services = _load_baseline(
        paths,
        private_state,
        baseline_path,
        network=document.get("network"),
        expected_sha256=source.get("sha256"),
        max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if baseline_sha != source.get("sha256"):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "baseline evidence digest changed")
    current = document.get("current_topology")
    post = document.get("post_add_topology")
    target = document.get("target")
    if not isinstance(current, Mapping) or not isinstance(post, Mapping) or not isinstance(target, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "node-add prep topology is incomplete")
    if list(current.get("nodes", [])) != nodes or list(current.get("validator_set", [])) != validators:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "current topology no longer matches baseline evidence")
    target_node = target.get("node")
    target_validator = target.get("validator_address")
    if target_node in current.get("nodes", []):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "target node is already present in current topology")
    if target_node not in post.get("nodes", []):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "target node is missing from post-add topology")
    if target_validator in current.get("validator_set", []):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "target validator is already present in current topology")
    if target_validator not in post.get("validator_set", []):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "target validator is missing from post-add topology")
    if document.get("execution_plan", {}).get("generic_topology_diff") is not True:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "node-add prep must be topology-diff driven")
    if document.get("execution_plan", {}).get("hardcoded_stage_target") is not False:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "node-add prep must not use a hardcoded stage target")
    source_topology_role = source.get("topology_role", _baseline_topology_role(baseline))
    if source_topology_role == "identity-history-only" and current.get("baseline_topology_used_as_live") is True:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_PREP_TRANSACTION_INVALID", "identity/history baseline was used as live topology")

    return {
        "clean": True,
        "transaction_path": str(resolved),
        "node_add_prep_transaction_sha256": digest,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "operator_directed_testing_path": document.get("execution_plan", {}).get("operator_directed_testing_path") is True,
        "baseline_topology_role": source_topology_role,
        "old_baseline_topology_used_as_live": current.get("baseline_topology_used_as_live") is True,
        "current_topology_source": current.get("source"),
        "target_node": target_node,
        "target_host": target["controller_id"],
        "target_validator_address": target_validator,
        "current_nodes": list(current["nodes"]),
        "post_add_nodes": list(post["nodes"]),
        "current_validator_set": list(current["validator_set"]),
        "post_add_validator_set": list(post["validator_set"]),
        "generic_topology_diff": True,
        "hardcoded_stage_target": False,
        "live_mutation_performed": False,
        "service_creation_performed": False,
        "validator_admission_performed": False,
        "source_baseline_evidence_sha256": baseline_sha,
        "next_phase": document["summary"]["next_phase"],
    }


__all__ = [
    "MotherDeploymentNodeAddPrepError",
    "build_node_add_prep_transaction",
    "verify_node_add_prep_transaction",
    "write_node_add_prep_transaction",
]
