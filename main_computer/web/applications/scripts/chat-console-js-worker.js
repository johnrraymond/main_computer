/* Chat Console JavaScript code-cell worker. Runs inside a dedicated Worker. */
function chatCodeClone(value) {
  try {
    return JSON.parse(JSON.stringify(value ?? null));
  } catch {
    return null;
  }
}

function chatCodeNormalizeVariables(value) {
  const source = chatCodeClone(value);
  if (!source || typeof source !== "object" || Array.isArray(source)) return {};
  const normalized = {};
  Object.entries(source).forEach(([key, item]) => {
    const name = String(key || "").trim();
    if (!name || name.length > 80 || name === "__proto__" || name === "constructor" || name === "prototype") return;
    try {
      const encoded = JSON.stringify(item);
      if (encoded && encoded.length <= 12000) normalized[name] = item;
    } catch {
      // skip non-serializable values
    }
  });
  return normalized;
}

function chatCodeAppendOutput(stdout, item) {
  try {
    if (typeof item === "string") {
      stdout.push(item);
    } else {
      stdout.push(JSON.stringify(item));
    }
  } catch {
    stdout.push(String(item));
  }
}

function chatCodeOutputPartForValue(value) {
  if (value === undefined) return null;
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
    return {kind: "text", title: "Result", content: String(value ?? ""), metadata: {}};
  }
  return {kind: "json", title: "Result", content: chatCodeClone(value), metadata: {}};
}

function chatCodeErrorMessage(error) {
  return error && error.stack ? error.stack : error && error.message ? error.message : String(error || "JavaScript worker failed.");
}

function chatCodeSafeVariableValue(value) {
  const cloned = chatCodeClone(value);
  if (cloned === null || ["string", "number", "boolean"].includes(typeof cloned) || Array.isArray(cloned) || (typeof cloned === "object" && cloned)) {
    return cloned;
  }
  return String(value);
}

function chatCodeVariableProxy(initialVariables) {
  const variables = chatCodeNormalizeVariables(initialVariables);
  const passthrough = new Set([
    "console", "Math", "JSON", "Number", "String", "Boolean", "Array", "Object", "Date", "RegExp",
    "parseInt", "parseFloat", "isNaN", "isFinite", "vars", "shared", "context", "undefined", "NaN", "Infinity",
  ]);
  const proxy = new Proxy(variables, {
    has(_target, key) {
      if (typeof key === "symbol") return false;
      return !passthrough.has(String(key));
    },
    get(target, key) {
      if (key === Symbol.unscopables) return undefined;
      if (typeof key === "symbol") return undefined;
      return target[String(key)];
    },
    set(target, key, value) {
      if (typeof key === "symbol") return true;
      const name = String(key || "").trim();
      if (!name || name === "__proto__" || name === "constructor" || name === "prototype") return true;
      target[name] = chatCodeSafeVariableValue(value);
      return true;
    },
  });
  return {variables, proxy};
}

function chatCodeCompile(source) {
  const text = String(source || "").trim();
  const hasExplicitReturn = /\breturn\b/.test(text);
  const args = ["vars", "shared", "context", "console", "window", "document", "localStorage", "fetch", "XMLHttpRequest", "importScripts"];
  if (hasExplicitReturn) {
    return new Function(...args, `with (vars) {\n${text}\n}`);
  }
  try {
    return new Function(...args, `with (vars) {\nreturn (${text});\n}`);
  } catch {
    return new Function(...args, `with (vars) {\n${text}\n}`);
  }
}

self.onmessage = (event) => {
  const startedAt = Date.now();
  const request = event.data || {};
  const stdout = [];
  const outputParts = [];
  const {variables, proxy} = chatCodeVariableProxy(request.shared_variables || {});
  const scopedConsole = Object.freeze({
    log(...items) {
      stdout.push(items.map((item) => {
        try {
          return typeof item === "string" ? item : JSON.stringify(item);
        } catch {
          return String(item);
        }
      }).join(" "));
    },
    warn(...items) {
      chatCodeAppendOutput(stdout, items.join(" "));
    },
    error(...items) {
      chatCodeAppendOutput(stdout, items.join(" "));
    },
  });
  const context = Object.freeze({
    get(name, fallback = undefined) {
      const key = String(name || "");
      return Object.prototype.hasOwnProperty.call(variables, key) ? variables[key] : fallback;
    },
    set(name, value) {
      const key = String(name || "").trim();
      if (key) variables[key] = chatCodeSafeVariableValue(value);
      return variables[key];
    },
    delete(name) {
      delete variables[String(name || "")];
    },
    keys() {
      return Object.keys(variables);
    },
    all() {
      return chatCodeClone(variables);
    },
  });
  try {
    const run = chatCodeCompile(request.source || "");
    const value = run(proxy, proxy, context, scopedConsole, undefined, undefined, undefined, undefined, undefined, undefined);
    if (stdout.length) outputParts.push({kind: "stdout", title: "stdout", content: stdout.join("\n"), metadata: {}});
    const resultPart = chatCodeOutputPartForValue(value);
    if (resultPart) outputParts.push(resultPart);
    if (!outputParts.length) outputParts.push({kind: "text", title: "JavaScript", content: "JavaScript cell completed.", metadata: {}});
    self.postMessage({
      id: request.id,
      ok: true,
      value,
      variables: chatCodeNormalizeVariables(variables),
      output_parts: outputParts,
      error: null,
      duration_ms: Math.max(0, Date.now() - startedAt),
    });
  } catch (error) {
    if (stdout.length) outputParts.push({kind: "stdout", title: "stdout", content: stdout.join("\n"), metadata: {}});
    outputParts.push({kind: "error", title: "JavaScript error", content: chatCodeErrorMessage(error), metadata: {}});
    self.postMessage({
      id: request.id,
      ok: false,
      value: null,
      variables: chatCodeNormalizeVariables(variables),
      output_parts: outputParts,
      error: chatCodeErrorMessage(error),
      duration_ms: Math.max(0, Date.now() - startedAt),
    });
  }
};
