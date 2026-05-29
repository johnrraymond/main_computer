from __future__ import annotations

import pytest

from scripts.run_worker_local_chain_settlement_execution_smoke import (
    DEFAULT_STATE_FILE,
    LEGACY_STATE_FILE,
    abi_uint,
    deployment_address,
    resolve_chain_id,
    resolve_contract_address,
    resolve_rpc_url,
    select_state,
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


def test_phase8_deployment_address_accepts_target_based_public_contract_record() -> None:
    reserve = "0x3333333333333333333333333333333333333333"
    state = {
        "contracts": {
            "reserve": {
                "target": "src/XLagBridgeReserve.sol:XLagBridgeReserve",
                "address": reserve,
            }
        }
    }

    assert deployment_address(state) == reserve


def test_phase8_select_state_falls_back_past_dry_run_current_without_reserve(tmp_path) -> None:
    root = tmp_path
    current = root / DEFAULT_STATE_FILE
    legacy = root / LEGACY_STATE_FILE
    current.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)

    current.write_text(
        """{
          "chain": {
            "chain_id": 31337,
            "host_rpc_url": "http://127.0.0.1:8545"
          },
          "deployments": {
            "xlag-bridge-reserve": {
              "target": "src/XLagBridgeReserve.sol:XLagBridgeReserve",
              "address": null
            }
          }
        }
        """,
        encoding="utf-8",
    )
    legacy.write_text(
        """{
          "chain": {
            "chain_id": 42424242,
            "host_rpc_url": "http://127.0.0.1:18545"
          },
          "deployments": {
            "xlag-bridge-reserve": {
              "target": "src/XLagBridgeReserve.sol:XLagBridgeReserve",
              "address": "0xe7f1725e7734ce288f8367e1bb143e90bb3f0512"
            }
          },
          "offices": [
            "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
            "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
            "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
            "0x90f79bf6eb2c4f870365e785982e1f101e93b906"
          ]
        }
        """,
        encoding="utf-8",
    )

    selected_path, state, env, candidates = select_state(root, DEFAULT_STATE_FILE)

    assert selected_path == legacy
    assert str(current) in candidates
    assert str(legacy) in candidates
    assert resolve_rpc_url(state, env, None) == "http://127.0.0.1:18545"
    assert resolve_chain_id(state, env, None) == 42424242
    assert resolve_contract_address(state, env, None) == "0xe7f1725e7734ce288f8367e1bb143e90bb3f0512"

