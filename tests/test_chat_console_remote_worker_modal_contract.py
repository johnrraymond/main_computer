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


def test_chat_console_remote_worker_modal_is_phase_six_decision_panel_with_compact_assessment() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "Remote Worker control" in source
    assert "Remote Worker control" in source
    assert "Current Local AI Worker" in source
    assert "Remote Overflow Assessment" in source
    assert "Show diagnostic details" in source
    assert "compact read-only remote-overflow assessment" in source
    assert "records the selected intent separately from the modal close reason" in source
    assert "Full card details are collapsed below." in source
    assert "Blocking worker age" in source
    assert "Last checked" in source
    assert "No credits are held or spent" in source
    assert "Hub options route this blocked request through the Remote Hub" in source
    assert "data-chat-console-remote-worker-control-modal" in source
    assert "data-chat-remote-overflow-assessment-details" in source
    assert "data-chat-remote-overflow-assessment-details-summary" in source
    assert "data-chat-remote-overflow-assessment-grid" in source


def test_chat_console_remote_worker_modal_calls_read_only_assessment_endpoint() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "function chatConsoleFetchRemoteOverflowAssessment" in source
    assert "function chatConsoleBuildRemoteOverflowAssessmentPayload" in source
    assert "function chatConsoleRefreshRemoteOverflowAssessment" in source
    assert "/api/applications/chat-console/ai/remote-overflow/assess" in source
    assert "phase4_modal_assessment_cards" in source
    assert "remote_overflow_enabled: true" in source
    assert "no_credit_hold_created: true" in source
    assert "no_credit_spent: true" in source
    assert "real_remote_worker_contacted: false" in source
    assert "private_worker_prices_exposed: false" in source
    assert "chatConsoleRefreshRemoteOverflowAssessment({pendingRequest: boundPendingRequest, capacity})" in source
    assert "await chatConsoleRefreshRemoteOverflowAssessment({pendingRequest, capacity: snapshot})" in source
    assert "data-chat-remote-worker-status-card=\"assessment-summary\"" in source
    assert "chatConsoleRenderRemoteOverflowAssessmentCards" in source
    assert "chatConsoleRemoteOverflowAssessmentDetailsSummaryText" in source
    assert "chatRemoteOverflowAssessmentDetailsSummary" in source
    assert "lastAssessment" in source


def test_chat_console_remote_worker_modal_has_large_selectable_options() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "function chatConsoleChooseRemoteWorkerControlOption" in source
    assert "Wait for Available Local Worker" in source
    assert "Use Remote Worker This Once" in source
    assert "Use Remote Worker When Needed for This Chat" in source
    assert "Always Use Remote Worker When Local AI Is Busy" in source
    assert "Route this blocked request through the Remote Hub and enable the visible chat option beside the RAG controls." in source
    assert "Route this blocked request through the Remote Hub and record non-permanent global remote-worker intent for busy-local overflow." in source
    assert "The Worker app global setting is not connected yet, so no permanent Worker setting is changed in this phase." in source
    assert '[data-chat-remote-worker-option="wait_local"]' in source
    assert "button.dataset.chatRemoteWorkerOption = mode" in source
    assert "use_remote_once: \"remote_once\"" in source
    assert "use_remote_when_needed_for_chat: \"remote_when_needed_for_chat\"" in source
    assert "always_when_busy: \"remote_when_needed_global\"" in source


def test_chat_console_remote_worker_modal_records_durable_intent_separate_from_close_reason() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "function chatConsoleCanonicalRemoteWorkerIntentMode" in source
    assert "function chatConsoleBuildRemoteWorkerControlIntent" in source
    assert "function chatConsoleBuildRemoteWorkerControlCloseReason" in source
    assert 'mode: canonicalMode' in source
    assert 'scope: chatConsoleRemoteWorkerIntentScope(canonicalMode)' in source
    assert 'phase: "phase5_durable_intent"' in source
    assert "remote_worker_overflow_intent" in source
    assert "remote_worker_overflow_close_reason" in source
    assert "lastIntent" in source
    assert "lastCloseReason" in source
    assert "close_reason_details" in source
    assert "remote_execution_started: false" in source
    assert "mock_remote_submit_started: false" in source
    assert "credit_hold_created: false" in source
    assert "credit_spent: false" in source
    assert "function chatConsoleSubmitRemoteHubOnce" in source
    assert "/api/applications/chat-console/ai/remote-overflow/hub-submit" in source
    assert "remote_execution_source: \"remote_hub\"" in source
    assert "remote_worker_intent_mode: intentMode" in source
    assert "remote_hub_current_request: true" in source
    assert "authorization_granted_by_user: true" in source
    assert "credit_ready: true" in source
    assert "willing_worker_count: 1" in source
    assert "phase6-remote-hub-" in source


def test_chat_console_remote_worker_phase_five_intent_scope_rules() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert 'use_remote_once: "remote_once"' in source
    assert 'use_remote_when_needed_for_chat: "remote_when_needed_for_chat"' in source
    assert 'always_when_busy: "remote_when_needed_global"' in source
    assert 'if (mode === "remote_once" || mode === "wait_local") return "request";' in source
    assert 'if (mode === "remote_when_needed_for_chat") return "chat";' in source
    assert 'if (mode === "remote_when_needed_global") return "global";' in source
    assert 'canonicalMode === "remote_when_needed_for_chat"' in source
    assert 'canonicalMode === "remote_once"' in source
    assert 'chatConsoleRemoteWorkerControlState.globalWhenBusyIntent = {' in source
    assert 'permanent_worker_setting_changed: false' in source
    assert "localStorage" not in source


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
    assert "chatConsoleSetRemoteWorkerWhenBusyForChat(" in source
    assert "when_busy_for_chat_intent" in source
    assert "when_busy_for_chat_cleared_at" in source
    assert "chat_request_pane_checkbox" in source


def test_chat_console_remote_worker_wait_is_default_and_auto_selected() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "CHAT_CONSOLE_REMOTE_WORKER_CONTROL_CAPACITY_INTERVAL_MS = 2000" in source
    assert "function chatConsoleStartRemoteWorkerControlCapacityWatcher" in source
    assert "function chatConsoleRemoteWorkerControlCapacityTick" in source
    assert 'chatConsoleChooseRemoteWorkerControlOption("wait_local"' in source
    assert "auto-selected Wait for Available Local Worker because local AI became available; starting the pending request locally" in source
    assert "If you leave this open, this is selected automatically when the blocking local AI call disappears and the pending request starts locally." in source
    assert "escape_wait_local" in source
    assert "close_wait_local" in source
    assert "backdrop_wait_local" in source


def test_chat_console_remote_worker_preflight_runs_before_ai_evaluate_fetch() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    preflight_index = source.index("await chatConsoleMaybeShowRemoteWorkerControlForBusyLocal")
    fetch_index = source.index("const response = await fetch(endpoint")
    assert preflight_index < fetch_index



def test_chat_console_remote_worker_pending_request_ownership_and_lease_contract() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "pendingLocalRequests: new Map()" in source
    assert "localStartLease: null" in source
    assert "activePendingRequestId" in source
    assert "function chatConsoleRegisterPendingLocalAiRequest" in source
    assert "function chatConsoleTryAcquireLocalAiStartLease" in source
    assert "function chatConsoleWaitForPendingLocalAiStartLease" in source
    assert "pending_request_id" in source
    assert "data-chat-remote-worker-pending-request-footer" in source
    assert "Unable to acquire local AI start lease" in source
    assert "local AI start lease is held" in source
    assert "chatConsoleReleaseLocalAiStartLease" in source
    assert "chatConsoleForgetPendingLocalAiRequest" in source


def test_chat_console_remote_worker_backend_start_gate_is_used() -> None:
    manager_source = (REPO_ROOT / "main_computer" / "chat_ai_subprocess.py").read_text(encoding="utf-8")
    route_source = (REPO_ROOT / "main_computer" / "viewport_routes_chat_console.py").read_text(encoding="utf-8")
    rag_route_source = (REPO_ROOT / "main_computer" / "viewport_routes_rag_assisted_thinking.py").read_text(encoding="utf-8")

    assert "max_local_concurrency: int = 1" in manager_source
    assert "active_runs = self._live_active_snapshots_locked()" in manager_source
    assert "Local AI capacity is exhausted" in manager_source
    run_method = manager_source[manager_source.index("    def run("):]
    assert "subprocess.Popen" in run_method
    assert run_method.index("active_runs = self._live_active_snapshots_locked()") < run_method.index("subprocess.Popen")
    assert "max_local_concurrency=1" in route_source
    assert "max_local_concurrency=1" in rag_route_source


def test_chat_console_remote_worker_phase_six_remote_once_uses_remote_hub_as_normal_ai_execution() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")
    route_source = (REPO_ROOT / "main_computer" / "viewport_routes_chat_console.py").read_text(encoding="utf-8")
    dispatch_source = (REPO_ROOT / "main_computer" / "viewport_route_dispatch.py").read_text(encoding="utf-8")

    assert "RemoteHubExecutionGateway" in route_source
    assert "MockHubAIOverflowProvider().run" not in route_source
    assert "/api/applications/chat-console/ai/remote-overflow/hub-submit" in dispatch_source
    assert "function chatConsoleSubmitRemoteHubOnce" in source
    assert "function chatConsoleSetRemoteHubExecutionState" in source
    assert "function renderChatConsoleRemoteHubThinkingCard" in source
    assert "/api/applications/chat-console/ai/remote-overflow/hub-submit" in source
    assert "function chatConsoleRemoteWorkerIntentUsesRemoteHubForCurrentRequest" in source
    assert 'canonicalMode === "remote_once"' in source
    assert 'canonicalMode === "remote_when_needed_for_chat"' in source
    assert 'canonicalMode === "remote_when_needed_global"' in source
    assert "useRemoteHubForCurrentRequest = chatConsoleRemoteWorkerIntentUsesRemoteHubForCurrentRequest(remoteHubIntentMode)" in source
    assert "data = await chatConsoleSubmitRemoteHubOnce({pendingRequest: pendingLocalRequest, cell, payload, mode: remoteHubIntentMode})" in source
    assert "Remote Hub is working on this request." in source
    assert "Remote Hub response received." in source
    assert "Remote Hub AI" in source
    assert "no credits spent" in source
    assert "chatConsoleWaitForPendingLocalAiStartLease" in source
    evaluate_body = source[source.index("async function evaluateChatConsoleCell"):]
    assert evaluate_body.index("if (useRemoteHubForCurrentRequest)") < evaluate_body.index("chatConsoleWaitForPendingLocalAiStartLease")
    assert "chatConsoleHideRemoteWorkerControlModal(closeReason.reason)" in source
    assert source.index("chatConsoleHideRemoteWorkerControlModal(closeReason.reason)") < source.index("chatConsoleResolveRemoteWorkerControlChoice(choice, pendingRequest.id)")


def test_chat_console_remote_worker_modal_has_styles() -> None:
    css = CHAT_CONSOLE_CSS.read_text(encoding="utf-8")

    assert ".chat-remote-worker-control-backdrop" in css
    assert ".chat-remote-worker-control-modal" in css
    assert ".chat-remote-worker-control-status-grid" in css
    assert ".chat-remote-worker-control-status-card" in css
    assert ".chat-remote-worker-control-assessment-details" in css
    assert ".chat-remote-worker-control-assessment-details-summary" in css
    assert ".chat-remote-worker-control-option-grid" in css
    assert ".chat-remote-worker-control-option-card" in css
    assert ".chat-remote-worker-control-option-card.default" in css
    assert ".chat-remote-worker-chat-toggle.enabled" in css
    assert ".chat-remote-worker-control-pending-footer" in css


def test_chat_console_remote_worker_busy_preflight_waits_before_local_fetch() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "const choice = await new Promise((resolve) => {" in source
    assert "resolveChoice: resolve" in source
    assert "chatConsoleResolveRemoteWorkerControlChoice(choice, pendingRequest.id)" in source
    assert "function chatConsoleWaitForLocalAiCapacityAvailable" in source
    assert "function chatConsoleRemoteWorkerSleep" in source
    assert "local AI became available; acquiring pending request lease before starting locally" in source
    assert "local AI is busy; waiting on Remote Worker control before starting the pending local request" in source
    assert "waiting for local AI slot before starting the pending local request" in source
    assert "local AI became available after wait-local close; acquiring pending request lease before starting locally" in source

    preflight_index = source.index("remoteWorkerGate = await chatConsoleMaybeShowRemoteWorkerControlForBusyLocal")
    local_start_index = source.index("local AI became available; acquiring pending request lease before starting locally")
    fetch_index = source.index("const response = await fetch(endpoint")
    assert preflight_index < fetch_index
    assert local_start_index < fetch_index
