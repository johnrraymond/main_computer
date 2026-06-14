from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from tools.temporal_lab.event_log import DEFAULT_EVENT_LOG_PATH, append_jsonl_event, fake_token_text
from tools.temporal_lab.models import FakeTokenRequest, FakeTokenResult


try:  # pragma: no cover - exercised only when temporalio is installed.
    from temporalio import activity
except ImportError:  # pragma: no cover - this shim keeps unit tests dependency-light.
    class _ActivityShim:
        def defn(self, *decorator_args: object, **decorator_kwargs: object) -> object:
            if decorator_args and callable(decorator_args[0]) and len(decorator_args) == 1 and not decorator_kwargs:
                return decorator_args[0]

            def decorator(func: object) -> object:
                return func

            return decorator

        def heartbeat(self, *args: object, **kwargs: object) -> None:
            return None

    activity = _ActivityShim()  # type: ignore[assignment]


def default_worker_id() -> str:
    return os.getenv("TEMPORAL_LAB_WORKER_ID") or f"worker-pid-{os.getpid()}"


def _activity_heartbeat(payload: dict[str, Any]) -> None:
    try:
        activity.heartbeat(payload)  # type: ignore[attr-defined]
    except RuntimeError:
        # Temporal raises when called outside an activity context. Direct unit
        # tests and local dry smoke paths should still be able to exercise the
        # fake token loop and JSONL output.
        return


class FakeTokenActivities:
    def __init__(
        self,
        *,
        event_log_path: str | Path = DEFAULT_EVENT_LOG_PATH,
        worker_id: str | None = None,
    ) -> None:
        self.event_log_path = Path(event_log_path)
        self.worker_id = worker_id or default_worker_id()

    @activity.defn(name="emit_fake_tokens")  # type: ignore[misc]
    async def emit_fake_tokens(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        request = FakeTokenRequest.from_mapping(request_payload)
        events_written = 0

        append_jsonl_event(
            self.event_log_path,
            {
                "event": "start",
                "request_id": request.request_id,
                "account_id": request.account_id,
                "ring": request.ring,
                "partition": request.partition,
                "credits_offered": request.credits_offered,
                "worker_id": self.worker_id,
            },
        )
        events_written += 1

        for seq in range(1, request.token_count + 1):
            if request.token_interval_seconds:
                await asyncio.sleep(request.token_interval_seconds)

            token_event = {
                "event": "token",
                "request_id": request.request_id,
                "account_id": request.account_id,
                "ring": request.ring,
                "partition": request.partition,
                "credits_offered": request.credits_offered,
                "worker_id": self.worker_id,
                "seq": seq,
                "text": fake_token_text(seq),
            }
            append_jsonl_event(self.event_log_path, token_event)
            events_written += 1

            progress = {
                "event": "progress",
                "request_id": request.request_id,
                "account_id": request.account_id,
                "ring": request.ring,
                "partition": request.partition,
                "credits_offered": request.credits_offered,
                "worker_id": self.worker_id,
                "tokens": seq,
                "token_count": request.token_count,
            }
            append_jsonl_event(self.event_log_path, progress)
            events_written += 1
            _activity_heartbeat(progress)

        append_jsonl_event(
            self.event_log_path,
            {
                "event": "done",
                "request_id": request.request_id,
                "account_id": request.account_id,
                "ring": request.ring,
                "partition": request.partition,
                "credits_offered": request.credits_offered,
                "worker_id": self.worker_id,
                "tokens": request.token_count,
                "result": {"ok": True},
            },
        )
        events_written += 1

        return FakeTokenResult(
            request_id=request.request_id,
            account_id=request.account_id,
            credits_offered=request.credits_offered,
            ring=request.ring,
            partition=request.partition,
            worker_id=self.worker_id,
            token_count=request.token_count,
            events_written=events_written,
            event_log_path=str(self.event_log_path),
        ).to_dict()
