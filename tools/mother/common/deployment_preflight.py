"""GET-only live preflight for a committed Mother starter deployment plan.

The preflight proves that the Coolify controller, project, server, desired
environment, and target resource namespace still agree with committed Mother
identity.  It never sends a mutating HTTP method and never renders secrets.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import urllib.parse

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import (
    CoolifyObservationError,
    _DEFAULT_OPENER,
    get_coolify_json,
    resolve_coolify_controller,
)
from .deployment_plan import build_starter_deployment_plan
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_PREFLIGHT_KIND = "main_computer.mother.deployment_preflight.v1"
_MAX_ITEMS = 1000
_SAFE_KEYS = (
    "uuid",
    "id",
    "name",
    "status",
    "state",
    "project_uuid",
    "environment_uuid",
    "server_uuid",
    "service_uuid",
    "application_uuid",
)


class MotherDeploymentPreflightError(RuntimeError):
    """The live Coolify state could not be safely preflighted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _items(payload: Any, preferred_keys: tuple[str, ...]) -> list[Any]:
    if type(payload) is list:
        return list(payload)
    if type(payload) is dict:
        for key in (*preferred_keys, "data"):
            value = payload.get(key)
            if type(value) is list:
                return list(value)
        if any(key in payload for key in ("uuid", "id", "name")):
            return [payload]
    return []


def _safe_item(item: Any) -> dict[str, Any]:
    if type(item) is not dict:
        return {"value_type": type(item).__name__}
    result: dict[str, Any] = {}
    for key in _SAFE_KEYS:
        value = item.get(key)
        if value is None or type(value) in {bool, int, float}:
            if key in item:
                result[key] = value
        elif type(value) is str:
            result[key] = value[:512]
    return result


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_INVALID_PLAN",
            f"{path} must be a non-empty string",
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_INVALID_PLAN",
            f"{path} is not a safe identifier",
        )
    return text


def _blocker(code: str, message: str, *, path: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "message": message}
    if path:
        result["path"] = path
    return result


def _request_items(
    controller: Any,
    path: str,
    *,
    preferred_keys: tuple[str, ...],
    authenticated: bool,
    timeout: float,
    max_response_bytes: int,
    max_items: int,
    opener: Any,
) -> dict[str, Any]:
    try:
        observed = get_coolify_json(
            controller,
            path,
            authenticated=authenticated,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
    except CoolifyObservationError as exc:
        return {
            "error_code": exc.code,
            "error_message": str(exc),
            "items": [],
            "ok": False,
            "path": path,
            "status": None,
        }

    items = _items(observed.payload, preferred_keys)
    if len(items) > max_items:
        return {
            "error_code": "MOTHER_DEPLOY_PREFLIGHT_TOO_MANY_ITEMS",
            "error_message": f"{path} returned more than {max_items} items",
            "items": [],
            "ok": False,
            "path": path,
            "status": observed.status,
        }
    result: dict[str, Any] = {
        "items": [_safe_item(item) for item in items],
        "ok": observed.ok,
        "path": path,
        "response_sha256": observed.response_sha256,
        "status": observed.status,
    }
    if not items and type(observed.payload) is str:
        result["safe_text"] = observed.payload[:512]
    elif not items and type(observed.payload) is dict:
        result["safe_fields"] = _safe_item(observed.payload)
    return result


def _endpoint_blocker(label: str, endpoint: Mapping[str, Any]) -> dict[str, Any] | None:
    if bool(endpoint.get("ok")):
        return None
    status = endpoint.get("status")
    detail = endpoint.get("error_message") or f"HTTP {status}"
    return _blocker(
        "MOTHER_DEPLOY_PREFLIGHT_ENDPOINT_FAILED",
        f"Coolify {label} observation failed: {detail}",
        path=str(endpoint.get("path") or ""),
    )


def _matches_by_uuid(items: list[dict[str, Any]], expected: str) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("uuid", item.get("id")) == expected
    ]


def _matches_by_name(items: list[dict[str, Any]], expected: str) -> list[dict[str, Any]]:
    return [item for item in items if item.get("name") == expected]


def _controller_preflight(
    private_state: PrivateStateReadResult,
    node_plan: Mapping[str, Any],
    *,
    network: str,
    timeout: float,
    max_response_bytes: int,
    max_items: int,
    opener: Any,
) -> dict[str, Any]:
    node = _identifier(node_plan.get("node"), "plan node")
    controller_plan = node_plan.get("controller")
    desired = node_plan.get("desired")
    if not isinstance(controller_plan, Mapping) or not isinstance(desired, Mapping):
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_INVALID_PLAN",
            f"plan entry for {node} is incomplete",
        )

    controller_id = _identifier(controller_plan.get("controller_id"), "controller id")
    project_uuid = _identifier(controller_plan.get("project_uuid"), "project UUID")
    server_uuid = _identifier(controller_plan.get("server_uuid"), "server UUID")
    environment_name = _identifier(desired.get("environment_name"), "environment name")
    service_name = _identifier(desired.get("service_name"), "service name")

    controller = resolve_coolify_controller(
        private_state,
        network,
        controller_id,
        require_enabled=True,
        require_token=True,
    )

    endpoints = {
        "health": _request_items(
            controller,
            "/api/health",
            preferred_keys=(),
            authenticated=False,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_items=1,
            opener=opener,
        ),
        "version": _request_items(
            controller,
            "/api/v1/version",
            preferred_keys=(),
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_items=1,
            opener=opener,
        ),
        "projects": _request_items(
            controller,
            "/api/v1/projects",
            preferred_keys=("projects",),
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_items=max_items,
            opener=opener,
        ),
        "servers": _request_items(
            controller,
            "/api/v1/servers",
            preferred_keys=("servers",),
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_items=max_items,
            opener=opener,
        ),
        "applications": _request_items(
            controller,
            "/api/v1/applications",
            preferred_keys=("applications",),
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_items=max_items,
            opener=opener,
        ),
        "services": _request_items(
            controller,
            "/api/v1/services",
            preferred_keys=("services",),
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_items=max_items,
            opener=opener,
        ),
        "resources": _request_items(
            controller,
            "/api/v1/resources",
            preferred_keys=("resources",),
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_items=max_items,
            opener=opener,
        ),
    }
    encoded_project = urllib.parse.quote(project_uuid, safe="")
    endpoints["environments"] = _request_items(
        controller,
        f"/api/v1/projects/{encoded_project}/environments",
        preferred_keys=("environments",),
        authenticated=True,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        max_items=max_items,
        opener=opener,
    )

    blockers = [
        blocker
        for label, endpoint in endpoints.items()
        if (blocker := _endpoint_blocker(label, endpoint)) is not None
    ]

    projects = endpoints["projects"].get("items", [])
    project_matches = _matches_by_uuid(projects, project_uuid)
    if endpoints["projects"].get("ok") and len(project_matches) != 1:
        blockers.append(
            _blocker(
                "MOTHER_DEPLOY_PREFLIGHT_PROJECT_BINDING_MISMATCH",
                f"expected exactly one Coolify project with UUID {project_uuid!r}; found {len(project_matches)}",
                path=f"networks.{network}.coolify.controllers.{controller_id}.project_uuid",
            )
        )

    servers = endpoints["servers"].get("items", [])
    server_matches = _matches_by_uuid(servers, server_uuid)
    if endpoints["servers"].get("ok") and len(server_matches) != 1:
        blockers.append(
            _blocker(
                "MOTHER_DEPLOY_PREFLIGHT_SERVER_BINDING_MISMATCH",
                f"expected exactly one Coolify server with UUID {server_uuid!r}; found {len(server_matches)}",
                path=f"networks.{network}.coolify.controllers.{controller_id}.server_uuid",
            )
        )

    environments = endpoints["environments"].get("items", [])
    environment_matches = _matches_by_name(environments, environment_name)
    if endpoints["environments"].get("ok") and len(environment_matches) > 1:
        blockers.append(
            _blocker(
                "MOTHER_DEPLOY_PREFLIGHT_ENVIRONMENT_AMBIGUOUS",
                f"multiple Coolify environments are named {environment_name!r}",
                path=endpoints["environments"]["path"],
            )
        )

    resource_matches: list[dict[str, Any]] = []
    for label in ("applications", "services", "resources"):
        for item in _matches_by_name(endpoints[label].get("items", []), service_name):
            resource_matches.append({"endpoint": label, **item})
    if resource_matches:
        blockers.append(
            _blocker(
                "MOTHER_DEPLOY_PREFLIGHT_TARGET_EXISTS",
                f"Coolify already contains {len(resource_matches)} resource(s) named {service_name!r}",
                path=f"networks.{network}.deployment.targets.{node}",
            )
        )

    return {
        "node": node,
        "controller_id": controller_id,
        "project": {
            "expected_uuid": project_uuid,
            "matches": project_matches,
            "verified": len(project_matches) == 1,
        },
        "server": {
            "expected_uuid": server_uuid,
            "matches": server_matches,
            "verified": len(server_matches) == 1,
        },
        "environment": {
            "desired_name": environment_name,
            "matches": environment_matches,
            "status": (
                "absent-create-required"
                if not environment_matches
                else "existing-unique"
                if len(environment_matches) == 1
                else "ambiguous"
            ),
        },
        "target_resource": {
            "desired_name": service_name,
            "matches": resource_matches,
            "status": "absent" if not resource_matches else "conflict",
        },
        "endpoints": endpoints,
        "blockers": blockers,
        "clean": not blockers,
    }


def run_starter_deployment_preflight(
    private_state: PrivateStateReadResult,
    *,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    timeout: float = 30.0,
    max_response_bytes: int = 4 * 1024 * 1024,
    max_items: int = _MAX_ITEMS,
    opener: Any = _DEFAULT_OPENER,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Run the authenticated GET-only live preflight for selected starter nodes."""

    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    if type(timeout) not in {int, float} or timeout <= 0:
        raise ValueError("timeout must be positive")
    if type(max_response_bytes) is not int or max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be a positive integer")
    if type(max_items) is not int or max_items <= 0:
        raise ValueError("max_items must be a positive integer")

    plan = build_starter_deployment_plan(
        private_state,
        network=network,
        selected_nodes=selected_nodes,
    )
    offline_blockers = [
        {**blocker, "node": node_plan["node"]}
        for node_plan in plan["sequence"]
        for blocker in node_plan.get("blockers", [])
    ]
    if offline_blockers:
        codes = ", ".join(sorted({item["code"] for item in offline_blockers}))
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_OFFLINE_BLOCKERS",
            f"resolve offline deployment blockers before live preflight: {codes}",
        )

    results = [
        _controller_preflight(
            private_state,
            node_plan,
            network=network,
            timeout=float(timeout),
            max_response_bytes=max_response_bytes,
            max_items=max_items,
            opener=opener,
        )
        for node_plan in plan["sequence"]
    ]
    blockers = [
        {**blocker, "node": result["node"]}
        for result in results
        for blocker in result["blockers"]
    ]
    timestamp = observed_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    _parse_observed_at(timestamp)
    return {
        "kind": _PREFLIGHT_KIND,
        "observed_at": timestamp,
        "schema_version": 1,
        "mother_binding": dict(plan["mother_binding"]),
        "network": network,
        "policy": {
            "allowed_http_method": "GET",
            "legacy_allfather_executor_invoked": False,
            "legacy_qbft_executor_invoked": False,
            "live_mutation_performed": False,
            "network_access_performed": True,
            "redirects_followed": False,
            "secrets_in_output": False,
        },
        "results": results,
        "remaining_global_blockers": list(plan["global_blockers"]),
        "summary": {
            "blocker_codes": sorted({item["code"] for item in blockers}),
            "blocker_count": len(blockers),
            "clean": not blockers,
            "target_count": len(results),
        },
    }


def _parse_observed_at(value: Any) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_INVALID",
            "preflight evidence observation time is missing",
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_INVALID",
            "preflight evidence observation time is malformed",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_INVALID",
            "preflight evidence observation time must be UTC",
        )
    return parsed.astimezone(timezone.utc)


def _contains_sensitive_key(value: Any) -> bool:
    forbidden = {"api_token", "private_key", "password", "secret", "mnemonic", "seed"}
    if isinstance(value, Mapping):
        return any(str(key).lower() in forbidden or _contains_sensitive_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def write_deployment_preflight_evidence(
    paths: PrivateStatePaths,
    report: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    """Persist one canonical, secret-free preflight observation immutably."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    payload_object = dict(report)
    if payload_object.get("kind") != _PREFLIGHT_KIND or _contains_sensitive_key(payload_object):
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_INVALID",
            "preflight evidence is malformed or contains a sensitive field",
        )
    observed_at = payload_object.get("observed_at")
    _parse_observed_at(observed_at)
    network = _identifier(payload_object.get("network"), "network")
    payload = canonical_json(payload_object)
    digest = hashlib.sha256(payload).hexdigest()
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(observed_at))[:32] or "observation"
    root = paths.root / "evidence" / "deployment-preflight"
    try:
        root.relative_to(paths.root)
    except ValueError as exc:
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_PATH_UNSAFE",
            "preflight evidence path escapes Mother state",
        ) from exc
    current = paths.root
    for part in ("evidence", "deployment-preflight"):
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    destination = root / f"{stamp}-{network}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentPreflightError(
                "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_CONFLICT",
                "preflight evidence destination already contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_WRITE_FAILED",
            "preflight evidence reread mismatch",
        )
    return destination, digest


def verify_deployment_preflight_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 300,
    selected_nodes: Iterable[str] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a clean, fresh preflight report against the current Mother binding."""

    if type(max_age_seconds) is not int or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be a positive integer")
    root = (paths.root / "evidence" / "deployment-preflight").resolve(strict=False)
    candidate = Path(evidence_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_PATH_UNSAFE",
            "preflight evidence must be beneath the canonical evidence root",
        ) from exc
    try:
        raw = candidate.read_bytes()
        report = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_INVALID",
            "preflight evidence could not be read as JSON",
        ) from exc
    if type(report) is not dict or canonical_json(report) != raw or report.get("kind") != _PREFLIGHT_KIND:
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_INVALID",
            "preflight evidence is not canonical Mother evidence",
        )
    if _contains_sensitive_key(report):
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_INVALID",
            "preflight evidence contains a sensitive field",
        )
    expected_binding = {
        "content_sha256": private_state.binding.content_hash.digest,
        "generation": private_state.binding.generation,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }
    if report.get("mother_binding") != expected_binding:
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_STALE_BINDING",
            "preflight evidence does not bind the current Mother generation",
        )
    observed = _parse_observed_at(report.get("observed_at"))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    age = (current.astimezone(timezone.utc) - observed).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_STALE_TIME",
            "preflight evidence is outside the permitted freshness window",
        )
    summary = report.get("summary")
    if not isinstance(summary, Mapping) or summary.get("clean") is not True:
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_NOT_CLEAN",
            "preflight evidence contains a live blocker",
        )
    expected_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    actual_nodes = tuple(item.get("node") for item in report.get("results", []) if isinstance(item, Mapping))
    if expected_nodes and actual_nodes != expected_nodes:
        raise MotherDeploymentPreflightError(
            "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_SELECTION_MISMATCH",
            "preflight evidence does not cover the requested node sequence",
        )
    return {
        "clean": True,
        "evidence_path": str(candidate),
        "evidence_sha256": hashlib.sha256(raw).hexdigest(),
        "mother_binding": expected_binding,
        "network": report.get("network"),
        "nodes": list(actual_nodes),
        "observed_at": report.get("observed_at"),
        "age_seconds": int(age),
    }


__all__ = [
    "MotherDeploymentPreflightError",
    "run_starter_deployment_preflight",
    "verify_deployment_preflight_evidence",
    "write_deployment_preflight_evidence",
]
