from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable


JSON_HEADERS = {"Content-Type": "application/json; charset=utf-8"}
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _http_json(url: str, *, method: str = "GET", body: bytes | None = None, timeout: float = 2.0) -> object:
    request = urllib.request.Request(url=url, method=method, data=body)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    return json.loads(payload.decode("utf-8"))


class ComposeNanoJevController:
    def __init__(
        self,
        *,
        root: Path,
        compose_file: Path,
        project_name: str,
        backend_port: int,
        start_timeout_seconds: float,
        docker_command: str = "docker",
        image_name: str = "main-computer/nanojev:unified-games-v1",
    ) -> None:
        self.root = root
        self.compose_file = compose_file
        self.project_name = project_name
        self.backend_port = int(backend_port)
        self.start_timeout_seconds = float(start_timeout_seconds)
        self.docker_command = docker_command
        self.image_name = image_name
        self.backend_url = f"http://127.0.0.1:{self.backend_port}"

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["MAIN_COMPUTER_NANOJEV_BIND_PORT"] = str(self.backend_port)
        return env

    def _compose(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = [
            self.docker_command,
            "compose",
            "--project-name",
            self.project_name,
            "-f",
            str(self.compose_file),
            *args,
        ]
        return subprocess.run(
            command,
            cwd=self.root,
            env=self._env(),
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def image_exists(self) -> bool:
        result = subprocess.run(
            [self.docker_command, "image", "inspect", self.image_name],
            cwd=self.root,
            env=self._env(),
            check=False,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    def ensure_image(self) -> None:
        if self.image_exists():
            return
        result = self._compose("build", "--progress", "plain", "nanojev", check=False)
        if result.returncode != 0:
            raise RuntimeError(f"NanoJev compose build failed ({result.returncode}): {result.stdout.strip()}")

    def health(self) -> bool:
        try:
            payload = _http_json(self.backend_url + "/api/health", timeout=1.5)
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        return bool(payload.get("ready")) and bool(payload.get("model_loaded_once")) and int(payload.get("provider_calls", -1)) == 0

    def start(self) -> None:
        if self.health():
            return
        self.ensure_image()
        result = self._compose("up", "-d", "--no-build", "nanojev", check=False)
        if result.returncode != 0:
            raise RuntimeError(f"NanoJev compose up failed ({result.returncode}): {result.stdout.strip()}")
        deadline = time.monotonic() + self.start_timeout_seconds
        while time.monotonic() < deadline:
            if self.health():
                return
            time.sleep(0.5)
        raise TimeoutError(f"NanoJev did not become healthy within {self.start_timeout_seconds:.0f} seconds")

    def stop(self) -> None:
        result = self._compose("down", "--remove-orphans", check=False)
        if result.returncode != 0:
            raise RuntimeError(f"NanoJev compose down failed ({result.returncode}): {result.stdout.strip()}")


class NanoJevLifecycle:
    def __init__(
        self,
        controller: ComposeNanoJevController,
        *,
        idle_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.controller = controller
        self.idle_seconds = max(0.0, float(idle_seconds))
        self.clock = clock
        self.lock = threading.RLock()
        self.pinned = False
        self.dirty = False
        self.active_requests = 0
        self.last_activity = self.clock()
        self.running = controller.health()
        self.last_error = ""

    def _refresh_running(self) -> bool:
        running = self.controller.health()
        with self.lock:
            self.running = running
            if not running:
                self.dirty = False
                self.active_requests = 0
        return running

    def ensure_on(self, *, pin: bool = False) -> None:
        with self.lock:
            if pin:
                self.pinned = True
            self.dirty = False
            self.last_activity = self.clock()
            already_running = self.running
        if already_running and self.controller.health():
            return
        with self.lock:
            try:
                self.controller.start()
                self.running = True
                self.last_error = ""
                self.last_activity = self.clock()
            except Exception as exc:
                self.running = False
                self.last_error = str(exc)
                raise

    def turn_on(self) -> dict[str, object]:
        self.ensure_on(pin=True)
        return self.status(refresh=False)

    def turn_off(self) -> dict[str, object]:
        with self.lock:
            self.pinned = False
            if self.running:
                self.dirty = True
                self.last_activity = self.clock()
            else:
                self.dirty = False
        return self.status(refresh=False)

    def begin_request(self) -> None:
        self.ensure_on(pin=False)
        with self.lock:
            self.active_requests += 1
            self.dirty = False
            self.last_activity = self.clock()

    def end_request(self) -> None:
        with self.lock:
            self.active_requests = max(0, self.active_requests - 1)
            self.last_activity = self.clock()
            if not self.pinned and self.running and self.active_requests == 0:
                self.dirty = True

    def touch(self) -> dict[str, object]:
        with self.lock:
            if self.running:
                self.last_activity = self.clock()
                if not self.pinned:
                    self.dirty = True
        return self.status(refresh=False)

    def sweep(self) -> bool:
        now = self.clock()
        with self.lock:
            should_stop = (
                self.running
                and self.dirty
                and not self.pinned
                and self.active_requests == 0
                and (now - self.last_activity) >= self.idle_seconds
            )
        if not should_stop:
            return False
        with self.lock:
            try:
                self.controller.stop()
                self.running = False
                self.dirty = False
                self.last_error = ""
                return True
            except Exception as exc:
                self.last_error = str(exc)
                return False

    def shutdown_now(self) -> None:
        with self.lock:
            self.pinned = False
            self.dirty = False
        try:
            if self.running or self.controller.health():
                self.controller.stop()
        finally:
            with self.lock:
                self.running = False
                self.active_requests = 0

    def status(self, *, refresh: bool = True) -> dict[str, object]:
        if refresh:
            self._refresh_running()
        with self.lock:
            now = self.clock()
            elapsed = max(0.0, now - self.last_activity)
            remaining = None
            if self.running and self.dirty and not self.pinned and self.active_requests == 0:
                remaining = max(0.0, self.idle_seconds - elapsed)
            return {
                "ok": True,
                "mode": "lazy-managed",
                "running": self.running,
                "pinned": self.pinned,
                "dirty": self.dirty,
                "active_requests": self.active_requests,
                "idle_timeout_seconds": self.idle_seconds,
                "idle_remaining_seconds": remaining,
                "backend_url": self.controller.backend_url,
                "last_error": self.last_error,
            }


class NanoJevManagerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], lifecycle: NanoJevLifecycle) -> None:
        super().__init__(address, NanoJevManagerHandler)
        self.lifecycle = lifecycle


class NanoJevManagerHandler(BaseHTTPRequestHandler):
    server: NanoJevManagerServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"nanojev-manager {self.address_string()} {fmt % args}", flush=True)

    def _send_json(self, status: int, payload: object) -> None:
        data = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length > 0 else b""

    def _proxy(self) -> None:
        lifecycle = self.server.lifecycle
        body = self._read_body()
        request_started = False
        try:
            lifecycle.begin_request()
            request_started = True
            backend_url = lifecycle.controller.backend_url + self.path
            request = urllib.request.Request(backend_url, data=(body if body else None), method=self.command)
            for name, value in self.headers.items():
                lname = name.lower()
                if lname in HOP_BY_HOP or lname in {"host", "content-length"}:
                    continue
                request.add_header(name, value)
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    payload = response.read()
                    status = int(response.status)
                    headers = list(response.headers.items())
            except urllib.error.HTTPError as exc:
                payload = exc.read()
                status = int(exc.code)
                headers = list(exc.headers.items())
            self.send_response(status)
            for name, value in headers:
                if name.lower() in HOP_BY_HOP or name.lower() == "content-length":
                    continue
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            self._send_json(503, {"ok": False, "error": str(exc)})
        finally:
            if request_started:
                lifecycle.end_request()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/control/status":
            self._send_json(200, self.server.lifecycle.status())
            return
        if self.path.startswith("/api/"):
            self._proxy()
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/control/on":
            try:
                self._send_json(200, self.server.lifecycle.turn_on())
            except Exception as exc:
                self._send_json(503, {"ok": False, "error": str(exc), "status": self.server.lifecycle.status(refresh=False)})
            return
        if self.path == "/control/off":
            self._send_json(200, self.server.lifecycle.turn_off())
            return
        if self.path == "/control/touch":
            self._send_json(200, self.server.lifecycle.touch())
            return
        if self.path == "/control/shutdown":
            try:
                self.server.lifecycle.shutdown_now()
                self._send_json(200, {"ok": True, "state": "shutdown"})
            finally:
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path.startswith("/api/"):
            self._proxy()
            return
        self._send_json(404, {"ok": False, "error": "not found"})


def _start_sweeper(server: NanoJevManagerServer, interval_seconds: float) -> threading.Thread:
    def run() -> None:
        while True:
            time.sleep(interval_seconds)
            try:
                server.lifecycle.sweep()
            except Exception as exc:
                print(f"nanojev-manager sweep failed: {exc}", flush=True)

    thread = threading.Thread(target=run, name="nanojev-idle-sweeper", daemon=True)
    thread.start()
    return thread


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lazy NanoJev Docker lifecycle manager and localhost proxy")
    parser.add_argument("--root", required=True)
    parser.add_argument("--compose-file", required=True)
    parser.add_argument("--project-name", default="main-computer-nanojev")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=9765)
    parser.add_argument("--backend-port", type=int, default=9766)
    parser.add_argument("--idle-seconds", type=float, default=300.0)
    parser.add_argument("--start-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--docker-command", default="docker")
    parser.add_argument("--image-name", default="main-computer/nanojev:unified-games-v1")
    parser.add_argument("--sweep-interval-seconds", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    compose_file = Path(args.compose_file).resolve()
    controller = ComposeNanoJevController(
        root=root,
        compose_file=compose_file,
        project_name=args.project_name,
        backend_port=args.backend_port,
        start_timeout_seconds=args.start_timeout_seconds,
        docker_command=args.docker_command,
        image_name=args.image_name,
    )
    lifecycle = NanoJevLifecycle(controller, idle_seconds=args.idle_seconds)
    server = NanoJevManagerServer((args.listen_host, args.listen_port), lifecycle)
    _start_sweeper(server, max(0.1, float(args.sweep_interval_seconds)))
    print(
        f"NanoJev lazy manager listening at http://{args.listen_host}:{args.listen_port}; "
        f"backend={controller.backend_url}; idle={args.idle_seconds:g}s",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
