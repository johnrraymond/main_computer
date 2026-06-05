    let chatConsoleThreadController = null;
    let chatConsoleSpreadsheetThreadController = null;
    const chatConsoleEmbeddedThreadControllers = new WeakMap();
    const chatConsoleEmbeddedSessions = new WeakMap();
    const chatConsoleActiveAiRequests = new Map();
    const CHAT_CONSOLE_AI_RECONNECT_TIMEOUT_MS = 10 * 60 * 1000;
    const CHAT_CONSOLE_AI_RECONNECT_INTERVAL_MS = 1200;
    const CHAT_CONSOLE_THINKING_ACTIVITY_INTERVAL_MS = 5000;
    const CHAT_CONSOLE_THINKING_OLLAMA_INTERVAL_MS = 20000;
    const CHAT_CONSOLE_REMOTE_WORKER_CONTROL_CAPACITY_INTERVAL_MS = 2000;
    const chatConsoleThinkingState = {
      events: [],
      ollamaModels: [],
      ollamaModelsStatus: "not checked",
      ollamaModelsUpdatedAt: "",
      ollamaModelsLastFetchMs: 0,
      localCapacityByThread: {},
      localCapacityStatus: "not checked",
      localCapacityUpdatedAt: "",
      refreshTimer: null,
      refreshInFlight: false
    };
    const chatConsoleRemoteWorkerControlState = {
      modal: null,
      capacityTimer: null,
      escapeHandler: null,
      capacityRefreshInFlight: false,
      lastCapacitySnapshot: null,
      lastAssessment: null,
      assessmentRefreshInFlight: false,
      lastHubReadiness: null,
      hubReadinessRefreshInFlight: false,
      lastChoice: null,
      lastShownAt: "",
      lastCellId: "",
      lastRunId: "",
      lastThreadId: "",
      activePendingRequestId: "",
      pendingLocalRequests: new Map(),
      localStartLease: null,
      resolveChoice: null,
      globalWhenBusyIntent: null,
      lastCloseReason: null,
      lastIntent: null,
      readinessGeneration: 0,
      readinessSmartCollapse: true,
      readinessCardState: {}
    };
    const chatConsoleNotebookRenderSignatures = new WeakMap();

    function chatConsoleThreadsApi() {
      return window.MainComputerChatThreads || null;
    }

    function chatConsoleThreadControllerApi() {
      return window.MainComputerChatThreadController || null;
    }

    function chatConsoleEmbeddedStatusNodes() {
      const nodes = [
        ...document.querySelectorAll("[data-chat-console-embedded-status]"),
        document.querySelector("#spreadsheet-chat-thread-status")
      ].filter(Boolean);
      return [...new Set(nodes)];
    }

    function chatConsoleEmbeddedSpreadsheetStatus() {
      return document.querySelector("#spreadsheet-chat-thread-status");
    }

    function chatConsoleSetStatus(message) {
      if (chatConsoleSaveStatus) chatConsoleSaveStatus.textContent = message;
      chatConsoleEmbeddedStatusNodes().forEach((node) => {
        node.textContent = message;
      });
    }

    function chatConsoleRemoteWorkerOptions(state = chatConsoleState) {
      const options = state?.remote_worker_options;
      return options && typeof options === "object" && !Array.isArray(options) ? options : {};
    }

    function chatConsoleRemoteWorkerWhenBusyForChatEnabled() {
      return Boolean(chatConsoleRemoteWorkerOptions().when_busy_for_chat);
    }

    function chatConsoleSetRemoteWorkerWhenBusyForChat(enabled, message = "remote worker chat preference saved", options = {}) {
      if (!chatConsoleState) return false;
      const now = chatConsoleNow();
      const nextOptions = {
        ...chatConsoleRemoteWorkerOptions(),
        when_busy_for_chat: Boolean(enabled),
        updated_at: now
      };
      if (options.intent) {
        nextOptions.when_busy_for_chat_intent = options.intent;
      } else if (!enabled) {
        nextOptions.when_busy_for_chat_intent = null;
      }
      if (!enabled) nextOptions.when_busy_for_chat_cleared_at = now;
      if (options.closeReason) nextOptions.when_busy_for_chat_close_reason = options.closeReason;
      chatConsoleState.remote_worker_options = nextOptions;
      saveChatConsoleState(message);
      if (options.render !== false) renderChatConsoleNotebook();
      return true;
    }

    function chatConsoleCanonicalRemoteWorkerIntentMode(mode) {
      const raw = String(mode || "").trim();
      const aliases = {
        use_remote_once: "remote_once",
        use_remote_when_needed_for_chat: "remote_when_needed_for_chat",
        always_when_busy: "remote_when_needed_global",
        remote_global: "remote_when_needed_global"
      };
      return aliases[raw] || raw || "wait_local";
    }

    function chatConsoleRemoteWorkerIntentScope(mode) {
      if (mode === "remote_once" || mode === "wait_local") return "request";
      if (mode === "remote_when_needed_for_chat") return "chat";
      if (mode === "remote_when_needed_global") return "global";
      return "request";
    }

    function chatConsoleRemoteWorkerIntentUsesRemoteHubForCurrentRequest(mode) {
      const canonicalMode = chatConsoleCanonicalRemoteWorkerIntentMode(mode);
      return canonicalMode === "remote_once"
        || canonicalMode === "remote_when_needed_for_chat"
        || canonicalMode === "remote_when_needed_global";
    }

    function chatConsoleBuildRemoteWorkerControlIntent({mode, pendingRequest = null, source = "modal_option", auto = false, reason = "", active = true} = {}) {
      const canonicalMode = chatConsoleCanonicalRemoteWorkerIntentMode(mode);
      const request = pendingRequest || chatConsolePendingLocalAiRequest();
      return {
        mode: canonicalMode,
        scope: chatConsoleRemoteWorkerIntentScope(canonicalMode),
        active: Boolean(active),
        source,
        selected_at: chatConsoleNow(),
        pending_request_id: request?.id || "",
        thread_id: request?.thread_id || "",
        run_id: request?.run_id || "",
        cell_id: request?.cell_id || "",
        reason: reason || canonicalMode,
        auto: Boolean(auto),
        phase: "phase5_durable_intent",
        remote_execution_started: false,
        mock_remote_submit_started: false,
        credit_hold_created: false,
        credit_spent: false,
        permanent_worker_setting_changed: false
      };
    }

    function chatConsoleBuildRemoteWorkerControlCloseReason({reason = "dismissed", pendingRequest = null, source = "modal", auto = false} = {}) {
      const request = pendingRequest || chatConsolePendingLocalAiRequest();
      return {
        reason: String(reason || "dismissed"),
        source,
        closed_at: chatConsoleNow(),
        auto: Boolean(auto),
        pending_request_id: request?.id || "",
        thread_id: request?.thread_id || "",
        run_id: request?.run_id || "",
        cell_id: request?.cell_id || "",
        phase: "phase5_durable_intent"
      };
    }

    function chatConsoleCreatePendingLocalAiRequestId(runId) {
      const cleanRunId = String(runId || "run").replace(/[^a-zA-Z0-9_-]+/g, "_").slice(0, 48) || "run";
      return `pending_local_${cleanRunId}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
    }

    function chatConsoleShortRemoteWorkerId(value, limit = 14) {
      const text = String(value || "").trim();
      if (!text) return "not assigned";
      if (text.length <= limit) return text;
      return `${text.slice(0, Math.max(4, limit - 3))}…`;
    }

    function chatConsoleRegisterPendingLocalAiRequest({cell, runId, threadId, endpoint, payload}) {
      const pendingRequestId = chatConsoleCreatePendingLocalAiRequestId(runId || cell?.run_id || cell?.id || "run");
      const request = {
        id: pendingRequestId,
        cell_id: cell?.id || "",
        run_id: runId || cell?.run_id || "",
        thread_id: threadId || "",
        endpoint: endpoint || "",
        payload,
        status: "pending",
        created_at: chatConsoleNow(),
        updated_at: chatConsoleNow(),
        start_attempts: 0,
        started_at: "",
        completed_at: "",
        cancelled_at: "",
        close_reason: ""
      };
      chatConsoleRemoteWorkerControlState.pendingLocalRequests.set(pendingRequestId, request);
      return request;
    }

    function chatConsolePendingLocalAiRequest(pendingRequestId = "") {
      const id = String(pendingRequestId || chatConsoleRemoteWorkerControlState.activePendingRequestId || "").trim();
      return id ? chatConsoleRemoteWorkerControlState.pendingLocalRequests.get(id) || null : null;
    }

    function chatConsoleUpdatePendingLocalAiRequest(pendingRequestId, updates = {}) {
      const request = chatConsolePendingLocalAiRequest(pendingRequestId);
      if (!request) return null;
      Object.assign(request, updates, {updated_at: chatConsoleNow()});
      chatConsoleRemoteWorkerControlState.pendingLocalRequests.set(request.id, request);
      return request;
    }

    function chatConsolePendingLocalAiRequestFooterText(request) {
      if (!request) return "Pending request: not assigned";
      const pendingId = chatConsoleShortRemoteWorkerId(request.id, 24);
      const runId = chatConsoleShortRemoteWorkerId(request.run_id, 20);
      const threadId = chatConsoleShortRemoteWorkerId(request.thread_id, 18);
      return `Pending request ${pendingId} · run ${runId} · thread ${threadId}`;
    }

    function chatConsoleUpdateRemoteWorkerPendingRequestFooter(request = null) {
      const modal = chatConsoleRemoteWorkerControlState.modal;
      if (!modal) return;
      const footer = modal.querySelector("[data-chat-remote-worker-pending-request-footer]");
      if (footer) footer.textContent = chatConsolePendingLocalAiRequestFooterText(request || chatConsolePendingLocalAiRequest());
    }

    function chatConsoleTryAcquireLocalAiStartLease(pendingRequestId, capacity = null) {
      const request = chatConsolePendingLocalAiRequest(pendingRequestId);
      if (!request) return {ok: false, reason: "pending_request_missing", pending_request_id: pendingRequestId || ""};
      if (request.cancelled_at) return {ok: false, reason: "pending_request_cancelled", pending_request_id: request.id};
      if (request.started_at || request.status === "starting" || request.status === "running") {
        return {ok: false, reason: "pending_request_already_started", pending_request_id: request.id};
      }
      if (capacity && chatConsoleShouldOpenRemoteWorkerControlForCapacity(capacity)) {
        return {ok: false, reason: "local_ai_still_busy", pending_request_id: request.id};
      }
      const currentLease = chatConsoleRemoteWorkerControlState.localStartLease;
      if (currentLease && currentLease.pending_request_id !== request.id) {
        return {
          ok: false,
          reason: "local_ai_start_lease_held",
          pending_request_id: request.id,
          held_by_pending_request_id: currentLease.pending_request_id || ""
        };
      }
      const lease = {
        ok: true,
        pending_request_id: request.id,
        run_id: request.run_id || "",
        thread_id: request.thread_id || "",
        cell_id: request.cell_id || "",
        acquired_at: chatConsoleNow()
      };
      chatConsoleRemoteWorkerControlState.localStartLease = lease;
      chatConsoleUpdatePendingLocalAiRequest(request.id, {
        status: "starting",
        start_attempts: Number(request.start_attempts || 0) + 1,
        started_at: lease.acquired_at
      });
      return lease;
    }

    function chatConsoleReleaseLocalAiStartLease(pendingRequestId, reason = "released") {
      const id = String(pendingRequestId || "").trim();
      const lease = chatConsoleRemoteWorkerControlState.localStartLease;
      if (lease && (!id || lease.pending_request_id === id)) {
        chatConsoleRemoteWorkerControlState.localStartLease = null;
      }
      if (id) {
        chatConsoleUpdatePendingLocalAiRequest(id, {
          status: reason === "completed" ? "completed" : "released",
          completed_at: reason === "completed" ? chatConsoleNow() : ""
        });
      }
    }

    function chatConsoleForgetPendingLocalAiRequest(pendingRequestId) {
      const id = String(pendingRequestId || "").trim();
      if (!id) return;
      if (chatConsoleRemoteWorkerControlState.activePendingRequestId === id) chatConsoleRemoteWorkerControlState.activePendingRequestId = "";
      chatConsoleRemoteWorkerControlState.pendingLocalRequests.delete(id);
    }

    async function chatConsoleWaitForPendingLocalAiStartLease({pendingRequestId, threadId}) {
      const requestId = String(pendingRequestId || "").trim();
      if (!requestId) return {ok: false, reason: "pending_request_missing", pending_request_id: ""};
      while (chatConsolePendingLocalAiRequest(requestId)) {
        chatConsoleSetStatus("waiting for local AI slot before acquiring pending request lease");
        const snapshot = await chatConsoleFetchLocalAiCapacityNow(threadId);
        chatConsoleRemoteWorkerControlState.lastCapacitySnapshot = snapshot;
        if (chatConsoleShouldOpenRemoteWorkerControlForCapacity(snapshot)) {
          await chatConsoleRemoteWorkerSleep(CHAT_CONSOLE_REMOTE_WORKER_CONTROL_CAPACITY_INTERVAL_MS);
          continue;
        }
        const lease = chatConsoleTryAcquireLocalAiStartLease(requestId, snapshot);
        if (lease.ok) return lease;
        if (lease.reason === "pending_request_already_started" || lease.reason === "pending_request_cancelled") return lease;
        const heldBy = lease.held_by_pending_request_id ? ` by ${chatConsoleShortRemoteWorkerId(lease.held_by_pending_request_id, 18)}` : "";
        chatConsoleSetStatus(`local AI start lease is held${heldBy}; this pending request will retry`);
        await chatConsoleRemoteWorkerSleep(CHAT_CONSOLE_REMOTE_WORKER_CONTROL_CAPACITY_INTERVAL_MS);
      }
      return {ok: false, reason: "pending_request_missing", pending_request_id: requestId};
    }

    function chatConsoleRemoteWorkerControlSummary(snapshot) {
      const reason = String(snapshot?.reason_code || "local_ai_busy");
      const count = Number(snapshot?.active_run_count || 0);
      const activeThreadIds = Array.isArray(snapshot?.active_thread_ids) ? snapshot.active_thread_ids.filter(Boolean) : [];
      const activeThreadText = activeThreadIds.length ? ` Active thread: ${activeThreadIds[0]}.` : "";
      if (reason === "thread_busy") return "This chat is already using the local AI slot.";
      if (reason === "local_concurrency_exhausted") return `Another local AI request is using the available slot${count ? ` (${count} active)` : ""}.${activeThreadText}`;
      if (reason === "local_ai_available") return "Local AI is available now.";
      return snapshot?.user_message || "Local AI is busy right now.";
    }

    function chatConsoleRemoteWorkerStatusItems(items) {
      const list = document.createElement("dl");
      list.className = "chat-remote-worker-control-status-list";
      items.forEach(([label, value]) => {
        const term = document.createElement("dt");
        term.textContent = label;
        const detail = document.createElement("dd");
        detail.textContent = value || "not available";
        list.append(term, detail);
      });
      return list;
    }

    function chatConsolePopulateRemoteWorkerStatusCard(card, {status, message, items = []}) {
      if (!card) return;
      const statusNode = card.querySelector("[data-chat-remote-worker-status]");
      const messageNode = card.querySelector("[data-chat-remote-worker-message]");
      const listNode = card.querySelector(".chat-remote-worker-control-status-list");
      if (statusNode) statusNode.textContent = status || "Unknown";
      if (messageNode) messageNode.textContent = message || "";
      if (listNode) listNode.replaceWith(chatConsoleRemoteWorkerStatusItems(items));
    }

    function chatConsoleRemoteWorkerLocalStatus(capacity, threadId, runId) {
      const activeRuns = Number(capacity?.active_run_count || 0);
      const maxConcurrency = Number(capacity?.max_local_concurrency || 1);
      const busy = chatConsoleShouldOpenRemoteWorkerControlForCapacity(capacity);
      const activeRunList = Array.isArray(capacity?.active_runs) ? capacity.active_runs : [];
      const activeThreadIds = Array.isArray(capacity?.active_thread_ids) ? capacity.active_thread_ids.filter(Boolean) : [];
      const matchingRun = activeRunList.find((run) => String(run?.thread_id || "") === String(threadId || ""))
        || activeRunList[0]
        || null;
      const activeRunIds = activeRunList.map((run) => run?.run_id || run?.id || "").filter(Boolean);
      const ageSeconds = Number(matchingRun?.age_s || 0);
      const checkedAt = capacity?.updated_at ? new Date(capacity.updated_at).toLocaleTimeString() : new Date().toLocaleTimeString();
      return {
        status: busy ? "Busy" : "Available",
        message: chatConsoleRemoteWorkerControlSummary(capacity),
        items: [
          ["Reason", capacity?.reason_code || (busy ? "local_ai_busy" : "local_ai_available")],
          ["Active local AI runs", `${activeRuns} / ${maxConcurrency} local slot${maxConcurrency === 1 ? "" : "s"}`],
          ["Checked thread", threadId || capacity?.thread_id || ""],
          ["Pending request run", runId || ""],
          ["Blocking thread", matchingRun?.thread_id || activeThreadIds[0] || ""],
          ["Blocking run", matchingRun?.run_id || activeRunIds[0] || ""],
          ["Blocking PID", matchingRun?.pid ? String(matchingRun.pid) : ""],
          ["Blocking worker age", ageSeconds ? `${ageSeconds.toFixed(1)}s` : ""],
          ["Last checked", checkedAt]
        ]
      };
    }

    function chatConsoleRemoteWorkerHubStatus() {
      return {
        status: "Template",
        message: "Hub worker information will appear here as the overflow pathway matures.",
        items: [
          ["Available workers", "template / not checked yet"],
          ["Hub mode", "Phase 2 control surface"],
          ["Worker contact", "none"],
          ["Credits", "not checked, held, or spent"]
        ]
      };
    }

    function chatConsoleRemoteOverflowAssessmentPlaceholderStatus(status = "Checking") {
      return {
        status,
        message: status === "Unavailable"
          ? "Remote overflow assessment could not be loaded. Local-first waiting remains available."
          : "Loading a compact read-only remote-overflow assessment.",
        items: [
          ["Decision", status === "Unavailable" ? "assessment unavailable" : "checking assessment"],
          ["Reason", status === "Unavailable" ? "assessment_load_failed" : "waiting_for_backend_assessment"],
          ["Remote offer", "no"],
          ["Cards checked", "0"],
          ["Safety", "hold: not created · spend: none · worker: not contacted"]
        ]
      };
    }

    function chatConsoleRemoteOverflowDetailLabel(key) {
      return String(key || "")
        .replace(/_/g, " ")
        .replace(/\b\w/g, (char) => char.toUpperCase());
    }

    function chatConsoleRemoteOverflowDetailValue(value) {
      if (value === true) return "true";
      if (value === false) return "false";
      if (value === null || value === undefined || value === "") return "";
      if (Array.isArray(value)) return value.length ? value.map((item) => chatConsoleRemoteOverflowDetailValue(item)).join(", ") : "[]";
      if (typeof value === "object") {
        try {
          return JSON.stringify(value);
        } catch {
          return String(value);
        }
      }
      return String(value);
    }

    function chatConsoleRemoteOverflowAssessmentCardStatus(card) {
      const details = card?.details && typeof card.details === "object" && !Array.isArray(card.details) ? card.details : {};
      const detailItems = Object.entries(details)
        .slice(0, 10)
        .map(([key, value]) => [chatConsoleRemoteOverflowDetailLabel(key), chatConsoleRemoteOverflowDetailValue(value)])
        .filter(([, value]) => String(value || "").trim());
      const reasonCode = details.reason_code || card?.key || "";
      return {
        status: card?.status || "Unknown",
        message: card?.message || "",
        items: [
          ["Card", card?.key || ""],
          ["Reason", reasonCode || ""],
          ...detailItems.filter(([label]) => label !== "Reason Code")
        ]
      };
    }

    function chatConsoleRemoteOverflowAssessmentStatus(assessment) {
      if (!assessment) return chatConsoleRemoteOverflowAssessmentPlaceholderStatus();
      const cardCount = Array.isArray(assessment.cards) ? assessment.cards.length : 0;
      const action = assessment.action || assessment.status || "read_only_assessment";
      const reason = assessment.reason_code || "diagnostic_phase";
      return {
        status: assessment.status || assessment.reason_code || "Assessed",
        message: assessment.user_message || "Remote overflow assessment returned a compact diagnostic summary. Full card details are collapsed below.",
        items: [
          ["Decision", action],
          ["Reason", reason],
          ["Remote offer", assessment.offer_remote ? "yes" : "no"],
          ["Cards checked", String(cardCount)],
          ["Safety", "hold: not created · spend: none · worker: not contacted"]
        ]
      };
    }

    function chatConsoleRemoteOverflowAssessmentDetailsSummaryText(assessment) {
      const cardCount = Array.isArray(assessment?.cards) ? assessment.cards.length : 0;
      return cardCount ? `Show diagnostic details (${cardCount} cards)` : "Show diagnostic details";
    }

    function chatConsoleRenderRemoteOverflowAssessmentCards(assessment) {
      const cards = Array.isArray(assessment?.cards) ? assessment.cards.filter((card) => card && typeof card === "object") : [];
      if (!cards.length) {
        return [
          chatConsoleRemoteWorkerStatusCard({
            kind: "assessment-loading",
            title: "Remote overflow assessment",
            ...chatConsoleRemoteOverflowAssessmentPlaceholderStatus()
          })
        ];
      }
      return cards.map((card) => chatConsoleRemoteWorkerStatusCard({
        kind: `assessment-${String(card.key || "card").replace(/[^a-z0-9_-]+/gi, "-").toLowerCase()}`,
        title: card.title || chatConsoleRemoteOverflowDetailLabel(card.key || "Assessment card"),
        ...chatConsoleRemoteOverflowAssessmentCardStatus(card)
      }));
    }

    function chatConsoleUpdateRemoteOverflowAssessmentCards(assessment) {
      chatConsoleRemoteWorkerControlState.lastAssessment = assessment || null;
      const modal = chatConsoleRemoteWorkerControlState.modal;
      if (!modal) return;
      const summaryCard = modal.querySelector('[data-chat-remote-worker-status-card="assessment-summary"]');
      if (summaryCard) chatConsolePopulateRemoteWorkerStatusCard(summaryCard, chatConsoleRemoteOverflowAssessmentStatus(assessment));
      const detailsSummary = modal.querySelector("[data-chat-remote-overflow-assessment-details-summary]");
      if (detailsSummary) detailsSummary.textContent = chatConsoleRemoteOverflowAssessmentDetailsSummaryText(assessment);
      const grid = modal.querySelector("[data-chat-remote-overflow-assessment-grid]");
      if (!grid) return;
      grid.replaceChildren(...chatConsoleRenderRemoteOverflowAssessmentCards(assessment));
    }

    function chatConsoleRemoteOverflowMessagesFromPendingRequest(request) {
      const payload = request?.payload && typeof request.payload === "object" ? request.payload : {};
      if (Array.isArray(payload.messages)) return payload.messages.filter((item) => item && typeof item === "object");
      const cell = payload.cell && typeof payload.cell === "object" ? payload.cell : null;
      const stateCell = request?.cell_id ? chatConsoleState?.cells?.find((item) => item.id === request.cell_id) : null;
      const content = String(payload.prompt || payload.source || cell?.source || stateCell?.source || "").trim();
      return content ? [{role: "user", content}] : [];
    }

    function chatConsoleRemoteOverflowModelFromPendingRequest(request) {
      const payload = request?.payload && typeof request.payload === "object" ? request.payload : {};
      const cell = payload.cell && typeof payload.cell === "object" ? payload.cell : {};
      const config = payload.config && typeof payload.config === "object" ? payload.config : {};
      return String(payload.model || config.model || cell.model || payload.preferred_model || "");
    }

    function chatConsoleNumberOrNull(value) {
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    }

    const CHAT_CONSOLE_CREDIT_WEI_PER_CREDIT = 1000000000000000000n;

    function chatConsoleCreditDecimalToWei(value, fallback = "0") {
      const raw = String(value ?? fallback ?? "0").trim() || String(fallback ?? "0");
      const negative = raw.startsWith("-");
      const text = negative ? raw.slice(1) : raw;
      const match = text.match(/^(\d+)(?:\.(\d+))?$/);
      if (!match || negative) {
        if (raw !== String(fallback ?? "0")) return chatConsoleCreditDecimalToWei(fallback, "0");
        return 0n;
      }
      const whole = BigInt(match[1] || "0");
      const fractionRaw = match[2] || "";
      const fraction = (fractionRaw + "000000000000000000").slice(0, 18);
      let wei = whole * CHAT_CONSOLE_CREDIT_WEI_PER_CREDIT + BigInt(fraction || "0");
      if (fractionRaw.length > 18 && /[1-9]/.test(fractionRaw.slice(18))) wei += 1n;
      return wei;
    }

    function chatConsoleCreditWeiToText(value) {
      let wei;
      try {
        wei = BigInt(String(value ?? "0"));
      } catch {
        wei = 0n;
      }
      if (wei < 0n) wei = 0n;
      const whole = wei / CHAT_CONSOLE_CREDIT_WEI_PER_CREDIT;
      const fraction = wei % CHAT_CONSOLE_CREDIT_WEI_PER_CREDIT;
      if (fraction === 0n) return whole.toString();
      return `${whole.toString()}.${fraction.toString().padStart(18, "0").replace(/0+$/, "")}`;
    }

    function chatConsoleCreditWeiProduct(tokens, creditsPerTokenWei) {
      let tokenCount;
      try {
        tokenCount = BigInt(String(tokens ?? "0"));
      } catch {
        tokenCount = 0n;
      }
      let perToken;
      try {
        perToken = BigInt(String(creditsPerTokenWei ?? "0"));
      } catch {
        perToken = 0n;
      }
      if (tokenCount < 0n) tokenCount = 0n;
      if (perToken < 0n) perToken = 0n;
      return tokenCount * perToken;
    }

    function chatConsoleEstimatedInputTokensForRemoteOverflow(request) {
      const messages = chatConsoleRemoteOverflowMessagesFromPendingRequest(request);
      const contentChars = messages.reduce((total, item) => total + String(item?.content || "").length, 0);
      const attachmentCount = messages.reduce((total, item) => {
        const attachments = Array.isArray(item?.attachments) ? item.attachments : [];
        return total + attachments.length;
      }, 0);
      return Math.max(1, Math.ceil(contentChars / 4) + attachmentCount * 256);
    }

    function chatConsoleWorkerPaidOverflowContext(request) {
      return {
        estimatedInputTokens: chatConsoleEstimatedInputTokensForRemoteOverflow(request),
        ready: false,
        hub_checked: false,
        hub_reachable: null,
        hub_key_valid: null,
        hub_credit_ready: null,
        funds_ok: null,
        reason_code: "hub_readiness_not_checked",
        user_message: "Hub readiness has not run yet.",
        walletAddress: "",
        accountId: "",
        activeMultisessionKeyId: "",
        availableCredits: null,
        requiredCredits: null,
        estimatedMaxCreditsApprox: null,
        maxOutputTokens: null,
        creditsPerTokenText: ""
      };
    }

    function chatConsolePaidOverflowReadinessFromContext(paidOverflow = {}, hubReadiness = null, hubError = null) {
      if (hubError) {
        return {
          ready: false,
          hub_checked: true,
          hub_reachable: false,
          hub_key_valid: null,
          hub_credit_ready: false,
          funds_ok: false,
          reason_code: "hub_unreachable",
          user_message: `Hub readiness could not be checked: ${hubError.message || hubError}`,
          wallet_address: "",
          account_id: "",
          multisession_key_id: "",
          available_credits: 0,
          available_credit_wei: "0",
          available_credits_display: "0",
          required_credits: 1,
          required_credit_wei: "1000000000000000000",
          required_credits_display: "1",
          estimated_max_credits_approx: "0",
          estimated_max_credit_wei: "0",
          max_output_tokens: 0,
          credits_per_token: "",
          credits_per_token_wei: "",
          checks: [
            {
              key: "hub-reachability",
              title: "Hub reachable",
              ok: false,
              detail: `Could not contact the configured Hub: ${hubError.message || hubError}`
            }
          ],
          hub: null,
          updated_at: chatConsoleNow()
        };
      }

      if (!hubReadiness) {
        return {
          ready: false,
          hub_checked: false,
          hub_reachable: null,
          hub_key_valid: null,
          hub_credit_ready: null,
          funds_ok: null,
          reason_code: "hub_readiness_not_checked",
          user_message: "Hub readiness has not run yet.",
          wallet_address: "",
          account_id: "",
          multisession_key_id: "",
          available_credits: 0,
          available_credit_wei: "0",
          available_credits_display: "0",
          required_credits: 1,
          required_credit_wei: "1000000000000000000",
          required_credits_display: "1",
          estimated_max_credits_approx: "0",
          estimated_max_credit_wei: "0",
          max_output_tokens: 0,
          credits_per_token: "",
          credits_per_token_wei: "",
          checks: [
            {
              key: "hub-reachability",
              title: "Hub reachable",
              ok: null,
              unknownText: "Not checked",
              detail: "Reachability has not been checked yet."
            },
            {
              key: "paid-overflow-setting",
              title: "Paid overflow setting",
              ok: null,
              unknownText: "Not checked",
              detail: "The local backend has not loaded the Worker paid overflow policy yet."
            },
            {
              key: "connected-wallet",
              title: "Connected wallet",
              ok: null,
              unknownText: "Not checked",
              detail: "The local backend has not selected a wallet-backed multi-session key yet."
            },
            {
              key: "hub-key-validation",
              title: "Multi-session key usable",
              ok: null,
              unknownText: "Not checked",
              detail: "The Hub has not validated the active multi-session key yet."
            },
            {
              key: "spendable-credits",
              title: "Spendable bridged credits",
              ok: null,
              unknownText: "Not checked",
              detail: "The Hub balance has not been checked yet."
            },
            {
              key: "authorization-budget",
              title: "Approximate authorization",
              ok: null,
              unknownText: "Not checked",
              detail: "The Hub has not checked the approximate authorization budget yet."
            }
          ],
          hub: null,
          updated_at: chatConsoleNow()
        };
      }

      const account = hubReadiness.account && typeof hubReadiness.account === "object" ? hubReadiness.account : {};
      const availableCredits = chatConsoleNumberOrNull(hubReadiness.available_credits ?? account.available_credits) ?? 0;
      const requiredCredits = chatConsoleNumberOrNull(hubReadiness.required_credits) ?? 1;
      const availableCreditWei = String(hubReadiness.available_credit_wei ?? account.available_credit_wei ?? "");
      const requiredCreditWei = String(hubReadiness.required_credit_wei ?? hubReadiness.estimated_max_credit_wei ?? "");
      const approximateCredits = String(
        hubReadiness.estimated_max_credits_approx
        ?? hubReadiness.required_credits_display
        ?? chatConsoleCreditWeiToText(requiredCreditWei)
        ?? ""
      );
      const maxOutputTokens = chatConsoleNumberOrNull(hubReadiness.max_output_tokens) ?? 0;
      const ready = Boolean(hubReadiness.ready);
      const checks = Array.isArray(hubReadiness.checks) ? hubReadiness.checks : [];
      return {
        ready,
        hub_checked: true,
        hub_reachable: hubReadiness.hub_reachable === false ? false : true,
        hub_key_valid: hubReadiness.valid === true,
        hub_credit_ready: hubReadiness.credit_ready === false ? false : Boolean(hubReadiness.ready),
        funds_ok: hubReadiness.funds_ok === false ? false : Boolean(hubReadiness.credit_ready !== false),
        reason_code: String(hubReadiness.reason_code || (ready ? "paid_overflow_ready" : "paid_overflow_not_ready")),
        user_message: String(hubReadiness.user_message || (ready ? "Paid overflow is ready." : "Paid overflow is not ready.")),
        wallet_address: String(hubReadiness.wallet_address || ""),
        account_id: String(hubReadiness.account_id || ""),
        multisession_key_id: String(hubReadiness.multisession_key_id || ""),
        available_credits: availableCredits,
        available_credit_wei: availableCreditWei,
        available_credits_display: String(hubReadiness.available_credits_display ?? account.available_credits_display ?? chatConsoleCreditWeiToText(availableCreditWei)),
        required_credits: requiredCredits,
        required_credit_wei: requiredCreditWei,
        required_credits_display: String(hubReadiness.required_credits_display ?? chatConsoleCreditWeiToText(requiredCreditWei)),
        estimated_max_credits_approx: approximateCredits,
        estimated_max_credit_wei: String(hubReadiness.estimated_max_credit_wei ?? requiredCreditWei),
        max_output_tokens: maxOutputTokens,
        credits_per_token: String(hubReadiness.credits_per_token ?? ""),
        credits_per_token_wei: String(hubReadiness.credits_per_token_wei ?? ""),
        checks,
        hub: hubReadiness,
        updated_at: chatConsoleNow()
      };
    }

    const CHAT_CONSOLE_PAID_OVERFLOW_READINESS_CHECKS = [
      {
        key: "hub-reachability",
        shortTitle: "Hub",
        title: "Hub reachable",
        dependencies: [],
        waitingDetail: "Waiting for Hub reachability before this can be known."
      },
      {
        key: "paid-overflow-setting",
        shortTitle: "Setting",
        title: "Paid overflow setting",
        dependencies: [],
        waitingDetail: "Waiting for the backend paid-overflow setting."
      },
      {
        key: "connected-wallet",
        shortTitle: "Wallet",
        title: "Connected wallet",
        dependencies: [],
        waitingDetail: "Waiting for backend wallet context."
      },
      {
        key: "hub-key-validation",
        shortTitle: "Key",
        title: "Multi-session key usable",
        dependencies: ["hub-reachability", "connected-wallet"],
        waitingDetail: "Waiting for Hub and wallet before key validation can be known."
      },
      {
        key: "spendable-credits",
        shortTitle: "Credits",
        title: "Spendable bridged credits",
        dependencies: ["hub-reachability", "connected-wallet"],
        waitingDetail: "Waiting for Hub and wallet before spendable credits can be known."
      },
      {
        key: "authorization-budget",
        shortTitle: "Estimate",
        title: "Approximate authorization",
        dependencies: ["hub-reachability", "connected-wallet", "spendable-credits"],
        waitingDetail: "Waiting for spendable credits before the approximate authorization can be known."
      }
    ];

    function chatConsolePaidOverflowReadinessDefinitions() {
      return CHAT_CONSOLE_PAID_OVERFLOW_READINESS_CHECKS.map((item) => ({...item}));
    }

    function chatConsolePaidOverflowReadinessDefinition(key) {
      return CHAT_CONSOLE_PAID_OVERFLOW_READINESS_CHECKS.find((item) => item.key === key) || {
        key,
        shortTitle: key || "Check",
        title: key || "Check",
        dependencies: [],
        waitingDetail: "Waiting for prerequisite checks."
      };
    }

    function chatConsolePaidOverflowReadinessRows(readiness, paidOverflow) {
      const state = readiness || chatConsolePaidOverflowReadinessFromContext(paidOverflow);
      const incoming = Array.isArray(state.checks) ? state.checks : [];
      const byKey = new Map();
      incoming.forEach((check) => {
        if (!check || typeof check !== "object") return;
        const key = String(check.key || "").trim();
        if (!key) return;
        byKey.set(key, check);
      });
      return chatConsolePaidOverflowReadinessDefinitions().map((definition) => {
        const check = byKey.get(definition.key) || {};
        return {
          key: definition.key,
          shortTitle: definition.shortTitle,
          title: String(check.title || definition.title || definition.shortTitle),
          ok: check.ok === true ? true : (check.ok === false ? false : null),
          unknownText: String(check.unknownText || check.unknown_text || "Checking"),
          detail: String(check.detail || check.message || definition.waitingDetail || ""),
          dependencies: Array.isArray(definition.dependencies) ? definition.dependencies.slice() : [],
          waitingDetail: definition.waitingDetail || "Waiting for prerequisite checks."
        };
      });
    }

    function chatConsoleReadinessStatusParts(state, unknownText = "Checking") {
      if (state === "ok" || state === true) return {mark: "✓", className: "ok", label: "Ready"};
      if (state === "blocked" || state === false) return {mark: "✕", className: "bad", label: "Needs attention"};
      if (state === "blocked-by-prior") return {mark: "…", className: "waiting", label: "Waiting"};
      if (state === "inactive") return {mark: "–", className: "inactive", label: "Not active"};
      return {mark: "?", className: "warn", label: unknownText || "Checking"};
    }

    function chatConsolePaidOverflowOwnStateForRow(row, phase = "resolved") {
      if (phase === "checking") return "checking";
      if (row.ok === true) return "ok";
      if (row.ok === false) return "blocked";
      return "checking";
    }

    function chatConsolePaidOverflowReadinessPipelineRows(readiness, paidOverflow = null, phase = "resolved") {
      const baseRows = chatConsolePaidOverflowReadinessRows(readiness, paidOverflow).map((row) => ({
        ...row,
        ownState: chatConsolePaidOverflowOwnStateForRow(row, phase),
        effectiveState: chatConsolePaidOverflowOwnStateForRow(row, phase),
        blockingDependency: "",
        blockedByPrior: false
      }));
      const rowByKey = new Map(baseRows.map((row) => [row.key, row]));
      baseRows.forEach((row) => {
        const dependencies = Array.isArray(row.dependencies) ? row.dependencies : [];
        const blocking = dependencies.find((depKey) => rowByKey.get(depKey)?.effectiveState === "blocked");
        if (blocking) {
          row.effectiveState = "blocked-by-prior";
          row.blockedByPrior = true;
          row.blockingDependency = blocking;
          const blockingTitle = rowByKey.get(blocking)?.shortTitle || rowByKey.get(blocking)?.title || blocking;
          row.detail = `Waiting for ${blockingTitle} before this can be known.`;
          return;
        }
        const pending = dependencies.find((depKey) => {
          const depState = rowByKey.get(depKey)?.effectiveState;
          return depState === "checking" || depState === "blocked-by-prior" || depState === "inactive";
        });
        if (pending && row.ownState !== "blocked") {
          row.effectiveState = "checking";
          row.blockingDependency = pending;
          row.detail = row.waitingDetail || row.detail;
        }
      });
      return baseRows;
    }

    function chatConsolePaidOverflowReadinessAllDone(rows) {
      return rows.length > 0 && rows.every((row) => !["checking"].includes(row.effectiveState));
    }

    function chatConsolePaidOverflowReadinessRow(row) {
      const definition = chatConsolePaidOverflowReadinessDefinition(row.key);
      const parts = chatConsoleReadinessStatusParts(row.effectiveState || row.ownState || "checking", row.unknownText || "Checking");
      const item = document.createElement("article");
      item.className = `chat-remote-worker-control-readiness-row ${parts.className}`;
      item.dataset.chatPaidOverflowReadinessCheck = row.key || "";
      item.dataset.chatPaidOverflowEffectiveState = row.effectiveState || row.ownState || "checking";
      item.dataset.chatPaidOverflowOwnState = row.ownState || "checking";
      item.dataset.chatPaidOverflowSmartCard = "true";
      item.dataset.chatPaidOverflowExpanded = "false";
      const mark = document.createElement("div");
      mark.className = "chat-remote-worker-control-readiness-mark";
      mark.dataset.chatPaidOverflowReadinessMark = "true";
      mark.textContent = parts.mark;
      const title = document.createElement("button");
      title.type = "button";
      title.className = "chat-remote-worker-control-readiness-title";
      title.dataset.chatPaidOverflowReadinessTitle = "true";
      title.textContent = row.shortTitle || definition.shortTitle || row.title || "";
      title.title = row.title || definition.title || "";
      const summary = document.createElement("div");
      summary.className = "chat-remote-worker-control-readiness-detail";
      summary.dataset.chatPaidOverflowReadinessSummaryText = "true";
      summary.textContent = row.detail || "";
      const badge = document.createElement("div");
      badge.className = "chat-remote-worker-control-readiness-badge";
      badge.dataset.chatPaidOverflowReadinessBadge = "true";
      badge.textContent = parts.label;
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "chat-remote-worker-control-readiness-toggle";
      toggle.dataset.chatPaidOverflowReadinessToggle = "true";
      toggle.setAttribute("aria-expanded", "false");
      toggle.textContent = "+";
      const detail = document.createElement("div");
      detail.className = "chat-remote-worker-control-readiness-detail-panel";
      detail.dataset.chatPaidOverflowReadinessDetail = "true";
      detail.textContent = row.detail || "";
      const toggleRow = () => {
        const expanded = item.dataset.chatPaidOverflowExpanded === "true";
        chatConsoleSetPaidOverflowReadinessRowExpanded(item, !expanded, {manual: true});
      };
      title.addEventListener("click", toggleRow);
      toggle.addEventListener("click", toggleRow);
      item.append(mark, title, summary, badge, toggle, detail);
      return item;
    }

    function chatConsolePaidOverflowReadinessMetric(label, value, key = "") {
      const node = document.createElement("div");
      node.className = "chat-remote-worker-control-readiness-metric";
      if (key) node.dataset.chatPaidOverflowReadinessMetric = key;
      const labelNode = document.createElement("div");
      labelNode.className = "chat-remote-worker-control-readiness-metric-label";
      labelNode.textContent = label;
      const valueNode = document.createElement("div");
      valueNode.className = "chat-remote-worker-control-readiness-metric-value";
      valueNode.dataset.chatPaidOverflowReadinessMetricValue = key || label;
      valueNode.textContent = String(value ?? "");
      node.append(labelNode, valueNode);
      return node;
    }

    function chatConsoleSetPaidOverflowReadinessRowExpanded(rowNode, expanded, {manual = false} = {}) {
      if (!rowNode) return;
      const key = rowNode.dataset.chatPaidOverflowReadinessCheck || "";
      if (manual && key) {
        const cardState = chatConsoleRemoteWorkerControlState.readinessCardState[key] || {};
        cardState.userExpanded = Boolean(expanded);
        cardState.manualTouched = true;
        chatConsoleRemoteWorkerControlState.readinessCardState[key] = cardState;
      }
      rowNode.dataset.chatPaidOverflowExpanded = expanded ? "true" : "false";
      rowNode.classList.toggle("expanded", Boolean(expanded));
      const toggle = rowNode.querySelector("[data-chat-paid-overflow-readiness-toggle]");
      if (toggle) {
        toggle.textContent = expanded ? "−" : "+";
        toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
      }
    }

    function chatConsolePaidOverflowReadinessCard(readiness, paidOverflow) {
      const state = readiness || chatConsolePaidOverflowReadinessFromContext(paidOverflow);
      const card = document.createElement("section");
      card.className = "chat-remote-worker-control-readiness-card smart";
      card.dataset.chatPaidOverflowReadinessCard = "true";
      card.dataset.chatPaidOverflowSmartModal = "true";
      const heading = document.createElement("div");
      heading.className = "chat-remote-worker-control-readiness-heading";
      const title = document.createElement("button");
      title.type = "button";
      title.className = "chat-remote-worker-control-readiness-card-title";
      title.dataset.chatPaidOverflowReadinessCardTitle = "true";
      title.textContent = "Paid overflow readiness";
      title.addEventListener("click", () => chatConsoleTogglePaidOverflowReadinessRows());
      const badge = document.createElement("span");
      badge.className = `chat-remote-worker-control-readiness-summary ${state.ready ? "ok" : "bad"}`;
      badge.dataset.chatPaidOverflowReadinessSummary = "true";
      badge.textContent = state.ready ? "Ready" : "Blocked";
      heading.append(title, badge);

      const description = document.createElement("p");
      description.className = "chat-remote-worker-control-readiness-message";
      description.dataset.chatPaidOverflowReadinessMessage = "true";
      description.textContent = state.user_message;

      const rows = document.createElement("div");
      rows.className = "chat-remote-worker-control-readiness-rows";
      rows.dataset.chatPaidOverflowReadinessRows = "true";
      const pipelineRows = chatConsolePaidOverflowReadinessPipelineRows(state, paidOverflow, "resolved");
      rows.append(...pipelineRows.map(chatConsolePaidOverflowReadinessRow));

      const metrics = document.createElement("div");
      metrics.className = "chat-remote-worker-control-readiness-metrics";
      metrics.dataset.chatPaidOverflowReadinessMetrics = "true";
      [
        ["Available credits", state.available_credits_display ?? state.available_credits, "available"],
        ["Available credit wei", state.available_credit_wei ?? "", "available-credit-wei"],
        ["Max output tokens", state.max_output_tokens, "max-output-tokens"],
        ["Credits per token", state.credits_per_token, "credits-per-token"],
        ["Credits/token wei", state.credits_per_token_wei ?? "", "credits-per-token-wei"],
        ["Approx hold/charge", state.required_credits_display ?? state.estimated_max_credits_approx, "approx-required"],
        ["Approx hold wei", state.required_credit_wei ?? state.estimated_max_credit_wei ?? "", "required-credit-wei"],
        ["Billing note", "approx only", "billing-note"]
      ].forEach(([label, value, key]) => metrics.append(chatConsolePaidOverflowReadinessMetric(label, value, key)));

      const note = document.createElement("p");
      note.className = "chat-remote-worker-control-readiness-note";
      note.textContent = "These values are authorization estimates only. No credits are held or spent by this modal or by Hub readiness checks.";

      const actions = document.createElement("div");
      actions.className = "chat-remote-worker-control-readiness-actions";
      const refresh = document.createElement("button");
      refresh.type = "button";
      refresh.className = "chat-remote-worker-control-readiness-refresh";
      refresh.textContent = "Refresh readiness";
      refresh.addEventListener("click", () => chatConsoleRefreshRemoteHubReadiness().catch(() => {}));
      actions.append(refresh);

      card.append(heading, description, rows, metrics, note, actions);
      return card;
    }

    function chatConsoleUpdateRemoteWorkerPaidOptionAvailability(readiness = null) {
      const modal = chatConsoleRemoteWorkerControlState.modal;
      if (!modal) return;
      const state = readiness || chatConsoleRemoteWorkerControlState.lastHubReadiness;
      const ready = Boolean(state?.ready);
      const reason = state?.user_message || "Paid overflow readiness has not passed.";
      modal.querySelectorAll('[data-chat-remote-worker-paid-option="true"]').forEach((button) => {
        button.disabled = !ready;
        button.title = ready ? "Paid overflow readiness passed." : reason;
        button.dataset.chatRemoteWorkerPaidReady = ready ? "true" : "false";
      });
    }

    function chatConsoleTogglePaidOverflowReadinessRows(expanded = null) {
      const modal = chatConsoleRemoteWorkerControlState.modal;
      const rows = modal ? [...modal.querySelectorAll("[data-chat-paid-overflow-readiness-check]")] : [];
      const shouldExpand = expanded === null ? rows.some((row) => row.dataset.chatPaidOverflowExpanded !== "true") : Boolean(expanded);
      rows.forEach((row) => chatConsoleSetPaidOverflowReadinessRowExpanded(row, shouldExpand, {manual: true}));
    }

    function chatConsoleUpdatePaidOverflowReadinessMetric(card, key, value) {
      const node = card?.querySelector(`[data-chat-paid-overflow-readiness-metric="${key}"] [data-chat-paid-overflow-readiness-metric-value]`);
      if (node) node.textContent = String(value ?? "");
    }

    function chatConsoleUpdatePaidOverflowReadinessRow(rowNode, row, {allDone = false, phase = "resolved"} = {}) {
      if (!rowNode) return;
      const parts = chatConsoleReadinessStatusParts(row.effectiveState || row.ownState || "checking", row.unknownText || "Checking");
      rowNode.className = `chat-remote-worker-control-readiness-row ${parts.className}`;
      rowNode.dataset.chatPaidOverflowEffectiveState = row.effectiveState || row.ownState || "checking";
      rowNode.dataset.chatPaidOverflowOwnState = row.ownState || "checking";
      rowNode.dataset.chatPaidOverflowBlockedByPrior = row.blockedByPrior ? "true" : "false";
      rowNode.dataset.chatPaidOverflowBlockingDependency = row.blockingDependency || "";
      rowNode.querySelector("[data-chat-paid-overflow-readiness-mark]").textContent = parts.mark;
      rowNode.querySelector("[data-chat-paid-overflow-readiness-title]").textContent = row.shortTitle || row.title || "";
      rowNode.querySelector("[data-chat-paid-overflow-readiness-title]").title = row.title || "";
      rowNode.querySelector("[data-chat-paid-overflow-readiness-summary-text]").textContent = row.detail || "";
      rowNode.querySelector("[data-chat-paid-overflow-readiness-badge]").textContent = parts.label;
      rowNode.querySelector("[data-chat-paid-overflow-readiness-detail]").textContent = row.detail || "";
      const key = row.key || "";
      const cardState = chatConsoleRemoteWorkerControlState.readinessCardState[key] || {};
      const previousState = cardState.effectiveState || "";
      const currentState = row.effectiveState || row.ownState || "checking";
      const stateChanged = previousState && previousState !== currentState;
      cardState.effectiveState = currentState;
      cardState.ownState = row.ownState || "checking";
      if (stateChanged) {
        cardState.autoExpandedForState = false;
        if (currentState === "ok") {
          cardState.userExpanded = false;
          cardState.manualTouched = false;
        }
      }
      let expand = rowNode.dataset.chatPaidOverflowExpanded === "true";
      if (phase === "checking") {
        // Hold the current shape while checks are in flight; smart collapse runs only after all checks resolve.
      } else if (allDone && chatConsoleRemoteWorkerControlState.readinessSmartCollapse) {
        if (currentState === "ok") {
          expand = false;
        } else if (currentState === "blocked" && !cardState.autoExpandedForState && cardState.userExpanded !== false) {
          expand = true;
          cardState.autoExpandedForState = true;
        } else if (currentState === "blocked-by-prior" && !cardState.manualTouched) {
          expand = false;
        }
      }
      chatConsoleRemoteWorkerControlState.readinessCardState[key] = cardState;
      chatConsoleSetPaidOverflowReadinessRowExpanded(rowNode, expand, {manual: false});
    }

    function chatConsoleUpdatePaidOverflowReadinessCard(readiness, paidOverflow = null, options = {}) {
      chatConsoleRemoteWorkerControlState.lastHubReadiness = readiness || null;
      const modal = chatConsoleRemoteWorkerControlState.modal;
      if (!modal) return;
      const context = paidOverflow || chatConsoleWorkerPaidOverflowContext(chatConsolePendingLocalAiRequest(chatConsoleRemoteWorkerControlState.activePendingRequestId));
      const state = readiness || chatConsolePaidOverflowReadinessFromContext(context);
      let card = modal.querySelector("[data-chat-paid-overflow-readiness-card]");
      if (!card) {
        card = chatConsolePaidOverflowReadinessCard(state, context);
        modal.append(card);
        return;
      }
      const phase = options.phase || "resolved";
      const pipelineRows = chatConsolePaidOverflowReadinessPipelineRows(state, context, phase);
      const allDone = phase !== "checking" && chatConsolePaidOverflowReadinessAllDone(pipelineRows);
      card.dataset.chatPaidOverflowAllChecksDone = allDone ? "true" : "false";
      card.dataset.chatPaidOverflowSmartCollapse = chatConsoleRemoteWorkerControlState.readinessSmartCollapse ? "true" : "false";
      card.classList.toggle("all-done", allDone);
      card.classList.toggle("checking", phase === "checking");
      const summary = card.querySelector("[data-chat-paid-overflow-readiness-summary]");
      if (summary) {
        summary.className = `chat-remote-worker-control-readiness-summary ${phase === "checking" ? "warn" : (state.ready ? "ok" : "bad")}`;
        summary.textContent = phase === "checking" ? "Checking" : (state.ready ? "Ready" : "Blocked");
      }
      const message = card.querySelector("[data-chat-paid-overflow-readiness-message]");
      if (message) {
        message.textContent = phase === "checking"
          ? "Checking Hub-backed paid overflow readiness. No credits are held or spent by this check."
          : state.user_message;
      }
      const rowsContainer = card.querySelector("[data-chat-paid-overflow-readiness-rows]");
      pipelineRows.forEach((row) => {
        let rowNode = rowsContainer?.querySelector(`[data-chat-paid-overflow-readiness-check="${row.key}"]`);
        if (!rowNode && rowsContainer) {
          rowNode = chatConsolePaidOverflowReadinessRow(row);
          rowsContainer.append(rowNode);
        }
        chatConsoleUpdatePaidOverflowReadinessRow(rowNode, row, {allDone, phase});
      });
      chatConsoleUpdatePaidOverflowReadinessMetric(card, "available", state.available_credits_display ?? state.available_credits);
      chatConsoleUpdatePaidOverflowReadinessMetric(card, "available-credit-wei", state.available_credit_wei ?? "");
      chatConsoleUpdatePaidOverflowReadinessMetric(card, "max-output-tokens", state.max_output_tokens);
      chatConsoleUpdatePaidOverflowReadinessMetric(card, "credits-per-token", state.credits_per_token);
      chatConsoleUpdatePaidOverflowReadinessMetric(card, "credits-per-token-wei", state.credits_per_token_wei ?? "");
      chatConsoleUpdatePaidOverflowReadinessMetric(card, "approx-required", state.required_credits_display ?? state.estimated_max_credits_approx ?? "");
      chatConsoleUpdatePaidOverflowReadinessMetric(card, "required-credit-wei", state.required_credit_wei ?? state.estimated_max_credit_wei ?? "");
      chatConsoleUpdateRemoteWorkerPaidOptionAvailability(readiness);
    }

    async function chatConsoleFetchRemoteHubReadiness({pendingRequest = null} = {}) {
      const request = pendingRequest || chatConsolePendingLocalAiRequest();
      const body = chatConsoleBuildRemoteOverflowAssessmentPayload({pendingRequest: request});
      const response = await fetch("/api/applications/chat-console/ai/remote-overflow/hub-readiness", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        cache: "no-store",
        body: JSON.stringify(body)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) throw new Error(data.error || `remote Hub readiness HTTP ${response.status}`);
      return data.hub_readiness || data.readiness || data;
    }

    async function chatConsoleRefreshRemoteHubReadiness({pendingRequest = null} = {}) {
      if (chatConsoleRemoteWorkerControlState.hubReadinessRefreshInFlight) {
        return chatConsoleRemoteWorkerControlState.lastHubReadiness;
      }
      const request = pendingRequest || chatConsolePendingLocalAiRequest();
      const paidOverflow = chatConsoleWorkerPaidOverflowContext(request);
      chatConsoleRemoteWorkerControlState.readinessGeneration += 1;
      const generation = chatConsoleRemoteWorkerControlState.readinessGeneration;
      let readiness = chatConsolePaidOverflowReadinessFromContext(paidOverflow);
      chatConsoleUpdatePaidOverflowReadinessCard(readiness, paidOverflow, {phase: "checking", generation});

      chatConsoleRemoteWorkerControlState.hubReadinessRefreshInFlight = true;
      try {
        const hubReadiness = await chatConsoleFetchRemoteHubReadiness({pendingRequest: request});
        readiness = chatConsolePaidOverflowReadinessFromContext(paidOverflow, hubReadiness);
        chatConsoleUpdatePaidOverflowReadinessCard(readiness, paidOverflow, {phase: "resolved", generation});
        chatConsoleSetStatus(`paid overflow readiness ${readiness.reason_code || "updated"}`);
        return readiness;
      } catch (error) {
        readiness = chatConsolePaidOverflowReadinessFromContext(paidOverflow, null, error);
        chatConsoleUpdatePaidOverflowReadinessCard(readiness, paidOverflow, {phase: "resolved", generation});
        chatConsoleSetStatus(`paid overflow readiness failed: ${error.message || error}`);
        return readiness;
      } finally {
        chatConsoleRemoteWorkerControlState.hubReadinessRefreshInFlight = false;
      }
    }

    function chatConsolePaidOverflowReadinessReady(readiness) {
      return Boolean(readiness?.ready);
    }

    function chatConsoleRefreshPaidOverflowReadinessAfterPayment() {
      chatConsoleRefreshRemoteHubReadiness().catch(() => {});
    }

    function chatConsoleBuildRemoteOverflowAssessmentPayload({pendingRequest, capacity = null}) {
      const request = pendingRequest || chatConsolePendingLocalAiRequest();
      const payload = request?.payload && typeof request.payload === "object" ? request.payload : {};
      const paidOverflow = chatConsoleWorkerPaidOverflowContext(request);
      return {
        phase: "phase4_modal_assessment_cards",
        pending_request_id: request?.id || "",
        thread_id: request?.thread_id || payload.thread_id || chatConsoleRemoteWorkerControlState.lastThreadId || "",
        run_id: request?.run_id || payload.run_id || chatConsoleRemoteWorkerControlState.lastRunId || "",
        cell_id: request?.cell_id || chatConsoleRemoteWorkerControlState.lastCellId || "",
        model: chatConsoleRemoteOverflowModelFromPendingRequest(request),
        capability: payload.rag_type || payload.capability || "chat.completions",
        messages: chatConsoleRemoteOverflowMessagesFromPendingRequest(request),
        max_local_concurrency: 1,
        local_only: false,
        local_capacity_settings: {
          max_local_concurrency: 1
        },
        local_capacity_snapshot: capacity || chatConsoleRemoteWorkerControlState.lastCapacitySnapshot || null,
        browser_estimate: {
          estimated_input_tokens: paidOverflow.estimatedInputTokens,
          approximation_only: true
        },
        credit: {
          known: false,
          approximation_only: true,
          no_credit_hold_created: true,
          no_credit_spent: true
        },
        payment_authorization: {
          kind: "backend_worker_paid_overflow_context",
          approximation_only: true
        },
        hub: {
          mode: "mock_safe_template",
          real_remote_worker_contacted: false,
          private_worker_prices_exposed: false
        }
      };
    }

    async function chatConsoleFetchRemoteOverflowAssessment({pendingRequest, capacity = null}) {
      const body = chatConsoleBuildRemoteOverflowAssessmentPayload({pendingRequest, capacity});
      const response = await fetch("/api/applications/chat-console/ai/remote-overflow/assess", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        cache: "no-store",
        body: JSON.stringify(body)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) throw new Error(data.error || `remote overflow assessment HTTP ${response.status}`);
      return data.remote_overflow || data.assessment || data;
    }

    function chatConsoleBuildRemoteHubSubmitPayload({pendingRequest, cell, payload, mode = ""}) {
      const request = pendingRequest || chatConsolePendingLocalAiRequest();
      const assessmentPayload = chatConsoleBuildRemoteOverflowAssessmentPayload({pendingRequest: request});
      const basePayload = payload && typeof payload === "object" ? payload : (request?.payload && typeof request.payload === "object" ? request.payload : {});
      const submitCell = cell && typeof cell === "object"
        ? {...cell}
        : (basePayload.cell && typeof basePayload.cell === "object" ? {...basePayload.cell} : {});
      const intentMode = chatConsoleCanonicalRemoteWorkerIntentMode(
        mode || request?.remote_worker_overflow_intent?.mode || request?.choice?.mode || "remote_once"
      );
      const preflightSeed = String(request?.id || request?.run_id || submitCell.id || Date.now()).replace(/[^a-zA-Z0-9_-]+/g, "_").slice(0, 80);
      return {
        ...assessmentPayload,
        ...basePayload,
        phase: "phase6_remote_hub_execution",
        pending_request_id: request?.id || "",
        thread_id: request?.thread_id || basePayload.thread_id || chatConsoleState?.id || "",
        run_id: request?.run_id || basePayload.run_id || submitCell.run_id || "",
        cell: {
          ...submitCell,
          run_id: request?.run_id || basePayload.run_id || submitCell.run_id || ""
        },
        remote_overflow_enabled: Boolean(assessmentPayload.remote_overflow_enabled),
        local_only: false,
        authorization_granted_by_user: true,
        remote_execution_source: "remote_hub",
        remote_worker_intent_mode: intentMode,
        remote_worker_intent_scope: chatConsoleRemoteWorkerIntentScope(intentMode),
        remote_hub_current_request: true,
        remote_once: intentMode === "remote_once",
        credit_ready: Boolean(assessmentPayload.credit_ready),
        max_output_tokens: assessmentPayload.max_output_tokens,
        credits_per_token: assessmentPayload.credits_per_token,
        payment_authorization: assessmentPayload.payment_authorization,
        willing_worker_count: assessmentPayload.credit_ready ? 1 : 0,
        credit: {
          ...(assessmentPayload.credit && typeof assessmentPayload.credit === "object" ? assessmentPayload.credit : {}),
          credit_ready: Boolean(assessmentPayload.credit_ready),
          no_credit_hold_created: true,
          no_credit_spent: true
        },
        hub: {
          ...(assessmentPayload.hub && typeof assessmentPayload.hub === "object" ? assessmentPayload.hub : {}),
          mode: "remote_hub_current_request",
          willing_worker_count: assessmentPayload.credit_ready ? 1 : 0,
          preflight_id: `phase6-remote-hub-${preflightSeed}`,
          real_remote_worker_contacted: false,
          private_worker_prices_exposed: false
        }
      };
    }

    function chatConsoleSetRemoteHubExecutionState(cellId, patch = {}) {
      const cell = chatConsoleState?.cells?.find((item) => item.id === cellId);
      if (!cell) return false;
      const current = cell.remote_worker_execution && typeof cell.remote_worker_execution === "object" ? cell.remote_worker_execution : {};
      cell.remote_worker_execution = {
        ...current,
        source: "remote_hub",
        status: patch.status || current.status || "running",
        updated_at: chatConsoleNow(),
        credit_hold_created: false,
        credit_spent: false,
        ...patch
      };
      cell.updated_at = chatConsoleNow();
      saveChatConsoleState("remote hub execution state saved");
      return true;
    }

    async function chatConsoleSubmitRemoteHubOnce({pendingRequest, cell, payload, mode = ""}) {
      const request = pendingRequest || chatConsolePendingLocalAiRequest();
      if (!request?.id) throw new Error("Remote Hub submit requires a pending request.");
      if (request.remote_hub_submit_started_at) {
        throw new Error("Remote Hub submit already started for this pending request.");
      }
      const intentMode = chatConsoleCanonicalRemoteWorkerIntentMode(
        mode || request?.remote_worker_overflow_intent?.mode || request?.choice?.mode || "remote_once"
      );
      if (!chatConsoleRemoteWorkerIntentUsesRemoteHubForCurrentRequest(intentMode)) {
        throw new Error(`Remote Hub submit cannot run for intent ${intentMode || "unknown"}.`);
      }
      const paidOverflowReadiness = await chatConsoleRefreshRemoteHubReadiness({pendingRequest: request});
      if (!chatConsolePaidOverflowReadinessReady(paidOverflowReadiness)) {
        throw new Error(paidOverflowReadiness?.user_message || "Paid overflow readiness did not pass.");
      }
      chatConsoleUpdatePendingLocalAiRequest(request.id, {
        status: "remote_hub_running",
        remote_hub_submit_started_at: chatConsoleNow(),
        remote_worker_intent_mode: intentMode
      });
      chatConsoleSetRemoteHubExecutionState(request.cell_id || cell?.id || "", {
        mode: intentMode,
        status: "running",
        started_at: chatConsoleNow(),
        run_id: request.run_id || payload?.run_id || cell?.run_id || "",
        pending_request_id: request.id,
        message: "Remote Hub is working on this request."
      });
      renderChatConsoleNotebook();
      const body = chatConsoleBuildRemoteHubSubmitPayload({pendingRequest: request, cell, payload, mode: intentMode});
      try {
        const response = await fetch("/api/applications/chat-console/ai/remote-overflow/hub-submit", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          cache: "no-store",
          body: JSON.stringify(body)
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) throw new Error(data.error || `Remote Hub submit returned ${response.status}`);
        const paymentReceipt = data?.remote_overflow_result?.payment || data?.remote_overflow_result?.response?.metadata?.payment || data?.payment || null;
        chatConsoleRefreshPaidOverflowReadinessAfterPayment();
        chatConsoleSetRemoteHubExecutionState(request.cell_id || cell?.id || "", {
          mode: intentMode,
          status: "completed",
          completed_at: chatConsoleNow(),
          run_id: request.run_id || body.run_id || "",
          pending_request_id: request.id,
          message: "Remote Hub response received.",
          provider: data?.remote_overflow_result?.response?.provider || data?.output_cell?.provider || "remote-hub-ai",
          model: data?.remote_overflow_result?.response?.model || data?.output_cell?.model || body.model || "",
          credit_hold_created: Boolean(paymentReceipt?.hold_id),
          credit_spent: Number(paymentReceipt?.charged_credits || 0) > 0,
          payment: paymentReceipt || null
        });
        return data;
      } catch (error) {
        chatConsoleSetRemoteHubExecutionState(request.cell_id || cell?.id || "", {
          mode: intentMode,
          status: "error",
          error: error.message || String(error),
          completed_at: chatConsoleNow(),
          run_id: request.run_id || body.run_id || "",
          pending_request_id: request.id,
          message: "Remote Hub could not complete this request."
        });
        renderChatConsoleNotebook();
        throw error;
      }
    }

    async function chatConsoleRefreshRemoteOverflowAssessment({pendingRequest = null, capacity = null} = {}) {
      if (chatConsoleRemoteWorkerControlState.assessmentRefreshInFlight) return chatConsoleRemoteWorkerControlState.lastAssessment;
      const request = pendingRequest || chatConsolePendingLocalAiRequest();
      if (!request) return null;
      chatConsoleRemoteWorkerControlState.assessmentRefreshInFlight = true;
      try {
        const assessment = await chatConsoleFetchRemoteOverflowAssessment({pendingRequest: request, capacity});
        chatConsoleUpdateRemoteOverflowAssessmentCards(assessment);
        chatConsoleSetStatus(`remote overflow assessment ${assessment.reason_code || assessment.status || "updated"}`);
        return assessment;
      } catch (error) {
        chatConsoleUpdateRemoteOverflowAssessmentCards(null);
        chatConsoleSetStatus(`remote overflow assessment failed: ${error.message || error}`);
        return null;
      } finally {
        chatConsoleRemoteWorkerControlState.assessmentRefreshInFlight = false;
      }
    }

    function chatConsoleRemoteWorkerStatusCard({kind, title, status, message, items}) {
      const card = document.createElement("article");
      card.className = "chat-remote-worker-control-status-card";
      card.dataset.chatRemoteWorkerStatusCard = kind || "";
      const heading = document.createElement("div");
      heading.className = "chat-remote-worker-control-status-heading";
      const titleNode = document.createElement("h3");
      titleNode.textContent = title;
      const badge = document.createElement("span");
      badge.className = "chat-remote-worker-control-status-badge";
      badge.dataset.chatRemoteWorkerStatus = "true";
      badge.textContent = status || "Unknown";
      heading.append(titleNode, badge);
      const messageNode = document.createElement("p");
      messageNode.dataset.chatRemoteWorkerMessage = "true";
      messageNode.textContent = message || "";
      card.append(heading, messageNode, chatConsoleRemoteWorkerStatusItems(items || []));
      return card;
    }

    function chatConsoleUpdateRemoteWorkerControlLocalCard(capacity) {
      chatConsoleRemoteWorkerControlState.lastCapacitySnapshot = capacity || null;
      const modal = chatConsoleRemoteWorkerControlState.modal;
      if (!modal) return;
      const card = modal.querySelector('[data-chat-remote-worker-status-card="local"]');
      const status = chatConsoleRemoteWorkerLocalStatus(
        capacity,
        chatConsoleRemoteWorkerControlState.lastThreadId,
        chatConsoleRemoteWorkerControlState.lastRunId
      );
      chatConsolePopulateRemoteWorkerStatusCard(card, status);
    }

    function chatConsoleRemoteWorkerOptionCard({mode, title, kicker, description, details, defaultOption = false, paidRemote = false}) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `chat-remote-worker-control-option-card${defaultOption ? " default" : ""}`;
      button.dataset.chatRemoteWorkerOption = mode;
      if (paidRemote) {
        button.dataset.chatRemoteWorkerPaidOption = "true";
        button.disabled = true;
        button.title = "Paid overflow readiness has not passed.";
      }
      button.addEventListener("click", () => chatConsoleChooseRemoteWorkerControlOption(mode, {reason: mode}));
      const kickerNode = document.createElement("span");
      kickerNode.className = "chat-remote-worker-control-option-kicker";
      kickerNode.textContent = kicker || "";
      const titleNode = document.createElement("strong");
      titleNode.textContent = title;
      const descriptionNode = document.createElement("span");
      descriptionNode.className = "chat-remote-worker-control-option-description";
      descriptionNode.textContent = description || "";
      button.append(kickerNode, titleNode, descriptionNode);
      if (details) {
        const detailsNode = document.createElement("small");
        detailsNode.textContent = details;
        button.append(detailsNode);
      }
      return button;
    }

    function chatConsoleStopRemoteWorkerControlCapacityWatcher() {
      if (chatConsoleRemoteWorkerControlState.capacityTimer) {
        clearInterval(chatConsoleRemoteWorkerControlState.capacityTimer);
        chatConsoleRemoteWorkerControlState.capacityTimer = null;
      }
      chatConsoleRemoteWorkerControlState.capacityRefreshInFlight = false;
    }

    async function chatConsoleRemoteWorkerControlCapacityTick() {
      if (!chatConsoleRemoteWorkerControlState.modal || chatConsoleRemoteWorkerControlState.capacityRefreshInFlight) return;
      const pendingRequestId = chatConsoleRemoteWorkerControlState.activePendingRequestId;
      const pendingRequest = chatConsolePendingLocalAiRequest(pendingRequestId);
      const threadId = pendingRequest?.thread_id || chatConsoleRemoteWorkerControlState.lastThreadId;
      if (!threadId || !pendingRequestId) return;
      chatConsoleRemoteWorkerControlState.capacityRefreshInFlight = true;
      try {
        const snapshot = await chatConsoleFetchLocalAiCapacityNow(threadId);
        if (pendingRequestId !== chatConsoleRemoteWorkerControlState.activePendingRequestId) return;
        chatConsoleUpdateRemoteWorkerControlLocalCard(snapshot);
        chatConsoleUpdateRemoteWorkerPendingRequestFooter(pendingRequest);
        await chatConsoleRefreshRemoteOverflowAssessment({pendingRequest, capacity: snapshot});
        await chatConsoleRefreshRemoteHubReadiness({pendingRequest});
        if (!chatConsoleShouldOpenRemoteWorkerControlForCapacity(snapshot)) {
          chatConsoleChooseRemoteWorkerControlOption("wait_local", {
            auto: true,
            pendingRequestId,
            reason: "local_ai_available",
            closeReason: "auto-selected Wait for Available Local Worker because local AI became available; starting the pending request locally"
          });
        }
      } catch (error) {
        chatConsoleSetStatus(`remote worker control capacity refresh failed: ${error.message || error}`);
      } finally {
        chatConsoleRemoteWorkerControlState.capacityRefreshInFlight = false;
      }
    }

    function chatConsoleStartRemoteWorkerControlCapacityWatcher() {
      chatConsoleStopRemoteWorkerControlCapacityWatcher();
      chatConsoleRemoteWorkerControlState.capacityTimer = setInterval(
        () => chatConsoleRemoteWorkerControlCapacityTick().catch(() => {}),
        CHAT_CONSOLE_REMOTE_WORKER_CONTROL_CAPACITY_INTERVAL_MS
      );
    }

    function chatConsoleHideRemoteWorkerControlModal(reason = "dismissed") {
      chatConsoleStopRemoteWorkerControlCapacityWatcher();
      if (chatConsoleRemoteWorkerControlState.escapeHandler) {
        document.removeEventListener("keydown", chatConsoleRemoteWorkerControlState.escapeHandler);
        chatConsoleRemoteWorkerControlState.escapeHandler = null;
      }
      const modal = chatConsoleRemoteWorkerControlState.modal || document.querySelector("[data-chat-console-remote-worker-control-modal]");
      if (modal) modal.remove();
      chatConsoleRemoteWorkerControlState.modal = null;
      if (reason) chatConsoleSetStatus(`remote worker control ${reason}`);
    }

    function chatConsoleRecordRemoteWorkerControlCellIntent(cellId, intent, closeReason = null) {
      const cell = chatConsoleState?.cells?.find((item) => item.id === cellId);
      if (!cell) return false;
      cell.remote_worker_overflow_intent = intent;
      cell.remote_worker_overflow_close_reason = closeReason;
      cell.updated_at = chatConsoleNow();
      saveChatConsoleState("remote worker request intent saved");
      return true;
    }

    function chatConsoleResolveRemoteWorkerControlChoice(choice, pendingRequestId = "") {
      const request = chatConsolePendingLocalAiRequest(pendingRequestId || choice?.pending_request_id || "");
      const resolver = request?.resolveChoice || chatConsoleRemoteWorkerControlState.resolveChoice;
      if (request) {
        request.resolveChoice = null;
        chatConsoleRemoteWorkerControlState.pendingLocalRequests.set(request.id, request);
      }
      if (!request || chatConsoleRemoteWorkerControlState.activePendingRequestId === request.id) {
        chatConsoleRemoteWorkerControlState.resolveChoice = null;
      }
      if (typeof resolver === "function") resolver(choice);
    }

    function chatConsoleChooseRemoteWorkerControlOption(mode, context = {}) {
      const now = chatConsoleNow();
      const pendingRequestId = String(context.pendingRequestId || chatConsoleRemoteWorkerControlState.activePendingRequestId || "").trim();
      const pendingRequest = chatConsolePendingLocalAiRequest(pendingRequestId);
      if (!pendingRequest) return null;
      const canonicalMode = chatConsoleCanonicalRemoteWorkerIntentMode(mode);
      if (chatConsoleRemoteWorkerIntentUsesRemoteHubForCurrentRequest(canonicalMode)) {
        const readiness = chatConsoleRemoteWorkerControlState.lastHubReadiness
          || chatConsolePaidOverflowReadinessFromContext(chatConsoleWorkerPaidOverflowContext(pendingRequest));
        if (!chatConsolePaidOverflowReadinessReady(readiness)) {
          chatConsoleUpdateRemoteWorkerPaidOptionAvailability(readiness);
          chatConsoleSetStatus(`paid overflow readiness blocked: ${readiness.user_message || readiness.reason_code || "not ready"}`);
          return null;
        }
      }
      const cellId = pendingRequest.cell_id || chatConsoleRemoteWorkerControlState.lastCellId;
      const closeReason = chatConsoleBuildRemoteWorkerControlCloseReason({
        reason: context.closeReason || context.reason || canonicalMode,
        pendingRequest,
        source: context.source || (context.auto ? "system" : "modal_option"),
        auto: Boolean(context.auto)
      });
      const intent = chatConsoleBuildRemoteWorkerControlIntent({
        mode: canonicalMode,
        pendingRequest,
        source: context.source || (context.auto ? "system" : "modal_option"),
        auto: Boolean(context.auto),
        reason: context.reason || canonicalMode
      });
      const choice = {
        mode: canonicalMode,
        requested_mode: mode,
        selected_at: now,
        auto: Boolean(context.auto),
        reason: context.reason || canonicalMode,
        close_reason: closeReason.reason,
        close_reason_details: closeReason,
        pending_request_id: pendingRequest.id,
        thread_id: pendingRequest.thread_id || "",
        run_id: pendingRequest.run_id || "",
        intent
      };
      chatConsoleRemoteWorkerControlState.lastChoice = choice;
      chatConsoleRemoteWorkerControlState.lastIntent = intent;
      chatConsoleRemoteWorkerControlState.lastCloseReason = closeReason;
      chatConsoleUpdatePendingLocalAiRequest(pendingRequest.id, {
        status: canonicalMode === "wait_local" ? "waiting_local" : "intent_recorded",
        close_reason: closeReason.reason,
        remote_worker_overflow_close_reason: closeReason,
        remote_worker_overflow_intent: intent,
        choice
      });
      chatConsoleRecordRemoteWorkerControlCellIntent(cellId, intent, closeReason);

      if (canonicalMode === "remote_when_needed_for_chat" || mode === "use_remote_when_needed_for_chat") {
        chatConsoleSetRemoteWorkerWhenBusyForChat(true, "remote worker chat preference enabled", {
          render: false,
          intent,
          closeReason
        });
      } else if (canonicalMode === "remote_when_needed_global" || mode === "always_when_busy") {
        chatConsoleRemoteWorkerControlState.globalWhenBusyIntent = {
          intent,
          close_reason: closeReason,
          permanent_worker_setting_changed: false
        };
      }

      chatConsoleHideRemoteWorkerControlModal(closeReason.reason);
      chatConsoleResolveRemoteWorkerControlChoice(choice, pendingRequest.id);
      if (canonicalMode === "remote_when_needed_for_chat" || canonicalMode === "remote_once") renderChatConsoleNotebook();
      return choice;
    }

    function chatConsoleShowRemoteWorkerControlModal({cell, runId, threadId, capacity, pendingRequest = null, resolveChoice = null}) {
      const boundPendingRequest = pendingRequest || chatConsoleRegisterPendingLocalAiRequest({cell, runId, threadId, endpoint: "", payload: null});
      const existing = chatConsoleRemoteWorkerControlState.modal || document.querySelector("[data-chat-console-remote-worker-control-modal]");
      if (existing) {
        const previousPendingRequestId = chatConsoleRemoteWorkerControlState.activePendingRequestId;
        chatConsoleChooseRemoteWorkerControlOption("wait_local", {
          auto: true,
          pendingRequestId: previousPendingRequestId,
          reason: "superseded_by_new_remote_worker_control",
          closeReason: "superseded by a newer Remote Worker control"
        });
      }

      const backdrop = document.createElement("div");
      backdrop.className = "chat-remote-worker-control-backdrop";
      backdrop.dataset.chatConsoleRemoteWorkerControlModal = "true";
      backdrop.setAttribute("role", "presentation");
      backdrop.addEventListener("click", (event) => {
        if (event.target === backdrop) {
          chatConsoleChooseRemoteWorkerControlOption("wait_local", {pendingRequestId: boundPendingRequest.id, reason: "backdrop_wait_local"});
        }
      });

      const modal = document.createElement("section");
      modal.className = "chat-remote-worker-control-modal";
      modal.setAttribute("role", "dialog");
      modal.setAttribute("aria-modal", "true");
      modal.setAttribute("aria-labelledby", "chat-remote-worker-control-title");
      modal.setAttribute("aria-describedby", "chat-remote-worker-control-description");

      const header = document.createElement("div");
      header.className = "chat-remote-worker-control-header";
      const headingWrap = document.createElement("div");
      const eyebrow = document.createElement("div");
      eyebrow.className = "chat-remote-worker-control-eyebrow";
      eyebrow.textContent = "Paid overflow readiness";
      const title = document.createElement("h2");
      title.id = "chat-remote-worker-control-title";
      title.textContent = "Remote Worker control";
      headingWrap.append(eyebrow, title);
      const close = chatConsoleButton("×", () => chatConsoleChooseRemoteWorkerControlOption("wait_local", {pendingRequestId: boundPendingRequest.id, reason: "close_wait_local"}));
      close.className = "chat-remote-worker-control-x";
      close.setAttribute("aria-label", "Wait for Available Local Worker and close");
      header.append(headingWrap, close);

      const description = document.createElement("p");
      description.id = "chat-remote-worker-control-description";
      description.textContent = "Local AI is busy. This panel shows the blocking local worker, paid overflow readiness, and a compact read-only remote-overflow assessment. It records the selected intent separately from the modal close reason. No credits are held or spent by this modal or by Hub readiness checks.";

      const statusGrid = document.createElement("div");
      statusGrid.className = "chat-remote-worker-control-status-grid";
      const localStatus = chatConsoleRemoteWorkerLocalStatus(capacity, boundPendingRequest.thread_id || threadId, boundPendingRequest.run_id || runId || cell?.run_id || "");
      statusGrid.append(
        chatConsoleRemoteWorkerStatusCard({kind: "local", title: "Current Local AI Worker", ...localStatus}),
        chatConsoleRemoteWorkerStatusCard({kind: "assessment-summary", title: "Remote Overflow Assessment", ...chatConsoleRemoteOverflowAssessmentPlaceholderStatus()})
      );

      const initialPaidOverflowContext = chatConsoleWorkerPaidOverflowContext(boundPendingRequest);
      const initialPaidOverflowReadiness = chatConsolePaidOverflowReadinessFromContext(initialPaidOverflowContext);
      const paidOverflowReadinessCard = chatConsolePaidOverflowReadinessCard(initialPaidOverflowReadiness, initialPaidOverflowContext);

      const assessmentDetails = document.createElement("details");
      assessmentDetails.className = "chat-remote-worker-control-assessment-details";
      assessmentDetails.dataset.chatRemoteOverflowAssessmentDetails = "true";
      const assessmentDetailsSummary = document.createElement("summary");
      assessmentDetailsSummary.className = "chat-remote-worker-control-assessment-details-summary";
      assessmentDetailsSummary.dataset.chatRemoteOverflowAssessmentDetailsSummary = "true";
      assessmentDetailsSummary.textContent = chatConsoleRemoteOverflowAssessmentDetailsSummaryText(null);

      const assessmentGrid = document.createElement("div");
      assessmentGrid.className = "chat-remote-worker-control-status-grid chat-remote-worker-control-assessment-grid";
      assessmentGrid.dataset.chatRemoteOverflowAssessmentGrid = "true";
      assessmentGrid.append(...chatConsoleRenderRemoteOverflowAssessmentCards(null));
      assessmentDetails.append(assessmentDetailsSummary, assessmentGrid);

      const optionsTitle = document.createElement("h3");
      optionsTitle.className = "chat-remote-worker-control-options-title";
      optionsTitle.textContent = "Choose a path";

      const optionsGrid = document.createElement("div");
      optionsGrid.className = "chat-remote-worker-control-option-grid";
      optionsGrid.append(
        chatConsoleRemoteWorkerOptionCard({
          mode: "wait_local",
          kicker: "Default / safe",
          title: "Wait for Available Local Worker",
          description: "Close this control and keep local-first behavior.",
          details: "If you leave this open, this is selected automatically when the blocking local AI call disappears and the pending request starts locally.",
          defaultOption: true
        }),
        chatConsoleRemoteWorkerOptionCard({
          mode: "use_remote_once",
          kicker: "This request",
          title: "Use Remote Worker This Once",
          description: "Send this blocked request through the Remote Hub.",
          details: "The dialog closes immediately; the normal request card shows Remote Hub progress. Credits are held and charged only after all readiness checks pass and the Hub request succeeds.",
          paidRemote: true
        }),
        chatConsoleRemoteWorkerOptionCard({
          mode: "use_remote_when_needed_for_chat",
          kicker: "This chat",
          title: "Use Remote Worker When Needed for This Chat",
          description: "Route this blocked request through the Remote Hub and enable the visible chat option beside the RAG controls.",
          details: "Uncheck that chat option later to clear the chat-scoped intent. Future automatic chat failover is handled in a later phase.",
          paidRemote: true
        }),
        chatConsoleRemoteWorkerOptionCard({
          mode: "always_when_busy",
          kicker: "Global preference",
          title: "Always Use Remote Worker When Local AI Is Busy",
          description: "Route this blocked request through the Remote Hub and record non-permanent global remote-worker intent for busy-local overflow.",
          details: "The Worker app global setting is not connected yet, so no permanent Worker setting is changed in this phase.",
          paidRemote: true
        })
      );

      const notice = document.createElement("p");
      notice.className = "chat-remote-worker-control-notice";
      notice.textContent = "This dialog only asks for a decision after readiness is visible. Wait-local continues through the normal local AI path; Hub options route this blocked request through the Remote Hub and show progress in the regular request card. No credits are held or spent by the modal or readiness check; the paid request can spend only after all checks pass.";

      const pendingFooter = document.createElement("p");
      pendingFooter.className = "chat-remote-worker-control-pending-footer";
      pendingFooter.dataset.chatRemoteWorkerPendingRequestFooter = "true";
      pendingFooter.textContent = chatConsolePendingLocalAiRequestFooterText(boundPendingRequest);

      modal.append(header, description, statusGrid, paidOverflowReadinessCard, assessmentDetails, optionsTitle, optionsGrid, notice, pendingFooter);
      backdrop.append(modal);
      document.body.append(backdrop);

      boundPendingRequest.resolveChoice = typeof resolveChoice === "function" ? resolveChoice : null;
      chatConsoleUpdatePendingLocalAiRequest(boundPendingRequest.id, {
        status: "modal_bound",
        resolveChoice: boundPendingRequest.resolveChoice
      });
      chatConsoleRemoteWorkerControlState.modal = backdrop;
      chatConsoleRemoteWorkerControlState.lastCapacitySnapshot = capacity || null;
      chatConsoleRemoteWorkerControlState.lastShownAt = chatConsoleNow();
      chatConsoleRemoteWorkerControlState.lastCellId = boundPendingRequest.cell_id || cell?.id || "";
      chatConsoleRemoteWorkerControlState.lastRunId = boundPendingRequest.run_id || runId || cell?.run_id || "";
      chatConsoleRemoteWorkerControlState.lastThreadId = boundPendingRequest.thread_id || threadId || "";
      chatConsoleRemoteWorkerControlState.activePendingRequestId = boundPendingRequest.id;
      chatConsoleRemoteWorkerControlState.resolveChoice = typeof resolveChoice === "function" ? resolveChoice : null;
      chatConsoleRemoteWorkerControlState.lastHubReadiness = initialPaidOverflowReadiness;
      chatConsoleUpdateRemoteWorkerPaidOptionAvailability(initialPaidOverflowReadiness);

      chatConsoleRemoteWorkerControlState.escapeHandler = (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          chatConsoleChooseRemoteWorkerControlOption("wait_local", {pendingRequestId: boundPendingRequest.id, reason: "escape_wait_local"});
        }
      };
      document.addEventListener("keydown", chatConsoleRemoteWorkerControlState.escapeHandler);

      const defaultOption = modal.querySelector('[data-chat-remote-worker-option="wait_local"]');
      defaultOption?.focus?.({preventScroll: true});
      chatConsoleStartRemoteWorkerControlCapacityWatcher();
      chatConsoleRefreshRemoteOverflowAssessment({pendingRequest: boundPendingRequest, capacity}).catch(() => {});
      chatConsoleRefreshRemoteHubReadiness({pendingRequest: boundPendingRequest}).catch(() => {});
      chatConsoleSetStatus("remote worker control opened because local AI is busy");
      return backdrop;
    }

    async function chatConsoleFetchLocalAiCapacityNow(threadId) {
      const cleanThreadId = String(threadId || "").trim();
      const params = new URLSearchParams({
        thread_id: cleanThreadId,
        max_local_concurrency: "1"
      });
      const response = await fetch(`/api/applications/chat-console/ai/capacity?${params.toString()}`, {cache: "no-store"});
      const snapshot = await response.json().catch(() => ({}));
      if (!response.ok || snapshot.ok === false) throw new Error(snapshot.error || `capacity HTTP ${response.status}`);
      if (cleanThreadId) {
        chatConsoleThinkingState.localCapacityByThread[cleanThreadId] = snapshot;
        chatConsoleThinkingState.localCapacityStatus = "loaded";
        chatConsoleThinkingState.localCapacityUpdatedAt = chatConsoleNow();
      }
      return snapshot;
    }

    function chatConsoleRemoteWorkerSleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }

    async function chatConsoleWaitForLocalAiCapacityAvailable(threadId) {
      let snapshot = chatConsoleRemoteWorkerControlState.lastCapacitySnapshot || null;
      while (chatConsoleShouldOpenRemoteWorkerControlForCapacity(snapshot)) {
        chatConsoleSetStatus("waiting for local AI slot before starting the pending local request");
        await chatConsoleRemoteWorkerSleep(CHAT_CONSOLE_REMOTE_WORKER_CONTROL_CAPACITY_INTERVAL_MS);
        snapshot = await chatConsoleFetchLocalAiCapacityNow(threadId);
        chatConsoleRemoteWorkerControlState.lastCapacitySnapshot = snapshot;
      }
      return snapshot || await chatConsoleFetchLocalAiCapacityNow(threadId);
    }

    function chatConsoleShouldOpenRemoteWorkerControlForCapacity(snapshot) {
      if (!snapshot || snapshot.ok === false) return false;
      return snapshot.busy === true || snapshot.available_now === false || Number(snapshot.active_run_count || 0) >= Number(snapshot.max_local_concurrency || 1);
    }

    async function chatConsoleMaybeShowRemoteWorkerControlForBusyLocal({cell, runId, threadId, pendingRequest = null}) {
      if (!cell || cell.type !== "ai") return null;
      const request = pendingRequest || chatConsoleRegisterPendingLocalAiRequest({cell, runId, threadId, endpoint: "", payload: null});
      try {
        const snapshot = await chatConsoleFetchLocalAiCapacityNow(threadId);
        chatConsoleRemoteWorkerControlState.lastCapacitySnapshot = snapshot;
        if (!chatConsoleShouldOpenRemoteWorkerControlForCapacity(snapshot)) {
          return {
            capacity: snapshot,
            pending_request_id: request.id,
            choice: {
              mode: "local_available",
              reason: "local_ai_available",
              auto: true,
              pending_request_id: request.id,
              thread_id: request.thread_id || threadId || "",
              run_id: request.run_id || runId || ""
            }
          };
        }

        chatConsoleSetStatus("local AI is busy; waiting on Remote Worker control before starting the pending local request");
        const choice = await new Promise((resolve) => {
          chatConsoleShowRemoteWorkerControlModal({cell, runId, threadId, capacity: snapshot, pendingRequest: request, resolveChoice: resolve});
        });
        let latestCapacity = chatConsoleRemoteWorkerControlState.lastCapacitySnapshot || snapshot;
        if (choice?.mode === "wait_local" && chatConsoleShouldOpenRemoteWorkerControlForCapacity(latestCapacity)) {
          latestCapacity = await chatConsoleWaitForLocalAiCapacityAvailable(threadId);
          choice.waited_for_local_available = true;
        }
        return {capacity: latestCapacity, choice, pending_request_id: request.id};
      } catch (error) {
        chatConsoleSetStatus(`local AI capacity preflight unavailable: ${error.message || error}`);
        return null;
      }
    }

    function chatConsoleShouldShowThinking(cell) {
      return Boolean(cell && cell.type !== "output" && cell.status === "running");
    }

    function chatConsoleHasWaitingCells() {
      return Boolean(chatConsoleState?.cells?.some((cell) => chatConsoleShouldShowThinking(cell)));
    }

    function chatConsoleThinkingRunId(cell) {
      return String(cell?.run_id || chatConsoleActiveAiRequests.get(cell?.id)?.run_id || "");
    }

    function chatConsoleThinkingThreadId(cell) {
      return String(chatConsoleActiveAiRequests.get(cell?.id)?.thread_id || chatConsoleState?.id || "");
    }

    function chatConsoleThinkingThreadIds() {
      const ids = new Set();
      (chatConsoleState?.cells || [])
        .filter((cell) => chatConsoleShouldShowThinking(cell))
        .forEach((cell) => {
          const threadId = chatConsoleThinkingThreadId(cell);
          if (threadId) ids.add(threadId);
        });
      return [...ids];
    }

    function compactChatConsoleThinkingText(value, limit = 360) {
      const text = String(value || "").replace(/\s+/g, " ").trim();
      if (!text) return "";
      return text.length > limit ? `${text.slice(0, Math.max(0, limit - 1)).trim()}…` : text;
    }

    function chatConsoleThinkingEventText(event) {
      const data = event?.data && typeof event.data === "object" ? event.data : {};
      const candidates = [
        data.latest_text ? `model stream: ${data.latest_text}` : "",
        data.history_label,
        data.status_preview,
        data.running_text,
        data.thinking_preview,
        data.prompt_preview ? `prompt: ${data.prompt_preview}` : "",
        data.user_prompt_preview ? `user prompt: ${data.user_prompt_preview}` : "",
        data.command_preview ? `command: ${data.command_preview}` : "",
        event?.message,
        event?.status
      ];
      return compactChatConsoleThinkingText(candidates.find((item) => String(item || "").trim()) || event?.title || "activity");
    }

    function chatConsoleThinkingEventDedupeKey(event) {
      const data = event?.data && typeof event.data === "object" ? event.data : {};
      const runId = String(data.run_id || "");
      const title = String(event?.title || event?.kind || "Activity");
      const ragType = String(data.rag_type || data.step || data.stage || "");
      const seed = `${title} ${event?.source || ""} ${event?.kind || ""} ${event?.message || ""} ${(event?.tags || []).join(" ")} ${ragType}`.toLowerCase();

      if (
        title === "Model text transmitted" ||
        data.latest_text ||
        Number(data.content_chars || 0) > 0 ||
        seed.includes("content_delta")
      ) {
        return `${runId}|model-content-stream|${ragType || "model_stream"}`;
      }

      if (
        title === "Model thinking stream active" ||
        Number(data.thinking_chars || 0) > 0 ||
        seed.includes("thinking_delta")
      ) {
        return `${runId}|model-private-thinking-stream|${ragType || "model_stream"}`;
      }

      if (seed.includes("still waiting") || seed.includes("waiting for streamed tokens")) {
        return `${runId}|model-waiting|${ragType || title}`;
      }

      return [
        runId,
        event?.source || "",
        event?.kind || "",
        title,
        event?.status || event?.severity || "",
        ragType,
        data.signal || ""
      ].join("|");
    }

    function chatConsoleThinkingEventCategory(event) {
      const tags = Array.isArray(event?.tags) ? event.tags.join(" ") : "";
      const data = event?.data && typeof event.data === "object" ? event.data : {};
      const seed = `${event?.source || ""} ${event?.kind || ""} ${event?.title || ""} ${event?.message || ""} ${tags} ${data.rag_type || ""} ${data.step || ""}`.toLowerCase();
      if (event?.severity === "error" || seed.includes("fault") || seed.includes("error") || seed.includes("failed")) return "fault";
      if (seed.includes("docker") || seed.includes("executor")) return "docker";
      if (seed.includes("retrieval") || seed.includes("context")) return "retrieval";
      if (seed.includes("rag")) return "rag";
      if (seed.includes("stream") || seed.includes("token") || seed.includes("thinking_delta") || seed.includes("content_delta")) return "stream";
      if (seed.includes("model") || seed.includes("ollama") || seed.includes("local-ai") || seed.includes("ai")) return "model";
      if (seed.includes("subprocess") || seed.includes("queued")) return "subprocess";
      if (seed.includes("waiting") || seed.includes("running") || seed.includes("started")) return "waiting";
      return "activity";
    }

    function chatConsoleThinkingEventsForCell(cell) {
      const runId = chatConsoleThinkingRunId(cell);
      if (!runId) return [];
      const seen = new Set();
      const newestUnique = [];
      (chatConsoleThinkingState.events || [])
        .filter((event) => String(event?.data?.run_id || "") === runId)
        .sort((left, right) => String(right.ts || right.timestamp || "").localeCompare(String(left.ts || left.timestamp || "")))
        .forEach((event) => {
          const key = chatConsoleThinkingEventDedupeKey(event);
          if (seen.has(key)) return;
          seen.add(key);
          newestUnique.push(event);
        });
      return newestUnique.slice(0, 16);
    }

    function renderChatConsoleThinkingCard(card) {
      const article = document.createElement("article");
      article.className = "chat-thinking-card";
      article.dataset.category = card.category || "activity";
      const title = document.createElement("strong");
      title.textContent = card.title || "Activity";
      const message = document.createElement("p");
      message.textContent = card.message || "";
      article.append(title, message);
      if (card.meta) {
        const meta = document.createElement("small");
        meta.textContent = card.meta;
        article.append(meta);
      }
      if (Array.isArray(card.models)) {
        const list = document.createElement("div");
        list.className = "chat-thinking-model-list";
        if (!card.models.length) {
          const row = document.createElement("div");
          row.className = "chat-thinking-model-row muted";
          row.textContent = card.emptyText || "No Ollama models are currently loaded.";
          list.append(row);
        } else {
          card.models.forEach((model) => {
            const row = document.createElement("div");
            row.className = "chat-thinking-model-row";
            const name = document.createElement("span");
            name.textContent = model.name || "model";
            const meta = document.createElement("small");
            meta.textContent = [
              model.id ? `id ${model.id}` : "",
              model.size || "",
              model.processor || "",
              model.context ? `context ${model.context}` : "",
              model.until ? `until ${model.until}` : ""
            ].filter(Boolean).join(" | ");
            row.append(name, meta);
            list.append(row);
          });
        }
        article.append(list);
      }
      return article;
    }

    function renderChatConsoleRemoteHubThinkingCard(cell) {
      const execution = cell?.remote_worker_execution && typeof cell.remote_worker_execution === "object" ? cell.remote_worker_execution : null;
      if (!execution || execution.source !== "remote_hub") return null;
      const status = String(execution.status || "running");
      const message = execution.message || (status === "completed"
        ? "Remote Hub response received."
        : status === "error"
          ? "Remote Hub could not complete this request."
          : "Remote Hub is working on this request.");
      return renderChatConsoleThinkingCard({
        category: status === "error" ? "fault" : "model",
        title: "Remote Hub",
        message,
        meta: [
          status,
          execution.run_id ? `run ${execution.run_id}` : "",
          execution.pending_request_id ? `pending ${chatConsoleShortRemoteWorkerId(execution.pending_request_id, 18)}` : "",
          "Remote Hub AI",
          execution.credit_spent
            ? `charged ${execution.payment?.charged_credits || ""} credits`.trim()
            : "no credits spent"
        ].filter(Boolean).join(" | ")
      });
    }

    function renderChatConsoleLocalCapacityThinkingCard(cell) {
      const threadId = chatConsoleThinkingThreadId(cell);
      const runId = chatConsoleThinkingRunId(cell);
      const snapshot = threadId ? chatConsoleThinkingState.localCapacityByThread?.[threadId] : null;
      if (!threadId) {
        return renderChatConsoleThinkingCard({
          category: "capacity",
          title: "Local AI capacity",
          message: "Local capacity was not checked because the active chat thread id is not available yet.",
          meta: runId ? `run ${runId} | capacity skipped` : "capacity skipped"
        });
      }
      if (!snapshot) {
        return renderChatConsoleThinkingCard({
          category: "capacity",
          title: "Local AI capacity",
          message: "Checking whether local AI is already busy for this request.",
          meta: [
            runId ? `run ${runId}` : "",
            `thread ${threadId}`,
            chatConsoleThinkingState.localCapacityStatus || "not checked"
          ].filter(Boolean).join(" | ")
        });
      }
      const card = Array.isArray(snapshot.cards)
        ? snapshot.cards.find((item) => item && item.key === "local_capacity") || snapshot.cards[0]
        : null;
      const reason = String(snapshot.reason_code || card?.details?.reason_code || "");
      const activeRuns = Array.isArray(snapshot.active_runs) ? snapshot.active_runs : [];
      const matchingActiveRun = activeRuns.find((item) => String(item?.thread_id || "") === threadId)
        || (runId ? activeRuns.find((item) => String(item?.run_id || "") === runId) : null)
        || activeRuns[0]
        || null;
      const activeRunId = String(card?.details?.active_run_id || matchingActiveRun?.run_id || "");
      const activeThreadId = String(card?.details?.active_thread_id || matchingActiveRun?.thread_id || "");
      const activeCount = Number(snapshot.active_run_count || card?.details?.active_run_count || 0);
      const maxConcurrency = Number(snapshot.max_local_concurrency || card?.details?.max_local_concurrency || 1);
      const updated = snapshot.updated_at ? new Date(snapshot.updated_at).toLocaleTimeString() : (chatConsoleThinkingState.localCapacityUpdatedAt ? new Date(chatConsoleThinkingState.localCapacityUpdatedAt).toLocaleTimeString() : "");
      let message = card?.message || snapshot.user_message || "Local AI capacity state is available.";
      if (reason === "thread_busy") {
        message = "This chat is currently using the local AI slot. Run id and thread id are separate identifiers.";
      } else if (reason === "local_concurrency_exhausted") {
        message = "Local AI has no free slot right now; see the active run/thread ids below.";
      } else if (reason === "local_ai_available") {
        message = "This chat can use local AI now.";
      }
      return renderChatConsoleThinkingCard({
        category: "capacity",
        title: card?.title || "Local AI capacity",
        message,
        meta: [
          reason,
          runId ? `run ${runId}` : "",
          activeRunId && activeRunId !== runId ? `active run ${activeRunId}` : "",
          `thread ${threadId}`,
          activeThreadId && activeThreadId !== threadId ? `active thread ${activeThreadId}` : "",
          `${activeCount} active / ${maxConcurrency} local slot${maxConcurrency === 1 ? "" : "s"}`,
          updated ? `updated ${updated}` : ""
        ].filter(Boolean).join(" | ")
      });
    }

    function renderChatConsoleThinkingPanel(cell) {
      const frame = document.createElement("details");
      frame.className = "chat-thinking-frame";
      frame.open = true;
      const runId = chatConsoleThinkingRunId(cell);
      const events = chatConsoleThinkingEventsForCell(cell);
      const summary = document.createElement("summary");
      summary.innerHTML = `
        <span class="chat-thinking-title">&gt; Thinking</span>
        <span class="chat-thinking-meta">node/leaf waiting${runId ? ` · ${runId}` : ""}</span>
        <span class="chat-thinking-collapse">∨ Collapse</span>
      `;
      const grid = document.createElement("div");
      grid.className = "chat-thinking-grid";
      grid.append(renderChatConsoleThinkingCard({
        category: "message",
        title: "Current message",
        message: compactChatConsoleThinkingText(cell.source || "Waiting for the current node/leaf result.", 420),
        meta: cell.type || "cell"
      }));
      grid.append(renderChatConsoleLocalCapacityThinkingCard(cell));
      const remoteHubCard = renderChatConsoleRemoteHubThinkingCard(cell);
      if (remoteHubCard) grid.append(remoteHubCard);
      if (!events.length) {
        grid.append(renderChatConsoleThinkingCard({
          category: "waiting",
          title: "Waiting for activity",
          message: "The request is running; current-message activity boxes will fill in as the route reports status.",
          meta: runId || "pending run"
        }));
      } else {
        events.forEach((event) => {
          grid.append(renderChatConsoleThinkingCard({
            category: chatConsoleThinkingEventCategory(event),
            title: event.title || event.kind || "Activity",
            message: chatConsoleThinkingEventText(event),
            meta: [
              event.source || "",
              event.status || event.severity || "",
              event.ts ? new Date(event.ts).toLocaleTimeString() : ""
            ].filter(Boolean).join(" | ")
          }));
        });
      }
      const modelsUpdated = chatConsoleThinkingState.ollamaModelsUpdatedAt
        ? `updated ${new Date(chatConsoleThinkingState.ollamaModelsUpdatedAt).toLocaleTimeString()}`
        : chatConsoleThinkingState.ollamaModelsStatus;
      grid.append(renderChatConsoleThinkingCard({
        category: "ollama",
        title: "Models in use on this computer",
        message: "Live Ollama process table for the local machine.",
        meta: modelsUpdated || "not checked",
        models: chatConsoleThinkingState.ollamaModels || [],
        emptyText: chatConsoleThinkingState.ollamaModelsStatus === "loaded"
          ? "No Ollama models are currently loaded."
          : "Ollama model usage has not loaded yet."
      }));
      frame.append(summary, grid);
      return frame;
    }

    async function refreshChatConsoleThinkingActivity() {
      if (chatConsoleThinkingState.refreshInFlight || !chatConsoleHasWaitingCells()) return;
      chatConsoleThinkingState.refreshInFlight = true;
      try {
        const nowMs = Date.now();
        const shouldRefreshModels = !chatConsoleThinkingState.ollamaModelsLastFetchMs
          || (nowMs - chatConsoleThinkingState.ollamaModelsLastFetchMs) >= CHAT_CONSOLE_THINKING_OLLAMA_INTERVAL_MS;
        const [snapshotResult, modelsResult] = await Promise.allSettled([
          fetch("/api/activity/snapshot", {cache: "no-store"}).then((response) => response.ok ? response.json() : Promise.reject(new Error(`activity HTTP ${response.status}`))),
          shouldRefreshModels
            ? fetch("/api/activity/ollama-ps", {cache: "no-store"}).then((response) => response.ok ? response.json() : Promise.reject(new Error(`ollama HTTP ${response.status}`)))
            : Promise.resolve({skipped: true})
        ]);
        if (snapshotResult.status === "fulfilled") {
          chatConsoleThinkingState.events = Array.isArray(snapshotResult.value.events) ? snapshotResult.value.events : [];
        }
        if (shouldRefreshModels) {
          chatConsoleThinkingState.ollamaModelsLastFetchMs = nowMs;
          if (modelsResult.status === "fulfilled") {
            chatConsoleThinkingState.ollamaModels = Array.isArray(modelsResult.value.models) ? modelsResult.value.models : [];
            chatConsoleThinkingState.ollamaModelsStatus = modelsResult.value.ok ? "loaded" : "unavailable";
            chatConsoleThinkingState.ollamaModelsUpdatedAt = chatConsoleNow();
          } else {
            chatConsoleThinkingState.ollamaModels = [];
            chatConsoleThinkingState.ollamaModelsStatus = "unavailable";
            chatConsoleThinkingState.ollamaModelsUpdatedAt = chatConsoleNow();
          }
        }
        const capacityThreadIds = chatConsoleThinkingThreadIds();
        if (capacityThreadIds.length) {
          const capacityResults = await Promise.allSettled(capacityThreadIds.map((threadId) => {
            const params = new URLSearchParams({
              thread_id: threadId,
              max_local_concurrency: "1"
            });
            return fetch(`/api/applications/chat-console/ai/capacity?${params.toString()}`, {cache: "no-store"})
              .then((response) => response.ok ? response.json() : Promise.reject(new Error(`capacity HTTP ${response.status}`)));
          }));
          capacityResults.forEach((result, index) => {
            const threadId = capacityThreadIds[index];
            if (result.status === "fulfilled") {
              chatConsoleThinkingState.localCapacityByThread[threadId] = result.value;
            } else {
              chatConsoleThinkingState.localCapacityByThread[threadId] = {
                ok: false,
                scope: "local-ai",
                available_now: false,
                busy: null,
                reason_code: "capacity_check_unavailable",
                user_message: `Local AI capacity check is unavailable: ${result.reason?.message || result.reason || "request failed"}`,
                thread_id: threadId,
                active_run_count: 0,
                max_local_concurrency: 1,
                cards: [{
                  key: "local_capacity",
                  title: "Local AI capacity",
                  status: "warning",
                  message: "Local AI capacity check is unavailable right now.",
                  details: {reason_code: "capacity_check_unavailable"}
                }],
                updated_at: chatConsoleNow()
              };
            }
          });
          chatConsoleThinkingState.localCapacityStatus = "loaded";
          chatConsoleThinkingState.localCapacityUpdatedAt = chatConsoleNow();
        }
      } finally {
        chatConsoleThinkingState.refreshInFlight = false;
      }
      if (chatConsoleHasWaitingCells()) renderChatConsoleThinkingActivityPanels();
    }

    function chatConsoleEnsureThinkingRefresh() {
      if (chatConsoleHasWaitingCells()) {
        if (!chatConsoleThinkingState.refreshTimer) {
          chatConsoleThinkingState.refreshTimer = setInterval(refreshChatConsoleThinkingActivity, CHAT_CONSOLE_THINKING_ACTIVITY_INTERVAL_MS);
          refreshChatConsoleThinkingActivity().catch(() => {});
        }
      } else if (chatConsoleThinkingState.refreshTimer) {
        clearInterval(chatConsoleThinkingState.refreshTimer);
        chatConsoleThinkingState.refreshTimer = null;
      }
    }

    function chatConsoleEmbeddedNotebooks() {
      return [...document.querySelectorAll("[data-chat-console-embedded-notebook]")];
    }

    function chatConsoleSpreadsheetEmbeddedNotebook() {
      return document.querySelector("#spreadsheet-embedded-chat-notebook") || document.querySelector('[data-chat-console-embed="spreadsheet"] [data-chat-console-embedded-notebook]');
    }

    function chatConsoleLegacyCalculatorNotebook() {
      if (typeof calculatorChatNotebook === "undefined" || !calculatorChatNotebook) return null;
      return calculatorChatNotebook.isConnected ? calculatorChatNotebook : null;
    }

    function chatConsoleActiveEmbeddedNotebook() {
      const activeApp = String(document.body?.dataset?.activeApp || "");
      if (!activeApp) return null;
      return chatConsoleEmbeddedNotebooks().find((notebook) => {
        const host = notebook.closest("[data-chat-console-embed]");
        return host?.dataset?.chatConsoleActiveApp === activeApp || host?.dataset?.chatConsoleEmbed === activeApp;
      }) || null;
    }

    function chatConsoleSetThreadUrl(threadId) {
      if (!threadId || !window.history?.replaceState) return;
      try {
        const url = new URL(window.location.href);
        url.searchParams.set("thread", threadId);
        window.history.replaceState(null, "", url.toString());
      } catch {
        // Leave URL unchanged when the environment does not expose a normal location.
      }
    }

    function chatConsoleLoadThreadFromUrl() {
      const threadStore = chatConsoleThreadsApi();
      if (!threadStore?.load || !threadStore?.setActive) return;
      let threadId = "";
      try {
        threadId = new URL(window.location.href).searchParams.get("thread") || "";
      } catch {
        threadId = "";
      }
      if (!threadId) return;
      threadStore.load();
      if (threadStore.get?.(threadId)) threadStore.setActive(threadId);
      else if (chatConsoleSaveStatus) chatConsoleSaveStatus.textContent = "thread link not found; showing active thread";
    }

    function chatConsoleLoadThreadState(thread, message = "thread loaded", options = {}) {
      if (!thread) {
        chatConsoleSetStatus("thread not found");
        return null;
      }
      chatConsoleState = migrateChatConsoleState(JSON.parse(JSON.stringify(thread)));
      if (!chatConsoleState.cells?.length) chatConsoleState.cells = [createChatConsoleCell("ai")];
      if (options.syncUrl !== false) chatConsoleSetThreadUrl(chatConsoleState.id);
      renderChatConsoleNotebook();
      chatConsoleSetStatus(message);
      return chatConsoleState;
    }

    function chatConsoleBuildThreadLink(thread) {
      const url = new URL(window.location.href);
      url.searchParams.set("thread", thread.id);
      return url.toString();
    }

    async function chatConsoleCopyThreadLink(thread) {
      if (!thread?.id) return false;
      const href = chatConsoleBuildThreadLink(thread);
      try {
        await navigator.clipboard?.writeText?.(href);
        return true;
      } catch {
        chatConsoleSetThreadUrl(thread.id);
        return false;
      }
    }

    function chatConsoleMountThreadController() {
      if (chatConsoleThreadController) {
        chatConsoleThreadController.render();
        return chatConsoleThreadController;
      }
      const controllerApi = chatConsoleThreadControllerApi();
      const threadStore = chatConsoleThreadsApi();
      if (!controllerApi?.mount || !threadStore || !chatConsoleApp) return null;
      chatConsoleThreadController = controllerApi.mount(chatConsoleApp, {
        threadStore,
        getActiveThreadId() {
          return chatConsoleState?.id || threadStore.getActive?.()?.id || "";
        },
        getActiveThread() {
          return (chatConsoleState?.id && threadStore.get?.(chatConsoleState.id)) || threadStore.getActive?.() || chatConsoleState || null;
        },
        setActiveThreadId(threadId, thread) {
          return threadStore.setActive?.(threadId) || thread || null;
        },
        beforeThreadChange() {
          if (chatConsoleState?.id) saveChatConsoleState("thread saved");
        },
        afterThreadChange(thread, context = {}) {
          chatConsoleLoadThreadState(thread, context.message || "thread loaded");
        },
        createThreadOptions() {
          return {title: "New Chat"};
        },
        status(message) {
          chatConsoleSetStatus(message);
        },
        buildThreadLink: chatConsoleBuildThreadLink,
        copyThreadLink: chatConsoleCopyThreadLink,
        focusAfterNew() {
          activeChatConsoleNotebook()?.querySelector("textarea")?.focus();
        }
      });
      return chatConsoleThreadController;
    }


    function chatConsoleEscapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function chatConsoleBooleanOption(value, fallback = true) {
      if (value === undefined || value === null || value === "") return Boolean(fallback);
      if (typeof value === "boolean") return value;
      const text = String(value).trim().toLowerCase();
      if (["0", "false", "off", "no", "hidden"].includes(text)) return false;
      if (["1", "true", "on", "yes", "visible"].includes(text)) return true;
      return Boolean(fallback);
    }

    function chatConsoleCssToken(value, fallback = "full") {
      const text = String(value || fallback || "full").trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
      return text || fallback || "full";
    }

    function chatConsoleClonePayload(value) {
      if (value === undefined || value === null) return null;
      try {
        return JSON.parse(JSON.stringify(value));
      } catch (error) {
        console.warn("Chat Console embedded context could not be serialized.", error);
        return null;
      }
    }

    function chatConsoleNormalizeMountPlugin(plugin, index = 0) {
      if (!plugin || typeof plugin !== "object" || Array.isArray(plugin)) return null;
      const id = chatConsoleCssToken(plugin.id || plugin.name || `mount-plugin-${index + 1}`, "");
      if (!id) return null;
      const label = String(plugin.label || plugin.title || id).trim() || id;
      const rawAppliesTo = plugin.appliesTo || plugin.cellType || plugin.cellTypes || "ai";
      const appliesTo = Array.isArray(rawAppliesTo)
        ? rawAppliesTo.map((item) => String(item || "").trim()).filter(Boolean)
        : String(rawAppliesTo || "ai").trim();
      return {
        ...plugin,
        id,
        label,
        checkedLabel: String(plugin.checkedLabel || plugin.enabledLabel || "").trim(),
        hint: String(plugin.hint || plugin.description || "").trim(),
        appliesTo,
        defaultEnabled: chatConsoleBooleanOption(
          plugin.defaultEnabled !== undefined ? plugin.defaultEnabled : plugin.defaultChecked,
          false
        ),
        disabled: chatConsoleBooleanOption(plugin.disabled, false),
        endpoint: String(plugin.endpoint || plugin.evaluateEndpoint || "").trim()
      };
    }

    function chatConsoleActiveMountPlugins(session = chatConsoleActiveEmbeddedSessionForPayload()) {
      const raw = session?.options?.plugins || session?.options?.mountPlugins || session?.config?.plugins || [];
      if (!Array.isArray(raw)) return [];
      return raw
        .map((plugin, index) => chatConsoleNormalizeMountPlugin(plugin, index))
        .filter(Boolean);
    }

    function chatConsoleMountPluginAppliesToCell(plugin, cell) {
      if (!plugin || !cell || cell.type === "output") return false;
      const appliesTo = plugin.appliesTo;
      if (Array.isArray(appliesTo)) return appliesTo.includes(cell.type) || appliesTo.includes("*");
      const text = String(appliesTo || "ai").trim();
      return text === "*" || text === cell.type;
    }

    function chatConsoleMountPluginOptions(cell) {
      return cell?.mount_plugin_options && typeof cell.mount_plugin_options === "object" && !Array.isArray(cell.mount_plugin_options)
        ? cell.mount_plugin_options
        : {};
    }

    function chatConsoleMountPluginOption(cell, plugin) {
      const options = chatConsoleMountPluginOptions(cell);
      const raw = options[plugin.id] && typeof options[plugin.id] === "object" && !Array.isArray(options[plugin.id]) ? options[plugin.id] : {};
      const explicit = Object.prototype.hasOwnProperty.call(raw, "enabled");
      return {
        ...raw,
        enabled: explicit ? Boolean(raw.enabled) : Boolean(plugin.defaultEnabled)
      };
    }

    function chatConsoleMountPluginEnabledForCell(cell, plugin) {
      return chatConsoleMountPluginOption(cell, plugin).enabled;
    }

    function updateChatConsoleMountPluginOption(cellId, pluginId, patch = {}) {
      const cell = chatConsoleState.cells.find((item) => item.id === cellId);
      if (!cell) return;
      const current = chatConsoleMountPluginOptions(cell);
      const nextForPlugin = {
        ...(current[pluginId] && typeof current[pluginId] === "object" && !Array.isArray(current[pluginId]) ? current[pluginId] : {}),
        ...patch
      };
      updateChatConsoleCell(cellId, {
        mount_plugin_options: {
          ...current,
          [pluginId]: nextForPlugin
        }
      });
      renderChatConsoleNotebook();
    }

    function chatConsoleEnabledMountPluginsForCell(cell, session = chatConsoleActiveEmbeddedSessionForPayload()) {
      return chatConsoleActiveMountPlugins(session)
        .filter((plugin) => chatConsoleMountPluginAppliesToCell(plugin, cell))
        .filter((plugin) => chatConsoleMountPluginEnabledForCell(cell, plugin));
    }

    function chatConsoleBuildMountPluginPayload(plugin, cell, context = {}) {
      const option = chatConsoleMountPluginOption(cell, plugin);
      const base = {
        id: plugin.id,
        label: plugin.label,
        enabled: Boolean(option.enabled)
      };
      if (plugin.pathway) base.pathway = String(plugin.pathway);
      if (plugin.targetKind) base.target_kind = String(plugin.targetKind);
      if (plugin.targetId) base.target_id = String(plugin.targetId);
      if (plugin.lockedTarget !== undefined) base.locked_target = Boolean(plugin.lockedTarget);
      if (plugin.endpoint) base.endpoint = plugin.endpoint;
      if (typeof plugin.buildPayload === "function") {
        try {
          const extra = plugin.buildPayload({
            ...context,
            cell,
            plugin,
            option
          });
          if (extra && typeof extra === "object" && !Array.isArray(extra)) {
            Object.assign(base, chatConsoleClonePayload(extra) || {});
          }
        } catch (error) {
          console.warn("Chat Console mount plugin payload builder failed.", error);
        }
      }
      return base;
    }

    function chatConsoleEmbeddedContextSource(config = {}) {
      if (!config) return null;
      const source = {
        embed_id: String(config.embedId || ""),
        active_app: String(config.activeApp || "")
      };
      if (config.targetKind) source.target_kind = String(config.targetKind);
      if (config.targetId) source.target_id = String(config.targetId);
      return source.embed_id || source.active_app || source.target_kind || source.target_id ? source : null;
    }

    function chatConsoleReadEmbeddedContext(session) {
      if (!session?.options || typeof session.options.getEmbeddedContext !== "function") return null;
      try {
        const context = session.options.getEmbeddedContext({
          config: session.config,
          host: session.host,
          source: chatConsoleEmbeddedContextSource(session.config)
        });
        return chatConsoleClonePayload(context);
      } catch (error) {
        console.warn("Chat Console embedded context provider failed.", error);
        return null;
      }
    }

    function chatConsoleDefaultEmbeddedLinkedTarget(config = {}, context = null) {
      const contextObject = context && typeof context === "object" && !Array.isArray(context) ? context : {};
      const kind = String(contextObject.target_kind || config.targetKind || "").trim();
      const id = String(contextObject.target_id || contextObject.project_id || contextObject.website_id || config.targetId || "").trim();
      const path = String(contextObject.target_path || contextObject.project_path || contextObject.website_path || contextObject.allowed_root || "").trim();
      if (!kind && !id && !path) return null;
      const target = {app: String(config.activeApp || config.embedId || "embedded-chat")};
      if (kind) target.kind = kind;
      if (id) target.id = id;
      if (path) target.path = path;
      return target;
    }

    function chatConsoleBuildEmbeddedThreadMetadata(config = {}, options = {}, context = null) {
      const source = chatConsoleEmbeddedContextSource(config);
      const metadata = {
        origin_app: source?.active_app || config.activeApp || config.embedId || "embedded-chat",
        embedded_chat: true
      };
      if (config.targetKind) metadata.target_kind = String(config.targetKind);
      if (config.targetId) metadata.target_id = String(config.targetId);
      const defaultTarget = chatConsoleDefaultEmbeddedLinkedTarget(config, context);
      if (defaultTarget) metadata.linked_targets = [defaultTarget];
      if (typeof options.buildThreadMetadata === "function") {
        try {
          const custom = options.buildThreadMetadata(context, {config, source, defaultTarget}) || {};
          if (custom && typeof custom === "object" && !Array.isArray(custom)) Object.assign(metadata, chatConsoleClonePayload(custom) || {});
        } catch (error) {
          console.warn("Chat Console embedded metadata builder failed.", error);
        }
      }
      return metadata;
    }

    function chatConsoleApplyEmbeddedThreadMetadata(threadStore, threadId, config = {}, options = {}, context = null) {
      if (!threadStore?.saveThread || !threadId) return null;
      const metadata = chatConsoleBuildEmbeddedThreadMetadata(config, options, context);
      return threadStore.saveThread(threadId, {metadata}, {makeActive: false});
    }

    function chatConsoleActiveEmbeddedSessionForPayload() {
      const notebook = typeof activeChatConsoleNotebook === "function" ? activeChatConsoleNotebook() : null;
      const host = notebook?.closest?.("[data-chat-console-embed]") || null;
      return (host && chatConsoleEmbeddedSessions.get(host)) || null;
    }

    function chatConsoleEmbeddedConfig(host, options = {}) {
      const embedId = String(options.embedId || host?.dataset?.chatConsoleEmbed || "embedded-chat");
      const idPrefix = String(options.idPrefix || host?.dataset?.chatConsoleIdPrefix || `${embedId}-chat`);
      const activeApp = String(options.activeApp || host?.dataset?.chatConsoleActiveApp || embedId);
      const classPrefix = String(options.classPrefix || host?.dataset?.chatConsoleClassPrefix || "");
      const title = String(options.title || host?.dataset?.chatConsoleTitle || "Chat Console");
      const subtitle = String(options.subtitle || host?.dataset?.chatConsoleSubtitle || "Embedded notebook cells use the shared Chat Console renderer.");
      const status = String(options.initialStatus || host?.dataset?.chatConsoleInitialStatus || "thread ready");
      const targetKind = String(options.targetKind || host?.dataset?.chatConsoleTargetKind || "");
      const targetId = String(options.targetId || host?.dataset?.chatConsoleTargetId || "");
      const layout = chatConsoleCssToken(options.layout || host?.dataset?.chatConsoleLayout || "full", "full");
      const showThreadRail = chatConsoleBooleanOption(
        options.showThreadRail !== undefined ? options.showThreadRail : host?.dataset?.chatConsoleShowThreadRail,
        true
      );
      const showCurrentThreadBar = chatConsoleBooleanOption(
        options.showCurrentThreadBar !== undefined ? options.showCurrentThreadBar : host?.dataset?.chatConsoleShowCurrentThreadBar,
        true
      );
      return {
        embedId,
        idPrefix,
        activeApp,
        classPrefix,
        title,
        subtitle,
        status,
        targetKind,
        targetId,
        layout,
        showThreadRail,
        showCurrentThreadBar,
        notebookId: String(options.notebookId || host?.dataset?.chatConsoleNotebookId || `${idPrefix}-notebook`),
        statusId: String(options.statusId || host?.dataset?.chatConsoleStatusId || `${idPrefix}-thread-status`),
        threadTitle: String(options.threadTitle || host?.dataset?.chatConsoleThreadTitle || `${title} Thread`)
      };
    }

    function chatConsoleEmbeddedClass(base, config, suffix) {
      return `${base}${config.classPrefix ? ` ${config.classPrefix}-${suffix}` : ""}`;
    }

    function chatConsoleBuildEmbeddedShell(host, options = {}) {
      if (!host) return null;
      const config = chatConsoleEmbeddedConfig(host, options);
      host.dataset.chatConsoleEmbed = config.embedId;
      host.dataset.chatConsoleActiveApp = config.activeApp;
      host.dataset.chatConsoleIdPrefix = config.idPrefix;
      host.dataset.chatConsoleClassPrefix = config.classPrefix;
      host.dataset.chatConsoleTargetKind = config.targetKind;
      host.dataset.chatConsoleTargetId = config.targetId;
      host.dataset.chatConsoleLayout = config.layout;
      host.dataset.chatConsoleShowThreadRail = config.showThreadRail ? "1" : "0";
      host.dataset.chatConsoleShowCurrentThreadBar = config.showCurrentThreadBar ? "1" : "0";
      if (host.dataset.chatConsoleEmbeddedShell === "1") return config;

      const threadNewId = `${config.idPrefix}-thread-new`;
      const threadSearchId = `${config.idPrefix}-thread-search`;
      const threadListId = `${config.idPrefix}-thread-list`;
      const threadRenameId = `${config.idPrefix}-thread-rename`;
      const threadCloneId = `${config.idPrefix}-thread-clone`;
      const threadArchiveId = `${config.idPrefix}-thread-archive`;
      const activeTitleId = `${config.idPrefix}-thread-active-title`;
      const activeMetaId = `${config.idPrefix}-thread-active-meta`;
      const copyLinkId = `${config.idPrefix}-thread-copy-link`;
      const shellClass = [
        chatConsoleEmbeddedClass("chat-console-shell", config, "chat-console-shell"),
        `chat-console-embedded-layout-${config.layout}`,
        config.showThreadRail ? "" : "chat-console-embedded-no-thread-rail",
        config.showCurrentThreadBar ? "" : "chat-console-embedded-no-current-thread-bar"
      ].filter(Boolean).join(" ");

      host.innerHTML = `
        <div class="${chatConsoleEscapeHtml(shellClass)}" data-chat-console-embedded-shell data-chat-console-layout="${chatConsoleEscapeHtml(config.layout)}">
          <header class="${chatConsoleEmbeddedClass("chat-console-header", config, "chat-console-header")}">
            <div>
              <strong>${chatConsoleEscapeHtml(config.title)}</strong>
              <span>${chatConsoleEscapeHtml(config.subtitle)}</span>
            </div>
            <div class="${chatConsoleEmbeddedClass("chat-console-save-status", config, "chat-thread-status")}" id="${chatConsoleEscapeHtml(config.statusId)}" data-chat-console-embedded-status>${chatConsoleEscapeHtml(config.status)}</div>
          </header>
          <div class="${chatConsoleEmbeddedClass("chat-console-thread-layout", config, "chat-console-layout")}">
            <aside class="${chatConsoleEmbeddedClass("chat-thread-rail", config, "chat-thread-rail")}" aria-label="${chatConsoleEscapeHtml(config.title)} threads"${config.showThreadRail ? "" : " hidden aria-hidden=\"true\""}>
              <div class="chat-thread-rail-header">
                <div>
                  <strong>Threads</strong>
                  <span>search and switch</span>
                </div>
                <button type="button" id="${chatConsoleEscapeHtml(threadNewId)}" data-chat-thread-new>New</button>
              </div>
              <label class="chat-thread-search-label" for="${chatConsoleEscapeHtml(threadSearchId)}">Search chats</label>
              <input id="${chatConsoleEscapeHtml(threadSearchId)}" class="chat-thread-search" type="search" placeholder="Search chats..." autocomplete="off" data-chat-thread-search>
              <div class="${chatConsoleEmbeddedClass("chat-thread-list", config, "chat-thread-list")}" id="${chatConsoleEscapeHtml(threadListId)}" role="list" aria-label="Saved chat threads" data-chat-thread-list></div>
              <div class="chat-thread-actions" aria-label="Current thread actions">
                <button type="button" id="${chatConsoleEscapeHtml(threadRenameId)}" data-chat-thread-rename>Rename</button>
                <button type="button" id="${chatConsoleEscapeHtml(threadCloneId)}" data-chat-thread-clone>Clone</button>
                <button type="button" id="${chatConsoleEscapeHtml(threadArchiveId)}" data-chat-thread-archive>Archive</button>
              </div>
            </aside>
            <section class="${chatConsoleEmbeddedClass("chat-thread-workspace", config, "chat-workspace")}" aria-label="Active embedded chat thread">
              <div class="${chatConsoleEmbeddedClass("chat-thread-current-bar", config, "chat-current-bar")}"${config.showCurrentThreadBar ? "" : " hidden aria-hidden=\"true\""}>
                <div>
                  <strong id="${chatConsoleEscapeHtml(activeTitleId)}" data-chat-thread-active-title>Current thread</strong>
                  <span id="${chatConsoleEscapeHtml(activeMetaId)}" data-chat-thread-active-meta>No thread selected</span>
                </div>
                <div class="chat-thread-current-actions">
                  <button type="button" id="${chatConsoleEscapeHtml(copyLinkId)}" data-chat-thread-copy-link>Copy link</button>
                </div>
              </div>
              <div class="${chatConsoleEmbeddedClass("chat-console-notebook", config, "embedded-chat-notebook")}" id="${chatConsoleEscapeHtml(config.notebookId)}" aria-label="${chatConsoleEscapeHtml(config.title)} embedded Chat Console notebook cells" data-chat-console-embedded-notebook></div>
            </section>
          </div>
        </div>
      `;
      host.dataset.chatConsoleEmbeddedShell = "1";
      return config;
    }

    function chatConsoleMountEmbedded(root, options = {}) {
      const host = typeof root === "string" ? document.querySelector(root) : (root || document.querySelector("[data-chat-console-embed]"));
      const controllerApi = chatConsoleThreadControllerApi();
      const threadStore = chatConsoleThreadsApi();
      if (!host || !controllerApi?.mount || !threadStore?.load) return null;
      const config = chatConsoleBuildEmbeddedShell(host, options);
      const session = {host, config, options};
      chatConsoleEmbeddedSessions.set(host, session);
      threadStore.load();

      const embeddedContext = chatConsoleReadEmbeddedContext(session);
      let threadId = String(options.threadId || options.getLinkedThreadId?.() || "");
      let activeEmbeddedThreadId = threadId;
      const embeddedLinkedThreadId = () => String(options.getLinkedThreadId?.() || host.dataset.linkedThreadId || activeEmbeddedThreadId || "").trim();
      let thread = threadId ? threadStore.get?.(threadId) : null;
      if (!thread) {
        thread = threadStore.getActive?.() || threadStore.list?.()[0] || threadStore.create?.({
          title: config.threadTitle,
          metadata: chatConsoleBuildEmbeddedThreadMetadata(config, options, embeddedContext),
          makeActive: false
        });
        threadId = thread?.id || "";
        activeEmbeddedThreadId = threadId;
      }
      if (threadId && threadStore.setActive) threadStore.setActive(threadId);
      if (threadId) {
        thread = chatConsoleApplyEmbeddedThreadMetadata(threadStore, threadId, config, options, embeddedContext) || thread;
      }
      if (thread) chatConsoleLoadThreadState(thread, "embedded chat ready", {syncUrl: false});
      if (threadId && options.setLinkedThreadId) options.setLinkedThreadId(threadId, thread, {reason: "initialize", embedded_context: embeddedContext});

      chatConsoleEmbeddedThreadControllers.get(host)?.destroy?.();
      const controller = controllerApi.mount(host, {
        threadStore,
        elements: {
          newButton: host.querySelector("[data-chat-thread-new]"),
          searchInput: host.querySelector("[data-chat-thread-search]"),
          list: host.querySelector("[data-chat-thread-list]"),
          activeTitle: host.querySelector("[data-chat-thread-active-title]"),
          activeMeta: host.querySelector("[data-chat-thread-active-meta]"),
          renameButton: host.querySelector("[data-chat-thread-rename]"),
          cloneButton: host.querySelector("[data-chat-thread-clone]"),
          archiveButton: host.querySelector("[data-chat-thread-archive]"),
          copyLinkButton: host.querySelector("[data-chat-thread-copy-link]")
        },
        getActiveThreadId() {
          return embeddedLinkedThreadId() || chatConsoleState?.id || threadStore.getActive?.()?.id || "";
        },
        getActiveThread() {
          const linkedThreadId = embeddedLinkedThreadId();
          return (linkedThreadId && threadStore.get?.(linkedThreadId)) || (chatConsoleState?.id && threadStore.get?.(chatConsoleState.id)) || threadStore.getActive?.() || chatConsoleState || null;
        },
        setActiveThreadId(nextThreadId, nextThread, context = {}) {
          const activeContext = chatConsoleReadEmbeddedContext(session);
          activeEmbeddedThreadId = String(nextThreadId || "");
          const active = threadStore.setActive?.(nextThreadId) || nextThread || null;
          const saved = nextThreadId ? (chatConsoleApplyEmbeddedThreadMetadata(threadStore, nextThreadId, config, options, activeContext) || active) : active;
          if (saved?.id) activeEmbeddedThreadId = String(saved.id);
          if (nextThreadId && options.setLinkedThreadId) options.setLinkedThreadId(nextThreadId, saved, {...context, embedded_context: activeContext});
          return saved;
        },
        beforeThreadChange() {
          if (chatConsoleState?.id) saveChatConsoleState("thread saved");
        },
        afterThreadChange(nextThread, context = {}) {
          const activeContext = chatConsoleReadEmbeddedContext(session);
          const saved = nextThread?.id ? (chatConsoleApplyEmbeddedThreadMetadata(threadStore, nextThread.id, config, options, activeContext) || nextThread) : nextThread;
          if (saved?.id) activeEmbeddedThreadId = String(saved.id);
          chatConsoleLoadThreadState(saved, context.message || "thread loaded", {syncUrl: false});
          if (saved?.id && options.setLinkedThreadId) options.setLinkedThreadId(saved.id, saved, {...context, embedded_context: activeContext});
        },
        createThreadOptions() {
          const activeContext = chatConsoleReadEmbeddedContext(session);
          return {
            title: config.threadTitle,
            metadata: chatConsoleBuildEmbeddedThreadMetadata(config, options, activeContext),
            makeActive: false
          };
        },
        cloneThreadOptions(activeThread) {
          return {title: `${window.MainComputerChatThreadController?.threadTitle?.(activeThread) || activeThread?.title || "Chat"} embedded copy`, makeActive: false};
        },
        status(message) {
          chatConsoleSetStatus(message);
          options.status?.(message);
        },
        buildThreadLink: options.buildThreadLink || chatConsoleBuildThreadLink,
        copyThreadLink: options.copyThreadLink || chatConsoleCopyThreadLink,
        focusAfterNew() {
          host.querySelector("[data-chat-console-embedded-notebook] textarea")?.focus();
        }
      });
      chatConsoleEmbeddedThreadControllers.set(host, controller);
      if (config.embedId === "spreadsheet") chatConsoleSpreadsheetThreadController = controller;
      renderChatConsoleNotebook();
      return controller;
    }

    function chatConsoleMountSpreadsheetEmbedded(root, options = {}) {
      const host = root || document.querySelector("#spreadsheet-chat-thread-panel");
      return chatConsoleMountEmbedded(host, {
        embedId: "spreadsheet",
        activeApp: "spreadsheet",
        idPrefix: "spreadsheet-chat",
        classPrefix: "spreadsheet",
        title: "Chat Console",
        subtitle: "Embedded in this workbook. Same chat threads, skinnier workspace.",
        notebookId: "spreadsheet-embedded-chat-notebook",
        statusId: "spreadsheet-chat-thread-status",
        threadTitle: "Spreadsheet Chat",
        ...options
      });
    }

    function chatConsoleRenderThreadController() {
      if (chatConsoleThreadController) chatConsoleThreadController.render();
      else chatConsoleMountThreadController();
    }

    function renderChatConsoleThreadSelector() {
      chatConsoleRenderThreadController();
    }

    function switchChatConsoleThread(threadId, message = "thread loaded") {
      return chatConsoleThreadController?.select(threadId, {message}) || null;
    }

    function createChatConsoleThread() {
      return chatConsoleThreadController?.create() || null;
    }

    function renameChatConsoleThread() {
      return chatConsoleThreadController?.rename() || null;
    }

    function cloneChatConsoleThread() {
      return chatConsoleThreadController?.clone() || null;
    }

    function archiveChatConsoleThread() {
      return chatConsoleThreadController?.archive() || null;
    }

    async function copyChatConsoleThreadLink() {
      return chatConsoleThreadController?.copyLink() || false;
    }

    function initChatConsoleApp() {
      if (chatConsoleInitialized) return;
      chatConsoleInitialized = true;
      chatConsoleLoadThreadFromUrl();
      chatConsoleState = loadChatConsoleState();
      if (!chatConsoleState.cells?.length) chatConsoleState = createChatConsoleNotebook();
      chatConsoleSetThreadUrl(chatConsoleState.id);
      renderChatConsoleNotebook();
      chatConsoleMountThreadController();
      // Cell creation lives in the shared notebook renderer: + Insert creates a cell,
      // and that cell exposes the regular AI/JS/Python/etc. type tabs. Embedded
      // apps should not duplicate per-language creation buttons.
      chatConsoleOpenVarsSpreadsheet?.addEventListener("click", () => {
        exportChatConsoleVariablesToSpreadsheet().catch((error) => {
          chatConsoleSaveStatus.textContent = error.message || "shared variable export failed";
        });
      });
      chatConsoleClear?.addEventListener("click", () => {
        if (!confirm("Clear this chat thread?")) return;
        const existingId = chatConsoleState?.id || "";
        const cleared = createChatConsoleNotebook();
        if (existingId) cleared.id = existingId;
        cleared.title = "New Chat";
        chatConsoleState = cleared;
        saveChatConsoleState("thread cleared");
        renderChatConsoleNotebook();
        renderChatConsoleThreadSelector();
      });
    }
    function addChatConsoleCell(type = chatConsoleState.last_used_input_type || "ai", source = "", afterId = "") {
      const threadContext = getChatConsoleThreadContext(afterId);
      const cell = createChatConsoleCell(type, source, {
        thread_parent_output_cell_id: threadContext.thread_parent_output_cell_id,
        thread_parent_cell_id: threadContext.thread_parent_cell_id
      });
      const index = afterId ? chatConsoleState.cells.findIndex((item) => item.id === afterId) : -1;
      chatConsoleState.cells.splice(index >= 0 ? index + 1 : chatConsoleState.cells.length, 0, cell);
      if (chatConsoleInputTypes.has(type)) chatConsoleState.last_used_input_type = type;
      setChatConsoleSelectedCell(cell.id);
      saveChatConsoleState("cell added");
      renderChatConsoleNotebook();
      setTimeout(() => activeChatConsoleNotebook()?.querySelector(`[data-cell-id="${cell.id}"] textarea`)?.focus(), 0);
      return cell;
    }
    function setChatConsoleSelectedCell(cellId) {
      chatConsoleState.selected_cell_id = cellId || "";
    }
    function getChatConsoleThreadContext(cellId) {
      const cell = chatConsoleState.cells.find((item) => item.id === cellId);
      if (!cell) return {thread_parent_output_cell_id: null, thread_parent_cell_id: null};
      if (cell.type === "output") {
        return {thread_parent_output_cell_id: cell.id, thread_parent_cell_id: cell.id};
      }
      return {
        thread_parent_output_cell_id: cell.thread_parent_output_cell_id || null,
        thread_parent_cell_id: cell.id
      };
    }
    function chatConsoleContinuationTypeForCell(cell) {
      const type = cell?.type || chatConsoleState?.last_used_input_type || "ai";
      return chatConsoleInputTypes.has(type) ? type : "ai";
    }
    function ensureChatConsoleContinuationAfterOutput(outputCell, sourceCell) {
      if (!chatConsoleState || !outputCell?.id) return null;
      if (getChatConsoleChildrenForOutput(outputCell.id).length) return null;
      const cell = createChatConsoleCell(chatConsoleContinuationTypeForCell(sourceCell), "", {
        thread_parent_output_cell_id: outputCell.id,
        thread_parent_cell_id: outputCell.thread_parent_cell_id || outputCell.source_cell_id || sourceCell?.id || null
      });
      chatConsoleState.cells.push(cell);
      setChatConsoleSelectedCell(cell.id);
      return cell;
    }
    function updateChatConsoleCell(cellId, patch) {
      const cell = chatConsoleState.cells.find((item) => item.id === cellId);
      if (!cell) return;
      Object.assign(cell, patch, {updated_at: chatConsoleNow()});
      if (chatConsoleInputTypes.has(cell.type)) chatConsoleState.last_used_input_type = cell.type;
      setChatConsoleSelectedCell(cell.id);
      saveChatConsoleState("notebook saved");
    }
    function setChatConsoleCellType(cellId, type) {
      if (type === "output") return;
      updateChatConsoleCell(cellId, {type, status: "idle"});
      renderChatConsoleNotebook();
    }
    function moveChatConsoleCell(cellId, delta) {
      const index = chatConsoleState.cells.findIndex((cell) => cell.id === cellId);
      const next = index + delta;
      if (index < 0 || next < 0 || next >= chatConsoleState.cells.length) return;
      const [cell] = chatConsoleState.cells.splice(index, 1);
      chatConsoleState.cells.splice(next, 0, cell);
      saveChatConsoleState("cell moved");
      renderChatConsoleNotebook();
    }
    function deleteChatConsoleCell(cellId) {
      if (!confirm("Delete this cell?")) return;
      const deleted = chatConsoleState.cells.find((cell) => cell.id === cellId);
      if (deleted?.type === "output") {
        const source = chatConsoleState.cells.find((cell) => cell.id === deleted.source_cell_id);
        if (source) {
          source.output_variant_ids = (source.output_variant_ids || []).filter((id) => id !== cellId);
          source.selected_output_variant_index = Math.max(0, Math.min(Number(source.selected_output_variant_index || 0), source.output_variant_ids.length - 1));
        }
      }
      chatConsoleState.cells = chatConsoleState.cells.filter((cell) => cell.id !== cellId);
      if (!chatConsoleState.cells.length) chatConsoleState.cells.push(createChatConsoleCell("ai"));
      saveChatConsoleState("cell deleted");
      renderChatConsoleNotebook();
    }
    function chatConsoleNotebookTargets() {
      return [...new Set([chatConsoleNotebook, chatConsoleLegacyCalculatorNotebook(), ...chatConsoleEmbeddedNotebooks()].filter(Boolean))];
    }
    function activeChatConsoleNotebook() {
      const activeEmbeddedNotebook = chatConsoleActiveEmbeddedNotebook();
      if (activeEmbeddedNotebook) return activeEmbeddedNotebook;
      const spreadsheetNotebook = chatConsoleSpreadsheetEmbeddedNotebook();
      if (document.body.dataset.activeApp === "spreadsheet" && spreadsheetNotebook) {
        return spreadsheetNotebook;
      }
      const calculatorNotebook = chatConsoleLegacyCalculatorNotebook();
      if (document.body.dataset.activeApp === "calculator" && calculatorChatPanel && !calculatorChatPanel.hidden && calculatorNotebook) {
        return calculatorNotebook;
      }
      return chatConsoleNotebook || calculatorNotebook || spreadsheetNotebook || chatConsoleEmbeddedNotebooks()[0] || null;
    }
    async function exportChatConsoleVariablesToSpreadsheet() {
      const variables = typeof chatConsoleVariableSnapshot === "function" ? chatConsoleVariableSnapshot() : {};
      if (!variables || !Object.keys(variables).length) {
        throw new Error("No shared variables are available to export.");
      }
      chatConsoleSaveStatus.textContent = "exporting shared variables...";
      const response = await fetch("/api/applications/chat-console/shared-variables/export", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          variables,
          source: {
            application: "chat-console",
            thread_id: chatConsoleState?.id || "",
            active_thread_id: chatConsoleState?.id || "",
            thread_title: chatConsoleState?.title || "",
            selected_cell_id: chatConsoleState?.selected_cell_id || "",
            cell_count: Array.isArray(chatConsoleState?.cells) ? chatConsoleState.cells.length : 0,
            exported_at: chatConsoleNow(),
          },
        })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || `shared variable export returned ${response.status}`);
      }
      if (!data.spreadsheet_url) {
        throw new Error("Spreadsheet import URL was not returned.");
      }
      window.open(data.spreadsheet_url, "_blank", "noopener");
      chatConsoleSaveStatus.textContent = `shared variables exported (${data.count || Object.keys(variables).length})`;
      return data;
    }

    function findChatConsoleCellElement(target, cellId) {
      if (!target || !cellId) return null;
      return [...target.querySelectorAll("[data-cell-id]")].find((candidate) => candidate.dataset?.cellId === cellId) || null;
    }

    function chatConsoleRenderSignatureValue(value) {
      try {
        return JSON.stringify(value ?? null);
      } catch {
        return String(value ?? "");
      }
    }

    function chatConsoleVisibleCellsForRender() {
      const visibleCells = [];
      getChatConsoleRootCells().forEach((cell) => collectChatConsoleCellAndSelectedContinuation(cell, visibleCells));
      return visibleCells;
    }

    function chatConsoleCellRenderSignature(cell) {
      return chatConsoleRenderSignatureValue({
        id: cell.id,
        type: cell.type,
        status: cell.status || "",
        source: cell.source || "",
        run_id: cell.run_id || "",
        source_cell_id: cell.source_cell_id || "",
        thread_parent_output_cell_id: cell.thread_parent_output_cell_id || "",
        selected_output_variant_index: Number(cell.selected_output_variant_index || 0),
        output_variant_ids: cell.output_variant_ids || [],
        parts: cell.parts || [],
        attachments: cell.attachments || [],
        rag_assisted_thinking: Boolean(cell.rag_assisted_thinking),
        rag_assisted_thinking_options: cell.rag_assisted_thinking_options || {},
        mount_plugin_options: cell.mount_plugin_options || {},
        remote_worker_overflow_intent: cell.remote_worker_overflow_intent || {}
      });
    }

    function chatConsoleNotebookRenderSignature() {
      return chatConsoleRenderSignatureValue({
        cells: chatConsoleVisibleCellsForRender().map(chatConsoleCellRenderSignature),
        remote_worker_options: chatConsoleRemoteWorkerOptions()
      });
    }

    function captureChatConsoleNotebookFocus(target) {
      const active = document.activeElement;
      if (!target || !active || active.tagName !== "TEXTAREA" || !target.contains(active)) return null;
      const cellElement = active.closest("[data-cell-id]");
      const cellId = cellElement?.dataset?.cellId || "";
      if (!cellId) return null;
      return {
        cellId,
        selectionStart: active.selectionStart,
        selectionEnd: active.selectionEnd,
        scrollTop: active.scrollTop,
        scrollLeft: active.scrollLeft
      };
    }
    function restoreChatConsoleNotebookFocus(target, focusState) {
      if (!target || !focusState?.cellId) return;
      const cellElement = findChatConsoleCellElement(target, focusState.cellId);
      const textarea = cellElement?.querySelector("textarea");
      if (!textarea) return;
      const length = textarea.value.length;
      const selectionStart = Math.max(0, Math.min(Number(focusState.selectionStart) || 0, length));
      const selectionEnd = Math.max(selectionStart, Math.min(Number(focusState.selectionEnd) || selectionStart, length));
      try {
        textarea.focus({preventScroll: true});
      } catch {
        textarea.focus();
      }
      try {
        textarea.setSelectionRange(selectionStart, selectionEnd);
      } catch {
        // Ignore selection restoration failures for unsupported input states.
      }
      textarea.scrollTop = Number(focusState.scrollTop) || 0;
      textarea.scrollLeft = Number(focusState.scrollLeft) || 0;
    }
    function renderChatConsoleNotebook() {
      if (!chatConsoleState) return;
      const renderSignature = chatConsoleNotebookRenderSignature();
      chatConsoleNotebookTargets().forEach((target) => {
        if (chatConsoleNotebookRenderSignatures.get(target) === renderSignature && target.querySelector("[data-cell-id]")) {
          renderChatConsoleThinkingActivityPanels(target);
          return;
        }
        const focusState = captureChatConsoleNotebookFocus(target);
        target.innerHTML = "";
        renderChatConsoleVisibleThread(target);
        chatConsoleNotebookRenderSignatures.set(target, renderSignature);
        restoreChatConsoleNotebookFocus(target, focusState);
      });
      chatConsoleEnsureThinkingRefresh();
    }
    function renderChatConsoleVisibleThread(target = activeChatConsoleNotebook()) {
      if (!target) return;
      const visibleCells = chatConsoleVisibleCellsForRender();
      target.append(renderChatConsoleInsertStrip(""));
      visibleCells.forEach((cell, index) => {
        target.append(renderChatConsoleCell(cell));
        const nextCell = visibleCells[index + 1];
        if (cell.type === "output" && nextCell && nextCell.type !== "output") {
          target.append(renderChatConsoleInsertStrip(cell.id));
        }
      });
    }
    function renderChatConsoleThinkingActivityPanels(target = null) {
      const waitingCells = (chatConsoleState?.cells || []).filter((cell) => chatConsoleShouldShowThinking(cell));
      if (!waitingCells.length) return;
      const targets = target ? [target] : chatConsoleNotebookTargets();
      targets.forEach((notebookTarget) => {
        waitingCells.forEach((cell) => {
          const cellElement = findChatConsoleCellElement(notebookTarget, cell.id);
          if (!cellElement) return;

          const tabs = cellElement.querySelector(".chat-cell-tabs");
          if (tabs && !tabs.querySelector('[data-chat-cell-tab="thinking"]')) {
            tabs.append(renderChatConsoleThinkingTab());
          }

          const existingPanel = cellElement.querySelector(".chat-thinking-frame");
          const nextPanel = renderChatConsoleThinkingPanel(cell);
          if (existingPanel) {
            nextPanel.open = existingPanel.open;
            existingPanel.replaceWith(nextPanel);
          } else {
            const controls = cellElement.querySelector(".chat-cell-controls");
            if (controls) controls.before(nextPanel);
          }
        });
      });
    }
    function renderChatConsoleCellAndSelectedContinuation(cell, target = activeChatConsoleNotebook()) {
      if (!target) return;
      const visibleCells = [];
      collectChatConsoleCellAndSelectedContinuation(cell, visibleCells);
      visibleCells.forEach((visibleCell, index) => {
        target.append(renderChatConsoleCell(visibleCell));
        const nextCell = visibleCells[index + 1];
        if (visibleCell.type === "output" && nextCell && nextCell.type !== "output") {
          target.append(renderChatConsoleInsertStrip(visibleCell.id));
        }
      });
    }
    function collectChatConsoleCellAndSelectedContinuation(cell, visibleCells = []) {
      visibleCells.push(cell);
      const selectedOutput = getChatConsoleSelectedOutputForSource(cell.id);
      if (!selectedOutput) return visibleCells;
      visibleCells.push(selectedOutput);
      getChatConsoleChildrenForOutput(selectedOutput.id).forEach((child) => {
        collectChatConsoleCellAndSelectedContinuation(child, visibleCells);
      });
      return visibleCells;
    }
    function getChatConsoleRootCells() {
      return chatConsoleState.cells.filter((cell) => cell.type !== "output" && !cell.thread_parent_output_cell_id);
    }
    function getChatConsoleChildrenForOutput(outputCellId) {
      return chatConsoleState.cells.filter((cell) => cell.type !== "output" && cell.thread_parent_output_cell_id === outputCellId);
    }
    function getChatConsoleSelectedOutputForSource(sourceCellId) {
      const variants = getChatConsoleOutputVariants(sourceCellId);
      return variants[getChatConsoleSelectedVariantIndex(sourceCellId)] || null;
    }
    function getChatConsoleOutputVariants(sourceCellId) {
      const source = chatConsoleState.cells.find((candidate) => candidate.id === sourceCellId);
      const variants = chatConsoleState.cells.filter((candidate) => candidate.type === "output" && candidate.source_cell_id === sourceCellId);
      if (source?.output_variant_ids?.length) {
        const byId = new Map(variants.map((variant) => [variant.id, variant]));
        const ordered = source.output_variant_ids.map((id) => byId.get(id)).filter(Boolean);
        variants.forEach((variant) => {
          if (!source.output_variant_ids.includes(variant.id)) ordered.push(variant);
        });
        return ordered;
      }
      return variants.sort((left, right) => Number(left.variant_index || 0) - Number(right.variant_index || 0));
    }
    function getChatConsoleSelectedVariantIndex(sourceCellId) {
      const source = chatConsoleState.cells.find((candidate) => candidate.id === sourceCellId);
      const variants = getChatConsoleOutputVariants(sourceCellId);
      const rawIndex = Number(source?.selected_output_variant_index ?? variants.length - 1);
      return Math.max(0, Math.min(Number.isFinite(rawIndex) ? rawIndex : variants.length - 1, Math.max(variants.length - 1, 0)));
    }

    function chatConsoleCanApplyEvaluationResult(threadId, sourceCellId) {
      return Boolean(
        chatConsoleState?.id
        && String(chatConsoleState.id) === String(threadId || "")
        && chatConsoleState.cells?.some((candidate) => candidate.id === sourceCellId && candidate.type !== "output")
      );
    }
    function chatConsoleNormalizeOutputForSource(outputCell, sourceCell, variantIndex) {
      if (!outputCell || !sourceCell) return outputCell;
      outputCell.id = outputCell.id || chatConsoleId("out");
      outputCell.type = "output";
      outputCell.source_cell_id = sourceCell.id;
      outputCell.variant_index = variantIndex;
      outputCell.variant_group_id = `variants-${sourceCell.id}`;
      outputCell.thread_parent_output_cell_id = sourceCell.thread_parent_output_cell_id || null;
      outputCell.thread_parent_cell_id = sourceCell.id;
      return outputCell;
    }
    function chatConsoleAppendContinuationToState(state, outputCell, sourceCell) {
      if (!state || !outputCell?.id || !sourceCell?.id) return null;
      const hasChild = (state.cells || []).some((candidate) => candidate.type !== "output" && candidate.thread_parent_output_cell_id === outputCell.id);
      if (hasChild) return null;
      const cell = createChatConsoleCell(chatConsoleContinuationTypeForCell(sourceCell), "", {
        thread_parent_output_cell_id: outputCell.id,
        thread_parent_cell_id: outputCell.thread_parent_cell_id || outputCell.source_cell_id || sourceCell.id || null
      });
      state.cells.push(cell);
      state.selected_cell_id = cell.id;
      return cell;
    }
    function chatConsoleApplyEvaluationOutputToCurrentThread(sourceCell, outputCell, variantIndex, saveMessage) {
      if (!chatConsoleState || !sourceCell || !outputCell) return false;
      chatConsoleNormalizeOutputForSource(outputCell, sourceCell, variantIndex);
      chatConsoleState.cells.push(outputCell);
      sourceCell.output_variant_ids = [...(sourceCell.output_variant_ids || []), outputCell.id];
      sourceCell.selected_output_variant_index = variantIndex;
      sourceCell.status = outputCell.status === "error" ? "error" : "ok";
      ensureChatConsoleContinuationAfterOutput(outputCell, sourceCell);
      saveChatConsoleState(saveMessage || "cell evaluated");
      try {
        window.dispatchEvent(new CustomEvent("main-computer-chat-console-output-applied", {
          detail: {
            thread_id: chatConsoleState.id,
            source_cell_id: sourceCell.id,
            output_cell: outputCell,
            metadata: outputCell.metadata || {}
          }
        }));
      } catch {
        // Embedded hosts may choose to ignore chat output lifecycle notifications.
      }
      return true;
    }
    function chatConsolePersistEvaluationOutputToThread(threadId, sourceCellId, outputCell, saveMessage) {
      const store = chatConsoleThreadsApi();
      if (!store?.get || !store?.saveThread || !threadId || !sourceCellId || !outputCell) return false;
      const thread = store.get(threadId);
      if (!thread?.id || !Array.isArray(thread.cells)) return false;
      const draft = migrateChatConsoleState(JSON.parse(JSON.stringify(thread)));
      const sourceCell = draft.cells.find((candidate) => candidate.id === sourceCellId && candidate.type !== "output");
      if (!sourceCell) return false;
      const savedOutput = JSON.parse(JSON.stringify(outputCell));
      const variantIndex = (sourceCell.output_variant_ids || []).length;
      chatConsoleNormalizeOutputForSource(savedOutput, sourceCell, variantIndex);
      draft.cells.push(savedOutput);
      sourceCell.output_variant_ids = [...(sourceCell.output_variant_ids || []), savedOutput.id];
      sourceCell.selected_output_variant_index = variantIndex;
      sourceCell.status = savedOutput.status === "error" ? "error" : "ok";
      chatConsoleAppendContinuationToState(draft, savedOutput, sourceCell);
      draft.updated_at = chatConsoleNow();
      store.saveThread(threadId, draft, {replace: true, makeActive: false});
      chatConsoleSetStatus(saveMessage || "AI output saved to its original chat thread");
      renderChatConsoleThreadSelector();
      return true;
    }
    function chatConsoleApplyOrPersistEvaluationOutput(evaluationThreadId, sourceCell, outputCell, variantIndex, saveMessage) {
      if (chatConsoleCanApplyEvaluationResult(evaluationThreadId, sourceCell?.id || "")) {
        return chatConsoleApplyEvaluationOutputToCurrentThread(sourceCell, outputCell, variantIndex, saveMessage);
      }
      if (chatConsolePersistEvaluationOutputToThread(evaluationThreadId, sourceCell?.id || "", outputCell, saveMessage)) {
        return false;
      }
      chatConsoleSetStatus("AI output was not inserted because the active chat changed and the original thread is unavailable.");
      return false;
    }
    function isChatConsoleActiveOutput(cell) {
      const variants = getChatConsoleOutputVariants(cell.source_cell_id);
      if (variants.length <= 1) return true;
      return variants[getChatConsoleSelectedVariantIndex(cell.source_cell_id)]?.id === cell.id;
    }
    function renderChatConsoleCell(cell) {
      const element = document.createElement("article");
      const roleClass = cell.type === "output" ? "chat-console-cell-output chat-console-output-indent" : "chat-console-cell-input";
      element.className = `chat-cell chat-cell-${cell.type} ${roleClass}`;
      element.dataset.cellId = cell.id;
      element.addEventListener("focusin", () => setChatConsoleSelectedCell(cell.id));
      element.addEventListener("click", () => setChatConsoleSelectedCell(cell.id));
      if (cell.type !== "output") {
        element.addEventListener("dragover", (event) => {
          event.preventDefault();
          element.classList.add("drag-over");
        });
        element.addEventListener("dragleave", () => element.classList.remove("drag-over"));
        element.addEventListener("drop", (event) => {
          event.preventDefault();
          element.classList.remove("drag-over");
          addChatConsoleAttachments(cell.id, [...(event.dataTransfer?.files || [])]);
        });
      }
      element.append(renderChatConsoleTabs(cell));
      if (cell.type === "output") {
        element.append(renderChatConsoleOutputCell(cell));
      } else {
        element.append(renderChatConsoleInputCell(cell));
      }
      return element;
    }
    function renderChatConsoleTabs(cell) {
      const tabs = document.createElement("div");
      tabs.className = "chat-cell-tabs";
      const tabItems = cell.type === "output" ? chatConsoleOutputCellTypes : chatConsoleInputCellTypes;
      tabItems.forEach(({type, label}) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `chat-cell-tab${cell.type === type ? " active" : ""}`;
        button.textContent = label;
        button.dataset.chatCellTab = type;
        button.disabled = cell.type === "output";
        button.addEventListener("click", () => setChatConsoleCellType(cell.id, type));
        tabs.append(button);
      });
      if (chatConsoleShouldShowThinking(cell)) {
        tabs.append(renderChatConsoleThinkingTab());
      }
      return tabs;
    }
    function renderChatConsoleThinkingTab() {
      const thinking = document.createElement("button");
      thinking.type = "button";
      thinking.className = "chat-cell-tab chat-cell-thinking-tab active";
      thinking.textContent = "Thinking";
      thinking.disabled = true;
      thinking.dataset.chatCellTab = "thinking";
      return thinking;
    }
    function renderChatConsoleInsertStrip(afterId = "") {
      const strip = document.createElement("div");
      strip.className = "chat-console-insert-strip";
      const button = chatConsoleButton("+ Insert", () => addChatConsoleCell("ai", "", afterId));
      button.dataset.chatConsoleInsert = "ai";
      strip.append(button);
      return strip;
    }
    function chatConsoleCellTypeLabel(type) {
      const item = chatConsoleCellTypes.find((candidate) => candidate.type === type);
      return item?.label || type || "cell";
    }
    function chatConsoleInputPlaceholder(type) {
      if (type === "terminal") return "PowerShell command...";
      if (type === "mathics") return "Mathics expression...";
      if (type === "comment") return "Write notes...";
      if (type === "javascript") return "JavaScript code. Use vars.name = value or context.set('name', value) to share variables.";
      if (type === "python") return "Python code. Assign locals or use vars['name'] = value to share variables.";
      if (type === "basic") return "BASIC code. Use PRINT, GETVAR(\"name\"), and SETVAR(\"name\", value) for shared variables.";
      if (type === "calculator") return "Calculator expression, for example 1+1 or (4*7)/2.";
      return "Ask the model...";
    }
    function chatConsoleRunLabel(type) {
      if (type === "mathics") return "Evaluate";
      if (type === "terminal") return "Run";
      if (type === "comment") return "";
      if (type === "javascript") return "Run JS";
      if (type === "python") return "Run Python";
      if (type === "basic") return "Run BASIC";
      if (type === "calculator") return "Calculate";
      return "Run";
    }
    function chatConsoleRagAtOptions(cell) {
      const raw = cell?.rag_assisted_thinking_options && typeof cell.rag_assisted_thinking_options === "object" ? cell.rag_assisted_thinking_options : {};
      return {
        enabled: Boolean(cell?.rag_assisted_thinking),
        think: raw.think || "low"
      };
    }
    function updateChatConsoleRagAtOptions(cellId, patch = {}) {
      const cell = chatConsoleState.cells.find((item) => item.id === cellId);
      if (!cell) return;
      const current = chatConsoleRagAtOptions(cell);
      const next = {...current, ...patch};
      updateChatConsoleCell(cellId, {
        rag_assisted_thinking: Boolean(next.enabled),
        rag_assisted_thinking_options: {
          think: next.think || "low"
        }
      });
      renderChatConsoleNotebook();
    }
    function renderChatConsoleMountPluginControls(cell) {
      return chatConsoleActiveMountPlugins()
        .filter((plugin) => chatConsoleMountPluginAppliesToCell(plugin, cell))
        .map((plugin) => {
          const option = chatConsoleMountPluginOption(cell, plugin);
          const label = document.createElement("label");
          label.className = `chat-rag-at-toggle chat-mount-plugin-toggle${option.enabled ? " enabled" : ""}`;
          label.dataset.chatMountPluginId = plugin.id;
          if (plugin.hint) label.title = plugin.hint;
          const toggle = document.createElement("input");
          toggle.type = "checkbox";
          toggle.checked = option.enabled;
          toggle.disabled = Boolean(plugin.disabled);
          toggle.addEventListener("change", () => updateChatConsoleMountPluginOption(cell.id, plugin.id, {enabled: toggle.checked}));
          label.append(toggle, document.createTextNode(` ${option.enabled && plugin.checkedLabel ? plugin.checkedLabel : plugin.label}`));
          return label;
        });
    }
    function renderChatConsoleRagAtControls(cell) {
      const options = chatConsoleRagAtOptions(cell);
      const mountPluginControls = renderChatConsoleMountPluginControls(cell);
      const hasEnabledMountPlugin = mountPluginControls.some((control) => control.querySelector("input")?.checked);
      const remoteWorkerChatEnabled = chatConsoleRemoteWorkerWhenBusyForChatEnabled();
      const wrap = document.createElement("div");
      wrap.className = `chat-rag-at-controls${options.enabled || hasEnabledMountPlugin || remoteWorkerChatEnabled ? " enabled" : ""}`;

      const toggleLabel = document.createElement("label");
      toggleLabel.className = "chat-rag-at-toggle";
      const toggle = document.createElement("input");
      toggle.type = "checkbox";
      toggle.checked = options.enabled;
      toggle.addEventListener("change", () => updateChatConsoleRagAtOptions(cell.id, {enabled: toggle.checked}));
      toggleLabel.append(toggle, document.createTextNode(" RAG-AT"));

      const remoteWorkerLabel = document.createElement("label");
      remoteWorkerLabel.className = `chat-rag-at-toggle chat-remote-worker-chat-toggle${remoteWorkerChatEnabled ? " enabled" : ""}`;
      remoteWorkerLabel.title = "Use remote worker overflow when local AI is busy for this chat. Uncheck to back out.";
      const remoteWorkerToggle = document.createElement("input");
      remoteWorkerToggle.type = "checkbox";
      remoteWorkerToggle.checked = remoteWorkerChatEnabled;
      remoteWorkerToggle.dataset.chatRemoteWorkerWhenBusyForChat = "true";
      remoteWorkerToggle.addEventListener("change", () => {
        const toggleIntent = chatConsoleBuildRemoteWorkerControlIntent({
          mode: "remote_when_needed_for_chat",
          source: "chat_request_pane_checkbox",
          reason: remoteWorkerToggle.checked ? "chat_checkbox_enabled" : "chat_checkbox_disabled",
          active: remoteWorkerToggle.checked
        });
        const toggleCloseReason = chatConsoleBuildRemoteWorkerControlCloseReason({
          reason: remoteWorkerToggle.checked ? "chat_checkbox_enabled" : "chat_checkbox_disabled",
          source: "chat_request_pane_checkbox"
        });
        chatConsoleSetRemoteWorkerWhenBusyForChat(
          remoteWorkerToggle.checked,
          remoteWorkerToggle.checked ? "remote worker chat preference enabled" : "remote worker chat preference disabled",
          {intent: remoteWorkerToggle.checked ? toggleIntent : null, closeReason: toggleCloseReason}
        );
      });
      remoteWorkerLabel.append(remoteWorkerToggle, document.createTextNode(" Remote worker when local AI is busy for this chat"));

      const thinkLabel = document.createElement("label");
      thinkLabel.className = "chat-rag-at-field";
      thinkLabel.append(document.createTextNode("Thinking "));
      const think = document.createElement("select");
      ["off", "low", "medium", "high"].forEach((value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        if (String(options.think || "low") === value) option.selected = true;
        think.append(option);
      });
      think.disabled = !options.enabled;
      think.addEventListener("change", () => updateChatConsoleRagAtOptions(cell.id, {enabled: true, think: think.value}));
      thinkLabel.append(think);

      const hint = document.createElement("span");
      hint.className = "chat-rag-at-hint";
      hint.textContent = options.enabled ? "AI activity; Docker follows global executor setting" : hasEnabledMountPlugin ? "mount plugin on" : "off";

      wrap.append(toggleLabel, ...mountPluginControls, remoteWorkerLabel, thinkLabel, hint);
      return wrap;
    }
    function renderChatConsoleInputCell(cell) {
      const wrap = document.createElement("div");
      const textarea = document.createElement("textarea");
      textarea.value = cell.source || "";
      textarea.placeholder = chatConsoleInputPlaceholder(cell.type);
      const resizeTextarea = () => {
        textarea.style.height = "auto";
        textarea.style.height = `${textarea.scrollHeight}px`;
      };
      textarea.addEventListener("input", () => {
        updateChatConsoleCell(cell.id, {source: textarea.value});
        resizeTextarea();
      });
      textarea.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
          event.preventDefault();
          evaluateChatConsoleCell(cell.id);
        }
        if (event.key === "Escape") textarea.blur();
      });
      requestAnimationFrame(resizeTextarea);
      wrap.append(textarea);
      if (cell.type === "ai") wrap.append(renderChatConsoleRagAtControls(cell));
      if (chatConsoleShouldShowThinking(cell)) wrap.append(renderChatConsoleThinkingPanel(cell));
      const controls = document.createElement("div");
      controls.className = "chat-cell-controls";
      const runLabel = chatConsoleRunLabel(cell.type);
      if (cell.type === "terminal") controls.append(chatConsoleButton("Stage", () => stageChatConsoleTerminalCell(cell.id)));
      if (runLabel) {
        if (cell.type === "ai" && cell.status === "running") controls.append(chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id)));
        else controls.append(chatConsoleButton(runLabel, () => evaluateChatConsoleCell(cell.id)));
      }
      if (cell.type !== "output") controls.append(chatConsoleAttachmentButton(cell));
      controls.append(chatConsoleButton("Duplicate", () => duplicateChatConsoleCell(cell.id)));
      controls.append(chatConsoleButton("Up", () => moveChatConsoleCell(cell.id, -1)));
      controls.append(chatConsoleButton("Down", () => moveChatConsoleCell(cell.id, 1)));
      controls.append(chatConsoleButton("Delete", () => deleteChatConsoleCell(cell.id)));
      if (cell.status === "review-required") {
        const review = document.createElement("span");
        review.className = "chat-cell-review";
        review.textContent = "review required before Run";
        controls.append(review);
      }
      wrap.append(controls, renderChatConsoleAttachmentTray(cell));
      return wrap;
    }
    function chatConsoleButton(label, onClick) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", onClick);
      return button;
    }
    function chatConsoleAttachmentButton(cell) {
      const label = chatConsoleButton("Add attachment", () => input.click());
      const input = document.createElement("input");
      input.type = "file";
      input.multiple = true;
      input.hidden = true;
      input.addEventListener("change", () => addChatConsoleAttachments(cell.id, [...input.files]));
      label.append(input);
      return label;
    }
    function renderChatConsoleAttachmentTray(cell) {
      const tray = document.createElement("div");
      tray.className = "chat-cell-attachments";
      (cell.attachments || []).forEach((attachment) => {
        const item = document.createElement("div");
        item.className = "chat-attachment";
        if (String(attachment.mime_type || "").startsWith("image/") && attachment.preview_url) {
          const image = document.createElement("img");
          image.src = attachment.preview_url;
          image.alt = attachment.filename || "attachment";
          item.append(image);
        }
        const name = document.createElement("span");
        name.textContent = `${attachment.filename || "attachment"} (${attachment.size || 0} bytes)`;
        item.append(name, chatConsoleButton("Remove", () => removeChatConsoleAttachment(cell.id, attachment.id)));
        tray.append(item);
      });
      return tray;
    }
    async function addChatConsoleAttachments(cellId, files) {
      const cell = chatConsoleState.cells.find((item) => item.id === cellId);
      if (!cell) return;
      for (const file of files) {
        const dataUrl = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result || ""));
          reader.onerror = reject;
          reader.readAsDataURL(file);
        });
        const dataBase64 = String(dataUrl).split(",", 2)[1] || "";
        const response = await fetch("/api/applications/chat-console/attachments", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({filename: file.name, mime_type: file.type || "application/octet-stream", data_base64: dataBase64})
        });
        const data = await response.json();
        if (response.ok && data.attachment) cell.attachments.push(data.attachment);
      }
      saveChatConsoleState("attachment added");
      renderChatConsoleNotebook();
    }
    function removeChatConsoleAttachment(cellId, attachmentId) {
      const cell = chatConsoleState.cells.find((item) => item.id === cellId);
      if (!cell) return;
      cell.attachments = (cell.attachments || []).filter((attachment) => attachment.id !== attachmentId);
      saveChatConsoleState("attachment removed");
      renderChatConsoleNotebook();
    }
    function duplicateChatConsoleCell(cellId) {
      const cell = chatConsoleState.cells.find((item) => item.id === cellId);
      if (!cell || cell.type === "output") return;
      const duplicate = createChatConsoleCell(cell.type, cell.source || "", {
        attachments: JSON.parse(JSON.stringify(cell.attachments || [])),
        mount_plugin_options: JSON.parse(JSON.stringify(cell.mount_plugin_options || {})),
        thread_parent_output_cell_id: cell.thread_parent_output_cell_id || null,
        thread_parent_cell_id: cell.thread_parent_cell_id || null
      });
      const index = chatConsoleState.cells.findIndex((item) => item.id === cellId);
      chatConsoleState.cells.splice(index + 1, 0, duplicate);
      setChatConsoleSelectedCell(duplicate.id);
      saveChatConsoleState("cell duplicated");
      renderChatConsoleNotebook();
    }
    function stageChatConsoleTerminalCell(cellId) {
      updateChatConsoleCell(cellId, {status: "review-required"});
      renderChatConsoleNotebook();
    }
    async function stopChatConsoleAiRequest(cellId) {
      const cell = chatConsoleState.cells.find((item) => item.id === cellId);
      const active = chatConsoleActiveAiRequests.get(cellId) || {};
      const runId = active.run_id || cell?.run_id || "";
      const threadId = active.thread_id || chatConsoleState?.id || "";
      if (!runId && !threadId) return;
      try {
        await fetch("/api/applications/chat-console/ai/stop", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({run_id: runId, thread_id: threadId})
        });
        chatConsoleSetStatus(`stop requested for ${runId || threadId}`);
      } catch (error) {
        chatConsoleSetStatus(`stop request failed: ${error.message || error}`);
      }
      if (cell && cell.status === "running") cell.status = "cancelled";
      renderChatConsoleNotebook();
    }
    function chatConsoleIsRecoverableFetchError(error) {
      const message = String(error?.message || error || "").toLowerCase();
      return error instanceof TypeError || message.includes("failed to fetch") || message.includes("networkerror") || message.includes("load failed");
    }

    function chatConsoleSleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }

    async function pollChatConsoleAiRunResult(runId, threadId, options = {}) {
      const deadline = Date.now() + Number(options.timeoutMs || CHAT_CONSOLE_AI_RECONNECT_TIMEOUT_MS);
      let lastResult = null;
      while (Date.now() <= deadline) {
        const params = new URLSearchParams();
        if (runId) params.set("run_id", runId);
        if (threadId) params.set("thread_id", threadId);
        try {
          const response = await fetch(`/api/applications/chat-console/ai/run-result?${params.toString()}`, {cache: "no-store"});
          const data = await response.json().catch(() => ({}));
          if (response.ok && data.ok !== false) {
            lastResult = data;
            if (data.output_cell) return data;
            if (data.status === "failed" || data.status === "cancelled" || data.error) return data;
            if (data.running) {
              chatConsoleSetStatus(`reconnected to AI subprocess ${data.run_id || runId}; waiting for final output`);
            }
          }
        } catch (pollError) {
          chatConsoleSetStatus(`AI subprocess reconnect retrying: ${pollError.message || pollError}`);
        }
        await chatConsoleSleep(Number(options.intervalMs || CHAT_CONSOLE_AI_RECONNECT_INTERVAL_MS));
      }
      return lastResult || {ok: false, status: "timeout", error: "Timed out reconnecting to AI subprocess."};
    }

    async function recoverChatConsoleAiOutputAfterFetchLoss({cell, runId, threadId}) {
      if (!cell || !runId) return null;
      cell.status = "reconnecting";
      chatConsoleActiveAiRequests.set(cell.id, {run_id: runId, thread_id: threadId, reconnecting: true});
      chatConsoleSetStatus(`connection lost; reconnecting to AI subprocess ${runId}`);
      renderChatConsoleNotebook();
      const result = await pollChatConsoleAiRunResult(runId, threadId);
      if (result?.output_cell) {
        chatConsoleSetStatus(`recovered AI subprocess output for ${result.run_id || runId}`);
        return result.output_cell;
      }
      if (result?.running) {
        cell.status = "running";
        chatConsoleSetStatus(`AI subprocess ${result.run_id || runId} is still running out-of-band`);
        renderChatConsoleNotebook();
        return null;
      }
      throw new Error(result?.error || "AI subprocess reconnect finished without an output cell.");
    }

    async function evaluateChatConsoleCell(cellId) {
      const evaluationThreadId = chatConsoleState?.id || "";
      const cell = chatConsoleState.cells.find((item) => item.id === cellId);
      if (!cell || cell.type === "comment" || cell.type === "output") return;
      const startedRagAt = cell.type === "ai" ? chatConsoleRagAtOptions(cell) : {enabled: false};
      const startedRunId = cell.type === "ai"
        ? `${startedRagAt.enabled ? "rag_assisted_thinking_v4" : "chat_ai"}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
        : "";
      updateChatConsoleCell(cellId, startedRunId ? {status: "running", run_id: startedRunId} : {status: "running"});
      renderChatConsoleNotebook();
      const variantIndex = (cell.output_variant_ids || []).length;
      try {
        let outputCell = null;
        if (cell.type === "calculator") {
          outputCell = evaluateChatConsoleCalculatorCell({...cell, variant_index: variantIndex});
        } else if (chatConsoleCodeCellTypes.has(cell.type)) {
          outputCell = await evaluateChatConsoleCodeCell({...cell, variant_index: variantIndex});
        } else {
          const ragAt = cell.type === "ai" ? startedRagAt : {enabled: false};
          const payload = {cell: {...cell, variant_index: variantIndex}, thread_id: chatConsoleState?.id || ""};
          const embeddedSession = chatConsoleActiveEmbeddedSessionForPayload();
          const embeddedSource = chatConsoleEmbeddedContextSource(embeddedSession?.config);
          const embeddedContext = chatConsoleReadEmbeddedContext(embeddedSession);
          const activeMountPlugins = cell.type === "ai" ? chatConsoleEnabledMountPluginsForCell(cell, embeddedSession) : [];
          const mountPluginContext = {
            state: chatConsoleState,
            thread_id: chatConsoleState?.id || "",
            variant_index: variantIndex,
            embedded_context: embeddedContext,
            embedded_context_source: embeddedSource,
            embedded_session: embeddedSession,
            config: embeddedSession?.config || {}
          };
          if (embeddedSource) payload.embedded_context_source = embeddedSource;
          if (embeddedContext !== null) payload.embedded_context = embeddedContext;
          if (activeMountPlugins.length) {
            payload.mount_plugins = activeMountPlugins.map((plugin) => chatConsoleBuildMountPluginPayload(plugin, cell, mountPluginContext));
            payload.mount_plugin_state = payload.mount_plugins.reduce((acc, plugin) => {
              acc[plugin.id] = {enabled: Boolean(plugin.enabled)};
              return acc;
            }, {});
          }
          const pluginEndpoint = activeMountPlugins.find((plugin) => plugin.endpoint)?.endpoint || "";
          const endpoint = pluginEndpoint || (ragAt.enabled ? "/api/applications/chat-console/rag-assisted-thinking/evaluate" : "/api/applications/chat-console/cell/evaluate");
          const aiRunId = cell.type === "ai" ? (cell.run_id || startedRunId || `${ragAt.enabled ? "rag_assisted_thinking_v3" : "chat_ai"}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`) : "";
          if (aiRunId) {
            payload.run_id = aiRunId;
            payload.cell.run_id = aiRunId;
            chatConsoleActiveAiRequests.set(cell.id, {run_id: aiRunId, thread_id: payload.thread_id, rag: ragAt.enabled});
            window.MainComputerActivityDock?.recordLocalEvent?.({
              source: "chat-console",
              kind: "ai",
              time_model: "parallel",
              severity: "info",
              title: ragAt.enabled ? "AI RAG request started" : "AI notebook request started",
              message: cell.source || "AI request",
              status: "running",
              tags: ["ai", ragAt.enabled ? "rag" : "notebook", activeMountPlugins.length ? "mount-plugin" : "", "thinking", "local-ai", "chat-console"].filter(Boolean),
              data: {
                run_id: aiRunId,
                cell_id: cell.id,
                thread_id: payload.thread_id,
                activity_filter: "ai",
                prompt_preview: cell.source || "",
                raw_thinking_exposed: false,
                running_text: ragAt.enabled ? "RAG-AT request running" : "AI notebook request running",
                rag_type: ragAt.enabled ? "chat_console_rag_at" : "chat_console_ai",
                rag_types_seen: ragAt.enabled ? ["chat_console_rag_at"] : ["chat_console_ai"],
                mount_plugins: activeMountPlugins.map((plugin) => plugin.id)
              }
            });
          }
          if (ragAt.enabled) {
            payload.think = ragAt.think === "off" ? false : ragAt.think || "medium";
            payload.auto_apply = false;
            payload.queries = [cell.source || ""];
          }
          let pendingLocalRequest = null;
          let localAiStartLease = null;
          let remoteWorkerGate = null;
          let remoteHubIntentMode = "";
          let useRemoteHubForCurrentRequest = false;
          if (cell.type === "ai") {
            pendingLocalRequest = chatConsoleRegisterPendingLocalAiRequest({
              cell,
              runId: aiRunId,
              threadId: payload.thread_id,
              endpoint,
              payload
            });
            remoteWorkerGate = await chatConsoleMaybeShowRemoteWorkerControlForBusyLocal({
              cell,
              runId: aiRunId,
              threadId: payload.thread_id,
              pendingRequest: pendingLocalRequest
            });
            remoteHubIntentMode = chatConsoleCanonicalRemoteWorkerIntentMode(remoteWorkerGate?.choice?.mode);
            useRemoteHubForCurrentRequest = chatConsoleRemoteWorkerIntentUsesRemoteHubForCurrentRequest(remoteHubIntentMode);
            if (useRemoteHubForCurrentRequest) {
              chatConsoleSetStatus("Remote Hub is working on this request");
              chatConsoleStopRemoteWorkerControlCapacityWatcher();
            } else {
              if (remoteWorkerGate?.choice?.reason === "local_ai_available") {
                chatConsoleSetStatus("local AI is available; acquiring pending request lease before starting locally");
              } else if (remoteWorkerGate?.choice?.auto && remoteWorkerGate.choice.mode === "wait_local") {
                chatConsoleSetStatus("local AI became available; acquiring pending request lease before starting locally");
              } else if (remoteWorkerGate?.choice?.waited_for_local_available) {
                chatConsoleSetStatus("local AI became available after wait-local close; acquiring pending request lease before starting locally");
              }
              localAiStartLease = await chatConsoleWaitForPendingLocalAiStartLease({
                pendingRequestId: remoteWorkerGate?.pending_request_id || pendingLocalRequest.id,
                threadId: payload.thread_id
              });
              if (!localAiStartLease?.ok) {
                throw new Error(`Unable to acquire local AI start lease: ${localAiStartLease?.reason || "unknown"}`);
              }
              chatConsoleUpdatePendingLocalAiRequest(localAiStartLease.pending_request_id, {status: "running"});
            }
          }
          let data = {};
          try {
            if (useRemoteHubForCurrentRequest) {
              data = await chatConsoleSubmitRemoteHubOnce({pendingRequest: pendingLocalRequest, cell, payload, mode: remoteHubIntentMode});
            } else {
              const response = await fetch(endpoint, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload)
              });
              data = await response.json().catch(() => ({}));
              if (!response.ok || data.ok === false) throw new Error(data.error || `cell evaluation returned ${response.status}`);
            }
          } finally {
            if (localAiStartLease?.pending_request_id) {
              chatConsoleReleaseLocalAiStartLease(localAiStartLease.pending_request_id, "completed");
              chatConsoleForgetPendingLocalAiRequest(localAiStartLease.pending_request_id);
            } else if (pendingLocalRequest?.id) {
              if (useRemoteHubForCurrentRequest) {
                chatConsoleUpdatePendingLocalAiRequest(pendingLocalRequest.id, {
                  status: "remote_hub_completed",
                  completed_at: chatConsoleNow()
                });
              }
              chatConsoleForgetPendingLocalAiRequest(pendingLocalRequest.id);
            }
          }
          outputCell = data.output_cell;
          if (ragAt.enabled && data.run_id) chatConsoleSetStatus(`RAG-AT run ${data.run_id} complete; open Activity Monitor AI filter`);
        }
        chatConsoleApplyOrPersistEvaluationOutput(
          evaluationThreadId,
          cell,
          outputCell,
          variantIndex,
          cell.type === "calculator" ? "calculator cell evaluated" : chatConsoleCodeCellTypes.has(cell.type) ? "code cell evaluated" : "cell evaluated"
        );
      } catch (error) {
        const activeRequest = cell?.type === "ai" ? chatConsoleActiveAiRequests.get(cell.id) : null;
        const recoverableAiFetchLoss = Boolean(cell?.type === "ai" && activeRequest?.run_id && chatConsoleIsRecoverableFetchError(error));
        let recoveryDeferred = false;
        if (recoverableAiFetchLoss) {
          try {
            const recoveredOutput = await recoverChatConsoleAiOutputAfterFetchLoss({
              cell,
              runId: activeRequest.run_id,
              threadId: activeRequest.thread_id || chatConsoleState?.id || ""
            });
            if (recoveredOutput) {
              chatConsoleApplyOrPersistEvaluationOutput(
                evaluationThreadId,
                cell,
                recoveredOutput,
                variantIndex,
                "cell evaluation recovered after fetch loss"
              );
            } else {
              recoveryDeferred = true;
            }
          } catch (reconnectError) {
            error = reconnectError;
          }
        }
        if (!recoveryDeferred && cell.status !== "ok") {
          cell.status = "error";
          const outputCell = {
            id: chatConsoleId("out"),
            type: "output",
            source_cell_id: cell.id,
            variant_group_id: `variants-${cell.id}`,
            variant_index: (cell.output_variant_ids || []).length,
            parts: [{id: chatConsoleId("part"), kind: "error", title: recoverableAiFetchLoss ? "Reconnect failed" : "Error", content: error.message || "Cell evaluation failed", snippets: []}],
            status: "error",
            thread_parent_output_cell_id: cell.thread_parent_output_cell_id || null,
            thread_parent_cell_id: cell.id,
            created_at: chatConsoleNow(),
            updated_at: chatConsoleNow()
          };
          chatConsoleApplyOrPersistEvaluationOutput(
            evaluationThreadId,
            cell,
            outputCell,
            outputCell.variant_index,
            recoverableAiFetchLoss ? "cell evaluation reconnect failed" : "cell evaluation failed"
          );
        }
      }
      if (cell?.type === "ai" && cell.status !== "running" && cell.status !== "reconnecting") chatConsoleActiveAiRequests.delete(cell.id);
      renderChatConsoleNotebook();
    }
    function evaluateChatConsoleCalculatorCell(cell) {
      const result = evaluateCalculatorArithmeticExpression(cell.source || "");
      const now = chatConsoleNow();
      const output = result.ok ? String(result.value) : result.error || "check expression";
      return {
        id: chatConsoleId("out"),
        type: "output",
        source_cell_id: cell.id,
        variant_index: cell.variant_index || 0,
        parts: [{
          id: chatConsoleId("part"),
          kind: result.ok ? "calculator" : "error",
          title: result.ok ? "Calculator result" : "Calculator error",
          content: result.ok ? `${result.expression} = ${output}` : output,
          snippets: []
        }],
        provenance: {cell_type: "calculator", expression: result.expression || cell.source || ""},
        status: result.ok ? "ok" : "error",
        created_at: now,
        updated_at: now
      };
    }
    function renderChatConsoleOutputCell(cell) {
      const wrap = document.createElement("div");
      wrap.className = "chat-output-cell-block";
      const chrome = document.createElement("div");
      chrome.className = "chat-output-cell-chrome";
      const actions = document.createElement("div");
      actions.className = "chat-output-cell-actions";
      const variants = getChatConsoleOutputVariants(cell.source_cell_id);
      if (variants.length) {
        const index = variants.findIndex((item) => item.id === cell.id);
        const nav = document.createElement("div");
        nav.className = "chat-output-navigation chat-output-variant-row";
        const previous = chatConsoleButton("<", () => focusChatConsoleVariant(cell.source_cell_id, index - 1));
        previous.setAttribute("aria-label", "Previous result");
        previous.disabled = index <= 0;
        const counter = document.createElement("span");
        counter.className = "chat-output-counter";
        counter.textContent = `${index + 1}/${variants.length}`;
        const next = chatConsoleButton(">", () => focusChatConsoleVariant(cell.source_cell_id, index + 1));
        next.setAttribute("aria-label", "Next result");
        next.disabled = index >= variants.length - 1;
        nav.append(previous, counter, next);
        chrome.append(nav);
      }
      const copyButton = chatConsoleButton("Copy", () => copyChatConsoleOutputCell(cell.id));
      copyButton.className = "chat-output-copy";
      actions.append(copyButton, chatConsoleButton("Delete output", () => deleteChatConsoleCell(cell.id)));
      chrome.append(actions);
      const body = document.createElement("div");
      body.className = "chat-output-cell-body";
      (cell.parts || []).forEach((part) => body.append(renderChatConsoleOutputPart(cell, part)));
      wrap.append(chrome, body);
      return wrap;
    }
    function focusChatConsoleVariant(sourceCellId, index) {
      const variants = getChatConsoleOutputVariants(sourceCellId);
      const targetIndex = Math.max(0, Math.min(index, variants.length - 1));
      const source = chatConsoleState.cells.find((cell) => cell.id === sourceCellId);
      if (!source || !variants[targetIndex]) return;
      source.selected_output_variant_index = targetIndex;
      saveChatConsoleState("result selected");
      renderChatConsoleNotebook();
      chatConsoleNotebook.querySelector(`[data-cell-id="${variants[targetIndex].id}"]`)?.scrollIntoView({block: "center"});
    }
    function renderChatConsoleOutputPart(outputCell, part) {
      const section = document.createElement("section");
      section.className = `chat-output-part ${part.kind || "text"}`;
      const title = document.createElement("strong");
      title.textContent = ""; // part.title || part.kind || "Output";
      const content = document.createElement("div");
      if (part.kind === "markdown") {
        content.className = "chat-output-markdown";
        content.innerHTML = renderChatConsoleMarkdown(part.content || "");
      } else if (part.kind === "image" || part.kind === "plot") {
        content.className = part.kind === "plot" ? "chat-output-plot" : "chat-output-image";
        const dataUrl = part.metadata?.data_url || part.content?.data_url || "";
        if (dataUrl) {
          const image = document.createElement("img");
          image.src = dataUrl;
          image.alt = part.title || part.kind;
          content.append(image);
        } else {
          content.textContent = `[${part.kind === "plot" ? "Plot" : "Image"}: ${part.title || part.kind}]`;
        }
      } else if (part.kind === "table" && Array.isArray(part.content)) {
        content.append(renderChatConsoleOutputTable(part.content));
      } else {
        content.textContent = typeof part.content === "string" ? part.content : JSON.stringify(part.content, null, 2);
      }
      section.append(title, content);
      (part.snippets || []).forEach((snippet) => section.append(renderChatConsoleSnippet(outputCell, part, snippet)));
      return section;
    }
    function renderChatConsoleOutputTable(rows) {
      const table = document.createElement("table");
      table.className = "chat-output-table";
      rows.forEach((row, rowIndex) => {
        const tr = document.createElement("tr");
        (Array.isArray(row) ? row : Object.values(row || {})).forEach((value) => {
          const cell = document.createElement(rowIndex === 0 ? "th" : "td");
          cell.textContent = String(value ?? "");
          tr.append(cell);
        });
        table.append(tr);
      });
      return table;
    }
    function serializeChatConsolePartContent(part) {
      if (typeof part.content === "string") return part.content;
      if (part.content === null || part.content === undefined) return "";
      return JSON.stringify(part.content, null, 2);
    }
    function serializeOutputCellToPlainText(outputCell) {
      const lines = [`Output ${Number(outputCell.variant_index || 0) + 1}`];
      (outputCell.parts || []).forEach((part) => {
        const title = part.title || part.kind || "Output part";
        lines.push("", `${title}:`);
        if (part.kind === "image" || part.kind === "plot") {
          lines.push(`[${part.kind === "plot" ? "Plot" : "Image"}: ${title}]`);
        } else if (part.kind === "terminal") {
          lines.push(`Command: ${serializeChatConsolePartContent(part)}`);
          if (part.metadata?.cwd) lines.push(`cwd: ${part.metadata.cwd}`);
          if (part.metadata?.exit_code !== undefined) lines.push(`exit code: ${part.metadata.exit_code}`);
        } else if (part.kind === "table" && Array.isArray(part.content)) {
          part.content.forEach((row) => lines.push((Array.isArray(row) ? row : Object.values(row || {})).map((value) => String(value ?? "")).join("\t")));
        } else {
          lines.push(serializeChatConsolePartContent(part));
        }
        (part.snippets || []).forEach((snippet) => {
          lines.push("", `Snippet${snippet.language ? ` (${snippet.language})` : ""}:`, snippet.content || "");
        });
      });
      return lines.join("\n").trim();
    }
    function serializeOutputCellToHtml(outputCell) {
      const parts = (outputCell.parts || []).map((part) => {
        const title = escapeHtml(part.title || part.kind || "Output part");
        let body = "";
        if (part.kind === "markdown") {
          body = renderChatConsoleMarkdown(part.content || "");
        } else if (part.kind === "image" || part.kind === "plot") {
          const dataUrl = part.metadata?.data_url || part.content?.data_url || "";
          body = dataUrl
            ? `<img src="${escapeHtml(dataUrl)}" alt="${title}" />`
            : `<p>[${part.kind === "plot" ? "Plot" : "Image"}: ${title}]</p>`;
        } else if (part.kind === "table" && Array.isArray(part.content)) {
          body = `<table>${part.content.map((row, rowIndex) => `<tr>${(Array.isArray(row) ? row : Object.values(row || {})).map((value) => `<${rowIndex === 0 ? "th" : "td"}>${escapeHtml(value)}</${rowIndex === 0 ? "th" : "td"}>`).join("")}</tr>`).join("")}</table>`;
        } else {
          body = `<pre>${escapeHtml(serializeChatConsolePartContent(part))}</pre>`;
        }
        const snippets = (part.snippets || []).map((snippet) => `<pre><code>${escapeHtml(snippet.content || "")}</code></pre>`).join("");
        return `<section data-kind="${escapeHtml(part.kind || "text")}"><h3>${title}</h3>${body}${snippets}</section>`;
      }).join("");
      return `<article data-main-computer-output-cell="${escapeHtml(outputCell.id || "")}">${parts}</article>`;
    }
    function serializeOutputCellToJson(outputCell) {
      return JSON.stringify({
        id: outputCell.id || "",
        source_cell_id: outputCell.source_cell_id || "",
        variant_index: outputCell.variant_index || 0,
        parts: outputCell.parts || [],
        provenance: outputCell.provenance || {},
        thread_parent_output_cell_id: outputCell.thread_parent_output_cell_id || null
      }, null, 2);
    }
    async function copyChatConsoleOutputCell(outputCellId) {
      const outputCell = chatConsoleState.cells.find((cell) => cell.id === outputCellId && cell.type === "output");
      if (!outputCell) return;
      const text = serializeOutputCellToPlainText(outputCell);
      const html = serializeOutputCellToHtml(outputCell);
      const json = serializeOutputCellToJson(outputCell);
      try {
        if (navigator.clipboard?.write && window.ClipboardItem) {
          await navigator.clipboard.write([new ClipboardItem({
            "text/plain": new Blob([text], {type: "text/plain"}),
            "text/html": new Blob([html], {type: "text/html"}),
            "application/x-main-computer-output-cell+json": new Blob([json], {type: "application/x-main-computer-output-cell+json"})
          })]);
        } else if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(text);
        } else {
          const scratch = document.createElement("textarea");
          scratch.value = text;
          scratch.style.position = "fixed";
          scratch.style.left = "-9999px";
          document.body.append(scratch);
          scratch.select();
          document.execCommand("copy");
          scratch.remove();
        }
        if (chatConsoleSaveStatus) chatConsoleSaveStatus.textContent = "output copied";
      } catch (error) {
        try {
          await navigator.clipboard?.writeText?.(text);
          if (chatConsoleSaveStatus) chatConsoleSaveStatus.textContent = "output copied as text";
        } catch {
          if (chatConsoleSaveStatus) chatConsoleSaveStatus.textContent = `copy failed: ${error.message || error}`;
        }
      }
    }
    function renderChatConsoleMarkdown(markdown) {
      const withoutFences = String(markdown || "").replace(/```[\w-]*\n?[\s\S]*?```/g, "\n\n");
      const blocks = [];
      let listItems = [];
      function flushList() {
        if (!listItems.length) return;
        blocks.push(`<ul>${listItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
        listItems = [];
      }
      withoutFences.split(/\r?\n/).forEach((line) => {
        const heading = line.match(/^(#{1,4})\s+(.+)$/);
        const bullet = line.match(/^\s*[-*]\s+(.+)$/);
        if (heading) {
          flushList();
          const level = Math.min(4, heading[1].length + 2);
          blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
        } else if (bullet) {
          listItems.push(bullet[1]);
        } else if (line.trim()) {
          flushList();
          blocks.push(`<p>${renderInlineMarkdown(line.trim())}</p>`);
        } else {
          flushList();
        }
      });
      flushList();
      return blocks.join("") || "<p></p>";
    }
    function renderChatConsoleSnippet(outputCell, part, snippet) {
      const block = document.createElement("div");
      block.className = "chat-snippet";
      const pre = document.createElement("pre");
      pre.textContent = snippet.content || "";
      const buttons = document.createElement("div");
      buttons.className = "chat-snippet-promote";
      (snippet.suggested_target_cell_types || ["comment"]).forEach((targetType) => {
        const label = `+ ${chatConsoleCellTypeLabel(targetType)}`;
        buttons.append(chatConsoleButton(label, () => promoteChatConsoleSnippet(outputCell, part, snippet, targetType)));
      });
      if (typeof window.spreadsheetImportChatCodeSnippetFromChatConsole === "function") {
        buttons.append(chatConsoleButton("Import to selected cell", () => {
          window.spreadsheetImportChatCodeSnippetFromChatConsole(outputCell, part, snippet, chatConsoleState);
        }));
      }
      block.append(pre, buttons);
      return block;
    }
    function promoteChatConsoleSnippet(outputCell, part, snippet, targetType) {
      const type = chatConsoleInputTypes.has(targetType) ? targetType : "comment";
      const key = `${outputCell.id}:${part.id}:${snippet.id}:${type}:${chatConsoleSourceHash(snippet.content)}`;
      const existing = chatConsoleState.cells.find((cell) => cell.promoted_from?.promotion_key === key);
      if (existing) {
        setChatConsoleSelectedCell(existing.id);
        renderChatConsoleNotebook();
        activeChatConsoleNotebook()?.querySelector(`[data-cell-id="${existing.id}"]`)?.scrollIntoView({block: "center"});
        return;
      }
      const promoted = createChatConsoleCell(type, snippet.content || "", {
        status: type === "terminal" ? "review-required" : "idle",
        thread_parent_output_cell_id: outputCell.id,
        thread_parent_cell_id: outputCell.id,
        promoted_from: {
          promotion_key: key,
          promoted_from_output_cell_id: outputCell.id,
          promoted_from_output_part_id: part.id,
          promoted_from_snippet_id: snippet.id,
          promoted_from_variant_index: outputCell.variant_index || 0,
          promoted_at: chatConsoleNow()
        }
      });
      const index = chatConsoleState.cells.findIndex((cell) => cell.id === outputCell.id);
      chatConsoleState.cells.splice(index + 1, 0, promoted);
      setChatConsoleSelectedCell(promoted.id);
      saveChatConsoleState("snippet promoted manually");
      renderChatConsoleNotebook();
    }


    function chatConsoleOpenPaidOverflowReadinessUtility() {
      const existing = chatConsoleRemoteWorkerControlState.modal || document.querySelector("[data-chat-console-remote-worker-control-modal]");
      if (existing) {
        existing.querySelector("[data-chat-paid-overflow-readiness-card-title]")?.focus?.({preventScroll: true});
        chatConsoleRefreshRemoteHubReadiness().catch(() => {});
        return existing;
      }
      const runId = `paid_overflow_readiness_${Date.now().toString(36)}`;
      const threadId = chatConsoleState?.id || "paid-overflow-readiness-preview";
      const cell = {
        id: `paid_overflow_readiness_cell_${Date.now().toString(36)}`,
        type: "ai",
        run_id: runId,
        source: "Paid overflow readiness preview"
      };
      const pendingRequest = chatConsoleRegisterPendingLocalAiRequest({
        cell,
        runId,
        threadId,
        endpoint: "",
        payload: {
          run_id: runId,
          thread_id: threadId,
          prompt: "Paid overflow readiness preview",
          messages: [{role: "user", content: "Paid overflow readiness preview"}]
        }
      });
      return chatConsoleShowRemoteWorkerControlModal({
        cell,
        runId,
        threadId,
        pendingRequest,
        capacity: {
          ok: true,
          busy: true,
          available_now: false,
          active_run_count: 1,
          max_local_concurrency: 1,
          blocking_run_id: "preview",
          last_checked_at: chatConsoleNow()
        }
      });
    }

    function chatConsolePaidOverflowReadinessDebugPayload(states = {}) {
      const normalized = states && typeof states === "object" ? states : {};
      const keyAliases = {
        hub: "hub-reachability",
        setting: "paid-overflow-setting",
        wallet: "connected-wallet",
        key: "hub-key-validation",
        credits: "spendable-credits",
        estimate: "authorization-budget"
      };
      const valueForDefinition = (definition) => {
        const aliasEntry = Object.entries(keyAliases).find(([, key]) => key === definition.key);
        const alias = aliasEntry ? aliasEntry[0] : "";
        return String(normalized[definition.key] ?? normalized[alias] ?? "checking").trim().toLowerCase();
      };
      const okForValue = (value) => {
        if (["ok", "ready", "green", "pass", "passed", "true"].includes(value)) return true;
        if (["blocked", "bad", "red", "fail", "failed", "false"].includes(value)) return false;
        return null;
      };
      const checks = chatConsolePaidOverflowReadinessDefinitions().map((definition) => {
        const value = valueForDefinition(definition);
        const ok = okForValue(value);
        const detail = ok === true
          ? `${definition.shortTitle} passed.`
          : ok === false
            ? `${definition.shortTitle} is blocking paid overflow.`
            : `${definition.shortTitle} is checking or waiting for prerequisites.`;
        return {
          key: definition.key,
          title: definition.title,
          ok,
          unknownText: value === "inactive" ? "Not active" : "Checking",
          detail
        };
      });
      const ready = checks.every((check) => check.ok === true);
      const maxOutputTokens = Number(normalized.max_output_tokens ?? normalized.maxOutputTokens ?? 1024);
      const estimatedInputTokens = Number(normalized.estimated_input_tokens ?? normalized.estimatedInputTokens ?? 1);
      const creditsPerToken = String(normalized.credits_per_token ?? normalized.creditsPerToken ?? "0.001");
      const creditsPerTokenWei = String(normalized.credits_per_token_wei ?? normalized.creditsPerTokenWei ?? chatConsoleCreditDecimalToWei(creditsPerToken, "0.001").toString());
      const requiredCreditWei = String(
        normalized.required_credit_wei
        ?? normalized.requiredCreditWei
        ?? normalized.estimated_max_credit_wei
        ?? normalized.estimatedMaxCreditWei
        ?? chatConsoleCreditWeiProduct(estimatedInputTokens + maxOutputTokens, creditsPerTokenWei).toString()
      );
      const availableCreditWei = String(
        normalized.available_credit_wei
        ?? normalized.availableCreditWei
        ?? chatConsoleCreditDecimalToWei(normalized.available_credits ?? normalized.availableCredits ?? "0", "0").toString()
      );
      return {
        ready,
        valid: checks.find((check) => check.key === "hub-key-validation")?.ok === true,
        hub_reachable: checks.find((check) => check.key === "hub-reachability")?.ok === true,
        reason_code: ready ? "paid_overflow_ready" : "paid_overflow_debug_state",
        user_message: ready
          ? "Debug readiness state is ready."
          : "Debug readiness state is blocked or still checking.",
        wallet_address: "0x0000000000000000000000000000000000000000",
        account_id: "wallet:0x0000000000000000000000000000000000000000",
        multisession_key_id: "debug",
        available_credits: Number(normalized.available_credits ?? normalized.availableCredits ?? 0),
        available_credit_wei: availableCreditWei,
        available_credits_display: chatConsoleCreditWeiToText(availableCreditWei),
        required_credits: Number(normalized.required_credits ?? normalized.requiredCredits ?? 1),
        required_credit_wei: requiredCreditWei,
        required_credits_display: chatConsoleCreditWeiToText(requiredCreditWei),
        credit_ready: checks.find((check) => check.key === "spendable-credits")?.ok === true,
        funds_ok: checks.find((check) => check.key === "spendable-credits")?.ok === true,
        max_output_tokens: maxOutputTokens,
        credits_per_token: creditsPerToken,
        credits_per_token_wei: creditsPerTokenWei,
        estimated_max_credits_approx: String(normalized.estimated_max_credits_approx ?? normalized.estimatedMaxCreditsApprox ?? chatConsoleCreditWeiToText(requiredCreditWei)),
        estimated_max_credit_wei: requiredCreditWei,
        checks
      };
    }

    function chatConsoleSetPaidOverflowReadinessUtilityState(states = {}, options = {}) {
      chatConsoleOpenPaidOverflowReadinessUtility();
      const payload = chatConsolePaidOverflowReadinessDebugPayload(states);
      const readiness = chatConsolePaidOverflowReadinessFromContext(chatConsoleWorkerPaidOverflowContext(chatConsolePendingLocalAiRequest()), payload);
      const phase = options.phase || (Object.values(states || {}).some((value) => String(value).toLowerCase() === "checking") ? "checking" : "resolved");
      chatConsoleUpdatePaidOverflowReadinessCard(readiness, null, {phase});
      return readiness;
    }

    function chatConsoleShowPaidOverflowReadinessScenario(name = "invalid_key") {
      const scenarios = {
        checking: {hub: "checking", setting: "checking", wallet: "checking", key: "checking", credits: "checking", estimate: "checking"},
        hub_unreachable: {hub: "blocked", setting: "ok", wallet: "ok", key: "blocked", credits: "blocked", estimate: "blocked"},
        paid_overflow_disabled: {hub: "ok", setting: "blocked", wallet: "ok", key: "ok", credits: "ok", estimate: "ok", available_credits: 3, required_credit_wei: "1025000000000000000"},
        no_wallet: {hub: "ok", setting: "ok", wallet: "blocked", key: "blocked", credits: "blocked", estimate: "blocked"},
        invalid_key: {hub: "ok", setting: "ok", wallet: "ok", key: "blocked", credits: "ok", estimate: "ok", available_credits: 3, required_credit_wei: "1025000000000000000"},
        insufficient_credits: {hub: "ok", setting: "ok", wallet: "ok", key: "ok", credits: "blocked", estimate: "blocked", available_credits: 0, required_credit_wei: "4097000000000000000"},
        ready: {hub: "ok", setting: "ok", wallet: "ok", key: "ok", credits: "ok", estimate: "ok", available_credits: 3, required_credit_wei: "1025000000000000000"}
      };
      return chatConsoleSetPaidOverflowReadinessUtilityState(scenarios[name] || scenarios.invalid_key, {phase: name === "checking" ? "checking" : "resolved"});
    }

    window.MainComputerPaidOverflowReadiness = {
      open: chatConsoleOpenPaidOverflowReadinessUtility,
      refresh() {
        chatConsoleOpenPaidOverflowReadinessUtility();
        return chatConsoleRefreshRemoteHubReadiness();
      },
      close(reason = "paid_overflow_readiness_utility_close") {
        return chatConsoleHideRemoteWorkerControlModal(reason);
      },
      setState: chatConsoleSetPaidOverflowReadinessUtilityState,
      showScenario: chatConsoleShowPaidOverflowReadinessScenario,
      collapseAll() {
        chatConsoleTogglePaidOverflowReadinessRows(false);
      },
      expandAll() {
        chatConsoleTogglePaidOverflowReadinessRows(true);
      },
      getState() {
        return {
          open: Boolean(chatConsoleRemoteWorkerControlState.modal),
          smartCollapse: chatConsoleRemoteWorkerControlState.readinessSmartCollapse,
          lastHubReadiness: chatConsoleRemoteWorkerControlState.lastHubReadiness,
          readinessGeneration: chatConsoleRemoteWorkerControlState.readinessGeneration
        };
      }
    };


    window.chatConsoleMountEmbedded = chatConsoleMountEmbedded;
    window.chatConsoleMountSpreadsheetEmbedded = chatConsoleMountSpreadsheetEmbedded;
    window.MainComputerChatConsole = {
      ...(window.MainComputerChatConsole || {}),
      mountEmbedded: chatConsoleMountEmbedded,
      mountSpreadsheetEmbedded: chatConsoleMountSpreadsheetEmbedded,
      getActiveEmbeddedContext() {
        return chatConsoleReadEmbeddedContext(chatConsoleActiveEmbeddedSessionForPayload());
      },
      renderNotebook: renderChatConsoleNotebook,
      addCell: addChatConsoleCell,
      closeRemoteWorkerControl(reason = "programmatic_wait_local") {
        return chatConsoleChooseRemoteWorkerControlOption("wait_local", {reason, closeReason: reason});
      },
      paidOverflowReadiness: window.MainComputerPaidOverflowReadiness,
      getRemoteWorkerControlState() {
        return {
          open: Boolean(chatConsoleRemoteWorkerControlState.modal),
          lastChoice: chatConsoleRemoteWorkerControlState.lastChoice,
          lastThreadId: chatConsoleRemoteWorkerControlState.lastThreadId,
          lastRunId: chatConsoleRemoteWorkerControlState.lastRunId,
          lastAssessment: chatConsoleRemoteWorkerControlState.lastAssessment,
          lastIntent: chatConsoleRemoteWorkerControlState.lastIntent,
          lastCloseReason: chatConsoleRemoteWorkerControlState.lastCloseReason,
          chatWhenBusy: chatConsoleRemoteWorkerWhenBusyForChatEnabled(),
          globalWhenBusyIntent: chatConsoleRemoteWorkerControlState.globalWhenBusyIntent
        };
      }
    };
