"""Read-only T3 post-admission steady-state gate after C2 validator admission.

This phase consumes passing C2 validator-admission evidence and performs only
Coolify control-plane GET observations.  It does not PATCH Compose, deploy,
publish routing/topology, create public RPC, cast validator votes, or use SSH.
The goal is to bind the newly admitted A1+C1+C2 validator set to fresh service
observations before any later soak or publication slice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import time
from typing import Any

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_c2_validator_admission import (
    verify_c2_validator_admission_evidence,
    _component_healthy,
    _find_service_record,
    _http,
    _identifier,
    _safe_response,
    _service_status,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_TRANSACTION_KIND = "main_computer.mother.deployment_t3_post_admission_steady_state_transaction.v1"
_RELEASE_KIND = "main_computer.mother.deployment_t3_post_admission_steady_state_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_t3_post_admission_steady_state_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_t3_post_admission_steady_state_evidence.v1"

_TRANSACTION_DIRECTORY = ("actions", "deployment-t3-post-admission-steady-state-transactions")
_RELEASE_DIRECTORY = ("actions", "deployment-t3-post-admission-steady-state-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-t3-post-admission-steady-state-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-t3-post-admission-steady-state")

_A1_NODE = "mainneta-super1"
_C1_NODE = "mainnetc-super1"
_C2_NODE = "mainnetc-super2"
_ORDER = (_A1_NODE, _C1_NODE, _C2_NODE)
_CONTROLLER_BY_NODE = {
    _A1_NODE: "coolify-a",
    _C1_NODE: "coolify-c",
    _C2_NODE: "coolify-c",
}
_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900


class MotherDeploymentT3PostAdmissionSteadyStateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentT3PostAdmissionSteadyStateError:
    return MotherDeploymentT3PostAdmissionSteadyStateError(code, message)


def _timestamp(value: str | None = None) -> str:
    if value is not None:
        parsed = _parse_utc(value, "timestamp")
        return parsed.isoformat().replace("+00:00", "Z")
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_TIME_INVALID", f"{label} is missing")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age(value: Any, *, now: datetime | None = None) -> int:
    completed = _parse_utc(value, "created_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - completed).total_seconds())
    if age < -60:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_TIME_INVALID", "artifact timestamp is in the future")
    return max(age, 0)


def _duration(seconds: int) -> str:
    if seconds < _MIN_RELEASE_SECONDS or seconds > _MAX_RELEASE_SECONDS:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_RELEASE_WINDOW_INVALID", "release expiry must be between 30 and 900 seconds")
    return (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _whole_seconds(value: int | float, *, label: str) -> int:
    if isinstance(value, bool):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_WINDOW_INVALID", f"{label} must be a whole number of seconds")
    if isinstance(value, float):
        if not value.is_integer():
            raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_WINDOW_INVALID", f"{label} must be a whole number of seconds")
        value = int(value)
    seconds = int(value)
    if seconds != value:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_WINDOW_INVALID", f"{label} must be a whole number of seconds")
    return seconds


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _root(paths: PrivateStatePaths) -> Path:
    root = paths.root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ensure_directory(paths: PrivateStatePaths, parts: tuple[str, ...], *, operation: OperationIdentity) -> Path:
    current = _root(paths)
    for part in parts:
        current = current / part
    current.mkdir(parents=True, exist_ok=True)
    _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_PATH_INVALID", f"{label} is outside the runtime state root") from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, directory: tuple[str, ...], label: str) -> Path:
    if not isinstance(locator, str) or not locator:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_PATH_INVALID", f"{label} locator is missing")
    candidate = (paths.root / Path(locator)).resolve(strict=False)
    allowed = (paths.root / Path(*directory)).resolve(strict=False)
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_PATH_INVALID", f"{label} is outside its evidence directory") from exc
    return candidate


def _canonical_file(path: Path) -> tuple[dict[str, Any], bytes, str]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_PATH_INVALID", f"cannot read {path}") from exc
    import json

    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_JSON_INVALID", f"{path} is not canonical JSON") from exc
    if not isinstance(document, dict):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_JSON_INVALID", f"{path} is not an object")
    digest = hashlib.sha256(canonical_json(document)).hexdigest()
    return document, payload, digest


def _canonical_under(paths: PrivateStatePaths, path: Path, directory: tuple[str, ...], label: str) -> tuple[dict[str, Any], bytes, str]:
    resolved = path.resolve(strict=False)
    allowed = (paths.root / Path(*directory)).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_PATH_INVALID", f"{label} is outside its directory") from exc
    return _canonical_file(resolved)


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    body = {key: value for key, value in document.items() if key != field}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def _contains_sensitive(value: Any) -> bool:
    sensitive_markers = (
        "private_key",
        "private-key",
        "password",
        "bearer ",
        "0x0000000000000000000000000000000000000000000000000000000000000006",
    )
    if isinstance(value, str):
        text = value.lower()
        return any(marker in text for marker in sensitive_markers)
    if isinstance(value, Mapping):
        return any(_contains_sensitive(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive(item) for item in value)
    return False


def _selection(selected_nodes: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if not selected:
        return (_C2_NODE,)
    if selected != (_C2_NODE,):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_SELECTION_MISMATCH", "T3 post-admission steady-state is scoped to mainnetc-super2")
    return selected


def _load_admission_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    admission_evidence_path: Path,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    verified = verify_c2_validator_admission_evidence(
        paths,
        private_state,
        admission_evidence_path,
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        now=now,
    )
    document, _, digest = _canonical_under(
        paths,
        admission_evidence_path,
        ("evidence", "deployment-c2-validator-admission"),
        "C2 validator-admission evidence",
    )
    if digest != verified["evidence_sha256"]:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_SOURCE_INVALID", "C2 validator-admission evidence digest mismatch")
    final_set = verified.get("final_validator_set")
    if not (
        verified.get("clean") is True
        and verified.get("validator_vote_proven") is True
        and verified.get("validator_activation_proven") is True
        and verified.get("routing_or_topology_published") is False
        and verified.get("public_endpoint_created") is False
        and isinstance(final_set, list)
        and len(final_set) == 3
        and verified.get("next_phase") == "stage-t3-post-admission-steady-state"
    ):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_SOURCE_INVALID", "C2 validator-admission evidence is not a passing T3 admission source")
    return document, verified, digest


def build_t3_post_admission_steady_state_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    admission_evidence_path: Path,
    *,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 86400,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    _selection(selected_nodes)
    source_document, verified, digest = _load_admission_evidence(
        paths,
        private_state,
        Path(admission_evidence_path),
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if network != verified.get("network"):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_NETWORK_MISMATCH", "T3 steady-state network does not match C2 admission evidence")
    created_text = _timestamp(created_at)
    final_set = list(verified["final_validator_set"])
    targets = {
        node: {
            "node": node,
            "controller_id": _CONTROLLER_BY_NODE[node],
            "required_live_observation": True,
            "readiness_source": "coolify-voter-service-health" if node != _C2_NODE else "c2-validator-admission-evidence",
            "coolify_aggregate_status_required": node != _C2_NODE,
        }
        for node in _ORDER
    }
    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "mother_binding": _binding(private_state),
        "network": network,
        "nodes": list(_ORDER),
        "candidate_node": _C2_NODE,
        "candidate_validator_address": verified["candidate_validator_address"],
        "source_admission_evidence": {
            "locator": _relative(paths, Path(admission_evidence_path), label="C2 validator-admission evidence"),
            "sha256": digest,
            "completed_at": source_document.get("completed_at"),
        },
        "chain": {
            "chain_id": source_document.get("chain_id"),
            "genesis_sha256": source_document.get("genesis_sha256"),
            "validator_set": final_set,
            "validator_count": len(final_set),
        },
        "targets": targets,
        "execution_plan": {
            "kind": "read-only-t3-post-admission-steady-state-observation",
            "allowed_http_methods": ["GET"],
            "mutation_count": 0,
            "observation_nodes": list(_ORDER),
            "minimum_observation_windows": 2,
            "window_separation_seconds_default": 60,
        },
        "policy": {
            "compiler": "mother-native-t3-post-admission-steady-state-v1",
            "read_only": True,
            "network_access_performed": False,
            "live_mutation_performed": False,
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
            "operator_release_required": True,
            "live_execution_authorized": False,
            "read_only_observation_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
        },
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_RELEASE_REQUIRED",
                "message": "an explicit expiring operator release is required for this exact T3 steady-state observation plan",
            }
        ],
    }
    transaction["summary"] = {
        "transaction_valid": True,
        "candidate_node": _C2_NODE,
        "validator_count": len(final_set),
        "final_validator_set_bound": True,
        "source_admission_evidence_verified": True,
        "read_only_observation_authorized": False,
        "mutation_count": 0,
        "public_endpoint_created": False,
        "routing_or_topology_publication_authorized": False,
        "validator_vote_authorized": False,
        "validator_activation_authorized": False,
        "next_phase": "release-t3-post-admission-steady-state",
    }
    transaction["t3_post_admission_steady_state_transaction_sha256"] = _digest_without(
        transaction,
        "t3_post_admission_steady_state_transaction_sha256",
    )
    return transaction


def write_t3_post_admission_steady_state_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(transaction)
    if document.get("kind") != _TRANSACTION_KIND or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_TRANSACTION_INVALID", "T3 steady-state transaction is malformed or sensitive")
    digest = _digest_without(document, "t3_post_admission_steady_state_transaction_sha256")
    if document.get("t3_post_admission_steady_state_transaction_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_TRANSACTION_INVALID", "T3 steady-state transaction digest mismatch")
    payload = canonical_json(document)
    root = _ensure_directory(paths, _TRANSACTION_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "t3steadystate"
    destination = root / f"{stamp}-mainnet-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_TRANSACTION_CONFLICT", "transaction destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_t3_post_admission_steady_state_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    _selection(selected_nodes)
    document, _, _file_sha = _canonical_under(paths, Path(transaction_path), _TRANSACTION_DIRECTORY, "T3 steady-state transaction")
    digest = _digest_without(document, "t3_post_admission_steady_state_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("mother_binding") != _binding(private_state)
        or document.get("t3_post_admission_steady_state_transaction_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_TRANSACTION_INVALID", "T3 steady-state transaction is invalid")
    age = _age(document.get("created_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_TRANSACTION_STALE", "T3 steady-state transaction is outside the freshness window")
    source = document.get("source_admission_evidence")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_TRANSACTION_INVALID", "source admission evidence binding is missing")
    source_path = _resolve_locator(
        paths,
        source.get("locator"),
        directory=("evidence", "deployment-c2-validator-admission"),
        label="source admission evidence",
    )
    _, verified, source_sha = _load_admission_evidence(
        paths,
        private_state,
        source_path,
        max_age_seconds=max_age_seconds,
        now=now,
    )
    chain = document.get("chain")
    if not isinstance(chain, Mapping) or source_sha != source.get("sha256") or sorted(chain.get("validator_set", [])) != sorted(verified["final_validator_set"]):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_TRANSACTION_INVALID", "T3 steady-state transaction source binding changed")
    return {
        "clean": True,
        "transaction_path": str(Path(transaction_path).resolve(strict=False)),
        "t3_post_admission_steady_state_transaction_sha256": digest,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "nodes": list(_ORDER),
        "candidate_node": _C2_NODE,
        "candidate_validator_address": document["candidate_validator_address"],
        "validator_set": list(chain["validator_set"]),
        "validator_count": int(chain["validator_count"]),
        "source_admission_evidence_sha256": source_sha,
        "read_only_observation_authorized": False,
        "mutation_count": 0,
        "routing_or_topology_publication_authorized": False,
        "public_endpoint_created": False,
        "next_phase": "release-t3-post-admission-steady-state",
    }


def build_t3_post_admission_steady_state_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_transaction_sha256: str,
    selected_nodes: Iterable[str] = (),
    transaction_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    acknowledged = str(acknowledged_transaction_sha256 or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", acknowledged):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_ACK_INVALID", "acknowledged transaction SHA-256 is invalid")
    verified = verify_t3_post_admission_steady_state_transaction(
        paths,
        private_state,
        Path(transaction_path),
        selected_nodes=selected_nodes,
        max_age_seconds=transaction_max_age_seconds,
        now=now,
    )
    if verified["t3_post_admission_steady_state_transaction_sha256"] != acknowledged:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_ACK_MISMATCH", "acknowledged transaction SHA-256 does not match")
    tx, _, _ = _canonical_under(paths, Path(transaction_path), _TRANSACTION_DIRECTORY, "T3 steady-state transaction")
    created_text = _timestamp(created_at)
    release = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": _duration(expires_in_seconds),
        "mother_binding": _binding(private_state),
        "network": verified["network"],
        "nodes": list(_ORDER),
        "candidate_node": _C2_NODE,
        "candidate_validator_address": verified["candidate_validator_address"],
        "source_transaction": {
            "locator": _relative(paths, Path(transaction_path), label="T3 steady-state transaction"),
            "sha256": verified["t3_post_admission_steady_state_transaction_sha256"],
        },
        "source_admission_evidence": dict(tx["source_admission_evidence"]),
        "chain": dict(tx["chain"]),
        "targets": dict(tx["targets"]),
        "execution_plan": dict(tx["execution_plan"]),
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "read_only": True,
            "mutation_count": 0,
            "live_mutation_performed": False,
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
            "authorization_source": "explicit-operator-release",
            "requested_use_limit": 1,
            "live_execution_authorized": True,
            "read_only_observation_authorized": True,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
        },
    }
    release["summary"] = {
        "clean": True,
        "executor_implemented": True,
        "read_only_observation_authorized": True,
        "live_execution_authorized": True,
        "validator_count": verified["validator_count"],
        "mutation_count": 0,
        "validator_vote_authorized": False,
        "validator_activation_authorized": False,
        "routing_or_topology_publication_authorized": False,
        "public_endpoint_created": False,
        "next_phase": "execute-t3-post-admission-steady-state",
    }
    release["t3_post_admission_steady_state_release_sha256"] = _digest_without(
        release,
        "t3_post_admission_steady_state_release_sha256",
    )
    return release


def write_t3_post_admission_steady_state_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(release)
    if document.get("kind") != _RELEASE_KIND or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_RELEASE_INVALID", "T3 steady-state release is malformed or sensitive")
    digest = _digest_without(document, "t3_post_admission_steady_state_release_sha256")
    if document.get("t3_post_admission_steady_state_release_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_RELEASE_INVALID", "T3 steady-state release digest mismatch")
    payload = canonical_json(document)
    root = _ensure_directory(paths, _RELEASE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "t3steadystaterelease"
    destination = root / f"{stamp}-mainnet-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_RELEASE_CONFLICT", "release destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def _release_claim_path(paths: PrivateStatePaths, digest: str) -> Path:
    return paths.root / Path(*_CLAIM_DIRECTORY) / f"{digest}.json"


def verify_t3_post_admission_steady_state_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    _selection(selected_nodes)
    document, _, _file_sha = _canonical_under(paths, Path(release_path), _RELEASE_DIRECTORY, "T3 steady-state release")
    digest = _digest_without(document, "t3_post_admission_steady_state_release_sha256")
    if (
        document.get("kind") != _RELEASE_KIND
        or document.get("mother_binding") != _binding(private_state)
        or document.get("t3_post_admission_steady_state_release_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_RELEASE_INVALID", "T3 steady-state release is invalid")
    created_age = _age(document.get("created_at"), now=now)
    if created_age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_RELEASE_STALE", "T3 steady-state release is outside the freshness window")
    expires = _parse_utc(document.get("expires_at"), "release.expires_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if expires <= reference:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_RELEASE_EXPIRED", "T3 steady-state release has expired")
    source_tx = document.get("source_transaction")
    if not isinstance(source_tx, Mapping):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_RELEASE_INVALID", "release transaction binding is missing")
    tx_path = _resolve_locator(paths, source_tx.get("locator"), directory=_TRANSACTION_DIRECTORY, label="T3 steady-state transaction")
    verified_tx = verify_t3_post_admission_steady_state_transaction(
        paths,
        private_state,
        tx_path,
        selected_nodes=selected_nodes,
        max_age_seconds=transaction_max_age_seconds,
        now=now,
    )
    if verified_tx["t3_post_admission_steady_state_transaction_sha256"] != source_tx.get("sha256"):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_RELEASE_INVALID", "release transaction binding changed")
    claim_path = _release_claim_path(paths, digest)
    return {
        "clean": True,
        "release_path": str(Path(release_path).resolve(strict=False)),
        "t3_post_admission_steady_state_release_sha256": digest,
        "age_seconds": created_age,
        "expires_at": document["expires_at"],
        "release_already_claimed": claim_path.exists(),
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "nodes": list(_ORDER),
        "candidate_node": _C2_NODE,
        "candidate_validator_address": document["candidate_validator_address"],
        "validator_set": list(document["chain"]["validator_set"]),
        "validator_count": int(document["chain"]["validator_count"]),
        "source_admission_evidence_sha256": document["source_admission_evidence"]["sha256"],
        "read_only_observation_authorized": True,
        "live_execution_authorized": True,
        "mutation_count": 0,
        "validator_vote_authorized": False,
        "validator_activation_authorized": False,
        "routing_or_topology_publication_authorized": False,
        "public_endpoint_created": False,
        "next_phase": "execute-t3-post-admission-steady-state",
    }


def inspect_t3_post_admission_steady_state_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    acknowledged = str(acknowledged_release_sha256 or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", acknowledged):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_ACK_INVALID", "acknowledged release SHA-256 is invalid")
    verified = verify_t3_post_admission_steady_state_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        now=now,
    )
    if verified["t3_post_admission_steady_state_release_sha256"] != acknowledged:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_ACK_MISMATCH", "acknowledged release SHA-256 does not match")
    return {
        **verified,
        "execute_requested": False,
        "executor_implemented": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "summary": {
            "clean": True,
            "executor_implemented": True,
            "read_only_observation_authorized": True,
            "live_execution_authorized": True,
            "release_already_claimed": verified["release_already_claimed"],
            "mutation_count": 0,
            "live_mutation_performed": False,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "next_phase": "execute-t3-post-admission-steady-state",
        },
    }


def _observe_node(
    *,
    controller: Any,
    controller_id: str,
    node: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    window_index: int,
    admission_verified: bool,
) -> dict[str, Any]:
    response = _http(
        controller,
        "GET",
        "/api/v1/services",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    record: Mapping[str, Any] | None = None
    status = "missing"
    component_healthy = False
    service_uuid = None
    if response["ok"]:
        try:
            record = _find_service_record(response["payload"], node=node)
            service_uuid = _identifier(record.get("uuid"), f"{node} service UUID")
            status = _service_status(record)
            component_healthy = _component_healthy(record, node=node)
        except Exception:
            record = None
    if node == _C2_NODE:
        verified = bool(service_uuid) and admission_verified
        readiness_source = "c2-validator-admission-evidence"
        aggregate_required = False
    else:
        verified = bool(service_uuid) and component_healthy
        readiness_source = "coolify-voter-service-health"
        aggregate_required = True
    return {
        "window": window_index,
        "node": node,
        "controller_id": controller_id,
        "method": "GET",
        "endpoint": "/api/v1/services",
        "response": _safe_response(response),
        "service_uuid": service_uuid,
        "service_status": status,
        "component_or_service_healthy": component_healthy,
        "verified": verified,
        "proof_source": readiness_source,
        "coolify_aggregate_status_required": aggregate_required,
        "observed_at": _timestamp(),
    }


def _write_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(evidence)
    if document.get("kind") != _EVIDENCE_KIND or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID", "T3 steady-state evidence is malformed or sensitive")
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, _EVIDENCE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "t3steadystateevidence"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_EVIDENCE_CONFLICT", "evidence destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_t3_post_admission_steady_state_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    window_count: int = 2,
    window_seconds: float = 60.0,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    if window_count < 1 or window_count > 5:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_WINDOW_INVALID", "window count must be between 1 and 5")
    canonical_window_seconds = _whole_seconds(window_seconds, label="window separation")
    if canonical_window_seconds < 0 or canonical_window_seconds > 900:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_WINDOW_INVALID", "window separation must be between 0 and 900 seconds")
    inspected = inspect_t3_post_admission_steady_state_release(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        now=now,
    )
    if inspected["release_already_claimed"]:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_RELEASE_ALREADY_CONSUMED", "this T3 steady-state release is already claimed")
    release, _, _ = _canonical_under(paths, Path(inspected["release_path"]), _RELEASE_DIRECTORY, "T3 steady-state release")
    digest = inspected["t3_post_admission_steady_state_release_sha256"]
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="T3 steady-state release"), "sha256": digest},
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_root = _ensure_directory(paths, _CLAIM_DIRECTORY, operation=operation)
    claim_path = claim_root / f"{digest}.json"
    if claim_path.exists():
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_RELEASE_ALREADY_CONSUMED", "this T3 steady-state release is already claimed")
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)

    started = _timestamp()
    observations: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None
    admission_verified = False
    try:
        source = release["source_admission_evidence"]
        source_path = _resolve_locator(
            paths,
            source.get("locator"),
            directory=("evidence", "deployment-c2-validator-admission"),
            label="source admission evidence",
        )
        _, verified_admission, source_sha = _load_admission_evidence(
            paths,
            private_state,
            source_path,
            max_age_seconds=transaction_max_age_seconds,
            now=now,
        )
        if source_sha != source.get("sha256"):
            raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_SOURCE_INVALID", "source admission evidence digest changed")
        if sorted(verified_admission["final_validator_set"]) != sorted(release["chain"]["validator_set"]):
            raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_SOURCE_INVALID", "source admission evidence validator set changed")
        admission_verified = True
        controllers = {
            "coolify-a": resolve_coolify_controller(private_state, release["network"], "coolify-a"),
            "coolify-c": resolve_coolify_controller(private_state, release["network"], "coolify-c"),
        }
        for window_index in range(1, window_count + 1):
            if window_index > 1 and canonical_window_seconds:
                time.sleep(canonical_window_seconds)
            for node in _ORDER:
                controller_id = _CONTROLLER_BY_NODE[node]
                observations.append(
                    _observe_node(
                        controller=controllers[controller_id],
                        controller_id=controller_id,
                        node=node,
                        timeout=timeout,
                        max_response_bytes=max_response_bytes,
                        opener=opener,
                        window_index=window_index,
                        admission_verified=admission_verified,
                    )
                )
            if not all(item.get("verified") is True for item in observations if item.get("window") == window_index):
                raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_NOT_HEALTHY", f"T3 steady-state observation window {window_index} did not verify all nodes")
    except MotherDeploymentT3PostAdmissionSteadyStateError as exc:
        failure = {"code": exc.code, "message": str(exc)[:512]}
    except Exception as exc:  # pragma: no cover
        failure = {"code": "MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_UNEXPECTED_FAILURE", "message": str(exc)[:512]}

    complete = failure is None and admission_verified and len(observations) == window_count * len(_ORDER) and all(item.get("verified") is True for item in observations)
    completed = _timestamp()
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": "pass" if complete else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "nodes": list(_ORDER),
        "candidate_node": _C2_NODE,
        "candidate_validator_address": inspected["candidate_validator_address"],
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="T3 steady-state release"), "sha256": digest},
        "execution_claim": {"locator": _relative(paths, claim_path, label="T3 steady-state claim")},
        "source_admission_evidence": dict(release["source_admission_evidence"]),
        "chain_id": release["chain"]["chain_id"],
        "genesis_sha256": release["chain"]["genesis_sha256"],
        "validator_set": list(release["chain"]["validator_set"]),
        "observation_windows": window_count,
        "window_seconds": canonical_window_seconds,
        "service_observations": observations,
        "failure": failure,
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "read_only": True,
            "mutation_count": 0,
            "live_mutation_performed": False,
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
            "read_only_observation_authorized": True,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
        },
        "summary": {
            "clean": complete,
            "complete": complete,
            "source_admission_evidence_reverified": admission_verified,
            "final_validator_set_bound": complete,
            "service_observation_count": len(observations),
            "observation_windows_verified": window_count if complete else 0,
            "A1_service_verified": all(item.get("verified") is True for item in observations if item.get("node") == _A1_NODE),
            "C1_service_verified": all(item.get("verified") is True for item in observations if item.get("node") == _C1_NODE),
            "C2_service_observed": all(bool(item.get("service_uuid")) for item in observations if item.get("node") == _C2_NODE),
            "C2_readiness_source": "c2-validator-admission-evidence",
            "network_access_performed": bool(observations),
            "live_mutation_performed": False,
            "mutation_count": 0,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "manual_ssh_required": False,
            "next_phase": "stage-t3-steady-state-soak" if complete else "manual-review-required",
        },
        "next_phase": "stage-t3-steady-state-soak" if complete else "manual-review-required",
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "chain_mutation_count": 0,
        "service_mutation_count": 0,
    }
    evidence_path, evidence_sha = _write_evidence(paths, evidence, operation=operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence


def verify_t3_post_admission_steady_state_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    _selection(selected_nodes)
    document, _, digest = _canonical_under(paths, Path(evidence_path), _EVIDENCE_DIRECTORY, "T3 steady-state evidence")
    if (
        document.get("kind") != _EVIDENCE_KIND
        or document.get("mother_binding") != _binding(private_state)
        or document.get("status") != "pass"
        or _contains_sensitive(document)
    ):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID", "T3 steady-state evidence is invalid")
    age = _age(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_EVIDENCE_STALE", "T3 steady-state evidence is outside the freshness window")
    release_ref = document.get("release")
    if not isinstance(release_ref, Mapping):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID", "evidence release binding is missing")
    release_path = _resolve_locator(paths, release_ref.get("locator"), directory=_RELEASE_DIRECTORY, label="T3 steady-state release")
    release, _, _release_file_sha = _canonical_under(paths, release_path, _RELEASE_DIRECTORY, "T3 steady-state release")
    release_sha = _digest_without(release, "t3_post_admission_steady_state_release_sha256")
    if release_sha != release_ref.get("sha256"):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID", "evidence release binding changed")
    source = document.get("source_admission_evidence")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID", "evidence source binding is missing")
    source_path = _resolve_locator(
        paths,
        source.get("locator"),
        directory=("evidence", "deployment-c2-validator-admission"),
        label="source admission evidence",
    )
    _, verified_admission, source_sha = _load_admission_evidence(
        paths,
        private_state,
        source_path,
        max_age_seconds=transaction_max_age_seconds,
        now=now,
    )
    summary = document.get("summary")
    policy = document.get("policy")
    authority = document.get("authority")
    observations = document.get("service_observations")
    if not isinstance(summary, Mapping) or not isinstance(policy, Mapping) or not isinstance(authority, Mapping) or not isinstance(observations, list):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID", "T3 steady-state evidence is incomplete")
    if not all([
        source_sha == source.get("sha256") == release.get("source_admission_evidence", {}).get("sha256"),
        sorted(document.get("validator_set", [])) == sorted(release.get("chain", {}).get("validator_set", [])) == sorted(verified_admission["final_validator_set"]),
        len(document.get("validator_set", [])) == 3,
        summary.get("clean") is True,
        summary.get("source_admission_evidence_reverified") is True,
        summary.get("final_validator_set_bound") is True,
        summary.get("live_mutation_performed") is False,
        summary.get("mutation_count") == 0,
        summary.get("validator_vote_performed") is False,
        summary.get("validator_activation_performed") is False,
        summary.get("routing_or_topology_published") is False,
        summary.get("public_endpoint_created") is False,
        policy.get("read_only") is True,
        policy.get("routing_or_topology_published") is False,
        authority.get("read_only_observation_authorized") is True,
        authority.get("validator_vote_authorized") is False,
        authority.get("validator_activation_authorized") is False,
        authority.get("routing_or_topology_publication_authorized") is False,
        document.get("next_phase") == "stage-t3-steady-state-soak",
    ]):
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID", "T3 steady-state evidence does not prove the bounded gate")
    required_nodes = set(_ORDER)
    observed_nodes = {item.get("node") for item in observations if isinstance(item, Mapping) and item.get("verified") is True}
    if observed_nodes != required_nodes:
        raise _fail("MOTHER_DEPLOY_T3_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID", "T3 steady-state evidence is missing verified node observations")
    return {
        "clean": True,
        "evidence_path": str(Path(evidence_path).resolve(strict=False)),
        "evidence_sha256": digest,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "nodes": list(_ORDER),
        "candidate_node": _C2_NODE,
        "candidate_validator_address": document["candidate_validator_address"],
        "validator_set": list(document["validator_set"]),
        "validator_count": len(document["validator_set"]),
        "source_admission_evidence_sha256": source_sha,
        "source_admission_evidence_reverified": True,
        "read_only": True,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": "stage-t3-steady-state-soak",
    }


__all__ = [
    "MotherDeploymentT3PostAdmissionSteadyStateError",
    "build_t3_post_admission_steady_state_transaction",
    "write_t3_post_admission_steady_state_transaction",
    "verify_t3_post_admission_steady_state_transaction",
    "build_t3_post_admission_steady_state_release",
    "write_t3_post_admission_steady_state_release",
    "verify_t3_post_admission_steady_state_release",
    "inspect_t3_post_admission_steady_state_release",
    "execute_t3_post_admission_steady_state_release",
    "verify_t3_post_admission_steady_state_evidence",
]
