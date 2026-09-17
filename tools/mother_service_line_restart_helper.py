#!/usr/bin/env python3
"""Standalone Mother restart helper for one Coolify Compose service line.

This tool creates one temporary docker:27-cli Coolify service on the selected
controller.  The temporary service mounts /var/run/docker.sock and performs only
a Docker-level start/restart of exactly one container whose Compose labels match
the requested parent service UUID and service line.

Manual/operator runs preserve the temporary helper service by default for log
inspection.  Mother automation may pass --delete-helper-after-exit to remove the
temporary helper after it has exited.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping
import urllib.parse
import urllib.request

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import (  # noqa: E402
    CoolifyController,
    list_coolify_controllers,
    resolve_coolify_controller,
)
from tools.mother.common.deployment_completed_helper_cleanup import (  # noqa: E402
    MotherDeploymentCompletedHelperCleanupError,
    _application_uuid,
    _controller_config,
    _http,
    _resolve_environment_uuid,
)
from tools.mother.common.models import OperationIdentity  # noqa: E402
from tools.mother.common.paths import MotherPaths  # noqa: E402
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state  # noqa: E402


KIND = "main_computer.mother.service_line_restart_helper.v1"
EVIDENCE_SUBDIR = "mother-service-line-restart-helper"
HELPER_PREFIX = "mother-service-line-restart"
RUNTIME_PREFIX = "MOTHER_SERVICE_LINE_RESTART_HELPER"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")


class MotherServiceLineRestartHelperError(RuntimeError):
    """Service-line restart helper failed before a trustworthy result existed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or not _IDENTIFIER_RE.fullmatch(value.strip()):
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_INVALID_ARGUMENT",
            f"invalid {field}",
        )
    return value.strip()


def _uuid(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or not _UUID_RE.fullmatch(value.strip()):
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_INVALID_ARGUMENT",
            f"invalid {field}",
        )
    return value.strip()


def _positive(value: float | int, field: str) -> float:
    if type(value) not in {int, float} or value <= 0:
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_INVALID_ARGUMENT",
            f"{field} must be positive",
        )
    return float(value)


def _nonnegative(value: float | int, field: str) -> float:
    if type(value) not in {int, float} or value < 0:
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_INVALID_ARGUMENT",
            f"{field} must be non-negative",
        )
    return float(value)


def _slug_display_name(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value.lower())
    return "-".join(parts)


def _validate_display_name_guard(node: str, display_name: str | None) -> dict[str, Any]:
    if display_name is None:
        return {
            "provided": False,
            "display_name": None,
            "display_name_slug": None,
            "node": node,
            "matched": None,
        }
    text = display_name.strip()
    if not text:
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_INVALID_ARGUMENT",
            "display name must not be empty when provided",
        )
    slug = _slug_display_name(text)
    matched = slug == node.lower()
    if not matched:
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_DISPLAY_NAME_MISMATCH",
            f"display name {text!r} does not normalize to node {node!r}",
        )
    return {
        "provided": True,
        "display_name": text,
        "display_name_slug": slug,
        "node": node,
        "matched": True,
    }


def _service_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("services", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        if any(key in payload for key in ("uuid", "id", "name")):
            return [payload]
    return []


def _safe_record(item: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("uuid", "id", "name", "description", "status", "project_uuid", "environment_uuid", "server_uuid"):
        value = item.get(key)
        if value is None or type(value) in {bool, int, float}:
            if key in item:
                result[key] = value
        elif type(value) is str:
            result[key] = value[:512]
    return result


def _service_uuid_from_record(item: Mapping[str, Any]) -> str:
    value = item.get("uuid", item.get("id"))
    return _uuid(value, "resolved service_uuid")


def _service_name_from_record(item: Mapping[str, Any]) -> str:
    return _identifier(item.get("name"), "resolved service name")


def _request_services(
    controller: CoolifyController,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    response = _http(
        controller,
        "GET",
        "/api/v1/services",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    return {
        "method": "GET",
        "endpoint": "/api/v1/services",
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "payload": response.get("payload"),
    }


def _resolve_service_line_target(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    node: str | None,
    display_name: str | None,
    controller_id: str | None,
    service_uuid: str | None,
    service_line: str | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    network_id = _identifier(network, "network")
    observations: list[dict[str, Any]] = []

    exact_controller = _identifier(controller_id, "controller_id") if controller_id is not None else None
    exact_service_uuid = _uuid(service_uuid, "service_uuid") if service_uuid is not None else None

    if node is not None:
        node_id = _identifier(node, "node")
        display_guard = _validate_display_name_guard(node_id, display_name)
        line = _identifier(service_line or node_id, "service_line")
    else:
        if display_name is not None:
            raise MotherServiceLineRestartHelperError(
                "MOTHER_SERVICE_LINE_RESTART_INVALID_ARGUMENT",
                "--display-name requires --node",
            )
        if service_line is None:
            raise MotherServiceLineRestartHelperError(
                "MOTHER_SERVICE_LINE_RESTART_INVALID_ARGUMENT",
                "either --node or --service-line is required",
            )
        node_id = None
        display_guard = {
            "provided": False,
            "display_name": None,
            "display_name_slug": None,
            "node": None,
            "matched": None,
        }
        line = _identifier(service_line, "service_line")

    if exact_controller is not None and exact_service_uuid is not None:
        controller = resolve_coolify_controller(private_state, network_id, exact_controller, require_enabled=True, require_token=True)
        return {
            "network": network_id,
            "controller_id": exact_controller,
            "controller": controller,
            "service_uuid": exact_service_uuid,
            "service_line": line,
            "node": node_id,
            "display_name_guard": display_guard,
            "resolution_strategy": "explicit-controller-service-line",
            "resolution_observations": observations,
            "matched_service_record": None,
        }

    controllers = [
        controller
        for controller in list_coolify_controllers(private_state)
        if controller.network == network_id and controller.enabled and controller.api_token.strip()
    ]
    if exact_controller is not None:
        controllers = [controller for controller in controllers if controller.controller_id == exact_controller]
    if not controllers:
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_CONTROLLER_NOT_FOUND",
            "no enabled Coolify controller with API token matched the request",
        )

    matches: list[dict[str, Any]] = []
    for controller in controllers:
        response = _request_services(
            controller,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        observations.append(
            {
                key: response[key]
                for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")
            }
            | {"controller_id": controller.controller_id, "phase": "service-line-target-resolution"}
        )
        if response["ok"] is not True:
            continue
        for item in _service_items(response.get("payload")):
            safe = _safe_record(item)
            name = str(item.get("name") or "").strip()
            uuid = str(item.get("uuid") or item.get("id") or "").strip()
            if exact_service_uuid is not None:
                matched = uuid == exact_service_uuid
            elif node_id is not None:
                matched = name == node_id
            else:
                matched = name == line
            if matched:
                matches.append(
                    {
                        "controller": controller,
                        "controller_id": controller.controller_id,
                        "service_uuid": _service_uuid_from_record(item),
                        "service_name": _service_name_from_record(item),
                        "record": safe,
                    }
                )

    unique = {
        (match["controller_id"], match["service_uuid"]): match
        for match in matches
    }
    matches = list(unique.values())
    if len(matches) != 1:
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_TARGET_RESOLUTION_AMBIGUOUS"
            if matches
            else "MOTHER_SERVICE_LINE_RESTART_TARGET_NOT_FOUND",
            f"expected exactly one Coolify service match, found {len(matches)}",
        )

    match = matches[0]
    return {
        "network": network_id,
        "controller_id": match["controller_id"],
        "controller": match["controller"],
        "service_uuid": match["service_uuid"],
        "service_line": line,
        "node": node_id,
        "display_name_guard": display_guard,
        "resolution_strategy": "coolify-service-list",
        "resolution_observations": observations,
        "matched_service_record": match["record"],
    }


def _single_quote(value: object) -> str:
    text = str(value)
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _runtime_event(**values: object) -> str:
    tokens = []
    for key, value in values.items():
        if value is None:
            continue
        text = str(value).replace("\n", "_").replace("\r", "_").replace("\t", "_").replace(" ", "_")
        tokens.append(f"{key}={text}")
    return f"{RUNTIME_PREFIX} " + " ".join(tokens)


def _helper_shell_script(
    *,
    parent_service_uuid: str,
    service_line: str,
    max_wait_seconds: float,
    poll_interval_seconds: float,
) -> str:
    service = _uuid(parent_service_uuid, "service_uuid")
    line = _identifier(service_line, "service_line")
    wait_limit = max(1, int(round(_nonnegative(max_wait_seconds, "max_wait_seconds"))))
    poll = max(1, int(round(_positive(poll_interval_seconds, "poll_interval_seconds"))))

    # The temporary helper intentionally never calls docker compose or Coolify.
    # It uses exact Docker Compose labels to select one existing service-line
    # container, then docker start/restart on that one container only.
    lines = [
        "#!/bin/sh",
        "set +e",
        f"prefix={_single_quote(RUNTIME_PREFIX)}",
        "emit() { echo \"$prefix $*\"; }",
        f"project={_single_quote(service)}",
        f"line={_single_quote(line)}",
        f"max_wait={wait_limit}",
        f"poll_interval={poll}",
        "emit phase=script_start project=$project service_line=$line",
        "ids=$(docker ps -a --filter \"label=com.docker.compose.project=$project\" --filter \"label=com.docker.compose.service=$line\" --format '{{.ID}}')",
        "candidate_count=$(printf '%s\n' \"$ids\" | sed '/^$/d' | wc -l | tr -d ' ')",
        "ids_compact=$(printf '%s' \"$ids\" | tr '\n' ',')",
        "emit phase=candidates candidate_count=$candidate_count candidate_ids=$ids_compact",
        "if [ \"$candidate_count\" != \"1\" ]; then",
        "  emit phase=complete status=failed reason=candidate-count-not-one candidate_count=$candidate_count",
        "  touch /tmp/mother-service-line-restart-done",
        "  exit 20",
        "fi",
        "cid=$(printf '%s\n' \"$ids\" | sed '/^$/d' | head -n 1)",
        "name=$(docker inspect --format '{{.Name}}' \"$cid\" 2>/tmp/mslr-inspect-name.err | sed 's#^/##')",
        "image=$(docker inspect --format '{{.Config.Image}}' \"$cid\" 2>/tmp/mslr-inspect-image.err)",
        "before_status=$(docker inspect --format '{{.State.Status}}' \"$cid\" 2>/tmp/mslr-inspect-status.err)",
        "before_running=$(docker inspect --format '{{.State.Running}}' \"$cid\" 2>/tmp/mslr-inspect-running.err)",
        "before_oom=$(docker inspect --format '{{.State.OOMKilled}}' \"$cid\" 2>/tmp/mslr-inspect-oom.err)",
        "before_health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \"$cid\" 2>/tmp/mslr-inspect-health.err)",
        "inspect_rc=$?",
        "emit phase=selected container_id=$cid container_name=$name image=$image before_status=$before_status before_running=$before_running before_health=$before_health before_oom=$before_oom inspect_rc=$inspect_rc",
        "if [ \"$inspect_rc\" != \"0\" ]; then",
        "  err_b64=$(cat /tmp/mslr-inspect-status.err /tmp/mslr-inspect-running.err /tmp/mslr-inspect-health.err 2>/dev/null | base64 | tr -d '\n')",
        "  emit phase=complete status=failed reason=inspect-failed container_id=$cid stderr_b64=$err_b64",
        "  touch /tmp/mother-service-line-restart-done",
        "  exit 21",
        "fi",
        "if [ \"$before_oom\" = \"true\" ]; then",
        "  emit phase=complete status=failed reason=oom-killed-refused container_id=$cid before_status=$before_status",
        "  touch /tmp/mother-service-line-restart-done",
        "  exit 22",
        "fi",
        "case \"$before_status\" in",
        "  running)",
        "    action=restart",
        "    docker restart \"$cid\" >/tmp/mslr-action.out 2>/tmp/mslr-action.err",
        "    action_rc=$?",
        "    ;;",
        "  created|exited)",
        "    action=start",
        "    docker start \"$cid\" >/tmp/mslr-action.out 2>/tmp/mslr-action.err",
        "    action_rc=$?",
        "    ;;",
        "  *)",
        "    emit phase=complete status=failed reason=unsupported-before-state container_id=$cid before_status=$before_status",
        "    touch /tmp/mother-service-line-restart-done",
        "    exit 23",
        "    ;;",
        "esac",
        "action_stdout_b64=$(cat /tmp/mslr-action.out 2>/dev/null | base64 | tr -d '\n')",
        "action_stderr_b64=$(cat /tmp/mslr-action.err 2>/dev/null | base64 | tr -d '\n')",
        "emit phase=action action=$action action_rc=$action_rc container_id=$cid stdout_b64=$action_stdout_b64 stderr_b64=$action_stderr_b64",
        "if [ \"$action_rc\" != \"0\" ]; then",
        "  emit phase=complete status=failed reason=docker-action-failed action=$action action_rc=$action_rc container_id=$cid stderr_b64=$action_stderr_b64",
        "  touch /tmp/mother-service-line-restart-done",
        "  exit 24",
        "fi",
        "deadline=$(($(date +%s) + max_wait))",
        "while :; do",
        "  after_status=$(docker inspect --format '{{.State.Status}}' \"$cid\" 2>/tmp/mslr-after-status.err)",
        "  after_running=$(docker inspect --format '{{.State.Running}}' \"$cid\" 2>/tmp/mslr-after-running.err)",
        "  after_health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \"$cid\" 2>/tmp/mslr-after-health.err)",
        "  emit phase=poll container_id=$cid after_status=$after_status after_running=$after_running after_health=$after_health",
        "  if [ \"$after_running\" = \"true\" ] && { [ \"$after_health\" = \"healthy\" ] || [ \"$after_health\" = \"none\" ]; }; then",
        "    emit phase=complete status=pass reason=service-line-running action=$action container_id=$cid container_name=$name after_status=$after_status after_running=$after_running after_health=$after_health",
        "    touch /tmp/mother-service-line-restart-done",
        "    exit 0",
        "  fi",
        "  now=$(date +%s)",
        "  if [ \"$now\" -ge \"$deadline\" ]; then",
        "    emit phase=complete status=failed reason=running-health-timeout action=$action container_id=$cid after_status=$after_status after_running=$after_running after_health=$after_health",
        "    touch /tmp/mother-service-line-restart-done",
        "    exit 25",
        "  fi",
        "  sleep \"$poll_interval\"",
        "done",
    ]
    return "\n".join(lines) + "\n"


def _escape_compose_interpolation(text: str) -> str:
    return text.replace("$", "$$")


def _helper_compose(
    *,
    helper_service_name: str,
    parent_service_uuid: str,
    service_line: str,
    max_wait_seconds: float,
    poll_interval_seconds: float,
) -> str:
    name = _identifier(helper_service_name, "helper service name")
    shell = _escape_compose_interpolation(
        _helper_shell_script(
            parent_service_uuid=parent_service_uuid,
            service_line=service_line,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    )
    compose = {
        "services": {
            name: {
                "image": "docker:27-cli",
                "command": ["sh", "-lc", shell],
                "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
                "restart": "no",
                "labels": {
                    "main_computer.mother.component": "service-line-restart-helper",
                    "main_computer.mother.restart_scope": "single-compose-service-line",
                    "main_computer.mother.target-service-uuid": _uuid(parent_service_uuid, "service_uuid"),
                    "main_computer.mother.target-service-line": _identifier(service_line, "service_line"),
                    "main_computer.mother.not_a_validator": "true",
                    "main_computer.mother.not_a_chain_service": "true",
                },
                "healthcheck": {
                    "test": ["CMD-SHELL", "test -f /tmp/mother-service-line-restart-done"],
                    "interval": "5s",
                    "timeout": "2s",
                    "retries": 3,
                    "start_period": "1s",
                },
            }
        }
    }
    rendered = yaml.safe_dump(compose, sort_keys=False)
    parsed = yaml.safe_load(rendered)
    if not isinstance(parsed, Mapping) or "services" not in parsed or name not in parsed["services"]:
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_COMPOSE_INVALID",
            "compiled restart helper Compose is invalid",
        )
    if "$" in rendered.replace("$$", ""):
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_COMPOSE_INVALID",
            "restart helper Compose contains an unescaped dollar interpolation",
        )
    if "docker compose" in shell.lower():
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_COMPOSE_INVALID",
            "restart helper must not invoke docker compose",
        )
    return rendered


def _temporary_service_body(controller_config: Mapping[str, Any], *, network: str, name: str, compose: str) -> dict[str, Any]:
    return {
        "project_uuid": controller_config["project_uuid"],
        "server_uuid": controller_config["server_uuid"],
        "environment_name": _identifier(network, "network"),
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "name": _identifier(name, "helper service name"),
        "description": "Ephemeral Mother restart helper for one Compose service line",
        "instant_deploy": False,
    }


def _helper_service_name(controller_id: str, service_line: str) -> str:
    controller = _identifier(controller_id, "controller_id").replace("_", "-")
    line = _identifier(service_line, "service_line").replace("_", "-")
    return f"{HELPER_PREFIX}-{controller}-{line}-{_stamp().lower()}"[:120]


def _service_status_from_detail(payload: Any, service_uuid: str, service_name: str) -> str:
    target_uuid = _uuid(service_uuid, "helper_service_uuid")
    target_name = _identifier(service_name, "helper_service_name")
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
        elif isinstance(item, list):
            for value in item:
                walk(value)

    walk(payload)
    return candidates[-1] if candidates else ""


def _wait_for_helper_exit(
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
) -> dict[str, Any]:
    service = _uuid(service_uuid, "helper_service_uuid")
    endpoint = f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
    started = time.monotonic()
    observations: list[dict[str, Any]] = []
    statuses: list[str] = []
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
        status = _service_status_from_detail(response.get("payload"), service, service_name) if response["ok"] else ""
        statuses.append(status)
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
                "phase": "restart-helper-exit-wait",
                "service_uuid": service,
                "service_name": service_name,
                "service_status": status,
            }
        )
        elapsed = time.monotonic() - started
        if response["ok"] and status.startswith("exited"):
            return {
                "completed": True,
                "reason": "helper-exited",
                "service_uuid": service,
                "service_name": service_name,
                "final_status": status,
                "observed_statuses": statuses,
                "observation_count": len(observations),
                "wait_milliseconds": int(elapsed * 1000),
                "observations": observations,
            }
        if elapsed >= max_wait_seconds:
            return {
                "completed": False,
                "reason": "helper-exit-timeout",
                "service_uuid": service,
                "service_name": service_name,
                "final_status": status,
                "observed_statuses": statuses,
                "observation_count": len(observations),
                "wait_milliseconds": int(elapsed * 1000),
                "observations": observations,
            }
        if poll_interval_seconds > 0:
            time.sleep(min(poll_interval_seconds, max(0.0, max_wait_seconds - elapsed)))
        else:
            return {
                "completed": False,
                "reason": "helper-exit-not-observed",
                "service_uuid": service,
                "service_name": service_name,
                "final_status": status,
                "observed_statuses": statuses,
                "observation_count": len(observations),
                "wait_milliseconds": int(elapsed * 1000),
                "observations": observations,
            }


def _iter_log_text_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        result: list[str] = []
        for key, child in value.items():
            if str(key).lower() in {"docker_compose_raw", "docker_compose", "compose", "source", "raw"}:
                continue
            result.extend(_iter_log_text_values(child))
        return result
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for child in value:
            result.extend(_iter_log_text_values(child))
        return result
    return []


def _parse_runtime_event_line(line: str) -> dict[str, str] | None:
    if RUNTIME_PREFIX not in line:
        return None
    text = line.split(RUNTIME_PREFIX, 1)[1].strip()
    event: dict[str, str] = {}
    for token in text.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        key = key.strip()
        if key:
            event[key] = value.strip()
    return event if event else None


def _runtime_events_from_payload(payload: object) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for text in _iter_log_text_values(payload):
        for line in text.splitlines():
            event = _parse_runtime_event_line(line)
            if event is not None:
                events.append(event)
    return events


def _collect_helper_logs(
    *,
    controller: CoolifyController,
    controller_id: str,
    helper_service_uuid: str,
    helper_service_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    encoded_service = urllib.parse.quote(_uuid(helper_service_uuid, "helper_service_uuid"), safe="")
    encoded_name = urllib.parse.quote(_identifier(helper_service_name, "helper_service_name"), safe="")
    endpoints = [
        f"/api/v1/services/{encoded_service}/logs?sub_service_name={encoded_name}",
        f"/api/v1/services/{encoded_service}/logs",
    ]
    probes: list[dict[str, Any]] = []
    events: list[dict[str, str]] = []
    for endpoint in endpoints:
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
            probe_events = _runtime_events_from_payload(response.get("payload"))
            probes.append(
                {
                    "method": "GET",
                    "endpoint": endpoint,
                    "status": response["status"],
                    "ok": response["ok"],
                    "response_sha256": response["response_sha256"],
                    "byte_length": response["byte_length"],
                    "elapsed_ms": response["elapsed_ms"],
                    "controller_id": controller_id,
                    "event_count": len(probe_events),
                    "observed": bool(probe_events),
                }
            )
            events.extend(probe_events)
            if probe_events:
                break
        except Exception as exc:  # noqa: BLE001 - diagnostics boundary
            probes.append(
                {
                    "method": "GET",
                    "endpoint": endpoint,
                    "status": None,
                    "ok": False,
                    "controller_id": controller_id,
                    "event_count": 0,
                    "observed": False,
                    "error_code": getattr(exc, "code", type(exc).__name__),
                    "error": str(exc),
                }
            )
    complete_events = [event for event in events if event.get("phase") == "complete"]
    completion = complete_events[-1] if complete_events else None
    return {
        "observed": bool(events),
        "event_count": len(events),
        "events": events,
        "completion": completion,
        "probes": probes,
    }


def _target_service_line_status_readback(
    *,
    controller: CoolifyController,
    controller_id: str,
    service_uuid: str,
    service_line: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    parent_uuid = _uuid(service_uuid, "service_uuid")
    line = _identifier(service_line, "service_line")
    endpoint = f"/api/v1/services/{urllib.parse.quote(parent_uuid, safe='')}"
    response = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )

    candidates: list[dict[str, Any]] = []

    def walk(item: Any, *, path: str) -> None:
        if isinstance(item, Mapping):
            name = item.get("name")
            status = item.get("status")
            uuid = item.get("uuid", item.get("id"))
            if type(name) is str and name.strip() == line and type(status) is str:
                candidates.append(
                    {
                        "path": path,
                        "name": name,
                        "uuid": uuid if type(uuid) in {str, int} else None,
                        "status": status,
                    }
                )
            for key, value in item.items():
                if isinstance(value, (Mapping, list)):
                    walk(value, path=f"{path}.{key}" if path else str(key))
        elif isinstance(item, list):
            for index, value in enumerate(item):
                if isinstance(value, (Mapping, list)):
                    walk(value, path=f"{path}[{index}]")

    if response["ok"]:
        walk(response.get("payload"), path="$")

    statuses = [str(item.get("status") or "") for item in candidates]
    selected = candidates[-1] if candidates else None
    selected_status = str(selected.get("status") or "") if isinstance(selected, Mapping) else ""
    return {
        "method": "GET",
        "endpoint": endpoint,
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "controller_id": controller_id,
        "service_uuid": parent_uuid,
        "service_line": line,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "selected": selected,
        "selected_status": selected_status,
        "observed_statuses": statuses,
        "running_healthy_observed": selected_status.startswith("running:healthy"),
    }


def _completion_from_target_readback(readback: Mapping[str, Any]) -> dict[str, str] | None:
    if readback.get("running_healthy_observed") is not True:
        return None
    return {
        "phase": "complete",
        "status": "pass",
        "reason": "helper-result-inferred-from-target-readback",
        "action": "unknown",
        "after_status": str(readback.get("selected_status") or ""),
        "after_running": "true",
        "after_health": "healthy",
    }


def _wait_for_target_service_line_status_readback(
    *,
    controller: CoolifyController,
    controller_id: str,
    service_uuid: str,
    service_line: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
) -> dict[str, Any]:
    wait_limit = _nonnegative(max_wait_seconds, "max_wait_seconds")
    poll_interval = _nonnegative(poll_interval_seconds, "poll_interval_seconds")
    started = time.monotonic()
    observations: list[dict[str, Any]] = []
    last_readback: dict[str, Any] | None = None

    while True:
        last_readback = _target_service_line_status_readback(
            controller=controller,
            controller_id=controller_id,
            service_uuid=service_uuid,
            service_line=service_line,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        observations.append(last_readback)
        if last_readback.get("running_healthy_observed") is True:
            elapsed = time.monotonic() - started
            return {
                "completed": True,
                "reason": "target-service-line-running-healthy",
                "service_uuid": _uuid(service_uuid, "service_uuid"),
                "service_line": _identifier(service_line, "service_line"),
                "final_status": last_readback.get("selected_status"),
                "running_healthy_observed": True,
                "observation_count": len(observations),
                "wait_milliseconds": int(elapsed * 1000),
                "observations": observations,
                "last_readback": last_readback,
            }

        elapsed = time.monotonic() - started
        if elapsed >= wait_limit:
            return {
                "completed": False,
                "reason": "target-service-line-readback-timeout",
                "service_uuid": _uuid(service_uuid, "service_uuid"),
                "service_line": _identifier(service_line, "service_line"),
                "final_status": last_readback.get("selected_status") if isinstance(last_readback, Mapping) else None,
                "running_healthy_observed": False,
                "observation_count": len(observations),
                "wait_milliseconds": int(elapsed * 1000),
                "observations": observations,
                "last_readback": last_readback,
            }

        time.sleep(min(poll_interval, max(0.0, wait_limit - elapsed)))


def _delete_helper_service(
    *,
    controller: CoolifyController,
    controller_id: str,
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "helper_service_uuid")
    endpoint = f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
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
        "ok": response["ok"] or response["status"] == 404,
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "controller_id": controller_id,
        "service_uuid": service,
        "service_name": service_name,
        "cleanup_scope": "restart-helper-delete-after-exit",
    }


def run_service_line_restart_helper(
    private_state: PrivateStateReadResult,
    *,
    runtime_state_root: str | Path,
    network: str,
    mode: str,
    node: str | None = None,
    display_name: str | None = None,
    controller_id: str | None = None,
    service_uuid: str | None = None,
    service_line: str | None = None,
    delete_helper_after_exit: bool = False,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    max_wait_seconds: float = 60.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    if mode not in {"inspect", "execute"}:
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_INVALID_ARGUMENT",
            "mode must be inspect or execute",
        )
    request_timeout = _positive(timeout, "timeout")
    response_limit = int(max_response_bytes)
    if response_limit <= 0:
        raise MotherServiceLineRestartHelperError(
            "MOTHER_SERVICE_LINE_RESTART_INVALID_ARGUMENT",
            "max_response_bytes must be positive",
        )
    wait_limit = _nonnegative(max_wait_seconds, "max_wait_seconds")
    poll_interval = _nonnegative(poll_interval_seconds, "poll_interval_seconds")

    target = _resolve_service_line_target(
        private_state,
        network=network,
        node=node,
        display_name=display_name,
        controller_id=controller_id,
        service_uuid=service_uuid,
        service_line=service_line,
        timeout=request_timeout,
        max_response_bytes=response_limit,
        opener=opener,
    )
    controller: CoolifyController = target["controller"]
    controller_name = str(target["controller_id"])
    parent_service_uuid = str(target["service_uuid"])
    resolved_service_line = str(target["service_line"])
    helper_name = _helper_service_name(controller_name, resolved_service_line)
    compose = _helper_compose(
        helper_service_name=helper_name,
        parent_service_uuid=parent_service_uuid,
        service_line=resolved_service_line,
        max_wait_seconds=wait_limit,
        poll_interval_seconds=poll_interval or 1.0,
    )
    helper_script = _helper_shell_script(
        parent_service_uuid=parent_service_uuid,
        service_line=resolved_service_line,
        max_wait_seconds=wait_limit,
        poll_interval_seconds=poll_interval or 1.0,
    )

    base_result: dict[str, Any] = {
        "kind": KIND,
        "observed_at": _utc_now(),
        "status": "pass" if mode == "inspect" else "pending",
        "reason": "inspect-only" if mode == "inspect" else None,
        "mode": mode,
        "network": target["network"],
        "node": target["node"],
        "display_name_guard": target["display_name_guard"],
        "controller_id": controller_name,
        "service_uuid": parent_service_uuid,
        "service_line": resolved_service_line,
        "resolution_strategy": target["resolution_strategy"],
        "matched_service_record": target["matched_service_record"],
        "resolution_observations": target["resolution_observations"],
        "helper_service_name": helper_name,
        "helper_service_uuid": None,
        "helper_delete_requested": bool(delete_helper_after_exit),
        "helper_delete_performed": False,
        "helper_left_for_inspection": mode == "execute" and not delete_helper_after_exit,
        "compose_sha256": hashlib.sha256(compose.encode("utf-8")).hexdigest(),
        "helper_script_sha256": hashlib.sha256(helper_script.encode("utf-8")).hexdigest(),
        "runtime_event_prefix": RUNTIME_PREFIX,
        "temporary_service_strategy": (
            "create docker:27-cli service with /var/run/docker.sock; start it; wait for helper exit; "
            "delete only when --delete-helper-after-exit is set"
        ),
        "forbidden_restart_paths": {
            "coolify_parent_restart": False,
            "coolify_application_restart": False,
            "docker_compose_up": False,
        },
    }
    if mode == "inspect":
        return base_result

    controller_config = _controller_config(private_state, network=str(target["network"]), controller_id=controller_name)
    observations: list[dict[str, Any]] = list(target["resolution_observations"])
    helper_service_uuid: str | None = None
    create_receipt: dict[str, Any] | None = None
    start_receipt: dict[str, Any] | None = None
    exit_result: dict[str, Any] | None = None
    log_result: dict[str, Any] | None = None
    delete_receipt: dict[str, Any] | None = None

    try:
        environment_uuid = _resolve_environment_uuid(
            controller=controller,
            controller_id=controller_name,
            endpoint=f"/api/v1/projects/{urllib.parse.quote(str(controller_config['project_uuid']), safe='')}/environments",
            expected_name=str(target["network"]),
            timeout=request_timeout,
            max_response_bytes=response_limit,
            opener=opener,
            observations=observations,
        )
        body = _temporary_service_body(controller_config, network=str(target["network"]), name=helper_name, compose=compose)
        body["environment_uuid"] = environment_uuid
        create_response = _http(
            controller,
            "POST",
            "/api/v1/services",
            body=body,
            timeout=request_timeout,
            max_response_bytes=response_limit,
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
            "controller_id": controller_name,
            "service_name": helper_name,
            "request_body_sha256": hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "restart_scope": "single-compose-service-line",
        }
        observations.append(
            {
                key: create_receipt[key]
                for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms", "controller_id")
            }
            | {"phase": "restart-helper-create"}
        )
        if create_response["ok"] is not True:
            result = dict(base_result)
            result.update(
                {
                    "status": "failed",
                    "reason": "helper-create-failed",
                    "create": create_receipt,
                    "start": None,
                    "completion": None,
                    "logs": None,
                    "delete": None,
                    "observations": observations,
                }
            )
            return result

        helper_service_uuid = _application_uuid(create_response.get("payload"))
        create_receipt["service_uuid"] = helper_service_uuid
        start_endpoint = f"/api/v1/services/{urllib.parse.quote(helper_service_uuid, safe='')}/start"
        start_response = _http(
            controller,
            "POST",
            start_endpoint,
            body=None,
            timeout=request_timeout,
            max_response_bytes=response_limit,
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
            "controller_id": controller_name,
            "service_uuid": helper_service_uuid,
            "service_name": helper_name,
            "restart_scope": "single-compose-service-line",
        }
        observations.append(
            {
                key: start_receipt[key]
                for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms", "controller_id")
            }
            | {"phase": "restart-helper-start"}
        )
        if start_response["ok"] is not True:
            result = dict(base_result)
            result.update(
                {
                    "helper_service_uuid": helper_service_uuid,
                    "status": "failed",
                    "reason": "helper-start-failed",
                    "create": create_receipt,
                    "start": start_receipt,
                    "completion": None,
                    "logs": None,
                    "delete": None,
                    "observations": observations,
                }
            )
            return result

        exit_result = _wait_for_helper_exit(
            controller=controller,
            controller_id=controller_name,
            service_uuid=helper_service_uuid,
            service_name=helper_name,
            timeout=request_timeout,
            max_response_bytes=response_limit,
            max_wait_seconds=wait_limit,
            poll_interval_seconds=poll_interval,
            opener=opener,
        )
        observations.extend(exit_result.get("observations", []))
        log_result = _collect_helper_logs(
            controller=controller,
            controller_id=controller_name,
            helper_service_uuid=helper_service_uuid,
            helper_service_name=helper_name,
            timeout=request_timeout,
            max_response_bytes=response_limit,
            opener=opener,
        )
        observations.extend(log_result.get("probes", []))

        completion = log_result.get("completion") if isinstance(log_result, Mapping) else None
        target_readback = None
        target_readback_wait = None
        result_source = "runtime-logs" if isinstance(completion, Mapping) else None
        if exit_result.get("completed") is True and not isinstance(completion, Mapping):
            target_readback_wait = _wait_for_target_service_line_status_readback(
                controller=controller,
                controller_id=controller_name,
                service_uuid=parent_service_uuid,
                service_line=resolved_service_line,
                timeout=request_timeout,
                max_response_bytes=response_limit,
                max_wait_seconds=wait_limit,
                poll_interval_seconds=poll_interval,
                opener=opener,
            )
            for readback in target_readback_wait.get("observations", []):
                observations.append(
                    {
                        key: readback[key]
                        for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms", "controller_id")
                    }
                    | {
                        "phase": "restart-helper-target-service-line-readback",
                        "service_uuid": parent_service_uuid,
                        "service_line": resolved_service_line,
                        "candidate_count": readback.get("candidate_count"),
                        "selected_status": readback.get("selected_status"),
                        "running_healthy_observed": readback.get("running_healthy_observed"),
                    }
                )

            maybe_readback = target_readback_wait.get("last_readback")
            target_readback = maybe_readback if isinstance(maybe_readback, Mapping) else None
            if isinstance(target_readback, Mapping):
                inferred_completion = _completion_from_target_readback(target_readback)
                if inferred_completion is not None:
                    completion = inferred_completion
                    result_source = "target-service-line-readback"

        helper_passed = (
            exit_result.get("completed") is True
            and isinstance(completion, Mapping)
            and completion.get("status") == "pass"
        )
        reason = (
            str(completion.get("reason"))
            if isinstance(completion, Mapping) and completion.get("reason")
            else str(target_readback_wait.get("reason"))
            if isinstance(target_readback_wait, Mapping) and target_readback_wait.get("reason")
            else "helper-result-not-observed"
            if exit_result.get("completed") is True
            else str(exit_result.get("reason") or "helper-exit-failed")
        )

        result = dict(base_result)
        result.update(
            {
                "helper_service_uuid": helper_service_uuid,
                "status": "pass" if helper_passed else "failed",
                "reason": reason,
                "result_source": result_source,
                "create": create_receipt,
                "start": start_receipt,
                "completion": exit_result,
                "logs": log_result,
                "target_service_line_readback": target_readback,
                "target_service_line_readback_wait": target_readback_wait,
                "selected_container_id": completion.get("container_id") if isinstance(completion, Mapping) else None,
                "selected_container_name": completion.get("container_name") if isinstance(completion, Mapping) else None,
                "action": completion.get("action") if isinstance(completion, Mapping) else None,
                "after_status": completion.get("after_status") if isinstance(completion, Mapping) else None,
                "after_running": completion.get("after_running") if isinstance(completion, Mapping) else None,
                "after_health": completion.get("after_health") if isinstance(completion, Mapping) else None,
                "delete": None,
                "observations": observations,
            }
        )
        return result
    finally:
        # Deletion is intentionally after service exit/log capture.  Manual runs
        # preserve the helper by default for Coolify/Docker log inspection.
        if helper_service_uuid is not None and delete_helper_after_exit:
            try:
                delete_receipt = _delete_helper_service(
                    controller=controller,
                    controller_id=controller_name,
                    service_uuid=helper_service_uuid,
                    service_name=helper_name,
                    timeout=request_timeout,
                    max_response_bytes=response_limit,
                    opener=opener,
                )
                observations.append(
                    {
                        key: delete_receipt[key]
                        for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms", "controller_id")
                    }
                    | {"phase": "restart-helper-delete-after-exit"}
                )
            except Exception as exc:  # noqa: BLE001 - preserve primary result diagnostics
                delete_receipt = {
                    "method": "DELETE",
                    "endpoint": f"/api/v1/services/{urllib.parse.quote(helper_service_uuid, safe='')}",
                    "status": None,
                    "ok": False,
                    "controller_id": controller_name,
                    "service_uuid": helper_service_uuid,
                    "service_name": helper_name,
                    "error_code": getattr(exc, "code", type(exc).__name__),
                    "error": str(exc),
                    "cleanup_scope": "restart-helper-delete-after-exit",
                }

            # The finally block cannot mutate a dict already returned unless we
            # store it before return, so deletion status is reconciled by the
            # wrapper below when execute_with_delete is used via main/tests.
            _LAST_DELETE_RECEIPT[helper_service_uuid] = delete_receipt


_LAST_DELETE_RECEIPT: dict[str, dict[str, Any]] = {}


def execute_service_line_restart_helper(
    private_state: PrivateStateReadResult,
    *,
    runtime_state_root: str | Path,
    network: str,
    mode: str,
    node: str | None = None,
    display_name: str | None = None,
    controller_id: str | None = None,
    service_uuid: str | None = None,
    service_line: str | None = None,
    delete_helper_after_exit: bool = False,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    max_wait_seconds: float = 60.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    before_delete_keys = set(_LAST_DELETE_RECEIPT)
    result = run_service_line_restart_helper(
        private_state,
        runtime_state_root=runtime_state_root,
        network=network,
        mode=mode,
        node=node,
        display_name=display_name,
        controller_id=controller_id,
        service_uuid=service_uuid,
        service_line=service_line,
        delete_helper_after_exit=delete_helper_after_exit,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        max_wait_seconds=max_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        opener=opener,
    )
    helper_uuid = result.get("helper_service_uuid")
    delete_receipt = None
    if isinstance(helper_uuid, str):
        delete_receipt = _LAST_DELETE_RECEIPT.pop(helper_uuid, None)
    for stale in set(_LAST_DELETE_RECEIPT) - before_delete_keys:
        if delete_receipt is None:
            delete_receipt = _LAST_DELETE_RECEIPT.pop(stale, None)
    if delete_helper_after_exit and mode == "execute":
        result = dict(result)
        result["delete"] = delete_receipt
        result["helper_delete_performed"] = bool(delete_receipt and delete_receipt.get("ok") is True)
        result["helper_left_for_inspection"] = not result["helper_delete_performed"]
        if result.get("status") == "pass" and result["helper_delete_performed"] is not True:
            result["status"] = "failed"
            result["reason"] = "helper-delete-failed"
    return result


def _write_evidence(runtime_state_root: str | Path, result: Mapping[str, Any]) -> Path:
    root = Path(runtime_state_root) / "mother" / "evidence" / EVIDENCE_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_stamp()}-{result.get('network', 'unknown')}-{result.get('service_line', 'unknown')}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _load_private_state(runtime_state_root: str | Path, *, network: str, mode: str) -> PrivateStateReadResult:
    paths = MotherPaths(runtime_state_root=runtime_state_root)
    operation = OperationIdentity(
        operation_id=f"service-line-restart-helper-{_stamp()}",
        request_id=f"{mode}-{_stamp()}",
        network=_identifier(network, "network"),
        operation_kind="MOTHER-OP-RESTORE-SERVICE",
    )
    return read_private_state(paths.resolve_private_state_paths(), operation=operation)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a temporary Mother Docker restart helper for exactly one Coolify Compose service line."
    )
    parser.add_argument("mode", choices=("inspect", "execute"))
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--node", help="Mother node key, e.g. mainnetc-super1. Defaults service line to the node key.")
    parser.add_argument("--display-name", help='Human guard, e.g. "Mainnetc Super1"; must normalize to --node when provided.')
    parser.add_argument("--controller-id", help="Diagnostic exact mode: Coolify controller id.")
    parser.add_argument("--service-uuid", help="Diagnostic exact mode: parent Coolify service UUID / Compose project.")
    parser.add_argument("--service-line", help="Exact Compose service line. Defaults to --node.")
    parser.add_argument("--delete-helper-after-exit", action="store_true", help="Delete the temporary helper service after it exits.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    parser.add_argument("--max-wait-seconds", type=float, default=60.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--write-evidence", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        private_state = _load_private_state(args.runtime_state_root, network=args.network, mode=args.mode)
        result = execute_service_line_restart_helper(
            private_state,
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            mode=args.mode,
            node=args.node,
            display_name=args.display_name,
            controller_id=args.controller_id,
            service_uuid=args.service_uuid,
            service_line=args.service_line,
            delete_helper_after_exit=args.delete_helper_after_exit,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
        if args.write_evidence:
            result = dict(result)
            result["evidence_path"] = str(_write_evidence(args.runtime_state_root, result))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") == "pass" else 2
    except (MotherServiceLineRestartHelperError, MotherDeploymentCompletedHelperCleanupError) as exc:
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
