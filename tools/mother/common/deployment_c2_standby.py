"""C2 standby service and reserved-identity installation wrappers.

This module intentionally composes the existing, battle-tested standby-service
and identity-install lifecycles instead of inventing a second executor.  The
wrappers add the post-T2 C2 evidence gate, force the canonical
``mainnetc-super2`` target, and keep the boundary explicit: service creation and
identity environment installation only.  No replica synchronization, genesis
rewrite, QBFT vote, validator restart, or chain mutation is authorized here.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import CoolifyObservationError, get_coolify_json, resolve_coolify_controller
from .deployment_c2_state_extension import verify_c2_state_extension_evidence
from .deployment_execution import (
    build_deployment_execution_request,
    verify_deployment_execution_request,
    write_deployment_execution_request,
)
from .deployment_transaction import (
    build_deployment_mutation_transaction,
    verify_deployment_mutation_transaction,
    write_deployment_mutation_transaction,
)
from .deployment_release import (
    build_deployment_mutation_release,
    verify_deployment_mutation_release,
    write_deployment_mutation_release,
)
from .deployment_executor import inspect_released_mutation, execute_released_mutation
from .deployment_standby import (
    run_deployment_standby_verification,
    verify_deployment_standby_evidence,
    write_deployment_standby_verification,
)
from .deployment_identity_install import (
    build_deployment_identity_install_transaction,
    verify_deployment_identity_install_transaction,
    write_deployment_identity_install_transaction,
)
from .deployment_identity_release import (
    build_deployment_identity_release,
    verify_deployment_identity_release,
    write_deployment_identity_release,
)
from .deployment_identity_executor import inspect_released_identity, execute_released_identity
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_C2_NODE = "mainnetc-super2"
_C2_CONTROLLER = "coolify-c"


_IDENTITY_VERIFICATION_KIND = "main_computer.mother.deployment_c2_standby_identity_verification.v1"
_IDENTITY_EXECUTION_KIND = "main_computer.mother.deployment_identity_execution_result.v1"
_IDENTITY_EXECUTION_DIRECTORY = ("actions", "deployment-identity-executions")
_IDENTITY_EVIDENCE_DIRECTORY = ("evidence", "deployment-c2-standby-identity")
_C2_IDENTITY_KEYS = {
    "MC_MOTHER_VALIDATOR_PRIVATE_KEY",
    "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY",
}
_PRIVATE_KEY_RE = re.compile(r"0x[0-9a-fA-F]{64}\Z")





def _utc_timestamp(value: str | None = None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", "observed_at is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", "observed_at must be UTC")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", f"{label} could not be read as canonical JSON") from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", f"{label} is not canonical JSON")
    return value, raw, hashlib.sha256(raw).hexdigest()


def _beneath(paths: PrivateStatePaths, path: Path, parts: tuple[str, str], *, label: str) -> Path:
    root = (paths.root / parts[0] / parts[1]).resolve(strict=False)
    candidate = Path(path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_PATH_UNSAFE", f"{label} is outside the canonical Mother directory") from exc
    return candidate


def _raw_items(payload: Any, keys: tuple[str, ...]) -> list[Mapping[str, Any]]:
    raw: list[Any] = []
    if type(payload) is list:
        raw = payload
    elif isinstance(payload, Mapping):
        for key in (*keys, "data"):
            if type(payload.get(key)) is list:
                raw = payload[key]
                break
        else:
            if any(key in payload for key in ("uuid", "id", "key", "name")):
                raw = [payload]
    if len(raw) > 1000:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_TOO_MANY_ITEMS", "Coolify returned too many environment rows")
    return [item for item in raw if isinstance(item, Mapping)]


def _env_key(item: Mapping[str, Any]) -> str | None:
    for key in ("key", "name"):
        value = item.get(key)
        if type(value) is str and value:
            return value
    return None


def _env_uuid(item: Mapping[str, Any]) -> str | None:
    for key in ("uuid", "id"):
        value = item.get(key)
        if type(value) in {str, int} and str(value):
            return str(value)
    return None


def _visible_value(item: Mapping[str, Any]) -> str | None:
    for key in ("value", "real_value"):
        value = item.get(key)
        if type(value) is str:
            return value
    value = item.get("environment_variable")
    if isinstance(value, Mapping):
        return _visible_value(value)
    return None


def _identity_evidence_secret_free(value: Any) -> bool:
    if isinstance(value, Mapping):
        return all(_identity_evidence_secret_free(item) for item in value.values())
    if isinstance(value, list):
        return all(_identity_evidence_secret_free(item) for item in value)
    if type(value) is str:
        if _PRIVATE_KEY_RE.fullmatch(value) or value.lower().startswith("bearer "):
            return False
    return True


def _write_c2_standby_identity_verification(
    paths: PrivateStatePaths,
    verification: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(verification)
    if document.get("kind") != _IDENTITY_VERIFICATION_KIND or not _identity_evidence_secret_free(document):
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_EVIDENCE_INVALID", "identity verification evidence is malformed or contains a secret value")
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    root = paths.root / _IDENTITY_EVIDENCE_DIRECTORY[0] / _IDENTITY_EVIDENCE_DIRECTORY[1]
    try:
        root.resolve(strict=False).relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_PATH_UNSAFE", "identity evidence root escapes Mother state") from exc
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("observed_at", "")))[:32] or "identity"
    destination = root / f"{stamp}-{document.get('network', 'network')}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_EVIDENCE_CONFLICT", "identity evidence destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


class MotherDeploymentC2StandbyError(RuntimeError):
    """C2 standby lifecycle failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentC2StandbyError:
    return MotherDeploymentC2StandbyError(code, message)


def _nodes(selected_nodes: Iterable[str] = ()) -> tuple[str, ...]:
    values = tuple(str(item) for item in selected_nodes)
    if values and values != (_C2_NODE,):
        raise _fail(
            "MOTHER_DEPLOY_C2_STANDBY_WRONG_NODE",
            "C2 standby operations may target only mainnetc-super2",
        )
    return (_C2_NODE,)


def _c2_gate(
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
        or verified.get("next_phase") != "preflight-c2-standby"
        or verified.get("service_mutation_count") != 0
        or verified.get("chain_mutation_count") != 0
        or verified.get("validator_mutation_count") != 0
        or verified.get("validator_restart_count") != 0
        or verified.get("validator_vote_performed") is not False
    ):
        raise _fail(
            "MOTHER_DEPLOY_C2_STANDBY_C2_EXTENSION_REQUIRED",
            "a clean C2 state-extension evidence artifact is required",
        )
    return verified


def _decorate_service(payload: Mapping[str, Any], gate: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["c2_state_extension_evidence"] = {
        "path": gate.get("evidence_path"),
        "sha256": gate.get("evidence_sha256"),
        "file_sha256": gate.get("evidence_file_sha256"),
    }
    result["c2_standby_scope"] = {
        "node": _C2_NODE,
        "controller_id": _C2_CONTROLLER,
        "mode": "soft",
        "service_mutation_only": True,
        "identity_install_authorized": False,
        "replica_sync_authorized": False,
        "validator_vote_authorized": False,
        "validator_restart_authorized": False,
        "chain_mutation_authorized": False,
    }
    return result


def _decorate_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["c2_identity_scope"] = {
        "node": _C2_NODE,
        "controller_id": _C2_CONTROLLER,
        "mode": "soft",
        "service_env_mutation_only": True,
        "service_create_authorized": False,
        "replica_sync_authorized": False,
        "validator_vote_authorized": False,
        "validator_restart_authorized": False,
        "chain_mutation_authorized": False,
    }
    return result


def stage_c2_standby_service_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    preflight_evidence_path: Path,
    c2_state_extension_evidence_path: Path,
    *,
    network: str = "mainnet",
    preflight_max_age_seconds: int = 300,
    c2_state_extension_max_age_seconds: int = 86400,
    created_at: str | None = None,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    """Persist the generic request and C2-only service-creation transaction."""

    gate = _c2_gate(
        paths,
        private_state,
        Path(c2_state_extension_evidence_path),
        max_age_seconds=c2_state_extension_max_age_seconds,
        now=now,
        operation=operation,
    )
    request = build_deployment_execution_request(
        paths,
        private_state,
        Path(preflight_evidence_path),
        network=network,
        selected_nodes=(_C2_NODE,),
        max_age_seconds=preflight_max_age_seconds,
        created_at=created_at,
        now=now,
    )
    request_path, request_sha = write_deployment_execution_request(
        paths,
        request,
        operation=operation,
    )
    transaction = build_deployment_mutation_transaction(
        paths,
        private_state,
        request_path,
        selected_nodes=(_C2_NODE,),
        max_age_seconds=preflight_max_age_seconds,
        created_at=created_at,
        now=now,
    )
    transaction_path, transaction_sha = write_deployment_mutation_transaction(
        paths,
        transaction,
        operation=operation,
    )
    return _decorate_service(
        {
            **transaction,
            "request_artifact": {"path": str(request_path), "sha256": request_sha},
            "transaction_artifact": {"path": str(transaction_path), "sha256": transaction_sha},
            "c2_summary": {
                "next_phase": "verify-c2-standby-service-transaction",
                "service_mutation_count": transaction.get("summary", {}).get("mutation_count"),
                "identity_mutation_count": 0,
                "chain_mutation_count": 0,
                "validator_mutation_count": 0,
                "validator_restart_count": 0,
                "validator_vote_performed": False,
            },
        },
        gate,
    )


def verify_c2_standby_service_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    c2_state_extension_evidence_path: Path,
    *,
    max_age_seconds: int = 300,
    c2_state_extension_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    gate = _c2_gate(
        paths,
        private_state,
        Path(c2_state_extension_evidence_path),
        max_age_seconds=c2_state_extension_max_age_seconds,
        now=now,
        operation=operation,
    )
    verified = verify_deployment_mutation_transaction(
        paths,
        private_state,
        Path(transaction_path),
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if verified.get("mutation_count") not in {1, 2}:
        raise _fail(
            "MOTHER_DEPLOY_C2_STANDBY_TRANSACTION_INVALID",
            "C2 standby service preparation must contain one service mutation and at most one environment mutation",
        )
    return _decorate_service(
        {
            **verified,
            "node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "service_mutation_count": verified.get("mutation_count"),
            "identity_mutation_count": 0,
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_performed": False,
            "next_phase": "c2-standby-service-release-not-yet-authorized",
        },
        gate,
    )


def build_c2_standby_service_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    c2_state_extension_evidence_path: Path,
    *,
    acknowledged_transaction_sha256: str,
    max_age_seconds: int = 300,
    c2_state_extension_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    gate = _c2_gate(
        paths,
        private_state,
        Path(c2_state_extension_evidence_path),
        max_age_seconds=c2_state_extension_max_age_seconds,
        now=now,
        operation=operation,
    )
    release = build_deployment_mutation_release(
        paths,
        private_state,
        Path(transaction_path),
        acknowledged_transaction_sha256=acknowledged_transaction_sha256,
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        expires_in_seconds=expires_in_seconds,
        created_at=created_at,
        now=now,
    )
    return _decorate_service(
        {
            **release,
            "c2_summary": {
                "next_phase": "verify-c2-standby-service-release",
                "service_mutation_count": release.get("summary", {}).get("mutation_count"),
                "identity_mutation_count": 0,
                "chain_mutation_count": 0,
                "validator_mutation_count": 0,
                "validator_restart_count": 0,
                "validator_vote_authorized": False,
            },
        },
        gate,
    )


def write_c2_standby_service_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    payload = dict(release)
    payload.pop("c2_state_extension_evidence", None)
    payload.pop("c2_standby_scope", None)
    payload.pop("c2_summary", None)
    return write_deployment_mutation_release(paths, payload, operation=operation)


def verify_c2_standby_service_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    c2_state_extension_evidence_path: Path,
    *,
    max_age_seconds: int = 300,
    c2_state_extension_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    gate = _c2_gate(
        paths,
        private_state,
        Path(c2_state_extension_evidence_path),
        max_age_seconds=c2_state_extension_max_age_seconds,
        now=now,
        operation=operation,
    )
    verified = verify_deployment_mutation_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if verified.get("mutation_count") not in {1, 2}:
        raise _fail(
            "MOTHER_DEPLOY_C2_STANDBY_RELEASE_INVALID",
            "C2 standby service release must authorize one service mutation and at most one environment mutation",
        )
    return _decorate_service(
        {
            **verified,
            "node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "service_mutation_count": verified.get("mutation_count"),
            "identity_mutation_count": 0,
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_authorized": False,
            "next_phase": "apply-c2-standby-service",
        },
        gate,
    )


def inspect_c2_standby_service_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    c2_state_extension_evidence_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 300,
    c2_state_extension_max_age_seconds: int = 86400,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    gate = _c2_gate(
        paths,
        private_state,
        Path(c2_state_extension_evidence_path),
        max_age_seconds=c2_state_extension_max_age_seconds,
        now=now,
        operation=operation,
    )
    inspected = inspect_released_mutation(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        now=now,
    )
    return _decorate_service(
        {
            **inspected,
            "node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "service_mutation_count": inspected.get("mutation_count"),
            "identity_mutation_count": 0,
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_authorized": False,
            "validator_vote_performed": False,
            "execute_requested": False,
        },
        gate,
    )


def execute_c2_standby_service_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    c2_state_extension_evidence_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 300,
    c2_state_extension_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int,
    opener: Any,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    gate = _c2_gate(
        paths,
        private_state,
        Path(c2_state_extension_evidence_path),
        max_age_seconds=c2_state_extension_max_age_seconds,
        now=now,
        operation=operation,
    )
    result = execute_released_mutation(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        operation=operation,
    )
    return _decorate_service(
        {
            **result,
            "node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "service_mutation_count": result.get("summary", {}).get("planned_mutation_count"),
            "identity_mutation_count": 0,
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_performed": False,
            "next_phase": (
                "verify-c2-standby-service"
                if result.get("status") == "pass"
                else "manual-review-required"
            ),
        },
        gate,
    )


def verify_c2_standby_service(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    network: str = "mainnet",
    max_age_seconds: int = 300,
    observed_at: str | None = None,
    timeout: float = 30.0,
    max_response_bytes: int,
    opener: Any,
    write_evidence: bool,
    operation: OperationIdentity,
) -> dict[str, Any]:
    result = run_deployment_standby_verification(
        paths,
        private_state,
        Path(execution_path),
        network=network,
        selected_nodes=(_C2_NODE,),
        observed_at=observed_at,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if result.get("summary", {}).get("clean") is not True:
        result = {
            **result,
            "next_phase": "manual-review-required",
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_performed": False,
        }
        return result
    if write_evidence:
        path, digest = write_deployment_standby_verification(
            paths,
            result,
            operation=operation,
        )
        result = {**result, "evidence": {"path": str(path), "sha256": digest}}
    return {
        **result,
        "node": _C2_NODE,
        "controller_id": _C2_CONTROLLER,
        "service_mutation_count": 0,
        "identity_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "next_phase": "stage-c2-standby-identity",
    }


def stage_c2_standby_identity_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    standby_evidence_path: Path,
    *,
    max_age_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    verified_standby = verify_deployment_standby_evidence(
        paths,
        private_state,
        Path(standby_evidence_path),
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if verified_standby.get("clean") is not True:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_EVIDENCE_REQUIRED", "clean C2 standby evidence is required")
    transaction = build_deployment_identity_install_transaction(
        paths,
        private_state,
        Path(standby_evidence_path),
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        created_at=created_at,
        now=now,
    )
    transaction_path, transaction_sha = write_deployment_identity_install_transaction(
        paths,
        transaction,
        operation=operation,
    )
    return _decorate_identity(
        {
            **transaction,
            "transaction_artifact": {"path": str(transaction_path), "sha256": transaction_sha},
            "standby_evidence_verification": dict(verified_standby),
            "c2_summary": {
                "next_phase": "verify-c2-standby-identity-transaction",
                "service_mutation_count": 0,
                "identity_mutation_count": transaction.get("summary", {}).get("mutation_count"),
                "chain_mutation_count": 0,
                "validator_mutation_count": 0,
                "validator_restart_count": 0,
                "validator_vote_performed": False,
            },
        }
    )


def verify_c2_standby_identity_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    verified = verify_deployment_identity_install_transaction(
        paths,
        private_state,
        Path(transaction_path),
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if verified.get("mutation_count") != 2:
        raise _fail(
            "MOTHER_DEPLOY_C2_STANDBY_IDENTITY_TRANSACTION_INVALID",
            "C2 identity installation must contain exactly two service env mutations",
        )
    return _decorate_identity(
        {
            **verified,
            "node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "service_mutation_count": 0,
            "identity_mutation_count": verified.get("mutation_count"),
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_performed": False,
            "next_phase": "c2-standby-identity-release-not-yet-authorized",
        }
    )


def build_c2_standby_identity_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_identity_transaction_sha256: str,
    max_age_seconds: int = 300,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    release = build_deployment_identity_release(
        paths,
        private_state,
        Path(transaction_path),
        acknowledged_identity_transaction_sha256=acknowledged_identity_transaction_sha256,
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        expires_in_seconds=expires_in_seconds,
        created_at=created_at,
        now=now,
    )
    return _decorate_identity(
        {
            **release,
            "c2_summary": {
                "next_phase": "verify-c2-standby-identity-release",
                "service_mutation_count": 0,
                "identity_mutation_count": release.get("summary", {}).get("mutation_count"),
                "chain_mutation_count": 0,
                "validator_mutation_count": 0,
                "validator_restart_count": 0,
                "validator_vote_authorized": False,
            },
        }
    )


def write_c2_standby_identity_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    payload = dict(release)
    payload.pop("c2_identity_scope", None)
    payload.pop("c2_summary", None)
    return write_deployment_identity_release(paths, payload, operation=operation)


def verify_c2_standby_identity_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    verified = verify_deployment_identity_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if verified.get("mutation_count") != 2:
        raise _fail(
            "MOTHER_DEPLOY_C2_STANDBY_IDENTITY_RELEASE_INVALID",
            "C2 identity release must authorize exactly two service env mutations",
        )
    return _decorate_identity(
        {
            **verified,
            "node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "service_mutation_count": 0,
            "identity_mutation_count": verified.get("mutation_count"),
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_authorized": False,
            "next_phase": "apply-c2-standby-identity",
        }
    )


def inspect_c2_standby_identity_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    inspected = inspect_released_identity(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        now=now,
    )
    return _decorate_identity(
        {
            **inspected,
            "node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "service_mutation_count": 0,
            "identity_mutation_count": inspected.get("mutation_count"),
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_authorized": False,
            "validator_vote_performed": False,
            "execute_requested": False,
        }
    )


def execute_c2_standby_identity_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 300,
    timeout: float = 30.0,
    max_response_bytes: int,
    opener: Any,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    result = execute_released_identity(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=(_C2_NODE,),
        max_age_seconds=max_age_seconds,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        operation=operation,
    )
    return _decorate_identity(
        {
            **result,
            "node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "service_mutation_count": 0,
            "identity_mutation_count": result.get("summary", {}).get("planned_mutation_count"),
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_performed": False,
            "next_phase": (
                "c2-standby-identity-installed"
                if result.get("status") == "pass"
                else "manual-review-required"
            ),
        }
    )


def verify_c2_standby_identity(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    network: str = "mainnet",
    observed_at: str | None = None,
    timeout: float = 30.0,
    max_response_bytes: int,
    opener: Any,
    write_evidence: bool,
    operation: OperationIdentity,
) -> dict[str, Any]:
    execution_candidate = _beneath(
        paths,
        Path(execution_path),
        _IDENTITY_EXECUTION_DIRECTORY,
        label="identity execution",
    )
    execution, execution_raw, execution_file_sha256 = _canonical_file(
        execution_candidate,
        label="identity execution",
    )
    if execution.get("kind") != _IDENTITY_EXECUTION_KIND or execution.get("schema_version") != 1:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", "identity execution has the wrong kind")
    if execution.get("status") != "pass":
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_FAILED", "identity execution did not pass")
    execution_nodes = execution.get("nodes")
    if execution.get("network") != network or execution_nodes != [_C2_NODE]:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_TARGET_MISMATCH", "identity execution is not bound to the C2 standby target")
    if execution.get("node") not in {None, _C2_NODE} or execution.get("controller_id") not in {None, _C2_CONTROLLER}:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_TARGET_MISMATCH", "identity execution is not bound to the C2 standby target")
    if execution.get("service_mutation_count", 0) not in {0, None} or execution.get("chain_mutation_count", 0) not in {0, None}:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_SCOPE_ESCALATED", "identity execution escaped the C2 identity-only scope")
    if execution.get("validator_mutation_count", 0) not in {0, None} or execution.get("validator_restart_count", 0) not in {0, None} or execution.get("validator_vote_performed", False) is not False:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_VALIDATOR_SCOPE_ESCALATED", "identity execution touched validator authority")
    if not _identity_evidence_secret_free(execution):
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_SECRET_LEAK", "identity execution contains a raw secret value")

    receipts = execution.get("mutation_receipts")
    if type(receipts) is not list or len(receipts) != 2:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", "identity execution must contain exactly two mutation receipts")

    expected: dict[str, dict[str, Any]] = {}
    endpoints: set[str] = set()
    for raw_receipt in receipts:
        if not isinstance(raw_receipt, Mapping):
            raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", "identity receipt is malformed")
        receipt = dict(raw_receipt)
        key = receipt.get("environment_key")
        if key not in _C2_IDENTITY_KEYS:
            raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", "identity receipt contains an unexpected environment key")
        if key in expected:
            raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", "identity receipt duplicates an environment key")
        if receipt.get("status") != "succeeded" or receipt.get("live_write_acknowledged") is not True:
            raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INCOMPLETE", "identity receipt was not a successful live write")
        postcondition = receipt.get("postcondition")
        if not isinstance(postcondition, Mapping) or postcondition.get("commitment_verified") is not True:
            raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_COMMITMENT_UNPROVEN", "identity receipt did not prove the value commitment")
        value_sha256 = receipt.get("value_sha256")
        if type(value_sha256) is not str or len(value_sha256) != 64:
            raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", "identity receipt value commitment is missing")
        env_uuid = receipt.get("environment_variable_uuid")
        if type(env_uuid) not in {str, int} or not str(env_uuid):
            raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", "identity receipt environment UUID is missing")
        endpoint = receipt.get("endpoint")
        if type(endpoint) is not str or not endpoint.startswith("/api/v1/services/") or not endpoint.endswith("/envs"):
            raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", "identity receipt endpoint is unsupported")
        endpoints.add(endpoint)
        expected[str(key)] = {
            "environment_key": str(key),
            "environment_variable_uuid": str(env_uuid),
            "value_sha256": value_sha256,
            "mutation_id": str(receipt.get("mutation_id")),
            "endpoint": endpoint,
            "proof_mode": str(postcondition.get("proof_mode")),
        }

    if set(expected) != _C2_IDENTITY_KEYS or len(endpoints) != 1:
        raise _fail("MOTHER_DEPLOY_C2_STANDBY_IDENTITY_INVALID", "identity execution does not cover the exact C2 key set on one service")

    observed_at_value = _utc_timestamp(observed_at)
    endpoint = next(iter(endpoints))
    controller = resolve_coolify_controller(private_state, network, _C2_CONTROLLER)
    try:
        observation = get_coolify_json(
            controller,
            endpoint,
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        endpoint_observation: dict[str, Any] = {
            "ok": observation.ok,
            "path": endpoint,
            "status": observation.status,
            "response_sha256": observation.response_sha256,
            "byte_length": observation.byte_length,
            "elapsed_ms": observation.elapsed_ms,
        }
        env_items = _raw_items(observation.payload, ("envs", "environment_variables", "variables"))
    except CoolifyObservationError as exc:
        endpoint_observation = {
            "ok": False,
            "path": endpoint,
            "status": None,
            "error_code": exc.code,
            "error_message": str(exc),
        }
        env_items = []

    key_results: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    for key in sorted(_C2_IDENTITY_KEYS):
        matches = [item for item in env_items if _env_key(item) == key]
        expected_item = expected[key]
        result: dict[str, Any] = {
            "environment_key": key,
            "expected_environment_variable_uuid": expected_item["environment_variable_uuid"],
            "expected_value_sha256": expected_item["value_sha256"],
            "matches": len(matches),
            "uuid_verified": False,
            "commitment_verified": False,
            "proof_mode": "live-get-value-sha256",
        }
        if len(matches) != 1:
            blockers.append({"code": "MOTHER_DEPLOY_C2_STANDBY_IDENTITY_KEY_NOT_UNIQUE", "message": f"{key} is not unique in Coolify"})
            key_results.append(result)
            continue
        item = matches[0]
        actual_uuid = _env_uuid(item)
        result["observed_environment_variable_uuid"] = actual_uuid
        result["uuid_verified"] = actual_uuid == expected_item["environment_variable_uuid"]
        visible = _visible_value(item)
        if visible is not None:
            result["observed_value_sha256"] = hashlib.sha256(visible.encode("utf-8")).hexdigest()
            result["commitment_verified"] = result["observed_value_sha256"] == expected_item["value_sha256"]
        if result["uuid_verified"] is not True:
            blockers.append({"code": "MOTHER_DEPLOY_C2_STANDBY_IDENTITY_UUID_MISMATCH", "message": f"{key} UUID does not match the execution receipt"})
        if result["commitment_verified"] is not True:
            blockers.append({"code": "MOTHER_DEPLOY_C2_STANDBY_IDENTITY_COMMITMENT_MISMATCH", "message": f"{key} live value commitment does not match the execution receipt"})
        key_results.append(result)

    clean = bool(
        endpoint_observation.get("ok") is True
        and not blockers
        and len(key_results) == 2
    )
    summary = {
        "clean": clean,
        "blocker_count": len(blockers),
        "blocker_codes": [item["code"] for item in blockers],
        "verified_identity_key_count": sum(1 for item in key_results if item.get("uuid_verified") is True and item.get("commitment_verified") is True),
        "identity_mutation_count": 2,
        "service_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "next_phase": "prove-identity-rollback-cycle-before-genesis" if clean else "manual-review-required",
    }
    verification: dict[str, Any] = {
        "kind": _IDENTITY_VERIFICATION_KIND,
        "schema_version": 1,
        "observed_at": observed_at_value,
        "network": network,
        "node": _C2_NODE,
        "nodes": [_C2_NODE],
        "controller_id": _C2_CONTROLLER,
        "mother_binding": {
            "generation": private_state.binding.generation,
            "content_sha256": private_state.binding.content_hash.digest,
            "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
        },
        "execution": {
            "path": str(execution_candidate),
            "sha256": execution.get("result_artifact", {}).get("sha256"),
            "file_sha256": execution_file_sha256,
            "completed_at": execution.get("completed_at"),
            "identity_profile_sha256": execution.get("identity_profile_sha256"),
        },
        "endpoint": endpoint_observation,
        "identity_results": key_results,
        "blockers": blockers,
        "policy": {
            "allowed_http_method": "GET",
            "live_mutation_performed": False,
            "network_access_performed": True,
            "private_state_updated": False,
            "secrets_in_output": False,
            "service_deploy_or_start_performed": False,
            "replica_sync_performed": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
        },
        "identity_mutation_count": 0,
        "service_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "summary": summary,
        "next_phase": summary["next_phase"],
    }
    if write_evidence:
        evidence_path, evidence_sha = _write_c2_standby_identity_verification(
            paths,
            verification,
            operation=operation,
        )
        verification = {**verification, "evidence": {"path": str(evidence_path), "sha256": evidence_sha}}
    return verification



__all__ = [
    "MotherDeploymentC2StandbyError",
    "stage_c2_standby_service_transaction",
    "verify_c2_standby_service_transaction",
    "build_c2_standby_service_release",
    "write_c2_standby_service_release",
    "verify_c2_standby_service_release",
    "inspect_c2_standby_service_release",
    "execute_c2_standby_service_release",
    "verify_c2_standby_service",
    "stage_c2_standby_identity_transaction",
    "verify_c2_standby_identity_transaction",
    "build_c2_standby_identity_release",
    "write_c2_standby_identity_release",
    "verify_c2_standby_identity_release",
    "inspect_c2_standby_identity_release",
    "execute_c2_standby_identity_release",
    "verify_c2_standby_identity",
]
