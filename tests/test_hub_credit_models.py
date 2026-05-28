from __future__ import annotations

import unittest

from main_computer.hub_credit_models import (
    CREDIT_UNIT_KEY,
    CREDIT_UNIT_NAME,
    ChainEventRef,
    CreditDeposit,
    HubCreditAccount,
    HubCreditHold,
    HubCreditTransaction,
    RequestCharge,
    RequestReceipt,
    WorkerEarning,
    WorkerClaim,
    WorkerQualityReport,
    WorkerSettlementBatch,
    make_report_token,
    make_worker_commitment,
    token_digest,
    truncate_for_settlement,
)


class HubCreditModelTests(unittest.TestCase):
    def test_chain_event_ref_is_stable_and_normalized(self) -> None:
        event_a = ChainEventRef(
            chain_id=42424242,
            contract_address="0xABCDEF0000000000000000000000000000000000",
            tx_hash="0xFEED000000000000000000000000000000000000000000000000000000000000",
            log_index=2,
            block_number=99,
        )
        event_b = ChainEventRef(
            chain_id=42424242,
            contract_address="0xabcdef0000000000000000000000000000000000",
            tx_hash="0xfeed000000000000000000000000000000000000000000000000000000000000",
            log_index=2,
            block_number=100,
        )

        self.assertEqual(event_a.event_uid, event_b.event_uid)
        self.assertEqual(event_a.contract_address, "0xabcdef0000000000000000000000000000000000")
        self.assertEqual(event_a.tx_hash, "0xfeed000000000000000000000000000000000000000000000000000000000000")
        self.assertEqual(event_a.as_dict()["event_uid"], event_a.event_uid)

    def test_phase0_accounting_objects_serialize(self) -> None:
        self.assertEqual(CREDIT_UNIT_NAME, "Compute Credits")
        self.assertEqual(CREDIT_UNIT_KEY, "compute_credit")

        account = HubCreditAccount(
            account_id="User One",
            owner_address="0xABCDEF0000000000000000000000000000000000",
            available_credits=50,
            held_credits=10,
        )
        self.assertEqual(account.account_id, "user-one")
        self.assertEqual(account.owner_address, "0xabcdef0000000000000000000000000000000000")
        self.assertEqual(account.as_dict()["available_credits"], 50)

        tx = HubCreditTransaction(
            transaction_id="",
            account_id=account.account_id,
            transaction_type="deposit_indexed",
            credits=50,
            memo="unit test",
        )
        self.assertTrue(tx.transaction_id.startswith("ctx_"))
        self.assertEqual(tx.transaction_type, "deposit_indexed")

        hold = HubCreditHold(
            hold_id="",
            account_id=account.account_id,
            request_id="hub_req_01",
            credits=12,
        )
        self.assertTrue(hold.hold_id.startswith("hold_"))
        self.assertEqual(hold.status, "held")

        charge = RequestCharge(
            charge_id="",
            account_id=account.account_id,
            request_id="hub_req_01",
            hold_id=hold.hold_id,
            charged_credits=9,
            released_credits=3,
        )
        self.assertTrue(charge.charge_id.startswith("chg_"))
        self.assertEqual(charge.as_dict()["released_credits"], 3)

    def test_deposit_uses_chain_event_as_idempotency_source(self) -> None:
        event = ChainEventRef(
            chain_id=42424242,
            contract_address="0x1111111111111111111111111111111111111111",
            tx_hash="0x2222222222222222222222222222222222222222222222222222222222222222",
            log_index=7,
        )
        deposit_a = CreditDeposit(
            deposit_id="",
            account_id="buyer",
            payer_address="0x3333333333333333333333333333333333333333",
            payment_asset="native",
            payment_amount_base_units=123,
            credits_granted=12,
            chain_event=event,
        )
        deposit_b = CreditDeposit(
            deposit_id="",
            account_id="buyer",
            payer_address="0x3333333333333333333333333333333333333333",
            payment_asset="native",
            payment_amount_base_units=999,
            credits_granted=99,
            chain_event=event,
        )

        self.assertEqual(deposit_a.deposit_id, deposit_b.deposit_id)
        self.assertEqual(deposit_a.as_dict()["chain_event"]["event_uid"], event.event_uid)

    def test_worker_commitment_and_report_token_do_not_expose_worker_id(self) -> None:
        commitment = make_worker_commitment(
            worker_node_id="GPU Worker 01",
            request_id="hub_req_01",
            epoch_salt="epoch-secret",
        )
        token = make_report_token(
            hub_secret="hub-secret",
            account_id="buyer",
            request_id="hub_req_01",
            worker_commitment=commitment,
        )
        receipt = RequestReceipt(
            request_id="hub_req_01",
            account_id="buyer",
            charged_credits=9,
            worker_commitment=commitment,
            report_token=token,
            model="fake-model",
        )
        public_receipt = receipt.as_user_dict()

        self.assertTrue(commitment.startswith("wcom_"))
        self.assertTrue(token.startswith("rpt_"))
        self.assertNotIn("gpu-worker-01", commitment)
        self.assertNotIn("GPU Worker 01", token)
        self.assertNotIn("worker_node_id", public_receipt)
        self.assertEqual(public_receipt["worker_commitment"], commitment)

        report = WorkerQualityReport(
            report_id="",
            request_id="hub_req_01",
            account_id="buyer",
            worker_commitment=commitment,
            report_token_hash=token_digest(token),
            rating=2,
            reason="low quality answer",
        )
        self.assertTrue(report.report_id.startswith("rptcase_"))
        self.assertNotIn("report_token_hash", report.as_user_dict())
        self.assertIn("report_token_hash", report.as_admin_dict())

    def test_worker_earning_public_view_hides_raw_worker_and_request_mapping(self) -> None:
        earning = WorkerEarning(
            earning_id="",
            worker_node_id="GPU Worker 01",
            request_id="hub_req_01",
            credits=17,
            worker_commitment="wcom_public",
        )

        private = earning.as_private_dict()
        public = earning.as_public_dict()

        self.assertEqual(private["worker_node_id"], "gpu-worker-01")
        self.assertEqual(private["request_id"], "hub_req_01")
        self.assertNotIn("worker_node_id", public)
        self.assertNotIn("request_id", public)
        self.assertEqual(public["worker_commitment"], "wcom_public")

    def test_worker_claim_serializes_earning_ids_and_idempotency_key(self) -> None:
        claim = WorkerClaim(
            claim_id="",
            worker_node_id="GPU Worker 01",
            claimed_credits=17,
            earning_ids=["earn_a", "earn_b"],
            idempotency_key="claim-key",
            metadata={"phase": 4},
        )

        payload = claim.as_dict()
        self.assertTrue(payload["claim_id"].startswith("wclaim_"))
        self.assertEqual(payload["worker_node_id"], "gpu-worker-01")
        self.assertEqual(payload["claimed_credits"], 17)
        self.assertEqual(payload["earning_ids"], ["earn_a", "earn_b"])
        self.assertEqual(payload["idempotency_key"], "claim-key")
        self.assertEqual(payload["status"], "claimed")

    def test_settlement_batch_truncates_public_value_and_carries_dust(self) -> None:
        published, dust = truncate_for_settlement(1237, bucket_size=100)
        self.assertEqual(published, 1200)
        self.assertEqual(dust, 37)

        batch = WorkerSettlementBatch.from_exact_total(
            window_start="2026-05-24T00:00:00+00:00",
            window_end="2026-05-25T00:00:00+00:00",
            exact_total=1237,
            bucket_size=100,
            worker_count=4,
            batch_root="0xabc",
        )
        self.assertEqual(batch.total_credits_exact, 1237)
        self.assertEqual(batch.total_credits_published, 1200)
        self.assertEqual(batch.dust_credits, 37)
        self.assertTrue(batch.batch_id.startswith("batch_"))


if __name__ == "__main__":
    unittest.main()
