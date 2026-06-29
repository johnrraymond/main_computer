from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path

import pytest

from main_computer.hub import HubRegistry
from main_computer.exp_fdb_hub_state import ExperimentalFoundationDbRegistry
from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin
from main_computer.hub_plex_service import market_worker_offer_from_payload


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


def test_worker_auto_connect_network_drives_saved_selected_network() -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
    )()

    settings = harness._sanitize_worker_settings(
        {
            "selectedNetwork": "dev",
            "workerAutoConnectNetwork": "testnet",
            "workerRequestedRing": "3",
        }
    )

    assert settings["workerAutoConnectNetwork"] == "testnet"
    assert settings["selectedNetwork"] == "testnet"




def test_worker_settings_drops_legacy_configured_hubs_from_settings_payload() -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
    )()

    settings = harness._sanitize_worker_settings(
        {
            "selectedNetwork": "dev",
            "workerAutoConnectNetwork": "dev",
            "hubs": [
                {"name": "Main Computer Mainnet Hub", "url": "https://mainnet-hub.greatlibrary.io", "role": "use-provide"},
                {"name": "Main Computer Mainnet Hub", "url": "https://mainnet-hub.greatlibrary.io", "role": "use-provide"},
                {"name": "Friend Hub", "url": "https://friend-hub.local", "role": "use-only"},
            ],
        }
    )

    assert "hubs" not in settings


def test_worker_settings_save_migrates_legacy_hubs_out_of_persisted_file(tmp_path: Path) -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {
            "debug_root": tmp_path,
            "config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})(),
        },
    )()
    legacy_payload = {
        "selectedNetwork": "dev",
        "workerAutoConnectNetwork": "dev",
        "hubs": [
            {"name": "Main Computer Mainnet Hub", "url": "https://mainnet-hub.greatlibrary.io", "role": "use-provide"},
            {"name": "Main Computer Mainnet Hub", "url": "https://mainnet-hub.greatlibrary.io", "role": "use-provide"},
            {"name": "Main Computer Local Dev Hub", "url": "http://127.0.0.1:8871", "role": "use-provide"},
            {"name": "Friend Hub", "url": "https://friend-hub.local", "role": "use-only"},
        ],
    }
    settings_path = tmp_path / "worker_settings.json"
    settings_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    loaded = harness._load_worker_settings()
    saved = harness._save_worker_settings({"hubs": legacy_payload["hubs"]}, changed_fields=["hubs"])

    assert "hubs" not in loaded
    assert "hubs" not in saved
    assert "hubs" not in json.loads(settings_path.read_text(encoding="utf-8"))


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
    assert payload["credits_per_request"] == "2"
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
    assert normalized["credits_per_request"] == "2"
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


def test_fdb_registry_worker_price_index_uses_wei_for_decimal_prices() -> None:
    registry = ExperimentalFoundationDbRegistry.__new__(ExperimentalFoundationDbRegistry)
    parts = registry._worker_index_parts(
        {
            "node_id": "fdb-decimal-worker-001",
            "worker_instance_id": "fdb-decimal-worker-001",
            "status": "available",
            "active_requests": 0,
            "max_concurrency": 1,
            "model": "mock-ai-model-idle-only",
            "models": ["mock-ai-model-idle-only"],
            "credits_per_request": "1.024",
            "credits_per_request_wei": "1024000000000000000",
            "capabilities": {
                "worker_network": "testnet",
                "requested_ring": "3",
                "pricing": {
                    "pricing_type": "approx_per_token_v0",
                    "credits_per_token": "0.001",
                    "credits_per_token_wei": "1000000000000000",
                    "target_output_tokens": 1024,
                    "estimated_credits_per_request": "1.024",
                    "estimated_credits_per_request_wei": "1024000000000000000",
                    "credits_per_request": "1.024",
                    "credits_per_request_wei": "1024000000000000000",
                },
            },
        }
    )

    assert parts
    assert parts[0][0] == "testnet"
    assert parts[0][1] == "3"
    assert parts[0][2] == "mock-ai-model-idle-only"
    assert parts[0][3] == 1_024_000_000_000_000_000



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
                "duration_seconds": 900,
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
                    "multisession_key_id": "msk-runtime-worker",
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

    assert policy["requirements"]["local_policy_normal_allowed"] is False


def test_worker_runtime_policy_work_now_override_allows_active_user_session(monkeypatch: pytest.MonkeyPatch) -> None:
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

    settings = harness._worker_set_work_now_override(_runtime_settings(harness), duration_seconds=7 * 60 * 60)
    policy = harness._worker_runtime_policy(settings)

    assert policy["allowed_to_accept"] is True
    assert policy["requirements"]["local_policy_allowed"] is True
    assert policy["requirements"]["local_policy_normal_allowed"] is False
    assert policy["requirements"]["work_now_override_active"] is True
    assert policy["local_policy"]["label"] == "Allowed by Work now"
    assert policy["workNowOverride"]["active"] is True
    assert "Work-now override" in policy["reason"]


def test_worker_runtime_clearing_work_now_override_returns_to_normal_policy_block(monkeypatch: pytest.MonkeyPatch) -> None:
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

    settings = harness._worker_set_work_now_override(_runtime_settings(harness), duration_seconds=15 * 60)
    settings = harness._worker_clear_work_now_override(settings)
    policy = harness._worker_runtime_policy(settings)

    assert policy["allowed_to_accept"] is False
    assert policy["workNowOverride"]["active"] is False
    assert policy["requirements"]["work_now_override_active"] is False
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
    assert "Local AI is busy" in blocked["reason"]
    assert blocked["local_policy"]["label"] == "Blocked"
    assert blocked["local_policy"]["reason"].startswith("Local AI is busy")

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



def test_worker_runtime_uses_live_session_instead_of_removed_heartbeat_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    captured: dict[str, object] = {}

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

    def fake_live_session(*, hub_url: str, worker_id: str, auth_message: dict[str, object]) -> dict[str, object]:
        captured["hub_url"] = hub_url
        captured["worker_id"] = worker_id
        captured["auth_message"] = auth_message
        return {
            "ok": True,
            "transport": "websocket",
            "endpoint": "/api/hub/v1/workers/live-session",
            "connection_id": "conn-test",
            "alive": True,
        }

    monkeypatch.setattr(harness, "_ensure_worker_live_session", fake_live_session)

    settings = dict(_runtime_settings(harness))
    signed = dict(settings["signedWorkerConnection"])  # type: ignore[index]
    worker = dict(signed["worker"])  # type: ignore[index]
    worker["multisession_key_id"] = "msk-worker-live-session-test"
    worker["wallet_address"] = signed["wallet_address"]
    worker["chain_id"] = signed["chain_id"]
    signed["worker"] = worker
    settings["signedWorkerConnection"] = signed

    saved, status = harness._worker_runtime_transition(settings, action="sync", send_heartbeat=True)

    auth_message = captured["auth_message"]  # type: ignore[assignment]
    assert saved["workerRuntimeLastHeartbeatStatus"] == "available"
    assert status["runtime"]["heartbeat_result"]["endpoint"] == "/api/hub/v1/workers/live-session"
    assert status["runtime"]["heartbeat_result"]["transport"] == "websocket"
    assert captured["hub_url"] == "http://127.0.0.1:8770"
    assert captured["worker_id"] == "runtime-worker-001"
    assert auth_message["type"] == "worker.auth"  # type: ignore[index]
    assert auth_message["worker_id"] == "runtime-worker-001"  # type: ignore[index]
    assert auth_message["market"]["rings"] == ["ring-3"]  # type: ignore[index]
    assert auth_message["market"]["price"]["amount"] == "1.024"  # type: ignore[index]
    assert auth_message["multisession_authorization"]["multisession_key_id"] == "msk-worker-live-session-test"  # type: ignore[index]


def test_worker_runtime_closes_live_session_when_not_accepting(tmp_path: Path) -> None:
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
    captured: dict[str, object] = {}

    def fake_close(*, hub_url: str, worker_id: str, reason: str) -> dict[str, object]:
        captured["hub_url"] = hub_url
        captured["worker_id"] = worker_id
        captured["reason"] = reason
        return {
            "ok": True,
            "transport": "websocket",
            "endpoint": "/api/hub/v1/workers/live-session",
            "closed_by_runtime": True,
        }

    harness._close_worker_live_session = fake_close  # type: ignore[method-assign]

    result = harness._post_worker_runtime_heartbeat_to_hub(
        hub_url="http://127.0.0.1:8770",
        settings=_runtime_settings(harness),
        phase="not_accepting",
        hub_status="offline",
        active_jobs=0,
        policy={"allowed_to_accept": False},
    )

    assert result["endpoint"] == "/api/hub/v1/workers/live-session"
    assert captured == {
        "hub_url": "http://127.0.0.1:8770",
        "worker_id": "runtime-worker-001",
        "reason": "runtime_not_accepting",
    }



def test_worker_runtime_status_truth_accept_paid_jobs_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        lambda: {"supported": True, "ok": True, "active": False, "reason": "unit-test-idle", "sessions": []},
    )

    settings = dict(_runtime_settings(harness))
    settings["sellerEnabled"] = False
    settings["rentalEnabled"] = False

    _saved, status = harness._worker_runtime_transition(settings, action="sync", send_heartbeat=False)

    assert status["status"] == "not_accepting"
    assert status["statusLabel"] == "Not accepting"
    assert status["reason"] == "Accept paid jobs is off."
    assert status["next"] == "Turn on Accept paid jobs when you want this computer to work."
    assert status["localPolicy"]["label"] == "Blocked"


def test_worker_runtime_status_truth_worker_start_missing_but_local_policy_allowed(tmp_path: Path) -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {
            "debug_root": tmp_path,
            "config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})(),
            "signal": lambda *args, **kwargs: None,
            "chat_ai_processes": type(
                "ChatAI",
                (),
                {
                    "local_ai_capacity_snapshot": lambda self, *, thread_id="", max_local_concurrency=1: {
                        "ok": True,
                        "available_now": True,
                        "busy": False,
                        "reason_code": "local_ai_available",
                        "user_message": "AI is idle.",
                        "active_run_count": 0,
                        "active_runs": [],
                    }
                },
            )(),
        },
    )()

    settings = dict(_runtime_settings(harness))
    settings["sellerAvailabilityMode"] = "ai_idle"
    settings["sellerOnlyWhenIdle"] = False
    settings["rentalOnlyWhenIdle"] = False
    settings["workerRegisteredId"] = ""
    settings["workerHubRegistration"] = {}
    settings["signedWorkerConnection"] = {
        "network": "dev",
        "requested_ring": "3",
        "wallet_address": "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf",
        "credit_wallet": "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf",
        "hub_url": "http://127.0.0.1:8770",
        "chain_id": "42424242",
        "message": "",
        "signature": "",
        "status": "unsigned",
    }

    _saved, status = harness._worker_runtime_transition(settings, action="sync", send_heartbeat=False)

    assert status["status"] == "not_accepting"
    assert status["reason"] == "Worker has not been registered with the Hub."
    assert status["next"] == "Work now."
    assert status["signedOrder"]["label"] == "Not started"
    assert status["hubRegistration"]["label"] == "Not submitted"
    assert status["localPolicy"]["label"] == "Allowed"
    assert status["localPolicy"]["reason"] == "AI is idle."
    assert status["runtime"]["label"] == "Not accepting"


def test_worker_runtime_status_truth_hub_registration_missing_is_separate_from_worker_start(tmp_path: Path) -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {
            "debug_root": tmp_path,
            "config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})(),
            "signal": lambda *args, **kwargs: None,
            "chat_ai_processes": type(
                "ChatAI",
                (),
                {
                    "local_ai_capacity_snapshot": lambda self, *, thread_id="", max_local_concurrency=1: {
                        "ok": True,
                        "available_now": True,
                        "busy": False,
                        "reason_code": "local_ai_available",
                        "user_message": "AI is idle.",
                        "active_run_count": 0,
                        "active_runs": [],
                    }
                },
            )(),
        },
    )()

    settings = dict(_runtime_settings(harness))
    settings["sellerAvailabilityMode"] = "ai_idle"
    settings["sellerOnlyWhenIdle"] = False
    settings["rentalOnlyWhenIdle"] = False
    settings["workerRegisteredId"] = ""
    settings["workerHubRegistration"] = {}
    signed = dict(settings["signedWorkerConnection"])
    signed["status"] = "ready"
    signed["worker_start_status"] = "ready"
    signed["signed_order_status"] = "ready"
    signed["hub_registration_status"] = "not_submitted"
    signed["hub_registered"] = False
    signed.pop("worker_id", None)
    signed.pop("worker", None)
    settings["signedWorkerConnection"] = signed

    _saved, status = harness._worker_runtime_transition(settings, action="sync", send_heartbeat=False)

    assert status["status"] == "not_accepting"
    assert status["reason"] == "Worker registration has not been submitted to the Hub."
    assert status["signedOrder"]["label"] == "Ready"
    assert status["hubRegistration"]["label"] == "Not submitted"
    assert status["localPolicy"]["label"] == "Allowed"



def test_worker_runtime_status_truth_hub_registration_failed_after_worker_start(tmp_path: Path) -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {
            "debug_root": tmp_path,
            "config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})(),
            "signal": lambda *args, **kwargs: None,
            "chat_ai_processes": type(
                "ChatAI",
                (),
                {
                    "local_ai_capacity_snapshot": lambda self, *, thread_id="", max_local_concurrency=1: {
                        "ok": True,
                        "available_now": True,
                        "busy": False,
                        "reason_code": "local_ai_available",
                        "user_message": "AI is idle.",
                        "active_run_count": 0,
                        "active_runs": [],
                    }
                },
            )(),
        },
    )()

    settings = dict(_runtime_settings(harness))
    settings["sellerAvailabilityMode"] = "ai_idle"
    settings["sellerOnlyWhenIdle"] = False
    settings["rentalOnlyWhenIdle"] = False
    settings["workerRegisteredId"] = ""
    settings["workerHubRegistration"] = {
        "status": "failed",
        "error": "Hub returned HTTP 400: bad worker price",
    }
    signed = dict(settings["signedWorkerConnection"])
    signed["status"] = "hub-registration-failed"
    signed["worker_start_status"] = "ready"
    signed["signed_order_status"] = "ready"
    signed["hub_registration_status"] = "failed"
    signed["hub_registered"] = False
    signed["hub_registration_error"] = "Hub returned HTTP 400: bad worker price"
    signed.pop("worker_id", None)
    signed.pop("worker", None)
    settings["signedWorkerConnection"] = signed

    _saved, status = harness._worker_runtime_transition(settings, action="sync", send_heartbeat=False)

    assert status["status"] == "not_accepting"
    assert status["signedOrder"]["label"] == "Ready"
    assert status["hubRegistration"]["label"] == "Failed"
    assert status["hubRegistration"]["lastError"] == "Hub returned HTTP 400: bad worker price"
    assert status["reason"] == "Hub registration failed: Hub returned HTTP 400: bad worker price"
    assert status["next"] == "Work now."
    assert status["runtime"]["lastError"] == "Hub returned HTTP 400: bad worker price"


def test_worker_start_persists_hub_registration_failure_state(tmp_path: Path) -> None:
    payload = _seller_payload()
    payload["availability"] = {
        "accept_paid_jobs": True,
        "availability_mode": "ai_idle",
        "only_when_idle": False,
        "idle_source": "local_ai_capacity_v1",
        "ai_idle_required": True,
    }

    class _StartWorkingHarness(_WorkerRoutesHarness):
        def __init__(self) -> None:
            self.client_address = ("127.0.0.1", 12345)
            self.sent_payload: dict[str, object] | None = None
            self.sent_status: HTTPStatus | None = None
            self.hub_payload: dict[str, object] | None = None
            self.saved_hub_registration_statuses: list[str] = []

        def _save_worker_settings(self, settings: dict[str, object], *, changed_fields: list[str] | None = None) -> dict[str, object]:
            saved = super()._save_worker_settings(settings, changed_fields=changed_fields)
            signed = saved.get("signedWorkerConnection") if isinstance(saved.get("signedWorkerConnection"), dict) else {}
            status = str(signed.get("hub_registration_status") or "")
            if status:
                self.saved_hub_registration_statuses.append(status)
            return saved

        def _read_json(self) -> dict[str, object]:
            return {
                "network": "dev",
                "requested_ring": "3",
                "duration_seconds": 900,
                "wallet_address": "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf",
                "hub_url": "http://127.0.0.1:8871",
                "chain_id": "42424242",
                "worker": payload,
            }

        def _worker_multisession_authorization_for_wallet(
            self,
            *,
            hub_url: str,
            wallet_address: str,
            chain_id: str = "",
            requested_key_id: str = "",
        ) -> dict[str, object]:
            return {
                "kind": "multisession_key",
                "wallet_address": wallet_address,
                "multisession_key_id": requested_key_id or "msk_test_start",
                "key_id": requested_key_id or "msk_test_start",
                "chain_id": chain_id,
            }

        def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
            self.sent_payload = payload
            self.sent_status = status

        def _post_worker_start_to_hub(self, *, hub_url: str, payload: dict[str, object]) -> dict[str, object]:
            self.hub_payload = payload
            raise RuntimeError("Hub returned HTTP 400: invalid literal for int() with base 10: '1.024'")

    harness = _StartWorkingHarness()
    harness.server = type(
        "Server",
        (),
        {
            "debug_root": tmp_path,
            "config": type("Config", (), {"hub_url": "http://127.0.0.1:8871"})(),
            "signal": lambda *args, **kwargs: None,
        },
    )()
    harness._save_worker_settings({"selectedNetwork": "dev", "sellerEnabled": True})

    harness._handle_worker_network_work_now()

    saved = harness._load_worker_settings()
    signed = saved["signedWorkerConnection"]
    assert harness.sent_status == HTTPStatus.BAD_REQUEST
    assert harness.sent_payload and harness.sent_payload["ok"] is False
    assert harness.hub_payload is not None
    assert "signed_connection" not in harness.hub_payload
    assert harness.hub_payload["multisession_authorization"]["key_id"] == "msk_test_start"  # type: ignore[index]
    assert harness.hub_payload["worker"]["credits_per_request"] == "2"  # type: ignore[index]
    assert harness.hub_payload["worker"]["pricing"]["credits_per_request"] == "1.024"  # type: ignore[index]
    assert signed.get("message", "") == ""
    assert signed.get("signature", "") == ""
    assert harness.saved_hub_registration_statuses[-3:] == ["not_submitted", "submitting", "failed"]
    assert signed["status"] == "hub-registration-failed"
    assert signed["signed_order_status"] == "ready"
    assert signed["hub_registration_status"] == "failed"
    assert signed["hub_registration_attempted_at"]
    assert signed["hub_registered"] is False
    assert "invalid literal for int()" in signed["hub_registration_error"]
    assert saved["workerHubRegistration"]["status"] == "failed"
    assert "invalid literal for int()" in saved["workerHubRegistration"]["error"]
    assert saved["workerConnectionStatus"] == "failed"



def test_worker_start_rejects_missing_multisession_key_before_hub_submit(tmp_path: Path) -> None:
    payload = _seller_payload()
    payload["availability"] = {
        "accept_paid_jobs": True,
        "availability_mode": "ai_idle",
        "only_when_idle": False,
        "idle_source": "local_ai_capacity_v1",
        "ai_idle_required": True,
    }

    class _StartWorkingHarness(_WorkerRoutesHarness):
        def __init__(self) -> None:
            self.client_address = ("127.0.0.1", 12345)
            self.sent_payload: dict[str, object] | None = None
            self.sent_status: HTTPStatus | None = None
            self.hub_called = False

        def _read_json(self) -> dict[str, object]:
            return {
                "network": "dev",
                "requested_ring": "3",
                "duration_seconds": 900,
                "wallet_address": "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf",
                "hub_url": "http://127.0.0.1:8871",
                "chain_id": "42424242",
                "worker": payload,
            }

        def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
            self.sent_payload = payload
            self.sent_status = status

        def _post_worker_start_to_hub(self, *, hub_url: str, payload: dict[str, object]) -> dict[str, object]:
            self.hub_called = True
            return {}

    harness = _StartWorkingHarness()
    harness.server = type(
        "Server",
        (),
        {
            "debug_root": tmp_path,
            "config": type("Config", (), {"hub_url": "http://127.0.0.1:8871"})(),
            "signal": lambda *args, **kwargs: None,
        },
    )()
    harness._save_worker_settings({"selectedNetwork": "dev", "sellerEnabled": True})

    harness._handle_worker_network_work_now()

    saved = harness._load_worker_settings()
    signed = saved["signedWorkerConnection"]
    assert harness.sent_status == HTTPStatus.BAD_REQUEST
    assert harness.hub_called is False
    assert signed["signed_order_status"] == "ready"
    assert signed["hub_registration_status"] == "failed"
    assert "No active saved multi-session key was found" in signed["hub_registration_error"]


def test_worker_runtime_status_truth_ai_busy_blocks_after_registration(tmp_path: Path) -> None:
    harness = _WorkerRoutesHarness()

    class _BusyChatAI:
        def local_ai_capacity_snapshot(self, *, thread_id: str = "", max_local_concurrency: int = 1) -> dict[str, object]:
            return {
                "ok": True,
                "available_now": False,
                "busy": True,
                "reason_code": "local_concurrency_exhausted",
                "user_message": "Local AI has no free slot right now.",
                "active_run_count": 1,
                "active_runs": [],
            }

    harness.server = type(
        "Server",
        (),
        {
            "debug_root": tmp_path,
            "config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})(),
            "signal": lambda *args, **kwargs: None,
            "chat_ai_processes": _BusyChatAI(),
        },
    )()

    settings = dict(_runtime_settings(harness))
    settings["sellerAvailabilityMode"] = "ai_idle"
    settings["sellerOnlyWhenIdle"] = False
    settings["rentalOnlyWhenIdle"] = False

    _saved, status = harness._worker_runtime_transition(settings, action="sync", send_heartbeat=False)

    assert status["status"] == "not_accepting"
    assert status["reason"].startswith("Local AI is busy.")
    assert status["next"] == "Wait until local AI work finishes."
    assert status["localPolicy"]["label"] == "Blocked"
    assert status["runtime"]["label"] == "Not accepting"


def test_worker_runtime_status_truth_registered_policy_allowed_accepting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        lambda: {"supported": True, "ok": True, "active": False, "reason": "unit-test-idle", "sessions": []},
    )

    _saved, status = harness._worker_runtime_transition(_runtime_settings(harness), action="sync", send_heartbeat=False)

    assert status["status"] == "accepting"
    assert status["statusLabel"] == "Accepting work"
    assert status["reason"] == "Hub registration accepted and local policy allows work."
    assert status["next"] == "Waiting for Hub job assignment."
    assert status["runtime"]["phase"] == "accepting"
    assert status["localPolicy"]["label"] == "Allowed"


def test_worker_runtime_status_truth_draining_with_active_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        lambda: {"supported": True, "ok": True, "active": False, "reason": "unit-test-idle", "sessions": []},
    )

    settings = dict(_runtime_settings(harness))
    settings["workerRuntimePhase"] = "accepting"
    settings["workerRuntimeActiveJobs"] = 1

    _saved, status = harness._worker_runtime_transition(settings, action="deactivate", send_heartbeat=False)

    assert status["status"] == "draining"
    assert status["statusLabel"] == "Finishing current work"
    assert status["reason"] == "The worker is draining and will disconnect after active work finishes."
    assert status["next"] == "Wait for the active job to finish."
    assert status["runtime"]["phase"] == "draining"

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


def test_worker_runtime_live_session_preserves_registered_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _WorkerRoutesHarness()
    harness.server = type(
        "Server",
        (),
        {"config": type("Config", (), {"hub_url": "http://127.0.0.1:8765"})()},
    )()

    captured: dict[str, object] = {}

    def fake_live_session(*, hub_url: str, worker_id: str, auth_message: dict[str, object]) -> dict[str, object]:
        captured["hub_url"] = hub_url
        captured["worker_id"] = worker_id
        captured["auth_message"] = auth_message
        return {
            "ok": True,
            "transport": "websocket",
            "endpoint": "/api/hub/v1/workers/live-session",
            "connection_id": "conn-test",
            "alive": True,
        }

    monkeypatch.setattr(harness, "_ensure_worker_live_session", fake_live_session)

    policy = {
        "allowed_to_accept": True,
        "user_activity": {"active": False, "reason": "unit-test-idle"},
    }
    settings = _runtime_settings(harness)
    signed = dict(settings["signedWorkerConnection"])  # type: ignore[index]
    worker = dict(signed["worker"])  # type: ignore[index]
    worker["multisession_key_id"] = "msk-worker-live-session-capabilities"
    worker["wallet_address"] = signed["wallet_address"]
    worker["chain_id"] = signed["chain_id"]
    signed["worker"] = worker
    settings["signedWorkerConnection"] = signed

    result = harness._post_worker_runtime_heartbeat_to_hub(
        hub_url="http://127.0.0.1:8770",
        settings=settings,
        phase="accepting",
        hub_status="available",
        active_jobs=0,
        policy=policy,
    )

    assert result["endpoint"] == "/api/hub/v1/workers/live-session"
    auth_message = captured["auth_message"]
    assert auth_message["worker_id"] == "runtime-worker-001"  # type: ignore[index]
    assert auth_message["status"] == "available"  # type: ignore[index]
    assert auth_message["max_concurrency"] == 1  # type: ignore[index]
    capabilities = auth_message["capabilities"]  # type: ignore[index]
    assert capabilities["pricing"]["credits_per_request"] == "1.024"  # type: ignore[index]
    assert capabilities["execution"]["mode"] == "worker_pull_v0"  # type: ignore[index]
    assert capabilities["assigned_ring"] == "3"  # type: ignore[index]
    assert capabilities["runtime"]["transport"] == "websocket"  # type: ignore[index]
    assert capabilities["runtime"]["live_session_endpoint"] == "/api/hub/v1/workers/live-session"  # type: ignore[index]
    assert capabilities["availability"]["availability_mode"] == "totally_idle"  # type: ignore[index]
    assert capabilities["availability"]["only_when_idle"] is True  # type: ignore[index]
    assert capabilities["availability"]["worker_runtime_phase"] == "accepting"  # type: ignore[index]


def test_market_worker_offer_from_payload_keeps_decimal_price_as_wei() -> None:
    worker = _seller_payload()
    offer = market_worker_offer_from_payload(worker)

    assert offer["credits_per_request"] == "1.024"
    assert offer["credits_per_request_wei"] == "1024000000000000000"
    assert offer["credits_per_request_display"] == "1.024"
    assert offer["offer_id"].startswith("offer_")

def test_worker_network_session_select_same_target_preserves_saved_setup(tmp_path: Path) -> None:
    class _NetworkSelectHarness(_WorkerRoutesHarness):
        def __init__(self) -> None:
            self.client_address = ("127.0.0.1", 12345)
            self.sent_payload: dict[str, object] | None = None
            self.sent_status: HTTPStatus | None = None
            self.closed_sessions: list[dict[str, object]] = []
            self.server = type(
                "Server",
                (),
                {
                    "debug_root": tmp_path,
                    "config": type("Config", (), {"hub_url": "http://127.0.0.1:8770"})(),
                    "signal": lambda *args, **kwargs: None,
                },
            )()

        def _read_json(self) -> dict[str, object]:
            return {"network": "dev", "requested_ring": "3"}

        def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
            self.sent_payload = payload
            self.sent_status = status

        def _worker_network_profiles_payload(self) -> list[dict[str, object]]:
            return [
                {
                    "network": "dev",
                    "network_key": "dev",
                    "display_name": "Dev",
                    "kind": "dev",
                    "chain_id": "42424242",
                    "chain_rpc_url": "http://127.0.0.1:18545",
                    "hub_url": "http://127.0.0.1:8770",
                    "hub_public_url": "http://127.0.0.1:8770",
                    "hub_bind_host": "127.0.0.1",
                    "hub_bind_port": 8770,
                    "deployment_manifest_path": "",
                }
            ]

        def _fetch_hub_status(self, hub_url: str) -> dict[str, object]:
            return {"reachable": True, "hub_url": hub_url}

        def _close_worker_live_session(self, **kwargs: object) -> dict[str, object]:
            self.closed_sessions.append(dict(kwargs))
            return {"ok": True}

    harness = _NetworkSelectHarness()
    settings = _runtime_settings(harness)
    settings["workerHubRegistration"] = {
        "status": "accepted",
        "worker": {"node_id": "runtime-worker-001", "worker_id": "runtime-worker-001"},
    }
    harness._save_worker_settings(settings)

    harness._handle_worker_network_session_select()

    saved = harness._load_worker_settings()
    assert harness.sent_status == HTTPStatus.OK
    assert harness.closed_sessions == []
    assert saved["selectedNetwork"] == "dev"
    assert saved["workerAutoConnectNetwork"] == "dev"
    assert saved["workerRegisteredId"] == "runtime-worker-001"
    assert saved["workerHubRegistration"]["status"] == "accepted"
    assert saved["signedWorkerConnection"]["wallet_address"] == "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
    assert saved["signedWorkerConnection"]["worker_id"] == "runtime-worker-001"
    assert saved["signedWorkerConnection"]["hub_registered"] is True


def test_worker_network_session_select_different_target_clears_saved_setup(tmp_path: Path) -> None:
    class _NetworkSelectHarness(_WorkerRoutesHarness):
        def __init__(self) -> None:
            self.client_address = ("127.0.0.1", 12345)
            self.sent_payload: dict[str, object] | None = None
            self.sent_status: HTTPStatus | None = None
            self.closed_sessions: list[dict[str, object]] = []
            self.server = type(
                "Server",
                (),
                {
                    "debug_root": tmp_path,
                    "config": type("Config", (), {"hub_url": "http://127.0.0.1:8770"})(),
                    "signal": lambda *args, **kwargs: None,
                },
            )()

        def _read_json(self) -> dict[str, object]:
            return {"network": "testnet", "requested_ring": "3"}

        def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
            self.sent_payload = payload
            self.sent_status = status

        def _worker_network_profiles_payload(self) -> list[dict[str, object]]:
            return [
                {
                    "network": "testnet",
                    "network_key": "testnet",
                    "display_name": "Testnet",
                    "kind": "testnet",
                    "chain_id": "42424241",
                    "chain_rpc_url": "",
                    "hub_url": "http://127.0.0.1:8781",
                    "hub_public_url": "http://127.0.0.1:8781",
                    "hub_bind_host": "127.0.0.1",
                    "hub_bind_port": 8781,
                    "deployment_manifest_path": "",
                }
            ]

        def _fetch_hub_status(self, hub_url: str) -> dict[str, object]:
            return {"reachable": True, "hub_url": hub_url}

        def _close_worker_live_session(self, **kwargs: object) -> dict[str, object]:
            self.closed_sessions.append(dict(kwargs))
            return {"ok": True}

    harness = _NetworkSelectHarness()
    settings = _runtime_settings(harness)
    settings["workerHubRegistration"] = {
        "status": "accepted",
        "worker": {"node_id": "runtime-worker-001", "worker_id": "runtime-worker-001"},
    }
    harness._save_worker_settings(settings)

    harness._handle_worker_network_session_select()

    saved = harness._load_worker_settings()
    assert harness.sent_status == HTTPStatus.OK
    assert saved["selectedNetwork"] == "testnet"
    assert saved["workerAutoConnectNetwork"] == "testnet"
    assert saved["workerRegisteredId"] == ""
    assert saved["workerHubRegistration"] == {}
    assert saved["signedWorkerConnection"]["wallet_address"] == ""
    assert saved["signedWorkerConnection"]["worker_id"] == ""
    assert len(harness.closed_sessions) == 1
    assert harness.closed_sessions[0]["reason"] == "worker_auto_connect_network_changed"

