from __future__ import annotations

"""Static smoke for the Phase 1 busy-local Remote Worker control modal.

This smoke proves the Chat Console checks local AI capacity during AI request
startup and can open the Phase 1 modal before the normal AI evaluation fetch.
It does not test credits, user choice, hub assessment, or mock submit.
"""

from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    js = (repo_root / "main_computer" / "web" / "applications" / "scripts" / "chat-console.js").read_text(encoding="utf-8")
    css = (repo_root / "main_computer" / "web" / "applications" / "styles" / "chat-console.css").read_text(encoding="utf-8")

    required_js = [
        "chatConsoleRemoteWorkerControlState",
        "function chatConsoleMaybeShowRemoteWorkerControlForBusyLocal",
        "function chatConsoleFetchLocalAiCapacityNow",
        "function chatConsoleShowRemoteWorkerControlModal",
        "/api/applications/chat-console/ai/capacity",
        "await chatConsoleMaybeShowRemoteWorkerControlForBusyLocal",
        "Phase 1 busy-local trigger",
        "Remote Worker control",
        "No credits are checked, held, or spent in this Phase 1 modal.",
        "No remote worker is contacted yet.",
    ]
    required_css = [
        ".chat-remote-worker-control-backdrop",
        ".chat-remote-worker-control-modal",
        ".chat-remote-worker-control-diagnostics",
        ".chat-remote-worker-control-actions",
    ]

    missing_js = [item for item in required_js if item not in js]
    missing_css = [item for item in required_css if item not in css]

    order_ok = True
    if not missing_js:
        order_ok = js.index("await chatConsoleMaybeShowRemoteWorkerControlForBusyLocal") < js.index("const response = await fetch(endpoint")
    if missing_js or missing_css or not order_ok:
        print({"ok": False, "missing_js": missing_js, "missing_css": missing_css, "preflight_before_fetch": order_ok})
        return 1

    print({
        "ok": True,
        "phase": "busy-local remote worker modal",
        "capacity_endpoint": "/api/applications/chat-console/ai/capacity",
        "credits_checked": False,
        "remote_worker_contacted": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
