/* MCEL Form Presentation Law v1.
 * Separate from mcel-form-law.js (validation/submission). Form owns *painted
 * shape* requirements. Layout owns geometry changes. Canonical IR binds targets;
 * no text, position or CSS class may create a semantic identity.
 * This implementation is Lab-only until operational acceptance. */
var McelSemanticFormPresentationLaw = (() => {
  "use strict";
  const schema = "mcel.semantic-form-presentation.v1";
  const profiles = Object.freeze([
    {id:"form-current",label:"Current (preserve)",description:"Retain the existing form of each theme and chrome."},
    {id:"form-angular",label:"Angular",description:"Tight rectangular geometry."},
    {id:"form-soft",label:"Soft",description:"Rounded controls and surfaces."},
    {id:"form-pill",label:"Pill",description:"Capsule controls, restrained container radii."},
    {id:"form-wireframe",label:"Wireframe",description:"Unrounded edge treatments; theme colors unchanged."}
  ]);
  const known = new Set(profiles.map(item => item.id));
  const normalize = id => known.has(id) ? id : "form-current";
  const styleId = "mcel-lab-semantic-form-presentation";
  const targetAttr = "data-mcel-form-presentation-kind";
  const cornerAttr = "data-mcel-form-presentation-corner";
  const refusalAttr = "data-mcel-form-shape-refused";
  const scopeAttr = "data-mcel-form-presentation-root";
  const runtimeViews = new WeakMap();
  const legacyCurrent = Object.freeze({
    "theme-machine": ["22px","999px","999px","999px"],
    "theme-local": ["22px","14px","30px","999px"],
    "theme-saas": ["28px","16px","40% 60% 48% 52%","999px"],
    "theme-editorial": ["10px","6px","6px","2px"],
    "theme-luxury": ["4px","2px","2px","2px"],
    "theme-civic": ["14px","8px","999px 999px 20px 20px","999px"],
    "theme-accessible": ["0","0","999px","999px"],
    "theme-debug": ["0","0","999px","999px"]
  });
  const treatments = Object.freeze({
    "form-angular": ["2px","2px","2px","2px","2px","2px"],
    "form-soft": ["14px","9px","12px","12px","999px","22px"],
    "form-pill": ["18px","12px","999px","20px","999px","999px"],
    "form-wireframe": ["0","0","0","0","0","0"]
  });
  // Form specifies shape; Layout alone may increase safe interior padding.
  // Neither law changes display, scroll ownership, control state or color.
  function legacyCss() {
    const selectors = [];
    for (const [theme,values] of Object.entries(legacyCurrent)) {
      const [panel,field,ornament,button] = values;
      selectors.push(`body[data-mcel-form="form-current"].${theme}{`+
        `--mcel-form-panel-radius:${panel};--mcel-form-field-radius:${field};`+
        `--mcel-form-control-radius:${button};--mcel-form-mark-radius:999px;`+
        `--mcel-form-envelope-radius:999px;--mcel-form-ornament-radius:${ornament};`+
        `--mcel-form-elevation:var(--site-shadow);--mcel-form-ornament-shadow:var(--site-hero-ornament-shadow);}`);
    }
    for (const [profile,values] of Object.entries(treatments)) {
      const [panel,field,button,ornament,mark,envelope] = values;
      selectors.push(`body[data-mcel-form="${profile}"]{`+
        `--mcel-form-panel-radius:${panel};--mcel-form-field-radius:${field};`+
        `--mcel-form-control-radius:${button};--mcel-form-mark-radius:${mark};`+
        `--mcel-form-envelope-radius:${envelope};--mcel-form-ornament-radius:${ornament};`+
        `--mcel-form-elevation:${profile === "form-wireframe" ? "none" : "var(--site-shadow)"};`+
        `--mcel-form-ornament-shadow:${profile === "form-wireframe" ? "none" : "var(--site-hero-ornament-shadow)"};}`);
    }
    selectors.push(`body[data-mcel-form]{--mcel-form-flat-radius:0;`+
      `--mcel-form-repair-radius:min(var(--mcel-form-panel-radius),28px);`+
      `--mcel-form-repair-small-radius:min(var(--mcel-form-panel-radius),22px);`+
      `--mcel-form-narrow-radius:min(var(--mcel-form-field-radius),18cqi);}`);
    return selectors.join("\n");
  }
  function radiusFor(profile, kind) {
    if (profile === "form-current") return null;
    const values = treatments[profile];
    if (!values) return null;
    return kind === "control" ? values[2] : kind === "field" ? values[1] :
      kind === "badge" ? values[4] : kind === "group" ? values[1] : values[0];
  }
  function classification(element) {
    const matches = selector => element?.matches?.(selector);
    if (matches('button,[role="button"],[role="tab"],[role="switch"]')) return "control";
    if (matches('input,select,textarea,[role="combobox"],[role="textbox"]')) return "field";
    if (matches('[role="status"],[role="note"],output')) return "badge";
    return "region";
  }
  function cornerAwareGroups(controls, painted, computed) {
    const candidates = [...new Set([...controls, ...painted])];
    const results = [];
    const radius = v => {const n = parseFloat(v);return Number.isFinite(n) && n > 0 ? n : 0;};
    for (const element of candidates) {
      if (element.matches('button,input,select,textarea,[role="button"],[role="tab"],[role="switch"]')) continue;
      const style = computed(element);
      if ([style.borderTopLeftRadius,style.borderTopRightRadius,style.borderBottomLeftRadius,
           style.borderBottomRightRadius].some(v => radius(v))) continue;
      if ([style.overflowX,style.overflowY].some(v => v !== "visible" && v !== "clip")) continue;
      if ([style.borderTopWidth,style.borderRightWidth,style.borderBottomWidth,style.borderLeftWidth]
          .some(v => parseFloat(v) > 0)) continue;
      const bounds = element.getBoundingClientRect();
      if (!bounds.width || !bounds.height) continue;
      const buttons = [...element.querySelectorAll('button,[role="button"],[role="tab"],[role="switch"]')]
        .filter(button => {
          const box = button.getBoundingClientRect();
          return box.width && box.height && computed(button).display !== "none" &&
            radius(computed(button).borderTopLeftRadius) > 0;
        });
      if (buttons.length < 2) continue;
      const boxes = buttons.map(button => button.getBoundingClientRect());
      const rounding = Math.min(24,...buttons.map(button => radius(computed(button).borderTopLeftRadius)));
      const tolerance = Math.max(2,rounding * 2);
      if (Math.abs(Math.min(...boxes.map(b => b.left)) - bounds.left) > tolerance ||
          Math.abs(Math.max(...boxes.map(b => b.right)) - bounds.right) > tolerance ||
          Math.abs(Math.max(...boxes.map(b => b.bottom)) - bounds.bottom) > tolerance) continue;
      results.push({element, radius:Number(rounding.toFixed(2))});
    }
    return results;
  }
  function apply({root,ir,bridge,bindingModel="operational-runtime",profile="form-current"}={}) {
    const id = normalize(profile);
    if (profile !== id || !root?.ownerDocument || !bridge)
      return {schema,status:"refused",reason:"Invalid Form profile or missing canonical inputs"};
    if (typeof McelSemanticLayoutInference === "undefined" ||
        typeof McelSemanticLayoutInference.applyFormShapeLayout !== "function")
      return {schema,status:"refused",reason:"Form requires the canonical semantic Layout solver"};
    const result = bindingModel === "operational-runtime"
      ? bridge.observeRuntimePresentationTargets?.({root,ir})
      : bridge.observePresentationTargets?.({root,ir});
    if (result?.status !== "source-bound" || !result.targets.some(t => t.kind === "control") ||
        !result.targets.some(t => t.kind === "region"))
      return {schema,status:"refused",reason:"Source-bound controls and regions required",diagnostics:result?.diagnostics};
    const doc = root.ownerDocument, computed = doc.defaultView.getComputedStyle.bind(doc.defaultView);
    // Recover authored geometry before computing the next shape profile.
    // Never carry the previous profile's padding into the next solve.
    if (typeof McelSemanticLayoutInference !== "undefined")
      McelSemanticLayoutInference.clearFormShapeLayout(root);
    let sheet = doc.getElementById(styleId);
    if (sheet) sheet.disabled = true;
    // Remove *only* Form's own runtime attributes; never disturb Theme's identity
    // or its color projection. Idempotent across profile changes.
    for (const el of [root,...root.querySelectorAll(`[${targetAttr}],[${cornerAttr}],[${refusalAttr}]`)]) {
      el.removeAttribute(targetAttr);el.removeAttribute(cornerAttr);el.removeAttribute(refusalAttr);
    }
    const targets = new Map();
    for (const binding of result.targets) {
      if (binding.kind === "region") targets.set(binding.element,"region");
      else {
        const type = classification(binding.element);
        targets.set(binding.element,type === "region" ? "group" : type);
        // An operational control may be a canonical wrapper containing button
        // and field widgets; their eligibility derives from this exact binding.
        for (const child of binding.element.querySelectorAll('button,input,select,textarea,[role="button"],[role="tab"],[role="switch"],[role="combobox"],[role="textbox"]'))
          targets.set(child,classification(child));
      }
    }
    const controls = result.targets.filter(t => t.kind === "control").map(t => t.element);
    // Visual descendants belong to source-bound regions, not to inferred nodes.
    // Paint observation is shape-only; no new semantic actions are inferred.
    const regions = result.targets.filter(t => t.kind === "region").map(t => t.element);
    const painted = [];
    for (const el of root.querySelectorAll('*')) {
      if (targets.has(el) || !regions.some(region=>region.contains(el)) ||
          el.matches('button,input,select,textarea,[role="button"],[role="tab"],[role="switch"]')) continue;
      const css = computed(el);
      if ((css.backgroundColor !== "transparent" && css.backgroundColor !== "rgba(0, 0, 0, 0)") ||
          (css.backgroundImage && css.backgroundImage !== "none")) painted.push(el);
    }
    for (const [el,kind] of targets) el.setAttribute(targetAttr,kind);
    root.setAttribute(scopeAttr,"true");
    root.setAttribute("data-mcel-form-profile",id);
    const scope = `[${scopeAttr}="true"]`;
    // Theme Law previously suppressed these shadows. Retain that baseline,
    // but make Form the sole owner of the edge/elevation treatment.
    let css = `@layer mcel-semantic-form{${scope} [data-mcel-theme-preview-surface],`+
      `${scope} [data-mcel-theme-preview-kind="control"],`+
      `${scope} :is(button,input,select,textarea,[role="button"],[role="tab"],[role="switch"]){`+
      `box-shadow:none!important;}}`;
    if (id !== "form-current") {
      const kinds = ["control","field","group","region","badge"];
      css += `@layer mcel-semantic-form{${kinds.map(kind =>
        `${scope} [${targetAttr}="${kind}"]${kind === "region" ? ":not([data-mcel-natural-layout])" : ""}{`+
        `border-radius:${radiusFor(id,kind)}!important;}`
      ).join('')} `+
      // Explicitly select nested controls only from source-bound semantic
      // groups; never blanket all buttons in the document.
      `${scope} [${targetAttr}="group"] :is(button,input,select,textarea,[role="button"],[role="tab"]){`+
      `border-radius:${radiusFor(id,"control")}!important;}`+
      `${scope} [${targetAttr}="group"] :is(input,select,textarea){border-radius:${radiusFor(id,"field")}!important;}`+
      // A canonical region may contain native controls not declared as separate
      // action nodes (for example, an embedded notebook). Native type is used
      // solely to apply physical form, never to infer an operation or authority.
      `${scope} [${targetAttr}="region"] :is(button,[role="button"],[role="tab"],[role="switch"]){`+
      `border-radius:${radiusFor(id,"control")}!important;}`+
      `${scope} [${targetAttr}="region"] :is(input,select,textarea,[role="combobox"],[role="textbox"]){`+
      `border-radius:${radiusFor(id,"field")}!important;}`+
      `}`;
    }
    if (!sheet) {sheet=doc.createElement("style");sheet.id=styleId;doc.head.appendChild(sheet);}
    sheet.textContent=css;sheet.disabled=false;
    // Group corner paint uses the chosen *computed* control radius. Shape
    // containment never changes scroll ownership or hides overflow.
    const cornerGroups = cornerAwareGroups(controls,painted,computed);
    cornerGroups.forEach((group,index)=>group.element.setAttribute(cornerAttr,String(index)));
    if (cornerGroups.length) sheet.textContent += `@layer mcel-semantic-form{${cornerGroups.map((g,i)=>
      `${scope} [${cornerAttr}="${i}"]{border-radius:${g.radius}px!important;}`).join('')}}`;
    // Geometry negotiation is a single deterministic Layout solve over
    // source-bound shape containers. No DOM nudging, rendering retries,
    // event remounts or application-specific selectors.
    const layout = McelSemanticLayoutInference.applyFormShapeLayout({root,containers:[
      ...[...targets].filter(([el,kind])=>kind==="region" || kind==="group").map(([el])=>el),
      ...painted, ...cornerGroups.map(group=>group.element)
    ]});
    // Do not retain a curved frame whose interior cannot be proven. The
    // bounded safe fallback is a square frame plus an explicit diagnostic;
    // never hide or clip the offending content to conceal the violation.
    for (const violation of layout.violations) {
      if (violation.node && (violation.node===root || root.contains(violation.node)))
        violation.node.setAttribute(refusalAttr,"true");
    }
    if (layout.violations.length) sheet.textContent +=
      `@layer mcel-semantic-form{${scope} [${targetAttr}][${refusalAttr}="true"]{border-radius:0!important;}}`;
    // The operational runtime declares view transitions explicitly. Re-solve
    // once for newly revealed semantic regions (e.g. Calculator Graphing),
    // not by polling the DOM or observing every attribute mutation.
    if (!runtimeViews.has(root)) {
      root.addEventListener("mcel:view-transition", () => {
        const request=runtimeViews.get(root);
        if (root.isConnected && request) apply(request);
      });
    }
    runtimeViews.set(root,{root,ir,bridge,bindingModel,profile:id});
    return {schema,status:"applied-in-isolated-lab-frame",profile:id,
      shapeLayout:layout,
      regions:result.targets.filter(t=>t.kind==="region").length,
      controls:result.targets.filter(t=>t.kind==="control").length,
      shapedElements:targets.size,paintedDescendants:painted.length,
      roundedGroupBackgrounds:cornerGroups.length,diagnostics:result.diagnostics};
  }
  return Object.freeze({schema,profiles,normalize,legacyCss,apply,cornerAwareGroups});
})();
if (typeof window !== "undefined") window.McelSemanticFormPresentationLaw = McelSemanticFormPresentationLaw;
if (typeof module !== "undefined" && module.exports) module.exports = McelSemanticFormPresentationLaw;
