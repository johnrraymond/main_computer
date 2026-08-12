"""Generic pre-admission Mother ``add-node rollback`` release and executor.

This module rolls back a failed add-node attempt before validator admission.  It
consumes failed add-node evidence, deletes only the exact standby Coolify service
created for that attempt, proves that no chain/routing/topology/admission mutation
was performed, and writes clean rollback evidence that can be used as a new
``add-node prep`` baseline.

Post-admission rollback is intentionally not implemented here; once a validator
has been admitted, the documented rollback path is ordinary ``remove-node``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any
import urllib.error
import urllib.request

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import (
    _DEFAULT_MAX_RESPONSE_BYTES,
    _DEFAULT_OPENER,
    resolve_coolify_controller,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_RELEASE_KIND = "main_computer.mother.deployment_node_add_rollback_release.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_node_add_rollback_evidence.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-node-add-rollback-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-node-add-rollback-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-add-rollback")
_FAILED_ADD_EVIDENCE_ROOT = "evidence"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_UUID_RE = re.compile(r"^[A-Za-z0-9_-]{3,96}$")
_MIN_RELEASE_SECONDS = 1
_MAX_RELEASE_SECONDS = 900


class MotherDeploymentNodeAddRollbackError(RuntimeError):
    """Node-add rollback failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentNodeAddRollbackError:
    return MotherDeploymentNodeAddRollbackError(code, message)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_INVALID", f"{label} is invalid")
    return value


def _uuid(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _UUID_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_INVALID", f"{label} is invalid")
    return value


def _sha256(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if _SHA256_RE.fullmatch(text) is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_INVALID", f"{label} is not a SHA-256 digest")
    return text


def _timestamp(value: str | None = None, *, now: datetime | None = None) -> str:
    if value is not None:
        return _parse_utc(value, "created_at").isoformat(timespec="seconds").replace("+00:00", "Z")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    return reference.replace(microsecond=0).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_TIME_INVALID", f"{label} is missing")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any, *, now: datetime | None) -> int:
    observed = _parse_utc(value, "completed_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - observed).total_seconds())
    if age < -60:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_TIME_INVALID", "source evidence timestamp is in the future")
    return max(age, 0)


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _root(paths: PrivateStatePaths, parts: Iterable[str]) -> Path:
    current = paths.root
    for part in parts:
        current = current / part
    return current


def _ensure_directory(path: Path, *, operation: OperationIdentity) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _secure_private_path(path, is_directory=True, operation=operation)
    return path


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_PATH_INVALID", f"{label} is outside the Mother state root") from exc


def _resolve_under(paths: PrivateStatePaths, locator: Any, directory: tuple[str, ...], *, label: str) -> Path:
    if not isinstance(locator, str) or not locator:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_PATH_INVALID", f"{label} locator is missing")
    candidate = (paths.root / Path(locator)).resolve(strict=False)
    allowed = _root(paths, directory).resolve(strict=False)
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_PATH_INVALID", f"{label} is outside its directory") from exc
    return candidate


def _resolve_failed_evidence_path(paths: PrivateStatePaths, path: Path) -> Path:
    resolved = Path(path).resolve(strict=False)
    allowed = (paths.root / _FAILED_ADD_EVIDENCE_ROOT).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_PATH_INVALID", "failed add-node evidence is outside evidence") from exc
    return resolved


def _canonical_file(path: Path) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_PATH_INVALID", f"cannot read {path}") from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_JSON_INVALID", f"{path} is not JSON") from exc
    if not isinstance(document, dict):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_JSON_INVALID", f"{path} is not a JSON object")
    canonical = canonical_json(document)
    return document, canonical, hashlib.sha256(canonical).hexdigest()


def _digest_without(value: Mapping[str, Any], key: str) -> str:
    return hashlib.sha256(canonical_json({item_key: item for item_key, item in value.items() if item_key != key})).hexdigest()


def _contains_sensitive(value: Any) -> bool:
    needles = (
        "BEGIN PRIVATE KEY",
        "BEGIN RSA PRIVATE KEY",
        "api_token",
        "bearer ",
    )
    private_key_re = re.compile(r"^0x[0-9a-fA-F]{64}$")
    def walk(item: Any) -> bool:
        if isinstance(item, str):
            if private_key_re.fullmatch(item.strip()) is not None:
                return True
            lowered = item.lower()
            return any(needle.lower() in lowered for needle in needles)
        if isinstance(item, Mapping):
            return any(walk(key) or walk(val) for key, val in item.items())
        if isinstance(item, list):
            return any(walk(val) for val in item)
        return False
    return walk(value)


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    if hasattr(opener, "open"):
        return opener.open(request, timeout=timeout)
    if callable(opener):
        return opener(request, timeout=timeout)
    raise TypeError("opener must be callable or provide open(request, timeout=...)")


def _http(
    controller: Any,
    method: str,
    endpoint: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    request = urllib.request.Request(
        controller.base_url.rstrip("/") + endpoint,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {controller.api_token}",
            "User-Agent": "main-computer-mother-node-add-rollback/1",
        },
        method=method,
    )
    started = time.monotonic()
    try:
        try:
            response = _open(opener, request, float(timeout))
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_REQUEST_FAILED", "Coolify request failed") from exc
    if len(raw) > max_response_bytes:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_RESPONSE_TOO_LARGE", "Coolify response is too large")
    try:
        payload: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "payload": payload,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


def _records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        records: list[Mapping[str, Any]] = [payload]
        for key in ("data", "resource", "service"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                records.append(nested)
            elif isinstance(nested, list):
                records.extend(item for item in nested if isinstance(item, Mapping))
        return records
    return []


def _expected_service(payload: Any, *, node: str, service_uuid: str) -> Mapping[str, Any]:
    matches = [item for item in _records(payload) if item.get("uuid") == service_uuid]
    if len(matches) != 1:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_SERVICE_MISMATCH", "Coolify did not return exactly one service with the rollback UUID")
    record = matches[0]
    if record.get("name") != node:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_SERVICE_MISMATCH", "rollback UUID does not belong to the target node")
    return record


def _receipt(*, phase: str, method: str, endpoint: str, response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "phase": phase,
        "method": method,
        "endpoint": endpoint,
        "http_status": response.get("status"),
        "response_sha256": response.get("response_sha256"),
        "byte_length": response.get("byte_length"),
        "elapsed_ms": response.get("elapsed_ms"),
    }


def _delete_created_service(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    node: str,
    service_uuid: str,
    timeout: float,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    max_response_bytes: int,
    operation: OperationIdentity,
    opener: Any,
) -> dict[str, Any]:
    del operation
    controller = resolve_coolify_controller(
        private_state,
        network,
        controller_id,
        require_enabled=True,
        require_token=True,
    )
    endpoint = f"/api/v1/services/{service_uuid}"
    receipts: list[dict[str, Any]] = []
    before = _http(controller, "GET", endpoint, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
    receipts.append(_receipt(phase="precondition", method="GET", endpoint=endpoint, response=before))
    if before["status"] == 404:
        return {
            "status": "pass",
            "clean": True,
            "network": network,
            "controller_id": controller_id,
            "node": node,
            "service_uuid": service_uuid,
            "already_absent": True,
            "live_mutation_performed": False,
            "receipts": receipts,
        }
    if not before["ok"]:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_PRECONDITION_FAILED", f"Coolify service lookup failed with HTTP {before['status']}")
    record = _expected_service(before["payload"], node=node, service_uuid=service_uuid)
    observed_status = record.get("status") if isinstance(record.get("status"), str) else None
    deleted = _http(controller, "DELETE", endpoint, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
    receipts.append(_receipt(phase="delete", method="DELETE", endpoint=endpoint, response=deleted))
    if deleted["status"] not in {200, 202, 204, 404}:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_DELETE_FAILED", f"Coolify service deletion failed with HTTP {deleted['status']}")
    deadline = time.monotonic() + max(0.0, float(max_wait_seconds))
    while True:
        after = _http(controller, "GET", endpoint, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        receipts.append(_receipt(phase="verify-absent", method="GET", endpoint=endpoint, response=after))
        if after["status"] == 404:
            break
        if not after["ok"]:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_VERIFY_FAILED", f"Coolify absence verification failed with HTTP {after['status']}")
        if time.monotonic() >= deadline:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_VERIFY_TIMEOUT", "service remained visible after rollback deletion")
        if poll_interval_seconds:
            time.sleep(max(0.0, float(poll_interval_seconds)))
    return {
        "status": "pass",
        "clean": True,
        "network": network,
        "controller_id": controller_id,
        "node": node,
        "service_uuid": service_uuid,
        "already_absent": False,
        "observed_status_before_removal": observed_status,
        "live_mutation_performed": True,
        "mutation_count": 1,
        "receipts": receipts,
    }


def _write_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    digest = _sha256(release.get("node_add_rollback_release_sha256"), "add-node rollback release sha256")
    if release.get("kind") != _RELEASE_KIND or _digest_without(release, "node_add_rollback_release_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_RELEASE_INVALID", "release digest is invalid")
    if _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_SENSITIVE", "add-node rollback release contains sensitive material")
    directory = _ensure_directory(_root(paths, _RELEASE_DIRECTORY), operation=operation)
    target = release["target"]
    name = f"{release['created_at'].replace('-', '').replace(':', '')}-{release['network']}-{target['node']}-{digest[:16]}.json"
    path = directory / name
    atomic_files.durable_create(path, canonical_json(dict(release)), operation=operation)
    _secure_private_path(path, is_directory=False, operation=operation)
    return path, digest


def write_node_add_rollback_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    return _write_release(paths, release, operation=operation)


def _write_evidence(paths: PrivateStatePaths, evidence: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    if evidence.get("kind") != _EVIDENCE_KIND:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_EVIDENCE_INVALID", "evidence kind is invalid")
    if _contains_sensitive(evidence):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_SENSITIVE", "add-node rollback evidence contains sensitive material")
    payload = canonical_json(dict(evidence))
    digest = hashlib.sha256(payload).hexdigest()
    directory = _ensure_directory(_root(paths, _EVIDENCE_DIRECTORY), operation=operation)
    target = evidence["target"]
    path = directory / f"{evidence['completed_at'].replace('-', '').replace(':', '')}-{target['node']}-{digest[:16]}.json"
    atomic_files.durable_create(path, payload, operation=operation)
    _secure_private_path(path, is_directory=False, operation=operation)
    return path, digest


def _claim_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> Path:
    digest = _sha256(release.get("node_add_rollback_release_sha256"), "add-node rollback release sha256")
    directory = _ensure_directory(_root(paths, _CLAIM_DIRECTORY), operation=operation)
    path = directory / f"{digest}.json"
    claim = {
        "kind": "main_computer.mother.deployment_node_add_rollback_execution_claim.v1",
        "claimed_at": _timestamp(),
        "release_sha256": digest,
        "operation_id": operation.operation_id,
    }
    atomic_files.durable_create(path, canonical_json(claim), operation=operation)
    _secure_private_path(path, is_directory=False, operation=operation)
    return path


def _load_failed_add_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    failed_evidence_path: Path,
    *,
    expected_sha256: str,
    max_age_seconds: int,
    now: datetime | None,
) -> tuple[dict[str, Any], Path, str, str]:
    resolved = _resolve_failed_evidence_path(paths, failed_evidence_path)
    document, raw, digest = _canonical_file(resolved)
    if digest != _sha256(expected_sha256, "acknowledged failed add-node evidence sha256"):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_SOURCE_MISMATCH", "failed add-node evidence SHA does not match")
    if document.get("mother_binding") != _binding(private_state):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_SOURCE_INVALID", "failed add-node evidence Mother binding does not match")
    kind = document.get("kind")
    if not isinstance(kind, str) or not kind.startswith("main_computer.mother.deployment_node_add_") or not kind.endswith("_evidence.v1"):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_SOURCE_INVALID", "source evidence is not add-node evidence")
    if document.get("status") != "failed":
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_SOURCE_INVALID", "rollback requires failed pre-admission add-node evidence")
    if _age_seconds(document.get("completed_at"), now=now) > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_SOURCE_STALE", "failed add-node evidence is outside the freshness window")
    target = document.get("target")
    if not isinstance(target, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_SOURCE_INVALID", "source target is missing")
    controller_id = _identifier(target.get("controller_id"), "target controller")
    node = _identifier(target.get("node"), "target node")
    service_uuid = _uuid(
        target.get("created_service_uuid") or target.get("service_uuid"),
        "created service UUID",
    )
    pre_admission_safe = (
        document.get("validator_admission_performed") is False
        and document.get("validator_vote_performed") is False
        and document.get("validator_activation_performed") is False
        and document.get("routing_or_topology_published") is False
        and document.get("public_endpoint_created") is False
        and document.get("chain_mutation_count", 0) in (0, None)
        and document.get("validator_mutation_count", 0) in (0, None)
    )
    summary = document.get("summary")
    if isinstance(summary, Mapping):
        pre_admission_safe = pre_admission_safe and summary.get("validator_admission_performed") is False and summary.get("routing_or_topology_published") is False and summary.get("public_endpoint_created") is False
    if not pre_admission_safe:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_POST_ADMISSION_REFUSED", "source evidence is not safely pre-admission; use remove-node after admission")
    if not isinstance(document.get("current_topology"), Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_SOURCE_INVALID", "source current topology is missing")
    loaded = dict(document)
    loaded["target"] = {**dict(target), "controller_id": controller_id, "node": node, "created_service_uuid": service_uuid}
    return loaded, resolved, digest, hashlib.sha256(raw).hexdigest()


def build_node_add_rollback_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    failed_evidence_path: Path,
    *,
    acknowledged_failed_evidence_sha256: str,
    max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not _MIN_RELEASE_SECONDS <= int(expires_in_seconds) <= _MAX_RELEASE_SECONDS:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_RELEASE_INVALID", "release expiry must be between 1 and 900 seconds")
    source, resolved, source_sha, byte_sha = _load_failed_add_evidence(
        paths,
        private_state,
        Path(failed_evidence_path),
        expected_sha256=acknowledged_failed_evidence_sha256,
        max_age_seconds=max_age_seconds,
        now=now,
    )
    network = _identifier(source.get("network"), "network")
    target = dict(source["target"])
    node = _identifier(target["node"], "target node")
    controller_id = _identifier(target["controller_id"], "target controller")
    service_uuid = _uuid(target["created_service_uuid"], "created service UUID")
    created = _timestamp(created_at, now=now)
    expires = (_parse_utc(created, "created_at") + timedelta(seconds=int(expires_in_seconds))).isoformat(timespec="seconds").replace("+00:00", "Z")
    current_topology = dict(source["current_topology"])
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created,
        "expires_at": expires,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": source.get("mode"),
        "source_failed_add_node_evidence": {
            "locator": _relative(paths, resolved, label="failed add-node evidence"),
            "sha256": source_sha,
            "byte_sha256": byte_sha,
            "kind": source.get("kind"),
            "status": source.get("status"),
            "failure": source.get("failure"),
        },
        "target": {
            "node": node,
            "controller_id": controller_id,
            "created_service_uuid": service_uuid,
            "validator_address": target.get("validator_address"),
        },
        "current_topology": current_topology,
        "rollback_baseline_topology": current_topology,
        "authority": {
            "authorization_source": "explicit-operator-release",
            "requested_use_limit": 1,
            "live_execution_authorized": True,
            "standby_service_deletion_authorized": True,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "private_state_mutation_authorized": False,
        },
        "policy": {
            "compiler": "mother-native-add-node-rollback-v1",
            "allowed_http_methods": ["GET", "DELETE"],
            "coolify_control_plane_only": True,
            "requested_use_limit": 1,
            "failed_pre_admission_add_node_only": True,
            "delete_exact_created_service_only": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "chain_mutation_performed": False,
            "validator_vote_performed": False,
            "validator_admission_performed": False,
            "routing_or_topology_published": False,
        },
        "summary": {
            "clean": True,
            "target_node": node,
            "target_host": controller_id,
            "created_service_uuid": service_uuid,
            "failed_source_kind": source.get("kind"),
            "rollback_authorized": True,
            "retry_after_rollback_authorized": True,
            "generic_topology_diff": True,
            "hardcoded_stage_target": False,
            "next_phase": f"add-node-rollback-{network}",
        },
    }
    release["node_add_rollback_release_sha256"] = _digest_without(release, "node_add_rollback_release_sha256")
    if _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_SENSITIVE", "add-node rollback release contains sensitive material")
    return release


def verify_node_add_rollback_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    max_age_seconds: int = 900,
    failed_evidence_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = Path(release_path).resolve(strict=False)
    allowed = _root(paths, _RELEASE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_PATH_INVALID", "add-node rollback release is outside its directory") from exc
    release, _raw, file_sha = _canonical_file(resolved)
    digest = _digest_without(release, "node_add_rollback_release_sha256")
    if release.get("kind") != _RELEASE_KIND or release.get("node_add_rollback_release_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_RELEASE_INVALID", "release digest is invalid")
    if release.get("mother_binding") != _binding(private_state) or _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_RELEASE_INVALID", "release binding is invalid")
    if _age_seconds(release.get("created_at"), now=now) > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_RELEASE_STALE", "release is outside the freshness window")
    if _parse_utc(release.get("expires_at"), "expires_at") < (now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_RELEASE_EXPIRED", "release has expired")
    source = release.get("source_failed_add_node_evidence")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_RELEASE_INVALID", "source failed evidence binding is missing")
    source_path = _resolve_under(paths, source.get("locator"), (_FAILED_ADD_EVIDENCE_ROOT,), label="failed add-node evidence")
    _load_failed_add_evidence(
        paths,
        private_state,
        source_path,
        expected_sha256=str(source.get("sha256")),
        max_age_seconds=failed_evidence_max_age_seconds,
        now=now,
    )
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{digest}.json"
    return {
        "clean": True,
        "network": release["network"],
        "target_node": release["target"]["node"],
        "target_host": release["target"]["controller_id"],
        "created_service_uuid": release["target"]["created_service_uuid"],
        "node_add_rollback_release_sha256": digest,
        "release_path": str(resolved),
        "release_already_claimed": claim_path.exists(),
        "failed_source_kind": source.get("kind"),
        "rollback_authorized": True,
        "validator_admission_authorized": False,
        "validator_vote_authorized": False,
        "routing_or_topology_publication_authorized": False,
        "next_phase": release["summary"]["next_phase"],
    }


def execute_node_add_rollback_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 900,
    failed_evidence_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_wait_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    verified = verify_node_add_rollback_release(
        paths,
        private_state,
        Path(release_path),
        max_age_seconds=max_age_seconds,
        failed_evidence_max_age_seconds=failed_evidence_max_age_seconds,
        now=now,
    )
    acknowledged = _sha256(acknowledged_release_sha256, "acknowledged release sha256")
    if verified["node_add_rollback_release_sha256"] != acknowledged:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_ACK_MISMATCH", "acknowledged release SHA does not match")
    if verified.get("release_already_claimed") is True:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_RELEASE_ALREADY_CLAIMED", "add-node rollback release was already claimed")
    release, _raw, digest = _canonical_file(Path(release_path))
    claim_path = _claim_release(paths, release, operation=operation)
    target = release["target"]
    started_at = _timestamp(now=now)
    failure: dict[str, str] | None = None
    service_removal: dict[str, Any] | None = None
    try:
        service_removal = _delete_created_service(
            private_state,
            network=release["network"],
            controller_id=target["controller_id"],
            node=target["node"],
            service_uuid=target["created_service_uuid"],
            timeout=timeout,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            max_response_bytes=max_response_bytes,
            operation=operation,
            opener=opener,
        )
    except MotherDeploymentNodeAddRollbackError as exc:
        failure = {"code": exc.code, "message": str(exc)[:512]}
    except Exception as exc:  # pragma: no cover
        failure = {"code": "MOTHER_DEPLOY_NODE_ADD_ROLLBACK_UNEXPECTED_FAILURE", "message": str(exc)[:512]}
    completed_at = _timestamp(now=now)
    service_absent = bool(service_removal and service_removal.get("status") == "pass")
    service_deleted = bool(service_removal and service_removal.get("already_absent") is not True)
    service_already_absent = bool(service_removal and service_removal.get("already_absent") is True)
    complete = failure is None and service_absent
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "pass" if complete else "failed",
        "failure": failure,
        "mother_binding": release["mother_binding"],
        "network": release["network"],
        "mode": release.get("mode"),
        "target": dict(target),
        "current_topology": dict(release["rollback_baseline_topology"]),
        "rollback_baseline_topology": dict(release["rollback_baseline_topology"]),
        "release": {"locator": _relative(paths, Path(release_path), label="add-node rollback release"), "sha256": digest},
        "execution_claim": {"locator": _relative(paths, claim_path, label="add-node rollback execution claim")},
        "source_failed_add_node_evidence": dict(release["source_failed_add_node_evidence"]),
        "service_removal": service_removal,
        "authority": {
            "release_consumed": True,
            "standby_service_deletion_authorized": True,
            "standby_service_absence_proven": service_absent,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "private_state_mutation_authorized": False,
        },
        "policy": {
            "allowed_http_methods": ["GET", "DELETE"],
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "delete_exact_created_service_only": True,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "chain_mutation_performed": False,
            "validator_vote_performed": False,
            "validator_admission_performed": False,
            "routing_or_topology_published": False,
            "public_http_endpoint_created": False,
        },
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_vote_performed": False,
        "validator_admission_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "service_deletion_performed": service_deleted,
        "service_already_absent": service_already_absent,
        "live_mutation_performed": service_deleted,
        "next_phase": f"add-node-rollback-complete-{release['network']}" if complete else "manual-review-required",
    }
    evidence["summary"] = {
        "clean": complete,
        "complete": complete,
        "target_node": target["node"],
        "target_host": target["controller_id"],
        "created_service_uuid": target["created_service_uuid"],
        "standby_service_absent": service_absent,
        "service_deletion_performed": service_deleted,
        "service_already_absent": service_already_absent,
        "retry_after_rollback_authorized": complete,
        "rollback_baseline_usable_by_add_node_prep": complete,
        "generic_topology_diff": True,
        "hardcoded_stage_target": False,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_vote_performed": False,
        "validator_admission_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "live_mutation_performed": service_deleted,
        "next_phase": evidence["next_phase"],
    }
    path, evidence_sha = _write_evidence(paths, evidence, operation=operation)
    return {**evidence, "evidence": {"path": str(path), "sha256": evidence_sha}}


def verify_node_add_rollback_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
    release_max_age_seconds: int = 86400,
    failed_evidence_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = Path(evidence_path).resolve(strict=False)
    allowed = _root(paths, _EVIDENCE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_PATH_INVALID", "add-node rollback evidence is outside its directory") from exc
    document, _raw, file_sha = _canonical_file(resolved)
    if document.get("kind") != _EVIDENCE_KIND or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_EVIDENCE_INVALID", "rollback evidence is invalid")
    if _age_seconds(document.get("completed_at"), now=now) > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_EVIDENCE_STALE", "rollback evidence is outside the freshness window")
    release_binding = document.get("release")
    if not isinstance(release_binding, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_EVIDENCE_INVALID", "release binding is missing")
    release_path = _resolve_under(paths, release_binding.get("locator"), _RELEASE_DIRECTORY, label="add-node rollback release")
    release = verify_node_add_rollback_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=release_max_age_seconds,
        failed_evidence_max_age_seconds=failed_evidence_max_age_seconds,
        now=now,
    )
    clean = (
        document.get("status") == "pass"
        and document.get("failure") is None
        and isinstance(document.get("summary"), Mapping)
        and document["summary"].get("clean") is True
        and document["summary"].get("rollback_baseline_usable_by_add_node_prep") is True
        and document.get("chain_mutation_count") == 0
        and document.get("validator_mutation_count") == 0
        and document.get("validator_vote_performed") is False
        and document.get("validator_admission_performed") is False
        and document.get("routing_or_topology_published") is False
        and document.get("public_endpoint_created") is False
        and document.get("service_removal", {}).get("status") == "pass"
    )
    if not clean:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_ROLLBACK_EVIDENCE_NOT_CLEAN", "rollback evidence is not clean")
    return {
        "clean": True,
        "network": document["network"],
        "target_node": document["target"]["node"],
        "target_host": document["target"]["controller_id"],
        "created_service_uuid": document["target"]["created_service_uuid"],
        "node_add_rollback_evidence_sha256": file_sha,
        "release_sha256": release["node_add_rollback_release_sha256"],
        "standby_service_absent": True,
        "service_deletion_performed": document.get("service_deletion_performed") is True,
        "service_already_absent": document.get("service_already_absent") is True,
        "rollback_baseline_usable_by_add_node_prep": True,
        "validator_admission_performed": False,
        "validator_vote_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": document["next_phase"],
    }
