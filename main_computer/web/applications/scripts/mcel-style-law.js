    var McelLabStyleLaw = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const {attributes, defaults, themes: contractThemes, themeAliases: contractThemeAliases} = contract;
      const registry = typeof McelLabLawRegistry !== "undefined" ? McelLabLawRegistry : window.McelLabLawRegistry;

      const fallbackThemes = Object.freeze([
        "theme-machine",
        "theme-local",
        "theme-saas",
        "theme-editorial",
        "theme-luxury",
        "theme-civic",
        "theme-accessible",
        "theme-debug"
      ]);

      const themes = Object.freeze(contractThemes || fallbackThemes);

      const themeAliases = Object.freeze({
        default: "theme-machine", // Preserves the existing Lab default until shared-default migration.
        "theme-basic": "theme-local",
        basic: "theme-local",
        local: "theme-local",
        "local-service": "theme-local",
        "small-business": "theme-local",
        machine: "theme-machine",
        original: "theme-machine",
        "original-mcel": "theme-machine",
        launch: "theme-saas",
        startup: "theme-saas",
        saas: "theme-saas",
        product: "theme-saas",
        "theme-article": "theme-editorial",
        article: "theme-editorial",
        editorial: "theme-editorial",
        magazine: "theme-editorial",
        "theme-premium": "theme-luxury",
        premium: "theme-luxury",
        luxury: "theme-luxury",
        portfolio: "theme-luxury",
        civic: "theme-civic",
        nonprofit: "theme-civic",
        public: "theme-civic",
        "theme-accessibility": "theme-accessible",
        accessibility: "theme-accessible",
        accessible: "theme-accessible",
        "high-contrast": "theme-accessible",
        debug: "theme-debug",
        wireframe: "theme-debug",
        ...(contractThemeAliases || {})
      });

      const themeDefinitions = Object.freeze({
        "theme-machine": Object.freeze({
          id: "theme-machine",
          label: "Original MCEL",
          description: "The original dark MCEL product surface with gold, sky, and mint accents and the green hero ornament.",
          audience: "MCEL default, demos, engine lab previews"
        }),
        "theme-local": Object.freeze({
          id: "theme-local",
          label: "Local Service",
          description: "Warm small-business pages with obvious calls to action, trust cards, and practical form styling.",
          audience: "restaurants, neighborhood services, local markets, clinics"
        }),
        "theme-saas": Object.freeze({
          id: "theme-saas",
          label: "SaaS Launch",
          description: "Polished product-launch pages with dark gradients, strong hero contrast, and modern conversion buttons.",
          audience: "apps, tools, product launches, B2B demos"
        }),
        "theme-editorial": Object.freeze({
          id: "theme-editorial",
          label: "Editorial / Magazine",
          description: "Reading-first pages with paper texture, serif headlines, narrow measure, and article-like sections.",
          audience: "blogs, newsletters, guides, publications"
        }),
        "theme-luxury": Object.freeze({
          id: "theme-luxury",
          label: "Luxury / Portfolio",
          description: "Premium visual language with dark surfaces, restrained gold accents, large imagery, and spacious sections.",
          audience: "studios, consultants, portfolios, premium services"
        }),
        "theme-civic": Object.freeze({
          id: "theme-civic",
          label: "Civic / Nonprofit",
          description: "Clear institutional pages with trustworthy blues, strong navigation hierarchy, and public-service forms.",
          audience: "nonprofits, civic projects, community programs"
        }),
        "theme-accessible": Object.freeze({
          id: "theme-accessible",
          label: "Accessible High Contrast",
          description: "High-contrast, large-target, reduced-decoration pages for legibility and keyboard confidence.",
          audience: "public services, accessibility-first deployments, critical forms"
        }),
        "theme-debug": Object.freeze({
          id: "theme-debug",
          label: "Debug Wireframe",
          description: "Developer-facing wireframe theme that makes semantic boxes, generated parts, and layout proof visible.",
          audience: "MCEL development and QA"
        })
      });

      const themeCatalog = Object.freeze(themes.map((id) => themeDefinitions[id] || Object.freeze({
        id,
        label: id.replace(/^theme-/, "").replace(/-/g, " "),
        description: "Custom MCEL theme",
        audience: "custom"
      })));

      // Color Law v1 is observational. Applications will eventually supply
      // canonical meaning/state; this patch does NOT assign roles to elements,
      // inject styles, or alter the existing Theme projection.
      const semanticColorSchema = "mcel.semantic-color.v1";
      const semanticColorRoles = Object.freeze([
        "surface.canvas", "surface.panel", "surface.control",
        "text.primary", "text.muted", "navigation.group", "navigation.item", "navigation.selected",
        "action.start", "action.stop", "action.enable", "action.disable",
        "action.inspect", "action.generic", "action.input", "action.operator",
        "action.evaluate", "action.clear", "action.reset",
        "diagnostic.error", "diagnostic.warning", "diagnostic.success", "diagnostic.info"
      ]);
      const semanticColorStates = Object.freeze([
        "enabled", "disabled", "active", "selected", "pending", "unavailable"
      ]);
      const semanticColorSurfaces = Object.freeze([
        "text", "indicator", "control", "panel", "navigation"
      ]);
      const semanticPaletteTokens = Object.freeze([
        "--mcel-theme-card", "--mcel-theme-ink", "--mcel-theme-muted",
        "--mcel-theme-accent", "--mcel-theme-accent-2", "--mcel-theme-line",
        "--mcel-theme-error", "--mcel-theme-warning", "--mcel-theme-success"
      ]);
      // Canonical Theme palette authority. The MCEL Lab preview's legacy
      // CSS swatches are compatibility projections of these same tokens.
      // Applications may only declare semantic roles; physical palette values
      // are owned here and shared by every application host.
      const themeColorPalettes = Object.freeze({
        "theme-machine": Object.freeze({"--mcel-theme-accent": "#f6c75b", "--mcel-theme-accent-2": "#aee06f", "--mcel-theme-card": "#090b08", "--mcel-theme-error": "#ff8b8b", "--mcel-theme-ink": "#fff8df", "--mcel-theme-line": "rgba(246, 199, 91, 0.22)", "--mcel-theme-muted": "#b9b28d", "--mcel-theme-success": "#87e6a9", "--mcel-theme-warning": "#ffda72"}),
        "theme-local": Object.freeze({"--mcel-theme-accent": "#2d7a4f", "--mcel-theme-accent-2": "#d77a2d", "--mcel-theme-card": "#fffaf0", "--mcel-theme-error": "#a62b3d", "--mcel-theme-ink": "#1b2118", "--mcel-theme-line": "rgba(47, 80, 48, 0.24)", "--mcel-theme-muted": "#647158", "--mcel-theme-success": "#146b42", "--mcel-theme-warning": "#855300"}),
        "theme-saas": Object.freeze({"--mcel-theme-accent": "#64e4ff", "--mcel-theme-accent-2": "#9d7cff", "--mcel-theme-card": "rgba(18, 24, 48, 0.92)", "--mcel-theme-error": "#ff909b", "--mcel-theme-ink": "#f6f8ff", "--mcel-theme-line": "rgba(127, 157, 255, 0.32)", "--mcel-theme-muted": "#aab5d6", "--mcel-theme-success": "#76efb5", "--mcel-theme-warning": "#ffe084"}),
        "theme-editorial": Object.freeze({"--mcel-theme-accent": "#9b3f2c", "--mcel-theme-accent-2": "#1f5a68", "--mcel-theme-card": "#fffdf7", "--mcel-theme-error": "#aa3542", "--mcel-theme-ink": "#251f17", "--mcel-theme-line": "rgba(45, 35, 22, 0.22)", "--mcel-theme-muted": "#736450", "--mcel-theme-success": "#246b4b", "--mcel-theme-warning": "#835000"}),
        "theme-luxury": Object.freeze({"--mcel-theme-accent": "#d6b56d", "--mcel-theme-accent-2": "#8e6f41", "--mcel-theme-card": "#15110d", "--mcel-theme-error": "#ffa0a0", "--mcel-theme-ink": "#fbf3df", "--mcel-theme-line": "rgba(214, 181, 109, 0.34)", "--mcel-theme-muted": "#b9aa88", "--mcel-theme-success": "#94e3ad", "--mcel-theme-warning": "#ebce84"}),
        "theme-civic": Object.freeze({"--mcel-theme-accent": "#075da8", "--mcel-theme-accent-2": "#1b7b6d", "--mcel-theme-card": "#ffffff", "--mcel-theme-error": "#aa2a46", "--mcel-theme-ink": "#132538", "--mcel-theme-line": "rgba(7, 93, 168, 0.24)", "--mcel-theme-muted": "#51677d", "--mcel-theme-success": "#166e4c", "--mcel-theme-warning": "#895400"}),
        "theme-accessible": Object.freeze({"--mcel-theme-accent": "#00e5ff", "--mcel-theme-accent-2": "#ff7a00", "--mcel-theme-card": "#000000", "--mcel-theme-error": "#ff9393", "--mcel-theme-ink": "#ffffff", "--mcel-theme-line": "#ffffff", "--mcel-theme-muted": "#ffffff", "--mcel-theme-success": "#7bff9a", "--mcel-theme-warning": "#ffe04b"}),
        "theme-debug": Object.freeze({"--mcel-theme-accent": "#005cff", "--mcel-theme-accent-2": "#ff3b30", "--mcel-theme-card": "rgba(255,255,255,0.84)", "--mcel-theme-error": "#a72d3d", "--mcel-theme-ink": "#101010", "--mcel-theme-line": "#005cff", "--mcel-theme-muted": "#313131", "--mcel-theme-success": "#176b46", "--mcel-theme-warning": "#815000"}),
      });
      function themePaletteFor(theme = "default") {
        const id = theme === "default" ? "theme-machine" :
          themes.includes(theme) ? theme : themeAliases[theme];
        return themeColorPalettes[id] || null;
      }
      const roleSet = new Set(semanticColorRoles);
      const stateSet = new Set(semanticColorStates);
      const surfaceSet = new Set(semanticColorSurfaces);
      const cssValue = value => typeof value === "string" && value.trim() &&
        !/[{};<>]/.test(value) ? value.trim() : "";

      // Get *the existing Theme CSS palette*, not a separately maintained
      // list of RGB values. No component colors are chosen here.
      function semanticPaletteFor(element, getStyle) {
        if (!element || typeof getStyle !== "function") return null;
        const computed = getStyle(element);
        const palette = {};
        for (const name of semanticPaletteTokens) {
          const value = cssValue(computed?.getPropertyValue?.(name));
          if (!value) return null;
          palette[name] = value;
        }
        return Object.freeze(palette);
      }

      function resolveColor({theme = "theme-machine", role, state = "enabled",
          surface = "indicator", palette = null} = {}) {
        // Do not silently paint a wrongly spelled meaning as ordinary text.
        const requested = String(theme || "theme-machine");
        const canonical = themes.includes(requested) ? requested : themeAliases[requested];
        const refuse = reason => Object.freeze({schema:semanticColorSchema,
          status:"unresolved",theme:requested,role,state,surface,reason});
        if (!themes.includes(canonical)) return refuse("unknown-theme");
        if (!roleSet.has(role)) return refuse("unknown-role");
        if (!stateSet.has(state)) return refuse("unknown-state");
        if (!surfaceSet.has(surface)) return refuse("unknown-surface");
        if (palette !== null && (typeof palette !== "object" ||
            semanticPaletteTokens.some(name => !cssValue(palette[name]))))
          return refuse("incomplete-theme-palette");
        const token = name => palette === null ? `var(${name})` : cssValue(palette[name]);
        const card = token("--mcel-theme-card"), ink = token("--mcel-theme-ink");
        const muted = token("--mcel-theme-muted"), line = token("--mcel-theme-line");
        const accent = token("--mcel-theme-accent"), second = token("--mcel-theme-accent-2");
        const error = token("--mcel-theme-error"), warning = token("--mcel-theme-warning");
        const success = token("--mcel-theme-success");
        const mix = (base, tint, strength) => `color-mix(in srgb, ${base} ${100 - strength}%, ${tint} ${strength}%)`;
        const severity = role === "diagnostic.error" ? error :
          role === "diagnostic.warning" ? warning :
          role === "diagnostic.success" ? success : null;
        const operation = ({"action.start":success,"action.enable":success,
          "action.stop":error,"action.disable":warning,
          "action.inspect":second,"action.generic":accent,
          "action.input":accent,"action.operator":second,
          "action.evaluate":success,"action.clear":warning,"action.reset":warning})[role] || null;
        const meaning = severity || operation || (role === "diagnostic.info" ? accent :
          role === "navigation.selected" ? accent : accent);
        let foreground = ink, background = card, border = line, colorAccent = meaning;
        // Muted labels must remain legible on nested semantic surfaces;
        // mix toward Theme ink rather than treating muted as an unchecked literal.
        if (role === "text.muted") foreground = mix(muted,ink,25);
        if (role === "surface.control" || role === "navigation.item")
          background = mix(card, accent, 10);
        if (role === "navigation.selected") {
          background = mix(card, accent, 18);
          border = accent;
        }
        if (severity || operation || role === "diagnostic.info") {
          // Distinct semantic signals even in a monochrome/limited palette.
          // The theme supplies the physical signal colors; apps supply meaning.
          foreground = (severity || role === "diagnostic.info" || surface === "text") ? meaning : ink;
          background = surface === "text" ? card : mix(card, meaning, 10);
          border = meaning;
        }
        if (surface === "text") background = card;
        if (state === "active" || state === "selected") {
          if (surface !== "text") background = mix(card, meaning, 20);
          border = meaning;
        } else if (state === "pending") {
          foreground = surface === "text" ? warning : ink;
          background = surface === "text" ? card : mix(card, warning, 12);
          border = warning;
          colorAccent = warning;
        } else if (state === "disabled" || state === "unavailable") {
          foreground = muted;
          background = card;
          border = line;
          colorAccent = muted;
        }
        return Object.freeze({schema:semanticColorSchema,status:"resolved",theme:canonical,
          role,state,surface,foreground,background,border,accent:colorAccent});
      }

      // Color Law v2: pure *complete visual-state* resolution. This is an
      // additive planning API: the existing v1 resolveColor and all live
      // consumers remain unchanged until a separate controlled migration.
      // Transient input is compiled to native CSS pseudo-state rules rather
      // than causing any mouse/keyboard JavaScript paint loop.
      const semanticVisualStateSchema = "mcel.semantic-color-treatment.v2";
      const semanticVisualStates = Object.freeze({
        availability:Object.freeze(["available", "disabled", "unavailable"]),
        consequence:Object.freeze(["unspecified", "routine", "reversible", "destructive", "critical"]),
        selection:Object.freeze(["off", "on", "mixed"]),
        operation:Object.freeze(["idle", "pending", "succeeded", "failed"]),
        context:Object.freeze(["default", "toolbar", "dialog", "form", "navigation", "nested", "inline"]),
        contrast:Object.freeze(["normal", "more"]),
        colorScheme:Object.freeze(["auto", "light", "dark"])
      });
      const visualInteractionKeys = Object.freeze(["hovered", "pressed", "focused", "focusVisible"]);
      const visualEnvironmentKeys = Object.freeze(["contrast", "colorScheme", "forcedColors", "reducedMotion"]);
      const visualInteractionDefault = Object.freeze({hovered:false,pressed:false,focused:false,focusVisible:false});
      const visualEnvironmentDefault = Object.freeze({contrast:"normal",colorScheme:"auto",forcedColors:false,reducedMotion:false});
      const formColorChannel = (base,tint,strength) =>
        `color-mix(in srgb, ${base} ${100-strength}%, ${tint} ${strength}%)`;
      // Contrast proof needs only the concrete Theme tokens (hex / CSS rgb[a]).
      // All eight shipped palettes use those formats. Unknown formats are
      // unproven, never silently treated as readable diagnostic foregrounds.
      const parseSemanticRgb = color => {
        const value=String(color||"").trim();
        const hex=value.match(/^#([0-9a-f]{6})$/i);
        if (hex) return Object.freeze({rgb:[0,2,4].map(i=>parseInt(hex[1].slice(i,i+2),16)/255),a:1});
        const css=value.match(/^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*(0(?:\.\d+)?|1(?:\.0+)?))?\s*\)$/i);
        if (!css) return null;
        const rgb=css.slice(1,4).map(n=>Number(n)/255),a=css[4]===undefined?1:Number(css[4]);
        return rgb.every(n=>n>=0&&n<=1)&&a>=0&&a<=1?Object.freeze({rgb,a}):null;
      };
      const semanticLuminance = rgb => rgb.map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4)
        .reduce((total,n,i)=>total+n*[.2126,.7152,.0722][i],0);
      const contrastRatio = (a,b) => {
        const la=semanticLuminance(a),lb=semanticLuminance(b);
        return (Math.max(la,lb)+.05)/(Math.min(la,lb)+.05);
      };
      const compositeOn = (color,background) => color.rgb.map((v,i)=>v*color.a+background[i]*(1-color.a));
      const mixSemanticRgba = (first,second,secondPercent) => {
        const p=secondPercent/100,alpha=first.a*(1-p)+second.a*p;
        if (!alpha) return {rgb:[0,0,0],a:0};
        return {a:alpha,rgb:first.rgb.map((n,i)=>
          (n*first.a*(1-p)+second.rgb[i]*second.a*p)/alpha)};
      };
      function semanticTextContrastProven(text,card,tint,strength) {
        const foreground=parseSemanticRgb(text),back=parseSemanticRgb(card),color=parseSemanticRgb(tint);
        if (!foreground||!back||!color) return false;
        const mixed=mixSemanticRgba(back,color,strength);
        // Transparent theme cards can sit over either a light or dark host.
        // The worst of both is the safe lower bound absent a host substrate.
        return [[0,0,0],[1,1,1]].every(underlay=>
          contrastRatio(compositeOn(foreground,compositeOn(mixed,underlay)),
            compositeOn(mixed,underlay)) >= 4.65);
      }
      const valueKeysMatch = (input,allowed) => !Object.keys(input).some(key=>!allowed.includes(key));
      const semanticRoleIsAction = role => typeof role === "string" && role.startsWith("action.");
      // Interaction is a property of semantic items, not DOM containment.
      // CSS :hover also matches an ancestor when hovering its descendants;
      // panels and navigation groups must never acquire an item's treatment.
      const semanticRoleIsInteractive = role => semanticRoleIsAction(role) ||
        role === "navigation.item" || role === "navigation.selected" || role === "surface.control";

      function resolveColorTreatment(options = {}) {
        const schema = semanticVisualStateSchema;
        const refuse = reason => Object.freeze({schema,status:"unresolved",reason});
        if (!options || typeof options !== "object" || Array.isArray(options)) return refuse("invalid-input");
        const accepted = ["theme","role","surface","palette","consequence","availability",
          "selection","operation","context","interaction","environment"];
        if (!valueKeysMatch(options,accepted)) return refuse("unknown-input-dimension");
        const {theme="theme-machine",role,surface="control",palette=null,
          consequence="unspecified",availability="available",selection="off",
          operation="idle",context="default"} = options;
        const interaction = {...visualInteractionDefault,...(options.interaction || {})};
        const environment = {...visualEnvironmentDefault,...(options.environment || {})};
        if (options.interaction !== undefined && (options.interaction === null ||
            typeof options.interaction !== "object" || Array.isArray(options.interaction)))
          return refuse("invalid-interaction");
        if (options.environment !== undefined && (options.environment === null ||
            typeof options.environment !== "object" || Array.isArray(options.environment)))
          return refuse("invalid-environment");
        if (!valueKeysMatch(interaction,visualInteractionKeys)) return refuse("unknown-interaction-dimension");
        if (!valueKeysMatch(environment,visualEnvironmentKeys)) return refuse("unknown-environment-dimension");
        for (const key of visualInteractionKeys) if (typeof interaction[key] !== "boolean")
          return refuse(`invalid-interaction-${key}`);
        for (const key of ["forcedColors","reducedMotion"]) if (typeof environment[key] !== "boolean")
          return refuse(`invalid-environment-${key}`);
        for (const [name,value] of [["availability",availability],["consequence",consequence],
          ["selection",selection],["operation",operation],["context",context],
          ["contrast",environment.contrast],["colorScheme",environment.colorScheme]]) {
          if (!semanticVisualStates[name].includes(value)) return refuse(`unknown-${name}`);
        }
        const canonicalBase=resolveColor({theme,role,surface,palette,state:"enabled"});
        if (canonicalBase.status !== "resolved") return refuse(canonicalBase.reason);
        if (interaction.focusVisible && !interaction.focused)
          return refuse("focus-visible-without-focus");
        if (interaction.pressed && availability !== "available")
          return refuse("pressed-unavailable-control");
        if (!semanticRoleIsAction(role) && consequence !== "unspecified")
          return refuse("consequence-on-non-action");
        if (selection !== "off" && !["control","navigation","indicator"].includes(surface))
          return refuse("selection-on-nonselectable-surface");
        if (role === "navigation.group" && selection !== "off")
          return refuse("selection-on-navigation-group");

        const token = name => palette === null ? `var(${name})` : cssValue(palette[name]);
        const card=token("--mcel-theme-card"),ink=token("--mcel-theme-ink"),
          muted=token("--mcel-theme-muted"),line=token("--mcel-theme-line"),
          accent=token("--mcel-theme-accent"),error=token("--mcel-theme-error"),
          warning=token("--mcel-theme-warning"),success=token("--mcel-theme-success");
        // Risk is independent of action name. In particular, "stop" is NOT
        // presumed destructive: explicit consequence is authoritative.
        const riskColor = consequence === "critical" || consequence === "destructive" ? error : null;
        // Action purpose is an independent semantic axis. A routine Stop is
        // not automatically destructive, but it must not become the same
        // meaning as Start merely because both are executable controls.
        const meaningColor = semanticRoleIsAction(role) ? (riskColor || canonicalBase.accent) : canonicalBase.accent;
        const statusColor = operation === "pending" ? warning :
          operation === "succeeded" ? success : operation === "failed" ? error : null;
        const isSelectable=selection !== "off";
        const isBlocked=availability !== "available";
        const interactive=!isBlocked && semanticRoleIsInteractive(role) &&
          (surface === "control" || surface === "navigation" || surface === "indicator");
        const pointer = interactive ? (interaction.pressed ? "pressed" : interaction.hovered ? "hovered" : "rest") : "rest";
        const stronger=environment.contrast === "more";
        const baseStrength = consequence === "critical" ? 20 :
          context === "toolbar" || context === "inline" ? 6 : context === "nested" ? 12 : 10;
        // Stateful emphasis composes, not overrides: selected+hover must
        // differ from selected-at-rest, and selected+press must differ from
        // pressed-at-rest. A bounded sum avoids an exponential combination
        // table while keeping independent focus/status channels intact.
        const selectionStrength = selection === "on" ? 14 : selection === "mixed" ? 7 : 0;
        const pointerStrength = pointer === "pressed" ? 24 : pointer === "hovered" ? 8 : 0;
        const signalStrength = statusColor ? (operation === "failed" ? 8 : 4) : 0;
        const strength = role === "navigation.group" ? 0 :
          Math.min(36,baseStrength+selectionStrength+pointerStrength+signalStrength);
        // Preserve a diagnostic's meaning-colored text only if its rendered
        // foreground passes WCAG normal-text contrast on *both* possible
        // backdrops of a translucent Theme card. Otherwise use Theme ink;
        // the separate semantic status and border channels retain meaning.
        const backgroundTint=statusColor && operation === "failed" ? statusColor : meaningColor;
        const appliedStrength=surface === "text" ? 0 : strength;
        let foreground=canonicalBase.foreground;
        if (role.startsWith("diagnostic.") &&
            !semanticTextContrastProven(foreground,card,backgroundTint,appliedStrength))
          foreground=ink;
        let background=surface === "text" ? card : formColorChannel(card,backgroundTint,strength);
        let border=isSelectable ? meaningColor : statusColor ||
          (semanticRoleIsAction(role) && riskColor ? riskColor : canonicalBase.border);
        let outlineColor=interaction.focusVisible && interactive ? ink : "transparent";
        let visualAccent=meaningColor;
        let selectionColor=isSelectable ? meaningColor : "transparent";
        let signalColor=statusColor || (role.startsWith("diagnostic.") ? meaningColor : "transparent");
        if (pointer === "pressed" && isSelectable) border=meaningColor;
        if (stronger && !isBlocked) {
          border=isSelectable ? meaningColor : ink;
          // A high-contrast focus ring remains an independent channel.
          if (interaction.focusVisible && interactive) outlineColor=ink;
        }
        if (isBlocked) {
          foreground=muted;
          background=card;
          border=line;
          visualAccent=muted;
          // Selection and operational status remain declared; blocking an
          // action must not erase a pending result or selection meaning.
        }
        if (environment.forcedColors) {
          // The platform controls physical colors in forced-colors mode;
          // semantic status/selection survive as independent channels.
          foreground=isBlocked ? "GrayText" : "CanvasText";
          background="Canvas";
          border=isBlocked ? "GrayText" : isSelectable ? "Highlight" : "CanvasText";
          visualAccent=isBlocked ? "GrayText" : "Highlight";
          outlineColor=interaction.focusVisible && interactive ? "Highlight" : "transparent";
          selectionColor=isSelectable ? "Highlight" : "transparent";
          signalColor=statusColor ? "Highlight" : signalColor === "transparent" ? "transparent" : "Highlight";
        }
        const channels=Object.freeze({
          foreground,background,border,accent:visualAccent,
          focusRing:outlineColor,selection:selectionColor,status:signalColor
        });
        const semantics=Object.freeze({role,surface,consequence,availability,selection,operation,context});
        const applied=Object.freeze({pointer,focusVisible:interaction.focusVisible,
          selected:isSelectable,operating:operation !== "idle",blocked:isBlocked,
          forcedColors:environment.forcedColors,reducedMotion:environment.reducedMotion});
        return Object.freeze({schema,status:"resolved",theme:canonicalBase.theme,
          semantics,interaction:Object.freeze({...interaction}),
          environment:Object.freeze({...environment}),applied,channels});
      }

      // A single pure compile step produces the full native browser interaction
      // matrix. Only semantic or operational state changes require recompiling;
      // :hover, :active and :focus-visible never cause JS style mutations.
      function compileColorStateCss({id, important = false, ...options} = {}) {
        const schema="mcel.semantic-color-css.v1";
        const refuse=reason=>Object.freeze({schema,status:"unresolved",reason});
        if (typeof id !== "string" || !/^[a-z][a-z0-9-]{0,63}$/.test(id)) return refuse("invalid-style-identity");
        if (typeof important !== "boolean") return refuse("invalid-cascade-priority");
        // CSS emission is a paint-capable output and requires verifiable
        // *computed* palette colors, not unresolved var() placeholders or
        // arbitrary CSS strings. Pure v1 and v2 queries remain available
        // without a palette for observational planning only.
        if (!options.palette) return refuse("computed-theme-palette-required");
        if (semanticPaletteTokens.some(name=>!parseSemanticRgb(options.palette[name])))
          return refuse("unverifiable-theme-palette");
        if (Object.prototype.hasOwnProperty.call(options,"interaction")) return refuse("interaction-is-css-owned");
        if (Object.prototype.hasOwnProperty.call(options,"environment")) return refuse("environment-is-css-owned");
        const selector=`[data-mcel-color-state="${id}"]`;
        // Noninteractive semantics compile a single resting treatment.
        // Do not emit :hover/:active/:focus-visible for ancestors of buttons.
        const phases=Object.freeze(semanticRoleIsInteractive(options.role) ? [
          Object.freeze({suffix:"",interaction:{}}),
          Object.freeze({suffix:":hover",interaction:{hovered:true}}),
          Object.freeze({suffix:":active",interaction:{pressed:true}}),
          Object.freeze({suffix:":hover:active",interaction:{hovered:true,pressed:true}}),
          Object.freeze({suffix:":focus-visible",interaction:{focused:true,focusVisible:true}}),
          Object.freeze({suffix:":hover:focus-visible",interaction:{hovered:true,focused:true,focusVisible:true}}),
          Object.freeze({suffix:":active:focus-visible",interaction:{pressed:true,focused:true,focusVisible:true}}),
          Object.freeze({suffix:":hover:active:focus-visible",interaction:{hovered:true,pressed:true,focused:true,focusVisible:true}})
        ] : [Object.freeze({suffix:"",interaction:{}})]);
        const build=(environment,media="")=>{
          const entries=[],hoverEntries=[];
          for (const phase of phases) {
            // Disabled elements do not produce pressed/hover paint, even if
            // synthetic or stale DOM flags would otherwise match.
            const resolved=resolveColorTreatment({...options,interaction:phase.interaction,environment});
            if (resolved.status !== "resolved") {
              if (resolved.reason === "pressed-unavailable-control" &&
                  options.availability && options.availability !== "available") continue;
              return {error:resolved.reason};
            }
            const ch=resolved.channels;
            const declarations=[
              `color:${ch.foreground}`,`background-color:${options.surface === "text" ? "transparent" : ch.background}`,
              `border-color:${ch.border}`,`outline-color:${ch.focusRing}`,
              `--mcel-color-accent:${ch.accent}`,`--mcel-color-selection:${ch.selection}`,
              `--mcel-color-status:${ch.status}`
            ];
            // Migrated controls may be embedded in incumbent important CSS.
            // Caller opts into paint-only important declarations inside its
            // own scoped cascade layer; unmodified consumers remain normal.
            const rule=`${selector}${phase.suffix}{${declarations.map(part =>
              important ? `${part}!important` : part).join(";")};}`;
            if (phase.suffix.includes(":hover")) hoverEntries.push(rule);
            else entries.push(rule);
          }
          // Coarse touch devices must not retain a sticky hover appearance.
          // Press remains native :active; focus-visible remains available
          // for keyboard users regardless of pointer capabilities.
          const hoverCss=`@media (hover: hover) and (pointer: fine){${hoverEntries.join("\n")}}`;
          const css=[entries.join("\n"),hoverCss].join("\n");
          return {css:media ? `@media ${media}{${css}}` : css};
        };
        const standard=build(visualEnvironmentDefault);
        if (standard.error) return refuse(standard.error);
        const highContrast=build({...visualEnvironmentDefault,contrast:"more"},"(prefers-contrast: more)");
        if (highContrast.error) return refuse(highContrast.error);
        const forced=build({...visualEnvironmentDefault,forcedColors:true},"(forced-colors: active)");
        if (forced.error) return refuse(forced.error);
        return Object.freeze({schema,status:"resolved",id,selector,
          css:[standard.css,highContrast.css,forced.css].join("\n"),
          phases:Object.freeze(phases.map(item=>item.suffix || "rest")),
          transientMechanism:"native-css",paintWrites:0});
      }

      // Color Law v3: one canonical *authored* declaration. This record
      // contains semantic meaning, never literal colors, selectors, labels,
      // media queries or style declarations. A consumer cannot invent a role.
      const semanticColorDeclarationSchema = "mcel.semantic-color-declaration.v1";
      const semanticColorKinds = Object.freeze(["control", "form", "region", "text", "plot", "evidence", "embedded"]);
      const semanticColorDeclarationKeys = Object.freeze([
        "role", "surface", "context", "consequence", "selection", "operation"
      ]);
      function validateColorDeclaration(declaration, kind) {
        const reject = reason => Object.freeze({schema:semanticColorDeclarationSchema,status:"unresolved",reason});
        if (!semanticColorKinds.includes(kind)) return reject("unknown-element-kind");
        if (!declaration || typeof declaration !== "object" || Array.isArray(declaration))
          return reject("missing-color-declaration");
        if (!valueKeysMatch(declaration,semanticColorDeclarationKeys))
          return reject("unknown-color-declaration-field");
        const {role,surface,context="default",consequence="unspecified",selection="off",
          operation="idle"} = declaration;
        if (!roleSet.has(role)) return reject("unknown-role");
        if (!surfaceSet.has(surface)) return reject("unknown-surface");
        for (const [key,value] of [["context",context],["consequence",consequence],
            ["selection",selection],["operation",operation]])
          if (!semanticVisualStates[key].includes(value)) return reject(`unknown-${key}`);
        if ((kind === "control" || kind === "form") &&
            !(surface === "control" || surface === "navigation")) return reject("control-surface-required");
        if (kind === "region" && !(surface === "panel" || surface === "navigation"))
          return reject("region-surface-required");
        if (kind === "region" && semanticRoleIsInteractive(role))
          return reject("interactive-role-on-region");
        if (kind === "text" && !(surface === "text" || surface === "indicator"))
          return reject("text-surface-required");
        if (kind === "plot" && surface !== "panel") return reject("plot-surface-required");
        if ((kind === "evidence" || kind === "embedded") && surface !== "panel")
          return reject("shell-surface-required");
        if (!role.startsWith("action.") && consequence !== "unspecified")
          return reject("consequence-on-non-action");
        if (selection !== "off" && !["control","navigation","indicator"].includes(surface))
          return reject("selection-on-nonselectable-surface");
        const candidate=resolveColorTreatment({role,surface,context,consequence,selection,operation});
        if (candidate.status !== "resolved") return reject(candidate.reason);
        return Object.freeze({schema:semanticColorDeclarationSchema,status:"declared",
          kind,semantic:Object.freeze({role,surface,context,consequence,selection,operation})});
      }
      function resolveDeclaredColor({declaration,kind,theme="default",palette,
          availability="available",selection,operation,interaction,environment}={}) {
        const declared=validateColorDeclaration(declaration,kind);
        if (declared.status !== "declared") return declared;
        const semantic=declared.semantic;
        return resolveColorTreatment({theme,palette,...semantic,availability,
          selection:selection===undefined?semantic.selection:selection,
          operation:operation===undefined?semantic.operation:operation,
          ...(interaction===undefined?{}:{interaction}),
          ...(environment===undefined?{}:{environment})});
      }
      function compileDeclaredColor({declaration,kind,theme="default",palette,
          availability="available",selection,operation,id,important=true}={}) {
        const declared=validateColorDeclaration(declaration,kind);
        if (declared.status !== "declared") return declared;
        const semantic=declared.semantic;
        return compileColorStateCss({id,important,theme,palette,...semantic,availability,
          selection:selection===undefined?semantic.selection:selection,
          operation:operation===undefined?semantic.operation:operation});
      }

      const densityScale = Object.freeze({
        auto: "1",
        calm: "0.82",
        dense: "1.18",
        compressed: "1.36"
      });

      const statePulse = Object.freeze({
        idle: "0",
        draft: "0.35",
        live: "1",
        warning: "1.45"
      });

      const rankWeight = Object.freeze({
        primary: "900",
        secondary: "750",
        minor: "600"
      });

      function normalizeTheme(theme) {
        const candidate = String(theme || "").trim();
        if (themes.includes(candidate)) return candidate;
        const normalized = candidate.toLowerCase();
        const alias = themeAliases[normalized] || themeAliases[candidate];
        return themes.includes(alias) ? alias : "theme-machine";
      }

      function themeDefinition(theme) {
        const normalized = normalizeTheme(theme);
        return themeDefinitions[normalized] || themeCatalog.find((item) => item.id === normalized) || themeDefinitions["theme-machine"];
      }

      function themeLabel(theme) {
        return themeDefinition(theme).label;
      }

      function sourceElements(root) {
        return [...(root?.querySelectorAll?.(`[${attributes.type}]`) || [])];
      }

      function attr(element, name, fallback = "") {
        return String(element?.getAttribute?.(name) || fallback).trim() || fallback;
      }

      function wordList(element) {
        return attr(element, attributes.words)
          .split(/\s+/)
          .map((word) => word.trim())
          .filter(Boolean);
      }

      function computeElementLaw(element, index = 0, total = 1) {
        const kind = attr(element, attributes.kind, defaults.kind);
        const flow = attr(element, attributes.flow, defaults.flow);
        const rank = attr(element, attributes.rank, defaults.rank);
        const state = attr(element, attributes.state, defaults.state);
        const density = attr(element, attributes.computedDensity, attr(element, attributes.density, defaults.density));
        const neighborhood = attr(element, attributes.neighborhood, "isolated");
        const relation = attr(element, attributes.relation, "none");
        const relationCount = Number(attr(element, attributes.relationCount, "0")) || 0;
        const words = wordList(element);
        const strength = Math.min(words.length + relationCount + (rank === "primary" ? 2 : 0), 12);

        return {
          index,
          type: attr(element, attributes.type, defaults.type),
          kind,
          flow,
          rank,
          state,
          density,
          neighborhood,
          relation,
          relationCount,
          words,
          tokens: {
            "--mc-density-scale": densityScale[density] || densityScale.auto,
            "--mc-state-pulse": statePulse[state] || statePulse.idle,
            "--mc-rank-weight": rankWeight[rank] || rankWeight.secondary,
            "--mc-relation-count": String(relationCount),
            "--mc-word-count": String(words.length),
            "--mc-cluster-index": String(index + 1),
            "--mc-cluster-total": String(Math.max(total, 1)),
            "--mc-intent-strength": String(strength)
          },
          hooks: {
            flowAxis: flow === "reverse" ? "reverse-inline" : flow === "stack" ? "block" : flow === "split" ? "split" : "inline",
            fieldPressure: density === "compressed" ? "high" : density === "dense" ? "medium" : "low",
            attention: state === "live" || state === "warning" ? "active" : "quiet",
            relationMode: relationCount > 0 ? relation : "none"
          }
        };
      }

      function applyElementLaw(element, law) {
        Object.entries(law.tokens).forEach(([name, value]) => {
          element.style.setProperty(name, value);
        });
        element.setAttribute(attributes.styleLaw, "true");
        element.setAttribute(attributes.flowAxis, law.hooks.flowAxis);
        element.setAttribute(attributes.fieldPressure, law.hooks.fieldPressure);
        element.setAttribute(attributes.attention, law.hooks.attention);
        element.setAttribute(attributes.relationMode, law.hooks.relationMode);
      }

      function applyRuntimeLaw(root, options = {}) {
        const theme = normalizeTheme(options.theme);
        const elements = sourceElements(root);
        if (root?.classList) {
          themes.forEach((name) => root.classList.remove(name));
          root.classList.add(theme);
          root.setAttribute(attributes.theme, theme);
        }
        const elementReports = elements.map((element, index) => {
          const law = computeElementLaw(element, index, elements.length);
          applyElementLaw(element, law);
          return law;
        });
        return {
          theme,
          elementCount: elementReports.length,
          elements: elementReports,
          warnings: elementReports.length ? [] : ["No MCEL runtime elements were available for CSS law."],
          cssLawClean: true
        };
      }

      function reportFor(root, options = {}) {
        return applyRuntimeLaw(root, options);
      }

      if (registry?.register) {
        registry.register({
          id: "style.semantic-tokens.v1",
          label: "CSS Law Runtime",
          version: "v1",
          reads: [attributes.kind, attributes.flow, attributes.rank, attributes.state, attributes.density, attributes.words, attributes.connects],
          writesRuntimeOnly: [attributes.styleLaw, attributes.flowAxis, attributes.fieldPressure, attributes.attention, attributes.relationMode],
          sourcePollutionForbidden: true,
          compute: computeElementLaw,
          apply: applyRuntimeLaw,
          reportFor
        });
      }

      return Object.freeze({
        themes,
        themeAliases,
        themeCatalog,
        normalizeTheme,
        themeDefinition,
        themeLabel,
        semanticColorSchema, semanticColorRoles, semanticColorStates,
        semanticColorSurfaces, semanticPaletteTokens, semanticPaletteFor,
        themePaletteFor,themeColorPalettes,
        resolveColor,
        semanticVisualStateSchema, semanticVisualStates,
        resolveColorTreatment, compileColorStateCss,
        semanticColorDeclarationSchema,semanticColorKinds,semanticColorDeclarationKeys,
        validateColorDeclaration,resolveDeclaredColor,compileDeclaredColor,
        computeElementLaw,
        applyRuntimeLaw,
        reportFor
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabStyleLaw = McelLabStyleLaw;
    }
