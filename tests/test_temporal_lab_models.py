from __future__ import annotations

import pytest

from tools.temporal_lab.models import (
    DEFAULT_RING_CATALOG,
    FakeTokenRequest,
    RingOffer,
    TemporalLabModelError,
    choose_ring_for_offer,
    decide_ring,
    parse_rings_csv,
    required_credits_for_ring,
    task_queue_for_ring,
)


def test_ring_task_queues_use_opaque_worker_pool_ids() -> None:
    assert task_queue_for_ring(0) == "scheduler-lab-fake-tokens-ring-0"
    assert task_queue_for_ring(1) == "scheduler-lab-fake-tokens-ring-1"
    assert task_queue_for_ring(2) == "scheduler-lab-fake-tokens-ring-2"
    assert task_queue_for_ring(3) == "scheduler-lab-fake-tokens-ring-3"


def test_requester_supplies_credits_offered_not_ring_or_required_credits() -> None:
    request = FakeTokenRequest(
        request_id="req-001",
        account_id="acct-001",
        credits_offered=5,
        token_count=5,
        token_interval_seconds=0.0,
        payload={"prompt": "hello"},
        idempotency_key="idem-001",
    )

    assert request.ring is None
    assert request.partition == "unresolved"
    assert request.to_dict()["credits_offered"] == 5
    assert "required_credits" not in request.to_dict()
    assert FakeTokenRequest.from_mapping(request.to_dict()) == request


def test_offer_catalog_promotes_by_service_rank_not_ring_number() -> None:
    request = FakeTokenRequest(
        request_id="req-002",
        account_id="acct-001",
        credits_offered=10,
        token_count=5,
    )

    decision = decide_ring(request)

    assert decision.accepted is True
    assert decision.ring == 0
    assert decision.service_rank == 2
    assert decision.required_credits == 10
    assert decision.task_queue == "scheduler-lab-fake-tokens-ring-0"


def test_default_catalog_does_not_encode_ring_zero_as_highest_price() -> None:
    per_token_by_ring = {offer.ring: offer.credits_per_token for offer in DEFAULT_RING_CATALOG}

    assert per_token_by_ring[0] < per_token_by_ring[1]
    assert required_credits_for_ring(FakeTokenRequest(request_id="req", account_id="acct", token_count=5), 0) == 10
    assert required_credits_for_ring(FakeTokenRequest(request_id="req", account_id="acct", token_count=5), 1) == 20


def test_custom_catalog_can_price_rings_in_any_order() -> None:
    request = FakeTokenRequest(
        request_id="req-003",
        account_id="acct-001",
        credits_offered=6,
        token_count=3,
    )
    catalog = (
        RingOffer(ring=0, service_rank=0, credits_per_token=0),
        RingOffer(ring=1, service_rank=3, credits_per_token=2),
        RingOffer(ring=2, service_rank=2, credits_per_token=1),
        RingOffer(ring=3, service_rank=1, credits_per_token=4),
    )

    chosen = choose_ring_for_offer(request, catalog=catalog)

    assert chosen is not None
    assert chosen.ring == 1
    assert chosen.service_rank == 3


def test_zero_credit_offer_routes_to_base_ring_when_catalog_allows_it() -> None:
    request = FakeTokenRequest(
        request_id="req-004",
        account_id="acct-001",
        credits_offered=0,
        token_count=3,
    )

    decision = decide_ring(request)

    assert decision.accepted is True
    assert decision.ring == 3
    assert decision.required_credits == 0
    assert decision.reason == "accepted"


def test_ring_csv_normalization() -> None:
    assert parse_rings_csv("3, 2,0") == (3, 2, 0)


def test_invalid_request_is_rejected_early() -> None:
    with pytest.raises(TemporalLabModelError):
        FakeTokenRequest(request_id="", account_id="acct")

    with pytest.raises(TemporalLabModelError):
        FakeTokenRequest(request_id="req", account_id="acct", ring=9)

    with pytest.raises(TemporalLabModelError):
        FakeTokenRequest(request_id="req", account_id="acct", token_count=0)

    with pytest.raises(TemporalLabModelError):
        FakeTokenRequest(request_id="req", account_id="acct", credits_offered=-1)
