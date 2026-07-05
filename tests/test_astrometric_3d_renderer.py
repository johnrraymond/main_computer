from __future__ import annotations

from pathlib import Path

from main_computer.astrometric_renderer_service import AstrometricRendererService
from main_computer.viewport_route_dispatch import APPLICATION_ROUTE_NAMES, _application_route_target


def test_astrometric_application_route_registered():
    assert "astrometric" in APPLICATION_ROUTE_NAMES
    assert _application_route_target("/applications/astrometric") == "astrometric"
    assert _application_route_target("/apps/astrometric") == "astrometric"


def test_astrometric_renderer_service_contract_paths(tmp_path: Path):
    service = AstrometricRendererService(tmp_path)
    assert service.compose_file == tmp_path / "docker-compose.astrometric.yml"
    assert service.base_url == "http://127.0.0.1:8794"
    assert service._compose_command("up", "-d", "--build", "--force-recreate", "astrometric-renderer")[-5:] == [
        "up",
        "-d",
        "--build",
        "--force-recreate",
        "astrometric-renderer",
    ]


def test_astrometric_status_is_safe_without_compose_file(tmp_path: Path):
    service = AstrometricRendererService(tmp_path)
    status = service.status()
    assert status["ok"] is True
    assert status["compose_present"] is False
    assert status["renderer"]["base_url"] == "http://127.0.0.1:8794"
    assert status["stream_path"] == "/api/applications/astrometric/stream.mjpg"


def test_astrometric_renderer_health_distinguishes_reachable_from_stream_ready(tmp_path: Path, monkeypatch):
    service = AstrometricRendererService(tmp_path)

    class FakeResponse:
        status = 200
        content_type = "application/json"
        headers = {}
        body = b'{"ok":true,"frame_seq":0,"stream_ready":false,"gl_ready":true}'

    monkeypatch.setattr(service, "_renderer_request", lambda *args, **kwargs: FakeResponse())
    health = service.renderer_health()

    assert health["reachable"] is True
    assert health["stream_ready"] is False
    assert health["gl_ready"] is True


def test_astrometric_renderer_env_defaults_keep_startup_interactive(tmp_path: Path):
    service = AstrometricRendererService(tmp_path)
    env = service._renderer_env()

    assert env["ASTROMETRIC_RENDERER_WIDTH"] == "640"
    assert env["ASTROMETRIC_RENDERER_HEIGHT"] == "360"
    assert env["ASTROMETRIC_RENDERER_FPS"] == "10"
    assert env["ASTROMETRIC_RENDERER_IDLE_STEPS"] == "520"
    assert env["ASTROMETRIC_RENDERER_MOVING_STEPS"] == "220"


def test_astrometric_renderer_health_reports_tcp_probe_on_timeout(tmp_path: Path, monkeypatch):
    service = AstrometricRendererService(tmp_path)

    def raise_timeout(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(service, "_renderer_request", raise_timeout)
    monkeypatch.setattr(service, "_tcp_probe", lambda **kwargs: {"tcp_open": False, "tcp_error": "connection refused"})

    health = service.renderer_health()

    assert health["reachable"] is False
    assert health["stream_ready"] is False
    assert health["tcp_open"] is False
    assert "timed out" in health["error"]
