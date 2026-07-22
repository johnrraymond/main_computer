(() => {
  "use strict";

  const VERSION = "mcel-code-editor-surface-status-v1";
  const DEFAULT_APP_ID = "code-editor";
  const STATUS_SELECTOR = "#code-editor-mcel-surface-status";
  const REFRESH_INTERVAL_MS = 30000;

  function getGlobal() {
    return typeof window !== "undefined" ? window : globalThis;
  }

  function getDocument() {
    try {
      if (typeof document !== "undefined" && document?.querySelector) return document;
    } catch {}
    return null;
  }

  function normalizeState(value) {
    const state = String(value || "").toLowerCase();
    if (state === "pass" || state === "ok") return "pass";
    if (state === "fail" || state === "error") return "fail";
    if (state === "warning" || state === "warn") return "warning";
    if (state === "unavailable") return "unavailable";
    if (state === "pending") return "pending";
    return "unknown";
  }

  function flagOk(value) {
    return value === true || value === "true" || value === "pass" || value === "ok";
  }

  function pathwayFromReport(report) {
    if (!report || typeof report !== "object") return null;
    return report.mcelSurfacePathway || report.summary?.mcelSurfacePathway || null;
  }

  function boolWord(value) {
    return flagOk(value) ? "PASS" : "FAIL";
  }

  function summarizePathway(pathway) {
    if (!pathway || typeof pathway !== "object") {
      return {
        state: "pending",
        value: "pending",
        label: "MCEL Surface",
        title: "MCEL surface pathway status is pending; no diagnosis report has been read yet.",
        details: {
          semanticRidges: "pending",
          surfaceIR: "pending",
          layout: "pending",
          extraction: "pending",
          roundTrip: "pending"
        }
      };
    }

    const explicitStatus = normalizeState(pathway.status || pathway.roundTripStatus || pathway.verdict);
    const semanticRidges = flagOk(pathway.semanticRidgesPresent);
    const surfaceIR = flagOk(pathway.surfaceIrBuildable) && flagOk(pathway.surfaceIrValid);
    const layout = flagOk(pathway.layoutGrammarPresent) && flagOk(pathway.layoutGrammarValid);
    const extraction = flagOk(pathway.extractable);
    const roundTrip = String(pathway.roundTripStatus || "").toLowerCase() === "pass";
    const valid = pathway.valid === true || (semanticRidges && surfaceIR && layout && extraction && roundTrip);
    const hasErrorCount = Number(pathway.counts?.errors || 0) > 0;
    const hasWarningCount = Number(pathway.counts?.warnings || 0) > 0;
    const state = valid && explicitStatus !== "fail" && !hasErrorCount
      ? hasWarningCount ? "warning" : "pass"
      : explicitStatus === "unavailable" ? "unavailable" : explicitStatus === "pending" ? "pending" : "fail";

    const value = state === "pass" ? "PASS" :
      state === "warning" ? "WARN" :
      state === "unavailable" ? "UNAVAILABLE" :
      state === "pending" ? "PENDING" :
      "FAIL";

    const title = [
      `MCEL Surface: ${value}`,
      `Ridges ${boolWord(semanticRidges)}`,
      `Surface IR ${boolWord(surfaceIR)}`,
      `Layout ${boolWord(layout)}`,
      `Extract ${boolWord(extraction)}`,
      `Round-trip ${roundTrip ? "PASS" : "FAIL"}`
    ].join(" · ");

    return {
      state,
      value,
      label: "MCEL Surface",
      title,
      details: {
        semanticRidges: semanticRidges ? "pass" : "fail",
        surfaceIR: surfaceIR ? "pass" : "fail",
        layout: layout ? "pass" : "fail",
        extraction: extraction ? "pass" : "fail",
        roundTrip: roundTrip ? "pass" : "fail"
      },
      raw: pathway
    };
  }

  function escapeText(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderStatus(el, summary) {
    if (!el) return null;
    const safeSummary = summary || summarizePathway(null);
    const state = normalizeState(safeSummary.state);
    try {
      el.dataset.mcelSurfaceStatusState = state;
      el.setAttribute("data-mcel-surface-status-state", state);
      el.setAttribute("title", safeSummary.title || "");
      el.setAttribute("aria-label", safeSummary.title || `MCEL Surface: ${safeSummary.value || "pending"}`);
      el.innerHTML = [
        '<span class="code-editor-mcel-surface-status__label">',
        escapeText(safeSummary.label || "MCEL Surface"),
        "</span>",
        '<span class="code-editor-mcel-surface-status__value">',
        escapeText(safeSummary.value || "pending"),
        "</span>"
      ].join("");
    } catch {}
    return safeSummary;
  }

  function diagnose(appId = DEFAULT_APP_ID) {
    const global = getGlobal();
    try {
      if (global.McelSelfDiagnosis && typeof global.McelSelfDiagnosis.diagnose === "function") {
        return global.McelSelfDiagnosis.diagnose(appId, {silent: true});
      }
    } catch {}

    try {
      if (global.MCEL && typeof global.MCEL.diagnose === "function") {
        return global.MCEL.diagnose(appId, {silent: true});
      }
    } catch {}

    try {
      if (global.MCELDiagnosticsCounterWidget && typeof global.MCELDiagnosticsCounterWidget.getStatus === "function") {
        return global.MCELDiagnosticsCounterWidget.getStatus(appId)?.report || null;
      }
    } catch {}

    return null;
  }

  function refresh(appId = DEFAULT_APP_ID, options = {}) {
    const doc = options.document || getDocument();
    const el = options.element || doc?.querySelector?.(STATUS_SELECTOR) || null;
    if (!el) return null;
    const report = options.report || diagnose(appId);
    const pathway = pathwayFromReport(report);
    const summary = summarizePathway(pathway);
    el.__mcelSurfaceStatusReport = report || null;
    el.__mcelSurfaceStatusSummary = summary;
    return renderStatus(el, summary);
  }

  function mount(appId = DEFAULT_APP_ID, options = {}) {
    const doc = options.document || getDocument();
    const el = options.element || doc?.querySelector?.(STATUS_SELECTOR) || null;
    if (!el) return null;

    refresh(appId, {document: doc, element: el});

    const global = getGlobal();
    if (!options.noInterval && typeof global.setInterval === "function") {
      if (el.__mcelSurfaceStatusInterval && typeof global.clearInterval === "function") {
        global.clearInterval(el.__mcelSurfaceStatusInterval);
      }
      el.__mcelSurfaceStatusInterval = global.setInterval(() => {
        refresh(appId, {document: doc, element: el});
      }, Number(options.intervalMs || REFRESH_INTERVAL_MS));
    }

    return el;
  }

  function boot() {
    mount(DEFAULT_APP_ID);
  }

  const api = Object.freeze({
    VERSION,
    STATUS_SELECTOR,
    REFRESH_INTERVAL_MS,
    pathwayFromReport,
    summarizePathway,
    renderStatus,
    refresh,
    mount,
    _private: Object.freeze({
      normalizeState,
      flagOk,
      boolWord,
      escapeText
    })
  });

  const global = getGlobal();
  try {
    global.McelCodeEditorSurfaceStatus = api;
  } catch {}

  const doc = getDocument();
  if (doc?.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", boot, {once: true});
  } else if (doc) {
    boot();
  }
})();
