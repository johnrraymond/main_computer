from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHAT_CONSOLE_JS = REPO_ROOT / "main_computer" / "web" / "applications" / "scripts" / "chat-console.js"
CHAT_CONSOLE_CSS = REPO_ROOT / "main_computer" / "web" / "applications" / "styles" / "chat-console.css"


def test_chat_console_thinking_panel_fetches_local_ai_capacity() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "localCapacityByThread" in source
    assert "function chatConsoleThinkingThreadIds" in source
    assert "function renderChatConsoleLocalCapacityThinkingCard" in source
    assert "/api/applications/chat-console/ai/capacity" in source
    assert 'thread_id: threadId' in source
    assert 'max_local_concurrency: "1"' in source


def test_chat_console_thinking_panel_renders_capacity_card_before_activity_cards() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    current_message_index = source.index('title: "Current message"')
    capacity_card_index = source.index("grid.append(renderChatConsoleLocalCapacityThinkingCard(cell));")
    waiting_activity_index = source.index('title: "Waiting for activity"')

    assert current_message_index < capacity_card_index < waiting_activity_index
    assert 'category: "capacity"' in source
    assert "Local AI capacity" in source


def test_chat_console_capacity_card_has_dedicated_thinking_style() -> None:
    css = CHAT_CONSOLE_CSS.read_text(encoding="utf-8")

    assert '.chat-thinking-card[data-category="capacity"]' in css


def test_chat_console_capacity_card_labels_run_and_thread_separately() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "This chat is currently using the local AI slot. Run id and thread id are separate identifiers." in source
    assert "`run ${runId}`" in source
    assert "`thread ${threadId}`" in source
    assert "`active run ${activeRunId}`" in source
    assert "`active thread ${activeThreadId}`" in source

