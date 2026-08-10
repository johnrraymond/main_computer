"""Fail-closed post-T2 reservation of canonical C2 in Mother private state.

This lifecycle is intentionally local-only.  Staging binds the successful
validator-RPC canary proof to fresh C2 validator/Hub identities while persisting
their private keys only beneath Mother ``secrets``.  A one-use expiring release
then authorizes exactly one private-state generation advance.  No Coolify,
validator, QBFT, or network mutation is performed here.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any, Callable, Iterable, Mapping

import yaml

from . import atomic_files
from .canonical import canonical_json
from .deployment_plan import build_starter_deployment_plan
from .deployment_validator_rpc_canary_execution import verify_validator_rpc_canary_evidence
from .ethereum_identity import generate_private_key, is_private_key, private_key_to_address
from .models import OperationIdentity, PrivateStatePaths
from .private_state import (
    PrivateStateReadResult,
    _secure_private_path,
    prepare_private_state_successor,
    read_private_state,
    replace_verified_private_state,
)


_C2_NODE = "mainnetc-super2"
_C2_CONTROLLER = "coolify-c"
_A1_NODE = "mainneta-super1"
_C1_NODE = "mainnetc-super1"
_TRANSACTION_KIND = "main_computer.mother.deployment_c2_state_extension_transaction.v1"
_RELEASE_KIND = "main_computer.mother.deployment_c2_state_extension_release.v1"
_SECRET_KIND = "main_computer.mother.deployment_c2_state_extension_secret.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_c2_state_extension_evidence.v1"
_TRANSACTION_DIRECTORY = ("actions", "deployment-c2-state-extension-transactions")
_RELEASE_DIRECTORY = ("actions", "deployment-c2-state-extension-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-c2-state-extension-claims")
_SECRET_DIRECTORY = ("secrets", "deployment-c2-state-extension")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-c2-state-extension")


class MotherDeploymentC2StateExtensionError(RuntimeError):
    """C2 reservation/state-extension lifecycle failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True, repr=False)
class C2StateExtensionStaging:
    transaction: dict[str, Any]
    secret_payload: bytes

    def __repr__(self) -> str:
        return (
            "C2StateExtensionStaging("
            f"transaction_sha256={self.transaction.get('transaction_sha256')!r}, "
            f"secret_payload_bytes={len(self.secret_payload)}, redacted=True)"
        )


def _fail(code: str, message: str) -> MotherDeploymentC2StateExtensionError:
    return MotherDeploymentC2StateExtensionError(code, message)


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", f"{path} must be a non-empty string")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if value in {".", ".."} or any(ch not in allowed for ch in value):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", f"{path} is not a safe identifier")
    return value


def _utc(value: str | None, path: str) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if type(value) is not str or not value:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", f"{path} must be a UTC timestamp")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", f"{path} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", f"{path} must be UTC")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: Any, path: str) -> datetime:
    return datetime.fromisoformat(_utc(value, path).replace("Z", "+00:00"))


def _age_seconds(created_at: str, *, now: datetime | None) -> int:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    created = _parse_utc(created_at, "created_at")
    age = int((current.astimezone(timezone.utc) - created).total_seconds())
    if age < 0:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "artifact timestamp is in the future")
    return age


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", f"{path} must be a mapping")
    return value


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _binding_from_closure(closure: Any) -> dict[str, Any]:
    return {
        "generation": closure.binding.generation,
        "content_sha256": closure.binding.content_hash.digest,
        "manifest_sha256": closure.binding.recovery_manifest_hash.digest,
    }


def _document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        value = yaml.safe_load(private_state.document_bytes)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "committed Mother private state is malformed") from exc
    if type(value) is not dict:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "committed Mother private state must be a mapping")
    return value


def _canonical_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", f"{label} could not be read as canonical JSON") from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", f"{label} is not canonical JSON")
    return value, raw, hashlib.sha256(raw).hexdigest()


def _relative_locator(paths: PrivateStatePaths, candidate: Path, *, label: str) -> str:
    try:
        return Path(candidate).resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_PATH_UNSAFE", f"{label} must be beneath Mother state") from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_PATH_UNSAFE", f"{label} locator must be a relative POSIX path")
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_PATH_UNSAFE", f"{label} locator is unsafe")
    resolved = (paths.root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_PATH_UNSAFE", f"{label} locator escapes Mother state") from exc
    return resolved


def _artifact_digest(document: Mapping[str, Any], field: str) -> str:
    payload = dict(document)
    payload.pop(field, None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _safe_private_key(value: Any, path: str) -> str:
    if not is_private_key(value):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_SECRET_INVALID", f"{path} is not a valid private key")
    return "0x" + str(value)[2:].lower()


def _validate_current_t2(document: dict[str, Any], *, network: str) -> dict[str, Any]:
    if network != "mainnet":
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_TARGET_INVALID", "C2 extension is defined only for mainnet")
    if document.get("kind") != "main_computer.mother.private_state.v1" or document.get("schema_version") != 1:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "private state is not schema version 1")
    network_state = _mapping(_mapping(document.get("networks"), "networks").get(network), f"networks.{network}")
    deployment = _mapping(network_state.get("deployment"), f"networks.{network}.deployment")
    targets = _mapping(deployment.get("targets"), f"networks.{network}.deployment.targets")
    validators = _mapping(network_state.get("validators"), f"networks.{network}.validators")
    nodes = _mapping(network_state.get("nodes"), f"networks.{network}.nodes")
    seeds = _mapping(network_state.get("node_seed_material"), f"networks.{network}.node_seed_material")
    controllers = _mapping(
        _mapping(network_state.get("coolify"), f"networks.{network}.coolify").get("controllers"),
        f"networks.{network}.coolify.controllers",
    )

    for existing in (_A1_NODE, _C1_NODE):
        if existing not in targets or existing not in validators or existing not in nodes or existing not in seeds:
            raise _fail(
                "MOTHER_DEPLOY_C2_STATE_EXTENSION_T2_REQUIRED",
                f"canonical T2 reservation {existing!r} is incomplete",
            )
    for mapping, label in (
        (targets, "deployment target"),
        (validators, "validator"),
        (nodes, "node"),
        (seeds, "node seed material"),
    ):
        if _C2_NODE in mapping:
            raise _fail(
                "MOTHER_DEPLOY_C2_STATE_EXTENSION_ALREADY_RESERVED",
                f"{label} for {_C2_NODE!r} already exists",
            )
    controller = _mapping(controllers.get(_C2_CONTROLLER), f"networks.{network}.coolify.controllers.{_C2_CONTROLLER}")
    if controller.get("enabled") is not True:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_TARGET_INVALID", "coolify-c is not enabled")
    return network_state


def _without_c2(document: dict[str, Any], *, network: str) -> dict[str, Any]:
    result = deepcopy(document)
    network_state = _mapping(_mapping(result["networks"], "networks")[network], f"networks.{network}")
    for container_path in (
        ("deployment", "targets"),
        ("validators",),
        ("nodes",),
        ("node_seed_material",),
    ):
        current: Any = network_state
        for part in container_path:
            current = _mapping(current.get(part), ".".join(container_path))
        current.pop(_C2_NODE, None)
    return result


def _extend_document(
    document: dict[str, Any],
    *,
    network: str,
    generated_at: str,
    validator_private_key: str,
    hub_private_key: str,
) -> dict[str, Any]:
    _validate_current_t2(document, network=network)
    result = deepcopy(document)
    network_state = _mapping(_mapping(result["networks"], "networks")[network], f"networks.{network}")
    deployment = _mapping(network_state["deployment"], f"networks.{network}.deployment")
    targets = _mapping(deployment["targets"], f"networks.{network}.deployment.targets")
    validators = _mapping(network_state["validators"], f"networks.{network}.validators")
    nodes = _mapping(network_state["nodes"], f"networks.{network}.nodes")
    seeds = _mapping(network_state["node_seed_material"], f"networks.{network}.node_seed_material")

    validator_private_key = _safe_private_key(validator_private_key, "validator_private_key")
    hub_private_key = _safe_private_key(hub_private_key, "hub_admin_private_key")
    validator_address = private_key_to_address(validator_private_key)
    hub_address = private_key_to_address(hub_private_key)
    hub_path = f"networks.{network}.node_seed_material.{_C2_NODE}.wallets.hub_admin.private_key"

    validators[_C2_NODE] = {
        "address": validator_address,
        "private_key": validator_private_key,
    }
    nodes[_C2_NODE] = {
        "guard_route_reservation": f"{_C2_NODE}.guard",
        "host": _C2_CONTROLLER,
        "hub_route_reservation": f"{_C2_NODE}.hub",
        "rpc_route_reservation": f"{_C2_NODE}.rpc",
        "validator_ref": f"networks.{network}.validators.{_C2_NODE}",
    }
    seeds[_C2_NODE] = {
        "wallets": {
            "hub_admin": {
                "address": hub_address,
                "metadata": {
                    "address_derivation": "secp256k1-keccak256-eip55",
                    "generated_at": generated_at,
                    "generated_by": "tools/mother_deploy.py:stage-c2-state-extension",
                    "reason": "Mother post-T2 mainnetc-super2 Hub administrator identity reservation",
                },
                "private_key": hub_private_key,
            }
        }
    }
    targets[_C2_NODE] = {
        "controller_ref": f"networks.{network}.coolify.controllers.{_C2_CONTROLLER}",
        "desired_environment_name": "mainnet",
        "desired_service_name": _C2_NODE,
        "hub_admin_address": hub_address,
        "hub_admin_private_key_path": hub_path,
        "key_material_status": "present",
        "live_resource_uuid": None,
        "status": "absent-awaiting-redeployment",
    }

    if canonical_json(_without_c2(result, network=network)) != canonical_json(document):
        raise _fail(
            "MOTHER_DEPLOY_C2_STATE_EXTENSION_PRESERVATION_FAILED",
            "C2 extension changed existing Mother state outside the new C2 reservation",
        )
    return result


def _verify_canary_gate(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int,
    operation: OperationIdentity,
    now: datetime | None,
) -> dict[str, Any]:
    verified = verify_validator_rpc_canary_evidence(
        paths,
        private_state,
        Path(evidence_path),
        selected_nodes=(_C1_NODE, _A1_NODE),
        max_age_seconds=max_age_seconds,
        release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        funding_evidence_max_age_seconds=86400,
        funding_transaction_max_age_seconds=86400,
        soak_max_age_seconds=86400,
        operation=operation,
    )
    if (
        verified.get("clean") is not True
        or verified.get("chain_state") != "exact-cross-validator-verified"
        or verified.get("next_phase") != "validator-rpc-canary-execution-complete"
        or verified.get("validator_mutation_count") != 0
        or verified.get("validator_restart_count") != 0
        or verified.get("validator_vote_performed") is not False
    ):
        raise _fail(
            "MOTHER_DEPLOY_C2_STATE_EXTENSION_CANARY_REQUIRED",
            "successful exact cross-validator T2 canary evidence is required",
        )
    return verified


def stage_c2_state_extension(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    canary_evidence_path: Path,
    *,
    network: str = "mainnet",
    canary_max_age_seconds: int = 86400,
    created_at: str | None = None,
    now: datetime | None = None,
    operation: OperationIdentity,
    key_factory: Callable[[], str] = generate_private_key,
) -> C2StateExtensionStaging:
    if not isinstance(paths, PrivateStatePaths) or not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("canonical private-state paths and read result are required")
    network = _identifier(network, "network")
    created = _utc(created_at, "created_at")
    current_document = _document(private_state)
    _validate_current_t2(current_document, network=network)

    canary = _verify_canary_gate(
        paths,
        private_state,
        Path(canary_evidence_path),
        max_age_seconds=canary_max_age_seconds,
        operation=operation,
        now=now,
    )
    evidence_path = Path(canary["evidence_path"])
    _, evidence_raw, evidence_file_sha = _canonical_file(evidence_path, label="validator-RPC canary evidence")
    del evidence_raw

    validator_key = _safe_private_key(key_factory(), "generated validator private key")
    hub_key = _safe_private_key(key_factory(), "generated Hub private key")
    if validator_key == hub_key:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_SECRET_INVALID", "C2 validator and Hub identities must be distinct")
    validator_address = private_key_to_address(validator_key)
    hub_address = private_key_to_address(hub_key)

    successor_document = _extend_document(
        current_document,
        network=network,
        generated_at=created,
        validator_private_key=validator_key,
        hub_private_key=hub_key,
    )
    closure = prepare_private_state_successor(
        private_state,
        successor_document,
        updated_at=created,
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    predecessor_object_sha = hashlib.sha256(private_state.canonical_object_bytes).hexdigest()

    stamp = re.sub(r"[^0-9A-Za-z]+", "", created)[:24] or "c2"
    secret_locator = (
        f"{_SECRET_DIRECTORY[0]}/{_SECRET_DIRECTORY[1]}/"
        f"{stamp}-{_C2_NODE}-{validator_address[2:18]}.json"
    )
    secret = {
        "kind": _SECRET_KIND,
        "schema_version": 1,
        "network": network,
        "node": _C2_NODE,
        "generated_at": created,
        "predecessor_binding": _binding(private_state),
        "canary_evidence_sha256": canary["evidence_sha256"],
        "validator": {"address": validator_address, "private_key": validator_key},
        "hub_admin": {"address": hub_address, "private_key": hub_key},
    }
    secret_payload = canonical_json(secret)
    secret_sha = hashlib.sha256(secret_payload).hexdigest()

    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": created,
        "network": network,
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "target": {
            "node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "controller_ref": f"networks.{network}.coolify.controllers.{_C2_CONTROLLER}",
            "desired_environment_name": "mainnet",
            "desired_service_name": _C2_NODE,
            "mode": "soft",
        },
        "predecessor_binding": _binding(private_state),
        "predecessor_canonical_object_sha256": predecessor_object_sha,
        "successor_binding": _binding_from_closure(closure),
        "canary_evidence": {
            "locator": _relative_locator(paths, evidence_path, label="canary evidence"),
            "sha256": canary["evidence_sha256"],
            "file_sha256": evidence_file_sha,
            "chain_state": canary["chain_state"],
        },
        "reservation": {
            "validator_address": validator_address,
            "hub_admin_address": hub_address,
            "secret_payload_locator": secret_locator,
            "secret_payload_sha256": secret_sha,
            "private_key_material_in_transaction": False,
        },
        "authority": {
            "offline_compilation_only": True,
            "private_state_update_authorized": False,
            "live_execution_authorized": False,
            "network_access_authorized": False,
            "service_mutation_authorized": False,
            "validator_mutation_authorized": False,
            "validator_restart_authorized": False,
            "validator_vote_authorized": False,
            "release_required": True,
        },
        "policy": {
            "private_state_generation_increment": 1,
            "predecessor_recovery_required": True,
            "preserve_existing_state_outside_c2": True,
            "coolify_mutation_count": 0,
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_count": 0,
        },
        "summary": {
            "clean": True,
            "c2_reserved": True,
            "private_state_updated": False,
            "secret_payload_persisted": False,
            "next_phase": "verify-c2-state-extension-transaction",
        },
    }
    transaction["transaction_sha256"] = _artifact_digest(transaction, "transaction_sha256")
    return C2StateExtensionStaging(transaction=transaction, secret_payload=secret_payload)


def _ensure_private_directory(path: Path, *, operation: OperationIdentity) -> None:
    atomic_files.ensure_durable_directory(path, operation=operation)
    _secure_private_path(path, is_directory=True, operation=operation)


def _write_canonical_artifact(
    root: Path,
    document: Mapping[str, Any],
    *,
    digest_field: str,
    stamp_field: str,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    digest = _artifact_digest(document, digest_field)
    if document.get(digest_field) != digest:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", f"{digest_field} does not match artifact")
    payload = canonical_json(dict(document))
    _ensure_private_directory(root, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get(stamp_field, "")))[:32] or "artifact"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_CONFLICT", "artifact destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def write_c2_state_extension_transaction(
    paths: PrivateStatePaths,
    staging: C2StateExtensionStaging,
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if not isinstance(staging, C2StateExtensionStaging):
        raise TypeError("staging must be C2StateExtensionStaging")
    transaction = staging.transaction
    secret_path = _resolve_locator(paths, transaction["reservation"]["secret_payload_locator"], label="secret payload")
    _ensure_private_directory(secret_path.parent, operation=operation)
    created_secret = False
    if secret_path.exists():
        if secret_path.read_bytes() != staging.secret_payload:
            raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_CONFLICT", "C2 secret payload locator already contains different bytes")
    else:
        atomic_files.durable_create(secret_path, staging.secret_payload, operation=operation)
        _secure_private_path(secret_path, is_directory=False, operation=operation)
        created_secret = True
    if hashlib.sha256(secret_path.read_bytes()).hexdigest() != transaction["reservation"]["secret_payload_sha256"]:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_SECRET_INVALID", "persisted C2 secret payload digest mismatch")
    try:
        path, digest = _write_canonical_artifact(
            paths.root / _TRANSACTION_DIRECTORY[0] / _TRANSACTION_DIRECTORY[1],
            transaction,
            digest_field="transaction_sha256",
            stamp_field="created_at",
            operation=operation,
        )
    except BaseException:
        if created_secret:
            try:
                secret_path.unlink()
            except OSError:
                pass
        raise
    return path, digest


def _load_secret(paths: PrivateStatePaths, transaction: Mapping[str, Any]) -> dict[str, Any]:
    reservation = _mapping(transaction.get("reservation"), "reservation")
    secret_path = _resolve_locator(paths, reservation.get("secret_payload_locator"), label="secret payload")
    secret, raw, digest = _canonical_file(secret_path, label="C2 secret payload")
    if digest != reservation.get("secret_payload_sha256"):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_SECRET_INVALID", "C2 secret payload digest does not match transaction")
    if (
        secret.get("kind") != _SECRET_KIND
        or secret.get("schema_version") != 1
        or secret.get("network") != transaction.get("network")
        or secret.get("node") != _C2_NODE
        or secret.get("predecessor_binding") != transaction.get("predecessor_binding")
    ):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_SECRET_INVALID", "C2 secret payload binding is invalid")
    validator = _mapping(secret.get("validator"), "secret.validator")
    hub = _mapping(secret.get("hub_admin"), "secret.hub_admin")
    validator_key = _safe_private_key(validator.get("private_key"), "secret.validator.private_key")
    hub_key = _safe_private_key(hub.get("private_key"), "secret.hub_admin.private_key")
    if private_key_to_address(validator_key) != reservation.get("validator_address") or validator.get("address") != reservation.get("validator_address"):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_SECRET_INVALID", "C2 validator private key/address binding is invalid")
    if private_key_to_address(hub_key) != reservation.get("hub_admin_address") or hub.get("address") != reservation.get("hub_admin_address"):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_SECRET_INVALID", "C2 Hub private key/address binding is invalid")
    del raw
    return secret


def verify_c2_state_extension_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    max_age_seconds: int = 86400,
    canary_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    transaction, _, file_sha = _canonical_file(Path(transaction_path), label="C2 state-extension transaction")
    if transaction.get("kind") != _TRANSACTION_KIND or transaction.get("schema_version") != 1:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "C2 state-extension transaction kind is invalid")
    digest = _artifact_digest(transaction, "transaction_sha256")
    if transaction.get("transaction_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "C2 state-extension transaction digest mismatch")
    age = _age_seconds(transaction["created_at"], now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_EXPIRED", "C2 state-extension transaction is too old")
    if transaction.get("network") != operation.network or transaction.get("predecessor_binding") != _binding(private_state):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_PREDECESSOR_CHANGED", "current Mother private state no longer matches transaction predecessor")

    current_doc = _document(private_state)
    _validate_current_t2(current_doc, network=transaction["network"])
    if hashlib.sha256(private_state.canonical_object_bytes).hexdigest() != transaction.get("predecessor_canonical_object_sha256"):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_PREDECESSOR_CHANGED", "predecessor canonical-object commitment changed")

    canary_ref = _mapping(transaction.get("canary_evidence"), "canary_evidence")
    canary_path = _resolve_locator(paths, canary_ref.get("locator"), label="canary evidence")
    verified_canary = _verify_canary_gate(
        paths,
        private_state,
        canary_path,
        max_age_seconds=canary_max_age_seconds,
        operation=operation,
        now=now,
    )
    if verified_canary.get("evidence_sha256") != canary_ref.get("sha256"):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_CANARY_REQUIRED", "canary evidence digest no longer matches transaction")

    secret = _load_secret(paths, transaction)
    successor_doc = _extend_document(
        current_doc,
        network=transaction["network"],
        generated_at=secret["generated_at"],
        validator_private_key=secret["validator"]["private_key"],
        hub_private_key=secret["hub_admin"]["private_key"],
    )
    closure = prepare_private_state_successor(
        private_state,
        successor_doc,
        updated_at=secret["generated_at"],
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    if _binding_from_closure(closure) != transaction.get("successor_binding"):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_SUCCESSOR_MISMATCH", "reconstructed C2 successor does not match transaction commitment")

    return {
        "clean": True,
        "network": transaction["network"],
        "node": _C2_NODE,
        "controller_id": _C2_CONTROLLER,
        "age_seconds": age,
        "transaction_path": str(Path(transaction_path)),
        "transaction_sha256": digest,
        "transaction_file_sha256": file_sha,
        "predecessor_binding": transaction["predecessor_binding"],
        "successor_binding": transaction["successor_binding"],
        "validator_address": transaction["reservation"]["validator_address"],
        "hub_admin_address": transaction["reservation"]["hub_admin_address"],
        "private_key_material_in_transaction": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "next_phase": "c2-state-extension-release-not-yet-authorized",
    }


def build_c2_state_extension_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledge_transaction_sha256: str,
    transaction_max_age_seconds: int = 86400,
    canary_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    if type(expires_in_seconds) is not int or expires_in_seconds <= 0 or expires_in_seconds > 3600:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "expires_in_seconds must be between 1 and 3600")
    verified = verify_c2_state_extension_transaction(
        paths,
        private_state,
        Path(transaction_path),
        max_age_seconds=transaction_max_age_seconds,
        canary_max_age_seconds=canary_max_age_seconds,
        now=now,
        operation=operation,
    )
    if acknowledge_transaction_sha256 != verified["transaction_sha256"]:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_ACK_MISMATCH", "transaction SHA-256 acknowledgement does not match")
    created = _utc(created_at, "created_at")
    expires = (_parse_utc(created, "created_at") + timedelta(seconds=expires_in_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created,
        "expires_at": expires,
        "network": verified["network"],
        "requested_use_limit": 1,
        "transaction": {
            "locator": _relative_locator(paths, Path(transaction_path), label="transaction"),
            "sha256": verified["transaction_sha256"],
        },
        "predecessor_binding": verified["predecessor_binding"],
        "successor_binding": verified["successor_binding"],
        "target": {"node": _C2_NODE, "controller_id": _C2_CONTROLLER},
        "authority": {
            "private_state_update_authorized": True,
            "live_execution_authorized": True,
            "network_access_authorized": False,
            "service_mutation_authorized": False,
            "chain_mutation_authorized": False,
            "validator_mutation_authorized": False,
            "validator_restart_authorized": False,
            "validator_vote_authorized": False,
        },
    }
    release["release_sha256"] = _artifact_digest(release, "release_sha256")
    return release


def write_c2_state_extension_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    return _write_canonical_artifact(
        paths.root / _RELEASE_DIRECTORY[0] / _RELEASE_DIRECTORY[1],
        release,
        digest_field="release_sha256",
        stamp_field="created_at",
        operation=operation,
    )


def verify_c2_state_extension_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    canary_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    release, _, file_sha = _canonical_file(Path(release_path), label="C2 state-extension release")
    if release.get("kind") != _RELEASE_KIND or release.get("schema_version") != 1:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "C2 state-extension release kind is invalid")
    digest = _artifact_digest(release, "release_sha256")
    if release.get("release_sha256") != digest or release.get("requested_use_limit") != 1:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "C2 state-extension release digest/use limit is invalid")
    age = _age_seconds(release["created_at"], now=now)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if age > max_age_seconds or current.astimezone(timezone.utc) >= _parse_utc(release["expires_at"], "expires_at"):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_EXPIRED", "C2 state-extension release is expired")

    tx_ref = _mapping(release.get("transaction"), "release.transaction")
    tx_path = _resolve_locator(paths, tx_ref.get("locator"), label="transaction")
    verified_tx = verify_c2_state_extension_transaction(
        paths,
        private_state,
        tx_path,
        max_age_seconds=transaction_max_age_seconds,
        canary_max_age_seconds=canary_max_age_seconds,
        now=now,
        operation=operation,
    )
    if (
        verified_tx["transaction_sha256"] != tx_ref.get("sha256")
        or release.get("predecessor_binding") != verified_tx["predecessor_binding"]
        or release.get("successor_binding") != verified_tx["successor_binding"]
    ):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "release does not bind the verified transaction")
    authority = _mapping(release.get("authority"), "release.authority")
    if (
        authority.get("private_state_update_authorized") is not True
        or authority.get("live_execution_authorized") is not True
        or authority.get("network_access_authorized") is not False
        or authority.get("service_mutation_authorized") is not False
        or authority.get("chain_mutation_authorized") is not False
        or authority.get("validator_mutation_authorized") is not False
        or authority.get("validator_restart_authorized") is not False
        or authority.get("validator_vote_authorized") is not False
    ):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "release authority is not the exact local-only C2 scope")

    return {
        "clean": True,
        "network": release["network"],
        "node": _C2_NODE,
        "controller_id": _C2_CONTROLLER,
        "age_seconds": age,
        "expires_at": release["expires_at"],
        "release_path": str(Path(release_path)),
        "release_sha256": digest,
        "release_file_sha256": file_sha,
        "transaction_path": str(tx_path),
        "transaction_sha256": verified_tx["transaction_sha256"],
        "predecessor_binding": verified_tx["predecessor_binding"],
        "successor_binding": verified_tx["successor_binding"],
        "validator_address": verified_tx["validator_address"],
        "private_state_update_authorized": True,
        "network_access_authorized": False,
        "service_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_authorized": False,
    }


def _claim_path(paths: PrivateStatePaths, release_sha256: str) -> Path:
    return paths.root / _CLAIM_DIRECTORY[0] / _CLAIM_DIRECTORY[1] / f"{release_sha256}.json"


def inspect_c2_state_extension_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledge_release_sha256: str,
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    canary_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    verified = verify_c2_state_extension_release(
        paths,
        private_state,
        Path(release_path),
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        canary_max_age_seconds=canary_max_age_seconds,
        now=now,
        operation=operation,
    )
    if acknowledge_release_sha256 != verified["release_sha256"]:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_ACK_MISMATCH", "release SHA-256 acknowledgement does not match")
    claimed = _claim_path(paths, verified["release_sha256"]).exists()
    return {
        **verified,
        "release_already_claimed": claimed,
        "private_state_updated": False,
        "live_mutation_performed": False,
        "network_access_performed": False,
        "service_mutation_performed": False,
        "chain_mutation_performed": False,
        "validator_vote_performed": False,
    }


def _write_evidence(
    paths: PrivateStatePaths,
    evidence: dict[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    evidence["evidence_sha256"] = _artifact_digest(evidence, "evidence_sha256")
    return _write_canonical_artifact(
        paths.root / _EVIDENCE_DIRECTORY[0] / _EVIDENCE_DIRECTORY[1],
        evidence,
        digest_field="evidence_sha256",
        stamp_field="completed_at",
        operation=operation,
    )


def _claim_release(
    paths: PrivateStatePaths,
    verified: Mapping[str, Any],
    *,
    operation: OperationIdentity,
    claimed_at: str,
) -> Path:
    claim = {
        "kind": "main_computer.mother.deployment_c2_state_extension_claim.v1",
        "schema_version": 1,
        "claimed_at": claimed_at,
        "release_sha256": verified["release_sha256"],
        "transaction_sha256": verified["transaction_sha256"],
        "predecessor_binding": verified["predecessor_binding"],
        "successor_binding": verified["successor_binding"],
    }
    destination = _claim_path(paths, verified["release_sha256"])
    _ensure_private_directory(destination.parent, operation=operation)
    try:
        atomic_files.durable_create(destination, canonical_json(claim), operation=operation)
        _secure_private_path(destination, is_directory=False, operation=operation)
    except Exception as exc:
        if destination.exists():
            raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_RELEASE_CONSUMED", "C2 state-extension release is already claimed") from exc
        raise
    return destination


def execute_c2_state_extension_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledge_release_sha256: str,
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    canary_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    verified = verify_c2_state_extension_release(
        paths,
        private_state,
        Path(release_path),
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        canary_max_age_seconds=canary_max_age_seconds,
        now=now,
        operation=operation,
    )
    if acknowledge_release_sha256 != verified["release_sha256"]:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_ACK_MISMATCH", "release SHA-256 acknowledgement does not match")
    if _claim_path(paths, verified["release_sha256"]).exists():
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_RELEASE_CONSUMED", "C2 state-extension release is already claimed")

    started = _utc(None if now is None else now.astimezone(timezone.utc).isoformat(), "started_at")

    tx, _, _ = _canonical_file(Path(verified["transaction_path"]), label="C2 state-extension transaction")
    secret = _load_secret(paths, tx)
    old_doc = _document(private_state)
    successor_doc = _extend_document(
        old_doc,
        network=verified["network"],
        generated_at=secret["generated_at"],
        validator_private_key=secret["validator"]["private_key"],
        hub_private_key=secret["hub_admin"]["private_key"],
    )
    closure = prepare_private_state_successor(
        private_state,
        successor_doc,
        updated_at=secret["generated_at"],
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    if _binding_from_closure(closure) != verified["successor_binding"]:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_SUCCESSOR_MISMATCH", "execution successor does not match released commitment")

    # Consume the one-use authority only after every deterministic local input has
    # been reconstructed and revalidated.  After this point the only authorized
    # mutation is the exact private-state generation replacement.
    claim_path = _claim_release(paths, verified, operation=operation, claimed_at=started)

    status = "pass"
    failure: dict[str, Any] | None = None
    try:
        result = replace_verified_private_state(
            paths,
            closure,
            private_state.binding,
            operation=operation,
        )
        if not result.installed:
            raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_CONFLICT", "successor generation was not installed")
    except Exception as exc:
        status = "manual-review-required"
        failure = {
            "code": getattr(exc, "code", type(exc).__name__),
            "message": str(exc),
        }

    try:
        observed = read_private_state(paths, operation=operation)
        observed_binding = _binding(observed)
    except Exception:
        observed = None
        observed_binding = None

    committed = observed is not None and observed_binding == verified["successor_binding"]
    if status == "pass" and not committed:
        status = "manual-review-required"
        failure = {
            "code": "MOTHER_DEPLOY_C2_STATE_EXTENSION_POSTCONDITION_FAILED",
            "message": "successor generation did not verify after replacement",
        }

    completed = _utc(None if now is None else now.astimezone(timezone.utc).isoformat(), "completed_at")
    predecessor_prefix = f"predecessor/generation-{private_state.binding.generation:08d}/"
    recovery_names = (
        tuple(item.relative_path for item in observed.recovery_manifest.entries)
        if observed is not None
        else ()
    )
    predecessor_recovery_preserved = committed and all(
        predecessor_prefix + suffix in recovery_names
        for suffix in (
            "identity.private.yaml",
            "identity.private.meta.json",
            "private-recovery/manifest.json",
        )
    )
    if status == "pass" and not predecessor_recovery_preserved:
        status = "manual-review-required"
        failure = {
            "code": "MOTHER_DEPLOY_C2_STATE_EXTENSION_RECOVERY_MISSING",
            "message": "successor does not preserve the exact predecessor recovery closure",
        }

    evidence = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": status,
        "network": verified["network"],
        "node": _C2_NODE,
        "controller_id": _C2_CONTROLLER,
        "release": {
            "locator": _relative_locator(paths, Path(release_path), label="release"),
            "sha256": verified["release_sha256"],
        },
        "transaction": {
            "locator": _relative_locator(paths, Path(verified["transaction_path"]), label="transaction"),
            "sha256": verified["transaction_sha256"],
        },
        "execution_claim": {
            "locator": _relative_locator(paths, claim_path, label="claim"),
        },
        "predecessor_binding": verified["predecessor_binding"],
        "successor_binding": verified["successor_binding"],
        "observed_binding": observed_binding,
        "reservation": {
            "validator_address": verified["validator_address"],
            "hub_admin_address": tx["reservation"]["hub_admin_address"],
            "private_key_material_in_evidence": False,
        },
        "failure": failure,
        "summary": {
            "clean": status == "pass",
            "complete": status == "pass",
            "private_state_updated": committed,
            "generation_advanced_exactly_once": committed
            and verified["successor_binding"]["generation"] == verified["predecessor_binding"]["generation"] + 1,
            "predecessor_recovery_preserved": predecessor_recovery_preserved,
            "existing_state_outside_c2_preserved": committed
            and observed is not None
            and canonical_json(_without_c2(_document(observed), network=verified["network"])) == private_state.canonical_object_bytes,
            "c2_reserved": committed,
            "network_access_performed": False,
            "service_mutation_count": 0,
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_performed": False,
            "next_phase": "preflight-c2-standby" if status == "pass" else "manual-review-required",
        },
    }
    evidence_path, evidence_sha = _write_evidence(paths, evidence, operation=operation)
    response = {
        "status": status,
        "network": verified["network"],
        "node": _C2_NODE,
        "evidence": {"path": str(evidence_path), "sha256": evidence_sha},
        "summary": evidence["summary"],
    }
    if failure is not None:
        response["failure"] = failure
    return response


def verify_c2_state_extension_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    evidence, _, file_sha = _canonical_file(Path(evidence_path), label="C2 state-extension evidence")
    if evidence.get("kind") != _EVIDENCE_KIND or evidence.get("schema_version") != 1:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "C2 state-extension evidence kind is invalid")
    digest = _artifact_digest(evidence, "evidence_sha256")
    if evidence.get("evidence_sha256") != digest or evidence.get("status") != "pass":
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_INVALID", "C2 state-extension evidence is not a passing bound artifact")
    age = _age_seconds(evidence["completed_at"], now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_EXPIRED", "C2 state-extension evidence is too old")
    if evidence.get("successor_binding") != _binding(private_state):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_SUCCESSOR_MISMATCH", "current Mother generation is not the evidenced C2 successor")
    predecessor = _mapping(evidence.get("predecessor_binding"), "predecessor_binding")
    successor = _mapping(evidence.get("successor_binding"), "successor_binding")
    if successor.get("generation") != predecessor.get("generation") + 1:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_SUCCESSOR_MISMATCH", "evidence did not advance exactly one generation")

    doc = _document(private_state)
    network = evidence["network"]
    network_state = _mapping(_mapping(doc["networks"], "networks")[network], f"networks.{network}")
    validators = _mapping(network_state["validators"], f"networks.{network}.validators")
    nodes = _mapping(network_state["nodes"], f"networks.{network}.nodes")
    targets = _mapping(_mapping(network_state["deployment"], "deployment")["targets"], "deployment.targets")
    seeds = _mapping(network_state["node_seed_material"], "node_seed_material")
    reservation = _mapping(evidence.get("reservation"), "reservation")
    if (
        _mapping(validators.get(_C2_NODE), "C2 validator").get("address") != reservation.get("validator_address")
        or _mapping(nodes.get(_C2_NODE), "C2 node").get("host") != _C2_CONTROLLER
        or _mapping(targets.get(_C2_NODE), "C2 target").get("controller_ref")
        != f"networks.{network}.coolify.controllers.{_C2_CONTROLLER}"
        or _mapping(_mapping(_mapping(seeds.get(_C2_NODE), "C2 seed").get("wallets"), "C2 wallets").get("hub_admin"), "C2 hub").get("address")
        != reservation.get("hub_admin_address")
    ):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_SUCCESSOR_MISMATCH", "C2 reservation fields do not match evidence")

    prefix = f"predecessor/generation-{predecessor['generation']:08d}/"
    recovery_names = tuple(item.relative_path for item in private_state.recovery_manifest.entries)
    if not all(
        prefix + suffix in recovery_names
        for suffix in (
            "identity.private.yaml",
            "identity.private.meta.json",
            "private-recovery/manifest.json",
        )
    ):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_RECOVERY_MISSING", "predecessor recovery closure is not preserved")

    predecessor_identity_path = prefix + "identity.private.yaml"
    predecessor_object = next(
        (item for item in private_state.recovery_objects if item.relative_path == predecessor_identity_path),
        None,
    )
    if predecessor_object is None:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_RECOVERY_MISSING", "predecessor identity recovery object is missing")
    try:
        predecessor_document = yaml.safe_load(predecessor_object.payload.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_RECOVERY_MISSING", "predecessor identity recovery object is malformed") from exc
    if type(predecessor_document) is not dict:
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_RECOVERY_MISSING", "predecessor identity recovery object is not a mapping")
    if canonical_json(_without_c2(doc, network=network)) != canonical_json(predecessor_document):
        raise _fail(
            "MOTHER_DEPLOY_C2_STATE_EXTENSION_PRESERVATION_FAILED",
            "state outside the C2 reservation differs from the recovered predecessor",
        )

    plan = build_starter_deployment_plan(private_state, network=network, selected_nodes=(_C2_NODE,))
    sequence = plan.get("sequence")
    if type(sequence) is not list or len(sequence) != 1 or sequence[0].get("node") != _C2_NODE or sequence[0].get("mode") != "soft":
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_PLAN_INVALID", "existing deployment machinery does not compile C2 as a soft target")
    if sequence[0].get("blockers"):
        raise _fail("MOTHER_DEPLOY_C2_STATE_EXTENSION_PLAN_INVALID", "C2 reservation still has node-specific deployment blockers")

    return {
        "clean": True,
        "network": network,
        "node": _C2_NODE,
        "controller_id": _C2_CONTROLLER,
        "age_seconds": age,
        "evidence_path": str(Path(evidence_path)),
        "evidence_sha256": digest,
        "evidence_file_sha256": file_sha,
        "predecessor_binding": predecessor,
        "successor_binding": successor,
        "generation_advanced_exactly_once": True,
        "predecessor_recovery_preserved": True,
        "existing_state_outside_c2_preserved": True,
        "c2_deployment_plan_compiles": True,
        "c2_mode": "soft",
        "network_access_performed": False,
        "service_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "next_phase": "preflight-c2-standby",
    }


__all__ = [
    "C2StateExtensionStaging",
    "MotherDeploymentC2StateExtensionError",
    "build_c2_state_extension_release",
    "execute_c2_state_extension_release",
    "inspect_c2_state_extension_release",
    "stage_c2_state_extension",
    "verify_c2_state_extension_evidence",
    "verify_c2_state_extension_release",
    "verify_c2_state_extension_transaction",
    "write_c2_state_extension_release",
    "write_c2_state_extension_transaction",
]
