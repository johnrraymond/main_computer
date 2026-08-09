(function installMainComputerCodeEditorViewModel(root, factory) {
  "use strict";

  const api = factory(function requireCodeEditorCore() {
    if (root && root.MainComputerCodeEditorCore) {
      return root.MainComputerCodeEditorCore;
    }
    if (typeof require === "function") {
      return require("./code-editor-core.js");
    }
    throw new Error("Code Editor core is unavailable; load code-editor-core.js before code-editor-view-model.js");
  });
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.MainComputerCodeEditorViewModel = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function buildMainComputerCodeEditorViewModel(requireCodeEditorCore) {
  "use strict";

  function coreApi() {
    return requireCodeEditorCore();
  }

  function text(value) {
    return value == null ? "" : String(value);
  }

  function freeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.freeze(value);
    Object.keys(value).forEach((key) => freeze(value[key]));
    return value;
  }

  function activeFileTitle(state = {}) {
    const file = state.activeFile || {};
    return file.path ? file.path : "No file open";
  }

  function draftStatus(state = {}) {
    const draft = state.draft || {};
    if (!draft.path) return "no-draft";
    return draft.dirty ? "dirty" : "clean";
  }

  function buildWorkspaceViewModel(state = {}) {
    const workspace = state.workspace || {};
    const files = Array.isArray(workspace.files) ? workspace.files : [];
    return freeze({
      repoDir: text(state.repoDir || workspace.repoDir || "."),
      status: text(workspace.status || "idle"),
      fileCount: files.length,
      files: files.map((file) => ({
        path: text(file.path),
        name: text(file.name || file.path),
        kind: file.kind === "dir" ? "dir" : "file",
        label: `${file.kind === "dir" ? "📁" : "📄"} ${text(file.path)}`
      })),
      emptyMessage: files.length ? "" : "No files loaded yet.",
      statusText: files.length ? `Loaded ${files.length} workspace entries` : "Load workspace files"
    });
  }

  function buildActiveFileViewModel(state = {}) {
    const file = state.activeFile || {};
    const draft = state.draft || {};
    const open = !!file.path;
    return freeze({
      open,
      path: text(file.path),
      title: activeFileTitle(state),
      language: text(file.language || (file.path ? coreApi().inferLanguage(file.path) : "text")),
      text: text(Object.prototype.hasOwnProperty.call(draft, "text") ? draft.text : file.content),
      dirty: draft.dirty === true,
      status: draftStatus(state),
      sourceHash: text(file.sourceHash || draft.baseHash || ""),
      saveEnabled: open && draft.dirty === true,
      closeEnabled: open,
      statusText: open
        ? `${file.path}${draft.dirty ? " has unsaved changes" : " is clean"}`
        : "Open a source file from the workspace"
    });
  }

  function buildReceiptViewModel(state = {}) {
    const receipts = Array.isArray(state.receipts) ? state.receipts : [];
    const latest = receipts[0] || null;
    return freeze({
      latest,
      count: receipts.length,
      statusText: latest
        ? `${latest.intent}: ${latest.status}${latest.path ? ` (${latest.path})` : ""}`
        : "No Code Editor runtime receipts yet.",
      receipts: receipts.map((receipt) => ({
        intent: text(receipt.intent),
        status: text(receipt.status),
        ok: receipt.ok !== false,
        path: text(receipt.path),
        mutationAllowed: receipt.mutationAllowed === true,
        message: text(receipt.message),
        timestamp: text(receipt.timestamp)
      }))
    });
  }

  function buildPatchViewModel(state = {}) {
    const pending = state.pendingPatch || null;
    const files = pending && Array.isArray(pending.changedFiles) ? pending.changedFiles.map(text).filter(Boolean) : [];
    return freeze({
      present: !!pending,
      state: text(pending && pending.state || "none"),
      handle: text(pending && pending.handle),
      fileCount: files.length,
      files,
      reviewed: pending && pending.reviewed === true,
      approved: pending && pending.approved === true,
      statusText: pending
        ? `${text(pending.state || "prepared")} reviewed patch ${text(pending.handle || "")}`.trim()
        : "No reviewed patch has been prepared."
    });
  }


  function buildRuntimeViewModel(state = {}) {
    return freeze({
      schema: "main-computer-code-editor-view-model-v1",
      workspace: buildWorkspaceViewModel(state),
      activeFile: buildActiveFileViewModel(state),
      patch: buildPatchViewModel(state),
      receipts: buildReceiptViewModel(state),
      lastError: state.lastError || null
    });
  }

  function renderFileListHtml(state = {}) {
    const workspace = buildWorkspaceViewModel(state);
    if (!workspace.files.length) {
      return '<div class="file-map-empty">No directory loaded yet.</div>';
    }
    return workspace.files.map((file) => {
      const disabled = file.kind === "dir" ? " disabled" : "";
      const path = escapeHtml(file.path);
      return [
        '<label class="file-map-item" data-code-editor-runtime-file="', path, '">',
        '<input type="radio" name="code-editor-runtime-open-file" value="', path, '"', disabled, '>',
        '<span>', escapeHtml(file.label), '</span>',
        '</label>'
      ].join("");
    }).join("");
  }

  function renderRuntimePreviewHtml(state = {}) {
    const active = buildActiveFileViewModel(state);
    const receipts = buildReceiptViewModel(state);
    const patch = buildPatchViewModel(state);
    const latest = receipts.latest || {};
    const receiptRows = receipts.receipts.length
      ? receipts.receipts.slice(0, 6).map((receipt) => [
        '<li data-code-editor-receipt-ok="', receipt.ok ? "true" : "false", '">',
        '<strong>', escapeHtml(receipt.intent || "unknown"), '</strong>',
        '<span>', escapeHtml(receipt.status || ""), '</span>',
        receipt.path ? '<code>' + escapeHtml(receipt.path) + '</code>' : '',
        receipt.message ? '<small>' + escapeHtml(receipt.message) + '</small>' : '',
        '</li>'
      ].join("")).join("")
      : '<li>No runtime receipts yet.</li>';
    return [
      '<section class="mcel-code-editor-runtime-summary" data-code-editor-runtime="dsl-native">',
      '<div class="mcel-code-editor-runtime-line">',
      '<strong>Active file</strong>',
      '<span>', escapeHtml(active.title), '</span>',
      '</div>',
      '<div class="mcel-code-editor-runtime-line">',
      '<strong>Draft</strong>',
      '<span>', escapeHtml(active.statusText), '</span>',
      '</div>',
      '<div class="mcel-code-editor-runtime-line">',
      '<strong>Patch</strong>',
      '<span>', escapeHtml(patch.statusText), '</span>',
      '</div>',
      '<output id="code-editor-runtime-receipt" aria-live="polite">',
      escapeHtml(receipts.statusText),
      '</output>',
      '<ol class="mcel-code-editor-receipts">',
      receiptRows,
      '</ol>',
      latest.handle ? '<code class="mcel-code-editor-handle">' + escapeHtml(latest.handle) + '</code>' : '',
      '</section>'
    ].join("");
  }

  function escapeHtml(value) {
    return text(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  return freeze({
    schema: "main-computer-code-editor-view-model-v1",
    buildWorkspaceViewModel,
    buildActiveFileViewModel,
    buildReceiptViewModel,
    buildPatchViewModel,
    buildRuntimeViewModel,
    renderFileListHtml,
    renderRuntimePreviewHtml,
    escapeHtml
  });
});
