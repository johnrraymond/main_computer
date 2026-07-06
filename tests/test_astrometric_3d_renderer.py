from __future__ import annotations

from io import BytesIO
from pathlib import Path

from main_computer.astrometric_renderer_service import AstrometricRendererService
from main_computer.viewport_route_dispatch import APPLICATION_ROUTE_NAMES, _application_route_target
from main_computer.viewport_routes_astrometric import ViewportAstrometricRoutesMixin


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




def test_astrometric_docker_lifecycle_is_page_scoped(tmp_path: Path):
    service = AstrometricRendererService(tmp_path)
    command = service._compose_command("down", "--remove-orphans")

    assert "-p" in command
    assert command[command.index("-p") + 1] == "main-computer-astrometric"
    assert str(tmp_path / "docker-compose.astrometric.yml") in command


def test_astrometric_container_runtime_can_use_podman_for_compose_and_direct_calls(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MAIN_COMPUTER_CONTAINER_RUNTIME", "podman")
    service = AstrometricRendererService(tmp_path)

    compose = service._compose_command("ps", "-a")
    direct = service._docker_base()

    assert compose[:2] == ["podman", "compose"]
    assert direct == ["podman"]


def test_astrometric_compose_override_infers_podman_direct_command(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MAIN_COMPUTER_CONTAINER_RUNTIME", raising=False)
    monkeypatch.setenv("MAIN_COMPUTER_CONTAINER_COMPOSE_COMMAND", "podman-compose")
    service = AstrometricRendererService(tmp_path)

    assert service._docker_compose_base() == ["podman-compose"]
    assert service._docker_base() == ["podman"]


def test_astrometric_status_reports_container_runtime_without_breaking_docker_key(tmp_path: Path, monkeypatch):
    service = AstrometricRendererService(tmp_path)
    (tmp_path / "docker-compose.astrometric.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setenv("MAIN_COMPUTER_CONTAINER_RUNTIME", "podman")
    monkeypatch.setattr(service, "renderer_health", lambda **_kwargs: {"reachable": False, "stream_ready": False})
    monkeypatch.setattr(
        service,
        "_container_lifecycle",
        lambda: {
            "container": "main-computer-astrometric-renderer",
            "compose_project": "main-computer-astrometric",
            "controlled_by_page": True,
            "running": False,
            "state": "exited",
            "restart_policy": "no",
        },
    )

    def fake_run(*args, **kwargs):
        class Completed:
            returncode = 0
            stdout = "podman compose version 1.2.3"
            stderr = ""
        return Completed()

    monkeypatch.setattr("subprocess.run", fake_run)

    status = service.status()

    assert status["docker"]["runtime"] == "podman"
    assert status["docker"]["container_command"] == ["podman"]
    assert status["docker"]["compose_command"][:2] == ["podman", "compose"]
    assert status["container_runtime"]["runtime"] == "podman"
    assert status["docker"]["container"]["controlled_by_page"] is True


def test_astrometric_compose_does_not_auto_restart_renderer():
    repo_root = Path(__file__).resolve().parents[1]
    compose = (repo_root / "docker-compose.astrometric.yml").read_text(encoding="utf-8")

    assert "name: main-computer-astrometric" in compose
    assert 'restart: "no"' in compose
    assert "restart: unless-stopped" not in compose


def test_astrometric_status_reports_page_controlled_container_lifecycle(tmp_path: Path, monkeypatch):
    service = AstrometricRendererService(tmp_path)
    (tmp_path / "docker-compose.astrometric.yml").write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr(service, "renderer_health", lambda **_kwargs: {"reachable": False, "stream_ready": False})
    monkeypatch.setattr(service, "_docker_compose_base", lambda: ["docker", "compose"])

    def fake_run(*args, **kwargs):
        class Completed:
            returncode = 0
            stdout = "Docker Compose version v5.1.4"
            stderr = ""
        return Completed()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(
        service,
        "_container_lifecycle",
        lambda: {
            "container": "main-computer-astrometric-renderer",
            "compose_project": "main-computer-astrometric",
            "controlled_by_page": True,
            "running": False,
            "state": "exited",
            "restart_policy": "no",
        },
    )

    status = service.status()

    assert status["docker"]["container"]["controlled_by_page"] is True
    assert status["docker"]["container"]["compose_project"] == "main-computer-astrometric"
    assert status["docker"]["container"]["restart_policy"] == "no"


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
    assert env["ASTROMETRIC_RENDERER_IDLE_STEPS"] == "1900"
    assert env["ASTROMETRIC_RENDERER_MOVING_STEPS"] == "800"
    assert env["ASTROMETRIC_RENDERER_IDLE_STEP_LENGTH"] == "1.5e8"
    assert env["ASTROMETRIC_RENDERER_MOVING_STEP_LENGTH"] == "1.8e8"
    assert env["ASTROMETRIC_RENDERER_MODE"] == "gpu"
    assert env["ASTROMETRIC_RENDERER_BACKEND"] == "cuda"
    assert service._renderer_env({"ASTROMETRIC_RENDERER_MODE": "smoke"})["ASTROMETRIC_RENDERER_MODE"] == "smoke"


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


def test_astrometric_renderer_diagnostics_safe_without_compose_file(tmp_path: Path):
    service = AstrometricRendererService(tmp_path)
    diagnostics = service.diagnostics()

    assert diagnostics["ok"] is True
    assert diagnostics["diagnostics"]["compose_present"] is False
    assert diagnostics["status"]["compose_present"] is False
    assert any("start-smoke" in step for step in diagnostics["recommended_next_steps"])


def test_astrometric_start_smoke_uses_smoke_mode(tmp_path: Path, monkeypatch):
    service = AstrometricRendererService(tmp_path)
    (tmp_path / "docker-compose.astrometric.yml").write_text("services: {}\n", encoding="utf-8")
    calls = []

    def fake_run_compose(*args, **kwargs):
        calls.append((args, kwargs))
        return {"returncode": 0, "stdout": "", "stderr": "", "command": list(args)}

    monkeypatch.setattr(service, "_run_compose", fake_run_compose)
    monkeypatch.setattr(service, "_wait_for_renderer", lambda **kwargs: {"reachable": True, "stream_ready": True})
    monkeypatch.setattr(
        service,
        "status",
        lambda: {
            "ok": True,
            "renderer": {"reachable": True, "stream_ready": True, "renderer_mode": "smoke"},
            "compose_present": True,
        },
    )

    result = service.action("start-smoke")

    assert result["ok"] is True
    assert result["action"] == "start-smoke"
    assert calls[0][1]["env_overrides"]["ASTROMETRIC_RENDERER_MODE"] == "smoke"
    assert calls[0][1]["env_overrides"]["ASTROMETRIC_RENDERER_WIDTH"] == "480"




def test_astrometric_start_gpu_uses_cuda_backend(tmp_path: Path, monkeypatch):
    service = AstrometricRendererService(tmp_path)
    (tmp_path / "docker-compose.astrometric.yml").write_text("services: {}\n", encoding="utf-8")
    calls = []

    def fake_run_compose(*args, **kwargs):
        calls.append((args, kwargs))
        return {"returncode": 0, "stdout": "", "stderr": "", "command": list(args)}

    monkeypatch.setattr(service, "_run_compose", fake_run_compose)
    monkeypatch.setattr(service, "_wait_for_renderer", lambda **kwargs: {"reachable": True, "stream_ready": True})
    monkeypatch.setattr(
        service,
        "status",
        lambda: {
            "ok": True,
            "renderer": {
                "reachable": True,
                "stream_ready": True,
                "renderer_mode": "gpu",
                "renderer_backend": "cuda",
                "cuda_ready": True,
            },
            "compose_present": True,
        },
    )

    result = service.action("start-gpu")

    assert result["ok"] is True
    assert result["action"] == "start"
    assert calls[0][1]["env_overrides"]["ASTROMETRIC_RENDERER_MODE"] == "gpu"
    assert calls[0][1]["env_overrides"]["ASTROMETRIC_RENDERER_BACKEND"] == "cuda"


def test_astrometric_renderer_docker_build_uses_cuda_source():
    repo_root = Path(__file__).resolve().parents[1]
    cmake = (repo_root / "docker/astrometric-renderer/CMakeLists.txt").read_text(encoding="utf-8")
    dockerfile = (repo_root / "docker/astrometric-renderer/Dockerfile").read_text(encoding="utf-8")
    compose = (repo_root / "docker-compose.astrometric.yml").read_text(encoding="utf-8")
    cuda_source = repo_root / "docker/astrometric-renderer/src/astrometric_renderer.cu"

    assert cuda_source.exists()
    assert "LANGUAGES CXX CUDA" in cmake
    assert "src/astrometric_renderer.cu" in cmake
    assert "nvidia/cuda:" in dockerfile
    assert "ASTROMETRIC_RENDERER_BACKEND" in compose
    assert "ASTROMETRIC_RENDERER_IDLE_STEP_LENGTH:-1.5e8" in compose
    assert "ASTROMETRIC_RENDERER_MOVING_STEP_LENGTH:-1.8e8" in compose
    assert "compute,utility" in compose




def test_astrometric_cuda_source_includes_stdio_before_jpeglib():
    repo_root = Path(__file__).resolve().parents[1]
    cuda_text = (repo_root / "docker/astrometric-renderer/src/astrometric_renderer.cu").read_text(encoding="utf-8")
    stdio_index = cuda_text.index("#include <stdio.h>")
    jpeg_index = cuda_text.index("#include <jpeglib.h>")

    assert stdio_index < jpeg_index


def test_astrometric_cuda_defaults_make_black_hole_visible():
    repo_root = Path(__file__).resolve().parents[1]
    cuda_text = (repo_root / "docker/astrometric-renderer/src/astrometric_renderer.cu").read_text(encoding="utf-8")

    assert "float radius = 1.65e11f;" in cuda_text
    assert "float minRadius = 1.05e11f;" in cuda_text
    assert "float elevation = 1.10f;" in cuda_text
    assert "elevation = 1.10f;" in cuda_text
    assert "int idleSteps = 1900;" in cuda_text
    assert "int movingSteps = 800;" in cuda_text
    assert "float idleStepLength = 1.5e8f;" in cuda_text
    assert "float movingStepLength = 1.8e8f;" in cuda_text
    assert "params.diskInner = static_cast<float>(kSagittariusARs * 2.2);" in cuda_text
    assert "params.diskOuter = static_cast<float>(kSagittariusARs * 5.2);" in cuda_text
    assert "fabsf(oldPos.y - newPos.y) > params.diskThickness" not in cuda_text
    assert "oldPos + (newPos - oldPos) * segmentT" in cuda_text


def test_astrometric_stream_proxy_flushes_small_read1_chunks():
    class FakeUpstream:
        headers = {"Content-Type": "multipart/x-mixed-replace; boundary=frame"}

        def __init__(self):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, _size):
            raise AssertionError("stream proxy must not block on large read() calls")

        def read1(self, size):
            assert size == 4096
            self.calls += 1
            if self.calls == 1:
                return b"--frame\r\n"
            if self.calls == 2:
                return b"Content-Type: image/jpeg\r\n\r\nabc\r\n"
            return b""

    class FakeRenderer:
        def __init__(self):
            self.upstream = FakeUpstream()

        def open_stream(self):
            return self.upstream

    class FakeServer:
        def __init__(self):
            self.astrometric_renderer = FakeRenderer()
            self.signals = []

        def signal(self, name, **kwargs):
            self.signals.append((name, kwargs))

    class FakeHandler(ViewportAstrometricRoutesMixin):
        def __init__(self):
            self.server = FakeServer()
            self.wfile = BytesIO()
            self.status = None
            self.headers = []

        def send_response(self, status):
            self.status = status

        def send_header(self, name, value):
            self.headers.append((name, value))

        def end_headers(self):
            pass

        def _send_json(self, *_args, **_kwargs):
            raise AssertionError("stream success path should not send JSON")

    handler = FakeHandler()
    handler._handle_astrometric_stream()

    assert handler.status == 200
    assert ("Content-Type", "multipart/x-mixed-replace; boundary=frame") in handler.headers
    assert handler.wfile.getvalue() == b"--frame\r\nContent-Type: image/jpeg\r\n\r\nabc\r\n"
    assert handler.server.astrometric_renderer.upstream.calls == 3

def test_astrometric_viewport_has_no_center_reticle_overlay():
    repo_root = Path(__file__).resolve().parents[1]
    html = (repo_root / "main_computer/web/applications/apps/astrometric.html").read_text(encoding="utf-8")
    css = (repo_root / "main_computer/web/applications/styles/astrometric.css").read_text(encoding="utf-8")

    assert "astrometric-reticle" not in html
    assert ".astrometric-reticle" not in css
    assert "linear-gradient(90deg, transparent calc(50% - 0.5px)" not in css


def test_astrometric_cuda_uses_interpolated_disk_plane_hits_not_phi_texture():
    repo_root = Path(__file__).resolve().parents[1]
    cuda_text = (repo_root / "docker/astrometric-renderer/src/astrometric_renderer.cu").read_text(encoding="utf-8")

    assert "struct DiskHit" in cuda_text
    assert "__device__ DiskHit intersectDiskPlane" in cuda_text
    assert "float product = oldPos.y * newPos.y;" in cuda_text
    assert "if (!(product < 0.0f))" in cuda_text
    assert "oldNear" not in cuda_text
    assert "newNear" not in cuda_text
    assert "hit.angle = atan2f(pos.z, pos.x);" in cuda_text
    assert "sinf(diskHit.angle)" in cuda_text
    assert "ray.phi * 12.0f" not in cuda_text
    assert "float doppler =" not in cuda_text
    assert "repairCenterAxisSeams" not in cuda_text
    assert "repairCenterAxisSeams(hostRgba" not in cuda_text
    assert "float azimuth = 0.18f;" in cuda_text
    assert "azimuth = 0.18f;" in cuda_text
    assert "Vec3 background = skyColor(dir);" in cuda_text
    assert "color = mix3(background, diskColor, diskAlpha);" in cuda_text



def test_astrometric_cuda_default_camera_starts_outside_disk_annulus():
    repo_root = Path(__file__).resolve().parents[1]
    cuda_text = (repo_root / "docker/astrometric-renderer/src/astrometric_renderer.cu").read_text(encoding="utf-8")

    # The prior reset camera inherited the upstream demo radius, which places the
    # camera inside the rendered disk annulus once the CUDA renderer makes the
    # disk the final MJPEG image.  That produces the clipped foreground-sheet
    # view.  Keep the default/reset camera outside the outer disk.
    assert "float radius = 1.65e11f;" in cuda_text
    assert "radius = 1.65e11f;" in cuda_text
    assert "float minRadius = 1.05e11f;" in cuda_text
    assert "params.diskOuter = static_cast<float>(kSagittariusARs * 5.2);" in cuda_text
    assert "color = mix3(background, diskColor, diskAlpha);" in cuda_text
    assert "starting inside the accretion disk annulus" in cuda_text


def test_astrometric_stop_force_removes_fixed_container_after_compose_down(tmp_path: Path, monkeypatch):
    service = AstrometricRendererService(tmp_path)
    (tmp_path / "docker-compose.astrometric.yml").write_text("services: {}\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run_direct(command, *, timeout=12.0, env_overrides=None):
        calls.append(command)
        joined = " ".join(command)
        if "inspect -f {{json .State}}" in joined:
            return {"command": command, "returncode": 1, "stderr": "No such container"}
        if "rm -f main-computer-astrometric-renderer" in joined:
            return {"command": command, "returncode": 0, "stdout": "main-computer-astrometric-renderer"}
        return {"command": command, "returncode": 0, "stdout": ""}

    monkeypatch.setattr(service, "_run_direct", fake_run_direct)
    monkeypatch.setattr(service, "_run_compose", lambda *args, **kwargs: {"command": service._compose_command(*args), "returncode": 0})
    monkeypatch.setattr(service, "renderer_health", lambda **kwargs: {"reachable": False, "stream_ready": False, "tcp_open": False})

    result = service.action("stop")

    assert result["ok"] is True
    assert result["result"]["force_remove_container"]["returncode"] == 0
    assert any(command[-3:] == ["rm", "-f", "main-computer-astrometric-renderer"] for command in calls)


def test_astrometric_stop_reports_lingering_renderer_port(tmp_path: Path, monkeypatch):
    service = AstrometricRendererService(tmp_path)
    (tmp_path / "docker-compose.astrometric.yml").write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr(service, "_run_compose", lambda *args, **kwargs: {"command": service._compose_command(*args), "returncode": 0})
    monkeypatch.setattr(service, "_try_docker", lambda *args, **kwargs: {"command": ["docker", *args], "returncode": 0})
    monkeypatch.setattr(service, "_container_lifecycle", lambda: {"running": False, "state": "not_created"})
    monkeypatch.setattr(service, "renderer_health", lambda **kwargs: {"reachable": True, "stream_ready": True, "tcp_open": True})

    result = service.action("stop")

    assert result["ok"] is False
    assert "renderer port is still reachable" in result["message"]


def test_astrometric_stop_detaches_mjpeg_with_blank_image():
    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "main_computer/web/applications/scripts/astrometric.js").read_text(encoding="utf-8")

    assert "const ASTROMETRIC_BLANK_IMAGE =" in script
    assert "astrometricStream.src = ASTROMETRIC_BLANK_IMAGE;" in script
    assert 'astrometricDetachStream("stopping renderer")' in script
