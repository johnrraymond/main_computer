#!/usr/bin/env python3
"""API-only smoke for the Coolify deploy/CleanupDocker materialization race.

This reproducer mirrors the single-node bootstrap control-plane boundary without
Mother topology, validator admission, chain state, SSH, or host Docker access.

The first trial deliberately reproduces the known failure signature:

1. Create a disposable standby service on coolify-a with instant_deploy=false.
2. PATCH it to a five-component, volume-backed compose.
3. Force-deploy it through POST /api/v1/deploy.
4. Trigger Coolify Docker cleanup a few seconds later through the Coolify API.
5. Show that first materialization can fail and an identical later deploy repairs it.

The optional checker trial then exercises the proposed production behavior:

1. Create and PATCH a second disposable service.
2. Arm a bounded cleanup-boundary checker by capturing cleanup execution IDs.
3. Force-deploy while the checker watches service health and cleanup executions.
4. Deliberately inject cleanup during that boundary.
5. Require the checker to mark the attempt contaminated only because a new cleanup
   execution crossed the boundary, then wait for that cleanup to finish.
6. Delete/expire checker #1, arm a fresh checker with a fresh 180-second boundary,
   and issue the same deploy again.
7. Require the second deploy to become running:healthy with no cleanup crossing.

The checker is modeled as a bounded Mother-side API watcher/evidence record. It
does not require a Coolify API token inside a remote helper container. The smoke
is intentionally Coolify-API-only and uses synthetic no-secret workloads.
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
import threading
import time
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.getcwd())

from tools.mother.common.canonical import canonical_json
from tools.mother.common.coolify_state import (
    _DEFAULT_MAX_RESPONSE_BYTES,
    _DEFAULT_OPENER,
    resolve_coolify_controller,
)
from tools.mother.common.deployment_completed_helper_cleanup import (
    MotherDeploymentCompletedHelperCleanupError,
    _application_uuid,
    _controller_config,
    _resolve_environment_uuid,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import read_private_state


_UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_SAFE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_TOKEN_RE = re.compile(r"[0-9]+\|[A-Za-z0-9._~-]{16,}")
_PRIVATE_KEY_RE = re.compile(r"0x[0-9a-fA-F]{64}")


class SmokeError(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp_for_name() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y%m%dt%H%M%S").lower()


def _safe_name(value: str, label: str) -> str:
    if not isinstance(value, str) or _SAFE_NAME_RE.fullmatch(value) is None:
        raise SmokeError(f"{label} must match {_SAFE_NAME_RE.pattern}")
    return value


def _uuid(value: str, label: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise SmokeError(f"{label} is not a safe Coolify uuid")
    return value


def _variant_service_name(base: str, suffix: str) -> str:
    base = _safe_name(base, "base service_name")
    suffix = _safe_name(suffix, "service-name suffix")
    tail = f"-{suffix}"
    max_base = 63 - len(tail)
    trimmed = base[:max_base].rstrip("-")
    if not trimmed:
        raise SmokeError("service-name variant could not be constructed safely")
    return _safe_name(trimmed + tail, "variant service_name")


def _cleanup_execution_items(receipt: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(receipt, Mapping) or receipt.get("ok") is not True:
        return []
    payload = receipt.get("payload")
    items: Any = payload
    if isinstance(payload, Mapping):
        for key in ("executions", "items", "data"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                items = candidate
                break
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, Mapping)]


def _cleanup_execution_ids(receipt: Mapping[str, Any] | None) -> set[str]:
    return {
        str(item.get("uuid"))
        for item in _cleanup_execution_items(receipt)
        if isinstance(item.get("uuid"), str) and item.get("uuid")
    }


def _cleanup_execution_terminal(item: Mapping[str, Any]) -> bool:
    if item.get("finished_at"):
        return True
    status = str(item.get("status") or "").strip().lower()
    return status in {
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


def _cleanup_execution_succeeded(item: Mapping[str, Any]) -> bool:
    if not _cleanup_execution_terminal(item):
        return False
    status = str(item.get("status") or "").strip().lower()
    return status not in {"failed", "error", "cancelled", "canceled"}


def _wait_for_new_cleanup_execution(
    controller: Any,
    cleanup_base: str,
    *,
    baseline_ids: set[str],
    poll_seconds: float,
    poll_interval_seconds: float,
    timeout: float,
    max_response_bytes: int,
) -> tuple[bool, dict[str, Any] | None, list[dict[str, Any]]]:
    deadline = time.monotonic() + max(0.0, poll_seconds)
    observations: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    while True:
        receipt = _http(
            controller,
            "GET",
            cleanup_base + "/executions",
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        items = _cleanup_execution_items(receipt)
        new_items = [
            item
            for item in items
            if isinstance(item.get("uuid"), str) and item.get("uuid") not in baseline_ids
        ]
        if new_items:
            new_items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
            selected = new_items[0]
        observations.append(
            {
                "observed_at": _timestamp(),
                "http": {
                    key: receipt.get(key)
                    for key in ("ok", "status", "response_sha256", "byte_length", "elapsed_ms")
                },
                "new_execution": _redact(selected) if selected is not None else None,
            }
        )
        if selected is not None and _cleanup_execution_terminal(selected):
            return _cleanup_execution_succeeded(selected), selected, observations
        if time.monotonic() >= deadline:
            return False, selected, observations
        time.sleep(max(0.0, poll_interval_seconds))


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
        "User-Agent": "main-computer-coolify-forced-deploy-materialization-smoke/1",
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


def _standby_compose(service_name: str) -> str:
    service_name = _safe_name(service_name, "service_name")
    return "\n".join(
        [
            f"name: {service_name}",
            "",
            "services:",
            f"  {service_name}:",
            "    image: alpine:3.20",
            '    restart: "no"',
            "    command:",
            "      - sh",
            "      - -lc",
            "      - exec tail -f /dev/null",
            "    labels:",
            "      main_computer.mother.smoke.stage: standby",
            f"      main_computer.mother.smoke.service_name: {service_name}",
            "",
        ]
    )


def _bootstrap_shape_compose(service_name: str) -> str:
    """No-secret five-component compose shaped like the first-node bootstrap."""

    service_name = _safe_name(service_name, "service_name")
    label = "main_computer.mother.smoke.coolify_forced_deploy_materialization"
    return f"""name: {service_name}

services:
  mother-genesis-init:
    image: alpine:3.20
    restart: "no"
    command:
      - sh
      - -ec
      - |
        mkdir -p /config /data
        echo smoke-genesis > /config/genesis.json
        echo smoke-node-data > /data/materialized.txt
    volumes:
      - mother-config:/config
      - mother-data:/data
    labels:
      {label}: "true"
      {label}.component: genesis-init

  mother-super-node-fdb:
    image: alpine:3.20
    restart: unless-stopped
    depends_on:
      mother-genesis-init:
        condition: service_completed_successfully
    command:
      - sh
      - -ec
      - |
        mkdir -p /fdb
        while true; do date -u +%s > /fdb/healthy; sleep 2; done
    healthcheck:
      test: ["CMD", "sh", "-ec", "test -s /fdb/healthy"]
      interval: 3s
      timeout: 2s
      retries: 20
      start_period: 2s
    volumes:
      - mother-fdb-data:/fdb
    labels:
      {label}: "true"
      {label}.component: fdb

  {service_name}:
    image: alpine:3.20
    restart: unless-stopped
    depends_on:
      mother-genesis-init:
        condition: service_completed_successfully
    command:
      - sh
      - -ec
      - |
        test -s /config/genesis.json
        while true; do date -u +%s > /data/chain-healthy; sleep 2; done
    healthcheck:
      test: ["CMD", "sh", "-ec", "test -s /config/genesis.json && test -s /data/chain-healthy"]
      interval: 3s
      timeout: 2s
      retries: 20
      start_period: 2s
    volumes:
      - mother-config:/config:ro
      - mother-data:/data
    labels:
      {label}: "true"
      {label}.component: primary

  mother-super-node-hub:
    image: python:3.12-alpine
    restart: unless-stopped
    depends_on:
      mother-super-node-fdb:
        condition: service_healthy
      {service_name}:
        condition: service_healthy
    command:
      - python
      - -u
      - -c
      - |
        import http.server, json, time
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt, *args): pass
            def do_GET(self):
                body = json.dumps({{"ok": True, "time": time.time()}}, sort_keys=True).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        http.server.ThreadingHTTPServer(("0.0.0.0", 8790), Handler).serve_forever()
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - import urllib.request; urllib.request.urlopen("http://127.0.0.1:8790/health", timeout=2).read()
      interval: 3s
      timeout: 2s
      retries: 20
      start_period: 2s
    labels:
      {label}: "true"
      {label}.component: hub

  mother-genesis-proof-guardian:
    image: python:3.12-alpine
    restart: unless-stopped
    depends_on:
      mother-super-node-hub:
        condition: service_healthy
    command:
      - python
      - -u
      - -c
      - |
        import os, time, urllib.request
        os.makedirs("/data", exist_ok=True)
        while True:
            urllib.request.urlopen("http://mother-super-node-hub:8790/health", timeout=2).read()
            with open("/data/guardian-healthy", "w", encoding="utf-8") as handle:
                handle.write(str(int(time.time())))
            time.sleep(2)
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - import os,time; p="/data/guardian-healthy"; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 20
      interval: 3s
      timeout: 2s
      retries: 20
      start_period: 2s
    volumes:
      - mother-data:/data
    labels:
      {label}: "true"
      {label}.component: guardian

volumes:
  mother-config:
  mother-data:
  mother-fdb-data:
"""


def _service_row_body(
    controller_config: Mapping[str, Any],
    *,
    network: str,
    environment_uuid: str,
    service_name: str,
    compose: str,
) -> dict[str, Any]:
    return {
        "project_uuid": controller_config["project_uuid"],
        "server_uuid": controller_config["server_uuid"],
        "environment_name": _safe_name(network, "network"),
        "environment_uuid": _uuid(environment_uuid, "environment_uuid"),
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "name": _safe_name(service_name, "service_name"),
        "description": "Disposable API-only smoke for Coolify standby->PATCH->forced-deploy materialization boundary.",
        "instant_deploy": False,
    }


def _status_snapshot(payload: Any, *, service_uuid: str, service_name: str) -> dict[str, Any]:
    records: list[dict[str, Any]] = []

    def walk(value: Any, path: str = "$") -> None:
        if isinstance(value, Mapping):
            name = value.get("name") or value.get("service_name") or value.get("serviceName")
            uuid = value.get("uuid") or value.get("service_uuid") or value.get("application_uuid")
            status = value.get("status") or value.get("state")
            image = value.get("image") or value.get("docker_image")
            if (
                uuid == service_uuid
                or name == service_name
                or name in {
                    "mother-genesis-init",
                    "mother-super-node-fdb",
                    "mother-super-node-hub",
                    "mother-genesis-proof-guardian",
                }
            ):
                records.append(
                    {
                        "path": path,
                        "name": name,
                        "uuid": uuid,
                        "status": status,
                        "image": image,
                    }
                )
            for key, child in value.items():
                if isinstance(child, (Mapping, list)):
                    walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(payload)
    parent = next((r for r in records if r.get("uuid") == service_uuid), None)
    parent_status = str((parent or {}).get("status") or "")
    return {
        "parent_status": parent_status,
        "running_healthy": parent_status == "running:healthy",
        "records": records,
    }


def _detail_snapshot(
    controller: Any,
    service_uuid: str,
    service_name: str,
    *,
    label: str,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
    receipt = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    )
    snapshot = {
        "label": label,
        "observed_at": _timestamp(),
        "endpoint": endpoint,
        "http": {key: receipt.get(key) for key in ("ok", "status", "response_sha256", "byte_length", "elapsed_ms")},
    }
    if receipt.get("ok") is True:
        snapshot.update(_status_snapshot(receipt.get("payload"), service_uuid=service_uuid, service_name=service_name))
    else:
        snapshot["error"] = _redact(receipt)
    return snapshot


def _poll_until_healthy(
    controller: Any,
    service_uuid: str,
    service_name: str,
    *,
    phase: str,
    poll_seconds: float,
    poll_interval_seconds: float,
    timeout: float,
    max_response_bytes: int,
) -> tuple[bool, list[dict[str, Any]]]:
    deadline = time.monotonic() + max(0.0, poll_seconds)
    observations: list[dict[str, Any]] = []
    while True:
        snap = _detail_snapshot(
            controller,
            service_uuid,
            service_name,
            label=f"{phase}-poll",
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        observations.append(snap)
        if snap.get("running_healthy") is True:
            return True, observations
        if time.monotonic() >= deadline:
            return False, observations
        time.sleep(max(0.0, poll_interval_seconds))

def _active_cleanup_execution_items(receipt: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return [item for item in _cleanup_execution_items(receipt) if not _cleanup_execution_terminal(item)]


def _watch_materialization_boundary(
    controller: Any,
    cleanup_base: str,
    *,
    service_uuid: str,
    service_name: str,
    baseline_cleanup_ids: set[str],
    checker_id: str,
    checker_ttl_seconds: float,
    poll_interval_seconds: float,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    """Watch one deploy boundary until healthy, cleanup overlap, or TTL expiry."""

    deadline = time.monotonic() + max(0.0, checker_ttl_seconds)
    observations: list[dict[str, Any]] = []
    while True:
        detail = _detail_snapshot(
            controller,
            service_uuid,
            service_name,
            label=f"{checker_id}-service",
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        cleanup_receipt = _http(
            controller,
            "GET",
            cleanup_base + "/executions",
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        new_cleanup_items = [
            item
            for item in _cleanup_execution_items(cleanup_receipt)
            if isinstance(item.get("uuid"), str)
            and item.get("uuid")
            and item.get("uuid") not in baseline_cleanup_ids
        ]
        new_cleanup_items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        overlap = new_cleanup_items[0] if new_cleanup_items else None
        observations.append(
            {
                "observed_at": _timestamp(),
                "service": detail,
                "cleanup_http": {
                    key: cleanup_receipt.get(key)
                    for key in ("ok", "status", "response_sha256", "byte_length", "elapsed_ms")
                },
                "new_cleanup_execution": _redact(overlap) if overlap is not None else None,
            }
        )

        # Be conservative: if a new cleanup execution is visible in the same poll
        # that health becomes visible, the boundary is treated as contaminated.
        if overlap is not None:
            return {
                "status": "cleanup-overlap",
                "checker_id": checker_id,
                "cleanup_execution": _redact(overlap),
                "observations": observations,
            }
        if detail.get("running_healthy") is True:
            return {
                "status": "healthy",
                "checker_id": checker_id,
                "cleanup_execution": None,
                "observations": observations,
            }
        if time.monotonic() >= deadline:
            return {
                "status": "expired",
                "checker_id": checker_id,
                "cleanup_execution": None,
                "observations": observations,
            }
        time.sleep(max(0.0, poll_interval_seconds))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="API-only live smoke for the Coolify deploy/CleanupDocker materialization race."
    )
    ap.add_argument("--runtime-state-root", default="runtime/state")
    ap.add_argument("--network", default="mainnet")
    ap.add_argument(
        "--controller",
        default="coolify-a",
        help="Coolify controller from Mother private state; this reproducer is scoped to coolify-a.",
    )
    ap.add_argument("--service-name", default="", help="Disposable service name; default is generated.")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--max-response-bytes", type=int, default=_DEFAULT_MAX_RESPONSE_BYTES)
    ap.add_argument("--poll-seconds", type=float, default=180.0)
    ap.add_argument("--poll-interval-seconds", type=float, default=5.0)
    ap.add_argument(
        "--second-deploy-if-first-not-healthy",
        action="store_true",
        help="Issue one identical API forced deploy after the raw race trial fails, then poll again.",
    )
    ap.add_argument(
        "--trigger-docker-cleanup-after-seconds",
        type=float,
        default=None,
        help=(
            "After deploy acceptance, wait this many seconds and trigger Coolify server Docker cleanup through "
            "the API. Unused-volume/network deletion is disabled for this controlled collision probe."
        ),
    )
    ap.add_argument(
        "--prove-cleanup-checker-retry",
        "--prove-cleanup-serialization-fix",
        dest="prove_cleanup_checker_retry",
        action="store_true",
        help=(
            "Run a second disposable-service trial using the proposed bounded cleanup-boundary checker: arm, "
            "deploy, inject cleanup, detect contamination, wait cleanup terminal, arm a fresh checker, and "
            "redeploy the same saved service. The old --prove-cleanup-serialization-fix spelling remains an alias."
        ),
    )
    ap.add_argument(
        "--checker-ttl-seconds",
        type=float,
        default=180.0,
        help="Lifetime of each cleanup-boundary checker before it expires and must be replaced.",
    )
    ap.add_argument(
        "--cleanup-wait-seconds",
        type=float,
        default=180.0,
        help="Maximum API-only wait for the cleanup that contaminated checker attempt #1 to finish.",
    )
    ap.add_argument(
        "--cleanup-poll-interval-seconds",
        type=float,
        default=2.0,
        help="Polling interval for cleanup executions and checker boundary state.",
    )
    ap.add_argument(
        "--delete-service-row-after-smoke",
        action="store_true",
        help="Delete disposable service rows through the Coolify API before exiting.",
    )
    args = ap.parse_args()

    if args.controller != "coolify-a":
        raise SmokeError("this reproducer is intentionally scoped to controller coolify-a")
    if args.trigger_docker_cleanup_after_seconds is not None and args.trigger_docker_cleanup_after_seconds < 0:
        raise SmokeError("--trigger-docker-cleanup-after-seconds must be >= 0")
    if args.checker_ttl_seconds <= 0:
        raise SmokeError("--checker-ttl-seconds must be > 0")
    if args.prove_cleanup_checker_retry and args.trigger_docker_cleanup_after_seconds is None:
        raise SmokeError(
            "--prove-cleanup-checker-retry requires --trigger-docker-cleanup-after-seconds so the same run "
            "contains both a deliberate race and the checker-controlled retry proof"
        )

    service_name = args.service_name or f"mother-forced-deploy-smoke-{_stamp_for_name()}"
    service_name = _safe_name(service_name, "service_name")

    mother_paths = MotherPaths(runtime_state_root=args.runtime_state_root)
    operation = OperationIdentity(
        operation_id=f"coolify-forced-deploy-materialization-smoke-{int(time.time())}",
        request_id="manual-coolify-forced-deploy-materialization-smoke",
        network=args.network,
        operation_kind="MOTHER-OP-DIAGNOSE",
    )
    private_state = read_private_state(mother_paths.resolve_private_state_paths(), operation=operation)
    controller = resolve_coolify_controller(
        private_state,
        args.network,
        args.controller,
        require_enabled=True,
        require_token=True,
    )
    controller_config = _controller_config(
        private_state,
        network=args.network,
        controller_id=args.controller,
    )
    server_uuid = _uuid(str(controller_config["server_uuid"]), "server_uuid")
    cleanup_base = f"/api/v1/servers/{urllib.parse.quote(server_uuid, safe='')}/docker-cleanup"

    environment_observations: list[dict[str, Any]] = []
    try:
        environment_uuid = _resolve_environment_uuid(
            controller=controller,
            controller_id=args.controller,
            endpoint=f"/api/v1/projects/{urllib.parse.quote(str(controller_config['project_uuid']), safe='')}/environments",
            expected_name=args.network,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            opener=_DEFAULT_OPENER,
            observations=environment_observations,
        )
    except MotherDeploymentCompletedHelperCleanupError as exc:
        raise SmokeError(str(exc)) from exc

    standby_compose = _standby_compose(service_name)
    bootstrap_compose = _bootstrap_shape_compose(service_name)
    create_body = _service_row_body(
        controller_config,
        network=args.network,
        environment_uuid=environment_uuid,
        service_name=service_name,
        compose=standby_compose,
    )

    result: dict[str, Any] = {
        "kind": "main_computer.mother.coolify_forced_deploy_materialization_smoke.v2",
        "started_at": _timestamp(),
        "network": args.network,
        "controller_id": args.controller,
        "server_uuid": server_uuid,
        "transport": "coolify-api-only",
        "ssh_used": False,
        "service_name": service_name,
        "standby_compose_sha256": hashlib.sha256(standby_compose.encode("utf-8")).hexdigest(),
        "bootstrap_shape_compose_sha256": hashlib.sha256(bootstrap_compose.encode("utf-8")).hexdigest(),
        "environment_resolution": environment_observations,
        "snapshots": [],
    }

    service_uuid: str | None = None
    checker_service_uuid: str | None = None
    delete_receipt: dict[str, Any] | None = None
    checker_delete_receipt: dict[str, Any] | None = None

    try:
        # ------------------------------------------------------------------
        # Trial A: raw deploy + deliberate cleanup collision.
        # ------------------------------------------------------------------
        create = _http(
            controller,
            "POST",
            "/api/v1/services",
            body=create_body,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
        )
        result["create"] = create
        if create.get("ok") is not True:
            raise SmokeError("Coolify rejected disposable standby service creation")
        try:
            service_uuid = _application_uuid(create.get("payload"))
        except MotherDeploymentCompletedHelperCleanupError as exc:
            raise SmokeError(f"could not bind created service UUID: {exc}") from exc
        service_uuid = _uuid(service_uuid, "created service UUID")
        result["service_uuid"] = service_uuid

        result["snapshots"].append(
            _detail_snapshot(
                controller,
                service_uuid,
                service_name,
                label="after-create-standby",
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
        )

        patch_body = {
            "name": service_name,
            "docker_compose_raw": base64.b64encode(bootstrap_compose.encode("utf-8")).decode("ascii"),
        }
        patch_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
        patch = _http(
            controller,
            "PATCH",
            patch_endpoint,
            body=patch_body,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
        )
        result["patch"] = patch
        if patch.get("ok") is not True:
            raise SmokeError("Coolify rejected bootstrap-shape compose PATCH")

        result["snapshots"].append(
            _detail_snapshot(
                controller,
                service_uuid,
                service_name,
                label="after-patch-before-deploy",
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
        )

        deploy_body = {"uuid": service_uuid, "force": True}
        first_deploy = _http(
            controller,
            "POST",
            "/api/v1/deploy",
            body=deploy_body,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
        )
        result["first_deploy"] = first_deploy
        result["first_deploy_requested_at"] = _timestamp()
        if first_deploy.get("ok") is not True:
            raise SmokeError("Coolify rejected first forced deploy")

        collision_accepted = False
        if args.trigger_docker_cleanup_after_seconds is not None:
            cleanup_settings = _http(
                controller,
                "GET",
                cleanup_base,
                body=None,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            cleanup_executions_before = _http(
                controller,
                "GET",
                cleanup_base + "/executions",
                body=None,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            if args.trigger_docker_cleanup_after_seconds:
                time.sleep(args.trigger_docker_cleanup_after_seconds)
            cleanup_receipt = _http(
                controller,
                "POST",
                cleanup_base + "/run",
                body={"delete_unused_volumes": False, "delete_unused_networks": False},
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            collision_accepted = cleanup_receipt.get("ok") is True
            result["docker_cleanup_collision"] = {
                "requested_after_seconds": args.trigger_docker_cleanup_after_seconds,
                "requested_at": _timestamp(),
                "server_uuid": server_uuid,
                "safe_delete_unused_volumes": False,
                "safe_delete_unused_networks": False,
                "settings_before": cleanup_settings,
                "executions_before": cleanup_executions_before,
                "run_receipt": cleanup_receipt,
            }

        first_healthy, first_polls = _poll_until_healthy(
            controller,
            service_uuid,
            service_name,
            phase="first-deploy",
            poll_seconds=args.poll_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
        )
        result["first_deploy_healthy"] = first_healthy
        result["first_deploy_polls"] = first_polls
        if args.trigger_docker_cleanup_after_seconds is not None:
            result["docker_cleanup_collision"]["executions_after_first_poll_window"] = _http(
                controller,
                "GET",
                cleanup_base + "/executions",
                body=None,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )

        second_healthy: bool | None = None
        if not first_healthy and args.second_deploy_if_first_not_healthy:
            second_deploy = _http(
                controller,
                "POST",
                "/api/v1/deploy",
                body=deploy_body,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            result["second_deploy"] = second_deploy
            result["second_deploy_requested_at"] = _timestamp()
            if second_deploy.get("ok") is True:
                second_healthy, second_polls = _poll_until_healthy(
                    controller,
                    service_uuid,
                    service_name,
                    phase="second-deploy",
                    poll_seconds=args.poll_seconds,
                    poll_interval_seconds=args.poll_interval_seconds,
                    timeout=args.timeout,
                    max_response_bytes=args.max_response_bytes,
                )
                result["second_deploy_polls"] = second_polls
            else:
                second_healthy = False
        result["second_deploy_healthy"] = second_healthy

        # ------------------------------------------------------------------
        # Trial B: exact proposed checker/retry behavior.
        # ------------------------------------------------------------------
        checker_first_overlap_detected: bool | None = None
        checker_first_marked_contaminated: bool | None = None
        checker_overlap_completed: bool | None = None
        checker_second_overlap_detected: bool | None = None
        checker_second_healthy: bool | None = None
        checker_retry_reason: str | None = None
        checker_proven: bool | None = None

        if args.prove_cleanup_checker_retry:
            checker_name = _variant_service_name(service_name, "checker")
            checker_standby = _standby_compose(checker_name)
            checker_bootstrap = _bootstrap_shape_compose(checker_name)
            checker_trial: dict[str, Any] = {
                "service_name": checker_name,
                "strategy": (
                    "arm cleanup-boundary checker -> deploy -> inject cleanup -> detect contamination -> "
                    "wait cleanup terminal -> delete checker -> arm fresh checker -> redeploy same service"
                ),
                "checker_ttl_seconds": args.checker_ttl_seconds,
                "snapshots": [],
            }
            result["checker_retry_trial"] = checker_trial

            checker_create = _http(
                controller,
                "POST",
                "/api/v1/services",
                body=_service_row_body(
                    controller_config,
                    network=args.network,
                    environment_uuid=environment_uuid,
                    service_name=checker_name,
                    compose=checker_standby,
                ),
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            checker_trial["create"] = checker_create
            if checker_create.get("ok") is not True:
                raise SmokeError("Coolify rejected checker-trial standby service creation")
            try:
                checker_service_uuid = _application_uuid(checker_create.get("payload"))
            except MotherDeploymentCompletedHelperCleanupError as exc:
                raise SmokeError(f"could not bind checker-trial service UUID: {exc}") from exc
            checker_service_uuid = _uuid(checker_service_uuid, "checker-trial service UUID")
            checker_trial["service_uuid"] = checker_service_uuid

            checker_patch = _http(
                controller,
                "PATCH",
                f"/api/v1/services/{urllib.parse.quote(checker_service_uuid, safe='')}",
                body={
                    "name": checker_name,
                    "docker_compose_raw": base64.b64encode(checker_bootstrap.encode("utf-8")).decode("ascii"),
                },
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            checker_trial["patch"] = checker_patch
            if checker_patch.get("ok") is not True:
                raise SmokeError("Coolify rejected checker-trial bootstrap-shape compose PATCH")

            # Arm checker #1 only when no cleanup is already active.
            checker1_baseline = _http(
                controller,
                "GET",
                cleanup_base + "/executions",
                body=None,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            checker1_active_before = _active_cleanup_execution_items(checker1_baseline)
            if checker1_active_before:
                raise SmokeError(
                    "checker trial started while a cleanup execution was already active; rerun after it is terminal"
                )
            checker1_ids = _cleanup_execution_ids(checker1_baseline)
            checker1_id = f"cleanup-boundary-checker-1-{int(time.time())}"
            checker1 = {
                "checker_id": checker1_id,
                "armed_at": _timestamp(),
                "ttl_seconds": args.checker_ttl_seconds,
                "cleanup_baseline_ids": sorted(checker1_ids),
                "active_cleanup_at_arm": [],
            }
            checker_trial["checker_first_attempt"] = checker1

            checker_first_deploy = _http(
                controller,
                "POST",
                "/api/v1/deploy",
                body={"uuid": checker_service_uuid, "force": True},
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            checker1["deploy"] = checker_first_deploy
            checker1["deploy_requested_at"] = _timestamp()
            if checker_first_deploy.get("ok") is not True:
                raise SmokeError("Coolify rejected checker-trial first forced deploy")

            injection: dict[str, Any] = {}

            def inject_cleanup() -> None:
                try:
                    delay = float(args.trigger_docker_cleanup_after_seconds or 0.0)
                    if delay:
                        time.sleep(delay)
                    injection["requested_at"] = _timestamp()
                    injection["receipt"] = _http(
                        controller,
                        "POST",
                        cleanup_base + "/run",
                        body={"delete_unused_volumes": False, "delete_unused_networks": False},
                        timeout=args.timeout,
                        max_response_bytes=args.max_response_bytes,
                    )
                except Exception as exc:  # diagnostic thread; surface explicitly below
                    injection["exception"] = f"{type(exc).__name__}: {exc}"

            cleanup_thread = threading.Thread(target=inject_cleanup, name="mother-smoke-cleanup-injector")
            cleanup_thread.start()
            checker1_result = _watch_materialization_boundary(
                controller,
                cleanup_base,
                service_uuid=checker_service_uuid,
                service_name=checker_name,
                baseline_cleanup_ids=checker1_ids,
                checker_id=checker1_id,
                checker_ttl_seconds=args.checker_ttl_seconds,
                poll_interval_seconds=args.cleanup_poll_interval_seconds,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            cleanup_thread.join()
            checker1["cleanup_injection"] = injection
            checker1["result"] = checker1_result
            checker1["deleted_at"] = _timestamp()
            checker1["delete_reason"] = (
                "cleanup-boundary-crossed"
                if checker1_result.get("status") == "cleanup-overlap"
                else "checker-finished"
            )

            checker_first_overlap_detected = checker1_result.get("status") == "cleanup-overlap"
            checker_first_marked_contaminated = checker_first_overlap_detected
            checker_retry_reason = "cleanup-boundary-crossed" if checker_first_overlap_detected else None
            if not checker_first_overlap_detected:
                raise SmokeError(
                    "checker trial did not observe the deliberately injected cleanup crossing the first deploy boundary"
                )

            injected_receipt = injection.get("receipt")
            if not isinstance(injected_receipt, Mapping) or injected_receipt.get("ok") is not True:
                raise SmokeError("checker trial cleanup injection was not accepted by Coolify")

            (
                checker_overlap_completed,
                checker_overlap_execution,
                checker_overlap_polls,
            ) = _wait_for_new_cleanup_execution(
                controller,
                cleanup_base,
                baseline_ids=checker1_ids,
                poll_seconds=args.cleanup_wait_seconds,
                poll_interval_seconds=args.cleanup_poll_interval_seconds,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            checker1["overlap_completion"] = {
                "completed_successfully": checker_overlap_completed,
                "execution": _redact(checker_overlap_execution),
                "polls": checker_overlap_polls,
            }
            if not checker_overlap_completed:
                raise SmokeError("cleanup that contaminated checker attempt #1 did not finish successfully")

            # Arm checker #2 from fresh state. This is the production retry boundary.
            checker2_baseline = _http(
                controller,
                "GET",
                cleanup_base + "/executions",
                body=None,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            checker2_active_before = _active_cleanup_execution_items(checker2_baseline)
            if checker2_active_before:
                raise SmokeError("fresh checker could not arm because cleanup was still active")
            checker2_ids = _cleanup_execution_ids(checker2_baseline)
            checker2_id = f"cleanup-boundary-checker-2-{int(time.time())}"
            checker2 = {
                "checker_id": checker2_id,
                "armed_at": _timestamp(),
                "ttl_seconds": args.checker_ttl_seconds,
                "cleanup_baseline_ids": sorted(checker2_ids),
                "active_cleanup_at_arm": [],
            }
            checker_trial["checker_second_attempt"] = checker2

            checker_second_deploy = _http(
                controller,
                "POST",
                "/api/v1/deploy",
                body={"uuid": checker_service_uuid, "force": True},
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            checker2["deploy"] = checker_second_deploy
            checker2["deploy_requested_at"] = _timestamp()
            if checker_second_deploy.get("ok") is not True:
                raise SmokeError("Coolify rejected checker-trial second forced deploy")

            checker2_result = _watch_materialization_boundary(
                controller,
                cleanup_base,
                service_uuid=checker_service_uuid,
                service_name=checker_name,
                baseline_cleanup_ids=checker2_ids,
                checker_id=checker2_id,
                checker_ttl_seconds=args.checker_ttl_seconds,
                poll_interval_seconds=args.cleanup_poll_interval_seconds,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
            checker2["result"] = checker2_result
            checker2["deleted_at"] = _timestamp()
            checker2["delete_reason"] = "healthy" if checker2_result.get("status") == "healthy" else "checker-finished"

            checker_second_overlap_detected = checker2_result.get("status") == "cleanup-overlap"
            checker_second_healthy = checker2_result.get("status") == "healthy"
            checker_proven = (
                checker_first_overlap_detected
                and checker_first_marked_contaminated
                and checker_overlap_completed
                and not checker_second_overlap_detected
                and checker_second_healthy
            )

        race_reproduced = first_deploy.get("ok") is True and not first_healthy and collision_accepted
        result["diagnosis"] = {
            "first_deploy_accepted": first_deploy.get("ok") is True,
            "first_deploy_became_healthy": first_healthy,
            "second_deploy_attempted": bool(not first_healthy and args.second_deploy_if_first_not_healthy),
            "second_deploy_became_healthy": second_healthy,
            "reproduced_first_materialization_failure": first_deploy.get("ok") is True and not first_healthy,
            "reproduced_redeploy_repairs_materialization": (
                first_deploy.get("ok") is True and not first_healthy and second_healthy is True
            ),
            "docker_cleanup_collision_requested": args.trigger_docker_cleanup_after_seconds is not None,
            "docker_cleanup_collision_accepted": collision_accepted,
            "race_reproduced": race_reproduced,
            "checker_retry_attempted": args.prove_cleanup_checker_retry,
            "checker_first_attempt_armed": True if args.prove_cleanup_checker_retry else None,
            "checker_first_attempt_cleanup_overlap_detected": (
                checker_first_overlap_detected if args.prove_cleanup_checker_retry else None
            ),
            "checker_first_attempt_marked_contaminated": (
                checker_first_marked_contaminated if args.prove_cleanup_checker_retry else None
            ),
            "checker_first_attempt_retry_reason": checker_retry_reason if args.prove_cleanup_checker_retry else None,
            "overlapping_cleanup_completed": checker_overlap_completed if args.prove_cleanup_checker_retry else None,
            "checker_second_attempt_armed": True if args.prove_cleanup_checker_retry else None,
            "checker_second_attempt_cleanup_overlap_detected": (
                checker_second_overlap_detected if args.prove_cleanup_checker_retry else None
            ),
            "checker_second_attempt_became_healthy": (
                checker_second_healthy if args.prove_cleanup_checker_retry else None
            ),
            "checker_retry_model_proven": checker_proven if args.prove_cleanup_checker_retry else None,
            "race_reproduced_and_checker_retry_proven": (
                race_reproduced and checker_proven if args.prove_cleanup_checker_retry else None
            ),
        }
    except SmokeError as exc:
        result["error"] = str(exc)
        result.setdefault("diagnosis", {})["smoke_completed"] = False
    finally:
        if service_uuid and args.delete_service_row_after_smoke:
            delete_receipt = _http(
                controller,
                "DELETE",
                f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}",
                body=None,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
        if checker_service_uuid and args.delete_service_row_after_smoke:
            checker_delete_receipt = _http(
                controller,
                "DELETE",
                f"/api/v1/services/{urllib.parse.quote(checker_service_uuid, safe='')}",
                body=None,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
            )
        result["delete_service_row_after_smoke_requested"] = args.delete_service_row_after_smoke
        result["delete"] = delete_receipt
        result["checker_delete"] = checker_delete_receipt
        result["completed_at"] = _timestamp()

    out_dir = mother_paths.root / "twiddles"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"coolify-forced-deploy-materialization-smoke-{service_name}-{int(time.time())}.json"
    out_path.write_text(json.dumps(_redact(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    diagnosis = result.get("diagnosis") if isinstance(result.get("diagnosis"), Mapping) else {}
    summary = {
        "status": "wrote",
        "path": str(out_path),
        "controller_id": args.controller,
        "transport": "coolify-api-only",
        "service_name": service_name,
        "service_uuid": service_uuid,
        "checker_service_uuid": checker_service_uuid,
        "diagnosis": diagnosis,
        "delete_service_row_after_smoke_requested": args.delete_service_row_after_smoke,
        "next": (
            "Inspect the evidence JSON and Coolify service rows; disposable services were left in place."
            if not args.delete_service_row_after_smoke
            else "Delete was requested through the Coolify API; verify receipts in evidence."
        ),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))

    if "error" in result:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
