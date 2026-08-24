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
import urllib.error
import urllib.parse
import urllib.request

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
_LIVE_CURRENT_TOPOLOGY_KIND = "main_computer.mother.live_current_topology_evidence.v1"
_ADD_POST_ADMISSION_TOPOLOGY_KIND = "main_computer.mother.add_node_post_admission_topology_evidence.v1"
_ADD_VALIDATOR_ADMISSION_KIND = "main_computer.mother.deployment_node_add_validator_admission_evidence.v1"
_EMPTY_EVIDENCE_DIRECTORY = ("evidence", "deployment-live-topology-empty-rectification")
_LIVE_CURRENT_TOPOLOGY_DIRECTORY = ("evidence", "deployment-live-current-topology")
_ADD_POST_ADMISSION_TOPOLOGY_DIRECTORY = ("evidence", "deployment-node-add-post-admission-observe")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_NODE_HINT_RE = re.compile(r"\bmainnet[a-z]+-super[0-9]+\b")
_CANONICAL_HISTORY_PROOF_CONTRACT = "mother-add-node-validator-admission-canonical-block-history-v1"
_CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS = (
    "first_block_number",
    "first_block_hash",
    "first_block_parent_hash",
    "first_block_validator_set",
    "second_block_number",
    "second_block_hash",
    "second_block_parent_hash",
    "second_block_validator_set",
    "latest_block_number",
    "latest_block_hash",
    "latest_block_parent_hash",
    "latest_validator_set",
)
_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_FIELD = "candidate_activation_canonical_history_proof"
_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD = "candidate_activation_canonical_history_proof_sha256"


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


def _same_validator_set(values: Iterable[str], expected: Iterable[str]) -> bool:
    return sorted(_address(item, "validator") for item in values) == sorted(_address(item, "validator") for item in expected)


def _canonical_history_proof_payload_missing_fields(value: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(value, Mapping):
        return list(_CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS)
    return [field for field in _CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS if field not in value]


def _canonical_history_proof_payload_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(value))).hexdigest()


def _looks_like_canonical_history_proof_payload(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("canonical_history_proof_contract") == _CANONICAL_HISTORY_PROOF_CONTRACT:
        return True
    return all(field in value for field in _CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS)


def _find_canonical_history_proof_payload(value: Any, *, depth: int = 0) -> Mapping[str, Any] | None:
    if depth > 10:
        return None
    if _looks_like_canonical_history_proof_payload(value):
        return value
    if isinstance(value, Mapping):
        for key, item in value.items():
            if re.search(r"key|secret|token|password|private", str(key), re.IGNORECASE):
                continue
            found = _find_canonical_history_proof_payload(item, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_canonical_history_proof_payload(item, depth=depth + 1)
            if found is not None:
                return found
    return None



def _open(opener: Any, request: urllib.request.Request, timeout: float):
    try:
        return opener.open(request, timeout=timeout)
    except TypeError:
        return opener.open(request)


def _scoped_public_candidate_activation_proof_endpoint(document: Mapping[str, Any]) -> bool:
    endpoint = document.get("candidate_activation_proof_endpoint")
    if not isinstance(endpoint, Mapping):
        return False
    url = endpoint.get("url")
    host = endpoint.get("host")
    return all([
        endpoint.get("kind") == "mother-add-node-validator-admission-public-proof-endpoint.v1",
        endpoint.get("transport") == "http-public-controller",
        endpoint.get("public_http_endpoint_created") is True,
        isinstance(url, str) and url.startswith("http://") and url.endswith("/proof"),
        isinstance(host, str) and bool(host.strip()),
    ])


def _public_endpoint_policy_clean(document: Mapping[str, Any], summary: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    scoped = _scoped_public_candidate_activation_proof_endpoint(document)
    public_flags = [
        summary.get("public_endpoint_created"),
        summary.get("public_candidate_activation_proof_endpoint_created"),
        policy.get("public_http_endpoint_created"),
        policy.get("public_candidate_activation_proof_endpoint_created"),
        document.get("public_endpoint_created"),
    ]
    for value in public_flags:
        if value is True and not scoped:
            return False
        if value not in (False, None, True):
            return False
    return True



def _validator_admission_public_endpoint_policy_ok(document: Mapping[str, Any], summary: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    if _public_endpoint_policy_clean(document, summary, policy):
        return True
    endpoint = document.get("candidate_activation_proof_endpoint")
    scoped_legacy = (
        isinstance(endpoint, Mapping)
        and endpoint.get("kind") == "mother-add-node-validator-admission-public-proof-endpoint.v1"
        and endpoint.get("transport") == "http-public-controller"
        and endpoint.get("public_http_endpoint_created") is True
        and isinstance(endpoint.get("url"), str)
        and str(endpoint.get("url")).startswith("http://")
        and str(endpoint.get("url")).endswith("/proof")
    )
    if not scoped_legacy:
        return False
    summary_proxy = dict(summary)
    policy_proxy = dict(policy)
    document_proxy = dict(document)
    endpoint_proxy = dict(endpoint)
    endpoint_proxy.setdefault("host", "legacy-test-host")
    document_proxy["candidate_activation_proof_endpoint"] = endpoint_proxy
    return _public_endpoint_policy_clean(document_proxy, summary_proxy, policy_proxy)


def _fetch_candidate_activation_proof_payload(
    proof_endpoint: Mapping[str, Any] | None,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    if not isinstance(proof_endpoint, Mapping):
        return None, {"transport": "missing", "ok": False, "reason": "candidate activation proof endpoint is missing"}
    if proof_endpoint.get("kind") != "mother-add-node-validator-admission-public-proof-endpoint.v1":
        return None, {"transport": proof_endpoint.get("transport"), "ok": False, "reason": "candidate activation proof endpoint kind is not supported"}
    url = proof_endpoint.get("url")
    if not isinstance(url, str) or not url.startswith("http://"):
        return None, {"transport": proof_endpoint.get("transport"), "ok": False, "reason": "candidate activation proof endpoint URL is missing or unsupported"}
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "main-computer-mother-post-admission-proof-recheck/1",
        },
        method="GET",
    )
    try:
        response = _open(opener, request, timeout=timeout)
        status = int(getattr(response, "status", response.getcode()))
        content_type = str(response.headers.get("Content-Type", ""))
        raw = response.read(max_response_bytes + 1)
        response.close()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        content_type = str(exc.headers.get("Content-Type", "")) if exc.headers else ""
        raw = exc.read(max_response_bytes + 1)
    except Exception as exc:
        return None, {
            "transport": proof_endpoint.get("transport"),
            "url": url,
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc)[:300],
        }
    truncated = len(raw) > max_response_bytes
    raw = raw[:max_response_bytes]
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    summary = {
        "transport": proof_endpoint.get("transport"),
        "url": url,
        "status": status,
        "ok": 200 <= status <= 299,
        "content_type": content_type,
        "byte_length": len(raw),
        "truncated": truncated,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
    }
    if isinstance(payload, Mapping):
        return payload, summary
    summary["reason"] = "proof endpoint did not return a JSON object"
    return None, summary

def _canonical_history_proof_payload_verified(payload: Any, *, expected_validator_set: Iterable[str]) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if payload.get("canonical_history_proof_contract") != _CANONICAL_HISTORY_PROOF_CONTRACT:
        return False
    if _canonical_history_proof_payload_missing_fields(payload):
        return False
    try:
        expected = [str(item) for item in expected_validator_set]
        for field in ("first_block_validator_set", "second_block_validator_set", "latest_validator_set"):
            value = payload.get(field)
            if not isinstance(value, list) or not _same_validator_set([str(item) for item in value], expected):
                return False
    except MotherDeploymentTopologyRectificationError:
        return False
    for field in ("first_block_hash", "first_block_parent_hash", "second_block_hash", "second_block_parent_hash", "latest_block_hash", "latest_block_parent_hash"):
        value = payload.get(field)
        if not isinstance(value, str) or re.fullmatch(r"0x[0-9a-fA-F]{64}", value) is None:
            return False
    first = payload.get("first_block_number")
    second = payload.get("second_block_number")
    latest = payload.get("latest_block_number")
    if not isinstance(first, int) or not isinstance(second, int) or not isinstance(latest, int):
        return False
    return first >= 0 and second > first and latest >= second


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


def _fresh_empty_topology_requires_new_genesis(candidate: Mapping[str, Any]) -> bool:
    nodes = candidate.get("nodes")
    validators = candidate.get("validator_set")
    return (
        candidate.get("fresh_genesis_required") is True
        or candidate.get("genesis_lineage") == "fresh-required"
    ) and nodes == [] and validators == []


def _chain_identity(document: Mapping[str, Any], topology: Mapping[str, Any]) -> tuple[int, str | None]:
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
        if genesis is None:
            if _fresh_empty_topology_requires_new_genesis(candidate):
                return chain_id, None
            raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_INVALID", "topology genesis SHA-256 is missing")
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
    result = {
        "node": node,
        "controller_id": controller,
        "service_uuid": service_uuid,
        "service_status": record.get("service_status"),
        "readiness_source": record.get("readiness_source") or source,
        "last_observed_at": record.get("last_observed_at") or record.get("observed_at") or completed_at,
    }
    route = record.get("validator_route") if isinstance(record.get("validator_route"), Mapping) else None
    if route is None and isinstance(record.get("p2p_route"), Mapping):
        route = record.get("p2p_route")
    if isinstance(route, Mapping):
        result["validator_route"] = dict(route)
    for key in ("vpn_ip", "p2p_port", "p2p_endpoint"):
        if record.get(key) is not None:
            result[key] = record.get(key)
    return result


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


def _nodes_and_services(document: Mapping[str, Any]) -> tuple[list[str], list[str], int, str | None, dict[str, dict[str, Any]]]:
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
    service_routes = document.get("service_routes")
    if not isinstance(service_routes, Mapping):
        service_routes = {}
    candidate_route = service_routes.get(candidate_node) if isinstance(service_routes.get(candidate_node), Mapping) else None
    if candidate_route is None and isinstance(document.get("candidate_validator_route"), Mapping):
        candidate_route = document.get("candidate_validator_route")
    candidate_service: dict[str, Any] = {
        "node": candidate_node,
        "controller_id": candidate_controller_id,
        "service_uuid": candidate_service_uuid,
        "service_status": None,
        "readiness_source": "add-node-validator-admission-target",
        "last_observed_at": document.get("completed_at"),
    }
    if isinstance(candidate_route, Mapping):
        candidate_service["validator_route"] = dict(candidate_route)
        for key in ("vpn_ip", "p2p_port", "p2p_endpoint"):
            if candidate_route.get(key) is not None:
                candidate_service[key] = candidate_route.get(key)
    services: dict[str, dict[str, Any]] = {candidate_node: candidate_service}
    for item in document.get("precondition_receipts") or []:
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        controller_id = item.get("controller_id")
        service_uuid = item.get("service_uuid")
        if isinstance(node, str) and isinstance(controller_id, str) and isinstance(service_uuid, str) and service_uuid:
            service_record = {
                "node": node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "service_status": item.get("service_status"),
                "readiness_source": item.get("name") or "add-node-validator-admission-precondition",
                "last_observed_at": document.get("completed_at"),
            }
            route = service_routes.get(node) if isinstance(service_routes.get(node), Mapping) else None
            if isinstance(route, Mapping):
                service_record["validator_route"] = dict(route)
                for key in ("vpn_ip", "p2p_port", "p2p_endpoint"):
                    if route.get(key) is not None:
                        service_record[key] = route.get(key)
            services[node] = service_record
    for item in document.get("mutation_receipts") or []:
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        controller_id = item.get("controller_id")
        service_uuid = item.get("service_uuid")
        if isinstance(node, str) and isinstance(controller_id, str) and isinstance(service_uuid, str) and service_uuid:
            previous = services.get(node, {})
            service_record = {
                "node": node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "service_status": previous.get("service_status"),
                "readiness_source": item.get("mutation_id") or "add-node-validator-admission-mutation",
                "last_observed_at": document.get("completed_at"),
            }
            route = previous.get("validator_route") if isinstance(previous, Mapping) and isinstance(previous.get("validator_route"), Mapping) else None
            if route is None:
                route = service_routes.get(node) if isinstance(service_routes.get(node), Mapping) else None
            if isinstance(route, Mapping):
                service_record["validator_route"] = dict(route)
                for key in ("vpn_ip", "p2p_port", "p2p_endpoint"):
                    if route.get(key) is not None:
                        service_record[key] = route.get(key)
            services[node] = service_record
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
            _public_endpoint_policy_clean(document, summary, policy),
            authority.get("validator_vote_proven") is True,
            authority.get("validator_activation_proven") is True,
            policy.get("routing_or_topology_published") is False,
            document.get("routing_or_topology_published") is not True,
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
            "validator_route": dict(document.get("candidate_validator_route")) if isinstance(document.get("candidate_validator_route"), Mapping) else {},
            "p2p_port": document.get("candidate_p2p_port"),
            "p2p_endpoint": document.get("candidate_p2p_endpoint"),
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



def _service_hint_is_live(hint: Mapping[str, Any]) -> bool:
    """Treat only explicitly terminal Coolify services as non-live inventory hints."""
    status = str(hint.get("status") or "").strip().lower()
    terminal = status == "exited" or status.startswith("exited:") or status == "stopped" or status.startswith("stopped:")
    return not terminal


def _observed_inventory_errors(detection: Mapping[str, Any]) -> list[dict[str, Any]]:
    hints = detection.get("observed_service_hints")
    if not isinstance(hints, list):
        return []
    return [dict(item) for item in hints if isinstance(item, Mapping) and item.get("error")]


def _primary_live_service_hints_by_node(detection: Mapping[str, Any], nodes: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Return one exact top-level Coolify service hint for every expected node.

    Helper containers often mention a node name in their command, description, or
    generated name.  A live topology seal must not accidentally bind a helper, so
    this helper accepts only inventory rows whose service name exactly matches the
    Mother node identity.
    """

    expected = [_identifier(node, "live topology node") for node in nodes]
    hints = detection.get("observed_service_hints")
    if not isinstance(hints, list):
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_INVENTORY_MISSING", "live Coolify inventory hints are missing")

    by_node: dict[str, list[Mapping[str, Any]]] = {node: [] for node in expected}
    for item in hints:
        if not isinstance(item, Mapping) or item.get("error"):
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        for node in expected:
            if name == node:
                by_node[node].append(item)

    missing = [node for node, matches in by_node.items() if not matches]
    if missing:
        raise _fail(
            "MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_NODE_MISSING",
            "live Coolify inventory lacks exact primary service rows for: " + ", ".join(missing),
        )

    ambiguous = [node for node, matches in by_node.items() if len(matches) != 1]
    if ambiguous:
        raise _fail(
            "MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_AMBIGUOUS",
            "live Coolify inventory has ambiguous primary service rows for: " + ", ".join(ambiguous),
        )

    result: dict[str, dict[str, Any]] = {}
    for node, matches in by_node.items():
        hint = dict(matches[0])
        uuid = hint.get("uuid")
        controller_id = hint.get("controller_id")
        if not isinstance(uuid, str) or not uuid:
            raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_INVENTORY_INVALID", f"live service UUID is missing for {node}")
        if not isinstance(controller_id, str) or not controller_id:
            raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_INVENTORY_INVALID", f"live controller id is missing for {node}")
        result[node] = hint
    return result


def _live_service_detail_observation(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    node: str,
    hint: Mapping[str, Any],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    controller_id = _identifier(hint.get("controller_id"), f"{node} live controller id")
    service_uuid = str(hint.get("uuid") or "")
    if not service_uuid:
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_INVENTORY_INVALID", f"live service UUID is missing for {node}")
    controller = resolve_coolify_controller(private_state, network, controller_id)
    endpoint = "/api/v1/services/" + urllib.parse.quote(service_uuid, safe="")
    observation = get_coolify_json(
        controller,
        endpoint,
        authenticated=True,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if not (200 <= observation.status < 300):
        raise _fail(
            "MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_SERVICE_UNAVAILABLE",
            f"live service {node} was not readable at {controller_id}/{service_uuid}",
        )
    detail_hints = _service_hints_from_payload(observation.payload)
    exact_name_seen = any(item.get("name") == node for item in detail_hints if isinstance(item, Mapping))
    if detail_hints and not exact_name_seen:
        raise _fail(
            "MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_SERVICE_MISMATCH",
            f"live service detail for {node} did not retain exact node identity",
        )
    service_observation = {
        "node": node,
        "controller_id": controller_id,
        "service_uuid": service_uuid,
        "status": observation.status,
        "service_status": hint.get("status"),
        "response_sha256": observation.response_sha256,
        "byte_length": observation.byte_length,
        "present": True,
        "absent": False,
        "observed_at": observed_at,
    }
    service_record = {
        "node": node,
        "controller_id": controller_id,
        "service_uuid": service_uuid,
        "service_status": hint.get("status"),
        "readiness_source": "live-topology-seal-coolify-inventory",
        "last_observed_at": observed_at,
    }
    return service_observation, service_record


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


def _guardian_service_name(voter: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "-", voter).strip("-").lower()
    return f"mother-add-node-validator-admission-voter-{safe}"


def _record_children(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    for key in ("children", "containers", "services", "applications", "resources"):
        value = record.get(key)
        if isinstance(value, list):
            found.extend(item for item in value if isinstance(item, Mapping))
        elif isinstance(value, Mapping):
            found.extend(item for item in value.values() if isinstance(item, Mapping))
    return found


def _record_component_names(record: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("name", "service", "service_name", "serviceName", "subName"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
    return names


def _record_status(record: Mapping[str, Any]) -> str:
    for key in ("status", "human_status", "state", "health"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _exact_component_status(record: Mapping[str, Any], *, names: Iterable[str]) -> str:
    expected = {str(item) for item in names if str(item)}
    stack = [record]
    while stack:
        item = stack.pop(0)
        if _record_component_names(item) & expected:
            return _record_status(item)
        stack.extend(_record_children(item))
    return "missing"


def _is_dynamic_validator_admission_voter_guardian(name: str) -> bool:
    return str(name).startswith("mother-add-node-validator-admission-voter-")


def _validator_admission_guardian_bindings(source: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    candidate_node = _identifier(source.get("candidate_node"), "candidate node")
    voter_nodes_raw = source.get("voter_nodes")
    if not isinstance(voter_nodes_raw, list):
        raise _fail("MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INVALID", "validator-admission voter nodes are missing")
    voter_nodes = [_identifier(item, "voter node") for item in voter_nodes_raw]
    bindings: dict[str, dict[str, str]] = {
        candidate_node: {
            "controller_id": _identifier(source.get("target_host"), "target controller"),
            "service_uuid": _identifier(source.get("created_service_uuid"), "target service UUID"),
            "guardian_name": "mother-add-node-validator-activation-guardian",
        }
    }
    for node in voter_nodes:
        bindings[node] = {"guardian_name": _guardian_service_name(node)}

    records: list[Mapping[str, Any]] = []
    for key in ("mutation_receipts", "health_observations", "precondition_receipts"):
        value = source.get(key)
        if isinstance(value, list):
            records.extend(item for item in value if isinstance(item, Mapping))

    for record in records:
        node = record.get("node")
        if not isinstance(node, str) or node not in bindings:
            continue
        controller_id = record.get("controller_id")
        service_uuid = record.get("service_uuid")
        if isinstance(controller_id, str) and controller_id:
            bindings[node].setdefault("controller_id", controller_id)
        if isinstance(service_uuid, str) and service_uuid:
            bindings[node].setdefault("service_uuid", service_uuid)

    missing = [
        node
        for node, binding in bindings.items()
        if not binding.get("controller_id") or not binding.get("service_uuid") or not binding.get("guardian_name")
    ]
    if missing:
        raise _fail(
            "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INVALID",
            "validator-admission evidence lacks exact guardian service bindings for: " + ", ".join(sorted(missing)),
        )
    return bindings


def _fresh_validator_admission_guardian_observations(
    source: Mapping[str, Any],
    private_state: PrivateStateReadResult,
    *,
    network: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> tuple[list[dict[str, Any]], bool]:
    observations: list[dict[str, Any]] = []
    all_healthy = True
    expected_final_validators = [_address(item, "source final validator") for item in (source.get("final_validator_set") or [])]
    if not expected_final_validators:
        raise _fail(
            "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INVALID",
            "source validator-admission evidence lacks guardian-verified final validator set",
        )
    source_payload = source.get(_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_FIELD)
    if not _canonical_history_proof_payload_verified(source_payload, expected_validator_set=expected_final_validators):
        raise _fail(
            "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INVALID",
            "source validator-admission evidence lacks an actual canonical block-history proof payload",
        )
    source_payload_sha = _canonical_history_proof_payload_sha256(source_payload) if isinstance(source_payload, Mapping) else None
    if source.get(_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD) != source_payload_sha:
        raise _fail(
            "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INVALID",
            "source validator-admission canonical proof payload digest mismatch",
        )

    for node, binding in sorted(_validator_admission_guardian_bindings(source).items()):
        controller_id = _identifier(binding["controller_id"], f"{node} guardian controller")
        service_uuid = _identifier(binding["service_uuid"], f"{node} guardian service UUID")
        guardian_name = _identifier(binding["guardian_name"], f"{node} guardian name")
        controller = resolve_coolify_controller(private_state, network, controller_id)
        endpoint = "/api/v1/services/" + urllib.parse.quote(service_uuid, safe="")
        observation = get_coolify_json(
            controller,
            endpoint,
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        payload = observation.payload if isinstance(observation.payload, Mapping) else {}
        service_status = _record_status(payload)
        guardian_status = _exact_component_status(payload, names=[guardian_name])
        fresh_recheck_required = not _is_dynamic_validator_admission_voter_guardian(guardian_name)
        component_healthy = 200 <= observation.status < 300 and guardian_status == "running:healthy"
        proof_payload: Mapping[str, Any] | None = None
        proof_payload_sha: str | None = None
        proof_payload_missing_fields: list[str] = []
        proof_endpoint_response: dict[str, Any] | None = None
        proof_payload_source = "not_required"
        proof_payload_verified = not fresh_recheck_required
        proof_payload_status = "not_required"
        if fresh_recheck_required:
            proof_payload = _find_canonical_history_proof_payload(payload)
            if isinstance(proof_payload, Mapping):
                proof_payload_source = "coolify-service-detail"
            else:
                proof_payload, proof_endpoint_response = _fetch_candidate_activation_proof_payload(
                    source.get("candidate_activation_proof_endpoint") if isinstance(source.get("candidate_activation_proof_endpoint"), Mapping) else None,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                if isinstance(proof_payload, Mapping):
                    proof_payload_source = "candidate-activation-proof-endpoint"
            if not isinstance(proof_payload, Mapping):
                proof_payload_status = "missing"
                proof_payload_missing_fields = list(_CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS)
                proof_payload_verified = False
            else:
                proof_payload_sha = _canonical_history_proof_payload_sha256(proof_payload)
                proof_payload_missing_fields = _canonical_history_proof_payload_missing_fields(proof_payload)
                proof_payload_verified = _canonical_history_proof_payload_verified(
                    proof_payload,
                    expected_validator_set=expected_final_validators,
                )
                if proof_payload_verified and proof_payload_sha == source_payload_sha:
                    proof_payload_status = "observed-current"
                elif proof_payload_verified:
                    proof_payload_status = "observed-current-different-payload"
                elif proof_payload_missing_fields:
                    proof_payload_status = "missing-fields"
                else:
                    proof_payload_status = "mismatch"
        guardian_healthy = True and proof_payload_verified
        # Dynamic validator-admission voters are one-shot execution helpers.  Their
        # durable vote proof is validated in the source admission evidence before
        # cleanup; post-admission topology must freshly re-check the continuous
        # candidate activation guardian with an actual canonical proof payload,
        # not just a Coolify healthy status.
        all_healthy = all_healthy and (guardian_healthy or not fresh_recheck_required)
        observation_state = {
            "node": node,
            "controller_id": controller_id,
            "service_uuid": service_uuid,
            "endpoint": endpoint,
            "status": observation.status,
            "service_status": service_status,
            "proof_guardian_name": guardian_name,
            "proof_guardian_status": guardian_status,
            "proof_guardian_component_healthy": component_healthy,
            "proof_guardian_healthy": guardian_healthy,
            "fresh_recheck_required": fresh_recheck_required,
            "fresh_recheck_satisfied": guardian_healthy or not fresh_recheck_required,
            "guardian_proof_payload_status": proof_payload_status,
            "guardian_proof_payload_source": proof_payload_source,
            "guardian_proof_payload_missing_fields": proof_payload_missing_fields,
            "guardian_proof_payload_verified": proof_payload_verified,
            "guardian_proof_payload_sha256": proof_payload_sha,
            "guardian_proof_endpoint_response": proof_endpoint_response,
            "expected_source_proof_payload_sha256": source_payload_sha if fresh_recheck_required else None,
            "response_sha256": observation.response_sha256,
            "byte_length": observation.byte_length,
            "observed_at": _timestamp(),
        }
        if isinstance(proof_payload, Mapping):
            latest_values = proof_payload.get("latest_validator_set")
            if isinstance(latest_values, list):
                observation_state["guardian_proof_latest_validator_set"] = [str(item) for item in latest_values]
        observations.append(observation_state)
    return observations, all_healthy


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
    live_node_hints = sorted(
        set(
            node
            for hint in inventory_hints
            if _service_hint_is_live(hint)
            for node in hint.get("node_hints", [])
        )
    )
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


def build_fresh_empty_topology_rectification_evidence(
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
    """Adopt an empty live topology for an operator-declared fresh chain reset.

    Unlike ``adopt-empty-current-topology``, this intentionally breaks genesis
    lineage.  It is the safe command for "I deleted all super nodes and want the
    next add-node to create a new first-validator genesis" because the produced
    topology does not carry forward the previous validator-bearing genesis hash.
    """

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
            "fresh empty topology reset does not accept --actual-node values; the live network must be empty",
        )
    if detection["summary"]["rectification_required"] is not True:
        if detection["observed_live_node_hints"] or detection["present_expected_nodes"] or detection["unknown_expected_nodes"]:
            raise _fail(
                "MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_NOT_YET_IMPLEMENTED",
                "fresh empty topology reset requires every previous topology service to be absent",
            )
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_REFUSED", "topology evidence is not stale-empty")
    if detection["observed_live_node_hints"]:
        raise _fail(
            "MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_NOT_YET_IMPLEMENTED",
            "fresh empty topology reset detected live node hints; aborting instead of breaking genesis lineage",
        )

    completed = _timestamp(now=now)
    target = detection.get("target")
    final_topology = {
        "source": "read-only-live-empty-topology-fresh-chain-reset",
        "chain_id": detection["chain_id"],
        "genesis_sha256": None,
        "genesis_lineage": "fresh-required",
        "fresh_genesis_required": True,
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
        "mode": "manual-fresh-empty-topology-reset",
        "chain_id": detection["chain_id"],
        "genesis_sha256": None,
        "genesis_lineage": "fresh-required",
        "fresh_genesis_required": True,
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
            "operation": "manual-fresh-empty-topology-reset",
            "added_nodes": [],
            "removed_nodes": list(detection["expected_nodes"]),
            "unchanged_nodes": [],
            "pre_validator_count": len(detection["expected_validator_set"]),
            "post_validator_count": 0,
        },
        "authority": {
            "read_only_rectification": True,
            "fresh_chain_reset_declared": True,
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
            "chain_mutation_performed": False,
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
            "fresh_chain_reset": True,
            "fresh_genesis_required": True,
            "old_genesis_reused": False,
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
        raise _fail("MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_SENSITIVE", "fresh rectification evidence contains sensitive material")
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


def adopt_fresh_empty_topology(
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
    evidence = build_fresh_empty_topology_rectification_evidence(
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



def build_live_current_topology_evidence(
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
    """Seal the live non-empty topology observed in Coolify.

    By default the live node set must exactly match the acknowledged topology.
    When ``actual_nodes`` is supplied, it is an explicit operator declaration
    of the intended live subset.  The declaration must exactly match live
    Coolify primary-node inventory and may contain only identities already
    present in the acknowledged topology.  Validator identities are selected
    positionally from that acknowledged topology; no new identity is inferred.
    """

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
    summary = detection.get("summary")
    if not isinstance(summary, Mapping):
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_INVALID", "topology detection summary is missing")
    if _observed_inventory_errors(detection):
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_INVENTORY_ERROR", "live Coolify inventory had unreadable controllers")
    if detection.get("unexpected_live_nodes"):
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_UNEXPECTED_NODE", "live Coolify inventory contains unexpected Mother nodes")
    if detection.get("unknown_expected_nodes"):
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_UNKNOWN_EXPECTED_NODE", "expected service status was neither present nor absent")

    nodes = [_identifier(item, "expected topology node") for item in detection.get("expected_nodes", [])]
    validators = [_address(item, "expected topology validator") for item in detection.get("expected_validator_set", [])]
    if not nodes:
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_EMPTY_REFUSED", "use empty-topology rectification for empty live topology")
    if len(nodes) != len(validators):
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_INVALID", "expected nodes and validators are not aligned")
    if len(set(nodes)) != len(nodes) or len(set(validators)) != len(validators):
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_DUPLICATE", "expected topology contains duplicate nodes or validators")

    live_node_hints = [_identifier(item, "live node hint") for item in detection.get("observed_live_node_hints", [])]
    operator_actual_nodes = [_identifier(item, "actual node") for item in actual_nodes if str(item or "").strip()]
    if len(set(operator_actual_nodes)) != len(operator_actual_nodes):
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_ACTUAL_NODE_INVALID", "--actual-node contains duplicates")

    source_nodes = list(nodes)
    source_validators = list(validators)
    source_validator_by_node = {node: source_validators[index] for index, node in enumerate(source_nodes)}
    live_node_set = set(live_node_hints)

    if operator_actual_nodes:
        actual_node_set = set(operator_actual_nodes)
        unknown_actual_nodes = sorted(actual_node_set - set(source_nodes))
        if unknown_actual_nodes:
            raise _fail(
                "MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_ACTUAL_NODE_INVALID",
                "--actual-node is outside the acknowledged topology: " + ", ".join(unknown_actual_nodes),
            )
        if actual_node_set != live_node_set:
            missing = sorted(actual_node_set - live_node_set)
            extra = sorted(live_node_set - actual_node_set)
            pieces = []
            if missing:
                pieces.append("declared but not live: " + ", ".join(missing))
            if extra:
                pieces.append("live but not declared: " + ", ".join(extra))
            raise _fail(
                "MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_ACTUAL_NODE_MISMATCH",
                "; ".join(pieces) or "--actual-node does not match live Coolify inventory",
            )
        nodes = [node for node in source_nodes if node in actual_node_set]
        validators = [source_validator_by_node[node] for node in nodes]
    elif live_node_set != set(source_nodes):
        missing = sorted(set(source_nodes) - live_node_set)
        extra = sorted(live_node_set - set(source_nodes))
        pieces = []
        if missing:
            pieces.append("missing live nodes: " + ", ".join(missing))
        if extra:
            pieces.append("unexpected live nodes: " + ", ".join(extra))
        raise _fail(
            "MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_NODE_SET_MISMATCH",
            "; ".join(pieces) or "live node set does not match acknowledged topology",
        )

    if not nodes:
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_EMPTY_REFUSED", "use empty-topology rectification for empty live topology")

    subset_seal = set(nodes) != set(source_nodes)
    primary_hints = _primary_live_service_hints_by_node(detection, nodes)
    completed = _timestamp(now=now)
    old_services_raw = detection.get("expected_services")
    old_services = old_services_raw if isinstance(old_services_raw, Mapping) else {}

    services: dict[str, dict[str, Any]] = {}
    live_observations: list[dict[str, Any]] = []
    refreshed_nodes: list[str] = []
    service_uuid_changes: list[dict[str, Any]] = []
    for node in nodes:
        hint = primary_hints[node]
        observation, live_record = _live_service_detail_observation(
            private_state,
            network=network,
            node=node,
            hint=hint,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            observed_at=completed,
        )
        live_observations.append(observation)
        previous = old_services.get(node)
        if isinstance(previous, Mapping):
            for key in ("validator_route", "p2p_route", "vpn_ip", "p2p_port", "p2p_endpoint"):
                if key in previous and key not in live_record:
                    live_record[key] = previous[key]
            old_uuid = str(previous.get("service_uuid") or "")
            if old_uuid != live_record["service_uuid"]:
                refreshed_nodes.append(node)
                service_uuid_changes.append({
                    "node": node,
                    "previous_controller_id": previous.get("controller_id"),
                    "previous_service_uuid": old_uuid,
                    "live_controller_id": live_record["controller_id"],
                    "live_service_uuid": live_record["service_uuid"],
                })
        services[node] = _topology_service_record(
            node,
            live_record,
            source="live-topology-seal-coolify-inventory",
            completed_at=completed,
        )

    target = detection.get("target")
    live_target = None
    if isinstance(target, Mapping):
        target_node = target.get("node")
        if isinstance(target_node, str) and target_node in services and target_node in nodes:
            live_target = {
                "node": target_node,
                "controller_id": services[target_node]["controller_id"],
                "service_uuid": services[target_node]["service_uuid"],
                "previous_service_uuid": target.get("service_uuid") or target.get("previous_service_uuid") or "",
                "validator_address": validators[nodes.index(target_node)],
            }
    if live_target is None:
        live_target = target

    topology = {
        "source": (
            "read-only-live-current-topology-subset-seal"
            if subset_seal
            else "read-only-live-current-topology-seal"
        ),
        "chain_id": detection.get("chain_id"),
        "genesis_sha256": detection.get("genesis_sha256"),
        "nodes": nodes,
        "services": services,
        "validator_count": len(validators),
        "validator_set": validators,
        "baseline_topology_used_as_live": False,
        "validator_set_preserved_from_source_topology": not subset_seal,
        "validator_identities_selected_from_source_topology": True,
        "operator_declared_actual_nodes": list(operator_actual_nodes),
        "service_uuid_refreshed_nodes": sorted(refreshed_nodes),
    }

    evidence: dict[str, Any] = {
        "kind": _LIVE_CURRENT_TOPOLOGY_KIND,
        "schema_version": 1,
        "completed_at": completed,
        "observed_at": detection.get("observed_at"),
        "status": "pass",
        "failure": None,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": (
            "read-only-live-current-topology-subset-seal"
            if subset_seal
            else "read-only-live-current-topology-seal"
        ),
        "source_previous_topology_evidence": dict(detection.get("topology_evidence") or {}),
        "staleness_detection": {
            "status": detection.get("status"),
            "summary": dict(summary),
            "expected_nodes": list(nodes),
            "expected_services": dict(detection.get("expected_services") or {}),
            "expected_validator_set": list(validators),
            "present_expected_nodes": list(detection.get("present_expected_nodes") or []),
            "missing_expected_nodes": list(detection.get("missing_expected_nodes") or []),
            "observed_live_node_hints": list(detection.get("observed_live_node_hints") or []),
            "operator_declared_actual_nodes": list(operator_actual_nodes),
            "unexpected_live_nodes": list(detection.get("unexpected_live_nodes") or []),
            "observed_service_hints": list(detection.get("observed_service_hints") or []),
            "network_access_performed": True,
        },
        "service_observations": live_observations,
        "service_uuid_changes": service_uuid_changes,
        "target": live_target,
        "current_topology": topology,
        "final_topology": topology,
        "topology_diff": {
            "operation": (
                "read-only-live-current-topology-subset-seal"
                if subset_seal
                else "read-only-live-current-topology-seal"
            ),
            "added_nodes": [],
            "removed_nodes": [node for node in source_nodes if node not in set(nodes)],
            "unchanged_nodes": list(nodes),
            "service_uuid_refreshed_nodes": sorted(refreshed_nodes),
            "pre_validator_count": len(source_validators),
            "post_validator_count": len(validators),
        },
        "authority": {
            "read_only_topology_seal": True,
            "use_live_topology": True,
            "live_coolify_primary_services_verified": True,
            "node_identity_preserved": True,
            "validator_set_preserved_from_source_topology": not subset_seal,
            "validator_identities_selected_from_source_topology": True,
            "operator_declared_actual_nodes": list(operator_actual_nodes),
            "live_mutation_authorized": False,
        },
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "finalize_mutation_performed": False,
            "chain_mutation_performed": False,
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
            "live_topology_sealed": True,
            "use_live_topology": True,
            "current_topology_marked_by_evidence": True,
            "topology_current": True,
            "topology_stale": False,
            "node_identity_preserved": True,
            "validator_set_preserved_from_source_topology": not subset_seal,
            "validator_identities_selected_from_source_topology": True,
            "operator_declared_actual_nodes": list(operator_actual_nodes),
            "live_coolify_primary_services_verified": True,
            "service_uuid_refreshed_nodes": sorted(refreshed_nodes),
            "final_nodes": nodes,
            "final_validator_count": len(validators),
            "final_validator_set": validators,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "chain_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "next_phase": f"topology-baseline-ready-{network}",
        },
        "live_mutation_performed": False,
        "chain_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": f"topology-baseline-ready-{network}",
    }
    if _contains_sensitive(evidence):
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_SENSITIVE", "live topology seal evidence contains sensitive material")
    evidence["live_current_topology_sha256"] = _digest_without(evidence, "live_current_topology_sha256")
    return evidence


def write_live_current_topology_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if evidence.get("kind") != _LIVE_CURRENT_TOPOLOGY_KIND:
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_INVALID", "not a live current topology evidence document")
    document = dict(evidence)
    digest = _digest_without(document, "live_current_topology_sha256")
    if document.get("live_current_topology_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_INVALID", "live current topology evidence digest mismatch")
    payload = canonical_json(document)
    root = _ensure_directory(paths, _LIVE_CURRENT_TOPOLOGY_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", ""))) or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"{stamp}-{document.get('network', 'mainnet')}-live-current-topology-{digest[:16]}.json"
    atomic_files.durable_create(path, payload, operation=operation)
    return path, hashlib.sha256(payload).hexdigest()


def seal_live_current_topology(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    topology_evidence_path: Path,
    *,
    network: str = "mainnet",
    acknowledged_topology_evidence_sha256: str,
    actual_nodes: Iterable[str] = (),
    use_live_topology: bool = False,
    max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = 4 * 1024 * 1024,
    write_evidence: bool = False,
    operation: OperationIdentity,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
) -> dict[str, Any]:
    if use_live_topology is not True:
        raise _fail("MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_ACK_REQUIRED", "--use-live-topology is required to seal from live Coolify inventory")
    evidence = build_live_current_topology_evidence(
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
        evidence_path, evidence_sha = write_live_current_topology_evidence(
            paths,
            evidence,
            operation=operation,
        )
        evidence = {**evidence, "evidence": {"path": str(evidence_path), "sha256": evidence_sha}}
    return evidence


def build_add_node_post_admission_topology_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    validator_admission_evidence_path: Path,
    *,
    network: str = "mainnet",
    acknowledged_validator_admission_evidence_sha256: str,
    max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = 4 * 1024 * 1024,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the read-only topology proof that closes an existing-topology add-node.

    Validator admission is a live mutation proof, so ``add-node prep`` correctly
    rejects it as the baseline for the next add.  This function re-observes the
    live topology from that clean admission proof and writes a non-mutating
    topology evidence document whose canonical JSON digest can be supplied to
    the next ``add-node prep`` call.
    """

    resolved = Path(validator_admission_evidence_path).resolve(strict=False)
    source, _source_raw, source_digest = _canonical_file(resolved)
    expected = _sha256(acknowledged_validator_admission_evidence_sha256, "validator-admission evidence SHA-256")
    if source_digest != expected:
        raise _fail(
            "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_ACK_MISMATCH",
            "validator-admission evidence SHA-256 mismatch",
        )
    if source.get("kind") != _ADD_VALIDATOR_ADMISSION_KIND:
        raise _fail(
            "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INVALID",
            "source evidence is not add-node validator-admission evidence",
        )
    expected_next_phase = f"add-node-post-admission-observe-{network}"
    if source.get("next_phase") != expected_next_phase:
        raise _fail(
            "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INVALID",
            f"validator-admission evidence is not ready for {expected_next_phase}",
        )

    from .deployment_node_add_validator_admission import (
        MotherDeploymentNodeAddValidatorAdmissionError,
        verify_node_add_validator_admission_evidence,
    )

    try:
        admission_verification = verify_node_add_validator_admission_evidence(
            paths,
            private_state,
            resolved,
            max_age_seconds=max_age_seconds,
            now=now,
        )
    except MotherDeploymentNodeAddValidatorAdmissionError as exc:
        raise _fail(
            "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INVALID",
            "source validator-admission evidence does not prove guardian-verified final validator set",
        ) from exc

    fresh_guardian_observations, fresh_guardians_healthy = _fresh_validator_admission_guardian_observations(
        source,
        private_state,
        network=network,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if not fresh_guardians_healthy:
        raise _fail(
            "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_UNCLEAN",
            "fresh validator-admission guardian proof is not healthy/current on every expected validator",
        )

    detection = detect_topology_staleness(
        paths,
        private_state,
        resolved,
        network=network,
        acknowledged_topology_evidence_sha256=source_digest,
        max_age_seconds=max_age_seconds,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        now=now,
    )
    summary = detection.get("summary")
    if not isinstance(summary, Mapping):
        raise _fail("MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INVALID", "topology observation summary is missing")
    if (
        detection.get("status") != "pass"
        or summary.get("clean") is not True
        or summary.get("topology_current") is not True
        or summary.get("topology_stale") is True
        or summary.get("manual_review_required") is True
    ):
        raise _fail(
            "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_UNCLEAN",
            "live topology is not clean/current after validator admission",
        )

    nodes = [_identifier(item, "observed node") for item in detection.get("expected_nodes", [])]
    validators = [_address(item, "observed validator") for item in detection.get("expected_validator_set", [])]
    verified_final_validators = [_address(item, "verified final validator") for item in admission_verification.get("final_validator_set", [])]
    if sorted(validators) != sorted(verified_final_validators):
        raise _fail(
            "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_UNCLEAN",
            "observed topology validator set does not match guardian-verified validator-admission final set",
        )
    if len(nodes) != len(validators) or not nodes:
        raise _fail(
            "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INCOMPLETE",
            "observed topology nodes and validators are missing or not aligned",
        )
    services_raw = detection.get("expected_services")
    if not isinstance(services_raw, Mapping):
        raise _fail("MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INCOMPLETE", "observed service records are missing")
    services: dict[str, dict[str, Any]] = {}
    for node in nodes:
        record = services_raw.get(node)
        if not isinstance(record, Mapping):
            raise _fail("MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INCOMPLETE", f"observed service record missing for {node}")
        services[node] = _topology_service_record(
            node,
            record,
            source="post-admission-topology-observation",
            completed_at=detection.get("observed_at"),
        )

    completed = _timestamp(now=now)
    target = detection.get("target")
    topology = {
        "source": "add-node-post-admission-observe",
        "chain_id": detection.get("chain_id"),
        "genesis_sha256": _sha256(detection.get("genesis_sha256"), "observed genesis SHA-256"),
        "nodes": nodes,
        "services": services,
        "validator_count": len(validators),
        "validator_set": validators,
        "validator_admission_previously_performed": True,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
    }

    evidence: dict[str, Any] = {
        "kind": _ADD_POST_ADMISSION_TOPOLOGY_KIND,
        "schema_version": 1,
        "completed_at": completed,
        "observed_at": detection.get("observed_at"),
        "status": "pass",
        "failure": None,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": source.get("mode"),
        "source_validator_admission_evidence": {
            "path": str(resolved),
            "locator": _relative(paths, resolved, label="validator-admission evidence"),
            "sha256": source_digest,
            "kind": source.get("kind"),
            "completed_at": source.get("completed_at"),
        },
        "live_topology_observation": {
            "status": detection.get("status"),
            "observed_at": detection.get("observed_at"),
            "summary": dict(summary),
            "topology_evidence": dict(detection.get("topology_evidence") or {}),
        },
        "fresh_validator_admission_guardian_observations": fresh_guardian_observations,
        "service_observations": list(detection.get("expected_service_observations") or []),
        "observed_live_node_hints": list(detection.get("observed_live_node_hints") or []),
        "target": target,
        "current_topology": topology,
        "final_topology": topology,
        "topology_diff": {
            "operation": "add-node-post-admission-observe",
            "added_nodes": [source.get("candidate_node")] if isinstance(source.get("candidate_node"), str) else [],
            "removed_nodes": [],
            "unchanged_nodes": [node for node in nodes if node != source.get("candidate_node")],
            "pre_validator_count": len(source.get("current_validator_set") or []),
            "post_validator_count": len(validators),
        },
        "authority": {
            "read_only_post_admission_observe": True,
            "validator_admission_previously_proven": True,
            "target_service_top_level_healthy_previously_proven": True,
            "post_admission_cleanup_previously_proven": True,
            "fresh_validator_admission_guardians_verified": True,
            "topology_current": True,
            "live_mutation_authorized": False,
        },
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "finalize_mutation_performed": False,
            "chain_mutation_performed": False,
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
            "current_topology_marked_by_evidence": True,
            "source_validator_admission_clean": True,
            "fresh_validator_admission_guardians_verified": True,
            "topology_current": True,
            "topology_stale": False,
            "final_nodes": nodes,
            "final_validator_count": len(validators),
            "final_validator_set": validators,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "chain_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "next_phase": f"add-node-prep-{network}",
        },
        "live_mutation_performed": False,
        "chain_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": f"add-node-prep-{network}",
    }
    if _contains_sensitive(evidence):
        raise _fail("MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_SENSITIVE", "post-admission topology evidence contains sensitive material")
    evidence["add_node_post_admission_topology_sha256"] = _digest_without(evidence, "add_node_post_admission_topology_sha256")
    return evidence


def write_add_node_post_admission_topology_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if evidence.get("kind") != _ADD_POST_ADMISSION_TOPOLOGY_KIND:
        raise _fail("MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INVALID", "not an add-node post-admission topology evidence document")
    document = dict(evidence)
    digest = _digest_without(document, "add_node_post_admission_topology_sha256")
    if document.get("add_node_post_admission_topology_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INVALID", "post-admission topology evidence digest mismatch")
    payload = canonical_json(document)
    root = _ensure_directory(paths, _ADD_POST_ADMISSION_TOPOLOGY_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", ""))) or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = document.get("target")
    target_node = target.get("node") if isinstance(target, Mapping) else None
    suffix = f"-from-{target_node}" if isinstance(target_node, str) and target_node else ""
    path = root / f"{stamp}-{document.get('network', 'mainnet')}-topology-finalize{suffix}.json"
    atomic_files.durable_create(path, payload, operation=operation)
    return path, hashlib.sha256(payload).hexdigest()


def finalize_add_node_post_admission_topology(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    validator_admission_evidence_path: Path,
    *,
    network: str = "mainnet",
    acknowledged_validator_admission_evidence_sha256: str,
    max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = 4 * 1024 * 1024,
    write_evidence: bool = False,
    operation: OperationIdentity,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
) -> dict[str, Any]:
    evidence = build_add_node_post_admission_topology_evidence(
        paths,
        private_state,
        validator_admission_evidence_path,
        network=network,
        acknowledged_validator_admission_evidence_sha256=acknowledged_validator_admission_evidence_sha256,
        max_age_seconds=max_age_seconds,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        now=now,
    )
    canonical_sha = hashlib.sha256(canonical_json(evidence)).hexdigest()
    if write_evidence:
        evidence_path, evidence_sha = write_add_node_post_admission_topology_evidence(
            paths,
            evidence,
            operation=operation,
        )
        from .deployment_node_add_prep import _load_baseline

        _load_baseline(
            paths,
            private_state,
            evidence_path,
            network=network,
            expected_sha256=evidence_sha,
            max_age_seconds=max_age_seconds,
            now=now,
        )
        evidence = {
            **evidence,
            "evidence": {"path": str(evidence_path), "sha256": evidence_sha},
            "prep_baseline_loader_accepted": True,
        }
    else:
        evidence = {**evidence, "evidence_sha256": canonical_sha}
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
    "adopt_fresh_empty_topology",
    "build_add_node_post_admission_topology_evidence",
    "build_live_current_topology_evidence",
    "build_empty_topology_rectification_evidence",
    "build_fresh_empty_topology_rectification_evidence",
    "detect_topology_staleness",
    "finalize_add_node_post_admission_topology",
    "seal_live_current_topology",
    "verify_empty_topology_rectification_evidence",
    "write_add_node_post_admission_topology_evidence",
    "write_empty_topology_rectification_evidence",
    "write_live_current_topology_evidence",
]
