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
      gridTemplateColumns: tracks.map(min => `minmax(${Math.min(min, Number(width))}px, 1fr)`).join(" "),
      gap, unmetMinWidth: deficit,
      selectors: Object.fromEntries(active.map(item => [item.id, item.selector]))});
  }

  // Executable structural-CSS contract v1. Opted-in nested grids use semantic
  // child controls and browser measurements. Application CSS is a candidate,
  // never a source of proof that labels fit. No application-name branches.
  const ADAPTIVE_SCHEMA = "mcel.adaptive-control-grid.v1";
  const adaptiveObservers = new WeakMap();
  const adaptiveOriginalStyles = new WeakMap();
  const adaptiveGroupOriginalStyles = new WeakMap();
  function gridTrackCount(columns) {
    return (String(columns).match(/(?:\d+(?:\.\d+)?)px/g) || []).length;
  }
  function atomicLabelLines(element) {
    const document = element?.ownerDocument;
    if (!document?.createTreeWalker || !document?.createRange) return {measured: false, split: []};
    const walker = document.createTreeWalker(element, document.defaultView?.NodeFilter?.SHOW_TEXT || 4);
    const range = document.createRange();
    const split = [];
    try {
      let node;
      while ((node = walker.nextNode())) {
        const source = String(node.data || "");
        for (const match of source.matchAll(/[A-Za-z]{2,}/g)) {
          const lines = [], fragments = [];
          for (let index = 0; index < match[0].length; index++) {
            range.setStart(node, match.index + index);
            range.setEnd(node, match.index + index + 1);
            const rect = range.getBoundingClientRect();
            if (!(rect.width > 0 && rect.height > 0)) continue;
            let line = lines.findIndex(y => Math.abs(y - rect.top) <= 2);
            if (line === -1) {line = lines.length; lines.push(rect.top); fragments.push("");}
            fragments[line] += match[0][index];
          }
          if (fragments.length > 1) split.push({word: match[0], fragments});
        }
      }
    } finally {range.detach?.();}
    return {measured: true, split};
  }
  function eligibleControl(element) {
    return !!element?.matches?.('button, [role="button"]');
  }
  function atomicFitRequired(element) {
    return !["allow-intraword-wrap", "multiline-anywhere"].includes(
      String(element.getAttribute("data-mcel-text-fit") || "").toLowerCase());
  }
  function adaptiveContract(container) {
    if (!container?.matches?.('[data-mcel-layout-role="adaptive-control-grid"]')) fail("adaptive grid must opt into the structural CSS contract");
    const maxColumns = Number(container.getAttribute("data-mcel-max-columns") || 4);
    const minTargetWidth = Number(container.getAttribute("data-mcel-min-target-width") || 40);
    if (!Number.isSafeInteger(maxColumns) || maxColumns < 1 || maxColumns > 16 ||
        !Number.isFinite(minTargetWidth) || minTargetWidth < 24 || minTargetWidth > 256) {
      fail("adaptive grid has invalid column or target-size obligations");
    }
    const children = [...container.children].filter(eligibleControl);
    if (!children.length || children.length !== container.children.length) fail("adaptive grid must contain direct actionable controls only");
    if (children.some(child => !String(child.textContent || "").trim())) fail("adaptive grid has an unlabeled control");
    return Object.freeze({schema: ADAPTIVE_SCHEMA, maxColumns, minTargetWidth,
      controls: children.map(el => String(el.getAttribute("data-mc-widget-id") || el.id || el.textContent.trim()))});
  }
  function gridControlEvidence(container, contract) {
    const controls = [...container.children];
    const widths = controls.map(el => {
      const r = el.getBoundingClientRect();
      const styles = el.ownerDocument.defaultView.getComputedStyle(el);
      return {label: String(el.textContent || "").trim(), width: r.width,
        height: r.height, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth,
        minTargetWidth: contract.minTargetWidth, atomicFitRequired: atomicFitRequired(el), css: {
          whiteSpace: styles.whiteSpace, overflowWrap: styles.overflowWrap, wordBreak: styles.wordBreak},
        text: atomicFitRequired(el) ? atomicLabelLines(el) : {measured: true, split: []}};
    });
    const failures = widths.flatMap(control => {
      const failed = [];
      if (!control.text.measured) failed.push("unavailable-text-measurement");
      if (control.text.split.length) failed.push("atomic-control-label-split");
      if (control.width + 0.5 < contract.minTargetWidth) failed.push("undersized-pointer-target");
      if (control.scrollWidth > control.clientWidth + 1) failed.push("control-content-overflow");
      return failed.map(code => ({control: control.label, code}));
    });
    const s = container.ownerDocument.defaultView.getComputedStyle(container);
    return {width: container.getBoundingClientRect().width, columns: s.gridTemplateColumns,
      observedColumns: gridTrackCount(s.gridTemplateColumns), controls: widths, failures};
  }
  function previewAdaptiveCandidate(container, contract, columns) {
    // This temporary rendering lives inside the original styling context. It
    // is inert, offscreen and removed in finally; the application DOM/CSS is
    // never changed by a rejected candidate.
    const preview = container.cloneNode(true);
    preview.removeAttribute("id");
    preview.setAttribute("aria-hidden", "true");
    preview.setAttribute("inert", "");
    preview.setAttribute("data-mcel-layout-preview", "true");
    preview.style.setProperty("position", "fixed", "important");
    preview.style.setProperty("left", "-100000px", "important");
    preview.style.setProperty("top", "0px", "important");
    preview.style.setProperty("width", `${container.getBoundingClientRect().width}px`, "important");
    preview.style.setProperty("box-sizing", "border-box", "important");
    preview.style.setProperty("pointer-events", "none", "important");
    preview.style.setProperty("grid-template-columns", `repeat(${columns}, minmax(0, 1fr))`, "important");
    // The candidate contract prohibits splitting words; the width check
    // independently rejects clipping introduced by that prohibition.
    for (const button of preview.children) {
      if (!atomicFitRequired(button)) continue;
      button.style.setProperty("white-space", "nowrap", "important");
      button.style.setProperty("overflow-wrap", "normal", "important");
      button.style.setProperty("word-break", "normal", "important");
    }
    container.parentElement.appendChild(preview);
    try {
      const evidence = gridControlEvidence(preview, contract);
      const accepted = evidence.observedColumns === columns && evidence.failures.length === 0;
      return {columns, accepted, evidence,
        repairConceptIds: ["preserve-atomic-control-label", "reconsider-parent-track-allocation"],
        status: accepted ? "verified" : "rejected", applicationAuthorized: false};
    } finally {preview.remove();}
  }
  function evaluateAdaptiveControlGrid(container) {
    const contract = adaptiveContract(container);
    const original = gridControlEvidence(container, contract);
    const candidates = [];
    // Max-column search preserves density, while each candidate is validated
    // in the actual browser (not accepted from an arithmetic width estimate).
    for (let columns = Math.min(contract.maxColumns, contract.controls.length); columns >= 1; columns--) {
      candidates.push(previewAdaptiveCandidate(container, contract, columns));
    }
    const accepted = candidates.find(candidate => candidate.accepted) || null;
    return {schema: ADAPTIVE_SCHEMA, contract, original, candidates,
      acceptedColumns: accepted?.columns || null,
      status: accepted ? "candidate-verified" : "unsatisfied",
      applicationAuthorized: false, sourceMutationAuthorized: false};
  }
  function applyAdaptiveControlGrid(container, evaluation) {
    if (!container || evaluation?.schema !== ADAPTIVE_SCHEMA) fail("a verified CSS contract evaluation is required");
    if (!evaluation.acceptedColumns || evaluation.status !== "candidate-verified") {
      return {status: "refused", reason: "no-verified-candidate", evaluation};
    }
    const verifiedAtApply = previewAdaptiveCandidate(container, adaptiveContract(container), evaluation.acceptedColumns);
    if (!verifiedAtApply.accepted) {
      return {status: "refused", reason: "candidate-stale-or-invalid", observed: verifiedAtApply.evidence, evaluation};
    }
    if (!adaptiveOriginalStyles.has(container)) {
      adaptiveOriginalStyles.set(container, {container: container.style.cssText,
        children: [...container.children].map(child => child.style.cssText)});
    }
    const original = adaptiveOriginalStyles.get(container);
    // Retain the last accepted state if a new candidate fails validation.
    const before = {container: container.style.cssText, children: [...container.children].map(el => el.style.cssText)};
    container.style.setProperty("grid-template-columns", `repeat(${evaluation.acceptedColumns}, minmax(0, 1fr))`, "important");
    for (const button of container.children) {
      if (!atomicFitRequired(button)) continue;
      button.style.setProperty("white-space", "nowrap", "important");
      button.style.setProperty("overflow-wrap", "normal", "important");
      button.style.setProperty("word-break", "normal", "important");
    }
    const observed = gridControlEvidence(container, evaluation.contract);
    if (observed.observedColumns !== evaluation.acceptedColumns || observed.failures.length) {
      container.style.cssText = before.container;
      [...container.children].forEach((button, i) => {button.style.cssText = before.children[i];});
      container.dataset.mcelLayoutProof = "refused";
      return {status: "refused", reason: "rendered-layout-failed", observed, evaluation};
    }
    // Diagnostic independence: the layout engine does not get to declare its
    // own visual-fit evidence sufficient when the separate detector is loaded.
    const independentDetector = global.McelSelfDiagnosis?._private?.detectAtomicControlLabelSplits;
    let independentDiagnostics = "unavailable";
    if (typeof independentDetector === "function") {
      let independentViolations;
      try { independentViolations = independentDetector(container, {}); }
      catch (error) { independentViolations = [{type: "diagnostic-unavailable", detail: String(error)}]; }
      if (!Array.isArray(independentViolations) || independentViolations.length) {
        container.style.cssText = before.container;
        [...container.children].forEach((button, i) => {button.style.cssText = before.children[i];});
        container.dataset.mcelLayoutProof = "refused";
        return {status: "refused", reason: "independent-diagnostic-rejected",
          independentViolations, observed, evaluation};
      }
      independentDiagnostics = "pass";
    }
    container.dataset.mcelLayoutProof = independentDiagnostics === "pass" ? "pass" : "observed-only";
    container.dataset.mcelLayoutAuthority = "mcel-structural-css";
    container.dataset.mcelLayoutColumns = String(evaluation.acceptedColumns);
    return {status: "pass", observed, evaluation, independentDiagnostics,
      sourceMutationAuthorized: false, previousAuthoredCSS: original.container};
  }

  // A parent can delegate its two-track allocation along with its nested
  // adaptive grid. A locally valid grid is not sufficient evidence that the
  // layout of the whole group is efficient or that its sibling remains usable.
  // This contract is structural, opted in, and independent of application ids.
  const GROUP_SCHEMA = "mcel.adaptive-control-group.v1";
  function adaptiveGroupContract(group) {
    if (!group?.matches?.('[data-mcel-layout-role="adaptive-control-group"]'))
      fail("adaptive control group must opt into the structural CSS contract");
    if (group.children.length !== 2) fail("adaptive control group requires exactly two direct regions");
    const index = [...group.children].findIndex(child =>
      child.matches('[data-mcel-layout-role="adaptive-control-grid"]'));
    if (index < 0 || [...group.children].filter(child =>
      child.matches('[data-mcel-layout-role="adaptive-control-grid"]')).length !== 1)
      fail("adaptive control group requires exactly one direct adaptive grid");
    if (group.ownerDocument.defaultView.getComputedStyle(group).display !== "grid") fail("adaptive control group requires CSS grid");
    return {schema: GROUP_SCHEMA, index, grid: group.children[index],
      sibling: group.children[1 - index], gridContract: adaptiveContract(group.children[index])};
  }
  function candidateGroupEvidence(group, contract, columns) {
    const grid = group.children[contract.index];
    const sibling = group.children[1 - contract.index];
    const gridEvidence = gridControlEvidence(grid, contract.gridContract);
    const violations = [...gridEvidence.failures];
    if (gridEvidence.observedColumns !== columns)
      violations.push({code: "computed-column-count-mismatch"});
    for (const el of sibling.querySelectorAll('button, [role="button"]')) {
      const r = el.getBoundingClientRect();
      const lines = atomicFitRequired(el) ? atomicLabelLines(el) : {measured:true, split:[]};
      if (!lines.measured || lines.split.length) violations.push({code:"sibling-label-integrity",label:el.textContent.trim()});
      if (r.width + 0.5 < contract.gridContract.minTargetWidth)
        violations.push({code:"sibling-undersized-control",label:el.textContent.trim()});
      if (el.scrollWidth > el.clientWidth + 1)
        violations.push({code:"sibling-content-overflow",label:el.textContent.trim()});
    }
    // Catch a constrained sibling whose own contents spill past its allocated
    // track. No claim is made about application-specific semantic readability.
    if (sibling.scrollWidth > sibling.clientWidth + 1)
      violations.push({code:"sibling-region-overflow"});
    if (group.scrollWidth > group.clientWidth + 1)
      violations.push({code:"group-overflow"});
    return {violations, gridEvidence, groupHeight:group.getBoundingClientRect().height,
      siblingWidth:sibling.getBoundingClientRect().width,
      gridWidth:grid.getBoundingClientRect().width,
      rows:Math.ceil(contract.gridContract.controls.length / columns),
      emptyCells:(columns - contract.gridContract.controls.length % columns) % columns};
  }
  function previewAdaptiveGroupCandidate(group, contract, share, columns) {
    const preview = group.cloneNode(true);
    preview.removeAttribute("id");
    preview.setAttribute("aria-hidden", "true");
    preview.setAttribute("inert", "");
    preview.style.setProperty("position", "fixed", "important");
    preview.style.setProperty("left", "-100000px", "important");
    preview.style.setProperty("top", "0px", "important");
    preview.style.setProperty("width", `${group.getBoundingClientRect().width}px`, "important");
    preview.style.setProperty("box-sizing", "border-box", "important");
    preview.style.setProperty("pointer-events", "none", "important");
    const stacked = share === "stack";
    const first = stacked ? null : contract.index === 0 ? share : 1 - share;
    const template = stacked ? "minmax(0, 1fr)" : `${first.toFixed(3)}fr ${(1-first).toFixed(3)}fr`;
    preview.style.setProperty("grid-template-columns", template, "important");
    const grid = preview.children[contract.index];
    grid.style.setProperty("grid-template-columns", `repeat(${columns}, minmax(0, 1fr))`, "important");
    for (const button of grid.children) {
      if (!atomicFitRequired(button)) continue;
      button.style.setProperty("white-space", "nowrap", "important");
      button.style.setProperty("overflow-wrap", "normal", "important");
      button.style.setProperty("word-break", "normal", "important");
    }
    group.parentElement.appendChild(preview);
    try {
      const evidence = candidateGroupEvidence(preview, contract, columns);
      const accepted = evidence.violations.length === 0;
      return {share, columns, template, evidence, accepted,
        // Lower scores are preferable. Layout height and empty rows are
        // explicit objectives, unlike the old first-fit-only algorithm.
        quality: accepted ? {
          rows:evidence.rows, emptyCells:evidence.emptyCells,
          height:evidence.groupHeight, area:evidence.groupHeight * group.getBoundingClientRect().width,
          allocationShift:stacked ? 1 : Math.abs(share - 0.5)
        } : null};
    } finally {preview.remove();}
  }
  function evaluateAdaptiveControlGroup(group) {
    const contract = adaptiveGroupContract(group);
    // Keep the search finite. The ratios are preferences, not requirements;
    // acceptance is always established from real browser measurements.
    const shares = [0.50,0.55,0.60,0.65,0.70,0.75];
    const candidates = [];
    for (const columns of Array.from({length:Math.min(contract.gridContract.maxColumns,
          contract.gridContract.controls.length)},(_,i)=>i+1)) {
      for (const share of [...shares, "stack"])
        candidates.push(previewAdaptiveGroupCandidate(group,contract,share,columns));
    }
    const valid = candidates.filter(candidate=>candidate.accepted);
    valid.sort((a,b)=>a.quality.rows-b.quality.rows ||
      a.quality.height-b.quality.height ||
      a.quality.emptyCells-b.quality.emptyCells ||
      a.quality.allocationShift-b.quality.allocationShift);
    return {schema:GROUP_SCHEMA,status:valid.length?"candidate-verified":"unsatisfied",
      contract:{index:contract.index,gridContract:contract.gridContract},
      candidates, selected:valid[0]||null,
      applicationAuthorized:false,sourceMutationAuthorized:false};
  }
  function applyAdaptiveControlGroup(group, evaluation) {
    if (!group || evaluation?.schema !== GROUP_SCHEMA) fail("adaptive group evaluation required");
    if (!evaluation.selected) return {status:"refused",reason:"no-verified-group-candidate"};
    const contract = adaptiveGroupContract(group);
    const fresh = previewAdaptiveGroupCandidate(group,contract,
      evaluation.selected.share,evaluation.selected.columns);
    if (!fresh.accepted) return {status:"refused",reason:"stale-group-candidate"};
    const grid = contract.grid;
    const before = {parent:group.style.cssText, grid:grid.style.cssText,
      children:[...grid.children].map(x=>x.style.cssText)};
    group.style.setProperty("grid-template-columns",fresh.template,"important");
    const nestedEval = evaluateAdaptiveControlGrid(grid);
    // Selection must honor the previously verified joint arrangement.
    const selectedGrid = nestedEval.candidates.find(c=>c.columns===fresh.columns && c.accepted);
    if (!selectedGrid) {
      group.style.cssText=before.parent;
      return {status:"refused",reason:"joint-candidate-invalid-on-live-group"};
    }
    const applied = applyAdaptiveControlGrid(grid,{...nestedEval,acceptedColumns:fresh.columns});
    const actual = candidateGroupEvidence(group,contract,fresh.columns);
    if (applied.status!=="pass" || actual.violations.length) {
      group.style.cssText=before.parent;
      grid.style.cssText=before.grid;
      [...grid.children].forEach((x,i)=>{x.style.cssText=before.children[i];});
      return {status:"refused",reason:"joint-layout-failed-live-verification",actual,applied};
    }
    if (!adaptiveGroupOriginalStyles.has(group)) adaptiveGroupOriginalStyles.set(group,before.parent);
    group.dataset.mcelLayoutAuthority="mcel-structural-css";
    group.dataset.mcelLayoutProof=grid.dataset.mcelLayoutProof;
    return {status:"pass",columns:fresh.columns,share:fresh.share,
      groupMode:fresh.share === "stack" ? "stacked" : "side-by-side",
      quality:fresh.quality,actual,independentDiagnostics:applied.independentDiagnostics,
      applicationAuthorized:false,sourceMutationAuthorized:false};
  }

  function clearAdaptiveControlGrids(root) {
    adaptiveObservers.get(root)?.disconnect();
    adaptiveObservers.delete(root);
    for (const group of root.querySelectorAll('[data-mcel-layout-role="adaptive-control-group"]')) {
      if (adaptiveGroupOriginalStyles.has(group)) {
        group.style.cssText = adaptiveGroupOriginalStyles.get(group);
        adaptiveGroupOriginalStyles.delete(group);
      }
      delete group.dataset.mcelLayoutAuthority;
      delete group.dataset.mcelLayoutProof;
    }
    for (const element of root.querySelectorAll('[data-mcel-layout-role="adaptive-control-grid"]')) {
      const original = adaptiveOriginalStyles.get(element);
      if (original) {
        element.style.cssText = original.container;
        [...element.children].forEach((button, i) => {if (original.children[i] !== undefined) button.style.cssText = original.children[i];});
        adaptiveOriginalStyles.delete(element);
      }
      delete element.dataset.mcelLayoutProof;
      delete element.dataset.mcelLayoutAuthority;
      delete element.dataset.mcelLayoutColumns;
    }
  }
  function reflowAdaptiveControlGrids(root) {
    const results = [];
    for (const group of root.querySelectorAll('[data-mcel-layout-role="adaptive-control-group"]')) {
      if (group.matches('[data-mcel-canonical-layout-claimed]') || group.closest('[data-mcel-canonical-layout-claimed]')) continue;
      const style = group.ownerDocument.defaultView.getComputedStyle(group);
      if (style.display === "none" || style.visibility === "hidden" || group.getBoundingClientRect().width < 1) continue;
      try {
        const evaluation = evaluateAdaptiveControlGroup(group);
        const applied = applyAdaptiveControlGroup(group,evaluation);
        results.push({controlGrid:String(group.getAttribute("data-mc-widget-id") || ""),
          status:applied.status, columns:applied.columns || null, parentShare:applied.share ?? null,
          groupMode:applied.groupMode || null,
          quality:applied.quality || null, candidates:evaluation.candidates,
          independentDiagnostics:applied.independentDiagnostics || "unavailable",
          reason:applied.reason || null});
      } catch (error) {
        results.push({controlGrid:String(group.getAttribute("data-mc-widget-id") || ""),
          status:"refused",columns:null,candidates:[],reason:String(error?.message || error)});
      }
    }
    for (const element of root.querySelectorAll('[data-mcel-layout-role="adaptive-control-grid"]')) {
      if (element.closest('[data-mcel-canonical-layout-claimed]')) continue;
      if (element.parentElement?.matches?.('[data-mcel-layout-role="adaptive-control-group"]')) continue;
      const style = element.ownerDocument.defaultView.getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden" || element.getBoundingClientRect().width < 1) continue;
      try {
        const evaluation = evaluateAdaptiveControlGrid(element);
        const result = applyAdaptiveControlGrid(element, evaluation);
        results.push({controlGrid: String(element.getAttribute("data-mc-widget-id") || ""),
          status: result.status, columns: evaluation.acceptedColumns, candidates: evaluation.candidates,
          independentDiagnostics: result.independentDiagnostics || "unavailable",
          reason: result.reason || null});
      } catch (error) {
        results.push({controlGrid: String(element.getAttribute("data-mc-widget-id") || ""),
          status: "refused", columns: null, candidates: [], reason: String(error?.message || error)});
      }
    }
    root.dataset.mcelNestedLayoutProof = !results.length ? "not-applicable" :
      (results.some(x => x.status !== "pass") ? "fail" :
        results.some(x => x.independentDiagnostics !== "pass") ? "observed-only" : "pass");
    if (typeof CustomEvent === "function" && typeof root.dispatchEvent === "function") {
      root.dispatchEvent(new CustomEvent("mcel:nested-layout-verified", {bubbles: true,
        detail: {schema: ADAPTIVE_SCHEMA, results}}));
    }
    return results;
  }
  function observeAdaptiveControlGrids(root) {
    if (typeof ResizeObserver !== "function" || adaptiveObservers.has(root)) return;
    const observer = new ResizeObserver(() => reflowAdaptiveControlGrids(root));
    for (const group of root.querySelectorAll('[data-mcel-layout-role="adaptive-control-group"]')) observer.observe(group);
    for (const grid of root.querySelectorAll('[data-mcel-layout-role="adaptive-control-grid"]')) {
      if (!grid.parentElement?.matches?.('[data-mcel-layout-role="adaptive-control-group"]')) observer.observe(grid);
    }
    adaptiveObservers.set(root, observer);
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
    const nestedLayout = reflowAdaptiveControlGrids(root);
    observeAdaptiveControlGrids(root);
    root.dataset.mcelLayoutAuthority = "inferred";
    root.dataset.mcelLayoutMode = plan.mode;
    root.dataset.mcelLayoutRows = String(plan.rows.length);
    if (typeof CustomEvent === "function" && typeof root.dispatchEvent === "function") {
      root.dispatchEvent(new CustomEvent("mcel:layout-inferred", {bubbles: true,
        detail: {schema: plan.schema, mode: plan.mode, rows: plan.rows,
          authority: "semantic-layout", unmetMinWidth: plan.unmetMinWidth, nestedLayout}}));
    }
    return plan;
  }

  function clearWorkspaceLayout(root, plan) {
    if (!root || !plan) return;
    clearAdaptiveControlGrids(root);
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


  // Canonical recursive layout-tree execution (mcel.layout-tree.v1). This
  // accepts the normalized DSL/IR declaration, never legacy HTML attributes.
  // A failed hard constraint cannot be converted into a quality score.
  const TREE_SCHEMA = "mcel.layout-tree.v1";
  const TREE_EVIDENCE = "mcel.layout-tree-evaluation.v1";
  const treeOriginalStyles = new WeakMap();
  const treeObservers = new WeakMap();
  function canonicalLayoutTree(source) {
    const tree = source?.schema === TREE_SCHEMA ? source :
      (source?.layoutTree || source?.layouts?.find(layout => layout.layoutTree)?.layoutTree);
    if (!tree || tree.schema !== TREE_SCHEMA || !tree.root) fail("canonical layout-tree.v1 is required");
    const seen = new Set(); let count = 0;
    function validate(node, depth) {
      if (!node || typeof node !== "object" || Array.isArray(node) || depth > 10 || ++count > 64) fail("invalid layout tree depth/size");
      if (Object.keys(node).some(key => !["id","selector","role","children","maxColumns","minTargetWidth","gap","atomicText","variants","verifyControls","requireVisible"].includes(key)))
        fail("unknown canonical layout property");
      if (typeof node.id !== "string" || !node.id || seen.has(node.id)) fail("duplicate or absent layout node id");
      seen.add(node.id);
      if (typeof node.selector !== "string" || !node.selector.trim()) fail("layout node selector required");
      if (!["group","control-grid","region"].includes(node.role)) fail("unknown layout node role");
      if (!Number.isInteger(node.maxColumns ?? 3) || (node.maxColumns ?? 3)<1 || (node.maxColumns ?? 3)>8) fail("invalid layout maxColumns");
      if (!Number.isFinite(node.minTargetWidth ?? 40) || (node.minTargetWidth ?? 40)<24 || (node.minTargetWidth ?? 40)>256) fail("invalid layout minTargetWidth");
      if (!Number.isFinite(node.gap ?? 8) || (node.gap ?? 8)<0 || (node.gap ?? 8)>64) fail("invalid layout gap");
      if (node.atomicText !== undefined && typeof node.atomicText !== "boolean") fail("invalid layout atomicText");
      if (node.requireVisible !== undefined && typeof node.requireVisible !== "boolean") fail("invalid layout requireVisible");
      if (node.verifyControls !== undefined && typeof node.verifyControls !== "boolean") fail("invalid layout verifyControls");
      if (node.variants !== undefined) {
        if (node.role !== "group" || !Array.isArray(node.variants) || node.variants.length < 1 || node.variants.length > 8) fail("invalid layout variants");
        const childIds=new Set((node.children||[]).map(c=>c.id));
        for (const variant of node.variants) {
          if (!variant || Object.keys(variant).sort().join(",") !== "areas,tracks" ||
              !Array.isArray(variant.tracks) || variant.tracks.length < 1 || variant.tracks.length > 8 ||
              variant.tracks.some(track => typeof track !== "string" || !/^(?:[0-9]+(?:\.[0-9]+)?px|minmax\(0,\s*[0-9]+(?:\.[0-9]+)?fr\))$/.test(track)) ||
              !Array.isArray(variant.areas) || variant.areas.length < 1 || variant.areas.length > 12 ||
              variant.areas.some(row => typeof row !== "string")) fail("invalid layout variant CSS");
          const rows=variant.areas.map(row=>row.trim().split(/\s+/));
          if (rows.some(row=>row.length!==variant.tracks.length || row.some(area=>area!=="."&&!childIds.has(area)))) fail("invalid layout area map");
          for (const area of new Set(rows.flat().filter(x=>x!=="."))) {
            const positions=[];
            rows.forEach((row,r)=>row.forEach((v,c)=>{if(v===area) positions.push([r,c]);}));
            const rr=positions.map(p=>p[0]), cc=positions.map(p=>p[1]);
            if (positions.length !== (Math.max(...rr)-Math.min(...rr)+1)*(Math.max(...cc)-Math.min(...cc)+1)) fail("layout area must be rectangular");
          }
        }
      }
      const children = node.children || [];
      if (!Array.isArray(children) || (node.role === "group" ? !children.length : !!children.length)) fail("invalid layout children");
      for (const child of children) validate(child, depth + 1);
    }
    validate(tree.root, 0);
    return tree;
  }
  function resolveLayoutNodes(host, tree) {
    const nodes = [];
    function walk(definition, parent) {
      let element;
      try { element = host.matches(definition.selector) ? host : host.querySelector(definition.selector); }
      catch (_) { fail(`invalid layout selector: ${definition.id}`); }
      if (!element || (parent && element.parentElement !== parent)) fail(`layout structure does not match declaration: ${definition.id}`);
      if (nodes.some(entry => entry.el === element)) fail(`layout selector alias: ${definition.id}`);
      if (definition.role === "control-grid" && (![...element.children].length ||
          ![...element.children].every(eligibleControl))) fail(`control-grid requires direct actionable controls: ${definition.id}`);
      nodes.push({def: definition, el: element});
      for (const child of definition.children || []) walk(child, element);
    }
    walk(tree.root, null);
    if (nodes[0].el !== host) fail("layout tree root must match the supplied host");
    return nodes;
  }
  function canonicalTreeOptions(entry) {
    const def=entry.def;
    if (def.role === "region") return [{columns:0,template:"",gap:0}];
    const count = def.role === "group" ? def.children.length : entry.el.children.length;
    const options = [];
    if (def.variants?.length) return def.variants.map(variant=>({
      columns:variant.tracks.length,template:variant.tracks.join(" "),gap:def.gap ?? 8,
      areas:variant.areas.slice(), names:[...new Set(variant.areas.join(" ").split(/\s+/).filter(x=>x!=="."))]
    }));
    for (let columns=1; columns<=Math.min(def.maxColumns || 3,count);columns++) {
      options.push({columns,template:`repeat(${columns}, minmax(0, 1fr))`,gap:def.gap ?? 8});
      // A generic two-region grouping may redistribute space, not just change
      // column count. These are bounded candidate ratios, never source CSS.
      if (def.role === "group" && count === 2 && columns === 2)
        for (const split of [0.35,0.4,0.45,0.55,0.6,0.65])
          options.push({columns,template:`minmax(0, ${split}fr) minmax(0, ${1-split}fr)`,gap:def.gap ?? 8});
    }
    return options;
  }
  function setCanonicalTreeStyles(entries, choices) {
    entries.forEach((entry,index) => {
      const choice = choices[index];
      if (!choice.columns) return;
      const el=entry.el;
      // Never turn a host-hidden application pane back on with generated !important display.
      if (!treeElementVisible(el)) return;
      el.style.setProperty("display","grid","important");
      el.style.setProperty("grid-template-columns",choice.template,"important");
      el.style.setProperty("gap",`${choice.gap}px`,"important");
      if (choice.areas) {
        el.style.setProperty("grid-template-areas",choice.areas.map(row=>`"${row}"`).join(" "),"important");
        for (const child of entry.def.children || []) {
          const target=[...el.children].find(node=>node.matches(child.selector));
          if (target && choice.names.includes(child.id)) target.style.setProperty("grid-area",child.id,"important");
        }
      }

      if (entry.def.role === "control-grid" && entry.def.atomicText !== false) {
        for (const button of el.children) {
          if (!atomicFitRequired(button)) continue;
          button.style.setProperty("white-space","nowrap","important");
          button.style.setProperty("overflow-wrap","normal","important");
          button.style.setProperty("word-break","normal","important");
        }
      }
    });
  }
  function treeElementVisible(el) {
    for (let node=el; node; node=node.parentElement) {
      const style=node.ownerDocument.defaultView.getComputedStyle(node);
      if (node.hidden || style.display === "none" || style.visibility === "hidden") return false;
    }
    return el.getBoundingClientRect().width > 0;
  }
  function canonicalTreeEvidence(entries, choices) {
    const failures = [], records=[];
    let emptyCells=0, gridRows=0;
    entries.forEach((entry,index) => {
      const {el,def}=entry, choice=choices[index];
      const style=el.ownerDocument.defaultView.getComputedStyle(el);
      const rect=el.getBoundingClientRect();
      if (!treeElementVisible(el)) {
        records.push({id:def.id,role:def.role,visible:false,columns:choice.columns});
        return;
      }
      if (choice.areas) {
        const listed=new Set(choice.names);
        for (const child of def.children) {
          const target=entries.find(entry=>entry.def===child)?.el;
          if (target && treeElementVisible(target) !== listed.has(child.id))
            failures.push({id:def.id,code:"layout-variant-visibility-mismatch",child:child.id});
        }
        for (const child of el.children) {
          if (treeElementVisible(child) && !def.children.some(c=>child.matches(c.selector)))
            failures.push({id:def.id,code:"undeclared-visible-layout-region"});
        }
      }
      const children=def.role === "control-grid" ? [...el.children] : (def.role === "group" ? [...el.children] : []);
      if (choice.columns && gridTrackCount(style.gridTemplateColumns)!==choice.columns)
        failures.push({id:def.id,code:"computed-css-track-conflict"});
      if (el.scrollWidth > el.clientWidth + 1 && !["auto","scroll"].includes(style.overflowX))
        failures.push({id:def.id,code:"region-content-overflow"});
      if (choice.columns) {
        gridRows += Math.ceil(children.length / choice.columns);
        emptyCells += (choice.columns - children.length % choice.columns) % choice.columns;
      }
      // Region siblings are part of the same hard-constraint decision. An
      // excellent keypad layout is still invalid if the adjacent toolbar
      // becomes unreadable or unusable.
      if (def.role === "region" && def.verifyControls !== false) for (const button of el.querySelectorAll('button, [role="button"]')) {
        if (!treeElementVisible(button)) continue;
        const r=button.getBoundingClientRect();
        if (r.width + .5 < (def.minTargetWidth ?? 40))
          failures.push({id:def.id,code:"sibling-undersized-target",label:button.textContent.trim(),width:r.width});
        if (button.scrollWidth > button.clientWidth + 1)
          failures.push({id:def.id,code:"sibling-content-overflow",label:button.textContent.trim()});
        if (def.atomicText !== false && atomicFitRequired(button)) {
          const text=atomicLabelLines(button);
          if (!text.measured || text.split.length)
            failures.push({id:def.id,code:"sibling-text-integrity",label:button.textContent.trim()});
        }
      }
      if (def.role === "control-grid") for (const button of children) {
        if (!treeElementVisible(button)) continue;
        const r=button.getBoundingClientRect();
        if (r.width + .5 < (def.minTargetWidth ?? 40)) failures.push({id:def.id,code:"undersized-target",label:button.textContent.trim(),width:r.width});
        if (button.scrollWidth > button.clientWidth + 1) failures.push({id:def.id,code:"control-content-overflow",label:button.textContent.trim()});
        if (def.atomicText !== false && atomicFitRequired(button)) {
          const text=atomicLabelLines(button);
          if (!text.measured) failures.push({id:def.id,code:"text-measurement-unavailable"});
          if (text.split.length) failures.push({id:def.id,code:"atomic-label-split",label:button.textContent.trim(),split:text.split});
        }
      }
      records.push({id:def.id,role:def.role,columns:choice.columns,width:rect.width,height:rect.height,
        computedColumns:style.gridTemplateColumns});
    });
    return {failures,records,quality:{height:entries[0].el.getBoundingClientRect().height,
      emptyCells,gridRows}};
  }
  function previewCanonicalTree(host, tree, choices) {
    if (!host.parentElement) fail("layout host must be attached");
    const preview=host.cloneNode(true);
    // Preserve ID selectors inside the isolated clone; host-local resolution
    // never queries through document.getElementById().
    preview.setAttribute("inert",""); preview.setAttribute("aria-hidden","true");
    preview.setAttribute("data-mcel-layout-preview","true");
    preview.style.setProperty("position","fixed","important");
    preview.style.setProperty("left","-100000px","important");
    preview.style.setProperty("top","0px","important");
    preview.style.setProperty("width",`${host.getBoundingClientRect().width}px`,"important");
    preview.style.setProperty("box-sizing","border-box","important");
    preview.style.setProperty("pointer-events","none","important");
    host.parentElement.appendChild(preview);
    try {
      const entries=resolveLayoutNodes(preview,tree);
      setCanonicalTreeStyles(entries,choices);
      const evidence=canonicalTreeEvidence(entries,choices);
      // An observed structural fit is not a conformant candidate when the
      // independent browser detector finds a split atomic label elsewhere in
      // the governed application surface. Reject before quality ranking.
      if (!evidence.failures.length) {
        const detector=global.McelSelfDiagnosis?._private?.detectAtomicControlLabelSplits;
        if (typeof detector === "function") {
          try {
            const findings=detector(preview,{});
            evidence.independent="pass";
            if (findings.length) {
              evidence.independent="fail";
              evidence.failures.push(...findings.map(f=>({id:f.controlId||"surface",code:"independent-atomic-text-split",label:f.label})));
            }
          } catch (_) { evidence.independent="unavailable"; }
        } else evidence.independent="unavailable";
      }
      return evidence;
    } finally {preview.remove();}
  }
  function evaluateCanonicalLayoutTree(host, source, options={}) {
    const tree=canonicalLayoutTree(source);
    const entries=resolveLayoutNodes(host,tree);
    const optionsByEntry=entries.map(canonicalTreeOptions);
    const maxCandidates=Number(options.maxCandidates || 192);
    if (!Number.isInteger(maxCandidates) || maxCandidates < 1 || maxCandidates > 512) fail("invalid candidate limit");
    const totalCombinations=optionsByEntry.reduce((total,list)=>
      Math.min(maxCandidates+1,total * list.length),1);
    const authoredCss=entries.filter(entry=>entry.def.role!=="region").map(entry=>({
      id:entry.def.id,selector:entry.def.selector,
      computedGridTracks:entry.el.ownerDocument.defaultView.getComputedStyle(entry.el).gridTemplateColumns,
      inlineGridDeclaration:entry.el.style.getPropertyValue("grid-template-columns"),
      inlineGridPriority:entry.el.style.getPropertyPriority("grid-template-columns")
    }));
    const candidates=[];
    function search(choices,index) {
      if (index===optionsByEntry.length) {
        const evidence=previewCanonicalTree(host,tree,choices);
        candidates.push({choices:choices.map(choice=>({...choice})),accepted:!evidence.failures.length,evidence});
        return;
      }
      for (const choice of optionsByEntry[index]) {
        if (candidates.length >= maxCandidates) return;
        search([...choices,choice],index+1);
      }
    }
    search([],0);
    const acceptable=candidates.filter(candidate=>candidate.accepted);
    acceptable.sort((a,b)=>a.evidence.quality.height-b.evidence.quality.height ||
      a.evidence.quality.emptyCells-b.evidence.quality.emptyCells ||
      a.evidence.quality.gridRows-b.evidence.quality.gridRows ||
      JSON.stringify(a.choices).localeCompare(JSON.stringify(b.choices)));
    const selected=acceptable[0] || null;
    return {schema:TREE_EVIDENCE,status:selected?"candidate-verified":"unsatisfied",
      tree, candidates,selected,searchTruncated:totalCombinations>maxCandidates,
      authoredCss, generatedCss:selected?entries.filter(entry=>entry.def.role!=="region").map(entry=>({
        id:entry.def.id,selector:entry.def.selector,
        gridTemplateColumns:selected.choices[entries.indexOf(entry)].template,
        display:"grid",gapPx:selected.choices[entries.indexOf(entry)].gap,
        ownedProperties:["display","grid-template-columns","gap"]
      })):[],
      applicationAuthorized:false,sourceMutationAuthorized:false};
  }
  function applyCanonicalLayoutTree(host,evaluation) {
    if (!host || evaluation?.schema !== TREE_EVIDENCE || !evaluation.selected) return {status:"refused",reason:"no-verified-layout-candidate"};
    let entries;
    try { entries=resolveLayoutNodes(host,canonicalLayoutTree(evaluation.tree)); }
    catch (error) { return {status:"refused",reason:String(error)}; }
    const before=entries.map(entry=>({el:entry.el,hadStyle:entry.el.hasAttribute("style"),css:entry.el.getAttribute("style") || "",
      children:(["control-grid","group"].includes(entry.def.role) ? [...entry.el.children] : []).map(child=>({hadStyle:child.hasAttribute("style"),css:child.getAttribute("style") || ""}))}));
    const fresh=previewCanonicalTree(host,evaluation.tree,evaluation.selected.choices);
    if (fresh.failures.length) return {status:"refused",reason:"stale-layout-candidate",evidence:fresh};
    setCanonicalTreeStyles(entries,evaluation.selected.choices);
    const observed=canonicalTreeEvidence(entries,evaluation.selected.choices);
    const detector=global.McelSelfDiagnosis?._private?.detectAtomicControlLabelSplits;
    let independent="unavailable";
    let independentFindings=[];
    if (typeof detector === "function") {
      try { independentFindings=detector(host,{}); independent=independentFindings.length===0 ? "pass" : "fail"; }
      catch (_) { independent="fail"; }
    }
    if (observed.failures.length || independent === "fail") {
      for (const record of before) {
        if (record.hadStyle) record.el.style.cssText=record.css;
        else { record.el.removeAttribute("style"); record.el.removeAttribute("style"); }
        [...record.el.children].slice(0, record.children.length).forEach((child,i)=>{if (record.children[i]!==undefined) {
          if (record.children[i].hadStyle) child.style.cssText=record.children[i].css;
          else { child.removeAttribute("style"); child.removeAttribute("style"); }
        }});
      }
      return {status:"refused",reason:"live-layout-verification-failed",observed,independent,
        independentFindings: independentFindings.map(f=>({controlId:f.controlId,label:f.label,type:f.type}))};
    }
    if (!treeOriginalStyles.has(host)) treeOriginalStyles.set(host,before);
    host.dataset.mcelCanonicalLayoutProof=independent==="pass"?"pass":"observed-only";
    host.dataset.mcelCanonicalLayoutAuthority="mcel-layout-tree.v1";
    return {status:"pass",observed,independent,quality:evaluation.selected.evidence.quality,
      applicationAuthorized:false,sourceMutationAuthorized:false};
  }
  function clearCanonicalLayoutTree(host) {
    const original=treeOriginalStyles.get(host);
    if (original) {
      for (const record of original) {
        if (record.hadStyle) record.el.style.cssText=record.css;
        else { record.el.removeAttribute("style"); record.el.removeAttribute("style"); }
        [...record.el.children].slice(0, record.children.length).forEach((child,i)=>{if (record.children[i]!==undefined) {
          if (record.children[i].hadStyle) child.style.cssText=record.children[i].css;
          else { child.removeAttribute("style"); child.removeAttribute("style"); }
        }});
      }
      // Chromium may serialize an empty style attribute after a computed-
      // style flush. Remove it after every structural restoration is done.
      for (const record of original) {
        if (!record.hadStyle) { record.el.setAttribute("style", ""); record.el.removeAttribute("style"); }
        [...record.el.children].slice(0, record.children.length).forEach((child,i) => {
          if (!record.children[i].hadStyle) { child.setAttribute("style", ""); child.removeAttribute("style"); }
        });
      }
      treeOriginalStyles.delete(host);
    }
    delete host.dataset.mcelCanonicalLayoutProof;
    delete host.dataset.mcelCanonicalLayoutAuthority;
    // Final attribute removal occurs after dataset updates, which can flush
    // Chromium's cached inline-style serialization following measurements.
    if (original) for (const record of original) {
      if (!record.hadStyle) { record.el.setAttribute("style", ""); record.el.removeAttribute("style"); }
      [...record.el.children].slice(0, record.children.length).forEach((child,i) => {
        if (!record.children[i].hadStyle) { child.setAttribute("style", ""); child.removeAttribute("style"); }
      });
    }
  }

  function mountCanonicalLayoutTree(host, normalizedIr, options={}) {
    if (!host || treeObservers.has(host)) fail("canonical layout host is already mounted");
    const tree=canonicalLayoutTree(normalizedIr);
    const run=()=>{
      const evaluation=evaluateCanonicalLayoutTree(host,tree,options);
      return {...applyCanonicalLayoutTree(host,evaluation),evaluation};
    };
    const initial=run();
    if (initial.status !== "pass") return {status:"refused",reason:initial.reason || "unsatisfied",initial};
    let lastWidth=host.getBoundingClientRect().width;
    const observer=typeof ResizeObserver === "function" ? new ResizeObserver(() => {
      const width=host.getBoundingClientRect().width;
      if (Math.abs(width-lastWidth) < 1) return;
      lastWidth=width;
      try {run();} catch (_) { host.dataset.mcelCanonicalLayoutProof="fail"; }
    }) : null;
    observer?.observe(host);
    treeObservers.set(host,{observer});
    return {status:"pass",initial,observation:observer?"active":"unavailable"};
  }
  function unmountCanonicalLayoutTree(host) {
    treeObservers.get(host)?.observer?.disconnect();
    treeObservers.delete(host);
    clearCanonicalLayoutTree(host);
  }

  global.McelSemanticLayoutInference = Object.freeze({SCHEMA, ADAPTIVE_SCHEMA, TREE_SCHEMA,
    canonicalLayoutTree, evaluateCanonicalLayoutTree, applyCanonicalLayoutTree, clearCanonicalLayoutTree,
    mountCanonicalLayoutTree, unmountCanonicalLayoutTree,
    inferWorkspaceLayout, applyWorkspaceLayout, clearWorkspaceLayout,
    adaptiveContract, evaluateAdaptiveControlGrid, applyAdaptiveControlGrid,
    evaluateAdaptiveControlGroup, applyAdaptiveControlGroup,
    reflowAdaptiveControlGrids, clearAdaptiveControlGrids});
})(typeof window === "undefined" ? globalThis : window);
