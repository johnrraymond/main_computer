#!/usr/bin/env python3
"""Standalone retired add-node admission voter shim cleanup.

This tool intentionally handles one thing only: installing or verifying the
known inert ``mother-add-node-validator-admission-voter-<node>`` retired health
shim.  It does not clean genesis helpers, activation guardians, replica helpers,
Docker host containers, QBFT votes, or generic post-work helper candidates.
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


KIND = "main_computer.mother.admission_voter_cleanup.v1"
EVIDENCE_SUBDIR = "mother-admission-voter-cleanup"

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

TARGET_PREFIX = "mother-add-node-validator-admission-voter-"
SHIM_IMAGE = "alpine:3.20"
SHIM_LABEL = "main_computer.mother.post_work_shim"
RETIRED_VOTER_LABEL = "main_computer.mother.retired_admission_voter_shim"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class MotherAdmissionVoterCleanupError(RuntimeError):
    """Admission voter cleanup failed before a trustworthy result."""

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
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be a simple identifier",
        )
    text = value.strip()
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_INVALID_ARGUMENT",
            f"{field} must not contain path syntax",
        )
    return text


def _uuid(value: object, field: str) -> str:
    if type(value) is not str or not UUID_RE.fullmatch(value.strip()):
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be a Coolify UUID-like identifier",
        )
    return value.strip()


def _sha256(value: object, field: str) -> str:
    if type(value) is not str or not SHA256_RE.fullmatch(value.strip()):
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be a SHA-256 hex digest",
        )
    return value.strip().lower()


def _positive(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be numeric",
        ) from exc
    if number <= 0:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be positive",
        )
    return number


def _nonnegative(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be numeric",
        ) from exc
    if number < 0:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be non-negative",
        )
    return number


def _progress_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


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
        print(f"{_utc_now()} mother-admission-voter-cleanup.{phase} {message}{suffix}", file=sys.stderr)

    return emit


def _operation(network: str, suffix: str) -> OperationIdentity:
    stamp = _utc_now().replace(":", "").replace("-", "")
    return OperationIdentity(
        operation_id=f"mother-admission-voter-cleanup-{suffix}-{stamp}",
        request_id=f"mother-admission-voter-cleanup-{suffix}-{stamp}-request",
        network=network,
        operation_kind="MOTHER-OP-ADD-NODE",
    )


def _target_name(node: str) -> str:
    return f"{TARGET_PREFIX}{node}"


def _is_shim_record(item: Mapping[str, Any], *, target_name: str) -> bool:
    if item.get("name") != target_name:
        return False
    labels = _labels(item.get("labels"))
    status = _status(item.get("status"))
    if _truthy_label(labels, RETIRED_VOTER_LABEL) or _truthy_label(labels, SHIM_LABEL):
        return "unhealthy" not in status
    # Coolify service-detail payloads do not always echo Compose labels.  The
    # real admission voter uses python:3.12-alpine; the retired shim is the
    # deliberately tiny alpine:3.20 service installed by this script.
    return _safe_scalar(item.get("image")).strip() == SHIM_IMAGE and _healthy(status)


def _is_shim_definition(name: str, definition: object, *, target_name: str) -> bool:
    if name != target_name or not isinstance(definition, Mapping):
        return False
    labels = _labels(definition.get("labels"))
    image = _safe_scalar(definition.get("image")).strip()
    return (
        _truthy_label(labels, RETIRED_VOTER_LABEL)
        or _truthy_label(labels, SHIM_LABEL)
        or image == SHIM_IMAGE
    )


def _shim_service(service_uuid: str, *, target_name: str) -> dict[str, Any]:
    return {
        "image": SHIM_IMAGE,
        "container_name": f"{target_name}-{service_uuid}",
        "command": [
            "sh",
            "-lc",
            (
                "printf '%s\\n' "
                "'retired Mother admission voter shim; no vote contract is served here' "
                "> /tmp/mother-retired-helper-shim; "
                "while true; do sleep 3600; done"
            ),
        ],
        "environment": {
            "MOTHER_RETIRED_ADMISSION_VOTER_SHIM": "true",
            "CANDIDATE_VALIDATOR": ZERO_ADDRESS,
        },
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
            RETIRED_VOTER_LABEL: "true",
            "main_computer.mother.helper": target_name,
            "main_computer.mother.retired_helper_shim": "true",
            "main_computer.mother.not_a_validator_voter": "true",
            "main_computer.mother.retired_from_workflow": "add-node",
        },
    }


def _parse_compose(compose_text: str) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_COMPOSE_INVALID",
            "Coolify docker compose content is not valid YAML",
        ) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("services"), dict):
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_COMPOSE_INVALID",
            "Coolify docker compose content does not contain a services mapping",
        )
    return parsed


def _depends_on_names(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if isinstance(value, Mapping):
        return tuple(str(item) for item in value.keys())
    if type(value) is str and value.strip():
        return (value.strip(),)
    return ()


def _validate_compose_or_raise(compose: Mapping[str, Any], *, node: str, target_name: str) -> None:
    services = compose.get("services")
    if not isinstance(services, Mapping):
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_COMPOSE_INVALID",
            "rewritten compose content does not contain a services mapping",
        )
    if node not in services:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_MAIN_SERVICE_MISSING",
            f"rewritten compose does not contain the target node service {node!r}",
        )
    if target_name not in services:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_SHIM_MISSING",
            f"rewritten compose does not contain {target_name}",
        )
    if not _is_shim_definition(target_name, services[target_name], target_name=target_name):
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_SHIM_INVALID",
            f"{target_name} is not the retired shim definition",
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
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_COMPOSE_DANGLING_DEPENDS_ON",
            "rewritten compose has depends_on entries whose target services are missing: "
            + json.dumps(missing, sort_keys=True),
        )


def _install_shim_in_compose(
    compose_text: str,
    *,
    service_uuid: str,
    node: str,
    target_name: str,
) -> tuple[str, bool, tuple[str, ...]]:
    parsed = _parse_compose(compose_text)
    services = parsed["services"]
    before_other_services = {
        name: definition
        for name, definition in services.items()
        if name != target_name
    }

    replaced_existing = target_name in services
    services[target_name] = _shim_service(service_uuid, target_name=target_name)
    _validate_compose_or_raise(parsed, node=node, target_name=target_name)

    after_other_services = {
        name: definition
        for name, definition in services.items()
        if name != target_name
    }
    if before_other_services != after_other_services:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_SCOPE_VIOLATION",
            "admission voter cleanup changed non-target services",
        )

    return yaml.safe_dump(parsed, sort_keys=False), replaced_existing, tuple(str(name) for name in before_other_services.keys())


def _compose_summary(payload: Any, *, node: str, target_name: str) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for compose_text, source_field, source_encoding in _compose_text_candidates_from_service_payload(payload):
        source_sha = hashlib.sha256(compose_text.encode("utf-8")).hexdigest()
        try:
            parsed = _parse_compose(compose_text)
            services = parsed["services"]
            definition = services.get(target_name)
            target_declared = target_name in services
            target_is_shim = _is_shim_definition(target_name, definition, target_name=target_name)
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


def _target_records(payload: Any, *, target_name: str) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    applications = payload.get("applications")
    if not isinstance(applications, list):
        return []
    records: list[dict[str, Any]] = []
    for item in applications:
        if isinstance(item, Mapping) and item.get("name") == target_name:
            records.append(dict(item))
    return records


def _classify_payload(payload: Any, *, node: str, target_name: str) -> dict[str, Any]:
    records = _target_records(payload, target_name=target_name)
    shim_records = [item for item in records if _is_shim_record(item, target_name=target_name)]
    compose = _compose_summary(payload, node=node, target_name=target_name)
    parent_status = _status(payload.get("status")) if isinstance(payload, Mapping) else ""
    return {
        "parent_status": parent_status,
        "target_name": target_name,
        "target_record_count": len(records),
        "target_records": [
            {
                "uuid": _safe_scalar(item.get("uuid")),
                "name": _safe_scalar(item.get("name")),
                "status": _safe_scalar(item.get("status")),
                "image": _safe_scalar(item.get("image")),
                "exclude_from_status": item.get("exclude_from_status") is True,
                "is_retired_shim": _is_shim_record(item, target_name=target_name),
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


def _validate_admission_evidence(
    paths: MotherPaths,
    evidence_path: str | Path,
    *,
    acknowledged_sha256: str,
    network: str,
    node: str,
) -> dict[str, Any]:
    expected_sha = _sha256(acknowledged_sha256, "acknowledged admission evidence SHA-256")
    path = paths.validate_contained(evidence_path)
    expected_root = paths.evidence_root / "deployment-node-add-validator-admission"
    try:
        path.relative_to(expected_root)
    except ValueError as exc:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_EVIDENCE_PATH_INVALID",
            "validator-admission evidence must be under mother/evidence/deployment-node-add-validator-admission",
        ) from exc
    if not path.is_file():
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_EVIDENCE_MISSING",
            f"validator-admission evidence does not exist: {path}",
        )
    actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_sha != expected_sha:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_EVIDENCE_ACK_MISMATCH",
            "acknowledged admission evidence SHA-256 does not match the evidence file",
        )
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_EVIDENCE_INVALID",
            "validator-admission evidence is not valid JSON",
        ) from exc
    summary = evidence.get("summary") if isinstance(evidence, Mapping) else None
    voter_nodes = evidence.get("transient_voter_guardian_nodes") if isinstance(evidence, Mapping) else None
    if not isinstance(voter_nodes, list):
        voter_nodes = evidence.get("voter_nodes") if isinstance(evidence, Mapping) else None
    if not (
        isinstance(evidence, Mapping)
        and evidence.get("kind") == "main_computer.mother.deployment_node_add_validator_admission_evidence.v1"
        and evidence.get("network") == network
        and evidence.get("status") == "pass"
        and isinstance(summary, Mapping)
        and summary.get("complete") is True
        and summary.get("final_validator_set_verified") is True
        and node in [str(item) for item in (voter_nodes if isinstance(voter_nodes, list) else [])]
    ):
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_EVIDENCE_NOT_ACCEPTED",
            "execute requires passed validator-admission evidence where this node was a transient voter helper",
        )
    return {
        "path": str(path),
        "sha256": actual_sha,
        "network": evidence.get("network"),
        "status": evidence.get("status"),
        "candidate_node": evidence.get("summary", {}).get("target_node") if isinstance(summary, Mapping) else evidence.get("candidate_node"),
        "next_phase": evidence.get("next_phase"),
        "voter_nodes": [str(item) for item in voter_nodes],
    }


def inspect_admission_voter_cleanup(
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
    target_name = _target_name(node_name)
    request_timeout = _positive(timeout, "timeout")
    response_limit = int(max_response_bytes)
    if response_limit <= 0:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_INVALID_ARGUMENT",
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
    classification = _classify_payload(payload, node=node_name, target_name=target_name)

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
        "target_name": target_name,
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
            "only_target_supported": target_name,
        },
    }


def execute_admission_voter_cleanup(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    service_uuid: str,
    node: str,
    admission_evidence: str | Path,
    acknowledged_admission_evidence_sha256: str,
    acknowledged_service_uuid: str,
    allow_retired_admission_voter_shim: bool,
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
    target_name = _target_name(node_name)
    if _uuid(acknowledged_service_uuid, "acknowledged_service_uuid") != service:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_ACK_REQUIRED",
            "acknowledged service UUID must match the target service UUID",
        )
    if not allow_retired_admission_voter_shim:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_SHIM_FLAG_REQUIRED",
            "execute requires --allow-retired-admission-voter-shim",
        )

    paths = MotherPaths(runtime_state_root=private_state.paths.root.parent)
    accepted_evidence = _validate_admission_evidence(
        paths,
        admission_evidence,
        acknowledged_sha256=acknowledged_admission_evidence_sha256,
        network=network_id,
        node=node_name,
    )

    request_timeout = _positive(timeout, "timeout")
    response_limit = int(max_response_bytes)
    if response_limit <= 0:
        raise MotherAdmissionVoterCleanupError(
            "MOTHER_ADMISSION_VOTER_CLEANUP_INVALID_ARGUMENT",
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
    initial_payload = _service_payload(initial_detail)
    initial_classification = _classify_payload(initial_payload, node=node_name, target_name=target_name)
    observations.append({"phase": "initial", "classification": initial_classification})

    already_clean = (
        initial_classification["summary"]["compose_target_is_retired_shim"]
        and initial_classification["summary"]["target_record_is_retired_shim"]
    )

    patch_receipt: dict[str, Any] | None = None
    compose_attempts: list[dict[str, Any]] = []
    if not already_clean:
        last_error: Exception | None = None
        for compose_text, source_field, source_encoding in _compose_text_candidates_from_service_payload(initial_payload):
            source_sha = hashlib.sha256(compose_text.encode("utf-8")).hexdigest()
            try:
                rewritten, replaced_existing, preserved_names = _install_shim_in_compose(
                    compose_text,
                    service_uuid=service,
                    node=node_name,
                    target_name=target_name,
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
                    "installed_retired_admission_voter_shim": True,
                    "retired_admission_voter_shim_replaced_existing": replaced_existing,
                    "retired_admission_voter_shim_votes": False,
                    "retired_admission_voter_shim_private_key_required": False,
                    "preserved_service_names": list(preserved_names),
                    "target_name": target_name,
                }
            )
            patch_receipt = receipt
            if not receipt.get("ok"):
                raise MotherAdmissionVoterCleanupError(
                    "MOTHER_ADMISSION_VOTER_CLEANUP_PATCH_FAILED",
                    f"Coolify compose PATCH failed with HTTP {receipt.get('status')}",
                )
            break

        if patch_receipt is None:
            raise MotherAdmissionVoterCleanupError(
                "MOTHER_ADMISSION_VOTER_CLEANUP_COMPOSE_PATCH_UNAVAILABLE",
                "could not build a safe one-target admission voter shim compose rewrite: "
                + (str(last_error) if last_error else json.dumps(compose_attempts, sort_keys=True)),
            )

    deadline = time.monotonic() + wait_limit
    final_detail: Mapping[str, Any] | None = None
    final_classification: dict[str, Any] | None = None
    poll = 0
    while True:
        poll += 1
        detail = _service_detail(
            controller,
            service,
            timeout=request_timeout,
            max_response_bytes=response_limit,
            opener=opener,
        )
        observations.append(
            {
                "phase": "verification-poll",
                "poll": poll,
                "service_detail": {
                    "method": "GET",
                    "endpoint": f"/api/v1/services/{service}",
                    "status": detail.get("status"),
                    "ok": detail.get("ok"),
                    "response_sha256": detail.get("response_sha256"),
                    "byte_length": detail.get("byte_length"),
                    "elapsed_ms": detail.get("elapsed_ms"),
                },
            }
        )
        final_detail = detail
        final_classification = _classify_payload(_service_payload(detail), node=node_name, target_name=target_name)
        clean = (
            final_classification["summary"]["compose_target_is_retired_shim"]
            and final_classification["summary"]["target_record_is_retired_shim"]
        )
        _emit_progress(
            progress,
            "execute.poll",
            "verification poll classified admission voter",
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
        "target_name": target_name,
        "accepted_admission_evidence": accepted_evidence,
        "mutation_performed": patch_receipt is not None,
        "coolify_touched": patch_receipt is not None,
        "compose_touched": patch_receipt is not None,
        "docker_touched": False,
        "chain_touched": False,
        "initial_classification": initial_classification,
        "final_classification": final_classification,
        "patch_receipt": patch_receipt,
        "compose_attempts": compose_attempts,
        "observations": observations,
        "summary": {
            **final_classification["summary"],
            "clean": clean,
            "already_clean": already_clean,
            "mutation_performed": patch_receipt is not None,
            "only_target_supported": target_name,
            "installed_retired_admission_voter_shim": bool(patch_receipt),
            "admission_evidence_verified": True,
        },
    }
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
        description="Install or verify one retired mother-add-node-validator-admission-voter-<node> health shim."
    )
    parser.add_argument("mode", choices=("inspect", "execute"))
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--controller-id", required=True)
    parser.add_argument("--service-uuid", required=True)
    parser.add_argument("--node-name", required=True)
    parser.add_argument("--admission-evidence")
    parser.add_argument("--acknowledge-admission-evidence-sha256")
    parser.add_argument("--acknowledge-service-uuid")
    parser.add_argument("--allow-retired-admission-voter-shim", action="store_true")
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
            result = inspect_admission_voter_cleanup(
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
            if not args.admission_evidence or not args.acknowledge_admission_evidence_sha256:
                raise MotherAdmissionVoterCleanupError(
                    "MOTHER_ADMISSION_VOTER_CLEANUP_EVIDENCE_REQUIRED",
                    "execute requires --admission-evidence and --acknowledge-admission-evidence-sha256",
                )
            result = execute_admission_voter_cleanup(
                private_state,
                network=args.network,
                controller_id=args.controller_id,
                service_uuid=args.service_uuid,
                node=args.node_name,
                admission_evidence=args.admission_evidence,
                acknowledged_admission_evidence_sha256=args.acknowledge_admission_evidence_sha256,
                acknowledged_service_uuid=args.acknowledge_service_uuid or "",
                allow_retired_admission_voter_shim=args.allow_retired_admission_voter_shim,
                instant_deploy=args.instant_deploy,
                max_wait_seconds=args.max_wait_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
                progress=progress,
            )
        if args.write_evidence:
            result = dict(result)
            result["evidence_path"] = str(_write_evidence(args.runtime_state_root, result))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") in {"pass", "clean"} else 2
    except MotherAdmissionVoterCleanupError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except MotherDeploymentCompletedHelperCleanupError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
