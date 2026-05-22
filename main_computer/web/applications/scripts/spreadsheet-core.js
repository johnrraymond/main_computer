    function spreadsheetColumnName(index) {
      let name = "";
      let value = Number(index) || 1;
      while (value > 0) {
        const remainder = (value - 1) % 26;
        name = String.fromCharCode(65 + remainder) + name;
        value = Math.floor((value - 1) / 26);

      }
      return name || "A";
    }
    function spreadsheetColumnIndex(name) {
      return String(name || "A").toUpperCase().split("").reduce((total, char) => total * 26 + char.charCodeAt(0) - 64, 0);
    }
    function spreadsheetCellParts(ref) {
      const match = String(ref || "A1").toUpperCase().match(/^([A-Z]+)([1-9][0-9]*)$/);
      return match ? {col: spreadsheetColumnIndex(match[1]), row: Number(match[2])} : {col: 1, row: 1};
    }
    function spreadsheetCellRef(row, col) {
      return `${spreadsheetColumnName(col)}${row}`;
    }
    function spreadsheetCoreClone(value, fallback = null) {
      try {
        return JSON.parse(JSON.stringify(value ?? fallback));
      } catch {
        return fallback;
      }
    }
    function spreadsheetNormalizeSheetName(rawName = "Sheet") {
      let name = String(rawName || "Sheet").replace(/[\\/\?\*\[\]:]/g, "-").trim();
      if (!name || name === "__main_computer_metadata") name = "Sheet";
      return name.slice(0, 31).trim() || "Sheet";
    }
    function spreadsheetWorkbookSheets() {
      if (!spreadsheetWorkbook || typeof spreadsheetWorkbook !== "object") spreadsheetWorkbook = spreadsheetDefaultWorkbook();
      if (!spreadsheetWorkbook.sheets || typeof spreadsheetWorkbook.sheets !== "object" || !Object.keys(spreadsheetWorkbook.sheets).length) {
        spreadsheetWorkbook.sheets = {Sheet1: {rows: 50, cols: 26, cells: {}}};
      }
      Object.entries(spreadsheetWorkbook.sheets).forEach(([name, sheet]) => {
        if (!sheet || typeof sheet !== "object") spreadsheetWorkbook.sheets[name] = {rows: 50, cols: 26, cells: {}};
        spreadsheetWorkbook.sheets[name].rows = Math.max(1, Number(spreadsheetWorkbook.sheets[name].rows) || 50);
        spreadsheetWorkbook.sheets[name].cols = Math.max(1, Number(spreadsheetWorkbook.sheets[name].cols) || 26);
        spreadsheetWorkbook.sheets[name].cells ||= {};
      });
      return spreadsheetWorkbook.sheets;
    }
    function spreadsheetSheetNames() {
      return Object.keys(spreadsheetWorkbookSheets());
    }
    function spreadsheetUniqueSheetName(rawName = "Sheet", sheets = spreadsheetWorkbookSheets()) {
      const base = spreadsheetNormalizeSheetName(rawName);
      if (!Object.prototype.hasOwnProperty.call(sheets, base)) return base;
      for (let index = 2; index < 1000; index += 1) {
        const suffix = ` ${index}`;
        const candidate = `${base.slice(0, Math.max(1, 31 - suffix.length))}${suffix}`;
        if (!Object.prototype.hasOwnProperty.call(sheets, candidate)) return candidate;
      }
      return `${base.slice(0, 25)} ${Date.now().toString().slice(-5)}`;
    }
    function spreadsheetEnsureActiveSheetName() {
      const sheets = spreadsheetWorkbookSheets();
      let sheetName = spreadsheetWorkbook.active_sheet || Object.keys(sheets)[0] || "Sheet1";
      if (!Object.prototype.hasOwnProperty.call(sheets, sheetName)) sheetName = Object.keys(sheets)[0] || "Sheet1";
      sheets[sheetName] ||= {rows: 50, cols: 26, cells: {}};
      sheets[sheetName].cells ||= {};
      spreadsheetWorkbook.active_sheet = sheetName;
      return sheetName;
    }
    function spreadsheetActiveSheetName() {
      return spreadsheetEnsureActiveSheetName();
    }
    function spreadsheetActiveSheet() {
      const sheetName = spreadsheetEnsureActiveSheetName();
      return spreadsheetWorkbook.sheets[sheetName];
    }
    function spreadsheetBlankSheet(rows = 50, cols = 26) {
      return {rows: Math.max(1, Number(rows) || 50), cols: Math.max(1, Number(cols) || 26), cells: {}};
    }
    function spreadsheetAddWorkbookSheet(rawName = "Sheet") {
      const sheets = spreadsheetWorkbookSheets();
      const name = spreadsheetUniqueSheetName(rawName, sheets);
      sheets[name] = spreadsheetBlankSheet();
      spreadsheetWorkbook.active_sheet = name;
      return name;
    }
    function spreadsheetRenameWorkbookSheet(oldName = spreadsheetActiveSheetName(), rawNewName = oldName) {
      const sheets = spreadsheetWorkbookSheets();
      if (!Object.prototype.hasOwnProperty.call(sheets, oldName)) return "";
      const cleanName = spreadsheetNormalizeSheetName(rawNewName);
      if (!cleanName || cleanName === oldName) return oldName;
      const newName = spreadsheetUniqueSheetName(cleanName, Object.fromEntries(Object.entries(sheets).filter(([name]) => name !== oldName)));
      const nextSheets = {};
      Object.entries(sheets).forEach(([name, sheet]) => {
        nextSheets[name === oldName ? newName : name] = sheet;
      });
      spreadsheetWorkbook.sheets = nextSheets;
      if (spreadsheetWorkbook.active_sheet === oldName) spreadsheetWorkbook.active_sheet = newName;
      return newName;
    }
    function spreadsheetDuplicateWorkbookSheet(sourceName = spreadsheetActiveSheetName()) {
      const sheets = spreadsheetWorkbookSheets();
      if (!Object.prototype.hasOwnProperty.call(sheets, sourceName)) return "";
      const duplicateName = spreadsheetUniqueSheetName(`${sourceName} Copy`, sheets);
      const duplicate = spreadsheetCoreClone(sheets[sourceName], spreadsheetBlankSheet());
      duplicate.cells ||= {};
      sheets[duplicateName] = duplicate;
      spreadsheetWorkbook.active_sheet = duplicateName;
      return duplicateName;
    }
    function spreadsheetDeleteWorkbookSheet(sheetName = spreadsheetActiveSheetName()) {
      const sheets = spreadsheetWorkbookSheets();
      const names = Object.keys(sheets);
      if (names.length <= 1 || !Object.prototype.hasOwnProperty.call(sheets, sheetName)) return "";
      const oldIndex = Math.max(0, names.indexOf(sheetName));
      delete sheets[sheetName];
      const remaining = Object.keys(sheets);
      spreadsheetWorkbook.active_sheet = remaining[Math.min(oldIndex, remaining.length - 1)] || remaining[0] || "Sheet1";
      return spreadsheetWorkbook.active_sheet;
    }
    function spreadsheetDefaultWorkbook(rows = 50, cols = 26) {
      return {version: 1, active_sheet: "Sheet1", sheets: {Sheet1: {rows, cols, cells: {A1: {value: "Example"}, B1: {value: "42"}}}}, metadata: {}};
    }
    async function spreadsheetApi(path, payload = {}) {
      const response = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(data.error || `spreadsheet API returned ${response.status}`);
        error.status = response.status;
        error.payload = data;
        throw error;
      }
      return data;
    }
    function spreadsheetSetDirty(dirty, message = "") {
      spreadsheetDirty = dirty;
      spreadsheetSaveState.textContent = dirty ? "dirty - disk save needed" : "clean";
      if (message) spreadsheetStatus.textContent = message;
      try {
        localStorage.setItem("main-computer-spreadsheet-grid-v1", JSON.stringify({path: spreadsheetPath, workbook: spreadsheetWorkbook}));
      } catch {}
    }
    function spreadsheetUpdatePathUi() {
      spreadsheetCurrentPath.textContent = `spreadsheets/${spreadsheetPath}`;
      spreadsheetFileList.querySelectorAll("button[data-spreadsheet-path]").forEach((button) => {
        button.classList.toggle("active", button.dataset.spreadsheetPath === spreadsheetPath);
      });
    }
    function renderSpreadsheetFiles() {
      spreadsheetFileList.textContent = "";
      if (!spreadsheetFiles.length) {
        const empty = document.createElement("div");
        empty.className = "spreadsheet-save-state";
        empty.textContent = "No disk spreadsheets yet.";
        spreadsheetFileList.append(empty);
        return;
      }
      spreadsheetFiles.forEach((file) => {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.spreadsheetPath = file.path;
        button.textContent = file.display_path || file.path;
        button.addEventListener("click", () => {
          spreadsheetSelectedPath = file.path;
          spreadsheetFileList.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
        });
        button.addEventListener("dblclick", () => openSpreadsheet(file.path));
        spreadsheetFileList.append(button);
      });
      spreadsheetUpdatePathUi();
    }
    async function loadSpreadsheetFiles() {
      const data = await spreadsheetApi("/api/applications/spreadsheet/files");
      spreadsheetFiles = data.files || [];
      renderSpreadsheetFiles();
      if (!spreadsheetWorkbook) {
        if (spreadsheetFiles.length) await openSpreadsheet(spreadsheetFiles[0].path);
        else await createSpreadsheet("untitled.json");
      }
    }
    async function openSpreadsheet(path) {
      const data = await spreadsheetApi("/api/applications/spreadsheet/read", {path});
      spreadsheetWorkbook = spreadsheetNormalizeLoadedWorkbook(data.workbook || spreadsheetDefaultWorkbook());
      spreadsheetPath = data.path || path;
      spreadsheetContentHash = data.content_hash || "";
      spreadsheetSelectedPath = spreadsheetPath;
      spreadsheetSelectionAnchor = null;
      spreadsheetSelectedRange = null;
