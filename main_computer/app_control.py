from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Callable
from main_computer.main_log_hooks import install_main_log_hooks_from_env

from main_computer.config import MainComputerConfig
from main_computer.heartbeat import HeartbeatConfig, ensure_heartbeat_service
from main_computer.service_supervisor import (
    ServiceSupervisor,
    _normalized_command_line,
    _read_process_command_line_default,
    _terminate_process_default,
    resolve_python_command,
)
from main_computer.viewport import serve
from main_computer.viewport_state import (
    _clear_viewport_pid_file,
    _control_root_path,
    _viewport_pid_path,
)


ProcessCommandReader = Callable[[int], str | None]
ProcessTerminator = Callable[[int], None]


APP_CONTROL_SERVICE_NAME = "main-computer-app-control"
DEFAULT_CONTROL_PORT = 8765


def _coerce_control_port(value: object, *, fallback: int = DEFAULT_CONTROL_PORT) -> int:
    try:
        port = int(str(value or "").strip())
    except (TypeError, ValueError):
        return fallback
    if 1 <= port <= 65535:
        return port
    return fallback


def _default_control_port() -> int:
    return _coerce_control_port(os.environ.get("MAIN_COMPUTER_CONTROL_PORT"))


def _resolve_control_port(port: int | None) -> int:
    if port is None:
        return _default_control_port()
    return _coerce_control_port(port)


def _looks_like_viewport_command(command_line: str | None) -> bool:
    normalized = _normalized_command_line(command_line)
    if not normalized:
        return False
    if "main_computer.app_control" in normalized and " run" in f" {normalized} ":
        return True
    if "main_computer.cli" in normalized and " viewport" in f" {normalized} ":
        return True
    if "main_computer.viewport" in normalized:
        return True
    return False


def _pid_from_text(raw: str) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            pid = int(text)
        except ValueError:
            return None
        return pid if pid > 0 else None
    if isinstance(payload, dict):
        try:
            pid = int(payload.get("pid"))
        except (TypeError, ValueError):
            return None
        return pid if pid > 0 else None
    if isinstance(payload, int):
        return payload if payload > 0 else None
    return None


def stop_existing_viewport(
    *,
    root: Path | str,
    process_command_reader: ProcessCommandReader | None = None,
    process_terminator: ProcessTerminator | None = None,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    control_root = _control_root_path(root_path)
    pid_file = _viewport_pid_path(control_root)
    reader = process_command_reader or _read_process_command_line_default
    terminator = process_terminator or _terminate_process_default

    if not pid_file.exists():
        return {
            "ok": True,
            "state": "missing",
            "message": "no existing viewport PID file was present",
            "pid_file": str(pid_file),
        }

    try:
        pid = _pid_from_text(pid_file.read_text(encoding="utf-8"))
    except OSError as exc:
        return {
            "ok": False,
            "state": "read-failed",
            "message": "could not read existing viewport PID file",
            "pid_file": str(pid_file),
            "error": str(exc),
        }

    if not pid:
        _clear_viewport_pid_file(pid_file)
        return {
            "ok": True,
            "state": "invalid-cleared",
            "message": "invalid viewport PID file was cleared",
            "pid_file": str(pid_file),
        }

    if pid == os.getpid():
        return {
            "ok": True,
            "state": "current",
            "message": "viewport PID file already belongs to this process",
            "pid": pid,
            "pid_file": str(pid_file),
        }

    live_command = reader(pid)
    if not live_command:
        _clear_viewport_pid_file(pid_file)
        return {
            "ok": True,
            "state": "stale-cleared",
            "message": "stale viewport PID file was cleared",
            "pid": pid,
            "pid_file": str(pid_file),
        }

    if not _looks_like_viewport_command(live_command):
        return {
            "ok": False,
            "state": "not-terminated",
            "message": "existing viewport PID did not look like a Main Computer app server; leaving it alone",
            "pid": pid,
            "pid_file": str(pid_file),
            "live_command_line": live_command,
        }

    try:
        terminator(pid)
    except Exception as exc:
        return {
            "ok": False,
            "state": "terminate-failed",
            "message": "existing viewport process matched but could not be terminated",
            "pid": pid,
            "pid_file": str(pid_file),
            "live_command_line": live_command,
            "error": str(exc),
        }

    time.sleep(0.2)
    _clear_viewport_pid_file(pid_file)
    return {
        "ok": True,
        "state": "terminated",
        "message": "existing Main Computer app server was terminated",
        "pid": pid,
        "pid_file": str(pid_file),
        "live_command_line": live_command,
    }


def replace_heartbeat(*, root: Path | str, host: str, port: int, verbose: bool = True) -> dict[str, Any]:
    root_path = Path(root).resolve()
    heartbeat_port = int(os.environ.get("MAIN_COMPUTER_HEARTBEAT_PORT") or port + 1)
    control_root = _control_root_path(root_path)
    return ensure_heartbeat_service(
        HeartbeatConfig(
            workspace=root_path,
            bind_host=host,
            server_port=port,
            heartbeat_port=heartbeat_port,
            verbose=verbose,
            control_root=control_root,
        ),
        replace_existing=True,
    )


def run_app(
    *,
    root: Path | str,
    host: str = "127.0.0.1",
    port: int | None = None,
    verbose: bool = True,
    process_command_reader: ProcessCommandReader | None = None,
    process_terminator: ProcessTerminator | None = None,
) -> int:
    port = _resolve_control_port(port)
    root_path = Path(root).resolve()
    install_main_log_hooks_from_env(default_service_name=APP_CONTROL_SERVICE_NAME, root=root_path)
    os.environ.setdefault("MAIN_COMPUTER_CONTROL_ROOT", str(root_path))
    os.chdir(root_path)

    viewport_takeover = stop_existing_viewport(
        root=root_path,
        process_command_reader=process_command_reader,
        process_terminator=process_terminator,
    )
    heartbeat = replace_heartbeat(root=root_path, host=host, port=port, verbose=verbose)
    print(json.dumps({"app_control": APP_CONTROL_SERVICE_NAME, "viewport_takeover": viewport_takeover, "heartbeat": heartbeat}, indent=2, sort_keys=True))

    config = replace(MainComputerConfig.from_env(), workspace=root_path)
    serve(config, host=host, port=port, verbose=verbose)
    return 0


def bootstrap(
    *,
    root: Path | str,
    python_command: str = "python",
    poll_interval_s: float = 5.0,
    host: str = "127.0.0.1",
    port: int | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    port = _resolve_control_port(port)
    root_path = Path(root).resolve()
    resolved_python_command = resolve_python_command(python_command)
    os.environ["MAIN_COMPUTER_PYTHON_COMMAND"] = resolved_python_command
    os.environ.setdefault("MAIN_COMPUTER_CONTROL_ROOT", str(root_path))
    os.chdir(root_path)

    # Make repeated desktop/application shortcut clicks act as a refresh:
    # clear the currently-running app server and heartbeat before the new
    # supervisor starts its own app child.
    viewport_takeover = stop_existing_viewport(root=root_path)
    heartbeat = replace_heartbeat(root=root_path, host=host, port=port, verbose=verbose)

    supervisor = ServiceSupervisor(
        root=root_path,
        python_command=resolved_python_command,
        poll_interval_s=poll_interval_s,
    )
    state = supervisor.supervise()
    state.setdefault("bootstrap", {})
    state["bootstrap"].update(
        {
            "app_control": APP_CONTROL_SERVICE_NAME,
            "viewport_takeover": viewport_takeover,
            "heartbeat": heartbeat,
        }
    )
    return state


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap or run the Main Computer app/control service.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--python-command", default=os.environ.get("MAIN_COMPUTER_PYTHON_COMMAND", sys.executable))
    parser.add_argument("--poll-interval-s", type=float, default=5.0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("-noverbose", "--noverbose", dest="verbose", action="store_false", default=True)

    subparsers = parser.add_subparsers(dest="command")

    bootstrap_parser = subparsers.add_parser("bootstrap", help="Refresh existing app/heartbeat and run the service supervisor.")
    bootstrap_parser.set_defaults(command="bootstrap")

    run_parser = subparsers.add_parser("run", help="Refresh existing app/heartbeat and run the app server.")
    run_parser.set_defaults(command="run")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    command = args.command or "bootstrap"

    if command == "run":
        return run_app(root=args.root, host=args.host, port=args.port, verbose=bool(args.verbose))

    state = bootstrap(
        root=args.root,
        python_command=args.python_command,
        poll_interval_s=args.poll_interval_s,
        host=args.host,
        port=args.port,
        verbose=bool(args.verbose),
    )
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0 if state.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
