from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from main_computer.hub_credit_models import (
    CREDIT_LEDGER_VERSION,
    CREDIT_UNIT_KEY,
    CREDIT_UNIT_NAME,
    ChainEventRef,
    CreditDeposit,
    HubCreditAccount,
    HubCreditHold,
    HubCreditTransaction,
    RequestCharge,
    WorkerEarning,
    clean_account_id,
    clean_worker_id,
    make_worker_commitment,
    positive_int,
    stable_id,
    utc_now,
)


HUB_CREDIT_LEDGER_STORE_VERSION = "hub-credit-ledger-store-v1"


def _copy_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _account_from_dict(payload: dict[str, Any]) -> HubCreditAccount:
    return HubCreditAccount(
        account_id=str(payload.get("account_id", "")),
        owner_address=str(payload.get("owner_address", "")),
        available_credits=positive_int(payload.get("available_credits")),
        held_credits=positive_int(payload.get("held_credits")),
        spent_credits=positive_int(payload.get("spent_credits")),
        earned_credits=positive_int(payload.get("earned_credits")),
        created_at=str(payload.get("created_at", "")),
        updated_at=str(payload.get("updated_at", "")),
        metadata=_copy_dict(payload.get("metadata")),
    )


def _transaction_from_dict(payload: dict[str, Any]) -> HubCreditTransaction:
    return HubCreditTransaction(
        transaction_id=str(payload.get("transaction_id", "")),
        account_id=str(payload.get("account_id", "")),
        transaction_type=str(payload.get("transaction_type", "")),
        credits=positive_int(payload.get("credits")),
        created_at=str(payload.get("created_at", "")),
        request_id=str(payload.get("request_id", "")),
        worker_node_id=str(payload.get("worker_node_id", "")),
        batch_id=str(payload.get("batch_id", "")),
        deposit_id=str(payload.get("deposit_id", "")),
        hold_id=str(payload.get("hold_id", "")),
        memo=str(payload.get("memo", "")),
        metadata=_copy_dict(payload.get("metadata")),
    )


def _deposit_from_dict(payload: dict[str, Any]) -> CreditDeposit:
    return CreditDeposit(
        deposit_id=str(payload.get("deposit_id", "")),
        account_id=str(payload.get("account_id", "")),
        payer_address=str(payload.get("payer_address", "")),
        payment_asset=str(payload.get("payment_asset", "native")),
        payment_amount_base_units=positive_int(payload.get("payment_amount_base_units")),
        credits_granted=positive_int(payload.get("credits_granted")),
        chain_event=ChainEventRef.from_dict(_copy_dict(payload.get("chain_event"))),
        status=str(payload.get("status", "indexed")),
        memo=str(payload.get("memo", "")),
        created_at=str(payload.get("created_at", "")),
    )


def _hold_from_dict(payload: dict[str, Any]) -> HubCreditHold:
    return HubCreditHold(
        hold_id=str(payload.get("hold_id", "")),
        account_id=str(payload.get("account_id", "")),
        request_id=str(payload.get("request_id", "")),
        credits=positive_int(payload.get("credits")),
        status=str(payload.get("status", "held")),
        created_at=str(payload.get("created_at", "")),
        expires_at=str(payload.get("expires_at", "")),
        released_at=str(payload.get("released_at", "")),
        charged_at=str(payload.get("charged_at", "")),
    )


def _charge_from_dict(payload: dict[str, Any]) -> RequestCharge:
    return RequestCharge(
        charge_id=str(payload.get("charge_id", "")),
        account_id=str(payload.get("account_id", "")),
        request_id=str(payload.get("request_id", "")),
        hold_id=str(payload.get("hold_id", "")),
        charged_credits=positive_int(payload.get("charged_credits")),
        released_credits=positive_int(payload.get("released_credits")),
        worker_earning_id=str(payload.get("worker_earning_id", "")),
        created_at=str(payload.get("created_at", "")),
    )


def _earning_from_dict(payload: dict[str, Any]) -> WorkerEarning:
    return WorkerEarning(
        earning_id=str(payload.get("earning_id", "")),
        worker_node_id=str(payload.get("worker_node_id", "")),
        request_id=str(payload.get("request_id", "")),
        credits=positive_int(payload.get("credits")),
        worker_commitment=str(payload.get("worker_commitment", "")),
        status=str(payload.get("status", "earned")),
        batch_id=str(payload.get("batch_id", "")),
        created_at=str(payload.get("created_at", "")),
    )


class HubCreditLedger:
    """JSON-backed internal Compute Credit ledger.

    R1 intentionally keeps this off-chain. On-chain purchase events can be
    imported later by an indexer, but request charges and worker earnings remain
    internal hub accounting until explicit settlement phases are implemented.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "ledger.json"
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self, *, recent_limit: int = 25) -> dict[str, Any]:
        data = self._load()
        accounts = [_account_from_dict(item) for item in data["accounts"].values()]
        transactions = [_transaction_from_dict(item) for item in data["transactions"]]
        deposits = [_deposit_from_dict(item) for item in data["deposits"].values()]
        holds = [_hold_from_dict(item) for item in data["holds"].values()]
        charges = [_charge_from_dict(item) for item in data["charges"].values()]
        worker_earnings = [_earning_from_dict(item) for item in data["worker_earnings"].values()]
        return {
            "ok": True,
            "unit": {"name": CREDIT_UNIT_NAME, "key": CREDIT_UNIT_KEY},
            "schema_version": CREDIT_LEDGER_VERSION,
            "store_version": HUB_CREDIT_LEDGER_STORE_VERSION,
            "account_count": len(accounts),
            "deposit_count": len(deposits),
            "purchase_count": len(deposits),
            "transaction_count": len(transactions),
            "hold_count": len(holds),
            "active_hold_count": sum(1 for hold in holds if hold.status == "held"),
            "charge_count": len(charges),
            "worker_earning_count": len(worker_earnings),
            "totals": {
                "available_credits": sum(account.available_credits for account in accounts),
                "held_credits": sum(account.held_credits for account in accounts),
                "spent_credits": sum(account.spent_credits for account in accounts),
                "earned_credits": sum(account.earned_credits for account in accounts),
                "deposited_credits": sum(deposit.credits_granted for deposit in deposits),
                "purchased_credits": sum(deposit.credits_granted for deposit in deposits),
                "active_held_credits": sum(hold.credits for hold in holds if hold.status == "held"),
                "charged_credits": sum(charge.charged_credits for charge in charges),
                "worker_earned_credits": sum(earning.credits for earning in worker_earnings),
            },
            "recent_transactions": [tx.as_dict() for tx in transactions[-max(0, int(recent_limit or 0)):]][::-1],
            "recent_deposits": [deposit.as_dict() for deposit in deposits[-max(0, int(recent_limit or 0)):]][::-1],
            "recent_purchases": [deposit.as_dict() for deposit in deposits[-max(0, int(recent_limit or 0)):]][::-1],
            "recent_holds": [hold.as_dict() for hold in holds[-max(0, int(recent_limit or 0)):]][::-1],
            "recent_charges": [charge.as_dict() for charge in charges[-max(0, int(recent_limit or 0)):]][::-1],
            "recent_worker_earnings": [
                earning.as_private_dict()
                for earning in worker_earnings[-max(0, int(recent_limit or 0)):]
            ][::-1],
        }

    def get_account(self, account_id: str) -> HubCreditAccount:
        clean_id = clean_account_id(account_id)
        data = self._load()
        payload = data["accounts"].get(clean_id)
        if isinstance(payload, dict):
            return _account_from_dict(payload)
        return HubCreditAccount(account_id=clean_id)

    def list_accounts(self, *, limit: int = 100) -> list[HubCreditAccount]:
        clean_limit = min(500, max(1, int(limit or 100)))
        data = self._load()
        accounts = [_account_from_dict(item) for item in data["accounts"].values()]
        return sorted(accounts, key=lambda item: item.updated_at, reverse=True)[:clean_limit]

    def list_transactions(self, *, account_id: str = "", limit: int = 100) -> list[HubCreditTransaction]:
        clean_id = clean_account_id(account_id, default="") if account_id else ""
        clean_limit = min(500, max(1, int(limit or 100)))
        data = self._load()
        transactions = [_transaction_from_dict(item) for item in data["transactions"]]
        if clean_id:
            transactions = [tx for tx in transactions if tx.account_id == clean_id]
        return sorted(transactions, key=lambda item: item.created_at, reverse=True)[:clean_limit]

    def list_deposits(self, *, account_id: str = "", limit: int = 100) -> list[CreditDeposit]:
        clean_id = clean_account_id(account_id, default="") if account_id else ""
        clean_limit = min(500, max(1, int(limit or 100)))
        data = self._load()
        deposits = [_deposit_from_dict(item) for item in data["deposits"].values()]
        if clean_id:
            deposits = [deposit for deposit in deposits if deposit.account_id == clean_id]
        return sorted(deposits, key=lambda item: item.created_at, reverse=True)[:clean_limit]

    def list_purchases(self, *, account_id: str = "", limit: int = 100) -> list[CreditDeposit]:
        """Backward-compatible alias for deposit listings."""
        return self.list_deposits(account_id=account_id, limit=limit)

    def list_holds(
        self,
        *,
        account_id: str = "",
        request_id: str = "",
        active_only: bool = False,
        limit: int = 100,
    ) -> list[HubCreditHold]:
        clean_id = clean_account_id(account_id, default="") if account_id else ""
        clean_request = str(request_id or "").strip()
        clean_limit = min(500, max(1, int(limit or 100)))
        data = self._load()
        holds = [_hold_from_dict(item) for item in data["holds"].values()]
        if clean_id:
            holds = [hold for hold in holds if hold.account_id == clean_id]
        if clean_request:
            holds = [hold for hold in holds if hold.request_id == clean_request]
        if active_only:
            holds = [hold for hold in holds if hold.status == "held"]
        return sorted(holds, key=lambda item: item.created_at, reverse=True)[:clean_limit]

    def list_charges(
        self,
        *,
        account_id: str = "",
        request_id: str = "",
        limit: int = 100,
    ) -> list[RequestCharge]:
        clean_id = clean_account_id(account_id, default="") if account_id else ""
        clean_request = str(request_id or "").strip()
        clean_limit = min(500, max(1, int(limit or 100)))
        data = self._load()
        charges = [_charge_from_dict(item) for item in data["charges"].values()]
        if clean_id:
            charges = [charge for charge in charges if charge.account_id == clean_id]
        if clean_request:
            charges = [charge for charge in charges if charge.request_id == clean_request]
        return sorted(charges, key=lambda item: item.created_at, reverse=True)[:clean_limit]

    def list_worker_earnings(
        self,
        *,
        worker_node_id: str = "",
        request_id: str = "",
        limit: int = 100,
    ) -> list[WorkerEarning]:
        clean_worker = clean_worker_id(worker_node_id, default="") if worker_node_id else ""
        clean_request = str(request_id or "").strip()
        clean_limit = min(500, max(1, int(limit or 100)))
        data = self._load()
        earnings = [_earning_from_dict(item) for item in data["worker_earnings"].values()]
        if clean_worker:
            earnings = [earning for earning in earnings if earning.worker_node_id == clean_worker]
        if clean_request:
            earnings = [earning for earning in earnings if earning.request_id == clean_request]
        return sorted(earnings, key=lambda item: item.created_at, reverse=True)[:clean_limit]

    def bridge_reconciliation_totals(self, account_id: str) -> dict[str, Any]:
        clean_id = clean_account_id(account_id)
        data = self._load()
        transactions = [_transaction_from_dict(item) for item in data["transactions"]]
        rectifications = [
            tx for tx in transactions
            if tx.account_id == clean_id and tx.transaction_type == "bridge_spend_rectified"
        ]
        withdrawals = [
            tx for tx in transactions
            if tx.account_id == clean_id and tx.transaction_type == "withdrawal_released"
        ]
        return {
            "ok": True,
            "account_id": clean_id,
            "rectified_credits": sum(tx.credits for tx in rectifications),
            "withdrawn_credits": sum(tx.credits for tx in withdrawals),
            "rectification_count": len(rectifications),
            "withdrawal_count": len(withdrawals),
            "rectifications": [tx.as_dict() for tx in rectifications],
            "withdrawals": [tx.as_dict() for tx in withdrawals],
        }

    def record_bridge_reconciliation(
        self,
        *,
        account_id: str,
        rectified_credits: int = 0,
        withdrawn_credits: int = 0,
        rectification_id: str = "",
        withdrawal_id: str = "",
        recipient_address: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record bridge escrow reconciliation that has already landed on-chain.

        Rectification records do not change the hub balance because finalized
        charges already moved requester credits into spent credits.  Withdrawal
        records reduce available credits so funds released from escrow cannot be
        privately spent again inside the hub.
        """

        clean_id = clean_account_id(account_id)
        clean_rectified = positive_int(rectified_credits)
        clean_withdrawn = positive_int(withdrawn_credits)
        if clean_rectified <= 0 and clean_withdrawn <= 0:
            raise ValueError("rectified_credits or withdrawn_credits must be positive.")
        now = utc_now()
        base_metadata = dict(metadata or {})
        txs: list[HubCreditTransaction] = []

        with self._lock:
            data = self._load_unlocked()
            account = self._ensure_account_unlocked(data, clean_id, now=now)

            existing_transactions = [_transaction_from_dict(item) for item in data["transactions"]]
            existing_rectification = None
            if clean_rectified > 0 and rectification_id:
                existing_rectification = next(
                    (
                        tx for tx in existing_transactions
                        if tx.account_id == clean_id
                        and tx.transaction_type == "bridge_spend_rectified"
                        and str(tx.metadata.get("rectification_id", "")) == str(rectification_id)
                    ),
                    None,
                )
            existing_withdrawal = None
            if clean_withdrawn > 0 and withdrawal_id:
                existing_withdrawal = next(
                    (
                        tx for tx in existing_transactions
                        if tx.account_id == clean_id
                        and tx.transaction_type == "withdrawal_released"
                        and str(tx.metadata.get("withdrawal_id", "")) == str(withdrawal_id)
                    ),
                    None,
                )

            if clean_rectified > 0 and existing_rectification is None:
                rectification_tx = HubCreditTransaction(
                    transaction_id=stable_id(
                        "ctx",
                        {
                            "type": "bridge_spend_rectified",
                            "account_id": clean_id,
                            "rectification_id": rectification_id,
                            "credits": clean_rectified,
                        },
                    ),
                    account_id=clean_id,
                    transaction_type="bridge_spend_rectified",
                    credits=clean_rectified,
                    created_at=now,
                    memo=memo or "bridge spend rectified on-chain",
                    metadata={
                        **base_metadata,
                        "rectification_id": str(rectification_id or ""),
                    },
                )
                data["transactions"].append(rectification_tx.as_dict())
                txs.append(rectification_tx)

            if clean_withdrawn > 0 and existing_withdrawal is None:
                if account.available_credits < clean_withdrawn:
                    raise ValueError(
                        f"Cannot record withdrawal of {clean_withdrawn} credits for {clean_id}; "
                        f"only {account.available_credits} credits are available in the hub ledger."
                    )
                account = HubCreditAccount(
                    account_id=account.account_id,
                    owner_address=account.owner_address,
                    available_credits=account.available_credits - clean_withdrawn,
                    held_credits=account.held_credits,
                    spent_credits=account.spent_credits,
                    earned_credits=account.earned_credits,
                    created_at=account.created_at,
                    updated_at=now,
                    metadata=account.metadata,
                )
                withdrawal_tx = HubCreditTransaction(
                    transaction_id=stable_id(
                        "ctx",
                        {
                            "type": "withdrawal_released",
                            "account_id": clean_id,
                            "withdrawal_id": withdrawal_id,
                            "credits": clean_withdrawn,
                        },
                    ),
                    account_id=clean_id,
                    transaction_type="withdrawal_released",
                    credits=clean_withdrawn,
                    created_at=now,
                    memo=memo or "bridge escrow withdrawal released on-chain",
                    metadata={
                        **base_metadata,
                        "withdrawal_id": str(withdrawal_id or ""),
                        "recipient_address": str(recipient_address or ""),
                    },
                )
                data["transactions"].append(withdrawal_tx.as_dict())
                data["accounts"][account.account_id] = account.as_dict()
                txs.append(withdrawal_tx)
            elif clean_withdrawn <= 0:
                data["accounts"][account.account_id] = account.as_dict()

            self._save_unlocked(data)
            bridge_txs = [_transaction_from_dict(item) for item in data["transactions"]]
            bridge_rectifications = [
                tx for tx in bridge_txs
                if tx.account_id == clean_id and tx.transaction_type == "bridge_spend_rectified"
            ]
            bridge_withdrawals = [
                tx for tx in bridge_txs
                if tx.account_id == clean_id and tx.transaction_type == "withdrawal_released"
            ]
            return {
                "ok": True,
                "idempotent": bool(
                    (clean_rectified <= 0 or existing_rectification is not None)
                    and (clean_withdrawn <= 0 or existing_withdrawal is not None)
                ),
                "account": account.as_dict(),
                "transactions": [tx.as_dict() for tx in txs],
                "bridge_reconciliation": {
                    "ok": True,
                    "account_id": clean_id,
                    "rectified_credits": sum(tx.credits for tx in bridge_rectifications),
                    "withdrawn_credits": sum(tx.credits for tx in bridge_withdrawals),
                    "rectification_count": len(bridge_rectifications),
                    "withdrawal_count": len(bridge_withdrawals),
                    "rectifications": [tx.as_dict() for tx in bridge_rectifications],
                    "withdrawals": [tx.as_dict() for tx in bridge_withdrawals],
                },
                "ledger": self._status_from_data(data),
            }

    def issue(
        self,
        *,
        account_id: str,
        credits: int,
        memo: str = "",
        owner_address: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_credits = positive_int(credits)
        if clean_credits <= 0:
            raise ValueError("credits must be positive.")
        clean_id = clean_account_id(account_id)
        now = utc_now()
        with self._lock:
            data = self._load_unlocked()
            account = self._ensure_account_unlocked(
                data,
                clean_id,
                owner_address=owner_address,
                metadata=metadata,
                now=now,
            )
            account = HubCreditAccount(
                account_id=account.account_id,
                owner_address=owner_address or account.owner_address,
                available_credits=account.available_credits + clean_credits,
                held_credits=account.held_credits,
                spent_credits=account.spent_credits,
                earned_credits=account.earned_credits,
                created_at=account.created_at,
                updated_at=now,
                metadata={**account.metadata, **dict(metadata or {})},
            )
            tx = HubCreditTransaction(
                transaction_id=stable_id(
                    "ctx",
                    {
                        "account_id": clean_id,
                        "type": "admin_adjustment",
                        "credits": clean_credits,
                        "created_at": now,
                        "memo": memo,
                    },
                ),
                account_id=clean_id,
                transaction_type="admin_adjustment",
                credits=clean_credits,
                created_at=now,
                memo=memo,
                metadata=dict(metadata or {}),
            )
            data["accounts"][clean_id] = account.as_dict()
            data["transactions"].append(tx.as_dict())
            self._save_unlocked(data)
            return {"ok": True, "account": account.as_dict(), "transaction": tx.as_dict(), "ledger": self._status_from_data(data)}

    def record_deposit(self, deposit: CreditDeposit) -> dict[str, Any]:
        """Import a purchase/deposit receipt exactly once.

        The future chain indexer should call this after it observes
        CreditDeposited. Idempotency is by deposit_id, which is derived from the
        ChainEventRef in the Phase 0 model.
        """

        now = utc_now()
        with self._lock:
            data = self._load_unlocked()
            existing = data["deposits"].get(deposit.deposit_id)
            if isinstance(existing, dict):
                account = self._ensure_account_unlocked(data, deposit.account_id, now=now)
                return {
                    "ok": True,
                    "idempotent": True,
                    "deposit": _deposit_from_dict(existing).as_dict(),
                    "account": account.as_dict(),
                    "ledger": self._status_from_data(data),
                }

            account = self._ensure_account_unlocked(
                data,
                deposit.account_id,
                owner_address=deposit.payer_address,
                now=now,
            )
            account = HubCreditAccount(
                account_id=account.account_id,
                owner_address=account.owner_address or deposit.payer_address,
                available_credits=account.available_credits + deposit.credits_granted,
                held_credits=account.held_credits,
                spent_credits=account.spent_credits,
                earned_credits=account.earned_credits,
                created_at=account.created_at,
                updated_at=now,
                metadata=account.metadata,
            )
            tx = HubCreditTransaction(
                transaction_id=stable_id(
                    "ctx",
                    {
                        "account_id": account.account_id,
                        "type": "deposit_indexed",
                        "deposit_id": deposit.deposit_id,
                    },
                ),
                account_id=account.account_id,
                transaction_type="deposit_indexed",
                credits=deposit.credits_granted,
                created_at=deposit.created_at or now,
                deposit_id=deposit.deposit_id,
                memo=deposit.memo,
                metadata={"chain_event": deposit.chain_event.as_dict()},
            )
            data["accounts"][account.account_id] = account.as_dict()
            data["deposits"][deposit.deposit_id] = deposit.as_dict()
            data["transactions"].append(tx.as_dict())
            self._save_unlocked(data)
            return {
                "ok": True,
                "idempotent": False,
                "deposit": deposit.as_dict(),
                "account": account.as_dict(),
                "transaction": tx.as_dict(),
                "ledger": self._status_from_data(data),
            }

    def create_hold(
        self,
        *,
        account_id: str,
        request_id: str,
        credits: int,
        expires_at: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Reserve requester credits before request dispatch.

        Idempotency is by the stable hold id derived from account_id + request_id.
        Calling this again for the same request returns the existing hold and does
        not reserve credits twice.
        """

        clean_id = clean_account_id(account_id)
        clean_request = str(request_id or "").strip()
        clean_credits = positive_int(credits)
        if not clean_request:
            raise ValueError("request_id is required.")
        if clean_credits <= 0:
            raise ValueError("credits must be positive.")
        now = utc_now()
        hold = HubCreditHold(
            hold_id="",
            account_id=clean_id,
            request_id=clean_request,
            credits=clean_credits,
            status="held",
            created_at=now,
            expires_at=expires_at,
        )

        with self._lock:
            data = self._load_unlocked()
            existing_payload = data["holds"].get(hold.hold_id)
            if isinstance(existing_payload, dict):
                existing = _hold_from_dict(existing_payload)
                account = self._ensure_account_unlocked(data, clean_id, now=now)
                return {
                    "ok": True,
                    "idempotent": True,
                    "hold": existing.as_dict(),
                    "account": account.as_dict(),
                    "ledger": self._status_from_data(data),
                }

            account = self._ensure_account_unlocked(data, clean_id, now=now)
            if account.available_credits < clean_credits:
                raise ValueError(
                    f"Insufficient Compute Credits for account {clean_id}: "
                    f"available={account.available_credits}, required={clean_credits}."
                )

            account = HubCreditAccount(
                account_id=account.account_id,
                owner_address=account.owner_address,
                available_credits=account.available_credits - clean_credits,
                held_credits=account.held_credits + clean_credits,
                spent_credits=account.spent_credits,
                earned_credits=account.earned_credits,
                created_at=account.created_at,
                updated_at=now,
                metadata=account.metadata,
            )
            tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "hold_created", "hold_id": hold.hold_id}),
                account_id=account.account_id,
                transaction_type="hold_created",
                credits=clean_credits,
                created_at=now,
                request_id=clean_request,
                hold_id=hold.hold_id,
                memo=memo or f"hold for request {clean_request}",
                metadata=dict(metadata or {}),
            )

            data["accounts"][account.account_id] = account.as_dict()
            data["holds"][hold.hold_id] = hold.as_dict()
            data["transactions"].append(tx.as_dict())
            self._save_unlocked(data)
            return {
                "ok": True,
                "idempotent": False,
                "hold": hold.as_dict(),
                "account": account.as_dict(),
                "transaction": tx.as_dict(),
                "ledger": self._status_from_data(data),
            }

    def release_hold(
        self,
        *,
        hold_id: str,
        reason: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_hold = str(hold_id or "").strip()
        if not clean_hold:
            raise ValueError("hold_id is required.")
        now = utc_now()

        with self._lock:
            data = self._load_unlocked()
            payload = data["holds"].get(clean_hold)
            if not isinstance(payload, dict):
                raise KeyError(f"Unknown credit hold: {clean_hold}")
            hold = _hold_from_dict(payload)
            account = self._ensure_account_unlocked(data, hold.account_id, now=now)

            if hold.status != "held":
                return {
                    "ok": True,
                    "idempotent": True,
                    "hold": hold.as_dict(),
                    "account": account.as_dict(),
                    "ledger": self._status_from_data(data),
                }

            released = hold.credits
            account = HubCreditAccount(
                account_id=account.account_id,
                owner_address=account.owner_address,
                available_credits=account.available_credits + released,
                held_credits=max(0, account.held_credits - released),
                spent_credits=account.spent_credits,
                earned_credits=account.earned_credits,
                created_at=account.created_at,
                updated_at=now,
                metadata=account.metadata,
            )
            released_hold = HubCreditHold(
                hold_id=hold.hold_id,
                account_id=hold.account_id,
                request_id=hold.request_id,
                credits=hold.credits,
                status="released",
                created_at=hold.created_at,
                expires_at=hold.expires_at,
                released_at=now,
                charged_at=hold.charged_at,
            )
            tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "hold_released", "hold_id": hold.hold_id, "reason": reason}),
                account_id=account.account_id,
                transaction_type="hold_released",
                credits=released,
                created_at=now,
                request_id=hold.request_id,
                hold_id=hold.hold_id,
                memo=memo or reason or f"released hold for request {hold.request_id}",
                metadata=dict(metadata or {}),
            )

            data["accounts"][account.account_id] = account.as_dict()
            data["holds"][hold.hold_id] = released_hold.as_dict()
            data["transactions"].append(tx.as_dict())
            self._save_unlocked(data)
            return {
                "ok": True,
                "idempotent": False,
                "hold": released_hold.as_dict(),
                "account": account.as_dict(),
                "transaction": tx.as_dict(),
                "ledger": self._status_from_data(data),
            }

    def charge_hold(
        self,
        *,
        hold_id: str,
        charged_credits: int,
        worker_node_id: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_hold = str(hold_id or "").strip()
        clean_charged = positive_int(charged_credits)
        if not clean_hold:
            raise ValueError("hold_id is required.")
        if clean_charged <= 0:
            raise ValueError("charged_credits must be positive.")
        now = utc_now()

        with self._lock:
            data = self._load_unlocked()
            payload = data["holds"].get(clean_hold)
            if not isinstance(payload, dict):
                raise KeyError(f"Unknown credit hold: {clean_hold}")
            hold = _hold_from_dict(payload)

            existing_charge = next(
                (
                    _charge_from_dict(item)
                    for item in data["charges"].values()
                    if isinstance(item, dict) and str(item.get("hold_id", "")) == clean_hold
                ),
                None,
            )
            if existing_charge is not None:
                account = self._ensure_account_unlocked(data, existing_charge.account_id, now=now)
                earning = (
                    _earning_from_dict(data["worker_earnings"][existing_charge.worker_earning_id])
                    if existing_charge.worker_earning_id in data["worker_earnings"]
                    else None
                )
                return {
                    "ok": True,
                    "idempotent": True,
                    "hold": hold.as_dict(),
                    "account": account.as_dict(),
                    "charge": existing_charge.as_dict(),
                    "worker_earning": earning.as_private_dict() if earning else None,
                    "ledger": self._status_from_data(data),
                }

            if hold.status != "held":
                raise ValueError(f"Cannot charge hold {hold.hold_id} with status {hold.status}.")
            if clean_charged > hold.credits:
                raise ValueError(
                    f"Cannot charge {clean_charged} credits from hold {hold.hold_id}; "
                    f"only {hold.credits} credits were held."
                )

            released_credits = max(0, hold.credits - clean_charged)
            account = self._ensure_account_unlocked(data, hold.account_id, now=now)
            account = HubCreditAccount(
                account_id=account.account_id,
                owner_address=account.owner_address,
                available_credits=account.available_credits + released_credits,
                held_credits=max(0, account.held_credits - hold.credits),
                spent_credits=account.spent_credits + clean_charged,
                earned_credits=account.earned_credits,
                created_at=account.created_at,
                updated_at=now,
                metadata=account.metadata,
            )

            earning: WorkerEarning | None = None
            if worker_node_id:
                earning = self._record_worker_earning_unlocked(
                    data,
                    worker_node_id=worker_node_id,
                    request_id=hold.request_id,
                    credits=clean_charged,
                    now=now,
                    metadata=metadata,
                )

            charge = RequestCharge(
                charge_id="",
                account_id=account.account_id,
                request_id=hold.request_id,
                hold_id=hold.hold_id,
                charged_credits=clean_charged,
                released_credits=released_credits,
                worker_earning_id=earning.earning_id if earning else "",
                created_at=now,
            )
            charged_hold = HubCreditHold(
                hold_id=hold.hold_id,
                account_id=hold.account_id,
                request_id=hold.request_id,
                credits=hold.credits,
                status="charged",
                created_at=hold.created_at,
                expires_at=hold.expires_at,
                released_at=now if released_credits else "",
                charged_at=now,
            )
            charge_tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "request_charged", "charge_id": charge.charge_id}),
                account_id=account.account_id,
                transaction_type="request_charged",
                credits=clean_charged,
                created_at=now,
                request_id=hold.request_id,
                worker_node_id=worker_node_id,
                hold_id=hold.hold_id,
                memo=memo or f"charged request {hold.request_id}",
                metadata=dict(metadata or {}),
            )

            data["accounts"][account.account_id] = account.as_dict()
            data["holds"][hold.hold_id] = charged_hold.as_dict()
            data["charges"][charge.charge_id] = charge.as_dict()
            data["transactions"].append(charge_tx.as_dict())
            if released_credits:
                release_tx = HubCreditTransaction(
                    transaction_id=stable_id("ctx", {"type": "hold_released", "hold_id": hold.hold_id, "charge_id": charge.charge_id}),
                    account_id=account.account_id,
                    transaction_type="hold_released",
                    credits=released_credits,
                    created_at=now,
                    request_id=hold.request_id,
                    hold_id=hold.hold_id,
                    memo=f"released unused hold credits for request {hold.request_id}",
                    metadata=dict(metadata or {}),
                )
                data["transactions"].append(release_tx.as_dict())
            self._save_unlocked(data)
            return {
                "ok": True,
                "idempotent": False,
                "hold": charged_hold.as_dict(),
                "account": account.as_dict(),
                "charge": charge.as_dict(),
                "worker_earning": earning.as_private_dict() if earning else None,
                "ledger": self._status_from_data(data),
            }

    def record_worker_earning(
        self,
        *,
        worker_node_id: str,
        request_id: str,
        credits: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_worker = clean_worker_id(worker_node_id)
        clean_request = str(request_id or "").strip()
        clean_credits = positive_int(credits)
        if not clean_request:
            raise ValueError("request_id is required.")
        if clean_credits <= 0:
            raise ValueError("credits must be positive.")
        now = utc_now()

        with self._lock:
            data = self._load_unlocked()
            earning = self._record_worker_earning_unlocked(
                data,
                worker_node_id=clean_worker,
                request_id=clean_request,
                credits=clean_credits,
                now=now,
                metadata=metadata,
            )
            self._save_unlocked(data)
            worker_account = self._ensure_account_unlocked(data, clean_worker, now=now)
            return {
                "ok": True,
                "worker_earning": earning.as_private_dict(),
                "worker_account": worker_account.as_dict(),
                "ledger": self._status_from_data(data),
            }

    def _record_worker_earning_unlocked(
        self,
        data: dict[str, Any],
        *,
        worker_node_id: str,
        request_id: str,
        credits: int,
        now: str,
        metadata: dict[str, Any] | None = None,
    ) -> WorkerEarning:
        clean_worker = clean_worker_id(worker_node_id)
        clean_request = str(request_id or "").strip()
        clean_credits = positive_int(credits)
        candidate = WorkerEarning(
            earning_id="",
            worker_node_id=clean_worker,
            request_id=clean_request,
            credits=clean_credits,
            worker_commitment=make_worker_commitment(
                worker_node_id=clean_worker,
                request_id=clean_request,
                epoch_salt="paid-mock-worker-earning-v0",
            ),
            status="earned",
            created_at=now,
        )
        existing = data["worker_earnings"].get(candidate.earning_id)
        if isinstance(existing, dict):
            return _earning_from_dict(existing)

        worker_account = self._ensure_account_unlocked(data, clean_worker, now=now)
        worker_account = HubCreditAccount(
            account_id=worker_account.account_id,
            owner_address=worker_account.owner_address,
            available_credits=worker_account.available_credits,
            held_credits=worker_account.held_credits,
            spent_credits=worker_account.spent_credits,
            earned_credits=worker_account.earned_credits + clean_credits,
            created_at=worker_account.created_at,
            updated_at=now,
            metadata=worker_account.metadata,
        )
        earning_tx = HubCreditTransaction(
            transaction_id=stable_id("ctx", {"type": "worker_earned", "earning_id": candidate.earning_id}),
            account_id=worker_account.account_id,
            transaction_type="worker_earned",
            credits=clean_credits,
            created_at=now,
            request_id=clean_request,
            worker_node_id=clean_worker,
            memo=f"worker earned for request {clean_request}",
            metadata=dict(metadata or {}),
        )
        data["accounts"][worker_account.account_id] = worker_account.as_dict()
        data["worker_earnings"][candidate.earning_id] = candidate.as_private_dict()
        data["transactions"].append(earning_tx.as_dict())
        return candidate

    def _ensure_account_unlocked(
        self,
        data: dict[str, Any],
        account_id: str,
        *,
        owner_address: str = "",
        metadata: dict[str, Any] | None = None,
        now: str = "",
    ) -> HubCreditAccount:
        clean_id = clean_account_id(account_id)
        now = now or utc_now()
        payload = data["accounts"].get(clean_id)
        if isinstance(payload, dict):
            account = _account_from_dict(payload)
            if owner_address and owner_address != account.owner_address:
                account = HubCreditAccount(
                    account_id=account.account_id,
                    owner_address=owner_address,
                    available_credits=account.available_credits,
                    held_credits=account.held_credits,
                    spent_credits=account.spent_credits,
                    earned_credits=account.earned_credits,
                    created_at=account.created_at,
                    updated_at=now,
                    metadata={**account.metadata, **dict(metadata or {})},
                )
                data["accounts"][clean_id] = account.as_dict()
            return account
        account = HubCreditAccount(
            account_id=clean_id,
            owner_address=owner_address,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )
        data["accounts"][clean_id] = account.as_dict()
        return account

    def _status_from_data(self, data: dict[str, Any]) -> dict[str, Any]:
        accounts = [_account_from_dict(item) for item in data["accounts"].values()]
        deposits = [_deposit_from_dict(item) for item in data["deposits"].values()]
        holds = [_hold_from_dict(item) for item in data["holds"].values()]
        charges = [_charge_from_dict(item) for item in data["charges"].values()]
        worker_earnings = [_earning_from_dict(item) for item in data["worker_earnings"].values()]
        return {
            "ok": True,
            "unit": {"name": CREDIT_UNIT_NAME, "key": CREDIT_UNIT_KEY},
            "schema_version": CREDIT_LEDGER_VERSION,
            "store_version": HUB_CREDIT_LEDGER_STORE_VERSION,
            "account_count": len(accounts),
            "deposit_count": len(deposits),
            "purchase_count": len(deposits),
            "transaction_count": len(data["transactions"]),
            "hold_count": len(holds),
            "active_hold_count": sum(1 for hold in holds if hold.status == "held"),
            "charge_count": len(charges),
            "worker_earning_count": len(worker_earnings),
            "totals": {
                "available_credits": sum(account.available_credits for account in accounts),
                "held_credits": sum(account.held_credits for account in accounts),
                "spent_credits": sum(account.spent_credits for account in accounts),
                "earned_credits": sum(account.earned_credits for account in accounts),
                "deposited_credits": sum(deposit.credits_granted for deposit in deposits),
                "purchased_credits": sum(deposit.credits_granted for deposit in deposits),
                "active_held_credits": sum(hold.credits for hold in holds if hold.status == "held"),
                "charged_credits": sum(charge.charged_credits for charge in charges),
                "worker_earned_credits": sum(earning.credits for earning in worker_earnings),
            },
        }

    def _load(self) -> dict[str, Any]:
        with self._lock:
            return self._load_unlocked()

    def _load_unlocked(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return self._normalize(data)
            except (OSError, json.JSONDecodeError):
                pass
        return self._normalize({})

    def _save_unlocked(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(self._normalize(data), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        raw_accounts = data.get("accounts") if isinstance(data.get("accounts"), dict) else {}
        accounts = {
            clean_account_id(account_id): _account_from_dict(payload).as_dict()
            for account_id, payload in raw_accounts.items()
            if isinstance(payload, dict)
        }

        raw_transactions = data.get("transactions") if isinstance(data.get("transactions"), list) else []
        transactions = []
        seen_tx: set[str] = set()
        for item in raw_transactions:
            if not isinstance(item, dict):
                continue
            try:
                tx = _transaction_from_dict(item)
            except ValueError:
                continue
            if tx.transaction_id in seen_tx:
                continue
            seen_tx.add(tx.transaction_id)
            transactions.append(tx.as_dict())

        raw_deposits = data.get("deposits") if isinstance(data.get("deposits"), dict) else {}
        deposits = {}
        for deposit_id, item in raw_deposits.items():
            if not isinstance(item, dict):
                continue
            try:
                deposit = _deposit_from_dict(item)
            except Exception:
                continue
            deposits[deposit.deposit_id or str(deposit_id)] = deposit.as_dict()

        raw_holds = data.get("holds") if isinstance(data.get("holds"), dict) else {}
        holds = {}
        for hold_id, item in raw_holds.items():
            if not isinstance(item, dict):
                continue
            try:
                hold = _hold_from_dict(item)
            except Exception:
                continue
            holds[hold.hold_id or str(hold_id)] = hold.as_dict()

        raw_charges = data.get("charges") if isinstance(data.get("charges"), dict) else {}
        charges = {}
        for charge_id, item in raw_charges.items():
            if not isinstance(item, dict):
                continue
            try:
                charge = _charge_from_dict(item)
            except Exception:
                continue
            charges[charge.charge_id or str(charge_id)] = charge.as_dict()

        raw_earnings = data.get("worker_earnings") if isinstance(data.get("worker_earnings"), dict) else {}
        worker_earnings = {}
        for earning_id, item in raw_earnings.items():
            if not isinstance(item, dict):
                continue
            try:
                earning = _earning_from_dict(item)
            except Exception:
                continue
            worker_earnings[earning.earning_id or str(earning_id)] = earning.as_private_dict()

        return {
            "version": HUB_CREDIT_LEDGER_STORE_VERSION,
            "accounts": accounts,
            "transactions": sorted(transactions, key=lambda item: str(item.get("created_at", ""))),
            "deposits": deposits,
            "holds": holds,
            "charges": charges,
            "worker_earnings": worker_earnings,
            "settlement_batches": data.get("settlement_batches") if isinstance(data.get("settlement_batches"), dict) else {},
            "reports": data.get("reports") if isinstance(data.get("reports"), dict) else {},
        }
