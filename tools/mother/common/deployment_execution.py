"""Immutable, non-executing deployment request bound to fresh preflight evidence.

This module does not implement the authoritative ``MOTHER-OP-ADD-NODE`` prep
or do stages.  It creates and verifies a secret-free execution request that a
future executor may consume only after the separate mutation-authority and
operation-control contracts are implemented.
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
from .deployment_plan import build_starter_deployment_plan
from .deployment_preflight import verify_deployment_preflight_evidence
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_REQUEST_KIND = "main_computer.mother.deployment_execution_request.v1"
_REQUEST_DIRECTORY = ("actions", "deployment-requests")


class MotherDeploymentExecutionError(RuntimeError):
    """A bounded execution request could not be created or verified."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            f"{path} must be a non-empty string",
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            f"{path} is not a safe identifier",
        )
    return text


def _utc_timestamp(value: Any, path: str) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if type(value) is not str or not value:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            f"{path} must be a UTC timestamp",
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            f"{path} is malformed",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            f"{path} must be UTC",
        )
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _contains_sensitive_key(value: Any) -> bool:
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
            str(key).lower() in forbidden or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _request_digest(request: Mapping[str, Any]) -> str:
    payload = dict(request)
    payload.pop("request_sha256", None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _request_root(paths: PrivateStatePaths) -> Path:
    return paths.root / _REQUEST_DIRECTORY[0] / _REQUEST_DIRECTORY[1]


def _relative_locator(paths: PrivateStatePaths, candidate: Path) -> str:
    root = paths.root.resolve(strict=False)
    resolved = Path(candidate).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_EVIDENCE_PATH_UNSAFE",
            "preflight evidence must be beneath the canonical Mother root",
        ) from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            "preflight evidence locator must be a relative POSIX path",
        )
    pure = PureWindowsPath(locator)
    candidate = Path(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_EVIDENCE_PATH_UNSAFE",
            "preflight evidence locator is unsafe",
        )
    resolved = (paths.root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_EVIDENCE_PATH_UNSAFE",
            "preflight evidence locator escapes Mother state",
        ) from exc
    return resolved


def _sequence_item(network: str, item: Mapping[str, Any]) -> dict[str, Any]:
    node = _identifier(item.get("node"), "deployment node")
    mode = _identifier(item.get("mode"), f"deployment node {node} mode")
    controller = item.get("controller")
    desired = item.get("desired")
    if not isinstance(controller, Mapping) or not isinstance(desired, Mapping):
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            f"deployment node {node} is missing controller or desired state",
        )
    controller_id = _identifier(controller.get("controller_id"), f"deployment node {node} controller")
    environment = _identifier(desired.get("environment_name"), f"deployment node {node} environment")
    service = _identifier(desired.get("service_name"), f"deployment node {node} service")
    return {
        "ordinal": item.get("ordinal"),
        "node": node,
        "operation": "add-node",
        "mode": mode,
        "controller": {
            "controller_id": controller_id,
            "project_uuid": controller.get("project_uuid"),
            "server_uuid": controller.get("server_uuid"),
        },
        "desired": {
            "environment_name": environment,
            "service_name": service,
        },
        "identity_refs": {
            "hub_admin": f"networks.{network}.node_seed_material.{node}.wallets.hub_admin",
            "node": f"networks.{network}.nodes.{node}",
            "validator": f"networks.{network}.validators.{node}",
        },
        "genesis_ref": f"networks.{network}.genesis" if mode == "initial" else None,
        "phases": list(item.get("phases", [])),
    }


def build_deployment_execution_request(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build one deterministic, secret-free, non-executing request."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    network = _identifier(network, "network")
    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)

    evidence = verify_deployment_preflight_evidence(
        paths,
        private_state,
        Path(evidence_path),
        max_age_seconds=max_age_seconds,
        selected_nodes=requested_nodes,
        now=now,
    )
    nodes = requested_nodes or tuple(_identifier(item, "evidence node") for item in evidence["nodes"])
    if not nodes:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            "preflight evidence does not cover any deployment target",
        )

    plan = build_starter_deployment_plan(
        private_state,
        network=network,
        selected_nodes=nodes,
    )
    offline_blockers = [
        {**blocker, "node": item["node"]}
        for item in plan["sequence"]
        for blocker in item.get("blockers", [])
    ]
    if offline_blockers:
        codes = ", ".join(sorted({item["code"] for item in offline_blockers}))
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_OFFLINE_BLOCKERS",
            f"deployment identity is no longer complete: {codes}",
        )

    if tuple(item["node"] for item in plan["sequence"]) != nodes:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_SELECTION_MISMATCH",
            "deployment plan does not match the preflight node sequence",
        )

    request: dict[str, Any] = {
        "kind": _REQUEST_KIND,
        "schema_version": 1,
        "created_at": _utc_timestamp(created_at, "created_at"),
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "network": network,
        "mother_binding": dict(plan["mother_binding"]),
        "preflight_evidence": {
            "locator": _relative_locator(paths, Path(evidence_path)),
            "sha256": evidence["evidence_sha256"],
            "observed_at": evidence["observed_at"],
        },
        "authority": {
            "current": "observe-only",
            "live_execution_authorized": False,
        },
        "policy": {
            "authoritative_prep_completed": False,
            "legacy_allfather_executor_invoked": False,
            "legacy_qbft_executor_invoked": False,
            "live_mutation_performed": False,
            "network_access_performed": False,
            "secrets_in_output": False,
        },
        "sequence": [_sequence_item(network, item) for item in plan["sequence"]],
        "remaining_global_blockers": list(plan["global_blockers"]),
        "summary": {
            "request_valid": True,
            "live_execution_ready": False,
            "target_count": len(plan["sequence"]),
            "blocker_codes": sorted(item["code"] for item in plan["global_blockers"]),
        },
    }
    if _contains_sensitive_key(request):
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            "execution request contains a sensitive field",
        )
    request["request_sha256"] = _request_digest(request)
    return request


def write_deployment_execution_request(
    paths: PrivateStatePaths,
    request: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    """Persist one canonical execution request immutably beneath actions."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    payload_object = dict(request)
    digest = _request_digest(payload_object)
    if (
        payload_object.get("kind") != _REQUEST_KIND
        or payload_object.get("request_sha256") != digest
        or _contains_sensitive_key(payload_object)
    ):
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            "execution request is malformed, unbound, or sensitive",
        )
    payload = canonical_json(payload_object)
    root = _request_root(paths)
    current = paths.root
    for part in _REQUEST_DIRECTORY:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(payload_object.get("created_at", "")))[:32] or "request"
    network = _identifier(payload_object.get("network"), "network")
    destination = root / f"{stamp}-{network}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentExecutionError(
                "MOTHER_DEPLOY_EXECUTION_REQUEST_CONFLICT",
                "execution request destination already contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_WRITE_FAILED",
            "execution request reread mismatch",
        )
    return destination, digest


def verify_deployment_execution_request(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    request_path: Path,
    *,
    max_age_seconds: int = 300,
    selected_nodes: Iterable[str] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a request, its current binding, and its still-fresh evidence."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    root = _request_root(paths).resolve(strict=False)
    candidate = Path(request_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_PATH_UNSAFE",
            "execution request must be beneath the canonical request root",
        ) from exc
    try:
        raw = candidate.read_bytes()
        request = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            "execution request could not be read as canonical JSON",
        ) from exc
    if (
        type(request) is not dict
        or canonical_json(request) != raw
        or request.get("kind") != _REQUEST_KIND
        or _contains_sensitive_key(request)
        or request.get("request_sha256") != _request_digest(request)
    ):
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            "execution request is noncanonical, modified, or sensitive",
        )
    expected_binding = {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }
    if request.get("mother_binding") != expected_binding:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_STALE_BINDING",
            "execution request does not bind the current Mother generation",
        )
    sequence = request.get("sequence")
    if type(sequence) is not list or not sequence:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            "execution request sequence is missing",
        )
    actual_nodes = tuple(_identifier(item.get("node"), "request node") for item in sequence if isinstance(item, Mapping))
    if len(actual_nodes) != len(sequence):
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            "execution request sequence contains an invalid item",
        )
    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested_nodes and requested_nodes != actual_nodes:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_SELECTION_MISMATCH",
            "execution request does not cover the requested node sequence",
        )
    evidence = request.get("preflight_evidence")
    if not isinstance(evidence, Mapping):
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID",
            "execution request preflight binding is missing",
        )
    evidence_path = _resolve_locator(paths, evidence.get("locator"))
    rebuilt = build_deployment_execution_request(
        paths,
        private_state,
        evidence_path,
        network=request.get("network"),
        selected_nodes=actual_nodes,
        max_age_seconds=max_age_seconds,
        created_at=request.get("created_at"),
        now=now,
    )
    if rebuilt != request:
        raise MotherDeploymentExecutionError(
            "MOTHER_DEPLOY_EXECUTION_REQUEST_MISMATCH",
            "execution request no longer matches the current plan and evidence",
        )
    return {
        "clean": True,
        "request_path": str(candidate),
        "request_sha256": request["request_sha256"],
        "mother_binding": expected_binding,
        "network": request["network"],
        "nodes": list(actual_nodes),
        "preflight_evidence": dict(request["preflight_evidence"]),
        "live_execution_authorized": False,
    }


__all__ = [
    "MotherDeploymentExecutionError",
    "build_deployment_execution_request",
    "verify_deployment_execution_request",
    "write_deployment_execution_request",
]
