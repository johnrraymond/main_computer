from __future__ import annotations

import json
import tempfile
import threading
import unittest
from collections.abc import Sequence
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer, HubWorkerHttpServer
from main_computer.models import ChatMessage, ChatResponse


def post_json(url: str, payload: dict, *, timeout: float = 5.0) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, *, timeout: float = 5.0) -> dict:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class PaidMockRequestExecutionTests(unittest.TestCase):
    def _start_server(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return thread

    def test_paid_v1_request_creates_hold_charge_worker_earning_and_receipt(self) -> None:
        worker_calls: list[list[ChatMessage]] = []

        def mock_worker_chat(messages: Sequence[ChatMessage]) -> ChatResponse:
            worker_calls.append(list(messages))
            return ChatResponse(
                content="mock paid worker response",
                provider="mock-worker",
                model="mock-fast-chat",
                metadata={"mock": True},
            )

        with tempfile.TemporaryDirectory() as hub_tmp, tempfile.TemporaryDirectory() as worker_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="mock-fast-chat",
                hub_root=Path(hub_tmp) / "hub-runtime",
                hub_credits_per_request=5,
            )
            worker_config = MainComputerConfig(
                workspace=Path(worker_tmp),
                provider="mock",
                model="mock-fast-chat",
                hub_worker_node_id="Paid Mock Worker 01",
                hub_credits_per_request=7,
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            worker = HubWorkerHttpServer(("127.0.0.1", 0), worker_config, mock_worker_chat, verbose=False)

            hub.credit_ledger.issue(
                account_id="paid-mock-requester",
                credits=100,
                memo="test funded requester",
                owner_address="0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
            )

            hub_thread = self._start_server(hub)
            worker_thread = self._start_server(worker)
            try:
                hub_base = f"http://127.0.0.1:{hub.server_port}"
                worker_base = f"http://127.0.0.1:{worker.server_port}"

                registered = post_json(
                    f"{hub_base}/api/hub/v1/workers/register",
                    {
                        "node_id": "Paid Mock Worker 01",
                        "endpoint": worker_base,
                        "model": "mock-fast-chat",
                        "models": ["mock-fast-chat"],
                        "credits_per_request": 7,
                        "capabilities": {"provider": "mock"},
                    },
                )
                self.assertTrue(registered["ok"])
                self.assertEqual(registered["worker"]["node_id"], "paid-mock-worker-01")

                quote = post_json(
                    f"{hub_base}/api/hub/v1/requests/quote",
                    {
                        "account_id": "paid-mock-requester",
                        "model": "mock-fast-chat",
                        "prompt": "hello paid path",
                        "max_credits": 20,
                    },
                )
                self.assertEqual(quote["quote"]["estimated_credits"], 5)
                self.assertEqual(quote["quote"]["max_credits"], 20)

                payload = {
                    "account_id": "paid-mock-requester",
                    "client_node_id": "paid-mock-requester",
                    "model": "mock-fast-chat",
                    "prompt": "hello paid path",
                    "max_credits": 20,
                    "worker_node_id": "paid-mock-worker-01",
                    "idempotency_key": "paid-mock-test-key-01",
                }
                submitted = post_json(f"{hub_base}/api/hub/v1/requests", payload)
                status = submitted["request"]

                self.assertEqual(status["state"], "completed")
                self.assertEqual(status["selected_worker_node_id"], "paid-mock-worker-01")
                self.assertEqual(status["account_id"], "paid-mock-requester")
                self.assertEqual(status["max_credits"], 20)
                self.assertTrue(status["hold_id"])
                self.assertTrue(status["charge_id"])
                self.assertEqual(status["charged_credits"], 7)
                self.assertEqual(status["released_credits"], 13)
                self.assertTrue(status["worker_earning_id"])
                self.assertEqual(status["receipt"]["charged_credits"], 7)
                self.assertEqual(status["response"]["content"], "mock paid worker response")
                self.assertEqual(status["response"]["metadata"]["hub"]["payment"]["charged_credits"], 7)
                self.assertEqual(worker_calls[0][-1].content, "hello paid path")

                account_query = urlencode({"account_id": "paid-mock-requester"})
                balance = get_json(f"{hub_base}/api/hub/v1/credits/balance?{account_query}")
                self.assertEqual(balance["account"]["available_credits"], 93)
                self.assertEqual(balance["account"]["held_credits"], 0)
                self.assertEqual(balance["account"]["spent_credits"], 7)

                hold_query = urlencode({"account_id": "paid-mock-requester", "request_id": status["request_id"]})
                holds = get_json(f"{hub_base}/api/hub/v1/credits/holds?{hold_query}")
                self.assertEqual(holds["hold_count"], 1)
                self.assertEqual(holds["holds"][0]["status"], "charged")
                self.assertEqual(holds["holds"][0]["credits"], 20)

                charges = get_json(f"{hub_base}/api/hub/v1/requests/{status['request_id']}/charges")
                self.assertEqual(charges["charge_count"], 1)
                self.assertEqual(charges["charges"][0]["charged_credits"], 7)
                self.assertEqual(charges["charges"][0]["released_credits"], 13)

                earning_query = urlencode({"worker_node_id": "paid-mock-worker-01", "request_id": status["request_id"]})
                earnings = get_json(f"{hub_base}/api/hub/v1/credits/worker-earnings?{earning_query}")
                self.assertEqual(earnings["worker_earning_count"], 1)
                self.assertEqual(earnings["worker_earnings"][0]["credits"], 7)

                duplicate = post_json(f"{hub_base}/api/hub/v1/requests", payload)
                self.assertEqual(duplicate["request"]["request_id"], status["request_id"])
                self.assertEqual(len(worker_calls), 1)

                duplicate_charges = get_json(f"{hub_base}/api/hub/v1/requests/{status['request_id']}/charges")
                self.assertEqual(duplicate_charges["charge_count"], 1)
            finally:
                hub.shutdown()
                worker.shutdown()
                hub_thread.join(timeout=5)
                worker_thread.join(timeout=5)
                hub.server_close()
                worker.server_close()

    def test_unfunded_paid_request_is_rejected_before_worker_dispatch(self) -> None:
        worker_calls: list[list[ChatMessage]] = []

        def mock_worker_chat(messages: Sequence[ChatMessage]) -> ChatResponse:
            worker_calls.append(list(messages))
            return ChatResponse(content="should not run", provider="mock-worker", model="mock-fast-chat")

        with tempfile.TemporaryDirectory() as hub_tmp, tempfile.TemporaryDirectory() as worker_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="mock-fast-chat",
                hub_root=Path(hub_tmp) / "hub-runtime",
                hub_credits_per_request=5,
            )
            worker_config = MainComputerConfig(
                workspace=Path(worker_tmp),
                provider="mock",
                model="mock-fast-chat",
                hub_worker_node_id="Paid Mock Worker 02",
                hub_credits_per_request=5,
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            worker = HubWorkerHttpServer(("127.0.0.1", 0), worker_config, mock_worker_chat, verbose=False)
            hub_thread = self._start_server(hub)
            worker_thread = self._start_server(worker)
            try:
                hub_base = f"http://127.0.0.1:{hub.server_port}"
                worker_base = f"http://127.0.0.1:{worker.server_port}"

                post_json(
                    f"{hub_base}/api/hub/v1/workers/register",
                    {
                        "node_id": "Paid Mock Worker 02",
                        "endpoint": worker_base,
                        "model": "mock-fast-chat",
                        "credits_per_request": 5,
                    },
                )

                request = Request(
                    f"{hub_base}/api/hub/v1/requests",
                    data=json.dumps(
                        {
                            "account_id": "unfunded-requester",
                            "client_node_id": "unfunded-requester",
                            "model": "mock-fast-chat",
                            "prompt": "do not dispatch",
                            "max_credits": 20,
                            "worker_node_id": "paid-mock-worker-02",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(request, timeout=5)
                self.assertEqual(ctx.exception.code, 400)
                body = json.loads(ctx.exception.read().decode("utf-8"))
                self.assertIn("Insufficient Compute Credits", body["error"])
                self.assertEqual(worker_calls, [])

                holds = get_json(f"{hub_base}/api/hub/v1/credits/holds?account_id=unfunded-requester")
                self.assertEqual(holds["hold_count"], 0)
            finally:
                hub.shutdown()
                worker.shutdown()
                hub_thread.join(timeout=5)
                worker_thread.join(timeout=5)
                hub.server_close()
                worker.server_close()


if __name__ == "__main__":
    unittest.main()
