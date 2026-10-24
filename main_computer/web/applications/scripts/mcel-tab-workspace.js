/* MCEL tab-workspace projection v1. Generic UI composition from validated IR.
 * No application-specific selectors, live host APIs, capabilities or effects.
 * Selection delegates to the pure, typed McelViewTransition authority.
 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.McelTabWorkspace = api;
})(typeof globalThis !== "undefined" ? globalThis : null, function () {
  "use strict";
  const STATUSES = new Set(["not-requested", "loading", "available", "empty", "failed"]);
  function requireContract(ok, code) { if (!ok) throw new Error(`MCEL_TAB_WORKSPACE_${code}`); }
  function text(tag, value, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = String(value);
    return node;
  }
  function check(ir) {
    requireContract(ir && typeof ir === "object" && ir.schema && ir.application, "MISSING_IR");
    const surfaces = (ir.surfaces || []).filter(s => s.semanticSurface?.tabGroup);
    requireContract(surfaces.length === 1, "ONE_TAB_GROUP_REQUIRED");
    const semantic = surfaces[0].semanticSurface;
    const group = semantic.tabGroup;
    const tabs = group.tabs;
    requireContract(group.role === "single-selection-view-state" && Array.isArray(tabs) && tabs.length > 0,
      "INVALID_GROUP");
    requireContract(group.inactivePanelPolicy === "hidden-but-model-preserved" &&
      group.a11y?.tabList === "tablist" && group.a11y?.tab === "tab" &&
      group.a11y?.panel === "tabpanel" && group.a11y?.keyboard === "standard-tabs", "INVALID_A11Y");
    const unique = (items) => new Set(items).size === items.length;
    requireContract(tabs.every(t => typeof t.id === "string" && t.id && typeof t.label === "string" && t.label &&
      typeof t.tab === "string" && t.tab && typeof t.panel === "string" && t.panel) &&
      unique(tabs.map(t => t.id)) && unique(tabs.map(t => t.tab)) && unique(tabs.map(t => t.panel)) &&
      tabs.some(t => t.id === group.defaultTabId), "INVALID_TAB_GRAPH");
    const controls = semantic.controls || [];
    const regions = semantic.regions || [];
    requireContract(tabs.every(t => controls.filter(c => c.role === "select-tab" && c.selects === t.id &&
      c.selector === t.tab && c.controls === t.panel).length === 1 &&
      regions.filter(r => r.role === "notebook-data-panel" && r.selector === t.panel &&
      r.controlledBy === t.tab && r.readOnly === true).length === 1), "UNBOUND_TAB_GRAPH");
    const states = ir.states || [];
    const selection = states.find(s => s.sourceName === group.state && s.authority === "renderer-local");
    const ids = tabs.map(t => t.id);
    requireContract(selection && selection.initial === group.defaultTabId &&
      JSON.stringify(selection.schema?.enum) === JSON.stringify(ids), "INVALID_SELECTION_STATE");
    const intentions = (ir.intents || []).filter(i => i.operationKind === "interaction" &&
      i.lane === "local-view-state" && i.viewTransition?.target?.ref === selection.id);
    requireContract(intentions.length === 1 && intentions[0].risk === "local-state" &&
      intentions[0].viewTransition.invalidInput === "refuse" &&
      intentions[0].viewTransition.value?.input?.ref === intentions[0].input?.[0]?.id &&
      JSON.stringify(intentions[0].input[0].schema?.enum) === JSON.stringify(ids) &&
      !(intentions[0].writes || []).length && !(intentions[0].effectRefs || []).length &&
      !(ir.effects || []).length && !(ir.capabilities || []).length, "UNSAFE_VIEW_TRANSITION");
    const sources = new Map();
    for (const tab of tabs) {
      const region = regions.find(r => r.role === "notebook-data-panel" && r.selector === tab.panel);
      requireContract(typeof region.dataSource === "string" && region.dataSource.length > 0, "INVALID_DATA_SOURCE");
      sources.set(tab.id, region.dataSource);
    }
    return {group, tabs, intent: intentions[0], selection, sources, states};
  }
  function mount(rootElement, ir, options = {}) {
    requireContract(rootElement && typeof rootElement.replaceChildren === "function", "MISSING_ROOT");
    const contract = check(ir);
    const transition = options.transitions || globalThis.McelViewTransition;
    requireContract(transition && typeof transition.apply === "function", "MISSING_TYPED_EXECUTOR");
    const {group, tabs, selection, intent, sources, states} = contract;
    const doc = rootElement.ownerDocument;
    const prefix = String(options.instanceId || `mcel-tab-${Math.random().toString(36).slice(2, 9)}`);
    requireContract(/^[A-Za-z][A-Za-z0-9_-]{0,59}$/.test(prefix), "INVALID_INSTANCE_ID");
    let current = {[selection.id]: selection.initial};
    let snapshot = options.snapshot && typeof options.snapshot === "object" && !Array.isArray(options.snapshot)
      ? structuredClone(options.snapshot) : {};
    let status = options.status || "not-requested";
    requireContract(STATUSES.has(status), "INVALID_DATA_STATUS");
    let disposed = false;
    const app = doc.createElement("section");
    app.className = "mcel-tab-workspace";
    app.dataset.mcelGenerated = "tab-workspace-v1";
    const head = doc.createElement("header");
    head.className = "mcel-tab-workspace__header";
    const headings = doc.createElement("div");
    headings.append(text("p", "MCEL · Generated workspace", "mcel-tab-workspace__eyebrow"));
    headings.append(text("h1", ir.application.title || "Data workspace"));
    headings.append(text("p", "Read-only data views · Local selection", "mcel-tab-workspace__subtitle"));
    head.append(headings);
    head.append(text("span", "Preview · No machine controls", "mcel-tab-workspace__safety"));
    app.append(head);
    const tabList = doc.createElement("div");
    tabList.className = "mcel-tab-workspace__tabs";
    tabList.id = `${prefix}-tabs`;
    tabList.setAttribute("role", "tablist");
    tabList.setAttribute("aria-label", "Data views");
    const panelGroup = doc.createElement("div");
    panelGroup.className = "mcel-tab-workspace__panels";
    const ui = new Map();
    for (const t of tabs) {
      const button = doc.createElement("button");
      button.type = "button";
      button.className = "mcel-tab-workspace__tab";
      button.id = `${prefix}-tab-${t.id}`;
      button.dataset.mcelTab = t.id;
      button.setAttribute("role", "tab");
      button.setAttribute("aria-controls", `${prefix}-panel-${t.id}`);
      button.textContent = t.label;
      tabList.append(button);
      const panel = doc.createElement("section");
      panel.className = "mcel-tab-workspace__panel";
      panel.id = `${prefix}-panel-${t.id}`;
      panel.dataset.mcelPanel = t.id;
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", button.id);
      panel.setAttribute("tabindex", "0");
      panel.append(text("h2", t.label));
      const content = doc.createElement("div");
      content.className = "mcel-tab-workspace__content";
      panel.append(content);
      panelGroup.append(panel);
      ui.set(t.id, {button, panel, content});
      button.addEventListener("click", () => select(t.id));
      button.addEventListener("keydown", (ev) => {
        if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(ev.key)) return;
        ev.preventDefault();
        const i = tabs.findIndex(tab => tab.id === t.id);
        const index = ev.key === "Home" ? 0 : ev.key === "End" ? tabs.length - 1 :
          (i + (ev.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        const target = tabs[index].id;
        const result = select(target);
        if (result.ok) ui.get(target).button.focus();
      });
    }
    app.append(tabList, panelGroup);
    // Data presentation is generic: no app-specific table templates or host APIs.
    const renderData = () => {
      for (const tab of tabs) {
        const parent = ui.get(tab.id).content;
        parent.replaceChildren();
        const data = snapshot[sources.get(tab.id)];
        if (status === "loading" || status === "not-requested" || status === "failed") {
          const labels = {loading: "Loading data…", "not-requested": "Data not requested", failed: "Data unavailable"};
          parent.append(text("p", labels[status], "mcel-tab-workspace__notice"));
          continue;
        }
        if (data === undefined || data === null ||
            (Array.isArray(data) && !data.length) ||
            (typeof data === "object" && !Array.isArray(data) && !Object.keys(data).length)) {
          parent.append(text("p", "No data for this view", "mcel-tab-workspace__notice"));
          continue;
        }
        const rows = Array.isArray(data) ? data : Object.entries(data).map(([metric, value]) => ({metric, value}));
        if (rows.some(row => row && typeof row === "object" && !Array.isArray(row))) {
          const keys = [...new Set(rows.flatMap(row => row && typeof row === "object" && !Array.isArray(row)
            ? Object.keys(row) : ["value"]))].slice(0, 24);
          const frame = doc.createElement("div");
          frame.className = "mcel-tab-workspace__table-scroll";
          frame.tabIndex = 0;
          frame.setAttribute("aria-label", `${tab.label} data table, scrollable`);
          const table = doc.createElement("table");
          const thead = doc.createElement("thead");
          const header = doc.createElement("tr");
          for (const key of keys) header.append(text("th", key));
          thead.append(header);
          const tbody = doc.createElement("tbody");
          for (const row of rows.slice(0, 500)) {
            const tr = doc.createElement("tr");
            for (const key of keys) {
              const value = row && typeof row === "object" && !Array.isArray(row) ? row[key] : row;
              tr.append(text("td", value === undefined || value === null ? "—" :
                typeof value === "object" ? JSON.stringify(value) : value));
            }
            tbody.append(tr);
          }
          table.append(thead, tbody);
          frame.append(table);
          parent.append(frame);
        } else {
          const list = doc.createElement("ul");
          for (const value of rows.slice(0, 500)) list.append(text("li", value));
          parent.append(list);
        }
      }
    };
    const renderSelection = () => {
      for (const [id, {button, panel}] of ui) {
        const selected = current[selection.id] === id;
        button.setAttribute("aria-selected", String(selected));
        button.tabIndex = selected ? 0 : -1;
        panel.hidden = !selected;
      }
    };
    function select(id) {
      if (disposed) return {ok: false, code: "disposed", effects: []};
      const response = transition.apply(intent, current, {[intent.input[0].sourceName]: id}, states);
      if (response.ok !== true || response.effects?.length) return response.ok ?
        {ok: false, code: "unexpected-effect", effects: []} : response;
      current = response.nextState;
      renderSelection();
      if (group.routeSync === "view-state-only" && options.syncRoute === true) {
        // Query/hash-only routing; never navigate to the host application's URL.
        try {
          const url = new URL(doc.defaultView.location.href);
          url.hash = `tab=${encodeURIComponent(id)}`;
          doc.defaultView.history.replaceState(null, "", url.href);
        } catch (_ignored) { /* local file / opaque origin: route update unavailable */ }
      }
      return {ok: true, selected: id, effects: [], state: {[selection.id]: id}};
    }
    function routeSelection() {
      if (group.routeSync !== "view-state-only" || options.syncRoute !== true) return null;
      try {
        const encoded = doc.defaultView.location.hash.match(/^#tab=(.+)$/)?.[1];
        const id = encoded ? decodeURIComponent(encoded) : null;
        return tabs.some(tab => tab.id === id) ? id : null;
      } catch (_ignored) { return null; }
    }
    function onRouteChange() {
      const id = routeSelection();
      if (id && !disposed) {
        const result = transition.apply(intent, current, {[intent.input[0].sourceName]: id}, states);
        if (result.ok && !(result.effects || []).length) {
          current = result.nextState;
          renderSelection();
        }
      }
    }
    renderSelection();
    renderData();
    rootElement.replaceChildren(app);
    if (options.syncRoute === true && group.routeSync === "view-state-only") {
      doc.defaultView.addEventListener("hashchange", onRouteChange);
      doc.defaultView.addEventListener("popstate", onRouteChange);
      onRouteChange();
    }
    return Object.freeze({
      select,
      updateSnapshot(nextSnapshot, nextStatus = "available") {
        if (disposed) return {ok: false, code: "disposed"};
        if (!nextSnapshot || typeof nextSnapshot !== "object" || Array.isArray(nextSnapshot) ||
            !STATUSES.has(nextStatus)) return {ok: false, code: "invalid-snapshot"};
        snapshot = structuredClone(nextSnapshot);
        status = nextStatus;
        renderData();
        return {ok: true, effects: []};
      },
      getState() { return {selected: current[selection.id], status, effects: []}; },
      destroy() {
        disposed = true;
        doc.defaultView.removeEventListener("hashchange", onRouteChange);
        doc.defaultView.removeEventListener("popstate", onRouteChange);
        rootElement.replaceChildren();
      },
    });
  }
  return Object.freeze({version: "mcel-tab-workspace-v1", check, mount});
});
