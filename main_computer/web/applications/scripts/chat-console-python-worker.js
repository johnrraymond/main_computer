// Chat Console Python code-cell worker. User code runs in this worker through Pyodide.
const CHAT_CONSOLE_PYODIDE_SCRIPT_URL = "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/pyodide.js";
const CHAT_CONSOLE_PYODIDE_INDEX_URL = "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/";

let chatConsolePyodidePromise = null;

function chatPyDuration(startedAt) {
  return Math.max(0, Date.now() - startedAt);
}

function chatPyClone(value) {
  try {
    return JSON.parse(JSON.stringify(value ?? null));
  } catch {
    return null;
  }
}

function chatPyNormalizeVariables(value) {
  const source = chatPyClone(value);
  if (!source || typeof source !== "object" || Array.isArray(source)) return {};
  const normalized = {};
  Object.entries(source).forEach(([key, item]) => {
    const name = String(key || "").trim();
    if (!name || name.length > 80 || name === "__proto__" || name === "constructor" || name === "prototype") return;
    try {
      const encoded = JSON.stringify(item);
      if (encoded && encoded.length <= 12000) normalized[name] = item;
    } catch {
      // skip non-serializable variables
    }
  });
  return normalized;
}

function chatPyToPlainJs(value) {
  if (value == null) return null;
  if (typeof value === "number" || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value.toJs === "function") {
    const jsValue = value.toJs({dict_converter: Object.fromEntries});
    if (typeof value.destroy === "function") value.destroy();
    return jsValue;
  }
  return value;
}

function chatPyOutputPartForResult(value) {
  const plain = chatPyToPlainJs(value);
  if (plain == null) return null;
  if (Array.isArray(plain) || (typeof plain === "object" && plain)) {
    return {kind: "json", title: "Result", content: plain, metadata: {}};
  }
  return {kind: "text", title: "Result", content: String(plain), metadata: {}};
}

function chatPyErrorMessage(error) {
  return error && error.stack ? error.stack : error && error.message ? error.message : String(error || "Python worker failed.");
}

async function chatPyLoadPyodide() {
  if (!chatConsolePyodidePromise) {
    chatConsolePyodidePromise = (async () => {
      try {
        importScripts(CHAT_CONSOLE_PYODIDE_SCRIPT_URL);
      } catch (error) {
        throw new Error(`Could not load Pyodide from CDN: ${chatPyErrorMessage(error)}`);
      }
      if (typeof loadPyodide !== "function") {
        throw new Error("Pyodide script loaded but loadPyodide was not available.");
      }
      return loadPyodide({indexURL: CHAT_CONSOLE_PYODIDE_INDEX_URL});
    })();
  }
  return chatConsolePyodidePromise;
}

async function chatPyInstallSharedVariables(pyodide, variables) {
  pyodide.globals.set("chat_variables_json", JSON.stringify(chatPyNormalizeVariables(variables)));
  await pyodide.runPythonAsync(`
import builtins
import json
import keyword
vars = json.loads(chat_variables_json)
shared = vars
_mc_builtin_names = set(dir(builtins))
_mc_reserved_names = {
    "builtins", "json", "keyword", "vars", "shared",
    "get_var", "set_var", "del_var", "chat_variables_json",
    "_mc_builtin_names", "_mc_reserved_names", "_mc_is_shared_name", "_mc_install_shared_globals",
}
def _mc_is_shared_name(name):
    return (
        isinstance(name, str)
        and name.isidentifier()
        and not keyword.iskeyword(name)
        and not name.startswith("_")
        and name not in _mc_reserved_names
        and name not in _mc_builtin_names
    )
def _mc_install_shared_globals():
    for _mc_name, _mc_value in list(vars.items()):
        if _mc_is_shared_name(_mc_name):
            globals()[_mc_name] = _mc_value
def get_var(name, default=None):
    return vars.get(str(name), default)
def set_var(name, value):
    _mc_name = str(name)
    vars[_mc_name] = value
    if _mc_is_shared_name(_mc_name):
        globals()[_mc_name] = value
    return value
def del_var(name):
    _mc_name = str(name)
    _mc_value = vars.pop(_mc_name, None)
    if _mc_is_shared_name(_mc_name):
        globals().pop(_mc_name, None)
    return _mc_value
_mc_install_shared_globals()
`);
}

async function chatPyExportSharedVariables(pyodide) {
  const exportedJson = await pyodide.runPythonAsync(`
import json
_mc_reserved = {
    "builtins", "json", "keyword", "vars", "shared", "get_var", "set_var", "del_var",
    "chat_variables_json", "_mc_builtin_names", "_mc_reserved_names", "_mc_reserved",
    "_mc_is_shared_name", "_mc_install_shared_globals", "_mc_export",
}
_mc_export = dict(vars)
for _mc_name, _mc_value in list(globals().items()):
    if _mc_name.startswith("_") or _mc_name in _mc_reserved:
        continue
    if callable(_mc_value):
        continue
    try:
        json.dumps(_mc_value)
    except Exception:
        continue
    _mc_export[_mc_name] = _mc_value
json.dumps(_mc_export)
`);
  const plain = chatPyToPlainJs(exportedJson);
  try {
    return chatPyNormalizeVariables(JSON.parse(String(plain || "{}")));
  } catch {
    return {};
  }
}

self.onmessage = async (event) => {
  const request = event.data || {};
  const startedAt = Date.now();
  const stdout = [];
  const stderr = [];
  const outputParts = [];
  let variables = chatPyNormalizeVariables(request.shared_variables || {});
  let pyodide = null;
  try {
    pyodide = await chatPyLoadPyodide();
    pyodide.setStdout({batched: (text) => stdout.push(text)});
    pyodide.setStderr({batched: (text) => stderr.push(text)});
    await chatPyInstallSharedVariables(pyodide, variables);
    const result = await pyodide.runPythonAsync(String(request.source || ""));
    variables = await chatPyExportSharedVariables(pyodide);

    if (stdout.length) outputParts.push({kind: "stdout", title: "stdout", content: stdout.join("\n"), metadata: {}});
    if (stderr.length) outputParts.push({kind: "stderr", title: "stderr", content: stderr.join("\n"), metadata: {}});
    const resultPart = chatPyOutputPartForResult(result);
    if (resultPart) outputParts.push(resultPart);
    if (!outputParts.length) outputParts.push({kind: "text", title: "Python", content: "Python cell completed.", metadata: {}});

    self.postMessage({
      id: request.id,
      ok: true,
      value: resultPart ? resultPart.content : null,
      variables,
      output_parts: outputParts,
      error: null,
      duration_ms: chatPyDuration(startedAt),
    });
  } catch (error) {
    try {
      if (typeof pyodide !== "undefined") variables = await chatPyExportSharedVariables(pyodide);
    } catch {
      // keep last known variables
    }
    if (stdout.length) outputParts.push({kind: "stdout", title: "stdout", content: stdout.join("\n"), metadata: {}});
    if (stderr.length) outputParts.push({kind: "stderr", title: "stderr", content: stderr.join("\n"), metadata: {}});
    outputParts.push({kind: "error", title: "Python error", content: chatPyErrorMessage(error), metadata: {}});
    self.postMessage({
      id: request.id,
      ok: false,
      value: null,
      variables,
      output_parts: outputParts,
      error: chatPyErrorMessage(error),
      duration_ms: chatPyDuration(startedAt),
    });
  }
};
