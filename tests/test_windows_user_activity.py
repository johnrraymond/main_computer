from __future__ import annotations

import subprocess

from main_computer.windows_user_activity import (
    collect_windows_user_activity,
    parse_query_user_idle_seconds,
    parse_query_user_output,
)


def test_parse_query_user_output_detects_active_console_user() -> None:
    output = """
 USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME
>alice                 console             1  Active      none   6/20/2026 8:12 AM
 bob                   rdp-tcp#3           2  Disc         1:23  6/20/2026 7:44 AM
"""

    sessions = parse_query_user_output(output, idle_active_threshold_s=300)

    assert len(sessions) == 2
    assert sessions[0]["username"] == "alice"
    assert sessions[0]["session_name"] == "console"
    assert sessions[0]["session_id"] == 1
    assert sessions[0]["connected"] is True
    assert sessions[0]["active"] is True
    assert sessions[0]["console"] is True
    assert sessions[1]["username"] == "bob"
    assert sessions[1]["connected"] is False
    assert sessions[1]["active"] is False


def test_parse_query_user_output_handles_missing_session_name_and_idle_threshold() -> None:
    output = """
 USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME
 carol                                      3  Active      12    6/20/2026 9:01 AM
"""

    sessions = parse_query_user_output(output, idle_active_threshold_s=300)

    assert len(sessions) == 1
    assert sessions[0]["username"] == "carol"
    assert sessions[0]["session_name"] == ""
    assert sessions[0]["idle_seconds"] == 12 * 60
    assert sessions[0]["connected"] is True
    assert sessions[0]["active"] is False


def test_parse_query_user_idle_seconds_formats() -> None:
    assert parse_query_user_idle_seconds("none") == 0
    assert parse_query_user_idle_seconds(".") == 0
    assert parse_query_user_idle_seconds("7") == 7 * 60
    assert parse_query_user_idle_seconds("1:23") == 83 * 60
    assert parse_query_user_idle_seconds("2+03:04") == ((2 * 24 + 3) * 60 + 4) * 60
    assert parse_query_user_idle_seconds("unknown") is None


def test_collect_windows_user_activity_uses_query_runner() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                " USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME\n"
                ">alice                 console             1  Active      none   6/20/2026 8:12 AM\n"
            ),
            stderr="",
        )

    result = collect_windows_user_activity(
        runner=runner,
        os_name="nt",
        system_name="Windows",
        idle_active_threshold_s=300,
    )

    assert result["supported"] is True
    assert result["ok"] is True
    assert result["active"] is True
    assert result["active_session_count"] == 1
    assert result["connected_session_count"] == 1
    assert calls == [["quser.exe"]]


def test_collect_windows_user_activity_non_windows_is_safe() -> None:
    result = collect_windows_user_activity(os_name="posix", system_name="Linux")

    assert result["supported"] is False
    assert result["ok"] is None
    assert result["active"] is None
    assert result["reason"] == "non-windows"
