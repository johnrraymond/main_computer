(() => {
  "use strict";

  const VERSION = "mcel-code-editor-ridge-inspector-v1";
  const DEFAULT_APP_ID = "code-editor";
  const TRIGGER_SELECTOR = "#code-editor-mcel-surface-status";
  const INSPECTOR_ID = "code-editor-mcel-ridge-inspector";
  const OPEN_ATTR = "data-mcel-ridge-inspector-open";

  function getGlobal() {
    return typeof window !== "undefined" ? window : globalThis;
  }

  function getDocument() {
    try {
      if (typeof document !== "undefined" && document?.querySelector) return document;
    } catch {}
    return null;
  }

  function flagOk(value) {
    return value === true || value === "true" || value === "pass" || value === "ok";
  }

  function statusApi() {
    const global = getGlobal();
    return global.McelCodeEditorSurfaceStatus || null;
  }

  function diagnosisApi() {
    const global = getGlobal();
    return global.McelSelfDiagnosis || global.MCEL || null;
  }

  function escapeText(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function pathwayFromReport(report) {
    const api = statusApi();
    if (api && typeof api.pathwayFromReport === "function") {
      return api.pathwayFromReport(report);
    }
    if (!report || typeof report !== "object") return null;
    return report.mcelSurfacePathway || report.summary?.mcelSurfacePathway || null;
  }

  function summarizePathway(pathway) {
    const api = statusApi();
    if (api && typeof api.summarizePathway === "function") {
      return api.summarizePathway(pathway);
    }
    const pass = pathway && flagOk(pathway.valid) && String(pathway.roundTripStatus || "").toLowerCase() === "pass";
    return {
      state: pass ? "pass" : "pending",
      value: pass ? "PASS" : "PENDING",
      label: "MCEL Surface",
      title: pass ? "MCEL Surface: PASS" : "MCEL surface pathway status is pending.",
      details: {
        semanticRidges: flagOk(pathway?.semanticRidgesPresent) ? "pass" : "pending",
        surfaceIR: flagOk(pathway?.surfaceIrBuildable) && flagOk(pathway?.surfaceIrValid) ? "pass" : "pending",
        layout: flagOk(pathway?.layoutGrammarPresent) && flagOk(pathway?.layoutGrammarValid) ? "pass" : "pending",
        extraction: flagOk(pathway?.extractable) ? "pass" : "pending",
        roundTrip: String(pathway?.roundTripStatus || "").toLowerCase() === "pass" ? "pass" : "pending"
      },
      raw: pathway || null
    };
  }

  function readReport(appId = DEFAULT_APP_ID, options = {}) {
    if (options.report) return options.report;

    const trigger = options.trigger || getDocument()?.querySelector?.(TRIGGER_SELECTOR) || null;
    if (trigger?.__mcelSurfaceStatusReport) return trigger.__mcelSurfaceStatusReport;

    const api = statusApi();
    try {
      if (api && typeof api.refresh === "function") {
        api.refresh(appId, {element: trigger || undefined});
        if (trigger?.__mcelSurfaceStatusReport) return trigger.__mcelSurfaceStatusReport;
      }
    } catch {}

    const diagnosis = diagnosisApi();
    try {
      if (diagnosis && typeof diagnosis.diagnose === "function") {
        return diagnosis.diagnose(appId, {silent: true});
      }
    } catch {}

    return null;
  }

  function checkStateClass(value) {
    const state = String(value || "pending").toLowerCase();
    if (state === "pass" || state === "ok") return "pass";
    if (state === "fail" || state === "error") return "fail";
    if (state === "warning" || state === "warn") return "warning";
    return "pending";
  }

  function checkLine(id, label, state, detail) {
    const safeState = checkStateClass(state);
    return [
      '<li class="code-editor-mcel-ridge-inspector__check" data-mcel-ridge-check="', escapeText(id), '" data-mcel-ridge-check-state="', safeState, '">',
      '<span class="code-editor-mcel-ridge-inspector__check-dot" aria-hidden="true"></span>',
      '<span class="code-editor-mcel-ridge-inspector__check-label">', escapeText(label), '</span>',
      '<strong class="code-editor-mcel-ridge-inspector__check-state">', escapeText(safeState.toUpperCase()), '</strong>',
      detail ? '<small>' + escapeText(detail) + '</small>' : '',
      '</li>'
    ].join("");
  }

  function buildInspectorModel(input = {}) {
    const report = input.report || null;
    const pathway = input.pathway || pathwayFromReport(report) || null;
    const summary = input.summary || summarizePathway(pathway);
    const primarySurface = report?.primarySurface || {};
    const ownership = primarySurface.ownership || {};
    const details = summary.details || {};

    return {
      version: VERSION,
      state: checkStateClass(summary.state),
      value: summary.value || "PENDING",
      title: summary.title || "MCEL surface pathway status",
      surfaceId: ownership.primarySurfaceId || primarySurface.expected || pathway?.surfaceId || "code-editor.surface.monaco-selected-file-editor",
      hostSelector: ownership.hostSelector || primarySurface.host?.selector || "#code-studio-runtime-monaco",
      editorSelector: ownership.editorSelector || primarySurface.editor?.selector || ".monaco-editor",
      renderer: pathway?.rendererId || pathway?.renderer || "code-editor.surface-diagnostics",
      projection: pathway?.projection || "diagnostic-html",
      roundTripStatus: pathway?.roundTripStatus || details.roundTrip || "pending",
      checks: [
        {id: "semantic-ridges", label: "Semantic ridges", state: details.semanticRidges || (flagOk(pathway?.semanticRidgesPresent) ? "pass" : "pending")},
        {id: "surface-ir", label: "SemanticSurfaceIR", state: details.surfaceIR || (flagOk(pathway?.surfaceIrBuildable) && flagOk(pathway?.surfaceIrValid) ? "pass" : "pending")},
        {id: "layout", label: "Shared layout grammar", state: details.layout || (flagOk(pathway?.layoutGrammarPresent) && flagOk(pathway?.layoutGrammarValid) ? "pass" : "pending")},
        {id: "extraction", label: "Surface extraction", state: details.extraction || (flagOk(pathway?.extractable) ? "pass" : "pending")},
        {id: "roundtrip", label: "Round-trip verification", state: details.roundTrip || (String(pathway?.roundTripStatus || "").toLowerCase() === "pass" ? "pass" : "pending")}
      ],
      counts: pathway?.counts || report?.counts || null,
      issues: Array.isArray(pathway?.issues) ? pathway.issues : Array.isArray(report?.issues) ? report.issues : []
    };
  }

  function renderInspectorContent(model) {
    const safe = model || buildInspectorModel();
    const issueCount = Array.isArray(safe.issues) ? safe.issues.length : 0;
    const checks = (safe.checks || []).map((check) => checkLine(check.id, check.label, check.state, check.detail)).join("");
    return [
      '<div class="code-editor-mcel-ridge-inspector__header">',
      '<div><span class="code-editor-mcel-ridge-inspector__eyebrow">MCEL ridge inspector</span>',
      '<strong>Surface pathway ', escapeText(safe.value || safe.state || "pending"), '</strong></div>',
      '<button type="button" class="code-editor-mcel-ridge-inspector__close" data-mcel-ridge-inspector-close aria-label="Close MCEL ridge inspector">×</button>',
      '</div>',
      '<dl class="code-editor-mcel-ridge-inspector__meta">',
      '<div><dt>Surface</dt><dd>', escapeText(safe.surfaceId), '</dd></div>',
      '<div><dt>Renderer</dt><dd>', escapeText(safe.renderer), '</dd></div>',
      '<div><dt>Projection</dt><dd>', escapeText(safe.projection), '</dd></div>',
      '<div><dt>Host</dt><dd>', escapeText(safe.hostSelector), '</dd></div>',
      '<div><dt>Editor</dt><dd>', escapeText(safe.editorSelector), '</dd></div>',
      '</dl>',
      '<ul class="code-editor-mcel-ridge-inspector__checks">',
      checks,
      '</ul>',
      '<p class="code-editor-mcel-ridge-inspector__footer" data-mcel-ridge-inspector-issue-count="', String(issueCount), '">',
      issueCount ? escapeText(`${issueCount} issue(s) available in the full diagnosis report.`) : 'No active MCEL surface-pathway issues.',
      '</p>'
    ].join("");
  }

  function ensureInspector(doc = getDocument()) {
    if (!doc?.querySelector) return null;
    let panel = doc.querySelector(`#${INSPECTOR_ID}`);
    if (panel) return panel;

    panel = doc.createElement("aside");
    panel.id = INSPECTOR_ID;
    panel.className = "code-editor-mcel-ridge-inspector";
    panel.hidden = true;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "false");
    panel.setAttribute("aria-label", "MCEL surface ridge inspector");
    panel.setAttribute("data-mcel-surface-id", "code-editor.surface.mcel-ridge-inspector");
    panel.setAttribute("data-mcel-surface-role", "supporting-diagnostic-surface");
    panel.setAttribute("data-mcel-node-id", "code-editor.node.mcel-ridge-inspector");
    panel.setAttribute("data-mcel-node-type", "ridge_inspector");
    panel.setAttribute("data-mcel-region", "code-editor.region.titlebar");
    panel.setAttribute("data-mcel-home-region", "code-editor.region.titlebar");
    panel.setAttribute("data-mcel-actual-region", "code-editor.region.titlebar");
    panel.setAttribute("data-mcel-source", "mcel-code-editor-ridge-inspector.js");
    panel.setAttribute("data-mcel-provenance", "patch:mcel-safe-11-ridge-inspector");

    const host = doc.querySelector(".code-studio-title-actions") || doc.querySelector(".code-studio-titlebar") || doc.body;
    host?.appendChild?.(panel);
    return panel;
  }

  function setOpen(trigger, panel, open) {
    if (!trigger || !panel) return false;
    const state = !!open;
    panel.hidden = !state;
    panel.setAttribute("aria-hidden", String(!state));
    trigger.setAttribute("aria-expanded", String(state));
    trigger.setAttribute(OPEN_ATTR, String(state));
    return state;
  }

  function refreshInspector(options = {}) {
    const doc = options.document || getDocument();
    const trigger = options.trigger || doc?.querySelector?.(TRIGGER_SELECTOR) || null;
    const panel = options.panel || ensureInspector(doc);
    if (!trigger || !panel) return null;
    const report = readReport(DEFAULT_APP_ID, {trigger, report: options.report});
    const model = buildInspectorModel({report});
    panel.setAttribute("data-mcel-ridge-inspector-state", model.state);
    panel.innerHTML = renderInspectorContent(model);
    return model;
  }

  function openInspector(options = {}) {
    const doc = options.document || getDocument();
    const trigger = options.trigger || doc?.querySelector?.(TRIGGER_SELECTOR) || null;
    const panel = options.panel || ensureInspector(doc);
    if (!trigger || !panel) return null;
    const model = refreshInspector({document: doc, trigger, panel, report: options.report});
    setOpen(trigger, panel, true);
    return model;
  }

  function closeInspector(options = {}) {
    const doc = options.document || getDocument();
    const trigger = options.trigger || doc?.querySelector?.(TRIGGER_SELECTOR) || null;
    const panel = options.panel || doc?.querySelector?.(`#${INSPECTOR_ID}`) || null;
    if (!trigger || !panel) return false;
    return !setOpen(trigger, panel, false);
  }

  function toggleInspector(options = {}) {
    const doc = options.document || getDocument();
    const trigger = options.trigger || doc?.querySelector?.(TRIGGER_SELECTOR) || null;
    const panel = options.panel || ensureInspector(doc);
    if (!trigger || !panel) return null;
    const isOpen = trigger.getAttribute("aria-expanded") === "true" && !panel.hidden;
    return isOpen ? (closeInspector({document: doc, trigger, panel}), null) : openInspector({document: doc, trigger, panel, report: options.report});
  }

  function mount(options = {}) {
    const doc = options.document || getDocument();
    const trigger = options.trigger || doc?.querySelector?.(TRIGGER_SELECTOR) || null;
    if (!doc || !trigger) return null;
    const panel = ensureInspector(doc);

    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-controls", INSPECTOR_ID);
    trigger.setAttribute("data-mcel-ridge-inspector-trigger", "code-editor");
    if (!trigger.hasAttribute("tabindex")) trigger.setAttribute("tabindex", "0");

    if (trigger.__mcelRidgeInspectorMounted) return {trigger, panel};
    trigger.__mcelRidgeInspectorMounted = true;

    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      toggleInspector({document: doc, trigger, panel});
    });

    trigger.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleInspector({document: doc, trigger, panel});
      }
      if (event.key === "Escape") {
        closeInspector({document: doc, trigger, panel});
      }
    });

    doc.addEventListener("click", (event) => {
      const target = event.target;
      if (!panel.hidden && target !== trigger && !trigger.contains?.(target) && !panel.contains?.(target)) {
        closeInspector({document: doc, trigger, panel});
      }
    });

    panel?.addEventListener?.("click", (event) => {
      const target = event.target;
      if (target?.matches?.("[data-mcel-ridge-inspector-close]")) {
        event.preventDefault();
        closeInspector({document: doc, trigger, panel});
        trigger.focus?.();
      }
    });

    return {trigger, panel};
  }

  function boot() {
    mount();
  }

  const api = Object.freeze({
    VERSION,
    TRIGGER_SELECTOR,
    INSPECTOR_ID,
    buildInspectorModel,
    renderInspectorContent,
    refreshInspector,
    openInspector,
    closeInspector,
    toggleInspector,
    mount,
    _private: Object.freeze({
      flagOk,
      checkStateClass,
      escapeText,
      checkLine,
      pathwayFromReport,
      summarizePathway
    })
  });

  const global = getGlobal();
  try {
    global.McelCodeEditorRidgeInspector = api;
  } catch {}

  const doc = getDocument();
  if (doc?.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", boot, {once: true});
  } else if (doc) {
    boot();
  }
})();
