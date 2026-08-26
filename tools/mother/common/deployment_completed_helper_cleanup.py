"""Clean up completed one-shot Mother helper applications from a Coolify node stack."""

from __future__ import annotations

from datetime import datetime, timezone
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import atomic_files
from .canonical import canonical_json
from .deployment_coolify_context import load_controller_config
from .coolify_state import CoolifyController, resolve_coolify_controller
from .models import OperationIdentity
from .private_state import PrivateStateReadResult


_KIND = "main_computer.mother.completed_helper_cleanup.v1"
_EVIDENCE_SUBDIR = "completed-mother-helper-cleanup"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")

COMPLETED_HELPER_NAMES = frozenset(
    {
        "mother-genesis-init",
        "mother-genesis-proof-guardian",
        "mother-replica-init",
        "mother-replica-sync-guardian",
        "mother-superseded-service-cleanup",
        "mother-validator-admission-guardian",
    }
)

COMPLETED_HELPER_PREFIXES = (
    "mother-add-node-validator-admission-voter-",
    "mother-node-remove-voter-",
)


def _is_dynamic_completed_helper_name(name: object) -> bool:
    return type(name) is str and any(name.startswith(prefix) for prefix in COMPLETED_HELPER_PREFIXES)


def _is_completed_helper_name(name: object) -> bool:
    if type(name) is not str:
        return False
    return name in COMPLETED_HELPER_NAMES or _is_dynamic_completed_helper_name(name)


DEFAULT_REQUIRED_COMPONENT_NAMES = (
    "mother-super-node-fdb",
    "mother-super-node-hub",
)

PRESERVED_HELPER_NAMES = frozenset(
    {
        "mother-validator-quorum-recovery-initial-guardian",
    }
)


class MotherDeploymentCompletedHelperCleanupError(RuntimeError):
    """Completed-helper cleanup failed before a trustworthy result could be produced."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or not _IDENTIFIER_RE.fullmatch(value.strip()):
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_INVALID_ARGUMENT",
            f"invalid {field}",
        )
    return value.strip()


def _uuid(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or not _UUID_RE.fullmatch(value.strip()):
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_INVALID_ARGUMENT",
            f"invalid {field}",
        )
    return value.strip()


def _positive(value: float | int, field: str) -> float:
    if type(value) not in {int, float} or value <= 0:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be positive",
        )
    return float(value)


def _nonnegative(value: float | int, field: str) -> float:
    if type(value) not in {int, float} or value < 0:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be non-negative",
        )
    return float(value)


def _operation(value: OperationIdentity) -> OperationIdentity:
    if not isinstance(value, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    return value


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    if hasattr(opener, "open"):
        return opener.open(request, timeout=timeout)
    if callable(opener):
        return opener(request, timeout=timeout)
    raise TypeError("opener must be callable or provide open(request, timeout=...)")


def _http(
    controller: CoolifyController,
    method: str,
    endpoint: str,
    *,
    body: Mapping[str, Any] | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(endpoint)
    if (
        not endpoint.startswith("/api/v1/")
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or "\\" in endpoint
        or "\x00" in endpoint
        or any(part in {"..", "."} for part in Path(parsed.path).parts)
    ):
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_UNSAFE_ENDPOINT",
            "Coolify endpoint is unsafe",
        )
    payload = canonical_json(dict(body)) if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {controller.api_token}",
        "User-Agent": "main-computer-mother-completed-helper-cleanup/1",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        controller.base_url + endpoint,
        data=payload,
        headers=headers,
        method=method.upper(),
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
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_REQUEST_FAILED",
            "Coolify request failed",
        ) from exc
    if len(raw) > max_response_bytes:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_RESPONSE_TOO_LARGE",
            "Coolify response is too large",
        )
    try:
        payload_out: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload_out = raw.decode("utf-8", errors="replace")
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "payload": payload_out,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


def _status(value: object) -> str:
    return value.strip().lower() if type(value) is str else ""


def _healthy(status: str) -> bool:
    normalized = status.strip().lower()
    return normalized.startswith("running:healthy") and "unhealthy" not in normalized


def _terminal_completed(status: str) -> bool:
    normalized = status.strip().lower()
    return normalized in {"exited", "stopped"} or normalized.startswith("exited:") or normalized.startswith("stopped:")


def _terminal_completed_success(status: str) -> bool:
    normalized = status.strip().lower()
    return normalized in {
        "exited",
        "exited:0",
        "stopped:0",
        "exited (0)",
        "stopped (0)",
        "exited successfully",
        "stopped successfully",
    }


def _completed_helper_cleanup_eligible(name: object, status: str) -> bool:
    if not _is_completed_helper_name(name):
        return False
    if _is_dynamic_completed_helper_name(name):
        return _terminal_completed_success(status)
    return _terminal_completed(status)


def _parent_degraded(status: str) -> bool:
    normalized = status.strip().lower()
    return "degraded" in normalized or "unhealthy" in normalized


def _safe_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, bool)):
        return str(value)[:240]
    return ""


def _truthy_excluded(value: object) -> bool:
    if value is True:
        return True
    if type(value) is int and value == 1:
        return True
    if type(value) is str:
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _application_records(payload: Any) -> list[dict[str, str | bool]]:
    if not isinstance(payload, Mapping):
        return []
    raw_items = payload.get("applications")
    if type(raw_items) is not list:
        return []
    records: list[dict[str, str | bool]] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        name = _safe_scalar(item.get("name")).strip()
        uuid = _safe_scalar(item.get("uuid")).strip()
        status = _safe_scalar(item.get("status")).strip()
        image = _safe_scalar(item.get("image")).strip()
        if not name and not uuid:
            continue
        records.append(
            {
                "name": name,
                "uuid": uuid,
                "status": status,
                "image": image,
                "exclude_from_status": _truthy_excluded(item.get("exclude_from_status")),
            }
        )
    return records


def _component_summary(
    *,
    payload: Any,
    node: str,
    required_component_names: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_BAD_RESPONSE",
            "Coolify service detail payload is not an object",
        )

    parent = {
        "name": _safe_scalar(payload.get("name")),
        "uuid": _safe_scalar(payload.get("uuid")),
        "status": _safe_scalar(payload.get("status")),
        "description": _safe_scalar(payload.get("description")),
    }
    applications = _application_records(payload)
    by_name = {item["name"]: item for item in applications if item["name"]}

    required_names = tuple(dict.fromkeys((node, *required_component_names)))
    required_components = []
    missing_required: list[str] = []
    unhealthy_required: list[dict[str, str]] = []
    for name in required_names:
        record = by_name.get(name)
        if record is None:
            missing_required.append(name)
            required_components.append({"name": name, "uuid": "", "status": "missing", "image": ""})
            continue
        required_components.append(record)
        if not _healthy(_status(record.get("status"))):
            unhealthy_required.append(record)

    completed_helpers = [
        item
        for item in applications
        if _completed_helper_cleanup_eligible(item.get("name"), _status(item.get("status")))
        and item.get("exclude_from_status") is not True
    ]
    running_or_nonterminal_completed_helpers = [
        item
        for item in applications
        if _is_completed_helper_name(item.get("name"))
        and not _completed_helper_cleanup_eligible(item.get("name"), _status(item.get("status")))
    ]
    preserved_helpers = [item for item in applications if item.get("name") in PRESERVED_HELPER_NAMES]
    excluded_terminal = [
        item
        for item in applications
        if item.get("exclude_from_status") is True and _terminal_completed(_status(item.get("status")))
    ]
    excluded_unhealthy = [
        item
        for item in applications
        if item.get("exclude_from_status") is True and "unhealthy" in _status(item.get("status"))
    ]
    unexpected_terminal = [
        item
        for item in applications
        if _terminal_completed(_status(item.get("status")))
        and not _is_completed_helper_name(item.get("name"))
        and item.get("name") not in {name for name in required_names}
        and item.get("exclude_from_status") is not True
    ]
    unclassified_unhealthy = [
        item
        for item in applications
        if "unhealthy" in _status(item.get("status"))
        and item.get("name") not in {name for name in required_names}
        and not _is_completed_helper_name(item.get("name"))
        and item.get("exclude_from_status") is not True
    ]

    parent_status = _status(parent.get("status"))
    clean = (
        not completed_helpers
        and not missing_required
        and not unhealthy_required
        and not unexpected_terminal
        and not unclassified_unhealthy
        and not _parent_degraded(parent_status)
    )
    core_healthy = not missing_required and not unhealthy_required

    return {
        "parent": parent,
        "applications": applications,
        "required_components": required_components,
        "completed_helper_candidates": completed_helpers,
        "running_or_nonterminal_completed_helpers": running_or_nonterminal_completed_helpers,
        "preserved_helpers": preserved_helpers,
        "excluded_completed_helper_records": excluded_terminal,
        "excluded_unhealthy_components": excluded_unhealthy,
        "unexpected_terminal_components": unexpected_terminal,
        "unclassified_unhealthy_components": unclassified_unhealthy,
        "summary": {
            "clean": clean,
            "parent_status_clean": not _parent_degraded(parent_status),
            "core_required_components_healthy": core_healthy,
            "required_component_count": len(required_components),
            "completed_helper_candidate_count": len(completed_helpers),
            "running_or_nonterminal_completed_helper_count": len(running_or_nonterminal_completed_helpers),
            "preserved_helper_count": len(preserved_helpers),
            "excluded_completed_helper_record_count": len(excluded_terminal),
            "excluded_unhealthy_component_count": len(excluded_unhealthy),
            "unexpected_terminal_component_count": len(unexpected_terminal),
            "unclassified_unhealthy_component_count": len(unclassified_unhealthy),
        },
    }


def _service_detail(
    controller: CoolifyController,
    service_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    quoted = urllib.parse.quote(service_uuid, safe="")
    response = _http(
        controller,
        "GET",
        f"/api/v1/services/{quoted}",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if not response["ok"]:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_SERVICE_DETAIL_FAILED",
            f"Coolify service detail request failed with HTTP {response['status']}",
        )
    return response



def _decode_compose_value(value: object) -> tuple[str, str]:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_MISSING",
            "Coolify service detail does not include docker compose content",
        )
    text = value.strip()
    try:
        decoded = base64.b64decode(text.encode("ascii"), validate=True)
        decoded_text = decoded.decode("utf-8")
        if "services:" in decoded_text or decoded_text.lstrip().startswith(("version:", "name:")):
            return decoded_text, "base64"
    except (binascii.Error, UnicodeDecodeError, ValueError):
        pass
    return value, "plain"


def _compose_text_candidates_from_service_payload(payload: Any) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(payload, Mapping):
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_BAD_RESPONSE",
            "Coolify service detail payload is not an object",
        )

    # Any compose text written back through docker_compose_raw must originate
    # from the raw/source compose when Coolify provides it.  docker_compose is
    # Coolify's rendered form and may contain generated container names,
    # injected environment, networks, and resource-prefixed volume names.
    # Round-tripping that rendered form into docker_compose_raw recursively
    # materializes those generated values and corrupts compose lineage.
    raw = payload.get("docker_compose_raw")
    if type(raw) is str and raw.strip():
        text, encoding = _decode_compose_value(raw)
        return ((text, "docker_compose_raw", encoding),)

    rendered = payload.get("docker_compose")
    if type(rendered) is str and rendered.strip():
        text, encoding = _decode_compose_value(rendered)
        return ((text, "docker_compose", encoding),)

    raise MotherDeploymentCompletedHelperCleanupError(
        "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_MISSING",
        "Coolify service detail does not include docker compose content",
    )


def _compose_text_from_service_payload(payload: Any) -> tuple[str, str, str]:
    return _compose_text_candidates_from_service_payload(payload)[0]



def _label_values(labels: object) -> tuple[str, ...]:
    if isinstance(labels, Mapping):
        return tuple(str(item) for pair in labels.items() for item in pair)
    if isinstance(labels, list):
        return tuple(str(item) for item in labels)
    if isinstance(labels, str):
        return (labels,)
    return ()


def _service_helper_match(service_name: str, definition: object, helper_names: tuple[str, ...]) -> str | None:
    haystack: list[str] = [service_name]
    if isinstance(definition, Mapping):
        for key in ("container_name", "hostname", "name"):
            value = definition.get(key)
            if isinstance(value, str):
                haystack.append(value)
        haystack.extend(_label_values(definition.get("labels")))
        environment = definition.get("environment")
        if isinstance(environment, Mapping):
            for key, value in environment.items():
                if str(key).upper() in {"SERVICE_NAME", "COOLIFY_SERVICE_NAME", "MOTHER_HELPER_NAME"}:
                    haystack.append(str(value))
        elif isinstance(environment, list):
            for item in environment:
                item_text = str(item)
                if item_text.startswith(("SERVICE_NAME=", "COOLIFY_SERVICE_NAME=", "MOTHER_HELPER_NAME=")):
                    haystack.append(item_text)

    for helper_name in helper_names:
        for value in haystack:
            if value == helper_name or helper_name in value:
                return helper_name
    return None


def _remove_completed_helpers_from_compose(
    compose_text: str,
    helper_names: tuple[str, ...],
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    try:
        parsed = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_INVALID",
            "Coolify docker compose content is not valid YAML",
        ) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("services"), dict):
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_INVALID",
            "Coolify docker compose content does not contain a services mapping",
        )

    services = parsed["services"]
    removed_service_names: list[str] = []
    removed_helper_names: list[str] = []
    for service_name, definition in list(services.items()):
        if not isinstance(service_name, str):
            continue
        matched_helper = _service_helper_match(service_name, definition, helper_names)
        if matched_helper is None:
            continue
        del services[service_name]
        removed_service_names.append(service_name)
        if matched_helper not in removed_helper_names:
            removed_helper_names.append(matched_helper)

    if not removed_service_names:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_NO_MATCH",
            "no completed helper service names were present in the compose content",
        )

    cleaned = yaml.safe_dump(parsed, sort_keys=False)
    return cleaned, tuple(removed_service_names), tuple(removed_helper_names)



def _patch_service_compose(
    controller: CoolifyController,
    service_uuid: str,
    compose_text: str,
    *,
    instant_deploy: bool,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "service_uuid")
    endpoint = f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
    encoded = base64.b64encode(compose_text.encode("utf-8")).decode("ascii")
    body = {
        "docker_compose_raw": encoded,
        "instant_deploy": bool(instant_deploy),
    }
    response = _http(
        controller,
        "PATCH",
        endpoint,
        body=body,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    return {
        "method": "PATCH",
        "endpoint": endpoint,
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "service_uuid": service,
        "docker_compose_raw_sha256": hashlib.sha256(encoded.encode("ascii")).hexdigest(),
        "instant_deploy": bool(instant_deploy),
    }





def _patch_service_compose_reconcile(
    controller: CoolifyController,
    service_uuid: str,
    payload: Any,
    helper_names: tuple[str, ...],
    *,
    instant_deploy: bool,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "service_uuid")
    attempts: list[dict[str, Any]] = []
    for compose_text, source_field, source_encoding in _compose_text_candidates_from_service_payload(payload):
        source_digest = hashlib.sha256(compose_text.encode("utf-8")).hexdigest()
        try:
            _remove_completed_helpers_from_compose(compose_text, helper_names)
        except MotherDeploymentCompletedHelperCleanupError as exc:
            if exc.code != "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_NO_MATCH":
                attempts.append(
                    {
                        "source_field": source_field,
                        "source_encoding": source_encoding,
                        "source_sha256": source_digest,
                        "ok": False,
                        "error_code": exc.code,
                    }
                )
                continue
            patch_receipt = _patch_service_compose(
                controller,
                service,
                compose_text,
                instant_deploy=instant_deploy,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            return {
                **patch_receipt,
                "refresh_scope": "compose-reconcile",
                "source_field": source_field,
                "source_encoding": source_encoding,
                "source_sha256": source_digest,
                "compose_source_attempts": [
                    *attempts,
                    {
                        "source_field": source_field,
                        "source_encoding": source_encoding,
                        "source_sha256": source_digest,
                        "ok": True,
                        "removed_service_count": 0,
                        "removed_helper_count": 0,
                        "compose_already_clean": True,
                    },
                ],
                "removed_service_names": [],
                "removed_helper_names": [],
                "removed_service_count": 0,
                "removed_helper_count": 0,
            }

        attempts.append(
            {
                "source_field": source_field,
                "source_encoding": source_encoding,
                "source_sha256": source_digest,
                "ok": False,
                "error_code": "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_STILL_HAS_HELPERS",
            }
        )

    return {
        "method": "PATCH",
        "endpoint": f"/api/v1/services/{service}",
        "status": None,
        "ok": False,
        "service_uuid": service,
        "instant_deploy": bool(instant_deploy),
        "refresh_scope": "compose-reconcile",
        "error_code": "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_RECONCILE_NO_CLEAN_SOURCE",
        "error_message": "no helper-free Coolify compose source was available for reconcile refresh",
        "compose_source_attempts": attempts,
        "removed_service_names": [],
        "removed_helper_names": [],
        "removed_service_count": 0,
        "removed_helper_count": 0,
    }


def _request_service_redeploy_refresh(
    controller: CoolifyController,
    service_uuid: str,
    *,
    force: bool,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "service_uuid")
    endpoint = (
        f"/api/v1/deploy?uuid={urllib.parse.quote(service, safe='')}"
        f"&force={'true' if force else 'false'}"
    )
    response = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    return {
        "method": "GET",
        "endpoint": endpoint,
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "service_uuid": service,
        "force": bool(force),
        "refresh_scope": "service-redeploy",
    }


def _controller_config(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
) -> dict[str, Any]:
    return load_controller_config(
        private_state,
        network=network,
        controller_id=controller_id,
        allowed_controllers={controller_id},
        error_factory=MotherDeploymentCompletedHelperCleanupError,
        rejected_code="MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_CONTROLLER_REJECTED",
        invalid_code="MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_PRIVATE_STATE_INVALID",
        placement_description="completed helper cleanup placement",
    )


def _application_uuid(payload: Any) -> str:
    found: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, value in item.items():
                if str(key) in {"uuid", "service_uuid", "application_uuid"} and type(value) is str:
                    clean = value.strip()
                    if _UUID_RE.fullmatch(clean):
                        found.add(clean)
                elif isinstance(value, (Mapping, list)):
                    walk(value)
        elif type(item) is list:
            for value in item:
                walk(value)

    walk(payload)
    if len(found) != 1:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_SERVICE_UUID_MISSING",
            "Coolify mutation response did not contain exactly one service uuid",
        )
    return next(iter(found))


def _environment_uuid(payload: Any, expected_name: str) -> str:
    expected = _identifier(expected_name, "environment_name")
    matches: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            name = item.get("name")
            uuid = item.get("uuid")
            if name == expected and type(uuid) is str and _UUID_RE.fullmatch(uuid):
                matches.append(uuid)
            for value in item.values():
                if isinstance(value, (Mapping, list)):
                    walk(value)
        elif type(item) is list:
            for value in item:
                walk(value)

    walk(payload)
    if len(set(matches)) != 1:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_ENVIRONMENT_INVALID",
            f"expected exactly one Coolify environment named {expected}",
        )
    return next(iter(set(matches)))


def _resolve_environment_uuid(
    *,
    controller: CoolifyController,
    controller_id: str,
    endpoint: str,
    expected_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
) -> str:
    response = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    observations.append(
        {
            "method": "GET",
            "endpoint": endpoint,
            "status": response["status"],
            "ok": response["ok"],
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "controller_id": controller_id,
            "phase": "completed-helper-docker-orphan-cleanup-environment-resolution",
        }
    )
    if response.get("ok") is not True:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_ENVIRONMENT_INVALID",
            f"{controller_id} environment inventory failed with HTTP {response.get('status')}",
        )
    return _environment_uuid(response.get("payload"), expected_name)


def _healthy_status(value: object) -> bool:
    return type(value) is str and value.startswith("running:healthy")


def _service_detail_status(payload: Any, service_uuid: str, service_name: str) -> str:
    target_uuid = _uuid(service_uuid, "temporary_service_uuid")
    target_name = _identifier(service_name, "temporary_service_name")
    candidates: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            uuid = item.get("uuid")
            name = item.get("name")
            status = item.get("status")
            if type(status) is str and (uuid == target_uuid or name == target_name):
                candidates.append(status)
            for value in item.values():
                if isinstance(value, (Mapping, list)):
                    walk(value)
        elif type(item) is list:
            for value in item:
                walk(value)

    walk(payload)
    for status in candidates:
        if _healthy_status(status):
            return status
    return candidates[-1] if candidates else ""


def _wait_for_temporary_service_health(
    *,
    controller: CoolifyController,
    controller_id: str,
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    started = time.monotonic()
    first_status = ""
    last_status = ""
    observed_statuses: list[str] = []
    observation_count = 0
    endpoint = f"/api/v1/services/{urllib.parse.quote(_uuid(service_uuid, 'temporary_service_uuid'), safe='')}"
    while True:
        response = _http(
            controller,
            "GET",
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        status = _service_detail_status(response.get("payload"), service_uuid, service_name) if response.get("ok") else ""
        observation_count += 1
        if status and not first_status:
            first_status = status
        if status:
            observed_statuses.append(status)
            last_status = status
        elapsed = time.monotonic() - started
        observations.append(
            {
                "method": "GET",
                "endpoint": endpoint,
                "status": response["status"],
                "ok": response["ok"],
                "response_sha256": response["response_sha256"],
                "byte_length": response["byte_length"],
                "elapsed_ms": response["elapsed_ms"],
                "controller_id": controller_id,
                "phase": "completed-helper-docker-orphan-cleanup-status-health-result",
                "service_uuid": service_uuid,
                "service_name": service_name,
                "service_status": status or None,
                "healthy": _healthy_status(status),
            }
        )
        if _healthy_status(status):
            return {
                "healthy": True,
                "service_status": status,
                "first_status": first_status or None,
                "final_status": status or None,
                "observed_statuses": observed_statuses,
                "service_uuid": service_uuid,
                "service_name": service_name,
                "observation_count": observation_count,
                "wait_seconds": int(elapsed),
                "wait_milliseconds": int(round(elapsed * 1000)),
            }
        if elapsed >= max_wait_seconds:
            return {
                "healthy": False,
                "service_status": last_status or None,
                "first_status": first_status or None,
                "final_status": last_status or None,
                "observed_statuses": observed_statuses,
                "service_uuid": service_uuid,
                "service_name": service_name,
                "observation_count": observation_count,
                "wait_seconds": int(elapsed),
                "wait_milliseconds": int(round(elapsed * 1000)),
                "reason": "health-timeout",
            }
        if poll_interval_seconds > 0:
            time.sleep(min(poll_interval_seconds, max(0.0, max_wait_seconds - elapsed)))
        else:
            break
    return {
        "healthy": False,
        "service_status": last_status or None,
        "first_status": first_status or None,
        "final_status": last_status or None,
        "observed_statuses": observed_statuses,
        "service_uuid": service_uuid,
        "service_name": service_name,
        "observation_count": observation_count,
        "wait_seconds": int(time.monotonic() - started),
        "wait_milliseconds": int(round((time.monotonic() - started) * 1000)),
        "reason": "health-timeout",
    }


def _docker_orphan_cleanup_script(
    *,
    parent_service_uuid: str,
    node: str,
    helper_names: tuple[str, ...],
) -> str:
    project = _uuid(parent_service_uuid, "service_uuid")
    node_name = _identifier(node, "node")
    helpers = tuple(_identifier(name, "completed_helper_name") for name in helper_names)
    if not helpers:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_INVALID_ARGUMENT",
            "docker orphan cleanup requires at least one helper name",
        )
    helper_exact_lines = "\n".join(f"    {name}) printf '%s' '{name}'; return 0 ;;" for name in helpers)
    helper_name_lines = "\n".join(f"    *{name}*) printf '%s' '{name}'; return 0 ;;" for name in helpers)
    return "\n".join(
        [
            "set -u",
            f"project='{project}'",
            f"node='{node_name}'",
            "proof_dir='/proof'",
            "healthy=\"$proof_dir/healthy\"",
            "proof=\"$proof_dir/completed-helper-orphan-cleanup.json\"",
            "failure=\"$proof_dir/completed-helper-orphan-cleanup-failed.json\"",
            "log=\"$proof_dir/completed-helper-orphan-cleanup.log\"",
            "mkdir -p \"$proof_dir\" \"$proof_dir/.docker\"",
            "normalize_label() {",
            "  case \"${1:-}\" in",
            "    ''|'<no value>'|'<nil>'|'null') printf '' ;;",
            "    *) printf '%s' \"$1\" ;;",
            "  esac",
            "}",
            "match_helper() {",
            "  case \"${1:-}\" in",
            helper_exact_lines,
            "  esac",
            "  case \"${1:-}\" in",
            helper_name_lines,
            "  esac",
            "  return 1",
            "}",
            "json_escape() { printf '%s' \"$1\" | sed 's/\\\\/\\\\\\\\/g; s/\"/\\\\\"/g'; }",
            "run_cleanup() {",
            "  set -e",
            "  ids=\"$(docker ps -aq --filter \"label=com.docker.compose.project=$project\")\"",
            "  candidate_count=0",
            "  removed_count=0",
            "  removed_names=''",
            "  skipped_names=''",
            "  for id in $ids; do",
            "    service=\"$(normalize_label \"$(docker inspect --format '{{ index .Config.Labels \"com.docker.compose.service\" }}' \"$id\" 2>/dev/null || true)\")\"",
            "    coolify_service=\"$(normalize_label \"$(docker inspect --format '{{ index .Config.Labels \"coolify.serviceName\" }}' \"$id\" 2>/dev/null || true)\")\"",
            "    mother_node=\"$(normalize_label \"$(docker inspect --format '{{ index .Config.Labels \"main_computer.mother.node\" }}' \"$id\" 2>/dev/null || true)\")\"",
            "    status=\"$(normalize_label \"$(docker inspect --format '{{ .State.Status }}' \"$id\" 2>/dev/null || true)\")\"",
            "    name=\"$(docker inspect --format '{{ .Name }}' \"$id\" 2>/dev/null | sed 's#^/##' || true)\"",
            "    matched=\"$(match_helper \"$service\" || true)\"",
            "    if [ -z \"$matched\" ]; then matched=\"$(match_helper \"$coolify_service\" || true)\"; fi",
            "    if [ -z \"$matched\" ]; then matched=\"$(match_helper \"$name\" || true)\"; fi",
            "    if [ -z \"$matched\" ]; then",
            "      if [ -n \"$skipped_names\" ]; then skipped_names=\"$skipped_names,$name\"; else skipped_names=\"$name\"; fi",
            "      continue",
            "    fi",
            "    candidate_count=$((candidate_count + 1))",
            "    if [ -n \"$mother_node\" ] && [ \"$mother_node\" != \"$node\" ]; then",
            "      echo \"refusing helper container outside acknowledged node: id=$id name=$name helper=$matched mother_node=$mother_node expected=$node\" >&2",
            "      exit 1",
            "    fi",
            "    case \"$status\" in",
            "      exited|dead|created) ;;",
            "      *) echo \"refusing to remove non-terminal helper container: id=$id name=$name helper=$matched status=$status\" >&2; exit 1 ;;",
            "    esac",
            "  done",
            "  for id in $ids; do",
            "    service=\"$(normalize_label \"$(docker inspect --format '{{ index .Config.Labels \"com.docker.compose.service\" }}' \"$id\" 2>/dev/null || true)\")\"",
            "    coolify_service=\"$(normalize_label \"$(docker inspect --format '{{ index .Config.Labels \"coolify.serviceName\" }}' \"$id\" 2>/dev/null || true)\")\"",
            "    name=\"$(docker inspect --format '{{ .Name }}' \"$id\" 2>/dev/null | sed 's#^/##' || true)\"",
            "    matched=\"$(match_helper \"$service\" || true)\"",
            "    if [ -z \"$matched\" ]; then matched=\"$(match_helper \"$coolify_service\" || true)\"; fi",
            "    if [ -z \"$matched\" ]; then matched=\"$(match_helper \"$name\" || true)\"; fi",
            "    if [ -z \"$matched\" ]; then continue; fi",
            "    docker rm -f \"$id\" >/dev/null",
            "    removed_count=$((removed_count + 1))",
            "    if [ -n \"$removed_names\" ]; then removed_names=\"$removed_names,$name\"; else removed_names=\"$name\"; fi",
            "  done",
            "  remaining=''",
            "  for id in $(docker ps -aq --filter \"label=com.docker.compose.project=$project\"); do",
            "    service=\"$(normalize_label \"$(docker inspect --format '{{ index .Config.Labels \"com.docker.compose.service\" }}' \"$id\" 2>/dev/null || true)\")\"",
            "    coolify_service=\"$(normalize_label \"$(docker inspect --format '{{ index .Config.Labels \"coolify.serviceName\" }}' \"$id\" 2>/dev/null || true)\")\"",
            "    name=\"$(docker inspect --format '{{ .Name }}' \"$id\" 2>/dev/null | sed 's#^/##' || true)\"",
            "    if match_helper \"$service\" >/dev/null || match_helper \"$coolify_service\" >/dev/null || match_helper \"$name\" >/dev/null; then",
            "      if [ -n \"$remaining\" ]; then remaining=\"$remaining,$name\"; else remaining=\"$name\"; fi",
            "    fi",
            "  done",
            "  if [ -n \"$remaining\" ]; then",
            "    echo \"completed helper orphan containers remain after docker cleanup: $remaining\" >&2",
            "    exit 1",
            "  fi",
            "  cat > \"$proof.tmp\" <<EOF",
            "{",
            "  \"completed\": true,",
            "  \"candidate_count\": $candidate_count,",
            "  \"removed_container_count\": $removed_count,",
            "  \"removed_container_names\": \"$(json_escape \"$removed_names\")\",",
            "  \"skipped_container_names\": \"$(json_escape \"$skipped_names\")\",",
            f"  \"node\": \"{node_name}\",",
            f"  \"project_uuid\": \"{project}\"",
            "}",
            "EOF",
            "  mv \"$proof.tmp\" \"$proof\"",
            "}",
            "if run_cleanup > \"$log.tmp\" 2>&1; then",
            "  mv \"$log.tmp\" \"$log\"",
            "  rm -f \"$failure\"",
            "  date +%s > \"$healthy\"",
            "  cat \"$log\" || true",
            "  echo \"completed-helper orphan cleanup completed: project=$project\"",
            "else",
            "  code=$?",
            "  mv \"$log.tmp\" \"$log\" 2>/dev/null || true",
            "  rm -f \"$healthy\"",
            "  cat > \"$failure.tmp\" <<EOF",
            "{",
            "  \"completed\": false,",
            "  \"exit_code\": $code,",
            f"  \"node\": \"{node_name}\",",
            f"  \"project_uuid\": \"{project}\",",
            "  \"log_path\": \"/proof/completed-helper-orphan-cleanup.log\"",
            "}",
            "EOF",
            "  mv \"$failure.tmp\" \"$failure\"",
            "  cat \"$log\" >&2 || true",
            "  echo \"completed-helper orphan cleanup failed: project=$project exit_code=$code\" >&2",
            "fi",
            "exec tail -f /dev/null",
        ]
    )

def _docker_orphan_cleanup_compose(
    *,
    service_name: str,
    parent_service_uuid: str,
    node: str,
    helper_names: tuple[str, ...],
) -> str:
    name = _identifier(service_name, "temporary_service_name")
    script = _docker_orphan_cleanup_script(
        parent_service_uuid=parent_service_uuid,
        node=node,
        helper_names=helper_names,
    )
    # Docker Compose interpolates $VAR and ${...} before the command reaches
    # the container. Escape every shell dollar so the generated Compose carries
    # the cleanup script through interpolation unchanged.
    escaped_script = script.replace("$", "$$")
    indented = "\n".join("        " + line for line in escaped_script.splitlines())
    compose = "\n".join(
        [
            "services:",
            f"  {name}:",
            "    image: docker:27-cli",
            "    restart: \"no\"",
            "    read_only: true",
            "    network_mode: none",
            "    environment:",
            "      DOCKER_CONFIG: /proof/.docker",
            "      HOME: /proof",
            "      TMPDIR: /tmp",
            "    tmpfs:",
            "      - /tmp",
            "    command:",
            "      - sh",
            "      - -ec",
            "      - |",
            indented,
            "    healthcheck:",
            "      test:",
            "        - CMD",
            "        - sh",
            "        - -ec",
            "        - test -f /proof/healthy && test -f /proof/completed-helper-orphan-cleanup.json",
            "      interval: 5s",
            "      timeout: 5s",
            "      retries: 24",
            "      start_period: 10s",
            "    volumes:",
            "      - /var/run/docker.sock:/var/run/docker.sock",
            "      - cleanup-proof:/proof",
            "    labels:",
            f"      main_computer.mother.node: {node}",
            "      main_computer.mother.component: completed-helper-orphan-container-cleanup",
            f"      main_computer.mother.target-service-uuid: {parent_service_uuid}",
            "volumes:",
            "  cleanup-proof:",
            "",
        ]
    )
    parsed = yaml.safe_load(compose)
    if not isinstance(parsed, Mapping) or "services" not in parsed or name not in parsed["services"]:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_DOCKER_COMPOSE_INVALID",
            "compiled Docker orphan cleanup Compose is invalid",
        )
    if "$" in compose.replace("$$", ""):
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_DOCKER_COMPOSE_INVALID",
            "Docker orphan cleanup Compose contains an unescaped dollar interpolation",
        )
    if "/var/run/docker.sock:/var/run/docker.sock" not in compose:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_DOCKER_COMPOSE_INVALID",
            "Docker orphan cleanup Compose does not mount the Docker socket",
        )
    for helper in helper_names:
        if helper not in compose:
            raise MotherDeploymentCompletedHelperCleanupError(
                "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_DOCKER_COMPOSE_INVALID",
                "Docker orphan cleanup Compose is missing a helper allowlist entry",
            )
    return compose


def _temporary_service_body(controller_config: Mapping[str, Any], name: str, compose: str) -> dict[str, Any]:
    return {
        "project_uuid": controller_config["project_uuid"],
        "server_uuid": controller_config["server_uuid"],
        "environment_name": "mainnet",
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "name": name,
        "description": "Ephemeral Mother cleanup of completed helper orphan containers",
        "instant_deploy": False,
    }


_TARGETED_HELPER_SERVICE_NAMES = frozenset(
    {
        "mother-add-node-validator-activation-guardian",
        "mother-genesis-proof-guardian",
    }
)
_TARGETED_HELPER_SERVICE_PREFIXES = ("mother-add-node-validator-admission-voter-",)


def _targeted_helper_name(value: object) -> str:
    name = _identifier(value, "targeted_helper_service")
    if name in _TARGETED_HELPER_SERVICE_NAMES or any(name.startswith(prefix) for prefix in _TARGETED_HELPER_SERVICE_PREFIXES):
        return name
    raise MotherDeploymentCompletedHelperCleanupError(
        "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_UNSAFE_TARGETED_HELPER",
        f"existing helper mimic restart is not allowed for {name}",
    )


def _targeted_helper_application_uuid_from_payload(payload: Any, helper_name: str) -> str:
    """Return the existing Coolify child/application UUID for an allowed helper.

    Cleanup only rewrites helper rows that Coolify already knows about.  A
    missing or ambiguous helper record is not repaired by creating a new service
    here; that belongs to the lifecycle phase that owns the helper.
    """
    helper = _targeted_helper_name(helper_name)
    matches = [item for item in _application_records(payload) if item.get("name") == helper]
    if len(matches) != 1:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_TARGETED_HELPER_RECORD_INVALID",
            f"expected exactly one existing Coolify application record for {helper}; found {len(matches)}",
        )
    return _uuid(matches[0].get("uuid"), "targeted_helper_application_uuid")


def _restart_existing_helper_application(
    *,
    controller: CoolifyController,
    parent_service_uuid: str,
    helper_name: str,
    helper_application_uuid: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Restart/start one existing helper child service after its mimic Compose rewrite.

    This deliberately avoids parent service deploy/start/restart and avoids the
    old temporary Docker-socket helper-apply service.  The cleanup contract is
    control-plane scoped: the helper row existed, the parent Compose was patched
    to the retired autohealthy mimic, and Coolify accepted a child-only restart
    or start operation for that helper UUID.
    """
    parent = _uuid(parent_service_uuid, "service_uuid")
    helper = _targeted_helper_name(helper_name)
    application = _uuid(helper_application_uuid, "targeted_helper_application_uuid")
    endpoints = (
        ("POST", f"/api/v1/applications/{urllib.parse.quote(application, safe='')}/restart", "application-restart"),
        ("POST", f"/api/v1/applications/{urllib.parse.quote(application, safe='')}/start", "application-start"),
        (
            "POST",
            f"/api/v1/services/{urllib.parse.quote(parent, safe='')}/applications/{urllib.parse.quote(application, safe='')}/restart",
            "service-application-restart",
        ),
        (
            "POST",
            f"/api/v1/services/{urllib.parse.quote(parent, safe='')}/applications/{urllib.parse.quote(application, safe='')}/start",
            "service-application-start",
        ),
    )
    attempts: list[dict[str, Any]] = []
    for method, endpoint, endpoint_scope in endpoints:
        response = _http(
            controller,
            method,
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        receipt = {
            "method": method,
            "endpoint": endpoint,
            "status": response["status"],
            "ok": response["ok"],
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "endpoint_scope": endpoint_scope,
            "cleanup_scope": "existing-helper-mimic-restart",
            "parent_service_uuid": parent,
            "target_helper_service": helper,
            "target_application_uuid": application,
            "post_restart_health_poll_performed": False,
        }
        attempts.append(receipt)
        observations.append(
            {key: receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")}
        )
        if response["ok"]:
            return {
                **receipt,
                "attempts": attempts,
                "reason": None,
            }

    return {
        **attempts[-1],
        "ok": False,
        "attempts": attempts,
        "reason": "helper-child-restart-failed",
    }


def _run_docker_orphan_container_cleanup(
    *,
    private_state: PrivateStateReadResult,
    network: str,
    controller: CoolifyController,
    controller_id: str,
    parent_service_uuid: str,
    node: str,
    helper_names: tuple[str, ...],
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    controller_config = _controller_config(
        private_state,
        network=network,
        controller_id=controller_id,
    )
    service_name = f"mother-helper-orphan-cleanup-{_uuid(parent_service_uuid, 'service_uuid')[:8]}"
    service_uuid: str | None = None
    create_receipt: dict[str, Any] | None = None
    start_receipt: dict[str, Any] | None = None
    health_result: dict[str, Any] | None = None
    delete_receipt: dict[str, Any] | None = None
    try:
        environment_uuid = _resolve_environment_uuid(
            controller=controller,
            controller_id=controller_id,
            endpoint=f"/api/v1/projects/{urllib.parse.quote(str(controller_config['project_uuid']), safe='')}/environments",
            expected_name="mainnet",
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            observations=observations,
        )
        compose = _docker_orphan_cleanup_compose(
            service_name=service_name,
            parent_service_uuid=parent_service_uuid,
            node=node,
            helper_names=helper_names,
        )
        body = _temporary_service_body(controller_config, service_name, compose)
        body["environment_uuid"] = environment_uuid
        create_response = _http(
            controller,
            "POST",
            "/api/v1/services",
            body=body,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        create_receipt = {
            "method": "POST",
            "endpoint": "/api/v1/services",
            "status": create_response["status"],
            "ok": create_response["ok"],
            "response_sha256": create_response["response_sha256"],
            "byte_length": create_response["byte_length"],
            "elapsed_ms": create_response["elapsed_ms"],
            "service_name": service_name,
            "request_body_sha256": hashlib.sha256(canonical_json(body)).hexdigest(),
            "cleanup_scope": "docker-orphan-containers",
        }
        observations.append({key: create_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
        if not create_response["ok"]:
            return {
                "ok": False,
                "service_name": service_name,
                "create": create_receipt,
                "start": None,
                "health": None,
                "delete": None,
                "reason": "create-failed",
            }
        service_uuid = _application_uuid(create_response.get("payload"))
        create_receipt["service_uuid"] = service_uuid
        start_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}/start"
        start_response = _http(
            controller,
            "POST",
            start_endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        start_receipt = {
            "method": "POST",
            "endpoint": start_endpoint,
            "status": start_response["status"],
            "ok": start_response["ok"],
            "response_sha256": start_response["response_sha256"],
            "byte_length": start_response["byte_length"],
            "elapsed_ms": start_response["elapsed_ms"],
            "service_uuid": service_uuid,
            "service_name": service_name,
            "cleanup_scope": "docker-orphan-containers",
        }
        observations.append({key: start_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
        if not start_response["ok"]:
            return {
                "ok": False,
                "service_name": service_name,
                "service_uuid": service_uuid,
                "create": create_receipt,
                "start": start_receipt,
                "health": None,
                "delete": None,
                "reason": "start-failed",
            }
        health_result = _wait_for_temporary_service_health(
            controller=controller,
            controller_id=controller_id,
            service_uuid=service_uuid,
            service_name=service_name,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            # Coolify service-detail status can lag or misclassify this
            # Docker-socket helper even after the container is healthy. Treat
            # this poll as advisory and cap it; the authoritative success gate
            # is the final parent-service recheck below.
            max_wait_seconds=min(max_wait_seconds, 30.0),
            poll_interval_seconds=poll_interval_seconds,
            opener=opener,
            observations=observations,
        )
        return {
            "ok": health_result.get("healthy") is True,
            "service_name": service_name,
            "service_uuid": service_uuid,
            "create": create_receipt,
            "start": start_receipt,
            "health": health_result,
            "delete": None,
            "cleanup_scope": "docker-orphan-containers",
            "reason": None if health_result.get("healthy") is True else health_result.get("reason", "health-failed"),
        }
    finally:
        if service_uuid is not None:
            endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
            response = _http(
                controller,
                "DELETE",
                endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            delete_receipt = {
                "method": "DELETE",
                "endpoint": endpoint,
                "status": response["status"],
                "ok": response["ok"] or response["status"] in {404},
                "response_sha256": response["response_sha256"],
                "byte_length": response["byte_length"],
                "elapsed_ms": response["elapsed_ms"],
                "service_uuid": service_uuid,
                "service_name": service_name,
                "cleanup_scope": "temporary-cleanup-service-delete",
            }
            observations.append({key: delete_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
            # A failed temporary service delete is surfaced in the returned payload by
            # mutating the in-flight health result; callers must not call the whole
            # cleanup clean unless the final parent status also verifies clean.
            if health_result is not None:
                health_result["temporary_service_delete"] = delete_receipt

def inspect_completed_mother_helper_cleanup(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    service_uuid: str,
    node: str,
    required_component_names: tuple[str, ...] = DEFAULT_REQUIRED_COMPONENT_NAMES,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    opener: Any = urllib.request.urlopen,
    operation: OperationIdentity,
) -> dict[str, Any]:
    op = _operation(operation)
    network_id = _identifier(network, "network")
    controller_name = _identifier(controller_id, "controller_id")
    service = _uuid(service_uuid, "service_uuid")
    node_name = _identifier(node, "node")
    required = tuple(_identifier(name, "required_component_name") for name in required_component_names)
    request_timeout = _positive(timeout, "timeout")
    response_limit = int(max_response_bytes)
    if response_limit <= 0:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_INVALID_ARGUMENT",
            "max_response_bytes must be positive",
        )

    controller = resolve_coolify_controller(
        private_state,
        network_id,
        controller_name,
        require_enabled=True,
        require_token=True,
    )
    detail = _service_detail(
        controller,
        service,
        timeout=request_timeout,
        max_response_bytes=response_limit,
        opener=opener,
    )
    components = _component_summary(
        payload=detail["payload"],
        node=node_name,
        required_component_names=required,
    )
    summary = {
        **components["summary"],
        "live_mutation_performed": False,
        "application_delete_count": 0,
        "service_detail_http_status": detail["status"],
    }
    status = "pass" if summary["clean"] else "manual-review-required"
    return {
        "kind": _KIND,
        "schema_version": 1,
        "status": status,
        "network": network_id,
        "controller_id": controller_name,
        "service_uuid": service,
        "node": node_name,
        "operation_id": op.operation_id,
        "observed_at": _utc_now(),
        "mode": "inspect",
        "http_observations": [
            {
                "method": "GET",
                "endpoint": f"/api/v1/services/{service}",
                "status": detail["status"],
                "ok": detail["ok"],
                "response_sha256": detail["response_sha256"],
                "byte_length": detail["byte_length"],
                "elapsed_ms": detail["elapsed_ms"],
            }
        ],
        "parent": components["parent"],
        "required_components": components["required_components"],
        "completed_helper_candidates": components["completed_helper_candidates"],
        "running_or_nonterminal_completed_helpers": components["running_or_nonterminal_completed_helpers"],
        "preserved_helpers": components["preserved_helpers"],
        "excluded_completed_helper_records": components["excluded_completed_helper_records"],
        "unexpected_terminal_components": components["unexpected_terminal_components"],
        "unclassified_unhealthy_components": components["unclassified_unhealthy_components"],
        "summary": summary,
    }


def _delete_application(
    controller: CoolifyController,
    application_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    app_uuid = _uuid(application_uuid, "application_uuid")
    endpoint = f"/api/v1/applications/{urllib.parse.quote(app_uuid, safe='')}"
    response = _http(
        controller,
        "DELETE",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    return {
        "method": "DELETE",
        "endpoint": endpoint,
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "application_uuid": app_uuid,
        "delete_scope": "application",
    }


def _delete_service_application(
    controller: CoolifyController,
    service_uuid: str,
    application_uuid: str,
    *,
    endpoint_style: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "service_uuid")
    app_uuid = _uuid(application_uuid, "application_uuid")
    if endpoint_style == "plural":
        endpoint = (
            f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
            f"/applications/{urllib.parse.quote(app_uuid, safe='')}"
        )
    elif endpoint_style == "singular":
        endpoint = (
            f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
            f"/application/{urllib.parse.quote(app_uuid, safe='')}"
        )
    else:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_INVALID_ARGUMENT",
            "invalid nested application delete endpoint style",
        )
    response = _http(
        controller,
        "DELETE",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    return {
        "method": "DELETE",
        "endpoint": endpoint,
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "service_uuid": service,
        "application_uuid": app_uuid,
        "endpoint_style": endpoint_style,
        "delete_scope": "service-application",
    }


def _delete_service_application_with_fallbacks(
    controller: CoolifyController,
    service_uuid: str,
    application_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for endpoint_style in ("plural", "singular"):
        receipt = _delete_service_application(
            controller,
            service_uuid,
            application_uuid,
            endpoint_style=endpoint_style,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        attempts.append(receipt)
        if receipt["ok"]:
            return {
                **receipt,
                "attempts": attempts,
            }
    return {
        **attempts[-1],
        "attempts": attempts,
    }


def _patch_application_status_exclusion(
    controller: CoolifyController,
    service_uuid: str,
    application_uuid: str,
    *,
    endpoint_scope: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "service_uuid")
    app_uuid = _uuid(application_uuid, "application_uuid")
    if endpoint_scope == "application":
        endpoint = f"/api/v1/applications/{urllib.parse.quote(app_uuid, safe='')}"
    elif endpoint_scope == "service-applications":
        endpoint = (
            f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
            f"/applications/{urllib.parse.quote(app_uuid, safe='')}"
        )
    elif endpoint_scope == "service-application":
        endpoint = (
            f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
            f"/application/{urllib.parse.quote(app_uuid, safe='')}"
        )
    else:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_INVALID_ARGUMENT",
            "invalid status exclusion endpoint scope",
        )

    body = {"exclude_from_status": True}
    response = _http(
        controller,
        "PATCH",
        endpoint,
        body=body,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    return {
        "method": "PATCH",
        "endpoint": endpoint,
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "service_uuid": service,
        "application_uuid": app_uuid,
        "endpoint_scope": endpoint_scope,
        "patch_scope": "completed-helper-status-exclusion",
        "body_sha256": hashlib.sha256(canonical_json(body)).hexdigest(),
    }


def _patch_application_status_exclusion_with_fallbacks(
    controller: CoolifyController,
    service_uuid: str,
    application_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for endpoint_scope in ("application", "service-applications", "service-application"):
        receipt = _patch_application_status_exclusion(
            controller,
            service_uuid,
            application_uuid,
            endpoint_scope=endpoint_scope,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        attempts.append(receipt)
        if receipt["ok"]:
            return {
                **receipt,
                "attempts": attempts,
            }
    return {
        **attempts[-1],
        "attempts": attempts,
    }


def execute_completed_mother_helper_cleanup(
    paths: Any,
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    service_uuid: str,
    node: str,
    acknowledged_service_uuid: str,
    required_component_names: tuple[str, ...] = DEFAULT_REQUIRED_COMPONENT_NAMES,
    max_wait_seconds: float = 120.0,
    poll_interval_seconds: float = 5.0,
    allow_compose_rewrite: bool = False,
    instant_deploy_compose_rewrite: bool = False,
    allow_nested_application_delete: bool = False,
    allow_compose_reconcile_refresh: bool = False,
    instant_deploy_compose_reconcile_refresh: bool = False,
    allow_service_redeploy_refresh: bool = False,
    force_service_redeploy_refresh: bool = True,
    allow_docker_orphan_container_cleanup: bool = False,
    allow_coolify_model_status_exclusion: bool = False,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    opener: Any = urllib.request.urlopen,
    operation: OperationIdentity,
) -> dict[str, Any]:
    op = _operation(operation)
    network_id = _identifier(network, "network")
    controller_name = _identifier(controller_id, "controller_id")
    service = _uuid(service_uuid, "service_uuid")
    node_name = _identifier(node, "node")
    if _uuid(acknowledged_service_uuid, "acknowledged_service_uuid") != service:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_ACK_REQUIRED",
            "acknowledged service UUID must match the target service UUID",
        )
    required = tuple(_identifier(name, "required_component_name") for name in required_component_names)
    request_timeout = _positive(timeout, "timeout")
    response_limit = int(max_response_bytes)
    if response_limit <= 0:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_INVALID_ARGUMENT",
            "max_response_bytes must be positive",
        )
    wait_limit = _nonnegative(max_wait_seconds, "max_wait_seconds")
    poll_interval = _nonnegative(poll_interval_seconds, "poll_interval_seconds")

    controller = resolve_coolify_controller(
        private_state,
        network_id,
        controller_name,
        require_enabled=True,
        require_token=True,
    )

    observations: list[dict[str, Any]] = []
    initial_detail = _service_detail(
        controller,
        service,
        timeout=request_timeout,
        max_response_bytes=response_limit,
        opener=opener,
    )
    observations.append(
        {
            "method": "GET",
            "endpoint": f"/api/v1/services/{service}",
            "status": initial_detail["status"],
            "ok": initial_detail["ok"],
            "response_sha256": initial_detail["response_sha256"],
            "byte_length": initial_detail["byte_length"],
            "elapsed_ms": initial_detail["elapsed_ms"],
        }
    )
    initial = _component_summary(
        payload=initial_detail["payload"],
        node=node_name,
        required_component_names=required,
    )

    delete_receipts: list[dict[str, Any]] = []
    for candidate in initial["completed_helper_candidates"]:
        app_uuid = candidate.get("uuid", "")
        receipt = _delete_application(
            controller,
            app_uuid,
            timeout=request_timeout,
            max_response_bytes=response_limit,
            opener=opener,
        )
        receipt["application_name"] = candidate.get("name", "")
        delete_receipts.append(receipt)
        observations.append({key: receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})

    nested_delete_receipts: list[dict[str, Any]] = []
    delete_ok = all(item["ok"] for item in delete_receipts)
    if delete_receipts and not delete_ok and allow_nested_application_delete:
        by_uuid = {
            candidate.get("uuid", ""): candidate
            for candidate in initial["completed_helper_candidates"]
            if type(candidate.get("uuid")) is str and candidate.get("uuid")
        }
        for receipt in delete_receipts:
            if receipt["ok"]:
                continue
            app_uuid = receipt.get("application_uuid", "")
            candidate = by_uuid.get(app_uuid, {})
            nested_receipt = _delete_service_application_with_fallbacks(
                controller,
                service,
                app_uuid,
                timeout=request_timeout,
                max_response_bytes=response_limit,
                opener=opener,
            )
            nested_receipt["application_name"] = candidate.get("name", "")
            nested_delete_receipts.append(nested_receipt)
            for attempt in nested_receipt.get("attempts", []):
                observations.append({key: attempt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})

    nested_delete_ok = bool(nested_delete_receipts) and all(item["ok"] for item in nested_delete_receipts)

    compose_rewrite: dict[str, Any] | None = None
    if delete_receipts and not (delete_ok or nested_delete_ok) and allow_compose_rewrite:
        helper_names = tuple(
            item.get("name", "")
            for item in initial["completed_helper_candidates"]
            if type(item.get("name")) is str and item.get("name")
        )
        compose_attempts: list[dict[str, Any]] = []
        best_candidate: tuple[str, str, str, tuple[str, ...], tuple[str, ...]] | None = None
        for compose_text, compose_source_field, compose_source_encoding in _compose_text_candidates_from_service_payload(initial_detail["payload"]):
            source_digest = hashlib.sha256(compose_text.encode("utf-8")).hexdigest()
            try:
                cleaned_compose, removed_services, removed_helpers = _remove_completed_helpers_from_compose(
                    compose_text,
                    helper_names,
                )
            except MotherDeploymentCompletedHelperCleanupError as exc:
                compose_attempts.append(
                    {
                        "source_field": compose_source_field,
                        "source_encoding": compose_source_encoding,
                        "source_sha256": source_digest,
                        "ok": False,
                        "error_code": exc.code,
                        "removed_service_count": 0,
                        "removed_helper_count": 0,
                    }
                )
                continue

            compose_attempts.append(
                {
                    "source_field": compose_source_field,
                    "source_encoding": compose_source_encoding,
                    "source_sha256": source_digest,
                    "ok": True,
                    "removed_service_names": list(removed_services),
                    "removed_helper_names": list(removed_helpers),
                    "removed_service_count": len(removed_services),
                    "removed_helper_count": len(removed_helpers),
                }
            )
            if best_candidate is None or len(removed_helpers) > len(best_candidate[4]):
                best_candidate = (
                    cleaned_compose,
                    compose_source_field,
                    compose_source_encoding,
                    removed_services,
                    removed_helpers,
                )

        if best_candidate is None:
            compose_rewrite = {
                "method": "PATCH",
                "endpoint": f"/api/v1/services/{service}",
                "status": None,
                "ok": False,
                "service_uuid": service,
                "instant_deploy": bool(instant_deploy_compose_rewrite),
                "error_code": "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_NO_MATCH",
                "error_message": "no completed helper service names were present in any Coolify compose field",
                "compose_source_attempts": compose_attempts,
                "removed_service_names": [],
                "removed_helper_names": [],
                "removed_service_count": 0,
                "removed_helper_count": 0,
            }
        else:
            cleaned_compose, compose_source_field, compose_source_encoding, removed_services, removed_helpers = best_candidate
            patch_receipt = _patch_service_compose(
                controller,
                service,
                cleaned_compose,
                instant_deploy=instant_deploy_compose_rewrite,
                timeout=request_timeout,
                max_response_bytes=response_limit,
                opener=opener,
            )
            observations.append({key: patch_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
            compose_rewrite = {
                **patch_receipt,
                "source_field": compose_source_field,
                "source_encoding": compose_source_encoding,
                "compose_source_attempts": compose_attempts,
                "removed_service_names": list(removed_services),
                "removed_helper_names": list(removed_helpers),
                "removed_service_count": len(removed_services),
                "removed_helper_count": len(removed_helpers),
            }

    service_compose_reconcile: dict[str, Any] | None = None
    if (
        delete_receipts
        and not (delete_ok or nested_delete_ok)
        and not (compose_rewrite is not None and compose_rewrite.get("ok") is True)
        and allow_compose_reconcile_refresh
    ):
        helper_names = tuple(
            item.get("name", "")
            for item in initial["completed_helper_candidates"]
            if type(item.get("name")) is str and item.get("name")
        )
        service_compose_reconcile = _patch_service_compose_reconcile(
            controller,
            service,
            initial_detail["payload"],
            helper_names,
            instant_deploy=instant_deploy_compose_reconcile_refresh,
            timeout=request_timeout,
            max_response_bytes=response_limit,
            opener=opener,
        )
        if service_compose_reconcile.get("status") is not None:
            observations.append(
                {
                    key: service_compose_reconcile[key]
                    for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")
                }
            )

    service_redeploy_refresh: dict[str, Any] | None = None
    if (
        delete_receipts
        and not (delete_ok or nested_delete_ok)
        and not (compose_rewrite is not None and compose_rewrite.get("ok") is True)
        and not (service_compose_reconcile is not None and service_compose_reconcile.get("ok") is True)
        and allow_service_redeploy_refresh
    ):
        service_redeploy_refresh = _request_service_redeploy_refresh(
            controller,
            service,
            force=bool(force_service_redeploy_refresh),
            timeout=request_timeout,
            max_response_bytes=response_limit,
            opener=opener,
        )
        observations.append(
            {
                key: service_redeploy_refresh[key]
                for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")
            }
        )

    status_exclusion_receipts: list[dict[str, Any]] = []
    status_exclusion_attempted_uuids: set[str] = set()

    def _status_exclusion_candidates(components: Mapping[str, Any]) -> list[dict[str, Any]]:
        excluded_by_uuid = {
            item.get("uuid", "")
            for item in components.get("excluded_completed_helper_records", [])
            if isinstance(item, Mapping) and type(item.get("uuid")) is str and item.get("uuid")
        }
        candidates: list[dict[str, Any]] = []
        for item in components.get("completed_helper_candidates", []):
            if not isinstance(item, Mapping):
                continue
            app_uuid = item.get("uuid")
            if type(app_uuid) is not str or not app_uuid:
                continue
            if app_uuid in excluded_by_uuid or app_uuid in status_exclusion_attempted_uuids:
                continue
            if not _is_completed_helper_name(item.get("name")):
                continue
            if not _terminal_completed(_status(item.get("status"))):
                continue
            candidates.append(dict(item))
        return candidates

    def _attempt_status_exclusion(
        components: Mapping[str, Any],
        *,
        remediation_phase: str,
    ) -> list[dict[str, Any]]:
        receipts: list[dict[str, Any]] = []
        for candidate in _status_exclusion_candidates(components):
            app_uuid = candidate["uuid"]
            status_exclusion_attempted_uuids.add(app_uuid)
            receipt = _patch_application_status_exclusion_with_fallbacks(
                controller,
                service,
                app_uuid,
                timeout=request_timeout,
                max_response_bytes=response_limit,
                opener=opener,
            )
            receipt["application_name"] = candidate.get("name", "")
            receipt["application_status"] = candidate.get("status", "")
            receipt["application_exclude_from_status"] = candidate.get("exclude_from_status", False)
            receipt["remediation_phase"] = remediation_phase
            status_exclusion_receipts.append(receipt)
            receipts.append(receipt)
            for attempt in receipt.get("attempts", []):
                observations.append(
                    {
                        key: attempt[key]
                        for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")
                    }
                )
        return receipts

    if (
        allow_coolify_model_status_exclusion
        and initial["completed_helper_candidates"]
        and not (delete_ok or nested_delete_ok)
    ):
        _attempt_status_exclusion(initial, remediation_phase="initial-service-detail")

    docker_orphan_container_cleanup: dict[str, Any] | None = None
    if delete_receipts and allow_docker_orphan_container_cleanup:
        helper_names = tuple(
            item.get("name", "")
            for item in initial["completed_helper_candidates"]
            if type(item.get("name")) is str and item.get("name")
        )
        docker_orphan_container_cleanup = _run_docker_orphan_container_cleanup(
            private_state=private_state,
            network=network_id,
            controller=controller,
            controller_id=controller_name,
            parent_service_uuid=service,
            node=node_name,
            helper_names=helper_names,
            timeout=request_timeout,
            max_response_bytes=response_limit,
            max_wait_seconds=wait_limit,
            poll_interval_seconds=poll_interval,
            opener=opener,
            observations=observations,
        )

    final_detail = initial_detail
    final = initial
    redeploy_after_unclean_refresh_attempted = False
    status_exclusion_redeploy_refresh_attempted = False
    started = time.monotonic()
    while True:
        final_detail = _service_detail(
            controller,
            service,
            timeout=request_timeout,
            max_response_bytes=response_limit,
            opener=opener,
        )
        observations.append(
            {
                "method": "GET",
                "endpoint": f"/api/v1/services/{service}",
                "status": final_detail["status"],
                "ok": final_detail["ok"],
                "response_sha256": final_detail["response_sha256"],
                "byte_length": final_detail["byte_length"],
                "elapsed_ms": final_detail["elapsed_ms"],
            }
        )
        final = _component_summary(
            payload=final_detail["payload"],
            node=node_name,
            required_component_names=required,
        )
        if final["summary"]["clean"]:
            break
        live_status_exclusions: list[dict[str, Any]] = []
        if allow_coolify_model_status_exclusion:
            live_status_exclusions = _attempt_status_exclusion(
                final,
                remediation_phase="live-service-recheck",
            )
        if (
            not status_exclusion_redeploy_refresh_attempted
            and service_redeploy_refresh is None
            and allow_service_redeploy_refresh
            and any(item.get("ok") is True for item in status_exclusion_receipts)
        ):
            service_redeploy_refresh = _request_service_redeploy_refresh(
                controller,
                service,
                force=bool(force_service_redeploy_refresh),
                timeout=request_timeout,
                max_response_bytes=response_limit,
                opener=opener,
            )
            observations.append(
                {
                    key: service_redeploy_refresh[key]
                    for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")
                }
            )
            status_exclusion_redeploy_refresh_attempted = True
            started = time.monotonic()
            if poll_interval > 0:
                time.sleep(min(poll_interval, wait_limit))
            continue
        if live_status_exclusions and any(item.get("ok") is True for item in live_status_exclusions):
            started = time.monotonic()
            if poll_interval > 0:
                time.sleep(min(poll_interval, wait_limit))
            continue
        if (
            not redeploy_after_unclean_refresh_attempted
            and service_redeploy_refresh is None
            and allow_service_redeploy_refresh
            and delete_receipts
            and not (delete_ok or nested_delete_ok)
            and service_compose_reconcile is not None
            and service_compose_reconcile.get("ok") is True
        ):
            service_redeploy_refresh = _request_service_redeploy_refresh(
                controller,
                service,
                force=bool(force_service_redeploy_refresh),
                timeout=request_timeout,
                max_response_bytes=response_limit,
                opener=opener,
            )
            observations.append(
                {
                    key: service_redeploy_refresh[key]
                    for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")
                }
            )
            redeploy_after_unclean_refresh_attempted = True
            started = time.monotonic()
            if poll_interval > 0:
                time.sleep(min(poll_interval, wait_limit))
            continue
        if time.monotonic() - started >= wait_limit:
            break
        if poll_interval > 0:
            time.sleep(min(poll_interval, max(0.0, wait_limit - (time.monotonic() - started))))
        else:
            break

    coolify_model_status_exclusion: dict[str, Any] | None = None
    if allow_coolify_model_status_exclusion:
        coolify_model_status_exclusion = {
            "enabled": True,
            "status": "attempted" if status_exclusion_receipts else "no-unexcluded-terminal-helper-candidates",
            "ok": bool(status_exclusion_receipts) and all(item["ok"] for item in status_exclusion_receipts),
            "receipts": status_exclusion_receipts,
            "patched_application_count": len(status_exclusion_receipts),
            "patched_application_success_count": sum(1 for item in status_exclusion_receipts if item["ok"]),
            "patched_application_names": [
                item.get("application_name", "") for item in status_exclusion_receipts if item.get("application_name")
            ],
            "patched_application_uuids": [
                item.get("application_uuid", "") for item in status_exclusion_receipts if item.get("application_uuid")
            ],
            "patch_scope": "completed-helper-status-exclusion",
        }

    # The temporary Coolify service status is advisory only. We observed
    # Coolify reporting the helper as exited/starting:unhealthy while the
    # underlying Docker container was running:healthy and had completed its
    # proof. Credit the Docker orphan cleanup only when the authoritative
    # parent recheck is clean after the helper was successfully started.
    if (
        docker_orphan_container_cleanup is not None
        and docker_orphan_container_cleanup.get("ok") is not True
        and isinstance(docker_orphan_container_cleanup.get("start"), Mapping)
        and docker_orphan_container_cleanup["start"].get("ok") is True
        and final["summary"]["clean"] is True
    ):
        docker_orphan_container_cleanup["ok"] = True
        docker_orphan_container_cleanup["reason"] = None
        docker_orphan_container_cleanup["verification"] = {
            "source": "final-parent-service-recheck",
            "parent_status": final["parent"].get("status"),
            "clean": True,
            "temporary_service_status_advisory": True,
        }

    compose_rewrite_ok = compose_rewrite is not None and compose_rewrite.get("ok") is True
    service_compose_reconcile_ok = service_compose_reconcile is not None and service_compose_reconcile.get("ok") is True
    service_redeploy_refresh_ok = service_redeploy_refresh is not None and service_redeploy_refresh.get("ok") is True
    docker_orphan_container_cleanup_ok = (
        docker_orphan_container_cleanup is not None and docker_orphan_container_cleanup.get("ok") is True
    )
    coolify_model_status_exclusion_attempt_count = len(status_exclusion_receipts)
    coolify_model_status_exclusion_ok = (
        coolify_model_status_exclusion_attempt_count > 0
        and coolify_model_status_exclusion is not None
        and coolify_model_status_exclusion.get("ok") is True
    )
    mutation_ok = (
        (delete_ok if delete_receipts else True)
        or nested_delete_ok
        or compose_rewrite_ok
        or service_compose_reconcile_ok
        or service_redeploy_refresh_ok
        or docker_orphan_container_cleanup_ok
        or coolify_model_status_exclusion_ok
    )
    summary = {
        **final["summary"],
        "initial_parent_status_clean": initial["summary"]["parent_status_clean"],
        "initial_completed_helper_candidate_count": initial["summary"]["completed_helper_candidate_count"],
        "application_delete_count": len(delete_receipts),
        "application_delete_success_count": sum(1 for item in delete_receipts if item["ok"]),
        "all_delete_requests_succeeded": delete_ok,
        "nested_application_delete_count": len(nested_delete_receipts),
        "nested_application_delete_success_count": sum(1 for item in nested_delete_receipts if item["ok"]),
        "all_nested_application_delete_requests_succeeded": nested_delete_ok,
        "nested_application_delete_enabled": bool(allow_nested_application_delete),
        "service_compose_rewrite_count": 1 if compose_rewrite is not None else 0,
        "service_compose_rewrite_succeeded": compose_rewrite_ok,
        "service_compose_rewrite_instant_deploy": bool(
            compose_rewrite is not None and compose_rewrite.get("instant_deploy") is True
        ),
        "service_compose_reconcile_count": 1 if service_compose_reconcile is not None else 0,
        "service_compose_reconcile_succeeded": bool(
            service_compose_reconcile is not None and service_compose_reconcile.get("ok") is True
        ),
        "service_compose_reconcile_instant_deploy": bool(
            service_compose_reconcile is not None and service_compose_reconcile.get("instant_deploy") is True
        ),
        "service_compose_reconcile_enabled": bool(allow_compose_reconcile_refresh),
        "service_redeploy_requested": bool(
            (compose_rewrite is not None and compose_rewrite.get("instant_deploy") is True)
            or (service_compose_reconcile is not None and service_compose_reconcile.get("instant_deploy") is True)
            or service_redeploy_refresh is not None
        ),
        "service_redeploy_refresh_count": 1 if service_redeploy_refresh is not None else 0,
        "service_redeploy_refresh_succeeded": service_redeploy_refresh_ok,
        "service_redeploy_refresh_force": bool(
            service_redeploy_refresh is not None and service_redeploy_refresh.get("force") is True
        ),
        "service_redeploy_refresh_enabled": bool(allow_service_redeploy_refresh),
        "docker_orphan_container_cleanup_count": 1 if docker_orphan_container_cleanup is not None else 0,
        "docker_orphan_container_cleanup_succeeded": docker_orphan_container_cleanup_ok,
        "docker_orphan_container_cleanup_enabled": bool(allow_docker_orphan_container_cleanup),
        "coolify_model_status_exclusion_count": coolify_model_status_exclusion_attempt_count,
        "coolify_model_status_exclusion_succeeded": coolify_model_status_exclusion_ok,
        "coolify_model_status_exclusion_enabled": bool(allow_coolify_model_status_exclusion),
        "cleanup_mutation_succeeded": mutation_ok,
        "live_mutation_performed": bool(
            delete_receipts
            or nested_delete_receipts
            or compose_rewrite is not None
            or service_compose_reconcile is not None
            or service_redeploy_refresh is not None
            or docker_orphan_container_cleanup is not None
            or bool(status_exclusion_receipts)
        ),
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "service_detail_http_status": final_detail["status"],
        "unresolved_completed_helper_count": len(final["completed_helper_candidates"]),
        "unresolved_completed_helper_names": [
            item.get("name", "") for item in final["completed_helper_candidates"] if item.get("name")
        ],
        "unresolved_completed_helper_uuids": [
            item.get("uuid", "") for item in final["completed_helper_candidates"] if item.get("uuid")
        ],
        "unresolved_completed_helper_statuses": [
            {
                "name": item.get("name", ""),
                "uuid": item.get("uuid", ""),
                "status": item.get("status", ""),
                "exclude_from_status": item.get("exclude_from_status", False),
            }
            for item in final["completed_helper_candidates"]
        ],
    }
    status = "pass" if summary["clean"] and mutation_ok else "manual-review-required"
    document = {
        "kind": _KIND,
        "schema_version": 1,
        "status": status,
        "network": network_id,
        "controller_id": controller_name,
        "service_uuid": service,
        "node": node_name,
        "operation_id": op.operation_id,
        "observed_at": _utc_now(),
        "mode": "execute",
        "initial_parent": initial["parent"],
        "final_parent": final["parent"],
        "initial_completed_helper_candidates": initial["completed_helper_candidates"],
        "deleted_applications": delete_receipts,
        "nested_deleted_applications": nested_delete_receipts,
        "service_compose_rewrite": compose_rewrite,
        "service_compose_reconcile": service_compose_reconcile,
        "service_redeploy_refresh": service_redeploy_refresh,
        "docker_orphan_container_cleanup": docker_orphan_container_cleanup,
        "coolify_model_status_exclusion": coolify_model_status_exclusion,
        "final_required_components": final["required_components"],
        "final_completed_helper_candidates": final["completed_helper_candidates"],
        "unresolved_completed_helper_candidates": final["completed_helper_candidates"],
        "final_preserved_helpers": final["preserved_helpers"],
        "excluded_completed_helper_records": final["excluded_completed_helper_records"],
        "final_excluded_completed_helper_records": final["excluded_completed_helper_records"],
        "final_unexpected_terminal_components": final["unexpected_terminal_components"],
        "final_unclassified_unhealthy_components": final["unclassified_unhealthy_components"],
        "http_observations": observations,
        "summary": summary,
    }
    path, digest = write_completed_mother_helper_cleanup_evidence(
        paths,
        document,
        operation=op,
    )
    return {
        **document,
        "evidence": {
            "path": str(path),
            "sha256": digest,
        },
    }


def write_completed_mother_helper_cleanup_evidence(
    paths: Any,
    document: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    op = _operation(operation)
    payload = canonical_json(dict(document))
    digest = hashlib.sha256(payload).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = Path(paths.root) / "evidence" / _EVIDENCE_SUBDIR / f"{stamp}-{digest[:16]}.json"
    atomic_files.durable_create(target, payload, operation=op)
    return target, digest


def verify_completed_mother_helper_cleanup_evidence(
    paths: Any,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
) -> dict[str, Any]:
    path = Path(evidence_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_EVIDENCE_READ_FAILED",
            "failed to read cleanup evidence",
        ) from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_EVIDENCE_INVALID",
            "cleanup evidence is not JSON",
        ) from exc
    if not isinstance(document, Mapping) or document.get("kind") != _KIND:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_EVIDENCE_INVALID",
            "cleanup evidence kind is invalid",
        )
    digest = hashlib.sha256(canonical_json(dict(document))).hexdigest()
    if digest != hashlib.sha256(raw).hexdigest():
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_EVIDENCE_INVALID",
            "cleanup evidence is not canonical",
        )
    observed = document.get("observed_at")
    if type(observed) is str and observed.endswith("Z"):
        try:
            dt = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MotherDeploymentCompletedHelperCleanupError(
                "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_EVIDENCE_INVALID",
                "cleanup evidence timestamp is invalid",
            ) from exc
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        if age > max_age_seconds:
            raise MotherDeploymentCompletedHelperCleanupError(
                "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_EVIDENCE_EXPIRED",
                "cleanup evidence is expired",
            )
    summary = document.get("summary")
    clean = isinstance(summary, Mapping) and summary.get("clean") is True
    return {
        "clean": clean,
        "status": document.get("status"),
        "evidence_sha256": digest,
        "service_uuid": document.get("service_uuid"),
        "node": document.get("node"),
        "summary": dict(summary) if isinstance(summary, Mapping) else {},
    }


__all__ = [
    "COMPLETED_HELPER_NAMES",
    "DEFAULT_REQUIRED_COMPONENT_NAMES",
    "MotherDeploymentCompletedHelperCleanupError",
    "execute_completed_mother_helper_cleanup",
    "inspect_completed_mother_helper_cleanup",
    "verify_completed_mother_helper_cleanup_evidence",
    "write_completed_mother_helper_cleanup_evidence",
]
