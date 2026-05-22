from __future__ import annotations

import os
from pathlib import Path
import sys

from main_computer import app_control


def test_stop_existing_viewport_terminates_matching_app_process(tmp_path: Path) -> None:
    pid_file = tmp_path / ".main_computer_viewport.pid"
    pid_file.write_text("12345\n", encoding="utf-8")
    terminated: list[int] = []

    result = app_control.stop_existing_viewport(
        root=tmp_path,
        process_command_reader=lambda pid: "python -m main_computer.app_control --root X run",
        process_terminator=terminated.append,
    )

    assert result["ok"] is True
    assert result["state"] == "terminated"
    assert terminated == [12345]
    assert not pid_file.exists()


def test_stop_existing_viewport_refuses_unrelated_live_process(tmp_path: Path) -> None:
    pid_file = tmp_path / ".main_computer_viewport.pid"
    pid_file.write_text("12345\n", encoding="utf-8")
    terminated: list[int] = []

    result = app_control.stop_existing_viewport(
        root=tmp_path,
        process_command_reader=lambda pid: "notepad.exe",
        process_terminator=terminated.append,
    )

    assert result["ok"] is False
    assert result["state"] == "not-terminated"
    assert terminated == []
    assert pid_file.exists()


def test_stop_existing_viewport_clears_stale_pid(tmp_path: Path) -> None:
    pid_file = tmp_path / ".main_computer_viewport.pid"
    pid_file.write_text("12345\n", encoding="utf-8")

    result = app_control.stop_existing_viewport(
        root=tmp_path,
        process_command_reader=lambda pid: None,
        process_terminator=lambda pid: (_ for _ in ()).throw(AssertionError("stale pid should not be killed")),
    )

    assert result["ok"] is True
    assert result["state"] == "stale-cleared"
    assert not pid_file.exists()


def test_bootstrap_refreshes_app_then_runs_supervisor(tmp_path: Path, monkeypatch) -> None:
    events: list[tuple[str, object]] = []

    monkeypatch.setattr(
        app_control,
        "stop_existing_viewport",
        lambda **kwargs: events.append(("viewport", kwargs["root"])) or {"ok": True, "state": "terminated"},
    )
    monkeypatch.setattr(
        app_control,
        "replace_heartbeat",
        lambda **kwargs: events.append(("heartbeat", kwargs["root"])) or {"ok": True, "state": "replaced"},
    )

    class FakeSupervisor:
        def __init__(self, **kwargs):
            events.append(("supervisor-init", kwargs["root"]))
            events.append(("supervisor-python", kwargs["python_command"]))

        def supervise(self):
            events.append(("supervisor-run", "called"))
            return {"ok": True, "state": "supervising"}

    monkeypatch.setattr(app_control, "ServiceSupervisor", FakeSupervisor)

    state = app_control.bootstrap(root=tmp_path, python_command="managed-python", poll_interval_s=1)

    assert state["ok"] is True
    assert state["bootstrap"]["viewport_takeover"]["state"] == "terminated"
    assert state["bootstrap"]["heartbeat"]["state"] == "replaced"
    assert events[0][0] == "viewport"
    assert events[1][0] == "heartbeat"
    assert events[2][0] == "supervisor-init"
    assert events[3] == ("supervisor-python", "managed-python")
    assert events[4] == ("supervisor-run", "called")
    assert os.environ["MAIN_COMPUTER_PYTHON_COMMAND"] == "managed-python"


def test_bootstrap_normalizes_plain_python_to_current_interpreter(tmp_path: Path, monkeypatch) -> None:
    events: list[tuple[str, object]] = []

    monkeypatch.setattr(app_control, "stop_existing_viewport", lambda **kwargs: {"ok": True, "state": "missing"})
    monkeypatch.setattr(app_control, "replace_heartbeat", lambda **kwargs: {"ok": True, "state": "replaced"})

    class FakeSupervisor:
        def __init__(self, **kwargs):
            events.append(("supervisor-python", kwargs["python_command"]))

        def supervise(self):
            return {"ok": True, "state": "supervising"}

    monkeypatch.setattr(app_control, "ServiceSupervisor", FakeSupervisor)

    state = app_control.bootstrap(root=tmp_path, python_command="python", poll_interval_s=1)

    assert state["ok"] is True
    assert events == [("supervisor-python", sys.executable)]
    assert os.environ["MAIN_COMPUTER_PYTHON_COMMAND"] == sys.executable


def test_run_app_defaults_control_port_from_environment(tmp_path: Path, monkeypatch) -> None:
    events: list[tuple[str, object]] = []

    monkeypatch.setenv("MAIN_COMPUTER_CONTROL_PORT", "28865")
    monkeypatch.setattr(
        app_control,
        "stop_existing_viewport",
        lambda **kwargs: events.append(("viewport", kwargs["root"])) or {"ok": True, "state": "missing"},
    )
    monkeypatch.setattr(
        app_control,
        "replace_heartbeat",
        lambda **kwargs: events.append(("heartbeat-port", kwargs["port"])) or {"ok": True, "state": "ready"},
    )
    monkeypatch.setattr(
        app_control,
        "serve",
        lambda config, *, host, port, verbose: events.append(("serve-port", port)),
    )

    assert app_control.run_app(root=tmp_path) == 0
    assert ("heartbeat-port", 28865) in events
    assert ("serve-port", 28865) in events


def test_parser_leaves_port_for_environment_default(monkeypatch) -> None:
    monkeypatch.setenv("MAIN_COMPUTER_CONTROL_PORT", "28865")

    args = app_control._build_parser().parse_args(["--root", ".", "run"])

    assert args.port is None
    assert app_control._resolve_control_port(args.port) == 28865
