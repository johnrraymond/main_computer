from __future__ import annotations

import asyncio

from tools.temporal_lab.activities import FakeTokenActivities
from tools.temporal_lab.event_log import read_jsonl_events
from tools.temporal_lab.models import FakeTokenRequest
from tools.temporal_lab.workflows import (
    FakeTokenWorkflow,
    activity_heartbeat_timeout,
    activity_retry_policy,
    activity_start_to_close_timeout,
)


def test_workflow_contract_imports_without_live_temporal_dependency() -> None:
    request = FakeTokenRequest(
        request_id="req-contract",
        account_id="acct-contract",
        credits_offered=5,
        ring=2,
        token_count=2,
        token_interval_seconds=0.0,
    )

    assert FakeTokenWorkflow.__name__ == "FakeTokenWorkflow"
    assert activity_start_to_close_timeout(request).total_seconds() >= 30
    assert activity_heartbeat_timeout(request).total_seconds() >= 10


def test_workflow_activity_retry_policy_surfaces_failure_once() -> None:
    request = FakeTokenRequest(
        request_id="req-retry-policy",
        account_id="acct-contract",
        credits_offered=5,
        ring=2,
        token_count=2,
        token_interval_seconds=0.0,
        payload={"force_failure": True},
    )

    policy = activity_retry_policy(request)
    if policy is not None:
        assert policy.maximum_attempts == 1


def test_activity_direct_smoke_writes_start_token_progress_done(tmp_path) -> None:
    event_log = tmp_path / "events.jsonl"
    request = FakeTokenRequest(
        request_id="req-direct",
        account_id="acct-direct",
        credits_offered=2,
        ring=2,
        token_count=2,
        token_interval_seconds=0.0,
    )

    result = asyncio.run(
        FakeTokenActivities(event_log_path=event_log, worker_id="worker-direct").emit_fake_tokens(
            request.to_dict()
        )
    )

    events = read_jsonl_events(event_log)
    assert [event["event"] for event in events] == [
        "start",
        "token",
        "progress",
        "token",
        "progress",
        "done",
    ]
    assert all(event["ring"] == 2 for event in events)
    assert all(event["partition"] == "ring-2" for event in events)
    assert result["request_id"] == "req-direct"
    assert result["worker_id"] == "worker-direct"
    assert result["token_count"] == 2
    assert result["events_written"] == 6


def test_requester_rejects_when_no_catalog_ring_is_affordable_before_temporal_import() -> None:
    from tools.temporal_lab.models import RingOffer
    from tools.temporal_lab.requester import submit_request

    request = FakeTokenRequest(
        request_id="req-no-credit",
        account_id="acct-direct",
        credits_offered=1,
        token_count=5,
        token_interval_seconds=0.0,
    )

    payload = asyncio.run(
        submit_request(
            temporal_address="localhost:7233",
            namespace="scheduler-lab",
            request=request,
            catalog=(RingOffer(ring=2, service_rank=1, credits_per_token=1),),
        )
    )

    assert payload["decision"]["accepted"] is False
    assert payload["decision"]["reason"] == "no_affordable_ring"
    assert payload["result"] is None
