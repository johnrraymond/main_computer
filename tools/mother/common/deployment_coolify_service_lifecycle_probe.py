"""Harmless Coolify service lifecycle probe for the Mother deployment operator.

The probe creates one temporary no-secret, no-chain service, starts it, observes
documented service and deployment inventory channels, and deletes it.  It never
binds a wallet key, contacts validator RPC, opens a port, or creates a volume.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import quote

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_post_admission_steady_state import (
    _binding,
    _canonical_under,
    _contains_sensitive,
    _ensure_root,
    _parse_utc,
    _timestamp,
)
from .deployment_private_rpc import _controller_config
from .deployment_validator_admission_executor import _http
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_EVIDENCE_KIND = "main_computer.mother.deployment_coolify_service_lifecycle_probe_evidence.v4"
_LEGACY_EVIDENCE_KIND = "main_computer.mother.deployment_coolify_service_lifecycle_probe_evidence.v3"
_EVIDENCE_DIRECTORY = ("evidence", "deployment-coolify-service-lifecycle-probes")
_ACKNOWLEDGEMENT = "NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE"
_ALLOWED_CONTROLLERS = frozenset({"coolify-a", "coolify-c"})
_IMAGE = "ghcr.io/foundry-rs/foundry:latest"
_UUID_RE = re.compile(r"^[A-Za-z0-9_-]{8,96}$")


class MotherDeploymentCoolifyServiceLifecycleProbeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error(code: str, message: str) -> MotherDeploymentCoolifyServiceLifecycleProbeError:
    return MotherDeploymentCoolifyServiceLifecycleProbeError(code, message)


def _validate(
    *,
    network: str,
    controller_id: str,
    environment_name: str,
    observe_seconds: float,
    poll_interval_seconds: float,
) -> None:
    if network != "mainnet":
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_NETWORK_INVALID",
            "the lifecycle probe is restricted to mainnet controller metadata",
        )
    if controller_id not in _ALLOWED_CONTROLLERS:
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_CONTROLLER_INVALID",
            "controller_id must be coolify-a or coolify-c",
        )
    if environment_name != "mainnet":
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_ENVIRONMENT_INVALID",
            "the lifecycle probe is restricted to the mainnet environment",
        )
    if not (0 <= observe_seconds <= 300):
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_TIMING_INVALID",
            "observe_seconds must be between 0 and 300",
        )
    if not (0 <= poll_interval_seconds <= 60):
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_TIMING_INVALID",
            "poll_interval_seconds must be between 0 and 60",
        )


def _probe_name(controller_id: str, operation: OperationIdentity) -> str:
    suffix = hashlib.sha256(operation.operation_id.encode("utf-8")).hexdigest()[:10]
    side = controller_id.rsplit("-", 1)[-1]
    return f"mother-lifecycle-probe-{side}-{suffix}"


def _compose(name: str, observe_seconds: float) -> str:
    linger_seconds = max(90, int(observe_seconds) + 60)
    text = (
        f"name: {name}\n\n"
        "services:\n"
        f"  {name}:\n"
        f"    image: {_IMAGE}\n"
        "    restart: \"no\"\n"
        "    read_only: true\n"
        "    entrypoint:\n"
        "      - /bin/sh\n"
        "      - -ec\n"
        "    command:\n"
        "      - |\n"
        f"        printf 'MOTHER_COOLIFY_SERVICE_LIFECYCLE_PROBE_READY={name}\\n'\n"
        f"        exec sleep {linger_seconds}\n"
        "    healthcheck:\n"
        "      test:\n"
        "        - CMD-SHELL\n"
        "        - grep -aq 'sleep' /proc/1/cmdline\n"
        "      interval: 2s\n"
        "      timeout: 2s\n"
        "      retries: 10\n"
        "      start_period: 2s\n"
        "    labels:\n"
        "      main_computer.mother.stage: coolify-service-lifecycle-probe\n"
        f"      main_computer.mother.probe: {name}\n"
    )
    parsed = yaml.safe_load(text)
    services = parsed.get("services") if isinstance(parsed, Mapping) else None
    if not isinstance(services, Mapping) or list(services) != [name]:
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_COMPOSE_INVALID",
            "probe Compose is malformed",
        )
    service = services[name]
    if not (
        isinstance(service, Mapping)
        and service.get("entrypoint") == ["/bin/sh", "-ec"]
        and type(service.get("command")) is list
        and len(service["command"]) == 1
        and "exec sleep " in str(service["command"][0])
        and isinstance(service.get("healthcheck"), Mapping)
        and service["healthcheck"].get("test")
        == ["CMD-SHELL", "grep -aq 'sleep' /proc/1/cmdline"]
    ):
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_COMPOSE_INVALID",
            "probe Compose does not override the image entrypoint with one exact shell script",
        )
    forbidden = ("ports:", "volumes:", "secrets:", "traefik.", "http://", "https://")
    if any(item in text for item in forbidden):
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_COMPOSE_INVALID",
            "probe Compose attempts a forbidden capability",
        )
    return text


def _records(payload: Any) -> list[Mapping[str, Any]]:
    if type(payload) is list:
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        result: list[Mapping[str, Any]] = [payload]
        for key in ("data", "services", "resources", "deployments", "environments"):
            nested = payload.get(key)
            if type(nested) is list:
                result.extend(item for item in nested if isinstance(item, Mapping))
            elif isinstance(nested, Mapping):
                result.append(nested)
        return result
    return []


def _one_uuid(payload: Any) -> str:
    found: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, value in item.items():
                if str(key) in {"uuid", "service_uuid"} and type(value) is str:
                    clean = value.strip()
                    if _UUID_RE.fullmatch(clean):
                        found.add(clean)
                elif isinstance(value, (Mapping, list)):
                    walk(value)
        elif type(item) is list:
            for value in item:
                walk(value)

    walk(payload)
    if len(found) != 1:
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_SERVICE_INVALID",
            "service creation did not return exactly one usable service UUID",
        )
    return next(iter(found))


def _environment_uuid(payload: Any, expected_name: str) -> str:
    matches: set[str] = set()
    for item in _records(payload):
        name = item.get("name")
        uuid = item.get("uuid")
        if name == expected_name and type(uuid) is str and _UUID_RE.fullmatch(uuid.strip()):
            matches.add(uuid.strip())
    if len(matches) != 1:
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_ENVIRONMENT_INVALID",
            "environment inventory did not return exactly one expected environment UUID",
        )
    return next(iter(matches))


def _safe_subset(record: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "uuid",
        "name",
        "status",
        "type",
        "service_type",
        "config_hash",
        "created_at",
        "updated_at",
        "deleted_at",
        "environment_id",
        "server_id",
        "destination_type",
        "destination_id",
        "deployment_uuid",
        "application_name",
        "resource_uuid",
    )
    return {key: record.get(key) for key in allowed if key in record}


def _healthy_running_status(value: Any) -> bool:
    """Return true only for Coolify's exact healthy-running status forms."""
    return value in {"running:healthy", "running:healthy:excluded"}


def _matching_records(payload: Any, service_uuid: str, service_name: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in _records(payload):
        if (
            item.get("uuid") == service_uuid
            or item.get("resource_uuid") == service_uuid
            or item.get("application_name") == service_name
            or item.get("name") == service_name
        ):
            matches.append(_safe_subset(item))
    unique = {canonical_json(item): item for item in matches}
    return [unique[key] for key in sorted(unique)]


def _response_record(
    *,
    phase: str,
    controller_id: str,
    method: str,
    endpoint: str,
    response: Mapping[str, Any],
    service_uuid: str | None = None,
    service_name: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "phase": phase,
        "controller_id": controller_id,
        "method": method,
        "endpoint": endpoint,
        "http_status": response.get("status"),
        "response_sha256": response.get("response_sha256"),
        "byte_length": response.get("byte_length"),
        "elapsed_ms": response.get("elapsed_ms"),
    }
    if service_uuid is not None:
        record["service_uuid"] = service_uuid
    if service_name is not None:
        record["service_name"] = service_name
    return record



def _safe_status(value: Any) -> str | None:
    if type(value) is not str:
        return None
    clean = value.strip()
    if not clean or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", clean):
        return None
    return clean


def _collection_shape(value: Any) -> str:
    if value is None:
        return "missing-or-null"
    if type(value) is list:
        return "list"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


def _service_subresource_diagnostics(payload: Any, expected_name: str) -> dict[str, Any]:
    """Return the sanitized service-detail facts used to select the logs sub-resource."""
    inventories: dict[str, list[dict[str, Any]]] = {
        "applications": [],
        "databases": [],
    }
    shapes: dict[str, str] = {}
    names: set[str] = set()

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
                    if field == "status" and _safe_status(clean) is None:
                        continue
                    if field == "type" and not re.fullmatch(r"[A-Za-z0-9_.\\:-]{1,255}", clean):
                        continue
                    safe[field] = clean
                inventories[key].append(safe)
                name = safe.get("name")
                if type(name) is str:
                    names.add(name)
    else:
        shapes = {key: "service-detail-not-object" for key in inventories}

    candidate_names = sorted(names)
    selected: str | None = None
    selection_reason = "no-candidates"
    if expected_name in names:
        selected = expected_name
        selection_reason = "expected-name-match"
    elif len(candidate_names) == 1:
        selected = candidate_names[0]
        selection_reason = "single-candidate"
    elif candidate_names:
        selection_reason = "ambiguous-candidates"

    return {
        "service_detail_shape": "object" if isinstance(payload, Mapping) else _collection_shape(payload),
        "subresource_collection_shapes": shapes,
        "subresources": inventories,
        "candidate_sub_service_names": candidate_names,
        "selected_sub_service_name": selected,
        "selection_reason": selection_reason,
        "service_detail_status": (
            _safe_status(payload.get("status")) if isinstance(payload, Mapping) else None
        ),
    }


def _service_subresource_name(payload: Any, expected_name: str) -> str | None:
    """Resolve the exact Coolify service application name required by the logs API."""
    return _service_subresource_diagnostics(payload, expected_name)[
        "selected_sub_service_name"
    ]


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
        if "container not found" in normalized:
            return "container-not-found"
        if "resource not found" in normalized or "service not found" in normalized:
            return "resource-not-found"
        return "not-found"
    if status == 422:
        return "validation-error"
    if status == 429:
        return "rate-limited"
    if type(status) is int and status >= 500:
        return "server-error"
    return "http-error"


def _runtime_log_channel(
    *,
    controller: Any,
    controller_id: str,
    service_uuid: str,
    service_name: str,
    service_detail_payload: Any,
    service_detail_http_status: Any,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observation_sequence: int,
    observation_started_at: str,
) -> dict[str, Any]:
    diagnostics = _service_subresource_diagnostics(
        service_detail_payload,
        service_name,
    )
    sub_service_name = diagnostics["selected_sub_service_name"]
    endpoint_base = f"/api/v1/services/{service_uuid}/logs"
    query_parameters = {
        "sub_service_name": sub_service_name,
        "lines": "100",
        "show_timestamps": "false",
    }
    base_record: dict[str, Any] = {
        "phase": "service-lifecycle-observation",
        "controller_id": controller_id,
        "method": "GET",
        "endpoint": endpoint_base,
        "channel": "service-runtime-logs",
        "service_uuid": service_uuid,
        "service_name": service_name,
        "observation_sequence": observation_sequence,
        "observation_started_at": observation_started_at,
        "service_detail_http_status": service_detail_http_status,
        "service_detail_shape": diagnostics["service_detail_shape"],
        "service_detail_status": diagnostics["service_detail_status"],
        "subresource_collection_shapes": diagnostics[
            "subresource_collection_shapes"
        ],
        "subresources": diagnostics["subresources"],
        "candidate_sub_service_names": diagnostics[
            "candidate_sub_service_names"
        ],
        "selected_sub_service_name": sub_service_name,
        "sub_service_name": sub_service_name,
        "selection_reason": diagnostics["selection_reason"],
        "query_parameters": query_parameters,
    }
    marker = f"MOTHER_COOLIFY_SERVICE_LIFECYCLE_PROBE_READY={service_name}"
    if sub_service_name is None:
        return {
            **base_record,
            "sub_service_name_resolved": False,
            "request_path": None,
            "http_status": None,
            "response_sha256": None,
            "byte_length": None,
            "elapsed_ms": None,
            "response_classification": "sub-service-name-unresolved",
            "logs_field_present": False,
            "marker_observed": False,
        }

    request_path = (
        f"{endpoint_base}?sub_service_name={quote(sub_service_name, safe='')}"
        "&lines=100&show_timestamps=false"
    )
    response = _http(
        controller,
        "GET",
        request_path,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    payload = response.get("payload")
    logs = payload.get("logs") if isinstance(payload, Mapping) else None
    return {
        **base_record,
        "sub_service_name_resolved": True,
        "request_path": request_path,
        "http_status": response.get("status"),
        "response_sha256": response.get("response_sha256"),
        "byte_length": response.get("byte_length"),
        "elapsed_ms": response.get("elapsed_ms"),
        "response_classification": _runtime_log_response_classification(response),
        "logs_field_present": type(logs) is str,
        "marker_observed": type(logs) is str and marker in logs,
    }


def _read_observation(
    *,
    controller: Any,
    controller_id: str,
    controller_meta: Mapping[str, Any],
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    sequence: int,
) -> dict[str, Any]:
    channels: list[dict[str, Any]] = []
    service_detail_payload: Any = None
    service_detail_http_status: Any = None
    observation_started_at = _timestamp()

    endpoints = (
        ("service-list", "/api/v1/services"),
        ("service-detail", f"/api/v1/services/{service_uuid}"),
        ("deployment-list", "/api/v1/deployments"),
        ("server-resources", f"/api/v1/servers/{controller_meta['server_uuid']}/resources"),
    )
    for channel, endpoint in endpoints:
        response = _http(
            controller,
            "GET",
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        if channel == "service-detail":
            service_detail_payload = response.get("payload")
            service_detail_http_status = response.get("status")
        item = _response_record(
            phase="service-lifecycle-observation",
            controller_id=controller_id,
            method="GET",
            endpoint=endpoint,
            response=response,
            service_uuid=service_uuid,
            service_name=service_name,
        )
        item["channel"] = channel
        item["matches"] = _matching_records(response.get("payload"), service_uuid, service_name)
        item["match_count"] = len(item["matches"])
        channels.append(item)

    channels.append(
        _runtime_log_channel(
            controller=controller,
            controller_id=controller_id,
            service_uuid=service_uuid,
            service_name=service_name,
            service_detail_payload=service_detail_payload,
            service_detail_http_status=service_detail_http_status,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            observation_sequence=sequence,
            observation_started_at=observation_started_at,
        )
    )

    return {
        "sequence": sequence,
        "observed_at": observation_started_at,
        "channels": channels,
    }


def _mutation_receipt(
    *,
    mutation_id: str,
    controller_id: str,
    method: str,
    endpoint: str,
    response: Mapping[str, Any],
    service_uuid: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mutation_id": mutation_id,
        "controller_id": controller_id,
        "method": method,
        "endpoint": endpoint,
        "http_status": response.get("status"),
        "response_sha256": response.get("response_sha256"),
        "byte_length": response.get("byte_length"),
        "elapsed_ms": response.get("elapsed_ms"),
        "status": "succeeded" if response.get("ok") is True else "failed",
        "live_write_acknowledged": response.get("ok") is True,
    }
    if service_uuid is not None:
        result["service_uuid"] = service_uuid
    payload = response.get("payload")
    if isinstance(payload, Mapping):
        safe_payload = {
            key: payload.get(key)
            for key in ("message", "uuid", "service_uuid", "deployment_uuid")
            if key in payload
        }
        if safe_payload:
            result["response_fields"] = safe_payload
    return result


def inspect_coolify_service_lifecycle_probe(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    environment_name: str,
    observe_seconds: float,
    poll_interval_seconds: float,
    operation: OperationIdentity,
) -> dict[str, Any]:
    _validate(
        network=network,
        controller_id=controller_id,
        environment_name=environment_name,
        observe_seconds=observe_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    controller_meta = _controller_config(
        private_state,
        network=network,
        controller_id=controller_id,
    )
    name = _probe_name(controller_id, operation)
    compose = _compose(name, observe_seconds)
    return {
        "status": "inspection",
        "network": network,
        "controller_id": controller_id,
        "environment_name": environment_name,
        "probe_name": name,
        "authority": {
            "live_execution_authorized": False,
            "network_access_authorized": False,
            "secret_binding_authorized": False,
            "chain_access_authorized": False,
            "public_endpoint_authorized": False,
            "volume_authorized": False,
            "requested_mutation_count": 3,
        },
        "planned_mutations": [
            {"method": "POST", "endpoint": "/api/v1/services", "purpose": "create one temporary probe"},
            {
                "method": "POST",
                "endpoint": "/api/v1/services/{service_uuid}/start",
                "purpose": "start the no-secret long-running probe",
            },
            {
                "method": "DELETE",
                "endpoint": "/api/v1/services/{service_uuid}",
                "purpose": "delete the temporary probe",
            },
        ],
        "planned_observation_endpoints": [
            f"/api/v1/projects/{controller_meta['project_uuid']}/environments",
            "/api/v1/services",
            "/api/v1/services/{service_uuid}",
            "/api/v1/services/{service_uuid}/logs?sub_service_name={service_name}",
            "/api/v1/deployments",
            f"/api/v1/servers/{controller_meta['server_uuid']}/resources",
        ],
        "compose": {
            "sha256": hashlib.sha256(compose.encode("utf-8")).hexdigest(),
            "image": _IMAGE,
            "read_only": True,
            "entrypoint": ["/bin/sh", "-ec"],
            "single_script_argument": True,
            "healthcheck_required": True,
            "runtime_log_marker_required": True,
            "runtime_log_endpoint_template": "/api/v1/services/{service_uuid}/logs?sub_service_name={service_name}",
            "ports": [],
            "volumes": [],
            "secrets": [],
        },
        "timing": {
            "observe_seconds": observe_seconds,
            "poll_interval_seconds": poll_interval_seconds,
        },
        "required_acknowledgement": _ACKNOWLEDGEMENT,
        "summary": {
            "clean": True,
            "live_mutation_performed": False,
            "network_access_performed": False,
            "secret_binding_count": 0,
            "chain_rpc_call_count": 0,
            "public_endpoint_count": 0,
            "volume_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
        },
    }


def _write_evidence(
    paths: PrivateStatePaths,
    evidence: dict[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    digest = hashlib.sha256(canonical_json(evidence)).hexdigest()
    document = dict(evidence)
    document["coolify_service_lifecycle_probe_evidence_sha256"] = digest
    raw = canonical_json(document)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = _ensure_root(paths, _EVIDENCE_DIRECTORY, operation)
    path = root / f"{stamp}-{digest[:16]}.json"
    atomic_files.durable_create(path, raw, operation=operation)
    _secure_private_path(path, is_directory=False, operation=operation)
    return path, digest


def execute_coolify_service_lifecycle_probe(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    environment_name: str,
    acknowledged_probe: str,
    observe_seconds: float = 60.0,
    poll_interval_seconds: float = 5.0,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    _validate(
        network=network,
        controller_id=controller_id,
        environment_name=environment_name,
        observe_seconds=observe_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    if acknowledged_probe != _ACKNOWLEDGEMENT:
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_ACKNOWLEDGEMENT_INVALID",
            f"acknowledgement must equal {_ACKNOWLEDGEMENT}",
        )
    if timeout <= 0 or max_response_bytes <= 0:
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_INVALID",
            "timeout and max_response_bytes must be positive",
        )

    controller_meta = _controller_config(
        private_state,
        network=network,
        controller_id=controller_id,
    )
    controller = resolve_coolify_controller(private_state, network, controller_id)
    service_name = _probe_name(controller_id, operation)
    compose = _compose(service_name, observe_seconds)
    started_at = _timestamp()
    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    service_uuid: str | None = None
    cleanup_succeeded = False
    failure: dict[str, str] | None = None

    try:
        environment_endpoint = f"/api/v1/projects/{controller_meta['project_uuid']}/environments"
        environment_response = _http(
            controller,
            "GET",
            environment_endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        environment_observation = _response_record(
            phase="environment-resolution",
            controller_id=controller_id,
            method="GET",
            endpoint=environment_endpoint,
            response=environment_response,
            service_name=service_name,
        )
        observations.append({"sequence": -1, "observed_at": _timestamp(), "channels": [environment_observation]})
        if environment_response.get("ok") is not True:
            raise _error(
                "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_ENVIRONMENT_INVALID",
                f"environment inventory failed with HTTP {environment_response.get('status')}",
            )
        environment_uuid = _environment_uuid(environment_response.get("payload"), environment_name)

        create_body = {
            "project_uuid": controller_meta["project_uuid"],
            "server_uuid": controller_meta["server_uuid"],
            "environment_name": environment_name,
            "environment_uuid": environment_uuid,
            "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
            "name": service_name,
            "description": "Temporary no-secret no-chain Mother service lifecycle probe",
            "instant_deploy": False,
        }
        create_response = _http(
            controller,
            "POST",
            "/api/v1/services",
            body=create_body,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        receipts.append(
            _mutation_receipt(
                mutation_id=f"{service_name}.create",
                controller_id=controller_id,
                method="POST",
                endpoint="/api/v1/services",
                response=create_response,
            )
        )
        if create_response.get("ok") is not True:
            raise _error(
                "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_CREATE_FAILED",
                f"service creation failed with HTTP {create_response.get('status')}",
            )
        service_uuid = _one_uuid(create_response.get("payload"))
        receipts[-1]["service_uuid"] = service_uuid

        prestart = _read_observation(
            controller=controller,
            controller_id=controller_id,
            controller_meta=controller_meta,
            service_uuid=service_uuid,
            service_name=service_name,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            sequence=0,
        )
        prestart["phase"] = "pre-start"
        observations.append(prestart)

        start_endpoint = f"/api/v1/services/{service_uuid}/start"
        start_response = _http(
            controller,
            "POST",
            start_endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        receipts.append(
            _mutation_receipt(
                mutation_id=f"{service_name}.start",
                controller_id=controller_id,
                method="POST",
                endpoint=start_endpoint,
                response=start_response,
                service_uuid=service_uuid,
            )
        )
        if start_response.get("ok") is not True:
            raise _error(
                "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_START_FAILED",
                f"service start failed with HTTP {start_response.get('status')}",
            )

        observed_started = time.monotonic()
        sequence = 1
        while True:
            observations.append(
                _read_observation(
                    controller=controller,
                    controller_id=controller_id,
                    controller_meta=controller_meta,
                    service_uuid=service_uuid,
                    service_name=service_name,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                    sequence=sequence,
                )
            )
            elapsed = time.monotonic() - observed_started
            if elapsed >= observe_seconds:
                break
            sequence += 1
            time.sleep(min(poll_interval_seconds, max(0.0, observe_seconds - elapsed)))
    except Exception as exc:  # evidence must survive every bounded failure
        failure = {
            "code": str(getattr(exc, "code", "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_FAILED")),
            "message": str(exc).replace("\r", " ").replace("\n", " ").strip()[:300],
        }
    finally:
        if service_uuid is not None:
            delete_endpoint = f"/api/v1/services/{service_uuid}"
            try:
                delete_response = _http(
                    controller,
                    "DELETE",
                    delete_endpoint,
                    body=None,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                cleanup_succeeded = int(delete_response.get("status", 0)) in {200, 204, 404}
                receipts.append(
                    _mutation_receipt(
                        mutation_id=f"{service_name}.delete",
                        controller_id=controller_id,
                        method="DELETE",
                        endpoint=delete_endpoint,
                        response={
                            **delete_response,
                            "ok": cleanup_succeeded,
                        },
                        service_uuid=service_uuid,
                    )
                )
                if not cleanup_succeeded and failure is None:
                    failure = {
                        "code": "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_CLEANUP_FAILED",
                        "message": f"temporary service deletion failed with HTTP {delete_response.get('status')}",
                    }
            except Exception as cleanup_exc:
                receipts.append(
                    {
                        "mutation_id": f"{service_name}.delete",
                        "controller_id": controller_id,
                        "method": "DELETE",
                        "endpoint": delete_endpoint,
                        "service_uuid": service_uuid,
                        "status": "failed",
                        "live_write_acknowledged": False,
                    }
                )
                if failure is None:
                    failure = {
                        "code": str(
                            getattr(
                                cleanup_exc,
                                "code",
                                "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_CLEANUP_FAILED",
                            )
                        ),
                        "message": str(cleanup_exc).replace("\r", " ").replace("\n", " ").strip()[:300],
                    }

    service_status_values: set[str] = set()
    service_list_seen = False
    service_detail_seen = False
    server_resource_seen = False
    deployment_seen = False
    service_log_endpoint_seen = False
    service_log_marker_seen = False
    service_log_response_classifications: set[str] = set()
    for observation in observations:
        for channel in observation.get("channels", []):
            if not isinstance(channel, Mapping):
                continue
            name = channel.get("channel")
            if name == "service-runtime-logs":
                if channel.get("http_status") == 200:
                    service_log_endpoint_seen = True
                if channel.get("marker_observed") is True:
                    service_log_marker_seen = True
                classification = channel.get("response_classification")
                if type(classification) is str:
                    service_log_response_classifications.add(classification)
                continue
            matches = channel.get("matches")
            if type(matches) is not list:
                continue
            if name == "service-list" and matches:
                service_list_seen = True
            elif name == "service-detail" and matches:
                service_detail_seen = True
            elif name == "server-resources" and matches:
                server_resource_seen = True
            elif name == "deployment-list" and matches:
                deployment_seen = True
            for match in matches:
                if isinstance(match, Mapping) and type(match.get("status")) is str:
                    service_status_values.add(match["status"])

    healthy_running_status_values = sorted(
        value for value in service_status_values if _healthy_running_status(value)
    )
    healthy_running_observed = bool(healthy_running_status_values)
    complete = bool(
        failure is None
        and cleanup_succeeded
        and service_uuid is not None
        and service_list_seen
        and service_detail_seen
        and server_resource_seen
        and healthy_running_observed
        and service_log_endpoint_seen
        and service_log_marker_seen
        and len(receipts) == 3
        and [item.get("status") for item in receipts] == ["succeeded", "succeeded", "succeeded"]
    )
    completed_at = _timestamp()
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 4,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "pass" if complete else "manual-review-required",
        "network": network,
        "mother_binding": _binding(private_state),
        "operation_id": operation.operation_id,
        "controller_id": controller_id,
        "environment_name": environment_name,
        "probe_name": service_name,
        "service_uuid": service_uuid,
        "authority": {
            "secret_binding_authorized": False,
            "chain_access_authorized": False,
            "public_endpoint_authorized": False,
            "volume_authorized": False,
            "requested_mutation_count": 3,
        },
        "mutation_receipts": receipts,
        "observations": observations,
        "failure": failure,
        "summary": {
            "clean": complete,
            "complete": complete,
            "temporary_service_deleted": cleanup_succeeded,
            "service_list_record_observed": service_list_seen,
            "service_detail_record_observed": service_detail_seen,
            "server_resource_record_observed": server_resource_seen,
            "deployment_record_observed": deployment_seen,
            "observed_status_values": sorted(service_status_values),
            "healthy_running_observed": healthy_running_observed,
            "healthy_running_status_values": healthy_running_status_values,
            "service_runtime_log_endpoint_observed": service_log_endpoint_seen,
            "service_runtime_log_marker_observed": service_log_marker_seen,
            "service_runtime_log_response_classifications": sorted(
                service_log_response_classifications
            ),
            "runtime_log_marker": f"MOTHER_COOLIFY_SERVICE_LIFECYCLE_PROBE_READY={service_name}",
            "image_entrypoint_override_verified": True,
            "single_script_argument_verified": True,
            "application_mutation_count": len(receipts),
            "secret_binding_count": 0,
            "chain_rpc_call_count": 0,
            "public_endpoint_count": 0,
            "volume_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "next_phase": (
                "service-runtime-log-result-channel-proven"
                if complete
                else "manual-review-required"
            ),
        },
    }
    if _contains_sensitive(evidence):
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_EVIDENCE_SENSITIVE",
            "probe evidence unexpectedly contains sensitive material",
        )
    evidence_path, evidence_sha = _write_evidence(paths, evidence, operation=operation)
    return {
        "status": evidence["status"],
        "network": network,
        "controller_id": controller_id,
        "probe_name": service_name,
        "service_uuid": service_uuid,
        "summary": evidence["summary"],
        "evidence": {"path": str(evidence_path), "sha256": evidence_sha},
    }


def verify_coolify_service_lifecycle_probe_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
) -> dict[str, Any]:
    document, _, file_sha = _canonical_under(
        paths,
        Path(evidence_path),
        _EVIDENCE_DIRECTORY,
        "Coolify service lifecycle probe evidence",
    )
    digest = hashlib.sha256(
        canonical_json(
            {
                key: value
                for key, value in document.items()
                if key != "coolify_service_lifecycle_probe_evidence_sha256"
            }
        )
    ).hexdigest()
    evidence_version = (
        document.get("kind"),
        document.get("schema_version"),
    )
    if not (
        evidence_version in {
            (_LEGACY_EVIDENCE_KIND, 3),
            (_EVIDENCE_KIND, 4),
        }
        and document.get("coolify_service_lifecycle_probe_evidence_sha256") == digest
        and document.get("mother_binding") == _binding(private_state)
        and not _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_EVIDENCE_INVALID",
            "probe evidence is not canonical or correctly bound",
        )
    completed = _parse_utc(document.get("completed_at"), "probe.completed_at")
    age = int((datetime.now(timezone.utc) - completed).total_seconds())
    if age < -15 or age > max_age_seconds:
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_EVIDENCE_EXPIRED",
            "probe evidence is outside the accepted verification window",
        )
    summary = document.get("summary")
    receipts = document.get("mutation_receipts")
    runtime_log_classification_contract = (
        evidence_version == (_LEGACY_EVIDENCE_KIND, 3)
        or (
            type(summary) is dict
            and type(summary.get("service_runtime_log_response_classifications")) is list
            and "ok-logs" in summary["service_runtime_log_response_classifications"]
        )
    )
    if not (
        isinstance(summary, Mapping)
        and type(receipts) is list
        and summary.get("temporary_service_deleted") is True
        and summary.get("service_list_record_observed") is True
        and summary.get("service_detail_record_observed") is True
        and summary.get("server_resource_record_observed") is True
        and summary.get("healthy_running_observed") is True
        and summary.get("service_runtime_log_endpoint_observed") is True
        and summary.get("service_runtime_log_marker_observed") is True
        and runtime_log_classification_contract
        and summary.get("runtime_log_marker")
        == f"MOTHER_COOLIFY_SERVICE_LIFECYCLE_PROBE_READY={document.get('probe_name')}"
        and summary.get("image_entrypoint_override_verified") is True
        and summary.get("single_script_argument_verified") is True
        and type(summary.get("observed_status_values")) is list
        and type(summary.get("healthy_running_status_values")) is list
        and summary["healthy_running_status_values"]
        and all(
            _healthy_running_status(value)
            for value in summary["healthy_running_status_values"]
        )
        and set(summary["healthy_running_status_values"]).issubset(
            set(summary["observed_status_values"])
        )
        and summary.get("secret_binding_count") == 0
        and summary.get("chain_rpc_call_count") == 0
        and summary.get("public_endpoint_count") == 0
        and summary.get("volume_count") == 0
        and summary.get("validator_mutation_count") == 0
        and summary.get("validator_restart_count") == 0
    ):
        raise _error(
            "MOTHER_DEPLOY_COOLIFY_SERVICE_LIFECYCLE_PROBE_EVIDENCE_INVALID",
            "probe evidence violates the no-secret no-chain cleanup contract",
        )
    return {
        "clean": document.get("status") == "pass" and summary.get("clean") is True,
        "status": document.get("status"),
        "network": document.get("network"),
        "controller_id": document.get("controller_id"),
        "probe_name": document.get("probe_name"),
        "service_uuid": document.get("service_uuid"),
        "age_seconds": max(0, age),
        "evidence_path": str(Path(evidence_path).resolve(strict=False)),
        "evidence_file_sha256": file_sha,
        "evidence_sha256": digest,
        "temporary_service_deleted": summary.get("temporary_service_deleted"),
        "service_list_record_observed": summary.get("service_list_record_observed"),
        "service_detail_record_observed": summary.get("service_detail_record_observed"),
        "server_resource_record_observed": summary.get("server_resource_record_observed"),
        "deployment_record_observed": summary.get("deployment_record_observed"),
        "observed_status_values": summary.get("observed_status_values"),
        "healthy_running_observed": summary.get("healthy_running_observed"),
        "healthy_running_status_values": summary.get("healthy_running_status_values"),
        "service_runtime_log_endpoint_observed": summary.get("service_runtime_log_endpoint_observed"),
        "service_runtime_log_marker_observed": summary.get("service_runtime_log_marker_observed"),
        "service_runtime_log_response_classifications": summary.get(
            "service_runtime_log_response_classifications"
        ),
        "runtime_log_marker": summary.get("runtime_log_marker"),
        "image_entrypoint_override_verified": summary.get("image_entrypoint_override_verified"),
        "single_script_argument_verified": summary.get("single_script_argument_verified"),
        "secret_binding_count": 0,
        "chain_rpc_call_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "next_phase": summary.get("next_phase"),
    }
