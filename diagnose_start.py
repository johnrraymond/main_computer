#!/usr/bin/env python3
"""Diagnose Main Computer startup without hiding the first real failure.

This script is intentionally safe by default: it does not start or stop the app,
Docker, or supervised services unless you explicitly pass --run-app.

Typical use from the repository root:

    python diagnose_start.py

Optional foreground app probe on an alternate port:

    python diagnose_start.py --run-app --run-port 8766
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
import traceback
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_HOST = "127.0.0.1"
DEFAULT_CONTROL_PORT = 8765
DEFAULT_MAIN_LOG_PORT = 8767
ERROR_PATTERNS = (
    "Traceback",
    "ImportError",
    "ModuleNotFoundError",
    "SyntaxError",
    "NameError",
    "AttributeError",
    "PermissionError",
    "Address already in use",
    "WinError",
    "Fatal Python error",
)


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _json_default(value: object) -> str:
    try:
        return str(value)
    except Exception:
        return repr(value)


def _coerce_port(value: object, default: int) -> int:
    try:
        port = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return port if 1 <= port <= 65535 else default


def _shorten(text: str, limit: int = 4000) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[-limit:]


def _tail_lines(text: str, lines: int) -> str:
    split = str(text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if lines <= 0:
        return ""
    return "\n".join(split[-lines:])


def _read_text(path: Path, limit_bytes: int = 256 * 1024) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"<could not read {path}: {exc}>"
    if len(data) > limit_bytes:
        data = data[-limit_bytes:]
    return data.decode("utf-8", errors="replace")


def _command_text(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([str(part) for part in command])
    return " ".join(shlex.quote(str(part)) for part in command)


class DiagnosticReport:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []
        self.started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()

    def add(self, severity: str, check: str, message: str, **details: Any) -> None:
        severity = severity.upper()
        payload = {
            "severity": severity,
            "check": check,
            "message": message,
            "details": details,
        }
        self.results.append(payload)
        print(f"[{severity}] {check}: {message}")
        for key, value in details.items():
            if value in (None, "", [], {}):
                continue
            if isinstance(value, (dict, list, tuple)):
                rendered = json.dumps(value, indent=2, sort_keys=True, default=_json_default)
            else:
                rendered = str(value)
            rendered = _shorten(rendered, 5000)
            for line in rendered.splitlines():
                print(f"       {key}: {line}")

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.results:
            sev = str(item.get("severity") or "INFO")
            out[sev] = out.get(sev, 0) + 1
        return out

    def failed(self) -> bool:
        return any(item.get("severity") == "FAIL" for item in self.results)

    def to_payload(self, *, root: Path, python_command: str, args: argparse.Namespace) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "started_at": self.started_at,
            "finished_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "root": str(root),
            "python": python_command,
            "argv": vars(args),
            "counts": self.counts(),
            "results": self.results,
        }


def resolve_root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return Path(__file__).resolve().parent


def resolve_python(root: Path, explicit: str | None = None) -> str:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env_python = os.environ.get("MAIN_COMPUTER_PYTHON_COMMAND")
    if env_python:
        candidates.append(env_python)
    candidates.extend(
        [
            str(root / ".venv" / "Scripts" / "python.exe"),
            str(root.parent / ".venv" / "Scripts" / "python.exe"),
            str(root / ".venv" / "bin" / "python"),
            str(root.parent / ".venv" / "bin" / "python"),
            sys.executable,
            "python",
        ]
    )
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        if text in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
            return text
        if Path(text).exists():
            return str(Path(text).resolve())
    return sys.executable or "python"


def base_env(root: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("MAIN_COMPUTER_ROOT", str(root))
    env.setdefault("MAIN_COMPUTER_CONTROL_ROOT", str(root))
    env.setdefault("MAIN_COMPUTER_CONTROL_PORT", str(port))
    env.setdefault("MAIN_COMPUTER_MAIN_LOG_HOOKS", "0")
    return env


def run_command(
    command: list[str],
    *,
    root: Path,
    env: dict[str, str],
    timeout_s: float,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "timeout": False,
            "returncode": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
            "elapsed_s": round(time.monotonic() - started, 3),
            "command": _command_text(command),
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        return {
            "ok": False,
            "timeout": True,
            "returncode": None,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_s": round(time.monotonic() - started, 3),
            "command": _command_text(command),
        }
    except OSError as exc:
        return {
            "ok": False,
            "timeout": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.monotonic() - started, 3),
            "command": _command_text(command),
        }


def check_paths(report: DiagnosticReport, root: Path, python_command: str) -> None:
    required = [
        root,
        root / "start.bat",
        root / "start_v2.bat",
        root / "scripts" / "main-computer-start-stop.ps1",
        root / "main_computer",
        root / "main_computer" / "app_control.py",
        root / "main_computer" / "service_supervisor.py",
    ]
    for path in required:
        if path.exists():
            report.add("PASS", "path exists", str(path))
        else:
            report.add("FAIL", "missing path", str(path))

    if python_command in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        report.add("WARN", "python resolver", "using generic Python command", python=python_command)
    elif Path(python_command).exists():
        report.add("PASS", "python resolver", "resolved Python executable", python=python_command)
    else:
        report.add("FAIL", "python resolver", "resolved Python executable does not exist", python=python_command)


def check_python_version(report: DiagnosticReport, root: Path, python_command: str, env: dict[str, str], timeout_s: float) -> None:
    command = [
        python_command,
        "-c",
        "import sys, json; print(json.dumps({'executable': sys.executable, 'version': sys.version, 'prefix': sys.prefix}))",
    ]
    result = run_command(command, root=root, env=env, timeout_s=timeout_s)
    if result["ok"]:
        report.add("PASS", "python version", "Python is runnable", output=_tail_lines(result["stdout"], 20))
    else:
        report.add(
            "FAIL",
            "python version",
            "Python command could not run",
            command=result["command"],
            timeout=result.get("timeout"),
            returncode=result.get("returncode"),
            stderr=_tail_lines(result["stderr"], 80),
            stdout=_tail_lines(result["stdout"], 80),
        )


def import_modules(report: DiagnosticReport, root: Path, python_command: str, env: dict[str, str], timeout_s: float) -> None:
    modules = [
        "main_computer.main_log_service",
        "main_computer.service_supervisor",
        "main_computer.app_control",
        "main_computer.executor_service",
        "main_computer.applications_service",
        "main_computer.blockchain_service",
    ]
    for module in modules:
        code = (
            "import importlib, json; "
            f"m = importlib.import_module({module!r}); "
            "print(json.dumps({'module': m.__name__, 'file': getattr(m, '__file__', None)}))"
        )
        command = [python_command, "-X", "faulthandler", "-c", code]
        result = run_command(command, root=root, env=env, timeout_s=timeout_s)
        if result["ok"]:
            report.add("PASS", f"import {module}", "module imported", output=_tail_lines(result["stdout"], 20))
        else:
            report.add(
                "FAIL",
                f"import {module}",
                "module import failed",
                command=result["command"],
                returncode=result["returncode"],
                timeout=result.get("timeout"),
                stderr=_tail_lines(result["stderr"], 120),
                stdout=_tail_lines(result["stdout"], 40),
            )


def run_status_command(report: DiagnosticReport, root: Path, timeout_s: float) -> None:
    helper = root / "scripts" / "main-computer-start-stop.ps1"
    if not helper.exists():
        report.add("WARN", "start-stop status", "status helper is missing", helper=str(helper))
        return

    powershell = "powershell.exe" if os.name == "nt" else "pwsh"
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(helper),
        "-Action",
        "status",
        "-Root",
        str(root),
        "-StartedBy",
        "diagnose_start.py",
    ]
    result = run_command(command, root=root, env=os.environ.copy(), timeout_s=timeout_s)
    if result["ok"]:
        report.add("PASS", "start-stop status", "status helper completed", stdout=_tail_lines(result["stdout"], 80))
    else:
        severity = "WARN" if "not recognized" in result["stderr"].lower() or "no such file" in result["stderr"].lower() else "FAIL"
        report.add(
            severity,
            "start-stop status",
            "status helper failed",
            command=result["command"],
            returncode=result["returncode"],
            stdout=_tail_lines(result["stdout"], 100),
            stderr=_tail_lines(result["stderr"], 100),
        )


def parse_pid_payload(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            return {"pid": int(text)}
        except ValueError:
            return {"parse_error": "not JSON and not a plain integer", "raw": text[:500]}
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, int):
        return {"pid": payload}
    return {"parse_error": "PID payload is not an object or integer", "raw": text[:500]}


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def read_process_command_line(pid: int, timeout_s: float = 3.0) -> str:
    if pid <= 0:
        return ""
    proc_path = Path("/proc") / str(pid) / "cmdline"
    try:
        if proc_path.exists():
            raw = proc_path.read_bytes()
            return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        pass

    if os.name == "nt":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine",
        ]
    else:
        command = ["ps", "-p", str(pid), "-o", "args="]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or "").strip()


def check_pid_file(report: DiagnosticReport, path: Path, label: str) -> None:
    if not path.exists():
        report.add("WARN", f"pid file {label}", "PID file is missing", path=str(path))
        return
    payload = parse_pid_payload(_read_text(path, 32 * 1024))
    pid = _coerce_port(payload.get("pid"), 0)
    if not pid:
        report.add("WARN", f"pid file {label}", "PID file did not contain a usable PID", path=str(path), payload=payload)
        return
    live = pid_is_running(pid)
    command_line = read_process_command_line(pid) if live else ""
    severity = "PASS" if live else "WARN"
    report.add(severity, f"pid file {label}", "PID points to a running process" if live else "PID does not appear to be running", path=str(path), pid=pid, command_line=command_line)


def check_state_and_logs(report: DiagnosticReport, root: Path, tail: int) -> None:
    supervisor_state = root / "runtime" / "service_supervisor" / "state.json"
    check_pid_file(report, root / ".main_computer_service_supervisor.pid", "supervisor")
    check_pid_file(report, root / ".main_computer_viewport.pid", "viewport")

    if not supervisor_state.exists():
        report.add("WARN", "supervisor state", "supervisor state file is missing", path=str(supervisor_state))
    else:
        try:
            state = json.loads(supervisor_state.read_text(encoding="utf-8"))
        except Exception as exc:
            report.add("FAIL", "supervisor state", "could not parse supervisor state JSON", path=str(supervisor_state), error=str(exc))
            state = None
        if isinstance(state, dict):
            children = state.get("children") if isinstance(state.get("children"), dict) else {}
            report.add(
                "PASS" if state.get("ok") else "WARN",
                "supervisor state",
                f"state={state.get('state')} ok={state.get('ok')}",
                path=str(supervisor_state),
                updated_at=state.get("updated_at"),
                service=state.get("service"),
                child_names=sorted(children.keys()),
            )
            for name, child in sorted(children.items()):
                if not isinstance(child, dict):
                    continue
                child_state = child.get("state")
                severity = "PASS" if child_state == "running" and child.get("pid") else "WARN"
                report.add(
                    severity,
                    f"child state {name}",
                    f"state={child_state} pid={child.get('pid')}",
                    restart_count=child.get("restart_count"),
                    last_exit_code=child.get("last_exit_code"),
                    command=child.get("command"),
                    stdout=child.get("stdout"),
                    stderr=child.get("stderr"),
                )
                for stream_name in ("stderr", "stdout"):
                    value = child.get(stream_name)
                    if not value:
                        continue
                    path = Path(str(value))
                    if path.exists():
                        scan_log_file(report, path, f"{name} {stream_name}", tail)

    supervisor_runtime = root / "runtime" / "service_supervisor"
    if supervisor_runtime.exists():
        logs = sorted(
            [p for p in supervisor_runtime.glob("*.log") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in logs[:12]:
            scan_log_file(report, path, path.name, tail)
    else:
        report.add("WARN", "service supervisor logs", "runtime/service_supervisor directory is missing", path=str(supervisor_runtime))

    for rel in [
        "runtime/main_log/state.json",
        "runtime/executor_service/state.json",
        "runtime/applications_service/state.json",
        "runtime/blockchain_service/state.json",
    ]:
        path = root / rel
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                report.add("INFO", f"state file {rel}", f"state={payload.get('state')} ok={payload.get('ok')}", path=str(path), updated_at=payload.get("updated_at"))
            except Exception as exc:
                report.add("WARN", f"state file {rel}", "could not parse state JSON", path=str(path), error=str(exc))


def scan_log_file(report: DiagnosticReport, path: Path, label: str, tail: int) -> None:
    text = _read_text(path)
    matches = [pattern for pattern in ERROR_PATTERNS if pattern.lower() in text.lower()]
    if matches:
        report.add(
            "WARN",
            f"log scan {label}",
            "log contains startup error pattern(s)",
            path=str(path),
            patterns=matches,
            tail=_tail_lines(text, tail),
        )
    else:
        report.add("INFO", f"log scan {label}", "no common error patterns found in inspected tail", path=str(path), tail=_tail_lines(text, min(tail, 20)))


def tcp_port_open(host: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def http_probe(host: str, port: int, timeout_s: float) -> dict[str, Any]:
    url = f"http://{host}:{port}/"
    try:
        request = Request(url, headers={"User-Agent": "main-computer-diagnose-start/1"})
        with urlopen(request, timeout=timeout_s) as response:
            body = response.read(500).decode("utf-8", errors="replace")
            return {"ok": True, "url": url, "status": getattr(response, "status", None), "body_start": body}
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}


def check_ports(report: DiagnosticReport, host: str, port: int, main_log_port: int, timeout_s: float) -> None:
    for label, candidate in [("app/control", port), ("main-log", main_log_port)]:
        open_ = tcp_port_open(host, candidate, timeout_s)
        report.add("PASS" if open_ else "WARN", f"tcp port {label}", f"{host}:{candidate} is {'open' if open_ else 'not open'}")
        if open_:
            probe = http_probe(host, candidate, timeout_s)
            report.add("PASS" if probe.get("ok") else "WARN", f"http probe {label}", "HTTP probe completed" if probe.get("ok") else "HTTP probe failed", **probe)


def module_path(root: Path, module: str) -> Path | None:
    if not module.startswith("main_computer"):
        return None
    parts = module.split(".")
    package_path = root.joinpath(*parts)
    init_path = package_path / "__init__.py"
    file_path = package_path.with_suffix(".py")
    if file_path.exists():
        return file_path
    if init_path.exists():
        return init_path
    return None


def top_level_names(path: Path) -> tuple[set[str], bool]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return set(), False
    names: set[str] = set()
    has_star_import = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(extract_target_names(target))
        elif isinstance(node, ast.AnnAssign):
            names.update(extract_target_names(node.target))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    has_star_import = True
                    continue
                exported = alias.asname or alias.name.split(".", 1)[0]
                names.add(exported)
    return names, has_star_import


def extract_target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for item in node.elts:
            out.update(extract_target_names(item))
        return out
    return set()


def static_local_import_check(report: DiagnosticReport, root: Path, limit: int = 80) -> None:
    package = root / "main_computer"
    if not package.exists():
        report.add("WARN", "static local imports", "main_computer package directory is missing", path=str(package))
        return

    cache: dict[Path, tuple[set[str], bool]] = {}
    missing: list[dict[str, str]] = []
    unresolved_modules: list[dict[str, str]] = []

    for source in sorted(package.rglob("*.py")):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        except SyntaxError as exc:
            missing.append({"source": str(source.relative_to(root)), "import": "<syntax>", "message": str(exc)})
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                continue
            imported_module = node.module or ""
            if not imported_module.startswith("main_computer."):
                continue

            imported_path = module_path(root, imported_module)
            if imported_path is None:
                unresolved_modules.append({"source": str(source.relative_to(root)), "module": imported_module})
                continue
            if imported_path not in cache:
                cache[imported_path] = top_level_names(imported_path)
            available, has_star_import = cache[imported_path]
            if has_star_import:
                # Compatibility modules sometimes re-export names with import *.
                # Static analysis cannot prove those exports, so avoid noisy false failures.
                continue

            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name not in available:
                    missing.append(
                        {
                            "source": str(source.relative_to(root)),
                            "module": imported_module,
                            "name": alias.name,
                            "target": str(imported_path.relative_to(root)),
                        }
                    )

    if missing:
        report.add(
            "WARN",
            "static local imports",
            "possible local from-import names were not found by static analysis",
            missing=missing[:limit],
            omitted=max(0, len(missing) - limit),
        )
    else:
        report.add("PASS", "static local imports", "no obvious missing local from-import names found")

    if unresolved_modules:
        report.add(
            "WARN",
            "static local import modules",
            "some local import modules could not be resolved to files",
            unresolved=unresolved_modules[:limit],
            omitted=max(0, len(unresolved_modules) - limit),
        )


def run_app_probe(report: DiagnosticReport, root: Path, python_command: str, env: dict[str, str], host: str, port: int, timeout_s: float) -> None:
    command = [
        python_command,
        "-X",
        "faulthandler",
        "-m",
        "main_computer.app_control",
        "--root",
        str(root),
        "--host",
        host,
        "--port",
        str(port),
        "run",
    ]

    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        report.add("FAIL", "foreground app probe", "could not launch app command", command=_command_text(command), error=str(exc))
        return

    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=3)
        report.add(
            "PASS",
            "foreground app probe",
            f"app was still running after {timeout_s}s, so no immediate import/startup crash was observed",
            command=_command_text(command),
            elapsed_s=round(time.monotonic() - started, 3),
            stdout=_tail_lines(stdout, 80),
            stderr=_tail_lines(stderr, 120),
        )
        return

    severity = "PASS" if process.returncode == 0 else "FAIL"
    report.add(
        severity,
        "foreground app probe",
        f"app command exited with code {process.returncode}",
        command=_command_text(command),
        elapsed_s=round(time.monotonic() - started, 3),
        stdout=_tail_lines(stdout, 120),
        stderr=_tail_lines(stderr, 160),
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose Main Computer start.bat/startup failures.")
    parser.add_argument("--root", default=None, help="Repository root. Defaults to the directory containing diagnose_start.py.")
    parser.add_argument("--python", default=None, help="Python executable to use. Defaults to the same resolver used by start_v2.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_coerce_port(os.environ.get("MAIN_COMPUTER_CONTROL_PORT"), DEFAULT_CONTROL_PORT))
    parser.add_argument("--main-log-port", type=int, default=_coerce_port(os.environ.get("MAIN_COMPUTER_MAIN_LOG_PORT"), DEFAULT_MAIN_LOG_PORT))
    parser.add_argument("--timeout-s", type=float, default=10.0, help="Timeout for quick subprocess checks.")
    parser.add_argument("--tail", type=int, default=80, help="Log tail lines to include when reporting errors.")
    parser.add_argument("--json", default=None, help="Optional JSON report path. Defaults under runtime/start_diagnostics.")
    parser.add_argument("--skip-status-helper", action="store_true", help="Skip the PowerShell start-stop status command.")
    parser.add_argument("--skip-static-imports", action="store_true", help="Skip static local import mismatch scan.")
    parser.add_argument("--run-app", action="store_true", help="Also launch app_control run in the foreground for a bounded probe. This may touch viewport/heartbeat PID files.")
    parser.add_argument("--run-port", type=int, default=None, help="Port for --run-app. Use an alternate port such as 8766 to avoid conflicts.")
    parser.add_argument("--run-timeout-s", type=float, default=12.0, help="How long --run-app may run before being treated as non-crashing.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    report = DiagnosticReport()

    try:
        root = resolve_root(args.root)
        python_command = resolve_python(root, args.python)
        port = _coerce_port(args.port, DEFAULT_CONTROL_PORT)
        main_log_port = _coerce_port(args.main_log_port, DEFAULT_MAIN_LOG_PORT)
        env = base_env(root, port)

        print("Main Computer startup diagnosis")
        print(f"root:   {root}")
        print(f"python: {python_command}")
        print(f"port:   {args.host}:{port}")
        print("")

        check_paths(report, root, python_command)
        check_python_version(report, root, python_command, env, args.timeout_s)
        if not args.skip_static_imports:
            static_local_import_check(report, root)
        import_modules(report, root, python_command, env, args.timeout_s)
        check_state_and_logs(report, root, args.tail)
        check_ports(report, args.host, port, main_log_port, min(3.0, max(0.25, args.timeout_s)))
        if not args.skip_status_helper:
            run_status_command(report, root, max(5.0, args.timeout_s))

        if args.run_app:
            run_port = _coerce_port(args.run_port, port) if args.run_port else port
            run_env = dict(env)
            run_env["MAIN_COMPUTER_CONTROL_PORT"] = str(run_port)
            report.add(
                "WARN",
                "foreground app probe",
                "--run-app was requested; this can touch viewport/heartbeat PID files",
                run_port=run_port,
            )
            run_app_probe(report, root, python_command, run_env, args.host, run_port, args.run_timeout_s)

        json_path = Path(args.json).expanduser().resolve() if args.json else root / "runtime" / "start_diagnostics" / f"diagnose_start-{_now_stamp()}.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report.to_payload(root=root, python_command=python_command, args=args), indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")

        counts = report.counts()
        print("")
        print("Summary:", ", ".join(f"{key}={counts[key]}" for key in sorted(counts)))
        print(f"JSON report: {json_path}")

        if report.failed():
            print("")
            print("Startup diagnosis found at least one hard failure. Fix the first FAIL near the top first; later failures may be cascading.")
            return 1

        print("")
        print("No hard startup failures were detected by these checks. Inspect WARN entries and use --run-app --run-port 8766 for a bounded foreground app probe.")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception:
        print("diagnose_start.py crashed while diagnosing startup:", file=sys.stderr)
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
