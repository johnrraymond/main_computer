/* MCEL Semantic Chrome Presentation Law v4 — source-bound material composition.
 * All mounted applications use the same region/control material resolver.
 * An eligible primary workspace may additionally receive TNG side rails.
 * Layout and Form own geometry; Theme/Color own semantic palette and state.
 * Chrome-owned decorative textures never participate in semantic geometry.
 */
var McelSemanticChromePresentationLaw = (() => {
  "use strict";
  const schema = "mcel.semantic-chrome-presentation.v1";
  const profileId = "chrome-tng-side-rails";
  const styleId = "mcel-lab-tng-side-rails-css";
  const rootAttr = "data-mcel-semantic-chrome";
  const workAttr = "data-mcel-tng-workspace";
  const unitAttr = "data-mcel-tng-primary-unit";
  const generatedAttr = "data-mcel-chrome-generated";
  const tokens = ["--mcel-theme-chrome-primary", "--mcel-theme-chrome-inset",
    "--mcel-theme-chrome-glass", "--mcel-theme-chrome-phosphor"];
  const mounts = new WeakMap();
  const wearSurfaceAttr = "data-mcel-tng-wear-surface";
  const wearGlintAttr = "data-mcel-tng-wear-glint";
  const wearPositionAttr = "data-mcel-tng-wear-position";
  const materialAttr = "data-mcel-chrome-material";
  const materialOwnerAttr = "data-mcel-chrome-material-owner";
  const controlSelector = 'button, input, select, textarea, [role="button"]';
  const fieldTypesExcluded = new Set(["hidden", "checkbox", "radio", "file", "range", "color", "image"]);
  const controlMaterialSelector = `[${materialAttr}="raised-control"], [${materialAttr}="emissive-control"]`;

  // A single, deterministic collection of physical defects underlies both
  // the dark wear and the light-responsive glints. The same marks appear in
  // both textures; only the pigment and reflection alpha differ. The two SVG
  // data URIs are generated locally, not fetched from a network or image API.
  function buildWearTextures() {
    let seed = 14573;
    const random = () => {
      seed = (1664525 * seed + 1013904223) >>> 0;
      return seed / 4294967296;
    };
    const marks = [];
    for (let i = 0; i < 440; i++) marks.push({
      type: "dot", x: random() * 128, y: random() * 128,
      r: .42 + random() * .65, alpha: .30 + random() * .55
    });
    for (let i = 0; i < 27; i++) {
      const length = 5 + random() * 19;
      marks.push({
        type: "scratch", x: random() * 110, y: random() * 120,
        dx: length, dy: -length * (.20 + random() * .20),
        width: .7 + random() * .6, alpha: .42 + random() * .45
      });
    }
    const texture = reflective => {
      const color = reflective ? "#ffffff" : "#180d07";
      const content = marks.map(mark => {
        const opacity = (reflective ? mark.alpha : mark.alpha * .65).toFixed(2);
        if (mark.type === "dot") return `<circle cx="${mark.x.toFixed(2)}" ` +
          `cy="${mark.y.toFixed(2)}" r="${mark.r.toFixed(2)}" ` +
          `fill="${color}" fill-opacity="${opacity}"/>`;
        return `<path d="M${mark.x.toFixed(2)} ${mark.y.toFixed(2)} ` +
          `l${mark.dx.toFixed(2)} ${mark.dy.toFixed(2)}" fill="none" ` +
          `stroke="${color}" stroke-width="${mark.width.toFixed(2)}" ` +
          `stroke-opacity="${opacity}" stroke-linecap="round"/>`;
      }).join("");
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="128" ` +
        `height="128" viewBox="0 0 128 128">${content}</svg>`;
      return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
    };
    return {dark: texture(false), glint: texture(true)};
  }
  const wearTextures = buildWearTextures();
  // The bars use only the horizontal space made available by the existing
  // semantic Layout. No new row, top/bottom band, or resize observer exists.
  const css = `
    /* One directional light for TNG material. Material overlays may change
       luminance, but never replace Color Law foreground/background/border
       values or the Theme-defined rail/purple/glass palette. */
    [${rootAttr}="${profileId}"] { --mcel-tng-light-angle: 145deg; }
    /* The existing Calculator canvas is the glass display field. This
       paints a fixed, extremely fine phosphor grid and mild internal light
       without another DOM layer, pointer handler, or geometry pass. */
    [${rootAttr}="${profileId}"],
    [${rootAttr}="${profileId}"] > [data-mcel-natural-layout] {
      background-color: var(--mcel-tng-glass) !important;
      background-image:
        radial-gradient(ellipse 66% 65% at 53% 37%,
          color-mix(in srgb, var(--mcel-tng-phosphor) 8%, transparent), transparent 100%),
        radial-gradient(circle,
          color-mix(in srgb, var(--mcel-tng-phosphor) 35%, transparent) 0 0.65px,
          transparent 0.95px),
        linear-gradient(135deg,
          color-mix(in srgb, var(--mcel-tng-glass) 94%, white),
          var(--mcel-tng-glass) 30%,
          color-mix(in srgb, var(--mcel-tng-glass) 94%, black)),
        linear-gradient(var(--mcel-tng-glass), var(--mcel-tng-glass));
      background-size: 100% 100%, 5px 5px, 100% 100%, 100% 100%;
    }
    /* A tactile finish for EXISTING canonical controls. Color Law continues
       to own every button's foreground/background/border color and state.
       Chrome adds only light response (not additional controls, geometry,
       input listeners, or a second semantic color resolver). */
    [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}) {
      /* Diagonal upper-left light; gradient is ALPHA ONLY, so the
         semantic background supplied by Color Law remains visible. */
      background-image: linear-gradient(var(--mcel-tng-light-angle),
        color-mix(in srgb, white 20%, transparent) 0%,
        color-mix(in srgb, white 7%, transparent) 21%,
        transparent 48%,
        color-mix(in srgb, black 11%, transparent) 72%,
        color-mix(in srgb, black 30%, transparent) 100%) !important;
      box-shadow:
        inset 1px 1px 0 color-mix(in srgb, white 47%, transparent),
        inset 2px 2px 2px color-mix(in srgb, white 9%, transparent),
        inset -2px -2px 3px color-mix(in srgb, black 55%, transparent),
        2px 3px 3px color-mix(in srgb, black 37%, transparent) !important;
      text-shadow: 0 0 2px color-mix(in srgb, currentColor 17%, transparent);
    }
    /* Native :hover/:active transitions. No JS recoloring, no change to
       the runtime's selected, disabled or operational state. */
    [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}):hover:not(:disabled):not([aria-disabled="true"]) {
      background-image: linear-gradient(var(--mcel-tng-light-angle),
        color-mix(in srgb, white 30%, transparent) 0%,
        color-mix(in srgb, white 12%, transparent) 22%,
        transparent 48%,
        color-mix(in srgb, black 9%, transparent) 75%,
        color-mix(in srgb, black 24%, transparent) 100%) !important;
      box-shadow:
        inset 1px 1px 0 color-mix(in srgb, white 64%, transparent),
        inset -2px -2px 3px color-mix(in srgb, black 46%, transparent),
        0 0 7px color-mix(in srgb, currentColor 22%, transparent),
        2px 3px 3px color-mix(in srgb, black 34%, transparent) !important;
    }
    [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}):active:not(:disabled):not([aria-disabled="true"]) {
      /* Invert the bevel, not the external light direction. */
      background-image: linear-gradient(var(--mcel-tng-light-angle),
        color-mix(in srgb, black 34%, transparent),
        color-mix(in srgb, black 14%, transparent) 34%,
        transparent 63%,
        color-mix(in srgb, white 8%, transparent)) !important;
      box-shadow:
        inset 3px 3px 6px color-mix(in srgb, black 68%, transparent),
        inset -1px -1px 1px color-mix(in srgb, white 15%, transparent) !important;
    }
    /* The selected MODE button retains its Color Law selection palette;
       only its recessed material edge becomes more legible. */
    [${rootAttr}="${profileId}"] :is(${controlMaterialSelector})
      :is([aria-pressed="true"], [aria-selected="true"]):not(:active) {
      box-shadow:
        inset 0 1px 0 color-mix(in srgb, white 34%, transparent),
        inset 0 -2px 3px color-mix(in srgb, black 38%, transparent),
        0 0 5px color-mix(in srgb, currentColor 18%, transparent) !important;
    }
    /* Operationally significant buttons borrow a subtle emissive halo
       from the Theme's existing TNG phosphor token. The actual action
       colors, hover colors, focus outline and contrast remain Color Law's. */
    [${rootAttr}="${profileId}"] [${materialAttr}="emissive-control"]:not(:active) {
      box-shadow:
        inset 0 1px 0 color-mix(in srgb, white 30%, transparent),
        inset 0 -2px 2px color-mix(in srgb, black 37%, transparent),
        0 0 7px color-mix(in srgb, var(--mcel-tng-phosphor) 34%, transparent) !important;
    }
    [${rootAttr}="${profileId}"] [${materialAttr}="emissive-control"]
      :hover:not(:active):not(:disabled) {
      box-shadow:
        inset 0 1px 0 color-mix(in srgb, white 39%, transparent),
        inset 0 -2px 2px color-mix(in srgb, black 30%, transparent),
        0 0 10px color-mix(in srgb, var(--mcel-tng-phosphor) 46%, transparent) !important;
    }
    /* A positioning context only for buttons whose natural position is static.
       The marker is reversible, unlike a persistent inline style mutation. */
    [${rootAttr}="${profileId}"] [${wearPositionAttr}="relative"] {
      position: relative !important;
    }
    /* Micro-grain and scratch glints are real inert overlay elements. They
       overlay (never replace) the Color/Theme paint and the 145-degree
       highlights. The gold segments remain worn; purple insets stay clean. */
    [${rootAttr}="${profileId}"] [${wearSurfaceAttr}] {
      position: absolute !important;
      inset: 0 !important;
      display: block !important;
      overflow: hidden !important;
      border-radius: inherit !important;
      pointer-events: none !important;
      user-select: none !important;
      z-index: 3 !important;
      background-image: ${wearTextures.dark} !important;
      background-size: 128px 128px !important;
      background-repeat: repeat !important;
      background-position: var(--mcel-tng-wear-x, 0px) var(--mcel-tng-wear-y, 0px) !important;
      opacity: var(--mcel-tng-wear-base, .48) !important;
    }
    [${rootAttr}="${profileId}"] [${wearGlintAttr}] {
      position: absolute !important;
      inset: 0 !important;
      display: block !important;
      border-radius: inherit !important;
      pointer-events: none !important;
      background-image: ${wearTextures.glint} !important;
      background-size: 128px 128px !important;
      background-repeat: repeat !important;
      background-position: var(--mcel-tng-wear-x, 0px) var(--mcel-tng-wear-y, 0px) !important;
      -webkit-mask-image: linear-gradient(var(--mcel-tng-light-angle, 145deg),
        black 0%, rgba(0,0,0,.95) 22%, rgba(0,0,0,.48) 60%, rgba(0,0,0,.05) 100%) !important;
      mask-image: linear-gradient(var(--mcel-tng-light-angle, 145deg),
        black 0%, rgba(0,0,0,.95) 22%, rgba(0,0,0,.48) 60%, rgba(0,0,0,.05) 100%) !important;
      opacity: var(--mcel-tng-wear-light, .85) !important;
    }
    [${rootAttr}="${profileId}"] [${wearSurfaceAttr}="rail"] {
      -webkit-mask-image: linear-gradient(to bottom, black 0% 43.9%,
        transparent 44% 56%, black 56.1% 100%) !important;
      mask-image: linear-gradient(to bottom, black 0% 43.9%,
        transparent 44% 56%, black 56.1% 100%) !important;
    }
    [${rootAttr}="${profileId}"] [${wearSurfaceAttr}="button"] {
      inset: 1px !important;
      opacity: calc(var(--mcel-tng-wear-base, .48) * .65) !important;
    }
    [${rootAttr}="${profileId}"] [${wearSurfaceAttr}="button"] > [${wearGlintAttr}] {
      opacity: calc(var(--mcel-tng-wear-light, .85) * .28) !important;
    }
    [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}):hover:not(:disabled):not([aria-disabled="true"])
      > [${wearSurfaceAttr}="button"] > [${wearGlintAttr}] {
      opacity: var(--mcel-tng-wear-light, .85) !important;
    }
    /* Depressing a raised control changes its surface normal. The source of
       light remains at 145 degrees, but the inset face now catches reflected
       scratches toward the opposing (lower-right) corner. Preserve the SAME
       grain/scratch coordinates rather than moving or regenerating defects. */
    [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}):active:not(:disabled):not([aria-disabled="true"])
      > [${wearSurfaceAttr}="button"] {
      opacity: calc(var(--mcel-tng-wear-base, .48) * 1.12) !important;
      -webkit-mask-image: linear-gradient(var(--mcel-tng-light-angle, 145deg),
        rgba(0,0,0,.35) 0%, rgba(0,0,0,.70) 35%, black 75%) !important;
      mask-image: linear-gradient(var(--mcel-tng-light-angle, 145deg),
        rgba(0,0,0,.35) 0%, rgba(0,0,0,.70) 35%, black 75%) !important;
    }
    [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}):active:not(:disabled):not([aria-disabled="true"])
      > [${wearSurfaceAttr}="button"] > [${wearGlintAttr}] {
      -webkit-mask-image: linear-gradient(var(--mcel-tng-light-angle, 145deg),
        rgba(0,0,0,.02) 0%, rgba(0,0,0,.18) 30%,
        rgba(0,0,0,.78) 68%, black 100%) !important;
      mask-image: linear-gradient(var(--mcel-tng-light-angle, 145deg),
        rgba(0,0,0,.02) 0%, rgba(0,0,0,.18) 30%,
        rgba(0,0,0,.78) 68%, black 100%) !important;
      opacity: var(--mcel-tng-wear-light, .85) !important;
      /* A normal click is brief: the depression must be perceptible before
         mouse-up rather than spending 120ms fading into view. */
      transition: opacity 25ms linear !important;
    }
    @media (prefers-reduced-motion: no-preference) {
      [${rootAttr}="${profileId}"] [${wearSurfaceAttr}="button"] > [${wearGlintAttr}] {
        transition: opacity 120ms ease;
      }
    }
    @media (prefers-reduced-motion: no-preference) {
      [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}) { transition: box-shadow 110ms ease, background-image 110ms ease; }
    }
    /* The browser/OS remains authoritative in forced-colors mode. Surface
       highlights must never obscure native system colors or focus rings. */
    @media (forced-colors: active) {
      [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}) {
        background-image: none !important;
        box-shadow: none !important;
        text-shadow: none !important;
      }
      [${rootAttr}="${profileId}"] [${wearSurfaceAttr}] {
        display: none !important;
      }
    }
    /* Region and field materials consume the already resolved Theme surface
       colors. Alpha-only light, no independent material palette. These rules
       apply identically to any operational MCEL application. */
    [${rootAttr}="${profileId}"] [${materialAttr}="housing"] {
      background-image: linear-gradient(var(--mcel-tng-light-angle),
        color-mix(in srgb, white 8%, transparent),
        transparent 38%, color-mix(in srgb, black 23%, transparent)) !important;
      box-shadow:
        inset 1px 1px 0 color-mix(in srgb, var(--mcel-tng-phosphor) 27%, transparent),
        inset -2px -2px 6px color-mix(in srgb, black 38%, transparent),
        1px 3px 7px color-mix(in srgb, black 26%, transparent) !important;
    }
    [${rootAttr}="${profileId}"] [${materialAttr}="instrument-glass"] {
      background-image:
        radial-gradient(ellipse 85% 70% at 24% 8%,
          color-mix(in srgb, var(--mcel-tng-phosphor) 13%, transparent), transparent 85%),
        linear-gradient(var(--mcel-tng-light-angle),
          color-mix(in srgb, white 5%, transparent),
          transparent 40%, color-mix(in srgb, black 32%, transparent)) !important;
      box-shadow: inset 2px 2px 5px color-mix(in srgb, black 49%, transparent),
        inset -1px -1px 0 color-mix(in srgb, var(--mcel-tng-phosphor) 22%, transparent) !important;
    }
    [${rootAttr}="${profileId}"] [${materialAttr}="recessed-field"] {
      background-image: linear-gradient(var(--mcel-tng-light-angle),
        color-mix(in srgb, black 17%, transparent),
        transparent 52%, color-mix(in srgb, white 6%, transparent)) !important;
      box-shadow: inset 2px 2px 4px color-mix(in srgb, black 49%, transparent),
        inset -1px -1px 0 color-mix(in srgb, var(--mcel-tng-phosphor) 20%, transparent) !important;
    }
    @media (forced-colors: active) {
      [${rootAttr}="${profileId}"] :is(
        [${materialAttr}="housing"], [${materialAttr}="instrument-glass"],
        [${materialAttr}="recessed-field"]) {
        background-image: none !important;
        box-shadow: none !important;
      }
    }
    [${rootAttr}="${profileId}"] [${workAttr}="true"] {
      container-name: mcel-tng-workspace;
      container-type: inline-size;
    }
    [${rootAttr}="${profileId}"] [${unitAttr}="true"] {
      position: relative !important;
      margin-inline: auto !important;
      isolation: isolate;
    }
    [${rootAttr}="${profileId}"] [${unitAttr}="true"] > [${generatedAttr}="true"] {
      box-sizing: border-box;
      display: block;
      position: absolute;
      inset-block: 0;
      inline-size: clamp(0px, calc((100cqw - 100% - 32px) / 2), 82px);
      min-inline-size: 0;
      pointer-events: none !important;
      user-select: none;
      z-index: 0;
      background: linear-gradient(to bottom,
        var(--mcel-tng-rail-primary) 0% 44%,
        transparent 44% 46%,
        var(--mcel-tng-rail-inset) 46% 54%,
        transparent 54% 56%,
        var(--mcel-tng-rail-primary) 56% 100%);
      filter: drop-shadow(0 3px 4px color-mix(in srgb, black 55%, transparent))
        drop-shadow(0 0 4px color-mix(in srgb, var(--mcel-tng-rail-primary) 20%, transparent));
    }
    /* Molded highlights belong to the existing rails, not to new framing.
       Restrict them to gold so the purple inset remains clean and square. */
    [${rootAttr}="${profileId}"] [${unitAttr}="true"] > [${generatedAttr}="true"]::before {
      content: "";
      position: absolute;
      inset: 0;
      border-radius: inherit;
      /* Directional specular layer is clipped to the gold segments by
         the existing mask. It cannot recolor the purple inset. */
      background:
        radial-gradient(ellipse 78% 32% at 14% 7%,
          color-mix(in srgb, white 72%, transparent),
          color-mix(in srgb, white 26%, transparent) 29%,
          transparent 90%),
        linear-gradient(var(--mcel-tng-light-angle),
          color-mix(in srgb, white 37%, transparent),
          color-mix(in srgb, white 13%, transparent) 23%,
          transparent 53%,
          color-mix(in srgb, black 15%, transparent) 85%,
          color-mix(in srgb, black 24%, transparent));
      -webkit-mask-image: linear-gradient(to bottom,
        black 0% 44%, transparent 44% 56%, black 56% 100%);
      mask-image: linear-gradient(to bottom,
        black 0% 44%, transparent 44% 56%, black 56% 100%);
    }
    [${rootAttr}="${profileId}"] [${unitAttr}="true"] >
      [data-mcel-chrome-part="tng-side-right"]::before {
      background:
        radial-gradient(ellipse 72% 28% at 27% 8%,
          color-mix(in srgb, white 51%, transparent),
          color-mix(in srgb, white 17%, transparent) 29%,
          transparent 90%),
        linear-gradient(var(--mcel-tng-light-angle),
          color-mix(in srgb, white 27%, transparent),
          color-mix(in srgb, white 10%, transparent) 23%,
          transparent 53%,
          color-mix(in srgb, black 18%, transparent) 85%,
          color-mix(in srgb, black 29%, transparent));
    }
    [${rootAttr}="${profileId}"] [${unitAttr}="true"] > [${generatedAttr}="true"]::after {
      content: "";
      position: absolute;
      inset-inline: 0;
      top: 46%;
      height: 8%;
      border-radius: 0;
      background: linear-gradient(to bottom,
        color-mix(in srgb, var(--mcel-tng-rail-inset) 88%, black),
        var(--mcel-tng-rail-inset) 45%,
        color-mix(in srgb, var(--mcel-tng-rail-inset) 93%, black));
      box-shadow:
        inset 0 2px 3px color-mix(in srgb, black 32%, transparent),
        inset 0 -1px 0 color-mix(in srgb, white 19%, transparent),
        0 0 1px color-mix(in srgb, black 23%, transparent);
    }
    [${rootAttr}="${profileId}"] [${unitAttr}="true"] > [data-mcel-chrome-part="tng-side-left"] {
      inset-inline-end: calc(100% + 8px);
      border-radius: 48px 0 0 48px;
    }
    [${rootAttr}="${profileId}"] [${unitAttr}="true"] > [data-mcel-chrome-part="tng-side-right"] {
      inset-inline-start: calc(100% + 8px);
      border-radius: 0 48px 48px 0;
    }
    @container mcel-tng-workspace (max-width: 480px) {
      [${unitAttr}="true"] > [${generatedAttr}="true"] { display: none !important; }
    }
  `;
  function restoreVariable(element, name, record) {
    if (record.value) element.style.setProperty(name, record.value, record.priority);
    else element.style.removeProperty(name);
  }
  /* Application-neutral: assignments come only from exact canonical runtime
     region and node bindings. Native controls below those bindings inherit
     material through their DOM role, not application name or containment zone. */
  function resolveMaterialTargets({root, ir, bridge}) {
    const observed = bridge?.observeRuntimePresentationTargets?.({root, ir});
    if (observed?.status !== "source-bound") return {
      status: "refused", reason: observed?.reason || "Source-bound operational runtime required",
      assignments: new Map()
    };
    const assignments = new Map();
    const regions = observed.targets.filter(t => t.kind === "region" &&
      root.contains(t.element));
    const nodes = observed.targets.filter(t => t.kind === "control" &&
      root.contains(t.element));
    const decorative = element => Boolean(element.closest(
      `[${generatedAttr}="true"], [data-mcel-chrome-decorative="true"], [${wearSurfaceAttr}]`));
    const offer = (element, material) => {
      if (element !== root && root.contains(element) && !decorative(element))
        assignments.set(element, material);
    };
    const regionMaterials = {
      "mode-control": "housing", "input": "housing", "inspection": "housing",
      "assistance": "housing", "status": "housing", "navigation": "housing",
      "results": "instrument-glass", "output": "instrument-glass"
    };
    for (const region of regions) {
      const purpose = region.element.getAttribute("data-mcel-purpose") || region.role;
      if (regionMaterials[purpose]) offer(region.element, regionMaterials[purpose]);
    }
    // Descendants of exact source-bound regions and nodes are allowed to
    // inherit their *physical* type from native HTML semantics. The root
    // workspace is not a catch-all: diagnostics and unrelated DOM stay clean.
    const scopes = new Set([...regions.filter(t => t.element !== root &&
      (t.element.getAttribute("data-mcel-purpose") || t.role) !== "workspace"),
      ...nodes].map(t => t.element));
    for (const scope of scopes) {
      const controls = [scope, ...scope.querySelectorAll(controlSelector)];
      for (const element of controls) {
        if (!element.matches(controlSelector) || decorative(element)) continue;
        const tag = element.localName, type = (element.getAttribute("type") || "text").toLowerCase();
        if (tag === "button" || element.getAttribute("role") === "button" ||
            (tag === "input" && ["button", "submit", "reset"].includes(type))) {
          offer(element, type === "submit" ? "emissive-control" : "raised-control");
        } else if ((tag === "input" && !fieldTypesExcluded.has(type)) ||
                   tag === "textarea" || tag === "select") {
          offer(element, "recessed-field");
        }
      }
    }
    return {status: "ready", assignments, regions: regions.length, nodes: nodes.length};
  }

  /* The material markers and wear are scoped to this Chrome invocation.
     Mutation reconciliation also covers mode changes and generated controls.
     It never writes a position/size/color/style into an application's source. */
  function mountMaterials({root, ir, bridge, rails}) {
    const originalMarkers = new Map();
    const overlays = new Set();
    let lastReport = {status:"ready", assignments:new Map(), regions:0, nodes:0};
    function attachWear(element, kind, index) {
      // Native void controls can receive CSS material, but cannot host a
      // decorative child (input elements in particular).
      if (kind === "button" && element.localName === "input") return;
      if (element.querySelector(`:scope > [${wearSurfaceAttr}]`)) return;
      if (kind === "button" && root.ownerDocument.defaultView
          ?.getComputedStyle(element).position === "static") {
        element.setAttribute(wearPositionAttr, "relative");
      }
      const surface = root.ownerDocument.createElement("span");
      surface.setAttribute(wearSurfaceAttr, kind);
      surface.setAttribute("data-mcel-chrome-decorative", "true");
      surface.setAttribute("aria-hidden", "true");
      surface.setAttribute("inert", "");
      surface.style.setProperty("--mcel-tng-wear-x", `${(index * 37) % 128}px`);
      surface.style.setProperty("--mcel-tng-wear-y", `${(index * 53) % 128}px`);
      const glint = root.ownerDocument.createElement("span");
      glint.setAttribute(wearGlintAttr, "");
      glint.setAttribute("aria-hidden", "true");
      glint.setAttribute("inert", "");
      surface.appendChild(glint);
      element.appendChild(surface);
      overlays.add(surface);
    }
    function reconcile() {
      const report = resolveMaterialTargets({root, ir, bridge});
      if (report.status !== "ready") return;
      lastReport = report;
      for (const [element, original] of originalMarkers) {
        if (!report.assignments.has(element)) {
          if (root.contains(element)) {
            if (original.material === null) element.removeAttribute(materialAttr);
            else element.setAttribute(materialAttr, original.material);
            if (original.owner === null) element.removeAttribute(materialOwnerAttr);
            else element.setAttribute(materialOwnerAttr, original.owner);
            element.querySelector(`:scope > [${wearSurfaceAttr}="button"]`)?.remove();
            element.removeAttribute(wearPositionAttr);
          }
          originalMarkers.delete(element);
        }
      }
      let index = 0;
      for (const [element, material] of report.assignments) {
        if (!originalMarkers.has(element)) {
          // Runtime clones may carry Chrome's own material marker; such a
          // clone must not become the new source of material authority.
          const owned = element.getAttribute(materialOwnerAttr) === profileId;
          originalMarkers.set(element, {material:owned ? null : element.getAttribute(materialAttr),
            owner:owned ? null : element.getAttribute(materialOwnerAttr)});
        }
        if (element.getAttribute(materialAttr) !== material)
          element.setAttribute(materialAttr, material);
        element.setAttribute(materialOwnerAttr, profileId);
        if (material === "raised-control" || material === "emissive-control")
          attachWear(element, "button", index++);
      }
      // Do not retain obsolete detached texture nodes across long sessions.
      for (const overlay of overlays) {
        if (!root.contains(overlay)) overlays.delete(overlay);
      }
    }
    rails.forEach((rail, index) => attachWear(rail, "rail", index));
    reconcile();
    const observer = new root.ownerDocument.defaultView.MutationObserver(reconcile);
    observer.observe(root, {childList:true, subtree:true});
    return {
      report: () => ({regions:lastReport.regions, nodes:lastReport.nodes,
        materials: Object.fromEntries([...lastReport.assignments.values()].reduce((acc, value) =>
          acc.set(value, (acc.get(value) || 0) + 1), new Map()))}),
      clear: () => {
        observer.disconnect();
        // Also remove cloned overlays created by an application rerender.
        root.querySelectorAll(`[${wearSurfaceAttr}]`).forEach(overlay => overlay.remove());
        overlays.forEach(overlay => overlay.remove());
        for (const [element, original] of originalMarkers) {
          if (original.material === null) element.removeAttribute(materialAttr);
          else element.setAttribute(materialAttr, original.material);
          if (original.owner === null) element.removeAttribute(materialOwnerAttr);
          else element.setAttribute(materialOwnerAttr, original.owner);
          element.removeAttribute(wearPositionAttr);
        }
      }
    };
  }
  function clear(root) {
    const prior = mounts.get(root);
    if (!prior) return 0;
    prior.materials.clear();
    for (const rail of prior.rails) rail.remove();
    prior.unit?.removeAttribute(unitAttr);
    prior.workspace?.removeAttribute(workAttr);
    root.removeAttribute(rootAttr);
    for (const [name, previous] of prior.originalVariables)
      restoreVariable(root, name, previous);
    mounts.delete(root);
    return prior.rails.length;
  }
  function findPrimaryUnit({root, ir, bridge}) {
    const report = bridge?.observeRuntimePresentationTargets?.({root,ir});
    if (report?.status !== "source-bound")
      return {status:"refused",reason:"Source-bound canonical runtime targets required"};
    const targets = report.targets.filter(item => item.kind === "region" &&
      item.element.getAttribute("data-mcel-purpose") === "input" &&
      item.element.getAttribute("data-mcel-priority") === "primary" &&
      item.element.getAttribute("data-mcel-tasks")?.split(/\s+/).includes("compute"));
    const units = [...new Set(targets.map(item =>
      item.element.closest('[data-mcel-working-unit][data-mcel-sizing="intrinsic"]')))].filter(Boolean);
    if (units.length !== 1) return {status:"refused",reason:"Exactly one primary intrinsic compute working unit required"};
    const unit=units[0];
    const workspace=unit.parentElement;
    if (workspace?.getAttribute("data-mcel-natural-scroller") !== "workspace" ||
        !root.contains(workspace)) return {status:"refused",reason:"Canonical workspace ownership not verified"};
    return {status:"ready",unit,workspace};
  }
  function apply({root,ir,bridge,profile="chrome-strict-hierarchy",theme="theme-machine",styleLaw}={}) {
    if (!root?.ownerDocument || !root.isConnected)
      return {schema,status:"refused",reason:"Mounted modern MCEL root is required"};
    if (profile !== profileId) {
      clear(root);
      return {schema,status:"unchanged",profile};
    }
    if (!styleLaw?.themePaletteFor) return {schema,status:"refused",reason:"Shared Theme Law is required"};
    const palette=styleLaw.themePaletteFor(theme);
    if (!palette || tokens.some(name=>!palette[name])) {
      clear(root);
      return {schema,status:"refused",reason:"Theme has no canonical Chrome palette"};
    }
    // Source-bound material assignment is required; the special TNG rails
    // are optional if the mounted application's geometry has no rail host.
    const materialsReady=resolveMaterialTargets({root,ir,bridge});
    if (materialsReady.status !== "ready") {
      clear(root); return {schema,profile,status:"refused",reason:materialsReady.reason};
    }
    const found=findPrimaryUnit({root,ir,bridge});
    const unit=found.status === "ready" ? found.unit : null;
    const workspace=found.status === "ready" ? found.workspace : null;
    clear(root);
    const doc=root.ownerDocument;
    let sheet=doc.getElementById(styleId);
    if (!sheet) {
      sheet=doc.createElement("style");sheet.id=styleId;
      sheet.setAttribute("data-mcel-chrome-owner",profileId);
      doc.head.appendChild(sheet);
    }
    if (sheet.textContent !== css) sheet.textContent=css;
    const vars = [
      ["--mcel-tng-rail-primary",palette[tokens[0]]],
      ["--mcel-tng-rail-inset",palette[tokens[1]]],
      ["--mcel-tng-glass",palette[tokens[2]]],
      ["--mcel-tng-phosphor",palette[tokens[3]]]
    ];
    const originalVariables=new Map(vars.map(([name])=>[name,{
      value:root.style.getPropertyValue(name),priority:root.style.getPropertyPriority(name)
    }]));
    root.setAttribute(rootAttr,profileId);
    if (workspace) workspace.setAttribute(workAttr,"true");
    if (unit) unit.setAttribute(unitAttr,"true");
    vars.forEach(([name,value])=>root.style.setProperty(name,value));
    const rails = unit ? ["left","right"].map(side=>{
      const rail=doc.createElement("span");
      rail.setAttribute(generatedAttr,"true");
      rail.setAttribute("data-mcel-chrome-part",`tng-side-${side}`);
      rail.setAttribute("data-mcel-chrome-primitive","structural-side-rail");
      rail.setAttribute("aria-hidden","true");
      rail.setAttribute("inert","");
      unit.appendChild(rail);
      return rail;
    }) : [];
    const materials=mountMaterials({root,ir,bridge,rails});
    mounts.set(root,{unit,workspace,rails,originalVariables,materials});
    return {schema,status:"applied",profile,theme,rails:rails.length,purpleInsets:rails.length,
      displayMaterial:"black-glass-pixel",railMaterial:"clean-molded",
      source:ir?.application?.appId||"",targetPurpose:unit ? "eligible-primary-workspace" : "none",
      materialCoverage:materials.report(),
      horizontalOnly:true,appChildrenPreserved:true};
  }
  return Object.freeze({schema,profileId,apply,clear,findPrimaryUnit,resolveMaterialTargets});
})();
if (typeof window !== "undefined") window.McelSemanticChromePresentationLaw = McelSemanticChromePresentationLaw;
if (typeof module !== "undefined" && module.exports) module.exports=McelSemanticChromePresentationLaw;
