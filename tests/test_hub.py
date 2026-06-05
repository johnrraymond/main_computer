from __future__ import annotations

import json
import tempfile
import threading
import unittest
from collections.abc import Sequence
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer, HubRegistry, HubWorkerHttpServer
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.hub_plex_models import HubAIRequest
from main_computer.hub_security import (
    decrypt_hub_envelope,
    derive_hub_session_key,
    encrypt_hub_envelope,
    generate_hub_session_keypair,
    hub_transport_is_encrypted_or_loopback,
)
from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers.hub import HubProvider
from main_computer.router import MainComputer


class HubServerTests(unittest.TestCase):
    def _start_server(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return thread

    def test_hub_transport_allows_docker_dev_service_names_only_with_explicit_opt_in(self) -> None:
        self.assertFalse(hub_transport_is_encrypted_or_loopback("http://hub:8770"))
        self.assertFalse(hub_transport_is_encrypted_or_loopback("http://hub-worker:8771"))
        self.assertTrue(
            hub_transport_is_encrypted_or_loopback(
                "http://hub:8770",
                allow_insecure_dev_network=True,
            )
        )
        self.assertTrue(
            hub_transport_is_encrypted_or_loopback(
                "http://hub-worker:8771",
                allow_insecure_dev_network=True,
            )
        )

    def test_hub_transport_still_rejects_public_http_when_dev_network_opted_in(self) -> None:
        self.assertFalse(
            hub_transport_is_encrypted_or_loopback(
                "http://hub.example",
                allow_insecure_dev_network=True,
            )
        )

    def test_hub_registers_worker_dispatches_encrypted_chat_and_queues_energy_credits(self) -> None:
        worker_calls: list[list[ChatMessage]] = []

        def fake_worker_chat(messages: Sequence[ChatMessage]) -> ChatResponse:
            worker_calls.append(list(messages))
            return ChatResponse(content="worker response", provider="fake-worker", model="fake-model")

        with tempfile.TemporaryDirectory() as hub_tmp, tempfile.TemporaryDirectory() as worker_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
                hub_credits_per_request=3,
            )
            worker_config = MainComputerConfig(
                workspace=Path(worker_tmp),
                model="fake-model",
                hub_worker_node_id="GPU Worker 01",
                hub_credits_per_request=3,
            )
            worker = HubWorkerHttpServer(("127.0.0.1", 0), worker_config, fake_worker_chat, verbose=False)
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            worker_thread = self._start_server(worker)
            hub_thread = self._start_server(hub)
            try:
                hub_base = f"http://127.0.0.1:{hub.server_port}"
                worker_base = f"http://127.0.0.1:{worker.server_port}"
                register_request = Request(
                    f"{hub_base}/api/hub/workers/register",
                    data=json.dumps(
                        {
                            "node_id": "GPU Worker 01",
                            "endpoint": worker_base,
                            "model": "fake-model",
                            "credits_per_request": 3,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(register_request, timeout=5) as response:
                    registered = json.loads(response.read().decode("utf-8"))
                self.assertTrue(registered["ok"])
                self.assertEqual(registered["worker"]["node_id"], "gpu-worker-01")

                plaintext_request = Request(
                    f"{hub_base}/api/hub/chat",
                    data=json.dumps(
                        {
                            "client_node_id": "client-01",
                            "model": "fake-model",
                            "messages": [{"role": "user", "content": "hello hub"}],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as plaintext_error:
                    urlopen(plaintext_request, timeout=5)
                self.assertEqual(plaintext_error.exception.code, 400)

                provider = HubProvider(
                    model="fake-model",
                    hub_url=hub_base,
                    timeout_s=5.0,
                    client_node_id="client-01",
                )
                chat = provider.chat([ChatMessage(role="user", content="hello hub")])

                self.assertEqual(chat.content, "worker response")
                self.assertEqual(chat.provider, "hub")
                self.assertEqual(chat.metadata["hub"]["worker_node_id"], "gpu-worker-01")
                self.assertEqual(chat.metadata["hub"]["credits_queued"], 3)
                self.assertEqual(chat.metadata["hub"]["security_mode"], "high-security")
                self.assertTrue(chat.metadata["hub"]["hub_blind"])
                self.assertEqual(worker_calls[0][-1].content, "hello hub")

                with urlopen(f"{hub_base}/api/hub/status", timeout=5) as response:
                    status = json.loads(response.read().decode("utf-8"))
                self.assertEqual(status["energy"]["balances"]["gpu-worker-01"], 0)
                self.assertEqual(status["energy"]["payout_queue"]["balances"]["gpu-worker-01"], 0)
                self.assertTrue(status["energy"]["payout_queue"]["privacy"]["exact_amounts_hidden"])
                self.assertEqual(status["energy"]["payout_queue"]["recent"][-1]["kind"], "hub_worker_payout_queued")
                self.assertEqual(status["energy"]["payout_queue"]["recent"][-1]["credits"], 0)
                self.assertEqual(status["energy"]["payout_queue"]["recent"][-1]["request_id"], "")
                self.assertTrue(status["security"]["high_security_default"])

                with urlopen(f"{hub_base}/api/hub/status?audit=1", timeout=5) as response:
                    audit_status = json.loads(response.read().decode("utf-8"))
                self.assertEqual(audit_status["energy"]["payout_queue"]["balances"]["gpu-worker-01"], 3)

                with urlopen(f"{hub_base}/api/hub/payouts?node_id=gpu-worker-01", timeout=5) as response:
                    payout_summary = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payout_summary["pending_credits"], 0)
                self.assertTrue(payout_summary["privacy"]["exact_amounts_hidden"])
                self.assertEqual(payout_summary["pending_count"], 1)

                with urlopen(f"{hub_base}/api/hub/payouts?node_id=gpu-worker-01&audit=1", timeout=5) as response:
                    audit_payout_summary = json.loads(response.read().decode("utf-8"))
                self.assertEqual(audit_payout_summary["pending_credits"], 3)
                self.assertEqual(audit_payout_summary["pending_credits_exact"], 3)

                claim_request = Request(
                    f"{hub_base}/api/hub/payouts/claim",
                    data=json.dumps({"node_id": "gpu-worker-01", "exact": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(claim_request, timeout=5) as response:
                    claim = json.loads(response.read().decode("utf-8"))
                self.assertEqual(claim["claimed_credits"], 3)
                self.assertEqual(claim["claimed_count"], 1)
                self.assertEqual(claim["transaction"]["kind"], "hub_worker_payout_claim")
                self.assertEqual(claim["ledger"]["balances"]["gpu-worker-01"], 3)
                self.assertEqual(claim["ledger"]["payout_queue"]["balances"].get("gpu-worker-01", 0), 0)
            finally:
                hub.shutdown()
                worker.shutdown()
                hub_thread.join(timeout=5)
                worker_thread.join(timeout=5)
                hub.server_close()
                worker.server_close()

    def test_hub_forwards_encrypted_chat_to_registered_upstream_hub(self) -> None:
        worker_calls: list[list[ChatMessage]] = []

        def fake_worker_chat(messages: Sequence[ChatMessage]) -> ChatResponse:
            worker_calls.append(list(messages))
            return ChatResponse(content="upstream worker response", provider="fake-worker", model="fake-model")

        with (
            tempfile.TemporaryDirectory() as local_tmp,
            tempfile.TemporaryDirectory() as upstream_tmp,
            tempfile.TemporaryDirectory() as worker_tmp,
        ):
            local_config = MainComputerConfig(
                workspace=Path(local_tmp),
                model="fake-model",
                hub_root=Path(local_tmp) / "hub-runtime",
                hub_credits_per_request=2,
            )
            upstream_config = MainComputerConfig(
                workspace=Path(upstream_tmp),
                model="fake-model",
                hub_root=Path(upstream_tmp) / "hub-runtime",
                hub_credits_per_request=5,
            )
            worker_config = MainComputerConfig(
                workspace=Path(worker_tmp),
                model="fake-model",
                hub_worker_node_id="GPU Worker 02",
                hub_credits_per_request=5,
            )
            worker = HubWorkerHttpServer(("127.0.0.1", 0), worker_config, fake_worker_chat, verbose=False)
            upstream = HubHttpServer(("127.0.0.1", 0), upstream_config, verbose=False)
            local = HubHttpServer(("127.0.0.1", 0), local_config, verbose=False)
            worker_thread = self._start_server(worker)
            upstream_thread = self._start_server(upstream)
            local_thread = self._start_server(local)
            try:
                worker_base = f"http://127.0.0.1:{worker.server_port}"
                upstream_base = f"http://127.0.0.1:{upstream.server_port}"
                local_base = f"http://127.0.0.1:{local.server_port}"

                register_worker = Request(
                    f"{upstream_base}/api/hub/workers/register",
                    data=json.dumps(
                        {
                            "node_id": "GPU Worker 02",
                            "endpoint": worker_base,
                            "model": "fake-model",
                            "credits_per_request": 5,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(register_worker, timeout=5):
                    pass

                register_upstream = Request(
                    f"{local_base}/api/hub/upstreams/register",
                    data=json.dumps(
                        {
                            "node_id": "Upstream Hub 01",
                            "endpoint": upstream_base,
                            "credits_per_request": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(register_upstream, timeout=5) as response:
                    registered = json.loads(response.read().decode("utf-8"))

                self.assertTrue(registered["ok"])
                self.assertEqual(registered["upstream_hub"]["node_id"], "upstream-hub-01")
                self.assertEqual(registered["hub"]["upstream_count"], 1)

                provider = HubProvider(
                    model="fake-model",
                    hub_url=local_base,
                    timeout_s=5.0,
                    client_node_id="client-01",
                )
                chat = provider.chat([ChatMessage(role="user", content="hello upstream hub")])

                self.assertEqual(chat.content, "upstream worker response")
                self.assertEqual(chat.provider, "hub")
                self.assertEqual(chat.metadata["hub"]["worker_node_id"], "gpu-worker-02")
                self.assertEqual(chat.metadata["hub"]["upstream_hub_node_id"], "upstream-hub-01")
                self.assertTrue(chat.metadata["hub"]["forwarded"])
                self.assertEqual(chat.metadata["hub"]["security_mode"], "high-security")
                self.assertEqual(worker_calls[0][-1].content, "hello upstream hub")

                with urlopen(f"{local_base}/api/hub/status", timeout=5) as response:
                    local_status = json.loads(response.read().decode("utf-8"))
                self.assertEqual(local_status["upstream_count"], 1)
                self.assertEqual(local_status["energy"]["balances"]["upstream-hub-01"], 0)
                self.assertEqual(local_status["energy"]["payout_queue"]["balances"]["upstream-hub-01"], 0)
                self.assertTrue(local_status["energy"]["payout_queue"]["privacy"]["exact_amounts_hidden"])
                with urlopen(f"{local_base}/api/hub/status?audit=1", timeout=5) as response:
                    local_audit_status = json.loads(response.read().decode("utf-8"))
                self.assertEqual(local_audit_status["energy"]["payout_queue"]["balances"]["upstream-hub-01"], 2)

                with urlopen(f"{upstream_base}/api/hub/status", timeout=5) as response:
                    upstream_status = json.loads(response.read().decode("utf-8"))
                self.assertEqual(upstream_status["energy"]["balances"]["gpu-worker-02"], 0)
                self.assertEqual(upstream_status["energy"]["payout_queue"]["balances"]["gpu-worker-02"], 0)
                self.assertTrue(upstream_status["energy"]["payout_queue"]["privacy"]["exact_amounts_hidden"])
                with urlopen(f"{upstream_base}/api/hub/status?audit=1", timeout=5) as response:
                    upstream_audit_status = json.loads(response.read().decode("utf-8"))
                self.assertEqual(upstream_audit_status["energy"]["payout_queue"]["balances"]["gpu-worker-02"], 5)
            finally:
                local.shutdown()
                upstream.shutdown()
                worker.shutdown()
                local_thread.join(timeout=5)
                upstream_thread.join(timeout=5)
                worker_thread.join(timeout=5)
                local.server_close()
                upstream.server_close()
                worker.server_close()

    def test_hub_ai_request_dto_normalizes_prompt_payload(self) -> None:
        request = HubAIRequest.from_payload(
            {
                "prompt": "hello api",
                "model": "fake-model",
                "client_node_id": "Client 01!",
            }
        )

        self.assertEqual(request.messages, [{"role": "user", "content": "hello api", "attachments": []}])
        self.assertEqual(request.model, "fake-model")
        self.assertEqual(request.client_node_id, "client-01-")

    def test_hub_v1_request_api_dispatches_and_tracks_status(self) -> None:
        worker_calls: list[list[ChatMessage]] = []

        def fake_worker_chat(messages: Sequence[ChatMessage]) -> ChatResponse:
            worker_calls.append(list(messages))
            return ChatResponse(content="v1 worker response", provider="fake-worker", model="fake-model")

        with tempfile.TemporaryDirectory() as hub_tmp, tempfile.TemporaryDirectory() as worker_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
                hub_credits_per_request=4,
            )
            worker_config = MainComputerConfig(
                workspace=Path(worker_tmp),
                model="fake-model",
                hub_worker_node_id="V1 Worker",
                hub_credits_per_request=4,
            )
            worker = HubWorkerHttpServer(("127.0.0.1", 0), worker_config, fake_worker_chat, verbose=False)
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            worker_thread = self._start_server(worker)
            hub_thread = self._start_server(hub)
            try:
                hub_base = f"http://127.0.0.1:{hub.server_port}"
                worker_base = f"http://127.0.0.1:{worker.server_port}"

                register_request = Request(
                    f"{hub_base}/api/hub/v1/workers/register",
                    data=json.dumps(
                        {
                            "node_id": "V1 Worker",
                            "endpoint": worker_base,
                            "model": "fake-model",
                            "credits_per_request": 4,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(register_request, timeout=5) as response:
                    registered = json.loads(response.read().decode("utf-8"))
                self.assertTrue(registered["ok"])

                with urlopen(f"{hub_base}/api/hub/v1/workers", timeout=5) as response:
                    workers = json.loads(response.read().decode("utf-8"))
                self.assertEqual(workers["worker_count"], 1)
                self.assertEqual(workers["workers"][0]["node_id"], "v1-worker")
                self.assertNotIn("endpoint", workers["workers"][0])

                with urlopen(f"{hub_base}/api/hub/v1/models", timeout=5) as response:
                    models = json.loads(response.read().decode("utf-8"))
                self.assertIn("fake-model", models["models"])

                submit_request = Request(
                    f"{hub_base}/api/hub/v1/requests",
                    data=json.dumps(
                        {
                            "client_node_id": "api-client",
                            "model": "fake-model",
                            "prompt": "hello from v1",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(submit_request, timeout=5) as response:
                    submitted = json.loads(response.read().decode("utf-8"))

                request_status = submitted["request"]
                self.assertEqual(request_status["state"], "completed")
                self.assertEqual(request_status["selected_worker_node_id"], "v1-worker")
                self.assertEqual(request_status["credits_queued"], 4)
                self.assertEqual(request_status["security_mode"], "legacy-plaintext")
                self.assertEqual(request_status["response"]["content"], "v1 worker response")
                self.assertTrue(request_status["polling_url"].endswith(request_status["request_id"]))
                self.assertEqual(worker_calls[0][-1].content, "hello from v1")

                with urlopen(f"{hub_base}{request_status['polling_url']}", timeout=5) as response:
                    polled = json.loads(response.read().decode("utf-8"))
                self.assertEqual(polled["request"]["request_id"], request_status["request_id"])
                self.assertEqual(polled["request"]["state"], "completed")
            finally:
                hub.shutdown()
                worker.shutdown()
                hub_thread.join(timeout=5)
                worker_thread.join(timeout=5)
                hub.server_close()
                worker.server_close()

    def test_hub_v1_request_api_retries_unavailable_exact_worker_then_falls_back(self) -> None:
        worker_calls: list[list[ChatMessage]] = []

        def fallback_worker_chat(messages: Sequence[ChatMessage]) -> ChatResponse:
            worker_calls.append(list(messages))
            return ChatResponse(content="fallback response", provider="fallback-worker", model="fallback-model")

        with tempfile.TemporaryDirectory() as hub_tmp, tempfile.TemporaryDirectory() as worker_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
                hub_credits_per_request=2,
            )
            worker_config = MainComputerConfig(
                workspace=Path(worker_tmp),
                model="fallback-model",
                hub_worker_node_id="Fallback Worker",
                hub_credits_per_request=2,
            )
            worker = HubWorkerHttpServer(("127.0.0.1", 0), worker_config, fallback_worker_chat, verbose=False)
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            worker_thread = self._start_server(worker)
            hub_thread = self._start_server(hub)
            try:
                hub_base = f"http://127.0.0.1:{hub.server_port}"
                worker_base = f"http://127.0.0.1:{worker.server_port}"

                bad_worker_request = Request(
                    f"{hub_base}/api/hub/v1/workers/register",
                    data=json.dumps(
                        {
                            "node_id": "Bad Exact Worker",
                            "endpoint": "http://127.0.0.1:1",
                            "model": "fake-model",
                            "credits_per_request": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(bad_worker_request, timeout=5):
                    pass

                fallback_register_request = Request(
                    f"{hub_base}/api/hub/v1/workers/register",
                    data=json.dumps(
                        {
                            "node_id": "Fallback Worker",
                            "endpoint": worker_base,
                            "model": "fallback-model",
                            "credits_per_request": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(fallback_register_request, timeout=5):
                    pass

                submit_request = Request(
                    f"{hub_base}/api/hub/v1/requests",
                    data=json.dumps(
                        {
                            "client_node_id": "api-client",
                            "model": "fake-model",
                            "messages": [{"role": "user", "content": "retry me"}],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(submit_request, timeout=5) as response:
                    submitted = json.loads(response.read().decode("utf-8"))

                request_status = submitted["request"]
                self.assertEqual(request_status["state"], "completed")
                self.assertEqual(request_status["selected_worker_node_id"], "fallback-worker")
                self.assertEqual(request_status["response"]["content"], "fallback response")
                self.assertEqual(request_status["response"]["metadata"]["hub"]["attempt"], 2)
                self.assertEqual(worker_calls[0][-1].content, "retry me")

                with urlopen(f"{hub_base}/api/hub/v1/workers?debug=1", timeout=5) as response:
                    workers = json.loads(response.read().decode("utf-8"))
                statuses = {worker["node_id"]: worker["status"] for worker in workers["workers"]}
                self.assertEqual(statuses["bad-exact-worker"], "offline")
                self.assertEqual(statuses["fallback-worker"], "available")
            finally:
                hub.shutdown()
                worker.shutdown()
                hub_thread.join(timeout=5)
                worker_thread.join(timeout=5)
                hub.server_close()
                worker.server_close()

    def test_hub_v1_heartbeat_metrics_idempotency_and_events(self) -> None:
        worker_calls: list[list[ChatMessage]] = []

        def fake_worker_chat(messages: Sequence[ChatMessage]) -> ChatResponse:
            worker_calls.append(list(messages))
            return ChatResponse(content="phase two response", provider="fake-worker", model="fake-model")

        with tempfile.TemporaryDirectory() as hub_tmp, tempfile.TemporaryDirectory() as worker_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
                hub_credits_per_request=6,
            )
            worker_config = MainComputerConfig(
                workspace=Path(worker_tmp),
                model="fake-model",
                hub_worker_node_id="Heartbeat Worker",
                hub_credits_per_request=6,
            )
            worker = HubWorkerHttpServer(("127.0.0.1", 0), worker_config, fake_worker_chat, verbose=False)
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            worker_thread = self._start_server(worker)
            hub_thread = self._start_server(hub)
            try:
                hub_base = f"http://127.0.0.1:{hub.server_port}"
                worker_base = f"http://127.0.0.1:{worker.server_port}"

                register_request = Request(
                    f"{hub_base}/api/hub/v1/workers/register",
                    data=json.dumps(
                        {
                            "node_id": "Heartbeat Worker",
                            "endpoint": worker_base,
                            "model": "fake-model",
                            "models": ["fake-model", "vision-model"],
                            "capabilities": {"gpu": "test-gpu", "vram_gb": 24},
                            "credits_per_request": 6,
                            "max_concurrency": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(register_request, timeout=5) as response:
                    registered = json.loads(response.read().decode("utf-8"))
                self.assertEqual(registered["worker"]["models"], ["fake-model", "vision-model"])
                self.assertEqual(registered["worker"]["max_concurrency"], 2)

                heartbeat_request = Request(
                    f"{hub_base}/api/hub/v1/workers/heartbeat-worker/heartbeat",
                    data=json.dumps(
                        {
                            "status": "available",
                            "queue_depth": 3,
                            "active_requests": 0,
                            "max_concurrency": 2,
                            "capabilities": {"gpu": "test-gpu", "driver": "ok"},
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(heartbeat_request, timeout=5) as response:
                    heartbeat = json.loads(response.read().decode("utf-8"))
                self.assertEqual(heartbeat["worker"]["queue_depth"], 3)
                self.assertEqual(heartbeat["worker"]["capabilities"]["driver"], "ok")

                with urlopen(f"{hub_base}/api/hub/v1/workers/heartbeat-worker?debug=1", timeout=5) as response:
                    worker_status = json.loads(response.read().decode("utf-8"))
                self.assertEqual(worker_status["worker"]["node_id"], "heartbeat-worker")
                self.assertEqual(worker_status["worker"]["max_concurrency"], 2)
                self.assertEqual(worker_status["worker"]["capabilities"]["gpu"], "test-gpu")
                self.assertIn("endpoint", worker_status["worker"])

                submit_payload = {
                    "client_node_id": "api-client",
                    "model": "fake-model",
                    "prompt": "phase two",
                    "idempotency_key": "request-key-01",
                    "deadline_seconds": 30,
                    "metadata": {"max_retries": 1},
                }
                submit_request = Request(
                    f"{hub_base}/api/hub/v1/requests",
                    data=json.dumps(submit_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(submit_request, timeout=5) as response:
                    submitted = json.loads(response.read().decode("utf-8"))
                status = submitted["request"]
                self.assertEqual(status["state"], "completed")
                self.assertEqual(status["idempotency_key"], "request-key-01")
                self.assertEqual(status["selected_worker_node_id"], "heartbeat-worker")
                self.assertTrue(status["deadline_at"])
                self.assertEqual(status["response"]["content"], "phase two response")

                duplicate_request = Request(
                    f"{hub_base}/api/hub/v1/requests",
                    data=json.dumps(submit_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(duplicate_request, timeout=5) as response:
                    duplicate = json.loads(response.read().decode("utf-8"))
                self.assertEqual(duplicate["request"]["request_id"], status["request_id"])
                self.assertEqual(len(worker_calls), 1)

                with urlopen(f"{hub_base}/api/hub/v1/requests/{status['request_id']}/events", timeout=5) as response:
                    events = json.loads(response.read().decode("utf-8"))
                event_types = [event["type"] for event in events["events"]]
                self.assertIn("request.accepted", event_types)
                self.assertIn("worker.selected", event_types)
                self.assertIn("request.completed", event_types)

                with urlopen(f"{hub_base}/api/hub/v1/requests?state=completed", timeout=5) as response:
                    requests = json.loads(response.read().decode("utf-8"))
                self.assertEqual(requests["request_count"], 1)
                self.assertEqual(requests["requests"][0]["request_id"], status["request_id"])

                with urlopen(f"{hub_base}/api/hub/v1/metrics", timeout=5) as response:
                    metrics = json.loads(response.read().decode("utf-8"))
                self.assertEqual(metrics["requests"]["by_state"]["completed"], 1)
                self.assertEqual(metrics["workers"]["total"], 1)
                self.assertEqual(metrics["workers"]["stale"], 0)
            finally:
                hub.shutdown()
                worker.shutdown()
                hub_thread.join(timeout=5)
                worker_thread.join(timeout=5)
                hub.server_close()
                worker.server_close()

    def test_hub_registry_excludes_stale_workers_until_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as hub_tmp:
            registry = HubRegistry(Path(hub_tmp), allow_insecure_dev_network=False)
            registry.register_worker(
                node_id="Stale Worker",
                endpoint="http://127.0.0.1:9999",
                model="fake-model",
                max_concurrency=1,
            )

            stale_count = registry.expire_stale_workers(stale_after_s=0)
            self.assertGreaterEqual(stale_count, 1)
            self.assertIsNone(registry.select_worker("fake-model"))
            status = registry.status()
            self.assertEqual(status["stale_worker_count"], 1)

            heartbeat = registry.heartbeat_worker("stale-worker", status="available", queue_depth=0, active_requests=0)
            self.assertFalse(heartbeat.stale)
            self.assertEqual(heartbeat.status, "available")
            self.assertIsNotNone(registry.select_worker("fake-model"))

    def test_hub_provider_defaults_to_temporary_key_encrypted_envelopes(self) -> None:
        captured: dict[str, object] = {}
        worker_keypair = generate_hub_session_keypair()
        session_id = "sess-test"
        request_id = "hub-test"
        shared: dict[str, bytes] = {}

        class FakeResponse:
            def __init__(self, payload: dict[str, object]):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured.setdefault("urls", []).append(request.full_url)
            captured["timeout"] = timeout
            payload = json.loads(request.data.decode("utf-8"))
            if request.full_url.endswith("/api/hub/sessions/start"):
                captured["session_payload"] = payload
                self.assertNotIn("messages", payload)
                shared["key"] = derive_hub_session_key(
                    private_key=worker_keypair.private_key,
                    peer_public_key=str(payload["requester_public_key"]),
                    session_id=session_id,
                )
                return FakeResponse(
                    {
                        "ok": True,
                        "session_id": session_id,
                        "request_id": request_id,
                        "worker_public_key": worker_keypair.public_key,
                        "metadata": {"hub": {"worker_node_id": "gpu-01", "credits_queued": 7}},
                    }
                )
            if request.full_url.endswith("/api/hub/sessions/chat"):
                captured["relay_payload"] = payload
                self.assertNotIn("messages", payload)
                self.assertIn("envelope", payload)
                request_aad = {"session_id": session_id, "request_id": request_id, "direction": "request"}
                decrypted = decrypt_hub_envelope(payload["envelope"], key=shared["key"], aad=request_aad)
                captured["decrypted_messages"] = decrypted["messages"]
                response_aad = {"session_id": session_id, "request_id": request_id, "direction": "response"}
                response_envelope = encrypt_hub_envelope(
                    {
                        "content": "from encrypted hub",
                        "provider": "fake-worker",
                        "model": "fake-model",
                        "metadata": {"worker": "ok"},
                    },
                    key=shared["key"],
                    aad=response_aad,
                )
                return FakeResponse(
                    {
                        "ok": True,
                        "session_id": session_id,
                        "request_id": request_id,
                        "response_envelope": response_envelope,
                        "metadata": {"hub": {"worker_node_id": "gpu-01", "credits_queued": 7}},
                    }
                )
            raise AssertionError(request.full_url)

        provider = HubProvider(
            model="fake-model",
            hub_url="http://127.0.0.1:8770",
            timeout_s=12.0,
            client_node_id="client-01",
        )
        with patch("main_computer.providers.hub.urlopen", fake_urlopen):
            response = provider.chat([ChatMessage(role="user", content="hi")])

        self.assertEqual(response.content, "from encrypted hub")
        self.assertEqual(response.provider, "hub")
        self.assertEqual(response.model, "fake-model")
        self.assertEqual(captured["urls"], ["http://127.0.0.1:8770/api/hub/sessions/start", "http://127.0.0.1:8770/api/hub/sessions/chat"])
        self.assertEqual(captured["timeout"], 12.0)
        self.assertEqual(captured["decrypted_messages"][0]["content"], "hi")
        self.assertEqual(response.metadata["hub"]["worker_node_id"], "gpu-01")
        self.assertTrue(response.metadata["hub"]["hub_blind"])

    def test_hub_provider_allows_high_security_docker_dev_http_when_explicitly_opted_in(self) -> None:
        captured: dict[str, object] = {}
        shared: dict[str, object] = {}

        class FakeResponse:
            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured.setdefault("urls", []).append(request.full_url)
            payload = json.loads(request.data.decode("utf-8"))
            if request.full_url == "http://hub:8770/api/hub/sessions/start":
                requester_public_key = payload["requester_public_key"]
                keypair = generate_hub_session_keypair()
                session_id = "sess-dev"
                request_id = "req-dev"
                shared["session_id"] = session_id
                shared["request_id"] = request_id
                shared["key"] = derive_hub_session_key(
                    private_key=keypair.private_key,
                    peer_public_key=requester_public_key,
                    session_id=session_id,
                )
                return FakeResponse(
                    {
                        "ok": True,
                        "session_id": session_id,
                        "request_id": request_id,
                        "worker_public_key": keypair.public_key,
                        "metadata": {"hub": {"worker_node_id": "docker-worker"}},
                    }
                )
            if request.full_url == "http://hub:8770/api/hub/sessions/chat":
                response_aad = {"session_id": shared["session_id"], "request_id": shared["request_id"], "direction": "response"}
                return FakeResponse(
                    {
                        "ok": True,
                        "response_envelope": encrypt_hub_envelope(
                            {
                                "content": "docker dev ok",
                                "provider": "fake-worker",
                                "model": "fake-model",
                            },
                            key=shared["key"],
                            aad=response_aad,
                        ),
                        "metadata": {"hub": {"worker_node_id": "docker-worker"}},
                    }
                )
            raise AssertionError(request.full_url)

        provider = HubProvider(
            model="fake-model",
            hub_url="http://hub:8770",
            timeout_s=12.0,
            client_node_id="client-01",
            allow_insecure_dev_network=True,
        )
        with patch("main_computer.providers.hub.urlopen", fake_urlopen):
            response = provider.chat([ChatMessage(role="user", content="hi")])

        self.assertEqual(response.content, "docker dev ok")
        self.assertEqual(
            captured["urls"],
            ["http://hub:8770/api/hub/sessions/start", "http://hub:8770/api/hub/sessions/chat"],
        )

    def test_hub_provider_can_explicitly_use_legacy_plaintext_mode(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps(
                    {
                        "content": "from hub",
                        "provider": "hub",
                        "model": "fake-model",
                        "metadata": {"hub": {"worker_node_id": "gpu-01"}},
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        provider = HubProvider(
            model="fake-model",
            hub_url="http://hub.example",
            timeout_s=12.0,
            client_node_id="client-01",
            high_security=False,
        )
        with patch("main_computer.providers.hub.urlopen", fake_urlopen):
            response = provider.chat([ChatMessage(role="user", content="hi")])

        self.assertEqual(response.content, "from hub")
        self.assertEqual(response.provider, "hub")
        self.assertEqual(response.model, "fake-model")
        self.assertEqual(captured["url"], "http://hub.example/api/hub/chat")
        self.assertEqual(captured["timeout"], 12.0)
        payload = captured["payload"]
        self.assertFalse(payload["high_security"])
        self.assertEqual(payload["client_node_id"], "client-01")
        self.assertEqual(payload["messages"][0]["content"], "hi")

    def test_router_builds_hub_provider_from_config(self) -> None:
        config = MainComputerConfig(
            workspace=Path.cwd(),
            provider="hub",
            model="fake-model",
            hub_url="http://127.0.0.1:8770",
            hub_client_node_id="client-01",
        )

        computer = MainComputer.build(config)

        self.assertIsInstance(computer.provider, HubProvider)
        self.assertEqual(computer.provider.hub_url, "http://127.0.0.1:8770")
        self.assertEqual(computer.provider.client_node_id, "client-01")
        self.assertTrue(computer.provider.high_security)

    def test_hub_serves_admin_control_site_and_bootstrap_payload(self) -> None:
        with tempfile.TemporaryDirectory() as hub_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            hub_thread = self._start_server(hub)
            try:
                hub_base = f"http://127.0.0.1:{hub.server_port}"

                with urlopen(f"{hub_base}/admin", timeout=5) as response:
                    html = response.read().decode("utf-8")
                    content_type = response.headers.get("Content-Type", "")

                self.assertIn("text/html", content_type)
                self.assertIn("Main Computer Hub Control", html)
                self.assertIn("/api/hub/v1/admin/bootstrap", html)
                self.assertIn("Register worker", html)

                with urlopen(f"{hub_base}/api/hub/v1/admin/bootstrap", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertTrue(payload["ok"])
                self.assertEqual(payload["service"], "main-computer-hub")
                self.assertEqual(payload["api_version"], "v1")
                self.assertIn("/admin", payload["admin_site"]["routes"])
                self.assertEqual(payload["endpoints"]["worker_register"], "/api/hub/v1/workers/register")
                self.assertEqual(payload["worker_count"], 0)
                self.assertEqual(payload["request_count"], 0)
            finally:
                hub.shutdown()
                hub.server_close()
                hub_thread.join(timeout=2)

    def test_admin_bootstrap_reflects_registered_workers_for_control_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as hub_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            hub_thread = self._start_server(hub)
            try:
                hub_base = f"http://127.0.0.1:{hub.server_port}"
                register_request = Request(
                    f"{hub_base}/api/hub/v1/workers/register",
                    data=json.dumps(
                        {
                            "node_id": "GPU Admin Worker",
                            "endpoint": "http://127.0.0.1:8771",
                            "models": ["fake-model", "backup-model"],
                            "max_concurrency": 3,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(register_request, timeout=5) as response:
                    registered = json.loads(response.read().decode("utf-8"))
                self.assertTrue(registered["ok"])

                with urlopen(f"{hub_base}/api/hub/v1/admin/bootstrap", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(payload["worker_count"], 1)
                worker = payload["workers"][0]
                self.assertEqual(worker["node_id"], "gpu-admin-worker")
                self.assertEqual(worker["models"], ["fake-model", "backup-model"])
                self.assertEqual(worker["max_concurrency"], 3)
                self.assertIn("fake-model", payload["models"])
                self.assertEqual(payload["status"]["available_worker_count"], 1)
            finally:
                hub.shutdown()
                hub.server_close()
                hub_thread.join(timeout=2)


    def test_remote_overflow_safe_chat_surface_returns_no_credit_observable_response(self) -> None:
        with tempfile.TemporaryDirectory() as hub_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            hub_thread = self._start_server(hub)
            try:
                provider = HubProvider(
                    model="fake-model",
                    hub_url=f"http://127.0.0.1:{hub.server_port}",
                    client_node_id="chat-console-client",
                )
                response = provider.remote_overflow_safe_chat(
                    [ChatMessage(role="user", content="verify observable hub overflow")],
                    remote_overflow_request_id="overflow-correlation-01",
                    metadata={"pending_request_id": "pending-01"},
                )

                self.assertEqual(response.provider, "remote-hub-ai")
                self.assertEqual(response.model, "fake-model")
                self.assertIn("Remote Hub AI response received.", response.content)
                self.assertIn("No credits were held", response.content)
                self.assertTrue(response.metadata["remote_hub_observable_passthrough"])
                self.assertTrue(response.metadata["no_credit_hold_created"])
                self.assertTrue(response.metadata["no_credit_spent"])
                self.assertTrue(response.metadata["no_real_paid_worker_contacted"])
                self.assertEqual(response.metadata["remote_overflow_request_id"], "overflow-correlation-01")
                self.assertEqual(response.metadata["hub"]["remote_overflow_request_id"], "overflow-correlation-01")
                self.assertEqual(response.metadata["hub"]["surface"], "/api/hub/remote-overflow/safe-chat")
            finally:
                hub.shutdown()
                hub.server_close()
                hub_thread.join(timeout=2)


    def test_remote_overflow_safe_chat_charges_wallet_account_with_multisession_key(self) -> None:
        wallet = "0x7780097b4756ed08176d288b9acb8d9e878a5269"
        key_id = "msk_paid_overflow_test"
        with tempfile.TemporaryDirectory() as hub_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            account_id = wallet_account_id(wallet)
            hub.credit_ledger.record_completed_bridge_deposit(
                account_id=account_id,
                owner_address=wallet,
                chain_completed_credits=2,
                deposit_id="paid-overflow-funded",
                memo="test bridge funding",
            )
            with hub.multisession_key_store_lock:
                data = {
                    "version": "main-computer-multisession-keys-v1",
                    "keys": {
                        key_id: {
                            "id": key_id,
                            "status": "active",
                            "created_at": "2026-06-03T00:00:00+00:00",
                            "revoked_at": "",
                            "wallet_address": wallet,
                            "chain_id": "0x28757b2",
                            "request_id": "msk-request",
                            "origin": "test",
                        }
                    },
                }
                hub.multisession_key_store_path.parent.mkdir(parents=True, exist_ok=True)
                hub.multisession_key_store_path.write_text(json.dumps(data), encoding="utf-8")

            hub_thread = self._start_server(hub)
            try:
                provider = HubProvider(
                    model="fake-model",
                    hub_url=f"http://127.0.0.1:{hub.server_port}",
                    client_node_id="chat-console-client",
                )

                def paid_metadata(request_id: str) -> dict[str, object]:
                    return {
                        "payment_authorization": {
                            "kind": "multisession_key",
                            "paid_overflow_enabled": True,
                            "wallet_address": wallet,
                            "multisession_key_id": key_id,
                            "chain_id": "0x28757b2",
                            "max_output_tokens": 10,
                            "credits_per_token": "0.001",
                            "max_authorized_credits": 1,
                        },
                        "paid_overflow_enabled": True,
                        "pending_request_id": request_id,
                    }

                first = provider.remote_overflow_safe_chat(
                    [ChatMessage(role="user", content="hi")],
                    remote_overflow_request_id="paid-overflow-01",
                    metadata=paid_metadata("paid-overflow-01"),
                )
                self.assertIn("Paid overflow charged 1 whole credit", first.content)
                self.assertEqual(first.metadata["payment"]["account_id"], account_id)
                self.assertEqual(first.metadata["payment"]["wallet_address"], wallet)
                self.assertEqual(first.metadata["payment"]["charged_credits"], 1)
                self.assertEqual(first.metadata["payment"]["available_credits_after"], 1)
                self.assertEqual(first.metadata["payment"]["spent_credits_after"], 1)
                self.assertTrue(first.metadata["credit_hold_created"])
                self.assertTrue(first.metadata["credit_spent"])

                duplicate = provider.remote_overflow_safe_chat(
                    [ChatMessage(role="user", content="hi")],
                    remote_overflow_request_id="paid-overflow-01",
                    metadata=paid_metadata("paid-overflow-01"),
                )
                self.assertTrue(duplicate.metadata["payment"]["idempotent"])
                self.assertEqual(duplicate.metadata["payment"]["available_credits_after"], 1)
                self.assertEqual(duplicate.metadata["payment"]["spent_credits_after"], 1)

                second = provider.remote_overflow_safe_chat(
                    [ChatMessage(role="user", content="hi again")],
                    remote_overflow_request_id="paid-overflow-02",
                    metadata=paid_metadata("paid-overflow-02"),
                )
                self.assertEqual(second.metadata["payment"]["available_credits_after"], 0)
                self.assertEqual(second.metadata["payment"]["spent_credits_after"], 2)

                payload = {
                    "model": "fake-model",
                    "client_node_id": "chat-console-client",
                    "messages": [{"role": "user", "content": "third"}],
                    "remote_overflow_request_id": "paid-overflow-03",
                    "metadata": paid_metadata("paid-overflow-03"),
                }
                request = Request(
                    f"http://127.0.0.1:{hub.server_port}/api/hub/remote-overflow/safe-chat",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=5)
                self.assertEqual(error.exception.code, 402)

                account = hub.credit_ledger.get_account(account_id)
                self.assertEqual(account.available_credits, 0)
                self.assertEqual(account.held_credits, 0)
                self.assertEqual(account.spent_credits, 2)
                charges = hub.credit_ledger.list_charges(account_id=account_id)
                self.assertEqual(len(charges), 2)
            finally:
                hub.shutdown()
                hub.server_close()
                hub_thread.join(timeout=2)


    def test_multisession_key_validate_endpoint_reports_hub_key_readiness_without_spending(self) -> None:
        wallet = "0x7780097b4756ed08176d288b9acb8d9e878a5269"
        key_id = "msk_readiness_test"
        with tempfile.TemporaryDirectory() as hub_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            account_id = wallet_account_id(wallet)
            hub.credit_ledger.record_completed_bridge_deposit(
                account_id=account_id,
                owner_address=wallet,
                chain_completed_credits=2,
                deposit_id="readiness-funded",
                memo="test bridge funding",
            )
            with hub.multisession_key_store_lock:
                hub.multisession_key_store_path.parent.mkdir(parents=True, exist_ok=True)
                hub.multisession_key_store_path.write_text(
                    json.dumps(
                        {
                            "version": "main-computer-multisession-keys-v1",
                            "keys": {
                                key_id: {
                                    "id": key_id,
                                    "status": "active",
                                    "created_at": "2026-06-03T00:00:00+00:00",
                                    "revoked_at": "",
                                    "wallet_address": wallet,
                                    "chain_id": "0x28757b2",
                                    "request_id": "msk-readiness-request",
                                    "origin": "test",
                                },
                                "msk_revoked": {
                                    "id": "msk_revoked",
                                    "status": "revoked",
                                    "created_at": "2026-06-03T00:00:00+00:00",
                                    "revoked_at": "2026-06-03T00:01:00+00:00",
                                    "wallet_address": wallet,
                                    "chain_id": "0x28757b2",
                                    "request_id": "msk-revoked-request",
                                    "origin": "test",
                                },
                            },
                        }
                    ),
                    encoding="utf-8",
                )

            hub_thread = self._start_server(hub)
            try:
                provider = HubProvider(
                    model="fake-model",
                    hub_url=f"http://127.0.0.1:{hub.server_port}",
                    client_node_id="chat-console-client",
                )
                ready = provider.validate_multisession_key(
                    {
                        "payment_authorization": {
                            "kind": "multisession_key",
                            "paid_overflow_enabled": True,
                            "wallet_address": wallet,
                            "multisession_key_id": key_id,
                            "chain_id": "0x28757b2",
                            "max_authorized_credits": 1,
                        }
                    }
                )
                self.assertTrue(ready["ok"])
                self.assertTrue(ready["valid"])
                self.assertTrue(ready["ready"])
                self.assertTrue(ready["credit_ready"])
                self.assertEqual(ready["reason_code"], "active")
                self.assertEqual(ready["account_id"], account_id)
                self.assertEqual(ready["account"]["available_credits"], 2)

                revoked = provider.validate_multisession_key(
                    {
                        "payment_authorization": {
                            "kind": "multisession_key",
                            "wallet_address": wallet,
                            "multisession_key_id": "msk_revoked",
                            "chain_id": "0x28757b2",
                            "max_authorized_credits": 1,
                        }
                    }
                )
                self.assertFalse(revoked["valid"])
                self.assertFalse(revoked["ready"])
                self.assertEqual(revoked["reason_code"], "key_not_active")
                self.assertEqual(revoked["account_id"], account_id)

                too_expensive = provider.validate_multisession_key(
                    {
                        "payment_authorization": {
                            "kind": "multisession_key",
                            "wallet_address": wallet,
                            "multisession_key_id": key_id,
                            "chain_id": "0x28757b2",
                            "max_authorized_credits": 3,
                        }
                    }
                )
                self.assertTrue(too_expensive["valid"])
                self.assertFalse(too_expensive["ready"])
                self.assertFalse(too_expensive["credit_ready"])
                self.assertEqual(too_expensive["reason_code"], "insufficient_spendable_credits")

                account = hub.credit_ledger.get_account(account_id)
                self.assertEqual(account.available_credits, 2)
                self.assertEqual(account.held_credits, 0)
                self.assertEqual(account.spent_credits, 0)
                self.assertEqual(hub.credit_ledger.list_charges(account_id=account_id), [])
            finally:
                hub.shutdown()
                hub.server_close()
                hub_thread.join(timeout=2)


    def test_remote_overflow_safe_chat_rejects_disabled_paid_overflow_before_hold(self) -> None:
        wallet = "0x7780097b4756ed08176d288b9acb8d9e878a5269"
        key_id = "msk_paid_overflow_disabled_test"
        with tempfile.TemporaryDirectory() as hub_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            account_id = wallet_account_id(wallet)
            hub.credit_ledger.record_completed_bridge_deposit(
                account_id=account_id,
                owner_address=wallet,
                chain_completed_credits=2,
                deposit_id="paid-overflow-disabled-funded",
                memo="test bridge funding",
            )
            with hub.multisession_key_store_lock:
                data = {
                    "version": "main-computer-multisession-keys-v1",
                    "keys": {
                        key_id: {
                            "id": key_id,
                            "status": "active",
                            "created_at": "2026-06-03T00:00:00+00:00",
                            "revoked_at": "",
                            "wallet_address": wallet,
                            "chain_id": "0x28757b2",
                            "request_id": "msk-disabled-request",
                            "origin": "test",
                        }
                    },
                }
                hub.multisession_key_store_path.parent.mkdir(parents=True, exist_ok=True)
                hub.multisession_key_store_path.write_text(json.dumps(data), encoding="utf-8")

            hub_thread = self._start_server(hub)
            try:
                payload = {
                    "model": "fake-model",
                    "client_node_id": "chat-console-client",
                    "messages": [{"role": "user", "content": "hi"}],
                    "remote_overflow_request_id": "paid-overflow-disabled",
                    "metadata": {
                        "payment_authorization": {
                            "kind": "multisession_key",
                            "paid_overflow_enabled": False,
                            "wallet_address": wallet,
                            "multisession_key_id": key_id,
                            "chain_id": "0x28757b2",
                            "max_output_tokens": 10,
                            "credits_per_token": "0.001",
                            "max_authorized_credits": 1,
                        }
                    },
                }
                request = Request(
                    f"http://127.0.0.1:{hub.server_port}/api/hub/remote-overflow/safe-chat",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=5)
                self.assertEqual(error.exception.code, 403)

                account = hub.credit_ledger.get_account(account_id)
                self.assertEqual(account.available_credits, 2)
                self.assertEqual(account.held_credits, 0)
                self.assertEqual(account.spent_credits, 0)
                self.assertEqual(hub.credit_ledger.list_charges(account_id=account_id), [])
            finally:
                hub.shutdown()
                hub.server_close()
                hub_thread.join(timeout=2)


    def test_remote_overflow_safe_chat_retries_loopback_when_hub_dns_name_fails(self) -> None:
        class DnsFailOnceHubProvider(HubProvider):
            def _post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
                if self.hub_url.startswith("http://unresolvable-hub-name"):
                    raise RuntimeError(
                        "Hub request failed for "
                        f"{self.hub_url.rstrip('/')}{path}: "
                        "<urlopen error [Errno 11001] getaddrinfo failed>"
                    )
                return super()._post_json(path, payload)

        with tempfile.TemporaryDirectory() as hub_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            hub_thread = self._start_server(hub)
            try:
                provider = DnsFailOnceHubProvider(
                    model="fake-model",
                    hub_url=f"http://unresolvable-hub-name:{hub.server_port}",
                    client_node_id="chat-console-client",
                )
                response = provider.remote_overflow_safe_chat(
                    [ChatMessage(role="user", content="verify loopback fallback")],
                    remote_overflow_request_id="overflow-correlation-fallback",
                    metadata={"pending_request_id": "pending-fallback"},
                )

                self.assertEqual(response.provider, "remote-hub-ai")
                self.assertIn("Remote Hub AI response received.", response.content)
                self.assertEqual(response.metadata["hub_url"], f"http://127.0.0.1:{hub.server_port}")
                self.assertEqual(
                    response.metadata["hub_url_fallback_from"],
                    f"http://unresolvable-hub-name:{hub.server_port}",
                )
                self.assertEqual(
                    response.metadata["hub_url_fallback_to"],
                    f"http://127.0.0.1:{hub.server_port}",
                )
                self.assertEqual(response.metadata["remote_overflow_request_id"], "overflow-correlation-fallback")
                self.assertTrue(response.metadata["no_credit_hold_created"])
                self.assertTrue(response.metadata["no_credit_spent"])
            finally:
                hub.shutdown()
                hub.server_close()
                hub_thread.join(timeout=2)



if __name__ == "__main__":
    unittest.main()
