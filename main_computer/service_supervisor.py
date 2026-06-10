from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Protocol, TextIO

from main_computer.main_log_client import (
    DEFAULT_MAIN_LOG_HOST,
    DEFAULT_MAIN_LOG_PORT,
    ENV_MAIN_LOG_HOST,
    ENV_MAIN_LOG_PORT,
    ENV_MAIN_LOG_URL,
    emit_main_log_event,
    healthcheck_main_log,
)
from main_computer.service_control import (
    complete_control_request,
    control_status,
    enqueue_applications_action,
    pending_control_requests,
)
from main_computer.executor_service import (
    EXECUTOR_SERVICE_PID_FILENAME,
    SERVICE_NAME as EXECUTOR_SERVICE_NAME,
)


SleepFunc = Callable[[float], None]
TimeFunc = Callable[[], float]
OutputFunc = Callable[[str], None]
ProcessCommandReader = Callable[[int], str | None]
ProcessTerminator = Callable[[int], None]


DEFAULT_POLL_INTERVAL_S = 5.0
SERVICE_NAME = "main-computer-service-supervisor"
SERVICE_SUPERVISOR_PID_FILENAME = ".main_computer_service_supervisor.pid"
GENERIC_PYTHON_COMMANDS = {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}
MAIN_LOG_CHILD_NAME = "main-log"
MAIN_LOG_SERVICE_NAME = "main-computer-main-log-service"
MAIN_LOG_READY_TIMEOUT_S = 3.0
MAIN_LOG_RESTART_EMIT_TIMEOUT_S = 0.2
MAIN_LOG_STREAM_EMIT_TIMEOUT_S = 0.05
STREAM_DRAIN_JOIN_TIMEOUT_S = 2.0
EXECUTOR_CHILD_NAME = "executor"
EXECUTOR_HEARTBEAT_STARTUP_GRACE_S = 90.0


def resolve_python_command(python_command: str | None = None) -> str:
    """Return the concrete interpreter used to launch child services.

    Plain ``python`` is fragile on Windows because child processes may resolve it
    through App Execution Aliases instead of the active virtual environment.
    Treat generic Python launcher names as a request to reuse this supervisor's
    interpreter, which preserves installed packages such as Paramiko.
    """

    command = str(python_command or os.environ.get("MAIN_COMPUTER_PYTHON_COMMAND") or "").strip()
    if not command or command.casefold() in GENERIC_PYTHON_COMMANDS:
        return sys.executable
    return command


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process_stream_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _normalized_command_line(value: str | None) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split()).casefold()


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
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return
    os.kill(int(pid), signal.SIGTERM)


def _safe_resolved_path(value: Any) -> str:
    try:
        return str(Path(str(value)).expanduser().resolve())
    except Exception:
        return str(value or "")


def _pid_entry_from_text(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            return {"pid": int(text)}
        except ValueError:
            return {"parse_error": "PID file was neither JSON nor a plain integer"}
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, int):
        return {"pid": payload}
    return {"parse_error": "PID file JSON must be an object"}


def _pid_from_entry(entry: dict[str, Any]) -> int | None:
    try:
        pid = int(entry.get("pid"))
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _looks_like_service_supervisor_command(command_line: str | None) -> bool:
    normalized = _normalized_command_line(command_line)
    return (
        "main_computer.service_supervisor" in normalized
        or "service_supervisor.py" in normalized
        or ("main_computer.app_control" in normalized and " bootstrap" in f" {normalized} ")
        or ("app_control.py" in normalized and " bootstrap" in f" {normalized} ")
    )


def _pid_file_command_matches_live(entry: dict[str, Any], live_command_line: str | None) -> bool:
    recorded = _normalized_command_line(str(entry.get("command_line") or ""))
    live = _normalized_command_line(live_command_line)
    return bool(recorded and live and (recorded in live or live in recorded))


def _main_log_url_from_environment() -> str:
    explicit = str(os.environ.get(ENV_MAIN_LOG_URL) or "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = str(os.environ.get(ENV_MAIN_LOG_HOST) or DEFAULT_MAIN_LOG_HOST).strip() or DEFAULT_MAIN_LOG_HOST
    port_text = str(os.environ.get(ENV_MAIN_LOG_PORT) or DEFAULT_MAIN_LOG_PORT).strip()
    try:
        port = int(port_text)
    except ValueError:
        port = DEFAULT_MAIN_LOG_PORT
    return f"http://{host}:{port}"


def _main_log_port_from_environment() -> int:
    port_text = str(os.environ.get(ENV_MAIN_LOG_PORT) or DEFAULT_MAIN_LOG_PORT).strip()
    try:
        port = int(port_text)
    except ValueError:
        port = DEFAULT_MAIN_LOG_PORT
    return port if 1 <= port <= 65535 else DEFAULT_MAIN_LOG_PORT


def _main_log_host_from_environment() -> str:
    return str(os.environ.get(ENV_MAIN_LOG_HOST) or DEFAULT_MAIN_LOG_HOST).strip() or DEFAULT_MAIN_LOG_HOST


def _coerce_float_env(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name) or "").strip())
    except ValueError:
        return default


class ProcessLike(Protocol):
    @property
    def pid(self) -> int:
        ...

    def poll(self) -> int | None:
        ...


ProcessFactory = Callable[[list[str], Path, Path, Path], ProcessLike]


@dataclass(frozen=True)
class ChildSpec:
    name: str
    module: str
    args: tuple[str, ...] = ()
    role: str = "service"
    start_priority: int = 10
    stop_priority: int = 10

    @property
    def is_logging(self) -> bool:
        return self.role == "logging" or self.name == MAIN_LOG_CHILD_NAME

    def command(self, *, python_command: str, root: Path) -> list[str]:
        args = list(self.args)
        if self.module == "main_computer.app_control" and "--port" not in args:
            control_port = str(os.environ.get("MAIN_COMPUTER_CONTROL_PORT") or "").strip()
            if control_port:
                args = ["--port", control_port, *args]
        return [python_command, "-m", self.module, "--root", str(root), *args]


@dataclass
class ChildRuntime:
    spec: ChildSpec
    process: ProcessLike
    command: list[str]
    stdout_path: Path
    stderr_path: Path
    started_at: str
    restart_count: int = 0
    last_exit_code: int | None = None
    stdout_handle: TextIO | None = None
    stderr_handle: TextIO | None = None
    stream_threads: tuple[threading.Thread, ...] = ()
    started_monotonic: float = 0.0

    def close_logs(self) -> None:
        for handle_name in ("stdout_handle", "stderr_handle"):
            handle = getattr(self, handle_name)
            if handle is not None:
                try:
                    handle.close()
                finally:
                    setattr(self, handle_name, None)
        for thread in self.stream_threads:
            try:
                thread.join(timeout=STREAM_DRAIN_JOIN_TIMEOUT_S)
            except RuntimeError:
                pass


CHILD_SPECS: tuple[ChildSpec, ...] = (
    ChildSpec(
        name=MAIN_LOG_CHILD_NAME,
        module="main_computer.main_log_service",
        args=("serve",),
        role="logging",
        start_priority=0,
        stop_priority=1000,
    ),
    ChildSpec(name="app", module="main_computer.app_control", args=("run",)),
    ChildSpec(name="executor", module="main_computer.executor_service", args=("boot", "--watch")),
    ChildSpec(name="applications", module="main_computer.applications_service", args=("boot", "--watch")),
    ChildSpec(name="blockchain", module="main_computer.blockchain_service", args=("boot", "--watch")),
)


class ServiceSupervisor:
    """Start and supervise the resident Main Computer services.

    The app, executor, applications, and blockchain services each own their own
    boot behavior. This supervisor is the outer resident process: it starts
    those service processes and restarts any one of them if the Python process
    exits.
    """

    def __init__(
        self,
        *,
        root: Path | str,
        python_command: str = "python",
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        sleep_func: SleepFunc | None = None,
        time_func: TimeFunc | None = None,
        output_func: OutputFunc | None = print,
        process_command_reader: ProcessCommandReader | None = None,
        process_terminator: ProcessTerminator | None = None,
        process_factory: ProcessFactory | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.python_command = resolve_python_command(python_command)
        self.poll_interval_s = max(1.0, float(poll_interval_s))
        self.sleep = sleep_func or time.sleep
        self.time = time_func or time.monotonic
        self.output = output_func
        self.process_command_reader = process_command_reader or _read_process_command_line_default
        self.process_terminator = process_terminator or _terminate_process_default
        self.process_factory = process_factory

        self.runtime_dir = self.root / "runtime" / "service_supervisor"
        self.state_path = self.runtime_dir / "state.json"
        self.log_path = self.runtime_dir / "service.log"
        self.pid_path = self.root / SERVICE_SUPERVISOR_PID_FILENAME
        self._children: dict[str, ChildRuntime] = {}
        self._last_state: dict[str, Any] = {}
        self._self_restart_requested = False
        self._shutdown_requested = False
        self._shutdown_reason = ""
        self._replacement_started = False
        self.executor_heartbeat_startup_grace_s = max(
            self.poll_interval_s,
            _coerce_float_env("MAIN_COMPUTER_EXECUTOR_HEARTBEAT_STARTUP_GRACE_S", EXECUTOR_HEARTBEAT_STARTUP_GRACE_S),
        )
        self.main_log_host = _main_log_host_from_environment()
        self.main_log_port = _main_log_port_from_environment()
        self.main_log_url = _main_log_url_from_environment()
        os.environ.setdefault(ENV_MAIN_LOG_HOST, self.main_log_host)
        os.environ.setdefault(ENV_MAIN_LOG_PORT, str(self.main_log_port))
        os.environ.setdefault(ENV_MAIN_LOG_URL, self.main_log_url)

    def supervise(self, *, max_loops: int | None = None) -> dict[str, Any]:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        pid_claim = self._claim_pid_file()
        self._emit(
            "service supervisor starting",
            pid=os.getpid(),
            pid_file=str(self.pid_path),
            root=str(self.root),
        )

        loops = 0
        state = self._write_supervisor_state(state="starting", pid_claim=pid_claim)
        try:
            while max_loops is None or loops < max_loops:
                loops += 1
                for spec in self._start_ordered_specs():
                    child = self._children.get(spec.name)
                    if child is None:
                        self._start_child(spec)
                        continue

                    exit_code = child.process.poll()
                    if exit_code is not None:
                        child.last_exit_code = int(exit_code)
                        restart_count = child.restart_count + 1
                        self._emit(
                            "child service exited; restarting",
                            child=spec.name,
                            exit_code=exit_code,
                            restart_count=restart_count,
                        )
                        child.close_logs()
                        self._start_child(spec, restart_count=restart_count, last_exit_code=int(exit_code))
                        continue

                    heartbeat_problem = self._executor_heartbeat_restart_reason(child)
                    if heartbeat_problem:
                        self._restart_child(
                            spec.name,
                            source="supervisor-heartbeat-watchdog",
                            parameters={"reason": heartbeat_problem},
                        )

                self._process_control_queue()
                if self._shutdown_requested:
                    state = self._write_supervisor_state(state="stopping", pid_claim=pid_claim, ok=False)
                    self._emit("service supervisor shutdown requested", reason=self._shutdown_reason or "control-request")
                    self._stop_children()
                    state = self._write_supervisor_state(state="stopped", pid_claim=pid_claim, ok=True)
                    return state
                if self._self_restart_requested:
                    state = self._write_supervisor_state(state="restarting", pid_claim=pid_claim, ok=False)
                    self._start_replacement_supervisor()
                    return state

                state = self._write_supervisor_state(state="supervising", pid_claim=pid_claim)

                if max_loops is not None and loops >= max_loops:
                    break
                self.sleep(self.poll_interval_s)
        except KeyboardInterrupt:
            state = self._write_supervisor_state(state="stopping", pid_claim=pid_claim, ok=False)
            self._emit("service supervisor stopping", reason="keyboard-interrupt")
            self._stop_children()
        finally:
            if max_loops is None and not self._replacement_started:
                self._release_pid_file_if_current()
        return self._last_state or state

    def _spec_order_index(self, name: str) -> int:
        for index, spec in enumerate(CHILD_SPECS):
            if spec.name == name:
                return index
        return len(CHILD_SPECS)

    def _start_ordered_specs(self) -> list[ChildSpec]:
        return [
            spec
            for _index, spec in sorted(
                enumerate(CHILD_SPECS),
                key=lambda item: (item[1].start_priority, item[0]),
            )
        ]

    def _stop_ordered_children(self) -> list[tuple[str, ChildRuntime]]:
        return sorted(self._children.items(), key=lambda item: (item[1].spec.stop_priority, self._spec_order_index(item[0])))

    def _child_env(self, spec: ChildSpec) -> dict[str, str]:
        env = os.environ.copy()
        env["MAIN_COMPUTER_ROOT"] = str(self.root)
        env["MAIN_COMPUTER_SERVICE_NAME"] = spec.name
        env[ENV_MAIN_LOG_HOST] = self.main_log_host
        env[ENV_MAIN_LOG_PORT] = str(self.main_log_port)
        env[ENV_MAIN_LOG_URL] = self.main_log_url
        env["MAIN_COMPUTER_MAIN_LOG_HOOKS"] = "0" if spec.is_logging else "1"
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    def _wait_for_main_log_ready(self) -> dict[str, Any]:
        if self.process_factory is not None:
            return {"ok": True, "state": "skipped", "message": "process factory test mode"}
        timeout_s = max(0.0, _coerce_float_env("MAIN_COMPUTER_MAIN_LOG_READY_TIMEOUT_S", MAIN_LOG_READY_TIMEOUT_S))
        deadline = self.time() + timeout_s
        last: dict[str, Any] = {"ok": False, "state": "not-checked"}
        while True:
            last = healthcheck_main_log(url=self.main_log_url, timeout_s=MAIN_LOG_RESTART_EMIT_TIMEOUT_S)
            if last.get("ok"):
                return last
            now = self.time()
            if now >= deadline:
                self._safe_restart_emit(
                    "main-log health check failed; continuing restart fail-open",
                    url=self.main_log_url,
                    healthcheck=last,
                    timeout_s=timeout_s,
                )
                return last
            self.sleep(min(0.1, max(0.0, deadline - now)))

    def _safe_restart_emit(self, message: str, *, url: str | None = None, **fields: Any) -> dict[str, Any]:
        event = {
            "service": SERVICE_NAME,
            "source_service": SERVICE_NAME,
            "kind": "supervisor-restart",
            "stream": "event",
            "message": message,
            **fields,
        }
        return emit_main_log_event(
            event,
            url=url or self.main_log_url,
            timeout_s=MAIN_LOG_RESTART_EMIT_TIMEOUT_S,
            fallback_output=self.output,
            fallback_on_error=True,
        )

    def _emit_child_stream_to_main_log(self, *, child: str, stream: str, path: Path, chunk: str) -> None:
        if child == MAIN_LOG_CHILD_NAME:
            return
        text = str(chunk)
        if not text:
            return
        emit_main_log_event(
            {
                "service": SERVICE_NAME,
                "source_service": child,
                "kind": "child-stream",
                "stream": stream,
                "path": str(path),
                "message": text.rstrip("\n"),
            },
            url=self.main_log_url,
            timeout_s=MAIN_LOG_STREAM_EMIT_TIMEOUT_S,
            fallback_on_error=False,
        )

    def _start_stream_drain(
        self,
        *,
        child: str,
        stream: str,
        pipe: Any,
        path: Path,
        forward_to_main_log: bool,
    ) -> threading.Thread:
        def _drain() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("a", encoding="utf-8") as handle:
                    while True:
                        chunk = pipe.readline()
                        if not chunk:
                            break
                        if isinstance(chunk, bytes):
                            text = chunk.decode("utf-8", errors="replace")
                        else:
                            text = str(chunk)
                        handle.write(text)
                        handle.flush()
                        if forward_to_main_log:
                            self._emit_child_stream_to_main_log(child=child, stream=stream, path=path, chunk=text)
            except Exception as exc:  # pragma: no cover - defensive stream drain
                self._safe_restart_emit(
                    "child stream drain failed; continuing supervisor",
                    child=child,
                    stream=stream,
                    path=str(path),
                    error=str(exc),
                )
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        thread = threading.Thread(target=_drain, name=f"main-computer-{child}-{stream}-drain", daemon=True)
        thread.start()
        return thread

    def _process_control_queue(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for request in pending_control_requests(self.root, channel="supervisor", limit=50):
            try:
                result = self._handle_control_request(request.payload)
            except Exception as exc:  # pragma: no cover - defensive control boundary
                result = {
                    "ok": False,
                    "state": "failed",
                    "error": str(exc),
                    "message": "supervisor control request failed",
                }
            complete_control_request(request, result=result)
            results.append({"request": request.payload, "result": result})
        if results:
            self._emit("processed supervisor control requests", count=len(results))
        return results

    def _handle_control_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "").strip().lower()
        target = str(payload.get("target") or "").strip().lower()
        source = str(payload.get("source") or "unknown").strip() or "unknown"
        parameters = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}

        if action in {"shutdown", "stop", "halt"}:
            if target not in {"", "system", "all", "services", "children", "supervisor", "service-supervisor"}:
                return {
                    "ok": False,
                    "state": "unknown-target",
                    "message": f"unknown supervisor shutdown target: {target}",
                    "action": action,
                    "target": target,
                    "allowed_targets": ["system", "all", "services", "children", "supervisor"],
                }
            self._shutdown_requested = True
            self._shutdown_reason = f"{action}:{target or 'system'} from {source}"
            return {
                "ok": True,
                "state": "accepted",
                "message": "supervisor system shutdown accepted",
                "action": action,
                "target": target or "system",
                "source": source,
            }

        if action != "restart":
            return {
                "ok": False,
                "state": "unsupported-action",
                "message": f"unsupported supervisor action: {action}",
                "action": action,
                "target": target,
            }

        if target in {"supervisor", "self", "service-supervisor"}:
            self._self_restart_requested = True
            return {
                "ok": True,
                "state": "accepted",
                "message": "supervisor self-restart accepted",
                "action": action,
                "target": target,
                "source": source,
            }

        if target in {"app", "viewport", "main-app", "main_app", "ui"}:
            return self._restart_child("app", source=source, parameters=parameters)

        if target in {"executor", "executor_service", "executor-service"}:
            return self._restart_child("executor", source=source, parameters=parameters)

        if target in {"applications", "applications_service", "applications-service"}:
            return self._restart_child("applications", source=source, parameters=parameters)

        if target in {"blockchain", "chain", "anvil", "ethereum"}:
            return self._restart_child("blockchain", source=source, parameters=parameters)

        if target in {"main-log", "main_log", "logging", "logger"}:
            return self._restart_child(MAIN_LOG_CHILD_NAME, source=source, parameters=parameters)

        if target in {"services", "children", "all"}:
            return self._restart_all_children(source=source, parameters=parameters)

        proxied = self._proxy_application_restart(target=target, source=source, parameters=parameters)
        if proxied.get("ok"):
            return proxied

        return {
            "ok": False,
            "state": "unknown-target",
            "message": f"unknown supervisor restart target: {target}",
            "action": action,
            "target": target,
            "allowed_targets": [
                "supervisor",
                "app",
                "executor",
                "applications",
                "blockchain",
                "main-log",
                "all",
                "onlyoffice",
                "gitea",
                "coolify",
                "postgres",
                "redis",
                "soketi",
            ],
        }

    def _restart_child(self, name: str, *, source: str = "", parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        spec = next((candidate for candidate in CHILD_SPECS if candidate.name == name), None)
        if spec is None:
            return {
                "ok": False,
                "state": "unknown-child",
                "message": f"unknown child service: {name}",
                "child": name,
            }

        child = self._children.get(name)
        restart_count = 0
        last_exit_code: int | None = None
        previous_pid: int | None = None
        if child is not None:
            previous_pid = int(child.process.pid)
            restart_count = child.restart_count + 1
            polled = child.process.poll()
            if polled is not None:
                last_exit_code = int(polled)
            self._stop_child_runtime(name, child, reason=f"requested restart from {source or 'unknown'}")

        started = self._start_child(spec, restart_count=restart_count, last_exit_code=last_exit_code)
        return {
            "ok": True,
            "state": "restarted",
            "message": f"{name} child service restart requested",
            "child": name,
            "previous_pid": previous_pid,
            "pid": int(started.process.pid),
            "restart_count": restart_count,
            "source": source,
            "parameters": dict(parameters or {}),
        }

    def _restart_all_children(self, *, source: str = "", parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        restart_counts: dict[str, int] = {}
        last_exit_codes: dict[str, int | None] = {}
        previous_pids: dict[str, int] = {}
        for name, child in self._children.items():
            restart_counts[name] = child.restart_count + 1
            polled = child.process.poll()
            last_exit_codes[name] = int(polled) if polled is not None else child.last_exit_code
            previous_pids[name] = int(child.process.pid)

        self._safe_restart_emit(
            "full supervised restart requested",
            source=source,
            parameters=dict(parameters or {}),
            previous_pids=previous_pids,
        )
        self._stop_children(exclude_roles={"logging"}, reason="full-restart")
        self._safe_restart_emit("full restart stopped non-logging services", previous_pids=previous_pids)
        self._stop_children(only_roles={"logging"}, reason="full-restart")
        if self.output is not None:
            self.output(
                json.dumps(
                    {
                        "at": _now_iso(),
                        "service": SERVICE_NAME,
                        "message": "old main-log stopped; starting replacement main-log first",
                        "main_log_fallback": True,
                    },
                    sort_keys=True,
                )
            )

        results: list[dict[str, Any]] = []
        for spec in self._start_ordered_specs():
            started = self._start_child(
                spec,
                restart_count=restart_counts.get(spec.name, 0),
                last_exit_code=last_exit_codes.get(spec.name),
            )
            results.append(
                {
                    "child": spec.name,
                    "pid": int(started.process.pid),
                    "previous_pid": previous_pids.get(spec.name),
                    "restart_count": started.restart_count,
                }
            )

        self._safe_restart_emit("full supervised restart completed", source=source, results=results)
        return {
            "ok": True,
            "state": "restarted",
            "message": "all supervised service children were restarted, including main-log",
            "source": source,
            "parameters": dict(parameters or {}),
            "results": results,
        }

    def _proxy_application_restart(
        self,
        *,
        target: str,
        source: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        application_targets = {
            "onlyoffice",
            "gitea",
            "coolify",
            "postgres",
            "redis",
            "soketi",
            "coolify-postgres",
            "coolify-redis",
            "coolify-soketi",
            "app-servers",
            "applications-servers",
        }
        if target not in application_targets:
            return {"ok": False, "state": "not-application-target", "target": target}

        queued = enqueue_applications_action(
            self.root,
            action="restart",
            target=target,
            source=f"supervisor:{source}",
            parameters=parameters or {},
        )
        if "applications" not in self._children:
            self._restart_child("applications", source=source, parameters={"reason": "application restart requested"})
        return {
            "ok": True,
            "state": "proxied",
            "message": f"application-server restart was queued for applications service: {target}",
            "target": target,
            "applications_queue": queued,
        }

    def _start_replacement_supervisor(self) -> None:
        if self._replacement_started:
            return
        self._replacement_started = True
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        stdout_path = self.runtime_dir / f"replacement-supervisor-{stamp}.stdout.log"
        stderr_path = self.runtime_dir / f"replacement-supervisor-{stamp}.stderr.log"
        command = [
            self.python_command,
            "-m",
            "main_computer.app_control",
            "--root",
            str(self.root),
        ]
        control_port = str(os.environ.get("MAIN_COMPUTER_CONTROL_PORT") or "").strip()
        if control_port:
            command.extend(["--port", control_port])
        command.extend(["--python-command", self.python_command])
        command.append("bootstrap")
        if self.process_factory is not None:
            process = self.process_factory(command, self.root, stdout_path, stderr_path)
            self._emit("replacement supervisor started", pid=process.pid, command=command)
            return

        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_handle = stdout_path.open("a", encoding="utf-8")
        stderr_handle = stderr_path.open("a", encoding="utf-8")
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                command,
                cwd=str(self.root),
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                creationflags=creationflags,
            )
        finally:
            stdout_handle.close()
            stderr_handle.close()
        self._emit("replacement supervisor started", pid=process.pid, stdout=str(stdout_path), stderr=str(stderr_path))

    def _executor_heartbeat_restart_reason(self, child: ChildRuntime) -> str | None:
        """Return a restart reason when the executor child is alive but telemetry is stale.

        The executor service owns ``runtime/executor_service/state.json``. After a
        crash/reboot, an old state file can survive while a newly started executor
        claims its PID and then blocks before its first heartbeat write. The
        supervisor must treat that as an unhealthy running child instead of
        waiting forever for ``process.poll()`` to report an exit.
        """

        if child.spec.name != EXECUTOR_CHILD_NAME:
            return None

        elapsed_s = max(0.0, self.time() - float(child.started_monotonic or 0.0))
        if elapsed_s < self.executor_heartbeat_startup_grace_s:
            return None

        pid_path = self.root / EXECUTOR_SERVICE_PID_FILENAME
        pid_entry, pid_error = self._read_json_or_pid_file(pid_path)
        if pid_error:
            return f"executor PID file is {pid_error}"
        if not pid_entry:
            return "executor PID file is missing after startup grace"
        if str(pid_entry.get("service") or "") != EXECUTOR_SERVICE_NAME:
            return "executor PID file is not owned by the executor service"
        pid_root_value = pid_entry.get("root")
        if pid_root_value:
            pid_root = _safe_resolved_path(pid_root_value)
            if pid_root != str(self.root):
                return f"executor PID file root mismatch: {pid_root}"
        claimed_pid = _pid_from_entry(pid_entry)

        state_path = self.root / "runtime" / "executor_service" / "state.json"
        state, state_error = self._read_json_or_pid_file(state_path)
        if state_error:
            return f"executor heartbeat state is {state_error}"
        if not state:
            return "executor heartbeat state is missing after startup grace"

        state_root_value = state.get("root")
        if state_root_value:
            state_root = _safe_resolved_path(state_root_value)
            if state_root != str(self.root):
                return f"executor heartbeat state root mismatch: {state_root}"

        service = state.get("service") if isinstance(state.get("service"), dict) else {}
        state_pid = _pid_from_entry(service)
        if claimed_pid is not None and state_pid is not None and state_pid != claimed_pid:
            return f"executor heartbeat belongs to pid {state_pid}, but PID file claims pid {claimed_pid}"

        heartbeat = str(service.get("heartbeat_at") or "").strip()
        if not heartbeat:
            return "executor heartbeat_at is missing"

        age_s = self._heartbeat_age_s(heartbeat)
        if age_s is None:
            return "executor heartbeat_at is invalid"

        policy = state.get("policy") if isinstance(state.get("policy"), dict) else {}
        try:
            stale_after_s = float(policy.get("stale_after_s") or 600.0)
        except (TypeError, ValueError):
            stale_after_s = 600.0

        if age_s > stale_after_s:
            return f"executor heartbeat is stale ({int(age_s)}s old)"

        return None

    def _read_json_or_pid_file(self, path: Path) -> tuple[dict[str, Any], str]:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}, "missing"
        except OSError as exc:
            return {}, f"unreadable: {exc}"

        entry = _pid_entry_from_text(raw)
        if entry.get("parse_error"):
            return {}, f"invalid: {entry['parse_error']}"
        return entry, ""

    @staticmethod
    def _heartbeat_age_s(value: str) -> float | None:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())

    def _start_child(self, spec: ChildSpec, *, restart_count: int = 0, last_exit_code: int | None = None) -> ChildRuntime:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        stdout_path = self.runtime_dir / f"{spec.name}-{stamp}.stdout.log"
        stderr_path = self.runtime_dir / f"{spec.name}-{stamp}.stderr.log"
        command = spec.command(python_command=self.python_command, root=self.root)

        stdout_handle: TextIO | None = None
        stderr_handle: TextIO | None = None
        stream_threads: tuple[threading.Thread, ...] = ()
        if self.process_factory is not None:
            process = self.process_factory(command, self.root, stdout_path, stderr_path)
        else:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                command,
                cwd=str(self.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                creationflags=creationflags,
                env=self._child_env(spec),
            )
            threads: list[threading.Thread] = []
            if process.stdout is not None:
                threads.append(
                    self._start_stream_drain(
                        child=spec.name,
                        stream="stdout",
                        pipe=process.stdout,
                        path=stdout_path,
                        forward_to_main_log=not spec.is_logging,
                    )
                )
            if process.stderr is not None:
                threads.append(
                    self._start_stream_drain(
                        child=spec.name,
                        stream="stderr",
                        pipe=process.stderr,
                        path=stderr_path,
                        forward_to_main_log=not spec.is_logging,
                    )
                )
            stream_threads = tuple(threads)

        runtime = ChildRuntime(
            spec=spec,
            process=process,
            command=command,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            started_at=_now_iso(),
            restart_count=restart_count,
            last_exit_code=last_exit_code,
            stdout_handle=stdout_handle,
            started_monotonic=self.time(),
            stderr_handle=stderr_handle,
            stream_threads=stream_threads,
        )
        self._children[spec.name] = runtime
        self._emit(
            "child service started",
            child=spec.name,
            pid=process.pid,
            restart_count=restart_count,
            stdout=str(stdout_path),
            stderr=str(stderr_path),
        )
        if spec.is_logging:
            self._wait_for_main_log_ready()
        return runtime

    def _stop_child_runtime(self, name: str, child: ChildRuntime, *, reason: str = "") -> None:
        try:
            self._safe_restart_emit(
                "stopping child service",
                child=child.spec.name,
                pid=child.process.pid,
                reason=reason,
            )
            self.process_terminator(int(child.process.pid))
        except Exception as exc:  # pragma: no cover - defensive shutdown path
            self._emit("child stop failed", child=child.spec.name, pid=child.process.pid, error=str(exc))
        finally:
            child.close_logs()
            self._children.pop(name, None)

    def _stop_children(
        self,
        *,
        only_roles: set[str] | None = None,
        exclude_roles: set[str] | None = None,
        reason: str = "",
    ) -> None:
        only_roles = set(only_roles or set())
        exclude_roles = set(exclude_roles or set())
        for name, child in self._stop_ordered_children():
            role = child.spec.role
            if only_roles and role not in only_roles:
                continue
            if exclude_roles and role in exclude_roles:
                continue
            self._stop_child_runtime(name, child, reason=reason)

    def _write_supervisor_state(
        self,
        *,
        state: str,
        pid_claim: dict[str, Any],
        ok: bool | None = None,
    ) -> dict[str, Any]:
        child_states = {name: self._child_state(child) for name, child in sorted(self._children.items(), key=lambda item: self._spec_order_index(item[0]))}
        all_running = bool(child_states) and all(item.get("state") == "running" for item in child_states.values())
        if ok is None:
            ok = all_running
        payload: dict[str, Any] = {
            "schema_version": 1,
            "ok": bool(ok),
            "state": state if ok else ("degraded" if state == "supervising" else state),
            "updated_at": _now_iso(),
            "root": str(self.root),
            "runtime_dir": str(self.runtime_dir),
            "state_path": str(self.state_path),
            "main_log": {
                "url": self.main_log_url,
                "host": self.main_log_host,
                "port": self.main_log_port,
                "child": MAIN_LOG_CHILD_NAME,
            },
            "service": {
                "name": SERVICE_NAME,
                "pid": os.getpid(),
                "pid_file": str(self.pid_path),
                "state": state,
                "poll_interval_s": self.poll_interval_s,
                "pid_claim": pid_claim,
            },
            "children": child_states,
            "control": control_status(self.root),
        }
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._last_state = payload
        return payload

    def _child_state(self, child: ChildRuntime) -> dict[str, Any]:
        exit_code = child.process.poll()
        return {
            "name": child.spec.name,
            "module": child.spec.module,
            "role": child.spec.role,
            "pid": int(child.process.pid),
            "state": "running" if exit_code is None else "exited",
            "returncode": exit_code,
            "last_exit_code": child.last_exit_code,
            "restart_count": child.restart_count,
            "started_at": child.started_at,
            "command": child.command,
            "stdout": str(child.stdout_path),
            "stderr": str(child.stderr_path),
            "main_log_url": self.main_log_url if child.spec.is_logging else None,
        }

    def _prior_state_for_takeover(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _terminate_prior_generation_ordered(self, prior_pid: int, claim: dict[str, Any]) -> None:
        prior_state = self._prior_state_for_takeover()
        children = prior_state.get("children") if isinstance(prior_state.get("children"), dict) else {}
        main_log_info = prior_state.get("main_log") if isinstance(prior_state.get("main_log"), dict) else {}
        old_logger_url = str(main_log_info.get("url") or self.main_log_url).strip() or self.main_log_url
        claim["ordered_takeover_attempted"] = True
        claim["prior_children"] = list(children.keys()) if isinstance(children, dict) else []
        claim["prior_main_log_url"] = old_logger_url
        terminations: list[dict[str, Any]] = []

        self._safe_restart_emit(
            "new supervisor taking over prior generation",
            url=old_logger_url,
            prior_pid=prior_pid,
            prior_children=claim.get("prior_children", []),
        )

        def _child_sort(item: tuple[str, Any]) -> tuple[int, str]:
            name, payload = item
            role = payload.get("role") if isinstance(payload, dict) else ""
            is_logger = name == MAIN_LOG_CHILD_NAME or role == "logging"
            return (1 if is_logger else 0, name)

        for name, payload in sorted(children.items(), key=_child_sort):
            if not isinstance(payload, dict):
                continue
            try:
                child_pid = int(payload.get("pid"))
            except (TypeError, ValueError):
                continue
            if child_pid <= 0:
                continue
            role = str(payload.get("role") or ("logging" if name == MAIN_LOG_CHILD_NAME else "service"))
            if role == "logging" or name == MAIN_LOG_CHILD_NAME:
                self._safe_restart_emit(
                    "stopping prior main-log last during takeover",
                    url=old_logger_url,
                    child=name,
                    pid=child_pid,
                )
            else:
                self._safe_restart_emit(
                    "stopping prior non-log child during takeover",
                    url=old_logger_url,
                    child=name,
                    pid=child_pid,
                )
            try:
                self.process_terminator(child_pid)
                terminations.append({"child": name, "pid": child_pid, "role": role, "state": "terminated"})
            except Exception as exc:
                terminations.append({"child": name, "pid": child_pid, "role": role, "state": "termination-failed", "error": str(exc)})

        # If no child state was available, fall back to the prior supervisor PID.
        # This is less precise, but it still must not block takeover.
        try:
            self.process_terminator(prior_pid)
            supervisor_termination = {"pid": prior_pid, "state": "terminated"}
        except Exception as exc:
            supervisor_termination = {"pid": prior_pid, "state": "termination-failed", "error": str(exc)}

        claim["ordered_terminations"] = terminations
        claim["prior_supervisor_termination"] = supervisor_termination
        failed = [item for item in terminations if item.get("state") != "terminated"]
        if supervisor_termination.get("state") != "terminated":
            failed.append({"child": "prior-supervisor", **supervisor_termination})
        claim["state"] = "terminated" if not failed else "termination-failed"
        if failed:
            claim["termination_error"] = "one or more prior generation processes could not be terminated"

    def _claim_pid_file(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        prior_entry: dict[str, Any] = {}
        prior_pid: int | None = None
        live_command_line: str | None = None
        if self.pid_path.exists():
            try:
                prior_entry = _pid_entry_from_text(self.pid_path.read_text(encoding="utf-8"))
            except OSError as exc:
                prior_entry = {"read_error": str(exc)}
            prior_pid = _pid_from_entry(prior_entry)
            if prior_pid and prior_pid != os.getpid():
                live_command_line = self.process_command_reader(prior_pid)

        claim: dict[str, Any] = {
            "state": "claimed",
            "pid_file": str(self.pid_path),
            "current_pid": os.getpid(),
            "prior_pid": prior_pid,
            "prior_service": prior_entry.get("service"),
        }

        if prior_pid and prior_pid != os.getpid():
            recorded_root = _safe_resolved_path(prior_entry.get("root"))
            current_root = _safe_resolved_path(self.root)
            root_matches = bool(recorded_root and recorded_root == current_root)
            service_matches = prior_entry.get("service") == SERVICE_NAME
            exact_command_matches = _pid_file_command_matches_live(prior_entry, live_command_line)
            live_looks_like_supervisor = _looks_like_service_supervisor_command(live_command_line)
            command_matches = bool(exact_command_matches or (service_matches and root_matches and live_looks_like_supervisor))
            should_terminate = bool(root_matches and service_matches and command_matches and live_looks_like_supervisor)
            claim.update(
                {
                    "prior_root": recorded_root,
                    "current_root": current_root,
                    "prior_command_line": prior_entry.get("command_line"),
                    "live_command_line": live_command_line,
                    "prior_root_matches": root_matches,
                    "prior_service_matches": service_matches,
                    "prior_command_matches_pid_file": command_matches,
                    "prior_exact_command_matches_pid_file": exact_command_matches,
                    "live_command_looks_like_service": live_looks_like_supervisor,
                    "termination_attempted": should_terminate,
                }
            )
            if should_terminate:
                self._terminate_prior_generation_ordered(prior_pid, claim)
            else:
                claim["state"] = "not-terminated"

        record = {
            "schema_version": 1,
            "service": SERVICE_NAME,
            "pid": os.getpid(),
            "root": str(self.root),
            "command_line": " ".join(shlex.quote(part) for part in [sys.executable, *sys.argv]),
            "claimed_at": _now_iso(),
            "prior_claim": claim,
        }
        self.pid_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return claim

    def _release_pid_file_if_current(self) -> None:
        try:
            if not self.pid_path.exists():
                return
            entry = _pid_entry_from_text(self.pid_path.read_text(encoding="utf-8"))
            if _pid_from_entry(entry) == os.getpid() and entry.get("service") == SERVICE_NAME:
                self.pid_path.unlink()
        except OSError:
            return

    def _emit(self, message: str, **fields: Any) -> None:
        payload = {"at": _now_iso(), "service": SERVICE_NAME, "message": message, **fields}
        line = f"{SERVICE_NAME}: {message}"
        if fields:
            details = " ".join(f"{key}={value!r}" for key, value in sorted(fields.items()))
            line = f"{line} {details}"
        try:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        except OSError:
            pass
        if message != "child service started" or fields.get("child") != MAIN_LOG_CHILD_NAME:
            emit_main_log_event(
                {
                    "service": SERVICE_NAME,
                    "source_service": SERVICE_NAME,
                    "kind": "supervisor-event",
                    "stream": "event",
                    "message": message,
                    **fields,
                },
                url=self.main_log_url,
                timeout_s=MAIN_LOG_STREAM_EMIT_TIMEOUT_S,
                fallback_on_error=False,
            )
        if self.output is not None:
            self.output(line)


def load_service_supervisor_state(root: Path | str) -> dict[str, Any]:
    state_path = Path(root).resolve() / "runtime" / "service_supervisor" / "state.json"
    if not state_path.exists():
        return {
            "ok": False,
            "state": "missing",
            "message": "service supervisor state file does not exist",
            "state_path": str(state_path),
        }
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "state": "corrupt",
            "message": f"service supervisor state file is not valid JSON: {exc}",
            "state_path": str(state_path),
        }


def _load_state_file(path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "ok": False,
            "state": "missing",
            "message": f"{label} state file does not exist yet",
            "state_path": str(path),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "state": "corrupt",
            "message": f"{label} state file is not valid JSON: {exc}",
            "state_path": str(path),
        }
    except OSError as exc:
        return {
            "ok": False,
            "state": "unreadable",
            "message": f"{label} state file could not be read: {exc}",
            "state_path": str(path),
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "state": "invalid",
            "message": f"{label} state file did not contain an object",
            "state_path": str(path),
        }
    payload.setdefault("state_path", str(path))
    return payload


def _short(value: Any, *, limit: int = 140) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _state_name(payload: dict[str, Any]) -> str:
    return str(payload.get("state") or ("ready" if payload.get("ok") else "unknown"))


def _component_state(payload: dict[str, Any], *names: str) -> str:
    components = payload.get("components")
    if isinstance(components, dict):
        for name in names:
            component = components.get(name)
            if isinstance(component, dict) and component.get("state"):
                return str(component.get("state"))
    for name in names:
        component = payload.get(name)
        if isinstance(component, dict) and component.get("state"):
            return str(component.get("state"))
    return "unknown"


def _service_line(label: str, payload: dict[str, Any]) -> str:
    service = payload.get("service") if isinstance(payload.get("service"), dict) else {}
    pid = service.get("pid") or payload.get("pid") or "?"
    message = _short(payload.get("message"))
    message_suffix = f" message={message}" if message else ""
    return (
        f"{label}: state={_state_name(payload)} ok={bool(payload.get('ok'))} "
        f"boot_proven={bool(payload.get('boot_proven'))} pid={pid}{message_suffix}"
    )


def render_service_supervisor_summary(root: Path | str) -> str:
    root_path = Path(root).resolve()
    supervisor = load_service_supervisor_state(root_path)
    executor = _load_state_file(root_path / "runtime" / "executor_service" / "state.json", label="executor")
    applications = _load_state_file(root_path / "runtime" / "applications_service" / "state.json", label="applications")
    blockchain = _load_state_file(root_path / "runtime" / "blockchain_service" / "state.json", label="blockchain")

    lines = [
        f"Main Computer startup status @ {_now_iso()}",
        _service_line("Supervisor", supervisor),
    ]

    children = supervisor.get("children") if isinstance(supervisor.get("children"), dict) else {}
    if children:
        for child_name in (MAIN_LOG_CHILD_NAME, "app", "executor", "applications", "blockchain"):
            child = children.get(child_name)
            if not isinstance(child, dict):
                continue
            pid = child.get("pid") or "?"
            restarts = child.get("restart_count", 0)
            returncode = child.get("returncode")
            stdout = child.get("stdout") or child.get("stdout_log") or ""
            stderr = child.get("stderr") or child.get("stderr_log") or ""
            exit_suffix = "" if returncode in (None, "") else f" returncode={returncode}"
            lines.append(
                f"  child {child_name}: state={child.get('state', 'unknown')} pid={pid} "
                f"restarts={restarts}{exit_suffix}"
            )
            if stdout:
                lines.append(f"    stdout={stdout}")
            if stderr:
                lines.append(f"    stderr={stderr}")
    else:
        lines.append("  children: not reported yet")

    lines.append(_service_line("Executor service", executor))
    lines.append(
        "  executor components: "
        f"wsl={_component_state(executor, 'wsl')} "
        f"docker={_component_state(executor, 'docker')} "
        f"compose={_component_state(executor, 'compose')}"
    )

    lines.append(_service_line("Applications service", applications))
    lines.append(
        "  applications components: "
        f"env={_component_state(applications, 'env', 'environment')} "
        f"docker={_component_state(applications, 'docker')} "
        f"compose={_component_state(applications, 'compose')} "
        f"applications={_component_state(applications, 'applications')}"
    )

    lines.append(_service_line("Blockchain service", blockchain))
    lines.append(
        "  blockchain components: "
        f"config={_component_state(blockchain, 'config')} "
        f"runtime={_component_state(blockchain, 'runtime')} "
        f"docker={_component_state(blockchain, 'docker')} "
        f"compose={_component_state(blockchain, 'compose')} "
        f"rpc={_component_state(blockchain, 'rpc')}"
    )

    lines.extend(
        [
            "Useful files:",
            f"  supervisor={root_path / 'runtime' / 'service_supervisor' / 'state.json'}",
            f"  app_pid={root_path / '.main_computer_viewport.pid'}",
            f"  heartbeat_pid={root_path / '.main_computer_heartbeat.pid'}",
            f"  executor={root_path / 'runtime' / 'executor_service' / 'state.json'}",
            f"  applications={root_path / 'runtime' / 'applications_service' / 'state.json'}",
            f"  blockchain={root_path / 'runtime' / 'blockchain_service' / 'state.json'}",
        ]
    )
    return "\n".join(lines)


def print_service_supervisor_summary(
    root: Path | str,
    *,
    wait_s: float = 0.0,
    interval_s: float = 2.0,
    output_func: OutputFunc | None = print,
    sleep_func: SleepFunc = time.sleep,
    time_func: TimeFunc = time.monotonic,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    deadline = time_func() + max(0.0, float(wait_s))
    interval = max(0.5, float(interval_s))
    last_rendered = ""
    state: dict[str, Any] = load_service_supervisor_state(root_path)

    while True:
        rendered = render_service_supervisor_summary(root_path)
        state = load_service_supervisor_state(root_path)
        if output_func is not None and rendered != last_rendered:
            if last_rendered:
                output_func("")
            output_func(rendered)
            last_rendered = rendered

        if wait_s <= 0:
            break
        children = state.get("children") if isinstance(state.get("children"), dict) else {}
        has_children = (
            bool(children.get("app"))
            and bool(children.get("executor"))
            and bool(children.get("applications"))
            and bool(children.get("blockchain"))
        )
        if state.get("ok") and has_children:
            break
        now = time_func()
        if now >= deadline:
            break
        sleep_func(min(interval, max(0.0, deadline - now)))

    return state


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervise resident Main Computer app/executor/application/blockchain services.")
    parser.add_argument("--root", default=".", help="Repository/build root. Defaults to the current directory.")
    parser.add_argument("--python-command", default=os.environ.get("MAIN_COMPUTER_PYTHON_COMMAND", sys.executable))
    parser.add_argument(
        "--poll-interval-s",
        type=float,
        default=float(os.environ.get("MAIN_COMPUTER_SERVICE_SUPERVISOR_POLL_S", DEFAULT_POLL_INTERVAL_S)),
    )

    subparsers = parser.add_subparsers(dest="command")

    supervise = subparsers.add_parser("supervise", help="Start and supervise app/executor/applications/blockchain services.")
    supervise.add_argument("--max-loops", type=int, default=None, help=argparse.SUPPRESS)

    status = subparsers.add_parser("status", help="Print service-supervisor status.")
    status.add_argument("--json", action="store_true", help="Print raw JSON state.")
    status.add_argument("--summary", action="store_true", help="Print a readable startup/status summary.")
    status.add_argument("--wait-s", type=float, default=0.0, help="Poll for this many seconds while waiting for startup state.")
    status.add_argument("--interval-s", type=float, default=2.0, help="Polling interval used with --wait-s.")

    return parser


def _supervisor_from_args(args: argparse.Namespace) -> ServiceSupervisor:
    return ServiceSupervisor(
        root=args.root,
        python_command=args.python_command,
        poll_interval_s=args.poll_interval_s,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "supervise"

    if command == "status":
        if getattr(args, "summary", False):
            print_service_supervisor_summary(args.root, wait_s=args.wait_s, interval_s=args.interval_s)
        else:
            print(json.dumps(load_service_supervisor_state(args.root), indent=2, sort_keys=True))
        return 0

    supervisor = _supervisor_from_args(args)
    state = supervisor.supervise(max_loops=getattr(args, "max_loops", None))
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0 if state.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
