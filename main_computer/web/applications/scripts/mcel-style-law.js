    const McelLabStyleLaw = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const {attributes, defaults, themes: contractThemes} = contract;

      const themes = Object.freeze(contractThemes || [
        "theme-basic",
        "theme-machine",
        "theme-article",
        "theme-debug",
        "theme-accessibility"
      ]);

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
        return themes.includes(candidate) ? candidate : "theme-machine";
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

      return Object.freeze({
        themes,
        normalizeTheme,
        computeElementLaw,
        applyRuntimeLaw,
        reportFor
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabStyleLaw = McelLabStyleLaw;
    }
