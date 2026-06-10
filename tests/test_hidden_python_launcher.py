from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def load_hidden_launcher() -> ModuleType:
    path = ROOT / "scripts" / "main_computer_hidden_launcher.py"
    spec = importlib.util.spec_from_file_location("main_computer_hidden_launcher", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_hidden_launcher_windows_creation_kwargs_do_not_use_detached_process(monkeypatch) -> None:
    launcher = load_hidden_launcher()
    monkeypatch.setattr(launcher.os, "name", "nt")
    monkeypatch.setattr(launcher.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(launcher.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    monkeypatch.setattr(launcher.subprocess, "DETACHED_PROCESS", 0x00000008, raising=False)

    kwargs = launcher._launch_creation_kwargs()

    flags = int(kwargs["creationflags"])
    assert flags & launcher.subprocess.CREATE_NO_WINDOW
    assert flags & launcher.subprocess.CREATE_NEW_PROCESS_GROUP
    assert not (flags & launcher.subprocess.DETACHED_PROCESS)


def test_hidden_launcher_starts_windows_target_with_create_no_window(monkeypatch, tmp_path: Path) -> None:
    launcher = load_hidden_launcher()
    monkeypatch.setattr(launcher.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(launcher.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    monkeypatch.setattr(launcher.subprocess, "DETACHED_PROCESS", 0x00000008, raising=False)
    monkeypatch.setattr(
        launcher,
        "_launch_creation_kwargs",
        lambda: {"creationflags": launcher.subprocess.CREATE_NO_WINDOW | launcher.subprocess.CREATE_NEW_PROCESS_GROUP},
    )

    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 45678

    def fake_popen(command, **kwargs):
        calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess()

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    pid_json = tmp_path / "runtime" / "start_stop" / "pid.json"
    rc = launcher.main(
        [
            "--cwd",
            str(tmp_path),
            "--stdout",
            str(tmp_path / "stdout.log"),
            "--stderr",
            str(tmp_path / "stderr.log"),
            "--pid-json",
            str(pid_json),
            "--",
            "python.exe",
            "-m",
            "main_computer.app_control",
            "bootstrap",
        ]
    )

    assert rc == 0
    assert calls
    flags = int(calls[0]["kwargs"]["creationflags"])  # type: ignore[index]
    assert flags & launcher.subprocess.CREATE_NO_WINDOW
    assert flags & launcher.subprocess.CREATE_NEW_PROCESS_GROUP
    assert not (flags & launcher.subprocess.DETACHED_PROCESS)
    assert pid_json.exists()
    assert "45678" in pid_json.read_text(encoding="utf-8")
