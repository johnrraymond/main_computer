    function clampDocumentNumber(value, fallback, min, max) {
      const number = Number(value);
      if (!Number.isFinite(number)) return fallback;
      return Math.max(min, Math.min(max, Math.round(number)));
    }
    function normalizeDocumentLayoutState(state = {}) {
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
          zoom: Math.max(0.5, Math.min(2, Number(rawView.zoom || fallback.view.zoom))),
          showPageBreaks: "showPageBreaks" in rawView ? Boolean(rawView.showPageBreaks) : fallback.view.showPageBreaks
        }
      };
    }
    function documentLayoutSize(state = documentSession.layoutState) {
      const normalized = normalizeDocumentLayoutState(state);
      if (normalized.layout.mode === "custom" && normalized.layout.custom) return normalized.layout.custom;
      return documentPagePresets[normalized.layout.preset || "letter"];
    }
    function applyDocumentLayoutState(state = documentSession.layoutState) {
      const normalized = normalizeDocumentLayoutState(state);
      documentSession.layoutState = normalized;
      const size = documentLayoutSize(normalized);
      const margins = normalized.layout.margins;
      documentWorkspaceStyle("--document-page-width", `${size.widthPx}px`);
      documentWorkspaceStyle("--document-page-height", `${size.heightPx}px`);
      documentWorkspaceStyle("--document-margin-top", `${margins.top}px`);
      documentWorkspaceStyle("--document-margin-right", `${margins.right}px`);
      documentWorkspaceStyle("--document-margin-bottom", `${margins.bottom}px`);
      documentWorkspaceStyle("--document-margin-left", `${margins.left}px`);
      documentWorkspaceStyle("--document-zoom", normalized.view.zoom);
      documentCanvas?.classList.toggle("document-view-paged", normalized.view.mode === "paged");
      documentCanvas?.classList.toggle("document-view-endless", normalized.view.mode === "endless");
      documentCanvas?.classList.toggle("document-show-page-breaks", Boolean(normalized.view.showPageBreaks));
      documentPage?.classList.toggle("mc-endless-page", normalized.view.mode === "endless");
      documentLayoutButton?.setAttribute("data-document-layout", normalized.layout.mode === "custom" ? "custom" : normalized.layout.preset || "letter");
      scheduleDocumentRepagination();
    }
    function documentWorkspaceStyle(name, value) {
      documentEditor?.closest(".document-workspace")?.style.setProperty(name, String(value));
    }
