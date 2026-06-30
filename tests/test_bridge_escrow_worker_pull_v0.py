from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
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
        if not allow_error:
            raise
        body = json.loads(exc.read().decode("utf-8"))
        body["_http_status"] = exc.code
        return body


def get_json(url: str, *, timeout: float = 5.0) -> dict:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class BridgeEscrowWorkerPullV0Tests(unittest.TestCase):
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

    def test_worker_pull_happy_path_and_duplicate_result_do_not_double_charge(self) -> None:
        hub, hub_base = self._start_hub(credits_per_request=5)
        hub.credit_ledger.issue(
            account_id="bridge-escrow-requester-0",
            credits=100_000_000,
            memo="worker pull test funded requester",
            owner_address="0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
        )

        registered = post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": "Pull Worker 01",
                "endpoint": "http://127.0.0.1:1",
                "model": "mock-fast-chat",
                "models": ["mock-fast-chat"],
                "credits_per_request": 5_500_000,
                "capabilities": {"provider": "mock", "worker_pull_v0": True},
            },
        )
        self.assertTrue(registered["ok"])
        self.assertEqual(registered["worker"]["node_id"], "pull-worker-01")

        heartbeat = post_json(
            f"{hub_base}/api/hub/v1/workers/heartbeat",
            {"worker_node_id": "pull-worker-01", "status": "available", "model": "mock-fast-chat"},
        )
        self.assertTrue(heartbeat["ok"])

        submitted = post_json(
            f"{hub_base}/api/hub/v1/requests",
            {
                "account_id": "bridge-escrow-requester-0",
                "client_node_id": "bridge-escrow-requester-0",
                "model": "mock-fast-chat",
                "prompt": "hello worker pull",
                "max_credits": 6_000_000,
                "execution_mode": "worker_pull_v0",
                "metadata": {
                    "worker_pull_v0": True,
                    "mock_provider_config": {"answer": "worker pull answer"},
                },
                "idempotency_key": "worker-pull-test-01",
            },
        )
        request_status = submitted["request"]
        self.assertEqual(request_status["state"], "queued")
        self.assertTrue(request_status["hold_id"])
        self.assertFalse(request_status["charge_id"])

        events = get_json(f"{hub_base}/api/hub/v1/requests/{request_status['request_id']}/events")["events"]
        event_types = [event["type"] for event in events]
        self.assertLess(event_types.index("payment.hold.created"), event_types.index("request.queued"))

        polled = post_json(f"{hub_base}/api/hub/v1/workers/poll", {"worker_node_id": "pull-worker-01"})
        lease = polled["lease"]
        self.assertIsInstance(lease, dict)
        self.assertEqual(lease["request_id"], request_status["request_id"])
        self.assertEqual(lease["model"], "mock-fast-chat")
        self.assertEqual(lease["messages"][-1]["content"], "hello worker pull")
        self.assertNotIn("account_id", lease)
        self.assertNotIn("requester_wallet", lease)
        self.assertNotIn("balance", lease)
        self.assertNotIn("ledger", lease)

        nothing = post_json(f"{hub_base}/api/hub/v1/workers/poll", {"worker_node_id": "pull-worker-01"})
        self.assertIsNone(nothing["lease"])

        wrong = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": "pull-worker-01",
                "request_id": lease["request_id"],
                "lease_id": "lease_wrong",
                "result": {
                    "status": "success",
                    "response": {"content": "bad", "provider": "mock-worker", "model": "mock-fast-chat"},
                },
            },
            allow_error=True,
        )
        self.assertEqual(wrong["_http_status"], 400)

        completed = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": "pull-worker-01",
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {
                        "content": "worker pull answer",
                        "provider": "mock-worker",
                        "model": "mock-fast-chat",
                        "metadata": {"mock": True},
                    },
                },
            },
        )["request"]
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["charged_credits"], 5_500_000)
        self.assertEqual(completed["released_credits"], 0)
        self.assertTrue(completed["worker_earning_id"])
        self.assertEqual(completed["response"]["content"], "worker pull answer")
        self.assertEqual(completed["response"]["metadata"]["hub"]["lease_id"], lease["lease_id"])

        duplicate = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": "pull-worker-01",
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {"content": "again", "provider": "mock-worker", "model": "mock-fast-chat"},
                },
            },
        )
        self.assertTrue(duplicate["ok"])
        self.assertTrue(duplicate["idempotent"])
        self.assertEqual(duplicate["duplicate_completion_additional_charge"], 0)

        charges = get_json(f"{hub_base}/api/hub/v1/requests/{lease['request_id']}/charges")
        self.assertEqual(charges["charge_count"], 1)
        self.assertEqual(charges["charges"][0]["charged_credits"], 5_500_000)

    def test_worker_stream_events_reach_requester_before_final_result(self) -> None:
        hub, hub_base = self._start_hub(credits_per_request=5)
        account_id = "requester-stream"
        hub.credit_ledger.issue(account_id=account_id, credits=100, memo="fund streaming requester")

        registered = post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": "Streaming Worker 01",
                "endpoint": "http://127.0.0.1:1",
                "model": "mock-fast-chat",
                "credits_per_request": 5,
                "capabilities": {"provider": "mock", "worker_pull_v0": True},
            },
        )
        self.assertTrue(registered["ok"])

        submitted = post_json(
            f"{hub_base}/api/hub/v1/requests",
            {
                "account_id": account_id,
                "client_node_id": account_id,
                "model": "mock-fast-chat",
                "prompt": "stream before final",
                "max_credits": 10,
                "execution_mode": "worker_pull_v0",
                "metadata": {"worker_pull_v0": True},
                "idempotency_key": "worker-stream-test-01",
            },
        )["request"]
        lease = post_json(
            f"{hub_base}/api/hub/v1/workers/poll",
            {"worker_node_id": "streaming-worker-01", "lease_seconds": 10},
        )["lease"]
        self.assertEqual(lease["request_id"], submitted["request_id"])

        streamed_events: list[dict] = []
        token_seen = threading.Event()
        done_seen = threading.Event()
        reader_errors: list[BaseException] = []

        def read_stream() -> None:
            try:
                with urlopen(
                    f"{hub_base}/api/hub/v1/requests/{lease['request_id']}/stream?timeout_seconds=5&heartbeat_seconds=0.25",
                    timeout=10,
                ) as response:
                    for raw_line in response:
                        if not raw_line.strip():
                            continue
                        event = json.loads(raw_line.decode("utf-8"))
                        streamed_events.append(event)
                        if event.get("event") == "token":
                            token_seen.set()
                        if event.get("event") == "done":
                            done_seen.set()
                            return
            except BaseException as exc:  # pragma: no cover - surfaced by assertions below.
                reader_errors.append(exc)

        reader = threading.Thread(target=read_stream, daemon=True)
        reader.start()
        time.sleep(0.1)

        submitted_stream_event = post_json(
            f"{hub_base}/api/hub/v1/workers/stream-events",
            {
                "worker_node_id": "streaming-worker-01",
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "event": {
                    "event": "token",
                    "sequence": 1,
                    "text": "partial answer",
                    "delta": "partial answer",
                    "content_delta": "partial answer",
                },
            },
        )
        self.assertTrue(submitted_stream_event["ok"])
        self.assertTrue(token_seen.wait(2), streamed_events)
        self.assertFalse(reader_errors)

        live_status = get_json(f"{hub_base}/api/hub/v1/requests/{lease['request_id']}")["request"]
        self.assertEqual(live_status["state"], "running")
        self.assertIsNone(live_status.get("response"))

        completed = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": "streaming-worker-01",
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {
                        "content": "partial answer completed",
                        "provider": "mock-worker",
                        "model": "mock-fast-chat",
                    },
                },
            },
        )["request"]
        self.assertEqual(completed["state"], "completed")
        self.assertTrue(done_seen.wait(2), streamed_events)
        reader.join(2)
        self.assertFalse(reader_errors)

        token_events = [event for event in streamed_events if event.get("event") == "token"]
        self.assertEqual(token_events[-1]["delta"], "partial answer")
        done_index = next(index for index, event in enumerate(streamed_events) if event.get("event") == "done")
        token_index = next(index for index, event in enumerate(streamed_events) if event.get("event") == "token")
        self.assertLess(token_index, done_index)

        stored_events = get_json(f"{hub_base}/api/hub/v1/requests/{lease['request_id']}/events")["events"]
        stored_types = [event["type"] for event in stored_events]
        self.assertLess(stored_types.index("worker.stream.event"), stored_types.index("request.completed"))

    def test_insufficient_funds_request_never_becomes_pollable(self) -> None:
        _hub, hub_base = self._start_hub(credits_per_request=5)
        post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": "Pull Worker 02",
                "endpoint": "http://127.0.0.1:1",
                "model": "mock-fast-chat",
                "credits_per_request": 5,
            },
        )

        rejected = post_json(
            f"{hub_base}/api/hub/v1/requests",
            {
                "account_id": "unfunded-bridge-requester",
                "client_node_id": "unfunded-bridge-requester",
                "model": "mock-fast-chat",
                "prompt": "do not lease",
                "max_credits": 20,
                "execution_mode": "worker_pull_v0",
                "metadata": {"worker_pull_v0": True},
            },
            allow_error=True,
        )
        self.assertEqual(rejected["_http_status"], 400)
        self.assertIn("Insufficient Compute Credits", rejected["error"])

        polled = post_json(f"{hub_base}/api/hub/v1/workers/poll", {"worker_node_id": "pull-worker-02"})
        self.assertIsNone(polled["lease"])

    def test_worker_pull_lease_is_owned_by_worker_instance(self) -> None:
        hub, hub_base = self._start_hub(credits_per_request=5)
        account_id = "requester-worker-instance"
        hub.credit_ledger.issue(account_id=account_id, credits=100, memo="fund worker instance requester")
        for instance_id in ("shared-worker-slot-a", "shared-worker-slot-b"):
            registered = post_json(
                f"{hub_base}/api/hub/v1/workers/register",
                {
                    "node_id": "Shared Worker Node",
                    "worker_instance_id": instance_id,
                    "endpoint": "http://127.0.0.1:1",
                    "model": "mock-fast-chat",
                    "models": ["mock-fast-chat"],
                    "credits_per_request": 5,
                    "capabilities": {"provider": "mock", "worker_pull_v0": True},
                },
            )
            self.assertTrue(registered["ok"])
            self.assertEqual(registered["worker"]["node_id"], "shared-worker-node")
            self.assertEqual(registered["worker"]["worker_instance_id"], instance_id)

        submitted = post_json(
            f"{hub_base}/api/hub/v1/requests",
            {
                "account_id": account_id,
                "client_node_id": account_id,
                "model": "mock-fast-chat",
                "prompt": "instance-owned work",
                "max_credits": 10,
                "execution_mode": "worker_pull_v0",
                "metadata": {"worker_pull_v0": True},
            },
        )["request"]
        selected_instance_id = submitted["selected_worker_instance_id"]
        self.assertEqual(selected_instance_id, "shared-worker-slot-a")

        wrong_poll = post_json(
            f"{hub_base}/api/hub/v1/workers/poll",
            {"worker_node_id": "shared-worker-node", "worker_instance_id": "shared-worker-slot-b"},
        )
        self.assertIsNone(wrong_poll["lease"])

        polled = post_json(
            f"{hub_base}/api/hub/v1/workers/poll",
            {"worker_node_id": "shared-worker-node", "worker_instance_id": selected_instance_id},
        )
        lease = polled["lease"]
        self.assertIsInstance(lease, dict)
        self.assertEqual(lease["worker_node_id"], "shared-worker-node")
        self.assertEqual(lease["worker_instance_id"], selected_instance_id)

        wrong_result = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": "shared-worker-node",
                "worker_instance_id": "shared-worker-slot-b",
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {"content": "wrong slot", "provider": "mock-worker", "model": "mock-fast-chat"},
                },
            },
            allow_error=True,
        )
        self.assertEqual(wrong_result["_http_status"], 400)
        self.assertIn("wrong worker instance", wrong_result["error"])

        completed = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": "shared-worker-node",
                "worker_instance_id": selected_instance_id,
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {"content": "right slot", "provider": "mock-worker", "model": "mock-fast-chat"},
                },
            },
        )["request"]
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["selected_worker_node_id"], "shared-worker-node")
        self.assertEqual(completed["selected_worker_instance_id"], selected_instance_id)
        self.assertEqual(completed["response"]["metadata"]["hub"]["worker_instance_id"], selected_instance_id)

    def test_expired_worker_pull_lease_fails_request_releases_hold_and_rejects_late_result(self) -> None:
        hub, hub_base = self._start_hub(credits_per_request=5)
        hub.credit_ledger.issue(account_id="requester", credits=100, memo="fund")
        post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": "Pull Worker 03",
                "endpoint": "http://127.0.0.1:1",
                "model": "mock-fast-chat",
                "credits_per_request": 5,
            },
        )
        submitted = post_json(
            f"{hub_base}/api/hub/v1/requests",
            {
                "account_id": "requester",
                "client_node_id": "requester",
                "model": "mock-fast-chat",
                "prompt": "expire lease",
                "max_credits": 10,
                "execution_mode": "worker_pull_v0",
                "metadata": {"worker_pull_v0": True},
            },
        )["request"]
        first = post_json(
            f"{hub_base}/api/hub/v1/workers/poll",
            {"worker_node_id": "pull-worker-03", "lease_seconds": 1},
        )["lease"]
        self.assertEqual(first["request_id"], submitted["request_id"])

        time.sleep(1.2)
        expired_result = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": "pull-worker-03",
                "request_id": first["request_id"],
                "lease_id": first["lease_id"],
                "result": {
                    "status": "success",
                    "response": {"content": "late", "provider": "mock-worker", "model": "mock-fast-chat"},
                },
            },
            allow_error=True,
        )
        self.assertEqual(expired_result["_http_status"], 400)

        final_status = get_json(f"{hub_base}/api/hub/v1/requests/{first['request_id']}")["request"]
        self.assertEqual(final_status["state"], "failed")
        self.assertEqual(final_status["terminal_reason"], "worker_lost_timeout")
        self.assertFalse(final_status.get("charge_id"))

        charges = get_json(f"{hub_base}/api/hub/v1/requests/{first['request_id']}/charges")
        self.assertEqual(charges["charge_count"], 0)

        events = get_json(f"{hub_base}/api/hub/v1/requests/{first['request_id']}/events")["events"]
        event_types = [event["type"] for event in events]
        self.assertIn("payment.hold.released", event_types)
        self.assertIn("request.failed", event_types)

        second = post_json(f"{hub_base}/api/hub/v1/workers/poll", {"worker_node_id": "pull-worker-03"})["lease"]
        self.assertIsNone(second)


    def test_completed_worker_pull_result_is_retained_for_requester_pickup(self) -> None:
        _hub, hub_base = self._start_hub(credits_per_request=5)
        account_id = "requester-pickup"
        _hub.credit_ledger.issue(account_id=account_id, credits=100, memo="fund pickup requester")
        post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": "Pickup Worker 01",
                "endpoint": "http://127.0.0.1:1",
                "model": "mock-fast-chat",
                "credits_per_request": 5,
            },
        )
        submitted = post_json(
            f"{hub_base}/api/hub/v1/requests",
            {
                "account_id": account_id,
                "client_node_id": account_id,
                "model": "mock-fast-chat",
                "prompt": "complete while requester is away",
                "max_credits": 10,
                "execution_mode": "worker_pull_v0",
                "metadata": {
                    "worker_pull_v0": True,
                    "requester_result_retention_window_seconds": 60,
                },
            },
        )["request"]
        lease = post_json(
            f"{hub_base}/api/hub/v1/workers/poll",
            {"worker_node_id": "pickup-worker-01", "lease_seconds": 10},
        )["lease"]

        completed = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": "pickup-worker-01",
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {
                        "content": "stored result",
                        "provider": "mock-worker",
                        "model": "mock-fast-chat",
                    },
                },
            },
        )["request"]

        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["request_id"], submitted["request_id"])
        retention = completed["response"]["metadata"]["hub"]["result_retention"]
        self.assertTrue(retention["retained"])
        self.assertEqual(retention["window_seconds"], 60)

        pickup = get_json(f"{hub_base}/api/hub/v1/requests/{lease['request_id']}/result?account_id={account_id}")
        self.assertTrue(pickup["ok"])
        self.assertTrue(pickup["result_available"])
        self.assertTrue(pickup["retained"])
        self.assertFalse(pickup["expired"])
        self.assertEqual(pickup["result"]["content"], "stored result")
        self.assertEqual(pickup["request"]["state"], "completed")

        charges = get_json(f"{hub_base}/api/hub/v1/requests/{lease['request_id']}/charges")
        self.assertEqual(charges["charge_count"], 1)



if __name__ == "__main__":
    unittest.main()
