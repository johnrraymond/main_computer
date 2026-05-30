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


def test_chat_console_remote_worker_modal_is_phase_two_control_panel() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "Phase 2 remote-worker controls" in source
    assert "Remote Worker control" in source
    assert "Current Local AI Worker" in source
    assert "Remote Hub / Workers" in source
    assert "Hub worker information will appear here as the overflow pathway matures." in source
    assert "Phase 2 records modal choices only." in source
    assert "No credits are checked, held, or spent" in source
    assert "no hub assessment or remote worker is contacted yet." in source
    assert "data-chat-console-remote-worker-control-modal" in source


def test_chat_console_remote_worker_modal_has_large_selectable_options() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "function chatConsoleChooseRemoteWorkerControlOption" in source
    assert "Wait for Available Local Worker" in source
    assert "Use Remote Worker This Once" in source
    assert "Use Remote Worker When Needed for This Chat" in source
    assert "Always Use Remote Worker When Local AI Is Busy" in source
    assert "To turn this off later, open the Worker app and unselect this option." in source
    assert '[data-chat-remote-worker-option="wait_local"]' in source
    assert "button.dataset.chatRemoteWorkerOption = mode" in source
    assert "mode === \"use_remote_once\"" in source
    assert "mode === \"use_remote_when_needed_for_chat\"" in source
    assert "mode === \"always_when_busy\"" in source


def test_chat_console_remote_worker_chat_choice_is_visible_and_reversible_like_rag() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "function chatConsoleRemoteWorkerWhenBusyForChatEnabled" in source
    assert "function chatConsoleSetRemoteWorkerWhenBusyForChat" in source
    assert "remote_worker_options" in source
    assert "remote_worker_options: chatConsoleRemoteWorkerOptions()" in source
    assert "chat-remote-worker-chat-toggle" in source
    assert "Remote worker when local AI is busy for this chat" in source
    assert "Use remote worker overflow when local AI is busy for this chat. Uncheck to back out." in source
    assert "remoteWorkerToggle.checked = remoteWorkerChatEnabled" in source
    assert "chatConsoleSetRemoteWorkerWhenBusyForChat(remoteWorkerToggle.checked" in source


def test_chat_console_remote_worker_wait_is_default_and_auto_selected() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "CHAT_CONSOLE_REMOTE_WORKER_CONTROL_CAPACITY_INTERVAL_MS" in source
    assert "function chatConsoleStartRemoteWorkerControlCapacityWatcher" in source
    assert "function chatConsoleRemoteWorkerControlCapacityTick" in source
    assert 'chatConsoleChooseRemoteWorkerControlOption("wait_local"' in source
    assert "auto-selected Wait for Available Local Worker because local AI became available" in source
    assert "This is also selected automatically when the blocking local AI call disappears." in source
    assert "escape_wait_local" in source
    assert "close_wait_local" in source
    assert "backdrop_wait_local" in source


def test_chat_console_remote_worker_preflight_runs_before_ai_evaluate_fetch() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    preflight_index = source.index("await chatConsoleMaybeShowRemoteWorkerControlForBusyLocal")
    fetch_index = source.index("const response = await fetch(endpoint")
    assert preflight_index < fetch_index


def test_chat_console_remote_worker_modal_has_styles() -> None:
    css = CHAT_CONSOLE_CSS.read_text(encoding="utf-8")

    assert ".chat-remote-worker-control-backdrop" in css
    assert ".chat-remote-worker-control-modal" in css
    assert ".chat-remote-worker-control-status-grid" in css
    assert ".chat-remote-worker-control-status-card" in css
    assert ".chat-remote-worker-control-option-grid" in css
    assert ".chat-remote-worker-control-option-card" in css
    assert ".chat-remote-worker-control-option-card.default" in css
    assert ".chat-remote-worker-chat-toggle.enabled" in css
