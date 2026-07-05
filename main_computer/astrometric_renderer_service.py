from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_RENDERER_PORT = 8794
DEFAULT_RENDERER_HOST = "127.0.0.1"
COMPOSE_FILENAME = "docker-compose.astrometric.yml"


@dataclass(frozen=True)
class RendererHttpResponse:
    status: int
    content_type: str
    body: bytes
    headers: dict[str, str]


class AstrometricRendererError(RuntimeError):
    """Raised when the Docker-backed astrometric renderer cannot be controlled."""


class AstrometricRendererService:
    """Backend control plane for the Dockerized C++ astrometric renderer.

    The browser never talks to the renderer container directly. Main Computer owns
    Docker lifecycle, reverse-proxies frames, and forwards mouse/camera events to
    the renderer process running inside the GPU-aware container.
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.host = os.environ.get("ASTROMETRIC_RENDERER_HOST", DEFAULT_RENDERER_HOST).strip() or DEFAULT_RENDERER_HOST
        self.port = int(os.environ.get("ASTROMETRIC_RENDERER_PORT", str(DEFAULT_RENDERER_PORT)) or DEFAULT_RENDERER_PORT)
        self.compose_file = self.repo_root / COMPOSE_FILENAME

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _split_command_override(self, value: str) -> list[str]:
        try:
            return shlex.split(value)
        except ValueError:
            return value.split()

    def _docker_compose_base(self) -> list[str]:
        override = os.environ.get("MAIN_COMPUTER_DOCKER_COMPOSE", "").strip()
        if override:
            return self._split_command_override(override)

        try:
            completed = subprocess.run(
                ["docker", "compose", "version"],
                cwd=self.repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=8,
                check=False,
            )
            if completed.returncode == 0:
                return ["docker", "compose"]
        except (OSError, subprocess.TimeoutExpired):
            pass
        return ["docker-compose"]

    def _compose_command(self, *args: str) -> list[str]:
        return [
            *self._docker_compose_base(),
            "-f",
            str(self.compose_file),
            *args,
        ]

    def _renderer_env(self) -> dict[str, str]:
        env = {**os.environ, "ASTROMETRIC_RENDERER_PORT": str(self.port)}
        env.setdefault("ASTROMETRIC_RENDERER_WIDTH", "800")
        env.setdefault("ASTROMETRIC_RENDERER_HEIGHT", "450")
        env.setdefault("ASTROMETRIC_RENDERER_FPS", "12")
        env.setdefault("ASTROMETRIC_RENDERER_IDLE_STEPS", "960")
        env.setdefault("ASTROMETRIC_RENDERER_MOVING_STEPS", "520")
        return env

    def _run_compose(self, *args: str, timeout: float = 180.0) -> dict[str, Any]:
        if not self.compose_file.exists():
            raise AstrometricRendererError(f"Missing {COMPOSE_FILENAME}; apply the astrometric renderer patch first.")

        command = self._compose_command(*args)
        started = time.time()
        try:
            completed = subprocess.run(
                command,
                cwd=self.repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
                env=self._renderer_env(),
            )
        except FileNotFoundError as exc:
            raise AstrometricRendererError("Docker Compose was not found. Install Docker Desktop/Compose or set MAIN_COMPUTER_DOCKER_COMPOSE.") from exc
        except subprocess.TimeoutExpired as exc:
            raise AstrometricRendererError(f"Docker Compose timed out while running: {' '.join(command)}") from exc

        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:],
            "elapsed_s": round(time.time() - started, 3),
        }

    def _try_compose(self, *args: str, timeout: float = 12.0) -> dict[str, Any]:
        try:
            return self._run_compose(*args, timeout=timeout)
        except Exception as exc:
            return {"returncode": 1, "error": str(exc), "command": self._compose_command(*args)}

    def _renderer_request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        timeout: float = 2.0,
        headers: dict[str, str] | None = None,
    ) -> RendererHttpResponse:
        url = f"{self.base_url}{path}"
        request_headers = {"Connection": "close", **(headers or {})}
        req = Request(url, data=body, method=method, headers=request_headers)
        try:
            with urlopen(req, timeout=timeout) as response:
                return RendererHttpResponse(
                    status=int(response.status),
                    content_type=response.headers.get("Content-Type", "application/octet-stream"),
                    body=response.read(),
                    headers={key: value for key, value in response.headers.items()},
                )
        except HTTPError as exc:
            return RendererHttpResponse(
                status=int(exc.code),
                content_type=exc.headers.get("Content-Type", "text/plain"),
                body=exc.read(),
                headers={key: value for key, value in exc.headers.items()},
            )
        except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
            raise AstrometricRendererError(str(exc)) from exc

    def renderer_health(self, *, timeout: float = 1.5) -> dict[str, Any]:
        try:
            response = self._renderer_request("/health", timeout=timeout)
            payload = json.loads(response.body.decode("utf-8"))
            if isinstance(payload, dict):
                stream_ready = bool(payload.get("stream_ready") or int(payload.get("frame_seq") or 0) > 0)
                return {"reachable": True, "stream_ready": stream_ready, **payload}
        except Exception as exc:
            return {"reachable": False, "stream_ready": False, "error": str(exc)}
        return {"reachable": False, "stream_ready": False, "error": "Renderer returned an invalid health payload."}

    def _wait_for_renderer(self, *, timeout: float = 60.0) -> dict[str, Any]:
        """Wait until the C++ process is answering health and has produced a frame.

        A listening HTTP port is not enough for the app: the browser needs a live
        MJPEG stream.  The returned payload is the best observed status so the UI
        can show an honest "starting" or error state instead of a false ready pill.
        """

        deadline = time.time() + max(1.0, timeout)
        best: dict[str, Any] = {"reachable": False, "stream_ready": False, "error": "renderer did not answer before wait started"}
        while time.time() < deadline:
            health = self.renderer_health(timeout=2.0)
            best = health
            if health.get("reachable") and health.get("stream_ready"):
                return health
            last_error = str(health.get("last_error") or "").strip()
            if health.get("reachable") and last_error and not health.get("stream_ready"):
                # Keep polling briefly; the renderer reports transient GL failures
                # through last_error while the container may still recover.
                pass
            time.sleep(1.0)
        return best

    def _diagnostics(self) -> dict[str, Any]:
        if not self.compose_file.exists():
            return {"compose_present": False}
        return {
            "compose_present": True,
            "ps": self._try_compose("ps", "astrometric-renderer", timeout=10),
            "logs": self._try_compose("logs", "--tail", "120", "astrometric-renderer", timeout=10),
        }

    def status(self) -> dict[str, Any]:
        health = self.renderer_health()
        compose_present = self.compose_file.exists()
        docker_available = True
        docker_error = ""
        try:
            base = self._docker_compose_base()
            completed = subprocess.run(
                [*base, "version"] if base[-1:] == ["compose"] else [base[0], "--version"],
                cwd=self.repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
            docker_available = completed.returncode == 0
            docker_error = (completed.stderr or completed.stdout).strip()
        except Exception as exc:
            docker_available = False
            docker_error = str(exc)

        renderer = {
            "host": self.host,
            "port": self.port,
            "base_url": self.base_url,
            **health,
        }
        return {
            "ok": True,
            "compose_file": str(self.compose_file),
            "compose_present": compose_present,
            "renderer": renderer,
            "docker": {
                "available": docker_available,
                "error": docker_error,
                "compose_command": self._docker_compose_base(),
            },
            "stream_path": "/api/applications/astrometric/stream.mjpg",
            "frame_path": "/api/applications/astrometric/frame.jpg",
            "camera_path": "/api/applications/astrometric/camera",
        }

    def action(self, action: str) -> dict[str, Any]:
        action = str(action or "").strip().lower()
        diagnostics: dict[str, Any] | None = None
        wait_health: dict[str, Any] | None = None

        if action == "status":
            return self.status()
        if action == "start":
            result = self._run_compose("up", "-d", "--build", "astrometric-renderer")
            if result.get("returncode", 1) == 0:
                wait_health = self._wait_for_renderer(timeout=75.0)
        elif action == "stop":
            result = self._run_compose("down", "--remove-orphans", timeout=90)
        elif action == "restart":
            down = self._run_compose("down", "--remove-orphans", timeout=90)
            up = self._run_compose("up", "-d", "--build", "astrometric-renderer")
            result = {"down": down, "up": up, "returncode": up.get("returncode", 1)}
            if result.get("returncode", 1) == 0:
                wait_health = self._wait_for_renderer(timeout=75.0)
        else:
            raise AstrometricRendererError(f"Unsupported astrometric renderer action: {action or 'empty'}")

        status = self.status()
        compose_ok = result.get("returncode", 0) == 0
        renderer_ready = bool(status.get("renderer", {}).get("stream_ready"))
        ok = compose_ok and (action == "stop" or renderer_ready)
        if action in {"start", "restart"} and not renderer_ready:
            diagnostics = self._diagnostics()

        message = ""
        if action in {"start", "restart"} and compose_ok and not renderer_ready:
            renderer = status.get("renderer", {})
            reason = renderer.get("last_error") or renderer.get("error") or "renderer did not produce a frame before the startup timeout"
            message = f"Renderer container started, but the GPU stream is not ready yet: {reason}"
        elif not compose_ok:
            message = "Docker Compose command failed."

        return {
            "ok": ok,
            "action": action,
            "message": message,
            "result": result,
            "wait_health": wait_health,
            "status": status,
            "diagnostics": diagnostics,
        }

    def send_camera(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        response = self._renderer_request(
            "/camera",
            method="POST",
            body=body,
            timeout=2.0,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            parsed = json.loads(response.body.decode("utf-8"))
        except Exception:
            parsed = {"ok": response.status < 400, "body": response.body.decode("utf-8", errors="replace")}
        return {"status": response.status, **parsed}

    def fetch_frame(self) -> RendererHttpResponse:
        return self._renderer_request("/frame.jpg", timeout=4.0)

    def open_stream(self, timeout: float = 8.0):
        req = Request(f"{self.base_url}/stream.mjpg", method="GET", headers={"Connection": "close"})
        return urlopen(req, timeout=timeout)
