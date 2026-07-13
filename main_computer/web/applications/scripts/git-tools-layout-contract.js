(() => {
  "use strict";

  const globalObject = typeof window !== "undefined" ? window : globalThis;
  const CONTRACT_VERSION = "mcel-git-tools-layout.v2";
  const PREFERENCES_VERSION = 2;
  const STORAGE_KEY = "main-computer-git-tools-layout-preferences-v2";
  const LEGACY_STORAGE_KEY = "main-computer-git-tools-layout-preferences-v1";
  const HISTORY_LIMIT = 20;
  const RAW_GEOMETRY_KEYS = Object.freeze([
    "x", "y", "w", "h", "left", "top", "right", "bottom", "width", "height"
  ]);

  function deepFreeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.freeze(value);
    Object.values(value).forEach(deepFreeze);
    return value;
  }

  const APPLICATION_CONTRACT = deepFreeze({
    id: "git-tools",
    version: 1,
    root: "git-tools-application",
    description: "Repository workflow workbench with persistent project context, dominant action workflow, and phase-aware support.",
    units: {
      "repository.project-identity": {
        role: "project-identity",
        selector: ".git-project-roster",
        required: true,
      },
      "repository.command": {
        role: "command",
        selector: "#git-tools-command-surface",
        required: true,
      },
      "repository.command-workflow": {
        role: "primary-work",
        selector: ".git-project-main",
        required: true,
      },
      "repository.phase-support": {
        role: "phase-support",
        selector: "#git-workflow-accordion",
        required: false,
      },
      "repository.persistent-feedback": {
        role: "persistent-feedback",
        selector: "#git-tools-feedback-band",
        required: true,
      },
    },
    relationships: [
      {subject: "repository.project-identity", relation: "scopes", object: "repository.command-workflow", strength: "hard"},
      {subject: "repository.command", relation: "controls", object: "repository.command-workflow", strength: "hard"},
      {subject: "repository.phase-support", relation: "supports", object: "repository.command-workflow", strength: "strong"},
      {subject: "repository.persistent-feedback", relation: "confirms", object: "repository.command-workflow", strength: "hard"},
    ],
    invariants: [
      "selected-project-context-remains-attributable",
      "workflow-remains-the-primary-work-surface",
      "critical-actions-remain-reachable",
      "support-remediation-is-monotonic",
      "user-preferences-remain-semantic",
      "status-remains-persistent",
      "no-undeclared-overlap",
    ],
    phases: [
      "project-selection",
      "selected-project-default",
      "planning",
      "execution",
      "proof-review",
      "recovery",
    ],
    operations: {
      dock: {requires: ["placement"], mutableCapability: "placement"},
      "resize-share": {requires: ["share"], mutableCapability: "share"},
      collapse: {requires: ["collapsed"], mutableCapability: "collapsed"},
      "tab-with": {requires: ["targetUserId"], mutableCapability: "tab-group"},
      "select-support": {requires: ["view"]},
      "select-surface": {requires: ["surface"]},
      "select-stage": {requires: ["stage"]},
      undo: {requires: []},
      reset: {requires: []},
    },
  });

  const SAFE_DEFAULTS = deepFreeze({
    "repository.project-identity": {
      prefer: "left",
      allowed: ["left", "top", "stage", "trigger"],
      fallback: ["top", "stage", "trigger"],
      strength: "strong",
      minInline: 220,
      minBlock: 120,
      maxShare: 0.24,
      preferredShare: 0.20,
      mutable: ["placement", "collapsed"],
    },
    "repository.command": {
      prefer: "top",
      allowed: ["top"],
      fallback: [],
      strength: "required",
      minInline: 0,
      minBlock: 44,
      maxShare: 0.10,
      preferredShare: 1,
      mutable: [],
    },
    "repository.command-workflow": {
      prefer: "center",
      allowed: ["center"],
      fallback: [],
      strength: "required",
      minInline: 520,
      minBlock: 360,
      maxShare: 0.78,
      preferredShare: 1,
      mutable: [],
    },
    "repository.phase-support": {
      prefer: "right",
      allowed: ["right", "bottom", "tab", "stage", "trigger"],
      fallback: ["bottom", "tab", "stage", "trigger"],
      strength: "preferred",
      minInline: 420,
      minBlock: 260,
      maxShare: 0.32,
      preferredShare: 0.28,
      mutable: ["placement", "share", "collapsed", "tab-group"],
    },
    "repository.persistent-feedback": {
      prefer: "bottom",
      allowed: ["bottom", "top"],
      fallback: ["top"],
      strength: "strong",
      minInline: 420,
      minBlock: 44,
      maxShare: 0.10,
      preferredShare: 1,
      mutable: ["placement"],
    },
  });

  const DEFAULT_PREFERENCES = deepFreeze({
    version: PREFERENCES_VERSION,
    units: {
      "repository.project-identity": {
        placement: "left",
        preferredShare: 0.20,
        collapsed: false,
        tabWith: "",
      },
      "repository.phase-support": {
        placement: "right",
        preferredShare: 0.28,
        collapsed: false,
        tabWith: "repository.command-workflow",
      },
      "repository.persistent-feedback": {
        placement: "bottom",
        preferredShare: 1,
        collapsed: false,
        tabWith: "",
      },
    },
  });

  const CAPACITY_BANDS = deepFreeze([
    {id: "wide", minWidth: 1440, maxRemediationLevel: 0},
    {id: "medium", minWidth: 1024, maxRemediationLevel: 1},
    {id: "narrow", minWidth: 720, maxRemediationLevel: 2},
    {id: "compact", minWidth: 0, maxRemediationLevel: 3},
  ]);

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function numberOr(value, fallback) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : fallback;
  }

  function tokenList(value) {
    return String(value || "")
      .trim()
      .split(/[\s,]+/)
      .map((entry) => entry.trim())
      .filter(Boolean);
  }

  function unique(values) {
    return [...new Set(values)];
  }

  function capacityForWidth(width) {
    const safeWidth = Math.max(320, numberOr(width, 1440));
    return CAPACITY_BANDS.find((band) => safeWidth >= band.minWidth) || CAPACITY_BANDS[CAPACITY_BANDS.length - 1];
  }

  function elementForUnit(root, unitId) {
    const definition = APPLICATION_CONTRACT.units[unitId];
    if (!root || !definition?.selector) return null;
    return root.querySelector(definition.selector);
  }

  function hintFromElement(element, fallback) {
    const dataset = element?.dataset || {};
    const allowed = tokenList(dataset.mcLayoutAllowed);
    const fallbacks = tokenList(dataset.mcLayoutFallback);
    const mutable = tokenList(dataset.mcLayoutUserMutable);
    return {
      prefer: dataset.mcLayoutPrefer || fallback.prefer,
      allowed: allowed.length ? allowed : [...fallback.allowed],
      fallback: fallbacks.length ? fallbacks : [...fallback.fallback],
      strength: dataset.mcLayoutStrength || fallback.strength,
      policy: dataset.mcLayoutPolicy || "",
      inactive: dataset.mcLayoutInactive || "",
      minInline: numberOr(dataset.mcLayoutMinInline, fallback.minInline),
      minBlock: numberOr(dataset.mcLayoutMinBlock, fallback.minBlock),
      maxShare: clamp(numberOr(dataset.mcLayoutMaxShare, fallback.maxShare), 0.05, 1),
      preferredShare: clamp(numberOr(dataset.mcLayoutPreferredShare, fallback.preferredShare || 0.2), 0.05, 1),
      mutable: mutable.length ? mutable : [...fallback.mutable],
      userId: dataset.mcLayoutUserId || "",
      componentId: dataset.mcComponentId || "",
    };
  }

  function extractAuthoredContract(root) {
    const units = {};
    const missing = [];
    const mismatches = [];
    for (const [unitId, definition] of Object.entries(APPLICATION_CONTRACT.units)) {
      const fallback = SAFE_DEFAULTS[unitId];
      const element = elementForUnit(root, unitId);
      if (!element && definition.required) missing.push(unitId);
      const hint = hintFromElement(element, fallback);
      if (element && hint.userId && hint.userId !== unitId) {
        mismatches.push(`${unitId} is authored as ${hint.userId}`);
      }
      units[unitId] = {
        id: unitId,
        role: definition.role,
        selector: definition.selector,
        required: definition.required === true,
        present: Boolean(element),
        ...hint,
      };
    }
    const shell = root?.querySelector?.(".git-tools-shell") || null;
    return {
      id: APPLICATION_CONTRACT.id,
      version: APPLICATION_CONTRACT.version,
      contractVersion: CONTRACT_VERSION,
      root: APPLICATION_CONTRACT.root,
      layout: shell?.dataset?.mcLayout || "dock-workbench",
      policy: shell?.dataset?.mcLayoutPolicy || "dominant-workflow-stack",
      zones: tokenList(shell?.dataset?.mcLayoutZones || "top left center right bottom tab stage trigger"),
      units,
      relationships: clone(APPLICATION_CONTRACT.relationships),
      invariants: [...APPLICATION_CONTRACT.invariants],
      phases: [...APPLICATION_CONTRACT.phases],
      missing,
      mismatches,
      complete: missing.length === 0 && mismatches.length === 0,
    };
  }

  function rejectRawGeometry(value, path = "preferences") {
    if (!value || typeof value !== "object") return [];
    const violations = [];
    for (const [key, child] of Object.entries(value)) {
      const childPath = `${path}.${key}`;
      if (RAW_GEOMETRY_KEYS.includes(String(key).toLowerCase())) violations.push(childPath);
      violations.push(...rejectRawGeometry(child, childPath));
    }
    return violations;
  }

  function normalizeUnitPreference(unitId, input, authored) {
    const unit = authored?.units?.[unitId] || SAFE_DEFAULTS[unitId];
    const defaults = DEFAULT_PREFERENCES.units[unitId] || {};
    const source = input && typeof input === "object" ? input : {};
    const allowed = unit.allowed || [];
    const fallbackPlacement = defaults.placement || unit.prefer || allowed[0] || "";
    let placement = String(source.placement || fallbackPlacement);
    if (!allowed.includes(placement)) placement = fallbackPlacement;
    return {
      placement,
      preferredShare: clamp(
        numberOr(source.preferredShare, defaults.preferredShare || unit.preferredShare || 0.2),
        0.08,
        Math.max(0.08, unit.maxShare || 1),
      ),
      collapsed: source.collapsed === true,
      tabWith: String(source.tabWith || defaults.tabWith || ""),
    };
  }

  function normalizePreferences(input, authored) {
    const source = input && typeof input === "object" ? input : {};
    const units = {};
    for (const unitId of Object.keys(DEFAULT_PREFERENCES.units)) {
      units[unitId] = normalizeUnitPreference(unitId, source.units?.[unitId], authored);
    }
    return {
      version: PREFERENCES_VERSION,
      units,
      rawGeometryViolations: rejectRawGeometry(source),
    };
  }

  function placementOrder(preference, unit) {
    const initial = preference.collapsed ? "trigger" : preference.placement;
    return unique([initial, ...(unit.fallback || []), "trigger"])
      .filter((placement) => unit.allowed.includes(placement));
  }

  function supportPlacementForCapacity(preference, unit, capacity) {
    const allowedByCapacity = {
      wide: ["right", "bottom", "tab", "stage", "trigger"],
      medium: ["bottom", "tab", "stage", "trigger"],
      narrow: ["tab", "stage", "trigger"],
      compact: ["stage", "trigger"],
    }[capacity] || ["trigger"];
    return placementOrder(preference, unit).find((placement) => allowedByCapacity.includes(placement)) || "trigger";
  }

  function identityPlacementForCapacity(preference, unit, capacity, phase) {
    if (preference.collapsed) return "trigger";
    const desired = preference.placement;
    if (capacity === "wide") return desired === "top" ? "top" : "left";
    if (capacity === "medium") return desired === "top" ? "top" : "left";
    if (capacity === "narrow") {
      if (phase === "project-selection") return desired === "trigger" ? "trigger" : "stage";
      return "trigger";
    }
    if (phase === "project-selection") return "stage";
    return "trigger";
  }

  function dimensionForShare(total, share, minimum, maximumShare, hardMaximum = Infinity) {
    const maximum = Math.min(total * maximumShare, hardMaximum);
    return Math.round(clamp(total * share, Math.min(minimum, maximum), maximum));
  }

  function normalizePhase(value) {
    const phase = String(value || "");
    return APPLICATION_CONTRACT.phases.includes(phase) ? phase : "selected-project-default";
  }

  function normalizeSupportView(value) {
    const view = String(value || "");
    return ["server", "evidence", "advanced"].includes(view) ? view : "server";
  }

  function normalizeStage(value) {
    const stage = String(value || "");
    return ["identity", "workflow", "support"].includes(stage) ? stage : "workflow";
  }

  function normalizeActiveSurface(value) {
    const surface = String(value || "");
    return ["identity", "workflow", "support"].includes(surface) ? surface : "workflow";
  }

  function openSupportPlacementForCapacity(preference, unit, capacity) {
    const placement = preference?.placement === "trigger"
      ? unit?.prefer || "right"
      : preference?.placement || unit?.prefer || "right";
    return supportPlacementForCapacity(
      {...preference, placement, collapsed: false},
      unit,
      capacity,
    );
  }

  function resolveLayout({
    viewport = {},
    authored,
    preferences,
    phase = "selected-project-default",
    supportView = "server",
    centerTab = "workflow",
    stage = "workflow",
    activeSurface = "workflow",
    identityOpen = false,
    supportOpen = false,
  }) {
    const width = Math.max(320, numberOr(viewport.width, 1440));
    const height = Math.max(480, numberOr(viewport.height, 900));
    const capacityBand = capacityForWidth(width);
    const capacity = capacityBand.id;
    const safePhase = normalizePhase(phase);
    const safeActiveSurface = normalizeActiveSurface(activeSurface);
    const normalized = normalizePreferences(preferences, authored);
    const identityUnit = authored?.units?.["repository.project-identity"] || SAFE_DEFAULTS["repository.project-identity"];
    const supportUnit = authored?.units?.["repository.phase-support"] || SAFE_DEFAULTS["repository.phase-support"];
    const identityPreference = normalized.units["repository.project-identity"];
    const supportPreference = normalized.units["repository.phase-support"];
    const feedbackPreference = normalized.units["repository.persistent-feedback"];

    const identity = identityOpen
      ? capacity === "compact" || capacity === "narrow"
        ? "stage"
        : identityPreference.placement === "top"
            ? "top"
            : "left"
      : identityPlacementForCapacity(identityPreference, identityUnit, capacity, safePhase);
    const support = supportOpen
      ? openSupportPlacementForCapacity(supportPreference, supportUnit, capacity)
      : supportPlacementForCapacity(supportPreference, supportUnit, capacity);
    const feedback = feedbackPreference.placement === "top" ? "top" : "bottom";
    let resolvedCenterTab = safeActiveSurface === "support" ? "support" : "workflow";
    let resolvedStage = normalizeStage(stage);

    if (support !== "tab") resolvedCenterTab = "workflow";
    if (support === "tab" && safeActiveSurface === "support") resolvedStage = "support";
    if (support !== "stage" && identity !== "stage") resolvedStage = "workflow";
    if (identity === "stage" && safeActiveSurface === "identity") resolvedStage = "identity";
    if (
      identity === "stage"
      && safePhase === "project-selection"
      && safeActiveSurface === "workflow"
      && !supportOpen
    ) {
      resolvedStage = "identity";
    }
    if (support === "stage" && safeActiveSurface === "support") {
      resolvedStage = "support";
      resolvedCenterTab = "support";
    }
    if (
      (support === "stage" || identity === "stage")
      && safeActiveSurface === "workflow"
      && !(identity === "stage" && safePhase === "project-selection" && !supportOpen)
    ) {
      resolvedStage = "workflow";
    }

    const identityInline = identity === "left"
      ? dimensionForShare(width, identityPreference.preferredShare, identityUnit.minInline, identityUnit.maxShare, 360)
      : 0;
    const supportInline = support === "right"
      ? dimensionForShare(width, supportPreference.preferredShare, supportUnit.minInline, supportUnit.maxShare, 540)
      : 0;
    const supportBlock = support === "bottom"
      ? dimensionForShare(height, supportPreference.preferredShare, supportUnit.minBlock, 0.42, 380)
      : 0;
    const identityBlock = identity === "top"
      ? Math.round(clamp(height * 0.24, identityUnit.minBlock, 240))
      : 0;

    const preferred = {
      identity: identityPreference.collapsed ? "trigger" : identityPreference.placement,
      support: supportPreference.collapsed ? "trigger" : supportPreference.placement,
      feedback: feedbackPreference.placement,
    };
    const actual = {identity, support, feedback};
    const explanations = [];
    if (actual.identity !== preferred.identity) {
      explanations.push(`project identity remediated from ${preferred.identity} to ${actual.identity} at ${capacity} capacity`);
    }
    if (actual.support !== preferred.support) {
      explanations.push(`phase support remediated from ${preferred.support} to ${actual.support} at ${capacity} capacity`);
    }

    return {
      contractVersion: CONTRACT_VERSION,
      complete: Boolean(authored?.complete),
      capacity,
      remediationLevel: capacityBand.maxRemediationLevel,
      viewport: {width, height},
      phase: safePhase,
      supportView: normalizeSupportView(supportView),
      activeSurface: safeActiveSurface,
      centerTab: resolvedCenterTab,
      stage: resolvedStage,
      identityOpen: identityOpen === true,
      supportOpen: supportOpen === true,
      preferred,
      actual,
      dimensions: {
        identityInline,
        identityBlock,
        supportInline,
        supportBlock,
        commandBlock: 52,
        feedbackBlock: 48,
      },
      preferences: normalized,
      remediated: explanations.length > 0,
      explanations,
      contractDiagnostics: {
        missing: [...(authored?.missing || [])],
        mismatches: [...(authored?.mismatches || [])],
        rawGeometryViolations: [...normalized.rawGeometryViolations],
      },
    };
  }

  function validateOperation(operation, authored) {
    const source = operation && typeof operation === "object" ? operation : {};
    const kind = String(source.kind || "");
    if (!APPLICATION_CONTRACT.operations[kind]) return {ok: false, reason: `unknown operation: ${kind}`};
    if (["undo", "reset", "select-support", "select-surface", "select-stage"].includes(kind)) return {ok: true, operation: source};
    const userId = String(source.userId || "");
    const unit = authored?.units?.[userId];
    if (!unit) return {ok: false, reason: `unknown layout unit: ${userId}`};
    const capabilityByKind = {
      dock: "placement",
      "resize-share": "share",
      collapse: "collapsed",
      "tab-with": "tab-group",
    };
    const capability = capabilityByKind[kind];
    if (capability && !(unit.mutable || []).includes(capability)) {
      return {ok: false, reason: `${userId} does not permit ${capability}`};
    }
    if (kind === "dock" && !unit.allowed.includes(String(source.placement || ""))) {
      return {ok: false, reason: `${source.placement} is not allowed for ${userId}`};
    }
    return {ok: true, operation: source};
  }

  function applyOperationToPreferences(preferences, operation, authored) {
    const check = validateOperation(operation, authored);
    if (!check.ok) return {ok: false, reason: check.reason, preferences: clone(preferences)};
    if (operation.kind === "reset") {
      return {ok: true, preferences: normalizePreferences(DEFAULT_PREFERENCES, authored)};
    }
    if (["undo", "select-support", "select-surface", "select-stage"].includes(operation.kind)) {
      return {ok: true, preferences: normalizePreferences(preferences, authored)};
    }
    const next = normalizePreferences(preferences, authored);
    const unit = next.units[operation.userId];
    if (!unit) return {ok: false, reason: `unknown layout unit: ${operation.userId}`, preferences: next};
    if (operation.kind === "dock") {
      unit.placement = String(operation.placement);
      unit.collapsed = operation.placement === "trigger";
    } else if (operation.kind === "resize-share") {
      const authoredUnit = authored.units[operation.userId] || SAFE_DEFAULTS[operation.userId];
      unit.preferredShare = clamp(numberOr(operation.share, unit.preferredShare), 0.08, authoredUnit.maxShare || 1);
    } else if (operation.kind === "collapse") {
      unit.collapsed = operation.collapsed === true;
    } else if (operation.kind === "tab-with") {
      unit.tabWith = String(operation.targetUserId || "");
      unit.placement = "tab";
      unit.collapsed = false;
    }
    return {ok: true, preferences: normalizePreferences(next, authored)};
  }

  function selectedProjectFromDom(root) {
    let fromApi = null;
    if (typeof globalObject.currentGitProject === "function") {
      try {
        fromApi = globalObject.currentGitProject();
      } catch (_error) {
        fromApi = null;
      }
    }
    if (fromApi && (fromApi.path || fromApi.name || fromApi.id)) {
      return {
        id: String(fromApi.id || ""),
        name: String(fromApi.name || fromApi.label || fromApi.path || "Selected project"),
        path: String(fromApi.path || ""),
      };
    }
    const selected = root?.querySelector?.(".git-project-row.selected") || null;
    if (!selected) return null;
    return {
      id: String(selected.dataset?.gitProjectId || ""),
      name: String(selected.querySelector?.(".git-project-row-title strong")?.textContent || "Selected project").trim(),
      path: String(selected.querySelector?.("code")?.textContent || "").trim(),
    };
  }

  function operationStateText(root) {
    return String(root?.querySelector?.("#git-server-operation-state")?.textContent || "").trim();
  }

  function derivePhase(root, state) {
    const selected = selectedProjectFromDom(root);
    if (!selected) return "project-selection";
    const operationState = operationStateText(root).toLowerCase();
    if (/(running|queued|executing|starting|pushing|fetching|applying)/.test(operationState)) return "execution";
    if (state.supportEngaged && state.supportView === "advanced") return "recovery";
    if (state.supportEngaged && state.supportView === "evidence") return "proof-review";
    if (state.supportEngaged && state.supportView === "server") return "planning";
    return "selected-project-default";
  }

  function textContent(root, selector, fallback = "") {
    return String(root?.querySelector?.(selector)?.textContent || fallback).trim();
  }

  function setText(root, selector, value) {
    const node = root?.querySelector?.(selector);
    if (!node) return;
    const next = String(value || "");
    if (node.textContent !== next) node.textContent = next;
  }

  function setPressed(button, pressed) {
    if (!button) return;
    button.setAttribute("aria-pressed", pressed ? "true" : "false");
    button.classList.toggle("is-active", Boolean(pressed));
  }

  function applyResolvedLayout(root, resolved) {
    if (!root || !resolved) return false;
    const shell = root.querySelector(".git-tools-shell");
    if (!shell) return false;
    root.dataset.gitLayoutLive = "true";
    root.dataset.gitLayoutCapacity = resolved.capacity;
    root.dataset.gitLayoutPhase = resolved.phase;
    root.dataset.gitLayoutIdentity = resolved.actual.identity;
    root.dataset.gitLayoutSupport = resolved.actual.support;
    root.dataset.gitLayoutFeedback = resolved.actual.feedback;
    root.dataset.gitLayoutSupportView = resolved.supportView;
    root.dataset.gitLayoutActiveSurface = resolved.activeSurface;
    root.dataset.gitLayoutSupportOpen = resolved.supportOpen ? "true" : "false";
    root.dataset.gitLayoutCenterTab = resolved.centerTab;
    root.dataset.gitLayoutStage = resolved.stage;
    root.dataset.gitLayoutRemediated = resolved.remediated ? "true" : "false";
    root.dataset.gitLayoutContract = resolved.contractVersion;
    shell.style.setProperty("--git-layout-identity-inline", `${resolved.dimensions.identityInline}px`);
    shell.style.setProperty("--git-layout-identity-block", `${resolved.dimensions.identityBlock}px`);
    shell.style.setProperty("--git-layout-support-inline", `${resolved.dimensions.supportInline}px`);
    shell.style.setProperty("--git-layout-support-block", `${resolved.dimensions.supportBlock}px`);
    shell.style.setProperty("--git-layout-command-block", `${resolved.dimensions.commandBlock}px`);
    shell.style.setProperty("--git-layout-feedback-block", `${resolved.dimensions.feedbackBlock}px`);
    return true;
  }

  function updateChrome(root, resolved) {
    const selected = selectedProjectFromDom(root);
    const projectName = selected?.name || "Choose a project";
    const projectPath = selected?.path || "No repository selected";
    const operation = operationStateText(root) || textContent(root, "#git-server-status", "idle");
    setText(root, "#git-tools-layout-project-name", projectName);
    setText(root, "#git-tools-layout-project-path", projectPath);
    setText(root, "#git-tools-layout-phase", resolved.phase.replaceAll("-", " "));
    setText(root, "#git-tools-layout-status", operation);
    setText(
      root,
      "#git-tools-layout-resolution",
      `${resolved.capacity} · identity ${resolved.actual.identity} · support ${resolved.actual.support}`,
    );

    root.querySelectorAll("[data-git-layout-support]").forEach((button) => {
      const pressed = button.dataset.gitLayoutSupport === resolved.supportView;
      setPressed(button, pressed);
      button.setAttribute("aria-selected", pressed ? "true" : "false");
    });
    root.querySelectorAll("[data-git-layout-center]").forEach((button) => {
      const target = button.dataset.gitLayoutCenter;
      const pressed = target === resolved.activeSurface;
      setPressed(button, pressed);
      button.setAttribute("aria-selected", pressed ? "true" : "false");
    });

    root.querySelectorAll("[data-git-layout-dock]").forEach((control) => {
      const userId = control.dataset.gitLayoutDock;
      const unit = resolved.preferences?.units?.[userId];
      if (unit && control.value !== unit.placement) control.value = unit.placement;
    });
    root.querySelectorAll("[data-git-layout-share]").forEach((control) => {
      const userId = control.dataset.gitLayoutShare;
      const unit = resolved.preferences?.units?.[userId];
      if (!unit) return;
      const next = String(unit.preferredShare);
      if (control.value !== next) control.value = next;
      control.setAttribute("aria-valuenow", next);
    });

    const identityToggle = root.querySelector("[data-git-layout-action='toggle-identity']");
    if (identityToggle) {
      const collapsed = resolved.actual.identity === "trigger";
      identityToggle.textContent = collapsed ? "Show projects" : "Hide projects";
      identityToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }
    const supportToggle = root.querySelector("[data-git-layout-action='toggle-support']");
    if (supportToggle) {
      const collapsed = resolved.actual.support === "trigger";
      supportToggle.textContent = collapsed ? "Show support" : "Hide support";
      supportToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }

    const serverPane = root.querySelector("#git-server-pane");
    if (serverPane && resolved.actual.support !== "trigger") serverPane.open = true;
    const advancedPanel = root.querySelector('[data-git-support-panel="advanced"]');
    if (advancedPanel && "open" in advancedPanel) {
      advancedPanel.open = resolved.supportView === "advanced";
    }
    if (typeof globalObject.CustomEvent === "function") {
      root.dispatchEvent?.(new globalObject.CustomEvent("git-tools:layout-resolved", {detail: clone(resolved)}));
    }
  }

  function loadStoredPreferences(storage, authored) {
    const defaults = normalizePreferences(DEFAULT_PREFERENCES, authored);
    if (!storage?.getItem) return defaults;
    try {
      const raw = storage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Number(parsed?.version) === PREFERENCES_VERSION) {
          return normalizePreferences(parsed, authored);
        }
      }

      /*
       * The first live Git Tools layout stored states produced while the
       * project and support surfaces were still structurally unstable. Do not
       * replay a stale hidden-project or undersized-support state into the
       * corrected workbench. The v2 schema starts once from authored defaults.
       */
      if (storage.getItem(LEGACY_STORAGE_KEY)) {
        storage.removeItem?.(LEGACY_STORAGE_KEY);
        saveStoredPreferences(storage, defaults);
      }
      return defaults;
    } catch (_error) {
      return defaults;
    }
  }

  function saveStoredPreferences(storage, preferences) {
    if (!storage?.setItem) return false;
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify({
        version: PREFERENCES_VERSION,
        units: preferences.units,
      }));
      return true;
    } catch (_error) {
      return false;
    }
  }

  function mount(options = {}) {
    const doc = options.document || globalObject.document;
    const root = options.root || doc?.querySelector?.("#git-tools-app") || null;
    if (!root) return null;
    if (root.__mcelGitToolsLayoutController) return root.__mcelGitToolsLayoutController;

    const authored = extractAuthoredContract(root);
    let storage = options.storage || null;
    if (!storage) {
      try {
        storage = globalObject.localStorage || null;
      } catch (_error) {
        storage = null;
      }
    }
    let preferences = loadStoredPreferences(storage, authored);
    let latest = null;
    let resizeTimer = 0;
    let mutationTimer = 0;
    const history = [];
    const state = {
      supportView: "server",
      supportEngaged: false,
      centerTab: "workflow",
      stage: "workflow",
      activeSurface: "workflow",
      identityOpen: false,
      supportOpen: false,
    };

    function viewport() {
      const rect = root.getBoundingClientRect?.() || {};
      return {
        width: Math.max(320, numberOr(rect.width, globalObject.innerWidth || 1440)),
        height: Math.max(480, numberOr(rect.height, globalObject.innerHeight || 900)),
      };
    }

    function resolve() {
      const phase = derivePhase(root, state);
      /*
       * Phase is evidence about the current operation, not permission to
       * overwrite the operator's selected support surface. Keeping this
       * explicit prevents a push from switching to Activity and stranding the
       * publish controls there after the command completes.
       */
      latest = resolveLayout({
        viewport: viewport(),
        authored,
        preferences,
        phase,
        supportView: state.supportView,
        centerTab: state.centerTab,
        stage: state.stage,
        activeSurface: state.activeSurface,
        identityOpen: state.identityOpen,
        supportOpen: state.supportOpen,
      });
      if (latest.actual.support === "tab" && state.stage === "support") latest.centerTab = "support";
      if (latest.actual.support === "stage" && state.stage === "support") latest.stage = "support";
      applyResolvedLayout(root, latest);
      updateChrome(root, latest);
      return clone(latest);
    }

    function persist() {
      saveStoredPreferences(storage, preferences);
      return clone(preferences);
    }

    function pushHistory() {
      history.push(clone(preferences));
      if (history.length > HISTORY_LIMIT) history.shift();
    }

    function focusTarget(target) {
      if (!target) return false;
      const focus = () => {
        try {
          target.focus({preventScroll: true});
        } catch (_error) {
          target.focus?.();
        }
        target.scrollIntoView?.({block: "nearest", inline: "nearest"});
      };
      if (typeof globalObject.requestAnimationFrame === "function") {
        globalObject.requestAnimationFrame(focus);
      } else {
        globalObject.setTimeout?.(focus, 0);
      }
      return true;
    }

    function focusSurface(surface) {
      const selectorBySurface = {
        identity: "#git-project-roster-surface",
        workflow: "#git-project-workflow-surface",
        support: "#git-workflow-accordion",
      };
      return focusTarget(
        root.querySelector(selectorBySurface[normalizeActiveSurface(surface)]),
      );
    }

    function focusSupportView(view) {
      const selectorByView = {
        server: "#git-server-publish-panel",
        evidence: "#git-server-operation-panel",
        advanced: "#git-server-recovery-panel",
      };
      return focusTarget(
        root.querySelector(selectorByView[normalizeSupportView(view)]),
      );
    }

    function selectSurface(surface, options = {}) {
      const next = normalizeActiveSurface(surface);
      state.activeSurface = next;
      state.identityOpen = next === "identity";
      if (next === "support") {
        state.supportOpen = true;
        state.supportEngaged = true;
        state.centerTab = "support";
        state.stage = "support";
      } else {
        state.centerTab = "workflow";
        state.stage = next;
        if (next === "workflow") state.supportEngaged = false;
      }
      const resolved = resolve();
      if (options.focus !== false) focusSurface(next);
      return resolved;
    }

    function applyOperation(operation) {
      const kind = String(operation?.kind || "");
      if (kind === "undo") {
        const prior = history.pop();
        if (!prior) return {ok: false, reason: "layout history is empty", resolved: clone(latest)};
        preferences = normalizePreferences(prior, authored);
        persist();
        return {ok: true, preferences: clone(preferences), resolved: resolve()};
      }
      if (kind === "select-support") {
        state.supportView = normalizeSupportView(operation.view);
        const resolved = selectSurface("support", {focus: false});
        focusSupportView(state.supportView);
        return {ok: true, preferences: clone(preferences), resolved};
      }
      if (kind === "select-surface") {
        const resolved = selectSurface(operation.surface);
        return {ok: true, preferences: clone(preferences), resolved};
      }
      if (kind === "select-stage") {
        const resolved = selectSurface(normalizeStage(operation.stage));
        return {ok: true, preferences: clone(preferences), resolved};
      }
      const result = applyOperationToPreferences(preferences, operation, authored);
      if (!result.ok) return {...result, resolved: clone(latest)};
      pushHistory();
      preferences = result.preferences;
      if (kind === "dock" && operation.userId === "repository.phase-support") {
        state.supportOpen = String(operation.placement || "") !== "trigger";
      }
      if (kind === "collapse" && operation.userId === "repository.project-identity") {
        state.identityOpen = operation.collapsed !== true;
        if (operation.collapsed === true && state.activeSurface === "identity") {
          state.activeSurface = "workflow";
          state.stage = "workflow";
        }
      }
      if (kind === "collapse" && operation.userId === "repository.phase-support") {
        state.supportOpen = operation.collapsed !== true;
        if (operation.collapsed === true && state.activeSurface === "support") {
          state.activeSurface = "workflow";
          state.centerTab = "workflow";
          state.stage = "workflow";
          state.supportEngaged = false;
        }
      }
      if (kind === "reset") {
        state.supportView = "server";
        state.supportEngaged = false;
        state.centerTab = "workflow";
        state.stage = "workflow";
        state.activeSurface = "workflow";
        state.identityOpen = false;
        state.supportOpen = false;
      }
      persist();
      return {ok: true, preferences: clone(preferences), resolved: resolve()};
    }

    function bindButtons() {
      root.addEventListener("click", (event) => {
        const control = event.target?.closest?.(
          "[data-git-layout-center], [data-git-layout-support], [data-git-layout-action]",
        );
        if (!control || !root.contains(control)) return;

        if (control.dataset.gitLayoutSupport) {
          event.preventDefault();
          applyOperation({
            kind: "select-support",
            view: control.dataset.gitLayoutSupport,
          });
          return;
        }

        if (control.dataset.gitLayoutCenter) {
          event.preventDefault();
          applyOperation({
            kind: "select-surface",
            surface: control.dataset.gitLayoutCenter,
          });
          return;
        }

        const action = control.dataset.gitLayoutAction;
        if (action === "toggle-identity") {
          event.preventDefault();
          if (latest?.actual?.identity === "trigger") {
            selectSurface("identity");
            return;
          }
          state.identityOpen = false;
          applyOperation({
            kind: "collapse",
            userId: "repository.project-identity",
            collapsed: true,
          });
          return;
        }
        if (action === "toggle-support") {
          event.preventDefault();
          if (latest?.actual?.support === "trigger") {
            state.supportOpen = true;
            selectSurface("support");
            return;
          }
          state.supportOpen = false;
          applyOperation({
            kind: "collapse",
            userId: "repository.phase-support",
            collapsed: true,
          });
          return;
        }
        if (action === "undo") {
          event.preventDefault();
          applyOperation({kind: "undo"});
          return;
        }
        if (action === "reset") {
          event.preventDefault();
          applyOperation({kind: "reset"});
        }
      });

      root.addEventListener("change", (event) => {
        const control = event.target;
        if (!control || !root.contains(control)) return;
        if (control.dataset?.gitLayoutDock) {
          applyOperation({
            kind: "dock",
            userId: control.dataset.gitLayoutDock,
            placement: control.value,
          });
          return;
        }
        if (control.dataset?.gitLayoutShare) {
          applyOperation({
            kind: "resize-share",
            userId: control.dataset.gitLayoutShare,
            share: Number(control.value),
          });
        }
      });
    }

    bindButtons();

    if (typeof globalObject.MutationObserver === "function") {
      const observer = new globalObject.MutationObserver(() => {
        globalObject.clearTimeout?.(mutationTimer);
        mutationTimer = globalObject.setTimeout?.(resolve, 40) || 0;
      });
      observer.observe(root, {childList: true, subtree: true, characterData: true});
    }

    globalObject.addEventListener?.("resize", () => {
      globalObject.clearTimeout?.(resizeTimer);
      resizeTimer = globalObject.setTimeout?.(resolve, 60) || 0;
    });

    const controller = {
      contract: authored,
      get preferences() { return clone(preferences); },
      get resolved() { return clone(latest); },
      resolve,
      persist,
      applyOperation,
      selectSupport(view) { return applyOperation({kind: "select-support", view}); },
      selectSurface(surface) { return applyOperation({kind: "select-surface", surface}); },
      selectStage(stage) { return applyOperation({kind: "select-stage", stage}); },
      reset() { return applyOperation({kind: "reset"}); },
      undo() { return applyOperation({kind: "undo"}); },
      exportPreferences() { return clone(preferences); },
    };

    root.__mcelGitToolsLayoutController = controller;
    globalObject.MainComputerGitToolsLayoutController = controller;
    resolve();
    return controller;
  }

  globalObject.MainComputerGitToolsLayout = Object.freeze({
    CONTRACT_VERSION,
    PREFERENCES_VERSION,
    STORAGE_KEY,
    LEGACY_STORAGE_KEY,
    APPLICATION_CONTRACT,
    SAFE_DEFAULTS,
    DEFAULT_PREFERENCES,
    CAPACITY_BANDS,
    extractAuthoredContract,
    rejectRawGeometry,
    normalizePreferences,
    normalizeActiveSurface,
    resolveLayout,
    validateOperation,
    applyOperationToPreferences,
    derivePhase,
    applyResolvedLayout,
    mount,
  });

  if (typeof document !== "undefined") {
    const start = () => mount({document});
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", start, {once: true});
    } else {
      start();
    }
  }
})();
