from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Sequence
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers import LLMProvider
from main_computer.viewport import ViewportServer


class FakeRagAtProvider(LLMProvider):
    name = "fake-rag-at"
    model = "fake-thinking"

    def __init__(self) -> None:
        self.think = None
        self.calls: list[list[ChatMessage]] = []
        self.stream_callback = None

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.calls.append(list(messages))
        prompt = "\n\n".join(message.content for message in messages)
        if "README.md" not in prompt:
            raise AssertionError("RAG-AT repair call did not include retrieved README context")
        if self.stream_callback is not None:
            self.stream_callback(
                {
                    "type": "request_waiting",
                    "provider": self.name,
                    "model": self.model,
                    "status_preview": "Fake provider is waiting for a response. Thinking: on (mode: medium).",
                    "think": self.think,
                    "thinking_enabled": True,
                    "thinking_state": str(self.think or "unspecified"),
                    "thinking_status": "Thinking: on (mode: medium).",
                    "stream_event_type": "request_waiting",
                    "stream_phase": "request",
                    "stream_heartbeat": True,
                    "waiting_reason": "fake_waiting_for_response",
                    "message_count": len(messages),
                    "request_bytes": len(prompt.encode("utf-8")),
                    "transport": "test_stream",
                    "elapsed_ms": 123,
                    "content_chars": 0,
                    "thinking_chars": 0,
                }
            )
            self.stream_callback(
                {
                    "type": "thinking_delta",
                    "provider": self.name,
                    "model": self.model,
                    "delta": "raw route thought",
                    "thinking_preview": "raw route thought",
                    "thinking_chars": 24,
                    "raw_thinking_exposed": True,
                }
            )
            self.stream_callback(
                {
                    "type": "content_delta",
                    "provider": self.name,
                    "model": self.model,
                    "content_preview": "partial RAG answer",
                    "content_chars": 18,
                }
            )
        return ChatResponse(
            content=json.dumps(
                {
                    "ok": True,
                    "summary": "RAG-AT used retrieved README context.",
                    "answer": "The RAG-assisted thinking path is wired to the Activity Monitor.",
                    "files": [],
                    "commands": [],
                    "warnings": [],
                }
            ),
            provider=self.name,
            model=self.model,
            metadata={"thinking": "PRIVATE_FAKE_THINKING", "think": self.think},
        )


class ViewportRagAssistedThinkingRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        (self.repo / "README.md").write_text(
            "# RAG-AT fixture\n\nUse RAG-AT to answer from retrieved README context.\n",
            encoding="utf-8",
        )
        (self.repo / "main_computer").mkdir()
        (self.repo / "main_computer" / "example.py").write_text("VALUE = 'activity monitor'\n", encoding="utf-8")

        self.server = ViewportServer(
            ("127.0.0.1", 0),
            MainComputerConfig(workspace=self.repo),
            verbose=False,
        )
        self.server.debug_root = self.repo.resolve()
        self.provider = FakeRagAtProvider()
        self.server.computer.provider = self.provider
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tempdir.cleanup()

    def _json_post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_chat_console_rag_assisted_thinking_route_returns_output_cell_and_activity(self) -> None:
        data = self._json_post(
            "/api/applications/chat-console/rag-assisted-thinking/evaluate",
            {
                "cell": {
                    "id": "cell-rag-at",
                    "type": "ai",
                    "source": "Use RAG-AT to answer from README context.",
                    "variant_index": 0,
                },
                "think": "medium",
                "queries": ["README RAG-AT"],
                "docker_enabled": False,
                "auto_apply": False,
            },
        )

        self.assertTrue(data["ok"])
        self.assertEqual(data["mode"], "rag_assisted_thinking_v4")
        self.assertTrue(str(data["run_id"]).startswith("rag_assisted_thinking_v4_"))
        self.assertEqual(self.provider.think, "medium")
        self.assertEqual(len(self.provider.calls), 1)

        output_cell = data["output_cell"]
        self.assertEqual(output_cell["type"], "output")
        self.assertEqual(output_cell["status"], "ok")
        self.assertEqual(output_cell["provider"], self.provider.name)
        self.assertEqual(output_cell["model"], self.provider.model)
        self.assertEqual(output_cell["parts"][0]["title"], "AI response")
        self.assertNotIn("Run id:", output_cell["parts"][0]["content"])
        self.assertNotIn("Activity Monitor:", output_cell["parts"][0]["content"])
        self.assertIn("run_id", output_cell["parts"][0]["metadata"])

        ai_events = self.server.activity.events(filter_id="ai", limit=80)
        self.assertTrue(any(event["data"].get("run_id") == data["run_id"] for event in ai_events))
        serialized_events = json.dumps(self.server.activity.events(filter_id="live", limit=120))
        self.assertIn("rag_type", serialized_events)
        self.assertIn("rag_types_seen", serialized_events)
        self.assertIn("running_text", serialized_events)
        self.assertIn("system_prompt_preview", serialized_events)
        self.assertIn("model_input", serialized_events)
        self.assertIn("partial RAG answer", serialized_events)
        self.assertIn("raw route thought", serialized_events)
        self.assertIn('"raw_thinking_exposed": true', serialized_events)
        self.assertIn("Thinking: on (mode: medium).", serialized_events)
        self.assertIn("fake_waiting_for_response", serialized_events)
        self.assertIn("thinking_enabled", serialized_events)
        self.assertIn("stream_phase", serialized_events)
        self.assertIn("rag-assisted-thinking-v4", serialized_events)
        self.assertNotIn("rag-assisted-thinking-v3", serialized_events)
        self.assertNotIn("PRIVATE_FAKE_THINKING", serialized_events)
        self.assertNotIn("Docker verification started", serialized_events)

    def test_chat_console_rag_assisted_thinking_policy_ignores_stale_docker_flag_when_executor_is_off(self) -> None:
        policy = self.server.RequestHandlerClass._rag_assisted_thinking_policy(  # type: ignore[attr-defined]
            self,
            {
                "think": "medium",
                "docker_enabled": True,
                "verify_before": True,
                "verify_after": True,
                "require_docker_success": True,
            },
        )
        self.assertFalse(policy.docker_enabled)
        self.assertFalse(policy.verify_before)
        self.assertFalse(policy.verify_after)
        self.assertFalse(policy.require_docker_success)
        self.assertEqual(policy.docker_command, "")

        policy = self.server.RequestHandlerClass._rag_assisted_thinking_policy(  # type: ignore[attr-defined]
            self,
            {"think": "medium", "require_docker": True},
        )
        self.assertTrue(policy.docker_enabled)
        self.assertTrue(policy.verify_before)
        self.assertTrue(policy.verify_after)
        self.assertTrue(policy.require_docker_success)
        self.assertEqual(policy.docker_image, "main-computer-executor:latest")

    def test_chat_console_rag_assisted_thinking_policy_links_docker_to_executor_and_rag_kill_switch(self) -> None:
        self.server.config = MainComputerConfig(
            workspace=self.repo,
            executor_enabled=True,
            executor_image="main-computer-executor:test",
            executor_timeout_s=45.0,
        )

        policy = self.server.RequestHandlerClass._rag_assisted_thinking_policy(  # type: ignore[attr-defined]
            self,
            {"think": "medium", "docker_enabled": False, "verify_before": False, "verify_after": False},
        )

        self.assertTrue(policy.docker_enabled)
        self.assertTrue(policy.verify_before)
        self.assertTrue(policy.verify_after)
        self.assertTrue(policy.require_docker_success)
        self.assertEqual(policy.docker_image, "main-computer-executor:test")
        self.assertEqual(policy.docker_timeout_s, 45.0)

        self.server.config = MainComputerConfig(
            workspace=self.repo,
            executor_enabled=True,
            rag_docker_enabled=False,
        )
        policy = self.server.RequestHandlerClass._rag_assisted_thinking_policy(self, {"think": "medium"})  # type: ignore[attr-defined]
        self.assertFalse(policy.docker_enabled)
        self.assertEqual(policy.docker_command, "")


class ChatConsoleRagAtStaticTests(unittest.TestCase):
    def test_chat_console_frontend_contains_rag_at_toggle_and_endpoint(self) -> None:
        root = Path(__file__).resolve().parents[1]
        chat_js = (root / "main_computer" / "web" / "applications" / "scripts" / "chat-console.js").read_text(encoding="utf-8")
        state_js = (root / "main_computer" / "web" / "applications" / "scripts" / "chat-console-state.js").read_text(encoding="utf-8")
        css = (root / "main_computer" / "web" / "applications" / "styles" / "chat-console.css").read_text(encoding="utf-8")

        self.assertIn("renderChatConsoleRagAtControls", chat_js)
        self.assertIn("/api/applications/chat-console/rag-assisted-thinking/evaluate", chat_js)
        self.assertIn("RAG-AT", chat_js)
        self.assertIn("rag_assisted_thinking_v4", chat_js)
        self.assertIn("rag_assisted_thinking", state_js)
        self.assertIn("AI activity", chat_js)
        self.assertIn("AI activity; Docker follows global executor setting", chat_js)
        self.assertIn('think: "low"', state_js)
        self.assertIn('raw.think || "low"', chat_js)
        self.assertNotIn("payload.docker_enabled", chat_js)
        self.assertNotIn("Docker verify", chat_js)
        self.assertIn(".chat-rag-at-controls", css)



if __name__ == "__main__":
    unittest.main()
