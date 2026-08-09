(function installMainComputerCodeEditorCore(root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.MainComputerCodeEditorCore = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function buildMainComputerCodeEditorCore() {
  "use strict";

  const SCHEMA = "main-computer-code-editor-core-v1";
  const RECEIPT_SCHEMA = "mcel-code-editor-runtime-receipt-v1";
  const DEFAULT_REPO_DIR = ".";

  class CodeEditorPathError extends Error {
    constructor(code, message, path) {
      super(message);
      this.name = "CodeEditorPathError";
      this.code = code;
      this.path = path == null ? "" : String(path);
    }
  }

  function text(value) {
    return value == null ? "" : String(value);
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value == null ? null : value));
  }

  function freeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.freeze(value);
    Object.keys(value).forEach((key) => freeze(value[key]));
    return value;
  }

  function normalizeRepoDir(value) {
    const raw = text(value).trim().replace(/\\/g, "/");
    if (!raw || raw === ".") return DEFAULT_REPO_DIR;
    if (/^[a-zA-Z]:\//.test(raw) || raw.startsWith("/") || raw.includes("\0")) {
      throw new CodeEditorPathError("repo-dir-outside-workspace", "Repository directory must stay inside the workspace.", raw);
    }
    const parts = raw.split("/").filter((part) => part && part !== ".");
    if (parts.some((part) => part === "..")) {
      throw new CodeEditorPathError("repo-dir-outside-workspace", "Repository directory cannot contain parent traversal.", raw);
    }
    return parts.join("/") || DEFAULT_REPO_DIR;
  }

  function normalizeProjectPath(value) {
    const raw = text(value).trim().replace(/\\/g, "/");
    if (!raw) {
      throw new CodeEditorPathError("path-required", "A project-relative file path is required.", raw);
    }
    if (raw.includes("\0") || /^[a-zA-Z]:\//.test(raw) || raw.startsWith("/") || /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) {
      throw new CodeEditorPathError("path-outside-workspace", "File path must be project-relative.", raw);
    }
    const parts = raw.split("/").filter((part) => part && part !== ".");
    if (!parts.length) {
      throw new CodeEditorPathError("path-required", "A project-relative file path is required.", raw);
    }
    if (parts.some((part) => part === "..")) {
      throw new CodeEditorPathError("path-outside-workspace", "File path cannot contain parent traversal.", raw);
    }
    return parts.join("/");
  }

  function normalizeFileEntries(items) {
    const entries = Array.isArray(items) ? items : [];
    return freeze(entries.map((entry) => {
      const item = entry && typeof entry === "object" ? entry : {};
      const path = item.path ? normalizeProjectPath(item.path) : "";
      return {
        path,
        name: text(item.name || path.split("/").pop()),
        kind: item.kind === "dir" ? "dir" : "file",
        bytes: Number.isFinite(Number(item.bytes)) ? Number(item.bytes) : 0,
        depth: Number.isFinite(Number(item.depth)) ? Number(item.depth) : Math.max(0, path.split("/").length - 1),
        hasChildren: item.has_children === true || item.hasChildren === true
      };
    }).filter((entry) => entry.path));
  }

  function initialState(seed = {}) {
    const repoDir = normalizeRepoDir(seed.repoDir || seed.repo_dir || DEFAULT_REPO_DIR);
    return freeze({
      schema: SCHEMA,
      appId: "code-editor",
      repoDir,
      workspace: {
        repoDir,
        path: "",
        files: [],
        inspectedAt: "",
        status: "idle"
      },
      activeFile: null,
      draft: null,
      pendingPatch: null,
      receipts: [],
      lastError: null
    });
  }

  function withReceipt(state, receipt) {
    const receipts = [normalizeReceipt(receipt)].concat(Array.isArray(state.receipts) ? state.receipts : []);
    return receipts.slice(0, 40);
  }

  function normalizeReceipt(input) {
    const source = input && typeof input === "object" ? input : {};
    const ok = source.ok !== false;
    return freeze({
      schema: RECEIPT_SCHEMA,
      ok,
      status: text(source.status || (ok ? "pass" : "fail")),
      intent: text(source.intent || "unknown"),
      path: text(source.path || source.savedPath || ""),
      repoDir: text(source.repoDir || source.repo_dir || DEFAULT_REPO_DIR),
      mutationAllowed: source.mutationAllowed === true,
      changedFiles: Array.isArray(source.changedFiles) ? source.changedFiles.map(text) : [],
      message: text(source.message || source.error || ""),
      handle: text(source.handle || ""),
      timestamp: text(source.timestamp || new Date().toISOString()),
      raw: source.raw && typeof source.raw === "object" ? clone(source.raw) : {}
    });
  }

  function recordFailure(state, intent, error) {
    const current = state && typeof state === "object" ? state : initialState();
    const receipt = normalizeReceipt({
      ok: false,
      status: "fail",
      intent,
      repoDir: current.repoDir,
      path: current.activeFile && current.activeFile.path,
      message: error && error.message ? error.message : text(error)
    });
    return freeze(Object.assign({}, current, {
      lastError: {
        code: error && error.code ? error.code : "runtime-error",
        message: receipt.message
      },
      receipts: withReceipt(current, receipt)
    }));
  }

  function applyWorkspaceInspection(state, payload = {}) {
    const current = state && typeof state === "object" ? state : initialState();
    const repoDir = normalizeRepoDir(payload.repoDir || payload.repo_dir || current.repoDir);
    const files = normalizeFileEntries(payload.files || payload.entries || []);
    const receipt = normalizeReceipt({
      ok: true,
      intent: "inspectWorkspace",
      repoDir,
      status: "pass",
      message: `inspected ${files.length} workspace entries`
    });
    return freeze(Object.assign({}, current, {
      repoDir,
      workspace: {
        repoDir,
        path: text(payload.path || ""),
        files,
        inspectedAt: text(payload.inspectedAt || new Date().toISOString()),
        status: "ready"
      },
      lastError: null,
      receipts: withReceipt(current, receipt)
    }));
  }

  function applyOpenedFile(state, payload = {}) {
    const current = state && typeof state === "object" ? state : initialState();
    const path = normalizeProjectPath(payload.path);
    const content = text(payload.content);
    const sourceHash = text(payload.sourceHash || payload.source_hash || "");
    const language = text(payload.language || inferLanguage(path));
    const activeFile = freeze({
      path,
      content,
      language,
      sourceHash,
      openedAt: text(payload.openedAt || new Date().toISOString())
    });
    const draft = freeze({
      path,
      text: content,
      dirty: false,
      baseHash: sourceHash,
      updatedAt: activeFile.openedAt
    });
    const receipt = normalizeReceipt({
      ok: true,
      intent: "openFile",
      repoDir: current.repoDir,
      path,
      status: "pass",
      message: `opened ${path}`
    });
    return freeze(Object.assign({}, current, {
      activeFile,
      draft,
      lastError: null,
      receipts: withReceipt(current, receipt)
    }));
  }

  function applyDraftEdit(state, input = {}) {
    const current = state && typeof state === "object" ? state : initialState();
    const activePath = current.activeFile && current.activeFile.path;
    const path = normalizeProjectPath(input.path || activePath);
    if (activePath && path !== activePath) {
      throw new CodeEditorPathError("draft-path-mismatch", "Draft edits must target the active file.", path);
    }
    const textValue = text(Object.prototype.hasOwnProperty.call(input, "text") ? input.text : input.draftText);
    const baseHash = text(input.baseHash || input.base_hash || (current.draft && current.draft.baseHash) || (current.activeFile && current.activeFile.sourceHash) || "");
    const previousText = current.activeFile && current.activeFile.path === path ? current.activeFile.content : "";
    const draft = freeze({
      path,
      text: textValue,
      dirty: textValue !== previousText,
      baseHash,
      updatedAt: text(input.updatedAt || new Date().toISOString())
    });
    const receipt = normalizeReceipt({
      ok: true,
      intent: "editDraft",
      repoDir: current.repoDir,
      path,
      status: "pass",
      message: draft.dirty ? `draft updated for ${path}` : `draft clean for ${path}`
    });
    return freeze(Object.assign({}, current, {
      draft,
      lastError: null,
      receipts: withReceipt(current, receipt)
    }));
  }


  function normalizePatchOperation(value) {
    const operation = text(value || "modify").trim().toLowerCase();
    if (operation !== "modify" && operation !== "create") {
      throw new CodeEditorPathError("unsupported-patch-operation", "Reviewed patch changes support modify and create only.", operation);
    }
    return operation;
  }

  function assertSha256(value, code, message, path) {
    const digest = text(value).trim().toLowerCase();
    if (!/^[a-f0-9]{64}$/.test(digest)) {
      throw new CodeEditorPathError(code, message, path);
    }
    return digest;
  }

  function patchContainer(input = {}) {
    const source = input && typeof input === "object" ? input : {};
    const patch = source.reviewedPatch || source.reviewed_patch || source.patchArtifact || source.patch_artifact || {};
    return patch && typeof patch === "object" ? patch : {};
  }

  function rawPatchChanges(input = {}) {
    const source = input && typeof input === "object" ? input : {};
    const patch = patchContainer(source);
    return source.changes ||
      source.replacementFiles ||
      source.replacement_files ||
      patch.changes ||
      patch.replacementFiles ||
      patch.replacement_files ||
      [];
  }

  function dirtyDraftChange(state, input = {}) {
    const current = state && typeof state === "object" ? state : initialState();
    const draft = current.draft || {};
    const active = current.activeFile || {};
    if (!draft.path || draft.dirty !== true) return null;
    return {
      operation: "modify",
      path: draft.path,
      expected_before_sha256: text(input.expectedBeforeSha256 || input.expected_before_sha256 || draft.baseHash || active.sourceHash),
      replacement_text: draft.text
    };
  }

  function normalizePatchChanges(rawChanges, options = {}) {
    const source = Array.isArray(rawChanges) ? rawChanges : [];
    if (!source.length) {
      throw new CodeEditorPathError(
        "reviewed-patch-changes-required",
        "Patch preview requires at least one replacement-file change.",
        ""
      );
    }
    return freeze(source.map((raw) => {
      const item = raw && typeof raw === "object" ? raw : {};
      const operation = normalizePatchOperation(item.operation);
      const path = normalizeProjectPath(item.path || item.filePath || item.selectedPath);
      const replacementText = Object.prototype.hasOwnProperty.call(item, "replacement_text")
        ? item.replacement_text
        : Object.prototype.hasOwnProperty.call(item, "replacementText")
          ? item.replacementText
          : item.text;
      if (typeof replacementText !== "string") {
        throw new CodeEditorPathError("replacement-text-required", "Each patch change requires UTF-8 replacement text.", path);
      }
      const normalized = {
        operation,
        path,
        replacement_text: replacementText
      };
      const expected = item.expected_before_sha256 == null ? item.expectedBeforeSha256 : item.expected_before_sha256;
      if (operation === "modify" || expected != null || options.requireExpectedHash === true) {
        normalized.expected_before_sha256 = assertSha256(
          expected,
          "source-hash-required",
          "Modify patch changes require a 64-character source hash from a freshly opened file.",
          path
        );
      }
      const replacementSha = item.replacement_sha256 == null ? item.replacementSha256 : item.replacement_sha256;
      if (replacementSha != null && text(replacementSha)) {
        normalized.replacement_sha256 = assertSha256(
          replacementSha,
          "replacement-hash-invalid",
          "Replacement hash must be a 64-character SHA-256 digest.",
          path
        );
      }
      return normalized;
    }));
  }

  function patchChangedFiles(payload = {}) {
    if (Array.isArray(payload.changedFiles)) return payload.changedFiles.map(text).filter(Boolean);
    if (Array.isArray(payload.changed_files)) return payload.changed_files.map(text).filter(Boolean);
    const transaction = payload.transaction && typeof payload.transaction === "object" ? payload.transaction : {};
    const changes = Array.isArray(transaction.changes) ? transaction.changes : [];
    return changes.map((item) => {
      const path = item && typeof item === "object" ? item.path : "";
      return text(path);
    }).filter(Boolean);
  }

  function transactionHandle(payload = {}) {
    const patch = patchContainer(payload);
    return text(
      payload.handle ||
      payload.transactionHandle ||
      payload.transaction_handle ||
      patch.handle ||
      patch.transactionHandle ||
      patch.transaction_handle ||
      ""
    ).trim().toLowerCase();
  }

  function validateTransactionHandle(raw) {
    const handle = text(raw).trim().toLowerCase();
    if (!/^[a-f0-9]{32}$/.test(handle)) {
      throw new CodeEditorPathError(
        "reviewed-patch-handle-required",
        "Reviewed patch apply requires a server-issued transaction handle.",
        handle
      );
    }
    return handle;
  }

  function buildPatchPreviewRequest(state, input = {}) {
    const current = state && typeof state === "object" ? state : initialState();
    let changes = rawPatchChanges(input);
    if (!Array.isArray(changes) || !changes.length) {
      const fallback = dirtyDraftChange(current, input);
      changes = fallback ? [fallback] : [];
    }
    return freeze({
      repo_dir: normalizeRepoDir(input.repoDir || input.repo_dir || current.repoDir),
      project_root: text(input.projectRoot || input.project_root || ".") || ".",
      changes: normalizePatchChanges(changes),
      validation_profile: text(input.validationProfile || input.validation_profile || "none") || "none",
      preview_only: true,
      mutation_allowed: false
    });
  }

  function applyPatchPreview(state, payload = {}) {
    const current = state && typeof state === "object" ? state : initialState();
    const handle = validateTransactionHandle(payload.handle);
    const transaction = payload.transaction && typeof payload.transaction === "object" ? clone(payload.transaction) : {};
    const changedFiles = patchChangedFiles(payload);
    const projectRoot = text(transaction.project_root || transaction.projectRoot || payload.project_root || payload.projectRoot || ".");
    const pendingPatch = freeze({
      state: "prepared",
      handle,
      projectRoot,
      changedFiles,
      transaction,
      reviewed: false,
      approved: false,
      preparedAt: text(payload.preparedAt || new Date().toISOString())
    });
    const receipt = normalizeReceipt({
      ok: true,
      status: payload.status || "prepared",
      intent: "previewAiderPlan",
      repoDir: current.repoDir,
      mutationAllowed: false,
      changedFiles,
      handle,
      message: `prepared reviewed patch preview for ${changedFiles.length || "requested"} file(s)`,
      raw: payload.raw || payload
    });
    return freeze(Object.assign({}, current, {
      pendingPatch,
      lastError: null,
      receipts: withReceipt(current, receipt)
    }));
  }

  function buildReviewedPatchApplyRequest(state, input = {}) {
    const current = state && typeof state === "object" ? state : initialState();
    const pending = current.pendingPatch || {};
    const handle = validateTransactionHandle(transactionHandle(input) || pending.handle);
    const reviewed = input.reviewed === true || input.reviewedEvidence === true || input.reviewed_evidence === true;
    const approved = input.approved === true || input.confirmed === true;
    if (!reviewed || !approved) {
      const error = new CodeEditorPathError(
        "reviewed-patch-approval-required",
        "Reviewed patch apply requires reviewed=true and approved=true or confirmed=true.",
        handle
      );
      throw error;
    }
    return freeze({
      repo_dir: normalizeRepoDir(input.repoDir || input.repo_dir || current.repoDir),
      handle,
      reviewed: true,
      approved: input.approved === true,
      confirmed: input.confirmed === true,
      require_project_manifest: input.requireProjectManifest === true || input.require_project_manifest === true
    });
  }

  function applyReviewedPatchResult(state, payload = {}) {
    const current = state && typeof state === "object" ? state : initialState();
    const handle = validateTransactionHandle(payload.handle || (current.pendingPatch && current.pendingPatch.handle));
    const changedFiles = patchChangedFiles(payload);
    const receipt = normalizeReceipt({
      ok: true,
      status: payload.status || payload.state || "applied",
      intent: "applyReviewedPatch",
      repoDir: current.repoDir,
      mutationAllowed: true,
      changedFiles,
      handle,
      message: `applied reviewed patch to ${changedFiles.length || "approved"} file(s)`,
      raw: payload.raw || payload
    });
    const pendingPatch = freeze(Object.assign({}, current.pendingPatch || {}, {
      state: "applied",
      handle,
      changedFiles,
      reviewed: true,
      approved: true,
      appliedAt: text(payload.appliedAt || new Date().toISOString()),
      receipt: payload.receipt && typeof payload.receipt === "object" ? clone(payload.receipt) : {}
    }));
    return freeze(Object.assign({}, current, {
      pendingPatch,
      lastError: null,
      receipts: withReceipt(current, receipt)
    }));
  }

  function applySavedFile(state, payload = {}) {
    const current = state && typeof state === "object" ? state : initialState();
    const savedPath = normalizeProjectPath(payload.savedPath || payload.path || (current.draft && current.draft.path));
    const savedText = text(Object.prototype.hasOwnProperty.call(payload, "text") ? payload.text : (current.draft && current.draft.text));
    const sourceHash = text(payload.sourceHash || payload.source_hash || (current.draft && current.draft.baseHash) || "");
    const activeFile = freeze({
      path: savedPath,
      content: savedText,
      language: inferLanguage(savedPath),
      sourceHash,
      openedAt: current.activeFile && current.activeFile.openedAt || "",
      savedAt: text(payload.savedAt || new Date().toISOString())
    });
    const draft = freeze({
      path: savedPath,
      text: savedText,
      dirty: false,
      baseHash: sourceHash,
      updatedAt: activeFile.savedAt
    });
    const receipt = normalizeReceipt({
      ok: true,
      intent: "saveFile",
      repoDir: current.repoDir,
      path: savedPath,
      status: payload.status || "pass",
      mutationAllowed: true,
      changedFiles: payload.changedFiles || [savedPath],
      handle: payload.handle || "",
      message: `saved ${savedPath}`,
      raw: payload.raw || {}
    });
    return freeze(Object.assign({}, current, {
      activeFile,
      draft,
      lastError: null,
      receipts: withReceipt(current, receipt)
    }));
  }

  function discardDraft(state) {
    const current = state && typeof state === "object" ? state : initialState();
    if (!current.activeFile) return current;
    const draft = freeze({
      path: current.activeFile.path,
      text: current.activeFile.content,
      dirty: false,
      baseHash: current.activeFile.sourceHash,
      updatedAt: new Date().toISOString()
    });
    return freeze(Object.assign({}, current, {
      draft,
      lastError: null,
      receipts: withReceipt(current, normalizeReceipt({
        ok: true,
        intent: "discardDraft",
        repoDir: current.repoDir,
        path: current.activeFile.path,
        status: "pass",
        message: `discarded draft for ${current.activeFile.path}`
      }))
    }));
  }

  function closeFile(state) {
    const current = state && typeof state === "object" ? state : initialState();
    const path = current.activeFile && current.activeFile.path || "";
    return freeze(Object.assign({}, current, {
      activeFile: null,
      draft: null,
      lastError: null,
      receipts: withReceipt(current, normalizeReceipt({
        ok: true,
        intent: "closeFile",
        repoDir: current.repoDir,
        path,
        status: "pass",
        message: path ? `closed ${path}` : "closed active file"
      }))
    }));
  }

  function buildSaveRequest(state, input = {}) {
    const current = state && typeof state === "object" ? state : initialState();
    const draft = current.draft || {};
    const active = current.activeFile || {};
    const path = normalizeProjectPath(input.path || draft.path || active.path);
    const replacementText = text(Object.prototype.hasOwnProperty.call(input, "text") ? input.text : draft.text);
    const expectedBeforeSha256 = text(
      input.expectedBeforeSha256 ||
      input.expected_before_sha256 ||
      draft.baseHash ||
      active.sourceHash
    );
    if (!/^[a-f0-9]{64}$/i.test(expectedBeforeSha256)) {
      throw new CodeEditorPathError(
        "stale-source-hash-required",
        "File save requires a 64-character source hash from a freshly opened file.",
        path
      );
    }
    return freeze({
      repo_dir: normalizeRepoDir(input.repoDir || input.repo_dir || current.repoDir),
      project_root: text(input.projectRoot || input.project_root || ".") || ".",
      path,
      replacement_text: replacementText,
      expected_before_sha256: expectedBeforeSha256.toLowerCase(),
      explicit_save: true,
      stale_source_checked: true,
      write_policy: "explicit-save",
      validation_profile: text(input.validationProfile || input.validation_profile || "none") || "none"
    });
  }

  function inferLanguage(path) {
    const lower = text(path).toLowerCase();
    if (lower.endsWith(".py")) return "python";
    if (lower.endsWith(".js") || lower.endsWith(".mjs") || lower.endsWith(".cjs")) return "javascript";
    if (lower.endsWith(".ts")) return "typescript";
    if (lower.endsWith(".json")) return "json";
    if (lower.endsWith(".html")) return "html";
    if (lower.endsWith(".css")) return "css";
    if (lower.endsWith(".md")) return "markdown";
    return "text";
  }

  return freeze({
    schema: SCHEMA,
    receiptSchema: RECEIPT_SCHEMA,
    CodeEditorPathError,
    normalizeRepoDir,
    normalizeProjectPath,
    normalizeFileEntries,
    initialState,
    applyWorkspaceInspection,
    applyOpenedFile,
    applyDraftEdit,
    applySavedFile,
    discardDraft,
    closeFile,
    buildSaveRequest,
    normalizePatchChanges,
    buildPatchPreviewRequest,
    applyPatchPreview,
    buildReviewedPatchApplyRequest,
    applyReviewedPatchResult,
    recordFailure,
    normalizeReceipt,
    inferLanguage
  });
});
