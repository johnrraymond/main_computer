from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_dev_chain_flow():
    spec = importlib.util.spec_from_file_location("dev_chain_flow", ROOT / "dev-chain-flow.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_flow_defaults_to_app_facing_deployment_manifest() -> None:
    flow = load_dev_chain_flow()

    assert flow.DEFAULT_STATE_FILE == Path("runtime/deployments/current.json")


def test_resolve_state_file_prefers_current_deployment_manifest(tmp_path: Path) -> None:
    flow = load_dev_chain_flow()
    current = tmp_path / "runtime/deployments/current.json"
    legacy = tmp_path / "runtime/dev-chain/latest.json"
    current.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)
    current.write_text("{}", encoding="utf-8")
    legacy.write_text("{}", encoding="utf-8")

    assert flow.resolve_state_file(tmp_path, flow.DEFAULT_STATE_FILE) == current


def test_resolve_state_file_falls_back_to_legacy_dev_chain_state(tmp_path: Path) -> None:
    flow = load_dev_chain_flow()
    legacy = tmp_path / "runtime/dev-chain/latest.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}", encoding="utf-8")

    assert flow.resolve_state_file(tmp_path, flow.DEFAULT_STATE_FILE) == legacy


def test_production_shaped_deployment_helpers_are_supported() -> None:
    flow = load_dev_chain_flow()
    reserve = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"
    state = {
        "chain": {"rpc_url": "http://127.0.0.1:18546", "chain_id": 42424242},
        "contracts": {
            "xlag-bridge-reserve": {
                "target": "src/XLagBridgeReserve.sol:XLagBridgeReserve",
                "address": reserve,
            }
        },
        "offices": [{"address": address} for address in flow.DEFAULT_ANVIL_OFFICES],
    }

    assert flow.rpc_url_from_state(state, {}, None) == "http://127.0.0.1:18546"
    assert flow.deployment_address(state, "xlag-bridge-reserve") == reserve.lower()


def test_native_eng_unit_helpers_are_integer_base_units() -> None:
    flow = load_dev_chain_flow()

    assert flow.parse_eng("1") == 10**18
    assert flow.parse_eng("0.125") == 125_000_000_000_000_000
    assert flow.format_eng(10**18) == "1 ENG"
    assert flow.format_eng(125_000_000_000_000_000) == "0.125 ENG"

    try:
        flow.parse_eng("0.0000000000000000001")
    except ValueError as exc:
        assert "18 decimal" in str(exc)
    else:
        raise AssertionError("expected over-precise ENG amount to fail")


def test_propose_payout_calldata_uses_native_eng_base_units() -> None:
    flow = load_dev_chain_flow()
    recipient = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
    data = flow.propose_payout_data(
        recipient=recipient,
        amount_wei=flow.parse_eng("0.125"),
        memo="native ENG payout flow",
        expires_block=123,
    )

    assert data.startswith("0x707216b1")
    assert data[10:74].endswith(recipient.lower().removeprefix("0x"))
    assert flow.abi_uint(flow.parse_eng("0.125")) in data
    assert flow.abi_uint(123) in data
    assert "6e617469766520454e47207061796f757420666c6f77" in data


def test_run_flow_funds_proposes_seconds_mines_executes_and_verifies() -> None:
    flow = load_dev_chain_flow()
    offices = flow.DEFAULT_ANVIL_OFFICES
    reserve = "0xe7f1725e7734ce288f8367e1bb143e90bb3f0512"
    state = {
        "run_id": "unit-flow",
        "dry_run": False,
        "chain": {"host_rpc_url": "http://127.0.0.1:18545", "chain_id": 42424242},
        "offices": [{"address": address} for address in offices],
        "deployments": {
            "xlag-bridge-reserve": {
                "target": "src/XLagBridgeReserve.sol:XLagBridgeReserve",
                "address": reserve,
            }
        },
    }

    balances = {
        reserve: 0,
        offices[3]: flow.parse_eng("10000"),
    }
    block_number = {"value": 2}
    next_proposal_id = {"value": 1}
    proposal_state = {"value": 1}
    payout_amount = flow.parse_eng("0.125")
    tx_counter = {"value": 0}
    receipts: dict[str, dict] = {}
    sent_methods: list[str] = []

    def receipt() -> str:
        tx_counter["value"] += 1
        tx_hash = "0x" + f"{tx_counter['value']:064x}"
        receipts[tx_hash] = {"status": "0x1", "transactionHash": tx_hash, "blockNumber": hex(block_number["value"])}
        block_number["value"] += 1
        return tx_hash

    def fake_rpc(url, method, params=None, *, timeout=10.0):
        params = params or []

        if method == "eth_chainId":
            return hex(42424242)
        if method == "eth_blockNumber":
            return hex(block_number["value"])
        if method == "eth_getBalance":
            return hex(balances.get(flow.normalize_address(params[0]), 0))
        if method == "eth_getTransactionReceipt":
            return receipts.get(params[0])
        if method == "evm_mine":
            block_number["value"] += 1
            return "0x0"
        if method == "eth_call":
            call = params[0]
            data = call["data"].removeprefix("0x")
            if data.startswith(flow.SELECTORS["XLagBridgeReserve.nextProposalId()"]):
                return "0x" + flow.abi_uint(next_proposal_id["value"])
            if data.startswith(flow.SELECTORS["XLagBridgeReserve.payoutDelayBlocks()"]):
                return "0x" + flow.abi_uint(1)
            if data.startswith(flow.SELECTORS["XLagBridgeReserve.proposalState(uint256)"]):
                return "0x" + flow.abi_uint(proposal_state["value"])
            raise AssertionError(f"unexpected eth_call data {data}")
        if method == "eth_sendTransaction":
            tx = params[0]
            data = str(tx.get("data", "")).removeprefix("0x")
            to = flow.normalize_address(tx.get("to"))
            if to != reserve:
                raise AssertionError(f"unexpected tx target {to}")

            if tx.get("value"):
                balances[reserve] = balances.get(reserve, 0) + int(tx["value"], 16)
                sent_methods.append("fund")
                return receipt()

            if data.startswith(flow.SELECTORS["XLagBridgeReserve.proposePayout(address,uint256,string,uint64)"]):
                next_proposal_id["value"] += 1
                proposal_state["value"] = 1
                sent_methods.append("propose")
                return receipt()

            if data.startswith(flow.SELECTORS["XLagBridgeReserve.secondPayout(uint256)"]):
                sent_methods.append("second")
                return receipt()

            if data.startswith(flow.SELECTORS["XLagBridgeReserve.executePayout(uint256)"]):
                balances[reserve] -= payout_amount
                balances[offices[3]] += payout_amount
                proposal_state["value"] = flow.EXECUTED_STATE
                sent_methods.append("execute")
                return receipt()

            raise AssertionError(f"unexpected tx data {data}")

        raise AssertionError(f"unexpected method {method}")

    ok, summary, steps = flow.run_flow(
        state=state,
        env={},
        rpc_url=None,
        expected_chain_id=None,
        fund_wei=flow.parse_eng("1"),
        payout_wei=payout_amount,
        memo="native ENG payout flow",
        recipient=None,
        expires_blocks=100,
        mine_extra_blocks=1,
        timeout=1,
        poll_s=0,
        rpc_func=fake_rpc,
    )

    assert ok
    assert sent_methods == ["fund", "propose", "second", "execute"]
    assert summary["payout_wei"] == payout_amount
    assert summary["recipient_delta_wei"] == payout_amount
    assert summary["reserve_balance_after_wei"] == flow.parse_eng("0.875")
    assert {step.name for step in steps} >= {
        "fund-reserve",
        "propose-payout",
        "second-payout",
        "execute-payout",
        "proposal-state-executed",
        "recipient-native-eng-received",
    }


def test_flow_main_refuses_when_prod_lock_exists(tmp_path: Path, monkeypatch) -> None:
    flow = load_dev_chain_flow()
    monkeypatch.setattr(flow, "find_repo_root", lambda _start: tmp_path)
    (tmp_path / ".prod.lock").write_text('{"deployment":"prod","protected":true}\n', encoding="utf-8")

    code = flow.main([])

    assert code == 1
    assert not (tmp_path / "runtime" / "dev-chain" / "flow-latest.json").exists()
