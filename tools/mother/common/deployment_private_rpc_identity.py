"""Offline reservation of a dedicated private non-validator RPC identity.

The secret is written once beneath the protected Mother state root.  Commands
return only the public Besu node id, account address, and an artifact locator.
No network access, Coolify mutation, validator mutation, or secret printing is
performed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from . import atomic_files
from .canonical import canonical_json
from .ethereum_identity import (
    generate_private_key,
    is_private_key,
    private_key_to_address,
    private_key_to_node_id,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_KIND = "main_computer.mother.deployment_private_rpc_identity.v1"
_SCHEMA_VERSION = 1
_DIRECTORY = ("secrets", "deployment-private-rpc-identities")
_SECRET_ENV = "MC_MOTHER_RPC_NODE_PRIVATE_KEY"
_SERVICE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class MotherDeploymentPrivateRpcIdentityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error(code: str, message: str) -> MotherDeploymentPrivateRpcIdentityError:
    return MotherDeploymentPrivateRpcIdentityError(code, message)


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _timestamp(value: str | None = None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if type(value) is not str or not value:
        raise _error("MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_INVALID", "created_at must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise _error("MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_INVALID", "created_at is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise _error("MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_INVALID", "created_at must be UTC")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _service_name(value: Any) -> str:
    if type(value) is not str or _SERVICE_RE.fullmatch(value) is None:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_INVALID",
            "service_name must be a lowercase DNS-safe label",
        )
    if value in {"mainneta-super1", "mainnetc-super1"} or not value.startswith("mainnet-rpc"):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_INVALID",
            "service_name must begin with mainnet-rpc and must not reuse a validator name",
        )
    return value


def _root(paths: PrivateStatePaths) -> Path:
    return paths.root / _DIRECTORY[0] / _DIRECTORY[1]


def _destination(paths: PrivateStatePaths, service_name: str) -> Path:
    return _root(paths) / f"{service_name}.json"


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(
        canonical_json({key: value for key, value in document.items() if key != field})
    ).hexdigest()


def _relative(paths: PrivateStatePaths, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_PATH_UNSAFE",
            "identity artifact is outside Mother state",
        ) from exc


def _ensure_root(
    paths: PrivateStatePaths,
    *,
    operation: OperationIdentity,
) -> Path:
    current = paths.root
    for part in _DIRECTORY:
        current /= part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _read_document(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    identity_path: Path,
    *,
    network: str,
    service_name: str | None,
    operation: OperationIdentity,
) -> tuple[dict[str, Any], Path, str]:
    if network != "mainnet":
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_NETWORK_REJECTED",
            "private RPC identity reservation currently accepts mainnet only",
        )
    candidate = Path(identity_path).resolve(strict=False)
    expected_root = _root(paths).resolve(strict=False)
    try:
        candidate.relative_to(expected_root)
    except ValueError as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_PATH_UNSAFE",
            "identity artifact is outside the canonical private RPC identity directory",
        ) from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_NOT_FOUND",
            "private RPC identity artifact is missing or unsafe",
        )
    _secure_private_path(candidate, is_directory=False, operation=operation)
    payload = candidate.read_bytes()
    try:
        value = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_INVALID",
            "private RPC identity artifact is not canonical JSON",
        ) from exc
    if type(value) is not dict or canonical_json(value) != payload:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_INVALID",
            "private RPC identity artifact is not canonical",
        )
    digest = _digest_without(value, "private_rpc_identity_sha256")
    private_key = value.get("private_key")
    try:
        derived_node_id = private_key_to_node_id(private_key)
        derived_address = private_key_to_address(private_key).lower()
    except (TypeError, ValueError) as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_INVALID",
            "private RPC identity private key is invalid",
        ) from exc
    actual_service = _service_name(value.get("service_name"))
    if not (
        value.get("kind") == _KIND
        and value.get("schema_version") == _SCHEMA_VERSION
        and value.get("network") == network
        and value.get("mother_binding") == _binding(private_state)
        and value.get("secret_environment_variable") == _SECRET_ENV
        and value.get("validator_identity") is False
        and value.get("private_rpc_identity_sha256") == digest
        and value.get("node_id") == derived_node_id
        and str(value.get("address") or "").lower() == derived_address
        and (service_name is None or actual_service == _service_name(service_name))
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_INVALID",
            "private RPC identity artifact is modified, stale, or contradictory",
        )
    return value, candidate, hashlib.sha256(payload).hexdigest()


def verify_private_rpc_identity(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    identity_path: Path,
    *,
    network: str = "mainnet",
    service_name: str | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    value, candidate, file_sha = _read_document(
        paths,
        private_state,
        identity_path,
        network=network,
        service_name=service_name,
        operation=operation,
    )
    return {
        "clean": True,
        "kind": _KIND,
        "network": value["network"],
        "service_name": value["service_name"],
        "identity_path": str(candidate),
        "identity_locator": _relative(paths, candidate),
        "identity_artifact": {
            "path": str(candidate),
            "locator": _relative(paths, candidate),
            "sha256": file_sha,
        },
        "identity_file_sha256": file_sha,
        "identity_sha256": value["private_rpc_identity_sha256"],
        "node_id": value["node_id"],
        "address": value["address"],
        "secret_environment_variable": _SECRET_ENV,
        "validator_identity": False,
        "private_key_present": True,
        "private_key_printed": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_mutation_performed": False,
        "next_phase": "stage-private-rpc-transaction",
    }


def inspect_private_rpc_identity_reservation(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    *,
    service_name: str = "mainnet-rpc1",
    network: str = "mainnet",
    operation: OperationIdentity,
) -> dict[str, Any]:
    if network != "mainnet":
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_NETWORK_REJECTED",
            "private RPC identity reservation currently accepts mainnet only",
        )
    name = _service_name(service_name)
    destination = _destination(paths, name)
    if destination.exists():
        result = verify_private_rpc_identity(
            paths,
            private_state,
            destination,
            network=network,
            service_name=name,
            operation=operation,
        )
        return {
            "status": "pass",
            "identity_exists": True,
            "write_performed": False,
            **result,
        }
    return {
        "status": "pass",
        "network": network,
        "service_name": name,
        "identity_exists": False,
        "would_generate_private_key": True,
        "write_performed": False,
        "private_key_printed": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_mutation_performed": False,
        "next_phase": "reserve-private-rpc-identity-with-write",
    }


def reserve_private_rpc_identity(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    *,
    service_name: str = "mainnet-rpc1",
    network: str = "mainnet",
    created_at: str | None = None,
    operation: OperationIdentity,
    key_factory: Callable[[], str] = generate_private_key,
) -> dict[str, Any]:
    if network != "mainnet":
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_NETWORK_REJECTED",
            "private RPC identity reservation currently accepts mainnet only",
        )
    name = _service_name(service_name)
    destination = _destination(paths, name)
    if destination.exists():
        verified = verify_private_rpc_identity(
            paths,
            private_state,
            destination,
            network=network,
            service_name=name,
            operation=operation,
        )
        return {
            "status": "pass",
            "identity_exists": True,
            "identity_created": False,
            "write_performed": False,
            **verified,
        }

    private_key = key_factory()
    if not is_private_key(private_key):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_INVALID",
            "key factory returned an invalid secp256k1 private key",
        )
    node_id = private_key_to_node_id(private_key)
    address = private_key_to_address(private_key).lower()
    document: dict[str, Any] = {
        "kind": _KIND,
        "schema_version": _SCHEMA_VERSION,
        "created_at": _timestamp(created_at),
        "network": network,
        "service_name": name,
        "mother_binding": _binding(private_state),
        "private_key": private_key,
        "node_id": node_id,
        "address": address,
        "secret_environment_variable": _SECRET_ENV,
        "validator_identity": False,
    }
    document["private_rpc_identity_sha256"] = _digest_without(
        document,
        "private_rpc_identity_sha256",
    )
    payload = canonical_json(document)
    root = _ensure_root(paths, operation=operation)
    destination = root / f"{name}.json"
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    verified = verify_private_rpc_identity(
        paths,
        private_state,
        destination,
        network=network,
        service_name=name,
        operation=operation,
    )
    return {
        "status": "pass",
        "identity_exists": True,
        "identity_created": True,
        "write_performed": True,
        **verified,
    }


__all__ = [
    "MotherDeploymentPrivateRpcIdentityError",
    "inspect_private_rpc_identity_reservation",
    "reserve_private_rpc_identity",
    "verify_private_rpc_identity",
]
