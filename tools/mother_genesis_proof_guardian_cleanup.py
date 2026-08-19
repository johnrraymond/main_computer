#!/usr/bin/env python3
"""Standalone retired genesis proof guardian shim cleanup.

This tool intentionally handles one thing only: installing or verifying the
known inert ``mother-genesis-proof-guardian`` retired health shim.  It does not
clean admission voters, activation guardians, replica helpers, Docker host
containers, QBFT votes, or generic post-work helper candidates.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Mapping
import urllib.request

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import resolve_coolify_controller
from tools.mother.common.deployment_completed_helper_cleanup import (
    MotherDeploymentCompletedHelperCleanupError,
    _compose_text_candidates_from_service_payload,
    _patch_service_compose,
    _service_detail,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state


KIND = "main_computer.mother.genesis_proof_guardian_cleanup.v1"
EVIDENCE_SUBDIR = "mother-genesis-proof-guardian-cleanup"

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")

SHIM_NAME = "mother-genesis-proof-guardian"
SHIM_IMAGE = "alpine:3.20"
SHIM_LABEL = "main_computer.mother.post_work_shim"


class MotherGenesisProofGuardianCleanupError(RuntimeError):
    """Genesis proof guardian cleanup failed before a trustworthy result."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


ProgressCallback = Callable[[str, str, Mapping[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return ""


def _status(value: object) -> str:
    return _safe_scalar(value).strip().lower()


def _healthy(status: str) -> bool:
    return "healthy" in status and "unhealthy" not in status


def _labels(value: object) -> dict[str, str]:
    if isinstance(value, Mapping):
        return {str(key): _safe_scalar(item) for key, item in value.items()}
    if isinstance(value, list):
        labels: dict[str, str] = {}
        for item in value:
            if type(item) is not str:
                continue
            key, sep, raw_value = item.partition("=")
            if sep:
                labels[key.strip()] = raw_value.strip()
            elif key.strip():
                labels[key.strip()] = "true"
        return labels
    return {}


def _truthy_label(labels: Mapping[str, str], key: str) -> bool:
    value = labels.get(key)
    return type(value) is str and value.strip().lower() in {"1", "true", "yes", "y"}


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or not IDENTIFIER_RE.fullmatch(value.strip()):
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_INVALID_ARGUMENT",
            f"invalid {field}",
        )
    return value.strip()


def _uuid(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or not UUID_RE.fullmatch(value.strip()):
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_INVALID_ARGUMENT",
            f"invalid {field}",
        )
    return value.strip()


def _positive(value: float | int, field: str) -> float:
    if type(value) not in {int, float} or value <= 0:
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be positive",
        )
    return float(value)


def _nonnegative(value: float | int, field: str) -> float:
    if type(value) not in {int, float} or value < 0:
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be non-negative",
        )
    return float(value)


def _progress_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    text = str(value)
    if not text:
        return '""'
    if re.search(r"\s", text):
        return json.dumps(text, sort_keys=True)
    return text


def _emit_progress(
    progress: ProgressCallback | None,
    phase: str,
    message: str,
    **details: Any,
) -> None:
    if progress is not None:
        progress(phase, message, details)


def _stderr_progress(quiet: bool = False) -> ProgressCallback | None:
    if quiet:
        return None

    def emit(phase: str, message: str, details: Mapping[str, Any]) -> None:
        suffix = ""
        if details:
            suffix = " " + " ".join(f"{key}={_progress_value(value)}" for key, value in sorted(details.items()))
        print(f"{_utc_now()} mother-genesis-proof-guardian-cleanup.{phase} {message}{suffix}", file=sys.stderr)

    return emit


def _operation(network: str, suffix: str) -> OperationIdentity:
    stamp = _utc_now().replace(":", "").replace("-", "")
    return OperationIdentity(
        operation_id=f"mother-genesis-proof-guardian-cleanup-{suffix}-{stamp}",
        request_id=f"mother-genesis-proof-guardian-cleanup-{suffix}-{stamp}-request",
        network=network,
        operation_kind="MOTHER-OP-ADD-NODE",
    )


def _is_shim_record(item: Mapping[str, Any]) -> bool:
    if item.get("name") != SHIM_NAME:
        return False
    labels = _labels(item.get("labels"))
    status = _status(item.get("status"))
    if _truthy_label(labels, SHIM_LABEL):
        return "unhealthy" not in status
    # Coolify service-detail payloads do not always echo Compose labels.  The
    # real genesis guardian uses python:3.12-alpine; the retired shim is the
    # deliberately tiny alpine:3.20 service installed by this script.
    return _safe_scalar(item.get("image")).strip() == SHIM_IMAGE and _healthy(status)


def _is_shim_definition(name: str, definition: object) -> bool:
    if name != SHIM_NAME or not isinstance(definition, Mapping):
        return False
    labels = _labels(definition.get("labels"))
    image = _safe_scalar(definition.get("image")).strip()
    return _truthy_label(labels, SHIM_LABEL) or image == SHIM_IMAGE


def _shim_service(service_uuid: str) -> dict[str, Any]:
    return {
        "image": SHIM_IMAGE,
        "container_name": f"{SHIM_NAME}-{service_uuid}",
        "command": [
            "sh",
            "-lc",
            (
                "printf '%s\\n' "
                "'retired Mother genesis proof guardian shim; no proof contract is served here' "
                "> /tmp/mother-retired-helper-shim; "
                "while true; do sleep 3600; done"
            ),
        ],
        "healthcheck": {
            "test": ["CMD", "sh", "-lc", "test -f /tmp/mother-retired-helper-shim"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 12,
            "start_period": "5s",
        },
        "restart": "unless-stopped",
        "labels": {
            SHIM_LABEL: "true",
            "main_computer.mother.helper": SHIM_NAME,
            "main_computer.mother.retired_helper_shim": "true",
            "main_computer.mother.not_a_proof_guardian": "true",
        },
    }


def _parse_compose(compose_text: str) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_COMPOSE_INVALID",
            "Coolify docker compose content is not valid YAML",
        ) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("services"), dict):
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_COMPOSE_INVALID",
            "Coolify docker compose content does not contain a services mapping",
        )
    return parsed


def _depends_on_names(value: object) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        return tuple(str(name) for name in value.keys())
    if isinstance(value, list):
        return tuple(str(name) for name in value)
    if type(value) is str and value.strip():
        return (value.strip(),)
    return ()


def _validate_compose_or_raise(compose: Mapping[str, Any], *, node: str) -> None:
    services = compose.get("services")
    if not isinstance(services, Mapping):
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_COMPOSE_INVALID",
            "rewritten compose content does not contain a services mapping",
        )
    if node not in services:
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_MAIN_SERVICE_MISSING",
            f"rewritten compose does not contain the target node service {node!r}",
        )
    if SHIM_NAME not in services:
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_SHIM_MISSING",
            "rewritten compose does not contain mother-genesis-proof-guardian",
        )
    if not _is_shim_definition(SHIM_NAME, services[SHIM_NAME]):
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_SHIM_INVALID",
            "mother-genesis-proof-guardian is not the retired shim definition",
        )

    service_names = {str(name) for name in services.keys()}
    missing: list[dict[str, str]] = []
    for service_name, definition in services.items():
        if not isinstance(definition, Mapping):
            continue
        for dependency in _depends_on_names(definition.get("depends_on")):
            if dependency not in service_names:
                missing.append({"service": str(service_name), "missing_dependency": dependency})
    if missing:
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_COMPOSE_DANGLING_DEPENDS_ON",
            "rewritten compose has depends_on entries whose target services are missing: "
            + json.dumps(missing, sort_keys=True),
        )


def _install_shim_in_compose(
    compose_text: str,
    *,
    service_uuid: str,
    node: str,
) -> tuple[str, bool, tuple[str, ...]]:
    parsed = _parse_compose(compose_text)
    services = parsed["services"]
    before_other_services = {
        name: definition
        for name, definition in services.items()
        if name != SHIM_NAME
    }

    replaced_existing = SHIM_NAME in services
    services[SHIM_NAME] = _shim_service(service_uuid)
    _validate_compose_or_raise(parsed, node=node)

    after_other_services = {
        name: definition
        for name, definition in services.items()
        if name != SHIM_NAME
    }
    if before_other_services != after_other_services:
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_SCOPE_VIOLATION",
            "genesis proof guardian cleanup changed non-target services",
        )

    return yaml.safe_dump(parsed, sort_keys=False), replaced_existing, tuple(str(name) for name in before_other_services.keys())


def _compose_summary(payload: Any, *, node: str) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for compose_text, source_field, source_encoding in _compose_text_candidates_from_service_payload(payload):
        source_sha = hashlib.sha256(compose_text.encode("utf-8")).hexdigest()
        try:
            parsed = _parse_compose(compose_text)
            services = parsed["services"]
            definition = services.get(SHIM_NAME)
            target_declared = SHIM_NAME in services
            target_is_shim = _is_shim_definition(SHIM_NAME, definition)
            node_declared = node in services
            return {
                "ok": True,
                "source_field": source_field,
                "source_encoding": source_encoding,
                "source_sha256": source_sha,
                "service_names": [str(name) for name in services.keys()],
                "node_service_declared": node_declared,
                "target_service_declared": target_declared,
                "target_service_is_retired_shim": target_is_shim,
            }
        except Exception as exc:  # noqa: BLE001 - summarized for inspect evidence
            attempts.append(
                {
                    "ok": False,
                    "source_field": source_field,
                    "source_encoding": source_encoding,
                    "source_sha256": source_sha,
                    "error": str(exc),
                    "error_code": getattr(exc, "code", type(exc).__name__),
                }
            )
    return {
        "ok": False,
        "service_names": [],
        "node_service_declared": False,
        "target_service_declared": False,
        "target_service_is_retired_shim": False,
        "attempts": attempts,
    }


def _service_payload(response: Mapping[str, Any]) -> Any:
    return response.get("json") if "json" in response else response.get("payload")


def _target_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    applications = payload.get("applications")
    if not isinstance(applications, list):
        return []
    records: list[dict[str, Any]] = []
    for item in applications:
        if isinstance(item, Mapping) and item.get("name") == SHIM_NAME:
            records.append(dict(item))
    return records


def _classify_payload(payload: Any, *, node: str) -> dict[str, Any]:
    records = _target_records(payload)
    shim_records = [item for item in records if _is_shim_record(item)]
    compose = _compose_summary(payload, node=node)
    parent_status = _status(payload.get("status")) if isinstance(payload, Mapping) else ""
    return {
        "parent_status": parent_status,
        "target_name": SHIM_NAME,
        "target_record_count": len(records),
        "target_records": [
            {
                "uuid": _safe_scalar(item.get("uuid")),
                "name": _safe_scalar(item.get("name")),
                "status": _safe_scalar(item.get("status")),
                "image": _safe_scalar(item.get("image")),
                "exclude_from_status": item.get("exclude_from_status") is True,
                "is_retired_shim": _is_shim_record(item),
            }
            for item in records
        ],
        "retired_shim_record_count": len(shim_records),
        "compose": compose,
        "summary": {
            "parent_status_healthy": _healthy(parent_status),
            "target_record_present": bool(records),
            "target_record_is_retired_shim": bool(shim_records),
            "compose_target_declared": bool(compose.get("target_service_declared")),
            "compose_target_is_retired_shim": bool(compose.get("target_service_is_retired_shim")),
            "node_service_declared": bool(compose.get("node_service_declared")),
        },
    }


def inspect_genesis_proof_guardian_cleanup(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    service_uuid: str,
    node: str,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    opener: Any = urllib.request.urlopen,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    network_id = _identifier(network, "network")
    controller_name = _identifier(controller_id, "controller_id")
    service = _uuid(service_uuid, "service_uuid")
    node_name = _identifier(node, "node")
    request_timeout = _positive(timeout, "timeout")
    response_limit = int(max_response_bytes)
    if response_limit <= 0:
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_INVALID_ARGUMENT",
            "max_response_bytes must be positive",
        )

    _emit_progress(progress, "inspect", "resolving Coolify controller", controller_id=controller_name, network=network_id)
    controller = resolve_coolify_controller(
        private_state,
        network_id,
        controller_name,
        require_enabled=True,
        require_token=True,
    )

    _emit_progress(progress, "inspect", "fetching service detail", endpoint=f"/api/v1/services/{service}")
    detail = _service_detail(
        controller,
        service,
        timeout=request_timeout,
        max_response_bytes=response_limit,
        opener=opener,
    )
    payload = _service_payload(detail)
    classification = _classify_payload(payload, node=node_name)

    cleanup_required = not (
        classification["summary"]["target_record_is_retired_shim"]
        and classification["summary"]["compose_target_is_retired_shim"]
    )

    return {
        "kind": KIND,
        "observed_at": _utc_now(),
        "mode": "inspect",
        "status": "cleanup-required" if cleanup_required else "clean",
        "network": network_id,
        "controller_id": controller_name,
        "service_uuid": service,
        "node": node_name,
        "target_name": SHIM_NAME,
        "mutation_performed": False,
        "coolify_touched": False,
        "compose_touched": False,
        "docker_touched": False,
        "chain_touched": False,
        "service_detail": {
            "method": "GET",
            "endpoint": f"/api/v1/services/{service}",
            "status": detail.get("status"),
            "ok": detail.get("ok"),
            "response_sha256": detail.get("response_sha256"),
            "byte_length": detail.get("byte_length"),
            "elapsed_ms": detail.get("elapsed_ms"),
        },
        "classification": classification,
        "summary": {
            **classification["summary"],
            "cleanup_required": cleanup_required,
            "only_target_supported": SHIM_NAME,
        },
    }


def execute_genesis_proof_guardian_cleanup(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    service_uuid: str,
    node: str,
    acknowledged_service_uuid: str,
    allow_retired_genesis_proof_guardian_shim: bool,
    instant_deploy: bool = False,
    max_wait_seconds: float = 60.0,
    poll_interval_seconds: float = 5.0,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    opener: Any = urllib.request.urlopen,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    network_id = _identifier(network, "network")
    controller_name = _identifier(controller_id, "controller_id")
    service = _uuid(service_uuid, "service_uuid")
    node_name = _identifier(node, "node")
    if _uuid(acknowledged_service_uuid, "acknowledged_service_uuid") != service:
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_ACK_REQUIRED",
            "acknowledged service UUID must match the target service UUID",
        )
    if not allow_retired_genesis_proof_guardian_shim:
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_SHIM_FLAG_REQUIRED",
            "execute requires --allow-retired-genesis-proof-guardian-shim",
        )

    request_timeout = _positive(timeout, "timeout")
    response_limit = int(max_response_bytes)
    if response_limit <= 0:
        raise MotherGenesisProofGuardianCleanupError(
            "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_INVALID_ARGUMENT",
            "max_response_bytes must be positive",
        )
    wait_limit = _nonnegative(max_wait_seconds, "max_wait_seconds")
    poll_interval = _nonnegative(poll_interval_seconds, "poll_interval_seconds")

    _emit_progress(progress, "execute", "resolving Coolify controller", controller_id=controller_name, network=network_id)
    controller = resolve_coolify_controller(
        private_state,
        network_id,
        controller_name,
        require_enabled=True,
        require_token=True,
    )

    observations: list[dict[str, Any]] = []
    _emit_progress(progress, "execute", "fetching initial service detail", endpoint=f"/api/v1/services/{service}")
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
            "status": initial_detail.get("status"),
            "ok": initial_detail.get("ok"),
            "response_sha256": initial_detail.get("response_sha256"),
            "byte_length": initial_detail.get("byte_length"),
            "elapsed_ms": initial_detail.get("elapsed_ms"),
        }
    )
    initial_payload = _service_payload(initial_detail)
    initial_classification = _classify_payload(initial_payload, node=node_name)

    patch_receipt: dict[str, Any] | None = None
    if not initial_classification["summary"]["compose_target_is_retired_shim"]:
        _emit_progress(progress, "execute.compose", "installing retired genesis proof guardian shim", service_uuid=service)
        compose_attempts: list[dict[str, Any]] = []
        last_error: Exception | None = None
        for compose_text, source_field, source_encoding in _compose_text_candidates_from_service_payload(initial_payload):
            source_sha = hashlib.sha256(compose_text.encode("utf-8")).hexdigest()
            try:
                rewritten, replaced_existing, preserved_services = _install_shim_in_compose(
                    compose_text,
                    service_uuid=service,
                    node=node_name,
                )
            except Exception as exc:  # noqa: BLE001 - collect source attempt diagnostics
                last_error = exc
                compose_attempts.append(
                    {
                        "ok": False,
                        "source_field": source_field,
                        "source_encoding": source_encoding,
                        "source_sha256": source_sha,
                        "error_code": getattr(exc, "code", type(exc).__name__),
                        "error_message": str(exc),
                    }
                )
                continue

            receipt = _patch_service_compose(
                controller,
                service,
                rewritten,
                instant_deploy=instant_deploy,
                timeout=request_timeout,
                max_response_bytes=response_limit,
                opener=opener,
            )
            receipt.update(
                {
                    "source_field": source_field,
                    "source_encoding": source_encoding,
                    "source_sha256": source_sha,
                    "installed_retired_genesis_proof_guardian_shim": True,
                    "retired_genesis_proof_guardian_shim_replaced_existing": replaced_existing,
                    "retired_genesis_proof_guardian_shim_serves_proof": False,
                    "retired_genesis_proof_guardian_shim_mounts_proof_volume": False,
                    "preserved_service_names": list(preserved_services),
                    "compose_source_attempts": [
                        *compose_attempts,
                        {
                            "ok": True,
                            "source_field": source_field,
                            "source_encoding": source_encoding,
                            "source_sha256": source_sha,
                            "replaced_existing": replaced_existing,
                        },
                    ],
                }
            )
            patch_receipt = receipt
            observations.append(
                {
                    "method": receipt.get("method"),
                    "endpoint": receipt.get("endpoint"),
                    "status": receipt.get("status"),
                    "ok": receipt.get("ok"),
                    "response_sha256": receipt.get("response_sha256"),
                    "byte_length": receipt.get("byte_length"),
                    "elapsed_ms": receipt.get("elapsed_ms"),
                }
            )
            if not receipt.get("ok"):
                break
            _emit_progress(progress, "execute.compose", "retired shim compose patch sent", http_status=receipt.get("status"), ok=receipt.get("ok"))
            break

        if patch_receipt is None:
            raise MotherGenesisProofGuardianCleanupError(
                "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_COMPOSE_PATCH_NOT_POSSIBLE",
                "could not build a retired genesis proof guardian shim compose patch"
                + (f": {last_error}" if last_error else ""),
            )
    else:
        _emit_progress(progress, "execute.compose", "retired shim already declared in Compose", service_uuid=service)

    final_detail = None
    final_classification = None
    deadline = time.monotonic() + wait_limit
    poll = 0
    while True:
        poll += 1
        _emit_progress(progress, "execute.poll", "fetching service detail for verification", poll=poll)
        detail = _service_detail(
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
                "status": detail.get("status"),
                "ok": detail.get("ok"),
                "response_sha256": detail.get("response_sha256"),
                "byte_length": detail.get("byte_length"),
                "elapsed_ms": detail.get("elapsed_ms"),
            }
        )
        final_detail = detail
        final_classification = _classify_payload(_service_payload(detail), node=node_name)
        clean = (
            final_classification["summary"]["compose_target_is_retired_shim"]
            and final_classification["summary"]["target_record_is_retired_shim"]
        )
        _emit_progress(
            progress,
            "execute.poll",
            "verification poll classified genesis proof guardian",
            poll=poll,
            clean=clean,
            parent_status=final_classification.get("parent_status"),
            target_records=final_classification.get("target_record_count"),
        )
        if clean or time.monotonic() >= deadline or poll_interval <= 0:
            break
        time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    assert final_detail is not None and final_classification is not None
    clean = (
        final_classification["summary"]["compose_target_is_retired_shim"]
        and final_classification["summary"]["target_record_is_retired_shim"]
    )
    status = "pass" if clean and (patch_receipt is None or patch_receipt.get("ok")) else "manual-review-required"

    result = {
        "kind": KIND,
        "observed_at": _utc_now(),
        "mode": "execute",
        "status": status,
        "network": network_id,
        "controller_id": controller_name,
        "service_uuid": service,
        "node": node_name,
        "target_name": SHIM_NAME,
        "mutation_performed": patch_receipt is not None,
        "coolify_touched": patch_receipt is not None,
        "compose_touched": patch_receipt is not None,
        "docker_touched": False,
        "chain_touched": False,
        "observations": observations,
        "initial_classification": initial_classification,
        "final_classification": final_classification,
        "compose_patch": patch_receipt,
        "summary": {
            "clean": clean,
            "retired_genesis_proof_guardian_shim_installed": bool(
                patch_receipt and patch_receipt.get("installed_retired_genesis_proof_guardian_shim")
            ),
            "retired_genesis_proof_guardian_shim_replaced_existing": bool(
                patch_receipt and patch_receipt.get("retired_genesis_proof_guardian_shim_replaced_existing")
            ),
            "retired_genesis_proof_guardian_shim_serves_proof": False,
            "retired_genesis_proof_guardian_shim_mounts_proof_volume": False,
            "only_target_supported": SHIM_NAME,
            "target_record_is_retired_shim": final_classification["summary"]["target_record_is_retired_shim"],
            "compose_target_is_retired_shim": final_classification["summary"]["compose_target_is_retired_shim"],
        },
    }
    _emit_progress(progress, "execute", "genesis proof guardian cleanup complete", status=status, clean=clean)
    return result


def _load_private_state(runtime_state_root: str | Path, *, network: str, mode: str) -> PrivateStateReadResult:
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root)).resolve_private_state_paths()
    return read_private_state(paths, operation=_operation(network, mode))


def _write_evidence(runtime_state_root: str | Path, result: Mapping[str, Any]) -> Path:
    root = Path(runtime_state_root) / "mother" / "evidence" / EVIDENCE_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    stamp = _utc_now().replace("-", "").replace(":", "")
    service = _safe_scalar(result.get("service_uuid")) or "unknown-service"
    path = root / f"{stamp}-{service}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install or verify the retired mother-genesis-proof-guardian health shim only."
    )
    parser.add_argument("mode", choices=("inspect", "execute"))
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--controller-id", required=True)
    parser.add_argument("--service-uuid", required=True)
    parser.add_argument("--node-name", required=True)
    parser.add_argument("--acknowledge-service-uuid")
    parser.add_argument("--allow-retired-genesis-proof-guardian-shim", action="store_true")
    parser.add_argument("--instant-deploy", action="store_true")
    parser.add_argument("--max-wait-seconds", type=float, default=60.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    parser.add_argument("--write-evidence", action="store_true")
    parser.add_argument("--quiet-progress", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    progress = _stderr_progress(args.quiet_progress)
    try:
        private_state = _load_private_state(args.runtime_state_root, network=args.network, mode=args.mode)
        if args.mode == "inspect":
            result = inspect_genesis_proof_guardian_cleanup(
                private_state,
                network=args.network,
                controller_id=args.controller_id,
                service_uuid=args.service_uuid,
                node=args.node_name,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
                progress=progress,
            )
        else:
            result = execute_genesis_proof_guardian_cleanup(
                private_state,
                network=args.network,
                controller_id=args.controller_id,
                service_uuid=args.service_uuid,
                node=args.node_name,
                acknowledged_service_uuid=args.acknowledge_service_uuid or "",
                allow_retired_genesis_proof_guardian_shim=args.allow_retired_genesis_proof_guardian_shim,
                instant_deploy=args.instant_deploy,
                max_wait_seconds=args.max_wait_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
                progress=progress,
            )
        if args.write_evidence:
            result = dict(result)
            evidence_path = _write_evidence(args.runtime_state_root, result)
            result["evidence_path"] = str(evidence_path)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") in {"pass", "clean", "cleanup-required"} else 2
    except (MotherGenesisProofGuardianCleanupError, MotherDeploymentCompletedHelperCleanupError) as exc:
        code = getattr(exc, "code", "MOTHER_GENESIS_PROOF_GUARDIAN_CLEANUP_FAILED")
        print(
            json.dumps(
                {
                    "kind": KIND,
                    "observed_at": _utc_now(),
                    "status": "failed",
                    "error_code": code,
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
