from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Sequence

from main_computer.chat_ai_subprocess import (
    ModelIOLoggingProvider,
    STREAM_ACTIVITY_BRIDGE_ATTR,
    _worker_stream_callback,
)
from main_computer.models import ChatMessage, ChatResponse
from main_computer.rag_assisted_thinking_v3 import ActivityAwareProvider, UnifiedRagActivityBus


class CaptureStdout:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def emit(self, message: dict[str, object]) -> None:
        self.messages.append(message)


class FakeStreamProvider:
    name = "fake-provider"
    model = "fake-model"

    def __init__(self) -> None:
        self.stream_callback = None

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        if self.stream_callback is not None:
            self.stream_callback(
                {
                    "type": "thinking_delta",
                    "provider": self.name,
                    "model": self.model,
                    "delta": "raw thought",
                    "thinking_preview": "raw thought",
                    "thinking_chars": 17,
                    "raw_thinking_exposed": True,
                }
            )
            self.stream_callback(
                {
                    "type": "content_delta",
                    "provider": self.name,
                    "model": self.model,
                    "delta": "Hello",
                    "content_preview": "Hello",
                    "content_chars": 5,
                }
            )
        return ChatResponse(content="Hello", provider=self.name, model=self.model, metadata={})


class CaptureBus:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def record(self, **event):
        self.events.append(event)
        return event


class ChatAiSubprocessStreamingTests(unittest.TestCase):
    def test_worker_stream_callback_emits_activity_for_model_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = CaptureStdout()
            provider = FakeStreamProvider()
            callback = _worker_stream_callback(
                stdout,
                log_file=str(Path(tmp) / "session.log"),
                run_id="run-stream",
                provider=provider,
                source="rag-assisted-thinking-v3",
                tags=["ai", "rag", "thinking", "local-ai", "model-call", "stream", "subprocess"],
            )

            self.assertTrue(getattr(callback, STREAM_ACTIVITY_BRIDGE_ATTR))
            callback(
                {
                    "type": "content_delta",
                    "provider": provider.name,
                    "model": provider.model,
                    "delta": "Hello",
                    "content_preview": "Hello",
                    "content_chars": 5,
                }
            )

            self.assertEqual(len(stdout.messages), 1)
            message = stdout.messages[0]
            self.assertEqual(message["type"], "activity")
            event = message["event"]
            self.assertEqual(event["source"], "rag-assisted-thinking-v3")
            self.assertEqual(event["title"], "Model text transmitted")
            self.assertEqual(event["data"]["latest_text"], "Hello")
            self.assertIn("stream", event["tags"])

    def test_worker_stream_callback_emits_raw_model_thinking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = CaptureStdout()
            provider = FakeStreamProvider()
            callback = _worker_stream_callback(
                stdout,
                log_file=str(Path(tmp) / "session.log"),
                run_id="run-thinking",
                provider=provider,
                source="rag-assisted-thinking-v3",
                tags=["ai", "rag", "thinking", "local-ai", "model-call", "stream", "subprocess"],
            )

            callback(
                {
                    "type": "thinking_delta",
                    "provider": provider.name,
                    "model": provider.model,
                    "delta": "raw thought",
                    "thinking_preview": "raw thought",
                    "thinking_chars": 11,
                    "raw_thinking_exposed": True,
                }
            )

            self.assertEqual(len(stdout.messages), 1)
            event = stdout.messages[0]["event"]
            self.assertEqual(event["title"], "Model thinking transmitted")
            self.assertEqual(event["message"], "raw thought")
            self.assertEqual(event["data"]["latest_text"], "raw thought")
            self.assertEqual(event["data"]["thinking_preview"], "raw thought")
            self.assertEqual(event["data"]["running_text"], "raw thought")
            self.assertTrue(event["data"]["raw_thinking_exposed"])



    def test_worker_stream_callback_emits_request_status_without_thinking_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = CaptureStdout()
            provider = FakeStreamProvider()
            callback = _worker_stream_callback(
                stdout,
                log_file=str(Path(tmp) / "session.log"),
                run_id="run-waiting",
                provider=provider,
                source="rag-assisted-thinking-v3",
                tags=["ai", "rag", "thinking", "local-ai", "model-call", "stream", "subprocess"],
            )

            callback(
                {
                    "type": "request_submitted",
                    "provider": provider.name,
                    "model": provider.model,
                    "status_preview": "Ollama request submitted; waiting for the HTTP response to open.",
                    "elapsed_ms": 12,
                }
            )

            self.assertEqual(len(stdout.messages), 1)
            event = stdout.messages[0]["event"]
            self.assertEqual(event["title"], "Model request submitted")
            self.assertIn("waiting for the HTTP response", event["message"])
            self.assertEqual(event["data"]["thinking_preview"], "")
            self.assertIn("waiting for the HTTP response", event["data"]["status_preview"])

    def test_worker_stream_callback_emits_waiting_heartbeat_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = CaptureStdout()
            provider = FakeStreamProvider()
            callback = _worker_stream_callback(
                stdout,
                log_file=str(Path(tmp) / "session.log"),
                run_id="run-heartbeat",
                provider=provider,
                source="rag-assisted-thinking-v3",
                tags=["ai", "rag", "thinking", "local-ai", "model-call", "stream", "subprocess"],
            )

            callback(
                {
                    "type": "request_waiting",
                    "provider": provider.name,
                    "model": provider.model,
                    "status_preview": "Ollama request is still waiting for the HTTP response to open.",
                    "elapsed_ms": 5000,
                }
            )

            self.assertEqual(len(stdout.messages), 1)
            event = stdout.messages[0]["event"]
            self.assertEqual(event["title"], "Model request still waiting")
            self.assertIn("still waiting", event["message"])
            self.assertEqual(event["data"]["thinking_preview"], "")
            self.assertIn("still waiting", event["data"]["status_preview"])
            self.assertEqual(event["data"]["running_text"], event["message"])

    def test_activity_aware_provider_chains_unmarked_previous_stream_callback(self) -> None:
        provider = FakeStreamProvider()
        previous_events: list[dict[str, object]] = []

        def previous_callback(event: dict[str, object]) -> None:
            previous_events.append(event)

        provider.stream_callback = previous_callback
        bus = CaptureBus()
        activity = UnifiedRagActivityBus(bus, run_id="run-chain", log_file="")
        wrapped = ActivityAwareProvider(provider, activity, run_id="run-chain")

        response = wrapped.chat([ChatMessage(role="user", content="hello")])

        self.assertEqual(response.content, "Hello")
        self.assertEqual(len(previous_events), 2)
        titles = [str(event.get("title") or "") for event in bus.events]
        self.assertIn("Model thinking transmitted", titles)
        self.assertIn("Model text transmitted", titles)

    def test_activity_aware_provider_defers_to_marked_subprocess_stream_bridge(self) -> None:
        provider = FakeStreamProvider()
        bridge_events: list[dict[str, object]] = []

        def subprocess_bridge(event: dict[str, object]) -> None:
            bridge_events.append(event)

        setattr(subprocess_bridge, STREAM_ACTIVITY_BRIDGE_ATTR, True)
        provider.stream_callback = subprocess_bridge
        bus = CaptureBus()
        activity = UnifiedRagActivityBus(bus, run_id="run-bridge", log_file="")
        wrapped = ActivityAwareProvider(provider, activity, run_id="run-bridge")

        wrapped.chat([ChatMessage(role="user", content="hello")])

        self.assertEqual(len(bridge_events), 2)
        titles = [str(event.get("title") or "") for event in bus.events]
        self.assertIn("AI model input prepared", titles)
        self.assertIn("AI RAG thinking call started", titles)
        self.assertIn("AI RAG thinking call completed", titles)
        self.assertNotIn("Model text transmitted", titles)

    def test_model_io_logging_provider_brackets_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "session.log"
            provider = FakeStreamProvider()
            wrapped = ModelIOLoggingProvider(
                provider,
                log_file=log_file,
                run_id="run-model-log",
                label="rag",
            )

            response = wrapped.chat([ChatMessage(role="user", content="hello")])
            log_text = log_file.read_text(encoding="utf-8")

        self.assertEqual(response.content, "Hello")
        self.assertEqual(provider.diagnostic_log_file, str(log_file))
        self.assertEqual(provider.diagnostic_run_id, "run-model-log")
        self.assertEqual(provider.diagnostic_label, "rag")
        self.assertIn("model input to provider", log_text)
        self.assertIn("model provider call starting", log_text)
        self.assertIn("provider_class: FakeStreamProvider", log_text)
        self.assertIn("model provider call returned", log_text)
        self.assertIn("model output from provider", log_text)


if __name__ == "__main__":
    unittest.main()
