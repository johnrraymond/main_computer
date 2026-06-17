from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any

from main_computer.credit_units import (
    credit_count_to_wei,
    credit_wei_to_decimal_text,
    credit_wei_to_whole_credits_floor,
    positive_credit_wei,
)


CREDIT_UNIT_NAME = "Compute Credits"
CREDIT_UNIT_KEY = "compute_credit"
CREDIT_LEDGER_VERSION = "hub-credit-ledger-v0"
CREDIT_BASE_UNITS_PER_CREDIT = 1_000_000
DEFAULT_WORKER_PAYOUT_PRECISION_PLACES = 3
MAX_WORKER_PAYOUT_PRECISION_PLACES = 18

CREDIT_TRANSACTION_TYPES = {
    "deposit_indexed",
    "hold_created",
    "hold_released",
    "request_charged",
    "worker_earned",
    "bridge_deposit_completed",
    "bridge_spend_rectified",
    "withdrawal_released",
    "refund_issued",
    "batch_settled",
    "worker_claimed",
    "admin_adjustment",
}

CREDIT_HOLD_STATUSES = {"held", "released", "charged", "expired", "cancelled"}
WORKER_EARNING_STATUSES = {"earned", "batched", "claimed", "paid", "cancelled"}
WORKER_CLAIM_STATUSES = {"claimed", "settled", "void"}
WORKER_BATCH_STATUSES = {"draft", "opened", "approved", "settled", "cancelled"}
QUALITY_REPORT_STATUSES = {"submitted", "reviewing", "accepted", "rejected", "actioned"}


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def clean_account_id(value: str, *, default: str = "anonymous-account") -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(value or "").strip().lower())
    return text or default


def clean_worker_id(value: str, *, default: str = "hub-worker") -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().lower())
    return text or default


def normalize_address(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("0x"):
        return "0x" + text[2:].lower()
    return text.lower()


def positive_int(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return max(0, int(default))
    return max(0, parsed)


def _credit_wei_or_legacy(value: Any, credits: Any) -> int:
    if value is None or str(value).strip() == "":
        legacy = positive_int(credits)
        return credit_count_to_wei(legacy) if legacy > 0 else 0
    return positive_credit_wei(value)


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_id(prefix: str, payload: dict[str, Any], *, length: int = 24) -> str:
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    clean_prefix = "".join(ch for ch in str(prefix or "id") if ch.isalnum() or ch in {"_", "-"}).strip("_-") or "id"
    return f"{clean_prefix}_{digest[:max(8, int(length))]}"


def make_worker_commitment(*, worker_node_id: str, request_id: str, epoch_salt: str) -> str:
    """Return a hub-verifiable worker commitment without exposing the worker id.

    The hub stores the reveal mapping. Users can submit the commitment with a
    report token, but the public value should not be enough to reconstruct the
    worker/request mapping without the hub's epoch salt.
    """

    clean_worker = clean_worker_id(worker_node_id)
    payload = f"{clean_worker}|{str(request_id or '').strip()}"
    digest = hmac.new(str(epoch_salt or "").encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"wcom_{digest[:48]}"


def make_report_token(*, hub_secret: str, account_id: str, request_id: str, worker_commitment: str) -> str:
    """Return an opaque report token for a completed request.

    The token intentionally contains no raw worker id. The hub can recompute it
    when validating a report because it retains the request/account/commitment
    mapping internally.
    """

    payload = {
        "account_id": clean_account_id(account_id),
        "request_id": str(request_id or "").strip(),
        "worker_commitment": str(worker_commitment or "").strip(),
        "version": CREDIT_LEDGER_VERSION,
    }
    digest = hmac.new(str(hub_secret or "").encode("utf-8"), canonical_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()
    return f"rpt_{digest[:56]}"


def token_digest(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def truncate_for_settlement(value: int, *, bucket_size: int) -> tuple[int, int]:
    """Round a worker settlement down into a public bucket and return dust.

    The exact internal earning remains in the hub ledger. The public settlement
    can publish only the bucketed amount and carry the dust forward, making
    request-level reconstruction harder.
    """

    clean_value = positive_int(value)
    clean_bucket = max(1, int(bucket_size or 1))
    published = (clean_value // clean_bucket) * clean_bucket
    return published, clean_value - published


def normalize_worker_payout_precision_places(value: Any = None) -> int:
    """Return a safe public payout precision in decimal credit places.

    Compute credits are represented internally as millionth-credit units.  The
    default public worker payout precision is 3 decimal places, so exact worker
    claim amounts such as 5.500123 credits are exported as 5.500 credits and the
    remaining 0.000123 credits stay in the bridge/reserve account.
    """

    if value is None or value == "":
        return DEFAULT_WORKER_PAYOUT_PRECISION_PLACES
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WORKER_PAYOUT_PRECISION_PLACES
    return min(MAX_WORKER_PAYOUT_PRECISION_PLACES, max(0, parsed))


def worker_payout_bucket_size_for_precision(value: Any = None) -> int:
    precision = normalize_worker_payout_precision_places(value)
    scale = 18 - precision
    return 10 ** max(0, scale)


def truncate_worker_payout_for_precision(value: int, *, precision_places: Any = None) -> tuple[int, int, int, int]:
    precision = normalize_worker_payout_precision_places(precision_places)
    bucket_size = worker_payout_bucket_size_for_precision(precision)
    published, dust = truncate_for_settlement(value, bucket_size=bucket_size)
    return published, dust, precision, bucket_size


def _asdict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return {}


@dataclass(frozen=True)
class ChainEventRef:
    chain_id: int
    contract_address: str
    tx_hash: str
    log_index: int
    block_number: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "chain_id", int(self.chain_id or 0))
        object.__setattr__(self, "contract_address", normalize_address(self.contract_address))
        object.__setattr__(self, "tx_hash", normalize_address(self.tx_hash))
        object.__setattr__(self, "log_index", max(0, int(self.log_index or 0)))
        object.__setattr__(self, "block_number", max(0, int(self.block_number or 0)))

    @property
    def event_uid(self) -> str:
        return stable_id(
            "evt",
            {
                "chain_id": self.chain_id,
                "contract_address": self.contract_address,
                "tx_hash": self.tx_hash,
                "log_index": self.log_index,
            },
        )

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["event_uid"] = self.event_uid
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChainEventRef":
        return cls(
            chain_id=positive_int(payload.get("chain_id")),
            contract_address=str(payload.get("contract_address", "")),
            tx_hash=str(payload.get("tx_hash", "")),
            log_index=positive_int(payload.get("log_index")),
            block_number=positive_int(payload.get("block_number")),
        )


@dataclass(frozen=True)
class HubCreditAccount:
    account_id: str
    owner_address: str = ""
    available_credits: int = 0
    held_credits: int = 0
    spent_credits: int = 0
    earned_credits: int = 0
    bridge_completed_credits: int = 0
    available_credit_wei: int | str | None = None
    held_credit_wei: int | str | None = None
    spent_credit_wei: int | str | None = None
    earned_credit_wei: int | str | None = None
    bridge_completed_credit_wei: int | str | None = None
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        now = utc_now()
        available_wei = _credit_wei_or_legacy(self.available_credit_wei, self.available_credits)
        held_wei = _credit_wei_or_legacy(self.held_credit_wei, self.held_credits)
        spent_wei = _credit_wei_or_legacy(self.spent_credit_wei, self.spent_credits)
        earned_wei = _credit_wei_or_legacy(self.earned_credit_wei, self.earned_credits)
        bridge_wei = _credit_wei_or_legacy(self.bridge_completed_credit_wei, self.bridge_completed_credits)
        object.__setattr__(self, "account_id", clean_account_id(self.account_id))
        object.__setattr__(self, "owner_address", normalize_address(self.owner_address))
        object.__setattr__(self, "available_credit_wei", available_wei)
        object.__setattr__(self, "held_credit_wei", held_wei)
        object.__setattr__(self, "spent_credit_wei", spent_wei)
        object.__setattr__(self, "earned_credit_wei", earned_wei)
        object.__setattr__(self, "bridge_completed_credit_wei", bridge_wei)
        object.__setattr__(self, "available_credits", credit_wei_to_whole_credits_floor(available_wei))
        object.__setattr__(self, "held_credits", credit_wei_to_whole_credits_floor(held_wei))
        object.__setattr__(self, "spent_credits", credit_wei_to_whole_credits_floor(spent_wei))
        object.__setattr__(self, "earned_credits", credit_wei_to_whole_credits_floor(earned_wei))
        object.__setattr__(self, "bridge_completed_credits", credit_wei_to_whole_credits_floor(bridge_wei))
        object.__setattr__(self, "created_at", self.created_at or now)
        object.__setattr__(self, "updated_at", self.updated_at or self.created_at or now)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "available_credit_wei",
            "held_credit_wei",
            "spent_credit_wei",
            "earned_credit_wei",
            "bridge_completed_credit_wei",
        ):
            data[key] = str(data[key])
        data["available_credits_display"] = credit_wei_to_decimal_text(self.available_credit_wei)
        data["held_credits_display"] = credit_wei_to_decimal_text(self.held_credit_wei)
        data["spent_credits_display"] = credit_wei_to_decimal_text(self.spent_credit_wei)
        data["earned_credits_display"] = credit_wei_to_decimal_text(self.earned_credit_wei)
        data["bridge_completed_credits_display"] = credit_wei_to_decimal_text(self.bridge_completed_credit_wei)
        return data


@dataclass(frozen=True)
class HubCreditTransaction:
    transaction_id: str
    account_id: str
    transaction_type: str
    credits: int
    credit_wei: int | str | None = None
    created_at: str = ""
    request_id: str = ""
    worker_node_id: str = ""
    batch_id: str = ""
    deposit_id: str = ""
    hold_id: str = ""
    memo: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        tx_type = str(self.transaction_type or "").strip()
        if tx_type not in CREDIT_TRANSACTION_TYPES:
            raise ValueError(f"Unsupported credit transaction type: {tx_type}")
        credit_wei = _credit_wei_or_legacy(self.credit_wei, self.credits)
        object.__setattr__(self, "transaction_id", self.transaction_id or stable_id("ctx", asdict(self)))
        object.__setattr__(self, "account_id", clean_account_id(self.account_id))
        object.__setattr__(self, "transaction_type", tx_type)
        object.__setattr__(self, "credit_wei", credit_wei)
        object.__setattr__(self, "credits", credit_wei_to_whole_credits_floor(credit_wei))
        object.__setattr__(self, "worker_node_id", clean_worker_id(self.worker_node_id, default="") if self.worker_node_id else "")
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["credit_wei"] = str(self.credit_wei)
        data["credits_display"] = credit_wei_to_decimal_text(self.credit_wei)
        return data


@dataclass(frozen=True)
class CreditDeposit:
    deposit_id: str
    account_id: str
    payer_address: str
    payment_asset: str
    payment_amount_base_units: int
    credits_granted: int
    chain_event: ChainEventRef
    credits_granted_wei: int | str | None = None
    status: str = "indexed"
    memo: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        event = self.chain_event if isinstance(self.chain_event, ChainEventRef) else ChainEventRef.from_dict(_asdict(self.chain_event))
        object.__setattr__(self, "chain_event", event)
        object.__setattr__(self, "deposit_id", self.deposit_id or stable_id("dep", {"account_id": self.account_id, "event_uid": event.event_uid}))
        object.__setattr__(self, "account_id", clean_account_id(self.account_id))
        object.__setattr__(self, "payer_address", normalize_address(self.payer_address))
        object.__setattr__(self, "payment_asset", normalize_address(self.payment_asset) or "native")
        grant_wei = _credit_wei_or_legacy(self.credits_granted_wei, self.credits_granted)
        object.__setattr__(self, "payment_amount_base_units", positive_int(self.payment_amount_base_units))
        object.__setattr__(self, "credits_granted_wei", grant_wei)
        object.__setattr__(self, "credits_granted", credit_wei_to_whole_credits_floor(grant_wei))
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["chain_event"] = self.chain_event.as_dict()
        data["credits_granted_wei"] = str(self.credits_granted_wei)
        data["credits_granted_display"] = credit_wei_to_decimal_text(self.credits_granted_wei)
        return data


@dataclass(frozen=True)
class HubCreditHold:
    hold_id: str
    account_id: str
    request_id: str
    credits: int
    credit_wei: int | str | None = None
    status: str = "held"
    created_at: str = ""
    expires_at: str = ""
    released_at: str = ""
    charged_at: str = ""

    def __post_init__(self) -> None:
        clean_status = str(self.status or "held").strip()
        if clean_status not in CREDIT_HOLD_STATUSES:
            raise ValueError(f"Unsupported credit hold status: {clean_status}")
        object.__setattr__(self, "hold_id", self.hold_id or stable_id("hold", {"account_id": self.account_id, "request_id": self.request_id}))
        object.__setattr__(self, "account_id", clean_account_id(self.account_id))
        credit_wei = _credit_wei_or_legacy(self.credit_wei, self.credits)
        object.__setattr__(self, "request_id", str(self.request_id or "").strip())
        object.__setattr__(self, "credit_wei", credit_wei)
        object.__setattr__(self, "credits", credit_wei_to_whole_credits_floor(credit_wei))
        object.__setattr__(self, "status", clean_status)
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["credit_wei"] = str(self.credit_wei)
        data["credits_display"] = credit_wei_to_decimal_text(self.credit_wei)
        return data


@dataclass(frozen=True)
class RequestCharge:
    charge_id: str
    account_id: str
    request_id: str
    hold_id: str
    charged_credits: int
    charged_credit_wei: int | str | None = None
    released_credits: int = 0
    released_credit_wei: int | str | None = None
    worker_earning_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        charged_wei = _credit_wei_or_legacy(self.charged_credit_wei, self.charged_credits)
        released_wei = _credit_wei_or_legacy(self.released_credit_wei, self.released_credits)
        object.__setattr__(self, "charge_id", self.charge_id or stable_id("chg", {"account_id": self.account_id, "request_id": self.request_id, "hold_id": self.hold_id}))
        object.__setattr__(self, "account_id", clean_account_id(self.account_id))
        object.__setattr__(self, "charged_credit_wei", charged_wei)
        object.__setattr__(self, "released_credit_wei", released_wei)
        object.__setattr__(self, "charged_credits", credit_wei_to_whole_credits_floor(charged_wei))
        object.__setattr__(self, "released_credits", credit_wei_to_whole_credits_floor(released_wei))
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["charged_credit_wei"] = str(self.charged_credit_wei)
        data["released_credit_wei"] = str(self.released_credit_wei)
        data["charged_credits_display"] = credit_wei_to_decimal_text(self.charged_credit_wei)
        data["released_credits_display"] = credit_wei_to_decimal_text(self.released_credit_wei)
        return data


@dataclass(frozen=True)
class WorkerEarning:
    earning_id: str
    worker_node_id: str
    request_id: str
    credits: int
    worker_commitment: str
    earned_credit_wei: int | str | None = None
    status: str = "earned"
    batch_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        clean_status = str(self.status or "earned").strip()
        if clean_status not in WORKER_EARNING_STATUSES:
            raise ValueError(f"Unsupported worker earning status: {clean_status}")
        clean_worker = clean_worker_id(self.worker_node_id)
        earned_wei = _credit_wei_or_legacy(self.earned_credit_wei, self.credits)
        object.__setattr__(self, "worker_node_id", clean_worker)
        object.__setattr__(self, "earned_credit_wei", earned_wei)
        object.__setattr__(self, "earning_id", self.earning_id or stable_id("earn", {"worker_node_id": clean_worker, "request_id": self.request_id, "earned_credit_wei": str(earned_wei)}))
        object.__setattr__(self, "credits", credit_wei_to_whole_credits_floor(earned_wei))
        object.__setattr__(self, "status", clean_status)
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    def as_private_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["earned_credit_wei"] = str(self.earned_credit_wei)
        data["earned_credits_display"] = credit_wei_to_decimal_text(self.earned_credit_wei)
        return data

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "earning_id": self.earning_id,
            "worker_commitment": self.worker_commitment,
            "earned_credit_wei": str(self.earned_credit_wei),
            "earned_credits_display": credit_wei_to_decimal_text(self.earned_credit_wei),
            "status": self.status,
            "batch_id": self.batch_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class WorkerClaim:
    claim_id: str
    worker_node_id: str
    claimed_credits: int
    claimed_credit_wei: int | str | None = None
    earning_ids: list[str] = field(default_factory=list)
    status: str = "claimed"
    idempotency_key: str = ""
    settlement_tx_hash: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        clean_status = str(self.status or "claimed").strip()
        if clean_status not in WORKER_CLAIM_STATUSES:
            raise ValueError(f"Unsupported worker claim status: {clean_status}")
        clean_worker = clean_worker_id(self.worker_node_id)
        clean_earning_ids = [str(item or "").strip() for item in (self.earning_ids or []) if str(item or "").strip()]
        claimed_wei = _credit_wei_or_legacy(self.claimed_credit_wei, self.claimed_credits)
        clean_key = str(self.idempotency_key or "").strip()
        object.__setattr__(self, "worker_node_id", clean_worker)
        object.__setattr__(self, "earning_ids", clean_earning_ids)
        object.__setattr__(self, "claimed_credit_wei", claimed_wei)
        object.__setattr__(self, "claimed_credits", credit_wei_to_whole_credits_floor(claimed_wei))
        object.__setattr__(self, "status", clean_status)
        object.__setattr__(self, "idempotency_key", clean_key)
        object.__setattr__(self, "settlement_tx_hash", str(self.settlement_tx_hash or "").strip())
        object.__setattr__(self, "claim_id", self.claim_id or stable_id("wclaim", {
            "worker_node_id": clean_worker,
            "earning_ids": clean_earning_ids,
            "claimed_credit_wei": str(claimed_wei),
            "idempotency_key": clean_key,
        }))
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["claimed_credit_wei"] = str(self.claimed_credit_wei)
        data["claimed_credits_display"] = credit_wei_to_decimal_text(self.claimed_credit_wei)
        return data


@dataclass(frozen=True)
class WorkerSettlementBatch:
    batch_id: str
    window_start: str
    window_end: str
    total_credits_exact: int
    total_credits_published: int
    dust_credits: int
    worker_count: int
    total_credit_wei_exact: int | str | None = None
    total_credit_wei_published: int | str | None = None
    dust_credit_wei: int | str | None = None
    batch_root: str = ""
    status: str = "draft"
    worker_node_id: str = ""
    claim_ids: list[str] = field(default_factory=list)
    precision_places: int = DEFAULT_WORKER_PAYOUT_PRECISION_PLACES
    rounding_bucket_credits: int = 0
    rounding_bucket_credit_wei: int | str | None = None
    bridge_account_id: str = "bridge-worker-payout-dust"
    payout_rail: str = ""
    operator_id: str = ""
    settlement_reference: str = ""
    settlement_tx_hash: str = ""
    settlement_proof_id: str = ""
    settlement_proof_hash: str = ""
    settled_at: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        clean_status = str(self.status or "draft").strip()
        if clean_status not in WORKER_BATCH_STATUSES:
            raise ValueError(f"Unsupported worker batch status: {clean_status}")
        precision = normalize_worker_payout_precision_places(self.precision_places)
        bucket_size_wei = positive_credit_wei(self.rounding_bucket_credit_wei, default=worker_payout_bucket_size_for_precision(precision))
        if bucket_size_wei <= 0:
            bucket_size_wei = worker_payout_bucket_size_for_precision(precision)
        exact_wei = _credit_wei_or_legacy(self.total_credit_wei_exact, self.total_credits_exact)
        published_wei = _credit_wei_or_legacy(self.total_credit_wei_published, self.total_credits_published)
        dust_wei = _credit_wei_or_legacy(self.dust_credit_wei, self.dust_credits)
        claim_ids = [str(item or "").strip() for item in (self.claim_ids or []) if str(item or "").strip()]
        object.__setattr__(
            self,
            "batch_id",
            self.batch_id or stable_id(
                "batch",
                {
                    "window_start": self.window_start,
                    "window_end": self.window_end,
                    "total_credit_wei_published": str(published_wei),
                    "claim_ids": claim_ids,
                },
            ),
        )
        object.__setattr__(self, "total_credit_wei_exact", exact_wei)
        object.__setattr__(self, "total_credit_wei_published", published_wei)
        object.__setattr__(self, "dust_credit_wei", dust_wei)
        object.__setattr__(self, "total_credits_exact", credit_wei_to_whole_credits_floor(exact_wei))
        object.__setattr__(self, "total_credits_published", credit_wei_to_whole_credits_floor(published_wei))
        object.__setattr__(self, "dust_credits", credit_wei_to_whole_credits_floor(dust_wei))
        object.__setattr__(self, "worker_count", positive_int(self.worker_count))
        object.__setattr__(self, "status", clean_status)
        object.__setattr__(self, "worker_node_id", clean_worker_id(self.worker_node_id, default="") if self.worker_node_id else "")
        object.__setattr__(self, "claim_ids", claim_ids)
        object.__setattr__(self, "precision_places", precision)
        object.__setattr__(self, "rounding_bucket_credit_wei", bucket_size_wei)
        object.__setattr__(self, "rounding_bucket_credits", credit_wei_to_whole_credits_floor(bucket_size_wei))
        object.__setattr__(self, "bridge_account_id", clean_account_id(self.bridge_account_id, default="bridge-worker-payout-dust"))
        object.__setattr__(self, "payout_rail", str(self.payout_rail or "").strip())
        object.__setattr__(self, "operator_id", clean_account_id(self.operator_id, default="") if self.operator_id else "")
        object.__setattr__(self, "settlement_reference", str(self.settlement_reference or "").strip())
        object.__setattr__(self, "settlement_tx_hash", str(self.settlement_tx_hash or "").strip())
        object.__setattr__(self, "settlement_proof_id", str(self.settlement_proof_id or "").strip())
        object.__setattr__(self, "settlement_proof_hash", str(self.settlement_proof_hash or "").strip())
        object.__setattr__(self, "settled_at", str(self.settled_at or "").strip())
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    @classmethod
    def from_exact_total(
        cls,
        *,
        window_start: str,
        window_end: str,
        exact_total: int,
        worker_count: int,
        bucket_size: int | None = None,
        batch_root: str = "",
        worker_node_id: str = "",
        claim_ids: list[str] | None = None,
        precision_places: int | None = None,
        bridge_account_id: str = "bridge-worker-payout-dust",
        status: str = "draft",
        metadata: dict[str, Any] | None = None,
    ) -> "WorkerSettlementBatch":
        precision = normalize_worker_payout_precision_places(precision_places)
        clean_bucket = positive_int(bucket_size, default=worker_payout_bucket_size_for_precision(precision))
        if clean_bucket <= 0:
            clean_bucket = worker_payout_bucket_size_for_precision(precision)
        published, dust = truncate_for_settlement(exact_total, bucket_size=clean_bucket)
        return cls(
            batch_id="",
            window_start=window_start,
            window_end=window_end,
            total_credits_exact=0,
            total_credits_published=0,
            dust_credits=0,
            worker_count=worker_count,
            total_credit_wei_exact=exact_total,
            total_credit_wei_published=published,
            dust_credit_wei=dust,
            batch_root=batch_root,
            status=status,
            worker_node_id=worker_node_id,
            claim_ids=list(claim_ids or []),
            precision_places=precision,
            rounding_bucket_credit_wei=clean_bucket,
            bridge_account_id=bridge_account_id,
            metadata=dict(metadata or {}),
        )

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["total_credit_wei_exact"] = str(self.total_credit_wei_exact)
        data["total_credit_wei_published"] = str(self.total_credit_wei_published)
        data["dust_credit_wei"] = str(self.dust_credit_wei)
        data["rounding_bucket_credit_wei"] = str(self.rounding_bucket_credit_wei)
        data["total_credits_exact_display"] = credit_wei_to_decimal_text(self.total_credit_wei_exact)
        data["total_credits_published_display"] = credit_wei_to_decimal_text(self.total_credit_wei_published)
        data["dust_credits_display"] = credit_wei_to_decimal_text(self.dust_credit_wei)
        return data

@dataclass(frozen=True)
class RequestReceipt:
    request_id: str
    account_id: str
    charged_credits: int
    worker_commitment: str
    report_token: str
    completed_at: str = ""
    model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", clean_account_id(self.account_id))
        object.__setattr__(self, "charged_credits", positive_int(self.charged_credits))
        object.__setattr__(self, "completed_at", self.completed_at or utc_now())

    def as_user_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "account_id": self.account_id,
            "charged_credits": self.charged_credits,
            "worker_commitment": self.worker_commitment,
            "report_token": self.report_token,
            "completed_at": self.completed_at,
            "model": self.model,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class WorkerQualityReport:
    report_id: str
    request_id: str
    account_id: str
    worker_commitment: str
    report_token_hash: str
    rating: int
    reason: str
    status: str = "submitted"
    created_at: str = ""
    reviewed_at: str = ""
    admin_notes: str = ""
    verdict: str = "needs_revision"
    feedback_tags: list[str] = field(default_factory=list)
    note: str = ""
    source: str = "requester"
    agent_run_id: str = ""
    agent_step_id: str = ""
    parent_request_id: str = ""
    version: int = 1
    updated_at: str = ""

    def __post_init__(self) -> None:
        clean_status = str(self.status or "submitted").strip()
        if clean_status not in QUALITY_REPORT_STATUSES:
            raise ValueError(f"Unsupported worker quality report status: {clean_status}")
        clean_account = clean_account_id(self.account_id)
        clean_verdict = str(self.verdict or "needs_revision").strip().lower()
        if clean_verdict not in {"accepted", "rejected", "needs_revision"}:
            clean_verdict = "needs_revision"
        clean_tags: list[str] = []
        for raw in self.feedback_tags or []:
            tag = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(raw or "").strip().lower())
            if tag and tag not in clean_tags:
                clean_tags.append(tag)
        created = self.created_at or utc_now()
        object.__setattr__(self, "report_id", self.report_id or stable_id("rptcase", {"request_id": self.request_id, "account_id": clean_account, "token": self.report_token_hash}))
        object.__setattr__(self, "account_id", clean_account)
        object.__setattr__(self, "rating", max(1, min(5, int(self.rating or 1))))
        object.__setattr__(self, "status", clean_status)
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", self.updated_at or created)
        object.__setattr__(self, "verdict", clean_verdict)
        object.__setattr__(self, "feedback_tags", clean_tags)
        object.__setattr__(self, "note", str(self.note or self.reason or "")[:1000])
        object.__setattr__(self, "source", str(self.source or "requester").strip().lower() or "requester")
        object.__setattr__(self, "version", max(1, int(self.version or 1)))

    def as_user_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "request_id": self.request_id,
            "account_id": self.account_id,
            "worker_commitment": self.worker_commitment,
            "rating": self.rating,
            "score": self.rating,
            "reason": self.reason,
            "verdict": self.verdict,
            "feedback_tags": list(self.feedback_tags),
            "note": self.note,
            "source": self.source,
            "agent_run_id": self.agent_run_id,
            "agent_step_id": self.agent_step_id,
            "parent_request_id": self.parent_request_id,
            "status": self.status,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "reviewed_at": self.reviewed_at,
            "worker_identity_private": True,
            "money_movement": False,
        }

    def as_admin_dict(self) -> dict[str, Any]:
        return asdict(self)
