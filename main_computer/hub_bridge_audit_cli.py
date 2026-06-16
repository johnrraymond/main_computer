from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from main_computer.temporal_fdb_hub_stress_smoke import (
    DEFAULT_STRESS_A_URL,
    DEFAULT_STRESS_B_URL,
    DEFAULT_STRESS_REPORT_PATH,
)


class HubBridgeAuditCliError(RuntimeError):
    """Raised when the read-only bridge audit CLI cannot inspect a target."""


@dataclass(frozen=True)
class HubBridgeAuditConfig:
    hub_urls: tuple[str, ...] = (DEFAULT_STRESS_A_URL, DEFAULT_STRESS_B_URL)
    namespace: str = ""
    report_path: Path | None = DEFAULT_STRESS_REPORT_PATH
    account_id: str = ""
    wallet_address: str = ""
    worker_node_id: str = ""
    limit: int = 500
    timeout_seconds: float = 5.0
    output: str = "text"
    strict: bool = False
    offline: bool = False
    require_live_hubs: bool = False


FetchJson = Callable[[str, str, dict[str, Any], float], dict[str, Any]]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _json_get(hub_url: str, path: str, params: dict[str, Any] | None = None, timeout_seconds: float = 5.0) -> dict[str, Any]:
    query = {key: value for key, value in dict(params or {}).items() if value not in (None, "")}
    url = hub_url.rstrip("/") + path
    if query:
        url += "?" + urlencode(query)
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=max(1.0, float(timeout_seconds or 5.0))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            error_payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            error_payload = {"error": str(exc)}
        raise HubBridgeAuditCliError(f"GET {url} failed with HTTP {exc.code}: {error_payload}") from exc
    except (OSError, URLError, TimeoutError) as exc:
        raise HubBridgeAuditCliError(f"GET {url} failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise HubBridgeAuditCliError(f"GET {url} returned non-object JSON.")
    return payload


def _event_type_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type") or event.get("type") or "").strip()
        if not event_type:
            continue
        counts[event_type] = counts.get(event_type, 0) + 1
    return dict(sorted(counts.items()))


def _event_amount_sum(events: list[dict[str, Any]], event_type: str) -> int:
    return sum(_safe_int(event.get("amount_wei")) for event in events if str(event.get("event_type", "")) == event_type)


def _event_reference_ids(events: list[dict[str, Any]], event_type: str) -> list[str]:
    refs = {
        str(event.get("reference_id") or "").strip()
        for event in events
        if str(event.get("event_type", "")) == event_type and str(event.get("reference_id") or "").strip()
    }
    return sorted(refs)


def _first_nonzero_int(*values: Any) -> int:
    for value in values:
        number = _safe_int(value)
        if number:
            return number
    return 0


def _sum_prefixed_movements(movements: dict[str, Any], prefix: str) -> int:
    return sum(_movement_amount(movement) for key, movement in movements.items() if str(key).startswith(prefix))


def _count_named_movements(movements: dict[str, Any], *, include: str, prefix: str = "") -> int:
    count = 0
    include_lower = include.lower()
    for key, movement in movements.items():
        key_text = str(key).lower()
        if prefix and not key_text.startswith(prefix.lower()):
            continue
        if include_lower not in key_text:
            continue
        if _movement_amount(movement) or _extract_tx_hashes(movement):
            count += 1
    return count


def _extract_tx_hashes(value: Any) -> set[str]:
    hashes: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in {"transaction_hash", "transactionhash", "hash"} and isinstance(item, str) and item.startswith("0x"):
                hashes.add(item)
            elif normalized_key == "transaction_hashes" and isinstance(item, list):
                hashes.update(str(entry) for entry in item if isinstance(entry, str) and entry.startswith("0x"))
            else:
                hashes.update(_extract_tx_hashes(item))
    elif isinstance(value, list):
        for item in value:
            hashes.update(_extract_tx_hashes(item))
    return hashes


def _infer_active_wallet_locks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Infer currently active locks from the ordered audit stream.

    The Hub exposes a per-wallet lock endpoint, but not a global lock listing.
    For operator readback we can infer active locks from bridge.wallet.locked and
    bridge.wallet.unlocked events returned by /bridge/audit.
    """

    locks: dict[tuple[str, str], dict[str, Any]] = {}
    for event in sorted(events, key=lambda item: str(item.get("created_at", ""))):
        event_type = str(event.get("event_type", ""))
        wallet = str(event.get("wallet_address", ""))
        reference_id = str(event.get("reference_id", ""))
        key = (wallet.lower(), reference_id)
        if event_type == "bridge.wallet.locked":
            locks[key] = {
                "wallet_address": wallet,
                "payout_id": reference_id,
                "worker_node_id": str(event.get("worker_node_id", "")),
                "account_id": str(event.get("account_id", "")),
                "created_at": str(event.get("created_at", "")),
                "amount_wei": str(event.get("amount_wei", "")),
            }
        elif event_type == "bridge.wallet.unlocked":
            locks.pop(key, None)
    return sorted(locks.values(), key=lambda item: (item.get("created_at", ""), item.get("wallet_address", "")))


def _summarize_requests(payload: dict[str, Any]) -> dict[str, Any]:
    requests = payload.get("requests")
    if not isinstance(requests, list):
        requests = []
    active_states = {"leased", "assigned", "running", "in_progress", "processing", "started"}
    terminal_states = {"completed", "failed", "cancelled", "canceled", "expired"}
    active = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        state = str(request.get("state") or request.get("status") or "").strip().lower()
        if state in active_states or (state and state not in terminal_states and bool(request.get("lease_id") or request.get("worker_node_id"))):
            active.append(request)
    return {
        "request_count": len(requests),
        "active_lease_like_request_count": len(active),
        "active_lease_like_request_ids": [
            str(item.get("request_id") or item.get("id") or "") for item in active[:20]
        ],
    }


def _unreachable_hub_summary(hub_url: str, error: str) -> dict[str, Any]:
    return {
        "hub_url": hub_url.rstrip("/"),
        "ok": False,
        "reachable": False,
        "error": error,
        "bridge_backend": {},
        "credit_status": {},
        "audit_event_count": 0,
        "audit_event_type_counts": {},
        "deposit_requested_wei": 0,
        "deposit_confirmed_wei": 0,
        "payout_requested_wei": 0,
        "payout_confirmed_wei": 0,
        "payout_failed_wei": 0,
        "confirmed_deposit_count": 0,
        "confirmed_payout_count": 0,
        "failed_payout_count": 0,
        "active_wallet_locks": [],
        "active_wallet_lock_count": 0,
        "active_holds": [],
        "active_hold_count": 0,
        "deposit_count": 0,
        "worker_earning_count": 0,
        "request_summary": {
            "request_count": 0,
            "active_lease_like_request_count": 0,
            "active_lease_like_request_ids": [],
        },
        "reconciliation": {},
        "mock_wallet_status": {},
        "tx_hash_count": 0,
        "tx_hashes": [],
    }


def _summarize_hub(
    hub_url: str,
    *,
    config: HubBridgeAuditConfig,
    fetch_json: FetchJson = _json_get,
) -> dict[str, Any]:
    params = {
        "limit": config.limit,
        "account_id": config.account_id,
        "wallet_address": config.wallet_address,
        "worker_node_id": config.worker_node_id,
    }
    status = fetch_json(hub_url, "/api/hub/v1/status", {}, config.timeout_seconds)
    credits = fetch_json(hub_url, "/api/hub/v1/credits", {}, config.timeout_seconds)
    audit = fetch_json(hub_url, "/api/hub/v1/bridge/audit", params, config.timeout_seconds)
    holds = fetch_json(
        hub_url,
        "/api/hub/v1/credits/holds",
        {"limit": config.limit, "account_id": config.account_id, "active": "1"},
        config.timeout_seconds,
    )
    deposits = fetch_json(
        hub_url,
        "/api/hub/v1/credits/deposits",
        {"limit": config.limit, "account_id": config.account_id},
        config.timeout_seconds,
    )
    earnings = fetch_json(
        hub_url,
        "/api/hub/v1/credits/worker-earnings",
        {"limit": config.limit, "worker_node_id": config.worker_node_id},
        config.timeout_seconds,
    )
    requests = fetch_json(hub_url, "/api/hub/v1/requests", {"limit": config.limit}, config.timeout_seconds)
    reconciliation = fetch_json(
        hub_url,
        "/api/hub/v1/credits/bridge-reconciliation",
        {"account_id": config.account_id},
        config.timeout_seconds,
    )
    mock_wallets: dict[str, Any] = {}
    try:
        mock_wallets = fetch_json(
            hub_url,
            "/api/hub/v1/bridge/mock-chain/wallets",
            {"wallet_address": config.wallet_address},
            config.timeout_seconds,
        )
    except HubBridgeAuditCliError as exc:
        mock_wallets = {"ok": False, "error": str(exc)}

    events_raw = audit.get("events", [])
    events = [event for event in events_raw if isinstance(event, dict)]
    active_locks = _infer_active_wallet_locks(events)
    active_holds = [hold for hold in holds.get("holds", []) if isinstance(hold, dict)]
    event_counts = _event_type_counts(events)
    tx_hashes = sorted(_extract_tx_hashes(events))

    return {
        "hub_url": hub_url.rstrip("/"),
        "ok": bool(status),
        "reachable": True,
        "bridge_backend": status.get("bridge_backend", {}),
        "credit_status": {
            key: credits.get(key)
            for key in (
                "account_count",
                "transaction_count",
                "hold_count",
                "deposit_count",
                "worker_earning_count",
                "unit",
            )
            if key in credits
        },
        "audit_event_count": len(events),
        "audit_event_type_counts": event_counts,
        "deposit_requested_wei": _event_amount_sum(events, "bridge.deposit.requested"),
        "deposit_confirmed_wei": _event_amount_sum(events, "bridge.deposit.confirmed"),
        "payout_requested_wei": _event_amount_sum(events, "bridge.payout.requested"),
        "payout_confirmed_wei": _event_amount_sum(events, "bridge.payout.confirmed"),
        "payout_failed_wei": _event_amount_sum(events, "bridge.payout.failed"),
        "confirmed_deposit_count": event_counts.get("bridge.deposit.confirmed", 0),
        "confirmed_payout_count": event_counts.get("bridge.payout.confirmed", 0),
        "failed_payout_count": event_counts.get("bridge.payout.failed", 0),
        "deposit_confirmed_reference_ids": _event_reference_ids(events, "bridge.deposit.confirmed"),
        "payout_confirmed_reference_ids": _event_reference_ids(events, "bridge.payout.confirmed"),
        "payout_failed_reference_ids": _event_reference_ids(events, "bridge.payout.failed"),
        "active_wallet_locks": active_locks,
        "active_wallet_lock_count": len(active_locks),
        "active_holds": active_holds,
        "active_hold_count": len(active_holds),
        "deposit_count": _safe_int(deposits.get("deposit_count")),
        "worker_earning_count": _safe_int(earnings.get("worker_earning_count")),
        "request_summary": _summarize_requests(requests),
        "reconciliation": reconciliation,
        "mock_wallet_status": {
            "wallet_count": mock_wallets.get("wallet_count"),
            "total_available_credit_wei": mock_wallets.get("total_available_credit_wei"),
            "error": mock_wallets.get("error"),
        },
        "tx_hash_count": len(tx_hashes),
        "tx_hashes": tx_hashes,
    }


def _load_report(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HubBridgeAuditCliError(f"Could not read stress report {path}: {exc}") from exc
    return payload if isinstance(payload, dict) else None


def _movement_amount(movement: Any) -> int:
    if not isinstance(movement, dict):
        return 0
    return _safe_int(movement.get("amount_units"))


def _report_bridge_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {"available": False}
    movements = report.get("dev_chain_bridge_movements")
    if not isinstance(movements, dict):
        rollup = report.get("dev_chain_rollup")
        movements = rollup.get("bridge_movements", {}) if isinstance(rollup, dict) else {}
    movements = movements if isinstance(movements, dict) else {}

    deposit_total = _sum_prefixed_movements(movements, "requester_")
    payout_total = _sum_prefixed_movements(movements, "worker_")
    tx_hashes: set[str] = set()
    for movement in movements.values():
        tx_hashes.update(_extract_tx_hashes(movement))

    failed_payout_chain_movement_count = _count_named_movements(movements, include="failed", prefix="worker_")

    rollup = report.get("dev_chain_rollup") if isinstance(report.get("dev_chain_rollup"), dict) else {}
    escrow_address = str(report.get("dev_chain_escrow_address") or rollup.get("escrow_address") or "")
    deltas = rollup.get("balance_deltas_wei") if isinstance(rollup.get("balance_deltas_wei"), dict) else {}
    observed_escrow_delta = _safe_int(deltas.get(escrow_address)) if escrow_address else 0
    expected_escrow_delta = deposit_total - payout_total

    random_summary = rollup.get("random_bridge_event_summary") if isinstance(rollup.get("random_bridge_event_summary"), dict) else {}
    if not random_summary and isinstance(report.get("bridge_random_event_summary"), dict):
        random_summary = report.get("bridge_random_event_summary", {})
    random_summary = random_summary if isinstance(random_summary, dict) else {}

    intentional_failed_payout_count = _first_nonzero_int(
        random_summary.get("payout_failed_count"),
        report.get("bridge_random_payout_failed_count"),
        rollup.get("bridge_random_payout_failed_count"),
    )
    intentional_failed_payout_requested_count = _first_nonzero_int(
        random_summary.get("payout_failed_requested_count"),
        report.get("bridge_random_payout_failed_requested_count"),
        rollup.get("bridge_random_payout_failed_requested_count"),
        intentional_failed_payout_count,
    )
    funding_confirmed_count = _first_nonzero_int(
        random_summary.get("funding_confirmed_count"),
        report.get("bridge_random_funding_event_count"),
        rollup.get("bridge_random_funding_event_count"),
    )
    payout_confirmed_count = _first_nonzero_int(
        random_summary.get("payout_confirmed_count"),
        report.get("bridge_random_payout_confirmed_count"),
        rollup.get("bridge_random_payout_confirmed_count"),
    )
    active_work_rejection_count = _first_nonzero_int(
        random_summary.get("active_work_payout_rejection_count"),
        report.get("bridge_active_work_payout_rejection_count"),
        rollup.get("bridge_active_work_payout_rejection_count"),
    )

    escrow_matches = expected_escrow_delta == observed_escrow_delta
    return {
        "available": True,
        "report_run_id": str(report.get("run_id") or ""),
        "bridge_backend": str(report.get("bridge_backend") or ""),
        "dev_chain_run_id": str(report.get("dev_chain_run_id") or ""),
        "escrow_address": escrow_address,
        "deposit_units_from_movements": deposit_total,
        "payout_units_from_movements": payout_total,
        "expected_escrow_delta": expected_escrow_delta,
        "observed_escrow_delta": observed_escrow_delta,
        "escrow_delta_matches": escrow_matches,
        "balance_delta_nonzero_count": _safe_int(report.get("dev_chain_balance_delta_nonzero_count")),
        "random_bridge_event_summary": random_summary,
        "intentional_failed_payout_count": intentional_failed_payout_count,
        "intentional_failed_payout_requested_count": intentional_failed_payout_requested_count,
        "funding_confirmed_count": funding_confirmed_count,
        "payout_confirmed_count": payout_confirmed_count,
        "active_work_payout_rejection_count": active_work_rejection_count,
        "failed_payout_chain_movement_count": failed_payout_chain_movement_count,
        "failed_payouts_have_chain_movements": failed_payout_chain_movement_count > 0,
        "movement_count": len(movements),
        "tx_hash_count": len(tx_hashes),
        "tx_hashes": sorted(tx_hashes),
        "invariants": {
            "deposit_units": deposit_total,
            "confirmed_payout_units": payout_total,
            "expected_escrow_delta": expected_escrow_delta,
            "observed_escrow_delta": observed_escrow_delta,
            "escrow_delta_matches": escrow_matches,
            "failed_payout_chain_movement_count": failed_payout_chain_movement_count,
            "failed_payouts_have_chain_movements": failed_payout_chain_movement_count > 0,
        },
    }



def _build_failure_mode_summary(
    *,
    totals: dict[str, Any],
    report_summary: dict[str, Any],
    live_hub_count: int,
) -> dict[str, Any]:
    """Classify expected failure coverage separately from unresolved failure modes."""

    intentional_failed = _safe_int(report_summary.get("intentional_failed_payout_count"))
    live_failed = _safe_int(totals.get("unique_failed_payout_count"))
    observed_failed = live_failed if live_hub_count else intentional_failed
    unexpected_failed = max(0, observed_failed - intentional_failed) if report_summary.get("available") else observed_failed

    active_wallet_locks = _safe_int(totals.get("active_wallet_lock_count"))
    active_holds = _safe_int(totals.get("active_hold_count"))
    active_lease_like = _safe_int(totals.get("active_lease_like_request_count"))
    escrow_matches = bool(report_summary.get("escrow_delta_matches")) if report_summary.get("available") else None
    failed_chain_movements = _safe_int(report_summary.get("failed_payout_chain_movement_count"))

    if active_wallet_locks or active_holds or active_lease_like:
        bridge_run_health = "stuck-state-detected"
    elif escrow_matches is False:
        bridge_run_health = "escrow-mismatch"
    elif failed_chain_movements:
        bridge_run_health = "failed-payout-chain-movement-detected"
    elif unexpected_failed:
        bridge_run_health = "unexpected-failed-payouts"
    elif not report_summary.get("available") and not live_hub_count:
        bridge_run_health = "insufficient-data"
    else:
        bridge_run_health = "clean"

    return {
        "bridge_run_health": bridge_run_health,
        "intentional_failed_payouts": intentional_failed,
        "observed_failed_payouts": observed_failed,
        "unexpected_failed_payouts": unexpected_failed,
        "active_wallet_locks_remaining": active_wallet_locks,
        "active_holds_remaining": active_holds,
        "active_lease_like_requests": active_lease_like,
        "escrow_matches": escrow_matches,
        "failed_payout_chain_movement_count": failed_chain_movements,
        "failed_payouts_have_chain_movements": bool(report_summary.get("failed_payouts_have_chain_movements")),
        "intentional_failure_coverage_seen": intentional_failed > 0,
        "live_failed_payouts_available": live_hub_count > 0,
    }



def build_audit_report(
    config: HubBridgeAuditConfig,
    *,
    fetch_json: FetchJson = _json_get,
) -> dict[str, Any]:
    stress_report = _load_report(config.report_path)
    report_summary = _report_bridge_summary(stress_report)

    hubs: list[dict[str, Any]] = []
    hub_errors: list[str] = []
    if not config.offline:
        for hub_url in config.hub_urls:
            try:
                hubs.append(_summarize_hub(hub_url, config=config, fetch_json=fetch_json))
            except HubBridgeAuditCliError as exc:
                if config.require_live_hubs:
                    raise
                hub_errors.append(f"{hub_url.rstrip('/')}: {exc}")
                hubs.append(_unreachable_hub_summary(hub_url, str(exc)))

    reachable_hubs = [hub for hub in hubs if bool(hub.get("reachable"))]
    unique_confirmed_deposit_refs = {
        ref for hub in reachable_hubs for ref in hub.get("deposit_confirmed_reference_ids", []) if isinstance(ref, str) and ref
    }
    unique_confirmed_payout_refs = {
        ref for hub in reachable_hubs for ref in hub.get("payout_confirmed_reference_ids", []) if isinstance(ref, str) and ref
    }
    unique_failed_payout_refs = {
        ref for hub in reachable_hubs for ref in hub.get("payout_failed_reference_ids", []) if isinstance(ref, str) and ref
    }
    totals = {
        "audit_event_count": sum(_safe_int(hub.get("audit_event_count")) for hub in reachable_hubs),
        "active_wallet_lock_count": sum(_safe_int(hub.get("active_wallet_lock_count")) for hub in reachable_hubs),
        "active_hold_count": sum(_safe_int(hub.get("active_hold_count")) for hub in reachable_hubs),
        "active_lease_like_request_count": sum(
            _safe_int(hub.get("request_summary", {}).get("active_lease_like_request_count"))
            for hub in reachable_hubs
            if isinstance(hub.get("request_summary"), dict)
        ),
        "confirmed_deposit_count": sum(_safe_int(hub.get("confirmed_deposit_count")) for hub in reachable_hubs),
        "confirmed_payout_count": sum(_safe_int(hub.get("confirmed_payout_count")) for hub in reachable_hubs),
        "failed_payout_count": sum(_safe_int(hub.get("failed_payout_count")) for hub in reachable_hubs),
        "unique_confirmed_deposit_count": len(unique_confirmed_deposit_refs),
        "unique_confirmed_payout_count": len(unique_confirmed_payout_refs),
        "unique_failed_payout_count": len(unique_failed_payout_refs),
        "unique_failed_payout_reference_ids": sorted(unique_failed_payout_refs),
        "tx_hash_count": len({tx for hub in reachable_hubs for tx in hub.get("tx_hashes", []) if isinstance(tx, str)}),
        "reachable_hub_count": len(reachable_hubs),
        "unreachable_hub_count": len(hub_errors),
    }

    failure_modes = _build_failure_mode_summary(
        totals=totals,
        report_summary=report_summary,
        live_hub_count=len(reachable_hubs),
    )

    ok = True
    warnings: list[str] = []
    notes: list[str] = []

    if config.offline:
        notes.append("offline mode enabled; skipped live Hub HTTP inspection")
    elif hub_errors:
        if report_summary.get("available"):
            notes.append(
                f"{len(hub_errors)} live Hub(s) were unreachable; using the saved stress report for offline bridge movement checks"
            )
        else:
            ok = False
            warnings.append(
                f"{len(hub_errors)} live Hub(s) were unreachable and no stress report was available for offline audit"
            )

    if report_summary.get("available") and not report_summary.get("escrow_delta_matches"):
        ok = False
        warnings.append("dev-chain escrow delta does not match movement totals")
    if totals["active_wallet_lock_count"]:
        ok = False
        warnings.append(f"{totals['active_wallet_lock_count']} active wallet lock(s) inferred from audit events")
    if totals["active_hold_count"]:
        ok = False
        warnings.append(f"{totals['active_hold_count']} active credit hold(s) reported")
    if totals["active_lease_like_request_count"]:
        ok = False
        warnings.append(f"{totals['active_lease_like_request_count']} active lease-like request(s) reported")
    if failure_modes["unexpected_failed_payouts"]:
        ok = False
        warnings.append(
            f"{failure_modes['unexpected_failed_payouts']} unexpected failed payout(s) beyond intentional stress coverage"
        )
    if failure_modes["failed_payout_chain_movement_count"]:
        ok = False
        warnings.append("failed payout movement unexpectedly appears in dev-chain bridge movements")
    if config.offline and not report_summary.get("available"):
        ok = False
        warnings.append("offline mode requested but no stress report was available")

    if failure_modes["intentional_failure_coverage_seen"] and not failure_modes["unexpected_failed_payouts"]:
        notes.append(
            f"{failure_modes['intentional_failed_payouts']} intentional failed payout(s) were exercised as stress coverage"
        )
    if failure_modes["bridge_run_health"] == "clean":
        notes.append("bridge failure-mode classification is clean: no unexpected failures, stuck locks, active holds, or escrow mismatch")

    return {
        "ok": ok,
        "namespace": config.namespace,
        "hub_count": len(hubs),
        "live_hub_count": len(reachable_hubs),
        "unreachable_hub_count": len(hub_errors),
        "offline": bool(config.offline),
        "hubs": hubs,
        "hub_errors": hub_errors,
        "totals": totals,
        "stress_report": report_summary,
        "invariants": report_summary.get("invariants", {}) if isinstance(report_summary, dict) else {},
        "failure_modes": failure_modes,
        "warnings": warnings,
        "notes": notes,
    }

def render_text_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Hub bridge audit summary")
    if report.get("namespace"):
        lines.append(f"namespace: {report['namespace']}")
    lines.append(f"ok: {bool(report.get('ok'))}")
    totals = report.get("totals", {}) if isinstance(report.get("totals"), dict) else {}
    lines.append(
        "totals: "
        f"reachable_hubs={_safe_int(totals.get('reachable_hub_count'))} "
        f"unreachable_hubs={_safe_int(totals.get('unreachable_hub_count'))} "
        f"audit_events={_safe_int(totals.get('audit_event_count'))} "
        f"active_wallet_locks={_safe_int(totals.get('active_wallet_lock_count'))} "
        f"active_holds={_safe_int(totals.get('active_hold_count'))} "
        f"active_lease_like_requests={_safe_int(totals.get('active_lease_like_request_count'))} "
        f"confirmed_deposits={_safe_int(totals.get('confirmed_deposit_count'))} "
        f"confirmed_payouts={_safe_int(totals.get('confirmed_payout_count'))} "
        f"failed_payouts={_safe_int(totals.get('failed_payout_count'))} "
        f"tx_hashes={_safe_int(totals.get('tx_hash_count'))}"
    )

    stress = report.get("stress_report", {}) if isinstance(report.get("stress_report"), dict) else {}
    if stress.get("available"):
        lines.append(
            "stress_report: "
            f"run_id={stress.get('report_run_id')} "
            f"backend={stress.get('bridge_backend')} "
            f"dev_chain_run_id={stress.get('dev_chain_run_id')}"
        )
        lines.append(
            "escrow: "
            f"address={stress.get('escrow_address')} "
            f"expected_delta={stress.get('expected_escrow_delta')} "
            f"observed_delta={stress.get('observed_escrow_delta')} "
            f"matches={bool(stress.get('escrow_delta_matches'))}"
        )
        random_summary = stress.get("random_bridge_event_summary", {})
        if isinstance(random_summary, dict) and random_summary:
            lines.append("random_bridge_events: " + json.dumps(random_summary, sort_keys=True))
    else:
        lines.append("stress_report: unavailable")

    invariants = report.get("invariants", {}) if isinstance(report.get("invariants"), dict) else {}
    if invariants:
        lines.append(
            "invariants: "
            f"deposit_units={_safe_int(invariants.get('deposit_units'))} "
            f"confirmed_payout_units={_safe_int(invariants.get('confirmed_payout_units'))} "
            f"expected_escrow_delta={_safe_int(invariants.get('expected_escrow_delta'))} "
            f"observed_escrow_delta={_safe_int(invariants.get('observed_escrow_delta'))} "
            f"escrow_matches={bool(invariants.get('escrow_delta_matches'))} "
            f"failed_payout_chain_movements={_safe_int(invariants.get('failed_payout_chain_movement_count'))}"
        )

    failure_modes = report.get("failure_modes", {}) if isinstance(report.get("failure_modes"), dict) else {}
    if failure_modes:
        lines.append(
            "failure_modes: "
            f"health={failure_modes.get('bridge_run_health')} "
            f"intentional_failed_payouts={_safe_int(failure_modes.get('intentional_failed_payouts'))} "
            f"observed_failed_payouts={_safe_int(failure_modes.get('observed_failed_payouts'))} "
            f"unexpected_failed_payouts={_safe_int(failure_modes.get('unexpected_failed_payouts'))} "
            f"active_wallet_locks_remaining={_safe_int(failure_modes.get('active_wallet_locks_remaining'))} "
            f"active_holds_remaining={_safe_int(failure_modes.get('active_holds_remaining'))} "
            f"active_lease_like_requests={_safe_int(failure_modes.get('active_lease_like_requests'))}"
        )

    for index, hub in enumerate(report.get("hubs", []) if isinstance(report.get("hubs"), list) else [], start=1):
        if not isinstance(hub, dict):
            continue
        if not bool(hub.get("reachable", True)):
            lines.append(f"hub[{index}]: {hub.get('hub_url')} unreachable")
            if hub.get("error"):
                lines.append(f"  error: {hub.get('error')}")
            continue
        backend = hub.get("bridge_backend")
        backend_name = backend.get("backend") if isinstance(backend, dict) else backend
        lines.append(f"hub[{index}]: {hub.get('hub_url')} backend={backend_name}")
        lines.append(
            "  bridge: "
            f"audit_events={hub.get('audit_event_count')} "
            f"confirmed_deposits={hub.get('confirmed_deposit_count')} "
            f"confirmed_payouts={hub.get('confirmed_payout_count')} "
            f"failed_payouts={hub.get('failed_payout_count')} "
            f"tx_hashes={hub.get('tx_hash_count')}"
        )
        lines.append(
            "  stuck: "
            f"active_wallet_locks={hub.get('active_wallet_lock_count')} "
            f"active_holds={hub.get('active_hold_count')} "
            f"active_lease_like_requests={hub.get('request_summary', {}).get('active_lease_like_request_count', 0) if isinstance(hub.get('request_summary'), dict) else 0}"
        )
        event_counts = hub.get("audit_event_type_counts", {})
        if isinstance(event_counts, dict) and event_counts:
            lines.append("  audit_event_types: " + json.dumps(event_counts, sort_keys=True))
        if hub.get("active_wallet_locks"):
            lines.append("  active_wallet_locks: " + json.dumps(hub.get("active_wallet_locks"), sort_keys=True))
        if hub.get("active_holds"):
            lines.append("  active_holds: " + json.dumps(hub.get("active_holds"), sort_keys=True))
    notes = report.get("notes")
    if isinstance(notes, list) and notes:
        lines.append("notes:")
        lines.extend(f"  - {note}" for note in notes)
    warnings = report.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.append("warnings:")
        lines.extend(f"  - {warning}" for warning in warnings)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only audit summary for Hub bridge/FDB/dev-chain stress state.")
    parser.add_argument("--hub-url", action="append", default=None, help="Hub URL to inspect. May be repeated. Defaults to stress Hub A and B.")
    parser.add_argument("--hub-a-url", default="", help="Convenience alias for a Hub A URL.")
    parser.add_argument("--hub-b-url", default="", help="Convenience alias for a Hub B URL.")
    parser.add_argument("--namespace", default="", help="Optional namespace label to include in the report.")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_STRESS_REPORT_PATH, help="Stress report JSON to use for dev-chain balance rollup checks.")
    parser.add_argument("--no-report", action="store_true", help="Do not read a stress report JSON.")
    parser.add_argument("--offline", action="store_true", help="Skip live Hub HTTP calls and audit from the saved stress report only.")
    parser.add_argument("--require-live-hubs", action="store_true", help="Fail if any configured Hub URL is unreachable.")
    parser.add_argument("--account-id", default="", help="Filter account-scoped endpoints/audit events.")
    parser.add_argument("--wallet-address", default="", help="Filter wallet-scoped bridge audit/wallet endpoints.")
    parser.add_argument("--worker-node-id", default="", help="Filter worker-scoped bridge audit/earnings endpoints.")
    parser.add_argument("--limit", type=int, default=500, help="Maximum records to request from each list endpoint.")
    parser.add_argument("--timeout-seconds", type=float, default=5.0, help="HTTP timeout per request.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of text.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when inferred stuck state or escrow mismatch is detected.")
    return parser


def _config_from_args(args: argparse.Namespace) -> HubBridgeAuditConfig:
    urls: list[str] = []
    if args.hub_url:
        urls.extend(str(url) for url in args.hub_url if str(url).strip())
    if str(args.hub_a_url or "").strip():
        urls.append(str(args.hub_a_url).strip())
    if str(args.hub_b_url or "").strip():
        urls.append(str(args.hub_b_url).strip())
    if not urls:
        urls = [DEFAULT_STRESS_A_URL, DEFAULT_STRESS_B_URL]
    return HubBridgeAuditConfig(
        hub_urls=tuple(dict.fromkeys(url.rstrip("/") for url in urls)),
        namespace=str(args.namespace or ""),
        report_path=None if args.no_report else args.report_path,
        account_id=str(args.account_id or ""),
        wallet_address=str(args.wallet_address or ""),
        worker_node_id=str(args.worker_node_id or ""),
        limit=max(1, int(args.limit or 500)),
        timeout_seconds=max(1.0, float(args.timeout_seconds or 5.0)),
        output="json" if args.json else "text",
        strict=bool(args.strict),
        offline=bool(args.offline),
        require_live_hubs=bool(args.require_live_hubs),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _config_from_args(args)
    try:
        report = build_audit_report(config)
    except HubBridgeAuditCliError as exc:
        print(f"FAIL: Hub bridge audit failed: {exc}", file=sys.stderr)
        return 2
    if config.output == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text_report(report))
    if config.strict and not bool(report.get("ok")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
