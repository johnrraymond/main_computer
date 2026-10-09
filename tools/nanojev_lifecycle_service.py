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
        health_poll_seconds: float = 0.5,
        health_request_timeout_seconds: float = 1.5,
        stop_timeout_seconds: float = 10.0,
        docker_command: str = "docker",
        image_name: str = "main-computer/nanojev:managed-v2",
        checkpoint_selector: str = "champion",
        hf_repo: str = "johnrraymond/NanoJev-CLEF",
    ) -> None:
        self.root = root
        self.compose_file = compose_file
        self.project_name = project_name
        self.backend_port = int(backend_port)
        self.start_timeout_seconds = max(0.1, float(start_timeout_seconds))
        self.health_poll_seconds = max(0.05, float(health_poll_seconds))
        self.health_request_timeout_seconds = max(0.05, float(health_request_timeout_seconds))
        self.stop_timeout_seconds = max(0.0, float(stop_timeout_seconds))
        self.docker_command = docker_command
        self.image_name = image_name
        self.checkpoint_selector = str(checkpoint_selector).strip() or "champion"
        self.hf_repo = str(hf_repo).strip() or "johnrraymond/NanoJev-CLEF"
        self.backend_url = f"http://127.0.0.1:{self.backend_port}"
        self.model_loaded_once = False
        self.checkpoint_validated = False
        self.health_error = ""

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["MAIN_COMPUTER_NANOJEV_BIND_PORT"] = str(self.backend_port)
        env["MAIN_COMPUTER_NANOJEV_CHECKPOINT"] = self.checkpoint_selector
        env["MAIN_COMPUTER_NANOJEV_HF_REPO"] = self.hf_repo
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

    def container_id(self) -> str:
        # Compose includes stopped containers only with -a. Its project/service
        # labels ensure we never adopt an unrelated Docker container.
        result = self._compose("ps", "-a", "-q", "nanojev", check=False)
        if result.returncode != 0:
            raise RuntimeError(f"NanoJev container inspection failed ({result.returncode}): {result.stdout.strip()}")
        ids = result.stdout.strip().splitlines()
        if len(ids) > 1:
            raise RuntimeError(f"Expected one NanoJev container, found {len(ids)}")
        return ids[0].strip() if ids else ""

    def container_state(self) -> str:
        """Read Docker state without starting NanoJev, touching its idle timer, or building.

        Compose labels scope this query to our project and service. This is a
        lightweight observation; it must not use `compose up` or `start`.
        """
        command = [
            self.docker_command, "ps", "-a",
            "--filter", f"label=com.docker.compose.project={self.project_name}",
            "--filter", "label=com.docker.compose.service=nanojev",
            "--format", "{{.State}}",
        ]
        result = subprocess.run(
            command, cwd=self.root, env=self._env(), check=False,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=3.0,
        )
        if result.returncode != 0:
            raise RuntimeError(f"NanoJev Docker status failed ({result.returncode}): {result.stdout.strip()}")
        states = [line.strip().lower() for line in result.stdout.splitlines() if line.strip()]
        if len(states) > 1:
            raise RuntimeError(f"Expected one NanoJev container, found {len(states)}")
        return states[0] if states else "absent"

    def image_bootstrap(self) -> dict[str, object] | None:
        # Provisioning is owned by ./start, never by this lazy lifecycle manager.
        status_path = self.root / "runtime" / "start_stop" / "nanojev-image-bootstrap.json"
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(payload, dict) and payload.get("image") == self.image_name:
            return payload
        return None

    def require_image(self) -> None:
        result = subprocess.run(
            [self.docker_command, "image", "inspect", self.image_name],
            cwd=self.root,
            env=self._env(),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.returncode == 0:
            return
        detail = result.stdout.strip()
        if "No such image:" in detail or "No such object:" in detail:
            bootstrap = self.image_bootstrap()
            if bootstrap and bootstrap.get("state") in {"checking", "building"}:
                raise RuntimeError("NanoJev image bootstrap is running in the background; check /control/status")
            if bootstrap and bootstrap.get("state") == "failed":
                raise RuntimeError(f"NanoJev image bootstrap failed: {bootstrap.get('error', 'unknown error')}")
            raise RuntimeError(
                f"NanoJev image {self.image_name!r} is missing; run ./start to launch background image bootstrap"
            )
        raise RuntimeError(f"NanoJev image inspection failed ({result.returncode}): {detail}")

    def health(self) -> bool:
        try:
            payload = _http_json(
                self.backend_url + "/api/health",
                timeout=self.health_request_timeout_seconds,
            )
        except Exception:
            self.model_loaded_once = False
            self.checkpoint_validated = False
            self.health_error = ""
            return False
        if not isinstance(payload, dict):
            self.model_loaded_once = False
            self.checkpoint_validated = False
            self.health_error = "Invalid NanoJev backend health response"
            return False
        self.model_loaded_once = bool(payload.get("model_loaded_once"))
        # Model and checkpoint identity are separate facts; a loaded wrong
        # checkpoint must never open the Tactical AI performance gate.
        if self.checkpoint_selector == "unified-games-v1":
            observed = payload.get("checkpoint_selector")
            identity_ok = observed in (None, "unified-games-v1")
        else:
            identity_ok = (
                payload.get("model_family") == "nanojev-clef"
                and payload.get("checkpoint_selector") == self.checkpoint_selector
                and payload.get("checkpoint_repo") == self.hf_repo
            )
        self.checkpoint_validated = bool(payload.get("ready")) and self.model_loaded_once and identity_ok
        self.health_error = (
            f"Checkpoint mismatch: expected {self.hf_repo}/{self.checkpoint_selector}; "
            f"backend reports {payload.get('checkpoint_repo')}/{payload.get('checkpoint_selector')}"
            if self.model_loaded_once and not identity_ok else ""
        )
        return self.checkpoint_validated

    def start(self) -> None:
        if self.health():
            return
        if self.container_id():
            # Never use `up` for an existing container: Compose may recreate it
            # after configuration/image changes, and recreate breaks image pinning.
            result = self._compose("start", "nanojev", check=False)
            operation = "start"
        else:
            # Provision once from an explicitly built image. A lazy game wake-up
            # must never perform an unbounded image build (or implicitly pull).
            self.require_image()
            result = self._compose("up", "-d", "--no-build", "--pull", "never", "nanojev", check=False)
            operation = "up"
        if result.returncode != 0:
            raise RuntimeError(f"NanoJev compose {operation} failed ({result.returncode}): {result.stdout.strip()}")
        deadline = time.monotonic() + self.start_timeout_seconds
        while time.monotonic() < deadline:
            if self.health():
                return
            time.sleep(self.health_poll_seconds)
        raise TimeoutError(f"NanoJev did not become healthy within {self.start_timeout_seconds:.0f} seconds")

    def stop(self) -> None:
        # Stop only the managed service. Never remove its container or image.
        stop_timeout = str(max(0, int(round(self.stop_timeout_seconds))))
        result = self._compose("stop", "--timeout", stop_timeout, "nanojev", check=False)
        if result.returncode != 0:
            raise RuntimeError(f"NanoJev compose stop failed ({result.returncode}): {result.stdout.strip()}")


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
        # Serialize blocking Docker start/stop transitions without holding the
        # state lock.  /control/status must remain responsive while Compose is
        # starting or stopping the backend.
        self.transition_lock = threading.Lock()
        self.pinned = False
        self.dirty = False
        self.active_requests = 0
        self.last_activity = self.clock()
        self.running = controller.health()
        self.last_error = ""
        self.phase = "ready" if self.running else "idle"
        self.shutdown_complete = False
        # Docker observation is deliberately asynchronous. A stalled docker.exe
        # must never stall the manager's /control/status endpoint or the game UI.
        self._container_state = "unknown"
        self._container_error = ""
        self._container_probe_started_at = float("-inf")
        self._container_probe_active = False

    def _probe_container_state(self) -> None:
        state, error = "unknown", ""
        try:
            state = str(self.controller.container_state())
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        with self.lock:
            self._container_state = state
            self._container_error = error
            self._container_probe_active = False

    def _schedule_container_probe(self) -> None:
        if not hasattr(self.controller, "container_state"):
            return
        with self.lock:
            now = time.monotonic()
            if self._container_probe_active or now - self._container_probe_started_at < 2.0:
                return
            self._container_probe_started_at = now
            self._container_probe_active = True
        threading.Thread(target=self._probe_container_state, name="nanojev-docker-status", daemon=True).start()

    def _refresh_running(self) -> bool:
        # A transition owns the authoritative state while Docker is changing it.
        # Do not perform a second blocking backend probe or overwrite the visible
        # starting/stopping phase from a concurrent /control/status request.
        with self.lock:
            if self.phase in {"starting", "stopping"}:
                return self.running
        running = self.controller.health()
        with self.lock:
            if self.phase in {"starting", "stopping"}:
                return self.running
            self.running = running
            self.phase = "ready" if running else ("error" if self.last_error else "idle")
            if not running:
                self.dirty = False
                self.active_requests = 0
        return running

    def ensure_on(self, *, pin: bool = False) -> None:
        with self.transition_lock:
            with self.lock:
                if pin:
                    self.pinned = True
                self.dirty = False
                self.last_activity = self.clock()
                already_running = self.running
            if already_running and self.controller.health():
                return
            with self.lock:
                self.phase = "starting"
                self.last_error = ""
            try:
                self.controller.start()
            except Exception as exc:
                with self.lock:
                    self.running = False
                    self.phase = "error"
                    self.last_error = str(exc)
                raise
            else:
                with self.lock:
                    self.running = True
                    self.phase = "ready"
                    self.last_error = ""
                    self.last_activity = self.clock()

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

        # Serialize the backend transition, then re-check the idle predicate: a
        # request may have arrived while we were waiting for another transition.
        with self.transition_lock:
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
                self.phase = "stopping"
                self.last_error = ""
            try:
                self.controller.stop()
            except Exception as exc:
                with self.lock:
                    self.phase = "error"
                    self.last_error = str(exc)
                return False
            else:
                with self.lock:
                    self.running = False
                    self.dirty = False
                    self.phase = "idle"
                    self.last_error = ""
                return True

    def shutdown_now(self) -> None:
        # Stop the backend even if it is unhealthy or partially started, but
        # preserve the Compose container/image across manager generations.
        # Keep the state lock free so control/status remains observable.
        with self.transition_lock:
            with self.lock:
                if self.shutdown_complete:
                    return
                self.pinned = False
                self.dirty = False
                self.phase = "stopping"
                self.last_error = ""
            try:
                self.controller.stop()
            except Exception as exc:
                with self.lock:
                    self.phase = "error"
                    self.last_error = str(exc)
                raise
            else:
                with self.lock:
                    self.running = False
                    self.active_requests = 0
                    self.phase = "idle"
                    self.last_error = ""
                    self.shutdown_complete = True

    def status(self, *, refresh: bool = True) -> dict[str, object]:
        if refresh:
            self._schedule_container_probe()
            self._refresh_running()
        with self.lock:
            now = self.clock()
            elapsed = max(0.0, now - self.last_activity)
            remaining = None
            if self.running and self.dirty and not self.pinned and self.active_requests == 0:
                remaining = max(0.0, self.idle_seconds - elapsed)
            bootstrap = self.controller.image_bootstrap() if hasattr(self.controller, "image_bootstrap") else None
            phase = self.phase
            if not self.running and phase in {"idle", "error"} and bootstrap and bootstrap.get("state") in {"checking", "building"}:
                phase = "building"
            container = self._container_state
            model_loaded = bool(getattr(self.controller, "model_loaded_once", self.running))
            checkpoint_validated = bool(getattr(self.controller, "checkpoint_validated", self.running)) and self.running
            backend_error = str(getattr(self.controller, "health_error", ""))
            if self.phase == "error":
                runtime_state = "error"
            elif self.phase in {"starting", "stopping"}:
                runtime_state = self.phase
            elif checkpoint_validated:
                runtime_state = "busy" if self.active_requests else "ready"
            elif model_loaded and backend_error:
                runtime_state = "error"
            elif container == "running":
                runtime_state = "loading"
            elif phase == "building":
                runtime_state = "building"
            elif self._container_error:
                runtime_state = "error"
            elif container in {"created", "absent"}:
                runtime_state = container
            elif container in {"exited", "stopped", "dead"}:
                runtime_state = "stopped"
            else:
                runtime_state = "unknown"
            return {
                "ok": True,
                "mode": "lazy-managed",
                "phase": phase,
                "runtime_state": runtime_state,
                "container_state": container,
                "container_exists": None if container == "unknown" else container != "absent",
                "container_running": container == "running" if container != "unknown" else None,
                "model_loaded": model_loaded,
                "checkpoint_validated": checkpoint_validated,
                "backend_error": backend_error,
                "container_error": self._container_error,
                "image_bootstrap": bootstrap,
                "running": self.running,
                "backend_ready": self.running and self.phase == "ready",
                "pinned": self.pinned,
                "dirty": self.dirty,
                "active_requests": self.active_requests,
                "idle_timeout_seconds": self.idle_seconds,
                "idle_remaining_seconds": remaining,
                "backend_url": self.controller.backend_url,
                "checkpoint_selector": getattr(self.controller, "checkpoint_selector", None),
                "checkpoint_repo": getattr(self.controller, "hf_repo", None),
                "last_error": self.last_error,
            }


class NanoJevManagerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        lifecycle: NanoJevLifecycle,
        *,
        proxy_timeout_seconds: float,
    ) -> None:
        super().__init__(address, NanoJevManagerHandler)
        self.lifecycle = lifecycle
        self.proxy_timeout_seconds = max(0.1, float(proxy_timeout_seconds))


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
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Control/status clients are intentionally allowed to use short timeouts.
            # A client disappearing after headers is not a manager failure.
            self.close_connection = True

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
                with urllib.request.urlopen(request, timeout=self.server.proxy_timeout_seconds) as response:
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
            except Exception as exc:
                self._send_json(503, {"ok": False, "error": str(exc)})
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
    parser.add_argument("--start-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--health-poll-seconds", type=float, default=0.5)
    parser.add_argument("--health-request-timeout-seconds", type=float, default=1.5)
    parser.add_argument("--stop-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--proxy-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--docker-command", default="docker")
    parser.add_argument("--image-name", default="main-computer/nanojev:managed-v2")
    parser.add_argument(
        "--checkpoint",
        default=os.environ.get("MAIN_COMPUTER_NANOJEV_CHECKPOINT", "champion"),
        help="CLEF Hugging Face revision/tag/branch; use unified-games-v1 for the legacy runtime",
    )
    parser.add_argument(
        "--hf-repo",
        default=os.environ.get("MAIN_COMPUTER_NANOJEV_HF_REPO", "johnrraymond/NanoJev-CLEF"),
    )
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
        health_poll_seconds=args.health_poll_seconds,
        health_request_timeout_seconds=args.health_request_timeout_seconds,
        stop_timeout_seconds=args.stop_timeout_seconds,
        docker_command=args.docker_command,
        image_name=args.image_name,
        checkpoint_selector=args.checkpoint,
        hf_repo=args.hf_repo,
    )
    lifecycle = NanoJevLifecycle(controller, idle_seconds=args.idle_seconds)
    server = NanoJevManagerServer(
        (args.listen_host, args.listen_port),
        lifecycle,
        proxy_timeout_seconds=args.proxy_timeout_seconds,
    )
    _start_sweeper(server, max(0.1, float(args.sweep_interval_seconds)))
    print(
        f"NanoJev lazy manager listening at http://{args.listen_host}:{args.listen_port}; "
        f"backend={controller.backend_url}; idle={args.idle_seconds:g}s; "
        f"start_timeout={args.start_timeout_seconds:g}s; "
        f"health_request_timeout={args.health_request_timeout_seconds:g}s; "
        f"stop_timeout={args.stop_timeout_seconds:g}s; "
        f"checkpoint={controller.checkpoint_selector}; repo={controller.hf_repo}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        try:
            lifecycle.shutdown_now()
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
