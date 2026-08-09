(function installMainComputerCodeEditorCapabilities(root) {
  "use strict";

  const DEFAULT_HEADERS = Object.freeze({"Content-Type": "application/json"});

  function requireFetch(fetcher) {
    const resolved = fetcher || (root && root.fetch);
    if (typeof resolved !== "function") {
      throw new Error("Code Editor capability transport is unavailable");
    }
    return resolved.bind(root);
  }

  async function requestJson(path, payload, options = {}) {
    const fetcher = requireFetch(options.fetcher);
    const response = await fetcher(path, {
      method: "POST",
      headers: DEFAULT_HEADERS,
      body: JSON.stringify(payload || {})
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      const detail = data.detail ? ` ${data.detail}` : "";
      throw new Error(`${data.error || `request returned ${response.status}`}${detail}`);
    }
    return data;
  }

  async function sha256Text(value) {
    const text = String(value == null ? "" : value);
    if (root && root.crypto && root.crypto.subtle && typeof root.TextEncoder === "function") {
      const digest = await root.crypto.subtle.digest("SHA-256", new root.TextEncoder().encode(text));
      return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
    }
    if (typeof require === "function") {
      const crypto = require("crypto");
      return crypto.createHash("sha256").update(text, "utf8").digest("hex");
    }
    throw new Error("SHA-256 support is unavailable");
  }

  async function inspectWorkspace(input = {}, options = {}) {
    const payload = {
      repo_dir: input.repoDir || input.repo_dir || ".",
      path: input.path || "",
      query: input.query || "",
      limit: input.limit || 500
    };
    const data = await requestJson("/api/applications/editor/files", payload, options);
    return {
      ok: true,
      repoDir: data.repo_dir || payload.repo_dir,
      path: data.path || payload.path || "",
      files: data.files || data.entries || [],
      raw: data
    };
  }

  async function openFile(input = {}, options = {}) {
    const path = input.path || input.filePath || input.selectedPath;
    const repoDir = input.repoDir || input.repo_dir || ".";
    const data = await requestJson("/api/applications/editor/read", {
      repo_dir: repoDir,
      files: path,
      instruction: input.instruction || "Open source file in Code Editor runtime."
    }, options);
    const files = Array.isArray(data.files) ? data.files : [];
    const first = files[0] || {};
    const content = String(first.content == null ? "" : first.content);
    return {
      ok: true,
      repoDir: data.repo_dir || repoDir,
      path: first.path || path,
      content,
      chars: first.chars || content.length,
      sourceHash: await sha256Text(content),
      raw: data
    };
  }

  async function saveFile(request = {}, options = {}) {
    const data = await requestJson("/api/applications/editor/project/file/save", request, options);
    return {
      ok: true,
      repoDir: data.repo_dir || request.repo_dir || ".",
      savedPath: data.savedPath || request.path,
      handle: data.handle || "",
      changedFiles: data.changedFiles || [data.savedPath || request.path],
      status: data.status || data.state || "pass",
      receipt: data.receipt || {},
      transaction: data.transaction || {},
      raw: data
    };
  }


  async function previewAiderPlan(request = {}, options = {}) {
    const data = await requestJson("/api/applications/editor/project/transaction/prepare", request, options);
    return {
      ok: true,
      repoDir: data.repo_dir || request.repo_dir || ".",
      handle: data.handle || "",
      status: data.status || data.state || "prepared",
      state: data.state || "prepared",
      transaction: data.transaction || {},
      changedFiles: data.changedFiles || [],
      raw: data
    };
  }

  async function applyReviewedPatch(request = {}, options = {}) {
    const data = await requestJson("/api/applications/editor/project/transaction/apply", request, options);
    return {
      ok: true,
      repoDir: data.repo_dir || request.repo_dir || ".",
      handle: data.handle || request.handle || "",
      status: data.status || data.state || "applied",
      state: data.state || "applied",
      transaction: data.transaction || {},
      receipt: data.receipt || {},
      changedFiles: data.changedFiles || [],
      raw: data
    };
  }

  const api = Object.freeze({
    schema: "main-computer-code-editor-capabilities-v1",
    requestJson,
    inspectWorkspace,
    openFile,
    saveFile,
    previewAiderPlan,
    applyReviewedPatch,
    sha256Text
  });

  if (root) {
    root.MainComputerCodeEditorCapabilities = api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
