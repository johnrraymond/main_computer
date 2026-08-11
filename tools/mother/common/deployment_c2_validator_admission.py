"""C2 validator admission planning gate.

This module consumes clean C2 replica-sync evidence and compiles the exact
QBFT admission intent for ``mainnetc-super2``.  It deliberately stops at the
operator-release boundary in this patch: no vote is cast, no validator is
activated, and no routing/topology publication is authorized here.
"""""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
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
_CLAIM_DIRECTORY = ("actions", "deployment-c2-validator-admission-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-c2-validator-admission")
_SYNC_EVIDENCE_DIRECTORY = ("evidence", "deployment-c2-replica-sync")
_CLAIM_KIND = "main_computer.mother.deployment_c2_validator_admission_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_c2_validator_admission_evidence.v1"

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
        "chain_id": verified_tx["chain_id"],
        "genesis_sha256": verified_tx["genesis_sha256"],
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
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{verified['c2_validator_admission_release_sha256']}.json"
    return {
        **verified,
        "execute_requested": False,
        "release_already_claimed": claim_path.exists(),
        "executor_implemented": True,
        "live_execution_authorized": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "resolved_blocker_codes": ["MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_EXECUTOR_NOT_IMPLEMENTED"],
        "remaining_blocker_codes": ["MOTHER_DEPLOY_C2_RPC_ROUTING_NOT_AUTHORIZED"],
        "summary": {
            "clean": True,
            "candidate_node": _C2_NODE,
            "current_validator_count": len(verified["current_validator_set"]),
            "desired_validator_count": len(verified["desired_validator_set"]),
            "logical_vote_count": verified["logical_vote_count"],
            "two_existing_validator_votes_required": True,
            "validator_vote_authorized": True,
            "validator_activation_authorized": True,
            "executor_implemented": True,
            "live_execution_authorized": True,
            "live_mutation_performed": False,
            "validator_vote_performed": False,
            "routing_or_topology_publication_authorized": False,
            "next_phase": "execute-c2-validator-admission",
        },
    }


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    return opener.open(request, timeout=timeout) if hasattr(opener, "open") else opener(request, timeout=timeout)


def _http(
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
        "User-Agent": "main-computer-mother-c2-validator-admission/1",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        controller.base_url.rstrip("/") + endpoint,
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
    except (urllib.error.URLError, OSError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_REQUEST_FAILED", "Coolify request failed") from exc
    if len(raw) > max_response_bytes:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RESPONSE_TOO_LARGE", "Coolify response is too large")
    try:
        parsed: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = raw.decode("utf-8", errors="replace")
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "payload": parsed,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


def _safe_response(response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": int(response.get("status", 0)),
        "ok": bool(response.get("ok")),
        "response_sha256": _sha256(response.get("response_sha256"), "response SHA-256")
        if re.fullmatch(r"[0-9a-f]{64}", str(response.get("response_sha256", "")))
        else str(response.get("response_sha256", "")),
        "byte_length": int(response.get("byte_length", 0)),
        "elapsed_ms": int(response.get("elapsed_ms", 0)),
    }


def _records(payload: Any) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    if isinstance(payload, Mapping):
        if any(key in payload for key in ("uuid", "id", "name")):
            records.append(payload)
        for key in ("data", "service", "resource", "application"):
            value = payload.get(key)
            if isinstance(value, Mapping):
                records.append(value)
            elif isinstance(value, list):
                records.extend(item for item in value if isinstance(item, Mapping))
        for key in ("services", "resources", "applications", "databases"):
            value = payload.get(key)
            if isinstance(value, list):
                records.extend(item for item in value if isinstance(item, Mapping))
    elif isinstance(payload, list):
        records.extend(item for item in payload if isinstance(item, Mapping))
    return records


def _children(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    children: list[Mapping[str, Any]] = []
    for key in ("applications", "services", "databases"):
        value = record.get(key)
        if isinstance(value, list):
            children.extend(item for item in value if isinstance(item, Mapping))
    return children


def _find_service_record(payload: Any, *, node: str, service_uuid: str | None = None) -> Mapping[str, Any]:
    candidates = [
        item for item in _records(payload)
        if (service_uuid is not None and item.get("uuid") == service_uuid)
        or (service_uuid is None and item.get("name") == node)
        or (item.get("name") == node and (service_uuid is None or item.get("uuid") == service_uuid))
    ]
    top = [item for item in candidates if item.get("name") == node or item.get("uuid") == service_uuid]
    if len(top) != 1:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_SERVICE_MISMATCH", f"expected one service record for {node}; found {len(top)}")
    return top[0]


def _service_status(record: Mapping[str, Any]) -> str:
    value = record.get("status", "")
    return value if isinstance(value, str) else ""


def _component_healthy(record: Mapping[str, Any], *, node: str) -> bool:
    status = _service_status(record)
    if status == "running:healthy":
        return True
    for child in _children(record):
        if child.get("name") == node and child.get("status") == "running:healthy":
            return True
    return False


def _compose_text(record: Mapping[str, Any]) -> str:
    for key in ("docker_compose_raw", "docker_compose", "dockerComposeRaw", "dockerCompose"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if "\n" in text or text.startswith(("name:", "services:", "version:")):
            return text
        try:
            decoded = base64.b64decode(text, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if "services:" in decoded:
            return decoded
    raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_COMPOSE_MISSING", "Coolify service record has no Compose text")


def _controller_for_voter(voter: str) -> str:
    return "coolify-a" if voter == _A1_NODE else "coolify-c"


def _guardian_service_name(voter: str) -> str:
    suffix = "a1" if voter == _A1_NODE else "c1"
    return f"mother-c2-validator-admission-voter-{suffix}"


def _voter_script(
    *,
    voter: str,
    candidate: str,
    current_validators: Iterable[str],
    desired_validators: Iterable[str],
    chain_id: int,
    genesis_sha256: str,
    request_sha256: str,
) -> str:
    current = [item.lower() for item in current_validators]
    desired = [item.lower() for item in desired_validators]
    request = {"jsonrpc": "2.0", "id": 1, "method": "qbft_proposeValidatorVote", "params": [candidate.lower(), True]}
    request_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return "\n".join([
        "import hashlib, json, os, time, urllib.request",
        f"RPC = 'http://{voter}:8545'",
        f"VOTER_NODE = {voter!r}",
        f"EXPECTED_CHAIN_ID = {int(chain_id)}",
        f"EXPECTED_GENESIS_SHA256 = {genesis_sha256!r}",
        f"EXPECTED_CURRENT = {current!r}",
        f"EXPECTED_DESIRED = {desired!r}",
        f"CANDIDATE_VALIDATOR = {candidate.lower()!r}",
        f"REQUEST = json.loads({request_json!r})",
        f"EXPECTED_REQUEST_SHA256 = {request_sha256!r}",
        "PROOF = '/proof/' + VOTER_NODE + '-c2-validator-admission.json'",
        "HEALTHY = '/proof/' + VOTER_NODE + '-c2-validator-admission-healthy'",
        "MAX_BLOCK_AGE_SECONDS = 90",
        "def encoded(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()",
        "def rpc(method, params):",
        "    body = encoded({'jsonrpc':'2.0','id':1,'method':method,'params':params})",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    with urllib.request.urlopen(req, timeout=5) as response:",
        "        value = json.loads(response.read(1048576).decode())",
        "    if value.get('error') is not None or 'result' not in value: raise RuntimeError(method + ' failed')",
        "    return value['result']",
        "def validators(): return [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])]",
        "def same_set(left, right): return sorted(left) == sorted(right)",
        "def prove():",
        "    if hashlib.sha256(encoded(REQUEST)).hexdigest() != EXPECTED_REQUEST_SHA256: raise RuntimeError('vote request commitment mismatch')",
        "    chain_id = int(rpc('eth_chainId', []), 16)",
        "    if chain_id != EXPECTED_CHAIN_ID: raise RuntimeError('chain id mismatch')",
        "    genesis = rpc('eth_getBlockByNumber', ['0x0', False])",
        "    if not isinstance(genesis, dict) or not genesis.get('hash'): raise RuntimeError('genesis block missing')",
        "    with open('/config/genesis.json', 'rb') as handle:",
        "        if hashlib.sha256(handle.read()).hexdigest() != EXPECTED_GENESIS_SHA256: raise RuntimeError('genesis commitment mismatch')",
        "    current = validators()",
        "    vote_submitted = False",
        "    if not same_set(current, EXPECTED_DESIRED):",
        "        if not same_set(current, EXPECTED_CURRENT): raise RuntimeError('unexpected pre-vote validator set')",
        "        if rpc(REQUEST['method'], REQUEST['params']) is not True: raise RuntimeError('validator vote rejected')",
        "        vote_submitted = True",
        "    deadline = time.time() + 120",
        "    while time.time() < deadline:",
        "        final = validators()",
        "        if same_set(final, EXPECTED_DESIRED): break",
        "        if not same_set(final, EXPECTED_CURRENT): raise RuntimeError('unexpected validator transition')",
        "        time.sleep(2)",
        "    if not same_set(final, EXPECTED_DESIRED): raise RuntimeError('desired validator set not reached')",
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    time.sleep(4)",
        "    second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first: raise RuntimeError('block height did not advance')",
        "    latest = rpc('eth_getBlockByNumber', ['latest', False])",
        "    if not isinstance(latest, dict) or not latest.get('hash'): raise RuntimeError('latest block missing')",
        "    block_time = int(latest.get('timestamp', '0x0'), 16)",
        "    now = int(time.time())",
        "    if block_time > now + 15 or now - block_time > MAX_BLOCK_AGE_SECONDS: raise RuntimeError('latest block is stale')",
        "    proof = {'voter_node':VOTER_NODE,'chain_id':chain_id,'genesis_sha256':EXPECTED_GENESIS_SHA256,'rpc_request_sha256':EXPECTED_REQUEST_SHA256,'vote_submitted':vote_submitted,'expected_current_validator_set':EXPECTED_CURRENT,'desired_validator_set':EXPECTED_DESIRED,'final_validator_set':final,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'latest_block_hash':latest['hash'],'latest_block_timestamp':block_time,'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "    tmp = PROOF + '.tmp'",
        "    with open(tmp, 'w', encoding='utf-8') as handle: json.dump(proof, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(tmp, PROOF)",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle: handle.write(str(int(time.time())))",
        "while True:",
        "    try:",
        "        prove()",
        "    except Exception:",
        "        try: os.unlink(HEALTHY)",
        "        except FileNotFoundError: pass",
        "    time.sleep(6)",
        "",
    ])




def _verify_c2_sync_precondition(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release: Mapping[str, Any],
    inspected: Mapping[str, Any],
    *,
    max_age_seconds: int,
    now: datetime | None,
) -> dict[str, Any]:
    sync_ref = release.get("sync_evidence")
    if not isinstance(sync_ref, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "C2 sync evidence binding is missing")
    sync_path = _resolve_locator(paths, sync_ref.get("locator"), label="C2 replica sync evidence")
    expected_sync_sha = _sha256(sync_ref.get("sha256"), "C2 replica sync evidence SHA-256")
    verified = verify_c2_replica_sync_evidence(
        paths,
        private_state,
        sync_path,
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if verified.get("evidence_sha256") != expected_sync_sha:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "C2 sync evidence digest mismatch")
    candidate = release.get("candidate")
    if not isinstance(candidate, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "C2 candidate binding is missing")
    current_set = [_address(item, "current validator") for item in inspected["current_validator_set"]]
    verified_set = [_address(item, "expected validator") for item in verified.get("expected_validator_set", [])]
    required = [
        verified.get("network") == inspected["network"],
        verified.get("node") == _C2_NODE,
        verified.get("replica_node") == _C2_NODE,
        _identifier(verified.get("service_uuid"), "C2 sync evidence service UUID") == _identifier(candidate.get("service_uuid"), "candidate service UUID"),
        int(verified.get("chain_id")) == int(inspected["chain_id"]),
        verified.get("genesis_sha256") == inspected["genesis_sha256"],
        sorted(verified_set) == sorted(current_set),
        verified.get("replica_synchronized") is True,
        verified.get("service_running_healthy") is True,
        verified.get("initial_chain_reverified") is True,
        verified.get("guardian_internal_only") is True,
        verified.get("public_endpoint_created") is False,
        verified.get("validator_vote_authorized") is False,
        verified.get("validator_activation_authorized") is False,
        verified.get("routing_or_topology_publication_authorized") is False,
        verified.get("next_phase") == "stage-c2-validator-admission",
    ]
    if not all(required):
        raise _fail(
            "MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID",
            "C2 sync evidence no longer proves admission readiness",
        )
    return {
        "name": "mainnetc-super2-sync-evidence-before-c2-admission",
        "controller_id": _C2_CONTROLLER,
        "method": "VERIFY",
        "endpoint": _relative(paths, sync_path, label="C2 replica sync evidence"),
        "status": "verified",
        "response_sha256": verified["evidence_sha256"],
        "service_uuid": verified["service_uuid"],
        "service_status": "verified-by-c2-replica-sync-evidence",
        "component_or_service_healthy": True,
        "verified": True,
        "proof_source": "c2-replica-sync-evidence",
        "replica_synchronized": True,
        "service_running_healthy": True,
        "coolify_aggregate_status_required": False,
    }

def _install_voter_guardian(compose_text: str, *, voter: str, script: str) -> tuple[str, str]:
    try:
        document = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_COMPOSE_INVALID", "service Compose cannot be parsed") from exc
    if not isinstance(document, dict):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_COMPOSE_INVALID", "service Compose root is invalid")
    services = document.setdefault("services", {})
    if not isinstance(services, dict) or voter not in services:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_COMPOSE_INVALID", f"{voter} service is missing from Compose")
    name = _guardian_service_name(voter)
    indented = script
    services[name] = {
        "image": "python:3.12-alpine",
        "restart": "unless-stopped",
        "read_only": True,
        "depends_on": {voter: {"condition": "service_started"}},
        "command": ["python", "-u", "-c", indented],
        "healthcheck": {
            "test": [
                "CMD", "python", "-c",
                f"import os,time; p='/proof/{voter}-c2-validator-admission-healthy'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
            ],
            "interval": "10s",
            "timeout": "5s",
            "retries": 24,
            "start_period": "30s",
        },
        "volumes": ["mother-config:/config:ro", "mother-c2-validator-admission-proof:/proof"],
        "labels": {
            "main_computer.mother.stage": "c2-validator-admission",
            "main_computer.mother.voter-node": voter,
            "main_computer.mother.routing-publication": "blocked",
        },
    }
    volumes = document.setdefault("volumes", {})
    if not isinstance(volumes, dict):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_COMPOSE_INVALID", "Compose volumes section is invalid")
    volumes.setdefault("mother-c2-validator-admission-proof", None)
    updated = yaml.safe_dump(document, sort_keys=False)
    section = updated.split(f"  {name}:", 1)[1].split("\nvolumes:", 1)[0]
    if any(marker in section for marker in ("ports:", "expose:", "traefik.", "fqdn:", "domains:")):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_GUARDIAN_EXPOSED", "C2 validator-admission guardian must remain internal-only")
    if "8545:8545" in updated:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RPC_EXPOSED", "C2 validator admission must not publish JSON-RPC")
    return updated, name


def _write_c2_admission_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(evidence)
    if document.get("kind") != _EVIDENCE_KIND or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "C2 validator-admission evidence is malformed or sensitive")
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    current = _ensure_directory(paths, _EVIDENCE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "c2admissionevidence"
    destination = current / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_EVIDENCE_CONFLICT", "evidence destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_c2_validator_admission_release(
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
    max_wait_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_c2_validator_admission_release(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes or (_C2_NODE,),
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        now=now,
    )
    if inspected["release_already_claimed"]:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_ALREADY_CONSUMED", "this C2 validator-admission release is already claimed")
    release, _, _ = _canonical_under(paths, Path(inspected["release_path"]), _RELEASE_DIRECTORY, "C2 validator-admission release")
    digest = inspected["c2_validator_admission_release_sha256"]
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="C2 validator-admission release"), "sha256": digest},
        "node": _C2_NODE,
        "candidate_validator_address": inspected["candidate_validator_address"],
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_root = _ensure_directory(paths, _CLAIM_DIRECTORY, operation=operation)
    claim_path = claim_root / f"{digest}.json"
    if claim_path.exists():
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_RELEASE_ALREADY_CONSUMED", "this C2 validator-admission release is already claimed")
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)

    controller_a = resolve_coolify_controller(private_state, inspected["network"], "coolify-a")
    controller_c = resolve_coolify_controller(private_state, inspected["network"], "coolify-c")
    controllers = {"coolify-a": controller_a, "coolify-c": controller_c}
    started = _timestamp()
    preconditions: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None

    try:
        inventories: dict[str, dict[str, Any]] = {}
        service_records: dict[str, Mapping[str, Any]] = {}
        service_uuids: dict[str, str] = {}
        preconditions.append(_verify_c2_sync_precondition(
            paths,
            private_state,
            release,
            inspected,
            max_age_seconds=transaction_max_age_seconds,
            now=now,
        ))
        for controller_id, node in (("coolify-a", _A1_NODE), ("coolify-c", _C1_NODE)):
            inventory = _http(controllers[controller_id], "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            inventories[f"{controller_id}:{node}"] = inventory
            record = _find_service_record(inventory["payload"], node=node)
            uuid = _identifier(record.get("uuid"), f"{node} service UUID")
            healthy = _component_healthy(record, node=node)
            service_records[node] = record
            service_uuids[node] = uuid
            preconditions.append({
                "name": f"{node}-service-before-c2-admission",
                "controller_id": controller_id,
                "method": "GET",
                "endpoint": "/api/v1/services",
                "status": inventory["status"],
                "response_sha256": inventory["response_sha256"],
                "service_uuid": uuid,
                "service_status": _service_status(record),
                "component_or_service_healthy": healthy,
                "verified": healthy,
                "proof_source": "coolify-voter-service-health",
            })
            if not healthy:
                raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_PRECONDITION_UNHEALTHY", f"{node} is not healthy enough for C2 admission")

        plan = release["admission_plan"]
        current_set = [_address(item, "current validator") for item in plan["current_validator_set"]]
        desired_set = [_address(item, "desired validator") for item in plan["desired_validator_set"]]
        candidate = _address(plan["candidate_validator_address"], "candidate validator address")
        request_by_voter = {item["voter_node"]: item for item in plan["rpc_requests"] if isinstance(item, Mapping)}
        for voter in _VOTER_NODES:
            controller_id = _controller_for_voter(voter)
            controller = controllers[controller_id]
            uuid = service_uuids[voter]
            endpoint = f"/api/v1/services/{urllib.parse.quote(uuid, safe='')}"
            detail = _http(controller, "GET", endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            if not detail["ok"]:
                raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_PRECONDITION_FAILED", f"{voter} service detail failed with HTTP {detail['status']}")
            detail_record = _find_service_record(detail["payload"], node=voter, service_uuid=uuid)
            original_compose = _compose_text(detail_record)
            vote_request = request_by_voter.get(voter)
            if not isinstance(vote_request, Mapping):
                raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_INVALID", f"{voter} vote request is missing")
            script = _voter_script(
                voter=voter,
                candidate=candidate,
                current_validators=current_set,
                desired_validators=desired_set,
                chain_id=int(release["admission_plan"].get("chain_id", release.get("chain_id", 0)) or release.get("current_chain", {}).get("chain_id", 0) or inspected["chain_id"]),
                genesis_sha256=inspected["genesis_sha256"],
                request_sha256=_sha256(vote_request.get("rpc_request_sha256"), f"{voter} vote request SHA-256"),
            )
            updated_compose, guardian_name = _install_voter_guardian(original_compose, voter=voter, script=script)
            encoded = base64.b64encode(updated_compose.encode("utf-8")).decode("ascii")
            body = {"docker_compose_raw": encoded, "instant_deploy": False, "name": voter}
            body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
            patch = _http(controller, "PATCH", endpoint, body=body, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            patch_ok = patch["status"] in {200, 201, 202}
            receipts.append({
                "ordinal": len(receipts) + 1,
                "mutation_id": f"{voter}.install-c2-validator-admission-guardian",
                "controller_id": controller_id,
                "node": voter,
                "service_uuid": uuid,
                "method": "PATCH",
                "endpoint": endpoint,
                "body_sha256": body_sha,
                "guardian_service": guardian_name,
                "response": _safe_response(patch),
                "live_write_acknowledged": patch_ok,
                "status": "succeeded" if patch_ok else "failed",
            })
            if not patch_ok:
                raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_MUTATION_FAILED", f"Coolify rejected {voter} guardian patch with HTTP {patch['status']}")
            deploy_endpoint = f"/api/v1/deploy?uuid={urllib.parse.quote(uuid, safe='')}&force=true"
            deploy = _http(controller, "GET", deploy_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            deploy_ok = deploy["status"] in {200, 201, 202}
            receipts.append({
                "ordinal": len(receipts) + 1,
                "mutation_id": f"{voter}.deploy-c2-validator-admission-guardian",
                "controller_id": controller_id,
                "node": voter,
                "service_uuid": uuid,
                "method": "GET",
                "endpoint": deploy_endpoint,
                "body_sha256": None,
                "guardian_service": guardian_name,
                "response": _safe_response(deploy),
                "live_write_acknowledged": deploy_ok,
                "status": "succeeded" if deploy_ok else "failed",
            })
            if not deploy_ok:
                raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_MUTATION_FAILED", f"Coolify rejected {voter} guardian deploy with HTTP {deploy['status']}")

        deadline = time.monotonic() + max_wait_seconds
        healthy_voters: set[str] = set()
        last_statuses: dict[str, str] = {}
        while True:
            healthy_voters.clear()
            for controller_id, voter in (("coolify-a", _A1_NODE), ("coolify-c", _C1_NODE)):
                inventory = _http(controllers[controller_id], "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
                if inventory["ok"]:
                    record = _find_service_record(inventory["payload"], node=voter, service_uuid=service_uuids[voter])
                    status = _service_status(record)
                    last_statuses[voter] = status
                    healthy = _component_healthy(record, node=voter) or status == "running:healthy"
                    if healthy:
                        healthy_voters.add(voter)
                    observations.append({
                        "node": voter,
                        "controller_id": controller_id,
                        "status": status,
                        "component_or_service_healthy": healthy,
                        "response_sha256": inventory["response_sha256"],
                        "observed_at": _timestamp(),
                    })
            if set(_VOTER_NODES) <= healthy_voters:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0.0, poll_interval_seconds))
        if not (set(_VOTER_NODES) <= healthy_voters):
            raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_NOT_HEALTHY", f"C2 admission voter guardians did not become healthy: {last_statuses!r}")
    except MotherDeploymentC2ValidatorAdmissionError as exc:
        failure = {"code": exc.code, "message": str(exc)[:512]}
    except Exception as exc:  # pragma: no cover
        failure = {"code": "MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_UNEXPECTED_FAILURE", "message": str(exc)[:512]}

    completed = _timestamp()
    succeeded = sum(item.get("status") == "succeeded" for item in receipts)
    complete = failure is None and succeeded == 4 and set(_VOTER_NODES) <= {
        item.get("node") for item in observations if item.get("component_or_service_healthy") is True
    }
    live_mutation = any(item.get("live_write_acknowledged") is True for item in receipts)
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": "pass" if complete else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "node": _C2_NODE,
        "nodes": [_A1_NODE, _C1_NODE, _C2_NODE],
        "candidate_node": _C2_NODE,
        "candidate_validator_address": inspected["candidate_validator_address"],
        "voter_nodes": list(_VOTER_NODES),
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="C2 validator-admission release"), "sha256": digest},
        "execution_claim": {"locator": _relative(paths, claim_path, label="C2 validator-admission claim")},
        "chain_id": inspected["chain_id"],
        "genesis_sha256": inspected["genesis_sha256"],
        "current_validator_set": list(inspected["current_validator_set"]),
        "desired_validator_set": list(inspected["desired_validator_set"]),
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "health_observations": observations,
        "failure": failure,
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
            "automatic_rollback_performed": False,
        },
        "authority": {
            "release_consumed": True,
            "validator_vote_authorized": True,
            "validator_activation_authorized": True,
            "validator_vote_proven": complete,
            "validator_activation_proven": complete,
            "routing_or_topology_publication_authorized": False,
        },
        "summary": {
            "clean": complete,
            "complete": complete,
            "current_validator_set_reverified": complete,
            "final_validator_set_verified": complete,
            "desired_validator_count": len(inspected["desired_validator_set"]),
            "current_validator_count": len(inspected["current_validator_set"]),
            "logical_vote_count": inspected["logical_vote_count"],
            "two_existing_validator_votes_required": True,
            "planned_mutation_count": 4,
            "attempted_mutation_count": len(receipts),
            "succeeded_mutation_count": succeeded,
            "failed_mutation_count": sum(item.get("status") != "succeeded" for item in receipts),
            "network_access_performed": bool(preconditions or receipts or observations),
            "live_mutation_performed": live_mutation,
            "validator_vote_performed": complete,
            "validator_activation_performed": complete,
            "routing_or_topology_publication_authorized": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "manual_ssh_required": False,
            "c2_sync_evidence_reverified": any(item.get("proof_source") == "c2-replica-sync-evidence" and item.get("verified") is True for item in preconditions),
            "c2_readiness_source": "c2-replica-sync-evidence",
            "blocks_advancing": complete,
            "latest_block_fresh": complete,
            "next_phase": "stage-t3-post-admission-steady-state" if complete else "manual-review-required",
        },
        "next_phase": "stage-t3-post-admission-steady-state" if complete else "manual-review-required",
        "validator_mutation_count": 1 if complete else 0,
        "validator_vote_performed": complete,
        "validator_restart_count": None,
        "chain_mutation_count": 0,
        "service_mutation_count": succeeded,
    }
    evidence_path, evidence_sha = _write_c2_admission_evidence(paths, evidence, operation=operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence


def verify_c2_validator_admission_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    document, _, digest = _canonical_under(paths, Path(evidence_path), _EVIDENCE_DIRECTORY, "C2 validator-admission evidence")
    if document.get("kind") != _EVIDENCE_KIND or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "C2 validator-admission evidence is invalid or stale")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (_C2_NODE,):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_SELECTION_MISMATCH", "C2 validator-admission evidence targets only mainnetc-super2")
    age = _age(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_EVIDENCE_STALE", "C2 validator-admission evidence is outside the freshness window")
    summary = document.get("summary")
    authority = document.get("authority")
    policy = document.get("policy")
    if not isinstance(summary, Mapping) or not isinstance(authority, Mapping) or not isinstance(policy, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "C2 validator-admission evidence is incomplete")
    if not all([
        document.get("status") == "pass",
        summary.get("clean") is True,
        summary.get("validator_vote_performed") is True,
        summary.get("validator_activation_performed") is True,
        summary.get("final_validator_set_verified") is True,
        summary.get("blocks_advancing") is True,
        summary.get("latest_block_fresh") is True,
        summary.get("public_endpoint_created") is False,
        summary.get("routing_or_topology_published") is False,
        authority.get("validator_vote_proven") is True,
        authority.get("validator_activation_proven") is True,
        policy.get("routing_or_topology_published") is False,
        summary.get("next_phase") == "stage-t3-post-admission-steady-state",
    ]):
        raise _fail("MOTHER_DEPLOY_C2_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "C2 validator-admission evidence does not prove admission")
    return {
        "clean": True,
        "evidence_path": str(Path(evidence_path).resolve(strict=False)),
        "evidence_sha256": digest,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "nodes": [_C2_NODE],
        "candidate_node": _C2_NODE,
        "candidate_validator_address": document["candidate_validator_address"],
        "current_validator_set": list(document["current_validator_set"]),
        "final_validator_set": list(document["desired_validator_set"]),
        "desired_validator_set": list(document["desired_validator_set"]),
        "validator_vote_proven": True,
        "validator_activation_proven": True,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": "stage-t3-post-admission-steady-state",
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
    "execute_c2_validator_admission_release",
    "verify_c2_validator_admission_evidence",
]
