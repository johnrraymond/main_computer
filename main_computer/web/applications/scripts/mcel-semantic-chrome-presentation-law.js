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
  // A DOM-root owner survives duplicated script evaluations and same-origin
  // Lab iframe controllers. No second realm may leave a second set of rails.
  const realmOwner = Symbol("mcel-chrome-owner");
  const ownerProperty = "__mcelChromePresentationOwner";
  const wearSurfaceAttr = "data-mcel-tng-wear-surface";
  const wearGlintAttr = "data-mcel-tng-wear-glint";
  const wearPositionAttr = "data-mcel-tng-wear-position";
  const materialAttr = "data-mcel-chrome-material";
  const materialOwnerAttr = "data-mcel-chrome-material-owner";
  // Native buttons and accessible interactive widgets receive raised material.
  // <details><summary> already exposes its disclosure affordance with a caret;
  // adding button bevels, shadows, or wear to that summary duplicates the
  // indicator. It remains keyboard-operable but is not a raised control.
  const controlSelector = 'button, input, select, textarea, ' +
    '[role="button"], [role="tab"], [role="switch"], ' +
    '[role="textbox"], [role="combobox"]';
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
  /* One repeating 8x8 pixel tile, not stacked textures: a single bright
     phosphor pixel and an irregular, dim pixel field between the hot points.
     The SVG uses the Theme Law's phosphor pigment and crisp, one-pixel cells;
     a deterministic pattern avoids noisy repainting on hover or mouse-down. */
  function buildStippleTexture(phosphor) {
    const pixels = [];
    for (let y = 0; y < 8; y++) {
      for (let x = 0; x < 8; x++) {
        const hot = x === 0 && y === 0;
        const grain = (x * 37 + y * 61 + x * y * 17 + (x ^ y) * 11 + x * x + y * y * 3) % 19;
        if (!hot && grain % 7 === 0) continue;
        const opacity = hot ? "0.35" : (0.042 + (grain % 9) * 0.008).toFixed(3);
        pixels.push(`<rect x="${x}" y="${y}" width="1" height="1" ` +
          `fill="${phosphor}" fill-opacity="${opacity}"/>`);
      }
    }
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8" ` +
      `viewBox="0 0 8 8" shape-rendering="crispEdges">${pixels.join("")}</svg>`;
    return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
  }
  // The bars use only the horizontal space made available by the existing
  // semantic Layout. No new row, top/bottom band, or resize observer exists.
  const css = `
    /* One directional light for TNG material. Material overlays may change
       luminance, but never replace Color Law foreground/background/border
       values or the Theme-defined rail/purple/glass palette. */
    [${rootAttr}="${profileId}"] {
      --mcel-tng-light-angle: 145deg;
      /* The workspace and tactile controls inherit one Theme-colored SVG
         stipple tile through --mcel-tng-stipple, set by apply(). */
    }
    /* The existing Calculator canvas is the glass display field. This
       paints a fixed, extremely fine phosphor grid and mild internal light
       without another DOM layer, pointer handler, or geometry pass. */
    [${rootAttr}="${profileId}"],
    [${rootAttr}="${profileId}"] > [data-mcel-natural-layout] {
      background-color: var(--mcel-tng-glass) !important;
      background-image:
        radial-gradient(ellipse 66% 65% at 53% 37%,
          color-mix(in srgb, var(--mcel-tng-phosphor) 8%, transparent), transparent 100%),
        var(--mcel-tng-stipple),
        linear-gradient(135deg,
          color-mix(in srgb, var(--mcel-tng-glass) 94%, white),
          var(--mcel-tng-glass) 30%,
          color-mix(in srgb, var(--mcel-tng-glass) 94%, black)),
        linear-gradient(var(--mcel-tng-glass), var(--mcel-tng-glass));
      background-size: 100% 100%, 8px 8px, 100% 100%, 100% 100%;
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
        color-mix(in srgb, black 30%, transparent) 100%),
        var(--mcel-tng-stipple) !important;
      /* One 8px tile remains unchanged through hover, held and latched
         states; only the button's directional lighting and bevel move. */
      background-size: 100% 100%, 8px 8px !important;
      box-shadow:
        inset 1px 1px 0 color-mix(in srgb, white 47%, transparent),
        inset 2px 2px 2px color-mix(in srgb, white 9%, transparent),
        inset -2px -2px 3px color-mix(in srgb, black 55%, transparent),
        2px 3px 3px color-mix(in srgb, black 37%, transparent) !important;
      text-shadow: 0 0 2px color-mix(in srgb, currentColor 17%, transparent);
    }
    /* Native :hover/:active transitions. No JS recoloring, no change to
       the runtime's selected, disabled or operational state. */
    [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}):hover:not(:active):not([aria-pressed]):not(:disabled):not([aria-disabled="true"]) {
      background-image: linear-gradient(var(--mcel-tng-light-angle),
        color-mix(in srgb, white 30%, transparent) 0%,
        color-mix(in srgb, white 12%, transparent) 22%,
        transparent 48%,
        color-mix(in srgb, black 9%, transparent) 75%,
        color-mix(in srgb, black 24%, transparent) 100%),
        var(--mcel-tng-stipple) !important;
      box-shadow:
        inset 1px 1px 0 color-mix(in srgb, white 64%, transparent),
        inset -2px -2px 3px color-mix(in srgb, black 46%, transparent),
        0 0 7px color-mix(in srgb, currentColor 22%, transparent),
        2px 3px 3px color-mix(in srgb, black 34%, transparent) !important;
    }
    /* A latched toggle has the very same recessed face as a held-down
       momentary button. The :is(:active, ...) selector must remain on the
       CONTROL itself: adding whitespace would select its descendants instead.
       Hover must never override its semantic state. */
    [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}):is(:active, [aria-pressed="true"], [aria-selected="true"]):not(:disabled):not([aria-disabled="true"]) {
      /* Invert the bevel, not the external light direction. */
      background-image: linear-gradient(var(--mcel-tng-light-angle),
        color-mix(in srgb, black 34%, transparent),
        color-mix(in srgb, black 14%, transparent) 34%,
        transparent 63%,
        color-mix(in srgb, white 8%, transparent)),
        var(--mcel-tng-stipple) !important;
      box-shadow:
        inset 3px 3px 6px color-mix(in srgb, black 68%, transparent),
        inset -1px -1px 1px color-mix(in srgb, white 15%, transparent) !important;
      /* The face must snap inward on mouse-down, even for a brief click.
         The ordinary raised/hover transition resumes after release. */
      transition: none !important;
    }
    /* Operationally significant buttons borrow a subtle emissive halo
       from the Theme's existing TNG phosphor token. The actual action
       colors, hover colors, focus outline and contrast remain Color Law's. */
    [${rootAttr}="${profileId}"] [${materialAttr}="emissive-control"]:not(:active):not([aria-pressed="true"]):not([aria-selected="true"]) {
      box-shadow:
        inset 0 1px 0 color-mix(in srgb, white 30%, transparent),
        inset 0 -2px 2px color-mix(in srgb, black 37%, transparent),
        0 0 7px color-mix(in srgb, var(--mcel-tng-phosphor) 34%, transparent) !important;
    }
    [${rootAttr}="${profileId}"] [${materialAttr}="emissive-control"]:hover:not(:active):not([aria-pressed]):not(:disabled) {
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
    [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}):hover:not(:active):not([aria-pressed]):not(:disabled):not([aria-disabled="true"])
      > [${wearSurfaceAttr}="button"] > [${wearGlintAttr}] {
      opacity: var(--mcel-tng-wear-light, .85) !important;
    }
    /* Depressing a raised control changes its surface normal. The source of
       light remains at 145 degrees, but the inset face now catches reflected
       scratches toward the opposing (lower-right) corner. Preserve the SAME
       grain/scratch coordinates rather than moving or regenerating defects. */
    [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}):is(:active, [aria-pressed="true"], [aria-selected="true"]):not(:disabled):not([aria-disabled="true"])
      > [${wearSurfaceAttr}="button"] {
      opacity: calc(var(--mcel-tng-wear-base, .48) * 1.12) !important;
      -webkit-mask-image: linear-gradient(var(--mcel-tng-light-angle, 145deg),
        rgba(0,0,0,.35) 0%, rgba(0,0,0,.70) 35%, black 75%) !important;
      mask-image: linear-gradient(var(--mcel-tng-light-angle, 145deg),
        rgba(0,0,0,.35) 0%, rgba(0,0,0,.70) 35%, black 75%) !important;
    }
    [${rootAttr}="${profileId}"] :is(${controlMaterialSelector}):is(:active, [aria-pressed="true"], [aria-selected="true"]):not(:disabled):not([aria-disabled="true"])
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
  /* Vulcan is an independent, nonstructural chrome. The white
     sidebar and reading area, boundary-free navigation, rounded input, and quiet
     type are mapped to *existing* semantic regions/controls. Nothing is
     injected into an operational application and no event handler is replaced.
     No decorative wear, side bars, or generated components are involved. */
  const vulcanId = "chrome-vulcan";
  const vulcanStyleId = "mcel-vulcan-chrome-css";
  const vulcanCss = `
    [${rootAttr}="${vulcanId}"] {
      color-scheme: light;
      --mcel-vulcan-ink: #202123;
      --mcel-vulcan-muted: #6e6e73;
      --mcel-vulcan-rule: #e6e6e8;
      --mcel-vulcan-paper: #ffffff;
      --mcel-vulcan-sidebar: #ffffff;
      --mcel-vulcan-hover: #f0f0f1;
      --mcel-vulcan-pressed: #e8e8eb;
      --mcel-vulcan-input: #f9f9fa;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont,
        "Segoe UI", Arial, sans-serif !important;
      color: var(--mcel-vulcan-ink) !important;
      background: var(--mcel-vulcan-paper) !important;
      background-image: none !important;
      text-shadow: none !important;
    }
    [${rootAttr}="${vulcanId}"] > [data-mcel-natural-layout],
    [${rootAttr}="${vulcanId}"] [data-mcel-purpose="workspace"],
    [${rootAttr}="${vulcanId}"] [data-mcel-natural-scroller="workspace"] {
      background: var(--mcel-vulcan-paper) !important;
      background-image: none !important;
      color: var(--mcel-vulcan-ink) !important;
    }
    /* Source-bound secondary regions are cards rather than heavy housings. */
    [${rootAttr}="${vulcanId}"] :is(
      [data-mcel-chrome-material="housing"],
      [data-mcel-chrome-material="instrument-glass"]
    ) {
      background: var(--mcel-vulcan-paper) !important;
      background-image: none !important;
      color: var(--mcel-vulcan-ink) !important;
      border-color: var(--mcel-vulcan-rule) !important;
      box-shadow: none !important;
      text-shadow: none !important;
    }
    /* Existing navigation receives the pale sidebar visual language.
       The layout authority decides whether the rail is left or elsewhere. */
    [${rootAttr}="${vulcanId}"] :is(
      nav, [role="navigation"], [data-mcel-purpose="navigation"],
      [data-mcel-purpose="nav"], [data-mcel-purpose="sidebar"]
    ) {
      background: var(--mcel-vulcan-sidebar) !important;
      background-image: none !important;
      border-color: var(--mcel-vulcan-rule) !important;
      color: var(--mcel-vulcan-ink) !important;
      box-shadow: none !important;
    }
    [${rootAttr}="${vulcanId}"] :is(
      h1,h2,h3,h4,h5,h6,p,label,legend,summary,li
    ) {
      color: var(--mcel-vulcan-ink) !important;
      text-shadow: none !important;
      letter-spacing: normal;
    }
    [${rootAttr}="${vulcanId}"] :is(h1,h2,h3,h4,h5,h6) {
      font-weight: 600;
      line-height: 1.3;
    }
    [${rootAttr}="${vulcanId}"] :is(p,li) { line-height: 1.65; }
    [${rootAttr}="${vulcanId}"] :is(hr, [role="separator"]) {
      border-color: var(--mcel-vulcan-rule) !important;
    }
    [${rootAttr}="${vulcanId}"] :is(button, [role="button"], [role="tab"], [role="switch"],
      input:is([type="button"],[type="reset"],[type="submit"])) {
      background: var(--mcel-vulcan-paper) !important;
      background-image: none !important;
      color: var(--mcel-vulcan-ink) !important;
      border: 1px solid var(--mcel-vulcan-rule) !important;
      border-radius: 12px !important;
      box-shadow: none !important;
      text-shadow: none !important;
      filter: none !important;
      transition: background-color 100ms ease, box-shadow 100ms ease;
    }
    [${rootAttr}="${vulcanId}"] :is(button,[role="button"],[role="tab"],[role="switch"],
      input:is([type="button"],[type="reset"],[type="submit"])):hover:not(:disabled):not([aria-disabled="true"]):not(:active):not([aria-pressed="true"]):not([aria-selected="true"]):not([aria-checked="true"]) {
      background: var(--mcel-vulcan-hover) !important;
      box-shadow: 0 1px 3px #0000000b !important;
    }
    /* A held button and a latched toggle must have identical pressed faces,
       and hover may not erase either state. */
    [${rootAttr}="${vulcanId}"] :is(button,[role="button"],[role="tab"],[role="switch"],
      input:is([type="button"],[type="reset"],[type="submit"])):is(
        :active,[aria-pressed="true"],[aria-selected="true"],[aria-checked="true"]
      ):not(:disabled):not([aria-disabled="true"]) {
      background: var(--mcel-vulcan-pressed) !important;
      box-shadow: inset 0 1px 3px #00000019 !important;
      transition: none !important;
    }
    [${rootAttr}="${vulcanId}"] :is(
      input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="color"]):not([type="file"]):not([type="hidden"]),
      textarea,select,[role="textbox"],[role="combobox"]
    ) {
      background: var(--mcel-vulcan-input) !important;
      background-image: none !important;
      color: var(--mcel-vulcan-ink) !important;
      border: 1px solid var(--mcel-vulcan-rule) !important;
      border-radius: 16px !important;
      box-shadow: none !important;
      text-shadow: none !important;
    }
    [${rootAttr}="${vulcanId}"] :is(input,textarea)::placeholder {
      color: var(--mcel-vulcan-muted) !important;
    }
    [${rootAttr}="${vulcanId}"] :is(button,input,textarea,select,[role="button"],[role="tab"],
      [role="switch"],[role="textbox"],[role="combobox"]):focus-visible {
      outline: 2px solid #7387a5 !important;
      outline-offset: 2px !important;
    }
    [${rootAttr}="${vulcanId}"] :is(button,input,textarea,select,[role="button"],
      [role="tab"],[role="switch"]):disabled,
    [${rootAttr}="${vulcanId}"] [aria-disabled="true"] {
      opacity: .55;
      cursor: not-allowed;
    }
    [${rootAttr}="${vulcanId}"] :is(pre,code,kbd) {
      background: #f7f7f8 !important;
      color: #262626 !important;
      border-color: var(--mcel-vulcan-rule) !important;
      text-shadow: none !important;
    }
    /* Vulcan refinement: no contrasting sidebar, no panel housing or
       shadow seams. Do not remove any semantic nodes or their handlers. */
    [${rootAttr}="${vulcanId}"],
    [${rootAttr}="${vulcanId}"] :is(
      [data-mcel-presentation], [data-mcel-natural-layout],
      [data-mcel-natural-scroller], [data-mcel-zone],
      [data-mcel-region], [data-mcel-mode-group],
      [data-mcel-navigation-chrome], [data-mcel-mode-tabs],
      [data-mcel-inspector-activity], [data-mcel-purpose="sidebar"],
      [data-mcel-purpose="navigation"]
    ) {
      background: #fff !important;
      background-image: none !important;
      color: #202123 !important;
      box-shadow: none !important;
      text-shadow: none !important;
      border-color: transparent !important;
    }
    [${rootAttr}="${vulcanId}"] :is(
      [data-mcel-zone], [data-mcel-natural-scroller],
      [data-mcel-region], [data-mcel-navigation-chrome]
    ) {
      border-width: 0 !important;
    }
    /* Source-bound nested content (records, logs, editor frames and activity)
       retains its own semantics but cannot import a dark Theme background. */
    [${rootAttr}="${vulcanId}"] :is(
      [data-mcel-node], [data-mcel-color-role="surface.panel"],
      [data-mcel-document-surface], [data-mcel-activity],
      [data-mcel-inspector-activity], [data-mcel-records],
      [data-collection], [data-state], output[data-state],
      [data-mcel-purpose="results"]
    ) {
      background: #fff !important;
      background-image: none !important;
      box-shadow: none !important;
      border-color: transparent !important;
      color: #202123 !important;
    }
    /* File Manager's explorer is projected asynchronously from read evidence.
       These source-bound nested nodes are not semantic regions or record rows,
       so the general white-surface rules above do not reach them. Theme CSS
       otherwise leaves three illegible dark islands after the initial load.
       Gate every correction on active Vulcan, not on the application itself. */
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      [data-mcel-explorer-navigation],
      [data-mcel-explorer-content-pane],
      [data-mcel-directory-head],
      [data-mcel-directory-columns],
      [data-mcel-directory-contents],
      [data-mcel-folder-tree],
      [data-mcel-explorer-navigation] [role="treeitem"],
      [data-mcel-directory-entry]
    ) {
      background: #fff !important;
      background-image: none !important;
      color: #202123 !important;
      box-shadow: none !important;
      text-shadow: none !important;
      border-color: transparent !important;
    }
    /* The tree and directory are content, not individual dark cards. */
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      [data-mcel-explorer-navigation], [data-mcel-explorer-content-pane]
    ) {
      border: 0 !important;
      background: #fff !important;
    }
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      [data-mcel-explorer-navigation] h3,
      [data-mcel-directory-head], [data-mcel-directory-head] h3
    ) {
      color: #202123 !important;
      background: #fff !important;
      border: 0 !important;
    }
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      [data-mcel-directory-columns], [data-mcel-directory-entry]
    ) {
      color: #202123 !important;
      background: #fff !important;
      border: 0 !important;
      border-bottom: 1px solid #ededee !important;
    }
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      [data-mcel-directory-path], [data-mcel-directory-type],
      [data-mcel-directory-size], [data-mcel-directory-icon],
      [data-mcel-tree-icon], [data-mcel-tree-toggle], [data-mcel-tree-leaf]
    ) {
      color: #60636a !important;
      text-shadow: none !important;
    }
    /* Navigation locations and folder branches are not boxed controls.
       Selected rows alone receive the same light gray highlight as other
       Vulcan navigation, without dark contrast or separator bars. */
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      [data-mcel-purpose="navigation"] [data-record-key],
      [data-mcel-explorer-navigation] [role="treeitem"]
    ) {
      background: transparent !important;
      border: 0 !important;
      box-shadow: none !important;
      color: #202123 !important;
    }
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      [data-mcel-purpose="navigation"] [data-record-key][aria-selected="true"],
      [data-mcel-explorer-navigation] [role="treeitem"][aria-selected="true"],
      [data-mcel-directory-entry][aria-selected="true"]
    ) {
      background: #ececee !important;
      border: 0 !important;
      box-shadow: none !important;
      color: #202123 !important;
    }
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"]
      [data-mcel-directory-entry]:hover:not([aria-selected="true"]) {
      background: #f5f5f6 !important;
    }
    /* Monaco's mount surface should be an empty white canvas until an
       editor is initialized, rather than showing its former dark placeholder. */
    [${rootAttr}="${vulcanId}"] [data-mcel-document-surface] {
      border: 0 !important;
    }
    /* Unselected navigation actions are literal text on paper. The active
       view alone receives a pale-gray fill, with no outline or dark gap. */
    [${rootAttr}="${vulcanId}"] [data-mcel-mode-tabs] :is(
      button, a, [role="tab"], [role="button"]
    ),
    [${rootAttr}="${vulcanId}"] :is(
      [data-mcel-mode-tabs], [data-mcel-navigation-chrome]
    ) :is(button,a,[role="tab"],[role="button"]):not(:focus-visible) {
      appearance: none !important;
      color: #202123 !important;
      background: transparent !important;
      background-color: transparent !important;
      background-image: none !important;
      border: 0 !important;
      outline: 0 !important;
      border-radius: 10px !important;
      box-shadow: none !important;
      text-shadow: none !important;
      filter: none !important;
    }
    [${rootAttr}="${vulcanId}"] [data-mcel-mode-tabs] :is(
      button,a,[role="tab"],[role="button"]
    ):is([aria-pressed="true"],[aria-selected="true"],[aria-current="page"],:active),
    [${rootAttr}="${vulcanId}"] [data-mcel-mode-tabs] :is(
      button,a,[role="tab"],[role="button"]
    ):is([aria-pressed="true"],[aria-selected="true"],[aria-current="page"]):hover {
      background: #ececee !important;
      background-color: #ececee !important;
      background-image: none !important;
      box-shadow: none !important;
      border: 0 !important;
      color: #202123 !important;
      font-weight: 650;
    }
    [${rootAttr}="${vulcanId}"] [data-mcel-mode-tabs] :is(
      button,a,[role="tab"],[role="button"]
    ):hover:not(:active):not([aria-pressed="true"]):not([aria-selected="true"]):not([aria-current="page"]) {
      background: #f5f5f6 !important;
      box-shadow: none !important;
    }
    /* All other buttons are quiet, boundary-free controls. Selection is
       indicated by a filled face, not by the TNG outline or bezel. */
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      button,[role="button"],[role="tab"],[role="switch"],
      input:is([type="button"],[type="reset"],[type="submit"])
    ) {
      border: 0 !important;
      outline-offset: 2px;
      box-shadow: none !important;
      background-color: transparent !important;
      background-image: none !important;
      color: #202123 !important;
      transition: none !important;
      animation: none !important;
    }
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      button,[role="button"],[role="tab"],[role="switch"],
      input:is([type="button"],[type="reset"],[type="submit"])
    ):hover:not(:disabled):not([aria-disabled="true"]):not(:active):not([aria-pressed="true"]):not([aria-selected="true"]):not([aria-checked="true"]):not([aria-current="page"]) {
      background-color: #f5f5f6 !important;
      box-shadow: none !important;
    }
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      button,[role="button"],[role="tab"],[role="switch"],
      input:is([type="button"],[type="reset"],[type="submit"])
    ):is(:active,[aria-pressed="true"],[aria-selected="true"],[aria-checked="true"],[aria-current="page"]):not(:disabled) {
      background-color: #ececee !important;
      box-shadow: none !important;
    }
    /* Navigation is intentionally text-only, but the calculator's
       source-bound expression keypad is an operational key grid. Each
       keycap must retain a visible boundary including =, rather than
       inheriting the borderless navigation treatment. */
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"]
      [data-mcel-expression-pad] button[data-pad-action] {
      appearance: none !important;
      background: #fff !important;
      background-image: none !important;
      border: 1px solid #c8c9cd !important;
      border-radius: 12px !important;
      box-shadow: none !important;
      color: #202123 !important;
    }
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"]
      [data-mcel-expression-pad] button[data-pad-action]:hover:not(:disabled):not(:active) {
      background: #f5f5f6 !important;
      border-color: #bfc0c5 !important;
    }
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"]
      [data-mcel-expression-pad] button[data-pad-action]:active:not(:disabled) {
      background: #ececee !important;
      border-color: #aeb0b6 !important;
      box-shadow: inset 0 1px 2px #00000010 !important;
    }
    /* An authored command is not a navigation destination. Keep Vulcan's
       borderless mode switches, record selection, activity filters and plain
       text actions unchanged. Only the canonical task footer and the typed
       operation notebook's action row gain the calculator keycap outline.
       This rule is app-neutral and never matches controls by their labels. */
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      [data-mcel-task-actions] > button,
      [data-mcel-task-actions] > [data-node-kind="control"] > button,
      [data-mcel-operation-actions] > button
    ) {
      border: 1px solid #c8c9cd !important;
      border-radius: 12px !important;
      background-color: #fff !important;
      box-shadow: none !important;
    }
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      [data-mcel-task-actions] > button,
      [data-mcel-task-actions] > [data-node-kind="control"] > button,
      [data-mcel-operation-actions] > button
    ):hover:not(:disabled):not([aria-disabled="true"]):not(:active) {
      border-color: #bfc0c5 !important;
    }
    [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"] :is(
      [data-mcel-task-actions] > button,
      [data-mcel-task-actions] > [data-node-kind="control"] > button,
      [data-mcel-operation-actions] > button
    ):is(:active,[aria-pressed="true"]):not(:disabled):not([aria-disabled="true"]) {
      border-color: #aeb0b6 !important;
    }
    [${rootAttr}="${vulcanId}"] :is(button,[role="button"],[role="tab"],[role="switch"],input[type="submit"],input[type="button"],input[type="reset"]) {
      transition: none !important;
      animation: none !important;
    }
    html:has(body [${rootAttr}="${vulcanId}"]) {
      color-scheme: light !important;
      background: #fff !important;
    }
    /* No dark background can bleed through empty space around the mounted
       app, including the generic expert/workbench host body. */
    body.mcel-expert-page:has(> [${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"]),
    body.mcel-workbench-page:has([${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"]),
    body:has([${rootAttr}="${vulcanId}"][${rootAttr}="${vulcanId}"]) {
      background: #fff !important;
      background-image: none !important;
      color: #202123 !important;
      color-scheme: light;
    }
    /* The old fixed-width calculator diagnostic strip is not part of the
       Vulcan chrome. Preserve its live DOM for diagnostics/a11y, but leave no
       visual bar, background, or reserved footer geometry. */
    body:has([${rootAttr}="${vulcanId}"]) :is(#mcel-expanded-diagnostic,#mcel-workbench-status) {
      position: absolute !important;
      width: 1px !important;
      height: 1px !important;
      overflow: hidden !important;
      clip-path: inset(50%) !important;
      margin: 0 !important;
      padding: 0 !important;
      border: 0 !important;
      background: transparent !important;
      white-space: nowrap !important;
    }
    [${rootAttr}="${vulcanId}"] ::selection { background: #dceafa; color: #202123; }
    @media (prefers-reduced-motion: reduce) {
      [${rootAttr}="${vulcanId}"] :is(button,[role="button"],[role="tab"],[role="switch"]) {
        transition: none !important;
      }
    }
  `;

  function mountVulcanMaterials({root,ir,bridge}) {
    const originals = new Map();
    let lastReport = {regions:0,nodes:0,assignments:new Map()};
    function reconcile() {
      const report = resolveMaterialTargets({root,ir,bridge,profile:vulcanId});
      if (report.status !== "ready") return;
      lastReport = report;
      for (const [element,original] of originals) {
        if (!report.assignments.has(element)) {
          if (element.getAttribute(materialOwnerAttr) === vulcanId) {
            if (original.material === null) element.removeAttribute(materialAttr);
            else element.setAttribute(materialAttr,original.material);
            if (original.owner === null) element.removeAttribute(materialOwnerAttr);
            else element.setAttribute(materialOwnerAttr,original.owner);
          }
          originals.delete(element);
        }
      }
      for (const [element,material] of report.assignments) {
        if (!originals.has(element)) originals.set(element,{
          material:element.getAttribute(materialAttr),owner:element.getAttribute(materialOwnerAttr)
        });
        if (element.getAttribute(materialAttr) !== material) element.setAttribute(materialAttr,material);
        if (element.getAttribute(materialOwnerAttr) !== vulcanId) element.setAttribute(materialOwnerAttr,vulcanId);
      }
    }
    reconcile();
    const observer = new root.ownerDocument.defaultView.MutationObserver(reconcile);
    observer.observe(root,{childList:true,subtree:true});
    return {
      report:() => ({regions:lastReport.regions,nodes:lastReport.nodes,
        materials:Object.fromEntries([...lastReport.assignments.values()].reduce((map,kind)=>
          map.set(kind,(map.get(kind)||0)+1),new Map()))}),
      clear:() => {
        observer.disconnect();
        for (const [element,original] of originals) {
          if (element.getAttribute(materialOwnerAttr) !== vulcanId) continue;
          if (original.material === null) element.removeAttribute(materialAttr);
          else element.setAttribute(materialAttr,original.material);
          if (original.owner === null) element.removeAttribute(materialOwnerAttr);
          else element.setAttribute(materialOwnerAttr,original.owner);
        }
        originals.clear();
      }
    };
  }

  function applyVulcan({root,ir,bridge,theme}) {
    const ready = resolveMaterialTargets({root,ir,bridge,profile:vulcanId});
    if (ready.status !== "ready") {
      clear(root);
      return {schema,profile:vulcanId,status:"refused",reason:ready.reason};
    }
    clear(root);
    const doc=root.ownerDocument;
    let sheet=doc.getElementById(vulcanStyleId);
    if (!sheet) {
      sheet=doc.createElement("style");sheet.id=vulcanStyleId;
      sheet.setAttribute("data-mcel-chrome-owner",vulcanId);
      // Vulcan paints only its own root. Its important overrides share the
      // canonical layer so state-specific color rules cannot reinstate dark
      // surfaces after the chrome is selected.
      doc.head.insertBefore(sheet,doc.head.firstChild);
    }
    const layeredVulcanCss=`@layer mcel-semantic-color-canonical{${vulcanCss}}`;
    if(sheet.textContent!==layeredVulcanCss)sheet.textContent=layeredVulcanCss;
    root.setAttribute(rootAttr,vulcanId);
    const materials=mountVulcanMaterials({root,ir,bridge});
    mounts.set(root,{unit:null,workspace:null,rails:[],originalVariables:new Map(),materials});
    root[ownerProperty]={owner:realmOwner,clear};
    return {schema,status:"applied",profile:vulcanId,theme,rails:0,
      source:ir?.application?.appId||"",materialCoverage:materials.report(),
      appChildrenPreserved:true,horizontalOnly:false};
  }

  function restoreVariable(element, name, record) {
    if (record.value) element.style.setProperty(name, record.value, record.priority);
    else element.style.removeProperty(name);
  }
  /* Application-neutral: assignments come only from exact canonical runtime
     region and node bindings. Native controls below those bindings inherit
     material through their DOM role, not application name or containment zone. */
  function resolveMaterialTargets({root, ir, bridge, profile=profileId}) {
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
    // File explorer tree names, disclosure arrows and directory-name links
    // are row navigation, not raised command keycaps. The source runtime
    // already gives these controls transparent faces and row-level selection.
    // Do not stamp TNG bevel/wear material on them. This is deliberately
    // restricted to TNG: Vulcan keeps its existing source-bound assignments.
    const plainExplorerNavigation = element => profile === profileId && element.matches(
      '[data-mcel-explorer-navigation] [role="treeitem"] :is(' +
        'button[data-select-key], button[data-mcel-tree-toggle], button[data-mcel-inspect-folder]), ' +
      '[data-mcel-directory-entry] button[data-mcel-directory-open]');
    const offer = (element, material) => {
      if (element !== root && root.contains(element) && !decorative(element))
        assignments.set(element, material);
    };
    // Every verified non-workspace region is a physical housing unless its
    // purpose explicitly denotes a readout. Thus new semantic purposes (such
    // as query-controls and commands) cannot silently lose Chrome treatment.
    for (const region of regions) {
      const purpose = region.element.getAttribute("data-mcel-purpose") || region.role;
      const material = purpose === "workspace" ? null :
        ["results", "output"].includes(purpose) ? "instrument-glass" : "housing";
      if (material) offer(region.element, material);
    }
    // The source-bound workspace is the *application* boundary, not an
    // untrusted page-wide selector. Runtime-rendered tabs, document actions,
    // disclosures and refresh controls can live directly under a workspace,
    // without their own IR node or non-workspace region. Their *physical*
    // material is derived from native/accessible HTML semantics; their intent
    // and capability are never inferred or changed by Chrome.
    const scopes = new Set([...regions, ...nodes]
      .map(target => target.element).filter(element => element !== root));
    for (const scope of scopes) {
      const controls = [scope, ...scope.querySelectorAll(controlSelector)];
      for (const element of controls) {
        if (!element.matches(controlSelector) || decorative(element) ||
            plainExplorerNavigation(element)) continue;
        const tag = element.localName;
        const type = (element.getAttribute("type") || "text").toLowerCase();
        const role = element.getAttribute("role") || "";
        // A disclosure caret is its own indicator, even if authored with
        // an extra interactive role. Never elevate a native summary.
        if (tag === "summary" && element.parentElement?.localName === "details") continue;
        // Choice and system-specific inputs keep native/browser material.
        if (tag === "input" && fieldTypesExcluded.has(type)) continue;
        if (tag === "button" ||
            ["button", "tab", "switch"].includes(role) ||
            (tag === "input" && ["button", "submit", "reset"].includes(type))) {
          offer(element, type === "submit" ? "emissive-control" : "raised-control");
        } else if (tag === "input" || tag === "textarea" || tag === "select" ||
                   ["textbox", "combobox"].includes(role)) {
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
        // Reconciliation is driven by child insertion, including new activity
        // receipts. Do not restamp every existing material owner on each read:
        // those redundant mutations can wake other presentation observers.
        if (element.getAttribute(materialOwnerAttr) !== profileId)
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
    if(!root) return 0;
    const foreign=root[ownerProperty];
    let removedForeign=0;
    if(foreign && foreign.owner!==realmOwner && typeof foreign.clear==="function") {
      removedForeign=foreign.clear(root);
    }
    const prior = mounts.get(root);
    if (!prior) return removedForeign;
    prior.materials.clear();
    for (const rail of prior.rails) rail.remove();
    prior.unit?.removeAttribute(unitAttr);
    prior.workspace?.removeAttribute(workAttr);
    root.removeAttribute(rootAttr);
    for (const [name, previous] of prior.originalVariables)
      restoreVariable(root, name, previous);
    mounts.delete(root);
    if(root[ownerProperty]?.owner===realmOwner)delete root[ownerProperty];
    return prior.rails.length+removedForeign;
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
    // Unlike TNG, Vulcan needs no themed phosphor palette or rail geometry.
    if (profile === vulcanId) return applyVulcan({root,ir,bridge,theme});
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
    // Take over any earlier presentation law instance on this same physical
    // root before material classification. This includes a parent Lab realm.
    if(root[ownerProperty] && root[ownerProperty].owner!==realmOwner)clear(root);
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
      ["--mcel-tng-phosphor",palette[tokens[3]]],
      ["--mcel-tng-stipple",buildStippleTexture(palette[tokens[3]])]
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
    root[ownerProperty]={owner:realmOwner,clear};
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
