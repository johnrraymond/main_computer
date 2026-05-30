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
      const CHROME_FRAME_ATTR = "data-mcel-chrome-frame";
      const CHROME_REGION_ROLE_ATTR = "data-mcel-chrome-region-role";
      const FIT_REGION_ATTR = "data-mcel-fit-region";
      const FIT_POLICY_ATTR = "data-mcel-fit-policy";
      const FIT_REMEDIATION_ATTR = "data-mcel-fit-remediation";
      const CONTRACT_VERSION = "mcel.chrome.v1";

      const chromes = Object.freeze([
        "chrome-strict-hierarchy",
        "chrome-editorial-flow",
        "chrome-cluster-grid",
        "chrome-spotlight",
        "chrome-journey",
        "chrome-compact-disclosure"
      ]);

      const commonHardObjectSelector = "img,svg,canvas,video,iframe,table,pre,code,input,textarea,select,button";
      const structuralObserveSelectors = Object.freeze([
        "[data-mcel-chrome-generated=\"true\"]",
        "[data-mcel-fit-region]",
        "[data-mcel-fit-policy]",
        "[data-mc]"
      ]);

      function chromeFitContractDefinition({genericFallback = true} = {}) {
        return Object.freeze({
          observeSelectors: structuralObserveSelectors,
          hardObjectSelector: commonHardObjectSelector,
          tolerancePx: 2,
          genericFallback
        });
      }

      function chromeCompositionContractDefinition(selectors, warnings, remedies) {
        return Object.freeze({
          observeSelectors: Object.freeze(selectors),
          warnings: Object.freeze(warnings),
          remedies: Object.freeze(remedies)
        });
      }

      function chromeRemediationDefinition(order, strategies) {
        return Object.freeze({
          order: Object.freeze(order),
          strategies: Object.freeze(strategies.map((strategy) => Object.freeze(strategy)))
        });
      }

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
          compositionContract: chromeCompositionContractDefinition([], [], {}),
          remediation: chromeRemediationDefinition([], [])
        }),
        "chrome-editorial-flow": Object.freeze({
          id: "chrome-editorial-flow",
          label: "Editorial Flow",
          contractVersion: CONTRACT_VERSION,
          kind: "structural-render",
          description: "Realizes the same source as a magazine-like reading flow: lede, story body, and supporting action rail.",
          preservesPixelBaseline: false,
          restructuresHierarchy: true,
          fitContract: chromeFitContractDefinition(),
          compositionContract: chromeCompositionContractDefinition(
            [
              ".mcel-chrome-editorial-rail > .mc",
              "[data-mcel-fit-region=\"narrow\"].mc",
              "[data-mcel-fit-region=\"narrow\"] > .mc"
            ],
            [
              "primary-control-width-collapsed-relative-to-input",
              "shape-interior-escape"
            ],
            {
              "primary-control-width-collapsed-relative-to-input": "control-balance",
              "shape-interior-escape": "shape-inset-content"
            }
          ),
          remediation: chromeRemediationDefinition(
            [
              "content-negotiate",
              "object-grow",
              "object-reshape",
              "region-reflow"
            ],
            [
              {
                id: "content-negotiate",
                label: "Content negotiation",
                meaning: "Scale, wrap, and stack children inside the chrome-owned object before changing the object."
              },
              {
                id: "object-grow",
                label: "Object growth",
                meaning: "Let the chrome-owned object claim more usable interior size when the shell can support it."
              },
              {
                id: "object-reshape",
                label: "Object reshape",
                meaning: "Allow Editorial Flow to relax decorative shape tokens when content cannot fit the original shape."
              },
              {
                id: "region-reflow",
                label: "Region reflow",
                meaning: "Move the supporting rail into normal flow as the chrome-level last resort."
              }
            ]
          )
        }),
        "chrome-cluster-grid": Object.freeze({
          id: "chrome-cluster-grid",
          label: "Cluster Grid",
          contractVersion: CONTRACT_VERSION,
          kind: "peer-cluster-render",
          description: "Realizes peer children as a designed responsive card cluster with an optional intro region.",
          preservesPixelBaseline: false,
          restructuresHierarchy: true,
          fitContract: chromeFitContractDefinition(),
          compositionContract: chromeCompositionContractDefinition(
            [
              ".mcel-chrome-cluster-grid > [data-mcel-chrome-frame]",
              "[data-mcel-fit-region=\"grid-body\"] > .mc"
            ],
            [
              "shape-interior-escape"
            ],
            {
              "shape-interior-escape": "shape-inset-content"
            }
          ),
          remediation: chromeRemediationDefinition(
            [
              "content-negotiate",
              "object-grow",
              "region-reflow"
            ],
            [
              {
                id: "content-negotiate",
                label: "Card content negotiation",
                meaning: "Wrap long titles, controls, and hard objects before changing the grid."
              },
              {
                id: "object-grow",
                label: "Card growth",
                meaning: "Let cards claim more block size when the cluster can support it."
              },
              {
                id: "region-reflow",
                label: "Grid reflow",
                meaning: "Reduce column count and stack the grid as the chrome-level last resort."
              }
            ]
          )
        }),
        "chrome-spotlight": Object.freeze({
          id: "chrome-spotlight",
          label: "Spotlight",
          contractVersion: CONTRACT_VERSION,
          kind: "priority-render",
          description: "Promotes one primary child, then arranges supporting children around it as a rail or stack.",
          preservesPixelBaseline: false,
          restructuresHierarchy: true,
          fitContract: chromeFitContractDefinition(),
          compositionContract: chromeCompositionContractDefinition(
            [
              ".mcel-chrome-spotlight-primary > [data-mcel-chrome-frame]",
              ".mcel-chrome-spotlight-support > [data-mcel-chrome-frame]",
              "[data-mcel-fit-region=\"spotlight-body\"] > .mc"
            ],
            [
              "primary-control-width-collapsed-relative-to-input",
              "shape-interior-escape"
            ],
            {
              "primary-control-width-collapsed-relative-to-input": "control-balance",
              "shape-interior-escape": "shape-inset-content"
            }
          ),
          remediation: chromeRemediationDefinition(
            [
              "content-negotiate",
              "object-grow",
              "region-reflow"
            ],
            [
              {
                id: "content-negotiate",
                label: "Spotlight content negotiation",
                meaning: "Balance the promoted child and support rail before changing the shell."
              },
              {
                id: "object-grow",
                label: "Spotlight growth",
                meaning: "Give the primary story or action more usable interior size."
              },
              {
                id: "region-reflow",
                label: "Support rail reflow",
                meaning: "Move the support rail under the primary child when equal hierarchy is too flat."
              }
            ]
          )
        }),
        "chrome-journey": Object.freeze({
          id: "chrome-journey",
          label: "Journey",
          contractVersion: CONTRACT_VERSION,
          kind: "sequence-render",
          description: "Turns ordered semantic children into steps, timeline entries, milestones, onboarding, or process flow.",
          preservesPixelBaseline: false,
          restructuresHierarchy: true,
          fitContract: chromeFitContractDefinition(),
          compositionContract: chromeCompositionContractDefinition(
            [
              ".mcel-chrome-journey-step > [data-mcel-chrome-frame]",
              "[data-mcel-fit-region=\"sequence-content\"] > .mc"
            ],
            [
              "shape-interior-escape"
            ],
            {
              "shape-interior-escape": "shape-inset-content"
            }
          ),
          remediation: chromeRemediationDefinition(
            [
              "content-negotiate",
              "object-grow",
              "region-reflow"
            ],
            [
              {
                id: "content-negotiate",
                label: "Step content negotiation",
                meaning: "Wrap and balance each step before changing the journey rail."
              },
              {
                id: "object-grow",
                label: "Step growth",
                meaning: "Let milestones use more block size when the sequence can support it."
              },
              {
                id: "region-reflow",
                label: "Sequence reflow",
                meaning: "Collapse the timeline into a simple mobile stack as the last resort."
              }
            ]
          )
        }),
        "chrome-compact-disclosure": Object.freeze({
          id: "chrome-compact-disclosure",
          label: "Compact Disclosure",
          contractVersion: CONTRACT_VERSION,
          kind: "disclosure-render",
          description: "Preserves hierarchy while reducing visual weight through controlled disclosure panels.",
          preservesPixelBaseline: false,
          restructuresHierarchy: true,
          fitContract: chromeFitContractDefinition(),
          compositionContract: chromeCompositionContractDefinition(
            [
              ".mcel-chrome-compact-panel > [data-mcel-chrome-region-role=\"body\"]",
              "[data-mcel-fit-region=\"disclosure-body\"] > .mc"
            ],
            [
              "primary-control-width-collapsed-relative-to-input",
              "shape-interior-escape"
            ],
            {
              "primary-control-width-collapsed-relative-to-input": "control-balance",
              "shape-interior-escape": "shape-inset-content"
            }
          ),
          remediation: chromeRemediationDefinition(
            [
              "content-negotiate",
              "object-grow",
              "region-reflow"
            ],
            [
              {
                id: "content-negotiate",
                label: "Disclosure content negotiation",
                meaning: "Keep each open panel contained before changing the disclosure shell."
              },
              {
                id: "object-grow",
                label: "Panel growth",
                meaning: "Let the active panel claim enough space for dense policy, FAQ, or admin content."
              },
              {
                id: "region-reflow",
                label: "Disclosure reflow",
                meaning: "Stack panels with simple full-width summaries as the chrome-level last resort."
              }
            ]
          )
        })
      });

      const chromeAliases = Object.freeze({
        strict: "chrome-strict-hierarchy",
        hierarchy: "chrome-strict-hierarchy",
        "strict-hierarchy": "chrome-strict-hierarchy",
        "strict-hierarchy.v1": "chrome-strict-hierarchy",
        "chrome-strict": "chrome-strict-hierarchy",
        editorial: "chrome-editorial-flow",
        magazine: "chrome-editorial-flow",
        article: "chrome-editorial-flow",
        "editorial-flow": "chrome-editorial-flow",
        "editorial-flow.v1": "chrome-editorial-flow",
        "chrome-editorial": "chrome-editorial-flow",
        cluster: "chrome-cluster-grid",
        grid: "chrome-cluster-grid",
        cards: "chrome-cluster-grid",
        "card-grid": "chrome-cluster-grid",
        "cluster-grid": "chrome-cluster-grid",
        "cluster-grid.v1": "chrome-cluster-grid",
        "chrome-cluster": "chrome-cluster-grid",
        spotlight: "chrome-spotlight",
        feature: "chrome-spotlight",
        primary: "chrome-spotlight",
        "spotlight.v1": "chrome-spotlight",
        journey: "chrome-journey",
        steps: "chrome-journey",
        timeline: "chrome-journey",
        process: "chrome-journey",
        milestones: "chrome-journey",
        "journey.v1": "chrome-journey",
        disclosure: "chrome-compact-disclosure",
        compact: "chrome-compact-disclosure",
        accordion: "chrome-compact-disclosure",
        "compact-disclosure": "chrome-compact-disclosure",
        "compact-disclosure.v1": "chrome-compact-disclosure",
        "chrome-disclosure": "chrome-compact-disclosure"
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
          hardObjectSelector: commonHardObjectSelector,
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
          "editorial-rail": {region: "narrow", policy: "contain"},
          "cluster-shell": {region: "shell", policy: "chrome-remediates"},
          "cluster-intro": {region: "wide", policy: "contain"},
          "cluster-grid": {region: "grid", policy: "contain"},
          "cluster-item": {region: "grid-item", policy: "contain"},
          "cluster-body": {region: "grid-body", policy: "contain"},
          "spotlight-shell": {region: "shell", policy: "chrome-remediates"},
          "spotlight-primary": {region: "wide", policy: "contain"},
          "spotlight-support": {region: "narrow", policy: "contain"},
          "spotlight-item": {region: "spotlight-item", policy: "contain"},
          "spotlight-body": {region: "spotlight-body", policy: "contain"},
          "journey-shell": {region: "shell", policy: "chrome-remediates"},
          "journey-intro": {region: "wide", policy: "contain"},
          "journey-sequence": {region: "sequence", policy: "contain"},
          "journey-step": {region: "sequence-item", policy: "contain"},
          "journey-body": {region: "sequence-body", policy: "contain"},
          "journey-content": {region: "sequence-content", policy: "contain"},
          "compact-shell": {region: "shell", policy: "chrome-remediates"},
          "compact-intro": {region: "wide", policy: "contain"},
          "compact-panels": {region: "disclosure", policy: "contain"},
          "compact-panel": {region: "disclosure-item", policy: "contain"},
          "compact-summary": {region: "disclosure-summary", policy: "contain"},
          "compact-body": {region: "disclosure-body", policy: "contain"}
        };
        return map[part] || {region: "flow", policy: "contain"};
      }

      function smartChildren(element) {
        return [...(element?.children || [])].filter((child) =>
          child.nodeType === 1 &&
          child.tagName !== "SCRIPT" &&
          child.tagName !== "STYLE" &&
          child.tagName !== "TEMPLATE" &&
          child.getAttribute(attributes.generated) !== "true" &&
          child.getAttribute(CHROME_GENERATED_ATTR) !== "true"
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

      function generatedPart(part, chrome, tagName = "div") {
        const element = document.createElement(tagName);
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

      function markChromeFrame(element, frame = "object") {
        element.setAttribute(CHROME_FRAME_ATTR, frame);
        return element;
      }

      function markChromeRegion(element, role) {
        element.setAttribute(CHROME_REGION_ROLE_ATTR, role);
        return element;
      }

      function generatedRegion(part, chrome, role, tagName = "div") {
        return markChromeRegion(generatedPart(part, chrome, tagName), role);
      }

      function generatedObjectFrame(part, chrome, children, options = {}) {
        const frame = markChromeFrame(generatedPart(part, chrome, options.tagName || "div"), options.frame || "object");
        const body = generatedRegion(options.bodyPart || `${part}-body`, chrome, "body");
        children.filter(Boolean).forEach((child) => body.appendChild(child));
        frame.appendChild(body);
        return frame;
      }

      function appendBucket(parent, part, children, chrome) {
        if (!children.length) return null;
        const bucket = generatedPart(part, chrome);
        children.forEach((child) => bucket.appendChild(child));
        parent.appendChild(bucket);
        return bucket;
      }

      function appendFramedBucket(parent, bucketPart, framePart, children, chrome, options = {}) {
        if (!children.length) return null;
        const bucket = generatedPart(bucketPart, chrome);
        children.forEach((child) => {
          bucket.appendChild(generatedObjectFrame(framePart, chrome, [child], options));
        });
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

      function isPrimaryCandidate(child) {
        const kind = child.getAttribute(attributes.kind) || "";
        const component = child.getAttribute(attributes.componentName) || "";
        const rank = child.getAttribute("data-mc-rank") || "";
        const tagName = child.tagName || "";
        return kind === "hero" ||
          component === "HeroSection" ||
          rank === "primary" ||
          tagName === "HEADER";
      }

      function isIntroCandidate(child, index) {
        const slot = child.getAttribute("data-mc-slot") || "";
        const kind = child.getAttribute(attributes.kind) || "";
        const componentKind = child.getAttribute(attributes.componentKind) || "";
        const rank = child.getAttribute("data-mc-rank") || "";
        const tagName = child.tagName || "";
        return index === 0 && (
          /^H[1-6]$/.test(tagName) ||
          tagName === "HEADER" ||
          slot === "title" ||
          slot === "intro" ||
          kind === "hero" ||
          componentKind === "page-intro" ||
          rank === "primary"
        );
      }

      function splitIntroAndItems(children) {
        const intro = [];
        const items = [];
        children.forEach((child, index) => {
          if (isIntroCandidate(child, index)) {
            intro.push(child);
          } else {
            items.push(child);
          }
        });
        if (items.length < 2 && children.length >= 2) {
          return {intro: [], items: [...children]};
        }
        return {intro, items};
      }

      function labelForChild(child, fallback) {
        const labelElement = child.querySelector?.("h1,h2,h3,h4,h5,h6,[data-mc-slot=\"title\"],[data-mc-slot=\"label\"],summary");
        const text = String((labelElement || child).textContent || "").replace(/\s+/g, " ").trim();
        if (!text) return fallback;
        return text.length > 72 ? `${text.slice(0, 69)}…` : text;
      }

      function createReport(chrome, sourceHtml) {
        return {
          contractVersion: CONTRACT_VERSION,
          chrome,
          label: chromeLabel(chrome),
          changed: false,
          generatedContainers: 0,
          movedSourceElements: 0,
          preservesPixelBaseline: chromeDefinition(chrome).preservesPixelBaseline === true,
          visibleResponse: Boolean(String(sourceHtml || "").trim()),
          warnings: []
        };
      }

      function chromeDomContext(runtimeHtml, chrome, report, fallbackName) {
        const sourceHtml = String(runtimeHtml || "");
        if (!sourceHtml.trim()) {
          report.warnings.push(`No runtime HTML was available for ${fallbackName}.`);
          return {sourceHtml, template: null, root: null, children: [], generatedRuntimeParts: [], unavailable: true};
        }
        if (typeof document === "undefined" || typeof document.createElement !== "function") {
          report.warnings.push(`DOM APIs are unavailable; ${fallbackName} fell back to strict hierarchy.`);
          return {sourceHtml, template: null, root: null, children: [], generatedRuntimeParts: [], unavailable: true};
        }

        const template = document.createElement("template");
        template.innerHTML = sourceHtml;
        const root = firstMeaningfulRoot(template.content);
        if (!root) {
          report.warnings.push(`No root element was found; ${fallbackName} fell back to strict hierarchy.`);
          return {sourceHtml, template, root: null, children: [], generatedRuntimeParts: [], unavailable: true};
        }

        const children = smartChildren(root);
        const generatedRuntimeParts = [...root.children].filter(isGeneratedElement);
        return {sourceHtml, template, root, children, generatedRuntimeParts, unavailable: false};
      }

      function markOnlyRoot(root, template, chrome, report, warning) {
        if (root) root.setAttribute(CHROME_ATTR, chrome);
        if (warning) report.warnings.push(warning);
        return {html: template?.innerHTML || "", report};
      }

      function countGenerated(container) {
        return container ? container.querySelectorAll(`[${CHROME_GENERATED_ATTR}="true"]`).length + 1 : 0;
      }

      function resetRootForChrome(root, generatedRuntimeParts, shell, chrome) {
        root.setAttribute(CHROME_ATTR, chrome);
        root.innerHTML = "";
        generatedRuntimeParts.forEach((part) => root.appendChild(part));
        root.appendChild(shell);
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
        const report = createReport(chrome, runtimeHtml);
        const context = chromeDomContext(runtimeHtml, chrome, report, "editorial flow");
        if (context.unavailable) return {html: context.sourceHtml, report};
        const {template, root, children: rootChildren, generatedRuntimeParts} = context;

        if (rootChildren.length < 2) {
          return markOnlyRoot(root, template, chrome, report, "Not enough peer source children to restructure; editorial flow only marked the root.");
        }

        const {hero, body, aside} = classifyEditorialChildren(rootChildren);
        if (!hero.length && !body.length) {
          return markOnlyRoot(root, template, chrome, report, "Editorial buckets were empty; editorial flow only marked the root.");
        }

        const shell = generatedPart("editorial-shell", chrome);
        resetRootForChrome(root, generatedRuntimeParts, shell, chrome);

        appendBucket(shell, "editorial-lede", hero, chrome);
        appendBucket(shell, "editorial-body", body, chrome);
        appendBucket(shell, "editorial-rail", aside, chrome);

        report.changed = true;
        report.generatedContainers = countGenerated(shell);
        report.movedSourceElements = hero.length + body.length + aside.length;
        return {html: template.innerHTML, report};
      }

      function applyClusterGridHtml(runtimeHtml, options = {}) {
        const chrome = "chrome-cluster-grid";
        const report = createReport(chrome, runtimeHtml);
        const context = chromeDomContext(runtimeHtml, chrome, report, "cluster grid");
        if (context.unavailable) return {html: context.sourceHtml, report};
        const {template, root, children: rootChildren, generatedRuntimeParts} = context;

        if (rootChildren.length < 2) {
          return markOnlyRoot(root, template, chrome, report, "Not enough peer source children to cluster; cluster grid only marked the root.");
        }

        const {intro, items} = splitIntroAndItems(rootChildren);
        if (items.length < 2) {
          return markOnlyRoot(root, template, chrome, report, "Cluster grid needs at least two peer items; it only marked the root.");
        }

        const shell = generatedPart("cluster-shell", chrome);
        resetRootForChrome(root, generatedRuntimeParts, shell, chrome);
        appendBucket(shell, "cluster-intro", intro, chrome);
        appendFramedBucket(shell, "cluster-grid", "cluster-item", items, chrome, {bodyPart: "cluster-body"});

        report.changed = true;
        report.generatedContainers = countGenerated(shell);
        report.movedSourceElements = intro.length + items.length;
        return {html: template.innerHTML, report};
      }

      function applySpotlightHtml(runtimeHtml, options = {}) {
        const chrome = "chrome-spotlight";
        const report = createReport(chrome, runtimeHtml);
        const context = chromeDomContext(runtimeHtml, chrome, report, "spotlight");
        if (context.unavailable) return {html: context.sourceHtml, report};
        const {template, root, children: rootChildren, generatedRuntimeParts} = context;

        if (rootChildren.length < 2) {
          return markOnlyRoot(root, template, chrome, report, "Not enough source children to spotlight; spotlight only marked the root.");
        }

        let primaryIndex = rootChildren.findIndex(isPrimaryCandidate);
        if (primaryIndex < 0) primaryIndex = 0;
        const primary = rootChildren[primaryIndex];
        const support = rootChildren.filter((_, index) => index !== primaryIndex);
        if (!primary || !support.length) {
          return markOnlyRoot(root, template, chrome, report, "Spotlight could not find primary and support children; it only marked the root.");
        }

        const shell = generatedPart("spotlight-shell", chrome);
        resetRootForChrome(root, generatedRuntimeParts, shell, chrome);
        appendFramedBucket(shell, "spotlight-primary", "spotlight-item", [primary], chrome, {bodyPart: "spotlight-body", frame: "primary"});
        appendFramedBucket(shell, "spotlight-support", "spotlight-item", support, chrome, {bodyPart: "spotlight-body", frame: "support"});

        report.changed = true;
        report.generatedContainers = countGenerated(shell);
        report.movedSourceElements = support.length + 1;
        return {html: template.innerHTML, report};
      }

      function applyJourneyHtml(runtimeHtml, options = {}) {
        const chrome = "chrome-journey";
        const report = createReport(chrome, runtimeHtml);
        const context = chromeDomContext(runtimeHtml, chrome, report, "journey");
        if (context.unavailable) return {html: context.sourceHtml, report};
        const {template, root, children: rootChildren, generatedRuntimeParts} = context;

        if (rootChildren.length < 2) {
          return markOnlyRoot(root, template, chrome, report, "Not enough ordered source children to sequence; journey only marked the root.");
        }

        const {intro, items} = splitIntroAndItems(rootChildren);
        if (items.length < 2) {
          return markOnlyRoot(root, template, chrome, report, "Journey needs at least two ordered children; it only marked the root.");
        }

        const shell = generatedPart("journey-shell", chrome);
        const sequence = generatedPart("journey-sequence", chrome);
        resetRootForChrome(root, generatedRuntimeParts, shell, chrome);
        appendBucket(shell, "journey-intro", intro, chrome);
        items.forEach((child, index) => {
          const step = generatedPart("journey-step", chrome);
          step.setAttribute("data-mcel-step", String(index + 1));
          step.appendChild(generatedObjectFrame("journey-body", chrome, [child], {bodyPart: "journey-content", frame: "sequence-body"}));
          sequence.appendChild(step);
        });
        shell.appendChild(sequence);

        report.changed = true;
        report.generatedContainers = countGenerated(shell);
        report.movedSourceElements = intro.length + items.length;
        return {html: template.innerHTML, report};
      }

      function applyCompactDisclosureHtml(runtimeHtml, options = {}) {
        const chrome = "chrome-compact-disclosure";
        const report = createReport(chrome, runtimeHtml);
        const context = chromeDomContext(runtimeHtml, chrome, report, "compact disclosure");
        if (context.unavailable) return {html: context.sourceHtml, report};
        const {template, root, children: rootChildren, generatedRuntimeParts} = context;

        if (rootChildren.length < 2) {
          return markOnlyRoot(root, template, chrome, report, "Not enough source children for disclosure; compact disclosure only marked the root.");
        }

        const {intro, items} = splitIntroAndItems(rootChildren);
        if (items.length < 2) {
          return markOnlyRoot(root, template, chrome, report, "Compact disclosure needs at least two panels; it only marked the root.");
        }

        const shell = generatedPart("compact-shell", chrome);
        const panels = generatedPart("compact-panels", chrome);
        resetRootForChrome(root, generatedRuntimeParts, shell, chrome);
        appendBucket(shell, "compact-intro", intro, chrome);
        items.forEach((child, index) => {
          const panel = markChromeFrame(generatedPart("compact-panel", chrome, "details"), "disclosure");
          const summary = generatedRegion("compact-summary", chrome, "header", "summary");
          const body = generatedRegion("compact-body", chrome, "body");
          summary.textContent = labelForChild(child, `Panel ${index + 1}`);
          body.appendChild(child);
          panel.appendChild(summary);
          panel.appendChild(body);
          if (index === 0) panel.open = true;
          panels.appendChild(panel);
        });
        shell.appendChild(panels);

        report.changed = true;
        report.generatedContainers = countGenerated(shell);
        report.movedSourceElements = intro.length + items.length;
        return {html: template.innerHTML, report};
      }

      function applyChromeHtml(runtimeHtml, options = {}) {
        const chrome = normalizeChrome(options.chrome);
        if (chrome === "chrome-editorial-flow") return applyEditorialFlowHtml(runtimeHtml, {...options, chrome});
        if (chrome === "chrome-cluster-grid") return applyClusterGridHtml(runtimeHtml, {...options, chrome});
        if (chrome === "chrome-spotlight") return applySpotlightHtml(runtimeHtml, {...options, chrome});
        if (chrome === "chrome-journey") return applyJourneyHtml(runtimeHtml, {...options, chrome});
        if (chrome === "chrome-compact-disclosure") return applyCompactDisclosureHtml(runtimeHtml, {...options, chrome});
        return applyStrictHierarchyHtml(runtimeHtml, {...options, chrome});
      }

      return Object.freeze({
        CONTRACT_VERSION,
        CHROME_ATTR,
        CHROME_GENERATED_ATTR,
        CHROME_PART_ATTR,
        CHROME_ID_ATTR,
        CHROME_FRAME_ATTR,
        CHROME_REGION_ROLE_ATTR,
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
