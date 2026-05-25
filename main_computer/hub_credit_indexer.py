from __future__ import annotations

from typing import Any

from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.hub_credit_models import ChainEventRef, CreditDeposit, clean_account_id, positive_int


HUB_CREDIT_INDEXER_PHASE = "R2A"
HUB_CREDIT_INDEXER_MODE = "manual-normalized-import"
HUB_CREDIT_INDEXER_EVENT = "HubCreditSale.CreditPurchased"


def _require_string(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} is required.")
    return value


def _require_positive_int(payload: dict[str, Any], key: str) -> int:
    value = positive_int(payload.get(key))
    if value <= 0:
        raise ValueError(f"{key} must be positive.")
    return value


def _require_non_negative_int(payload: dict[str, Any], key: str) -> int:
    try:
        value = int(payload.get(key, 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a non-negative integer.") from exc
    if value < 0:
        raise ValueError(f"{key} must be a non-negative integer.")
    return value


def _is_hex(value: str, *, chars: int) -> bool:
    text = str(value or "").strip()
    if len(text) != chars + 2 or not text.lower().startswith("0x"):
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in text[2:])


def _require_evm_address(payload: dict[str, Any], key: str) -> str:
    value = _require_string(payload, key)
    if not _is_hex(value, chars=40):
        raise ValueError(f"{key} must be a 20-byte 0x-prefixed hex address.")
    return value


def _require_tx_hash(payload: dict[str, Any], key: str = "tx_hash") -> str:
    value = _require_string(payload, key)
    if not _is_hex(value, chars=64):
        raise ValueError(f"{key} must be a 32-byte 0x-prefixed hex hash.")
    return value


def _clean_payment_asset(value: Any) -> str:
    text = str(value or "native").strip()
    if not text:
        return "native"
    if text.lower() == "native":
        return "native"
    if not _is_hex(text, chars=40):
        raise ValueError("payment_asset must be 'native' or a 20-byte 0x-prefixed token address.")
    return text


class HubCreditIndexer:
    """R2A manual funding receipt importer for the internal Compute Credit ledger.

    This intentionally does not perform RPC calls, credit-card checkout, fiat
    processing, or request charging. It only validates a normalized on-chain
    funding receipt and records it through HubCreditLedger idempotently.
    """

    def __init__(self, ledger: HubCreditLedger) -> None:
        self.ledger = ledger

    def status(self) -> dict[str, Any]:
        ledger_status = self.ledger.status(recent_limit=10)
        return {
            "ok": True,
            "phase": HUB_CREDIT_INDEXER_PHASE,
            "mode": HUB_CREDIT_INDEXER_MODE,
            "event": HUB_CREDIT_INDEXER_EVENT,
            "rpc_sync_supported": False,
            "credit_card_supported": False,
            "request_charging_supported": False,
            "idempotency": "chain_id + contract_address + tx_hash + log_index",
            "ledger": {
                "unit": ledger_status.get("unit", {}),
                "account_count": ledger_status.get("account_count", 0),
                "purchase_count": ledger_status.get("purchase_count", 0),
                "transaction_count": ledger_status.get("transaction_count", 0),
                "totals": ledger_status.get("totals", {}),
            },
            "endpoints": {
                "status": "/api/hub/v1/credits/indexer",
                "manual_import": "/api/hub/v1/credits/purchases/import",
                "purchases": "/api/hub/v1/credits/purchases",
                "transactions": "/api/hub/v1/credits/transactions",
            },
        }

    def build_deposit(self, payload: dict[str, Any]) -> CreditDeposit:
        if not isinstance(payload, dict):
            raise ValueError("purchase import payload must be a JSON object.")

        account_id_raw = _require_string(payload, "account_id")
        account_id = clean_account_id(account_id_raw, default="")
        if not account_id:
            raise ValueError("account_id is required.")

        event = ChainEventRef(
            chain_id=_require_positive_int(payload, "chain_id"),
            contract_address=_require_evm_address(payload, "contract_address"),
            tx_hash=_require_tx_hash(payload),
            log_index=_require_non_negative_int(payload, "log_index"),
            block_number=_require_non_negative_int(payload, "block_number"),
        )
        return CreditDeposit(
            deposit_id="",
            account_id=account_id,
            payer_address=_require_evm_address(payload, "payer_address"),
            payment_asset=_clean_payment_asset(payload.get("payment_asset", "native")),
            payment_amount_base_units=_require_positive_int(payload, "payment_amount_base_units"),
            credits_granted=_require_positive_int(payload, "credits_granted"),
            chain_event=event,
            memo=str(payload.get("memo", "")).strip() or "funding receipt import",
        )

    def import_purchase(self, payload: dict[str, Any]) -> dict[str, Any]:
        deposit = self.build_deposit(payload)
        result = dict(self.ledger.record_deposit(deposit))
        result["indexer"] = self.status()
        result["event_uid"] = deposit.chain_event.event_uid

        if result.get("idempotent") and "transaction" not in result:
            matching = [
                tx.as_dict()
                for tx in self.ledger.list_transactions(account_id=deposit.account_id, limit=500)
                if tx.deposit_id == deposit.deposit_id and tx.transaction_type == "deposit_indexed"
            ]
            if matching:
                result["transaction"] = matching[0]
        return result
