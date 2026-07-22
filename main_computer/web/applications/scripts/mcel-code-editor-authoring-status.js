(() => {
  "use strict";

  const VERSION = "mcel-code-editor-authoring-status-v1";
  const DEFAULT_APP_ID = "code-editor";
  const STATUS_SELECTOR = "#code-editor-mcel-authoring-status";
  const REFRESH_INTERVAL_MS = 2000;

  function getGlobal() {
    return typeof window !== "undefined" ? window : globalThis;
  }

  function getDocument() {
    try {
      if (typeof document !== "undefined" && document?.querySelector) return document;
    } catch {}
    return null;
  }

  function authoredDocumentApi() {
    const global = getGlobal();
    return global.McelAuthoredSurfaceDocument || null;
  }

  function escapeText(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function readCurrentEditorText(options = {}) {
    if (typeof options.sourceText === "string") return options.sourceText;

    const global = getGlobal();
    const adapter = global.MainComputerMonacoAdapter || null;
    try {
      if (adapter && typeof adapter.getValue === "function") {
        const value = adapter.getValue();
        if (typeof value === "string") return value;
      }
    } catch {}

    const doc = options.document || getDocument();
    const candidates = [
      "#code-studio-runtime-draft",
      "#code-studio-source-editor",
      "textarea[data-code-studio-selected-file]",
      "textarea"
    ];

    for (const selector of candidates) {
      const element = doc?.querySelector?.(selector);
      if (typeof element?.value === "string") return element.value;
    }

    return "";
  }

  function normalizeReport(report) {
    if (!report || typeof report !== "object") {
      return {
        state: "pending",
        value: "pending",
        visible: false,
        title: "Authored MCEL surface status is pending.",
        report: null
      };
    }

    if (report.applicable === false || report.status === "not-applicable" || !report.containsSurfaceRidges) {
      return {
        state: "not-applicable",
        value: "no surface",
        visible: false,
        title: "The current document does not contain MCEL surface ridges.",
        report
      };
    }

    const errors = Number(report.counts?.errors || 0);
    const warnings = Number(report.counts?.warnings || 0);
    const pass = report.valid === true && report.surfaceIrBuildable && report.surfaceIrValid && report.layoutGrammarBuildable && report.layoutGrammarValid && errors === 0;
    const state = pass ? (warnings > 0 ? "warning" : "pass") : "fail";
    const value = state === "pass" ? "PASS" : state === "warning" ? "WARN" : "FAIL";
    const title = [
      `Authored MCEL Surface: ${value}`,
      `IR ${report.surfaceIrBuildable && report.surfaceIrValid ? "PASS" : "FAIL"}`,
      `Layout ${report.layoutGrammarBuildable && report.layoutGrammarValid ? "PASS" : "FAIL"}`,
      `Surfaces ${Number(report.surfaceCount || 0)}`,
      errors ? `${errors} error${errors === 1 ? "" : "s"}` : "",
      warnings ? `${warnings} warning${warnings === 1 ? "" : "s"}` : ""
    ].filter(Boolean).join(" · ");

    return {state, value, visible: true, title, report};
  }

  function analyzeCurrentDocument(options = {}) {
    const api = authoredDocumentApi();
    if (!api || typeof api.analyzeText !== "function") {
      return {
        contractVersion: VERSION,
        status: "unavailable",
        valid: false,
        applicable: false,
        containsSurfaceRidges: false,
        counts: {errors: 1, warnings: 0, ok: 0},
        diagnostics: [{
          code: "code-editor-authoring-status-missing-authored-document-api",
          severity: "error",
          finding: "McelAuthoredSurfaceDocument is required for authored surface status."
        }]
      };
    }

    const sourceText = readCurrentEditorText(options);
    return api.analyzeText(sourceText, options.analysisOptions || {});
  }

  function renderStatus(element, report) {
    if (!element) return null;

    const model = normalizeReport(report);
    element.__mcelAuthoringStatusReport = report || null;
    element.dataset.mcelAuthoringStatusState = model.state;
    element.setAttribute("title", model.title);
    element.setAttribute("aria-label", model.title);
    element.hidden = !model.visible;

    element.innerHTML = [
      '<span class="code-editor-mcel-authoring-status__label">Authored MCEL</span>',
      `<span class="code-editor-mcel-authoring-status__value">${escapeText(model.value)}</span>`
    ].join("");

    return model;
  }

  function refresh(options = {}) {
    const doc = options.document || getDocument();
    const element = options.element || doc?.querySelector?.(STATUS_SELECTOR) || null;
    if (!element) return null;

    const report = analyzeCurrentDocument(options);
    return renderStatus(element, report);
  }

  function mount(options = {}) {
    const doc = options.document || getDocument();
    const element = options.element || doc?.querySelector?.(STATUS_SELECTOR) || null;
    if (!element) return null;

    refresh({document: doc, element});

    const global = getGlobal();
    if (!options.noInterval && typeof global.setInterval === "function") {
      if (element.__mcelAuthoringStatusInterval && typeof global.clearInterval === "function") {
        global.clearInterval(element.__mcelAuthoringStatusInterval);
      }
      element.__mcelAuthoringStatusInterval = global.setInterval(() => {
        refresh({document: doc, element});
      }, Number(options.intervalMs || REFRESH_INTERVAL_MS));
    }

    const sourceEditor = doc?.querySelector?.("#code-studio-source-editor");
    sourceEditor?.addEventListener?.("input", () => refresh({document: doc, element}));

    return element;
  }

  function boot() {
    mount();
  }

  const api = Object.freeze({
    VERSION,
    STATUS_SELECTOR,
    REFRESH_INTERVAL_MS,
    readCurrentEditorText,
    analyzeCurrentDocument,
    normalizeReport,
    renderStatus,
    refresh,
    mount
  });

  const global = getGlobal();
  try {
    global.McelCodeEditorAuthoringStatus = api;
  } catch {}

  const doc = getDocument();
  if (doc?.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", boot, {once: true});
  } else if (doc) {
    boot();
  }
})();
