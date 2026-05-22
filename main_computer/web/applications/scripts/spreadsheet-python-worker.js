// Spreadsheet Python code-cell worker. User code runs in this worker through Pyodide.
const SPREADSHEET_PYODIDE_SCRIPT_URL = "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/pyodide.js";
const SPREADSHEET_PYODIDE_INDEX_URL = "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/";

let spreadsheetPyodidePromise = null;

function spreadsheetWorkerDuration(startedAt) {
  return Math.max(0, Date.now() - startedAt);
}

function spreadsheetNormalizeRef(ref) {
  return String(ref || "").trim().toUpperCase();
}

function spreadsheetColumnIndex(label) {
  return String(label || "").split("").reduce((total, char) => total * 26 + char.charCodeAt(0) - 64, 0) - 1;
}

function spreadsheetColumnLabel(index) {
  let value = Number(index) + 1;
  let label = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    label = String.fromCharCode(65 + remainder) + label;
    value = Math.floor((value - 1) / 26);
  }
  return label;
}

function spreadsheetParseRef(ref) {
  const match = spreadsheetNormalizeRef(ref).match(/^([A-Z]{1,3})([1-9][0-9]*)$/);
  if (!match) return null;
  return {col: spreadsheetColumnIndex(match[1]), row: Number(match[2]) - 1};
}

function spreadsheetRefsForRange(label) {
  const [startLabel, endLabel = startLabel] = String(label || "").split(":");
  const start = spreadsheetParseRef(startLabel);
  const end = spreadsheetParseRef(endLabel);
  if (!start || !end) return [];
  const refs = [];
  const rowStart = Math.min(start.row, end.row);
  const rowEnd = Math.max(start.row, end.row);
  const colStart = Math.min(start.col, end.col);
  const colEnd = Math.max(start.col, end.col);
  for (let row = rowStart; row <= rowEnd; row += 1) {
    const rowValues = [];
    for (let col = colStart; col <= colEnd; col += 1) {
      rowValues.push(`${spreadsheetColumnLabel(col)}${row + 1}`);
    }
    refs.push(rowValues);
  }
  return refs;
}

function spreadsheetActiveCells(request) {
  const workbook = request.workbook_snapshot || {};
  const activeSheet = request.active_sheet || workbook.active_sheet || "Sheet1";
  return workbook.sheets?.[activeSheet]?.cells || {};
}

function spreadsheetCellValue(cells, ref) {
  const cell = cells[spreadsheetNormalizeRef(ref)];
  if (!cell) return "";
  if (cell && typeof cell === "object") return cell.value ?? "";
  return cell ?? "";
}

function spreadsheetScalarForCell(value) {
  if (value == null) return "";
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function spreadsheetToPlainJs(value) {
  if (value == null) return null;
  if (typeof value === "number" || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value.toJs === "function") {
    const jsValue = value.toJs({dict_converter: Object.fromEntries});
    if (typeof value.destroy === "function") value.destroy();
    return jsValue;
  }
  return value;
}

function spreadsheetCompactValue(value) {
  const plain = spreadsheetToPlainJs(value);
  if (plain == null) return null;
  if (typeof plain === "number" || typeof plain === "string" || typeof plain === "boolean") return plain;
  try {
    return JSON.stringify(plain);
  } catch {
    return String(plain);
  }
}

function spreadsheetOutputPartForResult(value) {
  const plain = spreadsheetToPlainJs(value);
  if (plain == null) return null;
  if (Array.isArray(plain) || (typeof plain === "object" && plain)) {
    return {kind: "json", title: "Result", content: plain, metadata: {}};
  }
  return {kind: "text", title: "Result", content: plain, metadata: {}};
}

function spreadsheetErrorMessage(error) {
  return error && error.stack ? error.stack : error && error.message ? error.message : String(error || "Python worker failed.");
}

async function spreadsheetLoadPyodide() {
  if (!spreadsheetPyodidePromise) {
    spreadsheetPyodidePromise = (async () => {
      try {
        importScripts(SPREADSHEET_PYODIDE_SCRIPT_URL);
      } catch (error) {
        throw new Error(`Could not load Pyodide from CDN: ${spreadsheetErrorMessage(error)}`);
      }
      if (typeof loadPyodide !== "function") {
        throw new Error("Could not load Pyodide from CDN: loadPyodide() was not available.");
      }
      try {
        return await loadPyodide({indexURL: SPREADSHEET_PYODIDE_INDEX_URL});
      } catch (error) {
        throw new Error(`Could not initialize Pyodide: ${spreadsheetErrorMessage(error)}`);
      }
    })();
  }
  return spreadsheetPyodidePromise;
}

function spreadsheetPostPythonError(request, startedAt, title, error, outputParts = []) {
  const message = spreadsheetErrorMessage(error);
  self.postMessage({
    id: request.id,
    ok: false,
    value: null,
    writes: [],
    output_parts: [...outputParts, {kind: "error", title, content: message, metadata: {}}],
    dependencies: [],
    error: message,
    duration_ms: spreadsheetWorkerDuration(startedAt),
  });
}

self.onmessage = async (event) => {
  const request = event.data || {};
  const startedAt = Date.now();
  const outputParts = [];
  const dependencies = [];
  const writes = [];
  let stdoutText = "";
  let stderrText = "";

  try {
    const pyodide = await spreadsheetLoadPyodide();
    const cells = spreadsheetActiveCells(request);

    const addDependency = (ref) => {
      const normalized = spreadsheetNormalizeRef(ref);
      if (normalized && !dependencies.includes(normalized)) dependencies.push(normalized);
    };

    self.spreadsheet_get = (ref) => {
      const normalized = spreadsheetNormalizeRef(ref);
      addDependency(normalized);
      return spreadsheetScalarForCell(spreadsheetCellValue(cells, normalized));
    };
    self.spreadsheet_get_number = (ref) => {
      const value = Number(self.spreadsheet_get(ref));
      return Number.isFinite(value) ? value : 0;
    };
    self.spreadsheet_range = (label) => {
      return spreadsheetRefsForRange(label).map((row) => row.map((ref) => {
        addDependency(ref);
        return spreadsheetScalarForCell(spreadsheetCellValue(cells, ref));
      }));
    };
    self.spreadsheet_write = (ref, value) => {
      const normalized = spreadsheetNormalizeRef(ref);
      const nextValue = spreadsheetToPlainJs(value);
      writes.push({kind: "write", target: normalized, value: nextValue});
      return nextValue;
    };
    self.spreadsheet_write_range = (label, values) => {
      const target = String(label || "").trim().toUpperCase();
      const nextValues = spreadsheetToPlainJs(values);
      writes.push({kind: "writeRange", target, value: nextValues});
      return nextValues;
    };

    pyodide.setStdout({batched: (text) => { stdoutText += `${text}\n`; }});
    pyodide.setStderr({batched: (text) => { stderrText += `${text}\n`; }});

    await pyodide.runPythonAsync(`
from js import spreadsheet_get, spreadsheet_get_number, spreadsheet_range, spreadsheet_write, spreadsheet_write_range

class Sheet:
    def get(self, ref):
        return spreadsheet_get(ref)

    def get_number(self, ref):
        return spreadsheet_get_number(ref)

    def range(self, label):
        return spreadsheet_range(label).to_py()

    def write(self, ref, value):
        return spreadsheet_write(ref, value)

    def write_range(self, label, values):
        return spreadsheet_write_range(label, values)

    def attach(self, target, source_or_value=None, fn=None):
        if callable(fn):
            value = fn(self.range(source_or_value) if isinstance(source_or_value, str) else source_or_value)
        elif callable(source_or_value):
            value = source_or_value()
        else:
            value = source_or_value
        return self.write(target, value)

    def set_result(self, value):
        return value

sheet = Sheet()
spreadsheet = sheet
`);

    let result = await pyodide.runPythonAsync(String(request.source || ""));
    result = spreadsheetToPlainJs(result);
    const value = spreadsheetCompactValue(result);
    const resultPart = spreadsheetOutputPartForResult(result);

    if (stdoutText.trim()) outputParts.push({kind: "stdout", title: "stdout", content: stdoutText.trimEnd(), metadata: {}});
    if (stderrText.trim()) outputParts.push({kind: "stderr", title: "stderr", content: stderrText.trimEnd(), metadata: {}});
    if (resultPart) outputParts.push(resultPart);
    writes.forEach((write) => {
      outputParts.push({kind: "write_preview", title: "Write preview", content: `${write.target}: ${spreadsheetCompactValue(write.value)}`, metadata: write});
    });
    if (!outputParts.length) outputParts.push({kind: "text", title: "Python", content: "Python cell completed.", metadata: {}});

    self.postMessage({
      id: request.id,
      ok: true,
      value,
      writes,
      output_parts: outputParts,
      dependencies,
      error: null,
      duration_ms: spreadsheetWorkerDuration(startedAt),
    });
  } catch (error) {
    if (stdoutText.trim()) outputParts.push({kind: "stdout", title: "stdout", content: stdoutText.trimEnd(), metadata: {}});
    if (stderrText.trim()) outputParts.push({kind: "stderr", title: "stderr", content: stderrText.trimEnd(), metadata: {}});
    const title = String(error && error.message || "").includes("Pyodide") ? "Python runtime load failed" : "Python error";
    spreadsheetPostPythonError(request, startedAt, title, error, outputParts);
  }
};
