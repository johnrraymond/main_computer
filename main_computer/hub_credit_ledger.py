from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from main_computer.credit_units import credit_count_to_wei, credit_wei_to_decimal_text, credit_wei_to_whole_credits_floor, positive_credit_wei
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
    WorkerClaim,
    WorkerSettlementBatch,
    clean_account_id,
    clean_worker_id,
    make_worker_commitment,
    positive_int,
    stable_id,
    truncate_worker_payout_for_precision,
    worker_payout_bucket_size_for_precision,
    normalize_worker_payout_precision_places,
    utc_now,
)


HUB_CREDIT_LEDGER_STORE_VERSION = "hub-credit-ledger-store-v1"


_ETH_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_ETH_TX_HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def _clean_eth_address(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if not _ETH_ADDRESS_RE.fullmatch(text):
        raise ValueError(f"{field_name} must be an Ethereum-style 0x address.")
    return text


def _clean_eth_tx_hash(value: Any, *, field_name: str = "settlement_tx_hash") -> str:
    text = str(value or "").strip().lower()
    if not _ETH_TX_HASH_RE.fullmatch(text):
        raise ValueError(f"{field_name} must be an Ethereum-style 0x transaction hash.")
    return text


def _positive_chain_id(value: Any) -> int:
    parsed = positive_int(value)
    if parsed <= 0:
        raise ValueError("chain_id must be a positive integer.")
    return parsed

def _require_same_chain_execution_field(
    metadata: dict[str, Any],
    field_name: str,
    expected: Any,
    *,
    batch_id: str,
) -> None:
    """Require an idempotent chain receipt replay to match the stored receipt."""
    if field_name not in metadata:
        raise ValueError(
            f"Settlement batch {batch_id} is already settled, but no stored chain receipt "
            f"{field_name} is available for idempotent replay."
        )
    stored_raw = metadata.get(field_name)
    if isinstance(expected, int):
        stored = positive_int(stored_raw)
        if stored != expected:
            raise ValueError(
                f"Settlement batch {batch_id} is already settled with a different chain receipt "
                f"{field_name}."
            )
        return
    stored = str(stored_raw or "").strip().lower()
    incoming = str(expected or "").strip().lower()
    if not stored or stored != incoming:
        raise ValueError(
            f"Settlement batch {batch_id} is already settled with a different chain receipt "
            f"{field_name}."
        )




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
        bridge_completed_credits=positive_int(payload.get("bridge_completed_credits")),
        available_credit_wei=payload.get("available_credit_wei"),
        held_credit_wei=payload.get("held_credit_wei"),
        spent_credit_wei=payload.get("spent_credit_wei"),
        earned_credit_wei=payload.get("earned_credit_wei"),
        bridge_completed_credit_wei=payload.get("bridge_completed_credit_wei"),
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
        credit_wei=payload.get("credit_wei"),
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
        credits_granted_wei=payload.get("credits_granted_wei"),
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
        credit_wei=payload.get("credit_wei"),
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
        charged_credit_wei=payload.get("charged_credit_wei"),
        released_credits=positive_int(payload.get("released_credits")),
        released_credit_wei=payload.get("released_credit_wei"),
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
        earned_credit_wei=payload.get("earned_credit_wei"),
        status=str(payload.get("status", "earned")),
        batch_id=str(payload.get("batch_id", "")),
        created_at=str(payload.get("created_at", "")),
    )


def _claim_from_dict(payload: dict[str, Any]) -> WorkerClaim:
    earning_ids = payload.get("earning_ids")
    if not isinstance(earning_ids, list):
        earning_ids = []
    return WorkerClaim(
        claim_id=str(payload.get("claim_id", "")),
        worker_node_id=str(payload.get("worker_node_id", "")),
        claimed_credits=positive_int(payload.get("claimed_credits")),
        claimed_credit_wei=payload.get("claimed_credit_wei"),
        earning_ids=[str(item) for item in earning_ids],
        status=str(payload.get("status", "claimed")),
        idempotency_key=str(payload.get("idempotency_key", "")),
        settlement_tx_hash=str(payload.get("settlement_tx_hash", "")),
        created_at=str(payload.get("created_at", "")),
        metadata=_copy_dict(payload.get("metadata")),
    )


def _batch_from_dict(payload: dict[str, Any]) -> WorkerSettlementBatch:
    claim_ids = payload.get("claim_ids")
    if not isinstance(claim_ids, list):
        claim_ids = []
    return WorkerSettlementBatch(
        batch_id=str(payload.get("batch_id", "")),
        window_start=str(payload.get("window_start", "")),
        window_end=str(payload.get("window_end", "")),
        total_credits_exact=positive_int(payload.get("total_credits_exact")),
        total_credits_published=positive_int(payload.get("total_credits_published")),
        dust_credits=positive_int(payload.get("dust_credits")),
        worker_count=positive_int(payload.get("worker_count")),
        total_credit_wei_exact=payload.get("total_credit_wei_exact"),
        total_credit_wei_published=payload.get("total_credit_wei_published"),
        dust_credit_wei=payload.get("dust_credit_wei"),
        batch_root=str(payload.get("batch_root", "")),
        status=str(payload.get("status", "draft")),
        worker_node_id=str(payload.get("worker_node_id", "")),
        claim_ids=[str(item) for item in claim_ids],
        precision_places=normalize_worker_payout_precision_places(payload.get("precision_places")),
        rounding_bucket_credits=positive_int(
            payload.get("rounding_bucket_credits"),
            default=worker_payout_bucket_size_for_precision(payload.get("precision_places")),
        ),
        bridge_account_id=str(payload.get("bridge_account_id", "bridge-worker-payout-dust")),
        payout_rail=str(payload.get("payout_rail", "")),
        operator_id=str(payload.get("operator_id", "")),
        settlement_reference=str(payload.get("settlement_reference", "")),
        settlement_tx_hash=str(payload.get("settlement_tx_hash", "")),
        settlement_proof_id=str(payload.get("settlement_proof_id", "")),
        settlement_proof_hash=str(payload.get("settlement_proof_hash", "")),
        settled_at=str(payload.get("settled_at", "")),
        created_at=str(payload.get("created_at", "")),
        metadata=_copy_dict(payload.get("metadata")),
    )


def _settlement_proof_identity(
    *,
    batch: WorkerSettlementBatch,
    payout_rail: str = "",
    operator_id: str = "",
    settlement_reference: str = "",
    settlement_tx_hash: str = "",
    settlement_proof: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_rail = str(payout_rail or batch.payout_rail or "operator-manual").strip() or "operator-manual"
    clean_operator = clean_account_id(operator_id, default="") if operator_id else batch.operator_id
    clean_reference = str(settlement_reference or batch.settlement_reference or "").strip()
    clean_tx_hash = str(settlement_tx_hash or batch.settlement_tx_hash or "").strip()
    clean_proof = dict(settlement_proof or {})
    proof_payload = {
        "batch_id": batch.batch_id,
        "worker_node_id": batch.worker_node_id,
        "claim_ids": list(batch.claim_ids),
        "payout_rail": clean_rail,
        "operator_id": clean_operator,
        "settlement_reference": clean_reference,
        "settlement_tx_hash": clean_tx_hash,
        "total_credits_published": batch.total_credits_published,
        "bridge_retained_credits": batch.dust_credits,
        "proof": clean_proof,
    }
    canonical = json.dumps(proof_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    proof_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    proof_id = stable_id("proof", {"batch_id": batch.batch_id, "proof_hash": proof_hash})
    return {
        "payout_rail": clean_rail,
        "operator_id": clean_operator,
        "settlement_reference": clean_reference,
        "settlement_tx_hash": clean_tx_hash,
        "settlement_proof": clean_proof,
        "settlement_proof_id": proof_id,
        "settlement_proof_hash": proof_hash,
    }


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
        worker_claims = [_claim_from_dict(item) for item in data["worker_claims"].values()]
        settlement_batches = [_batch_from_dict(item) for item in data["settlement_batches"].values()]
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
            "worker_claim_count": len(worker_claims),
            "worker_settlement_batch_count": len(settlement_batches),
            "totals": {
                "available_credits": sum(account.available_credits for account in accounts),
                "held_credits": sum(account.held_credits for account in accounts),
                "spent_credits": sum(account.spent_credits for account in accounts),
                "earned_credits": sum(account.earned_credits for account in accounts),
                "bridge_completed_credits": sum(account.bridge_completed_credits for account in accounts),
                "available_credit_wei": str(sum(account.available_credit_wei for account in accounts)),
                "held_credit_wei": str(sum(account.held_credit_wei for account in accounts)),
                "spent_credit_wei": str(sum(account.spent_credit_wei for account in accounts)),
                "earned_credit_wei": str(sum(account.earned_credit_wei for account in accounts)),
                "bridge_completed_credit_wei": str(sum(account.bridge_completed_credit_wei for account in accounts)),
                "available_credits_display": credit_wei_to_decimal_text(sum(account.available_credit_wei for account in accounts)),
                "held_credits_display": credit_wei_to_decimal_text(sum(account.held_credit_wei for account in accounts)),
                "spent_credits_display": credit_wei_to_decimal_text(sum(account.spent_credit_wei for account in accounts)),
                "bridge_completed_credits_display": credit_wei_to_decimal_text(sum(account.bridge_completed_credit_wei for account in accounts)),
                "deposited_credits": sum(deposit.credits_granted for deposit in deposits),
                "purchased_credits": sum(deposit.credits_granted for deposit in deposits),
                "deposited_credit_wei": str(sum(deposit.credits_granted_wei for deposit in deposits)),
                "purchased_credit_wei": str(sum(deposit.credits_granted_wei for deposit in deposits)),
                "active_held_credits": sum(hold.credits for hold in holds if hold.status == "held"),
                "active_held_credit_wei": str(sum(hold.credit_wei for hold in holds if hold.status == "held")),
                "charged_credits": sum(charge.charged_credits for charge in charges),
                "charged_credit_wei": str(sum(charge.charged_credit_wei for charge in charges)),
                "worker_earned_credits": sum(earning.credits for earning in worker_earnings),
                "worker_claimed_credits": sum(claim.claimed_credits for claim in worker_claims if claim.status in {"claimed", "settled"}),
                "worker_settlement_exact_credits": sum(batch.total_credits_exact for batch in settlement_batches if batch.status == "settled"),
                "worker_settlement_published_credits": sum(batch.total_credits_published for batch in settlement_batches if batch.status == "settled"),
                "worker_settlement_dust_credits": sum(batch.dust_credits for batch in settlement_batches if batch.status == "settled"),
                "bridge_retained_worker_payout_dust_credits": sum(batch.dust_credits for batch in settlement_batches if batch.status == "settled"),
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
            "recent_worker_claims": [
                claim.as_dict()
                for claim in worker_claims[-max(0, int(recent_limit or 0)):]
            ][::-1],
            "recent_worker_settlement_batches": [
                batch.as_dict()
                for batch in settlement_batches[-max(0, int(recent_limit or 0)):]
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
            holds = []
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


    def list_worker_claims(
        self,
        *,
        worker_node_id: str = "",
        limit: int = 100,
    ) -> list[WorkerClaim]:
        clean_worker = clean_worker_id(worker_node_id, default="") if worker_node_id else ""
        clean_limit = min(500, max(1, int(limit or 100)))
        data = self._load()
        claims = [_claim_from_dict(item) for item in data["worker_claims"].values()]
        if clean_worker:
            claims = [claim for claim in claims if claim.worker_node_id == clean_worker]
        return sorted(claims, key=lambda item: item.created_at, reverse=True)[:clean_limit]

    def list_worker_settlement_batches(
        self,
        *,
        worker_node_id: str = "",
        limit: int = 100,
    ) -> list[WorkerSettlementBatch]:
        clean_worker = clean_worker_id(worker_node_id, default="") if worker_node_id else ""
        clean_limit = min(500, max(1, int(limit or 100)))
        data = self._load()
        batches = [_batch_from_dict(item) for item in data["settlement_batches"].values()]
        if clean_worker:
            batches = [batch for batch in batches if batch.worker_node_id == clean_worker]
        return sorted(batches, key=lambda item: item.created_at, reverse=True)[:clean_limit]

    def worker_settlement_totals(
        self,
        worker_node_id: str,
        *,
        precision_places: int | None = None,
    ) -> dict[str, Any]:
        if not str(worker_node_id or "").strip():
            raise ValueError("worker_node_id is required.")
        clean_worker = clean_worker_id(worker_node_id)
        data = self._load()
        return self._worker_settlement_totals_from_data(data, clean_worker, precision_places=precision_places)

    def create_worker_settlement_batch(
        self,
        *,
        worker_node_id: str,
        claim_ids: list[str] | None = None,
        precision_places: int | None = None,
        idempotency_key: str = "",
        bridge_account_id: str = "bridge-worker-payout-dust",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(worker_node_id or "").strip():
            raise ValueError("worker_node_id is required.")
        clean_worker = clean_worker_id(worker_node_id)
        clean_key = str(idempotency_key or "").strip()
        explicit_claim_ids = claim_ids is not None
        requested_claim_ids = [str(item or "").strip() for item in (claim_ids or []) if str(item or "").strip()]
        if len(requested_claim_ids) != len(set(requested_claim_ids)):
            raise ValueError("claim_ids must not contain duplicates.")
        precision = normalize_worker_payout_precision_places(precision_places)
        bucket_size = worker_payout_bucket_size_for_precision(precision)
        now = utc_now()

        with self._lock:
            data = self._load_unlocked()

            if clean_key:
                for item in data["settlement_batches"].values():
                    if not isinstance(item, dict):
                        continue
                    batch = _batch_from_dict(item)
                    if batch.worker_node_id == clean_worker and str(batch.metadata.get("idempotency_key", "")) == clean_key:
                        return {
                            "ok": True,
                            "idempotent": True,
                            "batch": batch.as_dict(),
                            "worker_settlement_totals": self._worker_settlement_totals_from_data(data, clean_worker, precision_places=precision),
                            "ledger": self._status_from_data(data),
                        }

            before = self._worker_settlement_totals_from_data(data, clean_worker, precision_places=precision)
            settleable_ids = [str(item) for item in before["settleable_claim_ids"]]
            selected_ids = requested_claim_ids if explicit_claim_ids else settleable_ids
            if not selected_ids:
                return {
                    "ok": True,
                    "idempotent": False,
                    "batch": None,
                    "batch_claim_count": 0,
                    "total_credits_exact": 0,
                    "total_credits_published": 0,
                    "dust_credits": 0,
                    "bridge_retained_credits": 0,
                    "worker_settlement_totals": before,
                    "ledger": self._status_from_data(data),
                }

            claims_by_id = {
                claim.claim_id: claim
                for claim in (_claim_from_dict(item) for item in data["worker_claims"].values())
            }
            already_batched_ids = set(before["batched_claim_ids"])
            selected_claims: list[WorkerClaim] = []
            for claim_id in selected_ids:
                claim = claims_by_id.get(claim_id)
                if claim is None:
                    raise KeyError(f"Unknown worker claim: {claim_id}")
                if claim.worker_node_id != clean_worker:
                    raise ValueError(f"Claim {claim_id} belongs to another worker.")
                if claim.status != "claimed":
                    raise ValueError(f"Claim {claim_id} is not settleable; status is {claim.status}.")
                if claim_id in already_batched_ids:
                    raise ValueError(f"Claim {claim_id} is already included in a settlement batch.")
                selected_claims.append(claim)

            exact_total = sum(claim.claimed_credit_wei for claim in selected_claims)
            published, dust, precision, bucket_size = truncate_worker_payout_for_precision(exact_total, precision_places=precision)
            batch = WorkerSettlementBatch(
                batch_id="",
                window_start=selected_claims[0].created_at if selected_claims else now,
                window_end=now,
                total_credits_exact=0,
                total_credits_published=0,
                dust_credits=0,
                worker_count=1 if selected_claims else 0,
                total_credit_wei_exact=exact_total,
                total_credit_wei_published=published,
                dust_credit_wei=dust,
                status="opened",
                worker_node_id=clean_worker,
                claim_ids=selected_ids,
                precision_places=precision,
                rounding_bucket_credit_wei=bucket_size,
                bridge_account_id=bridge_account_id,
                created_at=now,
                metadata={
                    **dict(metadata or {}),
                    "idempotency_key": clean_key,
                    "payout_rounding": "floor_to_precision",
                    "bridge_retained_credits": dust,
                },
            )
            data["settlement_batches"][batch.batch_id] = batch.as_dict()
            self._save_unlocked(data)
            return {
                "ok": True,
                "idempotent": False,
                "batch": batch.as_dict(),
                "batch_claim_count": len(selected_ids),
                "total_credits_exact": exact_total,
                "total_credits_published": published,
                "dust_credits": dust,
                "bridge_retained_credits": dust,
                "worker_settlement_totals": self._worker_settlement_totals_from_data(data, clean_worker, precision_places=precision),
                "ledger": self._status_from_data(data),
            }

    def settle_worker_settlement_batch(
        self,
        *,
        batch_id: str,
        settlement_reference: str = "",
        settlement_tx_hash: str = "",
        payout_rail: str = "",
        operator_id: str = "",
        settlement_proof: dict[str, Any] | None = None,
        idempotency_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_batch_id = str(batch_id or "").strip()
        if not clean_batch_id:
            raise ValueError("batch_id is required.")
        clean_key = str(idempotency_key or "").strip()
        now = utc_now()

        with self._lock:
            data = self._load_unlocked()
            payload = data["settlement_batches"].get(clean_batch_id)
            if not isinstance(payload, dict):
                raise KeyError(f"Unknown worker settlement batch: {clean_batch_id}")
            batch = _batch_from_dict(payload)
            if batch.status == "settled":
                return {
                    "ok": True,
                    "idempotent": True,
                    "batch": batch.as_dict(),
                    "settled_credits": batch.total_credits_published,
                    "additional_settled_credits": 0,
                    "bridge_retained_credits": batch.dust_credits,
                    "worker_settlement_totals": self._worker_settlement_totals_from_data(data, batch.worker_node_id, precision_places=batch.precision_places),
                    "ledger": self._status_from_data(data),
                }
            if batch.status == "cancelled":
                raise ValueError(f"Cannot settle cancelled worker settlement batch {clean_batch_id}.")

            proof_fields = _settlement_proof_identity(
                batch=batch,
                payout_rail=payout_rail,
                operator_id=operator_id,
                settlement_reference=settlement_reference,
                settlement_tx_hash=settlement_tx_hash,
                settlement_proof=settlement_proof,
            )

            updated_batch = WorkerSettlementBatch(
                batch_id=batch.batch_id,
                window_start=batch.window_start,
                window_end=batch.window_end,
                total_credits_exact=0,
                total_credits_published=0,
                dust_credits=0,
                worker_count=batch.worker_count,
                total_credit_wei_exact=batch.total_credit_wei_exact,
                total_credit_wei_published=batch.total_credit_wei_published,
                dust_credit_wei=batch.dust_credit_wei,
                batch_root=batch.batch_root,
                status="settled",
                worker_node_id=batch.worker_node_id,
                claim_ids=batch.claim_ids,
                precision_places=batch.precision_places,
                rounding_bucket_credits=batch.rounding_bucket_credits,
                bridge_account_id=batch.bridge_account_id,
                payout_rail=proof_fields["payout_rail"],
                operator_id=proof_fields["operator_id"],
                settlement_reference=proof_fields["settlement_reference"],
                settlement_tx_hash=proof_fields["settlement_tx_hash"],
                settlement_proof_id=proof_fields["settlement_proof_id"],
                settlement_proof_hash=proof_fields["settlement_proof_hash"],
                settled_at=now,
                created_at=batch.created_at,
                metadata={
                    **dict(batch.metadata or {}),
                    **dict(metadata or {}),
                    "settle_idempotency_key": clean_key,
                    "bridge_retained_credits": batch.dust_credits,
                    "payout_rail": proof_fields["payout_rail"],
                    "operator_id": proof_fields["operator_id"],
                    "settlement_proof_id": proof_fields["settlement_proof_id"],
                    "settlement_proof_hash": proof_fields["settlement_proof_hash"],
                    "settlement_proof": proof_fields["settlement_proof"],
                },
            )
            data["settlement_batches"][updated_batch.batch_id] = updated_batch.as_dict()

            updated_claims = []
            for claim_id in batch.claim_ids:
                claim_payload = data["worker_claims"].get(claim_id)
                if not isinstance(claim_payload, dict):
                    raise KeyError(f"Settlement batch references unknown worker claim: {claim_id}")
                claim = _claim_from_dict(claim_payload)
                if claim.worker_node_id != batch.worker_node_id:
                    raise ValueError(f"Settlement batch claim {claim_id} belongs to another worker.")
                if claim.status == "settled":
                    updated_claims.append(claim)
                    continue
                if claim.status != "claimed":
                    raise ValueError(f"Settlement batch claim {claim_id} is not claimed; status is {claim.status}.")
                settled_claim = WorkerClaim(
                    claim_id=claim.claim_id,
                    worker_node_id=claim.worker_node_id,
                    claimed_credits=0,
                    claimed_credit_wei=claim.claimed_credit_wei,
                    earning_ids=claim.earning_ids,
                    status="settled",
                    idempotency_key=claim.idempotency_key,
                    settlement_tx_hash=settlement_tx_hash or claim.settlement_tx_hash,
                    created_at=claim.created_at,
                    metadata={
                        **dict(claim.metadata or {}),
                        "settlement_batch_id": updated_batch.batch_id,
                        "settled_at": now,
                    },
                )
                data["worker_claims"][settled_claim.claim_id] = settled_claim.as_dict()
                updated_claims.append(settled_claim)

            settlement_tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "batch_settled", "batch_id": updated_batch.batch_id}),
                account_id=updated_batch.worker_node_id or "worker-settlement",
                transaction_type="batch_settled",
                credits=0,
                credit_wei=updated_batch.total_credit_wei_published,
                created_at=now,
                worker_node_id=updated_batch.worker_node_id,
                batch_id=updated_batch.batch_id,
                memo=f"worker settlement batch {updated_batch.batch_id} settled",
                metadata={
                    **dict(metadata or {}),
                    "idempotency_key": clean_key,
                    "settlement_reference": proof_fields["settlement_reference"],
                    "settlement_tx_hash": proof_fields["settlement_tx_hash"],
                    "payout_rail": proof_fields["payout_rail"],
                    "operator_id": proof_fields["operator_id"],
                    "settlement_proof_id": proof_fields["settlement_proof_id"],
                    "settlement_proof_hash": proof_fields["settlement_proof_hash"],
                    "claim_ids": list(updated_batch.claim_ids),
                    "total_credits_exact": updated_batch.total_credits_exact,
                    "total_credits_published": updated_batch.total_credits_published,
                    "dust_credits": updated_batch.dust_credits,
                    "bridge_account_id": updated_batch.bridge_account_id,
                    "bridge_retained_credits": updated_batch.dust_credits,
                    "precision_places": updated_batch.precision_places,
                    "rounding_bucket_credits": updated_batch.rounding_bucket_credits,
                },
            )
            data["transactions"].append(settlement_tx.as_dict())
            self._save_unlocked(data)
            return {
                "ok": True,
                "idempotent": False,
                "batch": updated_batch.as_dict(),
                "claims": [claim.as_dict() for claim in updated_claims],
                "transaction": settlement_tx.as_dict(),
                "settled_credits": updated_batch.total_credits_published,
                "additional_settled_credits": updated_batch.total_credits_published,
                "bridge_retained_credits": updated_batch.dust_credits,
                "worker_settlement_totals": self._worker_settlement_totals_from_data(data, updated_batch.worker_node_id, precision_places=updated_batch.precision_places),
                "ledger": self._status_from_data(data),
            }

    def record_worker_settlement_proof(
        self,
        *,
        batch_id: str,
        settlement_reference: str = "",
        settlement_tx_hash: str = "",
        payout_rail: str = "operator-manual",
        operator_id: str = "",
        settlement_proof: dict[str, Any] | None = None,
        idempotency_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record an operator/manual payout proof for an opened settlement batch.

        Phase 7a intentionally does not move funds on-chain.  It records the
        durable proof that an operator payout rail executed the rounded published
        batch amount, while exact claim and dust values remain audit-only.
        """
        if not str(settlement_reference or settlement_tx_hash or "").strip():
            raise ValueError("settlement_reference or settlement_tx_hash is required.")
        return self.settle_worker_settlement_batch(
            batch_id=batch_id,
            settlement_reference=settlement_reference,
            settlement_tx_hash=settlement_tx_hash,
            payout_rail=payout_rail or "operator-manual",
            operator_id=operator_id,
            settlement_proof=settlement_proof,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )

    def record_worker_settlement_chain_execution(
        self,
        *,
        batch_id: str,
        chain_id: int,
        contract_address: str,
        recipient_address: str,
        payout_units_executed: int,
        settlement_tx_hash: str,
        proposal_id: str = "",
        block_number: int | None = None,
        payout_rail: str = "xlag-bridge-reserve",
        operator_id: str = "",
        settlement_reference: str = "",
        settlement_proof: dict[str, Any] | None = None,
        idempotency_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a Phase 7b externally executed chain payout receipt.

        The hub still does not sign or submit reserve transactions.  The chain
        receipt must come from an external wallet/operator flow such as
        XLagBridgeReserve propose/second/execute.  This method validates that the
        executed chain payout amount matches the rounded published batch amount,
        then records the durable settlement proof exactly once.
        """
        clean_batch_id = str(batch_id or "").strip()
        if not clean_batch_id:
            raise ValueError("batch_id is required.")
        clean_chain_id = _positive_chain_id(chain_id)
        clean_contract = _clean_eth_address(contract_address, field_name="contract_address")
        clean_recipient = _clean_eth_address(recipient_address, field_name="recipient_address")
        clean_tx_hash = _clean_eth_tx_hash(settlement_tx_hash)
        executed_units = positive_int(payout_units_executed)
        if executed_units <= 0:
            raise ValueError("payout_units_executed must be positive.")
        clean_proposal_id = str(proposal_id or "").strip()
        clean_rail = str(payout_rail or "xlag-bridge-reserve").strip() or "xlag-bridge-reserve"
        clean_operator = clean_account_id(operator_id, default="") if operator_id else ""
        clean_reference = str(settlement_reference or "").strip()
        if not clean_reference:
            proposal_label = clean_proposal_id or clean_tx_hash
            clean_reference = f"{clean_rail}:{clean_chain_id}:{clean_contract}:{proposal_label}"
        clean_key = str(idempotency_key or "").strip()
        clean_metadata = dict(metadata or {}) if isinstance(metadata, dict) else {}
        clean_block = positive_int(block_number) if block_number is not None else 0

        data = self._load()
        payload = data["settlement_batches"].get(clean_batch_id)
        if not isinstance(payload, dict):
            raise KeyError(f"Unknown worker settlement batch: {clean_batch_id}")
        batch = _batch_from_dict(payload)
        if executed_units != batch.total_credits_published:
            raise ValueError(
                "payout_units_executed must equal the rounded published settlement amount "
                f"({batch.total_credits_published})."
            )
        if batch.status == "settled":
            if batch.payout_rail and batch.payout_rail != clean_rail:
                raise ValueError(f"Settlement batch {clean_batch_id} is already settled via {batch.payout_rail}.")
            if not batch.settlement_tx_hash:
                raise ValueError(f"Settlement batch {clean_batch_id} is already settled without a chain tx hash.")
            if batch.settlement_tx_hash.lower() != clean_tx_hash:
                raise ValueError(f"Settlement batch {clean_batch_id} is already settled with a different tx hash.")

            stored_chain_metadata = _copy_dict(batch.metadata)
            for field_name, expected in (
                ("chain_id", clean_chain_id),
                ("contract_address", clean_contract),
                ("recipient_address", clean_recipient),
                ("payout_units_executed", executed_units),
                ("proposal_id", clean_proposal_id),
                ("block_number", clean_block),
            ):
                _require_same_chain_execution_field(
                    stored_chain_metadata,
                    field_name,
                    expected,
                    batch_id=clean_batch_id,
                )

            return {
                "ok": True,
                "idempotent": True,
                "batch": batch.as_dict(),
                "settled_credits": batch.total_credits_published,
                "additional_settled_credits": 0,
                "bridge_retained_credits": batch.dust_credits,
                "chain_payout_execution": {
                    "chain_id": clean_chain_id,
                    "contract_address": clean_contract,
                    "recipient_address": clean_recipient,
                    "payout_units_executed": executed_units,
                    "bridge_retained_credits": batch.dust_credits,
                    "proposal_id": clean_proposal_id,
                    "settlement_tx_hash": clean_tx_hash,
                    "block_number": clean_block,
                    "payout_rail": clean_rail,
                    "status": "already_recorded",
                },
                "worker_settlement_totals": self._worker_settlement_totals_from_data(
                    data, batch.worker_node_id, precision_places=batch.precision_places
                ),
                "ledger": self._status_from_data(data),
            }

        chain_receipt = {
            "phase": "phase7b-chain-payout-execution",
            "chain_id": clean_chain_id,
            "contract_address": clean_contract,
            "recipient_address": clean_recipient,
            "payout_units_executed": executed_units,
            "bridge_retained_credits": batch.dust_credits,
            "proposal_id": clean_proposal_id,
            "settlement_tx_hash": clean_tx_hash,
            "block_number": clean_block,
            "payout_rail": clean_rail,
            "rounded_public_payout": True,
        }
        proof_payload = {
            **chain_receipt,
            **(dict(settlement_proof or {}) if isinstance(settlement_proof, dict) else {}),
        }
        result = self.settle_worker_settlement_batch(
            batch_id=clean_batch_id,
            settlement_reference=clean_reference,
            settlement_tx_hash=clean_tx_hash,
            payout_rail=clean_rail,
            operator_id=clean_operator,
            settlement_proof=proof_payload,
            idempotency_key=clean_key,
            metadata={
                **clean_metadata,
                "phase7b_chain_payout_execution": True,
                "chain_id": clean_chain_id,
                "contract_address": clean_contract,
                "recipient_address": clean_recipient,
                "payout_units_executed": executed_units,
                "proposal_id": clean_proposal_id,
                "block_number": clean_block,
            },
        )
        result["chain_payout_execution"] = {
            **chain_receipt,
            "settlement_reference": clean_reference,
            "settlement_proof_id": result.get("batch", {}).get("settlement_proof_id", ""),
            "settlement_proof_hash": result.get("batch", {}).get("settlement_proof_hash", ""),
            "status": "recorded",
        }
        return result


    def worker_claim_totals(self, worker_node_id: str) -> dict[str, Any]:
        if not str(worker_node_id or "").strip():
            raise ValueError("worker_node_id is required.")
        clean_worker = clean_worker_id(worker_node_id)
        data = self._load()
        return self._worker_claim_totals_from_data(data, clean_worker)

    def record_worker_claim(
        self,
        *,
        worker_node_id: str,
        earning_ids: list[str] | None = None,
        claim_credits: int | None = None,
        idempotency_key: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(worker_node_id or "").strip():
            raise ValueError("worker_node_id is required.")
        clean_worker = clean_worker_id(worker_node_id)
        clean_key = str(idempotency_key or "").strip()
        explicit_earning_ids = earning_ids is not None
        requested_ids = [str(item or "").strip() for item in (earning_ids or []) if str(item or "").strip()]
        if len(requested_ids) != len(set(requested_ids)):
            raise ValueError("earning_ids must not contain duplicates.")
        requested_claim_credits = None if claim_credits is None else positive_int(claim_credits)
        now = utc_now()

        with self._lock:
            data = self._load_unlocked()

            if clean_key:
                for item in data["worker_claims"].values():
                    if not isinstance(item, dict):
                        continue
                    claim = _claim_from_dict(item)
                    if claim.worker_node_id == clean_worker and claim.idempotency_key == clean_key:
                        return {
                            "ok": True,
                            "idempotent": True,
                            "claim": claim.as_dict(),
                            "claimed_credits": claim.claimed_credits,
                            "claimed_count": len(claim.earning_ids),
                            "worker_claim_totals": self._worker_claim_totals_from_data(data, clean_worker),
                            "ledger": self._status_from_data(data),
                        }

            before = self._worker_claim_totals_from_data(data, clean_worker)
            claimable_ids = [str(item) for item in before["claimable_earning_ids"]]
            selected_ids = requested_ids if explicit_earning_ids else claimable_ids
            if not selected_ids:
                if requested_claim_credits not in (None, 0):
                    raise ValueError("claim_credits cannot be positive when no earnings are claimable.")
                return {
                    "ok": True,
                    "idempotent": False,
                    "claim": None,
                    "claimed_credits": 0,
                    "claimed_count": 0,
                    "worker_claim_totals": before,
                    "ledger": self._status_from_data(data),
                }

            earnings_by_id = {
                earning.earning_id: earning
                for earning in (_earning_from_dict(item) for item in data["worker_earnings"].values())
            }
            already_claimed_ids = set(before["claimed_earning_ids"])
            selected_earnings: list[WorkerEarning] = []
            for earning_id in selected_ids:
                earning = earnings_by_id.get(earning_id)
                if earning is None:
                    raise KeyError(f"Unknown worker earning: {earning_id}")
                if earning.worker_node_id != clean_worker:
                    raise ValueError(f"Earning {earning_id} belongs to another worker.")
                if earning.status != "earned":
                    raise ValueError(f"Earning {earning_id} is not finalized/earned; status is {earning.status}.")
                if earning_id in already_claimed_ids:
                    raise ValueError(f"Earning {earning_id} has already been claimed.")
                selected_earnings.append(earning)

            claimed_credit_wei = sum(earning.earned_credit_wei for earning in selected_earnings)
            if requested_claim_credits is not None and credit_count_to_wei(requested_claim_credits) != claimed_credit_wei:
                raise ValueError(f"claim_credits mismatch: requested {requested_claim_credits}, selected earnings total {credit_wei_to_decimal_text(claimed_credit_wei)}.")
            if claimed_credit_wei <= 0:
                return {
                    "ok": True,
                    "idempotent": False,
                    "claim": None,
                    "claimed_credits": 0,
                    "claimed_count": 0,
                    "worker_claim_totals": before,
                    "ledger": self._status_from_data(data),
                }

            claim = WorkerClaim(
                claim_id="",
                worker_node_id=clean_worker,
                claimed_credits=0,
                claimed_credit_wei=claimed_credit_wei,
                earning_ids=selected_ids,
                idempotency_key=clean_key,
                created_at=now,
                metadata=dict(metadata or {}),
            )
            data["worker_claims"][claim.claim_id] = claim.as_dict()
            claim_tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "worker_claimed", "claim_id": claim.claim_id}),
                account_id=clean_worker,
                transaction_type="worker_claimed",
                credits=0,
                credit_wei=claimed_credit_wei,
                created_at=now,
                worker_node_id=clean_worker,
                memo=memo or f"worker claimed {len(selected_ids)} earning(s)",
                metadata={
                    **dict(metadata or {}),
                    "claim_id": claim.claim_id,
                    "earning_ids": selected_ids,
                    "idempotency_key": clean_key,
                },
            )
            data["transactions"].append(claim_tx.as_dict())
            self._save_unlocked(data)
            return {
                "ok": True,
                "idempotent": False,
                "claim": claim.as_dict(),
                "claimed_credit_wei": str(claimed_credit_wei),
                "claimed_credits_display": credit_wei_to_decimal_text(claimed_credit_wei),
                "claimed_count": len(selected_ids),
                "transaction": claim_tx.as_dict(),
                "worker_claim_totals": self._worker_claim_totals_from_data(data, clean_worker),
                "ledger": self._status_from_data(data),
            }


    def bridge_reconciliation_totals(self, account_id: str) -> dict[str, Any]:
        clean_id = clean_account_id(account_id)
        now = utc_now()
        with self._lock:
            data = self._load_unlocked()
            account, repaired_account, expected_available = self._repair_bridge_withdrawal_available_unlocked(
                data,
                clean_id,
                now=now,
            )
            if repaired_account:
                self._save_unlocked(data)
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
                "expected_available_credits": expected_available,
                "account_repaired": repaired_account,
                "account": account.as_dict(),
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
                    bridge_completed_credits=account.bridge_completed_credits,
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

            account, repaired_account, _expected_available = self._repair_bridge_withdrawal_available_unlocked(
                data,
                clean_id,
                now=now,
            )
            if repaired_account:
                base_metadata.setdefault("hub_available_repaired", True)

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
                bridge_completed_credits=account.bridge_completed_credits,
                available_credit_wei=account.available_credit_wei + credit_count_to_wei(clean_credits),
                held_credit_wei=account.held_credit_wei,
                spent_credit_wei=account.spent_credit_wei,
                earned_credit_wei=account.earned_credit_wei,
                bridge_completed_credit_wei=account.bridge_completed_credit_wei,
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
                credit_wei=credit_count_to_wei(clean_credits),
                created_at=now,
                memo=memo,
                metadata=dict(metadata or {}),
            )
            data["accounts"][clean_id] = account.as_dict()
            data["transactions"].append(tx.as_dict())
            self._save_unlocked(data)
            return {"ok": True, "account": account.as_dict(), "transaction": tx.as_dict(), "ledger": self._status_from_data(data)}

    def ensure_account_available_credit_wei(
        self,
        *,
        account_id: str,
        minimum_available_credit_wei: int | str,
        owner_address: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dev/testing helper: top up an account until available credit reaches a floor.

        Normal bridge escrow imports remain idempotent by chain event.  Local smoke
        tools, however, need a repeatable way to ensure the dev requester wallet
        can run the same paid request multiple times after direct-spend charging
        replaced credit holds.  This method only adds the delta needed to restore
        the requested available-credit floor.
        """

        clean_minimum_wei = positive_credit_wei(minimum_available_credit_wei)
        if clean_minimum_wei <= 0:
            raise ValueError("minimum_available_credit_wei must be positive.")
        clean_id = clean_account_id(account_id)
        now = utc_now()
        clean_metadata = dict(metadata or {})

        with self._lock:
            data = self._load_unlocked()
            account = self._ensure_account_unlocked(
                data,
                clean_id,
                owner_address=owner_address,
                metadata=clean_metadata,
                now=now,
            )
            current_available_wei = int(account.available_credit_wei)
            top_up_wei = max(0, clean_minimum_wei - current_available_wei)
            if top_up_wei <= 0:
                return {
                    "ok": True,
                    "top_up_applied": False,
                    "top_up_credit_wei": "0",
                    "minimum_available_credit_wei": str(clean_minimum_wei),
                    "account": account.as_dict(),
                    "ledger": self._status_from_data(data),
                }

            account = HubCreditAccount(
                account_id=account.account_id,
                owner_address=owner_address or account.owner_address,
                available_credit_wei=current_available_wei + top_up_wei,
                held_credit_wei=account.held_credit_wei,
                spent_credit_wei=account.spent_credit_wei,
                earned_credit_wei=account.earned_credit_wei,
                bridge_completed_credit_wei=account.bridge_completed_credit_wei,
                created_at=account.created_at,
                updated_at=now,
                metadata={**account.metadata, **clean_metadata},
            )
            tx = HubCreditTransaction(
                transaction_id=stable_id(
                    "ctx",
                    {
                        "account_id": clean_id,
                        "type": "dev_wallet_funding_top_up",
                        "credit_wei": str(top_up_wei),
                        "created_at": now,
                        "memo": memo,
                    },
                ),
                account_id=clean_id,
                transaction_type="admin_adjustment",
                credits=0,
                credit_wei=top_up_wei,
                created_at=now,
                memo=memo,
                metadata={
                    **clean_metadata,
                    "minimum_available_credit_wei": str(clean_minimum_wei),
                    "previous_available_credit_wei": str(current_available_wei),
                },
            )
            data["accounts"][clean_id] = account.as_dict()
            data["transactions"].append(tx.as_dict())
            self._save_unlocked(data)
            return {
                "ok": True,
                "top_up_applied": True,
                "top_up_credit_wei": str(top_up_wei),
                "minimum_available_credit_wei": str(clean_minimum_wei),
                "account": account.as_dict(),
                "transaction": tx.as_dict(),
                "ledger": self._status_from_data(data),
            }


    def record_completed_bridge_deposit(
        self,
        *,
        account_id: str,
        owner_address: str = "",
        chain_completed_credit_wei: int | str,
        deposit_id: str,
        completion_tx_hash: str = "",
        chain_id: int = 0,
        contract_address: str = "",
        completed_units: int = 0,
        deposit_amount_units: int = 0,
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Reconcile wallet funding from the escrow contract's completed aggregate.

        The contract reports completed base units.  Those units are the canonical
        credit wei amount; they are not divided into whole credits.
        """

        clean_id = clean_account_id(account_id)
        clean_chain_completed_wei = positive_credit_wei(chain_completed_credit_wei)
        clean_deposit_id = str(deposit_id or "").strip().lower()
        if not clean_deposit_id:
            raise ValueError("deposit_id is required.")
        now = utc_now()
        tx_hash = str(completion_tx_hash or "").strip().lower()
        base_metadata = {
            **dict(metadata or {}),
            "deposit_id": clean_deposit_id,
            "chain_completed_credit_wei": str(clean_chain_completed_wei),
            "chain_completed_credits_display": credit_wei_to_decimal_text(clean_chain_completed_wei),
            "completed_units": str(positive_int(completed_units)),
            "deposit_amount_units": str(positive_int(deposit_amount_units)),
            "completion_tx_hash": tx_hash,
            "chain_id": positive_int(chain_id),
            "contract_address": str(contract_address or "").strip().lower(),
        }

        with self._lock:
            data = self._load_unlocked()
            account = self._ensure_account_unlocked(
                data,
                clean_id,
                owner_address=owner_address,
                metadata={"funding_model": "hub_credit_bridge_escrow_wallet_v2"},
                now=now,
            )

            local_completed_wei = positive_credit_wei(account.bridge_completed_credit_wei)
            if clean_chain_completed_wei < local_completed_wei:
                raise ValueError(
                    "Hub local bridge_completed_credit_wei is ahead of the chain-completed aggregate "
                    f"for {clean_id}: local={credit_wei_to_decimal_text(local_completed_wei)} credits, "
                    f"chain={credit_wei_to_decimal_text(clean_chain_completed_wei)} credits."
                )

            delta_wei = clean_chain_completed_wei - local_completed_wei
            if delta_wei <= 0:
                return {
                    "ok": True,
                    "idempotent": True,
                    "delta_credit_wei": "0",
                    "delta_credits_display": credit_wei_to_decimal_text(0),
                    "chain_completed_credit_wei": str(clean_chain_completed_wei),
                    "chain_completed_credits_display": credit_wei_to_decimal_text(clean_chain_completed_wei),
                    "local_completed_credit_wei": str(local_completed_wei),
                    "local_completed_credits_display": credit_wei_to_decimal_text(local_completed_wei),
                    "deposit_id": clean_deposit_id,
                    "account": account.as_dict(),
                    "ledger": self._status_from_data(data),
                }

            account = HubCreditAccount(
                account_id=account.account_id,
                owner_address=owner_address or account.owner_address,
                available_credits=account.available_credits,
                held_credits=account.held_credits,
                spent_credits=account.spent_credits,
                earned_credits=account.earned_credits,
                bridge_completed_credits=account.bridge_completed_credits,
                available_credit_wei=account.available_credit_wei + delta_wei,
                held_credit_wei=account.held_credit_wei,
                spent_credit_wei=account.spent_credit_wei,
                earned_credit_wei=account.earned_credit_wei,
                bridge_completed_credit_wei=clean_chain_completed_wei,
                created_at=account.created_at,
                updated_at=now,
                metadata={**account.metadata, "funding_model": "hub_credit_bridge_escrow_wallet_v2"},
            )
            tx = HubCreditTransaction(
                transaction_id=stable_id(
                    "ctx",
                    {
                        "account_id": clean_id,
                        "type": "bridge_deposit_completed",
                        "deposit_id": clean_deposit_id,
                        "chain_completed_credit_wei": str(clean_chain_completed_wei),
                    },
                ),
                account_id=clean_id,
                transaction_type="bridge_deposit_completed",
                credits=0,
                credit_wei=delta_wei,
                created_at=now,
                deposit_id=clean_deposit_id,
                memo=memo or f"bridge deposit completed for {clean_id}",
                metadata=base_metadata,
            )
            data["accounts"][clean_id] = account.as_dict()
            data["transactions"].append(tx.as_dict())
            self._save_unlocked(data)
            return {
                "ok": True,
                "idempotent": False,
                "delta_credit_wei": str(delta_wei),
                "delta_credits_display": credit_wei_to_decimal_text(delta_wei),
                "chain_completed_credit_wei": str(clean_chain_completed_wei),
                "chain_completed_credits_display": credit_wei_to_decimal_text(clean_chain_completed_wei),
                "local_completed_credit_wei": str(local_completed_wei),
                "local_completed_credits_display": credit_wei_to_decimal_text(local_completed_wei),
                "deposit_id": clean_deposit_id,
                "account": account.as_dict(),
                "transaction": tx.as_dict(),
                "ledger": self._status_from_data(data),
            }

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
                bridge_completed_credits=account.bridge_completed_credits,
                available_credit_wei=account.available_credit_wei + deposit.credits_granted_wei,
                held_credit_wei=account.held_credit_wei,
                spent_credit_wei=account.spent_credit_wei,
                earned_credit_wei=account.earned_credit_wei,
                bridge_completed_credit_wei=account.bridge_completed_credit_wei,
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
                credit_wei=deposit.credits_granted_wei,
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
        raise RuntimeError("Credit holds are disabled. Spend credits directly when the request goes through.")

    def create_hold_credit_wei(
        self,
        *,
        account_id: str,
        request_id: str,
        credit_wei: int | str,
        expires_at: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError("Credit holds are disabled. Spend credits directly when the request goes through.")

    def release_hold(
        self,
        *,
        hold_id: str,
        reason: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError("Credit holds are disabled. There are no active holds to release.")

    def charge_hold(
        self,
        *,
        hold_id: str,
        charged_credits: int,
        worker_node_id: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError("Credit holds are disabled. Spend credits directly when the request goes through.")

    def charge_hold_credit_wei(
        self,
        *,
        hold_id: str,
        charged_credit_wei: int | str,
        worker_node_id: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError("Credit holds are disabled. Spend credits directly when the request goes through.")

    def spend_request_credit(
        self,
        *,
        account_id: str,
        request_id: str,
        credits: int,
        worker_node_id: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.spend_request_credit_wei(
            account_id=account_id,
            request_id=request_id,
            credit_wei=credit_count_to_wei(credits),
            worker_node_id=worker_node_id,
            memo=memo,
            metadata=metadata,
        )

    def spend_request_credit_wei(
        self,
        *,
        account_id: str,
        request_id: str,
        credit_wei: int | str,
        worker_node_id: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically spend requester credits without creating or honoring holds.

        Legacy active holds are collapsed back into spendable balance first so
        older stuck requests cannot keep wallet funds trapped after the hold path
        has been removed.
        """

        clean_id = clean_account_id(account_id)
        clean_request = str(request_id or "").strip()
        clean_worker = clean_worker_id(worker_node_id, default="") if worker_node_id else ""
        clean_credit_wei = positive_credit_wei(credit_wei)
        if not clean_request:
            raise ValueError("request_id is required.")
        if clean_credit_wei <= 0:
            raise ValueError("credit_wei must be positive.")
        now = utc_now()
        charge = RequestCharge(
            charge_id="",
            account_id=clean_id,
            request_id=clean_request,
            hold_id="",
            charged_credits=0,
            charged_credit_wei=clean_credit_wei,
            released_credits=0,
            released_credit_wei=0,
            worker_earning_id="",
            created_at=now,
        )

        with self._lock:
            data = self._load_unlocked()
            existing_payload = data["charges"].get(charge.charge_id)
            if isinstance(existing_payload, dict):
                existing_charge = _charge_from_dict(existing_payload)
                account = self._ensure_account_unlocked(data, existing_charge.account_id, now=now)
                earning = (
                    _earning_from_dict(data["worker_earnings"][existing_charge.worker_earning_id])
                    if existing_charge.worker_earning_id in data["worker_earnings"]
                    else None
                )
                return {
                    "ok": True,
                    "idempotent": True,
                    "account": account.as_dict(),
                    "charge": existing_charge.as_dict(),
                    "worker_earning": earning.as_private_dict() if earning else None,
                    "ledger": self._status_from_data(data),
                }

            account = self._ensure_account_unlocked(data, clean_id, now=now)
            spendable_wei = account.available_credit_wei + account.held_credit_wei
            if spendable_wei < clean_credit_wei:
                raise ValueError(
                    f"Insufficient Compute Credits for account {clean_id}: "
                    f"{credit_wei_to_decimal_text(spendable_wei)} credits spendable, "
                    f"{credit_wei_to_decimal_text(clean_credit_wei)} credits required."
                )

            collapsed_holds: list[str] = []
            if account.held_credit_wei > 0:
                for hold_id, payload in list(data["holds"].items()):
                    if not isinstance(payload, dict):
                        continue
                    hold = _hold_from_dict(payload)
                    if hold.account_id != clean_id or hold.status != "held":
                        continue
                    cancelled = HubCreditHold(
                        hold_id=hold.hold_id,
                        account_id=hold.account_id,
                        request_id=hold.request_id,
                        credits=hold.credits,
                        credit_wei=hold.credit_wei,
                        status="cancelled",
                        created_at=hold.created_at,
                        expires_at=hold.expires_at,
                        released_at=now,
                        charged_at=hold.charged_at,
                    )
                    data["holds"][cancelled.hold_id] = cancelled.as_dict()
                    collapsed_holds.append(cancelled.hold_id)

            earning: WorkerEarning | None = None
            if clean_worker:
                earning = self._record_worker_earning_unlocked(
                    data,
                    worker_node_id=clean_worker,
                    request_id=clean_request,
                    credits=0,
                    earned_credit_wei=clean_credit_wei,
                    now=now,
                    metadata={
                        **dict(metadata or {}),
                        "credit_wei": str(clean_credit_wei),
                        "credits_display": credit_wei_to_decimal_text(clean_credit_wei),
                        "direct_spend": True,
                    },
                )
                charge = RequestCharge(
                    charge_id=charge.charge_id,
                    account_id=charge.account_id,
                    request_id=charge.request_id,
                    hold_id="",
                    charged_credits=0,
                    charged_credit_wei=clean_credit_wei,
                    released_credits=0,
                    released_credit_wei=0,
                    worker_earning_id=earning.earning_id,
                    created_at=charge.created_at,
                )

            account = HubCreditAccount(
                account_id=account.account_id,
                owner_address=account.owner_address,
                available_credits=account.available_credits,
                held_credits=0,
                spent_credits=account.spent_credits,
                earned_credits=account.earned_credits,
                bridge_completed_credits=account.bridge_completed_credits,
                available_credit_wei=spendable_wei - clean_credit_wei,
                held_credit_wei=0,
                spent_credit_wei=account.spent_credit_wei + clean_credit_wei,
                earned_credit_wei=account.earned_credit_wei,
                bridge_completed_credit_wei=account.bridge_completed_credit_wei,
                created_at=account.created_at,
                updated_at=now,
                metadata=account.metadata,
            )
            charge_tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "request_charged", "charge_id": charge.charge_id}),
                account_id=account.account_id,
                transaction_type="request_charged",
                credits=0,
                credit_wei=clean_credit_wei,
                created_at=now,
                request_id=clean_request,
                worker_node_id=clean_worker,
                hold_id="",
                memo=memo or f"charged request {clean_request}",
                metadata={
                    **dict(metadata or {}),
                    "credit_wei": str(clean_credit_wei),
                    "credits_display": credit_wei_to_decimal_text(clean_credit_wei),
                    "direct_spend": True,
                    "legacy_holds_cancelled": collapsed_holds,
                },
            )

            data["accounts"][account.account_id] = account.as_dict()
            data["charges"][charge.charge_id] = charge.as_dict()
            data["transactions"].append(charge_tx.as_dict())
            self._save_unlocked(data)
            return {
                "ok": True,
                "idempotent": False,
                "account": account.as_dict(),
                "charge": charge.as_dict(),
                "worker_earning": earning.as_private_dict() if earning else None,
                "legacy_holds_cancelled": collapsed_holds,
                "ledger": self._status_from_data(data),
            }

    def record_worker_earning(
        self,
        *,
        worker_node_id: str,
        request_id: str,
        credits: int = 0,
        earned_credit_wei: int | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_worker = clean_worker_id(worker_node_id)
        clean_request = str(request_id or "").strip()
        clean_earned_wei = positive_credit_wei(earned_credit_wei, default=credit_count_to_wei(credits))
        if not clean_request:
            raise ValueError("request_id is required.")
        if clean_earned_wei <= 0:
            raise ValueError("earned_credit_wei must be positive.")
        now = utc_now()

        with self._lock:
            data = self._load_unlocked()
            earning = self._record_worker_earning_unlocked(
                data,
                worker_node_id=clean_worker,
                request_id=clean_request,
                credits=0,
                earned_credit_wei=clean_earned_wei,
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
        credits: int = 0,
        earned_credit_wei: int | str | None = None,
        now: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> WorkerEarning:
        clean_worker = clean_worker_id(worker_node_id)
        clean_request = str(request_id or "").strip()
        clean_earned_wei = positive_credit_wei(earned_credit_wei, default=credit_count_to_wei(credits))
        candidate = WorkerEarning(
            earning_id="",
            worker_node_id=clean_worker,
            request_id=clean_request,
            credits=0,
            earned_credit_wei=clean_earned_wei,
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
            earned_credits=0,
            bridge_completed_credits=worker_account.bridge_completed_credits,
            available_credit_wei=worker_account.available_credit_wei,
            held_credit_wei=worker_account.held_credit_wei,
            spent_credit_wei=worker_account.spent_credit_wei,
            earned_credit_wei=worker_account.earned_credit_wei + clean_earned_wei,
            bridge_completed_credit_wei=worker_account.bridge_completed_credit_wei,
            created_at=worker_account.created_at,
            updated_at=now,
            metadata=worker_account.metadata,
        )
        earning_tx = HubCreditTransaction(
            transaction_id=stable_id("ctx", {"type": "worker_earned", "earning_id": candidate.earning_id}),
            account_id=worker_account.account_id,
            transaction_type="worker_earned",
            credits=0,
            credit_wei=clean_earned_wei,
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


    def _worker_claim_totals_from_data(self, data: dict[str, Any], worker_node_id: str) -> dict[str, Any]:
        clean_worker = clean_worker_id(worker_node_id)
        earnings = [
            _earning_from_dict(item)
            for item in data["worker_earnings"].values()
            if isinstance(item, dict)
        ]
        worker_earnings = [earning for earning in earnings if earning.worker_node_id == clean_worker]
        finalized_earnings = [earning for earning in worker_earnings if earning.status == "earned"]
        claims = [
            _claim_from_dict(item)
            for item in data["worker_claims"].values()
            if isinstance(item, dict)
        ]
        worker_claims = [
            claim
            for claim in claims
            if claim.worker_node_id == clean_worker and claim.status in {"claimed", "settled"}
        ]

        claimed_earning_ids: set[str] = set()
        for claim in worker_claims:
            claimed_earning_ids.update(claim.earning_ids)
        finalized_by_id = {earning.earning_id: earning for earning in finalized_earnings}
        claimable_earnings = [
            earning
            for earning in sorted(finalized_earnings, key=lambda item: item.created_at)
            if earning.earning_id not in claimed_earning_ids
        ]
        claimed_finalized_ids = [
            earning_id
            for earning_id in sorted(claimed_earning_ids)
            if earning_id in finalized_by_id
        ]
        already_claimed_units = sum(finalized_by_id[earning_id].earned_credit_wei for earning_id in claimed_finalized_ids)
        claimable_units = sum(earning.earned_credit_wei for earning in claimable_earnings)

        return {
            "ok": True,
            "worker_node_id": clean_worker,
            "finalized_earning_units": sum(earning.earned_credit_wei for earning in finalized_earnings),
            "claimable_units": claimable_units,
            "already_claimed_units": already_claimed_units,
            "earning_count": len(finalized_earnings),
            "claim_count": len(worker_claims),
            "claimable_earning_ids": [earning.earning_id for earning in claimable_earnings],
            "claimed_earning_ids": claimed_finalized_ids,
            "can_claim": claimable_units > 0,
            "block_reason": "" if claimable_units > 0 else "no_finalized_unclaimed_worker_earnings",
            "earnings": [earning.as_private_dict() for earning in sorted(worker_earnings, key=lambda item: item.created_at)],
            "claims": [claim.as_dict() for claim in sorted(worker_claims, key=lambda item: item.created_at)],
        }


    def _worker_settlement_totals_from_data(
        self,
        data: dict[str, Any],
        worker_node_id: str,
        *,
        precision_places: int | None = None,
    ) -> dict[str, Any]:
        clean_worker = clean_worker_id(worker_node_id)
        precision = normalize_worker_payout_precision_places(precision_places)
        bucket_size = worker_payout_bucket_size_for_precision(precision)
        claims = [
            _claim_from_dict(item)
            for item in data["worker_claims"].values()
            if isinstance(item, dict)
        ]
        worker_claims = [
            claim
            for claim in claims
            if claim.worker_node_id == clean_worker and claim.status in {"claimed", "settled"}
        ]
        batches = [
            _batch_from_dict(item)
            for item in data["settlement_batches"].values()
            if isinstance(item, dict)
        ]
        worker_batches = [batch for batch in batches if batch.worker_node_id == clean_worker]

        batched_claim_ids: set[str] = set()
        settled_claim_ids: set[str] = set()
        for batch in worker_batches:
            if batch.status != "cancelled":
                batched_claim_ids.update(batch.claim_ids)
            if batch.status == "settled":
                settled_claim_ids.update(batch.claim_ids)

        settleable_claims = [
            claim
            for claim in sorted(worker_claims, key=lambda item: item.created_at)
            if claim.status == "claimed" and claim.claim_id not in batched_claim_ids
        ]
        exact_units = sum(claim.claimed_credit_wei for claim in settleable_claims)
        published_units, dust_units, precision, bucket_size = truncate_worker_payout_for_precision(
            exact_units,
            precision_places=precision,
        )
        settled_batches = [batch for batch in worker_batches if batch.status == "settled"]
        open_batches = [batch for batch in worker_batches if batch.status not in {"settled", "cancelled"}]

        return {
            "ok": True,
            "worker_node_id": clean_worker,
            "precision_places": precision,
            "rounding_bucket_credits": bucket_size,
            "settleable_units_exact": exact_units,
            "settleable_units_published": published_units,
            "settleable_dust_units": dust_units,
            "bridge_retained_units_if_settled": dust_units,
            "settleable_claim_count": len(settleable_claims),
            "settleable_claim_ids": [claim.claim_id for claim in settleable_claims],
            "batched_claim_ids": sorted(batched_claim_ids),
            "settled_claim_ids": sorted(settled_claim_ids),
            "claimed_units_total": sum(claim.claimed_credit_wei for claim in worker_claims),
            "settled_units_exact": sum(batch.total_credit_wei_exact for batch in settled_batches),
            "settled_units_published": sum(batch.total_credit_wei_published for batch in settled_batches),
            "bridge_retained_units": sum(batch.dust_credit_wei for batch in settled_batches),
            "open_batch_count": len(open_batches),
            "settled_batch_count": len(settled_batches),
            "can_create_batch": exact_units > 0,
            "block_reason": "" if exact_units > 0 else "no_claimed_unsettled_worker_claims",
            "claims": [claim.as_dict() for claim in sorted(worker_claims, key=lambda item: item.created_at)],
            "batches": [batch.as_dict() for batch in sorted(worker_batches, key=lambda item: item.created_at)],
        }


    def _account_bridge_funding_credits_unlocked(self, data: dict[str, Any], account_id: str) -> int:
        """Return durable requester-side funding credits for bridge reconciliation.

        This intentionally derives the funding base from immutable ledger records,
        not from account.available_credits, because available_credits may be stale
        after an interrupted withdrawal reconciliation.
        """

        clean_id = clean_account_id(account_id)
        transactions = [_transaction_from_dict(item) for item in data["transactions"]]
        funded_from_transactions = sum(
            tx.credits
            for tx in transactions
            if tx.account_id == clean_id and tx.transaction_type in {"admin_adjustment", "deposit_indexed", "bridge_deposit_completed"}
        )
        if funded_from_transactions > 0:
            return funded_from_transactions
        deposits = [_deposit_from_dict(item) for item in data["deposits"].values()]
        return sum(deposit.credits_granted for deposit in deposits if deposit.account_id == clean_id)

    def _repair_bridge_withdrawal_available_unlocked(
        self,
        data: dict[str, Any],
        account_id: str,
        *,
        now: str,
    ) -> tuple[HubCreditAccount, bool, int]:
        """Repair stale requester availability after an already-recorded withdrawal.

        Phase 3 smokes are intentionally rerunnable.  A prior run may have landed
        the withdrawal on-chain and recorded a withdrawal_released transaction, but
        crashed or ran older code before account.available_credits was debited.  In
        that state the transaction history is the durable source of truth.  This
        helper only lowers an over-stated available balance; it never mints or
        raises credits.
        """

        clean_id = clean_account_id(account_id)
        account = self._ensure_account_unlocked(data, clean_id, now=now)
        transactions = [_transaction_from_dict(item) for item in data["transactions"]]
        withdrawn_credits = sum(
            tx.credits
            for tx in transactions
            if tx.account_id == clean_id and tx.transaction_type == "withdrawal_released"
        )
        if withdrawn_credits <= 0:
            return account, False, account.available_credits

        funded_credits = self._account_bridge_funding_credits_unlocked(data, clean_id)
        expected_available = max(
            0,
            funded_credits - account.spent_credits - withdrawn_credits,
        )
        if account.available_credits <= expected_available:
            return account, False, expected_available

        repaired = HubCreditAccount(
            account_id=account.account_id,
            owner_address=account.owner_address,
            available_credits=expected_available,
            held_credits=0,
            spent_credits=account.spent_credits,
            earned_credits=account.earned_credits,
            bridge_completed_credits=account.bridge_completed_credits,
            created_at=account.created_at,
            updated_at=now,
            metadata=account.metadata,
        )
        data["accounts"][clean_id] = repaired.as_dict()
        return repaired, True, expected_available

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
                    bridge_completed_credits=account.bridge_completed_credits,
                    available_credit_wei=account.available_credit_wei,
                    held_credit_wei=account.held_credit_wei,
                    spent_credit_wei=account.spent_credit_wei,
                    earned_credit_wei=account.earned_credit_wei,
                    bridge_completed_credit_wei=account.bridge_completed_credit_wei,
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
        worker_claims = [_claim_from_dict(item) for item in data["worker_claims"].values()]
        settlement_batches = [_batch_from_dict(item) for item in data["settlement_batches"].values()]
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
            "worker_claim_count": len(worker_claims),
            "worker_settlement_batch_count": len(settlement_batches),
            "totals": {
                "available_credits": sum(account.available_credits for account in accounts),
                "held_credits": sum(account.held_credits for account in accounts),
                "spent_credits": sum(account.spent_credits for account in accounts),
                "earned_credits": sum(account.earned_credits for account in accounts),
                "bridge_completed_credits": sum(account.bridge_completed_credits for account in accounts),
                "available_credit_wei": str(sum(account.available_credit_wei for account in accounts)),
                "held_credit_wei": str(sum(account.held_credit_wei for account in accounts)),
                "spent_credit_wei": str(sum(account.spent_credit_wei for account in accounts)),
                "earned_credit_wei": str(sum(account.earned_credit_wei for account in accounts)),
                "bridge_completed_credit_wei": str(sum(account.bridge_completed_credit_wei for account in accounts)),
                "available_credits_display": credit_wei_to_decimal_text(sum(account.available_credit_wei for account in accounts)),
                "held_credits_display": credit_wei_to_decimal_text(sum(account.held_credit_wei for account in accounts)),
                "spent_credits_display": credit_wei_to_decimal_text(sum(account.spent_credit_wei for account in accounts)),
                "bridge_completed_credits_display": credit_wei_to_decimal_text(sum(account.bridge_completed_credit_wei for account in accounts)),
                "deposited_credits": sum(deposit.credits_granted for deposit in deposits),
                "purchased_credits": sum(deposit.credits_granted for deposit in deposits),
                "deposited_credit_wei": str(sum(deposit.credits_granted_wei for deposit in deposits)),
                "purchased_credit_wei": str(sum(deposit.credits_granted_wei for deposit in deposits)),
                "active_held_credits": sum(hold.credits for hold in holds if hold.status == "held"),
                "active_held_credit_wei": str(sum(hold.credit_wei for hold in holds if hold.status == "held")),
                "charged_credits": sum(charge.charged_credits for charge in charges),
                "charged_credit_wei": str(sum(charge.charged_credit_wei for charge in charges)),
                "worker_earned_credits": sum(earning.credits for earning in worker_earnings),
                "worker_claimed_credits": sum(claim.claimed_credits for claim in worker_claims if claim.status in {"claimed", "settled"}),
                "worker_settlement_exact_credits": sum(batch.total_credits_exact for batch in settlement_batches if batch.status == "settled"),
                "worker_settlement_published_credits": sum(batch.total_credits_published for batch in settlement_batches if batch.status == "settled"),
                "worker_settlement_dust_credits": sum(batch.dust_credits for batch in settlement_batches if batch.status == "settled"),
                "bridge_retained_worker_payout_dust_credits": sum(batch.dust_credits for batch in settlement_batches if batch.status == "settled"),
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
        accounts = {}
        for account_id, payload in raw_accounts.items():
            if not isinstance(payload, dict):
                continue
            account = _account_from_dict(payload)
            if account.held_credit_wei:
                account = HubCreditAccount(
                    account_id=account.account_id,
                    owner_address=account.owner_address,
                    available_credits=account.available_credits,
                    held_credits=0,
                    spent_credits=account.spent_credits,
                    earned_credits=account.earned_credits,
                    bridge_completed_credits=account.bridge_completed_credits,
                    available_credit_wei=account.available_credit_wei + account.held_credit_wei,
                    held_credit_wei=0,
                    spent_credit_wei=account.spent_credit_wei,
                    earned_credit_wei=account.earned_credit_wei,
                    bridge_completed_credit_wei=account.bridge_completed_credit_wei,
                    created_at=account.created_at,
                    updated_at=account.updated_at,
                    metadata=dict(account.metadata or {}),
                )
            accounts[clean_account_id(account_id)] = account.as_dict()

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
            if hold.status == "held":
                hold = HubCreditHold(
                    hold_id=hold.hold_id,
                    account_id=hold.account_id,
                    request_id=hold.request_id,
                    credits=hold.credits,
                    credit_wei=hold.credit_wei,
                    status="cancelled",
                    expires_at=hold.expires_at,
                    created_at=hold.created_at,
                    released_at=hold.released_at or utc_now(),
                    charged_at=hold.charged_at,
                )
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


        raw_claims = data.get("worker_claims") if isinstance(data.get("worker_claims"), dict) else {}
        worker_claims = {}
        for claim_id, item in raw_claims.items():
            if not isinstance(item, dict):
                continue
            try:
                claim = _claim_from_dict(item)
            except Exception:
                continue
            worker_claims[claim.claim_id or str(claim_id)] = claim.as_dict()

        raw_batches = data.get("settlement_batches") if isinstance(data.get("settlement_batches"), dict) else {}
        settlement_batches = {}
        for batch_id, item in raw_batches.items():
            if not isinstance(item, dict):
                continue
            try:
                batch = _batch_from_dict(item)
            except Exception:
                continue
            settlement_batches[batch.batch_id or str(batch_id)] = batch.as_dict()

        return {
            "version": HUB_CREDIT_LEDGER_STORE_VERSION,
            "accounts": accounts,
            "transactions": sorted(transactions, key=lambda item: str(item.get("created_at", ""))),
            "deposits": deposits,
            "holds": holds,
            "charges": charges,
            "worker_earnings": worker_earnings,
            "worker_claims": worker_claims,
            "settlement_batches": settlement_batches,
            "reports": data.get("reports") if isinstance(data.get("reports"), dict) else {},
        }
