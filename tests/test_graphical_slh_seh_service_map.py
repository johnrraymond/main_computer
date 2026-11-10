"""Read-only /graphical SLH/SEH cards. No actual remote or SLH mutations."""
from pathlib import Path
from unittest.mock import patch

from main_computer.viewport_route_dispatch import (
    _control_panel_seh_service,
    _control_panel_slh_service,
)


def _local(condition, org=None):
    return condition, {
        "local_service": {"state": "running", "binding": "loopback", "port": 8897},
        "organization_service": org or {},
    }


def test_slh_running_uses_existing_verified_local_status():
    with patch("main_computer.mcel_slh_application._local_status", return_value=_local("RUNNING", {"state": "running"})) as probe:
        card = _control_panel_slh_service()
    assert card["id"] == "slh"
    assert card["state"] == "healthy"
    assert card["port"] == 8897
    assert card["required"] is False
    assert "organization running" in card["detail"]
    probe.assert_called_once_with(timeout=0.6)


def test_slh_stopped_by_policy_is_not_a_system_failure():
    with patch("main_computer.mcel_slh_application._local_status", return_value=("UNREACHABLE", {})):
        card = _control_panel_slh_service()
    assert card["state"] == "disabled"
    assert card["required"] is False
    assert "not running" in card["summary"]


def test_unverified_slh_identity_is_not_reported_as_healthy():
    with patch("main_computer.mcel_slh_application._local_status", return_value=("UNVERIFIED", {})):
        assert _control_panel_slh_service()["state"] == "unknown"


def test_seh_ready_with_tunnel_connected():
    with patch("main_computer.viewport_route_dispatch._control_panel_http_json", return_value={
        "ok": True, "data": {"service": "main_computer.seh", "live": True, "ready": True, "slh_connected": True},
    }) as probe:
        card = _control_panel_seh_service()
    assert card["id"] == "seh"
    assert card["state"] == "healthy"
    assert card["required"] is False
    probe.assert_called_once_with("https://external.greatlibrary.io/healthz", timeout_s=0.8)


def test_seh_live_but_unbound_is_degraded_not_down():
    with patch("main_computer.viewport_route_dispatch._control_panel_http_json", return_value={
        "ok": True, "data": {"service": "main_computer.seh", "live": True, "ready": False, "bound": False},
    }):
        card = _control_panel_seh_service()
    assert card["state"] == "degraded"


def test_seh_live_but_slh_disconnected_is_degraded():
    with patch("main_computer.viewport_route_dispatch._control_panel_http_json", return_value={
        "ok": True, "data": {"service": "main_computer.seh", "live": True, "ready": False, "slh_connected": False},
    }):
        card = _control_panel_seh_service()
    assert card["state"] == "degraded"
    assert "disconnected" in card["detail"]


def test_seh_explicit_live_false_is_down():
    with patch("main_computer.viewport_route_dispatch._control_panel_http_json", return_value={
        "ok": True, "data": {"service": "main_computer.seh", "live": False, "ready": False},
    }):
        assert _control_panel_seh_service()["state"] == "down"


def test_seh_unreachable_or_wrong_service_identity_is_unknown():
    for response in (
        {"ok": False, "error": "timeout"},
        {"ok": True, "data": {"service": "someone_else", "live": True, "ready": True, "slh_connected": True}},
        {"ok": True, "data": {"service": "main_computer.seh", "live": "yes", "ready": True}},
    ):
        with patch("main_computer.viewport_route_dispatch._control_panel_http_json", return_value=response):
            assert _control_panel_seh_service()["state"] == "unknown"


def test_seh_health_with_text_content_type_json_is_understood():
    with patch("main_computer.viewport_route_dispatch._control_panel_http_json", return_value={
        "ok": True, "data": '{"service":"main_computer.seh","live":true,"ready":true,"slh_connected":true}',
    }):
        assert _control_panel_seh_service()["state"] == "healthy"


def test_graphical_card_order_and_rendering_are_existing_contract():
    root = Path(__file__).resolve().parents[1]
    backend = (root / "main_computer/viewport_route_dispatch.py").read_text(encoding="utf-8")
    frontend = (root / "main_computer/web/graphical.html").read_text(encoding="utf-8")
    assert '"worker", "slh", "seh", "ollama"' in backend
    assert '_control_panel_slh_service(),' in backend
    assert '_control_panel_seh_service(),' in backend
    assert 'Secure Local Hub (SLH)</h3>' in frontend
    assert 'Secure External Hub (SEH)</h3>' in frontend
    assert 'function renderServices(payload)' in frontend
    assert 'payload.service_order' in frontend
