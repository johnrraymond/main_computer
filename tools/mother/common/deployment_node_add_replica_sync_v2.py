"""Guarded generic Mother ``add-node replica-sync`` v2 executor.

V2 preserves the v1 release/evidence contracts while adding an explicit
permanent-node readiness gate between Coolify ``/start`` acknowledgement and
the temporary guardian-start helper.  The v1 module remains unchanged so the
CLI can be rolled back by switching a single executor selector.

Guarded generic Mother ``add-node replica-sync`` release and executor.

This module consumes clean generic add-node identity evidence and authorizes only
the next live mutation: configure and deploy the target as a non-validator
replica with an internal synchronization guardian.  It does not admit the
validator, cast QBFT votes, publish routing/topology, or create a public
endpoint.  The implementation is topology-derived and does not special-case deprecated
fixture/testing path labels. The active path is the operator-directed evidence
sequence for the selected node and release.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import (
    _DEFAULT_MAX_RESPONSE_BYTES,
    _DEFAULT_OPENER,
    resolve_coolify_controller,
)
from .deployment_completed_helper_cleanup import (
    _application_uuid,
    _controller_config,
    _resolve_environment_uuid,
    _temporary_service_body,
    _wait_for_temporary_service_health,
)
from .deployment_genesis import _genesis_policy
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path
from .deployment_validator_routes import ensure_service_validator_route, validator_route_from_record


_RELEASE_KIND = "main_computer.mother.deployment_node_add_replica_sync_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_node_add_replica_sync_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_node_add_replica_sync_evidence.v1"
_IDENTITY_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-add-identity")
_RELEASE_DIRECTORY = ("actions", "deployment-node-add-replica-sync-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-node-add-replica-sync-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-add-replica-sync")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_IDENTITY_ENV_KEYS = ("MC_MOTHER_VALIDATOR_PRIVATE_KEY", "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY")
_INIT_IMAGE = "alpine:3.20"
_BESU_IMAGE = "hyperledger/besu:latest"
_PROOF_IMAGE = "python:3.12-alpine"
_MIN_RELEASE_SECONDS = 1
_MAX_RELEASE_SECONDS = 900
_IMPLEMENTATION_VERSION = 2
_GUARDIAN_START_DIAGNOSTIC_MARKERS = {
    "project-not-found": "MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG project-not-found",
    "node-not-running-healthy": "MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG node-not-running-healthy",
    "guardian-compose-up-failed": "MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG guardian-compose-up-failed",
    "guardian-health-timeout": "MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG guardian-health-timeout",
}


class MotherDeploymentNodeAddReplicaSyncError(RuntimeError):
    """Node-add replica synchronization failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentNodeAddReplicaSyncError:
    return MotherDeploymentNodeAddReplicaSyncError(code, message)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_INVALID", f"{label} is missing")
    text = value.strip()
    if not _IDENTIFIER_RE.fullmatch(text):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_INVALID", f"{label} is not a valid identifier")
    return text


def _address(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ADDRESS_RE.fullmatch(value.strip()):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_INVALID", f"{label} is not a valid address")
    return value.strip().lower()


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_INVALID", f"{label} is not a SHA-256 digest")
    return value


def _timestamp(value: str | None = None, *, now: datetime | None = None) -> str:
    if value is not None:
        parsed = _parse_utc(value, "timestamp")
        return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    return reference.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_INVALID", f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_INVALID", f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_INVALID", f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any, *, now: datetime | None) -> int:
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    return max(0, int((reference - _parse_utc(value, "timestamp")).total_seconds()))


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _digest_without(value: Mapping[str, Any], key: str) -> str:
    copy = dict(value)
    copy.pop(key, None)
    return hashlib.sha256(canonical_json(copy)).hexdigest()


def _root(paths: PrivateStatePaths, parts: Iterable[str]) -> Path:
    current = paths.root
    for part in parts:
        current = current / part
    return current


def _ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    resolved = Path(path).resolve(strict=False)
    try:
        return resolved.relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_PATH_INVALID", f"{label} is outside Mother runtime state") from exc


def _resolve_under(paths: PrivateStatePaths, locator: Any, directory: tuple[str, ...], *, label: str) -> Path:
    if not isinstance(locator, str) or not locator:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_PATH_INVALID", f"{label} locator is missing")
    normalized = locator.replace("\\", "/")
    candidate = (paths.root / normalized).resolve(strict=False)
    allowed = _root(paths, directory).resolve(strict=False)
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_PATH_INVALID", f"{label} is outside its directory") from exc
    return candidate


def _canonical_file(path: Path) -> tuple[dict[str, Any], bytes, str]:
    resolved = Path(path)
    raw = resolved.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_JSON_INVALID", f"{resolved} is not canonical JSON") from exc
    if not isinstance(document, dict):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_JSON_INVALID", f"{resolved} is not a JSON object")
    canonical = canonical_json(document)
    return document, raw, hashlib.sha256(canonical).hexdigest()


def _document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(private_state.document_bytes.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_STATE_INVALID", "private state cannot be decoded") from exc
    if not isinstance(parsed, dict):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_STATE_INVALID", "private state document is not a mapping")
    return parsed


def _resolve_dotted(document: Mapping[str, Any], ref: Any) -> Any:
    if not isinstance(ref, str) or not ref:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_STATE_INVALID", "dotted reference is missing")
    current: Any = document
    for part in ref.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_STATE_INVALID", f"dotted reference {ref!r} cannot be resolved")
        current = current[part]
    return current


def _state_private_key(private_state: PrivateStateReadResult, *, network: str, node: str) -> str:
    state = _document(private_state)
    try:
        value = state["networks"][network]["validators"][node]["private_key"]
    except (KeyError, TypeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_STATE_INVALID", f"{node} validator identity is missing") from exc
    if not isinstance(value, str) or _PRIVATE_KEY_RE.fullmatch(value.strip()) is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_STATE_INVALID", f"{node} validator identity is invalid")
    return value.strip()


def _public_node_id(private_key: str) -> str:
    if _PRIVATE_KEY_RE.fullmatch(private_key) is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_STATE_INVALID", "validator private key is invalid")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:  # pragma: no cover
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_DEPENDENCY_MISSING", "cryptography is required to derive public node IDs") from exc
    key = ec.derive_private_key(int(private_key[2:], 16), ec.SECP256K1())
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return public[1:].hex()


def _node_id_from_enode(value: str) -> str:
    match = re.match(r"^enode://([0-9a-f]{128})@", value.lower())
    if not match:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_INVALID", "bootnode enode is invalid")
    return match.group(1)


def _advertised_host(base_url: str) -> str:
    host = urllib.parse.urlsplit(base_url).hostname
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.\[\]:-]+", host):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_CONTROLLER_INVALID", "controller URL has no safe hostname")
    return host.lower()


def _contains_sensitive(value: Any) -> bool:
    needles = (
        "BEGIN PRIVATE KEY",
        "BEGIN RSA PRIVATE KEY",
        "api_token",
        "bearer ",
    )
    def walk(item: Any) -> bool:
        if isinstance(item, str):
            lowered = item.lower()
            if _PRIVATE_KEY_RE.fullmatch(item.strip()) is not None:
                return True
            return any(marker.lower() in lowered for marker in needles)
        if isinstance(item, Mapping):
            return any(walk(key) or walk(val) for key, val in item.items())
        if isinstance(item, list):
            return any(walk(val) for val in item)
        return False
    return walk(value)


def _raw_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        items: list[Mapping[str, Any]] = []
        if any(key in payload for key in ("uuid", "id", "name", "key")):
            items.append(payload)
        for key in ("data", "service", "resource"):
            if isinstance(payload.get(key), Mapping):
                items.append(payload[key])
        for key in ("services", "resources", "envs", "environment_variables", "variables"):
            if isinstance(payload.get(key), list):
                items.extend(item for item in payload[key] if isinstance(item, Mapping))
        return items
    return []


def _text(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return ""


def _env_key(item: Mapping[str, Any]) -> str:
    return _text(item, "key", "name", "variable")


def _visible_value(item: Mapping[str, Any]) -> str | None:
    for key in ("value", "real_value", "literal_value"):
        value = item.get(key)
        if isinstance(value, str) and value and not value.startswith("********"):
            return value
    return None


def _service_record(payload: Any, *, service_uuid: str, node: str) -> Mapping[str, Any]:
    matches = [
        item for item in _raw_items(payload)
        if _text(item, "uuid", "id") == service_uuid or _text(item, "name") == node
    ]
    matches = [
        item for item in matches
        if _text(item, "name") in {"", node} or _text(item, "uuid", "id") == service_uuid
    ]
    unique: dict[str, Mapping[str, Any]] = {}
    for item in matches:
        key = _text(item, "uuid", "id") or _text(item, "name")
        unique[key] = item
    matches = list(unique.values())
    if len(matches) != 1:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_SERVICE_MISMATCH", "Coolify does not expose one exact target service")
    if _text(matches[0], "name") not in {"", node} and _text(matches[0], "uuid", "id") != service_uuid:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_SERVICE_MISMATCH", "Coolify service name does not match target node")
    return matches[0]


def _service_status(item: Mapping[str, Any] | None) -> str:
    if not isinstance(item, Mapping):
        return ""
    status = _text(item, "status", "human_status", "state")
    health = _text(item, "health", "health_status")
    if status and health and health not in status:
        return f"{status}:{health}"
    return status


def _compose_from_service_record(record: Mapping[str, Any]) -> str:
    for key in ("docker_compose_raw", "dockerComposeRaw", "docker_compose", "dockerCompose", "compose"):
        value = record.get(key)
        if isinstance(value, str) and value:
            if "\n" in value or value.lstrip().startswith("name:") or value.lstrip().startswith("services:"):
                return value
            try:
                return base64.b64decode(value, validate=True).decode("utf-8")
            except Exception:
                return value
    return ""


def _http(
    controller: Any,
    method: str,
    endpoint: str,
    *,
    body: Mapping[str, Any] | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    payload = canonical_json(dict(body)) if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {controller.api_token}",
        "User-Agent": "main-computer-mother-add-node-replica-sync/1",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(controller.base_url + endpoint, data=payload, headers=headers, method=method)
    started = time.monotonic()
    try:
        try:
            response = opener.open(request, timeout=float(timeout)) if hasattr(opener, "open") else opener(request, float(timeout))
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_REQUEST_FAILED", "Coolify request failed") from exc
    if len(raw) > max_response_bytes:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_RESPONSE_TOO_LARGE", "Coolify response is too large")
    try:
        payload_obj: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload_obj = raw.decode("utf-8", errors="replace")
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "payload": payload_obj,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
        "content_type": getattr(response, "headers", {}).get("Content-Type", "application/json") if "response" in locals() else "application/json",
    }


def _safe_response(response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": response.get("status"),
        "ok": response.get("ok"),
        "content_type": response.get("content_type"),
        "response_sha256": response.get("response_sha256"),
        "byte_length": response.get("byte_length"),
        "elapsed_ms": response.get("elapsed_ms"),
    }


def _single_quote(value: object) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _guardian_start_service_name(controller_id: str) -> str:
    controller = _identifier(controller_id, "controller_id").replace("_", "-")
    return f"mother-replica-guardian-start-{controller}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S').lower()}"


def _application_statuses(payload: Any) -> dict[str, dict[str, str]]:
    if not isinstance(payload, Mapping):
        return {}
    applications = payload.get("applications")
    if not isinstance(applications, list):
        return {}
    records: dict[str, dict[str, str]] = {}
    for item in applications:
        if not isinstance(item, Mapping):
            continue
        name = _text(item, "name")
        if not name:
            continue
        records[name] = {
            "name": name,
            "uuid": _text(item, "uuid", "id"),
            "status": _service_status(item),
            "image": _text(item, "image"),
        }
    return records


def _component_healthy(status: object) -> bool:
    return isinstance(status, str) and status.startswith("running:healthy") and "unhealthy" not in status


def _replica_sync_component_summary(payload: Any, *, node: str) -> dict[str, Any]:
    applications = _application_statuses(payload)
    node_record = applications.get(node)
    guardian_record = applications.get("mother-replica-sync-guardian")
    init_record = applications.get("mother-replica-init")
    node_status = node_record.get("status", "") if node_record else ""
    guardian_status = guardian_record.get("status", "") if guardian_record else ""
    init_status = init_record.get("status", "") if init_record else ""
    ok = _component_healthy(node_status) and _component_healthy(guardian_status)
    return {
        "component_aware_success": ok,
        "node_running_healthy": _component_healthy(node_status),
        "guardian_running_healthy": _component_healthy(guardian_status),
        "node_status": node_status or "missing",
        "guardian_status": guardian_status or "missing",
        "init_status": init_status or "missing",
        "required_components": {
            node: node_record or {"name": node, "uuid": "", "status": "missing", "image": ""},
            "mother-replica-sync-guardian": guardian_record or {"name": "mother-replica-sync-guardian", "uuid": "", "status": "missing", "image": ""},
        },
        "optional_components": {
            "mother-replica-init": init_record or {"name": "mother-replica-init", "uuid": "", "status": "missing", "image": ""},
        },
    }


def _record_replica_sync_target_diagnostic(
    *,
    controller: Any,
    controller_id: str,
    service_uuid: str,
    node: str,
    phase: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
    record: dict[str, Any] = {
        "method": "GET",
        "endpoint": endpoint,
        "controller_id": controller_id,
        "phase": phase,
        "observed_at": _timestamp(),
        "service_uuid": service_uuid,
        "node": node,
    }
    try:
        response = _http(
            controller,
            "GET",
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        record.update(
            {
                "status": response["status"],
                "ok": response["ok"],
                "response_sha256": response["response_sha256"],
                "byte_length": response["byte_length"],
                "elapsed_ms": response["elapsed_ms"],
            }
        )
        if response.get("ok"):
            payload = response.get("payload")
            try:
                parent_status = _service_status(_service_record(payload, service_uuid=service_uuid, node=node))
            except MotherDeploymentNodeAddReplicaSyncError:
                parent_status = _service_status(payload if isinstance(payload, Mapping) else None)
            summary = _replica_sync_component_summary(payload, node=node)
            record.update(
                {
                    "parent_status": parent_status or None,
                    "node_status": summary.get("node_status"),
                    "guardian_status": summary.get("guardian_status"),
                    "init_status": summary.get("init_status"),
                    "component_aware_success": bool(summary.get("component_aware_success") is True),
                }
            )
    except Exception as exc:  # noqa: BLE001
        record.update(
            {
                "ok": False,
                "diagnostic_error_code": str(getattr(exc, "code", type(exc).__name__)),
                "diagnostic_error": str(exc)[:512],
            }
        )
    observations.append(record)
    return record


def _wait_for_replica_sync_node_ready(
    *,
    controller: Any,
    controller_id: str,
    service_uuid: str,
    node: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Wait for the permanent replica node component before starting the helper.

    Coolify's service ``/start`` acknowledgement is asynchronous.  The guardian
    helper performs fail-fast Docker assertions, so launching it before the
    permanent node is reported ``running:healthy`` creates a race with Coolify's
    deployment materialization.  This waiter establishes that readiness boundary
    using read-only service-detail GETs.
    """

    endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
    started = time.monotonic()
    last_summary: dict[str, Any] = {}
    observed_statuses: list[dict[str, str]] = []
    observation_count = 0
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
        observation_count += 1
        parent_status = ""
        if response.get("ok"):
            try:
                parent_status = _service_status(
                    _service_record(response["payload"], service_uuid=service_uuid, node=node)
                )
            except MotherDeploymentNodeAddReplicaSyncError:
                parent_status = _service_status(
                    response.get("payload") if isinstance(response.get("payload"), Mapping) else None
                )
            last_summary = _replica_sync_component_summary(response.get("payload"), node=node)
        else:
            last_summary = {
                "component_aware_success": False,
                "node_running_healthy": False,
                "guardian_running_healthy": False,
                "node_status": "unknown",
                "guardian_status": "unknown",
                "init_status": "unknown",
            }

        statuses = {
            "parent_status": parent_status,
            "node_status": str(last_summary.get("node_status") or ""),
            "guardian_status": str(last_summary.get("guardian_status") or ""),
            "init_status": str(last_summary.get("init_status") or ""),
        }
        observed_statuses.append(statuses)
        observations.append(
            {
                "method": "GET",
                "endpoint": endpoint,
                "status": response["status"],
                "ok": response["ok"],
                "response_sha256": response["response_sha256"],
                "byte_length": response["byte_length"],
                "elapsed_ms": response["elapsed_ms"],
                "observed_at": _timestamp(),
                "controller_id": controller_id,
                "phase": "replica-sync-node-readiness",
                **statuses,
                "node_running_healthy": bool(last_summary.get("node_running_healthy") is True),
            }
        )

        elapsed = time.monotonic() - started
        if response.get("ok") and last_summary.get("node_running_healthy") is True:
            return {
                "healthy": True,
                "reason": "replica-sync-node-running-healthy",
                "service_uuid": service_uuid,
                "node": node,
                "component_summary": last_summary,
                "observed_statuses": observed_statuses,
                "observation_count": observation_count,
                "wait_seconds": int(elapsed),
                "wait_milliseconds": int(round(elapsed * 1000)),
            }
        if elapsed >= max_wait_seconds:
            return {
                "healthy": False,
                "reason": "replica-sync-node-readiness-timeout",
                "service_uuid": service_uuid,
                "node": node,
                "component_summary": last_summary,
                "observed_statuses": observed_statuses,
                "observation_count": observation_count,
                "wait_seconds": int(elapsed),
                "wait_milliseconds": int(round(elapsed * 1000)),
            }
        if poll_interval_seconds > 0:
            time.sleep(min(poll_interval_seconds, max(0.0, max_wait_seconds - elapsed)))
        else:
            break

    return {
        "healthy": False,
        "reason": "replica-sync-node-readiness-timeout",
        "service_uuid": service_uuid,
        "node": node,
        "component_summary": last_summary,
        "observed_statuses": observed_statuses,
        "observation_count": observation_count,
        "wait_seconds": int(time.monotonic() - started),
        "wait_milliseconds": int(round((time.monotonic() - started) * 1000)),
    }


def _wait_for_replica_sync_components(
    *,
    controller: Any,
    controller_id: str,
    service_uuid: str,
    node: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
    started = time.monotonic()
    last_summary: dict[str, Any] = {}
    observed_statuses: list[dict[str, str]] = []
    observation_count = 0
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
        observation_count += 1
        parent_status = ""
        if response.get("ok"):
            try:
                parent_status = _service_status(_service_record(response["payload"], service_uuid=service_uuid, node=node))
            except MotherDeploymentNodeAddReplicaSyncError:
                parent_status = _service_status(response.get("payload") if isinstance(response.get("payload"), Mapping) else None)
            last_summary = _replica_sync_component_summary(response.get("payload"), node=node)
        else:
            last_summary = {
                "component_aware_success": False,
                "node_running_healthy": False,
                "guardian_running_healthy": False,
                "node_status": "unknown",
                "guardian_status": "unknown",
                "init_status": "unknown",
            }
        statuses = {
            "parent_status": parent_status,
            "node_status": str(last_summary.get("node_status") or ""),
            "guardian_status": str(last_summary.get("guardian_status") or ""),
            "init_status": str(last_summary.get("init_status") or ""),
        }
        observed_statuses.append(statuses)
        observations.append(
            {
                "method": "GET",
                "endpoint": endpoint,
                "status": response["status"],
                "ok": response["ok"],
                "response_sha256": response["response_sha256"],
                "byte_length": response["byte_length"],
                "elapsed_ms": response["elapsed_ms"],
                "observed_at": _timestamp(),
                "controller_id": controller_id,
                "phase": "replica-sync-component-health",
                **statuses,
                "component_aware_success": bool(last_summary.get("component_aware_success") is True),
            }
        )
        elapsed = time.monotonic() - started
        if response.get("ok") and last_summary.get("component_aware_success") is True:
            return {
                "healthy": True,
                "reason": "replica-sync-components-healthy",
                "service_uuid": service_uuid,
                "node": node,
                "component_summary": last_summary,
                "observed_statuses": observed_statuses,
                "observation_count": observation_count,
                "wait_seconds": int(elapsed),
                "wait_milliseconds": int(round(elapsed * 1000)),
            }
        if elapsed >= max_wait_seconds:
            return {
                "healthy": False,
                "reason": "replica-sync-component-health-timeout",
                "service_uuid": service_uuid,
                "node": node,
                "component_summary": last_summary,
                "observed_statuses": observed_statuses,
                "observation_count": observation_count,
                "wait_seconds": int(elapsed),
                "wait_milliseconds": int(round(elapsed * 1000)),
            }
        if poll_interval_seconds > 0:
            time.sleep(min(poll_interval_seconds, max(0.0, max_wait_seconds - elapsed)))
        else:
            break
    return {
        "healthy": False,
        "reason": "replica-sync-component-health-timeout",
        "service_uuid": service_uuid,
        "node": node,
        "component_summary": last_summary,
        "observed_statuses": observed_statuses,
        "observation_count": observation_count,
        "wait_seconds": int(time.monotonic() - started),
        "wait_milliseconds": int(round((time.monotonic() - started) * 1000)),
    }


def _guardian_start_script(*, service_uuid: str, node: str, compose_b64: str, wait_seconds: int, poll_seconds: int) -> str:
    service = _identifier(service_uuid, "service_uuid")
    node_name = _identifier(node, "node")
    wait_limit = max(1, int(wait_seconds))
    poll_interval = max(1, int(poll_seconds))
    return "\n".join(
        [
            "set -eu",
            f"TARGET_SERVICE_UUID={_single_quote(service)}",
            f"NODE_NAME={_single_quote(node_name)}",
            "GUARDIAN_NAME='mother-replica-sync-guardian'",
            f"WAIT_LIMIT={wait_limit}",
            f"POLL_INTERVAL={poll_interval}",
            "COMPOSE_FILE=/tmp/mother-replica-sync-guardian.yml",
            "cat > /tmp/mother-replica-sync-guardian.yml.b64 <<'MOTHER_REPLICA_SYNC_GUARDIAN_COMPOSE'",
            compose_b64,
            "MOTHER_REPLICA_SYNC_GUARDIAN_COMPOSE",
            "base64 -d /tmp/mother-replica-sync-guardian.yml.b64 > \"$COMPOSE_FILE\"",
            "normalize_label() {",
            "  case \"${1:-}\" in ''|'<no value>'|'<nil>'|'null') printf '' ;; *) printf '%s' \"$1\" ;; esac",
            "}",
            "find_project() {",
            "  uuid=\"$1\"",
            "  for c in $(docker ps -aq --filter \"label=com.docker.compose.project=$uuid\" 2>/dev/null || true); do",
            "    project=\"$(normalize_label \"$(docker inspect -f '{{ index .Config.Labels \"com.docker.compose.project\" }}' \"$c\" 2>/dev/null || true)\")\"",
            "    workdir=\"$(normalize_label \"$(docker inspect -f '{{ index .Config.Labels \"com.docker.compose.project.working_dir\" }}' \"$c\" 2>/dev/null || true)\")\"",
            "    if [ -n \"$project\" ]; then printf '%s\\n%s\\n' \"$project\" \"$workdir\"; return 0; fi",
            "  done",
            "  for c in $(docker ps -aq --filter \"name=$uuid\" 2>/dev/null || true); do",
            "    project=\"$(normalize_label \"$(docker inspect -f '{{ index .Config.Labels \"com.docker.compose.project\" }}' \"$c\" 2>/dev/null || true)\")\"",
            "    workdir=\"$(normalize_label \"$(docker inspect -f '{{ index .Config.Labels \"com.docker.compose.project.working_dir\" }}' \"$c\" 2>/dev/null || true)\")\"",
            "    if [ -n \"$project\" ]; then printf '%s\\n%s\\n' \"$project\" \"$workdir\"; return 0; fi",
            "  done",
            "  echo \"MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG project-not-found service_uuid=$uuid\" >&2",
            "  return 1",
            "}",
            "node_healthy() {",
            "  project=\"$1\"",
            "  ids=\"$(docker ps -aq --filter \"label=com.docker.compose.project=$project\" --filter \"label=com.docker.compose.service=$NODE_NAME\" 2>/dev/null || true)\"",
            "  for c in $ids; do",
            "    state=\"$(normalize_label \"$(docker inspect -f '{{ .State.Status }}' \"$c\" 2>/dev/null || true)\")\"",
            "    health=\"$(normalize_label \"$(docker inspect -f '{{ if .State.Health }}{{ .State.Health.Status }}{{ end }}' \"$c\" 2>/dev/null || true)\")\"",
            "    if [ \"$state\" = running ] && [ \"$health\" = healthy ]; then return 0; fi",
            "  done",
            "  return 1",
            "}",
            "guardian_healthy() {",
            "  project=\"$1\"",
            "  ids=\"$(docker ps -aq --filter \"label=com.docker.compose.project=$project\" --filter \"label=com.docker.compose.service=$GUARDIAN_NAME\" 2>/dev/null || true)\"",
            "  for c in $ids; do",
            "    state=\"$(normalize_label \"$(docker inspect -f '{{ .State.Status }}' \"$c\" 2>/dev/null || true)\")\"",
            "    health=\"$(normalize_label \"$(docker inspect -f '{{ if .State.Health }}{{ .State.Health.Status }}{{ end }}' \"$c\" 2>/dev/null || true)\")\"",
            "    if [ \"$state\" = running ] && [ \"$health\" = healthy ]; then return 0; fi",
            "  done",
            "  return 1",
            "}",
            "info=\"$(find_project \"$TARGET_SERVICE_UUID\")\"",
            "PROJECT=\"$(printf '%s\\n' \"$info\" | sed -n '1p')\"",
            "WORKDIR=\"$(printf '%s\\n' \"$info\" | sed -n '2p')\"",
            "if ! node_healthy \"$PROJECT\"; then",
            "  echo \"MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG node-not-running-healthy node=$NODE_NAME project=$PROJECT\" >&2",
            "  exit 1",
            "fi",
            "if [ -n \"$WORKDIR\" ] && [ -d \"$WORKDIR\" ]; then",
            "  docker compose -p \"$PROJECT\" -f \"$COMPOSE_FILE\" --project-directory \"$WORKDIR\" up -d --no-deps --force-recreate \"$GUARDIAN_NAME\" || { echo \"MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG guardian-compose-up-failed project=$PROJECT\" >&2; exit 1; }",
            "else",
            "  docker compose -p \"$PROJECT\" -f \"$COMPOSE_FILE\" up -d --no-deps --force-recreate \"$GUARDIAN_NAME\" || { echo \"MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG guardian-compose-up-failed project=$PROJECT\" >&2; exit 1; }",
            "fi",
            "start=$(date +%s)",
            "while :; do",
            "  if guardian_healthy \"$PROJECT\"; then",
            "    touch /tmp/mother-replica-sync-guardian-started",
            "    echo mother-replica-sync-guardian-started",
            "    sleep 120",
            "    exit 0",
            "  fi",
            "  now=$(date +%s)",
            "  if [ $((now - start)) -ge \"$WAIT_LIMIT\" ]; then",
            "    echo \"MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG guardian-health-timeout project=$PROJECT\" >&2",
            "    exit 1",
            "  fi",
            "  sleep \"$POLL_INTERVAL\"",
            "done",
        ]
    ) + "\n"


def _guardian_start_compose(service_name: str, script: str) -> str:
    escaped_script = script.replace("$", "$$")
    compose = {
        "services": {
            service_name: {
                "image": "docker:27-cli",
                "command": ["sh", "-lc", escaped_script],
                "volumes": [
                    "/var/run/docker.sock:/var/run/docker.sock",
                    "/data/coolify:/data/coolify:ro",
                ],
                "restart": "no",
                "labels": {
                    "main_computer.mother.component": "replica-sync-guardian-start",
                    "main_computer.mother.not_a_validator": "true",
                    "main_computer.mother.not_a_chain_service": "true",
                },
                "healthcheck": {
                    "test": ["CMD-SHELL", "test -f /tmp/mother-replica-sync-guardian-started"],
                    "interval": "5s",
                    "timeout": "2s",
                    "retries": 3,
                    "start_period": "1s",
                },
            }
        }
    }
    return yaml.safe_dump(compose, sort_keys=False)


def _guardian_start_log_diagnostic(
    *,
    controller: Any,
    controller_id: str,
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    quoted_uuid = urllib.parse.quote(service_uuid, safe="")
    quoted_name = urllib.parse.quote(service_name, safe="")
    endpoints = [
        (
            "service-sub-service",
            f"/api/v1/services/{quoted_uuid}/logs?sub_service_name={quoted_name}&lines=100&show_timestamps=false",
        ),
        ("service", f"/api/v1/services/{quoted_uuid}/logs?lines=100"),
    ]
    attempts: list[dict[str, Any]] = []
    observed_markers: list[str] = []
    selected_logs_sha256: str | None = None
    selected_logs_byte_length: int | None = None
    for endpoint_kind, endpoint in endpoints:
        try:
            response = _http(
                controller,
                "GET",
                endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
        except Exception as exc:  # noqa: BLE001
            attempts.append(
                {
                    "endpoint_kind": endpoint_kind,
                    "endpoint": endpoint,
                    "ok": False,
                    "diagnostic_error_code": str(getattr(exc, "code", type(exc).__name__)),
                    "diagnostic_error": str(exc)[:512],
                }
            )
            continue
        payload = response.get("payload")
        logs: str | None = None
        if isinstance(payload, Mapping) and isinstance(payload.get("logs"), str):
            logs = payload["logs"]
        elif isinstance(payload, str):
            logs = payload
        markers = [
            name
            for name, token in _GUARDIAN_START_DIAGNOSTIC_MARKERS.items()
            if isinstance(logs, str) and token in logs
        ]
        attempts.append(
            {
                "endpoint_kind": endpoint_kind,
                "endpoint": endpoint,
                "status": response["status"],
                "ok": response["ok"],
                "response_sha256": response["response_sha256"],
                "byte_length": response["byte_length"],
                "elapsed_ms": response["elapsed_ms"],
                "logs_field_present": isinstance(logs, str),
                "failure_markers": markers,
            }
        )
        if isinstance(logs, str):
            observed_markers = markers
            selected_logs_sha256 = hashlib.sha256(logs.encode("utf-8")).hexdigest()
            selected_logs_byte_length = len(logs.encode("utf-8"))
        if response.get("ok") and isinstance(logs, str):
            break
        if response.get("status") not in {400, 404, 405, 422}:
            break
    return {
        "observed_at": _timestamp(),
        "controller_id": controller_id,
        "temporary_service_uuid": service_uuid,
        "temporary_service_name": service_name,
        "read_only": True,
        "attempts": attempts,
        "failure_markers": observed_markers,
        "logs_sha256": selected_logs_sha256,
        "logs_byte_length": selected_logs_byte_length,
        "raw_logs_persisted": False,
    }


def _guardian_start_base_result(
    *,
    status: str,
    reason: str | None,
    observations: list[dict[str, Any]],
    service_uuid: str,
    service_name: str,
    target_service_uuid: str,
    create: Mapping[str, Any] | None = None,
    start: Mapping[str, Any] | None = None,
    health: Mapping[str, Any] | None = None,
    delete: Mapping[str, Any] | None = None,
    failure_diagnostic: Mapping[str, Any] | None = None,
    error_code: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    result = {
        "status": status,
        "reason": reason,
        "create": dict(create) if isinstance(create, Mapping) else None,
        "start": dict(start) if isinstance(start, Mapping) else None,
        "health": dict(health) if isinstance(health, Mapping) else None,
        "delete": dict(delete) if isinstance(delete, Mapping) else None,
        "failure_diagnostic": dict(failure_diagnostic) if isinstance(failure_diagnostic, Mapping) else None,
        "observations": observations,
        "temporary_service_created": bool(service_uuid),
        "temporary_service_deleted": bool(isinstance(delete, Mapping) and delete.get("ok") is True),
        "temporary_service_uuid": service_uuid or None,
        "temporary_service_name": service_name,
        "target_service_uuid": target_service_uuid,
        "forced_service": "mother-replica-sync-guardian",
        "node_recreated": False,
        "init_recreated": False,
        "parent_redeploy_performed": False,
        "parent_restart_performed": False,
    }
    if error_code:
        result["error_code"] = error_code
    if error:
        result["error"] = error[:512]
    return result


def _run_replica_sync_guardian_start(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    service_uuid: str,
    node: str,
    compose_text: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    start_service_uuid = ""
    delete_receipt: dict[str, Any] | None = None
    create_receipt: dict[str, Any] | None = None
    start_receipt: dict[str, Any] | None = None
    health_result: dict[str, Any] | None = None
    failure_diagnostic: dict[str, Any] | None = None
    service_name = _guardian_start_service_name(controller_id)
    controller = resolve_coolify_controller(private_state, network, controller_id)
    status = "failed"
    reason: str | None = "temporary-service-not-started"
    error_code: str | None = None
    error: str | None = None

    try:
        controller_config = _controller_config(private_state, network=network, controller_id=controller_id)
        environment_uuid = _resolve_environment_uuid(
            controller=controller,
            controller_id=controller_id,
            endpoint=f"/api/v1/projects/{urllib.parse.quote(str(controller_config['project_uuid']), safe='')}/environments",
            expected_name=network,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            observations=observations,
        )
        script = _guardian_start_script(
            service_uuid=service_uuid,
            node=node,
            compose_b64=base64.b64encode(compose_text.encode("utf-8")).decode("ascii"),
            wait_seconds=max(1, int(max_wait_seconds)),
            poll_seconds=max(1, int(poll_interval_seconds or 1)),
        )
        helper_compose = _guardian_start_compose(service_name, script)
        body = _temporary_service_body(controller_config, service_name, helper_compose)
        body["environment_name"] = network
        body["environment_uuid"] = environment_uuid
        body["description"] = "Ephemeral Mother add-node replica-sync guardian start"
        body["instant_deploy"] = False

        create_response = _http(
            controller,
            "POST",
            "/api/v1/services",
            body=body,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        create_receipt = {
            "method": "POST",
            "endpoint": "/api/v1/services",
            "status": create_response["status"],
            "ok": create_response["ok"],
            "response_sha256": create_response["response_sha256"],
            "byte_length": create_response["byte_length"],
            "elapsed_ms": create_response["elapsed_ms"],
            "service_name": service_name,
            "request_body_sha256": hashlib.sha256(canonical_json(body)).hexdigest(),
        }
        observations.append({key: create_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
        if not create_response["ok"]:
            reason = "temporary-service-create-failed"
            return _guardian_start_base_result(
                status=status,
                reason=reason,
                observations=observations,
                service_uuid=start_service_uuid,
                service_name=service_name,
                target_service_uuid=service_uuid,
                create=create_receipt,
            )

        start_service_uuid = _application_uuid(create_response.get("payload"))
        create_receipt["service_uuid"] = start_service_uuid
        start_endpoint = f"/api/v1/services/{urllib.parse.quote(start_service_uuid, safe='')}/start"
        start_response = _http(
            controller,
            "POST",
            start_endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        start_receipt = {
            "method": "POST",
            "endpoint": start_endpoint,
            "status": start_response["status"],
            "ok": start_response["ok"],
            "response_sha256": start_response["response_sha256"],
            "byte_length": start_response["byte_length"],
            "elapsed_ms": start_response["elapsed_ms"],
            "service_uuid": start_service_uuid,
            "service_name": service_name,
        }
        observations.append({key: start_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
        if not start_response["ok"]:
            reason = "temporary-service-start-failed"
            return _guardian_start_base_result(
                status=status,
                reason=reason,
                observations=observations,
                service_uuid=start_service_uuid,
                service_name=service_name,
                target_service_uuid=service_uuid,
                create=create_receipt,
                start=start_receipt,
            )

        health_result = _wait_for_temporary_service_health(
            controller=controller,
            controller_id=controller_id,
            service_uuid=start_service_uuid,
            service_name=service_name,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            opener=opener,
            observations=observations,
        )
        if health_result.get("healthy") is True:
            status = "pass"
            reason = None
        else:
            reason = str(health_result.get("reason") or "temporary-service-not-healthy")
            failure_diagnostic = _guardian_start_log_diagnostic(
                controller=controller,
                controller_id=controller_id,
                service_uuid=start_service_uuid,
                service_name=service_name,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        reason = "temporary-service-exception"
        error_code = str(getattr(exc, "code", type(exc).__name__))
        error = str(exc)
    finally:
        if start_service_uuid:
            endpoint = f"/api/v1/services/{urllib.parse.quote(start_service_uuid, safe='')}"
            try:
                response = _http(
                    controller,
                    "DELETE",
                    endpoint,
                    body=None,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                delete_receipt = {
                    "method": "DELETE",
                    "endpoint": endpoint,
                    "status": response["status"],
                    "ok": response["ok"] or response["status"] == 404,
                    "response_sha256": response["response_sha256"],
                    "byte_length": response["byte_length"],
                    "elapsed_ms": response["elapsed_ms"],
                    "service_uuid": start_service_uuid,
                    "service_name": service_name,
                }
            except Exception as exc:  # noqa: BLE001
                delete_receipt = {
                    "method": "DELETE",
                    "endpoint": endpoint,
                    "status": None,
                    "ok": False,
                    "error_code": getattr(exc, "code", type(exc).__name__),
                    "error": str(exc)[:512],
                    "service_uuid": start_service_uuid,
                    "service_name": service_name,
                }
            observations.append({key: delete_receipt.get(key) for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms") if key in delete_receipt})

    return _guardian_start_base_result(
        status=status,
        reason=reason,
        observations=observations,
        service_uuid=start_service_uuid,
        service_name=service_name,
        target_service_uuid=service_uuid,
        create=create_receipt,
        start=start_receipt,
        health=health_result,
        delete=delete_receipt,
        failure_diagnostic=failure_diagnostic,
        error_code=error_code,
        error=error,
    )


def _claim_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> Path:
    digest = _sha256(release.get("node_add_replica_sync_release_sha256"), "node-add replica-sync release sha256")
    root = _ensure_directory(_root(paths, _CLAIM_DIRECTORY))
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {"sha256": digest},
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_path = root / f"{digest}.json"
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)
    return claim_path


def _write_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(release)
    digest = _sha256(document.get("node_add_replica_sync_release_sha256"), "node-add replica-sync release sha256")
    if document.get("kind") != _RELEASE_KIND or _digest_without(document, "node_add_replica_sync_release_sha256") != digest or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_RELEASE_INVALID", "node-add replica-sync release is malformed")
    root = _ensure_directory(_root(paths, _RELEASE_DIRECTORY))
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "nodeaddreplicasync"
    node = _identifier(document.get("target", {}).get("node"), "target node")
    destination = root / f"{stamp}-{document['network']}-{node}-{digest[:16]}.json"
    payload = canonical_json(document)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_RELEASE_CONFLICT", "release destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_RELEASE_WRITE_FAILED", "node-add replica-sync release reread mismatch")
    return destination, digest


def write_node_add_replica_sync_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    return _write_release(paths, release, operation=operation)


def _write_evidence(paths: PrivateStatePaths, evidence: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(evidence)
    if document.get("kind") != _EVIDENCE_KIND or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_EVIDENCE_INVALID", "node-add replica-sync evidence is malformed")
    root = _ensure_directory(_root(paths, _EVIDENCE_DIRECTORY))
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "nodeaddreplicasync"
    node = _identifier(document.get("target", {}).get("node"), "target node")
    digest_seed = hashlib.sha256(canonical_json(document)).hexdigest()
    destination = root / f"{stamp}-{node}-{digest_seed[:16]}.json"
    payload = canonical_json(document)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_EVIDENCE_CONFLICT", "evidence destination contains different bytes")
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
        _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, hashlib.sha256(payload).hexdigest()


def _candidate_genesis_dicts(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if isinstance(value.get("config"), Mapping) and isinstance(value.get("alloc"), Mapping) and "extraData" in value:
            yield value
        for key in ("canonical_json", "genesis", "genesis_block", "document", "payload"):
            if key in value:
                yield from _candidate_genesis_dicts(value[key])
        for child in value.values():
            if isinstance(child, (Mapping, list)):
                yield from _candidate_genesis_dicts(child)
    elif isinstance(value, list):
        for item in value:
            yield from _candidate_genesis_dicts(item)


def _discover_genesis(paths: PrivateStatePaths, private_state: PrivateStateReadResult, *, network: str, genesis_sha256: str) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = _sha256(genesis_sha256, "genesis SHA-256")
    search_roots = [paths.root / "actions", paths.root / "evidence"]
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            try:
                document, _raw, _file_sha = _canonical_file(path)
            except Exception:
                continue
            for candidate in _candidate_genesis_dicts(document):
                candidate_dict = dict(candidate)
                digest = hashlib.sha256(canonical_json(candidate_dict)).hexdigest()
                if digest == expected:
                    return candidate_dict, {
                        "source": "runtime-state-artifact",
                        "locator": _relative(paths, path, label="genesis artifact"),
                        "sha256": digest,
                    }

    # Fallback for first-genesis networks whose policy is still reconstructible from private state.
    document = _document(private_state)
    try:
        validators = document["networks"][network]["validators"]
    except (KeyError, TypeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_GENESIS_MISSING", "genesis artifact could not be found and validator state is missing") from exc
    if isinstance(validators, Mapping):
        for node, info in validators.items():
            if not isinstance(info, Mapping):
                continue
            address = info.get("address")
            if not isinstance(address, str):
                continue
            try:
                genesis, _alloc = _genesis_policy(document, network=network, initial_validator_address=address)
            except Exception:
                continue
            digest = hashlib.sha256(canonical_json(genesis)).hexdigest()
            if digest == expected:
                return genesis, {
                    "source": "reconstructed-from-private-genesis-policy",
                    "initial_validator_node": str(node),
                    "sha256": digest,
                }
    raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_GENESIS_MISSING", "canonical genesis JSON matching current topology SHA was not found")


def _bootnode(private_state: PrivateStateReadResult, identity_evidence: Mapping[str, Any]) -> dict[str, Any]:
    network = _identifier(identity_evidence.get("network"), "network")
    target_node = _identifier(identity_evidence.get("target", {}).get("node"), "target node")
    services = identity_evidence.get("current_topology", {}).get("services")
    nodes = identity_evidence.get("current_topology", {}).get("nodes")
    if not isinstance(services, Mapping) or not isinstance(nodes, list) or not nodes:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_BOOTNODE_MISSING", "current topology has no bootnode candidates")
    for raw_node in nodes:
        node = _identifier(raw_node, "current topology node")
        if node == target_node:
            continue
        service = services.get(node)
        if not isinstance(service, Mapping):
            continue
        controller_id = _identifier(service.get("controller_id"), f"{node} controller")
        private_key = _state_private_key(private_state, network=network, node=node)
        node_id = _public_node_id(private_key)
        route = ensure_service_validator_route(
            private_state,
            network=network,
            node=node,
            service=service,
            services=services,
        )
        host = str(route["advertised_host"])
        p2p_port = int(route["p2p_port"])
        return {
            "node": node,
            "controller_id": controller_id,
            "service_uuid": service.get("service_uuid"),
            "enode": f"enode://{node_id}@{host}:{p2p_port}",
            "node_id_sha256": hashlib.sha256(node_id.encode("ascii")).hexdigest(),
            "advertised_host": host,
            "p2p_port": p2p_port,
            "p2p_endpoint": route["p2p_endpoint"],
            "validator_route": dict(route),
        }
    raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_BOOTNODE_MISSING", "no current topology node has a usable bootnode identity")


def _sync_script(
    *,
    node: str,
    chain_id: int,
    genesis_sha256: str,
    expected_validators: list[str],
    bootnode_node_id: str,
    target_validator_node_id: str,
    target_validator_address: str,
) -> str:
    validators_json = json.dumps([item.lower() for item in expected_validators], separators=(",", ":"))
    return "\n".join([
        "import hashlib, json, os, time, traceback, urllib.request",
        f"RPC = 'http://{node}:8545'",
        f"EXPECTED_CHAIN_ID = {chain_id}",
        f"EXPECTED_GENESIS_SHA256 = '{genesis_sha256}'",
        f"EXPECTED_VALIDATORS = {validators_json}",
        f"EXPECTED_BOOTNODE_ID = '{bootnode_node_id.lower()}'",
        f"TARGET_VALIDATOR_NODE_ID = '{target_validator_node_id.lower()}'",
        f"TARGET_VALIDATOR_ADDRESS = '{target_validator_address.lower()}'",
        "PROOF = '/proof/proof.json'",
        "HEALTHY = '/proof/healthy'",
        "LAST_ERROR = '/proof/last-error.json'",
        "MAX_BLOCK_AGE_SECONDS = 120",
        "def rpc(method, params):",
        "    body = json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}, separators=(',', ':')).encode()",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    with urllib.request.urlopen(req, timeout=5) as response:",
        "        value = json.loads(response.read(1048576).decode())",
        "    if value.get('error') is not None or 'result' not in value:",
        "        raise RuntimeError(method + ' failed: ' + repr(value.get('error')))",
        "    return value['result']",
        "def normalize_node_id(value):",
        "    text = str(value or '').lower()",
        "    return text[2:] if text.startswith('0x') else text",
        "def write_json(path, payload):",
        "    temporary = path + '.tmp'",
        "    with open(temporary, 'w', encoding='utf-8') as handle:",
        "        json.dump(payload, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(temporary, path)",
        "def prove():",
        "    with open('/config/genesis.json', 'rb') as handle:",
        "        genesis_digest = hashlib.sha256(handle.read()).hexdigest()",
        "    if genesis_digest != EXPECTED_GENESIS_SHA256:",
        "        raise RuntimeError('genesis commitment mismatch')",
        "    chain_id = int(rpc('eth_chainId', []), 16)",
        "    if chain_id != EXPECTED_CHAIN_ID:",
        "        raise RuntimeError('chain id mismatch')",
        "    genesis = rpc('eth_getBlockByNumber', ['0x0', False])",
        "    if not isinstance(genesis, dict) or not genesis.get('hash'):",
        "        raise RuntimeError('genesis block missing')",
        "    local_info = rpc('admin_nodeInfo', [])",
        "    actual_node_id = normalize_node_id(local_info.get('id')) if isinstance(local_info, dict) else ''",
        "    if not actual_node_id:",
        "        raise RuntimeError('replica node identity missing')",
        "    if actual_node_id == TARGET_VALIDATOR_NODE_ID:",
        "        raise RuntimeError('replica node identity is the target validator identity')",
        "    peers = rpc('admin_peers', [])",
        "    if not isinstance(peers, list) or EXPECTED_BOOTNODE_ID not in json.dumps(peers, sort_keys=True).lower():",
        "        raise RuntimeError('expected bootnode peer missing')",
        "    if int(rpc('net_peerCount', []), 16) < 1:",
        "        raise RuntimeError('peer count is zero')",
        "    if rpc('eth_syncing', []) is not False:",
        "        raise RuntimeError('replica is still syncing')",
        "    validators = [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])]",
        "    if validators != EXPECTED_VALIDATORS:",
        "        raise RuntimeError('validator set mismatch')",
        "    if TARGET_VALIDATOR_ADDRESS in validators:",
        "        raise RuntimeError('target is already a validator')",
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    time.sleep(4)",
        "    second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first:",
        "        raise RuntimeError('block height did not advance')",
        "    latest = rpc('eth_getBlockByNumber', ['latest', False])",
        "    if not isinstance(latest, dict) or not latest.get('hash') or int(latest.get('number', '0x0'), 16) < second:",
        "        raise RuntimeError('latest block is missing')",
        "    block_time = int(latest.get('timestamp', '0x0'), 16)",
        "    current_time = int(time.time())",
        "    if block_time > current_time + 15 or current_time - block_time > MAX_BLOCK_AGE_SECONDS:",
        "        raise RuntimeError('latest block is stale')",
        "    proof = {'chain_id':chain_id,'genesis_block_present':True,'genesis_sha256':genesis_digest,'bootnode_peer_verified':True,'replica_node_id':actual_node_id,'target_validator_node_id':TARGET_VALIDATOR_NODE_ID,'replica_node_identity_distinct_from_target_validator':True,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'latest_block_hash':latest['hash'],'latest_block_number':second,'latest_block_timestamp':block_time,'syncing':False,'validator_set':validators,'target_validator_active':False,'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "    write_json(PROOF, proof)",
        "    try: os.unlink(LAST_ERROR)",
        "    except FileNotFoundError: pass",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle:",
        "        handle.write(str(int(time.time())))",
        "while True:",
        "    try:",
        "        prove()",
        "    except Exception as exc:",
        "        try: os.unlink(HEALTHY)",
        "        except FileNotFoundError: pass",
        "        write_json(LAST_ERROR, {'error':str(exc),'type':type(exc).__name__,'traceback':traceback.format_exc(limit=4),'observed_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})",
        "    time.sleep(6)",
        "",
    ])


def _replica_sync_compose(
    *,
    node: str,
    chain_id: int,
    genesis: Mapping[str, Any],
    genesis_sha256: str,
    expected_validators: list[str],
    bootnode_enode: str,
    target_validator_node_id: str,
    target_validator_address: str,
    candidate_p2p_port: int,
) -> str:
    candidate_p2p_port = int(candidate_p2p_port)
    if not 1 <= candidate_p2p_port <= 65535:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_ROUTE_INVALID", "candidate P2P port is invalid")
    encoded_genesis = base64.b64encode(canonical_json(dict(genesis))).decode("ascii")
    bootnode_node_id = _node_id_from_enode(bootnode_enode)
    script = _sync_script(
        node=node,
        chain_id=chain_id,
        genesis_sha256=genesis_sha256,
        expected_validators=expected_validators,
        bootnode_node_id=bootnode_node_id,
        target_validator_node_id=target_validator_node_id,
        target_validator_address=target_validator_address,
    )
    indented_script = "\n".join("        " + line for line in script.splitlines())
    return "\n".join([
        f"name: {node}",
        "",
        "services:",
        "  mother-replica-init:",
        f"    image: {_INIT_IMAGE}",
        '    restart: "no"',
        "    volumes:",
        "      - mother-config:/config",
        "      - mother-data:/var/lib/besu",
        "    command:",
        "      - sh",
        "      - -ec",
        "      - |",
        "        umask 077",
        f"        printf '%s' '{encoded_genesis}' | base64 -d > /config/genesis.json",
        "        od -An -N32 -tx1 /dev/urandom | tr -d ' \\n' > /config/nodekey",
        '        test "$$(wc -c < /config/nodekey)" -eq 64',
        "        rm -rf /var/lib/besu/*",
        "        mkdir -p /var/lib/besu",
        "        chown -R 1000:1000 /config /var/lib/besu",
        "        chmod 0400 /config/nodekey",
        "        chmod 0444 /config/genesis.json",
        f"  {node}:",
        f"    image: {_BESU_IMAGE}",
        '    restart: unless-stopped',
        "    depends_on:",
        "      mother-replica-init:",
        "        condition: service_completed_successfully",
        "    command:",
        "      - --data-path=/var/lib/besu",
        "      - --genesis-file=/config/genesis.json",
        "      - --node-private-key-file=/config/nodekey",
        f"      - --network-id={chain_id}",
        "      - --sync-mode=FULL",
        "      - --data-storage-format=BONSAI",
        "      - --p2p-enabled=true",
        f"      - --p2p-port={candidate_p2p_port}",
        "      - --discovery-enabled=true",
        f"      - --bootnodes={bootnode_enode}",
        "      - --rpc-http-enabled=true",
        "      - --rpc-http-host=0.0.0.0",
        "      - --rpc-http-port=8545",
        "      - --rpc-http-api=ETH,NET,WEB3,QBFT,ADMIN",
        f"      - --host-allowlist=localhost,127.0.0.1,{node}",
        "      - --min-gas-price=0",
        "    volumes:",
        "      - mother-config:/config:ro",
        "      - mother-data:/var/lib/besu",
        "    labels:",
        "      main_computer.mother.stage: add-node-replica-sync",
        f"      main_computer.mother.node: {node}",
        "      main_computer.mother.replica-sync: proof-active",
        "      main_computer.mother.validator-activation: blocked",
        "  mother-replica-sync-guardian:",
        f"    image: {_PROOF_IMAGE}",
        "    restart: unless-stopped",
        "    read_only: true",
        "    depends_on:",
        f"      {node}:",
        "        condition: service_started",
        "    command:",
        "      - python",
        "      - -u",
        "      - -c",
        "      - |",
        indented_script,
        "    healthcheck:",
        "      test:",
        "        - CMD",
        "        - python",
        "        - -c",
        "        - import os,time; p='/proof/healthy'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 24",
        "      start_period: 30s",
        "    volumes:",
        "      - mother-config:/config:ro",
        "      - mother-sync-proof:/proof",
        "",
        "volumes:",
        "  mother-config:",
        "  mother-data:",
        "  mother-sync-proof:",
        "",
    ])


def _compose_report(*, observed: str, expected: str, node: str) -> dict[str, Any]:
    observed_sha = hashlib.sha256(observed.encode("utf-8")).hexdigest()
    expected_sha = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    exact = observed_sha == expected_sha
    try:
        observed_doc = yaml.safe_load(observed)
    except yaml.YAMLError:
        observed_doc = None
    try:
        expected_doc = yaml.safe_load(expected)
    except yaml.YAMLError:
        expected_doc = None
    yaml_equivalent = observed_doc == expected_doc and observed_doc is not None
    observed_text = observed
    expected_p2p_match = re.search(r"--p2p-port=(\d+)", expected)
    expected_p2p_port = expected_p2p_match.group(1) if expected_p2p_match else "30303"
    services = observed_doc.get("services") if isinstance(observed_doc, Mapping) else {}
    replica = services.get(node) if isinstance(services, Mapping) and isinstance(services.get(node), Mapping) else {}
    guardian = services.get("mother-replica-sync-guardian") if isinstance(services, Mapping) and isinstance(services.get("mother-replica-sync-guardian"), Mapping) else {}
    checks = {
        "has_replica_service": bool(replica),
        "has_sync_guardian": bool(guardian),
        "guardian_internal_only": bool(guardian) and not any(key in guardian for key in ("ports", "expose", "domains", "fqdn")),
        "guardian_read_only": guardian.get("read_only") is True,
        "guardian_healthcheck_present": isinstance(guardian.get("healthcheck"), Mapping),
        "uses_genesis_file_arg": "--genesis-file=/config/genesis.json" in observed_text,
        "uses_node_private_key_file": "--node-private-key-file=/config/nodekey" in observed_text,
        "sync_mode_full": "--sync-mode=FULL" in observed_text,
        "bootnode_present": "--bootnodes=enode://" in observed_text,
        "p2p_container_port_enabled": f"--p2p-port={expected_p2p_port}" in observed_text,
        "host_p2p_tcp_port_absent": f"{expected_p2p_port}:{expected_p2p_port}/tcp" not in observed_text,
        "host_p2p_udp_port_absent": f"{expected_p2p_port}:{expected_p2p_port}/udp" not in observed_text,
        "rpc_not_host_published": "8545:8545" not in observed_text,
        "validator_activation_blocked": "main_computer.mother.validator-activation: blocked" in observed_text,
        "replica_sync_proof_active": "main_computer.mother.replica-sync: proof-active" in observed_text,
        "no_vote_script": "qbft_proposeValidatorVote" not in observed_text,
    }
    missing = [key for key, value in checks.items() if value is not True]
    verified = exact or yaml_equivalent or not missing
    return {
        "expected_compose_sha256": expected_sha,
        "observed_compose_sha256": observed_sha,
        "exact_match": exact,
        "yaml_equivalent": yaml_equivalent,
        "coolify_normalized_compose_accepted": verified and not exact,
        "sync_compose_verified": verified,
        "missing_checks": missing,
        **checks,
    }


def _load_identity_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    expected_sha256: str | None,
    max_age_seconds: int,
    release_max_age_seconds: int,
    add_do_max_age_seconds: int,
    add_do_release_max_age_seconds: int,
    transaction_max_age_seconds: int,
    baseline_max_age_seconds: int,
    now: datetime | None,
) -> tuple[dict[str, Any], Path, str, str]:
    # Release expiration protects the one live identity mutation; it must not
    # make durable already-written identity evidence unusable for later phases.
    # Validate the evidence document itself here instead of re-running the
    # identity release freshness gate.
    del release_max_age_seconds, add_do_max_age_seconds, add_do_release_max_age_seconds, transaction_max_age_seconds, baseline_max_age_seconds
    resolved = Path(evidence_path).resolve(strict=False)
    allowed = _root(paths, _IDENTITY_EVIDENCE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_PATH_INVALID", "add-node identity evidence is outside its directory") from exc
    document, raw, file_sha = _canonical_file(resolved)
    if expected_sha256 is not None and file_sha != expected_sha256:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_IDENTITY_MISMATCH", "acknowledged add-node identity evidence SHA does not match")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_IDENTITY_STALE", "add-node identity evidence is outside the freshness window")
    target = document.get("target")
    receipts = document.get("mutation_receipts")
    clean = (
        document.get("kind") == "main_computer.mother.deployment_node_add_identity_evidence.v1"
        and document.get("mother_binding") == _binding(private_state)
        and document.get("status") == "pass"
        and document.get("failure") is None
        and isinstance(target, Mapping)
        and isinstance(receipts, list)
        and len(receipts) == len(_IDENTITY_ENV_KEYS)
        and all(isinstance(item, Mapping) and item.get("status") == "succeeded" and item.get("live_write_acknowledged") is True for item in receipts)
        and document.get("identity_install_performed") is True
        and document.get("identity_install_proven") is True
        and document.get("replica_sync_performed") is False
        and document.get("validator_admission_performed") is False
        and document.get("routing_or_topology_published") is False
        and document.get("public_endpoint_created") is False
        and document.get("summary", {}).get("generic_topology_diff") is True
        and document.get("summary", {}).get("hardcoded_stage_target") is False
        and document.get("next_phase") == f"add-node-replica-sync-{document.get('network')}"
        and not _contains_sensitive(document)
    )
    if not clean:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_IDENTITY_INVALID", "add-node identity evidence is not a clean replica-sync gate")
    return document, resolved, file_sha, hashlib.sha256(raw).hexdigest()

def build_node_add_replica_sync_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    identity_evidence_path: Path,
    *,
    acknowledged_add_node_identity_evidence_sha256: str,
    max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not _MIN_RELEASE_SECONDS <= int(expires_in_seconds) <= _MAX_RELEASE_SECONDS:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_RELEASE_INVALID", "release expiry must be between 1 and 900 seconds")
    identity_evidence, resolved, evidence_sha, byte_sha = _load_identity_evidence(
        paths,
        private_state,
        Path(identity_evidence_path),
        expected_sha256=_sha256(acknowledged_add_node_identity_evidence_sha256, "acknowledged add-node identity evidence sha256"),
        max_age_seconds=max_age_seconds,
        release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    network = _identifier(identity_evidence["network"], "network")
    target = dict(identity_evidence["target"])
    node = _identifier(target["node"], "target node")
    controller_id = _identifier(target["controller_id"], "target controller")
    service_uuid = _identifier(target["created_service_uuid"], "created service UUID")
    chain_id = identity_evidence.get("current_topology", {}).get("chain_id")
    if not isinstance(chain_id, int) or chain_id <= 0:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_CHAIN_INVALID", "current topology chain ID is invalid")
    genesis_sha = _sha256(identity_evidence.get("current_topology", {}).get("genesis_sha256"), "current topology genesis SHA-256")
    genesis, genesis_source = _discover_genesis(paths, private_state, network=network, genesis_sha256=genesis_sha)
    current_validators = identity_evidence.get("current_topology", {}).get("validator_set")
    if not isinstance(current_validators, list) or not current_validators:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_VALIDATORS_INVALID", "current validator set is missing")
    expected_validators = [_address(item, "current validator") for item in current_validators]
    target_validator = _address(target.get("validator_address"), "target validator address")
    if target_validator in expected_validators:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_VALIDATORS_INVALID", "target validator is already active before replica sync")
    bootnode = _bootnode(private_state, identity_evidence)
    candidate_route = target.get("validator_route") if isinstance(target.get("validator_route"), Mapping) else None
    if not isinstance(candidate_route, Mapping):
        candidate_route = identity_evidence.get("prepared_post_add_topology", {}).get("target_validator_route")
    if not isinstance(candidate_route, Mapping):
        candidate_route = validator_route_from_record(target) or {}
    candidate_p2p_port = int(candidate_route.get("p2p_port") or 30303)
    if not 1 <= candidate_p2p_port <= 65535:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_ROUTE_INVALID", "candidate validator route P2P port is invalid")
    target_private_key = _state_private_key(private_state, network=network, node=node)
    target_validator_node_id = _public_node_id(target_private_key)
    compose = _replica_sync_compose(
        node=node,
        chain_id=chain_id,
        genesis=genesis,
        genesis_sha256=genesis_sha,
        expected_validators=expected_validators,
        bootnode_enode=bootnode["enode"],
        target_validator_node_id=target_validator_node_id,
        target_validator_address=target_validator,
        candidate_p2p_port=candidate_p2p_port,
    )
    compose_bytes = compose.encode("utf-8")
    service_uuid_quoted = urllib.parse.quote(service_uuid, safe="")
    body = {
        "name": node,
        "docker_compose_raw": base64.b64encode(compose_bytes).decode("ascii"),
    }
    body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
    created = _timestamp(created_at, now=now)
    expires = (_parse_utc(created, "created_at") + timedelta(seconds=int(expires_in_seconds))).isoformat(timespec="seconds").replace("+00:00", "Z")
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created,
        "expires_at": expires,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": identity_evidence["mode"],
        "source_add_identity_evidence": {
            "locator": _relative(paths, resolved, label="add-node identity evidence"),
            "sha256": evidence_sha,
            "byte_sha256": byte_sha,
        },
        "source_add_do_evidence": dict(identity_evidence["source_add_do_evidence"]),
        "source_prep_transaction": dict(identity_evidence["source_prep_transaction"]),
        "source_baseline_evidence": dict(identity_evidence["source_baseline_evidence"]),
        "target": target,
        "current_topology": dict(identity_evidence["current_topology"]),
        "standby_topology": dict(identity_evidence["standby_topology"]),
        "prepared_post_add_topology": dict(identity_evidence["prepared_post_add_topology"]),
        "topology_diff": dict(identity_evidence["topology_diff"]),
        "identity_commitments": list(identity_evidence.get("identity_commitments", [])),
        "proof_plan": {
            "target_node": node,
            "controller_id": controller_id,
            "service_uuid": service_uuid,
            "chain_id": chain_id,
            "genesis_sha256": genesis_sha,
            "genesis_source": genesis_source,
            "expected_validator_set": expected_validators,
            "target_validator_address": target_validator,
            "candidate_validator_route": dict(candidate_route),
            "candidate_p2p_port": candidate_p2p_port,
            "bootnode": bootnode,
            "replica_node_identity_source": "runtime-generated-non-validator",
            "target_validator_node_id_sha256": hashlib.sha256(target_validator_node_id.encode("ascii")).hexdigest(),
            "sync_compose": {
                "sha256": hashlib.sha256(compose_bytes).hexdigest(),
                "semantic_sha256": hashlib.sha256(canonical_json(yaml.safe_load(compose))).hexdigest(),
                "byte_length": len(compose_bytes),
                "canonical_text": compose,
                "guardian_image": _PROOF_IMAGE,
                "guardian_public_ports": [],
                "guardian_domains": [],
                "host_rpc_mapping_present": False,
                "host_p2p_mapping_present": False,
                "host_p2p_publication_authorized": False,
                "candidate_p2p_port": candidate_p2p_port,
            },
            "preconditions": [
                {"controller_id": controller_id, "method": "GET", "endpoint": f"/api/v1/services/{service_uuid_quoted}", "assertion": "target standby service still exists"},
                {"controller_id": controller_id, "method": "GET", "endpoint": f"/api/v1/services/{service_uuid_quoted}/envs", "assertion": "identity env keys remain installed"},
            ],
            "mutations": [
                {"ordinal": 1, "mutation_id": f"{node}.install-replica-sync-compose", "controller_id": controller_id, "method": "PATCH", "endpoint": f"/api/v1/services/{service_uuid_quoted}", "canonical_request_body": body, "body_sha256": body_sha, "success_statuses": [200, 201, 202]},
                {"ordinal": 2, "mutation_id": f"{node}.start-replica-sync-compose", "controller_id": controller_id, "method": "POST", "endpoint": f"/api/v1/services/{service_uuid_quoted}/start", "canonical_request_body": None, "body_sha256": None, "success_statuses": [200, 201, 202]},
            ],
            "proof": {
                "transport": "coolify-control-plane-plus-temporary-docker-helper",
                "manual_ssh_required": False,
                "public_endpoint_created": False,
                "guardian_internal_only": True,
                "predicates": [
                    "genesis-file-sha256",
                    "chain-id",
                    "genesis-block-present",
                    "runtime-replica-node-id-present",
                    "target-node-id-not-validator-node-id",
                    "bootnode-peer-present",
                    "peer-count-positive",
                    "eth-syncing-false",
                    "fresh-block-height-advancing",
                    "pre-add-validator-set",
                    "target-not-validator",
                ],
                "success_signal": "target node and mother-replica-sync-guardian components report running:healthy under the replica-sync proof Compose",
            },
        },
        "authority": {
            "authorization_source": "explicit-operator-release",
            "requested_use_limit": 1,
            "live_execution_authorized": True,
            "service_creation_authorized": False,
            "identity_install_authorized": False,
            "replica_start_authorized": True,
            "replica_sync_authorized": True,
            "temporary_docker_helper_service_authorized": True,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
        },
        "policy": {
            "compiler": "mother-native-add-node-replica-sync-v1",
            "allowed_http_methods": ["GET", "PATCH", "POST", "DELETE"],
            "coolify_control_plane_only": False,
            "temporary_docker_helper_service_authorized": True,
            "requested_use_limit": 1,
            "identity_install_previously_performed": True,
            "replica_sync_authorized": True,
            "replica_node_only": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "host_p2p_mapping_present": False,
            "host_p2p_publication_authorized": False,
            "private_keys_materialized_in_memory_only": True,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
        },
        "summary": {
            "clean": True,
            "executor_implemented": True,
            "generic_topology_diff": True,
            "hardcoded_stage_target": False,
            "target_node": node,
            "target_host": controller_id,
            "created_service_uuid": service_uuid,
            "target_p2p_port": candidate_p2p_port,
            "target_p2p_endpoint": candidate_route.get("p2p_endpoint"),
            "identity_install_previously_performed": True,
            "replica_sync_authorized": True,
            "validator_admission_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "next_phase": f"add-node-replica-sync-{network}",
        },
    }
    release["node_add_replica_sync_release_sha256"] = _digest_without(release, "node_add_replica_sync_release_sha256")
    if _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_SENSITIVE", "node-add replica-sync release contains sensitive material")
    return release


def verify_node_add_replica_sync_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    max_age_seconds: int = 900,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = Path(release_path).resolve(strict=False)
    allowed = _root(paths, _RELEASE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_PATH_INVALID", "node-add replica-sync release is outside its directory") from exc
    document, _raw, _file_sha = _canonical_file(resolved)
    digest = _digest_without(document, "node_add_replica_sync_release_sha256")
    if document.get("kind") != _RELEASE_KIND or document.get("node_add_replica_sync_release_sha256") != digest or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_RELEASE_INVALID", "node-add replica-sync release is invalid")
    age = _age_seconds(document.get("created_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_RELEASE_STALE", "node-add replica-sync release is outside the freshness window")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if _parse_utc(document.get("expires_at"), "expires_at") < reference:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_RELEASE_EXPIRED", "node-add replica-sync release has expired")
    source = document.get("source_add_identity_evidence")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_RELEASE_INVALID", "source add-node identity evidence binding is missing")
    identity_path = _resolve_under(paths, source.get("locator"), _IDENTITY_EVIDENCE_DIRECTORY, label="add-node identity evidence")
    identity_evidence, _resolved_identity, identity_sha, _byte_sha = _load_identity_evidence(
        paths,
        private_state,
        identity_path,
        expected_sha256=source.get("sha256"),
        max_age_seconds=identity_max_age_seconds,
        release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if document.get("target") != identity_evidence.get("target"):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_RELEASE_INVALID", "release target does not match identity evidence")
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{digest}.json"
    target = document["target"]
    return {
        "clean": True,
        "release_path": str(resolved),
        "node_add_replica_sync_release_sha256": digest,
        "release_already_claimed": claim_path.exists(),
        "age_seconds": age,
        "expires_at": document["expires_at"],
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "source_add_identity_evidence_sha256": identity_sha,
        "source_add_do_evidence_sha256": document["source_add_do_evidence"]["sha256"],
        "source_prep_transaction_sha256": document["source_prep_transaction"]["sha256"],
        "source_baseline_evidence_sha256": document["source_baseline_evidence"]["sha256"],
        "target_node": target["node"],
        "target_host": target["controller_id"],
        "created_service_uuid": target["created_service_uuid"],
        "chain_id": document["proof_plan"]["chain_id"],
        "genesis_sha256": document["proof_plan"]["genesis_sha256"],
        "bootnode_node": document["proof_plan"]["bootnode"]["node"],
        "replica_sync_authorized": True,
        "validator_admission_authorized": False,
        "generic_topology_diff": True,
        "hardcoded_stage_target": False,
        "next_phase": document["summary"]["next_phase"],
    }


def execute_node_add_replica_sync_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 900,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    acknowledged = _sha256(acknowledged_release_sha256, "acknowledged release sha256")
    verified = verify_node_add_replica_sync_release(
        paths,
        private_state,
        Path(release_path),
        max_age_seconds=max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if verified["node_add_replica_sync_release_sha256"] != acknowledged:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_ACK_MISMATCH", "acknowledged release SHA does not match")
    if verified.get("release_already_claimed") is True:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_RELEASE_ALREADY_CLAIMED", "node-add replica-sync release was already claimed")
    release, _raw, _file_sha = _canonical_file(Path(release_path))
    claim_path = _claim_release(paths, release, operation=operation)
    network = _identifier(release["network"], "network")
    target = release["target"]
    node = _identifier(target["node"], "target node")
    controller_id = _identifier(target["controller_id"], "target controller")
    service_uuid = _identifier(target["created_service_uuid"], "created service UUID")
    controller = resolve_coolify_controller(private_state, network, controller_id)
    plan = release["proof_plan"]

    started_at = _timestamp(now=now)
    preconditions: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None
    proof_report: dict[str, Any] | None = None
    guardian_start: dict[str, Any] | None = None
    node_readiness: dict[str, Any] | None = None
    component_health: dict[str, Any] | None = None
    try:
        service_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
        service_detail = _http(controller, "GET", service_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not service_detail["ok"]:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_PRECONDITION_FAILED", f"Coolify service detail failed with HTTP {service_detail['status']}")
        service_record = _service_record(service_detail["payload"], service_uuid=service_uuid, node=node)
        preconditions.append({
            "name": "target-standby-service-exists-before-replica-sync",
            "controller_id": controller_id,
            "method": "GET",
            "endpoint": service_endpoint,
            "status": service_detail["status"],
            "response_sha256": service_detail["response_sha256"],
            "verified": True,
            "service_status": _service_status(service_record),
        })

        env_endpoint = f"{service_endpoint}/envs"
        envs = _http(controller, "GET", env_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not envs["ok"]:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_PRECONDITION_FAILED", f"Coolify env GET failed with HTTP {envs['status']}")
        env_records = _raw_items(envs["payload"])
        identity_results: list[dict[str, Any]] = []
        commitments = {
            item.get("environment_key"): item
            for item in release.get("identity_commitments", [])
            if isinstance(item, Mapping) and isinstance(item.get("environment_key"), str)
        }
        for key in _IDENTITY_ENV_KEYS:
            matches = [item for item in env_records if _env_key(item) == key]
            result = {"environment_key": key, "matches": len(matches), "present": len(matches) == 1, "commitment_verified_when_visible": None}
            if len(matches) != 1:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_IDENTITY_PRECONDITION_FAILED", f"Coolify does not expose one installed {key}")
            visible = _visible_value(matches[0])
            commitment = commitments.get(key, {})
            if visible is not None and isinstance(commitment.get("value_sha256"), str):
                observed = hashlib.sha256(visible.encode("utf-8")).hexdigest()
                result["observed_value_sha256"] = observed
                result["commitment_verified_when_visible"] = observed == commitment["value_sha256"]
                if result["commitment_verified_when_visible"] is not True:
                    raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_IDENTITY_PRECONDITION_FAILED", f"Coolify identity commitment does not match for {key}")
            identity_results.append(result)
        preconditions.append({
            "name": "target-identity-env-installed-before-replica-sync",
            "controller_id": controller_id,
            "method": "GET",
            "endpoint": env_endpoint,
            "status": envs["status"],
            "response_sha256": envs["response_sha256"],
            "verified": True,
            "identity_env_keys": identity_results,
        })

        for mutation in plan["mutations"]:
            body = mutation.get("canonical_request_body")
            response = _http(
                controller,
                mutation["method"],
                mutation["endpoint"],
                body=dict(body) if isinstance(body, Mapping) else None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            ok = response["status"] in mutation["success_statuses"]
            receipts.append({
                "ordinal": mutation["ordinal"],
                "mutation_id": mutation["mutation_id"],
                "node": node,
                "controller_id": controller_id,
                "method": mutation["method"],
                "endpoint": mutation["endpoint"],
                "body_sha256": mutation.get("body_sha256"),
                "status": "succeeded" if ok else "failed",
                "live_write_acknowledged": ok,
                "response": _safe_response(response),
            })
            if not ok:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_MUTATION_FAILED", f"Coolify rejected replica-sync mutation {mutation['ordinal']}")

        _record_replica_sync_target_diagnostic(
            controller=controller,
            controller_id=controller_id,
            service_uuid=service_uuid,
            node=node,
            phase="replica-sync-target-before-guardian-start",
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            observations=observations,
        )

        node_readiness = _wait_for_replica_sync_node_ready(
            controller=controller,
            controller_id=controller_id,
            service_uuid=service_uuid,
            node=node,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            opener=opener,
            observations=observations,
        )
        if node_readiness.get("healthy") is not True:
            summary = (
                node_readiness.get("component_summary")
                if isinstance(node_readiness.get("component_summary"), Mapping)
                else {}
            )
            last_status = summary.get("node_status") or "unknown"
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_NODE_NOT_READY",
                f"target replica node did not reach running:healthy before guardian start "
                f"(last status {last_status!r})",
            )

        readiness_summary = (
            node_readiness.get("component_summary")
            if isinstance(node_readiness.get("component_summary"), Mapping)
            else {}
        )
        if readiness_summary.get("guardian_running_healthy") is True:
            guardian_start = {
                "status": "pass",
                "reason": "guardian-already-running-healthy",
                "observations": [],
                "temporary_service_created": False,
                "temporary_service_deleted": False,
                "temporary_service_uuid": None,
                "temporary_service_name": None,
                "target_service_uuid": service_uuid,
                "forced_service": "mother-replica-sync-guardian",
                "node_recreated": False,
                "init_recreated": False,
                "parent_redeploy_performed": False,
                "parent_restart_performed": False,
                "skipped": True,
            }
        else:
            guardian_start = _run_replica_sync_guardian_start(
                private_state,
                network=network,
                controller_id=controller_id,
                service_uuid=service_uuid,
                node=node,
                compose_text=plan["sync_compose"]["canonical_text"],
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                opener=opener,
            )
        if guardian_start.get("status") != "pass":
            _record_replica_sync_target_diagnostic(
                controller=controller,
                controller_id=controller_id,
                service_uuid=service_uuid,
                node=node,
                phase="replica-sync-target-after-guardian-start-failure",
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                observations=observations,
            )
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_GUARDIAN_START_FAILED",
                str(guardian_start.get("reason") or "replica-sync guardian did not start"),
            )

        component_health = _wait_for_replica_sync_components(
            controller=controller,
            controller_id=controller_id,
            service_uuid=service_uuid,
            node=node,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            opener=opener,
            observations=observations,
        )
        if component_health.get("healthy") is not True:
            summary = component_health.get("component_summary") if isinstance(component_health.get("component_summary"), Mapping) else {}
            last_status = summary.get("guardian_status") or summary.get("node_status") or "unknown"
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_NOT_HEALTHY",
                f"target replica sync components did not reach running:healthy (last status {last_status!r})",
            )

        post_detail = _http(controller, "GET", service_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not post_detail["ok"]:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_POSTCONDITION_FAILED", f"Coolify service detail failed after deploy with HTTP {post_detail['status']}")
        post_record = _service_record(post_detail["payload"], service_uuid=service_uuid, node=node)
        observed_compose = _compose_from_service_record(post_record)
        proof_report = _compose_report(observed=observed_compose, expected=plan["sync_compose"]["canonical_text"], node=node)
        preconditions.append({
            "name": "target-replica-sync-proof-compose",
            "controller_id": controller_id,
            "method": "GET",
            "endpoint": service_endpoint,
            "status": post_detail["status"],
            "response_sha256": post_detail["response_sha256"],
            "verified": proof_report.get("sync_compose_verified") is True,
            "compose_verification": proof_report,
        })
        if proof_report.get("sync_compose_verified") is not True:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_POSTCONDITION_FAILED", "live target Compose does not match replica-sync proof semantics")
    except MotherDeploymentNodeAddReplicaSyncError as exc:
        failure = {"code": exc.code, "message": str(exc)[:512]}
    except Exception as exc:  # pragma: no cover
        failure = {"code": "MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_UNEXPECTED_FAILURE", "message": str(exc)[:512]}

    completed_at = _timestamp(now=now)
    complete = failure is None and len(receipts) == 2 and all(item.get("status") == "succeeded" for item in receipts)
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "implementation_version": _IMPLEMENTATION_VERSION,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "pass" if complete else "failed",
        "failure": failure,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": release["mode"],
        "release": {
            "locator": _relative(paths, Path(release_path), label="add-node replica-sync release"),
            "sha256": acknowledged,
        },
        "execution_claim": {
            "locator": _relative(paths, claim_path, label="add-node replica-sync execution claim"),
        },
        "source_add_identity_evidence": dict(release["source_add_identity_evidence"]),
        "source_add_do_evidence": dict(release["source_add_do_evidence"]),
        "source_prep_transaction": dict(release["source_prep_transaction"]),
        "source_baseline_evidence": dict(release["source_baseline_evidence"]),
        "target": dict(target),
        "current_topology": dict(release["current_topology"]),
        "standby_topology": dict(release["standby_topology"]),
        "prepared_post_add_topology": dict(release["prepared_post_add_topology"]),
        "topology_diff": dict(release["topology_diff"]),
        "identity_commitments": list(release.get("identity_commitments", [])),
        "proof_plan_summary": {
            "chain_id": plan["chain_id"],
            "genesis_sha256": plan["genesis_sha256"],
            "expected_validator_set": list(plan["expected_validator_set"]),
            "target_validator_address": plan["target_validator_address"],
            "candidate_validator_route": dict(plan.get("candidate_validator_route", {})),
            "candidate_p2p_port": plan.get("candidate_p2p_port"),
            "bootnode": dict(plan["bootnode"]),
            "sync_compose_sha256": plan["sync_compose"]["sha256"],
            "replica_node_identity_source": plan["replica_node_identity_source"],
            "target_validator_node_id_sha256": plan["target_validator_node_id_sha256"],
        },
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "health_observations": observations,
        "replica_sync_node_readiness": node_readiness,
        "replica_sync_guardian_start": guardian_start,
        "replica_sync_component_health": component_health,
        "proof": {
            "mode": "internal-health-assertion-bound-to-replica-sync-compose",
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "host_p2p_mapping_present": False,
            "guardian_internal_only": True,
            "service_status": (
                component_health.get("component_summary", {}).get("guardian_status")
                if isinstance(component_health, Mapping)
                else None
            ),
            "component_health": component_health,
            "compose_verification": proof_report,
            "predicates_proven_by_guardian": list(plan["proof"]["predicates"]),
            "chain_id": plan["chain_id"],
            "genesis_sha256": plan["genesis_sha256"],
            "expected_validator_set": list(plan["expected_validator_set"]),
            "target_validator_address": plan["target_validator_address"],
            "target_validator_active": False,
            "candidate_validator_route": dict(plan.get("candidate_validator_route", {})),
            "candidate_p2p_port": plan.get("candidate_p2p_port"),
            "bootnode_node": plan["bootnode"]["node"],
            "bootnode_node_id_sha256": plan["bootnode"]["node_id_sha256"],
            "replica_node_identity_source": plan["replica_node_identity_source"],
            "target_validator_node_id_sha256": plan["target_validator_node_id_sha256"],
        },
        "authority": {
            "release_consumed": True,
            "service_creation_authorized": False,
            "identity_install_authorized": False,
            "replica_start_authorized": True,
            "replica_sync_authorized": True,
            "replica_sync_proven": complete,
            "temporary_docker_helper_service_authorized": True,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH", "POST", "DELETE"],
            "coolify_control_plane_only": False,
            "temporary_docker_helper_service_authorized": True,
            "identity_install_previously_performed": True,
            "replica_node_only": True,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "host_p2p_mapping_present": False,
            "host_p2p_publication_authorized": False,
            "private_state_updated": False,
            "secrets_in_output": False,
            "service_deploy_or_start_performed": complete,
            "temporary_docker_helper_service_performed": bool(guardian_start and guardian_start.get("temporary_service_created") is True),
            "replica_sync_performed": complete,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "routing_or_topology_published": False,
        },
        "service_mutation_count": len([item for item in receipts if item.get("live_write_acknowledged")]),
        "identity_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_admission_performed": False,
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "replica_sync_performed": complete,
        "remaining_phases": [
            "admit-validator",
            "post-admission-observe",
            "finalize-operation",
        ],
        "next_phase": f"add-node-validator-admission-{network}" if complete else "manual-review-required",
    }
    evidence["summary"] = {
        "clean": complete,
        "complete": complete,
        "target_node": node,
        "target_host": controller_id,
        "created_service_uuid": service_uuid,
        "generic_topology_diff": True,
        "hardcoded_stage_target": False,
        "identity_install_previously_performed": True,
        "replica_sync_performed": complete,
        "replica_sync_proven": complete,
        "service_running_healthy": complete,
        "component_aware_health_verified": bool(component_health and component_health.get("healthy") is True),
        "sync_compose_verified": bool(proof_report and proof_report.get("sync_compose_verified") is True),
        "genesis_file_commitment_verified": complete,
        "chain_id_verified": complete,
        "genesis_block_present": complete,
        "replica_node_identity_verified": complete,
        "bootnode_peer_verified": complete,
        "peer_count_positive": complete,
        "sync_complete": complete,
        "blocks_advancing": complete,
        "latest_block_fresh": complete,
        "validator_set_verified": complete,
        "target_not_validator": complete,
        "validator_admission_performed": False,
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "routing_or_topology_published": False,
        "network_access_performed": bool(preconditions or receipts or observations),
        "live_mutation_performed": any(item.get("live_write_acknowledged") for item in receipts),
        "mutation_count": len([item for item in receipts if item.get("live_write_acknowledged")]),
        "next_phase": evidence["next_phase"],
    }
    if _contains_sensitive(evidence):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_SENSITIVE", "node-add replica-sync evidence contains sensitive material")
    path, digest = _write_evidence(paths, evidence, operation=operation)
    return {**evidence, "evidence": {"path": str(path), "sha256": digest}}


def verify_node_add_replica_sync_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
    release_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    del release_max_age_seconds
    resolved = Path(evidence_path).resolve(strict=False)
    allowed = _root(paths, _EVIDENCE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_PATH_INVALID", "node-add replica-sync evidence is outside its directory") from exc
    document, _raw, file_sha = _canonical_file(resolved)
    if document.get("kind") != _EVIDENCE_KIND or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_EVIDENCE_INVALID", "node-add replica-sync evidence is invalid")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_EVIDENCE_STALE", "node-add replica-sync evidence is outside the freshness window")
    release_binding = document.get("release")
    if not isinstance(release_binding, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_EVIDENCE_INVALID", "release binding is missing")
    release_path = _resolve_under(paths, release_binding.get("locator"), _RELEASE_DIRECTORY, label="add-node replica-sync release")
    release_document, _release_raw, _release_file_sha = _canonical_file(release_path)
    release_digest = _digest_without(release_document, "node_add_replica_sync_release_sha256")
    if (
        release_document.get("kind") != _RELEASE_KIND
        or release_document.get("node_add_replica_sync_release_sha256") != release_digest
        or release_binding.get("sha256") != release_digest
        or release_document.get("mother_binding") != _binding(private_state)
        or _contains_sensitive(release_document)
    ):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_EVIDENCE_INVALID", "release binding is invalid")
    source = release_document.get("source_add_identity_evidence")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_EVIDENCE_INVALID", "release source identity binding is missing")
    identity_path = _resolve_under(paths, source.get("locator"), _IDENTITY_EVIDENCE_DIRECTORY, label="add-node identity evidence")
    _load_identity_evidence(
        paths,
        private_state,
        identity_path,
        expected_sha256=source.get("sha256"),
        max_age_seconds=identity_max_age_seconds,
        release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    summary = document.get("summary")
    proof = document.get("proof")
    authority = document.get("authority")
    receipts = document.get("mutation_receipts")
    clean = (
        document.get("status") == "pass"
        and document.get("failure") is None
        and isinstance(summary, Mapping)
        and isinstance(proof, Mapping)
        and isinstance(authority, Mapping)
        and isinstance(receipts, list)
        and len(receipts) == 2
        and all(isinstance(item, Mapping) and item.get("status") == "succeeded" and item.get("live_write_acknowledged") is True for item in receipts)
        and summary.get("clean") is True
        and summary.get("replica_sync_performed") is True
        and summary.get("replica_sync_proven") is True
        and summary.get("service_running_healthy") is True
        and summary.get("sync_compose_verified") is True
        and summary.get("genesis_file_commitment_verified") is True
        and summary.get("chain_id_verified") is True
        and summary.get("bootnode_peer_verified") is True
        and summary.get("sync_complete") is True
        and summary.get("validator_set_verified") is True
        and summary.get("target_not_validator") is True
        and summary.get("validator_admission_performed") is False
        and summary.get("routing_or_topology_published") is False
        and summary.get("public_endpoint_created") is False
        and summary.get("generic_topology_diff") is True
        and summary.get("hardcoded_stage_target") is False
        and proof.get("guardian_internal_only") is True
        and proof.get("host_rpc_mapping_present") is False
        and proof.get("host_p2p_mapping_present") is False
        and authority.get("validator_admission_authorized") is False
        and authority.get("validator_vote_authorized") is False
        and authority.get("routing_or_topology_publication_authorized") is False
        and document.get("next_phase") == f"add-node-validator-admission-{document.get('network')}"
    )
    if not clean:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_EVIDENCE_INVALID", "node-add replica-sync evidence is not clean")
    target = document["target"]
    return {
        "clean": True,
        "evidence_path": str(resolved),
        "evidence_sha256": file_sha,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "node_add_replica_sync_release_sha256": release_digest,
        "source_add_identity_evidence_sha256": document["source_add_identity_evidence"]["sha256"],
        "source_add_do_evidence_sha256": document["source_add_do_evidence"]["sha256"],
        "source_prep_transaction_sha256": document["source_prep_transaction"]["sha256"],
        "source_baseline_evidence_sha256": document["source_baseline_evidence"]["sha256"],
        "target_node": target["node"],
        "target_host": target["controller_id"],
        "created_service_uuid": target["created_service_uuid"],
        "chain_id": document["proof"]["chain_id"],
        "genesis_sha256": document["proof"]["genesis_sha256"],
        "bootnode_node": document["proof"]["bootnode_node"],
        "identity_install_previously_performed": True,
        "replica_sync_performed": True,
        "replica_sync_proven": True,
        "service_running_healthy": True,
        "validator_admission_performed": False,
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "generic_topology_diff": True,
        "hardcoded_stage_target": False,
        "next_phase": document["next_phase"],
    }


__all__ = [
    "MotherDeploymentNodeAddReplicaSyncError",
    "build_node_add_replica_sync_release",
    "execute_node_add_replica_sync_release",
    "verify_node_add_replica_sync_evidence",
    "verify_node_add_replica_sync_release",
    "write_node_add_replica_sync_release",
]
