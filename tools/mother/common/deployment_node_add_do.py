
"""Guarded generic Mother ``add-node do`` release and standby-service executor.

This module intentionally implements the first live generic add-node mutation:
creating exactly one standby super-node service for the explicit node recorded in
an ``add-node prep`` transaction.  It does not install identity, synchronize the
replica, admit a validator, publish routing/topology, or finalize the add.  Those
later phases must consume this evidence through generic add-node phases; no
named-node or golden-path stage is special-cased here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path, PureWindowsPath
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
    get_coolify_json,
    resolve_coolify_controller,
)
from .deployment_node_add_prep import verify_node_add_prep_transaction
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_RELEASE_KIND = "main_computer.mother.deployment_node_add_do_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_node_add_do_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_node_add_do_evidence.v1"
_PREP_DIRECTORY = ("actions", "deployment-node-add-prep-transactions")
_RELEASE_DIRECTORY = ("actions", "deployment-node-add-do-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-node-add-do-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-add-do")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SENSITIVE_MARKERS = (
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "api_token",
    "bearer ",
    "password",
    "private_key",
    "secret",
)


class MotherDeploymentNodeAddDoError(RuntimeError):
    """Node-add do/release failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentNodeAddDoError:
    return MotherDeploymentNodeAddDoError(code, message)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or not _IDENTIFIER_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_INVALID", f"{label} is not a valid identifier")
    return value


def _sha256(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if not _SHA256_RE.fullmatch(text):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_INVALID", f"{label} is not a SHA-256 digest")
    return text


def _timestamp(value: str | None = None, *, now: datetime | None = None) -> str:
    if value is not None:
        parsed = _parse_utc(value, "timestamp")
        return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    return reference.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_TIME_INVALID", f"{label} is missing")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any, *, now: datetime | None = None) -> int:
    observed = _parse_utc(value, "created_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - observed).total_seconds())
    if age < -60:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_TIME_INVALID", "timestamp is in the future")
    return max(age, 0)


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _digest_without(document: Mapping[str, Any], *keys: str) -> str:
    payload = dict(document)
    for key in keys:
        payload.pop(key, None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, Mapping):
        sensitive_keys = {"api_token", "access_token", "bearer_token", "password", "private_key", "secret"}
        return any(str(key).lower() in sensitive_keys or _contains_sensitive(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker.lower() in lowered for marker in _SENSITIVE_MARKERS)
    return False


def _root(paths: PrivateStatePaths, parts: Iterable[str]) -> Path:
    current = paths.root
    for part in parts:
        current = current / part
    return current


def _ensure_directory(paths: PrivateStatePaths, parts: Iterable[str], *, operation: OperationIdentity) -> Path:
    current = paths.root
    for part in parts:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _relative(paths: PrivateStatePaths, candidate: Path, *, label: str) -> str:
    root = paths.root.resolve(strict=False)
    resolved = Path(candidate).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_PATH_INVALID", f"{label} is outside the Mother root") from exc


def _resolve_under(paths: PrivateStatePaths, locator: Any, parts: Iterable[str], *, label: str) -> Path:
    if not isinstance(locator, str) or not locator or "\\" in locator:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_PATH_INVALID", f"{label} locator must be a relative POSIX path")
    pure = PureWindowsPath(locator)
    candidate = Path(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_PATH_INVALID", f"{label} locator is unsafe")
    resolved = (paths.root / candidate).resolve(strict=False)
    allowed = _root(paths, parts).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_PATH_INVALID", f"{label} is outside its canonical directory") from exc
    return resolved


def _canonical_file(path: Path) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_FILE_UNREADABLE", f"cannot read {path}") from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_FILE_INVALID", "canonical JSON file is malformed") from exc
    if not isinstance(document, dict):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_FILE_INVALID", "canonical JSON file must contain an object")
    if canonical_json(document) != raw:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_FILE_INVALID", "file is not canonical Mother JSON")
    return document, raw, hashlib.sha256(raw).hexdigest()


def _load_prep_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    expected_sha256: str | None = None,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> tuple[dict[str, Any], Path, str, str]:
    resolved = Path(transaction_path).resolve(strict=False)
    allowed = _root(paths, _PREP_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_PATH_INVALID", "add-node prep transaction is outside its directory") from exc
    verified = verify_node_add_prep_transaction(
        paths,
        private_state,
        resolved,
        max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    document, raw, byte_sha = _canonical_file(resolved)
    digest = str(verified["node_add_prep_transaction_sha256"])
    if expected_sha256 is not None and _sha256(expected_sha256, "acknowledged prep transaction sha256") != digest:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_ACK_MISMATCH", "acknowledged prep transaction SHA does not match")
    if document.get("node_add_prep_transaction_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_TRANSACTION_INVALID", "prep transaction digest mismatch")
    return document, resolved, digest, byte_sha


def _private_document(private_state: PrivateStateReadResult) -> Mapping[str, Any]:
    try:
        document = yaml.safe_load(private_state.document_bytes)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_PRIVATE_STATE_INVALID", "Mother private state is malformed YAML") from exc
    if not isinstance(document, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_PRIVATE_STATE_INVALID", "Mother private state is not a mapping")
    return document


def _network_doc(private_state: PrivateStateReadResult, network: str) -> Mapping[str, Any]:
    document = _private_document(private_state)
    networks = document.get("networks")
    if not isinstance(networks, Mapping) or not isinstance(networks.get(network), Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_PRIVATE_STATE_INVALID", "network private state is missing")
    return networks[network]


def _target_deployment(private_state: PrivateStateReadResult, network: str, node: str) -> Mapping[str, Any]:
    network_doc = _network_doc(private_state, network)
    deployment = network_doc.get("deployment")
    targets = deployment.get("targets") if isinstance(deployment, Mapping) else None
    target = targets.get(node) if isinstance(targets, Mapping) else None
    if not isinstance(target, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_TARGET_INVALID", "target deployment metadata is missing from Mother private state")
    return target


def _controller_metadata(private_state: PrivateStateReadResult, network: str, controller_id: str) -> Mapping[str, Any]:
    network_doc = _network_doc(private_state, network)
    coolify = network_doc.get("coolify")
    controllers = coolify.get("controllers") if isinstance(coolify, Mapping) else None
    controller = controllers.get(controller_id) if isinstance(controllers, Mapping) else None
    if not isinstance(controller, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_TARGET_INVALID", "target controller metadata is missing from Mother private state")
    return controller


def _safe_response(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": int(receipt["status"]),
        "ok": bool(receipt["ok"]),
        "content_type": str(receipt.get("content_type", "")),
        "response_sha256": str(receipt["response_sha256"]),
        "byte_length": int(receipt["byte_length"]),
        "elapsed_ms": int(receipt["elapsed_ms"]),
    }


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    if hasattr(opener, "open"):
        return opener.open(request, timeout=timeout)
    if callable(opener):
        return opener(request, timeout=timeout)
    raise TypeError("opener must be callable or provide open(request, timeout=...)")


def _http_post(
    controller: Any,
    endpoint: str,
    body: Mapping[str, Any],
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    payload = canonical_json(dict(body))
    request = urllib.request.Request(
        controller.base_url + endpoint,
        data=payload,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {controller.api_token}",
            "Content-Type": "application/json",
            "User-Agent": "main-computer-mother-add-node-do/1",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        try:
            response = _open(opener, request, float(timeout))
            status = int(getattr(response, "status", response.getcode()))
            content_type = str(response.headers.get("Content-Type", ""))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            content_type = str(exc.headers.get("Content-Type", "")) if exc.headers else ""
            raw = exc.read(max_response_bytes + 1)
    except urllib.error.URLError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_REQUEST_FAILED", f"Coolify POST failed: {exc.reason}") from exc
    except OSError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_REQUEST_FAILED", "Coolify POST failed") from exc
    if len(raw) > max_response_bytes:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_RESPONSE_TOO_LARGE", f"Coolify response exceeded {max_response_bytes} bytes")
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed: Any = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        parsed = text.strip()
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "content_type": content_type,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
        "payload": parsed,
    }


def _path_value(payload: Any, dotted: str) -> Any:
    current = payload
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _uuid_from_payload(payload: Any, paths: Iterable[str]) -> str | None:
    for path in paths:
        value = _path_value(payload, path)
        if isinstance(value, str) and value.strip() and _IDENTIFIER_RE.fullmatch(value.strip()):
            return value.strip()
    return None


def _items(payload: Any, keys: tuple[str, ...]) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in (*keys, "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        if any(key in payload for key in ("uuid", "id", "name")):
            return [payload]
    return []


def _find_named(items: Iterable[Mapping[str, Any]], name: str) -> list[Mapping[str, Any]]:
    return [item for item in items if item.get("name") == name]


def _extract_uuid(item: Mapping[str, Any], label: str) -> str:
    value = item.get("uuid", item.get("id"))
    if not isinstance(value, str) or not value or not _IDENTIFIER_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_RESPONSE_INVALID", f"{label} did not contain a usable UUID")
    return value


def _standby_compose(node: str) -> str:
    return "\n".join(
        [
            f"name: {node}",
            "",
            "services:",
            f"  {node}:",
            "    image: alpine:3.20",
            "    restart: \"no\"",
            "    command:",
            "      - sh",
            "      - -lc",
            "      - exec tail -f /dev/null",
            "    labels:",
            "      main_computer.mother.stage: standby",
            f"      main_computer.mother.node: {node}",
            "",
        ]
    )


def _service_body(*, node: str, project_uuid: str, server_uuid: str, environment_name: str, environment_uuid: str) -> dict[str, Any]:
    compose = _standby_compose(node)
    return {
        "server_uuid": server_uuid,
        "project_uuid": project_uuid,
        "environment_name": environment_name,
        "environment_uuid": environment_uuid,
        "name": node,
        "description": (
            f"Main Computer Mother generic add-node standby shell for {node}; "
            "identity, replica sync, validator admission, routing, and topology publication are not installed by this phase"
        ),
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "instant_deploy": False,
    }


def build_node_add_do_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_prep_transaction_sha256: str,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if expires_in_seconds <= 0 or expires_in_seconds > 900:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_RELEASE_INVALID", "release expiry must be between 1 and 900 seconds")
    transaction, resolved, tx_sha, byte_sha = _load_prep_transaction(
        paths,
        private_state,
        Path(transaction_path),
        expected_sha256=acknowledged_prep_transaction_sha256,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if transaction.get("summary", {}).get("next_phase") != f"add-node-do-{transaction.get('network')}":
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_TRANSACTION_INVALID", "prep transaction is not at add-node-do phase")
    created = _timestamp(created_at, now=now)
    expires = (_parse_utc(created, "created_at") + timedelta(seconds=int(expires_in_seconds))).isoformat(timespec="seconds").replace("+00:00", "Z")
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created,
        "expires_at": expires,
        "mother_binding": _binding(private_state),
        "network": transaction["network"],
        "mode": transaction["mode"],
        "source_transaction": {
            "locator": _relative(paths, resolved, label="add-node prep transaction"),
            "sha256": tx_sha,
            "byte_sha256": byte_sha,
        },
        "source_baseline_evidence": dict(transaction["source_baseline_evidence"]),
        "target": dict(transaction["target"]),
        "current_topology": dict(transaction["current_topology"]),
        "post_add_topology": dict(transaction["post_add_topology"]),
        "topology_diff": dict(transaction["topology_diff"]),
        "ordered_add_plan": list(transaction["ordered_add_plan"]),
        "authority": {
            "authorization_source": "explicit-operator-release",
            "requested_use_limit": 1,
            "live_execution_authorized": True,
            "service_creation_authorized": True,
            "identity_install_authorized": False,
            "replica_sync_authorized": False,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
        },
        "policy": {
            "compiler": "mother-native-add-node-do-v1",
            "requested_use_limit": 1,
            "service_creation_authorized": True,
            "identity_install_authorized": False,
            "replica_sync_authorized": False,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "public_http_endpoint_created": False,
            "manual_ssh_required": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "summary": {
            "clean": True,
            "executor_implemented": True,
            "generic_topology_diff": True,
            "hardcoded_stage_target": False,
            "target_node": transaction["target"]["node"],
            "target_host": transaction["target"]["controller_id"],
            "current_validator_count": transaction["current_topology"]["validator_count"],
            "post_add_validator_count": transaction["post_add_topology"]["validator_count"],
            "service_creation_authorized": True,
            "validator_admission_authorized": False,
            "next_phase": f"add-node-do-{transaction['network']}",
        },
    }
    release["node_add_do_release_sha256"] = _digest_without(release, "node_add_do_release_sha256")
    if _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_SENSITIVE", "node-add do release contains sensitive material")
    return release


def write_node_add_do_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(document, "node_add_do_release_sha256")
    if document.get("kind") != _RELEASE_KIND or document.get("node_add_do_release_sha256") != digest or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_RELEASE_INVALID", "node-add do release is malformed or sensitive")
    payload = canonical_json(document)
    root = _ensure_directory(paths, _RELEASE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "nodeadddo"
    network = str(document.get("network") or "network")
    target = str(document.get("target", {}).get("node") or "node")
    destination = root / f"{stamp}-{network}-{target}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_RELEASE_CONFLICT", "release destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_node_add_do_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    max_age_seconds: int = 900,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = Path(release_path).resolve(strict=False)
    allowed = _root(paths, _RELEASE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_PATH_INVALID", "node-add do release is outside its directory") from exc
    document, _raw, _file_sha = _canonical_file(resolved)
    digest = _digest_without(document, "node_add_do_release_sha256")
    if document.get("kind") != _RELEASE_KIND or document.get("node_add_do_release_sha256") != digest or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_RELEASE_INVALID", "node-add do release is invalid")
    age = _age_seconds(document.get("created_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_RELEASE_STALE", "node-add do release is outside the freshness window")
    if _parse_utc(document.get("expires_at"), "expires_at") < (now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_RELEASE_EXPIRED", "node-add do release has expired")
    source = document.get("source_transaction")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_RELEASE_INVALID", "source transaction binding is missing")
    tx_path = _resolve_under(paths, source.get("locator"), _PREP_DIRECTORY, label="add-node prep transaction")
    transaction, _tx_resolved, tx_sha, _byte_sha = _load_prep_transaction(
        paths,
        private_state,
        tx_path,
        expected_sha256=source.get("sha256"),
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{digest}.json"
    return {
        "clean": True,
        "release_path": str(resolved),
        "node_add_do_release_sha256": digest,
        "release_already_claimed": claim_path.exists(),
        "age_seconds": age,
        "expires_at": document["expires_at"],
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "source_prep_transaction_sha256": tx_sha,
        "source_baseline_evidence_sha256": document["source_baseline_evidence"]["sha256"],
        "target_node": document["target"]["node"],
        "target_host": document["target"]["controller_id"],
        "target_validator_address": document["target"]["validator_address"],
        "current_nodes": list(document["current_topology"]["nodes"]),
        "post_add_nodes": list(document["post_add_topology"]["nodes"]),
        "current_validator_set": list(document["current_topology"]["validator_set"]),
        "post_add_validator_set": list(document["post_add_topology"]["validator_set"]),
        "generic_topology_diff": True,
        "hardcoded_stage_target": False,
        "service_creation_authorized": True,
        "validator_admission_authorized": False,
        "next_phase": document["summary"]["next_phase"],
    }


def _claim_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> Path:
    digest = _sha256(release.get("node_add_do_release_sha256"), "release sha256")
    root = _ensure_directory(paths, _CLAIM_DIRECTORY, operation=operation)
    destination = root / f"{digest}.json"
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "created_at": _timestamp(),
        "release": {
            "sha256": digest,
        },
        "requested_use_number": 1,
    }
    payload = canonical_json(claim)
    try:
        atomic_files.durable_create(destination, payload, operation=operation)
    except FileExistsError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_RELEASE_ALREADY_CLAIMED", "node-add do release was already claimed") from exc
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination


def _write_evidence(paths: PrivateStatePaths, evidence: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(evidence)
    digest = _digest_without(document, "evidence")
    payload = canonical_json(document)
    root = _ensure_directory(paths, _EVIDENCE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "nodeadddo"
    target = str(document.get("target", {}).get("node") or "node")
    destination = root / f"{stamp}-{target}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_EVIDENCE_CONFLICT", "evidence destination contains different bytes")
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
        _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, hashlib.sha256(payload).hexdigest()


def execute_node_add_do_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 900,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    acknowledged = _sha256(acknowledged_release_sha256, "acknowledged release sha256")
    verified = verify_node_add_do_release(
        paths,
        private_state,
        Path(release_path),
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if verified["node_add_do_release_sha256"] != acknowledged:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_ACK_MISMATCH", "acknowledged release SHA does not match")
    if verified.get("release_already_claimed") is True:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_RELEASE_ALREADY_CLAIMED", "node-add do release was already claimed")

    release, _raw, _file_sha = _canonical_file(Path(release_path))
    claim_path = _claim_release(paths, release, operation=operation)

    network = _identifier(release["network"], "network")
    target = release["target"]
    node = _identifier(target["node"], "target node")
    controller_id = _identifier(target["controller_id"], "target controller")
    controller = resolve_coolify_controller(private_state, network, controller_id)
    target_deployment = _target_deployment(private_state, network, node)
    controller_meta = _controller_metadata(private_state, network, controller_id)
    desired_environment = _identifier(target_deployment.get("desired_environment_name"), "target desired environment")
    desired_service = _identifier(target_deployment.get("desired_service_name"), "target desired service")
    if desired_service != node:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_TARGET_INVALID", "target desired service name must equal the node name")
    project_uuid = _identifier(controller_meta.get("project_uuid"), "target controller project UUID")
    server_uuid = _identifier(controller_meta.get("server_uuid"), "target controller server UUID")

    started_at = _timestamp(now=now)
    mutation_receipts: list[dict[str, Any]] = []
    live_mutation_count = 0
    failure: dict[str, str] | None = None
    status = "failed"
    created_service_uuid: str | None = None
    environment_uuid: str | None = None
    environment_status = "unknown"
    service_creation_performed = False
    service_creation_proven = False
    target_observation: dict[str, Any] | None = None
    try:
        env_endpoint = f"/api/v1/projects/{urllib.parse.quote(project_uuid, safe='')}/environments"
        env_get = get_coolify_json(
            controller,
            env_endpoint,
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        env_matches = _find_named(_items(env_get.payload, ("environments",)), desired_environment)
        if len(env_matches) > 1:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_ENVIRONMENT_AMBIGUOUS", "target environment name matched multiple Coolify environments")
        if len(env_matches) == 1:
            environment_uuid = _extract_uuid(env_matches[0], "existing environment")
            environment_status = "existing"
            mutation_receipts.append(
                {
                    "ordinal": 1,
                    "phase": "prospective-host-readiness",
                    "mutation_id": f"{node}.observe-existing-environment",
                    "node": node,
                    "controller_id": controller_id,
                    "method": "GET",
                    "endpoint": env_endpoint,
                    "live_write_acknowledged": False,
                    "response": {
                        "status": env_get.status,
                        "ok": 200 <= env_get.status < 300,
                        "content_type": env_get.content_type,
                        "response_sha256": env_get.response_sha256,
                        "byte_length": env_get.byte_length,
                        "elapsed_ms": env_get.elapsed_ms,
                    },
                    "status": "succeeded",
                    "bound_uuid": environment_uuid,
                }
            )
        else:
            env_body = {"name": desired_environment}
            env_post = _http_post(
                controller,
                env_endpoint,
                env_body,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            if not env_post["ok"]:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_ENVIRONMENT_CREATE_FAILED", "Coolify environment creation failed")
            environment_uuid = _uuid_from_payload(env_post["payload"], ("uuid", "environment.uuid", "data.uuid"))
            if environment_uuid is None:
                # Fall back to re-listing by exact name; some Coolify versions return an ack without the UUID.
                env_after = get_coolify_json(
                    controller,
                    env_endpoint,
                    authenticated=True,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                matches = _find_named(_items(env_after.payload, ("environments",)), desired_environment)
                if len(matches) != 1:
                    raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_ENVIRONMENT_CREATE_UNBOUND", "created environment UUID could not be bound")
                environment_uuid = _extract_uuid(matches[0], "created environment")
            environment_status = "created"
            live_mutation_count += 1
            mutation_receipts.append(
                {
                    "ordinal": 1,
                    "phase": "prospective-host-readiness",
                    "mutation_id": f"{node}.create-environment",
                    "node": node,
                    "controller_id": controller_id,
                    "method": "POST",
                    "endpoint": env_endpoint,
                    "body_sha256": hashlib.sha256(canonical_json(env_body)).hexdigest(),
                    "live_write_acknowledged": True,
                    "response": _safe_response(env_post),
                    "status": "succeeded",
                    "bound_uuid": environment_uuid,
                }
            )

        # Fail closed if the desired service already exists before this add-node do.
        existing_services: list[Mapping[str, Any]] = []
        for endpoint, keys in (("/api/v1/services", ("services",)), ("/api/v1/resources", ("resources",))):
            observed = get_coolify_json(
                controller,
                endpoint,
                authenticated=True,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            existing_services.extend(_find_named(_items(observed.payload, keys), node))
        unique_existing = {str(item.get("uuid", item.get("id"))): item for item in existing_services if isinstance(item.get("uuid", item.get("id")), str)}
        if unique_existing:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_TARGET_EXISTS", "target service already exists before add-node do")

        service_body = _service_body(
            node=node,
            project_uuid=project_uuid,
            server_uuid=server_uuid,
            environment_name=desired_environment,
            environment_uuid=environment_uuid,
        )
        service_post = _http_post(
            controller,
            "/api/v1/services",
            service_body,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        if not service_post["ok"]:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_SERVICE_CREATE_FAILED", "Coolify service creation failed")
        created_service_uuid = _uuid_from_payload(service_post["payload"], ("uuid", "service.uuid", "data.uuid"))
        if created_service_uuid is None:
            for endpoint, keys in (("/api/v1/services", ("services",)), ("/api/v1/resources", ("resources",))):
                observed = get_coolify_json(
                    controller,
                    endpoint,
                    authenticated=True,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                matches = _find_named(_items(observed.payload, keys), node)
                if len(matches) == 1:
                    created_service_uuid = _extract_uuid(matches[0], "created service")
                    break
        if created_service_uuid is None:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_SERVICE_CREATE_UNBOUND", "created service UUID could not be bound")
        service_creation_performed = True
        live_mutation_count += 1
        mutation_receipts.append(
            {
                "ordinal": 2 if environment_status == "existing" else 2,
                "phase": "create-standby-service",
                "mutation_id": f"{node}.create-standby-service",
                "node": node,
                "controller_id": controller_id,
                "method": "POST",
                "endpoint": "/api/v1/services",
                "body_sha256": hashlib.sha256(canonical_json(service_body)).hexdigest(),
                "live_write_acknowledged": True,
                "response": _safe_response(service_post),
                "status": "succeeded",
                "bound_uuid": created_service_uuid,
            }
        )

        target_get = get_coolify_json(
            controller,
            f"/api/v1/services/{urllib.parse.quote(created_service_uuid, safe='')}",
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        target_observation = {
            "node": node,
            "controller_id": controller_id,
            "service_uuid": created_service_uuid,
            "method": "GET",
            "endpoint": f"/api/v1/services/{created_service_uuid}",
            "observed_at": _timestamp(now=now),
            "response": {
                "status": target_get.status,
                "ok": 200 <= target_get.status < 300,
                "content_type": target_get.content_type,
                "response_sha256": target_get.response_sha256,
                "byte_length": target_get.byte_length,
                "elapsed_ms": target_get.elapsed_ms,
            },
            "service_status": target_get.payload.get("status") if isinstance(target_get.payload, Mapping) else None,
            "node_observed": 200 <= target_get.status < 300,
        }
        service_creation_proven = bool(target_observation["node_observed"])
        if not service_creation_proven:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_SERVICE_NOT_PROVEN", "created target service could not be re-observed")
        status = "pass"
    except MotherDeploymentNodeAddDoError as exc:
        failure = {"code": exc.code, "message": str(exc)}

    completed_at = _timestamp(now=now)
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        "failure": failure,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": release["mode"],
        "release": {
            "locator": _relative(paths, Path(release_path), label="add-node do release"),
            "sha256": acknowledged,
        },
        "execution_claim": {
            "locator": _relative(paths, claim_path, label="add-node do execution claim"),
        },
        "source_transaction": dict(release["source_transaction"]),
        "source_baseline_evidence": dict(release["source_baseline_evidence"]),
        "target": {
            **dict(target),
            "created_service_uuid": created_service_uuid,
            "environment_uuid": environment_uuid,
            "environment_status": environment_status,
        },
        "current_topology": dict(release["current_topology"]),
        "prepared_post_add_topology": dict(release["post_add_topology"]),
        "standby_topology": {
            "nodes": [*release["current_topology"]["nodes"], node] if service_creation_proven else list(release["current_topology"]["nodes"]),
            "standby_node": node if service_creation_proven else None,
            "standby_service_uuid": created_service_uuid,
            "validator_set": list(release["current_topology"]["validator_set"]),
            "validator_count": int(release["current_topology"]["validator_count"]),
            "validator_admission_performed": False,
        },
        "topology_diff": dict(release["topology_diff"]),
        "ordered_add_plan": list(release["ordered_add_plan"]),
        "mutation_receipts": mutation_receipts,
        "target_service_observation": target_observation,
        "service_creation_performed": service_creation_performed,
        "service_creation_proven": service_creation_proven,
        "validator_admission_performed": False,
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "live_mutation_performed": live_mutation_count > 0,
        "policy": {
            "allowed_http_methods": ["GET", "POST"],
            "coolify_control_plane_only": True,
            "service_creation_performed": service_creation_performed,
            "identity_install_performed": False,
            "replica_sync_performed": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "routing_or_topology_published": False,
            "public_http_endpoint_created": False,
            "manual_ssh_required": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "authority": {
            "release_consumed": True,
            "service_creation_authorized": True,
            "service_creation_proven": service_creation_proven,
            "identity_install_authorized": False,
            "replica_sync_authorized": False,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
        },
        "remaining_phases": [
            "install-identity",
            "sync-replica",
            "admit-validator",
            "post-admission-observe",
            "finalize-operation",
        ],
        "next_phase": f"add-node-identity-{network}" if status == "pass" else "manual-review-required",
    }
    evidence["summary"] = {
        "clean": status == "pass",
        "complete": status == "pass",
        "target_node": node,
        "target_host": controller_id,
        "created_service_uuid": created_service_uuid,
        "service_creation_performed": service_creation_performed,
        "service_creation_proven": service_creation_proven,
        "current_nodes": list(release["current_topology"]["nodes"]),
        "standby_nodes": list(evidence["standby_topology"]["nodes"]),
        "prepared_post_add_nodes": list(release["post_add_topology"]["nodes"]),
        "current_validator_count": int(release["current_topology"]["validator_count"]),
        "prepared_post_add_validator_count": int(release["post_add_topology"]["validator_count"]),
        "validator_admission_performed": False,
        "live_mutation_performed": live_mutation_count > 0,
        "mutation_count": live_mutation_count,
        "generic_topology_diff": True,
        "hardcoded_stage_target": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": evidence["next_phase"],
    }
    if _contains_sensitive(evidence):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_SENSITIVE", "node-add do evidence contains sensitive material")
    path, digest = _write_evidence(paths, evidence, operation=operation)
    return {**evidence, "evidence": {"path": str(path), "sha256": digest}}


def verify_node_add_do_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
    release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = Path(evidence_path).resolve(strict=False)
    allowed = _root(paths, _EVIDENCE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_PATH_INVALID", "node-add do evidence is outside its directory") from exc
    document, raw, file_sha = _canonical_file(resolved)
    if document.get("kind") != _EVIDENCE_KIND or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_EVIDENCE_INVALID", "node-add do evidence is invalid")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_EVIDENCE_STALE", "node-add do evidence is outside the freshness window")
    release_binding = document.get("release")
    if not isinstance(release_binding, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_EVIDENCE_INVALID", "release binding is missing")
    release_path = _resolve_under(paths, release_binding.get("locator"), _RELEASE_DIRECTORY, label="add-node do release")
    release_verified = verify_node_add_do_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    clean = (
        document.get("status") == "pass"
        and document.get("failure") is None
        and document.get("service_creation_performed") is True
        and document.get("service_creation_proven") is True
        and document.get("validator_admission_performed") is False
        and document.get("routing_or_topology_published") is False
        and document.get("public_endpoint_created") is False
        and document.get("summary", {}).get("generic_topology_diff") is True
        and document.get("summary", {}).get("hardcoded_stage_target") is False
    )
    if not clean:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_DO_EVIDENCE_INVALID", "node-add do evidence is not clean")
    target = document["target"]
    return {
        "clean": True,
        "evidence_path": str(resolved),
        "evidence_sha256": file_sha,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "node_add_do_release_sha256": release_verified["node_add_do_release_sha256"],
        "source_prep_transaction_sha256": document["source_transaction"]["sha256"],
        "source_baseline_evidence_sha256": document["source_baseline_evidence"]["sha256"],
        "target_node": target["node"],
        "target_host": target["controller_id"],
        "created_service_uuid": target.get("created_service_uuid"),
        "current_nodes": list(document["current_topology"]["nodes"]),
        "standby_nodes": list(document["standby_topology"]["nodes"]),
        "prepared_post_add_nodes": list(document["prepared_post_add_topology"]["nodes"]),
        "service_creation_performed": True,
        "service_creation_proven": True,
        "validator_admission_performed": False,
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "live_mutation_performed": True,
        "generic_topology_diff": True,
        "hardcoded_stage_target": False,
        "next_phase": document["next_phase"],
    }


__all__ = [
    "MotherDeploymentNodeAddDoError",
    "build_node_add_do_release",
    "execute_node_add_do_release",
    "verify_node_add_do_evidence",
    "verify_node_add_do_release",
    "write_node_add_do_release",
]
