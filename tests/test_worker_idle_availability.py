from __future__ import annotations

import json
from pathlib import Path

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

    assert settings["sellerAvailabilityMode"] == "totally_idle"
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


def test_worker_settings_migrates_idle_only_false_to_ai_idle_mode() -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
    )()

    settings = harness._sanitize_worker_settings({"sellerOnlyWhenIdle": False})

    assert settings["sellerAvailabilityMode"] == "ai_idle"
    assert settings["sellerOnlyWhenIdle"] is False
    assert settings["rentalOnlyWhenIdle"] is False


def test_worker_settings_preserves_ai_idle_availability_mode() -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
    )()

    settings = harness._sanitize_worker_settings({"sellerAvailabilityMode": "ai_idle", "sellerOnlyWhenIdle": True})

    assert settings["sellerAvailabilityMode"] == "ai_idle"
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

    with pytest.raises(ValueError, match="Only when totally idle is selected"):
        _WorkerRoutesHarness()._worker_registration_payload_from_ui(_seller_payload())


def test_worker_offer_registration_allows_ai_idle_mode_when_quser_reports_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "main_computer.viewport_routes_energy.collect_windows_user_activity",
        lambda: (_ for _ in ()).throw(AssertionError("ai_idle registration should not call quser")),
    )

    payload = _seller_payload()
    payload["availability"] = {
        "accept_paid_jobs": True,
        "availability_mode": "ai_idle",
        "only_when_idle": False,
        "idle_source": "local_ai_capacity_v1",
        "ai_idle_required": True,
    }

    normalized = _WorkerRoutesHarness()._worker_registration_payload_from_ui(payload)
    availability = normalized["capabilities"]["availability"]  # type: ignore[index]

    assert availability["availability_mode"] == "ai_idle"
    assert availability["only_when_idle"] is False
    assert availability["ai_idle_required"] is True



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
    assert availability["availability_mode"] == "totally_idle"
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



def _runtime_settings(harness: _WorkerRoutesHarness | None = None) -> dict[str, object]:
    harness = harness or _WorkerRoutesHarness()
    if not hasattr(harness, "server"):
        harness.server = type(
            "Server",
            (),
            {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
        )()
    settings = harness._sanitize_worker_settings(
        {
            "selectedNetwork": "dev",
            "sellerEnabled": True,
            "sellerAvailabilityMode": "totally_idle",
            "sellerOnlyWhenIdle": True,
            "workerConnectedHubUrl": "http://127.0.0.1:8770",
            "workerRegisteredId": "runtime-worker-001",
            "nodeId": "runtime-worker-001",
            "models": "gemma4:26b",
            "signedWorkerConnection": {
                "network": "dev",
                "requested_ring": "3",
                "wallet_address": "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf",
                "credit_wallet": "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf",
                "hub_url": "http://127.0.0.1:8770",
                "chain_id": "42424242",
                "message": "{}",
                "signature": "0xabc",
                "status": "hub-registered",
                "hub_registered": True,
                "worker_id": "runtime-worker-001",
                "worker": {
                    "node_id": "runtime-worker-001",
                    "worker_instance_id": "runtime-worker-001",
                    "capabilities": {
                        "capabilities": ["chat.completions"],
                        "pricing": {"credits_per_request": "1.024"},
                        "execution": {"mode": "worker_pull_v0"},
                        "assigned_ring": "3",
                    },
                },
            },
        }
    )
    return settings


def test_worker_runtime_policy_uses_quser_idle_status(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
    )()

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

    policy = harness._worker_runtime_policy(_runtime_settings(harness))

    assert policy["allowed_to_accept"] is True
    assert policy["source"] == "windows_quser_v1"
    assert policy["user_activity"]["active"] is False


def test_worker_runtime_policy_blocks_accepting_when_quser_reports_active(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
    )()

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

    policy = harness._worker_runtime_policy(_runtime_settings(harness))

    assert policy["allowed_to_accept"] is False
    assert "active interactive user session" in policy["reason"]


def test_worker_runtime_policy_ai_idle_mode_uses_local_ai_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _WorkerRoutesHarness()

    class _ChatAI:
        def __init__(self, busy: bool) -> None:
            self.busy = busy

        def local_ai_capacity_snapshot(self, *, thread_id: str = "", max_local_concurrency: int = 1) -> dict[str, object]:
            return {
                "ok": True,
                "scope": "local-ai",
                "available_now": not self.busy,
                "busy": self.busy,
                "reason_code": "local_concurrency_exhausted" if self.busy else "local_ai_available",
                "user_message": "Local AI has no free slot right now." if self.busy else "This chat can use local AI now.",
                "thread_id": thread_id,
                "active_run_count": 1 if self.busy else 0,
                "max_local_concurrency": max_local_concurrency,
                "active_thread_ids": ["chat"] if self.busy else [],
                "active_runs": [],
            }

    harness.server = type(
        "Server",
        (),
        {
            "config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})(),
            "chat_ai_processes": _ChatAI(False),
        },
    )()

    monkeypatch.setattr(
        "main_computer.viewport_routes_energy.collect_windows_user_activity",
        lambda: (_ for _ in ()).throw(AssertionError("ai_idle mode should not call quser")),
    )

    settings = _runtime_settings(harness)
    settings["sellerAvailabilityMode"] = "ai_idle"
    settings["sellerOnlyWhenIdle"] = False
    settings["rentalOnlyWhenIdle"] = False

    policy = harness._worker_runtime_policy(settings)

    assert policy["allowed_to_accept"] is True
    assert policy["source"] == "local_ai_capacity_v1"
    assert policy["availability_mode"] == "ai_idle"
    assert policy["user_activity"] is None
    assert policy["local_ai_capacity"]["available_now"] is True

    harness.server.chat_ai_processes = _ChatAI(True)

    blocked = harness._worker_runtime_policy(settings)

    assert blocked["allowed_to_accept"] is False
    assert "Local AI is not idle" in blocked["reason"]

    allowed_while_own_job_runs = harness._worker_runtime_policy(settings, active_jobs=1)

    assert allowed_while_own_job_runs["allowed_to_accept"] is True


def test_worker_runtime_transition_derives_enabled_from_seller_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {
            "debug_root": tmp_path,
            "config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})(),
            "signal": lambda *args, **kwargs: None,
        },
    )()

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

    settings = dict(_runtime_settings(harness))
    settings["workerRuntimeEnabled"] = False

    saved, status = harness._worker_runtime_transition(settings, action="sync", send_heartbeat=False)

    assert saved["workerRuntimeEnabled"] is True
    assert status["runtime"]["enabled"] is True
    assert status["runtime"]["phase"] == "accepting"

    saved["sellerEnabled"] = False
    saved["rentalEnabled"] = False
    saved, status = harness._worker_runtime_transition(saved, action="sync", send_heartbeat=False)

    assert saved["workerRuntimeEnabled"] is False
    assert status["runtime"]["enabled"] is False
    assert status["runtime"]["phase"] == "not_accepting"


def test_worker_runtime_transition_heartbeats_accepting_then_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {
            "debug_root": tmp_path,
            "config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})(),
            "signal": lambda *args, **kwargs: None,
        },
    )()

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
    heartbeats: list[dict[str, object]] = []

    def capture_heartbeat(**kwargs: object) -> dict[str, object]:
        heartbeats.append(dict(kwargs))
        return {"ok": True, "worker": {"node_id": "runtime-worker-001"}}

    monkeypatch.setattr(harness, "_post_worker_runtime_heartbeat_to_hub", capture_heartbeat)

    settings = dict(_runtime_settings(harness))
    settings["workerRuntimeEnabled"] = True

    saved, status = harness._worker_runtime_transition(settings, action="sync", send_heartbeat=True)

    assert status["runtime"]["phase"] == "accepting"
    assert status["runtime"]["hub_status"] == "available"
    assert saved["workerRuntimePhase"] == "accepting"
    assert heartbeats[-1]["hub_status"] == "available"

    saved, status = harness._worker_runtime_transition(saved, action="deactivate", send_heartbeat=True)

    assert status["runtime"]["phase"] == "not_accepting"
    assert status["runtime"]["hub_status"] == "offline"
    assert heartbeats[-1]["hub_status"] == "offline"


def test_worker_runtime_heartbeat_preserves_registered_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
    )()

    captured: dict[str, object] = {}

    class _FakeResponse:
        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true, "worker": {"node_id": "runtime-worker-001"}}'

    def fake_urlopen(request: object, timeout: float = 0.0) -> _FakeResponse:
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse()

    monkeypatch.setattr("main_computer.viewport_routes_energy.urlopen", fake_urlopen)

    policy = {
        "allowed_to_accept": True,
        "user_activity": {"active": False, "reason": "unit-test-idle"},
    }

    harness._post_worker_runtime_heartbeat_to_hub(
        hub_url="http://127.0.0.1:8770",
        settings=_runtime_settings(harness),
        phase="accepting",
        hub_status="available",
        active_jobs=0,
        policy=policy,
    )

    body = captured["body"]
    assert body["worker_node_id"] == "runtime-worker-001"
    assert body["status"] == "available"
    assert body["max_concurrency"] == 1
    capabilities = body["capabilities"]
    assert capabilities["pricing"]["credits_per_request"] == "1.024"
    assert capabilities["execution"]["mode"] == "worker_pull_v0"
    assert capabilities["assigned_ring"] == "3"
    assert capabilities["runtime"]["no_job_polling"] is True
    assert capabilities["availability"]["availability_mode"] == "totally_idle"
    assert capabilities["availability"]["only_when_idle"] is True
    assert capabilities["availability"]["worker_runtime_phase"] == "accepting"
