from __future__ import annotations

import threading
from types import SimpleNamespace

from main_computer.config import MainComputerConfig
from main_computer.viewport_routes_energy import WorkerRuntimeService
from main_computer.worker_runtime_supervisor import WorkerRuntimeSupervisor


class _FakeChatAIProcesses:
    def local_ai_capacity_snapshot(self, **_kwargs):
        return {"ok": True, "available_now": True, "active_run_count": 0, "reason_code": "idle"}


class _DummyServer:
    def __init__(self, tmp_path):
        self.debug_root = tmp_path
        self.config = MainComputerConfig(workspace=tmp_path, hub_url="http://127.0.0.1:8780")
        self.computer = SimpleNamespace(provider=SimpleNamespace(name="unit", model="unit"))
        self.chat_ai_processes = _FakeChatAIProcesses()
        self.worker_runtime_lock = threading.RLock()
        self.signals: list[tuple[str, dict[str, object]]] = []

    def signal(self, name: str, **fields):
        self.signals.append((name, fields))


def _complete_worker_settings(*, multisession_key_id: str = "key-1") -> dict[str, object]:
    wallet = "0x" + "1" * 40
    hub_url = "http://127.0.0.1:8780"
    worker = {
        "worker_id": "worker-1",
        "node_id": "worker-1",
        "wallet_address": wallet,
        "credit_wallet": wallet,
        "capabilities": {"capabilities": ["chat.completions"]},
    }
    signed = {
        "network": "dev",
        "requested_ring": "3",
        "wallet_address": wallet,
        "credit_wallet": wallet,
        "hub_url": hub_url,
        "chain_id": "42424242",
        "status": "hub-registered",
        "worker_start_status": "ready",
        "signed_order_status": "ready",
        "hub_registration_status": "accepted",
        "hub_registered": True,
        "worker_id": "worker-1",
        "worker": worker,
    }
    if multisession_key_id:
        signed["multisession_key_id"] = multisession_key_id
    return {
        "selectedNetwork": "dev",
        "workerAutoConnectNetwork": "dev",
        "workerRequestedRing": "3",
        "workerConnectedHubUrl": hub_url,
        "workerConnectionStatus": "connected",
        "workerRegisteredId": "worker-1",
        "workerHubRegistration": {"status": "accepted", "worker": worker},
        "signedWorkerConnection": signed,
        "sellerEnabled": True,
        "rentalEnabled": True,
        "sellerAvailabilityMode": "ai_idle",
        "models": "gemma4:26b",
    }


def test_worker_runtime_supervisor_reconnects_complete_saved_setup_without_worker_page(tmp_path, monkeypatch) -> None:
    server = _DummyServer(tmp_path)
    service = WorkerRuntimeService(server)
    service._save_worker_settings(_complete_worker_settings())

    heartbeats: list[dict[str, object]] = []

    def fake_heartbeat(**kwargs):
        heartbeats.append(kwargs)
        return {
            "ok": True,
            "transport": "websocket",
            "live_session": {"alive": True, "active_work_count": 0},
        }

    monkeypatch.setattr(service, "_post_worker_runtime_heartbeat_to_hub", fake_heartbeat)

    supervisor = WorkerRuntimeSupervisor(service, interval_s=60)
    status = supervisor.reconcile_now(reason="unit-startup", send_heartbeat=True)

    assert heartbeats
    assert heartbeats[0]["hub_url"] == "http://127.0.0.1:8780"
    assert status["autoConnect"] == {"network": "dev", "enabled": True}
    assert status["runtime"]["state"] == "CONNECTED"
    assert status["runtimeDisplay"]["center"] == "CONNECTED"
    assert status["runtimeDisplay"]["foot"] == "Backend heartbeat active."


def test_worker_runtime_supervisor_reports_setup_when_multisession_key_is_missing(tmp_path, monkeypatch) -> None:
    server = _DummyServer(tmp_path)
    service = WorkerRuntimeService(server)
    service._save_worker_settings(_complete_worker_settings(multisession_key_id=""))

    def fail_if_heartbeat(**_kwargs):
        raise AssertionError("missing saved multi-session key must not attempt a live-session heartbeat")

    monkeypatch.setattr(service, "_post_worker_runtime_heartbeat_to_hub", fail_if_heartbeat)

    supervisor = WorkerRuntimeSupervisor(service, interval_s=60)
    status = supervisor.reconcile_now(reason="unit-startup", send_heartbeat=True)

    assert status["autoConnect"] == {"network": "dev", "enabled": True}
    assert status["runtime"]["state"] == "SETUP"
    assert status["runtimeDisplay"]["center"] == "SETUP"
    assert status["runtimeDisplay"]["nw"] == "Worker setup incomplete"
    assert "multisession_key" in status["runtime"]["setup"]["missing"]
    assert status["runtimeDisplay"]["foot"] == "Complete Worker setup once."


def test_worker_runtime_status_read_is_display_only(tmp_path, monkeypatch) -> None:
    server = _DummyServer(tmp_path)
    service = WorkerRuntimeService(server)
    service._save_worker_settings(_complete_worker_settings())

    def fail_if_heartbeat(**_kwargs):
        raise AssertionError("read-only runtime status must not reconnect or heartbeat")

    monkeypatch.setattr(service, "_post_worker_runtime_heartbeat_to_hub", fail_if_heartbeat)

    status = service.read_worker_runtime_status()

    assert status["autoConnect"] == {"network": "dev", "enabled": True}
    assert status["runtime"]["state"] == "RECONNECTING"
    assert status["runtime"]["allowed_to_accept"] is False
    assert status["runtime"]["heartbeat_result"] is None
    assert status["runtimeDisplay"]["center"] == "RECONNECTING"


class _FakeRuntimeForSupervisor:
    def __init__(self) -> None:
        self.server = _DummySignalServer()
        self.reconcile_event = threading.Event()
        self.reconcile_count = 0
        self.read_count = 0

    def _status(self, *, heartbeat_at: str = "2026-01-01T00:00:00+00:00") -> dict[str, object]:
        return {
            "ok": True,
            "autoConnect": {"network": "dev", "enabled": True},
            "runtimeDisplay": {
                "state": "CONNECTED",
                "center": "CONNECTED",
                "tone": "good",
                "nw": "Worker runtime connected",
                "ne": "Dev auto hub",
                "sw": "Heartbeat healthy",
                "se": "AI idle",
                "foot": "Backend heartbeat active.",
            },
            "runtime": {
                "state": "CONNECTED",
                "phase": "accepting",
                "enabled": True,
                "allowed_to_accept": True,
                "allowedToAccept": True,
                "active_jobs": 0,
                "activeJobs": 0,
                "lastHeartbeatAt": heartbeat_at,
                "last_heartbeat_at": heartbeat_at,
                "lastCheckedAt": heartbeat_at,
                "last_checked_at": heartbeat_at,
                "lastError": "",
                "heartbeat_result": {
                    "ok": True,
                    "transport": "websocket",
                    "live_session": {"alive": True, "active_work_count": 0},
                },
            },
        }

    def reconcile_worker_runtime(self, *, reason: str = "manual", send_heartbeat: bool = True) -> dict[str, object]:
        self.reconcile_count += 1
        self.reconcile_event.set()
        return self._status(heartbeat_at=f"2026-01-01T00:00:{self.reconcile_count:02d}+00:00")

    def read_worker_runtime_status(self) -> dict[str, object]:
        self.read_count += 1
        return self._status()


class _DummySignalServer:
    def __init__(self) -> None:
        self.signals: list[tuple[str, dict[str, object]]] = []

    def signal(self, name: str, **fields: object) -> None:
        self.signals.append((name, fields))


def test_worker_runtime_supervisor_status_autostarts_background_owner() -> None:
    runtime = _FakeRuntimeForSupervisor()
    supervisor = WorkerRuntimeSupervisor(runtime, interval_s=1)

    try:
        status = supervisor.status()
        assert runtime.reconcile_event.wait(2)
        diagnostics = supervisor.diagnostics()
        assert diagnostics["thread_alive"] is True
        assert diagnostics["loop_count"] >= 1
        assert runtime.reconcile_count >= 1
        assert status["supervisor"]["thread_alive"] is True
        assert any(name == "worker-runtime-supervisor-autostart" for name, _fields in runtime.server.signals)
        assert any(name == "worker-runtime-supervisor-reconcile" for name, _fields in runtime.server.signals)
    finally:
        supervisor.stop()


def test_worker_runtime_supervisor_start_can_publish_readiness_before_any_status_request() -> None:
    runtime = _FakeRuntimeForSupervisor()
    supervisor = WorkerRuntimeSupervisor(runtime, interval_s=60)

    try:
        supervisor.start(wait_for_initial_reconcile=True, wait_timeout_s=2)
        status = supervisor.status(ensure_running=False)

        assert runtime.reconcile_count >= 1
        assert status["runtime"]["state"] == "CONNECTED"
        assert status["runtime"]["phase"] == "accepting"
        assert status["runtime"]["heartbeat_result"]["live_session"]["alive"] is True
        assert status["supervisor"]["thread_alive"] is True
        assert any(name == "worker-runtime-supervisor-reconcile" for name, _fields in runtime.server.signals)
    finally:
        supervisor.stop()


def test_worker_runtime_supervisor_marks_stale_connected_cache_as_reconnecting() -> None:
    runtime = _FakeRuntimeForSupervisor()
    supervisor = WorkerRuntimeSupervisor(runtime, interval_s=1)
    supervisor.update_status(runtime._status(heartbeat_at="2026-01-01T00:00:00+00:00"))

    with supervisor._status_lock:
        supervisor._latest_status_at -= 120.0

    status = supervisor.status(ensure_running=False)

    assert status["runtime"]["state"] == "RECONNECTING"
    assert status["runtime"]["stale"] is True
    assert status["runtimeDisplay"]["center"] == "RECONNECTING"
    assert status["runtimeDisplay"]["sw"] == "Supervisor stale"
    assert status["supervisor"]["supervisor_stale"] is True


def test_worker_runtime_supervisor_reconciles_stale_connected_cache_on_status() -> None:
    runtime = _FakeRuntimeForSupervisor()
    supervisor = WorkerRuntimeSupervisor(runtime, interval_s=1)
    supervisor.update_status(runtime._status(heartbeat_at="2026-01-01T00:00:00+00:00"))

    with supervisor._status_lock:
        supervisor._latest_status_at -= 120.0

    supervisor.ensure_started = lambda reason="runtime-status": False  # type: ignore[method-assign]
    status = supervisor.status(ensure_running=True)

    assert runtime.reconcile_count == 1
    assert status["runtime"]["state"] == "CONNECTED"
    assert status["runtimeDisplay"]["center"] == "CONNECTED"


def test_worker_runtime_supervisor_marks_connected_cache_without_live_session_as_reconnecting() -> None:
    runtime = _FakeRuntimeForSupervisor()
    supervisor = WorkerRuntimeSupervisor(runtime, interval_s=1)
    status_without_live = runtime._status(heartbeat_at="2026-01-01T00:00:00+00:00")
    status_without_live["runtime"]["heartbeat_result"] = None
    supervisor.update_status(status_without_live)

    status = supervisor.status(ensure_running=False)

    assert status["runtime"]["state"] == "RECONNECTING"
    assert status["runtime"]["allowed_to_accept"] is False
    assert status["runtime"]["missing_live_session"] is True
    assert status["runtimeDisplay"]["center"] == "RECONNECTING"
    assert status["runtimeDisplay"]["sw"] == "Live session missing"
    assert status["supervisor"]["supervisor_stale"] is True


def test_worker_runtime_supervisor_fresh_success_exposes_stable_diagnostics_contract() -> None:
    runtime = _FakeRuntimeForSupervisor()
    supervisor = WorkerRuntimeSupervisor(runtime, interval_s=1)

    status = supervisor.reconcile_now(reason="contract", send_heartbeat=True)
    diagnostics = status["supervisor"]

    expected_keys = {
        "running",
        "thread_alive",
        "threadAlive",
        "interval_s",
        "intervalSeconds",
        "stale_after_s",
        "staleAfterSeconds",
        "started_at",
        "startedAt",
        "stopped_at",
        "stoppedAt",
        "last_attempt_at",
        "lastAttemptAt",
        "last_success_at",
        "lastSuccessAt",
        "last_error_at",
        "lastErrorAt",
        "last_error",
        "lastError",
        "last_reason",
        "lastReason",
        "loop_count",
        "loopCount",
        "success_count",
        "successCount",
        "error_count",
        "errorCount",
        "latest_status_at",
        "latestStatusAt",
        "latest_status_age_s",
        "latestStatusAgeSeconds",
        "supervisor_stale",
        "supervisorStale",
        "stale",
    }

    assert expected_keys <= set(diagnostics)
    assert status["runtime"]["supervisor"] == diagnostics
    assert diagnostics["successCount"] == 1
    assert diagnostics["errorCount"] == 0
    assert diagnostics["lastReason"] == "contract"
    assert diagnostics["lastAttemptAt"]
    assert diagnostics["lastSuccessAt"]
    assert diagnostics["supervisorStale"] is False
    assert status["runtime"]["state"] == "CONNECTED"
    assert status["runtimeDisplay"]["center"] == "CONNECTED"


def test_worker_runtime_status_response_is_compact_and_redacted_by_default(tmp_path) -> None:
    server = _DummyServer(tmp_path)
    service = WorkerRuntimeService(server)
    sensitive_wallet = "0x" + "a" * 40
    status = {
        "ok": True,
        "autoConnect": {"network": "dev", "enabled": True},
        "runtimeDisplay": {"center": "CONNECTED"},
        "status": "available",
        "statusLabel": "Available",
        "reason": "ok",
        "identity": {
            "wallet": sensitive_wallet,
            "creditWallet": sensitive_wallet,
            "workerId": "local-worker-001",
        },
        "signedOrder": {
            "wallet": sensitive_wallet,
            "multisession_key_id": "msk_secret",
        },
        "settings": {
            "signedWorkerConnection": {
                "wallet_address": sensitive_wallet,
                "multisession_key_id": "msk_secret",
            }
        },
        "localPolicy": {"allowed": True, "reason": "AI idle", "source": "local_ai_capacity_v1"},
        "worker": {"pricingPolicy": "fixed", "pool": {"wallet_address": sensitive_wallet}},
        "runtime": {
            "enabled": True,
            "state": "CONNECTED",
            "phase": "accepting",
            "allowed_to_accept": True,
            "allowedToAccept": True,
            "active_jobs": 0,
            "activeJobs": 0,
            "hub_status": "available",
            "hubAvailability": "available",
            "reason": "ok",
            "last_checked_at": "2026-01-01T00:00:00+00:00",
            "lastCheckedAt": "2026-01-01T00:00:00+00:00",
            "last_heartbeat_at": "2026-01-01T00:00:00+00:00",
            "lastHeartbeatAt": "2026-01-01T00:00:00+00:00",
            "heartbeat_result": {
                "ok": True,
                "transport": "websocket",
                "endpoint": "/api/hub/v1/workers/live-session",
                "worker": {
                    "status": "available",
                    "active_requests": 0,
                    "max_concurrency": 1,
                    "capabilities": {
                        "capabilities": ["chat.completions"],
                        "credit_wallet": sensitive_wallet,
                        "multisession_key_id": "msk_secret",
                        "worker_instance_id": "local-worker-001",
                    },
                },
                "live_session": {
                    "alive": True,
                    "active_work_count": 0,
                    "worker_instance_id": "conn_secret",
                    "accepted": {"connection_id": "conn_secret", "wallet_address": sensitive_wallet},
                    "last_offer": {"multisession_key_id": "msk_secret"},
                },
            },
            "policy": {
                "local_policy": {"allowed": True, "reason": "AI idle", "source": "local_ai_capacity_v1"},
                "signed_order": {"wallet": sensitive_wallet, "multisession_key_id": "msk_secret"},
            },
            "setup": {"state": "ready", "ready": True, "network": "dev", "missing": []},
        },
        "supervisor": {"thread_alive": True, "supervisorStale": False},
    }

    compact = service._worker_runtime_status_response_payload(status)
    compact_text = str(compact)

    assert "settings" not in compact
    assert "signedOrder" not in compact
    assert "identity" not in compact
    assert sensitive_wallet not in compact_text
    assert "msk_secret" not in compact_text
    assert "local-worker-001" not in compact_text
    assert "conn_secret" not in compact_text
    assert compact["runtime"]["heartbeat_result"]["live_session"]["alive"] is True
    assert compact["runtime"]["heartbeat_result"]["worker"]["capabilities"] == ["chat.completions"]
    assert compact["supervisor"]["thread_alive"] is True
