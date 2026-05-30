from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHAT_CONSOLE_JS = REPO_ROOT / "main_computer" / "web" / "applications" / "scripts" / "chat-console.js"
CHAT_CONSOLE_CSS = REPO_ROOT / "main_computer" / "web" / "applications" / "styles" / "chat-console.css"


def test_chat_console_remote_worker_control_modal_hooks_busy_capacity() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "chatConsoleRemoteWorkerControlState" in source
    assert "function chatConsoleMaybeShowRemoteWorkerControlForBusyLocal" in source
    assert "function chatConsoleFetchLocalAiCapacityNow" in source
    assert "function chatConsoleShowRemoteWorkerControlModal" in source
    assert "/api/applications/chat-console/ai/capacity" in source
    assert 'max_local_concurrency: "1"' in source
    assert "chatConsoleShouldOpenRemoteWorkerControlForCapacity" in source
    assert "snapshot.busy === true" in source
    assert "snapshot.available_now === false" in source


def test_chat_console_remote_worker_modal_is_phase_one_only() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "Phase 1 busy-local trigger" in source
    assert "Remote Worker control" in source
    assert "No credits are checked, held, or spent in this Phase 1 modal." in source
    assert "No remote worker is contacted yet." in source
    assert "data-chat-console-remote-worker-control-modal" in source


def test_chat_console_remote_worker_preflight_runs_before_ai_evaluate_fetch() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    preflight_index = source.index("await chatConsoleMaybeShowRemoteWorkerControlForBusyLocal")
    fetch_index = source.index("const response = await fetch(endpoint")
    assert preflight_index < fetch_index


def test_chat_console_remote_worker_modal_has_styles() -> None:
    css = CHAT_CONSOLE_CSS.read_text(encoding="utf-8")

    assert ".chat-remote-worker-control-backdrop" in css
    assert ".chat-remote-worker-control-modal" in css
    assert ".chat-remote-worker-control-diagnostics" in css
    assert ".chat-remote-worker-control-actions" in css
