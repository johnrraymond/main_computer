    const documentPdfExport = (() => {
      function cleanPdfId(value, fallback = "main-computer-document") {
        const cleaned = String(value || "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
        return cleaned || fallback;
      }
      function cleanPdfFilename(value) {
        return `${cleanPdfId(value, "main-computer-document")}.pdf`;
      }
      function cleanPdfVectorFilename(value) {
        return `${cleanPdfId(value, "main-computer-document")}-vector.pdf`;
      }
      function cleanPdfSmokeFilename(value) {
        return `${cleanPdfId(value, "main-computer-document")}-pdf-smoke.zip`;
      }
      function cleanPdfRasterSmokeFilename(value) {
        return `${cleanPdfId(value, "main-computer-document")}-raster-pdf-smoke.zip`;
      }
      function cleanPdfVectorFitSmokeFilename(value) {
        return `${cleanPdfId(value, "main-computer-document")}-vector-fit-smoke.zip`;
      }
      function currentPdfDocumentTitle() {
        const firstHeading = documentCanvas?.querySelector?.(".mc-page-content h1, .mc-page-content h2, .mc-page-content h3");
        const headingText = firstHeading?.textContent?.trim();
        if (headingText) return headingText.slice(0, 120);
        const pathText = documentCurrentPath?.textContent?.trim();
        if (pathText && pathText !== "local draft") return pathText.split("/").pop().replace(/\.[^.]+$/, "").slice(0, 120);
        return "Main Computer Document";
      }
      function stripEditorOnlyPdfState(root) {
        root.querySelectorAll("[data-document-caret-marker], .mc-page-break-guide").forEach((element) => element.remove());
        root.querySelectorAll(".document-plugin-anchor-highlight").forEach((element) => {
          element.classList.remove("document-plugin-anchor-highlight");
        });
        root.querySelectorAll(".selected").forEach((element) => {
          element.classList.remove("selected");
        });
        root.querySelectorAll("[contenteditable], [draggable], [spellcheck]").forEach((element) => {
          element.removeAttribute("contenteditable");
          element.removeAttribute("draggable");
          element.removeAttribute("spellcheck");
        });
        root.querySelectorAll("[role], [aria-multiline], [tabindex], [title], [aria-label]").forEach((element) => {
          if (element.getAttribute("role") === "button" || element.getAttribute("role") === "textbox") element.removeAttribute("role");
          element.removeAttribute("aria-multiline");
          element.removeAttribute("tabindex");
          element.removeAttribute("title");
          element.removeAttribute("aria-label");
        });
        return root;
      }
      const PDF_VECTOR_STYLE_PROPERTIES = [
        "background-color",
        "border-bottom-color",
        "border-bottom-style",
        "border-bottom-width",
        "border-left-color",
        "border-left-style",
        "border-left-width",
        "border-right-color",
        "border-right-style",
        "border-right-width",
        "border-top-color",
        "border-top-style",
        "border-top-width",
        "box-sizing",
        "color",
        "display",
        "font-family",
        "font-size",
        "font-stretch",
        "font-style",
        "font-variant",
        "font-weight",
        "height",
        "letter-spacing",
        "line-height",
        "list-style-position",
        "list-style-type",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "max-height",
        "max-width",
        "min-height",
        "min-width",
        "object-fit",
        "overflow",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "padding-top",
        "text-align",
        "text-decoration-color",
        "text-decoration-line",
        "text-decoration-style",
        "text-indent",
        "text-transform",
        "vertical-align",
        "white-space",
        "width",
        "word-break",
        "word-spacing",
        "overflow-wrap"
      ];
      function copyComputedStylesForPdfVectorExport(source, target) {
        if (source.nodeType !== Node.ELEMENT_NODE || target.nodeType !== Node.ELEMENT_NODE) return;
        const computed = window.getComputedStyle(source);
        // Vector export uses the live paginated editor DOM as the source of truth.
        // Copy all computed properties, not only a small allow-list, so Chromium
        // prints the same typography, transforms, margins, wrapping, and object
        // placement that the user saw in the editor while keeping text/vector
        // content instead of embedding page screenshots.
        for (let index = 0; index < computed.length; index += 1) {
          const property = computed.item(index);
          const value = computed.getPropertyValue(property);
          if (value) target.style.setProperty(property, value, computed.getPropertyPriority(property));
        }
        const sourceChildren = Array.from(source.childNodes);
        const targetChildren = Array.from(target.childNodes);
        sourceChildren.forEach((sourceChild, index) => {
          const targetChild = targetChildren[index];
          if (sourceChild && targetChild) copyComputedStylesForPdfVectorExport(sourceChild, targetChild);
        });
      }
      function clonePageContentForPdfExport(page) {
        const content = page?.querySelector?.(".mc-page-content");
        const clone = content ? content.cloneNode(true) : document.createElement("div");
        if (content) copyComputedStylesForPdfVectorExport(content, clone);
        stripEditorOnlyPdfState(clone);
        return clone.innerHTML;
      }
      function clonePageOverlayForPdfExport(page) {
        const overlay = page?.querySelector?.(":scope > .mc-page-overlay-layer");
        const clone = overlay ? overlay.cloneNode(true) : document.createElement("div");
        if (overlay) copyComputedStylesForPdfVectorExport(overlay, clone);
        stripEditorOnlyPdfState(clone);
        return clone.innerHTML;
      }
      function cloneLivePageForPdfVectorExport(page, index, targetSize) {
        const clone = page?.cloneNode?.(true);
        if (!clone) return null;
        copyComputedStylesForPdfVectorExport(page, clone);
        stripEditorOnlyPdfState(clone);
        clone.removeAttribute("id");
        clone.setAttribute("data-vector-live-page", String(index));
        clone.style.margin = "0";
        clone.style.border = "0";
        clone.style.borderRadius = "0";
        clone.style.boxShadow = "none";
        clone.style.outline = "0";
        clone.style.transform = "none";
        clone.style.transformOrigin = "top left";
        clone.style.position = "relative";
        clone.style.left = "auto";
        clone.style.top = "auto";
        clone.style.right = "auto";
        clone.style.bottom = "auto";
        clone.style.width = `${Math.max(1, Math.round(targetSize.widthPx))}px`;
        clone.style.height = `${Math.max(1, Math.round(targetSize.heightPx))}px`;
        clone.style.maxWidth = "none";
        clone.style.maxHeight = "none";
        clone.style.overflow = "hidden";
        clone.style.pageBreakAfter = "always";
        clone.style.breakAfter = "page";
        clone.style.webkitPrintColorAdjust = "exact";
        clone.style.printColorAdjust = "exact";
        return clone.outerHTML;
      }
      function collectPdfPages() {
        const pages = Array.from(documentCanvas?.querySelectorAll?.(".mc-page") || []);
        if (!pages.length) {
          return [{contentHtml: getDocumentEditorHtml(), overlayHtml: ""}];
        }
        return pages.map((page) => ({
          contentHtml: clonePageContentForPdfExport(page),
          overlayHtml: clonePageOverlayForPdfExport(page)
        }));
      }
      function collectPdfVectorPages() {
        const pages = Array.from(documentCanvas?.querySelectorAll?.(".mc-page") || []);
        if (!pages.length) return [];
        const targetSize = currentPdfPageSize();
        return pages.map((page, index) => ({
          index: index + 1,
          widthPx: Math.max(1, Math.round(targetSize.widthPx)),
          heightPx: Math.max(1, Math.round(targetSize.heightPx)),
          source: "client-live-dom-page-html",
          html: cloneLivePageForPdfVectorExport(page, index + 1, targetSize)
        })).filter((page) => page.html);
      }
      function currentPdfPlugins() {
        if (documentObjectRuntime?.loadHiddenPlugins) {
          return documentObjectRuntime.loadHiddenPlugins().filter((plugin) => plugin?.enabled !== false);
        }
        return [];
      }
      function currentPdfPageSize() {
        if (typeof documentLayoutSize === "function") {
          const size = documentLayoutSize(documentSession.layoutState);
          if (size?.widthPx && size?.heightPx) {
            return {widthPx: Number(size.widthPx), heightPx: Number(size.heightPx)};
          }
        }
        const rawLayout = documentSession?.layoutState?.layout || {};
        const preset = rawLayout.preset || "letter";
        const presets = typeof documentPagePresets === "undefined" ? {} : documentPagePresets;
        const presetSize = presets[preset] || presets.letter || {widthPx: 816, heightPx: 1056};
        if (rawLayout.mode === "custom" && rawLayout.custom) {
          return {
            widthPx: Number(rawLayout.custom.widthPx || presetSize.widthPx || 816),
            heightPx: Number(rawLayout.custom.heightPx || presetSize.heightPx || 1056)
          };
        }
        return {widthPx: Number(presetSize.widthPx || 816), heightPx: Number(presetSize.heightPx || 1056)};
      }
      function cssPixelsToNumber(value) {
        const parsed = Number.parseFloat(String(value || "0"));
        return Number.isFinite(parsed) ? parsed : 0;
      }
      function measurePageCaptureBox(page, targetSize) {
        const rect = page.getBoundingClientRect();
        const computed = window.getComputedStyle(page);
        const targetWidth = Math.max(1, Math.round(targetSize.widthPx));
        const targetHeight = Math.max(1, Math.round(targetSize.heightPx));
        const offsetWidth = Math.round(page.offsetWidth || rect.width || targetWidth);
        const offsetHeight = Math.round(page.offsetHeight || rect.height || targetHeight);
        const clientWidth = Math.round(page.clientWidth || 0);
        const clientHeight = Math.round(page.clientHeight || 0);
        const borderTop = cssPixelsToNumber(computed.borderTopWidth);
        const borderRight = cssPixelsToNumber(computed.borderRightWidth);
        const borderBottom = cssPixelsToNumber(computed.borderBottomWidth);
        const borderLeft = cssPixelsToNumber(computed.borderLeftWidth);
        const sourceWidth = Math.max(1, offsetWidth || Math.round(rect.width) || targetWidth);
        const sourceHeight = Math.max(1, offsetHeight || Math.round(rect.height) || targetHeight);
        return {
          sourceWidth,
          sourceHeight,
          diagnostics: {
            offsetWidthPx: offsetWidth,
            offsetHeightPx: offsetHeight,
            clientWidthPx: clientWidth,
            clientHeightPx: clientHeight,
            rectWidthPx: Math.round(rect.width || 0),
            rectHeightPx: Math.round(rect.height || 0),
            computedBoxSizing: computed.boxSizing || "",
            computedBorderTopPx: borderTop,
            computedBorderRightPx: borderRight,
            computedBorderBottomPx: borderBottom,
            computedBorderLeftPx: borderLeft
          }
        };
      }
      function copyComputedStylesForPdfCapture(source, target) {
        if (source.nodeType !== Node.ELEMENT_NODE || target.nodeType !== Node.ELEMENT_NODE) return;
        const computed = window.getComputedStyle(source);
        for (let index = 0; index < computed.length; index += 1) {
          const property = computed.item(index);
          const value = computed.getPropertyValue(property);
          if (value) target.style.setProperty(property, value, computed.getPropertyPriority(property));
        }
        const sourceChildren = Array.from(source.childNodes);
        const targetChildren = Array.from(target.childNodes);
        sourceChildren.forEach((sourceChild, index) => {
          const targetChild = targetChildren[index];
          if (sourceChild && targetChild) copyComputedStylesForPdfCapture(sourceChild, targetChild);
        });
      }
      async function freezeLivePageForPdfCapture(page, sourceWidth, sourceHeight) {
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const frozen = page.cloneNode(true);
        copyComputedStylesForPdfCapture(page, frozen);
        stripEditorOnlyPdfState(frozen);
        frozen.removeAttribute("id");
        frozen.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
        frozen.style.margin = "0";
        frozen.style.border = "0";
        frozen.style.borderRadius = "0";
        frozen.style.boxShadow = "none";
        frozen.style.outline = "0";
        frozen.style.transform = "none";
        frozen.style.transformOrigin = "top left";
        frozen.style.boxSizing = "border-box";
        frozen.style.width = `${sourceWidth}px`;
        frozen.style.height = `${sourceHeight}px`;
        frozen.style.maxWidth = "none";
        frozen.style.overflow = "hidden";
        return frozen;
      }
      function svgToImage(svgText) {
        return new Promise((resolve, reject) => {
          const blob = new Blob([svgText], {type: "image/svg+xml;charset=utf-8"});
          const url = URL.createObjectURL(blob);
          const image = new Image();
          image.onload = () => {
            URL.revokeObjectURL(url);
            resolve(image);
          };
          image.onerror = () => {
            URL.revokeObjectURL(url);
            reject(new Error("Live page image capture failed while loading SVG snapshot."));
          };
          image.src = url;
        });
      }
      function utf8ToBase64(value) {
        const bytes = new TextEncoder().encode(String(value || ""));
        const chunkSize = 0x8000;
        let binary = "";
        for (let offset = 0; offset < bytes.length; offset += chunkSize) {
          const chunk = bytes.subarray(offset, offset + chunkSize);
          binary += String.fromCharCode(...chunk);
        }
        return btoa(binary);
      }
      function svgDataUrl(svgText) {
        return `data:image/svg+xml;base64,${utf8ToBase64(svgText)}`;
      }
      function pdfCaptureErrorMessage(error) {
        return error?.message ? String(error.message) : String(error || "unknown capture error");
      }
      function canvasToPngDataUrl(canvas) {
        return new Promise((resolve, reject) => {
          if (!canvas.toBlob) {
            try {
              resolve(canvas.toDataURL("image/png"));
            } catch (error) {
              reject(error);
            }
            return;
          }
          canvas.toBlob((blob) => {
            if (!blob) {
              reject(new Error("Live page image capture failed while encoding PNG."));
              return;
            }
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result || ""));
            reader.onerror = () => reject(reader.error || new Error("Live page image capture failed while reading PNG."));
            reader.readAsDataURL(blob);
          }, "image/png");
        });
      }
      async function captureLivePagePngForPdf(page, index, targetSize) {
        const targetWidth = Math.max(1, Math.round(targetSize.widthPx));
        const targetHeight = Math.max(1, Math.round(targetSize.heightPx));
        const {sourceWidth, sourceHeight, diagnostics} = measurePageCaptureBox(page, targetSize);
        const frozen = await freezeLivePageForPdfCapture(page, sourceWidth, sourceHeight);
        const serialized = new XMLSerializer().serializeToString(frozen);
        const scaleX = targetWidth / sourceWidth;
        const scaleY = targetHeight / sourceHeight;
        const svgText = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${targetWidth}" height="${targetHeight}" viewBox="0 0 ${targetWidth} ${targetHeight}">
  <foreignObject x="0" y="0" width="${targetWidth}" height="${targetHeight}">
    <div xmlns="http://www.w3.org/1999/xhtml" style="width:${sourceWidth}px;height:${sourceHeight}px;overflow:hidden;transform-origin:0 0;transform:scale(${scaleX}, ${scaleY});">
      ${serialized}
    </div>
  </foreignObject>
</svg>`;
        const snapshot = {
          index,
          svgDataUrl: svgDataUrl(svgText),
          widthPx: targetWidth,
          heightPx: targetHeight,
          sourceWidthPx: sourceWidth,
          sourceHeightPx: sourceHeight,
          scaleX,
          scaleY,
          ...diagnostics,
          method: "client-svg-foreignobject-snapshot"
        };
        try {
          const image = await svgToImage(svgText);
          const canvas = document.createElement("canvas");
          canvas.width = targetWidth;
          canvas.height = targetHeight;
          const context = canvas.getContext("2d", {alpha: false});
          if (!context) throw new Error("Live page image capture failed: 2D canvas is not available.");
          context.fillStyle = "#f7f4eb";
          context.fillRect(0, 0, targetWidth, targetHeight);
          context.drawImage(image, 0, 0, targetWidth, targetHeight);
          const dataUrl = await canvasToPngDataUrl(canvas);
          return {
            ...snapshot,
            dataUrl,
            svgDataUrl: undefined,
            method: "client-svg-foreignobject-png"
          };
        } catch (error) {
          return {
            ...snapshot,
            method: "client-svg-foreignobject-backend-rasterize",
            clientPngError: pdfCaptureErrorMessage(error)
          };
        }
      }
      async function collectLivePdfPageImages() {
        const pages = Array.from(documentCanvas?.querySelectorAll?.(".mc-page") || []);
        if (!pages.length) return [];
        if (document.fonts?.ready) {
          try {
            await document.fonts.ready;
          } catch {
            // Font readiness is best-effort; capture still continues.
          }
        }
        const targetSize = currentPdfPageSize();
        const pageImages = [];
        for (let index = 0; index < pages.length; index += 1) {
          pageImages.push(await captureLivePagePngForPdf(pages[index], index + 1, targetSize));
        }
        return pageImages;
      }
      async function buildPdfPayload(options = {}) {
        const payload = {
          title: currentPdfDocumentTitle(),
          sourcePath: documentSession.selectedPath || "",
          contentHtml: getDocumentEditorHtml(),
          layoutState: documentSession.layoutState,
          pages: collectPdfPages(),
          plugins: currentPdfPlugins(),
          devicePixelRatio: window.devicePixelRatio || 1
        };
        if (options.includeLiveVectorPages) {
          payload.vectorPages = collectPdfVectorPages();
          payload.vectorPageSource = payload.vectorPages.length ? "client-live-dom-page-html" : "fallback-content-html";
        }
        if (options.includeLivePageImages) {
          payload.pageImages = await collectLivePdfPageImages();
          payload.pageImageSource = "client-live-dom";
        }
        return payload;
      }
      function downloadDocumentBlob(blob, filename) {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename;
        anchor.rel = "noopener";
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1500);
      }
      async function settleDocumentPagination() {
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }
      async function errorMessageFromPdfResponse(response) {
        try {
          const data = await response.json();
          return data.error || `HTTP ${response.status}`;
        } catch {
          return `HTTP ${response.status}`;
        }
      }
      async function postPdfPayloadForDownload(endpoint, payload) {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        if (!response.ok) throw new Error(await errorMessageFromPdfResponse(response));
        return response.blob();
      }
      async function exportCurrentDocumentAsPdf() {
        try {
          if (documentStatus) documentStatus.textContent = "Preparing vector PDF...";
          await settleDocumentPagination();
          const payload = await buildPdfPayload({includeLiveVectorPages: true});
          payload.exportStrategy = "chromium-vector-live-dom";
          const blob = await postPdfPayloadForDownload("/api/applications/docs/export/pdf", payload);
          downloadDocumentBlob(blob, cleanPdfFilename(payload.title));
          if (documentStatus) documentStatus.textContent = "Vector PDF exported";
        } catch (error) {
          if (documentStatus) documentStatus.textContent = `PDF export failed: ${error.message || error}`;
          throw error;
        }
      }
      async function exportCurrentDocumentAsVectorPdf() {
        try {
          if (documentStatus) documentStatus.textContent = "Preparing vector PDF...";
          await settleDocumentPagination();
          const payload = await buildPdfPayload({includeLiveVectorPages: true});
          payload.exportStrategy = "chromium-vector-live-dom";
          const blob = await postPdfPayloadForDownload("/api/applications/docs/export/pdf-vector", payload);
          downloadDocumentBlob(blob, cleanPdfVectorFilename(payload.title));
          if (documentStatus) documentStatus.textContent = "Vector PDF exported";
        } catch (error) {
          if (documentStatus) documentStatus.textContent = `Vector PDF export failed: ${error.message || error}`;
          throw error;
        }
      }
      async function exportCurrentDocumentPdfSmoke() {
        try {
          if (documentStatus) documentStatus.textContent = "Capturing live PDF smoke assets...";
          await settleDocumentPagination();
          const payload = await buildPdfPayload({includeLivePageImages: true});
          const blob = await postPdfPayloadForDownload("/api/applications/docs/export/pdf-smoke", payload);
          downloadDocumentBlob(blob, cleanPdfSmokeFilename(payload.title));
          if (documentStatus) documentStatus.textContent = "PDF smoke assets saved from live page images";
        } catch (error) {
          if (documentStatus) documentStatus.textContent = `PDF smoke export failed: ${error.message || error}`;
          throw error;
        }
      }
      async function exportCurrentDocumentPdfRasterSmoke() {
        try {
          if (documentStatus) documentStatus.textContent = "Capturing live raster PDF smoke bundle...";
          await settleDocumentPagination();
          const payload = await buildPdfPayload({includeLivePageImages: true});
          const blob = await postPdfPayloadForDownload("/api/applications/docs/export/pdf-raster-smoke", payload);
          downloadDocumentBlob(blob, cleanPdfRasterSmokeFilename(payload.title));
          if (documentStatus) documentStatus.textContent = "Raster PDF smoke bundle saved from live page images";
        } catch (error) {
          if (documentStatus) documentStatus.textContent = `Raster PDF smoke failed: ${error.message || error}`;
          throw error;
        }
      }
      async function exportCurrentDocumentPdfVectorFitSmoke() {
        try {
          if (documentStatus) documentStatus.textContent = "Searching vector PDF fit settings...";
          await settleDocumentPagination();
          const payload = await buildPdfPayload({includeLiveVectorPages: true, includeLivePageImages: true});
          const blob = await postPdfPayloadForDownload("/api/applications/docs/export/pdf-vector-fit-smoke", payload);
          downloadDocumentBlob(blob, cleanPdfVectorFitSmokeFilename(payload.title));
          if (documentStatus) documentStatus.textContent = "Vector fit smoke bundle saved";
        } catch (error) {
          if (documentStatus) documentStatus.textContent = `Vector fit smoke failed: ${error.message || error}`;
          throw error;
        }
      }
      return {buildPdfPayload, exportCurrentDocumentAsPdf, exportCurrentDocumentAsVectorPdf, exportCurrentDocumentPdfSmoke, exportCurrentDocumentPdfRasterSmoke, exportCurrentDocumentPdfVectorFitSmoke};
    })();

    function exportCurrentDocumentAsPdf() {
      return documentPdfExport.exportCurrentDocumentAsPdf();
    }

    function exportCurrentDocumentAsVectorPdf() {
      return documentPdfExport.exportCurrentDocumentAsVectorPdf();
    }

    function exportCurrentDocumentPdfSmoke() {
      return documentPdfExport.exportCurrentDocumentPdfSmoke();
    }

    function exportCurrentDocumentPdfRasterSmoke() {
      return documentPdfExport.exportCurrentDocumentPdfRasterSmoke();
    }

    function exportCurrentDocumentPdfVectorFitSmoke() {
      return documentPdfExport.exportCurrentDocumentPdfVectorFitSmoke();
    }

