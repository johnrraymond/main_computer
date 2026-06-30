from __future__ import annotations

import json
import subprocess
import threading
import time

import pytest
from types import SimpleNamespace

from main_computer.chat_ai_subprocess import ActiveChatAIProcess, ChatAISubprocessManager
from main_computer.config import MainComputerConfig
from main_computer.models import ChatResponse
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
        auth_message={"type": "worker.auth"},
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


def test_worker_live_session_client_forwards_content_deltas_before_terminal_result() -> None:
    sent_messages: list[dict[str, object]] = []

    def executor(offer: dict[str, object]) -> dict[str, object]:
        callback = offer.get("_worker_live_session_delta_callback")
        assert callable(callback)
        callback(
            {
                "data": {
                    "stream_event_type": "content_delta",
                    "delta": "A",
                    "latest_text": "A",
                    "content_chars": 1,
                }
            }
        )
        callback(
            {
                "data": {
                    "stream_event_type": "content_delta",
                    "delta": "B",
                    "latest_text": "AB",
                    "content_chars": 2,
                }
            }
        )
        return {
            "status": "success",
            "response": {
                "content": "AB",
                "provider": "unit-test-provider",
                "model": "unit-test-model",
            },
        }

    client = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="worker-local-ai",
        auth_message={"type": "worker.auth"},
        work_executor=executor,
    )
    client._send_json = sent_messages.append  # type: ignore[method-assign]

    client._handle_work_offer(
        {
            "type": "hub.work.offer",
            "session_id": "sess-stream",
            "run_id": "run-stream",
            "request_id": "req-stream",
            "work": {
                "capabilities": ["chat.completions"],
                "messages": [{"role": "user", "content": "stream please"}],
                "model": "micro-agent-local",
            },
        }
    )

    deadline = time.monotonic() + 2.0
    while len(sent_messages) < 4 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert [message["type"] for message in sent_messages[:4]] == [
        "worker.work.accepted",
        "worker.work.delta",
        "worker.work.delta",
        "worker.work.result",
    ]
    first_delta = sent_messages[1]
    second_delta = sent_messages[2]
    assert first_delta["session_id"] == "sess-stream"
    assert first_delta["request_id"] == "req-stream"
    assert first_delta["run_id"] == "run-stream"
    assert first_delta["seq"] == 1
    assert first_delta["delta"] == "A"
    assert first_delta["content_so_far"] == "A"
    assert second_delta["seq"] == 2
    assert second_delta["delta"] == "B"
    assert second_delta["content_so_far"] == "AB"


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



def test_worker_live_session_child_emits_terminal_result_before_completion_activity(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import main_computer.chat_ai_subprocess as subprocess_module

    emitted: list[dict[str, object]] = []

    class _CaptureStdout:
        def emit(self, message: dict[str, object]) -> None:
            emitted.append(message)

    class _FakeProvider:
        name = "unit-provider"
        model = "unit-model"
        stream_callback = None

        def chat(self, messages):
            return ChatResponse(
                content="terminal result first",
                provider="unit-provider",
                model="unit-model",
                metadata={"message_count": len(messages)},
            )

    class _FakeComputer:
        provider = _FakeProvider()

    monkeypatch.setattr(subprocess_module.MainComputer, "build", lambda config: _FakeComputer())

    payload = subprocess_module._run_worker_live_session_chat_completion_child(
        {
            "run_id": "run-child-result-first",
            "source": "hello",
            "messages": [{"role": "user", "content": "hello"}],
            "config": {"workspace": str(tmp_path)},
        },
        _CaptureStdout(),
        log_file=str(tmp_path / "child-result-first.log"),
    )

    assert payload["response"]["content"] == "terminal result first"
    result_index = next(index for index, message in enumerate(emitted) if message.get("type") == "result")
    completed_activity_index = next(
        index
        for index, message in enumerate(emitted)
        if message.get("type") == "activity"
        and ((message.get("event") if isinstance(message.get("event"), dict) else {}).get("title"))
        == "Worker local AI subprocess completed"
    )
    assert result_index < completed_activity_index
    assert emitted[result_index]["payload"]["response"]["content"] == "terminal result first"  # type: ignore[index]



def test_worker_subprocess_parent_returns_terminal_result_before_child_exit(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import main_computer.chat_ai_subprocess as subprocess_module

    created: list[object] = []

    class _FakeStdin:
        def __init__(self) -> None:
            self.data = b""
            self.closed = False

        def write(self, data: bytes) -> int:
            self.data += data
            return len(data)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    class _FakeStdout:
        def __init__(self, process: "_FakePopen") -> None:
            self.process = process
            self.sent_result = False

        def readline(self) -> bytes:
            if not self.sent_result:
                self.sent_result = True
                return (
                    json.dumps(
                        {
                            "type": "result",
                            "ok": True,
                            "run_id": "run-terminal-before-exit",
                            "payload": {"response": {"content": "finished before exit"}},
                        }
                    )
                    + "\n"
                ).encode("utf-8")
            while self.process.running:
                time.sleep(0.01)
            return b""

        def close(self) -> None:
            return None

    class _FakeStderr:
        def readline(self) -> bytes:
            return b""

        def close(self) -> None:
            return None

    class _FakePopen:
        pid = 6161

        def __init__(self, *args, **kwargs) -> None:
            self.running = True
            self.terminated = False
            self.killed = False
            self.returncode: int | None = None
            self.stdin = _FakeStdin()
            self.stdout = _FakeStdout(self)
            self.stderr = _FakeStderr()
            created.append(self)

        def poll(self):
            return None if self.running else self.returncode

        def wait(self, timeout: float | None = None):
            if self.running:
                if timeout is not None:
                    raise subprocess.TimeoutExpired("fake-worker", timeout)
                raise subprocess.TimeoutExpired("fake-worker", 0)
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.running = False
            self.returncode = -15

        def kill(self) -> None:
            self.killed = True
            self.running = False
            self.returncode = -9

    monkeypatch.setattr(subprocess_module.subprocess, "Popen", _FakePopen)

    manager = ChatAISubprocessManager()
    payload = manager.run(
        command={
            "run_id": "run-terminal-before-exit",
            "mode": "worker_live_session_chat_completion",
            "source": "hello",
        },
        thread_id="worker-live-session:sess-terminal-before-exit",
        log_file=tmp_path / "terminal-before-exit.log",
        activity_bus=None,
        cwd=tmp_path,
        max_local_concurrency=1,
    )

    assert payload["response"]["content"] == "finished before exit"
    assert created
    assert created[0].terminated is True  # type: ignore[index]
    assert manager.local_ai_capacity_snapshot(thread_id="", max_local_concurrency=1)["active_run_count"] == 0



def test_worker_live_session_client_reports_executor_error_as_terminal_failure() -> None:
    sent_messages: list[dict[str, object]] = []

    def executor(offer: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("local model is not configured")

    client = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="worker-local-ai",
        auth_message={"type": "worker.auth"},
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


def test_worker_live_session_default_local_ai_timeout_is_generous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAIN_COMPUTER_WORKER_LIVE_SESSION_LOCAL_AI_TIMEOUT_SECONDS", raising=False)
    client = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="worker-local-ai",
        auth_message={"type": "worker.auth"},
        work_executor=lambda offer: {"status": "success"},
    )

    assert client._work_offer_timeout_seconds({"type": "hub.work.offer", "work": {}}) == 180.0
    assert client._work_offer_timeout_seconds(
        {
            "type": "hub.work.offer",
            "work": {"local_ai_timeout_seconds": 12},
        }
    ) == 12.0


def test_worker_live_session_client_keepalive_uses_worker_pong_contract() -> None:
    sent_messages: list[dict[str, object]] = []
    client = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="worker-local-ai",
        auth_message={"type": "worker.auth"},
    )
    client._send_json = sent_messages.append  # type: ignore[method-assign]

    client._send_keepalive()

    assert sent_messages
    keepalive = sent_messages[0]
    assert keepalive["type"] == "worker.pong"
    assert keepalive["keepalive"] is True
    assert "sent_at" in keepalive


def test_worker_live_session_client_times_out_stuck_local_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_messages: list[dict[str, object]] = []

    def executor(offer: dict[str, object]) -> dict[str, object]:
        time.sleep(5.0)
        return {"status": "success", "response": {"content": "too late"}}

    monkeypatch.setenv("MAIN_COMPUTER_WORKER_LIVE_SESSION_LOCAL_AI_TIMEOUT_SECONDS", "0.05")
    client = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="worker-local-ai",
        auth_message={"type": "worker.auth"},
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
        auth_message={"type": "worker.auth"},
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
        worker_id="0x800497af6946e0a74d50b3461f4904302e8c4104",
        auth_message=base_auth,
    )
    second = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="0x800497af6946e0a74d50b3461f4904302e8c4104",
        auth_message=changed_auth,
    )

    assert first.fingerprint == second.fingerprint


def test_worker_live_session_runtime_close_is_deferred_while_offer_work_active(tmp_path) -> None:
    routes = _DummyWorkerRoutes(tmp_path)
    client = _WorkerHubLiveSessionClient(
        hub_url="http://127.0.0.1:8871",
        worker_id="0x800497af6946e0a74d50b3461f4904302e8c4104",
        auth_message={"type": "worker.auth"},
    )
    client.active_work_count = 1

    key = ("http://127.0.0.1:8871", "0x800497af6946e0a74d50b3461f4904302e8c4104")
    lock, clients = routes._worker_live_session_clients()
    with lock:
        clients[key] = client

    snapshot = routes._close_worker_live_session(
        hub_url="http://127.0.0.1:8871",
        worker_id="0x800497af6946e0a74d50b3461f4904302e8c4104",
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
        worker_id="0x800497af6946e0a74d50b3461f4904302e8c4104",
        auth_message={"type": "worker.auth"},
    )
    client.active_work_count = 1
    key = ("http://127.0.0.1:8871", "0x800497af6946e0a74d50b3461f4904302e8c4104")
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
            "worker_id": "0x800497af6946e0a74d50b3461f4904302e8c4104",
            "worker": {
                "worker_id": "0x800497af6946e0a74d50b3461f4904302e8c4104",
                "capabilities": {"capabilities": ["chat.completions"]},
            },
        },
        "workerHubRegistration": {
            "status": "accepted",
            "worker": {
                "worker_id": "0x800497af6946e0a74d50b3461f4904302e8c4104",
                "capabilities": {"capabilities": ["chat.completions"]},
            },
        },
    }

    saved_settings: dict[str, object] = {}

    monkeypatch.setattr(routes, "_save_worker_settings", lambda value: saved_settings.update(value) or value)
    monkeypatch.setattr(routes, "_worker_runtime_multisession_authorization", lambda **kwargs: {"kind": "multisession_key", "key_id": "msk_worker", "wallet_address": "0x800497af6946e0a74d50b3461f4904302e8c4104", "chain_id": "42424242"})
    monkeypatch.setattr(routes, "_worker_local_ai_capacity_snapshot", lambda **kwargs: {"ok": True, "available_now": False, "busy": True, "active_run_count": 1, "reason_code": "local_ai_busy"})
    monkeypatch.setattr(routes, "_ensure_worker_live_session", lambda **kwargs: {**client.snapshot(), "alive": True})

    _saved, status = routes._worker_runtime_transition(settings, action="sync", send_heartbeat=True)

    assert status["runtime"]["active_jobs"] == 1
    assert status["runtime"]["phase"] == "accepting"
    assert status["runtime"]["hub_status"] == "busy"
    assert status["runtime"]["heartbeat_result"]["live_session"]["active_work_count"] == 1
    assert client.close_reason == ""
    assert saved_settings["workerRuntimeActiveJobs"] == 1
