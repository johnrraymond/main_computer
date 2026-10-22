(() => {
  "use strict";

  const VERSION = "mcel-code-editor-surface-status-v1";
  const DEFAULT_APP_ID = "code-editor";
  const STATUS_SELECTOR = "#code-editor-mcel-surface-status";
  const REFRESH_INTERVAL_MS = 30000;
  const ROUTE_ATTRIBUTE = "data-active-app";

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

  function conformanceFromReport(report) {
    if (!report || typeof report !== "object") return null;
    return report.appSurfaceConformance ||
      report.summary?.appSurfaceConformance ||
      report.measurements?.appSurfaceConformance ||
      null;
  }

  function reportHasActiveErrors(report) {
    if (!report || typeof report !== "object") return false;
    return Number(report.counts?.errors || report.current?.counts?.errors || 0) > 0;
  }

  function conformanceIsPassing(conformance) {
    if (!conformance || typeof conformance !== "object") return false;
    const status = normalizeState(conformance.status || conformance.verdict);
    return (conformance.valid === true || status === "pass") && status !== "fail";
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

  function summarizeReport(report) {
    const pathway = pathwayFromReport(report);
    const pathwaySummary = summarizePathway(pathway);
    const conformance = conformanceFromReport(report);
    const conformancePass = conformanceIsPassing(conformance) && !reportHasActiveErrors(report);
    const pathwayState = normalizeState(pathwaySummary.state);

    if (conformancePass && (pathwayState === "fail" || pathwayState === "unavailable" || pathwayState === "pending" || !pathway)) {
      const layerIds = Array.isArray(conformance.layers)
        ? conformance.layers.map((layer) => layer.id).filter(Boolean)
        : Array.isArray(conformance.requiredLayerIds) ? conformance.requiredLayerIds : [];
      return {
        state: "pass",
        value: "PASS",
        label: pathwaySummary.label || "MCEL Surface",
        title: [
          "MCEL Surface: PASS",
          "Host conformance PASS",
          layerIds.length ? `Layers ${layerIds.join(", ")}` : "",
          pathway ? `Pathway ${String(pathwaySummary.value || "").toUpperCase()}` : "Pathway not required for ordinary source"
        ].filter(Boolean).join(" · "),
        details: {
          semanticRidges: "host-pass",
          surfaceIR: "host-pass",
          layout: "host-pass",
          extraction: "host-pass",
          roundTrip: "host-pass",
          appSurfaceConformance: "pass"
        },
        raw: {
          pathway,
          appSurfaceConformance: conformance
        }
      };
    }

    if (conformance && !conformancePass && reportHasActiveErrors(report)) {
      return {
        ...pathwaySummary,
        state: "fail",
        value: "FAIL",
        title: `${pathwaySummary.title || "MCEL Surface"} · Host conformance FAIL`
      };
    }

    return pathwaySummary;
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

  function inactiveApplication(doc, appId) {
    const selected = String(doc?.body?.dataset?.activeApp || "");
    return Boolean(selected && selected !== appId);
  }

  function inactiveSummary() {
    return {
      ...summarizePathway(null),
      title: "MCEL Surface: PENDING · Code Editor is not the active application."
    };
  }

  function refresh(appId = DEFAULT_APP_ID, options = {}) {
    const doc = options.document || getDocument();
    const el = options.element || doc?.querySelector?.(STATUS_SELECTOR) || null;
    if (!el) return null;
    // The desktop route deliberately hides Code Editor. Measuring its zero-sized
    // layout there is not a failed Code Editor surface diagnosis.
    if (inactiveApplication(doc, appId)) {
      const summary = inactiveSummary();
      el.__mcelSurfaceStatusReport = null;
      el.__mcelSurfaceStatusSummary = summary;
      return renderStatus(el, summary);
    }
    const report = options.report || diagnose(appId);
    const summary = options.summary || summarizeReport(report);
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
    // Route activation sets data-active-app before it unhides the app. The
    // attribute observer runs after the route handler, and its animation-frame
    // callback measures the new visible layout without waiting 30 seconds.
    if (el.__mcelSurfaceStatusRouteObserver) {
      el.__mcelSurfaceStatusRouteObserver.disconnect();
    }
    el.__mcelSurfaceStatusRouteGeneration = (el.__mcelSurfaceStatusRouteGeneration || 0) + 1;
    const Observer = global.MutationObserver;
    if (!options.noObserver && typeof Observer === "function" && doc?.body) {
      const observer = new Observer(() => {
        const generation = ++el.__mcelSurfaceStatusRouteGeneration;
        if (inactiveApplication(doc, appId)) {
          refresh(appId, {document: doc, element: el});
          return;
        }
        const run = () => {
          if (generation !== el.__mcelSurfaceStatusRouteGeneration) return;
          refresh(appId, {document: doc, element: el});
        };
        if (typeof global.requestAnimationFrame === "function") {
          global.requestAnimationFrame(run);
        } else if (typeof global.setTimeout === "function") {
          global.setTimeout(run, 0);
        } else {
          run();
        }
      });
      observer.observe(doc.body, {attributes: true, attributeFilter: [ROUTE_ATTRIBUTE]});
      el.__mcelSurfaceStatusRouteObserver = observer;
    }
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
    conformanceFromReport,
    summarizePathway,
    summarizeReport,
    renderStatus,
    refresh,
    mount,
    _private: Object.freeze({
      normalizeState,
      flagOk,
      boolWord,
      reportHasActiveErrors,
      conformanceIsPassing,
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
