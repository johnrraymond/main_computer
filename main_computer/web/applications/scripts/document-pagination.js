    // Legacy application adapter. The engine is also mountable without this application.
    const documentPagination = McelDocumentEngine.createPagination({
      documentCanvas, documentPage, documentEditor, documentSession, documentFormat,
      saveDocumentDraft: () => saveDocumentDraft(),
      hydrateDocumentObjects: (root) => hydrateDocumentObjects(root),
      prepareDocumentObjectsForSerialization: (root) => prepareDocumentObjectsForSerialization(root),
      renderDocumentPluginRail: () => renderDocumentPluginRail()
    });
    const {getDocumentPageContents, getActiveDocumentEditor, getDocumentEditorHtml, setDocumentEditorHtml, createPage, measurePageContent, moveOverflowToNextPage, ensureEditableBlockContent, documentFormatValueForBlock, documentTagForFormatValue, createDocumentBlock, setDocumentCaretAtStart, documentBlockForRange, getDocumentCaretBlock, updateDocumentFormatForCaret, restoreDocumentSelectionFromMarker, replaceDocumentBlockTag, applyDocumentBlockFormat, insertDocumentParagraphAtRange, splitDocumentBlockAtSelection, isDocumentBackspaceMergeEvent, isRangeAtStartOfDocumentBlock, previousEditableDocumentBlock, terminalLeftStyleContainer, removeTrailingDocumentBreak, appendTextUsingPreviousStyle, mergeDocumentBlockBackwardUsingLeftStyle, handleDocumentBackspaceMerge, handleDocumentEditorKeydown, documentTextOffsetFromSelection, restoreDocumentSelectionFromTextOffset, removeDocumentCaretMarkers, createDocumentCaretMarker, preserveCaretDuringRepagination, normalizeDocumentPagesForRender, markDocumentPageOversize, renderDocumentPages, repaginateDocument, scheduleDocumentRepagination} = documentPagination;
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
