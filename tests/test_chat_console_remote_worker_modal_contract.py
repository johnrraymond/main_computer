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


def test_chat_console_remote_worker_modal_is_phase_four_assessment_panel() -> None:
    source = CHAT_CONSOLE_JS.read_text(encoding="utf-8")

    assert "Phase 4 remote-worker assessment" in source
    assert "Remote Worker control" in source
    assert "Current Local AI Worker" in source
    assert "Remote Overflow Assessment" in source
    assert "Diagnostic assessment cards" in source
    assert "read-only remote-overflow assessment endpoint" in source
    assert "This panel refreshes the blocking local worker every 2 seconds" in source
    assert "Blocking worker age" in source
    assert "Last checked" in source
    assert "No credits are held or spent" in source
    assert "no mock submit, real hub request, or real remote worker is contacted yet." in source
    assert "data-chat-console-remote-worker-control-modal" in source
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
    assert "lastAssessment" in source


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

    preflight_index = source.index("const remoteWorkerGate = await chatConsoleMaybeShowRemoteWorkerControlForBusyLocal")
    local_start_index = source.index("local AI became available; acquiring pending request lease before starting locally")
    fetch_index = source.index("const response = await fetch(endpoint")
    assert preflight_index < fetch_index
    assert local_start_index < fetch_index
