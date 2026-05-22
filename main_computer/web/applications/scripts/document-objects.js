    const documentObjectStorageKeys = {
      hiddenPluginsPrefix: "main-computer-document-hidden-plugins-v1:"
    };
    const documentObjectAttributeNames = {
      object: "data-doc-object",
      objectId: "data-doc-object-id",
      layout: "data-doc-object-layout",
      latex: "data-latex"
    };
    const documentObjectRuntime = (() => {
      const objectTypes = new Map();
      const hiddenPluginTypes = new Map();
      let selectedObjectId = "";
      let selectedHiddenPluginId = "";

      function escapeDocumentObjectText(value) {
        return String(value ?? "")
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;")
          .replaceAll("'", "&#39;");
      }
      function documentObjectCssEscape(value) {
        if (window.CSS?.escape) return window.CSS.escape(String(value));
        return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
      }
      function documentObjectId(prefix = "docobj") {
        if (window.crypto?.randomUUID) return `${prefix}-${window.crypto.randomUUID()}`;
        return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      }
      function currentPluginStorageKey() {
        const path = String(documentSession?.selectedPath || "").trim();
        return `${documentObjectStorageKeys.hiddenPluginsPrefix}${path || "scratchpad"}`;
      }
      function coerceLayout(layout) {
        return layout === "paragraph" ? "paragraph" : "inline";
      }
      function closestDocumentObject(node) {
        const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
        return element?.closest?.("[data-doc-object]") || null;
      }
      function registerObjectType(name, definition) {
        if (!name || !definition) return;
        objectTypes.set(name, {...definition, name});
      }
      function registerHiddenPluginType(name, definition) {
        if (!name || !definition) return;
        hiddenPluginTypes.set(name, {...definition, name});
      }
      function knownHiddenPluginType(type) {
        return hiddenPluginTypes.get(type) || hiddenPluginTypes.get("note");
      }
      function loadHiddenPlugins() {
        try {
          const parsed = JSON.parse(localStorage.getItem(currentPluginStorageKey()) || "[]");
          return Array.isArray(parsed) ? parsed.map(normalizeHiddenPlugin).filter(Boolean) : [];
        } catch {
          return [];
        }
      }
      function saveHiddenPlugins(plugins) {
        localStorage.setItem(currentPluginStorageKey(), JSON.stringify((plugins || []).map(normalizeHiddenPlugin).filter(Boolean)));
      }
      function normalizeHiddenPlugin(plugin) {
        if (!plugin || typeof plugin !== "object") return null;
        const type = String(plugin.type || "note").trim() || "note";
        const definition = knownHiddenPluginType(type);
        const capabilities = Array.isArray(plugin.capabilities) ? plugin.capabilities : definition.capabilities || [];
        return {
          id: String(plugin.id || documentObjectId("plugin")),
          type,
          label: String(plugin.label || definition.label || "Hidden plugin").slice(0, 80),
          enabled: plugin.enabled !== false,
          mode: String(plugin.mode || definition.mode || "viewer-only"),
          layout: "document-hidden",
          capabilities: capabilities.map((capability) => String(capability)).slice(0, 12),
          anchor: normalizePluginAnchor(plugin.anchor),
          config: plugin.config && typeof plugin.config === "object" ? plugin.config : {}
        };
      }
      function normalizePluginAnchor(anchor) {
        if (!anchor || typeof anchor !== "object") return {kind: "document"};
        const kind = String(anchor.kind || "document");
        if (kind === "block") return {kind, blockId: String(anchor.blockId || "")};
        if (kind === "page") return {kind, pageIndex: Number(anchor.pageIndex) || 1};
        if (kind === "coordinate") {
          return {
            kind,
            pageIndex: Number(anchor.pageIndex) || 1,
            x: Math.max(0, Math.min(1, Number(anchor.x) || 0)),
            y: Math.max(0, Math.min(1, Number(anchor.y) || 0))
          };
        }
        return {kind: "document"};
      }
      function ensureBlockId(block) {
        if (!block) return "";
        if (!block.dataset.docBlockId) block.dataset.docBlockId = documentObjectId("block");
        return block.dataset.docBlockId;
      }
      function blockSelector() {
        return ".mc-page-content > p, .mc-page-content > h1, .mc-page-content > h2, .mc-page-content > h3, .mc-page-content > h4, .mc-page-content > h5, .mc-page-content > h6, .mc-page-content > blockquote, .mc-page-content > pre, .mc-page-content > li, .mc-page-content > div, .mc-page-content > figure";
      }
      function captureHiddenPluginAnchor() {
        const selection = window.getSelection?.();
        if (selection?.rangeCount && documentCanvas?.contains(selection.anchorNode)) {
          const range = selection.getRangeAt(0);
          const element = range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer : range.startContainer.parentElement;
          const block = element?.closest?.(blockSelector());
          if (block) {
            return {kind: "block", blockId: ensureBlockId(block)};
          }
          const page = element?.closest?.(".mc-page");
          if (page) return {kind: "page", pageIndex: documentPageIndex(page)};
        }
        return {kind: "document"};
      }
      function documentPageIndex(page) {
        const pages = Array.from(documentCanvas?.querySelectorAll(".mc-page") || []);
        const index = pages.indexOf(page);
        return index >= 0 ? index + 1 : Number(page?.dataset.documentPageIndex) || 1;
      }
      function targetForPluginAnchor(anchor) {
        if (!documentCanvas) return null;
        const normalized = normalizePluginAnchor(anchor);
        if (normalized.kind === "block" && normalized.blockId) {
          return documentCanvas.querySelector(`[data-doc-block-id="${documentObjectCssEscape(normalized.blockId)}"]`);
        }
        if (normalized.kind === "page") {
          return Array.from(documentCanvas.querySelectorAll(".mc-page"))[Math.max(0, normalized.pageIndex - 1)] || documentPage;
        }
        if (normalized.kind === "coordinate") {
          return Array.from(documentCanvas.querySelectorAll(".mc-page"))[Math.max(0, normalized.pageIndex - 1)] || documentPage;
        }
        return documentPage || documentCanvas;
      }
      function describePluginAnchor(anchor) {
        const normalized = normalizePluginAnchor(anchor);
        if (normalized.kind === "block") return "block";
        if (normalized.kind === "page") return `page ${normalized.pageIndex}`;
        if (normalized.kind === "coordinate") return `page ${normalized.pageIndex} hotspot`;
        return "document";
      }
      function renderPluginRail() {
        if (!documentPluginMarkers || !documentCanvas) return;
        const plugins = loadHiddenPlugins();
        documentPluginMarkers.innerHTML = "";
        if (!plugins.length) {
          const empty = document.createElement("div");
          empty.className = "document-plugin-empty";
          empty.textContent = "No hidden plugins yet.";
          documentPluginMarkers.append(empty);
          return;
        }
        const canvasRect = documentCanvas.getBoundingClientRect();
        const minHeight = Math.max(documentCanvas.scrollHeight, documentCanvas.clientHeight);
        documentPluginMarkers.style.minHeight = `${minHeight}px`;
        plugins.forEach((plugin) => {
          const definition = knownHiddenPluginType(plugin.type);
          const marker = document.createElement("button");
          marker.type = "button";
          marker.className = "document-plugin-marker";
          marker.dataset.docPluginId = plugin.id;
          marker.classList.toggle("selected", plugin.id === selectedHiddenPluginId);
          marker.classList.toggle("disabled", !plugin.enabled);
          const target = targetForPluginAnchor(plugin.anchor);
          const targetRect = target?.getBoundingClientRect?.();
          let top = 8;
          if (targetRect) {
            top = targetRect.top - canvasRect.top + documentCanvas.scrollTop;
            if (plugin.anchor?.kind === "coordinate") {
              top += Math.max(0, Math.min(1, plugin.anchor.y || 0)) * targetRect.height;
            }
          }
          marker.style.top = `${Math.max(8, Math.round(top))}px`;
          marker.innerHTML = `<strong>${escapeDocumentObjectText(plugin.label)}</strong><span>${escapeDocumentObjectText(definition.label || plugin.type)} · ${escapeDocumentObjectText(describePluginAnchor(plugin.anchor))}</span>`;
          marker.addEventListener("click", () => selectHiddenPlugin(plugin.id));
          marker.addEventListener("dblclick", () => editHiddenPlugin(plugin.id));
          documentPluginMarkers.append(marker);
        });
      }
      function selectHiddenPlugin(id) {
        selectedHiddenPluginId = String(id || "");
        selectedObjectId = "";
        updateSelectedObjectClasses();
        renderPluginRail();
        highlightHiddenPluginAnchor();
      }
      function highlightHiddenPluginAnchor() {
        documentCanvas?.querySelectorAll(".document-plugin-anchor-highlight").forEach((element) => {
          element.classList.remove("document-plugin-anchor-highlight");
        });
        const plugin = loadHiddenPlugins().find((item) => item.id === selectedHiddenPluginId);
        const target = plugin ? targetForPluginAnchor(plugin.anchor) : null;
        target?.classList?.add("document-plugin-anchor-highlight");
      }
      function editHiddenPlugin(id) {
        const plugins = loadHiddenPlugins();
        const plugin = plugins.find((item) => item.id === id);
        if (!plugin) return;
        const nextLabel = window.prompt("Hidden plugin label", plugin.label);
        if (nextLabel === null) return;
        plugin.label = nextLabel.trim() || plugin.label;
        saveHiddenPlugins(plugins);
        renderPluginRail();
      }
      function createHiddenPlugin() {
        const label = window.prompt("Hidden plugin label", "Reader plugin");
        if (label === null) return;
        const plugins = loadHiddenPlugins();
        const plugin = normalizeHiddenPlugin({
          id: documentObjectId("plugin"),
          type: "note",
          label: label.trim() || "Reader plugin",
          anchor: captureHiddenPluginAnchor(),
          capabilities: ["render:overlay", "style:page", "observe:viewer"],
          config: {createdBy: "document-editor"}
        });
        plugins.push(plugin);
        saveHiddenPlugins(plugins);
        selectedHiddenPluginId = plugin.id;
        renderPluginRail();
        highlightHiddenPluginAnchor();
        if (documentStatus) documentStatus.textContent = "hidden plugin attached";
      }
      function deleteSelectedHiddenPlugin() {
        if (!selectedHiddenPluginId) return false;
        const plugins = loadHiddenPlugins();
        const next = plugins.filter((plugin) => plugin.id !== selectedHiddenPluginId);
        if (next.length === plugins.length) return false;
        selectedHiddenPluginId = "";
        saveHiddenPlugins(next);
        renderPluginRail();
        if (documentStatus) documentStatus.textContent = "hidden plugin removed";
        return true;
      }
      function ensurePageOverlayLayers(root = documentCanvas) {
        root?.querySelectorAll?.(".mc-page").forEach((page) => {
          if (page.querySelector(":scope > .mc-page-overlay-layer")) return;
          const overlay = document.createElement("div");
          overlay.className = "mc-page-overlay-layer";
          overlay.contentEditable = "false";
          overlay.setAttribute("aria-hidden", "true");
          page.append(overlay);
        });
      }
      function createMathObject(latex, layout = "inline", id = documentObjectId("math")) {
        const normalizedLayout = coerceLayout(layout);
        const element = document.createElement(normalizedLayout === "paragraph" ? "figure" : "span");
        element.dataset.docObject = "math";
        element.dataset.docObjectId = id;
        element.dataset.docObjectLayout = normalizedLayout;
        element.dataset.latex = String(latex || "");
        element.contentEditable = "false";
        element.draggable = true;
        hydrateMathObject(element);
        return element;
      }
      function hydrateMathObject(element) {
        if (!element) return;
        const layout = coerceLayout(element.dataset.docObjectLayout);
        const latex = String(element.dataset.latex || "");
        element.dataset.docObject = "math";
        element.dataset.docObjectLayout = layout;
        element.dataset.docObjectId = element.dataset.docObjectId || documentObjectId("math");
        element.contentEditable = "false";
        element.draggable = true;
        element.className = `document-object document-math-object document-math-${layout}${selectedObjectId === element.dataset.docObjectId ? " selected" : ""}`;
        element.setAttribute("role", "button");
        element.setAttribute("tabindex", "0");
        element.setAttribute("aria-label", `${layout === "paragraph" ? "Paragraph" : "Inline"} math: ${latex || "empty"}`);
        element.title = "Double-click to edit math. Use Switch Math Layout to toggle inline/paragraph.";
        element.innerHTML = "";
        const body = document.createElement("span");
        body.className = "document-math-body";
        if (window.katex?.render) {
          try {
            window.katex.render(latex || "\\square", body, {
              displayMode: layout === "paragraph",
              throwOnError: false,
              trust: false,
              strict: "warn"
            });
          } catch {
            body.textContent = latex || "□";
          }
        } else {
          body.textContent = latex || "□";
        }
        element.append(body);
      }
      function prepareForSerialization(root) {
        root?.querySelectorAll?.("[data-doc-object]").forEach((element) => {
          const type = element.dataset.docObject;
          const definition = objectTypes.get(type);
          if (definition?.serialize) definition.serialize(element);
          element.classList.remove("selected");
        });
        root?.querySelectorAll?.(".document-plugin-anchor-highlight").forEach((element) => {
          element.classList.remove("document-plugin-anchor-highlight");
        });
      }
      function serializeMathObject(element) {
        const layout = coerceLayout(element.dataset.docObjectLayout);
        const latex = String(element.dataset.latex || "");
        element.dataset.docObject = "math";
        element.dataset.docObjectLayout = layout;
        element.dataset.docObjectId = element.dataset.docObjectId || documentObjectId("math");
        element.contentEditable = "false";
        element.draggable = true;
        element.className = `document-object document-math-object document-math-${layout}`;
        element.removeAttribute("role");
        element.removeAttribute("tabindex");
        element.removeAttribute("aria-label");
        element.removeAttribute("title");
        element.textContent = layout === "paragraph" ? `\\[${latex}\\]` : `\\(${latex}\\)`;
      }
      function hydrateAll(root = documentCanvas) {
        ensurePageOverlayLayers(root);
        root?.querySelectorAll?.("[data-doc-object]").forEach((element) => {
          const definition = objectTypes.get(element.dataset.docObject);
          if (definition?.hydrate) definition.hydrate(element);
        });
        updateSelectedObjectClasses();
        renderPluginRail();
      }
      function getInsertionRange() {
        const selection = window.getSelection?.();
        if (!selection || !selection.rangeCount) return null;
        const range = selection.getRangeAt(0);
        const editor = getActiveDocumentEditor();
        if (!editor || !editor.contains(range.startContainer)) return null;
        return {range, editor};
      }
      function insertInlineMath(latex) {
        const target = getInsertionRange();
        if (!target) return false;
        const {range, editor} = target;
        if (!range.collapsed) range.deleteContents();
        const element = createMathObject(latex, "inline");
        range.insertNode(document.createTextNode(" "));
        range.insertNode(element);
        range.insertNode(document.createTextNode(" "));
        selectDocumentObject(element);
        editor.focus();
        return true;
      }
      function insertParagraphMath(latex) {
        const target = getInsertionRange();
        const element = createMathObject(latex, "paragraph");
        if (!target) {
          documentEditor?.append(element);
          selectDocumentObject(element);
          return true;
        }
        const {range, editor} = target;
        if (!range.collapsed) range.deleteContents();
        const block = documentBlockForRange(range, editor);
        if (block?.parentNode === editor) block.after(element);
        else range.insertNode(element);
        selectDocumentObject(element);
        return true;
      }
      function insertMath(layout = "inline") {
        const latex = window.prompt("Math LaTeX", "E = mc^2");
        if (latex === null) return;
        const inserted = coerceLayout(layout) === "paragraph" ? insertParagraphMath(latex) : insertInlineMath(latex);
        if (!inserted) return;
        hydrateAll(documentCanvas);
        saveDocumentDraft();
        scheduleDocumentRepagination();
        if (documentStatus) documentStatus.textContent = `${coerceLayout(layout)} math inserted`;
      }
      function selectDocumentObject(element) {
        if (!element) return;
        selectedObjectId = element.dataset.docObjectId || "";
        selectedHiddenPluginId = "";
        updateSelectedObjectClasses();
        renderPluginRail();
      }
      function updateSelectedObjectClasses() {
        documentCanvas?.querySelectorAll?.("[data-doc-object]").forEach((element) => {
          element.classList.toggle("selected", Boolean(selectedObjectId && element.dataset.docObjectId === selectedObjectId));
        });
      }
      function selectedDocumentObject() {
        if (!selectedObjectId || !documentCanvas) return null;
        return documentCanvas.querySelector(`[data-doc-object-id="${documentObjectCssEscape(selectedObjectId)}"]`);
      }
      function editMathObject(element) {
        const current = String(element?.dataset?.latex || "");
        const next = window.prompt("Math LaTeX", current);
        if (next === null || !element) return;
        element.dataset.latex = next;
        hydrateMathObject(element);
        saveDocumentDraft();
        scheduleDocumentRepagination();
      }
      function splitInlineMathIntoParagraph(element, figure) {
        const block = element.closest("p,h1,h2,h3,h4,h5,h6,blockquote,pre,li,div");
        const editor = element.closest(".mc-page-content");
        if (!block || !editor || block === editor || block.parentNode !== editor) {
          element.replaceWith(figure);
          return;
        }
        const before = document.createElement(block.tagName.toLowerCase());
        const after = document.createElement(block.tagName.toLowerCase());
        while (block.firstChild && block.firstChild !== element) before.appendChild(block.firstChild);
        element.remove();
        while (block.firstChild) after.appendChild(block.firstChild);
        const replacements = [];
        if (before.textContent.trim() || before.querySelector("[data-doc-object],img,br")) replacements.push(before);
        replacements.push(figure);
        if (after.textContent.trim() || after.querySelector("[data-doc-object],img,br")) replacements.push(after);
        block.replaceWith(...replacements);
      }
      function toggleMathLayout() {
        const element = selectedDocumentObject();
        if (!element || element.dataset.docObject !== "math") {
          if (documentStatus) documentStatus.textContent = "select a math embed first";
          return;
        }
        const latex = String(element.dataset.latex || "");
        const id = element.dataset.docObjectId || documentObjectId("math");
        const layout = coerceLayout(element.dataset.docObjectLayout);
        if (layout === "inline") {
          const figure = createMathObject(latex, "paragraph", id);
          splitInlineMathIntoParagraph(element, figure);
          selectDocumentObject(figure);
        } else {
          const span = createMathObject(latex, "inline", id);
          const paragraph = document.createElement("p");
          paragraph.append(span, document.createTextNode(" "));
          element.replaceWith(paragraph);
          selectDocumentObject(span);
        }
        saveDocumentDraft();
        scheduleDocumentRepagination();
        if (documentStatus) documentStatus.textContent = "math layout switched";
      }
      function handleCanvasClick(event) {
        const object = closestDocumentObject(event.target);
        if (object) {
          event.preventDefault();
          selectDocumentObject(object);
          return;
        }
        if (!event.target.closest(".document-plugin-marker")) {
          selectedObjectId = "";
          selectedHiddenPluginId = "";
          updateSelectedObjectClasses();
          renderPluginRail();
        }
      }
      function handleCanvasDoubleClick(event) {
        const object = closestDocumentObject(event.target);
        if (!object) return;
        event.preventDefault();
        const definition = objectTypes.get(object.dataset.docObject);
        if (definition?.edit) definition.edit(object);
      }
      function handleObjectKeydown(event) {
        if ((event.key === "Delete" || event.key === "Backspace") && selectedObjectId) {
          const element = selectedDocumentObject();
          if (element) {
            event.preventDefault();
            element.remove();
            selectedObjectId = "";
            saveDocumentDraft();
            scheduleDocumentRepagination();
            if (documentStatus) documentStatus.textContent = "document object removed";
            return true;
          }
        }
        if ((event.key === "Delete" || event.key === "Backspace") && selectedHiddenPluginId) {
          event.preventDefault();
          return deleteSelectedHiddenPlugin();
        }
        return false;
      }
      function wireEvents() {
        documentCanvas?.addEventListener("click", handleCanvasClick);
        documentCanvas?.addEventListener("dblclick", handleCanvasDoubleClick);
        documentCanvas?.addEventListener("scroll", renderPluginRail);
        window.addEventListener("resize", renderPluginRail);
      }

      registerObjectType("math", {
        label: "Math",
        layout: ["inline-flow", "block-flow"],
        capabilities: ["render:inline", "render:block"],
        hydrate: hydrateMathObject,
        serialize: serializeMathObject,
        edit: editMathObject
      });
      registerHiddenPluginType("note", {
        label: "Hidden plugin",
        mode: "viewer-only",
        capabilities: ["render:overlay", "style:page", "observe:viewer"]
      });

      return {
        registerObjectType,
        registerHiddenPluginType,
        hydrateAll,
        prepareForSerialization,
        renderPluginRail,
        insertMath,
        toggleMathLayout,
        createHiddenPlugin,
        handleObjectKeydown,
        wireEvents,
        loadHiddenPlugins,
        saveHiddenPlugins
      };
    })();

    function hydrateDocumentObjects(root = documentCanvas) {
      documentObjectRuntime.hydrateAll(root);
    }
    function prepareDocumentObjectsForSerialization(root) {
      documentObjectRuntime.prepareForSerialization(root);
      return root;
    }
    function renderDocumentPluginRail() {
      documentObjectRuntime.renderPluginRail();
    }
    function insertDocumentMathObject(layout = "inline") {
      documentObjectRuntime.insertMath(layout);
    }
    function toggleSelectedDocumentMathLayout() {
      documentObjectRuntime.toggleMathLayout();
    }
    function createHiddenDocumentPlugin() {
      documentObjectRuntime.createHiddenPlugin();
    }
