(() => {
  "use strict";

  const VERSION = "mcel-diagnostics-counter-widget-v3";
  const STARTUP_ERROR_WARNING_MS = 5000;
  const REFRESH_INTERVAL_MS = 30000;
  const DEFAULT_APP_ID = "code-editor";
  const SELECTORS = {
    root: "#code-editor-app",
    placeholder: "#code-editor-diagnostics-counter",
    titlebar: "#code-editor-app .code-studio-title-actions, #code-editor-app .code-studio-titlebar"
  };

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

    const host = measurements.surfaces?.monacoHost || primarySurface.host;
    const editor = measurements.surfaces?.monacoEditor || primarySurface.editor;

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
      schema: "mcel-diagnostics-counter-history-v2",
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
    const reportTimestamp = report?.timestamp || now;
    const startupWindow = isStartupWindow(history, now);
    const activeKeys = new Set();

    for (const finding of findings) {
      if (!shouldRememberIssue(finding)) continue;

      const key = issueKey(finding);
      const issue = compactIssue(finding);
      const originalNormalizedSeverity = normalizeIssueSeverity(issue.severity);
      const normalizedSeverity = normalizeIssueSeverity(issue.severity, {startupWindow});
      const startupDowngraded = originalNormalizedSeverity === "error" && normalizedSeverity === "warning";
      activeKeys.add(key);

      let entry = findHistoryEntry(history, key);
      if (!entry) {
        entry = {
          key,
          severity: issue.severity,
          normalizedSeverity,
          originalNormalizedSeverity,
          countedAsStartupWarning: startupDowngraded,
          lifecyclePhase: startupWindow ? "startup" : "runtime",
          code: issue.code,
          finding: issue.finding,
          recommendedNextProbe: issue.recommendedNextProbe,
          firstSeen: now,
          lastSeen: now,
          firstReportTimestamp: reportTimestamp,
          lastReportTimestamp: reportTimestamp,
          seenCount: 0,
          currentlyActive: false,
          clearedAt: ""
        };
        history.entries.push(entry);
      }

      entry.severity = issue.severity;
      entry.normalizedSeverity = normalizedSeverity;
      entry.originalNormalizedSeverity = originalNormalizedSeverity;
      entry.countedAsStartupWarning = Boolean(startupDowngraded || entry.countedAsStartupWarning);
      entry.lifecyclePhase = entry.lifecyclePhase === "runtime" || !startupWindow ? "runtime" : "startup";
      entry.code = issue.code;
      entry.finding = issue.finding;
      entry.recommendedNextProbe = issue.recommendedNextProbe;
      entry.lastSeen = now;
      entry.lastReportTimestamp = reportTimestamp;
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
        const bucket = entry.normalizedSeverity === "error" ? "errors" : entry.normalizedSeverity === "warning" ? "warnings" : "";
        if (!bucket) return counts;

        counts[`${bucket}Seen`] += 1;
        if (entry.currentlyActive) counts[`active${bucket[0].toUpperCase()}${bucket.slice(1)}`] += 1;
        else counts[`resolved${bucket[0].toUpperCase()}${bucket.slice(1)}`] += 1;
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
      schema: safeHistory.schema || "mcel-diagnostics-counter-history-v2",
      pageStartedAt: safeHistory.pageStartedAt || "",
      lastUpdatedAt: safeHistory.lastUpdatedAt || "",
      counts: issueHistoryCounts(safeHistory),
      issues: (safeHistory.entries || [])
        .map((entry) => ({
          severity: entry.severity || "",
          normalizedSeverity: entry.normalizedSeverity || normalizeIssueSeverity(entry.severity),
          originalNormalizedSeverity: entry.originalNormalizedSeverity || normalizeIssueSeverity(entry.severity),
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
        .sort((left, right) => String(right.lastSeen).localeCompare(String(left.lastSeen)))
    };
  }

  function compactPayload(report, counts, history) {
    const currentIssues = Array.isArray(report?.findings) ? report.findings.map(compactIssue) : [];
    const safeCounts = {
      errors: clampCount(counts.errors),
      warnings: clampCount(counts.warnings),
      ok: clampCount(counts.ok)
    };
    return {
      schema: "mcel-diagnostics-counter-copy-v3",
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

  function findMountTarget(doc) {
    return doc.querySelector(SELECTORS.titlebar);
  }

  function ensureWidget(doc) {
    let el = doc.querySelector(SELECTORS.placeholder);
    if (el) return el;

    el = doc.createElement("button");
    el.type = "button";
    el.id = "code-editor-diagnostics-counter";
    el.className = "mcel-diagnostics-counter";
    el.setAttribute("data-mcel-diagnostics-counter", "code-editor");
    el.setAttribute("aria-label", "MCEL diagnostics counter");
    render(el, {errors: 0, warnings: 0, ok: 0});

    const target = findMountTarget(doc);
    if (target) target.appendChild(el);
    return el;
  }

  function diagnose(appId) {
    const global = typeof window !== "undefined" ? window : globalThis;
    const api = global.MCEL;
    if (api && typeof api.diagnose === "function") {
      return api.diagnose(appId);
    }
    if (global.McelSelfDiagnosis && typeof global.McelSelfDiagnosis.diagnose === "function") {
      return global.McelSelfDiagnosis.diagnose(appId);
    }
    if (global.MCELDiagnosis && typeof global.MCELDiagnosis.diagnose === "function") {
      return global.MCELDiagnosis.diagnose(appId);
    }
    return {
      schema: "mcel-self-diagnosis-report-v1",
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

  function update(widget, options = {}) {
    const appId = options.appId || DEFAULT_APP_ID;
    let report;
    try {
      report = diagnose(appId);
    } catch (error) {
      report = {
        schema: "mcel-self-diagnosis-report-v1",
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
    return {report, counts, history};
  }

  function mount(appId = DEFAULT_APP_ID, options = {}) {
    const doc = getDocument();
    if (!doc) return null;

    const widget = ensureWidget(doc);
    if (!widget) return null;

    const intervalMs = Number(options.intervalMs || REFRESH_INTERVAL_MS);
    if (widget.__mcelDiagnosticsInterval) {
      window.clearInterval(widget.__mcelDiagnosticsInterval);
    }

    update(widget, {appId});

    widget.__mcelDiagnosticsInterval = window.setInterval(() => {
      update(widget, {appId});
    }, intervalMs);

    if (!widget.__mcelDiagnosticsClickBound) {
      widget.addEventListener("click", async () => {
        const current = update(widget, {appId});
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

    return widget;
  }

  function getStatus(appId = DEFAULT_APP_ID) {
    const doc = getDocument();
    const widget = doc?.querySelector(SELECTORS.placeholder) || null;
    if (!widget) return null;
    if (!widget.__mcelDiagnosticsReport) update(widget, {appId});
    return {
      counts: widget.__mcelDiagnosticsCounts || {errors: 0, warnings: 0, ok: 0},
      report: widget.__mcelDiagnosticsReport || null,
      history: widget.__mcelDiagnosticsIssueHistory || createIssueHistory()
    };
  }

  const api = Object.freeze({
    VERSION,
    REFRESH_INTERVAL_MS,
    mount,
    refresh(appId = DEFAULT_APP_ID) {
      const doc = getDocument();
      const widget = ensureWidget(doc);
      return widget ? update(widget, {appId}) : null;
    },
    getStatus,
    _private: Object.freeze({
      summarizeReport,
      derivedOkCount,
      compactPayload,
      formatCount,
      createIssueHistory,
      updateIssueHistory,
      compactIssueHistory,
      issueHistoryCounts,
      isStartupWindow,
      normalizeIssueSeverity,
      STARTUP_ERROR_WARNING_MS
    })
  });

  const global = typeof window !== "undefined" ? window : globalThis;
  try {
    global.MCELDiagnosticsCounterWidget = api;
  } catch {}

  function boot() {
    mount(DEFAULT_APP_ID);
  }

  const doc = getDocument();
  if (doc?.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", boot, {once: true});
  } else {
    boot();
  }
})();
