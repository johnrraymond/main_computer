from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from main_computer.windows_user_activity import collect_windows_user_activity

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
    "wsl": ("wsl", "wslhost", "wslservice", "wslrelay", "vmmemwsl"),
}
PRIMARY_SERVICE_ROLES = ("app", "heartbeat", "hub", "worker", "ollama", "gitea", "blockchain")


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
    running the deeper Level 5 sanity checks. It keeps process environment
    variables out of the payload and focuses on process, port, CPU, memory,
    handle, file, and socket telemetry that can be rendered safely in the
    local control panel.
    """

    repo_root = repo_root.expanduser().resolve()
    control_root = (control_root or _control_root_from_env() or repo_root).expanduser().resolve()
    known_ports = {str(name): int(port) for name, port in (known_ports or {}).items() if _is_int_like(port)}
    started_at = datetime.now(timezone.utc)
    current_pid = int(current_pid or os.getpid())

    pid_file_rows = _read_pid_files(control_root)
    warnings: list[str] = []
    observations: list[str] = []
    user_activity = collect_windows_user_activity()

    if psutil is None:
        warnings.append("psutil is not installed; process-level telemetry is unavailable.")
        return _empty_report(
            repo_root=repo_root,
            control_root=control_root,
            known_ports=known_ports,
            current_pid=current_pid,
            started_at=started_at,
            pid_file_rows=pid_file_rows,
            warnings=warnings,
            observations=observations,
            user_activity=user_activity,
        )

    pid_sources: dict[int, set[str]] = {}
    role_sources: dict[int, set[str]] = {}
    _add_pid(pid_sources, role_sources, current_pid, "current-process", "viewport-current")

    for row in pid_file_rows:
        pid = row.get("pid")
        if isinstance(pid, int):
            role = "heartbeat" if "heartbeat" in str(row.get("name", "")).lower() else "viewport"
            _add_pid(pid_sources, role_sources, pid, f"pid-file:{row.get('name')}", role)

    port_activity, port_pid_roles = _discover_port_processes(known_ports)
    port_listeners = [row for row in port_activity if str(row.get("status") or "").upper() == "LISTEN"]
    for pid, roles in port_pid_roles.items():
        for role in roles:
            _add_pid(pid_sources, role_sources, pid, f"known-port:{role}", role)

    marker_pids = _discover_marker_processes(repo_root)
    for pid, roles in marker_pids.items():
        for role in roles:
            _add_pid(pid_sources, role_sources, pid, f"marker:{role}", role)

    _expand_with_relatives(pid_sources, role_sources)

    volatile_pid_count = 0
    processes: list[dict[str, Any]] = []
    for pid in sorted(pid_sources):
        try:
            proc = psutil.Process(pid)
            proc.status()
        except Exception:
            volatile_pid_count += 1
            observations.append(f"PID {pid} was discovered but disappeared or became unreadable during the snapshot.")
            continue
        try:
            processes.append(
                _process_payload(
                    proc,
                    repo_root=repo_root,
                    sources=sorted(pid_sources.get(pid, set())),
                    roles=sorted(role_sources.get(pid, set())),
                    known_ports=known_ports,
                    current_pid=current_pid,
                )
            )
        except Exception as exc:
            warnings.append(f"PID {pid} telemetry failed: {exc}")

    processes.sort(key=_process_sort_key)
    if len(processes) > process_limit:
        warnings.append(f"Process telemetry truncated from {len(processes)} to {process_limit} rows.")
        processes = processes[:process_limit]

    relevant_pids = {int(row["pid"]) for row in processes if isinstance(row.get("pid"), int)}
    port_listeners = [
        row for row in port_listeners if row.get("pid") in relevant_pids or row.get("pid") is None
    ]
    port_activity = [
        row for row in port_activity if row.get("pid") in relevant_pids or row.get("pid") is None
    ]
    connection_rows = _connection_payloads(relevant_pids, known_ports, limit=connection_limit)
    total_rss = sum(int(row.get("memory_rss") or 0) for row in processes)
    active_connection_count = sum(1 for row in connection_rows if _is_active_socket_status(row.get("status")))
    active_known_port_activity_count = sum(1 for row in port_activity if _is_active_socket_status(row.get("status")))
    known_port_time_wait_count = sum(1 for row in port_activity if str(row.get("status") or "").upper() == "TIME_WAIT")
    service_summary = _service_summary(known_ports, port_activity)
    pid_file_health = _pid_file_health(pid_file_rows, relevant_pids)
    role_summary = _role_summary(processes)
    top_processes = _top_processes(processes)
    operator_summary = _operator_summary(
        service_summary=service_summary,
        top_processes=top_processes,
        summary_values={
            "process_count": len(processes),
            "known_port_listener_count": len(port_listeners),
            "known_port_time_wait_count": known_port_time_wait_count,
            "total_rss_human": _human_bytes(total_rss),
        },
        warnings=warnings,
        observations=observations,
    )

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
            "windows_user_activity": bool(user_activity.get("supported")),
        },
        "summary": {
            "process_count": len(processes),
            "connection_count": len(connection_rows),
            "active_connection_count": active_connection_count,
            "known_port_listener_count": len(port_listeners),
            "known_port_activity_count": len(port_activity),
            "known_port_time_wait_count": known_port_time_wait_count,
            "active_known_port_activity_count": active_known_port_activity_count,
            "pid_file_count": len(pid_file_rows),
            "volatile_pid_count": volatile_pid_count,
            "total_rss": total_rss,
            "total_rss_human": _human_bytes(total_rss),
            "warning_count": len(warnings),
            "observation_count": len(observations),
            "interactive_user_active": user_activity.get("active"),
            "interactive_user_active_session_count": int(user_activity.get("active_session_count") or 0),
            "interactive_user_connected_session_count": int(user_activity.get("connected_session_count") or 0),
        },
        "known_ports": known_ports,
        "user_activity": user_activity,
        "pid_files": pid_file_rows,
        "pid_file_health": pid_file_health,
        "operator_summary": operator_summary,
        "service_summary": service_summary,
        "role_summary": role_summary,
        "top_processes": top_processes,
        "port_listeners": port_listeners,
        "port_activity": port_activity,
        "port_activity_status_counts": dict(Counter(str(row.get("status") or "UNKNOWN") for row in port_activity)),
        "process_tree": _process_tree(processes),
        "processes": processes,
        "connections": connection_rows,
        "warnings": warnings,
        "observations": observations,
    }


def _empty_report(
    *,
    repo_root: Path,
    control_root: Path,
    known_ports: dict[str, int],
    current_pid: int,
    started_at: datetime,
    pid_file_rows: list[dict[str, Any]],
    warnings: list[str],
    observations: list[str],
    user_activity: dict[str, Any],
) -> dict[str, Any]:
    summary = {
        "process_count": 0,
        "connection_count": 0,
        "active_connection_count": 0,
        "known_port_listener_count": 0,
        "known_port_activity_count": 0,
        "known_port_time_wait_count": 0,
        "active_known_port_activity_count": 0,
        "pid_file_count": len(pid_file_rows),
        "volatile_pid_count": 0,
        "total_rss": 0,
        "total_rss_human": "0 B",
        "warning_count": len(warnings),
        "observation_count": len(observations),
        "interactive_user_active": user_activity.get("active"),
        "interactive_user_active_session_count": int(user_activity.get("active_session_count") or 0),
        "interactive_user_connected_session_count": int(user_activity.get("connected_session_count") or 0),
    }
    return {
        "ok": False,
        "level": "level-1-telemetry",
        "updated_at": started_at.isoformat(),
        "repo_root": str(repo_root),
        "control_root": str(control_root),
        "current_pid": current_pid,
        "capabilities": {
            "psutil": False,
            "process_details": False,
            "connections": False,
            "windows_user_activity": bool(user_activity.get("supported")),
        },
        "summary": summary,
        "known_ports": known_ports,
        "user_activity": user_activity,
        "pid_files": pid_file_rows,
        "pid_file_health": _pid_file_health(pid_file_rows, set()),
        "operator_summary": {
            "state": "degraded",
            "headline": "psutil is unavailable, so Level 1 cannot inspect running processes.",
            "next_checks": ["Install psutil in the Python environment that runs Main Computer."],
            "attention": warnings[:8],
        },
        "service_summary": _service_summary(known_ports, []),
        "role_summary": [],
        "top_processes": [],
        "port_listeners": [],
        "port_activity": [],
        "port_activity_status_counts": {},
        "process_tree": [],
        "processes": [],
        "connections": [],
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


def _pid_file_health(pid_file_rows: list[dict[str, Any]], live_pids: set[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in pid_file_rows:
        pid = row.get("pid")
        if not row.get("exists"):
            state = "missing"
        elif not isinstance(pid, int):
            state = "unreadable"
        elif pid in live_pids:
            state = "readable"
        else:
            state = "stale-or-not-observed"
        rows.append(
            {
                "name": row.get("name"),
                "pid": pid,
                "state": state,
                "exists": bool(row.get("exists")),
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
        remote = getattr(conn, "raddr", None)
        local_port = getattr(local, "port", None)
        remote_port = getattr(remote, "port", None)
        matched_port = local_port if local_port in port_to_name else remote_port if remote_port in port_to_name else None
        if matched_port is None:
            continue
        pid = getattr(conn, "pid", None)
        status = str(getattr(conn, "status", "") or "")
        service = port_to_name[int(matched_port)]
        row = {
            "service": service,
            "port": int(matched_port),
            "pid": int(pid) if pid else None,
            "status": status,
            "local": _format_address(local),
            "remote": _format_address(remote),
        }
        rows.append(row)
        if pid:
            roles.setdefault(int(pid), set()).add(service)
    rows.sort(
        key=lambda item: (
            item.get("service") or "",
            _socket_status_sort(str(item.get("status") or "")),
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
    current_pid: int,
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
        "is_current_process": pid == current_pid,
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
                    "status": str(getattr(conn, "status", "") or ""),
                    "local": _format_address(local),
                    "remote": _format_address(remote),
                }
            )
    return rows[:20]


def _connection_payloads(relevant_pids: set[int], known_ports: dict[str, int], *, limit: int) -> list[dict[str, Any]]:
    if psutil is None:
        return []
    rows: list[dict[str, Any]] = []
    port_to_name = {port: name for name, port in known_ports.items()}
    try:
        connections = psutil.net_connections(kind="inet")
    except Exception:
        return rows
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
                "status": str(getattr(conn, "status", "") or ""),
                "local": _format_address(local),
                "remote": _format_address(remote),
            }
        )
    rows.sort(key=lambda item: (item.get("service") or "", _socket_status_sort(str(item.get("status") or "")), item.get("pid") or 0, item.get("local") or ""))
    return rows[:limit]


def _service_summary(known_ports: dict[str, int], port_activity: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for service, port in sorted(known_ports.items(), key=lambda item: (item[0], item[1])):
        activity = [row for row in port_activity if row.get("service") == service and int(row.get("port") or -1) == int(port)]
        listeners = [row for row in activity if str(row.get("status") or "").upper() == "LISTEN"]
        active = [row for row in activity if _is_active_socket_status(row.get("status"))]
        status_counts = Counter(str(row.get("status") or "UNKNOWN") for row in activity)
        listener_pids = sorted({int(row["pid"]) for row in listeners if isinstance(row.get("pid"), int)})
        listener_endpoints = sorted({str(row.get("local") or "") for row in listeners if row.get("local")})
        findings: list[str] = []
        if len(listener_pids) > 1:
            findings.append("multiple-listener-pids")
        if any(_is_any_address(endpoint) for endpoint in listener_endpoints):
            findings.append("any-address-listener")
        if listeners:
            state = "listening"
        elif active:
            state = "active-no-listener"
        else:
            state = "not-observed"
        rows.append(
            {
                "service": service,
                "port": int(port),
                "state": state,
                "listener_count": len(listeners),
                "listener_pids": listener_pids,
                "listener_endpoints": listener_endpoints,
                "activity_count": len(activity),
                "active_activity_count": len(active),
                "time_wait_count": int(status_counts.get("TIME_WAIT", 0)),
                "status_counts": dict(status_counts),
                "findings": findings,
            }
        )
    return rows


def _role_summary(processes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for process in processes:
        for role in _summary_roles(process):
            bucket = buckets.setdefault(
                role,
                {
                    "role": role,
                    "process_count": 0,
                    "rss": 0,
                    "thread_count": 0,
                    "pids": [],
                    "top_process": None,
                },
            )
            bucket["process_count"] += 1
            bucket["rss"] += int(process.get("memory_rss") or 0)
            bucket["thread_count"] += int(process.get("num_threads") or 0)
            bucket["pids"].append(process.get("pid"))
            top = bucket.get("top_process")
            if not top or int(process.get("memory_rss") or 0) > int(top.get("memory_rss") or 0):
                bucket["top_process"] = _process_brief(process)
    rows = []
    for role, bucket in buckets.items():
        row = dict(bucket)
        row["rss_human"] = _human_bytes(row["rss"])
        row["pids"] = sorted(pid for pid in row["pids"] if isinstance(pid, int))[:24]
        rows.append(row)
    rows.sort(key=lambda row: (-int(row.get("rss") or 0), str(row.get("role") or "")))
    return rows


def _summary_roles(process: dict[str, Any]) -> list[str]:
    roles = {str(role) for role in process.get("roles", []) if role}
    result: list[str] = []
    for role in ("viewport-current", "viewport", "heartbeat", "app", "hub", "worker", "ollama", "gitea", "blockchain", "docker", "wsl", "main-computer"):
        if role in roles:
            result.append(role)
    if process.get("is_repo_process") and "repo-process" not in result:
        result.append("repo-process")
    return result or ["support"]


def _top_processes(processes: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    rows = sorted(processes, key=lambda row: int(row.get("memory_rss") or 0), reverse=True)
    return [_process_brief(row) for row in rows[:limit]]


def _process_brief(process: dict[str, Any]) -> dict[str, Any]:
    return {
        "pid": process.get("pid"),
        "name": process.get("name"),
        "roles": process.get("roles", []),
        "memory_rss": process.get("memory_rss"),
        "memory_human": process.get("memory_human"),
        "cpu_percent": process.get("cpu_percent"),
        "num_threads": process.get("num_threads"),
        "known_port_count": len(process.get("known_ports") or []),
        "command_preview": process.get("command_preview") or process.get("exe") or "",
    }


def _operator_summary(
    *,
    service_summary: list[dict[str, Any]],
    top_processes: list[dict[str, Any]],
    summary_values: dict[str, Any],
    warnings: list[str],
    observations: list[str],
) -> dict[str, Any]:
    attention: list[str] = []
    next_checks: list[str] = []

    if warnings:
        attention.extend(warnings[:6])

    missing_required = [
        row.get("service")
        for row in service_summary
        if row.get("service") in {"app", "heartbeat"} and row.get("state") != "listening"
    ]
    if missing_required:
        attention.append(f"Required service listeners missing: {', '.join(str(name) for name in missing_required)}.")
        next_checks.append("Restart the viewport/heartbeat pair or run Level 5 if the listener does not return.")

    for row in service_summary:
        findings = set(row.get("findings") or [])
        service = str(row.get("service") or "")
        if "multiple-listener-pids" in findings:
            attention.append(f"{service} port {row.get('port')} has multiple listener PIDs: {row.get('listener_pids')}.")
        if "any-address-listener" in findings:
            next_checks.append(f"Check whether {service} should be bound to all interfaces or loopback only.")

    for process in top_processes[:4]:
        rss = int(process.get("memory_rss") or 0)
        if rss >= 1024 * 1024 * 1024:
            attention.append(f"High-memory process: {process.get('name')} pid {process.get('pid')} uses {process.get('memory_human')}.")
            break

    if not attention:
        attention.append(
            f"No Level 1 hard warnings. {summary_values.get('known_port_listener_count', 0)} known-port listeners and "
            f"{summary_values.get('known_port_time_wait_count', 0)} TIME_WAIT rows observed."
        )

    if not next_checks and observations:
        next_checks.append("Review observations for transient process churn, but no hard Level 1 warning was raised.")
    if not next_checks:
        next_checks.append("Use Level 5 Diagnostics only if a service is unreachable or a Level 1 warning appears.")

    state = "ok"
    if warnings or missing_required:
        state = "degraded"
    elif any("multiple-listener-pids" in set(row.get("findings") or []) for row in service_summary):
        state = "attention"

    headline = (
        f"{summary_values.get('process_count', 0)} processes · "
        f"{summary_values.get('known_port_listener_count', 0)} listeners · "
        f"{summary_values.get('total_rss_human', '0 B')} RSS"
    )
    return {
        "state": state,
        "headline": headline,
        "attention": attention[:12],
        "next_checks": _dedupe(next_checks)[:8],
    }


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


def _socket_status_sort(status: str) -> int:
    status = status.upper()
    if status == "LISTEN":
        return 0
    if status == "ESTABLISHED":
        return 1
    if status in {"SYN_SENT", "SYN_RECV"}:
        return 2
    if status in {"CLOSE_WAIT", "FIN_WAIT1", "FIN_WAIT2"}:
        return 3
    if status == "TIME_WAIT":
        return 4
    return 5


def _is_active_socket_status(status: object) -> bool:
    return str(status or "").upper() not in {"", "NONE", "TIME_WAIT"}


def _is_any_address(endpoint: str) -> bool:
    value = str(endpoint or "").strip().lower()
    return value.startswith("0.0.0.0:") or value.startswith(":::") or value.startswith("[::]:")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


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
        "docker": 10,
        "wsl": 11,
    }
    roles = row.get("roles") if isinstance(row.get("roles"), list) else []
    first_role = min((role_order.get(str(role), 50) for role in roles), default=50)
    return (first_role, -int(row.get("memory_rss") or 0), int(row.get("pid") or 0))
