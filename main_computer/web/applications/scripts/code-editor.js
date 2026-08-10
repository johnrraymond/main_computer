(function installMainComputerCodeEditorRuntime(root, factory) {
  "use strict";

  const api = factory(root, function requireCodeEditorCore() {
    if (root && root.MainComputerCodeEditorCore) return root.MainComputerCodeEditorCore;
    if (typeof require === "function") return require("./code-editor-core.js");
    throw new Error("Code Editor core is unavailable; load code-editor-core.js before code-editor.js");
  }, function requireCodeEditorViewModel() {
    if (root && root.MainComputerCodeEditorViewModel) return root.MainComputerCodeEditorViewModel;
    if (typeof require === "function") return require("./code-editor-view-model.js");
    throw new Error("Code Editor view model is unavailable; load code-editor-view-model.js before code-editor.js");
  }, function requireCodeEditorCapabilities() {
    if (root && root.MainComputerCodeEditorCapabilities) return root.MainComputerCodeEditorCapabilities;
    if (typeof require === "function") return require("./code-editor-capabilities.js");
    throw new Error("Code Editor capabilities are unavailable; load code-editor-capabilities.js before code-editor.js");
  });

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  if (root) {
    root.MainComputerCodeEditor = api;
    const documentRef = root.document;
    const rootNode = documentRef && documentRef.querySelector
      ? documentRef.querySelector("#code-editor-app")
      : null;
    root.MainComputerCodeEditorRuntime = api.createCodeEditorRuntime({root: rootNode});
    if (rootNode) {
      root.MainComputerCodeEditorRuntime.mount(rootNode);
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function buildMainComputerCodeEditorRuntime(
  root,
  requireCodeEditorCore,
  requireCodeEditorViewModel,
  requireCodeEditorCapabilities
) {
  "use strict";

  function coreApi() {
    return requireCodeEditorCore();
  }

  function viewModelApi() {
    return requireCodeEditorViewModel();
  }

  function capabilityApi() {
    return requireCodeEditorCapabilities();
  }

  function text(value) {
    return value == null ? "" : String(value);
  }

  function escapeHtml(value) {
    return text(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function freeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.freeze(value);
    Object.keys(value).forEach((key) => freeze(value[key]));
    return value;
  }

  function createCodeEditorRuntime(options = {}) {
    const core = options.core || coreApi();
    const viewModel = options.viewModel || viewModelApi();
    const capabilities = options.capabilities || capabilityApi();
    const dom = {
      root: options.root || null,
      sourceEditor: null,
      sourceWorkspaceEditor: null,
      runtimePreview: null,
      fileMapList: null,
      fileMapStatus: null,
      fileMapSearch: null,
      selectedFiles: null,
      repoInput: null,
      aiderInstruction: null,
      aiderOutput: null,
      reviewedToggle: null,
      saveButtons: [],
      openButton: null,
      previewButton: null,
      applyButton: null,
      discardButton: null,
      closeButton: null,
      activeTitle: null,
      activePath: null,
      draftStatus: null,
      workspaceSummary: null,
      patchStatus: null,
      runtimeState: null,
      receipt: null,
      authoringHost: null,
      monacoHost: null,
      monacoStatus: null
    };
    let state = core.initialState({repoDir: options.repoDir || "."});
    let monacoMounted = false;
    let monacoMounting = null;
    let monacoSignature = "";
    let monacoLastReceipt = null;
    let legacySourceFileClickBound = false;
    let resizeStabilityBound = false;
    let resizeStabilityTimer = 0;

    function currentState() {
      return state;
    }

    function currentViewModel() {
      return viewModel.buildRuntimeViewModel(state);
    }

    function monacoRuntime() {
      return options.monacoAdapter || (root && root.MainComputerMonacoAdapter) || null;
    }

    function rememberMonacoReceipt(receipt = {}) {
      monacoLastReceipt = receipt && typeof receipt === "object" ? receipt : {message: text(receipt)};
      if (dom.authoringHost) {
        dom.authoringHost.dataset.monacoOutcome = text(monacoLastReceipt.externalOutcome || monacoLastReceipt.actionOutcome || "");
      }
      if (dom.monacoStatus) {
        dom.monacoStatus.textContent = monacoStatusText();
      }
      return monacoLastReceipt;
    }

    function monacoStatusText() {
      if (monacoMounted) {
        return state.activeFile
          ? "Monaco editor mounted for the active source file."
          : "Monaco editor mounted in placeholder mode. Open a source file to edit.";
      }
      if (monacoMounting) return "Monaco editor loading in the editor pane.";
      if (monacoLastReceipt && monacoLastReceipt.ok === false) {
        return `Monaco fallback active: ${text(monacoLastReceipt.externalOutcome || monacoLastReceipt.message || "unavailable")}`;
      }
      return "Monaco editor is ready to mount in the editor pane.";
    }

    function requestMonacoLayout(adapter) {
      if (!adapter || typeof adapter.layout !== "function") return;
      try {
        adapter.layout();
      } catch (error) {
        // Layout is best-effort; Monaco will remeasure again on the next frame.
      }
      const raf = root && typeof root.requestAnimationFrame === "function"
        ? root.requestAnimationFrame.bind(root)
        : (callback) => setTimeout(callback, 0);
      raf(() => {
        try {
          adapter.layout();
        } catch (error) {
          // Keep the editor mounted even when a browser reports a transient layout race.
        }
        raf(() => {
          try {
            adapter.layout();
          } catch (error) {
            // Final best-effort layout pass.
          }
        });
      });
    }

    function replaceState(nextState) {
      state = nextState;
      render();
      return runtimeResult();
    }

    function runtimeResult(extra = {}) {
      return freeze(Object.assign({
        ok: state.lastError == null,
        state,
        viewModel: currentViewModel()
      }, extra));
    }

    function sourceWorkspaceText() {
      const editor = dom.sourceWorkspaceEditor || (dom.root && dom.root.querySelector
        ? dom.root.querySelector("#code-studio-source-editor")
        : null);
      if (!editor) return "";
      return text(Object.prototype.hasOwnProperty.call(editor, "value") ? editor.value : editor.textContent);
    }

    function parseAuthoredSourceWorkspace() {
      const source = sourceWorkspaceText();
      const parserCtor = root && root.DOMParser;
      if (!source.trim() || typeof parserCtor !== "function") {
        return null;
      }
      let doc = null;
      try {
        doc = new parserCtor().parseFromString(source, "text/html");
      } catch (error) {
        return null;
      }
      const workspace = doc && doc.querySelector
        ? doc.querySelector('[data-mc-component="code-workspace"]')
        : null;
      if (!workspace) return null;
      const files = [];
      const byPath = {};
      Array.from(workspace.querySelectorAll('[data-mc-component="code-file"]')).forEach((node, index) => {
        const rawPath = node.getAttribute("data-mc-file-path") || `untitled-${index + 1}.txt`;
        let path = "";
        try {
          path = core.normalizeProjectPath(rawPath);
        } catch (error) {
          return;
        }
        const language = text(node.getAttribute("data-mc-language") || core.inferLanguage(path) || "plaintext");
        const content = text(node.textContent).replace(/^\n+|\s+$/g, "");
        const entry = {
          path,
          name: path.split("/").pop(),
          kind: "file",
          bytes: content.length,
          depth: Math.max(0, path.split("/").length - 1),
          language,
          source: "authored-source-workspace"
        };
        files.push(entry);
        byPath[path] = {
          repoDir: ".",
          path,
          content,
          language,
          sourceHash: "",
          source: "authored-source-workspace"
        };
      });
      return files.length ? {repoDir: ".", files, byPath} : null;
    }

    async function openFileFromAuthoredSource(path) {
      const snapshot = parseAuthoredSourceWorkspace();
      if (!snapshot || !snapshot.byPath[path]) return null;
      const file = snapshot.byPath[path];
      const sourceHash = typeof capabilities.sha256Text === "function"
        ? await capabilities.sha256Text(file.content)
        : "";
      return Object.assign({}, file, {sourceHash});
    }

    function hydrateWorkspaceFromAuthoredSource() {
      if (state.workspace && Array.isArray(state.workspace.files) && state.workspace.files.length > 0) {
        return;
      }
      const snapshot = parseAuthoredSourceWorkspace();
      if (!snapshot) return;
      state = core.applyWorkspaceInspection(state, {
        repoDir: snapshot.repoDir,
        path: "",
        files: snapshot.files,
        inspectedAt: new Date().toISOString()
      });
    }

    async function inspectWorkspace(input = {}) {
      try {
        const repoDir = input.repoDir || input.repo_dir || (dom.repoInput && dom.repoInput.value) || state.repoDir;
        const query = Object.prototype.hasOwnProperty.call(input, "query")
          ? input.query
          : (dom.fileMapSearch && dom.fileMapSearch.value) || "";
        const sourceSnapshot = parseAuthoredSourceWorkspace();
        if (sourceSnapshot && (repoDir === "." || input.source === "authored-source-workspace")) {
          const filteredFiles = query
            ? sourceSnapshot.files.filter((file) => file.path.toLowerCase().includes(text(query).toLowerCase()))
            : sourceSnapshot.files;
          return replaceState(core.applyWorkspaceInspection(state, {
            repoDir: sourceSnapshot.repoDir,
            path: "",
            files: filteredFiles,
            inspectedAt: new Date().toISOString()
          }));
        }
        const result = await capabilities.inspectWorkspace(Object.assign({}, input, {repoDir, query}));
        return replaceState(core.applyWorkspaceInspection(state, result));
      } catch (error) {
        const sourceSnapshot = parseAuthoredSourceWorkspace();
        if (sourceSnapshot) {
          return replaceState(core.applyWorkspaceInspection(state, {
            repoDir: sourceSnapshot.repoDir,
            path: "",
            files: sourceSnapshot.files,
            inspectedAt: new Date().toISOString()
          }));
        }
        return replaceState(core.recordFailure(state, "inspectWorkspace", error));
      }
    }

    async function openFile(input = {}) {
      let path = "";
      try {
        path = core.normalizeProjectPath(input.path || input.filePath || input.selectedPath || selectedDomPath());
        const authoredFile = await openFileFromAuthoredSource(path);
        if (authoredFile) {
          hydrateWorkspaceFromAuthoredSource();
          return replaceState(core.applyOpenedFile(state, authoredFile));
        }
        const repoDir = input.repoDir || input.repo_dir || (dom.repoInput && dom.repoInput.value) || state.repoDir;
        const result = await capabilities.openFile(Object.assign({}, input, {path, repoDir}));
        return replaceState(core.applyOpenedFile(state, result));
      } catch (error) {
        if (path) {
          try {
            const authoredFile = await openFileFromAuthoredSource(path);
            if (authoredFile) {
              hydrateWorkspaceFromAuthoredSource();
              return replaceState(core.applyOpenedFile(state, authoredFile));
            }
          } catch (fallbackError) {
            // Preserve the original failure; it is the one closest to the requested intent.
          }
        }
        return replaceState(core.recordFailure(state, "openFile", error));
      }
    }

    function editDraft(input = {}) {
      try {
        const next = core.applyDraftEdit(state, input);
        return replaceState(next);
      } catch (error) {
        return replaceState(core.recordFailure(state, "editDraft", error));
      }
    }

    async function saveFile(input = {}) {
      try {
        const draftText = Object.prototype.hasOwnProperty.call(input, "text")
          ? input.text
          : domDraftText();
        const draftPath = input.path || (state.draft && state.draft.path) || (state.activeFile && state.activeFile.path);
        const draftAlreadyCurrent = state.draft && state.draft.path === draftPath && state.draft.text === draftText;
        if (draftText !== null && !draftAlreadyCurrent) {
          state = core.applyDraftEdit(state, {
            path: draftPath,
            text: draftText
          });
        }
        const request = core.buildSaveRequest(state, input);
        const result = await capabilities.saveFile(request);
        const savedHash = await capabilities.sha256Text(request.replacement_text);
        return replaceState(core.applySavedFile(state, {
          savedPath: result.savedPath || request.path,
          text: request.replacement_text,
          sourceHash: savedHash,
          changedFiles: result.changedFiles,
          handle: result.handle,
          status: result.status,
          raw: result.raw
        }));
      } catch (error) {
        return replaceState(core.recordFailure(state, "saveFile", error));
      }
    }

    function discardDraft(input = {}) {
      void input;
      try {
        return replaceState(core.discardDraft(state));
      } catch (error) {
        return replaceState(core.recordFailure(state, "discardDraft", error));
      }
    }

    function closeFile(input = {}) {
      void input;
      try {
        return replaceState(core.closeFile(state));
      } catch (error) {
        return replaceState(core.recordFailure(state, "closeFile", error));
      }
    }

    async function previewAiderPlan(input = {}) {
      try {
        const request = core.buildPatchPreviewRequest(state, Object.assign({}, domPatchInput(), input));
        const result = await capabilities.previewAiderPlan(request);
        return replaceState(core.applyPatchPreview(state, result));
      } catch (error) {
        return replaceState(core.recordFailure(state, "previewAiderPlan", error));
      }
    }

    async function applyReviewedPatch(input = {}) {
      try {
        const reviewedByControl = dom.reviewedToggle && dom.reviewedToggle.checked === true;
        const request = core.buildReviewedPatchApplyRequest(state, Object.assign({
          reviewed: reviewedByControl,
          approved: reviewedByControl,
          confirmed: reviewedByControl
        }, input));
        const result = await capabilities.applyReviewedPatch(request);
        return replaceState(core.applyReviewedPatchResult(state, result));
      } catch (error) {
        return replaceState(core.recordFailure(state, "applyReviewedPatch", error));
      }
    }

    function mount(rootNode) {
      dom.root = rootNode || dom.root;
      if (!dom.root) return runtimeResult({mounted: false});
      ensureCanonicalSurface(dom.root);
      activateLegacyRuntimePane();
      enforceLegacyFidelityResizeStability();
      bindLegacyFidelityResizeStability();
      dom.root.dataset.codeEditorRuntime = "dsl-native";
      dom.root.dataset.codeEditorRuntimeFacade = "MainComputerCodeEditorRuntime";
      dom.authoringHost = dom.root.querySelector("#code-studio-runtime-monaco");
      dom.monacoHost = dom.authoringHost;
      dom.monacoStatus = dom.root.querySelector("#code-editor-monaco-status");
      dom.sourceEditor = dom.root.querySelector("#code-studio-runtime-draft");
      dom.sourceWorkspaceEditor = dom.root.querySelector("#code-studio-source-editor");
      dom.runtimePreview = dom.root.querySelector("#code-studio-runtime-preview");
      dom.fileMapList = dom.root.querySelector("#file-map-list");
      dom.fileMapStatus = dom.root.querySelector("#file-map-status");
      dom.fileMapSearch = dom.root.querySelector("#file-map-search");
      dom.selectedFiles = dom.root.querySelector("#aider-files");
      dom.repoInput = dom.root.querySelector("#aider-repo");
      dom.aiderInstruction = dom.root.querySelector("#aider-instruction");
      dom.aiderOutput = dom.root.querySelector("#aider-output");
      dom.reviewedToggle = dom.root.querySelector("#aider-reviewed");
      dom.openButton = dom.root.querySelector("#file-map-apply");
      dom.previewButton = dom.root.querySelector("#aider-preview");
      dom.applyButton = dom.root.querySelector("#aider-run");
      dom.discardButton = dom.root.querySelector("#code-studio-restore-live-workspace");
      dom.closeButton = dom.root.querySelector("#code-studio-clear-live-workspace");
      dom.saveButtons = [
        dom.root.querySelector("#code-studio-commit-runtime"),
        dom.root.querySelector("#code-studio-save-live-workspace")
      ].filter(Boolean);
      dom.activeTitle = dom.root.querySelector("#code-editor-active-title");
      dom.activePath = dom.root.querySelector("#code-editor-active-path") || dom.root.querySelector("#code-studio-top-route-status");
      dom.draftStatus = dom.root.querySelector("#code-editor-draft-status");
      dom.workspaceSummary = dom.root.querySelector("#code-editor-workspace-summary");
      dom.patchStatus = dom.root.querySelector("#code-editor-patch-status");
      dom.runtimeState = dom.root.querySelector("#code-studio-runtime-state");
      bindButton("#file-map-refresh", () => inspectWorkspace({query: dom.fileMapSearch ? dom.fileMapSearch.value : ""}));
      bindButton("#file-map-apply", () => openFile({path: selectedDomPath()}));
      bindButton("#code-studio-mount-runtime", () => render());
      bindButton("#code-studio-commit-runtime", () => saveFile({text: domDraftText()}));
      bindButton("#code-studio-save-live-workspace", () => saveFile({text: domDraftText()}));
      bindButton("#aider-preview", () => previewAiderPlan(domPatchInput()));
      bindButton("#aider-run", () => applyReviewedPatch({
        reviewed: dom.reviewedToggle && dom.reviewedToggle.checked === true,
        approved: dom.reviewedToggle && dom.reviewedToggle.checked === true,
        confirmed: dom.reviewedToggle && dom.reviewedToggle.checked === true
      }));
      bindButton("#code-studio-restore-live-workspace", () => discardDraft());
      bindButton("#code-studio-clear-live-workspace", () => closeFile());
      bindEnter(dom.fileMapSearch, () => inspectWorkspace({query: dom.fileMapSearch ? dom.fileMapSearch.value : ""}));
      bindInput(dom.sourceEditor, () => {
        if (state.activeFile) editDraft({path: state.activeFile.path, text: dom.sourceEditor.value});
      });
      bindInput(dom.reviewedToggle, () => render());
      bindLegacySourceFileClicks();
      hydrateWorkspaceFromAuthoredSource();
      render();
      return runtimeResult({mounted: true});
    }

    function bindLegacySourceFileClicks() {
      if (!dom.root || legacySourceFileClickBound) return;
      legacySourceFileClickBound = true;
      dom.root.addEventListener("click", (ev) => {
        const target = ev.target && ev.target.closest
          ? ev.target.closest("[data-code-studio-file]")
          : null;
        if (!target || !dom.root.contains(target)) return;
        const path = target.getAttribute("data-code-studio-file") || "";
        if (!path) return;
        ev.preventDefault();
        ev.stopPropagation();
        if (typeof ev.stopImmediatePropagation === "function") ev.stopImmediatePropagation();
        dom.root.querySelectorAll("[data-code-studio-file]").forEach((node) => {
          node.classList.toggle("active", node === target);
          if (node === target) node.setAttribute("aria-current", "true");
          else node.removeAttribute("aria-current");
        });
        if (dom.selectedFiles) dom.selectedFiles.value = path;
        openFile({path, source: "authored-source-workspace"});
      }, true);
    }

    function bindButton(selector, handler) {
      const button = dom.root && dom.root.querySelector(selector);
      if (!button || button.dataset.codeEditorRuntimeBound) return;
      button.dataset.codeEditorRuntimeBound = "true";
      button.addEventListener("click", (event) => {
        event.preventDefault();
        Promise.resolve(handler()).catch((error) => {
          replaceState(core.recordFailure(state, selector, error));
        });
      });
    }

    function bindInput(element, handler) {
      if (!element || element.dataset.codeEditorRuntimeBound) return;
      element.dataset.codeEditorRuntimeBound = "true";
      element.addEventListener("input", handler);
      element.addEventListener("change", handler);
    }

    function bindEnter(element, handler) {
      if (!element || element.dataset.codeEditorRuntimeEnterBound) return;
      element.dataset.codeEditorRuntimeEnterBound = "true";
      element.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          Promise.resolve(handler()).catch((error) => replaceState(core.recordFailure(state, "inspectWorkspace", error)));
        }
      });
    }

    function isLegacyFidelitySurface() {
      return !!(dom.root && dom.root.dataset && dom.root.dataset.codeEditorRuntimeSurfaceMode === "legacy-fidelity");
    }


    function enforceLegacyFidelityDefaultDiagnostics() {
      if (!dom.root || !isLegacyFidelitySurface()) return;
      const defaultMode = dom.root.dataset.codeEditorMode !== "mcel";
      const proofDock = dom.root.querySelector("#code-studio-bottom-panel");
      if (proofDock) {
        if (defaultMode) {
          proofDock.dataset.expanded = "false";
          proofDock.dataset.mcelResolvedPlacement = "hidden";
          proofDock.setAttribute("aria-hidden", "true");
        } else {
          proofDock.setAttribute("aria-hidden", "false");
        }
      }
      const proofDockToggle = dom.root.querySelector("#code-studio-toggle-assistant");
      if (proofDockToggle && defaultMode) {
        proofDockToggle.setAttribute("aria-expanded", "false");
        proofDockToggle.textContent = "Open proof dock";
      }
    }

    function enforceLegacyFidelityResizeStability() {
      if (!dom.root || !isLegacyFidelitySurface()) return;
      dom.root.dataset.codeEditorResizeStability = "locked";
      if (dom.root.dataset.codeEditorMode !== "mcel") {
        dom.root.dataset.codeEditorMode = "legacy-fidelity";
      }
      const shell = dom.root.querySelector(".code-studio-shell");
      if (shell) {
        shell.dataset.codeEditorRuntimeSurfaceMode = "legacy-fidelity";
        shell.dataset.codeEditorResizeStability = "locked";
      }
      const body = dom.root.querySelector(".code-studio-body");
      if (body) {
        body.dataset.codeEditorResizeStability = "locked";
      }
      const editor = dom.root.querySelector(".code-studio-editor-group");
      if (editor) {
        editor.dataset.codeEditorResizeStability = "locked";
      }
      enforceLegacyFidelityDefaultDiagnostics();
    }

    function bindLegacyFidelityResizeStability() {
      if (resizeStabilityBound || !root || !dom.root || !isLegacyFidelitySurface()) return;
      resizeStabilityBound = true;
      const schedule = () => {
        if (!root || typeof root.setTimeout !== "function") {
          enforceLegacyFidelityResizeStability();
          requestMonacoLayout(monacoRuntime());
          return;
        }
        if (resizeStabilityTimer) root.clearTimeout(resizeStabilityTimer);
        resizeStabilityTimer = root.setTimeout(() => {
          resizeStabilityTimer = 0;
          enforceLegacyFidelityResizeStability();
          requestMonacoLayout(monacoRuntime());
        }, 40);
      };
      root.addEventListener?.("resize", schedule);
      root.visualViewport?.addEventListener?.("resize", schedule);
      if (typeof root.MutationObserver === "function") {
        const observer = new root.MutationObserver((records) => {
          if (records.some((record) => record.attributeName === "data-code-editor-mode" || record.attributeName === "data-code-editor-runtime-surface-mode")) {
            schedule();
          }
        });
        observer.observe(dom.root, {
          attributes: true,
          attributeFilter: ["data-code-editor-mode", "data-code-editor-runtime-surface-mode"]
        });
      }
      schedule();
    }

    function activateLegacyRuntimePane() {
      if (!dom.root || !isLegacyFidelitySurface()) return;
      const runtimePane = dom.root.querySelector('[data-code-studio-pane="runtime"]');
      if (!runtimePane) return;
      dom.root.querySelectorAll("[data-code-studio-pane]").forEach((pane) => {
        const active = pane === runtimePane;
        pane.classList.toggle("active", active);
        pane.setAttribute("aria-hidden", active ? "false" : "true");
      });
      dom.root.querySelectorAll("[data-code-studio-tab]").forEach((tab) => {
        const active = tab.getAttribute("data-code-studio-tab") === "runtime";
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
      });
      runtimePane.dataset.codeEditorRuntimeOwnedPane = "true";
      runtimePane.dataset.codeEditorRuntimePrimaryPane = "true";
      if (dom.root.dataset.codeEditorMode !== "mcel") {
        dom.root.dataset.codeEditorMode = "legacy-fidelity";
      }
    }

    function refreshRuntimeEditorRefs() {
      if (!dom.root) return;
      dom.authoringHost = dom.root.querySelector("#code-studio-runtime-monaco") || dom.authoringHost;
      dom.monacoHost = dom.authoringHost;
      dom.monacoStatus = dom.root.querySelector("#code-editor-monaco-status") || dom.monacoStatus;
      dom.sourceEditor = dom.root.querySelector("#code-studio-runtime-draft") || dom.sourceEditor;
      dom.activeTitle = dom.root.querySelector("#code-editor-active-title") || dom.activeTitle;
      dom.activePath = dom.root.querySelector("#code-editor-active-path") || dom.root.querySelector("#code-studio-top-route-status") || dom.activePath;
      dom.draftStatus = dom.root.querySelector("#code-editor-draft-status") || dom.draftStatus;
      dom.patchStatus = dom.root.querySelector("#code-editor-patch-status") || dom.patchStatus;
      if (dom.sourceEditor && !dom.sourceEditor.dataset.codeEditorRuntimeInputBound) {
        dom.sourceEditor.dataset.codeEditorRuntimeInputBound = "true";
        dom.sourceEditor.addEventListener("input", () => {
          if (state.activeFile) editDraft({path: state.activeFile.path, text: dom.sourceEditor.value});
        });
      }
      const applyDraft = dom.root.querySelector("[data-code-studio-apply-draft]");
      if (applyDraft && !applyDraft.dataset.codeEditorRuntimeBound) {
        applyDraft.dataset.codeEditorRuntimeBound = "true";
        applyDraft.addEventListener("click", () => saveFile({text: domDraftText()}));
      }
    }

    function runtimePreviewIsPrimaryEditorHost() {
      return !!(dom.runtimePreview && (
        isLegacyFidelitySurface() ||
        dom.runtimePreview.getAttribute("data-code-editor-region") === "primary-editor-shell" ||
        dom.runtimePreview.getAttribute("data-mcel-region-role") === "primary-authoring-host"
      ));
    }

    function renderPrimaryEditorPreview(active, patch, receipts) {
      if (!dom.runtimePreview) return;
      const documentModel = monacoDocumentFor(active);
      const selectedPath = documentModel.placeholder ? "" : documentModel.path;
      const existingFrame = dom.runtimePreview.querySelector('[data-code-editor-runtime-monaco-frame="true"]');
      if (!existingFrame) {
        dom.runtimePreview.innerHTML = [
          '<section class="code-studio-monaco-authoring-surface mcel-code-editor-primary-authoring-surface" data-code-editor-runtime-monaco-frame="true" data-code-editor-region="primary-editor" data-monaco-mounted="false" data-monaco-outcome="not-started" data-mcel-surface-id="code-editor.surface.monaco-selected-file-editor" data-mcel-surface-kind="monaco-editor" data-mcel-surface-role="primary-authoring-surface" data-mcel-surface-contract="code-editor.contract.authoring.monaco-golden-path" data-mcel-renderer="MainComputerCodeEditorRuntime" data-mcel-projection="authoring" data-mcel-region="code-editor.region.primary-editor" data-mcel-region-role="primary-authoring-surface">',
            '<header class="code-studio-monaco-authoring-toolbar">',
              '<div class="code-studio-monaco-authoring-title">',
                '<strong id="code-editor-active-title">No file open</strong>',
                '<span id="code-editor-draft-status">Open a source file from the workspace</span>',
              '</div>',
              '<button type="button" data-code-studio-apply-draft="true">Save file</button>',
            '</header>',
            '<div id="code-studio-runtime-monaco" class="mcel-code-editor-authoring-host code-studio-monaco-host" data-code-studio-monaco-runtime="host" data-code-editor-runtime-host="true" data-code-editor-monaco-host="true" data-code-editor-golden-path="monaco-authoring" data-code-editor-region="primary-editor-host" data-mcel-node-id="code-editor.node.monaco-selected-file-editor" data-mcel-node-type="primary_authoring_editor" data-mcel-node-label="Monaco selected-file editor" data-mcel-source="MainComputerCodeEditorRuntime.syncMonacoEditor" data-mcel-provenance="runtime.monaco.mount" data-monaco-state="idle" aria-label="Monaco Code Editor surface">Loading Monaco editor…</div>',
            '<textarea id="code-studio-runtime-draft" class="code-studio-runtime-fallback" spellcheck="false" data-code-studio-selected-file="true" data-code-editor-runtime-draft="true" data-code-editor-fallback-editor="textarea" aria-label="Code Editor source draft fallback" disabled></textarea>',
            '<div id="code-editor-monaco-status" class="code-studio-monaco-status mcel-code-editor-muted">Monaco editor mounts here and remains visible even before a source file is opened.</div>',
            '<output id="code-editor-runtime-receipt" class="code-studio-runtime-receipt" aria-live="polite">No runtime receipts yet.</output>',
          '</section>'
        ].join("");
      }
      refreshRuntimeEditorRefs();
      const frame = dom.runtimePreview.querySelector('[data-code-editor-runtime-monaco-frame="true"]');
      if (frame) {
        frame.dataset.codeEditorSelectedPath = selectedPath;
        frame.dataset.monacoMounted = monacoMounted ? "true" : "false";
        frame.dataset.monacoOutcome = monacoLastReceipt
          ? text(monacoLastReceipt.externalOutcome || monacoLastReceipt.actionOutcome || "")
          : "not-started";
      }
      if (dom.activeTitle) dom.activeTitle.textContent = active.title;
      if (dom.draftStatus) dom.draftStatus.textContent = active.statusText;
      if (dom.receipt) dom.receipt.textContent = receipts.statusText;
      if (dom.patchStatus) dom.patchStatus.textContent = patch.statusText;
    }

    function render() {
      if (!dom.root) return;
      dom.root.dataset.codeEditorRuntime = "dsl-native";
      dom.root.dataset.codeEditorRuntimeFacade = "MainComputerCodeEditorRuntime";
      activateLegacyRuntimePane();
      const vm = currentViewModel();
      const active = vm.activeFile;
      const patch = vm.patch;
      const receipts = vm.receipts;

      if (dom.fileMapList) {
        dom.fileMapList.innerHTML = viewModel.renderFileListHtml(state);
        dom.fileMapList.querySelectorAll("[data-code-editor-runtime-file]").forEach((node) => {
          const path = node.getAttribute("data-code-editor-runtime-file") || "";
          const input = node.querySelector("input");
          if (input && state.activeFile && state.activeFile.path === path) {
            input.checked = true;
          }
          if (!node.dataset.codeEditorRuntimeBound) {
            node.dataset.codeEditorRuntimeBound = "true";
            node.addEventListener("dblclick", () => openFile({path}));
            node.addEventListener("click", () => {
              if (input && !input.disabled) input.checked = true;
              if (dom.selectedFiles && path) dom.selectedFiles.value = path;
              updateButtonStates(vm);
            });
          }
        });
      }
      if (dom.fileMapStatus) {
        dom.fileMapStatus.textContent = vm.workspace.statusText;
      }
      if (dom.workspaceSummary) {
        dom.workspaceSummary.textContent = `${vm.workspace.repoDir} · ${vm.workspace.fileCount} entr${vm.workspace.fileCount === 1 ? "y" : "ies"}`;
      }
      if (dom.activeTitle) {
        dom.activeTitle.textContent = active.title;
      }
      if (dom.activePath) {
        dom.activePath.textContent = active.path || "No file open";
      }
      if (dom.draftStatus) {
        dom.draftStatus.textContent = active.statusText;
      }
      if (dom.root && dom.root.querySelectorAll) {
        dom.root.querySelectorAll("[data-code-studio-file]").forEach((node) => {
          const isActive = !!(active.path && node.getAttribute("data-code-studio-file") === active.path);
          node.classList.toggle("active", isActive);
          if (isActive) node.setAttribute("aria-current", "true");
          else node.removeAttribute("aria-current");
        });
      }
      if (dom.sourceEditor) {
        const nextText = active.open ? active.text : "";
        if (dom.sourceEditor.value !== nextText) {
          dom.sourceEditor.value = nextText;
        }
        dom.sourceEditor.disabled = !active.open;
        dom.sourceEditor.placeholder = active.open
          ? "Edit the active source file."
          : "Load a workspace and open a source file.";
      }
      // #code-studio-source-editor is the authored source workspace, not a draft mirror.
      // Keeping it intact preserves the original Code Studio file model for legacy-fidelity clicks.
      if (dom.runtimePreview) {
        if (runtimePreviewIsPrimaryEditorHost()) {
          renderPrimaryEditorPreview(active, patch, receipts);
        } else {
          dom.runtimePreview.innerHTML = viewModel.renderRuntimePreviewHtml(state);
          const runtimeReceipt = dom.runtimePreview.querySelector("#code-editor-runtime-receipt");
          if (runtimeReceipt) dom.receipt = runtimeReceipt;
        }
      }
      if (dom.patchStatus) {
        dom.patchStatus.textContent = patch.statusText;
      }
      if (dom.aiderOutput) {
        dom.aiderOutput.textContent = state.lastError
          ? `Runtime error: ${state.lastError.message}`
          : [receipts.statusText, patch.statusText].filter(Boolean).join("\n");
      }
      if (dom.selectedFiles && state.activeFile) {
        dom.selectedFiles.value = state.activeFile.path;
      }
      if (dom.repoInput && dom.repoInput.value !== vm.workspace.repoDir) {
        dom.repoInput.value = vm.workspace.repoDir;
      }
      if (dom.runtimeState) {
        dom.runtimeState.textContent = `runtime: mounted / ${active.status}`;
      }
      if (dom.monacoStatus) {
        dom.monacoStatus.textContent = monacoStatusText();
      }
      updateButtonStates(vm);
      syncMonacoEditor(active);
      dom.root.dataset.codeEditorRuntime = "dsl-native";
      dom.root.dataset.codeEditorRuntimeFacade = "MainComputerCodeEditorRuntime";
      dom.root.dataset.codeEditorDraftStatus = active.status;
      enforceLegacyFidelityResizeStability();
    }

    function updateButtonStates(vm) {
      const active = vm.activeFile;
      const patch = vm.patch;
      dom.saveButtons.forEach((button) => {
        button.disabled = !active.saveEnabled;
      });
      if (dom.discardButton) dom.discardButton.disabled = !active.open;
      if (dom.closeButton) dom.closeButton.disabled = !active.open;
      if (dom.openButton) dom.openButton.disabled = !selectedDomPath();
      if (dom.previewButton) dom.previewButton.disabled = !(active.open && active.dirty);
      if (dom.applyButton) {
        dom.applyButton.disabled = !(patch.present && dom.reviewedToggle && dom.reviewedToggle.checked === true);
      }
    }

    function monacoDocumentFor(active) {
      if (active && active.open) {
        const path = active.path || "untitled.txt";
        return {
          path,
          language: active.language || core.inferLanguage(path),
          value: text(active.text),
          readOnly: false,
          placeholder: false
        };
      }
      return {
        path: "__no-file-open__.md",
        language: "markdown",
        value: [
          "# No file open",
          "",
          "Load a workspace and open a source file from the file list.",
          "",
          "The Monaco surface is mounted here permanently so the Code Editor does not flip",
          "between a blank legacy pane and the canonical DSL-native runtime surface."
        ].join("\n"),
        readOnly: true,
        placeholder: true
      };
    }

    function syncMonacoEditor(active) {
      if (!dom.authoringHost || !dom.monacoHost) return;
      const adapter = monacoRuntime();
      const documentModel = monacoDocumentFor(active);
      const path = documentModel.path;
      const language = documentModel.language;
      const value = documentModel.value;
      const readOnly = documentModel.readOnly;
      const signature = `${path}\0${language}\0${readOnly ? "readonly" : "editable"}`;

      if (!adapter || typeof adapter.mount !== "function") {
        monacoMounted = false;
        monacoSignature = "";
        dom.authoringHost.dataset.monacoState = "fallback";
        rememberMonacoReceipt({
          ok: false,
          actionOutcome: "blocked",
          externalOutcome: "adapter-unavailable",
          message: "MainComputerMonacoAdapter is not loaded; using textarea fallback."
        });
        return;
      }

      if (monacoMounted && typeof adapter.setModel === "function") {
        const currentValue = typeof adapter.getValue === "function" ? adapter.getValue() : null;
        if (monacoSignature === signature && currentValue === value) {
          dom.authoringHost.dataset.monacoState = "mounted";
          requestMonacoLayout(adapter);
          if (dom.monacoStatus) dom.monacoStatus.textContent = monacoStatusText();
          return;
        }
        const result = adapter.setModel({path, language, value, readOnly});
        rememberMonacoReceipt(Object.assign({effect: "editor.monaco.setModel"}, result));
        if (result && result.ok !== false) {
          monacoSignature = signature;
          dom.authoringHost.dataset.monacoState = "mounted";
          requestMonacoLayout(adapter);
          if (dom.monacoStatus) dom.monacoStatus.textContent = monacoStatusText();
          return;
        }
        monacoMounted = false;
        monacoSignature = "";
      }

      if (monacoMounting) return;
      dom.authoringHost.dataset.monacoState = "loading";
      if (dom.monacoStatus) dom.monacoStatus.textContent = monacoStatusText();
      monacoMounting = Promise.resolve(adapter.mount({
        host: dom.monacoHost,
        path,
        language,
        value,
        readOnly,
        allowCdn: true,
        onReceipt: rememberMonacoReceipt,
        onChange: (nextText) => {
          if (!documentModel.placeholder && state.activeFile && state.activeFile.path === path) {
            editDraft({path, text: nextText});
          }
        }
      })).then((receipt) => {
        monacoMounting = null;
        rememberMonacoReceipt(receipt);
        monacoMounted = !!(receipt && receipt.ok !== false);
        monacoSignature = monacoMounted ? signature : "";
        dom.authoringHost.dataset.monacoState = monacoMounted ? "mounted" : "fallback";
        if (monacoMounted) requestMonacoLayout(adapter);
        render();
        return receipt;
      }).catch((error) => {
        monacoMounting = null;
        monacoMounted = false;
        monacoSignature = "";
        rememberMonacoReceipt({
          ok: false,
          actionOutcome: "exception",
          externalOutcome: "mount-exception",
          message: error && error.message ? error.message : text(error)
        });
        if (dom.authoringHost) dom.authoringHost.dataset.monacoState = "fallback";
        render();
      });
    }

    function domPatchInput() {
      const input = {
        repoDir: (dom.repoInput && dom.repoInput.value) || state.repoDir,
        projectRoot: ".",
        instruction: dom.aiderInstruction ? dom.aiderInstruction.value : ""
      };
      if (state.draft && state.draft.dirty === true && state.activeFile) {
        input.changes = [{
          operation: "modify",
          path: state.draft.path,
          expected_before_sha256: state.draft.baseHash || state.activeFile.sourceHash,
          replacement_text: state.draft.text
        }];
      }
      return input;
    }

    function selectedDomPath() {
      if (dom.root) {
        const checked = dom.root.querySelector('input[name="code-editor-runtime-open-file"]:checked');
        if (checked && checked.value) return checked.value;
      }
      if (dom.selectedFiles && dom.selectedFiles.value) {
        return dom.selectedFiles.value.split(/\r?\n|,/).map((part) => part.trim()).filter(Boolean)[0] || "";
      }
      return "";
    }

    function domDraftText() {
      const adapter = monacoRuntime();
      if (monacoMounted && adapter && typeof adapter.getValue === "function") {
        const value = adapter.getValue();
        if (typeof value === "string") return value;
      }
      if (dom.sourceEditor && state.activeFile) return dom.sourceEditor.value;
      if (state.draft) return state.draft.text;
      return null;
    }

    return freeze({
      schema: "main-computer-code-editor-runtime-v1",
      facade: "MainComputerCodeEditorRuntime",
      state: currentState,
      viewModel: currentViewModel,
      inspectWorkspace,
      openFile,
      editDraft,
      saveFile,
      discardDraft,
      closeFile,
      previewAiderPlan,
      applyReviewedPatch,
      mount,
      render,
      monacoDebug: () => ({
        mounted: monacoMounted,
        mounting: !!monacoMounting,
        signature: monacoSignature,
        receipt: monacoLastReceipt,
        hostPresent: !!dom.monacoHost,
        hostIsDirectPane: dom.monacoHost === dom.authoringHost,
        runtimePaneActive: !!(dom.root && dom.root.querySelector('[data-code-studio-pane="runtime"].active')),
        activePane: dom.root && dom.root.querySelector('[data-code-studio-pane].active')
          ? dom.root.querySelector('[data-code-studio-pane].active').getAttribute("data-code-studio-pane")
          : "",
        authoredSourceWorkspaceFiles: (parseAuthoredSourceWorkspace() || {files: []}).files.length
      })
    });
  }

  function preserveExistingCodeStudioSurface(rootNode) {
    if (!rootNode || !rootNode.querySelector) return false;
    const existingShell = rootNode.querySelector(".code-studio-shell");
    const runtimePreview = rootNode.querySelector("#code-studio-runtime-preview");
    if (!existingShell || !runtimePreview) return false;
    rootNode.dataset.codeEditorRuntimeSurfaceMode = "legacy-fidelity";
    rootNode.dataset.codeEditorMode = "legacy-fidelity";
    existingShell.dataset.codeEditorRuntimeSurface = "dsl-native";
    existingShell.dataset.codeEditorRuntimeSurfaceMode = "legacy-fidelity";
    existingShell.dataset.codeEditorRuntimeOwner = "MainComputerCodeEditorRuntime";
    runtimePreview.dataset.codeEditorRuntimePrimaryHost = "true";

    const doc = rootNode.ownerDocument || (root && root.document) || null;
    const actions = rootNode.querySelector(".aider-actions");
    if (doc && actions && !rootNode.querySelector("#aider-reviewed")) {
      const reviewed = doc.createElement("span");
      reviewed.className = "aider-reviewed code-editor-reviewed-approval";
      reviewed.setAttribute("data-code-editor-runtime-added", "reviewed-patch-approval");
      reviewed.innerHTML = '<input id="aider-reviewed" type="checkbox"/> reviewed patch';
      actions.appendChild(reviewed);
    }

    const aiderOutput = rootNode.querySelector("#aider-output");
    if (doc && aiderOutput && !rootNode.querySelector("#code-editor-patch-status")) {
      const patchStatus = doc.createElement("div");
      patchStatus.id = "code-editor-patch-status";
      patchStatus.className = "code-editor-patch-status";
      patchStatus.textContent = "No reviewed patch has been prepared.";
      aiderOutput.parentNode.insertBefore(patchStatus, aiderOutput);
    }
    return true;
  }

  function ensureCanonicalSurface(rootNode) {
    if (!rootNode) {
      return rootNode;
    }
    if (preserveExistingCodeStudioSurface(rootNode)) {
      return rootNode;
    }
    if (rootNode.querySelector && rootNode.querySelector('[data-code-editor-runtime-surface="dsl-native"]')) {
      return rootNode;
    }
    rootNode.innerHTML = [
      '<div class="mcel-code-editor-shell code-studio-shell" data-code-editor-runtime-surface="dsl-native" data-mc-component-id="code-editor.runtime.shell" data-mc-component-kind="workspace">',
        '<header class="mcel-code-editor-titlebar code-studio-titlebar">',
          '<div class="mcel-code-editor-brand">',
            '<strong>Code Editor</strong>',
            '<span id="code-editor-active-path">No file open</span>',
          '</div>',
          '<div class="mcel-code-editor-status-chips" aria-label="MCEL Code Editor status">',
            '<button id="code-editor-mcel-surface-status" type="button">MCEL Surface: checking</button>',
            '<button id="code-editor-mcel-authoring-status" type="button">MCEL Authoring: checking</button>',
            '<button id="code-editor-mcel-preview-status" type="button">Preview: checking</button>',
          '</div>',
        '</header>',
        '<main class="mcel-code-editor-workbench code-studio-body">',
          '<aside class="mcel-code-editor-sidebar code-studio-sidebar" data-code-editor-region="workspace">',
            '<label class="mcel-code-editor-field">Repository',
              '<input id="aider-repo" type="text" value="." autocomplete="off">',
            '</label>',
            '<div class="mcel-code-editor-search-row">',
              '<input id="file-map-search" type="search" placeholder="src, tests, file name">',
              '<button id="file-map-refresh" type="button">Load workspace</button>',
            '</div>',
            '<button id="file-map-apply" type="button" disabled>Open selected</button>',
            '<div id="file-map-status" class="mcel-code-editor-muted">Load workspace files.</div>',
            '<div id="code-editor-workspace-summary" class="mcel-code-editor-muted">. · 0 entries</div>',
            '<div id="file-map-list" class="mcel-code-editor-file-list file-map-list" role="listbox" aria-label="Workspace files"></div>',
            '<textarea id="aider-files" class="mcel-code-editor-selected-files" aria-label="Selected files for reviewed patch"></textarea>',
          '</aside>',
          '<section class="mcel-code-editor-editor code-studio-editor-group" data-mc-component-id="code-editor.region.editor-group" data-code-editor-region="editor">',
            '<div class="mcel-code-editor-editor-toolbar code-studio-pane-toolbar">',
              '<strong id="code-editor-active-title">No file open</strong>',
              '<span id="code-editor-draft-status">Open a source file from the workspace</span>',
            '</div>',
            '<div id="code-studio-runtime-monaco" class="mcel-code-editor-authoring-host code-studio-monaco-host" data-code-editor-runtime-host="true" data-code-editor-monaco-host="true" data-monaco-state="idle" aria-label="Monaco Code Editor surface"></div>',
            '<textarea id="code-studio-runtime-draft" spellcheck="false" data-code-studio-selected-file="true" data-code-editor-runtime-draft="true" data-code-editor-fallback-editor="textarea" aria-label="Code Editor source draft fallback" disabled></textarea>',
            '<textarea id="code-studio-source-editor" hidden aria-hidden="true"></textarea>',
            '<div id="code-editor-monaco-status" class="mcel-code-editor-muted">Monaco editor mounts here and remains visible even before a source file is opened.</div>',
            '<div class="mcel-code-editor-editor-actions">',
              '<button id="code-studio-commit-runtime" type="button" disabled>Save file</button>',
              '<button id="code-studio-save-live-workspace" type="button" disabled>Save</button>',
              '<button id="code-studio-restore-live-workspace" type="button" disabled>Discard draft</button>',
              '<button id="code-studio-clear-live-workspace" type="button" disabled>Close file</button>',
              '<button id="code-studio-mount-runtime" type="button">Refresh view</button>',
            '</div>',
          '</section>',
          '<aside class="mcel-code-editor-assistant code-studio-inspector" data-mc-component-id="code-editor.region.inspector" data-code-editor-region="assistant">',
            '<section class="mcel-code-editor-card">',
              '<h3>Reviewed patch lane</h3>',
              '<label class="mcel-code-editor-field">Instruction',
                '<textarea id="aider-instruction" placeholder="Describe the reviewed change to prepare."></textarea>',
              '</label>',
              '<label class="mcel-code-editor-review-check">',
                '<input id="aider-reviewed" type="checkbox">',
                '<span>I reviewed and approve the prepared patch handle.</span>',
              '</label>',
              '<div class="mcel-code-editor-patch-actions">',
                '<button id="aider-preview" type="button" disabled>Preview command</button>',
                '<button id="aider-run" type="button" disabled>Apply reviewed patch</button>',
              '</div>',
              '<div id="code-editor-patch-status" class="mcel-code-editor-muted">No reviewed patch has been prepared.</div>',
              '<output id="aider-output" class="mcel-code-editor-output" aria-live="polite">No Code Editor runtime receipts yet.</output>',
            '</section>',
            '<section id="code-studio-runtime-preview" class="mcel-code-editor-card" aria-label="Runtime receipts"></section>',
          '</aside>',
        '</main>',
        '<footer class="mcel-code-editor-statusbar code-studio-statusbar">',
          '<span>main</span>',
          '<span>DSL-native source editor</span>',
          '<span id="code-studio-runtime-state">runtime: mounted / no-draft</span>',
          '<span>UTF-8</span>',
          '<span>JavaScript</span>',
        '</footer>',
      '</div>'
    ].join("");
    if (rootNode.dataset) {
      rootNode.dataset.codeEditorRuntimeSurfaceMode = "runtime-fallback";
    }
    return rootNode;
  }

  return freeze({
    schema: "main-computer-code-editor-runtime-module-v1",
    createCodeEditorRuntime,
    ensureCanonicalSurface
  });
});
