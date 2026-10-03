#!/usr/bin/env python3
"""Verify the Coolify replica-sync guardian production fallback without SSH.

This smoke uses only the local Mother private-state bundle plus outbound Coolify
API calls. It creates a disposable two-component Coolify service on one
controller, proves the node and replica-sync guardian are healthy, then invokes
the *actual* production replica-sync guardian-start fallback against that
disposable service.

The production fallback must recreate only ``mother-replica-sync-guardian`` from
Coolify's canonical normalized ``docker-compose.yml``. The smoke then verifies
through the Coolify API that the guardian and parent return healthy while the
node application UUID remains unchanged.

No SSH is used. No real Mother node, validator, topology, or chain state is
mutated. All target resources created by this smoke are disposable.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable
import urllib.parse
import urllib.request

import yaml


def _install_repo_import_path(explicit_repo_root: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit_repo_root:
        candidates.append(Path(explicit_repo_root).expanduser().resolve(strict=False))
    candidates.append(Path.cwd().resolve(strict=False))
    here = Path(__file__).resolve(strict=False)
    candidates.extend([here.parent, *(here.parents[:4])])
    for candidate in candidates:
        if (candidate / "tools" / "mother" / "common").is_dir():
            text = str(candidate)
            if text not in sys.path:
                sys.path.insert(0, text)
            return candidate
    raise SystemExit(
        "Could not find repo root containing tools/mother/common. "
        "Run from C:\\Users\\subsi\\main_computer or pass --repo-root."
    )


def _preparse_repo_root(argv: list[str]) -> str | None:
    for index, item in enumerate(argv):
        if item == "--repo-root" and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith("--repo-root="):
            return item.split("=", 1)[1]
    return None


REPO_ROOT = _install_repo_import_path(_preparse_repo_root(sys.argv[1:]))

from tools.mother.common.coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from tools.mother.common.deployment_completed_helper_cleanup import (
    _application_records,
    _application_uuid,
    _controller_config,
    _http,
    _resolve_environment_uuid,
    _temporary_service_body,
)
import tools.mother.common.deployment_node_add_replica_sync_v2 as replica_sync_v2
from tools.mother.common.deployment_node_add_replica_sync_v2 import _run_replica_sync_guardian_start
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state


KIND = "main_computer.mother.coolify_guardian_replacement_smoke.v1"
EVIDENCE_SUBDIR = "coolify-guardian-replacement-smoke"
GUARDIAN_NAME = "mother-replica-sync-guardian"
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class SmokeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> SmokeError:
    return SmokeError(code, message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ").lower()


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value.strip()):
        raise _fail("MOTHER_COOLIFY_GUARDIAN_SMOKE_INVALID_ARGUMENT", f"invalid {label}: {value!r}")
    return value.strip()


def _operation(network: str, mode: str) -> OperationIdentity:
    stamp = _stamp()
    operation_id = f"mother-coolify-guardian-smoke-{mode}-{network}-{stamp}"
    return OperationIdentity(
        operation_id=operation_id,
        request_id=f"{operation_id}-request",
        network=network,
        operation_kind="MOTHER-OP-RESTORE-SERVICE",
    )


def _load_private_state(runtime_state_root: str | Path, *, network: str, mode: str) -> PrivateStateReadResult:
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root)).resolve_private_state_paths()
    return read_private_state(paths, operation=_operation(network, mode))


def _safe_status(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _component_map(payload: Any) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("name")): dict(item)
        for item in _application_records(payload)
        if isinstance(item.get("name"), str) and item.get("name")
    }


def _snapshot(payload: Any, *, node: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {
            "parent_status": "",
            "node": {"name": node, "uuid": "", "status": "missing"},
            "guardian": {"name": GUARDIAN_NAME, "uuid": "", "status": "missing"},
        }
    components = _component_map(payload)
    node_record = components.get(node, {"name": node, "uuid": "", "status": "missing"})
    guardian_record = components.get(
        GUARDIAN_NAME,
        {"name": GUARDIAN_NAME, "uuid": "", "status": "missing"},
    )
    return {
        "parent_status": str(payload.get("status") or ""),
        "node": {
            "name": node,
            "uuid": str(node_record.get("uuid") or ""),
            "status": str(node_record.get("status") or ""),
            "exclude_from_status": bool(node_record.get("exclude_from_status") is True),
        },
        "guardian": {
            "name": GUARDIAN_NAME,
            "uuid": str(guardian_record.get("uuid") or ""),
            "status": str(guardian_record.get("status") or ""),
            "exclude_from_status": bool(guardian_record.get("exclude_from_status") is True),
        },
    }


def _healthy_component(record: Mapping[str, Any]) -> bool:
    status = _safe_status(record.get("status"))
    return status.startswith("running:healthy") and "unhealthy" not in status


def _baseline_ready(snapshot: Mapping[str, Any]) -> bool:
    node = snapshot.get("node")
    guardian = snapshot.get("guardian")
    return isinstance(node, Mapping) and isinstance(guardian, Mapping) and _healthy_component(node) and _healthy_component(guardian)


def _split_observed(snapshot: Mapping[str, Any]) -> bool:
    node = snapshot.get("node")
    guardian = snapshot.get("guardian")
    if not isinstance(node, Mapping) or not isinstance(guardian, Mapping):
        return False
    if not _healthy_component(node):
        return False
    guardian_status = _safe_status(guardian.get("status"))
    parent_status = _safe_status(snapshot.get("parent_status"))
    return (not _healthy_component(guardian)) and (
        guardian_status.startswith(("exited", "stopped", "degraded"))
        or "unhealthy" in guardian_status
        or parent_status.startswith(("exited", "stopped", "degraded"))
        or "unhealthy" in parent_status
    )


def _repaired(snapshot: Mapping[str, Any], *, expected_node_uuid: str) -> bool:
    node = snapshot.get("node")
    guardian = snapshot.get("guardian")
    if not isinstance(node, Mapping) or not isinstance(guardian, Mapping):
        return False
    return (
        str(node.get("uuid") or "") == expected_node_uuid
        and _healthy_component(node)
        and _healthy_component(guardian)
    )


def _target_compose(node: str) -> str:
    guardian_health = (
        "import socket; "
        f"socket.gethostbyname({node!r})"
    )
    compose = {
        "services": {
            node: {
                "image": "python:3.12-alpine",
                "restart": "unless-stopped",
                "command": [
                    "python",
                    "-u",
                    "-c",
                    "import time\nwhile True:\n    time.sleep(3600)\n",
                ],
                "healthcheck": {
                    "test": ["CMD", "python", "-c", "import sys; sys.exit(0)"],
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 6,
                    "start_period": "2s",
                },
                "labels": {
                    "main_computer.mother.component": "coolify-guardian-smoke-node",
                    "main_computer.mother.smoke": "true",
                },
            },
            GUARDIAN_NAME: {
                "image": "python:3.12-alpine",
                "restart": "unless-stopped",
                "read_only": True,
                "depends_on": {node: {"condition": "service_started"}},
                "command": [
                    "python",
                    "-u",
                    "-c",
                    "import time\nwhile True:\n    time.sleep(3600)\n",
                ],
                "healthcheck": {
                    "test": ["CMD", "python", "-c", guardian_health],
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 6,
                    "start_period": "2s",
                },
                "labels": {
                    "main_computer.mother.component": "coolify-guardian-smoke-guardian",
                    "main_computer.mother.smoke": "true",
                },
            },
        }
    }
    return yaml.safe_dump(compose, sort_keys=False)


def _detail(
    controller: Any,
    service_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
    response = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=urllib.request.urlopen,
    )
    return {**response, "endpoint": endpoint}


def _wait_snapshot(
    controller: Any,
    service_uuid: str,
    *,
    node: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    predicate: Callable[[Mapping[str, Any]], bool],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    deadline = time.monotonic() + max_wait_seconds
    samples: list[dict[str, Any]] = []
    last: dict[str, Any] = _snapshot({}, node=node)
    while True:
        response = _detail(
            controller,
            service_uuid,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        if response.get("ok"):
            last = _snapshot(response.get("payload"), node=node)
        else:
            last = {
                "parent_status": f"http:{response.get('status')}",
                "node": {"name": node, "uuid": "", "status": "unavailable"},
                "guardian": {"name": GUARDIAN_NAME, "uuid": "", "status": "unavailable"},
            }
        samples.append({
            "observed_at": _utc_now(),
            "http_status": response.get("status"),
            "response_sha256": response.get("response_sha256"),
            **last,
        })
        if response.get("ok") and predicate(last):
            return last, samples[-20:]
        if time.monotonic() >= deadline:
            return last, samples[-20:]
        time.sleep(max(0.5, min(poll_interval_seconds, deadline - time.monotonic())))


def _canonical_repair_script(*, target_uuid: str, node: str, wait_seconds: int, poll_seconds: int) -> str:
    target = _identifier(target_uuid, "target_uuid")
    node_name = _identifier(node, "node")
    wait_limit = max(1, int(wait_seconds))
    poll_interval = max(1, int(poll_seconds))
    return "\n".join(
        [
            "set -eu",
            f"TARGET_SERVICE_UUID='{target}'",
            f"NODE_NAME='{node_name}'",
            "GUARDIAN_NAME='mother-replica-sync-guardian'",
            f"WAIT_LIMIT={wait_limit}",
            f"POLL_INTERVAL={poll_interval}",
            "normalize_label() {",
            "  case \"${1:-}\" in ''|'<no value>'|'<nil>'|'null') printf '' ;; *) printf '%s' \"$1\" ;; esac",
            "}",
            "find_project() {",
            "  uuid=\"$1\"",
            "  for c in $(docker ps -aq --filter \"label=com.docker.compose.project=$uuid\" 2>/dev/null || true); do",
            "    project=\"$(normalize_label \"$(docker inspect -f '{{ index .Config.Labels \"com.docker.compose.project\" }}' \"$c\" 2>/dev/null || true)\")\"",
            "    workdir=\"$(normalize_label \"$(docker inspect -f '{{ index .Config.Labels \"com.docker.compose.project.working_dir\" }}' \"$c\" 2>/dev/null || true)\")\"",
            "    if [ -n \"$project\" ] && [ -n \"$workdir\" ]; then printf '%s\\n%s\\n' \"$project\" \"$workdir\"; return 0; fi",
            "  done",
            "  echo \"MOTHER_COOLIFY_GUARDIAN_CANONICAL_REPAIR_DIAG project-not-found service_uuid=$uuid\" >&2",
            "  return 1",
            "}",
            "node_healthy() {",
            "  project=\"$1\"",
            "  ids=\"$(docker ps -aq --filter \"label=com.docker.compose.project=$project\" --filter \"label=com.docker.compose.service=$NODE_NAME\" 2>/dev/null || true)\"",
            "  for c in $ids; do",
            "    state=\"$(normalize_label \"$(docker inspect -f '{{ .State.Status }}' \"$c\" 2>/dev/null || true)\")\"",
            "    health=\"$(normalize_label \"$(docker inspect -f '{{ if .State.Health }}{{ .State.Health.Status }}{{ end }}' \"$c\" 2>/dev/null || true)\")\"",
            "    if [ \"$state\" = running ] && [ \"$health\" = healthy ]; then return 0; fi",
            "  done",
            "  return 1",
            "}",
            "guardian_healthy() {",
            "  project=\"$1\"",
            "  ids=\"$(docker ps -aq --filter \"label=com.docker.compose.project=$project\" --filter \"label=com.docker.compose.service=$GUARDIAN_NAME\" 2>/dev/null || true)\"",
            "  for c in $ids; do",
            "    state=\"$(normalize_label \"$(docker inspect -f '{{ .State.Status }}' \"$c\" 2>/dev/null || true)\")\"",
            "    health=\"$(normalize_label \"$(docker inspect -f '{{ if .State.Health }}{{ .State.Health.Status }}{{ end }}' \"$c\" 2>/dev/null || true)\")\"",
            "    if [ \"$state\" = running ] && [ \"$health\" = healthy ]; then return 0; fi",
            "  done",
            "  return 1",
            "}",
            "info=\"$(find_project \"$TARGET_SERVICE_UUID\")\"",
            "PROJECT=\"$(printf '%s\\n' \"$info\" | sed -n '1p')\"",
            "WORKDIR=\"$(printf '%s\\n' \"$info\" | sed -n '2p')\"",
            "CANONICAL_COMPOSE=\"$WORKDIR/docker-compose.yml\"",
            "if ! node_healthy \"$PROJECT\"; then echo \"MOTHER_COOLIFY_GUARDIAN_CANONICAL_REPAIR_DIAG node-not-running-healthy\" >&2; exit 1; fi",
            "if [ ! -f \"$CANONICAL_COMPOSE\" ]; then echo \"MOTHER_COOLIFY_GUARDIAN_CANONICAL_REPAIR_DIAG canonical-compose-missing path=$CANONICAL_COMPOSE\" >&2; exit 1; fi",
            "docker compose -p \"$PROJECT\" -f \"$CANONICAL_COMPOSE\" --project-directory \"$WORKDIR\" up -d --no-deps --force-recreate \"$GUARDIAN_NAME\" || { echo \"MOTHER_COOLIFY_GUARDIAN_CANONICAL_REPAIR_DIAG canonical-compose-up-failed project=$PROJECT\" >&2; exit 1; }",
            "start=$(date +%s)",
            "while :; do",
            "  if guardian_healthy \"$PROJECT\"; then",
            "    touch /tmp/mother-coolify-guardian-canonical-repair-done",
            "    echo mother-coolify-guardian-canonical-repair-done",
            "    sleep 120",
            "    exit 0",
            "  fi",
            "  now=$(date +%s)",
            "  if [ $((now - start)) -ge \"$WAIT_LIMIT\" ]; then echo \"MOTHER_COOLIFY_GUARDIAN_CANONICAL_REPAIR_DIAG guardian-health-timeout project=$PROJECT\" >&2; exit 1; fi",
            "  sleep \"$POLL_INTERVAL\"",
            "done",
        ]
    ) + "\n"


def _canonical_repair_compose(service_name: str, script: str) -> str:
    compose = {
        "services": {
            service_name: {
                "image": "docker:27-cli",
                "command": ["sh", "-lc", script.replace("$", "$$")],
                "volumes": [
                    "/var/run/docker.sock:/var/run/docker.sock",
                    "/data/coolify:/data/coolify:ro",
                ],
                "restart": "no",
                "labels": {
                    "main_computer.mother.component": "coolify-guardian-canonical-repair-smoke",
                    "main_computer.mother.not_a_validator": "true",
                    "main_computer.mother.not_a_chain_service": "true",
                },
                "healthcheck": {
                    "test": ["CMD-SHELL", "test -f /tmp/mother-coolify-guardian-canonical-repair-done"],
                    "interval": "5s",
                    "timeout": "2s",
                    "retries": 3,
                    "start_period": "1s",
                },
            }
        }
    }
    return yaml.safe_dump(compose, sort_keys=False)


def _start_canonical_repair_helper(
    *,
    controller: Any,
    controller_config: Mapping[str, Any],
    environment_uuid: str,
    network: str,
    target_uuid: str,
    node: str,
    timeout: float,
    max_response_bytes: int,
    wait_seconds: float,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    helper_name = _identifier(f"mother-guardian-canonical-repair-{_stamp()}"[:63], "canonical repair helper")
    script = _canonical_repair_script(
        target_uuid=target_uuid,
        node=node,
        wait_seconds=max(1, int(wait_seconds)),
        poll_seconds=max(1, int(poll_interval_seconds)),
    )
    compose = _canonical_repair_compose(helper_name, script)
    body = _temporary_service_body(controller_config, helper_name, compose)
    body["environment_name"] = network
    body["environment_uuid"] = environment_uuid
    body["description"] = "Disposable Coolify canonical guardian repair smoke helper"
    body["instant_deploy"] = False
    create = _http(
        controller,
        "POST",
        "/api/v1/services",
        body=body,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=urllib.request.urlopen,
    )
    result: dict[str, Any] = {
        "helper_name": helper_name,
        "helper_uuid": None,
        "create": {
            "status": create.get("status"),
            "ok": create.get("ok") is True,
            "response_sha256": create.get("response_sha256"),
        },
        "start": None,
    }
    if not create.get("ok"):
        return result
    helper_uuid = _application_uuid(create.get("payload"))
    result["helper_uuid"] = helper_uuid
    endpoint = f"/api/v1/services/{urllib.parse.quote(helper_uuid, safe='')}/start"
    start = _http(
        controller,
        "POST",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=urllib.request.urlopen,
    )
    result["start"] = {
        "status": start.get("status"),
        "ok": start.get("ok") is True,
        "response_sha256": start.get("response_sha256"),
    }
    return result


def _child_action(
    controller: Any,
    *,
    parent_service_uuid: str,
    application_uuid: str,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    parent_q = urllib.parse.quote(parent_service_uuid, safe="")
    app_q = urllib.parse.quote(application_uuid, safe="")
    attempts = (
        ("POST", f"/api/v1/applications/{app_q}/restart", "application-restart"),
        ("POST", f"/api/v1/applications/{app_q}/start", "application-start"),
        ("POST", f"/api/v1/services/{parent_q}/applications/{app_q}/restart", "service-application-restart"),
        ("POST", f"/api/v1/services/{parent_q}/applications/{app_q}/start", "service-application-start"),
    )
    receipts: list[dict[str, Any]] = []
    for method, endpoint, scope in attempts:
        response = _http(
            controller,
            method,
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=_DEFAULT_OPENER,
        )
        receipt = {
            "method": method,
            "endpoint": endpoint,
            "scope": scope,
            "status": response.get("status"),
            "ok": response.get("ok") is True,
            "response_sha256": response.get("response_sha256"),
            "byte_length": response.get("byte_length"),
            "elapsed_ms": response.get("elapsed_ms"),
        }
        receipts.append(receipt)
        if receipt["ok"]:
            return {"ok": True, "selected": receipt, "attempts": receipts}
    return {"ok": False, "selected": None, "attempts": receipts}



def _smoke_guardian_start_log_diagnostic(
    *,
    controller: Any,
    controller_id: str,
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    """Read helper logs through Coolify's child-application APIs while it still exists."""
    quoted_service = urllib.parse.quote(service_uuid, safe="")
    detail_endpoint = f"/api/v1/services/{quoted_service}"
    detail = _http(
        controller,
        "GET",
        detail_endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    applications = []
    if detail.get("ok"):
        applications = [dict(item) for item in _application_records(detail.get("payload"))]
    selected = None
    for item in applications:
        if str(item.get("name") or "") == service_name:
            selected = item
            break
    if selected is None and len(applications) == 1:
        selected = applications[0]

    application_uuid = str((selected or {}).get("uuid") or "")
    attempts: list[dict[str, Any]] = []
    selected_logs: str | None = None
    if application_uuid:
        quoted_app = urllib.parse.quote(application_uuid, safe="")
        endpoints = (
            (
                "service-application",
                f"/api/v1/services/{quoted_service}/applications/{quoted_app}/logs?lines=300&show_timestamps=true",
            ),
            ("application", f"/api/v1/applications/{quoted_app}/logs?lines=300"),
        )
        for endpoint_kind, endpoint in endpoints:
            response = _http(
                controller,
                "GET",
                endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            payload = response.get("payload")
            logs = None
            if isinstance(payload, Mapping) and isinstance(payload.get("logs"), str):
                logs = payload.get("logs")
            elif isinstance(payload, str):
                logs = payload
            attempts.append(
                {
                    "endpoint_kind": endpoint_kind,
                    "endpoint": endpoint,
                    "status": response.get("status"),
                    "ok": response.get("ok") is True,
                    "response_sha256": response.get("response_sha256"),
                    "byte_length": response.get("byte_length"),
                    "logs_field_present": isinstance(logs, str),
                }
            )
            if isinstance(logs, str):
                selected_logs = logs
                break

    markers = []
    if isinstance(selected_logs, str):
        for token in (
            "MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG project-not-found",
            "MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG node-not-running-healthy",
            "MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG canonical-compose-missing",
            "MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG canonical-compose-up-failed",
            "MOTHER_REPLICA_SYNC_GUARDIAN_START_DIAG guardian-health-timeout",
            "mother-replica-sync-guardian-started",
        ):
            if token in selected_logs:
                markers.append(token)

    return {
        "observed_at": _utc_now(),
        "controller_id": controller_id,
        "temporary_service_uuid": service_uuid,
        "temporary_service_name": service_name,
        "application_uuid": application_uuid or None,
        "application_name": str((selected or {}).get("name") or "") or None,
        "application_status": str((selected or {}).get("status") or "") or None,
        "detail_status": detail.get("status"),
        "detail_ok": detail.get("ok") is True,
        "attempts": attempts,
        "failure_markers": markers,
        "logs": selected_logs[-12000:] if isinstance(selected_logs, str) else None,
        "logs_sha256": hashlib.sha256(selected_logs.encode("utf-8")).hexdigest() if isinstance(selected_logs, str) else None,
        "logs_byte_length": len(selected_logs.encode("utf-8")) if isinstance(selected_logs, str) else None,
        "raw_logs_persisted": isinstance(selected_logs, str),
        "read_only": True,
    }


def _delete_service(
    controller: Any,
    service_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
    response = _http(
        controller,
        "DELETE",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=urllib.request.urlopen,
    )
    return {
        "method": "DELETE",
        "endpoint": endpoint,
        "status": response.get("status"),
        "ok": response.get("ok") is True or response.get("status") == 404,
        "response_sha256": response.get("response_sha256"),
        "byte_length": response.get("byte_length"),
        "elapsed_ms": response.get("elapsed_ms"),
    }


def _cleanup_runner_compose(service_name: str, target_uuid: str) -> str:
    target_q = target_uuid.replace("'", "'\\''")
    script = "\n".join(
        [
            "set -eu",
            f"TARGET='{target_q}'",
            "for c in $(docker ps -aq --filter \"label=com.docker.compose.project=$TARGET\" 2>/dev/null || true); do docker rm -f \"$c\" >/dev/null 2>&1 || true; done",
            "docker network rm \"${TARGET}_default\" >/dev/null 2>&1 || true",
            "touch /tmp/mother-coolify-guardian-smoke-cleanup-done",
            "sleep 10",
        ]
    )
    compose = {
        "services": {
            service_name: {
                "image": "docker:27-cli",
                "command": ["sh", "-lc", script.replace("$", "$$")],
                "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
                "restart": "no",
                "healthcheck": {
                    "test": ["CMD-SHELL", "test -f /tmp/mother-coolify-guardian-smoke-cleanup-done"],
                    "interval": "2s",
                    "timeout": "1s",
                    "retries": 10,
                    "start_period": "1s",
                },
                "labels": {
                    "main_computer.mother.component": "coolify-guardian-smoke-cleanup",
                    "main_computer.mother.not_a_chain_service": "true",
                },
            }
        }
    }
    return yaml.safe_dump(compose, sort_keys=False)


def _best_effort_docker_cleanup_via_coolify(
    *,
    controller: Any,
    controller_config: Mapping[str, Any],
    environment_uuid: str,
    network: str,
    target_uuid: str,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    runner_name = f"mother-coolify-guardian-cleanup-{_stamp()}"
    compose = _cleanup_runner_compose(runner_name, target_uuid)
    body = _temporary_service_body(controller_config, runner_name, compose)
    body["environment_name"] = network
    body["environment_uuid"] = environment_uuid
    body["description"] = "Ephemeral cleanup for Coolify guardian replacement smoke"
    body["instant_deploy"] = False
    create = _http(
        controller,
        "POST",
        "/api/v1/services",
        body=body,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=urllib.request.urlopen,
    )
    result: dict[str, Any] = {
        "create_status": create.get("status"),
        "create_ok": create.get("ok") is True,
        "runner_uuid": None,
        "start_status": None,
        "delete_status": None,
    }
    if not create.get("ok"):
        return result
    runner_uuid = _application_uuid(create.get("payload"))
    result["runner_uuid"] = runner_uuid
    start_endpoint = f"/api/v1/services/{urllib.parse.quote(runner_uuid, safe='')}/start"
    start = _http(
        controller,
        "POST",
        start_endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=urllib.request.urlopen,
    )
    result["start_status"] = start.get("status")
    time.sleep(4)
    deletion = _delete_service(
        controller,
        runner_uuid,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    )
    result["delete_status"] = deletion.get("status")
    result["delete_ok"] = deletion.get("ok")
    return result


def _write_evidence(runtime_state_root: str | Path, network: str, evidence: Mapping[str, Any]) -> Path:
    root = MotherPaths(runtime_state_root=Path(runtime_state_root)).evidence_root / EVIDENCE_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    path = root / f"{_stamp()}-{network}-{digest[:16]}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def run_smoke(
    *,
    runtime_state_root: str | Path,
    network: str,
    controller_id: str,
    execute: bool,
    timeout: float,
    max_response_bytes: int,
    wait_seconds: float,
    fallback_wait_seconds: float,
    poll_interval_seconds: float,
    keep_target: bool,
) -> tuple[dict[str, Any], Path | None]:
    network = _identifier(network, "network")
    controller_id = _identifier(controller_id, "controller_id")
    mode = "execute" if execute else "plan"
    private_state = _load_private_state(runtime_state_root, network=network, mode=mode)
    controller = resolve_coolify_controller(private_state, network, controller_id)
    controller_config = _controller_config(private_state, network=network, controller_id=controller_id)
    observations: list[dict[str, Any]] = []
    environment_uuid = _resolve_environment_uuid(
        controller=controller,
        controller_id=controller_id,
        endpoint=f"/api/v1/projects/{urllib.parse.quote(str(controller_config['project_uuid']), safe='')}/environments",
        expected_name=network,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=urllib.request.urlopen,
        observations=observations,
    )

    token = _stamp().replace("t", "-").replace("z", "")[-20:]
    node = _identifier(f"mc-smoke-node-{token}"[:63], "smoke node")
    target_name = _identifier(f"mc-guardian-smoke-{token}"[:63], "smoke target")
    compose_text = _target_compose(node)
    plan = {
        "kind": KIND,
        "mode": mode,
        "created_at": _utc_now(),
        "network": network,
        "controller_id": controller_id,
        "target_name": target_name,
        "node": node,
        "guardian": GUARDIAN_NAME,
        "production_function_under_test": "tools.mother.common.deployment_node_add_replica_sync_v2._run_replica_sync_guardian_start",
        "expected_behavior": "production guardian helper recreates from Coolify canonical normalized docker-compose.yml only",
        "uses_ssh": False,
        "parent_restart_allowed": False,
        "real_mother_node_mutation_allowed": False,
        "target_compose_sha256": hashlib.sha256(compose_text.encode("utf-8")).hexdigest(),
        "observations": observations,
    }
    if not execute:
        plan["status"] = "planned"
        return plan, None

    target_uuid = ""
    result: dict[str, Any] = dict(plan)
    cleanup: dict[str, Any] = {}
    try:
        body = _temporary_service_body(controller_config, target_name, compose_text)
        body["environment_name"] = network
        body["environment_uuid"] = environment_uuid
        body["description"] = "Disposable Coolify guardian replacement reproduction smoke"
        body["instant_deploy"] = False
        create = _http(
            controller,
            "POST",
            "/api/v1/services",
            body=body,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=urllib.request.urlopen,
        )
        result["target_create"] = {
            "status": create.get("status"),
            "ok": create.get("ok") is True,
            "response_sha256": create.get("response_sha256"),
            "byte_length": create.get("byte_length"),
            "elapsed_ms": create.get("elapsed_ms"),
        }
        if not create.get("ok"):
            raise _fail("MOTHER_COOLIFY_GUARDIAN_SMOKE_CREATE_FAILED", f"target create failed with HTTP {create.get('status')}")
        target_uuid = _application_uuid(create.get("payload"))
        result["target_service_uuid"] = target_uuid

        start_endpoint = f"/api/v1/services/{urllib.parse.quote(target_uuid, safe='')}/start"
        start = _http(
            controller,
            "POST",
            start_endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=urllib.request.urlopen,
        )
        result["target_start"] = {
            "status": start.get("status"),
            "ok": start.get("ok") is True,
            "response_sha256": start.get("response_sha256"),
            "byte_length": start.get("byte_length"),
            "elapsed_ms": start.get("elapsed_ms"),
        }
        if not start.get("ok"):
            raise _fail("MOTHER_COOLIFY_GUARDIAN_SMOKE_START_FAILED", f"target start failed with HTTP {start.get('status')}")

        baseline, baseline_samples = _wait_snapshot(
            controller,
            target_uuid,
            node=node,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            predicate=_baseline_ready,
        )
        result["baseline"] = baseline
        result["baseline_samples"] = baseline_samples
        result["baseline_verified"] = _baseline_ready(baseline)
        if not result["baseline_verified"]:
            raise _fail("MOTHER_COOLIFY_GUARDIAN_SMOKE_BASELINE_NOT_HEALTHY", "disposable baseline never reached healthy node + guardian")

        node_uuid = str(baseline["node"]["uuid"])
        guardian_uuid = str(baseline["guardian"]["uuid"])
        if not node_uuid or not guardian_uuid:
            raise _fail("MOTHER_COOLIFY_GUARDIAN_SMOKE_COMPONENT_UUID_MISSING", "Coolify baseline lacks node/guardian application UUID")

        original_log_diagnostic = replica_sync_v2._guardian_start_log_diagnostic
        replica_sync_v2._guardian_start_log_diagnostic = _smoke_guardian_start_log_diagnostic
        try:
            broken = replica_sync_v2._run_replica_sync_guardian_start(
                private_state,
                network=network,
                controller_id=controller_id,
                service_uuid=target_uuid,
                node=node,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                max_wait_seconds=fallback_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                opener=_DEFAULT_OPENER,
            )
        finally:
            replica_sync_v2._guardian_start_log_diagnostic = original_log_diagnostic
        result["production_fallback"] = broken

        post_fallback, post_fallback_samples = _wait_snapshot(
            controller,
            target_uuid,
            node=node,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            predicate=lambda item: _repaired(item, expected_node_uuid=node_uuid),
        )
        result["post_fallback"] = post_fallback
        result["post_fallback_samples"] = post_fallback_samples
        result["production_fallback_verified"] = (
            broken.get("status") == "pass"
            and _repaired(post_fallback, expected_node_uuid=node_uuid)
        )
        result["node_uuid_preserved"] = str(post_fallback.get("node", {}).get("uuid") or "") == node_uuid
        result["bug_reproduced"] = _split_observed(post_fallback)

        result["status"] = (
            "pass"
            if result["production_fallback_verified"]
            and result["node_uuid_preserved"]
            and not result["bug_reproduced"]
            else "failed"
        )
        result["verdict"] = {
            "production_canonical_compose_fallback_worked": result["production_fallback_verified"],
            "bug_reproduced": result["bug_reproduced"],
            "node_application_uuid_unchanged": result["node_uuid_preserved"],
            "parent_restart_used": False,
            "ssh_used": False,
        }
    except Exception as exc:  # noqa: BLE001
        result["status"] = "failed"
        result["failure"] = {
            "code": str(getattr(exc, "code", type(exc).__name__)),
            "message": str(exc)[:1000],
        }
    finally:
        if target_uuid and not keep_target:
            try:
                cleanup["target_delete"] = _delete_service(
                    controller,
                    target_uuid,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                )
            except Exception as exc:  # noqa: BLE001
                cleanup["target_delete"] = {
                    "ok": False,
                    "error_code": str(getattr(exc, "code", type(exc).__name__)),
                    "error": str(exc)[:512],
                }
            try:
                cleanup["docker_orphan_cleanup"] = _best_effort_docker_cleanup_via_coolify(
                    controller=controller,
                    controller_config=controller_config,
                    environment_uuid=environment_uuid,
                    network=network,
                    target_uuid=target_uuid,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                )
            except Exception as exc:  # noqa: BLE001
                cleanup["docker_orphan_cleanup"] = {
                    "create_ok": False,
                    "error_code": str(getattr(exc, "code", type(exc).__name__)),
                    "error": str(exc)[:512],
                }
        elif target_uuid:
            cleanup["target_kept"] = True
        result["cleanup"] = cleanup

    evidence_path = _write_evidence(runtime_state_root, network, result)
    result["evidence_path"] = str(evidence_path)
    result["evidence_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    return result, evidence_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the Coolify replica-sync guardian production fallback on a disposable service using "
            "Coolify's canonical normalized Compose definition. No SSH."
        )
    )
    parser.add_argument("command", choices=["plan", "run"])
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--controller-id", default="coolify-c")
    parser.add_argument("--execute", action="store_true", help="Required with run; creates only disposable Coolify smoke resources.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=_DEFAULT_MAX_RESPONSE_BYTES)
    parser.add_argument("--wait-seconds", type=float, default=90.0)
    parser.add_argument("--fallback-wait-seconds", type=float, default=120.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    parser.add_argument("--keep-target", action="store_true", help="Keep the disposable target service for manual Coolify inspection.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run" and not args.execute:
        print("run requires --execute", file=sys.stderr)
        return 2
    if args.command == "plan" and args.execute:
        print("plan does not accept --execute", file=sys.stderr)
        return 2
    result, _ = run_smoke(
        runtime_state_root=args.runtime_state_root,
        network=args.network,
        controller_id=args.controller_id,
        execute=args.command == "run" and args.execute,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        wait_seconds=args.wait_seconds,
        fallback_wait_seconds=args.fallback_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        keep_target=args.keep_target,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"planned", "pass"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
