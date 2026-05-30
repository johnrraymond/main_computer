#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from typing import Any

from main_computer.remote_overflow import RemoteOverflowDecisionEngine, run_mock_hub_overflow


def capacity_available(*, thread_id: str = "", max_local_concurrency: int = 1) -> dict[str, Any]:
    return {
        "ok": True,
        "available_now": True,
        "busy": False,
        "reason_code": "local_ai_available",
        "user_message": "This chat can use local AI now.",
        "cards": [
            {
                "key": "local_capacity",
                "title": "Local AI capacity",
                "status": "pass",
                "message": "This chat can use local AI now.",
                "details": {"thread_id": thread_id, "max_local_concurrency": max_local_concurrency},
            }
        ],
    }


def capacity_busy(*, thread_id: str = "", max_local_concurrency: int = 1) -> dict[str, Any]:
    return {
        "ok": True,
        "available_now": False,
        "busy": True,
        "reason_code": "thread_busy",
        "user_message": "This chat is currently using the local AI slot.",
        "cards": [
            {
                "key": "local_capacity",
                "title": "Local AI capacity",
                "status": "blocked",
                "message": "This chat is currently using the local AI slot.",
                "details": {"thread_id": thread_id, "max_local_concurrency": max_local_concurrency},
            }
        ],
    }


def flatten(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {flatten(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(flatten(item) for item in value)
    return str(value)


def assert_case(name: str, condition: bool, details: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{name} failed: {details!r}")


def main() -> int:
    base = {
        "thread_id": "thread-smoke",
        "messages": [{"role": "user", "content": "smoke test remote overflow"}],
        "mock_thinking_delay_ms": 0,
    }

    disabled = RemoteOverflowDecisionEngine(local_capacity_provider=capacity_busy).assess(
        {**base, "remote_overflow_enabled": False, "credit_ready": True, "willing_worker_count": 3}
    ).as_dict()
    assert_case("remote disabled action", disabled["action"] == "remote_blocked_by_policy", disabled)
    assert_case("remote disabled no auth", disabled["authorization_required"] is False, disabled)

    local = RemoteOverflowDecisionEngine(local_capacity_provider=capacity_available).assess(
        {**base, "remote_overflow_enabled": True, "credit_ready": True, "willing_worker_count": 3}
    ).as_dict()
    assert_case("local available run local", local["action"] == "run_local", local)

    credit_block = RemoteOverflowDecisionEngine(local_capacity_provider=capacity_busy).assess(
        {**base, "remote_overflow_enabled": True, "bridged_credits": 1, "spendable_credits": 1, "willing_worker_count": 3}
    ).as_dict()
    assert_case("credit block", credit_block["action"] == "remote_blocked_by_credit", credit_block)

    zero_workers = RemoteOverflowDecisionEngine(local_capacity_provider=capacity_busy).assess(
        {**base, "remote_overflow_enabled": True, "credit_ready": True, "willing_worker_count": 0}
    ).as_dict()
    assert_case("zero workers unavailable", zero_workers["reason_code"] == "no_willing_workers", zero_workers)

    ready = RemoteOverflowDecisionEngine(local_capacity_provider=capacity_busy).assess(
        {**base, "remote_overflow_enabled": True, "credit_ready": True, "willing_worker_count": 2}
    ).as_dict()
    assert_case("authorization ready", ready["authorization_required"] is True, ready)

    mock = run_mock_hub_overflow(
        {**base, "remote_overflow_enabled": True, "credit_ready": True, "willing_worker_count": 2},
        local_capacity_provider=capacity_busy,
    )
    assert_case("mock result ok", mock["ok"] is True, mock)
    assert_case("mock result simulated", mock["remote_overflow_result"]["simulated"] is True, mock)
    assert_case("mock no credit spent", mock["remote_overflow_result"]["response"]["metadata"]["no_credit_spent"] is True, mock)

    full_text = flatten({"disabled": disabled, "local": local, "credit": credit_block, "zero": zero_workers, "ready": ready, "mock": mock})
    assert_case("no lowest worker price exposure", "lowest_worker_price" not in full_text, full_text)
    assert_case("no private worker minimum exposure", "private_worker_minimum" not in full_text, full_text)

    print("remote_overflow_assessment_smoke: ok")
    print("cases: remote_disabled, local_available, credit_blocked, zero_workers, authorization_ready, mock_hub_ai")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
