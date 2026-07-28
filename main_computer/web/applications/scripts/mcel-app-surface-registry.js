var McelAppSurfaceRegistry = (() => {
  "use strict";

  const registryVersion = "mcel.app-surface-registry.v1";

  const BASELINE_LAYER_IDS = Object.freeze([
    "semantic-surface",
    "layout-grammar",
    "runtime-ownership",
    "runtime-visual-fit",
    "diagnostic-no-throw"
  ]);

  const RUNTIME_LAYER_IDS = Object.freeze([
    "runtime-ownership",
    "runtime-visual-fit",
    "diagnostic-no-throw"
  ]);

  function deepFreeze(value) {
    if (!value || typeof value !== "object") return value;
    Object.freeze(value);
    Object.keys(value).forEach((key) => {
      const child = value[key];
      if (child && typeof child === "object" && !Object.isFrozen(child)) deepFreeze(child);
    });
    return value;
  }

  function uniqueStrings(values) {
    return Object.freeze([...(new Set((values || []).map((value) => String(value || "").trim()).filter(Boolean)))]);
  }

  function clonePolicy(policy) {
    if (!policy) return null;
    return deepFreeze(JSON.parse(JSON.stringify(policy)));
  }

  const REQUIRED_APP_POLICIES = deepFreeze({
    "file-explorer": {
      appId: "file-explorer",
      label: "File Explorer",
      state: "surface-aware",
      conformanceRequired: true,
      maturity: "semantic-runtime",
      surfaceId: "file-explorer.surface.primary",
      contractId: "file-explorer.contract.default.app-health",
      requiredLayerIds: BASELINE_LAYER_IDS,
      notes: "First non-editor MCEL surface pilot; requires semantic extraction, layout grammar, runtime ownership, visual fit, and diagnostic no-throw checks."
    },
    "website-builder": {
      appId: "website-builder",
      label: "Website Builder",
      state: "surface-aware",
      conformanceRequired: true,
      maturity: "semantic-runtime",
      surfaceId: "website-builder.surface.preview",
      contractId: "website-builder.contract.default.app-health",
      requiredLayerIds: RUNTIME_LAYER_IDS,
      notes: "Website project authoring, runtime setup, publish-lane preflight, and Git Tools handoff semantics are proven through the domain adapter and acceptance evidence; static authored-surface parity remains separately reported."
    },
    "mcel-lab": {
      appId: "mcel-lab",
      label: "MCEL Lab",
      state: "surface-aware",
      conformanceRequired: true,
      maturity: "semantic-runtime",
      surfaceId: "mcel-lab.form.work-surface.blueprint-inspection",
      contractId: "mcel-lab.contract.default.blueprint-studio-health",
      requiredLayerIds: RUNTIME_LAYER_IDS,
      notes: "Blueprint inspection, registry truth consumption, contained preview evidence, annotation drafting, validation, and reviewable repair-context export are proven through the domain adapter and acceptance evidence; static authored-surface parity remains separately reported."
    },
    "git-tools": {
      appId: "git-tools",
      label: "Git Tools",
      state: "surface-aware",
      conformanceRequired: true,
      maturity: "runtime-baseline",
      surfaceId: "git-tools.surface.workflow",
      contractId: "git-tools.contract.default.app-health",
      requiredLayerIds: RUNTIME_LAYER_IDS,
      notes: "Repository workflow surface is enrolled for runtime ownership, visual fit, and diagnostic no-throw proof while the governed-publish adapter remains scope-limited and not full semantic-runtime."
    },
    "code-editor": {
      appId: "code-editor",
      label: "Code Editor",
      state: "surface-aware",
      conformanceRequired: true,
      maturity: "semantic-runtime",
      surfaceId: "code-editor.surface.monaco-selected-file-editor",
      contractId: "code-editor.contract.authoring.monaco-golden-path",
      requiredLayerIds: RUNTIME_LAYER_IDS,
      notes: "Source-safe Code Editor semantic runtime is proven through the domain adapter and acceptance evidence; static authored-surface parity remains separately reported."
    },
    calculator: {
      appId: "calculator",
      label: "Calculator",
      state: "surface-aware",
      conformanceRequired: true,
      maturity: "semantic-runtime",
      surfaceId: "calculator.surface.workspace",
      contractId: "calculator.contract.default.app-health",
      requiredLayerIds: BASELINE_LAYER_IDS,
      notes: "Multi-lane computation surface; requires Calculator semantic extraction, shared layout grammar, runtime ownership, visual fit, and diagnostic no-throw checks."
    },
    document: {
      appId: "document",
      label: "Document Editor",
      state: "surface-aware",
      conformanceRequired: true,
      maturity: "semantic-runtime",
      surfaceId: "document-editor.surface.primary",
      contractId: "document-editor.contract.default.app-health",
      requiredLayerIds: BASELINE_LAYER_IDS,
      notes: "Rich authoring surface pilot; requires document semantic extraction, shared layout grammar, runtime ownership, visual fit, and diagnostic no-throw checks."
    }
  });

  const LEGACY_APP_POLICIES = deepFreeze({
    "ai-control": {appId: "ai-control", label: "AI Control", state: "legacy", conformanceRequired: false},
    astrometric: {appId: "astrometric", label: "Astrometric", state: "legacy", conformanceRequired: false},
    "chat-console": {appId: "chat-console", label: "Chat Console", state: "legacy", conformanceRequired: false},
    conductor: {appId: "conductor", label: "Conductor", state: "legacy", conformanceRequired: false},
    email: {appId: "email", label: "Email", state: "legacy", conformanceRequired: false},
    "layout-builder": {appId: "layout-builder", label: "Layout Builder", state: "legacy", conformanceRequired: false},
    onlyoffice: {appId: "onlyoffice", label: "OnlyOffice", state: "legacy", conformanceRequired: false},
    spreadsheet: {appId: "spreadsheet", label: "Spreadsheet", state: "legacy", conformanceRequired: false},
    "spreadsheet-smoke": {appId: "spreadsheet-smoke", label: "Spreadsheet Smoke", state: "legacy", conformanceRequired: false},
    "task-manager": {appId: "task-manager", label: "Task Manager", state: "legacy", conformanceRequired: false},
    terminal: {appId: "terminal", label: "Terminal", state: "legacy", conformanceRequired: false},
    wallet: {appId: "wallet", label: "Wallet", state: "legacy", conformanceRequired: false},
    webgl: {appId: "webgl", label: "WebGL", state: "legacy", conformanceRequired: false},
    worker: {appId: "worker", label: "Worker", state: "legacy", conformanceRequired: false}
  });

  const APP_POLICIES = deepFreeze({
    ...REQUIRED_APP_POLICIES,
    ...LEGACY_APP_POLICIES
  });

  function unknownPolicy(appId) {
    const safeAppId = String(appId || "").trim();
    return deepFreeze({
      appId: safeAppId,
      label: safeAppId || "Unknown app",
      state: "unregistered",
      conformanceRequired: false,
      maturity: "unregistered",
      surfaceId: "",
      contractId: "",
      requiredLayerIds: Object.freeze([]),
      notes: "No MCEL app-surface registry policy is declared for this app."
    });
  }

  function normalizePolicy(policy, appId = "") {
    const input = policy && typeof policy === "object" ? policy : unknownPolicy(appId);
    const required = !!input.conformanceRequired;
    return deepFreeze({
      appId: String(input.appId || appId || ""),
      label: String(input.label || input.appId || appId || ""),
      state: String(input.state || (required ? "surface-aware" : "legacy")),
      conformanceRequired: required,
      maturity: String(input.maturity || (required ? "runtime-baseline" : input.state || "legacy")),
      surfaceId: String(input.surfaceId || ""),
      contractId: String(input.contractId || ""),
      requiredLayerIds: uniqueStrings(input.requiredLayerIds || (required ? BASELINE_LAYER_IDS : [])),
      notes: String(input.notes || "")
    });
  }

  function getAppPolicy(appId) {
    const key = String(appId || "").trim();
    return clonePolicy(normalizePolicy(APP_POLICIES[key] || unknownPolicy(key), key));
  }

  function isConformanceRequired(appId) {
    return !!getAppPolicy(appId)?.conformanceRequired;
  }

  function requiredLayerIdsForApp(appId) {
    return getAppPolicy(appId)?.requiredLayerIds || Object.freeze([]);
  }

  function listPolicies() {
    return deepFreeze(Object.keys(APP_POLICIES).sort().map((appId) => normalizePolicy(APP_POLICIES[appId], appId)));
  }

  function listConformanceRequiredApps() {
    return deepFreeze(listPolicies().filter((policy) => policy.conformanceRequired).map((policy) => policy.appId));
  }

  function listLegacyApps() {
    return deepFreeze(listPolicies().filter((policy) => !policy.conformanceRequired && policy.state === "legacy").map((policy) => policy.appId));
  }

  function summarizeRegistry() {
    const policies = listPolicies();
    const required = policies.filter((policy) => policy.conformanceRequired);
    const legacy = policies.filter((policy) => !policy.conformanceRequired && policy.state === "legacy");
    return deepFreeze({
      registryVersion,
      requiredCount: required.length,
      legacyCount: legacy.length,
      requiredAppIds: required.map((policy) => policy.appId),
      legacyAppIds: legacy.map((policy) => policy.appId)
    });
  }

  return deepFreeze({
    registryVersion,
    BASELINE_LAYER_IDS,
    RUNTIME_LAYER_IDS,
    getAppPolicy,
    isConformanceRequired,
    requiredLayerIdsForApp,
    listPolicies,
    listConformanceRequiredApps,
    listLegacyApps,
    summarizeRegistry
  });
})();

if (typeof window !== "undefined") {
  window.McelAppSurfaceRegistry = McelAppSurfaceRegistry;
  window.MCEL = Object.assign({}, window.MCEL || {}, {appSurfaceRegistry: McelAppSurfaceRegistry});
}
