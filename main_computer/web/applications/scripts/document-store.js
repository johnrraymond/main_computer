    const documentStorageKeys = {
      selected: "main-computer-document-selected-v1",
      draftPrefix: "main-computer-document-editor-draft-v1:",
      layoutPrefix: "main-computer-document-layout-v1:",
      revisionPrefix: "main-computer-document-loaded-revision-v1:",
      scratchpad: "main-computer-document-editor-v1",
      scratchpadLayout: "main-computer-document-layout-v1",
    };
    const documentAiStorageKeys = {
      threads: "main-computer-document-ai-threads-v1",
      activeThread: "main-computer-document-ai-active-thread-v1",
      undoStack: "main-computer-document-ai-undo-v1"
    };
    const documentPagePresets = {
      letter: {label: "Letter", widthPx: 816, heightPx: 1056},
      a4: {label: "A4", widthPx: 794, heightPx: 1123},
      legal: {label: "Legal", widthPx: 816, heightPx: 1344},
      screen: {label: "Screen", widthPx: 960, heightPx: 1280}
    };
    function defaultDocumentLayoutState() {
      return {
        layout: {
          mode: "preset",
          preset: "letter",
          custom: null,
          margins: {top: 96, right: 96, bottom: 96, left: 96}
        },
        view: {
          mode: "paged",
          zoom: 1,
          showPageBreaks: true
        }
      };
    }
    const docsApi = {
      async listDocuments() {
        const response = await fetch("/api/applications/docs/files", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
      },
      async readDocument(path) {
        const response = await fetch("/api/applications/docs/read", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
      },
      async readDraft(path) {
        const response = await fetch("/api/applications/docs/draft/read", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
      },
      async writeDraft(path, html, options = {}) {
        const response = await fetch("/api/applications/docs/draft/write", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            path,
            html,
            layout: options.layout || null,
            revision: options.revision || null
          })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
      },
      async deleteDraft(path) {
        const response = await fetch("/api/applications/docs/draft/delete", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
      }
    };
    const documentRenderer = {
      render(content, kind = "text") {
        const lines = String(content || "").replace(/\r\n/g, "\n").split("\n");
        if (kind === "markdown") {
          const blocks = [];
          let listItems = [];
          function flushList() {
            if (listItems.length) {
              blocks.push(`<ul>${listItems.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`);
              listItems = [];
            }
          }
          lines.forEach((line) => {
            const heading = line.match(/^(#{1,3})\s+(.+)$/);
            const bullet = line.match(/^\s*[-*]\s+(.+)$/);
            if (heading) {
              flushList();
              blocks.push(`<h${heading[1].length}>${escapeHtml(heading[2])}</h${heading[1].length}>`);
            } else if (bullet) {
              listItems.push(bullet[1]);
            } else if (line.trim()) {
              flushList();
              blocks.push(`<p>${escapeHtml(line)}</p>`);
            } else {
              flushList();
            }
          });
          flushList();
          return blocks.join("") || "<p></p>";
        }
        return `<pre>${escapeHtml(content)}</pre>`;
      }
    };
    const draftStore = {
      key(path) {
        const clean = String(path || "").trim();
        return clean ? `${documentStorageKeys.draftPrefix}${clean}` : documentStorageKeys.scratchpad;
      },
      getDraft(path) {
        return localStorage.getItem(this.key(path));
      },
      setDraft(path, html) {
        localStorage.setItem(this.key(path), html);
      },
      removeDraft(path) {
        localStorage.removeItem(this.key(path));
      },
      hasDraft(path) {
        return this.getDraft(path) !== null;
      }
    };
    const documentLayoutStore = {
      key(path) {
        const clean = String(path || "").trim();
        return clean ? `${documentStorageKeys.layoutPrefix}${clean}` : documentStorageKeys.scratchpadLayout;
      },
      get(path) {
        try {
          return JSON.parse(localStorage.getItem(this.key(path)) || "null");
        } catch {
          return null;
        }
      },
      set(path, state) {
        localStorage.setItem(this.key(path), JSON.stringify(state));
      }
    };
    const selectionStore = {
      getSelectedPath() {
        return localStorage.getItem(documentStorageKeys.selected) || "";
      },
      setSelectedPath(path) {
        localStorage.setItem(documentStorageKeys.selected, path);
      },
      clearSelectedPath() {
        localStorage.removeItem(documentStorageKeys.selected);
      }
    };
    const revisionStore = {
      key(path) {
        return `${documentStorageKeys.revisionPrefix}${String(path || "").trim()}`;
      },
      getRevision(path) {
        try {
          return JSON.parse(localStorage.getItem(this.key(path)) || "null");
        } catch (error) {
          return null;
        }
      },
      setRevision(path, revision) {
        if (path) localStorage.setItem(this.key(path), JSON.stringify(revision || {}));
      }
    };
    const documentSession = {
      selectedPath: "",
      record: null,
      loadedRevision: null,
      diskRenderedHtml: "",
      localDraftHtml: "",
      hasLocalDraft: false,
      source: "scratchpad",
      layoutState: defaultDocumentLayoutState(),
    };
    const documentBackendDraftSave = {
      timer: 0,
      path: "",
      html: "",
      layout: null,
      revision: null,
      inFlight: false,
      lastError: ""
    };
    let documentRepaginationFrame = 0;
    const documentAiState = {
      threads: [],
      activeThreadId: "",
      lockedAnchor: null,
      pendingSuggestion: null,
      abortController: null,
      busy: false,
      undoStack: [],
      versionCounter: 0
    };
    const documentDraftView = {
      update(message = "") {
        const token = documentSession.loadedRevision?.content_hash ? `revision ${documentSession.loadedRevision.content_hash.slice(0, 8)}` : "";
        documentVersionToken.textContent = token;
        documentDraftBanner.hidden = !(documentSession.selectedPath && documentSession.hasLocalDraft && ["backend-draft", "local-draft", "local-fallback"].includes(documentSession.source));
        if (message) {
          documentDraftState.textContent = message;
          documentStatus.textContent = message;
        } else if (!documentSession.selectedPath) {
          documentDraftState.textContent = documentSession.source === "backend-scratchpad" ? "backend scratchpad" : "scratchpad draft";
        } else if (documentSession.hasLocalDraft && ["backend-draft", "local-draft", "local-fallback"].includes(documentSession.source)) {
          documentDraftState.textContent = documentSession.source === "backend-draft" ? "backend draft saved" : "local fallback draft";
        } else {
          documentDraftState.textContent = "no backend draft";
        }
        documentReadonlyNote.textContent = "Backend draft storage is enabled. Edits auto-save to the server; local storage is only a fallback cache.";
      },
      showDiskChangedDraftPreserved() {
        documentDraftBanner.hidden = false;
        documentDraftState.textContent = "Disk version changed; backend draft preserved.";
        documentStatus.textContent = "Disk version changed; backend draft preserved.";
      }
    };
    const documentLibraryView = {
      render(documents = []) {
        documentLibraryList.innerHTML = "";
        if (!documents.length) {
          documentLibraryList.innerHTML = '<div class="document-library-empty">No pretty docs found.</div>';
          return;
        }
        documents.forEach((doc) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "document-library-item";
          button.dataset.docPath = doc.path || "";
          button.innerHTML = `<strong>${escapeHtml(doc.title || doc.path || "Untitled")}</strong><span>${escapeHtml(doc.display_path || doc.path || "")}</span>`;
          button.addEventListener("click", () => loadPrettyDoc(doc.path || "", {rememberSelection: true}));
          documentLibraryList.append(button);
        });
        this.highlight(documentSession.selectedPath);
      },
      highlight(path) {
        documentLibraryList.querySelectorAll(".document-library-item").forEach((button) => {
          button.classList.toggle("active", button.dataset.docPath === path);
          button.setAttribute("aria-current", button.dataset.docPath === path ? "true" : "false");
        });
      }
    };
