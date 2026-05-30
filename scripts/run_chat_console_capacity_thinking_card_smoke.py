from __future__ import annotations

"""Smoke check that the chat-console thinking panel can surface local AI capacity.

This is a static smoke: it proves the frontend calls the backend capacity endpoint
and renders a Local AI capacity card in the existing Thinking card stack.
"""

from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    js = (repo_root / "main_computer" / "web" / "applications" / "scripts" / "chat-console.js").read_text(encoding="utf-8")
    css = (repo_root / "main_computer" / "web" / "applications" / "styles" / "chat-console.css").read_text(encoding="utf-8")

    required_js = [
        "localCapacityByThread",
        "function chatConsoleThinkingThreadIds",
        "function renderChatConsoleLocalCapacityThinkingCard",
        "/api/applications/chat-console/ai/capacity",
        "grid.append(renderChatConsoleLocalCapacityThinkingCard(cell));",
        "Local AI capacity",
        "This chat is currently using the local AI slot. Run id and thread id are separate identifiers.",
        "`run ${runId}`",
        "`thread ${threadId}`",
        "`active run ${activeRunId}`",
    ]
    missing_js = [item for item in required_js if item not in js]
    missing_css = ['.chat-thinking-card[data-category="capacity"]'] if '.chat-thinking-card[data-category="capacity"]' not in css else []

    if missing_js or missing_css:
        print({"ok": False, "missing_js": missing_js, "missing_css": missing_css})
        return 1

    print({
        "ok": True,
        "capacity_endpoint": "/api/applications/chat-console/ai/capacity",
        "renders_card": "Local AI capacity",
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
