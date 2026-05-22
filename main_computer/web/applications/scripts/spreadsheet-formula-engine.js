    const SPREADSHEET_FORMULA_ENGINE_ID = "hyperformula";
    const SPREADSHEET_FORMULA_CELL_KIND = "formula";
    const SPREADSHEET_FORMULA_CODE_KINDS = new Set(["javascript", "python", "basic"]);
    let spreadsheetFormulaCache = Object.create(null);
    let spreadsheetFormulaState = {
      ok: false,
      engine: SPREADSHEET_FORMULA_ENGINE_ID,
      formulaCount: 0,
      cache: spreadsheetFormulaCache,
      error: "Formula engine has not run yet.",
    };

    function spreadsheetIsFormulaSource(value) {
      const text = String(value ?? "").trim();
      return text.length > 1 && text.startsWith("=");
    }

    function spreadsheetNormalizeFormulaSource(value) {
      const text = String(value ?? "").trim();
      if (!text) return "";
      return text.startsWith("=") ? text : `=${text}`;
    }

    function spreadsheetFormulaCellSource(cell = {}) {
      const source = cell && typeof cell === "object" ? cell : {};
      const metadataFormula = source.metadata?.formula;
      if (spreadsheetIsFormulaSource(source.source)) return spreadsheetNormalizeFormulaSource(source.source);
      if (spreadsheetIsFormulaSource(source.value)) return spreadsheetNormalizeFormulaSource(source.value);
      if (typeof metadataFormula === "string" && metadataFormula.trim()) return spreadsheetNormalizeFormulaSource(metadataFormula);
      if (metadataFormula && typeof metadataFormula === "object" && String(metadataFormula.source || "").trim()) {
        return spreadsheetNormalizeFormulaSource(metadataFormula.source);
      }
      return "";
    }

    function spreadsheetIsFormulaCell(cell = {}) {
      const source = cell && typeof cell === "object" ? cell : {};
      const kind = String(source.kind || "").toLowerCase();
      if (SPREADSHEET_FORMULA_CODE_KINDS.has(kind)) return false;
      return kind === SPREADSHEET_FORMULA_CELL_KIND || spreadsheetIsFormulaSource(source.source) || spreadsheetIsFormulaSource(source.value);
    }

    function spreadsheetNormalizeFormulaCell(cell = {}) {
      const source = cell && typeof cell === "object" ? cell : {value: cell};
      const formulaSource = spreadsheetFormulaCellSource(source);
      if (!formulaSource) return {...source};
      return {
        value: String(source.value ?? ""),
        kind: SPREADSHEET_FORMULA_CELL_KIND,
        language: "none",
        source: formulaSource,
        output: source.output && typeof source.output === "object" ? source.output : {parts: []},
        status: ["clean", "dirty", "error"].includes(String(source.status || "").toLowerCase()) ? String(source.status || "clean").toLowerCase() : "clean",
        dependencies: Array.isArray(source.dependencies) ? source.dependencies : [],
        writes: Array.isArray(source.writes) ? source.writes : [],
        metadata: {
          ...(source.metadata && typeof source.metadata === "object" ? source.metadata : {}),
          formula: {
            ...(source.metadata?.formula && typeof source.metadata.formula === "object" ? source.metadata.formula : {}),
            engine: SPREADSHEET_FORMULA_ENGINE_ID,
            source: formulaSource,
          },
        },
      };
    }

    function spreadsheetFormulaCacheKey(sheetName, ref) {
      return `${sheetName || "Sheet1"}!${String(ref || "").toUpperCase()}`;
    }

    function spreadsheetFormulaSheetNames(workbook) {
      const sheets = workbook?.sheets && typeof workbook.sheets === "object" ? workbook.sheets : {};
      const names = Object.keys(sheets);
      return names.length ? names : ["Sheet1"];
    }

    function spreadsheetFormulaCellParts(ref) {
      if (typeof spreadsheetCellParts === "function") return spreadsheetCellParts(ref);
      const match = String(ref || "A1").toUpperCase().match(/^([A-Z]+)([1-9][0-9]*)$/);
      if (!match) return {col: 1, row: 1};
      const col = match[1].split("").reduce((total, char) => total * 26 + char.charCodeAt(0) - 64, 0);
      return {col, row: Number(match[2])};
    }

    function spreadsheetFormulaCellRef(row, col) {
      if (typeof spreadsheetCellRef === "function") return spreadsheetCellRef(row, col);
      let name = "";
      let value = Number(col) || 1;
      while (value > 0) {
        const remainder = (value - 1) % 26;
        name = String.fromCharCode(65 + remainder) + name;
        value = Math.floor((value - 1) / 26);
      }
      return `${name || "A"}${Number(row) || 1}`;
    }

    function spreadsheetFormulaCellInput(cell = {}) {
      if (spreadsheetIsFormulaCell(cell)) return spreadsheetNormalizeFormulaCell(cell).source;
      const source = cell && typeof cell === "object" ? cell : {value: cell};
      if (SPREADSHEET_FORMULA_CODE_KINDS.has(String(source.kind || "").toLowerCase())) {
        return source.value ?? "";
      }
      return source.value ?? "";
    }

    function spreadsheetWorkbookToHyperFormulaSheets(workbook) {
      const normalized = workbook && typeof workbook === "object"
        ? workbook
        : typeof spreadsheetDefaultWorkbook === "function" ? spreadsheetDefaultWorkbook() : {sheets: {}};
      const result = {};
      spreadsheetFormulaSheetNames(normalized).forEach((sheetName) => {
        const sheet = normalized.sheets?.[sheetName] || {};
        const rows = Math.max(1, Number(sheet.rows) || 1);
        const cols = Math.max(1, Number(sheet.cols) || 1);
        const matrix = Array.from({length: rows}, () => Array.from({length: cols}, () => ""));
        const cells = sheet.cells && typeof sheet.cells === "object" ? sheet.cells : {};
        Object.entries(cells).forEach(([ref, cell]) => {
          const parts = spreadsheetFormulaCellParts(ref);
          if (parts.row < 1 || parts.row > rows || parts.col < 1 || parts.col > cols) return;
          matrix[parts.row - 1][parts.col - 1] = spreadsheetFormulaCellInput(cell);
        });
        result[sheetName] = matrix;
      });
      return result;
    }

    function spreadsheetHyperFormulaClassFromGlobal(root = typeof window !== "undefined" ? window : globalThis) {
      if (typeof spreadsheetHyperFormulaGlobal === "function") return spreadsheetHyperFormulaGlobal(root);
      const candidate = root?.HyperFormula;
      if (!candidate) return null;
      if (typeof candidate.buildFromSheets === "function") return candidate;
      if (typeof candidate.HyperFormula?.buildFromSheets === "function") return candidate.HyperFormula;
      return null;
    }

    function spreadsheetFormulaErrorText(value) {
      if (typeof value === "string" && value.trim().startsWith("#")) return value.trim();
      if (!value || typeof value !== "object") return "";
      if (value.type || value.message) return String(value.message || value.type || "Formula error");
      if (value.value && String(value.value).startsWith("#")) return String(value.value);
      return "";
    }

    function spreadsheetFormulaDisplayText(value) {
      const error = spreadsheetFormulaErrorText(value);
      if (error) return error.startsWith("#") ? error : `#${error}`;
      if (value == null) return "";
      if (typeof value === "object") {
        try {
          return JSON.stringify(value);
        } catch {
          return String(value);
        }
      }
      return String(value);
    }

    function spreadsheetFormulaCellAddress(sheetId, ref) {
      const parts = spreadsheetFormulaCellParts(ref);
      return {sheet: sheetId, row: parts.row - 1, col: parts.col - 1};
    }

    function spreadsheetRecalculateFormulas(workbook = spreadsheetWorkbook, options = {}) {
      const HyperFormulaClass = options.HyperFormula || spreadsheetHyperFormulaClassFromGlobal(options.root);
      const nextCache = Object.create(null);
      let formulaCount = 0;
      if (!HyperFormulaClass || typeof HyperFormulaClass.buildFromSheets !== "function") {
        spreadsheetFormulaCache = nextCache;
        spreadsheetFormulaState = {
          ok: false,
          engine: SPREADSHEET_FORMULA_ENGINE_ID,
          formulaCount,
          cache: nextCache,
          error: "HyperFormula is not loaded.",
        };
        return spreadsheetFormulaState;
      }
      const sheets = spreadsheetWorkbookToHyperFormulaSheets(workbook);
      let hf = null;
      try {
        hf = HyperFormulaClass.buildFromSheets(sheets, {
          licenseKey: typeof SPREADSHEET_HYPERFORMULA_LICENSE_KEY === "string" ? SPREADSHEET_HYPERFORMULA_LICENSE_KEY : "gpl-v3",
        });
        spreadsheetFormulaSheetNames(workbook).forEach((sheetName) => {
          const sheet = workbook?.sheets?.[sheetName] || {};
          const cells = sheet.cells && typeof sheet.cells === "object" ? sheet.cells : {};
          const sheetId = typeof hf.getSheetId === "function" ? hf.getSheetId(sheetName) : sheetName;
          Object.entries(cells).forEach(([ref, cell]) => {
            if (!spreadsheetIsFormulaCell(cell)) return;
            formulaCount += 1;
            const address = spreadsheetFormulaCellAddress(sheetId, ref);
            const raw = typeof hf.getCellValue === "function" ? hf.getCellValue(address) : "";
            const error = spreadsheetFormulaErrorText(raw);
            const value = spreadsheetFormulaDisplayText(raw);
            nextCache[spreadsheetFormulaCacheKey(sheetName, ref)] = {
              sheet: sheetName,
              ref: String(ref || "").toUpperCase(),
              value,
              raw,
              error,
              source: spreadsheetFormulaCellSource(cell),
              engine: SPREADSHEET_FORMULA_ENGINE_ID,
            };
          });
        });
        spreadsheetFormulaCache = nextCache;
        spreadsheetFormulaState = {
          ok: true,
          engine: SPREADSHEET_FORMULA_ENGINE_ID,
          formulaCount,
          cache: nextCache,
          error: "",
        };
        return spreadsheetFormulaState;
      } catch (error) {
        spreadsheetFormulaCache = nextCache;
        spreadsheetFormulaState = {
          ok: false,
          engine: SPREADSHEET_FORMULA_ENGINE_ID,
          formulaCount,
          cache: nextCache,
          error: error?.message || String(error || "Formula recalculation failed."),
        };
        return spreadsheetFormulaState;
      } finally {
        if (hf && typeof hf.destroy === "function") {
          try {
            hf.destroy();
          } catch {
            // Ignore engine disposal errors. Formula results were already captured.
          }
        }
      }
    }

    function spreadsheetFormulaDisplayValue(cell = {}, sheetName = "", ref = "", cache = spreadsheetFormulaCache) {
      if (!sheetName) {
        sheetName = "Sheet1";
        try {
          if (typeof spreadsheetWorkbook === "object" && spreadsheetWorkbook?.active_sheet) sheetName = spreadsheetWorkbook.active_sheet;
        } catch {
          sheetName = "Sheet1";
        }
      }
      if (!spreadsheetIsFormulaCell(cell)) return cell && typeof cell === "object" ? String(cell.value ?? "") : String(cell ?? "");
      const item = ref ? cache?.[spreadsheetFormulaCacheKey(sheetName, ref)] : null;
      if (item) return String(item.value ?? "");
      return String(cell.value ?? "");
    }

    function spreadsheetFormulaRawSource(cell = {}) {
      return spreadsheetFormulaCellSource(cell);
    }

    function spreadsheetFormulaSourceForSave(cell = {}) {
      return spreadsheetIsFormulaCell(cell) ? spreadsheetNormalizeFormulaCell(cell).source : "";
    }

    function spreadsheetFormulaCacheEntry(sheetName = "", ref = "", cache = spreadsheetFormulaCache) {
      const nextSheetName = sheetName || (typeof spreadsheetWorkbook === "object" && spreadsheetWorkbook?.active_sheet ? spreadsheetWorkbook.active_sheet : "Sheet1");
      return ref ? cache?.[spreadsheetFormulaCacheKey(nextSheetName, ref)] || null : null;
    }

    function spreadsheetFormulaOutputParts(cell = {}, sheetName = "", ref = "", cache = spreadsheetFormulaCache) {
      if (!spreadsheetIsFormulaCell(cell)) return [];
      const item = spreadsheetFormulaCacheEntry(sheetName, ref, cache);
      const source = spreadsheetFormulaRawSource(cell);
      if (item?.error) {
        return [{
          kind: "error",
          title: "Formula error",
          content: `${source || "(blank)"} -> ${item.error}`,
          metadata: {
            engine: SPREADSHEET_FORMULA_ENGINE_ID,
            ref: String(ref || "").toUpperCase(),
            sheet: sheetName || "Sheet1",
          },
        }];
      }
      if (item) {
        return [{
          kind: "text",
          title: "Formula result",
          content: `${source || "(blank)"} = ${spreadsheetFormulaDisplayText(item.raw)}`,
          metadata: {
            engine: SPREADSHEET_FORMULA_ENGINE_ID,
            ref: String(ref || "").toUpperCase(),
            sheet: sheetName || "Sheet1",
          },
        }];
      }
      const stateError = spreadsheetFormulaState?.error || "";
      return [{
        kind: stateError ? "warning" : "text",
        title: stateError ? "Formula pending" : "Formula source",
        content: stateError ? `${source || "(blank)"} -> ${stateError}` : source || "(blank)",
        metadata: {
          engine: SPREADSHEET_FORMULA_ENGINE_ID,
          ref: String(ref || "").toUpperCase(),
          sheet: sheetName || "Sheet1",
        },
      }];
    }

    function spreadsheetFormulaStatusText(cell = {}, sheetName = "", ref = "", cache = spreadsheetFormulaCache) {
      if (!spreadsheetIsFormulaCell(cell)) return "";
      const item = spreadsheetFormulaCacheEntry(sheetName, ref, cache);
      const source = spreadsheetFormulaRawSource(cell);
      if (item?.error) return `formula error: ${item.error}`;
      if (item) return `formula: ${source || "(blank)"} = ${spreadsheetFormulaDisplayText(item.raw)}`;
      const stateError = spreadsheetFormulaState?.error || "";
      return stateError ? `formula pending: ${stateError}` : `formula: ${source || "(blank)"}`;
    }

    function spreadsheetFormulaCellFromEditValue(value, previousCell = {}) {
      const text = String(value ?? "");
      if (!spreadsheetIsFormulaSource(text)) return null;
      return spreadsheetNormalizeFormulaCell({
        ...(previousCell && typeof previousCell === "object" ? previousCell : {}),
        value: "",
        kind: SPREADSHEET_FORMULA_CELL_KIND,
        language: "none",
        source: spreadsheetNormalizeFormulaSource(text),
        status: "dirty",
      });
    }
