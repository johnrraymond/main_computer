"""P5.5b: backend-owned Worker recovery after offer/transport disruptions.

Uses the P5.5a isolated Worker fixture and real production offer-sync logic,
WorkerRuntimeService.reconcile_worker_runtime, WorkerRuntimeSupervisor and live
session removal.  Hub registration and MSK store are recorded doubles: this is
not a mainnet, production FDB, or paid-work acceptance test.
"""
from __future__ import annotations

import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest

from main_computer.hub_interface import resolve_hub_destination
from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin, WorkerRuntimeService
from main_computer.worker_runtime_supervisor import WorkerRuntimeSupervisor
from test_worker_offer_lifecycle_p55a import _Worker


def _make_worker(tmp_path, monkeypatch, network="mainnet"):
    import main_computer.viewport_routes_energy as energy

    worker = _Worker(tmp_path, network)
    monkeypatch.setattr(energy, "collect_windows_user_activity", lambda: {
        "active": worker.user_active, "reason": "P5.5b isolated Windows activity",
    })
    return worker


def _supervisor(worker):
    """Real supervisor and offer-sync orchestration, bounded runtime display stub.

    The actual runtime heartbeat FSM is covered by test_worker_runtime_supervisor;
    here the status adapter is isolated to keep recovery tests free of sockets.
    """
    runtime = WorkerRuntimeService(worker.server)
    runtime._load_worker_settings = worker._load_worker_settings
    runtime._worker_offer_sync_pending = worker._worker_offer_sync_pending
    runtime._worker_runtime_transition = lambda settings, **kwargs: (
        settings,
        {"runtime": {"state": "RECONNECTING", "phase": "not_accepting",
                     "allowed_to_accept": False}},
    )
    return WorkerRuntimeSupervisor(runtime, interval_s=60)


@pytest.mark.parametrize("network", ["dev", "test", "testnet", "mainnet"])
def test_restarted_supervisor_synchronizes_pending_only_after_normal_idle(tmp_path, monkeypatch, network):
    worker = _make_worker(tmp_path, monkeypatch, network)
    worker.user_active = True
    assert worker.submit()[0]["status"] == "deferred"
    before = deepcopy(worker._load_worker_settings()["workerOfferIntent"])
    assert worker.outbound == []

    first = _supervisor(worker)
    first.reconcile_now(reason="simulated-disconnect", send_heartbeat=True)
    assert worker.outbound == []
    assert worker._load_worker_settings()["workerOfferIntent"] == before

    # A new supervisor instance represents an application restart. It reads the
    # saved intent, not a retained browser callback or in-memory pending queue.
    restarted = _supervisor(worker)
    worker.user_active = False
    restarted.reconcile_now(reason="startup-after-disconnect", send_heartbeat=True)
    assert len(worker.outbound) == 1
    assert worker.outbound[0][0] == resolve_hub_destination({"selectedNetwork": network}).hub_url
    assert worker.outbound[0][1]["worker"]["pricing"]["credits_per_token"] == "0.001"
    saved = worker._load_worker_settings()
    assert saved["workerOfferPending"] is False
    assert saved["workerRegisteredId"] == f"issued-{network}-1"
    assert saved["workerWorkNowOverrideExpiresAt"] == ""
    # Repeated supervisor ticks never duplicate an accepted registration.
    restarted.reconcile_now(reason="periodic", send_heartbeat=True)
    assert len(worker.outbound) == 1


@pytest.mark.parametrize("key_failure", ["expired", "revoked"])
def test_pending_offer_refuses_revoked_or_expired_msk_then_resumes_when_reauthorized(
    tmp_path, monkeypatch, key_failure,
):
    worker = _make_worker(tmp_path, monkeypatch)
    worker.user_active = True
    assert worker.submit()[0]["status"] == "deferred"
    worker.user_active = False
    authorize = worker._worker_multisession_authorization_for_wallet
    state = {"active": False}

    def checked_msk(**kwargs):
        if not state["active"]:
            raise ValueError(f"Hub-scoped MSK {key_failure}")
        return authorize(**kwargs)

    worker._worker_multisession_authorization_for_wallet = checked_msk
    supervisor = _supervisor(worker)
    supervisor.reconcile_now(reason=f"msk-{key_failure}", send_heartbeat=True)
    saved = worker._load_worker_settings()
    assert saved["workerOfferPending"] is True
    assert saved["workerOfferStatus"]["status"] == "pending"
    assert saved["workerRegisteredId"] == ""
    assert worker.outbound == []
    assert any(args and args[0] == "worker-offer-sync-error" for args, _ in worker.events)

    state["active"] = True
    supervisor.reconcile_now(reason="msk-renewed", send_heartbeat=True)
    assert len(worker.outbound) == 1
    assert worker._load_worker_settings()["workerOfferPending"] is False
    assert worker._load_worker_settings()["workerWorkNowOverrideDurationSeconds"] == 0


def test_pending_offer_cannot_cross_network_after_restart(tmp_path, monkeypatch):
    worker = _make_worker(tmp_path, monkeypatch, "dev")
    worker.user_active = True
    assert worker.submit()[0]["status"] == "deferred"
    old_intent = deepcopy(worker._load_worker_settings()["workerOfferIntent"])

    saved = worker._load_worker_settings()
    saved.update({"selectedNetwork": "mainnet", "workerAutoConnectNetwork": "mainnet"})
    worker._save_worker_settings(saved)
    worker.user_active = False
    _supervisor(worker).reconcile_now(reason="post-switch-startup")
    assert worker.outbound == []
    assert worker._load_worker_settings()["workerOfferIntent"] == old_intent
    assert worker._load_worker_settings()["workerOfferPending"] is True

    # Only a new explicit offer made for the new selection may authenticate.
    worker.network = "mainnet"
    worker.body = worker.offer("0.003")
    response, metadata = worker.submit()
    assert metadata == {} and response["status"] == "accepted"
    assert len(worker.outbound) == 1
    assert worker.outbound[0][0] == resolve_hub_destination({"selectedNetwork": "mainnet"}).hub_url
    assert worker.outbound[0][1]["worker"]["pricing"]["credits_per_token"] == "0.003"
    assert worker._load_worker_settings()["workerOfferPending"] is False


def test_failed_hub_registration_does_not_auto_retry_or_report_connected(tmp_path, monkeypatch):
    worker = _make_worker(tmp_path, monkeypatch)
    worker.hub_denies = True
    response, meta = worker.submit()
    assert response["ok"] is False and meta.get("status") == 400
    assert len(worker.outbound) == 1
    saved = worker._load_worker_settings()
    assert saved["workerOfferStatus"]["status"] == "failed"
    assert saved["signedWorkerConnection"]["hub_registered"] is False
    assert saved["workerRegisteredId"] == ""
    assert any(reason == "offer_authentication_failed" for _, _, reason in worker.closes)

    # A failed receipt requires an explicit user retry, not an unbounded
    # background attempt to open a new accepting socket on every heartbeat.
    worker.hub_denies = False
    _supervisor(worker).reconcile_now(reason="periodic-after-failure")
    assert len(worker.outbound) == 1
    assert worker._load_worker_settings()["workerOfferStatus"]["status"] == "failed"
    worker.body = worker.offer("0.002")
    assert worker.submit()[0]["status"] == "accepted"
    assert len(worker.outbound) == 2


def test_availability_change_during_auth_closes_new_session_and_defers(tmp_path, monkeypatch):
    worker = _make_worker(tmp_path, monkeypatch)
    original_post = worker._post_worker_start_to_hub

    attempts = {"count": 0}

    def become_busy_after_hub_accepts(**kwargs):
        result = original_post(**kwargs)
        attempts["count"] += 1
        if attempts["count"] == 1:
            worker.user_active = True
        return result

    worker._post_worker_start_to_hub = become_busy_after_hub_accepts
    response, meta = worker.submit()
    assert meta == {} and response["status"] == "deferred"
    assert len(worker.outbound) == 1
    assert any(reason == "offer_normal_availability_changed" for _, _, reason in worker.closes)
    saved = worker._load_worker_settings()
    assert saved["workerOfferPending"] is True
    assert saved["signedWorkerConnection"]["hub_registered"] is False
    assert saved["workerWorkNowOverrideExpiresAt"] == ""
    worker.user_active = False
    _supervisor(worker).reconcile_now(reason="policy-restored")
    assert len(worker.outbound) == 2
    assert worker._load_worker_settings()["workerOfferPending"] is False


def test_network_switch_during_authentication_closes_original_session_without_market_claim(tmp_path, monkeypatch):
    worker = _make_worker(tmp_path, monkeypatch, "dev")
    original_post = worker._post_worker_start_to_hub

    def change_network_after_hub_accepts(**kwargs):
        receipt = original_post(**kwargs)
        state = worker._load_worker_settings()
        state.update({"selectedNetwork": "mainnet", "workerAutoConnectNetwork": "mainnet"})
        worker._save_worker_settings(state)
        return receipt

    worker._post_worker_start_to_hub = change_network_after_hub_accepts
    result, metadata = worker.submit()
    assert result["ok"] is False and metadata.get("status") == 400
    assert len(worker.outbound) == 1
    assert worker.outbound[0][0] == resolve_hub_destination({"selectedNetwork": "dev"}).hub_url
    assert any(reason == "offer_authentication_failed" for _, _, reason in worker.closes)
    saved = worker._load_worker_settings()
    assert saved["workerRegisteredId"] == ""
    assert saved["signedWorkerConnection"]["hub_registered"] is False


def test_live_session_cleanup_removes_socket_when_idle_and_preserves_busy_socket(tmp_path):
    class Socket:
        def __init__(self, busy=False):
            self.has_active_work = busy
            self.closed = []

        def close(self, *, reason):
            self.closed.append(reason)

        def snapshot(self):
            return {"ok": True, "alive": not self.closed, "has_active_work": self.has_active_work}

    lock = threading.RLock()
    hub = "https://mainnet-hub.greatlibrary.io"
    wallet = "0x" + "a" * 40
    idle, busy = Socket(), Socket(busy=True)
    clients = {(hub, wallet): idle, (hub, "busy-worker"): busy}
    instance = object.__new__(ViewportEnergyRoutesMixin)
    instance._worker_live_session_clients = lambda: (lock, clients)

    closed = ViewportEnergyRoutesMixin._close_worker_live_session(
        instance, hub_url=hub, worker_id=wallet, reason="p55b-stale-cleanup",
    )
    assert closed["closed_by_runtime"] is True and closed["alive"] is False
    assert idle.closed == ["p55b-stale-cleanup"]
    assert (hub, wallet) not in clients

    deferred = ViewportEnergyRoutesMixin._close_worker_live_session(
        instance, hub_url=hub, worker_id="busy-worker", reason="p55b-active-job",
    )
    assert deferred["close_deferred"] is True
    assert (hub, "busy-worker") in clients
    assert busy.closed == []


@pytest.mark.parametrize("live", [None, False])
def test_supervisor_never_exposes_stale_connected_cache_as_accepting(tmp_path, live):
    runtime = SimpleNamespace(server=SimpleNamespace(signal=lambda *a, **k: None))
    supervisor = WorkerRuntimeSupervisor(runtime, interval_s=60)
    heartbeat = {} if live is None else {"live_session": {"alive": live}}
    supervisor.update_status({
        "runtime": {"state": "CONNECTED", "phase": "accepting", "allowed_to_accept": True,
                    "heartbeat_result": heartbeat},
        "runtimeDisplay": {"state": "CONNECTED", "center": "CONNECTED"},
    })
    status = supervisor.status(ensure_running=False)
    assert status["runtime"]["state"] == "RECONNECTING"
    assert status["runtime"]["allowed_to_accept"] is False
    assert status["runtime"]["missing_live_session"] is True
    assert status["runtimeDisplay"]["center"] == "RECONNECTING"


def test_registered_worker_reconnects_after_lost_heartbeat_without_duplicate_offer(tmp_path, monkeypatch):
    """Exercise the real runtime FSM, not the offer-sync display stub."""
    from test_worker_runtime_supervisor import _DummyServer, _complete_worker_settings

    server = _DummyServer(tmp_path)
    runtime = WorkerRuntimeService(server)
    runtime._save_worker_settings(_complete_worker_settings())
    attempts = []

    def heartbeat(**kwargs):
        attempts.append(kwargs["hub_url"])
        if len(attempts) == 1:
            raise ConnectionError("isolated live-session disconnected")
        return {"ok": True, "transport": "websocket", "live_session": {"alive": True, "active_work_count": 0}}

    monkeypatch.setattr(runtime, "_post_worker_runtime_heartbeat_to_hub", heartbeat)
    # The transport in this test is the heartbeat path, not registration. An
    # unrequested new market offer would be an authorization regression.
    monkeypatch.setattr(runtime, "_post_worker_start_to_hub", lambda **kw: pytest.fail("unexpected registration"))
    supervisor = WorkerRuntimeSupervisor(runtime, interval_s=60)

    disconnected = supervisor.reconcile_now(reason="transport-dropped")
    assert disconnected["runtime"]["state"] == "FAILED"
    assert disconnected["runtime"]["allowed_to_accept"] is False
    assert "isolated live-session disconnected" in disconnected["runtime"]["heartbeat_error"]
    assert runtime._load_worker_settings()["workerRegisteredId"] == "worker-1"

    reconnected = supervisor.reconcile_now(reason="reconnect-after-drop")
    assert reconnected["runtime"]["state"] == "CONNECTED"
    assert reconnected["runtime"]["heartbeat_result"]["live_session"]["alive"] is True
    assert attempts == ["http://127.0.0.1:8780", "http://127.0.0.1:8780"]
    assert runtime._load_worker_settings()["workerRegisteredId"] == "worker-1"
    assert runtime._load_worker_settings()["workerOfferPending"] is False
