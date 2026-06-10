from __future__ import annotations

from pathlib import Path

from main_computer import heartbeat


def test_launch_detached_uses_create_no_window_on_windows(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(heartbeat.sys, "platform", "win32")
    monkeypatch.setattr(heartbeat.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(heartbeat.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    monkeypatch.setattr(heartbeat.subprocess, "DETACHED_PROCESS", 0x00000008, raising=False)

    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 56789

    def fake_popen(command, **kwargs):
        calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess()

    monkeypatch.setattr(heartbeat.subprocess, "Popen", fake_popen)

    process = heartbeat._launch_detached(
        ["python.exe", "-m", "main_computer.cli", "heartbeat"],
        cwd=tmp_path,
        stdout_path=tmp_path / "heartbeat.out.log",
        stderr_path=tmp_path / "heartbeat.err.log",
    )

    assert process.pid == 56789
    flags = int(calls[0]["kwargs"]["creationflags"])  # type: ignore[index]
    assert flags & heartbeat.subprocess.CREATE_NO_WINDOW
    assert flags & heartbeat.subprocess.CREATE_NEW_PROCESS_GROUP
    assert not (flags & heartbeat.subprocess.DETACHED_PROCESS)
