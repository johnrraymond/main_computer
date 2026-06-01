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


      function normalizedTokens(value) {
        return String(value || "")
          .toLowerCase()
          .split(/[\s,;|]+/)
          .map((token) => token.trim())
          .filter(Boolean);
      }

      function nullifyTokens(element) {
        return normalizedTokens(element?.getAttribute?.(attributes.nullify || "mcel-nullify"));
      }

      function nullifiedAttributeForToken(token) {
        const alias = {
          type: attributes.type,
          region: attributes.type,
          component: attributes.type,
          "data-mc": attributes.type,
          kind: attributes.kind,
          "data-mc-kind": attributes.kind,
          flow: attributes.flow,
          "data-mc-flow": attributes.flow,
          rank: attributes.rank,
          "data-mc-rank": attributes.rank,
          state: attributes.state,
          "data-mc-state": attributes.state,
          density: attributes.density,
          "data-mc-density": attributes.density,
          "size-policy": attributes.sizePolicy,
          "data-mc-size-policy": attributes.sizePolicy,
          "overflow-policy": attributes.overflowPolicy,
          "data-mc-overflow-policy": attributes.overflowPolicy,
          "scroll-policy": attributes.scrollPolicy,
          "data-mc-scroll-policy": attributes.scrollPolicy,
          slot: attributes.slot,
          "data-mc-slot": attributes.slot,
          action: attributes.action,
          "data-mc-action": attributes.action,
          "event-policy": attributes.eventPolicy,
          "data-mc-event-policy": attributes.eventPolicy,
          submit: attributes.submit,
          "data-mc-submit": attributes.submit,
          validation: attributes.validation,
          "data-mc-validation": attributes.validation,
          "dirty-policy": attributes.dirtyPolicy,
          "data-mc-dirty-policy": attributes.dirtyPolicy,
          "error-policy": attributes.errorPolicy,
          "data-mc-error-policy": attributes.errorPolicy
        };
        return alias[token] || (String(token || "").startsWith("data-mc-") ? token : "");
      }

      function nullifiesAttribute(element, attribute) {
        const tokens = nullifyTokens(element);
        if (!tokens.length) return false;
        if (tokens.some((token) => ["all", "defaults", "enrichment", "enrichments"].includes(token))) return true;
        if (tokens.some((token) => ["traits", "region-traits"].includes(token)) && [attributes.kind, attributes.flow, attributes.rank, attributes.state, attributes.density, attributes.sizePolicy, attributes.overflowPolicy, attributes.scrollPolicy].includes(attribute)) return true;
        if (tokens.includes("form") && [attributes.submit, attributes.validation, attributes.dirtyPolicy, attributes.errorPolicy].includes(attribute)) return true;
        if (tokens.includes("action") && [attributes.action, attributes.eventPolicy].includes(attribute)) return true;
        return tokens.some((token) => nullifiedAttributeForToken(token) === attribute);
      }

      function nullifiesGeneratedPart(element, part) {
        const tokens = nullifyTokens(element);
        if (!tokens.length) return false;
        return tokens.some((token) => (
          ["all", "defaults", "enrichment", "enrichments", "generated", "generated-parts", "parts"].includes(token) ||
          token === part ||
          token === `part:${part}` ||
          token === `generated:${part}` ||
          token === `data-mc-part:${part}` ||
          token === `data-mc-part=${part}`
        ));
      }

      function applyNullificationToElement(element) {
        [
          attributes.type,
          attributes.kind,
          attributes.flow,
          attributes.rank,
          attributes.state,
          attributes.density,
          attributes.sizePolicy,
          attributes.overflowPolicy,
          attributes.scrollPolicy,
          attributes.slot,
          attributes.submit,
          attributes.validation,
          attributes.dirtyPolicy,
          attributes.errorPolicy,
          attributes.action,
          attributes.eventPolicy
        ].filter(Boolean).forEach((attribute) => {
          if (nullifiesAttribute(element, attribute)) element.removeAttribute(attribute);
        });
      }

      function applyNullificationToTree(root) {
        root.querySelectorAll?.(`[${attributes.nullify || "mcel-nullify"}]`).forEach(applyNullificationToElement);
        return root;
      }

      function generatedPartsFor(element, elementSchema) {
        return [...(elementSchema.generatedParts || [])].filter((part) => !nullifiesGeneratedPart(element, part));
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
        return [...(docOrRoot?.querySelectorAll?.(`[${attributes.type}]`) || [])]
          .filter((element) => !nullifiesAttribute(element, attributes.type));
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
        if (nullifiesAttribute(element, attribute)) {
          element.removeAttribute(attribute);
          return "";
        }
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
        if (nullifiesAttribute(element, attribute)) {
          element.removeAttribute(attribute);
          return "";
        }
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
        generatedPartsFor(element, elementSchema).slice().reverse().forEach((part) => {
          element.insertBefore(createGeneratedPart(part, element), element.firstChild);
        });
        if (removed) {
          logEvent(events, "repair", "part-manager", "MCEL_PARTS_REBUILT", `Rebuilt ${generatedPartsFor(element, elementSchema).length} generated part(s) after removing ${removed} stale part(s) during ${reason}.`);
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

        generatedPartsFor(element, elementSchema).slice().reverse().forEach((part) => {
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
        applyNullificationToTree(doc.body);
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

        function record(name, passed, details = "") {
          tests.push({name, passed: Boolean(passed), details});
        }

        function test(name, callback) {
          try {
            const result = callback();
            record(name, result === true || result?.passed === true, result?.details || "");
          } catch (error) {
            record(name, false, error.message);
          }
        }

        test("compile inserts generated parts without touching source semantics", () => {
          const compiled = compileSource(contract.defaultSource, {reason: "contract-test"});
          const root = makeRuntimeRoot(compiled.runtimeHtml);
          const first = root.querySelector(`[${attributes.type}]`);
          return {
            passed: Boolean(first?.querySelector(`[${attributes.generated}="true"][${attributes.part}="rail"]`)) &&
              first.getAttribute(attributes.kind) === "signal",
            details: `${compiled.sourceCount} source element(s) compiled`
          };
        });

        test("serializer removes all generated runtime parts", () => {
          const compiled = compileSource(contract.defaultSource, {reason: "serializer-test"});
          const root = makeRuntimeRoot(compiled.runtimeHtml);
          const serialized = serializeRuntimeRoot(root, {reason: "serializer-test"});
          return {
            passed: serialized.report.serializerClean && !serialized.serialized.includes(attributes.generated),
            details: `${serialized.report.removedGeneratedParts} generated part(s) removed`
          };
        });

        test("repair restores canonical generated parts after damage", () => {
          const compiled = compileSource(contract.defaultSource, {reason: "repair-test"});
          const root = makeRuntimeRoot(compiled.runtimeHtml);
          damageRuntimeRoot(root);
          const repair = repairRuntimeRoot(root, {reason: "repair-test"});
          return {
            passed: repair.repaired > 0 && !debuggerStateFor(root.querySelector(`[${attributes.type}]`), root).missingParts.length,
            details: `${repair.repaired} element(s) repaired`
          };
        });

        test("schema normalizes invalid trait values", () => {
          const compiled = compileSource(`<section data-mc="nonsense" data-mc-kind="bogus" data-mc-flow="sideways"><h2>Bad</h2></section>`, {reason: "schema-test"});
          const root = makeRuntimeRoot(compiled.runtimeHtml);
          const first = root.querySelector(`[${attributes.type}]`);
          return {
            passed: first.getAttribute(attributes.type) === defaults.type &&
              first.getAttribute(attributes.kind) === defaults.kind &&
              first.getAttribute(attributes.flow) === defaults.flow,
            details: compiled.events.filter((event) => event.level === "warning").map((event) => event.code).join(", ")
          };
        });

        test("a11y report fails unlabeled source widgets", () => {
          const compiled = compileSource(`<section data-mc="panel"><p>No heading.</p></section>`, {reason: "a11y-test"});
          const root = makeRuntimeRoot(compiled.runtimeHtml);
          const report = computeA11y(root);
          return {
            passed: report.a11yValid === false && report.warnings.length > 0,
            details: report.warnings.join("; ")
          };
        });

        test("relations resolve when data-mc-connects points at a smart target", () => {
          const compiled = compileSource(`<section data-mc="panel" data-mc-connects="target"><h2>Source</h2></section><section id="target" data-mc="panel"><h2>Target</h2></section>`, {reason: "relation-test"});
          const root = makeRuntimeRoot(compiled.runtimeHtml);
          const first = root.querySelector(`[${attributes.type}]`);
          return {
            passed: first.getAttribute(attributes.relation) === "resolved" && first.getAttribute(attributes.relationCount) === "1",
            details: `relation=${first.getAttribute(attributes.relation)}`
          };
        });

        test("platform source policies survive while runtime proof facts are stripped", () => {
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
        });

        test("layout source policies survive while observed geometry is stripped", () => {
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
        });

        const passed = tests.filter((item) => item.passed).length;
        const failed = tests.length - passed;
        if (failed) {
          logEvent(events, "warning", "tests", "MCEL_CONTRACT_TESTS_FAILED", `${failed} MCEL contract test(s) failed.`);
        } else {
          logEvent(events, "success", "tests", "MCEL_CONTRACT_TESTS_PASSED", `${passed} MCEL contract test(s) passed.`);
        }
        return {passed, failed, tests, events};
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
        stripGeneratedParts,
        formatHtml,
        normalizeValue,
        runContractTests
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabEngine = McelLabEngine;
    }
