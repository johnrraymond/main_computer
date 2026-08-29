#!/usr/bin/env python3
"""YAGNI helper cleanup2 for Mother Coolify service-stack helpers.

This script does only the agreed cleanup2 flow:

1. Import the local finalized topology.
2. PATCH helper Compose blocks to inert alpine mimics for each super node.
3. Install and run one temporary cleanup2 service per controller.  cleanup2
   uses the local Docker socket to recreate only the named helper Compose
   services that Coolify's public API cannot restart as child resources.
4. For each touched parent service, queue Coolify Restart, wait 90 seconds,
   then wait for the top-level service status to become running:healthy before
   restarting the next touched parent service.
5. Leave block-advance proof to tools/mother_wait_for_block_advance.py.  This
   cleanup script does not do its own chain-height or Besu RPC probing.

It does not call public child application restart/deploy APIs, parent start,
parent deploy, Besu/FDB/Hub services, Besu RPC, or child-resource health polling.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Mapping
import urllib.parse
import urllib.request

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import CoolifyController, resolve_coolify_controller
from tools.mother.common.deployment_completed_helper_cleanup import (
    MotherDeploymentCompletedHelperCleanupError,
    _application_uuid,
    _controller_config,
    _http,
    _resolve_environment_uuid,
    _temporary_service_body,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state


KIND = "main_computer.mother.helper_cleanup2_yagni.v1"
EVIDENCE_SUBDIR = "mother-helper-cleanup2-yagni"
CLEANUP2_PREFIX = "mother-helper-cleanup2"
RUNTIME_DIAGNOSTIC_PREFIX = "MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC"
RUNTIME_DIAGNOSTIC_PHASES = (
    "script_start",
    "target_payload",
    "compose_decoded",
    "find_project",
    "find_project_candidate",
    "find_project_selected",
    "find_project_failed",
    "target_begin",
    "before",
    "remove_expected_container_before_recreate",
    "docker_compose_start",
    "docker_compose_exit",
    "docker_compose_stdout",
    "docker_compose_stderr",
    "after",
    "script_complete",
)

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")

HELPER_EXACT_NAMES = frozenset(
    {
        "mother-add-node-validator-activation-guardian",
        "mother-genesis-proof-guardian",
    }
)
HELPER_PREFIXES = (
    "mother-add-node-validator-admission-voter-",
    "mother-node-remove-voter-",
)
FORBIDDEN_SERVICE_NAMES = frozenset(
    {
        "mother-super-node-fdb",
        "mother-super-node-hub",
        "mother-replica-sync-guardian",
        "mother-replica-init",
        "mother-validator-activation-init",
        "mother-genesis-init",
    }
)

SHIM_IMAGE = "alpine:3.20"
SHIM_LABEL = "main_computer.mother.post_work_shim"
MIMIC_LABEL = "main_computer.mother.retired_helper_mimic"
PARENT_RESTART_SETTLE_SECONDS = 90.0
BLOCK_ADVANCE_WAITER_SCRIPT = "tools/mother_wait_for_block_advance.py"
BLOCK_ADVANCE_WAITER_SUBPROCESS_TIMEOUT_SECONDS = 720.0

class MotherHelperCleanup2YagniError(RuntimeError):
    """Cleanup2 could not produce a trustworthy result."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


ProgressCallback = Callable[[str, str, Mapping[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _identifier(value: object, name: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or text in {".", ".."}
        or "/" in text
        or "\\" in text
        or "\x00" in text
        or not IDENTIFIER_RE.fullmatch(text)
    ):
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_INVALID_ARGUMENT",
            f"{name} must be a simple identifier",
        )
    return text


def _uuid(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text or not UUID_RE.fullmatch(text):
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_INVALID_ARGUMENT",
            f"{name} must be a valid UUID-like identifier",
        )
    return text


def _sha256(value: object, name: str) -> str:
    text = str(value or "").strip().lower()
    if not SHA256_RE.fullmatch(text):
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_INVALID_ARGUMENT",
            f"{name} must be a SHA-256 hex digest",
        )
    return text


def _positive(value: float | int, name: str) -> float:
    number = float(value)
    if number <= 0:
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_INVALID_ARGUMENT",
            f"{name} must be positive",
        )
    return number


def _nonnegative(value: float | int, name: str) -> float:
    number = float(value)
    if number < 0:
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_INVALID_ARGUMENT",
            f"{name} must be non-negative",
        )
    return number


def _helper_allowed(name: str) -> bool:
    return name in HELPER_EXACT_NAMES or any(name.startswith(prefix) for prefix in HELPER_PREFIXES)


def _helper_name(value: object) -> str:
    name = _identifier(value, "helper_name")
    if name in FORBIDDEN_SERVICE_NAMES or not _helper_allowed(name):
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_UNSUPPORTED_HELPER",
            f"refusing to target unsupported helper service: {name}",
        )
    return name


def _service_summary(
    node: str,
    controller_id: str,
    service_uuid: str,
    record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "node": _identifier(node, "topology node"),
        "controller_id": _identifier(controller_id, f"{node} controller_id"),
        "service_uuid": _uuid(service_uuid, f"{node} service_uuid"),
    }
    if isinstance(record, Mapping):
        for key in ("validator_route", "p2p_route"):
            value = record.get(key)
            if isinstance(value, Mapping):
                result[key] = dict(value)
        for key in ("vpn_ip", "p2p_port", "p2p_endpoint", "enode"):
            value = record.get(key)
            if value is not None:
                result[key] = value
    return result


def _latest_sort_key(path: Path, document: Mapping[str, Any]) -> tuple[str, float, str]:
    stamp = document.get("completed_at") or document.get("observed_at") or ""
    return (str(stamp), path.stat().st_mtime if path.exists() else 0.0, path.name)


def _marks_current_topology(summary: Mapping[str, Any]) -> bool:
    return (
        summary.get("topology_current") is True
        or summary.get("current_topology_marked_by_evidence") is True
    )


def _emit_progress(progress: ProgressCallback | None, phase: str, message: str, **fields: Any) -> None:
    if progress is not None:
        progress(phase, message, fields)


def _stderr_progress(quiet: bool) -> ProgressCallback | None:
    if quiet:
        return None

    def emit(phase: str, message: str, fields: Mapping[str, Any]) -> None:
        suffix = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
        print(
            f"{_utc_now()} mother-helper-cleanup2-yagni.{phase} {message}{(' ' + suffix) if suffix else ''}",
            file=sys.stderr,
            flush=True,
        )

    return emit


def _operation(network: str, mode: str) -> OperationIdentity:
    network_id = _identifier(network, "network")
    mode_id = _identifier(mode, "mode")
    operation_id = f"mother-helper-cleanup2-yagni-{mode_id}-{network_id}-{_stamp()}"
    return OperationIdentity(
        operation_id=operation_id,
        request_id=f"{operation_id}-request",
        network=network_id,
        operation_kind="MOTHER-OP-RESTORE-SERVICE",
    )


def _load_private_state(runtime_state_root: str | Path, *, network: str, mode: str) -> PrivateStateReadResult:
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root)).resolve_private_state_paths()
    return read_private_state(paths, operation=_operation(network, mode))


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_EVIDENCE_MISSING",
            f"{label} does not exist: {path}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_EVIDENCE_INVALID",
            f"{label} is not valid JSON: {path}",
        ) from exc
    if not isinstance(value, Mapping):
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_EVIDENCE_INVALID",
            f"{label} must be a JSON object: {path}",
        )
    return value


def _resolve_mother_path(paths: MotherPaths, value: str | Path) -> Path:
    try:
        return paths.validate_contained(value)
    except ValueError as exc:
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_PATH_INVALID",
            str(exc),
        ) from exc


def _topology(document: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("final_topology", "current_topology", "post_add_topology", "post_removal_topology"):
        value = document.get(key)
        if isinstance(value, Mapping):
            return value
    return document


def _topology_nodes(document: Mapping[str, Any], topology: Mapping[str, Any]) -> list[str]:
    summary = document.get("summary")
    candidates: list[Any] = []
    if isinstance(summary, Mapping):
        candidates.append(summary.get("final_nodes"))
    candidates.extend([topology.get("nodes"), document.get("nodes")])

    for raw in candidates:
        if isinstance(raw, list):
            nodes = [_identifier(item, "topology node") for item in raw]
            if nodes:
                return list(dict.fromkeys(nodes))

    observations = document.get("service_observations")
    if isinstance(observations, list):
        nodes = [
            _identifier(item["node"], "service observation node")
            for item in observations
            if isinstance(item, Mapping) and isinstance(item.get("node"), str)
        ]
        if nodes:
            return list(dict.fromkeys(nodes))

    raise MotherHelperCleanup2YagniError(
        "MOTHER_HELPER_CLEANUP2_YAGNI_TOPOLOGY_INVALID",
        "topology evidence does not list current nodes",
    )


def _service_records(document: Mapping[str, Any], topology: Mapping[str, Any], nodes: list[str]) -> list[dict[str, Any]]:
    by_node: dict[str, dict[str, Any]] = {}

    raw_services = topology.get("services")
    if isinstance(raw_services, Mapping):
        for node in nodes:
            record = raw_services.get(node)
            if isinstance(record, Mapping):
                controller_id = record.get("controller_id")
                service_uuid = record.get("service_uuid") or record.get("created_service_uuid")
                if isinstance(controller_id, str) and isinstance(service_uuid, str) and service_uuid:
                    by_node[node] = _service_summary(node, controller_id, service_uuid, record)

    observations = document.get("service_observations")
    if isinstance(observations, list):
        for item in observations:
            if not isinstance(item, Mapping):
                continue
            node = item.get("node")
            controller_id = item.get("controller_id")
            service_uuid = item.get("service_uuid")
            if (
                isinstance(node, str)
                and node in nodes
                and isinstance(controller_id, str)
                and isinstance(service_uuid, str)
                and service_uuid
            ):
                merged = dict(by_node.get(node) or {})
                merged.update(_service_summary(node, controller_id, service_uuid, item))
                by_node[node] = merged

    missing = [node for node in nodes if node not in by_node]
    if missing:
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_TOPOLOGY_INVALID",
            "topology evidence lacks service records for: " + ", ".join(missing),
        )
    return [by_node[node] for node in nodes]


def _topology_evidence_dir(paths: MotherPaths) -> Path:
    return paths.evidence_root / "deployment-node-add-post-admission-observe"


def _candidate_topology_evidence(paths: MotherPaths, *, network: str) -> list[dict[str, Any]]:
    root = _topology_evidence_dir(paths)
    if not root.is_dir():
        return []
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            document = _load_json(path, label="topology evidence candidate")
            document_network = document.get("network")
            summary = document.get("summary")
            if isinstance(document_network, str) and document_network != network:
                continue
            if not (
                document.get("status") == "pass"
                and isinstance(summary, Mapping)
                and summary.get("complete") is True
                and summary.get("clean") is True
                and _marks_current_topology(summary)
            ):
                continue
            candidates.append({"path": path, "document": document, "sort_key": _latest_sort_key(path, document)})
        except MotherHelperCleanup2YagniError:
            continue
    return sorted(candidates, key=lambda item: item["sort_key"])


def _discover_latest_topology_evidence(paths: MotherPaths, *, network: str) -> Path:
    candidates = _candidate_topology_evidence(paths, network=network)
    if not candidates:
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_TOPOLOGY_NOT_FOUND",
            "no passed, clean, complete, current add-node post-admission topology evidence was found on disk",
        )
    return Path(candidates[-1]["path"])


def _load_topology(
    runtime_state_root: str | Path,
    *,
    network: str,
    topology_evidence: str | Path | None,
    acknowledged_sha256: str | None,
) -> dict[str, Any]:
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root))
    discovered = topology_evidence is None
    path = _discover_latest_topology_evidence(paths, network=network) if discovered else _resolve_mother_path(paths, topology_evidence)
    document = _load_json(path, label="topology evidence")
    actual_sha = _file_sha256(path)
    if acknowledged_sha256 is not None and actual_sha != _sha256(acknowledged_sha256, "topology evidence SHA-256"):
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_TOPOLOGY_ACK_MISMATCH",
            "acknowledged topology evidence SHA-256 does not match the evidence file",
        )

    summary = document.get("summary")
    if not (
        document.get("status") == "pass"
        and isinstance(summary, Mapping)
        and summary.get("complete") is True
        and summary.get("clean") is True
        and _marks_current_topology(summary)
    ):
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_TOPOLOGY_NOT_ACCEPTED",
            "cleanup2 requires passed, clean, complete, current topology evidence",
        )

    document_network = document.get("network")
    if isinstance(document_network, str) and document_network != network:
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_TOPOLOGY_NETWORK_MISMATCH",
            "topology evidence network does not match --network",
        )

    topology = _topology(document)
    nodes = _topology_nodes(document, topology)
    services = _service_records(document, topology, nodes)
    return {
        "path": path,
        "sha256": actual_sha,
        "discovered": discovered,
        "document": document,
        "topology": topology,
        "nodes": nodes,
        "services": services,
    }


def _decode_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return payload
    if isinstance(payload, Mapping) and isinstance(payload.get("raw"), str):
        raw = str(payload["raw"]).strip()
        if raw.startswith("{") or raw.startswith("["):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return payload
    return payload


def _safe_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)[:500]
    return ""


def _application_records(payload: Any) -> list[dict[str, str]]:
    payload = _decode_payload(payload)
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, Mapping):
        raw_items = []
        for key in ("applications", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                raw_items = value
                break
    else:
        raw_items = []

    records: list[dict[str, str]] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        name = _safe_scalar(item.get("name")).strip()
        uuid = _safe_scalar(item.get("uuid")).strip()
        status = _safe_scalar(item.get("status")).strip()
        image = _safe_scalar(item.get("image")).strip()
        if name and uuid:
            records.append({"name": name, "uuid": uuid, "status": status, "image": image})
    return records


def _decode_compose_value(value: object) -> tuple[str, str]:
    if type(value) is not str or not value.strip():
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_COMPOSE_MISSING",
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


def _compose_text_from_service_payload(payload: Any) -> tuple[str, str, str]:
    payload = _decode_payload(payload)
    if not isinstance(payload, Mapping):
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_BAD_RESPONSE",
            "Coolify service detail payload is not an object",
        )

    raw = payload.get("docker_compose_raw")
    if type(raw) is str and raw.strip():
        text, encoding = _decode_compose_value(raw)
        return text, "docker_compose_raw", encoding

    rendered = payload.get("docker_compose")
    if type(rendered) is str and rendered.strip():
        text, encoding = _decode_compose_value(rendered)
        return text, "docker_compose", encoding

    raise MotherHelperCleanup2YagniError(
        "MOTHER_HELPER_CLEANUP2_YAGNI_COMPOSE_MISSING",
        "Coolify service detail does not include docker compose content",
    )


def _parse_compose(compose_text: str) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_COMPOSE_INVALID",
            "Coolify docker compose content is not valid YAML",
        ) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("services"), dict):
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_COMPOSE_INVALID",
            "Coolify docker compose content does not contain a services mapping",
        )
    return parsed


DOCKER_COMPOSE_CLI_UNSUPPORTED_SERVICE_KEYS = frozenset(
    {
        # Coolify accepts/injects this metadata, but the docker compose CLI rejects it
        # with: Additional property exclude_from_hc is not allowed.
        "exclude_from_hc",
    }
)


def _strip_docker_compose_cli_unsupported_service_keys(compose: dict[str, Any]) -> dict[str, list[str]]:
    """Remove Coolify-only service keys before using docker compose CLI."""
    services = compose.get("services")
    removed: dict[str, list[str]] = {}
    if not isinstance(services, dict):
        return removed

    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        removed_keys: list[str] = []
        for key in DOCKER_COMPOSE_CLI_UNSUPPORTED_SERVICE_KEYS:
            if key in service:
                service.pop(key, None)
                removed_keys.append(key)
        if removed_keys:
            removed[str(service_name)] = sorted(removed_keys)
    return removed


def _docker_compose_cli_apply_compose(compose_text: str) -> tuple[str, dict[str, list[str]]]:
    """Return the helper apply Compose as accepted by docker compose CLI."""
    compose = _parse_compose(compose_text)
    removed = _strip_docker_compose_cli_unsupported_service_keys(compose)
    return yaml.safe_dump(compose, sort_keys=False), removed


def _command_text(value: object) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    if isinstance(value, tuple):
        return " ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _short_text(value: object, *, limit: int = 300) -> str:
    text = _command_text(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _helper_definition_diagnostic(
    compose: Mapping[str, Any],
    *,
    helper_name: str,
    service_uuid: str,
) -> dict[str, Any]:
    helper = _helper_name(helper_name)
    service = _uuid(service_uuid, "service_uuid")
    services = compose.get("services")
    definition: Any = services.get(helper) if isinstance(services, Mapping) else None
    if not isinstance(definition, Mapping):
        return {
            "helper_name": helper,
            "present": False,
            "expected_cleanup2_container_name": f"{helper}-{service}",
            "is_cleanup2_mimic_definition": False,
        }

    labels = definition.get("labels")
    labels_map = labels if isinstance(labels, Mapping) else {}
    command = definition.get("command")
    command_text = _command_text(command)
    healthcheck = definition.get("healthcheck")
    healthcheck_map = healthcheck if isinstance(healthcheck, Mapping) else {}
    selected_labels = {
        key: labels_map.get(key)
        for key in (
            SHIM_LABEL,
            MIMIC_LABEL,
            "main_computer.mother.helper",
            "main_computer.mother.cleanup_scope",
            "main_computer.mother.not_a_proof_guardian",
            "main_computer.mother.not_a_validator_voter",
            "main_computer.mother.not_an_activation_guardian",
        )
    }
    return {
        "helper_name": helper,
        "present": True,
        "image": str(definition.get("image") or ""),
        "container_name": str(definition.get("container_name") or ""),
        "expected_cleanup2_container_name": f"{helper}-{service}",
        "restart": str(definition.get("restart") or ""),
        "healthcheck_test": healthcheck_map.get("test"),
        "command_sha256": hashlib.sha256(command_text.encode("utf-8")).hexdigest() if command_text else None,
        "command_preview": _short_text(command),
        "selected_labels": selected_labels,
        "is_cleanup2_mimic_definition": (
            str(definition.get("image") or "") == SHIM_IMAGE
            and str(definition.get("container_name") or "") == f"{helper}-{service}"
            and labels_map.get(MIMIC_LABEL) == "true"
            and labels_map.get("main_computer.mother.cleanup_scope") == "helper-cleanup2-yagni"
            and labels_map.get("main_computer.mother.helper") == helper
            and labels_map.get("main_computer.mother.not_a_proof_guardian") == "true"
            and labels_map.get("main_computer.mother.not_a_validator_voter") == "true"
            and labels_map.get("main_computer.mother.not_an_activation_guardian") == "true"
            and "mother-retired-helper-shim" in command_text
        ),
    }


def _helper_definitions_diagnostic(
    compose: Mapping[str, Any],
    *,
    helper_names: tuple[str, ...],
    service_uuid: str,
) -> list[dict[str, Any]]:
    return [
        _helper_definition_diagnostic(compose, helper_name=helper, service_uuid=service_uuid)
        for helper in helper_names
    ]


def _parent_payload_diagnostic(
    payload: Any,
    *,
    helper_names: tuple[str, ...],
    service_uuid: str,
    compose: Mapping[str, Any] | None = None,
    compose_sha256: str | None = None,
    source_field: str | None = None,
    source_encoding: str | None = None,
) -> dict[str, Any]:
    decoded = _decode_payload(payload)
    payload_map = decoded if isinstance(decoded, Mapping) else {}
    helpers = tuple(_helper_name(item) for item in helper_names)
    helper_set = set(helpers)
    applications: list[dict[str, Any]] = []
    for record in _application_records(payload_map):
        name = str(record.get("name") or "")
        if name in helper_set:
            applications.append(
                {
                    "name": name,
                    "uuid": str(record.get("uuid") or ""),
                    "status": str(record.get("status") or record.get("service_status") or ""),
                    "image": str(record.get("image") or ""),
                }
            )
    result: dict[str, Any] = {
        "service_uuid": service_uuid,
        "parent_name": str(payload_map.get("name") or ""),
        "parent_status": str(payload_map.get("status") or payload_map.get("service_status") or ""),
        "source_field": source_field,
        "source_encoding": source_encoding,
        "compose_sha256": compose_sha256,
        "helper_application_records": applications,
        "helper_application_count": len(applications),
    }
    if compose is not None:
        result["helper_definitions"] = _helper_definitions_diagnostic(
            compose,
            helper_names=helpers,
            service_uuid=service_uuid,
        )
    return result


def _cleanup2_target_diagnostic(target: Mapping[str, Any]) -> dict[str, Any]:
    service_uuid = _uuid(target.get("service_uuid"), "cleanup2 target service_uuid")
    helpers = tuple(_helper_name(item) for item in target.get("helper_names") or ())
    return {
        "node": str(target.get("node") or ""),
        "service_uuid": service_uuid,
        "helper_names": list(helpers),
        "expected_container_names": [f"{helper}-{service_uuid}" for helper in helpers],
        "project_discovery": {
            "method": "docker ps -aq --filter name=<service_uuid>; inspect com.docker.compose.project",
            "service_uuid": service_uuid,
        },
        "docker_compose_up_template": (
            "docker compose -p <discovered_project> -f <decoded_patched_compose> "
            "--project-directory <discovered_workdir_if_available> up -d --no-deps --force-recreate "
            + " ".join(helpers)
        ),
        "runtime_diagnostics_log_prefix": RUNTIME_DIAGNOSTIC_PREFIX,
        "runtime_diagnostics_expected_phases": [
            "target_payload",
            "compose_decoded",
            "find_project",
            "find_project_candidate",
            "find_project_selected",
            "target_begin",
            "before",
            "remove_expected_container_before_recreate",
            "docker_compose_start",
            "docker_compose_exit",
            "docker_compose_stdout",
            "docker_compose_stderr",
            "after",
        ],
        "patched_compose_sha256": str(target.get("patched_compose_sha256") or ""),
        "docker_compose_cli_compose_sha256": str(target.get("docker_compose_cli_compose_sha256") or ""),
        "docker_compose_cli_removed_service_keys": dict(target.get("docker_compose_cli_removed_service_keys") or {}),
    }


def _patch_readback_diagnostic(
    controller: CoolifyController,
    *,
    controller_id: str,
    node: str,
    service_uuid: str,
    helper_names: tuple[str, ...],
    expected_compose_sha256: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    http_observations: list[dict[str, Any]],
    phase: str,
) -> dict[str, Any]:
    try:
        detail = _service_detail(
            controller,
            service_uuid,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        receipt = _receipt_from_detail(detail["receipt"], controller_id=controller_id, node=node, service_uuid=service_uuid)
        receipt["phase"] = phase
        http_observations.append(receipt)
        if detail.get("missing") is True:
            return {
                "phase": phase,
                "ok": False,
                "reason": "service-detail-404",
                "receipt": receipt,
            }
        compose_text, source_field, source_encoding = _compose_text_from_service_payload(detail["payload"])
        compose_sha256 = hashlib.sha256(compose_text.encode("utf-8")).hexdigest()
        parsed = _parse_compose(compose_text)
        payload_diag = _parent_payload_diagnostic(
            detail["payload"],
            helper_names=helper_names,
            service_uuid=service_uuid,
            compose=parsed,
            compose_sha256=compose_sha256,
            source_field=source_field,
            source_encoding=source_encoding,
        )
        mimic_ok = all(item.get("is_cleanup2_mimic_definition") is True for item in payload_diag.get("helper_definitions", []))
        return {
            "phase": phase,
            "ok": True,
            "receipt": receipt,
            "compose_sha256": compose_sha256,
            "expected_compose_sha256": expected_compose_sha256,
            "compose_matches_expected_patch": compose_sha256 == expected_compose_sha256,
            "all_target_helpers_are_cleanup2_mimics_in_saved_compose": mimic_ok,
            "parent_payload": payload_diag,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "phase": phase,
            "ok": False,
            "error_code": getattr(exc, "code", type(exc).__name__),
            "error": str(exc),
        }


def _mimic_service(service_uuid: str, helper_name: str) -> dict[str, Any]:
    service = _uuid(service_uuid, "service_uuid")
    helper = _helper_name(helper_name)
    return {
        "image": SHIM_IMAGE,
        "container_name": f"{helper}-{service}",
        "command": [
            "sh",
            "-lc",
            "while true; do echo mother-retired-helper-shim >/tmp/mother-retired-helper-shim; sleep 30; done",
        ],
        "healthcheck": {
            "test": ["CMD-SHELL", "echo ok"],
            "interval": "5s",
            "timeout": "2s",
            "retries": 3,
            "start_period": "1s",
        },
        "restart": "unless-stopped",
        "labels": {
            SHIM_LABEL: "true",
            MIMIC_LABEL: "true",
            "main_computer.mother.helper": helper,
            "main_computer.mother.cleanup_scope": "helper-cleanup2-yagni",
            "main_computer.mother.not_a_proof_guardian": "true",
            "main_computer.mother.not_a_validator_voter": "true",
            "main_computer.mother.not_an_activation_guardian": "true",
        },
    }


def _target_helper_names(payload: Any, compose: Mapping[str, Any]) -> tuple[str, ...]:
    names: set[str] = set()
    for record in _application_records(payload):
        name = record.get("name", "")
        if _helper_allowed(name):
            names.add(_helper_name(name))

    services = compose.get("services")
    if isinstance(services, Mapping):
        for name in services.keys():
            text = str(name)
            if _helper_allowed(text):
                names.add(_helper_name(text))

    return tuple(sorted(names))


def _rewrite_helper_mimics(
    compose_text: str,
    *,
    service_uuid: str,
    helper_names: tuple[str, ...],
) -> tuple[str, dict[str, Any]]:
    compose = _parse_compose(compose_text)
    services = compose["services"]

    non_targets_before = {name: definition for name, definition in services.items() if name not in set(helper_names)}
    installed: list[str] = []

    for helper in helper_names:
        name = _helper_name(helper)
        services[name] = _mimic_service(service_uuid, name)
        installed.append(name)

    non_targets_after = {name: definition for name, definition in services.items() if name not in set(helper_names)}
    if non_targets_before != non_targets_after:
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_NON_HELPER_CHANGED",
            "non-helper Compose services changed during helper mimic rewrite",
        )

    rewritten = yaml.safe_dump(compose, sort_keys=False)
    return rewritten, {
        "installed_helper_names": installed,
        "installed_helper_count": len(installed),
    }


def _patch_parent_compose(
    controller: CoolifyController,
    service_uuid: str,
    compose_text: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "service_uuid")
    encoded = base64.b64encode(compose_text.encode("utf-8")).decode("ascii")
    endpoint = f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
    body = {
        "docker_compose_raw": encoded,
        "instant_deploy": False,
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
        "instant_deploy": False,
        "patch_scope": "helper-mimic-compose-only",
    }



def _restart_parent_service(
    controller: CoolifyController,
    service_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "service_uuid")
    endpoint = f"/api/v1/services/{urllib.parse.quote(service, safe='')}/restart"
    response = _http(
        controller,
        "POST",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    return {
        "method": "POST",
        "endpoint": endpoint,
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "service_uuid": service,
        "restart_scope": "post-cleanup2-parent-status-reconcile",
    }


def _wait_for_parent_service_running_healthy(
    controller: CoolifyController,
    *,
    controller_id: str,
    node: str,
    service_uuid: str,
    initial_settle_seconds: float,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    sleeper: Callable[[float], None],
    http_observations: list[dict[str, Any]],
) -> dict[str, Any]:
    service = _uuid(service_uuid, "service_uuid")
    settle_seconds = _nonnegative(initial_settle_seconds, "initial_settle_seconds")
    if settle_seconds:
        sleeper(settle_seconds)

    observed_statuses: list[str] = []
    observed_receipts: list[dict[str, Any]] = []
    started = time.monotonic()
    while True:
        detail = _service_detail(
            controller,
            service,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        receipt = _receipt_from_detail(detail["receipt"], controller_id=controller_id, node=node, service_uuid=service)
        receipt["phase"] = "post-parent-restart-status-poll"
        observed_receipts.append(receipt)
        http_observations.append(receipt)

        status = ""
        if detail.get("missing") is not True and isinstance(detail.get("payload"), Mapping):
            status = _safe_scalar(detail["payload"].get("status")).strip()
        observed_statuses.append(status)

        if status == "running:healthy":
            return {
                "completed": True,
                "reason": "parent-service-running-healthy",
                "service_uuid": service,
                "node": node,
                "controller_id": controller_id,
                "initial_settle_seconds": settle_seconds,
                "final_status": status,
                "observed_statuses": observed_statuses,
                "observation_count": len(observed_statuses),
                "wait_milliseconds_after_settle": int((time.monotonic() - started) * 1000),
                "receipts": observed_receipts,
            }

        elapsed = time.monotonic() - started
        if elapsed >= max_wait_seconds:
            return {
                "completed": False,
                "reason": "parent-service-running-healthy-timeout",
                "service_uuid": service,
                "node": node,
                "controller_id": controller_id,
                "initial_settle_seconds": settle_seconds,
                "final_status": status,
                "observed_statuses": observed_statuses,
                "observation_count": len(observed_statuses),
                "wait_milliseconds_after_settle": int(elapsed * 1000),
                "receipts": observed_receipts,
            }

        sleeper(min(poll_interval_seconds, max(0.0, max_wait_seconds - elapsed)))





def _controller(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
) -> CoolifyController:
    return resolve_coolify_controller(
        private_state,
        _identifier(network, "network"),
        _identifier(controller_id, "controller_id"),
        require_enabled=True,
        require_token=True,
    )


def _service_detail(
    controller: CoolifyController,
    service_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "service_uuid")
    endpoint = f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
    response = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    receipt = {
        "method": "GET",
        "endpoint": endpoint,
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
    }
    if not response["ok"]:
        if response["status"] == 404:
            return {"receipt": receipt, "payload": {}, "missing": True}
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_SERVICE_DETAIL_FAILED",
            f"Coolify service detail failed with HTTP {response['status']}",
        )
    return {"receipt": receipt, "payload": _decode_payload(response.get("payload")), "missing": False}


def _single_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _cleanup2_script(targets: list[Mapping[str, Any]]) -> str:
    lines = [
        "set -eu",
        "DIAG_PREFIX=MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC",
        "diag() { printf '%s %s\n' \"$DIAG_PREFIX\" \"$*\" >&2; }",
        "branch() { printf '%s %s\n' MOTHER_HELPER_CLEANUP2_BRANCH \"$*\" >&2; }",
        "diag_file() {",
        "  phase=\"$1\"",
        "  name=\"$2\"",
        "  file=\"$3\"",
        "  if [ -f \"$file\" ]; then",
        "    bytes=\"$(wc -c < \"$file\" | tr -d ' ')\"",
        "    if command -v sha256sum >/dev/null 2>&1; then",
        "      sha=\"$(sha256sum \"$file\" | awk '{print $1}')\"",
        "    else",
        "      sha=\"\"",
        "    fi",
        "    b64=\"$(base64 \"$file\" 2>/dev/null | tr -d '\n' || true)\"",
        "    diag \"phase=$phase artifact=$name bytes=$bytes sha256=$sha base64=$b64\"",
        "  else",
        "    diag \"phase=$phase artifact=$name missing=true\"",
        "  fi",
        "}",
        "docker_ps_snapshot() {",
        "  phase=\"$1\"",
        "  uuid=\"$2\"",
        "  diag \"phase=$phase service_uuid=$uuid docker_ps_filter=name=$uuid\"",
        "  docker ps -a --filter \"name=$uuid\" --format \"$DIAG_PREFIX phase=$phase docker_ps id={{.ID}} name={{.Names}} image={{.Image}} status={{.Status}}\" >&2 || true",
        "}",
        "inspect_expected() {",
        "  phase=\"$1\"",
        "  uuid=\"$2\"",
        "  helper=\"$3\"",
        "  project=\"$4\"",
        "  cname=\"${helper}-${uuid}\"",
        "  ids=\"$(docker ps -aq --filter \"name=^${cname}$\" || true)\"",
        "  cid=\"$(printf '%s\n' \"$ids\" | sed -n '1p')\"",
        "  count=\"$(printf '%s\n' \"$ids\" | sed '/^$/d' | wc -l | tr -d ' ')\"",
        "  if [ -z \"$cid\" ]; then",
        "    diag \"phase=$phase expected_container=$cname helper=$helper project=$project found=false match_count=$count\"",
        "    return 0",
        "  fi",
        "  image=\"$(docker inspect -f '{{ .Config.Image }}' \"$cid\" 2>/dev/null || true)\"",
        "  status=\"$(docker inspect -f '{{ .State.Status }}' \"$cid\" 2>/dev/null || true)\"",
        "  running=\"$(docker inspect -f '{{ .State.Running }}' \"$cid\" 2>/dev/null || true)\"",
        "  health=\"$(docker inspect -f '{{ if .State.Health }}{{ .State.Health.Status }}{{ end }}' \"$cid\" 2>/dev/null || true)\"",
        "  compose_project=\"$(docker inspect -f '{{ index .Config.Labels \"com.docker.compose.project\" }}' \"$cid\" 2>/dev/null || true)\"",
        "  compose_service=\"$(docker inspect -f '{{ index .Config.Labels \"com.docker.compose.service\" }}' \"$cid\" 2>/dev/null || true)\"",
        "  retired=\"$(docker inspect -f '{{ index .Config.Labels \"main_computer.mother.retired_helper_mimic\" }}' \"$cid\" 2>/dev/null || true)\"",
        "  cleanup_scope=\"$(docker inspect -f '{{ index .Config.Labels \"main_computer.mother.cleanup_scope\" }}' \"$cid\" 2>/dev/null || true)\"",
        "  helper_label=\"$(docker inspect -f '{{ index .Config.Labels \"main_computer.mother.helper\" }}' \"$cid\" 2>/dev/null || true)\"",
        "  not_proof=\"$(docker inspect -f '{{ index .Config.Labels \"main_computer.mother.not_a_proof_guardian\" }}' \"$cid\" 2>/dev/null || true)\"",
        "  not_voter=\"$(docker inspect -f '{{ index .Config.Labels \"main_computer.mother.not_a_validator_voter\" }}' \"$cid\" 2>/dev/null || true)\"",
        "  not_activation=\"$(docker inspect -f '{{ index .Config.Labels \"main_computer.mother.not_an_activation_guardian\" }}' \"$cid\" 2>/dev/null || true)\"",
        "  post_work_shim=\"$(docker inspect -f '{{ index .Config.Labels \"main_computer.mother.post_work_shim\" }}' \"$cid\" 2>/dev/null || true)\"",
        "  cmd_json=\"$(docker inspect -f '{{ json .Config.Cmd }}' \"$cid\" 2>/dev/null || true)\"",
        "  case \"$cmd_json\" in",
        "    *mother-retired-helper-shim*) cmd_has_marker=true ;;",
        "    *) cmd_has_marker=false ;;",
        "  esac",
        "  is_mimic=false",
        "  if [ \"$image\" = \"alpine:3.20\" ] && [ \"$retired\" = \"true\" ] && [ \"$cleanup_scope\" = \"helper-cleanup2-yagni\" ] && [ \"$not_proof\" = \"true\" ] && [ \"$not_voter\" = \"true\" ] && [ \"$not_activation\" = \"true\" ] && [ \"$cmd_has_marker\" = \"true\" ]; then",
        "    is_mimic=true",
        "  fi",
        "  cmd_b64=\"$(printf '%s' \"$cmd_json\" | base64 2>/dev/null | tr -d '\n' || true)\"",
        "  diag \"phase=$phase expected_container=$cname helper=$helper project=$project found=true match_count=$count container_id=$cid image=$image status=$status running=$running health=$health compose_project=$compose_project compose_service=$compose_service retired_helper_mimic=$retired cleanup_scope=$cleanup_scope helper_label=$helper_label not_a_proof_guardian=$not_proof not_a_validator_voter=$not_voter not_an_activation_guardian=$not_activation post_work_shim=$post_work_shim cmd_has_marker=$cmd_has_marker is_cleanup2_mimic=$is_mimic cmd_json_base64=$cmd_b64\"",
        "}",
        "remove_expected_container_before_recreate() {",
        "  uuid=\"$1\"",
        "  helper=\"$2\"",
        "  project=\"$3\"",
        "  cname=\"${helper}-${uuid}\"",
        "  branch \"phase=remove_expected_container_before_recreate_enter service_uuid=$uuid helper=$helper expected_container=$cname project=$project\"",
        "  ids=\"$(docker ps -aq --filter \"name=^/${cname}$\" || true)\"",
        "  count=\"$(printf '%s\\n' \"$ids\" | sed '/^$/d' | wc -l | tr -d ' ')\"",
        "  if [ \"$count\" = \"0\" ]; then",
        "    branch \"phase=remove_expected_container_before_recreate_no_match service_uuid=$uuid helper=$helper expected_container=$cname project=$project match_count=$count\"",
        "    diag \"phase=remove_expected_container_before_recreate expected_container=$cname helper=$helper project=$project found=false match_count=0 removed=false\"",
        "    return 0",
        "  fi",
        "  if [ \"$count\" != \"1\" ]; then",
        "    branch \"phase=remove_expected_container_before_recreate_non_unique service_uuid=$uuid helper=$helper expected_container=$cname project=$project match_count=$count\"",
        "    diag \"phase=remove_expected_container_before_recreate expected_container=$cname helper=$helper project=$project refused=true reason=non_unique_match match_count=$count removed=false\"",
        "    return 1",
        "  fi",
        "  cid=\"$(printf '%s\\n' \"$ids\" | sed -n '1p')\"",
        "  image=\"$(docker inspect -f '{{ .Config.Image }}' \"$cid\" 2>/dev/null || true)\"",
        "  status=\"$(docker inspect -f '{{ .State.Status }}' \"$cid\" 2>/dev/null || true)\"",
        "  branch \"phase=remove_expected_container_before_recreate_before_rm service_uuid=$uuid helper=$helper expected_container=$cname project=$project container_id=$cid image=$image status=$status\"",
        "  diag \"phase=remove_expected_container_before_recreate expected_container=$cname helper=$helper project=$project container_id=$cid image=$image status=$status removing=true\"",
        "  docker rm -f \"$cid\"",
        "}",
        "echo mother-helper-cleanup2-started",
        "branch phase=script_start",
        "diag phase=script_start",
        "docker version >/tmp/mother-helper-cleanup2-docker-version.txt",
        "diag_file script_start docker_version /tmp/mother-helper-cleanup2-docker-version.txt",
        "docker compose version >/tmp/mother-helper-cleanup2-compose-version.txt",
        "diag_file script_start docker_compose_version /tmp/mother-helper-cleanup2-compose-version.txt",
        "",
        "find_project() {",
        "  uuid=\"$1\"",
        "  ids=\"$(docker ps -aq --filter \"name=$uuid\" || true)\"",
        "  diag \"phase=find_project service_uuid=$uuid matched_container_ids=$(printf '%s' \"$ids\" | tr '\n' ',')\"",
        "  for c in $ids; do",
        "    project=\"$(docker inspect -f '{{ index .Config.Labels \"com.docker.compose.project\" }}' \"$c\" 2>/dev/null || true)\"",
        "    workdir=\"$(docker inspect -f '{{ index .Config.Labels \"com.docker.compose.project.working_dir\" }}' \"$c\" 2>/dev/null || true)\"",
        "    image=\"$(docker inspect -f '{{ .Config.Image }}' \"$c\" 2>/dev/null || true)\"",
        "    name=\"$(docker inspect -f '{{ .Name }}' \"$c\" 2>/dev/null || true)\"",
        "    diag \"phase=find_project_candidate service_uuid=$uuid container_id=$c name=$name image=$image project=$project workdir=$workdir\"",
        "    if [ -n \"$project\" ] && [ \"$project\" != '<no value>' ]; then",
        "      diag \"phase=find_project_selected service_uuid=$uuid container_id=$c project=$project workdir=$workdir\"",
        "      printf '%s\n%s\n' \"$project\" \"$workdir\"",
        "      return 0",
        "    fi",
        "  done",
        "  echo \"compose project not found for service_uuid=$uuid\" >&2",
        "  diag \"phase=find_project_failed service_uuid=$uuid\"",
        "  return 1",
        "}",
        "",
        "run_target() {",
        "  uuid=\"$1\"",
        "  compose_file=\"$2\"",
        "  shift 2",
        "  branch \"phase=target_begin_before_find_project service_uuid=$uuid helpers=$*\"",
        "  info=\"$(find_project \"$uuid\")\"",
        "  project=\"$(printf '%s\n' \"$info\" | sed -n '1p')\"",
        "  workdir=\"$(printf '%s\n' \"$info\" | sed -n '2p')\"",
        "  branch \"phase=target_begin_after_find_project service_uuid=$uuid project=$project workdir=$workdir helpers=$*\"",
        "  diag \"phase=target_begin service_uuid=$uuid project=$project workdir=$workdir helpers=$* compose_file=$compose_file\"",
        "  docker_ps_snapshot before \"$uuid\"",
        "  for helper in \"$@\"; do",
        "    inspect_expected before \"$uuid\" \"$helper\" \"$project\"",
        "  done",
        "  for helper in \"$@\"; do",
        "    branch \"phase=remove_loop_before_call service_uuid=$uuid helper=$helper expected_container=${helper}-${uuid} project=$project\"",
        "    remove_expected_container_before_recreate \"$uuid\" \"$helper\" \"$project\"",
        "  done",
        "  out=\"$(mktemp /tmp/mother-helper-cleanup2-compose-stdout.XXXXXX)\"",
        "  err=\"$(mktemp /tmp/mother-helper-cleanup2-compose-stderr.XXXXXX)\"",
        "  branch \"phase=docker_compose_before_start service_uuid=$uuid project=$project helpers=$*\"",
        "  if [ -n \"$workdir\" ] && [ \"$workdir\" != '<no value>' ] && [ -d \"$workdir\" ]; then",
        "    diag \"phase=docker_compose_start service_uuid=$uuid project=$project workdir=$workdir command=docker compose -p $project -f $compose_file --project-directory $workdir up -d --no-deps --force-recreate $*\"",
        "    set +e",
        "    docker compose -p \"$project\" -f \"$compose_file\" --project-directory \"$workdir\" up -d --no-deps --force-recreate \"$@\" >\"$out\" 2>\"$err\"",
        "    rc=\"$?\"",
        "    set -e",
        "  else",
        "    diag \"phase=docker_compose_start service_uuid=$uuid project=$project workdir=$workdir command=docker compose -p $project -f $compose_file up -d --no-deps --force-recreate $*\"",
        "    set +e",
        "    docker compose -p \"$project\" -f \"$compose_file\" up -d --no-deps --force-recreate \"$@\" >\"$out\" 2>\"$err\"",
        "    rc=\"$?\"",
        "    set -e",
        "  fi",
        "  branch \"phase=docker_compose_after_exit service_uuid=$uuid project=$project exit_code=$rc\"",
        "  diag \"phase=docker_compose_exit service_uuid=$uuid project=$project exit_code=$rc\"",
        "  diag_file docker_compose_stdout stdout \"$out\"",
        "  diag_file docker_compose_stderr stderr \"$err\"",
        "  cat \"$out\"",
        "  cat \"$err\" >&2",
        "  docker_ps_snapshot after \"$uuid\"",
        "  for helper in \"$@\"; do",
        "    inspect_expected after \"$uuid\" \"$helper\" \"$project\"",
        "  done",
        "  rm -f \"$out\" \"$err\"",
        "  return \"$rc\"",
        "}",
        "",
    ]

    for index, target in enumerate(targets, start=1):
        service_uuid = _uuid(target.get("service_uuid"), "cleanup2 target service_uuid")
        helpers = tuple(_helper_name(item) for item in target.get("helper_names") or ())
        if not helpers:
            continue
        compose_b64 = str(target.get("compose_b64") or "").strip()
        if not compose_b64:
            raise MotherHelperCleanup2YagniError(
                "MOTHER_HELPER_CLEANUP2_YAGNI_CLEANUP2_PAYLOAD_INVALID",
                "cleanup2 target is missing patched Compose content",
            )
        b64_var = f"/tmp/mother-helper-cleanup2-{index}.yml.b64"
        compose_file = f"/tmp/mother-helper-cleanup2-{index}.yml"
        target_payload_message = (
            f"phase=target_payload index={index} service_uuid={service_uuid} "
            f"patched_compose_sha256={target.get('patched_compose_sha256') or ''} "
            f"docker_compose_cli_compose_sha256={target.get('docker_compose_cli_compose_sha256') or ''} "
            f"expected_container_names={','.join(f'{helper}-{service_uuid}' for helper in helpers)} "
            f"helpers={','.join(helpers)}"
        )
        lines.extend(
            [
                "branch " + _single_quote(target_payload_message),
                "diag " + _single_quote(target_payload_message),
                f"cat > {_single_quote(b64_var)} <<'MOTHER_HELPER_CLEANUP2_COMPOSE_{index}'",
                compose_b64,
                f"MOTHER_HELPER_CLEANUP2_COMPOSE_{index}",
                f"base64 -d {_single_quote(b64_var)} > {_single_quote(compose_file)}",
                "diag "
                + _single_quote(f"phase=compose_decoded index={index} service_uuid={service_uuid} compose_file={compose_file}"),
                "diag_file " + _single_quote(f"compose_decoded_{index}") + " " + _single_quote("patched_compose") + " " + _single_quote(compose_file),
                "branch " + _single_quote(f"phase=run_target_call index={index} service_uuid={service_uuid} helpers={','.join(helpers)}"),
                "run_target "
                + " ".join(
                    [_single_quote(service_uuid), _single_quote(compose_file)]
                    + [_single_quote(helper) for helper in helpers]
                ),
                "",
            ]
        )

    lines.extend(
        [
            "touch /tmp/mother-helper-cleanup2-done",
            "branch phase=script_complete",
            "diag phase=script_complete",
            "echo mother-helper-cleanup2-complete",
            "exit 0",
        ]
    )
    return "\n".join(lines) + "\n"


def _escape_docker_compose_interpolation(text: str) -> str:
    """Preserve shell variables when embedding a script in a Docker Compose value."""
    return text.replace("$", "$$")


def _cleanup2_compose(service_name: str, targets: list[Mapping[str, Any]]) -> str:
    name = _identifier(service_name, "cleanup2 service name")
    script = _escape_docker_compose_interpolation(_cleanup2_script(targets))
    compose = {
        "services": {
            name: {
                "image": "docker:27-cli",
                "command": ["sh", "-lc", script],
                "volumes": [
                    "/var/run/docker.sock:/var/run/docker.sock",
                    "/data/coolify:/data/coolify:ro",
                ],
                "restart": "no",
                "labels": {
                    "main_computer.mother.component": "cleanup2-helper-recreate",
                    "main_computer.mother.cleanup_scope": "helper-cleanup2-yagni",
                    "main_computer.mother.not_a_validator": "true",
                    "main_computer.mother.not_a_chain_service": "true",
                },
                "healthcheck": {
                    "test": ["CMD-SHELL", "test -f /tmp/mother-helper-cleanup2-done"],
                    "interval": "5s",
                    "timeout": "2s",
                    "retries": 3,
                    "start_period": "1s",
                },
            }
        }
    }
    return yaml.safe_dump(compose, sort_keys=False)


def _cleanup2_service_name(controller_id: str) -> str:
    controller = _identifier(controller_id, "controller_id").replace("_", "-")
    return f"{CLEANUP2_PREFIX}-{controller}-{_stamp().lower()}"


def _cleanup2_service_status(payload: object) -> str:
    decoded = _decode_payload(payload)
    if isinstance(decoded, Mapping):
        return str(decoded.get("status") or decoded.get("service_status") or "").strip()
    return ""



def _iter_log_text_values(value: object) -> list[str]:
    """Return likely log text values without scanning compose/source fields.

    Service detail payloads contain the generated cleanup2 script.  Scanning every
    string field causes false positives with literal ``$$helper`` and ``$$cname``
    from saved Compose.  This helper only accepts string leaves that are plausibly
    log/output fields, plus a bare string response from a logs endpoint.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        result: list[str] = []
        for key, child in value.items():
            key_text = str(key).lower()
            if key_text in {"docker_compose", "docker_compose_raw", "command", "commands", "compose", "raw"}:
                continue
            if key_text in {"log", "logs", "message", "output", "stdout", "stderr", "content", "data"}:
                result.extend(_iter_log_text_values(child))
            elif isinstance(child, (list, tuple)):
                result.extend(_iter_log_text_values(child))
            elif isinstance(child, Mapping):
                result.extend(_iter_log_text_values(child))
        return result
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for item in value:
            result.extend(_iter_log_text_values(item))
        return result
    return []


def _parse_runtime_diagnostic_line(line: str) -> dict[str, str] | None:
    if RUNTIME_DIAGNOSTIC_PREFIX not in line:
        return None
    text = line.split(RUNTIME_DIAGNOSTIC_PREFIX, 1)[1].strip()
    event: dict[str, str] = {}
    for token in text.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        key = key.strip()
        if not key:
            continue
        event[key] = value.strip()
    return event if event else None


def _runtime_diagnostic_events_from_payload(payload: object) -> list[dict[str, str]]:
    decoded = _decode_payload(payload)
    events: list[dict[str, str]] = []
    for text_value in _iter_log_text_values(decoded):
        for line in str(text_value).splitlines():
            event = _parse_runtime_diagnostic_line(line)
            if event is not None:
                events.append(event)
    return events


def _runtime_diagnostic_summary(events: list[dict[str, str]], targets: list[Mapping[str, Any]]) -> dict[str, Any]:
    expected_helpers: list[str] = []
    expected_containers: list[str] = []
    expected_remove_node_helpers: list[str] = []
    for target in targets:
        service_uuid = str(target.get("service_uuid") or "")
        for helper in target.get("helper_names") or []:
            helper_text = str(helper)
            if helper_text not in expected_helpers:
                expected_helpers.append(helper_text)
            container = f"{helper_text}-{service_uuid}"
            if container not in expected_containers:
                expected_containers.append(container)
            if helper_text.startswith("mother-node-remove-voter-") and helper_text not in expected_remove_node_helpers:
                expected_remove_node_helpers.append(helper_text)

    phase_counts: dict[str, int] = {}
    for event in events:
        phase = str(event.get("phase") or "")
        if phase:
            phase_counts[phase] = phase_counts.get(phase, 0) + 1

    expected_phase_observed = {phase: phase_counts.get(phase, 0) > 0 for phase in RUNTIME_DIAGNOSTIC_PHASES}
    expected_helper_remove_reached = {
        helper: any(
            event.get("phase") == "remove_expected_container_before_recreate" and event.get("helper") == helper
            for event in events
        )
        for helper in expected_helpers
    }
    expected_container_remove_reached = {
        container: any(
            event.get("phase") == "remove_expected_container_before_recreate" and event.get("expected_container") == container
            for event in events
        )
        for container in expected_containers
    }
    remove_node_helpers_seen = [
        helper for helper in expected_remove_node_helpers if expected_helper_remove_reached.get(helper) is True
    ]

    reason = None
    reason_detail: dict[str, Any] = {}
    if not events:
        reason = "runtime-diagnostics-not-observed"
    elif expected_phase_observed.get("script_start") is not True:
        reason = "cleanup2-script-start-not-observed"
    elif expected_phase_observed.get("target_payload") is not True:
        reason = "cleanup2-script-started-before-target-payload"
    elif expected_phase_observed.get("find_project_failed") is True:
        reason = "compose-project-discovery-failed"
        reason_detail["failed_service_uuids"] = sorted(
            {event.get("service_uuid") for event in events if event.get("phase") == "find_project_failed" and event.get("service_uuid")}
        )
    elif expected_phase_observed.get("target_begin") is not True:
        reason = "target-runner-not-reached-after-project-discovery"
    elif expected_phase_observed.get("remove_expected_container_before_recreate") is not True:
        reason = "remove-loop-not-reached"
    elif expected_remove_node_helpers and not remove_node_helpers_seen:
        reason = "remove-node-helper-remove-loop-not-reached"
    else:
        failing_exits = [
            event for event in events
            if event.get("phase") == "docker_compose_exit" and event.get("exit_code") not in {None, "", "0"}
        ]
        if failing_exits:
            reason = "docker-compose-recreate-failed"
            reason_detail["docker_compose_exit_events"] = failing_exits[:5]
        else:
            after_not_mimic = [
                event for event in events
                if event.get("phase") == "after"
                and event.get("expected_container") in expected_containers
                and event.get("is_cleanup2_mimic") == "false"
            ]
            if after_not_mimic:
                reason = "post-recreate-container-not-mimic"
                reason_detail["after_not_mimic_events"] = after_not_mimic[:5]
            else:
                reason = "runtime-diagnostics-observed-without-runtime-failure"

    return {
        "observed": bool(events),
        "event_count": len(events),
        "events_sample": events[:20],
        "phase_counts": phase_counts,
        "expected_phase_observed": expected_phase_observed,
        "expected_helpers": expected_helpers,
        "expected_containers": expected_containers,
        "expected_remove_node_helpers": expected_remove_node_helpers,
        "expected_helper_remove_reached": expected_helper_remove_reached,
        "expected_container_remove_reached": expected_container_remove_reached,
        "remove_node_helpers_seen": remove_node_helpers_seen,
        "remove_node_helper_cleanup_reached": bool(remove_node_helpers_seen) if expected_remove_node_helpers else False,
        "why_cleanup_action_not_proven": reason,
        "why_cleanup_action_not_proven_detail": reason_detail,
    }


def _cleanup2_log_probe(
    *,
    controller: CoolifyController,
    controller_id: str,
    endpoint: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        response = _http(
            controller,
            "GET",
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        events = _runtime_diagnostic_events_from_payload(response.get("payload"))
        observation = {
            "method": "GET",
            "endpoint": endpoint,
            "status": response["status"],
            "ok": response["ok"],
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "controller_id": controller_id,
            "phase": "cleanup2-runtime-diagnostic-log-probe",
            "runtime_diagnostics_observed": bool(events),
            "runtime_diagnostic_event_count": len(events),
        }
        observations.append(observation)
        return {
            "source": f"coolify-get:{endpoint}",
            "endpoint": endpoint,
            "http_ok": response["ok"],
            "http_status": response["status"],
            "observed": bool(events),
            "event_count": len(events),
            "events": events,
            "error_code": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "source": f"coolify-get:{endpoint}",
            "endpoint": endpoint,
            "http_ok": False,
            "http_status": None,
            "observed": False,
            "event_count": 0,
            "events": [],
            "error_code": getattr(exc, "code", type(exc).__name__),
            "error": str(exc),
        }


def _collect_cleanup2_runtime_diagnostics(
    *,
    controller: CoolifyController,
    controller_id: str,
    service_uuid: str,
    service_name: str,
    targets: list[Mapping[str, Any]],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    service = _uuid(service_uuid, "cleanup2 service_uuid")
    encoded_service = urllib.parse.quote(service, safe="")
    encoded_service_name = urllib.parse.quote(service_name, safe="")
    probes: list[dict[str, Any]] = []

    # Coolify service-compose logs require the compose service name selector.
    # The unqualified /logs route can return 400 even when logs exist.
    selected_logs_endpoint = f"/api/v1/services/{encoded_service}/logs?sub_service_name={encoded_service_name}"
    probes.append(
        _cleanup2_log_probe(
            controller=controller,
            controller_id=controller_id,
            endpoint=selected_logs_endpoint,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            observations=observations,
        )
    )

    if not any(item.get("observed") for item in probes):
        legacy_logs_endpoint = f"/api/v1/services/{encoded_service}/logs"
        probes.append(
            _cleanup2_log_probe(
                controller=controller,
                controller_id=controller_id,
                endpoint=legacy_logs_endpoint,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                observations=observations,
            )
        )

    events: list[dict[str, str]] = []
    for probe in probes:
        events.extend(list(probe.get("events") or []))

    summary = _runtime_diagnostic_summary(events, targets)
    summary["sources"] = [
        {
            "source": str(probe.get("source") or ""),
            "endpoint": str(probe.get("endpoint") or ""),
            "http_ok": probe.get("http_ok"),
            "http_status": probe.get("http_status"),
            "observed": bool(probe.get("observed")),
            "event_count": int(probe.get("event_count") or 0),
            "error_code": probe.get("error_code"),
        }
        for probe in probes
    ]
    summary["sources_with_events"] = [item["source"] for item in summary["sources"] if item["observed"]]
    if not events:
        summary["why_cleanup_action_not_proven"] = "runtime-logs-unavailable"
        summary["why_cleanup_action_not_proven_detail"] = {
            "selected_logs_endpoint": selected_logs_endpoint,
            "probe_statuses": summary["sources"],
            "note": (
                "No runtime diagnostic log lines were returned by read-only Coolify log probes, "
                "so cleanup2 cannot prove whether the remove loop ran."
            ),
        }
    return summary


def _wait_for_cleanup2_exit(
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
    targets: list[Mapping[str, Any]],
) -> dict[str, Any]:
    service = _uuid(service_uuid, "cleanup2 service_uuid")
    endpoint = f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
    started = time.monotonic()
    observed_statuses: list[str] = []
    observation_count = 0

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
        status = _cleanup2_service_status(response.get("payload"))
        observed_statuses.append(status)
        observation_count += 1
        observation = {
            "method": "GET",
            "endpoint": endpoint,
            "status": response["status"],
            "ok": response["ok"],
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "controller_id": controller_id,
            "phase": "cleanup2-temporary-service-exit-result",
            "service_uuid": service,
            "service_name": service_name,
            "service_status": status,
            "post_restart_health_poll_performed": False,
        }
        observations.append(observation)

        if response["ok"] and status.startswith("exited"):
            elapsed = time.monotonic() - started
            runtime_diagnostics = _collect_cleanup2_runtime_diagnostics(
                controller=controller,
                controller_id=controller_id,
                service_uuid=service,
                service_name=service_name,
                targets=targets,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                observations=observations,
            )
            return {
                "completed": True,
                "reason": "cleanup2-exited",
                "service_uuid": service,
                "service_name": service_name,
                "service_status": status,
                "first_status": observed_statuses[0] if observed_statuses else None,
                "final_status": status,
                "observed_statuses": observed_statuses,
                "observation_count": observation_count,
                "wait_seconds": int(max_wait_seconds),
                "wait_milliseconds": int(elapsed * 1000),
                "post_restart_health_poll_performed": False,
                "runtime_diagnostics": runtime_diagnostics,
                "runtime_diagnostics_observed": runtime_diagnostics.get("observed") is True,
                "remove_node_helper_cleanup_reached": runtime_diagnostics.get("remove_node_helper_cleanup_reached"),
                "why_cleanup_action_not_proven": runtime_diagnostics.get("why_cleanup_action_not_proven"),
            }

        elapsed = time.monotonic() - started
        if elapsed >= max_wait_seconds:
            runtime_diagnostics = _collect_cleanup2_runtime_diagnostics(
                controller=controller,
                controller_id=controller_id,
                service_uuid=service,
                service_name=service_name,
                targets=targets,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                observations=observations,
            )
            return {
                "completed": False,
                "reason": "cleanup2-exit-timeout",
                "service_uuid": service,
                "service_name": service_name,
                "service_status": status,
                "first_status": observed_statuses[0] if observed_statuses else None,
                "final_status": status,
                "observed_statuses": observed_statuses,
                "observation_count": observation_count,
                "wait_seconds": int(max_wait_seconds),
                "wait_milliseconds": int(elapsed * 1000),
                "post_restart_health_poll_performed": False,
                "runtime_diagnostics": runtime_diagnostics,
                "runtime_diagnostics_observed": runtime_diagnostics.get("observed") is True,
                "remove_node_helper_cleanup_reached": runtime_diagnostics.get("remove_node_helper_cleanup_reached"),
                "why_cleanup_action_not_proven": runtime_diagnostics.get("why_cleanup_action_not_proven"),
            }

        time.sleep(min(poll_interval_seconds, max(0.0, max_wait_seconds - elapsed)))


def _run_cleanup2_for_controller(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    targets: list[Mapping[str, Any]],
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    progress: ProgressCallback | None,
) -> dict[str, Any]:
    controller = _controller(private_state, network=network, controller_id=controller_id)
    controller_config = _controller_config(private_state, network=network, controller_id=controller_id)
    observations: list[dict[str, Any]] = []
    cleanup_service_uuid: str | None = None
    completion_result: dict[str, Any] | None = None
    delete_receipt: dict[str, Any] | None = None
    service_name = _cleanup2_service_name(controller_id)
    try:
        _emit_progress(progress, "cleanup2", "installing cleanup2 service", controller_id=controller_id, target_count=len(targets))
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

        compose = _cleanup2_compose(service_name, targets)
        cleanup2_script = _cleanup2_script(targets)
        cleanup2_compose_escaped_script = _escape_docker_compose_interpolation(cleanup2_script)
        cleanup2_diagnostics = {
            "cleanup2_compose_sha256": hashlib.sha256(compose.encode("utf-8")).hexdigest(),
            "cleanup2_script_sha256": hashlib.sha256(cleanup2_script.encode("utf-8")).hexdigest(),
            "cleanup2_compose_escaped_script_sha256": hashlib.sha256(
                cleanup2_compose_escaped_script.encode("utf-8")
            ).hexdigest(),
            "runtime_diagnostics_log_prefix": RUNTIME_DIAGNOSTIC_PREFIX,
            "runtime_diagnostics_capture": (
                "emitted by the temporary docker:27-cli service to stdout/stderr; "
                "outer cleanup2 now reads the service logs with sub_service_name and records why the remove loop was or was not observed"
            ),
            "runtime_diagnostics_include": [
                "docker version",
                "docker compose version",
                "matched container ids used for project discovery",
                "actual discovered compose project",
                "actual discovered compose working_dir",
                "actual docker compose up command",
                "docker compose stdout base64",
                "docker compose stderr base64",
                "docker compose exit code",
                "docker ps snapshots before and after",
                "docker inspect mimic verdict before and after for each expected helper container",
            ],
            "target_diagnostics": [_cleanup2_target_diagnostic(target) for target in targets],
            "temporary_service_strategy": (
                "create docker:27-cli service with /var/run/docker.sock; start it; wait for temporary service exit; "
                "leave the temporary service for manual docker logs inspection"
            ),
            "debug_destructive_leave_cleanup2_service_after_proof_guardian": False,
            "debug_destructive_warning": None,
            "parent_restart_performed": False,
            "parent_redeploy_performed": False,
            "post_restart_health_poll_performed": False,
        }
        _emit_progress(
            progress,
            "debug",
            "branch cleanup2 script generated",
            controller_id=controller_id,
            service_name=service_name,
            target_count=len(targets),
            cleanup2_script_sha256=cleanup2_diagnostics["cleanup2_script_sha256"],
            remove_node_helpers=[
                helper
                for target in targets
                for helper in list(target.get("helper_names") or [])
                if str(helper).startswith("mother-node-remove-voter-")
            ],
        )
        body = _temporary_service_body(controller_config, service_name, compose)
        body["environment_uuid"] = environment_uuid
        body["description"] = "Ephemeral Mother cleanup2 exact helper Docker Compose recreate"
        body["instant_deploy"] = False
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
            "cleanup_scope": "cleanup2-helper-compose-recreate",
            "target_count": len(targets),
            "request_body_sha256": hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        }
        observations.append({key: create_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
        if not create_response["ok"]:
            return {
                "controller_id": controller_id,
                "status": "failed",
                "reason": "create-failed",
                "create": create_receipt,
                "start": None,
                "health": None,
                "delete": None,
                "observations": observations,
                "target_count": len(targets),
                "targets": _cleanup2_target_summary(targets),
                "diagnostics": cleanup2_diagnostics,
            }

        cleanup_service_uuid = _application_uuid(create_response.get("payload"))
        create_receipt["service_uuid"] = cleanup_service_uuid
        _emit_progress(
            progress,
            "debug",
            "branch cleanup2 service created",
            controller_id=controller_id,
            service_name=service_name,
            cleanup_service_uuid=cleanup_service_uuid,
            target_count=len(targets),
        )
        start_endpoint = f"/api/v1/services/{urllib.parse.quote(cleanup_service_uuid, safe='')}/start"
        _emit_progress(progress, "cleanup2", "starting cleanup2 service", controller_id=controller_id, cleanup_service_uuid=cleanup_service_uuid)
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
            "service_uuid": cleanup_service_uuid,
            "service_name": service_name,
            "cleanup_scope": "cleanup2-helper-compose-recreate",
        }
        observations.append({key: start_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
        if not start_response["ok"]:
            return {
                "controller_id": controller_id,
                "cleanup_service_uuid": cleanup_service_uuid,
                "status": "failed",
                "reason": "start-failed",
                "create": create_receipt,
                "start": start_receipt,
                "health": None,
                "delete": None,
                "observations": observations,
                "target_count": len(targets),
                "targets": _cleanup2_target_summary(targets),
                "diagnostics": cleanup2_diagnostics,
            }

        _emit_progress(
            progress,
            "debug",
            "branch cleanup2 wait begin",
            controller_id=controller_id,
            service_name=service_name,
            cleanup_service_uuid=cleanup_service_uuid,
        )
        completion_result = _wait_for_cleanup2_exit(
            controller=controller,
            controller_id=controller_id,
            service_uuid=cleanup_service_uuid,
            service_name=service_name,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            opener=opener,
            observations=observations,
            targets=targets,
        )
        _emit_progress(
            progress,
            "debug",
            "branch cleanup2 wait complete",
            controller_id=controller_id,
            service_name=service_name,
            cleanup_service_uuid=cleanup_service_uuid,
            completed=completion_result.get("completed"),
            final_status=completion_result.get("final_status"),
            runtime_diagnostics_observed=completion_result.get("runtime_diagnostics_observed"),
            remove_node_helper_cleanup_reached=completion_result.get("remove_node_helper_cleanup_reached"),
            why_cleanup_action_not_proven=completion_result.get("why_cleanup_action_not_proven"),
        )
        ok = completion_result.get("completed") is True
        return {
            "controller_id": controller_id,
            "cleanup_service_uuid": cleanup_service_uuid,
            "status": "pass" if ok else "failed",
            "reason": None if ok else completion_result.get("reason", "cleanup2-not-exited"),
            "create": create_receipt,
            "start": start_receipt,
            "completion": completion_result,
            "health": None,
            "delete": None,
            "observations": observations,
            "target_count": len(targets),
            "targets": _cleanup2_target_summary(targets),
            "diagnostics": cleanup2_diagnostics,
            "docker_touched": start_receipt["ok"] is True,
            "parent_redeploy_performed": False,
            "parent_restart_performed": False,
            "post_restart_health_poll_performed": False,
            "debug_destructive_exit_after_proof_guardian_cleanup2": False,
            "debug_destructive_cleanup2_service_left_for_inspection": True,
        }
    finally:
        if cleanup_service_uuid is not None:
            endpoint = f"/api/v1/services/{urllib.parse.quote(cleanup_service_uuid, safe='')}"
            delete_receipt = {
                "method": "DELETE",
                "endpoint": endpoint,
                "status": None,
                "ok": None,
                "skipped": True,
                "reason": "cleanup2-temporary-service-delete-disabled-for-manual-log-inspection",
                "service_uuid": cleanup_service_uuid,
                "service_name": service_name,
                "cleanup_scope": "cleanup2-temporary-service-delete-disabled",
            }
            if completion_result is not None:
                completion_result["temporary_service_delete"] = delete_receipt


def _cleanup2_target_summary(targets: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "node": str(item.get("node") or ""),
            "service_uuid": str(item.get("service_uuid") or ""),
            "helper_names": list(item.get("helper_names") or []),
            "patched_compose_sha256": str(item.get("patched_compose_sha256") or ""),
            "docker_compose_cli_compose_sha256": str(item.get("docker_compose_cli_compose_sha256") or ""),
            "docker_compose_cli_removed_service_keys": dict(item.get("docker_compose_cli_removed_service_keys") or {}),
        }
        for item in targets
    ]


def _receipt_from_detail(receipt: Mapping[str, Any], *, controller_id: str, node: str, service_uuid: str) -> dict[str, Any]:
    result = dict(receipt)
    result["controller_id"] = controller_id
    result["node"] = node
    result["service_uuid"] = service_uuid
    return result


def _coerce_subprocess_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _tail_text(value: object, *, limit: int = 8000) -> str:
    text = _coerce_subprocess_text(value)
    if len(text) <= limit:
        return text
    return text[-limit:]


def _run_block_advance_waiter(
    *,
    network: str,
    controller_id: str,
    target_node: str,
    service_uuid: str,
    runtime_state_root: str | Path,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run the standalone block-advance waiter after one parent stack restart."""

    argv = [
        sys.executable,
        BLOCK_ADVANCE_WAITER_SCRIPT,
        network,
        controller_id,
        target_node,
        "--runtime-state-root",
        str(runtime_state_root),
        "--quiet",
    ]
    started = time.monotonic()
    receipt: dict[str, Any] = {
        "script": BLOCK_ADVANCE_WAITER_SCRIPT,
        "argv": list(argv),
        "cwd": str(REPO_ROOT),
        "invoked": True,
        "controller_id": controller_id,
        "node": target_node,
        "target_node": target_node,
        "service_uuid": service_uuid,
        "direct_besu_rpc_from_cleanup": False,
        "direct_chain_height_polling_from_cleanup": False,
    }

    run = runner or subprocess.run
    try:
        completed = run(
            argv,
            cwd=str(REPO_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=BLOCK_ADVANCE_WAITER_SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        receipt.update(
            {
                "status": "failed",
                "reason": "block-advance-waiter-timeout",
                "returncode": None,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "stdout_sha256": hashlib.sha256(_coerce_subprocess_text(exc.stdout).encode("utf-8")).hexdigest(),
                "stdout_byte_length": len(_coerce_subprocess_text(exc.stdout).encode("utf-8")),
                "stderr_tail": _tail_text(exc.stderr),
                "result": None,
            }
        )
        return receipt
    except Exception as exc:  # pragma: no cover - defensive subprocess boundary
        receipt.update(
            {
                "status": "failed",
                "reason": "block-advance-waiter-exec-failed",
                "returncode": None,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "error": str(exc),
                "stdout_sha256": None,
                "stdout_byte_length": 0,
                "stderr_tail": "",
                "result": None,
            }
        )
        return receipt

    stdout = _coerce_subprocess_text(getattr(completed, "stdout", ""))
    stderr = _coerce_subprocess_text(getattr(completed, "stderr", ""))
    returncode = int(getattr(completed, "returncode", 1))
    receipt.update(
        {
            "returncode": returncode,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
            "stdout_byte_length": len(stdout.encode("utf-8")),
            "stderr_tail": _tail_text(stderr),
        }
    )

    parsed: Any = None
    parse_error: str | None = None
    stripped = stdout.strip()
    if stripped:
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            parse_error = f"{exc.msg} at line {exc.lineno} column {exc.colno}"
    else:
        parse_error = "stdout-empty"

    receipt["result"] = parsed if isinstance(parsed, Mapping) else None
    if parse_error is not None:
        receipt["status"] = "failed"
        receipt["reason"] = "block-advance-waiter-json-invalid" if stripped else "block-advance-waiter-json-missing"
        receipt["parse_error"] = parse_error
        return receipt

    if not isinstance(parsed, Mapping):
        receipt["status"] = "failed"
        receipt["reason"] = "block-advance-waiter-json-not-object"
        return receipt

    completion = parsed.get("completion")
    completed_ok = isinstance(completion, Mapping) and completion.get("completed") is True
    parsed_status = str(parsed.get("status") or "")
    if returncode == 0 and parsed_status == "pass" and completed_ok:
        receipt["status"] = "pass"
        receipt["reason"] = str(parsed.get("reason") or completion.get("reason") or "block-advanced")
        receipt["baseline_block_number"] = completion.get("baseline_block_number")
        receipt["latest_block_number"] = completion.get("latest_block_number")
        receipt["block_advance"] = completion.get("block_advance")
        return receipt

    receipt["status"] = "failed"
    if returncode != 0:
        receipt["reason"] = "block-advance-waiter-nonzero-exit"
    elif parsed_status != "pass":
        receipt["reason"] = str(parsed.get("reason") or "block-advance-waiter-status-failed")
    else:
        receipt["reason"] = "block-advance-waiter-completion-missing"
    return receipt


def run_helper_cleanup2_yagni(
    private_state: PrivateStateReadResult,
    *,
    runtime_state_root: str | Path,
    network: str,
    topology_evidence: str | Path | None = None,
    acknowledged_topology_evidence_sha256: str | None = None,
    mode: str = "inspect",
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    max_wait_seconds: float = 60.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = urllib.request.urlopen,
    progress: ProgressCallback | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    block_advance_waiter_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    mode_name = _identifier(mode, "mode")
    if mode_name not in {"inspect", "execute"}:
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_INVALID_ARGUMENT",
            "mode must be inspect or execute",
        )
    network_id = _identifier(network, "network")
    request_timeout = _positive(timeout, "timeout")
    wait_limit = _nonnegative(max_wait_seconds, "max_wait_seconds")
    poll_interval = _nonnegative(poll_interval_seconds, "poll_interval_seconds")
    response_limit = int(max_response_bytes)
    if response_limit <= 0:
        raise MotherHelperCleanup2YagniError(
            "MOTHER_HELPER_CLEANUP2_YAGNI_INVALID_ARGUMENT",
            "max_response_bytes must be positive",
        )

    topology = _load_topology(
        runtime_state_root,
        network=network_id,
        topology_evidence=topology_evidence,
        acknowledged_sha256=acknowledged_topology_evidence_sha256,
    )
    services = topology["services"]

    patch_steps: list[dict[str, Any]] = []
    cleanup2_targets_by_controller: dict[str, list[dict[str, Any]]] = {}
    http_observations: list[dict[str, Any]] = []
    post_cleanup_readbacks: list[dict[str, Any]] = []
    parent_restart_receipts: list[dict[str, Any]] = []
    parent_restart_waits: list[dict[str, Any]] = []
    block_advance_waits: list[dict[str, Any]] = []

    for service_record in services:
        node = _identifier(service_record["node"], "topology node")
        controller_id = _identifier(service_record["controller_id"], "controller_id")
        service_uuid = _uuid(service_record["service_uuid"], "service_uuid")
        controller = _controller(private_state, network=network_id, controller_id=controller_id)

        _emit_progress(progress, "patch", "fetching parent service detail", node=node, controller_id=controller_id, service_uuid=service_uuid)
        detail = _service_detail(
            controller,
            service_uuid,
            timeout=request_timeout,
            max_response_bytes=response_limit,
            opener=opener,
        )
        http_observations.append(_receipt_from_detail(detail["receipt"], controller_id=controller_id, node=node, service_uuid=service_uuid))
        if detail.get("missing") is True:
            patch_steps.append(
                {
                    "node": node,
                    "controller_id": controller_id,
                    "service_uuid": service_uuid,
                    "status": "skipped",
                    "reason": "service-detail-404",
                    "helper_names": [],
                    "patch_receipt": None,
                    "source_field": None,
                    "source_encoding": None,
                }
            )
            continue

        compose_text, source_field, source_encoding = _compose_text_from_service_payload(detail["payload"])
        parsed_compose = _parse_compose(compose_text)
        compose_sha256 = hashlib.sha256(compose_text.encode("utf-8")).hexdigest()
        helper_names = _target_helper_names(detail["payload"], parsed_compose)
        _emit_progress(
            progress,
            "debug",
            "branch helper discovery",
            node=node,
            controller_id=controller_id,
            service_uuid=service_uuid,
            helper_names=list(helper_names),
            remove_node_helpers=[name for name in helper_names if name.startswith("mother-node-remove-voter-")],
            remove_node_helper_selected=any(name.startswith("mother-node-remove-voter-") for name in helper_names),
        )
        pre_patch_diagnostics = _parent_payload_diagnostic(
            detail["payload"],
            helper_names=helper_names,
            service_uuid=service_uuid,
            compose=parsed_compose,
            compose_sha256=compose_sha256,
            source_field=source_field,
            source_encoding=source_encoding,
        )

        if not helper_names:
            _emit_progress(
                progress,
                "debug",
                "branch no helpers selected",
                node=node,
                controller_id=controller_id,
                service_uuid=service_uuid,
            )
            patch_steps.append(
                {
                    "node": node,
                    "controller_id": controller_id,
                    "service_uuid": service_uuid,
                    "status": "no-op",
                    "reason": "no-supported-helper-services",
                    "helper_names": [],
                    "patch_receipt": None,
                    "source_field": source_field,
                    "source_encoding": source_encoding,
                    "diagnostics": {
                        "pre_patch_parent": pre_patch_diagnostics,
                    },
                }
            )
            continue

        _emit_progress(
            progress,
            "debug",
            "branch rewriting helper mimics",
            node=node,
            controller_id=controller_id,
            service_uuid=service_uuid,
            helper_names=list(helper_names),
        )
        rewritten, rewrite_summary = _rewrite_helper_mimics(
            compose_text,
            service_uuid=service_uuid,
            helper_names=helper_names,
        )
        rewritten_compose = _parse_compose(rewritten)
        rewritten_helper_diagnostics = _helper_definitions_diagnostic(
            rewritten_compose,
            helper_names=helper_names,
            service_uuid=service_uuid,
        )

        patch_receipt = None
        patch_readback = None
        if mode_name == "execute":
            _emit_progress(progress, "patch", "patching helper mimics", node=node, controller_id=controller_id, service_uuid=service_uuid, helper_names=list(helper_names))
            patch_receipt = _patch_parent_compose(
                controller,
                service_uuid,
                rewritten,
                timeout=request_timeout,
                max_response_bytes=response_limit,
                opener=opener,
            )
            http_observations.append(_receipt_from_detail(patch_receipt, controller_id=controller_id, node=node, service_uuid=service_uuid))
            if not patch_receipt["ok"]:
                patch_steps.append(
                    {
                        "node": node,
                        "controller_id": controller_id,
                        "service_uuid": service_uuid,
                        "status": "failed",
                        "reason": "patch-failed",
                        "helper_names": list(helper_names),
                        "rewrite_summary": rewrite_summary,
                        "patch_receipt": patch_receipt,
                        "source_field": source_field,
                        "source_encoding": source_encoding,
                        "diagnostics": {
                            "pre_patch_parent": pre_patch_diagnostics,
                            "rewritten_helper_definitions": rewritten_helper_diagnostics,
                        },
                    }
                )
                continue

        patched_compose_sha256 = hashlib.sha256(rewritten.encode("utf-8")).hexdigest()
        docker_compose_cli_compose, docker_compose_cli_removed_service_keys = _docker_compose_cli_apply_compose(rewritten)
        docker_compose_cli_compose_sha256 = hashlib.sha256(docker_compose_cli_compose.encode("utf-8")).hexdigest()
        if mode_name == "execute":
            patch_readback = _patch_readback_diagnostic(
                controller,
                controller_id=controller_id,
                node=node,
                service_uuid=service_uuid,
                helper_names=helper_names,
                expected_compose_sha256=patched_compose_sha256,
                timeout=request_timeout,
                max_response_bytes=response_limit,
                opener=opener,
                http_observations=http_observations,
                phase="post-patch-readback",
            )

        patch_steps.append(
            {
                "node": node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "status": "would-patch" if mode_name == "inspect" else "patched",
                "helper_names": list(helper_names),
                "rewrite_summary": rewrite_summary,
                "patch_receipt": patch_receipt,
                "source_field": source_field,
                "source_encoding": source_encoding,
                "patched_compose_sha256": patched_compose_sha256,
                "docker_compose_cli_compose_sha256": docker_compose_cli_compose_sha256,
                "docker_compose_cli_removed_service_keys": docker_compose_cli_removed_service_keys,
                "diagnostics": {
                    "pre_patch_parent": pre_patch_diagnostics,
                    "rewritten_helper_definitions": rewritten_helper_diagnostics,
                    "patch_readback": patch_readback,
                },
            }
        )
        _emit_progress(
            progress,
            "debug",
            "branch cleanup2 target queued",
            node=node,
            controller_id=controller_id,
            service_uuid=service_uuid,
            helper_names=list(helper_names),
            expected_container_names=[f"{helper}-{service_uuid}" for helper in helper_names],
            remove_node_helpers=[name for name in helper_names if name.startswith("mother-node-remove-voter-")],
        )
        cleanup2_targets_by_controller.setdefault(controller_id, []).append(
            {
                "node": node,
                "service_uuid": service_uuid,
                "helper_names": list(helper_names),
                "compose_b64": base64.b64encode(docker_compose_cli_compose.encode("utf-8")).decode("ascii"),
                "patched_compose_sha256": patched_compose_sha256,
                "docker_compose_cli_compose_sha256": docker_compose_cli_compose_sha256,
                "docker_compose_cli_removed_service_keys": docker_compose_cli_removed_service_keys,
                "target_diagnostics": {
                    "expected_container_names": [f"{helper}-{service_uuid}" for helper in helper_names],
                    "docker_compose_up_template": (
                        "docker compose -p <discovered_project> -f <decoded_patched_compose> "
                        "--project-directory <discovered_workdir_if_available> up -d --no-deps --force-recreate "
                        + " ".join(helper_names)
                    ),
                },
            }
        )

    cleanup2_steps: list[dict[str, Any]] = []
    if mode_name == "execute":
        for controller_id in sorted(cleanup2_targets_by_controller):
            targets = cleanup2_targets_by_controller[controller_id]
            if not targets:
                continue
            _emit_progress(
                progress,
                "debug",
                "branch cleanup2 controller selected",
                controller_id=controller_id,
                target_count=len(targets),
                service_uuids=[str(target.get("service_uuid") or "") for target in targets],
                remove_node_helpers=[
                    helper
                    for target in targets
                    for helper in list(target.get("helper_names") or [])
                    if str(helper).startswith("mother-node-remove-voter-")
                ],
            )
            cleanup2_step = _run_cleanup2_for_controller(
                private_state,
                network=network_id,
                controller_id=controller_id,
                targets=targets,
                timeout=request_timeout,
                max_response_bytes=response_limit,
                max_wait_seconds=wait_limit,
                poll_interval_seconds=poll_interval,
                opener=opener,
                progress=progress,
            )
            cleanup2_steps.append(cleanup2_step)

        for controller_id in sorted(cleanup2_targets_by_controller):
            controller = _controller(private_state, network=network_id, controller_id=controller_id)
            for target in cleanup2_targets_by_controller[controller_id]:
                post_cleanup_readbacks.append(
                    _patch_readback_diagnostic(
                        controller,
                        controller_id=controller_id,
                        node=str(target.get("node") or ""),
                        service_uuid=str(target.get("service_uuid") or ""),
                        helper_names=tuple(str(item) for item in target.get("helper_names") or ()),
                        expected_compose_sha256=str(target.get("patched_compose_sha256") or ""),
                        timeout=request_timeout,
                        max_response_bytes=response_limit,
                        opener=opener,
                        http_observations=http_observations,
                        phase="post-cleanup2-parent-readback",
                    )
                )

        stop_restart_sequence = False
        for controller_id in sorted(cleanup2_targets_by_controller):
            if stop_restart_sequence:
                break
            controller = _controller(private_state, network=network_id, controller_id=controller_id)
            for target in cleanup2_targets_by_controller[controller_id]:
                node = str(target.get("node") or "")
                service_uuid = str(target.get("service_uuid") or "")
                _emit_progress(
                    progress,
                    "cleanup2",
                    "restarting parent service after cleanup2",
                    node=node,
                    controller_id=controller_id,
                    service_uuid=service_uuid,
                )
                restart_receipt = _restart_parent_service(
                    controller,
                    service_uuid,
                    timeout=request_timeout,
                    max_response_bytes=response_limit,
                    opener=opener,
                )
                restart_receipt["controller_id"] = controller_id
                restart_receipt["node"] = node
                parent_restart_receipts.append(restart_receipt)
                http_observations.append(
                    {
                        key: restart_receipt[key]
                        for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")
                    }
                )

                _emit_progress(
                    progress,
                    "cleanup2",
                    "waiting before parent restart status check",
                    node=node,
                    controller_id=controller_id,
                    service_uuid=service_uuid,
                    settle_seconds=PARENT_RESTART_SETTLE_SECONDS,
                )
                wait_result = _wait_for_parent_service_running_healthy(
                    controller,
                    controller_id=controller_id,
                    node=node,
                    service_uuid=service_uuid,
                    initial_settle_seconds=PARENT_RESTART_SETTLE_SECONDS,
                    timeout=request_timeout,
                    max_response_bytes=response_limit,
                    max_wait_seconds=wait_limit,
                    poll_interval_seconds=poll_interval,
                    opener=opener,
                    sleeper=sleeper,
                    http_observations=http_observations,
                )
                parent_restart_waits.append(wait_result)

                _emit_progress(
                    progress,
                    "cleanup2",
                    "waiting for block advance after parent restart",
                    node=node,
                    controller_id=controller_id,
                    service_uuid=service_uuid,
                    delegated_script=BLOCK_ADVANCE_WAITER_SCRIPT,
                )
                block_wait_result = _run_block_advance_waiter(
                    network=network_id,
                    controller_id=controller_id,
                    target_node=node,
                    service_uuid=service_uuid,
                    runtime_state_root=runtime_state_root,
                    runner=block_advance_waiter_runner,
                )
                block_advance_waits.append(block_wait_result)
                _emit_progress(
                    progress,
                    "cleanup2",
                    "block advance waiter completed",
                    node=node,
                    controller_id=controller_id,
                    service_uuid=service_uuid,
                    status=block_wait_result.get("status"),
                    reason=block_wait_result.get("reason"),
                    baseline_block_number=block_wait_result.get("baseline_block_number"),
                    latest_block_number=block_wait_result.get("latest_block_number"),
                    block_advance=block_wait_result.get("block_advance"),
                )
                if block_wait_result.get("status") != "pass":
                    stop_restart_sequence = True
                    break

    patch_ok = all(step["status"] in {"patched", "would-patch", "no-op", "skipped"} for step in patch_steps)
    cleanup2_ok = mode_name == "inspect" or all(step.get("status") == "pass" for step in cleanup2_steps)
    parent_restart_ok = mode_name == "inspect" or all(receipt.get("ok") is True for receipt in parent_restart_receipts)
    expected_block_advance_wait_count = len(parent_restart_receipts)
    block_advance_waiter_ok = mode_name == "inspect" or (
        len(block_advance_waits) == expected_block_advance_wait_count
        and all(wait.get("status") == "pass" for wait in block_advance_waits)
    )
    block_advance_waiter = {
        "status": "pass" if block_advance_waiter_ok else "failed",
        "script": BLOCK_ADVANCE_WAITER_SCRIPT,
        "invoked": bool(block_advance_waits),
        "wait_count": len(block_advance_waits),
        "expected_wait_count": expected_block_advance_wait_count,
        "passed_count": sum(1 for wait in block_advance_waits if wait.get("status") == "pass"),
        "direct_besu_rpc_from_cleanup": False,
        "direct_chain_height_polling_from_cleanup": False,
        "waits": block_advance_waits,
    }

    status = "pass" if patch_ok and cleanup2_ok and parent_restart_ok and block_advance_waiter_ok else "failed"

    target_count = sum(len(item["helper_names"]) for items in cleanup2_targets_by_controller.values() for item in items)
    return {
        "kind": KIND,
        "schema_version": 1,
        "observed_at": _utc_now(),
        "mode": mode_name,
        "status": status,
        "network": network_id,
        "topology_evidence": {
            "path": str(topology["path"]),
            "sha256": topology["sha256"],
            "discovered_from_disk": bool(topology["discovered"]),
        },
        "service_order": [
            {
                "node": item["node"],
                "controller_id": item["controller_id"],
                "service_uuid": item["service_uuid"],
            }
            for item in services
        ],
        "patch_steps": patch_steps,
        "cleanup2_steps": cleanup2_steps,
        "post_cleanup_readbacks": post_cleanup_readbacks,
        "parent_restart_receipts": parent_restart_receipts,
        "parent_restart_waits": parent_restart_waits,
        "block_advance_waits": block_advance_waits,
        "block_advance_waiter": block_advance_waiter,
        "http_observations": http_observations,
        "summary": {
            "topology_imported": True,
            "targeted_super_node_count": len(services),
            "targeted_helper_service_count": target_count,
            "compose_patch_performed": mode_name == "execute",
            "compose_patch_accepted_count": sum(
                1 for step in patch_steps if isinstance(step.get("patch_receipt"), Mapping) and step["patch_receipt"].get("ok") is True
            ),
            "skipped_missing_service_count": sum(1 for step in patch_steps if step.get("reason") == "service-detail-404"),
            "cleanup2_controller_count": len(cleanup2_targets_by_controller),
            "cleanup2_performed": mode_name == "execute" and bool(cleanup2_steps),
            "cleanup2_passed": bool(cleanup2_steps) and all(step.get("status") == "pass" for step in cleanup2_steps) if mode_name == "execute" else None,
            "cleanup2_runtime_diagnostics_observed_count": sum(
                1
                for step in cleanup2_steps
                if isinstance(step.get("completion"), Mapping)
                and step["completion"].get("runtime_diagnostics_observed") is True
            ),
            "cleanup2_remove_node_helper_cleanup_reached": (
                any(
                    step.get("completion", {}).get("remove_node_helper_cleanup_reached") is True
                    for step in cleanup2_steps
                    if isinstance(step.get("completion"), Mapping)
                )
                if cleanup2_steps
                else None
            ),
            "cleanup2_why_cleanup_action_not_proven": [
                step.get("completion", {}).get("why_cleanup_action_not_proven")
                for step in cleanup2_steps
                if isinstance(step.get("completion"), Mapping)
                and step.get("completion", {}).get("why_cleanup_action_not_proven") is not None
            ],
            "debug_destructive_short_circuit_after_proof_guardian_cleanup2": False,
            "debug_destructive_cleanup2_services_left_for_inspection": [
                {
                    "controller_id": step.get("controller_id"),
                    "service_uuid": step.get("cleanup_service_uuid"),
                    "service_name": (
                        step.get("completion", {}).get("service_name")
                        if isinstance(step.get("completion"), Mapping)
                        else step.get("create", {}).get("service_name") if isinstance(step.get("create"), Mapping) else None
                    ),
                }
                for step in cleanup2_steps
                if step.get("cleanup_service_uuid")
            ],
            "post_cleanup_readback_count": len(post_cleanup_readbacks),
            "post_cleanup_readbacks_all_mimics_in_saved_compose": (
                all(item.get("all_target_helpers_are_cleanup2_mimics_in_saved_compose") is True for item in post_cleanup_readbacks)
                if post_cleanup_readbacks
                else None
            ),
            "parent_redeploy_performed": False,
            "parent_restart_performed": bool(parent_restart_receipts),
            "parent_restart_request_count": len(parent_restart_receipts),
            "parent_restart_wait_count": len(parent_restart_waits),
            "parent_restart_waits_all_running_healthy": (
                all(wait.get("completed") is True for wait in parent_restart_waits)
                if parent_restart_waits
                else None
            ),
            "child_public_api_restart_attempted": False,
            "temporary_helper_apply_service_created": False,
            "post_restart_health_poll_performed": bool(parent_restart_waits),
            "block_advance_waiter_prepared": True,
            "block_advance_waiter_invoked": bool(block_advance_waits),
            "block_advance_waiter_invoked_count": len(block_advance_waits),
            "block_advance_waiter_expected_count": expected_block_advance_wait_count,
            "block_advance_waiter_passed_count": sum(1 for wait in block_advance_waits if wait.get("status") == "pass"),
            "block_advance_waiter_all_passed": (
                len(block_advance_waits) == expected_block_advance_wait_count
                and all(wait.get("status") == "pass" for wait in block_advance_waits)
                if parent_restart_receipts or block_advance_waits
                else None
            ),
            "block_advance_waiter_script": BLOCK_ADVANCE_WAITER_SCRIPT,
            "direct_chain_height_polling_removed": True,
            "chain_touched": False,
        },
    }


def _write_evidence(runtime_state_root: str | Path, result: Mapping[str, Any]) -> Path:
    root = Path(runtime_state_root) / "mother" / "evidence" / EVIDENCE_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_stamp()}-{result.get('network', 'unknown')}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="YAGNI helper cleanup2: import topology, patch helper mimics, run one host-local cleanup2 service per controller."
    )
    parser.add_argument("mode", choices=("inspect", "execute"))
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--topology-evidence")
    parser.add_argument("--acknowledge-topology-evidence-sha256")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    parser.add_argument("--max-wait-seconds", type=float, default=60.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--write-evidence", action="store_true")
    parser.add_argument("--quiet-progress", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    progress = _stderr_progress(args.quiet_progress)
    try:
        private_state = _load_private_state(args.runtime_state_root, network=args.network, mode=args.mode)
        result = run_helper_cleanup2_yagni(
            private_state,
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            topology_evidence=args.topology_evidence,
            acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
            mode=args.mode,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            progress=progress,
        )
        if args.write_evidence:
            result = dict(result)
            result["evidence_path"] = str(_write_evidence(args.runtime_state_root, result))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") == "pass" else 2
    except (MotherHelperCleanup2YagniError, MotherDeploymentCompletedHelperCleanupError) as exc:
        print(
            json.dumps(
                {
                    "kind": KIND,
                    "observed_at": _utc_now(),
                    "status": "failed",
                    "error_code": getattr(exc, "code", type(exc).__name__),
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
