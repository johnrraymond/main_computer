"""Guarded generic Mother ``add-node identity`` release and executor.

This module installs the reserved validator and Hub administrator identity for
the explicit standby service created by the generic ``add-node do`` phase.  It
is topology-driven: the target node, service UUID, controller, and validator
identity are derived from add-node evidence plus committed Mother private state.
It does not synchronize the replica, admit the validator, publish routing or
topology, or create a public endpoint.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any
import urllib.parse
import urllib.request

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import (
    _DEFAULT_MAX_RESPONSE_BYTES,
    _DEFAULT_OPENER,
    get_coolify_json,
    resolve_coolify_controller,
)
from .deployment_node_add_do import verify_node_add_do_evidence
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path
import yaml


_RELEASE_KIND = "main_computer.mother.deployment_node_add_identity_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_node_add_identity_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_node_add_identity_evidence.v1"
_ADD_DO_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-add-do")
_RELEASE_DIRECTORY = ("actions", "deployment-node-add-identity-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-node-add-identity-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-add-identity")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_IDENTITY_ENV_KEYS = ("MC_MOTHER_VALIDATOR_PRIVATE_KEY", "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY")


class MotherDeploymentNodeAddIdentityError(RuntimeError):
    """Node-add identity install failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentNodeAddIdentityError:
    return MotherDeploymentNodeAddIdentityError(code, message)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_INVALID", f"{label} is missing")
    if not _IDENTIFIER_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_INVALID", f"{label} is not a valid identifier")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_INVALID", f"{label} must be a SHA-256 hex digest")
    return value


def _timestamp(value: str | None = None, *, now: datetime | None = None) -> str:
    if value is None:
        current = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
        return current.isoformat(timespec="seconds").replace("+00:00", "Z")
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_INVALID", "timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_INVALID", "timestamp is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_INVALID", "timestamp must be UTC")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_INVALID", f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_INVALID", f"{label} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_INVALID", f"{label} must be UTC")
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any, *, now: datetime | None = None) -> int:
    current = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((current - _parse_utc(value, "created_at")).total_seconds())
    return max(age, 0)


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _identity_current_validator_count(document: Mapping[str, Any]) -> int:
    current = document.get("current_topology")
    if not isinstance(current, Mapping):
        return -1
    value = current.get("validator_count")
    if isinstance(value, int):
        return value
    validators = current.get("validator_set")
    return len(validators) if isinstance(validators, list) else -1


def _identity_prepared_validator_count(document: Mapping[str, Any]) -> int:
    prepared = document.get("prepared_post_add_topology")
    if not isinstance(prepared, Mapping):
        return -1
    value = prepared.get("validator_count")
    if isinstance(value, int):
        return value
    validators = prepared.get("validator_set")
    return len(validators) if isinstance(validators, list) else -1


def _identity_after_install_routing(document: Mapping[str, Any]) -> dict[str, Any]:
    network = _identifier(document.get("network"), "network")
    current_count = _identity_current_validator_count(document)
    prepared_count = _identity_prepared_validator_count(document)
    if current_count == 0 and prepared_count == 1:
        return {
            "bootstrap_mode": "operator-directed-single-node",
            "single_node_bootstrap_required": True,
            "replica_sync_required": False,
            "validator_admission_required": False,
            "remaining_phases": [
                "single-node-bootstrap",
                "single-node-chain-and-hub-proof",
                "finalize-operation",
            ],
            "next_phase": f"add-node-single-node-bootstrap-{network}",
        }
    return {
        "bootstrap_mode": "join-existing-validator-set",
        "single_node_bootstrap_required": False,
        "replica_sync_required": True,
        "validator_admission_required": True,
        "remaining_phases": [
            "sync-replica",
            "admit-validator",
            "post-admission-observe",
            "finalize-operation",
        ],
        "next_phase": f"add-node-replica-sync-{network}",
    }


def _digest_without(value: Mapping[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _root(paths: PrivateStatePaths, directory: tuple[str, ...]) -> Path:
    current = paths.root
    for part in directory:
        current = current / part
    return current


def _ensure_directory(path: Path, *, operation: OperationIdentity) -> None:
    current = path
    atomic_files.ensure_durable_directory(current, operation=operation)
    _secure_private_path(current, is_directory=True, operation=operation)


def _relative(paths: PrivateStatePaths, candidate: Path, *, label: str) -> str:
    resolved = Path(candidate).resolve(strict=False)
    root = paths.root.resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_PATH_INVALID", f"{label} must be beneath Mother state root") from exc


def _resolve_under(paths: PrivateStatePaths, locator: Any, directory: tuple[str, ...], *, label: str) -> Path:
    if not isinstance(locator, str) or not locator or "\\" in locator:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_PATH_INVALID", f"{label} locator is invalid")
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_PATH_INVALID", f"{label} locator is unsafe")
    resolved = (paths.root / candidate).resolve(strict=False)
    allowed = _root(paths, directory).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_PATH_INVALID", f"{label} is outside its canonical directory") from exc
    return resolved


def _canonical_file(path: Path) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_INVALID", "canonical JSON file could not be read") from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_INVALID", "file is not canonical JSON")
    return value, raw, hashlib.sha256(raw).hexdigest()


def _private_document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        value = yaml.safe_load(private_state.document_bytes)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_PRIVATE_STATE_INVALID", "committed Mother private state is malformed") from exc
    if not isinstance(value, dict):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_PRIVATE_STATE_INVALID", "committed Mother private state must be a mapping")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_PRIVATE_STATE_INVALID", f"{label} is missing")
    return value


def _resolve_dotted(document: Mapping[str, Any], dotted: str) -> Any:
    current: Any = document
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_SOURCE_MISSING", f"private-state source {dotted!r} does not resolve")
        current = current[part]
    return current


def _private_key(value: Any, label: str) -> str:
    if not isinstance(value, str) or _PRIVATE_KEY_RE.fullmatch(value) is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_SOURCE_INVALID", f"{label} is not a reserved private key")
    return "0x" + value[2:].lower()


def _node_identity_sources(private_state: PrivateStateReadResult, network: str, node: str) -> dict[str, str]:
    document = _private_document(private_state)
    networks = _mapping(document.get("networks"), "networks")
    network_doc = _mapping(networks.get(network), f"networks.{network}")
    validators = _mapping(network_doc.get("validators"), f"networks.{network}.validators")
    validator = _mapping(validators.get(node), f"networks.{network}.validators.{node}")
    validator_key = _private_key(validator.get("private_key"), "validator reserved identity")
    deployment = _mapping(network_doc.get("deployment"), f"networks.{network}.deployment")
    targets = _mapping(deployment.get("targets"), f"networks.{network}.deployment.targets")
    target = _mapping(targets.get(node), f"networks.{network}.deployment.targets.{node}")
    hub_ref = target.get("hub_admin_private_key_path")
    if not isinstance(hub_ref, str) or not hub_ref:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_SOURCE_MISSING", f"Hub admin identity source is missing for {node!r}")
    hub_key = _private_key(_resolve_dotted(document, hub_ref), "Hub admin reserved identity")
    return {
        "MC_MOTHER_VALIDATOR_PRIVATE_KEY": validator_key,
        "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY": hub_key,
    }


def _body(key: str, value: str) -> dict[str, Any]:
    return {
        "key": key,
        "value": value,
        "is_build_time": False,
        "is_runtime": True,
        "is_literal": True,
        "is_multiline": False,
    }


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, Mapping):
        sensitive_keys = {"api_token", "access_token", "bearer_token", "password", "private_key", "secret", "value"}
        return any(str(key).lower() in sensitive_keys or _contains_sensitive(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    if isinstance(value, str):
        if _PRIVATE_KEY_RE.fullmatch(value) is not None:
            return True
        lowered = value.lower()
        return "bearer " in lowered or "begin private key" in lowered
    return False


def _safe_response(response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": response.get("status"),
        "ok": response.get("ok"),
        "content_type": response.get("content_type"),
        "response_sha256": response.get("response_sha256"),
        "byte_length": response.get("byte_length"),
        "elapsed_ms": response.get("elapsed_ms"),
    }


def _open(
    controller: Mapping[str, str],
    method: str,
    endpoint: str,
    *,
    body: Mapping[str, Any] | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    base = str(getattr(controller, "base_url", controller.get("url") if isinstance(controller, Mapping) else "")).rstrip("/")
    url = base + endpoint
    data = canonical_json(body) if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    token = getattr(controller, "api_token", controller.get("api_token") if isinstance(controller, Mapping) else "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = datetime.now(timezone.utc)
    try:
        response = opener.open(request, timeout=timeout)
        try:
            raw = response.read(max_response_bytes + 1)
            status = int(response.getcode())
            content_type = str(response.headers.get("Content-Type", ""))
        finally:
            response.close()
    except Exception as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_HTTP_FAILED", f"Coolify {method} {endpoint} failed: {exc}") from exc
    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    body_bytes = raw[:max_response_bytes]
    payload: Any = None
    if body_bytes:
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "content_type": content_type,
        "response_sha256": hashlib.sha256(body_bytes).hexdigest(),
        "byte_length": len(body_bytes),
        "elapsed_ms": elapsed_ms,
        "payload": payload,
    }


def _items(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        for key in ("data", "envs", "environment_variables", "variables"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, Mapping)]
    return []


def _env_key(value: Mapping[str, Any]) -> str | None:
    for key in ("key", "name"):
        raw = value.get(key)
        if isinstance(raw, str) and raw:
            return raw
    return None


def _env_uuid(value: Mapping[str, Any]) -> str | None:
    for key in ("uuid", "id"):
        raw = value.get(key)
        if isinstance(raw, (str, int)) and str(raw):
            return str(raw)
    for key in ("environment_variable", "env", "data"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            found = _env_uuid(nested)
            if found is not None:
                return found
    return None


def _visible_value(value: Mapping[str, Any]) -> str | None:
    for key in ("value", "real_value"):
        raw = value.get(key)
        if isinstance(raw, str):
            return raw
    return None


def _find_unique_env(payload: Any, key: str) -> Mapping[str, Any] | None:
    matches = [item for item in _items(payload) if _env_key(item) == key]
    if len(matches) > 1:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_ENV_AMBIGUOUS", f"Coolify environment key {key!r} appears more than once")
    return matches[0] if matches else None


def _claim_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> Path:
    digest = str(release["node_add_identity_release_sha256"])
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{digest}.json"
    _ensure_directory(claim_path.parent, operation=operation)
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release_sha256": digest,
        "source_add_do_evidence_sha256": release["source_add_do_evidence"]["sha256"],
        "target_node": release["target"]["node"],
        "target_service_uuid": release["target"]["created_service_uuid"],
        "operation_id": operation.operation_id,
    }
    payload = canonical_json(claim)
    if claim_path.exists():
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_RELEASE_ALREADY_CLAIMED", "node-add identity release was already claimed")
    atomic_files.durable_create(claim_path, payload, operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)
    return claim_path


def _write_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(document, "node_add_identity_release_sha256")
    if document.get("kind") != _RELEASE_KIND or document.get("node_add_identity_release_sha256") != digest or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_RELEASE_INVALID", "node-add identity release is malformed")
    directory = _root(paths, _RELEASE_DIRECTORY)
    _ensure_directory(directory, operation=operation)
    network = _identifier(document.get("network"), "network")
    node = _identifier(document.get("target", {}).get("node"), "target node")
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "nodeaddidentity"
    destination = directory / f"{stamp}-{network}-{node}-{digest[:16]}.json"
    payload = canonical_json(document)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_RELEASE_CONFLICT", "node-add identity release destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_RELEASE_WRITE_FAILED", "node-add identity release reread mismatch")
    return destination, digest


def write_node_add_identity_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    return _write_release(paths, release, operation=operation)


def _write_evidence(paths: PrivateStatePaths, evidence: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(evidence)
    if document.get("kind") != _EVIDENCE_KIND or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_EVIDENCE_INVALID", "node-add identity evidence is malformed")
    directory = _root(paths, _EVIDENCE_DIRECTORY)
    _ensure_directory(directory, operation=operation)
    node = _identifier(document.get("target", {}).get("node"), "target node")
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "nodeaddidentity"
    digest = hashlib.sha256(canonical_json(document)).hexdigest()
    destination = directory / f"{stamp}-{node}-{digest[:16]}.json"
    payload = canonical_json(document)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_EVIDENCE_CONFLICT", "node-add identity evidence destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_EVIDENCE_WRITE_FAILED", "node-add identity evidence reread mismatch")
    return destination, digest


def _load_add_do_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    expected_sha256: str | None,
    max_age_seconds: int,
    release_max_age_seconds: int,
    transaction_max_age_seconds: int,
    baseline_max_age_seconds: int,
    now: datetime | None,
) -> tuple[dict[str, Any], Path, str, str]:
    del release_max_age_seconds, transaction_max_age_seconds, baseline_max_age_seconds
    resolved = Path(evidence_path).resolve(strict=False)
    allowed = _root(paths, _ADD_DO_EVIDENCE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_PATH_INVALID", "add-node do evidence is outside its directory") from exc
    document, raw, file_sha = _canonical_file(resolved)
    if expected_sha256 is not None and file_sha != expected_sha256:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_ADD_DO_MISMATCH", "acknowledged add-node do evidence SHA does not match")
    if document.get("kind") != "main_computer.mother.deployment_node_add_do_evidence.v1" or document.get("mother_binding") != _binding(private_state):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_ADD_DO_INVALID", "add-node do evidence is invalid")
    if _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_ADD_DO_INVALID", "add-node do evidence contains sensitive material")
    if _age_seconds(document.get("completed_at"), now=now) > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_ADD_DO_STALE", "add-node do evidence is outside the freshness window")
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
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_ADD_DO_INVALID", "add-node do evidence is not clean")
    target = document.get("target")
    if not isinstance(target, Mapping) or not isinstance(target.get("created_service_uuid"), str) or not target["created_service_uuid"]:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_ADD_DO_INVALID", "add-node do evidence does not bind a created service UUID")
    if document.get("next_phase") != f"add-node-identity-{document.get('network')}":
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_ADD_DO_INVALID", "add-node do evidence is not at add-node identity phase")
    return document, resolved, file_sha, hashlib.sha256(raw).hexdigest()


def build_node_add_identity_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    add_do_evidence_path: Path,
    *,
    acknowledged_add_do_evidence_sha256: str,
    max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if expires_in_seconds <= 0 or expires_in_seconds > 900:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_RELEASE_INVALID", "release expiry must be between 1 and 900 seconds")
    add_do_evidence, resolved, evidence_sha, byte_sha = _load_add_do_evidence(
        paths,
        private_state,
        Path(add_do_evidence_path),
        expected_sha256=_sha256(acknowledged_add_do_evidence_sha256, "acknowledged add-node do evidence sha256"),
        max_age_seconds=max_age_seconds,
        release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    target = dict(add_do_evidence["target"])
    node = _identifier(target["node"], "target node")
    service_uuid = _identifier(target["created_service_uuid"], "created service UUID")
    # Resolve and commit to current private-state identities without persisting values.
    sources = _node_identity_sources(private_state, add_do_evidence["network"], node)
    identity_commitments = []
    for ordinal, env_key in enumerate(_IDENTITY_ENV_KEYS, start=1):
        value = sources[env_key]
        body = _body(env_key, value)
        identity_commitments.append(
            {
                "ordinal": ordinal,
                "environment_key": env_key,
                "phase": "install-identity",
                "method": "POST",
                "endpoint": f"/api/v1/services/{service_uuid}/envs",
                "body_materialization": "mother-private-state-in-memory",
                "body_sha256": hashlib.sha256(canonical_json(body)).hexdigest(),
                "value_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                "value_bytes": len(value.encode("utf-8")),
                "refuse_existing_key": True,
            }
        )
    created = _timestamp(created_at, now=now)
    expires = (_parse_utc(created, "created_at") + timedelta(seconds=int(expires_in_seconds))).isoformat(timespec="seconds").replace("+00:00", "Z")
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created,
        "expires_at": expires,
        "mother_binding": _binding(private_state),
        "network": add_do_evidence["network"],
        "mode": add_do_evidence["mode"],
        "source_add_do_evidence": {
            "locator": _relative(paths, resolved, label="add-node do evidence"),
            "sha256": evidence_sha,
            "byte_sha256": byte_sha,
        },
        "source_prep_transaction": dict(add_do_evidence["source_transaction"]),
        "source_baseline_evidence": dict(add_do_evidence["source_baseline_evidence"]),
        "target": target,
        "current_topology": dict(add_do_evidence["current_topology"]),
        "standby_topology": dict(add_do_evidence["standby_topology"]),
        "prepared_post_add_topology": dict(add_do_evidence["prepared_post_add_topology"]),
        "topology_diff": dict(add_do_evidence["topology_diff"]),
        "identity_commitments": identity_commitments,
        "authority": {
            "authorization_source": "explicit-operator-release",
            "requested_use_limit": 1,
            "live_execution_authorized": True,
            "service_creation_authorized": False,
            "identity_install_authorized": True,
            "replica_sync_authorized": False,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
        },
        "policy": {
            "compiler": "mother-native-add-node-identity-v1",
            "requested_use_limit": 1,
            "identity_install_authorized": True,
            "refuse_existing_identity_env_keys": True,
            "private_keys_materialized_in_memory_only": True,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "service_creation_authorized": False,
            "replica_sync_authorized": False,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "public_http_endpoint_created": False,
            "manual_ssh_required": False,
        },
        "summary": {
            "clean": True,
            "executor_implemented": True,
            "generic_topology_diff": True,
            "hardcoded_stage_target": False,
            "target_node": node,
            "target_host": target["controller_id"],
            "created_service_uuid": service_uuid,
            "identity_install_authorized": True,
            "identity_env_key_count": len(identity_commitments),
            "validator_admission_authorized": False,
            "next_phase": f"add-node-identity-{add_do_evidence['network']}",
        },
    }
    release["node_add_identity_release_sha256"] = _digest_without(release, "node_add_identity_release_sha256")
    if _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_SENSITIVE", "node-add identity release contains sensitive material")
    return release


def verify_node_add_identity_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    max_age_seconds: int = 900,
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
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_PATH_INVALID", "node-add identity release is outside its directory") from exc
    document, _raw, _file_sha = _canonical_file(resolved)
    digest = _digest_without(document, "node_add_identity_release_sha256")
    if document.get("kind") != _RELEASE_KIND or document.get("node_add_identity_release_sha256") != digest or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_RELEASE_INVALID", "node-add identity release is invalid")
    age = _age_seconds(document.get("created_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_RELEASE_STALE", "node-add identity release is outside the freshness window")
    if _parse_utc(document.get("expires_at"), "expires_at") < (now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_RELEASE_EXPIRED", "node-add identity release has expired")
    source = document.get("source_add_do_evidence")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_RELEASE_INVALID", "source add-node do evidence binding is missing")
    evidence_path = _resolve_under(paths, source.get("locator"), _ADD_DO_EVIDENCE_DIRECTORY, label="add-node do evidence")
    add_do_evidence, _resolved_evidence, evidence_sha, _byte_sha = _load_add_do_evidence(
        paths,
        private_state,
        evidence_path,
        expected_sha256=source.get("sha256"),
        max_age_seconds=add_do_max_age_seconds,
        release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    target = document["target"]
    if target != add_do_evidence["target"]:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_RELEASE_INVALID", "release target does not match add-node do evidence")
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{digest}.json"
    return {
        "clean": True,
        "release_path": str(resolved),
        "node_add_identity_release_sha256": digest,
        "release_already_claimed": claim_path.exists(),
        "age_seconds": age,
        "expires_at": document["expires_at"],
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "source_add_do_evidence_sha256": evidence_sha,
        "source_prep_transaction_sha256": document["source_prep_transaction"]["sha256"],
        "source_baseline_evidence_sha256": document["source_baseline_evidence"]["sha256"],
        "target_node": target["node"],
        "target_host": target["controller_id"],
        "created_service_uuid": target["created_service_uuid"],
        "identity_install_authorized": True,
        "identity_env_key_count": len(document.get("identity_commitments", [])),
        "validator_admission_authorized": False,
        "generic_topology_diff": True,
        "hardcoded_stage_target": False,
        "next_phase": document["summary"]["next_phase"],
    }


def execute_node_add_identity_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 900,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    acknowledged = _sha256(acknowledged_release_sha256, "acknowledged release sha256")
    verified = verify_node_add_identity_release(
        paths,
        private_state,
        Path(release_path),
        max_age_seconds=max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if verified["node_add_identity_release_sha256"] != acknowledged:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_ACK_MISMATCH", "acknowledged release SHA does not match")
    if verified.get("release_already_claimed") is True:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_RELEASE_ALREADY_CLAIMED", "node-add identity release was already claimed")

    release, _raw, _file_sha = _canonical_file(Path(release_path))
    claim_path = _claim_release(paths, release, operation=operation)

    network = _identifier(release["network"], "network")
    target = release["target"]
    node = _identifier(target["node"], "target node")
    controller_id = _identifier(target["controller_id"], "target controller")
    service_uuid = _identifier(target["created_service_uuid"], "created service UUID")
    controller = resolve_coolify_controller(private_state, network, controller_id)
    sources = _node_identity_sources(private_state, network, node)

    started_at = _timestamp(now=now)
    receipts: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None
    status = "failed"
    installed_count = 0
    try:
        endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}/envs"
        for ordinal, env_key in enumerate(_IDENTITY_ENV_KEYS, start=1):
            value = sources[env_key]
            body = _body(env_key, value)
            value_sha = hashlib.sha256(value.encode("utf-8")).hexdigest()
            body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
            before = _open(
                controller,
                "GET",
                endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            if not before["ok"]:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_PRECONDITION_FAILED", f"Coolify env GET failed for {env_key!r} with HTTP {before['status']}")
            if _find_unique_env(before["payload"], env_key) is not None:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_ENV_ALREADY_EXISTS", f"Coolify environment key {env_key!r} already exists; overwrite is refused")

            posted = _open(
                controller,
                "POST",
                endpoint,
                body=body,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            receipt: dict[str, Any] = {
                "ordinal": ordinal,
                "mutation_id": f"{node}.install-{env_key.lower().replace('_', '-')}",
                "node": node,
                "controller_id": controller_id,
                "endpoint": endpoint,
                "method": "POST",
                "phase": "install-identity",
                "environment_key": env_key,
                "value_sha256": value_sha,
                "body_sha256": body_sha,
                "environment_variable_uuid": None,
                "precondition": {
                    "status": before["status"],
                    "response_sha256": before["response_sha256"],
                    "key_absent": True,
                },
                "mutation_response": _safe_response(posted),
                "postcondition": {
                    "status": None,
                    "response_sha256": None,
                    "key_unique": None,
                    "commitment_verified": False,
                    "proof_mode": None,
                },
                "live_write_acknowledged": posted["ok"],
                "status": "failed",
            }
            receipts.append(receipt)
            if not posted["ok"]:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_MUTATION_FAILED", f"Coolify rejected identity write for {env_key!r} with HTTP {posted['status']}")

            after = _open(
                controller,
                "GET",
                endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            receipt["postcondition"]["status"] = after["status"]
            receipt["postcondition"]["response_sha256"] = after["response_sha256"]
            if not after["ok"]:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_POSTCONDITION_FAILED", f"Coolify post-write GET failed for {env_key!r} with HTTP {after['status']}")
            installed = _find_unique_env(after["payload"], env_key)
            if installed is None:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_POSTCONDITION_FAILED", f"Coolify did not expose installed environment key {env_key!r}")
            visible = _visible_value(installed)
            proof_mode = "readback-value-sha256"
            if visible is None and isinstance(posted["payload"], Mapping):
                visible = _visible_value(posted["payload"])
                proof_mode = "post-response-value-sha256"
            if visible is None or hashlib.sha256(visible.encode("utf-8")).hexdigest() != value_sha:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_COMMITMENT_NOT_PROVEN", f"Coolify did not prove value commitment for {env_key!r}")
            env_uuid = _env_uuid(installed)
            if env_uuid is None and isinstance(posted["payload"], Mapping):
                env_uuid = _env_uuid(posted["payload"])
            if env_uuid is None:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_POSTCONDITION_FAILED", f"Coolify did not return an environment-variable UUID for {env_key!r}")
            receipt["environment_variable_uuid"] = env_uuid
            receipt["postcondition"]["key_unique"] = True
            receipt["postcondition"]["commitment_verified"] = True
            receipt["postcondition"]["proof_mode"] = proof_mode
            receipt["status"] = "succeeded"
            installed_count += 1
        status = "pass"
    except MotherDeploymentNodeAddIdentityError as exc:
        failure = {"code": exc.code, "message": str(exc)}

    completed_at = _timestamp(now=now)
    complete = status == "pass" and installed_count == len(_IDENTITY_ENV_KEYS)
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
            "locator": _relative(paths, Path(release_path), label="add-node identity release"),
            "sha256": acknowledged,
        },
        "execution_claim": {
            "locator": _relative(paths, claim_path, label="add-node identity execution claim"),
        },
        "source_add_do_evidence": dict(release["source_add_do_evidence"]),
        "source_prep_transaction": dict(release["source_prep_transaction"]),
        "source_baseline_evidence": dict(release["source_baseline_evidence"]),
        "target": dict(target),
        "current_topology": dict(release["current_topology"]),
        "standby_topology": dict(release["standby_topology"]),
        "prepared_post_add_topology": dict(release["prepared_post_add_topology"]),
        "topology_diff": dict(release["topology_diff"]),
        "identity_commitments": list(release["identity_commitments"]),
        "mutation_receipts": receipts,
        "identity_install_performed": installed_count > 0,
        "identity_install_proven": complete,
        "service_creation_previously_performed": True,
        "replica_sync_performed": False,
        "validator_admission_performed": False,
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "live_mutation_performed": installed_count > 0,
        "policy": {
            "allowed_http_methods": ["GET", "POST"],
            "coolify_control_plane_only": True,
            "identity_install_performed": installed_count > 0,
            "identity_install_proven": complete,
            "private_keys_materialized_in_memory_only": True,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "service_creation_performed": False,
            "replica_sync_performed": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "routing_or_topology_published": False,
            "public_http_endpoint_created": False,
            "manual_ssh_required": False,
        },
        "authority": {
            "release_consumed": True,
            "service_creation_authorized": False,
            "identity_install_authorized": True,
            "identity_install_proven": complete,
            "replica_sync_authorized": False,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
        },
        **({
            "remaining_phases": _identity_after_install_routing(release)["remaining_phases"],
            "next_phase": _identity_after_install_routing(release)["next_phase"],
            "single_node_bootstrap_required": _identity_after_install_routing(release)["single_node_bootstrap_required"],
            "replica_sync_required": _identity_after_install_routing(release)["replica_sync_required"],
            "validator_admission_required": _identity_after_install_routing(release)["validator_admission_required"],
            "bootstrap_mode": _identity_after_install_routing(release)["bootstrap_mode"],
        } if complete else {
            "remaining_phases": ["manual-review"],
            "next_phase": "manual-review-required",
            "single_node_bootstrap_required": False,
            "replica_sync_required": False,
            "validator_admission_required": False,
            "bootstrap_mode": "manual-review",
        }),
    }
    evidence["summary"] = {
        "clean": complete,
        "complete": complete,
        "target_node": node,
        "target_host": controller_id,
        "created_service_uuid": service_uuid,
        "identity_install_performed": installed_count > 0,
        "identity_install_proven": complete,
        "identity_env_key_count": len(_IDENTITY_ENV_KEYS),
        "identity_env_key_proven_count": installed_count,
        "service_creation_previously_performed": True,
        "replica_sync_performed": False,
        "validator_admission_performed": False,
        "single_node_bootstrap_required": evidence["single_node_bootstrap_required"],
        "replica_sync_required": evidence["replica_sync_required"],
        "validator_admission_required": evidence["validator_admission_required"],
        "bootstrap_mode": evidence["bootstrap_mode"],
        "live_mutation_performed": installed_count > 0,
        "mutation_count": installed_count,
        "generic_topology_diff": True,
        "hardcoded_stage_target": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": evidence["next_phase"],
    }
    if _contains_sensitive(evidence):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_SENSITIVE", "node-add identity evidence contains sensitive material")
    path, digest = _write_evidence(paths, evidence, operation=operation)
    return {**evidence, "evidence": {"path": str(path), "sha256": digest}}


def verify_node_add_identity_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
    release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = Path(evidence_path).resolve(strict=False)
    allowed = _root(paths, _EVIDENCE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_PATH_INVALID", "node-add identity evidence is outside its directory") from exc
    document, raw, file_sha = _canonical_file(resolved)
    if document.get("kind") != _EVIDENCE_KIND or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_EVIDENCE_INVALID", "node-add identity evidence is invalid")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_EVIDENCE_STALE", "node-add identity evidence is outside the freshness window")
    release_binding = document.get("release")
    if not isinstance(release_binding, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_EVIDENCE_INVALID", "release binding is missing")
    release_path = _resolve_under(paths, release_binding.get("locator"), _RELEASE_DIRECTORY, label="add-node identity release")
    release_verified = verify_node_add_identity_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    receipts = document.get("mutation_receipts")
    clean = (
        document.get("status") == "pass"
        and document.get("failure") is None
        and document.get("identity_install_performed") is True
        and document.get("identity_install_proven") is True
        and document.get("replica_sync_performed") is False
        and document.get("validator_admission_performed") is False
        and document.get("routing_or_topology_published") is False
        and document.get("public_endpoint_created") is False
        and document.get("summary", {}).get("generic_topology_diff") is True
        and document.get("summary", {}).get("hardcoded_stage_target") is False
        and isinstance(receipts, list)
        and len(receipts) == len(_IDENTITY_ENV_KEYS)
        and all(isinstance(item, Mapping) and item.get("status") == "succeeded" and item.get("live_write_acknowledged") is True and isinstance(item.get("postcondition"), Mapping) and item["postcondition"].get("commitment_verified") is True for item in receipts)
    )
    if not clean:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_IDENTITY_EVIDENCE_INVALID", "node-add identity evidence is not clean")
    target = document["target"]
    return {
        "clean": True,
        "evidence_path": str(resolved),
        "evidence_sha256": file_sha,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "node_add_identity_release_sha256": release_verified["node_add_identity_release_sha256"],
        "source_add_do_evidence_sha256": document["source_add_do_evidence"]["sha256"],
        "source_prep_transaction_sha256": document["source_prep_transaction"]["sha256"],
        "source_baseline_evidence_sha256": document["source_baseline_evidence"]["sha256"],
        "target_node": target["node"],
        "target_host": target["controller_id"],
        "created_service_uuid": target["created_service_uuid"],
        "identity_install_performed": True,
        "identity_install_proven": True,
        "identity_env_key_count": len(receipts),
        "service_creation_previously_performed": True,
        "replica_sync_performed": False,
        "validator_admission_performed": False,
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "live_mutation_performed": True,
        "generic_topology_diff": True,
        "hardcoded_stage_target": False,
        "single_node_bootstrap_required": document.get("single_node_bootstrap_required") is True,
        "replica_sync_required": document.get("replica_sync_required") is True,
        "validator_admission_required": document.get("validator_admission_required") is True,
        "bootstrap_mode": document.get("bootstrap_mode"),
        "legacy_next_phase": (
            document["next_phase"]
            if _identity_current_validator_count(document) == 0
            and _identity_prepared_validator_count(document) == 1
            and document.get("next_phase") == f"add-node-replica-sync-{document.get('network')}"
            else None
        ),
        "next_phase": _identity_after_install_routing(document)["next_phase"],
    }


__all__ = [
    "MotherDeploymentNodeAddIdentityError",
    "build_node_add_identity_release",
    "execute_node_add_identity_release",
    "verify_node_add_identity_evidence",
    "verify_node_add_identity_release",
    "write_node_add_identity_release",
]
