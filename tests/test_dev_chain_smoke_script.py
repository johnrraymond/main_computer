from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_dev_chain_smoke():
    spec = importlib.util.spec_from_file_location("dev_chain_diagnosis", ROOT / "tools" / "dev-chain-diagnosis.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_extracts_deploy_state_with_deterministic_office_fallback() -> None:
    smoke = load_dev_chain_smoke()
    state = {
        "chain": {"host_rpc_url": "http://127.0.0.1:18545", "chain_id": 42424242},
        "deployments": {
            "alpha-beta-lockout": {
                "target": "AlphaBetaLockout.sol:AlphaBetaLockout",
                "address": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
            },
            "xlag-bridge-reserve": {
                "target": "src/XLagBridgeReserve.sol:XLagBridgeReserve",
                "deployedTo": "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512",
            },
        },
    }

    env = {}

    assert smoke.rpc_url_from_state(state, env, None) == "http://127.0.0.1:18545"
    assert smoke.chain_id_from_state(state, env, None) == 42424242
    assert smoke.extract_offices(state) == smoke.DEFAULT_ANVIL_OFFICES
    assert (
        smoke.extract_contract_address(
            state,
            env,
            contract="XLagBridgeReserve",
            keys=("xlag-bridge-reserve", "XLagBridgeReserve", "xlag"),
            env_keys=("XLAG_BRIDGE_RESERVE_ADDRESS",),
        )
        == "0xe7f1725e7734ce288f8367e1bb143e90bb3f0512"
    )


def test_abi_encoding_and_decoding_helpers() -> None:
    smoke = load_dev_chain_smoke()

    assert smoke.abi_uint(4).endswith("04")
    assert smoke.abi_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266").endswith(
        "f39fd6e51aad88f6f4ce6ab8827279cfffb92266"
    )
    assert smoke.decode_uint("0x" + smoke.abi_uint(42424242)) == 42424242
    assert (
        smoke.decode_address("0x" + smoke.abi_address("0x70997970C51812dc3A010C7d01b50E0d17dc79C8"))
        == "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"
    )


def test_verify_state_reads_chain_and_contract_configuration(monkeypatch) -> None:
    smoke = load_dev_chain_smoke()
    offices = smoke.DEFAULT_ANVIL_OFFICES
    alpha = "0x5fbdb2315678afecb367f032d93f642f64180aa3"
    xlag = "0xe7f1725e7734ce288f8367e1bb143e90bb3f0512"
    hub_credit_bridge_escrow = "0xcf7ed3acca5a467e9e704c703e8d87f634fb0fc9"

    state = {
        "dry_run": False,
        "chain": {
            "host_rpc_url": "http://127.0.0.1:18545",
            "chain_id": 42424242,
            "accounts": 4,
            "offices": [{"address": address} for address in offices],
        },
        "deployments": {
            "alpha-beta-lockout": {"target": "AlphaBetaLockout.sol:AlphaBetaLockout", "address": alpha},
            "xlag-bridge-reserve": {"target": "src/XLagBridgeReserve.sol:XLagBridgeReserve", "address": xlag},
            "hub_credit_bridge_escrow": {
                "target": "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow",
                "address": hub_credit_bridge_escrow,
            },
        },
    }

    def word(value: int) -> str:
        return "0x" + smoke.abi_uint(value)

    def address_word(address: str) -> str:
        return "0x" + smoke.abi_address(address)

    def decode_last_uint(data: str) -> int:
        return int(data[-64:], 16)

    def decode_last_address(data: str) -> str:
        return "0x" + data[-40:].lower()

    def fake_http_post_json(url, payload, timeout):
        method = payload["method"]
        params = payload.get("params") or []

        if method == "eth_chainId":
            return {"jsonrpc": "2.0", "id": 1, "result": hex(42424242)}, 200, None
        if method == "eth_blockNumber":
            return {"jsonrpc": "2.0", "id": 1, "result": "0x9"}, 200, None
        if method == "eth_getCode":
            return {"jsonrpc": "2.0", "id": 1, "result": "0x60006000"}, 200, None
        if method == "eth_getBalance":
            return {"jsonrpc": "2.0", "id": 1, "result": "0x0"}, 200, None
        if method == "eth_call":
            call = params[0]
            to = call["to"].lower()
            data = call["data"].removeprefix("0x")

            if to == alpha:
                if data.startswith(smoke.SELECTORS["AlphaBetaLockout.COUNCIL_SIZE()"]):
                    result = word(4)
                elif data.startswith(smoke.SELECTORS["AlphaBetaLockout.councilMember(uint256)"]):
                    result = address_word(offices[decode_last_uint(data)])
                elif data.startswith(smoke.SELECTORS["AlphaBetaLockout.isCouncilMember(address)"]):
                    result = word(1 if decode_last_address(data) in offices else 0)
                else:
                    raise AssertionError(f"unexpected alpha call {data}")

            elif to == xlag:
                if data.startswith(smoke.SELECTORS["XLagBridgeReserve.OFFICE_COUNT()"]):
                    result = word(4)
                elif data.startswith(smoke.SELECTORS["XLagBridgeReserve.getOffice(uint8)"]):
                    result = address_word(offices[decode_last_uint(data)])
                elif data.startswith(smoke.SELECTORS["XLagBridgeReserve.isOffice(address)"]):
                    result = word(1 if decode_last_address(data) in offices else 0)
                elif data.startswith(smoke.SELECTORS["XLagBridgeReserve.officeIndexPlusOne(address)"]):
                    address = decode_last_address(data)
                    result = word(offices.index(address) + 1 if address in offices else 0)
                elif data.startswith(smoke.SELECTORS["XLagBridgeReserve.maxPayoutWei()"]):
                    result = word(10**18)
                elif data.startswith(smoke.SELECTORS["XLagBridgeReserve.payoutDelayBlocks()"]):
                    result = word(1)
                elif data.startswith(smoke.SELECTORS["XLagBridgeReserve.resetDelayBlocks()"]):
                    result = word(1)
                elif data.startswith(smoke.SELECTORS["XLagBridgeReserve.nextProposalId()"]):
                    result = word(5)
                elif data.startswith(smoke.SELECTORS["XLagBridgeReserve.walletSmokeNonce()"]):
                    result = word(0)
                elif data.startswith(smoke.SELECTORS["XLagBridgeReserve.frobNonce()"]):
                    result = word(0)
                else:
                    raise AssertionError(f"unexpected xlag call {data}")
            else:
                raise AssertionError(f"unexpected call target {to}")

            return {"jsonrpc": "2.0", "id": 1, "result": result}, 200, None

        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(smoke, "http_post_json", fake_http_post_json)

    ok, results, summary = smoke.verify_state(
        state,
        {},
        rpc_url=None,
        expected_chain_id=None,
        timeout=0.1,
        allow_dry_run=False,
        expected_max_payout_wei=10**18,
        expected_payout_delay_blocks=1,
        expected_reset_delay_blocks=1,
    )

    assert ok
    assert all(result.ok for result in results)
    assert summary["contracts"]["xlag-bridge-reserve"] == xlag
    assert summary["contracts"]["hub_credit_bridge_escrow"] == hub_credit_bridge_escrow
    assert any(result.name == "hub_credit_bridge_escrow.code" and result.ok for result in results)
    assert summary["observed"]["xlag"]["max_payout_wei"] == 10**18
    assert summary["observed"]["xlag"]["next_proposal_id"] == 5
    assert summary["observed"]["xlag"]["wallet_smoke_nonce"] == 0
    assert summary["observed"]["xlag"]["frob_nonce"] == 0


def test_dry_run_state_fails_without_allow_dry_run() -> None:
    smoke = load_dev_chain_smoke()

    ok, results, _summary = smoke.verify_state(
        {"dry_run": True},
        {},
        rpc_url=None,
        expected_chain_id=None,
        timeout=0.1,
        allow_dry_run=False,
        expected_max_payout_wei=10**18,
        expected_payout_delay_blocks=1,
        expected_reset_delay_blocks=1,
    )

    assert not ok
    assert results[0].name == "deploy-state"
