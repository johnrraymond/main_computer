from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from main_computer.cli import _config_from_args
from main_computer.config import DEFAULT_ENERGY_CHAIN_ID, DEFAULT_ENERGY_CHAIN_RPC_URL, MainComputerConfig
from main_computer.energy import EnergyCreditLedger
from main_computer.governance import bridge_governance_status
from main_computer.models import ChatMessage, ChatResponse
from main_computer.revision import DebugAssetRevisionControl, RevisionControl
from main_computer.viewport import APPLICATIONS_INDEX_HTML, DEBUG_GRAPHICAL_INDEX_HTML, DEBUG_TEXT_INDEX_HTML, ENERGY_INDEX_HTML, GRAPHICAL_INDEX_HTML, REVISION_INDEX_HTML, TEXT_INDEX_HTML, ViewportHandler, ViewportServer, _application_route_target, serve


class ViewportApplicationRouteTests(unittest.TestCase):
    def test_application_routes_default_to_calculator(self) -> None:
        self.assertEqual(_application_route_target("/applications"), "calculator")
        self.assertEqual(_application_route_target("/apps"), "calculator")
        self.assertEqual(_application_route_target("/app"), "calculator")
        self.assertEqual(_application_route_target("/applications/calculator"), "calculator")
        self.assertEqual(_application_route_target("/apps/calculator"), "calculator")
        self.assertEqual(_application_route_target("/app/calculator"), "calculator")
        self.assertIn('if (!parts.length) return "calculator";', APPLICATIONS_INDEX_HTML)
        self.assertIn('let currentApp = "calculator";', APPLICATIONS_INDEX_HTML)
        self.assertIn('window.history.pushState(state, "", nextPath)', APPLICATIONS_INDEX_HTML)
        self.assertIn('window.addEventListener("popstate"', APPLICATIONS_INDEX_HTML)
        self.assertIn('`/applications/${normalized}`', APPLICATIONS_INDEX_HTML)
        self.assertIn("local command runner", APPLICATIONS_INDEX_HTML)
        self.assertIn("stubbed", APPLICATIONS_INDEX_HTML)

    def test_calculator_mathics_endpoints_validate_and_ask_model(self) -> None:
        config = MainComputerConfig(workspace=Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config)

        class FakeProvider:
            name = "fake"
            model = "fake-model"

            def __init__(self) -> None:
                self.messages = []

            def chat(self, messages):
                self.messages = messages
                return ChatResponse(content="D[Sin[x]^2, x]", provider="fake", model="fake-model")

        class FakeComputer:
            provider = FakeProvider()

            def chat(self, prompt: str) -> ChatResponse:
                return ChatResponse(content="fallback", provider="fake", model="fake-model")

        server.computer = FakeComputer()  # type: ignore[assignment]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            empty_evaluate = Request(
                f"{base}/api/applications/calculator/mathics/evaluate",
                data=json.dumps({"expression": ""}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as empty_error:
                urlopen(empty_evaluate, timeout=5)
            self.assertEqual(empty_error.exception.code, 400)

            evaluate_request = Request(
                f"{base}/api/applications/calculator/mathics/evaluate",
                data=json.dumps({"expression": "2 + 2", "timeout_s": 1}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(evaluate_request, timeout=10) as response:
                evaluated = json.loads(response.read().decode("utf-8"))
            self.assertEqual(evaluated["expression"], "2 + 2")
            if evaluated["ok"]:
                self.assertIn("4", evaluated["result_text"])
            else:
                self.assertIn("Mathics", evaluated["error"])
                self.assertNotIn("not installed", evaluated["error"].lower())
                self.assertIn("python", evaluated["diagnostics"])

            ask_request = Request(
                f"{base}/api/applications/calculator/mathics/ask",
                data=json.dumps({"prompt": "differentiate sine squared"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(ask_request, timeout=5) as response:
                asked = json.loads(response.read().decode("utf-8"))
            self.assertTrue(asked["ok"])
            self.assertEqual(asked["expression"], "D[Sin[x]^2, x]")
            prompt_text = "\n".join(message.content for message in server.computer.provider.messages)
            self.assertIn("Mathics/Wolfram-language-style expression", prompt_text)
            self.assertNotIn("Translate this calculator word problem", prompt_text)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_calculator_qa_endpoint_validates_and_uses_context(self) -> None:
        config = MainComputerConfig(workspace=Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config)

        class FakeProvider:
            name = "fake"
            model = "fake-model"

            def __init__(self) -> None:
                self.messages = []

            def chat(self, messages):
                self.messages = messages
                return ChatResponse(content="The graph context explains the result.", provider="fake", model="fake-model")

        class FakeComputer:
            provider = FakeProvider()

            def chat(self, prompt: str) -> ChatResponse:
                return ChatResponse(content="fallback", provider="fake", model="fake-model")

        server.computer = FakeComputer()  # type: ignore[assignment]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            blank_request = Request(
                f"{base}/api/applications/calculator/qa",
                data=json.dumps({"question": "   "}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as blank_error:
                urlopen(blank_request, timeout=5)
            self.assertEqual(blank_error.exception.code, 400)

            qa_request = Request(
                f"{base}/api/applications/calculator/qa",
                data=json.dumps(
                    {
                        "question": "Why does this graph look like that?",
                        "context": {
                            "basic_expression": "2+2",
                            "basic_result": "4",
                            "graph_expression": "sin(x)",
                            "graph_status": "graphed sin(x)",
                            "graph_range": {"x_min": "-10", "x_max": "10", "y_min": "-5", "y_max": "5"},
                            "mathics_output": "Cos[x]",
                        },
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(qa_request, timeout=5) as response:
                answered = json.loads(response.read().decode("utf-8"))
            self.assertTrue(answered["ok"])
            self.assertEqual(answered["answer"], "The graph context explains the result.")
            prompt_text = "\n".join(message.content for message in server.computer.provider.messages)
            self.assertIn("Why does this graph look like that?", prompt_text)
            self.assertIn("Basic expression: 2+2", prompt_text)
            self.assertIn("Basic result: 4", prompt_text)
            self.assertIn("Graph expression f(x): sin(x)", prompt_text)
            self.assertIn("Mathics output: Cos[x]", prompt_text)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_chat_console_endpoints_validate_and_evaluate_ai(self) -> None:
        config = MainComputerConfig(workspace=Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config)
        real_computer = server.computer

        class FakeProvider:
            name = "fake"
            model = "fake-model"

            def __init__(self) -> None:
                self.messages = []

            def chat(self, messages):
                self.messages = messages
                return ChatResponse(content="hello\n```mathics\n2+2\n```", provider="fake", model="fake-model")

        class FakeComputer:
            provider = FakeProvider()
            catalog = real_computer.catalog

            def context_pack(self, prompt: str):
                return real_computer.context_pack(prompt)

            def chat_console_ai(self, source: str, attachments=None) -> ChatResponse:
                return self.provider.chat([ChatMessage(role="system", content="typed notebook console"), ChatMessage(role="user", content=source)])

            def chat(self, prompt: str, context_pack=None) -> ChatResponse:
                return ChatResponse(content="fallback", provider="fake", model="fake-model")

        server.computer = FakeComputer()  # type: ignore[assignment]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            for cell_type in ("ai", "terminal", "mathics"):
                request = Request(
                    f"{base}/api/applications/chat-console/cell/evaluate",
                    data=json.dumps({"cell": {"id": f"{cell_type}-1", "type": cell_type, "source": ""}}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=5)
                self.assertEqual(error.exception.code, 400)

            ai_request = Request(
                f"{base}/api/applications/chat-console/cell/evaluate",
                data=json.dumps({"cell": {"id": "ai-1", "type": "ai", "source": "make mathics"}}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(ai_request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            output = data["output_cell"]
            self.assertEqual(output["type"], "output")
            self.assertEqual(output["parts"][0]["kind"], "markdown")
            self.assertEqual(output["parts"][0]["snippets"][0]["kind"], "mathics")
            self.assertTrue(any("typed notebook console" in message.content for message in server.computer.provider.messages))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_chat_console_attachment_endpoint_rejects_path_traversal(self) -> None:
        config = MainComputerConfig(workspace=Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            request = Request(
                f"{base}/api/applications/chat-console/attachments",
                data=json.dumps({"filename": "../bad.png", "mime_type": "image/png", "data_base64": "AA=="}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=5)
            self.assertEqual(error.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_diagnostics_api_returns_failed_report_with_checks(self) -> None:
        def record_failure(runner) -> None:
            runner._record("forced-viewport-failure", False, "health", {"reason": "test"})

        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_root = Path(tmpdir)
            os.chdir(debug_root)
            try:
                config = MainComputerConfig(workspace=debug_root)
                server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    request = Request(
                        f"{base}/api/diagnostics",
                        data=json.dumps({"level": "health"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with patch("main_computer.diagnostics.DiagnosticRunner._run_health", record_failure):
                        with urlopen(request, timeout=10) as response:
                            self.assertEqual(response.status, 200)
                            report = json.loads(response.read().decode("utf-8"))

                    self.assertFalse(report["ok"])
                    self.assertEqual(report["checks"][0]["name"], "forced-viewport-failure")
                    report_path = debug_root / "diagnostics_output_viewport" / "health" / "diagnostics_report.json"
                    self.assertTrue(report_path.exists())
                    written = json.loads(report_path.read_text(encoding="utf-8"))
                    self.assertFalse(written["ok"])
                    self.assertEqual(written["checks"][0]["name"], "forced-viewport-failure")
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
                    server.server_close()
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
