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
        body = exc.read().decode("utf-8")
        if not allow_error:
            raise
        payload = json.loads(body) if body else {}
        payload["_http_status"] = exc.code
        return payload


def get_json(url: str, *, timeout: float = 5.0) -> dict:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class Phase9MarketBackedPaidAIRequestTests(unittest.TestCase):
    def _start_hub(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config = MainComputerConfig(
            workspace=Path(tmp.name),
            model="mock-ai-model-phase9",
            hub_root=Path(tmp.name) / "hub-runtime",
            hub_credits_per_request=111,
        )
        hub = HubHttpServer(("127.0.0.1", 0), config, verbose=False)
        thread = threading.Thread(target=hub.serve_forever, daemon=True)
        thread.start()

        def cleanup() -> None:
            hub.shutdown()
            thread.join(timeout=5)
            hub.server_close()

        self.addCleanup(cleanup)
        return hub, f"http://127.0.0.1:{hub.server_port}"

    def test_market_quote_hold_lease_completion_and_replays_use_selected_offer_price(self) -> None:
        hub, hub_base = self._start_hub()
        account_id = "phase9-requester-local-001"
        worker_node_id = "paid-ai-seller-worker-phase9-local-001"
        other_worker_node_id = "paid-ai-seller-worker-phase9-local-999"
        model = "mock-ai-model-phase9"
        quoted_price = 5_500_123

        hub.credit_ledger.issue(
            account_id=account_id,
            credits=100_000_000,
            memo="phase9 funded requester",
            owner_address="0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
        )

        unpriced = post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": "phase9-unpriced-worker",
                "endpoint": "http://127.0.0.1:1",
                "model": "phase9-unpriced-model",
                "models": ["phase9-unpriced-model"],
                "execution_mode": "worker_pull_v0",
                "pricing": {"pricing_type": "unpriced_v0"},
                "capabilities": {"provider": "mock", "worker_pull_v0": True},
            },
        )
        self.assertTrue(unpriced["ok"])
        self.assertNotIn("offer", unpriced["worker"])

        unpriced_quote = post_json(
            f"{hub_base}/api/hub/v1/requests/quote",
            {
                "account_id": account_id,
                "model": "phase9-unpriced-model",
                "prompt": "quote unpriced",
                "max_credits": quoted_price,
                "execution_mode": "worker_pull_v0",
                "pricing_mode": "market_offer_fixed_per_call_v0",
            },
            allow_error=True,
        )
        self.assertEqual(unpriced_quote["_http_status"], 400)
        self.assertIn("priced", unpriced_quote["error"])

        expensive = post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": other_worker_node_id,
                "endpoint": "http://127.0.0.1:1",
                "model": model,
                "models": [model],
                "credits_per_request": 6_000_000,
                "execution_mode": "worker_pull_v0",
                "pricing": {
                    "pricing_type": "fixed_per_call_v0",
                    "credits_per_request": 6_000_000,
                    "unit": "compute_credit",
                },
                "capabilities": {"provider": "mock", "worker_pull_v0": True},
            },
        )
        self.assertTrue(expensive["worker"]["offer"]["offer_id"].startswith("offer_"))

        registered = post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": worker_node_id,
                "endpoint": "http://127.0.0.1:1",
                "model": model,
                "models": [model],
                "credits_per_request": quoted_price,
                "execution_mode": "worker_pull_v0",
                "pricing": {
                    "pricing_type": "fixed_per_call_v0",
                    "credits_per_request": quoted_price,
                    "unit": "compute_credit",
                },
                "capabilities": {"provider": "mock", "worker_pull_v0": True},
            },
        )
        self.assertTrue(registered["ok"])
        self.assertEqual(registered["worker"]["node_id"], worker_node_id)
        self.assertEqual(registered["worker"]["offer"]["credits_per_request"], quoted_price)

        quote_payload = {
            "account_id": account_id,
            "model": model,
            "prompt": "Say hello from Phase 9.",
            "max_credits": quoted_price,
            "execution_mode": "worker_pull_v0",
            "pricing_mode": "market_offer_fixed_per_call_v0",
            "idempotency_key": "phase9-quote-local-001",
        }
        quote = post_json(f"{hub_base}/api/hub/v1/requests/quote", quote_payload)
        quote_replay = post_json(f"{hub_base}/api/hub/v1/requests/quote", quote_payload)
        self.assertEqual(quote["quote"]["quote_id"], quote_replay["quote"]["quote_id"])
        self.assertTrue(quote_replay["idempotent"])
        self.assertEqual(quote["quote"]["quoted_credits"], quoted_price)
        self.assertEqual(quote["quote"]["selected_offer"]["worker_node_id"], worker_node_id)
        self.assertEqual(quote["quote"]["selected_offer_price_source"], "worker_registration")

        over_budget = post_json(
            f"{hub_base}/api/hub/v1/requests/quote",
            {
                **quote_payload,
                "max_credits": quoted_price - 1,
                "idempotency_key": "phase9-over-budget-quote",
            },
            allow_error=True,
        )
        self.assertEqual(over_budget["_http_status"], 400)
        self.assertIn("exceeds requester max_credits", over_budget["error"])

        request_payload = {
            "account_id": account_id,
            "client_node_id": account_id,
            "quote_id": quote["quote"]["quote_id"],
            "model": model,
            "prompt": "Return a Phase 9 response.",
            "max_credits": quoted_price,
            "execution_mode": "worker_pull_v0",
            "pricing_mode": "market_offer_fixed_per_call_v0",
            "metadata": {"worker_pull_v0": True},
            "idempotency_key": "phase9-request-local-001",
        }
        submitted = post_json(f"{hub_base}/api/hub/v1/requests", request_payload)["request"]
        duplicate_submitted = post_json(f"{hub_base}/api/hub/v1/requests", request_payload)["request"]
        self.assertEqual(submitted["request_id"], duplicate_submitted["request_id"])
        self.assertEqual(submitted["state"], "queued")
        self.assertEqual(submitted["pricing"]["quoted_credits"], quoted_price)
        self.assertEqual(submitted["pricing"]["held_credits"], quoted_price)
        self.assertEqual(submitted["selected_offer"]["worker_node_id"], worker_node_id)

        # Mutating the worker's advertised price after request acceptance must not
        # change the in-flight quote/hold/charge amount.
        post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": worker_node_id,
                "endpoint": "http://127.0.0.1:1",
                "model": model,
                "models": [model],
                "credits_per_request": 9_999_999,
                "execution_mode": "worker_pull_v0",
                "pricing": {
                    "pricing_type": "fixed_per_call_v0",
                    "credits_per_request": 9_999_999,
                    "unit": "compute_credit",
                },
                "capabilities": {"provider": "mock", "worker_pull_v0": True},
            },
        )

        holds = get_json(
            f"{hub_base}/api/hub/v1/credits/holds?"
            + urlencode({"account_id": account_id, "request_id": submitted["request_id"]})
        )
        self.assertEqual(holds["hold_count"], 1)
        self.assertEqual(holds["holds"][0]["credits"], quoted_price)
        self.assertEqual(holds["holds"][0]["status"], "held")

        unselected_poll = post_json(f"{hub_base}/api/hub/v1/workers/poll", {"worker_node_id": other_worker_node_id})
        self.assertIsNone(unselected_poll["lease"])

        polled = post_json(f"{hub_base}/api/hub/v1/workers/poll", {"worker_node_id": worker_node_id})
        lease = polled["lease"]
        self.assertIsInstance(lease, dict)
        self.assertEqual(lease["request_id"], submitted["request_id"])
        self.assertEqual(lease["pricing"]["quoted_credits"], quoted_price)
        self.assertEqual(lease["pricing"]["worker_earning_credits"], quoted_price)
        self.assertEqual(lease["selected_offer"]["worker_node_id"], worker_node_id)
        self.assertNotIn("account_id", lease)
        self.assertNotIn("ledger", lease)

        wrong_worker_complete = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": other_worker_node_id,
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {"status": "success", "response": {"content": "bad", "model": model}},
            },
            allow_error=True,
        )
        self.assertEqual(wrong_worker_complete["_http_status"], 400)

        completed = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": worker_node_id,
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {
                        "content": "Phase 9 response",
                        "provider": "mock-worker",
                        "model": model,
                    },
                },
            },
        )["request"]
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["charged_credits"], quoted_price)
        self.assertEqual(completed["released_credits"], 0)
        self.assertEqual(completed["pricing"]["charged_credits"], quoted_price)
        self.assertEqual(completed["receipt"]["quote_id"], quote["quote"]["quote_id"])
        self.assertEqual(completed["receipt"]["offer_id"], quote["quote"]["selected_offer"]["offer_id"])
        self.assertEqual(completed["response"]["metadata"]["hub"]["pricing"]["worker_earning_credits"], quoted_price)

        replay_completion = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": worker_node_id,
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {"content": "again", "provider": "mock-worker", "model": model},
                },
            },
        )
        self.assertTrue(replay_completion["ok"])
        self.assertTrue(replay_completion["idempotent"])
        self.assertEqual(replay_completion["duplicate_completion_additional_charge"], 0)

        charges = get_json(f"{hub_base}/api/hub/v1/requests/{lease['request_id']}/charges")
        self.assertEqual(charges["charge_count"], 1)
        self.assertEqual(charges["charges"][0]["charged_credits"], quoted_price)
        self.assertEqual(charges["charges"][0]["released_credits"], 0)

        earnings = get_json(
            f"{hub_base}/api/hub/v1/credits/worker-earnings?"
            + urlencode({"worker_node_id": worker_node_id, "request_id": lease["request_id"]})
        )
        self.assertEqual(earnings["worker_earning_count"], 1)
        self.assertEqual(earnings["worker_earnings"][0]["credits"], quoted_price)

    def test_market_quote_filters_workers_by_requested_ring_threshold(self) -> None:
        _hub, hub_base = self._start_hub()
        account_id = "phase9-ring-requester-local-001"
        model = "mock-ai-model-phase9-ring"

        ring3 = post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": "phase9-ring3-cheap-worker",
                "endpoint": "http://127.0.0.1:1",
                "model": model,
                "models": [model],
                "assigned_ring": 3,
                "credits_per_request": 1,
                "execution_mode": "worker_pull_v0",
                "pricing": {
                    "pricing_type": "fixed_per_call_v0",
                    "credits_per_request": 1,
                    "unit": "compute_credit",
                },
                "capabilities": {"provider": "mock", "worker_pull_v0": True},
            },
        )
        self.assertTrue(ring3["ok"])
        self.assertEqual(ring3["worker"]["offer"]["assigned_ring"], 3)

        rejected = post_json(
            f"{hub_base}/api/hub/v1/requests/quote",
            {
                "account_id": account_id,
                "model": model,
                "prompt": "ring 2 should not use ring 3",
                "max_price_credits": 2,
                "requested_ring": 2,
                "execution_mode": "worker_pull_v0",
                "pricing_mode": "market_offer_fixed_per_call_v0",
                "idempotency_key": "phase9-ring-threshold-reject",
            },
            allow_error=True,
        )
        self.assertEqual(rejected["_http_status"], 400)
        self.assertIn("requested_ring <= 2", rejected["error"])

        ring1 = post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": "phase9-ring1-price2-worker",
                "endpoint": "http://127.0.0.1:1",
                "model": model,
                "models": [model],
                "assigned_ring": 1,
                "credits_per_request": 2,
                "execution_mode": "worker_pull_v0",
                "pricing": {
                    "pricing_type": "fixed_per_call_v0",
                    "credits_per_request": 2,
                    "unit": "compute_credit",
                },
                "capabilities": {"provider": "mock", "worker_pull_v0": True},
            },
        )
        self.assertTrue(ring1["ok"])

        quote = post_json(
            f"{hub_base}/api/hub/v1/requests/quote",
            {
                "account_id": account_id,
                "model": model,
                "prompt": "ring 2 should use the best eligible ring <= 2 worker",
                "max_price_credits": 2,
                "requested_ring": 2,
                "execution_mode": "worker_pull_v0",
                "pricing_mode": "market_offer_fixed_per_call_v0",
                "idempotency_key": "phase9-ring-threshold-accept",
            },
        )["quote"]
        self.assertEqual(quote["requested_ring"], 2)
        self.assertEqual(quote["quoted_credits"], 2)
        self.assertEqual(quote["selected_offer"]["worker_node_id"], "phase9-ring1-price2-worker")
        self.assertEqual(quote["selected_offer"]["assigned_ring"], 1)

        ring3_quote = post_json(
            f"{hub_base}/api/hub/v1/requests/quote",
            {
                "account_id": account_id,
                "model": model,
                "prompt": "ring 3 can use ring 3 when it is cheapest",
                "max_price_credits": 2,
                "requested_ring": 3,
                "execution_mode": "worker_pull_v0",
                "pricing_mode": "market_offer_fixed_per_call_v0",
                "idempotency_key": "phase9-ring-threshold-ring3",
            },
        )["quote"]
        self.assertEqual(ring3_quote["selected_offer"]["worker_node_id"], "phase9-ring3-cheap-worker")
        self.assertEqual(ring3_quote["selected_offer"]["assigned_ring"], 3)


    def test_market_quote_balances_equivalent_workers_across_active_assignments(self) -> None:
        hub, hub_base = self._start_hub()
        account_id = "phase9-balance-requester-local-001"
        model = "mock-ai-model-phase9-balance"
        worker_ids = [
            "phase9-balance-worker-001",
            "phase9-balance-worker-002",
            "phase9-balance-worker-003",
        ]

        hub.credit_ledger.issue(
            account_id=account_id,
            credits=100,
            memo="phase9 balanced requester",
            owner_address="0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
        )
        for worker_id in worker_ids:
            registered = post_json(
                f"{hub_base}/api/hub/v1/workers/register",
                {
                    "node_id": worker_id,
                    "endpoint": "http://127.0.0.1:1",
                    "model": model,
                    "models": [model],
                    "assigned_ring": 1,
                    "credits_per_request": 2,
                    "execution_mode": "worker_pull_v0",
                    "pricing": {
                        "pricing_type": "fixed_per_call_v0",
                        "credits_per_request": 2,
                        "unit": "compute_credit",
                    },
                    "capabilities": {"provider": "mock", "worker_pull_v0": True},
                },
            )
            self.assertTrue(registered["ok"])

        selected: list[str] = []
        for index in range(6):
            quote_payload = {
                "account_id": account_id,
                "client_node_id": account_id,
                "model": model,
                "prompt": f"balanced request {index + 1}",
                "max_price_credits": 2,
                "requested_ring": 2,
                "execution_mode": "worker_pull_v0",
                "pricing_mode": "market_offer_fixed_per_call_v0",
                "idempotency_key": f"phase9-balance-quote-{index + 1}",
            }
            quote = post_json(f"{hub_base}/api/hub/v1/requests/quote", quote_payload)["quote"]
            worker_id = quote["selected_offer"]["worker_node_id"]
            selected.append(worker_id)

            submitted = post_json(
                f"{hub_base}/api/hub/v1/requests",
                {
                    **quote_payload,
                    "quote_id": quote["quote_id"],
                    "metadata": {"worker_pull_v0": True, "requested_ring": 2},
                    "idempotency_key": f"phase9-balance-submit-{index + 1}",
                },
            )["request"]
            self.assertEqual(submitted["state"], "queued")
            self.assertEqual(submitted["selected_offer"]["worker_node_id"], worker_id)

        self.assertEqual(set(selected), set(worker_ids))
        self.assertLessEqual(max(selected.count(worker_id) for worker_id in worker_ids), 2)


if __name__ == "__main__":
    unittest.main()
