"""P5.5c: real, loopback Stable Hub WebSocket persistence acceptance.

The server is the production StableHubHTTPServer with in-memory MSK/worker stores;
clients use the production _WorkerHubLiveSessionClient through Viewport's existing
socket owner.  No deployed Hub, real FoundationDB, credits, or job execution.
"""
from __future__ import annotations

import socket
import time
from types import SimpleNamespace

import pytest

from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin, WorkerRuntimeService
from main_computer.worker_runtime_supervisor import WorkerRuntimeSupervisor
from main_computer.worker_live_session import _WorkerHubLiveSessionClient
from test_stable_hub_worker_live_session import _post_json, _signed_msk_request, _start_server


def _eventually(check, *, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.025)
    assert check(), "expected Stable Hub live-session transition did not occur"


@pytest.fixture
def live_hub():
    """Isolated production HTTP/WebSocket implementation, no production stores."""
    hub, server_thread = _start_server(
        "dev-hub1", InMemoryStableMultiSessionKeyStore(),
        InMemoryStableWorkerSessionStore(),
    )
    url = f"http://127.0.0.1:{hub.server_port}"
    route = object.__new__(ViewportEnergyRoutesMixin)
    route.server = SimpleNamespace()
    # A Worker offer is not dispatched in this persistence-only test. If the
    # Hub ever sends one, fail instead of faking a paid completion.
    def unexpected_offer(*args, **kwargs):
        raise AssertionError("unexpected paid job in persistent transport lab")
    route._execute_worker_live_session_offer = unexpected_offer
    route._cancel_worker_live_session_offer = unexpected_offer
    try:
        issued = _post_json(
            f"{url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": _signed_msk_request(request_id=f"p55c-{time.monotonic_ns()}")},
        )
        yield SimpleNamespace(
            hub=hub, url=url, route=route, key=issued["key"]["id"],
            wallet=issued["key"]["wallet_address"],
        )
    finally:
        lock, clients = route._worker_live_session_clients()
        with lock:
            owned = list(clients.values())
            clients.clear()
        for client in owned:
            client.close(reason="p55c_test_teardown")
        # Explicit shutdown first: Windows must not leave an HTTP handler
        # keeping a disposable fixture directory or socket open.
        hub.shutdown()
        hub.server_close()
        server_thread.join(timeout=3)
        assert not server_thread.is_alive()


def _auth(fixture, *, price="0.05", capacity=2):
    return {
        "type": "worker.auth",
        "execution_protocol": "durable-authorization-v1",
        "chain_id": "42424242",
        "model": "local-p55c-test-model",
        "models": ["local-p55c-test-model"],
        "multisession_authorization": {
            "kind": "multisession_key", "multisession_key_id": fixture.key,
            "wallet_address": fixture.wallet, "chain_id": "42424242",
        },
        "market": {
            "rings": ["ring-2"],
            "models": ["local-p55c-test-model"],
            "price": {"amount": price, "unit": "credit"},
            "capabilities": ["python"],
            "max_concurrency": capacity,
        },
    }


def _client(fixture):
    lock, clients = fixture.route._worker_live_session_clients()
    with lock:
        return clients[(fixture.url, "selected-worker")]


def _ensure(fixture, auth=None):
    return fixture.route._ensure_worker_live_session(
        hub_url=fixture.url, worker_id="selected-worker",
        auth_message=auth or _auth(fixture),
    )


def _hub_sessions(fixture):
    with fixture.hub._live_worker_sessions_lock:
        return dict(fixture.hub.live_worker_sessions)


def test_single_authenticated_websocket_persists_across_reconciles_and_keepalives(live_hub):
    first = _ensure(live_hub)
    client = _client(live_hub)
    connection_id = client.accepted["connection_id"]
    assert first["alive"] is True
    assert first["accepted"]["type"] == "hub.auth.accepted"
    assert client.accepted["market"]["price"]["amount"] == "0.05"
    assert client.websocket_upgrade_status.startswith("HTTP/1.1 101")
    assert client.last_pong["type"] == "hub.pong.accepted"

    for _ in range(3):
        # Real message over the same open WebSocket, not a mocked heartbeat.
        previous = client.last_pong
        client._send_keepalive()
        _eventually(lambda: client.last_pong is not previous)
        assert _ensure(live_hub)["alive"] is True
        assert _client(live_hub) is client
        assert set(_hub_sessions(live_hub)) == {connection_id}
    assert client.is_alive
    assert _hub_sessions(live_hub)[connection_id].snapshot()["status"] == "live"



def test_background_keepalive_thread_preserves_one_connection_without_browser(live_hub, monkeypatch):
    # Production keeps 10-second intervals. Shorten only this test's timer;
    # _keepalive_loop enforces a 2-second minimum between real socket sends.
    original_init = _WorkerHubLiveSessionClient.__init__
    def fast_test_init(self, **kwargs):
        original_init(self, **kwargs)
        self.keepalive_interval_s = 0.01
    monkeypatch.setattr(_WorkerHubLiveSessionClient, "__init__", fast_test_init)
    _ensure(live_hub)
    client = _client(live_hub)
    connection_id = client.accepted["connection_id"]
    assert client._keepalive_thread is not None and client._keepalive_thread.is_alive()
    prior = client.last_pong
    _eventually(lambda: client.last_pong is not prior)
    prior = client.last_pong
    _eventually(lambda: client.last_pong is not prior)
    assert client.is_alive
    assert _client(live_hub) is client
    assert set(_hub_sessions(live_hub)) == {connection_id}


def test_browserless_backend_connection_ignores_volatile_activity_snapshots(live_hub):
    _ensure(live_hub)
    client = _client(live_hub)
    changed = _auth(live_hub)
    changed.update(status="busy", queue_depth=4, active_requests=0)
    changed["capabilities"] = {"availability": {"last_user_activity": "active"}}
    assert _ensure(live_hub, changed)["alive"]
    assert _client(live_hub) is client
    assert len(_hub_sessions(live_hub)) == 1
    # No browser, JavaScript, or UI server is involved in this test.


def test_price_update_replaces_socket_and_closes_old_market_slot(live_hub):
    _ensure(live_hub)
    before = _client(live_hub)
    old_id = before.accepted["connection_id"]
    updated = _ensure(live_hub, _auth(live_hub, price="0.09"))
    after = _client(live_hub)
    new_id = after.accepted["connection_id"]
    assert updated["alive"] is True
    assert after is not before and new_id != old_id
    assert after.accepted["market"]["price"]["amount"] == "0.09"
    assert before.close_reason == "worker_live_session_replaced"
    _eventually(lambda: set(_hub_sessions(live_hub)) == {new_id})
    assert _hub_sessions(live_hub)[new_id].snapshot()["status"] == "live"
    _eventually(lambda: live_hub.hub.worker_market_directory.get_worker(old_id)["status"] == "closed")
    assert live_hub.hub.worker_market_directory.get_worker(new_id)["status"] == "live"


def test_active_job_defers_session_replacement_without_creating_second_socket(live_hub):
    _ensure(live_hub)
    client = _client(live_hub)
    original_id = client.accepted["connection_id"]
    try:
        with client._state_lock:
            client.active_work_count = 1  # simulate a locally active job; no paid job is submitted
        result = _ensure(live_hub, _auth(live_hub, price="0.09"))
        assert result["replacement_deferred"] is True
        assert result["replacement_deferred_reason"] == "active_live_session_work"
        assert _client(live_hub) is client
        assert set(_hub_sessions(live_hub)) == {original_id}
    finally:
        with client._state_lock:
            client.active_work_count = 0
    _ensure(live_hub, _auth(live_hub, price="0.09"))
    _eventually(lambda: original_id not in _hub_sessions(live_hub))


def test_forced_tcp_loss_is_detected_and_existing_owner_can_reconnect(live_hub):
    _ensure(live_hub)
    old = _client(live_hub)
    old_id = old.accepted["connection_id"]
    # Drop the actual server-side transport, rather than flipping a client flag.
    session = _hub_sessions(live_hub)[old_id]
    session._handler.connection.shutdown(socket.SHUT_RDWR)
    session._handler.connection.close()
    _eventually(lambda: not old.is_alive)
    _eventually(lambda: old_id not in _hub_sessions(live_hub))
    _ensure(live_hub)
    new = _client(live_hub)
    assert new is not old and new.is_alive
    assert new.accepted["connection_id"] != old_id
    assert len(_hub_sessions(live_hub)) == 1


def test_backend_supervisor_background_loop_reestablishes_real_socket_after_drop(live_hub):
    """Real supervisor thread, with normal availability fixed to eligible.

    The actual availability state machine is independently covered by P5.5a/b;
    this test verifies automatic transport ownership without a browser.
    """
    route = live_hub.route
    runtime = WorkerRuntimeService(route.server)
    def transition(settings, **kwargs):
        session = route._ensure_worker_live_session(
            hub_url=live_hub.url, worker_id="selected-worker",
            auth_message=_auth(live_hub),
        )
        return settings, {"runtime": {
            "state": "CONNECTED", "phase": "accepting",
            "heartbeat_result": {"live_session": session},
        }}
    runtime._load_worker_settings = lambda: {"workerOfferPending": False}
    runtime._worker_runtime_transition = transition
    supervisor = WorkerRuntimeSupervisor(runtime, interval_s=1)
    supervisor.start(wait_for_initial_reconcile=True, wait_timeout_s=8)
    try:
        _eventually(lambda: bool(route._worker_live_session_clients()[1]))
        old = _client(live_hub)
        old_id = old.accepted["connection_id"]
        session = _hub_sessions(live_hub)[old_id]
        session._handler.connection.shutdown(socket.SHUT_RDWR)
        session._handler.connection.close()
        _eventually(lambda: not old.is_alive)
        # No manual reconcile: the backend-owned thread notices the dead socket
        # on its ordinary periodic tick and authenticates a replacement.
        _eventually(lambda: (
            _client(live_hub) is not old
            and _client(live_hub).is_alive
            and _client(live_hub).accepted["connection_id"] != old_id
        ))
        assert supervisor.diagnostics()["success_count"] >= 2
    finally:
        supervisor.stop(timeout_s=8)
    assert not supervisor.diagnostics()["running"]


def test_normal_policy_not_accepting_closes_real_socket_without_reauth(live_hub):
    _ensure(live_hub)
    client = _client(live_hub)
    old_id = client.accepted["connection_id"]
    route = live_hub.route
    # Exercise the production policy's early exit, before any new auth payload.
    route._worker_runtime_worker_id = lambda settings: "selected-worker"
    result = route._post_worker_runtime_heartbeat_to_hub(
        hub_url=live_hub.url, settings={}, phase="not_accepting",
        hub_status="offline", active_jobs=0,
        policy={"allowed_to_accept": False},
    )
    assert result["closed_by_runtime"] is True
    assert not client.is_alive
    _eventually(lambda: not _hub_sessions(live_hub))
    assert old_id not in _hub_sessions(live_hub)
    _eventually(lambda: live_hub.hub.worker_market_directory.get_worker(old_id)["status"] == "closed")
    assert not route._worker_live_session_clients()[1]


def test_hub_rejects_unknown_msk_without_publishing_market_capacity(live_hub):
    auth = _auth(live_hub)
    auth["multisession_authorization"]["multisession_key_id"] = "msk_nonexistent_p55c"
    # The production client must not silently fall back to legacy REST.
    client = _WorkerHubLiveSessionClient(
        hub_url=live_hub.url, worker_id="selected-worker", auth_message=auth,
        timeout_s=2,
    )
    try:
        with pytest.raises(RuntimeError, match="Hub rejected worker.auth"):
            client.start()
        assert not _hub_sessions(live_hub)
        assert live_hub.hub.worker_market_directory.list_workers() == []
        assert not client.accepted
    finally:
        client.close(reason="invalid_msk_test_finished")
