    const SPREADSHEET_REVOGRID_VERSION = "4.20.0";
    const SPREADSHEET_REVOGRID_VENDOR_URL = "/applications/vendor/revogrid/revo-grid.esm.js";
    const SPREADSHEET_REVOGRID_CDN_URL = "https://unpkg.com/@revolist/revogrid@4.20.0/dist/revo-grid/revo-grid.esm.js";
    let spreadsheetRevoGridLoadPromise = null;
    let spreadsheetRevoGridDiagnostic = "";

    function spreadsheetRevoGridLoadDiagnostic() {
      return spreadsheetRevoGridDiagnostic;
    }

    function spreadsheetLoadModuleScript(src) {
      return new Promise((resolve, reject) => {
        const existing = document.querySelector(`script[data-spreadsheet-revogrid-src="${src}"]`);
        if (existing) {
          existing.addEventListener("load", resolve, {once: true});
          existing.addEventListener("error", () => reject(new Error(`RevoGrid source failed: ${src}`)), {once: true});
          return;
        }
        const script = document.createElement("script");
        script.type = "module";
        script.async = true;
        script.src = src;
        script.dataset.spreadsheetRevogridLoader = "community";
        script.dataset.spreadsheetRevogridSrc = src;
        script.addEventListener("load", resolve, {once: true});
        script.addEventListener("error", () => reject(new Error(`RevoGrid source failed: ${src}`)), {once: true});
        document.head.append(script);
      });
    }

    async function spreadsheetTryRevoGridSource(src) {
      await spreadsheetLoadModuleScript(src);
      if (!window.customElements?.whenDefined) throw new Error("Custom elements registry is unavailable.");
      await window.customElements.whenDefined("revo-grid");
      return src;
    }

    function spreadsheetEnsureRevoGridLoaded() {
      if (window.customElements?.get("revo-grid")) {
        spreadsheetRevoGridDiagnostic = `RevoGrid Community ${SPREADSHEET_REVOGRID_VERSION} already registered.`;
        return Promise.resolve({source: "already-registered", version: SPREADSHEET_REVOGRID_VERSION});
      }
      if (spreadsheetRevoGridLoadPromise) return spreadsheetRevoGridLoadPromise;
      const attempted = [SPREADSHEET_REVOGRID_VENDOR_URL, SPREADSHEET_REVOGRID_CDN_URL];
      spreadsheetRevoGridDiagnostic = `Loading RevoGrid Community ${SPREADSHEET_REVOGRID_VERSION} from ${SPREADSHEET_REVOGRID_VENDOR_URL}`;
      spreadsheetRevoGridLoadPromise = (async () => {
        const errors = [];
        for (const src of attempted) {
          try {
            const loadedSource = await spreadsheetTryRevoGridSource(src);
            spreadsheetRevoGridDiagnostic = `RevoGrid Community ${SPREADSHEET_REVOGRID_VERSION} loaded from ${loadedSource}`;
            return {source: loadedSource, version: SPREADSHEET_REVOGRID_VERSION};
          } catch (error) {
            errors.push(`${src}: ${error?.message || error}`);
            spreadsheetRevoGridDiagnostic = `RevoGrid load failed from ${src}`;
          }
        }
        spreadsheetRevoGridLoadPromise = null;
        throw new Error(`RevoGrid Community could not be loaded. Attempted: ${errors.join(" | ")}`);
      })();
      return spreadsheetRevoGridLoadPromise;
    }

    function loadSpreadsheetRevoGrid() {
      return spreadsheetEnsureRevoGridLoaded();
    }
