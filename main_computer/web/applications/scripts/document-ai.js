    function documentAiNow() {
      return new Date().toISOString();
    }
    function documentAiId(prefix = "doc-ai") {
      return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    }
    function documentAiHash(value) {
      let hash = 0;
      const text = String(value || "");
      for (let index = 0; index < text.length; index += 1) {
        hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0;
      }
      return Math.abs(hash).toString(36);
    }
    function documentPlainTextFromHtml(html) {
      const div = document.createElement("div");
      div.innerHTML = html || "";
      return div.textContent || "";
    }
    function documentAiStorageKey(base) {
      return `${base}:${documentSession.selectedPath || "scratchpad"}`;
    }
    function loadDocumentAiState() {
      try {
        documentAiState.threads = JSON.parse(localStorage.getItem(documentAiStorageKey(documentAiStorageKeys.threads)) || "[]");
      } catch {
        documentAiState.threads = [];
      }
      try {
        documentAiState.undoStack = JSON.parse(localStorage.getItem(documentAiStorageKey(documentAiStorageKeys.undoStack)) || "[]");
      } catch {
        documentAiState.undoStack = [];
      }
      documentAiState.activeThreadId = localStorage.getItem(documentAiStorageKey(documentAiStorageKeys.activeThread)) || documentAiState.threads[0]?.id || "";
      documentAiState.lockedAnchor = documentAiState.threads.find((thread) => thread.id === documentAiState.activeThreadId)?.anchor || null;
      documentAiState.pendingSuggestion = null;
      renderDocumentAiPane();
    }
    function saveDocumentAiState() {
      localStorage.setItem(documentAiStorageKey(documentAiStorageKeys.threads), JSON.stringify(documentAiState.threads));
      localStorage.setItem(documentAiStorageKey(documentAiStorageKeys.undoStack), JSON.stringify(documentAiState.undoStack));
      localStorage.setItem(documentAiStorageKey(documentAiStorageKeys.activeThread), documentAiState.activeThreadId || "");
    }
    function getDocumentTextOffsetForPoint(container, offset) {
      if (!documentCanvas || !container) return 0;
      const range = document.createRange();
      range.setStart(documentCanvas, 0);
      try {
        range.setEnd(container, offset);
      } catch {
        return 0;
      }
      return range.toString().length;
    }
    function getDocumentNodeAtTextOffset(offset) {
      if (!documentCanvas) return null;
      const walker = document.createTreeWalker(documentCanvas, NodeFilter.SHOW_TEXT);
      let remaining = Math.max(0, Number(offset) || 0);
      let last = null;
      while (walker.nextNode()) {
        const node = walker.currentNode;
        last = node;
        if (remaining <= node.nodeValue.length) return {node, offset: remaining};
        remaining -= node.nodeValue.length;
      }
      return last ? {node: last, offset: last.nodeValue.length} : null;
    }
    function restoreDocumentSelectionFromOffsets(start, end = start) {
      const startPoint = getDocumentNodeAtTextOffset(start);
      const endPoint = getDocumentNodeAtTextOffset(end);
      const selection = window.getSelection?.();
      if (!startPoint || !selection) return false;
      const range = document.createRange();
      range.setStart(startPoint.node, startPoint.offset);
      if (endPoint) range.setEnd(endPoint.node, endPoint.offset);
      else range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
      startPoint.node.parentElement?.closest(".mc-page-content")?.focus();
      updateDocumentFormatForCaret();
      return true;
    }
    function getDocumentBlockIndex(block) {
      const blocks = Array.from(documentCanvas?.querySelectorAll(".mc-page-content > *") || []);
      return Math.max(0, blocks.indexOf(block));
    }
    function getDocumentSelectionSnapshot() {
      const selection = window.getSelection?.();
      if (!selection || !selection.rangeCount || !documentCanvas?.contains(selection.anchorNode)) return null;
      const range = selection.getRangeAt(0);
      const start = getDocumentTextOffsetForPoint(range.startContainer, range.startOffset);
      const end = getDocumentTextOffsetForPoint(range.endContainer, range.endOffset);
      const block = documentBlockForRange(range, getActiveDocumentEditor()) || getActiveDocumentEditor()?.querySelector(":scope > *");
      const page = block?.closest(".mc-page");
      const pages = Array.from(documentCanvas.querySelectorAll(".mc-page"));
      const size = documentLayoutSize(documentSession.layoutState);
      return {
        id: documentAiId("anchor"),
        created_at: documentAiNow(),
        document_path: documentSession.selectedPath || "",
        document_revision_hash: documentSession.loadedRevision?.content_hash || "",
        draft_hash: documentAiHash(getDocumentEditorHtml()),
        page_index: Math.max(0, pages.indexOf(page)),
        page_count: pages.length || 1,
        range: {
          start_text_offset: start,
          end_text_offset: end,
          collapsed: selection.isCollapsed,
          selected_text: range.toString()
        },
        block: {
          tag: documentFormatValueForBlock(block),
          text: block?.textContent || "",
          index_in_document: getDocumentBlockIndex(block)
        },
        layout: {
          mode: documentSession.layoutState.view.mode,
          preset: documentSession.layoutState.layout.preset,
          page_width_px: size.widthPx,
          page_height_px: size.heightPx
        },
        scroll: {
          canvas_scroll_top: documentCanvas.scrollTop,
          canvas_scroll_left: documentCanvas.scrollLeft
        }
      };
    }
    function captureDocumentAiAnchor(mode = "selection") {
      if (mode === "document") {
        const size = documentLayoutSize(documentSession.layoutState);
        const text = documentPlainTextFromHtml(getDocumentEditorHtml());
        return {
          id: documentAiId("anchor"),
          created_at: documentAiNow(),
          document_path: documentSession.selectedPath || "",
          document_revision_hash: documentSession.loadedRevision?.content_hash || "",
          draft_hash: documentAiHash(getDocumentEditorHtml()),
          page_index: 0,
          page_count: documentCanvas?.querySelectorAll(".mc-page").length || 1,
          range: {start_text_offset: 0, end_text_offset: text.length, collapsed: false, selected_text: text},
          block: {tag: "P", text: text.slice(0, 240), index_in_document: 0},
          layout: {mode: documentSession.layoutState.view.mode, preset: documentSession.layoutState.layout.preset, page_width_px: size.widthPx, page_height_px: size.heightPx},
          scroll: {canvas_scroll_top: documentCanvas?.scrollTop || 0, canvas_scroll_left: documentCanvas?.scrollLeft || 0}
        };
      }
      return getDocumentSelectionSnapshot() || captureDocumentAiAnchor("document");
    }
    function summarizeDocumentAiAnchor(anchor = documentAiState.lockedAnchor) {
      if (!anchor) return "No anchor locked.";
      const label = anchor.range?.collapsed ? "Caret" : "Selection";
      const sample = anchor.range?.selected_text || anchor.block?.text || "document";
      return `${label} on page ${Number(anchor.page_index || 0) + 1}/${anchor.page_count || 1}: ${sample.slice(0, 120)}`;
    }
    function activeDocumentAiThread() {
      return documentAiState.threads.find((thread) => thread.id === documentAiState.activeThreadId) || null;
    }
    function ensureDocumentAiThread(anchor, title = "") {
      let thread = activeDocumentAiThread();
      if (thread && documentAiState.lockedAnchor) return thread;
      thread = {
        id: documentAiId("thread"),
        document_path: documentSession.selectedPath || "",
        title: title || (anchor.range?.selected_text || anchor.block?.text || "Document thread").slice(0, 48),
        anchor,
        messages: [],
        suggestions: []
      };
      documentAiState.threads.unshift(thread);
      documentAiState.activeThreadId = thread.id;
      documentAiState.lockedAnchor = anchor;
      saveDocumentAiState();
      return thread;
    }
    function renderDocumentAiPane() {
      if (!documentAiPane) return;
      documentAiPane.classList.toggle("document-ai-busy", documentAiState.busy);
      documentAiAnchorSummary.textContent = summarizeDocumentAiAnchor();
      documentAiAnchorSummary.classList.toggle("document-ai-locked", Boolean(documentAiState.lockedAnchor));
      documentAiStatus.textContent = documentAiState.busy ? "asking document AI..." : (documentAiState.pendingSuggestion ? "suggestion ready" : "AI ready");
      documentAiThreads.innerHTML = documentAiState.threads.map((thread) => `<button type="button" class="document-ai-thread${thread.id === documentAiState.activeThreadId ? " active" : ""}" data-document-ai-thread="${escapeHtml(thread.id)}">${escapeHtml(thread.title || "Document thread")}</button>`).join("") || '<div class="document-library-empty">No AI threads yet.</div>';
      const thread = activeDocumentAiThread();
      documentAiMessages.innerHTML = thread?.messages?.map((message) => `<div class="document-ai-message ${escapeHtml(message.role)}">${escapeHtml(message.content || "")}</div>`).join("") || '<div class="document-library-empty">Ask about the current selection, caret, page, or whole document.</div>';
      renderDocumentAiPreview();
      documentAiSend.disabled = documentAiState.busy;
      documentAiCancel.disabled = !documentAiState.busy;
      documentAiApply.disabled = !documentAiState.pendingSuggestion || documentAiState.busy || documentAiState.pendingSuggestion.operation === "comment_only";
      documentAiUndo.disabled = !documentAiState.undoStack.length || documentAiState.busy;
    }
    function renderDocumentAiPreview() {
      if (!documentAiPreview) return;
      const suggestion = documentAiState.pendingSuggestion;
      if (!suggestion) {
        documentAiPreview.textContent = "Suggestions will appear here before they are applied.";
        return;
      }
      documentAiPreview.innerHTML = `
        <strong>${escapeHtml(suggestion.operation || "suggestion")}</strong>
        <span class="document-ai-diff-before">${escapeHtml(suggestion.before_text || "")}</span>
        <span class="document-ai-diff-after">${escapeHtml(suggestion.replacement_text || suggestion.after_text || "")}</span>
        <p>${escapeHtml(suggestion.rationale || "")}</p>
      `;
    }
    function documentAiDocumentPayload() {
      const html = getDocumentEditorHtml();
      return {
        path: documentSession.selectedPath || "",
        title: documentSession.record?.title || documentCurrentPath?.textContent || "local draft",
        kind: documentSession.record?.kind || "draft",
        revision_hash: documentSession.loadedRevision?.content_hash || "",
        html,
        text: documentPlainTextFromHtml(html),
        layout: documentSession.layoutState
      };
    }
    function documentAiActionFromInstruction(instruction) {
      const text = String(instruction || "").toLowerCase();
      if (text.includes("summarize")) return "summarize";
      if (text.includes("outline")) return "outline";
      if (text.includes("continue")) return "continue";
      if (text.includes("rewrite") || text.includes("grammar") || text.includes("concise") || text.includes("expand") || text.includes("improve")) return "rewrite";
      return "custom";
    }
    async function sendDocumentAiRequest(instruction) {
      const cleanInstruction = String(instruction || documentAiPrompt.value || "").trim();
      if (!cleanInstruction) {
        documentAiStatus.textContent = "ask or choose a prompt first";
        documentAiPrompt.focus();
        return;
      }
      const anchor = documentAiState.lockedAnchor || captureDocumentAiAnchor(cleanInstruction.toLowerCase().includes("whole document") || cleanInstruction.toLowerCase().includes("summarize") || cleanInstruction.toLowerCase().includes("outline") ? "document" : "selection");
      const thread = ensureDocumentAiThread(anchor, cleanInstruction);
      thread.messages.push({role: "user", content: cleanInstruction, created_at: documentAiNow()});
      documentAiState.busy = true;
      documentAiState.abortController = new AbortController();
      documentAiState.pendingSuggestion = null;
      saveDocumentAiState();
      renderDocumentAiPane();
      try {
        const response = await fetch("/api/applications/docs/ai", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          signal: documentAiState.abortController.signal,
          body: JSON.stringify({
            action: documentAiActionFromInstruction(cleanInstruction),
            instruction: cleanInstruction,
            document: documentAiDocumentPayload(),
            anchor,
            thread: {id: thread.id, messages: thread.messages},
            preferred_operation: null
          })
        });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
        const suggestion = data.suggestion || {operation: "comment_only", replacement_text: "", rationale: ""};
        suggestion.id = documentAiId("suggestion");
        suggestion.anchor_id = anchor.id;
        suggestion.before_text = anchor.range?.selected_text || anchor.block?.text || "";
        suggestion.created_at = documentAiNow();
        thread.messages.push({role: "assistant", content: data.content || suggestion.rationale || "", created_at: documentAiNow()});
        thread.suggestions.push(suggestion);
        documentAiState.pendingSuggestion = suggestion;
        documentAiStatus.textContent = "suggestion ready";
      } catch (error) {
        if (error.name === "AbortError") {
          documentAiStatus.textContent = "AI request cancelled";
        } else {
          documentAiStatus.textContent = `Document AI failed: ${error.message || error}`;
        }
      } finally {
        documentAiState.busy = false;
        documentAiState.abortController = null;
        saveDocumentAiState();
        renderDocumentAiPane();
      }
    }
    function safeDocumentAiHtmlFromText(text) {
      return String(text || "").split(/\n{2,}/).map((paragraph) => `<p>${escapeHtml(paragraph).replace(/\n/g, "<br>")}</p>`).join("") || "<p><br></p>";
    }
    function sanitizeDocumentAiReplacementHtml(html, fallbackText) {
      if (!html) return safeDocumentAiHtmlFromText(fallbackText);
      const template = document.createElement("template");
      template.innerHTML = html;
      const allowed = new Set(["P", "BR", "STRONG", "EM", "UL", "OL", "LI", "BLOCKQUOTE", "H1", "H2", "H3"]);
      template.content.querySelectorAll("*").forEach((node) => {
        if (!allowed.has(node.tagName)) {
          node.replaceWith(document.createTextNode(node.textContent || ""));
          return;
        }
        Array.from(node.attributes).forEach((attribute) => node.removeAttribute(attribute.name));
      });
      return template.innerHTML || safeDocumentAiHtmlFromText(fallbackText);
    }
    function insertDocumentAiHtmlAtRange(range, html) {
      const template = document.createElement("template");
      template.innerHTML = html;
      range.deleteContents();
      range.insertNode(template.content);
    }
    function applyDocumentAiSuggestion() {
      const suggestion = documentAiState.pendingSuggestion;
      const anchor = documentAiState.lockedAnchor;
      if (!suggestion || !anchor || suggestion.operation === "comment_only") return;
      const beforeHtml = getDocumentEditorHtml();
      if (anchor.draft_hash !== documentAiHash(beforeHtml) && suggestion.operation !== "replace_document") {
        documentAiStatus.textContent = "Document changed since anchor was locked. Refresh anchor before applying.";
        return;
      }
      const replacementHtml = sanitizeDocumentAiReplacementHtml(suggestion.replacement_html, suggestion.replacement_text || suggestion.after_text || "");
      if (suggestion.operation === "replace_document") {
        setDocumentEditorHtml(replacementHtml);
      } else {
        const start = anchor.range?.start_text_offset || 0;
        const end = suggestion.operation === "insert_at_caret" ? start : (anchor.range?.end_text_offset ?? start);
        if (!restoreDocumentSelectionFromOffsets(start, end)) {
          documentAiStatus.textContent = "Could not restore AI anchor.";
          return;
        }
        const selection = window.getSelection();
        const range = selection.getRangeAt(0);
        if (suggestion.operation === "replace_block") {
          const block = getDocumentCaretBlock();
          if (block) block.outerHTML = replacementHtml;
          else insertDocumentAiHtmlAtRange(range, replacementHtml);
        } else if (suggestion.operation === "append_after_selection") {
          range.collapse(false);
          insertDocumentAiHtmlAtRange(range, replacementHtml);
        } else {
          insertDocumentAiHtmlAtRange(range, replacementHtml);
        }
      }
      const afterHtml = getDocumentEditorHtml();
      documentAiState.undoStack.push({
        id: documentAiId("undo"),
        created_at: documentAiNow(),
        label: "Document AI edit",
        document_path: documentSession.selectedPath || "",
        before_html: beforeHtml,
        after_html: afterHtml,
        before_anchor: anchor,
        after_anchor: getDocumentSelectionSnapshot(),
        suggestion_id: suggestion.id
      });
      documentAiState.pendingSuggestion = null;
      saveDocumentDraft();
      saveDocumentAiState();
      scheduleDocumentRepagination();
      documentStatus.textContent = "AI edit applied to local draft";
      documentAiStatus.textContent = "AI edit applied to local draft";
      renderDocumentAiPane();
    }
    function undoDocumentAiEdit() {
      const entry = documentAiState.undoStack.pop();
      if (!entry) return;
      setDocumentEditorHtml(entry.before_html || "");
      saveDocumentDraft();
      saveDocumentAiState();
      scheduleDocumentRepagination();
      setTimeout(() => {
        const anchor = entry.before_anchor?.range;
        if (anchor) restoreDocumentSelectionFromOffsets(anchor.start_text_offset, anchor.end_text_offset);
      }, 0);
      documentStatus.textContent = "AI edit undone";
      documentAiStatus.textContent = "AI edit undone";
      renderDocumentAiPane();
    }
    function rebaseDocumentAiAnchor() {
      const anchor = captureDocumentAiAnchor("selection");
      documentAiState.lockedAnchor = anchor;
      const thread = activeDocumentAiThread();
      if (thread) thread.anchor = anchor;
      saveDocumentAiState();
      renderDocumentAiPane();
    }
