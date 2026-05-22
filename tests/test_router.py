from __future__ import annotations

from collections.abc import Sequence
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from main_computer.config import DEFAULT_OLLAMA_THINK, MainComputerConfig
from main_computer.models import ChatAttachment, ChatMessage, ChatResponse
from main_computer.providers.base import LLMProvider
from main_computer.providers.hub import HubProvider
from main_computer.providers.ollama import OllamaProvider
from main_computer.router import MainComputer
from main_computer.catalog import ProjectCatalog


class FakeProvider(LLMProvider):
    name = "fake"
    model = "fake-model"

    def __init__(self, content: str = "ok") -> None:
        self.messages: Sequence[ChatMessage] = []
        self.content = content

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.messages = messages
        return ChatResponse(self.content, self.name, self.model)


class RouterTests(unittest.TestCase):
    def test_router_adds_workspace_context(self) -> None:
        root = Path.cwd().parent
        provider = FakeProvider()
        config = MainComputerConfig(workspace=root)
        computer = MainComputer(config, ProjectCatalog(root), provider)

        response = computer.chat("hello")

        self.assertEqual(response.content, "ok")
        self.assertTrue(any("Deterministic workspace context pack:" in msg.content for msg in provider.messages))
        self.assertTrue(any("Priority main computer projects:" in msg.content for msg in provider.messages))
        self.assertIn("workspace_context", response.metadata)
        self.assertGreater(response.metadata["workspace_context"]["manifest_chars"], 0)
        self.assertEqual(provider.messages[-1].content, "hello")

    def test_router_uses_query_specific_context_pack(self) -> None:
        root = Path.cwd().parent
        provider = FakeProvider()
        config = MainComputerConfig(workspace=root)
        computer = MainComputer(config, ProjectCatalog(root), provider)

        response = computer.chat("load the todo and find viewport.py")

        context = "\n".join(message.content for message in provider.messages if message.role == "system")
        self.assertIn("main_computer_test/main_computer/viewport.py", context)
        self.assertIn("TODO.md", context)
        evidence_paths = {item["path"] for item in response.metadata["workspace_context"]["evidence"]}
        self.assertTrue(any(path.endswith("TODO.md") for path in evidence_paths))
        self.assertTrue(any(path.endswith("viewport.py") for path in evidence_paths))

    def test_config_reads_ollama_timeout_from_env(self) -> None:
        with patch.dict("os.environ", {"MAIN_COMPUTER_OLLAMA_TIMEOUT_S": "600"}, clear=True):
            config = MainComputerConfig.from_env()

        self.assertEqual(config.ollama_timeout_s, 600.0)
        self.assertEqual(config.ollama_think, DEFAULT_OLLAMA_THINK)
        self.assertEqual(config.patch_level, "0.1.0")

    def test_config_reads_ollama_think_from_env(self) -> None:
        cases = {
            "off": False,
            "false": False,
            "0": False,
            "no": False,
            "on": True,
            "true": True,
            "1": True,
            "yes": True,
            "low": "low",
            "medium": "medium",
            "high": "high",
            "none": None,
            "null": None,
            "default-empty": None,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                with patch.dict("os.environ", {"MAIN_COMPUTER_OLLAMA_THINK": raw}, clear=False):
                    config = MainComputerConfig.from_env()
                self.assertEqual(config.ollama_think, expected)

    def test_config_defaults_ollama_think_to_false(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            config = MainComputerConfig.from_env()

        self.assertIs(config.ollama_think, False)

    def test_config_allows_patch_level_override(self) -> None:
        with patch.dict("os.environ", {"MAIN_COMPUTER_PATCH_LEVEL": "0.1.1-test"}, clear=False):
            config = MainComputerConfig.from_env()

        self.assertEqual(config.patch_level, "0.1.1-test")

    def test_config_reads_rag_docker_kill_switch_from_env(self) -> None:
        with patch.dict("os.environ", {"MAIN_COMPUTER_RAG_DOCKER_ENABLED": "0"}, clear=True):
            config = MainComputerConfig.from_env()

        self.assertFalse(config.rag_docker_enabled)

    def test_router_build_passes_ollama_timeout(self) -> None:
        root = Path.cwd().parent
        config = MainComputerConfig(workspace=root, provider="ollama", model="gemma4:26b", ollama_timeout_s=450.0)
        computer = MainComputer.build(config)

        self.assertEqual(getattr(computer.provider, "timeout_s"), 450.0)

    def test_router_build_passes_default_ollama_think(self) -> None:
        root = Path.cwd().parent
        config = MainComputerConfig(workspace=root, provider="ollama")
        computer = MainComputer.build(config)

        self.assertIsInstance(computer.provider, OllamaProvider)
        self.assertIs(getattr(computer.provider, "think"), False)

    def test_config_reads_hub_insecure_dev_network_opt_in_from_env(self) -> None:
        with patch.dict("os.environ", {"MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK": "1"}, clear=True):
            config = MainComputerConfig.from_env()

        self.assertTrue(config.hub_allow_insecure_dev_network)

    def test_router_build_passes_hub_insecure_dev_network_opt_in(self) -> None:
        root = Path.cwd().parent
        config = MainComputerConfig(
            workspace=root,
            provider="hub",
            hub_url="http://hub:8770",
            hub_allow_insecure_dev_network=True,
        )
        computer = MainComputer.build(config)

        self.assertIsInstance(computer.provider, HubProvider)
        self.assertTrue(getattr(computer.provider, "allow_insecure_dev_network"))

    def test_router_auto_includes_missing_guidance_in_workspace_context(self) -> None:
        root = Path.cwd().parent
        provider = FakeProvider()
        config = MainComputerConfig(workspace=root)
        computer = MainComputer(config, ProjectCatalog(root), provider)

        response = computer.chat("hello")

        context = "\n".join(message.content for message in provider.messages if message.role == "system")
        self.assertIn("main_computer_test/missing.txt", context)
        self.assertIn("Companion gap inventory for the Main Computer User Requirements Document.", context)
        evidence_paths = {item["path"] for item in response.metadata["workspace_context"]["evidence"]}
        self.assertIn("main_computer_test/missing.txt", evidence_paths)

    def test_router_suggest_terminal_command_requests_json_from_provider(self) -> None:
        root = Path.cwd().parent
        provider = FakeProvider('{"command":"git status","description":"Show git status","risk":"read-only"}')
        config = MainComputerConfig(workspace=root)
        computer = MainComputer(config, ProjectCatalog(root), provider)

        response = computer.suggest_terminal_command("show me git status", cwd=str(root))

        self.assertIn('"command":"git status"', response.content)
        self.assertEqual(response.provider, "fake")
        self.assertTrue(any("Return JSON only" in msg.content for msg in provider.messages))
        self.assertTrue(any("Working directory:" in msg.content for msg in provider.messages))
        self.assertTrue(any("User request: show me git status" in msg.content for msg in provider.messages))
        self.assertIn("workspace_context", response.metadata)

    def test_router_chat_console_ai_uses_notebook_prompt(self) -> None:
        root = Path.cwd().parent
        provider = FakeProvider("notebook ok")
        config = MainComputerConfig(workspace=root)
        computer = MainComputer(config, ProjectCatalog(root), provider)

        response = computer.chat_console_ai("make a command")

        self.assertEqual(response.content, "notebook ok")
        prompt_text = "\n".join(message.content for message in provider.messages)
        self.assertIn("typed notebook console", prompt_text)
        self.assertIn("Do not instruct the system to auto-run terminal commands", prompt_text)
        self.assertIn("workspace_context", response.metadata)

    def test_ollama_provider_keeps_text_only_payload_shape(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"message":{"content":"ok"}}'

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            response = OllamaProvider(model="fake").chat([ChatMessage(role="user", content="hello")])

        self.assertEqual(response.content, "ok")
        self.assertEqual(captured["payload"]["messages"], [{"role": "user", "content": "hello"}])

    def test_ollama_provider_adds_image_attachments_to_payload(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"message":{"content":"ok"}}'

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        message = ChatMessage(
            role="user",
            content="describe",
            attachments=[
                ChatAttachment(id="img", filename="x.png", mime_type="image/png", data_base64="data:image/png;base64,abcd", kind="image")
            ],
        )
        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            OllamaProvider(model="fake").chat([message])

        self.assertEqual(captured["payload"]["messages"][0]["images"], ["abcd"])


if __name__ == "__main__":
    unittest.main()
