    function getDocumentPageContents() {
      if (!documentCanvas) return documentEditor ? [documentEditor] : [];
      return Array.from(documentCanvas.querySelectorAll(".mc-page-content"));
    }
    function getActiveDocumentEditor() {
      const selection = window.getSelection?.();
      const node = selection?.anchorNode;
      const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
      return element?.closest?.(".mc-page-content") || documentEditor;
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
      documentCanvas?.querySelectorAll(".mc-page:not(#document-page)").forEach((page) => page.remove());
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
      if (!editor || !editor.contains(selection.anchorNode)) return null;
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
      if (!editor || !editor.contains(selection.anchorNode)) return;
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
      if (!editor || !editor.contains(range.startContainer)) return false;
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
      if (!editor || !editor.contains(range.startContainer)) return false;
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
      if (!selection || !selection.rangeCount || !documentCanvas?.contains(selection.anchorNode)) return null;
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
      if (selection?.rangeCount && documentCanvas?.contains(selection.anchorNode)) {
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
      documentCanvas.querySelectorAll(".mc-page:not(#document-page)").forEach((page) => page.remove());
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
      renderDocumentPages();
    }
    function scheduleDocumentRepagination() {
      if (documentRepaginationFrame) cancelAnimationFrame(documentRepaginationFrame);
      documentRepaginationFrame = requestAnimationFrame(repaginateDocument);
    }
    function loadDocumentLayoutForCurrentPath(path = documentSession.selectedPath) {
      applyDocumentLayoutState(documentLayoutStore.get(path) || defaultDocumentLayoutState());
    }
    function saveDocumentLayoutForCurrentPath() {
      documentLayoutStore.set(documentSession.selectedPath, documentSession.layoutState);
    }
    function populateDocumentLayoutPopover(state = documentSession.layoutState) {
      const normalized = normalizeDocumentLayoutState(state);
      const size = documentLayoutSize(normalized);
      documentLayoutPreset.value = normalized.layout.mode === "custom" ? "custom" : normalized.layout.preset || "letter";
      documentLayoutWidth.value = size.widthPx;
      documentLayoutHeight.value = size.heightPx;
      documentLayoutWidth.disabled = normalized.layout.mode !== "custom";
      documentLayoutHeight.disabled = normalized.layout.mode !== "custom";
      documentMarginTop.value = normalized.layout.margins.top;
      documentMarginRight.value = normalized.layout.margins.right;
      documentMarginBottom.value = normalized.layout.margins.bottom;
      documentMarginLeft.value = normalized.layout.margins.left;
      documentViewPaged.checked = normalized.view.mode === "paged";
      documentViewEndless.checked = normalized.view.mode === "endless";
      documentShowPageBreaks.checked = Boolean(normalized.view.showPageBreaks);
    }
    function readDocumentLayoutPopoverState() {
      const selectedPreset = documentLayoutPreset.value;
      const isCustom = selectedPreset === "custom";
      const basePreset = documentSession.layoutState.layout.preset || "letter";
      const preset = isCustom ? null : selectedPreset;
      const presetSize = documentPagePresets[preset || basePreset] || documentPagePresets.letter;
      return normalizeDocumentLayoutState({
        layout: {
          mode: isCustom ? "custom" : "preset",
          preset,
          custom: isCustom ? {
            name: "Custom",
            widthPx: documentLayoutWidth.value,
            heightPx: documentLayoutHeight.value
          } : null,
          margins: {
            top: documentMarginTop.value,
            right: documentMarginRight.value,
            bottom: documentMarginBottom.value,
            left: documentMarginLeft.value
          }
        },
        view: {
          mode: documentViewEndless.checked ? "endless" : "paged",
          zoom: documentSession.layoutState.view.zoom || 1,
          showPageBreaks: documentShowPageBreaks.checked
        }
      });
    }
    function openDocumentLayoutPopover() {
      populateDocumentLayoutPopover();
      documentLayoutPopover.hidden = false;
      documentLayoutButton.setAttribute("aria-expanded", "true");
      documentLayoutPreset.focus();
    }
    function closeDocumentLayoutPopover() {
      documentLayoutPopover.hidden = true;
      documentLayoutButton.setAttribute("aria-expanded", "false");
    }
