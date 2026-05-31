from __future__ import annotations

from typing import Any

from main_computer.remote_overflow import (
    RemoteOverflowDecisionEngine,
    run_mock_hub_overflow,
)


def _capacity_available(*, thread_id: str = "", max_local_concurrency: int = 1) -> dict[str, Any]:
    return {
        "ok": True,
        "available_now": True,
        "busy": False,
        "reason_code": "local_ai_available",
        "user_message": "This chat can use local AI now.",
        "thread_id": thread_id,
        "active_run_count": 0,
        "max_local_concurrency": max_local_concurrency,
        "cards": [
            {
                "key": "local_capacity",
                "title": "Local AI capacity",
                "status": "pass",
                "message": "This chat can use local AI now.",
                "details": {"reason_code": "local_ai_available"},
            }
        ],
    }


def _capacity_busy(*, thread_id: str = "", max_local_concurrency: int = 1) -> dict[str, Any]:
    return {
        "ok": True,
        "available_now": False,
        "busy": True,
        "reason_code": "thread_busy",
        "user_message": "This chat is currently using the local AI slot.",
        "thread_id": thread_id,
        "active_run_count": 1,
        "max_local_concurrency": max_local_concurrency,
        "cards": [
            {
                "key": "local_capacity",
                "title": "Local AI capacity",
                "status": "blocked",
                "message": "This chat is currently using the local AI slot.",
                "details": {"reason_code": "thread_busy", "checked_thread_id": thread_id},
            }
        ],
    }


def _keys(assessment: dict[str, Any]) -> list[str]:
    return [str(card.get("key")) for card in assessment["cards"]]


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(_flatten(item) for item in value)
    return str(value)


def test_remote_disabled_short_circuits_before_credit_or_hub() -> None:
    assessment = RemoteOverflowDecisionEngine(local_capacity_provider=_capacity_busy).assess(
        {
            "thread_id": "thread-a",
            "remote_overflow_enabled": False,
            "messages": [{"role": "user", "content": "hello"}],
            "credit_ready": True,
            "willing_worker_count": 3,
        }
    ).as_dict()

    assert assessment["action"] == "remote_blocked_by_policy"
    assert assessment["reason_code"] == "remote_overflow_disabled"
    assert assessment["authorization_required"] is False
    assert assessment["offer_remote"] is False
    assert "local_capacity" in _keys(assessment)
    assert "credit_readiness" in _keys(assessment)
    assert "hub_availability" in _keys(assessment)
    assert next(card for card in assessment["cards"] if card["key"] == "local_capacity")["status"] == "skipped"
    assert next(card for card in assessment["cards"] if card["key"] == "credit_readiness")["status"] == "skipped"
    assert next(card for card in assessment["cards"] if card["key"] == "hub_availability")["status"] == "skipped"


def test_local_available_means_remote_not_needed_and_hub_skipped() -> None:
    assessment = RemoteOverflowDecisionEngine(local_capacity_provider=_capacity_available).assess(
        {
            "thread_id": "thread-a",
            "remote_overflow_enabled": True,
            "messages": [{"role": "user", "content": "hello"}],
            "credit_ready": True,
            "willing_worker_count": 3,
        }
    ).as_dict()

    assert assessment["status"] == "not_needed"
    assert assessment["action"] == "run_local"
    assert assessment["reason_code"] == "local_ai_available"
    assert assessment["authorization_required"] is False
    assert next(card for card in assessment["cards"] if card["key"] == "hub_availability")["status"] == "skipped"
    assert next(card for card in assessment["cards"] if card["key"] == "credit_readiness")["status"] == "skipped"


def test_local_busy_insufficient_credit_blocks_before_hub() -> None:
    assessment = RemoteOverflowDecisionEngine(local_capacity_provider=_capacity_busy).assess(
        {
            "thread_id": "thread-a",
            "remote_overflow_enabled": True,
            "messages": [{"role": "user", "content": "please do work"}],
            "max_output_tokens": 1000,
            "credits_per_token": 10,
            "bridged_credits": 10,
            "spendable_credits": 10,
            "willing_worker_count": 4,
        }
    ).as_dict()

    assert assessment["action"] == "remote_blocked_by_credit"
    assert assessment["reason_code"] == "insufficient_bridged_credits"
    assert assessment["authorization_required"] is False
    assert next(card for card in assessment["cards"] if card["key"] == "credit_readiness")["status"] == "blocked"
    assert next(card for card in assessment["cards"] if card["key"] == "hub_availability")["status"] == "skipped"


def test_local_busy_credit_ready_zero_workers_does_not_authorize() -> None:
    assessment = RemoteOverflowDecisionEngine(local_capacity_provider=_capacity_busy).assess(
        {
            "thread_id": "thread-a",
            "remote_overflow_enabled": True,
            "messages": [{"role": "user", "content": "please do work"}],
            "credit_ready": True,
            "willing_worker_count": 0,
        }
    ).as_dict()

    assert assessment["action"] == "remote_unavailable"
    assert assessment["reason_code"] == "no_willing_workers"
    assert assessment["authorization_required"] is False
    assert next(card for card in assessment["cards"] if card["key"] == "hub_availability")["status"] == "blocked"
    assert "lowest_worker_price" not in _flatten(assessment)
    assert "private_worker_minimum" not in _flatten(assessment)


def test_local_busy_credit_ready_willing_workers_requires_authorization() -> None:
    assessment = RemoteOverflowDecisionEngine(local_capacity_provider=_capacity_busy).assess(
        {
            "thread_id": "thread-a",
            "run_id": "run-a",
            "remote_overflow_enabled": True,
            "messages": [{"role": "user", "content": "please do work"}],
            "credit_ready": True,
            "willing_worker_count": 3,
            "model": "gemma4:26b",
            "capability": "chat.completions",
        }
    ).as_dict()

    assert assessment["status"] == "authorization_required"
    assert assessment["action"] == "authorization_required"
    assert assessment["reason_code"] == "remote_authorization_required"
    assert assessment["authorization_required"] is True
    assert assessment["offer_remote"] is True
    payload = assessment["authorization_payload"]
    assert payload["simulated"] is True
    assert payload["willing_worker_count"] == 3
    assert payload["private_worker_prices_exposed"] is False
    assert "lowest_worker_price" not in _flatten(assessment)
    assert "private_worker_minimum" not in _flatten(assessment)


def test_conduct_and_three_computer_calibration_are_visible_cards() -> None:
    assessment = RemoteOverflowDecisionEngine(local_capacity_provider=_capacity_busy).assess(
        {
            "thread_id": "thread-a",
            "remote_overflow_enabled": True,
            "messages": [{"role": "user", "content": "please do work"}],
            "credit_ready": True,
            "willing_worker_count": 1,
            "trust_calibration": {"command_count": 42},
        }
    ).as_dict()

    conduct = next(card for card in assessment["cards"] if card["key"] == "overflow_conduct")
    calibration = next(card for card in assessment["cards"] if card["key"] == "trust_calibration")
    assert conduct["details"]["no_credit_minted"] is True
    assert conduct["details"]["no_credit_hold_created"] is True
    assert conduct["details"]["private_worker_prices_exposed"] is False
    assert calibration["details"]["computer_1"] == "local_captain"
    assert calibration["details"]["computer_3"] == "hub_coordinator"
    assert calibration["details"]["fourth_source_can_authorize_spend"] is False
    assert calibration["details"]["command_count"] == 42


def test_mock_hub_ai_returns_fast_simulated_result_only_when_authorized() -> None:
    result = run_mock_hub_overflow(
        {
            "thread_id": "thread-a",
            "remote_overflow_enabled": True,
            "messages": [{"role": "user", "content": "answer through overflow"}],
            "credit_ready": True,
            "willing_worker_count": 2,
            "mock_thinking_delay_ms": 0,
        },
        local_capacity_provider=_capacity_busy,
    )

    assert result["ok"] is True
    assert result["status"] == "completed"
    remote_result = result["remote_overflow_result"]
    assert remote_result["source"] == "mock_hub_ai"
    assert remote_result["simulated"] is True
    assert remote_result["response"]["provider"] == "remote-hub-ai"
    assert "Remote Hub AI response received" in remote_result["response"]["content"]
    assert remote_result["response"]["metadata"]["no_real_remote_worker_contacted"] is True
    assert remote_result["response"]["metadata"]["no_credit_hold_created"] is True
    assert remote_result["response"]["metadata"]["no_credit_spent"] is True
    assert remote_result["cards"][0]["status"] == "simulated"


def test_mock_hub_ai_refuses_when_assessment_is_not_authorization_ready() -> None:
    result = run_mock_hub_overflow(
        {
            "thread_id": "thread-a",
            "remote_overflow_enabled": True,
            "messages": [{"role": "user", "content": "answer through overflow"}],
            "credit_ready": True,
            "willing_worker_count": 2,
            "mock_thinking_delay_ms": 0,
        },
        local_capacity_provider=_capacity_available,
    )

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["remote_overflow"]["reason_code"] == "local_ai_available"
