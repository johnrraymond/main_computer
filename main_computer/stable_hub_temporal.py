"""Opt-in Temporal supervision for Stable Hub accepted work sessions.

FoundationDB remains authoritative for worker execution, charges, and results.
Temporal workflows track acceptance-to-terminal state and survive coordinator
restarts. Neither workflow nor reconciliation retries a paid worker execution.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from datetime import timedelta
from typing import Any


TEMPORAL_TASK_QUEUE = "main-computer-stable-work-session-lifecycle"
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})


class TemporalLifecycleUnavailable(RuntimeError):
    """No verified Temporal runtime is available to supervise new paid work."""


try:
    from main_computer.stable_hub_temporal_workflow import WorkSessionWorkflow
except ImportError:
    WorkSessionWorkflow = None  # type: ignore[assignment]


def temporal_address() -> str:
    return os.environ.get("MAIN_COMPUTER_STABLE_TEMPORAL_ADDRESS", "").strip()


class StableHubTemporalRuntime:
    """Hub-local Temporal worker plus FDB outbox reconciler.

    A/C may share the same task queue. FDB's accepted-session records are an
    outbox; if process failure interrupts a start/signal, another live Hub
    can repair the Temporal lifecycle without spending credits again.
    """

    def __init__(self, *, address: str, namespace: str, store: Any) -> None:
        if not address or not namespace:
            raise TemporalLifecycleUnavailable("Temporal address and namespace are required")
        self.address = address
        self.namespace = namespace
        self.store = store
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._last_error = "not_started"
        self._thread = threading.Thread(target=self._thread_main, name="stable-hub-temporal", daemon=True)
        self._thread.start()

    def require_ready(self) -> None:
        if not self._ready.wait(timeout=1.0):
            raise TemporalLifecycleUnavailable(
                f"Temporal lifecycle worker is unavailable ({self._last_error}); no new paid work admitted"
            )

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    def close(self) -> None:
        self._stopping.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)
        self._thread.join(timeout=2)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        finally:
            loop.close()
            self._loop = None

    async def _serve(self) -> None:
        try:
            from temporalio.client import Client
            from temporalio.worker import Worker
        except ImportError:
            self._last_error = "temporalio_sdk_missing"
            return
        while not self._stopping.is_set():
            try:
                client = await Client.connect(self.address, namespace=self.namespace)
                worker = Worker(client, task_queue=TEMPORAL_TASK_QUEUE, workflows=[WorkSessionWorkflow])
                self._client = client
                task = asyncio.create_task(worker.run())
                self._ready.set()
                self._last_error = ""
                try:
                    while not self._stopping.is_set() and not task.done():
                        await self._reconcile_once(client)
                        await asyncio.sleep(3)
                    if task.done():
                        await task
                finally:
                    self._ready.clear()
                    self._client = None
                    await worker.shutdown()
                    if not task.done():
                        await task
            except Exception as exc:
                self._ready.clear()
                self._last_error = type(exc).__name__
                print(f"stable-hub Temporal unavailable: {type(exc).__name__}", file=sys.stderr)
                await asyncio.sleep(3)

    async def _reconcile_once(self, client: Any) -> None:
        # Read-only FDB inspection. We intentionally never repair accounting,
        # replay a worker offer, or mint/refund credit from this loop.
        try:
            document = await asyncio.to_thread(self.store.load)
        except Exception as exc:
            self._last_error = f"fdb_reconciliation_{type(exc).__name__}"
            self._ready.clear()
            return
        self._ready.set()
        sessions = document.get("accepted_sessions", {}) if isinstance(document, dict) else {}
        if not isinstance(sessions, dict):
            return
        for session in list(sessions.values()):
            if not isinstance(session, dict):
                continue
            execution = session.get("execution")
            if not isinstance(execution, dict) or execution.get("temporal_mode") != "enabled":
                continue
            try:
                await self._ensure_workflow(client, session)
            except Exception as exc:
                self._last_error = f"workflow_reconcile_{type(exc).__name__}"

    async def _ensure_workflow(self, client: Any, session: dict[str, Any]) -> None:
        session_id = str(session.get("session_id") or "")
        request_id = str(session.get("request_id") or "")
        owner_hub_id = str(session.get("owner_hub_id") or "")
        if not session_id or not request_id or not owner_hub_id:
            return
        from temporalio.common import WorkflowIDReusePolicy
        from temporalio.exceptions import WorkflowAlreadyStartedError
        try:
            await client.start_workflow(
                WorkSessionWorkflow.run, args=[session_id, request_id, owner_hub_id],
                id=session_id, task_queue=TEMPORAL_TASK_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                execution_timeout=timedelta(days=7),
            )
        except WorkflowAlreadyStartedError:
            pass  # Deterministic session ID: no duplicate workflow.
        status = str(session.get("status") or "")
        if status in TERMINAL_STATES:
            handle = client.get_workflow_handle(session_id)
            try:
                await handle.signal(WorkSessionWorkflow.finished, {
                    "status": status, "session_id": session_id,
                    "request_id": request_id,
                })
            except Exception:
                # A previously completed workflow rejects a duplicate signal.
                # A live workflow will be reconciled on the next pass.
                pass
