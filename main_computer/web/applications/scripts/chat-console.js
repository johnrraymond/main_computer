    let chatConsoleThreadController = null;
    let chatConsoleSpreadsheetThreadController = null;
    const chatConsoleEmbeddedThreadControllers = new WeakMap();
    const chatConsoleEmbeddedSessions = new WeakMap();
    const chatConsoleActiveAiRequests = new Map();
    const CHAT_CONSOLE_AI_RECONNECT_TIMEOUT_MS = 10 * 60 * 1000;
    const CHAT_CONSOLE_AI_RECONNECT_INTERVAL_MS = 1200;
    const CHAT_CONSOLE_THINKING_ACTIVITY_INTERVAL_MS = 5000;
    const CHAT_CONSOLE_THINKING_OLLAMA_INTERVAL_MS = 20000;
    const CHAT_CONSOLE_REMOTE_WORKER_CONTROL_CAPACITY_INTERVAL_MS = 1000;
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
      lastChoice: null,
      lastShownAt: "",
      lastCellId: "",
      lastRunId: "",
      lastThreadId: "",
      globalWhenBusyIntent: false
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
      chatConsoleState.remote_worker_options = {
        ...chatConsoleRemoteWorkerOptions(),
        when_busy_for_chat: Boolean(enabled),
        updated_at: chatConsoleNow()
      };
      saveChatConsoleState(message);
      if (options.render !== false) renderChatConsoleNotebook();
      return true;
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
      const activeThreadIds = Array.isArray(capacity?.active_thread_ids) ? capacity.active_thread_ids.filter(Boolean) : [];
      const activeRunIds = Array.isArray(capacity?.active_runs)
        ? capacity.active_runs.map((run) => run?.run_id || run?.id || "").filter(Boolean)
        : [];
      return {
        status: busy ? "Busy" : "Available",
        message: chatConsoleRemoteWorkerControlSummary(capacity),
        items: [
          ["Reason", capacity?.reason_code || (busy ? "local_ai_busy" : "local_ai_available")],
          ["Active local AI runs", `${activeRuns} / ${maxConcurrency} local slot${maxConcurrency === 1 ? "" : "s"}`],
          ["Checked thread", threadId || capacity?.thread_id || ""],
          ["Run", runId || ""],
          ["Blocking thread", activeThreadIds[0] || ""],
          ["Blocking run", activeRunIds[0] || ""]
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

    function chatConsoleRemoteWorkerOptionCard({mode, title, kicker, description, details, defaultOption = false}) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `chat-remote-worker-control-option-card${defaultOption ? " default" : ""}`;
      button.dataset.chatRemoteWorkerOption = mode;
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
      const threadId = chatConsoleRemoteWorkerControlState.lastThreadId;
      if (!threadId) return;
      chatConsoleRemoteWorkerControlState.capacityRefreshInFlight = true;
      try {
        const snapshot = await chatConsoleFetchLocalAiCapacityNow(threadId);
        chatConsoleUpdateRemoteWorkerControlLocalCard(snapshot);
        if (!chatConsoleShouldOpenRemoteWorkerControlForCapacity(snapshot)) {
          chatConsoleChooseRemoteWorkerControlOption("wait_local", {
            auto: true,
            reason: "local_ai_available",
            closeReason: "auto-selected Wait for Available Local Worker because local AI became available"
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

    function chatConsoleRecordRemoteWorkerControlCellIntent(cellId, intent) {
      const cell = chatConsoleState?.cells?.find((item) => item.id === cellId);
      if (!cell) return false;
      cell.remote_worker_overflow_intent = intent;
      cell.updated_at = chatConsoleNow();
      saveChatConsoleState("remote worker request intent saved");
      return true;
    }

    function chatConsoleChooseRemoteWorkerControlOption(mode, context = {}) {
      const now = chatConsoleNow();
      const cellId = chatConsoleRemoteWorkerControlState.lastCellId;
      const choice = {
        mode,
        selected_at: now,
        auto: Boolean(context.auto),
        reason: context.reason || mode,
        thread_id: chatConsoleRemoteWorkerControlState.lastThreadId || "",
        run_id: chatConsoleRemoteWorkerControlState.lastRunId || ""
      };
      chatConsoleRemoteWorkerControlState.lastChoice = choice;

      if (mode === "use_remote_once") {
        chatConsoleRecordRemoteWorkerControlCellIntent(cellId, {
          mode: "once",
          selected_at: now,
          run_id: choice.run_id,
          phase: "modal_controls_phase2"
        });
      } else if (mode === "use_remote_when_needed_for_chat") {
        chatConsoleSetRemoteWorkerWhenBusyForChat(true, "remote worker chat preference enabled", {render: false});
      } else if (mode === "always_when_busy") {
        chatConsoleRemoteWorkerControlState.globalWhenBusyIntent = true;
        try {
          window.localStorage?.setItem?.("mainComputer.remoteWorker.alwaysWhenLocalAiBusy.intent", JSON.stringify({
            enabled: true,
            selected_at: now,
            phase: "modal_controls_phase2"
          }));
        } catch {
          // Local storage can be unavailable in hardened contexts; the modal still records the choice in memory.
        }
      }

      chatConsoleHideRemoteWorkerControlModal(context.closeReason || mode);
      if (mode === "use_remote_when_needed_for_chat" || mode === "use_remote_once") renderChatConsoleNotebook();
      return choice;
    }

    function chatConsoleShowRemoteWorkerControlModal({cell, runId, threadId, capacity}) {
      const existing = chatConsoleRemoteWorkerControlState.modal || document.querySelector("[data-chat-console-remote-worker-control-modal]");
      if (existing) existing.remove();

      const backdrop = document.createElement("div");
      backdrop.className = "chat-remote-worker-control-backdrop";
      backdrop.dataset.chatConsoleRemoteWorkerControlModal = "true";
      backdrop.setAttribute("role", "presentation");
      backdrop.addEventListener("click", (event) => {
        if (event.target === backdrop) {
          chatConsoleChooseRemoteWorkerControlOption("wait_local", {reason: "backdrop_wait_local"});
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
      eyebrow.textContent = "Phase 2 remote-worker controls";
      const title = document.createElement("h2");
      title.id = "chat-remote-worker-control-title";
      title.textContent = "Remote Worker control";
      headingWrap.append(eyebrow, title);
      const close = chatConsoleButton("×", () => chatConsoleChooseRemoteWorkerControlOption("wait_local", {reason: "close_wait_local"}));
      close.className = "chat-remote-worker-control-x";
      close.setAttribute("aria-label", "Wait for Available Local Worker and close");
      header.append(headingWrap, close);

      const description = document.createElement("p");
      description.id = "chat-remote-worker-control-description";
      description.textContent = "Local AI is busy. Review the current local worker and remote hub template, then choose how this request should wait or mark remote-worker intent.";

      const statusGrid = document.createElement("div");
      statusGrid.className = "chat-remote-worker-control-status-grid";
      const localStatus = chatConsoleRemoteWorkerLocalStatus(capacity, threadId, runId || cell?.run_id || "");
      const hubStatus = chatConsoleRemoteWorkerHubStatus();
      statusGrid.append(
        chatConsoleRemoteWorkerStatusCard({kind: "local", title: "Current Local AI Worker", ...localStatus}),
        chatConsoleRemoteWorkerStatusCard({kind: "hub", title: "Remote Hub / Workers", ...hubStatus})
      );

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
          details: "This is also selected automatically when the blocking local AI call disappears.",
          defaultOption: true
        }),
        chatConsoleRemoteWorkerOptionCard({
          mode: "use_remote_once",
          kicker: "This request",
          title: "Use Remote Worker This Once",
          description: "Mark this one blocked request for remote-worker overflow.",
          details: "No chat or global setting is changed in this phase."
        }),
        chatConsoleRemoteWorkerOptionCard({
          mode: "use_remote_when_needed_for_chat",
          kicker: "This chat",
          title: "Use Remote Worker When Needed for This Chat",
          description: "Enable the visible chat option beside the RAG controls.",
          details: "Uncheck that chat option later to back out."
        }),
        chatConsoleRemoteWorkerOptionCard({
          mode: "always_when_busy",
          kicker: "Global preference",
          title: "Always Use Remote Worker When Local AI Is Busy",
          description: "Record global remote-worker intent for busy-local overflow.",
          details: "To turn this off later, open the Worker app and unselect this option."
        })
      );

      const notice = document.createElement("p");
      notice.className = "chat-remote-worker-control-notice";
      notice.textContent = "Phase 2 records modal choices only. No credits are checked, held, or spent; no hub assessment or remote worker is contacted yet.";

      modal.append(header, description, statusGrid, optionsTitle, optionsGrid, notice);
      backdrop.append(modal);
      document.body.append(backdrop);

      chatConsoleRemoteWorkerControlState.modal = backdrop;
      chatConsoleRemoteWorkerControlState.lastCapacitySnapshot = capacity || null;
      chatConsoleRemoteWorkerControlState.lastShownAt = chatConsoleNow();
      chatConsoleRemoteWorkerControlState.lastCellId = cell?.id || "";
      chatConsoleRemoteWorkerControlState.lastRunId = runId || cell?.run_id || "";
      chatConsoleRemoteWorkerControlState.lastThreadId = threadId || "";

      chatConsoleRemoteWorkerControlState.escapeHandler = (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          chatConsoleChooseRemoteWorkerControlOption("wait_local", {reason: "escape_wait_local"});
        }
      };
      document.addEventListener("keydown", chatConsoleRemoteWorkerControlState.escapeHandler);

      const defaultOption = modal.querySelector('[data-chat-remote-worker-option="wait_local"]');
      defaultOption?.focus?.({preventScroll: true});
      chatConsoleStartRemoteWorkerControlCapacityWatcher();
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

    function chatConsoleShouldOpenRemoteWorkerControlForCapacity(snapshot) {
      if (!snapshot || snapshot.ok === false) return false;
      return snapshot.busy === true || snapshot.available_now === false || Number(snapshot.active_run_count || 0) >= Number(snapshot.max_local_concurrency || 1);
    }

    async function chatConsoleMaybeShowRemoteWorkerControlForBusyLocal({cell, runId, threadId}) {
      if (!cell || cell.type !== "ai") return null;
      try {
        const snapshot = await chatConsoleFetchLocalAiCapacityNow(threadId);
        if (chatConsoleShouldOpenRemoteWorkerControlForCapacity(snapshot)) {
          chatConsoleShowRemoteWorkerControlModal({cell, runId, threadId, capacity: snapshot});
        }
        return snapshot;
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
        chatConsoleSetRemoteWorkerWhenBusyForChat(remoteWorkerToggle.checked, remoteWorkerToggle.checked ? "remote worker chat preference enabled" : "remote worker chat preference disabled");
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
          if (cell.type === "ai") {
            await chatConsoleMaybeShowRemoteWorkerControlForBusyLocal({
              cell,
              runId: aiRunId,
              threadId: payload.thread_id
            });
          }
          const response = await fetch(endpoint, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok || data.ok === false) throw new Error(data.error || `cell evaluation returned ${response.status}`);
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
      getRemoteWorkerControlState() {
        return {
          open: Boolean(chatConsoleRemoteWorkerControlState.modal),
          lastChoice: chatConsoleRemoteWorkerControlState.lastChoice,
          lastThreadId: chatConsoleRemoteWorkerControlState.lastThreadId,
          lastRunId: chatConsoleRemoteWorkerControlState.lastRunId,
          chatWhenBusy: chatConsoleRemoteWorkerWhenBusyForChatEnabled(),
          globalWhenBusyIntent: Boolean(chatConsoleRemoteWorkerControlState.globalWhenBusyIntent)
        };
      }
    };
