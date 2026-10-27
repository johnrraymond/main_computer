"""Crash-tolerant Stable Hub Worker lease and credit-hold reconciliation.

Every Hub independently scans the shared FoundationDB worker directory. The
atomic callback rechecks the exact connection generation and heartbeat before
fencing and settling an expired owner. Nothing depends on the dead owner's
cleanup handler running. A conflicting transaction retries as one unit.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from main_computer.stable_hub_worker_sessions import StableHubWorkerSessionError


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _expired(record: dict[str, Any], *, now: datetime, lease_seconds: float) -> bool:
    heartbeat = _timestamp(record.get("last_pong_at")) or _timestamp(record.get("connected_at"))
    if heartbeat is None:
        # Missing or malformed timestamps are not proof of death.
        return False
    return (now - heartbeat).total_seconds() >= lease_seconds


def recover_expired_worker_lease(
    server: Any, *, worker_id: str, connection_id: str, lease_epoch: int,
    now: datetime | None = None, lease_seconds: float = 90.0,
) -> dict[str, Any]:
    """Fence a proven expired generation and settle unfinished holds atomically.

    Normal socket-close cleanup may have failed after closing the owner; this
    also retries those CLOSED generations. A live newer generation is never
    affected. No partial release is committed on missing/charged credit evidence.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("recovery now must be timezone aware")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    store = server.worker_session_directory.store
    if any(directory.store is not store for directory in (
        server.worker_market_directory, server.accepted_work_session_directory,
        server.payout_ledger_directory,
    )):
        raise StableHubWorkerSessionError("recovery_directories_do_not_share_store")

    def _recover() -> dict[str, Any]:
        data = store.load()
        owner = data.get("workers", {}).get(worker_id)
        if not isinstance(owner, dict):
            return {"recovered": False, "failed_sessions": 0, "reason": "owner_missing"}
        if (str(owner.get("connection_id") or "") != connection_id
                or int(owner.get("lease_epoch") or 0) != lease_epoch):
            return {"recovered": False, "failed_sessions": 0, "reason": "generation_changed"}
        if str(owner.get("cluster_id") or "") != str(server.topology.cluster_id):
            raise StableHubWorkerSessionError("recovery_wrong_cluster")
        if owner.get("status") not in ("live", "closed"):
            return {"recovered": False, "failed_sessions": 0, "reason": "owner_not_recoverable"}
        if owner.get("status") == "live":
            if not _expired(owner, now=moment, lease_seconds=lease_seconds):
                return {"recovered": False, "failed_sessions": 0, "reason": "lease_recent"}
            # A live socket on THIS Hub is stronger evidence than a delayed
            # heartbeat. Remote sockets cannot be inspected across processes.
            if str(owner.get("owner_hub_id") or "") == str(server.hub.hub_id):
                local = server.get_live_worker_session(connection_id)
                if local is not None and local.is_live:
                    return {"recovered": False, "failed_sessions": 0, "reason": "local_socket_live"}

        market = data.get("market_workers", {}).get(worker_id)
        if isinstance(market, dict) and (str(market.get("connection_id") or "") != connection_id
                                              or int(market.get("lease_epoch") or 0) != lease_epoch):
            raise StableHubWorkerSessionError("recovery_market_generation_mismatch")
        open_sessions = [dict(item) for item in data.get("accepted_sessions", {}).values()
                         if isinstance(item, dict)
                         and str(item.get("worker_id") or "") == worker_id
                         and str(item.get("worker_connection_id") or "") == connection_id
                         and item.get("status") in ("accepted", "running")]
        # Validate ALL holds before performing ANY mutation, so one corrupt
        # reservation cannot leave a partially recovered owner/session set.
        for item in open_sessions:
            session_id = str(item.get("session_id") or "")
            hold_id = str((item.get("payout") or {}).get("hold_id") or "")
            hold = data.get("payout_holds", {}).get(hold_id)
            if (not hold_id or not isinstance(hold, dict)
                    or hold.get("status") != "held"
                    or str(hold.get("session_id") or "") != session_id
                    or str(hold.get("worker_id") or "") != worker_id):
                raise StableHubWorkerSessionError("recovery_open_session_hold_not_proven_held")
            account = data.get("payout_accounts", {}).get(str(hold.get("account_id") or ""))
            if (not isinstance(account, dict) or
                    int(account.get("held_credit_wei", 0)) < int(hold.get("credit_wei") or 0)):
                raise StableHubWorkerSessionError("recovery_hold_account_inconsistent")
        # The owner and market are fenced in this very transaction. A late
        # worker.pong may no longer resurrect the CLOSED owner.
        stamp = moment.isoformat()
        if owner.get("status") == "live":
            data["workers"][worker_id] = dict(
                owner, status="closed", closed_at=stamp,
                close_reason="recovery_lease_expired", recovered_by_hub_id=server.hub.hub_id,
                recovered_at=stamp,
            )
        if isinstance(market, dict) and market.get("status") != "closed":
            data["market_workers"][worker_id] = dict(
                market, status="closed", closed_at=stamp,
                close_reason="recovery_lease_expired", updated_at=stamp,
            )
        if owner.get("status") == "live" or (isinstance(market, dict) and market.get("status") != "closed"):
            store.save(data)

        for item in open_sessions:
            session_id = str(item["session_id"])
            payout = dict(item["payout"])
            released = server.payout_ledger_directory.release_hold(
                hold_id=str(payout["hold_id"]), session_id=session_id,
            )
            payout.update({
                "hold_status": "released", "payment_status": "released",
                "released_credits": str(released.get("amount") or "0"),
                "settlement_status": "not_earned", "direct_spend": False,
                "charge_id": "", "worker_earning_id": "", "charged_credits": "0",
            })
            server.accepted_work_session_directory.record_failed(
                session_id=session_id, worker_connection_id=connection_id,
                worker_failure={"type": "worker.work.failed", "error": {
                    "reason_code": "worker_owner_lease_expired_recovered",
                    "recovered_by_hub_id": str(server.hub.hub_id),
                }}, payout=payout,
            )
        return {"recovered": True, "failed_sessions": len(open_sessions),
                "reason": "expired_owner_reconciled"}

    atomic = getattr(store, "atomic", None)
    return atomic(_recover) if callable(atomic) else _recover()


def recover_expired_worker_leases(
    server: Any, *, now: datetime | None = None, lease_seconds: float = 90.0,
) -> dict[str, Any]:
    """Idempotent periodic survivor sweep. Errors are isolated per owner."""
    moment = now or datetime.now(timezone.utc)
    owners = server.worker_session_directory.store.load().get("workers", {})
    result: dict[str, Any] = {"scanned": 0, "recovered": 0, "failed_sessions": 0, "errors": []}
    for worker_id, owner in list(owners.items()):
        if not isinstance(owner, dict) or owner.get("status") not in ("live", "closed"):
            continue
        if owner.get("status") == "live" and not _expired(owner, now=moment, lease_seconds=lease_seconds):
            continue
        # A closed owner is retried ONLY while open accepted sessions remain;
        # otherwise repeated periodic FDB writes are unnecessary.
        if owner.get("status") == "closed":
            open_sessions = server.accepted_work_session_directory.list_open_sessions_for_worker_connection(
                worker_id=worker_id, connection_id=str(owner.get("connection_id") or ""))
            current_market = server.worker_market_directory.get_worker(worker_id)
            if not open_sessions and (not isinstance(current_market, dict)
                                      or current_market.get('status') == 'closed'):
                continue
        result["scanned"] += 1
        try:
            outcome = recover_expired_worker_lease(
                server, worker_id=worker_id,
                connection_id=str(owner.get("connection_id") or ""),
                lease_epoch=int(owner.get("lease_epoch") or 0),
                now=moment, lease_seconds=lease_seconds,
            )
            if outcome["recovered"]:
                result["recovered"] += 1
                result["failed_sessions"] += outcome["failed_sessions"]
        except Exception as exc:
            result["errors"].append({"worker_id": worker_id, "error": f"{type(exc).__name__}: {exc}"})
    return result
