#!/usr/bin/env python3
"""Prepare Besu static peer files before helper cleanup/restarts.

This script intentionally does not edit Coolify Compose, deploy, or restart any
service.  Its only mutating operation, when explicitly requested, is to launch
temporary Coolify writer services that write or delete
``/var/lib/besu/static-nodes.json`` on each survivor's actual Docker host.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import CoolifyController, CoolifyObservationError, resolve_coolify_controller
from tools.mother.common.deployment_coolify_context import load_controller_config
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.ethereum_identity import private_key_to_node_id
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state


KIND = "main_computer.mother.bootnode_precleanup.v1"
EVIDENCE_SUBDIR = "mother-bootnode-precleanup"
CURRENT_TOPOLOGY_EVIDENCE_SUBDIRS = (
    "deployment-node-add-post-admission-observe",
    "deployment-node-remove-finalize",
    "deployment-node-add-single-node-chain-and-hub-proof",
    "deployment-live-current-topology",
    "deployment-live-topology-empty-rectification",
)
STATIC_NODES_PATH = "/var/lib/besu/static-nodes.json"
CURL_IMAGE = "curlimages/curl:8.10.1"
STATIC_NODE_WRITER_PREFIX = "mother-static-node-writer"
WRITER_IMAGE = "docker:27-cli"
WRITER_HEALTH_PATH = "/proof/healthy"
DEFAULT_MAX_RESPONSE_BYTES = 12 * 1024 * 1024
DEFAULT_MAX_WAIT_SECONDS = 120.0
DEFAULT_POLL_INTERVAL_SECONDS = 5.0

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ENODE_RE = re.compile(r"^enode://(?P<id>[0-9a-fA-F]{128})@(?P<endpoint>[^/?#]+)$")
LOOPBACK_OR_WILDCARD_HOSTS = frozenset({"127.0.0.1", "localhost", "0.0.0.0", "::", "::1"})


class MotherBootnodePrecleanupError(RuntimeError):
    """Bootnode precleanup could not produce or apply a trustworthy plan."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


DockerRunner = Callable[..., subprocess.CompletedProcess[str]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


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
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_INVALID_ARGUMENT",
            f"{name} must be a simple identifier",
        )
    return text


def _uuid(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text or not UUID_RE.fullmatch(text):
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_INVALID_ARGUMENT",
            f"{name} must be a UUID-like identifier",
        )
    return text


def _sha256(value: object, name: str) -> str:
    text = str(value or "").strip().lower()
    if not SHA256_RE.fullmatch(text):
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_INVALID_ARGUMENT",
            f"{name} must be a SHA-256 hex digest",
        )
    return text


def _positive_int(value: object, name: str) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_INVALID_ARGUMENT",
            f"{name} must be an integer",
        ) from exc
    if number <= 0:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_INVALID_ARGUMENT",
            f"{name} must be positive",
        )
    return number



def _positive_float(value: object, name: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_INVALID_ARGUMENT",
            f"{name} must be a number",
        ) from exc
    if number <= 0:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_INVALID_ARGUMENT",
            f"{name} must be positive",
        )
    return number


def _nonnegative_float(value: object, name: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_INVALID_ARGUMENT",
            f"{name} must be a number",
        ) from exc
    if number < 0:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_INVALID_ARGUMENT",
            f"{name} must be non-negative",
        )
    return number


def _safe_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    if (
        not endpoint.startswith("/api/v1/")
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or "\\" in endpoint
        or "\x00" in endpoint
        or any(part in {"..", "."} for part in PurePosixPath(parsed.path).parts)
    ):
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_UNSAFE_ENDPOINT",
            "Coolify endpoint is unsafe",
        )
    return endpoint


def _open_url(opener: Any, request: urllib.request.Request, timeout: float):
    if hasattr(opener, "open"):
        return opener.open(request, timeout=timeout)
    if callable(opener):
        return opener(request, timeout=timeout)
    raise TypeError("opener must be callable or provide open(request, timeout=...)")


def _coolify_http(
    controller: CoolifyController,
    method: str,
    endpoint: str,
    *,
    body: Mapping[str, Any] | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    safe_endpoint = _safe_endpoint(endpoint)
    payload = canonical_json(dict(body)) if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {controller.api_token}",
        "User-Agent": "main-computer-mother-bootnode-precleanup/1",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        controller.base_url + safe_endpoint,
        data=payload,
        headers=headers,
        method=method.upper(),
    )
    started = time.monotonic()
    try:
        try:
            response = _open_url(opener, request, float(timeout))
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_COOLIFY_REQUEST_FAILED",
            "Coolify request failed",
        ) from exc
    if len(raw) > max_response_bytes:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_COOLIFY_RESPONSE_TOO_LARGE",
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
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _operation(network: str, mode: str) -> OperationIdentity:
    network_id = _identifier(network, "network")
    mode_id = _identifier(mode, "mode")
    operation_id = f"mother-bootnode-precleanup-{mode_id}-{network_id}-{_stamp()}"
    return OperationIdentity(
        operation_id=operation_id,
        request_id=f"{operation_id}-request",
        network=network_id,
        operation_kind="MOTHER-OP-RESTORE-SERVICE",
    )


def _load_private_state(runtime_state_root: str | Path, *, network: str, mode: str) -> PrivateStateReadResult:
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root)).resolve_private_state_paths()
    try:
        return read_private_state(paths, operation=_operation(network, mode))
    except MotherBootnodePrecleanupError:
        raise
    except Exception as exc:  # noqa: BLE001 - private-state readers use shared Mother errors.
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_PRIVATE_STATE_UNAVAILABLE",
            "failed to read Mother private state for Coolify writer service placement",
        ) from exc


def _controller(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
) -> CoolifyController:
    try:
        return resolve_coolify_controller(
            private_state,
            _identifier(network, "network"),
            _identifier(controller_id, "controller_id"),
            require_enabled=True,
            require_token=True,
        )
    except CoolifyObservationError as exc:
        raise MotherBootnodePrecleanupError(exc.code, str(exc)) from exc


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
        error_factory=MotherBootnodePrecleanupError,
        rejected_code="MOTHER_BOOTNODE_PRECLEANUP_CONTROLLER_REJECTED",
        invalid_code="MOTHER_BOOTNODE_PRECLEANUP_PRIVATE_STATE_INVALID",
        placement_description="bootnode precleanup writer service placement",
    )


def _application_uuid(payload: Any) -> str:
    found: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, value in item.items():
                if str(key) in {"uuid", "service_uuid", "application_uuid"} and type(value) is str:
                    clean = value.strip()
                    if UUID_RE.fullmatch(clean):
                        found.add(clean)
                elif isinstance(value, (Mapping, list)):
                    walk(value)
        elif type(item) is list:
            for value in item:
                walk(value)

    walk(payload)
    if len(found) != 1:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_WRITER_SERVICE_UUID_MISSING",
            "Coolify writer service response did not contain exactly one service uuid",
        )
    return next(iter(found))


def _environment_uuid(payload: Any, expected_name: str) -> str:
    expected = _identifier(expected_name, "environment_name")
    matches: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            name = item.get("name")
            uuid = item.get("uuid")
            if name == expected and type(uuid) is str and UUID_RE.fullmatch(uuid):
                matches.append(uuid)
            for value in item.values():
                if isinstance(value, (Mapping, list)):
                    walk(value)
        elif type(item) is list:
            for value in item:
                walk(value)

    walk(payload)
    if len(set(matches)) != 1:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_ENVIRONMENT_INVALID",
            f"expected exactly one Coolify environment named {expected}",
        )
    return next(iter(set(matches)))


def _resolve_environment_uuid(
    *,
    controller: CoolifyController,
    controller_id: str,
    project_uuid: str,
    expected_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
) -> str:
    endpoint = f"/api/v1/projects/{urllib.parse.quote(_uuid(project_uuid, 'project_uuid'), safe='')}/environments"
    response = _coolify_http(
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
            "phase": "bootnode-precleanup-writer-environment-resolution",
        }
    )
    if response.get("ok") is not True:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_ENVIRONMENT_INVALID",
            f"{controller_id} environment inventory failed with HTTP {response.get('status')}",
        )
    return _environment_uuid(response.get("payload"), expected_name)


def _healthy_status(value: object) -> bool:
    return type(value) is str and value.startswith("running:healthy")


def _service_detail_status(payload: Any, service_uuid: str, service_name: str) -> str:
    target_uuid = _uuid(service_uuid, "temporary writer service_uuid")
    target_name = _identifier(service_name, "temporary writer service_name")
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


def _wait_for_writer_service_health(
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
    sleeper: Callable[[float], None],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    started = time.monotonic()
    first_status = ""
    last_status = ""
    observed_statuses: list[str] = []
    observation_count = 0
    endpoint = f"/api/v1/services/{urllib.parse.quote(_uuid(service_uuid, 'temporary writer service_uuid'), safe='')}"
    while True:
        response = _coolify_http(
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
                "phase": "bootnode-precleanup-writer-health-poll",
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
        sleep_for = min(poll_interval_seconds, max(0.0, max_wait_seconds - elapsed))
        if sleep_for <= 0:
            break
        sleeper(sleep_for)
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


def _single_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_EVIDENCE_MISSING",
            f"{label} does not exist: {path}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_EVIDENCE_INVALID",
            f"{label} is not valid JSON: {path}",
        ) from exc
    if not isinstance(value, Mapping):
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_EVIDENCE_INVALID",
            f"{label} must be a JSON object: {path}",
        )
    return value


def _resolve_mother_path(paths: MotherPaths, value: str | Path) -> Path:
    try:
        return paths.validate_contained(value)
    except ValueError as exc:
        raise MotherBootnodePrecleanupError("MOTHER_BOOTNODE_PRECLEANUP_PATH_INVALID", str(exc)) from exc


def _topology(document: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("final_topology", "current_topology", "post_add_topology", "post_removal_topology"):
        value = document.get(key)
        if isinstance(value, Mapping):
            return value
    return document


def _marks_current_topology(summary: Mapping[str, Any]) -> bool:
    return (
        summary.get("topology_current") is True
        or summary.get("current_topology_marked_by_evidence") is True
    )


def _latest_sort_key(path: Path, document: Mapping[str, Any]) -> tuple[str, float, str]:
    stamp = document.get("completed_at") or document.get("observed_at") or ""
    return (str(stamp), path.stat().st_mtime if path.exists() else 0.0, path.name)


def _candidate_topology_evidence(paths: MotherPaths, *, network: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for subdir in CURRENT_TOPOLOGY_EVIDENCE_SUBDIRS:
        root = paths.evidence_root / subdir
        if not root.is_dir():
            continue
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
                candidates.append(
                    {
                        "path": path,
                        "document": document,
                        "source_subdir": subdir,
                        "sort_key": _latest_sort_key(path, document),
                    }
                )
            except MotherBootnodePrecleanupError:
                continue
    return sorted(candidates, key=lambda item: item["sort_key"])


def _discover_latest_topology_evidence(paths: MotherPaths, *, network: str) -> Path:
    candidates = _candidate_topology_evidence(paths, network=network)
    if not candidates:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_TOPOLOGY_NOT_FOUND",
            "no passed, clean, complete, current topology evidence was found across known topology evidence streams",
        )
    return Path(candidates[-1]["path"])


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
    raise MotherBootnodePrecleanupError(
        "MOTHER_BOOTNODE_PRECLEANUP_TOPOLOGY_INVALID",
        "topology evidence does not list current nodes",
    )


def _service_summary(node: str, record: Mapping[str, Any]) -> dict[str, Any]:
    controller_id = record.get("controller_id")
    service_uuid = record.get("service_uuid") or record.get("created_service_uuid")
    if not isinstance(controller_id, str) or not isinstance(service_uuid, str) or not service_uuid:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_TOPOLOGY_INVALID",
            f"topology service record for {node} lacks controller_id/service_uuid",
        )
    result: dict[str, Any] = {
        "node": _identifier(node, "topology node"),
        "controller_id": _identifier(controller_id, f"{node} controller_id"),
        "service_uuid": _uuid(service_uuid, f"{node} service_uuid"),
    }
    for key in ("validator_route", "p2p_route", "route"):
        value = record.get(key)
        if isinstance(value, Mapping):
            result[key] = dict(value)
    for key in (
        "container_name",
        "vpn_ip",
        "advertised_host",
        "host",
        "p2p_port",
        "p2p_endpoint",
        "endpoint",
        "enode",
        "node_id",
        "public_key",
        "id",
        "service_status",
        "last_observed_at",
    ):
        value = record.get(key)
        if value is not None:
            result[key] = value
    return result


def _service_records(document: Mapping[str, Any], topology: Mapping[str, Any], nodes: list[str]) -> list[dict[str, Any]]:
    by_node: dict[str, dict[str, Any]] = {}
    raw_services = topology.get("services")
    if isinstance(raw_services, Mapping):
        for node in nodes:
            record = raw_services.get(node)
            if isinstance(record, Mapping):
                by_node[node] = _service_summary(node, record)

    observations = document.get("service_observations")
    if isinstance(observations, list):
        for item in observations:
            if not isinstance(item, Mapping):
                continue
            node = item.get("node")
            if not isinstance(node, str) or node not in nodes:
                continue
            merged = dict(by_node.get(node) or {})
            try:
                merged.update(_service_summary(node, item))
            except MotherBootnodePrecleanupError:
                continue
            by_node[node] = merged

    missing = [node for node in nodes if node not in by_node]
    if missing:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_TOPOLOGY_INVALID",
            "topology evidence lacks service records for: " + ", ".join(missing),
        )
    return [by_node[node] for node in nodes]


def _load_topology(
    runtime_state_root: str | Path,
    *,
    network: str,
    topology_evidence: str | Path | None = None,
    acknowledged_sha256: str | None = None,
) -> dict[str, Any]:
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root))
    network_id = _identifier(network, "network")
    discovered = topology_evidence is None
    path = _discover_latest_topology_evidence(paths, network=network_id) if discovered else _resolve_mother_path(paths, topology_evidence)
    document = _load_json(path, label="topology evidence")
    actual_sha = _file_sha256(path)
    if acknowledged_sha256 is not None and actual_sha != _sha256(acknowledged_sha256, "topology evidence SHA-256"):
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_TOPOLOGY_ACK_MISMATCH",
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
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_TOPOLOGY_NOT_ACCEPTED",
            "bootnode precleanup requires passed, clean, complete, current topology evidence",
        )
    document_network = document.get("network")
    if isinstance(document_network, str) and document_network != network_id:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_TOPOLOGY_NETWORK_MISMATCH",
            "topology evidence network does not match requested network",
        )
    topology = _topology(document)
    nodes = _topology_nodes(document, topology)
    services = _service_records(document, topology, nodes)
    return {
        "path": str(path),
        "sha256": actual_sha,
        "discovered": discovered,
        "document": document,
        "topology": topology,
        "nodes": nodes,
        "services": services,
    }


def _host_from_endpoint(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith("enode://"):
        match = ENODE_RE.fullmatch(text)
        if not match:
            return None
        endpoint = match.group("endpoint")
    else:
        parsed = urlsplit("//" + text)
        endpoint = parsed.netloc or parsed.path
    if endpoint.startswith("["):
        end = endpoint.find("]")
        return endpoint[1:end] if end > 1 else None
    if ":" in endpoint:
        return endpoint.rsplit(":", 1)[0]
    return endpoint or None


def _port_from_endpoint(value: object) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith("enode://"):
        match = ENODE_RE.fullmatch(text)
        if not match:
            return None
        endpoint = match.group("endpoint")
    else:
        parsed = urlsplit("//" + text)
        endpoint = parsed.netloc or parsed.path
    if endpoint.startswith("["):
        remainder = endpoint[endpoint.find("]") + 1 :]
        if remainder.startswith(":") and remainder[1:].isdigit():
            return int(remainder[1:])
        return None
    if ":" not in endpoint:
        return None
    maybe_port = endpoint.rsplit(":", 1)[1]
    if not maybe_port.isdigit():
        return None
    port = int(maybe_port)
    return port if 0 < port < 65536 else None


def _is_forbidden_host(host: str) -> bool:
    return host.strip().lower() in LOOPBACK_OR_WILDCARD_HOSTS


def _route_mapping(record: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("validator_route", "p2p_route", "route"):
        value = record.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _route_host(record: Mapping[str, Any]) -> tuple[str | None, str]:
    route = _route_mapping(record)
    for source, container in (
        ("validator_route.advertised_host", route),
        ("validator_route.vpn_ip", route),
        ("validator_route.host", route),
        ("record.advertised_host", record),
        ("record.vpn_ip", record),
        ("record.host", record),
    ):
        value = container.get(source.split(".")[-1]) if isinstance(container, Mapping) else None
        if isinstance(value, str) and value.strip():
            host = value.strip()
            return (None, f"{source} is loopback/wildcard: {host}") if _is_forbidden_host(host) else (host, source)

    for source, container in (
        ("validator_route.p2p_endpoint", route),
        ("validator_route.endpoint", route),
        ("validator_route.enode", route),
        ("record.p2p_endpoint", record),
        ("record.endpoint", record),
        ("record.enode", record),
    ):
        value = container.get(source.split(".")[-1]) if isinstance(container, Mapping) else None
        host = _host_from_endpoint(value)
        if host:
            return (None, f"{source} is loopback/wildcard: {host}") if _is_forbidden_host(host) else (host, source)
    return None, "no advertised host/vpn_ip/endpoint found"


def _route_port(record: Mapping[str, Any]) -> tuple[int | None, str]:
    route = _route_mapping(record)
    for source, container in (
        ("validator_route.p2p_port", route),
        ("record.p2p_port", record),
    ):
        value = container.get("p2p_port") if isinstance(container, Mapping) else None
        try:
            port = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if 0 < port < 65536:
            return port, source
        return None, f"{source} is outside valid TCP/UDP port range: {value!r}"
    for source, container in (
        ("validator_route.p2p_endpoint", route),
        ("validator_route.endpoint", route),
        ("validator_route.enode", route),
        ("record.p2p_endpoint", record),
        ("record.endpoint", record),
        ("record.enode", record),
    ):
        value = container.get(source.split(".")[-1]) if isinstance(container, Mapping) else None
        port = _port_from_endpoint(value)
        if port is not None:
            return port, source
    return None, "no p2p_port/endpoint found"


def _node_id_from_enode(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = ENODE_RE.fullmatch(value.strip())
    if not match:
        return None
    return match.group("id").lower()


def _node_id_from_record(record: Mapping[str, Any]) -> tuple[str | None, str]:
    route = _route_mapping(record)
    for source, container in (
        ("record.node_id", record),
        ("record.public_key", record),
        ("record.id", record),
        ("validator_route.node_id", route),
        ("validator_route.public_key", route),
        ("validator_route.id", route),
    ):
        value = container.get(source.split(".")[-1]) if isinstance(container, Mapping) else None
        if isinstance(value, str):
            text = value.removeprefix("0x").strip().lower()
            if re.fullmatch(r"[0-9a-f]{128}", text):
                return text, source
    for source, container in (
        ("record.enode", record),
        ("validator_route.enode", route),
    ):
        value = container.get("enode") if isinstance(container, Mapping) else None
        node_id = _node_id_from_enode(value)
        if node_id:
            return node_id, source
    return None, "no node id/enode found"


def _container_name(record: Mapping[str, Any]) -> str:
    raw = record.get("container_name")
    if isinstance(raw, str) and raw.strip():
        return _identifier(raw.strip(), "container_name")
    node = _identifier(record.get("node"), "topology node")
    service_uuid = _uuid(record.get("service_uuid"), f"{node} service_uuid")
    return f"{node}-{service_uuid}"


def _parse_admin_node_info(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_NODE_INFO_INVALID",
            "admin_nodeInfo response was not valid JSON",
        ) from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("result"), Mapping):
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_NODE_INFO_INVALID",
            "admin_nodeInfo response lacks result object",
        )
    result = payload["result"]
    node_id = result.get("id")
    if not isinstance(node_id, str) or not re.fullmatch(r"[0-9a-fA-F]{128}", node_id):
        enode_id = _node_id_from_enode(result.get("enode"))
        if not enode_id:
            raise MotherBootnodePrecleanupError(
                "MOTHER_BOOTNODE_PRECLEANUP_NODE_INFO_INVALID",
                "admin_nodeInfo result lacks node id",
            )
        node_id = enode_id
    return {
        "node_id": node_id.lower(),
        "reported_enode": result.get("enode"),
        "reported_ip": result.get("ip"),
        "reported_listen_addr": result.get("listenAddr"),
    }


def _admin_node_info(
    container_name: str,
    *,
    runner: DockerRunner = subprocess.run,
    timeout: float = 15.0,
    curl_image: str = CURL_IMAGE,
) -> dict[str, Any]:
    payload = '{"jsonrpc":"2.0","method":"admin_nodeInfo","params":[],"id":1}'
    args = [
        "docker",
        "run",
        "--rm",
        "--network",
        f"container:{container_name}",
        curl_image,
        "-sS",
        "--max-time",
        "5",
        "http://127.0.0.1:8545",
        "-H",
        "Content-Type: application/json",
        "--data",
        payload,
    ]
    completed = runner(args, capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_NODE_INFO_FAILED",
            f"admin_nodeInfo failed for {container_name}: {str(completed.stderr or '').strip()}",
        )
    return _parse_admin_node_info(str(completed.stdout or ""))


def _static_nodes_json(static_nodes: Sequence[str]) -> bytes:
    return (json.dumps(list(static_nodes), indent=2, sort_keys=False) + "\n").encode("utf-8")



def _writer_service_name(node: str) -> str:
    safe_node = _identifier(node, "writer node").replace("_", "-")
    return f"{STATIC_NODE_WRITER_PREFIX}-{safe_node}-{_stamp().lower()}"


def _escape_docker_compose_interpolation(text: str) -> str:
    """Preserve shell variables when embedding a script in a Docker Compose value."""
    return text.replace("$", "$$")


def _writer_script(action: Mapping[str, Any]) -> str:
    node = _identifier(action.get("node"), "writer action node")
    container_name = _identifier(action.get("container_name"), "writer action container_name")
    operation = _identifier(action.get("action"), "writer action")
    if operation not in {"write-static-nodes", "delete-static-nodes"}:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_WRITER_ACTION_INVALID",
            f"unsupported static-node writer action: {operation}",
        )
    static_nodes = list(action.get("static_nodes") or [])
    if operation == "delete-static-nodes" and static_nodes:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_WRITER_ACTION_INVALID",
            "delete-static-nodes action must not carry static_nodes",
        )
    payload = _static_nodes_json(static_nodes)
    payload_b64 = base64.b64encode(payload).decode("ascii")
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    static_node_count = len(static_nodes)
    write_inner = (
        "set -eu; "
        "mkdir -p /var/lib/besu; "
        "tmp='/var/lib/besu/static-nodes.json.tmp.'$$; "
        "cat > \"$tmp\"; "
        "chmod 0644 \"$tmp\"; "
        "mv \"$tmp\" '/var/lib/besu/static-nodes.json'; "
        "test -f '/var/lib/besu/static-nodes.json'"
    )
    delete_inner = "set -eu; rm -f '/var/lib/besu/static-nodes.json'; test ! -e '/var/lib/besu/static-nodes.json'"
    return "\n".join(
        [
            "set -eu",
            "DIAG_PREFIX=MOTHER_BOOTNODE_PRECLEANUP_WRITER_DIAGNOSTIC",
            "diag() { printf '%s %s\\n' \"$DIAG_PREFIX\" \"$*\" >&2; }",
            "proof_dir='/proof'",
            "healthy=\"$proof_dir/healthy\"",
            "result=\"$proof_dir/result.json\"",
            "failure=\"$proof_dir/failed.json\"",
            "stdout_file=\"$proof_dir/docker-stdout.txt\"",
            "stderr_file=\"$proof_dir/docker-stderr.txt\"",
            "mkdir -p \"$proof_dir\"",
            "rm -f \"$healthy\" \"$result\" \"$failure\" \"$stdout_file\" \"$stderr_file\"",
            f"node={_single_quote(node)}",
            f"target_container={_single_quote(container_name)}",
            f"operation={_single_quote(operation)}",
            f"static_nodes_path={_single_quote(STATIC_NODES_PATH)}",
            f"payload_sha256={_single_quote(payload_sha256)}",
            f"static_node_count={static_node_count}",
            "diag \"phase=script_start node=$node target_container=$target_container operation=$operation path=$static_nodes_path static_node_count=$static_node_count\"",
            "write_failure() {",
            "  rc=\"$1\"",
            "  reason=\"$2\"",
            "  diag \"phase=script_failed node=$node target_container=$target_container operation=$operation reason=$reason exit_code=$rc\"",
            "  printf '{\"status\":\"failed\",\"node\":\"%s\",\"target_container\":\"%s\",\"action\":\"%s\",\"path\":\"%s\",\"reason\":\"%s\",\"exit_code\":%s}\\n' \"$node\" \"$target_container\" \"$operation\" \"$static_nodes_path\" \"$reason\" \"$rc\" > \"$failure\"",
            "  exit \"$rc\"",
            "}",
            "if ! docker inspect \"$target_container\" >/dev/null 2>\"$stderr_file\"; then",
            "  write_failure 20 target-container-not-found",
            "fi",
            "rc=0",
            "if [ \"$operation\" = 'write-static-nodes' ]; then",
            "  payload_file=\"$proof_dir/static-nodes.json\"",
            f"  cat > \"$proof_dir/static-nodes.json.b64\" <<'MOTHER_STATIC_NODES_JSON_B64'\n{payload_b64}\nMOTHER_STATIC_NODES_JSON_B64",
            "  if ! base64 -d \"$proof_dir/static-nodes.json.b64\" > \"$payload_file\" 2>\"$stderr_file\"; then",
            "    write_failure 21 payload-decode-failed",
            "  fi",
            "  set +e",
            "  docker exec -i \"$target_container\" sh -lc " + _single_quote(write_inner) + " < \"$payload_file\" >\"$stdout_file\" 2>\"$stderr_file\"",
            "  rc=\"$?\"",
            "  set -e",
            "  [ \"$rc\" = '0' ] || write_failure \"$rc\" write-static-nodes-failed",
            "elif [ \"$operation\" = 'delete-static-nodes' ]; then",
            "  set +e",
            "  docker exec \"$target_container\" sh -lc " + _single_quote(delete_inner) + " >\"$stdout_file\" 2>\"$stderr_file\"",
            "  rc=\"$?\"",
            "  set -e",
            "  [ \"$rc\" = '0' ] || write_failure \"$rc\" delete-static-nodes-failed",
            "else",
            "  write_failure 22 unsupported-operation",
            "fi",
            "printf '{\"status\":\"pass\",\"node\":\"%s\",\"target_container\":\"%s\",\"action\":\"%s\",\"path\":\"%s\",\"static_node_count\":%s,\"payload_sha256\":\"%s\"}\\n' \"$node\" \"$target_container\" \"$operation\" \"$static_nodes_path\" \"$static_node_count\" \"$payload_sha256\" > \"$result\"",
            "touch \"$healthy\"",
            "diag \"phase=script_complete node=$node target_container=$target_container operation=$operation path=$static_nodes_path\"",
            "while true; do sleep 3600; done",
            "",
        ]
    )


def _writer_service_compose(service_name: str, action: Mapping[str, Any]) -> str:
    name = _identifier(service_name, "writer service name")
    script = _escape_docker_compose_interpolation(_writer_script(action))
    compose = {
        "services": {
            name: {
                "image": WRITER_IMAGE,
                "command": ["sh", "-lc", script],
                "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
                "restart": "no",
                "labels": {
                    "main_computer.mother.component": "bootnode-precleanup-static-node-writer",
                    "main_computer.mother.cleanup_scope": "bootnode-precleanup",
                    "main_computer.mother.not_a_validator": "true",
                    "main_computer.mother.not_a_chain_service": "true",
                },
                "healthcheck": {
                    "test": ["CMD-SHELL", f"test -f {WRITER_HEALTH_PATH}"],
                    "interval": "5s",
                    "timeout": "2s",
                    "retries": 3,
                    "start_period": "1s",
                },
            }
        }
    }
    return yaml.safe_dump(compose, sort_keys=False)


def _temporary_service_body(controller_config: Mapping[str, Any], *, network: str, name: str, compose: str) -> dict[str, Any]:
    return {
        "project_uuid": controller_config["project_uuid"],
        "server_uuid": controller_config["server_uuid"],
        "environment_name": _identifier(network, "network"),
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "name": _identifier(name, "temporary writer service name"),
        "description": "Ephemeral Mother bootnode precleanup static-nodes writer",
        "instant_deploy": False,
    }


def _delete_writer_service(
    *,
    controller: CoolifyController,
    controller_id: str,
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    endpoint = f"/api/v1/services/{urllib.parse.quote(_uuid(service_uuid, 'writer service_uuid'), safe='')}"
    response = _coolify_http(
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
        "controller_id": controller_id,
        "service_uuid": service_uuid,
        "service_name": service_name,
        "cleanup_scope": "bootnode-precleanup-writer-service",
    }


def _run_writer_service(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    action: Mapping[str, Any],
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    sleeper: Callable[[float], None],
    preserve_services: bool,
) -> dict[str, Any]:
    node = _identifier(action.get("node"), "writer action node")
    controller_id = _identifier(action.get("controller_id"), f"{node} controller_id")
    container_name = _identifier(action.get("container_name"), f"{node} container_name")
    controller = _controller(private_state, network=network, controller_id=controller_id)
    controller_config = _controller_config(private_state, network=network, controller_id=controller_id)
    service_name = _writer_service_name(node)
    observations: list[dict[str, Any]] = []
    writer_service_uuid: str | None = None
    compose = _writer_service_compose(service_name, action)
    script = _writer_script(action)

    environment_uuid = _resolve_environment_uuid(
        controller=controller,
        controller_id=controller_id,
        project_uuid=str(controller_config["project_uuid"]),
        expected_name=network,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        observations=observations,
    )
    body = _temporary_service_body(controller_config, network=network, name=service_name, compose=compose)
    body["environment_uuid"] = environment_uuid

    create_response = _coolify_http(
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
        "controller_id": controller_id,
        "service_name": service_name,
        "cleanup_scope": "bootnode-precleanup-writer-service",
        "target_node": node,
        "target_container": container_name,
        "request_body_sha256": hashlib.sha256(canonical_json(body)).hexdigest(),
        "compose_sha256": hashlib.sha256(compose.encode("utf-8")).hexdigest(),
        "writer_script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
    }
    observations.append(
        {key: create_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")}
    )
    if not create_response["ok"]:
        return {
            "node": node,
            "controller_id": controller_id,
            "container_name": container_name,
            "action": action.get("action"),
            "path": action.get("path"),
            "status": "failed",
            "reason": "writer-service-create-failed",
            "create": create_receipt,
            "start": None,
            "health": None,
            "delete": None,
            "writer_service_preserved": False,
            "observations": observations,
        }

    writer_service_uuid = _application_uuid(create_response.get("payload"))
    create_receipt["service_uuid"] = writer_service_uuid
    start_endpoint = f"/api/v1/services/{urllib.parse.quote(writer_service_uuid, safe='')}/start"
    start_response = _coolify_http(
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
        "controller_id": controller_id,
        "service_uuid": writer_service_uuid,
        "service_name": service_name,
        "cleanup_scope": "bootnode-precleanup-writer-service",
        "target_node": node,
        "target_container": container_name,
    }
    observations.append(
        {key: start_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")}
    )
    if not start_response["ok"]:
        delete_receipt = None
        if not preserve_services:
            delete_receipt = _delete_writer_service(
                controller=controller,
                controller_id=controller_id,
                service_uuid=writer_service_uuid,
                service_name=service_name,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            observations.append(
                {key: delete_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")}
            )
        return {
            "node": node,
            "controller_id": controller_id,
            "container_name": container_name,
            "action": action.get("action"),
            "path": action.get("path"),
            "status": "failed",
            "reason": "writer-service-start-failed",
            "writer_service_uuid": writer_service_uuid,
            "writer_service_name": service_name,
            "create": create_receipt,
            "start": start_receipt,
            "health": None,
            "delete": delete_receipt,
            "writer_service_preserved": bool(preserve_services),
            "observations": observations,
        }

    health = _wait_for_writer_service_health(
        controller=controller,
        controller_id=controller_id,
        service_uuid=writer_service_uuid,
        service_name=service_name,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        max_wait_seconds=max_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        opener=opener,
        sleeper=sleeper,
        observations=observations,
    )

    delete_receipt = None
    if health.get("healthy") is True and not preserve_services:
        delete_receipt = _delete_writer_service(
            controller=controller,
            controller_id=controller_id,
            service_uuid=writer_service_uuid,
            service_name=service_name,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        observations.append(
            {key: delete_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")}
        )

    healthy = health.get("healthy") is True
    delete_ok = preserve_services or (delete_receipt is not None and delete_receipt.get("ok") is True)
    return {
        "node": node,
        "controller_id": controller_id,
        "container_name": container_name,
        "action": action.get("action"),
        "path": action.get("path"),
        "static_node_count": action.get("static_node_count"),
        "static_nodes_sha256": action.get("static_nodes_sha256"),
        "status": "pass" if healthy and delete_ok else "failed",
        "reason": None if healthy and delete_ok else ("writer-service-not-healthy" if not healthy else "writer-service-delete-failed"),
        "writer_service_created": True,
        "writer_service_uuid": writer_service_uuid,
        "writer_service_name": service_name,
        "writer_service_final_status": health.get("final_status"),
        "writer_service_healthy": healthy,
        "writer_service_deleted": (delete_receipt is not None and delete_receipt.get("ok") is True),
        "writer_service_preserved": bool(preserve_services),
        "create": create_receipt,
        "start": start_receipt,
        "health": health,
        "delete": delete_receipt,
        "observations": observations,
        "docker_touched_by_writer_service": start_receipt.get("ok") is True,
        "local_docker_mutation_performed": False,
        "parent_redeploy_performed": False,
        "parent_restart_performed": False,
    }



def _seed_record(
    service: Mapping[str, Any],
    *,
    node_info: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    node = _identifier(service.get("node"), "topology node")
    host, host_source = _route_host(service)
    port, port_source = _route_port(service)
    node_id, node_id_source = _node_id_from_record(service)
    node_info_used = False
    if node_id is None and isinstance(node_info, Mapping):
        value = node_info.get("node_id")
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{128}", value):
            node_id = value.lower()
            raw_source = node_info.get("node_id_source")
            node_id_source = raw_source if isinstance(raw_source, str) and raw_source.strip() else "admin_nodeInfo.id"
            node_info_used = True

    rejection_reasons: list[str] = []
    if host is None:
        rejection_reasons.append(host_source)
    if port is None:
        rejection_reasons.append(port_source)
    if node_id is None:
        rejection_reasons.append(node_id_source)

    container = _container_name(service)
    base = {
        "node": node,
        "controller_id": service.get("controller_id"),
        "service_uuid": service.get("service_uuid"),
        "container_name": container,
        "host": host,
        "host_source": host_source,
        "p2p_port": port,
        "p2p_port_source": port_source,
        "node_id": node_id,
        "node_id_source": node_id_source,
        "node_info_used": node_info_used,
    }
    if rejection_reasons:
        return {**base, "eligible": False, "rejection_reasons": rejection_reasons}
    enode = f"enode://{node_id}@{host}:{port}"
    return {**base, "eligible": True, "enode": enode, "enode_sha256": hashlib.sha256(enode.encode("utf-8")).hexdigest()}


def build_bootnode_precleanup_plan(
    services: Sequence[Mapping[str, Any]],
    *,
    exclude_nodes: Sequence[str] = (),
    max_static_nodes: int = 5,
    node_info_by_node: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    max_static_nodes = _positive_int(max_static_nodes, "max_static_nodes")
    excluded = {_identifier(node, "excluded node") for node in exclude_nodes}
    service_by_node = {_identifier(service.get("node"), "topology node"): dict(service) for service in services}
    survivors = [node for node in service_by_node if node not in excluded]
    node_info_by_node = node_info_by_node or {}

    seed_records = [
        _seed_record(service_by_node[node], node_info=node_info_by_node.get(node))
        for node in service_by_node
        if node not in excluded
    ]
    eligible_by_node = {record["node"]: record for record in seed_records if record.get("eligible") is True}
    actions: list[dict[str, Any]] = []
    failed_reasons: list[str] = []

    for node in survivors:
        service = service_by_node[node]
        container = _container_name(service)
        peer_records = [
            eligible_by_node[peer_node]
            for peer_node in survivors
            if peer_node != node and peer_node in eligible_by_node
        ][:max_static_nodes]
        static_nodes = [str(record["enode"]) for record in peer_records]
        if static_nodes:
            action = {
                "node": node,
                "container_name": container,
                "controller_id": service.get("controller_id"),
                "service_uuid": service.get("service_uuid"),
                "action": "write-static-nodes",
                "path": STATIC_NODES_PATH,
                "survivor_count": len(survivors),
                "static_nodes": static_nodes,
                "static_node_count": len(static_nodes),
                "static_nodes_sha256": hashlib.sha256(_static_nodes_json(static_nodes)).hexdigest(),
                "peer_nodes": [str(record["node"]) for record in peer_records],
            }
        else:
            action = {
                "node": node,
                "container_name": container,
                "controller_id": service.get("controller_id"),
                "service_uuid": service.get("service_uuid"),
                "action": "delete-static-nodes",
                "path": STATIC_NODES_PATH,
                "survivor_count": len(survivors),
                "static_nodes": [],
                "static_node_count": 0,
                "reason": "sole-survivor-has-no-valid-non-self-static-peers" if len(survivors) == 1 else "no-eligible-non-self-survivor-peer-seeds",
            }
            if len(survivors) > 1:
                failed_reasons.append(f"{node} has no eligible non-self survivor peer seed")
        actions.append(action)

    status = "pass" if not failed_reasons else "failed"
    return {
        "status": status,
        "clean": status == "pass",
        "complete": True,
        "excluded_nodes": sorted(excluded),
        "survivor_nodes": survivors,
        "survivor_count": len(survivors),
        "max_static_nodes": max_static_nodes,
        "eligible_seed_nodes": [str(record["node"]) for record in seed_records if record.get("eligible") is True],
        "rejected_seed_nodes": [record for record in seed_records if record.get("eligible") is not True],
        "actions": actions,
        "failure_reasons": failed_reasons,
    }


def _private_state_document(private_state: PrivateStateReadResult) -> Mapping[str, Any]:
    raw = getattr(private_state, "canonical_object_bytes", None)
    if isinstance(raw, bytes):
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MotherBootnodePrecleanupError(
                "MOTHER_BOOTNODE_PRECLEANUP_PRIVATE_STATE_MALFORMED",
                "Mother private state canonical object is not readable JSON",
            ) from exc
    else:
        document_bytes = getattr(private_state, "document_bytes", None)
        if not isinstance(document_bytes, bytes):
            raise MotherBootnodePrecleanupError(
                "MOTHER_BOOTNODE_PRECLEANUP_PRIVATE_STATE_MALFORMED",
                "Mother private state does not expose canonical_object_bytes or document_bytes",
            )
        try:
            document = yaml.safe_load(document_bytes)
        except yaml.YAMLError as exc:
            raise MotherBootnodePrecleanupError(
                "MOTHER_BOOTNODE_PRECLEANUP_PRIVATE_STATE_MALFORMED",
                "Mother private state document is not readable YAML",
            ) from exc
    if not isinstance(document, Mapping):
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_PRIVATE_STATE_MALFORMED",
            "Mother private state document is not a mapping",
        )
    return document


def _private_state_validator_identity(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    node: str,
) -> dict[str, str]:
    try:
        document = _private_state_document(private_state)
        networks = document.get("networks")
        if not isinstance(networks, Mapping):
            raise KeyError("networks")
        network_state = networks.get(network)
        if not isinstance(network_state, Mapping):
            raise KeyError(f"networks.{network}")
        nodes = network_state.get("nodes")
        if not isinstance(nodes, Mapping):
            raise KeyError(f"networks.{network}.nodes")
        node_wire = nodes.get(node)
        if not isinstance(node_wire, Mapping):
            raise KeyError(f"networks.{network}.nodes.{node}")
        validator_ref = node_wire.get("validator_ref")
        if not isinstance(validator_ref, str) or not validator_ref:
            raise ValueError(f"networks.{network}.nodes.{node}.validator_ref is missing")
        parts = validator_ref.split(".")
        if (
            len(parts) not in {4, 5}
            or parts[0] != "networks"
            or parts[2] != "validators"
            or parts[1] != network
            or (len(parts) == 5 and parts[4] != "private_key")
        ):
            raise ValueError(f"networks.{network}.nodes.{node}.validator_ref has unsupported shape")
        validator_id = _identifier(parts[3], "validator_id")
        validators = network_state.get("validators")
        if not isinstance(validators, Mapping):
            raise KeyError(f"networks.{network}.validators")
        validator = validators.get(validator_id)
        if not isinstance(validator, Mapping):
            raise KeyError(f"networks.{network}.validators.{validator_id}")
        private_key = validator.get("private_key")
        if not isinstance(private_key, str):
            raise ValueError(f"networks.{network}.validators.{validator_id}.private_key is missing")
        normalized_private_key = "0x" + private_key.removeprefix("0x").lower()
        node_id = private_key_to_node_id(normalized_private_key).lower()
        if not re.fullmatch(r"[0-9a-f]{128}", node_id):
            raise ValueError(f"networks.{network}.validators.{validator_id}.private_key did not derive a valid node id")
        address = validator.get("address")
        return {
            "node_id": node_id,
            "validator_ref": validator_ref,
            "validator_address": str(address) if isinstance(address, str) else "",
        }
    except MotherBootnodePrecleanupError:
        raise
    except KeyError as exc:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_PRIVATE_STATE_NODE_ID_UNAVAILABLE",
            f"Mother private state has no validator identity for {node}",
        ) from exc
    except (TypeError, ValueError) as exc:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_PRIVATE_STATE_NODE_ID_MALFORMED",
            f"Mother private-state validator identity for {node} is malformed",
        ) from exc


def _collect_private_state_node_info(
    private_state: PrivateStateReadResult,
    services: Sequence[Mapping[str, Any]],
    *,
    network: str,
    exclude_nodes: Sequence[str],
    mode: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    excluded = {_identifier(node, "excluded node") for node in exclude_nodes}
    infos: dict[str, dict[str, Any]] = {}
    observations: list[dict[str, Any]] = []
    for service in services:
        node = _identifier(service.get("node"), "topology node")
        if node in excluded:
            continue
        try:
            identity = _private_state_validator_identity(private_state, network=network, node=node)
            infos[node] = {
                "node_id": identity["node_id"],
                "node_id_source": "mother_private_state.validator_private_key",
                "validator_ref": identity["validator_ref"],
                "validator_address": identity["validator_address"],
            }
            observations.append({
                "node": node,
                "ok": True,
                "node_id_source": "mother_private_state.validator_private_key",
                "validator_ref": identity["validator_ref"],
                "validator_address": identity["validator_address"],
            })
        except MotherBootnodePrecleanupError as exc:
            observations.append({
                "node": node,
                "ok": False,
                "code": exc.code,
                "reason": str(exc),
            })
        except Exception as exc:  # noqa: BLE001 - private-state identity resolution should not leak secrets.
            observations.append({
                "node": node,
                "ok": False,
                "code": "MOTHER_BOOTNODE_PRECLEANUP_PRIVATE_STATE_NODE_ID_FAILED",
                "reason": f"private-state node id resolution failed for {node}: {exc}",
            })
    return infos, observations



def _collect_live_node_info(
    services: Sequence[Mapping[str, Any]],
    *,
    exclude_nodes: Sequence[str],
    skip_nodes: Sequence[str] = (),
    runner: DockerRunner = subprocess.run,
    timeout: float = 15.0,
    curl_image: str = CURL_IMAGE,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    excluded = {_identifier(node, "excluded node") for node in exclude_nodes}
    skipped = {_identifier(node, "skip node") for node in skip_nodes}
    infos: dict[str, dict[str, Any]] = {}
    observations: list[dict[str, Any]] = []
    for service in services:
        node = _identifier(service.get("node"), "topology node")
        if node in excluded:
            continue
        container = _container_name(service)
        if node in skipped:
            observations.append({
                "node": node,
                "container_name": container,
                "ok": True,
                "skipped": True,
                "reason": "node id already supplied by Mother private state",
            })
            continue
        try:
            info = _admin_node_info(container, runner=runner, timeout=timeout, curl_image=curl_image)
            infos[node] = {**info, "node_id_source": "admin_nodeInfo.id"}
            observations.append({
                "node": node,
                "container_name": container,
                "ok": True,
                "node_id_source": "admin_nodeInfo.id",
                "reported_enode": info.get("reported_enode"),
                "reported_ip": info.get("reported_ip"),
                "reported_listen_addr": info.get("reported_listen_addr"),
            })
        except MotherBootnodePrecleanupError as exc:
            observations.append({
                "node": node,
                "container_name": container,
                "ok": False,
                "code": exc.code,
                "reason": str(exc),
            })
    return infos, observations


def _write_evidence(paths: MotherPaths, evidence: Mapping[str, Any], *, network: str) -> tuple[str, str]:
    root = paths.evidence_root / EVIDENCE_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_stamp()}-{network}-bootnode-precleanup.json"
    payload = _pretty_json(evidence).encode("utf-8")
    path.write_bytes(payload)
    return str(path), hashlib.sha256(payload).hexdigest()


def run_bootnode_precleanup(
    *,
    network: str = "mainnet",
    runtime_state_root: str | Path = Path("runtime/state"),
    topology_evidence: str | Path | None = None,
    acknowledged_topology_evidence_sha256: str | None = None,
    exclude_nodes: Sequence[str] = (),
    max_static_nodes: int = 5,
    execute: bool = False,
    allow_mutation: bool = False,
    preserve_services: bool = False,
    probe_node_info: bool = True,
    write_evidence: bool = True,
    runner: DockerRunner = subprocess.run,
    timeout: float = 15.0,
    curl_image: str = CURL_IMAGE,
    opener: Any = urllib.request.urlopen,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    network_id = _identifier(network, "network")
    max_static_nodes = _positive_int(max_static_nodes, "max_static_nodes")
    request_timeout = _positive_float(timeout, "timeout")
    response_limit = _positive_int(max_response_bytes, "max_response_bytes")
    wait_limit = _nonnegative_float(max_wait_seconds, "max_wait_seconds")
    poll_interval = _nonnegative_float(poll_interval_seconds, "poll_interval_seconds")
    if execute and not allow_mutation:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_MUTATION_NOT_ALLOWED",
            "--execute requires --allow-mutation",
        )
    if allow_mutation and not execute:
        raise MotherBootnodePrecleanupError(
            "MOTHER_BOOTNODE_PRECLEANUP_MUTATION_MODE_INVALID",
            "--allow-mutation requires --execute",
        )

    paths = MotherPaths(runtime_state_root=Path(runtime_state_root))
    topology_state = _load_topology(
        runtime_state_root,
        network=network_id,
        topology_evidence=topology_evidence,
        acknowledged_sha256=acknowledged_topology_evidence_sha256,
    )
    services = list(topology_state["services"])
    private_state: PrivateStateReadResult | None = None
    private_state_node_info: dict[str, dict[str, Any]] = {}
    private_state_node_info_observations: list[dict[str, Any]] = []
    private_state_read_observation: dict[str, Any] | None = None
    private_state_mode = "execute" if execute else "inspect"
    try:
        private_state = _load_private_state(runtime_state_root, network=network_id, mode=private_state_mode)
        private_state_node_info, private_state_node_info_observations = _collect_private_state_node_info(
            private_state,
            services,
            network=network_id,
            exclude_nodes=exclude_nodes,
            mode=private_state_mode,
        )
        private_state_read_observation = {
            "ok": True,
            "node_info_count": len(private_state_node_info),
        }
    except MotherBootnodePrecleanupError as exc:
        private_state_read_observation = {
            "ok": False,
            "code": exc.code,
            "reason": str(exc),
        }

    live_node_info: dict[str, dict[str, Any]] = {}
    live_node_info_observations: list[dict[str, Any]] = []
    if probe_node_info:
        live_node_info, live_node_info_observations = _collect_live_node_info(
            services,
            exclude_nodes=exclude_nodes,
            skip_nodes=private_state_node_info.keys(),
            runner=runner,
            timeout=timeout,
            curl_image=curl_image,
        )

    node_info_by_node = {**live_node_info, **private_state_node_info}
    plan = build_bootnode_precleanup_plan(
        services,
        exclude_nodes=exclude_nodes,
        max_static_nodes=max_static_nodes,
        node_info_by_node=node_info_by_node,
    )
    mutation_results: list[dict[str, Any]] = []
    unsafe_plan_error: dict[str, Any] | None = None
    if execute:
        if plan["status"] != "pass":
            unsafe_plan_error = {
                "code": "MOTHER_BOOTNODE_PRECLEANUP_PLAN_UNSAFE",
                "error": "refusing to mutate because the bootnode precleanup plan is unsafe",
                "unsafe_plan_summary": {
                    "survivor_count": plan.get("survivor_count"),
                    "survivor_nodes": plan.get("survivor_nodes", []),
                    "eligible_seed_count": len(plan.get("eligible_seed_nodes", [])),
                    "rejected_seed_count": len(plan.get("rejected_seed_nodes", [])),
                    "failure_reasons": plan.get("failure_reasons", []),
                    "actions": [
                        {
                            "node": action.get("node"),
                            "action": action.get("action"),
                            "reason": action.get("reason"),
                            "static_node_count": action.get("static_node_count"),
                        }
                        for action in plan.get("actions", [])
                    ],
                    "rejected_seed_nodes": [
                        {
                            "node": rejected.get("node"),
                            "controller_id": rejected.get("controller_id"),
                            "service_uuid": rejected.get("service_uuid"),
                            "container_name": rejected.get("container_name"),
                            "host": rejected.get("host"),
                            "host_source": rejected.get("host_source"),
                            "p2p_port": rejected.get("p2p_port"),
                            "p2p_port_source": rejected.get("p2p_port_source"),
                            "node_id_source": rejected.get("node_id_source"),
                            "rejection_reasons": rejected.get("rejection_reasons", []),
                        }
                        for rejected in plan.get("rejected_seed_nodes", [])
                    ],
                },
            }
        else:
            if private_state is None:
                private_state = _load_private_state(runtime_state_root, network=network_id, mode="execute")
            for action in plan["actions"]:
                if action["action"] not in {"write-static-nodes", "delete-static-nodes"}:
                    raise MotherBootnodePrecleanupError(
                        "MOTHER_BOOTNODE_PRECLEANUP_INTERNAL_ERROR",
                        f"unsupported action: {action['action']}",
                    )
                result = _run_writer_service(
                    private_state,
                    network=network_id,
                    action=action,
                    timeout=request_timeout,
                    max_response_bytes=response_limit,
                    max_wait_seconds=wait_limit,
                    poll_interval_seconds=poll_interval,
                    opener=opener,
                    sleeper=sleeper,
                    preserve_services=preserve_services,
                )
                mutation_results.append(result)

    mutation_performed = bool(mutation_results)
    evidence_status = (
        "failed"
        if unsafe_plan_error is not None
        else plan["status"]
        if all(result.get("status") == "pass" for result in mutation_results)
        else "failed"
    )

    evidence: dict[str, Any] = {
        "kind": KIND,
        "schema_version": 1,
        "completed_at": _utc_now(),
        "status": evidence_status,
        "network": network_id,
        "topology_evidence": {
            "path": topology_state["path"],
            "sha256": topology_state["sha256"],
            "discovered": topology_state["discovered"],
            "searched_subdirs": list(CURRENT_TOPOLOGY_EVIDENCE_SUBDIRS) if topology_state["discovered"] else [],
        },
        "mode": "execute" if execute else "inspect",
        "policy": {
            "coolify_compose_patch_performed": False,
            "coolify_restart_performed": False,
            "coolify_writer_service_created": mutation_performed,
            "docker_container_static_nodes_mutation_performed": mutation_performed,
            "writer_service_static_nodes_mutation_performed": mutation_performed,
            "local_docker_static_nodes_mutation_performed": False,
            "live_mutation_performed": mutation_performed,
            "static_nodes_path": STATIC_NODES_PATH,
            "preserve_services": bool(preserve_services),
        },
        "private_state_node_info": {
            "read": private_state_read_observation,
            "observations": private_state_node_info_observations,
        },
        "live_node_info_observations": live_node_info_observations,
        "plan": plan,
        "mutation_results": mutation_results,
        "summary": {
            "clean": unsafe_plan_error is None and plan["clean"] and all(result.get("status") == "pass" for result in mutation_results),
            "complete": plan["complete"],
            "survivor_count": plan["survivor_count"],
            "actions": [action["action"] for action in plan["actions"]],
            "write_count": sum(1 for action in plan["actions"] if action["action"] == "write-static-nodes"),
            "delete_count": sum(1 for action in plan["actions"] if action["action"] == "delete-static-nodes"),
            "compose_patch_performed": False,
            "restart_performed": False,
            "writer_service_count": len(mutation_results),
            "writer_service_passed_count": sum(1 for result in mutation_results if result.get("status") == "pass"),
            "writer_service_deleted_count": sum(1 for result in mutation_results if result.get("writer_service_deleted") is True),
            "writer_service_preserved_count": sum(1 for result in mutation_results if result.get("writer_service_preserved") is True),
            "local_docker_mutation_performed": False,
            "live_mutation_performed": mutation_performed,
        },
    }
    if unsafe_plan_error is not None:
        evidence.update(unsafe_plan_error)
    evidence["bootnode_precleanup_sha256"] = hashlib.sha256(canonical_json(evidence)).hexdigest()

    if write_evidence:
        path, sha = _write_evidence(paths, evidence, network=network_id)
        evidence = {
            **evidence,
            "evidence": {"path": path, "sha256": sha},
        }
    return evidence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare Besu static-nodes.json before cleanup/restart.")
    parser.add_argument("network", nargs="?", default="mainnet", help="network name, for example mainnet")
    parser.add_argument("--runtime-state-root", default="runtime/state")
    parser.add_argument("--topology-evidence")
    parser.add_argument("--acknowledge-topology-evidence-sha256")
    parser.add_argument("--exclude-node", action="append", default=[], help="node to exclude, repeatable; used for remove-node target")
    parser.add_argument("--max-static-nodes", type=int, default=5)
    parser.add_argument("--execute", action="store_true", help="write/delete static-nodes.json through temporary Coolify writer services")
    parser.add_argument("--allow-mutation", action="store_true", help="required together with --execute")
    parser.add_argument("--preserve-services", action="store_true", help="do not delete temporary writer services after they reach running:healthy")
    parser.add_argument("--no-live-node-info", action="store_true", help="do not query containers for admin_nodeInfo")
    parser.add_argument("--no-write-evidence", action="store_true")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-response-bytes", type=int, default=DEFAULT_MAX_RESPONSE_BYTES)
    parser.add_argument("--max-wait-seconds", type=float, default=DEFAULT_MAX_WAIT_SECONDS)
    parser.add_argument("--poll-interval-seconds", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_bootnode_precleanup(
            network=args.network,
            runtime_state_root=args.runtime_state_root,
            topology_evidence=args.topology_evidence,
            acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
            exclude_nodes=args.exclude_node,
            max_static_nodes=args.max_static_nodes,
            execute=args.execute,
            allow_mutation=args.allow_mutation,
            preserve_services=args.preserve_services,
            probe_node_info=not args.no_live_node_info,
            write_evidence=not args.no_write_evidence,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
    except MotherBootnodePrecleanupError as exc:
        print(json.dumps({"status": "failed", "code": exc.code, "error": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    print(_pretty_json(result), end="")
    return 0 if result.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
