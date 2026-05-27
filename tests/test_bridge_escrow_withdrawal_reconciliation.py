from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.hub_credit_withdrawal import (
    compute_bridge_withdrawal_reconciliation,
    sum_active_hold_units,
    sum_finalized_charge_units,
)


class BridgeEscrowWithdrawalReconciliationTests(unittest.TestCase):
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
