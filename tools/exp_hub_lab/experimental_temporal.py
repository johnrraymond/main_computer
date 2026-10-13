"""Read-only P2-A evidence of real Experimental FDB -> Temporal supervision.

Operator workflow: verify one *terminal* paid session, save a checkpoint,
restart the Experimental Hub process and its Temporal worker, then verify the
same checkpoint. No request, worker offer, credit mutation or workflow start is
performed by this observer.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any


def _digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def verify_temporal_session(
    *, session_id: str, cluster_file: Path, exp_namespace: str,
    temporal_address: str, checkpoint: Path,
) -> dict[str, Any]:
    if not session_id.strip():
        raise ValueError("--temporal-session-id is required")
    if not temporal_address.strip():
        raise ValueError("Temporal address required: set MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS or use --temporal-address")
    if not cluster_file.is_file():
        raise ValueError(f"FoundationDB cluster file not found: {cluster_file}")
    # Lazy imports: an unconfigured Experimental Hub must never require Temporal SDK.
    try:
        from temporalio.client import Client
        from main_computer.stable_hub_worker_sessions import FoundationDbStableWorkerSessionStore
        from main_computer.stable_hub_temporal import temporal_workflow_is_completed
        from main_computer.exp_fdb_hub import _exp_fdb_stable_namespace
    except ImportError as exc:
        raise RuntimeError(f"P2-A live verification requires Temporal SDK and FoundationDB bindings: {exc}") from exc

    store = FoundationDbStableWorkerSessionStore(
        cluster_file=cluster_file,
        namespace=_exp_fdb_stable_namespace(exp_namespace),
    )
    document = await asyncio.to_thread(store.load)
    sessions = document.get("accepted_sessions", {})
    record = sessions.get(session_id) if isinstance(sessions, dict) else None
    if not isinstance(record, dict):
        raise RuntimeError(f"session not found in Experimental FDB outbox: {session_id}")
    execution = record.get("execution") or {}
    if execution.get("temporal_mode") != "enabled":
        raise RuntimeError(f"session {session_id} lacks durable Temporal intent")
    if str(execution.get("workflow_id") or "") != session_id:
        raise RuntimeError("session/workflow identity mismatch")
    if record.get("status") not in {"succeeded", "failed", "cancelled"}:
        raise RuntimeError("checkpoint only supports terminal sessions; wait for FDB terminal state")
    namespace = str(execution.get("namespace") or "")
    client = await Client.connect(temporal_address, namespace=namespace)
    handle = client.get_workflow_handle(session_id)
    description = await handle.describe()
    completed = temporal_workflow_is_completed(description.status)
    temporal_status = "COMPLETED" if completed else str(getattr(description.status, "name", description.status))
    if not completed:
        raise RuntimeError(f"terminal FDB session has no completed Temporal workflow: {temporal_status}")
    snapshot = {
        "session_id": session_id,
        "request_id": str(record.get("request_id") or ""),
        "owner_hub_id": str(record.get("owner_hub_id") or ""),
        "status": record["status"],
        "namespace": namespace,
        "fdb_record_sha256": _digest(record),
    }
    if checkpoint.is_file():
        baseline = json.loads(checkpoint.read_text(encoding="utf-8"))
        if baseline != snapshot:
            raise RuntimeError("P2-A checkpoint mismatch after restart: FDB terminal session changed")
        phase = "post-restart-compared"
    else:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        # Fail closed: do not replace an existing checkpoint during a race.
        try:
            with checkpoint.open("x", encoding="utf-8") as f:
                json.dump(snapshot, f, sort_keys=True, indent=2)
        except FileExistsError as exc:
            raise RuntimeError("checkpoint was created concurrently; rerun to compare it") from exc
        phase = "baseline-created"
    return {"ok": True, "phase": phase, "snapshot": snapshot,
            "temporal_status": temporal_status, "checkpoint": str(checkpoint)}


def verify(**kwargs: Any) -> dict[str, Any]:
    try:
        return asyncio.run(verify_temporal_session(**kwargs))
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
