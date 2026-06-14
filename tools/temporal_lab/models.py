from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


TASK_QUEUE_PREFIX = "scheduler-lab-fake-tokens"
SUPPORTED_RINGS = (0, 1, 2, 3)


class TemporalLabModelError(ValueError):
    """Raised when a Temporal lab request or decision is malformed."""


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TemporalLabModelError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_int(value: object, field_name: str, *, minimum: int) -> int:
    if isinstance(value, bool):
        raise TemporalLabModelError(f"{field_name} must be an integer")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise TemporalLabModelError(f"{field_name} must be an integer") from exc
    if parsed < minimum:
        raise TemporalLabModelError(f"{field_name} must be >= {minimum}")
    return parsed


def _require_float(value: object, field_name: str, *, minimum: float) -> float:
    if isinstance(value, bool):
        raise TemporalLabModelError(f"{field_name} must be a number")
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise TemporalLabModelError(f"{field_name} must be a number") from exc
    if not math.isfinite(parsed) or parsed < minimum:
        raise TemporalLabModelError(f"{field_name} must be a finite number >= {minimum:g}")
    return parsed


def normalize_ring(ring: int | str | None) -> int:
    if ring is None:
        raise TemporalLabModelError("ring is required")
    if isinstance(ring, bool):
        raise TemporalLabModelError("ring must be one of 0, 1, 2, 3")
    try:
        parsed = int(ring)
    except (TypeError, ValueError) as exc:
        raise TemporalLabModelError("ring must be one of 0, 1, 2, 3") from exc
    if parsed not in SUPPORTED_RINGS:
        allowed = ", ".join(str(item) for item in SUPPORTED_RINGS)
        raise TemporalLabModelError(f"Unknown ring {ring!r}; expected one of: {allowed}")
    return parsed


def task_queue_for_ring(ring: int | str | None) -> str:
    return f"{TASK_QUEUE_PREFIX}-ring-{normalize_ring(ring)}"


def partition_for_ring(ring: int | str | None) -> str:
    return f"ring-{normalize_ring(ring)}"


@dataclass(frozen=True)
class RingOffer:
    """One price-set entry for one worker-pool ring.

    Ring numbers are opaque worker-pool labels. Price and promotion behavior come
    from the catalog fields, not from numeric ordering of the ring IDs.
    """

    ring: int
    service_rank: int
    credits_per_token: int
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "ring", normalize_ring(self.ring))
        object.__setattr__(self, "service_rank", _require_int(self.service_rank, "service_rank", minimum=0))
        object.__setattr__(
            self,
            "credits_per_token",
            _require_int(self.credits_per_token, "credits_per_token", minimum=0),
        )
        if self.label:
            object.__setattr__(self, "label", _require_text(self.label, "label"))

    @property
    def partition(self) -> str:
        return f"ring-{self.ring}"

    @property
    def task_queue(self) -> str:
        return task_queue_for_ring(self.ring)

    def required_credits(self, token_count: int) -> int:
        return _require_int(token_count, "token_count", minimum=1) * self.credits_per_token

    def to_dict(self, *, token_count: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ring": self.ring,
            "partition": self.partition,
            "service_rank": self.service_rank,
            "credits_per_token": self.credits_per_token,
            "task_queue": self.task_queue,
        }
        if self.label:
            payload["label"] = self.label
        if token_count is not None:
            payload["required_credits"] = self.required_credits(token_count)
        return payload


# Stage 1.1 demo catalog. The important part is that ring IDs are not treated as
# price order. In this sample, ring 1 is the highest service rank, ring 0 is a
# mid/high pool, ring 2 is standard, and ring 3 is the zero-credit base pool.
DEFAULT_RING_CATALOG: tuple[RingOffer, ...] = (
    RingOffer(ring=3, service_rank=0, credits_per_token=0, label="base/free"),
    RingOffer(ring=2, service_rank=1, credits_per_token=1, label="standard"),
    RingOffer(ring=0, service_rank=2, credits_per_token=2, label="elevated"),
    RingOffer(ring=1, service_rank=3, credits_per_token=4, label="top"),
)


def parse_rings_csv(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return tuple(offer.ring for offer in DEFAULT_RING_CATALOG)
    rings = tuple(normalize_ring(item.strip()) for item in raw.split(",") if item.strip())
    if not rings:
        return tuple(offer.ring for offer in DEFAULT_RING_CATALOG)
    return rings


def ring_offer_for_ring(
    ring: int | str,
    *,
    catalog: Sequence[RingOffer] = DEFAULT_RING_CATALOG,
) -> RingOffer:
    normalized = normalize_ring(ring)
    for offer in catalog:
        if offer.ring == normalized:
            return offer
    raise TemporalLabModelError(f"ring {normalized} is not present in the active ring catalog")


@dataclass(frozen=True)
class FakeTokenRequest:
    request_id: str
    account_id: str
    credits_offered: int = 0
    token_count: int = 3
    token_interval_seconds: float = 1.0
    payload: dict[str, Any] = field(default_factory=dict)
    ring: int | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _require_text(self.request_id, "request_id"))
        object.__setattr__(self, "account_id", _require_text(self.account_id, "account_id"))
        object.__setattr__(self, "credits_offered", _require_int(self.credits_offered, "credits_offered", minimum=0))
        object.__setattr__(self, "token_count", _require_int(self.token_count, "token_count", minimum=1))
        object.__setattr__(
            self,
            "token_interval_seconds",
            _require_float(self.token_interval_seconds, "token_interval_seconds", minimum=0.0),
        )
        if not isinstance(self.payload, dict):
            raise TemporalLabModelError("payload must be a JSON object")
        object.__setattr__(self, "payload", dict(self.payload))
        if self.ring is not None:
            object.__setattr__(self, "ring", normalize_ring(self.ring))
        if self.idempotency_key is not None:
            object.__setattr__(self, "idempotency_key", _require_text(self.idempotency_key, "idempotency_key"))

    @property
    def partition(self) -> str:
        if self.ring is None:
            return "unresolved"
        return partition_for_ring(self.ring)

    @property
    def task_queue(self) -> str:
        if self.ring is None:
            raise TemporalLabModelError("request has no resolved ring/task queue yet")
        return task_queue_for_ring(self.ring)

    def with_ring(self, ring: int | str) -> "FakeTokenRequest":
        return FakeTokenRequest(
            request_id=self.request_id,
            account_id=self.account_id,
            credits_offered=self.credits_offered,
            token_count=self.token_count,
            token_interval_seconds=self.token_interval_seconds,
            payload=self.payload,
            ring=normalize_ring(ring),
            idempotency_key=self.idempotency_key,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "request_id": self.request_id,
            "account_id": self.account_id,
            "credits_offered": self.credits_offered,
            "token_count": self.token_count,
            "token_interval_seconds": self.token_interval_seconds,
            "payload": self.payload,
        }
        if self.ring is not None:
            payload["ring"] = self.ring
            payload["partition"] = self.partition
            payload["task_queue"] = self.task_queue
        if self.idempotency_key:
            payload["idempotency_key"] = self.idempotency_key
        return payload

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FakeTokenRequest":
        ring = raw.get("ring")
        if ring is None and "partition" in raw:
            # Backward-compatible loader for any Stage 1 JSON payloads shaped
            # as {"partition": "ring-2"}. Do not accept old semantic names here.
            partition = str(raw.get("partition") or "")
            if partition.startswith("ring-"):
                ring = partition.removeprefix("ring-")
        return cls(
            request_id=raw.get("request_id"),
            account_id=raw.get("account_id"),
            credits_offered=raw.get("credits_offered", 0),
            token_count=raw.get("token_count", 3),
            token_interval_seconds=raw.get("token_interval_seconds", 1.0),
            payload=dict(raw.get("payload") or {}),
            ring=ring,
            idempotency_key=raw.get("idempotency_key"),
        )


@dataclass(frozen=True)
class RingDecision:
    accepted: bool
    request_id: str
    account_id: str
    credits_offered: int
    token_count: int
    ring: int | None
    partition: str | None
    task_queue: str | None
    required_credits: int | None
    credits_per_token: int | None
    service_rank: int | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "request_id": self.request_id,
            "account_id": self.account_id,
            "credits_offered": self.credits_offered,
            "token_count": self.token_count,
            "ring": self.ring,
            "partition": self.partition,
            "task_queue": self.task_queue,
            "required_credits": self.required_credits,
            "credits_per_token": self.credits_per_token,
            "service_rank": self.service_rank,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class FakeTokenResult:
    request_id: str
    account_id: str
    credits_offered: int
    ring: int | None
    partition: str
    worker_id: str
    token_count: int
    events_written: int
    event_log_path: str
    result: dict[str, Any] = field(default_factory=lambda: {"ok": True})

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "account_id": self.account_id,
            "credits_offered": self.credits_offered,
            "ring": self.ring,
            "partition": self.partition,
            "worker_id": self.worker_id,
            "token_count": self.token_count,
            "events_written": self.events_written,
            "event_log_path": self.event_log_path,
            "result": dict(self.result),
        }


def affordable_ring_offers(
    request: FakeTokenRequest,
    *,
    catalog: Sequence[RingOffer] = DEFAULT_RING_CATALOG,
) -> tuple[RingOffer, ...]:
    return tuple(
        offer
        for offer in catalog
        if offer.required_credits(request.token_count) <= request.credits_offered
    )


def choose_ring_for_offer(
    request: FakeTokenRequest,
    *,
    catalog: Sequence[RingOffer] = DEFAULT_RING_CATALOG,
) -> RingOffer | None:
    affordable = affordable_ring_offers(request, catalog=catalog)
    if not affordable:
        return None

    # Promotion is by explicit service_rank, not by ring number and not by a
    # hidden assumption that lower/higher ring IDs are more expensive.
    return max(
        affordable,
        key=lambda offer: (
            offer.service_rank,
            -offer.required_credits(request.token_count),
            -offer.ring,
        ),
    )


def required_credits_for_ring(request: FakeTokenRequest, ring: int | str) -> int:
    return ring_offer_for_ring(ring).required_credits(request.token_count)


def decide_ring(request: FakeTokenRequest, *, catalog: Sequence[RingOffer] = DEFAULT_RING_CATALOG) -> RingDecision:
    offer = choose_ring_for_offer(request, catalog=catalog)
    if offer is None:
        return RingDecision(
            accepted=False,
            request_id=request.request_id,
            account_id=request.account_id,
            credits_offered=request.credits_offered,
            token_count=request.token_count,
            ring=None,
            partition=None,
            task_queue=None,
            required_credits=None,
            credits_per_token=None,
            service_rank=None,
            reason="no_affordable_ring",
        )

    return RingDecision(
        accepted=True,
        request_id=request.request_id,
        account_id=request.account_id,
        credits_offered=request.credits_offered,
        token_count=request.token_count,
        ring=offer.ring,
        partition=offer.partition,
        task_queue=offer.task_queue,
        required_credits=offer.required_credits(request.token_count),
        credits_per_token=offer.credits_per_token,
        service_rank=offer.service_rank,
        reason="accepted",
    )


# Backward-compatible aliases for callers/tests from Stage 1.
def parse_partitions_csv(raw: str | None) -> tuple[str, ...]:
    return tuple(partition_for_ring(ring) for ring in parse_rings_csv(raw))


def task_queue_for_partition(partition: str | None) -> str:
    if not partition:
        return task_queue_for_ring(3)
    candidate = str(partition).strip().lower()
    if candidate.startswith("ring-"):
        return task_queue_for_ring(candidate.removeprefix("ring-"))
    raise TemporalLabModelError("partition names are derived from rings and must look like ring-0..ring-3")


def decide_partition(request: FakeTokenRequest, *, available_credits: int | None = None) -> RingDecision:
    # Stage 1.1 no longer takes a requester-selected partition or separate
    # available_credits budget. The request's credits_offered is the business
    # input. The optional argument is ignored to keep old imports from crashing.
    return decide_ring(request)
