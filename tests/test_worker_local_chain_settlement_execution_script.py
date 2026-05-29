from __future__ import annotations

import pytest

from scripts.run_worker_local_chain_settlement_execution_smoke import (
    abi_uint,
    parse_payout_executed_event,
    rounded_down,
)


def _indexed_address(address: str) -> str:
    clean = address.removeprefix("0x").lower()
    return "0x" + ("0" * 24) + clean


def test_phase8_rounding_uses_published_amount_and_dust() -> None:
    published, dust, bucket = rounded_down(5_500_123, 3)

    assert published == 5_500_000
    assert dust == 123
    assert bucket == 1_000


def test_phase8_payout_event_parser_extracts_real_receipt_metadata() -> None:
    contract = "0x1111111111111111111111111111111111111111"
    recipient = "0x2222222222222222222222222222222222222222"
    tx_hash = "0x" + "a" * 64
    receipt = {
        "transactionHash": tx_hash,
        "blockNumber": "0x2a",
        "logs": [
            {
                "address": contract,
                "logIndex": "0x0",
                "topics": [
                    "0x" + "0" * 64,
                    "0x" + abi_uint(7),
                    _indexed_address(recipient),
                ],
                "data": "0x" + abi_uint(5_500_000),
            }
        ],
    }

    parsed = parse_payout_executed_event(
        receipt,
        contract_address=contract,
        proposal_id=7,
        recipient_address=recipient,
        amount_units=5_500_000,
    )

    assert parsed["event"] == "PayoutExecuted"
    assert parsed["proposal_id"] == "7"
    assert parsed["recipient_address"] == recipient
    assert parsed["amount_units"] == 5_500_000
    assert parsed["block_number"] == 42
    assert parsed["tx_hash"] == tx_hash


def test_phase8_payout_event_parser_rejects_exact_high_precision_amount() -> None:
    contract = "0x1111111111111111111111111111111111111111"
    recipient = "0x2222222222222222222222222222222222222222"
    receipt = {
        "transactionHash": "0x" + "b" * 64,
        "blockNumber": "0x2a",
        "logs": [
            {
                "address": contract,
                "logIndex": "0x0",
                "topics": [
                    "0x" + "0" * 64,
                    "0x" + abi_uint(7),
                    _indexed_address(recipient),
                ],
                "data": "0x" + abi_uint(5_500_123),
            }
        ],
    }

    with pytest.raises(RuntimeError, match="matching PayoutExecuted"):
        parse_payout_executed_event(
            receipt,
            contract_address=contract,
            proposal_id=7,
            recipient_address=recipient,
            amount_units=5_500_000,
        )
