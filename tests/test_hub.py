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
from main_computer.hub import HubHttpServer, HubWorkerHttpServer
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
                self.assertEqual(status["energy"]["payout_queue"]["balances"]["gpu-worker-01"], 3)
                self.assertEqual(status["energy"]["payout_queue"]["recent"][-1]["kind"], "hub_worker_payout_queued")
                self.assertTrue(status["security"]["high_security_default"])

                with urlopen(f"{hub_base}/api/hub/payouts?node_id=gpu-worker-01", timeout=5) as response:
                    payout_summary = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payout_summary["pending_credits"], 3)
                self.assertEqual(payout_summary["pending_count"], 1)

                claim_request = Request(
                    f"{hub_base}/api/hub/payouts/claim",
                    data=json.dumps({"node_id": "gpu-worker-01"}).encode("utf-8"),
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
                self.assertEqual(local_status["energy"]["payout_queue"]["balances"]["upstream-hub-01"], 2)

                with urlopen(f"{upstream_base}/api/hub/status", timeout=5) as response:
                    upstream_status = json.loads(response.read().decode("utf-8"))
                self.assertEqual(upstream_status["energy"]["balances"]["gpu-worker-02"], 0)
                self.assertEqual(upstream_status["energy"]["payout_queue"]["balances"]["gpu-worker-02"], 5)
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


if __name__ == "__main__":
    unittest.main()
