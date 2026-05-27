from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any


CREDIT_UNIT_NAME = "Compute Credits"
CREDIT_UNIT_KEY = "compute_credit"
CREDIT_LEDGER_VERSION = "hub-credit-ledger-v0"

CREDIT_TRANSACTION_TYPES = {
    "deposit_indexed",
    "hold_created",
    "hold_released",
    "request_charged",
    "worker_earned",
    "bridge_spend_rectified",
    "withdrawal_released",
    "refund_issued",
    "batch_settled",
    "admin_adjustment",
}

CREDIT_HOLD_STATUSES = {"held", "released", "charged", "expired", "cancelled"}
WORKER_EARNING_STATUSES = {"earned", "batched", "claimed", "paid", "cancelled"}
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
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        now = utc_now()
        object.__setattr__(self, "account_id", clean_account_id(self.account_id))
        object.__setattr__(self, "owner_address", normalize_address(self.owner_address))
        object.__setattr__(self, "available_credits", positive_int(self.available_credits))
        object.__setattr__(self, "held_credits", positive_int(self.held_credits))
        object.__setattr__(self, "spent_credits", positive_int(self.spent_credits))
        object.__setattr__(self, "earned_credits", positive_int(self.earned_credits))
        object.__setattr__(self, "created_at", self.created_at or now)
        object.__setattr__(self, "updated_at", self.updated_at or self.created_at or now)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HubCreditTransaction:
    transaction_id: str
    account_id: str
    transaction_type: str
    credits: int
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
        object.__setattr__(self, "transaction_id", self.transaction_id or stable_id("ctx", asdict(self)))
        object.__setattr__(self, "account_id", clean_account_id(self.account_id))
        object.__setattr__(self, "transaction_type", tx_type)
        object.__setattr__(self, "credits", positive_int(self.credits))
        object.__setattr__(self, "worker_node_id", clean_worker_id(self.worker_node_id, default="") if self.worker_node_id else "")
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CreditDeposit:
    deposit_id: str
    account_id: str
    payer_address: str
    payment_asset: str
    payment_amount_base_units: int
    credits_granted: int
    chain_event: ChainEventRef
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
        object.__setattr__(self, "payment_amount_base_units", positive_int(self.payment_amount_base_units))
        object.__setattr__(self, "credits_granted", positive_int(self.credits_granted))
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["chain_event"] = self.chain_event.as_dict()
        return data


@dataclass(frozen=True)
class HubCreditHold:
    hold_id: str
    account_id: str
    request_id: str
    credits: int
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
        object.__setattr__(self, "request_id", str(self.request_id or "").strip())
        object.__setattr__(self, "credits", positive_int(self.credits))
        object.__setattr__(self, "status", clean_status)
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RequestCharge:
    charge_id: str
    account_id: str
    request_id: str
    hold_id: str
    charged_credits: int
    released_credits: int = 0
    worker_earning_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "charge_id", self.charge_id or stable_id("chg", {"account_id": self.account_id, "request_id": self.request_id, "hold_id": self.hold_id}))
        object.__setattr__(self, "account_id", clean_account_id(self.account_id))
        object.__setattr__(self, "charged_credits", positive_int(self.charged_credits))
        object.__setattr__(self, "released_credits", positive_int(self.released_credits))
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkerEarning:
    earning_id: str
    worker_node_id: str
    request_id: str
    credits: int
    worker_commitment: str
    status: str = "earned"
    batch_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        clean_status = str(self.status or "earned").strip()
        if clean_status not in WORKER_EARNING_STATUSES:
            raise ValueError(f"Unsupported worker earning status: {clean_status}")
        object.__setattr__(self, "worker_node_id", clean_worker_id(self.worker_node_id))
        object.__setattr__(self, "earning_id", self.earning_id or stable_id("earn", {"worker_node_id": self.worker_node_id, "request_id": self.request_id}))
        object.__setattr__(self, "credits", positive_int(self.credits))
        object.__setattr__(self, "status", clean_status)
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    def as_private_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "earning_id": self.earning_id,
            "worker_commitment": self.worker_commitment,
            "credits": self.credits,
            "status": self.status,
            "batch_id": self.batch_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class WorkerSettlementBatch:
    batch_id: str
    window_start: str
    window_end: str
    total_credits_exact: int
    total_credits_published: int
    dust_credits: int
    worker_count: int
    batch_root: str = ""
    status: str = "draft"
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        clean_status = str(self.status or "draft").strip()
        if clean_status not in WORKER_BATCH_STATUSES:
            raise ValueError(f"Unsupported worker batch status: {clean_status}")
        object.__setattr__(self, "batch_id", self.batch_id or stable_id("batch", {"window_start": self.window_start, "window_end": self.window_end, "total": self.total_credits_published}))
        object.__setattr__(self, "total_credits_exact", positive_int(self.total_credits_exact))
        object.__setattr__(self, "total_credits_published", positive_int(self.total_credits_published))
        object.__setattr__(self, "dust_credits", positive_int(self.dust_credits))
        object.__setattr__(self, "worker_count", positive_int(self.worker_count))
        object.__setattr__(self, "status", clean_status)
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    @classmethod
    def from_exact_total(
        cls,
        *,
        window_start: str,
        window_end: str,
        exact_total: int,
        bucket_size: int,
        worker_count: int,
        batch_root: str = "",
    ) -> "WorkerSettlementBatch":
        published, dust = truncate_for_settlement(exact_total, bucket_size=bucket_size)
        return cls(
            batch_id="",
            window_start=window_start,
            window_end=window_end,
            total_credits_exact=exact_total,
            total_credits_published=published,
            dust_credits=dust,
            worker_count=worker_count,
            batch_root=batch_root,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


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

    def __post_init__(self) -> None:
        clean_status = str(self.status or "submitted").strip()
        if clean_status not in QUALITY_REPORT_STATUSES:
            raise ValueError(f"Unsupported worker quality report status: {clean_status}")
        object.__setattr__(self, "report_id", self.report_id or stable_id("rptcase", {"request_id": self.request_id, "account_id": self.account_id, "token": self.report_token_hash}))
        object.__setattr__(self, "account_id", clean_account_id(self.account_id))
        object.__setattr__(self, "rating", max(1, min(5, int(self.rating or 1))))
        object.__setattr__(self, "status", clean_status)
        object.__setattr__(self, "created_at", self.created_at or utc_now())

    def as_user_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "request_id": self.request_id,
            "account_id": self.account_id,
            "worker_commitment": self.worker_commitment,
            "rating": self.rating,
            "reason": self.reason,
            "status": self.status,
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
        }

    def as_admin_dict(self) -> dict[str, Any]:
        return asdict(self)
