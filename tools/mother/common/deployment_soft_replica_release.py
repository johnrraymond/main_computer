"""Explicit expiring release for one exact C-side soft-replica configuration."""

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
from .deployment_soft_replica import verify_soft_replica_transaction
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path

_RELEASE_KIND = "main_computer.mother.deployment_soft_replica_release.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-soft-replica-releases")
_TRANSACTION_DIRECTORY = ("actions", "deployment-soft-replica-transactions")
_BIRTH_EVIDENCE_DIRECTORY = ("evidence", "deployment-genesis-birth")
_BIRTH_RELEASE_DIRECTORY = ("actions", "deployment-genesis-birth-releases")
_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900


class MotherDeploymentSoftReplicaReleaseError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip() or not re.fullmatch(r"[A-Za-z0-9._-]+", value.strip()):
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", f"{path} is invalid")
    return value.strip()


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", f"{path} must be SHA-256")
    return value


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", f"{path} must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", f"{path} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", f"{path} must be UTC")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(value, "created_at")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _duration(value: int) -> int:
    if type(value) is not int or isinstance(value, bool) or not _MIN_RELEASE_SECONDS <= value <= _MAX_RELEASE_SECONDS:
        raise MotherDeploymentSoftReplicaReleaseError(
            "MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_TTL_INVALID",
            f"expires_in_seconds must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}",
        )
    return value


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _contains_sensitive(value: Any) -> bool:
    forbidden = {"access_token", "api_token", "credential", "mnemonic", "password", "private_key", "refresh_token", "secret", "seed"}
    if isinstance(value, Mapping):
        return any(str(k).lower() in forbidden or _contains_sensitive(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive(v) for v in value)
    return False


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    value = dict(document)
    value.pop(field, None)
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _resolve(paths: PrivateStatePaths, locator: Any, directory: tuple[str, str], label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_PATH_UNSAFE", f"{label} locator is unsafe")
    candidate = Path(locator)
    if candidate.is_absolute() or PureWindowsPath(locator).is_absolute() or any(p in {"", ".", ".."} for p in candidate.parts):
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_PATH_UNSAFE", f"{label} locator is unsafe")
    result = (paths.root / candidate).resolve(strict=False)
    expected = (paths.root / directory[0] / directory[1]).resolve(strict=False)
    try:
        result.relative_to(expected)
    except ValueError as exc:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_PATH_UNSAFE", f"{label} is outside its canonical directory") from exc
    return result


def _canonical(path: Path, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", f"{label} is unreadable") from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", f"{label} is not canonical JSON")
    return value, raw, hashlib.sha256(raw).hexdigest()


def _relative(paths: PrivateStatePaths, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_PATH_UNSAFE", "artifact is outside Mother state") from exc


def _proof_binding(paths: PrivateStatePaths, transaction: Mapping[str, Any]) -> dict[str, Any]:
    evidence_ref = transaction.get("genesis_birth_evidence")
    if not isinstance(evidence_ref, Mapping):
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "birth evidence binding is missing")
    evidence_path = _resolve(paths, evidence_ref.get("locator"), _BIRTH_EVIDENCE_DIRECTORY, "birth evidence")
    evidence, _, evidence_sha = _canonical(evidence_path, "birth evidence")
    if evidence_sha != evidence_ref.get("sha256") or evidence.get("status") != "pass":
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "birth evidence is invalid")
    summary = evidence.get("summary")
    if not isinstance(summary, Mapping) or summary.get("initial_chain_proven") is not True or summary.get("soft_replica_untouched") is not True:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "birth evidence does not prove the required initial chain")
    release_ref = evidence.get("release")
    if not isinstance(release_ref, Mapping):
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "birth evidence lacks release binding")
    birth_release_path = _resolve(paths, release_ref.get("locator"), _BIRTH_RELEASE_DIRECTORY, "birth release")
    birth_release, _, _ = _canonical(birth_release_path, "birth release")
    if birth_release.get("release_sha256") != release_ref.get("sha256"):
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "birth release digest mismatch")
    proof_compose = birth_release.get("proof_plan", {}).get("proof_compose")
    if not isinstance(proof_compose, Mapping) or type(proof_compose.get("canonical_text")) is not str:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "birth release lacks proof Compose")
    return {
        "evidence": {"locator": _relative(paths, evidence_path), "sha256": evidence_sha},
        "controller_id": "coolify-a",
        "node": "mainneta-super1",
        "service_uuid": _identifier(evidence.get("service_uuid"), "initial service UUID"),
        "service_status": "running:healthy",
        "proof_compose": {
            "canonical_text": proof_compose["canonical_text"],
            "sha256": _sha256(proof_compose.get("sha256"), "proof Compose SHA-256"),
        },
    }


def build_soft_replica_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_transaction_sha256: str,
    selected_nodes: Iterable[str] = (),
    transaction_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
) -> dict[str, Any]:
    acknowledged = _sha256(acknowledged_transaction_sha256, "acknowledged transaction SHA-256")
    verified = verify_soft_replica_transaction(
        paths, private_state, Path(transaction_path), selected_nodes=selected_nodes,
        max_age_seconds=transaction_max_age_seconds,
    )
    if acknowledged != verified["soft_replica_transaction_sha256"]:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_ACKNOWLEDGEMENT_MISMATCH", "operator acknowledgement does not match the exact transaction")
    transaction_path = Path(verified["transaction_path"])
    transaction, _, transaction_byte_sha = _canonical(transaction_path, "soft replica transaction")
    if transaction_byte_sha != verified["byte_sha256"]:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "transaction byte digest mismatch")
    replica = transaction.get("replica")
    writes = transaction.get("future_write_set")
    if not isinstance(replica, Mapping) or type(writes) is not list or len(writes) != 2:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "transaction write set is missing")
    node = _identifier(replica.get("node"), "replica node")
    requested = tuple(_identifier(n, "selected node") for n in selected_nodes)
    if requested and requested != (node,):
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_SELECTION_MISMATCH", "release may target only the staged replica")
    if node != "mainnetc-super1" or replica.get("controller_id") != "coolify-c":
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "starter soft replica must target only Coolify C")
    created_text = _timestamp(created_at)
    created = _parse_utc(created_text, "created_at")
    expires = created + timedelta(seconds=_duration(expires_in_seconds))
    proof = _proof_binding(paths, transaction)
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "network": transaction["network"],
        "mother_binding": _binding(private_state),
        "staged_scope": "release-and-apply-soft-replica-configuration",
        "transaction": {
            "locator": _relative(paths, transaction_path),
            "sha256": verified["soft_replica_transaction_sha256"],
            "byte_sha256": transaction_byte_sha,
        },
        "initial_chain_precondition": proof,
        "execution_plan": {
            "replica_node": node,
            "controller_id": "coolify-c",
            "service_uuid": _identifier(replica.get("service_uuid"), "replica service UUID"),
            "compose": dict(replica["compose"]),
            "identity_commitments": dict(replica.get("identity_commitments") or {}),
            "mutations": [dict(item) for item in writes],
        },
        "authority": {
            "configuration_apply_authorized": True,
            "replica_start_authorized": True,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "requested_use_limit": 1,
        },
        "policy": {
            "initial_node_read_only": True,
            "replica_node_only": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "qbft_vote_performed": False,
            "network_access_performed": False,
            "live_mutation_performed": False,
        },
        "remaining_blockers": [
            {"code": "MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_NOT_IMPLEMENTED", "message": "the one-use C-side executor must consume this exact release"},
            {"code": "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED", "message": "replica startup does not authorize a QBFT vote"},
        ],
        "summary": {
            "release_valid": True,
            "mutation_count": 2,
            "initial_node_read_only": True,
            "replica_node": node,
            "validator_vote_authorized": False,
            "next_phase_after_apply": "prove-soft-replica-synchronization-before-validator-admission",
        },
    }
    if _contains_sensitive(release):
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "release contains sensitive material")
    release["soft_replica_release_sha256"] = _digest_without(release, "soft_replica_release_sha256")
    return release


def write_soft_replica_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(document, "soft_replica_release_sha256")
    if document.get("kind") != _RELEASE_KIND or document.get("soft_replica_release_sha256") != digest or _contains_sensitive(document):
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "release is malformed")
    payload = canonical_json(document)
    current = paths.root
    for part in _RELEASE_DIRECTORY:
        current /= part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "replicarelease"
    destination = current / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_CONFLICT", "release destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_soft_replica_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    candidate = Path(release_path).resolve(strict=False)
    expected_root = (paths.root / _RELEASE_DIRECTORY[0] / _RELEASE_DIRECTORY[1]).resolve(strict=False)
    try:
        candidate.relative_to(expected_root)
    except ValueError as exc:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_PATH_UNSAFE", "release is outside its canonical directory") from exc
    document, raw, byte_sha = _canonical(candidate, "soft replica release")
    digest = _digest_without(document, "soft_replica_release_sha256")
    if document.get("kind") != _RELEASE_KIND or document.get("soft_replica_release_sha256") != digest or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "release is invalid or stale")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    created = _parse_utc(document.get("created_at"), "created_at")
    expires = _parse_utc(document.get("expires_at"), "expires_at")
    if reference < created - timedelta(seconds=1) or reference > expires or int((reference - created).total_seconds()) > max_age_seconds:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_EXPIRED", "release is outside its authority window")
    transaction_ref = document.get("transaction")
    if not isinstance(transaction_ref, Mapping):
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "transaction binding is missing")
    transaction_path = _resolve(paths, transaction_ref.get("locator"), _TRANSACTION_DIRECTORY, "soft replica transaction")
    expected = build_soft_replica_release(
        paths, private_state, transaction_path,
        acknowledged_transaction_sha256=_sha256(transaction_ref.get("sha256"), "transaction SHA-256"),
        selected_nodes=selected_nodes,
        transaction_max_age_seconds=transaction_max_age_seconds,
        expires_in_seconds=int((expires - created).total_seconds()),
        created_at=document.get("created_at"),
    )
    if canonical_json(expected) != raw:
        raise MotherDeploymentSoftReplicaReleaseError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_INVALID", "release no longer matches its exact inputs")
    plan = document["execution_plan"]
    return {
        "clean": True,
        "release_path": str(candidate),
        "soft_replica_release_sha256": digest,
        "byte_sha256": byte_sha,
        "soft_replica_transaction_sha256": transaction_ref["sha256"],
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "nodes": [plan["replica_node"]],
        "initial_node": document["initial_chain_precondition"]["node"],
        "replica_node": plan["replica_node"],
        "controller_id": plan["controller_id"],
        "service_uuid": plan["service_uuid"],
        "compose_sha256": plan["compose"]["sha256"],
        "mutation_count": len(plan["mutations"]),
        "created_at": document["created_at"],
        "expires_at": document["expires_at"],
        "staged_scope": document["staged_scope"],
        "transaction_apply_authorized": True,
        "replica_start_authorized": True,
        "validator_vote_authorized": False,
        "live_execution_authorized": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "remaining_blocker_codes": ["MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_NOT_IMPLEMENTED", "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED"],
    }


__all__ = [
    "MotherDeploymentSoftReplicaReleaseError",
    "build_soft_replica_release",
    "verify_soft_replica_release",
    "write_soft_replica_release",
]
