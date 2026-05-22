// Spreadsheet BASIC code-cell worker. User code runs in this worker through wwwBASIC.
const SPREADSHEET_WWWBASIC_SCRIPT_URL = "https://google.github.io/wwwbasic/wwwbasic.js";

let spreadsheetWwwBasicPromise = null;

function spreadsheetBasicDuration(startedAt) {
  return Math.max(0, Date.now() - startedAt);
}

function spreadsheetBasicErrorMessage(error) {
  return error && error.stack ? error.stack : error && error.message ? error.message : String(error || "BASIC worker failed.");
}

function spreadsheetLoadWwwBasic() {
  if (!spreadsheetWwwBasicPromise) {
    spreadsheetWwwBasicPromise = new Promise((resolve, reject) => {
      try {
        importScripts(SPREADSHEET_WWWBASIC_SCRIPT_URL);
      } catch (error) {
        reject(new Error(`Could not load wwwBASIC from CDN: ${spreadsheetBasicErrorMessage(error)}`));
        return;
      }
      const runtime = self.basic || self.wwwbasic || self.wwwBASIC || null;
      if (!runtime || typeof runtime.Basic !== "function") {
        reject(new Error("Could not initialize wwwBASIC: basic.Basic was not available."));
        return;
      }
      resolve(runtime);
    });
  }
  return spreadsheetWwwBasicPromise;
}

function spreadsheetBasicAppendOutput(stdout, value) {
  if (value === undefined || value === null) return stdout;
  if (typeof value === "number") return stdout + String.fromCharCode(value);
  return stdout + String(value);
}

function spreadsheetBasicLastValue(stdout) {
  const lines = String(stdout || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return null;
  const last = lines[lines.length - 1];
  const numeric = Number(last);
  return Number.isFinite(numeric) ? numeric : last;
}

function spreadsheetBasicNormalizeRef(ref) {
  return String(ref || "").trim().toUpperCase();
}

function spreadsheetBasicActiveCells(request) {
  const workbook = request.workbook_snapshot || {};
  const activeSheet = request.active_sheet || workbook.active_sheet || "Sheet1";
  return workbook.sheets?.[activeSheet]?.cells || {};
}

function spreadsheetBasicCellValue(cells, ref) {
  const cell = cells[spreadsheetBasicNormalizeRef(ref)];
  if (!cell) return "";
  if (cell && typeof cell === "object") return cell.value ?? "";
  return cell ?? "";
}

function spreadsheetBasicScalar(value) {
  if (value == null) return "";
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function spreadsheetBasicResponse(request, startedAt, overrides) {
  self.postMessage({
    id: request.id,
    ok: false,
    value: null,
    writes: [],
    output_parts: [],
    dependencies: [],
    error: null,
    duration_ms: spreadsheetBasicDuration(startedAt),
    ...(overrides || {}),
  });
}

async function spreadsheetRunBasic(runtime, source, stdoutState, request, dependencies, writes, resultState) {
  const cells = spreadsheetBasicActiveCells(request);
  const addDependency = (ref) => {
    const normalized = spreadsheetBasicNormalizeRef(ref);
    if (normalized && !dependencies.includes(normalized)) dependencies.push(normalized);
    return normalized;
  };
  const readCell = (ref) => spreadsheetBasicScalar(spreadsheetBasicCellValue(cells, addDependency(ref)));
  const writeCell = (ref, value) => {
    const target = spreadsheetBasicNormalizeRef(ref);
    writes.push({kind: "write", target, value: spreadsheetBasicScalar(value)});
    return value;
  };
  const attachCell = (ref, sourceText) => {
    const target = spreadsheetBasicNormalizeRef(ref);
    const sourceValue = String(sourceText || "");
    writes.push({kind: "attachCode", target, language: "basic", source: sourceValue, value: sourceValue});
    return sourceValue;
  };
  const bindings = {
    PutCh(value) {
      stdoutState.text = spreadsheetBasicAppendOutput(stdoutState.text, value);
    },
    putch(value) {
      stdoutState.text = spreadsheetBasicAppendOutput(stdoutState.text, value);
    },
    Print(value) {
      stdoutState.text += `${value ?? ""}\n`;
    },
    PRINT(value) {
      stdoutState.text += `${value ?? ""}\n`;
    },
    GETCELL(ref) {
      return readCell(ref);
    },
    "GETCELL$"(ref) {
      return String(readCell(ref));
    },
    GETNUMBER(ref) {
      const value = Number(readCell(ref));
      return Number.isFinite(value) ? value : 0;
    },
    SETCELL(ref, value) {
      return writeCell(ref, value);
    },
    WRITE(ref, value) {
      return writeCell(ref, value);
    },
    WRITERANGE(ref, value) {
      const target = spreadsheetBasicNormalizeRef(ref);
      writes.push({kind: "writeRange", target, value});
      return value;
    },
    SETRESULT(value) {
      resultState.value = spreadsheetBasicScalar(value);
      return resultState.value;
    },
    CELL_EVAL(sourceText) {
      // wwwBASIC binding argument/output conventions vary. This narrow binding
      // records the generated cell source for inspection and returns the text so
      // BASIC code can PRINT/verify it without depending on runtime introspection.
      resultState.eval_source = String(sourceText || "");
      return resultState.eval_source;
    },
    SPREADSHEET_ATTACH(ref, sourceText) {
      return attachCell(ref, sourceText);
    },
  };
  const options = {
    bindings,
    output(value) {
      stdoutState.text = spreadsheetBasicAppendOutput(stdoutState.text, value);
    },
  };
  const program = runtime.Basic(String(source || ""), options);
  if (program && typeof program.run === "function") {
    const result = program.run();
    if (result && typeof result.then === "function") await result;
    return program;
  }
  if (program && typeof program.Run === "function") {
    const result = program.Run();
    if (result && typeof result.then === "function") await result;
    return program;
  }
  if (typeof program === "function") {
    const result = program();
    if (result && typeof result.then === "function") await result;
    return {result};
  }
  return program;
}

self.onmessage = async (event) => {
  const request = event.data || {};
  const startedAt = Date.now();
  const outputParts = [];
  const stdoutState = {text: ""};
  const dependencies = [];
  const writes = [];
  const resultState = {value: null, eval_source: ""};

  try {
    const runtime = await spreadsheetLoadWwwBasic();
    const program = await spreadsheetRunBasic(runtime, request.source, stdoutState, request, dependencies, writes, resultState);
    const value = resultState.value !== null && resultState.value !== undefined
      ? resultState.value
      : program && Object.prototype.hasOwnProperty.call(program, "RESULT")
        ? program.RESULT
        : spreadsheetBasicLastValue(stdoutState.text);

    if (stdoutState.text.trim()) outputParts.push({kind: "stdout", title: "stdout", content: stdoutState.text.trimEnd(), metadata: {}});
    if (resultState.eval_source) outputParts.push({kind: "text", title: "CELL_EVAL source", content: resultState.eval_source, metadata: {language: "basic"}});
    if (value !== null && value !== undefined) outputParts.push({kind: "text", title: "Result", content: value, metadata: {}});
    writes.forEach((write) => outputParts.push({kind: "write_preview", title: write.kind === "attachCode" ? "Attach preview" : "Write preview", content: write, metadata: write}));
    if (!outputParts.length) outputParts.push({kind: "text", title: "BASIC", content: "BASIC cell completed.", metadata: {}});

    spreadsheetBasicResponse(request, startedAt, {
      ok: true,
      value,
      writes,
      dependencies,
      output_parts: outputParts,
      error: null,
    });
  } catch (error) {
    if (stdoutState.text.trim()) outputParts.push({kind: "stdout", title: "stdout", content: stdoutState.text.trimEnd(), metadata: {}});
    const message = spreadsheetBasicErrorMessage(error);
    const title = message.includes("wwwBASIC") || message.includes("basic.Basic") ? "BASIC runtime load failed" : "BASIC error";
    outputParts.push({kind: "error", title, content: message, metadata: {}});
    spreadsheetBasicResponse(request, startedAt, {
      ok: false,
      value: null,
      writes,
      dependencies,
      output_parts: outputParts,
      error: message,
    });
  }
};
