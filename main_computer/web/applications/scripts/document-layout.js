    const {clampDocumentNumber, normalizeDocumentLayoutState} = McelDocumentEngine;
    function documentLayoutSize(state = documentSession.layoutState) {
      return McelDocumentEngine.documentLayoutSize(state);
    }

    const DOCUMENT_AUTO_FIT_MIN_ZOOM = 0.45;
    const DOCUMENT_AUTO_FIT_GUTTER = 40;
    let documentLayoutFitObserver = null;
    let documentLayoutVisibilityObserver = null;
    let documentLayoutFitRefreshPending = false;

    function documentCanvasBoxWidth() {
      const canvas = typeof documentCanvas !== "undefined" ? documentCanvas : null;
      const stage = typeof documentObjectStage !== "undefined" ? documentObjectStage : null;
      const shell = document?.querySelector?.(".document-shell") || null;
      const candidates = [canvas, stage, shell];
      for (const el of candidates) {
        if (!el) continue;
        const rectWidth = Number(el.getBoundingClientRect?.().width || 0);
        const clientWidth = Number(el.clientWidth || 0);
        const width = clientWidth || rectWidth || 0;
        if (Number.isFinite(width) && width > 0) return width;
      }
      return 0;
    }

    function documentCanvasHorizontalPadding() {
      const canvas = typeof documentCanvas !== "undefined" ? documentCanvas : null;
      if (!canvas || typeof getComputedStyle !== "function") return 0;
      try {
        const style = getComputedStyle(canvas);
        return Number.parseFloat(style.paddingLeft || "0") + Number.parseFloat(style.paddingRight || "0");
      } catch {
        return 0;
      }
    }

    function documentEffectiveLayoutZoom(normalized, size) {
      const requested = Number(normalized?.view?.zoom || 1);
      const pageWidth = Number(size?.widthPx || 0);
      const canvasWidth = documentCanvasBoxWidth();
      if (!Number.isFinite(requested) || requested <= 0) return 1;
      if (!Number.isFinite(pageWidth) || pageWidth <= 0 || !Number.isFinite(canvasWidth) || canvasWidth <= 0) return requested;
      const available = canvasWidth - documentCanvasHorizontalPadding() - DOCUMENT_AUTO_FIT_GUTTER;
      if (!Number.isFinite(available) || available <= 0) return requested;
      const fitZoom = Math.max(DOCUMENT_AUTO_FIT_MIN_ZOOM, Math.min(1, available / pageWidth));
      return Math.round(Math.min(requested, fitZoom) * 1000) / 1000;
    }

    function scheduleDocumentLayoutFitRefresh() {
      if (documentLayoutFitRefreshPending) return;
      documentLayoutFitRefreshPending = true;
      const schedule = typeof requestAnimationFrame === "function"
        ? requestAnimationFrame
        : (callback) => setTimeout(callback, 0);
      schedule(() => {
        documentLayoutFitRefreshPending = false;
        applyDocumentLayoutState(documentSession.layoutState);
      });
    }

    function installDocumentLayoutFitObserver() {
      const canvas = typeof documentCanvas !== "undefined" ? documentCanvas : null;
      const stage = typeof documentObjectStage !== "undefined" ? documentObjectStage : null;
      const app = document?.querySelector?.("#document-app") || null;
      const shell = document?.querySelector?.(".document-shell") || null;
      const observed = [canvas, stage, shell, app].filter(Boolean);
      if (observed.length && !documentLayoutFitObserver && typeof ResizeObserver === "function") {
        documentLayoutFitObserver = new ResizeObserver(() => scheduleDocumentLayoutFitRefresh());
        observed.forEach((el) => {
          try {
            documentLayoutFitObserver.observe(el);
          } catch {}
        });
      }
      if ((app || shell) && !documentLayoutVisibilityObserver && typeof MutationObserver === "function") {
        documentLayoutVisibilityObserver = new MutationObserver(() => scheduleDocumentLayoutFitRefresh());
        [app, shell].filter(Boolean).forEach((el) => {
          try {
            documentLayoutVisibilityObserver.observe(el, {attributes: true, attributeFilter: ["class", "style", "hidden", "aria-hidden"]});
          } catch {}
        });
      }
      if (typeof window !== "undefined" && window?.addEventListener) {
        window.addEventListener("resize", scheduleDocumentLayoutFitRefresh, {passive: true});
      }
      scheduleDocumentLayoutFitRefresh();
      setTimeout(() => scheduleDocumentLayoutFitRefresh(), 80);
      setTimeout(() => scheduleDocumentLayoutFitRefresh(), 240);
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
      const effectiveZoom = documentEffectiveLayoutZoom(normalized, size);
      documentWorkspaceStyle("--document-requested-zoom", normalized.view.zoom);
      documentWorkspaceStyle("--document-zoom", effectiveZoom);
      documentCanvas?.setAttribute("data-document-auto-fit", effectiveZoom < normalized.view.zoom ? "true" : "false");
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

    installDocumentLayoutFitObserver();
