"""Explicit expiring release for one exact reserved-identity transaction.

The release records deliberate operator intent for one secret-safe staged
identity transaction.  It creates no second authentication system, performs no
network access, and never materializes private keys.
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
from .deployment_identity_install import verify_deployment_identity_install_transaction
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_RELEASE_KIND = "main_computer.mother.deployment_identity_release.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-identity-releases")
_TRANSACTION_DIRECTORY = ("actions", "deployment-identity-transactions")
_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900


class MotherDeploymentIdentityReleaseError(RuntimeError):
    """An identity transaction release failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            f"{path} must be a non-empty string",
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            f"{path} is not a safe identifier",
        )
    return text


def _sha256(value: Any, path: str) -> str:
    text = _identifier(value, path).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            f"{path} must be a lowercase SHA-256 digest",
        )
    return text


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            f"{path} must be a UTC timestamp",
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            f"{path} is malformed",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            f"{path} must be UTC",
        )
    return parsed.astimezone(timezone.utc)


def _utc_timestamp(value: Any, path: str) -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(value, path)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _duration(value: Any) -> int:
    if type(value) is not int or isinstance(value, bool):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            "expires_in_seconds must be an integer",
        )
    if value < _MIN_RELEASE_SECONDS or value > _MAX_RELEASE_SECONDS:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_TTL_INVALID",
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


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


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
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_PATH_UNSAFE",
            f"{label} must be beneath the canonical Mother root",
        ) from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            f"{label} locator must be a relative POSIX path",
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_PATH_UNSAFE",
            f"{label} locator is unsafe",
        )
    resolved = (paths.root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_PATH_UNSAFE",
            f"{label} locator escapes Mother state",
        ) from exc
    return resolved


def _load_canonical_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            f"{label} could not be read as canonical JSON",
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            f"{label} is not canonical JSON",
        )
    return value, raw


def build_deployment_identity_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_identity_transaction_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Release one exact secret-safe identity transaction for one use."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be PrivateStateReadResult")
    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    acknowledged = _sha256(
        acknowledged_identity_transaction_sha256,
        "acknowledged_identity_transaction_sha256",
    )
    lifetime = _duration(expires_in_seconds)
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)

    verified = verify_deployment_identity_install_transaction(
        paths,
        private_state,
        Path(transaction_path),
        selected_nodes=requested_nodes,
        max_age_seconds=max_age_seconds,
        now=reference_now,
    )
    if acknowledged != verified["identity_transaction_sha256"]:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact identity transaction SHA-256",
        )

    candidate = Path(verified["transaction_path"])
    transaction, raw = _load_canonical_json(candidate, label="identity transaction")
    created_text = _utc_timestamp(created_at, "created_at")
    created = _parse_utc(created_text, "created_at")
    if created > reference_now + timedelta(seconds=1):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            "identity release creation time is in the future",
        )
    expires_at = (created + timedelta(seconds=lifetime)).isoformat(timespec="seconds").replace("+00:00", "Z")

    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires_at,
        "network": verified["network"],
        "mother_binding": _binding(private_state),
        "identity_transaction": {
            "locator": _relative_locator(paths, candidate, label="identity transaction"),
            "sha256": verified["identity_transaction_sha256"],
            "byte_sha256": hashlib.sha256(raw).hexdigest(),
        },
        "nodes": list(verified["nodes"]),
        "staged_scope": verified["staged_scope"],
        "operator_release": {
            "acknowledged_identity_transaction_sha256": verified["identity_transaction_sha256"],
            "intent": "install-exact-reserved-identity-transaction",
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
            "secret_values_materialized": False,
            "secret_values_persisted": False,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "service_deploy_or_start_performed": False,
            "release_consumption_receipt_required": True,
            "consumption_enforcement_implemented": False,
            "secrets_in_output": False,
        },
        "resolved_blocker_codes": ["MOTHER_DEPLOY_IDENTITY_RELEASE_REQUIRED"],
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_IDENTITY_EXECUTOR_NOT_IMPLEMENTED",
                "message": "the one-use in-memory identity executor must consume this release",
            }
        ],
        "summary": {
            "release_valid": True,
            "target_count": len(verified["nodes"]),
            "mutation_count": verified["mutation_count"],
            "secret_reference_count": verified["secret_reference_count"],
            "persisted_secret_value_count": 0,
            "transaction_apply_authorized": True,
            "live_execution_authorized": False,
            "remaining_blocker_codes": ["MOTHER_DEPLOY_IDENTITY_EXECUTOR_NOT_IMPLEMENTED"],
        },
    }
    if _contains_sensitive_key(release):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            "identity release contains a sensitive field",
        )
    release["identity_release_sha256"] = _digest_without(release, "identity_release_sha256")
    return release


def write_deployment_identity_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be OperationIdentity")
    document = dict(release)
    digest = _digest_without(document, "identity_release_sha256")
    if (
        document.get("kind") != _RELEASE_KIND
        or document.get("identity_release_sha256") != digest
        or _contains_sensitive_key(document)
    ):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            "identity release is malformed, unbound, or sensitive",
        )
    payload = canonical_json(document)
    current = paths.root
    for part in _RELEASE_DIRECTORY:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "identityrelease"
    network = _identifier(document.get("network"), "network")
    destination = _release_root(paths) / f"{stamp}-{network}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentIdentityReleaseError(
                "MOTHER_DEPLOY_IDENTITY_RELEASE_CONFLICT",
                "identity release destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_WRITE_FAILED",
            "identity release reread mismatch",
        )
    return destination, digest


def verify_deployment_identity_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be PrivateStateReadResult")
    root = _release_root(paths).resolve(strict=False)
    candidate = Path(release_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_PATH_UNSAFE",
            "identity release must be beneath the canonical release root",
        ) from exc

    release, raw = _load_canonical_json(candidate, label="identity release")
    if (
        release.get("kind") != _RELEASE_KIND
        or release.get("identity_release_sha256") != _digest_without(release, "identity_release_sha256")
        or _contains_sensitive_key(release)
    ):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            "identity release is modified, unbound, or sensitive",
        )
    expected_binding = _binding(private_state)
    if release.get("mother_binding") != expected_binding:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_STALE_BINDING",
            "identity release does not bind the current Mother generation",
        )

    created = _parse_utc(release.get("created_at"), "created_at")
    expires = _parse_utc(release.get("expires_at"), "expires_at")
    lifetime = int((expires - created).total_seconds())
    _duration(lifetime)
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if created > reference_now + timedelta(seconds=1):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            "identity release creation time is in the future",
        )
    if reference_now > expires:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_EXPIRED",
            "identity release has expired",
        )

    binding = release.get("identity_transaction")
    if not isinstance(binding, Mapping):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            "identity transaction binding is missing",
        )
    transaction_path = _resolve_locator(paths, binding.get("locator"), label="identity transaction")
    try:
        transaction_path.relative_to(_transaction_root(paths).resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_PATH_UNSAFE",
            "bound identity transaction is outside the canonical transaction root",
        ) from exc
    transaction_raw = transaction_path.read_bytes()
    if hashlib.sha256(transaction_raw).hexdigest() != binding.get("byte_sha256"):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_TRANSACTION_MISMATCH",
            "bound identity transaction bytes no longer match",
        )

    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    verified = verify_deployment_identity_install_transaction(
        paths,
        private_state,
        transaction_path,
        selected_nodes=requested_nodes,
        max_age_seconds=max_age_seconds,
        now=reference_now,
    )
    if verified["identity_transaction_sha256"] != binding.get("sha256"):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_TRANSACTION_MISMATCH",
            "bound identity transaction digest no longer matches",
        )
    acknowledgement = release.get("operator_release")
    if not isinstance(acknowledgement, Mapping):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            "operator release acknowledgement is missing",
        )
    if acknowledgement.get("acknowledged_identity_transaction_sha256") != verified["identity_transaction_sha256"]:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_ACKNOWLEDGEMENT_MISMATCH",
            "identity release acknowledgement no longer matches the transaction",
        )
    if (
        acknowledgement.get("intent") != "install-exact-reserved-identity-transaction"
        or acknowledgement.get("requested_use_limit") != 1
    ):
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_INVALID",
            "identity release intent is malformed",
        )

    rebuilt = build_deployment_identity_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_identity_transaction_sha256=verified["identity_transaction_sha256"],
        selected_nodes=tuple(verified["nodes"]),
        max_age_seconds=max_age_seconds,
        expires_in_seconds=lifetime,
        created_at=release.get("created_at"),
        now=reference_now,
    )
    if rebuilt != release:
        raise MotherDeploymentIdentityReleaseError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_MISMATCH",
            "identity release no longer matches the exact transaction and policy",
        )

    return {
        "clean": True,
        "release_path": str(candidate),
        "identity_release_sha256": release["identity_release_sha256"],
        "byte_sha256": hashlib.sha256(raw).hexdigest(),
        "created_at": release["created_at"],
        "expires_at": release["expires_at"],
        "mother_binding": expected_binding,
        "network": release["network"],
        "nodes": list(verified["nodes"]),
        "mutation_count": verified["mutation_count"],
        "secret_reference_count": verified["secret_reference_count"],
        "persisted_secret_value_count": 0,
        "staged_scope": verified["staged_scope"],
        "identity_transaction_sha256": verified["identity_transaction_sha256"],
        "transaction_apply_authorized": True,
        "live_execution_authorized": False,
        "remaining_blocker_codes": ["MOTHER_DEPLOY_IDENTITY_EXECUTOR_NOT_IMPLEMENTED"],
        "network_access_performed": False,
        "live_mutation_performed": False,
    }


__all__ = [
    "MotherDeploymentIdentityReleaseError",
    "build_deployment_identity_release",
    "verify_deployment_identity_release",
    "write_deployment_identity_release",
]
