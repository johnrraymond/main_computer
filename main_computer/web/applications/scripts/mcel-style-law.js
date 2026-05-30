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
        computeElementLaw,
        applyRuntimeLaw,
        reportFor
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabStyleLaw = McelLabStyleLaw;
    }
