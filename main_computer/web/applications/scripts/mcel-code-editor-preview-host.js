(() => {
  "use strict";

  const VERSION = "mcel-code-editor-preview-host-v1";
  const DEFAULT_APP_ID = "code-editor";
  const STATUS_SELECTOR = "#code-editor-mcel-preview-status";
  const PANEL_ID = "code-editor-mcel-preview-popover";
  const PREVIEW_RENDERER_ID = "code-editor.preview.debug-html";
  const REFRESH_INTERVAL_MS = 2500;

  function getGlobal() {
    return typeof window !== "undefined" ? window : globalThis;
  }

  function getDocument() {
    try {
      if (typeof document !== "undefined" && document?.querySelector) return document;
    } catch {}
    return null;
  }

  function previewContractApi() {
    const global = getGlobal();
    return global.McelSurfacePreviewContract || null;
  }

  function authoredDocumentApi() {
    const global = getGlobal();
    return global.McelAuthoredSurfaceDocument || null;
  }

  function authoringStatusApi() {
    const global = getGlobal();
    return global.McelCodeEditorAuthoringStatus || null;
  }

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value);
  }

  function trimmed(value) {
    return safeString(value).trim();
  }

  function escapeText(value) {
    return safeString(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function escapeAttr(value) {
    return escapeText(value).replace(/"/g, "&quot;");
  }

  function splitTokens(values) {
    if (Array.isArray(values)) return values.map(trimmed).filter(Boolean);
    return trimmed(values).split(",").map((item) => item.trim()).filter(Boolean);
  }

  function readCurrentEditorText(options = {}) {
    if (typeof options.sourceText === "string") return options.sourceText;

    const authoring = authoringStatusApi();
    if (authoring && typeof authoring.readCurrentEditorText === "function") {
      return authoring.readCurrentEditorText(options);
    }

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

  function diagnostic(code, severity, finding, detail = {}) {
    return Object.freeze({code, severity, finding, detail: Object.freeze(detail || {})});
  }

  function countBySeverity(diagnostics) {
    const counts = {errors: 0, warnings: 0, ok: 0};
    (diagnostics || []).forEach((item) => {
      if (!item) return;
      if (item.severity === "error") counts.errors += 1;
      else if (item.severity === "warning") counts.warnings += 1;
      else counts.ok += 1;
    });
    return Object.freeze(counts);
  }

  function hasErrors(diagnostics) {
    return (diagnostics || []).some((item) => item && item.severity === "error");
  }

  function layoutNodeMap(layoutGrammar) {
    const out = new Map();
    (layoutGrammar?.nodes || []).forEach((node) => out.set(trimmed(node.id), node));
    return out;
  }

  function layoutEdgeMap(layoutGrammar) {
    const out = new Map();
    (layoutGrammar?.edges || []).forEach((edge) => out.set(trimmed(edge.id), edge));
    return out;
  }

  function layoutControlMap(layoutGrammar) {
    const out = new Map();
    (layoutGrammar?.controls || []).forEach((control) => out.set(trimmed(control.id), control));
    return out;
  }

  function nodePorts(layoutGrammar, nodeId) {
    return splitTokens(layoutGrammar?.nodePorts?.[nodeId] || []);
  }

  function styleForBox(layout, fallback = {}) {
    const x = Number(layout?.anchorX ?? fallback.anchorX ?? 0);
    const y = Number(layout?.anchorY ?? fallback.anchorY ?? 0);
    const width = Number(layout?.width ?? fallback.width ?? 120);
    const height = Number(layout?.height ?? fallback.height ?? 56);
    if (![x, y, width, height].every(Number.isFinite)) return "";
    return `left:${x - width / 2}px;top:${y - height / 2}px;width:${width}px;height:${height}px;`;
  }

  function renderSurfaceAttrs(surfaceIR, layoutGrammar, profile, surfaceKind) {
    const surface = surfaceIR?.surface || {};
    const viewport = layoutGrammar?.viewport || {};
    return [
      `data-mcel-surface-id="${escapeAttr(surface.id || "surface.preview")}"`,
      `data-mcel-surface-kind="${escapeAttr(surface.kind || "semantic-surface")}"`,
      `data-mcel-surface-role="${escapeAttr(surface.role || "authoring-preview")}"`,
      `data-mcel-surface-contract="${escapeAttr(surface.contract || surfaceIR?.ridgeContractVersion || "mcel.semantic-surface-ridges.v1")}"`,
      `data-mcel-renderer="${escapeAttr(profile.id)}"`,
      `data-mcel-projection="${escapeAttr(surfaceKind)}"`,
      `data-mcel-authoritative="true"`,
      `data-layout-viewport-width="${escapeAttr(viewport.width ?? "")}"`,
      `data-layout-viewport-height="${escapeAttr(viewport.height ?? "")}"`,
      `data-layout-safe-margin="${escapeAttr(viewport.safeMargin ?? 0)}"`
    ].join(" ");
  }

  function renderRegions(surfaceIR, layoutGrammar) {
    const regionRole = new Map((surfaceIR?.graph?.regions || []).map((region) => [trimmed(region.id), trimmed(region.role)]));
    return (layoutGrammar?.regions || []).map((region) => {
      const id = trimmed(region.id);
      const role = trimmed(region.role || regionRole.get(id));
      const style = [
        Number.isFinite(Number(region.x)) ? `left:${Number(region.x)}px` : "",
        Number.isFinite(Number(region.y)) ? `top:${Number(region.y)}px` : "",
        Number.isFinite(Number(region.width)) ? `width:${Number(region.width)}px` : "",
        Number.isFinite(Number(region.height)) ? `height:${Number(region.height)}px` : ""
      ].filter(Boolean).join(";");
      return `<section class="mcel-preview-region" style="${escapeAttr(style)}" data-mcel-region="${escapeAttr(id)}" data-mcel-region-role="${escapeAttr(role)}" data-layout-x="${escapeAttr(region.x ?? "")}" data-layout-y="${escapeAttr(region.y ?? "")}" data-layout-region-width="${escapeAttr(region.width ?? "")}" data-layout-region-height="${escapeAttr(region.height ?? "")}"><span>${escapeText(role || id)}</span></section>`;
    }).join("");
  }

  function renderNodes(surfaceIR, layoutGrammar) {
    const layouts = layoutNodeMap(layoutGrammar);
    return (surfaceIR?.graph?.nodes || []).map((node) => {
      const layout = layouts.get(trimmed(node.id)) || {};
      const ports = nodePorts(layoutGrammar, trimmed(node.id));
      const style = styleForBox(layout);
      return `<article class="mcel-preview-node" style="${escapeAttr(style)}" data-mcel-node-id="${escapeAttr(node.id)}" data-mcel-node-type="${escapeAttr(node.type)}" data-mcel-node-label="${escapeAttr(node.label)}" data-mcel-source="${escapeAttr(node.source)}" data-mcel-provenance="${escapeAttr(node.provenance)}" data-mcel-symbol="${escapeAttr(node.symbol)}" data-mcel-channel="${escapeAttr(node.channel)}" data-mcel-signal="${escapeAttr(node.signal)}" data-mcel-home-region="${escapeAttr(node.homeRegion)}" data-mcel-actual-region="${escapeAttr(node.actualRegion || node.homeRegion)}" data-mcel-teleported="${node.teleported ? "true" : "false"}" data-layout-anchor-x="${escapeAttr(layout.anchorX ?? "")}" data-layout-anchor-y="${escapeAttr(layout.anchorY ?? "")}" data-layout-width="${escapeAttr(layout.width ?? "")}" data-layout-height="${escapeAttr(layout.height ?? "")}" data-layout-z="${escapeAttr(layout.z ?? "")}" data-layout-region="${escapeAttr(layout.region || node.homeRegion)}" data-layout-ports="${escapeAttr(ports.join(","))}"><strong>${escapeText(node.symbol || "●")} ${escapeText(node.label || node.id)}</strong><small>${escapeText(node.type || "node")}</small></article>`;
    }).join("");
  }

  function renderEdges(surfaceIR, layoutGrammar) {
    const layouts = layoutEdgeMap(layoutGrammar);
    return (surfaceIR?.graph?.edges || []).map((edge) => {
      const layout = layouts.get(trimmed(edge.id)) || {};
      const allowed = splitTokens(edge.allowedInferences).join(",");
      const forbidden = splitTokens(edge.forbiddenInferences).join(",");
      return `<i class="mcel-preview-edge" data-mcel-edge-id="${escapeAttr(edge.id)}" data-mcel-edge-kind="${escapeAttr(edge.kind)}" data-mcel-from="${escapeAttr(edge.from)}" data-mcel-to="${escapeAttr(edge.to)}" data-mcel-relation="${escapeAttr(edge.relation)}" data-mcel-causal-link="${edge.causalLink ? "true" : "false"}" data-mcel-allowed-inferences="${escapeAttr(allowed)}" data-mcel-forbidden-inferences="${escapeAttr(forbidden)}" data-layout-route-kind="${escapeAttr(layout.routeKind)}" data-layout-from-port="${escapeAttr(layout.fromPort)}" data-layout-to-port="${escapeAttr(layout.toPort)}" data-layout-z="${escapeAttr(layout.z ?? "")}"></i>`;
    }).join("");
  }

  function renderControls(surfaceIR, layoutGrammar) {
    const layouts = layoutControlMap(layoutGrammar);
    return (surfaceIR?.graph?.controls || []).map((control) => {
      const layout = layouts.get(trimmed(control.id)) || {};
      const style = styleForBox(layout, {width: 120, height: 32});
      return `<button type="button" class="mcel-preview-control" style="${escapeAttr(style)}" data-mcel-control="${escapeAttr(control.id)}" data-mcel-control-action="${escapeAttr(control.action)}" data-mcel-reveals="${escapeAttr(control.reveals)}" data-layout-anchor-x="${escapeAttr(layout.anchorX ?? "")}" data-layout-anchor-y="${escapeAttr(layout.anchorY ?? "")}" data-layout-width="${escapeAttr(layout.width ?? "")}" data-layout-height="${escapeAttr(layout.height ?? "")}" data-layout-z="${escapeAttr(layout.z ?? "")}">${escapeText(control.id)}</button>`;
    }).join("");
  }

  function renderDebugPreviewHtml(request) {
    const profile = request.profile || createPreviewRenderer().profile;
    const surfaceIR = request.surfaceIR || {};
    const layoutGrammar = request.layoutGrammar || {};
    const viewport = layoutGrammar.viewport || {};
    const width = Number(viewport.width || 900);
    const height = Number(viewport.height || 520);
    const surfaceAttrs = renderSurfaceAttrs(surfaceIR, layoutGrammar, profile, "html");

    return `<!doctype html><html><head><meta charset="utf-8"><style>
      :root{color-scheme:dark}
      body{margin:0;background:#05060a;color:#e8eefc;font:12px system-ui,sans-serif}
      .mcel-preview-surface{position:relative;width:${Number.isFinite(width) ? width : 900}px;height:${Number.isFinite(height) ? height : 520}px;overflow:hidden;background:linear-gradient(135deg,#070a12,#101522);border:1px solid #2b3550}
      .mcel-preview-region{position:absolute;border:1px dashed rgba(138,180,248,.35);border-radius:14px;color:#8ab4f8;padding:6px;pointer-events:none}
      .mcel-preview-node{position:absolute;display:grid;place-items:center;gap:4px;border:1px solid rgba(82,196,125,.72);border-radius:12px;background:rgba(82,196,125,.11);box-sizing:border-box;text-align:center;padding:8px;overflow:hidden}
      .mcel-preview-node small{color:#b4becd}
      .mcel-preview-edge{display:none}
      .mcel-preview-control{position:absolute;border:1px solid rgba(255,197,89,.70);border-radius:999px;background:rgba(255,197,89,.12);color:#ffd98f}
    </style></head><body><main class="mcel-preview-surface" ${surfaceAttrs}>${renderRegions(surfaceIR, layoutGrammar)}${renderEdges(surfaceIR, layoutGrammar)}${renderNodes(surfaceIR, layoutGrammar)}${renderControls(surfaceIR, layoutGrammar)}</main></body></html>`;
  }

  function createPreviewRenderer(options = {}) {
    const profile = Object.freeze({
      id: options.id || PREVIEW_RENDERER_ID,
      label: "Code Editor MCEL authoring preview renderer",
      version: "1",
      defaultSurfaceKind: "html",
      surfaceKinds: Object.freeze(["html"]),
      capabilities: Object.freeze(["debug-preview", "semantic-ridges", "layout-ridges"]),
      inputs: Object.freeze({semanticSurfaceIR: true, sharedLayoutGrammar: true}),
      output: Object.freeze({
        emitsSemanticRidges: true,
        emitsLayoutRidges: true,
        authoritativeSurface: true,
        rendererAttribution: true,
        projectionAttribution: true
      })
    });

    return Object.freeze({
      profile,
      render(request) {
        return Object.freeze({
          surfaceKind: "html",
          renderedText: renderDebugPreviewHtml(Object.assign({}, request || {}, {profile}))
        });
      }
    });
  }

  function buildUnavailableReport(code, finding, detail) {
    const diagnostics = Object.freeze([diagnostic(code, "error", finding, detail || {})]);
    return Object.freeze({
      version: VERSION,
      previewable: false,
      status: "unavailable",
      valid: false,
      renderedText: "",
      diagnostics,
      counts: countBySeverity(diagnostics),
      summary: "MCEL Preview: unavailable"
    });
  }

  function renderPreviewForSource(options = {}) {
    const sourceText = readCurrentEditorText(options);
    const previewApi = previewContractApi();
    const authoredApi = authoredDocumentApi();
    if (!authoredApi || typeof authoredApi.analyzeText !== "function") {
      return buildUnavailableReport(
        "code-editor-preview-missing-authored-document-api",
        "Code Editor MCEL preview requires McelAuthoredSurfaceDocument."
      );
    }
    if (!previewApi || typeof previewApi.renderPreview !== "function") {
      return buildUnavailableReport(
        "code-editor-preview-missing-preview-contract-api",
        "Code Editor MCEL preview requires McelSurfacePreviewContract."
      );
    }

    const analysis = options.analysis || authoredApi.analyzeText(sourceText, options.analysisOptions || {});
    if (analysis.applicable === false || analysis.status === "not-applicable" || !analysis.containsSurfaceRidges) {
      return Object.freeze({
        version: VERSION,
        analysis,
        previewable: false,
        status: "not-applicable",
        valid: true,
        renderedText: "",
        diagnostics: Object.freeze([...(analysis.diagnostics || [])]),
        counts: analysis.counts || countBySeverity(analysis.diagnostics || []),
        summary: "MCEL Preview: not applicable"
      });
    }

    const renderer = options.renderer || createPreviewRenderer(options.rendererOptions || {});
    const report = previewApi.renderPreview({
      sourceText,
      analysis,
      surfaceIR: analysis.surfaceIR,
      layoutGrammar: analysis.layoutGrammar,
      renderer,
      surfaceKind: "html",
      verifyOutput: options.verifyOutput !== false,
      options: Object.assign({host: DEFAULT_APP_ID}, options.previewOptions || {})
    });

    return Object.freeze(Object.assign({
      version: VERSION,
      analysis
    }, report));
  }

  function normalizePreviewReport(report) {
    const subject = report || {};
    if (subject.status === "not-applicable" || subject.previewable === false && subject.valid) {
      return {
        state: "not-applicable",
        value: "no preview",
        visible: false,
        title: "The current document does not contain an MCEL surface preview.",
        report: subject
      };
    }
    const errors = Number(subject.counts?.errors || 0);
    const warnings = Number(subject.counts?.warnings || 0);
    const pass = subject.valid === true && subject.status === "pass" && errors === 0;
    const state = pass ? (warnings > 0 ? "warning" : "pass") : "fail";
    const value = pass ? "PASS" : state === "warning" ? "WARN" : "FAIL";
    const summary = subject.summary || `MCEL Preview: ${value}`;
    return {
      state,
      value,
      visible: true,
      title: summary,
      report: subject
    };
  }

  function ensurePreviewPanel(doc = getDocument()) {
    if (!doc?.querySelector) return null;
    let panel = doc.querySelector(`#${PANEL_ID}`);
    if (panel) return panel;

    panel = doc.createElement("aside");
    panel.id = PANEL_ID;
    panel.className = "code-editor-mcel-preview-popover";
    panel.hidden = true;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "false");
    panel.setAttribute("aria-label", "MCEL authored surface preview");
    panel.setAttribute("data-mcel-surface-id", "code-editor.surface.mcel-preview-popover");
    panel.setAttribute("data-mcel-surface-role", "supporting-preview-surface");
    panel.setAttribute("data-mcel-node-id", "code-editor.node.mcel-preview-popover");
    panel.setAttribute("data-mcel-node-type", "preview_host");
    panel.setAttribute("data-mcel-region", "code-editor.region.titlebar");
    panel.setAttribute("data-mcel-home-region", "code-editor.region.titlebar");
    panel.setAttribute("data-mcel-actual-region", "code-editor.region.titlebar");
    panel.setAttribute("data-mcel-source", "mcel-code-editor-preview-host.js");
    panel.setAttribute("data-mcel-provenance", "patch:mcel-safe-16-preview-host");

    const host = doc.querySelector(".code-studio-title-actions") || doc.querySelector(".code-studio-titlebar") || doc.body;
    host?.appendChild?.(panel);
    return panel;
  }

  function renderPreviewPanel(panel, model) {
    if (!panel) return null;
    const report = model?.report || {};
    const renderedText = safeString(report.renderedText);
    const status = model?.state || "pending";
    const summary = model?.title || report.summary || "MCEL Preview";
    const diagnostics = (report.diagnostics || []).filter((item) => item && item.severity === "error").slice(0, 3);
    const issueText = diagnostics.length
      ? diagnostics.map((item) => `<li>${escapeText(item.code)} — ${escapeText(item.finding)}</li>`).join("")
      : "<li>No blocking preview diagnostics.</li>";

    panel.setAttribute("data-mcel-preview-state", status);
    panel.innerHTML = [
      '<div class="code-editor-mcel-preview-popover__header">',
      '<div><span class="code-editor-mcel-preview-popover__eyebrow">MCEL preview</span>',
      `<strong>${escapeText(summary)}</strong></div>`,
      '<button type="button" class="code-editor-mcel-preview-popover__close" data-mcel-preview-close aria-label="Close MCEL preview">×</button>',
      '</div>',
      `<iframe class="code-editor-mcel-preview-popover__frame" title="MCEL authored surface preview" sandbox srcdoc="${escapeAttr(renderedText || "<!doctype html><p>No preview available.</p>")}"></iframe>`,
      '<ul class="code-editor-mcel-preview-popover__diagnostics">',
      issueText,
      '</ul>'
    ].join("");
    return panel;
  }

  function renderStatus(element, report, options = {}) {
    if (!element) return null;
    const model = normalizePreviewReport(report);
    element.__mcelPreviewHostReport = model.report || null;
    element.dataset.mcelPreviewStatusState = model.state;
    element.setAttribute("title", model.title);
    element.setAttribute("aria-label", model.title);
    element.hidden = !model.visible;
    element.innerHTML = [
      '<span class="code-editor-mcel-preview-status__label">MCEL Preview</span>',
      `<span class="code-editor-mcel-preview-status__value">${escapeText(model.value)}</span>`
    ].join("");

    const doc = options.document || getDocument();
    const panel = options.panel || doc?.querySelector?.(`#${PANEL_ID}`) || null;
    if (panel && element.getAttribute("aria-expanded") === "true") {
      renderPreviewPanel(panel, model);
    }

    return model;
  }

  function setOpen(trigger, panel, open, model) {
    if (!trigger || !panel) return false;
    const state = !!open;
    panel.hidden = !state;
    panel.setAttribute("aria-hidden", String(!state));
    trigger.setAttribute("aria-expanded", String(state));
    trigger.setAttribute("data-mcel-preview-open", String(state));
    if (state && model) renderPreviewPanel(panel, model);
    return state;
  }

  function refresh(options = {}) {
    const doc = options.document || getDocument();
    const element = options.element || doc?.querySelector?.(STATUS_SELECTOR) || null;
    if (!element) return null;
    const report = renderPreviewForSource(options);
    return renderStatus(element, report, {document: doc, panel: options.panel});
  }

  function togglePreview(options = {}) {
    const doc = options.document || getDocument();
    const trigger = options.element || doc?.querySelector?.(STATUS_SELECTOR) || null;
    if (!trigger || trigger.hidden) return null;
    const panel = options.panel || ensurePreviewPanel(doc);
    if (!panel) return null;
    const model = refresh({document: doc, element: trigger, sourceText: options.sourceText});
    const isOpen = trigger.getAttribute("aria-expanded") === "true" && !panel.hidden;
    setOpen(trigger, panel, !isOpen, model);
    return model;
  }

  function closePreview(options = {}) {
    const doc = options.document || getDocument();
    const trigger = options.element || doc?.querySelector?.(STATUS_SELECTOR) || null;
    const panel = options.panel || doc?.querySelector?.(`#${PANEL_ID}`) || null;
    return !setOpen(trigger, panel, false);
  }

  function mount(options = {}) {
    const doc = options.document || getDocument();
    const element = options.element || doc?.querySelector?.(STATUS_SELECTOR) || null;
    if (!doc || !element) return null;

    refresh({document: doc, element});

    element.addEventListener?.("click", (event) => {
      event.preventDefault();
      togglePreview({document: doc, element});
    });
    element.addEventListener?.("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        togglePreview({document: doc, element});
      }
      if (event.key === "Escape") {
        closePreview({document: doc, element});
      }
    });

    doc.addEventListener?.("click", (event) => {
      if (event.target?.closest?.("[data-mcel-preview-close]")) {
        event.preventDefault();
        closePreview({document: doc, element});
      }
    });

    const sourceEditor = doc.querySelector?.("#code-studio-source-editor");
    sourceEditor?.addEventListener?.("input", () => refresh({document: doc, element}));

    const global = getGlobal();
    if (!options.noInterval && typeof global.setInterval === "function") {
      if (element.__mcelPreviewHostInterval && typeof global.clearInterval === "function") {
        global.clearInterval(element.__mcelPreviewHostInterval);
      }
      element.__mcelPreviewHostInterval = global.setInterval(() => refresh({document: doc, element}), Number(options.intervalMs || REFRESH_INTERVAL_MS));
    }

    return element;
  }

  function boot() {
    mount();
  }

  const api = Object.freeze({
    VERSION,
    STATUS_SELECTOR,
    PANEL_ID,
    PREVIEW_RENDERER_ID,
    REFRESH_INTERVAL_MS,
    readCurrentEditorText,
    createPreviewRenderer,
    renderDebugPreviewHtml,
    renderPreviewForSource,
    normalizePreviewReport,
    ensurePreviewPanel,
    renderPreviewPanel,
    renderStatus,
    refresh,
    togglePreview,
    closePreview,
    mount
  });

  const global = getGlobal();
  try {
    global.McelCodeEditorPreviewHost = api;
  } catch {}

  const doc = getDocument();
  if (doc?.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", boot, {once: true});
  } else if (doc) {
    boot();
  }
})();
