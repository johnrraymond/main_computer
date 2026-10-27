#!/usr/bin/env python3
"""Read-only Stable Hub Testnet Captain credit-hold diagnosis.

This audit never signs, deposits, submits work, reconciles funds, or settles
sessions. The canonical Stable Hub exposes its FDB-backed credit records under
GET /api/hub/v1/payout/status, NOT the legacy GET /api/hub/v1/credits API.

A held reservation does not establish that an accepted work session is safe to
release. Session reconciliation is a separate, authorization-gated operation.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from main_computer.captain_cli import (
    CaptainCliError,
    _apply_captain_network_defaults,
    _fetch_hub_credit_balance,
    _get_hub_json,
    build_captain_options_parser,
    build_captain_runtime,
    plan_captain_bounded_bridge_top_up,
)
from main_computer.config import MainComputerConfig
from main_computer.hub_credit_indexer import wallet_account_id
from tools.worker_captain_funding_preflight import TESTNET_HUB

MAX_HOLDS_TO_INSPECT = 50
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class HoldAuditError(ValueError):
    """The available evidence is insufficient to infer a safe release."""


def _credit_wei(value: Any) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise HoldAuditError("inexact_credit_wei")
    return int(value)


def reconcile_read_only(balance: dict, ledger: dict, *, wallet_address: str) -> dict:
    """Cross-check wallet balance against Stable Hub payout status.

    The GET /api/hub/v1/payout/status response includes a `payout` mapping
    containing `accounts` and `holds`. Read snapshots can legitimately race a
    settlement; disagreement is reason to stop and re-read, never to mutate.
    """
    try:
        validated = plan_captain_bounded_bridge_top_up(balance, wallet_address=wallet_address)
    except (CaptainCliError, TypeError, ValueError, KeyError) as exc:
        raise HoldAuditError("unverified_wallet_balance") from exc
    if not isinstance(balance, dict):
        raise HoldAuditError("unverified_wallet_balance")

    # The Captain balance endpoint itself must prove the requested account ID.
    account_id = balance.get("account_id")
    if not isinstance(account_id, str) or account_id != wallet_account_id(wallet_address):
        raise HoldAuditError("missing_or_mismatched_account_identity")
    source_account = balance.get("account")
    if not isinstance(source_account, dict) or source_account.get("account_id") != account_id:
        raise HoldAuditError("missing_or_mismatched_account_data")
    held = _credit_wei(source_account.get("held_credit_wei"))
    spent = _credit_wei(source_account.get("spent_credit_wei"))
    available = validated["available_credit_wei"]
    completed = validated["bridge_completed_credit_wei"]

    # Stable Hub's v2 read-only projection is nested under `payout`; the
    # legacy /credits status JSON does not contain account/hold records.
    if not isinstance(ledger, dict) or ledger.get("ok") is not True:
        raise HoldAuditError("hub_payout_status_unavailable")
    payout = ledger.get("payout")
    if (not isinstance(payout, dict) or payout.get("ok") is not True
            or not isinstance(payout.get("holds"), list)
            or not isinstance(payout.get("accounts"), list)):
        raise HoldAuditError("hub_payout_records_unavailable")

    matching_accounts = [account for account in payout["accounts"]
                         if isinstance(account, dict) and account.get("account_id") == account_id]
    if len(matching_accounts) != 1:
        raise HoldAuditError("ledger_account_not_unique")
    ledger_account = matching_accounts[0]
    if (str(ledger_account.get("wallet_address") or "").lower() != wallet_address.lower()
            or _credit_wei(ledger_account.get("available_credit_wei")) != available
            or _credit_wei(ledger_account.get("held_credit_wei")) != held
            or _credit_wei(ledger_account.get("spent_credit_wei")) != spent
            or _credit_wei(ledger_account.get("bridge_completed_credit_wei")) != completed):
        raise HoldAuditError("wallet_ledger_views_disagree")

    active: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for record in payout["holds"]:
        if not isinstance(record, dict) or record.get("account_id") != account_id:
            continue
        if str(record.get("wallet_address") or "").lower() != wallet_address.lower():
            raise HoldAuditError("cross_wallet_hold_for_account")
        if record.get("status") != "held":
            continue
        hold_id = record.get("hold_id")
        request_id = record.get("request_id")
        session_id = record.get("session_id")
        if (not isinstance(hold_id, str) or not hold_id or hold_id in seen_ids
                or not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id)
                or not isinstance(session_id, str) or not session_id):
            raise HoldAuditError("invalid_held_reservation_identity")
        seen_ids.add(hold_id)
        amount = _credit_wei(record.get("credit_wei"))
        if amount <= 0:
            raise HoldAuditError("invalid_held_reservation_amount")
        active.append({
            "holdId": hold_id,
            "requestId": request_id,
            "sessionId": session_id,
            "creditWei": str(amount),
            "createdAt": str(record.get("created_at") or ""),
            "sessionState": "not_verified",
        })
        if len(active) > MAX_HOLDS_TO_INSPECT:
            raise HoldAuditError("too_many_active_holds_for_bounded_audit")

    active.sort(key=lambda item: (item["createdAt"], item["holdId"]))
    if sum(int(item["creditWei"]) for item in active) != held:
        raise HoldAuditError("held_credit_does_not_match_reservations")

    return {
        "ok": True,
        "readOnly": True,
        "network": "testnet",
        "ledgerSource": "/api/hub/v1/payout/status",
        "availableCreditWei": str(available),
        "heldCreditWei": str(held),
        "spentCreditWei": str(spent),
        "bridgeCompletedCreditWei": str(completed),
        "unexplainedFundingDeltaWei": str(completed - available - held - spent),
        "activeHoldCount": len(active),
        "activeHolds": active,
        "verdict": ("outstanding_holds_require_session_reconciliation"
                    if active else "no_active_holds"),
        "releaseAuthorized": False,
        "workerCycleCertified": False,
    }


def probe_live(*, repo_root: Path,
               hub_get: Callable[..., dict] = _get_hub_json) -> dict:
    try:
        options = build_captain_options_parser().parse_args(["--network", "testnet"])
        config = MainComputerConfig.from_env()
        _apply_captain_network_defaults(options, base_config=config, cwd=repo_root)
        runtime = build_captain_runtime(config, options=options,
                                        selector="captain", cwd=repo_root)
    except Exception as exc:
        raise HoldAuditError("captain_configuration_unavailable") from exc
    if (runtime.network != "testnet" or runtime.config.hub_url.rstrip("/") != TESTNET_HUB
            or runtime.chain_id != 42424241):
        raise HoldAuditError("testnet_identity_mismatch")
    try:
        balance = _fetch_hub_credit_balance(TESTNET_HUB,
                                            wallet_address=runtime.wallet.address, timeout_s=15)
    except Exception as exc:
        raise HoldAuditError("testnet_balance_unavailable") from exc
    try:
        # This endpoint is implemented by Stable Hub. The similarly named
        # /api/hub/v1/credits route belongs to the legacy Hub API.
        ledger = hub_get(TESTNET_HUB, "/api/hub/v1/payout/status", timeout_s=15)
    except Exception as exc:
        raise HoldAuditError("hub_payout_status_unavailable") from exc
    return reconcile_read_only(balance, ledger, wallet_address=runtime.wallet.address)


def main() -> int:
    try:
        evidence = probe_live(repo_root=Path(__file__).resolve().parents[1])
    except HoldAuditError as exc:
        evidence = {"ok": False, "readOnly": True, "releaseAuthorized": False,
                    "reason": str(exc)}
    except Exception:
        evidence = {"ok": False, "readOnly": True, "releaseAuthorized": False,
                    "reason": "hold_audit_internal_error"}
    print(json.dumps(evidence, sort_keys=True, indent=2))
    return 0 if evidence.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
