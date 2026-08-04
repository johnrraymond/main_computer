"""Rollback support for the first live Mother deployment mutation.

The first live deployment step creates empty Coolify environments when needed
and stopped, secret-free standby services.  This module turns the exact
successful create receipts into idempotent inverse DELETE operations, verifies
ownership before deletion, and verifies absence after deletion.

Rollback remains available only while no later successful deployment phase for
the same nodes is present.  This module does not finalize an operation and does
not roll back identity, genesis, validator, routing, or topology mutations.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any
import urllib.error
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
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_EXECUTION_KIND = "main_computer.mother.deployment_execution_result.v1"
_FRAME_KIND = "main_computer.mother.deployment_rollback_frame.v1"
_ROLLBACK_RESULT_KIND = "main_computer.mother.deployment_rollback_result.v1"
_JOURNAL_KIND = "main_computer.mother.deployment_rollback_journal.v1"
_EXECUTION_DIRECTORY = ("actions", "deployment-executions")
_ROLLBACK_DIRECTORY = ("actions", "deployment-rollbacks")
_JOURNAL_DIRECTORY = ("actions", "deployment-rollback-journals")
_ALLOWED_DELETE_PREFIXES = (
    "/api/v1/services/",
    "/api/v1/projects/",
)
_DEFAULT_POST_DELETE_WAIT_SECONDS = 15.0
_DEFAULT_POST_DELETE_POLL_SECONDS = 0.5

_DOWNSTREAM_RESULT_DIRECTORIES = (
    "deployment-identity-executions",
    "deployment-genesis-executions",
    "deployment-genesis-birth-executions",
    "deployment-soft-replica-executions",
    "deployment-soft-replica-sync-executions",
    "deployment-validator-admission-executions",
    "deployment-validator-quorum-recovery-executions",
    "deployment-post-admission-steady-state-executions",
    "deployment-post-admission-steady-state-continuation-executions",
)


class MotherDeploymentRollbackError(RuntimeError):
    """The first live deployment step could not be safely rolled back."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"{path} must be a non-empty string",
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"{path} contains unsupported characters",
        )
    return text


def _sha256(value: Any, path: str) -> str:
    text = _identifier(value, path).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"{path} must be a SHA-256 digest",
        )
    return text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"{path} must be a UTC timestamp",
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"{path} is malformed",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"{path} must be UTC",
        )
    return parsed


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _contains_sensitive_key(value: Any) -> bool:
    forbidden = {
        "access_token",
        "api_token",
        "credential",
        "mnemonic",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "seed",
    }
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in forbidden or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _safe_message(exc: BaseException) -> str:
    text = re.sub(r"\s+", " ", str(exc)).strip()
    return text[:500] or exc.__class__.__name__


def _root(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return (paths.root / parts[0] / parts[1]).resolve(strict=False)


def _beneath(paths: PrivateStatePaths, path: Path, parts: tuple[str, str], *, label: str) -> Path:
    root = _root(paths, parts)
    candidate = Path(path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_PATH_UNSAFE",
            f"{label} is outside the canonical Mother directory",
        ) from exc
    return candidate


def _canonical_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"{label} could not be read as canonical JSON",
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"{label} is not canonical JSON",
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _items(payload: Any) -> list[Mapping[str, Any]]:
    if type(payload) is list:
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("services", "environments", "resources", "data"):
            value = payload.get(key)
            if type(value) is list:
                return [item for item in value if isinstance(item, Mapping)]
        if any(key in payload for key in ("uuid", "id", "name")):
            return [payload]
    return []


def _resource_matches(payload: Any, uuid: str) -> list[Mapping[str, Any]]:
    matches: list[Mapping[str, Any]] = []
    for item in _items(payload):
        value = item.get("uuid", item.get("id"))
        if str(value) == uuid:
            matches.append(item)
    return matches


def _validate_delete_endpoint(endpoint: Any) -> str:
    if type(endpoint) is not str or not endpoint.startswith(_ALLOWED_DELETE_PREFIXES):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_ENDPOINT_REJECTED",
            "rollback DELETE endpoint is outside the bounded Coolify surface",
        )
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/api/v1/"):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_ENDPOINT_REJECTED",
            "rollback DELETE endpoint must be one relative /api/v1 path",
        )
    if "\\" in endpoint or any(part in {"", ".", ".."} for part in parsed.path.split("/")[1:]):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_ENDPOINT_REJECTED",
            "rollback DELETE endpoint is unsafe",
        )
    return endpoint


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    if hasattr(opener, "open"):
        return opener.open(request, timeout=timeout)
    return opener(request, timeout=timeout)


def _http_delete(
    controller,
    endpoint: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    safe_endpoint = _validate_delete_endpoint(endpoint)
    request = urllib.request.Request(
        controller.base_url + safe_endpoint,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {controller.api_token}",
            "User-Agent": "main-computer-mother-deployment-rollback/1",
        },
        method="DELETE",
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
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_REQUEST_FAILED",
            f"Coolify DELETE failed: {exc.reason}",
        ) from exc
    except OSError as exc:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_REQUEST_FAILED",
            "Coolify DELETE failed",
        ) from exc
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    if len(raw) > max_response_bytes:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_RESPONSE_TOO_LARGE",
            f"Coolify response exceeded {max_response_bytes} bytes",
        )
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "content_type": content_type,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": elapsed_ms,
    }



def _journal_candidate_from_mutation(mutation: Mapping[str, Any]) -> dict[str, Any]:
    mutation_id = _identifier(mutation.get("mutation_id"), "mutation id")
    controller_id = _identifier(mutation.get("controller_id"), f"{mutation_id} controller")
    node = _identifier(mutation.get("node"), f"{mutation_id} node")
    body = mutation.get("canonical_request_body")
    if not isinstance(body, Mapping):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"mutation {mutation_id!r} has no canonical request body",
        )
    expected_name = _identifier(body.get("name"), f"{mutation_id} expected name")
    endpoint = str(mutation.get("endpoint", ""))
    ordinal = mutation.get("ordinal")
    if type(ordinal) is not int or ordinal < 1:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"mutation {mutation_id!r} has no valid ordinal",
        )
    if endpoint == "/api/v1/services":
        resource_kind = "service"
        observe_endpoint = "/api/v1/services"
        expected_description = str(body.get("description", ""))
    elif endpoint.startswith("/api/v1/projects/") and endpoint.endswith("/environments"):
        parts = endpoint.split("/")
        if len(parts) != 6:
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                f"mutation {mutation_id!r} has an unsupported environment endpoint",
            )
        _identifier(parts[4], f"{mutation_id} project UUID")
        resource_kind = "environment"
        observe_endpoint = endpoint
        expected_description = ""
    else:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_ENDPOINT_REJECTED",
            f"mutation {mutation_id!r} has no supported inverse operation",
        )
    return {
        "source_ordinal": ordinal,
        "mutation_id": mutation_id,
        "node": node,
        "controller_id": controller_id,
        "resource_kind": resource_kind,
        "expected_name": expected_name,
        "expected_description": expected_description,
        "observe_endpoint": observe_endpoint,
        "state": "pending",
        "attempted_at": None,
        "completed_at": None,
        "created_uuid": None,
    }


def build_deployment_rollback_journal(
    transaction: Mapping[str, Any],
    *,
    release_sha256: str,
    transaction_sha256: str,
    mother_binding: Mapping[str, Any],
    network: str,
    nodes: Iterable[str],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a durable pre-mutation rollback journal for crash recovery."""

    mutations = transaction.get("mutations")
    if type(mutations) is not list:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "deployment transaction mutation set is missing",
        )
    candidates = [
        _journal_candidate_from_mutation(item)
        for item in mutations
        if isinstance(item, Mapping)
    ]
    if len(candidates) != len(mutations):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "deployment transaction contains an invalid mutation",
        )
    timestamp = created_at or _utc_now()
    journal = {
        "kind": _JOURNAL_KIND,
        "schema_version": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
        "status": "open-before-first-mutation",
        "mother_binding": dict(mother_binding),
        "network": _identifier(network, "network"),
        "nodes": [_identifier(item, "node") for item in nodes],
        "staged_scope": "prepare-standby-service",
        "release_sha256": _sha256(release_sha256, "release SHA-256"),
        "transaction_sha256": _sha256(transaction_sha256, "transaction SHA-256"),
        "policy": {
            "write_before_remote_mutation": True,
            "mark_in_flight_before_http": True,
            "idempotent_rollback": True,
            "recovery_from_in_flight_by_exact_name": True,
            "secrets_in_output": False,
        },
        "candidates": candidates,
    }
    if _contains_sensitive_key(journal):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback journal contains a sensitive field",
        )
    return journal


def _write_or_replace_journal(
    paths: PrivateStatePaths,
    journal_path: Path,
    journal: Mapping[str, Any],
    *,
    operation: OperationIdentity,
    create: bool,
) -> tuple[Path, str]:
    payload = canonical_json(dict(journal))
    digest = hashlib.sha256(payload).hexdigest()
    directory = _root(paths, _JOURNAL_DIRECTORY)
    atomic_files.ensure_durable_directory(directory, operation=operation)
    _secure_private_path(directory, is_directory=True, operation=operation)
    destination = _beneath(paths, journal_path, _JOURNAL_DIRECTORY, label="rollback journal")
    if create:
        atomic_files.durable_create(destination, payload, operation=operation)
    else:
        atomic_files.durable_replace(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def deployment_rollback_journal_path(
    paths: PrivateStatePaths,
    release_sha256: str,
) -> Path:
    return _root(paths, _JOURNAL_DIRECTORY) / f"{_sha256(release_sha256, 'release SHA-256')}.json"


def write_deployment_rollback_journal(
    paths: PrivateStatePaths,
    journal: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    release_sha256 = _sha256(journal.get("release_sha256"), "release SHA-256")
    destination = deployment_rollback_journal_path(paths, release_sha256)
    return _write_or_replace_journal(
        paths,
        destination,
        journal,
        operation=operation,
        create=True,
    )


def _load_journal_for_update(
    paths: PrivateStatePaths,
    journal_path: Path,
) -> tuple[dict[str, Any], Path]:
    candidate = _beneath(paths, journal_path, _JOURNAL_DIRECTORY, label="rollback journal")
    journal, _, _ = _canonical_file(candidate, label="rollback journal")
    if journal.get("kind") != _JOURNAL_KIND:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback journal kind is unsupported",
        )
    return journal, candidate


def update_deployment_rollback_journal_candidate(
    paths: PrivateStatePaths,
    journal_path: Path,
    *,
    mutation_id: str,
    state: str,
    operation: OperationIdentity,
    created_uuid: str | None = None,
    updated_at: str | None = None,
) -> tuple[Path, str]:
    """Durably mark one candidate before or after its remote HTTP mutation."""

    allowed_states = {
        "in-flight",
        "succeeded",
        "recovered-succeeded",
        "proven-absent",
        "rollback-succeeded",
    }
    if state not in allowed_states:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"unsupported rollback journal candidate state {state!r}",
        )
    journal, candidate_path = _load_journal_for_update(paths, journal_path)
    target_id = _identifier(mutation_id, "mutation id")
    candidates = journal.get("candidates")
    if type(candidates) is not list:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback journal candidates are missing",
        )
    matches = [item for item in candidates if isinstance(item, Mapping) and item.get("mutation_id") == target_id]
    if len(matches) != 1:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"rollback journal does not contain exactly one mutation {target_id!r}",
        )
    timestamp = updated_at or _utc_now()
    rewritten: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                "rollback journal contains an invalid candidate",
            )
        value = dict(item)
        if value.get("mutation_id") == target_id:
            value["state"] = state
            if state == "in-flight":
                value["attempted_at"] = timestamp
            else:
                value["completed_at"] = timestamp
            if created_uuid is not None:
                value["created_uuid"] = _identifier(created_uuid, f"{target_id} created UUID")
        rewritten.append(value)
    journal = {
        **journal,
        "updated_at": timestamp,
        "status": "mutation-in-progress" if state == "in-flight" else journal.get("status"),
        "candidates": rewritten,
    }
    return _write_or_replace_journal(
        paths,
        candidate_path,
        journal,
        operation=operation,
        create=False,
    )


def update_deployment_rollback_journal_status(
    paths: PrivateStatePaths,
    journal_path: Path,
    *,
    status: str,
    operation: OperationIdentity,
    updated_at: str | None = None,
) -> tuple[Path, str]:
    allowed = {
        "applied-awaiting-next-phase",
        "rollback-required",
        "rolled-back",
    }
    if status not in allowed:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            f"unsupported rollback journal status {status!r}",
        )
    journal, candidate_path = _load_journal_for_update(paths, journal_path)
    journal = {**journal, "updated_at": updated_at or _utc_now(), "status": status}
    return _write_or_replace_journal(
        paths,
        candidate_path,
        journal,
        operation=operation,
        create=False,
    )


def inspect_deployment_rollback_journal(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    journal_path: Path,
    *,
    acknowledged_journal_sha256: str,
) -> dict[str, Any]:
    """Verify a crash-recovery journal without network access."""

    candidate = _beneath(paths, Path(journal_path), _JOURNAL_DIRECTORY, label="rollback journal")
    journal, _, journal_sha256 = _canonical_file(candidate, label="rollback journal")
    if journal_sha256 != _sha256(acknowledged_journal_sha256, "acknowledged journal SHA-256"):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact rollback journal SHA-256",
        )
    if journal.get("kind") != _JOURNAL_KIND or journal.get("staged_scope") != "prepare-standby-service":
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback journal kind or scope is unsupported",
        )
    if journal.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_MOTHER_BINDING_MISMATCH",
            "rollback journal is not bound to the current Mother generation",
        )
    network = _identifier(journal.get("network"), "journal network")
    nodes_value = journal.get("nodes")
    if type(nodes_value) is not list:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback journal node set is missing",
        )
    nodes = {_identifier(item, "journal node") for item in nodes_value}
    blockers = _successful_downstream_results(
        paths,
        network=network,
        nodes=nodes,
        mother_binding=journal["mother_binding"],
        boundary_at=journal.get("created_at"),
    )
    if blockers:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_BOUNDARY_CROSSED",
            "a successful downstream deployment phase exists at or after this rollback boundary: "
            + ", ".join(blockers[:4]),
        )
    candidates = journal.get("candidates")
    if type(candidates) is not list:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback journal candidates are missing",
        )
    counts: dict[str, int] = {}
    for item in candidates:
        if not isinstance(item, Mapping):
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                "rollback journal contains an invalid candidate",
            )
        state = str(item.get("state", ""))
        counts[state] = counts.get(state, 0) + 1
    return {
        "clean": True,
        "rollback_implemented": True,
        "journal_path": str(candidate),
        "journal_sha256": journal_sha256,
        "mother_binding": dict(journal["mother_binding"]),
        "network": network,
        "nodes": sorted(nodes),
        "staged_scope": journal["staged_scope"],
        "journal_status": journal.get("status"),
        "candidate_count": len(candidates),
        "candidate_states": counts,
        "downstream_success_blockers": [],
        "rollback_boundary_open": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "journal": journal,
    }


def _candidate_name_matches(item: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    if str(item.get("name", "")) != str(candidate.get("expected_name", "")):
        return False
    expected_description = str(candidate.get("expected_description", ""))
    if expected_description:
        return str(item.get("description", "")) == expected_description
    return True


def _resolve_journal_frame(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    inspected: Mapping[str, Any],
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    operation: OperationIdentity,
) -> tuple[dict[str, Any], Path, str]:
    journal = dict(inspected["journal"])
    journal_path = Path(inspected["journal_path"])
    network = _identifier(inspected["network"], "journal network")
    resolved: list[dict[str, Any]] = []
    candidates = journal.get("candidates")
    assert type(candidates) is list
    for raw in candidates:
        if not isinstance(raw, Mapping):
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                "rollback journal contains an invalid candidate",
            )
        candidate = dict(raw)
        state = str(candidate.get("state", ""))
        if state == "pending":
            continue
        created_uuid = candidate.get("created_uuid")
        if type(created_uuid) is str and created_uuid:
            resolved.append(candidate)
            continue
        if state not in {"in-flight", "rollback-succeeded", "proven-absent"}:
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                f"rollback journal candidate {candidate.get('mutation_id')!r} is unresolved",
            )
        if state in {"rollback-succeeded", "proven-absent"}:
            continue
        controller_id = _identifier(candidate.get("controller_id"), "journal controller")
        controller = resolve_coolify_controller(
            private_state,
            network,
            controller_id,
            require_enabled=True,
            require_token=True,
        )
        observe_endpoint = str(candidate.get("observe_endpoint", ""))
        observation = get_coolify_json(
            controller,
            observe_endpoint,
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        if observation.status != 200:
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_OBSERVATION_FAILED",
                f"Coolify GET {observe_endpoint!r} returned HTTP {observation.status}",
            )
        matches = [item for item in _items(observation.payload) if _candidate_name_matches(item, candidate)]
        unique: dict[str, Mapping[str, Any]] = {}
        for item in matches:
            value = item.get("uuid", item.get("id"))
            if type(value) is str and value:
                unique[value] = item
        if not unique:
            journal_path, journal_digest = update_deployment_rollback_journal_candidate(
                paths,
                journal_path,
                mutation_id=_identifier(candidate.get("mutation_id"), "mutation id"),
                state="proven-absent",
                operation=operation,
            )
            continue
        if len(unique) != 1:
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_OWNERSHIP_AMBIGUOUS",
                f"journal candidate {candidate.get('mutation_id')!r} does not resolve uniquely",
            )
        created_uuid = next(iter(unique))
        journal_path, journal_digest = update_deployment_rollback_journal_candidate(
            paths,
            journal_path,
            mutation_id=_identifier(candidate.get("mutation_id"), "mutation id"),
            state="recovered-succeeded",
            created_uuid=created_uuid,
            operation=operation,
        )
        candidate["created_uuid"] = created_uuid
        candidate["state"] = "recovered-succeeded"
        resolved.append(candidate)

    operations: list[dict[str, Any]] = []
    for candidate in resolved:
        created_uuid = _identifier(candidate.get("created_uuid"), "journal created UUID")
        resource_kind = str(candidate.get("resource_kind", ""))
        observe_endpoint = str(candidate.get("observe_endpoint", ""))
        if resource_kind == "service":
            delete_endpoint = f"/api/v1/services/{created_uuid}"
        elif resource_kind == "environment":
            delete_endpoint = f"{observe_endpoint}/{created_uuid}"
        else:
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                "rollback journal resource kind is unsupported",
            )
        operations.append(
            {
                "source_ordinal": candidate.get("source_ordinal"),
                "mutation_id": candidate.get("mutation_id"),
                "node": candidate.get("node"),
                "controller_id": candidate.get("controller_id"),
                "resource_kind": resource_kind,
                "created_uuid": created_uuid,
                "expected_name": candidate.get("expected_name"),
                "observe": {"method": "GET", "endpoint": observe_endpoint},
                "inverse": {"method": "DELETE", "endpoint": _validate_delete_endpoint(delete_endpoint)},
            }
        )
    operations.sort(key=lambda item: int(item["source_ordinal"]), reverse=True)
    for rollback_ordinal, item in enumerate(operations, start=1):
        item["rollback_ordinal"] = rollback_ordinal
    frame = {
        "kind": _FRAME_KIND,
        "schema_version": 1,
        "staged_scope": "prepare-standby-service",
        "rollback_boundary": "before-any-later-successful-deployment-phase",
        "idempotent": True,
        "operations": operations,
        "summary": {
            "operation_count": len(operations),
            "service_delete_count": sum(item["resource_kind"] == "service" for item in operations),
            "environment_delete_count": sum(item["resource_kind"] == "environment" for item in operations),
        },
    }
    _, _, latest_digest = _canonical_file(journal_path, label="rollback journal")
    return frame, journal_path, latest_digest


def execute_deployment_journal_rollback(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    journal_path: Path,
    *,
    acknowledged_journal_sha256: str,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    """Recover and roll back an interrupted first-step execution journal."""

    inspected = inspect_deployment_rollback_journal(
        paths,
        private_state,
        journal_path,
        acknowledged_journal_sha256=acknowledged_journal_sha256,
    )
    started_at = _utc_now()
    frame, current_journal_path, current_journal_sha256 = _resolve_journal_frame(
        paths,
        private_state,
        inspected,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        operation=operation,
    )
    outcome = execute_deployment_rollback_frame(
        private_state,
        network=inspected["network"],
        frame=frame,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    status = "rolled-back" if outcome["summary"]["complete"] else "rollback-required"
    current_journal_path, current_journal_sha256 = update_deployment_rollback_journal_status(
        paths,
        current_journal_path,
        status=status,
        operation=operation,
    )
    completed_at = _utc_now()
    result = {
        "kind": _ROLLBACK_RESULT_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": outcome["status"],
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "nodes": list(inspected["nodes"]),
        "staged_scope": inspected["staged_scope"],
        "journal": {
            "locator": current_journal_path.resolve(strict=False).relative_to(
                paths.root.resolve(strict=False)
            ).as_posix(),
            "sha256": current_journal_sha256,
        },
        "rollback_frame": frame,
        "authority": {
            "authorization_source": "explicit-operator-acknowledgement",
            "rollback_boundary_open": True,
            "idempotent_retry_allowed": True,
            "crash_recovery_path": True,
        },
        "policy": {
            "allowed_http_methods": ["GET", "DELETE"],
            "exact_name_recovery_required": True,
            "exact_created_uuid_required_before_delete": True,
            "post_delete_absence_required": True,
            "stop_on_first_failure": True,
            "secrets_in_output": False,
        },
        "rollback_receipts": outcome["rollback_receipts"],
        "failure": outcome["failure"],
        "summary": dict(outcome["summary"]),
    }
    if _contains_sensitive_key(result):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_RESULT_INVALID",
            "rollback result contains a sensitive field",
        )
    result_path, result_digest = _write_rollback_result(paths, result, operation=operation)
    return {
        **result,
        "result_artifact": {"path": str(result_path), "sha256": result_digest},
    }


def build_deployment_rollback_frame(
    transaction: Mapping[str, Any],
    receipts: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build exact inverse operations for successful first-step creates."""

    mutations_value = transaction.get("mutations")
    if type(mutations_value) is not list:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "deployment transaction mutation set is missing",
        )
    mutations: dict[str, Mapping[str, Any]] = {}
    for item in mutations_value:
        if not isinstance(item, Mapping):
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                "deployment transaction contains an invalid mutation",
            )
        mutation_id = _identifier(item.get("mutation_id"), "mutation id")
        mutations[mutation_id] = item

    inverse: list[dict[str, Any]] = []
    for receipt in receipts:
        if not isinstance(receipt, Mapping) or receipt.get("status") != "succeeded":
            continue
        mutation_id = _identifier(receipt.get("mutation_id"), "receipt mutation id")
        mutation = mutations.get(mutation_id)
        if mutation is None:
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                f"successful receipt {mutation_id!r} has no transaction mutation",
            )
        response = receipt.get("response")
        if not isinstance(response, Mapping):
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                f"successful receipt {mutation_id!r} has no response binding",
            )
        created_uuid = _identifier(response.get("bound_uuid"), f"{mutation_id} created UUID")
        body = mutation.get("canonical_request_body")
        if not isinstance(body, Mapping):
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                f"mutation {mutation_id!r} has no canonical request body",
            )
        controller_id = _identifier(mutation.get("controller_id"), f"{mutation_id} controller")
        node = _identifier(mutation.get("node"), f"{mutation_id} node")
        expected_name = _identifier(body.get("name"), f"{mutation_id} expected name")
        endpoint = str(mutation.get("endpoint", ""))
        ordinal = mutation.get("ordinal")
        if type(ordinal) is not int or ordinal < 1:
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                f"mutation {mutation_id!r} has no valid ordinal",
            )

        if endpoint == "/api/v1/services":
            resource_kind = "service"
            observe_endpoint = "/api/v1/services"
            delete_endpoint = f"/api/v1/services/{created_uuid}"
        elif endpoint.startswith("/api/v1/projects/") and endpoint.endswith("/environments"):
            parts = endpoint.split("/")
            if len(parts) != 6:
                raise MotherDeploymentRollbackError(
                    "MOTHER_DEPLOY_ROLLBACK_INVALID",
                    f"mutation {mutation_id!r} has an unsupported environment endpoint",
                )
            project_uuid = _identifier(parts[4], f"{mutation_id} project UUID")
            resource_kind = "environment"
            observe_endpoint = endpoint
            delete_endpoint = f"{endpoint}/{created_uuid}"
        else:
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_ENDPOINT_REJECTED",
                f"mutation {mutation_id!r} has no supported inverse operation",
            )

        inverse.append(
            {
                "source_ordinal": ordinal,
                "mutation_id": mutation_id,
                "node": node,
                "controller_id": controller_id,
                "resource_kind": resource_kind,
                "created_uuid": created_uuid,
                "expected_name": expected_name,
                "observe": {"method": "GET", "endpoint": observe_endpoint},
                "inverse": {"method": "DELETE", "endpoint": _validate_delete_endpoint(delete_endpoint)},
            }
        )

    inverse.sort(key=lambda item: int(item["source_ordinal"]), reverse=True)
    for rollback_ordinal, item in enumerate(inverse, start=1):
        item["rollback_ordinal"] = rollback_ordinal

    frame = {
        "kind": _FRAME_KIND,
        "schema_version": 1,
        "staged_scope": "prepare-standby-service",
        "rollback_boundary": "before-any-later-successful-deployment-phase",
        "idempotent": True,
        "operations": inverse,
        "summary": {
            "operation_count": len(inverse),
            "service_delete_count": sum(item["resource_kind"] == "service" for item in inverse),
            "environment_delete_count": sum(item["resource_kind"] == "environment" for item in inverse),
        },
    }
    if _contains_sensitive_key(frame):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback frame contains a sensitive field",
        )
    return frame


def _observe_exact_resource(
    private_state: PrivateStateReadResult,
    network: str,
    operation: Mapping[str, Any],
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> tuple[Any, list[Mapping[str, Any]]]:
    controller_id = _identifier(operation.get("controller_id"), "rollback controller")
    controller = resolve_coolify_controller(
        private_state,
        network,
        controller_id,
        require_enabled=True,
        require_token=True,
    )
    observe = operation.get("observe")
    if not isinstance(observe, Mapping) or observe.get("method") != "GET":
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback observation contract is invalid",
        )
    endpoint = str(observe.get("endpoint", ""))
    observation = get_coolify_json(
        controller,
        endpoint,
        authenticated=True,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if observation.status != 200:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_OBSERVATION_FAILED",
            f"Coolify GET {endpoint!r} returned HTTP {observation.status}",
        )
    created_uuid = _identifier(operation.get("created_uuid"), "rollback created UUID")
    return controller, _resource_matches(observation.payload, created_uuid)


def _wait_for_exact_resource_absence(
    private_state: PrivateStateReadResult,
    network: str,
    operation: Mapping[str, Any],
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    wait_seconds: float = _DEFAULT_POST_DELETE_WAIT_SECONDS,
    poll_seconds: float = _DEFAULT_POST_DELETE_POLL_SECONDS,
) -> dict[str, Any]:
    """Poll Coolify until one deleted resource disappears from list endpoints."""

    if type(wait_seconds) not in {int, float} or wait_seconds < 0:
        raise ValueError("wait_seconds must be non-negative")
    if type(poll_seconds) not in {int, float} or poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")

    maximum_attempts = max(1, int(float(wait_seconds) / float(poll_seconds)) + 1)
    started = time.monotonic()
    for attempt in range(1, maximum_attempts + 1):
        _, remaining = _observe_exact_resource(
            private_state,
            network,
            operation,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        if not remaining:
            return {
                "absent": True,
                "observation_attempts": attempt,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        if attempt < maximum_attempts:
            time.sleep(float(poll_seconds))

    created_uuid = _identifier(operation.get("created_uuid"), "rollback created UUID")
    elapsed_ms = int((time.monotonic() - started) * 1000)
    raise MotherDeploymentRollbackError(
        "MOTHER_DEPLOY_ROLLBACK_POSTCONDITION_FAILED",
        (
            f"created UUID {created_uuid!r} remains present after DELETE "
            f"and {maximum_attempts} bounded absence observations"
        ),
    )


def execute_deployment_rollback_frame(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    frame: Mapping[str, Any],
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
) -> dict[str, Any]:
    """Execute and post-verify one exact idempotent rollback frame."""

    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    network_id = _identifier(network, "network")
    if frame.get("kind") != _FRAME_KIND or frame.get("staged_scope") != "prepare-standby-service":
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback frame kind or scope is unsupported",
        )
    operations = frame.get("operations")
    if type(operations) is not list:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback frame operations are missing",
        )
    if type(timeout) not in {int, float} or timeout <= 0:
        raise ValueError("timeout must be positive")
    if type(max_response_bytes) is not int or max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be a positive integer")

    receipts: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    for item in operations:
        if not isinstance(item, Mapping):
            failure = {
                "code": "MOTHER_DEPLOY_ROLLBACK_INVALID",
                "message": "rollback frame contains an invalid operation",
            }
            break
        try:
            controller, matches = _observe_exact_resource(
                private_state,
                network_id,
                item,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            expected_name = _identifier(item.get("expected_name"), "rollback expected name")
            created_uuid = _identifier(item.get("created_uuid"), "rollback created UUID")
            if len(matches) > 1:
                raise MotherDeploymentRollbackError(
                    "MOTHER_DEPLOY_ROLLBACK_OWNERSHIP_AMBIGUOUS",
                    f"multiple live resources match created UUID {created_uuid!r}",
                )
            if not matches:
                receipts.append(
                    {
                        "rollback_ordinal": item.get("rollback_ordinal"),
                        "mutation_id": item.get("mutation_id"),
                        "node": item.get("node"),
                        "controller_id": item.get("controller_id"),
                        "resource_kind": item.get("resource_kind"),
                        "created_uuid": created_uuid,
                        "status": "already-absent",
                        "delete_performed": False,
                        "postcondition": "absent",
                    }
                )
                continue
            live_name = str(matches[0].get("name", ""))
            if live_name != expected_name:
                raise MotherDeploymentRollbackError(
                    "MOTHER_DEPLOY_ROLLBACK_OWNERSHIP_MISMATCH",
                    f"created UUID {created_uuid!r} now has unexpected name {live_name!r}",
                )
            inverse = item.get("inverse")
            if not isinstance(inverse, Mapping) or inverse.get("method") != "DELETE":
                raise MotherDeploymentRollbackError(
                    "MOTHER_DEPLOY_ROLLBACK_INVALID",
                    "rollback inverse request is invalid",
                )
            endpoint = _validate_delete_endpoint(inverse.get("endpoint"))
            response = _http_delete(
                controller,
                endpoint,
                timeout=float(timeout),
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            if response["status"] not in {200, 202, 204, 404}:
                raise MotherDeploymentRollbackError(
                    "MOTHER_DEPLOY_ROLLBACK_HTTP_STATUS_REJECTED",
                    f"rollback DELETE returned HTTP {response['status']}",
                )
            absence = _wait_for_exact_resource_absence(
                private_state,
                network_id,
                item,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            receipts.append(
                {
                    "rollback_ordinal": item.get("rollback_ordinal"),
                    "mutation_id": item.get("mutation_id"),
                    "node": item.get("node"),
                    "controller_id": item.get("controller_id"),
                    "resource_kind": item.get("resource_kind"),
                    "created_uuid": created_uuid,
                    "status": "succeeded",
                    "delete_performed": response["status"] != 404,
                    "delete_response": response,
                    "postcondition": "absent",
                    "postcondition_observation_attempts": absence["observation_attempts"],
                    "postcondition_wait_ms": absence["elapsed_ms"],
                }
            )
        except MotherDeploymentRollbackError as exc:
            failure = {"code": exc.code, "message": _safe_message(exc)}
            break
        except Exception as exc:  # Preserve a bounded, secret-free failure.
            failure = {
                "code": "MOTHER_DEPLOY_ROLLBACK_UNEXPECTED_FAILURE",
                "message": _safe_message(exc),
            }
            break

    complete = failure is None and len(receipts) == len(operations)
    return {
        "status": "pass" if complete else "failed",
        "network": network_id,
        "staged_scope": "prepare-standby-service",
        "rollback_receipts": receipts,
        "failure": failure,
        "summary": {
            "planned_operation_count": len(operations),
            "attempted_operation_count": len(receipts) + (1 if failure is not None else 0),
            "succeeded_operation_count": sum(
                item["status"] in {"succeeded", "already-absent"} for item in receipts
            ),
            "delete_performed_count": sum(item.get("delete_performed") is True for item in receipts),
            "complete": complete,
            "postconditions_verified": complete,
        },
    }


def _successful_downstream_results(
    paths: PrivateStatePaths,
    *,
    network: str,
    nodes: set[str],
    mother_binding: Mapping[str, Any],
    boundary_at: str,
) -> list[str]:
    """Return successful downstream results that can actually follow this step.

    Historical deployment artifacts are durable by design.  A prior successful
    identity/genesis/admission execution for the same logical node names must
    not close a new first-step rollback boundary.  Only results bound to the
    same Mother generation and completed at or after this execution/journal
    boundary are relevant.
    """

    blockers: list[str] = []
    boundary = _parse_utc(boundary_at, "rollback boundary timestamp")
    actions_root = paths.root / "actions"
    for directory_name in _DOWNSTREAM_RESULT_DIRECTORIES:
        directory = actions_root / directory_name
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.glob("*.json")):
            locator = candidate.resolve(strict=False).relative_to(
                paths.root.resolve(strict=False)
            ).as_posix()
            try:
                value, _, _ = _canonical_file(candidate, label="downstream execution result")
            except MotherDeploymentRollbackError:
                blockers.append(locator)
                continue
            result_nodes = value.get("nodes")
            if type(result_nodes) is not list:
                continue
            normalized = {str(item) for item in result_nodes if type(item) is str}
            if not (
                value.get("status") == "pass"
                and value.get("network") == network
                and normalized.intersection(nodes)
                and value.get("mother_binding") == dict(mother_binding)
            ):
                continue
            try:
                completed_at = _parse_utc(
                    value.get("completed_at"),
                    f"{locator} completed_at",
                )
            except MotherDeploymentRollbackError:
                blockers.append(locator)
                continue
            if completed_at >= boundary:
                blockers.append(locator)
    return blockers


def inspect_deployment_mutation_rollback(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    acknowledged_execution_sha256: str,
) -> dict[str, Any]:
    """Verify rollback authority and frame without network access."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    candidate = _beneath(paths, Path(execution_path), _EXECUTION_DIRECTORY, label="execution result")
    execution, _, execution_sha256 = _canonical_file(candidate, label="execution result")
    acknowledged = _sha256(acknowledged_execution_sha256, "acknowledged execution SHA-256")
    if acknowledged != execution_sha256:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact execution result SHA-256",
        )
    if execution.get("kind") != _EXECUTION_KIND:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "execution result kind is unsupported",
        )
    if execution.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_MOTHER_BINDING_MISMATCH",
            "execution result is not bound to the current Mother generation",
        )
    if execution.get("staged_scope") != "prepare-standby-service":
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_SCOPE_REJECTED",
            "only the first standby-service deployment step is rollbackable here",
        )
    rollback = execution.get("rollback")
    if not isinstance(rollback, Mapping):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_FRAME_MISSING",
            "execution result has no executable rollback frame",
        )
    frame = rollback.get("frame")
    if not isinstance(frame, Mapping) or frame.get("kind") != _FRAME_KIND:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_FRAME_MISSING",
            "execution result has no valid rollback frame",
        )
    operations = frame.get("operations")
    if type(operations) is not list:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback frame operations are missing",
        )
    network = _identifier(execution.get("network"), "execution network")
    nodes_value = execution.get("nodes")
    if type(nodes_value) is not list:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "execution node set is missing",
        )
    nodes = {_identifier(item, "execution node") for item in nodes_value}
    blockers = _successful_downstream_results(
        paths,
        network=network,
        nodes=nodes,
        mother_binding=execution["mother_binding"],
        boundary_at=execution.get("completed_at"),
    )
    if blockers:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_BOUNDARY_CROSSED",
            "a successful downstream deployment phase exists at or after this rollback boundary: "
            + ", ".join(blockers[:4]),
        )
    automatic = rollback.get("automatic_attempt")
    automatic_complete = isinstance(automatic, Mapping) and automatic.get("summary", {}).get("complete") is True
    return {
        "clean": True,
        "rollback_implemented": True,
        "execution_path": str(candidate),
        "execution_sha256": execution_sha256,
        "mother_binding": dict(execution["mother_binding"]),
        "network": network,
        "nodes": sorted(nodes),
        "staged_scope": execution["staged_scope"],
        "rollback_operation_count": len(operations),
        "automatic_rollback_complete": automatic_complete,
        "downstream_success_blockers": [],
        "rollback_boundary_open": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "frame": dict(frame),
    }


def _write_rollback_result(
    paths: PrivateStatePaths,
    result: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    payload = canonical_json(dict(result))
    digest = hashlib.sha256(payload).hexdigest()
    directory = _root(paths, _ROLLBACK_DIRECTORY)
    atomic_files.ensure_durable_directory(directory, operation=operation)
    _secure_private_path(directory, is_directory=True, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(result.get("completed_at", "")))[:32] or "rollback"
    op_id = _identifier(operation.operation_id, "operation id")
    destination = directory / f"{stamp}-{op_id}-{digest[:16]}.json"
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_deployment_mutation_rollback(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    acknowledged_execution_sha256: str,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    """Execute and persist the explicit rollback of the first live step."""

    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    inspected = inspect_deployment_mutation_rollback(
        paths,
        private_state,
        execution_path,
        acknowledged_execution_sha256=acknowledged_execution_sha256,
    )
    started_at = _utc_now()
    outcome = execute_deployment_rollback_frame(
        private_state,
        network=inspected["network"],
        frame=inspected["frame"],
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    completed_at = _utc_now()
    result = {
        "kind": _ROLLBACK_RESULT_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": outcome["status"],
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "nodes": list(inspected["nodes"]),
        "staged_scope": inspected["staged_scope"],
        "execution": {
            "locator": Path(inspected["execution_path"]).resolve(strict=False).relative_to(
                paths.root.resolve(strict=False)
            ).as_posix(),
            "sha256": inspected["execution_sha256"],
        },
        "rollback_frame": dict(inspected["frame"]),
        "authority": {
            "authorization_source": "explicit-operator-acknowledgement",
            "rollback_boundary_open": True,
            "idempotent_retry_allowed": True,
        },
        "policy": {
            "allowed_http_methods": ["GET", "DELETE"],
            "exact_created_uuid_required": True,
            "exact_expected_name_required": True,
            "post_delete_absence_required": True,
            "stop_on_first_failure": True,
            "secrets_in_output": False,
        },
        "rollback_receipts": outcome["rollback_receipts"],
        "failure": outcome["failure"],
        "summary": dict(outcome["summary"]),
    }
    if _contains_sensitive_key(result):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_RESULT_INVALID",
            "rollback result contains a sensitive field",
        )
    result_path, result_digest = _write_rollback_result(paths, result, operation=operation)
    return {
        **result,
        "result_artifact": {"path": str(result_path), "sha256": result_digest},
    }


def verify_deployment_mutation_rollback(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    rollback_result_path: Path,
    *,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
) -> dict[str, Any]:
    """Re-observe every rolled-back UUID and prove that all remain absent."""

    candidate = _beneath(paths, Path(rollback_result_path), _ROLLBACK_DIRECTORY, label="rollback result")
    result, _, result_sha256 = _canonical_file(candidate, label="rollback result")
    if result.get("kind") != _ROLLBACK_RESULT_KIND:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback result kind is unsupported",
        )
    if result.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_MOTHER_BINDING_MISMATCH",
            "rollback result is not bound to the current Mother generation",
        )
    receipts = result.get("rollback_receipts")
    if type(receipts) is not list:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_INVALID",
            "rollback result receipts are missing",
        )
    frame = result.get("rollback_frame")
    if not isinstance(frame, Mapping):
        execution_binding = result.get("execution")
        if not isinstance(execution_binding, Mapping):
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                "rollback result source binding is missing",
            )
        execution_locator = execution_binding.get("locator")
        if type(execution_locator) is not str:
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                "rollback result execution locator is missing",
            )
        execution_path = _beneath(
            paths,
            paths.root / execution_locator,
            _EXECUTION_DIRECTORY,
            label="execution result",
        )
        execution, _, execution_sha256 = _canonical_file(execution_path, label="execution result")
        if execution_sha256 != execution_binding.get("sha256"):
            raise MotherDeploymentRollbackError(
                "MOTHER_DEPLOY_ROLLBACK_INVALID",
                "bound execution result digest changed",
            )
        rollback = execution.get("rollback")
        frame = rollback.get("frame") if isinstance(rollback, Mapping) else None
    operations = frame.get("operations") if isinstance(frame, Mapping) else None
    if type(operations) is not list:
        raise MotherDeploymentRollbackError(
            "MOTHER_DEPLOY_ROLLBACK_FRAME_MISSING",
            "bound execution result has no rollback frame",
        )

    checks: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    for item in operations:
        if not isinstance(item, Mapping):
            failure = {
                "code": "MOTHER_DEPLOY_ROLLBACK_INVALID",
                "message": "rollback frame contains an invalid operation",
            }
            break
        try:
            _, matches = _observe_exact_resource(
                private_state,
                _identifier(result.get("network"), "rollback network"),
                item,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            clean = not matches
            checks.append(
                {
                    "rollback_ordinal": item.get("rollback_ordinal"),
                    "mutation_id": item.get("mutation_id"),
                    "node": item.get("node"),
                    "controller_id": item.get("controller_id"),
                    "resource_kind": item.get("resource_kind"),
                    "created_uuid": item.get("created_uuid"),
                    "status": "absent" if clean else "present",
                    "clean": clean,
                }
            )
            if not clean:
                failure = {
                    "code": "MOTHER_DEPLOY_ROLLBACK_POSTCONDITION_FAILED",
                    "message": f"created UUID {item.get('created_uuid')!r} is present",
                }
                break
        except MotherDeploymentRollbackError as exc:
            failure = {"code": exc.code, "message": _safe_message(exc)}
            break

    clean = failure is None and len(checks) == len(operations)
    return {
        "kind": "main_computer.mother.deployment_rollback_verification.v1",
        "schema_version": 1,
        "observed_at": _utc_now(),
        "clean": clean,
        "mother_binding": _binding(private_state),
        "network": result.get("network"),
        "nodes": result.get("nodes"),
        "rollback_result": {
            "path": str(candidate),
            "sha256": result_sha256,
        },
        "checks": checks,
        "failure": failure,
        "summary": {
            "planned_check_count": len(operations),
            "completed_check_count": len(checks),
            "absent_count": sum(item["clean"] is True for item in checks),
            "clean": clean,
            "network_access_performed": True,
            "live_mutation_performed": False,
        },
    }


__all__ = [
    "MotherDeploymentRollbackError",
    "build_deployment_rollback_frame",
    "build_deployment_rollback_journal",
    "deployment_rollback_journal_path",
    "execute_deployment_journal_rollback",
    "execute_deployment_rollback_frame",
    "execute_deployment_mutation_rollback",
    "inspect_deployment_mutation_rollback",
    "inspect_deployment_rollback_journal",
    "update_deployment_rollback_journal_candidate",
    "update_deployment_rollback_journal_status",
    "verify_deployment_mutation_rollback",
    "write_deployment_rollback_journal",
]
