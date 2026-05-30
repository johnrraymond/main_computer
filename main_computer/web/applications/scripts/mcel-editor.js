    var McelLabEditor = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const engine = typeof McelLabEngine !== "undefined" ? McelLabEngine : window.McelLabEngine;
      const {attributes, defaults, runtimeOwnedAttributes, runtimeOwnedClasses, blockTemplates, schema} = contract;

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
            scrollPolicies: [...(elementSchema.allowedScrollPolicies || ["auto"])]
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
          [attributes.words, traits.words],
          [attributes.connects, traits.connects]
        ];
        updates.forEach(([attribute, value]) => {
          const normalized = String(value ?? "").trim();
          if (normalized) {
            element.setAttribute(attribute, normalized);
          } else if (attribute === attributes.connects) {
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
