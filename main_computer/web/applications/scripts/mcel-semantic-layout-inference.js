/* Shared semantic-to-grid inference for host-bound MCEL application workspaces.
 * Consumes generated semanticSurface + layoutGrammar, not app IDs or CSS plans.
 * Does not mutate semantic source or authorize application operations.
 */
(function (global) {
  "use strict";

  const SCHEMA = "mcel.inferred-workspace-layout.v1";
  function fail(message) { throw new Error(`MCEL semantic layout: ${message}`); }
  function value(value) { return String(value == null ? "" : value).trim(); }
  function finitePositive(value) { return Number.isFinite(Number(value)) && Number(value) > 0; }
  function orderedUnique(items) { return [...new Set(items)]; }

  function inferWorkspaceLayout({semanticSurface, layoutGrammar, mode, width, visibleRegions, gap = 12, forceSingleColumn = false}) {
    if (!semanticSurface || !layoutGrammar || !Array.isArray(semanticSurface.regions)
        || !Array.isArray(layoutGrammar.regions)) fail("semantic regions and layout relationships are required");
    if (!finitePositive(width) || !Number.isFinite(gap) || gap < 0) fail("available width and gap must be valid");
    const regions = new Map(semanticSurface.regions.map(region => [value(region.id), region]));
    const grammar = new Map(layoutGrammar.regions.map(region => [value(region.id), region]));
    const primary = [...regions.values()].filter(region => region.primary === true);
    if (primary.length !== 1 || !grammar.has(primary[0].id)) fail("exactly one declared primary work surface is required");
    // A primary surface may itself be a workspace (Calculator), or a leaf
    // inside a larger workspace (Code Editor). Find its closest ancestor with
    // multiple content children; never hard-code application IDs or zones.
    const parents = new Map();
    for (const parent of grammar.values()) {
      for (const child of parent.children || []) {
        if (parents.has(child)) fail(`region has multiple layout parents: ${child}`);
        parents.set(child, parent.id);
      }
    }
    let focusChild = primary[0].id;
    let workspace = grammar.get(focusChild);
    const visited = new Set();
    while (!Array.isArray(workspace.children) || workspace.children.length < 2) {
      if (visited.has(workspace.id)) fail("cyclic layout region hierarchy");
      visited.add(workspace.id);
      const parentId = parents.get(workspace.id);
      if (!parentId || !grammar.has(parentId)) fail("primary surface needs a workspace with multiple children");
      focusChild = workspace.id;
      workspace = grammar.get(parentId);
    }
    const childIds = workspace.children || [];
    if (!Array.isArray(childIds) || childIds.length === 0 || orderedUnique(childIds).length !== childIds.length) {
      fail("workspace requires unique declared child relationships");
    }
    const visible = visibleRegions == null ? null : new Set(visibleRegions);
    const constraints = new Map();
    for (const constraint of layoutGrammar.constraints || []) {
      if (!value(constraint.selector)) continue;
      if (finitePositive(constraint.minWidth)) {
        constraints.set(value(constraint.selector), Math.max(constraints.get(value(constraint.selector)) || 0, Number(constraint.minWidth)));
      }
    }
    const active = [];
    for (const id of childIds) {
      const region = regions.get(id);
      if (!region || !grammar.has(id) || !value(region.selector)) fail(`undeclared workspace child: ${id}`);
      if (visible && !visible.has(id)) continue;
      const modes = Array.isArray(region.focusModes) ? region.focusModes : [];
      const focus = modes.includes(mode) || (id === focusChild && primary[0].id !== workspace.id);
      const context = region.presentationRole === "context";
      const minWidth = constraints.get(value(region.selector)) || 240;
      active.push({id, selector: value(region.selector), minWidth, focus, context});
    }
    if (!active.length) fail("no active workspace children");
    // Semantic focus determines precedence. Order in the declaration is the
    // stable tie-breaker; no screen coordinates or application IDs are read.
    active.sort((a, b) => Number(b.focus) - Number(a.focus) || Number(a.context) - Number(b.context));
    const rows = [];
    let row = [], needed = 0;
    for (const item of active) {
      const extra = item.minWidth + (row.length ? gap : 0);
      if (row.length && (forceSingleColumn || needed + extra > width)) {
        rows.push(row); row = []; needed = 0;
      }
      row.push(item); needed += item.minWidth + (row.length > 1 ? gap : 0);
    }
    if (row.length) rows.push(row);
    const columns = Math.max(...rows.map(row => row.length));
    const tracks = Array.from({length: columns}, (_, index) => Math.max(...rows.filter(row => row[index]).map(row => row[index].minWidth)));
    // We use row-local fractional tracks via nested minmax() proportions only
    // when every row is the same shape; otherwise equal tracks preserve the
    // exact spanning relation of narrower rows.
    const templateRows = rows.map(row => {
      const cells = row.map(item => value(item.id).replace(/[^a-zA-Z0-9_-]/g, "_"));
      while (cells.length < columns) cells.push(cells[cells.length - 1]);
      return `"${cells.join(" ")}"`;
    });
    const minimumColumns = tracks.reduce((sum, track) => sum + track, 0) + gap * (columns - 1);
    // When a mixed-width row would not fit with shared grid tracks, reduce
    // column count instead of producing overflow or losing primary content.
    if (minimumColumns > width && columns > 1) {
      return inferWorkspaceLayout({semanticSurface, layoutGrammar, mode, width, visibleRegions,
        gap, forceSingleColumn: true});
    }
    const deficit = active.filter(item => item.minWidth > width).map(item => item.id);
    return Object.freeze({schema: SCHEMA, mode: value(mode), width: Number(width),
      workspaceSelector: value(workspace.selector),
      rows: rows.map(items => items.map(item => item.id)),
      order: active.map(item => item.id),
      areas: Object.fromEntries(active.map(item => [item.id, value(item.id).replace(/[^a-zA-Z0-9_-]/g, "_")])),
      gridTemplateAreas: templateRows.join(" "),
      gridTemplateColumns: tracks.map(min => `minmax(0, ${min}fr)`).join(" "),
      gap, unmetMinWidth: deficit,
      selectors: Object.fromEntries(active.map(item => [item.id, item.selector]))});
  }

  function applyWorkspaceLayout(root, plan, previous = null) {
    if (!root || !plan || plan.schema !== SCHEMA) fail("an inferred layout and host root are required");
    const workspace = root.querySelector(plan.workspaceSelector);
    if (!workspace) fail(`workspace selector did not resolve: ${plan.workspaceSelector}`);
    // Validate the entire projection before touching the DOM. A malformed
    // candidate must leave the last accepted layout unchanged.
    const elements = Object.entries(plan.selectors).map(([id, selector]) => {
      const element = root.querySelector(selector);
      if (!element || element.parentElement !== workspace) fail(`workspace child has no direct host element: ${id}`);
      return [id, element];
    });
    if (previous && previous.selectors) {
      for (const selector of Object.values(previous.selectors)) {
        const node = root.querySelector(selector);
        if (node) node.style.removeProperty("grid-area");
      }
    }
    workspace.style.display = "grid";
    workspace.style.gridTemplateAreas = plan.gridTemplateAreas;
    workspace.style.gridTemplateColumns = plan.gridTemplateColumns;
    workspace.style.gap = `${plan.gap}px`;
    elements.forEach(([id, element]) => { element.style.gridArea = plan.areas[id]; });
    root.dataset.mcelLayoutAuthority = "inferred";
    root.dataset.mcelLayoutMode = plan.mode;
    root.dataset.mcelLayoutRows = String(plan.rows.length);
    if (typeof CustomEvent === "function" && typeof root.dispatchEvent === "function") {
      root.dispatchEvent(new CustomEvent("mcel:layout-inferred", {bubbles: true,
        detail: {schema: plan.schema, mode: plan.mode, rows: plan.rows,
          authority: "semantic-layout", unmetMinWidth: plan.unmetMinWidth}}));
    }
    return plan;
  }

  function clearWorkspaceLayout(root, plan) {
    if (!root || !plan) return;
    const workspace = root.querySelector(plan.workspaceSelector);
    if (workspace) for (const key of ["display", "grid-template-areas", "grid-template-columns", "gap"]) workspace.style.removeProperty(key);
    for (const selector of Object.values(plan.selectors || {})) {
      const element = root.querySelector(selector);
      if (element) element.style.removeProperty("grid-area");
    }
    delete root.dataset.mcelLayoutAuthority;
    delete root.dataset.mcelLayoutMode;
    delete root.dataset.mcelLayoutRows;
  }

  global.McelSemanticLayoutInference = Object.freeze({SCHEMA, inferWorkspaceLayout, applyWorkspaceLayout, clearWorkspaceLayout});
})(typeof window === "undefined" ? globalThis : window);
