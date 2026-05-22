/* Spreadsheet JavaScript code-cell worker. Runs inside a dedicated Worker. */
self.onmessage = (event) => {
  const startedAt = Date.now();
  const request = event.data || {};
  const dependencies = [];
  const writes = [];
  const stdout = [];
  const cells = (((request.workbook_snapshot || {}).sheets || {})[request.active_sheet] || {}).cells || {};

  const normalizeRef = (ref) => String(ref || "").trim().toUpperCase();
  const columnIndex = (name) => String(name || "A").toUpperCase().split("").reduce((total, char) => total * 26 + char.charCodeAt(0) - 64, 0);
  const columnName = (index) => {
    let name = "";
    let value = Number(index) || 1;
    while (value > 0) {
      const remainder = (value - 1) % 26;
      name = String.fromCharCode(65 + remainder) + name;
      value = Math.floor((value - 1) / 26);
    }
    return name || "A";
  };
  const cellParts = (ref) => {
    const match = normalizeRef(ref).match(/^([A-Z]+)([1-9][0-9]*)$/);
    if (!match) throw new Error(`Invalid cell reference: ${ref}`);
    return {col: columnIndex(match[1]), row: Number(match[2])};
  };
  const cellRef = (row, col) => `${columnName(col)}${row}`;
  const rangeRefs = (range) => {
    const [startRef, endRef = startRef] = String(range || "").split(":");
    const start = cellParts(startRef);
    const end = cellParts(endRef);
    const refs = [];
    for (let row = Math.min(start.row, end.row); row <= Math.max(start.row, end.row); row += 1) {
      const values = [];
      for (let col = Math.min(start.col, end.col); col <= Math.max(start.col, end.col); col += 1) {
        values.push(cellRef(row, col));
      }
      refs.push(values);
    }
    return refs;
  };
  const readValue = (ref) => {
    const normalized = normalizeRef(ref);
    if (!dependencies.includes(normalized)) dependencies.push(normalized);
    return cells[normalized]?.value ?? "";
  };
  const sheetApi = {
    get(ref) {
      return readValue(ref);
    },
    getNumber(ref) {
      const value = Number(readValue(ref));
      return Number.isFinite(value) ? value : 0;
    },
    range(range) {
      const rows = rangeRefs(range);
      rows.flat().forEach((ref) => {
        if (!dependencies.includes(ref)) dependencies.push(ref);
      });
      return rows.map((row) => row.map((ref) => cells[ref]?.value ?? ""));
    },
    write(ref, value) {
      const target = normalizeRef(ref);
      writes.push({kind: "write", target, value});
      return {target, value};
    },
    writeRange(range, values) {
      const target = String(range || "").trim().toUpperCase();
      writes.push({kind: "writeRange", target, value: values});
      return {target, value: values};
    },
    attach(target, sourceOrValue, fn) {
      let value;
      if (typeof fn === "function") {
        value = fn(typeof sourceOrValue === "string" ? sheetApi.range(sourceOrValue) : sourceOrValue);
      } else if (typeof sourceOrValue === "function") {
        value = sourceOrValue();
      } else {
        value = sourceOrValue;
      }
      return sheetApi.write(target, value);
    },
    setResult(value) {
      return value;
    },
  };
  const sheet = Object.freeze(sheetApi);
  const spreadsheet = sheet;
  const scopedConsole = Object.freeze({
    log(...items) {
      stdout.push(items.map((item) => typeof item === "string" ? item : JSON.stringify(item)).join(" "));
    },
  });
  const source = String(request.source || "").trim();
  const hasExplicitReturn = /\breturn\b/.test(source);
  const compileUserSource = () => {
    const args = ["sheet", "spreadsheet", "console", "window", "document", "localStorage", "fetch", "XMLHttpRequest", "importScripts"];
    if (hasExplicitReturn) {
      return new Function(...args, `"use strict";\n${source}`);
    }
    try {
      return new Function(...args, `"use strict";\nreturn (${source});`);
    } catch {
      return new Function(...args, `"use strict";\n${source}`);
    }
  };
  try {
    const run = compileUserSource();
    const value = run(sheet, spreadsheet, scopedConsole, undefined, undefined, undefined, undefined, undefined, undefined);
    const outputParts = [];
    if (stdout.length) outputParts.push({kind: "stdout", title: "stdout", content: stdout.join("\n"), metadata: {}});
    if (value !== undefined) {
      const scalar = value == null || ["string", "number", "boolean"].includes(typeof value);
      outputParts.push({
        kind: scalar ? "text" : "json",
        title: "Result",
        content: scalar ? String(value ?? "") : value,
        metadata: {},
      });
    }
    writes.forEach((write) => outputParts.push({kind: "write_preview", title: "Write preview", content: write, metadata: {}}));
    self.postMessage({
      id: request.id,
      ok: true,
      value: value === undefined ? null : value,
      writes,
      output_parts: outputParts,
      dependencies,
      error: null,
      duration_ms: Date.now() - startedAt,
    });
  } catch (error) {
    self.postMessage({
      id: request.id,
      ok: false,
      value: null,
      writes,
      output_parts: [{kind: "error", title: "JavaScript error", content: error.message || String(error), metadata: {}}],
      dependencies,
      error: error.message || String(error),
      duration_ms: Date.now() - startedAt,
    });
  }
};
