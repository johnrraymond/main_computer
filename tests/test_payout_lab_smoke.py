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


def test_payout_lab_builds_requests_from_scheduler_created_source_accounts() -> None:
    from main_computer.credit_units import credit_count_to_wei
    from tools.payout_lab.run_payout_lab import (
        MemoryPayoutLedger,
        build_request_specs_from_source_accounts,
        wait_for_hub_source_accounts,
    )

    ledger = MemoryPayoutLedger()
    ledger.accounts["scheduler-requester-0001"] = {
        "account_id": "scheduler-requester-0001",
        "owner_address": "",
        "available_credit_wei": str(credit_count_to_wei(10)),
        "metadata": {"scheduler_lab": True},
    }
    ledger.accounts["not-scheduler"] = {
        "account_id": "not-scheduler",
        "owner_address": "",
        "available_credit_wei": str(credit_count_to_wei(100)),
        "metadata": {},
    }

    accounts = wait_for_hub_source_accounts(
        ledger=ledger,
        max_accounts=10,
        minimum_accounts=1,
        wait_seconds=0,
        poll_seconds=0.05,
    )
    assert [item.account_id for item in accounts] == ["scheduler-requester-0001"]

    specs = build_request_specs_from_source_accounts(
        accounts=accounts,
        request_count=8,
        max_payout_credits=3,
        duplicate_rate=0.0,
        seed=7,
    )
    assert len(specs) == 8
    assert {spec.account_id for spec in specs} == {"scheduler-requester-0001"}
    assert all(1 <= spec.credits <= 3 for spec in specs)



def test_payout_lab_hub_earned_source_builds_one_worker_payout_per_current_earning() -> None:
    from main_computer.credit_units import credit_count_to_wei
    from tools.payout_lab.run_payout_lab import (
        PayoutSourceAccount,
        build_request_specs_from_source_accounts,
    )

    accounts = [
        PayoutSourceAccount(
            account_id="",
            worker_node_id="worker-0001",
            wallet_address="0x1111111111111111111111111111111111111111",
            available_credit_wei=credit_count_to_wei(7),
            earning_ids=("earn-1", "earn-2"),
        ),
        PayoutSourceAccount(
            account_id="",
            worker_node_id="worker-0002",
            wallet_address="0x2222222222222222222222222222222222222222",
            available_credit_wei=credit_count_to_wei(3),
            earning_ids=("earn-3",),
        ),
    ]

    specs = build_request_specs_from_source_accounts(
        accounts=accounts,
        request_count=200,
        max_payout_credits=1,
        duplicate_rate=0.99,
        seed=123,
    )

    assert len(specs) == 2
    assert [spec.worker_node_id for spec in specs] == ["worker-0001", "worker-0002"]
    assert [spec.credits for spec in specs] == [7, 3]
    assert specs[0].earning_ids == ("earn-1", "earn-2")
    assert specs[1].earning_ids == ("earn-3",)
    assert all(spec.account_id == "" for spec in specs)
