from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER_PATH = ROOT / "tools" / "nanojev_lifecycle_service.py"


def _load_manager_module():
    spec = importlib.util.spec_from_file_location("nanojev_lifecycle_service", MANAGER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_bat_accepts_lazy_managed_nanojev_flag_and_idle_timeout() -> None:
    start = (ROOT / "start.bat").read_text(encoding="utf-8")
    start_v2 = (ROOT / "start_v2.bat").read_text(encoding="utf-8")

    assert 'call "%~dp0start_v2.bat" %*' in start
    assert '"--nanojev-managed"' in start_v2
    assert '"--nanojev-idle-seconds"' in start_v2
    assert "-NanoJevManaged" in start_v2
    assert "-NanoJevIdleSeconds" in start_v2


def test_startup_routes_managed_mode_through_python_manager_not_direct_container() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert 'MAIN_COMPUTER_NANOJEV_MANAGED = "0"' in helper
    assert 'MAIN_COMPUTER_NANOJEV_MANAGER_PORT = "9765"' in helper
    assert 'MAIN_COMPUTER_NANOJEV_BACKEND_PORT = "9766"' in helper
    assert 'MAIN_COMPUTER_NANOJEV_IDLE_SECONDS = "300"' in helper
    assert "function Start-MainComputerNanoJevManager" in helper
    assert 'tools\\nanojev_lifecycle_service.py' in helper
    assert 'state = "manager-ready-container-idle"' in helper
    assert 'image/container will be created only on first use' in helper
    assert '"build", "nanojev"' not in helper
    assert 'if (Test-MainComputerNanoJevManaged $launchContext)' in helper
    assert '$nanoJevStart = Start-MainComputerNanoJevManager $RootPath $launchContext $pythonCommand' in helper
    assert 'Stop-MainComputerNanoJevManagerGracefully $session' in helper


def test_managed_nanojev_keeps_public_api_stable_and_moves_container_to_backend_port() -> None:
    compose = (ROOT / "docker-compose.nanojev.yml").read_text(encoding="utf-8")
    manager = MANAGER_PATH.read_text(encoding="utf-8")

    assert "${MAIN_COMPUTER_NANOJEV_BIND_PORT:-9765}:9765" in compose
    assert 'parser.add_argument("--listen-port", type=int, default=9765)' in manager
    assert 'parser.add_argument("--backend-port", type=int, default=9766)' in manager
    assert 'if self.path.startswith("/api/")' in manager
    assert '"/control/on"' in manager
    assert '"/control/off"' in manager
    assert '"/control/status"' in manager
    assert '"/control/shutdown"' in manager


def test_lifecycle_off_marks_dirty_and_stops_only_after_idle_timeout() -> None:
    module = _load_manager_module()

    class FakeController:
        backend_url = "http://127.0.0.1:9766"

        def __init__(self) -> None:
            self.running = False
            self.starts = 0
            self.stops = 0

        def health(self) -> bool:
            return self.running

        def start(self) -> None:
            self.starts += 1
            self.running = True

        def stop(self) -> None:
            self.stops += 1
            self.running = False

    now = [100.0]
    controller = FakeController()
    lifecycle = module.NanoJevLifecycle(controller, idle_seconds=30.0, clock=lambda: now[0])

    on = lifecycle.turn_on()
    assert on["running"] is True
    assert on["pinned"] is True
    assert on["dirty"] is False
    assert controller.starts == 1

    off = lifecycle.turn_off()
    assert off["running"] is True
    assert off["pinned"] is False
    assert off["dirty"] is True

    now[0] += 29.0
    assert lifecycle.sweep() is False
    assert controller.stops == 0

    now[0] += 1.0
    assert lifecycle.sweep() is True
    assert controller.stops == 1
    assert lifecycle.status(refresh=False)["running"] is False


def test_proxy_activity_wakes_container_and_starts_idle_countdown_after_request() -> None:
    module = _load_manager_module()

    class FakeController:
        backend_url = "http://127.0.0.1:9766"

        def __init__(self) -> None:
            self.running = False
            self.starts = 0
            self.stops = 0

        def health(self) -> bool:
            return self.running

        def start(self) -> None:
            self.starts += 1
            self.running = True

        def stop(self) -> None:
            self.stops += 1
            self.running = False

    now = [0.0]
    controller = FakeController()
    lifecycle = module.NanoJevLifecycle(controller, idle_seconds=10.0, clock=lambda: now[0])

    lifecycle.begin_request()
    assert controller.starts == 1
    assert lifecycle.status(refresh=False)["dirty"] is False
    lifecycle.end_request()
    assert lifecycle.status(refresh=False)["dirty"] is True

    now[0] = 9.9
    assert lifecycle.sweep() is False
    now[0] = 10.0
    assert lifecycle.sweep() is True
    assert controller.stops == 1


def test_manager_builds_image_only_on_first_use_when_missing() -> None:
    manager = MANAGER_PATH.read_text(encoding="utf-8")

    assert 'def image_exists(self) -> bool:' in manager
    assert 'def ensure_image(self) -> None:' in manager
    assert 'self.ensure_image()' in manager
    assert 'self._compose("build", "--progress", "plain", "nanojev", check=False)' in manager
    assert 'self._compose("up", "-d", "--no-build", "nanojev", check=False)' in manager
    assert 'parser.add_argument("--image-name", default="main-computer/nanojev:unified-games-v1")' in manager
