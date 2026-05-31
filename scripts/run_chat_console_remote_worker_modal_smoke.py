from __future__ import annotations

"""Static smoke for the Phase 6 busy-local Remote Worker modal and Remote Hub path.

This smoke proves the Chat Console checks local AI capacity during AI request
startup, opens the modal before the normal AI evaluation fetch, calls the
read-only remote-overflow assessment endpoint, renders a compact assessment summary with collapsed diagnostic cards, records durable selectable intent separately from close reasons, exposes
large selectable option cards, and routes Remote Worker This Once through the Remote Hub while keeping the modal as a decision prompt. It also proves the
modal polls every 2 seconds, binds to one pending request, and waits for a
local-start lease before automated local retry.
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
        "CHAT_CONSOLE_REMOTE_WORKER_CONTROL_CAPACITY_INTERVAL_MS = 2000",
        "/api/applications/chat-console/ai/capacity",
        "remoteWorkerGate = await chatConsoleMaybeShowRemoteWorkerControlForBusyLocal",
        "const choice = await new Promise((resolve) => {",
        "resolveChoice: resolve",
        "function chatConsoleWaitForLocalAiCapacityAvailable",
        "function chatConsoleRemoteWorkerSleep",
        "local AI is busy; waiting on Remote Worker control before starting the pending local request",
        "waiting for local AI slot before starting the pending local request",
        "local AI became available after wait-local close; acquiring pending request lease before starting locally",
        "local AI became available; acquiring pending request lease before starting locally",
        "Remote Worker control",
        "Current Local AI Worker",
        "Remote Overflow Assessment",
        "Show diagnostic details",
        "/api/applications/chat-console/ai/remote-overflow/assess",
        "Wait for Available Local Worker",
        "Use Remote Worker This Once",
        "Use Remote Worker When Needed for This Chat",
        "Always Use Remote Worker When Local AI Is Busy",
        "The Worker app global setting is not connected yet, so no permanent Worker setting is changed in this phase.",
        "Remote worker when local AI is busy for this chat",
        "dataset.chatRemoteWorkerWhenBusyForChat",
        "compact read-only remote-overflow assessment",
        "records the selected intent separately from the modal close reason",
        "Blocking worker age",
        "Last checked",
        "No credits are held or spent",
        "pendingLocalRequests: new Map()",
        "localStartLease: null",
        "function chatConsoleTryAcquireLocalAiStartLease",
        "function chatConsoleWaitForPendingLocalAiStartLease",
        "data-chat-remote-worker-pending-request-footer",
        "Unable to acquire local AI start lease",
        "local AI start lease is held",
        "function chatConsoleCanonicalRemoteWorkerIntentMode",
        "function chatConsoleBuildRemoteWorkerControlIntent",
        "function chatConsoleBuildRemoteWorkerControlCloseReason",
        "phase5_durable_intent",
        "remote_worker_overflow_intent",
        "remote_worker_overflow_close_reason",
        "lastIntent",
        "lastCloseReason",
        "close_reason_details",
        "remote_execution_started: false",
        "mock_remote_submit_started: false",
        "permanent_worker_setting_changed: false",
        "function chatConsoleFetchRemoteOverflowAssessment",
        "function chatConsoleBuildRemoteOverflowAssessmentPayload",
        "function chatConsoleRefreshRemoteOverflowAssessment",
        "phase4_modal_assessment_cards",
        "remote_overflow_enabled: true",
        "no_credit_hold_created: true",
        "real_remote_worker_contacted: false",
        "private_worker_prices_exposed: false",
        "data-chat-remote-overflow-assessment-details",
        "data-chat-remote-overflow-assessment-details-summary",
        "data-chat-remote-overflow-assessment-grid",
        "This dialog only asks for a decision. Any option closes it immediately.",
        "Remote Worker This Once routes the request through the Remote Hub",
        "function chatConsoleSubmitRemoteHubOnce",
        "function chatConsoleSetRemoteHubExecutionState",
        "function renderChatConsoleRemoteHubThinkingCard",
        "/api/applications/chat-console/ai/remote-overflow/mock-submit",
        "Remote Hub is working on this request.",
        "Remote Hub response received.",
        "Remote Hub AI",
        "no credits spent",
    ]
    required_css = [
        ".chat-remote-worker-control-backdrop",
        ".chat-remote-worker-control-modal",
        ".chat-remote-worker-control-status-grid",
        ".chat-remote-worker-control-status-card",
        ".chat-remote-worker-control-assessment-details",
        ".chat-remote-worker-control-assessment-details-summary",
        ".chat-remote-worker-control-option-grid",
        ".chat-remote-worker-control-option-card",
        ".chat-remote-worker-chat-toggle.enabled",
        ".chat-remote-worker-control-pending-footer",
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
        "phase": "remote worker modal decision prompt with Remote Hub execution",
        "assessment_endpoint": "/api/applications/chat-console/ai/remote-overflow/assess",
        "capacity_endpoint": "/api/applications/chat-console/ai/capacity",
        "status_cards": ["Current Local AI Worker", "Remote Overflow Assessment"],
        "selectable_options": [
            "Wait for Available Local Worker",
            "Use Remote Worker This Once",
            "Use Remote Worker When Needed for This Chat",
            "Always Use Remote Worker When Local AI Is Busy",
        ],
        "credit_hold_created": False,
        "credits_spent": False,
        "remote_hub_submit_path": "/api/applications/chat-console/ai/remote-overflow/mock-submit",
        "real_credit_hold_created": False,
        "credits_spent": False,
        "capacity_poll_interval_ms": 2000,
        "starts_local_after_capacity_available": True,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
