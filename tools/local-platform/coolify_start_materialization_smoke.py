#!/usr/bin/env python3
"""Live smoke for the Coolify service-row /start materialization boundary.

This smoke deliberately exercises the same class of primitive that failed during
single-node bootstrap:

1. POST /api/v1/services with docker_compose_raw and instant_deploy=false.
2. POST /api/v1/services/<uuid>/start.
3. Poll Coolify readback.
4. Independently check Docker on the target host for service files, networks,
   volumes, compose project, and containers.

It does not touch Mother topology, validator admission, QBFT votes, or existing
node service rows.  It creates a disposable Coolify service row whose name starts
with mother-start-materialization-smoke.

The optional --manual-compose-up-if-no-containers flag reproduces the operator
fallback that proved the original generated files were usable: run docker compose
up -d from /data/coolify/services/<new_uuid> only if Coolify /start did not
materialize containers.
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
import shlex
import subprocess
import sys
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
        "User-Agent": "main-computer-coolify-start-materialization-smoke/1",
    }
    if raw_body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(controller.base_url + endpoint, data=raw_body, headers=headers, method=method)
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


def _service_row_body(
    controller_config: Mapping[str, Any],
    *,
    network: str,
    environment_uuid: str,
    service_name: str,
    compose: str,
    description: str,
) -> dict[str, Any]:
    return {
        "project_uuid": controller_config["project_uuid"],
        "server_uuid": controller_config["server_uuid"],
        "environment_name": _safe_name(network, "network"),
        "environment_uuid": _uuid(environment_uuid, "environment_uuid"),
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "name": _safe_name(service_name, "service_name"),
        "description": description,
        "instant_deploy": False,
    }


def _mock_compose(service_name: str, variant: str) -> str:
    """Return a no-secret compose that mimics the failed materialization boundary."""

    service_name = _safe_name(service_name, "service_name")
    label_prefix = "main_computer.mother.smoke.coolify_start_materialization"

    if variant == "minimal":
        return f"""services:
  smoke-main:
    image: alpine:3.20
    restart: unless-stopped
    command:
      - sh
      - -ec
      - |
        mkdir -p /proof
        while true; do date -u +%Y-%m-%dT%H:%M:%SZ > /proof/tick.txt; sleep 5; done
    healthcheck:
      test:
        - CMD
        - sh
        - -ec
        - test -s /proof/tick.txt
      interval: 5s
      timeout: 3s
      retries: 12
      start_period: 5s
    volumes:
      - smoke-proof:/proof
    labels:
      {label_prefix}: "true"
      {label_prefix}.service_name: {service_name}
volumes:
  smoke-proof:
"""

    if variant != "bootstrap-shape":
        raise SmokeError("variant must be minimal or bootstrap-shape")

    return f"""services:
  smoke-init:
    image: alpine:3.20
    restart: "no"
    command:
      - sh
      - -ec
      - |
        mkdir -p /config /proof
        echo smoke-genesis > /config/genesis.json
        echo smoke-init-complete > /proof/init.txt
    volumes:
      - smoke-config:/config
      - smoke-proof:/proof
    labels:
      {label_prefix}: "true"
      {label_prefix}.component: init
      {label_prefix}.service_name: {service_name}

  smoke-fdb:
    image: foundationdb/foundationdb:7.4.6
    restart: unless-stopped
    healthcheck:
      test:
        - CMD
        - sh
        - -ec
        - test -e /usr/sbin/fdbserver || test -e /usr/bin/fdbserver
      interval: 5s
      timeout: 3s
      retries: 12
      start_period: 5s
    labels:
      {label_prefix}: "true"
      {label_prefix}.component: fdb
      {label_prefix}.service_name: {service_name}

  smoke-hub:
    image: python:3.12-alpine
    restart: unless-stopped
    depends_on:
      smoke-init:
        condition: service_completed_successfully
      smoke-fdb:
        condition: service_started
    command:
      - python
      - -u
      - -c
      - |
        import http.server, json, time
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt, *args): pass
            def do_GET(self):
                body = json.dumps({{"ok": True, "service": "smoke-hub", "path": self.path, "time": time.time()}}, sort_keys=True).encode()
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
      interval: 5s
      timeout: 3s
      retries: 24
      start_period: 5s
    labels:
      {label_prefix}: "true"
      {label_prefix}.component: hub
      {label_prefix}.service_name: {service_name}

  smoke-proof:
    image: python:3.12-alpine
    restart: unless-stopped
    depends_on:
      smoke-hub:
        condition: service_healthy
    command:
      - python
      - -u
      - -c
      - |
        import json, os, time, urllib.request
        os.makedirs("/proof", exist_ok=True)
        while True:
            try:
                urllib.request.urlopen("http://smoke-hub:8790/health", timeout=2).read()
                proof = {{"ok": True, "contract": "coolify-start-materialization-smoke", "proved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}}
                with open("/proof/proof.json", "w", encoding="utf-8") as handle:
                    json.dump(proof, handle, sort_keys=True)
                with open("/proof/healthy", "w", encoding="utf-8") as handle:
                    handle.write(str(int(time.time())))
                try: os.unlink("/proof/last-error.json")
                except FileNotFoundError: pass
            except Exception as exc:
                try: os.unlink("/proof/healthy")
                except FileNotFoundError: pass
                with open("/proof/last-error.json", "w", encoding="utf-8") as handle:
                    json.dump({{"ok": False, "error": str(exc), "type": type(exc).__name__}}, handle, sort_keys=True)
            time.sleep(5)
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - import os,time; p="/proof/healthy"; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 30
      interval: 5s
      timeout: 3s
      retries: 24
      start_period: 5s
    volumes:
      - smoke-proof:/proof
    labels:
      {label_prefix}: "true"
      {label_prefix}.component: proof
      {label_prefix}.service_name: {service_name}

volumes:
  smoke-config:
  smoke-proof:
"""


def _run(argv: list[str], *, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "argv": argv,
            "rc": completed.returncode,
            "stdout": _redact(completed.stdout),
            "stderr": _redact(completed.stderr),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": argv,
            "rc": None,
            "timeout": True,
            "stdout": _redact(exc.stdout or ""),
            "stderr": _redact(exc.stderr or ""),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }


def _ssh(host: str, command: str, *, timeout: float) -> dict[str, Any]:
    return _run(["ssh", host, "bash", "-lc", command], timeout=timeout)


def _remote_probe(ssh_host: str, service_uuid: str, service_name: str, *, timeout: float) -> dict[str, Any]:
    service_uuid = _uuid(service_uuid, "service_uuid")
    service_name = _safe_name(service_name, "service_name")
    grep = shlex.quote(f"{service_uuid}|{service_name}|smoke-main|smoke-hub|smoke-proof|smoke-fdb|smoke-init")
    service_dir = f"/data/coolify/services/{shlex.quote(service_uuid)}"
    return {
        "service_dir": _ssh(
            ssh_host,
            f"test -d {service_dir}; echo service_dir_rc=$?; "
            f"test -f {service_dir}/docker-compose.yml; echo compose_file_rc=$?; "
            f"test -f {service_dir}/.env; echo env_file_rc=$?",
            timeout=timeout,
        ),
        "compose_config": _ssh(
            ssh_host,
            f"if test -f {service_dir}/docker-compose.yml; then "
            f"cd {service_dir} && docker compose --env-file .env -f docker-compose.yml -p {shlex.quote(service_uuid)} config >/tmp/{shlex.quote(service_uuid)}-smoke-rendered.yml; "
            f"echo compose_config_rc=$?; "
            f"else echo compose_config_rc=missing-compose-file; fi",
            timeout=timeout,
        ),
        "docker_ps": _ssh(
            ssh_host,
            "docker ps -a --no-trunc --format "
            + shlex.quote("{{.ID}} {{.Names}} {{.Image}} {{.Status}} {{.Labels}}")
            + f" | grep -Ei {grep} || true",
            timeout=timeout,
        ),
        "compose_ls": _ssh(
            ssh_host,
            f"docker compose ls -a | grep -Ei {grep} || true",
            timeout=timeout,
        ),
        "network": _ssh(
            ssh_host,
            f"docker network ls | grep -Ei {shlex.quote(service_uuid)} || true; "
            f"docker network inspect {shlex.quote(service_uuid)} --format "
            + shlex.quote("{{range $id, $c := .Containers}}{{println $id $c.Name $c.IPv4Address}}{{end}}")
            + " 2>/dev/null || true",
            timeout=timeout,
        ),
        "volumes": _ssh(
            ssh_host,
            f"docker volume ls | grep -Ei {shlex.quote(service_uuid)} || true",
            timeout=timeout,
        ),
    }


def _docker_ps_has_containers(remote_probe: Mapping[str, Any]) -> bool:
    docker_ps = remote_probe.get("docker_ps")
    if not isinstance(docker_ps, Mapping):
        return False
    stdout = docker_ps.get("stdout")
    if not isinstance(stdout, str):
        return False
    lines = [line for line in stdout.splitlines() if line.strip()]
    return any("smoke-" in line or "mother-start-materialization-smoke" in line for line in lines)


def _manual_compose_up(ssh_host: str, service_uuid: str, *, timeout: float) -> dict[str, Any]:
    service_uuid = _uuid(service_uuid, "service_uuid")
    service_dir = f"/data/coolify/services/{shlex.quote(service_uuid)}"
    return _ssh(
        ssh_host,
        f"cd {service_dir} && "
        f"docker compose --env-file .env -f docker-compose.yml -p {shlex.quote(service_uuid)} up -d; "
        f"echo manual_compose_up_rc=$?; "
        f"docker compose --env-file .env -f docker-compose.yml -p {shlex.quote(service_uuid)} ps -a",
        timeout=timeout,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Live Coolify /start materialization smoke.")
    ap.add_argument("--runtime-state-root", default="runtime/state")
    ap.add_argument("--network", default="mainnet")
    ap.add_argument("--controller", default="coolify-a")
    ap.add_argument("--ssh-host", default="", help="Optional SSH target for independent Docker probes, e.g. root@testnet")
    ap.add_argument("--variant", choices=("minimal", "bootstrap-shape"), default="bootstrap-shape")
    ap.add_argument("--service-name", default="", help="Optional disposable service name; default is generated")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--ssh-timeout", type=float, default=60.0)
    ap.add_argument("--max-response-bytes", type=int, default=_DEFAULT_MAX_RESPONSE_BYTES)
    ap.add_argument("--poll-seconds", type=float, default=180.0)
    ap.add_argument("--poll-interval-seconds", type=float, default=5.0)
    ap.add_argument("--manual-compose-up-if-no-containers", action="store_true")
    ap.add_argument("--delete-service-row-after-smoke", action="store_true")
    args = ap.parse_args()

    service_name = args.service_name or f"mother-start-materialization-smoke-{_stamp_for_name()}"
    service_name = _safe_name(service_name, "service_name")

    mother_paths = MotherPaths(runtime_state_root=args.runtime_state_root)
    operation = OperationIdentity(
        operation_id=f"coolify-start-materialization-smoke-{int(time.time())}",
        request_id="manual-coolify-start-materialization-smoke",
        network=args.network,
        operation_kind="MOTHER-OP-DIAGNOSE",
    )
    private_state = read_private_state(mother_paths.resolve_private_state_paths(), operation=operation)
    controller = resolve_coolify_controller(private_state, args.network, args.controller, require_enabled=True, require_token=True)

    observations: list[dict[str, Any]] = []
    controller_config = _controller_config(private_state, network=args.network, controller_id=args.controller)
    try:
        environment_uuid = _resolve_environment_uuid(
            controller=controller,
            controller_id=args.controller,
            endpoint=f"/api/v1/projects/{urllib.parse.quote(str(controller_config['project_uuid']), safe='')}/environments",
            expected_name=args.network,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            opener=_DEFAULT_OPENER,
            observations=observations,
        )
    except MotherDeploymentCompletedHelperCleanupError as exc:
        raise SmokeError(str(exc)) from exc

    compose = _mock_compose(service_name, args.variant)
    body = _service_row_body(
        controller_config,
        network=args.network,
        environment_uuid=environment_uuid,
        service_name=service_name,
        compose=compose,
        description="Disposable smoke for Coolify /start materialization boundary; safe to delete.",
    )
    body_sha256 = hashlib.sha256(canonical_json(body)).hexdigest()
    compose_sha256 = hashlib.sha256(compose.encode("utf-8")).hexdigest()

    create = _http(controller, "POST", "/api/v1/services", body=body, timeout=args.timeout, max_response_bytes=args.max_response_bytes)
    service_uuid: str | None = None
    start: dict[str, Any] | None = None
    delete_receipt: dict[str, Any] | None = None
    remote_before: dict[str, Any] | None = None
    remote_after_start: dict[str, Any] | None = None
    manual_up: dict[str, Any] | None = None
    remote_after_manual_up: dict[str, Any] | None = None
    poll_observations: list[dict[str, Any]] = []

    if create.get("ok") is True:
        try:
            service_uuid = _application_uuid(create.get("payload"))
        except MotherDeploymentCompletedHelperCleanupError as exc:
            service_uuid = None
            create["service_uuid_parse_error"] = str(exc)

    if service_uuid:
        if args.ssh_host:
            remote_before = _remote_probe(args.ssh_host, service_uuid, service_name, timeout=args.ssh_timeout)

        start_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}/start"
        start = _http(controller, "POST", start_endpoint, body=None, timeout=args.timeout, max_response_bytes=args.max_response_bytes)

        deadline = time.monotonic() + max(0.0, args.poll_seconds)
        detail_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
        while True:
            detail = _http(controller, "GET", detail_endpoint, body=None, timeout=args.timeout, max_response_bytes=args.max_response_bytes)
            status_values: list[str] = []

            def walk(value: Any) -> None:
                if isinstance(value, dict):
                    status = value.get("status") or value.get("state")
                    name = value.get("name") or value.get("service_name")
                    uuid = value.get("uuid") or value.get("service_uuid") or value.get("application_uuid")
                    if isinstance(status, str) and (uuid == service_uuid or name == service_name or (isinstance(name, str) and name.startswith("smoke-"))):
                        status_values.append(status)
                    for child in value.values():
                        if isinstance(child, (dict, list)):
                            walk(child)
                elif isinstance(value, list):
                    for child in value:
                        walk(child)

            walk(detail.get("payload"))
            poll_observations.append({
                "observed_at": _timestamp(),
                "ok": detail.get("ok"),
                "status": detail.get("status"),
                "response_sha256": detail.get("response_sha256"),
                "observed_statuses": status_values[:32],
            })
            if any(str(item).startswith("running") for item in status_values):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0.0, args.poll_interval_seconds))

        if args.ssh_host:
            remote_after_start = _remote_probe(args.ssh_host, service_uuid, service_name, timeout=args.ssh_timeout)
            if args.manual_compose_up_if_no_containers and not _docker_ps_has_containers(remote_after_start):
                manual_up = _manual_compose_up(args.ssh_host, service_uuid, timeout=max(args.ssh_timeout, 180.0))
                remote_after_manual_up = _remote_probe(args.ssh_host, service_uuid, service_name, timeout=args.ssh_timeout)

        if args.delete_service_row_after_smoke:
            delete_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
            delete_receipt = _http(controller, "DELETE", delete_endpoint, body=None, timeout=args.timeout, max_response_bytes=args.max_response_bytes)

    containers_after_start = _docker_ps_has_containers(remote_after_start or {}) if args.ssh_host else None
    containers_after_manual = _docker_ps_has_containers(remote_after_manual_up or {}) if args.ssh_host else None

    result = {
        "kind": "main_computer.mother.coolify_start_materialization_smoke.v1",
        "started_at": _timestamp(),
        "network": args.network,
        "controller_id": args.controller,
        "ssh_host_provided": bool(args.ssh_host),
        "variant": args.variant,
        "service_name": service_name,
        "service_uuid": service_uuid,
        "compose_sha256": compose_sha256,
        "create_body_sha256": body_sha256,
        "create": create,
        "start": start,
        "poll_observations": poll_observations,
        "remote_before_start": remote_before,
        "remote_after_start": remote_after_start,
        "manual_compose_up_if_no_containers_requested": args.manual_compose_up_if_no_containers,
        "manual_compose_up": manual_up,
        "remote_after_manual_compose_up": remote_after_manual_up,
        "delete_service_row_after_smoke_requested": args.delete_service_row_after_smoke,
        "delete": delete_receipt,
        "diagnosis": {
            "coolify_create_ok": create.get("ok") is True,
            "coolify_start_ok": bool(start and start.get("ok") is True),
            "docker_containers_observed_after_start": containers_after_start,
            "docker_containers_observed_after_manual_compose_up": containers_after_manual,
            "reproduced_failed_boundary": (start is not None and start.get("ok") is True and containers_after_start is False),
            "manual_compose_up_materialized_after_start_failed": (
                start is not None
                and start.get("ok") is True
                and containers_after_start is False
                and manual_up is not None
                and manual_up.get("rc") == 0
                and containers_after_manual is True
            ),
        },
        "completed_at": _timestamp(),
    }

    out_dir = mother_paths.root / "mother" / "twiddles"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"coolify-start-materialization-smoke-{service_name}-{int(time.time())}.json"
    out_path.write_text(json.dumps(_redact(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = {
        "status": "wrote",
        "path": str(out_path),
        "service_name": service_name,
        "service_uuid": service_uuid,
        "diagnosis": result["diagnosis"],
        "delete_service_row_after_smoke_requested": args.delete_service_row_after_smoke,
        "next": "Inspect the evidence JSON; delete the disposable service row when done." if not args.delete_service_row_after_smoke else "Service-row delete was requested; verify cleanup if needed.",
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if create.get("ok") is True and start is not None and start.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
