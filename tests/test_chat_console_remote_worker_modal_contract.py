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
    assert "backend_worker_paid_overflow_context" in source
    assert "browser_estimate" in source
    assert "estimated_input_tokens: paidOverflow.estimatedInputTokens" in source
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
    assert "function chatConsoleFetchRemoteHubReadiness" in source
    assert "function chatConsoleRefreshRemoteHubReadiness" in source
    assert "function chatConsolePaidOverflowReadinessCard" in source
    assert "function chatConsolePaidOverflowReadinessReady" in source
    assert "/api/applications/chat-console/ai/remote-overflow/hub-submit" in source
    assert "/api/applications/chat-console/ai/remote-overflow/hub-readiness" in source
    assert "remote_execution_source: \"remote_hub\"" in source
    assert "remote_worker_intent_mode: intentMode" in source
    assert "remote_hub_current_request: true" in source
    assert "authorization_granted_by_user: true" in source
    assert "credit_ready: Boolean(assessmentPayload.credit_ready)" in source
    assert "willing_worker_count: assessmentPayload.credit_ready ? 1 : 0" in source
    assert "phase6-remote-hub-" in source


def test_chat_console_paid_overflow_readiness_rechecks_stay_silent_after_first_resolved_state() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "function chatConsolePaidOverflowReadinessHasResolved" in source
    assert "function chatConsolePaidOverflowReadinessStateChanged" in source
    assert "const hadResolvedReadiness = chatConsolePaidOverflowReadinessHasResolved(previousReadiness);" in source
    assert "if (!hadResolvedReadiness) {\n        chatConsoleUpdatePaidOverflowReadinessCard(readiness, paidOverflow, {phase: \"checking\", generation});\n      }" in source
    assert "chatConsolePaidOverflowReadinessStateChanged(currentReadiness, nextReadiness, paidOverflow)" in source
    assert "chatConsoleRemoteWorkerControlState.lastHubReadiness = nextReadiness;" in source
    assert 'if (phase === "checking") return "checking";' not in source


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
    assert "backend_worker_paid_overflow_context" in source
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


def test_chat_console_paid_overflow_uses_backend_policy_and_multisession_credit_preflight() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")
    route_source = (REPO_ROOT / "main_computer" / "viewport_routes_chat_console.py").read_text(encoding="utf-8")
    energy_source = (REPO_ROOT / "main_computer" / "viewport_routes_energy.py").read_text(encoding="utf-8")
    dispatch_source = (REPO_ROOT / "main_computer" / "viewport_route_dispatch.py").read_text(encoding="utf-8")

    assert "localStorage" not in source
    assert "main-computer-worker-bridge-readiness-v1" not in source
    assert "main-computer-worker-settings-v4" not in source
    assert "chatConsoleWorkerPaidOverflowContext" in source
    assert "backend_worker_paid_overflow_context" in source
    assert "payment_authorization" in source
    assert "chatConsoleRefreshRemoteHubReadiness({pendingRequest: request})" in source
    assert "Paid overflow readiness did not pass." in source
    assert "remote_overflow_enabled: true" not in source
    assert 'submit_body["remote_overflow_enabled"] = True' not in route_source
    assert 'submit_body["credit_ready"] = True' not in route_source

    assert "_chat_console_backend_paid_overflow_context" in route_source
    assert "_chat_console_enrich_remote_overflow_body_with_backend_context" in route_source
    assert "estimate_remote_request" in route_source
    assert "worker_settings.json" in energy_source
    assert "_handle_worker_settings_load" in energy_source
    assert "_handle_worker_settings_save" in energy_source
    assert '"/api/applications/worker/settings"' in dispatch_source


def test_chat_console_paid_overflow_modal_checks_hub_multisession_key_before_paid_options() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")
    route_source = (REPO_ROOT / "main_computer" / "viewport_routes_chat_console.py").read_text(encoding="utf-8")
    dispatch_source = (REPO_ROOT / "main_computer" / "viewport_route_dispatch.py").read_text(encoding="utf-8")
    provider_source = (REPO_ROOT / "main_computer" / "providers" / "hub.py").read_text(encoding="utf-8")
    hub_source = (REPO_ROOT / "main_computer" / "hub.py").read_text(encoding="utf-8")

    assert "Paid overflow readiness" in source
    assert "Multi-session key usable" in source
    assert "Hub reachable" in source
    assert "No credits are held or spent by this modal or by Hub readiness checks." in source
    assert "data-chat-paid-overflow-readiness-card" in source
    assert "chatPaidOverflowReadinessCheck" in source
    assert "data-chat-remote-worker-paid-option" in source
    assert 'button.disabled = true' in source
    assert "chatConsoleRefreshRemoteHubReadiness({pendingRequest: boundPendingRequest})" in source
    assert "await chatConsoleRefreshRemoteHubReadiness({pendingRequest})" in source
    assert "chatConsolePaidOverflowReadinessReady(paidOverflowReadiness)" in source
    assert "function chatConsoleSavePaidOverflowSetting" in source
    assert 'changed_fields: ["remoteEnabled"]' in source
    assert 'data-chat-paid-overflow-setting-toggle' in source
    assert 'chatConsoleRefreshRemoteHubReadiness({force: true})' in source
    assert "valid: False" not in source

    assert "def _handle_chat_console_remote_overflow_hub_readiness" in route_source
    assert "_chat_console_backend_hub_readiness(body)" in route_source
    assert "provider.validate_multisession_key(enriched)" in route_source
    assert "/api/applications/chat-console/ai/remote-overflow/hub-readiness" in dispatch_source
    assert "def validate_multisession_key" in provider_source
    assert "/api/hub/v1/credits/multisession-keys/validate" in provider_source
    assert "def _handle_multisession_key_validate" in hub_source
    assert 'path == "/api/hub/v1/credits/multisession-keys/validate"' in hub_source


def test_chat_console_remote_worker_modal_has_styles() -> None:
    css = CHAT_CONSOLE_CSS.read_text(encoding="utf-8")

    assert ".chat-remote-worker-control-backdrop" in css
    assert ".chat-remote-worker-control-modal" in css
    assert ".chat-remote-worker-control-status-grid" in css
    assert ".chat-remote-worker-control-status-card" in css
    assert ".chat-remote-worker-control-assessment-details" in css
    assert ".chat-remote-worker-control-assessment-details-summary" in css
    assert ".chat-remote-worker-control-readiness-card" in css
    assert ".chat-remote-worker-control-readiness-row" in css
    assert ".chat-remote-worker-control-readiness-metrics" in css
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


def test_chat_console_paid_overflow_readiness_uses_smart_card_state_pipeline() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "readinessSmartCollapse: true" in source
    assert "readinessCardState: {}" in source
    assert "CHAT_CONSOLE_PAID_OVERFLOW_READINESS_CHECKS" in source
    assert 'shortTitle: "Hub"' in source
    assert 'shortTitle: "Setting"' in source
    assert 'shortTitle: "Wallet"' in source
    assert 'shortTitle: "Key"' in source
    assert 'shortTitle: "Credits"' in source
    assert 'shortTitle: "Estimate"' in source
    assert 'dependencies: ["hub-reachability", "connected-wallet"]' in source
    assert 'dependencies: ["hub-reachability", "connected-wallet", "spendable-credits"]' in source
    assert "chatConsolePaidOverflowReadinessPipelineRows" in source
    assert "chatConsolePaidOverflowReadinessAllDone" in source
    assert '"blocked-by-prior"' in source
    assert "Waiting for Hub and wallet before key validation can be known." in source
    assert "A check may only show red for its own reason" not in source
    assert "smart collapse runs only after all checks resolve" in source
    assert "phase === \"checking\"" in source
    assert "allDone && chatConsoleRemoteWorkerControlState.readinessSmartCollapse" in source
    assert "current.replaceWith(next)" not in source


def test_chat_console_paid_overflow_readiness_exposes_user_utility() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "window.MainComputerPaidOverflowReadiness" in source
    assert "chatConsoleOpenPaidOverflowReadinessUtility" in source
    assert "chatConsoleSetPaidOverflowReadinessUtilityState" in source
    assert "chatConsoleShowPaidOverflowReadinessScenario" in source
    assert "showScenario: chatConsoleShowPaidOverflowReadinessScenario" in source
    assert "collapseAll()" in source
    assert "expandAll()" in source
    assert "paidOverflowReadiness: window.MainComputerPaidOverflowReadiness" in source
    assert "hub_unreachable" in source
    assert "invalid_key" in source
    assert "insufficient_credits" in source
    assert "ready" in source


def test_chat_console_paid_overflow_readiness_styles_prevent_flashy_rebuilds() -> None:
    css = CHAT_CONSOLE_CSS.read_text(encoding="utf-8")
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert ".chat-remote-worker-control-readiness-card.smart" in css
    assert "contain: layout paint" in css
    assert ".chat-remote-worker-control-readiness-toggle" in css
    assert ".chat-remote-worker-control-readiness-detail-panel" in css
    assert ".chat-remote-worker-control-readiness-row.expanded" in css
    assert "transition: max-height 180ms ease, opacity 160ms ease, padding-top 160ms ease" in css
    assert 'item.dataset.chatPaidOverflowSmartCard = "true"' in source
    assert 'item.dataset.chatPaidOverflowExpanded = "false"' in source
    assert 'card.dataset.chatPaidOverflowSmartModal = "true"' in source



def test_chat_console_paid_overflow_uses_bigint_credit_wei_helpers() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "CHAT_CONSOLE_CREDIT_WEI_PER_CREDIT = 1000000000000000000n" in source
    assert "function chatConsoleCreditDecimalToWei" in source
    assert "function chatConsoleCreditWeiToText" in source
    assert "function chatConsoleCreditWeiProduct" in source
    assert "Available credit wei" in source
    assert "Approx hold wei" in source
    assert "Whole-credit hold" not in source
