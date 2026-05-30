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
      const CHROME_ID_ATTR = "data-mcel-chrome-id";
      const FIT_REGION_ATTR = "data-mcel-fit-region";
      const FIT_POLICY_ATTR = "data-mcel-fit-policy";
      const FIT_REMEDIATION_ATTR = "data-mcel-fit-remediation";
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
          restructuresHierarchy: false,
          fitContract: Object.freeze({
            observeSelectors: Object.freeze(["[data-mc]"]),
            tolerancePx: 2,
            genericFallback: false
          }),
          compositionContract: Object.freeze({
            observeSelectors: Object.freeze([]),
            warnings: Object.freeze([]),
            remedies: Object.freeze({})
          }),
          remediation: Object.freeze({
            strategies: Object.freeze([])
          })
        }),
        "chrome-editorial-flow": Object.freeze({
          id: "chrome-editorial-flow",
          label: "Editorial Flow",
          contractVersion: CONTRACT_VERSION,
          kind: "structural-render",
          description: "Realizes the same source as a magazine-like reading flow: lede, story body, and supporting action rail.",
          preservesPixelBaseline: false,
          restructuresHierarchy: true,
          fitContract: Object.freeze({
            observeSelectors: Object.freeze([
              "[data-mcel-chrome-generated=\"true\"]",
              "[data-mcel-fit-region]",
              "[data-mcel-fit-policy]",
              "[data-mc]"
            ]),
            hardObjectSelector: "img,svg,canvas,video,iframe,table,pre,code,input,textarea,select,button",
            tolerancePx: 2,
            genericFallback: true
          }),
          compositionContract: Object.freeze({
            observeSelectors: Object.freeze([
              ".mcel-chrome-editorial-rail > .mc",
              "[data-mcel-fit-region=\"narrow\"].mc",
              "[data-mcel-fit-region=\"narrow\"] > .mc"
            ]),
            warnings: Object.freeze([
              "primary-control-width-collapsed-relative-to-input"
            ]),
            remedies: Object.freeze({
              "primary-control-width-collapsed-relative-to-input": "control-balance"
            })
          }),
          remediation: Object.freeze({
            order: Object.freeze([
              "content-negotiate",
              "object-grow",
              "object-reshape",
              "region-reflow"
            ]),
            strategies: Object.freeze([
              Object.freeze({
                id: "content-negotiate",
                label: "Content negotiation",
                meaning: "Scale, wrap, and stack children inside the chrome-owned object before changing the object."
              }),
              Object.freeze({
                id: "object-grow",
                label: "Object growth",
                meaning: "Let the chrome-owned object claim more usable interior size when the shell can support it."
              }),
              Object.freeze({
                id: "object-reshape",
                label: "Object reshape",
                meaning: "Allow Editorial Flow to relax decorative shape tokens when content cannot fit the original shape."
              }),
              Object.freeze({
                id: "region-reflow",
                label: "Region reflow",
                meaning: "Move the supporting rail into normal flow as the chrome-level last resort."
              })
            ])
          })
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

      function chromeFitContract(chrome) {
        const definition = chromeDefinition(chrome);
        return definition.fitContract || {
          observeSelectors: ["[data-mc]"],
          hardObjectSelector: "img,svg,canvas,video,iframe,table,pre,code,input,textarea,select,button",
          tolerancePx: 2,
          genericFallback: false
        };
      }

      function chromeCompositionContract(chrome) {
        const definition = chromeDefinition(chrome);
        const contract = definition.compositionContract || {};
        return {
          observeSelectors: Array.isArray(contract.observeSelectors) ? [...contract.observeSelectors] : [],
          warnings: Array.isArray(contract.warnings) ? [...contract.warnings] : [],
          remedies: contract.remedies && typeof contract.remedies === "object" ? {...contract.remedies} : {}
        };
      }

      function chromeRemediationPlan(chrome) {
        const definition = chromeDefinition(chrome);
        const remediation = definition.remediation || {};
        const strategies = Array.isArray(remediation.strategies) ? remediation.strategies : [];
        return {
          chrome: definition.id,
          contractVersion: CONTRACT_VERSION,
          order: Array.isArray(remediation.order) ? [...remediation.order] : strategies.map((strategy) => strategy.id),
          strategies: strategies.map((strategy) => ({...strategy}))
        };
      }

      function fitMetadataForPart(part) {
        const map = {
          "editorial-shell": {region: "shell", policy: "chrome-remediates"},
          "editorial-lede": {region: "wide", policy: "contain"},
          "editorial-body": {region: "flow", policy: "contain"},
          "editorial-rail": {region: "narrow", policy: "contain"}
        };
        return map[part] || {region: "flow", policy: "contain"};
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
        const fit = fitMetadataForPart(part);
        element.setAttribute(CHROME_GENERATED_ATTR, "true");
        element.setAttribute(CHROME_PART_ATTR, part);
        element.setAttribute(CHROME_ATTR, chrome);
        element.setAttribute(CHROME_ID_ATTR, chrome);
        element.setAttribute(FIT_REGION_ATTR, fit.region);
        element.setAttribute(FIT_POLICY_ATTR, fit.policy);
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
        CHROME_ID_ATTR,
        FIT_REGION_ATTR,
        FIT_POLICY_ATTR,
        FIT_REMEDIATION_ATTR,
        chromes,
        chromeAliases,
        chromeCatalog,
        normalizeChrome,
        chromeDefinition,
        chromeLabel,
        chromeFitContract,
        chromeCompositionContract,
        chromeRemediationPlan,
        applyChromeHtml
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabChromeLaw = McelLabChromeLaw;
    }
