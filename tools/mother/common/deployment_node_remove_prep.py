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
_SINGLE_NODE_FINAL_TOPOLOGY_KIND = "main_computer.mother.deployment_node_add_single_node_chain_and_hub_proof_evidence.v1"
_ADD_VALIDATOR_ADMISSION_KIND = "main_computer.mother.deployment_node_add_validator_admission_evidence.v1"
_ADD_POST_ADMISSION_TOPOLOGY_KIND = "main_computer.mother.add_node_post_admission_topology_evidence.v1"
_REMOVE_FINALIZE_KIND = "main_computer.mother.deployment_node_remove_finalize_evidence.v1"
_T3_BASELINE_DIRECTORY = ("evidence", "deployment-t3-post-admission-steady-state")
_SINGLE_NODE_FINAL_TOPOLOGY_DIRECTORY = ("evidence", "deployment-node-add-single-node-chain-and-hub-proof")
_ADD_VALIDATOR_ADMISSION_DIRECTORY = ("evidence", "deployment-node-add-validator-admission")
_ADD_POST_ADMISSION_TOPOLOGY_DIRECTORY = ("evidence", "deployment-node-add-post-admission-observe")
_REMOVE_FINALIZE_DIRECTORY = ("evidence", "deployment-node-remove-finalize")
_SUPPORTED_BASELINE_KINDS = frozenset(
    {
        _T3_BASELINE_KIND,
        _SINGLE_NODE_FINAL_TOPOLOGY_KIND,
        _ADD_VALIDATOR_ADMISSION_KIND,
        _ADD_POST_ADMISSION_TOPOLOGY_KIND,
        _REMOVE_FINALIZE_KIND,
    }
)
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
    if document.get("kind") == _SINGLE_NODE_FINAL_TOPOLOGY_KIND:
        final_topology = document.get("final_topology")
        services = final_topology.get("services") if isinstance(final_topology, Mapping) else None
        if not isinstance(services, Mapping):
            raise _fail(
                "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
                "single-node final topology evidence does not contain services",
            )
        latest: dict[str, dict[str, Any]] = {}
        for node, record in services.items():
            if isinstance(node, str) and isinstance(record, Mapping):
                item = dict(record)
                item.setdefault("node", node)
                item.setdefault("observed_at", item.get("last_observed_at"))
                latest[node] = item
        return latest

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



def _receipt_service_records(
    document: Mapping[str, Any],
    *,
    candidate_node: str,
    candidate_controller_id: str,
    candidate_service_uuid: str,
) -> dict[str, dict[str, Any]]:
    services: dict[str, dict[str, Any]] = {
        candidate_node: {
            "node": candidate_node,
            "controller_id": candidate_controller_id,
            "service_uuid": candidate_service_uuid,
            "service_status": None,
            "readiness_source": "add-node-validator-admission-target",
            "observed_at": document.get("completed_at"),
        }
    }

    for item in document.get("precondition_receipts") or []:
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        controller_id = item.get("controller_id")
        service_uuid = item.get("service_uuid")
        if isinstance(node, str) and isinstance(controller_id, str) and isinstance(service_uuid, str) and service_uuid:
            services[node] = {
                "node": node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "service_status": item.get("service_status"),
                "readiness_source": item.get("name") or "add-node-validator-admission-precondition",
                "observed_at": document.get("completed_at"),
            }

    for item in document.get("mutation_receipts") or []:
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        controller_id = item.get("controller_id")
        service_uuid = item.get("service_uuid")
        if isinstance(node, str) and isinstance(controller_id, str) and isinstance(service_uuid, str) and service_uuid:
            services[node] = {
                "node": node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "service_status": services.get(node, {}).get("service_status"),
                "readiness_source": item.get("mutation_id") or "add-node-validator-admission-mutation",
                "observed_at": document.get("completed_at"),
            }

    for item in document.get("health_observations") or []:
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        if not isinstance(node, str) or node not in services:
            continue
        observed_at = item.get("observed_at")
        previous_at = services[node].get("observed_at")
        if previous_at is None or str(observed_at or "") >= str(previous_at or ""):
            services[node]["service_status"] = item.get("status")
            services[node]["observed_at"] = observed_at
            services[node]["readiness_source"] = "add-node-validator-admission-health-observation"

    return services



def _validator_admission_proves_removable_validator(document: Mapping[str, Any], summary: Mapping[str, Any], authority: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    failure = document.get("failure")
    failure_code = failure.get("code") if isinstance(failure, Mapping) else None
    status_ok = document.get("status") == "pass" and summary.get("clean") is True and summary.get("complete") is True
    post_admission_health_failure = (
        document.get("status") == "failed"
        and failure_code == "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_POST_ADMISSION_HEALTH_UNCLEAN"
        and summary.get("clean") is False
        and summary.get("complete") is False
        and summary.get("next_phase") == "manual-review-required"
        and document.get("next_phase") == "manual-review-required"
    )
    if not (status_ok or post_admission_health_failure):
        return False
    return all(
        (
            summary.get("validator_vote_performed") is True,
            summary.get("validator_activation_performed") is True,
            summary.get("final_validator_set_verified") is True,
            summary.get("routing_or_topology_published") is False,
            summary.get("public_endpoint_created") is False,
            authority.get("validator_vote_proven") is True,
            authority.get("validator_activation_proven") is True,
            policy.get("routing_or_topology_published") is False,
            policy.get("public_http_endpoint_created") is False,
            document.get("routing_or_topology_published") is not True,
            document.get("public_endpoint_created") is not True,
            document.get("chain_mutation_count") == 1,
        )
    )


def _normalize_validator_admission_baseline(document: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    summary = document.get("summary")
    authority = document.get("authority")
    policy = document.get("policy")
    if not isinstance(summary, Mapping) or not isinstance(authority, Mapping) or not isinstance(policy, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", "validator-admission evidence summary, authority, or policy is missing")

    candidate_node = _identifier(document.get("candidate_node"), "candidate node")
    candidate_validator = _address(document.get("candidate_validator_address"), "candidate validator address")
    candidate_controller_id = _identifier(document.get("target_host"), "candidate controller id")
    candidate_service_uuid = str(document.get("created_service_uuid") or "")
    if not candidate_service_uuid:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", "candidate service UUID is missing")

    voter_nodes_raw = document.get("voter_nodes")
    current_validators_raw = document.get("current_validator_set")
    desired_validators_raw = document.get("desired_validator_set")
    if not isinstance(voter_nodes_raw, list) or not isinstance(current_validators_raw, list) or not isinstance(desired_validators_raw, list):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", "validator-admission node or validator sets are missing")

    voter_nodes = [_identifier(item, "voter node") for item in voter_nodes_raw]
    current_validators = [_address(item, "current validator address") for item in current_validators_raw]
    desired_validators = [_address(item, "desired validator address") for item in desired_validators_raw]
    _dedupe(voter_nodes, "voter nodes")
    _dedupe(current_validators, "current validator set")
    _dedupe(desired_validators, "desired validator set")
    if candidate_node in voter_nodes:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "candidate node is already a voter node")
    if candidate_validator in current_validators:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "candidate validator is already in the current validator set")
    if len(voter_nodes) != len(current_validators):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "voter nodes and current validator set are not aligned")
    if sorted(desired_validators) != sorted([*current_validators, candidate_validator]):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "desired validator set does not equal current validators plus candidate")

    if not _validator_admission_proves_removable_validator(document, summary, authority, policy):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "validator-admission evidence does not prove an admitted current validator")

    nodes = [*voter_nodes, candidate_node]
    validators = [*current_validators, candidate_validator]
    services = _receipt_service_records(
        document,
        candidate_node=candidate_node,
        candidate_controller_id=candidate_controller_id,
        candidate_service_uuid=candidate_service_uuid,
    )
    missing = [node for node in nodes if node not in services]
    if missing:
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
            f"validator-admission evidence is missing service records for: {', '.join(missing)}",
        )

    chain_id = document.get("chain_id")
    if not isinstance(chain_id, int) or chain_id <= 0:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "validator-admission chain_id is invalid")
    normalized = {
        "kind": document.get("kind"),
        "schema_version": 1,
        "status": document.get("status"),
        "completed_at": document.get("completed_at"),
        "mother_binding": document.get("mother_binding"),
        "network": document.get("network"),
        "next_phase": document.get("next_phase"),
        "chain_id": chain_id,
        "genesis_sha256": _sha256(document.get("genesis_sha256"), "validator-admission genesis SHA-256"),
        "nodes": nodes,
        "validator_set": validators,
        "validator_count": len(validators),
    }
    return normalized, services


def _baseline_directory_for_kind(kind: Any) -> tuple[str, ...]:
    if kind == _SINGLE_NODE_FINAL_TOPOLOGY_KIND:
        return _SINGLE_NODE_FINAL_TOPOLOGY_DIRECTORY
    if kind == _ADD_VALIDATOR_ADMISSION_KIND:
        return _ADD_VALIDATOR_ADMISSION_DIRECTORY
    if kind == _ADD_POST_ADMISSION_TOPOLOGY_KIND:
        return _ADD_POST_ADMISSION_TOPOLOGY_DIRECTORY
    if kind == _REMOVE_FINALIZE_KIND:
        return _REMOVE_FINALIZE_DIRECTORY
    return _T3_BASELINE_DIRECTORY



def _normalize_add_post_admission_topology_baseline(
    document: Mapping[str, Any],
    summary: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    network = _identifier(document.get("network"), "baseline network")
    expected_next_phase = f"add-node-prep-{network}"
    required_truths = (
        document.get("status") == "pass",
        summary.get("clean") is True,
        summary.get("complete") is True,
        summary.get("topology_current") is True,
        summary.get("topology_stale") is False,
        summary.get("source_validator_admission_clean") is True,
        summary.get("current_topology_marked_by_evidence") is True,
        summary.get("next_phase") == expected_next_phase,
        document.get("next_phase") == expected_next_phase,
        document.get("failure") is None,
        document.get("live_mutation_performed") is False,
        document.get("chain_mutation_performed") is False,
        document.get("routing_or_topology_published") is False,
        document.get("public_endpoint_created") is False,
        policy.get("live_mutation_performed") is False,
        policy.get("finalize_mutation_performed") is False,
        policy.get("chain_mutation_performed") is False,
        policy.get("validator_admission_performed") is False,
        policy.get("validator_vote_performed") is False,
        policy.get("routing_or_topology_published") is False,
        policy.get("public_http_endpoint_created") is False,
        policy.get("private_keys_materialized") is False,
        policy.get("private_keys_persisted") is False,
    )
    if not all(required_truths):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "add-node post-admission topology evidence is not clean")

    final_topology = document.get("final_topology")
    if not isinstance(final_topology, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", "add-node post-admission final topology is missing")
    current_topology = document.get("current_topology")
    if isinstance(current_topology, Mapping):
        if current_topology.get("nodes") != final_topology.get("nodes") or current_topology.get("validator_set") != final_topology.get("validator_set"):
            raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "add-node post-admission current and final topology disagree")

    nodes_raw = final_topology.get("nodes")
    validators_raw = final_topology.get("validator_set")
    if not isinstance(nodes_raw, list) or not isinstance(validators_raw, list):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", "add-node post-admission topology nodes or validator set is missing")
    nodes = [_identifier(item, "baseline node") for item in nodes_raw]
    validators = [_address(item, "baseline validator address") for item in validators_raw]
    _dedupe(nodes, "baseline nodes")
    _dedupe(validators, "baseline validator set")
    if len(nodes) != len(validators) or len(nodes) < 1:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "add-node post-admission topology must contain matching nodes and validators")
    if int(final_topology.get("validator_count", len(validators))) != len(validators):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "add-node post-admission final validator count is inconsistent")
    if int(summary.get("final_validator_count", len(validators))) != len(validators):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "add-node post-admission summary validator count is inconsistent")

    services_raw = final_topology.get("services")
    if not isinstance(services_raw, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", "add-node post-admission topology services are missing")
    services: dict[str, dict[str, Any]] = {}
    for node in nodes:
        record = services_raw.get(node)
        if not isinstance(record, Mapping):
            raise _fail(
                "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
                f"add-node post-admission topology is missing service for: {node}",
            )
        item = dict(record)
        item.setdefault("node", node)
        item.setdefault("observed_at", item.get("last_observed_at") or document.get("observed_at") or document.get("completed_at"))
        services[node] = item

    normalized = dict(document)
    normalized["nodes"] = list(nodes)
    normalized["validator_set"] = list(validators)
    normalized["validator_count"] = len(validators)
    normalized["chain_id"] = final_topology.get("chain_id")
    normalized["genesis_sha256"] = final_topology.get("genesis_sha256")
    normalized["service_observations"] = list(document.get("service_observations") or [])
    return normalized, services


def _normalize_remove_finalize_baseline(
    document: Mapping[str, Any],
    summary: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    authority = document.get("authority")
    if not isinstance(authority, Mapping):
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
            "remove-finalize evidence authority is missing",
        )

    network = _identifier(document.get("network"), "baseline network")
    expected_next_phase = "remove-node-finalized-mainnet"
    required_truths = (
        document.get("status") == "pass",
        document.get("failure") is None,
        document.get("next_phase") == expected_next_phase,
        summary.get("clean") is True,
        summary.get("complete") is True,
        summary.get("target_service_absent") is True,
        summary.get("removed_validator_absent_from_final_set") is True,
        summary.get("next_phase") == expected_next_phase,
        summary.get("live_mutation_performed") is False,
        summary.get("routing_or_topology_published") is False,
        summary.get("public_endpoint_created") is False,
        policy.get("finalize_mutation_performed") is False,
        policy.get("routing_or_topology_published") is False,
        policy.get("public_http_endpoint_created") is False,
        policy.get("private_keys_materialized") is False,
        policy.get("private_keys_persisted") is False,
        authority.get("finalize_live_mutation_authorized") is False,
        authority.get("target_service_absence_proven") is True,
        authority.get("survivor_services_observed") is True,
        authority.get("routing_or_topology_publication_authorized") is False,
        authority.get("public_endpoint_creation_authorized") is False,
        document.get("live_mutation_performed") is False,
        document.get("routing_or_topology_published") is False,
        document.get("public_endpoint_created") is False,
    )
    if not all(required_truths):
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID",
            "remove-finalize evidence is not a clean finalized topology proof",
        )

    final_topology = document.get("final_topology")
    if not isinstance(final_topology, Mapping):
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
            "remove-finalize final topology is missing",
        )
    pre_removal_topology = document.get("pre_removal_topology")
    if pre_removal_topology is not None and not isinstance(pre_removal_topology, Mapping):
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
            "remove-finalize pre-removal topology is invalid",
        )

    nodes_raw = final_topology.get("nodes")
    validators_raw = final_topology.get("validator_set")
    if not isinstance(nodes_raw, list) or not isinstance(validators_raw, list):
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
            "remove-finalize final topology nodes or validator set is missing",
        )
    nodes = [_identifier(item, "baseline node") for item in nodes_raw]
    validators = [_address(item, "baseline validator address") for item in validators_raw]
    _dedupe(nodes, "baseline nodes")
    _dedupe(validators, "baseline validator set")
    if len(nodes) != len(validators):
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID",
            "remove-finalize final topology must contain matching nodes and validators",
        )
    if int(final_topology.get("validator_count", len(validators))) != len(validators):
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID",
            "remove-finalize final validator count is inconsistent",
        )
    if int(summary.get("final_validator_count", len(validators))) != len(validators):
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID",
            "remove-finalize summary validator count is inconsistent",
        )

    target = document.get("target")
    if not isinstance(target, Mapping):
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
            "remove-finalize target is missing",
        )
    removed_node = _identifier(target.get("node"), "removed node")
    removed_validator = _address(target.get("validator_address"), "removed validator address")
    if removed_node in nodes or removed_validator in validators:
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID",
            "remove-finalize target is still present in the final topology",
        )

    survivor_records: dict[str, dict[str, Any]] = {}
    survivors = document.get("survivors")
    if not isinstance(survivors, list):
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
            "remove-finalize survivors are missing",
        )
    for item in survivors:
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        if not isinstance(node, str) or node not in nodes:
            continue
        survivor_records[node] = dict(item)

    for item in document.get("survivor_service_observations") or []:
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        if not isinstance(node, str) or node not in nodes:
            continue
        record = survivor_records.setdefault(node, {"node": node})
        if item.get("controller_id"):
            record["controller_id"] = item.get("controller_id")
        if item.get("service_uuid"):
            record["service_uuid"] = item.get("service_uuid")
        record["service_status"] = item.get("service_status")
        record["observed_at"] = item.get("observed_at")
        record["readiness_source"] = "remove-finalize-survivor-observation"

    missing = [node for node in nodes if node not in survivor_records]
    if missing:
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
            f"remove-finalize evidence is missing survivor records for: {', '.join(missing)}",
        )
    for node in nodes:
        _service_record(node, survivor_records[node])

    chain_id = final_topology.get("chain_id")
    if chain_id is None and isinstance(pre_removal_topology, Mapping):
        chain_id = pre_removal_topology.get("chain_id")
    genesis_sha256 = final_topology.get("genesis_sha256")
    if genesis_sha256 is None and isinstance(pre_removal_topology, Mapping):
        genesis_sha256 = pre_removal_topology.get("genesis_sha256")
    if not isinstance(chain_id, int) or chain_id <= 0:
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID",
            "remove-finalize chain_id is invalid",
        )
    genesis_sha256 = _sha256(genesis_sha256, "remove-finalize genesis SHA-256")

    normalized = dict(document)
    normalized["network"] = network
    normalized["nodes"] = list(nodes)
    normalized["validator_set"] = list(validators)
    normalized["validator_count"] = len(validators)
    normalized["chain_id"] = chain_id
    normalized["genesis_sha256"] = genesis_sha256
    normalized["service_observations"] = list(document.get("survivor_service_observations") or [])
    return normalized, survivor_records


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
    kind = document.get("kind")
    if kind not in _SUPPORTED_BASELINE_KINDS:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_UNSUPPORTED", "baseline evidence kind is not supported for node removal prep")
    if document.get("schema_version") != 1 or document.get("network") != network:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "baseline evidence schema or network is invalid")
    if document.get("mother_binding") != _binding(private_state):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_CHANGED", "current Mother private-state binding no longer matches baseline evidence")
    if document.get("status") != "pass":
        summary_for_status = document.get("summary")
        authority_for_status = document.get("authority")
        policy_for_status = document.get("policy")
        if not (
            kind == _ADD_VALIDATOR_ADMISSION_KIND
            and isinstance(summary_for_status, Mapping)
            and isinstance(authority_for_status, Mapping)
            and isinstance(policy_for_status, Mapping)
            and _validator_admission_proves_removable_validator(document, summary_for_status, authority_for_status, policy_for_status)
        ):
            raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "baseline evidence did not pass")

    completed_at = document.get("completed_at")
    age = _age_seconds(completed_at, now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_STALE", "baseline evidence is outside the freshness window")

    summary = document.get("summary")
    policy = document.get("policy")
    if not isinstance(summary, Mapping) or not isinstance(policy, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", "baseline evidence summary or policy is missing")

    if kind == _ADD_VALIDATOR_ADMISSION_KIND:
        normalized, services = _normalize_validator_admission_baseline(document)
        return normalized, digest, age, services

    if kind == _ADD_POST_ADMISSION_TOPOLOGY_KIND:
        normalized, services = _normalize_add_post_admission_topology_baseline(document, summary, policy)
        return normalized, digest, age, services

    if kind == _REMOVE_FINALIZE_KIND:
        normalized, services = _normalize_remove_finalize_baseline(document, summary, policy)
        return normalized, digest, age, services

    if kind == _SINGLE_NODE_FINAL_TOPOLOGY_KIND:
        final_topology = document.get("final_topology")
        if not isinstance(final_topology, Mapping):
            raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", "single-node final topology is missing")
        required_truths = (
            summary.get("clean") is True,
            summary.get("complete") is True,
            summary.get("current_topology_marked_by_evidence") is True,
            summary.get("serves_chain") is True,
            summary.get("serves_hub") is True,
            summary.get("single_node_bootstrap_proven") is True,
            summary.get("routing_or_topology_published") is False,
            summary.get("public_endpoint_created") is False,
            policy.get("finalize_mutation_performed") is False,
            policy.get("routing_or_topology_published") is False,
            policy.get("public_endpoint_created") is False,
            document.get("routing_or_topology_published") is not True,
            document.get("public_endpoint_created") is not True,
            document.get("live_mutation_performed") is not True,
        )
        if not all(required_truths):
            raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "single-node final topology evidence is not clean")
        nodes_raw = final_topology.get("nodes")
        validators_raw = final_topology.get("validator_set")
        if not isinstance(nodes_raw, list) or not isinstance(validators_raw, list):
            raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE", "single-node final topology nodes or validator set is missing")
        nodes = [_identifier(item, "baseline node") for item in nodes_raw]
        validators = [_address(item, "baseline validator address") for item in validators_raw]
        _dedupe(nodes, "baseline nodes")
        _dedupe(validators, "baseline validator set")
        if len(nodes) != len(validators) or len(nodes) != 1:
            raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "single-node removal baseline must contain exactly one node and validator")
        if int(final_topology.get("validator_count", len(validators))) != len(validators):
            raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID", "single-node final validator count is inconsistent")
        normalized = dict(document)
        normalized["nodes"] = list(nodes)
        normalized["validator_set"] = list(validators)
        normalized["validator_count"] = len(validators)
        normalized["chain_id"] = final_topology.get("chain_id")
        normalized["genesis_sha256"] = final_topology.get("genesis_sha256")
        services = _latest_service_records(normalized)
        missing = [node for node in nodes if node not in services]
        if missing:
            raise _fail(
                "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INCOMPLETE",
                f"single-node final topology is missing services for: {', '.join(missing)}",
            )
        return normalized, digest, age, services

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
        "readiness_source": record.get("proof_source") or record.get("readiness_source"),
        "last_observed_at": record.get("observed_at") or record.get("last_observed_at"),
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
    single_node_decommission = not survivors and not post_validators and len(nodes) == 1
    if (not survivors or not post_validators) and not single_node_decommission:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TARGET_INVALID", "node removal would leave an inconsistent survivor topology")

    service_topology = {node: _service_record(node, services[node]) for node in nodes}
    created_text = _timestamp(created_at)
    service_deletion_is_first = bool(single_node_decommission)
    validator_removal_vote_required = not single_node_decommission
    routing_withdrawal_required = not single_node_decommission
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
        "ordered_removal_plan": (
            [
                {
                    "ordinal": 1,
                    "phase": "decommission-single-node-service",
                    "description": "delete the sole single-node service after proving it is the entire live topology",
                    "required_before_service_deletion": False,
                },
                {
                    "ordinal": 2,
                    "phase": "verify-empty-topology",
                    "description": "verify the topology is empty after the sole service is removed",
                    "required_before_service_deletion": False,
                },
            ]
            if single_node_decommission
            else [
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
            ]
        ),
        "execution_plan": {
            "kind": "mother-remove-node-prep-only",
            "prep_mutation_count": 0,
            "do_mutation_required": True,
            "single_node_decommission": single_node_decommission,
            "service_deletion_is_first": service_deletion_is_first,
            "routing_topology_withdrawal_required_before_service_deletion": routing_withdrawal_required,
            "rpc_withdrawal_required_before_service_deletion": routing_withdrawal_required,
            "qbft_validator_removal_required_before_service_deletion": validator_removal_vote_required,
            "allowed_next_command": f"remove-node do {network}",
        },
        "policy": {
            "compiler": "mother-native-remove-node-prep-v1",
            "read_only_preparation": True,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "mutation_count": 0,
            "service_deletion_performed": False,
            "service_deletion_is_first": service_deletion_is_first,
            "single_node_decommission": single_node_decommission,
            "validator_removal_vote_required": validator_removal_vote_required,
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
            "validator_removal_vote_required": validator_removal_vote_required,
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
        "routing_topology_withdrawal_required_before_service_deletion": routing_withdrawal_required,
        "service_deletion_is_first": service_deletion_is_first,
        "single_node_decommission": single_node_decommission,
        "validator_removal_vote_required": validator_removal_vote_required,
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
        _baseline_directory_for_kind(source.get("kind")),
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
    execution_plan = document.get("execution_plan", {})
    single_node_decommission = bool(execution_plan.get("single_node_decommission"))
    expected_service_deletion_is_first = single_node_decommission
    if execution_plan.get("service_deletion_is_first") is not expected_service_deletion_is_first:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_PREP_TRANSACTION_INVALID", "service deletion ordering does not match topology kind")
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
        "routing_topology_withdrawal_required_before_service_deletion": bool(execution_plan.get("routing_topology_withdrawal_required_before_service_deletion")),
        "service_deletion_is_first": bool(execution_plan.get("service_deletion_is_first")),
        "single_node_decommission": single_node_decommission,
        "validator_removal_vote_required": bool(execution_plan.get("qbft_validator_removal_required_before_service_deletion")),
        "live_mutation_performed": False,
        "service_deletion_performed": False,
        "source_baseline_evidence_sha256": baseline_sha,
        "next_phase": document["summary"]["next_phase"],
    }
