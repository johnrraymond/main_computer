from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, file_name: str):
    path = ROOT / "tools" / file_name
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_flow():
    return load_script("dev_chain_flow_for_bridge_tests", "dev-chain-flow.py")


def load_bridge():
    return load_script("dev_chain_ledger_bridge_tests", "dev-chain-ledger-bridge.py")


def write_state(tmp_path: Path, flow) -> tuple[Path, str, list[str]]:
    offices = flow.DEFAULT_ANVIL_OFFICES
    reserve = "0xe7f1725e7734ce288f8367e1bb143e90bb3f0512"
    state = {
        "run_id": "unit-ledger-bridge",
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
    state_file = tmp_path / "latest.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    state_file.with_suffix(".env").write_text("", encoding="utf-8")
    return state_file, reserve, offices


def fake_flow_rpc(flow, reserve: str, offices: list[str], payout_amount: int):
    balances = {
        reserve: 0,
        offices[3]: flow.parse_compute_credits("10000"),
    }
    block_number = {"value": 2}
    next_proposal_id = {"value": 1}
    proposal_state = {"value": 1}
    tx_counter = {"value": 0}
    receipts: dict[str, dict] = {}

    def receipt() -> str:
        tx_counter["value"] += 1
        tx_hash = "0x" + f"{tx_counter['value']:064x}"
        receipts[tx_hash] = {"status": "0x1", "transactionHash": tx_hash, "blockNumber": hex(block_number["value"])}
        block_number["value"] += 1
        return tx_hash

    def rpc(_url, method, params=None, *, timeout=10.0):
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
            data = params[0]["data"].removeprefix("0x")
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
            assert to == reserve

            if tx.get("value"):
                balances[reserve] += int(tx["value"], 16)
                return receipt()

            if data.startswith(flow.SELECTORS["XLagBridgeReserve.proposePayout(address,uint256,string,uint64)"]):
                next_proposal_id["value"] += 1
                proposal_state["value"] = 1
                return receipt()

            if data.startswith(flow.SELECTORS["XLagBridgeReserve.secondPayout(uint256)"]):
                return receipt()

            if data.startswith(flow.SELECTORS["XLagBridgeReserve.executePayout(uint256)"]):
                balances[reserve] -= payout_amount
                balances[offices[3]] += payout_amount
                proposal_state["value"] = flow.EXECUTED_STATE
                return receipt()

            raise AssertionError(f"unexpected tx data {data}")

        raise AssertionError(f"unexpected method {method}")

    return rpc


def test_bridge_claims_spends_and_records_compute_credit_audit(tmp_path) -> None:
    flow = load_flow()
    bridge = load_bridge()
    state_file, reserve, offices = write_state(tmp_path, flow)
    payout_wei = flow.parse_compute_credits("0.125")
    report = tmp_path / "ledger-bridge-report.json"

    ok, payload = bridge.run_bridge(
        repo=ROOT,
        ledger_root=tmp_path / "energy_credits",
        state_file=state_file,
        report_file=report,
        node_id="gpu-worker-01",
        node_role="gpu-worker",
        endpoint="local://gpu-worker-01",
        register_node=True,
        queue_wei=payout_wei,
        fund_wei=flow.parse_compute_credits("1"),
        recipient=None,
        memo="unit compute credit bridge",
        expected_chain_id=None,
        rpc_url=None,
        timeout_s=1,
        poll_s=0,
        rpc_func=fake_flow_rpc(flow, reserve, offices, payout_wei),
    )

    assert ok
    assert payload["claimed_credits"] == payout_wei
    assert payload["balance_before"] == 0
    assert payload["balance_after"] == 0
    assert payload["chain_summary"]["proposal_id"] == 1
    assert report.exists()

    ledger = json.loads((tmp_path / "energy_credits" / "ledger.json").read_text(encoding="utf-8"))
    kinds = [tx["kind"] for tx in ledger["transactions"]]
    assert kinds == [
        "hub_worker_payout_claim",
        "spend",
        "compute_credit_reserve_payout_executed",
    ]

    audit = ledger["transactions"][-1]
    assert audit["credits"] == 0
    assert audit["compute_credit_reserve"]["credits_reconciled"] == payout_wei
    assert audit["compute_credit_reserve"]["amount_base_units"] == payout_wei
    assert audit["compute_credit_reserve"]["recipient"] == offices[3]
    assert audit["compute_credit_reserve"]["contract_address"] == reserve
    assert audit["compute_credit_reserve"]["proposal_id"] == 1


def test_bridge_parser_rejects_queue_amount_above_fund_amount() -> None:
    flow = load_flow()
    assert flow.parse_compute_credits("1") < flow.parse_compute_credits("2")


def test_bridge_defaults_to_app_facing_deployment_manifest() -> None:
    bridge = load_bridge()

    assert bridge.DEFAULT_STATE_FILE == Path("runtime/deployments/dev/latest.json")


def test_bridge_resolve_state_file_prefers_current_deployment_manifest(tmp_path: Path) -> None:
    bridge = load_bridge()
    current = tmp_path / "runtime/deployments/dev/latest.json"
    legacy = tmp_path / "runtime/dev-chain/latest.json"
    current.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)
    current.write_text("{}", encoding="utf-8")
    legacy.write_text("{}", encoding="utf-8")

    assert bridge.resolve_state_file(tmp_path, bridge.DEFAULT_STATE_FILE) == current


def test_bridge_resolve_state_file_falls_back_to_legacy_dev_chain_state(tmp_path: Path) -> None:
    bridge = load_bridge()
    legacy = tmp_path / "runtime/dev-chain/latest.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}", encoding="utf-8")

    assert bridge.resolve_state_file(tmp_path, bridge.DEFAULT_STATE_FILE) == legacy


def test_bridge_main_refuses_when_prod_lock_exists(tmp_path: Path, monkeypatch) -> None:
    bridge = load_bridge()
    monkeypatch.setattr(bridge, "find_repo_root", lambda _start: tmp_path)
    (tmp_path / ".prod.lock").write_text('{"deployment":"prod","protected":true}\n', encoding="utf-8")

    code = bridge.main([])

    assert code == 1
    assert not (tmp_path / "energy_credits").exists()
    assert not (tmp_path / "runtime" / "dev-chain" / "ledger-bridge-latest.json").exists()
