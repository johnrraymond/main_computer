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
    assert status["runtimeDisplay"]["foot"] == "Worker is live."


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
    assert "multisession_key" in status["runtime"]["setup"]["missing"]
    assert status["runtimeDisplay"]["foot"] == "Open Worker setup once."


def test_worker_runtime_status_read_is_display_only(tmp_path, monkeypatch) -> None:
    server = _DummyServer(tmp_path)
    service = WorkerRuntimeService(server)
    service._save_worker_settings(_complete_worker_settings())

    def fail_if_heartbeat(**_kwargs):
        raise AssertionError("read-only runtime status must not reconnect or heartbeat")

    monkeypatch.setattr(service, "_post_worker_runtime_heartbeat_to_hub", fail_if_heartbeat)

    status = service.read_worker_runtime_status()

    assert status["autoConnect"] == {"network": "dev", "enabled": True}
    assert status["runtime"]["state"] in {"RECONNECTING", "CONNECTED"}
    assert status["runtimeDisplay"]["center"] in {"RECONNECTING", "CONNECTED"}
