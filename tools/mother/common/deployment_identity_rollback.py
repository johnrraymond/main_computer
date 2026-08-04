"""Rollback and crash recovery for reserved-identity installation.

The module persists a secret-free rollback journal before the first identity
environment-variable POST.  It records only commitments and Coolify UUIDs,
deletes exact created variables in reverse order, and independently proves the
pre-operation state (all targeted keys absent).
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


_JOURNAL_KIND = "main_computer.mother.deployment_identity_rollback_journal.v1"
_FRAME_KIND = "main_computer.mother.deployment_identity_rollback_frame.v1"
_RESULT_KIND = "main_computer.mother.deployment_identity_rollback_result.v1"
_VERIFICATION_KIND = "main_computer.mother.deployment_identity_rollback_verification.v1"
_EXECUTION_KIND = "main_computer.mother.deployment_identity_execution_result.v1"
_JOURNAL_DIRECTORY = ("actions", "deployment-identity-rollback-journals")
_RESULT_DIRECTORY = ("actions", "deployment-identity-rollbacks")
_VERIFICATION_DIRECTORY = ("evidence", "deployment-identity-rollbacks")
_EXECUTION_DIRECTORY = ("actions", "deployment-identity-executions")
_GENESIS_EXECUTION_DIRECTORY = ("actions", "deployment-genesis-executions")
_DEFAULT_POST_DELETE_WAIT_SECONDS = 15.0
_DEFAULT_POST_DELETE_POLL_SECONDS = 0.5
_SERVICE_ENVS_RE = re.compile(r"/api/v1/services/([A-Za-z0-9._-]+)/envs\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class MotherDeploymentIdentityRollbackError(RuntimeError):
    """An identity rollback could not be proven or executed safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            f"{path} must be a non-empty string",
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            f"{path} is not a safe identifier",
        )
    return text


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            f"{path} must be a lowercase SHA-256 digest",
        )
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            f"{path} must be a UTC timestamp",
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            f"{path} is malformed",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            f"{path} must be UTC",
        )
    return parsed.astimezone(timezone.utc)


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
        "value",
        "real_value",
        "literal_value",
    }
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in forbidden or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _safe_message(value: Any) -> str:
    text = str(value)
    if "Bearer " in text or "0x" in text or "|" in text:
        return "remote request failed"
    return text[:512]


def _root(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return paths.root / parts[0] / parts[1]


def _ensure_directory(
    paths: PrivateStatePaths,
    parts: tuple[str, str],
    *,
    operation: OperationIdentity,
) -> Path:
    current = paths.root
    for part in parts:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _beneath(paths: PrivateStatePaths, path: Path, parts: tuple[str, str], *, label: str) -> Path:
    root = _root(paths, parts).resolve(strict=False)
    candidate = Path(path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_PATH_UNSAFE",
            f"{label} is outside the canonical Mother directory",
        ) from exc
    return candidate


def _canonical_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            f"{label} could not be read as canonical JSON",
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            f"{label} is not canonical JSON",
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _items(payload: Any) -> list[Mapping[str, Any]]:
    if type(payload) is list:
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("envs", "environment_variables", "variables", "data"):
            value = payload.get(key)
            if type(value) is list:
                return [item for item in value if isinstance(item, Mapping)]
        if any(key in payload for key in ("uuid", "id", "key", "name")):
            return [payload]
    return []


def _env_key(item: Mapping[str, Any]) -> str:
    for field in ("key", "name", "variable"):
        value = item.get(field)
        if type(value) is str and value.strip():
            return value.strip()
    return ""


def _env_uuid(item: Mapping[str, Any]) -> str | None:
    for field in ("uuid", "id"):
        value = item.get(field)
        if type(value) is str and value.strip():
            return _identifier(value, f"environment variable {field}")
    return None


def _visible_value(item: Mapping[str, Any]) -> str | None:
    for field in ("value", "real_value", "literal_value"):
        value = item.get(field)
        if type(value) is str and value:
            if set(value) <= {"*", "•"} or value.lower() in {"<redacted>", "redacted", "masked"}:
                continue
            return value
    return None


def _transaction_profile(
    transaction: Mapping[str, Any],
    *,
    mother_binding: Mapping[str, Any],
    network: str,
    nodes: Iterable[str],
) -> tuple[str, list[dict[str, Any]]]:
    mutations = transaction.get("mutations")
    if type(mutations) is not list or not mutations:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity transaction mutation set is missing",
        )
    entries: list[dict[str, Any]] = []
    for expected_ordinal, raw in enumerate(mutations, start=1):
        if not isinstance(raw, Mapping) or raw.get("ordinal") != expected_ordinal:
            raise MotherDeploymentIdentityRollbackError(
                "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
                "identity transaction mutation ordering is invalid",
            )
        endpoint = raw.get("endpoint")
        match = _SERVICE_ENVS_RE.fullmatch(str(endpoint))
        template = raw.get("canonical_request_body_template")
        if match is None or not isinstance(template, Mapping):
            raise MotherDeploymentIdentityRollbackError(
                "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
                "identity mutation has no supported service environment endpoint",
            )
        entries.append(
            {
                "ordinal": expected_ordinal,
                "mutation_id": _identifier(raw.get("mutation_id"), "mutation id"),
                "node": _identifier(raw.get("node"), "mutation node"),
                "controller_id": _identifier(raw.get("controller_id"), "controller id"),
                "service_uuid": _identifier(match.group(1), "service UUID"),
                "environment_key": _identifier(template.get("key"), "environment key"),
                "value_sha256": _sha256(raw.get("value_sha256"), "value SHA-256"),
                "materialized_body_sha256": _sha256(
                    raw.get("materialized_body_sha256"),
                    "materialized body SHA-256",
                ),
            }
        )
    profile = {
        "mother_binding": dict(mother_binding),
        "network": _identifier(network, "network"),
        "nodes": [_identifier(item, "node") for item in nodes],
        "entries": entries,
    }
    return hashlib.sha256(canonical_json(profile)).hexdigest(), entries


def identity_profile_sha256(
    transaction: Mapping[str, Any],
    *,
    mother_binding: Mapping[str, Any],
    network: str,
    nodes: Iterable[str],
) -> str:
    return _transaction_profile(
        transaction,
        mother_binding=mother_binding,
        network=network,
        nodes=nodes,
    )[0]


def identity_rollback_journal_path(paths: PrivateStatePaths, release_sha256: str) -> Path:
    return _root(paths, _JOURNAL_DIRECTORY) / f"{_sha256(release_sha256, 'release SHA-256')}.json"


def build_identity_rollback_journal(
    transaction: Mapping[str, Any],
    *,
    mother_binding: Mapping[str, Any],
    network: str,
    nodes: Iterable[str],
    release_locator: str,
    release_sha256: str,
    transaction_locator: str,
    transaction_sha256: str,
    operation_id: str,
) -> dict[str, Any]:
    node_list = [_identifier(item, "node") for item in nodes]
    profile_sha256, entries = _transaction_profile(
        transaction,
        mother_binding=mother_binding,
        network=network,
        nodes=node_list,
    )
    candidates = [
        {
            **entry,
            "observe_endpoint": f"/api/v1/services/{entry['service_uuid']}/envs",
            "state": "prepared",
            "environment_variable_uuid": None,
        }
        for entry in entries
    ]
    journal = {
        "kind": _JOURNAL_KIND,
        "schema_version": 1,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "status": "prepared",
        "mother_binding": dict(mother_binding),
        "network": _identifier(network, "network"),
        "nodes": node_list,
        "staged_scope": "install-reserved-identity",
        "identity_profile_sha256": profile_sha256,
        "release": {
            "locator": release_locator,
            "sha256": _sha256(release_sha256, "release SHA-256"),
        },
        "identity_transaction": {
            "locator": transaction_locator,
            "sha256": _sha256(transaction_sha256, "transaction SHA-256"),
        },
        "operation_id": _identifier(operation_id, "operation id"),
        "candidates": candidates,
        "summary": {
            "candidate_count": len(candidates),
            "prepared_count": len(candidates),
        },
    }
    if _contains_sensitive_key(journal):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback journal contains a sensitive field",
        )
    return journal


def _write_document(
    paths: PrivateStatePaths,
    document: Mapping[str, Any],
    *,
    directory: tuple[str, str],
    destination: Path,
    operation: OperationIdentity,
    replace: bool,
) -> tuple[Path, str]:
    payload = canonical_json(dict(document))
    digest = hashlib.sha256(payload).hexdigest()
    _ensure_directory(paths, directory, operation=operation)
    if replace:
        atomic_files.durable_replace(destination, payload, operation=operation)
    else:
        if destination.exists():
            if destination.read_bytes() != payload:
                raise MotherDeploymentIdentityRollbackError(
                    "MOTHER_DEPLOY_IDENTITY_ROLLBACK_CONFLICT",
                    f"{destination.name} already contains different bytes",
                )
        else:
            atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def write_identity_rollback_journal(
    paths: PrivateStatePaths,
    journal: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if journal.get("kind") != _JOURNAL_KIND or _contains_sensitive_key(journal):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback journal is malformed or sensitive",
        )
    release = journal.get("release")
    if not isinstance(release, Mapping):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback journal release binding is missing",
        )
    destination = identity_rollback_journal_path(paths, release.get("sha256"))
    return _write_document(
        paths,
        journal,
        directory=_JOURNAL_DIRECTORY,
        destination=destination,
        operation=operation,
        replace=False,
    )


def _update_journal(
    paths: PrivateStatePaths,
    journal_path: Path,
    *,
    mutation_id: str | None = None,
    state: str | None = None,
    environment_variable_uuid: str | None = None,
    status: str | None = None,
    operation: OperationIdentity,
) -> tuple[Path, str, dict[str, Any]]:
    candidate = _beneath(paths, journal_path, _JOURNAL_DIRECTORY, label="identity rollback journal")
    journal, _, _ = _canonical_file(candidate, label="identity rollback journal")
    if journal.get("kind") != _JOURNAL_KIND:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback journal kind is invalid",
        )
    if mutation_id is not None:
        found = False
        candidates = journal.get("candidates")
        if type(candidates) is not list:
            raise MotherDeploymentIdentityRollbackError(
                "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
                "identity rollback journal candidates are missing",
            )
        for item in candidates:
            if isinstance(item, dict) and item.get("mutation_id") == mutation_id:
                found = True
                if state is not None:
                    item["state"] = state
                if environment_variable_uuid is not None:
                    item["environment_variable_uuid"] = _identifier(
                        environment_variable_uuid,
                        "environment variable UUID",
                    )
                break
        if not found:
            raise MotherDeploymentIdentityRollbackError(
                "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
                f"identity rollback candidate {mutation_id!r} is missing",
            )
    if status is not None:
        journal["status"] = status
    journal["updated_at"] = _utc_now()
    candidates = journal.get("candidates", [])
    journal["summary"] = {
        "candidate_count": len(candidates),
        "prepared_count": sum(isinstance(item, Mapping) and item.get("state") == "prepared" for item in candidates),
        "in_flight_count": sum(isinstance(item, Mapping) and item.get("state") == "in-flight" for item in candidates),
        "created_count": sum(
            isinstance(item, Mapping) and item.get("state") in {"created", "verified-created"}
            for item in candidates
        ),
        "absent_count": sum(
            isinstance(item, Mapping) and item.get("state") in {"proven-absent", "rollback-succeeded"}
            for item in candidates
        ),
    }
    path, digest = _write_document(
        paths,
        journal,
        directory=_JOURNAL_DIRECTORY,
        destination=candidate,
        operation=operation,
        replace=True,
    )
    return path, digest, journal


def update_identity_rollback_journal_candidate(
    paths: PrivateStatePaths,
    journal_path: Path,
    *,
    mutation_id: str,
    state: str,
    environment_variable_uuid: str | None = None,
    operation: OperationIdentity,
) -> tuple[Path, str, dict[str, Any]]:
    return _update_journal(
        paths,
        journal_path,
        mutation_id=_identifier(mutation_id, "mutation id"),
        state=_identifier(state, "journal state"),
        environment_variable_uuid=environment_variable_uuid,
        operation=operation,
    )


def update_identity_rollback_journal_status(
    paths: PrivateStatePaths,
    journal_path: Path,
    *,
    status: str,
    operation: OperationIdentity,
) -> tuple[Path, str, dict[str, Any]]:
    return _update_journal(
        paths,
        journal_path,
        status=_identifier(status, "journal status"),
        operation=operation,
    )


def inspect_identity_rollback_journal(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    journal_path: Path,
    *,
    acknowledged_journal_sha256: str,
) -> dict[str, Any]:
    candidate = _beneath(paths, journal_path, _JOURNAL_DIRECTORY, label="identity rollback journal")
    journal, _, digest = _canonical_file(candidate, label="identity rollback journal")
    if digest != _sha256(acknowledged_journal_sha256, "acknowledged journal SHA-256"):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact identity rollback journal",
        )
    if (
        journal.get("kind") != _JOURNAL_KIND
        or journal.get("mother_binding") != _binding(private_state)
        or journal.get("staged_scope") != "install-reserved-identity"
        or _contains_sensitive_key(journal)
    ):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback journal is malformed, stale, or sensitive",
        )
    return {
        "clean": True,
        "journal_path": str(candidate),
        "journal_sha256": digest,
        "mother_binding": dict(journal["mother_binding"]),
        "network": journal["network"],
        "nodes": list(journal["nodes"]),
        "identity_profile_sha256": journal["identity_profile_sha256"],
        "staged_scope": journal["staged_scope"],
        "status": journal["status"],
        "candidate_count": len(journal.get("candidates", [])),
        "rollback_implemented": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
    }


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    if hasattr(opener, "open"):
        return opener.open(request, timeout=timeout)
    return opener(request, timeout=timeout)


def _http_json(
    controller: Any,
    method: str,
    endpoint: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    request = urllib.request.Request(
        controller.base_url + endpoint,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {controller.api_token}",
            "User-Agent": "main-computer-mother-identity-rollback/1",
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
    except urllib.error.URLError as exc:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_REQUEST_FAILED",
            f"Coolify request failed: {_safe_message(exc.reason)}",
        ) from exc
    except OSError as exc:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_REQUEST_FAILED",
            "Coolify request failed",
        ) from exc
    if len(raw) > max_response_bytes:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_RESPONSE_TOO_LARGE",
            f"Coolify response exceeded {max_response_bytes} bytes",
        )
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


def _observe_candidate(
    private_state: PrivateStateReadResult,
    network: str,
    candidate: Mapping[str, Any],
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> tuple[Any, list[Mapping[str, Any]]]:
    controller = resolve_coolify_controller(
        private_state,
        network,
        _identifier(candidate.get("controller_id"), "controller id"),
        require_enabled=True,
        require_token=True,
    )
    endpoint = str(candidate.get("observe_endpoint", ""))
    observation = get_coolify_json(
        controller,
        endpoint,
        authenticated=True,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if observation.status != 200:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_OBSERVATION_FAILED",
            f"Coolify GET {endpoint!r} returned HTTP {observation.status}",
        )
    return controller, _items(observation.payload)


def _resolve_journal_frame(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    journal_path: Path,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    operation: OperationIdentity,
) -> tuple[dict[str, Any], Path, str]:
    candidate_path = _beneath(paths, journal_path, _JOURNAL_DIRECTORY, label="identity rollback journal")
    journal, _, journal_digest = _canonical_file(candidate_path, label="identity rollback journal")
    if journal.get("kind") != _JOURNAL_KIND or journal.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback journal is stale or malformed",
        )
    network = _identifier(journal.get("network"), "network")
    candidates = journal.get("candidates")
    if type(candidates) is not list:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback journal candidates are missing",
        )
    operations: list[dict[str, Any]] = []
    for raw in candidates:
        if not isinstance(raw, Mapping):
            raise MotherDeploymentIdentityRollbackError(
                "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
                "identity rollback journal has an invalid candidate",
            )
        item = dict(raw)
        state = item.get("state")
        if state in {"prepared", "post-rejected", "proven-absent", "rollback-succeeded"}:
            continue
        if state not in {"in-flight", "created", "verified-created"}:
            raise MotherDeploymentIdentityRollbackError(
                "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
                f"identity rollback candidate {item.get('mutation_id')!r} is unresolved",
            )
        _, envs = _observe_candidate(
            private_state,
            network,
            item,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        expected_key = _identifier(item.get("environment_key"), "environment key")
        env_uuid = item.get("environment_variable_uuid")
        matches: list[Mapping[str, Any]]
        if type(env_uuid) is str and env_uuid:
            exact_uuid = _identifier(env_uuid, "environment variable UUID")
            matches = [env for env in envs if _env_uuid(env) == exact_uuid]
            if matches and any(_env_key(env) != expected_key for env in matches):
                raise MotherDeploymentIdentityRollbackError(
                    "MOTHER_DEPLOY_IDENTITY_ROLLBACK_OWNERSHIP_MISMATCH",
                    f"environment UUID {exact_uuid!r} no longer belongs to {expected_key!r}",
                )
        else:
            matches = [env for env in envs if _env_key(env) == expected_key]
        if not matches:
            candidate_path, journal_digest, _ = update_identity_rollback_journal_candidate(
                paths,
                candidate_path,
                mutation_id=_identifier(item.get("mutation_id"), "mutation id"),
                state="proven-absent",
                operation=operation,
            )
            continue
        if len(matches) != 1:
            raise MotherDeploymentIdentityRollbackError(
                "MOTHER_DEPLOY_IDENTITY_ROLLBACK_OWNERSHIP_AMBIGUOUS",
                f"identity rollback candidate {item.get('mutation_id')!r} does not resolve uniquely",
            )
        match = matches[0]
        resolved_uuid = _env_uuid(match)
        if resolved_uuid is None:
            raise MotherDeploymentIdentityRollbackError(
                "MOTHER_DEPLOY_IDENTITY_ROLLBACK_OWNERSHIP_AMBIGUOUS",
                "Coolify did not return an environment-variable UUID",
            )
        if env_uuid is None:
            visible = _visible_value(match)
            if visible is None or hashlib.sha256(visible.encode("utf-8")).hexdigest() != item.get("value_sha256"):
                raise MotherDeploymentIdentityRollbackError(
                    "MOTHER_DEPLOY_IDENTITY_ROLLBACK_OWNERSHIP_UNPROVEN",
                    f"in-flight environment key {expected_key!r} cannot be attributed to this operation",
                )
            candidate_path, journal_digest, _ = update_identity_rollback_journal_candidate(
                paths,
                candidate_path,
                mutation_id=_identifier(item.get("mutation_id"), "mutation id"),
                state="created",
                environment_variable_uuid=resolved_uuid,
                operation=operation,
            )
        operations.append(
            {
                "source_ordinal": item["ordinal"],
                "mutation_id": item["mutation_id"],
                "node": item["node"],
                "controller_id": item["controller_id"],
                "service_uuid": item["service_uuid"],
                "environment_key": expected_key,
                "environment_variable_uuid": resolved_uuid,
                "observe": {"method": "GET", "endpoint": item["observe_endpoint"]},
                "inverse": {
                    "method": "DELETE",
                    "endpoint": f"{item['observe_endpoint']}/{resolved_uuid}",
                },
            }
        )
    operations.sort(key=lambda item: int(item["source_ordinal"]), reverse=True)
    for rollback_ordinal, item in enumerate(operations, start=1):
        item["rollback_ordinal"] = rollback_ordinal
    frame = {
        "kind": _FRAME_KIND,
        "schema_version": 1,
        "staged_scope": "install-reserved-identity",
        "rollback_boundary": "before-any-later-successful-identity-or-genesis-phase",
        "idempotent": True,
        "identity_profile_sha256": journal["identity_profile_sha256"],
        "operations": operations,
        "summary": {
            "operation_count": len(operations),
            "environment_variable_delete_count": len(operations),
        },
    }
    return frame, candidate_path, journal_digest


def execute_identity_rollback_frame(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    frame: Mapping[str, Any],
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    wait_seconds: float = _DEFAULT_POST_DELETE_WAIT_SECONDS,
    poll_seconds: float = _DEFAULT_POST_DELETE_POLL_SECONDS,
) -> dict[str, Any]:
    if frame.get("kind") != _FRAME_KIND or frame.get("staged_scope") != "install-reserved-identity":
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback frame kind or scope is unsupported",
        )
    operations = frame.get("operations")
    if type(operations) is not list:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback frame operations are missing",
        )
    receipts: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    try:
        for expected_ordinal, raw in enumerate(operations, start=1):
            if not isinstance(raw, Mapping) or raw.get("rollback_ordinal") != expected_ordinal:
                raise MotherDeploymentIdentityRollbackError(
                    "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
                    "identity rollback frame ordering is invalid",
                )
            item = dict(raw)
            controller, envs = _observe_candidate(
                private_state,
                network,
                {
                    **item,
                    "observe_endpoint": item.get("observe", {}).get("endpoint"),
                },
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            env_uuid = _identifier(item.get("environment_variable_uuid"), "environment variable UUID")
            key = _identifier(item.get("environment_key"), "environment key")
            exact = [env for env in envs if _env_uuid(env) == env_uuid]
            same_key = [env for env in envs if _env_key(env) == key]
            if not exact:
                if same_key:
                    raise MotherDeploymentIdentityRollbackError(
                        "MOTHER_DEPLOY_IDENTITY_ROLLBACK_OWNERSHIP_MISMATCH",
                        f"environment key {key!r} exists under a different UUID",
                    )
                receipts.append(
                    {
                        "rollback_ordinal": expected_ordinal,
                        "mutation_id": item["mutation_id"],
                        "node": item["node"],
                        "controller_id": item["controller_id"],
                        "environment_key": key,
                        "environment_variable_uuid": env_uuid,
                        "status": "already-absent",
                        "delete_performed": False,
                        "postcondition_verified": True,
                        "postcondition_observation_attempts": 1,
                        "postcondition_wait_ms": 0,
                    }
                )
                continue
            if len(exact) != 1 or _env_key(exact[0]) != key:
                raise MotherDeploymentIdentityRollbackError(
                    "MOTHER_DEPLOY_IDENTITY_ROLLBACK_OWNERSHIP_MISMATCH",
                    f"environment UUID {env_uuid!r} no longer matches {key!r}",
                )
            inverse = item.get("inverse")
            if not isinstance(inverse, Mapping) or inverse.get("method") != "DELETE":
                raise MotherDeploymentIdentityRollbackError(
                    "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
                    "identity rollback inverse operation is invalid",
                )
            endpoint = str(inverse.get("endpoint", ""))
            expected_endpoint = f"/api/v1/services/{_identifier(item.get('service_uuid'), 'service UUID')}/envs/{env_uuid}"
            if endpoint != expected_endpoint:
                raise MotherDeploymentIdentityRollbackError(
                    "MOTHER_DEPLOY_IDENTITY_ROLLBACK_ENDPOINT_REJECTED",
                    "identity rollback DELETE endpoint does not bind the exact service and variable UUID",
                )
            deleted = _http_json(
                controller,
                "DELETE",
                endpoint,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            if not deleted["ok"] and deleted["status"] != 404:
                raise MotherDeploymentIdentityRollbackError(
                    "MOTHER_DEPLOY_IDENTITY_ROLLBACK_DELETE_FAILED",
                    f"Coolify rejected DELETE for {key!r} with HTTP {deleted['status']}",
                )
            maximum_attempts = max(1, int(float(wait_seconds) / float(poll_seconds)) + 1)
            started = time.monotonic()
            verified = False
            for attempt in range(1, maximum_attempts + 1):
                _, after_envs = _observe_candidate(
                    private_state,
                    network,
                    {
                        **item,
                        "observe_endpoint": item.get("observe", {}).get("endpoint"),
                    },
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                if not [env for env in after_envs if _env_uuid(env) == env_uuid or _env_key(env) == key]:
                    verified = True
                    break
                if attempt < maximum_attempts:
                    time.sleep(float(poll_seconds))
            if not verified:
                raise MotherDeploymentIdentityRollbackError(
                    "MOTHER_DEPLOY_IDENTITY_ROLLBACK_POSTCONDITION_FAILED",
                    f"environment key {key!r} remains present after bounded DELETE verification",
                )
            receipts.append(
                {
                    "rollback_ordinal": expected_ordinal,
                    "mutation_id": item["mutation_id"],
                    "node": item["node"],
                    "controller_id": item["controller_id"],
                    "environment_key": key,
                    "environment_variable_uuid": env_uuid,
                    "status": "succeeded",
                    "delete_performed": deleted["status"] != 404,
                    "delete_response": {
                        "status": deleted["status"],
                        "response_sha256": deleted["response_sha256"],
                        "elapsed_ms": deleted["elapsed_ms"],
                    },
                    "postcondition_verified": True,
                    "postcondition_observation_attempts": attempt,
                    "postcondition_wait_ms": int((time.monotonic() - started) * 1000),
                }
            )
    except Exception as exc:
        if isinstance(exc, MotherDeploymentIdentityRollbackError):
            failure = {"code": exc.code, "message": _safe_message(exc)}
        else:
            failure = {
                "code": "MOTHER_DEPLOY_IDENTITY_ROLLBACK_UNEXPECTED_FAILURE",
                "message": _safe_message(exc),
            }
    complete = failure is None and len(receipts) == len(operations) and all(
        item.get("postcondition_verified") is True for item in receipts
    )
    return {
        "status": "pass" if complete else "failed",
        "rollback_receipts": receipts,
        "failure": failure,
        "summary": {
            "planned_operation_count": len(operations),
            "completed_operation_count": len(receipts),
            "absent_count": sum(
                item.get("status") in {"succeeded", "already-absent"} for item in receipts
            ),
            "postconditions_verified": complete,
            "network_access_performed": bool(operations),
            "live_mutation_performed": any(item.get("delete_performed") is True for item in receipts),
            "complete": complete,
        },
    }


def execute_identity_journal_rollback(
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
    inspected = inspect_identity_rollback_journal(
        paths,
        private_state,
        journal_path,
        acknowledged_journal_sha256=acknowledged_journal_sha256,
    )
    started_at = _utc_now()
    frame, current_path, current_digest = _resolve_journal_frame(
        paths,
        private_state,
        Path(inspected["journal_path"]),
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        operation=operation,
    )
    outcome = execute_identity_rollback_frame(
        private_state,
        network=inspected["network"],
        frame=frame,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    journal_status = "rolled-back" if outcome["summary"]["complete"] else "rollback-required"
    current_path, current_digest, _ = update_identity_rollback_journal_status(
        paths,
        current_path,
        status=journal_status,
        operation=operation,
    )
    result = {
        "kind": _RESULT_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "status": outcome["status"],
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "nodes": list(inspected["nodes"]),
        "staged_scope": inspected["staged_scope"],
        "identity_profile_sha256": inspected["identity_profile_sha256"],
        "journal": {
            "locator": current_path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": current_digest,
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
            "exact_service_uuid_required": True,
            "exact_environment_variable_uuid_required": True,
            "exact_environment_key_required": True,
            "post_delete_absence_required": True,
            "secrets_in_output": False,
        },
        "rollback_receipts": outcome["rollback_receipts"],
        "failure": outcome["failure"],
        "summary": dict(outcome["summary"]),
    }
    result_path, result_digest = _write_rollback_result(paths, result, operation=operation)
    return {**result, "result_artifact": {"path": str(result_path), "sha256": result_digest}}


def _load_execution(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    acknowledged_execution_sha256: str,
) -> tuple[dict[str, Any], Path, str]:
    candidate = _beneath(paths, execution_path, _EXECUTION_DIRECTORY, label="identity execution")
    execution, _, digest = _canonical_file(candidate, label="identity execution")
    if digest != _sha256(acknowledged_execution_sha256, "acknowledged execution SHA-256"):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact identity execution",
        )
    if (
        execution.get("kind") != _EXECUTION_KIND
        or execution.get("mother_binding") != _binding(private_state)
        or execution.get("staged_scope") != "install-reserved-identity"
        or _contains_sensitive_key(execution)
    ):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity execution is malformed, stale, or sensitive",
        )
    return execution, candidate, digest


def _successful_later_results(
    paths: PrivateStatePaths,
    execution: Mapping[str, Any],
    execution_path: Path,
) -> list[str]:
    boundary = _parse_utc(execution.get("completed_at"), "identity execution completed_at")
    binding = execution.get("mother_binding")
    network = execution.get("network")
    nodes = execution.get("nodes")
    blockers: list[str] = []
    scans = (
        (_EXECUTION_DIRECTORY, _EXECUTION_KIND, execution_path),
        (
            _GENESIS_EXECUTION_DIRECTORY,
            "main_computer.mother.deployment_genesis_execution_result.v1",
            None,
        ),
    )
    for parts, kind, exclude in scans:
        root = _root(paths, parts)
        if not root.is_dir():
            continue
        for candidate in root.glob("*.json"):
            if exclude is not None and candidate.resolve(strict=False) == exclude.resolve(strict=False):
                continue
            try:
                document, _, _ = _canonical_file(candidate, label="downstream deployment result")
            except MotherDeploymentIdentityRollbackError:
                continue
            if (
                document.get("kind") != kind
                or document.get("status") != "pass"
                or document.get("mother_binding") != binding
                or document.get("network") != network
                or document.get("nodes") != nodes
            ):
                continue
            completed = document.get("completed_at")
            try:
                completed_at = _parse_utc(completed, "downstream completed_at")
            except MotherDeploymentIdentityRollbackError:
                continue
            if completed_at >= boundary:
                blockers.append(str(candidate))
    return sorted(blockers)


def inspect_identity_mutation_rollback(
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
    execution, candidate, digest = _load_execution(
        paths,
        private_state,
        execution_path,
        acknowledged_execution_sha256=acknowledged_execution_sha256,
    )
    blockers = _successful_later_results(paths, execution, candidate)
    if blockers:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_BOUNDARY_CROSSED",
            "a later successful identity or genesis phase exists for this exact deployment profile",
        )
    journal_binding = execution.get("rollback_journal")
    if not isinstance(journal_binding, Mapping):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_FRAME_MISSING",
            "identity execution has no durable rollback journal binding",
        )
    journal_locator = journal_binding.get("locator")
    if (
        type(journal_locator) is not str
        or not journal_locator
        or "\\" in journal_locator
        or Path(journal_locator).is_absolute()
        or any(part in {"", ".", ".."} for part in Path(journal_locator).parts)
    ):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_PATH_UNSAFE",
            "identity rollback journal locator is unsafe",
        )
    journal_path = _beneath(
        paths,
        paths.root / journal_locator,
        _JOURNAL_DIRECTORY,
        label="identity rollback journal",
    )
    journal, _, journal_digest = _canonical_file(journal_path, label="identity rollback journal")
    _sha256(journal_binding.get("sha256"), "journal SHA-256")
    release_binding = execution.get("release")
    transaction_binding = execution.get("identity_transaction")
    if (
        journal.get("kind") != _JOURNAL_KIND
        or journal.get("mother_binding") != execution.get("mother_binding")
        or journal.get("network") != execution.get("network")
        or journal.get("nodes") != execution.get("nodes")
        or journal.get("identity_profile_sha256") != execution.get("identity_profile_sha256")
        or not isinstance(release_binding, Mapping)
        or not isinstance(transaction_binding, Mapping)
        or journal.get("release", {}).get("sha256") != release_binding.get("sha256")
        or journal.get("identity_transaction", {}).get("sha256") != transaction_binding.get("sha256")
    ):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_JOURNAL_MISMATCH",
            "identity rollback journal no longer matches the execution lineage",
        )
    frame, current_path, current_digest = _resolve_journal_frame(
        paths,
        private_state,
        journal_path,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        operation=operation,
    )
    return {
        "clean": True,
        "execution_path": str(candidate),
        "execution_sha256": digest,
        "mother_binding": dict(execution["mother_binding"]),
        "network": execution["network"],
        "nodes": list(execution["nodes"]),
        "staged_scope": execution["staged_scope"],
        "identity_profile_sha256": execution["identity_profile_sha256"],
        "journal_path": str(current_path),
        "journal_sha256": current_digest,
        "frame": frame,
        "rollback_boundary_open": True,
        "downstream_success_blockers": [],
        "rollback_operation_count": frame["summary"]["operation_count"],
        "rollback_implemented": True,
        "network_access_performed": True,
        "live_mutation_performed": False,
    }


def execute_identity_mutation_rollback(
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
    inspected = inspect_identity_mutation_rollback(
        paths,
        private_state,
        execution_path,
        acknowledged_execution_sha256=acknowledged_execution_sha256,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        operation=operation,
    )
    started_at = _utc_now()
    outcome = execute_identity_rollback_frame(
        private_state,
        network=inspected["network"],
        frame=inspected["frame"],
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    journal_status = "rolled-back" if outcome["summary"]["complete"] else "rollback-required"
    journal_path, journal_digest, _ = update_identity_rollback_journal_status(
        paths,
        Path(inspected["journal_path"]),
        status=journal_status,
        operation=operation,
    )
    result = {
        "kind": _RESULT_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "status": outcome["status"],
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "nodes": list(inspected["nodes"]),
        "staged_scope": inspected["staged_scope"],
        "identity_profile_sha256": inspected["identity_profile_sha256"],
        "execution": {
            "locator": Path(inspected["execution_path"]).resolve(strict=False).relative_to(
                paths.root.resolve(strict=False)
            ).as_posix(),
            "sha256": inspected["execution_sha256"],
        },
        "journal": {
            "locator": journal_path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": journal_digest,
        },
        "rollback_frame": inspected["frame"],
        "authority": {
            "authorization_source": "explicit-operator-execution-acknowledgement",
            "rollback_boundary_open": True,
            "idempotent_retry_allowed": True,
            "crash_recovery_path": False,
        },
        "policy": {
            "allowed_http_methods": ["GET", "DELETE"],
            "exact_environment_variable_uuid_required": True,
            "exact_environment_key_required": True,
            "post_delete_absence_required": True,
            "secrets_in_output": False,
        },
        "rollback_receipts": outcome["rollback_receipts"],
        "failure": outcome["failure"],
        "summary": dict(outcome["summary"]),
    }
    result_path, result_digest = _write_rollback_result(paths, result, operation=operation)
    return {**result, "result_artifact": {"path": str(result_path), "sha256": result_digest}}


def _write_rollback_result(
    paths: PrivateStatePaths,
    result: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if result.get("kind") != _RESULT_KIND or _contains_sensitive_key(result):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_RESULT_INVALID",
            "identity rollback result is malformed or sensitive",
        )
    payload = canonical_json(dict(result))
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, _RESULT_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(result.get("completed_at", "")))[:32] or "identityrollback"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentIdentityRollbackError(
                "MOTHER_DEPLOY_IDENTITY_ROLLBACK_CONFLICT",
                "identity rollback-result destination contains different bytes",
            )
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_identity_mutation_rollback(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    rollback_result_path: Path,
    *,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    observed_at: str | None = None,
) -> dict[str, Any]:
    candidate = _beneath(paths, rollback_result_path, _RESULT_DIRECTORY, label="identity rollback result")
    result, _, digest = _canonical_file(candidate, label="identity rollback result")
    if (
        result.get("kind") != _RESULT_KIND
        or result.get("status") != "pass"
        or result.get("mother_binding") != _binding(private_state)
        or result.get("summary", {}).get("complete") is not True
        or _contains_sensitive_key(result)
    ):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback result is not a complete current-generation success",
        )
    frame = result.get("rollback_frame")
    if not isinstance(frame, Mapping):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback frame is missing",
        )
    checks: list[dict[str, Any]] = []
    for raw in frame.get("operations", []):
        if not isinstance(raw, Mapping):
            raise MotherDeploymentIdentityRollbackError(
                "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
                "identity rollback verification contains an invalid operation",
            )
        _, envs = _observe_candidate(
            private_state,
            result["network"],
            {
                **raw,
                "observe_endpoint": raw.get("observe", {}).get("endpoint"),
            },
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        env_uuid = _identifier(raw.get("environment_variable_uuid"), "environment variable UUID")
        key = _identifier(raw.get("environment_key"), "environment key")
        present = [env for env in envs if _env_uuid(env) == env_uuid or _env_key(env) == key]
        checks.append(
            {
                "node": raw["node"],
                "controller_id": raw["controller_id"],
                "environment_key": key,
                "environment_variable_uuid": env_uuid,
                "absent": not present,
            }
        )
    clean = len(checks) == frame.get("summary", {}).get("operation_count") and all(
        item["absent"] is True for item in checks
    )
    if not clean:
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_POSTCONDITION_FAILED",
            "one or more identity environment variables remain present",
        )
    timestamp = observed_at or _utc_now()
    _parse_utc(timestamp, "observed_at")
    verification = {
        "kind": _VERIFICATION_KIND,
        "schema_version": 1,
        "observed_at": timestamp,
        "clean": True,
        "mother_binding": dict(result["mother_binding"]),
        "network": result["network"],
        "nodes": list(result["nodes"]),
        "staged_scope": result["staged_scope"],
        "identity_profile_sha256": result["identity_profile_sha256"],
        "rollback_result": {
            "locator": candidate.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": digest,
        },
        "rolled_back_execution": dict(result.get("execution", {})),
        "checks": checks,
        "summary": {
            "expected_absent_count": len(checks),
            "absent_count": len(checks),
            "network_access_performed": True,
            "live_mutation_performed": False,
            "clean": True,
        },
    }
    if _contains_sensitive_key(verification):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback verification contains a sensitive field",
        )
    verification["identity_rollback_verification_sha256"] = hashlib.sha256(
        canonical_json(verification)
    ).hexdigest()
    return verification


def write_identity_mutation_rollback_verification(
    paths: PrivateStatePaths,
    verification: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(verification)
    expected = document.pop("identity_rollback_verification_sha256", None)
    digest = hashlib.sha256(canonical_json(document)).hexdigest()
    document["identity_rollback_verification_sha256"] = digest
    if expected != digest or document.get("kind") != _VERIFICATION_KIND or _contains_sensitive_key(document):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_INVALID",
            "identity rollback verification is malformed or sensitive",
        )
    payload = canonical_json(document)
    byte_digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, _VERIFICATION_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("observed_at", "")))[:32] or "identityrollback"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentIdentityRollbackError(
                "MOTHER_DEPLOY_IDENTITY_ROLLBACK_CONFLICT",
                "identity rollback verification destination contains different bytes",
            )
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, byte_digest


def verify_identity_rollback_cycle_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    verification_path: Path,
    *,
    identity_profile_sha256_value: str,
    network: str,
    nodes: Iterable[str],
    before_execution_started_at: str,
    current_execution_sha256: str,
) -> dict[str, Any]:
    candidate = _beneath(
        paths,
        verification_path,
        _VERIFICATION_DIRECTORY,
        label="identity rollback verification",
    )
    verification, _, byte_digest = _canonical_file(candidate, label="identity rollback verification")
    semantic = dict(verification)
    stored_digest = semantic.pop("identity_rollback_verification_sha256", None)
    semantic_digest = hashlib.sha256(canonical_json(semantic)).hexdigest()
    requested_nodes = [_identifier(item, "node") for item in nodes]
    if (
        verification.get("kind") != _VERIFICATION_KIND
        or stored_digest != semantic_digest
        or verification.get("clean") is not True
        or verification.get("mother_binding") != _binding(private_state)
        or verification.get("network") != network
        or verification.get("nodes") != requested_nodes
        or verification.get("identity_profile_sha256") != identity_profile_sha256_value
        or verification.get("summary", {}).get("clean") is not True
        or _contains_sensitive_key(verification)
    ):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_CYCLE_REQUIRED",
            "identity rollback-cycle evidence does not bind the current deployment profile",
        )
    rolled_back_execution = verification.get("rolled_back_execution")
    if (
        not isinstance(rolled_back_execution, Mapping)
        or rolled_back_execution.get("sha256")
        == _sha256(current_execution_sha256, "current identity execution SHA-256")
    ):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_CYCLE_REQUIRED",
            "genesis requires a distinct successful identity reapplication after rollback",
        )
    if _parse_utc(verification.get("observed_at"), "rollback verification observed_at") > _parse_utc(
        before_execution_started_at,
        "identity execution started_at",
    ):
        raise MotherDeploymentIdentityRollbackError(
            "MOTHER_DEPLOY_IDENTITY_ROLLBACK_CYCLE_REQUIRED",
            "identity reapplication must occur after the verified rollback cycle",
        )
    return {
        "clean": True,
        "verification_path": str(candidate),
        "verification_sha256": byte_digest,
        "identity_rollback_verification_sha256": stored_digest,
        "identity_profile_sha256": identity_profile_sha256_value,
        "observed_at": verification["observed_at"],
    }


__all__ = [
    "MotherDeploymentIdentityRollbackError",
    "build_identity_rollback_journal",
    "execute_identity_journal_rollback",
    "execute_identity_mutation_rollback",
    "execute_identity_rollback_frame",
    "identity_profile_sha256",
    "identity_rollback_journal_path",
    "inspect_identity_mutation_rollback",
    "inspect_identity_rollback_journal",
    "update_identity_rollback_journal_candidate",
    "update_identity_rollback_journal_status",
    "verify_identity_mutation_rollback",
    "verify_identity_rollback_cycle_evidence",
    "write_identity_mutation_rollback_verification",
    "write_identity_rollback_journal",
]
