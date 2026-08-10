"""C2-only genesis/replica standby preparation.

This boundary configures the already-created ``mainnetc-super2`` standby service
with a non-validator Besu replica Compose template and the canonical genesis
file needed by a future synchronization step.  It intentionally performs only an
exact Coolify service PATCH.  It does not deploy or start the service, perform a
replica sync, publish routing/topology, restart any validator, or cast a QBFT
vote.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import resolve_coolify_controller
from .deployment_c2_state_extension import verify_c2_state_extension_evidence
from .deployment_identity_rollback import verify_identity_rollback_cycle_evidence
from .ethereum_identity import is_private_key
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_C2_NODE = "mainnetc-super2"
_A1_NODE = "mainneta-super1"
_C2_CONTROLLER = "coolify-c"
_A_CONTROLLER = "coolify-a"

_TRANSACTION_KIND = "main_computer.mother.deployment_c2_replica_standby_transaction.v1"
_RELEASE_KIND = "main_computer.mother.deployment_c2_replica_standby_release.v1"
_EXECUTION_KIND = "main_computer.mother.deployment_c2_replica_standby_execution_result.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_c2_replica_standby_verification.v1"

_TRANSACTION_DIRECTORY = ("actions", "deployment-c2-replica-standby-transactions")
_RELEASE_DIRECTORY = ("actions", "deployment-c2-replica-standby-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-c2-replica-standby-claims")
_EXECUTION_DIRECTORY = ("actions", "deployment-c2-replica-standby-executions")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-c2-replica-standby")

_C2_STATE_EVIDENCE_DIRECTORY = ("evidence", "deployment-c2-state-extension")
_C2_IDENTITY_EVIDENCE_DIRECTORY = ("evidence", "deployment-c2-standby-identity")
_IDENTITY_EXECUTION_DIRECTORY = ("actions", "deployment-identity-executions")
_IDENTITY_ROLLBACK_EVIDENCE_DIRECTORY = ("evidence", "deployment-identity-rollbacks")
_GENESIS_BIRTH_EVIDENCE_DIRECTORY = ("evidence", "deployment-genesis-birth")
_GENESIS_BIRTH_RELEASE_DIRECTORY = ("actions", "deployment-genesis-birth-releases")
_GENESIS_EXECUTION_DIRECTORY = ("actions", "deployment-genesis-executions")
_GENESIS_RELEASE_DIRECTORY = ("actions", "deployment-genesis-releases")
_GENESIS_TRANSACTION_DIRECTORY = ("actions", "deployment-genesis-transactions")

_IDENTITY_EVIDENCE_KIND = "main_computer.mother.deployment_c2_standby_identity_verification.v1"
_IDENTITY_EXECUTION_KIND = "main_computer.mother.deployment_identity_execution_result.v1"
_GENESIS_BIRTH_EVIDENCE_KIND = "main_computer.mother.deployment_genesis_birth_evidence.v1"
_GENESIS_TRANSACTION_KIND = "main_computer.mother.deployment_genesis_transaction.v1"

_EXPECTED_IDENTITY_KEYS = {
    "MC_MOTHER_VALIDATOR_PRIVATE_KEY",
    "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY",
}
_BESU_IMAGE = "hyperledger/besu:latest"
_INIT_IMAGE = "alpine:3.20"
_DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_DEFAULT_OPENER = urllib.request.build_opener()


class MotherDeploymentC2ReplicaStandbyError(RuntimeError):
    """C2 replica standby preparation failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentC2ReplicaStandbyError:
    return MotherDeploymentC2ReplicaStandbyError(code, message)


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_INVALID", f"{path} must be a non-empty string")
    text = value.strip()
    if text in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._:-]+", text):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_INVALID", f"{path} is not a safe identifier")
    return text


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_INVALID", f"{path} must be a lowercase SHA-256")
    return value


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_INVALID", f"{path} must be a UTC timestamp")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_INVALID", f"{path} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_INVALID", f"{path} must be UTC")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None = None, *, path: str = "created_at") -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(value, path)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _age(value: Any, *, now: datetime | None, path: str) -> int:
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - _parse_utc(value, path)).total_seconds())
    if age < -1:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_FUTURE_TIME", f"{path} is in the future")
    return max(0, age)


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        value = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_STATE_INVALID", "Mother private state is not canonical JSON") from exc
    if type(value) is not dict:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_STATE_INVALID", "Mother private state is not an object")
    return value


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_INVALID", f"{path} must be an object")
    return value


_PRIVATE_KEY_LITERAL_RE = re.compile(r"0x[0-9a-fA-F]{64}")
_SENSITIVE_KEY_RE = re.compile(r"(?:^|[_-])(private[_-]?key|secret|mnemonic|password|token)(?:$|[_-])", re.IGNORECASE)


def _looks_sensitive_key(value: Any) -> bool:
    return isinstance(value, str) and bool(_SENSITIVE_KEY_RE.search(value))


def _is_private_key_literal(value: Any) -> bool:
    return isinstance(value, str) and bool(_PRIVATE_KEY_LITERAL_RE.fullmatch(value))


def _contains_private_key_literal(value: Any) -> bool:
    """Return true only for contextual private-key literals.

    Besu genesis documents legitimately contain 32-byte ``0x`` hex fields such
    as ``mixHash``.  A bare regex scan of every string misclassifies those hash
    fields as private keys, so the secret guard is scoped to sensitive field
    names and common key/value environment-variable shapes.
    """

    if isinstance(value, Mapping):
        label = value.get("key")
        body_value = value.get("value")
        if _looks_sensitive_key(label) and _is_private_key_literal(body_value):
            return True
        name = value.get("name")
        if _looks_sensitive_key(name) and _is_private_key_literal(body_value):
            return True
        for key, item in value.items():
            if _looks_sensitive_key(key) and _is_private_key_literal(item):
                return True
            if _contains_private_key_literal(item):
                return True
    if isinstance(value, list):
        return any(_contains_private_key_literal(item) for item in value)
    return False


def _canonical_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_INVALID", f"{label} could not be read as canonical JSON") from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_INVALID", f"{label} is not canonical JSON")
    return value, raw, hashlib.sha256(raw).hexdigest()


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    try:
        return Path(path).resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_PATH_UNSAFE", f"{label} is outside Mother state") from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_PATH_UNSAFE", f"{label} locator must be relative POSIX")
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_PATH_UNSAFE", f"{label} locator is unsafe")
    resolved = (paths.root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_PATH_UNSAFE", f"{label} escapes Mother state") from exc
    return resolved


def _beneath(paths: PrivateStatePaths, path: Path, parts: tuple[str, str], *, label: str) -> Path:
    root = (paths.root / parts[0] / parts[1]).resolve(strict=False)
    candidate = Path(path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_PATH_UNSAFE", f"{label} is outside its canonical directory") from exc
    return candidate


def _ensure_directory(paths: PrivateStatePaths, parts: tuple[str, str], *, operation: OperationIdentity) -> Path:
    current = paths.root
    for part in parts:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: value for key, value in document.items() if key != field})).hexdigest()


def _public_node_id(private_key: str) -> str:
    if not is_private_key(private_key):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_STATE_INVALID", "A1 validator private key is invalid")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:  # pragma: no cover
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_DEPENDENCY_MISSING", "cryptography is required to derive the bootnode public ID") from exc
    key = ec.derive_private_key(int(private_key[2:], 16), ec.SECP256K1())
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return public[1:].hex()


def _advertised_host(base_url: str) -> str:
    host = urlsplit(base_url).hostname
    if type(host) is not str or not re.fullmatch(r"[A-Za-z0-9.\[\]:-]+", host):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_CONTROLLER_INVALID", "A1 controller URL has no safe hostname")
    return host.lower()


def _raw_items(payload: Any) -> list[Mapping[str, Any]]:
    if type(payload) is list:
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        items: list[Mapping[str, Any]] = []
        if any(key in payload for key in ("uuid", "id", "name", "key")):
            items.append(payload)
        for key in ("data", "service", "resource"):
            if isinstance(payload.get(key), Mapping):
                items.append(payload[key])
        for key in ("services", "resources", "envs", "environment_variables", "variables"):
            if type(payload.get(key)) is list:
                items.extend(item for item in payload[key] if isinstance(item, Mapping))
        return items
    return []


def _text(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if type(value) is str and value.strip():
            return value.strip()
        if type(value) is int:
            return str(value)
    return ""


def _env_key(item: Mapping[str, Any]) -> str:
    return _text(item, "key", "name", "variable")


def _env_uuid(item: Mapping[str, Any]) -> str:
    return _text(item, "uuid", "id")


def _visible_value(item: Mapping[str, Any]) -> str | None:
    for key in ("value", "real_value", "literal_value"):
        value = item.get(key)
        if type(value) is str and value and not (set(value) <= {"*", "•"}) and value.lower() not in {"redacted", "<redacted>", "masked"}:
            return value
    return None


def _extract_service_uuid_from_identity_evidence(identity: Mapping[str, Any]) -> str:
    execution = identity.get("execution")
    if not isinstance(execution, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_REQUIRED", "identity evidence lacks execution binding")
    endpoint = identity.get("endpoint")
    candidates: list[str] = []
    if isinstance(endpoint, Mapping) and type(endpoint.get("path")) is str:
        candidates.append(endpoint["path"])
    for result in identity.get("identity_results", []):
        if isinstance(result, Mapping) and type(result.get("endpoint")) is str:
            candidates.append(result["endpoint"])
    for candidate in candidates:
        match = re.fullmatch(r"/api/v1/services/([^/]+)/envs", candidate)
        if match:
            return _identifier(match.group(1), "identity service UUID")
    # v44 evidence does not duplicate the endpoint in every identity result; use execution path file.
    execution_path = execution.get("path")
    if type(execution_path) is str:
        try:
            doc, _, _ = _canonical_file(Path(execution_path), label="identity execution")
            for receipt in doc.get("mutation_receipts", []):
                if isinstance(receipt, Mapping):
                    endpoint = receipt.get("endpoint")
                    if type(endpoint) is str:
                        match = re.fullmatch(r"/api/v1/services/([^/]+)/envs", endpoint)
                        if match:
                            return _identifier(match.group(1), "identity service UUID")
        except MotherDeploymentC2ReplicaStandbyError:
            raise
        except Exception as exc:  # pragma: no cover
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_REQUIRED", "identity execution cannot be traced") from exc
    raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_REQUIRED", "identity evidence does not bind one service UUID")


def _verify_c2_extension(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    c2_state_extension_evidence: Path,
    *,
    max_age_seconds: int,
    now: datetime | None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    verified = verify_c2_state_extension_evidence(
        paths,
        private_state,
        Path(c2_state_extension_evidence),
        max_age_seconds=max_age_seconds,
        now=now,
        operation=operation,
    )
    if (
        verified.get("clean") is not True
        or verified.get("node") != _C2_NODE
        or verified.get("controller_id") != _C2_CONTROLLER
        or verified.get("c2_mode") != "soft"
        or verified.get("chain_mutation_count") != 0
        or verified.get("service_mutation_count") != 0
        or verified.get("validator_mutation_count") != 0
        or verified.get("validator_restart_count") != 0
        or verified.get("validator_vote_performed") is not False
    ):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_C2_EXTENSION_REQUIRED", "clean C2 state-extension evidence is required")
    return verified


def _verify_identity_gate(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    identity_evidence_path: Path,
    identity_rollback_evidence_path: Path,
    *,
    max_age_seconds: int,
    now: datetime | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    candidate = _beneath(
        paths,
        Path(identity_evidence_path),
        _C2_IDENTITY_EVIDENCE_DIRECTORY,
        label="C2 identity evidence",
    )
    identity, _, identity_byte_sha = _canonical_file(candidate, label="C2 identity evidence")
    if (
        identity.get("kind") != _IDENTITY_EVIDENCE_KIND
        or identity.get("schema_version") != 1
        or identity.get("network") != "mainnet"
        or identity.get("node") != _C2_NODE
        or identity.get("nodes") != [_C2_NODE]
        or identity.get("controller_id") != _C2_CONTROLLER
        or identity.get("mother_binding") != _binding(private_state)
        or identity.get("summary", {}).get("clean") is not True
        or identity.get("summary", {}).get("verified_identity_key_count") != 2
        or identity.get("next_phase") != "prove-identity-rollback-cycle-before-genesis"
        or identity.get("policy", {}).get("secrets_in_output") is not False
        or identity.get("policy", {}).get("service_deploy_or_start_performed") is not False
        or identity.get("policy", {}).get("replica_sync_performed") is not False
        or identity.get("validator_vote_performed") is not False
        or _contains_private_key_literal(identity)
    ):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_REQUIRED", "clean current C2 identity evidence is required")
    if _age(identity.get("observed_at"), now=now, path="identity observed_at") > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_STALE", "C2 identity evidence is outside the freshness window")

    execution_ref = _mapping(identity.get("execution"), "identity.execution")
    execution_path_value = execution_ref.get("path")
    if type(execution_path_value) is not str or not execution_path_value:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_REQUIRED", "identity evidence lacks execution path")
    execution_path = _beneath(
        paths,
        Path(execution_path_value),
        _IDENTITY_EXECUTION_DIRECTORY,
        label="identity execution",
    )
    execution, _, execution_byte_sha = _canonical_file(execution_path, label="identity execution")
    if (
        execution.get("kind") != _IDENTITY_EXECUTION_KIND
        or execution.get("status") != "pass"
        or execution.get("network") != "mainnet"
        or execution.get("nodes") != [_C2_NODE]
        or execution.get("mother_binding") != _binding(private_state)
        or execution.get("summary", {}).get("complete") is not True
        or _contains_private_key_literal(execution)
    ):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_REQUIRED", "C2 identity execution is not a current success")
    if execution_ref.get("file_sha256") not in {None, execution_byte_sha}:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_REQUIRED", "identity evidence and execution file digest disagree")
    identity_profile = _sha256(execution.get("identity_profile_sha256"), "identity profile SHA-256")
    rollback = verify_identity_rollback_cycle_evidence(
        paths,
        private_state,
        Path(identity_rollback_evidence_path),
        identity_profile_sha256_value=identity_profile,
        network="mainnet",
        nodes=(_C2_NODE,),
        before_execution_started_at=_identifier(execution.get("started_at"), "identity execution started_at"),
        current_execution_sha256=execution_byte_sha,
    )
    return (
        {
            "path": str(candidate),
            "sha256": identity_byte_sha,
            "observed_at": identity["observed_at"],
            "service_uuid": _extract_service_uuid_from_identity_evidence(identity),
            "identity_profile_sha256": identity_profile,
            "verified_identity_key_count": 2,
        },
        {
            "path": str(execution_path),
            "sha256": execution_byte_sha,
            "started_at": execution.get("started_at"),
            "completed_at": execution.get("completed_at"),
            "identity_profile_sha256": identity_profile,
            "commitments": _identity_commitments_from_execution(execution),
        },
        rollback,
    )


def _identity_commitments_from_execution(execution: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    receipts = execution.get("mutation_receipts")
    if type(receipts) is not list:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_REQUIRED", "identity execution receipts are missing")
    commitments: dict[str, dict[str, Any]] = {}
    service_uuid: str | None = None
    for receipt in receipts:
        if not isinstance(receipt, Mapping) or receipt.get("node") != _C2_NODE:
            continue
        key = receipt.get("environment_key")
        endpoint = receipt.get("endpoint")
        match = re.fullmatch(r"/api/v1/services/([^/]+)/envs", str(endpoint or ""))
        if key not in _EXPECTED_IDENTITY_KEYS or match is None:
            continue
        this_uuid = _identifier(match.group(1), "identity service UUID")
        if service_uuid not in (None, this_uuid):
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_REQUIRED", "identity receipts disagree about the service UUID")
        service_uuid = this_uuid
        commitments[str(key)] = {
            "environment_variable_uuid": str(receipt.get("environment_variable_uuid")),
            "value_sha256": _sha256(receipt.get("value_sha256"), f"{key} value SHA-256"),
        }
    if set(commitments) != _EXPECTED_IDENTITY_KEYS:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_REQUIRED", "identity execution does not cover the exact C2 key set")
    return commitments


def _verify_birth_lineage(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    genesis_birth_evidence_path: Path,
    *,
    c2_extension: Mapping[str, Any],
    max_age_seconds: int,
    now: datetime | None,
) -> tuple[dict[str, Any], dict[str, Any], Path, str]:
    evidence_path = _beneath(
        paths,
        Path(genesis_birth_evidence_path),
        _GENESIS_BIRTH_EVIDENCE_DIRECTORY,
        label="genesis birth evidence",
    )
    evidence, _, evidence_byte_sha = _canonical_file(evidence_path, label="genesis birth evidence")
    predecessor = _mapping(c2_extension.get("predecessor_binding"), "C2 predecessor binding")
    allowed_bindings = [_binding(private_state), dict(predecessor)]
    summary = evidence.get("summary")
    proof = evidence.get("proof")
    if (
        evidence.get("kind") != _GENESIS_BIRTH_EVIDENCE_KIND
        or evidence.get("status") != "pass"
        or evidence.get("network") != "mainnet"
        or evidence.get("initial_node") != _A1_NODE
        or evidence.get("mother_binding") not in allowed_bindings
        or not isinstance(summary, Mapping)
        or not isinstance(proof, Mapping)
        or summary.get("clean") is not True
        or summary.get("initial_chain_proven") is not True
        or summary.get("next_phase") != "stage-soft-replica-configuration"
        or summary.get("manual_ssh_required") is not False
        or summary.get("public_endpoint_created") is not False
        or summary.get("soft_replica_untouched") is not True
        or proof.get("service_status") != "running:healthy"
        or proof.get("hub_local_rpc_verified") is not True
        or _contains_private_key_literal(evidence)
    ):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_BIRTH_REQUIRED", "clean genesis birth evidence is required")
    if _age(evidence.get("completed_at"), now=now, path="genesis birth completed_at") > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_BIRTH_STALE", "genesis birth evidence is outside the freshness window")

    birth_release_ref = _mapping(evidence.get("release"), "birth evidence release")
    birth_release_path = _resolve_locator(paths, birth_release_ref.get("locator"), label="genesis birth release")
    birth_release_path = _beneath(paths, birth_release_path, _GENESIS_BIRTH_RELEASE_DIRECTORY, label="genesis birth release")
    birth_release, _, _ = _canonical_file(birth_release_path, label="genesis birth release")
    if _sha256(birth_release.get("release_sha256"), "birth release SHA-256") != _sha256(birth_release_ref.get("sha256"), "birth evidence release SHA-256"):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_BIRTH_INVALID", "birth release digest mismatch")

    execution_ref = _mapping(birth_release.get("genesis_execution"), "birth release genesis execution")
    execution_path = _resolve_locator(paths, execution_ref.get("locator"), label="genesis execution")
    execution_path = _beneath(paths, execution_path, _GENESIS_EXECUTION_DIRECTORY, label="genesis execution")
    execution, _, execution_byte_sha = _canonical_file(execution_path, label="genesis execution")
    expected_execution_sha = _sha256(execution_ref.get("sha256"), "genesis execution SHA-256")
    if execution_byte_sha != expected_execution_sha or evidence.get("genesis_execution_sha256") != expected_execution_sha:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_BIRTH_INVALID", "birth evidence does not bind the genesis execution")

    release_ref = _mapping(execution.get("release"), "genesis execution release")
    release_path = _resolve_locator(paths, release_ref.get("locator"), label="genesis release")
    release_path = _beneath(paths, release_path, _GENESIS_RELEASE_DIRECTORY, label="genesis release")
    release, _, _ = _canonical_file(release_path, label="genesis release")
    if _sha256(release.get("genesis_release_sha256"), "genesis release SHA-256") != _sha256(release_ref.get("sha256"), "genesis execution release SHA-256"):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_BIRTH_INVALID", "genesis release digest mismatch")

    tx_ref = _mapping(release.get("genesis_transaction"), "genesis release transaction")
    tx_path = _resolve_locator(paths, tx_ref.get("locator"), label="genesis transaction")
    tx_path = _beneath(paths, tx_path, _GENESIS_TRANSACTION_DIRECTORY, label="genesis transaction")
    tx, tx_raw, tx_byte_sha = _canonical_file(tx_path, label="genesis transaction")
    digest = _digest_without(tx, "genesis_transaction_sha256")
    if (
        tx.get("kind") != _GENESIS_TRANSACTION_KIND
        or tx.get("genesis_transaction_sha256") != digest
        or _sha256(tx_ref.get("sha256"), "genesis transaction SHA-256") != digest
        or _sha256(tx_ref.get("byte_sha256"), "genesis transaction byte SHA-256") != tx_byte_sha
        or _contains_private_key_literal(tx)
    ):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_BIRTH_INVALID", "genesis transaction lineage is invalid")
    genesis_block = _mapping(tx.get("genesis"), "genesis")
    genesis = _mapping(genesis_block.get("canonical_json"), "genesis.canonical_json")
    genesis_sha = _sha256(genesis_block.get("canonical_json_sha256"), "genesis SHA-256")
    if hashlib.sha256(canonical_json(dict(genesis))).hexdigest() != genesis_sha or genesis_sha != evidence.get("genesis_sha256"):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_BIRTH_INVALID", "canonical genesis digest mismatch")
    return (
        {
            "path": str(evidence_path),
            "sha256": evidence_byte_sha,
            "completed_at": evidence["completed_at"],
            "binding_source": "current" if evidence.get("mother_binding") == _binding(private_state) else "c2-predecessor-preserved",
            "chain_id": proof["chain_id"],
            "genesis_sha256": genesis_sha,
            "validator_set_at_birth": list(proof.get("validator_set") or []),
            "hub_local_rpc_url": proof["hub_local_rpc_url"],
        },
        tx,
        tx_path,
        tx_byte_sha,
    )


def _c2_validator_address(private_state: PrivateStateReadResult, *, network: str = "mainnet") -> str:
    state = _document(private_state)
    try:
        value = state["networks"][network]["validators"][_C2_NODE]["address"]
    except (KeyError, TypeError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_STATE_INVALID", "C2 validator reservation is missing") from exc
    if type(value) is not str or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_STATE_INVALID", "C2 validator address is invalid")
    return value.lower()


def _a1_bootnode(private_state: PrivateStateReadResult, *, network: str = "mainnet") -> dict[str, Any]:
    state = _document(private_state)
    try:
        private_key = state["networks"][network]["validators"][_A1_NODE]["private_key"]
    except (KeyError, TypeError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_STATE_INVALID", "A1 validator identity is missing") from exc
    public_node_id = _public_node_id(private_key)
    controller = resolve_coolify_controller(private_state, network, _A_CONTROLLER, require_enabled=True, require_token=False)
    host = _advertised_host(controller.base_url)
    return {
        "enode": f"enode://{public_node_id}@{host}:30303",
        "node_id_sha256": hashlib.sha256(public_node_id.encode("ascii")).hexdigest(),
        "advertised_host": host,
        "p2p_port": 30303,
    }


def _replica_compose(*, node: str, chain_id: int, genesis: Mapping[str, Any], bootnode_enode: str) -> str:
    encoded_genesis = base64.b64encode(canonical_json(dict(genesis))).decode("ascii")
    return "\n".join([
        f"name: {node}",
        "",
        "services:",
        "  mother-replica-init:",
        f"    image: {_INIT_IMAGE}",
        '    restart: "no"',
        "    environment:",
        '      MC_MOTHER_VALIDATOR_PRIVATE_KEY: "${MC_MOTHER_VALIDATOR_PRIVATE_KEY}"',
        "    volumes:",
        "      - mother-config:/config",
        "      - mother-data:/var/lib/besu",
        "    command:",
        "      - sh",
        "      - -ec",
        "      - |",
        "        umask 077",
        f"        printf '%s' '{encoded_genesis}' | base64 -d > /config/genesis.json",
        '        key="$${MC_MOTHER_VALIDATOR_PRIVATE_KEY#0x}"',
        '        test "$${#key}" -eq 64',
        '        printf \'%s\' "$${key}" > /config/nodekey',
        "        mkdir -p /var/lib/besu",
        "        chown -R 1000:1000 /config /var/lib/besu",
        "        chmod 0400 /config/nodekey",
        "        chmod 0444 /config/genesis.json",
        f"  {node}:",
        f"    image: {_BESU_IMAGE}",
        '    restart: "no"',
        "    depends_on:",
        "      mother-replica-init:",
        "        condition: service_completed_successfully",
        "    command:",
        "      - --data-path=/var/lib/besu",
        "      - --genesis-file=/config/genesis.json",
        "      - --node-private-key-file=/config/nodekey",
        f"      - --network-id={chain_id}",
        "      - --sync-mode=FULL",
        "      - --data-storage-format=BONSAI",
        "      - --p2p-enabled=true",
        "      - --p2p-port=30303",
        "      - --discovery-enabled=true",
        f"      - --bootnodes={bootnode_enode}",
        "      - --rpc-http-enabled=true",
        "      - --rpc-http-host=0.0.0.0",
        "      - --rpc-http-port=8545",
        "      - --rpc-http-api=ETH,NET,WEB3,QBFT,ADMIN",
        f"      - --host-allowlist=localhost,127.0.0.1,{node}",
        "      - --min-gas-price=0",
        "    ports:",
        '      - "30303:30303/tcp"',
        '      - "30303:30303/udp"',
        "    volumes:",
        "      - mother-config:/config:ro",
        "      - mother-data:/var/lib/besu",
        "    labels:",
        "      main_computer.mother.stage: c2-replica-standby",
        f"      main_computer.mother.node: {node}",
        "      main_computer.mother.replica-sync: blocked",
        "      main_computer.mother.validator-activation: blocked",
        "",
        "volumes:",
        "  mother-config:",
        "  mother-data:",
        "",
    ])


def _transaction_from_inputs(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    *,
    c2_state_extension_evidence: Path,
    identity_evidence: Path,
    identity_rollback_evidence: Path,
    genesis_birth_evidence: Path,
    created_at: str | None,
    now: datetime | None,
    c2_state_extension_max_age_seconds: int,
    identity_max_age_seconds: int,
    genesis_birth_max_age_seconds: int,
    operation: OperationIdentity,
) -> dict[str, Any]:
    created = _timestamp(created_at)
    c2 = _verify_c2_extension(
        paths,
        private_state,
        Path(c2_state_extension_evidence),
        max_age_seconds=c2_state_extension_max_age_seconds,
        now=now,
        operation=operation,
    )
    identity, identity_execution, rollback = _verify_identity_gate(
        paths,
        private_state,
        Path(identity_evidence),
        Path(identity_rollback_evidence),
        max_age_seconds=identity_max_age_seconds,
        now=now,
    )
    birth, genesis_tx, genesis_tx_path, genesis_tx_byte_sha = _verify_birth_lineage(
        paths,
        private_state,
        Path(genesis_birth_evidence),
        c2_extension=c2,
        max_age_seconds=genesis_birth_max_age_seconds,
        now=now,
    )
    genesis_block = _mapping(genesis_tx["genesis"], "genesis")
    genesis = dict(_mapping(genesis_block["canonical_json"], "genesis.canonical_json"))
    chain_id = genesis_block.get("chain_id")
    if type(chain_id) is not int or chain_id <= 0:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_BIRTH_INVALID", "genesis chain ID is invalid")
    bootnode = _a1_bootnode(private_state)
    compose = _replica_compose(node=_C2_NODE, chain_id=chain_id, genesis=genesis, bootnode_enode=bootnode["enode"])
    compose_bytes = compose.encode("utf-8")
    compose_sha = hashlib.sha256(compose_bytes).hexdigest()
    body = {
        "name": _C2_NODE,
        "docker_compose_raw": base64.b64encode(compose_bytes).decode("ascii"),
        "instant_deploy": False,
    }
    body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
    service_uuid = _identifier(identity["service_uuid"], "C2 service UUID")
    endpoint = f"/api/v1/services/{quote(service_uuid, safe='')}"
    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": created,
        "network": "mainnet",
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "mother_binding": _binding(private_state),
        "node": _C2_NODE,
        "nodes": [_C2_NODE],
        "controller_id": _C2_CONTROLLER,
        "service_uuid": service_uuid,
        "staged_scope": "prepare-c2-genesis-replica-standby-without-sync",
        "c2_state_extension_evidence": {
            "locator": _relative(paths, Path(c2["evidence_path"]), label="C2 state-extension evidence"),
            "sha256": c2["evidence_sha256"],
            "node": _C2_NODE,
            "mode": "soft",
        },
        "identity_evidence": {
            "locator": _relative(paths, Path(identity["path"]), label="identity evidence"),
            "sha256": identity["sha256"],
            "observed_at": identity["observed_at"],
            "verified_identity_key_count": 2,
        },
        "identity_execution": {
            "locator": _relative(paths, Path(identity_execution["path"]), label="identity execution"),
            "sha256": identity_execution["sha256"],
            "started_at": identity_execution["started_at"],
            "completed_at": identity_execution["completed_at"],
            "identity_profile_sha256": identity_execution["identity_profile_sha256"],
        },
        "identity_rollback_cycle": {
            "locator": _relative(paths, Path(rollback["verification_path"]), label="identity rollback evidence"),
            "sha256": rollback["verification_sha256"],
            "identity_rollback_verification_sha256": rollback["identity_rollback_verification_sha256"],
            "observed_at": rollback["observed_at"],
            "reapplied_after_verified_rollback": True,
        },
        "genesis_birth_evidence": {
            "locator": _relative(paths, Path(birth["path"]), label="genesis birth evidence"),
            "sha256": birth["sha256"],
            "completed_at": birth["completed_at"],
            "binding_source": birth["binding_source"],
            "initial_chain_proven": True,
        },
        "genesis_transaction": {
            "locator": _relative(paths, genesis_tx_path, label="genesis transaction"),
            "sha256": _sha256(genesis_tx.get("genesis_transaction_sha256"), "genesis transaction SHA-256"),
            "byte_sha256": genesis_tx_byte_sha,
            "genesis_sha256": birth["genesis_sha256"],
        },
        "initial_chain": {
            "node": _A1_NODE,
            "controller_id": _A_CONTROLLER,
            "chain_id": chain_id,
            "validator_set_at_birth": list(birth["validator_set_at_birth"]),
            "hub_local_rpc_url": birth["hub_local_rpc_url"],
            "bootnode": bootnode,
        },
        "replica": {
            "node": _C2_NODE,
            "mode": "soft",
            "role_before_admission": "non-validator-replica-standby",
            "controller_id": _C2_CONTROLLER,
            "service_uuid": service_uuid,
            "validator_address": _c2_validator_address(private_state),
            "identity_commitments": dict(identity_execution["commitments"]),
            "genesis_sha256": birth["genesis_sha256"],
            "compose": {
                "format": "docker-compose-yaml",
                "besu_image": _BESU_IMAGE,
                "init_image": _INIT_IMAGE,
                "canonical_text": compose,
                "sha256": compose_sha,
                "byte_length": len(compose_bytes),
                "contains_private_key_value": False,
                "host_rpc_mapping_present": False,
                "public_http_endpoint_created": False,
                "service_deploy_or_start_requested": False,
            },
        },
        "mutation": {
            "ordinal": 1,
            "mutation_id": f"{_C2_NODE}.install-replica-standby-compose",
            "controller_id": _C2_CONTROLLER,
            "method": "PATCH",
            "endpoint": endpoint,
            "canonical_request_body": body,
            "body_sha256": body_sha,
            "success_statuses": [200, 201, 202],
        },
        "authority": {
            "configuration_apply_authorized": False,
            "replica_start_authorized": False,
            "replica_sync_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
        },
        "policy": {
            "network_access_performed": False,
            "live_mutation_performed": False,
            "service_deploy_or_start_performed": False,
            "replica_sync_performed": False,
            "chain_mutation_performed": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
            "routing_or_topology_published": False,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_C2_REPLICA_STANDBY_RELEASE_REQUIRED",
                "message": "an explicit expiring operator release is required for this exact C2 replica standby transaction",
            },
            {
                "code": "MOTHER_DEPLOY_C2_REPLICA_SYNC_NOT_AUTHORIZED",
                "message": "installing the standby Compose/genesis does not authorize replica synchronization",
            },
            {
                "code": "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED",
                "message": "standby preparation does not authorize QBFT admission or voting",
            },
        ],
        "summary": {
            "transaction_valid": True,
            "apply_ready": False,
            "mutation_count": 1,
            "identity_rollback_cycle_proven": True,
            "identity_reapplication_proven_after_rollback": True,
            "genesis_birth_proven": True,
            "standby_compose_compiled": True,
            "replica_configured": False,
            "replica_started": False,
            "replica_sync_performed": False,
            "service_mutation_count": 1,
            "identity_mutation_count": 0,
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_authorized": False,
            "validator_vote_performed": False,
            "next_phase_after_apply": "prove-c2-replica-standby-before-sync",
            "blocker_codes": [
                "MOTHER_DEPLOY_C2_REPLICA_STANDBY_RELEASE_REQUIRED",
                "MOTHER_DEPLOY_C2_REPLICA_SYNC_NOT_AUTHORIZED",
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED",
            ],
        },
    }
    if _contains_private_key_literal(transaction):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_SECRET_LEAK", "C2 replica standby transaction contains a private key value")
    transaction["c2_replica_standby_transaction_sha256"] = _digest_without(transaction, "c2_replica_standby_transaction_sha256")
    return transaction


def stage_c2_replica_standby_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    *,
    c2_state_extension_evidence: Path,
    identity_evidence: Path,
    identity_rollback_evidence: Path,
    genesis_birth_evidence: Path,
    c2_state_extension_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    genesis_birth_max_age_seconds: int = 86400,
    created_at: str | None = None,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    return _transaction_from_inputs(
        paths,
        private_state,
        c2_state_extension_evidence=Path(c2_state_extension_evidence),
        identity_evidence=Path(identity_evidence),
        identity_rollback_evidence=Path(identity_rollback_evidence),
        genesis_birth_evidence=Path(genesis_birth_evidence),
        created_at=created_at,
        now=now,
        c2_state_extension_max_age_seconds=c2_state_extension_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        genesis_birth_max_age_seconds=genesis_birth_max_age_seconds,
        operation=operation,
    )


def write_c2_replica_standby_transaction(paths: PrivateStatePaths, transaction: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(transaction)
    if document.get("kind") != _TRANSACTION_KIND or document.get("c2_replica_standby_transaction_sha256") != _digest_without(document, "c2_replica_standby_transaction_sha256") or _contains_private_key_literal(document):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_TRANSACTION_INVALID", "C2 replica standby transaction is malformed")
    payload = canonical_json(document)
    digest = document["c2_replica_standby_transaction_sha256"]
    root = _ensure_directory(paths, _TRANSACTION_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "c2replicastandby"
    destination = root / f"{stamp}-{document['c2_replica_standby_transaction_sha256'][:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_TRANSACTION_CONFLICT", "transaction destination contains different bytes")
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_c2_replica_standby_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    max_age_seconds: int = 300,
    c2_state_extension_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    genesis_birth_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    candidate = _beneath(paths, Path(transaction_path), _TRANSACTION_DIRECTORY, label="C2 replica standby transaction")
    document, raw, byte_sha = _canonical_file(candidate, label="C2 replica standby transaction")
    digest = _digest_without(document, "c2_replica_standby_transaction_sha256")
    if document.get("kind") != _TRANSACTION_KIND or document.get("c2_replica_standby_transaction_sha256") != digest or document.get("mother_binding") != _binding(private_state) or _contains_private_key_literal(document):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_TRANSACTION_INVALID", "C2 replica standby transaction is invalid or stale")
    age = _age(document.get("created_at"), now=now, path="transaction created_at")
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_TRANSACTION_STALE", "C2 replica standby transaction is outside the freshness window")
    expected = _transaction_from_inputs(
        paths,
        private_state,
        c2_state_extension_evidence=_resolve_locator(paths, document["c2_state_extension_evidence"]["locator"], label="C2 state-extension evidence"),
        identity_evidence=_resolve_locator(paths, document["identity_evidence"]["locator"], label="identity evidence"),
        identity_rollback_evidence=_resolve_locator(paths, document["identity_rollback_cycle"]["locator"], label="identity rollback evidence"),
        genesis_birth_evidence=_resolve_locator(paths, document["genesis_birth_evidence"]["locator"], label="genesis birth evidence"),
        created_at=document.get("created_at"),
        now=now,
        c2_state_extension_max_age_seconds=c2_state_extension_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        genesis_birth_max_age_seconds=genesis_birth_max_age_seconds,
        operation=operation,
    )
    if canonical_json(expected) != raw:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_TRANSACTION_INVALID", "C2 replica standby transaction no longer matches current inputs")
    return {
        "clean": True,
        "transaction_path": str(candidate),
        "transaction_file_sha256": byte_sha,
        "c2_replica_standby_transaction_sha256": digest,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "node": _C2_NODE,
        "nodes": [_C2_NODE],
        "controller_id": _C2_CONTROLLER,
        "service_uuid": document["service_uuid"],
        "staged_scope": document["staged_scope"],
        "genesis_sha256": document["replica"]["genesis_sha256"],
        "compose_sha256": document["replica"]["compose"]["sha256"],
        "mutation_count": 1,
        "service_mutation_count": 1,
        "identity_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_authorized": False,
        "validator_vote_performed": False,
        "replica_sync_authorized": False,
        "replica_start_authorized": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "next_phase": "c2-replica-standby-release-not-yet-authorized",
    }


def build_c2_replica_standby_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_transaction_sha256: str,
    max_age_seconds: int = 300,
    c2_state_extension_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    genesis_birth_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    verified = verify_c2_replica_standby_transaction(
        paths,
        private_state,
        Path(transaction_path),
        max_age_seconds=max_age_seconds,
        c2_state_extension_max_age_seconds=c2_state_extension_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        genesis_birth_max_age_seconds=genesis_birth_max_age_seconds,
        now=now,
        operation=operation,
    )
    ack = _sha256(acknowledged_transaction_sha256, "acknowledged transaction SHA-256")
    if ack != verified["c2_replica_standby_transaction_sha256"]:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_RELEASE_ACK_MISMATCH", "operator acknowledgement does not match the transaction")
    created = _timestamp(created_at)
    expires = (_parse_utc(created, "created_at") + timedelta(seconds=int(expires_in_seconds))).isoformat(timespec="seconds").replace("+00:00", "Z")
    transaction, _, transaction_file_sha = _canonical_file(Path(verified["transaction_path"]), label="C2 replica standby transaction")
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created,
        "expires_at": expires,
        "network": verified["network"],
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "mother_binding": dict(verified["mother_binding"]),
        "node": _C2_NODE,
        "nodes": [_C2_NODE],
        "controller_id": _C2_CONTROLLER,
        "service_uuid": verified["service_uuid"],
        "staged_scope": verified["staged_scope"],
        "transaction": {
            "locator": _relative(paths, Path(verified["transaction_path"]), label="transaction"),
            "sha256": verified["c2_replica_standby_transaction_sha256"],
            "byte_sha256": transaction_file_sha,
            "created_at": transaction["created_at"],
        },
        "authority": {
            "authorization_source": "explicit-operator-release",
            "configuration_apply_authorized": True,
            "replica_start_authorized": False,
            "replica_sync_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "requested_use_limit": 1,
        },
        "policy": {
            "consumption_enforcement_implemented": True,
            "release_consumption_receipt_required": True,
            "live_mutation_performed": False,
            "network_access_performed": False,
            "service_deploy_or_start_performed": False,
            "replica_sync_performed": False,
            "chain_mutation_performed": False,
            "validator_vote_performed": False,
            "secrets_in_output": False,
        },
        "summary": {
            "release_valid": True,
            "transaction_apply_authorized": True,
            "mutation_count": 1,
            "service_mutation_count": 1,
            "identity_mutation_count": 0,
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_authorized": False,
            "remaining_blocker_codes": [
                "MOTHER_DEPLOY_C2_REPLICA_SYNC_NOT_AUTHORIZED",
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED",
            ],
            "next_phase": "verify-c2-replica-standby-release",
        },
    }
    release["c2_replica_standby_release_sha256"] = _digest_without(release, "c2_replica_standby_release_sha256")
    return release


def write_c2_replica_standby_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(release)
    if document.get("kind") != _RELEASE_KIND or document.get("c2_replica_standby_release_sha256") != _digest_without(document, "c2_replica_standby_release_sha256") or _contains_private_key_literal(document):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_RELEASE_INVALID", "C2 replica standby release is malformed")
    payload = canonical_json(document)
    digest = document["c2_replica_standby_release_sha256"]
    root = _ensure_directory(paths, _RELEASE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "c2replicastandby"
    destination = root / f"{stamp}-{document['c2_replica_standby_release_sha256'][:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_RELEASE_CONFLICT", "release destination contains different bytes")
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_c2_replica_standby_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    c2_state_extension_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    genesis_birth_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    candidate = _beneath(paths, Path(release_path), _RELEASE_DIRECTORY, label="C2 replica standby release")
    document, _, byte_sha = _canonical_file(candidate, label="C2 replica standby release")
    digest = _digest_without(document, "c2_replica_standby_release_sha256")
    if document.get("kind") != _RELEASE_KIND or document.get("c2_replica_standby_release_sha256") != digest or document.get("mother_binding") != _binding(private_state) or _contains_private_key_literal(document):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_RELEASE_INVALID", "C2 replica standby release is invalid or stale")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    created = _parse_utc(document.get("created_at"), "release created_at")
    expires = _parse_utc(document.get("expires_at"), "release expires_at")
    if reference < created - timedelta(seconds=1) or reference > expires or int((reference - created).total_seconds()) > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_RELEASE_EXPIRED", "C2 replica standby release is outside the active window")
    tx_ref = _mapping(document.get("transaction"), "release transaction")
    tx_path = _resolve_locator(paths, tx_ref.get("locator"), label="C2 replica standby transaction")
    verified_tx = verify_c2_replica_standby_transaction(
        paths,
        private_state,
        tx_path,
        max_age_seconds=transaction_max_age_seconds,
        c2_state_extension_max_age_seconds=c2_state_extension_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        genesis_birth_max_age_seconds=genesis_birth_max_age_seconds,
        now=now,
        operation=operation,
    )
    expected = build_c2_replica_standby_release(
        paths,
        private_state,
        tx_path,
        acknowledged_transaction_sha256=verified_tx["c2_replica_standby_transaction_sha256"],
        max_age_seconds=transaction_max_age_seconds,
        c2_state_extension_max_age_seconds=c2_state_extension_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        genesis_birth_max_age_seconds=genesis_birth_max_age_seconds,
        expires_in_seconds=int((expires - created).total_seconds()),
        created_at=document["created_at"],
        now=now,
        operation=operation,
    )
    if canonical_json(expected) != canonical_json(document):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_RELEASE_INVALID", "release no longer matches its exact inputs")
    return {
        "clean": True,
        "release_path": str(candidate),
        "release_file_sha256": byte_sha,
        "c2_replica_standby_release_sha256": digest,
        "c2_replica_standby_transaction_sha256": verified_tx["c2_replica_standby_transaction_sha256"],
        "created_at": document["created_at"],
        "expires_at": document["expires_at"],
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "node": _C2_NODE,
        "nodes": [_C2_NODE],
        "controller_id": _C2_CONTROLLER,
        "service_uuid": document["service_uuid"],
        "staged_scope": document["staged_scope"],
        "transaction_apply_authorized": True,
        "configuration_apply_authorized": True,
        "replica_start_authorized": False,
        "replica_sync_authorized": False,
        "validator_vote_authorized": False,
        "live_execution_authorized": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "mutation_count": 1,
        "service_mutation_count": 1,
        "identity_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "next_phase": "apply-c2-replica-standby",
    }


def inspect_c2_replica_standby_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    c2_state_extension_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    genesis_birth_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    verified = verify_c2_replica_standby_release(
        paths,
        private_state,
        Path(release_path),
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        c2_state_extension_max_age_seconds=c2_state_extension_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        genesis_birth_max_age_seconds=genesis_birth_max_age_seconds,
        now=now,
        operation=operation,
    )
    if _sha256(acknowledged_release_sha256, "acknowledged release SHA-256") != verified["c2_replica_standby_release_sha256"]:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_ACK_MISMATCH", "operator acknowledgement does not match the release")
    claim_path = paths.root / _CLAIM_DIRECTORY[0] / _CLAIM_DIRECTORY[1] / f"{verified['c2_replica_standby_release_sha256']}.json"
    return {
        **verified,
        "execute_requested": False,
        "executor_implemented": True,
        "release_already_claimed": claim_path.exists(),
        "live_execution_authorized": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "remaining_blocker_codes": [
            "MOTHER_DEPLOY_C2_REPLICA_SYNC_NOT_AUTHORIZED",
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED",
        ],
        "resolved_blocker_codes": ["MOTHER_DEPLOY_C2_REPLICA_STANDBY_EXECUTOR_NOT_IMPLEMENTED"],
    }


def _claim_release(paths: PrivateStatePaths, *, release_sha256: str, release_path: Path, transaction_sha256: str, operation: OperationIdentity) -> Path:
    root = _ensure_directory(paths, _CLAIM_DIRECTORY, operation=operation)
    destination = root / f"{release_sha256}.json"
    document = {
        "kind": "main_computer.mother.deployment_c2_replica_standby_execution_claim.v1",
        "release": {"locator": _relative(paths, release_path, label="release"), "sha256": release_sha256},
        "transaction_sha256": transaction_sha256,
        "node": _C2_NODE,
        "claimed_at": _timestamp(path="claimed_at"),
    }
    payload = canonical_json(document)
    try:
        atomic_files.durable_create(destination, payload, operation=operation)
    except FileExistsError as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_RELEASE_ALREADY_CONSUMED", "this C2 replica standby release is already claimed") from exc
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    return opener.open(request, timeout=timeout) if hasattr(opener, "open") else opener(request, timeout=timeout)


def _http_json(controller: Any, method: str, endpoint: str, *, body: Mapping[str, Any] | None, timeout: float, max_response_bytes: int, opener: Any) -> dict[str, Any]:
    payload = canonical_json(dict(body)) if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {controller.api_token}",
        "User-Agent": "main-computer-mother-c2-replica-standby/1",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(controller.base_url + endpoint, data=payload, headers=headers, method=method)
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
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_REQUEST_FAILED", "Coolify request failed") from exc
    if len(raw) > max_response_bytes:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_RESPONSE_TOO_LARGE", "Coolify response is too large")
    try:
        payload_obj: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload_obj = raw.decode("utf-8", errors="replace")
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "payload": payload_obj,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


def _service_record(payload: Any, *, service_uuid: str) -> Mapping[str, Any]:
    matches = [item for item in _raw_items(payload) if _text(item, "uuid", "id") == service_uuid or _text(item, "name") == _C2_NODE]
    matches = [item for item in matches if _text(item, "name") in {"", _C2_NODE} or _text(item, "uuid", "id") == service_uuid]
    if len(matches) != 1:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_SERVICE_MISMATCH", "Coolify does not expose one exact C2 service")
    if _text(matches[0], "name") not in {"", _C2_NODE}:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_SERVICE_MISMATCH", "Coolify service name is not C2")
    return matches[0]


def _verify_identity_commitments(payload: Any, commitments: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = _raw_items(payload)
    results: list[dict[str, Any]] = []
    for key in sorted(_EXPECTED_IDENTITY_KEYS):
        expected = _mapping(commitments.get(key), f"identity commitment {key}")
        matches = [item for item in records if _env_key(item) == key]
        result = {
            "environment_key": key,
            "expected_environment_variable_uuid": expected.get("environment_variable_uuid"),
            "expected_value_sha256": expected.get("value_sha256"),
            "matches": len(matches),
            "uuid_verified": False,
            "commitment_verified": False,
        }
        if len(matches) != 1:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_PRECONDITION_FAILED", f"Coolify does not expose one installed {key}")
        item = matches[0]
        result["observed_environment_variable_uuid"] = _env_uuid(item)
        result["uuid_verified"] = result["observed_environment_variable_uuid"] == expected.get("environment_variable_uuid")
        visible = _visible_value(item)
        if visible is not None:
            result["observed_value_sha256"] = hashlib.sha256(visible.encode("utf-8")).hexdigest()
            result["commitment_verified"] = result["observed_value_sha256"] == expected.get("value_sha256")
        if result["uuid_verified"] is not True or result["commitment_verified"] is not True:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_IDENTITY_PRECONDITION_FAILED", f"Coolify identity commitment does not match for {key}")
        results.append(result)
    return results


def _compose_from_service_record(record: Mapping[str, Any]) -> str:
    for key in ("docker_compose_raw", "dockerComposeRaw", "docker_compose", "dockerCompose", "compose"):
        value = record.get(key)
        if type(value) is str and value:
            if "\n" in value or value.lstrip().startswith("name:") or value.lstrip().startswith("services:"):
                return value
            try:
                decoded = base64.b64decode(value, validate=True)
                return decoded.decode("utf-8")
            except Exception:
                return value
    raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_COMPOSE_MISSING", "Coolify service response does not expose Compose")


def _parse_compose_yaml(compose: str) -> Mapping[str, Any] | None:
    try:
        parsed = yaml.safe_load(compose)
    except yaml.YAMLError:
        return None
    if isinstance(parsed, Mapping):
        return parsed
    return None


def _compose_service(document: Mapping[str, Any] | None, service_name: str) -> Mapping[str, Any]:
    if not isinstance(document, Mapping):
        return {}
    services = document.get("services")
    if not isinstance(services, Mapping):
        return {}
    service = services.get(service_name)
    return service if isinstance(service, Mapping) else {}


def _compose_command_text(service: Mapping[str, Any]) -> str:
    command = service.get("command")
    if isinstance(command, list):
        return "\n".join(str(item) for item in command)
    if isinstance(command, str):
        return command
    return ""


def _compose_label_value(service: Mapping[str, Any], key: str) -> str | None:
    labels = service.get("labels")
    if isinstance(labels, Mapping):
        value = labels.get(key)
        return str(value) if value is not None else None
    if isinstance(labels, list):
        for item in labels:
            text = str(item)
            if text.startswith(f"{key}="):
                return text.split("=", 1)[1]
            if text.startswith(f"{key}:"):
                return text.split(":", 1)[1].strip()
    return None


def _compose_environment_value(service: Mapping[str, Any], key: str) -> str | None:
    environment = service.get("environment")
    if isinstance(environment, Mapping):
        value = environment.get(key)
        return str(value) if value is not None else None
    if isinstance(environment, list):
        for item in environment:
            text = str(item)
            if text.startswith(f"{key}="):
                return text.split("=", 1)[1]
    return None


def _compose_required_substring(source: str, prefix: str) -> str:
    for line in source.splitlines():
        text = line.strip().strip("'\"")
        if text.startswith("- "):
            text = text[2:].strip().strip("'\"")
        if text.startswith(prefix):
            return text
    return ""


def _compose_embedded_genesis_token(source: str) -> str:
    match = re.search(r"printf\s+'%s'\s+'([A-Za-z0-9+/=]+)'\s+\|\s+base64\s+-d\s+>\s+/config/genesis\.json", source)
    return match.group(1) if match else ""


def _compose_semantic_report(*, observed: str, expected: str, node: str) -> dict[str, Any]:
    """Return secret-free proof that Coolify's normalized Compose is the staged standby.

    Coolify rewrites YAML presentation details after a service PATCH: quote style,
    blank lines, rendered ``docker_compose`` helper variables, and service-volume
    prefixes can change even when ``docker_compose_raw`` is semantically the
    same standby definition.  The live verifier therefore accepts exact byte
    equality when present, but otherwise proves the required raw Compose
    structure and policy guards.
    """

    observed_sha = hashlib.sha256(observed.encode("utf-8")).hexdigest()
    expected_sha = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    exact_match = observed_sha == expected_sha
    observed_doc = _parse_compose_yaml(observed)
    expected_doc = _parse_compose_yaml(expected)
    yaml_equivalent = observed_doc == expected_doc and observed_doc is not None

    observed_init = _compose_service(observed_doc, "mother-replica-init")
    observed_replica = _compose_service(observed_doc, node)
    expected_bootnode = _compose_required_substring(expected, "--bootnodes=")
    observed_bootnode = _compose_required_substring(observed, "--bootnodes=")
    expected_network_id = _compose_required_substring(expected, "--network-id=")
    observed_network_id = _compose_required_substring(observed, "--network-id=")
    expected_genesis_token = _compose_embedded_genesis_token(expected)
    observed_genesis_token = _compose_embedded_genesis_token(observed)
    replica_command = _compose_command_text(observed_replica)
    init_command = _compose_command_text(observed_init)

    required_checks = {
        "has_init_service": bool(observed_init),
        "has_replica_service": bool(observed_replica),
        "init_image_expected": str(observed_init.get("image", "")) == _INIT_IMAGE,
        "replica_image_expected": str(observed_replica.get("image", "")) == _BESU_IMAGE,
        "init_restart_no": str(observed_init.get("restart", "")).lower() == "no",
        "replica_restart_no": str(observed_replica.get("restart", "")).lower() == "no",
        "validator_key_env_reference": _compose_environment_value(observed_init, "MC_MOTHER_VALIDATOR_PRIVATE_KEY") == "${MC_MOTHER_VALIDATOR_PRIVATE_KEY}",
        "writes_genesis_file": "/config/genesis.json" in init_command and "base64 -d" in init_command,
        "writes_nodekey_file": "/config/nodekey" in init_command and "MC_MOTHER_VALIDATOR_PRIVATE_KEY" in init_command,
        "embedded_genesis_matches": bool(expected_genesis_token) and observed_genesis_token == expected_genesis_token,
        "uses_genesis_file_arg": "--genesis-file=/config/genesis.json" in replica_command or "--genesis-file=/config/genesis.json" in observed,
        "uses_node_private_key_file": "--node-private-key-file=/config/nodekey" in replica_command or "--node-private-key-file=/config/nodekey" in observed,
        "network_id_matches": bool(expected_network_id) and observed_network_id == expected_network_id,
        "bootnode_matches": bool(expected_bootnode) and observed_bootnode == expected_bootnode,
        "p2p_port_enabled": "--p2p-port=30303" in replica_command or "--p2p-port=30303" in observed,
        "rpc_internal_only": "8545:8545" not in observed and "'8545:8545" not in observed and '"8545:8545' not in observed,
        "p2p_tcp_port_present": "30303:30303/tcp" in observed,
        "p2p_udp_port_present": "30303:30303/udp" in observed,
        "replica_sync_blocked_label": _compose_label_value(observed_replica, "main_computer.mother.replica-sync") == "blocked" or "main_computer.mother.replica-sync: blocked" in observed,
        "validator_activation_blocked_label": _compose_label_value(observed_replica, "main_computer.mother.validator-activation") == "blocked" or "main_computer.mother.validator-activation: blocked" in observed,
        "stage_label": _compose_label_value(observed_replica, "main_computer.mother.stage") == "c2-replica-standby" or "main_computer.mother.stage: c2-replica-standby" in observed,
        "node_label": _compose_label_value(observed_replica, "main_computer.mother.node") == node or f"main_computer.mother.node: {node}" in observed,
        "old_alpine_tail_absent": "tail -f /dev/null" not in observed,
    }

    missing = [key for key, passed in required_checks.items() if passed is not True]
    semantic_verified = exact_match or yaml_equivalent or not missing
    return {
        "expected_compose_sha256": expected_sha,
        "observed_compose_sha256": observed_sha,
        "exact_match": exact_match,
        "yaml_equivalent": yaml_equivalent,
        "coolify_normalized_compose_accepted": semantic_verified and not exact_match,
        "standby_compose_verified": semantic_verified,
        "missing_required_checks": missing,
        "required_checks": required_checks,
    }


def _write_result(paths: PrivateStatePaths, result: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(result)
    if document.get("kind") != _EXECUTION_KIND or _contains_private_key_literal(document):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_EXECUTION_INVALID", "execution result is malformed or contains a secret")
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, _EXECUTION_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "c2replicastandby"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_EXECUTION_CONFLICT", "execution result destination contains different bytes")
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_c2_replica_standby_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    c2_state_extension_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    genesis_birth_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_c2_replica_standby_release(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        c2_state_extension_max_age_seconds=c2_state_extension_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        genesis_birth_max_age_seconds=genesis_birth_max_age_seconds,
        now=now,
        operation=operation,
    )
    if inspected["release_already_claimed"]:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_RELEASE_ALREADY_CONSUMED", "this C2 replica standby release is already claimed")
    release_doc, _, _ = _canonical_file(Path(inspected["release_path"]), label="C2 replica standby release")
    tx_path = _resolve_locator(paths, release_doc["transaction"]["locator"], label="C2 replica standby transaction")
    tx, _, _ = _canonical_file(tx_path, label="C2 replica standby transaction")
    mutation = _mapping(tx.get("mutation"), "transaction mutation")
    service_uuid = inspected["service_uuid"]
    claim = _claim_release(
        paths,
        release_sha256=inspected["c2_replica_standby_release_sha256"],
        release_path=Path(inspected["release_path"]),
        transaction_sha256=inspected["c2_replica_standby_transaction_sha256"],
        operation=operation,
    )
    controller = resolve_coolify_controller(private_state, inspected["network"], _C2_CONTROLLER)
    started_at = _timestamp(path="started_at")
    preconditions: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    try:
        service_endpoint = f"/api/v1/services/{quote(service_uuid, safe='')}"
        service = _http_json(controller, "GET", service_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        preconditions.append({
            "name": "c2-standby-service-present",
            "controller_id": _C2_CONTROLLER,
            "method": "GET",
            "endpoint": service_endpoint,
            "status": service["status"],
            "response_sha256": service["response_sha256"],
            "verified": False,
        })
        if not service["ok"]:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_PRECONDITION_FAILED", f"Coolify C service GET failed with HTTP {service['status']}")
        _service_record(service["payload"], service_uuid=service_uuid)
        preconditions[-1]["verified"] = True

        env_endpoint = f"/api/v1/services/{quote(service_uuid, safe='')}/envs"
        envs = _http_json(controller, "GET", env_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        preconditions.append({
            "name": "c2-identity-commitments",
            "controller_id": _C2_CONTROLLER,
            "method": "GET",
            "endpoint": env_endpoint,
            "status": envs["status"],
            "response_sha256": envs["response_sha256"],
            "verified": False,
        })
        if not envs["ok"]:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_PRECONDITION_FAILED", f"Coolify C env GET failed with HTTP {envs['status']}")
        identity_results = _verify_identity_commitments(envs["payload"], tx["replica"]["identity_commitments"])
        preconditions[-1]["verified"] = True
        preconditions[-1]["verified_identity_key_count"] = len(identity_results)

        body = _mapping(mutation.get("canonical_request_body"), "mutation body")
        if hashlib.sha256(canonical_json(dict(body))).hexdigest() != mutation.get("body_sha256"):
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_TRANSACTION_INVALID", "mutation body commitment changed")
        mutation_endpoint = mutation.get("endpoint")
        if type(mutation_endpoint) is not str or not mutation_endpoint.startswith("/api/v1/services/"):
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_TRANSACTION_INVALID", "mutation endpoint is invalid")
        response = _http_json(controller, "PATCH", mutation_endpoint, body=body, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        ok = response["status"] in list(mutation.get("success_statuses") or [])
        receipts.append({
            "ordinal": 1,
            "mutation_id": mutation["mutation_id"],
            "node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "service_uuid": service_uuid,
            "method": "PATCH",
            "endpoint": mutation["endpoint"],
            "body_sha256": mutation["body_sha256"],
            "response": {
                "status": response["status"],
                "ok": ok,
                "response_sha256": response["response_sha256"],
                "byte_length": response["byte_length"],
                "elapsed_ms": response["elapsed_ms"],
            },
            "status": "succeeded" if ok else "failed",
            "live_write_acknowledged": ok,
        })
        if not ok:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_MUTATION_FAILED", f"Coolify C rejected the standby Compose PATCH with HTTP {response['status']}")
    except MotherDeploymentC2ReplicaStandbyError as exc:
        failure = {"code": exc.code, "message": str(exc)}
    except Exception as exc:  # pragma: no cover
        failure = {"code": "MOTHER_DEPLOY_C2_REPLICA_STANDBY_UNEXPECTED_FAILURE", "message": str(exc)}

    completed_at = _timestamp(path="completed_at")
    complete = failure is None and len(receipts) == 1 and receipts[0].get("status") == "succeeded"
    result: dict[str, Any] = {
        "kind": _EXECUTION_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "pass" if complete else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "node": _C2_NODE,
        "nodes": [_C2_NODE],
        "controller_id": _C2_CONTROLLER,
        "service_uuid": service_uuid,
        "staged_scope": inspected["staged_scope"],
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="release"), "sha256": inspected["c2_replica_standby_release_sha256"]},
        "transaction": {"locator": _relative(paths, tx_path, label="transaction"), "sha256": inspected["c2_replica_standby_transaction_sha256"]},
        "execution_claim": {"locator": _relative(paths, claim, label="execution claim")},
        "genesis_sha256": tx["replica"]["genesis_sha256"],
        "compose_sha256": tx["replica"]["compose"]["sha256"],
        "authority": {
            "authorization_source": "explicit-operator-release",
            "release_consumed": True,
            "configuration_apply_authorized": True,
            "replica_start_authorized": False,
            "replica_sync_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "initial_node_read_only": True,
            "replica_node_only": True,
            "service_deploy_or_start_performed": False,
            "replica_sync_performed": False,
            "chain_mutation_performed": False,
            "routing_or_topology_published": False,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
        },
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "failure": failure,
        "service_mutation_count": 1 if complete else 0,
        "identity_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "summary": {
            "planned_mutation_count": 1,
            "attempted_mutation_count": len(receipts),
            "succeeded_mutation_count": sum(item.get("status") == "succeeded" for item in receipts),
            "failed_mutation_count": sum(item.get("status") != "succeeded" for item in receipts),
            "complete": complete,
            "live_mutation_performed": any(item.get("live_write_acknowledged") is True for item in receipts),
            "network_access_performed": bool(preconditions or receipts),
            "standby_compose_patch_succeeded": complete,
            "service_deploy_or_start_performed": False,
            "replica_sync_performed": False,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "next_phase": "verify-c2-replica-standby" if complete else "manual-review-required",
        },
        "next_phase": "verify-c2-replica-standby" if complete else "manual-review-required",
    }
    result_path, result_sha = _write_result(paths, result, operation=operation)
    result["result_artifact"] = {"path": str(result_path), "sha256": result_sha}
    return result


def _write_evidence(paths: PrivateStatePaths, evidence: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(evidence)
    if document.get("kind") != _EVIDENCE_KIND or _contains_private_key_literal(document):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_EVIDENCE_INVALID", "verification evidence is malformed or contains a private key")
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, _EVIDENCE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("observed_at", "")))[:32] or "c2replicastandby"
    destination = root / f"{stamp}-{document.get('network', 'network')}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_EVIDENCE_CONFLICT", "verification evidence destination contains different bytes")
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_c2_replica_standby(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    observed_at: str | None = None,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    write_evidence: bool = False,
    operation: OperationIdentity,
) -> dict[str, Any]:
    candidate = _beneath(paths, Path(execution_path), _EXECUTION_DIRECTORY, label="C2 replica standby execution")
    execution, _, execution_sha = _canonical_file(candidate, label="C2 replica standby execution")
    if (
        execution.get("kind") != _EXECUTION_KIND
        or execution.get("status") != "pass"
        or execution.get("mother_binding") != _binding(private_state)
        or execution.get("node") != _C2_NODE
        or execution.get("controller_id") != _C2_CONTROLLER
        or execution.get("policy", {}).get("service_deploy_or_start_performed") is not False
        or execution.get("policy", {}).get("replica_sync_performed") is not False
        or execution.get("validator_vote_performed") is not False
        or _contains_private_key_literal(execution)
    ):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_EXECUTION_INVALID", "C2 replica standby execution is not a clean scoped success")
    transaction_locator = _mapping(execution.get("transaction"), "execution.transaction").get("locator")
    tx_path = _resolve_locator(paths, transaction_locator, label="C2 replica standby transaction")
    tx, _, _ = _canonical_file(tx_path, label="C2 replica standby transaction")
    if tx.get("c2_replica_standby_transaction_sha256") != execution.get("transaction", {}).get("sha256"):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_EXECUTION_INVALID", "execution transaction binding is invalid")
    expected_compose = _mapping(_mapping(tx.get("replica"), "transaction.replica").get("compose"), "transaction.replica.compose").get("canonical_text")
    if type(expected_compose) is not str or not expected_compose:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_EXECUTION_INVALID", "execution transaction lacks canonical Compose")
    if hashlib.sha256(expected_compose.encode("utf-8")).hexdigest() != execution.get("compose_sha256"):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_STANDBY_EXECUTION_INVALID", "execution Compose commitment does not match transaction")

    controller = resolve_coolify_controller(private_state, execution["network"], _C2_CONTROLLER)
    service_uuid = _identifier(execution.get("service_uuid"), "service UUID")
    endpoint = f"/api/v1/services/{quote(service_uuid, safe='')}"
    observation = _http_json(controller, "GET", endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
    blockers: list[dict[str, str]] = []
    compose_sha: str | None = None
    compose_report: dict[str, Any] | None = None
    service_verified = False
    if not observation["ok"]:
        blockers.append({"code": "MOTHER_DEPLOY_C2_REPLICA_STANDBY_SERVICE_GET_FAILED", "message": f"Coolify C service GET returned HTTP {observation['status']}"})
    else:
        try:
            record = _service_record(observation["payload"], service_uuid=service_uuid)
            service_verified = True
            compose = _compose_from_service_record(record)
            compose_report = _compose_semantic_report(observed=compose, expected=expected_compose, node=_C2_NODE)
            compose_sha = compose_report["observed_compose_sha256"]
            if compose_report["standby_compose_verified"] is not True:
                blockers.append({"code": "MOTHER_DEPLOY_C2_REPLICA_STANDBY_COMPOSE_MISMATCH", "message": "live Coolify Compose does not match the required standby semantics"})
        except MotherDeploymentC2ReplicaStandbyError as exc:
            blockers.append({"code": exc.code, "message": str(exc)})
    clean = not blockers and service_verified and bool(compose_report and compose_report.get("standby_compose_verified") is True)

    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "observed_at": _timestamp(observed_at, path="observed_at"),
        "network": execution["network"],
        "node": _C2_NODE,
        "nodes": [_C2_NODE],
        "controller_id": _C2_CONTROLLER,
        "service_uuid": service_uuid,
        "mother_binding": dict(execution["mother_binding"]),
        "execution": {
            "locator": _relative(paths, candidate, label="execution"),
            "sha256": execution_sha,
            "completed_at": execution.get("completed_at"),
        },
        "endpoint": {
            "method": "GET",
            "path": endpoint,
            "status": observation["status"],
            "ok": observation["ok"],
            "response_sha256": observation["response_sha256"],
            "byte_length": observation["byte_length"],
            "elapsed_ms": observation["elapsed_ms"],
        },
        "genesis_sha256": execution.get("genesis_sha256"),
        "expected_compose_sha256": execution.get("compose_sha256"),
        "observed_compose_sha256": compose_sha,
        "compose_verification": compose_report or {},
        "blockers": blockers,
        "policy": {
            "allowed_http_method": "GET",
            "live_mutation_performed": False,
            "network_access_performed": True,
            "service_deploy_or_start_performed": False,
            "replica_sync_performed": False,
            "chain_mutation_performed": False,
            "routing_or_topology_published": False,
            "private_state_updated": False,
            "secrets_in_output": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
        },
        "service_mutation_count": 0,
        "identity_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "summary": {
            "clean": clean,
            "blocker_count": len(blockers),
            "blocker_codes": [item["code"] for item in blockers],
            "verified_service_count": 1 if service_verified else 0,
            "standby_compose_verified": bool(compose_report and compose_report.get("standby_compose_verified") is True),
            "standby_compose_exact_match": bool(compose_report and compose_report.get("exact_match") is True),
            "standby_compose_yaml_equivalent": bool(compose_report and compose_report.get("yaml_equivalent") is True),
            "coolify_normalized_compose_accepted": bool(compose_report and compose_report.get("coolify_normalized_compose_accepted") is True),
            "genesis_config_present": bool(compose_report and compose_report.get("required_checks", {}).get("uses_genesis_file_arg") is True),
            "replica_sync_performed": False,
            "service_deploy_or_start_performed": False,
            "chain_mutation_count": 0,
            "service_mutation_count": 0,
            "identity_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_performed": False,
            "next_phase": "stage-c2-replica-sync" if clean else "manual-review-required",
        },
        "next_phase": "stage-c2-replica-sync" if clean else "manual-review-required",
    }
    if write_evidence:
        path, digest = _write_evidence(paths, evidence, operation=operation)
        evidence = {**evidence, "evidence": {"path": str(path), "sha256": digest}}
    return evidence


__all__ = [
    "MotherDeploymentC2ReplicaStandbyError",
    "stage_c2_replica_standby_transaction",
    "write_c2_replica_standby_transaction",
    "verify_c2_replica_standby_transaction",
    "build_c2_replica_standby_release",
    "write_c2_replica_standby_release",
    "verify_c2_replica_standby_release",
    "inspect_c2_replica_standby_release",
    "execute_c2_replica_standby_release",
    "verify_c2_replica_standby",
]
