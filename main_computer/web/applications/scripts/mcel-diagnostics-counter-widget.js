(() => {
  "use strict";

  const VERSION = "mcel-diagnostics-counter-widget-v4";
  const STARTUP_ERROR_WARNING_MS = 5000;
  const REFRESH_INTERVAL_MS = 30000;
  const DEFAULT_APP_ID = "code-editor";

  const APP_WIDGETS = Object.freeze({
    "code-editor": {
      root: "#code-editor-app",
      placeholder: "#code-editor-diagnostics-counter",
      id: "code-editor-diagnostics-counter",
      anchors: [
        "#code-editor-app .code-studio-title-actions",
        "#code-editor-app .code-studio-titlebar"
      ]
    },
    calculator: {
      root: "#calculator-app",
      placeholder: "#calculator-diagnostics-counter",
      id: "calculator-diagnostics-counter",
      anchors: [
        "#calculator-app .calculator-mode-switch",
        "#calculator-app .calculator-shell"
      ]
    },
    "file-explorer": {
      root: "#file-explorer-app",
      placeholder: "#file-explorer-diagnostics-counter",
      id: "file-explorer-diagnostics-counter",
      anchors: [
        "#file-explorer-app .file-explorer-toolbar",
        "#file-explorer-app .file-explorer-roots-panel > div:first-child",
        "#file-explorer-app .file-explorer-shell"
      ]
    },
    "git-tools": {
      root: "#git-tools-app",
      placeholder: "#git-tools-diagnostics-counter",
      id: "git-tools-diagnostics-counter",
      anchors: [
        "#git-tools-app .git-tools-feedback-resolution",
        "#git-tools-app .git-tools-feedback-operation",
        "#git-tools-app"
      ]
    },
    "website-builder": {
      root: "#website-builder-app",
      placeholder: "#website-builder-diagnostics-counter",
      id: "website-builder-diagnostics-counter",
      anchors: [
        "#website-builder-app .website-builder-actions",
        "#website-builder-app .website-builder-summary",
        "#website-builder-app"
      ]
    }
  });

  function getDocument() {
    try {
      if (typeof document !== "undefined" && document?.querySelector) return document;
    } catch {}
    return null;
  }

  function clampCount(value) {
    const number = Number.isFinite(Number(value)) ? Math.max(0, Math.floor(Number(value))) : 0;
    return Math.min(99, number);
  }

  function formatCount(value) {
    return String(clampCount(value)).padStart(2, "0");
  }

  function isVisibleElement(el) {
    if (!el || typeof el.getBoundingClientRect !== "function") return false;
    try {
      const rect = el.getBoundingClientRect();
      const style = typeof getComputedStyle === "function" ? getComputedStyle(el) : {};
      return rect.width > 0 &&
        rect.height > 0 &&
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        Number(style.opacity || 1) !== 0 &&
        !el.hidden;
    } catch {
      return false;
    }
  }

  function appConfig(appId) {
    return APP_WIDGETS[appId] || APP_WIDGETS[DEFAULT_APP_ID];
  }

  function appRoot(doc, appId) {
    const config = appConfig(appId);
    return doc?.querySelector(config.root) || null;
  }

  function isAppVisible(doc, appId) {
    const root = appRoot(doc, appId);
    return isVisibleElement(root);
  }

  function isVisibleBox(box) {
    return Boolean(box?.exists && box.visible);
  }

  function isUsefulBox(box) {
    return Boolean(box?.exists && box.visible && Number(box.width) > 0 && Number(box.height) > 0);
  }

  function timestampMs(value) {
    const ms = Date.parse(String(value || ""));
    return Number.isFinite(ms) ? ms : null;
  }

  function isStartupWindow(history, now = new Date().toISOString()) {
    if (!history?.pageStartedAt) return false;
    const started = timestampMs(history.pageStartedAt);
    const current = timestampMs(now);
    if (started === null || current === null) return false;
    return current >= started && current - started <= STARTUP_ERROR_WARNING_MS;
  }

  function normalizeIssueSeverity(severity, options = {}) {
    const value = String(severity || "").toLowerCase();
    if (value === "critical" || value === "error") {
      return options.startupWindow ? "warning" : "error";
    }
    if (value === "warning") return "warning";
    return "";
  }

  function issueCounts(report, options = {}) {
    const findings = Array.isArray(report?.findings) ? report.findings : [];
    return findings.reduce(
      (counts, finding) => {
        const severity = normalizeIssueSeverity(finding?.severity, options);
        if (severity === "error") counts.errors += 1;
        else if (severity === "warning") counts.warnings += 1;
        return counts;
      },
      {errors: 0, warnings: 0}
    );
  }

  function uniqueRegionEntries(requiredRegions) {
    if (!requiredRegions || typeof requiredRegions !== "object") return [];
    return Object.entries(requiredRegions)
      .filter(([key]) => !String(key).startsWith("#") && !String(key).startsWith("."))
      .map(([, value]) => value)
      .filter(Boolean);
  }

  function uniqueForbiddenEntries(forbiddenRegions) {
    if (!Array.isArray(forbiddenRegions)) return [];
    const seen = new Set();
    return forbiddenRegions.filter((entry) => {
      const key = `${entry?.id || entry?.selector || "unknown"}:${entry?.matchIndex ?? "single"}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function derivedOkCount(report) {
    if (!report || typeof report !== "object") return 0;

    let ok = 0;
    const measurements = report.measurements || {};
    const summary = report.summary || {};
    const primarySurface = summary.primarySurface || {};
    const minWidth = Number(report.contract?.primarySurface?.minWidth || 1);
    const minHeight = Number(report.contract?.primarySurface?.minHeight || 1);

    if (report.verdict === "pass") ok += 1;
    if (report.mode && report.contract?.mode && report.mode === report.contract.mode) ok += 1;

    for (const region of uniqueRegionEntries(measurements.requiredRegions)) {
      if (region?.exists && region.visible) ok += 1;
    }

    const host = measurements.surfaces?.primaryHost || measurements.surfaces?.monacoHost || primarySurface.host;
    const editor = measurements.surfaces?.primaryEditor || measurements.surfaces?.monacoEditor || primarySurface.editor || host;

    if (host?.exists) ok += 1;
    if (isVisibleBox(host)) ok += 1;
    if (isUsefulBox(host) && Number(host.width) >= minWidth && Number(host.height) >= minHeight) ok += 1;

    if (editor?.exists) ok += 1;
    if (isVisibleBox(editor)) ok += 1;
    if (isUsefulBox(editor) && Number(editor.width) >= minWidth && Number(editor.height) >= minHeight) ok += 1;

    if (primarySurface.usable) ok += 1;
    if (primarySurface.exactlyOneAuthoritativeSurface) ok += 1;

    for (const entry of uniqueForbiddenEntries(measurements.forbiddenRegions)) {
      if (!entry?.box?.exists || !entry.box.visible) ok += 1;
    }

    const findings = Array.isArray(report.findings) ? report.findings : [];
    if (!findings.some((finding) => finding?.code === "collapsed-layout-owner")) ok += 1;
    if (!findings.some((finding) => finding?.code === "competing-editor-surface-visible")) ok += 1;

    return ok;
  }

  function summarizeReport(report, history) {
    const counts = issueCounts(report, {startupWindow: isStartupWindow(history, report?.timestamp || new Date().toISOString())});
    return {
      errors: counts.errors,
      warnings: counts.warnings,
      ok: derivedOkCount(report)
    };
  }

  function compactIssue(finding) {
    return {
      severity: finding?.severity || "",
      code: finding?.code || "",
      finding: finding?.finding || "",
      recommendedNextProbe: finding?.recommendedNextProbe || ""
    };
  }

  function shouldRememberIssue(finding) {
    return Boolean(normalizeIssueSeverity(finding?.severity));
  }

  function issueKey(finding) {
    const issue = compactIssue(finding);
    return [
      issue.code,
      issue.finding,
      issue.recommendedNextProbe
    ].join("\u001f");
  }

  function createIssueHistory(now = new Date().toISOString()) {
    return {
      schema: "mcel-diagnostics-counter-history-v3",
      pageStartedAt: now,
      startupErrorWarningMs: STARTUP_ERROR_WARNING_MS,
      lastUpdatedAt: now,
      entries: []
    };
  }

  function findHistoryEntry(history, key) {
    return (history.entries || []).find((entry) => entry.key === key) || null;
  }

  function updateIssueHistory(history, report, now = new Date().toISOString()) {
    const findings = Array.isArray(report?.findings) ? report.findings : [];
    const startupWindow = isStartupWindow(history, now);
    const activeKeys = new Set();

    for (const finding of findings) {
      if (!shouldRememberIssue(finding)) continue;
      const key = issueKey(finding);
      activeKeys.add(key);
      const originalNormalizedSeverity = normalizeIssueSeverity(finding?.severity, {startupWindow: false});
      const normalizedSeverity = normalizeIssueSeverity(finding?.severity, {startupWindow});
      const countedAsStartupWarning = originalNormalizedSeverity === "error" && normalizedSeverity === "warning";
      let entry = findHistoryEntry(history, key);

      if (!entry) {
        entry = {
          key,
          ...compactIssue(finding),
          normalizedSeverity,
          originalNormalizedSeverity,
          countedAsStartupWarning,
          lifecyclePhase: startupWindow ? "startup" : "runtime",
          firstSeen: now,
          lastSeen: now,
          firstReportTimestamp: report?.timestamp || now,
          lastReportTimestamp: report?.timestamp || now,
          seenCount: 0,
          currentlyActive: true,
          clearedAt: ""
        };
        history.entries.push(entry);
      }

      entry.severity = finding?.severity || entry.severity;
      entry.normalizedSeverity = normalizedSeverity;
      entry.originalNormalizedSeverity = originalNormalizedSeverity;
      entry.countedAsStartupWarning = Boolean(entry.countedAsStartupWarning || countedAsStartupWarning);
      entry.lifecyclePhase = entry.lifecyclePhase === "runtime" ? "runtime" : startupWindow ? "startup" : "runtime";
      entry.lastSeen = now;
      entry.lastReportTimestamp = report?.timestamp || now;
      entry.seenCount += 1;
      entry.currentlyActive = true;
      entry.clearedAt = "";
    }

    for (const entry of history.entries) {
      if (!activeKeys.has(entry.key) && entry.currentlyActive) {
        entry.currentlyActive = false;
        entry.clearedAt = now;
      }
    }

    history.lastUpdatedAt = now;
    return history;
  }

  function issueHistoryCounts(history) {
    return (history?.entries || []).reduce(
      (counts, entry) => {
        const severity = entry.normalizedSeverity || normalizeIssueSeverity(entry.severity);
        if (severity === "error") {
          counts.errorsSeen += 1;
          if (entry.currentlyActive) counts.activeErrors += 1;
          else counts.resolvedErrors += 1;
        } else if (severity === "warning") {
          counts.warningsSeen += 1;
          if (entry.currentlyActive) counts.activeWarnings += 1;
          else counts.resolvedWarnings += 1;
        }
        return counts;
      },
      {
        errorsSeen: 0,
        warningsSeen: 0,
        activeErrors: 0,
        activeWarnings: 0,
        resolvedErrors: 0,
        resolvedWarnings: 0
      }
    );
  }

  function compactIssueHistory(history) {
    const safeHistory = history || createIssueHistory();
    return {
      schema: safeHistory.schema || "mcel-diagnostics-counter-history-v3",
      pageStartedAt: safeHistory.pageStartedAt || "",
      lastUpdatedAt: safeHistory.lastUpdatedAt || "",
      counts: issueHistoryCounts(safeHistory),
      issues: (safeHistory.entries || []).map((entry) => ({
        severity: entry.severity || "",
        normalizedSeverity: entry.normalizedSeverity || normalizeIssueSeverity(entry.severity),
        originalNormalizedSeverity: entry.originalNormalizedSeverity || normalizeIssueSeverity(entry.severity, {startupWindow: false}),
        countedAsStartupWarning: Boolean(entry.countedAsStartupWarning),
        lifecyclePhase: entry.lifecyclePhase || "",
        code: entry.code || "",
        finding: entry.finding || "",
        recommendedNextProbe: entry.recommendedNextProbe || "",
        firstSeen: entry.firstSeen || "",
        lastSeen: entry.lastSeen || "",
        firstReportTimestamp: entry.firstReportTimestamp || "",
        lastReportTimestamp: entry.lastReportTimestamp || "",
        seenCount: Number(entry.seenCount || 0),
        currentlyActive: Boolean(entry.currentlyActive),
        clearedAt: entry.clearedAt || ""
      }))
    };
  }

  function compactBuckets(report, history) {
    const reportBuckets = report?.buckets || {};
    const historyIssues = compactIssueHistory(history).issues;
    return {
      activeRuntimeIssues: reportBuckets.activeRuntimeIssues || [],
      activeOverlayIssues: reportBuckets.activeOverlayIssues || [],
      activeLayoutIssues: reportBuckets.activeLayoutIssues || [],
      activeSurfaceIssues: reportBuckets.activeSurfaceIssues || [],
      activeContractIssues: reportBuckets.activeContractIssues || [],
      resolvedStartupWarnings: historyIssues.filter((issue) =>
        !issue.currentlyActive && issue.lifecyclePhase === "startup" && issue.normalizedSeverity === "warning"
      ),
      resolvedRuntimeIssues: historyIssues.filter((issue) =>
        !issue.currentlyActive && issue.lifecyclePhase !== "startup"
      )
    };
  }

  function compactPayload(report, counts, history) {
    const safeCounts = counts || {errors: 0, warnings: 0, ok: 0};
    const currentIssues = (Array.isArray(report?.findings) ? report.findings : [])
      .filter((finding) => normalizeIssueSeverity(finding?.severity))
      .map(compactIssue);
    return {
      schema: "mcel-diagnostics-counter-copy-v4",
      widgetVersion: VERSION,
      appId: report?.appId || DEFAULT_APP_ID,
      contractId: report?.contractId || "",
      route: report?.route || (typeof location !== "undefined" ? location.href : ""),
      timestamp: report?.timestamp || new Date().toISOString(),
      verdict: report?.verdict || "unknown",
      counts: safeCounts,
      current: {
        counts: safeCounts,
        issues: currentIssues
      },
      history: compactIssueHistory(history),
      buckets: compactBuckets(report, history),
      primarySurface: report?.summary?.primarySurface || null,
      issues: currentIssues
    };
  }

  function render(el, counts) {
    const errors = formatCount(counts.errors);
    const warnings = formatCount(counts.warnings);
    const ok = formatCount(counts.ok);
    el.dataset.errors = errors;
    el.dataset.warnings = warnings;
    el.dataset.ok = ok;
    el.dataset.state = counts.errors > 0 ? "error" : counts.warnings > 0 ? "warning" : "ok";
    el.setAttribute(
      "aria-label",
      `MCEL diagnostics: ${counts.errors} errors, ${counts.warnings} warnings, ${counts.ok} passing checks. Click to copy current and historical issues.`
    );
    el.innerHTML = [
      '<span class="mcel-diagnostics-counter__bracket">[</span>',
      `<span class="mcel-diagnostics-counter__error">${errors}</span>`,
      '<span class="mcel-diagnostics-counter__slash">/</span>',
      `<span class="mcel-diagnostics-counter__warning">${warnings}</span>`,
      '<span class="mcel-diagnostics-counter__slash">/</span>',
      `<span class="mcel-diagnostics-counter__ok">${ok}</span>`,
      '<span class="mcel-diagnostics-counter__bracket">]</span>'
    ].join("");
  }

  function findMountTarget(doc, appId) {
    const config = appConfig(appId);
    for (const selector of config.anchors || []) {
      const target = doc.querySelector(selector);
      if (target) return target;
    }
    return appRoot(doc, appId);
  }

  function ensureWidget(doc, appId = DEFAULT_APP_ID) {
    const config = appConfig(appId);
    let el = doc.querySelector(config.placeholder);
    if (el) return el;

    const root = appRoot(doc, appId);
    if (!root) return null;

    el = doc.createElement("button");
    el.type = "button";
    el.id = config.id;
    el.className = "mcel-diagnostics-counter";
    el.setAttribute("data-mcel-diagnostics-counter", appId);
    el.setAttribute("aria-label", "MCEL diagnostics counter");
    render(el, {errors: 0, warnings: 0, ok: 0});

    const target = findMountTarget(doc, appId);
    if (target) target.appendChild(el);
    return el;
  }

  function diagnose(appId) {
    const global = typeof window !== "undefined" ? window : globalThis;
    const api = global.MCEL;
    if (api && typeof api.diagnose === "function") return api.diagnose(appId, {silent: true});
    if (global.McelSelfDiagnosis && typeof global.McelSelfDiagnosis.diagnose === "function") return global.McelSelfDiagnosis.diagnose(appId, {silent: true});
    if (global.MCELDiagnosis && typeof global.MCELDiagnosis.diagnose === "function") return global.MCELDiagnosis.diagnose(appId, {silent: true});
    return {
      schema: "mcel-self-diagnosis-report-v2",
      version: VERSION,
      appId,
      contractId: "",
      route: typeof location !== "undefined" ? location.href : "",
      timestamp: new Date().toISOString(),
      verdict: "fail",
      summary: {critical: 1, warning: 0, info: 0},
      findings: [
        {
          severity: "critical",
          code: "diagnosis-api-missing",
          finding: "MCEL diagnosis API is not available.",
          recommendedNextProbe: "mcel.selfDiagnosis"
        }
      ],
      measurements: {}
    };
  }

  async function copyText(text, doc) {
    const nav = typeof navigator !== "undefined" ? navigator : null;
    if (nav?.clipboard?.writeText) {
      await nav.clipboard.writeText(text);
      return true;
    }

    if (!doc?.body) return false;
    const area = doc.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "readonly");
    area.style.position = "fixed";
    area.style.left = "-9999px";
    area.style.top = "0";
    doc.body.appendChild(area);
    area.select();

    let copied = false;
    try {
      copied = Boolean(doc.execCommand && doc.execCommand("copy"));
    } catch {
      copied = false;
    } finally {
      area.remove();
    }
    return copied;
  }

  function flashCopied(el, ok) {
    el.dataset.copyState = ok ? "copied" : "copy-failed";
    window.clearTimeout(el.__mcelDiagnosticsCopyTimer);
    el.__mcelDiagnosticsCopyTimer = window.setTimeout(() => {
      delete el.dataset.copyState;
    }, 1200);
  }

  function inactiveStatus(widget) {
    const counts = {errors: 0, warnings: 0, ok: 0};
    render(widget, counts);
    widget.__mcelDiagnosticsCounts = counts;
    widget.__mcelDiagnosticsInactive = true;
    return {report: null, counts, history: widget.__mcelDiagnosticsIssueHistory || createIssueHistory()};
  }

  function update(widget, options = {}) {
    const appId = options.appId || widget?.getAttribute?.("data-mcel-diagnostics-counter") || DEFAULT_APP_ID;
    const doc = getDocument();
    if (options.skipHidden !== false && doc && !isAppVisible(doc, appId)) {
      return inactiveStatus(widget);
    }

    let report;
    try {
      report = diagnose(appId);
    } catch (error) {
      report = {
        schema: "mcel-self-diagnosis-report-v2",
        version: VERSION,
        appId,
        contractId: "",
        route: typeof location !== "undefined" ? location.href : "",
        timestamp: new Date().toISOString(),
        verdict: "fail",
        summary: {critical: 1, warning: 0, info: 0},
        findings: [
          {
            severity: "critical",
            code: "diagnosis-threw",
            finding: error?.message || String(error),
            recommendedNextProbe: "console"
          }
        ],
        measurements: {}
      };
    }

    const history = widget.__mcelDiagnosticsIssueHistory || createIssueHistory(report?.timestamp || new Date().toISOString());
    const counts = summarizeReport(report, history);
    updateIssueHistory(history, report, report?.timestamp || new Date().toISOString());
    render(widget, counts);
    widget.__mcelDiagnosticsReport = report;
    widget.__mcelDiagnosticsCounts = counts;
    widget.__mcelDiagnosticsIssueHistory = history;
    widget.__mcelDiagnosticsInactive = false;
    return {report, counts, history};
  }

  function bindWidgetClick(widget, appId, doc) {
    if (widget.__mcelDiagnosticsClickBound) return;
    widget.addEventListener("click", async () => {
      const current = update(widget, {appId, skipHidden: false});
      const payload = compactPayload(current.report, current.counts, current.history || widget.__mcelDiagnosticsIssueHistory);
      const text = JSON.stringify(payload, null, 2);
      let copied = false;
      try {
        copied = await copyText(text, doc);
      } catch {
        copied = false;
      }
      if (!copied) {
        try {
          console.log(text);
        } catch {}
      }
      flashCopied(widget, copied);
    });
    widget.__mcelDiagnosticsClickBound = true;
  }

  function mount(appId = DEFAULT_APP_ID, options = {}) {
    const doc = getDocument();
    if (!doc) return null;

    const widget = ensureWidget(doc, appId);
    if (!widget) return null;

    const intervalMs = Number(options.intervalMs || REFRESH_INTERVAL_MS);
    if (widget.__mcelDiagnosticsInterval) {
      window.clearInterval(widget.__mcelDiagnosticsInterval);
    }

    update(widget, {appId});

    widget.__mcelDiagnosticsInterval = window.setInterval(() => {
      update(widget, {appId});
    }, intervalMs);

    bindWidgetClick(widget, appId, doc);
    return widget;
  }

  function mountAll(options = {}) {
    return Object.keys(APP_WIDGETS)
      .map((appId) => mount(appId, options))
      .filter(Boolean);
  }

  function getStatus(appId = DEFAULT_APP_ID) {
    const doc = getDocument();
    const config = appConfig(appId);
    const widget = doc?.querySelector(config.placeholder) || null;
    if (!widget) return null;
    if (!widget.__mcelDiagnosticsReport && isAppVisible(doc, appId)) update(widget, {appId});
    return {
      counts: widget.__mcelDiagnosticsCounts || {errors: 0, warnings: 0, ok: 0},
      report: widget.__mcelDiagnosticsReport || null,
      history: widget.__mcelDiagnosticsIssueHistory || createIssueHistory()
    };
  }

  const api = Object.freeze({
    VERSION,
    REFRESH_INTERVAL_MS,
    APP_WIDGETS,
    mount,
    mountAll,
    refresh(appId = DEFAULT_APP_ID) {
      const doc = getDocument();
      const widget = ensureWidget(doc, appId);
      return widget ? update(widget, {appId, skipHidden: false}) : null;
    },
    refreshAll() {
      return Object.keys(APP_WIDGETS).map((appId) => this.refresh(appId)).filter(Boolean);
    },
    getStatus,
    _private: Object.freeze({
      summarizeReport,
      derivedOkCount,
      compactPayload,
      compactBuckets,
      formatCount,
      createIssueHistory,
      updateIssueHistory,
      compactIssueHistory,
      issueHistoryCounts,
      isStartupWindow,
      normalizeIssueSeverity,
      STARTUP_ERROR_WARNING_MS,
      APP_WIDGETS
    })
  });

  const global = typeof window !== "undefined" ? window : globalThis;
  try {
    global.MCELDiagnosticsCounterWidget = api;
  } catch {}

  function boot() {
    mountAll();
  }

  const doc = getDocument();
  if (doc?.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", boot, {once: true});
  } else {
    boot();
  }
})();
