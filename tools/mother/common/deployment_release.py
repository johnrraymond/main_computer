"""Explicit, expiring operator release for one staged deployment transaction.

This is an operation-safety artifact, not a second authentication system.  The
Coolify API credential remains the remote authorization boundary.  A release
records deliberate operator intent for one exact transaction digest, node
sequence, Mother generation, and bounded lifetime.  It performs no network
access and no live mutation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any

from . import atomic_files
from .canonical import canonical_json
from .deployment_transaction import verify_deployment_mutation_transaction
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_RELEASE_KIND = "main_computer.mother.deployment_mutation_release.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-releases")
_TRANSACTION_DIRECTORY = ("actions", "deployment-transactions")
_MAX_RELEASE_SECONDS = 900
_MIN_RELEASE_SECONDS = 30


class MotherDeploymentReleaseError(RuntimeError):
    """A bounded deployment release could not be created or verified."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            f"{path} must be a non-empty string",
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            f"{path} is not a safe identifier",
        )
    return text


def _sha256(value: Any, path: str) -> str:
    text = _identifier(value, path).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            f"{path} must be a lowercase SHA-256 digest",
        )
    return text


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            f"{path} must be a UTC timestamp",
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            f"{path} is malformed",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            f"{path} must be UTC",
        )
    return parsed.astimezone(timezone.utc)


def _utc_timestamp(value: Any, path: str) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    else:
        parsed = _parse_utc(value, path)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _duration(value: Any) -> int:
    if type(value) is not int or isinstance(value, bool):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            "expires_in_seconds must be an integer",
        )
    if value < _MIN_RELEASE_SECONDS or value > _MAX_RELEASE_SECONDS:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_TTL_INVALID",
            f"expires_in_seconds must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}",
        )
    return value


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


def _digest_without(value: Mapping[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _release_root(paths: PrivateStatePaths) -> Path:
    return paths.root / _RELEASE_DIRECTORY[0] / _RELEASE_DIRECTORY[1]


def _transaction_root(paths: PrivateStatePaths) -> Path:
    return paths.root / _TRANSACTION_DIRECTORY[0] / _TRANSACTION_DIRECTORY[1]


def _relative_locator(paths: PrivateStatePaths, candidate: Path, *, label: str) -> str:
    root = paths.root.resolve(strict=False)
    resolved = Path(candidate).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_PATH_UNSAFE",
            f"{label} must be beneath the canonical Mother root",
        ) from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            f"{label} locator must be a relative POSIX path",
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_PATH_UNSAFE",
            f"{label} locator is unsafe",
        )
    resolved = (paths.root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_PATH_UNSAFE",
            f"{label} locator escapes Mother state",
        ) from exc
    return resolved


def _load_canonical_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            f"{label} could not be read as canonical JSON",
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            f"{label} is not canonical JSON",
        )
    return value, raw


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _remaining_blockers(transaction: Mapping[str, Any]) -> list[dict[str, Any]]:
    blockers = transaction.get("remaining_global_blockers")
    if type(blockers) is not list:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            "transaction remaining_global_blockers must be a list",
        )
    output: list[dict[str, Any]] = []
    for blocker in blockers:
        if not isinstance(blocker, Mapping):
            raise MotherDeploymentReleaseError(
                "MOTHER_DEPLOY_RELEASE_INVALID",
                "transaction contains an invalid global blocker",
            )
        code = _identifier(blocker.get("code"), "global blocker code")
        if code == "MOTHER_DEPLOY_MUTATION_AUTHORITY_DISABLED":
            continue
        output.append(dict(blocker))
    return output


def build_deployment_mutation_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_transaction_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record deliberate operator intent for one exact staged transaction."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    acknowledged = _sha256(acknowledged_transaction_sha256, "acknowledged_transaction_sha256")
    lifetime = _duration(expires_in_seconds)

    verified = verify_deployment_mutation_transaction(
        paths,
        private_state,
        Path(transaction_path),
        max_age_seconds=max_age_seconds,
        selected_nodes=requested_nodes,
        now=now,
    )
    if acknowledged != verified["transaction_sha256"]:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact transaction SHA-256",
        )

    candidate = Path(verified["transaction_path"])
    transaction, raw = _load_canonical_json(candidate, label="staged transaction")
    created_text = _utc_timestamp(created_at, "created_at")
    created = _parse_utc(created_text, "created_at")
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if created > reference_now + timedelta(seconds=1):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            "release creation time is in the future",
        )
    expires = created + timedelta(seconds=lifetime)

    remaining = _remaining_blockers(transaction)
    blocker_codes = sorted(
        _identifier(item.get("code"), "remaining blocker code")
        for item in remaining
    )
    if blocker_codes != ["MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED"]:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_UNEXPECTED_BLOCKERS",
            "transaction has unresolved blockers outside the expected executor boundary",
        )

    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "network": verified["network"],
        "mother_binding": dict(verified["mother_binding"]),
        "transaction": {
            "locator": _relative_locator(paths, candidate, label="staged transaction"),
            "sha256": verified["transaction_sha256"],
            "byte_sha256": hashlib.sha256(raw).hexdigest(),
            "created_at": transaction.get("created_at"),
        },
        "nodes": list(verified["nodes"]),
        "staged_scope": verified["staged_scope"],
        "operator_release": {
            "acknowledged_transaction_sha256": acknowledged,
            "intent": "apply-exact-bound-transaction",
            "requested_use_limit": 1,
        },
        "authority": {
            "identity_default": "observe-only",
            "authorization_source": "explicit-operator-release",
            "coolify_api_credential_required": True,
            "independent_authentication_system_created": False,
            "transaction_apply_authorized": True,
            "live_execution_authorized": False,
        },
        "policy": {
            "authoritative_prep_completed": False,
            "executor_implemented": False,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "legacy_allfather_executor_invoked": False,
            "legacy_qbft_executor_invoked": False,
            "secrets_in_output": False,
            "release_consumption_receipt_required": True,
            "consumption_enforcement_implemented": False,
        },
        "resolved_blocker_codes": ["MOTHER_DEPLOY_MUTATION_AUTHORITY_DISABLED"],
        "remaining_global_blockers": remaining,
        "summary": {
            "release_valid": True,
            "transaction_apply_authorized": True,
            "live_execution_authorized": False,
            "target_count": len(verified["nodes"]),
            "mutation_count": verified["mutation_count"],
            "remaining_blocker_codes": blocker_codes,
        },
    }
    if _contains_sensitive_key(release):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            "release contains a sensitive field",
        )
    release["release_sha256"] = _digest_without(release, "release_sha256")
    return release


def write_deployment_mutation_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    """Persist one canonical release immutably beneath Mother actions."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    payload_object = dict(release)
    digest = _digest_without(payload_object, "release_sha256")
    if (
        payload_object.get("kind") != _RELEASE_KIND
        or payload_object.get("release_sha256") != digest
        or _contains_sensitive_key(payload_object)
    ):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            "release is malformed, unbound, or sensitive",
        )
    payload = canonical_json(payload_object)
    root = _release_root(paths)
    current = paths.root
    for part in _RELEASE_DIRECTORY:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(payload_object.get("created_at", "")))[:32] or "release"
    network = _identifier(payload_object.get("network"), "network")
    destination = root / f"{stamp}-{network}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentReleaseError(
                "MOTHER_DEPLOY_RELEASE_CONFLICT",
                "release destination already contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_WRITE_FAILED",
            "release reread mismatch",
        )
    return destination, digest


def verify_deployment_mutation_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    max_age_seconds: int = 300,
    selected_nodes: Iterable[str] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a release against its transaction, lifetime, and Mother binding."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    root = _release_root(paths).resolve(strict=False)
    candidate = Path(release_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_PATH_UNSAFE",
            "release must be beneath the canonical release root",
        ) from exc

    release, raw = _load_canonical_json(candidate, label="deployment release")
    if (
        release.get("kind") != _RELEASE_KIND
        or _contains_sensitive_key(release)
        or release.get("release_sha256") != _digest_without(release, "release_sha256")
    ):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            "release is modified, unbound, or sensitive",
        )
    expected_binding = _binding(private_state)
    if release.get("mother_binding") != expected_binding:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_STALE_BINDING",
            "release does not bind the current Mother generation",
        )

    created = _parse_utc(release.get("created_at"), "created_at")
    expires = _parse_utc(release.get("expires_at"), "expires_at")
    lifetime = int((expires - created).total_seconds())
    _duration(lifetime)
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if created > reference_now + timedelta(seconds=1):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            "release creation time is in the future",
        )
    if reference_now > expires:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_EXPIRED",
            "deployment release has expired",
        )

    transaction_binding = release.get("transaction")
    if not isinstance(transaction_binding, Mapping):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            "transaction binding is missing",
        )
    transaction_path = _resolve_locator(paths, transaction_binding.get("locator"), label="staged transaction")
    try:
        transaction_path.resolve(strict=False).relative_to(_transaction_root(paths).resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_PATH_UNSAFE",
            "bound transaction is outside the canonical transaction root",
        ) from exc
    transaction_bytes = transaction_path.read_bytes()
    if hashlib.sha256(transaction_bytes).hexdigest() != transaction_binding.get("byte_sha256"):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_TRANSACTION_MISMATCH",
            "bound transaction bytes no longer match",
        )

    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    verified = verify_deployment_mutation_transaction(
        paths,
        private_state,
        transaction_path,
        max_age_seconds=max_age_seconds,
        selected_nodes=requested_nodes,
        now=reference_now,
    )
    if verified["transaction_sha256"] != transaction_binding.get("sha256"):
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_TRANSACTION_MISMATCH",
            "bound transaction digest no longer matches",
        )
    acknowledgement = release.get("operator_release")
    if not isinstance(acknowledgement, Mapping) or acknowledgement.get("acknowledged_transaction_sha256") != verified["transaction_sha256"]:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_ACKNOWLEDGEMENT_MISMATCH",
            "release acknowledgement no longer matches the transaction",
        )
    if acknowledgement.get("intent") != "apply-exact-bound-transaction" or acknowledgement.get("requested_use_limit") != 1:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_INVALID",
            "operator release intent is malformed",
        )

    rebuilt = build_deployment_mutation_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=verified["transaction_sha256"],
        selected_nodes=tuple(verified["nodes"]),
        max_age_seconds=max_age_seconds,
        expires_in_seconds=lifetime,
        created_at=release.get("created_at"),
        now=reference_now,
    )
    if rebuilt != release:
        raise MotherDeploymentReleaseError(
            "MOTHER_DEPLOY_RELEASE_MISMATCH",
            "release no longer matches the exact transaction and policy",
        )

    return {
        "clean": True,
        "release_path": str(candidate),
        "release_sha256": release["release_sha256"],
        "byte_sha256": hashlib.sha256(raw).hexdigest(),
        "created_at": release["created_at"],
        "expires_at": release["expires_at"],
        "mother_binding": expected_binding,
        "network": release["network"],
        "nodes": list(verified["nodes"]),
        "mutation_count": verified["mutation_count"],
        "staged_scope": verified["staged_scope"],
        "transaction_sha256": verified["transaction_sha256"],
        "transaction_apply_authorized": True,
        "live_execution_authorized": False,
        "remaining_blocker_codes": ["MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED"],
        "network_access_performed": False,
        "live_mutation_performed": False,
    }


__all__ = [
    "MotherDeploymentReleaseError",
    "build_deployment_mutation_release",
    "verify_deployment_mutation_release",
    "write_deployment_mutation_release",
]
