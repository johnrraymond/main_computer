"""Explicit expiring release for one exact private non-validator RPC transaction."""

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
from .deployment_private_rpc import verify_private_rpc_transaction
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_RELEASE_KIND = "main_computer.mother.deployment_private_rpc_release.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-private-rpc-releases")
_TRANSACTION_DIRECTORY = ("actions", "deployment-private-rpc-transactions")
_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900


class MotherDeploymentPrivateRpcReleaseError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error(code: str, message: str) -> MotherDeploymentPrivateRpcReleaseError:
    return MotherDeploymentPrivateRpcReleaseError(code, message)


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            f"{path} must be SHA-256",
        )
    return value


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            f"{path} must be UTC",
        )
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            f"{path} is malformed",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            f"{path} must be UTC",
        )
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(
        value,
        "created_at",
    )
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _duration(value: int) -> int:
    if (
        type(value) is not int
        or isinstance(value, bool)
        or not _MIN_RELEASE_SECONDS <= value <= _MAX_RELEASE_SECONDS
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_TTL_INVALID",
            (
                "expires_in_seconds must be between "
                f"{_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}"
            ),
        )
    return value


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _contains_sensitive(value: Any) -> bool:
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
            str(key).lower() in forbidden or _contains_sensitive(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    return False


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    value = dict(document)
    value.pop(field, None)
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _canonical(path: Path, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            f"{label} is unreadable",
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            f"{label} is not canonical JSON",
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _relative(paths: PrivateStatePaths, path: Path, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(
            paths.root.resolve(strict=False)
        ).as_posix()
    except ValueError as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_PATH_UNSAFE",
            f"{label} is outside Mother state",
        ) from exc


def _resolve(
    paths: PrivateStatePaths,
    locator: Any,
    directory: tuple[str, str],
    label: str,
) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_PATH_UNSAFE",
            f"{label} locator is unsafe",
        )
    candidate = Path(locator)
    if (
        candidate.is_absolute()
        or PureWindowsPath(locator).is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_PATH_UNSAFE",
            f"{label} locator is unsafe",
        )
    result = (paths.root / candidate).resolve(strict=False)
    expected = (paths.root / directory[0] / directory[1]).resolve(strict=False)
    try:
        result.relative_to(expected)
    except ValueError as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_PATH_UNSAFE",
            f"{label} is outside its canonical directory",
        ) from exc
    return result


def _ensure_root(
    paths: PrivateStatePaths,
    operation: OperationIdentity,
) -> Path:
    current = paths.root
    for part in _RELEASE_DIRECTORY:
        current /= part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def build_private_rpc_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_transaction_sha256: str,
    selected_nodes: Iterable[str] = (),
    transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
) -> dict[str, Any]:
    acknowledged = _sha256(
        acknowledged_transaction_sha256,
        "acknowledged transaction SHA-256",
    )
    verified = verify_private_rpc_transaction(
        paths,
        private_state,
        Path(transaction_path),
        selected_nodes=selected_nodes,
        max_age_seconds=transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
    )
    if acknowledged != verified["transaction_sha256"]:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact transaction",
        )

    canonical_transaction_path = Path(verified["transaction_path"])
    transaction, _, transaction_file_sha = _canonical(
        canonical_transaction_path,
        "private RPC transaction",
    )
    if transaction_file_sha != verified["transaction_file_sha256"]:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            "transaction file digest mismatch",
        )

    placement = transaction.get("placement")
    identity = transaction.get("identity")
    chain = transaction.get("chain")
    validator_peers = transaction.get("validator_peers")
    compose = transaction.get("compose")
    secret_bindings = transaction.get("required_secret_bindings")
    execution_plan = transaction.get("execution_plan")
    authority = transaction.get("authority")
    summary = transaction.get("summary")
    if not all(
        isinstance(item, Mapping)
        for item in (
            placement,
            identity,
            chain,
            validator_peers,
            compose,
            execution_plan,
            authority,
            summary,
        )
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            "transaction lacks an exact private RPC plan",
        )
    mutations = execution_plan.get("mutations")
    preconditions = execution_plan.get("preconditions")
    if (
        type(mutations) is not list
        or len(mutations) != 2
        or type(preconditions) is not list
        or type(secret_bindings) is not list
        or len(secret_bindings) != 1
        or authority.get("release_authorized") is not False
        or authority.get("live_execution_authorized") is not False
        or summary.get("mutation_count") != 2
        or summary.get("validator_mutation_count") != 0
        or placement.get("public_endpoint") is not None
        or placement.get("host_rpc_port") is not None
        or placement.get("host_p2p_port") is not None
        or identity.get("validator_identity") is not False
        or identity.get("private_key_material_in_transaction") is not False
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            "transaction authority or privacy boundary is not exact",
        )

    created_text = _timestamp(created_at)
    created = _parse_utc(created_text, "created_at")
    expires = created + timedelta(seconds=_duration(expires_in_seconds))
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "network": transaction["network"],
        "mother_binding": _binding(private_state),
        "staged_scope": "release-private-non-validator-rpc",
        "transaction": {
            "locator": _relative(
                paths,
                canonical_transaction_path,
                "private RPC transaction",
            ),
            "sha256": verified["transaction_sha256"],
            "file_sha256": transaction_file_sha,
        },
        "chain": dict(chain),
        "placement": dict(placement),
        "identity": dict(identity),
        "validator_peers": dict(validator_peers),
        "compose": dict(compose),
        "required_secret_bindings": [dict(item) for item in secret_bindings],
        "execution_plan": {
            "mutations": [dict(item) for item in mutations],
            "preconditions": list(preconditions),
        },
        "authority": {
            "private_rpc_service_create_authorized": True,
            "private_rpc_service_deploy_authorized": True,
            "secret_value_materialization_authorized": False,
            "validator_vote_authorized": False,
            "validator_identity_authorized": False,
            "validator_mutation_authorized": False,
            "public_endpoint_authorized": False,
            "host_port_authorized": False,
            "ssh_authorized": False,
            "requested_use_limit": 1,
        },
        "policy": {
            "non_validator_rpc_only": True,
            "existing_validators_read_only": True,
            "private_rpc_only": True,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_port_created": False,
            "private_key_material_in_release": False,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "validator_vote_performed": False,
            "validator_mutation_performed": False,
        },
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_PRIVATE_RPC_EXECUTOR_NOT_IMPLEMENTED",
                "message": (
                    "a one-use private RPC executor must consume this exact release "
                    "and bind the protected node key outside the artifact"
                ),
            }
        ],
        "summary": {
            "release_valid": True,
            "service_name": placement["service_name"],
            "controller_id": placement["controller_id"],
            "mutation_count": 2,
            "validator_mutation_count": 0,
            "public_endpoint_count": 0,
            "host_port_count": 0,
            "non_validator_rpc_only": True,
            "next_phase": "verify-private-rpc-release",
            "next_phase_after_executor": "prove-private-rpc-synchronization",
        },
    }
    if _contains_sensitive(release):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            "release contains sensitive material",
        )
    release["private_rpc_release_sha256"] = _digest_without(
        release,
        "private_rpc_release_sha256",
    )
    return release


def write_private_rpc_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(document, "private_rpc_release_sha256")
    if (
        document.get("kind") != _RELEASE_KIND
        or document.get("private_rpc_release_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            "private RPC release is malformed or sensitive",
        )
    payload = canonical_json(document)
    root = _ensure_root(paths, operation)
    stamp = (
        re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32]
        or "privaterpcrelease"
    )
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _error(
                "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_CONFLICT",
                "release destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_WRITE_FAILED",
            "release reread mismatch",
        )
    return destination, digest


def verify_private_rpc_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    candidate = Path(release_path).resolve(strict=False)
    expected_root = (
        paths.root / _RELEASE_DIRECTORY[0] / _RELEASE_DIRECTORY[1]
    ).resolve(strict=False)
    try:
        candidate.relative_to(expected_root)
    except ValueError as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_PATH_UNSAFE",
            "release is outside its canonical directory",
        ) from exc

    document, raw, file_sha = _canonical(candidate, "private RPC release")
    digest = _digest_without(document, "private_rpc_release_sha256")
    if (
        document.get("kind") != _RELEASE_KIND
        or document.get("schema_version") != 1
        or document.get("private_rpc_release_sha256") != digest
        or document.get("mother_binding") != _binding(private_state)
        or _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            "release is modified, stale, or sensitive",
        )

    reference = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    created = _parse_utc(document.get("created_at"), "created_at")
    expires = _parse_utc(document.get("expires_at"), "expires_at")
    age = int((reference - created).total_seconds())
    if (
        reference < created - timedelta(seconds=1)
        or reference > expires
        or age > max_age_seconds
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_EXPIRED",
            "release is outside its authority window",
        )

    transaction_ref = document.get("transaction")
    if not isinstance(transaction_ref, Mapping):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            "transaction binding is missing",
        )
    transaction_path = _resolve(
        paths,
        transaction_ref.get("locator"),
        _TRANSACTION_DIRECTORY,
        "private RPC transaction",
    )
    expected = build_private_rpc_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=_sha256(
            transaction_ref.get("sha256"),
            "transaction SHA-256",
        ),
        selected_nodes=selected_nodes,
        transaction_max_age_seconds=transaction_max_age_seconds,
        soak_max_age_seconds=soak_max_age_seconds,
        expires_in_seconds=int((expires - created).total_seconds()),
        created_at=document.get("created_at"),
    )
    if canonical_json(expected) != raw:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID",
            "release no longer matches its exact transaction",
        )

    placement = document["placement"]
    identity = document["identity"]
    return {
        "clean": True,
        "network": document["network"],
        "release_path": str(candidate),
        "private_rpc_release_sha256": digest,
        "release_file_sha256": file_sha,
        "transaction_sha256": transaction_ref["sha256"],
        "service_name": placement["service_name"],
        "controller_id": placement["controller_id"],
        "private_rpc_url_after_deployment": placement[
            "private_rpc_url_after_deployment"
        ],
        "rpc_node_id": identity["expected_node_id"],
        "rpc_node_address": identity["expected_node_address"],
        "chain_id": document["chain"]["chain_id"],
        "genesis_sha256": document["chain"]["genesis_sha256"],
        "validator_set": list(document["chain"]["validator_set"]),
        "validator_peer_count": document["validator_peers"]["minimum_peer_count"],
        "mutation_count": 2,
        "validator_mutation_count": 0,
        "public_endpoint_count": 0,
        "host_port_count": 0,
        "private_rpc_service_create_authorized": True,
        "private_rpc_service_deploy_authorized": True,
        "requested_use_limit": 1,
        "created_at": document["created_at"],
        "expires_at": document["expires_at"],
        "age_seconds": age,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
        "live_execution_available": False,
        "remaining_blocker_codes": [
            "MOTHER_DEPLOY_PRIVATE_RPC_EXECUTOR_NOT_IMPLEMENTED"
        ],
        "next_phase": "private-rpc-executor-not-yet-implemented",
    }


__all__ = [
    "MotherDeploymentPrivateRpcReleaseError",
    "build_private_rpc_release",
    "verify_private_rpc_release",
    "write_private_rpc_release",
]
