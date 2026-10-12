"""Side-effect-free Temporal lifecycle for an already accepted Stable Hub session.

The authoritative financial and worker-result records remain in FoundationDB.
Temporal neither executes worker inference nor performs credit transactions.
"""
from __future__ import annotations

from typing import Any
from temporalio import workflow


@workflow.defn
class WorkSessionWorkflow:
    def __init__(self) -> None:
        self._terminal: dict[str, Any] | None = None

    @workflow.run
    async def run(self, session_id: str, request_id: str, owner_hub_id: str) -> dict[str, Any]:
        await workflow.wait_condition(lambda: self._terminal is not None)
        return {
            "session_id": session_id,
            "request_id": request_id,
            "owner_hub_id": owner_hub_id,
            "terminal": self._terminal,
        }

    @workflow.signal
    def finished(self, terminal: dict[str, Any]) -> None:
        if self._terminal is None and str(terminal.get("status") or "") in {
            "succeeded", "failed", "cancelled",
        }:
            self._terminal = dict(terminal)

    @workflow.query
    def status(self) -> dict[str, Any]:
        return dict(self._terminal or {"status": "waiting"})
