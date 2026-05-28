from __future__ import annotations

import argparse
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.hub_credit_withdrawal import (
    compute_bridge_withdrawal_reconciliation,
    sum_active_hold_units,
    sum_finalized_charge_units,
)


def load_phase3_smoke_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "run_bridge_escrow_withdrawal_reconciliation_smoke.py"
    spec = importlib.util.spec_from_file_location("phase3_withdrawal_smoke", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BridgeEscrowWithdrawalReconciliationTests(unittest.TestCase):
    def test_phase3_smoke_validates_transaction_hashes(self) -> None:
        smoke = load_phase3_smoke_module()

        self.assertTrue(smoke.is_tx_hash("0x" + "a" * 64))
        self.assertTrue(smoke.is_tx_hash("0x" + "A" * 64))
        self.assertFalse(smoke.is_tx_hash("0x" + "a" * 63))
        self.assertFalse(smoke.is_tx_hash("0x" + "g" * 64))
        self.assertFalse(smoke.is_tx_hash(""))
        self.assertFalse(smoke.is_tx_hash(None))

    def test_rpc_transaction_receipt_rejects_invalid_hash_before_polling(self) -> None:
        smoke = load_phase3_smoke_module()

        with mock.patch.object(smoke, "rpc_json") as rpc_json:
            with self.assertRaises(smoke.SmokeFailure):
                smoke.rpc_transaction_receipt("http://127.0.0.1:8545", "0x123", timeout=0.1)
            rpc_json.assert_not_called()

    def test_rpc_transaction_receipt_accepts_valid_hash_and_returns_receipt(self) -> None:
        smoke = load_phase3_smoke_module()
        tx_hash = "0x" + "a" * 64
        receipt = {"transactionHash": tx_hash, "blockNumber": "0x7", "status": "0x1"}

        with mock.patch.object(smoke, "rpc_json", return_value=receipt) as rpc_json:
            self.assertEqual(
                smoke.rpc_transaction_receipt("http://127.0.0.1:8545", tx_hash, timeout=0.1),
                receipt,
            )
            rpc_json.assert_called_once_with(
                "http://127.0.0.1:8545",
                "eth_getTransactionReceipt",
                [tx_hash],
                timeout=0.1,
            )

    def test_internal_spend_produces_unrectified_spend_and_withdrawable_remainder(self) -> None:
        result = compute_bridge_withdrawal_reconciliation(
            deposit_units=100_000_000,
            finalized_spend_units=5_500_000,
            active_hold_units=0,
            already_rectified_units=0,
            already_withdrawn_units=0,
        )

        self.assertTrue(result.can_withdraw)
        self.assertEqual(result.unrectified_units, 5_500_000)
        self.assertEqual(result.withdrawable_units, 94_500_000)

    def test_already_rectified_spend_is_not_rectified_again(self) -> None:
        result = compute_bridge_withdrawal_reconciliation(
            deposit_units=100_000_000,
            finalized_spend_units=5_500_000,
            already_rectified_units=5_500_000,
        )

        self.assertTrue(result.can_withdraw)
        self.assertEqual(result.unrectified_units, 0)
        self.assertEqual(result.withdrawable_units, 94_500_000)

    def test_partial_rectification_computes_only_the_remaining_delta(self) -> None:
        result = compute_bridge_withdrawal_reconciliation(
            deposit_units=100_000_000,
            finalized_spend_units=5_500_000,
            already_rectified_units=2_000_000,
        )

        self.assertTrue(result.can_withdraw)
        self.assertEqual(result.unrectified_units, 3_500_000)

    def test_duplicate_withdrawal_state_does_not_release_twice(self) -> None:
        result = compute_bridge_withdrawal_reconciliation(
            deposit_units=100_000_000,
            finalized_spend_units=5_500_000,
            already_rectified_units=5_500_000,
            already_withdrawn_units=94_500_000,
        )

        self.assertFalse(result.can_withdraw)
        self.assertEqual(result.withdrawable_units, 0)
        self.assertEqual(result.block_reason, "no withdrawable balance remains")

    def test_active_holds_block_withdrawal_and_are_not_finalized_spend(self) -> None:
        holds = [
            {"status": "held", "credits": 6_000_000},
            {"status": "charged", "credits": 8_000_000},
            {"status": "released", "credits": 3_000_000},
        ]
        charges = [{"charged_credits": 5_500_000}]

        result = compute_bridge_withdrawal_reconciliation(
            deposit_units=100_000_000,
            finalized_spend_units=sum_finalized_charge_units(charges),
            active_hold_units=sum_active_hold_units(holds),
            already_rectified_units=0,
        )

        self.assertFalse(result.can_withdraw)
        self.assertEqual(result.active_hold_units, 6_000_000)
        self.assertEqual(result.finalized_spend_units, 5_500_000)
        self.assertEqual(result.block_reason, "active holds block withdrawal reconciliation")

    def test_fractional_atom_unit_spends_reconcile_exactly_with_integers(self) -> None:
        finalized = sum_finalized_charge_units(
            [
                {"charged_credits": 5_500_000},
                {"charged_credits": 2_250_000},
                {"charged_credits": 750_000},
            ]
        )

        result = compute_bridge_withdrawal_reconciliation(
            deposit_units=100_000_000,
            finalized_spend_units=finalized,
            already_rectified_units=5_500_000,
        )

        self.assertEqual(finalized, 8_500_000)
        self.assertTrue(result.can_withdraw)
        self.assertEqual(result.unrectified_units, 3_000_000)
        self.assertEqual(result.withdrawable_units, 91_500_000)

    def test_inconsistent_contract_or_ledger_state_fails_closed(self) -> None:
        rectified_too_far = compute_bridge_withdrawal_reconciliation(
            deposit_units=100_000_000,
            finalized_spend_units=5_500_000,
            already_rectified_units=6_000_000,
        )
        overdrawn = compute_bridge_withdrawal_reconciliation(
            deposit_units=100_000_000,
            finalized_spend_units=5_500_000,
            already_withdrawn_units=100_000_001,
        )
        spent_plus_withdrawn_too_high = compute_bridge_withdrawal_reconciliation(
            deposit_units=100_000_000,
            finalized_spend_units=5_500_000,
            already_withdrawn_units=95_000_000,
        )

        self.assertFalse(rectified_too_far.can_withdraw)
        self.assertIn("rectified", rectified_too_far.block_reason)
        self.assertFalse(overdrawn.can_withdraw)
        self.assertIn("withdrawn", overdrawn.block_reason)
        self.assertFalse(spent_plus_withdrawn_too_high.can_withdraw)
        self.assertIn("exceed", spent_plus_withdrawn_too_high.block_reason)

    def test_smoke_allows_placeholder_contract_for_auto_deploy_path(self) -> None:
        smoke = load_phase3_smoke_module()
        args = argparse.Namespace(rpc_url="", chain_id=0, contract_address="")
        chain = smoke.chain_config(
            {
                "chain": {
                    "rpc_url": "http://127.0.0.1:18545",
                    "chain_id": 42424242,
                    "contract_address": smoke.PLACEHOLDER_CONTRACT_ADDRESS,
                }
            },
            args,
        )

        self.assertEqual(chain["contract_address"], smoke.PLACEHOLDER_CONTRACT_ADDRESS)
        self.assertTrue(smoke.is_placeholder_contract_address(chain["contract_address"]))

    def test_smoke_parses_forge_deployment_address_outputs(self) -> None:
        smoke = load_phase3_smoke_module()
        expected = "0x1234567890abcdef1234567890abcdef12345678"

        self.assertEqual(
            smoke.parse_deployed_contract_address('{"deployedTo":"0x1234567890ABCDEF1234567890abcdef12345678"}'),
            expected,
        )
        self.assertEqual(
            smoke.parse_deployed_contract_address(f"Deployed to: {expected}\nTransaction hash: 0x" + "a" * 64),
            expected,
        )

    def test_smoke_can_use_deterministic_dev_key_fallback_for_local_anvil_actor(self) -> None:
        smoke = load_phase3_smoke_module()
        key = smoke.env_or_manifest_private_key(
            {
                "address": "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc",
                "private_key": "",
                "private_key_env": "",
            }
        )

        self.assertEqual(key, "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba")


    def test_anvil_port_from_rpc_url_uses_configured_port(self) -> None:
        smoke = load_phase3_smoke_module()

        self.assertEqual(smoke.anvil_port_from_rpc_url("http://127.0.0.1:8545"), 8545)
        self.assertEqual(smoke.anvil_port_from_rpc_url("http://localhost:18545"), 18545)

    def test_docker_anvil_command_publishes_requested_loopback_port(self) -> None:
        smoke = load_phase3_smoke_module()

        command = smoke.docker_anvil_command(
            docker="docker",
            port=8545,
            chain_id=42424242,
            container_name="phase3-test-anvil",
            network_name="phase3-test-net",
        )

        self.assertIn("-p", command)
        self.assertIn("127.0.0.1:8545:8545", command)
        self.assertIn("--network", command)
        self.assertIn("phase3-test-net", command)
        self.assertIn("--chain-id", command)
        self.assertIn("42424242", command)

    def test_docker_rpc_url_can_use_auto_started_anvil_network_override(self) -> None:
        smoke = load_phase3_smoke_module()

        self.assertEqual(
            smoke.docker_rpc_url(
                "http://127.0.0.1:8545",
                override="http://main-computer-phase3-anvil-42424242-8545:8545",
            ),
            "http://main-computer-phase3-anvil-42424242-8545:8545",
        )
        self.assertEqual(smoke.docker_network_args("phase3-test-net"), ["--network", "phase3-test-net"])

    def test_rpc_url_with_port_preserves_loopback_host_and_replaces_port(self) -> None:
        smoke = load_phase3_smoke_module()

        self.assertEqual(smoke.rpc_url_with_port("http://127.0.0.1:8545", 18545), "http://127.0.0.1:18545")
        self.assertEqual(smoke.rpc_url_with_port("http://localhost:8545", 18545), "http://localhost:18545")

    def test_foundry_tool_detection_uses_docker_only_when_local_tool_missing(self) -> None:
        smoke = load_phase3_smoke_module()

        with mock.patch.object(smoke.shutil, "which", side_effect=lambda name: "/usr/bin/forge" if name == "forge" else "/usr/bin/docker"):
            self.assertFalse(smoke.uses_dockerized_foundry_tool("forge", no_docker=False))

        with mock.patch.object(smoke.shutil, "which", side_effect=lambda name: None if name == "forge" else "/usr/bin/docker"):
            self.assertTrue(smoke.uses_dockerized_foundry_tool("forge", no_docker=False))
            self.assertFalse(smoke.uses_dockerized_foundry_tool("forge", no_docker=True))

    def test_attach_managed_docker_anvil_updates_chain_for_shared_network(self) -> None:
        smoke = load_phase3_smoke_module()
        chain = {"rpc_url": "http://127.0.0.1:8545"}

        smoke.attach_managed_anvil_to_chain(
            chain,
            {
                "mode": "docker",
                "rpc_url": "http://127.0.0.1:18545",
                "docker_network": "phase3-test-net",
                "docker_rpc_url": "http://phase3-test-anvil:8545",
            },
        )

        self.assertEqual(chain["rpc_url"], "http://127.0.0.1:18545")
        self.assertEqual(chain["docker_network"], "phase3-test-net")
        self.assertEqual(chain["docker_rpc_url"], "http://phase3-test-anvil:8545")

    def test_auto_started_anvil_does_not_persist_contract_address_by_default(self) -> None:
        smoke = load_phase3_smoke_module()

        args = argparse.Namespace(
            no_persist_auto_deploy_contract=False,
            persist_auto_started_contract_address=False,
            contract_address="",
        )
        self.assertFalse(smoke.should_persist_auto_deployed_contract(args, chain_auto_started=True))
        self.assertTrue(smoke.should_persist_auto_deployed_contract(args, chain_auto_started=False))

        args.persist_auto_started_contract_address = True
        self.assertTrue(smoke.should_persist_auto_deployed_contract(args, chain_auto_started=True))

        args.no_persist_auto_deploy_contract = True
        self.assertFalse(smoke.should_persist_auto_deployed_contract(args, chain_auto_started=False))

    def test_private_key_validator_rejects_truncated_dev_key(self) -> None:
        smoke = load_phase3_smoke_module()

        self.assertTrue(smoke.is_private_key("0x" + "a" * 64))
        self.assertFalse(smoke.is_private_key("0x" + "a" * 63))
        self.assertFalse(smoke.is_private_key("0x" + "g" * 64))

    def test_constructor_address_arg_appends_left_padded_address(self) -> None:
        smoke = load_phase3_smoke_module()

        encoded = smoke.append_constructor_address_arg(
            "0x6000",
            "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc",
        )

        self.assertEqual(
            encoded,
            "0x6000" + "0" * 24 + "9965507d1a55bcc2695c58ba16fb37d819b0a4dc",
        )

    def test_cast_send_uses_unlocked_json_rpc_before_private_key(self) -> None:
        smoke = load_phase3_smoke_module()
        calldata_calls = []
        tx_calls = []

        def fake_cast_calldata(**kwargs):
            calldata_calls.append(kwargs)
            return "0x12345678"

        def fake_rpc_send_unlocked_transaction(**kwargs):
            tx_calls.append(kwargs)
            return {"tx_hash": "0x" + "a" * 64, "block_number": 2}

        with mock.patch.object(smoke, "cast_calldata", side_effect=fake_cast_calldata), \
            mock.patch.object(smoke, "rpc_send_unlocked_transaction", side_effect=fake_rpc_send_unlocked_transaction), \
            mock.patch.object(smoke, "run_command") as run_command:
            result = smoke.cast_send(
                chain={"contract_address": "0x1111111111111111111111111111111111111111", "rpc_url": "http://127.0.0.1:8545"},
                base_args=[
                    "depositFor(address,uint256,bytes32,string)",
                    "0x2222222222222222222222222222222222222222",
                    "1",
                    "0x" + "b" * 64,
                    "memo",
                    "--value",
                    "1",
                ],
                private_key="0x" + "c" * 64,
                sender_address="0x3333333333333333333333333333333333333333",
                repo_root=Path("."),
                no_docker=False,
                timeout=1,
            )

        self.assertEqual(result["mode"], "unlocked-rpc")
        self.assertFalse(run_command.called)
        self.assertEqual(calldata_calls[0]["base_args"], [
            "depositFor(address,uint256,bytes32,string)",
            "0x2222222222222222222222222222222222222222",
            "1",
            "0x" + "b" * 64,
            "memo",
        ])
        self.assertEqual(tx_calls[0]["sender_address"], "0x3333333333333333333333333333333333333333")
        self.assertEqual(tx_calls[0]["to_address"], "0x1111111111111111111111111111111111111111")
        self.assertEqual(tx_calls[0]["data"], "0x12345678")
        self.assertEqual(tx_calls[0]["value"], 1)

    def test_cast_send_create_unlocked_uses_eth_send_transaction(self) -> None:
        smoke = load_phase3_smoke_module()
        tx_calls = []

        def fake_rpc_send_unlocked_transaction(**kwargs):
            tx_calls.append(kwargs)
            return {
                "tx_hash": "0x" + "d" * 64,
                "block_number": 4,
                "contract_address": "0x4444444444444444444444444444444444444444",
                "receipt": {"contractAddress": "0x4444444444444444444444444444444444444444"},
            }

        with mock.patch.object(smoke, "rpc_send_unlocked_transaction", side_effect=fake_rpc_send_unlocked_transaction), \
            mock.patch.object(smoke, "run_command") as run_command:
            result = smoke.cast_send_create_unlocked(
                chain={"rpc_url": "http://127.0.0.1:8545"},
                creation_calldata="0x6000",
                sender_address="0x5555555555555555555555555555555555555555",
                repo_root=Path("."),
                no_docker=False,
                timeout=1,
            )

        self.assertEqual(result["mode"], "unlocked-rpc")
        self.assertEqual(result["contract_address"], "0x4444444444444444444444444444444444444444")
        self.assertFalse(run_command.called)
        self.assertEqual(tx_calls[0]["sender_address"], "0x5555555555555555555555555555555555555555")
        self.assertEqual(tx_calls[0]["data"], "0x6000")
        self.assertNotIn("to_address", tx_calls[0])

    def test_ledger_records_bridge_rectification_and_withdrawal_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp) / "compute_credits")
            ledger.issue(account_id="requester", credits=100_000_000, owner_address="0xabc", memo="fund")
            hold = ledger.create_hold(account_id="requester", request_id="req-1", credits=6_000_000)["hold"]
            ledger.charge_hold(hold_id=hold["hold_id"], charged_credits=5_500_000, worker_node_id="worker-1")

            account_after_charge = ledger.get_account("requester")
            self.assertEqual(account_after_charge.available_credits, 94_500_000)
            self.assertEqual(account_after_charge.spent_credits, 5_500_000)
            self.assertEqual(sum(charge.charged_credits for charge in ledger.list_charges(account_id="requester")), 5_500_000)

            rectified = ledger.record_bridge_reconciliation(
                account_id="requester",
                rectified_credits=5_500_000,
                rectification_id="0x" + "a" * 64,
                memo="rectified on-chain",
            )
            duplicate_rectified = ledger.record_bridge_reconciliation(
                account_id="requester",
                rectified_credits=5_500_000,
                rectification_id="0x" + "a" * 64,
                memo="rectified on-chain",
            )
            self.assertFalse(rectified["idempotent"])
            self.assertTrue(duplicate_rectified["idempotent"])
            self.assertEqual(ledger.get_account("requester").available_credits, 94_500_000)

            withdrawn = ledger.record_bridge_reconciliation(
                account_id="requester",
                withdrawn_credits=94_500_000,
                withdrawal_id="0x" + "b" * 64,
                recipient_address="0xdef",
                memo="released on-chain",
            )
            duplicate_withdrawn = ledger.record_bridge_reconciliation(
                account_id="requester",
                withdrawn_credits=94_500_000,
                withdrawal_id="0x" + "b" * 64,
                recipient_address="0xdef",
                memo="released on-chain",
            )

            self.assertFalse(withdrawn["idempotent"])
            self.assertTrue(duplicate_withdrawn["idempotent"])
            self.assertEqual(ledger.get_account("requester").available_credits, 0)

            totals = ledger.bridge_reconciliation_totals("requester")
            self.assertEqual(totals["rectified_credits"], 5_500_000)
            self.assertEqual(totals["withdrawn_credits"], 94_500_000)


if __name__ == "__main__":
    unittest.main()
