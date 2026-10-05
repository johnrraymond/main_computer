#!/usr/bin/env python3
"""Smoke an in-place Besu sync-mode rewrite on one established Mother node.

This is intentionally a diagnostic mutator, not production topology policy.
It proves whether an existing Coolify service can be restarted with a requested
Besu sync profile while preserving the service row, persistent volume bindings,
node identity, and the rest of the Compose semantics. FULL means sync-min-peers=0;
SNAP means sync-min-peers=2.

The smoke refuses to operate on a Compose that contains a destructive Besu data
reset (for example the temporary add-node replica-sync init that removes
/var/lib/besu/*).  It is meant for established genesis/validator services only.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

import yaml

sys.path.insert(0, os.getcwd())

from tools.mother.common.canonical import canonical_json
from tools.mother.common.coolify_state import (
    _DEFAULT_MAX_RESPONSE_BYTES,
    _DEFAULT_OPENER,
    list_coolify_controllers,
)
from tools.mother.common.deployment_completed_helper_cleanup import _controller_config
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import read_private_state


_SYNC_FLAG_RE = re.compile(r"^--sync-mode=(FULL|SNAP)$", re.IGNORECASE)
_SYNC_MIN_PEERS_FLAG_RE = re.compile(r"^--sync-min-peers=(\d+)$", re.IGNORECASE)
_RUNTIME_SYNC_RE = re.compile(r"#\s*Sync mode:\s*(Full|Snap)\b", re.IGNORECASE)
_RUNTIME_SYNC_MIN_PEERS_RE = re.compile(r"#\s*Sync min peers:\s*(\d+)\b", re.IGNORECASE)
_NODE_ADDRESS_RE = re.compile(r"\bNode address\s+(0x[0-9a-fA-F]{40})\b")
_PRIVATE_KEY_RE = re.compile(r"0x[0-9a-fA-F]{64}")
_TOKEN_RE = re.compile(r"[0-9]+\|[A-Za-z0-9._~-]{16,}")
_DESTRUCTIVE_DATA_PATTERNS = (
    re.compile(r"rm\s+-rf\s+/var/lib/besu(?:/\*|/\.)?", re.IGNORECASE),
    re.compile(r"find\s+/var/lib/besu\b[^\n]*-delete\b", re.IGNORECASE),
)


class SmokeError(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        value = _PRIVATE_KEY_RE.sub("<redacted-private-key>", value)
        value = _TOKEN_RE.sub("<redacted-token>", value)
        if len(value) > 12000:
            return value[:6000] + "\n...<truncated>...\n" + value[-6000:]
        return value
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    return value


def _open(opener: Any, request: urllib.request.Request, timeout: float) -> Any:
    if opener is None:
        return urllib.request.urlopen(request, timeout=timeout)
    if callable(opener):
        return opener(request, timeout=timeout)
    opened = getattr(opener, "open", None)
    if callable(opened):
        return opened(request, timeout=timeout)
    raise TypeError("opener must be callable or expose open(request, timeout=...)")


def _http(
    controller: Any,
    method: str,
    endpoint: str,
    *,
    body: Mapping[str, Any] | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any = _DEFAULT_OPENER,
) -> dict[str, Any]:
    raw_body = canonical_json(dict(body)) if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {controller.api_token}",
        "User-Agent": "main-computer-mother-node-sync-mode-switch-smoke/1",
    }
    if raw_body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        controller.base_url + endpoint,
        data=raw_body,
        headers=headers,
        method=method,
    )
    started = time.monotonic()
    try:
        try:
            response = _open(opener, request, timeout)
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
            close = getattr(response, "close", None)
            if callable(close):
                close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (OSError, urllib.error.URLError) as exc:
        return {
            "method": method,
            "endpoint": endpoint,
            "ok": False,
            "exception": type(exc).__name__,
            "message": str(exc),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    if len(raw) > max_response_bytes:
        return {
            "method": method,
            "endpoint": endpoint,
            "ok": False,
            "exception": "ResponseTooLarge",
            "message": f"response exceeded {max_response_bytes} bytes",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    try:
        payload: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = raw.decode("utf-8", errors="replace")
    return {
        "method": method,
        "endpoint": endpoint,
        "status": status,
        "ok": 200 <= status < 300,
        "payload": _redact(payload),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _records(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            if isinstance(child, (Mapping, list)):
                yield from _records(child)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (Mapping, list)):
                yield from _records(child)


def _compose_text(record: Mapping[str, Any]) -> str:
    for key in ("docker_compose_raw", "dockerComposeRaw", "docker_compose", "dockerCompose", "compose"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if "\n" in text or text.startswith(("name:", "services:", "version:")):
            return text
        try:
            decoded = base64.b64decode(text, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if "services:" in decoded:
            return decoded
    raise SmokeError("Coolify service detail does not expose Compose text")


def _service_status(record: Mapping[str, Any]) -> str:
    status = str(record.get("status") or record.get("human_status") or record.get("state") or "").strip()
    health = str(record.get("health") or record.get("health_status") or "").strip()
    if status and health and health not in status:
        return f"{status}:{health}"
    return status


def _service_uuid(record: Mapping[str, Any]) -> str:
    value = str(record.get("uuid") or record.get("id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,127}", value):
        raise SmokeError("Coolify service UUID is missing or unsafe")
    return value


def _sync_profile(mode: str) -> tuple[str, int]:
    normalized = str(mode or "").strip().upper()
    if normalized not in {"FULL", "SNAP"}:
        raise SmokeError("requested sync mode must be FULL or SNAP")
    return normalized, (0 if normalized == "FULL" else 2)


def _target_service(compose_text: str, node: str) -> tuple[dict[str, Any], dict[str, Any], str, int]:
    try:
        compose = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise SmokeError(f"service Compose is not valid YAML: {exc}") from exc
    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict):
        raise SmokeError("service Compose has no services mapping")
    service = compose["services"].get(node)
    if not isinstance(service, dict):
        raise SmokeError(f"Compose has no exact node service {node!r}")
    command = service.get("command")
    if not isinstance(command, list):
        raise SmokeError(f"node service {node!r} does not use a list command")
    modes: list[str] = []
    min_peers: list[int] = []
    for item in command:
        if not isinstance(item, str):
            continue
        text = item.strip()
        match = _SYNC_FLAG_RE.fullmatch(text)
        if match:
            modes.append(match.group(1).upper())
        peers_match = _SYNC_MIN_PEERS_FLAG_RE.fullmatch(text)
        if peers_match:
            min_peers.append(int(peers_match.group(1)))
    if len(modes) != 1:
        raise SmokeError(f"node service {node!r} must contain exactly one --sync-mode flag")
    if len(min_peers) != 1:
        raise SmokeError(f"node service {node!r} must contain exactly one --sync-min-peers flag")
    return compose, service, modes[0], min_peers[0]


def _assert_no_destructive_data_reset(compose_text: str) -> None:
    for pattern in _DESTRUCTIVE_DATA_PATTERNS:
        if pattern.search(compose_text):
            raise SmokeError(
                "refusing sync-mode rewrite because Compose contains a destructive /var/lib/besu data reset; "
                "this smoke is only for established nodes"
            )


class _LiteralMultilineSafeDumper(yaml.SafeDumper):
    pass


def _represent_string_with_literal_multiline(dumper: yaml.SafeDumper, value: str) -> yaml.nodes.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_LiteralMultilineSafeDumper.add_representer(str, _represent_string_with_literal_multiline)


def _dump_compose_for_coolify(compose: Mapping[str, Any]) -> str:
    return yaml.dump(
        dict(compose),
        Dumper=_LiteralMultilineSafeDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def _is_retired_genesis_init_shim(compose: Mapping[str, Any]) -> bool:
    services = compose.get("services")
    if not isinstance(services, Mapping):
        return False
    genesis_init = services.get("mother-genesis-init")
    if not isinstance(genesis_init, Mapping):
        return False
    command = genesis_init.get("command")
    if isinstance(command, str):
        command_text = command
    elif isinstance(command, list):
        command_text = "\n".join(str(item) for item in command if isinstance(item, str))
    else:
        return False
    return "mother-retired-helper-shim" in command_text and re.search(r"\bwhile\s+true\b", command_text) is not None


def _drop_obsolete_retired_genesis_dependency(compose: Mapping[str, Any], node: str) -> bool:
    """Remove only the impossible completed-successfully edge to a retired infinite shim."""

    if not _is_retired_genesis_init_shim(compose):
        return False
    services = compose.get("services")
    if not isinstance(services, Mapping):
        return False
    service = services.get(node)
    if not isinstance(service, dict):
        return False
    depends_on = service.get("depends_on")
    if not isinstance(depends_on, dict):
        return False
    genesis_dependency = depends_on.get("mother-genesis-init")
    if not isinstance(genesis_dependency, Mapping):
        return False
    if str(genesis_dependency.get("condition") or "").strip() != "service_completed_successfully":
        return False

    del depends_on["mother-genesis-init"]
    if not depends_on:
        service.pop("depends_on", None)
    return True


def _rewrite_sync_mode(compose_text: str, node: str, requested_mode: str) -> tuple[str, str, int]:
    requested, requested_min_peers = _sync_profile(requested_mode)
    _assert_no_destructive_data_reset(compose_text)
    compose, service, current, current_min_peers = _target_service(compose_text, node)
    if current == requested and current_min_peers == requested_min_peers:
        raise SmokeError(
            f"node {node} is already configured for {requested} with --sync-min-peers={requested_min_peers}; "
            "no profile transition would be tested"
        )

    command = service["command"]
    rewritten_command: list[Any] = []
    mode_rewrites = 0
    peers_rewrites = 0
    for item in command:
        if isinstance(item, str) and _SYNC_FLAG_RE.fullmatch(item.strip()):
            rewritten_command.append(f"--sync-mode={requested}")
            mode_rewrites += 1
        elif isinstance(item, str) and _SYNC_MIN_PEERS_FLAG_RE.fullmatch(item.strip()):
            rewritten_command.append(f"--sync-min-peers={requested_min_peers}")
            peers_rewrites += 1
        else:
            rewritten_command.append(item)
    if mode_rewrites != 1 or peers_rewrites != 1:
        raise SmokeError("target node command changed while rewriting sync profile")
    service["command"] = rewritten_command

    # Established Mother services retire the one-shot genesis initializer into an
    # infinite healthy shim.  A stale service_completed_successfully dependency on
    # that shim can never become true after rematerialization, leaving Besu Created
    # forever.  Remove only that exact obsolete edge.
    _drop_obsolete_retired_genesis_dependency(compose, node)

    rewritten = _dump_compose_for_coolify(compose)
    _target_service(rewritten, node)
    return rewritten, current, current_min_peers


def _semantic_masked_sha(compose_text: str, node: str) -> str:
    try:
        compose = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise SmokeError(f"service Compose is not valid YAML: {exc}") from exc
    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict):
        raise SmokeError("service Compose has no services mapping")
    service = compose["services"].get(node)
    if not isinstance(service, dict) or not isinstance(service.get("command"), list):
        raise SmokeError("target node service is missing while computing masked semantic SHA")
    mode_changed = 0
    peers_changed = 0
    masked: list[Any] = []
    for item in service["command"]:
        if isinstance(item, str) and _SYNC_FLAG_RE.fullmatch(item.strip()):
            masked.append("--sync-mode=<PROFILE>")
            mode_changed += 1
        elif isinstance(item, str) and _SYNC_MIN_PEERS_FLAG_RE.fullmatch(item.strip()):
            masked.append("--sync-min-peers=<PROFILE>")
            peers_changed += 1
        else:
            masked.append(item)
    if mode_changed != 1 or peers_changed != 1:
        raise SmokeError("target node service does not contain exactly one sync-mode and sync-min-peers flag")
    service["command"] = masked
    # Normalize the one intentionally removable retired-helper dependency so the
    # semantic guard still rejects every unrelated Compose change.
    _drop_obsolete_retired_genesis_dependency(compose, node)
    return hashlib.sha256(canonical_json(compose)).hexdigest()


def _volume_fingerprint(compose_text: str, node: str) -> dict[str, Any]:
    compose, service, _mode, _min_peers = _target_service(compose_text, node)
    volumes = service.get("volumes")
    if not isinstance(volumes, list):
        raise SmokeError("target node service has no volume list")
    top = compose.get("volumes")
    if not isinstance(top, dict):
        raise SmokeError("Compose has no top-level volumes mapping")
    return {
        "node_volume_mounts": [str(item) for item in volumes],
        "top_level_volume_names": sorted(str(name) for name in top),
        "data_path_mount_present": any(str(item).split(":", 1)[-1].split(":", 1)[0] == "/var/lib/besu" for item in volumes),
        "config_mount_present": any(str(item).split(":", 1)[-1].split(":", 1)[0] == "/config" for item in volumes),
    }


def _flatten_log_strings(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, Mapping):
        preferred = value.get("logs")
        if isinstance(preferred, str):
            out.append(preferred)
        for key, child in value.items():
            if key == "logs":
                continue
            if isinstance(child, (str, Mapping, list)):
                out.extend(_flatten_log_strings(child))
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (str, Mapping, list)):
                out.extend(_flatten_log_strings(child))
    return out


def _runtime_facts_from_logs(log_text: str) -> dict[str, Any]:
    modes = [m.group(1).upper() for m in _RUNTIME_SYNC_RE.finditer(log_text)]
    min_peers = [int(m.group(1)) for m in _RUNTIME_SYNC_MIN_PEERS_RE.finditer(log_text)]
    addresses = [m.group(1).lower() for m in _NODE_ADDRESS_RE.finditer(log_text)]
    heights = [int(value) for value in re.findall(r"\b(?:Produced|Imported)\s+#(\d+)\b", log_text)]
    return {
        "runtime_sync_mode": modes[-1] if modes else None,
        "runtime_sync_min_peers": min_peers[-1] if min_peers else None,
        "node_address": addresses[-1] if addresses else None,
        "highest_logged_block": max(heights) if heights else None,
        "sync_mode_observations": len(modes),
        "sync_min_peers_observations": len(min_peers),
        "node_address_observations": len(addresses),
    }


def _logs_text(
    controller: Any,
    service_uuid: str,
    node: str,
    *,
    lines: int,
    timeout: float,
    max_response_bytes: int,
) -> tuple[str, list[dict[str, Any]]]:
    service_q = urllib.parse.quote(service_uuid, safe="")
    node_q = urllib.parse.quote(node, safe="")
    endpoints = [
        f"/api/v1/services/{service_q}/logs?sub_service_name={node_q}&lines={int(lines)}&show_timestamps=false",
        f"/api/v1/services/{service_q}/logs?lines={int(lines)}",
    ]
    attempts: list[dict[str, Any]] = []
    for endpoint in endpoints:
        receipt = _http(
            controller,
            "GET",
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        strings = _flatten_log_strings(receipt.get("payload")) if receipt.get("ok") is True else []
        text = "\n".join(strings)
        attempts.append(
            {
                "endpoint": endpoint,
                "ok": receipt.get("ok") is True,
                "status": receipt.get("status"),
                "response_sha256": receipt.get("response_sha256"),
                "byte_length": receipt.get("byte_length"),
                "log_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
            }
        )
        if text:
            return text, attempts
    return "", attempts


def _cleanup_items(payload: Any) -> list[Mapping[str, Any]]:
    items = payload
    if isinstance(payload, Mapping):
        for key in ("executions", "items", "data"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, Mapping)]


def _cleanup_ids(receipt: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("uuid"))
        for item in _cleanup_items(receipt.get("payload"))
        if isinstance(item.get("uuid"), str) and item.get("uuid")
    }


def _cleanup_active(receipt: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    terminal = {"completed", "complete", "finished", "success", "successful", "failed", "error", "cancelled", "canceled"}
    active: list[Mapping[str, Any]] = []
    for item in _cleanup_items(receipt.get("payload")):
        if item.get("finished_at"):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status not in terminal:
            active.append(item)
    return active



_CLEANUP_TERMINAL_STATUSES = {
    "completed",
    "complete",
    "finished",
    "success",
    "successful",
    "failed",
    "error",
    "cancelled",
    "canceled",
}


def _cleanup_id(item: Mapping[str, Any]) -> str:
    value = item.get("uuid")
    return value if isinstance(value, str) else ""


def _cleanup_terminal(item: Mapping[str, Any]) -> bool:
    if item.get("finished_at"):
        return True
    return str(item.get("status") or "").strip().lower() in _CLEANUP_TERMINAL_STATUSES


def _cleanup_succeeded(item: Mapping[str, Any]) -> bool:
    return _cleanup_terminal(item) and str(item.get("status") or "").strip().lower() not in {
        "failed",
        "error",
        "cancelled",
        "canceled",
    }


def _cleanup_snapshot(
    controller: Any,
    endpoint: str,
    *,
    timeout: float,
    max_response_bytes: int,
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    receipt = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    )
    if receipt.get("ok") is not True:
        raise SmokeError("could not observe Coolify cleanup execution boundary")
    return receipt, _cleanup_items(receipt.get("payload"))


def _wait_cleanup_clear(
    controller: Any,
    endpoint: str,
    *,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    timeout: float,
    max_response_bytes: int,
) -> set[str]:
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    while True:
        _receipt, items = _cleanup_snapshot(
            controller,
            endpoint,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        if not any(not _cleanup_terminal(item) for item in items):
            return {_cleanup_id(item) for item in items if _cleanup_id(item)}
        if time.monotonic() >= deadline:
            raise SmokeError("Coolify cleanup did not clear before the safe redeploy deadline")
        time.sleep(max(0.0, poll_interval_seconds))


def _wait_cleanup_terminal(
    controller: Any,
    endpoint: str,
    cleanup_uuid: str,
    *,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    timeout: float,
    max_response_bytes: int,
) -> None:
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    while True:
        _receipt, items = _cleanup_snapshot(
            controller,
            endpoint,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        item = next((item for item in items if _cleanup_id(item) == cleanup_uuid), None)
        if item is not None and _cleanup_terminal(item):
            if not _cleanup_succeeded(item):
                raise SmokeError(f"cleanup execution {cleanup_uuid} crossed the redeploy boundary and failed")
            return
        if time.monotonic() >= deadline:
            raise SmokeError(f"cleanup execution {cleanup_uuid} did not finish before the safe redeploy deadline")
        time.sleep(max(0.0, poll_interval_seconds))


def _watch_switch_attempt(
    controller: Any,
    *,
    cleanup_endpoint: str,
    cleanup_baseline_ids: set[str],
    service_uuid: str,
    node: str,
    requested_mode: str,
    requested_min_peers: int,
    poll_seconds: float,
    poll_interval_seconds: float,
    timeout: float,
    log_lines: int,
    max_response_bytes: int,
    attempt: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, poll_seconds)
    observations: list[dict[str, Any]] = []
    final_detail: Mapping[str, Any] | None = None
    final_compose: str | None = None
    final_runtime: dict[str, Any] | None = None
    final_log_attempts: list[dict[str, Any]] = []

    while True:
        detail_error: str | None = None
        configured: str | None = None
        configured_min_peers: int | None = None
        runtime: dict[str, Any] = {}
        service_status = ""
        try:
            detail = _detail(
                controller,
                service_uuid,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
            )
            compose = _compose_text(detail)
            _compose_obj, _service, configured, configured_min_peers = _target_service(compose, node)
            logs, log_attempts = _logs_text(
                controller,
                service_uuid,
                node,
                lines=log_lines,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
            )
            runtime = _runtime_facts_from_logs(logs)
            service_status = _service_status(detail)
            final_detail = detail
            final_compose = compose
            final_runtime = runtime
            final_log_attempts = log_attempts
        except SmokeError as exc:
            detail_error = str(exc)

        try:
            _cleanup_receipt, cleanup_items = _cleanup_snapshot(
                controller,
                cleanup_endpoint,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
            )
        except SmokeError as exc:
            return {
                "status": "cleanup-observation-failed",
                "cleanup_uuid": None,
                "observations": observations,
                "detail": final_detail,
                "compose": final_compose,
                "runtime": final_runtime,
                "log_attempts": final_log_attempts,
                "error": str(exc),
            }

        overlap = next(
            (
                item
                for item in cleanup_items
                if _cleanup_id(item) and _cleanup_id(item) not in cleanup_baseline_ids
            ),
            None,
        )
        cleanup_uuid = _cleanup_id(overlap) if overlap is not None else None
        observation = {
            "attempt": attempt,
            "observed_at": _timestamp(),
            "service_status": service_status or None,
            "configured_sync_mode": configured,
            "configured_sync_min_peers": configured_min_peers,
            "runtime_sync_mode": runtime.get("runtime_sync_mode"),
            "runtime_sync_min_peers": runtime.get("runtime_sync_min_peers"),
            "node_address": runtime.get("node_address"),
            "highest_logged_block": runtime.get("highest_logged_block"),
            "cleanup_boundary_crossed": overlap is not None,
            "cleanup_execution_uuid": cleanup_uuid,
        }
        if detail_error is not None:
            observation["error"] = detail_error
        observations.append(observation)

        # Match Mother's proven cleanup-boundary ordering: observe service state,
        # then sample CleanupDocker, and only then accept running:healthy.
        if overlap is not None:
            return {
                "status": "cleanup-overlap",
                "cleanup_uuid": cleanup_uuid,
                "observations": observations,
                "detail": final_detail,
                "compose": final_compose,
                "runtime": final_runtime,
                "log_attempts": final_log_attempts,
            }

        if (
            detail_error is None
            and service_status == "running:healthy"
            and configured == requested_mode
            and configured_min_peers == requested_min_peers
            and runtime.get("runtime_sync_mode") == requested_mode
            and runtime.get("runtime_sync_min_peers") == requested_min_peers
        ):
            return {
                "status": "healthy",
                "cleanup_uuid": None,
                "observations": observations,
                "detail": final_detail,
                "compose": final_compose,
                "runtime": final_runtime,
                "log_attempts": final_log_attempts,
            }

        if time.monotonic() >= deadline:
            return {
                "status": "not-healthy",
                "cleanup_uuid": None,
                "observations": observations,
                "detail": final_detail,
                "compose": final_compose,
                "runtime": final_runtime,
                "log_attempts": final_log_attempts,
            }
        time.sleep(max(0.0, poll_interval_seconds))


def _deploy_receipt(
    controller: Any,
    service_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
) -> Mapping[str, Any]:
    return _http(
        controller,
        "POST",
        "/api/v1/deploy",
        body={"uuid": service_uuid, "force": True},
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    )


def _cleanup_safe_redeploy(
    controller: Any,
    *,
    cleanup_endpoint: str,
    service_uuid: str,
    node: str,
    requested_mode: str,
    requested_min_peers: int,
    poll_seconds: float,
    poll_interval_seconds: float,
    timeout: float,
    log_lines: int,
    max_response_bytes: int,
) -> dict[str, Any]:
    deploy_receipts: list[dict[str, Any]] = []
    boundary_attempts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    retry_reason: str | None = None

    baseline = _wait_cleanup_clear(
        controller,
        cleanup_endpoint,
        max_wait_seconds=poll_seconds,
        poll_interval_seconds=poll_interval_seconds,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    )
    first_deploy = _deploy_receipt(
        controller,
        service_uuid,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    )
    deploy_receipts.append({"attempt": 1, "baseline_execution_ids": sorted(baseline), **_safe_receipt(first_deploy)})
    if first_deploy.get("ok") is not True:
        return {
            "ok": False,
            "status": "deploy-rejected",
            "deploy_receipts": deploy_receipts,
            "boundary_attempts": boundary_attempts,
            "observations": observations,
            "retry_reason": retry_reason,
            "final": None,
        }

    first = _watch_switch_attempt(
        controller,
        cleanup_endpoint=cleanup_endpoint,
        cleanup_baseline_ids=baseline,
        service_uuid=service_uuid,
        node=node,
        requested_mode=requested_mode,
        requested_min_peers=requested_min_peers,
        poll_seconds=poll_seconds,
        poll_interval_seconds=poll_interval_seconds,
        timeout=timeout,
        log_lines=log_lines,
        max_response_bytes=max_response_bytes,
        attempt=1,
    )
    observations.extend(first["observations"])
    boundary_attempts.append({
        "attempt": 1,
        "status": first["status"],
        "cleanup_uuid": first.get("cleanup_uuid"),
    })
    if first["status"] == "healthy":
        return {
            "ok": True,
            "status": "healthy",
            "deploy_receipts": deploy_receipts,
            "boundary_attempts": boundary_attempts,
            "observations": observations,
            "retry_reason": retry_reason,
            "final": first,
        }
    if first["status"] != "cleanup-overlap":
        return {
            "ok": False,
            "status": first["status"],
            "deploy_receipts": deploy_receipts,
            "boundary_attempts": boundary_attempts,
            "observations": observations,
            "retry_reason": retry_reason,
            "final": first,
        }

    cleanup_uuid = str(first.get("cleanup_uuid") or "")
    if not cleanup_uuid:
        return {
            "ok": False,
            "status": "cleanup-overlap-unidentified",
            "deploy_receipts": deploy_receipts,
            "boundary_attempts": boundary_attempts,
            "observations": observations,
            "retry_reason": "cleanup-boundary-crossed",
            "final": first,
        }

    retry_reason = "cleanup-boundary-crossed"
    _wait_cleanup_terminal(
        controller,
        cleanup_endpoint,
        cleanup_uuid,
        max_wait_seconds=poll_seconds,
        poll_interval_seconds=poll_interval_seconds,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    )
    retry_baseline = _wait_cleanup_clear(
        controller,
        cleanup_endpoint,
        max_wait_seconds=poll_seconds,
        poll_interval_seconds=poll_interval_seconds,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    )
    second_deploy = _deploy_receipt(
        controller,
        service_uuid,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    )
    deploy_receipts.append({"attempt": 2, "baseline_execution_ids": sorted(retry_baseline), **_safe_receipt(second_deploy)})
    if second_deploy.get("ok") is not True:
        return {
            "ok": False,
            "status": "cleanup-retry-deploy-rejected",
            "deploy_receipts": deploy_receipts,
            "boundary_attempts": boundary_attempts,
            "observations": observations,
            "retry_reason": retry_reason,
            "final": first,
        }

    second = _watch_switch_attempt(
        controller,
        cleanup_endpoint=cleanup_endpoint,
        cleanup_baseline_ids=retry_baseline,
        service_uuid=service_uuid,
        node=node,
        requested_mode=requested_mode,
        requested_min_peers=requested_min_peers,
        poll_seconds=poll_seconds,
        poll_interval_seconds=poll_interval_seconds,
        timeout=timeout,
        log_lines=log_lines,
        max_response_bytes=max_response_bytes,
        attempt=2,
    )
    observations.extend(second["observations"])
    boundary_attempts.append({
        "attempt": 2,
        "status": second["status"],
        "cleanup_uuid": second.get("cleanup_uuid"),
    })
    if second["status"] == "cleanup-overlap":
        return {
            "ok": False,
            "status": "cleanup-retry-contaminated",
            "deploy_receipts": deploy_receipts,
            "boundary_attempts": boundary_attempts,
            "observations": observations,
            "retry_reason": retry_reason,
            "final": second,
        }
    return {
        "ok": second["status"] == "healthy",
        "status": "healthy" if second["status"] == "healthy" else second["status"],
        "deploy_receipts": deploy_receipts,
        "boundary_attempts": boundary_attempts,
        "observations": observations,
        "retry_reason": retry_reason,
        "final": second,
    }


def _find_exact_service(
    controllers: list[Any],
    node: str,
    *,
    requested_controller: str | None,
    timeout: float,
    max_response_bytes: int,
    requested_service_uuid: str | None = None,
) -> tuple[Any, Mapping[str, Any], list[dict[str, Any]]]:
    observations: list[dict[str, Any]] = []
    if requested_service_uuid is not None:
        requested_service_uuid = requested_service_uuid.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,127}", requested_service_uuid):
            raise SmokeError("--service-uuid is missing or unsafe")
    matches: dict[tuple[str, str], tuple[Any, Mapping[str, Any]]] = {}
    for controller in controllers:
        if requested_controller and controller.controller_id != requested_controller:
            continue
        receipt = _http(
            controller,
            "GET",
            "/api/v1/services",
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        observations.append(
            {
                "controller_id": controller.controller_id,
                "ok": receipt.get("ok") is True,
                "status": receipt.get("status"),
                "response_sha256": receipt.get("response_sha256"),
            }
        )
        if receipt.get("ok") is not True:
            continue
        for record in _records(receipt.get("payload")):
            if str(record.get("name") or "").strip() != node:
                continue
            raw_uuid = str(record.get("uuid") or record.get("id") or "").strip()
            if not raw_uuid:
                continue
            if requested_service_uuid is not None and raw_uuid != requested_service_uuid:
                continue
            matches[(controller.controller_id, raw_uuid)] = (controller, record)
    if len(matches) != 1:
        rendered = ", ".join(f"{cid}/{uuid}" for cid, uuid in sorted(matches)) or "none"
        if requested_service_uuid is not None:
            raise SmokeError(
                f"expected exactly one Coolify service named {node!r} with UUID {requested_service_uuid!r}; "
                f"found {rendered}"
            )
        raise SmokeError(f"expected exactly one Coolify service named {node!r}; found {rendered}")
    controller, list_record = next(iter(matches.values()))
    return controller, list_record, observations


def _detail(
    controller: Any,
    service_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
) -> Mapping[str, Any]:
    endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
    receipt = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    )
    if receipt.get("ok") is not True or not isinstance(receipt.get("payload"), Mapping):
        raise SmokeError(f"could not read exact Coolify service detail for {service_uuid}")
    return receipt["payload"]


def _safe_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    safe = {
        key: receipt.get(key)
        for key in ("method", "endpoint", "ok", "status", "response_sha256", "byte_length", "elapsed_ms", "exception", "message")
        if receipt.get(key) is not None
    }
    if receipt.get("ok") is not True and receipt.get("payload") is not None:
        safe["payload"] = receipt.get("payload")
    return safe


def _compose_patch_body(compose_text: str) -> dict[str, Any]:
    return {
        "docker_compose_raw": base64.b64encode(compose_text.encode("utf-8")).decode("ascii"),
        "instant_deploy": False,
    }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=(
            "Smoke an in-place Besu sync-profile rewrite for one established Mother node "
            "(FULL=>sync-min-peers=0, SNAP=>sync-min-peers=2). "
            "Without --execute-mutations the script performs preflight only."
        )
    )
    ap.add_argument("node", help="Exact Mother/Coolify node service name, e.g. mainneta-super1")
    ap.add_argument("--network", default="mainnet")
    ap.add_argument("--sync-mode", required=True, choices=["FULL", "SNAP"])
    ap.add_argument("--controller", choices=["coolify-a", "coolify-c"], help="Optional controller disambiguation")
    ap.add_argument(
        "--service-uuid",
        help=(
            "Optional exact Coolify service UUID. Use this to select the live service when stale duplicate "
            "Coolify rows share the same logical node name; the UUID must still belong to the requested node."
        ),
    )
    ap.add_argument("--runtime-state-root", default=str((Path.cwd() / "runtime" / "state").resolve()))
    ap.add_argument("--execute-mutations", action="store_true", help="PATCH the existing service and perform a cleanup-safe redeploy")
    ap.add_argument("--poll-seconds", type=float, default=240.0)
    ap.add_argument("--poll-interval-seconds", type=float, default=5.0)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--log-lines", type=int, default=5000)
    ap.add_argument("--max-response-bytes", type=int, default=_DEFAULT_MAX_RESPONSE_BYTES)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    if args.poll_seconds <= 0 or args.poll_interval_seconds <= 0 or args.timeout <= 0:
        raise SmokeError("poll and timeout values must be positive")
    if args.log_lines < 100:
        raise SmokeError("--log-lines must be at least 100")

    mother_paths = MotherPaths(runtime_state_root=args.runtime_state_root)
    operation = OperationIdentity(
        operation_id=f"node-sync-mode-switch-smoke-{int(time.time())}",
        request_id="manual-node-sync-mode-switch-smoke",
        network=args.network,
        operation_kind="MOTHER-OP-DIAGNOSE",
    )
    private_state = read_private_state(mother_paths.resolve_private_state_paths(), operation=operation)
    controllers = [
        item
        for item in list_coolify_controllers(private_state)
        if item.network == args.network and item.enabled and bool(item.api_token.strip())
    ]
    if not controllers:
        raise SmokeError(f"no enabled authenticated Coolify controllers for network {args.network!r}")

    controller, list_record, discovery = _find_exact_service(
        controllers,
        args.node,
        requested_controller=args.controller,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        requested_service_uuid=args.service_uuid,
    )
    service_uuid = _service_uuid(list_record)
    before_detail = _detail(
        controller,
        service_uuid,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    if str(before_detail.get("name") or "").strip() != args.node:
        raise SmokeError("exact service detail no longer matches requested node")
    before_compose = _compose_text(before_detail)
    _assert_no_destructive_data_reset(before_compose)
    requested_mode, requested_min_peers = _sync_profile(args.sync_mode)
    rewritten_compose, current_mode, current_min_peers = _rewrite_sync_mode(before_compose, args.node, requested_mode)
    before_masked_sha = _semantic_masked_sha(before_compose, args.node)
    intended_masked_sha = _semantic_masked_sha(rewritten_compose, args.node)
    if before_masked_sha != intended_masked_sha:
        raise SmokeError("internal rewrite changed Compose semantics beyond --sync-mode")
    before_volumes = _volume_fingerprint(before_compose, args.node)
    if not (before_volumes["data_path_mount_present"] and before_volumes["config_mount_present"]):
        raise SmokeError("target node does not expose both /var/lib/besu and /config persistent mounts")

    before_logs, before_log_attempts = _logs_text(
        controller,
        service_uuid,
        args.node,
        lines=args.log_lines,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    before_runtime = _runtime_facts_from_logs(before_logs)
    if before_runtime["runtime_sync_mode"] is not None and before_runtime["runtime_sync_mode"] != current_mode:
        raise SmokeError(
            f"preflight runtime/config mismatch: logs say {before_runtime['runtime_sync_mode']} but Compose says {current_mode}"
        )
    if before_runtime["runtime_sync_min_peers"] is not None and before_runtime["runtime_sync_min_peers"] != current_min_peers:
        raise SmokeError(
            "preflight runtime/config mismatch: logs say sync-min-peers="
            f"{before_runtime['runtime_sync_min_peers']} but Compose says {current_min_peers}"
        )

    config = _controller_config(private_state, network=args.network, controller_id=controller.controller_id)
    server_uuid = str(config.get("server_uuid") or "").strip()
    if not server_uuid:
        raise SmokeError("controller has no server_uuid for cleanup-boundary observation")
    cleanup_endpoint = f"/api/v1/servers/{urllib.parse.quote(server_uuid, safe='')}/docker-cleanup/executions"
    cleanup_before = _http(
        controller,
        "GET",
        cleanup_endpoint,
        body=None,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    if cleanup_before.get("ok") is not True:
        raise SmokeError("could not establish Coolify cleanup-execution baseline")
    active_cleanup_before = _cleanup_active(cleanup_before)
    if active_cleanup_before and not args.execute_mutations:
        raise SmokeError("Coolify cleanup is already active; rerun after it finishes so the smoke boundary is uncontaminated")
    if args.execute_mutations:
        cleanup_baseline_ids = _wait_cleanup_clear(
            controller,
            cleanup_endpoint,
            max_wait_seconds=args.poll_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
        )
    else:
        cleanup_baseline_ids = _cleanup_ids(cleanup_before)

    result: dict[str, Any] = {
        "kind": "main_computer.mother.node_sync_mode_switch_smoke.v1",
        "started_at": _timestamp(),
        "network": args.network,
        "node": args.node,
        "controller_id": controller.controller_id,
        "service_uuid": service_uuid,
        "requested_service_uuid": args.service_uuid,
        "requested_sync_mode": args.sync_mode,
        "execute_mutations": bool(args.execute_mutations),
        "discovery": discovery,
        "before": {
            "service_status": _service_status(before_detail),
            "configured_sync_mode": current_mode,
            "configured_sync_min_peers": current_min_peers,
            "runtime": before_runtime,
            "log_attempts": before_log_attempts,
            "compose_sha256": hashlib.sha256(before_compose.encode("utf-8")).hexdigest(),
            "masked_semantic_sha256": before_masked_sha,
            "volumes": before_volumes,
        },
        "intended": {
            "configured_sync_mode": requested_mode,
            "configured_sync_min_peers": requested_min_peers,
            "compose_sha256": hashlib.sha256(rewritten_compose.encode("utf-8")).hexdigest(),
            "masked_semantic_sha256": intended_masked_sha,
        },
        "cleanup_boundary": {
            "server_uuid": server_uuid,
            "endpoint": cleanup_endpoint,
            "baseline_execution_ids": sorted(cleanup_baseline_ids),
            "active_before": bool(active_cleanup_before),
            "policy": "wait-clear -> deploy -> detect overlap -> wait terminal -> one exact redeploy",
            "max_deploy_attempts": 2,
        },
    }

    if not args.execute_mutations:
        result["ok"] = True
        result["status"] = "preflight-only"
        service_uuid_arg = f" --service-uuid {service_uuid}" if args.service_uuid else ""
        result["next_command"] = (
            f"python .\\tools\\mother\\node_sync_mode_switch_smoke.py {args.node} "
            f"--network {args.network}{service_uuid_arg} --sync-mode {args.sync_mode} --execute-mutations"
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    patch_body = _compose_patch_body(rewritten_compose)
    service_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
    patch = _http(
        controller,
        "PATCH",
        service_endpoint,
        body=patch_body,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    result["patch"] = _safe_receipt(patch)
    if patch.get("ok") is not True:
        result["ok"] = False
        result["status"] = "patch-rejected"
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2

    redeploy = _cleanup_safe_redeploy(
        controller,
        cleanup_endpoint=cleanup_endpoint,
        service_uuid=service_uuid,
        node=args.node,
        requested_mode=requested_mode,
        requested_min_peers=requested_min_peers,
        poll_seconds=args.poll_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        timeout=args.timeout,
        log_lines=args.log_lines,
        max_response_bytes=args.max_response_bytes,
    )
    result["deploy_attempts"] = redeploy["deploy_receipts"]
    if redeploy["deploy_receipts"]:
        result["cleanup_boundary"]["baseline_execution_ids"] = list(
            redeploy["deploy_receipts"][0].get("baseline_execution_ids", [])
        )
        result["deploy"] = {
            key: value
            for key, value in redeploy["deploy_receipts"][0].items()
            if key not in {"attempt", "baseline_execution_ids"}
        }
    result["observations"] = redeploy["observations"]
    result["cleanup_boundary"]["attempts"] = redeploy["boundary_attempts"]
    result["cleanup_boundary"]["retry_reason"] = redeploy["retry_reason"]
    result["cleanup_boundary"]["retry_performed"] = len(redeploy["deploy_receipts"]) == 2
    cleanup_crossed = any(
        attempt.get("status") == "cleanup-overlap"
        for attempt in redeploy["boundary_attempts"]
    )
    new_cleanup_ids = {
        str(attempt.get("cleanup_uuid"))
        for attempt in redeploy["boundary_attempts"]
        if attempt.get("cleanup_uuid")
    }
    result["cleanup_boundary"]["crossed"] = cleanup_crossed
    result["cleanup_boundary"]["new_execution_ids"] = sorted(new_cleanup_ids)
    result["cleanup_boundary"]["safe_redeploy_status"] = redeploy["status"]

    final_attempt = redeploy.get("final")
    after_detail = final_attempt.get("detail") if isinstance(final_attempt, Mapping) else None
    after_compose = final_attempt.get("compose") if isinstance(final_attempt, Mapping) else None
    after_runtime = final_attempt.get("runtime") if isinstance(final_attempt, Mapping) else None
    after_log_attempts = final_attempt.get("log_attempts", []) if isinstance(final_attempt, Mapping) else []

    if after_detail is None or after_compose is None or after_runtime is None:
        result["ok"] = False
        result["status"] = str(redeploy.get("status") or "post-deploy-observation-missing")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2

    after_volumes = _volume_fingerprint(after_compose, args.node)
    after_masked_sha = _semantic_masked_sha(after_compose, args.node)
    _compose_obj, _service, after_configured_mode, after_configured_min_peers = _target_service(after_compose, args.node)
    before_address = before_runtime.get("node_address")
    after_address = after_runtime.get("node_address")
    node_address_preserved: bool | None
    if before_address is None:
        node_address_preserved = None
    else:
        node_address_preserved = before_address == after_address

    acceptance = {
        "service_uuid_preserved": _service_uuid(after_detail) == service_uuid,
        "service_name_preserved": str(after_detail.get("name") or "").strip() == args.node,
        "only_sync_profile_semantics_changed": after_masked_sha == before_masked_sha,
        "volume_bindings_preserved": after_volumes == before_volumes,
        "configured_sync_mode_is_requested": after_configured_mode == requested_mode,
        "configured_sync_min_peers_is_requested": after_configured_min_peers == requested_min_peers,
        "runtime_sync_mode_is_requested": after_runtime.get("runtime_sync_mode") == requested_mode,
        "runtime_sync_min_peers_is_requested": after_runtime.get("runtime_sync_min_peers") == requested_min_peers,
        "service_running_healthy": _service_status(after_detail) == "running:healthy",
        "cleanup_boundary_safe_redeploy_proven": redeploy.get("ok") is True,
        "node_address_preserved_when_observable": node_address_preserved is not False,
    }
    result["after"] = {
        "service_status": _service_status(after_detail),
        "configured_sync_mode": after_configured_mode,
        "configured_sync_min_peers": after_configured_min_peers,
        "runtime": after_runtime,
        "log_attempts": after_log_attempts,
        "compose_sha256": hashlib.sha256(after_compose.encode("utf-8")).hexdigest(),
        "masked_semantic_sha256": after_masked_sha,
        "volumes": after_volumes,
    }
    result["acceptance"] = acceptance
    result["identity_proof_strength"] = (
        "runtime-node-address-and-volume-continuity" if before_address is not None else "volume-continuity-only-before-startup-address-not-in-log-window"
    )
    result["ok"] = all(acceptance.values())
    if result["ok"]:
        result["status"] = "switch-proven"
    elif str(redeploy.get("status") or "") not in {"", "healthy"}:
        result["status"] = str(redeploy["status"])
    else:
        result["status"] = "switch-not-proven"
    result["completed_at"] = _timestamp()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "type": type(exc).__name__}, indent=2, sort_keys=True))
        raise SystemExit(2)
