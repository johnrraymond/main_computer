from __future__ import annotations

import threading
import time

import pytest
from types import SimpleNamespace

from main_computer.chat_ai_subprocess import ActiveChatAIProcess, ChatAISubprocessManager
from main_computer.config import MainComputerConfig
from main_computer.viewport_routes_energy import _WorkerHubLiveSessionClient, ViewportEnergyRoutesMixin


class _FakeInlineComputer:
    def __init__(self, content: str) -> None:
        self.provider = SimpleNamespace(name="fake-provider", model="fake-model")

        self.content = content

    def chat_console_ai(self, source: str, attachments: list[object] | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            content=self.content,
            provider="fake-provider",
            model="fake-model",
            metadata={"saw_source": source, "attachment_count": len(attachments or [])},
        )


class _DummyWorkerRoutes(ViewportEnergyRoutesMixin):
    def __init__(self, tmp_path) -> None:
        self.server = SimpleNamespace(
            debug_root=tmp_path,
            config=MainComputerConfig(workspace=tmp_path),
            computer=_FakeInlineComputer("actual local model answer"),
            activity=None,
            chat_ai_processes=None,
        )


class _FakeCancellableProcess:
    pid = 5150

    def __init__(self) -> None:
        self.running = True
        self.terminated = False
        self.killed = False

    def poll(self):
        if self.running:
            return None
        return -15 if self.terminated else -9 if self.killed else 0

    def terminate(self) -> None:
        self.terminated = True
        self.running = False

    def kill(self) -> None:
        self.killed = True
        self.running = False

    def wait(self, timeout: float | None = None):
        self.running = False
        return self.poll()


def test_worker_live_session_client_uses_executor_result_instead_of_echoing_prompt() -> None:
    sent_messages: list[dict[str, object]] = []

    def executor(offer: dict[str, object]) -> dict[str, object]:
        return {
            "status": "success",
            "response": {
                "content": "actual executor response",
                "provider": "unit-test-provider",
                "model": "unit-test-model",
            },
        }

    client = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="worker-local-ai",
        auth_message={"type": "worker.auth", "worker_id": "worker-local-ai"},
        work_executor=executor,
    )
    client._send_json = sent_messages.append  # type: ignore[method-assign]

    client._handle_work_offer(
        {
            "type": "hub.work.offer",
            "session_id": "sess-local-ai",
            "run_id": "run-local-ai",
            "request_id": "req-local-ai",
            "work": {
                "capabilities": ["chat.completions"],
                "input": {"prompt": "echo me and this test should fail"},
                "messages": [{"role": "user", "content": "echo me and this test should fail"}],
                "model": "micro-agent-local",
            },
        }
    )

    deadline = time.monotonic() + 2.0
    while len(sent_messages) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert sent_messages[0]["type"] == "worker.work.accepted"
    assert len(sent_messages) >= 2
    result = sent_messages[1]
    assert result["type"] == "worker.work.result"
    response = result["result"]["response"]  # type: ignore[index]
    assert response["content"] == "actual executor response"  # type: ignore[index]
    assert response["content"] != "echo me and this test should fail"  # type: ignore[index]


def test_worker_live_session_route_executor_calls_local_ai_instead_of_echoing_prompt(tmp_path) -> None:
    routes = _DummyWorkerRoutes(tmp_path)
    result = routes._execute_worker_live_session_offer(
        {
            "type": "hub.work.offer",
            "session_id": "sess-route-local-ai",
            "run_id": "run-route-local-ai",
            "request_id": "req-route-local-ai",
            "worker_id": "worker-local-ai",
            "work": {
                "capabilities": ["chat.completions"],
                "input": {"prompt": "Do not echo this prompt."},
                "messages": [{"role": "user", "content": "Do not echo this prompt."}],
                "model": "micro-agent-local",
            },
        }
    )

    response = result["response"]
    assert response["content"] == "actual local model answer"
    assert response["content"] != "Do not echo this prompt."
    assert response["provider"] == "fake-provider"
    assert response["model"] == "fake-model"
    assert response["metadata"]["from_live_session"] is True
    assert response["metadata"]["saw_source"] == "Do not echo this prompt."
    assert result["local_ai"] is True



def test_worker_live_session_route_executor_uses_direct_chat_completion_subprocess_mode(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    routes = _DummyWorkerRoutes(tmp_path)
    captured: dict[str, object] = {}

    class _FakeSubprocessManager:
        def run(self, **kwargs):
            captured.update(kwargs)
            return {
                "response": {
                    "content": "direct worker model answer",
                    "provider": "unit-provider",
                    "model": "unit-model",
                    "metadata": {"direct_mode": True},
                }
            }

    routes.server.chat_ai_processes = _FakeSubprocessManager()
    monkeypatch.setenv("MAIN_COMPUTER_DISABLE_INLINE_TEST_PROVIDER", "1")

    result = routes._execute_worker_live_session_offer(
        {
            "type": "hub.work.offer",
            "session_id": "sess-route-direct",
            "run_id": "run-route-direct",
            "request_id": "req-route-direct",
            "worker_id": "worker-local-ai",
            "work": {
                "capabilities": ["chat.completions"],
                "input": {"prompt": "Reply with exactly five words."},
                "messages": [{"role": "user", "content": "Reply with exactly five words."}],
                "model": "micro-agent-local",
            },
        }
    )

    command = captured["command"]  # type: ignore[index]
    assert command["mode"] == "worker_live_session_chat_completion"  # type: ignore[index]
    assert command["mode"] != "chat_console_ai"  # type: ignore[index]
    assert command["source"] == "Reply with exactly five words."  # type: ignore[index]
    assert command["messages"] == [{"role": "user", "content": "Reply with exactly five words."}]  # type: ignore[index]
    assert captured["thread_id"] == "worker-live-session:sess-route-direct"
    assert captured["max_local_concurrency"] == 1

    response = result["response"]
    assert response["content"] == "direct worker model answer"
    assert response["metadata"]["direct_mode"] is True
    assert response["metadata"]["from_live_session"] is True



def test_worker_live_session_client_reports_executor_error_as_terminal_failure() -> None:
    sent_messages: list[dict[str, object]] = []

    def executor(offer: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("local model is not configured")

    client = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="worker-local-ai",
        auth_message={"type": "worker.auth", "worker_id": "worker-local-ai"},
        work_executor=executor,
    )
    client._send_json = sent_messages.append  # type: ignore[method-assign]

    client._handle_work_offer(
        {
            "type": "hub.work.offer",
            "session_id": "sess-local-ai-fails",
            "run_id": "run-local-ai-fails",
            "request_id": "req-local-ai-fails",
            "work": {
                "capabilities": ["chat.completions"],
                "input": {"prompt": "this should fail terminally"},
                "messages": [{"role": "user", "content": "this should fail terminally"}],
                "model": "micro-agent-local",
            },
        }
    )

    deadline = time.monotonic() + 2.0
    while len(sent_messages) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert sent_messages[0]["type"] == "worker.work.accepted"
    assert len(sent_messages) >= 2
    failure = sent_messages[1]
    assert failure["type"] == "worker.work.failed"
    assert failure["session_id"] == "sess-local-ai-fails"
    assert failure["request_id"] == "req-local-ai-fails"
    assert failure["error"]["status"] == "failed"  # type: ignore[index]
    assert failure["error"]["error_type"] == "RuntimeError"  # type: ignore[index]
    assert "local model is not configured" in failure["error"]["error"]  # type: ignore[index]
    assert client.snapshot()["active_work_count"] == 0


def test_worker_live_session_client_times_out_stuck_local_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_messages: list[dict[str, object]] = []

    def executor(offer: dict[str, object]) -> dict[str, object]:
        time.sleep(5.0)
        return {"status": "success", "response": {"content": "too late"}}

    monkeypatch.setenv("MAIN_COMPUTER_WORKER_LIVE_SESSION_LOCAL_AI_TIMEOUT_SECONDS", "0.05")
    client = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="worker-local-ai",
        auth_message={"type": "worker.auth", "worker_id": "worker-local-ai"},
        work_executor=executor,
    )
    client._send_json = sent_messages.append  # type: ignore[method-assign]

    client._handle_work_offer(
        {
            "type": "hub.work.offer",
            "session_id": "sess-local-ai-timeout",
            "run_id": "run-local-ai-timeout",
            "request_id": "req-local-ai-timeout",
            "work": {
                "capabilities": ["chat.completions"],
                "input": {"prompt": "timeout please"},
                "messages": [{"role": "user", "content": "timeout please"}],
                "model": "micro-agent-local",
            },
        }
    )

    deadline = time.monotonic() + 2.0
    while len(sent_messages) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert sent_messages[0]["type"] == "worker.work.accepted"
    assert len(sent_messages) >= 2
    failure = sent_messages[1]
    assert failure["type"] == "worker.work.failed"
    assert failure["error"]["error_type"] == "TimeoutError"  # type: ignore[index]
    assert "timed out" in failure["error"]["error"]  # type: ignore[index]
    assert client.snapshot()["active_work_count"] == 0


def test_worker_live_session_timeout_cancels_local_ai_capacity_slot(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent_messages: list[dict[str, object]] = []
    routes = _DummyWorkerRoutes(tmp_path)
    manager = ChatAISubprocessManager()
    routes.server.chat_ai_processes = manager
    process = _FakeCancellableProcess()
    started = threading.Event()
    thread_id = "worker-live-session:sess-local-ai-timeout-cancel"
    run_id = "run-local-ai-timeout-cancel"

    def executor(offer: dict[str, object]) -> dict[str, object]:
        manager._active_by_thread[thread_id] = ActiveChatAIProcess(
            thread_id=thread_id,
            run_id=run_id,
            process=process,  # type: ignore[arg-type]
            log_file=tmp_path / "worker-timeout-cancel.log",
            started_at=time.monotonic(),
        )
        started.set()
        while process.poll() is None:
            time.sleep(0.01)
        return {"status": "success", "response": {"content": "cancelled too late"}}

    client = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="worker-local-ai",
        auth_message={"type": "worker.auth", "worker_id": "worker-local-ai"},
        work_executor=executor,
        work_canceller=routes._cancel_worker_live_session_offer,
    )
    monkeypatch.setattr(client, "_work_offer_timeout_seconds", lambda offer: 0.05)
    client._send_json = sent_messages.append  # type: ignore[method-assign]

    client._handle_work_offer(
        {
            "type": "hub.work.offer",
            "session_id": "sess-local-ai-timeout-cancel",
            "run_id": run_id,
            "request_id": "req-local-ai-timeout-cancel",
            "work": {
                "capabilities": ["chat.completions"],
                "input": {"prompt": "timeout and cancel local AI"},
                "messages": [{"role": "user", "content": "timeout and cancel local AI"}],
                "model": "micro-agent-local",
            },
        }
    )

    assert started.wait(1.0)
    assert manager.local_ai_capacity_snapshot(thread_id="", max_local_concurrency=1)["busy"] is True

    deadline = time.monotonic() + 2.0
    while len(sent_messages) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert sent_messages[0]["type"] == "worker.work.accepted"
    assert len(sent_messages) >= 2
    failure = sent_messages[1]
    assert failure["type"] == "worker.work.failed"
    assert failure["error"]["error_type"] == "TimeoutError"  # type: ignore[index]
    assert process.terminated is True
    capacity = manager.local_ai_capacity_snapshot(thread_id="", max_local_concurrency=1)
    assert capacity["busy"] is False
    assert capacity["available_now"] is True
    assert capacity["active_run_count"] == 0
    assert client.snapshot()["active_work_count"] == 0


def test_worker_live_session_fingerprint_ignores_volatile_availability_snapshots() -> None:
    base_auth = {
        "type": "worker.auth",
        "worker_id": "local-worker-001",
        "worker_instance_id": "local-worker-001",
        "chain_id": "42424242",
        "model": "micro-agent-local",
        "models": ["micro-agent-local"],
        "capabilities": {
            "capabilities": ["chat.completions"],
            "availability": {
                "last_user_activity": {"idle_seconds": 1},
                "local_ai_capacity": {"active_run_count": 0},
            },
        },
        "market": {
            "rings": ["ring-3"],
            "capabilities": ["chat.completions"],
            "models": ["micro-agent-local"],
            "price": {"amount": "1.024", "unit": "compute_credit"},
            "active_sessions": 0,
            "max_concurrency": 1,
        },
        "multisession_authorization": {
            "kind": "multisession_key",
            "key_id": "msk_worker",
            "wallet_address": "0xworker",
            "chain_id": "42424242",
        },
    }
    changed_auth = {
        **base_auth,
        "capabilities": {
            "capabilities": ["chat.completions"],
            "availability": {
                "last_user_activity": {"idle_seconds": 99},
                "local_ai_capacity": {"active_run_count": 1},
            },
        },
        "market": {
            **base_auth["market"],
            "active_sessions": 1,
        },
    }

    first = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="local-worker-001",
        auth_message=base_auth,
    )
    second = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="local-worker-001",
        auth_message=changed_auth,
    )

    assert first.fingerprint == second.fingerprint


def test_worker_live_session_runtime_close_is_deferred_while_offer_work_active(tmp_path) -> None:
    routes = _DummyWorkerRoutes(tmp_path)
    client = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="local-worker-001",
        auth_message={"type": "worker.auth", "worker_id": "local-worker-001"},
    )
    client.active_work_count = 1

    key = ("http://127.0.0.1:8871", "local-worker-001")
    lock, clients = routes._worker_live_session_clients()
    with lock:
        clients[key] = client

    snapshot = routes._close_worker_live_session(
        hub_url="http://127.0.0.1:8871",
        worker_id="local-worker-001",
        reason="runtime_not_accepting",
    )

    assert snapshot["close_deferred"] is True
    assert snapshot["close_deferred_reason"] == "active_live_session_work"
    assert snapshot["closed_by_runtime"] is False
    assert snapshot["active_work_count"] == 1
    with lock:
        assert clients[key] is client
    assert client.close_reason == ""


def test_worker_runtime_sync_uses_live_session_active_work_instead_of_closing_as_ai_busy(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    routes = _DummyWorkerRoutes(tmp_path)
    client = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="local-worker-001",
        auth_message={"type": "worker.auth", "worker_id": "local-worker-001"},
    )
    client.active_work_count = 1
    key = ("http://127.0.0.1:8871", "local-worker-001")
    lock, clients = routes._worker_live_session_clients()
    with lock:
        clients[key] = client

    settings = {
        "sellerEnabled": True,
        "sellerAvailabilityMode": routes._WORKER_SELLER_AVAILABILITY_AI_IDLE,
        "selectedNetwork": "dev",
        "workerRequestedRing": "3",
        "workerConnectedHubUrl": "http://127.0.0.1:8871",
        "workerRuntimePhase": "accepting",
        "workerRuntimeActiveJobs": 0,
        "signedWorkerConnection": {
            "status": "hub-registered",
            "worker_start_status": "ready",
            "hub_registration_status": "accepted",
            "hub_registered": True,
            "wallet_address": "0x800497af6946e0a74d50b3461f4904302e8c4104",
            "credit_wallet": "0x800497af6946e0a74d50b3461f4904302e8c4104",
            "hub_url": "http://127.0.0.1:8871",
            "worker_id": "local-worker-001",
            "worker": {
                "worker_id": "local-worker-001",
                "capabilities": {"capabilities": ["chat.completions"]},
            },
        },
        "workerHubRegistration": {
            "status": "accepted",
            "worker": {
                "worker_id": "local-worker-001",
                "capabilities": {"capabilities": ["chat.completions"]},
            },
        },
    }

    saved_settings: dict[str, object] = {}

    monkeypatch.setattr(routes, "_save_worker_settings", lambda value: saved_settings.update(value) or value)
    monkeypatch.setattr(routes, "_worker_runtime_multisession_authorization", lambda **kwargs: {"kind": "multisession_key", "key_id": "msk_worker", "wallet_address": "0x800497af6946e0a74d50b3461f4904302e8c4104", "chain_id": "42424242"})
    monkeypatch.setattr(routes, "_worker_local_ai_capacity_snapshot", lambda **kwargs: {"ok": True, "available_now": False, "busy": True, "active_run_count": 1, "reason_code": "local_ai_busy"})
    monkeypatch.setattr(routes, "_ensure_worker_live_session", lambda **kwargs: client.snapshot())

    _saved, status = routes._worker_runtime_transition(settings, action="sync", send_heartbeat=True)

    assert status["runtime"]["active_jobs"] == 1
    assert status["runtime"]["phase"] == "accepting"
    assert status["runtime"]["hub_status"] == "busy"
    assert status["runtime"]["heartbeat_result"]["live_session"]["active_work_count"] == 1
    assert client.close_reason == ""
    assert saved_settings["workerRuntimeActiveJobs"] == 1
