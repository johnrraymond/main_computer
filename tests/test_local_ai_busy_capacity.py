from __future__ import annotations

import time
from pathlib import Path

from main_computer.chat_ai_subprocess import ActiveChatAIProcess, ChatAISubprocessManager


class FakeProcess:
    pid = 4242

    def __init__(self, *, running: bool = True) -> None:
        self.running = running

    def poll(self):
        return None if self.running else 0


def _install_active_run(manager: ChatAISubprocessManager, *, thread_id: str, run_id: str, running: bool = True) -> FakeProcess:
    process = FakeProcess(running=running)
    manager._active_by_thread[thread_id] = ActiveChatAIProcess(
        thread_id=thread_id,
        run_id=run_id,
        process=process,  # type: ignore[arg-type]
        log_file=Path("diagnostics_output/chat_console_ai_sessions/test/session.log"),
        started_at=time.monotonic() - 1.25,
    )
    return process


def test_local_ai_capacity_reports_available_when_no_runs_are_active() -> None:
    manager = ChatAISubprocessManager()

    snapshot = manager.local_ai_capacity_snapshot(thread_id="thread-a", max_local_concurrency=1)

    assert snapshot["ok"] is True
    assert snapshot["available_now"] is True
    assert snapshot["busy"] is False
    assert snapshot["reason_code"] == "local_ai_available"
    assert snapshot["active_run_count"] == 0
    assert snapshot["cards"][0]["key"] == "local_capacity"
    assert snapshot["cards"][0]["status"] == "pass"


def test_local_ai_capacity_reports_thread_busy_before_remote_overflow() -> None:
    manager = ChatAISubprocessManager()
    _install_active_run(manager, thread_id="thread-a", run_id="run-a")

    snapshot = manager.local_ai_capacity_snapshot(thread_id="thread-a", max_local_concurrency=8)

    assert snapshot["available_now"] is False
    assert snapshot["busy"] is True
    assert snapshot["reason_code"] == "thread_busy"
    assert snapshot["active_run_count"] == 1
    assert snapshot["active_thread_ids"] == ["thread-a"]
    assert snapshot["user_message"] == "This chat is currently using the local AI slot."
    assert snapshot["cards"][0]["status"] == "blocked"
    assert snapshot["cards"][0]["message"] == "This chat is currently using the local AI slot."
    assert snapshot["cards"][0]["details"]["checked_thread_id"] == "thread-a"
    assert snapshot["cards"][0]["details"]["active_thread_id"] == "thread-a"
    assert snapshot["cards"][0]["details"]["active_run_id"] == "run-a"


def test_local_ai_capacity_reports_global_concurrency_exhausted() -> None:
    manager = ChatAISubprocessManager()
    _install_active_run(manager, thread_id="thread-a", run_id="run-a")

    snapshot = manager.local_ai_capacity_snapshot(thread_id="thread-b", max_local_concurrency=1)

    assert snapshot["available_now"] is False
    assert snapshot["busy"] is True
    assert snapshot["reason_code"] == "local_concurrency_exhausted"
    assert snapshot["user_message"] == "Local AI has no free slot right now; another chat is using the local AI slot."
    assert snapshot["active_run_count"] == 1
    assert snapshot["cards"][0]["details"]["checked_thread_id"] == "thread-b"
    assert snapshot["cards"][0]["details"]["active_thread_id"] == "thread-a"
    assert snapshot["cards"][0]["details"]["active_run_id"] == "run-a"


def test_local_ai_capacity_prunes_finished_runs() -> None:
    manager = ChatAISubprocessManager()
    process = _install_active_run(manager, thread_id="thread-a", run_id="run-a")
    process.running = False

    snapshot = manager.local_ai_capacity_snapshot(thread_id="thread-b", max_local_concurrency=1)

    assert snapshot["available_now"] is True
    assert snapshot["reason_code"] == "local_ai_available"
    assert snapshot["active_run_count"] == 0
    assert manager.active_runs_snapshot()["active_run_count"] == 0
