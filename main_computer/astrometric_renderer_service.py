from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from main_computer.container_runtime import resolve_container_runtime, split_command_override


DEFAULT_RENDERER_PORT = 8794
DEFAULT_RENDERER_HOST = "127.0.0.1"
COMPOSE_FILENAME = "docker-compose.astrometric.yml"
COMPOSE_PROJECT_NAME = "main-computer-astrometric"
CONTAINER_NAME = "main-computer-astrometric-renderer"


@dataclass(frozen=True)
class RendererHttpResponse:
    status: int
    content_type: str
    body: bytes
    headers: dict[str, str]


class AstrometricRendererError(RuntimeError):
    """Raised when the container-backed astrometric renderer cannot be controlled."""


class AstrometricRendererService:
    """Backend control plane for the containerized C++ astrometric renderer.

    The browser never talks to the renderer container directly. Main Computer owns
    container lifecycle, reverse-proxies frames, and forwards mouse/camera events to
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

    def _container_runtime(self):
        return resolve_container_runtime(cwd=self.repo_root)

    def _split_command_override(self, value: str) -> list[str]:
        return split_command_override(value)

    def _docker_compose_base(self) -> list[str]:
        return list(self._container_runtime().compose_command)

    def _compose_command(self, *args: str) -> list[str]:
        return [
            *self._docker_compose_base(),
            "-p",
            COMPOSE_PROJECT_NAME,
            "-f",
            str(self.compose_file),
            *args,
        ]

    def _renderer_env(self, overrides: dict[str, str] | None = None) -> dict[str, str]:
        env = {**os.environ, "ASTROMETRIC_RENDERER_PORT": str(self.port)}
        env.setdefault("ASTROMETRIC_RENDERER_WIDTH", "640")
        env.setdefault("ASTROMETRIC_RENDERER_HEIGHT", "360")
        env.setdefault("ASTROMETRIC_RENDERER_FPS", "10")
        env.setdefault("ASTROMETRIC_RENDERER_IDLE_STEPS", "1900")
        env.setdefault("ASTROMETRIC_RENDERER_MOVING_STEPS", "800")
        env.setdefault("ASTROMETRIC_RENDERER_IDLE_STEP_LENGTH", "1.5e8")
        env.setdefault("ASTROMETRIC_RENDERER_MOVING_STEP_LENGTH", "1.8e8")
        env.setdefault("ASTROMETRIC_RENDERER_MODE", "gpu")
        env.setdefault("ASTROMETRIC_RENDERER_BACKEND", "cuda")
        if overrides:
            env.update({str(key): str(value) for key, value in overrides.items()})
        return env

    def _run_compose(
        self,
        *args: str,
        timeout: float = 180.0,
        env_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
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
                env=self._renderer_env(env_overrides),
            )
        except FileNotFoundError as exc:
            raise AstrometricRendererError("Container Compose was not found. Install Docker Desktop/Compose or Podman Compose, or set MAIN_COMPUTER_CONTAINER_COMPOSE_COMMAND.") from exc
        except subprocess.TimeoutExpired as exc:
            raise AstrometricRendererError(f"Container Compose timed out while running: {' '.join(command)}") from exc

        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:],
            "elapsed_s": round(time.time() - started, 3),
        }

    def _try_compose(
        self,
        *args: str,
        timeout: float = 12.0,
        env_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            return self._run_compose(*args, timeout=timeout, env_overrides=env_overrides)
        except Exception as exc:
            return {"returncode": 1, "error": str(exc), "command": self._compose_command(*args)}

    def _docker_base(self) -> list[str]:
        return list(self._container_runtime().container_command)

    def _run_direct(
        self,
        command: list[str],
        *,
        timeout: float = 12.0,
        env_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
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
                env=self._renderer_env(env_overrides),
            )
            return {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-12000:],
                "stderr": completed.stderr[-12000:],
                "elapsed_s": round(time.time() - started, 3),
            }
        except FileNotFoundError as exc:
            return {"command": command, "returncode": 127, "error": str(exc), "elapsed_s": round(time.time() - started, 3)}
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            return {
                "command": command,
                "returncode": 124,
                "error": f"timed out after {timeout}s",
                "stdout": stdout[-12000:],
                "stderr": stderr[-12000:],
                "elapsed_s": round(time.time() - started, 3),
            }

    def _try_docker(self, *args: str, timeout: float = 12.0) -> dict[str, Any]:
        return self._run_direct([*self._docker_base(), *args], timeout=timeout)

    def _container_lifecycle(self) -> dict[str, Any]:
        """Report the astrometric container state without starting or stopping it.

        The Astrometric page is the lifecycle control surface.  Status probes are
        read-only and container start/stop actions are scoped to the dedicated
        Compose project so this feature cannot accidentally tear down unrelated
        Main Computer containers that share the same repository directory.
        """

        lifecycle: dict[str, Any] = {
            "container": CONTAINER_NAME,
            "compose_project": COMPOSE_PROJECT_NAME,
            "controlled_by_page": True,
            "running": False,
            "state": "not_created",
            "restart_policy": "no",
        }
        if not self.compose_file.exists():
            lifecycle["state"] = "compose_missing"
            return lifecycle

        inspect = self._run_direct(
            [*self._docker_base(), "inspect", "-f", "{{json .State}}", CONTAINER_NAME],
            timeout=3.0,
        )
        lifecycle["inspect_returncode"] = inspect.get("returncode")
        if inspect.get("returncode") != 0:
            error = (inspect.get("stderr") or inspect.get("stdout") or inspect.get("error") or "").strip()
            if error:
                lifecycle["error"] = error[-1000:]
            return lifecycle

        raw = str(inspect.get("stdout") or "").strip()
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            lifecycle["state"] = "unknown"
            lifecycle["error"] = raw[-1000:] or "container inspect returned invalid state JSON"
            return lifecycle

        if isinstance(state, dict):
            lifecycle["state"] = str(state.get("Status") or "unknown")
            lifecycle["running"] = bool(state.get("Running"))
            lifecycle["started_at"] = state.get("StartedAt")
            lifecycle["finished_at"] = state.get("FinishedAt")
            lifecycle["exit_code"] = state.get("ExitCode")
            health = state.get("Health")
            if isinstance(health, dict):
                lifecycle["health"] = health.get("Status")
        return lifecycle


    def _container_absent_error(self, result: dict[str, Any]) -> bool:
        text = "\n".join(
            str(result.get(key) or "")
            for key in ("stdout", "stderr", "error")
        )
        return "No such container" in text or "No such object" in text

    def _wait_for_renderer_stopped(self, *, timeout: float = 10.0) -> dict[str, Any]:
        """Wait until the controlled container is stopped and the renderer port is closed.

        The browser can keep an MJPEG connection alive for a moment after its image
        element is detached.  Treat stop as complete only when the container
        is not running and the local renderer endpoint no longer answers.
        """

        deadline = time.time() + max(0.5, timeout)
        best: dict[str, Any] = {
            "stopped": False,
            "container": self._container_lifecycle(),
            "renderer": self.renderer_health(timeout=0.5),
        }
        while time.time() < deadline:
            container = self._container_lifecycle()
            renderer = self.renderer_health(timeout=0.5)
            tcp_open = bool(renderer.get("tcp_open") or renderer.get("reachable"))
            stopped = not bool(container.get("running")) and not tcp_open
            best = {"stopped": stopped, "container": container, "renderer": renderer}
            if stopped:
                return best
            time.sleep(0.25)
        return best

    def _stop_renderer_container(self) -> dict[str, Any]:
        """Stop the Astrometric renderer even if it was started by an older project name.

        The page-scoped Compose project is the normal control path, but earlier
        test iterations and manual starts may leave a container with the fixed
        name and different Compose labels.  Compose ``down`` will not remove that
        mismatched project container, so follow it with a direct ``docker rm -f``
        for the single fixed Astrometric renderer container name.
        """

        compose_down = self._try_compose("down", "--remove-orphans", timeout=90)
        force_remove = self._try_docker("rm", "-f", CONTAINER_NAME, timeout=30)
        stopped = self._wait_for_renderer_stopped(timeout=10.0)

        compose_ok = compose_down.get("returncode") == 0
        remove_ok = force_remove.get("returncode") == 0 or self._container_absent_error(force_remove)
        returncode = 0 if (compose_ok or remove_ok) and stopped.get("stopped") else 1
        return {
            "returncode": returncode,
            "compose_down": compose_down,
            "force_remove_container": force_remove,
            "stopped": stopped,
        }

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

    def _tcp_probe(self, *, timeout: float = 0.5) -> dict[str, Any]:
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return {"tcp_open": True}
        except Exception as exc:
            return {"tcp_open": False, "tcp_error": str(exc)}

    def renderer_health(self, *, timeout: float = 1.5) -> dict[str, Any]:
        try:
            response = self._renderer_request("/health", timeout=timeout)
            payload = json.loads(response.body.decode("utf-8"))
            if isinstance(payload, dict):
                stream_ready = bool(payload.get("stream_ready") or int(payload.get("frame_seq") or 0) > 0)
                return {"reachable": True, "stream_ready": stream_ready, **payload}
        except Exception as exc:
            return {"reachable": False, "stream_ready": False, "error": str(exc), **self._tcp_probe()}
        return {"reachable": False, "stream_ready": False, "error": "Renderer returned an invalid health payload.", **self._tcp_probe()}

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

    def _diagnostics(self, *, include_config: bool = False, include_exec_probe: bool = False) -> dict[str, Any]:
        """Collect enough evidence to distinguish container runtime, port, and GPU failures.

        This intentionally avoids pulling any new images.  It inspects the Compose
        project, the expected renderer container, and the already-built local image
        so a Windows/Docker/GPU problem can be diagnosed from the Astrometric UI.
        """

        probes: list[dict[str, Any]] = []
        for _ in range(3):
            health = self.renderer_health(timeout=0.75)
            probes.append(health)
            if health.get("reachable"):
                break
            time.sleep(0.15)

        diagnostics: dict[str, Any] = {
            "compose_present": self.compose_file.exists(),
            "repo_root": str(self.repo_root),
            "compose_file": str(self.compose_file),
            "renderer_base_url": self.base_url,
            "health_probes": probes,
            "tcp_probe": self._tcp_probe(timeout=0.75),
        }
        if not self.compose_file.exists():
            return diagnostics

        diagnostics.update(
            {
                "compose_ps": self._try_compose("ps", "-a", "astrometric-renderer", timeout=10),
                "compose_logs": self._try_compose("logs", "--tail", "220", "astrometric-renderer", timeout=10),
                "docker_ps": self._try_docker(
                    "ps",
                    "-a",
                    "--filter",
                    "name=main-computer-astrometric-renderer",
                    "--no-trunc",
                    timeout=10,
                ),
                "docker_logs": self._try_docker("logs", "--tail", "220", CONTAINER_NAME, timeout=10),
                "docker_port": self._try_docker("port", CONTAINER_NAME, timeout=8),
                "docker_inspect_state": self._try_docker(
                    "inspect",
                    "--format",
                    "{{json .State}}",
                    CONTAINER_NAME,
                    timeout=8,
                ),
                "docker_inspect_health": self._try_docker(
                    "inspect",
                    "--format",
                    "{{json .State.Health}}",
                    CONTAINER_NAME,
                    timeout=8,
                ),
                "docker_image": self._try_docker("image", "inspect", "main-computer/astrometric-renderer:local", timeout=8),
                "docker_info": self._try_docker("info", "--format", "{{json .Runtimes}}", timeout=8),
            }
        )
        diagnostics.update(
            {
                "container_ps": diagnostics.get("docker_ps"),
                "container_logs": diagnostics.get("docker_logs"),
                "container_port": diagnostics.get("docker_port"),
                "container_inspect_state": diagnostics.get("docker_inspect_state"),
                "container_inspect_health": diagnostics.get("docker_inspect_health"),
                "container_image": diagnostics.get("docker_image"),
                "container_info": diagnostics.get("docker_info"),
            }
        )
        if include_config:
            diagnostics["compose_config"] = self._try_compose("config", timeout=12)
        if include_exec_probe:
            diagnostics["container_probe"] = self._try_docker(
                "exec",
                CONTAINER_NAME,
                "sh",
                "-lc",
                "set -x; pwd; id; printenv | sort | grep -E 'ASTROMETRIC|NVIDIA|CUDA' || true; "
                "ls -l /dev/nvidia* 2>&1 || true; "
                "nvidia-smi -L 2>&1 || true; "
                "python3 - <<'PY' 2>/dev/null || true\nimport ctypes\nfor lib in ['libcuda.so.1', 'libcudart.so']:\n    try:\n        ctypes.CDLL(lib); print(lib, 'ok')\n    except Exception as exc:\n        print(lib, exc)\nPY\n"
                "ldconfig -p 2>/dev/null | grep -E 'libcuda|libcudart|libnvidia' | head -80 || true; "
                "ps -ef",
                timeout=12,
            )
        return diagnostics

    def diagnostics(self) -> dict[str, Any]:
        return {
            "ok": True,
            "status": self.status(),
            "diagnostics": self._diagnostics(include_config=True, include_exec_probe=True),
            "recommended_next_steps": [
                "If compose_ps/container_ps show the container exited, read compose_logs/container_logs first.",
                "If tcp_probe is false but the container is running, check container_port and the HTTP server log line.",
                "Use action=start-smoke to test container port mapping and Main Computer streaming without CUDA.",
                "If start-smoke streams but start-gpu does not, the failure is in CUDA/NVIDIA container runtime/GPU-kernel startup.",
            ],
        }

    def status(self) -> dict[str, Any]:
        health = self.renderer_health()
        compose_present = self.compose_file.exists()
        docker_available = True
        docker_error = ""
        docker_version = ""
        runtime = self._container_runtime()
        compose_base = list(runtime.compose_command)
        container_base = list(runtime.container_command)
        try:
            completed = subprocess.run(
                [*compose_base, "version"],
                cwd=self.repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
            docker_available = completed.returncode == 0
            docker_version = (completed.stdout or completed.stderr).strip()
            docker_error = "" if docker_available else (completed.stderr or completed.stdout).strip()
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
                "version": docker_version,
                "runtime": runtime.runtime,
                "runtime_source": runtime.source,
                "container_command": container_base,
                "compose_command": compose_base,
                "compose_project": COMPOSE_PROJECT_NAME,
                "container": self._container_lifecycle(),
            },
            "container_runtime": runtime.as_dict(),
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
        if action == "diagnose":
            return self.diagnostics()

        start_env: dict[str, str] | None = None
        if action in {"start", "start-gpu"}:
            action = "start"
            start_env = {"ASTROMETRIC_RENDERER_MODE": "gpu", "ASTROMETRIC_RENDERER_BACKEND": "cuda"}
            result = self._run_compose(
                "up",
                "-d",
                "--build",
                "--force-recreate",
                "astrometric-renderer",
                env_overrides=start_env,
            )
            if result.get("returncode", 1) == 0:
                wait_health = self._wait_for_renderer(timeout=75.0)
        elif action in {"start-smoke", "smoke", "port-smoke"}:
            action = "start-smoke"
            start_env = {
                "ASTROMETRIC_RENDERER_MODE": "smoke",
                "ASTROMETRIC_RENDERER_WIDTH": "480",
                "ASTROMETRIC_RENDERER_HEIGHT": "270",
                "ASTROMETRIC_RENDERER_FPS": "6",
            }
            result = self._run_compose(
                "up",
                "-d",
                "--build",
                "--force-recreate",
                "astrometric-renderer",
                env_overrides=start_env,
            )
            if result.get("returncode", 1) == 0:
                wait_health = self._wait_for_renderer(timeout=40.0)
        elif action == "stop":
            result = self._stop_renderer_container()
        elif action == "restart":
            down = self._run_compose("down", "--remove-orphans", timeout=90)
            up = self._run_compose(
                "up",
                "-d",
                "--build",
                "--force-recreate",
                "astrometric-renderer",
                env_overrides={"ASTROMETRIC_RENDERER_MODE": "gpu", "ASTROMETRIC_RENDERER_BACKEND": "cuda"},
            )
            result = {"down": down, "up": up, "returncode": up.get("returncode", 1)}
            if result.get("returncode", 1) == 0:
                wait_health = self._wait_for_renderer(timeout=75.0)
        else:
            raise AstrometricRendererError(f"Unsupported astrometric renderer action: {action or 'empty'}")

        status = self.status()
        compose_ok = result.get("returncode", 0) == 0
        renderer = status.get("renderer", {})
        lifecycle = status.get("docker", {}).get("container", {})
        renderer_ready = bool(renderer.get("stream_ready"))
        renderer_reachable = bool(renderer.get("reachable") or renderer.get("tcp_open"))
        if action == "stop":
            ok = compose_ok and not bool(lifecycle.get("running")) and not renderer_reachable
        else:
            ok = compose_ok and renderer_ready
        if action in {"start", "restart", "start-smoke"} and not renderer_ready:
            diagnostics = self._diagnostics()

        message = ""
        if action in {"start", "restart", "start-smoke"} and compose_ok and not renderer_ready:
            reason = (
                renderer.get("last_error")
                or renderer.get("startup_phase")
                or renderer.get("error")
                or "renderer did not produce a frame before the startup timeout"
            )
            message = f"Renderer container started, but the stream is not ready yet: {reason}"
        elif action == "stop" and not ok:
            if renderer_reachable:
                message = "Stop command ran, but the renderer port is still reachable. Another renderer process or an older container may still own port 8794."
            else:
                message = "Stop command ran, but Docker still reports the Astrometric renderer container as running."
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
