from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from main_computer.main_log_hooks import install_main_log_hooks_from_env
from main_computer.main_log_client import emit_main_log_text


Runner = Callable[..., subprocess.CompletedProcess[str]]
SleepFunc = Callable[[float], None]
TimeFunc = Callable[[], float]
OutputFunc = Callable[[str], None]
ProcessCommandReader = Callable[[int], str | None]
ProcessTerminator = Callable[[int], None]


DEFAULT_HEARTBEAT_INTERVAL_S = 30.0
DEFAULT_LIGHT_CHECK_INTERVAL_S = 180.0
DEFAULT_DOCKER_START_TIMEOUT_S = 45.0
DEFAULT_EXECUTOR_IMAGE = "main-computer-executor:latest"
DEFAULT_FOUNDATIONDB_CLUSTER_FILE = Path(".foundationdb") / "docker.cluster"
DEFAULT_FOUNDATIONDB_CONTAINER_NAME = "main-computer-foundationdb-smoke"
DEFAULT_FOUNDATIONDB_DOCKER_IMAGE = "foundationdb/foundationdb:7.4.6"
DEFAULT_FOUNDATIONDB_PORT = 4550
DEFAULT_FOUNDATIONDB_NAMESPACE = "main-computer-exp-fdb-autostart-smoke"
DEFAULT_FOUNDATIONDB_START_TIMEOUT_S = 45.0
SERVICE_NAME = "main-computer-executor-service"
EXECUTOR_SERVICE_PID_FILENAME = ".main_computer_executor_service.pid"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(value: str, limit: int = 2000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _process_stream_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _command_display(command: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def _first_nonempty_line(value: str, *, limit: int = 320) -> str:
    for line in str(value or "").replace("\r", "\n").splitlines():
        item = re.sub(r"\s+", " ", line).strip()
        if item:
            return _truncate(item, limit)
    return ""


def _compose_failure_warning(output: str, *, action: str) -> str:
    detail = _first_nonempty_line(output)
    lowered = str(output or "").lower()

    if "all predefined address pools have been fully subnetted" in lowered:
        return (
            "Docker could not allocate a new Compose network subnet. "
            "Unused old debug Compose stacks or Docker networks may be exhausting Docker Desktop's address pool."
        )

    if "address already in use" in lowered or "port is already allocated" in lowered:
        return "Docker Compose could not start because a required host port is already in use."

    if detail:
        return f"Docker Compose could not {action}: {detail}"

    return f"Docker Compose could not {action}; no error output was captured."


def _compose_failure_remediation(output: str, *, action: str) -> str:
    lowered = str(output or "").lower()

    if "all predefined address pools have been fully subnetted" in lowered:
        return (
            "Remove stale stopped debug Compose projects or unused Docker networks, then retry the debug stack. "
            "Useful checks: docker network ls; docker network prune; docker compose --project-name <project> down --remove-orphans."
        )

    if "address already in use" in lowered or "port is already allocated" in lowered:
        return "Find the process/container using the conflicting port, stop it, then retry Docker Compose."

    return f"Inspect the Compose project with docker compose ps -a and docker compose logs, then retry {action}."


def _normalized_command_line(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("\r", " ").replace("\n", " ")
    text = text.replace("\"", "").replace("'", "")
    return re.sub(r"\s+", " ", text)


def _current_command_line() -> str:
    return _command_display([sys.executable, *sys.argv])


def _read_process_command_line_default(pid: int) -> str | None:
    if pid <= 0:
        return None

    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    try:
        if proc_cmdline.exists():
            raw = proc_cmdline.read_bytes()
            if raw:
                return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        pass

    if os.name == "nt":
        commands = [
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine",
            ],
            [
                "wmic",
                "process",
                "where",
                f"ProcessId={pid}",
                "get",
                "CommandLine",
                "/value",
            ],
        ]
    else:
        commands = [["ps", "-p", str(pid), "-o", "args="]]

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        output = _process_stream_text(result.stdout).strip()
        if not output:
            continue
        if command[:1] == ["wmic"]:
            for line in output.splitlines():
                if line.lower().startswith("commandline="):
                    value = line.split("=", 1)[1].strip()
                    if value:
                        return value
            continue
        return output
    return None


def _terminate_process_default(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)


def _looks_like_executor_service_command(command_line: str | None) -> bool:
    normalized = _normalized_command_line(command_line)
    return "main_computer.executor_service" in normalized or "executor_service.py" in normalized


def _safe_resolved_path(value: Any) -> str:
    try:
        return str(Path(str(value)).expanduser().resolve())
    except Exception:
        return str(value or "")


def _pid_entry_from_text(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"parse_error": "pid file is not valid JSON", "raw": text[:200]}
        return payload if isinstance(payload, dict) else {"parse_error": "pid file JSON is not an object", "raw": text[:200]}

    first_line = text.splitlines()[0].strip()
    try:
        return {"pid": int(first_line), "legacy_plain_pid": True}
    except ValueError:
        return {"parse_error": "pid file does not contain a PID", "raw": text[:200]}


def _pid_from_entry(entry: dict[str, Any]) -> int | None:
    try:
        pid = int(entry.get("pid"))
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_file_command_matches_live(entry: dict[str, Any], live_command_line: str | None) -> bool:
    stored_command_line = entry.get("command_line")
    if not stored_command_line or not live_command_line:
        return False
    return _normalized_command_line(str(stored_command_line)) == _normalized_command_line(live_command_line)


def _pid_file_root_matches(entry: dict[str, Any], root: Path) -> bool:
    return _safe_resolved_path(entry.get("root")) == str(root.resolve())


def _which_or_path(command: str) -> str | None:
    found = shutil.which(command)
    if found:
        return found
    candidate = Path(command)
    if candidate.exists():
        return str(candidate)
    return None



def _flag_enabled(value: object, *, default: bool = True) -> bool:
    text = str(value if value is not None else ("1" if default else "0")).strip().lower()
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    return bool(default)


def _coerce_int_env(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.environ.get(name)
    try:
        value = int(str(raw).strip()) if raw not in (None, "") else int(default)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def _coerce_float_env(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.environ.get(name)
    try:
        value = float(str(raw).strip()) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(float(minimum), value)
    return value


def _parse_foundationdb_cluster_endpoint(cluster_file: Path) -> tuple[str, int] | None:
    try:
        text = cluster_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    match = re.search(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+@([^:,\s]+):([0-9]+)", text)
    if not match:
        return None
    port = int(match.group(2))
    if port <= 0 or port > 65535:
        return None
    return match.group(1), port


def _tcp_port_open(host: str, port: int, *, timeout_s: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            return True
    except OSError:
        return False


def _expand_windows_envvars(value: str) -> str:
    text = os.path.expandvars(str(value or ""))
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        text = re.sub(r"%LOCALAPPDATA%", lambda _match: local_appdata, text, flags=re.IGNORECASE)
    return text


def _runtime_image_file_name(distribution_name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(distribution_name or "").strip())
    name = re.sub(r"\s+", "-", name).strip("-. ")
    if not name:
        name = "main-computer-executor"
    return f"{name}-rootfs.tar"


def _default_wsl_runtime_root(profile_name: str, repo_root: Path) -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        base = Path(local_appdata) / "MainComputer"
        return base / ("wsl" if profile_name == "prod" else "wsl-test")
    return repo_root.parent / ".main-computer-runtime" / ("wsl" if profile_name == "prod" else "wsl-test")


def _host_path_to_wsl(path: Path) -> str:
    raw = str(Path(path).resolve())
    normalized = raw.replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", normalized)
    if match:
        return f"/mnt/{match.group(1).lower()}/{match.group(2)}"
    if normalized.startswith("//"):
        raise ValueError(f"UNC paths are not supported for WSL executor paths: {raw}")
    return normalized


def _parse_wsl_distribution_names(stdout: str) -> list[str]:
    """Return distro names from either normal or UTF-16-looking wsl.exe output."""

    cleaned = (stdout or "").replace("\x00", "")
    names: list[str] = []
    for line in cleaned.replace("\r", "\n").splitlines():
        item = line.strip().lstrip("*").strip()
        if not item:
            continue
        if item.lower().startswith("windows subsystem for linux"):
            continue
        names.append(item)
    return names


def _executor_service_state_path(root: Path | str) -> Path:
    repo_root = Path(root).resolve()
    return repo_root / "runtime" / "executor_service" / "state.json"


def load_executor_service_state(root: Path | str) -> dict[str, Any]:
    """Load the state written by the resident executor service.

    The graphical viewport uses this as the light-weight service-to-service
    contract. Missing or invalid state is reported as data instead of raising so
    the UI can show a clear "service missing/down" tile.
    """

    state_path = _executor_service_state_path(root)
    if not state_path.exists():
        return {
            "ok": False,
            "state": "missing",
            "service_available": False,
            "message": "executor service has not written state yet",
            "state_path": str(state_path),
        }

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "state": "invalid",
            "service_available": False,
            "message": f"executor service state could not be read: {exc}",
            "state_path": str(state_path),
        }

    if not isinstance(data, dict):
        return {
            "ok": False,
            "state": "invalid",
            "service_available": False,
            "message": "executor service state is not a JSON object",
            "state_path": str(state_path),
        }

    service = data.get("service") if isinstance(data.get("service"), dict) else {}
    heartbeat = service.get("heartbeat_at") if isinstance(service, dict) else None
    policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    stale_after_s = float(policy.get("stale_after_s") or max(DEFAULT_LIGHT_CHECK_INTERVAL_S * 2.0, 600.0))
    service_available = True
    if heartbeat:
        try:
            stamp = datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))
            age_s = (datetime.now(timezone.utc) - stamp).total_seconds()
            data["heartbeat_age_s"] = max(0.0, round(age_s, 1))
            if age_s > stale_after_s:
                service_available = False
                data["state"] = "stale"
                data["ok"] = False
                data["message"] = f"executor service heartbeat is stale ({int(age_s)}s old)"
        except ValueError:
            data["heartbeat_age_s"] = None

    data.setdefault("state_path", str(state_path))
    data["service_available"] = service_available
    return data


class ExecutorService:
    """Boot and keep alive the local execution substrate.

    This is deliberately a small resident service. It treats boot as incomplete
    until WSL, Docker, and the compose stack are all proven ready. While boot is
    incomplete, watch mode keeps running the full boot reconcile on the heartbeat
    cadence. After boot is proven, watch mode switches to cheap light keepalive
    checks and full reconcile only when the executor entrypoint changes.
    """

    def __init__(
        self,
        *,
        root: Path | str,
        wsl_distribution: str = "MainComputerExecutorTest",
        wsl_command: str = "wsl.exe",
        docker_command: str = "docker",
        powershell_command: str = "powershell.exe",
        compose_file: Path | str | None = None,
        runner: Runner | None = None,
        sleep_func: SleepFunc | None = None,
        time_func: TimeFunc | None = None,
        output_func: OutputFunc | None = print,
        process_command_reader: ProcessCommandReader | None = None,
        process_terminator: ProcessTerminator | None = None,
        heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
        light_check_interval_s: float = DEFAULT_LIGHT_CHECK_INTERVAL_S,
        docker_start_timeout_s: float = DEFAULT_DOCKER_START_TIMEOUT_S,
    ) -> None:
        self.root = Path(root).resolve()
        self.runtime_dir = self.root / "runtime" / "executor_service"
        self.state_path = self.runtime_dir / "state.json"
        self.log_path = self.runtime_dir / "service.log"
        self.pid_path = self.root / EXECUTOR_SERVICE_PID_FILENAME
        self.wsl_distribution = (wsl_distribution or "MainComputerExecutorTest").strip() or "MainComputerExecutorTest"
        self.wsl_command = (wsl_command or "wsl.exe").strip() or "wsl.exe"
        self.docker_command = (docker_command or "docker").strip() or "docker"
        self.executor_image = (os.environ.get("MAIN_COMPUTER_EXECUTOR_IMAGE") or DEFAULT_EXECUTOR_IMAGE).strip() or DEFAULT_EXECUTOR_IMAGE
        self.powershell_command = (powershell_command or "powershell.exe").strip() or "powershell.exe"
        self.compose_file = Path(compose_file) if compose_file else self.root / "docker-compose.dev.yml"
        if not self.compose_file.is_absolute():
            self.compose_file = (self.root / self.compose_file).resolve()
        self.compose_project = (
            os.environ.get("MAIN_COMPUTER_EXECUTOR_COMPOSE_PROJECT")
            or os.environ.get("MAIN_COMPUTER_DEV_COMPOSE_PROJECT")
            or ""
        ).strip()
        self.runner = runner or subprocess.run
        self.sleep = sleep_func or time.sleep
        self.time = time_func or time.monotonic
        self.output = output_func
        self.process_command_reader = process_command_reader or _read_process_command_line_default
        self.process_terminator = process_terminator or _terminate_process_default
        self.heartbeat_interval_s = max(1.0, float(heartbeat_interval_s))
        self.light_check_interval_s = max(self.heartbeat_interval_s, float(light_check_interval_s))
        self.docker_start_timeout_s = max(1.0, float(docker_start_timeout_s))

        self._last_state: dict[str, Any] = {}
        self._last_executor_fingerprint: str | None = None
        self._next_light_check = 0.0
        self._state_lock = threading.RLock()
        self._heartbeat_stop: threading.Event | None = None
        self._heartbeat_thread: threading.Thread | None = None

    @property
    def repo_executor_path(self) -> Path:
        return self.root / "docker" / "executor" / "main-computer-exec"

    def boot(self, *, watch: bool = False, max_watch_loops: int | None = None) -> dict[str, Any]:
        """Run one full boot reconciliation, then optionally enter keepalive."""

        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        pid_claim = self._claim_pid_file() if watch else self._component(
            ok=True,
            state="skipped",
            message="one-shot boot does not claim the resident service PID file",
            pid_file=str(self.pid_path),
            current_pid=os.getpid(),
        )
        self._emit(
            "executor service starting" if watch else "executor service boot check starting",
            pid=os.getpid(),
            pid_file=str(self.pid_path),
            root=str(self.root),
        )

        if watch:
            self._write_starting_state(pid_claim=pid_claim)
            self._start_heartbeat_pump()

        try:
            state = self._full_boot_reconcile()
            if watch:
                self._preserve_service_runtime_fields(state)
            state["service"]["watching"] = bool(watch)
            state["service"]["pid_file"] = str(self.pid_path)
            state["service"]["pid_claim"] = pid_claim
            if watch:
                state["service"]["state"] = "watching" if state.get("ok") else "booting"
                state["service"]["heartbeat_at"] = _now_iso()
            self._write_state(state)
            self._emit_boot_result(state, prefix="initial boot")

            if watch:
                return self.watch(max_watch_loops=max_watch_loops)
            return state
        except Exception:
            if watch:
                self._stop_heartbeat_pump()
                self._release_pid_file()
            raise

    def watch(self, *, max_watch_loops: int | None = None) -> dict[str, Any]:
        """Stay resident and finish boot before switching to keepalive.

        Until WSL, Docker, and the executor image build are all proven ready, watch mode
        keeps trying the full boot reconcile on the heartbeat cadence. Once boot
        is proven, watch mode only runs light keepalive checks unless the
        repo-owned executor entrypoint changes.
        """

        loops = 0
        state = self._last_state or self._base_state("watching")
        self._next_light_check = self.time() + self.light_check_interval_s

        try:
            while max_watch_loops is None or loops < max_watch_loops:
                loops += 1
                state = dict(self._last_state or state)
                boot_proven = bool(state.get("boot_proven") and state.get("ok"))
                state["updated_at"] = _now_iso()
                service = dict(state.get("service") or {})
                service.update(
                    {
                        "name": SERVICE_NAME,
                        "pid": os.getpid(),
                        "pid_file": str(self.pid_path),
                        "state": "watching" if boot_proven else "booting",
                        "watching": True,
                        "heartbeat_at": _now_iso(),
                    }
                )
                state["service"] = service
                state["state"] = "ready" if boot_proven else str(state.get("state") or "booting")
                self._write_state(state)

                if not boot_proven:
                    self._emit(
                        "boot is not complete; retrying full boot reconcile",
                        attempt=loops,
                        retry_interval_s=self.heartbeat_interval_s,
                        **self._component_state_fields(state),
                    )
                    state = self._full_boot_reconcile()
                    self._preserve_service_runtime_fields(state)
                    state["service"]["watching"] = True
                    state["service"]["state"] = "watching" if state.get("ok") else "booting"
                    self._write_state(state)
                    self._emit_boot_result(state, prefix="boot retry")
                    if state.get("ok"):
                        self._next_light_check = self.time() + self.light_check_interval_s
                elif self._executor_fingerprint_changed():
                    self._emit("executor entrypoint changed; reconciling infrastructure", attempt=loops)
                    state = self._full_boot_reconcile()
                    self._preserve_service_runtime_fields(state)
                    state["service"]["watching"] = True
                    state["service"]["state"] = "watching" if state.get("ok") else "booting"
                    self._write_state(state)
                    self._emit_boot_result(state, prefix="executor change reconcile")
                    self._next_light_check = self.time() + self.light_check_interval_s
                else:
                    now = self.time()
                    if now >= self._next_light_check:
                        self._emit("running light keepalive check", attempt=loops)
                        state = self._light_keepalive(dict(self._last_state or state))
                        self._preserve_service_runtime_fields(state)
                        self._write_state(state)
                        self._emit_boot_result(state, prefix="light keepalive")
                        self._next_light_check = now + self.light_check_interval_s

                if max_watch_loops is not None and loops >= max_watch_loops:
                    break
                self.sleep(self.heartbeat_interval_s)
        except KeyboardInterrupt:
            state = dict(self._last_state or state)
            state["state"] = "stopping"
            state["ok"] = False
            state.setdefault("service", {})["state"] = "stopping"
            self._write_state(state)
            self._emit("executor service stopping", reason="keyboard-interrupt")
        finally:
            self._stop_heartbeat_pump()
            self._release_pid_file()
        return self._last_state or state

    def _start_heartbeat_pump(self) -> None:
        """Keep the resident heartbeat fresh while blocking boot checks run."""

        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return

        stop_event = threading.Event()
        self._heartbeat_stop = stop_event
        thread = threading.Thread(
            target=self._heartbeat_pump,
            name="main-computer-executor-service-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread = thread
        thread.start()

    def _stop_heartbeat_pump(self) -> None:
        stop_event = self._heartbeat_stop
        thread = self._heartbeat_thread
        self._heartbeat_stop = None
        self._heartbeat_thread = None
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _heartbeat_pump(self) -> None:
        stop_event = self._heartbeat_stop
        if stop_event is None:
            return

        # The first heartbeat must not wait for the first interval. Startup
        # reconciliation is exactly where WSL/Docker/Compose can block, so make
        # the liveness signal independent from the boot path immediately and
        # keep retrying even if one state-file write fails.
        while not stop_event.is_set():
            try:
                self._refresh_heartbeat_state()
            except Exception as exc:  # pragma: no cover - defensive resident loop
                self._emit("executor heartbeat refresh failed; will retry", error=exc)
            if stop_event.wait(self.heartbeat_interval_s):
                break

    def _refresh_heartbeat_state(self) -> None:
        """Update only service liveness fields without changing boot progress.

        The executor can spend minutes inside WSL/Docker repair commands. The
        heartbeat is meant to prove that this Python service process is still
        alive, not that boot reconciliation has completed. Keep it moving while
        preserving the current component/progress payload.
        """

        with self._state_lock:
            state = dict(self._last_state or self._base_state("starting"))
            state["updated_at"] = _now_iso()
            service = dict(state.get("service") or {})
            service.update(
                {
                    "name": SERVICE_NAME,
                    "pid": os.getpid(),
                    "pid_file": str(self.pid_path),
                    "watching": True,
                    "heartbeat_at": _now_iso(),
                }
            )
            if not service.get("state"):
                service["state"] = str(state.get("state") or "booting")
            state["service"] = service
            self._write_state_unlocked(state)

    def _write_starting_state(self, *, pid_claim: dict[str, Any]) -> dict[str, Any]:
        """Publish a current resident-service heartbeat before slow boot checks run.

        Unsafe shutdowns can leave a previous ready state on disk. Watch mode must
        supersede that stale telemetry as soon as the new executor process has
        claimed ownership, before WSL/Docker/Compose reconciliation can block.
        """

        state = self._base_state("starting")
        state.update(
            {
                "boot_proven": False,
                "message": "executor service starting; boot reconcile pending",
            }
        )
        state["service"].update(
            {
                "pid_file": str(self.pid_path),
                "pid_claim": pid_claim,
                "state": "starting",
                "watching": True,
                "heartbeat_at": _now_iso(),
            }
        )
        pending_components = self._pending_boot_components()
        state.update(pending_components)
        state["components"] = pending_components
        self._write_state(state)
        return state

    def _base_state(self, phase: str) -> dict[str, Any]:
        return {
            "ok": False,
            "state": phase,
            "updated_at": _now_iso(),
            "root": str(self.root),
            "runtime_dir": str(self.runtime_dir),
            "state_path": str(self.state_path),
            "service": {
                "name": SERVICE_NAME,
                "pid": os.getpid(),
                "pid_file": str(self.pid_path),
                "state": phase,
                "watching": False,
                "heartbeat_at": _now_iso(),
            },
            "policy": {
                "heartbeat_interval_s": self.heartbeat_interval_s,
                "light_check_interval_s": self.light_check_interval_s,
                "stale_after_s": max(self.light_check_interval_s * 2.0, 600.0),
            },
            "components": {},
            "events": [],
        }

    def _component(
        self,
        *,
        ok: bool,
        state: str,
        message: str,
        **extra: Any,
    ) -> dict[str, Any]:
        payload = {
            "ok": bool(ok),
            "state": state,
            "message": message,
            "checked_at": _now_iso(),
        }
        payload.update(extra)
        return payload

    def _pending_boot_components(self) -> dict[str, dict[str, Any]]:
        return {
            "wsl": self._component(
                ok=False,
                state="pending",
                message="WSL executor check pending",
                distribution=self.wsl_distribution,
            ),
            "docker": self._component(
                ok=False,
                state="pending",
                message="Docker engine check pending",
                docker_command=self.docker_command,
            ),
            "foundationdb": self._component(
                ok=False,
                state="pending",
                message="FoundationDB Docker bootstrap pending",
                cluster_file=str(self._foundationdb_cluster_file()),
            ),
            "compose": self._component(
                ok=False,
                state="pending",
                message="executor Compose/image check pending",
                compose_file=str(self.compose_file),
            ),
        }

    def _emit(self, message: str, **fields: Any) -> None:
        if self.output is None:
            return
        suffix = ""
        compact_fields = {key: value for key, value in fields.items() if value not in (None, "")}
        if compact_fields:
            suffix = " " + " ".join(f"{key}={value}" for key, value in compact_fields.items())
        try:
            self.output(f"[{_now_iso()}] {SERVICE_NAME}: {message}{suffix}", flush=True)  # type: ignore[misc]
        except TypeError:
            self.output(f"[{_now_iso()}] {SERVICE_NAME}: {message}{suffix}")

    def _component_state_fields(self, state: dict[str, Any]) -> dict[str, str]:
        components = state.get("components") if isinstance(state.get("components"), dict) else {}
        return {
            "wsl": str((components.get("wsl") or state.get("wsl") or {}).get("state") or "unknown"),
            "docker": str((components.get("docker") or state.get("docker") or {}).get("state") or "unknown"),
            "foundationdb": str((components.get("foundationdb") or state.get("foundationdb") or {}).get("state") or "unknown"),
            "compose": str((components.get("compose") or state.get("compose") or {}).get("state") or "unknown"),
        }

    def _boot_progress_component(self, *, name: str, state: str, message: str, **extra: Any) -> dict[str, Any]:
        return self._component(ok=False, state=state, message=message, boot_step=name, **extra)

    def _publish_boot_progress(
        self,
        *,
        message: str,
        wsl: dict[str, Any] | None = None,
        docker: dict[str, Any] | None = None,
        foundationdb: dict[str, Any] | None = None,
        compose: dict[str, Any] | None = None,
    ) -> None:
        """Publish boot progress without claiming that dependencies are ready.

        This is intentionally separate from the final reconcile result. During
        reboot recovery a Docker/WSL command can run for a long time; the UI and
        supervisor need to see that the executor process is still alive and which
        milestone it is currently supervising.
        """

        current = dict(self._last_state or self._base_state("booting"))
        components = dict(current.get("components") or {})
        if not components:
            components.update(self._pending_boot_components())
        if wsl is not None:
            components["wsl"] = wsl
        if docker is not None:
            components["docker"] = docker
        if foundationdb is not None:
            components["foundationdb"] = foundationdb
        if compose is not None:
            components["compose"] = compose

        current.update(
            {
                "ok": False,
                "state": "booting",
                "boot_proven": False,
                "updated_at": _now_iso(),
                "message": message,
                "wsl": components.get("wsl"),
                "docker": components.get("docker"),
                "foundationdb": components.get("foundationdb"),
                "compose": components.get("compose"),
                "components": {
                    "wsl": components.get("wsl"),
                    "docker": components.get("docker"),
                    "foundationdb": components.get("foundationdb"),
                    "compose": components.get("compose"),
                },
            }
        )
        service = dict(current.get("service") or {})
        service.update(
            {
                "name": SERVICE_NAME,
                "pid": os.getpid(),
                "pid_file": str(self.pid_path),
                "watching": bool(service.get("watching") or self._heartbeat_stop is not None),
                "state": "booting",
                "heartbeat_at": _now_iso(),
            }
        )
        current["service"] = service
        self._write_state(current)


    def _emit_boot_result(self, state: dict[str, Any], *, prefix: str) -> None:
        fields = self._component_state_fields(state)
        message = str(state.get("message") or "")
        warning = self._warning_from_state(state)
        if state.get("ok"):
            self._emit(f"{prefix} complete; executor infrastructure is ready", **fields)
        else:
            self._emit(
                f"{prefix} incomplete; will keep trying" if state.get("service", {}).get("watching") else f"{prefix} incomplete",
                state=state.get("state"),
                detail=message,
                warning=warning,
                **fields,
            )

    def _warning_from_state(self, state: dict[str, Any]) -> str:
        if state.get("ok"):
            return ""

        explicit = str(state.get("warning") or "").strip()
        if explicit:
            return explicit

        for name in ("foundationdb", "compose", "docker", "wsl"):
            component = state.get(name)
            if not isinstance(component, dict) or component.get("ok"):
                continue
            detail = str(component.get("warning") or component.get("error") or component.get("message") or "").strip()
            if detail:
                return f"{name}: {_truncate(detail, 500)}"

        return str(state.get("message") or "executor infrastructure needs attention")

    def _preserve_service_runtime_fields(self, state: dict[str, Any]) -> None:
        previous_service = self._last_state.get("service") if isinstance(self._last_state.get("service"), dict) else {}
        service = dict(state.get("service") or {})
        for key in ("pid_file", "pid_claim"):
            if key in previous_service and key not in service:
                service[key] = previous_service[key]
        service["pid"] = os.getpid()
        service.setdefault("pid_file", str(self.pid_path))
        if previous_service.get("watching") or service.get("watching"):
            service["watching"] = True
            service["heartbeat_at"] = _now_iso()
        state["service"] = service

    def _read_pid_file(self) -> dict[str, Any]:
        try:
            raw = self.pid_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            return {"read_error": str(exc)}
        return _pid_entry_from_text(raw)

    def _current_process_command_line(self) -> str:
        try:
            live_command_line = self.process_command_reader(os.getpid())
        except Exception:
            live_command_line = None
        return live_command_line or _current_command_line()

    def _write_pid_file(self, *, claim: dict[str, Any]) -> None:
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "service": SERVICE_NAME,
            "root": str(self.root),
            "command_line": self._current_process_command_line(),
            "written_at": _now_iso(),
            "claim": claim,
        }
        tmp = self.pid_path.with_suffix(self.pid_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.pid_path)

    def _release_pid_file(self) -> None:
        entry = self._read_pid_file()
        if _pid_from_entry(entry) != os.getpid():
            return
        try:
            self.pid_path.unlink()
            self._emit("released executor service PID file", pid_file=str(self.pid_path))
        except OSError as exc:
            self._emit("could not release executor service PID file", pid_file=str(self.pid_path), error=exc)

    def _claim_pid_file(self) -> dict[str, Any]:
        entry = self._read_pid_file()
        current_pid = os.getpid()
        prior_pid = _pid_from_entry(entry)
        claim: dict[str, Any] = self._component(
            ok=True,
            state="claimed",
            message="executor service PID file claimed",
            pid_file=str(self.pid_path),
            current_pid=current_pid,
            prior_pid=prior_pid,
        )

        if entry.get("read_error"):
            claim.update(
                {
                    "state": "read-error",
                    "message": "could not read previous executor service PID file; continuing boot",
                    "error": entry.get("read_error"),
                }
            )
        elif entry.get("parse_error"):
            claim.update(
                {
                    "state": "parse-error",
                    "message": "previous executor service PID file was unreadable; continuing boot",
                    "error": entry.get("parse_error"),
                }
            )
        elif prior_pid is None:
            claim.update({"state": "new", "message": "no previous executor service PID file was present"})
        elif prior_pid == current_pid:
            claim.update({"state": "already-current", "message": "executor service PID file already belongs to this process"})
        else:
            live_command_line = self.process_command_reader(prior_pid)
            claim["prior_command_line"] = entry.get("command_line")
            claim["prior_live_command_line"] = live_command_line
            command_matches_pid_file = _pid_file_command_matches_live(entry, live_command_line)
            claim["prior_command_matches_pid_file"] = command_matches_pid_file
            claim["prior_root_matches"] = _pid_file_root_matches(entry, self.root)
            claim["prior_looks_like_executor_service"] = _looks_like_executor_service_command(live_command_line)

            if not live_command_line:
                claim.update({"state": "stale", "message": "previous executor service PID was not running"})
            elif (
                entry.get("service") == SERVICE_NAME
                and command_matches_pid_file
                and claim["prior_root_matches"]
                and claim["prior_looks_like_executor_service"]
            ):
                try:
                    self.process_terminator(prior_pid)
                except Exception as exc:  # pragma: no cover - platform dependent
                    claim.update(
                        {
                            "state": "terminate-failed",
                            "message": "previous executor service matched the PID file, but termination failed; continuing boot",
                            "error": str(exc),
                        }
                    )
                    self._emit("could not terminate previous executor service; continuing boot", prior_pid=prior_pid, error=exc)
                else:
                    claim.update(
                        {
                            "state": "terminated",
                            "message": "previous executor service matched the PID file and was asked to stop",
                            "terminated_pid": prior_pid,
                        }
                    )
                    self._emit("terminated previous executor service from PID file", prior_pid=prior_pid)
            else:
                claim.update(
                    {
                        "state": "not-terminated",
                        "message": "previous PID was not killed because its live command did not safely match the PID file and repo root",
                    }
                )
                self._emit(
                    "previous PID was not killed; continuing boot",
                    prior_pid=prior_pid,
                    command_matches_pid_file=command_matches_pid_file,
                    root_matches=claim["prior_root_matches"],
                    looks_like_executor=claim["prior_looks_like_executor_service"],
                )

        try:
            self._write_pid_file(claim=claim)
            claim["written"] = True
            self._emit("wrote executor service PID file", pid_file=str(self.pid_path), pid=current_pid)
        except OSError as exc:
            claim.update(
                {
                    "ok": False,
                    "state": "write-failed",
                    "message": "could not write executor service PID file; continuing boot",
                    "error": str(exc),
                    "written": False,
                }
            )
            self._emit("could not write executor service PID file; continuing boot", pid_file=str(self.pid_path), error=exc)
        return claim

    def _emit_completed_process_to_main_log(self, result: subprocess.CompletedProcess[str], *, cwd: Path) -> None:
        command = result.args if isinstance(result.args, list) else []
        fields = {
            "command": _command_display(command) if command else str(result.args),
            "cwd": str(cwd),
            "returncode": result.returncode,
        }
        if result.stdout:
            emit_main_log_text(
                service=SERVICE_NAME,
                source_service=SERVICE_NAME,
                kind="subprocess-stream",
                stream="stdout",
                message=_process_stream_text(result.stdout),
                timeout_s=0.05,
                **fields,
            )
        if result.stderr:
            emit_main_log_text(
                service=SERVICE_NAME,
                source_service=SERVICE_NAME,
                kind="subprocess-stream",
                stream="stderr",
                message=_process_stream_text(result.stderr),
                timeout_s=0.05,
                **fields,
            )

    def _run(
        self,
        command: list[str],
        *,
        timeout: float,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        run_cwd = cwd or self.root
        try:
            result = self.runner(
                command,
                cwd=str(run_cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            self._emit_completed_process_to_main_log(result, cwd=run_cwd)
            return result
        except subprocess.TimeoutExpired as exc:
            stdout = _process_stream_text(getattr(exc, "stdout", None) or getattr(exc, "output", None))
            stderr = _process_stream_text(getattr(exc, "stderr", None))
            timeout_message = f"command timed out after {timeout:g} seconds: {_command_display(command)}"
            if stderr:
                stderr = f"{stderr.rstrip()}\n{timeout_message}"
            else:
                stderr = timeout_message
            result = subprocess.CompletedProcess(command, 124, stdout=stdout, stderr=stderr)
            self._emit_completed_process_to_main_log(result, cwd=cwd or self.root)
            return result
        except OSError as exc:
            result = subprocess.CompletedProcess(
                command,
                127,
                stdout="",
                stderr=f"command failed to start: {_command_display(command)}: {exc}",
            )
            self._emit_completed_process_to_main_log(result, cwd=cwd or self.root)
            return result
        except subprocess.SubprocessError as exc:
            result = subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=f"command failed: {_command_display(command)}: {exc}",
            )
            self._emit_completed_process_to_main_log(result, cwd=cwd or self.root)
            return result


    def _foundationdb_cluster_file(self) -> Path:
        explicit = (os.environ.get("MAIN_COMPUTER_HUB_FDB_CLUSTER_FILE") or "").strip()
        if not explicit:
            explicit = (os.environ.get("MAIN_COMPUTER_FDB_CLUSTER_FILE") or "").strip()
        cluster_file = Path(explicit) if explicit else self.root / DEFAULT_FOUNDATIONDB_CLUSTER_FILE
        if not cluster_file.is_absolute():
            cluster_file = self.root / cluster_file
        return cluster_file.resolve()

    def _foundationdb_autostart_enabled(self) -> bool:
        return _flag_enabled(
            os.environ.get("MAIN_COMPUTER_FDB_AUTO_START", os.environ.get("MAIN_COMPUTER_DEV_HUB_FDB_AUTO_START")),
            default=True,
        )

    def _foundationdb_cluster_is_default(self, cluster_file: Path) -> bool:
        default_cluster_file = (self.root / DEFAULT_FOUNDATIONDB_CLUSTER_FILE).resolve()
        try:
            return cluster_file.resolve() == default_cluster_file
        except OSError:
            return False

    def _check_foundationdb_light(self) -> dict[str, Any]:
        cluster_file = self._foundationdb_cluster_file()
        if not self._foundationdb_autostart_enabled():
            return self._component(
                ok=True,
                state="disabled",
                message="FoundationDB Docker autostart is disabled",
                cluster_file=str(cluster_file),
            )

        if not self._foundationdb_cluster_is_default(cluster_file):
            return self._component(
                ok=True,
                state="custom-cluster-file",
                message="custom FoundationDB cluster file is owned outside the executor bootstrap path",
                cluster_file=str(cluster_file),
            )

        endpoint = _parse_foundationdb_cluster_endpoint(cluster_file)
        if endpoint is None:
            return self._component(
                ok=False,
                state="missing-or-invalid-cluster-file",
                message="default FoundationDB cluster file is not ready",
                cluster_file=str(cluster_file),
            )

        host, port = endpoint
        reachable = _tcp_port_open(host, port)
        return self._component(
            ok=reachable,
            state="ready" if reachable else "port-unreachable",
            message="FoundationDB default Docker cluster is reachable" if reachable else "FoundationDB cluster file exists but the coordinator port is not reachable",
            cluster_file=str(cluster_file),
            host=host,
            port=port,
        )

    def _reconcile_foundationdb(self) -> dict[str, Any]:
        cluster_file = self._foundationdb_cluster_file()
        current = self._check_foundationdb_light()
        if current.get("ok"):
            return current

        if not self._foundationdb_autostart_enabled():
            return current

        if not self._foundationdb_cluster_is_default(cluster_file):
            return current

        smoke_script = self.root / "scripts" / "smoke_foundationdb_credit_ledger_primitives.py"
        if not smoke_script.exists():
            return self._component(
                ok=False,
                state="missing-smoke-script",
                message="FoundationDB bootstrap smoke script is missing",
                cluster_file=str(cluster_file),
                script=str(smoke_script),
            )

        cluster_file.parent.mkdir(parents=True, exist_ok=True)
        container_name = (
            os.environ.get("MAIN_COMPUTER_FDB_CONTAINER_NAME")
            or os.environ.get("MAIN_COMPUTER_HUB_FDB_CONTAINER_NAME")
            or DEFAULT_FOUNDATIONDB_CONTAINER_NAME
        )
        fdb_port = _coerce_int_env("MAIN_COMPUTER_FDB_PORT", DEFAULT_FOUNDATIONDB_PORT, minimum=1, maximum=65535)
        docker_image = (
            os.environ.get("MAIN_COMPUTER_FDB_DOCKER_IMAGE")
            or os.environ.get("MAIN_COMPUTER_HUB_FDB_DOCKER_IMAGE")
            or DEFAULT_FOUNDATIONDB_DOCKER_IMAGE
        )
        start_timeout_s = _coerce_float_env(
            "MAIN_COMPUTER_FDB_START_TIMEOUT_S",
            DEFAULT_FOUNDATIONDB_START_TIMEOUT_S,
            minimum=1.0,
        )
        namespace = (
            os.environ.get("MAIN_COMPUTER_FDB_BOOTSTRAP_NAMESPACE")
            or DEFAULT_FOUNDATIONDB_NAMESPACE
        )

        command = [
            sys.executable,
            str(smoke_script),
            "--cluster-file",
            str(cluster_file),
            "--api-version",
            "740",
            "--namespace",
            str(namespace),
            "--concurrent-holds",
            "11",
            "--workers",
            "2",
            "--fdb-container-name",
            str(container_name),
            "--fdb-port",
            str(int(fdb_port)),
            "--fdb-docker-image",
            str(docker_image),
            "--docker-command",
            self.docker_command,
            "--docker-start-timeout",
            str(float(start_timeout_s)),
            "--keep-container",
            "--reuse-container",
        ]
        docker_platform = (os.environ.get("MAIN_COMPUTER_FDB_DOCKER_PLATFORM") or "").strip()
        if docker_platform:
            command.extend(["--docker-platform", docker_platform])

        result = self._run(command, timeout=max(float(start_timeout_s) + 180.0, 240.0))
        if result.returncode != 0:
            output = result.stderr or result.stdout or ""
            return self._component(
                ok=False,
                state="bootstrap-failed",
                message="FoundationDB Docker bootstrap failed",
                cluster_file=str(cluster_file),
                script=str(smoke_script),
                container_name=str(container_name),
                port=int(fdb_port),
                docker_image=str(docker_image),
                returncode=result.returncode,
                failed_command=_command_display(command),
                error=_truncate(output),
            )

        ready = self._check_foundationdb_light()
        ready.update(
            {
                "ok": True,
                "state": "ready",
                "message": "FoundationDB Docker bootstrap command succeeded",
                "bootstrapped": True,
                "container_name": str(container_name),
                "docker_image": str(docker_image),
                "stdout": _truncate(result.stdout or "", 2000),
                "post_bootstrap_check": {
                    "ok": bool(ready.get("ok")),
                    "state": str(ready.get("state") or "unknown"),
                    "message": str(ready.get("message") or ""),
                },
            }
        )
        return ready


    def _full_boot_reconcile(self) -> dict[str, Any]:
        state = self._base_state("booting")
        events: list[dict[str, Any]] = []
        pending = self._pending_boot_components()

        self._publish_boot_progress(
            message="checking WSL executor dependency",
            wsl=self._boot_progress_component(
                name="wsl",
                state="checking",
                message="checking WSL executor dependency",
                distribution=self.wsl_distribution,
            ),
            docker=pending["docker"],
            foundationdb=pending["foundationdb"],
            compose=pending["compose"],
        )
        wsl = self._reconcile_wsl_executor()

        self._publish_boot_progress(
            message="checking Docker engine dependency",
            wsl=wsl,
            docker=self._boot_progress_component(
                name="docker",
                state="checking",
                message="checking Docker engine dependency",
                docker_command=self.docker_command,
            ),
            foundationdb=pending["foundationdb"],
            compose=pending["compose"],
        )
        docker = self._reconcile_docker_engine()

        if docker.get("ok"):
            self._publish_boot_progress(
                message="checking FoundationDB Docker dependency",
                wsl=wsl,
                docker=docker,
                foundationdb=self._boot_progress_component(
                    name="foundationdb",
                    state="checking",
                    message="checking FoundationDB Docker dependency",
                    cluster_file=str(self._foundationdb_cluster_file()),
                ),
                compose=pending["compose"],
            )
            foundationdb = self._reconcile_foundationdb()
        else:
            foundationdb = self._component(
                ok=False,
                state="blocked",
                message="docker is not ready; FoundationDB bootstrap was not started",
                cluster_file=str(self._foundationdb_cluster_file()),
            )

        if docker.get("ok"):
            self._publish_boot_progress(
                message="checking executor Compose/image dependency",
                wsl=wsl,
                docker=docker,
                foundationdb=foundationdb,
                compose=self._boot_progress_component(
                    name="compose",
                    state="checking",
                    message="checking executor Compose/image dependency",
                    compose_file=str(self.compose_file),
                ),
            )
            compose = self._reconcile_compose_stack()
        else:
            compose = self._component(
                ok=False,
                state="blocked",
                message="docker is not ready; compose stack was not started",
                compose_file=str(self.compose_file),
            )

        ok = bool(wsl.get("ok") and docker.get("ok") and foundationdb.get("ok") and compose.get("ok"))
        state.update(
            {
                "ok": ok,
                "state": "ready" if ok else "down",
                "boot_proven": ok,
                "last_boot_reconcile_at": _now_iso(),
                "wsl": wsl,
                "docker": docker,
                "foundationdb": foundationdb,
                "compose": compose,
                "components": {
                    "wsl": wsl,
                    "docker": docker,
                    "foundationdb": foundationdb,
                    "compose": compose,
                },
                "events": events,
                "message": "executor infrastructure is ready" if ok else "executor infrastructure needs attention",
            }
        )
        if ok:
            state.pop("warning", None)
        else:
            state["warning"] = self._warning_from_state(state)
        state["service"]["state"] = state["state"]
        return state

    def _light_keepalive(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the cheapest useful checks after boot.

        The light path proves that Docker still answers, the default FDB
        coordinator is still reachable, and the executor Docker image is still
        available. It does not touch WSL because WSL repair/proof is handled by
        full boot reconcile before boot is complete.
        """

        docker = self._check_docker_light()
        if docker.get("ok"):
            foundationdb = self._check_foundationdb_light()
            compose = self._check_compose_light()
        else:
            foundationdb = self._component(
                ok=False,
                state="blocked",
                message="docker is down; FoundationDB light check was skipped",
                cluster_file=str(self._foundationdb_cluster_file()),
            )
            compose = self._component(
                ok=False,
                state="blocked",
                message="docker is down; compose light check was skipped",
                compose_file=str(self.compose_file),
            )

        if not docker.get("ok"):
            docker = self._reconcile_docker_engine()
            if docker.get("ok"):
                foundationdb = self._reconcile_foundationdb()
                compose = self._reconcile_compose_stack()
        elif not foundationdb.get("ok"):
            foundationdb = self._reconcile_foundationdb()
            if not compose.get("ok"):
                compose = self._reconcile_compose_stack()
        elif not compose.get("ok"):
            compose = self._reconcile_compose_stack()

        wsl = state.get("wsl") if isinstance(state.get("wsl"), dict) else {}
        ok = bool(wsl.get("ok") and docker.get("ok") and foundationdb.get("ok") and compose.get("ok"))
        state.update(
            {
                "ok": ok,
                "state": "ready" if ok else "degraded",
                "boot_proven": ok,
                "updated_at": _now_iso(),
                "last_light_check_at": _now_iso(),
                "docker": docker,
                "foundationdb": foundationdb,
                "compose": compose,
                "components": {
                    "wsl": wsl,
                    "docker": docker,
                    "foundationdb": foundationdb,
                    "compose": compose,
                },
                "message": "light keepalive passed" if ok else "light keepalive repaired or detected degraded infrastructure",
            }
        )
        if ok:
            state.pop("warning", None)
        else:
            state["warning"] = self._warning_from_state(state)
        state.setdefault("service", {})["state"] = "watching" if ok else "degraded"
        return state


    def _executor_fingerprint_changed(self) -> bool:
        fingerprint = self._executor_fingerprint()
        if fingerprint is None:
            return False
        if self._last_executor_fingerprint is None:
            self._last_executor_fingerprint = fingerprint
            return False
        if fingerprint != self._last_executor_fingerprint:
            self._last_executor_fingerprint = fingerprint
            return True
        return False

    def _executor_fingerprint(self) -> str | None:
        try:
            stat = self.repo_executor_path.stat()
        except OSError:
            return None
        return f"{stat.st_size}:{int(stat.st_mtime)}"

    def _reconcile_wsl_executor(self) -> dict[str, Any]:
        wsl_path = _which_or_path(self.wsl_command)
        if not wsl_path:
            return self._component(
                ok=False,
                state="missing",
                message=f"{self.wsl_command} was not found",
                distribution=self.wsl_distribution,
                wsl_command=self.wsl_command,
            )

        list_result = self._run([self.wsl_command, "--list", "--quiet"], timeout=10)
        names = _parse_wsl_distribution_names(list_result.stdout or "")
        if list_result.returncode != 0:
            return self._component(
                ok=False,
                state="down",
                message="wsl.exe exists but did not list distributions",
                distribution=self.wsl_distribution,
                wsl_path=wsl_path,
                error=_truncate(list_result.stderr or list_result.stdout or ""),
            )

        if self.wsl_distribution not in names:
            install_result = self._install_missing_wsl_distribution(wsl_path=wsl_path, installed_distributions=names)
            if not install_result.get("ok"):
                return install_result

            list_result = self._run([self.wsl_command, "--list", "--quiet"], timeout=10)
            names = _parse_wsl_distribution_names(list_result.stdout or "")
            if list_result.returncode != 0 or self.wsl_distribution not in names:
                return self._component(
                    ok=False,
                    state="missing-distro",
                    message=f"WSL distro {self.wsl_distribution!r} is still not installed after repair",
                    distribution=self.wsl_distribution,
                    installed_distributions=names,
                    wsl_path=wsl_path,
                    install_attempted=True,
                    reset_attempted=False,
                    error=_truncate(list_result.stderr or list_result.stdout or ""),
                )

        if not self.repo_executor_path.exists():
            return self._component(
                ok=False,
                state="missing-entrypoint",
                message="repo-owned executor entrypoint is missing",
                distribution=self.wsl_distribution,
                repo_entrypoint=str(self.repo_executor_path),
                wsl_path=wsl_path,
            )

        shim_result = self._install_wsl_executor_shim()
        if not shim_result.get("ok"):
            return shim_result

        contract_result = self._run_wsl_executor_contract()
        if not contract_result.get("ok"):
            return contract_result

        self._last_executor_fingerprint = self._executor_fingerprint()
        return self._component(
            ok=True,
            state="ready",
            message="WSL executor shim points to repo entrypoint and contract passed",
            distribution=self.wsl_distribution,
            wsl_path=wsl_path,
            repo_entrypoint=str(self.repo_executor_path),
            repo_entrypoint_wsl=_host_path_to_wsl(self.repo_executor_path),
            shim="/usr/local/bin/main-computer-exec",
            entrypoint_contract_ok=True,
        )

    def _runtime_profile_for_distribution(self) -> dict[str, str]:
        """Resolve the repo runtime profile for the configured WSL distro."""

        fallback = {
            "name": "test",
            "distributionName": self.wsl_distribution,
            "rootfsTar": "",
            "runtimeRoot": "",
        }
        config_path = self.root / "runtime" / "main-computer-runtime.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return fallback

        profiles = config.get("profiles") if isinstance(config, dict) else None
        if not isinstance(profiles, dict):
            return fallback

        for name, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            if str(profile.get("distributionName") or "").strip() == self.wsl_distribution:
                return {
                    "name": str(name),
                    "distributionName": self.wsl_distribution,
                    "rootfsTar": str(profile.get("rootfsTar") or fallback["rootfsTar"]),
                    "runtimeRoot": str(profile.get("runtimeRoot") or fallback["runtimeRoot"]),
                }

        default_name = str(config.get("defaultProfile") or fallback["name"])
        default_profile = profiles.get(default_name)
        if isinstance(default_profile, dict):
            return {
                "name": default_name,
                "distributionName": self.wsl_distribution,
                "rootfsTar": str(default_profile.get("rootfsTar") or fallback["rootfsTar"]),
                "runtimeRoot": str(default_profile.get("runtimeRoot") or fallback["runtimeRoot"]),
            }

        return fallback

    def _repo_relative_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.root / path

    def _expanded_path(self, value: str) -> Path | None:
        text = _expand_windows_envvars(value).strip()
        if not text:
            return None
        return Path(text).expanduser()

    def _runtime_image_path(self, profile: dict[str, str]) -> Path:
        profile_name = profile.get("name", "test")
        runtime_root_override = (
            os.environ.get("MAIN_COMPUTER_EXECUTOR_WSL_RUNTIME_ROOT", "").strip()
            or os.environ.get("MAIN_COMPUTER_WSL_RUNTIME_ROOT", "").strip()
        )
        if runtime_root_override:
            runtime_root = Path(runtime_root_override).expanduser()
            return runtime_root / "images" / _runtime_image_file_name(self.wsl_distribution)

        runtime_root = self._expanded_path(profile.get("runtimeRoot", ""))
        if runtime_root is not None:
            return runtime_root / "images" / _runtime_image_file_name(self.wsl_distribution)

        rootfs_tar = self._expanded_path(profile.get("rootfsTar", ""))
        if rootfs_tar is not None and rootfs_tar.is_absolute():
            return rootfs_tar

        return _default_wsl_runtime_root(profile_name, self.root) / "images" / _runtime_image_file_name(self.wsl_distribution)

    @staticmethod
    def _runtime_root_from_image_path(rootfs_tar: Path) -> Path | None:
        if rootfs_tar.parent.name.lower() == "images":
            return rootfs_tar.parent.parent
        return None

    def _run_powershell_script(self, script_path: Path, args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        command = [
            self.powershell_command,
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            *args,
        ]
        return self._run(command, timeout=timeout)

    def _install_missing_wsl_distribution(self, *, wsl_path: str, installed_distributions: list[str]) -> dict[str, Any]:
        """Import the configured WSL distro only when it is absent.

        This intentionally does not pass -Reset. Existing distros are left alone,
        and a non-empty orphaned install path fails closed instead of being
        overwritten.
        """

        profile = self._runtime_profile_for_distribution()
        profile_name = profile["name"]
        rootfs_tar = self._runtime_image_path(profile)
        runtime_root = self._runtime_root_from_image_path(rootfs_tar)
        build_script = self.root / "scripts" / "windows" / "build-main-computer-runtime.ps1"
        install_script = self.root / "scripts" / "windows" / "install-main-computer-runtime.ps1"

        if not install_script.exists():
            return self._component(
                ok=False,
                state="missing-runtime-installer",
                message="WSL runtime installer script is missing",
                distribution=self.wsl_distribution,
                profile=profile_name,
                install_script=str(install_script),
                installed_distributions=installed_distributions,
                wsl_path=wsl_path,
                install_attempted=False,
                reset_attempted=False,
            )

        if not rootfs_tar.exists():
            if not build_script.exists():
                return self._component(
                    ok=False,
                    state="missing-runtime-image",
                    message="WSL runtime image is missing and the runtime builder script is unavailable",
                    distribution=self.wsl_distribution,
                    profile=profile_name,
                    runtime_image=str(rootfs_tar),
                    build_script=str(build_script),
                    installed_distributions=installed_distributions,
                    wsl_path=wsl_path,
                    install_attempted=False,
                    reset_attempted=False,
                )

            build_args = [
                "-Profile",
                profile_name,
                "-OutputPath",
                str(rootfs_tar),
                "-DistributionName",
                self.wsl_distribution,
            ]
            if runtime_root is not None:
                build_args.extend(["-RuntimeRoot", str(runtime_root)])

            build_result = self._run_powershell_script(
                build_script,
                build_args,
                timeout=900,
            )
            if build_result.returncode != 0 or not rootfs_tar.exists():
                return self._component(
                    ok=False,
                    state="runtime-build-failed",
                    message="could not build missing WSL runtime image",
                    distribution=self.wsl_distribution,
                    profile=profile_name,
                    runtime_image=str(rootfs_tar),
                    build_script=str(build_script),
                    installed_distributions=installed_distributions,
                    wsl_path=wsl_path,
                    install_attempted=False,
                    reset_attempted=False,
                    stdout=_truncate(build_result.stdout or "", 2000),
                    error=_truncate(build_result.stderr or build_result.stdout or ""),
                )

        install_args = [
            "-Profile",
            profile_name,
            "-RuntimeImagePath",
            str(rootfs_tar),
            "-DistributionName",
            self.wsl_distribution,
            "-WslCommand",
            self.wsl_command,
        ]
        if runtime_root is not None:
            install_args.extend(["-RuntimeRoot", str(runtime_root)])

        install_result = self._run_powershell_script(
            install_script,
            install_args,
            timeout=300,
        )
        if install_result.returncode != 0:
            return self._component(
                ok=False,
                state="runtime-install-failed",
                message="could not install missing WSL runtime distro without reset",
                distribution=self.wsl_distribution,
                profile=profile_name,
                runtime_image=str(rootfs_tar),
                install_script=str(install_script),
                installed_distributions=installed_distributions,
                wsl_path=wsl_path,
                install_attempted=True,
                reset_attempted=False,
                stdout=_truncate(install_result.stdout or "", 2000),
                error=_truncate(install_result.stderr or install_result.stdout or ""),
            )

        return self._component(
            ok=True,
            state="installed",
            message="missing WSL runtime distro was installed without reset",
            distribution=self.wsl_distribution,
            profile=profile_name,
            runtime_image=str(rootfs_tar),
            install_script=str(install_script),
            installed_distributions=installed_distributions,
            wsl_path=wsl_path,
            install_attempted=True,
            reset_attempted=False,
            stdout=_truncate(install_result.stdout or "", 2000),
        )

    def _install_wsl_executor_shim(self) -> dict[str, Any]:
        repo_entrypoint_wsl = _host_path_to_wsl(self.repo_executor_path)
        shim = "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"exec /bin/bash {shlex.quote(repo_entrypoint_wsl)} \"$@\"",
                "",
            ]
        )
        script = "\n".join(
            [
                "set -e",
                "cat > /usr/local/bin/main-computer-exec <<'MAIN_COMPUTER_EXEC_SHIM'",
                shim.rstrip("\n"),
                "MAIN_COMPUTER_EXEC_SHIM",
                "chmod 0755 /usr/local/bin/main-computer-exec",
                "/usr/local/bin/main-computer-exec --version",
            ]
        )
        result = self._run(
            [
                self.wsl_command,
                "--distribution",
                self.wsl_distribution,
                "--user",
                "root",
                "--exec",
                "/bin/sh",
                "-lc",
                script,
            ],
            timeout=15,
        )
        if result.returncode != 0:
            return self._component(
                ok=False,
                state="repair-failed",
                message="could not install WSL executor shim",
                distribution=self.wsl_distribution,
                shim="/usr/local/bin/main-computer-exec",
                repo_entrypoint=str(self.repo_executor_path),
                error=_truncate(result.stderr or result.stdout or ""),
            )
        return self._component(
            ok=True,
            state="ready",
            message="WSL executor shim installed",
            distribution=self.wsl_distribution,
            shim="/usr/local/bin/main-computer-exec",
            repo_entrypoint=str(self.repo_executor_path),
            repo_entrypoint_wsl=repo_entrypoint_wsl,
        )

    def _run_wsl_executor_contract(self) -> dict[str, Any]:
        script = "\n".join(
            [
                "set -e",
                "rm -rf /tmp/main-computer-executor-service",
                "mkdir -p /tmp/main-computer-executor-service/workspace /tmp/main-computer-executor-service/outputs",
                "rm -rf /workspace /outputs",
                "ln -s /tmp/main-computer-executor-service/workspace /workspace",
                "ln -s /tmp/main-computer-executor-service/outputs /outputs",
                "/usr/local/bin/main-computer-exec run --cwd /workspace --timeout-ms 5000 --artifact-dir /outputs -- 'echo main-computer-exec-ready' | grep -q main-computer-exec-ready",
                "echo entrypoint-contract-ok",
            ]
        )
        result = self._run(
            [
                self.wsl_command,
                "--distribution",
                self.wsl_distribution,
                "--user",
                "root",
                "--exec",
                "/bin/sh",
                "-lc",
                script,
            ],
            timeout=15,
        )
        ok = result.returncode == 0 and "entrypoint-contract-ok" in (result.stdout or "")
        return self._component(
            ok=ok,
            state="ready" if ok else "contract-failed",
            message="WSL executor contract passed" if ok else "WSL executor contract failed",
            distribution=self.wsl_distribution,
            entrypoint_contract_ok=ok,
            stdout=_truncate(result.stdout or "", 1000),
            error="" if ok else _truncate(result.stderr or result.stdout or ""),
        )

    def _reconcile_docker_engine(self) -> dict[str, Any]:
        docker_path = _which_or_path(self.docker_command)
        if not docker_path:
            return self._component(
                ok=False,
                state="missing",
                message=f"{self.docker_command} was not found",
                docker_command=self.docker_command,
            )

        version = self._run([self.docker_command, "version"], timeout=12)
        started = False
        if version.returncode != 0:
            started = self._try_start_docker_desktop()
            deadline = self.time() + self.docker_start_timeout_s
            while self.time() < deadline:
                self.sleep(2.0)
                version = self._run([self.docker_command, "version"], timeout=12)
                if version.returncode == 0:
                    break

        if version.returncode != 0:
            return self._component(
                ok=False,
                state="down",
                message="docker exists but the engine is not responding",
                docker_command=self.docker_command,
                docker_path=docker_path,
                start_attempted=started,
                error=_truncate(version.stderr or version.stdout or ""),
            )

        compose = self._run([self.docker_command, "compose", "version"], timeout=12)
        if compose.returncode != 0:
            return self._component(
                ok=False,
                state="missing-compose",
                message="docker engine is available but docker compose is not",
                docker_command=self.docker_command,
                docker_path=docker_path,
                engine_available=True,
                error=_truncate(compose.stderr or compose.stdout or ""),
            )

        return self._component(
            ok=True,
            state="ready",
            message="Docker engine and compose are available",
            docker_command=self.docker_command,
            docker_path=docker_path,
            engine_available=True,
            compose_available=True,
            start_attempted=started,
            version_stdout=_truncate(version.stdout or "", 1000),
            compose_stdout=_truncate(compose.stdout or "", 1000),
        )

    def _try_start_docker_desktop(self) -> bool:
        if os.name != "nt":
            return False
        docker_desktop = Path(os.environ.get("MAIN_COMPUTER_DOCKER_DESKTOP_EXE", r"C:\Program Files\Docker\Docker\Docker Desktop.exe"))
        if not docker_desktop.exists():
            return False
        try:
            self._run(["cmd.exe", "/c", "start", "", str(docker_desktop)], timeout=5)
            return True
        except Exception:
            return False

    def _check_docker_light(self) -> dict[str, Any]:
        docker_path = _which_or_path(self.docker_command)
        if not docker_path:
            return self._component(ok=False, state="missing", message=f"{self.docker_command} was not found")
        result = self._run([self.docker_command, "ps", "--format", "{{.Names}}"], timeout=10)
        return self._component(
            ok=result.returncode == 0,
            state="ready" if result.returncode == 0 else "down",
            message="docker ps succeeded" if result.returncode == 0 else "docker ps failed",
            docker_path=docker_path,
            error="" if result.returncode == 0 else _truncate(result.stderr or result.stdout or ""),
        )

    def _compose_command(self, *args: str) -> list[str]:
        command = [self.docker_command, "compose"]
        if self.compose_project:
            command.extend(["--project-name", self.compose_project])
        command.extend(["-f", str(self.compose_file), *args])
        return command

    def _reconcile_compose_stack(self) -> dict[str, Any]:
        if not self.compose_file.exists():
            return self._component(
                ok=False,
                state="missing-compose-file",
                message="debug compose file is missing",
                compose_file=str(self.compose_file),
                compose_project=self.compose_project or None,
                image=self.executor_image,
            )

        command = self._compose_command("--profile", "executor", "build", "executor-image")
        result = self._run(
            command,
            timeout=300,
        )
        ok = result.returncode == 0
        output = result.stderr or result.stdout or ""
        payload = self._component(
            ok=ok,
            state="ready" if ok else "down",
            message="executor Docker image is built" if ok else "executor Docker image build failed",
            compose_file=str(self.compose_file),
            compose_project=self.compose_project or None,
            image=self.executor_image,
            built=ok,
            started=False,
            stdout=_truncate(result.stdout or "", 2000),
            error="" if ok else _truncate(output),
        )
        if not ok:
            payload.update(
                {
                    "warning": _compose_failure_warning(output, action="build the executor Docker image"),
                    "remediation": _compose_failure_remediation(output, action="the executor Docker image build"),
                    "returncode": result.returncode,
                    "failed_command": _command_display(command),
                }
            )
        return payload

    def _check_compose_light(self) -> dict[str, Any]:
        if not self.compose_file.exists():
            return self._component(
                ok=False,
                state="missing-compose-file",
                message="debug compose file is missing",
                compose_file=str(self.compose_file),
                compose_project=self.compose_project or None,
                image=self.executor_image,
            )

        command = [self.docker_command, "image", "inspect", self.executor_image]
        result = self._run(
            command,
            timeout=15,
        )
        ok = result.returncode == 0
        output = result.stderr or result.stdout or ""
        payload = self._component(
            ok=ok,
            state="ready" if ok else "down",
            message="executor Docker image is available" if ok else "executor Docker image is missing",
            compose_file=str(self.compose_file),
            compose_project=self.compose_project or None,
            image=self.executor_image,
            built=ok,
            error="" if ok else _truncate(output),
        )
        if not ok:
            payload.update(
                {
                    "warning": "Docker executor image is missing; the executor service will rebuild it.",
                    "remediation": "Re-run start_v2.bat or run the executor service boot check to rebuild the executor image.",
                    "returncode": result.returncode,
                    "failed_command": _command_display(command),
                }
            )
        return payload

    def _write_state(self, state: dict[str, Any]) -> None:
        with self._state_lock:
            self._write_state_unlocked(state)

    def _write_state_unlocked(self, state: dict[str, Any]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        state = dict(state)
        state.setdefault("updated_at", _now_iso())
        state.setdefault("service", {})
        state["service"] = dict(state["service"])
        state["service"].setdefault("pid", os.getpid())
        state["service"].setdefault("pid_file", str(self.pid_path))
        state["service"].setdefault("heartbeat_at", _now_iso())
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.state_path)
        self._last_state = state


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Boot and keep alive the Main Computer local executor infrastructure.")
    parser.add_argument("--root", default=".", help="Repository/build root. Defaults to the current directory.")
    parser.add_argument("--wsl-distribution", default=os.environ.get("MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION", "MainComputerExecutorTest"))
    parser.add_argument("--wsl-command", default=os.environ.get("MAIN_COMPUTER_EXECUTOR_WSL_COMMAND", "wsl.exe"))
    parser.add_argument("--docker-command", default=os.environ.get("MAIN_COMPUTER_DOCKER_COMMAND", "docker"))
    parser.add_argument("--powershell-command", default=os.environ.get("MAIN_COMPUTER_POWERSHELL_COMMAND", "powershell.exe"))
    parser.add_argument("--compose-file", default=os.environ.get("MAIN_COMPUTER_EXECUTOR_SERVICE_COMPOSE_FILE"))
    parser.add_argument("--heartbeat-interval-s", type=float, default=float(os.environ.get("MAIN_COMPUTER_EXECUTOR_SERVICE_HEARTBEAT_S", DEFAULT_HEARTBEAT_INTERVAL_S)))
    parser.add_argument("--light-check-interval-s", type=float, default=float(os.environ.get("MAIN_COMPUTER_EXECUTOR_SERVICE_LIGHT_CHECK_S", DEFAULT_LIGHT_CHECK_INTERVAL_S)))

    subparsers = parser.add_subparsers(dest="command")

    boot = subparsers.add_parser("boot", help="Reconcile infrastructure once, optionally remaining resident.")
    boot.add_argument("--watch", action="store_true", help="Remain alive, retrying boot on the heartbeat cadence until ready.")
    boot.add_argument("--max-watch-loops", type=int, default=None, help=argparse.SUPPRESS)

    status = subparsers.add_parser("status", help="Print the last executor-service state JSON.")
    status.add_argument("--json", action="store_true", help="Kept for compatibility; status always prints JSON.")

    return parser


def _service_from_args(args: argparse.Namespace) -> ExecutorService:
    return ExecutorService(
        root=args.root,
        wsl_distribution=args.wsl_distribution,
        wsl_command=args.wsl_command,
        docker_command=args.docker_command,
        powershell_command=args.powershell_command,
        compose_file=args.compose_file,
        heartbeat_interval_s=args.heartbeat_interval_s,
        light_check_interval_s=args.light_check_interval_s,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "boot"
    if command != "status":
        install_main_log_hooks_from_env(default_service_name=SERVICE_NAME, root=args.root)

    if command == "status":
        print(json.dumps(load_executor_service_state(args.root), indent=2, sort_keys=True))
        return 0

    service = _service_from_args(args)
    state = service.boot(watch=bool(getattr(args, "watch", False)), max_watch_loops=getattr(args, "max_watch_loops", None))
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0 if state.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
