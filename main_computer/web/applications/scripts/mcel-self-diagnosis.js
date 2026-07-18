(() => {
  "use strict";

  const VERSION = "mcel-self-diagnosis-v1";
  const REPORT_SCHEMA = "mcel-self-diagnosis-report-v1";

  const CODE_EDITOR_AUTHORING_CONTRACT = deepFreeze({
    contractId: "code-editor.contract.authoring.monaco-golden-path",
    appId: "code-editor",
    mode: "authoring",
    intent: "Expose one usable Monaco selected-file editor and keep MCEL diagnostic surfaces out of the default editing path.",
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
      {
        id: "code-editor.region.root",
        selector: "#code-editor-app",
        label: "Code Editor app root"
      },
      {
        id: "code-editor.region.explorer",
        selector: ".code-studio-sidebar",
        label: "Explorer"
      },
      {
        id: "code-editor.region.editor-group",
        selector: ".code-studio-editor-group",
        label: "Editor group"
      }
    ],
    forbiddenRegions: [
      {
        id: "code-editor.forbidden.source-pane",
        selector: '[data-code-studio-pane="source"]',
        label: "MCEL source model pane"
      },
      {
        id: "code-editor.forbidden.serialized-pane",
        selector: '[data-code-studio-pane="serialized"]',
        label: "Serialized output pane"
      },
      {
        id: "code-editor.forbidden.contract-pane",
        selector: '[data-code-studio-pane="contract"]',
        label: "Contract report pane"
      },
      {
        id: "code-editor.forbidden.runtime-scaffold.window",
        selector: ".code-studio-runtime-window",
        label: "Generated runtime window scaffold"
      },
      {
        id: "code-editor.forbidden.runtime-scaffold.layout",
        selector: ".code-studio-runtime-layout",
        label: "Generated runtime layout scaffold"
      },
      {
        id: "code-editor.forbidden.runtime-file-rail",
        selector: ".code-studio-runtime-files",
        label: "Generated runtime file rail"
      },
      {
        id: "code-editor.forbidden.fallback-textarea",
        selector: "#code-studio-runtime-draft, .code-studio-runtime-fallback",
        label: "Fallback textarea"
      },
      {
        id: "code-editor.forbidden.proof-dock",
        selector: ".code-studio-proof-dock, #code-studio-bottom-panel",
        label: "MCEL proof/evidence dock"
      },
      {
        id: "code-editor.forbidden.widget-overlay",
        selector: "#mc-widget-editor-root",
        label: "Widget editor overlay"
      }
    ],
    lifecycleAssertions: [
      "startup-authoring-mode-has-one-primary-editor",
      "file-click-keeps-one-primary-editor",
      "mcel-diagnostics-hidden-in-authoring"
    ]
  });


  function normalizeList(value) {
    return Array.isArray(value) ? value.filter(Boolean) : [];
  }

  function normalizePrimarySurface(surface) {
    const fallback = CODE_EDITOR_AUTHORING_CONTRACT.primarySurface;
    const input = surface && typeof surface === "object" ? surface : {};
    return {
      id: String(input.id || fallback.id || ""),
      label: String(input.label || fallback.label || ""),
      hostSelector: String(input.hostSelector || input.host_selector || fallback.hostSelector || ""),
      editorSelector: String(input.editorSelector || input.editor_selector || fallback.editorSelector || ""),
      minWidth: Number(input.minWidth || input.min_width || fallback.minWidth || 800),
      minHeight: Number(input.minHeight || input.min_height || fallback.minHeight || 600)
    };
  }

  function normalizeRegionEntries(entries, fallbackEntries) {
    const source = Array.isArray(entries) && entries.length ? entries : fallbackEntries;
    return normalizeList(source).map((entry) => ({
      id: String(entry?.id || entry?.selector || ""),
      selector: String(entry?.selector || entry?.id || ""),
      label: String(entry?.label || entry?.id || entry?.selector || "")
    }));
  }

  function normalizeDiagnosisContract(contract) {
    const input = contract && typeof contract === "object" ? contract : CODE_EDITOR_AUTHORING_CONTRACT;
    return {
      contractId: String(input.contractId || input.contract_id || CODE_EDITOR_AUTHORING_CONTRACT.contractId),
      appId: String(input.appId || input.app || input.app_id || CODE_EDITOR_AUTHORING_CONTRACT.appId),
      mode: String(input.mode || CODE_EDITOR_AUTHORING_CONTRACT.mode),
      intent: String(input.intent || CODE_EDITOR_AUTHORING_CONTRACT.intent || ""),
      source: String(input.source || "static-fallback"),
      derivedFromBlockTypes: normalizeList(input.derivedFromBlockTypes || input.derived_from_block_types || CODE_EDITOR_AUTHORING_CONTRACT.derivedFromBlockTypes),
      primarySurface: normalizePrimarySurface(input.primarySurface || input.primary_surface),
      requiredRegions: normalizeRegionEntries(input.requiredRegions || input.required_regions, CODE_EDITOR_AUTHORING_CONTRACT.requiredRegions),
      forbiddenRegions: normalizeRegionEntries(input.forbiddenRegions || input.forbidden_regions, CODE_EDITOR_AUTHORING_CONTRACT.forbiddenRegions),
      lifecycleAssertions: normalizeList(input.lifecycleAssertions || input.lifecycle_assertions || CODE_EDITOR_AUTHORING_CONTRACT.lifecycleAssertions),
      checks: normalizeList(input.checks)
    };
  }

  function getRegistryDiagnosisContract(appId, mode) {
    const registry = globalThis.McelRequirementsRegistry || globalThis.window?.McelRequirementsRegistry;
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
    if (options.contract) return normalizeDiagnosisContract(options.contract);
    const mode = String(options.mode || CODE_EDITOR_AUTHORING_CONTRACT.mode);
    const registryContract = getRegistryDiagnosisContract(appId, mode);
    return normalizeDiagnosisContract(registryContract || CODE_EDITOR_AUTHORING_CONTRACT);
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

  function computeBox(el) {
    if (!isElement(el)) {
      return {
        exists: false,
        selector: "",
        visible: false,
        width: 0,
        height: 0
      };
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
      inlineStyle: el.getAttribute?.("style") || "",
      childCount: el.children?.length || 0,
      textPreview: textPreview(el),
      valuePreview: valuePreview(el)
    };
  }

  function getComputedStyleSafe(el) {
    try {
      if (typeof getComputedStyle === "function") {
        return getComputedStyle(el);
      }
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
    findings.push({
      severity,
      code,
      finding,
      evidence,
      recommendedNextProbe
    });
  }

  function normalizedMode(snapshot) {
    return String(snapshot?.mode || snapshot?.root?.mode || "unknown").trim() || "unknown";
  }

  function visibleAndUseful(box, minWidth, minHeight) {
    return Boolean(box?.exists && box.visible && box.width >= minWidth && box.height >= minHeight);
  }

  function anyVisible(boxes) {
    return boxes.some((box) => Boolean(box?.exists && box.visible));
  }

  function firstVisible(boxes) {
    return boxes.find((box) => Boolean(box?.exists && box.visible)) || null;
  }

  function evaluateCodeEditorAuthoringSnapshot(snapshot, options = {}) {
    const contract = normalizeDiagnosisContract(options.contract || CODE_EDITOR_AUTHORING_CONTRACT);
    const minWidth = Number(options.minWidth || contract.primarySurface.minWidth || 800);
    const minHeight = Number(options.minHeight || contract.primarySurface.minHeight || 600);
    const findings = [];
    const mode = normalizedMode(snapshot);
    const surfaces = snapshot.surfaces || {};
    const host = surfaces.monacoHost || {};
    const monaco = surfaces.monacoEditor || {};
    const source = surfaces.sourceTextarea || {};
    const runtimeDraft = surfaces.runtimeDraft || {};
    const fallback = surfaces.fallbackTextarea || {};
    const requiredRegions = snapshot.requiredRegions || {};
    const forbiddenRegions = snapshot.forbiddenRegions || [];
    const ownerChain = Array.isArray(snapshot.ownerChain) ? snapshot.ownerChain : [];

    if (mode !== contract.mode) {
      addFinding(
        findings,
        "warning",
        "mode-mismatch",
        `Expected ${contract.mode} mode while evaluating Code Editor authoring contract.`,
        {expected: contract.mode, actual: mode},
        "mode.contractAudit"
      );
    }

    for (const region of contract.requiredRegions) {
      const observed = requiredRegions[region.id] || requiredRegions[region.selector] || {};
      if (!observed.exists) {
        addFinding(
          findings,
          "critical",
          "required-region-missing",
          `${region.label} is missing.`,
          {region},
          "layout.baseline"
        );
      } else if (!observed.visible) {
        addFinding(
          findings,
          "critical",
          "required-region-hidden",
          `${region.label} exists but is not visible.`,
          {region, observed},
          "layout.ownerProbe"
        );
      }
    }

    if (!host.exists) {
      addFinding(
        findings,
        "critical",
        "primary-editor-host-missing",
        "Code Editor authoring contract requires a Monaco host, but no host was found.",
        {selector: contract.primarySurface.hostSelector},
        "editor.surfaceAudit"
      );
    }

    if (!monaco.exists) {
      addFinding(
        findings,
        "critical",
        "primary-editor-missing",
        "Code Editor authoring contract requires a Monaco editor instance, but none was found.",
        {selector: contract.primarySurface.editorSelector, host},
        "editor.surfaceAudit"
      );
    }

    if (host.exists && !visibleAndUseful(host, minWidth, minHeight)) {
      addFinding(
        findings,
        "critical",
        "primary-editor-host-unusable",
        "Monaco host exists but is not a usable editor surface.",
        {
          selector: contract.primarySurface.hostSelector,
          minWidth,
          minHeight,
          observed: host
        },
        "layout.ownerProbe"
      );
    }

    if (monaco.exists && !visibleAndUseful(monaco, minWidth, minHeight)) {
      addFinding(
        findings,
        "critical",
        "primary-editor-unusable",
        "Monaco editor exists but is not a usable editor surface.",
        {
          selector: contract.primarySurface.editorSelector,
          minWidth,
          minHeight,
          observed: monaco
        },
        "layout.ownerProbe"
      );
    }

    const competing = [source, runtimeDraft, fallback].filter((box) => Boolean(box?.exists && box.visible));
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
        "critical",
        "forbidden-region-visible",
        `${entry.label || entry.selector} is visible even though the authoring contract forbids it.`,
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
        "The primary editor is collapsed by an ancestor or assigned parent track.",
        collapsedOwner,
        "layout.ownerProbe"
      );
    }

    const primarySurfaceUsable = visibleAndUseful(host, minWidth, minHeight) && visibleAndUseful(monaco, minWidth, minHeight);
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
          editor: compactBox(monaco)
        }
      },
      findings,
      measurements: {
        viewport: snapshot.viewport || {},
        requiredRegions,
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
        lifecycleAssertions: contract.lifecycleAssertions
      }
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

  function buildCodeEditorSnapshot(options = {}) {
    const doc = getDocument();
    const appId = "code-editor";
    const root = query("#code-editor-app", doc);
    const contract = resolveDiagnosisContract(appId, options);
    const rootMode = root?.dataset?.codeEditorMode ||
      root?.getAttribute?.("data-code-editor-mode") ||
      "unknown";

    const requiredRegions = {};
    for (const region of contract.requiredRegions) {
      requiredRegions[region.id] = computeBox(query(region.selector, root || doc));
      requiredRegions[region.selector] = requiredRegions[region.id];
    }

    const host = query(contract.primarySurface.hostSelector, root || doc);
    const monaco = query(contract.primarySurface.editorSelector, root || doc);
    const sourceTextarea = query("#code-studio-source-editor", root || doc);
    const runtimeDraft = query("#code-studio-runtime-draft", root || doc);
    const fallbackTextarea = query(".code-studio-runtime-fallback", root || doc);
    const activePane = query("[data-code-studio-pane].active", root || doc);

    const forbiddenRegions = [];
    for (const forbidden of contract.forbiddenRegions) {
      const matches = queryAll(forbidden.selector, root || doc);
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
            box: computeBox(el)
          });
        });
      }
    }

    const ownerTarget = options.ownerTargetSelector
      ? query(options.ownerTargetSelector, root || doc)
      : host || monaco || activePane || root;
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
      surfaces: {
        monacoHost: computeBox(host),
        monacoEditor: computeBox(monaco),
        sourceTextarea: computeBox(sourceTextarea),
        runtimeDraft: computeBox(runtimeDraft),
        fallbackTextarea: computeBox(fallbackTextarea),
        activePane: computeBox(activePane)
      },
      forbiddenRegions,
      ownerChain,
      overlays: detectOverlays(root || doc),
      panes: collectPaneState(root || doc)
    };
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

  function detectOverlays(root) {
    const doc = getDocument();
    if (!doc) return [];
    const selectors = [
      "#mc-widget-editor-root",
      ".code-studio-proof-dock",
      "#code-studio-bottom-panel",
      ".floating-tab",
      ".side-tab",
      ".vertical-tab",
      "[data-mcel-proof-surface]",
      "[data-code-studio-panel=\"assistant\"]"
    ];
    const seen = new Set();
    const overlays = [];
    selectors.forEach((selector) => {
      queryAll(selector, doc).forEach((el) => {
        if (seen.has(el)) return;
        seen.add(el);
        const box = computeBox(el);
        if (box.exists) {
          overlays.push({
            selector,
            box,
            insideRoot: root && root.contains ? root.contains(el) : false
          });
        }
      });
    });
    return overlays;
  }

  function diagnose(appId = "code-editor", options = {}) {
    if (appId !== "code-editor") {
      return unsupportedAppReport(appId);
    }

    const contract = resolveDiagnosisContract(appId, options);
    const snapshot = buildCodeEditorSnapshot({...options, contract});
    const report = evaluateCodeEditorAuthoringSnapshot(snapshot, {...options, contract});
    report.focus = options.focus || "contract";
    report.measurements.overlays = snapshot.overlays;
    report.measurements.panes = snapshot.panes;

    const visibleOverlays = snapshot.overlays.filter((entry) => entry.box?.visible);
    if (visibleOverlays.length) {
      const findings = report.findings || [];
      addFinding(
        findings,
        "warning",
        "visible-overlay-detected",
        "Overlay or diagnostic surfaces are visible while diagnosing the app.",
        {visibleOverlays},
        "overlay.detector"
      );
      report.summary = {
        ...report.summary,
        ...severityCounts(findings)
      };
      report.verdict = findings.some((finding) => finding.severity === "critical") ? "fail" : report.verdict;
    }

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
    const registryContract = getRegistryDiagnosisContract("code-editor", "authoring");
    return [normalizeDiagnosisContract(registryContract || CODE_EDITOR_AUTHORING_CONTRACT)];
  }

  function registerOnGlobal(global) {
    const api = deepFreeze({
      VERSION,
      REPORT_SCHEMA,
      CODE_EDITOR_AUTHORING_CONTRACT,
      diagnose,
      exportLastDiagnosis,
      listContracts,
      buildCodeEditorSnapshot,
      evaluateCodeEditorAuthoringSnapshot,
      resolveDiagnosisContract,
      _private: deepFreeze({
        computeBox,
        collectOwnerChain,
        findCollapsedOwner,
        visibleAndUseful
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
