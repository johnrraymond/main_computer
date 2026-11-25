/* Shared incumbent document engine. No application shell or storage authority. */
var McelDocumentEngine = (() => {
  "use strict";
    const documentPagePresets = {
      letter: {label: "Letter", widthPx: 816, heightPx: 1056},
      a4: {label: "A4", widthPx: 794, heightPx: 1123},
      legal: {label: "Legal", widthPx: 816, heightPx: 1344},
      screen: {label: "Screen", widthPx: 960, heightPx: 1280}
    };
    function defaultDocumentLayoutState() {
      return {
        layout: {
          mode: "preset",
          preset: "letter",
          custom: null,
          margins: {top: 96, right: 96, bottom: 96, left: 96}
        },
        view: {
          mode: "paged",
          zoom: 1,
          showPageBreaks: true
        }
      };
    }
    function clampDocumentNumber(value, fallback, min, max) {
      const number = Number(value);
      if (!Number.isFinite(number)) return fallback;
      return Math.max(min, Math.min(max, Math.round(number)));
    }
    function normalizeDocumentLayoutState(state = {}) {
      state = state && typeof state === "object" ? state : {};
      const fallback = defaultDocumentLayoutState();
      const rawLayout = state.layout && typeof state.layout === "object" ? state.layout : {};
      const rawView = state.view && typeof state.view === "object" ? state.view : {};
      const preset = Object.prototype.hasOwnProperty.call(documentPagePresets, rawLayout.preset) ? rawLayout.preset : fallback.layout.preset;
      const custom = rawLayout.custom && typeof rawLayout.custom === "object" ? rawLayout.custom : {};
      const mode = rawLayout.mode === "custom" ? "custom" : "preset";
      const widthPx = clampDocumentNumber(custom.widthPx, documentPagePresets[preset].widthPx, 320, 2400);
      const heightPx = clampDocumentNumber(custom.heightPx, documentPagePresets[preset].heightPx, 480, 3200);
      const margins = rawLayout.margins && typeof rawLayout.margins === "object" ? rawLayout.margins : {};
      const maxHorizontalMargin = Math.max(0, Math.floor((mode === "custom" ? widthPx : documentPagePresets[preset].widthPx) / 2) - 24);
      const maxVerticalMargin = Math.max(0, Math.floor((mode === "custom" ? heightPx : documentPagePresets[preset].heightPx) / 2) - 24);
      return {
        layout: {
          mode,
          preset: mode === "preset" ? preset : null,
          custom: mode === "custom" ? {name: "Custom", widthPx, heightPx} : null,
          margins: {
            top: clampDocumentNumber(margins.top, fallback.layout.margins.top, 0, Math.min(480, maxVerticalMargin)),
            right: clampDocumentNumber(margins.right, fallback.layout.margins.right, 0, Math.min(480, maxHorizontalMargin)),
            bottom: clampDocumentNumber(margins.bottom, fallback.layout.margins.bottom, 0, Math.min(480, maxVerticalMargin)),
            left: clampDocumentNumber(margins.left, fallback.layout.margins.left, 0, Math.min(480, maxHorizontalMargin))
          }
        },
        view: {
          mode: rawView.mode === "endless" ? "endless" : "paged",
          zoom: Math.max(0.5, Math.min(2, (Number.isFinite(Number(rawView.zoom)) && Number(rawView.zoom) > 0 ? Number(rawView.zoom) : fallback.view.zoom))),
          showPageBreaks: "showPageBreaks" in rawView ? Boolean(rawView.showPageBreaks) : fallback.view.showPageBreaks
        }
      };
    }
    function documentLayoutSize(state = defaultDocumentLayoutState()) {
      const normalized = normalizeDocumentLayoutState(state);
      if (normalized.layout.mode === "custom" && normalized.layout.custom) return normalized.layout.custom;
      return documentPagePresets[normalized.layout.preset || "letter"];
    }

  function createPagination(options) {
    const {documentCanvas, documentPage, documentEditor, documentSession, documentFormat = null,
      saveDocumentDraft, hydrateDocumentObjects, prepareDocumentObjectsForSerialization, renderDocumentPluginRail} = options;
    let documentRepaginationFrame = 0;
    function getDocumentPageContents() {
      if (!documentCanvas) return documentEditor ? [documentEditor] : [];
      return Array.from(documentCanvas.querySelectorAll(".mc-page-content"));
    }
    function getActiveDocumentEditor() {
      const selection = window.getSelection?.();
      const node = selection?.anchorNode;
      const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
      const candidate = element?.closest?.(".mc-page-content");
      return candidate && documentCanvas.contains(candidate) ? candidate : documentEditor;
    }
    function getDocumentEditorHtml() {
      return getDocumentPageContents().map((content) => {
        const clone = content.cloneNode(true);
        if (typeof prepareDocumentObjectsForSerialization === "function") {
          prepareDocumentObjectsForSerialization(clone);
        }
        return clone.innerHTML;
      }).join("");
    }
    function setDocumentEditorHtml(html) {
      if (!documentEditor) return;
      documentCanvas?.querySelectorAll(".mc-page").forEach((page) => { if (page !== documentPage) page.remove(); });
      documentEditor.innerHTML = html || "";
      if (typeof hydrateDocumentObjects === "function") hydrateDocumentObjects(documentCanvas);
      scheduleDocumentRepagination();
    }
    function createPage(index = 0) {
      if (!documentCanvas || index === 0) return documentPage;
      const page = document.createElement("div");
      page.className = "mc-page";
      page.dataset.documentPageIndex = String(index + 1);
      const guide = document.createElement("div");
      guide.className = "mc-page-break-guide";
      guide.setAttribute("aria-hidden", "true");
      const content = document.createElement("div");
      content.className = "mc-page-content document-editor";
      content.contentEditable = "true";
      content.setAttribute("role", "textbox");
      content.setAttribute("aria-multiline", "true");
      content.spellcheck = true;
      const overlay = document.createElement("div");
      overlay.className = "mc-page-overlay-layer";
      overlay.contentEditable = "false";
      overlay.setAttribute("aria-hidden", "true");
      page.append(guide, content, overlay);
      documentCanvas.append(page);
      return page;
    }
    function measurePageContent(content) {
      if (!content) return {scrollHeight: 0, clientHeight: 0, overflow: 0, overflowing: false};
      const scrollHeight = content.scrollHeight;
      const clientHeight = content.clientHeight;
      const overflow = Math.max(0, scrollHeight - clientHeight);
      return {scrollHeight, clientHeight, overflow, overflowing: overflow > 2};
    }
    function moveOverflowToNextPage(pageContent, nextPageContent) {
      const node = pageContent?.lastChild;
      if (node && nextPageContent) nextPageContent.insertBefore(node, nextPageContent.firstChild);
      return node;
    }
    function ensureEditableBlockContent(block) {
      if (!block) return;
      if (!block.textContent.trim() && !block.querySelector("br,img,hr,table")) {
        block.innerHTML = "<br>";
      }
    }
    function documentFormatValueForBlock(block) {
      const tagName = block?.tagName?.toUpperCase?.() || "P";
      if (tagName === "H1" || tagName === "H2" || tagName === "H3" || tagName === "BLOCKQUOTE") return tagName;
      return "P";
    }
    function documentTagForFormatValue(value) {
      const normalized = String(value || "P").toUpperCase();
      if (normalized === "H1" || normalized === "H2" || normalized === "H3" || normalized === "BLOCKQUOTE") return normalized;
      return "P";
    }
    function createDocumentBlock(tagName = "P") {
      return document.createElement(documentTagForFormatValue(tagName));
    }
    function setDocumentCaretAtStart(element) {
      if (!element) return;
      const selection = window.getSelection?.();
      if (!selection) return;
      const range = document.createRange();
      range.setStart(element, 0);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
      element.closest(".mc-page-content")?.focus();
    }
    function documentBlockForRange(range, editor) {
      const node = range.startContainer;
      const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
      const block = element?.closest?.("p,h1,h2,h3,h4,h5,h6,blockquote,pre,li,div");
      return block && editor.contains(block) && block !== editor ? block : null;
    }
    function getDocumentCaretBlock() {
      const selection = window.getSelection?.();
      if (!selection || !selection.rangeCount) return null;
      const editor = getActiveDocumentEditor();
      if (!editor || !editor.contains(selection.anchorNode) || !editor.contains(selection.focusNode)) return null;
      return documentBlockForRange(selection.getRangeAt(0), editor);
    }
    function updateDocumentFormatForCaret() {
      if (!documentFormat) return;
      const block = getDocumentCaretBlock();
      documentFormat.value = documentFormatValueForBlock(block);
    }
    function restoreDocumentSelectionFromMarker(marker) {
      if (!marker?.parentNode) return false;
      const selection = window.getSelection?.();
      if (!selection) return false;
      const editor = marker.closest(".mc-page-content");
      const range = document.createRange();
      range.setStartBefore(marker);
      range.collapse(true);
      marker.remove();
      selection.removeAllRanges();
      selection.addRange(range);
      editor?.focus();
      updateDocumentFormatForCaret();
      return true;
    }
    function replaceDocumentBlockTag(block, tagName) {
      if (!block) return null;
      const nextBlock = createDocumentBlock(tagName);
      Array.from(block.attributes).forEach((attribute) => {
        if (attribute.name !== "id") nextBlock.setAttribute(attribute.name, attribute.value);
      });
      while (block.firstChild) nextBlock.appendChild(block.firstChild);
      block.replaceWith(nextBlock);
      ensureEditableBlockContent(nextBlock);
      return nextBlock;
    }
    function applyDocumentBlockFormat(value) {
      const selection = window.getSelection?.();
      if (!selection || !selection.rangeCount) return;
      const editor = getActiveDocumentEditor();
      if (!editor || !editor.contains(selection.anchorNode) || !editor.contains(selection.focusNode)) return;
      const range = selection.getRangeAt(0);
      let block = documentBlockForRange(range, editor);
      if (!block) {
        const paragraph = createDocumentBlock(value);
        paragraph.innerHTML = "<br>";
        range.insertNode(paragraph);
        setDocumentCaretAtStart(paragraph);
        block = paragraph;
      }
      const marker = createDocumentCaretMarker();
      const markerRange = selection.getRangeAt(0).cloneRange();
      markerRange.collapse(true);
      markerRange.insertNode(marker);
      replaceDocumentBlockTag(block, value);
      restoreDocumentSelectionFromMarker(marker);
      saveDocumentDraft();
      scheduleDocumentRepagination();
    }
    function insertDocumentParagraphAtRange(range, editor) {
      const paragraph = createDocumentBlock("P");
      paragraph.innerHTML = "<br>";
      const topLevel = range.startContainer === editor
        ? editor.childNodes[range.startOffset - 1]
        : range.startContainer?.parentElement?.closest?.(".mc-page-content > *");
      if (topLevel?.parentNode === editor) topLevel.after(paragraph);
      else range.insertNode(paragraph);
      setDocumentCaretAtStart(paragraph);
    }
    function splitDocumentBlockAtSelection() {
      const selection = window.getSelection?.();
      if (!selection || !selection.rangeCount) return false;
      const range = selection.getRangeAt(0);
      const editor = getActiveDocumentEditor();
      if (!editor || !editor.contains(range.startContainer) || !editor.contains(range.endContainer)) return false;
      if (!range.collapsed) {
        range.deleteContents();
      }
      const block = documentBlockForRange(range, editor);
      if (!block) {
        insertDocumentParagraphAtRange(range, editor);
        return true;
      }
      const newBlock = createDocumentBlock("P");
      newBlock.removeAttribute("id");
      const trailingRange = range.cloneRange();
      trailingRange.setEnd(block, block.childNodes.length);
      const trailing = trailingRange.extractContents();
      newBlock.appendChild(trailing);
      ensureEditableBlockContent(block);
      ensureEditableBlockContent(newBlock);
      block.after(newBlock);
      setDocumentCaretAtStart(newBlock);
      return true;
    }
    function isDocumentBackspaceMergeEvent(event) {
      return event.key === "Backspace"
        && !event.shiftKey
        && !event.ctrlKey
        && !event.metaKey
        && !event.altKey
        && Boolean(event.target.closest(".mc-page-content"));
    }
    function isRangeAtStartOfDocumentBlock(range, block) {
      if (!range?.collapsed || !block) return false;
      const before = range.cloneRange();
      before.selectNodeContents(block);
      before.setEnd(range.startContainer, range.startOffset);
      return before.toString().length === 0;
    }
    function previousEditableDocumentBlock(block) {
      const blocks = Array.from(documentCanvas?.querySelectorAll(".mc-page-content > p, .mc-page-content > h1, .mc-page-content > h2, .mc-page-content > h3, .mc-page-content > blockquote, .mc-page-content > pre, .mc-page-content > li, .mc-page-content > div") || []);
      const index = blocks.indexOf(block);
      return index > 0 ? blocks[index - 1] : null;
    }
    function terminalLeftStyleContainer(block) {
      if (!block) return null;
      const inlineTags = new Set(["STRONG", "B", "EM", "I", "U", "SPAN", "CODE", "A", "MARK", "SMALL", "SUB", "SUP"]);
      let node = block.lastChild;
      while (node && node.nodeType === Node.ELEMENT_NODE && node.tagName === "BR") node = node.previousSibling;
      while (node?.lastChild) {
        node = node.lastChild;
        while (node && node.nodeType === Node.ELEMENT_NODE && node.tagName === "BR") node = node.previousSibling;
      }
      const element = node?.nodeType === Node.TEXT_NODE ? node.parentElement : node;
      return element && element !== block && inlineTags.has(element.tagName) && block.contains(element) ? element : null;
    }
    function removeTrailingDocumentBreak(block) {
      while (block?.lastChild?.nodeType === Node.ELEMENT_NODE && block.lastChild.tagName === "BR") {
        block.lastChild.remove();
      }
    }
    function appendTextUsingPreviousStyle(previousBlock, text) {
      const marker = createDocumentCaretMarker();
      removeTrailingDocumentBreak(previousBlock);
      const target = terminalLeftStyleContainer(previousBlock) || previousBlock;
      target.appendChild(marker);
      if (text) target.appendChild(document.createTextNode(text));
      return marker;
    }
    function mergeDocumentBlockBackwardUsingLeftStyle(currentBlock, previousBlock) {
      if (!currentBlock || !previousBlock) return false;
      const rightText = currentBlock.textContent || "";
      const marker = appendTextUsingPreviousStyle(previousBlock, rightText);
      currentBlock.remove();
      ensureEditableBlockContent(previousBlock);
      restoreDocumentSelectionFromMarker(marker);
      return true;
    }
    function handleDocumentBackspaceMerge(event) {
      if (!isDocumentBackspaceMergeEvent(event)) return false;
      const selection = window.getSelection?.();
      if (!selection || !selection.rangeCount || !selection.isCollapsed) return false;
      const range = selection.getRangeAt(0);
      const editor = getActiveDocumentEditor();
      if (!editor || !editor.contains(range.startContainer) || !editor.contains(range.endContainer)) return false;
      const currentBlock = documentBlockForRange(range, editor);
      if (!currentBlock || !isRangeAtStartOfDocumentBlock(range, currentBlock)) return false;
      const previousBlock = previousEditableDocumentBlock(currentBlock);
      if (!previousBlock) return false;
      event.preventDefault();
      if (!mergeDocumentBlockBackwardUsingLeftStyle(currentBlock, previousBlock)) return true;
      saveDocumentDraft();
      updateDocumentFormatForCaret();
      scheduleDocumentRepagination();
      return true;
    }
    function handleDocumentEditorKeydown(event) {
      if (handleDocumentBackspaceMerge(event)) return;
      if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
      if (!event.target.closest(".mc-page-content")) return;
      event.preventDefault();
      if (!splitDocumentBlockAtSelection()) return;
      saveDocumentDraft();
      updateDocumentFormatForCaret();
      scheduleDocumentRepagination();
    }
    function documentTextOffsetFromSelection() {
      const selection = window.getSelection?.();
      if (!selection || !selection.rangeCount || !documentCanvas?.contains(selection.anchorNode) || !documentCanvas.contains(selection.focusNode)) return null;
      const range = selection.getRangeAt(0);
      const walker = document.createTreeWalker(documentCanvas, NodeFilter.SHOW_TEXT);
      let offset = 0;
      while (walker.nextNode()) {
        const node = walker.currentNode;
        if (node === range.startContainer) return offset + range.startOffset;
        offset += node.nodeValue.length;
      }
      return offset;
    }
    function restoreDocumentSelectionFromTextOffset(offset) {
      if (offset === null || offset === undefined || !documentCanvas) return;
      const selection = window.getSelection?.();
      if (!selection) return;
      const range = document.createRange();
      const walker = document.createTreeWalker(documentCanvas, NodeFilter.SHOW_TEXT);
      let remaining = offset;
      let lastTextNode = null;
      while (walker.nextNode()) {
        const node = walker.currentNode;
        lastTextNode = node;
        if (remaining <= node.nodeValue.length) {
          range.setStart(node, Math.max(0, remaining));
          range.collapse(true);
          selection.removeAllRanges();
          selection.addRange(range);
          node.parentElement?.closest(".mc-page-content")?.focus();
          return;
        }
        remaining -= node.nodeValue.length;
      }
      const fallback = lastTextNode || getActiveDocumentEditor();
      if (fallback?.nodeType === Node.TEXT_NODE) {
        range.setStart(fallback, fallback.nodeValue.length);
      } else if (fallback) {
        range.selectNodeContents(fallback);
        range.collapse(false);
      } else {
        return;
      }
      selection.removeAllRanges();
      selection.addRange(range);
      getActiveDocumentEditor()?.focus();
    }
    function removeDocumentCaretMarkers() {
      documentCanvas?.querySelectorAll("[data-document-caret-marker]").forEach((marker) => marker.remove());
    }
    function createDocumentCaretMarker() {
      const marker = document.createElement("span");
      marker.dataset.documentCaretMarker = "true";
      marker.setAttribute("aria-hidden", "true");
      marker.contentEditable = "false";
      marker.style.cssText = "display:inline-block;width:0;height:0;overflow:hidden;line-height:0;";
      return marker;
    }
    function preserveCaretDuringRepagination() {
      removeDocumentCaretMarkers();
      const offset = documentTextOffsetFromSelection();
      const selection = window.getSelection?.();
      if (selection?.rangeCount && documentCanvas?.contains(selection.anchorNode) && documentCanvas.contains(selection.focusNode)) {
        const marker = createDocumentCaretMarker();
        const range = selection.getRangeAt(0).cloneRange();
        range.collapse(true);
        range.insertNode(marker);
        return {
          restore() {
            if (!restoreDocumentSelectionFromMarker(marker)) {
              removeDocumentCaretMarkers();
              restoreDocumentSelectionFromTextOffset(offset);
            }
          }
        };
      }
      return {
        restore() {
          restoreDocumentSelectionFromTextOffset(offset);
        }
      };
    }
    function normalizeDocumentPagesForRender() {
      if (!documentCanvas || !documentPage || !documentEditor) return [];
      const nodes = [];
      getDocumentPageContents().forEach((content) => {
        while (content.firstChild) nodes.push(content.removeChild(content.firstChild));
      });
      documentCanvas.querySelectorAll(".mc-page").forEach((page) => { if (page !== documentPage) page.remove(); });
      documentPage.className = "mc-page";
      documentPage.style.marginBottom = "";
      return nodes;
    }
    function markDocumentPageOversize(page, content) {
      const measurement = measurePageContent(content);
      if (!measurement.overflowing) return;
      // A single block taller than the printable area cannot be moved as a whole;
      // keep it reachable without adding an internal page scrollbar.
      page.classList.add("document-page-oversize");
      page.style.marginBottom = `${measurement.overflow + 28}px`;
    }
    function renderDocumentPages() {
      if (!documentCanvas || !documentPage || !documentEditor) return;
      const caret = preserveCaretDuringRepagination();
      const nodes = normalizeDocumentPagesForRender();
      const isEndless = documentSession.layoutState.view.mode === "endless";
      documentCanvas.classList.toggle("document-view-paged", !isEndless);
      documentCanvas.classList.toggle("document-view-endless", isEndless);
      documentPage.classList.toggle("mc-endless-page", isEndless);
      if (isEndless) {
        nodes.forEach((node) => documentEditor.appendChild(node));
        if (typeof hydrateDocumentObjects === "function") hydrateDocumentObjects(documentCanvas);
        caret.restore();
        if (typeof renderDocumentPluginRail === "function") renderDocumentPluginRail();
        return;
      }
      let pageIndex = 0;
      let currentPage = documentPage;
      let currentContent = documentEditor;
      nodes.forEach((node) => {
        currentContent.appendChild(node);
        if (!measurePageContent(currentContent).overflowing) return;
        if (currentContent.childNodes.length > 1) {
          pageIndex += 1;
          const nextPage = createPage(pageIndex);
          const nextContent = nextPage.querySelector(".mc-page-content");
          moveOverflowToNextPage(currentContent, nextContent);
          currentPage = nextPage;
          currentContent = nextContent;
        }
        markDocumentPageOversize(currentPage, currentContent);
      });
      if (typeof hydrateDocumentObjects === "function") hydrateDocumentObjects(documentCanvas);
      caret.restore();
      updateDocumentFormatForCaret();
      if (typeof renderDocumentPluginRail === "function") renderDocumentPluginRail();
    }
    function repaginateDocument() {
      documentRepaginationFrame = 0;
      if (options.deferWhileSelecting && documentCanvas.contains(window.getSelection()?.anchorNode) && !window.getSelection().isCollapsed) {
        options.onDeferred?.();
        return;
      }
      renderDocumentPages();
    }
    function scheduleDocumentRepagination() {
      if (documentRepaginationFrame) cancelAnimationFrame(documentRepaginationFrame);
      documentRepaginationFrame = requestAnimationFrame(repaginateDocument);
    }
    function cancelPagination() {
      if (documentRepaginationFrame) cancelAnimationFrame(documentRepaginationFrame);
      documentRepaginationFrame = 0;
    }
    return {getDocumentPageContents, getActiveDocumentEditor, getDocumentEditorHtml, setDocumentEditorHtml, createPage, measurePageContent, moveOverflowToNextPage, ensureEditableBlockContent, documentFormatValueForBlock, documentTagForFormatValue, createDocumentBlock, setDocumentCaretAtStart, documentBlockForRange, getDocumentCaretBlock, updateDocumentFormatForCaret, restoreDocumentSelectionFromMarker, replaceDocumentBlockTag, applyDocumentBlockFormat, insertDocumentParagraphAtRange, splitDocumentBlockAtSelection, isDocumentBackspaceMergeEvent, isRangeAtStartOfDocumentBlock, previousEditableDocumentBlock, terminalLeftStyleContainer, removeTrailingDocumentBreak, appendTextUsingPreviousStyle, mergeDocumentBlockBackwardUsingLeftStyle, handleDocumentBackspaceMerge, handleDocumentEditorKeydown, documentTextOffsetFromSelection, restoreDocumentSelectionFromTextOffset, removeDocumentCaretMarkers, createDocumentCaretMarker, preserveCaretDuringRepagination, normalizeDocumentPagesForRender, markDocumentPageOversize, renderDocumentPages, repaginateDocument, scheduleDocumentRepagination, cancelPagination};
  }
  function createObjects(options) {
    const {documentCanvas, documentPage, documentEditor, documentSession, documentPluginMarkers = null,
      documentStatus = null, getActiveDocumentEditor, documentBlockForRange, saveDocumentDraft,
      scheduleDocumentRepagination, storage} = options;
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
          const parsed = JSON.parse(storage.getItem(currentPluginStorageKey()) || "[]");
          return Array.isArray(parsed) ? parsed.map(normalizeHiddenPlugin).filter(Boolean) : [];
        } catch {
          return [];
        }
      }
      function saveHiddenPlugins(plugins) {
        storage.setItem(currentPluginStorageKey(), JSON.stringify((plugins || []).map(normalizeHiddenPlugin).filter(Boolean)));
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
        if (!editor || !editor.contains(range.startContainer) || !editor.contains(range.endContainer)) return null;
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
      let eventController = null;
      function unwireEvents() { eventController?.abort(); eventController = null; }
      function wireEvents() {
        unwireEvents();
        eventController = new AbortController();
        const eventOptions = {signal: eventController.signal};
        documentCanvas?.addEventListener("click", handleCanvasClick, eventOptions);
        documentCanvas?.addEventListener("dblclick", handleCanvasDoubleClick, eventOptions);
        documentCanvas?.addEventListener("scroll", renderPluginRail, eventOptions);
        if (options.globalResize) window.addEventListener("resize", renderPluginRail, eventOptions);
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
        unwireEvents,
        insertInlineMath,
        insertParagraphMath,
        loadHiddenPlugins,
        saveHiddenPlugins
      };
    })();

    return documentObjectRuntime;
  }
  return Object.freeze({documentPagePresets, defaultDocumentLayoutState, clampDocumentNumber,
    normalizeDocumentLayoutState, documentLayoutSize, createPagination, createObjects});
})();
