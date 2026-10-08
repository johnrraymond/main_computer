#!/usr/bin/env python3
"""Smoke test Coolify Compose PATCH + exact service-line restart helper.

This is intentionally NOT an add-node patch. It creates a disposable Coolify
service with one fake service line that serves /helloworld, verifies it, PATCHes
the same service line so it should serve /goodbyeworld, then runs the existing
mother_service_line_restart_helper against that exact line and verifies the
result over HTTP.

The test answers two questions:

    Does PATCH + service-line restart helper materialize the changed Compose
    definition for an already-existing service line?

    If it does not, does DELETE old disposable row + POST a fresh row from the
    patched Compose materialize the changed definition?

It deliberately does not mutate validator services.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

import yaml


def _repo_root() -> Path:
    # Run from repo root, or set MC_REPO_ROOT=C:\path\to\main_computer.
    return Path(__import__("os").environ.get("MC_REPO_ROOT") or Path.cwd()).resolve()


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import resolve_coolify_controller  # noqa: E402
from tools.mother.common.deployment_completed_helper_cleanup import (  # noqa: E402
    _application_uuid,
    _controller_config,
    _http,
    _resolve_environment_uuid,
)
from tools.mother_service_line_restart_helper import (  # noqa: E402
    _load_private_state,
    execute_service_line_restart_helper,
)


KIND = "main_computer.mother.patch_restart_service_line_smoke.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_response(response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": response.get("status"),
        "ok": response.get("ok"),
        "response_sha256": response.get("response_sha256"),
        "byte_length": response.get("byte_length"),
        "elapsed_ms": response.get("elapsed_ms"),
    }


def _server_script() -> str:
    return """import http.server, os
PATH = os.environ.get('SMOKE_PATH', '/helloworld')
BODY = (os.environ.get('SMOKE_BODY', 'helloworld') + '\\n').encode('utf-8')
PORT = int(os.environ.get('SMOKE_PORT', '8797'))

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.path == PATH:
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)
            return
        self.send_response(404)
        self.end_headers()

http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
"""


def _compose(*, service_line: str, host_port: int, container_port: int, route: str, body: str) -> str:
    health_cmd = (
        "python -c \"import urllib.request; "
        f"urllib.request.urlopen('http://127.0.0.1:{container_port}{route}', timeout=2).read()\""
    )
    doc = {
        "services": {
            service_line: {
                "image": "python:3.12-alpine",
                "restart": "unless-stopped",
                "command": ["python", "-u", "-c", _server_script()],
                "environment": {
                    "SMOKE_PATH": route,
                    "SMOKE_BODY": body,
                    "SMOKE_PORT": str(container_port),
                },
                "ports": [f"{host_port}:{container_port}"],
                "healthcheck": {
                    "test": ["CMD-SHELL", health_cmd],
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 12,
                    "start_period": "5s",
                },
                "labels": {
                    "main_computer.mother.patch_restart_smoke": "true",
                    "main_computer.mother.patch_restart_smoke_route": route,
                },
            }
        }
    }
    return yaml.safe_dump(doc, sort_keys=False)


def _public_url(*, controller_base_url: str, host: str | None, port: int, route: str, scheme: str) -> str:
    public_host = host
    if not public_host:
        parsed = urllib.parse.urlsplit(controller_base_url)
        public_host = parsed.hostname
    if not public_host:
        raise RuntimeError("could not derive public host; pass --public-host")
    return f"{scheme}://{public_host}:{port}{route}"


def _fetch_text(url: str, *, timeout: float, max_bytes: int = 65536) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "text/plain"}, method="GET")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            status = int(getattr(response, "status", response.getcode()))
            content_type = str(response.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as exc:
        raw = exc.read(max_bytes + 1)
        status = int(exc.code)
        content_type = str(exc.headers.get("Content-Type", "")) if exc.headers else ""
    except Exception as exc:  # noqa: BLE001 - smoke evidence boundary
        return {
            "ok": False,
            "status": None,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    raw = raw[:max_bytes]
    return {
        "ok": 200 <= status <= 299,
        "status": status,
        "content_type": content_type,
        "body": raw.decode("utf-8", errors="replace"),
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _wait_http(
    *,
    url: str,
    expected_status: int,
    expected_body_contains: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    request_timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    observations: list[dict[str, Any]] = []
    while True:
        probe = _fetch_text(url, timeout=request_timeout)
        observations.append(probe)
        if (
            probe.get("status") == expected_status
            and expected_body_contains in str(probe.get("body") or "")
        ):
            return {
                "completed": True,
                "url": url,
                "expected_status": expected_status,
                "expected_body_contains": expected_body_contains,
                "observation_count": len(observations),
                "wait_milliseconds": int((time.monotonic() - started) * 1000),
                "last_probe": probe,
                "observations": observations,
            }
        elapsed = time.monotonic() - started
        if elapsed >= timeout_seconds:
            return {
                "completed": False,
                "url": url,
                "expected_status": expected_status,
                "expected_body_contains": expected_body_contains,
                "observation_count": len(observations),
                "wait_milliseconds": int(elapsed * 1000),
                "last_probe": probe,
                "observations": observations,
            }
        time.sleep(min(max(0.1, poll_interval_seconds), max(0.0, timeout_seconds - elapsed)))


def _wait_http_not_matching(
    *,
    url: str,
    forbidden_status: int,
    forbidden_body_contains: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    request_timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    observations: list[dict[str, Any]] = []
    while True:
        probe = _fetch_text(url, timeout=request_timeout)
        observations.append(probe)
        still_matches = (
            probe.get("status") == forbidden_status
            and forbidden_body_contains in str(probe.get("body") or "")
        )
        if not still_matches:
            return {
                "completed": True,
                "url": url,
                "forbidden_status": forbidden_status,
                "forbidden_body_contains": forbidden_body_contains,
                "observation_count": len(observations),
                "wait_milliseconds": int((time.monotonic() - started) * 1000),
                "last_probe": probe,
                "observations": observations,
            }
        elapsed = time.monotonic() - started
        if elapsed >= timeout_seconds:
            return {
                "completed": False,
                "url": url,
                "forbidden_status": forbidden_status,
                "forbidden_body_contains": forbidden_body_contains,
                "observation_count": len(observations),
                "wait_milliseconds": int(elapsed * 1000),
                "last_probe": probe,
                "observations": observations,
            }
        time.sleep(min(max(0.1, poll_interval_seconds), max(0.0, timeout_seconds - elapsed)))


def _attempt_delete_remake(
    *,
    result: dict[str, Any],
    controller: Any,
    controller_config: Mapping[str, Any],
    environment_uuid: str,
    args: argparse.Namespace,
    original_service_uuid: str,
    original_service_name: str,
    service_line: str,
    goodbye_compose: str,
    hello_url: str,
    goodbye_url: str,
) -> dict[str, Any]:
    stage: dict[str, Any] = {
        "status": "pending",
        "hypothesis": (
            "DELETE the disposable row, then POST a fresh row from the goodbyeworld "
            "Compose and start that fresh row"
        ),
        "original_service_uuid": original_service_uuid,
        "original_service_name": original_service_name,
        "service_line": service_line,
        "host_port": args.host_port,
        "container_port": args.container_port,
        "fresh_row_start_uses_coolify_start": True,
        "touches_validator_services": False,
    }
    result["delete_remake"] = stage

    delete_endpoint = f"/api/v1/services/{urllib.parse.quote(original_service_uuid, safe='')}"
    delete = _http(
        controller,
        "DELETE",
        delete_endpoint,
        body=None,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        opener=urllib.request.urlopen,
    )
    stage["delete_original"] = {
        "method": "DELETE",
        "endpoint": delete_endpoint,
        "response": _safe_response(delete),
    }
    if delete.get("ok") is not True:
        stage["status"] = "failed"
        stage["reason"] = "delete-original-smoke-service-before-remake-failed"
        return stage

    old_route_stop_wait = _wait_http_not_matching(
        url=hello_url,
        forbidden_status=200,
        forbidden_body_contains="helloworld",
        timeout_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        request_timeout=args.timeout,
    )
    stage["old_route_stop_wait"] = old_route_stop_wait
    if old_route_stop_wait.get("completed") is not True:
        stage["status"] = "failed"
        stage["reason"] = "old-helloworld-route-still-observed-after-delete"
        return stage

    remake_service_name = args.remake_service_name or f"{original_service_name}-remade-{_stamp().lower()}"
    stage["remake_service_name"] = remake_service_name
    create_body = {
        "project_uuid": controller_config["project_uuid"],
        "server_uuid": controller_config["server_uuid"],
        "environment_uuid": environment_uuid,
        "environment_name": args.network,
        "docker_compose_raw": base64.b64encode(goodbye_compose.encode("utf-8")).decode("ascii"),
        "name": remake_service_name,
        "description": "Disposable smoke test for Coolify delete/remake materialization after PATCH diagnostic",
        "instant_deploy": False,
    }
    stage["create_body_sha256"] = hashlib.sha256(_canonical_json(create_body)).hexdigest()
    create = _http(
        controller,
        "POST",
        "/api/v1/services",
        body=create_body,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        opener=urllib.request.urlopen,
    )
    stage["create_remake"] = {
        "method": "POST",
        "endpoint": "/api/v1/services",
        "response": _safe_response(create),
    }
    if create.get("ok") is not True:
        stage["status"] = "failed"
        stage["reason"] = "remake-smoke-service-create-failed"
        return stage

    remake_service_uuid = _application_uuid(create.get("payload"))
    stage["service_uuid"] = remake_service_uuid

    start_endpoint = f"/api/v1/services/{urllib.parse.quote(remake_service_uuid, safe='')}/start"
    start = _http(
        controller,
        "POST",
        start_endpoint,
        body=None,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        opener=urllib.request.urlopen,
    )
    stage["start_remake"] = {
        "method": "POST",
        "endpoint": start_endpoint,
        "response": _safe_response(start),
        "fresh_row_setup_only": True,
    }
    if start.get("ok") is not True:
        stage["status"] = "failed"
        stage["reason"] = "remake-smoke-service-start-failed"
        return stage

    goodbye_wait_after_remake = _wait_http(
        url=goodbye_url,
        expected_status=200,
        expected_body_contains="goodbyeworld",
        timeout_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        request_timeout=args.timeout,
    )
    stage["goodbye_wait_after_remake"] = goodbye_wait_after_remake

    old_route_after_remake_probe = _fetch_text(hello_url, timeout=args.timeout)
    stage["old_route_after_remake_probe"] = old_route_after_remake_probe
    old_route_still_live = (
        old_route_after_remake_probe.get("status") == 200
        and "helloworld" in str(old_route_after_remake_probe.get("body") or "")
    )

    if goodbye_wait_after_remake.get("completed") is True and not old_route_still_live:
        stage["status"] = "pass"
        stage["reason"] = "delete-remake-materialized-goodbyeworld"
    elif goodbye_wait_after_remake.get("completed") is True and old_route_still_live:
        stage["status"] = "failed"
        stage["reason"] = "goodbyeworld-observed-but-old-helloworld-route-still-live-after-remake"
    else:
        stage["status"] = "failed"
        stage["reason"] = "goodbyeworld-not-observed-after-delete-remake"

    return stage


def _write_evidence(runtime_state_root: str | Path, result: Mapping[str, Any]) -> str:
    root = Path(runtime_state_root) / "mother" / "evidence" / "patch-restart-service-line-smoke"
    root.mkdir(parents=True, exist_ok=True)
    name = (
        f"{_stamp()}-{result.get('network', 'unknown')}-"
        f"{result.get('controller_id', 'unknown')}-{result.get('service_line', 'unknown')}.json"
    )
    path = root / name
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


def execute(args: argparse.Namespace) -> dict[str, Any]:
    private_state = _load_private_state(args.runtime_state_root, network=args.network, mode="execute")
    controller = resolve_coolify_controller(
        private_state,
        args.network,
        args.controller_id,
        require_enabled=True,
        require_token=True,
    )
    controller_config = _controller_config(private_state, network=args.network, controller_id=args.controller_id)

    service_line = args.service_line
    smoke_service_name = args.service_name or f"{service_line}-{_stamp().lower()}"

    observations: list[dict[str, Any]] = []
    env_endpoint = f"/api/v1/projects/{urllib.parse.quote(str(controller_config['project_uuid']), safe='')}/environments"
    environment_uuid = _resolve_environment_uuid(
        controller=controller,
        controller_id=args.controller_id,
        endpoint=env_endpoint,
        expected_name=args.network,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        opener=urllib.request.urlopen,
        observations=observations,
    )

    hello_compose = _compose(
        service_line=service_line,
        host_port=args.host_port,
        container_port=args.container_port,
        route="/helloworld",
        body="helloworld",
    )
    goodbye_compose = _compose(
        service_line=service_line,
        host_port=args.host_port,
        container_port=args.container_port,
        route="/goodbyeworld",
        body="goodbyeworld",
    )

    result: dict[str, Any] = {
        "kind": KIND,
        "observed_at": _utc_now(),
        "status": "pending",
        "network": args.network,
        "controller_id": args.controller_id,
        "service_name": smoke_service_name,
        "service_line": service_line,
        "host_port": args.host_port,
        "container_port": args.container_port,
        "initial_route": "/helloworld",
        "patched_route": "/goodbyeworld",
        "initial_setup_uses_coolify_start": True,
        "patch_materialization_uses_coolify_start": False,
        "restart_helper_required": True,
        "delete_remake_after_patch_failure_requested": bool(args.delete_remake_after_patch_failure),
        "delete_remake_touches_validator_services": False,
        "compose_sha256": {
            "hello": hashlib.sha256(hello_compose.encode("utf-8")).hexdigest(),
            "goodbye": hashlib.sha256(goodbye_compose.encode("utf-8")).hexdigest(),
        },
        "environment_uuid": environment_uuid,
        "observations": observations,
    }

    create_body = {
        "project_uuid": controller_config["project_uuid"],
        "server_uuid": controller_config["server_uuid"],
        "environment_uuid": environment_uuid,
        "environment_name": args.network,
        "docker_compose_raw": base64.b64encode(hello_compose.encode("utf-8")).decode("ascii"),
        "name": smoke_service_name,
        "description": "Disposable smoke test for Coolify PATCH + Mother service-line restart helper",
        "instant_deploy": False,
    }
    result["create_body_sha256"] = hashlib.sha256(_canonical_json(create_body)).hexdigest()
    create = _http(
        controller,
        "POST",
        "/api/v1/services",
        body=create_body,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        opener=urllib.request.urlopen,
    )
    result["create"] = {
        "method": "POST",
        "endpoint": "/api/v1/services",
        "response": _safe_response(create),
    }
    if create.get("ok") is not True:
        result["status"] = "failed"
        result["reason"] = "smoke-service-create-failed"
        return result

    service_uuid = _application_uuid(create.get("payload"))
    result["service_uuid"] = service_uuid

    # Setup only: create one disposable service line and prove it serves /helloworld.
    # The actual PATCH materialization test below intentionally does not use /start.
    start_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}/start"
    start = _http(
        controller,
        "POST",
        start_endpoint,
        body=None,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        opener=urllib.request.urlopen,
    )
    result["initial_start"] = {
        "method": "POST",
        "endpoint": start_endpoint,
        "response": _safe_response(start),
        "setup_only": True,
    }
    if start.get("ok") is not True:
        result["status"] = "failed"
        result["reason"] = "initial-smoke-service-start-failed"
        return result

    hello_url = _public_url(
        controller_base_url=controller.base_url,
        host=args.public_host,
        port=args.host_port,
        route="/helloworld",
        scheme=args.public_scheme,
    )
    goodbye_url = _public_url(
        controller_base_url=controller.base_url,
        host=args.public_host,
        port=args.host_port,
        route="/goodbyeworld",
        scheme=args.public_scheme,
    )
    result["hello_url"] = hello_url
    result["goodbye_url"] = goodbye_url

    hello_wait = _wait_http(
        url=hello_url,
        expected_status=200,
        expected_body_contains="helloworld",
        timeout_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        request_timeout=args.timeout,
    )
    result["hello_wait"] = hello_wait
    if hello_wait.get("completed") is not True:
        result["status"] = "failed"
        result["reason"] = "helloworld-not-observed-after-initial-start"
        return result

    patch_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
    patch_body = {
        "docker_compose_raw": base64.b64encode(goodbye_compose.encode("utf-8")).decode("ascii"),
        "name": smoke_service_name,
    }
    result["patch_body_sha256"] = hashlib.sha256(_canonical_json(patch_body)).hexdigest()
    patch = _http(
        controller,
        "PATCH",
        patch_endpoint,
        body=patch_body,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        opener=urllib.request.urlopen,
    )
    result["patch"] = {
        "method": "PATCH",
        "endpoint": patch_endpoint,
        "response": _safe_response(patch),
    }
    if patch.get("ok") is not True:
        result["status"] = "failed"
        result["reason"] = "goodbyeworld-compose-patch-failed"
        return result

    restart = execute_service_line_restart_helper(
        private_state,
        runtime_state_root=args.runtime_state_root,
        network=args.network,
        mode="execute",
        controller_id=args.controller_id,
        service_uuid=service_uuid,
        service_line=service_line,
        delete_helper_after_exit=True,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    result["restart_helper"] = restart
    if not (isinstance(restart, Mapping) and restart.get("status") == "pass"):
        result["status"] = "failed"
        result["reason"] = "service-line-restart-helper-failed-after-patch"
        return result

    goodbye_wait = _wait_http(
        url=goodbye_url,
        expected_status=200,
        expected_body_contains="goodbyeworld",
        timeout_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        request_timeout=args.timeout,
    )
    result["goodbye_wait"] = goodbye_wait

    old_route_probe = _fetch_text(hello_url, timeout=args.timeout)
    result["old_route_after_restart_probe"] = old_route_probe

    if goodbye_wait.get("completed") is True:
        result["status"] = "pass"
        result["reason"] = "patch-plus-service-line-restart-materialized-goodbyeworld"
    else:
        result["status"] = "failed"
        result["reason"] = "patched-route-not-observed-after-service-line-restart-helper"
        if args.delete_remake_after_patch_failure:
            delete_remake = _attempt_delete_remake(
                result=result,
                controller=controller,
                controller_config=controller_config,
                environment_uuid=environment_uuid,
                args=args,
                original_service_uuid=service_uuid,
                original_service_name=smoke_service_name,
                service_line=service_line,
                goodbye_compose=goodbye_compose,
                hello_url=hello_url,
                goodbye_url=goodbye_url,
            )
            if delete_remake.get("status") == "pass":
                result["status"] = "pass"
                result["reason"] = "delete-remake-materialized-goodbyeworld-after-patch-restart-failed"
                result["remade_service_uuid"] = delete_remake.get("service_uuid")
                result["remade_service_name"] = delete_remake.get("remake_service_name")
            else:
                result["status"] = "failed"
                result["reason"] = str(delete_remake.get("reason") or "delete-remake-smoke-failed")

    if args.cleanup_on_success and result["status"] == "pass":
        cleanup_service_uuid = service_uuid
        cleanup_target = "original"
        delete_remake_result = result.get("delete_remake")
        if isinstance(delete_remake_result, Mapping) and delete_remake_result.get("service_uuid"):
            cleanup_service_uuid = str(delete_remake_result["service_uuid"])
            cleanup_target = "remade"
        delete_endpoint = f"/api/v1/services/{urllib.parse.quote(cleanup_service_uuid, safe='')}"
        delete = _http(
            controller,
            "DELETE",
            delete_endpoint,
            body=None,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            opener=urllib.request.urlopen,
        )
        result["cleanup"] = {
            "method": "DELETE",
            "endpoint": delete_endpoint,
            "response": _safe_response(delete),
            "target": cleanup_target,
        }

    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Disposable smoke test for Coolify PATCH + Mother exact service-line restart helper, "
            "optionally followed by DELETE + fresh row remake."
        )
    )
    parser.add_argument("mode", choices=("execute",))
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--controller-id", required=True)
    parser.add_argument("--host-port", type=int, required=True, help="Remote host port to expose the disposable smoke service on.")
    parser.add_argument("--container-port", type=int, default=8797)
    parser.add_argument("--service-line", default="mother-patch-restart-smoke")
    parser.add_argument("--service-name", default=None, help="Coolify parent smoke service name. Defaults to service-line plus timestamp.")
    parser.add_argument("--public-host", default=None, help="Host/IP used for HTTP probes. Defaults to the Coolify controller base-url host.")
    parser.add_argument("--public-scheme", default="http", choices=("http", "https"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    parser.add_argument("--max-wait-seconds", type=float, default=180.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument(
        "--delete-remake-after-patch-failure",
        action="store_true",
        help=(
            "When PATCH + service-line restart does not expose /goodbyeworld, delete the "
            "disposable row, create a fresh row from the goodbyeworld Compose, start it, "
            "and verify /goodbyeworld."
        ),
    )
    parser.add_argument("--remake-service-name", default=None, help="Optional name for the fresh row in the delete/remake stage.")
    parser.add_argument("--cleanup-on-success", action="store_true", help="Delete the disposable smoke service after a passing run.")
    parser.add_argument("--write-evidence", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = execute(args)
        if args.write_evidence:
            result = dict(result)
            result["evidence_path"] = _write_evidence(args.runtime_state_root, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") == "pass" else 2
    except Exception as exc:  # noqa: BLE001 - smoke script should emit evidence on unexpected failure
        failure = {
            "kind": KIND,
            "observed_at": _utc_now(),
            "status": "failed",
            "reason": "unhandled-smoke-test-error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(failure, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
