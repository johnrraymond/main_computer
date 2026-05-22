from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from main_computer.models import ChatMessage
from main_computer.providers.ollama import OllamaProvider, OllamaStreamTerminalError
from main_computer.providers.openai_provider import OpenAIProvider


class ProviderTests(unittest.TestCase):
    class FakeStreamingResponse:
        def __init__(self, lines):
            self.lines = lines

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            return iter(self.lines)

    def test_ollama_provider_posts_chat(self) -> None:
        calls = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps({"message": {"content": "hello"}}).encode("utf-8")

        def fake_urlopen(request, timeout):
            calls["url"] = request.full_url
            calls["timeout"] = timeout
            calls["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            response = OllamaProvider(model="gemma4").chat([ChatMessage("user", "hi")])

        self.assertEqual(response.content, "hello")
        self.assertEqual(response.provider, "ollama")
        self.assertTrue(calls["url"].endswith("/api/chat"))
        self.assertEqual(calls["timeout"], 600.0)
        self.assertEqual(calls["payload"]["model"], "gemma4")
        self.assertIs(calls["payload"]["think"], False)

    def test_ollama_provider_writes_stream_diagnostics(self) -> None:
        lines = [
            json.dumps({"message": {"thinking": "hmm"}}).encode("utf-8"),
            json.dumps({"message": {"content": "hello"}}).encode("utf-8"),
            json.dumps({"done": True, "message": {}}).encode("utf-8"),
        ]
        callback_events: list[dict[str, object]] = []

        def fake_urlopen(request, timeout):
            return self.FakeStreamingResponse(lines)

        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "session.log"
            provider = OllamaProvider(model="gemma4")
            provider.diagnostic_log_file = str(log_file)
            provider.diagnostic_run_id = "run-diag"
            provider.diagnostic_label = "test"
            provider.stream_callback = callback_events.append

            with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
                response = provider.chat([ChatMessage("user", "hi")])

            log_text = log_file.read_text(encoding="utf-8")

        self.assertEqual(response.content, "hello")
        self.assertEqual([event["type"] for event in callback_events], ["request_submitted", "response_opened", "thinking_delta", "content_delta"])
        thinking_event = next(event for event in callback_events if event["type"] == "thinking_delta")
        self.assertEqual(thinking_event["delta"], "hmm")
        self.assertIn("hmm", thinking_event["thinking_preview"])
        self.assertTrue(thinking_event["raw_thinking_exposed"])
        self.assertEqual(thinking_event["thinking_chars"], 3)
        self.assertIn("ollama urlopen starting", log_text)
        self.assertIn("ollama response opened", log_text)
        self.assertIn("ollama raw stream line", log_text)
        self.assertIn("ollama parsed stream line", log_text)
        self.assertIn("ollama model input payload", log_text)
        self.assertIn("ollama model raw stream line", log_text)
        self.assertIn("ollama model parsed stream object", log_text)
        self.assertIn("ollama model output thinking delta", log_text)
        self.assertIn("ollama model output content delta", log_text)
        self.assertIn("ollama model output final content", log_text)
        self.assertIn("ollama model output final thinking", log_text)
        self.assertIn("ollama stream callback attempt", log_text)
        self.assertIn("ollama stream callback ok", log_text)
        self.assertIn("ollama stream completed", log_text)

    def test_ollama_provider_stream_error_is_terminal_with_counters(self) -> None:
        lines = [
            json.dumps({"message": {"thinking": "hmm"}}).encode("utf-8"),
            json.dumps({"message": {"content": "{\"ok\""}}).encode("utf-8"),
            json.dumps({"error": "GGML_ASSERT(ctx->mem_buffer != NULL) failed"}).encode("utf-8"),
        ]
        callback_events: list[dict[str, object]] = []

        def fake_urlopen(request, timeout):
            return self.FakeStreamingResponse(lines)

        provider = OllamaProvider(model="gemma4")
        provider.stream_callback = callback_events.append

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            with self.assertRaises(OllamaStreamTerminalError) as caught:
                provider.chat([ChatMessage("user", "hi")])

        self.assertEqual(caught.exception.terminal_fault_type, "provider_stream_error")
        self.assertEqual(caught.exception.partial_content_chars, 5)
        self.assertEqual(caught.exception.partial_thinking_chars, 3)
        self.assertIn("GGML_ASSERT", caught.exception.terminal_fault_message)
        error_event = callback_events[-1]
        self.assertEqual(error_event["stream_event_type"], "stream_error")
        self.assertEqual(error_event["partial_content_chars"], 5)
        self.assertEqual(error_event["partial_thinking_chars"], 3)

    def test_ollama_provider_thinking_only_watchdog_is_terminal(self) -> None:
        class SlowThinkingResponse(self.FakeStreamingResponse):
            def __iter__(self):
                yield json.dumps({"message": {"thinking": "x" * 1000}}).encode("utf-8")
                time.sleep(0.01)
                yield json.dumps({"message": {"thinking": "more"}}).encode("utf-8")

        def fake_urlopen(request, timeout):
            return SlowThinkingResponse([])

        provider = OllamaProvider(model="gemma4", thinking_only_watchdog_s=0.001)

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            with self.assertRaises(OllamaStreamTerminalError) as caught:
                provider.chat([ChatMessage("user", "hi")])

        self.assertEqual(caught.exception.terminal_fault_type, "thinking_only_watchdog")
        self.assertIn("produced 0 final content chars", caught.exception.terminal_fault_message)

    def test_ollama_provider_content_stall_watchdog_is_terminal(self) -> None:
        class SlowStreamingResponse(self.FakeStreamingResponse):
            def __iter__(self):
                yield json.dumps({"message": {"content": "hello"}}).encode("utf-8")
                time.sleep(0.01)
                yield json.dumps({"message": {"thinking": "waiting"}}).encode("utf-8")

        def fake_urlopen(request, timeout):
            return SlowStreamingResponse([])

        provider = OllamaProvider(model="gemma4", content_stall_watchdog_s=0.001)

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            with self.assertRaises(OllamaStreamTerminalError) as caught:
                provider.chat([ChatMessage("user", "hi")])

        self.assertEqual(caught.exception.terminal_fault_type, "content_stall_watchdog")
        self.assertEqual(caught.exception.partial_content_chars, 5)

    def test_ollama_provider_limits_model_io_diagnostic_records(self) -> None:
        long_prompt = "PROMPT-START-" + ("åß∂ƒ🙂" * 1800) + "-PROMPT-END"
        long_content = "CONTENT-START-" + ("ગુજરાતી🙂" * 1600) + "-CONTENT-END"
        long_thinking = "THINKING-START-" + ("考える🙂" * 1600) + "-THINKING-END"
        lines = [
            json.dumps({"message": {"thinking": long_thinking}}, ensure_ascii=False).encode("utf-8"),
            json.dumps({"message": {"content": long_content}}, ensure_ascii=False).encode("utf-8"),
            json.dumps({"done": True, "message": {}}).encode("utf-8"),
        ]

        def fake_urlopen(request, timeout):
            return self.FakeStreamingResponse(lines)

        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "session.log"
            provider = OllamaProvider(model="gemma4")
            provider.diagnostic_log_file = str(log_file)
            provider.diagnostic_run_id = "run-long"
            provider.diagnostic_label = "byte-limit"

            with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
                response = provider.chat([ChatMessage("user", long_prompt)])

            log_bytes = log_file.read_bytes()
            log_text = log_bytes.decode("utf-8")

        self.assertEqual(response.content, long_content)
        self.assertEqual(response.metadata["thinking"], long_thinking)

        model_io_lines = [
            line
            for line in log_bytes.splitlines(keepends=True)
            if b"ollama model " in line
        ]
        self.assertGreaterEqual(len(model_io_lines), 7)
        for line in model_io_lines:
            self.assertLessEqual(len(line), 4096, line[:120])

        input_line = next(line.decode("utf-8") for line in model_io_lines if b"ollama model input payload" in line)
        content_line = next(line.decode("utf-8") for line in model_io_lines if b"ollama model output content delta" in line)
        thinking_line = next(line.decode("utf-8") for line in model_io_lines if b"ollama model output thinking delta" in line)
        final_content_line = next(line.decode("utf-8") for line in model_io_lines if b"ollama model output final content" in line)
        final_thinking_line = next(line.decode("utf-8") for line in model_io_lines if b"ollama model output final thinking" in line)

        self.assertIn("...", input_line)
        self.assertIn("PROMPT-START-", input_line)
        self.assertIn("-PROMPT-END", input_line)
        self.assertIn("...", content_line)
        self.assertIn("CONTENT-START-", content_line)
        self.assertIn("-CONTENT-END", content_line)
        self.assertIn("...", thinking_line)
        self.assertIn("THINKING-START-", thinking_line)
        self.assertIn("-THINKING-END", thinking_line)
        self.assertIn("-CONTENT-END", final_content_line)
        self.assertIn("-THINKING-END", final_thinking_line)
        self.assertIn("ollama model raw stream line", log_text)
        self.assertIn("ollama model parsed stream object", log_text)

    def test_ollama_provider_logs_nonstream_response_body_diagnostics(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps({"message": {"content": "hello"}}).encode("utf-8")

        def fake_urlopen(request, timeout):
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "session.log"
            provider = OllamaProvider(model="gemma4")
            provider.diagnostic_log_file = str(log_file)

            with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
                response = provider.chat([ChatMessage("user", "hi")])

            log_bytes = log_file.read_bytes()
            log_text = log_bytes.decode("utf-8")

        self.assertEqual(response.content, "hello")
        self.assertIn("ollama model nonstream response body", log_text)
        for line in log_bytes.splitlines(keepends=True):
            if b"ollama model " in line:
                self.assertLessEqual(len(line), 4096)

    def test_ollama_provider_emits_waiting_heartbeat_before_response_opens(self) -> None:
        lines = [
            json.dumps({"message": {"content": "hello"}}).encode("utf-8"),
            json.dumps({"done": True, "message": {}}).encode("utf-8"),
        ]
        callback_events: list[dict[str, object]] = []

        def fake_urlopen(request, timeout):
            time.sleep(0.035)
            return self.FakeStreamingResponse(lines)

        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "session.log"
            provider = OllamaProvider(model="gemma4", stream_heartbeat_interval_s=0.01)
            provider.diagnostic_log_file = str(log_file)
            provider.stream_callback = callback_events.append

            with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
                response = provider.chat([ChatMessage("user", "hi")])

            log_text = log_file.read_text(encoding="utf-8")

        self.assertEqual(response.content, "hello")
        event_types = [str(event.get("type") or "") for event in callback_events]
        self.assertIn("request_waiting", event_types)
        waiting_event = next(event for event in callback_events if event.get("type") == "request_waiting")
        self.assertIn("waiting for the HTTP response", str(waiting_event.get("status_preview") or ""))
        self.assertIn("ollama stream heartbeat started", log_text)
        self.assertIn("ollama stream heartbeat stopped", log_text)

    def test_ollama_provider_logs_stream_callback_failure_and_returns(self) -> None:
        lines = [
            json.dumps({"message": {"content": "hello"}}).encode("utf-8"),
            json.dumps({"done": True, "message": {}}).encode("utf-8"),
        ]

        def fake_urlopen(request, timeout):
            return self.FakeStreamingResponse(lines)

        def failing_callback(event: dict[str, object]) -> None:
            raise RuntimeError("callback boom")

        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "session.log"
            provider = OllamaProvider(model="gemma4")
            provider.diagnostic_log_file = str(log_file)
            provider.stream_callback = failing_callback

            with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
                response = provider.chat([ChatMessage("user", "hi")])

            log_text = log_file.read_text(encoding="utf-8")

        self.assertEqual(response.content, "hello")
        self.assertIn("ollama stream callback failed", log_text)
        self.assertIn("callback boom", log_text)

    def test_ollama_provider_uses_configured_timeout(self) -> None:
        calls = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps({"message": {"content": "hello"}}).encode("utf-8")

        def fake_urlopen(request, timeout):
            calls["timeout"] = timeout
            return FakeResponse()

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            OllamaProvider(model="gemma4", timeout_s=600.0).chat([ChatMessage("user", "hi")])

        self.assertEqual(calls["timeout"], 600.0)

    def test_ollama_provider_sends_generation_options(self) -> None:
        calls = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps({"message": {"content": "ready"}}).encode("utf-8")

        def fake_urlopen(request, timeout):
            calls["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            OllamaProvider(
                model="gemma4",
                options={"temperature": 0, "num_predict": 16},
            ).chat([ChatMessage("user", "hi")])

        self.assertEqual(calls["payload"]["options"], {"temperature": 0, "num_predict": 16})

    def test_ollama_provider_defaults_to_non_thinking_when_not_enabled(self) -> None:
        calls = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps({"message": {"content": "READY."}}).encode("utf-8")

        def fake_urlopen(request, timeout):
            calls["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            response = OllamaProvider(model="gemma4").chat([ChatMessage("user", "hi")])

        self.assertEqual(response.content, "READY.")
        self.assertIs(calls["payload"]["think"], False)
        self.assertEqual(response.metadata["thinking_state"], "off")
        self.assertEqual(response.metadata["think_source"], "default_non_thinking")
        self.assertTrue(response.metadata["think_default_applied"])

    def test_ollama_provider_preserves_explicit_thinking_enabled(self) -> None:
        calls = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps({"message": {"content": "READY.", "thinking": "hidden"}}).encode("utf-8")

        def fake_urlopen(request, timeout):
            calls["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            response = OllamaProvider(model="gemma4", think=True).chat([ChatMessage("user", "hi")])

        self.assertIs(calls["payload"]["think"], True)
        self.assertEqual(response.metadata["thinking"], "hidden")
        self.assertEqual(response.metadata["thinking_state"], "on")
        self.assertEqual(response.metadata["think_source"], "explicit")
        self.assertFalse(response.metadata["think_default_applied"])

    def test_ollama_provider_rejects_generated_tokens_without_visible_content(self) -> None:
        lines = [
            json.dumps(
                {
                    "done": True,
                    "done_reason": "length",
                    "eval_count": 160,
                    "message": {},
                }
            ).encode("utf-8"),
        ]

        def fake_urlopen(request, timeout):
            return self.FakeStreamingResponse(lines)

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            with self.assertRaises(OllamaStreamTerminalError) as caught:
                OllamaProvider(model="gemma4").chat([ChatMessage("user", "hi")])

        self.assertEqual(caught.exception.terminal_fault_type, "generated_tokens_no_visible_response")
        self.assertIn("no visible final content", caught.exception.terminal_fault_message)
        self.assertIn("160 generated token", caught.exception.terminal_fault_message)

    def test_ollama_provider_rejects_thinking_only_terminal_success(self) -> None:
        lines = [
            json.dumps({"message": {"thinking": "hidden"}, "done": False}).encode("utf-8"),
            json.dumps({"done": True, "done_reason": "stop", "eval_count": 8, "message": {}}).encode("utf-8"),
        ]

        def fake_urlopen(request, timeout):
            return self.FakeStreamingResponse(lines)

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            with self.assertRaises(OllamaStreamTerminalError) as caught:
                OllamaProvider(model="gemma4", think=True).chat([ChatMessage("user", "hi")])

        self.assertEqual(caught.exception.terminal_fault_type, "thinking_only_no_visible_final_response")
        self.assertEqual(caught.exception.partial_thinking_chars, len("hidden"))

    def test_ollama_provider_can_control_thinking(self) -> None:
        calls = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps({"message": {"content": "READY.", "thinking": "hidden"}}).encode("utf-8")

        def fake_urlopen(request, timeout):
            calls["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            response = OllamaProvider(model="gemma4", think=False).chat([ChatMessage("user", "hi")])

        self.assertEqual(calls["payload"]["think"], False)
        self.assertEqual(response.metadata["thinking"], "hidden")

    def test_ollama_provider_sends_string_thinking_level(self) -> None:
        calls = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps({"message": {"content": "READY."}}).encode("utf-8")

        def fake_urlopen(request, timeout):
            calls["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            OllamaProvider(model="gemma4", think="medium").chat([ChatMessage("user", "hi")])

        self.assertEqual(calls["payload"]["think"], "medium")

    def test_openai_provider_calls_responses(self) -> None:
        calls = {}

        class FakeResponses:
            def create(self, **kwargs):
                calls.update(kwargs)
                return SimpleNamespace(output_text="ok", id="resp_test")

        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.responses = FakeResponses()

        original_openai = sys.modules.get("openai")
        sys.modules["openai"] = SimpleNamespace(OpenAI=FakeClient)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            response = OpenAIProvider(model="gpt-test").chat(
                [
                    ChatMessage("system", "system"),
                    ChatMessage("user", "hello"),
                ]
            )
        if original_openai is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = original_openai

        self.assertEqual(response.content, "ok")
        self.assertEqual(response.provider, "openai")
        self.assertEqual(calls["model"], "gpt-test")
        self.assertEqual(calls["instructions"], "system")
        self.assertEqual(calls["input"], "hello")


if __name__ == "__main__":
    unittest.main()
