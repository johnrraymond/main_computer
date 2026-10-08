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


def test_start_bat_defaults_to_lazy_managed_nanojev_and_keeps_direct_escape_hatch() -> None:
    start = (ROOT / "start.bat").read_text(encoding="utf-8")
    start_v2 = (ROOT / "start_v2.bat").read_text(encoding="utf-8")

    assert 'call "%~dp0start_v2.bat" %*' in start
    assert 'set "MC_NANOJEV_MANAGED=1"' in start_v2
    assert '"--nanojev-managed"' in start_v2
    assert '"--nanojev-direct"' in start_v2
    assert ':mc_disable_nanojev_managed' in start_v2
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
    assert 'parser.add_argument("--image-name", default="main-computer/nanojev:managed-v2")' in manager
    assert 'default=os.environ.get("MAIN_COMPUTER_NANOJEV_CHECKPOINT", "champion")' in manager
    assert 'default=os.environ.get("MAIN_COMPUTER_NANOJEV_HF_REPO", "johnrraymond/NanoJev-CLEF")' in manager


def test_managed_nanojev_defaults_to_public_clef_champion_and_keeps_unified_fallback() -> None:
    compose = (ROOT / "docker-compose.nanojev.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "docker" / "nanojev" / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (ROOT / "docker" / "nanojev" / "entrypoint.py").read_text(encoding="utf-8")
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert 'MAIN_COMPUTER_NANOJEV_CHECKPOINT: "${MAIN_COMPUTER_NANOJEV_CHECKPOINT:-champion}"' in compose
    assert 'MAIN_COMPUTER_NANOJEV_HF_REPO: "${MAIN_COMPUTER_NANOJEV_HF_REPO:-johnrraymond/NanoJev-CLEF}"' in compose
    assert 'image: main-computer/nanojev:managed-v2' in compose
    assert 'nanojev-hf-cache:/root/.cache/huggingface' in compose
    assert 'selector == "unified-games-v1"' in entrypoint
    assert '/opt/nanojev/source/scripts/serve_decisions.py' in entrypoint
    assert '/opt/nanojev-clef-service/clef_service.py' in entrypoint
    assert 'COPY tools/nanojev_three_backbone_clef_tinystories_structured_supervision_train.py' in dockerfile
    assert '"--image-name", "main-computer/nanojev:managed-v2"' in helper
    assert 'MAIN_COMPUTER_NANOJEV_START_TIMEOUT_SECONDS" "900"' in helper



def test_idle_unload_next_request_starts_container_again_so_checkpoint_is_reresolved() -> None:
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
    lifecycle = module.NanoJevLifecycle(controller, idle_seconds=5.0, clock=lambda: now[0])

    lifecycle.begin_request()
    lifecycle.end_request()
    assert controller.starts == 1

    now[0] = 5.0
    assert lifecycle.sweep() is True
    assert controller.stops == 1

    now[0] = 6.0
    lifecycle.begin_request()
    assert controller.starts == 2
    lifecycle.end_request()


def test_backend_health_remains_true_after_provider_calls(monkeypatch, tmp_path: Path) -> None:
    module = _load_manager_module()
    controller = module.ComposeNanoJevController(
        root=tmp_path,
        compose_file=tmp_path / "compose.yml",
        project_name="test",
        backend_port=9766,
        start_timeout_seconds=1,
        checkpoint_selector="champion",
        hf_repo="johnrraymond/NanoJev-CLEF",
    )
    monkeypatch.setattr(
        module,
        "_http_json",
        lambda *_args, **_kwargs: {
            "ready": True,
            "model_loaded_once": True,
            "provider_calls": 17,
            "model_family": "nanojev-clef",
            "checkpoint_selector": "champion",
            "checkpoint_repo": "johnrraymond/NanoJev-CLEF",
        },
    )

    assert controller.health() is True


def test_shutdown_always_tears_down_owned_compose_project_even_when_backend_is_unhealthy() -> None:
    module = _load_manager_module()

    class FakeController:
        backend_url = "http://127.0.0.1:9766"

        def __init__(self) -> None:
            self.stops = 0

        def health(self) -> bool:
            return False

        def start(self) -> None:
            raise AssertionError("start should not be called")

        def stop(self) -> None:
            self.stops += 1

    controller = FakeController()
    lifecycle = module.NanoJevLifecycle(controller, idle_seconds=5.0)
    assert lifecycle.status(refresh=False)["running"] is False

    lifecycle.shutdown_now()
    lifecycle.shutdown_now()

    assert controller.stops == 1
    status = lifecycle.status(refresh=False)
    assert status["running"] is False
    assert status["phase"] == "idle"


def test_compose_healthcheck_does_not_treat_prior_inference_as_unhealthy() -> None:
    compose = (ROOT / "docker-compose.nanojev.yml").read_text(encoding="utf-8")

    assert "d.get('ready') is True" in compose
    assert "d.get('model_loaded_once') is True" in compose
    assert "provider_calls') == 0" not in compose


def test_manager_startup_replaces_existing_generation_before_claiming_public_port() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert "function Stop-MainComputerNanoJevManagerAtUrl" in helper
    assert "$existingManagerStop = Stop-MainComputerNanoJevManagerAtUrl $managerUrl 30 15" in helper
    assert 'state = "manager-port-owned-by-foreign-service"' in helper
    assert 'state = "manager-exited-during-startup"' in helper
    assert '"--health-poll-seconds"' in helper
    assert '"--stop-timeout-seconds"' in helper
    assert '"--proxy-timeout-seconds"' in helper
    assert '"--sweep-interval-seconds"' in helper


def test_lifecycle_smoke_covers_real_response_idle_unload_manager_restart_and_fast_timing_flags() -> None:
    smoke = (ROOT / "tools" / "nanojev_lifecycle_smoke.py").read_text(encoding="utf-8")

    assert '"/api/evaluate"' in smoke
    assert '"idle_unload_complete"' in smoke
    assert '"manager_shutdown_complete"' in smoke
    assert 'parser.add_argument("--manager-generations", type=int, default=2)' in smoke
    assert 'parser.add_argument("--idle-seconds", type=float, default=3.0)' in smoke
    assert 'parser.add_argument("--sweep-interval-seconds", type=float, default=0.25)' in smoke
    assert 'parser.add_argument("--health-poll-seconds", type=float, default=0.25)' in smoke
    assert 'parser.add_argument("--poll-seconds", type=float, default=0.25)' in smoke
    assert 'parser.add_argument("--idle-unload-slack-seconds", type=float, default=15.0)' in smoke
    assert 'parser.add_argument("--stop-timeout-seconds", type=float, default=2.0)' in smoke
    assert "for generation in range(1, args.manager_generations + 1):" in smoke
    assert "compose_down(args)" in smoke


def test_host_side_health_probe_also_allows_post_inference_service() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")
    start = helper.index("function Test-MainComputerNanoJevHealth")
    end = helper.index("function Start-MainComputerNanoJev", start)
    health_function = helper[start:end]

    assert 'Get-ObjectPropertyValue $response "ready" $false' in health_function
    assert 'Get-ObjectPropertyValue $response "model_loaded_once" $false' in health_function
    assert "provider_calls" not in health_function


def test_controller_health_uses_configured_request_timeout(tmp_path, monkeypatch) -> None:
    module = _load_manager_module()
    observed: dict[str, float] = {}

    def fake_http_json(url: str, *, method: str = "GET", body=None, timeout: float = 2.0):
        observed["timeout"] = timeout
        return {
            "ready": True,
            "model_loaded_once": True,
            "model_family": "nanojev-clef",
            "checkpoint_selector": "champion",
            "checkpoint_repo": "johnrraymond/NanoJev-CLEF",
        }

    monkeypatch.setattr(module, "_http_json", fake_http_json)
    controller = module.ComposeNanoJevController(
        root=tmp_path,
        compose_file=tmp_path / "docker-compose.nanojev.yml",
        project_name="main-computer-nanojev",
        backend_port=9766,
        start_timeout_seconds=5.0,
        health_poll_seconds=0.05,
        health_request_timeout_seconds=0.25,
    )

    assert controller.health() is True
    assert observed["timeout"] == 0.25


def test_lifecycle_smoke_control_probe_timeout_exceeds_backend_health_probe(monkeypatch) -> None:
    import importlib.util
    from types import SimpleNamespace

    smoke_path = ROOT / "tools" / "nanojev_lifecycle_smoke.py"
    spec = importlib.util.spec_from_file_location("nanojev_lifecycle_smoke", smoke_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    observed: dict[str, float] = {}

    def fake_try_http_json(url: str, *, timeout_seconds: float):
        observed["timeout_seconds"] = timeout_seconds
        return {"ok": True, "mode": "lazy-managed"}

    monkeypatch.setattr(module, "try_http_json", fake_try_http_json)
    args = SimpleNamespace(
        manager_url="http://127.0.0.1:9765",
        control_request_timeout_seconds=0.1,
        health_request_timeout_seconds=0.75,
    )

    status = module.manager_status(args)
    assert status == {"ok": True, "mode": "lazy-managed"}
    assert observed["timeout_seconds"] == 1.25


def test_idle_stop_does_not_block_status_while_compose_down_is_in_progress() -> None:
    import threading

    module = _load_manager_module()
    stop_started = threading.Event()
    allow_stop = threading.Event()

    class FakeController:
        backend_url = "http://127.0.0.1:9766"

        def __init__(self) -> None:
            self.running = True
            self.health_calls = 0
            self.stops = 0

        def health(self) -> bool:
            self.health_calls += 1
            return self.running

        def start(self) -> None:
            self.running = True

        def stop(self) -> None:
            self.stops += 1
            stop_started.set()
            assert allow_stop.wait(timeout=2.0)
            self.running = False

    now = [0.0]
    controller = FakeController()
    lifecycle = module.NanoJevLifecycle(controller, idle_seconds=3.0, clock=lambda: now[0])
    lifecycle.turn_off()
    now[0] = 3.0

    result: list[bool] = []
    thread = threading.Thread(target=lambda: result.append(lifecycle.sweep()))
    thread.start()
    assert stop_started.wait(timeout=1.0)

    health_calls_before = controller.health_calls
    status = lifecycle.status()
    assert status["phase"] == "stopping"
    assert status["running"] is True
    assert status["backend_ready"] is False
    assert controller.health_calls == health_calls_before

    allow_stop.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert result == [True]
    assert controller.stops == 1
    final_status = lifecycle.status(refresh=False)
    assert final_status["phase"] == "idle"
    assert final_status["running"] is False


def test_lifecycle_smoke_does_not_call_a_live_manager_disappeared_on_one_missed_probe() -> None:
    smoke = (ROOT / "tools" / "nanojev_lifecycle_smoke.py").read_text(encoding="utf-8")

    assert '"idle_wait_control_probe_retry"' in smoke
    assert 'if process.poll() is not None:' in smoke
    assert 'manager exited while waiting for backend idle unload' in smoke
    assert 'wait_for_idle_unload(args, process, generation)' in smoke
