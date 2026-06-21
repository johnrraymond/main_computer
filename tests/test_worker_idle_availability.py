from __future__ import annotations

import pytest

from main_computer.hub import HubRegistry
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
        "credits_per_token": "0.001",
        "credits_per_token_wei": "1000000000000000",
        "estimated_credits_per_request": "1.024",
        "estimated_credits_per_request_wei": "1024000000000000000",
        "credits_per_request": "1.024",
        "credits_per_request_wei": "1024000000000000000",
        "target_output_tokens": 1024,
        "max_concurrency": 1,
        "availability": {
            "accept_paid_jobs": True,
            "only_when_idle": True,
            "idle_source": "windows_user_activity_v1",
        },
        "pricing": {
            "pricing_type": "approx_per_token_v0",
            "credits_per_token": "0.001",
            "credits_per_token_wei": "1000000000000000",
            "target_output_tokens": 1024,
            "estimated_credits_per_request": "1.024",
            "estimated_credits_per_request_wei": "1024000000000000000",
            "credits_per_request": "1.024",
            "credits_per_request_wei": "1024000000000000000",
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
    assert settings["sellerCreditsPerToken"] == "0.001"
    assert settings["sellerTargetTokens"] == 1024
    assert settings["models"] == "gemma4:26b"


def test_worker_settings_migrates_old_visual_defaults_to_current_defaults() -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
    )()

    settings = harness._sanitize_worker_settings(
        {
            "models": "mock-ai-model-phase9",
            "creditsPerRequest": "5500123",
            "sellerTargetTokens": 1024,
        }
    )

    assert settings["models"] == "gemma4:26b"
    assert settings["sellerCreditsPerToken"] == "0.001"
    assert settings["sellerTargetTokens"] == 1024


def test_worker_settings_preserves_non_legacy_custom_model_and_price() -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
    )()

    settings = harness._sanitize_worker_settings(
        {
            "models": "llama3.2:3b",
            "sellerCreditsPerToken": "0.0025",
        }
    )

    assert settings["models"] == "llama3.2:3b"
    assert settings["sellerCreditsPerToken"] == "0.0025"


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

    assert payload["credits_per_token"] == "0.001"
    assert payload["credits_per_token_wei"] == "1000000000000000"
    assert payload["estimated_credits_per_request"] == "1.024"
    assert payload["estimated_credits_per_request_wei"] == "1024000000000000000"
    assert payload["credits_per_request"] == "1.024"
    assert payload["credits_per_request_wei"] == "1024000000000000000"
    assert payload["target_output_tokens"] == 1024
    assert payload["capabilities"]["pricing"]["pricing_type"] == "approx_per_token_v0"  # type: ignore[index]
    assert payload["capabilities"]["pricing"]["credits_per_token"] == "0.001"  # type: ignore[index]
    assert payload["capabilities"]["pricing"]["credits_per_token_wei"] == "1000000000000000"  # type: ignore[index]
    assert payload["capabilities"]["pricing"]["estimated_credits_per_request"] == "1.024"  # type: ignore[index]
    assert payload["capabilities"]["pricing"]["estimated_credits_per_request_wei"] == "1024000000000000000"  # type: ignore[index]
    assert payload["capabilities"]["pricing"]["credits_per_request"] == "1.024"  # type: ignore[index]
    assert payload["capabilities"]["pricing"]["credits_per_request_wei"] == "1024000000000000000"  # type: ignore[index]
    assert payload["capabilities"]["pricing"]["target_output_tokens"] == 1024  # type: ignore[index]
    assert payload["capabilities"]["target_output_tokens"] == 1024  # type: ignore[index]

    availability = payload["capabilities"]["availability"]  # type: ignore[index]
    assert availability["only_when_idle"] is True
    assert availability["idle_verified"] is True
    assert availability["last_user_activity"]["active"] is False


def test_worker_offer_registration_migrates_legacy_default_model_and_price(monkeypatch: pytest.MonkeyPatch) -> None:
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
    payload = _seller_payload()
    payload["model"] = "mock-ai-model-phase9"
    payload["models"] = ["mock-ai-model-phase9"]
    payload["credits_per_request"] = "5500123"
    payload["credits_per_request_wei"] = ""
    payload["pricing"] = {
        "pricing_type": "fixed_per_call_v0",
        "credits_per_request": "5500123",
        "target_output_tokens": 1024,
        "unit": "compute_credit",
    }

    normalized = _WorkerRoutesHarness()._worker_registration_payload_from_ui(payload)

    assert normalized["model"] == "gemma4:26b"
    assert normalized["models"] == ["gemma4:26b"]
    assert normalized["credits_per_token"] == "0.001"
    assert normalized["credits_per_token_wei"] == "1000000000000000"
    assert normalized["estimated_credits_per_request"] == "1.024"
    assert normalized["estimated_credits_per_request_wei"] == "1024000000000000000"
    assert normalized["credits_per_request"] == "1.024"
    assert normalized["credits_per_request_wei"] == "1024000000000000000"
    assert normalized["capabilities"]["pricing"]["pricing_type"] == "fixed_per_call_v0"  # type: ignore[index]
    assert normalized["capabilities"]["pricing"]["credits_per_token"] == "0.001"  # type: ignore[index]
    assert normalized["capabilities"]["pricing"]["credits_per_token_wei"] == "1000000000000000"  # type: ignore[index]
    assert normalized["capabilities"]["pricing"]["credits_per_request"] == "1.024"  # type: ignore[index]
    assert normalized["capabilities"]["pricing"]["credits_per_request_wei"] == "1024000000000000000"  # type: ignore[index]


def test_hub_registry_preserves_per_token_seller_offer_price(tmp_path) -> None:
    registry = HubRegistry(tmp_path, allow_insecure_dev_network=True)

    worker = registry.register_worker(
        node_id="per-token-price-worker-001",
        endpoint="http://127.0.0.1:8771",
        model="mock-ai-model-idle-only",
        models=["mock-ai-model-idle-only"],
        credits_per_request="1.024",
        capabilities={
            "pricing": {
                "pricing_type": "approx_per_token_v0",
                "credits_per_token": "0.001",
                "credits_per_token_wei": "1000000000000000",
                "target_output_tokens": 1024,
                "estimated_credits_per_request": "1.024",
                "estimated_credits_per_request_wei": "1024000000000000000",
                "unit": "compute_credit",
            }
        },
    )

    worker_data = worker.as_dict()
    assert worker_data["credits_per_request"] == "1.024"
    assert worker_data["capabilities"]["pricing"]["pricing_type"] == "approx_per_token_v0"
    assert worker_data["capabilities"]["pricing"]["credits_per_token"] == "0.001"
    assert worker_data["capabilities"]["pricing"]["credits_per_token_wei"] == "1000000000000000"
    assert worker_data["capabilities"]["pricing"]["credits_per_request"] == "1.024"
    assert worker_data["capabilities"]["pricing"]["credits_per_request_wei"] == "1024000000000000000"
    assert worker_data["capabilities"]["pricing"]["target_output_tokens"] == 1024

    offer = worker_data["offer"]
    assert offer["pricing_type"] == "approx_per_token_v0"
    assert offer["credits_per_token"] == "0.001"
    assert offer["credits_per_token_wei"] == "1000000000000000"
    assert offer["credits_per_token_display"] == "0.001"
    assert offer["estimated_credits_per_request"] == "1.024"
    assert offer["estimated_credits_per_request_wei"] == "1024000000000000000"
    assert offer["credits_per_request"] == "1.024"
    assert offer["credits_per_request_wei"] == "1024000000000000000"
    assert offer["credits_per_request_display"] == "1.024"
    assert offer["target_output_tokens"] == 1024

    status_worker = registry.status()["workers"][0]
    assert status_worker["credits_per_request"] == "1.024"
    assert status_worker["offer"]["pricing_type"] == "approx_per_token_v0"
    assert status_worker["offer"]["credits_per_token"] == "0.001"
    assert status_worker["offer"]["credits_per_token_wei"] == "1000000000000000"
    assert status_worker["offer"]["credits_per_request"] == "1.024"
    assert status_worker["offer"]["credits_per_request_wei"] == "1024000000000000000"
    assert status_worker["offer"]["target_output_tokens"] == 1024
