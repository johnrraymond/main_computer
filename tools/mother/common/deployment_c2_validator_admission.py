"""C2 validator admission planning gate.

This module consumes clean C2 replica-sync evidence and compiles the exact
QBFT admission intent for ``mainnetc-super2``.  It deliberately stops at the
operator-release boundary in this patch: no vote is cast, no validator is
activated, and no routing/topology publication is authorized here.
"""""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any

import yaml

from . import atomic_files
from .canonical import canonical_json
from .deployment_c2_replica_sync import (
    verify_c2_replica_sync_evidence,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_C2_NODE = "mainnetc-super2"
_A1_NODE = "mainneta-super1"
_C1_NODE = "mainnetc-super1"
_VOTER_NODES = (_A1_NODE, _C1_NODE)
_C2_CONTROLLER = "coolify-c"

_TRANSACTION_KIND = "main_computer.mother.deployment_c2_validator_admission_transaction.v1"
_RELEASE_KIND = "main_computer.mother.deployment_c2_validator_admission_release.v1"
_TRANSACTION_DIRECTORY = ("actions", "deployment-c2-validator-admission-transactions")
_RELEASE_DIRECTORY = ("actions", "deployment-c2-validator-admission-releases")
_SYNC_EVIDENCE_DIRECTORY = ("evidence", "deployment-c2-replica-sync")

_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 3600


class MotherDeploymentC2ValidatorAdmissionError(Exception):
    """C2 validator admission planning failed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentC2ValidatorAdmissionError:
    return MotherDeploymentC2ValidatorAdmissionError(code, message)


def _identifier(value: Any, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", f"{label} is missing")
    text = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:/@+-]+", text):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", f"{label} contains unsafe characters")
    return text


def _sha256(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", f"{label} is not a SHA-256 digest")
    return text


def _address(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if re.fullmatch(r"0x[0-9a-f]{40}", text) is None:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", f"{label} is not an address")
    return text


def _parse_utc(value: Any, label: str) -> datetime:
    if type(value) is not str:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", f"{label} is missing")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", f"{label} must include UTC")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None = None) -> str:
    parsed = _parse_utc(value, "timestamp") if value is not None else datetime.now(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _age(value: Any, *, now: datetime | None) -> int:
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - _parse_utc(value, "timestamp")).total_seconds())
    if age < -1:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_FUTURE", "timestamp is in the future")
    return max(0, age)


def _duration(seconds: int) -> int:
    if not (_MIN_RELEASE_SECONDS <= int(seconds) <= _MAX_RELEASE_SECONDS):
        raise _fail(
            "MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_INVALID",
            f"release duration must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS} seconds",
        )
    return int(seconds)


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        data = yaml.safe_load(private_state.document_bytes)
    except yaml.YAMLError as exc:  # pragma: no cover
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_STATE_INVALID", "private state cannot be parsed") from exc
    if not isinstance(data, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_STATE_INVALID", "private state root is invalid")
    return dict(data)


def _root(paths: PrivateStatePaths, parts: tuple[str, ...]) -> Path:
    current = paths.root
    for part in parts:
        current /= part
    return current


def _ensure_directory(paths: PrivateStatePaths, parts: tuple[str, ...], *, operation: OperationIdentity) -> Path:
    current = paths.root
    for part in parts:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    try:
        return Path(path).resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_PATH_INVALID", f"{label} is outside runtime state") from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator.strip():
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_PATH_INVALID", f"{label} locator is missing")
    text = locator.replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text) or ".." in PureWindowsPath(text).parts or ".." in Path(text).parts:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_PATH_INVALID", f"{label} locator is unsafe")
    return (paths.root / Path(text)).resolve(strict=False)


def _beneath(paths: PrivateStatePaths, path: Path, directory: tuple[str, ...], *, label: str) -> Path:
    candidate = Path(path).resolve(strict=False)
    base = _root(paths, directory).resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_PATH_INVALID", f"{label} must be beneath {'/'.join(directory)}") from exc
    return candidate


def _canonical_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", f"{label} could not be read as canonical JSON") from exc
    if not isinstance(data, dict) or canonical_json(data) != raw:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", f"{label} is not canonical JSON")
    return data, raw, hashlib.sha256(raw).hexdigest()


def _canonical_under(paths: PrivateStatePaths, path: Path, directory: tuple[str, ...], label: str) -> tuple[dict[str, Any], bytes, str]:
    return _canonical_file(_beneath(paths, Path(path), directory, label=label), label=label)


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    copy = dict(document)
    copy[field] = None
    return hashlib.sha256(canonical_json(copy)).hexdigest()


def _contains_sensitive(document: Any) -> bool:
    sensitive_keys = {"private_key", "secret", "api_token", "token", "password", "value"}
    if isinstance(document, Mapping):
        for key, value in document.items():
            name = str(key).lower()
            if any(marker in name for marker in sensitive_keys):
                if isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value.strip()):
                    return True
                if isinstance(value, str) and "THISISASECRETTOKENVALUE" in value:
                    return True
            if _contains_sensitive(value):
                return True
    elif isinstance(document, list):
        return any(_contains_sensitive(item) for item in document)
    return False


def _validator_address(private_state: PrivateStateReadResult, *, network: str, node: str) -> str:
    state = _document(private_state)
    try:
        value = state["networks"][network]["validators"][node]["address"]
    except (KeyError, TypeError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_STATE_INVALID", f"{node} validator address is missing") from exc
    return _address(value, f"{node} validator address")


def _controller_for_node(node: str) -> str:
    if node == _A1_NODE:
        return "coolify-a"
    if node in {_C1_NODE, _C2_NODE}:
        return "coolify-c"
    raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", f"unsupported node {node!r}")


def build_c2_validator_admission_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    sync_evidence_path: Path,
    *,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    network = _identifier(network, "network")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (_C2_NODE,):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_SELECTION_MISMATCH", "C2 validator admission targets only mainnetc-super2")

    verified = verify_c2_replica_sync_evidence(
        paths,
        private_state,
        Path(sync_evidence_path),
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if verified.get("network") != network or verified.get("next_phase") != "stage-c2-validator-admission":
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "C2 sync evidence does not authorize admission")

    evidence, _, evidence_sha = _canonical_under(paths, Path(sync_evidence_path), _SYNC_EVIDENCE_DIRECTORY, "C2 replica sync evidence")
    proof = evidence.get("proof")
    if not isinstance(proof, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "C2 sync proof is missing")

    current_set = [_address(item, "current validator") for item in verified["expected_validator_set"]]
    if len(current_set) != 2:
        raise _fail(
            "MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_CURRENT_SET_INVALID",
            "C2 admission requires the existing A1+C1 validator set",
        )
    candidate = _validator_address(private_state, network=network, node=_C2_NODE)
    if candidate in current_set:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_ALREADY_ACTIVE", "C2 validator is already in the current set")
    desired_set = current_set + [candidate]
    vote_requests = [
        {
            "voter_node": node,
            "controller_id": _controller_for_node(node),
            "rpc_request": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "qbft_proposeValidatorVote",
                "params": [candidate, True],
            },
        }
        for node in _VOTER_NODES
    ]
    for item in vote_requests:
        item["rpc_request_sha256"] = hashlib.sha256(canonical_json(item["rpc_request"])).hexdigest()

    created_text = _timestamp(created_at)
    created = _parse_utc(created_text, "created_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if created > reference.replace(microsecond=0):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", "transaction creation time is in the future")

    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "network": network,
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "mother_binding": _binding(private_state),
        "staged_scope": "stage-c2-validator-admission-without-casting-votes",
        "sync_evidence": {
            "locator": _relative(paths, Path(sync_evidence_path), label="C2 replica sync evidence"),
            "sha256": evidence_sha,
            "completed_at": evidence.get("completed_at"),
        },
        "candidate": {
            "node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "service_uuid": _identifier(evidence.get("service_uuid"), "C2 service UUID"),
            "validator_address": candidate,
            "replica_sync_evidence_sha256": evidence_sha,
            "replica_synchronized": True,
            "validator_active_before_admission": False,
        },
        "current_chain": {
            "initial_node": verified["initial_node"],
            "replica_node": _C2_NODE,
            "chain_id": verified["chain_id"],
            "genesis_sha256": verified["genesis_sha256"],
            "current_validator_set": current_set,
            "desired_validator_set": desired_set,
            "current_validator_count": len(current_set),
            "desired_validator_count": len(desired_set),
            "c2_replica_synchronized": True,
            "routing_or_topology_published": False,
        },
        "admission": {
            "candidate_node": _C2_NODE,
            "candidate_validator_address": candidate,
            "voter_nodes": list(_VOTER_NODES),
            "vote_threshold": "all-existing-validators",
            "logical_vote_count": len(_VOTER_NODES),
            "rpc_requests": vote_requests,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "rpc_routing_or_topology_publication_authorized": False,
            "vote_cast": False,
        },
        "authority": {
            "transaction_apply_authorized": False,
            "live_execution_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "operator_release_required": True,
        },
        "policy": {
            "compiler": "mother-native-c2-qbft-validator-admission-v1",
            "network_access_performed": False,
            "live_mutation_performed": False,
            "qbft_vote_performed": False,
            "validator_activation_performed": False,
            "routing_or_topology_published": False,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_REQUIRED",
                "message": "an explicit expiring operator release is required for this exact C2 validator-admission transaction",
            },
            {
                "code": "MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_EXECUTOR_NOT_IMPLEMENTED",
                "message": "the two-voter internal QBFT admission executor is intentionally deferred to the next patch",
            },
        ],
        "summary": {
            "transaction_valid": True,
            "candidate_node": _C2_NODE,
            "current_validator_count": len(current_set),
            "desired_validator_count": len(desired_set),
            "logical_vote_count": len(_VOTER_NODES),
            "all_existing_validators_must_vote": True,
            "c2_replica_sync_proven": True,
            "persisted_secret_value_count": 0,
            "transaction_apply_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "next_phase": "release-c2-validator-admission",
            "blocker_codes": [
                "MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_EXECUTOR_NOT_IMPLEMENTED",
                "MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_REQUIRED",
            ],
        },
        "c2_validator_admission_transaction_sha256": None,
    }
    transaction["c2_validator_admission_transaction_sha256"] = _digest_without(
        transaction, "c2_validator_admission_transaction_sha256"
    )
    if _contains_sensitive(transaction):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", "transaction contains sensitive material")
    return transaction


def write_c2_validator_admission_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(transaction)
    digest = _digest_without(document, "c2_validator_admission_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("c2_validator_admission_transaction_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", "transaction is malformed")
    payload = canonical_json(document)
    current = _ensure_directory(paths, _TRANSACTION_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "c2admission"
    destination = current / f"{stamp}-{document['network']}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_CONFLICT", "transaction destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_c2_validator_admission_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    document, raw, byte_sha = _canonical_under(paths, Path(transaction_path), _TRANSACTION_DIRECTORY, "C2 validator-admission transaction")
    digest = _digest_without(document, "c2_validator_admission_transaction_sha256")
    if not all([
        document.get("kind") == _TRANSACTION_KIND,
        document.get("c2_validator_admission_transaction_sha256") == digest,
        document.get("mother_binding") == _binding(private_state),
        not _contains_sensitive(document),
    ]):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_TRANSACTION_INVALID", "transaction is invalid or stale")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (_C2_NODE,):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_SELECTION_MISMATCH", "C2 validator admission targets only mainnetc-super2")
    age = _age(document.get("created_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_TRANSACTION_STALE", "transaction is outside the freshness window")
    sync_ref = document.get("sync_evidence")
    if not isinstance(sync_ref, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_TRANSACTION_INVALID", "sync evidence binding is missing")
    evidence_path = _resolve_locator(paths, sync_ref.get("locator"), label="C2 replica sync evidence")
    expected = build_c2_validator_admission_transaction(
        paths,
        private_state,
        evidence_path,
        network=_identifier(document.get("network"), "network"),
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        created_at=document.get("created_at"),
        now=now,
    )
    if canonical_json(expected) != raw:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_TRANSACTION_INVALID", "transaction no longer matches current inputs")
    admission = document["admission"]
    current = document["current_chain"]
    return {
        "clean": True,
        "transaction_path": str(Path(transaction_path).resolve(strict=False)),
        "c2_validator_admission_transaction_sha256": digest,
        "byte_sha256": byte_sha,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "nodes": [_C2_NODE],
        "candidate_node": _C2_NODE,
        "candidate_validator_address": admission["candidate_validator_address"],
        "voter_nodes": list(admission["voter_nodes"]),
        "logical_vote_count": admission["logical_vote_count"],
        "chain_id": current["chain_id"],
        "genesis_sha256": current["genesis_sha256"],
        "current_validator_set": list(current["current_validator_set"]),
        "desired_validator_set": list(current["desired_validator_set"]),
        "transaction_apply_authorized": False,
        "validator_vote_authorized": False,
        "validator_activation_authorized": False,
        "routing_or_topology_publication_authorized": False,
        "live_execution_authorized": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "staged_scope": document["staged_scope"],
        "next_phase": "release-c2-validator-admission",
    }


def build_c2_validator_admission_release(
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
    acknowledged = _sha256(acknowledged_transaction_sha256, "acknowledged transaction SHA-256")
    verified = verify_c2_validator_admission_transaction(
        paths,
        private_state,
        Path(transaction_path),
        selected_nodes=selected_nodes or (_C2_NODE,),
        max_age_seconds=transaction_max_age_seconds,
        now=now,
    )
    if acknowledged != verified["c2_validator_admission_transaction_sha256"]:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_ACKNOWLEDGEMENT_MISMATCH", "operator acknowledgement does not match the exact transaction")
    transaction, _, tx_byte_sha = _canonical_under(paths, Path(verified["transaction_path"]), _TRANSACTION_DIRECTORY, "C2 validator-admission transaction")
    created_text = _timestamp(created_at)
    created = _parse_utc(created_text, "created_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if created > reference.replace(microsecond=0):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_INVALID", "release creation time is in the future")
    expires = created + timedelta(seconds=_duration(expires_in_seconds))
    admission = transaction["admission"]
    current = transaction["current_chain"]

    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "network": transaction["network"],
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "mother_binding": _binding(private_state),
        "staged_scope": "release-c2-validator-admission-executor-not-yet-implemented",
        "transaction": {
            "locator": _relative(paths, Path(verified["transaction_path"]), label="C2 validator-admission transaction"),
            "sha256": verified["c2_validator_admission_transaction_sha256"],
            "byte_sha256": tx_byte_sha,
        },
        "sync_evidence": dict(transaction["sync_evidence"]),
        "candidate": dict(transaction["candidate"]),
        "admission_plan": {
            "candidate_node": _C2_NODE,
            "candidate_validator_address": admission["candidate_validator_address"],
            "voter_nodes": list(admission["voter_nodes"]),
            "vote_threshold": admission["vote_threshold"],
            "logical_vote_count": admission["logical_vote_count"],
            "current_validator_set": list(current["current_validator_set"]),
            "desired_validator_set": list(current["desired_validator_set"]),
            "rpc_requests": list(admission["rpc_requests"]),
            "proof_required": [
                "A1 and C1 remain current validators before votes",
                "C2 remains synchronized before admission",
                "A1 vote for C2 is accepted",
                "C1 vote for C2 is accepted",
                "final QBFT validator set is exactly A1+C1+C2",
                "blocks remain fresh after admission",
            ],
        },
        "authority": {
            "authorization_source": "explicit-operator-release",
            "transaction_apply_authorized": True,
            "validator_vote_authorized": True,
            "validator_activation_authorized": True,
            "live_execution_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "requested_use_limit": 1,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "coolify_control_plane_only": True,
            "two_existing_validator_votes_required": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "routing_or_topology_published": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "executor_implemented": False,
        },
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_EXECUTOR_NOT_IMPLEMENTED",
                "message": "this release proves the C2 two-voter admission boundary; a follow-up executor patch must consume it",
            },
            {
                "code": "MOTHER_DEPLOY_C2_RPC_ROUTING_NOT_AUTHORIZED",
                "message": "validator admission does not authorize C2 RPC routing or topology publication",
            },
        ],
        "summary": {
            "release_valid": True,
            "candidate_node": _C2_NODE,
            "current_validator_count": len(current["current_validator_set"]),
            "desired_validator_count": len(current["desired_validator_set"]),
            "logical_vote_count": admission["logical_vote_count"],
            "two_existing_validator_votes_required": True,
            "validator_vote_authorized": True,
            "validator_activation_authorized": True,
            "live_execution_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "executor_implemented": False,
            "next_phase_after_apply": "implement-c2-validator-admission-executor",
        },
        "c2_validator_admission_release_sha256": None,
    }
    release["c2_validator_admission_release_sha256"] = _digest_without(release, "c2_validator_admission_release_sha256")
    if _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_INVALID", "release contains sensitive material")
    return release


def write_c2_validator_admission_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(document, "c2_validator_admission_release_sha256")
    if (
        document.get("kind") != _RELEASE_KIND
        or document.get("c2_validator_admission_release_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_INVALID", "release is malformed")
    payload = canonical_json(document)
    current = _ensure_directory(paths, _RELEASE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "c2admissionrelease"
    destination = current / f"{stamp}-{document['network']}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_CONFLICT", "release destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_c2_validator_admission_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    release, raw, byte_sha = _canonical_under(paths, Path(release_path), _RELEASE_DIRECTORY, "C2 validator-admission release")
    digest = _digest_without(release, "c2_validator_admission_release_sha256")
    if not all([
        release.get("kind") == _RELEASE_KIND,
        release.get("c2_validator_admission_release_sha256") == digest,
        release.get("mother_binding") == _binding(private_state),
        not _contains_sensitive(release),
    ]):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_INVALID", "release is invalid or stale")
    created_age = _age(release.get("created_at"), now=now)
    if created_age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_STALE", "release is outside the freshness window")
    expires = _parse_utc(release.get("expires_at"), "expires_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if reference > expires:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_EXPIRED", "release expired")
    tx_ref = release.get("transaction")
    if not isinstance(tx_ref, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_INVALID", "transaction binding is missing")
    tx_path = _resolve_locator(paths, tx_ref.get("locator"), label="C2 validator-admission transaction")
    verified_tx = verify_c2_validator_admission_transaction(
        paths,
        private_state,
        tx_path,
        selected_nodes=selected_nodes or (_C2_NODE,),
        max_age_seconds=transaction_max_age_seconds,
        now=now,
    )
    if verified_tx["c2_validator_admission_transaction_sha256"] != tx_ref.get("sha256"):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_INVALID", "transaction digest mismatch")
    expected = build_c2_validator_admission_release(
        paths,
        private_state,
        tx_path,
        acknowledged_transaction_sha256=verified_tx["c2_validator_admission_transaction_sha256"],
        selected_nodes=selected_nodes or (_C2_NODE,),
        transaction_max_age_seconds=transaction_max_age_seconds,
        expires_in_seconds=int((expires - _parse_utc(release["created_at"], "created_at")).total_seconds()),
        created_at=release["created_at"],
        now=now,
    )
    if canonical_json(expected) != raw:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_INVALID", "release no longer matches current inputs")
    return {
        "clean": True,
        "release_path": str(Path(release_path).resolve(strict=False)),
        "c2_validator_admission_release_sha256": digest,
        "byte_sha256": byte_sha,
        "age_seconds": created_age,
        "expires_at": release["expires_at"],
        "mother_binding": dict(release["mother_binding"]),
        "network": release["network"],
        "nodes": [_C2_NODE],
        "candidate_node": _C2_NODE,
        "candidate_validator_address": release["candidate"]["validator_address"],
        "voter_nodes": list(release["admission_plan"]["voter_nodes"]),
        "current_validator_set": list(release["admission_plan"]["current_validator_set"]),
        "desired_validator_set": list(release["admission_plan"]["desired_validator_set"]),
        "logical_vote_count": release["admission_plan"]["logical_vote_count"],
        "validator_vote_authorized": True,
        "validator_activation_authorized": True,
        "live_execution_authorized": False,
        "routing_or_topology_publication_authorized": False,
        "executor_implemented": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "remaining_blocker_codes": [item["code"] for item in release.get("remaining_blockers", [])],
        "next_phase": "implement-c2-validator-admission-executor",
    }


def inspect_c2_validator_admission_release(
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
    acknowledged = _sha256(acknowledged_release_sha256, "acknowledged release SHA-256")
    verified = verify_c2_validator_admission_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=selected_nodes or (_C2_NODE,),
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        now=now,
    )
    if acknowledged != verified["c2_validator_admission_release_sha256"]:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_ACKNOWLEDGEMENT_MISMATCH", "operator acknowledgement does not match release")
    return {
        **verified,
        "execute_requested": False,
        "release_already_claimed": False,
        "executor_implemented": False,
        "live_execution_authorized": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "summary": {
            "clean": True,
            "candidate_node": _C2_NODE,
            "current_validator_count": len(verified["current_validator_set"]),
            "desired_validator_count": len(verified["desired_validator_set"]),
            "logical_vote_count": verified["logical_vote_count"],
            "two_existing_validator_votes_required": True,
            "validator_vote_authorized": True,
            "validator_activation_authorized": True,
            "executor_implemented": False,
            "live_mutation_performed": False,
            "validator_vote_performed": False,
            "routing_or_topology_publication_authorized": False,
            "next_phase": "implement-c2-validator-admission-executor",
        },
    }


__all__ = [
    "MotherDeploymentC2ValidatorAdmissionError",
    "build_c2_validator_admission_transaction",
    "write_c2_validator_admission_transaction",
    "verify_c2_validator_admission_transaction",
    "build_c2_validator_admission_release",
    "write_c2_validator_admission_release",
    "verify_c2_validator_admission_release",
    "inspect_c2_validator_admission_release",
]
