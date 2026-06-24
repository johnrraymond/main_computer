from __future__ import annotations

import pytest

from scripts.run_worker_local_chain_settlement_execution_smoke import (
    DEFAULT_STATE_FILE,
    LEGACY_STATE_FILE,
    ensure_local_sender_gas_balance,
    abi_uint,
    deployment_address,
    resolve_chain_id,
    resolve_contract_address,
    resolve_rpc_url,
    select_state,
    parse_payout_executed_event,
    rounded_down,
    send_transaction,
    wait_receipt,
)


def _indexed_address(address: str) -> str:
    clean = address.removeprefix("0x").lower()
    return "0x" + ("0" * 24) + clean


def test_phase8_sender_gas_preflight_refills_depleted_local_account(monkeypatch) -> None:
    import scripts.run_worker_local_chain_settlement_execution_smoke as smoke

    address = "0x1111111111111111111111111111111111111111"
    balances = [0, 10_000 * 10**18]
    rpc_calls: list[tuple[str, list[object]]] = []

    def fake_get_balance(url: str, account: str, *, timeout: float) -> int:
        del url, timeout
        assert account == address
        return balances.pop(0)

    def fake_rpc(url: str, method: str, params: list[object] | None = None, *, timeout: float = 10.0) -> object:
        del url, timeout
        rpc_calls.append((method, list(params or [])))
        return "0x1"

    monkeypatch.setattr(smoke, "get_balance", fake_get_balance)
    monkeypatch.setattr(smoke, "rpc", fake_rpc)

    result = ensure_local_sender_gas_balance(
        "http://127.0.0.1:18545",
        address,
        timeout=5.0,
        minimum_balance_wei=10**17,
        target_balance_wei=10_000 * 10**18,
    )

    assert result["ok"] is True
    assert result["refilled"] is True
    assert result["balance_before_wei"] == 0
    assert result["balance_after_wei"] == 10_000 * 10**18
    assert rpc_calls == [("anvil_setBalance", [address, hex(10_000 * 10**18)])]


def test_phase8_sender_gas_preflight_refills_account_below_target_even_when_above_minimum(monkeypatch) -> None:
    import scripts.run_worker_local_chain_settlement_execution_smoke as smoke

    address = "0x1212121212121212121212121212121212121212"
    balances = [10**18, 10_000 * 10**18]
    rpc_calls: list[tuple[str, list[object]]] = []

    def fake_get_balance(url: str, account: str, *, timeout: float) -> int:
        del url, timeout
        assert account == address
        return balances.pop(0)

    def fake_rpc(url: str, method: str, params: list[object] | None = None, *, timeout: float = 10.0) -> object:
        del url, timeout
        rpc_calls.append((method, list(params or [])))
        return "0x1"

    monkeypatch.setattr(smoke, "get_balance", fake_get_balance)
    monkeypatch.setattr(smoke, "rpc", fake_rpc)

    result = ensure_local_sender_gas_balance(
        "http://127.0.0.1:18545",
        address,
        timeout=5.0,
        minimum_balance_wei=10**17,
        target_balance_wei=10_000 * 10**18,
    )

    assert result["ok"] is True
    assert result["refilled"] is True
    assert result["balance_before_wei"] == 10**18
    assert result["balance_after_wei"] == 10_000 * 10**18
    assert rpc_calls == [("anvil_setBalance", [address, hex(10_000 * 10**18)])]


def test_phase8_sender_gas_preflight_reports_reset_hint_when_refill_fails(monkeypatch) -> None:
    import scripts.run_worker_local_chain_settlement_execution_smoke as smoke

    address = "0x2222222222222222222222222222222222222222"

    monkeypatch.setattr(smoke, "get_balance", lambda url, account, *, timeout: 0)

    def fake_rpc(url: str, method: str, params: list[object] | None = None, *, timeout: float = 10.0) -> object:
        del url, params, timeout
        raise RuntimeError(f"{method} unavailable")

    monkeypatch.setattr(smoke, "rpc", fake_rpc)

    with pytest.raises(RuntimeError, match="python tools/dev-chain-reset.py --yes"):
        ensure_local_sender_gas_balance(
            "http://127.0.0.1:18545",
            address,
            timeout=5.0,
            minimum_balance_wei=10**17,
            target_balance_wei=10_000 * 10**18,
        )


def test_phase8_send_transaction_wraps_insufficient_funds_with_sender_balance(monkeypatch) -> None:
    import scripts.run_worker_local_chain_settlement_execution_smoke as smoke

    address = "0x3333333333333333333333333333333333333333"
    seen_payloads: list[dict[str, object]] = []

    def fake_rpc(url: str, method: str, params: list[object] | None = None, *, timeout: float = 10.0) -> object:
        del url, timeout
        if method == "eth_sendTransaction":
            seen_payloads.append(dict(params[0]))  # type: ignore[index]
            raise RuntimeError("eth_sendTransaction RPC error: {'code': -32003, 'message': 'Insufficient funds for gas * price + value'}")
        raise AssertionError(method)

    monkeypatch.setattr(smoke, "rpc", fake_rpc)
    monkeypatch.setattr(smoke, "get_balance", lambda url, account, *, timeout: 123)

    with pytest.raises(RuntimeError, match="from=0x3333333333333333333333333333333333333333.*balance_wei=123"):
        send_transaction(
            "http://127.0.0.1:18545",
            {"from": address, "to": "0x4444444444444444444444444444444444444444", "value": "0x7b"},
            timeout=5.0,
        )

    assert seen_payloads[0]["from"] == address
    assert seen_payloads[0]["value"] == "0x7b"


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



def test_phase8_wait_receipt_injects_missing_transaction_hash(monkeypatch) -> None:
    import scripts.run_worker_local_chain_settlement_execution_smoke as smoke

    tx_hash = "0x" + "d" * 64
    calls = {"count": 0}

    def fake_rpc(url: str, method: str, params: list[object] | None = None, *, timeout: float = 10.0) -> object:
        del url, timeout
        assert method == "eth_getTransactionReceipt"
        assert params == [tx_hash]
        calls["count"] += 1
        return {
            "status": "0x1",
            "blockNumber": "0x2a",
            "logs": [],
        }

    monkeypatch.setattr(smoke, "rpc", fake_rpc)

    receipt = wait_receipt("http://127.0.0.1:18545", tx_hash, timeout=1.0, poll_s=0.01)

    assert receipt["transactionHash"] == tx_hash
    assert calls["count"] == 1
