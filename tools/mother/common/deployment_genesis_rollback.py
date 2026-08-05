"""Rollback and crash recovery for first-genesis installation.

The rollback cancels exact active Coolify deployments for the initial service,
restores the exact Mother standby Compose that was verified before the first
genesis PATCH, leaves the reserved identity variables intact, and proves that
the initial service is stopped.  Coolify-managed persistent volumes
are not deleted because the public service API exposes no volume-delete
operation; every result reports that limitation explicitly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any
import urllib.error
import urllib.request

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_JOURNAL_KIND = "main_computer.mother.deployment_genesis_rollback_journal.v1"
_FRAME_KIND = "main_computer.mother.deployment_genesis_rollback_frame.v1"
_RESULT_KIND = "main_computer.mother.deployment_genesis_rollback_result.v1"
_VERIFICATION_KIND = "main_computer.mother.deployment_genesis_rollback_verification.v1"
_EXECUTION_KIND = "main_computer.mother.deployment_genesis_execution_result.v1"
_JOURNAL_DIRECTORY = ("actions", "deployment-genesis-rollback-journals")
_RESULT_DIRECTORY = ("actions", "deployment-genesis-rollbacks")
_VERIFICATION_DIRECTORY = ("evidence", "deployment-genesis-rollbacks")
_EXECUTION_DIRECTORY = ("actions", "deployment-genesis-executions")
_BIRTH_EVIDENCE_DIRECTORY = ("evidence", "deployment-genesis-birth")
_DEFAULT_POST_MUTATION_WAIT_SECONDS = 20.0
_DEFAULT_POLL_SECONDS = 0.5
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class MotherDeploymentGenesisRollbackError(RuntimeError):
    """A first-genesis rollback could not be proven or executed safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", f"{path} must be a non-empty string"
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", f"{path} is not a safe identifier"
        )
    return text


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", f"{path} must be a lowercase SHA-256 digest"
        )
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", f"{path} must be a UTC timestamp"
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", f"{path} is malformed"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", f"{path} must be UTC"
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
        "access_token", "api_token", "credential", "mnemonic", "password",
        "private_key", "refresh_token", "secret", "seed",
    }
    if isinstance(value, Mapping):
        return any(str(key).lower() in forbidden or _contains_sensitive_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _safe_message(value: Any) -> str:
    text = str(value)
    if "Bearer " in text or "|" in text:
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
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_PATH_UNSAFE",
            f"{label} is outside the canonical Mother directory",
        ) from exc
    return candidate


def _canonical_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", f"{label} could not be read as canonical JSON"
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", f"{label} is not canonical JSON"
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _write_document(
    paths: PrivateStatePaths,
    parts: tuple[str, str],
    document: Mapping[str, Any],
    *,
    operation: OperationIdentity,
    suffix: str,
) -> tuple[Path, str]:
    payload = canonical_json(dict(document))
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, parts, operation=operation)
    destination = root / suffix
    if destination.exists():
        atomic_files.durable_replace(destination, payload, operation=operation)
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def _standby_compose(node: str) -> str:
    return "\n".join(
        [
            f"name: {node}",
            "",
            "services:",
            f"  {node}:",
            "    image: alpine:3.20",
            '    restart: "no"',
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


def _decode_base64_text(value: str) -> str | None:
    compact = "".join(value.split())
    if not compact:
        return None
    padded = compact + ("=" * (-len(compact) % 4))
    try:
        decoded = base64.b64decode(padded, validate=True)
        return decoded.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _compose_candidates(payload: Any) -> list[str]:
    records: list[Mapping[str, Any]] = []
    if isinstance(payload, Mapping):
        records.append(payload)
        for key in ("data", "resource", "service"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                records.append(nested)
    output: list[str] = []
    for record in records:
        for key in ("docker_compose_raw", "docker_compose"):
            value = record.get(key)
            if type(value) is not str or not value:
                continue
            output.append(value)
            decoded = _decode_base64_text(value)
            if decoded is not None:
                output.append(decoded)
    return output


def _normalized(value: str) -> str:
    return value.replace("\r\n", "\n").rstrip()


def _semantic_compose(value: str) -> Any | None:
    try:
        document = yaml.safe_load(value)
    except yaml.YAMLError:
        return None
    return document if isinstance(document, Mapping) else None


def compose_matches(payload: Any, expected: str) -> bool:
    expected_normalized = _normalized(expected)
    expected_semantic = _semantic_compose(expected)
    for candidate in _compose_candidates(payload):
        if _normalized(candidate) == expected_normalized:
            return True
        candidate_semantic = _semantic_compose(candidate)
        if expected_semantic is not None and candidate_semantic == expected_semantic:
            return True
    return False


def require_compose(payload: Any, expected: str, *, label: str) -> dict[str, Any]:
    candidates = _compose_candidates(payload)
    if not candidates:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_COMPOSE_UNAVAILABLE",
            "Coolify service detail did not expose docker_compose_raw or docker_compose; "
            "the API token may require read:sensitive permission",
        )
    if not compose_matches(payload, expected):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_COMPOSE_MISMATCH",
            f"live service Compose does not match the expected {label}",
        )
    return {
        "verified": True,
        "expected_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        "match_mode": "normalized-text-or-base64-decoded",
    }


def _items(payload: Any) -> list[Mapping[str, Any]]:
    if type(payload) is list:
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("services", "resources", "data"):
            value = payload.get(key)
            if type(value) is list:
                return [item for item in value if isinstance(item, Mapping)]
        if any(key in payload for key in ("uuid", "id", "name", "status")):
            return [payload]
    return []


def _item_text(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if type(value) is str and value.strip():
            return value.strip()
    return ""


def _service_record(payload: Any, *, service_uuid: str, node: str) -> Mapping[str, Any]:
    matches = [item for item in _items(payload) if _item_text(item, "uuid", "id") == service_uuid]
    if len(matches) != 1 or _item_text(matches[0], "name") != node:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_SERVICE_MISMATCH",
            "Coolify does not expose the exact initial-node service binding",
        )
    return matches[0]


def _active_deployment_records(
    payload: Any,
    *,
    service_uuid: str,
    node: str,
) -> list[dict[str, str]]:
    """Return sanitized currently-running deployment bindings for the exact service."""

    matches: dict[str, dict[str, str]] = {}
    for item in _items(payload):
        deployment_uuid = _item_text(item, "deployment_uuid", "uuid", "id")
        if not deployment_uuid:
            continue
        exact_ids = {
            _item_text(item, "application_id"),
            _item_text(item, "application_uuid"),
            _item_text(item, "resource_uuid"),
            _item_text(item, "service_uuid"),
        }
        exact_names = {
            _item_text(item, "application_name"),
            _item_text(item, "name"),
        }
        if service_uuid not in exact_ids and node not in exact_names:
            continue
        safe_uuid = _identifier(deployment_uuid, "deployment UUID")
        record = {"deployment_uuid": safe_uuid}
        status = _item_text(item, "status")
        if status:
            record["status"] = status[:128]
        matches[safe_uuid] = record
    return [matches[key] for key in sorted(matches)]


def _cancel_active_deployments(
    controller: Any,
    *,
    service_uuid: str,
    node: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> list[dict[str, Any]]:
    observed = _http_json(
        controller,
        "GET",
        "/api/v1/deployments",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if not observed["ok"]:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_DEPLOYMENT_OBSERVATION_FAILED",
            f"Coolify active deployment GET failed with HTTP {observed['status']}",
        )

    receipts: list[dict[str, Any]] = []
    for binding in _active_deployment_records(
        observed["payload"],
        service_uuid=service_uuid,
        node=node,
    ):
        deployment_uuid = binding["deployment_uuid"]
        cancelled = _http_json(
            controller,
            "POST",
            f"/api/v1/deployments/{deployment_uuid}/cancel",
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        if cancelled["status"] not in {200, 201, 202, 400}:
            raise MotherDeploymentGenesisRollbackError(
                "MOTHER_DEPLOY_GENESIS_ROLLBACK_DEPLOYMENT_CANCEL_FAILED",
                (
                    f"Coolify rejected cancellation of deployment "
                    f"{deployment_uuid!r} with HTTP {cancelled['status']}"
                ),
            )
        request_accepted = cancelled["status"] in {200, 201, 202}
        receipt: dict[str, Any] = {
            "deployment_uuid": deployment_uuid,
            "status": "cancelled" if request_accepted else "already-terminal",
            "request_accepted": request_accepted,
            "response": {
                "status": cancelled["status"],
                "response_sha256": cancelled["response_sha256"],
                "elapsed_ms": cancelled["elapsed_ms"],
            },
        }
        if "status" in binding:
            receipt["observed_status"] = binding["status"]
        receipts.append(receipt)
    return receipts


def _stopped_status(value: str) -> bool:
    status = value.strip().lower()
    return status.startswith("exited") or status.startswith("stopped")


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    if hasattr(opener, "open"):
        return opener.open(request, timeout=timeout)
    return opener(request, timeout=timeout)


def _http_json(
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
        "User-Agent": "main-computer-mother-genesis-rollback/1",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        controller.base_url + endpoint,
        data=payload,
        headers=headers,
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
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_REQUEST_FAILED",
            f"Coolify request failed: {_safe_message(exc.reason)}",
        ) from exc
    except OSError as exc:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_REQUEST_FAILED", "Coolify request failed"
        ) from exc
    if len(raw) > max_response_bytes:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_RESPONSE_TOO_LARGE",
            f"Coolify response exceeded {max_response_bytes} bytes",
        )
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed: Any = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        parsed = text.strip()
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
        "payload": parsed,
    }


def genesis_rollback_journal_path(paths: PrivateStatePaths, release_sha256: str) -> Path:
    return _root(paths, _JOURNAL_DIRECTORY) / f"{_sha256(release_sha256, 'release SHA-256')}.json"


def build_genesis_rollback_journal(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    *,
    release_path: Path,
    release_sha256: str,
    genesis_transaction_sha256: str,
    genesis_sha256: str,
    genesis_compose_sha256: str,
    network: str,
    node: str,
    controller_id: str,
    service_uuid: str,
) -> dict[str, Any]:
    release_path = Path(release_path).resolve(strict=False)
    try:
        locator = release_path.relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_PATH_UNSAFE", "release path is outside Mother state"
        ) from exc
    node = _identifier(node, "node")
    standby = _standby_compose(node)
    restore_body = {
        "name": node,
        "docker_compose_raw": base64.b64encode(standby.encode("utf-8")).decode("ascii"),
    }
    journal = {
        "kind": _JOURNAL_KIND,
        "schema_version": 1,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "status": "open-before-first-patch",
        "network": _identifier(network, "network"),
        "mother_binding": _binding(private_state),
        "node": node,
        "controller_id": _identifier(controller_id, "controller_id"),
        "service_uuid": _identifier(service_uuid, "service_uuid"),
        "release": {"locator": locator, "sha256": _sha256(release_sha256, "release SHA-256")},
        "genesis_transaction_sha256": _sha256(genesis_transaction_sha256, "genesis transaction SHA-256"),
        "genesis_sha256": _sha256(genesis_sha256, "genesis SHA-256"),
        "genesis_compose_sha256": _sha256(genesis_compose_sha256, "genesis Compose SHA-256"),
        "prestate": {
            "service_status": "stopped",
            "standby_compose_sha256": hashlib.sha256(standby.encode("utf-8")).hexdigest(),
            "standby_compose": standby,
            "restore_body": restore_body,
            "restore_body_sha256": hashlib.sha256(canonical_json(restore_body)).hexdigest(),
            "identity_keys_preserved": [
                "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY",
                "MC_MOTHER_VALIDATOR_PRIVATE_KEY",
            ],
        },
        "candidates": [
            {"ordinal": 1, "mutation_id": f"{node}.install-first-genesis-compose", "state": "pending"},
            {"ordinal": 2, "mutation_id": f"{node}.deploy-first-genesis", "state": "pending"},
        ],
        "policy": {
            "journal_written_before_first_patch": True,
            "exact_service_uuid_required": True,
            "exact_standby_compose_required": True,
            "identity_variables_preserved": True,
            "persistent_volume_cleanup_authorized": False,
            "secrets_in_output": False,
        },
    }
    if _contains_sensitive_key(journal):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", "rollback journal contains a sensitive field"
        )
    return journal


def write_genesis_rollback_journal(
    paths: PrivateStatePaths,
    journal: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if journal.get("kind") != _JOURNAL_KIND:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", "rollback journal kind is invalid"
        )
    release = journal.get("release")
    if not isinstance(release, Mapping):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", "rollback journal release binding is missing"
        )
    release_sha = _sha256(release.get("sha256"), "release SHA-256")
    destination = genesis_rollback_journal_path(paths, release_sha)
    payload = canonical_json(dict(journal))
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, _JOURNAL_DIRECTORY, operation=operation)
    destination = root / destination.name
    if destination.exists():
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_JOURNAL_EXISTS",
            "a genesis rollback journal already exists for this release",
        )
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def update_genesis_rollback_journal(
    paths: PrivateStatePaths,
    journal_path: Path,
    *,
    mutation_id: str | None = None,
    state: str | None = None,
    status: str | None = None,
    operation: OperationIdentity,
) -> tuple[Path, str, dict[str, Any]]:
    candidate = _beneath(paths, journal_path, _JOURNAL_DIRECTORY, label="rollback journal")
    journal, _, _ = _canonical_file(candidate, label="rollback journal")
    if journal.get("kind") != _JOURNAL_KIND:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", "rollback journal kind is invalid"
        )
    document = dict(journal)
    if mutation_id is not None:
        mutation_id = _identifier(mutation_id, "mutation_id")
        candidates = document.get("candidates")
        if type(candidates) is not list:
            raise MotherDeploymentGenesisRollbackError(
                "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", "rollback journal candidates are missing"
            )
        updated = False
        new_candidates = []
        for item in candidates:
            if not isinstance(item, Mapping):
                raise MotherDeploymentGenesisRollbackError(
                    "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", "rollback journal candidate is invalid"
                )
            current = dict(item)
            if current.get("mutation_id") == mutation_id:
                current["state"] = _identifier(state, "candidate state")
                current["updated_at"] = _utc_now()
                updated = True
            new_candidates.append(current)
        if not updated:
            raise MotherDeploymentGenesisRollbackError(
                "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", "rollback journal mutation is unknown"
            )
        document["candidates"] = new_candidates
    if status is not None:
        document["status"] = _identifier(status, "journal status")
    document["updated_at"] = _utc_now()
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    atomic_files.durable_replace(candidate, payload, operation=operation)
    _secure_private_path(candidate, is_directory=False, operation=operation)
    return candidate, digest, document


def inspect_genesis_rollback_journal(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    journal_path: Path,
    *,
    acknowledged_journal_sha256: str,
) -> dict[str, Any]:
    candidate = _beneath(paths, journal_path, _JOURNAL_DIRECTORY, label="rollback journal")
    journal, _, digest = _canonical_file(candidate, label="rollback journal")
    if digest != _sha256(acknowledged_journal_sha256, "acknowledged journal SHA-256"):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact rollback journal SHA-256",
        )
    if journal.get("kind") != _JOURNAL_KIND or journal.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID",
            "rollback journal kind or Mother binding is invalid",
        )
    prestate = journal.get("prestate")
    if not isinstance(prestate, Mapping):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", "rollback journal prestate is missing"
        )
    standby = prestate.get("standby_compose")
    restore_body = prestate.get("restore_body")
    if type(standby) is not str or not isinstance(restore_body, Mapping):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", "rollback journal standby Compose is missing"
        )
    if hashlib.sha256(standby.encode("utf-8")).hexdigest() != prestate.get("standby_compose_sha256"):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", "rollback journal standby Compose commitment is invalid"
        )
    if hashlib.sha256(canonical_json(dict(restore_body))).hexdigest() != prestate.get("restore_body_sha256"):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_INVALID", "rollback journal restore body commitment is invalid"
        )
    node = _identifier(journal.get("node"), "node")
    service_uuid = _identifier(journal.get("service_uuid"), "service_uuid")
    return {
        "clean": True,
        "journal_path": str(candidate),
        "journal_sha256": digest,
        "network": _identifier(journal.get("network"), "network"),
        "node": node,
        "controller_id": _identifier(journal.get("controller_id"), "controller_id"),
        "service_uuid": service_uuid,
        "genesis_sha256": _sha256(journal.get("genesis_sha256"), "genesis SHA-256"),
        "genesis_compose_sha256": _sha256(journal.get("genesis_compose_sha256"), "genesis Compose SHA-256"),
        "standby_compose_sha256": _sha256(prestate.get("standby_compose_sha256"), "standby Compose SHA-256"),
        "status": journal.get("status"),
        "frame": {
            "kind": _FRAME_KIND,
            "schema_version": 1,
            "staged_scope": "install-and-start-first-genesis-on-initial-node",
            "idempotent": True,
            "node": node,
            "controller_id": journal.get("controller_id"),
            "service_uuid": service_uuid,
            "operations": [
                {
                    "ordinal": 1,
                    "operation_id": f"{node}.stop-first-genesis",
                    "method": "GET",
                    "endpoint": f"/api/v1/services/{service_uuid}/stop",
                    "postcondition": "exact service is stopped",
                },
                {
                    "ordinal": 2,
                    "operation_id": f"{node}.restore-standby-compose",
                    "method": "PATCH",
                    "endpoint": f"/api/v1/services/{service_uuid}",
                    "body_sha256": prestate.get("restore_body_sha256"),
                    "postcondition": "exact standby Compose restored and exact service stopped",
                },
            ],
            "summary": {"operation_count": 2},
        },
        "persistent_volume_cleanup_performed": False,
    }


def _wait_stopped(
    controller: Any,
    *,
    service_uuid: str,
    node: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    stable_observations: int = 3,
    reassert_stop: bool = False,
) -> dict[str, Any]:
    deadline = time.monotonic() + max_wait_seconds
    attempts = 0
    consecutive = 0
    waited_ms = 0
    last_status = ""
    reasserted_stop_count = 0
    while True:
        observed = _http_json(
            controller, "GET", "/api/v1/services", body=None,
            timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
        )
        attempts += 1
        if not observed["ok"]:
            raise MotherDeploymentGenesisRollbackError(
                "MOTHER_DEPLOY_GENESIS_ROLLBACK_OBSERVATION_FAILED",
                f"Coolify service inventory GET failed with HTTP {observed['status']}",
            )
        record = _service_record(observed["payload"], service_uuid=service_uuid, node=node)
        last_status = _item_text(record, "status")
        if _stopped_status(last_status):
            consecutive += 1
            if consecutive >= stable_observations:
                return {
                    "verified": True,
                    "attempts": attempts,
                    "wait_ms": waited_ms,
                    "status": last_status,
                    "stable_observations": consecutive,
                    "reasserted_stop_count": reasserted_stop_count,
                }
        else:
            consecutive = 0
            if reassert_stop:
                stop = _http_json(
                    controller, "GET", f"/api/v1/services/{service_uuid}/stop", body=None,
                    timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
                )
                if stop["status"] not in {200, 201, 202, 400}:
                    raise MotherDeploymentGenesisRollbackError(
                        "MOTHER_DEPLOY_GENESIS_ROLLBACK_STOP_FAILED",
                        f"Coolify rejected a repeated stop request with HTTP {stop['status']}",
                    )
                if stop["status"] in {200, 201, 202}:
                    reasserted_stop_count += 1
        if time.monotonic() >= deadline:
            raise MotherDeploymentGenesisRollbackError(
                "MOTHER_DEPLOY_GENESIS_ROLLBACK_STOP_POSTCONDITION_FAILED",
                f"service {service_uuid!r} did not remain stopped within the rollback window",
            )
        time.sleep(poll_interval_seconds)
        waited_ms += int(poll_interval_seconds * 1000)


def _verify_identity_keys(payload: Any) -> None:
    expected = {"MC_MOTHER_VALIDATOR_PRIVATE_KEY", "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY"}
    items: list[Mapping[str, Any]] = []
    if type(payload) is list:
        items = [item for item in payload if isinstance(item, Mapping)]
    elif isinstance(payload, Mapping):
        for key in ("envs", "environment_variables", "variables", "data"):
            value = payload.get(key)
            if type(value) is list:
                items = [item for item in value if isinstance(item, Mapping)]
                break
    keys = []
    for item in items:
        keys.append(_item_text(item, "key", "name", "variable"))
    for key in expected:
        if keys.count(key) != 1:
            raise MotherDeploymentGenesisRollbackError(
                "MOTHER_DEPLOY_GENESIS_ROLLBACK_IDENTITY_POSTCONDITION_FAILED",
                f"Coolify does not expose exactly one preserved {key!r}",
            )


def execute_genesis_journal_rollback(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    journal_path: Path,
    *,
    acknowledged_journal_sha256: str,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = _DEFAULT_POST_MUTATION_WAIT_SECONDS,
    poll_interval_seconds: float = _DEFAULT_POLL_SECONDS,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
    execution_binding: Mapping[str, Any] | None = None,
    authorization_source: str = "explicit-operator-journal-acknowledgement",
) -> dict[str, Any]:
    inspected = inspect_genesis_rollback_journal(
        paths,
        private_state,
        journal_path,
        acknowledged_journal_sha256=acknowledged_journal_sha256,
    )
    journal, _, _ = _canonical_file(Path(inspected["journal_path"]), label="rollback journal")
    prestate = journal["prestate"]
    standby = prestate["standby_compose"]
    restore_body = dict(prestate["restore_body"])
    controller = resolve_coolify_controller(
        private_state, inspected["network"], inspected["controller_id"],
        require_enabled=True, require_token=True,
    )
    node = inspected["node"]
    service_uuid = inspected["service_uuid"]
    started_at = _utc_now()
    receipts: list[dict[str, Any]] = []
    deployment_cancellation_receipts: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    live_mutation_performed = False
    service_stopped = False
    standby_compose_restored = False
    identity_keys_preserved = False
    try:
        deployment_cancellation_receipts = _cancel_active_deployments(
            controller,
            service_uuid=service_uuid,
            node=node,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        live_mutation_performed = any(
            item.get("request_accepted") is True
            for item in deployment_cancellation_receipts
        )

        detail = _http_json(
            controller, "GET", f"/api/v1/services/{service_uuid}", body=None,
            timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
        )
        if not detail["ok"]:
            raise MotherDeploymentGenesisRollbackError(
                "MOTHER_DEPLOY_GENESIS_ROLLBACK_OBSERVATION_FAILED",
                f"Coolify service detail GET failed with HTTP {detail['status']}",
            )
        already_standby = compose_matches(detail["payload"], standby)

        services = _http_json(
            controller, "GET", "/api/v1/services", body=None,
            timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
        )
        if not services["ok"]:
            raise MotherDeploymentGenesisRollbackError(
                "MOTHER_DEPLOY_GENESIS_ROLLBACK_OBSERVATION_FAILED",
                f"Coolify service inventory GET failed with HTTP {services['status']}",
            )
        record = _service_record(services["payload"], service_uuid=service_uuid, node=node)
        already_stopped = _stopped_status(_item_text(record, "status"))

        if already_standby and already_stopped:
            service_stopped = True
            standby_compose_restored = True
            receipts.append({
                "ordinal": 1,
                "operation_id": f"{node}.restore-standby-compose",
                "status": "already-restored",
                "service_uuid": service_uuid,
                "standby_compose_sha256": inspected["standby_compose_sha256"],
                "postconditions_verified": True,
            })
        elif already_standby:
            stop = _http_json(
                controller, "GET", f"/api/v1/services/{service_uuid}/stop", body=None,
                timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
            )
            if stop["status"] not in {200, 201, 202, 400}:
                raise MotherDeploymentGenesisRollbackError(
                    "MOTHER_DEPLOY_GENESIS_ROLLBACK_STOP_FAILED",
                    f"Coolify rejected the stop request with HTTP {stop['status']}",
                )
            stop_accepted = stop["status"] in {200, 201, 202}
            live_mutation_performed = live_mutation_performed or stop_accepted
            stop_receipt = {
                "ordinal": 1,
                "operation_id": f"{node}.stop-first-genesis",
                "status": "accepted" if stop_accepted else "not-accepted",
                "service_uuid": service_uuid,
                "request_accepted": stop_accepted,
                "response": {
                    "status": stop["status"],
                    "response_sha256": stop["response_sha256"],
                    "elapsed_ms": stop["elapsed_ms"],
                },
                "postcondition": {"verified": False, "pending": True},
            }
            receipts.append(stop_receipt)
            stopped = _wait_stopped(
                controller, service_uuid=service_uuid, node=node,
                timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
                max_wait_seconds=max_wait_seconds, poll_interval_seconds=poll_interval_seconds,
                reassert_stop=True,
            )
            stop_receipt["status"] = "succeeded"
            stop_receipt["postcondition"] = stopped
            service_stopped = True
            standby_compose_restored = True
            receipts.append({
                "ordinal": 2,
                "operation_id": f"{node}.restore-standby-compose",
                "status": "already-restored",
                "service_uuid": service_uuid,
                "standby_compose_sha256": inspected["standby_compose_sha256"],
                "postconditions_verified": True,
            })
        else:
            # A still-finishing deploy can restart the genesis Compose after an accepted
            # stop. Restore the non-restarting standby Compose before requiring a stable
            # stopped state, then reassert stop until the postcondition is stable.
            stop = _http_json(
                controller, "GET", f"/api/v1/services/{service_uuid}/stop", body=None,
                timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
            )
            if stop["status"] not in {200, 201, 202, 400}:
                raise MotherDeploymentGenesisRollbackError(
                    "MOTHER_DEPLOY_GENESIS_ROLLBACK_STOP_FAILED",
                    f"Coolify rejected the stop request with HTTP {stop['status']}",
                )
            stop_accepted = stop["status"] in {200, 201, 202}
            live_mutation_performed = live_mutation_performed or stop_accepted
            stop_receipt = {
                "ordinal": 1,
                "operation_id": f"{node}.stop-first-genesis",
                "status": "accepted" if stop_accepted else "not-accepted",
                "service_uuid": service_uuid,
                "request_accepted": stop_accepted,
                "response": {
                    "status": stop["status"],
                    "response_sha256": stop["response_sha256"],
                    "elapsed_ms": stop["elapsed_ms"],
                },
                "postcondition": {"verified": False, "deferred_until_after_restore": True},
            }
            receipts.append(stop_receipt)

            patch = _http_json(
                controller, "PATCH", f"/api/v1/services/{service_uuid}", body=restore_body,
                timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
            )
            if patch["status"] not in {200, 201, 202}:
                raise MotherDeploymentGenesisRollbackError(
                    "MOTHER_DEPLOY_GENESIS_ROLLBACK_RESTORE_FAILED",
                    f"Coolify rejected the standby Compose restore with HTTP {patch['status']}",
                )
            live_mutation_performed = True
            restore_receipt = {
                "ordinal": 2,
                "operation_id": f"{node}.restore-standby-compose",
                "status": "accepted",
                "service_uuid": service_uuid,
                "standby_compose_sha256": inspected["standby_compose_sha256"],
                "response": {
                    "status": patch["status"],
                    "response_sha256": patch["response_sha256"],
                    "elapsed_ms": patch["elapsed_ms"],
                },
                "compose_postcondition": {"verified": False, "pending": True},
                "stopped_postcondition": {"verified": False, "pending": True},
            }
            receipts.append(restore_receipt)

            stop_after = _http_json(
                controller, "GET", f"/api/v1/services/{service_uuid}/stop", body=None,
                timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
            )
            if stop_after["status"] not in {200, 201, 202, 400}:
                raise MotherDeploymentGenesisRollbackError(
                    "MOTHER_DEPLOY_GENESIS_ROLLBACK_STOP_FAILED",
                    f"Coolify rejected the post-restore stop request with HTTP {stop_after['status']}",
                )
            stop_after_accepted = stop_after["status"] in {200, 201, 202}
            live_mutation_performed = live_mutation_performed or stop_after_accepted
            stopped_after = _wait_stopped(
                controller, service_uuid=service_uuid, node=node,
                timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
                max_wait_seconds=max_wait_seconds, poll_interval_seconds=poll_interval_seconds,
                reassert_stop=True,
            )
            detail_after = _http_json(
                controller, "GET", f"/api/v1/services/{service_uuid}", body=None,
                timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
            )
            if not detail_after["ok"]:
                raise MotherDeploymentGenesisRollbackError(
                    "MOTHER_DEPLOY_GENESIS_ROLLBACK_OBSERVATION_FAILED",
                    f"Coolify service detail GET failed with HTTP {detail_after['status']}",
                )
            compose_proof = require_compose(detail_after["payload"], standby, label="standby Compose")
            stop_receipt["status"] = "succeeded"
            stop_receipt["postcondition"] = stopped_after
            restore_receipt["status"] = "succeeded"
            restore_receipt["post_restore_stop_response"] = {
                "status": stop_after["status"],
                "request_accepted": stop_after_accepted,
                "response_sha256": stop_after["response_sha256"],
                "elapsed_ms": stop_after["elapsed_ms"],
            }
            restore_receipt["compose_postcondition"] = compose_proof
            restore_receipt["stopped_postcondition"] = stopped_after
            service_stopped = True
            standby_compose_restored = True

        envs = _http_json(
            controller, "GET", f"/api/v1/services/{service_uuid}/envs", body=None,
            timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
        )
        if not envs["ok"]:
            raise MotherDeploymentGenesisRollbackError(
                "MOTHER_DEPLOY_GENESIS_ROLLBACK_IDENTITY_POSTCONDITION_FAILED",
                f"Coolify identity environment GET failed with HTTP {envs['status']}",
            )
        _verify_identity_keys(envs["payload"])
        identity_keys_preserved = True
        receipts.append({
            "ordinal": 3,
            "operation_id": f"{node}.verify-reserved-identities-preserved",
            "status": "succeeded",
            "service_uuid": service_uuid,
            "identity_key_count": 2,
            "response_sha256": envs["response_sha256"],
        })
    except Exception as exc:
        if isinstance(exc, MotherDeploymentGenesisRollbackError):
            failure = {"code": exc.code, "message": _safe_message(exc)}
        else:
            failure = {
                "code": "MOTHER_DEPLOY_GENESIS_ROLLBACK_UNEXPECTED_FAILURE",
                "message": _safe_message(exc),
            }

    complete = failure is None
    completed_at = _utc_now()
    result: dict[str, Any] = {
        "kind": _RESULT_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "pass" if complete else "failed",
        "network": inspected["network"],
        "mother_binding": _binding(private_state),
        "nodes": [node],
        "node": node,
        "controller_id": inspected["controller_id"],
        "service_uuid": service_uuid,
        "genesis_sha256": inspected["genesis_sha256"],
        "genesis_compose_sha256": inspected["genesis_compose_sha256"],
        "standby_compose_sha256": inspected["standby_compose_sha256"],
        "staged_scope": "install-and-start-first-genesis-on-initial-node",
        "journal": {
            "locator": Path(inspected["journal_path"]).resolve(strict=False).relative_to(
                paths.root.resolve(strict=False)
            ).as_posix(),
            "sha256": inspected["journal_sha256"],
        },
        "execution": dict(execution_binding) if execution_binding is not None else None,
        "authority": {
            "authorization_source": authorization_source,
            "idempotent_retry_allowed": True,
        },
        "rollback_frame": inspected["frame"],
        "deployment_cancellation_receipts": deployment_cancellation_receipts,
        "rollback_receipts": receipts,
        "failure": failure,
        "policy": {
            "allowed_http_methods": ["GET", "PATCH", "POST"],
            "exact_active_deployments_cancelled_before_stop": True,
            "exact_service_uuid_required": True,
            "exact_standby_compose_required": True,
            "reserved_identity_keys_preserved": True,
            "persistent_volume_cleanup_performed": False,
            "secrets_in_output": False,
        },
        "summary": {
            "complete": complete,
            "planned_operation_count": 2,
            "completed_operation_count": 2 if complete else sum(
                1
                for item in receipts
                if item.get("ordinal") in {1, 2}
                and item.get("status") in {"succeeded", "already-restored"}
            ),
            "postconditions_verified": complete,
            "service_stopped": service_stopped,
            "standby_compose_restored": standby_compose_restored,
            "identity_keys_preserved": identity_keys_preserved,
            "persistent_volume_cleanup_performed": False,
            "observed_active_deployment_count": len(deployment_cancellation_receipts),
            "cancelled_active_deployment_count": sum(
                1
                for item in deployment_cancellation_receipts
                if item.get("request_accepted") is True
            ),
            "live_mutation_performed": live_mutation_performed,
            "network_access_performed": True,
        },
    }
    stamp = re.sub(r"[^0-9A-Za-z]+", "", completed_at)[:32] or "genesisrollback"
    result_path, result_digest = _write_document(
        paths, _RESULT_DIRECTORY, result, operation=operation,
        suffix=f"{stamp}-{hashlib.sha256(canonical_json(result)).hexdigest()[:16]}.json",
    )
    result["result_artifact"] = {"path": str(result_path), "sha256": result_digest}
    return result


def _load_execution(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    acknowledged_execution_sha256: str,
) -> tuple[dict[str, Any], str, Path]:
    candidate = _beneath(paths, execution_path, _EXECUTION_DIRECTORY, label="genesis execution")
    execution, _, digest = _canonical_file(candidate, label="genesis execution")
    if digest != _sha256(acknowledged_execution_sha256, "acknowledged execution SHA-256"):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact genesis execution SHA-256",
        )
    if (
        execution.get("kind") != _EXECUTION_KIND
        or execution.get("status") != "pass"
        or execution.get("mother_binding") != _binding(private_state)
    ):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_EXECUTION_INVALID",
            "genesis execution is not a successful execution under the current Mother binding",
        )
    return execution, digest, candidate


def _downstream_birth_blockers(
    paths: PrivateStatePaths,
    *,
    execution_sha256: str,
    execution_completed_at: Any,
) -> list[dict[str, Any]]:
    completed = _parse_utc(execution_completed_at, "execution completed_at")
    root = _root(paths, _BIRTH_EVIDENCE_DIRECTORY)
    blockers: list[dict[str, Any]] = []
    if not root.exists():
        return blockers
    for path in root.glob("*.json"):
        try:
            evidence, _, digest = _canonical_file(path, label="genesis birth evidence")
        except MotherDeploymentGenesisRollbackError:
            continue
        binding = evidence.get("genesis_execution")
        observed = evidence.get("observed_at")
        if not isinstance(binding, Mapping) or binding.get("sha256") != execution_sha256:
            continue
        try:
            observed_at = _parse_utc(observed, "birth evidence observed_at")
        except MotherDeploymentGenesisRollbackError:
            continue
        if observed_at >= completed:
            blockers.append({
                "kind": evidence.get("kind"),
                "path": str(path),
                "sha256": digest,
                "observed_at": observed,
            })
    return blockers


def inspect_genesis_mutation_rollback(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    acknowledged_execution_sha256: str,
) -> dict[str, Any]:
    execution, execution_sha, candidate = _load_execution(
        paths,
        private_state,
        execution_path,
        acknowledged_execution_sha256=acknowledged_execution_sha256,
    )
    journal_binding = execution.get("rollback_journal")
    if not isinstance(journal_binding, Mapping):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_EXECUTION_INVALID",
            "genesis execution does not contain a rollback journal binding",
        )
    locator = journal_binding.get("locator")
    if type(locator) is not str:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_EXECUTION_INVALID",
            "genesis execution rollback journal locator is missing",
        )
    journal_path = (paths.root / Path(locator)).resolve(strict=False)
    inspected = inspect_genesis_rollback_journal(
        paths,
        private_state,
        journal_path,
        acknowledged_journal_sha256=_sha256(journal_binding.get("sha256"), "journal SHA-256"),
    )
    blockers = _downstream_birth_blockers(
        paths,
        execution_sha256=execution_sha,
        execution_completed_at=execution.get("completed_at"),
    )
    if blockers:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_BOUNDARY_CROSSED",
            "a successful genesis-birth proof exists for this execution",
        )
    return {
        "clean": True,
        "execution_path": str(candidate),
        "execution_sha256": execution_sha,
        "journal_path": inspected["journal_path"],
        "journal_sha256": inspected["journal_sha256"],
        "network": inspected["network"],
        "nodes": [inspected["node"]],
        "node": inspected["node"],
        "service_uuid": inspected["service_uuid"],
        "rollback_boundary_open": True,
        "downstream_success_blockers": [],
        "rollback_implemented": True,
        "rollback_operation_count": 2,
        "frame": inspected["frame"],
        "persistent_volume_cleanup_performed": False,
        "live_mutation_performed": False,
        "network_access_performed": False,
    }


def execute_genesis_mutation_rollback(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    acknowledged_execution_sha256: str,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = _DEFAULT_POST_MUTATION_WAIT_SECONDS,
    poll_interval_seconds: float = _DEFAULT_POLL_SECONDS,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_genesis_mutation_rollback(
        paths,
        private_state,
        execution_path,
        acknowledged_execution_sha256=acknowledged_execution_sha256,
    )
    return execute_genesis_journal_rollback(
        paths,
        private_state,
        Path(inspected["journal_path"]),
        acknowledged_journal_sha256=inspected["journal_sha256"],
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        max_wait_seconds=max_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        opener=opener,
        operation=operation,
        execution_binding={
            "locator": Path(inspected["execution_path"]).resolve(strict=False).relative_to(
                paths.root.resolve(strict=False)
            ).as_posix(),
            "sha256": inspected["execution_sha256"],
        },
        authorization_source="explicit-operator-execution-acknowledgement",
    )


def verify_genesis_mutation_rollback(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    rollback_result_path: Path,
    *,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    observed_at: str | None = None,
) -> dict[str, Any]:
    candidate = _beneath(paths, rollback_result_path, _RESULT_DIRECTORY, label="genesis rollback result")
    result, _, result_sha = _canonical_file(candidate, label="genesis rollback result")
    if (
        result.get("kind") != _RESULT_KIND
        or result.get("status") != "pass"
        or result.get("mother_binding") != _binding(private_state)
    ):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_RESULT_INVALID",
            "genesis rollback result is not a successful result under the current Mother binding",
        )
    journal_binding = result.get("journal")
    if not isinstance(journal_binding, Mapping):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_RESULT_INVALID", "rollback journal binding is missing"
        )
    journal_path = (paths.root / Path(str(journal_binding.get("locator")))).resolve(strict=False)
    journal, _, journal_sha = _canonical_file(journal_path, label="rollback journal")
    if journal_sha != journal_binding.get("sha256"):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_RESULT_INVALID", "rollback journal binding is invalid"
        )
    prestate = journal.get("prestate")
    if not isinstance(prestate, Mapping) or type(prestate.get("standby_compose")) is not str:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_RESULT_INVALID", "rollback journal prestate is invalid"
        )
    network = _identifier(result.get("network"), "network")
    node = _identifier(result.get("node"), "node")
    controller_id = _identifier(result.get("controller_id"), "controller_id")
    service_uuid = _identifier(result.get("service_uuid"), "service_uuid")
    controller = resolve_coolify_controller(
        private_state, network, controller_id, require_enabled=True, require_token=True
    )
    services = _http_json(
        controller, "GET", "/api/v1/services", body=None,
        timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
    )
    if not services["ok"]:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_VERIFICATION_FAILED",
            f"Coolify service inventory GET failed with HTTP {services['status']}",
        )
    record = _service_record(services["payload"], service_uuid=service_uuid, node=node)
    status = _item_text(record, "status")
    if not _stopped_status(status):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_VERIFICATION_FAILED",
            "the initial service is not stopped after rollback",
        )
    detail = _http_json(
        controller, "GET", f"/api/v1/services/{service_uuid}", body=None,
        timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
    )
    if not detail["ok"]:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_VERIFICATION_FAILED",
            f"Coolify service detail GET failed with HTTP {detail['status']}",
        )
    compose_proof = require_compose(
        detail["payload"], prestate["standby_compose"], label="standby Compose"
    )
    envs = _http_json(
        controller, "GET", f"/api/v1/services/{service_uuid}/envs", body=None,
        timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
    )
    if not envs["ok"]:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_VERIFICATION_FAILED",
            f"Coolify identity environment GET failed with HTTP {envs['status']}",
        )
    _verify_identity_keys(envs["payload"])
    verification: dict[str, Any] = {
        "kind": _VERIFICATION_KIND,
        "schema_version": 1,
        "observed_at": observed_at or _utc_now(),
        "network": network,
        "mother_binding": _binding(private_state),
        "nodes": [node],
        "node": node,
        "controller_id": controller_id,
        "service_uuid": service_uuid,
        "genesis_sha256": _sha256(result.get("genesis_sha256"), "genesis SHA-256"),
        "genesis_compose_sha256": _sha256(
            result.get("genesis_compose_sha256"), "genesis Compose SHA-256"
        ),
        "standby_compose_sha256": _sha256(
            result.get("standby_compose_sha256"), "standby Compose SHA-256"
        ),
        "rollback_result": {
            "locator": candidate.relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": result_sha,
        },
        "rolled_back_execution": result.get("execution"),
        "checks": {
            "service_stopped": True,
            "service_status": status,
            "standby_compose_restored": True,
            "standby_compose_sha256": prestate.get("standby_compose_sha256"),
            "compose_proof": compose_proof,
            "identity_keys_preserved": True,
            "identity_key_count": 2,
            "persistent_volume_cleanup_performed": False,
        },
        "clean": True,
        "summary": {
            "clean": True,
            "service_stopped": True,
            "standby_compose_restored": True,
            "identity_keys_preserved": True,
            "persistent_volume_cleanup_performed": False,
            "network_access_performed": True,
            "live_mutation_performed": False,
        },
    }
    verification["genesis_rollback_verification_sha256"] = hashlib.sha256(
        canonical_json(verification)
    ).hexdigest()
    return verification


def write_genesis_mutation_rollback_verification(
    paths: PrivateStatePaths,
    verification: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if verification.get("kind") != _VERIFICATION_KIND or verification.get("clean") is not True:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_VERIFICATION_INVALID",
            "only a clean genesis rollback verification may be persisted",
        )
    payload = canonical_json(dict(verification))
    digest = hashlib.sha256(payload).hexdigest()
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(verification.get("observed_at", "")))[:32] or "genesisrollback"
    root = _ensure_directory(paths, _VERIFICATION_DIRECTORY, operation=operation)
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentGenesisRollbackError(
                "MOTHER_DEPLOY_GENESIS_ROLLBACK_VERIFICATION_CONFLICT",
                "genesis rollback verification path contains different bytes",
            )
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
        _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest



def verify_genesis_rollback_cycle_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    verification_path: Path,
    *,
    network: str,
    node: str,
    service_uuid: str,
    genesis_sha256_value: str,
    before_execution_started_at: Any,
    current_execution_sha256: str,
) -> dict[str, Any]:
    candidate = _beneath(
        paths,
        verification_path,
        _VERIFICATION_DIRECTORY,
        label="genesis rollback verification",
    )
    verification, raw, byte_sha = _canonical_file(
        candidate, label="genesis rollback verification"
    )
    if (
        verification.get("kind") != _VERIFICATION_KIND
        or verification.get("clean") is not True
        or verification.get("mother_binding") != _binding(private_state)
    ):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_CYCLE_INVALID",
            "genesis rollback verification is not clean under the current Mother binding",
        )
    expected_network = _identifier(network, "network")
    expected_node = _identifier(node, "node")
    expected_service_uuid = _identifier(service_uuid, "service_uuid")
    expected_genesis_sha = _sha256(genesis_sha256_value, "genesis SHA-256")
    current_execution_sha = _sha256(
        current_execution_sha256, "current genesis execution SHA-256"
    )
    if verification.get("network") != expected_network:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_CYCLE_INVALID",
            "genesis rollback verification network does not match",
        )
    if verification.get("node") != expected_node or verification.get("service_uuid") != expected_service_uuid:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_CYCLE_INVALID",
            "genesis rollback verification target does not match",
        )
    if verification.get("genesis_sha256") != expected_genesis_sha:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_CYCLE_INVALID",
            "genesis rollback verification belongs to a different genesis profile",
        )
    rolled_back = verification.get("rolled_back_execution")
    if not isinstance(rolled_back, Mapping):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_CYCLE_INVALID",
            "rolled-back genesis execution binding is missing",
        )
    rolled_back_sha = _sha256(
        rolled_back.get("sha256"), "rolled-back genesis execution SHA-256"
    )
    if rolled_back_sha == current_execution_sha:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_REAPPLICATION_REQUIRED",
            "genesis birth requires a distinct successful genesis reapplication after rollback",
        )
    observed_at = _parse_utc(verification.get("observed_at"), "rollback observed_at")
    reapplied_at = _parse_utc(
        before_execution_started_at, "current execution started_at"
    )
    if reapplied_at < observed_at:
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_REAPPLICATION_REQUIRED",
            "current genesis execution started before verified rollback",
        )
    checks = verification.get("checks")
    if not isinstance(checks, Mapping) or not all(
        [
            checks.get("service_stopped") is True,
            checks.get("standby_compose_restored") is True,
            checks.get("identity_keys_preserved") is True,
        ]
    ):
        raise MotherDeploymentGenesisRollbackError(
            "MOTHER_DEPLOY_GENESIS_ROLLBACK_CYCLE_INVALID",
            "genesis rollback verification does not prove the required postconditions",
        )
    return {
        "clean": True,
        "verification_path": str(candidate),
        "verification_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_sha256": byte_sha,
        "genesis_rollback_verification_sha256": _sha256(
            verification.get("genesis_rollback_verification_sha256"),
            "genesis rollback verification SHA-256",
        ),
        "network": expected_network,
        "node": expected_node,
        "service_uuid": expected_service_uuid,
        "genesis_sha256": expected_genesis_sha,
        "rolled_back_execution_sha256": rolled_back_sha,
        "current_execution_sha256": current_execution_sha,
        "observed_at": verification.get("observed_at"),
        "reapplied_at": before_execution_started_at,
        "persistent_volume_cleanup_performed": False,
    }


__all__ = [
    "MotherDeploymentGenesisRollbackError",
    "build_genesis_rollback_journal",
    "compose_matches",
    "execute_genesis_journal_rollback",
    "execute_genesis_mutation_rollback",
    "genesis_rollback_journal_path",
    "inspect_genesis_mutation_rollback",
    "inspect_genesis_rollback_journal",
    "require_compose",
    "update_genesis_rollback_journal",
    "verify_genesis_mutation_rollback",
    "verify_genesis_rollback_cycle_evidence",
    "write_genesis_mutation_rollback_verification",
    "write_genesis_rollback_journal",
]
