/* Lab-only semantic presentation projection. The canonical IR supplies identity;
 * DOM styling is limited to this opt-in isolated frame. No chrome/operations. */
var McelSemanticThemePreview = (() => {
  "use strict";
  const schema = "mcel.semantic-theme-preview.v1";
  const colorTokens = Object.freeze([
    "--mcel-theme-card", "--mcel-theme-ink", "--mcel-theme-muted",
    "--mcel-theme-accent", "--mcel-theme-accent-2", "--mcel-theme-line"
  ]);
  const styleId = "mcel-lab-semantic-theme-color-only";
  const safeToken = value => typeof value === "string" && value.trim() &&
    !/[{};<>]/.test(value) ? value.trim() : "";
  const attr = {
    kind: "data-mcel-theme-preview-kind", operation: "data-mcel-theme-preview-operation",
    depth: "data-mcel-theme-preview-region-depth", surface: "data-mcel-theme-preview-surface"
  };

  function themeTokens(element, getStyle) {
    if (!element || typeof getStyle !== "function") return null;
    const computed = getStyle(element), tokens = {};
    for (const key of colorTokens) {
      const value = safeToken(computed?.getPropertyValue?.(key));
      if (!value) return null;
      tokens[key] = value;
    }
    return tokens;
  }

  // The semantic IR determines the region/control boundaries. Within those
  // boundaries, the already-painted descendants are merely visual components:
  // detecting their paint does NOT infer a new intent, capability, or region.
  function visualDescendants(root, targets, computed) {
    const regions = targets.filter(t => t.kind === "region").map(t => t.element);
    const canonical = new Set(targets.map(t => t.element));
    const results = [];
    for (const element of root.querySelectorAll("*")) {
      if (canonical.has(element) || !regions.some(region => region.contains(element))) continue;
      if (element.matches('button,input,select,textarea,option,[role="button"],[role="tab"],[role="switch"]')) continue;
      const style = computed(element);
      const background = style.backgroundColor;
      const painted = background && background !== "transparent" &&
        background !== "rgba(0, 0, 0, 0)";
      if (painted || (style.backgroundImage && style.backgroundImage !== "none"))
        results.push(element);
    }
    return results;
  }

  // Read actual computed colors instead of assuming that applying a CSS class
  // gives sufficient contrast. Handles rgb(), rgba() and color(srgb ...),
  // including alpha compositing through a semantic region's ancestors.
  function rgba(value) {
    if (!value) return null;
    const rgb = value.match(/^rgba?\(\s*([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)(?:\s*[,/]\s*([\d.]+))?\s*\)$/i);
    if (rgb) return [+rgb[1],+rgb[2],+rgb[3],rgb[4] === undefined ? 1 : +rgb[4]];
    const srgb = value.match(/^color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+))?\)$/i);
    if (srgb) return [+srgb[1]*255,+srgb[2]*255,+srgb[3]*255,srgb[4] === undefined ? 1 : +srgb[4]];
    if (value === "transparent") return [0,0,0,0];
    return null;
  }
  function backdrop(element, computed) {
    let color = [255,255,255];
    const stack = [];
    for (let current = element; current; current = current.parentElement) stack.push(current);
    for (const node of stack.reverse()) {
      const overlay = rgba(computed(node).backgroundColor);
      if (overlay) color = color.map((part,i) => overlay[i]*overlay[3]+part*(1-overlay[3]));
    }
    return color;
  }
  function luminance(color) {
    const channels = color.slice(0,3).map(value => {
      const c = value/255;
      return c <= 0.04045 ? c/12.92 : Math.pow((c+0.055)/1.055,2.4);
    });
    return 0.2126*channels[0]+0.7152*channels[1]+0.0722*channels[2];
  }
  function contrast(fg, bg) {
    const one = luminance(fg), two = luminance(bg);
    return (Math.max(one,two)+0.05)/(Math.min(one,two)+0.05);
  }
  function auditContrast(root, computed) {
    const result = {checked:0, minimumRatio:null, belowAA:0, examples:[]};
    for (const element of root.querySelectorAll("*")) {
      if (!element.closest('[data-mcel-theme-preview-kind="region"]') ||
          ![...element.childNodes].some(node => node.nodeType === 3 && node.textContent.trim())) continue;
      const style = computed(element);
      if (style.display === "none" || style.visibility === "hidden" || !element.getClientRects().length) continue;
      const foreground = rgba(style.color);
      if (!foreground) continue;
      const background = backdrop(element,computed);
      const fg = foreground.slice(0,3).map((part,i) => part*foreground[3]+background[i]*(1-foreground[3]));
      const ratio = contrast(fg,background);
      result.checked += 1;
      result.minimumRatio = result.minimumRatio === null ? ratio : Math.min(result.minimumRatio,ratio);
      if (ratio < 4.5) {
        result.belowAA += 1;
        if (result.examples.length < 6)
          result.examples.push({tag:element.tagName.toLowerCase(), ratio:Number(ratio.toFixed(2)),
            region:element.closest('[data-mcel-region]')?.getAttribute('data-mcel-region') || null});
      }
    }
    if (result.minimumRatio !== null) result.minimumRatio = Number(result.minimumRatio.toFixed(2));
    return result;
  }

  function apply({root, ir, bridge, styleLaw, theme, tokens, bindingModel = "legacy-host"} = {}) {
    if (!root?.ownerDocument || !bridge?.observePresentationTargets || !styleLaw?.themes?.includes(theme))
      return {schema, status:"refused", reason:"Missing canonical root, bridge, or recognized theme"};
    if (!tokens || colorTokens.some(token => !safeToken(tokens[token])))
      return {schema, status:"refused", reason:"Existing theme CSS tokens unavailable"};
    const report = bindingModel === "operational-runtime"
      ? bridge.observeRuntimePresentationTargets?.({root,ir})
      : bridge.observePresentationTargets({root,ir});
    if (report?.status !== "source-bound" || !report.targets.some(t => t.kind === "control") ||
        !report.targets.some(t => t.kind === "region"))
      return {schema, status:"refused", reason:"No source-proven control and region bindings", diagnostics:report?.diagnostics};

    const doc = root.ownerDocument;
    let sheet = doc.getElementById(styleId);
    // Inspect the *unprojected* surface paint, even on subsequent theme changes.
    // Otherwise a prior theme could progressively turn every child into a card.
    if (sheet) sheet.disabled = true;
    for (const element of root.querySelectorAll(`[${attr.kind}],[${attr.surface}]`)) {
      for (const key of Object.values(attr)) element.removeAttribute(key);
    }
    root.setAttribute("data-mcel-semantic-theme-sandbox-root","true");
    const computed = doc.defaultView?.getComputedStyle?.bind(doc.defaultView);
    const regions = report.targets.filter(t => t.kind === "region");
    for (const target of report.targets) {
      target.element.setAttribute(attr.kind,target.kind);
      if (target.kind === "control") target.element.setAttribute(attr.operation,target.operationKind);
      else {
        const depth = regions.filter(other => other !== target && other.element.contains(target.element)).length;
        target.element.setAttribute(attr.depth,String(Math.min(depth,2)));
      }
    }
    const painted = computed ? visualDescendants(root,report.targets,computed) : [];
    for (const element of painted) element.setAttribute(attr.surface,"true");

    const declarations = colorTokens.map(key => `${key}:${tokens[key]};`).join("");
    const scope = '[data-mcel-semantic-theme-sandbox-root]';
    // Every role owns both its background and its foreground. The only basis
    // for the scopes is a canonical region/control or its visual descendants.
    // No box metrics, layout, visibility, opacity, or operational state change.
    // The expert application still declares some backgrounds with high-specificity
    // !important shorthands. A dedicated important cascade layer outranks
    // those unlayered declarations for colors only; semantic placement and
    // all box metrics remain owned by the normal MCEL presentation law.
    const css = `@layer mcel-semantic-theme-preview{${scope}{${declarations}--mcel-preview-ink:var(--mcel-theme-ink);`+
      `--mcel-preview-field:var(--mcel-theme-card);`+
      `--mcel-preview-button:color-mix(in srgb,var(--mcel-theme-card) 82%,var(--mcel-theme-accent) 18%);`+
      `--mcel-preview-selected:color-mix(in srgb,var(--mcel-theme-card) 65%,var(--mcel-theme-accent) 35%);`+
      `background-color:var(--mcel-theme-card)!important;color:var(--mcel-preview-ink)!important;}`+
      `${scope} [${attr.kind}="region"]{--mcel-preview-region-surface:color-mix(in srgb,var(--mcel-theme-card) 95%,var(--mcel-theme-accent) 5%);`+
      `background-color:var(--mcel-preview-region-surface)!important;color:var(--mcel-preview-ink)!important;`+
      `border-color:var(--mcel-theme-line)!important;background-image:none!important;box-shadow:none!important;}`+
      `${scope} [${attr.kind}="region"][${attr.depth}="1"]{--mcel-preview-region-surface:color-mix(in srgb,var(--mcel-theme-card) 92%,var(--mcel-theme-accent) 8%);}`+
      `${scope} [${attr.kind}="region"][${attr.depth}="2"]{--mcel-preview-region-surface:color-mix(in srgb,var(--mcel-theme-card) 89%,var(--mcel-theme-accent) 11%);}`+
      `${scope} [${attr.surface}]{background-color:color-mix(in srgb,var(--mcel-preview-region-surface,var(--mcel-theme-card)) 90%,var(--mcel-theme-accent) 10%)!important;`+
      `color:var(--mcel-preview-ink)!important;border-color:var(--mcel-theme-line)!important;`+
      `background-image:none!important;box-shadow:none!important;}`+
      `${scope} [${attr.kind}="control"]{background-color:color-mix(in srgb,var(--mcel-preview-region-surface,var(--mcel-theme-card)) 90%,var(--mcel-theme-accent) 10%)!important;`+
      `color:var(--mcel-preview-ink)!important;border-color:var(--mcel-theme-line)!important;box-shadow:none!important;}`+
      `${scope} :is(label,legend,small,span,strong,em,p,h1,h2,h3,h4,h5,h6,output,summary,dt,dd,li,time){color:var(--mcel-preview-ink)!important;}`+
      `${scope} :is(input,textarea,select){background-color:var(--mcel-preview-field)!important;`+
      `color:var(--mcel-preview-ink)!important;border-color:var(--mcel-theme-line)!important;box-shadow:none!important;}`+
      `${scope} :is(button,[role="button"],[role="tab"],[role="switch"]){background-color:var(--mcel-preview-button)!important;`+
      `color:var(--mcel-preview-ink)!important;border-color:var(--mcel-theme-accent)!important;`+
      `background-image:none!important;box-shadow:none!important;}`+
      `${scope} [${attr.operation}="capability"] :is(button,[role="button"],[role="tab"]){`+
      `background-color:color-mix(in srgb,var(--mcel-theme-card) 82%,var(--mcel-theme-accent-2) 18%)!important;`+
      `border-color:var(--mcel-theme-accent-2)!important;}`+
      `${scope} :is(button,[role="button"],[role="tab"],[role="switch"]):hover:not(:disabled):not([aria-disabled="true"]){`+
      `background-color:color-mix(in srgb,var(--mcel-theme-card) 75%,var(--mcel-theme-accent) 25%)!important;}`+
      `${scope} :is(button,[role="button"],[role="tab"],[role="switch"]):active:not(:disabled):not([aria-disabled="true"]){`+
      `background-color:color-mix(in srgb,var(--mcel-theme-card) 70%,var(--mcel-theme-accent) 30%)!important;}`+
      `${scope} :is(button,[role="button"],[role="tab"],[role="switch"]):is([aria-pressed="true"],[aria-selected="true"],[aria-current="true"],[aria-current="page"],[data-selected="true"],[data-active="true"]){`+
      `background-color:var(--mcel-preview-selected)!important;color:var(--mcel-preview-ink)!important;`+
      `border-color:var(--mcel-theme-accent)!important;}`+
      `${scope} :is(button,[role="button"],[role="tab"],[role="switch"]):is(:disabled,[aria-disabled="true"]),`+
      `${scope} :is(input,textarea,select):is(:disabled,[aria-disabled="true"]){`+
      `background-color:var(--mcel-preview-region-surface,var(--mcel-theme-card))!important;`+
      `color:var(--mcel-theme-muted)!important;border-color:var(--mcel-theme-line)!important;}`+
      `${scope} :is(button,input,textarea,select,[role="button"],[role="tab"]):focus-visible{`+
      `outline:2px solid var(--mcel-theme-accent)!important;outline-offset:1px!important;}`+
      `body:has(${scope}){background-color:${tokens["--mcel-theme-card"]}!important;`+
      `color:${tokens["--mcel-theme-ink"]}!important;}}`;
    if (!sheet) {sheet=doc.createElement("style");sheet.id=styleId;doc.head.appendChild(sheet);}
    sheet.textContent=css;
    sheet.disabled=false;

    const visualEvidence={observed:0,uncoloredRegions:0,uncoloredControls:0,
      paintedDescendants:painted.length,sampleRegionBackground:"",sampleControlBackground:""};
    if (computed) {
      for (const target of report.targets) {
        const color=computed(target.element).backgroundColor || "";
        const transparent=!color || color === "transparent" || color === "rgba(0, 0, 0, 0)";
        visualEvidence.observed += 1;
        if (target.kind === "region") {
          if (!visualEvidence.sampleRegionBackground) visualEvidence.sampleRegionBackground=color;
          if (transparent) visualEvidence.uncoloredRegions += 1;
        } else if (target.kind === "control") {
          if (!visualEvidence.sampleControlBackground) visualEvidence.sampleControlBackground=color;
          if (transparent) visualEvidence.uncoloredControls += 1;
        }
      }
      visualEvidence.contrast=auditContrast(root,computed);
    }
    return {schema,status:"applied-in-isolated-lab-frame",theme,
      source:ir?.application?.appId||"",
      controls:report.targets.filter(t=>t.kind==="control").length,
      regions:regions.length,visualEvidence,diagnostics:report.diagnostics};
  }
  return Object.freeze({schema,themeTokens,apply});
})();
if (typeof window !== "undefined") window.McelSemanticThemePreview=McelSemanticThemePreview;
if (typeof module !== "undefined" && module.exports) module.exports=McelSemanticThemePreview;
