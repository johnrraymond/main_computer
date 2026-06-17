from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from main_computer.catalog import ProjectInfo
from main_computer.cli import build_parser
from main_computer.config import MainComputerConfig
from main_computer.models import ChatResponse
from main_computer.openclaw_bridge import OpenClawBridgeServer, serve


class _FakeCatalog:
    def list_projects(self) -> list[ProjectInfo]:
        root = Path("C:/workspace")
        return [
            ProjectInfo(
                name="alpha",
                path=root / "alpha",
                markers=("pyproject.toml",),
                child_count=2,
                file_count=3,
            )
        ]

    def inspect(self, name: str) -> ProjectInfo:
        if name != "alpha":
            raise KeyError("missing")
        root = Path("C:/workspace")
        return ProjectInfo(
            name="alpha",
            path=root / "alpha",
            markers=("pyproject.toml",),
            child_count=2,
            file_count=3,
        )


class _FakeComputer:
    def __init__(self) -> None:
        self.catalog = _FakeCatalog()

    def chat(self, prompt: str) -> ChatResponse:
        return ChatResponse(
            content=f"bridge saw: {prompt}",
            provider="fake",
            model="fake-model",
            metadata={"echo": prompt},
        )


class OpenClawBridgeTests(unittest.TestCase):
    def _start_server(self, *, token: str | None = None) -> tuple[OpenClawBridgeServer, threading.Thread]:
        config = MainComputerConfig(workspace=Path.cwd().parent, provider="ollama", model="gemma4:26b")
        with patch("main_computer.openclaw_bridge.MainComputer.build", return_value=_FakeComputer()):
            server = OpenClawBridgeServer(("127.0.0.1", 0), config, token=token, verbose=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def test_bridge_health_projects_and_chat(self) -> None:
        server, thread = self._start_server()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(f"{base}/v1/health", timeout=5) as response:
                health = json.loads(response.read().decode("utf-8"))
            self.assertTrue(health["ok"])
            self.assertEqual(health["service"], "main_computer_openclaw_bridge")
            self.assertFalse(health["auth_required"])

            with urlopen(f"{base}/v1/projects", timeout=5) as response:
                projects = json.loads(response.read().decode("utf-8"))
            self.assertEqual(projects["count"], 1)
            self.assertEqual(projects["projects"][0]["name"], "alpha")

            request = Request(
                f"{base}/v1/chat",
                data=json.dumps({"prompt": "hello bridge"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                chat = json.loads(response.read().decode("utf-8"))
            self.assertEqual(chat["content"], "bridge saw: hello bridge")
            self.assertEqual(chat["metadata"]["echo"], "hello bridge")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_bridge_requires_token_when_configured(self) -> None:
        server, thread = self._start_server(token="secret-token")
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with self.assertRaises(HTTPError) as missing:
                urlopen(f"{base}/v1/projects", timeout=5)
            self.assertEqual(missing.exception.code, 401)

            request = Request(
                f"{base}/v1/project/inspect",
                data=json.dumps({"name": "alpha"}).encode("utf-8"),
                headers={
                    "Authorization": "Bearer secret-token",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["name"], "alpha")
            self.assertEqual(payload["markers"], ["pyproject.toml"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_serve_rejects_non_loopback_bind_without_token(self) -> None:
        config = MainComputerConfig(workspace=Path.cwd().parent)
        with self.assertRaisesRegex(ValueError, "require --token"):
            serve(config, host="0.0.0.0", port=8767, token=None, verbose=False)

    def test_cli_parser_supports_openclaw_bridge_command(self) -> None:
        args = build_parser().parse_args(["openclaw-bridge", "--port", "9000", "--token", "abc"])
        self.assertEqual(args.command, "openclaw-bridge")
        self.assertEqual(args.port, 9000)
        self.assertEqual(args.token, "abc")

    def test_cli_parser_supports_openclaw_ops_commands(self) -> None:
        serve_args = build_parser().parse_args(["openclaw-ops", "serve", "--port", "9001", "--token", "abc"])
        self.assertEqual(serve_args.command, "openclaw-ops")
        self.assertEqual(serve_args.openclaw_ops_command, "serve")
        self.assertEqual(serve_args.port, 9001)
        self.assertEqual(serve_args.token, "abc")

        smoke_args = build_parser().parse_args(["openclaw-ops", "smoke", "--base-url", "http://127.0.0.1:9001"])
        self.assertEqual(smoke_args.command, "openclaw-ops")
        self.assertEqual(smoke_args.openclaw_ops_command, "smoke")
        self.assertEqual(smoke_args.base_url, "http://127.0.0.1:9001")


if __name__ == "__main__":
    unittest.main()
