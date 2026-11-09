"""P4: local Viewport and heartbeat must fail closed before side effects."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

from main_computer import viewport_bind_policy as bind

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "::1", "localhost", "192.168.1.7", "", "127.0.0.2", "127.000.0.1", "127.0.0.1 "])
def test_native_viewport_rejects_every_noncanonical_bind(host: str, monkeypatch) -> None:
    monkeypatch.delenv("MAIN_COMPUTER_VIEWPORT_CONTAINER_INTERNAL_BIND", raising=False)
    with pytest.raises(ValueError, match="127.0.0.1"):
        bind.require_local_viewport_bind(host, container_internal=True)


def test_loopback_allowed_and_alt_port_unaffected() -> None:
    assert bind.require_local_viewport_bind("127.0.0.1") == "127.0.0.1"
    assert bind.require_local_viewport_bind("127.0.0.1", service="Heartbeat") == "127.0.0.1"


def test_container_exception_requires_both_explicit_optin_and_real_container_marker(tmp_path: Path, monkeypatch) -> None:
    marker = tmp_path / "dockerenv"
    monkeypatch.setattr(bind, "_CONTAINER_MARKERS", (marker,))
    monkeypatch.setattr(bind.sys, "platform", "linux")
    monkeypatch.delenv("MAIN_COMPUTER_VIEWPORT_CONTAINER_INTERNAL_BIND", raising=False)
    with pytest.raises(ValueError):
        bind.require_local_viewport_bind("0.0.0.0", container_internal=True)
    monkeypatch.setenv("MAIN_COMPUTER_VIEWPORT_CONTAINER_INTERNAL_BIND", "1")
    with pytest.raises(ValueError):
        bind.require_local_viewport_bind("0.0.0.0", container_internal=True)
    marker.touch()
    assert bind.require_local_viewport_bind("0.0.0.0", container_internal=True) == "0.0.0.0"
    with pytest.raises(ValueError):
        bind.require_local_viewport_bind("0.0.0.0", service="Heartbeat")
    monkeypatch.setattr(bind.sys, "platform", "win32")
    with pytest.raises(ValueError):
        bind.require_local_viewport_bind("0.0.0.0", container_internal=True)


def test_app_control_invalid_run_and_bootstrap_cannot_stop_existing_viewport(tmp_path: Path, monkeypatch) -> None:
    from main_computer import app_control

    events: list[str] = []
    monkeypatch.setattr(app_control, "stop_existing_viewport", lambda **kwargs: events.append("stopped"))
    monkeypatch.setattr(app_control, "replace_heartbeat", lambda **kwargs: events.append("replaced"))
    for action in ("run", "bootstrap"):
        with pytest.raises(ValueError, match="127.0.0.1"):
            if action == "run":
                app_control.run_app(root=tmp_path, host="0.0.0.0")
            else:
                app_control.bootstrap(root=tmp_path, host="192.168.1.2")
    assert events == []


def test_heartbeat_replacement_rejected_before_killing_listener(tmp_path: Path, monkeypatch) -> None:
    from main_computer import heartbeat

    config = heartbeat.HeartbeatConfig(workspace=tmp_path, bind_host="0.0.0.0")
    monkeypatch.setattr(heartbeat, "_heartbeat_pid", lambda _: (_ for _ in ()).throw(AssertionError("should never read PID")))
    with pytest.raises(ValueError, match="Heartbeat"):
        heartbeat.ensure_heartbeat_service(config, replace_existing=True)
    with pytest.raises(ValueError):
        heartbeat.serve(config)
    assert not config.heartbeat_pid_file.exists()


def test_viewport_direct_wrapper_refuses_before_heartbeat_start(monkeypatch) -> None:
    from main_computer import viewport

    monkeypatch.setattr(viewport, "ensure_heartbeat_service", lambda *a, **k: (_ for _ in ()).throw(AssertionError("side effect")))
    with pytest.raises(ValueError):
        viewport.serve(None, host="0.0.0.0", port=0)


def test_direct_server_constructors_reject_wildcard_before_socket_creation(tmp_path: Path) -> None:
    from main_computer.viewport_server import ViewportServer
    from main_computer.heartbeat import HeartbeatConfig, HeartbeatServer

    with pytest.raises(ValueError, match="127.0.0.1"):
        ViewportServer(("0.0.0.0", 0), None)
    with pytest.raises(ValueError, match="Heartbeat"):
        HeartbeatServer(("0.0.0.0", 0), HeartbeatConfig(workspace=tmp_path))


def test_power_shell_launchers_validate_before_side_effects() -> None:
    dev = (ROOT / "dev-control.ps1").read_text(encoding="utf-8-sig")
    control = (ROOT / "control-main-computer.ps1").read_text(encoding="utf-8-sig")
    assert '[string]$BindHost = "127.0.0.1"' in dev
    assert '[string]$BindHost = "127.0.0.1"' in control
    assert '$BindHost -cne "127.0.0.1"' in dev
    assert '$BindHost -cne "127.0.0.1"' in control
    assert dev.index('$BindHost -cne "127.0.0.1"') < dev.index('Get-DetectedModes')
    assert control.index('$BindHost -cne "127.0.0.1"') > control.index('if ($unexpectedRemainingArgs.Count -gt 0)')
    assert control.index('$BindHost -cne "127.0.0.1"') < control.index('$pidFile = Join-Path')


def test_direct_cli_and_server_have_final_bind_guard() -> None:
    cli = (ROOT / "main_computer/cli.py").read_text(encoding="utf-8")
    app = (ROOT / "main_computer/app_control.py").read_text(encoding="utf-8")
    server = (ROOT / "main_computer/viewport_server.py").read_text(encoding="utf-8")
    assert 'require_local_viewport_bind(args.host, container_internal=True)' in cli
    assert 'require_local_viewport_bind(args.host, service="Heartbeat")' in cli
    assert 'require_local_viewport_bind(host, container_internal=True)' in server
    assert 'require_local_viewport_bind(server_address[0], container_internal=True)' in server
    assert app.count('require_local_viewport_bind(host)') == 2


def test_docker_publishes_only_host_loopback_and_separates_internal_wildcard() -> None:
    compose = (ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:${MAIN_COMPUTER_DOCKER_VIEWPORT_PORT:-18765}:8765"' in compose
    assert 'MAIN_COMPUTER_VIEWPORT_CONTAINER_INTERNAL_BIND: "1"' in compose
    assert '"${MAIN_COMPUTER_DOCKER_VIEWPORT_PORT:-18765}:8765"' not in compose
    assert '- 0.0.0.0\n      - --port\n      - "8765"' in compose


def test_startup_hub_flags_are_unchanged() -> None:
    helper = (ROOT / "scripts/main-computer-start-stop.ps1").read_text(encoding="utf-8-sig")
    assert '[switch]$NoDevHub' in helper
    assert '[switch]$NoSLHHub' in helper
