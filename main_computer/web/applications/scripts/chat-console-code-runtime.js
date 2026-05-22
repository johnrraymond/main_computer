    const CHAT_CONSOLE_CODE_LANGUAGES = new Set(["javascript", "python", "basic"]);
    const CHAT_CONSOLE_WORKER_SOURCE_IDS = {
      javascript: "chat-console-js-worker-source",
      python: "chat-console-python-worker-source",
      basic: "chat-console-basic-worker-source",
    };

    function chatConsoleJsonClone(value) {
      try {
        return JSON.parse(JSON.stringify(value ?? null));
      } catch {
        return null;
      }
    }

    function chatConsoleSharedVariables() {
      if (!chatConsoleState || typeof chatConsoleState !== "object") return {};
      if (!chatConsoleState.variables || typeof chatConsoleState.variables !== "object" || Array.isArray(chatConsoleState.variables)) {
        chatConsoleState.variables = {};
      }
      return chatConsoleState.variables;
    }

    function chatConsoleVariableSnapshot() {
      const snapshot = chatConsoleJsonClone(chatConsoleSharedVariables());
      return snapshot && typeof snapshot === "object" && !Array.isArray(snapshot) ? snapshot : {};
    }

    function chatConsoleNormalizeVariables(value, limit = 12000) {
      const cloned = chatConsoleJsonClone(value);
      if (!cloned || typeof cloned !== "object" || Array.isArray(cloned)) return {};
      const normalized = {};
      Object.entries(cloned).forEach(([key, item]) => {
        const name = String(key || "").trim();
        if (!name || name.length > 80 || name === "__proto__" || name === "constructor" || name === "prototype") return;
        try {
          const encoded = JSON.stringify(item);
          if (encoded && encoded.length <= limit) normalized[name] = item;
        } catch {
          // Skip non-serializable variables.
        }
      });
      return normalized;
    }

    function chatConsoleCodeRuntimeTimeout(language) {
      if (language === "python") return 60000;
      if (language === "basic") return 10000;
      return 2000;
    }

    function chatConsoleCodeWorkerSource(language) {
      const id = CHAT_CONSOLE_WORKER_SOURCE_IDS[language];
      const node = id ? document.getElementById(id) : null;
      const source = node?.textContent || "";
      if (!source.trim()) throw new Error(`${language} chat worker source is missing.`);
      if (source.includes("@include applications/scripts/")) {
        throw new Error("Chat console worker source was not expanded by the viewport include system.");
      }
      if (!source.includes("self.onmessage")) {
        throw new Error("Chat console worker source is invalid: missing self.onmessage.");
      }
      return source;
    }

    function chatConsoleCodeWorkerErrorResponse(request, title, error, durationMs = 0) {
      const message = error && error.message ? error.message : String(error || title || "Worker failed.");
      return {
        id: request?.id || "",
        ok: false,
        value: null,
        variables: chatConsoleVariableSnapshot(),
        output_parts: [{kind: "error", title, content: message, metadata: {}}],
        error: message,
        duration_ms: durationMs,
      };
    }

    function runChatConsoleCodeWorker(language, request, timeoutMs = 2000) {
      return new Promise((resolve) => {
        let worker = null;
        let workerUrl = "";
        let timer = null;
        let settled = false;
        const cleanup = () => {
          if (timer) {
            clearTimeout(timer);
            timer = null;
          }
          if (worker) {
            worker.terminate();
            worker = null;
          }
          if (workerUrl) {
            URL.revokeObjectURL(workerUrl);
            workerUrl = "";
          }
        };
        const finish = (response) => {
          if (settled) return;
          settled = true;
          cleanup();
          resolve({
            ok: false,
            value: null,
            variables: request?.shared_variables || {},
            output_parts: [],
            error: null,
            duration_ms: 0,
            ...(response || {}),
          });
        };
        try {
          const blob = new Blob([chatConsoleCodeWorkerSource(language)], {type: "text/javascript"});
          workerUrl = URL.createObjectURL(blob);
          worker = new Worker(workerUrl);
        } catch (error) {
          finish(chatConsoleCodeWorkerErrorResponse(request, "Worker error", error));
          return;
        }
        timer = setTimeout(() => {
          finish({
            id: request?.id || "",
            ok: false,
            value: null,
            variables: request?.shared_variables || {},
            output_parts: [{kind: "error", title: "Execution timeout", content: `Code cell execution exceeded ${timeoutMs}ms and was terminated.`, metadata: {language}}],
            error: "execution timeout",
            duration_ms: timeoutMs,
          });
        }, timeoutMs);
        worker.onmessage = (event) => {
          finish(event.data || {});
        };
        worker.onerror = (event) => {
          finish(chatConsoleCodeWorkerErrorResponse(request, "Worker error", event.message || "Worker failed."));
        };
        worker.onmessageerror = (event) => {
          finish(chatConsoleCodeWorkerErrorResponse(request, "Worker message error", event?.message || "Worker response could not be cloned."));
        };
        try {
          worker.postMessage(request);
        } catch (error) {
          finish(chatConsoleCodeWorkerErrorResponse(request, "Worker postMessage error", error));
        }
      });
    }

    function chatConsoleCodeWorkerSourceSummary(language = "javascript") {
      const id = CHAT_CONSOLE_WORKER_SOURCE_IDS[language];
      const node = id ? document.getElementById(id) : null;
      const source = node?.textContent || "";
      return {
        language,
        hasSource: Boolean(source.trim()),
        length: source.length,
        hasSelfOnMessage: source.includes("self.onmessage"),
        hasUnexpandedInclude: source.includes("@include applications/scripts/"),
      };
    }

    function chatConsoleOutputPart(kind, title, content, extra = {}) {
      return {
        id: chatConsoleId("part"),
        kind,
        title,
        content,
        language: extra.language || "",
        metadata: extra.metadata || {},
        snippets: extra.snippets || [],
      };
    }

    function chatConsoleVariableOutputPart(variables) {
      const normalized = chatConsoleNormalizeVariables(variables);
      const names = Object.keys(normalized).sort();
      return chatConsoleOutputPart("json", "Shared variables", normalized, {
        metadata: {names, count: names.length},
      });
    }

    async function evaluateChatConsoleCodeCell(cell) {
      const language = cell.type;
      const request = {
        id: `chat-console-${language}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        language,
        source: cell.source || "",
        shared_variables: chatConsoleVariableSnapshot(),
        timeout_ms: chatConsoleCodeRuntimeTimeout(language),
      };
      const response = await runChatConsoleCodeWorker(language, request, request.timeout_ms);
      const normalizedVariables = chatConsoleNormalizeVariables(response.variables || request.shared_variables || {});
      if (chatConsoleState) chatConsoleState.variables = normalizedVariables;
      const outputParts = Array.isArray(response.output_parts) ? response.output_parts : [];
      outputParts.push(chatConsoleVariableOutputPart(normalizedVariables));
      const created = chatConsoleNow();
      return {
        id: chatConsoleId("out"),
        type: "output",
        source_cell_id: cell.id,
        variant_group_id: `variants-${cell.id}`,
        variant_index: Number(cell.variant_index || 0) || 0,
        parts: outputParts.map((part) => ({
          id: part.id || chatConsoleId("part"),
          kind: part.kind || "text",
          title: part.title || part.kind || "Output",
          content: part.content ?? "",
          language: part.language || "",
          metadata: part.metadata && typeof part.metadata === "object" ? part.metadata : {},
          snippets: Array.isArray(part.snippets) ? part.snippets : [],
        })),
        status: response.ok ? "ok" : "error",
        provider: "local-browser-worker",
        model: `${language}-runtime`,
        created_at: created,
        updated_at: created,
        provenance: {
          source_cell_id: cell.id,
          source_cell_type: language,
          source_cell_source_hash: chatConsoleSourceHash(cell.source || ""),
          runtime: "browser-worker",
          duration_ms: Number(response.duration_ms || 0),
          shared_variable_names: Object.keys(normalizedVariables).sort(),
          created_at: created,
        },
      };
    }

    async function testChatConsoleCodeWorker(language = "javascript") {
      const source = language === "python"
        ? "x = int(vars.get('x', 1)) + 1\nx"
        : language === "basic"
          ? "PRINT GETVAR(\"x\")"
          : "vars.x = Number(vars.x || 1) + 1; return vars.x;";
      return runChatConsoleCodeWorker(language, {
        id: `chat-console-test-${Date.now()}`,
        language,
        source,
        shared_variables: {x: 1},
        timeout_ms: chatConsoleCodeRuntimeTimeout(language),
      }, chatConsoleCodeRuntimeTimeout(language));
    }

    window.chatConsoleCodeWorkerSourceSummary = chatConsoleCodeWorkerSourceSummary;
    window.testChatConsoleCodeWorker = testChatConsoleCodeWorker;
