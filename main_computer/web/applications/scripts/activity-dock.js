    (function () {
      const ACTIVITY_MAX_EVENTS = 400;
      const AI_SESSION_ARCHIVE_LIMIT = 12;
      const defaultFilters = {
        live: {id: "live", label: "Live", match: {}},
        faults: {id: "faults", label: "Faults", match: {severity: ["warn", "error"], fault: true}},
        subprocesses: {id: "subprocesses", label: "Subprocesses", match: {kind: ["subprocess"], tags: ["subprocess", "aider", "terminal", "docker", "vlc"]}},
        streams: {id: "streams", label: "Streams", match: {kind: ["stream"], tags: ["stream", "viewport", "vlc", "webgl"]}},
        ai: {id: "ai", label: "AI", match: {tags: ["ai", "local-ai", "rag", "thinking", "ollama", "docker", "executor"]}},
        fixtures: {id: "fixtures", label: "Fixtures", match: {time_model: ["static_fixture"]}},
        snapshots: {id: "snapshots", label: "Snapshots", match: {time_model: ["snapshot"]}},
        meta: {id: "meta", label: "Meta", match: {tags: ["meta", "fixture", "snapshot", "subprocess", "stream", "ai", "rag"]}}
      };

      const activityState = {
        open: false,
        filter: "live",
        filters: {...defaultFilters},
        events: [],
        backendEvents: [],
        tick: 0,
        lastSnapshot: null,
        lastBackendStatus: "local-only",
        initialized: false,
        aiSessionArchive: [],
        aiCurrentRunId: ""
      };

      const dom = {
        dock: () => document.querySelector("#machine-activity-dock"),
        toggle: () => document.querySelector("#activity-dock-toggle"),
        close: () => document.querySelector("#activity-dock-close"),
        heartbeat: () => document.querySelector("#machine-activity-heartbeat"),
        events: () => document.querySelector("#machine-activity-events"),
        filters: () => document.querySelector("#machine-activity-filters"),
        aiSession: () => document.querySelector("#machine-activity-ai-session"),
        filterState: () => document.querySelector("#machine-activity-filter-state"),
        summary: () => document.querySelector("#machine-activity-summary"),
        meta: () => document.querySelector("#machine-activity-meta"),
        metaShell: () => document.querySelector("#machine-activity-meta-shell")
      };

      function nowIso() {
        return new Date().toISOString();
      }

      function slug(value) {
        return String(value || "")
          .trim()
          .toLowerCase()
          .replace(/[^a-z0-9_.-]+/g, "-")
          .replace(/^-+|-+$/g, "") || "activity";
      }

      function severityFromEvent(event) {
        const seed = `${event.severity || ""} ${event.title || ""} ${event.message || ""} ${event.kind || ""}`.toLowerCase();
        if (seed.includes("error") || seed.includes("fail") || seed.includes("fault") || seed.includes("exception")) return "error";
        if (seed.includes("warn") || seed.includes("stalled") || seed.includes("visible-window")) return "warn";
        return event.severity || "info";
      }

      function normalizeEvent(event = {}) {
        const timestamp = event.ts || event.timestamp || nowIso();
        const source = String(event.source || "frontend").trim() || "frontend";
        const kind = String(event.kind || "event").trim() || "event";
        const timeModel = String(event.time_model || event.timeModel || "snapshot").trim() || "snapshot";
        const tags = Array.isArray(event.tags)
          ? event.tags.map((tag) => slug(tag)).filter(Boolean).slice(0, 12)
          : String(event.tags || "")
              .split(/[,\s]+/)
              .map((tag) => slug(tag))
              .filter(Boolean)
              .slice(0, 12);
        const normalized = {
          id: event.id || `${slug(source)}-${slug(kind)}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
          ts: timestamp,
          source,
          kind,
          time_model: timeModel,
          severity: severityFromEvent(event),
          title: String(event.title || event.event || kind || "Activity"),
          message: String(event.message || ""),
          status: String(event.status || ""),
          tags,
          fault: Boolean(event.fault),
          data: event.data && typeof event.data === "object" ? event.data : {}
        };
        if (normalized.severity === "error" || normalized.severity === "warn") normalized.fault = true;
        return normalized;
      }

      function mergeActivityEvents(existingEvents = [], incomingEvents = [], maxEvents = ACTIVITY_MAX_EVENTS) {
        const byId = new Map();
        const newest = (left, right) => {
          const leftMs = Date.parse(left?.ts || "") || 0;
          const rightMs = Date.parse(right?.ts || "") || 0;
          return rightMs >= leftMs ? right : left;
        };
        (existingEvents || []).forEach((event) => {
          const normalized = normalizeEvent(event);
          byId.set(normalized.id, normalized);
        });
        (incomingEvents || []).forEach((event) => {
          const normalized = normalizeEvent(event);
          const previous = byId.get(normalized.id);
          byId.set(normalized.id, previous ? newest(previous, normalized) : normalized);
        });
        return [...byId.values()]
          .sort((a, b) => String(b.ts || "").localeCompare(String(a.ts || "")))
          .slice(0, maxEvents);
      }

      function addEvent(event, options = {}) {
        const normalized = normalizeEvent(event);
        const collection = options.backend ? activityState.backendEvents : activityState.events;
        const existing = collection.findIndex((item) => item.id === normalized.id);
        if (existing >= 0) {
          collection.splice(existing, 1, normalized);
        } else {
          collection.unshift(normalized);
        }
        if (collection.length > ACTIVITY_MAX_EVENTS) collection.length = ACTIVITY_MAX_EVENTS;
        renderActivityDock();
        if (options.sync) {
          postLocalEvent(normalized).catch(() => {});
        }
        return normalized;
      }

      async function postLocalEvent(event) {
        const response = await fetch("/api/activity/event", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(event)
        });
        if (!response.ok) throw new Error(`activity event HTTP ${response.status}`);
        return response.json();
      }

      function eventMatchesFilter(event, filterId = activityState.filter) {
        const filter = activityState.filters[filterId] || activityState.filters.live;
        if (!filter || !filter.match || filterId === "live") return true;
        const match = filter.match;

        if (match.fault && !event.fault) return false;
        for (const key of ["source", "kind", "severity", "time_model", "status"]) {
          if (!match[key]) continue;
          const values = Array.isArray(match[key]) ? match[key] : [match[key]];
          if (!values.map(String).includes(String(event[key] || ""))) return false;
        }
        if (match.tags) {
          const required = Array.isArray(match.tags) ? match.tags.map(slug) : [slug(match.tags)];
          const eventTags = new Set((event.tags || []).map(slug));
          const sourceWords = slug(`${event.source} ${event.kind} ${event.title} ${event.message}`).split("-");
          const sourceSet = new Set(sourceWords);
          if (!required.some((tag) => eventTags.has(tag) || sourceSet.has(tag))) return false;
        }
        if (match.text) {
          const haystack = `${event.source} ${event.kind} ${event.title} ${event.message} ${(event.tags || []).join(" ")}`.toLowerCase();
          if (!haystack.includes(String(match.text).toLowerCase())) return false;
        }
        return true;
      }

      function allEvents() {
        const combined = [...activityState.events, ...activityState.backendEvents];
        const seen = new Set();
        return combined
          .filter((event) => {
            if (seen.has(event.id)) return false;
            seen.add(event.id);
            return true;
          })
          .sort((a, b) => String(b.ts).localeCompare(String(a.ts)));
      }

      function isAiSessionEvent(event) {
        const tags = new Set((event.tags || []).map(slug));
        const seed = slug(`${event.source} ${event.kind} ${event.title} ${event.message}`);
        return tags.has("ai") || tags.has("rag") || tags.has("local-ai") || tags.has("thinking") || seed.includes("ai") || seed.includes("rag");
      }

      function sessionStatusFromEvents(events) {
        const hasTerminal = events.some((event) => {
          const status = String(event.status || "").toLowerCase();
          const title = String(event.title || "").toLowerCase();
          return event.severity === "error" || ["failed", "error", "completed", "complete", "request_clarification", "abstain"].includes(status) || title.includes("completed") || title.includes("finished");
        });
        if (hasTerminal) {
          const terminal = events.find((event) => {
            const status = String(event.status || "").toLowerCase();
            const title = String(event.title || "").toLowerCase();
            return event.severity === "error" || ["failed", "error", "completed", "complete", "request_clarification", "abstain"].includes(status) || title.includes("completed") || title.includes("finished");
          });
          const terminalStatus = String(terminal?.status || "").toLowerCase();
          return terminal?.severity === "error" || terminalStatus === "failed" || terminalStatus === "error" ? "failed" : "completed";
        }
        for (const event of events) {
          const status = String(event.status || "").toLowerCase();
          if (["running", "started"].includes(status)) return "running";
        }
        return "connected";
      }

      function sessionDataValue(events, keys) {
        for (const event of events) {
          for (const key of keys) {
            const value = event.data?.[key];
            if (value !== undefined && value !== null && String(value).trim()) return value;
          }
        }
        return "";
      }

      function compactText(value, limit = 500) {
        const text = String(value || "").replace(/\s+/g, " ").trim();
        if (!text) return "";
        return text.length > limit ? `${text.slice(0, Math.max(0, limit - 1)).trim()}…` : text;
      }

      function dataText(data, key) {
        const value = data?.[key];
        if (value === undefined || value === null) return "";
        if (typeof value === "string") return compactText(value);
        if (typeof value === "number" || typeof value === "boolean") return String(value);
        if (Array.isArray(value)) {
          const flattened = value
            .filter((item) => ["string", "number", "boolean"].includes(typeof item))
            .map((item) => String(item))
            .join(" ");
          return compactText(flattened);
        }
        return "";
      }

      function sessionLogLocation(events) {
        return sessionDataValue(events, ["log_file", "log_path", "output_dir", "outputDir", "trace_path", "report_path"]);
      }

      function hasStreamPayload(event) {
        const tags = new Set((event.tags || []).map(slug));
        const data = event.data || {};
        if (!tags.has("stream")) return false;
        if (dataText(data, "latest_text") || dataText(data, "thinking_preview")) return true;
        const contentChars = Number(data.content_chars || 0);
        const thinkingChars = Number(data.thinking_chars || 0);
        if (contentChars > 0 || thinkingChars > 0) return true;
        return Boolean(compactText(event.message || ""));
      }

      function ragTypesFromEvent(event) {
        const data = event.data || {};
        const values = [];
        const push = (value) => {
          const text = slug(value).replace(/-/g, "_");
          if (text && text !== "activity") values.push(text);
        };
        if (Array.isArray(data.rag_types_seen)) data.rag_types_seen.forEach(push);
        ["rag_type", "step", "stage", "phase"].forEach((key) => push(data[key]));
        const toolPlan = data.tool_plan && typeof data.tool_plan === "object" ? data.tool_plan : {};
        if (Array.isArray(toolPlan.allowed_tools)) toolPlan.allowed_tools.forEach(push);
        (event.tags || []).forEach((tag) => {
          const clean = slug(tag).replace(/-/g, "_");
          if (["retrieval", "context", "context_inventory", "context_brief", "grounded_plan", "model_call", "docker", "executor", "quality", "web_search", "vision"].includes(clean)) {
            push(clean === "docker" || clean === "executor" ? "docker_executor" : clean);
          }
        });
        return values;
      }

      function collectRagTypes(events) {
        const result = [];
        events.slice().reverse().forEach((event) => {
          ragTypesFromEvent(event).forEach((type) => {
            if (type && !result.includes(type)) result.push(type);
          });
        });
        return result.slice(0, 18);
      }

      function historyTextForEvent(event) {
        const data = event.data || {};
        const labelledKeys = [
          ["system_prompt_preview", "system prompt"],
          ["system_prompt", "system prompt"],
          ["input_messages_preview", "model input"],
          ["user_prompt_preview", "user prompt"],
          ["prompt_preview", "prompt"],
          ["latest_text", "model stream"],
          ["thinking_preview", "model thinking"],
          ["command_preview", "docker command"],
          ["script_preview", "script"],
          ["stdout_preview", "stdout"],
          ["stderr_preview", "stderr"],
          ["history_label", ""],
          ["running_text", ""],
          ["ran_text", ""],
          ["command", "command"]
        ];
        for (const [key, label] of labelledKeys) {
          const value = dataText(data, key);
          if (value) return label ? `${label}: ${value}` : value;
        }
        if (data.summary && typeof data.summary === "object") {
          const summaryText = dataText(data.summary, "summary") || dataText(data.summary, "task_type") || dataText(data.summary, "type");
          if (summaryText) return summaryText;
        }
        if (Array.isArray(data.retrieved_paths) && data.retrieved_paths.length) {
          return `retrieved ${data.retrieved_paths.slice(0, 4).join(", ")}`;
        }
        return compactText(`${event.title || event.kind || "activity"}${event.message ? `: ${event.message}` : ""}`);
      }
      function sessionHistory(events) {
        const seen = new Set();

        return events
          .slice()
          .reverse()
          .map((event) => {
            const text = historyTextForEvent(event);
            if (!text) return null;

            const type = ragTypesFromEvent(event)[0] || slug(event.kind || "activity").replace(/-/g, "_");
            const status = event.status || event.severity || "";

            const normalizedText = String(text).replace(/\s+/g, " ").trim().toLowerCase();
            const duplicateKey = `${type}|${status}|${normalizedText}`;

            if (seen.has(duplicateKey)) return null;
            seen.add(duplicateKey);

            return {
              id: event.id,
              status,
              type,
              text,
              ts: event.ts
            };
          })
          .filter(Boolean)
          .slice(-180);
      }

      function normalizeHistoryText(value) {
        return String(value || "").replace(/\s+/g, " ").trim();
      }

      function historyItemFingerprint(item) {
        return [
          String(item?.type || ""),
          String(item?.status || ""),
          normalizeHistoryText(item?.text)
        ].join("|");
      }

      function historyFingerprint(history = []) {
        return (history || []).map((item) => historyItemFingerprint(item)).join("\n");
      }

      function cloneArchivedHistory(history = []) {
        return (history || []).slice(-180).map((item) => ({
          id: String(item?.id || ""),
          status: String(item?.status || ""),
          type: String(item?.type || ""),
          text: String(item?.text || ""),
          ts: String(item?.ts || "")
        }));
      }

      function freezeArchiveSnapshot(snapshot) {
        if (!snapshot || typeof Object.freeze !== "function") return snapshot;
        Object.freeze(snapshot.latest);
        snapshot.history.forEach((item) => Object.freeze(item));
        Object.freeze(snapshot.history);
        Object.freeze(snapshot.ragTypes);
        return Object.freeze(snapshot);
      }

      function findArchiveSnapshot(runId, reason) {
        if (!runId || !reason) return null;
        return activityState.aiSessionArchive.find((item) => item.runId === runId && item.archiveReason === reason) || null;
      }

      function isVisibleArchiveSnapshot(snapshot) {
        // top-card-snapshot entries are mutable live helpers for the main card,
        // not preserved subprocess/history panes. Rendering them below the main
        // card makes every pane look like it is filled by the newest stream data.
        return Boolean(snapshot && snapshot.runId && snapshot.archiveReason !== "top-card-snapshot");
      }

      function archiveSnapshotKey(snapshot) {
        return [
          String(snapshot?.runId || ""),
          String(snapshot?.historyFingerprint || historyFingerprint(snapshot?.history || []))
        ].join("|");
      }

      function trimAiSessionArchive() {
        const hiddenTopCards = [];
        const visibleHistory = [];
        activityState.aiSessionArchive.forEach((item) => {
          if (!item || !item.runId) return;
          if (item.archiveReason === "top-card-snapshot") {
            if (!hiddenTopCards.length) hiddenTopCards.push(item);
            return;
          }
          visibleHistory.push(item);
        });
        activityState.aiSessionArchive = [
          ...hiddenTopCards,
          ...visibleHistory.slice(0, AI_SESSION_ARCHIVE_LIMIT)
        ];
      }

      function visibleArchivedSessions(displayedRunId = "") {
        const seen = new Set();
        return activityState.aiSessionArchive
          .filter((item) => {
            if (!isVisibleArchiveSnapshot(item)) return false;
            if (displayedRunId && item.runId === displayedRunId && item.active) return false;
            const key = archiveSnapshotKey(item);
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
          })
          .slice(0, AI_SESSION_ARCHIVE_LIMIT);
      }

      function latestArchivedTopSession() {
        return activityState.aiSessionArchive.find((item) => item?.archiveReason === "top-card-snapshot")
          || activityState.aiSessionArchive.find((item) => isVisibleArchiveSnapshot(item))
          || null;
      }

      function summarizeAiSession(runId, sessionEvents) {
        if (!sessionEvents || !sessionEvents.length) return null;
        const latest = sessionEvents[0];
        const started = sessionEvents[sessionEvents.length - 1];
        const latestMs = Date.parse(latest.ts || "") || Date.now();
        const latestRunning = sessionEvents.find((event) => ["running", "started"].includes(String(event.status || "").toLowerCase()) || hasStreamPayload(event));
        const latestRunningMs = Date.parse(latestRunning?.ts || "") || latestMs;
        return {
          runId,
          latest,
          started,
          events: sessionEvents,
          eventCount: sessionEvents.length,
          status: sessionStatusFromEvents(sessionEvents),
          streaming: sessionEvents.some((event) => hasStreamPayload(event)),
          connected: Date.now() - latestRunningMs < 30000,
          ageMs: Date.now() - latestMs,
          provider: sessionDataValue(sessionEvents, ["provider"]),
          model: sessionDataValue(sessionEvents, ["model"]),
          latestText: sessionDataValue(sessionEvents, ["latest_text"]),
          thinkingPreview: sessionDataValue(sessionEvents, ["thinking_preview"]),
          contentChars: sessionDataValue(sessionEvents, ["content_chars"]),
          thinkingChars: sessionDataValue(sessionEvents, ["thinking_chars"]),
          logLocation: sessionLogLocation(sessionEvents),
          ragTypes: collectRagTypes(sessionEvents),
          history: sessionHistory(sessionEvents)
        };
      }

      function aiSessionSummaries() {
        const events = allEvents().filter((event) => isAiSessionEvent(event) && event.data?.run_id);
        const grouped = new Map();
        events.forEach((event) => {
          const runId = String(event.data.run_id || "");
          if (!runId) return;
          if (!grouped.has(runId)) grouped.set(runId, []);
          grouped.get(runId).push(event);
        });
        return [...grouped.entries()]
          .map(([runId, group]) => summarizeAiSession(runId, group))
          .filter(Boolean)
          .sort((a, b) => String(b.latest?.ts || "").localeCompare(String(a.latest?.ts || "")));
      }

      function latestAiSession(sessions = aiSessionSummaries()) {
        if (!sessions.length) return null;
        const active = sessions.find((session) => {
          const latestRunning = session.events.find((event) => ["running", "started"].includes(String(event.status || "").toLowerCase()) || hasStreamPayload(event));
          const latestRunningMs = Date.parse(latestRunning?.ts || "") || 0;
          return session.status === "running" && latestRunningMs && Date.now() - latestRunningMs < 30000;
        });
        return active || sessions[0] || null;
      }

      function archiveAiSession(session, reason = "observed", options = {}) {
        if (!session || !session.runId) return null;

        // Only the hidden top-card snapshot is allowed to follow live stream updates.
        // Every visible preserved/history card is a one-time immutable copy.
        const preserveSnapshot = Boolean(options.preserveSnapshot);
        const allowStreamUpdates = !preserveSnapshot && reason === "top-card-snapshot";
        const replaceSameRun = allowStreamUpdates && options.replaceSameRun !== false;
        if (!allowStreamUpdates && !preserveSnapshot) {
          const existing = findArchiveSnapshot(session.runId, reason);
          if (existing) return existing;
        }

        const history = cloneArchivedHistory(session.history);
        const fingerprint = historyFingerprint(history);
        if (preserveSnapshot) {
          const duplicate = activityState.aiSessionArchive.find((item) =>
            item.runId === session.runId &&
            item.archiveReason === reason &&
            item.historyFingerprint === fingerprint
          );
          if (duplicate) return duplicate;
        }
        const snapshot = freezeArchiveSnapshot({
          snapshotId: preserveSnapshot || !replaceSameRun
            ? `${session.runId}-${reason}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
            : `${session.runId}-${reason}`,
          runId: String(session.runId || ""),
          latest: {
            title: String(session.latest?.title || ""),
            kind: String(session.latest?.kind || ""),
            message: String(session.latest?.message || ""),
            status: String(session.latest?.status || ""),
            ts: String(session.latest?.ts || nowIso())
          },
          status: String(session.status || ""),
          streaming: Boolean(session.streaming),
          connected: Boolean(session.connected),
          active: allowStreamUpdates,
          ageMs: Number(session.ageMs || 0),
          provider: String(session.provider || ""),
          model: String(session.model || ""),
          latestText: String(session.latestText || ""),
          thinkingPreview: String(session.thinkingPreview || ""),
          contentChars: session.contentChars || "",
          thinkingChars: session.thinkingChars || "",
          logLocation: String(session.logLocation || ""),
          ragTypes: [...(session.ragTypes || [])].map((type) => String(type || "")).filter(Boolean),
          history,
          historyFingerprint: fingerprint,
          eventCount: Number(session.eventCount || session.events?.length || 0),
          archivedAt: nowIso(),
          archiveReason: reason
        });
        if (replaceSameRun) {
          activityState.aiSessionArchive = activityState.aiSessionArchive.filter((item) =>
            !(item.runId === snapshot.runId && item.archiveReason === snapshot.archiveReason)
          );
        }
        activityState.aiSessionArchive.unshift(snapshot);
        trimAiSessionArchive();
        return snapshot;
      }

      // Only archive sessions after they leave the live top card. The top card is the
      // only UI allowed to follow model stream updates; preserved sub-history is locked.
      function archiveObservedAiSessions(sessions, currentRunId) {
        sessions.forEach((session) => {
          if (!session || !session.runId || session.runId === currentRunId) return;
          archiveAiSession(session, "previous-run");
        });
      }

      function renderAiSessionCard(session, options = {}) {
        const archived = Boolean(options.archived);
        const active = !archived && session.status === "running" && session.connected;
        const stale = !archived && session.status === "running" && !session.connected;
        const failed = session.status === "failed";
        const stateClass = active ? "connected alive" : failed ? "failed" : stale ? "settled stale" : "settled finished";
        const statusLabel = archived ? "history" : active ? (session.streaming ? "streaming" : "alive") : failed ? "failed" : stale ? "last seen" : "finished";
        const throbberClass = active ? "alive active" : failed ? "failed" : "finished";
        const card = document.createElement("article");
        card.className = `machine-activity-session-card ${archived ? "archived" : "live"} ${stateClass}`;
        card.dataset.runId = session.runId || "";
        const latestTitle = session.latest?.title || session.latest?.kind || "AI activity";
        const latestMessage = session.latest?.message || session.latest?.status || "";
        const eventCount = session.eventCount || session.events?.length || 0;
        card.innerHTML = `
          <div class="machine-activity-session-head">
            <span class="machine-activity-throbber ${throbberClass}" aria-hidden="true"></span>
            <strong>${archived ? "AI session history" : "Current message"}</strong>
            <small></small>
          </div>
          <button class="machine-activity-copy-log" type="button" title="Copy log location">.</button>
          <p class="machine-activity-session-run"></p>
          <p class="machine-activity-session-model"></p>
          <p class="machine-activity-session-stream"></p>
          <p class="machine-activity-session-latest"></p>
          <div class="machine-activity-session-rag-types"></div>
          <div class="machine-activity-session-meta"></div>
          <details class="machine-activity-session-history-panel" ${archived ? "" : "open"}>
            <summary>
              <span class="machine-activity-session-history-title">&gt; Message activity</span>
              <span class="machine-activity-session-history-hint">∨ Collapse</span>
            </summary>
            <div class="machine-activity-session-thought-history" role="log" aria-label="${archived ? "Archived AI thought history" : "Current message AI activity"}"></div>
          </details>
        `;
        card.querySelector(".machine-activity-session-head small").textContent = statusLabel;
        const copyButton = card.querySelector(".machine-activity-copy-log");
        copyButton.disabled = !session.logLocation;
        copyButton.title = session.logLocation ? `Copy log location: ${session.logLocation}` : "No log location yet";
        copyButton.addEventListener("click", async () => {
          if (!session.logLocation) return;
          try {
            await navigator.clipboard?.writeText?.(String(session.logLocation));
            copyButton.textContent = "✓";
            setTimeout(() => {
              copyButton.textContent = ".";
            }, 900);
          } catch {
            copyButton.textContent = "!";
            setTimeout(() => {
              copyButton.textContent = ".";
            }, 900);
          }
        });
        card.querySelector(".machine-activity-session-run").textContent = session.runId;
        card.querySelector(".machine-activity-session-model").textContent = `${session.provider || "model"}${session.model ? ` / ${session.model}` : ""}`;
        const streamText = session.latestText
          ? `...${session.latestText}`
          : session.thinkingPreview
            ? session.thinkingPreview
            : active
              ? "alive; waiting for model bytes"
              : "";
        card.querySelector(".machine-activity-session-stream").textContent = streamText;
        card.querySelector(".machine-activity-session-latest").textContent = `${latestTitle}${latestMessage ? ` | ${latestMessage}` : ""}`;
        card.querySelector(".machine-activity-session-rag-types").textContent = session.ragTypes?.length ? `RAG types: ${session.ragTypes.join(", ")}` : "";
        const counts = [
          session.contentChars ? `${session.contentChars} text chars` : "",
          session.thinkingChars ? `${session.thinkingChars} thinking chars` : ""
        ].filter(Boolean).join(" | ");
        const archivedText = archived && session.archivedAt ? ` | archived ${new Date(session.archivedAt).toLocaleTimeString()}` : "";
        card.querySelector(".machine-activity-session-meta").textContent = `${eventCount} events${counts ? ` | ${counts}` : ""} | last update ${Math.max(0, Math.round((session.ageMs || 0) / 1000))}s ago${archivedText}`;

        const history = card.querySelector(".machine-activity-session-thought-history");
        const historyItems = session.history || [];
        if (!historyItems.length) {
          const empty = document.createElement("div");
          empty.className = "machine-activity-session-thought-row muted";
          empty.textContent = "current message activity will appear here";
          history.appendChild(empty);
        } else {
          historyItems.forEach((item, index) => {
            const row = document.createElement("details");
            row.className = "machine-activity-session-thought-row machine-activity-session-thought-panel";
            if (!archived && index === historyItems.length - 1) row.open = true;
            const summary = document.createElement("summary");
            const meta = document.createElement("span");
            meta.className = "machine-activity-session-thought-meta";
            meta.textContent = `${item.status || "event"} · ${item.type || "rag"}`;
            const preview = document.createElement("span");
            preview.className = "machine-activity-session-thought-preview";
            preview.textContent = item.text;
            summary.append(meta, preview);
            const body = document.createElement("div");
            body.className = "machine-activity-session-thought-body";
            body.textContent = item.text;
            row.append(summary, body);
            history.appendChild(row);
          });
        }
        return card;
      }

      function renderAiSessionBox() {
        const target = dom.aiSession();
        if (!target) return;
        if (activityState.filter !== "ai") {
          target.hidden = true;
          target.replaceChildren();
          return;
        }
        const sessions = aiSessionSummaries();
        const session = latestAiSession(sessions);
        const currentRunId = session?.runId || "";
        // The Chat Console owns the per-node Thinking tab. The Activity Dock remains
        // a general monitor and history surface, not the per-message Thinking frame.
        if (activityState.aiCurrentRunId && activityState.aiCurrentRunId !== currentRunId) {
          const previous = sessions.find((item) => item.runId === activityState.aiCurrentRunId)
            || activityState.aiSessionArchive.find((item) => item.runId === activityState.aiCurrentRunId);
          if (previous) archiveAiSession(previous, "reset-before-new-run");
        }
        archiveObservedAiSessions(sessions, currentRunId);
        if (session) archiveAiSession(session, "top-card-snapshot");
        activityState.aiCurrentRunId = currentRunId;

        const archivedTopSession = !session ? latestArchivedTopSession() : null;
        const displayedRunId = session?.runId || archivedTopSession?.runId || "";
        const archivedSessions = visibleArchivedSessions(displayedRunId);

        target.hidden = false;
        target.className = `machine-activity-ai-session ${session || archivedTopSession ? "has-session" : "idle"}`;

        const previousHistory = target.querySelector(".machine-activity-session-thought-history");
        const previousHistoryScroll = previousHistory
          ? {
              scrollTop: previousHistory.scrollTop,
              scrollHeight: previousHistory.scrollHeight,
              clientHeight: previousHistory.clientHeight,
              nearBottom:
                previousHistory.scrollHeight -
                  previousHistory.scrollTop -
                  previousHistory.clientHeight <
                12
            }
          : null;

        target.replaceChildren();

        if (session) {
          target.appendChild(renderAiSessionCard(session));
        } else if (archivedTopSession) {
          target.appendChild(renderAiSessionCard(archivedTopSession, {archived: true}));
        } else {
          const idle = document.createElement("article");
          idle.className = "machine-activity-session-card idle";
          idle.innerHTML = `
            <div class="machine-activity-session-head">
              <span class="machine-activity-throbber idle" aria-hidden="true"></span>
              <strong>AI session</strong>
              <small>waiting</small>
            </div>
            <p>No AI request is active yet.</p>
          `;
          target.appendChild(idle);
        }

        const nextHistory = target.querySelector(".machine-activity-session-thought-history");
        if (nextHistory && previousHistoryScroll) {
          requestAnimationFrame(() => {
            if (previousHistoryScroll.nearBottom) {
              nextHistory.scrollTop = nextHistory.scrollHeight;
            } else {
              nextHistory.scrollTop = previousHistoryScroll.scrollTop;
            }

            console.debug("AI history restored after layout", {
              wanted: previousHistoryScroll.scrollTop,
              actual: nextHistory.scrollTop,
              scrollHeight: nextHistory.scrollHeight,
              clientHeight: nextHistory.clientHeight,
              nearBottom: previousHistoryScroll.nearBottom
            });
          });
        }

        if (archivedSessions.length) {
          const archive = document.createElement("div");
          archive.className = "machine-activity-session-archive";
          const title = document.createElement("div");
          title.className = "machine-activity-session-archive-title";
          title.textContent = "Preserved session history";
          archive.appendChild(title);
          archivedSessions.forEach((archived) => archive.appendChild(renderAiSessionCard(archived, {archived: true})));
          target.appendChild(archive);
        }
      }

      function renderEvent(event) {
        const article = document.createElement("article");
        article.className = "machine-activity-event";
        article.dataset.severity = event.severity || "info";
        article.dataset.timeModel = event.time_model || "";
        if (event.data && event.data.run_id) article.dataset.runId = event.data.run_id;
        article.innerHTML = `
          <div class="machine-activity-event-title">
            <span></span>
            <small></small>
          </div>
          <div class="machine-activity-event-meta"></div>
          <div class="machine-activity-event-message"></div>
          <div class="machine-activity-event-detail"></div>
          <div class="machine-activity-tags"></div>
        `;
        article.querySelector(".machine-activity-event-title span").textContent = event.title || event.kind || "Activity";
        article.querySelector(".machine-activity-event-title small").textContent = event.severity || "info";
        const runId = event.data?.run_id ? ` | ${event.data.run_id}` : "";
        article.querySelector(".machine-activity-event-meta").textContent = `${event.source} | ${event.kind} | ${event.time_model}${runId} | ${new Date(event.ts).toLocaleTimeString()}`;
        article.querySelector(".machine-activity-event-message").textContent = event.message || event.status || "";
        const detail = historyTextForEvent(event);
        article.querySelector(".machine-activity-event-detail").textContent = detail && detail !== (event.message || event.status || "") ? detail : "";
        const tags = article.querySelector(".machine-activity-tags");
        (event.tags || []).slice(0, 8).forEach((tag) => {
          const pill = document.createElement("span");
          pill.className = "machine-activity-tag";
          pill.textContent = tag;
          tags.appendChild(pill);
        });
        return article;
      }

      function generateMetaModel() {
        const appButtons = [...document.querySelectorAll("[data-app]")].map((button) => ({
          app: button.dataset.app,
          label: button.querySelector("strong")?.textContent?.trim() || button.textContent.trim(),
          activity_filter: button.dataset.activityOpen || ""
        }));
        const components = [...document.querySelectorAll("[data-mc-component-id]")].map((node) => ({
          id: node.dataset.mcComponentId,
          kind: node.dataset.mcComponentKind || "",
          label: node.dataset.mcComponentLabel || "",
          owner: node.dataset.mcComponentOwner || "",
          feature: node.dataset.mcFeatureId || ""
        }));
        const visibleFaults = allEvents().filter((event) => event.fault).slice(0, 12).map((event) => ({
          source: event.source,
          title: event.title,
          severity: event.severity,
          tags: event.tags
        }));
        const filters = Object.values(activityState.filters).map((filter) => ({
          id: filter.id,
          label: filter.label,
          match: filter.match || {}
        }));
        const backend = activityState.lastSnapshot || {};
        return {
          generated_at: nowIso(),
          workspace: "applications",
          surfaces: [
            {id: "applications.launcher", type: "left-panel", visible: true},
            {id: "applications.workspace", type: "stage", visible: true},
            {id: "machine.activity.dock", type: "right-dock", visible: activityState.open ? "open" : "collapsed", default_filter: "live"}
          ],
          time_models: ["static_fixture", "snapshot", "parallel", "time_series"],
          apps: appButtons,
          component_count: components.length,
          components: components.slice(0, 80),
          filters,
          visible_faults: visibleFaults,
          backend_summary: {
            ok: Boolean(backend.ok),
            event_count: backend.event_count || 0,
            latest_signal: backend.latest_signal || null
          }
        };
      }

      function renderMetaModel() {
        const meta = generateMetaModel();
        const target = dom.meta();
        if (target) target.textContent = JSON.stringify(meta, null, 2);
        return meta;
      }

      function renderHeartbeat() {
        const heartbeat = dom.heartbeat();
        if (!heartbeat) return;
        const snapshot = activityState.lastSnapshot || {};
        const status = snapshot.ok ? "backend" : activityState.lastBackendStatus;
        const latest = snapshot.latest_signal?.title || snapshot.latest_signal?.event || "local pulse";
        heartbeat.textContent = `♥ ${activityState.tick} | ${status} | ${latest}`;
      }

      function renderActivityDock() {
        const list = dom.events();
        if (!list) return;
        const filtered = allEvents().filter((event) => eventMatchesFilter(event)).slice(0, 80);
        list.replaceChildren();
        if (!filtered.length) {
          const empty = document.createElement("article");
          empty.className = "machine-activity-event";
          empty.dataset.severity = "info";
          empty.textContent = "No activity records match this filter yet.";
          list.appendChild(empty);
        } else {
          filtered.forEach((event) => list.appendChild(renderEvent(event)));
        }
        const filterState = dom.filterState();
        if (filterState) {
          const filter = activityState.filters[activityState.filter] || activityState.filters.live;
          filterState.textContent = `${filter.label || activityState.filter} | ${filtered.length}/${allEvents().length} visible`;
        }
        const summary = dom.summary();
        if (summary) summary.textContent = `Viewing ${activityState.filter}; ${allEvents().length} bounded activity records in memory.`;
        document.querySelectorAll("[data-activity-filter]").forEach((button) => {
          button.classList.toggle("active", button.dataset.activityFilter === activityState.filter);
        });
        renderHeartbeat();
        renderAiSessionBox();
        renderMetaModel();
      }

      function setFilter(filterId = "live") {
        const normalized = slug(filterId);
        activityState.filter = activityState.filters[normalized] ? normalized : "live";
        addEvent({
          source: "activity-dock",
          kind: "filter",
          time_model: "snapshot",
          severity: "info",
          title: "Activity filter selected",
          message: activityState.filter,
          tags: ["activity", "filter", activityState.filter]
        });
        renderActivityDock();
      }

      function openDock(filterId = "") {
        activityState.open = true;
        document.body.classList.remove("activity-dock-collapsed");
        document.body.classList.add("activity-dock-open");
        const toggle = dom.toggle();
        if (toggle) {
          toggle.textContent = "Activity Open";
          toggle.setAttribute("aria-expanded", "true");
        }
        if (filterId) setFilter(filterId);
        addEvent({
          source: "activity-dock",
          kind: "ui",
          time_model: "snapshot",
          severity: "info",
          title: "Activity dock opened",
          message: activityState.filter,
          tags: ["activity", "ui"]
        });
        renderActivityDock();
      }

      function closeDock() {
        activityState.open = false;
        document.body.classList.remove("activity-dock-open");
        document.body.classList.add("activity-dock-collapsed");
        const toggle = dom.toggle();
        if (toggle) {
          toggle.textContent = "Open Activity";
          toggle.setAttribute("aria-expanded", "false");
        }
        addEvent({
          source: "activity-dock",
          kind: "ui",
          time_model: "snapshot",
          severity: "info",
          title: "Activity dock collapsed",
          message: "heartbeat remains visible",
          tags: ["activity", "ui"]
        });
        renderActivityDock();
      }

      function registerFilter(filter, options = {}) {
        const id = slug(filter?.id || filter?.label || "");
        if (!id) return null;
        const nextFilter = {
          id,
          label: String(filter.label || id),
          match: filter.match && typeof filter.match === "object" ? filter.match : {}
        };
        const previous = activityState.filters[id];
        activityState.filters[id] = nextFilter;
        if (!options.silent && JSON.stringify(previous || null) !== JSON.stringify(nextFilter)) {
          addEvent({
            source: "activity-dock",
            kind: "filter",
            time_model: "snapshot",
            severity: "info",
            title: "AI filter registered",
            message: activityState.filters[id].label,
            tags: ["activity", "filter", "ai"]
          });
        }
        return activityState.filters[id];
      }

      async function refreshBackendActivity() {
        try {
          const response = await fetch("/api/activity/snapshot", {cache: "no-store"});
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const snapshot = await response.json();
          activityState.lastSnapshot = snapshot;
          activityState.lastBackendStatus = "backend";
          activityState.backendEvents = mergeActivityEvents(
            activityState.backendEvents,
            snapshot.events || [],
            ACTIVITY_MAX_EVENTS
          );
          if (Array.isArray(snapshot.filters)) {
            snapshot.filters.forEach((filter) => registerFilter(filter));
          }
        } catch (error) {
          activityState.lastBackendStatus = "offline";
          addEvent({
            source: "activity-api",
            kind: "fault",
            time_model: "snapshot",
            severity: "warn",
            title: "Activity snapshot unavailable",
            message: error.message || String(error),
            tags: ["activity", "api", "fault"]
          });
        } finally {
          renderActivityDock();
        }
      }

      function recordFixtureInventory() {
        addEvent({
          id: "fixture-applications-shell",
          source: "applications-shell",
          kind: "fixture",
          time_model: "static_fixture",
          severity: "info",
          title: "Applications shell loaded",
          message: "Launcher, workspace, and Machine Activity Dock are present.",
          tags: ["fixture", "ui", "activity"]
        });
        [...document.querySelectorAll("[data-app]")].forEach((button) => {
          const app = button.dataset.app || "app";
          addEvent({
            id: `fixture-app-${slug(app)}`,
            source: `application.${app}`,
            kind: "fixture",
            time_model: "static_fixture",
            severity: "info",
            title: `${button.querySelector("strong")?.textContent?.trim() || app} fixture`,
            message: button.querySelector("span")?.textContent?.trim() || "Application launcher card.",
            tags: ["fixture", "application", app]
          });
        });
        addEvent({
          id: "activity-meta-model-ready",
          source: "activity-dock",
          kind: "meta",
          time_model: "snapshot",
          severity: "info",
          title: "Meta model generator ready",
          message: "The dock can expose fixtures, filters, faults, and live activity as JSON.",
          tags: ["activity", "meta", "ai"]
        });
      }

      function bindActivityDock() {
        document.body.classList.add("activity-dock-collapsed");

        document.addEventListener("click", (event) => {
          const opener = event.target.closest("[data-activity-open]");
          if (opener) {
            const filter = opener.dataset.activityOpen || "live";
            openDock(filter);
            return;
          }
          const filterButton = event.target.closest("[data-activity-filter]");
          if (filterButton) {
            setFilter(filterButton.dataset.activityFilter || "live");
          }
        });

        dom.close()?.addEventListener("click", (event) => {
          event.preventDefault();
          closeDock();
        });

        window.addEventListener("error", (event) => {
          addEvent({
            source: "frontend",
            kind: "fault",
            time_model: "snapshot",
            severity: "error",
            title: "Frontend error",
            message: event.message || "Unknown frontend error.",
            tags: ["frontend", "fault"]
          });
        });

        window.addEventListener("unhandledrejection", (event) => {
          addEvent({
            source: "frontend",
            kind: "fault",
            time_model: "snapshot",
            severity: "error",
            title: "Unhandled promise rejection",
            message: event.reason?.message || String(event.reason || "Unknown rejection."),
            tags: ["frontend", "fault", "promise"]
          });
        });

        setInterval(() => {
          activityState.tick += 1;
          renderHeartbeat();
        }, 1000);

        setInterval(() => {
          const session = latestAiSession();
          if (activityState.filter === "ai" || (session && session.status === "running")) {
            refreshBackendActivity();
          }
        }, 5000);
        setInterval(refreshBackendActivity, 20000);
        recordFixtureInventory();
        refreshBackendActivity();
        renderActivityDock();
        activityState.initialized = true;
      }

      window.MainComputerActivityDock = {
        open: openDock,
        close: closeDock,
        setFilter,
        registerFilter,
        recordLocalEvent: addEvent,
        refresh: refreshBackendActivity,
        metaModel: generateMetaModel,
        state: activityState
      };

      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bindActivityDock, {once: true});
      } else {
        bindActivityDock();
      }
    })();
