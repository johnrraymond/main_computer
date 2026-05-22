from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from main_computer.heartbeat import HEARTBEAT_PID_FILENAME, HeartbeatConfig, ensure_heartbeat_service, status_payload

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None

SERVER_ACTIONS = {"server_status", "server_shutdown", "server_start", "server_restart"}
PROCESS_ACTIONS = {"terminate_pid", "kill_pid"}
POWERSHELL_JSON_TIMEOUT_SECONDS = 1.5


def _env_int(name: str) -> int | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class TaskManagerService:
    """Local operations control deck for Main Computer.

    The service is intentionally conservative. It surfaces process state,
    connection state, server lifecycle controls, schedule planning, and
    AI-ready operational briefs without pretending to be a full OS supervisor.
    """

    def __init__(self, repo_root: Path, *, default_port: int = 8765, control_root: Path | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.default_port = default_port
        control_root_env = os.environ.get("MAIN_COMPUTER_CONTROL_ROOT", "").strip()
        self.control_root = (Path(control_root_env) if control_root_env else (control_root or self.repo_root)).expanduser().resolve()
        self.data_root = self.repo_root / "tools" / "task_manager"
        self.schedule_path = self.data_root / "schedules.json"
        self.control_script = self.repo_root / "control-main-computer.ps1"
        self.pid_file = self.control_root / ".main_computer_viewport.pid"
        self.heartbeat_pid_file = self.control_root / HEARTBEAT_PID_FILENAME
        env_port = _env_int("MAIN_COMPUTER_CONTROL_PORT")
        detected_port = self._detect_port() if int(default_port) == 8765 else None
        self.port = env_port or detected_port or default_port
        self.heartbeat_port = _env_int("MAIN_COMPUTER_HEARTBEAT_PORT") or self.port + 1

    def capabilities(self) -> dict[str, Any]:
        return {
            "process_listing": psutil is not None,
            "connection_watch": psutil is not None,
            "hardware_watch": psutil is not None,
            "gpu_watch": True,
            "server_control": True,
            "schedule_planning": True,
            "ai_brief": True,
        }

    def snapshot(
        self,
        *,
        query: str = "",
        limit: int = 24,
        include_all: bool = False,
        include_connections: bool = True,
    ) -> dict[str, Any]:
        limit = max(5, min(100, int(limit)))
        processes = self._process_rows(query=query, limit=limit, include_all=include_all)
        relevant_pids = {row["pid"] for row in processes if row.get("is_main_computer")}
        connections = self._connection_rows(relevant_pids=relevant_pids, limit=limit, include_all=include_all) if include_connections else []
        server = self._server_summary(processes=processes, connections=connections)
        schedules = self._read_schedules().get("schedules", [])
        hardware = self._hardware_snapshot()
        overview = {
            "main_computer_process_count": sum(1 for row in processes if row.get("is_main_computer")),
            "process_count": len(processes),
            "connection_count": len(connections),
            "schedule_count": len(schedules),
            "cpu_percent": hardware["cpu"].get("overall_percent"),
            "gpu_percent": hardware["gpu"].get("overall_percent"),
            "watch_hint": "watch listeners, established sockets, CPU or GPU pressure, memory-heavy workers, and server lifecycle actions from one deck.",
        }
        return {
            "ok": True,
            "repo_root": str(self.repo_root),
            "platform": sys.platform,
            "current_pid": os.getpid(),
            "overview": overview,
            "server": server,
            "processes": processes,
            "connections": connections,
            "hardware": hardware,
            "schedules": schedules,
            "capabilities": self.capabilities(),
        }

    def perform_action(
        self,
        *,
        action: str,
        pid: int | None = None,
        force: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        action = str(action or "").strip()
        if action not in SERVER_ACTIONS | PROCESS_ACTIONS:
            raise ValueError(f"Unsupported task action: {action}")
        if action == "server_status":
            snapshot = self.snapshot(limit=16, include_connections=True)
            return {
                "ok": True,
                "action": action,
                "message": "Server status refreshed.",
                "server": snapshot["server"],
                "snapshot": snapshot,
            }
        if action in PROCESS_ACTIONS:
            return self._process_action(action=action, pid=pid, confirm=confirm)
        return self._server_action(action=action, confirm=confirm)

    def list_schedules(self) -> dict[str, Any]:
        data = self._read_schedules()
        return {"ok": True, **data}

    def create_schedule(self, *, action: str, run_at: str, note: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        action = str(action or "").strip()
        if action not in SERVER_ACTIONS:
            raise ValueError("Task schedules currently support server lifecycle actions only.")
        run_at_text = str(run_at or "").strip()
        if not run_at_text:
            raise ValueError("run_at is required.")
        try:
            run_at_dt = datetime.fromisoformat(run_at_text)
        except ValueError as exc:
            raise ValueError("run_at must be a valid ISO or datetime-local value.") from exc
        if run_at_dt.tzinfo is None:
            run_at_dt = run_at_dt.replace(tzinfo=timezone.utc)
        payload = payload or {}
        data = self._read_schedules()
        item = {
            "id": uuid.uuid4().hex[:12],
            "action": action,
            "run_at": run_at_dt.isoformat(),
            "note": str(note or ""),
            "payload": payload,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "enabled": True,
        }
        data.setdefault("schedules", []).append(item)
        data["schedules"] = sorted(data["schedules"], key=lambda entry: entry.get("run_at", ""))
        self._write_schedules(data)
        return {"ok": True, "message": "Scheduled action recorded.", "schedule": item, "schedules": data["schedules"]}

    def delete_schedule(self, *, schedule_id: str) -> dict[str, Any]:
        schedule_id = str(schedule_id or "").strip()
        if not schedule_id:
            raise ValueError("schedule_id is required.")
        data = self._read_schedules()
        before = len(data.get("schedules", []))
        data["schedules"] = [item for item in data.get("schedules", []) if item.get("id") != schedule_id]
        if len(data["schedules"]) == before:
            raise ValueError(f"Unknown schedule id: {schedule_id}")
        self._write_schedules(data)
        return {"ok": True, "message": "Scheduled action deleted.", "schedules": data["schedules"]}

    def ai_brief(
        self,
        *,
        instruction: str,
        query: str = "",
        limit: int = 24,
        include_all: bool = False,
        include_connections: bool = True,
    ) -> dict[str, Any]:
        snapshot = self.snapshot(query=query, limit=limit, include_all=include_all, include_connections=include_connections)
        focused = {
            "overview": snapshot["overview"],
            "server": snapshot["server"],
            "hardware": snapshot["hardware"],
            "processes": snapshot["processes"][: min(12, len(snapshot["processes"]))],
            "connections": snapshot["connections"][: min(12, len(snapshot["connections"]))],
            "schedules": snapshot["schedules"],
        }
        prompt = (
            "You are assisting with the Main Computer task manager. "
            "Treat this as an operations and safety review. "
            "Focus on what should be watched, killed, restarted, scheduled, or left alone.\n\n"
            f"Operator instruction:\n{instruction}\n\n"
            f"Task manager snapshot:\n{json.dumps(focused, indent=2)}\n"
        )
        return {"ok": True, "prompt": prompt, "snapshot": snapshot}

    def _server_action(self, *, action: str, confirm: bool) -> dict[str, Any]:
        verb = action.replace("server_", "")
        plan = self._server_command(verb)
        server = self._server_summary(
            processes=self._process_rows(query="", limit=16, include_all=True),
            connections=self._connection_rows(relevant_pids=set(), limit=16, include_all=True),
        )
        if not confirm:
            return {"ok": True, "planned": True, "action": action, "message": f"Planned {verb} for the Main Computer server.", "command": plan, "server": server}

        if verb in {"shutdown", "restart"} and server.get("is_current_process"):
            self._launch_detached(self._deferred_server_command(verb))
            return {
                "ok": True,
                "deferred": True,
                "action": action,
                "message": f"Scheduled {verb} for the running Main Computer server.",
                "command": plan,
                "server": server,
            }

        if verb == "start" and not self.control_script.exists() and sys.platform != "win32":
            raise ValueError("Server start requires control-main-computer.ps1 or a platform-specific start hook.")

        completed = subprocess.run(plan, cwd=self.repo_root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        return {
            "ok": completed.returncode == 0,
            "action": action,
            "message": completed.stdout.strip() or completed.stderr.strip() or f"Server {verb} finished.",
            "command": plan,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "server": self.snapshot(limit=16, include_connections=True)["server"],
        }

    def _process_action(self, *, action: str, pid: int | None, confirm: bool) -> dict[str, Any]:
        if pid is None or pid <= 0:
            raise ValueError("A positive pid is required.")
        if not confirm:
            return {"ok": True, "planned": True, "action": action, "message": f"Planned {action} for pid {pid}.", "pid": pid}
        if action == "terminate_pid" and pid == os.getpid():
            self._launch_detached(self._deferred_kill_command(pid=pid, force=False))
            return {"ok": True, "deferred": True, "action": action, "message": f"Scheduled graceful termination for current pid {pid}.", "pid": pid}
        if action == "kill_pid" and pid == os.getpid():
            self._launch_detached(self._deferred_kill_command(pid=pid, force=True))
            return {"ok": True, "deferred": True, "action": action, "message": f"Scheduled force kill for current pid {pid}.", "pid": pid}
        if psutil is None:
            if sys.platform == "win32":
                command = ["powershell", "-NoProfile", "-Command", "taskkill /PID {} /T{}".format(pid, " /F" if action == "kill_pid" else "")]
                completed = subprocess.run(command, cwd=self.repo_root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
                return {
                    "ok": completed.returncode == 0,
                    "action": action,
                    "message": completed.stdout.strip() or completed.stderr.strip() or f"Process {pid} handled via {action}.",
                    "pid": pid,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            raise ValueError("psutil is required for direct pid termination on this platform.")
        proc = psutil.Process(pid)
        if action == "kill_pid":
            proc.kill()
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        return {"ok": True, "action": action, "message": f"Process {pid} handled via {action}.", "pid": pid}

    def _control_script_args(self, verb: str) -> list[str]:
        return [
            verb,
            "--auto-allow",
            "-Port",
            str(self.port),
            "-HeartbeatPort",
            str(self.heartbeat_port),
            "-Workspace",
            str(self.repo_root),
            "-ControlRoot",
            str(self.control_root),
            "-PythonPath",
            sys.executable,
        ]

    def _server_command(self, verb: str) -> list[str]:
        if self.control_script.exists():
            legacy_args = self._control_script_args(verb)
            if sys.platform == "win32":
                return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.control_script), *legacy_args]
            return ["pwsh", "-NoProfile", "-File", str(self.control_script), *legacy_args]
        server = self._server_summary(
            processes=self._process_rows(query="", limit=16, include_all=True),
            connections=self._connection_rows(relevant_pids=set(), limit=16, include_all=True),
        )
        pid = server.get("pid")
        if verb == "status":
            return [sys.executable, "-c", f"print({json.dumps(server)})"]
        if verb == "shutdown" and pid:
            if sys.platform == "win32":
                return ["taskkill", "/PID", str(pid), "/T", "/F"]
            return ["kill", "-TERM", str(pid)]
        raise ValueError("Server lifecycle command requires control-main-computer.ps1 on this platform.")

    def _deferred_server_command(self, verb: str) -> list[str]:
        if self.control_script.exists() and sys.platform == "win32":
            script = json.dumps(str(self.control_script))
            args = " ".join(json.dumps(value) for value in self._control_script_args(verb))
            return [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"Start-Sleep -Seconds 1; & {script} {args}",
            ]
        server = self._server_summary(
            processes=self._process_rows(query="", limit=16, include_all=True),
            connections=self._connection_rows(relevant_pids=set(), limit=16, include_all=True),
        )
        pid = server.get("pid")
        if not pid:
            raise ValueError("Cannot schedule a deferred server action when no server pid is known.")
        return ["sh", "-lc", f"sleep 1; kill -TERM {int(pid)}"]

    def _deferred_kill_command(self, *, pid: int, force: bool) -> list[str]:
        if sys.platform == "win32":
            if force:
                return ["powershell", "-NoProfile", "-Command", f"Start-Sleep -Seconds 1; taskkill /PID {pid} /T /F"]
            return ["powershell", "-NoProfile", "-Command", f"Start-Sleep -Seconds 1; taskkill /PID {pid} /T"]
        return ["sh", "-lc", f"sleep 1; kill -{'KILL' if force else 'TERM'} {pid}"]

    def _launch_detached(self, command: list[str]) -> None:
        kwargs: dict[str, Any] = {
            "cwd": self.repo_root,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            flags = 0
            flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            kwargs["creationflags"] = flags
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)

    def _process_rows(self, *, query: str, limit: int, include_all: bool) -> list[dict[str, Any]]:
        if sys.platform == "win32":
            return self._powershell_process_rows(query=query, limit=limit, include_all=include_all)
        if psutil is None:
            return []
        query_text = str(query or "").strip().lower()
        rows: list[dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "status", "cmdline", "memory_info", "create_time"]):
            try:
                info = proc.info
                pid = int(info.get("pid") or 0)
                name = str(info.get("name") or "")
                cmdline = " ".join(info.get("cmdline") or [])
                is_main = self._is_main_computer_process(name=name, cmdline=cmdline)
                if query_text and query_text not in name.lower() and query_text not in cmdline.lower() and query_text not in str(pid):
                    continue
                if not include_all and not is_main and self.repo_root.name.lower() not in cmdline.lower():
                    continue
                memory_info = info.get("memory_info")
                memory_rss = int(getattr(memory_info, "rss", 0) or 0)
                rows.append(
                    {
                        "pid": pid,
                        "name": name,
                        "status": str(info.get("status") or ""),
                        "cmdline": cmdline,
                        "command_preview": self._command_preview(cmdline),
                        "memory_rss": memory_rss,
                        "memory_human": self._human_bytes(memory_rss),
                        "is_main_computer": is_main,
                        "is_current_process": pid == os.getpid(),
                        "created_at": self._format_epoch(info.get("create_time")),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        rows.sort(key=lambda item: (not item.get("is_main_computer"), -(item.get("memory_rss") or 0), item.get("pid") or 0))
        return rows[:limit]

    def _connection_rows(self, *, relevant_pids: set[int], limit: int, include_all: bool) -> list[dict[str, Any]]:
        if psutil is None:
            if sys.platform == "win32":
                return self._powershell_connection_rows(relevant_pids=relevant_pids, limit=limit, include_all=include_all)
            return []
        rows: list[dict[str, Any]] = []
        try:
            connections = psutil.net_connections(kind="inet")
        except Exception:
            return []
        for conn in connections:
            pid = getattr(conn, "pid", None)
            status = str(getattr(conn, "status", "") or "")
            laddr = getattr(conn, "laddr", None)
            local_port = getattr(laddr, "port", None)
            relevant = include_all or (pid in relevant_pids) or (local_port == self.port)
            if not relevant:
                continue
            if status not in {"LISTEN", "ESTABLISHED", "CLOSE_WAIT", "TIME_WAIT", "SYN_SENT", "SYN_RECV"}:
                continue
            rows.append(
                {
                    "pid": pid,
                    "process_name": self._process_name(pid),
                    "status": status,
                    "local": self._format_address(laddr),
                    "remote": self._format_address(getattr(conn, "raddr", None)),
                }
            )
        rows.sort(key=lambda item: (item.get("status") != "LISTEN", item.get("pid") or 0, item.get("local") or ""))
        return rows[:limit]

    def _hardware_snapshot(self) -> dict[str, Any]:
        cpu = self._cpu_summary()
        gpu = self._gpu_summary()
        return {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "cpu": cpu,
            "gpu": gpu,
            "summary": self._hardware_summary_text(cpu=cpu, gpu=gpu),
        }

    def _cpu_summary(self) -> dict[str, Any]:
        if psutil is None:
            return {
                "available": False,
                "message": "CPU telemetry requires psutil.",
                "overall_percent": None,
                "logical_cores": None,
                "physical_cores": None,
                "frequency_mhz": None,
                "max_frequency_mhz": None,
                "load_average": [],
                "per_core": [],
            }
        try:
            per_core_raw = psutil.cpu_percent(interval=0.05, percpu=True)
        except Exception:
            per_core_raw = []
        per_core: list[dict[str, Any]] = []
        utilization_values: list[float] = []
        for index, value in enumerate(per_core_raw if isinstance(per_core_raw, list) else []):
            try:
                percent = max(0.0, min(100.0, float(value)))
            except Exception:
                percent = 0.0
            percent = round(percent, 1)
            utilization_values.append(percent)
            per_core.append(
                {
                    "index": index,
                    "label": f"CPU {index}",
                    "percent": percent,
                }
            )
        overall_percent = round(sum(utilization_values) / len(utilization_values), 1) if utilization_values else None
        logical_cores = None
        physical_cores = None
        try:
            logical_cores = psutil.cpu_count(logical=True)
        except Exception:
            logical_cores = None
        try:
            physical_cores = psutil.cpu_count(logical=False)
        except Exception:
            physical_cores = None
        frequency_mhz = None
        max_frequency_mhz = None
        if hasattr(psutil, "cpu_freq"):
            try:
                frequency = psutil.cpu_freq()
            except Exception:
                frequency = None
            if frequency is not None:
                try:
                    frequency_mhz = round(float(getattr(frequency, "current", 0.0) or 0.0), 1)
                except Exception:
                    frequency_mhz = None
                try:
                    max_frequency_mhz = round(float(getattr(frequency, "max", 0.0) or 0.0), 1)
                except Exception:
                    max_frequency_mhz = None
        load_average: list[float] = []
        if hasattr(os, "getloadavg"):
            try:
                load_average = [round(float(value), 2) for value in os.getloadavg()]
            except Exception:
                load_average = []
        return {
            "available": True,
            "message": "CPU telemetry current.",
            "overall_percent": overall_percent,
            "logical_cores": logical_cores or len(per_core) or None,
            "physical_cores": physical_cores,
            "frequency_mhz": frequency_mhz,
            "max_frequency_mhz": max_frequency_mhz,
            "load_average": load_average,
            "per_core": per_core,
        }

    def _gpu_summary(self) -> dict[str, Any]:
        nvidia_summary = self._nvidia_smi_gpu_summary()
        if nvidia_summary is not None:
            return nvidia_summary
        return {
            "available": False,
            "message": "GPU telemetry unavailable on this host.",
            "overall_percent": None,
            "devices": [],
        }

    def _nvidia_smi_gpu_summary(self) -> dict[str, Any] | None:
        binary = shutil.which("nvidia-smi") or "nvidia-smi"
        command = [
            binary,
            "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        devices: list[dict[str, Any]] = []
        utilization_values: list[float] = []
        for index, line in enumerate(lines):
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 5:
                continue
            name = parts[0]
            utilization_percent = self._parse_float(parts[1])
            memory_used_mb = self._parse_float(parts[2])
            memory_total_mb = self._parse_float(parts[3])
            temperature_c = self._parse_float(parts[4])
            if utilization_percent is not None:
                utilization_values.append(utilization_percent)
            devices.append(
                {
                    "index": index,
                    "name": name,
                    "utilization_percent": utilization_percent,
                    "memory_used_mb": memory_used_mb,
                    "memory_total_mb": memory_total_mb,
                    "temperature_c": temperature_c,
                }
            )
        if not devices:
            return {
                "available": False,
                "message": "No GPU rows were returned by nvidia-smi.",
                "overall_percent": None,
                "devices": [],
            }
        overall_percent = round(sum(utilization_values) / len(utilization_values), 1) if utilization_values else None
        return {
            "available": True,
            "message": "GPU telemetry current via nvidia-smi.",
            "overall_percent": overall_percent,
            "devices": devices,
        }

    def _hardware_summary_text(self, *, cpu: dict[str, Any], gpu: dict[str, Any]) -> str:
        cpu_percent = cpu.get("overall_percent")
        cpu_text = f"CPU {cpu_percent:.1f}%" if isinstance(cpu_percent, (int, float)) else "CPU unavailable"
        gpu_percent = gpu.get("overall_percent")
        if isinstance(gpu_percent, (int, float)):
            gpu_text = f"GPU {gpu_percent:.1f}%"
        elif gpu.get("available"):
            gpu_text = "GPU ready"
        else:
            gpu_text = "GPU unavailable"
        return f"{cpu_text} | {gpu_text}"

    def _parse_float(self, value: Any) -> float | None:
        try:
            return round(float(str(value).strip()), 1)
        except Exception:
            return None

    def _powershell_process_rows(self, *, query: str, limit: int, include_all: bool) -> list[dict[str, Any]]:
        data = self._run_powershell_json(
            "$ErrorActionPreference = 'SilentlyContinue'; $items = Get-CimInstance Win32_Process | Select-Object ProcessId, Name, CommandLine; $items | ConvertTo-Json -Depth 3 -Compress"
        )
        rows: list[dict[str, Any]] = []
        query_text = str(query or "").strip().lower()
        items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        for item in items:
            pid = int(item.get("ProcessId") or 0)
            name = str(item.get("Name") or "")
            cmdline = str(item.get("CommandLine") or "")
            is_main = self._is_main_computer_process(name=name, cmdline=cmdline)
            if query_text and query_text not in name.lower() and query_text not in cmdline.lower() and query_text not in str(pid):
                continue
            if not include_all and not is_main and self.repo_root.name.lower() not in cmdline.lower():
                continue
            rows.append(
                {
                    "pid": pid,
                    "name": name,
                    "status": "",
                    "cmdline": cmdline,
                    "command_preview": self._command_preview(cmdline),
                    "memory_rss": 0,
                    "memory_human": "n/a",
                    "is_main_computer": is_main,
                    "is_current_process": pid == os.getpid(),
                    "created_at": "",
                }
            )
        rows.sort(key=lambda item: (not item.get("is_main_computer"), item.get("pid") or 0))
        return rows[:limit]

    def _powershell_connection_rows(self, *, relevant_pids: set[int], limit: int, include_all: bool) -> list[dict[str, Any]]:
        data = self._run_powershell_json(
            "$ErrorActionPreference = 'SilentlyContinue'; $items = Get-NetTCPConnection | Select-Object LocalAddress, LocalPort, RemoteAddress, RemotePort, State, OwningProcess; $items | ConvertTo-Json -Depth 3 -Compress"
        )
        rows: list[dict[str, Any]] = []
        items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        for item in items:
            pid = item.get("OwningProcess")
            try:
                pid_int = int(pid) if pid not in {None, ""} else None
            except Exception:
                pid_int = None
            local_port = item.get("LocalPort")
            relevant = include_all or (pid_int in relevant_pids) or (str(local_port) == str(self.port))
            if not relevant:
                continue
            rows.append(
                {
                    "pid": pid_int,
                    "process_name": self._process_name(pid_int),
                    "status": str(item.get("State") or ""),
                    "local": f"{item.get('LocalAddress')}:{item.get('LocalPort')}",
                    "remote": f"{item.get('RemoteAddress')}:{item.get('RemotePort')}",
                }
            )
        rows.sort(key=lambda item: (item.get("status") != "Listen", item.get("pid") or 0, item.get("local") or ""))
        return rows[:limit]

    def _run_powershell_json(self, script: str) -> Any:
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=POWERSHELL_JSON_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if completed.returncode != 0:
            return []
        payload = completed.stdout.strip()
        if not payload:
            return []
        try:
            return json.loads(payload)
        except Exception:
            return []

    def _server_summary(self, *, processes: list[dict[str, Any]], connections: list[dict[str, Any]]) -> dict[str, Any]:
        server_pid = self._read_pid_file()
        if not server_pid:
            for item in processes:
                if "main_computer.cli viewport" in (item.get("cmdline") or ""):
                    server_pid = item.get("pid")
                    break
        listener = next((item for item in connections if item.get("status") in {"LISTEN", "Listen"} and str(item.get("local", "")).endswith(f":{self.port}")), None)
        heartbeat_config = HeartbeatConfig(
            workspace=self.repo_root,
            bind_host="127.0.0.1",
            server_port=self.port,
            heartbeat_port=self.heartbeat_port,
            python_executable=sys.executable,
            verbose=False,
            control_root=self.control_root,
        )
        status = status_payload(heartbeat_config)
        heartbeat = status["heartbeat"]
        heartbeat_missing = not bool(heartbeat.get("running") or heartbeat.get("ready"))
        if (server_pid or listener) and heartbeat_missing:
            # The task manager runs inside a live viewport. If the sidecar
            # heartbeat was killed by a start/status path or stale pid cleanup,
            # recreate it so browser controls can recover the viewport later.
            heartbeat = ensure_heartbeat_service(heartbeat_config)["heartbeat"]
        return {
            "running": bool(server_pid),
            "pid": server_pid,
            "port": self.port,
            "listener": listener.get("local") if listener else "",
            "control_script": str(self.control_script) if self.control_script.exists() else "",
            "pid_file": str(self.pid_file),
            "control_root": str(self.control_root),
            "is_current_process": server_pid == os.getpid(),
            "available_actions": sorted(SERVER_ACTIONS),
            "heartbeat_running": bool(heartbeat.get("running") or heartbeat.get("ready")),
            "heartbeat_pid": heartbeat.get("pid"),
            "heartbeat_port": heartbeat.get("port"),
            "heartbeat_url": heartbeat.get("url"),
            "heartbeat_pid_file": heartbeat.get("pid_file"),
            "heartbeat_ready": bool(heartbeat.get("ready")),
            "heartbeat_pid_file_pid": heartbeat.get("pid_file_pid"),
            "heartbeat_control_tracking": heartbeat.get("control_tracking"),
            "heartbeat_evidence": list(heartbeat.get("evidence") or []),
        }

    def _read_schedules(self) -> dict[str, Any]:
        if not self.schedule_path.exists():
            return {"schedules": []}
        try:
            data = json.loads(self.schedule_path.read_text(encoding="utf-8", errors="surrogateescape"))
        except Exception:
            return {"schedules": []}
        if not isinstance(data, dict):
            return {"schedules": []}
        schedules = data.get("schedules")
        if not isinstance(schedules, list):
            schedules = []
        return {"schedules": schedules}

    def _write_schedules(self, data: dict[str, Any]) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.schedule_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _detect_port(self) -> int | None:
        if not self.control_script.exists():
            return None
        try:
            text = self.control_script.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None
        match = re.search(r"\[int\]\$Port\s*=\s*(\d+)", text)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _read_pid_file(self) -> int | None:
        if not self.pid_file.exists():
            return None
        try:
            pid = int(self.pid_file.read_text(encoding="utf-8", errors="ignore").strip())
        except Exception:
            return None
        if psutil is None:
            return pid
        try:
            proc = psutil.Process(pid)
            proc.status()
            return pid
        except Exception:
            return None

    def _is_main_computer_process(self, *, name: str, cmdline: str) -> bool:
        haystack = f"{name} {cmdline}".lower()
        markers = [
            "main_computer",
            "main-computer",
            "control-main-computer",
            str(self.repo_root).lower(),
        ]
        return any(marker in haystack for marker in markers)

    def _process_name(self, pid: int | None) -> str:
        if pid in {None, 0} or psutil is None:
            return ""
        try:
            return str(psutil.Process(int(pid)).name())
        except Exception:
            return ""

    def _format_address(self, value: Any) -> str:
        if not value:
            return ""
        host = getattr(value, "ip", None) or getattr(value, "host", None)
        port = getattr(value, "port", None)
        if host is None and isinstance(value, tuple) and value:
            host = value[0]
            port = value[1] if len(value) > 1 else None
        return f"{host}:{port}" if port is not None else str(host)

    def _format_epoch(self, value: Any) -> str:
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except Exception:
            return ""

    def _command_preview(self, cmdline: str) -> str:
        text = str(cmdline or "").strip()
        if len(text) <= 120:
            return text
        return text[:117] + "..."

    def _human_bytes(self, value: int) -> str:
        size = float(value or 0)
        units = ["B", "KB", "MB", "GB", "TB"]
        idx = 0
        while size >= 1024 and idx < len(units) - 1:
            size /= 1024
            idx += 1
        if idx == 0:
            return f"{int(size)} {units[idx]}"
        return f"{size:.1f} {units[idx]}"
