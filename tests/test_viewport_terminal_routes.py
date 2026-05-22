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


class ViewportTerminalRouteTests(unittest.TestCase):
    def test_terminal_suggest_endpoint_validates_and_never_executes(self) -> None:
        config = MainComputerConfig(workspace=Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config)

        class FakeComputer:
            suggestion_content = '{"command":"git status","description":"Show git status","risk":"read-only"}'

            def suggest_terminal_command(self, prompt: str, cwd: str = ".") -> ChatResponse:
                self.last_prompt = prompt
                self.last_cwd = cwd
                return ChatResponse(content=self.suggestion_content, provider="fake", model="fake-model")

        server.computer = FakeComputer()  # type: ignore[assignment]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        not_executed_path = Path.cwd() / "terminal_suggestion_should_not_execute.txt"
        if not_executed_path.exists():
            not_executed_path.unlink()
        try:
            base = f"http://127.0.0.1:{server.server_port}"

            empty_request = Request(
                f"{base}/api/applications/terminal/suggest",
                data=json.dumps({"prompt": "", "cwd": "."}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as empty_error:
                urlopen(empty_request, timeout=5)
            self.assertEqual(empty_error.exception.code, 400)

            suggest_request = Request(
                f"{base}/api/applications/terminal/suggest",
                data=json.dumps({"prompt": "show me git status", "cwd": "."}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(suggest_request, timeout=5) as response:
                suggestion = json.loads(response.read().decode("utf-8"))
            self.assertEqual(suggestion["command"], "git status")
            self.assertEqual(suggestion["description"], "Show git status")
            self.assertEqual(suggestion["risk"], "read-only")
            self.assertEqual(suggestion["provider"], "fake")
            self.assertEqual(suggestion["model"], "fake-model")
            self.assertEqual(Path(suggestion["cwd"]).resolve(), Path.cwd().resolve())
            self.assertEqual(Path(server.computer.last_cwd).resolve(), Path.cwd().resolve())

            server.computer.suggestion_content = json.dumps(
                {
                    "command": "New-Item -ItemType File terminal_suggestion_should_not_execute.txt",
                    "description": "Create a file",
                    "risk": "write",
                }
            )
            write_request = Request(
                f"{base}/api/applications/terminal/suggest",
                data=json.dumps({"prompt": "create a test file", "cwd": "."}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(write_request, timeout=5) as response:
                write_suggestion = json.loads(response.read().decode("utf-8"))
            self.assertIn("New-Item", write_suggestion["command"])
            self.assertFalse(not_executed_path.exists())

            server.computer.suggestion_content = '{"command":"git status\\nGet-ChildItem","description":"Bad","risk":"read-only"}'
            multiline_request = Request(
                f"{base}/api/applications/terminal/suggest",
                data=json.dumps({"prompt": "two commands", "cwd": "."}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as multiline_error:
                urlopen(multiline_request, timeout=5)
            self.assertEqual(multiline_error.exception.code, 502)

            server.computer.suggestion_content = "not json"
            invalid_json_request = Request(
                f"{base}/api/applications/terminal/suggest",
                data=json.dumps({"prompt": "bad json", "cwd": "."}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as invalid_json_error:
                urlopen(invalid_json_request, timeout=5)
            self.assertEqual(invalid_json_error.exception.code, 502)
        finally:
            if not_executed_path.exists():
                not_executed_path.unlink()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
