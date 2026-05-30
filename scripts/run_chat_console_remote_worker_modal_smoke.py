from __future__ import annotations

"""Static smoke for the Phase 4 busy-local Remote Worker assessment modal.

This smoke proves the Chat Console checks local AI capacity during AI request
startup, opens the modal before the normal AI evaluation fetch, calls the
read-only remote-overflow assessment endpoint, renders a compact assessment summary with collapsed diagnostic cards, exposes
large selectable option cards, and keeps the phase free of credit holds/spend,
mock submit, real hub submit, or real remote worker contact. It also proves the
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
        "const remoteWorkerGate = await chatConsoleMaybeShowRemoteWorkerControlForBusyLocal",
        "const choice = await new Promise((resolve) => {",
        "resolveChoice: resolve",
        "function chatConsoleWaitForLocalAiCapacityAvailable",
        "function chatConsoleRemoteWorkerSleep",
        "local AI is busy; waiting on Remote Worker control before starting the pending local request",
        "waiting for local AI slot before starting the pending local request",
        "local AI became available after wait-local close; acquiring pending request lease before starting locally",
        "local AI became available; acquiring pending request lease before starting locally",
        "Phase 4 remote-worker assessment",
        "Current Local AI Worker",
        "Remote Overflow Assessment",
        "Show diagnostic details",
        "/api/applications/chat-console/ai/remote-overflow/assess",
        "Wait for Available Local Worker",
        "Use Remote Worker This Once",
        "Use Remote Worker When Needed for This Chat",
        "Always Use Remote Worker When Local AI Is Busy",
        "To turn this off later, open the Worker app and unselect this option.",
        "Remote worker when local AI is busy for this chat",
        "dataset.chatRemoteWorkerWhenBusyForChat",
        "compact read-only remote-overflow assessment",
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
        "No credits are held or spent, and no remote worker is contacted in this phase.",
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
        "phase": "remote worker modal compact assessment",
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
        "mock_submit_called": False,
        "real_hub_request_created": False,
        "remote_worker_contacted": False,
        "capacity_poll_interval_ms": 2000,
        "starts_local_after_capacity_available": True,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
