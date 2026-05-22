    const SPREADSHEET_CODE_LANGUAGES = new Set(["javascript", "python", "basic"]);
    const SPREADSHEET_WORKER_FILES = {
      javascript: "spreadsheet-js-worker.js",
      python: "spreadsheet-python-worker.js",
      basic: "spreadsheet-basic-worker.js",
    };
    const SPREADSHEET_WORKER_SOURCE_IDS = {
      javascript: "spreadsheet-js-worker-source",
      python: "spreadsheet-python-worker-source",
      basic: "spreadsheet-basic-worker-source",
    };

    function spreadsheetJsonClone(value) {
      try {
        return JSON.parse(JSON.stringify(value ?? null));
      } catch {
        return null;
      }
    }

    function spreadsheetNormalizeCell(cell = {}, options = {}) {
      const source = cell && typeof cell === "object" ? cell : {value: cell};
      const resetEvaluating = Boolean(options.resetEvaluating);
      const kind = ["value", "formula", "javascript", "python", "basic"].includes(String(source.kind || "").toLowerCase())
        ? String(source.kind || "value").toLowerCase()
        : "value";
      const language = SPREADSHEET_CODE_LANGUAGES.has(String(source.language || "").toLowerCase())
        ? String(source.language).toLowerCase()
        : SPREADSHEET_CODE_LANGUAGES.has(kind) ? kind : "none";
      let status = ["clean", "dirty", "evaluating", "error", "stale", "moved", "metadata_missing", "orphaned"].includes(String(source.status || "").toLowerCase())
        ? String(source.status || "clean").toLowerCase()
        : "clean";
      const output = source.output && typeof source.output === "object" ? {parts: Array.isArray(source.output.parts) ? source.output.parts : []} : {parts: []};
      if (resetEvaluating && status === "evaluating") {
        status = "dirty";
        output.parts = [{
          kind: "warning",
          title: "Interrupted run",
          content: "Previous code-cell run was interrupted before completion.",
          metadata: {},
        }, ...output.parts.filter((part) => part?.title !== "Running")];
      }
      return {
        value: String(source.value ?? ""),
        kind,
        language,
        source: String(source.source ?? ""),
        output,
        status,
        dependencies: Array.isArray(source.dependencies) ? source.dependencies.map((item) => String(item).toUpperCase()) : [],
        writes: Array.isArray(source.writes) ? source.writes : [],
        metadata: source.metadata && typeof source.metadata === "object" ? source.metadata : {},
      };
    }

    function spreadsheetNormalizeLoadedWorkbook(workbook) {
      const normalizedWorkbook = workbook && typeof workbook === "object" ? workbook : spreadsheetDefaultWorkbook();
      normalizedWorkbook.version = Number(normalizedWorkbook.version) || 1;
      normalizedWorkbook.metadata = normalizedWorkbook.metadata && typeof normalizedWorkbook.metadata === "object" ? normalizedWorkbook.metadata : {};
      if (!normalizedWorkbook.sheets || typeof normalizedWorkbook.sheets !== "object" || !Object.keys(normalizedWorkbook.sheets).length) {
        normalizedWorkbook.sheets = spreadsheetDefaultWorkbook().sheets;
      }
      Object.entries(normalizedWorkbook.sheets).forEach(([sheetName, sheet]) => {
        if (!sheet || typeof sheet !== "object") {
          normalizedWorkbook.sheets[sheetName] = {rows: 50, cols: 26, cells: {}};
          sheet = normalizedWorkbook.sheets[sheetName];
        }
        sheet.rows = Math.max(1, Number(sheet.rows) || 50);
        sheet.cols = Math.max(1, Number(sheet.cols) || 26);
        sheet.cells ||= {};
        Object.entries(sheet.cells).forEach(([ref, cell]) => {
          sheet.cells[ref] = spreadsheetNormalizeCell(cell, {resetEvaluating: true});
        });
      });
      if (!normalizedWorkbook.active_sheet || !Object.prototype.hasOwnProperty.call(normalizedWorkbook.sheets, normalizedWorkbook.active_sheet)) {
        normalizedWorkbook.active_sheet = Object.keys(normalizedWorkbook.sheets)[0] || "Sheet1";
      }
      return normalizedWorkbook;
    }

    function spreadsheetIsCodeCell(cell) {
      const normalized = spreadsheetNormalizeCell(cell);
      return SPREADSHEET_CODE_LANGUAGES.has(normalized.kind) || SPREADSHEET_CODE_LANGUAGES.has(normalized.language);
    }

    function spreadsheetSelectedRef() {
      return spreadsheetSelectedRange?.start || spreadsheetInspectorRef || "";
    }

    function spreadsheetGetCell(ref, create = false) {
      if (!ref) return null;
      const sheet = spreadsheetActiveSheet();
      if (create) sheet.cells[ref] ||= {};
      if (!sheet.cells[ref]) return null;
      sheet.cells[ref] = spreadsheetNormalizeCell(sheet.cells[ref]);
      return sheet.cells[ref];
    }

    function spreadsheetSetCellType(ref, type) {
      const cell = spreadsheetGetCell(ref, true);
      const kind = String(type || "value").toLowerCase();
      if (kind === "formula") {
        const formulaSource = typeof spreadsheetFormulaRawSource === "function" ? spreadsheetFormulaRawSource(cell) : "";
        const nextSource = formulaSource || (typeof spreadsheetIsFormulaSource === "function" && spreadsheetIsFormulaSource(cell.value) ? cell.value : "");
        const formulaCell = typeof spreadsheetNormalizeFormulaCell === "function"
          ? spreadsheetNormalizeFormulaCell({...cell, kind: "formula", language: "none", source: nextSource, status: "dirty"})
          : cell;
        Object.assign(cell, formulaCell, {kind: "formula", language: "none", status: "dirty"});
      } else {
        cell.kind = SPREADSHEET_CODE_LANGUAGES.has(kind) ? kind : "value";
        cell.language = SPREADSHEET_CODE_LANGUAGES.has(kind) ? kind : "none";
        if (cell.kind === "value") {
          cell.source = "";
          cell.metadata = cell.metadata && typeof cell.metadata === "object" ? cell.metadata : {};
          if (cell.metadata.formula) delete cell.metadata.formula;
          cell.status = "clean";
        } else {
          cell.status = "dirty";
          if (!cell.source) cell.source = cell.value || "";
        }
      }
      spreadsheetSetDirty(true, "cell type changed");
      if (typeof spreadsheetRefreshFormulaResults === "function") spreadsheetRefreshFormulaResults({preserveSelection: true});
      spreadsheetRenderInspector(ref);
      spreadsheetRefreshCellElement(ref);
    }

    function spreadsheetRefreshCellElement(ref) {
      const cell = spreadsheetGetCell(ref);
      if (!cell || !spreadsheetGridElement) return;
      const sheetName = typeof spreadsheetActiveSheetName === "function" ? spreadsheetActiveSheetName() : spreadsheetWorkbook?.active_sheet || "Sheet1";
      const parts = spreadsheetCellParts(ref);
      const source = Array.isArray(spreadsheetGridElement.source) ? spreadsheetGridElement.source.slice() : spreadsheetSheetToGridSource(spreadsheetActiveSheet(), sheetName);
      const row = source[parts.row - 1];
      if (!row) return;
      row[spreadsheetColumnName(parts.col)] = spreadsheetGridCellDisplayValue(cell, sheetName, ref);
      spreadsheetGridProgrammaticUpdate = true;
      spreadsheetGridElement.source = source;
      spreadsheetGridProgrammaticUpdate = false;
      if (spreadsheetSelectedRange?.cells?.includes(ref)) {
        spreadsheetRenderInspector(spreadsheetSelectedRange.start);
      }
    }

    function spreadsheetOutputText(value) {
      if (value == null) return "";
      if (typeof value === "string") return value;
      try {
        return JSON.stringify(value, null, 2);
      } catch {
        return String(value);
      }
    }

    function spreadsheetRefsForRangeLabel(label) {
      const [startRef, endRef = startRef] = String(label || "").split(":");
      return spreadsheetRangeRefs(startRef, endRef);
    }

    function spreadsheetWorkbookSnapshotForCodeCells(workbook = spreadsheetWorkbook) {
      const snapshot = spreadsheetJsonClone(workbook || spreadsheetDefaultWorkbook());
      const activeSheetName = snapshot?.active_sheet || "Sheet1";
      if (!snapshot?.sheets || typeof snapshot.sheets !== "object") return snapshot;
      Object.entries(snapshot.sheets).forEach(([sheetName, sheet]) => {
        if (!sheet?.cells || typeof sheet.cells !== "object") return;
        Object.entries(sheet.cells).forEach(([ref, cell]) => {
          if (!cell || typeof cell !== "object") return;
          if (typeof spreadsheetIsFormulaCell !== "function" || !spreadsheetIsFormulaCell(cell)) return;
          const displayValue = typeof spreadsheetFormulaDisplayValue === "function"
            ? spreadsheetFormulaDisplayValue(cell, sheetName || activeSheetName, ref)
            : String(cell.value ?? "");
          sheet.cells[ref] = {
            ...cell,
            value: String(displayValue ?? ""),
            metadata: {
              ...(cell.metadata && typeof cell.metadata === "object" ? cell.metadata : {}),
              formula_snapshot: {
                source: typeof spreadsheetFormulaRawSource === "function" ? spreadsheetFormulaRawSource(cell) : String(cell.source || ""),
                value: String(displayValue ?? ""),
                captured_at: new Date().toISOString(),
              },
            },
          };
        });
      });
      return snapshot;
    }

    function renderSpreadsheetOutputParts(parts = [], container = spreadsheetCellOutput) {
      container.textContent = "";
      if (!parts.length) {
        const empty = document.createElement("div");
        empty.className = "spreadsheet-output-part";
        empty.textContent = "No output yet.";
        container.append(empty);
        return;
      }
      parts.forEach((part) => {
        const block = document.createElement("div");
        block.className = "spreadsheet-output-part";
        block.dataset.kind = part.kind || "text";
        const title = document.createElement("strong");
        title.textContent = part.title || part.kind || "output";
        const content = document.createElement("div");
        content.textContent = spreadsheetOutputText(part.content);
        block.append(title, content);
        container.append(block);
      });
    }

    function renderSpreadsheetWritePreview(writes = []) {
      spreadsheetWritePreview.textContent = "";
      if (!writes.length) {
        spreadsheetWritePreview.textContent = "No pending write previews.";
        spreadsheetApplyWrites.disabled = true;
        return;
      }
      spreadsheetApplyWrites.disabled = false;
      writes.forEach((write) => {
        const item = document.createElement("div");
        item.className = "spreadsheet-output-part";
        item.dataset.kind = "write_preview";
        item.textContent = `${write.target}: ${spreadsheetOutputText(write.value)}`;
        spreadsheetWritePreview.append(item);
      });
    }

    function spreadsheetRenderInspector(ref = spreadsheetSelectedRef()) {
      spreadsheetInspectorRef = ref || "";
      const cell = ref ? (spreadsheetGetCell(ref, false) || spreadsheetNormalizeCell({})) : null;
      const sheetName = typeof spreadsheetActiveSheetName === "function" ? spreadsheetActiveSheetName() : spreadsheetWorkbook?.active_sheet || "Sheet1";
      const isFormula = Boolean(cell && typeof spreadsheetIsFormulaCell === "function" && spreadsheetIsFormulaCell(cell));
      const isCode = spreadsheetIsCodeCell(cell);
      spreadsheetSelectedCell.textContent = ref ? `Selected: ${sheetName}!${ref}` : "No cell selected";
      spreadsheetCellType.value = isFormula ? "formula" : cell && SPREADSHEET_CODE_LANGUAGES.has(cell.kind) ? cell.kind : "value";
      spreadsheetCellSource.value = isFormula && typeof spreadsheetFormulaRawSource === "function" ? spreadsheetFormulaRawSource(cell) : cell?.source || "";
      spreadsheetCellSource.disabled = !(isCode || isFormula);
      spreadsheetRunCell.disabled = !isCode || !ref;
      if (isFormula) {
        spreadsheetCodeStatus.textContent = typeof spreadsheetFormulaStatusText === "function"
          ? spreadsheetFormulaStatusText(cell, sheetName, ref)
          : "formula cell";
      } else {
        spreadsheetCodeStatus.textContent = cell ? `status: ${cell.status || "clean"}${cell.dependencies?.length ? `, reads ${cell.dependencies.join(", ")}` : ""}` : "code cell ready";
      }
      spreadsheetPendingWrites = isFormula ? [] : cell?.writes || [];
      const formulaOutputParts = isFormula && typeof spreadsheetFormulaOutputParts === "function"
        ? spreadsheetFormulaOutputParts(cell, sheetName, ref)
        : [];
      renderSpreadsheetOutputParts(isFormula ? formulaOutputParts : cell?.output?.parts || []);
      renderSpreadsheetWritePreview(spreadsheetPendingWrites);
      if (typeof spreadsheetRenderChatImportHistory === "function") spreadsheetRenderChatImportHistory(ref, cell);
    }

    function spreadsheetWorkerSource(language) {
      const id = SPREADSHEET_WORKER_SOURCE_IDS[language];
      const node = id ? document.getElementById(id) : null;
      const source = node?.textContent || "";
      if (!source.trim()) throw new Error(`${language} worker source is missing.`);
      if (source.includes("@include applications/scripts/")) {
        throw new Error("Spreadsheet worker source was not expanded by the viewport include system.");
      }
      if (!source.includes("self.onmessage")) {
        throw new Error("Spreadsheet worker source is invalid: missing self.onmessage.");
      }
      return source;
    }

    function spreadsheetWorkerErrorResponse(request, title, error, durationMs = 0) {
      const message = error && error.message ? error.message : String(error || title || "Worker failed.");
      return {
        id: request?.id || "",
        ok: false,
        value: null,
        writes: [],
        output_parts: [{kind: "error", title, content: message, metadata: {}}],
        dependencies: [],
        error: message,
        duration_ms: durationMs,
      };
    }

    function spreadsheetWorkerSourceSummary(language) {
      const id = SPREADSHEET_WORKER_SOURCE_IDS[language];
      const node = id ? document.getElementById(id) : null;
      const source = node?.textContent || "";
      return {
        language,
        hasSource: Boolean(source.trim()),
        length: source.length,
        hasSelfOnMessage: source.includes("self.onmessage"),
        hasUnexpandedInclude: source.includes("@include applications/scripts/"),
      };
    }

    function getSpreadsheetRuntimeTimeout(language) {
      if (language === "python") return 60000;
      if (language === "basic") return 10000;
      return 2000;
    }

    function runSpreadsheetWorker(language, request, timeoutMs = 2000) {
      return new Promise((resolve) => {
        let worker = null;
        let workerUrl = "";
        let timer = null;
        let settled = false;
        const cleanup = () => {
          if (timer) {
            clearTimeout(timer);
            timer = null;
          }
          if (worker) {
            worker.terminate();
            worker = null;
          }
          if (workerUrl) {
            URL.revokeObjectURL(workerUrl);
            workerUrl = "";
          }
        };
        const finish = (response) => {
          if (settled) return;
          settled = true;
          cleanup();
          resolve({
            ok: false,
            value: null,
            writes: [],
            output_parts: [],
            dependencies: [],
            error: null,
            duration_ms: 0,
            ...(response || {}),
          });
        };
        try {
          const blob = new Blob([spreadsheetWorkerSource(language)], {type: "text/javascript"});
          workerUrl = URL.createObjectURL(blob);
          worker = new Worker(workerUrl);
        } catch (error) {
          finish(spreadsheetWorkerErrorResponse(request, "Worker error", error));
          return;
        }
        timer = setTimeout(() => {
          finish({
            id: request?.id || "",
            ok: false,
            value: null,
            writes: [],
            output_parts: [{kind: "error", title: "Execution timeout", content: `Cell execution exceeded ${timeoutMs}ms and was terminated.`, metadata: {}}],
            dependencies: [],
            error: "execution timeout",
            duration_ms: timeoutMs,
          });
        }, timeoutMs);
        worker.onmessage = (event) => {
          finish(event.data || {});
        };
        worker.onerror = (event) => {
          finish(spreadsheetWorkerErrorResponse(request, "Worker error", event.message || "Worker failed."));
        };
        worker.onmessageerror = (event) => {
          finish(spreadsheetWorkerErrorResponse(request, "Worker message error", event?.message || "Worker response could not be cloned."));
        };
        try {
          worker.postMessage(request);
        } catch (error) {
          finish(spreadsheetWorkerErrorResponse(request, "Worker postMessage error", error));
        }
      });
    }

    async function testSpreadsheetWorker(language = "javascript") {
      const testSource = language === "python"
        ? "1 + 1"
        : language === "basic"
          ? "PRINT 1+1"
          : "1+1";
      const request = {
        id: `spreadsheet-test-${Date.now()}`,
        language,
        source: testSource,
        workbook_snapshot: spreadsheetJsonClone(spreadsheetWorkbook || spreadsheetDefaultWorkbook()),
        active_sheet: spreadsheetWorkbook?.active_sheet || "Sheet1",
        cell_ref: spreadsheetSelectedRef() || "A1",
        timeout_ms: getSpreadsheetRuntimeTimeout(language),
      };
      const response = await runSpreadsheetWorker(language, request, request.timeout_ms);
      console.log("Spreadsheet worker test", language, response);
      return response;
    }

    window.spreadsheetWorkerSourceSummary = spreadsheetWorkerSourceSummary;
    window.testSpreadsheetWorker = testSpreadsheetWorker;

    function applySpreadsheetCodeResponse(ref, response = {}) {
      const cell = spreadsheetGetCell(ref, true);
      if (!cell) return;
      const ok = Boolean(response.ok);
      cell.status = ok ? "clean" : "error";
      const resultValue = response.value;
      if (resultValue !== undefined && resultValue !== null) {
        cell.value = typeof resultValue === "object" ? JSON.stringify(resultValue) : String(resultValue);
      }
      cell.output = {
        parts: Array.isArray(response.output_parts) ? response.output_parts : [],
        text: "",
        error: response.error || "",
        updated_at: new Date().toISOString(),
      };
      cell.dependencies = Array.isArray(response.dependencies) ? response.dependencies : [];
      cell.writes = Array.isArray(response.writes) ? response.writes : [];
      spreadsheetSetDirty(true, ok ? "code cell executed" : "code cell failed");
      spreadsheetRenderInspector(ref);
      spreadsheetRefreshCellElement(ref);
    }

    async function runSpreadsheetSelectedCell() {
      const ref = spreadsheetSelectedRef();
      const cell = spreadsheetGetCell(ref, true);
      if (!ref || !spreadsheetIsCodeCell(cell)) {
        spreadsheetCodeStatus.textContent = "select a code cell first";
        return;
      }
      const language = cell.language || cell.kind;
      cell.status = "evaluating";
      cell.output = {parts: [{kind: "text", title: "Running", content: `Running ${language} cell...`, metadata: {}}]};
      cell.writes = [];
      spreadsheetRenderInspector(ref);
      spreadsheetRefreshCellElement(ref);
      try {
        if (typeof spreadsheetEnsureHyperFormulaLoaded === "function" && typeof spreadsheetRecalculateFormulas === "function") {
          await spreadsheetEnsureHyperFormulaLoaded().catch(() => null);
          spreadsheetRecalculateFormulas(spreadsheetWorkbook);
        }
        const request = {
          id: `spreadsheet-${Date.now()}-${Math.random().toString(16).slice(2)}`,
          language,
          source: cell.source,
          workbook_snapshot: spreadsheetWorkbookSnapshotForCodeCells(spreadsheetWorkbook),
          active_sheet: spreadsheetWorkbook?.active_sheet || "Sheet1",
          cell_ref: ref,
          timeout_ms: getSpreadsheetRuntimeTimeout(language),
        };
        const response = await runSpreadsheetWorker(language, request, request.timeout_ms);
        applySpreadsheetCodeResponse(ref, response);
      } catch (error) {
        applySpreadsheetCodeResponse(ref, spreadsheetWorkerErrorResponse({id: ""}, "Code cell error", error));
      }
    }

    function applySpreadsheetWritePreview() {
      const writes = spreadsheetPendingWrites || [];
      if (!writes.length) return;
      writes.forEach((write) => {
        if (!/^[A-Z]{1,3}[1-9][0-9]*(:[A-Z]{1,3}[1-9][0-9]*)?$/.test(String(write.target || ""))) return;
        if (write.kind === "attachCode") {
          const cell = spreadsheetGetCell(write.target, true);
          const language = SPREADSHEET_CODE_LANGUAGES.has(String(write.language || "").toLowerCase()) ? String(write.language).toLowerCase() : "basic";
          cell.kind = language;
          cell.language = language;
          cell.source = String(write.source ?? write.value ?? "");
          cell.status = "dirty";
          cell.metadata = cell.metadata && typeof cell.metadata === "object" ? cell.metadata : {};
          cell.metadata.attached_from_write_preview = {
            attached_at: new Date().toISOString(),
            source_cell: spreadsheetInspectorRef || "",
            language,
          };
        } else if (write.kind === "writeRange" && Array.isArray(write.value)) {
          const refs = spreadsheetRefsForRangeLabel(write.target).cells;
          refs.forEach((ref, index) => {
            const row = Math.floor(index / (Array.isArray(write.value[0]) ? write.value[0].length : 1));
            const col = index - row * (Array.isArray(write.value[0]) ? write.value[0].length : 1);
            const nextValue = Array.isArray(write.value[row]) ? write.value[row][col] : write.value[index];
            const cell = spreadsheetGetCell(ref, true);
            cell.value = String(nextValue ?? "");
            cell.kind = "value";
            cell.language = "none";
            cell.status = "clean";
          });
        } else {
          const cell = spreadsheetGetCell(write.target, true);
          cell.value = String(write.value ?? "");
          cell.kind = "value";
          cell.language = "none";
          cell.status = "clean";
        }
      });
      const source = spreadsheetGetCell(spreadsheetInspectorRef);
      if (source) source.writes = [];
      spreadsheetSetDirty(true, "write preview applied");
      if (typeof spreadsheetRefreshFormulaResults === "function") spreadsheetRefreshFormulaResults({preserveSelection: true});
      renderDiskSpreadsheet();
      if (spreadsheetInspectorRef) setSpreadsheetSelection(spreadsheetInspectorRef);
    }
