from __future__ import annotations

import os
import platform
import re
import subprocess
from typing import Any, Callable


Runner = Callable[..., subprocess.CompletedProcess[str]]

_ACTIVE_IDLE_THRESHOLD_S = 5 * 60
_QUSER_STATES = {
    "active",
    "conn",
    "connect",
    "connected",
    "disc",
    "disconnected",
    "listen",
    "idle",
    "down",
}


def collect_windows_user_activity(
    *,
    idle_active_threshold_s: int = _ACTIVE_IDLE_THRESHOLD_S,
    runner: Runner | None = None,
    os_name: str | None = None,
    system_name: str | None = None,
) -> dict[str, Any]:
    """Return a safe snapshot of interactive Windows user-session activity.

    The result is intentionally JSON-shaped so it can be embedded directly in
    telemetry and status payloads.  ``active`` means at least one interactive
    session is connected and has not been idle longer than
    ``idle_active_threshold_s``.  ``connected`` session counts are reported
    separately because an RDP/console session can be connected but idle.
    """

    effective_os_name = os_name if os_name is not None else os.name
    effective_system = system_name if system_name is not None else platform.system()
    if effective_os_name != "nt" and str(effective_system).casefold() != "windows":
        return {
            "supported": False,
            "ok": None,
            "active": None,
            "reason": "non-windows",
            "idle_active_threshold_s": int(idle_active_threshold_s),
            "sessions": [],
            "active_session_count": 0,
            "connected_session_count": 0,
        }

    runner = runner or subprocess.run
    command_errors: list[dict[str, Any]] = []
    raw_output = ""
    command_used: list[str] | None = None

    for command in (["quser.exe"], ["query.exe", "user"]):
        try:
            result = runner(
                command,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception as exc:
            command_errors.append({"command": command, "error": str(exc)})
            continue

        output = _process_output_text(result.stdout) or _process_output_text(result.stderr)
        if result.returncode == 0 and output.strip():
            raw_output = output
            command_used = command
            break
        command_errors.append(
            {
                "command": command,
                "returncode": result.returncode,
                "stderr": _compact(_process_output_text(result.stderr), 400),
            }
        )

    if not raw_output:
        return {
            "supported": True,
            "ok": False,
            "active": None,
            "reason": "query-user-unavailable",
            "idle_active_threshold_s": int(idle_active_threshold_s),
            "sessions": [],
            "active_session_count": 0,
            "connected_session_count": 0,
            "command_errors": command_errors,
        }

    sessions = parse_query_user_output(raw_output, idle_active_threshold_s=idle_active_threshold_s)
    connected_sessions = [session for session in sessions if session.get("connected")]
    active_sessions = [session for session in sessions if session.get("active")]
    return {
        "supported": True,
        "ok": True,
        "active": bool(active_sessions),
        "reason": "active-session-observed" if active_sessions else "no-active-session-observed",
        "idle_active_threshold_s": int(idle_active_threshold_s),
        "active_session_count": len(active_sessions),
        "connected_session_count": len(connected_sessions),
        "session_count": len(sessions),
        "command": command_used,
        "sessions": sessions,
    }


def parse_query_user_output(output: str, *, idle_active_threshold_s: int = _ACTIVE_IDLE_THRESHOLD_S) -> list[dict[str, Any]]:
    """Parse ``quser``/``query user`` output into stable session dictionaries."""

    sessions: list[dict[str, Any]] = []
    for raw_line in str(output or "").replace("\r", "\n").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if _looks_like_header(line):
            continue
        parsed = _parse_query_user_line(line, idle_active_threshold_s=idle_active_threshold_s)
        if parsed is not None:
            sessions.append(parsed)
    return sessions


def _parse_query_user_line(line: str, *, idle_active_threshold_s: int) -> dict[str, Any] | None:
    text = line.strip()
    current = text.startswith(">")
    if current:
        text = text[1:].strip()
    if not text:
        return None

    tokens = text.split()
    id_index = _session_id_index(tokens)
    if id_index is None or id_index == 0:
        return None

    username = tokens[0]
    session_name = " ".join(tokens[1:id_index]).strip()
    session_id = _to_int(tokens[id_index])
    state = tokens[id_index + 1] if len(tokens) > id_index + 1 else ""
    idle_display = tokens[id_index + 2] if len(tokens) > id_index + 2 else ""
    logon_time = " ".join(tokens[id_index + 3 :]).strip()
    idle_seconds = parse_query_user_idle_seconds(idle_display)

    connected = _is_connected_state(state)
    active = bool(
        connected
        and idle_seconds is not None
        and idle_seconds <= int(idle_active_threshold_s)
    )

    return {
        "username": username,
        "session_name": session_name,
        "session_id": session_id,
        "state": state,
        "idle": idle_display,
        "idle_seconds": idle_seconds,
        "logon_time": logon_time,
        "current": current,
        "connected": connected,
        "active": active,
        "console": session_name.casefold() == "console",
        "remote": session_name.casefold().startswith("rdp-"),
    }


def parse_query_user_idle_seconds(value: object) -> int | None:
    """Convert the QUSER IDLE TIME field to seconds.

    QUSER uses ``none``/``.`` for no idle time, an integer for minutes,
    ``HH:MM`` for hours and minutes, and ``D+HH:MM`` for day spans.
    """

    text = str(value or "").strip().casefold()
    if text in {"", "none", "."}:
        return 0
    if text.isdigit():
        return int(text) * 60

    day_count = 0
    rest = text
    if "+" in rest:
        day_part, rest = rest.split("+", 1)
        if not day_part.isdigit():
            return None
        day_count = int(day_part)

    match = re.fullmatch(r"(?P<hours>\d{1,2}):(?P<minutes>\d{1,2})", rest)
    if match:
        hours = int(match.group("hours"))
        minutes = int(match.group("minutes"))
        return ((day_count * 24 + hours) * 60 + minutes) * 60

    return None


def _session_id_index(tokens: list[str]) -> int | None:
    for index, token in enumerate(tokens[1:], start=1):
        if not token.isdigit():
            continue
        state = tokens[index + 1].casefold() if len(tokens) > index + 1 else ""
        if state in _QUSER_STATES:
            return index
    return None


def _is_connected_state(state: object) -> bool:
    return str(state or "").strip().casefold() in {"active", "conn", "connect", "connected"}


def _looks_like_header(line: str) -> bool:
    lowered = " ".join(line.strip().casefold().split())
    return lowered.startswith("username ") and " idle time" in lowered


def _process_output_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _to_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _compact(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
