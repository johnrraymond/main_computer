"""Offline compiler for one exact QBFT validator-admission transaction.

This boundary consumes canonical proof that C is a healthy synchronized
non-validator replica of A's Mother-owned chain.  It compiles the exact future
``qbft_proposeValidatorVote`` request that would admit C, but performs no
network access, grants no live authority, and does not cast the vote.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any

from . import atomic_files
from .canonical import canonical_json
from .deployment_soft_replica_sync import verify_soft_replica_sync_evidence
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_TRANSACTION_KIND = "main_computer.mother.deployment_validator_admission_transaction.v1"
_TRANSACTION_DIRECTORY = ("actions", "deployment-validator-admission-transactions")
_SYNC_EVIDENCE_DIRECTORY = ("evidence", "deployment-soft-replica-sync")
_SYNC_RELEASE_DIRECTORY = ("actions", "deployment-soft-replica-sync-releases")
_SYNC_RELEASE_KIND = "main_computer.mother.deployment_soft_replica_sync_release.v1"


class MotherDeploymentValidatorAdmissionError(RuntimeError):
    """Validator-admission staging or verification failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip() or not re.fullmatch(r"[A-Za-z0-9._-]+", value.strip()):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_INVALID", f"{path} is invalid"
        )
    return value.strip()


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_INVALID", f"{path} must be SHA-256"
        )
    return value


def _address(value: Any, path: str) -> str:
    if type(value) is not str or re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is None:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_INVALID", f"{path} must be an Ethereum address"
        )
    return value.lower()


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_INVALID", f"{path} must be a UTC timestamp"
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_INVALID", f"{path} is malformed"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_INVALID", f"{path} must be UTC"
        )
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(value, "created_at")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _private_document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        value = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_STATE_INVALID", "Mother private state is not canonical JSON"
        ) from exc
    if type(value) is not dict:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_STATE_INVALID", "Mother private state is not an object"
        )
    return value


def _contains_sensitive(value: Any) -> bool:
    forbidden = {
        "access_token", "api_token", "credential", "mnemonic", "password",
        "private_key", "refresh_token", "secret", "seed",
    }
    if isinstance(value, Mapping):
        return any(str(key).lower() in forbidden or _contains_sensitive(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    return False


def _resolve(paths: PrivateStatePaths, locator: Any, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_PATH_UNSAFE", f"{label} locator must be relative POSIX"
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_PATH_UNSAFE", f"{label} locator is unsafe"
        )
    result = (paths.root / candidate).resolve(strict=False)
    try:
        result.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_PATH_UNSAFE", f"{label} locator escapes Mother state"
        ) from exc
    return result


def _relative(paths: PrivateStatePaths, path: Path, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_PATH_UNSAFE", f"{label} is outside Mother state"
        ) from exc


def _canonical_under(
    paths: PrivateStatePaths,
    path: Path,
    directory: tuple[str, str],
    label: str,
) -> tuple[dict[str, Any], bytes, str]:
    expected = (paths.root / directory[0] / directory[1]).resolve(strict=False)
    candidate = path.resolve(strict=False)
    try:
        candidate.relative_to(expected)
    except ValueError as exc:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_PATH_UNSAFE", f"{label} is outside its canonical directory"
        ) from exc
    try:
        raw = candidate.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_INVALID", f"{label} is not readable canonical JSON"
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_INVALID", f"{label} is not canonical JSON"
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: value for key, value in document.items() if key != field})).hexdigest()


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_STATE_INVALID", f"{path} must be a mapping"
        )
    return value


def _validator_addresses(private_state: PrivateStateReadResult, network: str) -> tuple[str, str]:
    document = _private_document(private_state)
    networks = _mapping(document.get("networks"), "networks")
    network_state = _mapping(networks.get(network), f"networks.{network}")
    validators = _mapping(network_state.get("validators"), f"networks.{network}.validators")
    nodes = _mapping(network_state.get("nodes"), f"networks.{network}.nodes")
    result: list[str] = []
    for node in ("mainneta-super1", "mainnetc-super1"):
        reservation = _mapping(nodes.get(node), f"networks.{network}.nodes.{node}")
        expected_ref = f"networks.{network}.validators.{node}"
        if reservation.get("validator_ref") != expected_ref:
            raise MotherDeploymentValidatorAdmissionError(
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_STATE_INVALID", f"{node} does not reference its canonical validator"
            )
        validator = _mapping(validators.get(node), expected_ref)
        result.append(_address(validator.get("address"), f"{expected_ref}.address"))
    if result[0] == result[1]:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_STATE_INVALID", "A and C validator addresses must differ"
        )
    return result[0], result[1]


def build_validator_admission_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    synchronization_evidence_path: Path,
    *,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    created_at: str | None = None,
) -> dict[str, Any]:
    network = _identifier(network, "network")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != ("mainnetc-super1",):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_SELECTION_MISMATCH",
            "validator admission may target only mainnetc-super1",
        )
    verified = verify_soft_replica_sync_evidence(
        paths,
        private_state,
        Path(synchronization_evidence_path),
        selected_nodes=("mainnetc-super1",),
        max_age_seconds=max_age_seconds,
    )
    if verified.get("network") != network or verified.get("next_phase") != "stage-validator-admission-transaction":
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EVIDENCE_INVALID",
            "synchronization evidence does not authorize transaction staging",
        )
    evidence, _, evidence_sha = _canonical_under(
        paths,
        Path(synchronization_evidence_path),
        _SYNC_EVIDENCE_DIRECTORY,
        "synchronization evidence",
    )
    release_ref = evidence.get("release")
    if not isinstance(release_ref, Mapping):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "synchronization release binding is missing"
        )
    release_path = _resolve(paths, release_ref.get("locator"), "synchronization release")
    release, _, _ = _canonical_under(
        paths, release_path, _SYNC_RELEASE_DIRECTORY, "synchronization release"
    )
    release_digest = _digest_without(release, "soft_replica_sync_release_sha256")
    if not all([
        release.get("kind") == _SYNC_RELEASE_KIND,
        release.get("soft_replica_sync_release_sha256") == release_digest,
        release_digest == release_ref.get("sha256"),
        release.get("mother_binding") == _binding(private_state),
    ]):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "synchronization release binding is invalid"
        )
    proof = _mapping(evidence.get("proof"), "evidence.proof")
    authority = _mapping(evidence.get("authority"), "evidence.authority")
    summary = _mapping(evidence.get("summary"), "evidence.summary")
    if authority.get("validator_vote_authorized") is not False or summary.get("replica_synchronized") is not True:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "synchronization evidence authority is invalid"
        )
    initial_address, replica_address = _validator_addresses(private_state, network)
    validator_set = proof.get("validator_set")
    if type(validator_set) is not list or [_address(item, "proof.validator_set") for item in validator_set] != [initial_address]:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_VALIDATOR_SET_INVALID",
            "synchronization evidence does not prove A as the sole current validator",
        )
    initial = _mapping(release.get("initial_chain_precondition"), "release.initial_chain_precondition")
    plan = _mapping(release.get("proof_plan"), "release.proof_plan")
    if not all([
        initial.get("node") == "mainneta-super1",
        initial.get("controller_id") == "coolify-a",
        plan.get("replica_node") == "mainnetc-super1",
        plan.get("controller_id") == "coolify-c",
        _address(plan.get("replica_validator_address"), "release.replica_validator_address") == replica_address,
    ]):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "synchronization release node bindings are invalid"
        )
    rpc_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "qbft_proposeValidatorVote",
        "params": [replica_address, True],
    }
    rpc_request_sha = hashlib.sha256(canonical_json(rpc_request)).hexdigest()
    created_text = _timestamp(created_at)
    created = _parse_utc(created_text, "created_at")
    reference = datetime.now(timezone.utc)
    if created > reference.replace(microsecond=0):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_INVALID", "transaction creation time is in the future"
        )
    current_set = [initial_address]
    desired_set = [initial_address, replica_address]
    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "network": network,
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "mother_binding": _binding(private_state),
        "staged_scope": "stage-validator-admission-without-casting-vote",
        "synchronization_evidence": {
            "locator": _relative(paths, Path(synchronization_evidence_path), "synchronization evidence"),
            "sha256": evidence_sha,
            "completed_at": evidence.get("completed_at"),
        },
        "authority": {
            "transaction_apply_authorized": False,
            "live_execution_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "operator_release_required": True,
        },
        "policy": {
            "compiler": "mother-native-qbft-validator-admission-v1",
            "network_access_performed": False,
            "live_mutation_performed": False,
            "qbft_vote_performed": False,
            "validator_activation_performed": False,
            "initial_node_read_only_during_staging": True,
            "replica_node_read_only_during_staging": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "current_chain": {
            "chain_id": verified["chain_id"],
            "genesis_sha256": verified["genesis_sha256"],
            "initial_node": "mainneta-super1",
            "replica_node": "mainnetc-super1",
            "current_validator_set": current_set,
            "replica_synchronized": True,
            "initial_chain_reverified": True,
            "initial_service": {
                "controller_id": initial["controller_id"],
                "service_uuid": _identifier(initial.get("service_uuid"), "initial service UUID"),
                "proof_compose_sha256": _sha256(
                    _mapping(initial.get("proof_compose"), "initial proof Compose").get("sha256"),
                    "initial proof Compose SHA-256",
                ),
            },
            "replica_service": {
                "controller_id": plan["controller_id"],
                "service_uuid": _identifier(plan.get("service_uuid"), "replica service UUID"),
                "proof_compose_sha256": _sha256(
                    _mapping(plan.get("proof_compose"), "replica proof Compose").get("sha256"),
                    "replica proof Compose SHA-256",
                ),
            },
        },
        "admission": {
            "candidate_node": "mainnetc-super1",
            "candidate_validator_address": replica_address,
            "current_validator_set": current_set,
            "desired_validator_set": desired_set,
            "vote_origin_node": "mainneta-super1",
            "vote_origin_controller_id": initial["controller_id"],
            "vote_origin_service_uuid": _identifier(initial.get("service_uuid"), "vote origin service UUID"),
            "transport": "internal-only-rpc-guardian-through-coolify-control-plane",
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "rpc_request": rpc_request,
            "rpc_request_sha256": rpc_request_sha,
            "required_preconditions": [
                "A remains running:healthy under its exact birth-proof Compose",
                "C remains running:healthy under its exact synchronization-proof Compose",
                "C remains synchronized to A on the committed chain",
                "current QBFT validator set remains exactly A",
            ],
            "required_postconditions": [
                "QBFT validator set becomes exactly A plus C",
                "A remains healthy and producing blocks",
                "C remains healthy and synchronized",
            ],
            "vote_cast": False,
        },
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_REQUIRED",
                "message": "an explicit expiring operator release is required for this exact admission transaction",
            },
            {
                "code": "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_NOT_IMPLEMENTED",
                "message": "the one-use internal QBFT vote executor is not implemented in this patch",
            },
        ],
        "summary": {
            "transaction_valid": True,
            "candidate_node": "mainnetc-super1",
            "current_validator_count": 1,
            "desired_validator_count": 2,
            "logical_vote_count": 1,
            "persisted_secret_value_count": 0,
            "transaction_apply_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "next_phase": "release-and-execute-validator-admission",
            "blocker_codes": [
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_NOT_IMPLEMENTED",
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_REQUIRED",
            ],
        },
        "validator_admission_transaction_sha256": None,
    }
    transaction["validator_admission_transaction_sha256"] = _digest_without(
        transaction, "validator_admission_transaction_sha256"
    )
    if _contains_sensitive(transaction):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_INVALID", "validator-admission transaction contains sensitive material"
        )
    return transaction


def write_validator_admission_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(transaction)
    digest = _digest_without(document, "validator_admission_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("validator_admission_transaction_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_INVALID", "validator-admission transaction is malformed"
        )
    payload = canonical_json(document)
    current = paths.root
    for part in _TRANSACTION_DIRECTORY:
        current /= part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "admission"
    network = _identifier(document.get("network"), "network")
    destination = current / f"{stamp}-{network}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentValidatorAdmissionError(
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_CONFLICT", "transaction destination contains different bytes"
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_WRITE_FAILED", "transaction verification after write failed"
        )
    return destination, digest


def verify_validator_admission_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    document, raw, byte_sha = _canonical_under(
        paths,
        Path(transaction_path),
        _TRANSACTION_DIRECTORY,
        "validator-admission transaction",
    )
    digest = _digest_without(document, "validator_admission_transaction_sha256")
    if not all([
        document.get("kind") == _TRANSACTION_KIND,
        document.get("validator_admission_transaction_sha256") == digest,
        document.get("mother_binding") == _binding(private_state),
        not _contains_sensitive(document),
    ]):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_TRANSACTION_INVALID",
            "validator-admission transaction is invalid or stale",
        )
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != ("mainnetc-super1",):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_SELECTION_MISMATCH",
            "validator admission may target only mainnetc-super1",
        )
    created = _parse_utc(document.get("created_at"), "created_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - created).total_seconds())
    if age < -1 or age > max_age_seconds:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_TRANSACTION_STALE",
            "validator-admission transaction is outside the freshness window",
        )
    evidence_ref = document.get("synchronization_evidence")
    if not isinstance(evidence_ref, Mapping):
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_TRANSACTION_INVALID",
            "synchronization evidence binding is missing",
        )
    evidence_path = _resolve(paths, evidence_ref.get("locator"), "synchronization evidence")
    expected = build_validator_admission_transaction(
        paths,
        private_state,
        evidence_path,
        network=_identifier(document.get("network"), "network"),
        selected_nodes=("mainnetc-super1",),
        max_age_seconds=max_age_seconds,
        created_at=document.get("created_at"),
    )
    if canonical_json(expected) != raw:
        raise MotherDeploymentValidatorAdmissionError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_TRANSACTION_INVALID",
            "validator-admission transaction no longer matches current inputs",
        )
    admission = document["admission"]
    current = document["current_chain"]
    return {
        "clean": True,
        "transaction_path": str(Path(transaction_path).resolve(strict=False)),
        "validator_admission_transaction_sha256": digest,
        "byte_sha256": byte_sha,
        "age_seconds": max(0, age),
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "nodes": [admission["candidate_node"]],
        "initial_node": current["initial_node"],
        "candidate_node": admission["candidate_node"],
        "candidate_validator_address": admission["candidate_validator_address"],
        "chain_id": current["chain_id"],
        "genesis_sha256": current["genesis_sha256"],
        "current_validator_set": list(admission["current_validator_set"]),
        "desired_validator_set": list(admission["desired_validator_set"]),
        "rpc_method": admission["rpc_request"]["method"],
        "rpc_request_sha256": admission["rpc_request_sha256"],
        "persisted_secret_value_count": 0,
        "transaction_apply_authorized": False,
        "validator_vote_authorized": False,
        "validator_activation_authorized": False,
        "live_execution_authorized": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "staged_scope": document["staged_scope"],
        "next_phase": "release-and-execute-validator-admission",
    }


__all__ = [
    "MotherDeploymentValidatorAdmissionError",
    "build_validator_admission_transaction",
    "verify_validator_admission_transaction",
    "write_validator_admission_transaction",
]
