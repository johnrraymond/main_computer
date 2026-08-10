(function installCodeEditorBrowserSmoke(globalRoot, factory) {
  "use strict";

  const api = factory(globalRoot || {});

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  if (globalRoot) {
    try {
      globalRoot.MainComputerCodeEditorBrowserSmoke = api;
    } catch {}

    try {
      const existing = globalRoot.MCEL || {};
      globalRoot.MCEL = Object.assign({}, existing, {
        codeEditorBrowserSmoke: api
      });
    } catch {}
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function buildCodeEditorBrowserSmoke(defaultGlobal) {
  "use strict";

  const VERSION = "code-editor-browser-smoke-v1";
  const SCHEMA = "code-editor.browser-smoke.v1";
  const DEFAULT_APP_SELECTOR = "#code-editor-app";
  const DEFAULT_MIN_WIDTH = 320;
  const DEFAULT_MIN_HEIGHT = 220;

  function text(value) {
    return value == null ? "" : String(value);
  }

  function nowIso(globalRef) {
    const DateRef = (globalRef && globalRef.Date) || Date;
    try {
      return new DateRef().toISOString();
    } catch {
      return new Date().toISOString();
    }
  }

  function q(scope, selector) {
    if (!scope || typeof scope.querySelector !== "function") return null;
    try {
      return scope.querySelector(selector);
    } catch {
      return null;
    }
  }

  function qa(scope, selector) {
    if (!scope || typeof scope.querySelectorAll !== "function") return [];
    try {
      return Array.from(scope.querySelectorAll(selector) || []);
    } catch {
      return [];
    }
  }

  function datasetValue(element, name) {
    return element && element.dataset ? text(element.dataset[name]) : "";
  }

  function attr(element, name) {
    if (!element || typeof element.getAttribute !== "function") return "";
    const value = element.getAttribute(name);
    return value == null ? "" : text(value);
  }

  function computed(globalRef, element) {
    const fallback = {
      display: "",
      position: "",
      width: "",
      height: "",
      minWidth: "",
      maxWidth: "",
      overflow: "",
      gridTemplateColumns: "",
      gridTemplateRows: "",
      gridColumn: "",
      gridRow: ""
    };
    if (!element) return fallback;
    const getter = (globalRef && globalRef.getComputedStyle) || (typeof getComputedStyle === "function" ? getComputedStyle : null);
    if (!getter) return fallback;
    try {
      const style = getter(element);
      return style || fallback;
    } catch {
      return fallback;
    }
  }

  function round(value) {
    return Math.round(Number(value) || 0);
  }

  function rectOf(element) {
    if (!element || typeof element.getBoundingClientRect !== "function") {
      return {x: 0, y: 0, width: 0, height: 0, right: 0, bottom: 0};
    }
    try {
      const rect = element.getBoundingClientRect();
      const width = round(rect.width);
      const height = round(rect.height);
      const x = round(rect.x == null ? rect.left : rect.x);
      const y = round(rect.y == null ? rect.top : rect.y);
      return {
        x,
        y,
        width,
        height,
        right: round(rect.right == null ? x + width : rect.right),
        bottom: round(rect.bottom == null ? y + height : rect.bottom)
      };
    } catch {
      return {x: 0, y: 0, width: 0, height: 0, right: 0, bottom: 0};
    }
  }

  function parseTracks(value) {
    return text(value)
      .match(/-?\d+(?:\.\d+)?px/g)
      ?.map((part) => Number.parseFloat(part)) || [];
  }

  function hasZeroTrack(value) {
    return parseTracks(value).some((track) => track <= 1);
  }

  function measure(globalRef, element, selector) {
    const style = computed(globalRef, element);
    const rect = rectOf(element);
    return {
      exists: !!element,
      selector,
      tag: element && element.tagName ? text(element.tagName).toLowerCase() : "",
      id: element && element.id ? text(element.id) : "",
      className: element && element.className ? text(element.className) : "",
      rect,
      visible: !!element && style.display !== "none" && attr(element, "hidden") === "" && attr(element, "aria-hidden") !== "true" && rect.width > 0 && rect.height > 0,
      display: text(style.display),
      position: text(style.position),
      widthCss: text(style.width),
      heightCss: text(style.height),
      minWidth: text(style.minWidth),
      maxWidth: text(style.maxWidth),
      overflow: text(style.overflow),
      gridTemplateColumns: text(style.gridTemplateColumns),
      gridTemplateRows: text(style.gridTemplateRows),
      gridColumn: text(style.gridColumn),
      gridRow: text(style.gridRow),
      data: element && element.dataset ? Object.assign({}, element.dataset) : {},
      ariaHidden: attr(element, "aria-hidden"),
      inlineStyle: attr(element, "style")
    };
  }

  function severityCounts(checks) {
    return checks.reduce((counts, check) => {
      if (check.ok) counts.ok += 1;
      else if (check.severity === "warning") counts.warnings += 1;
      else counts.errors += 1;
      return counts;
    }, {errors: 0, warnings: 0, ok: 0});
  }

  function buildCheck(checks, ok, code, finding, evidence = {}, severity = "error") {
    checks.push({
      ok: !!ok,
      severity: ok ? "ok" : severity,
      code,
      finding,
      evidence
    });
  }

  function diagnosisApi(globalRef) {
    return (globalRef && globalRef.McelSelfDiagnosis) ||
      (globalRef && globalRef.MCEL && globalRef.MCEL.selfDiagnosis) ||
      (globalRef && globalRef.MCELDiagnosis) ||
      null;
  }

  function diagnoseIfRequested(globalRef, checks, options) {
    const requireDiagnosis = options.requireDiagnosis === true;
    const includeDiagnosis = options.includeDiagnosis !== false || requireDiagnosis;
    if (!includeDiagnosis) return null;

    const api = diagnosisApi(globalRef);
    if (!api || typeof api.diagnose !== "function") {
      buildCheck(
        checks,
        !requireDiagnosis,
        "diagnostics-api-available",
        "MCEL self-diagnosis API is available for raw verdict smoke.",
        {},
        "warning"
      );
      return null;
    }

    let report = null;
    try {
      report = api.diagnose("code-editor", {silent: true, focus: "browser-smoke"});
    } catch (error) {
      buildCheck(
        checks,
        false,
        "diagnostics-no-throw",
        "MCEL self-diagnosis runs without throwing.",
        {message: text(error && error.message ? error.message : error)},
        "error"
      );
      return null;
    }

    const findings = Array.isArray(report?.findings) ? report.findings : [];
    const criticals = findings.filter((finding) => finding && finding.severity === "critical");
    const errors = findings.filter((finding) => finding && (finding.severity === "error" || finding.normalizedSeverity === "error"));
    const rawPass = report && (report.verdict === "pass" || (criticals.length === 0 && errors.length === 0));

    buildCheck(
      checks,
      rawPass,
      "diagnostics-raw-verdict-pass",
      "Code Editor MCEL diagnosis has no critical/error findings.",
      {
        verdict: report ? report.verdict : "",
        criticalCodes: criticals.map((finding) => finding.code),
        errorCodes: errors.map((finding) => finding.code)
      },
      "error"
    );

    return report;
  }

  function runtimeState(globalRef) {
    const runtime = globalRef && globalRef.MainComputerCodeEditorRuntime;
    if (!runtime) return null;
    try {
      return typeof runtime.state === "function" ? runtime.state() : runtime.state || null;
    } catch {
      return null;
    }
  }

  function runtimeDebug(globalRef) {
    const runtime = globalRef && globalRef.MainComputerCodeEditorRuntime;
    if (!runtime || typeof runtime.monacoDebug !== "function") return null;
    try {
      return runtime.monacoDebug();
    } catch {
      return null;
    }
  }

  function run(options = {}) {
    const globalRef = options.global || defaultGlobal || {};
    const documentRef = options.document || globalRef.document || null;
    const app = options.rootElement || q(documentRef, options.appSelector || DEFAULT_APP_SELECTOR);
    const scope = app || documentRef;
    const minWidth = Number(options.minWidth || DEFAULT_MIN_WIDTH);
    const minHeight = Number(options.minHeight || DEFAULT_MIN_HEIGHT);
    const checks = [];

    const shell = q(scope, ".code-studio-shell");
    const titlebar = q(scope, ".code-studio-titlebar");
    const body = q(scope, ".code-studio-body");
    const activitybar = q(scope, ".code-studio-activitybar");
    const sidebar = q(scope, ".code-studio-sidebar");
    const editorGroup = q(scope, ".code-studio-editor-group");
    const activePane = q(scope, '[data-code-studio-pane="runtime"].active') || q(scope, ".code-studio-editor-pane.active");
    const runtimePreview = q(scope, "#code-studio-runtime-preview");
    const authoringSurface = q(scope, ".code-studio-monaco-authoring-surface");
    const monacoHost = q(scope, "#code-studio-runtime-monaco");
    const monacoEditor = q(scope, "#code-studio-runtime-monaco .monaco-editor") || q(scope, ".monaco-editor");
    const inspector = q(scope, ".code-studio-inspector");
    const proofDock = q(scope, "#code-studio-bottom-panel") || q(scope, ".code-studio-proof-dock");
    const activePanes = qa(scope, "[data-code-studio-pane].active");

    const measurements = {
      app: measure(globalRef, app, "#code-editor-app"),
      shell: measure(globalRef, shell, ".code-studio-shell"),
      titlebar: measure(globalRef, titlebar, ".code-studio-titlebar"),
      body: measure(globalRef, body, ".code-studio-body"),
      activitybar: measure(globalRef, activitybar, ".code-studio-activitybar"),
      sidebar: measure(globalRef, sidebar, ".code-studio-sidebar"),
      editorGroup: measure(globalRef, editorGroup, ".code-studio-editor-group"),
      activePane: measure(globalRef, activePane, '[data-code-studio-pane="runtime"].active'),
      runtimePreview: measure(globalRef, runtimePreview, "#code-studio-runtime-preview"),
      authoringSurface: measure(globalRef, authoringSurface, ".code-studio-monaco-authoring-surface"),
      monacoHost: measure(globalRef, monacoHost, "#code-studio-runtime-monaco"),
      monacoEditor: measure(globalRef, monacoEditor, ".monaco-editor"),
      inspector: measure(globalRef, inspector, ".code-studio-inspector"),
      proofDock: measure(globalRef, proofDock, "#code-studio-bottom-panel")
    };

    const shellTracks = parseTracks(measurements.shell.gridTemplateColumns);
    const bodyTracks = parseTracks(measurements.body.gridTemplateColumns);
    const defaultMode = datasetValue(app, "codeEditorMode") !== "mcel";

    buildCheck(checks, !!app, "root-present", "Code Editor root exists.", measurements.app);
    buildCheck(checks, !!shell, "shell-present", "Preserved Code Studio shell exists.", measurements.shell);
    buildCheck(
      checks,
      datasetValue(app, "codeEditorRuntimeSurfaceMode") === "legacy-fidelity",
      "legacy-fidelity-surface-mode",
      "Runtime surface mode remains legacy-fidelity.",
      {actual: datasetValue(app, "codeEditorRuntimeSurfaceMode")}
    );
    buildCheck(
      checks,
      datasetValue(app, "codeEditorMode") === "legacy-fidelity" || datasetValue(app, "codeEditorMode") === "mcel",
      "legacy-fidelity-mode-alias",
      "Default runtime mode stays on the legacy-fidelity authoring alias unless MCEL tools mode is explicitly active.",
      {actual: datasetValue(app, "codeEditorMode")}
    );
    buildCheck(
      checks,
      measurements.shell.display === "grid" && shellTracks.length === 1 && !hasZeroTrack(measurements.shell.gridTemplateColumns),
      "shell-single-column-grid",
      "Shell grid has one non-zero explicit column; resize/zoom must not create implicit zero-width shell columns.",
      {gridTemplateColumns: measurements.shell.gridTemplateColumns, tracks: shellTracks}
    );
    buildCheck(
      checks,
      measurements.titlebar.rect.width > 0 && measurements.body.rect.width > 0,
      "shell-direct-children-nonzero",
      "Titlebar and workbench body remain in non-zero shell tracks.",
      {
        titlebarWidth: measurements.titlebar.rect.width,
        bodyWidth: measurements.body.rect.width,
        titlebarGridColumn: measurements.titlebar.gridColumn,
        bodyGridColumn: measurements.body.gridColumn
      }
    );
    buildCheck(
      checks,
      measurements.body.display === "grid" && bodyTracks.length >= 2 && !hasZeroTrack(measurements.body.gridTemplateColumns),
      "workbench-grid-nonzero-tracks",
      "Workbench grid has non-zero visible tracks for the active layout.",
      {gridTemplateColumns: measurements.body.gridTemplateColumns, tracks: bodyTracks}
    );
    buildCheck(
      checks,
      activePanes.length === 1 && activePane && attr(activePane, "data-code-studio-pane") === "runtime",
      "runtime-pane-is-single-active-pane",
      "Exactly one active pane exists and it is the runtime selected-file editor pane.",
      {activePaneCount: activePanes.length, activePane: attr(activePane, "data-code-studio-pane")}
    );
    buildCheck(
      checks,
      measurements.editorGroup.rect.width > 0 && measurements.runtimePreview.rect.width > 0,
      "editor-group-and-runtime-preview-nonzero",
      "Editor group and runtime preview remain non-zero after resize/zoom.",
      {editorGroup: measurements.editorGroup.rect, runtimePreview: measurements.runtimePreview.rect}
    );
    buildCheck(
      checks,
      measurements.monacoHost.visible && measurements.monacoHost.rect.width >= minWidth && measurements.monacoHost.rect.height >= minHeight,
      "monaco-primary-surface-usable",
      "Monaco primary host is visible and above smoke minimum dimensions.",
      {host: measurements.monacoHost.rect, minWidth, minHeight}
    );
    buildCheck(
      checks,
      measurements.monacoEditor.visible && measurements.monacoEditor.rect.width >= minWidth && measurements.monacoEditor.rect.height >= minHeight,
      "monaco-editor-visible",
      "Monaco editor DOM is visible and above smoke minimum dimensions.",
      {editor: measurements.monacoEditor.rect, minWidth, minHeight}
    );
    buildCheck(
      checks,
      !defaultMode || !measurements.proofDock.visible,
      "proof-dock-hidden-by-default",
      "Default legacy-fidelity mode keeps the MCEL proof/evidence dock hidden.",
      {defaultMode, proofDock: measurements.proofDock.rect, display: measurements.proofDock.display, ariaHidden: measurements.proofDock.ariaHidden}
    );

    const diagnosis = diagnoseIfRequested(globalRef, checks, options);
    const state = runtimeState(globalRef);
    const debug = runtimeDebug(globalRef);
    const counts = severityCounts(checks);

    const result = {
      schema: SCHEMA,
      version: VERSION,
      appId: "code-editor",
      timestamp: nowIso(globalRef),
      verdict: counts.errors > 0 ? "fail" : "pass",
      counts,
      checks,
      measurements,
      runtime: {
        activePath: state && state.activeFile ? text(state.activeFile.path) : "",
        debug
      },
      diagnosis: diagnosis ? {
        verdict: diagnosis.verdict,
        counts: diagnosis.summary ? {
          errors: diagnosis.summary.errors || 0,
          warnings: diagnosis.summary.warnings || 0,
          ok: diagnosis.summary.ok || 0
        } : null,
        issueCodes: Array.isArray(diagnosis.findings) ? diagnosis.findings.map((finding) => finding.code) : []
      } : null
    };

    try {
      globalRef.__CE_CODE_EDITOR_BROWSER_SMOKE_LAST__ = result;
    } catch {}

    return result;
  }

  function print(options = {}) {
    const globalRef = options.global || defaultGlobal || {};
    const report = run(options);
    const logger = (globalRef && globalRef.console) || (typeof console !== "undefined" ? console : null);
    if (logger) {
      try {
        logger.group?.(`Code Editor browser smoke: ${report.verdict}`);
        logger.table?.(report.checks.map((check) => ({
          ok: check.ok,
          severity: check.severity,
          code: check.code,
          finding: check.finding
        })));
        logger.log?.(report);
        logger.groupEnd?.();
      } catch {}
    }
    return report;
  }

  function copy(options = {}) {
    const report = run(options);
    return JSON.stringify(report, null, 2);
  }

  function startResizeWatch(options = {}) {
    const globalRef = options.global || defaultGlobal || {};
    const state = {
      schema: "code-editor.browser-smoke.resize-watch.v1",
      startedAt: nowIso(globalRef),
      baseline: run(options),
      latest: null,
      history: []
    };

    const schedule = () => {
      const timeout = globalRef.setTimeout || setTimeout;
      const clear = globalRef.clearTimeout || clearTimeout;
      if (state.timer) clear(state.timer);
      state.timer = timeout(() => {
        state.latest = run(options);
        state.history.push(state.latest);
        if (state.history.length > 20) state.history.shift();
        try {
          globalRef.__CE_CODE_EDITOR_BROWSER_SMOKE_WATCH__ = state;
        } catch {}
      }, Number(options.delayMs || 120));
    };

    if (typeof globalRef.addEventListener === "function") {
      globalRef.addEventListener("resize", schedule);
    }
    if (globalRef.visualViewport && typeof globalRef.visualViewport.addEventListener === "function") {
      globalRef.visualViewport.addEventListener("resize", schedule);
    }

    state.snapshot = () => {
      state.latest = run(options);
      state.history.push(state.latest);
      return state.latest;
    };
    state.stop = () => {
      if (typeof globalRef.removeEventListener === "function") {
        globalRef.removeEventListener("resize", schedule);
      }
      if (globalRef.visualViewport && typeof globalRef.visualViewport.removeEventListener === "function") {
        globalRef.visualViewport.removeEventListener("resize", schedule);
      }
      return state;
    };

    try {
      globalRef.__CE_CODE_EDITOR_BROWSER_SMOKE_WATCH__ = state;
    } catch {}

    return state;
  }

  return Object.freeze({
    VERSION,
    SCHEMA,
    run,
    print,
    copy,
    startResizeWatch
  });
});
