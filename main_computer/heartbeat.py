from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None


VIEWPORT_PID_FILENAME = ".main_computer_viewport.pid"
HEARTBEAT_PID_FILENAME = ".main_computer_heartbeat.pid"
VIEWPORT_OUT_LOG = "main_computer_viewport.out.log"
VIEWPORT_ERR_LOG = "main_computer_viewport.err.log"
HEARTBEAT_OUT_LOG = "main_computer_heartbeat.out.log"
HEARTBEAT_ERR_LOG = "main_computer_heartbeat.err.log"


def _control_root_from_env() -> Path | None:
    value = os.environ.get("MAIN_COMPUTER_CONTROL_ROOT", "").strip()
    if not value:
        return None
    try:
        return Path(value).expanduser().resolve()
    except Exception:
        return Path(value).expanduser()


@dataclass(slots=True)
class HeartbeatConfig:
    workspace: Path
    bind_host: str = "127.0.0.1"
    server_port: int = 8765
    heartbeat_port: int | None = None
    python_executable: str = sys.executable
    verbose: bool = True
    control_root: Path | None = None

    @property
    def actual_heartbeat_port(self) -> int:
        return int(self.heartbeat_port if self.heartbeat_port is not None else self.server_port + 1)

    @property
    def actual_control_root(self) -> Path:
        return self.control_root or _control_root_from_env() or self.workspace

    @property
    def viewport_pid_file(self) -> Path:
        return self.actual_control_root / VIEWPORT_PID_FILENAME

    @property
    def heartbeat_pid_file(self) -> Path:
        return self.actual_control_root / HEARTBEAT_PID_FILENAME

    @property
    def viewport_out_log(self) -> Path:
        return self.actual_control_root / VIEWPORT_OUT_LOG

    @property
    def viewport_err_log(self) -> Path:
        return self.actual_control_root / VIEWPORT_ERR_LOG

    @property
    def heartbeat_out_log(self) -> Path:
        return self.actual_control_root / HEARTBEAT_OUT_LOG

    @property
    def heartbeat_err_log(self) -> Path:
        return self.actual_control_root / HEARTBEAT_ERR_LOG


def _read_pid_file(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8", errors="ignore").strip())
    except Exception:
        return None


def _write_pid_file(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(int(pid)), encoding="utf-8")


def _clear_pid_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _pid_is_running(pid: int | None) -> bool:
    if pid in {None, 0}:
        return False
    if psutil is not None:
        try:
            proc = psutil.Process(int(pid))
            proc.status()
            return True
        except Exception:
            return False

    # os.kill(pid, 0) is a useful POSIX existence probe, but it is not a
    # reliable Windows process liveness check.  Without psutil on Windows,
    # leave process ownership unverified instead of deleting PID files or
    # reporting a false DOWN state for a reachable service.
    if sys.platform == "win32":
        return False

    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _launch_detached(command: list[str], *, cwd: Path, stdout_path: Path | None = None, stderr_path: Path | None = None) -> subprocess.Popen[Any]:
    kwargs: dict[str, Any] = {"cwd": cwd}
    stdout_handle = None
    stderr_handle = None
    if stdout_path is not None:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_handle = open(stdout_path, "ab")
        kwargs["stdout"] = stdout_handle
    else:
        kwargs["stdout"] = subprocess.DEVNULL
    if stderr_path is not None:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_handle = open(stderr_path, "ab")
        kwargs["stderr"] = stderr_handle
    else:
        kwargs["stderr"] = subprocess.DEVNULL
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(command, **kwargs)
    finally:
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()
    return proc


def _probe_hosts_for_bind_host(host: str) -> list[str]:
    """Return concrete loopback hosts that can test a wildcard listener.

    Binding a server to 0.0.0.0 or :: is valid, but connecting to those wildcard
    addresses is not a reliable readiness probe on Windows. Probe through loopback
    instead so dev-control and the browser use the same reachable address family.
    """

    text = str(host or "").strip()
    if text in {"", "0.0.0.0", "*"}:
        return ["127.0.0.1"]
    if text == "::":
        return ["127.0.0.1", "::1"]
    return [text]


def _control_url_host_for_bind_host(host: str) -> str:
    text = str(host or "").strip()
    if text in {"", "0.0.0.0", "::", "*"}:
        return "127.0.0.1"
    return text


def _port_is_listening(host: str, port: int, *, timeout: float = 0.3) -> bool:
    for probe_host in _probe_hosts_for_bind_host(host):
        try:
            with socket.create_connection((probe_host, int(port)), timeout=timeout):
                return True
        except OSError:
            continue
    return False



def _matching_role_pid(config: HeartbeatConfig, role: str) -> int | None:
    if psutil is None:
        return None
    workspace = str(config.workspace).lower()
    if role == "viewport":
        markers = [
            "main_computer.cli viewport",
            f"--port {config.server_port}",
            f"--workspace {workspace}",
        ]
    else:
        markers = [
            "main_computer.cli heartbeat",
            f"--port {config.actual_heartbeat_port}",
            f"--server-port {config.server_port}",
            f"--workspace {workspace}",
        ]
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
        except Exception:
            continue
        if cmdline and all(marker in cmdline for marker in markers):
            pid = int(proc.info.get("pid") or 0)
            if pid and _pid_is_running(pid):
                return pid
    return None


def _role_pid_state(config: HeartbeatConfig, role: str) -> dict[str, Any]:
    if role == "viewport":
        pid_file = config.viewport_pid_file
    elif role == "heartbeat":
        pid_file = config.heartbeat_pid_file
    else:
        raise ValueError(f"Unsupported heartbeat role: {role}")

    pid_file_pid = _read_pid_file(pid_file)
    evidence: list[str] = []
    if pid_file_pid is None:
        control_tracking = "missing_pid_file"
    else:
        evidence.append("pid_file_present")
        control_tracking = "pid_file_unverified"

    if _pid_is_running(pid_file_pid):
        evidence.append("pid_file_alive")
        return {
            "pid": pid_file_pid,
            "pid_file_pid": pid_file_pid,
            "pid_running": True,
            "control_tracking": "pid_file",
            "evidence": evidence,
        }

    fallback = _matching_role_pid(config, role)
    if fallback:
        _write_pid_file(pid_file, fallback)
        evidence.append("process_list_match")
        evidence.append("pid_file_rehydrated")
        return {
            "pid": fallback,
            "pid_file_pid": pid_file_pid,
            "pid_running": True,
            "control_tracking": "rehydrated_from_process_list",
            "evidence": evidence,
        }

    if pid_file_pid is not None:
        # Status checks are intentionally non-destructive.  A missing psutil
        # dependency or a transient process-query failure should not erase
        # control state for a service that may still be reachable by HTTP.
        evidence.append("pid_file_not_verified")

    return {
        "pid": None,
        "pid_file_pid": pid_file_pid,
        "pid_running": False,
        "control_tracking": control_tracking,
        "evidence": evidence,
    }


def _viewport_pid(config: HeartbeatConfig) -> int | None:
    state = _role_pid_state(config, "viewport")
    return int(state["pid"]) if state.get("pid") else None


def _heartbeat_pid(config: HeartbeatConfig) -> int | None:
    state = _role_pid_state(config, "heartbeat")
    return int(state["pid"]) if state.get("pid") else None


def status_payload(config: HeartbeatConfig) -> dict[str, Any]:
    server_state = _role_pid_state(config, "viewport")
    heartbeat_state = _role_pid_state(config, "heartbeat")
    server_ready = _port_is_listening(config.bind_host, config.server_port)
    listener = f"{config.bind_host}:{config.server_port}" if server_ready else ""
    heartbeat_ready = _port_is_listening(config.bind_host, config.actual_heartbeat_port)
    server_evidence = list(server_state.get("evidence") or [])
    heartbeat_evidence = list(heartbeat_state.get("evidence") or [])
    if server_ready:
        server_evidence.append("listener_ready")
    if heartbeat_ready:
        heartbeat_evidence.append("health_endpoint_ready")
    control_host = _control_url_host_for_bind_host(config.bind_host)
    return {
        "ok": True,
        "server": {
            "running": bool(server_state.get("pid") or server_ready),
            "pid": server_state.get("pid"),
            "port": config.server_port,
            "listener": listener,
            "pid_file": str(config.viewport_pid_file),
            "pid_file_pid": server_state.get("pid_file_pid"),
            "ready": server_ready,
            "control_tracking": server_state.get("control_tracking"),
            "evidence": server_evidence,
        },
        "heartbeat": {
            "running": bool(heartbeat_state.get("pid") or heartbeat_ready),
            "pid": heartbeat_state.get("pid"),
            "port": config.actual_heartbeat_port,
            "url": f"http://{control_host}:{config.actual_heartbeat_port}/api/heartbeat/control",
            "pid_file": str(config.heartbeat_pid_file),
            "pid_file_pid": heartbeat_state.get("pid_file_pid"),
            "ready": heartbeat_ready,
            "control_tracking": heartbeat_state.get("control_tracking"),
            "evidence": heartbeat_evidence,
        },
    }


def start_viewport(config: HeartbeatConfig) -> dict[str, Any]:
    current = _viewport_pid(config)
    if current:
        payload = status_payload(config)
        payload.update({"message": f"Viewport already running pid {current}.", "changed": False})
        return payload
    config.workspace.mkdir(parents=True, exist_ok=True)
    command = [
        config.python_executable,
        "-B",
        "-m",
        "main_computer.cli",
        "viewport",
        "--host",
        config.bind_host,
        "--port",
        str(config.server_port),
        "--workspace",
        str(config.workspace),
    ]
    if not config.verbose:
        command.append("--noverbose")
    proc = _launch_detached(
        command,
        cwd=config.workspace,
        stdout_path=config.viewport_out_log,
        stderr_path=config.viewport_err_log,
    )
    _write_pid_file(config.viewport_pid_file, proc.pid)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if _port_is_listening(config.bind_host, config.server_port):
            break
        time.sleep(0.1)
    payload = status_payload(config)
    payload.update({"message": f"Viewport start requested on port {config.server_port}.", "changed": True})
    return payload


def stop_viewport(config: HeartbeatConfig) -> dict[str, Any]:
    current = _viewport_pid(config)
    if not current:
        _clear_pid_file(config.viewport_pid_file)
        payload = status_payload(config)
        payload.update({"message": "Viewport already stopped.", "changed": False})
        return payload
    try:
        if psutil is not None:
            proc = psutil.Process(int(current))
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        else:
            os.kill(int(current), 15)
    except Exception:
        pass
    time.sleep(0.2)
    if not _pid_is_running(current):
        _clear_pid_file(config.viewport_pid_file)
    payload = status_payload(config)
    payload.update({"message": f"Viewport shutdown requested for pid {current}.", "changed": True})
    return payload


def ensure_heartbeat_service(config: HeartbeatConfig, *, replace_existing: bool = False) -> dict[str, Any]:
    current = _heartbeat_pid(config)
    if replace_existing and current:
        try:
            if psutil is not None:
                proc = psutil.Process(int(current))
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
            else:
                os.kill(int(current), 15)
        except Exception:
            pass
        time.sleep(0.2)
        _clear_pid_file(config.heartbeat_pid_file)
        current = None
    if current and _port_is_listening(config.bind_host, config.actual_heartbeat_port):
        payload = status_payload(config)
        payload.update({"message": f"Heartbeat already running pid {current}.", "changed": False})
        return payload
    command = [
        config.python_executable,
        "-B",
        "-m",
        "main_computer.cli",
        "heartbeat",
        "--host",
        config.bind_host,
        "--port",
        str(config.actual_heartbeat_port),
        "--server-port",
        str(config.server_port),
        "--workspace",
        str(config.workspace),
    ]
    if not config.verbose:
        command.append("--noverbose")
    proc = _launch_detached(
        command,
        cwd=config.workspace,
        stdout_path=config.heartbeat_out_log,
        stderr_path=config.heartbeat_err_log,
    )
    _write_pid_file(config.heartbeat_pid_file, proc.pid)
    deadline = time.monotonic() + 10.0
    ready = False
    while time.monotonic() < deadline:
        if _port_is_listening(config.bind_host, config.actual_heartbeat_port):
            ready = True
            break
        time.sleep(0.1)
    payload = status_payload(config)
    message = (
        f"Heartbeat available on port {config.actual_heartbeat_port}."
        if ready
        else f"Heartbeat launch attempted on port {config.actual_heartbeat_port}, but no listener became reachable before the readiness timeout."
    )
    payload.update({"message": message, "changed": True, "ready": ready})
    return payload


class HeartbeatServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: HeartbeatConfig, *, verbose: bool = True) -> None:
        super().__init__(server_address, HeartbeatHandler)
        self.config = config
        self.verbose = verbose
        self.allow_reuse_address = True

    def signal(self, name: str, **fields: Any) -> None:
        if not self.verbose:
            return
        detail = " ".join(f"{key}={value}" for key, value in fields.items())
        print(f"[heartbeat] {name} {detail}".rstrip(), flush=True)

    def request_shutdown(self, *, source: str = "unknown") -> None:
        self.signal("shutdown-requested", source=source)

        def _shutdown() -> None:
            time.sleep(0.1)
            try:
                self.shutdown()
            except Exception as exc:
                self.signal("shutdown-error", error=exc)

        thread = threading.Thread(
            target=_shutdown,
            name="main-computer-heartbeat-shutdown",
            daemon=True,
        )
        thread.start()


class HeartbeatHandler(BaseHTTPRequestHandler):
    server: HeartbeatServer

    def log_message(self, format: str, *args: Any) -> None:
        self.server.signal("http", client=self.client_address[0], message=format % args)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path in {"/api/heartbeat/control", "/api/heartbeat/status", "/healthz"}:
            self._send_json(status_payload(self.server.config))
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path not in {"/api/heartbeat/control", "/api/heartbeat/status"}:
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        action = str(body.get("action", "status") or "status").strip()
        if action == "status":
            payload = status_payload(self.server.config)
            payload["message"] = "Heartbeat status current."
            self._send_json(payload)
            return
        if action == "start":
            payload = ensure_heartbeat_service(self.server.config)
            payload = start_viewport(self.server.config)
            payload["message"] = "Viewport start requested through heartbeat."
            self._send_json(payload)
            return
        if action == "shutdown":
            payload = stop_viewport(self.server.config)
            payload["message"] = "Viewport and heartbeat shutdown requested through heartbeat."
            payload["heartbeat_shutdown_requested"] = True
            self._send_json(payload)
            self.server.request_shutdown(source="heartbeat-control-shutdown")
            return
        if action == "restart":
            stop_viewport(self.server.config)
            payload = start_viewport(self.server.config)
            payload["message"] = "Viewport restart requested through heartbeat."
            self._send_json(payload)
            return
        self._send_json({"error": f"Unsupported heartbeat action: {action}"}, status=HTTPStatus.BAD_REQUEST)

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, data: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def serve(config: HeartbeatConfig) -> None:
    config.workspace.mkdir(parents=True, exist_ok=True)
    _write_pid_file(config.heartbeat_pid_file, os.getpid())
    server = HeartbeatServer((config.bind_host, config.actual_heartbeat_port), config, verbose=config.verbose)
    server.signal(
        "start",
        bind_host=config.bind_host,
        port=config.actual_heartbeat_port,
        server_port=config.server_port,
        workspace=config.workspace,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.signal("interrupt")
    finally:
        server.server_close()
        if _read_pid_file(config.heartbeat_pid_file) == os.getpid():
            _clear_pid_file(config.heartbeat_pid_file)
        server.signal("stop")
