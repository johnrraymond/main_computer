from __future__ import annotations

import pytest

from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin


class _WorkerRoutesHarness(ViewportEnergyRoutesMixin):
    def _clean_hub_url(self, value: str, *, allow_empty: bool = False) -> str:
        clean = str(value or "").strip().rstrip("/")
        if not clean and not allow_empty:
            return ""
        return clean


def _seller_payload() -> dict[str, object]:
    return {
        "node_id": "idle-only-ui-worker-001",
        "endpoint": "http://127.0.0.1:8771",
        "model": "mock-ai-model-idle-only",
        "models": ["mock-ai-model-idle-only"],
        "credits_per_request": 5500123,
        "max_concurrency": 1,
        "availability": {
            "accept_paid_jobs": True,
            "only_when_idle": True,
            "idle_source": "windows_user_activity_v1",
        },
        "pricing": {
            "pricing_type": "fixed_per_call_v0",
            "credits_per_request": 5500123,
            "unit": "compute_credit",
        },
        "execution": {
            "mode": "worker_pull_v0",
            "max_concurrency": 1,
        },
        "capabilities": {
            "capabilities": ["chat.completions"],
        },
    }


def test_worker_settings_defaults_to_idle_only_enabled() -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
    )()

    settings = harness._sanitize_worker_settings({})

    assert settings["sellerOnlyWhenIdle"] is True
    assert settings["rentalOnlyWhenIdle"] is True


def test_worker_settings_preserves_idle_only_false_choice() -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
    )()

    settings = harness._sanitize_worker_settings({"sellerOnlyWhenIdle": False})

    assert settings["sellerOnlyWhenIdle"] is False
    assert settings["rentalOnlyWhenIdle"] is False


def test_worker_offer_registration_blocks_idle_only_when_windows_user_is_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "main_computer.viewport_routes_energy.collect_windows_user_activity",
        lambda: {
            "supported": True,
            "ok": True,
            "active": True,
            "reason": "unit-test-active",
            "active_session_count": 1,
            "connected_session_count": 1,
            "sessions": [],
            "idle_active_threshold_s": 300,
        },
    )

    with pytest.raises(ValueError, match="Only accept jobs when idle is enabled"):
        _WorkerRoutesHarness()._worker_registration_payload_from_ui(_seller_payload())


def test_worker_offer_registration_allows_idle_only_when_windows_user_is_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "main_computer.viewport_routes_energy.collect_windows_user_activity",
        lambda: {
            "supported": True,
            "ok": True,
            "active": False,
            "reason": "unit-test-idle",
            "active_session_count": 0,
            "connected_session_count": 1,
            "sessions": [],
            "idle_active_threshold_s": 300,
        },
    )

    payload = _WorkerRoutesHarness()._worker_registration_payload_from_ui(_seller_payload())

    availability = payload["capabilities"]["availability"]  # type: ignore[index]
    assert availability["only_when_idle"] is True
    assert availability["idle_verified"] is True
    assert availability["last_user_activity"]["active"] is False
