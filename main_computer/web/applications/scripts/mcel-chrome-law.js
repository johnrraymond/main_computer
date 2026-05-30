    var McelLabChromeLaw = (() => {
      const contract = typeof McelLabContract !== "undefined"
        ? McelLabContract
        : (typeof window !== "undefined" ? window.McelLabContract : null);
      const attributes = contract?.attributes || {
        type: "data-mc",
        kind: "data-mc-kind",
        generated: "data-mc-generated",
        part: "data-mc-part",
        componentName: "data-mc-component",
        componentKind: "data-mc-component-kind",
        artifactOwner: "data-mc-owner",
        artifactOrigin: "data-mc-origin",
        artifactReason: "data-mc-reason"
      };

      const CHROME_ATTR = "data-mcel-chrome";
      const CHROME_GENERATED_ATTR = "data-mcel-chrome-generated";
      const CHROME_PART_ATTR = "data-mcel-chrome-part";
      const CONTRACT_VERSION = "mcel.chrome.v1";

      const chromes = Object.freeze([
        "chrome-strict-hierarchy",
        "chrome-editorial-flow"
      ]);

      const chromeDefinitions = Object.freeze({
        "chrome-strict-hierarchy": Object.freeze({
          id: "chrome-strict-hierarchy",
          label: "Strict Hierarchy",
          contractVersion: CONTRACT_VERSION,
          kind: "structural-render",
          description: "Preserves the compiled MCEL hierarchy exactly; this is the baseline chrome and should not change current theme pixels.",
          preservesPixelBaseline: true,
          restructuresHierarchy: false
        }),
        "chrome-editorial-flow": Object.freeze({
          id: "chrome-editorial-flow",
          label: "Editorial Flow",
          contractVersion: CONTRACT_VERSION,
          kind: "structural-render",
          description: "Realizes the same source as a magazine-like reading flow: lede, story body, and supporting action rail.",
          preservesPixelBaseline: false,
          restructuresHierarchy: true
        })
      });

      const chromeAliases = Object.freeze({
        strict: "chrome-strict-hierarchy",
        hierarchy: "chrome-strict-hierarchy",
        "strict-hierarchy": "chrome-strict-hierarchy",
        "chrome-strict": "chrome-strict-hierarchy",
        editorial: "chrome-editorial-flow",
        magazine: "chrome-editorial-flow",
        article: "chrome-editorial-flow",
        "editorial-flow": "chrome-editorial-flow",
        "chrome-editorial": "chrome-editorial-flow"
      });

      const chromeCatalog = Object.freeze(chromes.map((id) => chromeDefinitions[id] || Object.freeze({
        id,
        label: id.replace(/^chrome-/, "").replace(/-/g, " "),
        contractVersion: CONTRACT_VERSION,
        kind: "structural-render",
        description: "Custom MCEL structural chrome"
      })));

      function normalizeChrome(chrome) {
        const candidate = String(chrome || "").trim();
        if (chromes.includes(candidate)) return candidate;
        const normalized = candidate.toLowerCase();
        const alias = chromeAliases[normalized] || chromeAliases[candidate];
        return chromes.includes(alias) ? alias : "chrome-strict-hierarchy";
      }

      function chromeDefinition(chrome) {
        const normalized = normalizeChrome(chrome);
        return chromeDefinitions[normalized] || chromeCatalog.find((item) => item.id === normalized) || chromeDefinitions["chrome-strict-hierarchy"];
      }

      function chromeLabel(chrome) {
        return chromeDefinition(chrome).label;
      }

      function smartChildren(element) {
        return [...(element?.children || [])].filter((child) =>
          child.nodeType === 1 &&
          child.getAttribute(attributes.generated) !== "true" &&
          child.getAttribute(CHROME_GENERATED_ATTR) !== "true" &&
          child.hasAttribute(attributes.type)
        );
      }

      function isGeneratedElement(element) {
        return element?.nodeType === 1 && (
          element.getAttribute(attributes.generated) === "true" ||
          element.getAttribute(CHROME_GENERATED_ATTR) === "true"
        );
      }

      function firstMeaningfulRoot(fragment) {
        const children = [...(fragment?.children || [])].filter((child) => child.nodeType === 1);
        return children.find((child) => child.hasAttribute(attributes.type)) || children[0] || null;
      }

      function generatedPart(part, chrome) {
        const element = document.createElement("div");
        element.className = `mcel-chrome-part mcel-chrome-${part}`;
        element.setAttribute(CHROME_GENERATED_ATTR, "true");
        element.setAttribute(CHROME_PART_ATTR, part);
        element.setAttribute(CHROME_ATTR, chrome);
        element.setAttribute(attributes.artifactOwner, "mcel-chrome-law");
        element.setAttribute(attributes.artifactOrigin, "chrome-runtime");
        element.setAttribute(attributes.artifactReason, `chrome:${chrome}:${part}`);
        return element;
      }

      function appendBucket(parent, part, children, chrome) {
        if (!children.length) return null;
        const bucket = generatedPart(part, chrome);
        children.forEach((child) => bucket.appendChild(child));
        parent.appendChild(bucket);
        return bucket;
      }

      function classifyEditorialChildren(children) {
        const hero = [];
        const body = [];
        const aside = [];
        children.forEach((child) => {
          const type = child.getAttribute(attributes.type) || "";
          const kind = child.getAttribute(attributes.kind) || "";
          const component = child.getAttribute(attributes.componentName) || "";
          const tagName = child.tagName || "";
          if (kind === "hero" || component === "HeroSection") {
            hero.push(child);
          } else if (
            tagName === "FORM" ||
            type === "command-row" ||
            component === "SignupForm" ||
            component === "FooterCta"
          ) {
            aside.push(child);
          } else {
            body.push(child);
          }
        });
        return {hero, body, aside};
      }

      function applyStrictHierarchyHtml(runtimeHtml, options = {}) {
        const chrome = normalizeChrome(options.chrome);
        return {
          html: String(runtimeHtml || ""),
          report: {
            contractVersion: CONTRACT_VERSION,
            chrome,
            label: chromeLabel(chrome),
            changed: false,
            generatedContainers: 0,
            movedSourceElements: 0,
            preservesPixelBaseline: true,
            visibleResponse: Boolean(String(runtimeHtml || "").trim()),
            warnings: []
          }
        };
      }

      function applyEditorialFlowHtml(runtimeHtml, options = {}) {
        const chrome = "chrome-editorial-flow";
        const sourceHtml = String(runtimeHtml || "");
        const report = {
          contractVersion: CONTRACT_VERSION,
          chrome,
          label: chromeLabel(chrome),
          changed: false,
          generatedContainers: 0,
          movedSourceElements: 0,
          preservesPixelBaseline: false,
          visibleResponse: Boolean(sourceHtml.trim()),
          warnings: []
        };

        if (!sourceHtml.trim()) {
          report.warnings.push("No runtime HTML was available for editorial flow.");
          return {html: sourceHtml, report};
        }
        if (typeof document === "undefined" || typeof document.createElement !== "function") {
          report.warnings.push("DOM APIs are unavailable; editorial flow fell back to strict hierarchy.");
          return {html: sourceHtml, report};
        }

        const template = document.createElement("template");
        template.innerHTML = sourceHtml;
        const root = firstMeaningfulRoot(template.content);
        if (!root) {
          report.warnings.push("No root element was found; editorial flow fell back to strict hierarchy.");
          return {html: sourceHtml, report};
        }

        const rootChildren = smartChildren(root);
        if (rootChildren.length < 2) {
          root.setAttribute(CHROME_ATTR, chrome);
          report.warnings.push("Not enough peer source children to restructure; editorial flow only marked the root.");
          return {html: template.innerHTML, report};
        }

        const generatedRuntimeParts = [...root.children].filter(isGeneratedElement);
        const {hero, body, aside} = classifyEditorialChildren(rootChildren);
        if (!hero.length && !body.length) {
          root.setAttribute(CHROME_ATTR, chrome);
          report.warnings.push("Editorial buckets were empty; editorial flow only marked the root.");
          return {html: template.innerHTML, report};
        }

        const shell = generatedPart("editorial-shell", chrome);
        root.setAttribute(CHROME_ATTR, chrome);
        root.innerHTML = "";
        generatedRuntimeParts.forEach((part) => root.appendChild(part));
        root.appendChild(shell);

        appendBucket(shell, "editorial-lede", hero, chrome);
        appendBucket(shell, "editorial-body", body, chrome);
        appendBucket(shell, "editorial-rail", aside, chrome);

        report.changed = true;
        report.generatedContainers = shell.querySelectorAll(`[${CHROME_GENERATED_ATTR}="true"]`).length + 1;
        report.movedSourceElements = hero.length + body.length + aside.length;
        return {html: template.innerHTML, report};
      }

      function applyChromeHtml(runtimeHtml, options = {}) {
        const chrome = normalizeChrome(options.chrome);
        if (chrome === "chrome-editorial-flow") return applyEditorialFlowHtml(runtimeHtml, {...options, chrome});
        return applyStrictHierarchyHtml(runtimeHtml, {...options, chrome});
      }

      return Object.freeze({
        CONTRACT_VERSION,
        CHROME_ATTR,
        CHROME_GENERATED_ATTR,
        CHROME_PART_ATTR,
        chromes,
        chromeAliases,
        chromeCatalog,
        normalizeChrome,
        chromeDefinition,
        chromeLabel,
        applyChromeHtml
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabChromeLaw = McelLabChromeLaw;
    }
