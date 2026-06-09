from __future__ import annotations

import inspect
from pathlib import Path

from main_computer import rag_text_console_control_surface_smoke as smoke
from main_computer.config import MainComputerConfig
from main_computer.text_console import (
    TEXT_CONSOLE_AI_SYSTEM_PROMPT,
    TextConsoleConfig,
    build_text_console_model_input,
)
from main_computer.viewport_routes_applications import ViewportApplicationRoutesMixin


def test_text_console_smoke_config_owns_repo_root_context(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    legacy_bogus_workspace = repo_root / "definitely-not-the-text-console-workspace"

    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("MAIN_COMPUTER_WORKSPACE", str(legacy_bogus_workspace))

    _computer, _context_pack, _messages, diagnostics = smoke.build_text_console_intended_model_input(
        request_text=smoke.DEFAULT_CHAT_CONSOLE_MOUNT_REQUEST,
        base_url="http://127.0.0.1:11434",
        model="gemma4:26b",
        timeout=120.0,
        think=False,
    )

    text_console_config = diagnostics["text_console_config"]
    context_text = diagnostics["context_pack_text"]

    assert Path(text_console_config["current_directory"]) == repo_root
    assert Path(text_console_config["context_root"]) == repo_root
    assert Path(text_console_config["working_directory"]) == repo_root
    assert diagnostics["legacy_main_computer_config_adapter"]["workspace"] == str(repo_root)
    assert str(legacy_bogus_workspace) not in context_text
    assert f"Workspace root: {repo_root}" in context_text
    assert "- main_computer: present" in context_text
    assert diagnostics["config_validation_failures"] == []
    assert diagnostics["text_console_model_input_failures"] == []


def test_text_console_config_adapter_overrides_legacy_workspace(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    legacy_bogus_workspace = repo_root / "definitely-not-the-text-console-workspace"

    monkeypatch.chdir(repo_root)

    base_config = MainComputerConfig(
        workspace=legacy_bogus_workspace,
        provider="ollama",
        model="gemma4:26b",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_timeout_s=120.0,
        ollama_think=False,
    )
    text_console_config = TextConsoleConfig.from_current_directory(
        repo_root,
        provider=base_config.provider,
        model=base_config.model,
        base_url=base_config.ollama_base_url,
        timeout=base_config.ollama_timeout_s,
        think=base_config.ollama_think,
    )

    adapted = text_console_config.to_legacy_main_computer_config(MainComputerConfig, base_config=base_config)

    assert adapted.workspace == repo_root
    assert adapted.provider == base_config.provider
    assert adapted.model == base_config.model
    assert adapted.ollama_base_url == base_config.ollama_base_url
    assert adapted.ollama_timeout_s == base_config.ollama_timeout_s
    assert adapted.ollama_think is False


def test_production_text_console_model_input_uses_text_console_root(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    legacy_bogus_workspace = repo_root / "definitely-not-the-text-console-workspace"

    monkeypatch.chdir(repo_root)

    base_config = MainComputerConfig(
        workspace=legacy_bogus_workspace,
        provider="ollama",
        model="gemma4:26b",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_timeout_s=120.0,
        ollama_think=False,
    )
    text_console_config = TextConsoleConfig.from_current_directory(
        repo_root,
        provider=base_config.provider,
        model=base_config.model,
        base_url=base_config.ollama_base_url,
        timeout=base_config.ollama_timeout_s,
        think=base_config.ollama_think,
    )

    model_input = build_text_console_model_input(
        text_console_config=text_console_config,
        source=smoke.DEFAULT_CHAT_CONSOLE_MOUNT_REQUEST,
        base_config=base_config,
    )

    context_text = model_input.context_pack.text
    assert model_input.legacy_config.workspace == repo_root
    assert str(legacy_bogus_workspace) not in context_text
    assert f"Workspace root: {repo_root}" in context_text
    assert TEXT_CONSOLE_AI_SYSTEM_PROMPT in [message.content for message in model_input.messages]
    assert any("preview-only local computer mount requests" in message.content for message in model_input.messages)


def test_smoke_uses_production_text_console_config_component():
    assert smoke.TextConsoleConfig is TextConsoleConfig


def test_api_chat_handler_uses_text_console_route_not_chat_console_route():
    source = inspect.getsource(ViewportApplicationRoutesMixin._handle_chat)

    assert "TextConsoleConfig.from_current_directory" in source
    assert "self.server.debug_root" in source
    assert "build_text_console_model_input" in source
    assert "chat_response_from_text_console_model_input" in source
    assert "self.server.computer.context_pack(prompt)" not in source
    assert "inline_test_provider" in source
