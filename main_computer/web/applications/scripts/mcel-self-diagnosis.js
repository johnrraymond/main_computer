(() => {
  "use strict";

  const VERSION = "mcel-self-diagnosis-v2";
  const REPORT_SCHEMA = "mcel-self-diagnosis-report-v2";

  const APP_MODE_DEFAULTS = Object.freeze({
    "code-editor": "authoring",
    calculator: "default",
    "file-explorer": "default",
    "git-tools": "default",
    "website-builder": "default",
    "mcel-lab": "default"
  });

  const COMMON_OVERLAY_SELECTORS = Object.freeze([
    "#mc-widget-editor-pane.open",
    ".mc-widget-selection:not([hidden])",
    ".mc-widget-dock-preview:not([hidden])",
    ".code-studio-proof-dock",
    "#code-studio-bottom-panel",
    ".floating-tab",
    ".side-tab",
    ".vertical-tab",
    "[data-mcel-proof-surface]",
    "[data-code-studio-panel=\"assistant\"]"
  ]);

  const FALLBACK_CONTRACTS = deepFreeze({
    "code-editor": {
      contractId: "code-editor.contract.authoring.monaco-golden-path",
      appId: "code-editor",
      mode: "authoring",
      intent: "Expose one usable selected-source editor work surface with owned project-selection context, supporting context/feedback projection, and ambient status feedback.",
      derivedFromBlockTypes: [
        "mcel-region",
        "mcel-requirement",
        "mcel-acceptance",
        "mcel-boundary",
        "mcel-source-binding",
        "mcel-test-binding",
        "mcel-runtime-check",
        "mcel-form-primitive"
      ],
      primarySurface: {
        id: "code-editor.surface.monaco-selected-file-editor",
        label: "Monaco selected-file editor",
        hostSelector: "#code-studio-runtime-monaco",
        editorSelector: ".monaco-editor",
        minWidth: 520,
        minHeight: 320
      },
      requiredRegions: [
        {id: "code-editor.region.root", selector: "#code-editor-app", label: "Code Editor app root"},
        {id: "code-editor.region.explorer", selector: ".code-studio-sidebar", label: "Explorer"},
        {id: "code-editor.region.editor-group", selector: ".code-studio-editor-group", label: "Editor group"},
        {id: "code-editor.region.status-bar", selector: ".code-studio-statusbar", label: "Status bar"}
      ],
      optionalRegions: [
        {id: "code-editor.region.inspector", selector: ".code-studio-inspector", label: "Supporting reasoning/evidence projection", role: "secondary-context-feedback"}
      ],
      forbiddenRegions: [
        {id: "code-editor.forbidden.source-pane", selector: "[data-code-studio-pane=\"source\"]", label: "MCEL source model pane"},
        {id: "code-editor.forbidden.serialized-pane", selector: "[data-code-studio-pane=\"serialized\"]", label: "Serialized output pane"},
        {id: "code-editor.forbidden.contract-pane", selector: "[data-code-studio-pane=\"contract\"]", label: "Contract report pane"},
        {id: "code-editor.forbidden.runtime-scaffold.window", selector: ".code-studio-runtime-window", label: "Generated runtime window scaffold"},
        {id: "code-editor.forbidden.runtime-scaffold.layout", selector: ".code-studio-runtime-layout", label: "Generated runtime layout scaffold"},
        {id: "code-editor.forbidden.runtime-file-rail", selector: ".code-studio-runtime-files", label: "Generated runtime file rail"},
        {id: "code-editor.forbidden.fallback-textarea", selector: "#code-studio-runtime-draft, .code-studio-runtime-fallback", label: "Fallback textarea"},
        {id: "code-editor.forbidden.proof-dock", selector: ".code-studio-proof-dock, #code-studio-bottom-panel", label: "MCEL proof/evidence dock"},
        {id: "code-editor.forbidden.widget-overlay", selector: "#mc-widget-editor-pane.open, .mc-widget-selection:not([hidden]), .mc-widget-dock-preview:not([hidden])", label: "Active widget editor overlay"}
      ],
      lifecycleAssertions: [
        "startup-authoring-mode-has-one-primary-editor",
        "file-click-keeps-one-primary-editor",
        "mcel-diagnostics-hidden-in-authoring"
      ]
    },
    calculator: {
      contractId: "calculator.contract.default.app-health",
      appId: "calculator",
      mode: "default",
      intent: "Expose a usable calculator workspace and keep diagnostic overlays out of the default path.",
      derivedFromBlockTypes: ["mcel-runtime-check"],
      primarySurface: {
        id: "calculator.surface.workspace",
        label: "Calculator workspace",
        hostSelector: ".calculator-workspace",
        editorSelector: ".calculator-workspace",
        minWidth: 420,
        minHeight: 320
      },
      requiredRegions: [
        {id: "calculator.region.root", selector: "#calculator-app", label: "Calculator app root"},
        {id: "calculator.region.shell", selector: ".calculator-shell", label: "Calculator shell"},
        {id: "calculator.region.workspace", selector: ".calculator-workspace", label: "Calculator workspace"},
        {id: "calculator.region.display", selector: "#calculator-display", label: "Calculator display"}
      ],
      forbiddenRegions: [],
      lifecycleAssertions: []
    },
    "file-explorer": {
      contractId: "file-explorer.contract.default.app-health",
      appId: "file-explorer",
      mode: "default",
      intent: "Expose a usable file browsing surface.",
      derivedFromBlockTypes: ["mcel-runtime-check"],
      primarySurface: {
        id: "file-explorer.surface.main",
        label: "File Explorer main browsing surface",
        hostSelector: ".file-explorer-main",
        editorSelector: ".file-explorer-main",
        minWidth: 420,
        minHeight: 320
      },
      requiredRegions: [
        {id: "file-explorer.region.root", selector: "#file-explorer-app", label: "File Explorer app root"},
        {id: "file-explorer.region.roots", selector: ".file-explorer-roots-panel", label: "Roots panel"},
        {id: "file-explorer.region.main", selector: ".file-explorer-main", label: "Main browsing surface"},
        {id: "file-explorer.region.list", selector: "#file-explorer-list", label: "File list"}
      ],
      forbiddenRegions: [],
      lifecycleAssertions: []
    },
    "git-tools": {
      contractId: "git-tools.contract.default.app-health",
      appId: "git-tools",
      mode: "default",
      intent: "Expose a usable Git workflow surface.",
      derivedFromBlockTypes: ["mcel-runtime-check"],
      primarySurface: {
        id: "git-tools.surface.workflow",
        label: "Git Tools workflow surface",
        hostSelector: "#git-project-workflow-surface",
        editorSelector: "#git-project-workflow-surface",
        minWidth: 420,
        minHeight: 320
      },
      requiredRegions: [
        {id: "git-tools.region.root", selector: "#git-tools-app", label: "Git Tools app root"},
        {id: "git-tools.region.project-selector", selector: "#git-project-selector-panel", label: "Project selector"},
        {id: "git-tools.region.workflow", selector: "#git-project-workflow-surface", label: "Project workflow surface"}
      ],
      forbiddenRegions: [],
      lifecycleAssertions: []
    },
    "website-builder": {
      contractId: "website-builder.contract.default.app-health",
      appId: "website-builder",
      mode: "default",
      intent: "Expose a usable Website Builder preview/design surface.",
      derivedFromBlockTypes: ["mcel-runtime-check"],
      primarySurface: {
        id: "website-builder.surface.preview",
        label: "Website Builder preview surface",
        hostSelector: ".website-builder-preview",
        editorSelector: ".website-builder-preview",
        minWidth: 420,
        minHeight: 320
      },
      requiredRegions: [
        {id: "website-builder.region.root", selector: "#website-builder-app", label: "Website Builder app root"},
        {id: "website-builder.region.main", selector: ".website-builder-main", label: "Website Builder shell"},
        {id: "website-builder.region.summary", selector: ".website-builder-summary", label: "Website summary"},
        {id: "website-builder.region.preview", selector: ".website-builder-preview", label: "Preview/design surface"},
        {id: "website-builder.region.inspector", selector: ".website-builder-inspector", label: "Inspector"}
      ],
      forbiddenRegions: [],
      lifecycleAssertions: []
    }
  });

  const CODE_EDITOR_AUTHORING_CONTRACT = FALLBACK_CONTRACTS["code-editor"];

  function normalizeList(value) {
    return Array.isArray(value) ? value.filter(Boolean) : [];
  }

  function fallbackContractFor(appId = "code-editor") {
    return FALLBACK_CONTRACTS[appId] || null;
  }

  function normalizePrimarySurface(surface, fallback) {
    const input = surface && typeof surface === "object" ? surface : {};
    const safeFallback = fallback?.primarySurface || CODE_EDITOR_AUTHORING_CONTRACT.primarySurface;
    const hostSelector = String(input.hostSelector || input.host_selector || safeFallback.hostSelector || "");
    const editorSelector = String(input.editorSelector || input.editor_selector || safeFallback.editorSelector || hostSelector || "");
    return {
      id: String(input.id || safeFallback.id || ""),
      label: String(input.label || safeFallback.label || input.failure_message || ""),
      hostSelector,
      editorSelector,
      minWidth: Number(input.minWidth || input.min_width || safeFallback.minWidth || 1),
      minHeight: Number(input.minHeight || input.min_height || safeFallback.minHeight || 1)
    };
  }

  function normalizeRegionEntries(entries, fallbackEntries) {
    const source = Array.isArray(entries) && entries.length ? entries : fallbackEntries || [];
    return normalizeList(source).map((entry) => ({
      id: String(entry?.id || entry?.selector || ""),
      selector: String(entry?.selector || entry?.id || ""),
      label: String(entry?.label || entry?.id || entry?.selector || ""),
      role: String(entry?.role || entry?.surfaceRole || entry?.surface_role || ""),
      minWidth: Number(entry?.minWidth || entry?.min_width || 0),
      minHeight: Number(entry?.minHeight || entry?.min_height || 0),
      maxWidthRatio: Number(entry?.maxWidthRatio || entry?.max_width_ratio || 0)
    })).filter((entry) => entry.selector);
  }

  function normalizeDiagnosisContract(contract, fallback = CODE_EDITOR_AUTHORING_CONTRACT) {
    const input = contract && typeof contract === "object" ? contract : fallback;
    const safeFallback = fallback || CODE_EDITOR_AUTHORING_CONTRACT;
    const appId = String(input.appId || input.app || input.app_id || safeFallback.appId || "code-editor");
    const mode = String(input.mode || safeFallback.mode || APP_MODE_DEFAULTS[appId] || "default");
    return {
      contractId: String(input.contractId || input.contract_id || safeFallback.contractId || `${appId}.contract.${mode}`),
      appId,
      mode,
      intent: String(input.intent || safeFallback.intent || ""),
      source: String(input.source || "static-fallback"),
      derivedFromBlockTypes: normalizeList(input.derivedFromBlockTypes || input.derived_from_block_types || safeFallback.derivedFromBlockTypes),
      primarySurface: normalizePrimarySurface(input.primarySurface || input.primary_surface, safeFallback),
      requiredRegions: normalizeRegionEntries(input.requiredRegions || input.required_regions, safeFallback.requiredRegions),
      optionalRegions: normalizeRegionEntries(
        input.optionalRegions || input.optional_regions || input.allowedRegions || input.allowed_regions,
        safeFallback.optionalRegions || safeFallback.allowedRegions
      ),
      forbiddenRegions: normalizeRegionEntries(input.forbiddenRegions || input.forbidden_regions, safeFallback.forbiddenRegions),
      lifecycleAssertions: normalizeList(input.lifecycleAssertions || input.lifecycle_assertions || safeFallback.lifecycleAssertions),
      checks: normalizeList(input.checks)
    };
  }

  function getRegistry() {
    return globalThis.McelRequirementsRegistry || globalThis.window?.McelRequirementsRegistry || null;
  }

  function getCodeEditorSurfaceDiagnostics() {
    return globalThis.McelCodeEditorSurfaceDiagnostics || globalThis.window?.McelCodeEditorSurfaceDiagnostics || null;
  }

  function unavailableSurfacePathwaySummary(reason) {
    return {
      contractVersion: "mcel.code-editor-surface-diagnostics.v1",
      status: "unavailable",
      valid: false,
      semanticRidgesPresent: false,
      surfaceIrBuildable: false,
      surfaceIrValid: false,
      layoutGrammarPresent: false,
      layoutGrammarValid: false,
      extractable: false,
      roundTripStatus: "unavailable",
      counts: {errors: 0, warnings: 1, ok: 0},
      diagnosticCodes: [reason || "code-editor-surface-diagnostics-api-unavailable"]
    };
  }

  function attachMcelSurfacePathway(report, snapshot, options = {}) {
    if (!report || report.appId !== "code-editor") return report;
    const api = getCodeEditorSurfaceDiagnostics();
    const pathwayInput = {
      ...report,
      measurements: {
        ...(report.measurements || {}),
        ...(snapshot ? {
          viewport: snapshot.viewport || report.measurements?.viewport || {},
          requiredRegions: snapshot.requiredRegions || report.measurements?.requiredRegions || {},
          optionalRegions: snapshot.optionalRegions || report.measurements?.optionalRegions || {},
          surfaces: snapshot.surfaces || report.measurements?.surfaces || {}
        } : {})
      }
    };

    let summary = null;
    if (!api || typeof api.summarizeForDiagnosis !== "function") {
      summary = unavailableSurfacePathwaySummary("code-editor-surface-diagnostics-api-unavailable");
    } else {
      try {
        summary = api.summarizeForDiagnosis(pathwayInput, options.surfacePathwayOptions || {});
      } catch (error) {
        summary = {
          ...unavailableSurfacePathwaySummary("code-editor-surface-diagnostics-threw"),
          status: "fail",
          valid: false,
          counts: {errors: 1, warnings: 0, ok: 0},
          errorMessage: String(error?.message || error || "")
        };
      }
    }

    report.mcelSurfacePathway = summary;
    report.summary = {
      ...(report.summary || {}),
      mcelSurfacePathway: summary
    };
    return report;
  }

  function getRegistryDiagnosisContract(appId, mode) {
    const registry = getRegistry();
    if (!registry) return null;
    try {
      if (typeof registry.getRuntimeDiagnosisContract === "function") {
        const contract = registry.getRuntimeDiagnosisContract(appId, mode);
        if (contract) return contract;
      }
      if (typeof registry.getRuntimeDiagnosisContracts === "function") {
        const contracts = registry.getRuntimeDiagnosisContracts(appId);
        const modeContracts = contracts?.mode_contracts || {};
        if (modeContracts[mode]) return modeContracts[mode];
      }
    } catch {}
    return null;
  }

  function resolveDiagnosisContract(appId = "code-editor", options = {}) {
    const fallback = fallbackContractFor(appId);
    const requestedMode = String(options.mode || fallback?.mode || APP_MODE_DEFAULTS[appId] || "default");
    if (options.contract) return normalizeDiagnosisContract(options.contract, fallback || CODE_EDITOR_AUTHORING_CONTRACT);
    const registryContract = getRegistryDiagnosisContract(appId, requestedMode);
    if (registryContract) return normalizeDiagnosisContract(registryContract, fallback || registryContract);
    return fallback ? normalizeDiagnosisContract(fallback, fallback) : null;
  }

  function deepFreeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.freeze(value);
    for (const key of Object.keys(value)) {
      deepFreeze(value[key]);
    }
    return value;
  }

  function nowIso() {
    try {
      return new Date().toISOString();
    } catch {
      return "";
    }
  }

  function routeHref() {
    try {
      return String(globalThis.location?.href || "");
    } catch {
      return "";
    }
  }

  function getDocument() {
    try {
      if (typeof document !== "undefined" && document?.querySelector) return document;
    } catch {}
    return null;
  }

  function isElement(value) {
    return Boolean(value && typeof value === "object" && value.nodeType === 1);
  }

  function selectorFor(el) {
    if (!isElement(el)) return "";
    if (el.id) return `#${el.id}`;
    const tag = String(el.tagName || "").toLowerCase() || "element";
    const className = String(el.className || "").trim();
    if (className) return `${tag}.${className.replace(/\s+/g, ".")}`;
    const role = el.getAttribute?.("role");
    if (role) return `${tag}[role="${role}"]`;
    return tag;
  }

  function matchesSelector(el, selector) {
    if (!isElement(el) || !selector || typeof el.matches !== "function") return false;
    try {
      return el.matches(selector);
    } catch {
      return false;
    }
  }

  function query(selector, root) {
    if (!selector) return null;
    const base = root?.querySelector ? root : getDocument();
    if (!base) return null;
    try {
      if (matchesSelector(base, selector)) return base;
      return base.querySelector(selector);
    } catch {
      return null;
    }
  }

  function queryAll(selector, root) {
    if (!selector) return [];
    const base = root?.querySelectorAll ? root : getDocument();
    if (!base) return [];
    try {
      const matches = Array.from(base.querySelectorAll(selector));
      if (matchesSelector(base, selector) && !matches.includes(base)) {
        matches.unshift(base);
      }
      return matches;
    } catch {
      return [];
    }
  }

  function selectorUsesDocumentScope(selector) {
    return String(selector || "").includes("mc-widget");
  }

  function activeWidgetEditorOverlaySelector() {
    return "#mc-widget-editor-pane.open, .mc-widget-selection:not([hidden]), .mc-widget-dock-preview:not([hidden])";
  }

  function computeForbiddenRegionBox(el, forbidden = {}) {
    const selector = String(forbidden?.selector || "");
    const box = computeBox(el);
    if (selector.includes("#mc-widget-editor-root") && el?.id === "mc-widget-editor-root") {
      const active = query(activeWidgetEditorOverlaySelector(), el);
      if (!active) {
        return {
          ...box,
          visible: false,
          inactiveWidgetEditorShell: true,
          activeWidgetEditorOverlay: false
        };
      }
      const activeBox = computeBox(active);
      return {
        ...activeBox,
        activeWidgetEditorOverlay: Boolean(activeBox.visible),
        rootSelector: "#mc-widget-editor-root",
        rootBox: compactBox(box)
      };
    }
    return box;
  }

  function getComputedStyleSafe(el) {
    try {
      if (typeof getComputedStyle === "function") return getComputedStyle(el);
    } catch {}
    return {};
  }

  function textPreview(el) {
    try {
      return String(el.innerText || el.textContent || "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 180);
    } catch {
      return "";
    }
  }

  function valuePreview(el) {
    try {
      if ("value" in el) return String(el.value || "").slice(0, 180);
    } catch {}
    return "";
  }

  function computeBox(el) {
    if (!isElement(el)) {
      return {exists: false, selector: "", visible: false, width: 0, height: 0};
    }

    const rect = typeof el.getBoundingClientRect === "function"
      ? el.getBoundingClientRect()
      : {x: 0, y: 0, width: el.offsetWidth || 0, height: el.offsetHeight || 0, right: 0, bottom: 0};
    const style = getComputedStyleSafe(el);
    const width = Math.round(Number(rect.width || 0));
    const height = Math.round(Number(rect.height || 0));
    const display = style.display || "";
    const visibility = style.visibility || "";
    const opacity = style.opacity || "";
    const visible = width > 0 &&
      height > 0 &&
      display !== "none" &&
      visibility !== "hidden" &&
      Number(opacity || 1) !== 0 &&
      !el.hidden;

    return {
      exists: true,
      selector: selectorFor(el),
      tag: String(el.tagName || "").toLowerCase(),
      id: el.id || "",
      className: String(el.className || ""),
      x: Math.round(Number(rect.x || rect.left || 0)),
      y: Math.round(Number(rect.y || rect.top || 0)),
      width,
      height,
      right: Math.round(Number(rect.right || 0)),
      bottom: Math.round(Number(rect.bottom || 0)),
      visible,
      hidden: Boolean(el.hidden),
      ariaHidden: el.getAttribute?.("aria-hidden") || null,
      display,
      position: style.position || "",
      gridTemplateRows: style.gridTemplateRows || "",
      gridTemplateColumns: style.gridTemplateColumns || "",
      gridRow: style.gridRow || "",
      gridColumn: style.gridColumn || "",
      gridArea: style.gridArea || "",
      alignSelf: style.alignSelf || "",
      justifySelf: style.justifySelf || "",
      widthCss: style.width || "",
      heightCss: style.height || "",
      minWidth: style.minWidth || "",
      minHeight: style.minHeight || "",
      maxWidth: style.maxWidth || "",
      maxHeight: style.maxHeight || "",
      overflow: `${style.overflow || ""}/${style.overflowX || ""}/${style.overflowY || ""}`,
      zIndex: style.zIndex || "",
      pointerEvents: style.pointerEvents || "",
      inlineStyle: el.getAttribute?.("style") || "",
      childCount: el.children?.length || 0,
      textPreview: textPreview(el),
      valuePreview: valuePreview(el)
    };
  }

  function severityCounts(findings) {
    return findings.reduce(
      (counts, finding) => {
        counts[finding.severity] = (counts[finding.severity] || 0) + 1;
        return counts;
      },
      {critical: 0, warning: 0, info: 0}
    );
  }

  function addFinding(findings, severity, code, finding, evidence = {}, recommendedNextProbe = "") {
    findings.push({severity, code, finding, evidence, recommendedNextProbe});
  }

  function normalizedMode(snapshot, contract) {
    const mode = String(snapshot?.mode || "").trim();
    if (mode && mode !== "unknown") return mode;
    return String(contract?.mode || "default");
  }

  function visibleAndUseful(box, minWidth, minHeight) {
    return Boolean(box?.exists && box.visible && box.width >= minWidth && box.height >= minHeight);
  }

  function surfaceOwnershipProbe(snapshot = {}, contract = {}) {
    const surfaces = snapshot.surfaces || {};
    const host = surfaces.primaryHost || surfaces.monacoHost || {};
    const editor = surfaces.primaryEditor || surfaces.monacoEditor || host || {};
    const chain = Array.isArray(snapshot.ownerChain) ? snapshot.ownerChain : [];
    const selectors = chain.map((entry) => String(entry?.selector || ""));
    const primarySurfaceId = String(contract?.primarySurface?.id || "");
    const hostSelector = String(contract?.primarySurface?.hostSelector || "");
    const editorSelector = String(contract?.primarySurface?.editorSelector || "");
    const ownsHost = Boolean(hostSelector && String(host?.selector || "").includes(hostSelector.replace(/^#|^\./, ""))) ||
      selectors.some((selector) => hostSelector && selector.includes(hostSelector.replace(/^#|^\./, "")));
    const ownsEditor = Boolean(editorSelector && String(editor?.selector || "").includes(editorSelector.replace(/^#|^\./, ""))) ||
      selectors.some((selector) => editorSelector && selector.includes(editorSelector.replace(/^#|^\./, ""))) ||
      selectors.some((selector) => selector.includes("code-studio-monaco-authoring-surface") || selector.includes("code-studio-editor-group"));
    return {
      primarySurfaceId,
      hostSelector,
      editorSelector,
      ownsHost,
      ownsEditor,
      ownerChainSelectors: selectors.slice(0, 8)
    };
  }

  function compactBox(box) {
    if (!box) return {};
    return {
      exists: Boolean(box.exists),
      visible: Boolean(box.visible),
      selector: box.selector || "",
      width: Number(box.width || 0),
      height: Number(box.height || 0),
      display: box.display || "",
      gridRow: box.gridRow || "",
      gridColumn: box.gridColumn || ""
    };
  }

  function evaluateRuntimeContractSnapshot(snapshot, options = {}) {
    const contract = normalizeDiagnosisContract(options.contract || resolveDiagnosisContract(snapshot?.appId || "code-editor", options));
    const minWidth = Number(options.minWidth || contract.primarySurface.minWidth || 1);
    const minHeight = Number(options.minHeight || contract.primarySurface.minHeight || 1);
    const findings = [];
    const mode = normalizedMode(snapshot, contract);
    const surfaces = snapshot.surfaces || {};
    const host = surfaces.primaryHost || surfaces.monacoHost || {};
    const primaryEditor = surfaces.primaryEditor || surfaces.monacoEditor || host || {};
    const source = surfaces.sourceTextarea || {};
    const runtimeDraft = surfaces.runtimeDraft || {};
    const fallback = surfaces.fallbackTextarea || {};
    const requiredRegions = snapshot.requiredRegions || {};
    const optionalRegions = snapshot.optionalRegions || {};
    const forbiddenRegions = snapshot.forbiddenRegions || [];
    const ownerChain = Array.isArray(snapshot.ownerChain) ? snapshot.ownerChain : [];

    if (mode !== contract.mode) {
      addFinding(
        findings,
        "warning",
        "mode-mismatch",
        `Expected ${contract.mode} mode while evaluating ${contract.appId} diagnosis contract.`,
        {expected: contract.mode, actual: mode},
        "mode.contractAudit"
      );
    }

    for (const region of contract.requiredRegions) {
      const observed = requiredRegions[region.id] || requiredRegions[region.selector] || {};
      if (!observed.exists) {
        addFinding(findings, "critical", "required-region-missing", `${region.label} is missing.`, {region}, "layout.baseline");
      } else if (!observed.visible) {
        addFinding(findings, "critical", "required-region-hidden", `${region.label} exists but is not visible.`, {region, observed}, "layout.ownerProbe");
      }
    }

    for (const region of contract.optionalRegions || []) {
      const observed = optionalRegions[region.id] || optionalRegions[region.selector] || {};
      if (observed.exists && observed.visible && observed.position === "fixed") {
        addFinding(
          findings,
          "warning",
          "secondary-region-overlay-position",
          `${region.label || region.selector} is visible as a fixed overlay; secondary Code Editor panes should stay in their owned region.`,
          {region, observed},
          "overlay.ownedRegionPolicy"
        );
      }
    }


    const layoutCollisions = Array.isArray(snapshot.layoutCollisions) ? snapshot.layoutCollisions : [];
    if (layoutCollisions.length) {
      const blocksPrimarySurface = layoutCollisions.some((collision) => collision?.blocksPrimarySurface);
      addFinding(
        findings,
        blocksPrimarySurface ? "critical" : "warning",
        "semantic-layout-overlap-detected",
        blocksPrimarySurface
          ? "Rendered semantic projections overlap the primary work surface."
          : "Rendered semantic projections overlap or bleed outside their owned layout containers.",
        {layoutCollisions},
        "layout.collisionProbe"
      );
    }

    const contentFitViolations = Array.isArray(snapshot.contentFitViolations) ? snapshot.contentFitViolations : [];
    if (contentFitViolations.length) {
      addFinding(
        findings,
        "warning",
        "semantic-content-fit-violation",
        "Rendered semantic projections clip or overwrite their own readable content.",
        {contentFitViolations},
        "layout.contentFitProbe"
      );
    }

    const visualIntegrityViolations = Array.isArray(snapshot.visualIntegrityViolations) ? snapshot.visualIntegrityViolations : [];
    if (visualIntegrityViolations.length) {
      addFinding(
        findings,
        "critical",
        "visual-integrity-violation",
        "Rendered semantic surfaces collide, bleed, or overwrite readable content.",
        {visualIntegrityViolations},
        "layout.visualIntegrityProbe"
      );
    }

    if (!host.exists) {
      addFinding(
        findings,
        "critical",
        "primary-surface-host-missing",
        `${contract.primarySurface.label || "Primary surface"} host was not found.`,
        {selector: contract.primarySurface.hostSelector},
        "surface.visibilityAudit"
      );
    }

    if (!primaryEditor.exists) {
      addFinding(
        findings,
        "critical",
        contract.appId === "code-editor" ? "primary-editor-missing" : "primary-surface-missing",
        `${contract.primarySurface.label || "Primary surface"} was not found.`,
        {selector: contract.primarySurface.editorSelector || contract.primarySurface.hostSelector, host},
        "surface.visibilityAudit"
      );
    }

    if (host.exists && !visibleAndUseful(host, minWidth, minHeight)) {
      addFinding(
        findings,
        "critical",
        contract.appId === "code-editor" ? "primary-editor-host-unusable" : "primary-surface-host-unusable",
        `${contract.primarySurface.label || "Primary surface"} host exists but is not usable.`,
        {selector: contract.primarySurface.hostSelector, minWidth, minHeight, observed: host},
        "layout.ownerProbe"
      );
    }

    if (primaryEditor.exists && !visibleAndUseful(primaryEditor, minWidth, minHeight)) {
      addFinding(
        findings,
        "critical",
        contract.appId === "code-editor" ? "primary-editor-unusable" : "primary-surface-unusable",
        `${contract.primarySurface.label || "Primary surface"} exists but is not usable.`,
        {selector: contract.primarySurface.editorSelector || contract.primarySurface.hostSelector, minWidth, minHeight, observed: primaryEditor},
        "layout.ownerProbe"
      );
    }

    const competing = contract.appId === "code-editor"
      ? [source, runtimeDraft, fallback].filter((box) => Boolean(box?.exists && box.visible))
      : [];
    if (competing.length) {
      addFinding(
        findings,
        "critical",
        "competing-editor-surface-visible",
        "Authoring mode must expose one authoritative Monaco surface; competing editor surfaces are visible.",
        {competing},
        "editor.surfaceAudit"
      );
    }

    const visibleForbidden = forbiddenRegions.filter((entry) => Boolean(entry?.box?.exists && entry.box.visible));
    for (const entry of visibleForbidden) {
      addFinding(
        findings,
        entry.severity || "critical",
        "forbidden-region-visible",
        `${entry.label || entry.selector} is visible even though the contract forbids it.`,
        entry,
        "overlay.detector"
      );
    }

    const collapsedOwner = findCollapsedOwner(ownerChain);
    if (collapsedOwner) {
      addFinding(
        findings,
        "critical",
        "collapsed-layout-owner",
        "The primary surface is collapsed by an ancestor or assigned parent track.",
        collapsedOwner,
        "layout.ownerProbe"
      );
    }

    const optionalRegionEntries = (contract.optionalRegions || []).map((region) => ({
      ...region,
      box: optionalRegions[region.id] || optionalRegions[region.selector] || {exists: false, selector: region.selector, visible: false}
    }));
    const visibleOptionalRegionCount = optionalRegionEntries.filter((entry) => entry.box?.exists && entry.box.visible).length;
    const primarySurfaceUsable = visibleAndUseful(host, minWidth, minHeight) && visibleAndUseful(primaryEditor, minWidth, minHeight);
    const exactlyOnePrimary = primarySurfaceUsable && !competing.length;
    const counts = severityCounts(findings);
    const verdict = counts.critical > 0 ? "fail" : "pass";

    return {
      schema: REPORT_SCHEMA,
      version: VERSION,
      contractId: contract.contractId,
      appId: contract.appId,
      mode,
      route: snapshot.route || routeHref(),
      timestamp: snapshot.timestamp || nowIso(),
      verdict,
      summary: {
        critical: counts.critical,
        warning: counts.warning,
        info: counts.info,
        primarySurface: {
          expected: contract.primarySurface.id,
          usable: primarySurfaceUsable,
          exactlyOneAuthoritativeSurface: exactlyOnePrimary,
          host: compactBox(host),
          editor: compactBox(primaryEditor),
          ownership: surfaceOwnershipProbe(snapshot, contract)
        },
        optionalRegions: {
          visible: visibleOptionalRegionCount,
          total: optionalRegionEntries.length
        }
      },
      findings,
      measurements: {
        viewport: snapshot.viewport || {},
        requiredRegions,
        optionalRegions,
        surfaces,
        forbiddenRegions,
        ownerChain,
        layoutCollisions,
        contentFitViolations,
        visualIntegrityViolations
      },
      contract: {
        id: contract.contractId,
        appId: contract.appId,
        mode: contract.mode,
        derivedFromBlockTypes: contract.derivedFromBlockTypes,
        primarySurface: contract.primarySurface,
        optionalRegions: contract.optionalRegions || [],
        lifecycleAssertions: contract.lifecycleAssertions
      }
    };
  }

  function evaluateCodeEditorAuthoringSnapshot(snapshot, options = {}) {
    const contract = normalizeDiagnosisContract(
      options.contract || resolveDiagnosisContract("code-editor", {mode: "authoring"}),
      CODE_EDITOR_AUTHORING_CONTRACT
    );
    return evaluateRuntimeContractSnapshot(
      {...snapshot, appId: "code-editor"},
      {...options, contract}
    );
  }

  function findCollapsedOwner(chain) {
    if (!Array.isArray(chain) || chain.length < 2) return null;

    for (let index = 0; index < chain.length - 1; index += 1) {
      const current = chain[index] || {};
      const parent = chain[index + 1] || {};
      const currentHeight = Number(current.height || 0);
      const parentHeight = Number(parent.height || 0);
      const currentWidth = Number(current.width || 0);
      const parentWidth = Number(parent.width || 0);

      if (
        current.exists !== false &&
        parent.exists !== false &&
        (
          (currentHeight <= 80 && parentHeight >= 500) ||
          (currentWidth <= 200 && parentWidth >= 800)
        )
      ) {
        return {
          collapsedElement: current,
          parentElement: parent,
          chainIndex: index,
          reason: "child-is-tiny-while-parent-has-usable-space"
        };
      }
    }

    return null;
  }

  function defaultRootSelector(appId) {
    return `#${appId}-app`;
  }

  function inferRootSelector(contract, appId) {
    const rootRegion = (contract.requiredRegions || []).find((region) =>
      String(region.id || "").includes(".region.root") || String(region.label || "").toLowerCase().includes("root")
    );
    return rootRegion?.selector || defaultRootSelector(appId);
  }


  function overlapMetrics(a, b) {
    if (!a?.visible || !b?.visible) {
      return {overlaps: false, area: 0, width: 0, height: 0};
    }
    const left = Math.max(Number(a.x || 0), Number(b.x || 0));
    const top = Math.max(Number(a.y || 0), Number(b.y || 0));
    const right = Math.min(Number(a.right || 0), Number(b.right || 0));
    const bottom = Math.min(Number(a.bottom || 0), Number(b.bottom || 0));
    const width = Math.max(0, Math.round(right - left));
    const height = Math.max(0, Math.round(bottom - top));
    const area = width * height;
    return {overlaps: area > 0, area, width, height, left, top, right, bottom};
  }

  function boxBleed(ownerBox, childBox, tolerance = 6) {
    if (!ownerBox?.visible || !childBox?.visible) return null;
    const bleed = {
      top: Math.max(0, Math.round(Number(ownerBox.y || 0) - Number(childBox.y || 0))),
      right: Math.max(0, Math.round(Number(childBox.right || 0) - Number(ownerBox.right || 0))),
      bottom: Math.max(0, Math.round(Number(childBox.bottom || 0) - Number(ownerBox.bottom || 0))),
      left: Math.max(0, Math.round(Number(ownerBox.x || 0) - Number(childBox.x || 0)))
    };
    const maxBleed = Math.max(bleed.top, bleed.right, bleed.bottom, bleed.left);
    if (maxBleed <= tolerance) return null;
    return {...bleed, maxBleed};
  }

  function intersectPaintBox(box, boundaryBox) {
    if (!box?.visible || !boundaryBox?.visible) return {...box, visible: false, clipped: true};
    const left = Math.max(Number(box.x || 0), Number(boundaryBox.x || 0));
    const top = Math.max(Number(box.y || 0), Number(boundaryBox.y || 0));
    const right = Math.min(Number(box.right || 0), Number(boundaryBox.right || 0));
    const bottom = Math.min(Number(box.bottom || 0), Number(boundaryBox.bottom || 0));
    const width = Math.max(0, Math.round(right - left));
    const height = Math.max(0, Math.round(bottom - top));
    const visible = Boolean(box.visible && boundaryBox.visible && width > 0 && height > 0);
    return {
      ...box,
      x: Math.round(left),
      y: Math.round(top),
      right: Math.round(right),
      bottom: Math.round(bottom),
      width,
      height,
      visible,
      clipped: !visible ||
        width !== Number(box.width || 0) ||
        height !== Number(box.height || 0) ||
        Math.round(left) !== Number(box.x || 0) ||
        Math.round(top) !== Number(box.y || 0)
    };
  }

  function overflowClipsPaint(style) {
    return /hidden|clip|auto|scroll/.test(
      `${style?.overflow || ""} ${style?.overflowX || ""} ${style?.overflowY || ""}`
    );
  }

  function clippedPaintBox(el, rawBox, boundaryElement = null) {
    if (!rawBox?.visible || !isElement(el)) return rawBox || {exists: false, visible: false, width: 0, height: 0};

    let clipped = {...rawBox, clipped: false};
    const explicitBoundary = isElement(boundaryElement) ? boundaryElement : null;
    let current = el.parentElement || null;
    const seen = new Set();
    let reachedExplicitBoundary = false;

    while (isElement(current) && !seen.has(current)) {
      seen.add(current);
      if (current === explicitBoundary) reachedExplicitBoundary = true;

      const style = getComputedStyleSafe(current);
      if (overflowClipsPaint(style)) {
        clipped = intersectPaintBox(clipped, computeBox(current));
        if (!clipped.visible) return clipped;
      }

      if (current === explicitBoundary) break;
      current = current.parentElement || null;
    }

    // Some probes use a logical owner boundary that may not be an ancestor in
    // browser-repaired markup. Still clip against it when supplied so the probe
    // degrades into a bounded measurement instead of throwing or reporting an
    // unbounded scroll/layout box.
    if (explicitBoundary && !reachedExplicitBoundary) {
      clipped = intersectPaintBox(clipped, computeBox(explicitBoundary));
    }

    return clipped;
  }


  function clippedRangeBox(el, rawBox, boundaryElement = null) {
    // Text Range#getClientRects() returns painted fragments rather than whole
    // element layout boxes.  Those fragments still need the same overflow and
    // owner-boundary clipping as regular paint boxes.  Keep this as a named
    // helper so visual-fit probes do not crash when they switch between element
    // boxes and text-range boxes.
    return clippedPaintBox(el, rawBox, boundaryElement);
  }

  function layoutCollisionCandidateSelector(appId) {
    if (appId === "file-explorer") {
      return [
        ".file-explorer-shell",
        ".file-explorer-roots-panel",
        ".file-explorer-main",
        ".file-explorer-toolbar",
        "#file-explorer-list",
        "#file-explorer-preview",
        "[data-mcel-layout-zone]",
        "[data-mcel-zone]"
      ].join(", ");
    }
    if (appId === "mcel-lab") {
      return [
        ".mcel-lab-blueprint-workbench",
        ".mcel-lab-blueprint-navigation",
        ".mcel-lab-blueprint-primary",
        ".mcel-lab-blueprint-right-rail",
        ".mcel-lab-blueprint-status",
        ".mcel-lab-work-area",
        ".mcel-lab-blueprint-list",
        ".mcel-lab-blueprint-pills",
        ".mcel-lab-blueprint-facts",
        ".mcel-lab-mounted-preview",
        ".mcel-lab-work-context",
        ".mcel-lab-blueprint-navigation > .mcel-lab-shell-card",
        ".mcel-lab-blueprint-right-rail > .mcel-lab-shell-card"
      ].join(", ");
    }
    return "[data-mcel-layout-zone], [data-mcel-zone]";
  }

  function layoutBleedDescendantSelector(appId) {
    if (appId === "file-explorer") {
      return [
        ".file-explorer-roots-panel > *",
        ".file-explorer-main > *",
        ".file-explorer-toolbar > *",
        ".file-explorer-root-button",
        ".file-explorer-entry",
        "#file-explorer-list",
        "#file-explorer-preview",
        "[data-mcel-layout-zone]",
        "[data-mcel-zone]"
      ].join(", ");
    }
    if (appId === "mcel-lab") {
      return [
        "button",
        "select",
        "input",
        "textarea",
        "output",
        "summary",
        "h4",
        "p",
        "dl",
        "ol",
        "ul",
        "[data-mcel-blueprint-app-option]",
        "[data-mcel-blueprint-aspect-option]"
      ].join(", ");
    }
    return "button, select, input, textarea, output, summary, [data-mcel-layout-zone], [data-mcel-zone]";
  }

  function directVisibleChildren(container) {
    return Array.from(container?.children || [])
      .filter(isElement)
      .map((el) => ({el, box: computeBox(el)}))
      .filter((entry) => entry.box.visible && entry.box.width > 8 && entry.box.height > 8);
  }

  function detectSiblingLayoutCollisions(container, context = {}) {
    const collisions = [];
    const children = directVisibleChildren(container);
    for (let index = 0; index < children.length; index += 1) {
      for (let otherIndex = index + 1; otherIndex < children.length; otherIndex += 1) {
        const first = children[index];
        const second = children[otherIndex];
        const metrics = overlapMetrics(first.box, second.box);
        if (metrics.area < 36 || metrics.width < 6 || metrics.height < 6) continue;
        collisions.push({
          type: "sibling-overlap",
          appId: context.appId || "",
          container: selectorFor(container),
          first: compactBox(first.box),
          second: compactBox(second.box),
          overlap: metrics,
          blocksPrimarySurface: false
        });
      }
    }
    return collisions;
  }

  function detectSemanticProjectionBleed(owner, context = {}) {
    const collisions = [];
    const ownerBox = computeBox(owner);
    if (!ownerBox.visible) return collisions;
    const ownerBoundary = owner.parentElement || null;
    const overflow = String(ownerBox.overflow || "");
    const clipsOverflow = /hidden|clip|auto|scroll/.test(overflow);
    const descendants = queryAll(layoutBleedDescendantSelector(context.appId), owner)
      .filter((el) => el !== owner && isElement(el));
    const siblingBoxes = Array.from(owner.parentElement?.children || [])
      .filter((candidate) => candidate !== owner && isElement(candidate))
      .map((el) => ({el, box: clippedPaintBox(el, computeBox(el), owner.parentElement?.parentElement || null)}))
      .filter((entry) => entry.box?.visible);

    descendants.forEach((el) => {
      const rawChildBox = computeBox(el);
      if (!rawChildBox.visible || rawChildBox.width < 8 || rawChildBox.height < 8) return;

      // Compare the painted fragment, not the raw scroll/layout box. Without this,
      // scrollable cards look like they overlap following cards even when overflow
      // clipping prevents any visible paint from escaping the owner.
      const childBox = clippedPaintBox(el, rawChildBox, ownerBoundary);
      if (!childBox?.visible || childBox.width < 8 || childBox.height < 8) return;

      const bleed = boxBleed(ownerBox, childBox, 6);
      if (bleed && !clipsOverflow) {
        collisions.push({
          type: "semantic-projection-overflow",
          appId: context.appId || "",
          owner: compactBox(ownerBox),
          child: compactBox(childBox),
          rawChild: compactBox(rawChildBox),
          bleed,
          blocksPrimarySurface: false
        });
      }

      siblingBoxes.forEach((sibling) => {
        const metrics = overlapMetrics(childBox, sibling.box);
        if (metrics.area < 64 || metrics.width < 8 || metrics.height < 8) return;
        collisions.push({
          type: "semantic-projection-overlap",
          appId: context.appId || "",
          owner: compactBox(ownerBox),
          child: compactBox(childBox),
          rawChild: compactBox(rawChildBox),
          sibling: compactBox(sibling.box),
          overlap: metrics,
          blocksPrimarySurface: false
        });
      });
    });

    return collisions;
  }

  function detectLayoutCollisions(root, context = {}) {
    if (!isElement(root)) return [];
    const appId = String(context.appId || "");
    const collisions = [];
    const seen = new Set();
    queryAll(layoutCollisionCandidateSelector(appId), root).forEach((container) => {
      if (!isElement(container) || seen.has(container)) return;
      seen.add(container);
      detectSiblingLayoutCollisions(container, context).forEach((collision) => collisions.push(collision));
      if (
        (appId === "mcel-lab" && container.classList?.contains("mcel-lab-shell-card")) ||
        appId === "file-explorer"
      ) {
        detectSemanticProjectionBleed(container, context).forEach((collision) => collisions.push(collision));
      }
    });
    return collisions.slice(0, 12);
  }

  function contentFitCandidateSelector(appId) {
    if (appId === "file-explorer") {
      return [
        ".file-explorer-shell",
        ".file-explorer-roots-panel",
        ".file-explorer-main",
        ".file-explorer-toolbar",
        ".file-explorer-path",
        ".file-explorer-status",
        ".file-explorer-root-button",
        ".file-explorer-entry",
        "#file-explorer-list",
        "#file-explorer-preview",
        "[data-mcel-layout-zone]",
        "[data-mcel-zone]"
      ].join(", ");
    }
    if (appId === "mcel-lab") {
      return [
        ".mcel-lab-blueprint-list button",
        ".mcel-lab-blueprint-pills button",
        ".mcel-lab-selection-receipt",
        ".mcel-lab-work-context > summary",
        ".mcel-lab-selected-element-card"
      ].join(", ");
    }
    return "[data-mcel-layout-zone], [data-mcel-zone]";
  }

  function detectContentFitViolations(root, context = {}) {
    if (!isElement(root)) return [];
    const violations = [];
    const tolerance = 3;
    queryAll(contentFitCandidateSelector(context.appId), root).forEach((el) => {
      if (!isElement(el)) return;
      const box = computeBox(el);
      if (!box.visible || box.width < 8 || box.height < 8) return;

      const styles = window.getComputedStyle ? window.getComputedStyle(el) : {};
      const overflowX = String(styles.overflowX || styles.overflow || "");
      const overflowY = String(styles.overflowY || styles.overflow || "");
      const allowsHorizontalScroll = /auto|scroll/.test(overflowX);
      const allowsVerticalScroll = /auto|scroll/.test(overflowY);
      const clientWidth = Number(el.clientWidth || 0);
      const clientHeight = Number(el.clientHeight || 0);
      const scrollWidth = Number(el.scrollWidth || 0);
      const scrollHeight = Number(el.scrollHeight || 0);
      const horizontalClipped = scrollWidth > clientWidth + tolerance && !allowsHorizontalScroll;
      const verticalClipped = scrollHeight > clientHeight + tolerance && !allowsVerticalScroll;

      if (!horizontalClipped && !verticalClipped) return;
      violations.push({
        type: "semantic-content-fit",
        appId: context.appId || "",
        selector: selectorFor(el),
        box: compactBox(box),
        scroll: {
          clientWidth: Math.round(clientWidth),
          clientHeight: Math.round(clientHeight),
          scrollWidth: Math.round(scrollWidth),
          scrollHeight: Math.round(scrollHeight)
        },
        clipped: {
          horizontal: horizontalClipped,
          vertical: verticalClipped
        }
      });
    });
    return violations.slice(0, 12);
  }


  function visualIntegrityOwnerSelector(appId) {
    if (appId === "file-explorer") {
      return [
        "[data-mcel-visual-owner]",
        "[data-mcel-layout-zone]",
        ".file-explorer-shell",
        ".file-explorer-roots-panel",
        ".file-explorer-main",
        ".file-explorer-toolbar",
        ".file-explorer-root-button",
        ".file-explorer-entry",
        "#file-explorer-list",
        "#file-explorer-preview"
      ].join(", ");
    }
    if (appId === "mcel-lab") {
      return [
        "[data-mcel-visual-owner]",
        "[data-mcel-layout-zone]",
        "[data-mcel-zone]",
        ".mcel-lab-shell-card",
        ".mcel-lab-blueprint-list button",
        ".mcel-lab-blueprint-pills button",
        ".mcel-lab-selection-receipt",
        ".mcel-lab-work-context",
        ".mcel-lab-work-area > *"
      ].join(", ");
    }
    return "[data-mcel-visual-owner], [data-mcel-layout-zone], [data-mcel-zone]";
  }

  function visualIntegrityTextSelector(appId) {
    if (appId === "file-explorer") {
      return [
        "[data-mcel-readable]",
        ".file-explorer-roots-panel strong",
        ".file-explorer-roots-panel span",
        ".file-explorer-root-button",
        ".file-explorer-status",
        ".file-explorer-path",
        ".file-explorer-entry-title",
        ".file-explorer-entry-meta",
        "#file-explorer-preview"
      ].join(", ");
    }
    if (appId === "mcel-lab") {
      return [
        ".mcel-lab-shell-card-heading .eyebrow",
        ".mcel-lab-shell-card-heading h4",
        ".mcel-lab-blueprint-list button > strong",
        ".mcel-lab-blueprint-list button > span",
        ".mcel-lab-blueprint-pills button",
        ".mcel-lab-small-copy",
        ".mcel-lab-blueprint-facts dt",
        ".mcel-lab-blueprint-facts dd",
        ".mcel-lab-selection-receipt span",
        ".mcel-lab-work-context > summary strong",
        ".mcel-lab-work-context > summary .eyebrow",
        ".mcel-lab-selected-element-card h4",
        ".mcel-lab-selected-element-card p"
      ].join(", ");
    }
    return "[data-mcel-readable], [data-mcel-layout-zone] h1, [data-mcel-layout-zone] h2, [data-mcel-layout-zone] h3, [data-mcel-layout-zone] h4, [data-mcel-layout-zone] p, [data-mcel-layout-zone] button";
  }

  function visualStackContainerSelector(appId) {
    if (appId === "file-explorer") {
      return [
        ".file-explorer-shell",
        ".file-explorer-roots-panel",
        ".file-explorer-main",
        ".file-explorer-toolbar",
        ".file-explorer-roots",
        "#file-explorer-list"
      ].join(", ");
    }
    if (appId === "mcel-lab") {
      return [
        ".mcel-lab-blueprint-workbench",
        ".mcel-lab-blueprint-navigation",
        ".mcel-lab-blueprint-right-rail",
        ".mcel-lab-work-area",
        ".mcel-lab-blueprint-list",
        ".mcel-lab-blueprint-pills",
        ".mcel-lab-blueprint-facts",
        ".mcel-lab-work-context-body"
      ].join(", ");
    }
    return "[data-mcel-visual-stack], [data-mcel-layout-zone], [data-mcel-zone]";
  }

  function fullBox(box) {
    if (!box) return {};
    return {
      exists: Boolean(box.exists),
      visible: Boolean(box.visible),
      selector: box.selector || "",
      tag: box.tag || "",
      id: box.id || "",
      className: box.className || "",
      x: Number(box.x || 0),
      y: Number(box.y || 0),
      width: Number(box.width || 0),
      height: Number(box.height || 0),
      right: Number(box.right || 0),
      bottom: Number(box.bottom || 0),
      display: box.display || "",
      position: box.position || "",
      overflow: box.overflow || "",
      textPreview: box.textPreview || ""
    };
  }

  function boxFromClientRect(rect, selector = "", metadata = {}) {
    const x = Math.round(Number(rect.x || rect.left || 0));
    const y = Math.round(Number(rect.y || rect.top || 0));
    const width = Math.round(Number(rect.width || 0));
    const height = Math.round(Number(rect.height || 0));
    return {
      exists: true,
      visible: width > 0 && height > 0,
      selector,
      x,
      y,
      width,
      height,
      right: Math.round(Number(rect.right || x + width)),
      bottom: Math.round(Number(rect.bottom || y + height)),
      ...metadata
    };
  }

  function elementDepth(el, root) {
    let depth = 0;
    let node = el;
    while (isElement(node) && node !== root) {
      depth += 1;
      node = node.parentElement;
    }
    return depth;
  }

  function closestVisualOwner(el, root, context = {}) {
    const selector = visualIntegrityOwnerSelector(context.appId);
    let node = el;
    while (isElement(node) && node !== root) {
      try {
        if (typeof node.matches === "function" && node.matches(selector)) return node;
      } catch {}
      node = node.parentElement;
    }
    return isElement(root) ? root : null;
  }

  function ownerDescriptor(owner, root) {
    if (!isElement(owner)) return {};
    const box = computeBox(owner);
    return {
      selector: selectorFor(owner),
      role: owner.getAttribute?.("data-mcel-zone") ||
        owner.getAttribute?.("data-mcel-layout-zone") ||
        owner.getAttribute?.("data-mcel-visual-owner") ||
        "",
      depth: elementDepth(owner, root),
      box: fullBox(box)
    };
  }

  function textRangesForElement(el) {
    if (!isElement(el) || !el.textContent || !String(el.textContent).trim()) return [];
    const doc = el.ownerDocument || getDocument();
    if (!doc?.createRange) return [];
    const range = doc.createRange();
    try {
      range.selectNodeContents(el);
      return Array.from(range.getClientRects ? range.getClientRects() : []);
    } catch {
      return [];
    } finally {
      try {
        range.detach?.();
      } catch {}
    }
  }

  function collectReadableTextBoxes(root, context = {}) {
    if (!isElement(root)) return [];
    const boxes = [];
    queryAll(visualIntegrityTextSelector(context.appId), root).forEach((el) => {
      if (!isElement(el)) return;
      const owner = closestVisualOwner(el, root, context);
      const ownerBox = computeBox(owner);
      if (!ownerBox.visible) return;
      const text = textPreview(el);
      if (!text) return;
      const rects = textRangesForElement(el);
      rects.slice(0, 4).forEach((rect, index) => {
        const rawBox = boxFromClientRect(rect, selectorFor(el), {
          text,
          lineIndex: index,
          owner: ownerDescriptor(owner, root)
        });
        const box = clippedRangeBox(el, rawBox, owner.parentElement || root);
        if (box?.visible && box.width >= 4 && box.height >= 4) boxes.push({el, owner, box, rawBox});
      });
    });
    return boxes;
  }

  function elementsAreRelated(first, second) {
    if (!isElement(first) || !isElement(second)) return false;
    return first === second || first.contains(second) || second.contains(first);
  }

  function detectVisualStackOverlaps(root, context = {}) {
    if (!isElement(root)) return [];
    const violations = [];
    queryAll(visualStackContainerSelector(context.appId), root).forEach((container) => {
      if (!isElement(container)) return;
      const children = directVisibleChildren(container)
        .filter((entry) => entry.box.width >= 8 && entry.box.height >= 8);
      for (let index = 0; index < children.length; index += 1) {
        for (let otherIndex = index + 1; otherIndex < children.length; otherIndex += 1) {
          const first = children[index];
          const second = children[otherIndex];
          if (elementsAreRelated(first.el, second.el)) continue;
          const metrics = overlapMetrics(first.box, second.box);
          if (metrics.area < 48 || metrics.width < 8 || metrics.height < 5) continue;
          violations.push({
            type: "semantic-stack-overlap",
            appId: context.appId || "",
            container: selectorFor(container),
            first: fullBox(first.box),
            second: fullBox(second.box),
            overlap: metrics
          });
        }
      }
    });
    return violations;
  }

  function detectReadableTextIntegrityViolations(root, context = {}) {
    const violations = [];
    const readable = collectReadableTextBoxes(root, context);
    readable.forEach((entry) => {
      const ownerBox = computeBox(entry.owner);
      const bleed = boxBleed(ownerBox, entry.box, 2);
      if (bleed) {
        violations.push({
          type: "readable-text-outside-owner",
          appId: context.appId || "",
          owner: ownerDescriptor(entry.owner, root),
          text: fullBox(entry.box),
          bleed
        });
      }
    });

    for (let index = 0; index < readable.length; index += 1) {
      for (let otherIndex = index + 1; otherIndex < readable.length; otherIndex += 1) {
        const first = readable[index];
        const second = readable[otherIndex];
        if (elementsAreRelated(first.el, second.el)) continue;
        const sameOwner = first.owner && second.owner && first.owner === second.owner;
        const ownerOverlapRelevant = sameOwner ||
          (!elementsAreRelated(first.owner, second.owner) &&
            overlapMetrics(computeBox(first.owner), computeBox(second.owner)).area > 0);
        if (!ownerOverlapRelevant) continue;
        const metrics = overlapMetrics(first.box, second.box);
        if (metrics.area < 20 || metrics.width < 6 || metrics.height < 4) continue;
        violations.push({
          type: "readable-text-overlap",
          appId: context.appId || "",
          first: fullBox(first.box),
          second: fullBox(second.box),
          firstOwner: ownerDescriptor(first.owner, root),
          secondOwner: ownerDescriptor(second.owner, root),
          overlap: metrics
        });
      }
    }

    return violations;
  }

  function detectVisualIntegrityViolations(root, context = {}) {
    if (!isElement(root)) return [];
    const violations = [
      ...detectVisualStackOverlaps(root, context),
      ...detectReadableTextIntegrityViolations(root, context)
    ];
    return violations.slice(0, 16);
  }


  function buildDiagnosisSnapshot(appId = "code-editor", options = {}) {
    const doc = getDocument();
    const contract = resolveDiagnosisContract(appId, options);
    if (!contract) return null;
    const rootSelector = inferRootSelector(contract, appId);
    const root = query(rootSelector, doc);
    const rootMode = options.mode ||
      root?.dataset?.codeEditorMode ||
      root?.dataset?.mcelMode ||
      root?.getAttribute?.("data-code-editor-mode") ||
      root?.getAttribute?.("data-mcel-mode") ||
      contract.mode ||
      "default";

    const scope = root || doc;
    const requiredRegions = {};
    for (const region of contract.requiredRegions) {
      requiredRegions[region.id] = computeBox(query(region.selector, scope));
      requiredRegions[region.selector] = requiredRegions[region.id];
    }

    const optionalRegions = {};
    for (const region of contract.optionalRegions || []) {
      optionalRegions[region.id] = computeBox(query(region.selector, scope));
      optionalRegions[region.selector] = optionalRegions[region.id];
    }

    const host = query(contract.primarySurface.hostSelector || rootSelector, scope);
    const primaryEditor = query(contract.primarySurface.editorSelector || contract.primarySurface.hostSelector || rootSelector, scope) || host;
    const sourceTextarea = appId === "code-editor" ? query("#code-studio-source-editor", scope) : null;
    const runtimeDraft = appId === "code-editor" ? query("#code-studio-runtime-draft", scope) : null;
    const fallbackTextarea = appId === "code-editor" ? query(".code-studio-runtime-fallback", scope) : null;
    const activePane = query("[data-code-studio-pane].active", scope);

    const forbiddenRegions = [];
    for (const forbidden of contract.forbiddenRegions) {
      const selector = forbidden.selector || "";
      const forbiddenScope = selectorUsesDocumentScope(selector) ? doc : scope;
      const matches = queryAll(selector, forbiddenScope);
      if (!matches.length) {
        forbiddenRegions.push({
          id: forbidden.id,
          selector: forbidden.selector,
          label: forbidden.label,
          box: {exists: false, selector: forbidden.selector, visible: false, width: 0, height: 0}
        });
      } else {
        matches.forEach((el, index) => {
          forbiddenRegions.push({
            id: forbidden.id,
            selector: forbidden.selector,
            label: forbidden.label,
            matchIndex: index,
            box: computeForbiddenRegionBox(el, forbidden),
            severity: forbidden.severity || "critical"
          });
        });
      }
    }

    const ownerTarget = options.ownerTargetSelector
      ? query(options.ownerTargetSelector, scope)
      : primaryEditor || host || activePane || root;
    const ownerChain = collectOwnerChain(ownerTarget, root);

    return {
      appId,
      route: routeHref(),
      timestamp: nowIso(),
      mode: rootMode,
      viewport: {
        width: numberOrZero(globalThis.innerWidth),
        height: numberOrZero(globalThis.innerHeight),
        devicePixelRatio: numberOrZero(globalThis.devicePixelRatio || 1)
      },
      root: computeBox(root),
      requiredRegions,
      optionalRegions,
      surfaces: {
        primaryHost: computeBox(host),
        primaryEditor: computeBox(primaryEditor),
        monacoHost: computeBox(host),
        monacoEditor: computeBox(primaryEditor),
        sourceTextarea: computeBox(sourceTextarea),
        runtimeDraft: computeBox(runtimeDraft),
        fallbackTextarea: computeBox(fallbackTextarea),
        activePane: computeBox(activePane)
      },
      forbiddenRegions,
      ownerChain,
      overlays: detectOverlays(root || doc, {appId, mode: rootMode}),
      panes: collectPaneState(root || doc),
      layoutCollisions: detectLayoutCollisions(root || doc, {appId, mode: rootMode, contract}),
      contentFitViolations: detectContentFitViolations(root || doc, {appId, mode: rootMode, contract}),
      visualIntegrityViolations: detectVisualIntegrityViolations(root || doc, {appId, mode: rootMode, contract}),
      contract
    };
  }

  function buildCodeEditorSnapshot(options = {}) {
    return buildDiagnosisSnapshot("code-editor", {...options, mode: options.mode || "authoring"});
  }

  function numberOrZero(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function collectOwnerChain(target, stopAt) {
    const chain = [];
    let node = target;
    while (isElement(node)) {
      chain.push(computeBox(node));
      if (stopAt && node === stopAt) break;
      node = node.parentElement;
    }
    return chain;
  }

  function collectPaneState(root) {
    return queryAll("[data-code-studio-pane]", root).map((pane) => ({
      pane: pane.getAttribute("data-code-studio-pane") || "",
      active: pane.classList?.contains("active") || false,
      box: computeBox(pane)
    }));
  }

  function classifyOverlay(selector, box, context = {}) {
    const isWidgetEditor = selector.includes("mc-widget");
    const isProof = selector.includes("proof") || selector.includes("bottom-panel");
    const isFloating = selector.includes("floating") || selector.includes("side-tab") || selector.includes("vertical-tab");
    const classification = isWidgetEditor
      ? "widget-editor-overlay"
      : isProof
        ? "proof-diagnostic-surface"
        : isFloating
          ? "floating-diagnostic-tab"
          : "diagnostic-overlay";
    return {
      classification,
      policy: context.mode === "mcel-tools" || context.mode === "diagnostic" ? "allowed-diagnostic-overlay" : "forbidden-in-default-mode",
      severity: "warning",
      blocksPrimarySurface: Boolean(box?.visible && box.width > 200 && box.height > 200 && box.position === "fixed")
    };
  }

  function detectOverlays(root, context = {}) {
    const doc = getDocument();
    if (!doc) return [];
    const seen = new Set();
    const overlays = [];
    COMMON_OVERLAY_SELECTORS.forEach((selector) => {
      queryAll(selector, doc).forEach((el) => {
        if (seen.has(el)) return;
        seen.add(el);
        const box = computeBox(el);
        if (box.exists) {
          overlays.push({
            selector,
            box,
            insideRoot: root && root.contains ? root.contains(el) : false,
            ...classifyOverlay(selector, box, context)
          });
        }
      });
    });
    return overlays;
  }

  function buildReportBuckets(report) {
    const findings = Array.isArray(report?.findings) ? report.findings : [];
    const buckets = {
      activeRuntimeIssues: [],
      activeOverlayIssues: [],
      activeLayoutIssues: [],
      activeSurfaceIssues: [],
      activeContractIssues: []
    };

    for (const finding of findings) {
      const code = String(finding?.code || "");
      const compact = {
        severity: finding?.severity || "",
        code,
        finding: finding?.finding || "",
        recommendedNextProbe: finding?.recommendedNextProbe || ""
      };
      if (code.includes("overlay") || code.includes("forbidden-region")) buckets.activeOverlayIssues.push(compact);
      else if (
        code.includes("layout") ||
        code.includes("collapsed") ||
        code.includes("visual") ||
        code.includes("content-fit") ||
        code.includes("overlap")
      ) buckets.activeLayoutIssues.push(compact);
      else if (code.includes("surface") || code.includes("editor") || code.includes("competing")) buckets.activeSurfaceIssues.push(compact);
      else buckets.activeContractIssues.push(compact);

      if (finding?.severity === "critical" || finding?.severity === "warning") {
        buckets.activeRuntimeIssues.push(compact);
      }
    }

    return buckets;
  }

  function applyOverlayFindings(report, snapshot) {
    const visibleOverlays = (snapshot.overlays || []).filter((entry) => entry.box?.visible);
    if (!visibleOverlays.length) return report;
    const findings = report.findings || [];
    addFinding(
      findings,
      "warning",
      "visible-overlay-detected",
      "Overlay or diagnostic surfaces are visible while diagnosing the app.",
      {visibleOverlays},
      "overlay.detector"
    );
    report.findings = findings;
    report.summary = {
      ...report.summary,
      ...severityCounts(findings)
    };
    report.verdict = findings.some((finding) => finding.severity === "critical") ? "fail" : report.verdict;
    return report;
  }

  function diagnose(appId = "code-editor", options = {}) {
    const contract = resolveDiagnosisContract(appId, options);
    if (!contract) {
      return unsupportedAppReport(appId);
    }

    const snapshot = buildDiagnosisSnapshot(appId, {...options, contract});
    if (!snapshot) return unsupportedAppReport(appId);
    let report = evaluateRuntimeContractSnapshot(snapshot, {...options, contract});
    report.focus = options.focus || "contract";
    report.measurements.overlays = snapshot.overlays;
    report.measurements.panes = snapshot.panes;
    report.measurements.layoutCollisions = snapshot.layoutCollisions;
    report.measurements.contentFitViolations = snapshot.contentFitViolations;
    report = applyOverlayFindings(report, snapshot);
    report = attachMcelSurfacePathway(report, snapshot, options);
    report.buckets = buildReportBuckets(report);

    lastReport = report;
    logReport(report, options);
    return report;
  }

  function unsupportedAppReport(appId) {
    const report = {
      schema: REPORT_SCHEMA,
      version: VERSION,
      appId,
      route: routeHref(),
      timestamp: nowIso(),
      verdict: "unsupported",
      findings: [
        {
          severity: "warning",
          code: "unsupported-app",
          finding: `No MCEL self-diagnosis contract is registered for ${appId}.`,
          evidence: {},
          recommendedNextProbe: ""
        }
      ],
      buckets: {
        activeRuntimeIssues: [
          {
            severity: "warning",
            code: "unsupported-app",
            finding: `No MCEL self-diagnosis contract is registered for ${appId}.`,
            recommendedNextProbe: ""
          }
        ],
        activeOverlayIssues: [],
        activeLayoutIssues: [],
        activeSurfaceIssues: [],
        activeContractIssues: []
      },
      measurements: {},
      summary: {critical: 0, warning: 1, info: 0}
    };
    lastReport = report;
    return report;
  }

  function logReport(report, options = {}) {
    if (options.silent || typeof console === "undefined") return;
    try {
      console.groupCollapsed?.(`MCEL diagnosis: ${report.appId} ${report.verdict}`);
      console.log(report);
      if (Array.isArray(report.findings)) {
        console.table?.(report.findings.map((finding) => ({
          severity: finding.severity,
          code: finding.code,
          finding: finding.finding,
          next: finding.recommendedNextProbe || ""
        })));
      }
      console.groupEnd?.();
    } catch {}
  }

  function exportLastDiagnosis() {
    return JSON.stringify(lastReport || {}, null, 2);
  }

  function listContracts() {
    const registry = getRegistry();
    if (registry && typeof registry.listRuntimeDiagnosisContracts === "function") {
      try {
        const contracts = registry.listRuntimeDiagnosisContracts();
        if (Array.isArray(contracts) && contracts.length) {
          return contracts.map((contract) => normalizeDiagnosisContract(contract, fallbackContractFor(contract.appId || contract.app)));
        }
        if (contracts && typeof contracts === "object") {
          const flattened = [];
          for (const [appId, appContracts] of Object.entries(contracts)) {
            const modeContracts = appContracts?.mode_contracts || {};
            for (const contract of Object.values(modeContracts)) {
              flattened.push(normalizeDiagnosisContract(contract, fallbackContractFor(appId)));
            }
          }
          if (flattened.length) return flattened;
        }
      } catch {}
    }
    return Object.keys(FALLBACK_CONTRACTS).map((appId) => normalizeDiagnosisContract(FALLBACK_CONTRACTS[appId], FALLBACK_CONTRACTS[appId]));
  }

  function registerOnGlobal(global) {
    const api = deepFreeze({
      VERSION,
      REPORT_SCHEMA,
      CODE_EDITOR_AUTHORING_CONTRACT,
      FALLBACK_CONTRACTS,
      diagnose,
      exportLastDiagnosis,
      listContracts,
      buildDiagnosisSnapshot,
      buildCodeEditorSnapshot,
      evaluateRuntimeContractSnapshot,
      evaluateCodeEditorAuthoringSnapshot,
      resolveDiagnosisContract,
      _private: deepFreeze({
        computeBox,
        collectOwnerChain,
        findCollapsedOwner,
        visibleAndUseful,
        surfaceOwnershipProbe,
        getCodeEditorSurfaceDiagnostics,
        attachMcelSurfacePathway,
        buildReportBuckets,
        detectOverlays,
        detectLayoutCollisions,
        detectContentFitViolations,
        detectVisualIntegrityViolations,
        collectReadableTextBoxes,
        clippedPaintBox,
        clippedRangeBox,
        overlapMetrics,
        computeForbiddenRegionBox,
        selectorUsesDocumentScope
      })
    });

    try {
      global.McelSelfDiagnosis = api;
    } catch {}

    const existingMcel = global.MCEL || {};
    const existingDiagnose = existingMcel && existingMcel.diagnose;
    const facade = Object.assign({}, existingMcel, {
      diagnose(appId, options) {
        if (typeof appId === "string") {
          return api.diagnose(appId, options || {});
        }
        if (typeof existingDiagnose === "function") {
          return existingDiagnose.apply(this, arguments);
        }
        return api.diagnose("code-editor", appId || {});
      },
      exportDiagnosis: api.exportLastDiagnosis,
      selfDiagnosis: api
    });

    try {
      global.MCEL = facade;
    } catch {
      try {
        global.MCELDiagnosis = api;
      } catch {}
      return api;
    }

    try {
      global.MCELDiagnosis = api;
    } catch {}

    return api;
  }

  let lastReport = null;
  const global = typeof window !== "undefined" ? window : globalThis;
  registerOnGlobal(global);
})();
