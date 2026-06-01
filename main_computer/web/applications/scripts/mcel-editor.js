    var McelLabEditor = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const engine = typeof McelLabEngine !== "undefined" ? McelLabEngine : window.McelLabEngine;
      const {attributes, defaults, runtimeOwnedAttributes, runtimeOwnedClasses, blockTemplates, schema} = contract;
      const editorCatalog = contract.editorCatalog?.() || {};
      const nullifyAttribute = attributes.nullify || "mcel-nullify";
      const sourceContractAttributes = Object.freeze([
        attributes.type,
        attributes.kind,
        attributes.flow,
        attributes.rank,
        attributes.state,
        attributes.density,
        attributes.sizePolicy,
        attributes.overflowPolicy,
        attributes.scrollPolicy,
        attributes.componentName,
        attributes.componentKind,
        attributes.slot,
        attributes.propContract,
        attributes.stateOwner,
        attributes.stateScope,
        attributes.statePolicy,
        attributes.query,
        attributes.cachePolicy,
        attributes.mutation,
        attributes.syncPolicy,
        attributes.submit,
        attributes.validation,
        attributes.dirtyPolicy,
        attributes.errorPolicy,
        attributes.action,
        attributes.target,
        attributes.swapPolicy,
        attributes.eventPolicy,
        attributes.route,
        attributes.renderMode,
        attributes.hydration,
        attributes.islandPolicy,
        attributes.focusPolicy,
        attributes.a11yPolicy,
        attributes.performanceBudget,
        attributes.securityPolicy,
        attributes.words,
        attributes.connects
      ].filter(Boolean));


      function parseSource(source) {
        const parser = new DOMParser();
        return parser.parseFromString(String(source || ""), "text/html");
      }

      function sourceElements(docOrRoot) {
        return [...(docOrRoot?.querySelectorAll?.(`[${attributes.type}]`) || [])];
      }

      function serializeBody(docOrRoot) {
        const body = docOrRoot.body || docOrRoot;
        return engine.formatHtml((body.innerHTML || "").trim());
      }


      function normalizedTokens(value) {
        return String(value || "")
          .toLowerCase()
          .split(/[\s,;|]+/)
          .map((token) => token.trim())
          .filter(Boolean);
      }

      function slugFromText(text, fallback = "action") {
        const slug = String(text || "")
          .toLowerCase()
          .replace(/&/g, " and ")
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-+|-+$/g, "")
          .slice(0, 48);
        return slug || fallback;
      }

      function directChildren(element, selector) {
        return [...(element?.children || [])].filter((child) => child.matches?.(selector));
      }

      function firstOfTypeAmongSiblings(element, selector) {
        const parent = element?.parentElement;
        if (!parent) return false;
        return directChildren(parent, selector)[0] === element;
      }

      function firstHeading(parent) {
        return parent?.querySelector?.("h1,h2,h3,h4,h5,h6") || null;
      }

      function isBefore(candidate, target) {
        return Boolean(candidate?.compareDocumentPosition?.(target) & 4);
      }

      function hasActionControl(element) {
        return Boolean(element?.querySelector?.("a[href],button"));
      }

      function textLooksLikeMeta(element) {
        const text = String(element?.textContent || "").trim();
        if (!text) return false;
        if (text.length > 96) return false;
        if (/[·•|]/.test(text)) return true;
        return /\b(open|closed|today|hours|cluster|updated|posted|nearby|local)\b/i.test(text);
      }

      function defaultRegionAttributes(element) {
        const tag = String(element?.tagName || "").toLowerCase();
        const values = {};
        if (tag === "main") {
          Object.assign(values, {
            [attributes.type]: "smart-region",
            [attributes.kind]: "article",
            [attributes.flow]: "stack",
            [attributes.rank]: "primary",
            [attributes.state]: "live",
            [attributes.density]: "calm",
            [attributes.sizePolicy]: "fluid",
            [attributes.overflowPolicy]: "delegate",
            [attributes.scrollPolicy]: "external"
          });
        } else if (tag === "form") {
          Object.assign(values, {
            [attributes.type]: "smart-region",
            [attributes.kind]: "work",
            [attributes.flow]: "stack",
            [attributes.rank]: "secondary",
            [attributes.state]: "draft",
            [attributes.density]: "auto",
            [attributes.sizePolicy]: "adaptive",
            [attributes.overflowPolicy]: "contain",
            [attributes.scrollPolicy]: "auto",
            [attributes.validation]: "native",
            [attributes.dirtyPolicy]: "warn",
            [attributes.errorPolicy]: "inline"
          });
          if (!element.getAttribute(attributes.submit)) {
            values[attributes.submit] = slugFromText(element.getAttribute("name") || element.getAttribute("id") || "form.submit", "form.submit");
          }
        } else if (tag === "nav") {
          Object.assign(values, {
            [attributes.type]: "command-row",
            [attributes.kind]: "work",
            [attributes.flow]: "forward",
            [attributes.rank]: "minor",
            [attributes.state]: "idle",
            [attributes.density]: "compressed",
            [attributes.sizePolicy]: "intrinsic",
            [attributes.overflowPolicy]: "clip",
            [attributes.scrollPolicy]: "never"
          });
        } else if (tag === "article") {
          Object.assign(values, {
            [attributes.type]: "panel",
            [attributes.kind]: "article",
            [attributes.flow]: "stack",
            [attributes.rank]: "secondary",
            [attributes.state]: "idle",
            [attributes.density]: "dense",
            [attributes.sizePolicy]: "adaptive",
            [attributes.overflowPolicy]: "contain",
            [attributes.scrollPolicy]: "auto"
          });
        } else if (tag === "aside") {
          Object.assign(values, {
            [attributes.type]: "panel",
            [attributes.kind]: "signal",
            [attributes.flow]: "stack",
            [attributes.rank]: "minor",
            [attributes.state]: "idle",
            [attributes.density]: "dense",
            [attributes.sizePolicy]: "adaptive",
            [attributes.overflowPolicy]: "contain",
            [attributes.scrollPolicy]: "auto"
          });
        } else if (tag === "section") {
          const articleChildren = directChildren(element, "article").length;
          const heroCandidate = firstOfTypeAmongSiblings(element, "section") && Boolean(element.querySelector?.("h1"));
          if (articleChildren >= 2) {
            Object.assign(values, {
              [attributes.type]: "feed",
              [attributes.kind]: "signal",
              [attributes.flow]: "stack",
              [attributes.rank]: "secondary",
              [attributes.state]: "live",
              [attributes.density]: "auto",
              [attributes.sizePolicy]: "fluid",
              [attributes.overflowPolicy]: "expand",
              [attributes.scrollPolicy]: "never"
            });
          } else {
            Object.assign(values, {
              [attributes.type]: "panel",
              [attributes.kind]: heroCandidate ? "hero" : "signal",
              [attributes.flow]: heroCandidate && hasActionControl(element) ? "split" : "forward",
              [attributes.rank]: heroCandidate ? "primary" : "secondary",
              [attributes.state]: heroCandidate ? "live" : "idle",
              [attributes.density]: heroCandidate ? "calm" : "auto",
              [attributes.sizePolicy]: heroCandidate ? "fluid" : "adaptive",
              [attributes.overflowPolicy]: heroCandidate ? "expand" : "contain",
              [attributes.scrollPolicy]: heroCandidate ? "never" : "auto"
            });
          }
        }
        return values;
      }

      function defaultSlotAttributes(element) {
        const tag = String(element?.tagName || "").toLowerCase();
        if (/^h[1-6]$/.test(tag)) {
          return {[attributes.slot]: "title"};
        }
        if (tag === "p") {
          if (hasActionControl(element)) return {[attributes.slot]: "actions"};
          const heading = firstHeading(element.parentElement);
          if ((heading && isBefore(element, heading)) || textLooksLikeMeta(element)) {
            return {[attributes.slot]: "meta"};
          }
          return {[attributes.slot]: "body"};
        }
        if (["figure", "picture", "img", "video"].includes(tag)) {
          return {[attributes.slot]: "media"};
        }
        return {};
      }

      function defaultActionAttributes(element) {
        const tag = String(element?.tagName || "").toLowerCase();
        if (tag === "a" && element.hasAttribute("href")) {
          return {
            [attributes.action]: slugFromText(element.textContent || element.getAttribute("href"), "link"),
            [attributes.eventPolicy]: "audited"
          };
        }
        if (tag === "button") {
          return {
            [attributes.action]: slugFromText(element.textContent || element.getAttribute("type"), "button"),
            [attributes.eventPolicy]: "audited"
          };
        }
        return {};
      }

      function defaultsForElement(element) {
        return {
          ...defaultRegionAttributes(element),
          ...defaultSlotAttributes(element),
          ...defaultActionAttributes(element)
        };
      }

      function authoredSourceAttributes(element) {
        const authored = {};
        sourceContractAttributes.forEach((attribute) => {
          if (attribute && element.hasAttribute(attribute)) {
            authored[attribute] = element.getAttribute(attribute);
          }
        });
        return authored;
      }

      function sourceAttributeForToken(token, merged = {}) {
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
        if (alias[token]) return alias[token];
        if (sourceContractAttributes.includes(token)) return token;
        if ((editorCatalog.slots || []).includes(token) && merged[attributes.slot] === token) return attributes.slot;
        if ((editorCatalog.schemaTypes || []).includes(token) && merged[attributes.type] === token) return attributes.type;
        return "";
      }

      function nullifiedAttributes(element, merged = {}) {
        const tokens = normalizedTokens(element?.getAttribute?.(nullifyAttribute));
        const nullified = new Set();
        tokens.forEach((token) => {
          if (["all", "defaults", "enrichment", "enrichments"].includes(token)) {
            sourceContractAttributes.forEach((attribute) => nullified.add(attribute));
            return;
          }
          if (["traits", "region-traits"].includes(token)) {
            [attributes.kind, attributes.flow, attributes.rank, attributes.state, attributes.density, attributes.sizePolicy, attributes.overflowPolicy, attributes.scrollPolicy].forEach((attribute) => nullified.add(attribute));
            return;
          }
          if (token === "form") {
            [attributes.submit, attributes.validation, attributes.dirtyPolicy, attributes.errorPolicy].forEach((attribute) => nullified.add(attribute));
            return;
          }
          if (token === "action") {
            [attributes.action, attributes.eventPolicy].forEach((attribute) => nullified.add(attribute));
            return;
          }
          const attribute = sourceAttributeForToken(token, merged);
          if (attribute) nullified.add(attribute);
        });
        return nullified;
      }

      function applyNullification(element, merged) {
        nullifiedAttributes(element, merged).forEach((attribute) => {
          delete merged[attribute];
          element.removeAttribute(attribute);
        });
        return merged;
      }

      function mergeDefaultEnrichment(element) {
        const merged = {...defaultsForElement(element), ...authoredSourceAttributes(element)};
        applyNullification(element, merged);
        Object.entries(merged).forEach(([attribute, value]) => {
          const normalized = String(value ?? "").trim();
          if (attribute && normalized) {
            element.setAttribute(attribute, normalized);
          }
        });
      }

      function applyDefaultEnrichment(root) {
        if (root?.nodeType === 1) mergeDefaultEnrichment(root);
        root.querySelectorAll?.("*").forEach((element) => mergeDefaultEnrichment(element));
        return root;
      }

      function removeRuntimeState(element) {
        runtimeOwnedAttributes.forEach((attribute) => element.removeAttribute(attribute));
        element.removeAttribute("style");
        [...element.classList].forEach((className) => {
          if (runtimeOwnedClasses.includes(className) || className.startsWith("mc-")) {
            element.classList.remove(className);
          }
        });
        if (!element.getAttribute("class")) element.removeAttribute("class");
      }

      function sanitizeRoot(root) {
        root.querySelectorAll?.(`[${attributes.generated}="true"]`).forEach((node) => node.remove());
        sourceElements(root).forEach(removeRuntimeState);
        root.querySelectorAll?.("[contenteditable], [data-gjs-type], [data-highlightable], [data-gjs-highlightable]").forEach((node) => {
          node.removeAttribute("contenteditable");
          node.removeAttribute("data-gjs-type");
          node.removeAttribute("data-highlightable");
          node.removeAttribute("data-gjs-highlightable");
        });
        applyDefaultEnrichment(root);
        return root;
      }

      function sanitizeEditorHtml(html) {
        const doc = parseSource(html);
        sanitizeRoot(doc.body);
        return serializeBody(doc);
      }

      function canonicalSource(source) {
        const doc = parseSource(source);
        sanitizeRoot(doc.body);
        return serializeBody(doc);
      }

      function sourceList(source) {
        const doc = parseSource(source);
        sanitizeRoot(doc.body);
        return sourceElements(doc.body).map((element, index) => ({
          index,
          type: element.getAttribute(attributes.type) || defaults.type,
          kind: element.getAttribute(attributes.kind) || defaults.kind,
          flow: element.getAttribute(attributes.flow) || defaults.flow,
          rank: element.getAttribute(attributes.rank) || defaults.rank,
          state: element.getAttribute(attributes.state) || defaults.state,
          density: element.getAttribute(attributes.density) || defaults.density,
          sizePolicy: element.getAttribute(attributes.sizePolicy) || defaults.sizePolicy,
          overflowPolicy: element.getAttribute(attributes.overflowPolicy) || defaults.overflowPolicy,
          scrollPolicy: element.getAttribute(attributes.scrollPolicy) || defaults.scrollPolicy,
          componentName: element.getAttribute(attributes.componentName) || "",
          componentKind: element.getAttribute(attributes.componentKind) || "",
          stateOwner: element.getAttribute(attributes.stateOwner) || "",
          stateScope: element.getAttribute(attributes.stateScope) || "",
          statePolicy: element.getAttribute(attributes.statePolicy) || "",
          query: element.getAttribute(attributes.query) || "",
          cachePolicy: element.getAttribute(attributes.cachePolicy) || "",
          mutation: element.getAttribute(attributes.mutation) || "",
          syncPolicy: element.getAttribute(attributes.syncPolicy) || "",
          submit: element.getAttribute(attributes.submit) || "",
          validation: element.getAttribute(attributes.validation) || "",
          dirtyPolicy: element.getAttribute(attributes.dirtyPolicy) || "",
          errorPolicy: element.getAttribute(attributes.errorPolicy) || "",
          action: element.getAttribute(attributes.action) || "",
          target: element.getAttribute(attributes.target) || "",
          swapPolicy: element.getAttribute(attributes.swapPolicy) || "",
          eventPolicy: element.getAttribute(attributes.eventPolicy) || "",
          route: element.getAttribute(attributes.route) || "",
          renderMode: element.getAttribute(attributes.renderMode) || "",
          hydration: element.getAttribute(attributes.hydration) || "",
          islandPolicy: element.getAttribute(attributes.islandPolicy) || "",
          focusPolicy: element.getAttribute(attributes.focusPolicy) || "",
          a11yPolicy: element.getAttribute(attributes.a11yPolicy) || "",
          performanceBudget: element.getAttribute(attributes.performanceBudget) || "",
          securityPolicy: element.getAttribute(attributes.securityPolicy) || "",
          words: element.getAttribute(attributes.words) || "",
          connects: element.getAttribute(attributes.connects) || "",
          label: element.querySelector("h1,h2,h3,h4,h5,h6")?.textContent?.trim() || `MCEL widget ${index + 1}`
        }));
      }

      function normalizeRef(ref, source) {
        const list = sourceList(source);
        const requested = Number(ref?.index ?? ref ?? 0);
        const index = Math.min(Math.max(Number.isFinite(requested) ? requested : 0, 0), Math.max(list.length - 1, 0));
        return {index, total: list.length};
      }

      function elementForRef(doc, ref) {
        const elements = sourceElements(doc.body || doc);
        if (!elements.length) return {element: null, index: -1, total: 0};
        const requested = Number(ref?.index ?? ref ?? 0);
        const index = Math.min(Math.max(Number.isFinite(requested) ? requested : 0, 0), elements.length - 1);
        return {element: elements[index], index, total: elements.length};
      }

      function readTraits(source, ref = {index: 0}) {
        const doc = parseSource(source);
        sanitizeRoot(doc.body);
        const {element, index, total} = elementForRef(doc, ref);
        if (!element) {
          return {found: false, index: -1, total: 0};
        }
        const type = element.getAttribute(attributes.type) || defaults.type;
        const elementSchema = schema[type] || schema[defaults.type];
        return {
          found: true,
          index,
          total,
          type,
          kind: element.getAttribute(attributes.kind) || defaults.kind,
          flow: element.getAttribute(attributes.flow) || defaults.flow,
          rank: element.getAttribute(attributes.rank) || defaults.rank,
          state: element.getAttribute(attributes.state) || defaults.state,
          density: element.getAttribute(attributes.density) || defaults.density,
          sizePolicy: element.getAttribute(attributes.sizePolicy) || defaults.sizePolicy,
          overflowPolicy: element.getAttribute(attributes.overflowPolicy) || defaults.overflowPolicy,
          scrollPolicy: element.getAttribute(attributes.scrollPolicy) || defaults.scrollPolicy,
          componentName: element.getAttribute(attributes.componentName) || "",
          componentKind: element.getAttribute(attributes.componentKind) || "",
          stateOwner: element.getAttribute(attributes.stateOwner) || "",
          stateScope: element.getAttribute(attributes.stateScope) || "",
          statePolicy: element.getAttribute(attributes.statePolicy) || "",
          query: element.getAttribute(attributes.query) || "",
          cachePolicy: element.getAttribute(attributes.cachePolicy) || "",
          mutation: element.getAttribute(attributes.mutation) || "",
          syncPolicy: element.getAttribute(attributes.syncPolicy) || "",
          submit: element.getAttribute(attributes.submit) || "",
          validation: element.getAttribute(attributes.validation) || "",
          dirtyPolicy: element.getAttribute(attributes.dirtyPolicy) || "",
          errorPolicy: element.getAttribute(attributes.errorPolicy) || "",
          action: element.getAttribute(attributes.action) || "",
          target: element.getAttribute(attributes.target) || "",
          swapPolicy: element.getAttribute(attributes.swapPolicy) || "",
          eventPolicy: element.getAttribute(attributes.eventPolicy) || "",
          route: element.getAttribute(attributes.route) || "",
          renderMode: element.getAttribute(attributes.renderMode) || "",
          hydration: element.getAttribute(attributes.hydration) || "",
          islandPolicy: element.getAttribute(attributes.islandPolicy) || "",
          focusPolicy: element.getAttribute(attributes.focusPolicy) || "",
          a11yPolicy: element.getAttribute(attributes.a11yPolicy) || "",
          performanceBudget: element.getAttribute(attributes.performanceBudget) || "",
          securityPolicy: element.getAttribute(attributes.securityPolicy) || "",
          words: element.getAttribute(attributes.words) || "",
          connects: element.getAttribute(attributes.connects) || "",
          options: {
            kinds: [...elementSchema.allowedKinds],
            flows: [...elementSchema.allowedFlows],
            ranks: [...elementSchema.allowedRanks],
            states: [...elementSchema.allowedStates],
            densities: [...elementSchema.allowedDensities],
            sizePolicies: [...(elementSchema.allowedSizePolicies || ["adaptive"])],
            overflowPolicies: [...(elementSchema.allowedOverflowPolicies || ["contain"])],
            scrollPolicies: [...(elementSchema.allowedScrollPolicies || ["auto"])],
            componentKinds: [...(elementSchema.allowedComponentKinds || ["component"])],
            stateOwners: [...(elementSchema.allowedStateOwners || ["none"])],
            statePolicies: [...(elementSchema.allowedStatePolicies || ["local"])],
            cachePolicies: [...(elementSchema.allowedCachePolicies || ["none"])],
            syncPolicies: [...(elementSchema.allowedSyncPolicies || ["none"])],
            validationPolicies: [...(elementSchema.allowedValidationPolicies || ["none"])],
            dirtyPolicies: [...(elementSchema.allowedDirtyPolicies || ["none"])],
            errorPolicies: [...(elementSchema.allowedErrorPolicies || ["inline"])],
            swapPolicies: [...(elementSchema.allowedSwapPolicies || ["none"])],
            eventPolicies: [...(elementSchema.allowedEventPolicies || ["none"])],
            renderModes: [...(elementSchema.allowedRenderModes || ["client"])],
            hydrationPolicies: [...(elementSchema.allowedHydrationPolicies || ["none"])],
            islandPolicies: [...(elementSchema.allowedIslandPolicies || ["none"])],
            focusPolicies: [...(elementSchema.allowedFocusPolicies || ["auto"])],
            a11yPolicies: [...(elementSchema.allowedA11yPolicies || ["auto"])],
            performanceBudgets: [...(elementSchema.allowedPerformanceBudgets || ["none"])],
            securityPolicies: [...(elementSchema.allowedSecurityPolicies || ["default"])]
          }
        };
      }

      function applyTraits(source, ref, traits = {}) {
        const doc = parseSource(source);
        sanitizeRoot(doc.body);
        const {element, index, total} = elementForRef(doc, ref);
        const events = [];
        if (!element) {
          events.push({level: "warning", module: "editor", code: "MCEL_TRAIT_TARGET_MISSING", message: "No MCEL source element was available for trait update."});
          return {source: serializeBody(doc), index: -1, total: 0, events};
        }
        const updates = [
          [attributes.kind, traits.kind],
          [attributes.flow, traits.flow],
          [attributes.rank, traits.rank],
          [attributes.state, traits.state],
          [attributes.density, traits.density],
          [attributes.sizePolicy, traits.sizePolicy],
          [attributes.overflowPolicy, traits.overflowPolicy],
          [attributes.scrollPolicy, traits.scrollPolicy],
          [attributes.componentName, traits.componentName],
          [attributes.componentKind, traits.componentKind],
          [attributes.stateOwner, traits.stateOwner],
          [attributes.stateScope, traits.stateScope],
          [attributes.statePolicy, traits.statePolicy],
          [attributes.query, traits.query],
          [attributes.cachePolicy, traits.cachePolicy],
          [attributes.mutation, traits.mutation],
          [attributes.syncPolicy, traits.syncPolicy],
          [attributes.submit, traits.submit],
          [attributes.validation, traits.validation],
          [attributes.dirtyPolicy, traits.dirtyPolicy],
          [attributes.errorPolicy, traits.errorPolicy],
          [attributes.action, traits.action],
          [attributes.target, traits.target],
          [attributes.swapPolicy, traits.swapPolicy],
          [attributes.eventPolicy, traits.eventPolicy],
          [attributes.route, traits.route],
          [attributes.renderMode, traits.renderMode],
          [attributes.hydration, traits.hydration],
          [attributes.islandPolicy, traits.islandPolicy],
          [attributes.focusPolicy, traits.focusPolicy],
          [attributes.a11yPolicy, traits.a11yPolicy],
          [attributes.performanceBudget, traits.performanceBudget],
          [attributes.securityPolicy, traits.securityPolicy],
          [attributes.words, traits.words],
          [attributes.connects, traits.connects]
        ];
        updates.forEach(([attribute, value]) => {
          const normalized = String(value ?? "").trim();
          if (normalized) {
            element.setAttribute(attribute, normalized);
          } else if (attribute === attributes.connects || attribute === attributes.componentName || attribute === attributes.componentKind || attribute === attributes.stateOwner || attribute === attributes.stateScope || attribute === attributes.statePolicy || attribute === attributes.query || attribute === attributes.cachePolicy || attribute === attributes.mutation || attribute === attributes.syncPolicy || attribute === attributes.submit || attribute === attributes.validation || attribute === attributes.dirtyPolicy || attribute === attributes.errorPolicy || attribute === attributes.action || attribute === attributes.target || attribute === attributes.swapPolicy || attribute === attributes.eventPolicy || attribute === attributes.route || attribute === attributes.renderMode || attribute === attributes.hydration || attribute === attributes.islandPolicy || attribute === attributes.focusPolicy || attribute === attributes.a11yPolicy || attribute === attributes.performanceBudget || attribute === attributes.securityPolicy) {
            element.removeAttribute(attribute);
          }
        });
        events.push({level: "info", module: "editor", code: "MCEL_TRAIT_UPDATED", message: `Updated semantic traits on source widget ${index + 1} of ${total}.`});
        return {source: serializeBody(doc), index, total, events};
      }

      function insertBlock(source, blockKey = "panel", options = {}) {
        const doc = parseSource(source);
        sanitizeRoot(doc.body);
        const template = blockTemplates[blockKey] || blockTemplates.panel;
        const fragment = parseSource(template);
        const node = fragment.body.firstElementChild;
        const elements = sourceElements(doc.body);
        const afterIndex = Number(options.afterIndex);
        if (node && elements.length && Number.isFinite(afterIndex) && afterIndex >= 0 && afterIndex < elements.length) {
          elements[afterIndex].insertAdjacentElement("afterend", node);
        } else if (node) {
          doc.body.appendChild(node);
        }
        const nextIndex = node ? sourceElements(doc.body).indexOf(node) : Math.max(elements.length - 1, 0);
        return {
          source: serializeBody(doc),
          index: nextIndex,
          total: sourceElements(doc.body).length,
          events: [{level: "info", module: "editor", code: "MCEL_SOURCE_BLOCK_INSERTED", message: `Inserted clean ${blockKey} source block.`}]
        };
      }

      function removeSelectedSource(source, ref) {
        const doc = parseSource(source);
        sanitizeRoot(doc.body);
        const {element, index} = elementForRef(doc, ref);
        if (element) element.remove();
        return {
          source: serializeBody(doc),
          index: Math.max(index - 1, 0),
          total: sourceElements(doc.body).length,
          events: [{level: "info", module: "editor", code: "MCEL_SOURCE_BLOCK_REMOVED", message: `Removed source widget ${index + 1}.`}]
        };
      }

      return Object.freeze({
        parseSource,
        sanitizeEditorHtml,
        canonicalSource,
        applyDefaultEnrichment,
        sourceList,
        normalizeRef,
        readTraits,
        applyTraits,
        insertBlock,
        removeSelectedSource
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabEditor = McelLabEditor;
    }
