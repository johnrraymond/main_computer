#!/usr/bin/env python3
"""Guarded all-father cell entrypoint.

This runtime is intentionally small.  It starts an HTTP guard immediately, then
converges local child processes toward the desired state in the compiled
manifest.  Recovery is serialized: one wake/restart per guard tick.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse


DEFAULT_MANIFEST_PATH = "/opt/main-computer/allfather/manifest.json"


@dataclass
class ChildState:
    spec: dict[str, Any]
    process: subprocess.Popen[str] | None = None
    desired: bool = True
    last_started_at: float = 0.0
    last_exited_at: float = 0.0
    last_exit_code: int | None = None
    restart_count: int = 0
    last_error: str = ""

    @property
    def name(self) -> str:
        return str(self.spec.get("name") or "")

    @property
    def group(self) -> str:
        return str(self.spec.get("group") or "")

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def to_dict(self) -> dict[str, Any]:
        pid = self.process.pid if self.process is not None and self.running else None
        return {
            "name": self.name,
            "group": self.group,
            "desired": self.desired,
            "running": self.running,
            "pid": pid,
            "restart_count": self.restart_count,
            "last_started_at": self.last_started_at,
            "last_exited_at": self.last_exited_at,
            "last_exit_code": self.last_exit_code,
            "last_error": self.last_error,
            "critical": bool(self.spec.get("critical", True)),
        }


@dataclass
class GuardRuntime:
    manifest: dict[str, Any]
    children: dict[str, ChildState] = field(default_factory=dict)
    desired_up: bool = True
    drained: bool = False
    stop_requested: bool = False
    wake_requested: set[str] = field(default_factory=set)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        for spec in self.manifest.get("processes") or []:
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("name") or "").strip()
            command = spec.get("command")
            if not name or not isinstance(command, list) or not all(isinstance(item, str) for item in command):
                continue
            self.children[name] = ChildState(spec=spec, desired=bool(spec.get("desired", True)))

    @property
    def guard(self) -> dict[str, Any]:
        guard = self.manifest.get("guard")
        return guard if isinstance(guard, dict) else {}

    def tick_s(self) -> float:
        value = self.guard.get("tick_s", 10.0)
        try:
            return max(1.0, float(value))
        except (TypeError, ValueError):
            return 10.0

    def restart_budget(self) -> int:
        value = self.guard.get("restart_budget_per_tick", 1)
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 1

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "kind": "main_computer.allfather_guard_status.v1",
                "network_key": self.manifest.get("network_key"),
                "set_id": self.manifest.get("set_id"),
                "cell_id": self.manifest.get("cell_id"),
                "desired_counts": self.manifest.get("desired_counts") or {},
                "set_desired_counts": self.manifest.get("set_desired_counts") or {},
                "topology": self.manifest.get("topology") or {},
                "desired_up": self.desired_up,
                "drained": self.drained,
                "stop_requested": self.stop_requested,
                "guard": self.guard,
                "processes": [child.to_dict() for child in self.children.values()],
            }

    def identity(self) -> dict[str, Any]:
        identity = self.manifest.get("identity")
        if not isinstance(identity, dict):
            identity = {}
        return {
            "service": identity.get("service", "main-computer-allfather"),
            "role": identity.get("role", "function"),
            "capabilities": identity.get("capabilities", []),
            "network_key": self.manifest.get("network_key"),
            "set_id": self.manifest.get("set_id"),
            "cell_id": self.manifest.get("cell_id"),
            "coolify_server": self.manifest.get("coolify_server"),
            "vpn_ip": self.manifest.get("vpn_ip"),
            "state_root": self.manifest.get("state_root"),
            "host_port_offset": self.manifest.get("host_port_offset", 0),
            "desired_counts": self.manifest.get("desired_counts") or {},
            "set_desired_counts": self.manifest.get("set_desired_counts") or {},
            "topology": self.manifest.get("topology") or {},
            "ports": identity.get("ports", []),
            "guard": self.guard,
        }

    def topology(self) -> dict[str, Any]:
        topology = self.manifest.get("topology")
        if not isinstance(topology, dict):
            topology = {}
        return {
            "network_key": self.manifest.get("network_key"),
            "set_id": self.manifest.get("set_id"),
            "cell_id": self.manifest.get("cell_id"),
            "desired_counts": self.manifest.get("desired_counts") or {},
            "set_desired_counts": self.manifest.get("set_desired_counts") or {},
            "topology": topology,
        }

    def request_down(self) -> None:
        with self.lock:
            self.desired_up = False
            self.drained = True
            self.wake_requested.clear()
            for child in self.children.values():
                self._terminate_child(child)

    def request_up(self) -> None:
        with self.lock:
            self.desired_up = True
            self.drained = False

    def request_wake(self, name: str | None = None) -> None:
        with self.lock:
            if name:
                if name in self.children:
                    self.wake_requested.add(name)
            else:
                self.wake_requested.update(self.children)

    def _terminate_child(self, child: ChildState) -> None:
        if child.process is None or child.process.poll() is not None:
            return
        child.process.terminate()
        try:
            child.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.process.kill()
            child.process.wait(timeout=8)

    def _refresh_exits(self) -> None:
        now = time.time()
        for child in self.children.values():
            if child.process is None:
                continue
            code = child.process.poll()
            if code is None:
                continue
            child.last_exited_at = now
            child.last_exit_code = int(code)
            child.process = None

    def _can_restart(self, child: ChildState, now: float) -> bool:
        cooldown = child.spec.get("restart_cooldown_s", 30.0)
        try:
            cooldown_s = max(0.0, float(cooldown))
        except (TypeError, ValueError):
            cooldown_s = 30.0
        if child.last_started_at and now - child.last_started_at < cooldown_s:
            return False
        return True

    def _start_child(self, child: ChildState) -> bool:
        command = child.spec.get("command")
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            child.last_error = "invalid command"
            return False
        now = time.time()
        try:
            env = os.environ.copy()
            env["MC_ALLFATHER_NETWORK"] = str(self.manifest.get("network_key") or "")
            env["MC_ALLFATHER_SET_ID"] = str(self.manifest.get("set_id") or "")
            env["MC_ALLFATHER_CELL_ID"] = str(self.manifest.get("cell_id") or "")
            env["MC_ALLFATHER_PROCESS_NAME"] = child.name
            child.process = subprocess.Popen(command, cwd="/app", env=env, text=True)
            child.last_started_at = now
            child.restart_count += 1
            child.last_error = ""
            return True
        except Exception as exc:  # pragma: no cover - defensive container runtime path
            child.last_error = f"{type(exc).__name__}: {exc}"
            child.last_exited_at = now
            return False

    def converge_once(self) -> None:
        with self.lock:
            self._refresh_exits()
            if not self.desired_up or self.drained:
                return

            now = time.time()
            budget = self.restart_budget()
            started = 0

            for child in self.children.values():
                if started >= budget:
                    break
                if child.running or not child.desired:
                    continue
                if child.name not in self.wake_requested and not self._can_restart(child, now):
                    continue
                if self._start_child(child):
                    started += 1
                self.wake_requested.discard(child.name)

    def shutdown(self) -> None:
        with self.lock:
            self.stop_requested = True
            for child in self.children.values():
                self._terminate_child(child)


def load_manifest() -> dict[str, Any]:
    raw_b64 = os.environ.get("MC_ALLFATHER_MANIFEST_B64", "").strip()
    if raw_b64:
        try:
            payload = base64.b64decode(raw_b64.encode("ascii"))
            manifest = json.loads(payload.decode("utf-8"))
        except Exception as exc:
            raise SystemExit(f"Could not decode MC_ALLFATHER_MANIFEST_B64: {exc}") from exc
    else:
        path = Path(os.environ.get("MC_ALLFATHER_MANIFEST_PATH") or DEFAULT_MANIFEST_PATH)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SystemExit(f"Manifest file does not exist: {path}") from exc
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Could not parse manifest JSON in {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SystemExit("All-father manifest must be a JSON object.")
    if manifest.get("kind") != "main_computer.allfather_container.v1":
        raise SystemExit(f"Unsupported all-father manifest kind: {manifest.get('kind')!r}")
    return manifest


def make_handler(runtime: GuardRuntime) -> type[BaseHTTPRequestHandler]:
    class GuardHandler(BaseHTTPRequestHandler):
        server_version = "MainComputerAllfatherGuard/1.0"

        def _write_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                self._write_json(HTTPStatus.OK, {"ok": True, "cell_id": runtime.manifest.get("cell_id")})
                return
            if parsed.path == "/identity":
                self._write_json(HTTPStatus.OK, runtime.identity())
                return
            if parsed.path == "/topology":
                self._write_json(HTTPStatus.OK, runtime.topology())
                return
            if parsed.path in {"/status", "/processes"}:
                self._write_json(HTTPStatus.OK, runtime.status())
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/down":
                runtime.request_down()
                self._write_json(HTTPStatus.OK, {"ok": True, "desired_up": False})
                return
            if parsed.path == "/up":
                runtime.request_up()
                self._write_json(HTTPStatus.OK, {"ok": True, "desired_up": True})
                return
            if parsed.path == "/drain":
                runtime.request_down()
                self._write_json(HTTPStatus.OK, {"ok": True, "drained": True})
                return
            if parsed.path == "/wake":
                name = (query.get("name") or [""])[0].strip() or None
                runtime.request_wake(name)
                self._write_json(HTTPStatus.OK, {"ok": True, "wake": name or "all"})
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stdout.write("[guard-http] " + (fmt % args) + "\n")
            sys.stdout.flush()

    return GuardHandler


def main() -> int:
    manifest = load_manifest()
    runtime = GuardRuntime(manifest)
    port = int((manifest.get("guard") or {}).get("container_port") or os.environ.get("MC_ALLFATHER_GUARD_PORT") or 41414)
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(runtime))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Main Computer all-father guard listening on 0.0.0.0:{port}", flush=True)

    def _handle_stop(signum: int, _frame: Any) -> None:
        print(f"Received signal {signum}; shutting down all-father children.", flush=True)
        runtime.shutdown()
        server.shutdown()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    try:
        while not runtime.stop_requested:
            runtime.converge_once()
            time.sleep(runtime.tick_s())
    finally:
        runtime.shutdown()
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
