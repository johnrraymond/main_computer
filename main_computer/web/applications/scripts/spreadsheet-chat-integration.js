    let spreadsheetChatThreadController = null;

    function spreadsheetChatThreadsApi() {
      return window.MainComputerChatThreads || null;
    }

    function spreadsheetChatNow() {
      return new Date().toISOString();
    }

    function spreadsheetWorkbookMetadata() {
      if (!spreadsheetWorkbook) spreadsheetWorkbook = spreadsheetDefaultWorkbook();
      spreadsheetWorkbook.metadata = spreadsheetWorkbook.metadata && typeof spreadsheetWorkbook.metadata === "object" ? spreadsheetWorkbook.metadata : {};
      return spreadsheetWorkbook.metadata;
    }

    function spreadsheetWorkbookChatMetadata() {
      const metadata = spreadsheetWorkbookMetadata();
      metadata.chat = metadata.chat && typeof metadata.chat === "object" ? metadata.chat : {};
      return metadata.chat;
    }

    function spreadsheetChatThreadIdFromUrl() {
      try {
        return (new URLSearchParams(window.location.search).get("thread") || "").trim();
      } catch {
        return "";
      }
    }

    function spreadsheetSetChatThreadUrl(threadId) {
      if (!threadId || !window.history?.replaceState) return;
      try {
        const url = new URL(window.location.href);
        url.searchParams.set("thread", threadId);
        window.history.replaceState({app: "spreadsheet", thread: threadId}, "", `${url.pathname}${url.search}${url.hash}`);
      } catch {
        // Leave URL unchanged when browser history is not available.
      }
    }

    function spreadsheetGetWorkbookChatThreadId() {
      return String(spreadsheetWorkbookChatMetadata().active_thread_id || "");
    }

    function spreadsheetSetWorkbookChatThreadId(threadId, context = {}) {
      const id = String(threadId || "");
      if (!id) return "";
      const chat = spreadsheetWorkbookChatMetadata();
      const previous = String(chat.active_thread_id || "");
      chat.active_thread_id = id;
      chat.linked_by = "spreadsheet";
      chat.linked_at ||= spreadsheetChatNow();
      chat.last_selected_at = spreadsheetChatNow();
      chat.workbook_path = spreadsheetPath || chat.workbook_path || "";
      if (context.reason) chat.last_reason = String(context.reason);
      spreadsheetSetChatThreadUrl(id);
      if (previous !== id || context.reason === "create" || context.reason === "clone") {
        spreadsheetSetDirty(true, "linked chat thread changed");
      }
      return id;
    }

    function spreadsheetChatConsoleThreadUrl(threadId) {
      const url = new URL(window.location.href);
      url.pathname = "/applications/chat-console";
      url.search = "";
      if (threadId) url.searchParams.set("thread", threadId);
      return url.toString();
    }

    function spreadsheetChatStatus(message) {
      const status = spreadsheetChatThreadStatus || document.querySelector("#spreadsheet-chat-thread-status");
      if (status) status.textContent = message || "";
      if (message && spreadsheetStatus) spreadsheetStatus.textContent = message;
    }

    function spreadsheetChatThreadTitle(thread) {
      return window.MainComputerChatThreadController?.threadTitle?.(thread) || String(thread?.title || "New Chat");
    }

    function spreadsheetEnsureLinkedChatThread() {
      const store = spreadsheetChatThreadsApi();
      if (!store?.load) return null;
      store.load();
      let threadId = spreadsheetChatThreadIdFromUrl() || spreadsheetGetWorkbookChatThreadId();
      let thread = threadId ? store.get?.(threadId) : null;
      if (!thread) {
        thread = store.getActive?.() || store.list?.()[0] || store.create?.({
          title: "Spreadsheet Chat",
          metadata: {origin_app: "spreadsheet", linked_workbooks: [spreadsheetPath || "spreadsheet"]},
          makeActive: false
        });
        threadId = thread?.id || "";
      }
      if (threadId) spreadsheetSetWorkbookChatThreadId(threadId, {reason: "initialize"});
      return thread || null;
    }

    function spreadsheetBuildThreadLink(thread) {
      const url = new URL(window.location.href);
      url.pathname = "/applications/spreadsheet";
      url.searchParams.set("thread", thread?.id || spreadsheetGetWorkbookChatThreadId());
      return url.toString();
    }

    function spreadsheetMountChatThreadController() {
      const panel = spreadsheetChatThreadPanel || document.querySelector("#spreadsheet-chat-thread-panel");
      if (!panel) return null;
      const thread = spreadsheetEnsureLinkedChatThread();
      if (!window.chatConsoleMountSpreadsheetEmbedded) {
        spreadsheetChatStatus("embedded Chat Console is not ready");
        return null;
      }
      spreadsheetChatThreadController = window.chatConsoleMountSpreadsheetEmbedded(panel, {
        threadId: thread?.id || spreadsheetGetWorkbookChatThreadId(),
        getLinkedThreadId: spreadsheetGetWorkbookChatThreadId,
        setLinkedThreadId(threadId, threadValue, context = {}) {
          spreadsheetSetWorkbookChatThreadId(threadId, context);
          const store = spreadsheetChatThreadsApi();
          if (store?.saveThread && threadValue?.id) {
            const metadata = {
              ...(threadValue.metadata || {}),
              linked_workbooks: Array.from(new Set([...(threadValue.metadata?.linked_workbooks || []), spreadsheetPath || "spreadsheet"]))
            };
            store.saveThread(threadValue.id, {metadata}, {makeActive: false});
          }
        },
        buildThreadLink: spreadsheetBuildThreadLink,
        status: spreadsheetChatStatus,
      });
      return spreadsheetChatThreadController;
    }

    function spreadsheetChatLanguageForSnippet(snippet = {}) {
      const language = String(snippet.language || snippet.kind || "").trim().toLowerCase();
      if (language === "js") return "javascript";
      if (language === "py") return "python";
      if (language === "bas") return "basic";
      if (SPREADSHEET_CODE_LANGUAGES.has(language)) return language;
      return "";
    }

    function spreadsheetChatEnsureImportHistory(cell) {
      cell.metadata = cell.metadata && typeof cell.metadata === "object" ? cell.metadata : {};
      if (!Array.isArray(cell.metadata.chat_import_history)) {
        cell.metadata.chat_import_history = cell.metadata.chat_import_history ? [cell.metadata.chat_import_history] : [];
      }
      return cell.metadata.chat_import_history;
    }

    function spreadsheetChatSourceCellForOutput(outputCell, thread) {
      const sourceId = String(outputCell?.source_cell_id || outputCell?.thread_parent_cell_id || "");
      if (!sourceId) return null;
      const cells = Array.isArray(thread?.cells) ? thread.cells : Array.isArray(chatConsoleState?.cells) ? chatConsoleState.cells : [];
      return cells.find((item) => String(item?.id || "") === sourceId) || null;
    }

    function spreadsheetImportChatCodeSnippetFromChatConsole(outputCell, part, snippet, thread) {
      const language = spreadsheetChatLanguageForSnippet(snippet);
      if (!language) {
        spreadsheetChatStatus("only JavaScript, Python, and BASIC snippets can be imported into spreadsheet cells");
        return null;
      }
      const ref = spreadsheetSelectedRef();
      if (!ref) {
        spreadsheetChatStatus("select a target spreadsheet cell before importing chat code");
        return null;
      }
      const cell = spreadsheetGetCell(ref, true);
      const history = spreadsheetChatEnsureImportHistory(cell);
      const source = String(snippet?.content || "");
      const sourceCell = spreadsheetChatSourceCellForOutput(outputCell, thread);
      const aiRangeAction = sourceCell?.metadata?.spreadsheet_ai_range_action || null;
      const origin = {
        origin_app: aiRangeAction ? "spreadsheet-ai-range-action" : "chat-console",
        origin_thread_id: thread?.id || spreadsheetGetWorkbookChatThreadId(),
        origin_thread_title: spreadsheetChatThreadTitle(thread),
        origin_source_cell_id: outputCell?.source_cell_id || outputCell?.thread_parent_cell_id || "",
        origin_output_cell_id: outputCell?.id || "",
        origin_output_variant_index: outputCell?.variant_index || 0,
        origin_part_id: part?.id || "",
        origin_snippet_id: snippet?.id || "",
        origin_snippet_title: snippet?.title || "",
        original_language: language,
        original_code: source,
        original_target: ref,
        imported_at: spreadsheetChatNow(),
        active_sheet: aiRangeAction?.active_sheet || spreadsheetWorkbook?.active_sheet || "Sheet1",
        workbook_path: aiRangeAction?.workbook_path || spreadsheetPath,
        selected_range: aiRangeAction?.selected_range || (spreadsheetSelectedRange ? {start: spreadsheetSelectedRange.start, end: spreadsheetSelectedRange.end} : null),
        ai_range_action: aiRangeAction || null,
      };
      history.push(origin);
      cell.kind = language;
      cell.language = language;
      cell.source = source;
      cell.status = "dirty";
      cell.output = cell.output && typeof cell.output === "object" ? cell.output : {parts: []};
      cell.metadata.last_chat_import = origin;
      spreadsheetSetDirty(true, `imported ${language} chat code into ${ref}`);
      spreadsheetRenderInspector(ref);
      spreadsheetRefreshCellElement(ref);
      return cell;
    }

    function spreadsheetRenderChatImportHistory(ref, cell) {
      const target = spreadsheetImportHistory || document.querySelector("#spreadsheet-import-history");
      if (!target) return;
      target.textContent = "";
      if (!ref || !cell) return;
      const history = Array.isArray(cell?.metadata?.chat_import_history)
        ? cell.metadata.chat_import_history
        : cell?.metadata?.chat_import_history ? [cell.metadata.chat_import_history] : [];
      if (!history.length) {
        const empty = document.createElement("div");
        empty.className = "spreadsheet-import-history-empty";
        empty.textContent = "No chat import history for this cell.";
        target.append(empty);
        return;
      }
      const latest = history[history.length - 1];
      const sourceChanged = String(cell.source || "") !== String(latest.original_code || "");
      const head = document.createElement("div");
      head.className = "spreadsheet-import-history-head";
      const title = document.createElement("strong");
      title.textContent = sourceChanged ? "Import History · modified since import" : "Import History";
      const meta = document.createElement("span");
      meta.textContent = `${history.length} saved import${history.length === 1 ? "" : "s"}`;
      head.append(title, meta);
      target.append(head);

      const card = document.createElement("div");
      card.className = "spreadsheet-import-history-card";
      const importLines = [
        `Thread: ${latest.origin_thread_title || latest.origin_thread_id || "unknown"}`,
        `Output: ${latest.origin_output_cell_id || "unknown"}`,
        `Language: ${latest.original_language || "unknown"}`,
        `Imported: ${latest.imported_at || "unknown time"}`,
        `Original target: ${latest.original_target || ref}`,
      ];
      if (latest.ai_range_action?.selected_range?.label) {
        importLines.push(`AI range: ${latest.ai_range_action.selected_range.label}`);
      }
      if (latest.ai_range_action?.user_request) {
        const request = String(latest.ai_range_action.user_request).replace(/\s+/g, " ").trim();
        importLines.push(`AI request: ${request.slice(0, 160)}${request.length > 160 ? "..." : ""}`);
      }
      importLines.forEach((line) => {
        const item = document.createElement("div");
        item.textContent = line;
        card.append(item);
      });

      const actions = document.createElement("div");
      actions.className = "spreadsheet-import-history-actions";
      const restore = document.createElement("button");
      restore.type = "button";
      restore.textContent = "Restore Original";
      restore.addEventListener("click", () => {
        const language = spreadsheetChatLanguageForSnippet({language: latest.original_language});
        const targetRef = latest.original_target || ref;
        const targetCell = spreadsheetGetCell(targetRef, true);
        targetCell.kind = language || targetCell.kind || "value";
        targetCell.language = language || targetCell.language || "none";
        targetCell.source = String(latest.original_code || "");
        targetCell.status = language ? "dirty" : "clean";
        targetCell.metadata = targetCell.metadata && typeof targetCell.metadata === "object" ? targetCell.metadata : {};
        targetCell.metadata.chat_import_history = Array.isArray(targetCell.metadata.chat_import_history) ? targetCell.metadata.chat_import_history : history;
        targetCell.metadata.restored_chat_import_at = spreadsheetChatNow();
        spreadsheetSetDirty(true, `restored original chat import for ${targetRef}`);
        spreadsheetRenderInspector(targetRef);
        spreadsheetRefreshCellElement(targetRef);
      });
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "Copy Original";
      copy.addEventListener("click", () => {
        navigator.clipboard?.writeText?.(String(latest.original_code || "")).then(() => spreadsheetChatStatus("original chat code copied")).catch(() => spreadsheetChatStatus("copy original failed"));
      });
      const open = document.createElement("button");
      open.type = "button";
      open.textContent = "Open Origin Thread";
      open.addEventListener("click", () => {
        if (latest.origin_thread_id) window.open(spreadsheetChatConsoleThreadUrl(latest.origin_thread_id), "_blank", "noopener");
      });
      actions.append(restore, copy, open);
      card.append(actions);
      target.append(card);
    }

    function spreadsheetInitChatThreadIntegration() {
      spreadsheetMountChatThreadController();
    }
