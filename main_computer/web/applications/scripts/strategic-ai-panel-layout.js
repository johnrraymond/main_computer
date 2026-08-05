(function (global) {
  "use strict";

  const STORAGE_PREFIX = "main-computer.strategic-ai.panel-mode.v1";
  const PANEL_MODES = Object.freeze(["expanded", "compact", "collapsed"]);

  function stringValue(value) {
    return String(value || "").trim();
  }

  function normalizeMode(value, fallback = "expanded") {
    const mode = stringValue(value).toLowerCase();
    return PANEL_MODES.includes(mode) ? mode : fallback;
  }

  function storageKey(panelId) {
    return `${STORAGE_PREFIX}:${stringValue(panelId) || "panel"}`;
  }

  function defaultStorage() {
    try {
      return global.localStorage || null;
    } catch {
      return null;
    }
  }

  function readPanelMode(panelId, storage = defaultStorage()) {
    if (!storage?.getItem) return "expanded";
    try {
      return normalizeMode(storage.getItem(storageKey(panelId)), "expanded");
    } catch {
      return "expanded";
    }
  }

  function writePanelMode(panelId, mode, storage = defaultStorage()) {
    const normalized = normalizeMode(mode);
    if (!storage?.setItem) return normalized;
    try {
      storage.setItem(storageKey(panelId), normalized);
    } catch {
      // Layout preference persistence is best-effort only.
    }
    return normalized;
  }

  function panelId(panel) {
    return stringValue(panel?.dataset?.strategicAiPanelId || panel?.id || "panel");
  }

  function panelMode(panel) {
    return normalizeMode(panel?.dataset?.strategicPanelMode, "expanded");
  }

  function visiblePanels(dock) {
    return [...(dock?.querySelectorAll?.("[data-strategic-ai-panel]") || [])]
      .filter((panel) => !panel.hidden);
  }

  function preferredDockMode(panels) {
    const modes = panels.map(panelMode);
    if (modes.includes("expanded")) return "expanded";
    if (modes.includes("compact")) return "compact";
    return "collapsed";
  }

  const state = {
    bound: false,
    dock: null,
    host: null,
    observer: null,
    resizeObserver: null,
    storage: null
  };

  function syncControlLabels(panel) {
    const mode = panelMode(panel);
    const compact = panel.querySelector?.("[data-strategic-ai-panel-compact]");
    const collapse = panel.querySelector?.("[data-strategic-ai-panel-collapse]");

    if (compact) {
      compact.textContent = mode === "compact" ? "Full" : "Compact";
      compact.setAttribute("aria-pressed", mode === "compact" ? "true" : "false");
      compact.hidden = mode === "collapsed";
    }
    if (collapse) {
      const expanded = mode !== "collapsed";
      collapse.textContent = expanded ? "Collapse" : "Expand";
      collapse.setAttribute("aria-expanded", expanded ? "true" : "false");
      const label = stringValue(panel.getAttribute?.("data-mc-component-label") || panel.id);
      collapse.setAttribute(
        "aria-label",
        `${expanded ? "Collapse" : "Expand"} ${label || "strategic panel"}`
      );
    }
  }

  function syncHostMetrics() {
    const host = state.host;
    if (!host) return null;
    const width = Math.max(0, Number(host.clientWidth) || 0);
    const height = Math.max(0, Number(host.clientHeight) || 0);
    if (!width || !height) return null;

    const axis = width <= 920 ? "bottom" : "side";
    const expandedHeight = Math.min(300, Math.max(120, height - 220));
    const compactHeight = Math.min(180, Math.max(90, height - 240));
    host.dataset.strategicDockAxis = axis;
    host.style.setProperty("--strategic-dock-expanded-height", `${expandedHeight}px`);
    host.style.setProperty("--strategic-dock-compact-height", `${compactHeight}px`);
    host.style.setProperty("--strategic-dock-collapsed-height", "64px");
    return {width, height, axis, expandedHeight, compactHeight, collapsedHeight: 64};
  }

  function syncDock() {
    const dock = state.dock;
    const host = state.host;
    if (!dock || !host) return null;

    syncHostMetrics();
    const panels = visiblePanels(dock);
    const active = panels.length > 0;
    const mode = active ? preferredDockMode(panels) : "collapsed";

    dock.hidden = !active;
    dock.dataset.strategicDockMode = mode;
    dock.dataset.strategicDockPanelCount = String(panels.length);
    host.dataset.strategicDockActive = active ? "true" : "false";
    host.dataset.strategicDockMode = mode;
    host.style.setProperty("--strategic-dock-panel-count", String(panels.length));

    return {active, mode, panelCount: panels.length};
  }

  function applyPanelMode(panel, mode, options = {}) {
    if (!panel) return "expanded";
    const normalized = normalizeMode(mode);
    const current = panelMode(panel);
    if (normalized === "collapsed" && current !== "collapsed") {
      panel.dataset.strategicPreviousPanelMode = current;
    }
    if (normalized !== "collapsed") {
      panel.dataset.strategicPreviousPanelMode = normalized;
    }
    panel.dataset.strategicPanelMode = normalized;
    syncControlLabels(panel);
    if (options.persist !== false) {
      writePanelMode(panelId(panel), normalized, state.storage);
    }
    syncDock();
    return normalized;
  }

  function toggleCompact(panel) {
    const mode = panelMode(panel);
    return applyPanelMode(panel, mode === "compact" ? "expanded" : "compact");
  }

  function toggleCollapsed(panel) {
    const mode = panelMode(panel);
    if (mode === "collapsed") {
      return applyPanelMode(
        panel,
        normalizeMode(panel.dataset.strategicPreviousPanelMode, "expanded")
      );
    }
    return applyPanelMode(panel, "collapsed");
  }

  function bindPanel(panel) {
    if (!panel || panel.dataset.strategicPanelLayoutBound === "true") return;
    panel.dataset.strategicPanelLayoutBound = "true";

    const restored = readPanelMode(panelId(panel), state.storage);
    applyPanelMode(panel, restored, {persist: false});

    panel.querySelector?.("[data-strategic-ai-panel-compact]")
      ?.addEventListener("click", () => toggleCompact(panel));
    panel.querySelector?.("[data-strategic-ai-panel-collapse]")
      ?.addEventListener("click", () => toggleCollapsed(panel));
  }

  function bind(options = {}) {
    if (state.bound || typeof document === "undefined") return state;
    const dock = document.querySelector("#strategic-ai-panel-dock");
    if (!dock) return state;

    state.bound = true;
    state.dock = dock;
    state.host = dock.closest(".canvas-wrap") || dock.parentElement;
    state.storage = options.storage === undefined ? defaultStorage() : options.storage;

    dock.querySelectorAll("[data-strategic-ai-panel]").forEach(bindPanel);

    if (typeof global.MutationObserver === "function") {
      state.observer = new global.MutationObserver(() => syncDock());
      dock.querySelectorAll("[data-strategic-ai-panel]").forEach((panel) => {
        state.observer.observe(panel, {
          attributes: true,
          attributeFilter: ["hidden", "data-strategic-panel-mode"]
        });
      });
    }

    if (typeof global.ResizeObserver === "function") {
      state.resizeObserver = new global.ResizeObserver(() => syncDock());
      state.resizeObserver.observe(state.host);
    }

    global.addEventListener?.("main-computer-strategic-ai-session-change", syncDock);
    global.addEventListener?.("resize", syncDock);
    syncDock();
    return state;
  }

  const api = {
    STORAGE_PREFIX,
    PANEL_MODES,
    normalizeMode,
    storageKey,
    readPanelMode,
    writePanelMode,
    panelMode,
    visiblePanels,
    preferredDockMode,
    syncHostMetrics,
    applyPanelMode,
    toggleCompact,
    toggleCollapsed,
    syncDock,
    bind,
    state
  };

  global.MainComputerStrategicAIPanelLayout = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") bind();
})(typeof globalThis !== "undefined" ? globalThis : window);
