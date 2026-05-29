from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.hub_credit_models import WorkerEarning, make_worker_commitment
from main_computer.hub_plex_models import HubRequestRecord, HubRequestStatus


def post_json(url: str, payload: dict, *, timeout: float = 5.0, allow_error: bool = False) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if not allow_error:
            raise
        body = json.loads(exc.read().decode("utf-8"))
        body["_http_status"] = exc.code
        return body


def get_json(url: str, *, timeout: float = 5.0) -> dict:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class WorkerSettlementClaimTests(unittest.TestCase):
    def _start_server(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return thread

    def _start_hub(self, *, credits_per_request: int = 5):
        tmp = tempfile.TemporaryDirectory()
        config = MainComputerConfig(
            workspace=Path(tmp.name),
            model="mock-fast-chat",
            hub_root=Path(tmp.name) / "hub-runtime",
            hub_credits_per_request=credits_per_request,
        )
        hub = HubHttpServer(("127.0.0.1", 0), config, verbose=False)
        thread = self._start_server(hub)

        def cleanup() -> None:
            hub.shutdown()
            thread.join(5)
            hub.server_close()
            tmp.cleanup()

        self.addCleanup(cleanup)
        return hub, f"http://127.0.0.1:{hub.server_port}"

    def test_worker_earnings_become_claimable_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            first = ledger.record_worker_earning(worker_node_id="Worker One", request_id="req-1", credits=7)
            second = ledger.record_worker_earning(worker_node_id="Worker One", request_id="req-2", credits=11)
            other = ledger.record_worker_earning(worker_node_id="Worker Two", request_id="req-3", credits=13)

            first_id = first["worker_earning"]["earning_id"]
            second_id = second["worker_earning"]["earning_id"]
            other_id = other["worker_earning"]["earning_id"]

            before = ledger.worker_claim_totals("worker-one")
            self.assertEqual(before["finalized_earning_units"], 18)
            self.assertEqual(before["claimable_units"], 18)
            self.assertEqual(before["already_claimed_units"], 0)
            self.assertEqual(before["claimable_earning_ids"], [first_id, second_id])

            with self.assertRaises(ValueError):
                ledger.record_worker_claim(worker_node_id="worker-one", earning_ids=[other_id], idempotency_key="wrong-worker")

            with self.assertRaises(KeyError):
                ledger.record_worker_claim(worker_node_id="worker-one", earning_ids=["earn_missing"], idempotency_key="missing")

            with self.assertRaises(ValueError):
                ledger.record_worker_claim(
                    worker_node_id="worker-one",
                    earning_ids=[first_id, second_id],
                    claim_credits=17,
                    idempotency_key="bad-total",
                )

            claimed = ledger.record_worker_claim(worker_node_id="worker-one", idempotency_key="claim-all")
            self.assertTrue(claimed["ok"])
            self.assertFalse(claimed["idempotent"])
            self.assertEqual(claimed["claimed_credits"], 18)
            self.assertEqual(claimed["claimed_count"], 2)
            self.assertEqual(claimed["claim"]["earning_ids"], [first_id, second_id])
            self.assertEqual(claimed["worker_claim_totals"]["claimable_units"], 0)

            repeated = ledger.record_worker_claim(worker_node_id="worker-one", idempotency_key="claim-all")
            self.assertTrue(repeated["ok"])
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(repeated["claimed_credits"], 18)
            self.assertEqual(len(ledger.list_worker_claims(worker_node_id="worker-one")), 1)

            new_key_duplicate = ledger.record_worker_claim(worker_node_id="worker-one", idempotency_key="claim-again")
            self.assertTrue(new_key_duplicate["ok"])
            self.assertFalse(new_key_duplicate["idempotent"])
            self.assertEqual(new_key_duplicate["claimed_credits"], 0)
            self.assertEqual(new_key_duplicate["claimed_count"], 0)
            self.assertIsNone(new_key_duplicate["claim"])
            self.assertEqual(ledger.worker_claim_totals("worker-one")["already_claimed_units"], 18)

    def test_unfinalized_worker_earnings_are_not_claimable_and_normalization_adds_claims_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            earned = ledger.record_worker_earning(worker_node_id="worker-one", request_id="req-earned", credits=5)
            earned_id = earned["worker_earning"]["earning_id"]

            data = json.loads((Path(tmp) / "ledger.json").read_text(encoding="utf-8"))
            pending = WorkerEarning(
                earning_id="",
                worker_node_id="worker-one",
                request_id="req-pending",
                credits=99,
                worker_commitment=make_worker_commitment(
                    worker_node_id="worker-one",
                    request_id="req-pending",
                    epoch_salt="test",
                ),
                status="batched",
            )
            data["worker_earnings"][pending.earning_id] = pending.as_private_dict()
            data.pop("worker_claims", None)
            (Path(tmp) / "ledger.json").write_text(json.dumps(data), encoding="utf-8")

            reloaded = HubCreditLedger(Path(tmp))
            totals = reloaded.worker_claim_totals("worker-one")
            self.assertEqual(totals["finalized_earning_units"], 5)
            self.assertEqual(totals["claimable_units"], 5)
            self.assertEqual(totals["claimable_earning_ids"], [earned_id])
            self.assertEqual(reloaded.status()["worker_claim_count"], 0)

            with self.assertRaises(ValueError):
                reloaded.record_worker_claim(worker_node_id="worker-one", earning_ids=[pending.earning_id], idempotency_key="pending")

    def test_high_precision_worker_claim_payout_is_rounded_for_settlement_and_dust_stays_in_bridge_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            earning = ledger.record_worker_earning(
                worker_node_id="Precision Worker",
                request_id="high-precision-req",
                credits=5_500_123,
            )
            claim = ledger.record_worker_claim(
                worker_node_id="precision-worker",
                earning_ids=[earning["worker_earning"]["earning_id"]],
                idempotency_key="precision-claim",
            )
            claim_id = claim["claim"]["claim_id"]

            totals = ledger.worker_settlement_totals("precision-worker")
            self.assertEqual(totals["precision_places"], 3)
            self.assertEqual(totals["rounding_bucket_credits"], 1_000)
            self.assertEqual(totals["settleable_units_exact"], 5_500_123)
            self.assertEqual(totals["settleable_units_published"], 5_500_000)
            self.assertEqual(totals["settleable_dust_units"], 123)
            self.assertEqual(totals["bridge_retained_units_if_settled"], 123)

            batch = ledger.create_worker_settlement_batch(
                worker_node_id="precision-worker",
                idempotency_key="precision-batch",
            )
            self.assertTrue(batch["ok"])
            self.assertFalse(batch["idempotent"])
            self.assertEqual(batch["total_credits_exact"], 5_500_123)
            self.assertEqual(batch["total_credits_published"], 5_500_000)
            self.assertEqual(batch["dust_credits"], 123)
            self.assertEqual(batch["bridge_retained_credits"], 123)
            self.assertEqual(batch["batch"]["claim_ids"], [claim_id])
            self.assertEqual(batch["batch"]["precision_places"], 3)
            self.assertEqual(batch["batch"]["rounding_bucket_credits"], 1_000)
            self.assertEqual(batch["worker_settlement_totals"]["settleable_units_exact"], 0)

            settled = ledger.settle_worker_settlement_batch(
                batch_id=batch["batch"]["batch_id"],
                settlement_reference="operator-paid-rounded",
                idempotency_key="precision-settle",
            )
            self.assertTrue(settled["ok"])
            self.assertFalse(settled["idempotent"])
            self.assertEqual(settled["settled_credits"], 5_500_000)
            self.assertEqual(settled["additional_settled_credits"], 5_500_000)
            self.assertEqual(settled["bridge_retained_credits"], 123)
            self.assertEqual(settled["transaction"]["credits"], 5_500_000)
            self.assertEqual(settled["transaction"]["metadata"]["total_credits_exact"], 5_500_123)
            self.assertEqual(settled["transaction"]["metadata"]["bridge_retained_credits"], 123)
            self.assertEqual(ledger.list_worker_claims(worker_node_id="precision-worker")[0].status, "settled")

            duplicate = ledger.settle_worker_settlement_batch(
                batch_id=batch["batch"]["batch_id"],
                settlement_reference="operator-paid-rounded",
                idempotency_key="precision-settle",
            )
            self.assertTrue(duplicate["idempotent"])
            self.assertEqual(duplicate["additional_settled_credits"], 0)
            self.assertEqual(duplicate["bridge_retained_credits"], 123)

    def test_operator_settlement_proof_records_rounded_payout_proof_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            earning = ledger.record_worker_earning(
                worker_node_id="Proof Worker",
                request_id="proof-req",
                credits=5_500_123,
            )
            claim = ledger.record_worker_claim(
                worker_node_id="proof-worker",
                earning_ids=[earning["worker_earning"]["earning_id"]],
                idempotency_key="proof-claim",
            )
            batch = ledger.create_worker_settlement_batch(
                worker_node_id="proof-worker",
                claim_ids=[claim["claim"]["claim_id"]],
                idempotency_key="proof-batch",
            )["batch"]

            with self.assertRaises(ValueError):
                ledger.record_worker_settlement_proof(
                    batch_id=batch["batch_id"],
                    idempotency_key="missing-reference",
                )

            proof = ledger.record_worker_settlement_proof(
                batch_id=batch["batch_id"],
                settlement_reference="operator-wire-reference-001",
                payout_rail="operator-manual",
                operator_id="Settlement Operator",
                settlement_proof={
                    "executed_credits": 5_500_000,
                    "bridge_retained_credits": 123,
                    "receipt": "manual-ledger-row-001",
                },
                idempotency_key="operator-proof",
                metadata={"phase7a_operator_settlement_proof": True},
            )

            self.assertTrue(proof["ok"])
            self.assertFalse(proof["idempotent"])
            self.assertEqual(proof["settled_credits"], 5_500_000)
            self.assertEqual(proof["additional_settled_credits"], 5_500_000)
            self.assertEqual(proof["bridge_retained_credits"], 123)
            self.assertEqual(proof["batch"]["status"], "settled")
            self.assertEqual(proof["batch"]["payout_rail"], "operator-manual")
            self.assertEqual(proof["batch"]["operator_id"], "settlement-operator")
            self.assertEqual(proof["batch"]["settlement_reference"], "operator-wire-reference-001")
            self.assertTrue(proof["batch"]["settlement_proof_id"].startswith("proof_"))
            self.assertEqual(len(proof["batch"]["settlement_proof_hash"]), 64)
            self.assertEqual(proof["transaction"]["credits"], 5_500_000)
            self.assertEqual(proof["transaction"]["metadata"]["settlement_proof_id"], proof["batch"]["settlement_proof_id"])
            self.assertEqual(proof["transaction"]["metadata"]["settlement_proof_hash"], proof["batch"]["settlement_proof_hash"])

            repeated = ledger.record_worker_settlement_proof(
                batch_id=batch["batch_id"],
                settlement_reference="operator-wire-reference-001",
                payout_rail="operator-manual",
                operator_id="Settlement Operator",
                settlement_proof={"receipt": "manual-ledger-row-001"},
                idempotency_key="operator-proof",
            )
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(repeated["additional_settled_credits"], 0)
            self.assertEqual(repeated["batch"]["settlement_proof_id"], proof["batch"]["settlement_proof_id"])


    def test_phase7b_chain_payout_execution_records_rounded_receipt_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            earning = ledger.record_worker_earning(
                worker_node_id="Chain Worker",
                request_id="chain-req",
                credits=5_500_123,
            )
            claim = ledger.record_worker_claim(
                worker_node_id="chain-worker",
                earning_ids=[earning["worker_earning"]["earning_id"]],
                idempotency_key="chain-claim",
            )
            batch = ledger.create_worker_settlement_batch(
                worker_node_id="chain-worker",
                claim_ids=[claim["claim"]["claim_id"]],
                idempotency_key="chain-batch",
            )["batch"]

            with self.assertRaises(ValueError):
                ledger.record_worker_settlement_chain_execution(
                    batch_id=batch["batch_id"],
                    chain_id=42424242,
                    contract_address="0x1111111111111111111111111111111111111111",
                    recipient_address="0x2222222222222222222222222222222222222222",
                    payout_units_executed=5_500_123,
                    settlement_tx_hash="0x" + "a" * 64,
                    proposal_id="bad-exact-overpay",
                    idempotency_key="bad-exact-overpay",
                )

            receipt = ledger.record_worker_settlement_chain_execution(
                batch_id=batch["batch_id"],
                chain_id=42424242,
                contract_address="0x1111111111111111111111111111111111111111",
                recipient_address="0x2222222222222222222222222222222222222222",
                payout_units_executed=5_500_000,
                settlement_tx_hash="0x" + "a" * 64,
                proposal_id="7",
                block_number=12345,
                payout_rail="xlag-bridge-reserve",
                operator_id="Chain Operator",
                idempotency_key="chain-execution",
                metadata={"phase7b_chain_payout_execution": True},
            )

            self.assertTrue(receipt["ok"])
            self.assertFalse(receipt["idempotent"])
            self.assertEqual(receipt["settled_credits"], 5_500_000)
            self.assertEqual(receipt["additional_settled_credits"], 5_500_000)
            self.assertEqual(receipt["bridge_retained_credits"], 123)
            self.assertEqual(receipt["batch"]["payout_rail"], "xlag-bridge-reserve")
            self.assertEqual(receipt["batch"]["settlement_tx_hash"], "0x" + "a" * 64)
            self.assertEqual(receipt["batch"]["operator_id"], "chain-operator")
            self.assertEqual(receipt["chain_payout_execution"]["payout_units_executed"], 5_500_000)
            self.assertEqual(receipt["chain_payout_execution"]["bridge_retained_credits"], 123)
            self.assertEqual(receipt["transaction"]["metadata"]["payout_rail"], "xlag-bridge-reserve")
            self.assertEqual(receipt["transaction"]["metadata"]["total_credits_exact"], 5_500_123)
            self.assertEqual(receipt["transaction"]["metadata"]["total_credits_published"], 5_500_000)
            self.assertEqual(receipt["transaction"]["metadata"]["settlement_tx_hash"], "0x" + "a" * 64)

            repeated = ledger.record_worker_settlement_chain_execution(
                batch_id=batch["batch_id"],
                chain_id=42424242,
                contract_address="0x1111111111111111111111111111111111111111",
                recipient_address="0x2222222222222222222222222222222222222222",
                payout_units_executed=5_500_000,
                settlement_tx_hash="0x" + "a" * 64,
                proposal_id="7",
                block_number=12345,
                payout_rail="xlag-bridge-reserve",
                operator_id="Chain Operator",
                idempotency_key="chain-execution",
            )
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(repeated["additional_settled_credits"], 0)

            with self.assertRaises(ValueError):
                ledger.record_worker_settlement_chain_execution(
                    batch_id=batch["batch_id"],
                    chain_id=42424242,
                    contract_address="0x1111111111111111111111111111111111111111",
                    recipient_address="0x2222222222222222222222222222222222222222",
                    payout_units_executed=5_500_000,
                    settlement_tx_hash="0x" + "b" * 64,
                    proposal_id="8",
                    payout_rail="xlag-bridge-reserve",
                    idempotency_key="different-tx",
                )

    def test_phase7b_chain_payout_execution_api(self) -> None:
        hub, hub_base = self._start_hub()
        worker = "chain-api-worker"
        earning = hub.credit_ledger.record_worker_earning(worker_node_id=worker, request_id="chain-api-req", credits=5_500_123)
        claim = post_json(
            f"{hub_base}/api/hub/v1/workers/claims",
            {
                "worker_node_id": worker,
                "earning_ids": [earning["worker_earning"]["earning_id"]],
                "idempotency_key": "chain-api-claim",
            },
        )
        batch = post_json(
            f"{hub_base}/api/hub/v1/workers/settlements/batches",
            {
                "worker_node_id": worker,
                "claim_ids": [claim["claim"]["claim_id"]],
                "idempotency_key": "chain-api-batch",
            },
        )

        bad = post_json(
            f"{hub_base}/api/hub/v1/workers/settlements/chain-executions",
            {
                "batch_id": batch["batch"]["batch_id"],
                "chain_id": 42424242,
                "contract_address": "0x1111111111111111111111111111111111111111",
                "recipient_address": "0x2222222222222222222222222222222222222222",
                "payout_units_executed": 5_500_123,
                "settlement_tx_hash": "0x" + "c" * 64,
            },
            allow_error=True,
        )
        self.assertEqual(bad["_http_status"], 400)
        self.assertIn("rounded published settlement amount", bad["error"])

        receipt = post_json(
            f"{hub_base}/api/hub/v1/workers/settlements/chain-executions",
            {
                "batch_id": batch["batch"]["batch_id"],
                "chain_id": 42424242,
                "contract_address": "0x1111111111111111111111111111111111111111",
                "recipient_address": "0x2222222222222222222222222222222222222222",
                "payout_units_executed": 5_500_000,
                "settlement_tx_hash": "0x" + "c" * 64,
                "proposal_id": "42",
                "block_number": 777,
                "payout_rail": "xlag-bridge-reserve",
                "operator_id": "api-chain-operator",
                "idempotency_key": "chain-api-execution",
            },
        )
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["settled_credits"], 5_500_000)
        self.assertEqual(receipt["chain_payout_execution"]["contract_address"], "0x1111111111111111111111111111111111111111")
        self.assertEqual(receipt["chain_payout_execution"]["recipient_address"], "0x2222222222222222222222222222222222222222")
        self.assertEqual(receipt["chain_payout_execution"]["payout_units_executed"], 5_500_000)
        self.assertEqual(receipt["chain_payout_execution"]["bridge_retained_credits"], 123)
        self.assertEqual(receipt["batch"]["settlement_tx_hash"], "0x" + "c" * 64)
        self.assertEqual(receipt["batch"]["payout_rail"], "xlag-bridge-reserve")

        public_after = get_json(f"{hub_base}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker})}")
        public_after_json = json.dumps(public_after, sort_keys=True)
        self.assertNotIn("5500123", public_after_json)
        self.assertEqual(public_after["settled_units_published"], 5_500_000)
        self.assertIn("settlement_proof_id", public_after["batches"][0])


    def test_worker_settlement_api_uses_worker_precision_default(self) -> None:
        hub, hub_base = self._start_hub()
        worker = "precision-default-worker"
        registered = post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": worker,
                "endpoint": "http://127.0.0.1:1",
                "model": "mock-fast-chat",
                "models": ["mock-fast-chat"],
                "credits_per_request": 5_555_555,
                "settlement_precision_places": 2,
                "capabilities": {"provider": "mock", "worker_pull_v0": True},
            },
        )
        self.assertEqual(registered["worker"]["settlement_precision_places"], 2)
        earning = hub.credit_ledger.record_worker_earning(worker_node_id=worker, request_id="precision-api-req", credits=5_555_555)
        claimed = post_json(
            f"{hub_base}/api/hub/v1/workers/claims",
            {
                "worker_node_id": worker,
                "earning_ids": [earning["worker_earning"]["earning_id"]],
                "idempotency_key": "precision-api-claim",
            },
        )
        claim_id = claimed["claim"]["claim_id"]

        public_totals = get_json(f"{hub_base}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker})}")
        self.assertEqual(public_totals["precision_places"], 2)
        self.assertEqual(public_totals["rounding_bucket_credits"], 10_000)
        self.assertNotIn("settleable_units_exact", public_totals)
        self.assertNotIn("settleable_dust_units", public_totals)
        self.assertEqual(public_totals["settleable_units_published"], 5_550_000)
        self.assertTrue(public_totals["privacy"]["exact_amounts_hidden"])

        totals = get_json(f"{hub_base}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker, 'audit': '1'})}")
        self.assertEqual(totals["precision_places"], 2)
        self.assertEqual(totals["rounding_bucket_credits"], 10_000)
        self.assertEqual(totals["settleable_units_exact"], 5_555_555)
        self.assertEqual(totals["settleable_units_published"], 5_550_000)
        self.assertEqual(totals["settleable_dust_units"], 5_555)

        batch = post_json(
            f"{hub_base}/api/hub/v1/workers/settlements/batches",
            {
                "worker_node_id": worker,
                "idempotency_key": "precision-api-batch",
            },
        )
        self.assertEqual(batch["batch"]["claim_ids"], [claim_id])
        self.assertEqual(batch["total_credits_exact"], 5_555_555)
        self.assertEqual(batch["total_credits_published"], 5_550_000)
        self.assertEqual(batch["dust_credits"], 5_555)
        self.assertEqual(batch["bridge_retained_credits"], 5_555)
        self.assertEqual(batch["batch"]["precision_places"], 2)

        proof = post_json(
            f"{hub_base}/api/hub/v1/workers/settlements/proofs",
            {
                "batch_id": batch["batch"]["batch_id"],
                "settlement_reference": "operator-proof-api-reference",
                "payout_rail": "operator-manual",
                "operator_id": "api-operator",
                "settlement_proof": {"executed_credits": 5_550_000, "bridge_retained_credits": 5_555},
                "idempotency_key": "precision-api-proof",
            },
        )
        self.assertTrue(proof["ok"])
        self.assertEqual(proof["settled_credits"], 5_550_000)
        self.assertEqual(proof["batch"]["payout_rail"], "operator-manual")
        self.assertEqual(proof["batch"]["operator_id"], "api-operator")
        self.assertTrue(proof["batch"]["settlement_proof_id"].startswith("proof_"))

        public_after = get_json(f"{hub_base}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker})}")
        self.assertEqual(public_after["settled_units_published"], 5_550_000)
        self.assertIn("settlement_proof_id", public_after["batches"][0])
        self.assertNotIn("total_credits_exact", public_after["batches"][0])


    def test_phase6_public_payout_surfaces_hide_high_precision_worker_amounts(self) -> None:
        hub, hub_base = self._start_hub(credits_per_request=5_500_123)
        requester = "phase6-privacy-requester"
        worker = "phase6-privacy-worker"
        exact_units = 5_500_123
        published_units = 5_500_000
        hub.credit_ledger.issue(account_id=requester, credits=100_000_000, memo="phase6 privacy test")

        post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": worker,
                "endpoint": "http://127.0.0.1:1",
                "model": "mock-fast-chat",
                "models": ["mock-fast-chat"],
                "credits_per_request": exact_units,
                "capabilities": {"provider": "mock", "worker_pull_v0": True},
            },
        )
        submitted = post_json(
            f"{hub_base}/api/hub/v1/requests",
            {
                "account_id": requester,
                "client_node_id": requester,
                "model": "mock-fast-chat",
                "prompt": "phase6 privacy settlement",
                "max_credits": 6_000_000,
                "execution_mode": "worker_pull_v0",
                "metadata": {"worker_pull_v0": True, "mock_provider_config": {"answer": "phase6 answer"}},
                "idempotency_key": "phase6-privacy-request",
            },
        )["request"]
        self.assertEqual(submitted["state"], "queued")
        lease = post_json(f"{hub_base}/api/hub/v1/workers/poll", {"worker_node_id": worker})["lease"]
        completed = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": worker,
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {"content": "phase6 answer", "provider": "mock-worker", "model": "mock-fast-chat"},
                },
            },
        )["request"]
        self.assertEqual(completed["charged_credits"], exact_units)

        payout_queue_json = json.dumps(completed["response"]["metadata"]["hub"]["payout_queue"], sort_keys=True)
        self.assertNotIn(str(exact_units), payout_queue_json)
        self.assertIn(str(published_units), payout_queue_json)

        with urlopen(f"{hub_base}/api/hub/status", timeout=5) as response:
            public_status = json.loads(response.read().decode("utf-8"))
        public_energy_json = json.dumps(public_status["energy"], sort_keys=True)
        self.assertNotIn(str(exact_units), public_energy_json)
        self.assertIn(str(published_units), public_energy_json)
        self.assertTrue(public_status["energy"]["payout_queue"]["privacy"]["exact_amounts_hidden"])

        with urlopen(f"{hub_base}/api/hub/payouts?{urlencode({'node_id': worker})}", timeout=5) as response:
            public_payouts = json.loads(response.read().decode("utf-8"))
        public_payouts_json = json.dumps(public_payouts, sort_keys=True)
        self.assertNotIn(str(exact_units), public_payouts_json)
        self.assertEqual(public_payouts["pending_credits"], published_units)
        self.assertTrue(public_payouts["privacy"]["exact_amounts_hidden"])

        with urlopen(f"{hub_base}/api/hub/payouts?{urlencode({'node_id': worker, 'audit': '1'})}", timeout=5) as response:
            audit_payouts = json.loads(response.read().decode("utf-8"))
        audit_payouts_json = json.dumps(audit_payouts, sort_keys=True)
        self.assertIn(str(exact_units), audit_payouts_json)
        self.assertEqual(audit_payouts["pending_credits_exact"], exact_units)

        claim = post_json(
            f"{hub_base}/api/hub/v1/workers/claims",
            {"worker_node_id": worker, "idempotency_key": "phase6-privacy-claim"},
        )
        claim_id = claim["claim"]["claim_id"]

        public_totals = get_json(f"{hub_base}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker})}")
        public_totals_json = json.dumps(public_totals, sort_keys=True)
        self.assertNotIn(str(exact_units), public_totals_json)
        self.assertEqual(public_totals["settleable_units_published"], published_units)
        self.assertTrue(public_totals["privacy"]["exact_amounts_hidden"])

        audit_totals = get_json(f"{hub_base}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker, 'audit': '1'})}")
        self.assertIn(str(exact_units), json.dumps(audit_totals, sort_keys=True))
        self.assertEqual(audit_totals["settleable_units_exact"], exact_units)
        self.assertEqual(audit_totals["settleable_units_published"], published_units)
        self.assertEqual(audit_totals["settleable_dust_units"], 123)

        batch = post_json(
            f"{hub_base}/api/hub/v1/workers/settlements/batches",
            {"worker_node_id": worker, "claim_ids": [claim_id], "idempotency_key": "phase6-privacy-batch"},
        )
        self.assertEqual(batch["total_credits_published"], published_units)
        self.assertEqual(batch["dust_credits"], 123)


    def test_request_status_sanitizes_legacy_exact_payout_queue_metadata(self) -> None:
        exact_units = 5_500_123
        published_units = 5_500_000
        record = HubRequestRecord(
            request_id="hub_legacy_privacy",
            client_node_id="requester",
            model="mock-fast-chat",
            state="completed",
            response={
                "content": "answer",
                "provider": "hub",
                "model": "mock-fast-chat",
                "metadata": {
                    "hub": {
                        "payout_queue": {
                            "pending_count": 1,
                            "balances": {"worker": exact_units},
                            "counts": {"worker": 1},
                            "recent": [
                                {
                                    "payout_id": "payout_legacy",
                                    "kind": "hub_worker_payout_queued",
                                    "node_id": "worker",
                                    "credits": exact_units,
                                    "memo": "hub worker-pull request hub_secret",
                                    "request_id": "hub_secret",
                                    "created_at": "2026-05-28T00:00:00+00:00",
                                }
                            ],
                            "settlement": "batched-worker-claim",
                        }
                    }
                },
            },
        )

        status = HubRequestStatus.from_record(record).as_dict()
        payout_queue = status["response"]["metadata"]["hub"]["payout_queue"]
        rendered = json.dumps(payout_queue, sort_keys=True)

        self.assertNotIn(str(exact_units), rendered)
        self.assertIn(str(published_units), rendered)
        self.assertEqual(payout_queue["balances"]["worker"], published_units)
        self.assertEqual(payout_queue["recent"][0]["credits"], published_units)
        self.assertEqual(payout_queue["recent"][0]["request_id"], "")
        self.assertEqual(payout_queue["recent"][0]["memo"], "privacy-redacted")
        self.assertTrue(payout_queue["privacy"]["exact_amounts_hidden"])


    def test_worker_claim_api_records_idempotent_claim(self) -> None:
        hub, hub_base = self._start_hub()
        earning = hub.credit_ledger.record_worker_earning(worker_node_id="API Worker", request_id="api-req-1", credits=21)
        earning_id = earning["worker_earning"]["earning_id"]

        query = urlencode({"worker_node_id": "api-worker"})
        before = get_json(f"{hub_base}/api/hub/v1/workers/claims?{query}")
        self.assertEqual(before["claimable_units"], 21)
        self.assertEqual(before["claimable_earning_ids"], [earning_id])

        claimed = post_json(
            f"{hub_base}/api/hub/v1/workers/claims",
            {
                "worker_node_id": "api-worker",
                "earning_ids": [earning_id],
                "claim_credits": 21,
                "idempotency_key": "api-claim-1",
                "memo": "api test claim",
                "metadata": {"test": True},
            },
        )
        self.assertTrue(claimed["ok"])
        self.assertEqual(claimed["claimed_credits"], 21)
        self.assertEqual(claimed["worker_claim_totals"]["claimable_units"], 0)

        duplicate = post_json(
            f"{hub_base}/api/hub/v1/workers/claims",
            {"worker_node_id": "api-worker", "idempotency_key": "api-claim-1"},
        )
        self.assertTrue(duplicate["idempotent"])
        self.assertEqual(duplicate["claimed_credits"], 21)

        missing_worker = post_json(
            f"{hub_base}/api/hub/v1/workers/claims",
            {"idempotency_key": "missing-worker"},
            allow_error=True,
        )
        self.assertEqual(missing_worker["_http_status"], 400)
        self.assertIn("worker_node_id is required", missing_worker["error"])

    def test_worker_pull_completion_can_be_claimed_once(self) -> None:
        hub, hub_base = self._start_hub(credits_per_request=5)
        requester = "phase4-worker-settlement-requester-0-test"
        worker = "paid-mock-worker-phase4-0-test"
        hub.credit_ledger.issue(account_id=requester, credits=100_000_000, memo="phase4 worker settlement test")

        registered = post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": worker,
                "endpoint": "http://127.0.0.1:1",
                "model": "mock-fast-chat",
                "models": ["mock-fast-chat"],
                "credits_per_request": 5_500_000,
                "capabilities": {"provider": "mock", "worker_pull_v0": True},
            },
        )
        self.assertTrue(registered["ok"])
        self.assertNotEqual(registered["worker"]["node_id"], "paid-mock-worker-01")

        submitted = post_json(
            f"{hub_base}/api/hub/v1/requests",
            {
                "account_id": requester,
                "client_node_id": requester,
                "model": "mock-fast-chat",
                "prompt": "phase4 worker settlement",
                "max_credits": 6_000_000,
                "execution_mode": "worker_pull_v0",
                "metadata": {"worker_pull_v0": True, "mock_provider_config": {"answer": "phase4 answer"}},
                "idempotency_key": "phase4-worker-settlement-test",
            },
        )["request"]
        self.assertEqual(submitted["state"], "queued")

        lease = post_json(f"{hub_base}/api/hub/v1/workers/poll", {"worker_node_id": worker})["lease"]
        completed = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": worker,
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {"content": "phase4 answer", "provider": "mock-worker", "model": "mock-fast-chat"},
                },
            },
        )["request"]
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["charged_credits"], 5_500_000)
        self.assertTrue(completed["worker_earning_id"])

        before = get_json(f"{hub_base}/api/hub/v1/workers/claims?{urlencode({'worker_node_id': worker})}")
        self.assertEqual(before["claimable_units"], 5_500_000)
        self.assertEqual(before["claimable_earning_ids"], [completed["worker_earning_id"]])

        claimed = post_json(
            f"{hub_base}/api/hub/v1/workers/claims",
            {
                "worker_node_id": worker,
                "idempotency_key": "phase4-worker-claim-test",
                "memo": "phase4 worker claim test",
                "metadata": {"phase4_worker_settlement": True},
            },
        )
        self.assertEqual(claimed["claimed_credits"], 5_500_000)
        self.assertEqual(claimed["worker_claim_totals"]["claimable_units"], 0)

        duplicate_new_key = post_json(
            f"{hub_base}/api/hub/v1/workers/claims",
            {"worker_node_id": worker, "idempotency_key": "phase4-worker-claim-test-new-key"},
        )
        self.assertEqual(duplicate_new_key["claimed_credits"], 0)
        self.assertEqual(duplicate_new_key["claimed_count"], 0)


if __name__ == "__main__":
    unittest.main()
