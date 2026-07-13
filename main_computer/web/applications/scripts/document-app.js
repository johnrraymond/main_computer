    function scheduleBackendDraftSave(path, html) {
      documentBackendDraftSave.path = String(path || "");
      documentBackendDraftSave.html = html;
      documentBackendDraftSave.layout = documentSession.layoutState;
      documentBackendDraftSave.revision = documentSession.loadedRevision;
      documentBackendDraftSave.lastError = "";
      if (documentBackendDraftSave.timer) clearTimeout(documentBackendDraftSave.timer);
      documentBackendDraftSave.timer = setTimeout(() => flushBackendDraftSave(), 650);
    }
    async function flushBackendDraftSave() {
      if (documentBackendDraftSave.inFlight) {
        documentBackendDraftSave.timer = 0;
        return;
      }
      const path = documentBackendDraftSave.path;
      const html = documentBackendDraftSave.html;
      const layout = documentBackendDraftSave.layout;
      const revision = documentBackendDraftSave.revision;
      documentBackendDraftSave.timer = 0;
      documentBackendDraftSave.inFlight = true;
      try {
        await docsApi.writeDraft(path, html, {layout, revision});
        if (documentBackendDraftSave.path === path && documentBackendDraftSave.html === html) {
          documentSession.source = path ? "backend-draft" : "backend-scratchpad";
          documentBackendDraftSave.lastError = "";
          documentDraftView.update("draft saved to backend");
        }
      } catch (error) {
        documentBackendDraftSave.lastError = String(error?.message || error || "backend save failed");
        documentDraftView.update("backend save failed; local fallback kept");
      } finally {
        documentBackendDraftSave.inFlight = false;
        if (documentBackendDraftSave.timer === 0 && (documentBackendDraftSave.path !== path || documentBackendDraftSave.html !== html)) {
          scheduleBackendDraftSave(documentBackendDraftSave.path, documentBackendDraftSave.html);
        }
      }
    }
    function saveDocumentDraft() {
      const html = getDocumentEditorHtml();
      const path = documentSession.selectedPath || "";
      draftStore.setDraft(path, html);
      saveDocumentLayoutForCurrentPath();
      documentSession.localDraftHtml = html;
      documentSession.hasLocalDraft = Boolean(path);
      documentSession.source = path ? "backend-draft" : "backend-scratchpad";
      documentDraftView.update("saving draft to backend...");
      scheduleBackendDraftSave(path, html);
    }
    function setDocumentAiOpen(open) {
      const isOpen = Boolean(open);
      documentApp?.classList.toggle("document-ai-open", isOpen);
      documentAiPane?.setAttribute("aria-hidden", isOpen ? "false" : "true");
      documentAiToggle?.setAttribute("aria-expanded", isOpen ? "true" : "false");
      if (documentAiScrim) documentAiScrim.hidden = !isOpen;
      if (isOpen) {
        documentAiPane?.scrollTo?.({top: 0});
        documentAiMain?.scrollTo?.({top: 0});
      }
    }
    function toggleDocumentAi() {
      setDocumentAiOpen(!documentApp?.classList.contains("document-ai-open"));
    }
    function setDocumentLibraryOpen(open) {
      const isOpen = Boolean(open);
      documentApp?.classList.toggle("document-library-open", isOpen);
      documentLibrary?.setAttribute("aria-hidden", isOpen ? "false" : "true");
      documentLibraryToggle?.setAttribute("aria-expanded", isOpen ? "true" : "false");
      if (documentLibraryScrim) documentLibraryScrim.hidden = !isOpen;
    }
    function toggleDocumentLibrary() {
      setDocumentLibraryOpen(!documentApp?.classList.contains("document-library-open"));
    }
    async function loadPrettyDocsList() {
      documentLibraryStatus.textContent = "Loading pretty docs...";
      try {
        const data = await docsApi.listDocuments();
        documentRecords = data.documents || [];
        documentLibraryView.render(documentRecords);
        documentLibraryStatus.textContent = data.count ? `${data.count} docs · backend drafts enabled` : "No pretty docs found.";
        if (documentRecords.length) {
          const selected = selectionStore.getSelectedPath();
          const selectedExists = selected && documentRecords.some((doc) => doc.path === selected);
          const guide = documentRecords.find((doc) => doc.title === "Main Computer User Guide" || doc.path === "main-computer-user-guide.md");
          const nextPath = selectedExists ? selected : (!selected ? guide?.path : "");
          if (selected && !selectedExists) {
            selectionStore.clearSelectedPath();
            documentLibraryStatus.textContent = "Previous selected doc was not found.";
          }
          if (!documentSession.selectedPath && nextPath) {
            await loadPrettyDoc(nextPath, {rememberSelection: true});
          } else {
            documentLibraryView.highlight(documentSession.selectedPath);
          }
        }
      } catch (error) {
        documentLibraryStatus.textContent = `Pretty Docs failed: ${error.message || error}`;
        documentLibraryView.render([]);
      }
    }
    async function loadPrettyDoc(path, options = {}) {
      try {
        const data = await docsApi.readDocument(path);
        const selectedPath = data.path || "";
        const rendered = documentRenderer.render(data.content || "", data.kind || "text");
        const revision = {content_hash: data.content_hash || "", mtime: data.mtime || 0};
        let backendDraft = null;
        try {
          backendDraft = await docsApi.readDraft(selectedPath);
        } catch (draftError) {
          backendDraft = null;
        }
        const localDraftHtml = draftStore.getDraft(selectedPath);
        const draftHtml = backendDraft?.exists ? String(backendDraft.html || "") : localDraftHtml;
        const draftSource = backendDraft?.exists ? "backend-draft" : (localDraftHtml !== null ? "local-fallback" : "disk");
        documentCurrentPrettyPath = selectedPath;
        documentSession.selectedPath = selectedPath;
        documentSession.record = data;
        documentSession.loadedRevision = revision;
        documentSession.diskRenderedHtml = rendered;
        documentSession.localDraftHtml = draftHtml || "";
        documentSession.hasLocalDraft = draftHtml !== null;
        documentSession.source = draftSource;
        if (options.rememberSelection !== false) selectionStore.setSelectedPath(selectedPath);
        revisionStore.setRevision(selectedPath, revision);
        documentCurrentPath.textContent = data.display_path || `pretty_docs/${selectedPath}`;
        setDocumentEditorHtml(draftHtml !== null ? draftHtml : rendered);
        if (backendDraft?.exists && backendDraft.layout) applyDocumentLayoutState(backendDraft.layout);
        else loadDocumentLayoutForCurrentPath(selectedPath);
        const draftLabel = backendDraft?.exists ? "loaded backend draft" : "loaded local fallback draft";
        documentStatus.textContent = draftHtml !== null ? draftLabel : "loaded backend doc";
        documentLibraryStatus.textContent = draftHtml !== null ? draftLabel : "loaded backend doc";
        documentLibraryView.highlight(selectedPath);
        documentDraftView.update();
        loadDocumentAiState();
      } catch (error) {
        documentLibraryStatus.textContent = `Pretty Docs read failed: ${error.message || error}`;
      }
    }
    async function reloadSelectedPrettyDoc() {
      if (!documentSession.selectedPath) return;
      const previousHash = documentSession.loadedRevision?.content_hash || "";
      const hadDraft = documentSession.hasLocalDraft || draftStore.hasDraft(documentSession.selectedPath);
      await loadPrettyDoc(documentSession.selectedPath, {rememberSelection: true});
      if (hadDraft && previousHash && documentSession.loadedRevision?.content_hash && previousHash !== documentSession.loadedRevision.content_hash) {
        documentDraftView.showDiskChangedDraftPreserved();
      } else if (hadDraft) {
        documentStatus.textContent = "backend draft preserved";
      }
    }
    async function discardSelectedDraft() {
      if (!documentSession.selectedPath) return;
      const selectedPath = documentSession.selectedPath;
      draftStore.removeDraft(selectedPath);
      documentSession.localDraftHtml = "";
      documentSession.hasLocalDraft = false;
      documentSession.source = "disk";
      setDocumentEditorHtml(documentSession.diskRenderedHtml || "");
      loadDocumentLayoutForCurrentPath(selectedPath);
      documentDraftView.update("no backend draft");
      try {
        await docsApi.deleteDraft(selectedPath);
        documentDraftView.update("backend draft discarded");
      } catch (error) {
        documentDraftView.update("local fallback discarded; backend delete failed");
      }
    }
    async function loadBackendScratchpadDraft(localFallback = "") {
      try {
        const backendDraft = await docsApi.readDraft("");
        if (documentSession.selectedPath) return;
        if (backendDraft?.exists) {
          setDocumentEditorHtml(String(backendDraft.html || ""));
          if (backendDraft.layout) applyDocumentLayoutState(backendDraft.layout);
          documentSession.source = "backend-scratchpad";
          documentDraftView.update("backend scratchpad loaded");
          return;
        }
      } catch (error) {
        if (documentSession.selectedPath) return;
        if (localFallback) documentDraftView.update("backend unavailable; local fallback loaded");
      }
    }
    function initDocumentApp() {
      if (!documentInitialized) {
        documentInitialized = true;
        const saved = draftStore.getDraft("");
        setDocumentEditorHtml(saved || "<h2>Main Computer Document</h2><p>Start writing here.</p>");
        loadDocumentLayoutForCurrentPath("");
        loadDocumentAiState();
        loadBackendScratchpadDraft(saved || "");
        documentCurrentPath.textContent = "backend scratchpad";
        documentReadonlyNote.textContent = "Backend draft storage is enabled. Edits auto-save to the server; local storage is only a fallback cache.";
        documentDraftView.update();
        documentToolbar.addEventListener("click", (event) => {
          const button = event.target.closest("[data-document-command]");
          if (!button) return;
          event.preventDefault();
          getActiveDocumentEditor()?.focus();
          document.execCommand(button.dataset.documentCommand, false, null);
          if (typeof hydrateDocumentObjects === "function") hydrateDocumentObjects(documentCanvas);
          saveDocumentDraft();
          scheduleDocumentRepagination();
        });
        documentInsertInlineMath?.addEventListener("click", () => insertDocumentMathObject("inline"));
        documentInsertParagraphMath?.addEventListener("click", () => insertDocumentMathObject("paragraph"));
        documentToggleMathLayout?.addEventListener("click", toggleSelectedDocumentMathLayout);
        documentAddHiddenPlugin?.addEventListener("click", createHiddenDocumentPlugin);
        documentInsertScene?.addEventListener("click", promptAndInsertDocumentScene);
        documentInsertGameScene?.addEventListener("click", promptAndInsertDocumentGameScenePlugin);
        documentExportEpub?.addEventListener("click", exportCurrentDocumentAsEpub);
        documentExportPdf?.addEventListener("click", exportCurrentDocumentAsPdf);
        documentExportPdfVector?.addEventListener("click", exportCurrentDocumentAsVectorPdf);
        documentExportPdfSmoke?.addEventListener("click", exportCurrentDocumentPdfSmoke);
        documentExportPdfRasterSmoke?.addEventListener("click", exportCurrentDocumentPdfRasterSmoke);
        documentExportPdfVectorFitSmoke?.addEventListener("click", exportCurrentDocumentPdfVectorFitSmoke);
        documentObjectRuntime?.wireEvents?.();
        hydrateDocumentObjects(documentCanvas);
        documentFormat.addEventListener("change", () => {
          applyDocumentBlockFormat(documentFormat.value);
        });
        document.addEventListener("selectionchange", () => {
          if (documentCanvas?.contains(window.getSelection?.()?.anchorNode)) updateDocumentFormatForCaret();
        });
        documentCanvas.addEventListener("click", updateDocumentFormatForCaret);
        documentCanvas.addEventListener("mouseup", updateDocumentFormatForCaret);
        documentCanvas.addEventListener("keyup", updateDocumentFormatForCaret);
        documentCanvas.addEventListener("input", (event) => {
          if (!event.target.closest(".mc-page-content")) return;
          hydrateDocumentObjects(documentCanvas);
          saveDocumentDraft();
          updateDocumentFormatForCaret();
          scheduleDocumentRepagination();
        });
        documentCanvas.addEventListener("keydown", (event) => {
          if (documentObjectRuntime?.handleObjectKeydown?.(event)) return;
          handleDocumentEditorKeydown(event);
        });
        documentAiLockAnchor.addEventListener("click", () => {
          documentAiState.lockedAnchor = captureDocumentAiAnchor("selection");
          const thread = ensureDocumentAiThread(documentAiState.lockedAnchor);
          thread.anchor = documentAiState.lockedAnchor;
          saveDocumentAiState();
          renderDocumentAiPane();
        });
        documentAiRebase.addEventListener("click", rebaseDocumentAiAnchor);
        documentAiThreads.addEventListener("click", (event) => {
          const button = event.target.closest("[data-document-ai-thread]");
          if (!button) return;
          documentAiState.activeThreadId = button.dataset.documentAiThread || "";
          documentAiState.lockedAnchor = activeDocumentAiThread()?.anchor || null;
          saveDocumentAiState();
          renderDocumentAiPane();
        });
        documentAiPane.addEventListener("click", (event) => {
          const chip = event.target.closest("[data-document-ai-chip]");
          if (!chip) return;
          documentAiPrompt.value = chip.dataset.documentAiChip || "";
          sendDocumentAiRequest(documentAiPrompt.value);
        });
        documentAiSend.addEventListener("click", () => sendDocumentAiRequest());
        documentAiPrompt.addEventListener("keydown", (event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            sendDocumentAiRequest();
          }
        });
        documentAiCancel.addEventListener("click", () => {
          documentAiState.abortController?.abort();
        });
        documentAiApply.addEventListener("click", applyDocumentAiSuggestion);
        documentAiUndo.addEventListener("click", undoDocumentAiEdit);
        documentLayoutButton.addEventListener("click", () => {
          if (documentLayoutPopover.hidden) openDocumentLayoutPopover();
          else closeDocumentLayoutPopover();
        });
        documentLayoutPreset.addEventListener("change", () => {
          const selectedPreset = documentLayoutPreset.value;
          const isCustom = selectedPreset === "custom";
          documentLayoutWidth.disabled = !isCustom;
          documentLayoutHeight.disabled = !isCustom;
          if (!isCustom) {
            const size = documentPagePresets[selectedPreset] || documentPagePresets.letter;
            documentLayoutWidth.value = size.widthPx;
            documentLayoutHeight.value = size.heightPx;
          }
        });
        documentLayoutCancel.addEventListener("click", closeDocumentLayoutPopover);
        documentLayoutApply.addEventListener("click", () => {
          applyDocumentLayoutState(readDocumentLayoutPopoverState());
          saveDocumentDraft();
          documentStatus.textContent = documentSession.layoutState.view.mode === "endless" ? "endless view applied; backend save queued" : "paged layout applied; backend save queued";
          closeDocumentLayoutPopover();
        });
        documentLibraryToggle?.addEventListener("click", toggleDocumentLibrary);
        documentLibraryClose?.addEventListener("click", () => setDocumentLibraryOpen(false));
        documentLibraryScrim?.addEventListener("click", () => setDocumentLibraryOpen(false));
        documentAiToggle?.addEventListener("click", toggleDocumentAi);
        documentAiClose?.addEventListener("click", () => setDocumentAiOpen(false));
        documentAiScrim?.addEventListener("click", () => setDocumentAiOpen(false));
        document.addEventListener("keydown", (event) => {
          if (event.key === "Escape") {
            setDocumentLibraryOpen(false);
            setDocumentAiOpen(false);
          }
        });
        documentLibraryRefresh.addEventListener("click", () => loadPrettyDocsList());
        documentReloadDoc.addEventListener("click", () => reloadSelectedPrettyDoc());
        documentDiscardDraft.addEventListener("click", () => discardSelectedDraft());
      }
      if (!documentLibraryLoaded) {
        documentLibraryLoaded = true;
        loadPrettyDocsList();
      }
    }
