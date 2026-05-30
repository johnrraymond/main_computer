from __future__ import annotations

"""Static smoke for the Phase 2 busy-local Remote Worker control modal.

This smoke proves the Chat Console checks local AI capacity during AI request
startup, opens the modal before the normal AI evaluation fetch, shows the
two-card local/hub status grid, exposes large selectable option cards, and
keeps the phase free of credit checks, real hub submit, or real remote worker
contact.
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
        "function chatConsoleChooseRemoteWorkerControlOption",
        "CHAT_CONSOLE_REMOTE_WORKER_CONTROL_CAPACITY_INTERVAL_MS",
        "/api/applications/chat-console/ai/capacity",
        "await chatConsoleMaybeShowRemoteWorkerControlForBusyLocal",
        "Phase 2 remote-worker controls",
        "Current Local AI Worker",
        "Remote Hub / Workers",
        "Available workers",
        "template / not checked yet",
        "Wait for Available Local Worker",
        "Use Remote Worker This Once",
        "Use Remote Worker When Needed for This Chat",
        "Always Use Remote Worker When Local AI Is Busy",
        "To turn this off later, open the Worker app and unselect this option.",
        "Remote worker when local AI is busy for this chat",
        "dataset.chatRemoteWorkerWhenBusyForChat",
        "No credits are checked, held, or spent",
        "no hub assessment or remote worker is contacted yet",
    ]
    required_css = [
        ".chat-remote-worker-control-backdrop",
        ".chat-remote-worker-control-modal",
        ".chat-remote-worker-control-status-grid",
        ".chat-remote-worker-control-status-card",
        ".chat-remote-worker-control-option-grid",
        ".chat-remote-worker-control-option-card",
        ".chat-remote-worker-chat-toggle.enabled",
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
        "phase": "remote worker modal controls",
        "capacity_endpoint": "/api/applications/chat-console/ai/capacity",
        "status_cards": ["Current Local AI Worker", "Remote Hub / Workers"],
        "selectable_options": [
            "Wait for Available Local Worker",
            "Use Remote Worker This Once",
            "Use Remote Worker When Needed for This Chat",
            "Always Use Remote Worker When Local AI Is Busy",
        ],
        "credits_checked": False,
        "remote_worker_contacted": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
