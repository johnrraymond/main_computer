from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from main_computer.credit_units import (
    CreditUnitError,
    credit_decimal_text_to_wei,
    credit_wei_to_decimal_text,
    credit_wei_to_display_text,
    eth_wei_to_display_text,
)
from main_computer.hub_credit_indexer import HubCreditIndexer
from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.remote_overflow import assess_credit_readiness, estimate_remote_request


CREDIT = 10**18


def _deposit_payload(**overrides):
    payload = {
        "chain_id": 42424242,
        "contract_address": "0x1111111111111111111111111111111111111111",
        "tx_hash": "0x2222222222222222222222222222222222222222222222222222222222222222",
        "log_index": 0,
        "block_number": 123,
        "account_id": "0x3333333333333333333333333333333333333333",
        "payer_address": "0x3333333333333333333333333333333333333333",
        "payment_asset": "native",
        "payment_amount_base_units": CREDIT,
        "credits_granted_wei": str(750_000_000_000_000_000),
        "memo": "fractional funding receipt",
    }
    payload.update(overrides)
    return payload


def test_credit_and_eth_display_strings_hide_raw_wei() -> None:
    assert credit_decimal_text_to_wei("1.025", round_up=False) == 1_025_000_000_000_000_000
    assert credit_decimal_text_to_wei("0.000000000000000001", round_up=False) == 1
    with pytest.raises(CreditUnitError):
        credit_decimal_text_to_wei("0.0000000000000000001", round_up=False)

    assert credit_wei_to_decimal_text(750_000_000_000_000_000) == "0.75"
    assert credit_wei_to_display_text(1_025_000_000_000_000_000) == "1.025 credits"
    assert eth_wei_to_display_text(31_000_000_000_000_000) == "0.031 ETH"


def test_remote_readiness_uses_fractional_credit_wei_and_friendly_messages() -> None:
    estimate = estimate_remote_request(
        {
            "messages": [{"role": "user", "content": "x"}],
            "max_output_tokens": 1024,
            "credits_per_token": "0.001",
        }
    )
    assert estimate.estimated_max_credit_wei == 1_025_000_000_000_000_000

    ready = assess_credit_readiness(
        {
            "bridged_credit_wei": str(1_975_000_000_000_000_000),
            "spendable_credit_wei": str(1_975_000_000_000_000_000),
        },
        estimate,
    )
    assert ready.ok is True
    assert ready.reason_code == "credit_ready"
    assert "1.025 credits" in ready.message
    assert "1025000000000000000" not in ready.message

    blocked = assess_credit_readiness(
        {
            "bridged_credit_wei": str(1_024_999_999_999_999_999),
            "spendable_credit_wei": str(1_024_999_999_999_999_999),
        },
        estimate,
    )
    assert blocked.ok is False
    assert blocked.reason_code == "insufficient_bridged_credits"
    assert "1.024999999999999999 credits" in blocked.message
    assert "1025000000000000000" not in blocked.message


def test_fractional_funding_hold_charge_worker_claim_and_settlement_are_exact() -> None:
    account = "0x1111111111111111111111111111111111111111"
    with tempfile.TemporaryDirectory() as tmp:
        ledger = HubCreditLedger(Path(tmp))
        funded = ledger.record_completed_bridge_deposit(
            account_id=account,
            owner_address=account,
            chain_completed_credit_wei=750_000_000_000_000_000,
            deposit_id="fractional-deposit",
        )
        assert funded["account"]["available_credit_wei"] == "750000000000000000"
        assert funded["account"]["available_credits_display"] == "0.75"

        hold = ledger.create_hold_credit_wei(
            account_id=account,
            request_id="fractional-request",
            credit_wei=750_000_000_000_000_000,
        )
        charged = ledger.charge_hold_credit_wei(
            hold_id=hold["hold"]["hold_id"],
            charged_credit_wei=500_000_000_000_000_000,
            worker_node_id="fractional-worker",
        )
        assert charged["charge"]["charged_credit_wei"] == "500000000000000000"
        assert charged["charge"]["released_credit_wei"] == "250000000000000000"
        assert charged["worker_earning"]["earned_credit_wei"] == "500000000000000000"

        claim = ledger.record_worker_claim(worker_node_id="fractional-worker")
        assert claim["claimed_credit_wei"] == "500000000000000000"
        assert claim["claimed_credits_display"] == "0.5"

        batch = ledger.create_worker_settlement_batch(worker_node_id="fractional-worker", precision_places=18)
        assert batch["batch"]["total_credit_wei_exact"] == "500000000000000000"
        assert batch["batch"]["total_credit_wei_published"] == "500000000000000000"
        assert batch["batch"]["dust_credit_wei"] == "0"


def test_deposit_indexer_rejects_whole_credit_only_payload_after_redeploy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        indexer = HubCreditIndexer(HubCreditLedger(Path(tmp)))
        whole_only = _deposit_payload()
        whole_only.pop("credits_granted_wei")
        whole_only["credits_granted"] = 1
        with pytest.raises(ValueError, match="credits_granted_wei"):
            indexer.import_deposit(whole_only)

        imported = indexer.import_deposit(_deposit_payload())
        assert imported["deposit"]["credits_granted_wei"] == "750000000000000000"
        assert imported["deposit"]["credits_granted_display"] == "0.75"
        assert imported["account"]["available_credit_wei"] == "750000000000000000"
