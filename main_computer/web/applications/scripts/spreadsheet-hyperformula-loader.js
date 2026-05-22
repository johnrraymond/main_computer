    const SPREADSHEET_HYPERFORMULA_VERSION = "3.2.0";
    const SPREADSHEET_HYPERFORMULA_LICENSE_KEY = "gpl-v3";
    const SPREADSHEET_HYPERFORMULA_VENDOR_URL = "/applications/vendor/hyperformula/hyperformula.full.min.js";
    const SPREADSHEET_HYPERFORMULA_CDN_URL = "https://cdn.jsdelivr.net/npm/hyperformula@3.2.0/dist/hyperformula.full.min.js";
    let spreadsheetHyperFormulaLoadPromise = null;
    let spreadsheetHyperFormulaDiagnostic = "";

    function spreadsheetHyperFormulaLoadDiagnostic() {
      return spreadsheetHyperFormulaDiagnostic;
    }

    function spreadsheetHyperFormulaGlobal(root = window) {
      const candidate = root?.HyperFormula;
      if (!candidate) return null;
      if (typeof candidate.buildFromSheets === "function") return candidate;
      if (typeof candidate.HyperFormula?.buildFromSheets === "function") return candidate.HyperFormula;
      return null;
    }

    function spreadsheetLoadClassicScript(src, datasetName = "spreadsheetHyperformulaSrc") {
      return new Promise((resolve, reject) => {
        const existing = document.querySelector(`script[data-spreadsheet-hyperformula-src="${src}"]`);
        if (existing) {
          existing.addEventListener("load", resolve, {once: true});
          existing.addEventListener("error", () => reject(new Error(`HyperFormula source failed: ${src}`)), {once: true});
          return;
        }
        const script = document.createElement("script");
        script.async = true;
        script.src = src;
        script.dataset.spreadsheetHyperformulaLoader = "gpl-v3";
        script.dataset[datasetName] = src;
        script.setAttribute("data-spreadsheet-hyperformula-src", src);
        script.addEventListener("load", resolve, {once: true});
        script.addEventListener("error", () => reject(new Error(`HyperFormula source failed: ${src}`)), {once: true});
        document.head.append(script);
      });
    }

    async function spreadsheetTryHyperFormulaSource(src) {
      await spreadsheetLoadClassicScript(src);
      const HyperFormulaClass = spreadsheetHyperFormulaGlobal();
      if (!HyperFormulaClass) throw new Error(`HyperFormula did not register from ${src}`);
      return HyperFormulaClass;
    }

    function spreadsheetEnsureHyperFormulaLoaded() {
      const existing = spreadsheetHyperFormulaGlobal();
      if (existing) {
        spreadsheetHyperFormulaDiagnostic = `HyperFormula ${SPREADSHEET_HYPERFORMULA_VERSION} already registered.`;
        return Promise.resolve({HyperFormula: existing, source: "already-registered", version: SPREADSHEET_HYPERFORMULA_VERSION});
      }
      if (spreadsheetHyperFormulaLoadPromise) return spreadsheetHyperFormulaLoadPromise;
      const attempted = [SPREADSHEET_HYPERFORMULA_VENDOR_URL, SPREADSHEET_HYPERFORMULA_CDN_URL];
      spreadsheetHyperFormulaDiagnostic = `Loading HyperFormula ${SPREADSHEET_HYPERFORMULA_VERSION} from ${SPREADSHEET_HYPERFORMULA_VENDOR_URL}`;
      spreadsheetHyperFormulaLoadPromise = (async () => {
        const errors = [];
        for (const src of attempted) {
          try {
            const HyperFormulaClass = await spreadsheetTryHyperFormulaSource(src);
            spreadsheetHyperFormulaDiagnostic = `HyperFormula ${SPREADSHEET_HYPERFORMULA_VERSION} loaded from ${src}`;
            return {HyperFormula: HyperFormulaClass, source: src, version: SPREADSHEET_HYPERFORMULA_VERSION};
          } catch (error) {
            errors.push(`${src}: ${error?.message || error}`);
            spreadsheetHyperFormulaDiagnostic = `HyperFormula load failed from ${src}`;
          }
        }
        spreadsheetHyperFormulaLoadPromise = null;
        throw new Error(`HyperFormula could not be loaded. Attempted: ${errors.join(" | ")}`);
      })();
      return spreadsheetHyperFormulaLoadPromise;
    }

    function loadSpreadsheetHyperFormula() {
      return spreadsheetEnsureHyperFormulaLoaded();
    }
