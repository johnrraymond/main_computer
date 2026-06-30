    var McelLabEngine = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const {attributes, defaults, runtimeOwnedAttributes, runtimeOwnedClasses, schema, contractVersion} = contract;

      function logEvent(events, level, module, code, message) {
        events.push({level, module, code, message});
      }

      function normalizeValue(value, fallback) {
        const normalized = String(value || "").trim().toLowerCase();
        return normalized || fallback;
      }

      function schemaFor(rawType, events = []) {
        const requested = normalizeValue(rawType, defaults.type);
        if (schema[requested]) return {type: requested, schema: schema[requested]};
        logEvent(events, "warning", "schema", "MCEL_UNKNOWN_TYPE", `data-mc=${requested} normalized to ${defaults.type}.`);
        return {type: defaults.type, schema: schema[defaults.type]};
      }

      function parseSource(source) {
        const parser = new DOMParser();
        return parser.parseFromString(String(source || ""), "text/html");
      }

      function sourceElements(docOrRoot) {
        return [...(docOrRoot?.querySelectorAll?.(`[${attributes.type}]`) || [])];
      }

      function stripGeneratedParts(root) {
        root.querySelectorAll?.(`[${attributes.generated}="true"]`).forEach((node) => node.remove());
      }

      function removeRuntimeState(element) {
        runtimeOwnedAttributes.forEach((attribute) => element.removeAttribute(attribute));
        element.removeAttribute("style");
        [...element.classList].forEach((className) => {
          if (
            runtimeOwnedClasses.includes(className) ||
            className.startsWith("mc-")
          ) {
            element.classList.remove(className);
          }
        });
        if (!element.getAttribute("class")) element.removeAttribute("class");
      }

      function words(element) {
        return String(element.getAttribute(attributes.words) || "")
          .split(/\s+/)
          .map((word) => word.trim())
          .filter(Boolean);
      }

      function validateAttribute(element, events, attribute, allowed, fallback) {
        const value = normalizeValue(element.getAttribute(attribute), fallback);
        if (!allowed.includes(value)) {
          logEvent(events, "warning", "schema", "MCEL_SCHEMA_NORMALIZED", `${attribute}=${value} normalized to ${fallback}.`);
          element.setAttribute(attribute, fallback);
          return fallback;
        }
        element.setAttribute(attribute, value);
        return value;
      }

      function validateOptionalAttribute(element, events, attribute, allowed, fallback) {
        if (!element.hasAttribute(attribute)) return "";
        return validateAttribute(element, events, attribute, allowed, fallback);
      }

      function validateElement(element, elementSchema, events) {
        validateAttribute(element, events, attributes.kind, elementSchema.allowedKinds, defaults.kind);
        validateAttribute(element, events, attributes.flow, elementSchema.allowedFlows, defaults.flow);
        validateAttribute(element, events, attributes.rank, elementSchema.allowedRanks, defaults.rank);
        validateAttribute(element, events, attributes.state, elementSchema.allowedStates, defaults.state);
        validateAttribute(element, events, attributes.density, elementSchema.allowedDensities, defaults.density);
        validateAttribute(element, events, attributes.sizePolicy, elementSchema.allowedSizePolicies || ["adaptive"], defaults.sizePolicy);
        validateAttribute(element, events, attributes.overflowPolicy, elementSchema.allowedOverflowPolicies || ["contain"], defaults.overflowPolicy);
        validateAttribute(element, events, attributes.scrollPolicy, elementSchema.allowedScrollPolicies || ["auto"], defaults.scrollPolicy);
        validateOptionalAttribute(element, events, attributes.componentKind, elementSchema.allowedComponentKinds || ["component"], "component");
        validateOptionalAttribute(element, events, attributes.stateOwner, elementSchema.allowedStateOwners || ["none"], "none");
        validateOptionalAttribute(element, events, attributes.statePolicy, elementSchema.allowedStatePolicies || ["local"], "local");
        validateOptionalAttribute(element, events, attributes.cachePolicy, elementSchema.allowedCachePolicies || ["none"], "none");
        validateOptionalAttribute(element, events, attributes.syncPolicy, elementSchema.allowedSyncPolicies || ["none"], "none");
        validateOptionalAttribute(element, events, attributes.validation, elementSchema.allowedValidationPolicies || ["none"], "none");
        validateOptionalAttribute(element, events, attributes.dirtyPolicy, elementSchema.allowedDirtyPolicies || ["none"], "none");
        validateOptionalAttribute(element, events, attributes.errorPolicy, elementSchema.allowedErrorPolicies || ["inline"], "inline");
        validateOptionalAttribute(element, events, attributes.swapPolicy, elementSchema.allowedSwapPolicies || ["none"], "none");
        validateOptionalAttribute(element, events, attributes.eventPolicy, elementSchema.allowedEventPolicies || ["none"], "none");
        validateOptionalAttribute(element, events, attributes.renderMode, elementSchema.allowedRenderModes || ["client"], "client");
        validateOptionalAttribute(element, events, attributes.hydration, elementSchema.allowedHydrationPolicies || ["none"], "none");
        validateOptionalAttribute(element, events, attributes.islandPolicy, elementSchema.allowedIslandPolicies || ["none"], "none");
        validateOptionalAttribute(element, events, attributes.focusPolicy, elementSchema.allowedFocusPolicies || ["auto"], "auto");
        validateOptionalAttribute(element, events, attributes.a11yPolicy, elementSchema.allowedA11yPolicies || ["auto"], "auto");
        validateOptionalAttribute(element, events, attributes.performanceBudget, elementSchema.allowedPerformanceBudgets || ["none"], "none");
        validateOptionalAttribute(element, events, attributes.securityPolicy, elementSchema.allowedSecurityPolicies || ["default"], "default");
      }

      function computedDensity(element) {
        const explicit = normalizeValue(element.getAttribute(attributes.density), defaults.density);
        if (explicit && explicit !== "auto") return explicit;
        const wordCount = (element.textContent || "").trim().split(/\s+/).filter(Boolean).length;
        if (wordCount > 44) return "compressed";
        if (wordCount > 22) return "dense";
        return "calm";
      }

      function computedNeighborhood(element) {
        const parent = element.parentElement;
        if (!parent) return {neighborhood: "isolated", clusterSize: 1};
        const siblings = [...parent.children].filter((child) => child.hasAttribute?.(attributes.type));
        if (siblings.length <= 1) return {neighborhood: "isolated", clusterSize: siblings.length || 1};
        const index = siblings.indexOf(element);
        if (index === 0) return {neighborhood: "cluster-start", clusterSize: siblings.length};
        if (index === siblings.length - 1) return {neighborhood: "cluster-end", clusterSize: siblings.length};
        return {neighborhood: "cluster-middle", clusterSize: siblings.length};
      }

      function relationState(element, allSmartElements) {
        const raw = String(element.getAttribute(attributes.connects) || "").trim();
        if (!raw) return {state: "", count: 0};
        const targets = raw.split(/[,\s]+/).map((target) => target.trim()).filter(Boolean);
        const resolved = targets.filter((target) => (
          allSmartElements.some((candidate) => candidate.id === target || candidate.getAttribute("data-mc-id") === target)
        ));
        if (!targets.length) return {state: "", count: 0};
        if (resolved.length === targets.length) return {state: "resolved", count: resolved.length};
        if (resolved.length) return {state: "partial", count: resolved.length};
        return {state: "pending", count: 0};
      }



      function reasonLabelForElement(element) {
        return [
          element.getAttribute(attributes.type) || defaults.type,
          element.getAttribute(attributes.kind) || defaults.kind,
          element.getAttribute(attributes.flow) || defaults.flow,
          element.getAttribute(attributes.state) || defaults.state
        ].join("/");
      }

      function createGeneratedPart(part, element) {
        const node = element.ownerDocument.createElement("div");
        node.setAttribute(attributes.generated, "true");
        node.setAttribute(attributes.part, part);
        node.setAttribute(attributes.artifactOwner, "mcel-part-manager");
        node.setAttribute(attributes.artifactOrigin, "runtime");
        node.setAttribute(attributes.artifactReason, `compile:${element.getAttribute(attributes.type) || defaults.type}:${part}`);
        node.setAttribute(attributes.contractVersion, contractVersion || "mcel-lab");
        node.setAttribute("aria-hidden", "true");
        if (part === "meta") {
          node.textContent = [
            element.getAttribute(attributes.kind) || defaults.kind,
            element.getAttribute(attributes.state) || defaults.state,
            element.getAttribute(attributes.neighborhood) || "isolated"
          ].join(" · ");
        } else if (part === "copy") {
          node.textContent = words(element).slice(0, 5).join(" ") || "semantic runtime copy lane";
        }
        return node;
      }

      function rebuildGeneratedParts(element, elementSchema, events, reason) {
        const removed = element.querySelectorAll(`[${attributes.generated}="true"]`).length;
        stripGeneratedParts(element);
        elementSchema.generatedParts.slice().reverse().forEach((part) => {
          element.insertBefore(createGeneratedPart(part, element), element.firstChild);
        });
        if (removed) {
          logEvent(events, "repair", "part-manager", "MCEL_PARTS_REBUILT", `Rebuilt ${elementSchema.generatedParts.length} generated part(s) after removing ${removed} stale part(s) during ${reason}.`);
        }
      }

      function enhanceElement(element, allSmartElements, events, sourceIndex = 0) {
        const resolved = schemaFor(element.getAttribute(attributes.type), events);
        const type = resolved.type;
        const elementSchema = resolved.schema;
        element.setAttribute(attributes.type, type);
        validateElement(element, elementSchema, events);
        stripGeneratedParts(element);

        const density = computedDensity(element);
        const neighborhood = computedNeighborhood(element);
        const relation = relationState(element, allSmartElements);

        element.classList.add("mc", `mc-${type}`);
        element.setAttribute(attributes.enhanced, "true");
        element.setAttribute(attributes.sourceIndex, String(sourceIndex));
        element.setAttribute(attributes.artifactOwner, "mcel-runtime-builder");
        element.setAttribute(attributes.artifactOrigin, "source");
        element.setAttribute(attributes.artifactReason, `enhanced:${reasonLabelForElement(element)}`);
        element.setAttribute(attributes.contractVersion, contractVersion || "mcel-lab");
        element.setAttribute(attributes.computedDensity, density);
        element.setAttribute(attributes.neighborhood, neighborhood.neighborhood);
        element.setAttribute(attributes.clusterSize, String(neighborhood.clusterSize));
        element.style.setProperty("--mc-word-count", String(words(element).length));
        element.style.setProperty("--mc-source-index", String(sourceIndex));

        if (relation.state) {
          element.setAttribute(attributes.relation, relation.state);
          element.setAttribute(attributes.relationCount, String(relation.count));
        } else {
          element.removeAttribute(attributes.relation);
          element.removeAttribute(attributes.relationCount);
        }

        elementSchema.generatedParts.slice().reverse().forEach((part) => {
          element.insertBefore(createGeneratedPart(part, element), element.firstChild);
        });

        logEvent(
          events,
          "info",
          "runtime-builder",
          "MCEL_ELEMENT_ENHANCED",
          `${type} enhanced as ${element.getAttribute(attributes.kind)}/${element.getAttribute(attributes.flow)}/${element.getAttribute(attributes.state)} with overflow=${element.getAttribute(attributes.overflowPolicy)} scroll=${element.getAttribute(attributes.scrollPolicy)} render=${element.getAttribute(attributes.renderMode) || "unset"} component=${element.getAttribute(attributes.componentName) || "anonymous"}.`
        );
      }

      function compileDocument(doc, reason = "compile") {
        const events = [];
        logEvent(events, "info", "compiler", "MCEL_COMPILE_START", `Compiling source because ${reason}.`);
        const smartElements = sourceElements(doc.body);
        logEvent(events, "info", "source-reader", "MCEL_SOURCE_FOUND", `Found ${smartElements.length} MCEL source element(s).`);
        smartElements.forEach((element, index) => enhanceElement(element, smartElements, events, index));
        logEvent(events, "success", "compiler", "MCEL_COMPILE_DONE", "Runtime DOM synchronized from clean source.");
        return {doc, runtimeHtml: doc.body.innerHTML.trim(), events, sourceCount: smartElements.length};
      }

      function compileSource(source, options = {}) {
        return compileDocument(parseSource(source), options.reason || "compile");
      }

      function serializeRuntimeRoot(root, options = {}) {
        const clone = root?.cloneNode?.(true);
        const report = {
          removedGeneratedParts: 0,
          preservedSourceElements: 0,
          warnings: [],
          serializerClean: true,
          reason: options.reason || "serialize"
        };
        if (!clone) {
          report.serializerClean = false;
          report.warnings.push("No runtime root was available to serialize.");
          return {serialized: "", report};
        }
        clone.querySelectorAll(`[${attributes.generated}="true"]`).forEach((node) => {
          report.removedGeneratedParts += 1;
          node.remove();
        });
        sourceElements(clone).forEach((element) => {
          report.preservedSourceElements += 1;
          removeRuntimeState(element);
        });
        if (clone.querySelector(`[${attributes.generated}="true"]`)) {
          report.serializerClean = false;
          report.warnings.push("A generated runtime part survived serialization.");
        }
        const serialized = (clone.innerHTML || "").trim();
        if (serialized.includes(attributes.generated)) {
          report.serializerClean = false;
          report.warnings.push("Serialized source still contains a generated-part attribute literal.");
        }
        return {serialized, report};
      }

      function repairRuntimeRoot(root, options = {}) {
        const events = [];
        let repaired = 0;
        sourceElements(root).forEach((element) => {
          const resolved = schemaFor(element.getAttribute(attributes.type), events);
          const expected = [...resolved.schema.generatedParts];
          const before = [...element.querySelectorAll(`:scope > [${attributes.generated}="true"][${attributes.part}]`)]
            .map((node) => node.getAttribute(attributes.part));
          const needsRepair = before.length !== expected.length || expected.some((part, index) => before[index] !== part);
          const staleNestedParts = element.querySelectorAll(`[${attributes.generated}="true"]`).length !== before.length;
          if (needsRepair || staleNestedParts) {
            rebuildGeneratedParts(element, resolved.schema, events, options.reason || "repair");
            repaired += 1;
          }
        });
        logEvent(
          events,
          repaired ? "repair" : "info",
          "repair",
          repaired ? "MCEL_PART_REPAIRED" : "MCEL_REPAIR_NOOP",
          repaired ? `Restored canonical generated parts on ${repaired} MCEL element(s).` : "Runtime generated parts were already complete."
        );
        return {repaired, events};
      }

      function damageRuntimeRoot(root) {
        const generated = root?.querySelector?.(`[${attributes.generated}="true"]`);
        if (!generated) {
          return {
            damaged: false,
            part: "",
            events: [{level: "warning", module: "stress", code: "MCEL_DAMAGE_SKIPPED", message: "No generated runtime part was available to damage."}]
          };
        }
        const part = generated.getAttribute(attributes.part) || "unknown";
        generated.remove();
        return {
          damaged: true,
          part,
          events: [{level: "repair", module: "stress", code: "MCEL_RUNTIME_DAMAGED", message: `Deleted generated ${part} part.`}]
        };
      }

      function computeA11y(root) {
        const report = {
          elementsChecked: 0,
          labels: [],
          decorationsHidden: true,
          readingOrderValid: true,
          focusWarnings: [],
          scrollRegions: [],
          warnings: [],
          a11yValid: true
        };
        sourceElements(root).forEach((element) => {
          report.elementsChecked += 1;
          const heading = element.querySelector("h1,h2,h3,h4,h5,h6");
          if (heading) {
            report.labels.push(heading.textContent.trim());
          } else {
            report.warnings.push(`${element.getAttribute(attributes.type) || "element"} is missing a heading label.`);
          }
          const scrollPolicy = element.getAttribute(attributes.scrollPolicy) || defaults.scrollPolicy;
          const overflowPolicy = element.getAttribute(attributes.overflowPolicy) || defaults.overflowPolicy;
          const a11yPolicy = element.getAttribute(attributes.a11yPolicy) || "auto";
          const focusPolicy = element.getAttribute(attributes.focusPolicy) || "auto";
          if (a11yPolicy === "strict" && !heading && !element.getAttribute("aria-label")) {
            report.warnings.push(`${element.getAttribute(attributes.type) || "element"} strict a11y policy requires a heading or aria-label.`);
          }
          if (focusPolicy === "trap" && !element.hasAttribute("tabindex")) {
            report.focusWarnings.push(`${element.getAttribute(attributes.type) || "element"} traps focus but has no tabindex boundary.`);
          }
          if (["required", "child-only", "external", "viewport-only"].includes(scrollPolicy) || ["delegate", "paginate", "virtualize"].includes(overflowPolicy)) {
            const label = heading?.textContent?.trim() || element.getAttribute("aria-label") || element.id || "";
            report.scrollRegions.push({
              policy: scrollPolicy,
              overflowPolicy,
              label,
              keyboardReachable: !element.hasAttribute("inert")
            });
            if (!label) report.warnings.push(`${element.getAttribute(attributes.type) || "element"} scroll/overflow policy needs a heading, aria-label, or id.`);
            if (element.hasAttribute("inert")) report.focusWarnings.push(`${element.getAttribute(attributes.type) || "element"} declares scroll behavior but is inert.`);
          }
          element.querySelectorAll(`[${attributes.generated}="true"]`).forEach((generated) => {
            if (generated.getAttribute("aria-hidden") !== "true") {
              report.decorationsHidden = false;
              report.warnings.push(`${generated.getAttribute(attributes.part) || "generated"} is not aria-hidden.`);
            }
            if (generated.matches("button,a,input,select,textarea,[tabindex]")) {
              report.focusWarnings.push(`${generated.getAttribute(attributes.part) || "generated"} should not be focusable in this slice.`);
            }
          });
        });
        report.a11yValid = report.decorationsHidden && report.readingOrderValid && report.focusWarnings.length === 0 && report.warnings.length === 0;
        return report;
      }

      function debuggerStateFor(element, root) {
        if (!element) return {selected: false, sourceValid: false};
        const resolved = schemaFor(element.getAttribute(attributes.type), []);
        const generatedParts = [...element.querySelectorAll(`:scope > [${attributes.generated}="true"]`)]
          .map((node) => node.getAttribute(attributes.part))
          .filter(Boolean);
        const expectedParts = [...resolved.schema.generatedParts];
        const missingParts = expectedParts.filter((part) => !generatedParts.includes(part));
        const generatedPartsCanonical = expectedParts.length === generatedParts.length &&
          expectedParts.every((part, index) => generatedParts[index] === part);
        const serialization = serializeRuntimeRoot(root || element.parentElement || element, {reason: "debug-check"});
        return {
          sourceIndex: Number(element.getAttribute(attributes.sourceIndex) || "0"),
          type: element.getAttribute(attributes.type) || defaults.type,
          kind: element.getAttribute(attributes.kind) || defaults.kind,
          flow: element.getAttribute(attributes.flow) || defaults.flow,
          rank: element.getAttribute(attributes.rank) || defaults.rank,
          state: element.getAttribute(attributes.state) || defaults.state,
          density: element.getAttribute(attributes.computedDensity) || "calm",
          words: words(element),
          connects: element.getAttribute(attributes.connects) || "",
          sizePolicy: element.getAttribute(attributes.sizePolicy) || defaults.sizePolicy,
          overflowPolicy: element.getAttribute(attributes.overflowPolicy) || defaults.overflowPolicy,
          scrollPolicy: element.getAttribute(attributes.scrollPolicy) || defaults.scrollPolicy,
          layoutLaw: element.getAttribute(attributes.layoutLaw) || "",
          overflowComputed: element.getAttribute(attributes.overflowComputed) || "",
          scrollNeeded: element.getAttribute(attributes.scrollNeeded) || "",
          scrollOwner: element.getAttribute(attributes.scrollOwner) || "",
          layoutPressure: element.getAttribute(attributes.layoutPressure) || "",
          geometryProof: element.getAttribute(attributes.geometryProof) || "",
          componentName: element.getAttribute(attributes.componentName) || "",
          componentKind: element.getAttribute(attributes.componentKind) || "",
          stateOwner: element.getAttribute(attributes.stateOwner) || "",
          stateScope: element.getAttribute(attributes.stateScope) || "",
          statePolicy: element.getAttribute(attributes.statePolicy) || "",
          query: element.getAttribute(attributes.query) || "",
          cachePolicy: element.getAttribute(attributes.cachePolicy) || "",
          syncPolicy: element.getAttribute(attributes.syncPolicy) || "",
          submit: element.getAttribute(attributes.submit) || "",
          validation: element.getAttribute(attributes.validation) || "",
          action: element.getAttribute(attributes.action) || "",
          target: element.getAttribute(attributes.target) || "",
          renderMode: element.getAttribute(attributes.renderMode) || "",
          hydration: element.getAttribute(attributes.hydration) || "",
          a11yPolicy: element.getAttribute(attributes.a11yPolicy) || "",
          focusPolicy: element.getAttribute(attributes.focusPolicy) || "",
          performanceBudget: element.getAttribute(attributes.performanceBudget) || "",
          securityPolicy: element.getAttribute(attributes.securityPolicy) || "",
          proofTier: element.getAttribute(attributes.proofTier) || "",
          semanticRisk: element.getAttribute(attributes.semanticRisk) || "",
          relation: element.getAttribute(attributes.relation) || "",
          relationCount: Number(element.getAttribute(attributes.relationCount) || "0"),
          artifactOwner: element.getAttribute(attributes.artifactOwner) || "",
          artifactOrigin: element.getAttribute(attributes.artifactOrigin) || "",
          artifactReason: element.getAttribute(attributes.artifactReason) || "",
          contractVersion: element.getAttribute(attributes.contractVersion) || "",
          neighborhood: element.getAttribute(attributes.neighborhood) || "isolated",
          clusterSize: Number(element.getAttribute(attributes.clusterSize) || "1"),
          generatedParts,
          missingParts,
          generatedPartsCanonical,
          sourceValid: generatedPartsCanonical,
          serializerClean: serialization.report.serializerClean,
          a11yValid: computeA11y(root || element.parentElement || element).a11yValid
        };
      }


      const debugMechanisms = Object.freeze([
        Object.freeze({id: "mcel.debug.operation.timeline.v1", layer: "core", claim: "Every public MCEL operation can leave a timestamped envelope."}),
        Object.freeze({id: "mcel.debug.page.metrics.v1", layer: "browser", claim: "A capture records viewport and document height so page-stack failures are visible."}),
        Object.freeze({id: "mcel.debug.root.identity.v1", layer: "source", claim: "A capture records the selected root selector, source id, and component attributes."}),
        Object.freeze({id: "mcel.debug.css.computed-style.v1", layer: "style", claim: "A capture records computed display/background/overflow for critical nodes."}),
        Object.freeze({id: "mcel.debug.css.not-winning.v1", layer: "style", claim: "Expected computed styles can fail closed when scoped CSS is not winning."}),
        Object.freeze({id: "mcel.debug.theme-leak.v1", layer: "style", claim: "Global button/theme leakage is detected with color and selector evidence."}),
        Object.freeze({id: "mcel.debug.grid-contract.v1", layer: "layout", claim: "Grid-owned shells report when display:grid is missing at runtime."}),
        Object.freeze({id: "mcel.debug.stacked-children.v1", layer: "layout", claim: "Workbench children that should share a row report top/height evidence when stacked."}),
        Object.freeze({id: "mcel.debug.scroll-boundary.v1", layer: "layout", claim: "Page-level scroll bloat is reported separately from intended internal scroll areas."}),
        Object.freeze({id: "mcel.debug.dominant-element.v1", layer: "layout", claim: "The largest rendered elements are listed so runaway panels are obvious."}),
        Object.freeze({id: "mcel.debug.collapsed-dock.v1", layer: "interaction", claim: "Docks expected to be collapsed report if they dominate the app."}),
        Object.freeze({id: "mcel.debug.generated-boundary.v1", layer: "runtime", claim: "Generated/runtime-owned parts are counted and tagged for source firewall studies."}),
        Object.freeze({id: "mcel.debug.serializer-firewall.v1", layer: "serializer", claim: "Serialization captures can include whether generated runtime artifacts survived."}),
        Object.freeze({id: "mcel.debug.repair-evidence.v1", layer: "repair", claim: "Repair captures can be attached to the before/after runtime envelope."}),
        Object.freeze({id: "mcel.debug.a11y-snapshot.v1", layer: "a11y", claim: "A capture can include label, role, hidden, inert, and focus warnings."}),
        Object.freeze({id: "mcel.debug.contract-clause.v1", layer: "contract", claim: "Debug issues can point back to user-space contract clauses instead of vague failures."}),
        Object.freeze({id: "mcel.debug.failure-packet.v1", layer: "study", claim: "The packet is JSON-safe and ready to paste into a bug report."}),
        Object.freeze({id: "mcel.debug.no-hidden-magic.v1", layer: "study", claim: "A capture records what was inspected and what was not inspectable."})
      ]);

      function listDebugMechanisms() {
        return debugMechanisms.map((mechanism) => ({...mechanism}));
      }

      function debugNow() {
        try {
          return new Date().toISOString();
        } catch (_error) {
          return "unknown-time";
        }
      }

      function debugStableHash(value) {
        const text = String(value || "");
        let hash = 2166136261;
        for (let index = 0; index < text.length; index += 1) {
          hash ^= text.charCodeAt(index);
          hash = Math.imul(hash, 16777619);
        }
        return `fnv1a-${(hash >>> 0).toString(16).padStart(8, "0")}`;
      }

      function debugSafeText(value, max = 160) {
        return String(value || "")
          .replace(/\s+/g, " ")
          .trim()
          .slice(0, max);
      }

      function debugNodeLabel(node) {
        if (!node) return "null";
        if (node.nodeType === 9) return "document";
        const tag = String(node.tagName || "node").toLowerCase();
        const id = node.id ? `#${node.id}` : "";
        const classes = typeof node.className === "string" && node.className.trim()
          ? `.${node.className.trim().split(/\s+/).slice(0, 4).join(".")}`
          : "";
        return `${tag}${id}${classes}`;
      }

      function debugElementPath(node, root = null) {
        if (!node || node.nodeType !== 1) return debugNodeLabel(node);
        const parts = [];
        let current = node;
        while (current && current.nodeType === 1 && current !== root && parts.length < 8) {
          let part = String(current.tagName || "node").toLowerCase();
          if (current.id) {
            part += `#${current.id}`;
            parts.unshift(part);
            break;
          }
          if (typeof current.className === "string" && current.className.trim()) {
            part += `.${current.className.trim().split(/\s+/).slice(0, 2).join(".")}`;
          }
          const parent = current.parentElement;
          if (parent) {
            const siblings = [...parent.children].filter((item) => item.tagName === current.tagName);
            if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
          }
          parts.unshift(part);
          current = parent;
        }
        return parts.join(" > ") || debugNodeLabel(node);
      }

      function debugRect(node) {
        if (!node?.getBoundingClientRect) return null;
        const rect = node.getBoundingClientRect();
        const round = (value) => Math.round(Number(value || 0) * 100) / 100;
        return {
          x: round(rect.x),
          y: round(rect.y),
          w: round(rect.width),
          h: round(rect.height),
          right: round(rect.right),
          bottom: round(rect.bottom)
        };
      }

      function debugDocumentFor(root) {
        if (root?.nodeType === 9) return root;
        return root?.ownerDocument || (typeof document !== "undefined" ? document : null);
      }

      function debugWindowFor(doc) {
        return doc?.defaultView || (typeof window !== "undefined" ? window : null);
      }

      function debugComputed(node, properties = []) {
        const doc = debugDocumentFor(node);
        const view = debugWindowFor(doc);
        if (!node || !view?.getComputedStyle) return null;
        const computed = view.getComputedStyle(node);
        const result = {};
        properties.forEach((property) => {
          result[property] = computed.getPropertyValue(property);
        });
        return result;
      }

      function debugIsLightYellowish(color) {
        const match = String(color || "").match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        if (!match) return false;
        const red = Number(match[1]);
        const green = Number(match[2]);
        const blue = Number(match[3]);
        return red > 180 && green > 130 && blue < 140;
      }

      function debugResolveRoot(targetOrOptions, options = {}) {
        const looksLikeOptions = targetOrOptions &&
          typeof targetOrOptions === "object" &&
          !targetOrOptions.nodeType &&
          (
            targetOrOptions.root ||
            targetOrOptions.rootSelector ||
            targetOrOptions.name ||
            targetOrOptions.expected ||
            targetOrOptions.probes ||
            targetOrOptions.reason
          );
        const opts = looksLikeOptions ? targetOrOptions : options;
        const explicitRoot = looksLikeOptions ? opts.root : targetOrOptions;
        const doc = debugDocumentFor(explicitRoot);
        const root = explicitRoot ||
          (opts.rootSelector && doc?.querySelector ? doc.querySelector(opts.rootSelector) : null) ||
          doc?.body ||
          null;
        return {root, doc: debugDocumentFor(root) || doc, options: opts};
      }

      function debugElementSummary(node, root = null) {
        if (!node) return null;
        const rect = debugRect(node);
        const style = debugComputed(node, [
          "display",
          "position",
          "overflow",
          "overflow-x",
          "overflow-y",
          "height",
          "min-height",
          "max-height",
          "grid-template-columns",
          "grid-template-rows",
          "background-color",
          "color"
        ]);
        return {
          label: debugNodeLabel(node),
          path: debugElementPath(node, root),
          id: node.id || "",
          className: String(node.className || "").slice(0, 180),
          text: debugSafeText(node.textContent, 120),
          rect,
          style
        };
      }

      function debugLargestElements(root, limit = 20) {
        if (!root?.querySelectorAll) return [];
        return [...root.querySelectorAll("*")]
          .map((node) => debugElementSummary(node, root))
          .filter((summary) => summary?.rect && summary.rect.w > 20 && summary.rect.h > 20)
          .sort((left, right) => right.rect.h - left.rect.h)
          .slice(0, limit);
      }

      function debugChildrenStacking(container, root) {
        if (!container?.children) return null;
        const visible = [...container.children]
          .map((child) => ({child, rect: debugRect(child)}))
          .filter((item) => item.rect && item.rect.w > 10 && item.rect.h > 10);
        const tops = [...new Set(visible.map((item) => Math.round(item.rect.y)))];
        return {
          stacked: tops.length > 2,
          uniqueTops: tops,
          children: visible.map((item) => ({
            label: debugNodeLabel(item.child),
            path: debugElementPath(item.child, root),
            top: Math.round(item.rect.y),
            height: Math.round(item.rect.h),
            width: Math.round(item.rect.w)
          }))
        };
      }

      function captureDebugEnvelope(targetOrOptions = null, options = {}) {
        const {root, doc, options: opts} = debugResolveRoot(targetOrOptions, options);
        const view = debugWindowFor(doc);
        const issues = [];
        const inspected = [];
        const skipped = [];

        function addIssue(id, severity, message, data = {}) {
          issues.push({
            id,
            severity,
            message,
            data,
            contractClause: data.contractClause || null
          });
        }

        function inspectSelector(name, selector) {
          if (!selector || !doc?.querySelector) {
            skipped.push({name, selector, reason: "selector-unavailable"});
            return null;
          }
          const node = doc.querySelector(selector);
          if (!node) {
            skipped.push({name, selector, reason: "not-found"});
            return null;
          }
          inspected.push({name, selector, label: debugNodeLabel(node)});
          return node;
        }

        const expected = opts.expected || {};
        const selectors = opts.selectors || {};
        const rootSelector = opts.rootSelector || selectors.root || "";
        const rootSummary = debugElementSummary(root, root);

        if (!root) {
          addIssue(
            "mcel.debug.root.missing",
            "critical",
            "No debug root could be resolved. MCEL cannot explain this page without a target root.",
            {rootSelector}
          );
        }

        const viewport = view ? {
          width: Number(view.innerWidth || 0),
          height: Number(view.innerHeight || 0),
          scrollY: Number(view.scrollY || 0),
          devicePixelRatio: Number(view.devicePixelRatio || 1)
        } : null;
        const documentHeight = doc?.scrollingElement?.scrollHeight || doc?.body?.scrollHeight || 0;
        const documentMetrics = {
          readyState: doc?.readyState || "",
          url: doc?.location?.href || view?.location?.href || "",
          title: doc?.title || "",
          documentHeight,
          bodyHeight: doc?.body?.scrollHeight || 0,
          elementHeight: doc?.documentElement?.scrollHeight || 0,
          viewportToDocumentRatio: viewport?.height ? Math.round((documentHeight / viewport.height) * 100) / 100 : null
        };

        if (viewport?.height && expected.maxDocumentHeightRatio && documentHeight > viewport.height * expected.maxDocumentHeightRatio) {
          addIssue(
            "mcel.debug.page.too-tall",
            "critical",
            "The document is much taller than the viewport; the app is probably leaking internal panels into page scroll.",
            {
              documentHeight,
              viewportHeight: viewport.height,
              maxRatio: expected.maxDocumentHeightRatio,
              ratio: documentMetrics.viewportToDocumentRatio,
              contractClause: "mcel.user.runtime-generation-is-discardable.v1"
            }
          );
        }

        if (rootSummary?.rect && viewport?.height && expected.maxRootHeightRatio && rootSummary.rect.h > viewport.height * expected.maxRootHeightRatio) {
          addIssue(
            "mcel.debug.root.too-tall",
            "critical",
            "The MCEL root is taller than its allowed workbench envelope.",
            {
              rootHeight: rootSummary.rect.h,
              viewportHeight: viewport.height,
              maxRatio: expected.maxRootHeightRatio,
              contractClause: "mcel.user.runtime-generation-is-discardable.v1"
            }
          );
        }

        if (expected.rootBackground && rootSummary?.style?.["background-color"] !== expected.rootBackground) {
          addIssue(
            "mcel.debug.css.not-winning",
            "critical",
            "The root computed background does not match the expected scoped style.",
            {
              actual: rootSummary.style?.["background-color"] || "",
              expected: expected.rootBackground,
              rootSelector,
              mechanism: "mcel.debug.css.not-winning.v1",
              contractClause: "mcel.user.validation-is-evidence-not-trust.v1"
            }
          );
        }

        (expected.displayGridSelectors || []).forEach((selector) => {
          const node = inspectSelector("grid", selector);
          const style = debugComputed(node, ["display", "grid-template-columns", "grid-template-rows"]);
          if (node && style?.display !== "grid") {
            addIssue(
              "mcel.debug.layout.grid-missing",
              "critical",
              "A selector that is required to be a grid is not display:grid at runtime.",
              {
                selector,
                actualDisplay: style?.display || "",
                gridTemplateColumns: style?.["grid-template-columns"] || "",
                gridTemplateRows: style?.["grid-template-rows"] || "",
                mechanism: "mcel.debug.grid-contract.v1",
                contractClause: "mcel.user.validation-is-evidence-not-trust.v1"
              }
            );
          }
        });

        (expected.stackedChildrenSelectors || []).forEach((selector) => {
          const node = inspectSelector("stacking", selector);
          const stacking = debugChildrenStacking(node, root);
          if (stacking?.stacked) {
            addIssue(
              "mcel.debug.layout.children-stacked",
              "critical",
              "The main workbench children do not share a row; this is the vertical pile failure.",
              {
                selector,
                childTops: stacking.children,
                mechanism: "mcel.debug.stacked-children.v1",
                contractClause: "mcel.user.validation-is-evidence-not-trust.v1"
              }
            );
          }
        });

        (expected.collapsedSelectors || []).forEach((selector) => {
          const node = inspectSelector("collapsed", selector);
          const rect = debugRect(node);
          const expanded = node?.dataset?.expanded === "true" || node?.getAttribute?.("aria-expanded") === "true";
          const maxHeight = Number(expected.maxCollapsedHeight || 80);
          if (node && rect && !expanded && rect.h > maxHeight) {
            addIssue(
              "mcel.debug.layout.dock-not-collapsed",
              "high",
              "A dock expected to be collapsed is consuming visible space.",
              {
                selector,
                height: rect.h,
                maxHeight,
                dataExpanded: node?.dataset?.expanded || "",
                mechanism: "mcel.debug.collapsed-dock.v1",
                contractClause: "mcel.user.runtime-generation-is-discardable.v1"
              }
            );
          }
        });

        if (expected.forbidYellowGlobalThemeLeak !== false && root?.querySelectorAll) {
          const leakingButtons = [...root.querySelectorAll("button")]
            .map((button) => {
              const style = debugComputed(button, ["background-color", "color"]);
              return {button, style};
            })
            .filter((item) => debugIsLightYellowish(item.style?.["background-color"]))
            .slice(0, 10);

          if (leakingButtons.length) {
            addIssue(
              "mcel.debug.css.global-theme-leak",
              "critical",
              "Buttons inside the MCEL root are using a global yellow theme instead of a scoped component style.",
              {
                count: leakingButtons.length,
                samples: leakingButtons.map((item) => ({
                  text: debugSafeText(item.button.textContent, 80),
                  path: debugElementPath(item.button, root),
                  backgroundColor: item.style?.["background-color"] || "",
                  color: item.style?.color || ""
                })),
                mechanism: "mcel.debug.theme-leak.v1",
                contractClause: "mcel.user.validation-is-evidence-not-trust.v1"
              }
            );
          }
        }

        const selectorSummaries = {};
        Object.entries(selectors).forEach(([name, selector]) => {
          if (name === "root") return;
          const node = inspectSelector(name, selector);
          selectorSummaries[name] = debugElementSummary(node, root);
        });

        const sourceSelector = `[${attributes.type}]`;
        const generatedSelector = `[${attributes.generated}="true"]`;
        const sourceCount = root?.querySelectorAll ? root.querySelectorAll(sourceSelector).length : 0;
        const generatedCount = root?.querySelectorAll ? root.querySelectorAll(generatedSelector).length : 0;

        return {
          kind: "mcel-debug-envelope",
          contractVersion: contract.contractVersion,
          generatedAt: debugNow(),
          captureId: debugStableHash(`${opts.name || "mcel"}:${opts.reason || ""}:${documentMetrics.url}:${documentHeight}:${issues.length}`),
          name: opts.name || "mcel-debug-capture",
          reason: opts.reason || "manual-debug-capture",
          ok: issues.length === 0,
          mechanismCount: debugMechanisms.length,
          mechanisms: listDebugMechanisms(),
          issues,
          inspected,
          skipped,
          viewport,
          documentMetrics,
          root: rootSummary,
          selectors: selectorSummaries,
          largestElements: debugLargestElements(root, Number(opts.largestLimit || 20)),
          sourceBoundary: {
            sourceSelector,
            generatedSelector,
            sourceCount,
            generatedCount,
            sourceIds: root?.querySelectorAll ? [...root.querySelectorAll(sourceSelector)].slice(0, 20).map((node) => ({
              label: debugNodeLabel(node),
              path: debugElementPath(node, root),
              sourceIndex: node.getAttribute?.(attributes.sourceIndex) || "",
              type: node.getAttribute?.(attributes.type) || "",
              component: node.getAttribute?.(attributes.componentName) || ""
            })) : []
          }
        };
      }


      function formatHtml(html) {
        return String(html || "")
          .replace(/></g, ">\n<")
          .replace(/^\s+|\s+$/g, "");
      }

      function makeRuntimeRoot(html) {
        const root = document.createElement("div");
        root.innerHTML = html;
        return root;
      }

      function runContractTests() {
        const tests = [];
        const events = [];

        function normalizeGuarantees(guarantees = []) {
          return Object.freeze([...(Array.isArray(guarantees) ? guarantees : [guarantees])]
            .map((id) => String(id || "").trim())
            .filter(Boolean));
        }

        function record(name, guarantees, passed, details = "") {
          tests.push({name, guarantees: normalizeGuarantees(guarantees), passed: Boolean(passed), details});
        }

        function test(name, guarantees, callback) {
          try {
            const result = callback();
            record(name, guarantees, result === true || result?.passed === true, result?.details || "");
          } catch (error) {
            record(name, guarantees, false, error.message);
          }
        }

        test(
          "compile inserts generated parts without touching source semantics",
          [
            "mcel.contract.source-intent-is-input.v1",
            "mcel.contract.generated-runtime-is-discardable.v1"
          ],
          () => {
            const compiled = compileSource(contract.defaultSource, {reason: "contract-test"});
            const root = makeRuntimeRoot(compiled.runtimeHtml);
            const first = root.querySelector(`[${attributes.type}]`);
            return {
              passed: Boolean(first?.querySelector(`[${attributes.generated}="true"][${attributes.part}="rail"]`)) &&
                first.getAttribute(attributes.kind) === "signal",
              details: `${compiled.sourceCount} source element(s) compiled`
            };
          }
        );

        test(
          "serializer removes all generated runtime parts",
          [
            "mcel.contract.serializer-cleans-runtime-state.v1",
            "mcel.contract.generated-runtime-is-discardable.v1"
          ],
          () => {
            const compiled = compileSource(contract.defaultSource, {reason: "serializer-test"});
            const root = makeRuntimeRoot(compiled.runtimeHtml);
            const serialized = serializeRuntimeRoot(root, {reason: "serializer-test"});
            return {
              passed: serialized.report.serializerClean && !serialized.serialized.includes(attributes.generated),
              details: `${serialized.report.removedGeneratedParts} generated part(s) removed`
            };
          }
        );

        test(
          "repair restores canonical generated parts after damage",
          [
            "mcel.contract.repair-is-schema-bounded.v1",
            "mcel.contract.generated-runtime-is-discardable.v1"
          ],
          () => {
            const compiled = compileSource(contract.defaultSource, {reason: "repair-test"});
            const root = makeRuntimeRoot(compiled.runtimeHtml);
            damageRuntimeRoot(root);
            const repair = repairRuntimeRoot(root, {reason: "repair-test"});
            return {
              passed: repair.repaired > 0 && !debuggerStateFor(root.querySelector(`[${attributes.type}]`), root).missingParts.length,
              details: `${repair.repaired} element(s) repaired`
            };
          }
        );

        test(
          "schema normalizes invalid trait values",
          ["mcel.contract.source-intent-is-input.v1"],
          () => {
            const compiled = compileSource(`<section data-mc="nonsense" data-mc-kind="bogus" data-mc-flow="sideways"><h2>Bad</h2></section>`, {reason: "schema-test"});
            const root = makeRuntimeRoot(compiled.runtimeHtml);
            const first = root.querySelector(`[${attributes.type}]`);
            return {
              passed: first.getAttribute(attributes.type) === defaults.type &&
                first.getAttribute(attributes.kind) === defaults.kind &&
                first.getAttribute(attributes.flow) === defaults.flow,
              details: compiled.events.filter((event) => event.level === "warning").map((event) => event.code).join(", ")
            };
          }
        );

        test(
          "a11y report fails unlabeled source widgets",
          ["mcel.contract.validation-is-reporting-not-trust.v1"],
          () => {
            const compiled = compileSource(`<section data-mc="panel"><p>No heading.</p></section>`, {reason: "a11y-test"});
            const root = makeRuntimeRoot(compiled.runtimeHtml);
            const report = computeA11y(root);
            return {
              passed: report.a11yValid === false && report.warnings.length > 0,
              details: report.warnings.join("; ")
            };
          }
        );

        test(
          "relations resolve when data-mc-connects points at a smart target",
          ["mcel.contract.source-intent-is-input.v1"],
          () => {
            const compiled = compileSource(`<section data-mc="panel" data-mc-connects="target"><h2>Source</h2></section><section id="target" data-mc="panel"><h2>Target</h2></section>`, {reason: "relation-test"});
            const root = makeRuntimeRoot(compiled.runtimeHtml);
            const first = root.querySelector(`[${attributes.type}]`);
            return {
              passed: first.getAttribute(attributes.relation) === "resolved" && first.getAttribute(attributes.relationCount) === "1",
              details: `relation=${first.getAttribute(attributes.relation)}`
            };
          }
        );

        test(
          "platform source policies survive while runtime proof facts are stripped",
          [
            "mcel.contract.serializer-cleans-runtime-state.v1",
            "mcel.contract.validation-is-reporting-not-trust.v1"
          ],
          () => {
            const source = `<section data-mc="panel" data-mc-component="ProofCard" data-mc-component-kind="component" data-mc-state-owner="view" data-mc-state-policy="derived" data-mc-query="proof.cards" data-mc-cache-policy="stale-while-revalidate" data-mc-submit="proof.save" data-mc-validation="schema" data-mc-action="save-proof" data-mc-render="island" data-mc-hydration="visible" data-mc-a11y-policy="strict" data-mc-performance-budget="small"><h2>Platform</h2></section>`;
            const compiled = compileSource(source, {reason: "platform-serializer-test"});
            const root = makeRuntimeRoot(compiled.runtimeHtml);
            const first = root.querySelector(`[${attributes.type}]`);
            first.setAttribute(attributes.componentLaw, "true");
            first.setAttribute(attributes.stateLaw, "owned");
            first.setAttribute(attributes.dataLaw, "query");
            first.setAttribute(attributes.formLaw, "valid");
            first.setAttribute(attributes.actionLaw, "safe");
            first.setAttribute(attributes.renderLaw, "island");
            first.setAttribute(attributes.a11yLaw, "strict");
            first.setAttribute(attributes.performanceLaw, "small");
            first.setAttribute(attributes.proofTier, "platform-spine");
            first.setAttribute(attributes.semanticRisk, "low");
            const serialized = serializeRuntimeRoot(root, {reason: "platform-serializer-test"});
            return {
              passed: serialized.report.serializerClean &&
                serialized.serialized.includes(attributes.componentName) &&
                serialized.serialized.includes(attributes.renderMode) &&
                serialized.serialized.includes(attributes.a11yPolicy) &&
                !serialized.serialized.includes(attributes.componentLaw) &&
                !serialized.serialized.includes(attributes.performanceLaw) &&
                !serialized.serialized.includes(attributes.semanticRisk),
              details: `serialized=${serialized.serialized}`
            };
          }
        );

        test(
          "layout source policies survive while observed geometry is stripped",
          [
            "mcel.contract.serializer-cleans-runtime-state.v1",
            "mcel.contract.browser-facts-are-runtime-only.v1"
          ],
          () => {
            const source = `<section data-mc="panel" data-mc-overflow-policy="delegate" data-mc-scroll-policy="external" data-mc-size-policy="fluid"><h2>Layout</h2></section>`;
            const compiled = compileSource(source, {reason: "layout-serializer-test"});
            const root = makeRuntimeRoot(compiled.runtimeHtml);
            const first = root.querySelector(`[${attributes.type}]`);
            first.setAttribute(attributes.layoutLaw, "true");
            first.setAttribute(attributes.overflowComputed, "delegated");
            first.setAttribute(attributes.scrollNeeded, "true");
            first.setAttribute(attributes.scrollOwner, "parent");
            first.setAttribute(attributes.layoutPressure, "high");
            first.setAttribute(attributes.geometryProof, "fail");
            const serialized = serializeRuntimeRoot(root, {reason: "layout-serializer-test"});
            return {
              passed: serialized.report.serializerClean &&
                serialized.serialized.includes(attributes.overflowPolicy) &&
                serialized.serialized.includes(attributes.scrollPolicy) &&
                !serialized.serialized.includes(attributes.overflowComputed) &&
                !serialized.serialized.includes(attributes.geometryProof),
              details: `serialized=${serialized.serialized}`
            };
          }
        );

        const passed = tests.filter((item) => item.passed).length;
        const failed = tests.length - passed;
        const executableGuarantees = [...(contract.contractGuarantees || [])]
          .filter((guarantee) => guarantee.status === "executable")
          .map((guarantee) => guarantee.id);
        const guaranteeResults = executableGuarantees.map((id) => {
          const supportingTests = tests.filter((testResult) => testResult.guarantees.includes(id));
          const guaranteePassed = supportingTests.length > 0 && supportingTests.every((testResult) => testResult.passed);
          return {
            id,
            passed: guaranteePassed,
            supportingTests: supportingTests.map((testResult) => testResult.name)
          };
        });
        const uncoveredGuarantees = guaranteeResults
          .filter((result) => result.supportingTests.length === 0)
          .map((result) => result.id);
        const failedGuarantees = guaranteeResults
          .filter((result) => !result.passed)
          .map((result) => result.id);

        if (failed || failedGuarantees.length || uncoveredGuarantees.length) {
          logEvent(
            events,
            "warning",
            "tests",
            "MCEL_CONTRACT_GUARANTEES_FAILED",
            `${failed} test(s), ${failedGuarantees.length} guarantee(s), and ${uncoveredGuarantees.length} uncovered guarantee(s) require attention.`
          );
        } else {
          logEvent(
            events,
            "success",
            "tests",
            "MCEL_CONTRACT_GUARANTEES_PASSED",
            `${passed} MCEL contract test(s) covered ${guaranteeResults.length} executable guarantee(s).`
          );
        }
        return {passed, failed, tests, guaranteeResults, failedGuarantees, uncoveredGuarantees, events};
      }

      return Object.freeze({
        compileSource,
        compileDocument,
        parseSource,
        serializeRuntimeRoot,
        repairRuntimeRoot,
        damageRuntimeRoot,
        computeA11y,
        debuggerStateFor,
        captureDebugEnvelope,
        listDebugMechanisms,
        stripGeneratedParts,
        formatHtml,
        normalizeValue,
        runContractTests
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabEngine = McelLabEngine;
    }
