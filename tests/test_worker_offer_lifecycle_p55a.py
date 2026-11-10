"""P5.5a: one Worker's authenticated offer across availability transitions.

Every Hub transport here is a recorded, isolated fake. The production handler,
settings persistence, canonical resolver, seller-offer normalization, ordinary
idle policy, and supervisor's pending-offer entry point are real. No paid work,
Hub contact, or runtime service is started by these tests.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.hub_interface import resolve_hub_destination
from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin, WorkerRuntimeService


_WALLET = "0x" + "a" * 40
_KEY = "isolated-p55a-msk"
_NETWORK_CHAIN = {"dev": "42424242", "test": "42424241", "testnet": "42424241", "mainnet": "42424240"}


class _Worker(ViewportEnergyRoutesMixin):
    def __init__(self, root: Path, network: str):
        self.network = network
        self.user_active = False
        self.active_jobs = 0
        self.hub_denies = False
        self.body: dict = {}
        self.responses: list = []
        self.outbound: list = []
        self.closes: list = []
        self.events: list = []
        self.server = SimpleNamespace(
            debug_root=root, config=SimpleNamespace(hub_url="http://127.0.0.1:8871"),
            signal=lambda *args, **kwargs: self.events.append((args, kwargs)),
        )
        self._save_worker_settings({
            "selectedNetwork": network, "workerAutoConnectNetwork": network,
            "workerRequestedRing": "3", "sellerEnabled": True, "rentalEnabled": True,
            "sellerAvailabilityMode": "totally-idle",
            "workerWorkNowOverrideStartedAt": "", "workerWorkNowOverrideExpiresAt": "",
            "workerWorkNowOverrideDurationSeconds": 0,
        })
        self.body = self.offer("0.001")

    def offer(self, price: str) -> dict:
        return {
            "hub_url": resolve_hub_destination({"selectedNetwork": self.network}).hub_url,
            "network": self.network, "chain_id": _NETWORK_CHAIN[self.network],
            "requested_ring": "3", "wallet_address": _WALLET,
            "active_multisession_key_id": _KEY,
            "worker": {
                "node_id": "p55a-worker", "model": "p55a-model", "models": ["p55a-model"],
                "pricing": {"credits_per_token": price, "target_output_tokens": 1024},
                "availability": {"accept_paid_jobs": True, "availability_mode": "totally-idle", "only_when_idle": True},
                "execution": {"mode": "worker_pull_v0", "max_concurrency": 1},
                "capabilities": {"capabilities": ["chat.completions"]},
            },
        }

    def _worker_ui_client_is_local(self):
        return True

    def _read_json(self):
        return self.body

    def _send_json(self, data, **kwargs):
        self.responses.append((data, kwargs))

    def _worker_network_session_payload(self, settings, *, check_hub=False):
        network = settings.get("selectedNetwork")
        return {"session": {"profile": {
            "chain_id": _NETWORK_CHAIN[network],
            "hub_url": resolve_hub_destination(settings).hub_url,
        }}}

    def _worker_multisession_authorization_for_wallet(self, *, hub_url, wallet_address, chain_id="", requested_key_id=""):
        if (hub_url != resolve_hub_destination(self._load_worker_settings()).hub_url
                or wallet_address != _WALLET or requested_key_id != _KEY):
            raise ValueError("Unauthorized Hub-scoped MSK")
        return {"key_id": _KEY, "wallet_address": wallet_address, "chain_id": chain_id}

    def _worker_live_session_active_work_count(self, *, hub_url=None, worker_id=None):
        return self.active_jobs

    def _close_worker_live_session(self, *, hub_url, worker_id, reason):
        self.closes.append((hub_url, worker_id, reason))
        return {"ok": True}

    def _post_worker_start_to_hub(self, *, hub_url, payload):
        self.outbound.append((hub_url, deepcopy(payload)))
        if self.hub_denies:
            raise RuntimeError("Isolated Hub declined Worker registration")
        network = payload["network"]
        return {
            "ok": True, "worker_id": f"issued-{network}-{len(self.outbound)}", "assigned_ring": "3",
            "transport": "websocket-live-session" if network in {"mainnet", "testnet"} else "dev-test-connect",
            "worker": {**payload["worker"], "worker_id": f"issued-{network}-{len(self.outbound)}", "assigned_ring": "3"},
            "pricing_policy": "isolated", "pool": {},
        }

    def submit(self):
        self.responses.clear()
        self._handle_worker_offer_sync()
        assert len(self.responses) == 1
        return self.responses[0]


@pytest.fixture
def factory(tmp_path, monkeypatch):
    import main_computer.viewport_routes_energy as energy

    current: list[_Worker | None] = [None]
    monkeypatch.setattr(energy, "collect_windows_user_activity", lambda: {
        "active": current[0].user_active if current[0] else None,
        "reason": "isolated p55a policy fixture",
    })

    def make(network: str):
        worker = _Worker(tmp_path / network, network)
        current[0] = worker
        return worker

    return make


@pytest.mark.parametrize("network", ["dev", "test", "testnet", "mainnet"])
def test_authenticated_offer_update_deferred_then_supervisor_reconciles(network, factory):
    worker = factory(network)
    expected_hub = resolve_hub_destination({"selectedNetwork": network}).hub_url

    first, meta = worker.submit()
    assert not meta and first["ok"] is True and first["status"] == "accepted"
    assert first["worker_id"] == f"issued-{network}-1"
    assert len(worker.outbound) == 1
    assert worker.outbound[0][0] == expected_hub
    assert worker.outbound[0][1]["multisession_authorization"]["key_id"] == _KEY
    assert worker.outbound[0][1]["worker"]["pricing"]["credits_per_token"] == "0.001"
    assert worker._load_worker_settings()["workerWorkNowOverrideExpiresAt"] == ""

    # A second offer replaces the market only after the existing session is
    # withdrawn, and may not register while Windows reports the user as active.
    worker.user_active = True
    worker.body = worker.offer("0.002")
    second, meta = worker.submit()
    assert not meta and second["ok"] is True and second["status"] == "deferred"
    assert len(worker.outbound) == 1
    assert any(reason == "offer_update_replace_old_market" for _, _, reason in worker.closes)
    saved = worker._load_worker_settings()
    assert saved["workerOfferPending"] is True
    assert saved["workerRegisteredId"] == ""
    assert saved["workerOfferIntent"]["worker"]["pricing"]["credits_per_token"] == "0.002"
    assert saved["workerWorkNowOverrideExpiresAt"] == ""
    assert saved["workerWorkNowOverrideDurationSeconds"] == 0

    # The supervisor (rather than the browser or Work now) owns replay once
    # ordinary availability permits accepting work again.
    worker.user_active = False
    supervisor = WorkerRuntimeService(worker.server)
    supervisor._worker_offer_sync_pending = worker._worker_offer_sync_pending
    supervisor._load_worker_settings = worker._load_worker_settings
    supervisor._worker_runtime_transition = lambda settings, **kwargs: (settings, {"runtime": {"phase": "accepting"}})
    status = supervisor.reconcile_worker_runtime(reason="p55a-offer-resume")
    assert status["runtime"]["phase"] == "accepting"
    assert len(worker.outbound) == 2
    assert worker.outbound[1][0] == expected_hub
    assert worker.outbound[1][1]["worker"]["pricing"]["credits_per_token"] == "0.002"
    assert worker.outbound[1][1]["multisession_authorization"]["key_id"] == _KEY
    saved = worker._load_worker_settings()
    assert saved["workerOfferPending"] is False
    assert saved["workerRegisteredId"] == f"issued-{network}-2"
    assert saved["workerOfferStatus"]["status"] == "accepted"
    assert saved["workerWorkNowOverrideExpiresAt"] == ""
    assert saved["workerWorkNowOverrideDurationSeconds"] == 0


@pytest.mark.parametrize("network", ["dev", "mainnet"])
def test_work_now_override_does_not_authorize_offer_sync(network, factory):
    worker = factory(network)
    worker.user_active = True
    worker._save_worker_settings({
        **worker._load_worker_settings(),
        "workerWorkNowOverrideStartedAt": "2026-11-09T00:00:00+00:00",
        "workerWorkNowOverrideExpiresAt": "2099-01-01T00:00:00+00:00",
        "workerWorkNowOverrideDurationSeconds": 900,
    })
    policy = worker._worker_runtime_local_policy(worker._load_worker_settings())
    assert policy["allowed"] is True and policy["normal_allowed"] is False
    response, metadata = worker.submit()
    assert not metadata and response["status"] == "deferred"
    assert worker.outbound == []
    saved = worker._load_worker_settings()
    assert saved["workerOfferPending"] is True
    assert saved["workerWorkNowOverrideExpiresAt"] == "2099-01-01T00:00:00+00:00"
    assert saved["workerWorkNowOverrideDurationSeconds"] == 900


@pytest.mark.parametrize("tamper", ["hub", "network", "chain", "msk"])
def test_foreign_identity_rejected_before_any_market_change(tamper, factory):
    worker = factory("dev")
    if tamper == "hub":
        worker.body["hub_url"] = resolve_hub_destination({"selectedNetwork": "mainnet"}).hub_url
    elif tamper == "network":
        worker.body["network"] = "mainnet"
    elif tamper == "chain":
        worker.body["chain_id"] = _NETWORK_CHAIN["mainnet"]
    else:
        worker.body["active_multisession_key_id"] = "other-network-key"
    response, metadata = worker.submit()
    assert response["ok"] is False and metadata.get("status") == 400
    assert worker.outbound == [] and worker.closes == []
    assert worker._load_worker_settings()["workerOfferPending"] is False


def test_worker_update_cannot_replace_market_during_active_job(factory):
    worker = factory("mainnet")
    assert worker.submit()[0]["status"] == "accepted"
    worker.active_jobs = 1
    worker.body = worker.offer("0.002")
    response, metadata = worker.submit()
    assert response["ok"] is False and metadata.get("status") == 400
    assert "Active Worker jobs" in response["error"]
    assert len(worker.outbound) == 1 and worker.closes == []
    assert worker._load_worker_settings()["workerRegisteredId"] == "issued-mainnet-1"


def test_rejected_hub_receipt_cannot_claim_updated_market(factory):
    worker = factory("testnet")
    worker.hub_denies = True
    response, metadata = worker.submit()
    assert response["ok"] is False and metadata.get("status") == 400
    assert len(worker.outbound) == 1
    assert any(reason == "offer_authentication_failed" for _, _, reason in worker.closes)
    saved = worker._load_worker_settings()
    assert saved["workerOfferPending"] is True
    assert saved["workerOfferStatus"]["status"] == "failed"
    assert saved["workerRegisteredId"] == ""
    assert saved["signedWorkerConnection"]["hub_registered"] is False


def test_pending_offer_cannot_follow_a_later_network_switch(factory):
    worker = factory("dev")
    worker.user_active = True
    assert worker.submit()[0]["status"] == "deferred"
    saved = worker._load_worker_settings()
    saved["selectedNetwork"] = "test"
    saved["workerAutoConnectNetwork"] = "test"
    worker._save_worker_settings(saved)
    worker.user_active = False

    # The normal supervisor catches the target mismatch; it may not silently
    # register a previously saved Dev offer against the newly selected Test Hub.
    supervisor = WorkerRuntimeService(worker.server)
    supervisor._worker_offer_sync_pending = worker._worker_offer_sync_pending
    supervisor._load_worker_settings = worker._load_worker_settings
    supervisor._worker_runtime_transition = lambda settings, **kwargs: (settings, {"runtime": {"phase": "not_accepting"}})
    supervisor.reconcile_worker_runtime(reason="p55a-network-switch")
    assert worker.outbound == []
    assert worker._load_worker_settings()["workerOfferPending"] is True
    assert any(args and args[0] == "worker-offer-sync-error" for args, _ in worker.events)


def test_disabled_seller_cannot_publish_offer_even_when_computer_idle(factory):
    worker = factory("mainnet")
    worker._save_worker_settings({**worker._load_worker_settings(), "sellerEnabled": False, "rentalEnabled": False})
    response, meta = worker.submit()
    assert not meta and response["ok"] is True and response["status"] == "deferred"
    assert worker.outbound == []
    assert worker._load_worker_settings()["workerOfferPending"] is True
    assert worker._load_worker_settings()["workerRegisteredId"] == ""
