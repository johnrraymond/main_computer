from __future__ import annotations

"""Smoke test for the first local-AI busy/capacity backend primitive.

This does not start Ollama or a real model call.  It proves the backend can
programmatically answer the first remote-overflow routing question:

    "Is local AI capacity available now, or is it already busy?"
"""

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.chat_ai_subprocess import ActiveChatAIProcess, ChatAISubprocessManager


class FakeProcess:
    pid = 8675309

    def __init__(self, *, running: bool = True) -> None:
        self.running = running

    def poll(self):
        return None if self.running else 0


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    manager = ChatAISubprocessManager()

    available = manager.local_ai_capacity_snapshot(thread_id="chat-a", max_local_concurrency=1)
    require(available["available_now"] is True, "fresh manager should report local AI available")
    require(available["reason_code"] == "local_ai_available", "fresh manager should use local_ai_available reason")

    process = FakeProcess(running=True)
    manager._active_by_thread["chat-a"] = ActiveChatAIProcess(
        thread_id="chat-a",
        run_id="run-a",
        process=process,  # type: ignore[arg-type]
        log_file=Path("diagnostics_output/chat_console_ai_sessions/run-a/session.log"),
        started_at=time.monotonic() - 2.0,
    )

    same_thread_busy = manager.local_ai_capacity_snapshot(thread_id="chat-a", max_local_concurrency=4)
    require(same_thread_busy["available_now"] is False, "same thread should report busy")
    require(same_thread_busy["reason_code"] == "thread_busy", "same thread should use thread_busy reason")
    require(same_thread_busy["cards"][0]["status"] == "blocked", "busy capacity card should be blocked")

    global_busy = manager.local_ai_capacity_snapshot(thread_id="chat-b", max_local_concurrency=1)
    require(global_busy["available_now"] is False, "global capacity limit should report busy")
    require(global_busy["reason_code"] == "local_concurrency_exhausted", "global limit should use local_concurrency_exhausted")

    spare_capacity = manager.local_ai_capacity_snapshot(thread_id="chat-b", max_local_concurrency=2)
    require(spare_capacity["available_now"] is True, "higher concurrency limit should leave capacity available")
    require(spare_capacity["reason_code"] == "local_ai_available", "spare capacity should report available")

    process.running = False
    pruned = manager.local_ai_capacity_snapshot(thread_id="chat-b", max_local_concurrency=1)
    require(pruned["available_now"] is True, "finished process should be pruned")
    require(pruned["active_run_count"] == 0, "finished process should not remain active")

    print(
        json.dumps(
            {
                "ok": True,
                "smoke": "local-ai-busy-capacity",
                "proved": [
                    "available when no AI subprocess is active",
                    "busy for the same chat thread while a subprocess is active",
                    "busy when global local concurrency is exhausted",
                    "available when spare local concurrency remains",
                    "finished subprocesses are pruned before capacity is reported",
                    "capacity result includes remote-overflow-ready diagnostic cards",
                ],
                "example_busy_card": same_thread_busy["cards"][0],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
