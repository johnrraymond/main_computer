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
    HubCreditTransaction,
    clean_account_id,
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
        return {
            "ok": True,
            "unit": {"name": CREDIT_UNIT_NAME, "key": CREDIT_UNIT_KEY},
            "schema_version": CREDIT_LEDGER_VERSION,
            "store_version": HUB_CREDIT_LEDGER_STORE_VERSION,
            "account_count": len(accounts),
            "purchase_count": len(deposits),
            "transaction_count": len(transactions),
            "totals": {
                "available_credits": sum(account.available_credits for account in accounts),
                "held_credits": sum(account.held_credits for account in accounts),
                "spent_credits": sum(account.spent_credits for account in accounts),
                "earned_credits": sum(account.earned_credits for account in accounts),
                "purchased_credits": sum(deposit.credits_granted for deposit in deposits),
            },
            "recent_transactions": [tx.as_dict() for tx in transactions[-max(0, int(recent_limit or 0)):]][::-1],
            "recent_purchases": [deposit.as_dict() for deposit in deposits[-max(0, int(recent_limit or 0)):]][::-1],
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

    def list_purchases(self, *, account_id: str = "", limit: int = 100) -> list[CreditDeposit]:
        clean_id = clean_account_id(account_id, default="") if account_id else ""
        clean_limit = min(500, max(1, int(limit or 100)))
        data = self._load()
        purchases = [_deposit_from_dict(item) for item in data["deposits"].values()]
        if clean_id:
            purchases = [purchase for purchase in purchases if purchase.account_id == clean_id]
        return sorted(purchases, key=lambda item: item.created_at, reverse=True)[:clean_limit]

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
        return {
            "ok": True,
            "unit": {"name": CREDIT_UNIT_NAME, "key": CREDIT_UNIT_KEY},
            "schema_version": CREDIT_LEDGER_VERSION,
            "store_version": HUB_CREDIT_LEDGER_STORE_VERSION,
            "account_count": len(accounts),
            "purchase_count": len(deposits),
            "transaction_count": len(data["transactions"]),
            "totals": {
                "available_credits": sum(account.available_credits for account in accounts),
                "held_credits": sum(account.held_credits for account in accounts),
                "spent_credits": sum(account.spent_credits for account in accounts),
                "earned_credits": sum(account.earned_credits for account in accounts),
                "purchased_credits": sum(deposit.credits_granted for deposit in deposits),
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

        return {
            "version": HUB_CREDIT_LEDGER_STORE_VERSION,
            "accounts": accounts,
            "transactions": sorted(transactions, key=lambda item: str(item.get("created_at", ""))),
            "deposits": deposits,
            "holds": data.get("holds") if isinstance(data.get("holds"), dict) else {},
            "charges": data.get("charges") if isinstance(data.get("charges"), dict) else {},
            "worker_earnings": data.get("worker_earnings") if isinstance(data.get("worker_earnings"), dict) else {},
            "settlement_batches": data.get("settlement_batches") if isinstance(data.get("settlement_batches"), dict) else {},
            "reports": data.get("reports") if isinstance(data.get("reports"), dict) else {},
        }
