from __future__ import annotations

from tools.payout_lab.run_payout_lab import PayoutLabConfig, run_payout_lab


def test_mock_payout_lab_settles_concurrent_requests_without_overdraw() -> None:
    summary = run_payout_lab(
        PayoutLabConfig(
            backend="memory",
            wallets=4,
            starting_credits=50,
            requests=120,
            concurrency=16,
            settlement_workers=4,
            max_payout_credits=5,
            duplicate_rate=0.20,
            failure_rate=0.20,
            after_broadcast_crash_rate=0.20,
            settle_timeout_seconds=5.0,
            seed=20260619,
            run_id="pytest-payout-lab-basic",
        )
    )

    assert summary.ok, summary.as_dict()
    assert not summary.overdraw
    assert summary.lost_payout_count == 0
    assert summary.wallet_lock_count == 0
    assert summary.duplicate_chain_settlement_count == 0
    assert summary.duplicate_broadcast_attempt_count >= 0
    assert summary.settled_credit_wei == summary.accepted_credit_wei
    assert summary.available_credit_wei + summary.accepted_credit_wei == summary.seeded_credit_wei


def test_mock_payout_lab_handles_client_duplicate_requests_idempotently() -> None:
    summary = run_payout_lab(
        PayoutLabConfig(
            backend="memory",
            wallets=2,
            starting_credits=20,
            requests=80,
            concurrency=12,
            settlement_workers=3,
            max_payout_credits=4,
            duplicate_rate=0.50,
            failure_rate=0.10,
            after_broadcast_crash_rate=0.10,
            settle_timeout_seconds=5.0,
            seed=42,
            run_id="pytest-payout-lab-duplicates",
        )
    )

    assert summary.ok, summary.as_dict()
    assert summary.duplicate_response_count > 0
    assert summary.unique_accepted_count <= summary.request_count
    assert summary.mock_chain_tx_count == summary.unique_accepted_count
