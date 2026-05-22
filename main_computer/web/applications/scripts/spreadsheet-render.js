      renderDiskSpreadsheet();
      spreadsheetSetDirty(false, "disk workbook loaded");
      spreadsheetUpdatePathUi();
      if (typeof spreadsheetMountChatThreadController === "function") spreadsheetMountChatThreadController();
    }
    async function createSpreadsheet(path = "untitled.json") {
      const data = await spreadsheetApi("/api/applications/spreadsheet/create", {path, rows: 50, cols: 26});
      spreadsheetWorkbook = spreadsheetNormalizeLoadedWorkbook(data.workbook || spreadsheetDefaultWorkbook());
      spreadsheetPath = data.path || path;
      spreadsheetContentHash = data.content_hash || "";
      spreadsheetSelectedPath = spreadsheetPath;
      renderDiskSpreadsheet();
      spreadsheetSetDirty(false, "disk workbook created");
      if (typeof spreadsheetMountChatThreadController === "function") spreadsheetMountChatThreadController();
      await loadSpreadsheetFiles();
    }
    async function spreadsheetRecalculateBeforePersistence() {
      if (typeof spreadsheetEnsureHyperFormulaLoaded === "function" && typeof spreadsheetRecalculateFormulas === "function") {
        await spreadsheetEnsureHyperFormulaLoaded().catch(() => null);
        spreadsheetRecalculateFormulas(spreadsheetWorkbook);
        return;
      }
      if (typeof spreadsheetRecalculateFormulas === "function") spreadsheetRecalculateFormulas(spreadsheetWorkbook);
    }

    async function saveSpreadsheet(path = spreadsheetPath) {
      await spreadsheetRecalculateBeforePersistence();
      const data = await spreadsheetApi("/api/applications/spreadsheet/write", {
        path,
        expected_content_hash: path === spreadsheetPath ? spreadsheetContentHash : "",
        workbook: spreadsheetWorkbook
      });
      spreadsheetPath = data.path || path;
      spreadsheetContentHash = data.content_hash || "";
      spreadsheetSelectedPath = spreadsheetPath;
      spreadsheetSetDirty(false, "saved to disk");
      await loadSpreadsheetFiles();
    }
    const SPREADSHEET_XLSX_IMPORT_MAX_BYTES = 10 * 1024 * 1024;

    function spreadsheetArrayBufferToBase64(buffer) {
      const bytes = new Uint8Array(buffer);
      const chunkSize = 0x8000;
      let binary = "";
      for (let index = 0; index < bytes.length; index += chunkSize) {
        const chunk = bytes.subarray(index, index + chunkSize);
        binary += String.fromCharCode(...chunk);
      }
      return btoa(binary);
    }

    function spreadsheetBase64ToBlob(contentBase64, contentType = "application/octet-stream") {
      const binary = atob(String(contentBase64 || ""));
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      return new Blob([bytes], {type: contentType});
    }

    function spreadsheetDownloadBlob(filename, blob) {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename || "spreadsheet.xlsx";
      anchor.style.display = "none";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    }

    function spreadsheetWorkbookForXlsxExport() {
      const workbook = spreadsheetJsonClone(spreadsheetWorkbook || spreadsheetDefaultWorkbook());
      if (!workbook?.sheets || typeof workbook.sheets !== "object") return workbook;
      Object.entries(workbook.sheets).forEach(([sheetName, sheet]) => {
        if (!sheet?.cells || typeof sheet.cells !== "object") return;
        Object.entries(sheet.cells).forEach(([ref, cell]) => {
          if (!cell || typeof cell !== "object") return;
          if (typeof spreadsheetIsFormulaCell === "function" && spreadsheetIsFormulaCell(cell)) {
            sheet.cells[ref] = {
              ...cell,
              value: typeof spreadsheetFormulaDisplayValue === "function" ? spreadsheetFormulaDisplayValue(cell, sheetName, ref) : String(cell.value ?? ""),
            };
          }
        });
      });
      return workbook;
    }

    async function exportSpreadsheetXlsx() {
      await spreadsheetRecalculateBeforePersistence();
      const data = await spreadsheetApi("/api/applications/spreadsheet/export-xlsx", {
        path: spreadsheetPath,
        workbook: spreadsheetWorkbookForXlsxExport(),
      });
      const blob = spreadsheetBase64ToBlob(
        data.content_base64,
        data.content_type || "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      );
      spreadsheetDownloadBlob(data.filename || "spreadsheet.xlsx", blob);
      spreadsheetPlotStatus.textContent = `XLSX exported: ${data.filename} (${data.bytes || blob.size} bytes)`;
      return data;
    }

    async function importSpreadsheetXlsxFile(file) {
      if (!file) return null;
      if (!/\.xlsx$/i.test(file.name || "")) throw new Error("Choose an .xlsx file to import.");
      if (file.size > SPREADSHEET_XLSX_IMPORT_MAX_BYTES) throw new Error("XLSX import is limited to 10 MB.");
      spreadsheetStatus.textContent = `importing ${file.name}...`;
      const contentBase64 = spreadsheetArrayBufferToBase64(await file.arrayBuffer());
      const data = await spreadsheetApi("/api/applications/spreadsheet/import-xlsx", {
        filename: file.name,
        content_base64: contentBase64
      });
      spreadsheetWorkbook = spreadsheetNormalizeLoadedWorkbook(data.workbook || spreadsheetDefaultWorkbook());
      spreadsheetPath = data.path || spreadsheetPath.replace(/\.json$/i, "-imported.json");
      spreadsheetContentHash = "";
      spreadsheetSelectedPath = "";
      spreadsheetSelectionAnchor = null;
      spreadsheetSelectedRange = null;
      renderDiskSpreadsheet();
      spreadsheetUpdatePathUi();
      const warningText = Array.isArray(data.warnings) && data.warnings.length ? ` (${data.warnings.length} warning${data.warnings.length === 1 ? "" : "s"})` : "";
      spreadsheetSetDirty(true, `imported ${file.name}; save to persist${warningText}`);
      if (typeof spreadsheetMountChatThreadController === "function") spreadsheetMountChatThreadController();
      return data;
    }
    let spreadsheetGridElement = null;
    let spreadsheetGridProgrammaticUpdate = false;

    function spreadsheetSheetToGridColumns(sheet) {
      const cols = Math.max(26, Number(sheet?.cols) || 26);
      const columns = [];
      for (let col = 1; col <= cols; col += 1) {
        const name = spreadsheetColumnName(col);
        columns.push({prop: name, name, size: 110});
      }
      return columns;
    }

    function spreadsheetActiveSheetName() {
      return typeof spreadsheetEnsureActiveSheetName === "function"
        ? spreadsheetEnsureActiveSheetName()
        : spreadsheetWorkbook?.active_sheet || Object.keys(spreadsheetWorkbook?.sheets || {})[0] || "Sheet1";
    }

    function spreadsheetRenderSheetTabs() {
      if (!spreadsheetSheetTabs) return;
      const names = typeof spreadsheetSheetNames === "function" ? spreadsheetSheetNames() : Object.keys(spreadsheetWorkbook?.sheets || {});
      const active = spreadsheetActiveSheetName();
      spreadsheetSheetTabs.textContent = "";
      names.forEach((sheetName) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "spreadsheet-sheet-tab";
        button.dataset.sheetName = sheetName;
        button.setAttribute("role", "tab");
        button.setAttribute("aria-selected", sheetName === active ? "true" : "false");
        button.classList.toggle("active", sheetName === active);
        button.title = sheetName === active ? `${sheetName} (active sheet)` : `Switch to ${sheetName}`;
        button.textContent = sheetName;
        button.addEventListener("click", () => spreadsheetActivateSheet(sheetName));
        button.addEventListener("dblclick", () => spreadsheetPromptRenameSheet(sheetName));
        button.addEventListener("contextmenu", (event) => {
          event.preventDefault();
          spreadsheetPromptSheetAction(sheetName);
        });
        spreadsheetSheetTabs.append(button);
      });
      if (spreadsheetSheetDelete) spreadsheetSheetDelete.disabled = names.length <= 1;
    }

    function spreadsheetMarkSheetStructureChanged(message) {
      spreadsheetSetDirty(true, message);
      renderDiskSpreadsheet();
      if (typeof spreadsheetMountChatThreadController === "function") spreadsheetMountChatThreadController();
    }

    function spreadsheetActivateSheet(sheetName) {
      if (!sheetName || sheetName === spreadsheetActiveSheetName()) {
        spreadsheetRenderSheetTabs();
        return;
      }
      const sheets = typeof spreadsheetWorkbookSheets === "function" ? spreadsheetWorkbookSheets() : spreadsheetWorkbook?.sheets || {};
      if (!Object.prototype.hasOwnProperty.call(sheets, sheetName)) return;
      spreadsheetWorkbook.active_sheet = sheetName;
      spreadsheetSelectionAnchor = null;
      spreadsheetSelectedRange = null;
      spreadsheetMarkSheetStructureChanged(`active sheet: ${sheetName}`);
    }

    function spreadsheetPromptAddSheet() {
      const defaultName = typeof spreadsheetUniqueSheetName === "function" ? spreadsheetUniqueSheetName("Sheet") : "Sheet";
      const requested = prompt("New sheet name", defaultName);
      if (requested == null) return "";
      const created = typeof spreadsheetAddWorkbookSheet === "function" ? spreadsheetAddWorkbookSheet(requested || defaultName) : "";
      if (!created) return "";
      spreadsheetSelectionAnchor = null;
      spreadsheetSelectedRange = null;
      spreadsheetMarkSheetStructureChanged(`sheet added: ${created}`);
      return created;
    }

    function spreadsheetPromptRenameSheet(sheetName = spreadsheetActiveSheetName()) {
      const current = sheetName || spreadsheetActiveSheetName();
      const requested = prompt("Rename sheet", current);
      if (requested == null) return "";
      const renamed = typeof spreadsheetRenameWorkbookSheet === "function" ? spreadsheetRenameWorkbookSheet(current, requested) : "";
      if (!renamed) return "";
      spreadsheetMarkSheetStructureChanged(renamed === current ? `sheet unchanged: ${current}` : `sheet renamed: ${renamed}`);
      return renamed;
    }

    function spreadsheetPromptDuplicateSheet(sheetName = spreadsheetActiveSheetName()) {
      const created = typeof spreadsheetDuplicateWorkbookSheet === "function" ? spreadsheetDuplicateWorkbookSheet(sheetName) : "";
      if (!created) return "";
      spreadsheetSelectionAnchor = null;
      spreadsheetSelectedRange = null;
      spreadsheetMarkSheetStructureChanged(`sheet duplicated: ${created}`);
      return created;
    }

    function spreadsheetPromptDeleteSheet(sheetName = spreadsheetActiveSheetName()) {
      const names = typeof spreadsheetSheetNames === "function" ? spreadsheetSheetNames() : Object.keys(spreadsheetWorkbook?.sheets || {});
      if (names.length <= 1) {
        spreadsheetStatus.textContent = "workbook must keep at least one sheet";
        return "";
      }
      const target = sheetName || spreadsheetActiveSheetName();
      if (!confirm(`Delete sheet "${target}"? This cannot be undone.`)) return "";
      const next = typeof spreadsheetDeleteWorkbookSheet === "function" ? spreadsheetDeleteWorkbookSheet(target) : "";
      if (!next) return "";
      spreadsheetSelectionAnchor = null;
      spreadsheetSelectedRange = null;
      spreadsheetMarkSheetStructureChanged(`sheet deleted: ${target}; active sheet: ${next}`);
      return next;
    }

    function spreadsheetPromptSheetAction(sheetName = spreadsheetActiveSheetName()) {
      const action = String(prompt(`Sheet "${sheetName}": type rename, duplicate, or delete`, "rename") || "").trim().toLowerCase();
      if (action === "rename" || action === "r") return spreadsheetPromptRenameSheet(sheetName);
      if (action === "duplicate" || action === "copy" || action === "d") return spreadsheetPromptDuplicateSheet(sheetName);
      if (action === "delete" || action === "remove") return spreadsheetPromptDeleteSheet(sheetName);
      return "";
    }

    function spreadsheetGridCellDisplayValue(cell, sheetName = spreadsheetActiveSheetName(), ref = "") {
      const normalized = spreadsheetNormalizeCell(cell || {});
      if (typeof spreadsheetIsFormulaCell === "function" && spreadsheetIsFormulaCell(normalized)) {
        return spreadsheetFormulaDisplayValue(normalized, sheetName, ref);
      }
      if (!spreadsheetIsCodeCell(normalized)) return normalized.value || "";
      const label = (normalized.language || normalized.kind || "code").slice(0, 4).toUpperCase();
      const status = normalized.status && normalized.status !== "clean" ? ` ${normalized.status}` : "";
      return normalized.value ? normalized.value : `[${label}${status}]`;
    }

    function spreadsheetSheetToGridSource(sheet, sheetName = spreadsheetActiveSheetName()) {
      const rows = Math.max(50, Number(sheet?.rows) || 50);
      const cols = Math.max(26, Number(sheet?.cols) || 26);
      const source = [];
      for (let row = 1; row <= rows; row += 1) {
        const model = {__rowIndex: row - 1};
        for (let col = 1; col <= cols; col += 1) {
          const prop = spreadsheetColumnName(col);
          const ref = spreadsheetCellRef(row, col);
          model[prop] = spreadsheetGridCellDisplayValue(sheet?.cells?.[ref], sheetName, ref);
        }
        source.push(model);
      }
      return source;
    }

    function spreadsheetGridPositionToRef(rowIndex, propOrColumnIndex) {
      const row = Math.max(1, Number(rowIndex) + 1 || 1);
      const col = typeof propOrColumnIndex === "number"
        ? propOrColumnIndex + 1
        : spreadsheetColumnIndex(String(propOrColumnIndex || "A"));
      return spreadsheetCellRef(row, col);
    }

    function spreadsheetGridColumnPosition(detail, fallback = 0) {
      const source = detail?.column?.prop ?? detail?.column?.name ?? detail?.prop ?? detail?.x ?? detail?.colIndex ?? detail?.rgCol ?? fallback;
      if (typeof source === "number") return source;
      return Math.max(0, spreadsheetColumnIndex(String(source || "A")) - 1);
    }

    function spreadsheetNormalizeGridRange(eventOrDetail) {
      const detail = eventOrDetail?.detail || eventOrDetail || {};
      const range = detail.range || detail.newRange || detail.oldRange || detail;
      const focus = detail.focus || detail.start || range.focus || null;
      const end = detail.end || range.end || null;
      const startRow = range.y ?? range.rowIndex ?? range.rgRow ?? focus?.y ?? focus?.rowIndex ?? detail.rowIndex ?? detail.rgRow;
      const endRow = range.y1 ?? range.rowIndexEnd ?? range.rgRowEnd ?? end?.y ?? end?.rowIndex ?? startRow;
      const startCol = range.x ?? range.colIndex ?? range.rgCol ?? focus?.x ?? focus?.colIndex ?? spreadsheetGridColumnPosition(detail);
      const endCol = range.x1 ?? range.colIndexEnd ?? range.rgColEnd ?? end?.x ?? end?.colIndex ?? startCol;
      if (startRow == null || startCol == null) return null;
      return {
        startRef: spreadsheetGridPositionToRef(Number(startRow), startCol),
        endRef: spreadsheetGridPositionToRef(Number(endRow ?? startRow), endCol ?? startCol),
      };
    }

    async function spreadsheetRangeFromGridState(grid) {
      if (!grid) return null;
      try {
        const selected = await grid.getSelectedRange?.();
        const normalizedRange = spreadsheetNormalizeGridRange(selected);
        if (normalizedRange) return normalizedRange;
      } catch {}
      try {
        const focused = await grid.getFocused?.();
        const normalizedFocus = spreadsheetNormalizeGridRange(focused);
        if (normalizedFocus) return normalizedFocus;
      } catch {}
      return null;
    }

    function spreadsheetApplyGridSelection(eventOrDetail) {
      const normalized = spreadsheetNormalizeGridRange(eventOrDetail);
      if (normalized) {
        spreadsheetSelectionAnchor = normalized.startRef;
        setSpreadsheetSelection(normalized.startRef, normalized.endRef);
        return;
      }
      spreadsheetRangeFromGridState(spreadsheetGridElement).then((fallback) => {
        if (!fallback) return;
        spreadsheetSelectionAnchor = fallback.startRef;
        setSpreadsheetSelection(fallback.startRef, fallback.endRef);
      });
    }

    function spreadsheetGridDetailRows(detail) {
      if (!detail || typeof detail !== "object") return [];
      if (detail.models && typeof detail.models === "object") {
        return Object.entries(detail.models).map(([rowIndex, model]) => ({rowIndex: Number(rowIndex), model}));
      }
      if (detail.model && typeof detail.model === "object") {
        const rowIndex = Number(detail.rgRow ?? detail.rowIndex ?? detail.row ?? detail.model.__rowIndex ?? detail.cell?.y ?? detail.cell?.rowIndex ?? 0);
        return [{rowIndex, model: detail.model}];
      }
      if (detail.data && typeof detail.data === "object") {
        return Object.entries(detail.data).map(([rowIndex, model]) => ({rowIndex: Number(rowIndex), model}));
      }
      return [];
    }

    function spreadsheetPlainValueCell(value = "") {
      return spreadsheetNormalizeCell({
        value: String(value ?? ""),
        kind: "value",
        language: "none",
        source: "",
        output: {parts: []},
        status: "clean",
        dependencies: [],
        writes: [],
        metadata: {},
      });
    }

    function spreadsheetApplyGridValue(ref, value) {
      const sheet = spreadsheetActiveSheet();
      sheet.cells ||= {};
      const existing = spreadsheetGetCell(ref, true);
      if (!existing) return;
      const formulaCell = typeof spreadsheetFormulaCellFromEditValue === "function" ? spreadsheetFormulaCellFromEditValue(value, existing) : null;
      if (formulaCell) {
        sheet.cells[ref] = formulaCell;
        return;
      }
      if (typeof spreadsheetIsFormulaCell === "function" && spreadsheetIsFormulaCell(existing)) {
        sheet.cells[ref] = spreadsheetPlainValueCell(value);
        return;
      }
      existing.value = String(value ?? "");
      if (spreadsheetIsCodeCell(existing)) existing.status = "dirty";
    }

    function spreadsheetGridEditValue(detail) {
      if (!detail || typeof detail !== "object") return {hasValue: false, value: ""};
      for (const key of ["val", "value", "newValue", "nextValue"]) {
        if (Object.prototype.hasOwnProperty.call(detail, key)) return {hasValue: true, value: detail[key]};
      }
      return {hasValue: false, value: ""};
    }

    function spreadsheetSyncGridCellFromWorkbook(ref) {
      if (!ref) return;
      window.requestAnimationFrame?.(() => spreadsheetRefreshCellElement(ref)) || setTimeout(() => spreadsheetRefreshCellElement(ref), 0);
    }

    function spreadsheetApplyGridEditToWorkbook(eventOrDetail) {
      if (spreadsheetGridProgrammaticUpdate) return;
      const detail = eventOrDetail?.detail || eventOrDetail || {};
      const sheet = spreadsheetActiveSheet();
      let changed = false;
      let firstRef = "";
      const directProp = detail.prop ?? detail.column?.prop ?? detail.column?.name ?? (detail.rgCol != null ? detail.rgCol : null);
      const editValue = spreadsheetGridEditValue(detail);
      if (directProp != null && editValue.hasValue) {
        const rowIndex = Number(detail.rgRow ?? detail.rowIndex ?? detail.row ?? detail.model?.__rowIndex ?? detail.cell?.y ?? detail.cell?.rowIndex ?? 0);
        const ref = spreadsheetGridPositionToRef(rowIndex, directProp);
        spreadsheetApplyGridValue(ref, editValue.value);
        changed = true;
        firstRef = ref;
      }
      spreadsheetGridDetailRows(detail).forEach(({rowIndex, model}) => {
        if (!model || typeof model !== "object") return;
        Object.keys(model).forEach((prop) => {
          if (prop.startsWith("__")) return;
          const ref = spreadsheetGridPositionToRef(rowIndex, prop);
          if (!sheet.cells[ref] && (model[prop] == null || model[prop] === "")) return;
          spreadsheetApplyGridValue(ref, model[prop]);
          firstRef ||= ref;
          changed = true;
        });
      });
      if (!changed) return;
      spreadsheetSetDirty(true, "local edits pending disk save");
      if (typeof spreadsheetRefreshFormulaResults === "function") spreadsheetRefreshFormulaResults({preserveSelection: true});
      if (firstRef) {
        spreadsheetSelectionAnchor = firstRef;
        setSpreadsheetSelection(firstRef);
        spreadsheetRenderInspector(firstRef);
        spreadsheetSyncGridCellFromWorkbook(firstRef);
      }
    }

    function spreadsheetGridRefFromDetail(detail) {
      const source = detail?.detail || detail || {};
      const prop = source.prop || source.column?.prop || source.column?.name || source.model?.prop || source.cell?.prop || source.cell?.x;
      const rowIndex = source.rgRow ?? source.rowIndex ?? source.row ?? source.model?.__rowIndex ?? source.cell?.y ?? source.cell?.rowIndex;
      if (prop == null || rowIndex == null) return "";
      return spreadsheetGridPositionToRef(Number(rowIndex), prop);
    }

    function configureSpreadsheetGrid(grid) {
      grid.theme = "darkCompact";
      grid.setAttribute("theme", "darkCompact");
      grid.range = true;
      grid.useClipboard = true;
      grid.canFocus = true;
      grid.applyOnClose = true;
    }

    function ensureSpreadsheetGrid() {
      let host = document.getElementById("spreadsheet-grid-host");
      if (!host) {
        spreadsheetContainer.textContent = "";
        host = document.createElement("div");
        host.id = "spreadsheet-grid-host";
        host.className = "spreadsheet-grid-host";
        spreadsheetContainer.append(host);
      }
      let grid = host.querySelector("revo-grid#spreadsheet-grid");
      if (!grid) {
        host.textContent = "";
        grid = document.createElement("revo-grid");
        grid.id = "spreadsheet-grid";
        grid.className = "spreadsheet-grid";
        grid.setAttribute("row-headers", "true");
        configureSpreadsheetGrid(grid);
        // Persist only after RevoGrid has accepted the editor value. Lower-level
        // celledit/rangeeditapply events fire before RevoGrid saves and refreshing
        // source there can make a typed value disappear.
        grid.addEventListener("afteredit", spreadsheetApplyGridEditToWorkbook);
        grid.addEventListener("focuscell", spreadsheetApplyGridSelection);
        grid.addEventListener("afterfocus", spreadsheetApplyGridSelection);
        grid.addEventListener("setrange", spreadsheetApplyGridSelection);
        grid.addEventListener("selectionchangeinit", spreadsheetApplyGridSelection);
        host.append(grid);
      }
      configureSpreadsheetGrid(grid);
      spreadsheetGridElement = grid;
      return grid;
    }

    function renderSpreadsheetGridUnavailable(error) {
      spreadsheetContainer.innerHTML = "";
      const fallback = document.createElement("div");
      fallback.className = "spreadsheet-grid-error";
      fallback.innerHTML = "<strong>RevoGrid unavailable</strong>";
      const message = document.createElement("p");
      const diagnostic = typeof spreadsheetRevoGridLoadDiagnostic === "function" ? spreadsheetRevoGridLoadDiagnostic() : "";
      message.textContent = [error?.message || "Spreadsheet grid widget could not be loaded.", diagnostic].filter(Boolean).join(" ");
      fallback.append(message);
      spreadsheetContainer.append(fallback);
      spreadsheetStatus.textContent = "RevoGrid unavailable";
    }

    function spreadsheetRangeRefs(startRef, endRef) {
      const start = spreadsheetCellParts(startRef);
      const end = spreadsheetCellParts(endRef || startRef);
      const rowMin = Math.min(start.row, end.row);
      const rowMax = Math.max(start.row, end.row);
      const colMin = Math.min(start.col, end.col);
      const colMax = Math.max(start.col, end.col);
      const cells = [];
      for (let row = rowMin; row <= rowMax; row += 1) {
        for (let col = colMin; col <= colMax; col += 1) cells.push(spreadsheetCellRef(row, col));
      }
      return {start: spreadsheetCellRef(rowMin, colMin), end: spreadsheetCellRef(rowMax, colMax), rowMin, rowMax, colMin, colMax, cells};
    }
    function setSpreadsheetSelection(startRef, endRef = startRef) {
      spreadsheetSelectedRange = spreadsheetRangeRefs(startRef, endRef);
      const label = spreadsheetSelectedRange.start === spreadsheetSelectedRange.end ? spreadsheetSelectedRange.start : `${spreadsheetSelectedRange.start}:${spreadsheetSelectedRange.end}`;
      spreadsheetSelectionStatus.textContent = `selection: ${spreadsheetActiveSheetName()}!${label}`;
      spreadsheetRenderInspector(spreadsheetSelectedRange.start);
      if (typeof spreadsheetRenderAiRangeContextPreview === "function") spreadsheetRenderAiRangeContextPreview();
    }
    function clearSpreadsheetSelection() {
      spreadsheetSelectionAnchor = null;
      spreadsheetSelectedRange = null;
      spreadsheetSelectionStatus.textContent = "selection: none";
      spreadsheetRenderInspector("");
      if (typeof spreadsheetRenderAiRangeContextPreview === "function") spreadsheetRenderAiRangeContextPreview();
    }
    function spreadsheetRecalculateFormulaCache() {
      if (typeof spreadsheetRecalculateFormulas !== "function") return null;
      return spreadsheetRecalculateFormulas(spreadsheetWorkbook);
    }

    function spreadsheetRefreshGridSourceFromWorkbook() {
      if (!spreadsheetGridElement) return;
      const sheet = spreadsheetActiveSheet();
      spreadsheetGridProgrammaticUpdate = true;
      spreadsheetGridElement.source = spreadsheetSheetToGridSource(sheet, spreadsheetActiveSheetName());
      spreadsheetGridProgrammaticUpdate = false;
      spreadsheetRenderSheetTabs();
    }

    function spreadsheetRefreshFormulaResults(options = {}) {
      const state = spreadsheetRecalculateFormulaCache();
      spreadsheetRefreshGridSourceFromWorkbook();
      if (options.preserveSelection && spreadsheetSelectedRange?.start) spreadsheetRenderInspector(spreadsheetSelectedRange.start);
      return state;
    }

    function renderDiskSpreadsheet() {
      const sheet = spreadsheetActiveSheet();
      spreadsheetRenderSheetTabs();
      const rows = Math.max(50, Number(sheet.rows) || 50);
      const cols = Math.max(26, Number(sheet.cols) || 26);
      sheet.rows = rows;
      sheet.cols = cols;
      Object.entries(sheet.cells).forEach(([ref, cell]) => {
        sheet.cells[ref] = spreadsheetNormalizeCell(cell);
      });
      const applyGrid = () => {
        const grid = ensureSpreadsheetGrid();
        spreadsheetRecalculateFormulaCache();
        spreadsheetGridProgrammaticUpdate = true;
        grid.columns = spreadsheetSheetToGridColumns(sheet);
        grid.source = spreadsheetSheetToGridSource(sheet, spreadsheetActiveSheetName());
        spreadsheetGridProgrammaticUpdate = false;
        spreadsheetStatus.textContent = "RevoGrid ready";
        spreadsheetUpdatePathUi();
        clearSpreadsheetSelection();
        if (typeof spreadsheetEnsureHyperFormulaLoaded === "function") {
          spreadsheetEnsureHyperFormulaLoaded().then(() => {
            spreadsheetRefreshFormulaResults();
            spreadsheetStatus.textContent = spreadsheetFormulaState?.ok ? "RevoGrid ready; formulas ready" : "RevoGrid ready; formula cache unavailable";
          }).catch((error) => {
            spreadsheetRecalculateFormulaCache();
            spreadsheetStatus.textContent = `RevoGrid ready; formula engine unavailable: ${error?.message || error}`;
          });
        }
      };
      spreadsheetStatus.textContent = "loading RevoGrid...";
      spreadsheetEnsureRevoGridLoaded().then(applyGrid).catch(renderSpreadsheetGridUnavailable);
    }
    function plotSpreadsheetSelection() {
      const canvas = spreadsheetPlotCanvas;
      const context = canvas.getContext("2d");
