"""Read-only Mother topology staleness detection and empty-topology rectification.

This module intentionally does not mutate Coolify.  It compares Mother topology
evidence to read-only Coolify service observations and, when every Mother-known
service is absent and the operator declares the actual live node set empty,
writes a Mother-local empty-topology evidence document.

The rectification path is deliberately narrow.  Partial live topologies are
detected and rejected as not yet implemented.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import urllib.parse

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import (
    CoolifyObservationError,
    _DEFAULT_OPENER,
    get_coolify_json,
    list_coolify_controllers,
    resolve_coolify_controller,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult


_STALENESS_EVIDENCE_KIND = "main_computer.mother.live_topology_staleness_observation.v1"
_EMPTY_EVIDENCE_KIND = "main_computer.mother.live_topology_empty_rectification_evidence.v1"
_ADD_VALIDATOR_ADMISSION_KIND = "main_computer.mother.deployment_node_add_validator_admission_evidence.v1"
_EMPTY_EVIDENCE_DIRECTORY = ("evidence", "deployment-live-topology-empty-rectification")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_NODE_HINT_RE = re.compile(r"\bmainnet[a-z]+-super[0-9]+\b")


class MotherDeploymentTopologyRectificationError(RuntimeError):
    """Topology staleness detection or rectification failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentTopologyRectificationError:
    return MotherDeploymentTopologyRectificationError(code, message)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or not _IDENTIFIER_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", f"{label} is not a valid identifier")
    return value


def _sha256(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if not _SHA256_RE.fullmatch(text):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", f"{label} is not a SHA-256 digest")
    return text


def _address(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ADDRESS_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", f"{label} is not a validator address")
    return value.lower()


def _timestamp(now: datetime | None = None) -> str:
    value = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_TIME_INVALID", f"{label} is missing")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any, *, now: datetime | None = None) -> int:
    observed = _parse_utc(value, "topology evidence completed_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - observed).total_seconds())
    if age < -60:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_TIME_INVALID", "topology evidence timestamp is in the future")
    return max(age, 0)


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _root(paths: PrivateStatePaths, parts: Iterable[str]) -> Path:
    return paths.root.joinpath(*parts)


def _ensure_directory(paths: PrivateStatePaths, parts: Iterable[str], *, operation: OperationIdentity) -> Path:
    root = _root(paths, parts)
    atomic_files.ensure_durable_directory(root, operation=operation)
    return root


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    resolved = Path(path).resolve(strict=False)
    try:
        return resolved.relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_PATH_INVALID", f"{label} is outside Mother runtime state") from exc


def _canonical_file(path: Path) -> tuple[dict[str, Any], bytes, str]:
    raw = Path(path).read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "topology evidence is not canonical JSON") from exc
    if not isinstance(doc, dict):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "topology evidence root is not an object")
    canonical = canonical_json(doc)
    if canonical != raw:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "topology evidence is not canonical JSON")
    return doc, raw, hashlib.sha256(raw).hexdigest()


def _digest_without(document: Mapping[str, Any], key: str) -> str:
    copy = dict(document)
    copy.pop(key, None)
    return hashlib.sha256(canonical_json(copy)).hexdigest()


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        if re.fullmatch(r"0x[0-9a-f]{64}", lowered):
            return True
        if "private_key" in lowered or "api_token" in lowered:
            return True
        return False
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in {"api_token", "private_key", "validator_private_key", "hub_admin_private_key"}:
                return True
            if _contains_sensitive(item):
                return True
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    return False


def _items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return list(payload)
    if isinstance(payload, Mapping):
        for key in ("services", "resources", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return list(value)
        if any(key in payload for key in ("uuid", "id", "name")):
            return [payload]
    return []


def _topology(document: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("final_topology", "current_topology", "post_add_topology", "post_removal_topology"):
        value = document.get(key)
        if isinstance(value, Mapping):
            return value
    return document


def _chain_identity(document: Mapping[str, Any], topology: Mapping[str, Any]) -> tuple[int, str]:
    candidates: list[Mapping[str, Any]] = [topology, document]
    for key in ("final_topology", "current_topology", "post_add_topology", "post_removal_topology", "pre_removal_topology"):
        candidate = document.get(key)
        if isinstance(candidate, Mapping) and all(candidate is not item for item in candidates):
            candidates.append(candidate)

    for candidate in candidates:
        chain_id = candidate.get("chain_id")
        genesis = candidate.get("genesis_sha256")
        if chain_id is None and genesis is None:
            continue
        if not isinstance(chain_id, int) or chain_id <= 0:
            raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "topology chain_id is invalid")
        return chain_id, _sha256(genesis, "topology genesis SHA-256")

    raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "topology chain identity is missing")


def _controller_ids_for_inventory(
    private_state: PrivateStateReadResult,
    network: str,
    services: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    controller_ids = {
        str(record["controller_id"])
        for record in services.values()
        if isinstance(record, Mapping) and record.get("controller_id")
    }
    for controller in list_coolify_controllers(private_state):
        if controller.network == network and controller.enabled:
            controller_ids.add(controller.controller_id)
    return sorted(controller_ids)


def _topology_service_record(node: str, record: Mapping[str, Any], *, source: str, completed_at: Any) -> dict[str, Any]:
    controller = _identifier(record.get("controller_id"), f"{node} controller id")
    service_uuid = str(record.get("service_uuid") or record.get("created_service_uuid") or "")
    if not service_uuid:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", f"{node} service UUID is missing")
    return {
        "node": node,
        "controller_id": controller,
        "service_uuid": service_uuid,
        "service_status": record.get("service_status"),
        "readiness_source": record.get("readiness_source") or source,
        "last_observed_at": record.get("last_observed_at") or record.get("observed_at") or completed_at,
    }


def _observation_service_record(item: Mapping[str, Any], *, completed_at: Any) -> dict[str, Any] | None:
    node = item.get("node")
    controller_id = item.get("controller_id")
    service_uuid = item.get("service_uuid")
    if not isinstance(node, str) or not isinstance(controller_id, str) or not isinstance(service_uuid, str) or not service_uuid:
        return None
    node_id = _identifier(node, "service observation node")
    return _topology_service_record(
        node_id,
        {
            "controller_id": controller_id,
            "service_uuid": service_uuid,
            "service_status": item.get("service_status"),
            "readiness_source": item.get("readiness_source") or "service-observation",
            "observed_at": item.get("observed_at"),
        },
        source="service-observation",
        completed_at=completed_at,
    )


def _document_service_records(document: Mapping[str, Any], nodes: list[str], topology: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    services: dict[str, dict[str, Any]] = {}
    completed_at = document.get("completed_at")

    raw_services = topology.get("services")
    if isinstance(raw_services, Mapping):
        for node in nodes:
            record = raw_services.get(node)
            if not isinstance(record, Mapping):
                raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", f"topology service record missing for {node}")
            services[node] = _topology_service_record(
                node,
                record,
                source="topology-service-record",
                completed_at=completed_at,
            )

    for key, source in (("survivors", "survivor-record"),):
        raw = document.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            node = item.get("node")
            if not isinstance(node, str) or node not in nodes:
                continue
            services[node] = _topology_service_record(
                _identifier(node, "survivor node"),
                item,
                source=source,
                completed_at=completed_at,
            )

    for key, source in (("service_observations", "service-observation"), ("survivor_service_observations", "survivor-service-observation")):
        raw = document.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            record = _observation_service_record(item, completed_at=completed_at)
            if record is None or record["node"] not in nodes:
                continue
            record["readiness_source"] = record.get("readiness_source") or source
            previous = services.get(record["node"])
            if previous is None or str(record.get("last_observed_at") or "") >= str(previous.get("last_observed_at") or ""):
                services[record["node"]] = record

    target = document.get("target")
    if isinstance(target, Mapping):
        node = target.get("node") or target.get("desired_service_name")
        if isinstance(node, str) and node in nodes and node not in services:
            services[_identifier(node, "target node")] = _topology_service_record(
                _identifier(node, "target node"),
                target,
                source="target",
                completed_at=completed_at,
            )

    return services


def _nodes_and_services(document: Mapping[str, Any]) -> tuple[list[str], list[str], int, str, dict[str, dict[str, Any]]]:
    topology = _topology(document)
    nodes_raw = topology.get("nodes")
    validators_raw = topology.get("validator_set")
    if not isinstance(nodes_raw, list) or not isinstance(validators_raw, list):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "topology nodes or validator set is missing")
    nodes = [_identifier(item, "topology node") for item in nodes_raw]
    validators = [_address(item, "topology validator address") for item in validators_raw]
    if len(nodes) != len(validators):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "topology nodes and validator set are not aligned")
    chain_id, genesis = _chain_identity(document, topology)
    services = _document_service_records(document, nodes, topology)
    if set(services) != set(nodes):
        missing = [node for node in nodes if node not in services]
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", f"topology service records are missing for: {', '.join(missing)}")
    return nodes, validators, chain_id, genesis, services



def _receipt_service_records_for_validator_admission(
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
            "last_observed_at": document.get("completed_at"),
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
                "last_observed_at": document.get("completed_at"),
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
                "last_observed_at": document.get("completed_at"),
            }
    for item in document.get("health_observations") or []:
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        if not isinstance(node, str) or node not in services:
            continue
        observed_at = item.get("observed_at")
        previous_at = services[node].get("last_observed_at")
        if previous_at is None or str(observed_at or "") >= str(previous_at or ""):
            services[node]["service_status"] = item.get("status")
            services[node]["last_observed_at"] = observed_at
            services[node]["readiness_source"] = "add-node-validator-admission-health-observation"
    return services



def _validator_admission_represents_live_validator_topology(document: Mapping[str, Any], summary: Mapping[str, Any], authority: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
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


def _topology_detection_document(document: Mapping[str, Any]) -> Mapping[str, Any]:
    if document.get("kind") != _ADD_VALIDATOR_ADMISSION_KIND:
        return document

    summary = document.get("summary")
    authority = document.get("authority")
    policy = document.get("policy")
    if not isinstance(summary, Mapping) or not isinstance(authority, Mapping) or not isinstance(policy, Mapping):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "validator-admission evidence summary, authority, or policy is missing")

    candidate_node = _identifier(document.get("candidate_node"), "candidate node")
    candidate_validator = _address(document.get("candidate_validator_address"), "candidate validator address")
    candidate_controller_id = _identifier(document.get("target_host"), "candidate controller id")
    candidate_service_uuid = str(document.get("created_service_uuid") or "")
    if not candidate_service_uuid:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "candidate service UUID is missing")

    voter_nodes_raw = document.get("voter_nodes")
    current_validators_raw = document.get("current_validator_set")
    desired_validators_raw = document.get("desired_validator_set")
    if not isinstance(voter_nodes_raw, list) or not isinstance(current_validators_raw, list) or not isinstance(desired_validators_raw, list):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "validator-admission topology inputs are missing")
    voter_nodes = [_identifier(item, "voter node") for item in voter_nodes_raw]
    current_validators = [_address(item, "current validator") for item in current_validators_raw]
    desired_validators = [_address(item, "desired validator") for item in desired_validators_raw]
    if len(voter_nodes) != len(current_validators):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "voter nodes and current validator set are not aligned")
    if len(set(voter_nodes)) != len(voter_nodes) or len(set(current_validators)) != len(current_validators) or len(set(desired_validators)) != len(desired_validators):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "validator-admission topology contains duplicates")
    if candidate_node in voter_nodes or candidate_validator in current_validators:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "validator-admission candidate is already present")
    if sorted(desired_validators) != sorted([*current_validators, candidate_validator]):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "validator-admission desired set is inconsistent")

    if not _validator_admission_represents_live_validator_topology(document, summary, authority, policy):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "validator-admission evidence does not prove an admitted live validator topology")

    nodes = [*voter_nodes, candidate_node]
    validators = [*current_validators, candidate_validator]
    services = _receipt_service_records_for_validator_admission(
        document,
        candidate_node=candidate_node,
        candidate_controller_id=candidate_controller_id,
        candidate_service_uuid=candidate_service_uuid,
    )
    missing = [node for node in nodes if node not in services]
    if missing:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", f"validator-admission service records missing for: {', '.join(missing)}")
    chain_id = document.get("chain_id")
    if not isinstance(chain_id, int) or chain_id <= 0:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "validator-admission chain_id is invalid")

    return {
        "kind": document.get("kind"),
        "schema_version": 1,
        "completed_at": document.get("completed_at"),
        "status": document.get("status"),
        "mother_binding": document.get("mother_binding"),
        "network": document.get("network"),
        "next_phase": document.get("next_phase"),
        "target": {
            "node": candidate_node,
            "controller_id": candidate_controller_id,
            "service_uuid": candidate_service_uuid,
            "created_service_uuid": candidate_service_uuid,
            "validator_address": candidate_validator,
        },
        "current_topology": {
            "source": "deployment-node-add-validator-admission-evidence",
            "chain_id": chain_id,
            "genesis_sha256": _sha256(document.get("genesis_sha256"), "validator-admission genesis SHA-256"),
            "nodes": nodes,
            "services": services,
            "validator_count": len(validators),
            "validator_set": validators,
            "validator_admission_performed": True,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
        },
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
    }


def _service_hints_from_payload(payload: Any) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for item in _items(payload):
        if not isinstance(item, Mapping):
            continue
        fields = {
            "uuid": item.get("uuid") or item.get("id"),
            "name": item.get("name") or item.get("resourceName"),
            "description": item.get("description"),
            "status": item.get("status") or item.get("state"),
        }
        text = " ".join(str(value) for value in fields.values() if value is not None)
        nodes = sorted(set(match.group(0) for match in _NODE_HINT_RE.finditer(text)))
        if nodes:
            hints.append({**fields, "node_hints": nodes})
    return hints


def _latest_known_target(document: Mapping[str, Any], nodes: list[str], validators: list[str], services: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    target = document.get("target")
    if isinstance(target, Mapping):
        node = target.get("node") or target.get("desired_service_name")
        if isinstance(node, str) and node:
            service_uuid = target.get("created_service_uuid") or target.get("service_uuid")
            controller = target.get("controller_id")
            validator = target.get("validator_address")
            if not validator and node in nodes:
                validator = validators[nodes.index(node)]
            if not controller and node in services:
                controller = services[node].get("controller_id")
            if not service_uuid and node in services:
                service_uuid = services[node].get("service_uuid")
            if validator and controller:
                return {
                    "node": _identifier(node, "target node"),
                    "controller_id": _identifier(controller, "target controller id"),
                    "service_uuid": str(service_uuid or ""),
                    "previous_service_uuid": str(service_uuid or ""),
                    "validator_address": _address(validator, "target validator address"),
                }
    if len(nodes) == 1:
        service = services.get(nodes[0], {})
        return {
            "node": nodes[0],
            "controller_id": _identifier(service.get("controller_id"), "target controller id"),
            "service_uuid": str(service.get("service_uuid") or ""),
            "previous_service_uuid": str(service.get("service_uuid") or ""),
            "validator_address": validators[0],
        }
    return None


def detect_topology_staleness(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    topology_evidence_path: Path,
    *,
    network: str = "mainnet",
    acknowledged_topology_evidence_sha256: str | None = None,
    max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = 4 * 1024 * 1024,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = Path(topology_evidence_path).resolve(strict=False)
    document, _raw, digest = _canonical_file(resolved)
    if acknowledged_topology_evidence_sha256 is not None and digest != _sha256(acknowledged_topology_evidence_sha256, "topology evidence SHA-256"):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_STALENESS_ACK_MISMATCH", "topology evidence SHA-256 mismatch")
    if document.get("network") != network or document.get("schema_version") != 1:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "topology evidence schema or network is invalid")
    if document.get("mother_binding") != _binding(private_state):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_PRIVATE_STATE_CHANGED", "Mother private-state binding no longer matches topology evidence")
    detection_document = _topology_detection_document(document)
    if _contains_sensitive(detection_document):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_SENSITIVE", "topology evidence contains sensitive material")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_STALE", "topology evidence is outside the freshness window")

    nodes, validators, chain_id, genesis_sha, services = _nodes_and_services(detection_document)
    target = _latest_known_target(detection_document, nodes, validators, services)

    service_results: list[dict[str, Any]] = []
    inventory_hints: list[dict[str, Any]] = []
    controllers = _controller_ids_for_inventory(private_state, network, services)
    for controller_id in controllers:
        controller = resolve_coolify_controller(private_state, network, controller_id)
        try:
            listing = get_coolify_json(
                controller,
                "/api/v1/services",
                authenticated=True,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            for hint in _service_hints_from_payload(listing.payload):
                inventory_hints.append({"controller_id": controller_id, **hint})
        except CoolifyObservationError as exc:
            inventory_hints.append({"controller_id": controller_id, "error": exc.code})

    for node in nodes:
        service = services[node]
        controller = resolve_coolify_controller(private_state, network, service["controller_id"])
        path = "/api/v1/services/" + urllib.parse.quote(str(service["service_uuid"]), safe="")
        observation = get_coolify_json(
            controller,
            path,
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        present = 200 <= observation.status < 300
        absent = observation.status in {404, 410}
        service_results.append(
            {
                "node": node,
                "validator_address": validators[nodes.index(node)],
                "controller_id": service["controller_id"],
                "service_uuid": service["service_uuid"],
                "status": observation.status,
                "response_sha256": observation.response_sha256,
                "byte_length": observation.byte_length,
                "present": present,
                "absent": absent,
            }
        )

    present_expected = [item["node"] for item in service_results if item["present"]]
    missing_expected = [item["node"] for item in service_results if item["absent"]]
    unknown_expected = [item["node"] for item in service_results if not item["present"] and not item["absent"]]
    live_node_hints = sorted(set(node for hint in inventory_hints for node in hint.get("node_hints", [])))
    unexpected_live_nodes = [node for node in live_node_hints if node not in set(nodes)]
    inventory_errors = [hint for hint in inventory_hints if hint.get("error")]
    all_expected_absent = bool(nodes) and len(missing_expected) == len(nodes) and not present_expected and not unknown_expected
    no_expected_nodes = not nodes
    empty_topology_has_live_hints = no_expected_nodes and bool(live_node_hints)
    empty_topology_inventory_unknown = no_expected_nodes and bool(inventory_errors)
    topology_current = (
        (no_expected_nodes and not live_node_hints and not inventory_errors)
        or (
            not no_expected_nodes
            and len(present_expected) == len(nodes)
            and not missing_expected
            and not unknown_expected
            and not unexpected_live_nodes
            and not inventory_errors
        )
    )
    empty_rectification_required = all_expected_absent and not live_node_hints
    split_or_partial_live_topology = (
        bool(unexpected_live_nodes)
        or bool(missing_expected)
        or bool(unknown_expected)
        or bool(inventory_errors and not no_expected_nodes)
    )
    topology_stale = empty_rectification_required or split_or_partial_live_topology or empty_topology_has_live_hints or empty_topology_inventory_unknown
    rectification_required = empty_rectification_required
    partial_or_unknown = (
        split_or_partial_live_topology
        and not rectification_required
        and not no_expected_nodes
    ) or empty_topology_has_live_hints or empty_topology_inventory_unknown

    status = "pass"
    if rectification_required:
        status = "stale"
    elif partial_or_unknown:
        status = "manual-review-required"

    return {
        "kind": _STALENESS_EVIDENCE_KIND,
        "schema_version": 1,
        "observed_at": _timestamp(now=now),
        "status": status,
        "network": network,
        "mother_binding": _binding(private_state),
        "topology_evidence": {
            "path": str(resolved),
            "locator": _relative(paths, resolved, label="topology evidence"),
            "sha256": digest,
            "age_seconds": age,
            "kind": document.get("kind"),
            "next_phase": document.get("next_phase"),
        },
        "chain_id": chain_id,
        "genesis_sha256": genesis_sha,
        "expected_nodes": nodes,
        "expected_validator_set": validators,
        "expected_services": services,
        "expected_service_observations": service_results,
        "observed_live_node_hints": live_node_hints,
        "unexpected_live_nodes": unexpected_live_nodes,
        "observed_service_hints": inventory_hints,
        "present_expected_nodes": present_expected,
        "missing_expected_nodes": missing_expected,
        "unknown_expected_nodes": unknown_expected,
        "target": target,
        "authority": {
            "read_only_detection": True,
            "network_access_performed": True,
            "live_mutation_authorized": False,
            "rectification_authorized": False,
        },
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "live_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "summary": {
            "clean": status in {"pass", "stale"},
            "topology_current": topology_current,
            "topology_stale": topology_stale,
            "rectification_required": rectification_required,
            "rectification_supported": empty_rectification_required,
            "manual_review_required": partial_or_unknown,
            "expected_node_count": len(nodes),
            "present_expected_node_count": len(present_expected),
            "missing_expected_node_count": len(missing_expected),
            "observed_live_node_hints": live_node_hints,
            "unexpected_live_nodes": unexpected_live_nodes,
            "unexpected_live_node_count": len(unexpected_live_nodes),
            "observed_inventory_error_count": len(inventory_errors),
            "network_access_performed": True,
            "live_mutation_performed": False,
            "next_phase": (
                "adopt-empty-current-topology"
                if rectification_required
                else "manual-review-required"
                if partial_or_unknown
                else f"add-node-prep-{network}"
            ),
        },
    }


def build_empty_topology_rectification_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    topology_evidence_path: Path,
    *,
    network: str = "mainnet",
    acknowledged_topology_evidence_sha256: str,
    actual_nodes: Iterable[str] = (),
    max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = 4 * 1024 * 1024,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
) -> dict[str, Any]:
    operator_actual_nodes = [_identifier(node, "actual node") for node in actual_nodes if str(node or "").strip()]
    detection = detect_topology_staleness(
        paths,
        private_state,
        topology_evidence_path,
        network=network,
        acknowledged_topology_evidence_sha256=acknowledged_topology_evidence_sha256,
        max_age_seconds=max_age_seconds,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        now=now,
    )
    if operator_actual_nodes:
        raise _fail(
            "MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_NOT_YET_IMPLEMENTED",
            "rectification with live nodes is not yet implemented; provide no --actual-node values only when the live network is empty",
        )
    if detection["summary"]["rectification_required"] is not True:
        if detection["observed_live_node_hints"] or detection["present_expected_nodes"] or detection["unknown_expected_nodes"]:
            raise _fail(
                "MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_NOT_YET_IMPLEMENTED",
                "rectification for partial or non-empty live topology is not yet implemented",
            )
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_REFUSED", "topology evidence is not stale-empty")
    if detection["observed_live_node_hints"]:
        raise _fail(
            "MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_NOT_YET_IMPLEMENTED",
            "rectification detected live node hints; non-empty live topology rectification is not yet implemented",
        )

    completed = _timestamp(now=now)
    target = detection.get("target")
    final_topology = {
        "source": "read-only-live-empty-topology-rectification",
        "chain_id": detection["chain_id"],
        "genesis_sha256": detection["genesis_sha256"],
        "nodes": [],
        "services": {},
        "validator_count": 0,
        "validator_set": [],
        "baseline_topology_used_as_live": False,
        "rectified_from_stale_nodes": list(detection["expected_nodes"]),
    }
    evidence: dict[str, Any] = {
        "kind": _EMPTY_EVIDENCE_KIND,
        "schema_version": 1,
        "completed_at": completed,
        "status": "pass",
        "failure": None,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": "manual-empty-topology-rectification",
        "source_previous_topology_evidence": dict(detection["topology_evidence"]),
        "staleness_detection": {
            "expected_nodes": list(detection["expected_nodes"]),
            "expected_services": dict(detection["expected_services"]),
            "missing_expected_nodes": list(detection["missing_expected_nodes"]),
            "present_expected_nodes": [],
            "observed_live_node_hints": [],
            "observed_service_hints": list(detection["observed_service_hints"]),
            "network_access_performed": True,
        },
        "target": target,
        "current_topology": final_topology,
        "final_topology": final_topology,
        "topology_diff": {
            "operation": "manual-empty-topology-rectification",
            "added_nodes": [],
            "removed_nodes": list(detection["expected_nodes"]),
            "unchanged_nodes": [],
            "pre_validator_count": len(detection["expected_validator_set"]),
            "post_validator_count": 0,
        },
        "authority": {
            "read_only_rectification": True,
            "topology_staleness_detected": True,
            "all_expected_services_absent": True,
            "operator_declared_actual_nodes": [],
            "empty_topology_marked_by_evidence": True,
            "live_mutation_authorized": False,
        },
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "finalize_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_http_endpoint_created": False,
            "public_endpoint_created": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "summary": {
            "clean": True,
            "complete": True,
            "topology_rectified": True,
            "empty_topology_marked_by_evidence": True,
            "current_topology_marked_by_evidence": True,
            "final_nodes": [],
            "final_validator_count": 0,
            "final_validator_set": [],
            "actual_nodes": [],
            "expected_stale_nodes": list(detection["expected_nodes"]),
            "network_access_performed": True,
            "live_mutation_performed": False,
            "old_baseline_topology_used_as_live": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "next_phase": f"add-node-prep-{network}",
        },
        "live_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": f"add-node-prep-{network}",
    }
    if target is not None and target.get("node"):
        evidence["target"] = {
            "node": target["node"],
            "controller_id": target["controller_id"],
            "service_uuid": target.get("service_uuid") or "",
            "previous_service_uuid": target.get("previous_service_uuid") or target.get("service_uuid") or "",
            "validator_address": target["validator_address"],
        }
    if _contains_sensitive(evidence):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_SENSITIVE", "rectification evidence contains sensitive material")
    evidence["live_topology_empty_rectification_sha256"] = _digest_without(evidence, "live_topology_empty_rectification_sha256")
    return evidence


def write_empty_topology_rectification_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if evidence.get("kind") != _EMPTY_EVIDENCE_KIND:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "not an empty-topology rectification evidence document")
    document = dict(evidence)
    digest = _digest_without(document, "live_topology_empty_rectification_sha256")
    if document.get("live_topology_empty_rectification_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "rectification evidence digest mismatch")
    payload = canonical_json(document)
    root = _ensure_directory(paths, _EMPTY_EVIDENCE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", ""))) or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = digest[:16]
    path = root / f"{stamp}-{suffix}.json"
    atomic_files.durable_create(path, payload, operation=operation)
    return path, hashlib.sha256(payload).hexdigest()


def adopt_empty_current_topology(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    topology_evidence_path: Path,
    *,
    network: str = "mainnet",
    acknowledged_topology_evidence_sha256: str,
    actual_nodes: Iterable[str] = (),
    max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = 4 * 1024 * 1024,
    write_evidence: bool = False,
    operation: OperationIdentity,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
) -> dict[str, Any]:
    evidence = build_empty_topology_rectification_evidence(
        paths,
        private_state,
        topology_evidence_path,
        network=network,
        acknowledged_topology_evidence_sha256=acknowledged_topology_evidence_sha256,
        actual_nodes=actual_nodes,
        max_age_seconds=max_age_seconds,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        now=now,
    )
    if write_evidence:
        evidence_path, evidence_sha = write_empty_topology_rectification_evidence(
            paths,
            evidence,
            operation=operation,
        )
        evidence = {**evidence, "evidence": {"path": str(evidence_path), "sha256": evidence_sha}}
    return evidence


def verify_empty_topology_rectification_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = Path(evidence_path).resolve(strict=False)
    allowed = _root(paths, _EMPTY_EVIDENCE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_PATH_INVALID", "rectification evidence is outside its directory") from exc
    document, _raw, file_sha = _canonical_file(resolved)
    digest = _digest_without(document, "live_topology_empty_rectification_sha256")
    summary = document.get("summary")
    final = document.get("final_topology")
    policy = document.get("policy")
    if (
        document.get("kind") != _EMPTY_EVIDENCE_KIND
        or document.get("schema_version") != 1
        or document.get("mother_binding") != _binding(private_state)
        or document.get("live_topology_empty_rectification_sha256") != digest
        or not isinstance(summary, Mapping)
        or not isinstance(final, Mapping)
        or not isinstance(policy, Mapping)
        or _contains_sensitive(document)
    ):
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "rectification evidence is invalid")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_STALE", "rectification evidence is outside the freshness window")
    clean = all([
        document.get("status") == "pass",
        document.get("failure") is None,
        summary.get("clean") is True,
        summary.get("complete") is True,
        summary.get("topology_rectified") is True,
        summary.get("empty_topology_marked_by_evidence") is True,
        summary.get("final_nodes") == [],
        summary.get("final_validator_count") == 0,
        final.get("nodes") == [],
        final.get("validator_set") == [],
        final.get("validator_count") == 0,
        policy.get("live_mutation_performed") is False,
        policy.get("routing_or_topology_published") is False,
        policy.get("public_endpoint_created") is False or policy.get("public_http_endpoint_created") is False,
        str(document.get("next_phase", "")).startswith("add-node-prep-"),
    ])
    if not clean:
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "rectification evidence is not clean")
    return {
        "clean": True,
        "evidence_path": str(resolved),
        "evidence_sha256": file_sha,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "final_nodes": [],
        "final_validator_count": 0,
        "final_validator_set": [],
        "empty_topology_marked_by_evidence": True,
        "network_access_performed": True,
        "live_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "source_previous_topology_evidence_sha256": document["source_previous_topology_evidence"]["sha256"],
        "next_phase": document["next_phase"],
    }


__all__ = [
    "MotherDeploymentTopologyRectificationError",
    "adopt_empty_current_topology",
    "build_empty_topology_rectification_evidence",
    "detect_topology_staleness",
    "verify_empty_topology_rectification_evidence",
    "write_empty_topology_rectification_evidence",
]
