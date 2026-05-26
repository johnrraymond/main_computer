from __future__ import annotations

import os
import socket
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - psutil is optional at runtime
    psutil = None


PID_FILENAMES = (".main_computer_viewport.pid", ".main_computer_heartbeat.pid")
PROCESS_MARKERS = (
    "main_computer",
    "main-computer",
    "control-main-computer",
    "dev-control",
)
SERVICE_MARKERS = {
    "ollama": ("ollama",),
    "gitea": ("gitea",),
    "blockchain": ("anvil", "geth", "hardhat", "ganache"),
    "onlyoffice": ("onlyoffice", "documentserver", "converter", "docservice"),
    "docker": ("docker", "dockerd", "com.docker"),
    "wsl": ("wsl", "wslhost", "wslservice"),
}
NOISY_CONNECTION_STATUSES = {"TIME_WAIT"}


def collect_level1_telemetry(
    repo_root: Path,
    *,
    control_root: Path | None = None,
    known_ports: dict[str, int] | None = None,
    current_pid: int | None = None,
    process_limit: int = 160,
    connection_limit: int = 260,
) -> dict[str, Any]:
    """Collect a fast cross-process telemetry snapshot for Main Computer.

    Level 1 intentionally answers "what is running right now?" instead of
    running the deeper Level 5 sanity checks.  It keeps process environment
    variables out of the payload and focuses on process, port, CPU, memory,
    handle, file, and socket telemetry that can be rendered safely in the
    local control panel.
    """

    repo_root = repo_root.expanduser().resolve()
    control_root = (control_root or _control_root_from_env() or repo_root).expanduser().resolve()
    known_ports = {str(name): int(port) for name, port in (known_ports or {}).items() if _is_int_like(port)}
    started_at = datetime.now(timezone.utc)

    pid_file_rows = _read_pid_files(control_root)
    warnings: list[str] = []
    observations: list[str] = []
    if psutil is None:
        warnings.append("psutil is not installed; process-level telemetry is unavailable.")
        return {
            "ok": False,
            "level": "level-1-telemetry",
            "updated_at": started_at.isoformat(),
            "repo_root": str(repo_root),
            "control_root": str(control_root),
            "current_pid": current_pid or os.getpid(),
            "capabilities": {
                "psutil": False,
                "process_details": False,
                "connections": False,
            },
            "summary": {
                "process_count": 0,
                "connection_count": 0,
                "active_connection_count": 0,
                "known_port_listener_count": 0,
                "known_port_activity_count": 0,
                "known_port_time_wait_count": 0,
                "pid_file_count": len(pid_file_rows),
                "volatile_pid_count": 0,
                "total_rss": 0,
                "total_rss_human": "0 B",
                "warning_count": len(warnings),
                "observation_count": len(observations),
            },
            "known_ports": known_ports,
            "pid_files": pid_file_rows,
            "pid_file_health": [],
            "service_summary": [],
            "port_listeners": [],
            "port_activity": [],
            "processes": [],
            "process_tree": [],
            "notable_processes": {"top_memory": [], "top_cpu": [], "top_io_write": []},
            "connections": [],
            "connection_status_counts": {},
            "warnings": warnings,
            "observations": observations,
        }

    pid_sources: dict[int, set[str]] = {}
    role_sources: dict[int, set[str]] = {}
    current_pid = int(current_pid or os.getpid())
    _add_pid(pid_sources, role_sources, current_pid, "current-process", "viewport-current")

    for row in pid_file_rows:
        pid = row.get("pid")
        if isinstance(pid, int):
            role = "heartbeat" if "heartbeat" in str(row.get("name", "")).lower() else "viewport"
            _add_pid(pid_sources, role_sources, pid, f"pid-file:{row.get('name')}", role)

    port_activity_rows, port_pid_roles = _discover_port_processes(known_ports)
    port_listener_rows = [row for row in port_activity_rows if _is_listen(row)]
    for pid, roles in port_pid_roles.items():
        for role in roles:
            _add_pid(pid_sources, role_sources, pid, f"known-port:{role}", role)

    marker_pids = _discover_marker_processes(repo_root)
    for pid, roles in marker_pids.items():
        for role in roles:
            _add_pid(pid_sources, role_sources, pid, f"marker:{role}", role)

    _expand_with_relatives(pid_sources, role_sources)

    processes: list[dict[str, Any]] = []
    volatile_pids: list[dict[str, Any]] = []
    for pid in sorted(pid_sources):
        try:
            proc = psutil.Process(pid)
            proc.status()
        except Exception:
            volatile_pids.append(
                {
                    "pid": pid,
                    "roles": sorted(role_sources.get(pid, set())),
                    "sources": sorted(pid_sources.get(pid, set())),
                }
            )
            continue
        try:
            processes.append(
                _process_payload(
                    proc,
                    repo_root=repo_root,
                    sources=sorted(pid_sources.get(pid, set())),
                    roles=sorted(role_sources.get(pid, set())),
                    known_ports=known_ports,
                )
            )
        except Exception as exc:
            warnings.append(f"PID {pid} telemetry failed: {exc}")

    if volatile_pids:
        observations.append(f"{len(volatile_pids)} discovered PIDs exited or became unreadable during the snapshot.")

    processes.sort(key=_process_sort_key)
    if len(processes) > process_limit:
        warnings.append(f"Process telemetry truncated from {len(processes)} to {process_limit} rows.")
        processes = processes[:process_limit]

    relevant_pids = {int(row["pid"]) for row in processes if isinstance(row.get("pid"), int)}
    connection_rows, connection_status_counts = _connection_payloads(relevant_pids, known_ports, limit=connection_limit)
    total_rss = sum(int(row.get("memory_rss") or 0) for row in processes)
    port_activity_status_counts = _status_counts(port_activity_rows)
    active_connection_count = _active_status_count(connection_status_counts)
    active_known_port_activity_count = _active_row_count(port_activity_rows)
    service_summary = _service_summary(known_ports, port_listener_rows, port_activity_rows)
    pid_file_health = _pid_file_health(pid_file_rows, processes, volatile_pids)

    for row in pid_file_health:
        if row.get("state") == "stale":
            warnings.append(f"{row.get('name')} points at unreadable PID {row.get('pid')}.")
    for service in service_summary:
        for finding in service.get("findings", []):
            if finding == "multiple-listener-pids":
                observations.append(
                    f"{service.get('service')} has listeners on multiple PIDs: {', '.join(str(pid) for pid in service.get('listener_pids', []))}."
                )
            elif finding == "any-address-listener":
                observations.append(f"{service.get('service')} has at least one listener bound to an all-interfaces address.")

    return {
        "ok": True,
        "level": "level-1-telemetry",
        "updated_at": started_at.isoformat(),
        "repo_root": str(repo_root),
        "control_root": str(control_root),
        "current_pid": current_pid,
        "capabilities": {
            "psutil": True,
            "process_details": True,
            "connections": True,
        },
        "summary": {
            "process_count": len(processes),
            "connection_count": len(connection_rows),
            "active_connection_count": active_connection_count,
            "known_port_listener_count": len(port_listener_rows),
            "known_port_activity_count": len(port_activity_rows),
            "known_port_time_wait_count": int(port_activity_status_counts.get("TIME_WAIT", 0)),
            "active_known_port_activity_count": active_known_port_activity_count,
            "pid_file_count": len(pid_file_rows),
            "volatile_pid_count": len(volatile_pids),
            "total_rss": total_rss,
            "total_rss_human": _human_bytes(total_rss),
            "warning_count": len(warnings),
            "observation_count": len(observations),
        },
        "known_ports": known_ports,
        "pid_files": pid_file_rows,
        "pid_file_health": pid_file_health,
        "service_summary": service_summary,
        "port_listeners": port_listener_rows,
        "port_activity": port_activity_rows,
        "port_activity_status_counts": dict(port_activity_status_counts),
        "process_tree": _process_tree(processes),
        "processes": processes,
        "notable_processes": _notable_processes(processes),
        "volatile_pids": volatile_pids,
        "connections": connection_rows,
        "connection_status_counts": dict(connection_status_counts),
        "warnings": warnings,
        "observations": observations,
    }


def _control_root_from_env() -> Path | None:
    value = os.environ.get("MAIN_COMPUTER_CONTROL_ROOT", "").strip()
    if not value:
        return None
    return Path(value)


def _is_int_like(value: object) -> bool:
    try:
        int(value)  # type: ignore[arg-type]
        return True
    except Exception:
        return False


def _read_pid_files(control_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for filename in PID_FILENAMES:
        path = control_root / filename
        raw = ""
        pid: int | None = None
        exists = path.exists()
        if exists:
            try:
                raw = path.read_text(encoding="utf-8", errors="ignore").strip()
                pid = int(raw)
            except Exception:
                pid = None
        rows.append(
            {
                "name": filename,
                "path": str(path),
                "exists": exists,
                "raw": raw,
                "pid": pid,
            }
        )
    return rows


def _add_pid(
    pid_sources: dict[int, set[str]],
    role_sources: dict[int, set[str]],
    pid: int | None,
    source: str,
    role: str,
) -> None:
    if pid in {None, 0}:
        return
    pid_int = int(pid)
    pid_sources.setdefault(pid_int, set()).add(source)
    role_sources.setdefault(pid_int, set()).add(role)


def _discover_port_processes(known_ports: dict[str, int]) -> tuple[list[dict[str, Any]], dict[int, set[str]]]:
    rows: list[dict[str, Any]] = []
    roles: dict[int, set[str]] = {}
    if psutil is None or not known_ports:
        return rows, roles
    port_to_name = {port: name for name, port in known_ports.items()}
    try:
        connections = psutil.net_connections(kind="inet")
    except Exception:
        return rows, roles
    for conn in connections:
        local = getattr(conn, "laddr", None)
        local_port = getattr(local, "port", None)
        if local_port not in port_to_name:
            continue
        pid = getattr(conn, "pid", None)
        status = str(getattr(conn, "status", "") or "").upper()
        service = port_to_name[int(local_port)]
        row = {
            "service": service,
            "port": int(local_port),
            "pid": int(pid) if pid else None,
            "status": status,
            "local": _format_address(local),
            "remote": _format_address(getattr(conn, "raddr", None)),
        }
        rows.append(row)
        if pid:
            roles.setdefault(int(pid), set()).add(service)
    rows.sort(
        key=lambda item: (
            item.get("service") or "",
            item.get("status") != "LISTEN",
            item.get("status") or "",
            item.get("pid") or 0,
            item.get("local") or "",
            item.get("remote") or "",
        )
    )
    return rows, roles


def _discover_marker_processes(repo_root: Path) -> dict[int, set[str]]:
    roles: dict[int, set[str]] = {}
    if psutil is None:
        return roles
    repo_marker = str(repo_root).lower()
    repo_name = repo_root.name.lower()
    for proc in psutil.process_iter(["pid", "name", "cmdline", "ppid"]):
        try:
            info = proc.info
            pid = int(info.get("pid") or 0)
            name = str(info.get("name") or "")
            cmdline = " ".join(info.get("cmdline") or [])
            haystack = f"{name} {cmdline}".lower()
        except Exception:
            continue
        discovered: set[str] = set()
        if repo_marker and repo_marker in haystack:
            discovered.add("repo-root")
        if repo_name and repo_name in haystack:
            discovered.add("repo-name")
        for marker in PROCESS_MARKERS:
            if marker in haystack:
                discovered.add("main-computer")
        for role, markers in SERVICE_MARKERS.items():
            if any(marker in haystack for marker in markers):
                discovered.add(role)
        if discovered:
            roles.setdefault(pid, set()).update(discovered)
    return roles


def _expand_with_relatives(pid_sources: dict[int, set[str]], role_sources: dict[int, set[str]]) -> None:
    if psutil is None:
        return
    discovered = list(pid_sources)
    for pid in discovered:
        try:
            proc = psutil.Process(pid)
        except Exception:
            continue
        try:
            parent = proc.parent()
            if parent is not None and _looks_like_context_parent(parent):
                _add_pid(pid_sources, role_sources, parent.pid, f"parent-of:{pid}", "parent-context")
        except Exception:
            pass
        try:
            for child in proc.children(recursive=True):
                _add_pid(pid_sources, role_sources, child.pid, f"child-of:{pid}", "child-process")
        except Exception:
            pass


def _looks_like_context_parent(proc: Any) -> bool:
    try:
        name = str(proc.name() or "").lower()
        cmdline = " ".join(proc.cmdline() or []).lower()
    except Exception:
        return False
    haystack = f"{name} {cmdline}"
    return any(marker in haystack for marker in ("python", "powershell", "pwsh", "cmd", "bash", "wsl", "main_computer"))


def _process_payload(
    proc: Any,
    *,
    repo_root: Path,
    sources: list[str],
    roles: list[str],
    known_ports: dict[str, int],
) -> dict[str, Any]:
    with proc.oneshot():
        pid = int(proc.pid)
        name = _safe_call(proc.name, "")
        status = _safe_call(proc.status, "")
        ppid = _safe_call(proc.ppid, None)
        username = _safe_call(proc.username, "")
        exe = _safe_call(proc.exe, "")
        cwd = _safe_call(proc.cwd, "")
        cmdline_parts = _safe_call(proc.cmdline, [])
        cmdline = " ".join(cmdline_parts or []) if isinstance(cmdline_parts, list) else str(cmdline_parts or "")
        create_time = _safe_call(proc.create_time, None)
        memory_info = _safe_call(proc.memory_info, None)
        memory_rss = int(getattr(memory_info, "rss", 0) or 0)
        memory_vms = int(getattr(memory_info, "vms", 0) or 0)
        memory_percent = _safe_call(proc.memory_percent, None)
        cpu_percent = _safe_call(proc.cpu_percent, None)
        cpu_times = _safe_call(proc.cpu_times, None)
        num_threads = _safe_call(proc.num_threads, None)
        open_files = _safe_call(proc.open_files, [])
        io_counters = _safe_call(proc.io_counters, None)

    process_ports = _process_known_ports(proc, known_ports)
    children = []
    try:
        children = [int(child.pid) for child in proc.children(recursive=False)]
    except Exception:
        children = []

    return {
        "pid": pid,
        "ppid": int(ppid) if ppid is not None else None,
        "name": str(name or ""),
        "status": str(status or ""),
        "username": str(username or ""),
        "roles": roles,
        "sources": sources,
        "is_current_process": pid == os.getpid(),
        "is_repo_process": _path_contains(cwd, repo_root) or _path_contains(cmdline, repo_root),
        "created_at": _format_epoch(create_time),
        "cwd": str(cwd or ""),
        "exe": str(exe or ""),
        "cmdline": cmdline,
        "command_preview": _compact(cmdline, 220),
        "cpu_percent": _round_float(cpu_percent),
        "cpu_user_s": _round_float(getattr(cpu_times, "user", None)),
        "cpu_system_s": _round_float(getattr(cpu_times, "system", None)),
        "memory_rss": memory_rss,
        "memory_vms": memory_vms,
        "memory_human": _human_bytes(memory_rss),
        "memory_percent": _round_float(memory_percent),
        "num_threads": int(num_threads) if isinstance(num_threads, int) else None,
        "open_file_count": len(open_files or []) if isinstance(open_files, list) else None,
        "io": _io_payload(io_counters),
        "known_ports": process_ports,
        "children": children,
    }


def _process_known_ports(proc: Any, known_ports: dict[str, int]) -> list[dict[str, Any]]:
    if not known_ports:
        return []
    port_to_name = {port: name for name, port in known_ports.items()}
    rows: list[dict[str, Any]] = []
    try:
        if hasattr(proc, "net_connections"):
            connections = proc.net_connections(kind="inet")
        else:  # pragma: no cover - old psutil compatibility
            connections = proc.connections(kind="inet")
    except Exception:
        return rows
    for conn in connections:
        local = getattr(conn, "laddr", None)
        remote = getattr(conn, "raddr", None)
        ports = [
            getattr(local, "port", None),
            getattr(remote, "port", None),
        ]
        matched = [int(port) for port in ports if port in port_to_name]
        for port in matched:
            rows.append(
                {
                    "service": port_to_name[port],
                    "port": port,
                    "status": str(getattr(conn, "status", "") or "").upper(),
                    "local": _format_address(local),
                    "remote": _format_address(remote),
                }
            )
    rows.sort(key=lambda item: (item.get("service") or "", item.get("status") != "LISTEN", item.get("status") or ""))
    return rows[:20]


def _connection_payloads(
    relevant_pids: set[int],
    known_ports: dict[str, int],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    if psutil is None:
        return [], Counter()
    rows: list[dict[str, Any]] = []
    port_to_name = {port: name for name, port in known_ports.items()}
    try:
        connections = psutil.net_connections(kind="inet")
    except Exception:
        return rows, Counter()
    for conn in connections:
        pid = getattr(conn, "pid", None)
        local = getattr(conn, "laddr", None)
        remote = getattr(conn, "raddr", None)
        local_port = getattr(local, "port", None)
        remote_port = getattr(remote, "port", None)
        matched_port = local_port if local_port in port_to_name else remote_port if remote_port in port_to_name else None
        if pid not in relevant_pids and matched_port is None:
            continue
        rows.append(
            {
                "pid": int(pid) if pid else None,
                "service": port_to_name.get(int(matched_port), "") if matched_port is not None else "",
                "status": str(getattr(conn, "status", "") or "").upper(),
                "local": _format_address(local),
                "remote": _format_address(remote),
            }
        )
    status_counts = _status_counts(rows)
    rows.sort(key=lambda item: (item.get("service") or "", item.get("status") != "LISTEN", item.get("status") or "", item.get("pid") or 0, item.get("local") or ""))
    return rows[:limit], status_counts


def _process_tree(processes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_pid = {int(row["pid"]): row for row in processes if isinstance(row.get("pid"), int)}
    children: dict[int | None, list[int]] = {}
    for pid, row in by_pid.items():
        parent = row.get("ppid")
        parent_key = int(parent) if isinstance(parent, int) and parent in by_pid else None
        children.setdefault(parent_key, []).append(pid)

    def node(pid: int) -> dict[str, Any]:
        row = by_pid[pid]
        return {
            "pid": pid,
            "name": row.get("name"),
            "roles": row.get("roles", []),
            "memory_human": row.get("memory_human"),
            "children": [node(child_pid) for child_pid in sorted(children.get(pid, []))],
        }

    return [node(pid) for pid in sorted(children.get(None, []))]


def _pid_file_health(
    pid_files: list[dict[str, Any]],
    processes: list[dict[str, Any]],
    volatile_pids: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    readable_pids = {int(row["pid"]) for row in processes if isinstance(row.get("pid"), int)}
    volatile = {int(row["pid"]) for row in volatile_pids if isinstance(row.get("pid"), int)}
    rows: list[dict[str, Any]] = []
    for item in pid_files:
        pid = item.get("pid")
        if not item.get("exists"):
            state = "missing"
        elif not isinstance(pid, int):
            state = "invalid"
        elif pid in readable_pids:
            state = "readable"
        elif pid in volatile:
            state = "stale"
        else:
            state = "unresolved"
        rows.append(
            {
                "name": item.get("name"),
                "pid": pid,
                "state": state,
                "exists": bool(item.get("exists")),
            }
        )
    return rows


def _service_summary(
    known_ports: dict[str, int],
    port_listeners: list[dict[str, Any]],
    port_activity: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for service, port in sorted(known_ports.items(), key=lambda item: item[0]):
        listeners = [row for row in port_listeners if row.get("service") == service and row.get("port") == port]
        activity = [row for row in port_activity if row.get("service") == service and row.get("port") == port]
        listener_pids = sorted({int(row["pid"]) for row in listeners if isinstance(row.get("pid"), int)})
        listener_endpoints = sorted({str(row.get("local") or "") for row in listeners if row.get("local")})
        status_counts = _status_counts(activity)
        findings: list[str] = []
        if len(listener_pids) > 1:
            findings.append("multiple-listener-pids")
        if any(_is_any_address(endpoint) for endpoint in listener_endpoints):
            findings.append("any-address-listener")
        if listeners:
            state = "listening"
        elif _active_row_count(activity):
            state = "active-without-listener"
        else:
            state = "not-observed"
        rows.append(
            {
                "service": service,
                "port": port,
                "state": state,
                "listener_count": len(listeners),
                "listener_pids": listener_pids,
                "listener_endpoints": listener_endpoints,
                "activity_count": len(activity),
                "active_activity_count": _active_row_count(activity),
                "time_wait_count": int(status_counts.get("TIME_WAIT", 0)),
                "status_counts": dict(status_counts),
                "findings": findings,
            }
        )
    return rows


def _notable_processes(processes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    def brief(row: dict[str, Any]) -> dict[str, Any]:
        io = row.get("io") if isinstance(row.get("io"), dict) else {}
        return {
            "pid": row.get("pid"),
            "name": row.get("name"),
            "roles": row.get("roles", []),
            "memory_human": row.get("memory_human"),
            "memory_rss": row.get("memory_rss"),
            "cpu_percent": row.get("cpu_percent"),
            "num_threads": row.get("num_threads"),
            "write_bytes": io.get("write_bytes"),
            "command_preview": row.get("command_preview"),
        }

    return {
        "top_memory": [brief(row) for row in sorted(processes, key=lambda row: int(row.get("memory_rss") or 0), reverse=True)[:8]],
        "top_cpu": [brief(row) for row in sorted(processes, key=lambda row: float(row.get("cpu_percent") or 0), reverse=True)[:8]],
        "top_io_write": [
            brief(row)
            for row in sorted(
                processes,
                key=lambda row: int((row.get("io") if isinstance(row.get("io"), dict) else {}).get("write_bytes") or 0),
                reverse=True,
            )[:8]
        ],
    }


def _is_listen(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "").upper() == "LISTEN"


def _status_counts(rows: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(row.get("status") or "UNKNOWN").upper() for row in rows)


def _active_status_count(status_counts: Counter[str] | dict[str, int]) -> int:
    return sum(int(count) for status, count in status_counts.items() if str(status).upper() not in NOISY_CONNECTION_STATUSES)


def _active_row_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if str(row.get("status") or "").upper() not in NOISY_CONNECTION_STATUSES)


def _is_any_address(endpoint: str) -> bool:
    return endpoint.startswith("0.0.0.0:") or endpoint.startswith(":::") or endpoint.startswith("[::]:")


def _safe_call(func: Any, fallback: Any) -> Any:
    try:
        return func()
    except Exception:
        return fallback


def _path_contains(value: object, root: Path) -> bool:
    text = str(value or "").lower()
    return str(root).lower() in text


def _format_address(value: Any) -> str:
    if not value:
        return ""
    host = getattr(value, "ip", None) or getattr(value, "host", None)
    port = getattr(value, "port", None)
    if host is None and isinstance(value, tuple) and value:
        host = value[0]
        port = value[1] if len(value) > 1 else None
    return f"{host}:{port}" if port is not None else str(host)


def _format_epoch(value: object) -> str:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _round_float(value: object) -> float | None:
    try:
        return round(float(value), 2)
    except Exception:
        return None


def _io_payload(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    return {
        "read_count": getattr(value, "read_count", None),
        "write_count": getattr(value, "write_count", None),
        "read_bytes": getattr(value, "read_bytes", None),
        "write_bytes": getattr(value, "write_bytes", None),
    }


def _compact(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _human_bytes(value: int | float | None) -> str:
    if value is None:
        return "unknown"
    try:
        amount = float(value)
    except Exception:
        return "unknown"
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if abs(amount) < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} TB"


def _process_sort_key(row: dict[str, Any]) -> tuple[int, int, int]:
    role_order = {
        "viewport-current": 0,
        "viewport": 1,
        "heartbeat": 2,
        "app": 3,
        "hub": 4,
        "worker": 5,
        "ollama": 6,
        "gitea": 7,
        "blockchain": 8,
        "executor": 9,
    }
    roles = row.get("roles") if isinstance(row.get("roles"), list) else []
    first_role = min((role_order.get(str(role), 50) for role in roles), default=50)
    return (first_role, -int(row.get("memory_rss") or 0), int(row.get("pid") or 0))
