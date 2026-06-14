from __future__ import annotations

from datetime import timedelta
from typing import Any

from tools.temporal_lab.models import FakeTokenRequest


try:  # pragma: no cover - exercised only when temporalio is installed.
    from temporalio import workflow
except ImportError:  # pragma: no cover - this shim keeps contract tests dependency-light.
    class _WorkflowShim:
        def defn(self, cls: object | None = None, **kwargs: object) -> object:
            if cls is not None:
                return cls

            def decorator(inner_cls: object) -> object:
                return inner_cls

            return decorator

        def run(self, func: object | None = None, **kwargs: object) -> object:
            if func is not None:
                return func

            def decorator(inner_func: object) -> object:
                return inner_func

            return decorator

        async def execute_activity(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("temporalio is required to execute the workflow")

    workflow = _WorkflowShim()  # type: ignore[assignment]


def activity_start_to_close_timeout(request: FakeTokenRequest) -> timedelta:
    # Leave room for the requested token interval plus local Docker/Desktop
    # scheduling jitter during the live smoke. Unit tests can still use a zero
    # interval for fast direct activity execution.
    seconds = max(30.0, (request.token_interval_seconds * request.token_count) + 30.0)
    return timedelta(seconds=seconds)


def activity_heartbeat_timeout(request: FakeTokenRequest) -> timedelta:
    seconds = max(10.0, request.token_interval_seconds + 10.0)
    return timedelta(seconds=seconds)


@workflow.defn  # type: ignore[misc]
class FakeTokenWorkflow:
    @workflow.run  # type: ignore[misc]
    async def run(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        request = FakeTokenRequest.from_mapping(request_payload)
        return await workflow.execute_activity(  # type: ignore[attr-defined]
            "emit_fake_tokens",
            request.to_dict(),
            start_to_close_timeout=activity_start_to_close_timeout(request),
            heartbeat_timeout=activity_heartbeat_timeout(request),
        )
