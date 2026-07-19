(() => {
  "use strict";

  const VERSION = "mcel-self-diagnosis-v2";
  const REPORT_SCHEMA = "mcel-self-diagnosis-report-v2";

  const APP_MODE_DEFAULTS = Object.freeze({
    "code-editor": "authoring",
    calculator: "default",
    "file-explorer": "default",
    "git-tools": "default",
    "website-builder": "default"
  });

  const COMMON_OVERLAY_SELECTORS = Object.freeze([
    "#mc-widget-editor-root",
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
      intent: "Expose one usable Monaco selected-file editor with an owned explorer, optional right assistant/diagnostics pane, and status bar.",
      derivedFromBlockTypes: [
        "mcel-region",
        "mcel-requirement",
        "mcel-acceptance",
        "mcel-boundary",
        "mcel-source-binding",
        "mcel-test-binding",
        "mcel-runtime-check"
      ],
      primarySurface: {
        id: "code-editor.surface.monaco-selected-file-editor",
        label: "Monaco selected-file editor",
        hostSelector: "#code-studio-runtime-monaco",
        editorSelector: ".monaco-editor",
        minWidth: 800,
        minHeight: 600
      },
      requiredRegions: [
        {id: "code-editor.region.root", selector: "#code-editor-app", label: "Code Editor app root"},
        {id: "code-editor.region.explorer", selector: ".code-studio-sidebar", label: "Explorer"},
        {id: "code-editor.region.editor-group", selector: ".code-studio-editor-group", label: "Editor group"},
        {id: "code-editor.region.status-bar", selector: ".code-studio-statusbar", label: "Status bar"}
      ],
      optionalRegions: [
        {id: "code-editor.region.right-pane", selector: ".code-studio-inspector", label: "Right assistant/diagnostics pane", role: "secondary-assistant"}
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
        {id: "code-editor.forbidden.widget-overlay", selector: "#mc-widget-editor-root", label: "Widget editor overlay"}
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
          editor: compactBox(primaryEditor)
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
        ownerChain
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
      const forbiddenScope = selector.includes("#mc-widget-editor-root") ? doc : scope;
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
            box: computeBox(el),
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
    const isWidgetEditor = selector === "#mc-widget-editor-root";
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
      else if (code.includes("layout") || code.includes("collapsed")) buckets.activeLayoutIssues.push(compact);
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
    report = applyOverlayFindings(report, snapshot);
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
        buildReportBuckets,
        detectOverlays
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
