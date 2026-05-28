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
