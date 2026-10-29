"""Start.bat -> service supervisor -> SLH ownership contract."""
from __future__ import annotations

from pathlib import Path
import sys

from main_computer.slh import gateway_service
from main_computer.service_supervisor import CHILD_SPECS, ServiceSupervisor


class FakeAuthority:
    def __init__(self, directory: Path, *, initialized_after: int = 0):
        self.directory = directory
        self.initialized_after = initialized_after
        self.calls = 0

    def initialized(self) -> bool:
        self.calls += 1
        return self.calls > self.initialized_after


def test_gateway_waits_for_bootstrap_and_uses_actual_worker_gateway(tmp_path: Path):
    authority = FakeAuthority(tmp_path, initialized_after=2)
    sleeps = []
    calls = []
    assert gateway_service.run(state_dir=tmp_path,
        authority_factory=lambda _path: authority,
        sleep=sleeps.append, poll_seconds=0.5,
        gateway_main=lambda args: calls.append(args) or 0) == 0
    assert sleeps == [0.5]
    assert calls == [["serve", "--state-dir", str(tmp_path), "--port", "8898"]]


def test_initialized_gateway_starts_immediately(tmp_path: Path):
    authority = FakeAuthority(tmp_path)
    commands = []
    assert gateway_service.run(state_dir=tmp_path,
        authority_factory=lambda _path: authority,
        sleep=lambda _: (_ for _ in ()).throw(AssertionError("should not sleep")),
        gateway_main=lambda args: commands.append(args) or 0) == 0
    assert commands and commands[0][0] == "serve"


def test_default_supervisor_manages_slh_as_child_of_start_bat(tmp_path: Path):
    specs = {spec.name: spec for spec in CHILD_SPECS}
    assert "slh" in specs
    assert specs["slh"].module == "main_computer.slh.gateway_service"
    assert specs["slh"].command(python_command=sys.executable, root=tmp_path) == [
        sys.executable, "-m", "main_computer.slh.gateway_service", "--root", str(tmp_path), "run"]
    assert specs["slh"].stop_priority < specs["main-log"].stop_priority


def test_launcher_stop_recognizes_manual_gateway_ownership():
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "start.bat").read_text(encoding="utf-8")
    starter = (root / "start_v2.bat").read_text(encoding="utf-8")
    stop_ps = (root / "scripts/main-computer-start-stop.ps1").read_text(encoding="utf-8-sig")
    assert "start_v2.bat" in launcher
    assert "-Action start" in starter
    assert '"MAIN_COMPUTER_SLH_PORT" "8898"' in stop_ps
    assert '"tools.slh_managed_gateway"' in stop_ps
    assert '"main_computer.slh.gateway_service"' in stop_ps
