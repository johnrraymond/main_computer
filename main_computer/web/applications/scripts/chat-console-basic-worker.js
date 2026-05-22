// Chat Console BASIC code-cell worker. User code runs in this worker through wwwBASIC when available,
// with a local dependency-free BASIC subset fallback for offline chat-console execution.
const CHAT_CONSOLE_WWWBASIC_SCRIPT_URL = "https://google.github.io/wwwbasic/wwwbasic.js";

let chatConsoleWwwBasicPromise = null;

function chatBasicDuration(startedAt) {
  return Math.max(0, Date.now() - startedAt);
}

function chatBasicClone(value) {
  try {
    return JSON.parse(JSON.stringify(value ?? null));
  } catch {
    return null;
  }
}

function chatBasicNormalizeVariables(value) {
  const source = chatBasicClone(value);
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

function chatBasicErrorMessage(error) {
  return error && error.stack ? error.stack : error && error.message ? error.message : String(error || "BASIC worker failed.");
}

function chatBasicFallbackRuntime(reason = "") {
  return {
    fallback: true,
    reason,
    Basic(source, options) {
      return new ChatConsoleBasicFallbackProgram(source, options || {});
    },
  };
}

function chatLoadWwwBasic() {
  if (!chatConsoleWwwBasicPromise) {
    chatConsoleWwwBasicPromise = new Promise((resolve) => {
      try {
        importScripts(CHAT_CONSOLE_WWWBASIC_SCRIPT_URL);
      } catch (error) {
        resolve(chatBasicFallbackRuntime(`Could not load wwwBASIC from CDN: ${chatBasicErrorMessage(error)}`));
        return;
      }
      const runtime = self.basic || self.wwwbasic || self.wwwBASIC || null;
      if (!runtime || typeof runtime.Basic !== "function") {
        resolve(chatBasicFallbackRuntime("Could not initialize wwwBASIC: basic.Basic was not available."));
        return;
      }
      resolve(runtime);
    });
  }
  return chatConsoleWwwBasicPromise;
}

function chatBasicAppendOutput(stdout, value) {
  if (value === undefined || value === null) return stdout;
  if (typeof value === "number") return stdout + String.fromCharCode(value);
  return stdout + String(value);
}

function chatBasicLastValue(stdout) {
  const lines = String(stdout || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return null;
  const last = lines[lines.length - 1];
  const numeric = Number(last);
  return Number.isFinite(numeric) ? numeric : last;
}

function chatBasicSafeString(value) {
  return String(value ?? "").replace(/"/g, '""').slice(0, 1000);
}

function chatBasicVariablePrelude(variables) {
  const normalized = chatBasicNormalizeVariables(variables);
  const lines = [];
  Object.entries(normalized).forEach(([name, value]) => {
    if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(name)) return;
    if (typeof value === "number" && Number.isFinite(value)) {
      lines.push(`LET ${name.toUpperCase()} = ${value}`);
    } else if (typeof value === "boolean") {
      lines.push(`LET ${name.toUpperCase()} = ${value ? 1 : 0}`);
    } else if (typeof value === "string") {
      lines.push(`LET ${name.toUpperCase()}$ = "${chatBasicSafeString(value)}"`);
    }
  });
  return lines.length ? `${lines.join("\n")}\n` : "";
}

function chatBasicExtractProgramVariables(program, variables) {
  const exported = chatBasicNormalizeVariables(variables);
  const candidates = [
    program && program.variables,
    program && program.vars,
    program && program.symbols,
    program && program.environment,
    program && program.env,
  ];
  candidates.forEach((candidate) => {
    if (!candidate || typeof candidate !== "object") return;
    Object.entries(candidate).forEach(([key, value]) => {
      const name = String(key || "").replace(/\$$/, "").trim();
      if (!name || name.length > 80 || name.startsWith("_")) return;
      if (typeof value === "number" || typeof value === "string" || typeof value === "boolean") {
        exported[name] = value;
      }
    });
  });
  return chatBasicNormalizeVariables(exported);
}

function chatBasicSplitOutsideStrings(text, separators) {
  const parts = [];
  let current = "";
  let quote = false;
  const separatorSet = new Set(separators);
  for (let index = 0; index < String(text || "").length; index += 1) {
    const char = text[index];
    if (char === '"') {
      if (quote && text[index + 1] === '"') {
        current += '""';
        index += 1;
        continue;
      }
      quote = !quote;
      current += char;
      continue;
    }
    if (!quote && separatorSet.has(char)) {
      parts.push(current.trim());
      current = "";
      continue;
    }
    current += char;
  }
  if (current.trim() || parts.length) parts.push(current.trim());
  return parts;
}

function chatBasicStripLineNumber(line) {
  return String(line || "").replace(/^\s*\d+\s+/, "").trim();
}

function chatBasicStripComment(line) {
  const text = String(line || "");
  let quote = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === '"') {
      if (quote && text[index + 1] === '"') {
        index += 1;
        continue;
      }
      quote = !quote;
      continue;
    }
    if (!quote && char === "'") return text.slice(0, index).trim();
    if (!quote && /^REM(\s|$)/i.test(text.slice(index))) return text.slice(0, index).trim();
  }
  return text.trim();
}

function chatBasicToNumber(value) {
  if (typeof value === "number") return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  const numeric = Number(String(value ?? "").trim());
  return Number.isFinite(numeric) ? numeric : 0;
}

function chatBasicTruthy(value) {
  if (typeof value === "string") return value.length > 0;
  return chatBasicToNumber(value) !== 0;
}

function chatBasicNormalizeIdentifier(name) {
  return String(name || "").trim().replace(/\$$/, "").toUpperCase();
}

function chatBasicLookupVariable(variables, rawName, fallback = 0) {
  const source = variables && typeof variables === "object" ? variables : {};
  const exact = String(rawName || "").trim();
  const normalized = chatBasicNormalizeIdentifier(exact);
  const candidates = [
    exact,
    normalized,
    normalized.toLowerCase(),
    exact.replace(/\$$/, ""),
    normalized ? `${normalized}$` : "",
    normalized ? `${normalized.toLowerCase()}$` : "",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (Object.prototype.hasOwnProperty.call(source, candidate)) return source[candidate];
  }
  return fallback;
}

function chatBasicVariableNameFromTarget(target) {
  const name = String(target || "").trim();
  if (!/^[A-Za-z][A-Za-z0-9_]*\$?$/.test(name)) {
    throw new Error(`Invalid BASIC variable name: ${target}`);
  }
  return chatBasicNormalizeIdentifier(name);
}

class ChatConsoleBasicExpressionParser {
  constructor(text, variables) {
    this.text = String(text || "");
    this.index = 0;
    this.variables = variables || {};
  }

  parse() {
    const value = this.parseComparison();
    this.skipWhitespace();
    if (this.index < this.text.length) {
      throw new Error(`Unexpected BASIC expression text: ${this.text.slice(this.index)}`);
    }
    return value;
  }

  peek() {
    return this.text[this.index] || "";
  }

  skipWhitespace() {
    while (/\s/.test(this.peek())) this.index += 1;
  }

  matchOperator(operator) {
    this.skipWhitespace();
    if (this.text.slice(this.index, this.index + operator.length).toUpperCase() === operator.toUpperCase()) {
      this.index += operator.length;
      return true;
    }
    return false;
  }

  parseComparison() {
    let left = this.parseAdditive();
    this.skipWhitespace();
    const operators = ["<>", ">=", "<=", "=", ">", "<"];
    for (const operator of operators) {
      if (this.text.slice(this.index, this.index + operator.length) === operator) {
        this.index += operator.length;
        const right = this.parseAdditive();
        if (operator === "=") left = left === right ? 1 : 0;
        if (operator === "<>") left = left !== right ? 1 : 0;
        if (operator === ">") left = chatBasicToNumber(left) > chatBasicToNumber(right) ? 1 : 0;
        if (operator === "<") left = chatBasicToNumber(left) < chatBasicToNumber(right) ? 1 : 0;
        if (operator === ">=") left = chatBasicToNumber(left) >= chatBasicToNumber(right) ? 1 : 0;
        if (operator === "<=") left = chatBasicToNumber(left) <= chatBasicToNumber(right) ? 1 : 0;
        this.skipWhitespace();
      }
    }
    return left;
  }

  parseAdditive() {
    let value = this.parseMultiplicative();
    while (true) {
      if (this.matchOperator("+")) {
        const right = this.parseMultiplicative();
        value = typeof value === "string" || typeof right === "string" ? `${value}${right}` : chatBasicToNumber(value) + chatBasicToNumber(right);
      } else if (this.matchOperator("-")) {
        value = chatBasicToNumber(value) - chatBasicToNumber(this.parseMultiplicative());
      } else {
        return value;
      }
    }
  }

  parseMultiplicative() {
    let value = this.parsePower();
    while (true) {
      if (this.matchOperator("*")) {
        value = chatBasicToNumber(value) * chatBasicToNumber(this.parsePower());
      } else if (this.matchOperator("/")) {
        value = chatBasicToNumber(value) / chatBasicToNumber(this.parsePower());
      } else {
        return value;
      }
    }
  }

  parsePower() {
    let value = this.parseUnary();
    if (this.matchOperator("^")) {
      value = Math.pow(chatBasicToNumber(value), chatBasicToNumber(this.parsePower()));
    }
    return value;
  }

  parseUnary() {
    this.skipWhitespace();
    if (this.matchOperator("+")) return chatBasicToNumber(this.parseUnary());
    if (this.matchOperator("-")) return -chatBasicToNumber(this.parseUnary());
    return this.parsePrimary();
  }

  parsePrimary() {
    this.skipWhitespace();
    const char = this.peek();
    if (char === "(") {
      this.index += 1;
      const value = this.parseComparison();
      this.skipWhitespace();
      if (this.peek() !== ")") throw new Error("Missing closing parenthesis in BASIC expression.");
      this.index += 1;
      return value;
    }
    if (char === '"') return this.parseString();
    if (/[0-9.]/.test(char)) return this.parseNumber();
    if (/[A-Za-z_]/.test(char)) return this.parseIdentifierOrCall();
    throw new Error(`Unexpected BASIC expression token: ${char || "end of input"}`);
  }

  parseString() {
    let value = "";
    this.index += 1;
    while (this.index < this.text.length) {
      const char = this.text[this.index];
      if (char === '"') {
        if (this.text[this.index + 1] === '"') {
          value += '"';
          this.index += 2;
          continue;
        }
        this.index += 1;
        return value;
      }
      value += char;
      this.index += 1;
    }
    throw new Error("Unterminated BASIC string literal.");
  }

  parseNumber() {
    const start = this.index;
    while (/[0-9.]/.test(this.peek())) this.index += 1;
    if (/[Ee]/.test(this.peek())) {
      this.index += 1;
      if (/[+-]/.test(this.peek())) this.index += 1;
      while (/[0-9]/.test(this.peek())) this.index += 1;
    }
    const numeric = Number(this.text.slice(start, this.index));
    if (!Number.isFinite(numeric)) throw new Error(`Invalid BASIC number: ${this.text.slice(start, this.index)}`);
    return numeric;
  }

  parseIdentifierOrCall() {
    const start = this.index;
    while (/[A-Za-z0-9_$]/.test(this.peek())) this.index += 1;
    const rawName = this.text.slice(start, this.index);
    const name = rawName.toUpperCase();
    this.skipWhitespace();
    if (this.peek() === "(") {
      this.index += 1;
      const args = [];
      this.skipWhitespace();
      if (this.peek() !== ")") {
        while (true) {
          args.push(this.parseComparison());
          this.skipWhitespace();
          if (this.peek() === ",") {
            this.index += 1;
            continue;
          }
          break;
        }
      }
      if (this.peek() !== ")") throw new Error(`Missing closing parenthesis for BASIC function ${rawName}.`);
      this.index += 1;
      return this.callFunction(name, args);
    }
    if (name === "TRUE") return 1;
    if (name === "FALSE") return 0;
    return chatBasicLookupVariable(this.variables, rawName, 0);
  }

  callFunction(name, args) {
    if (name === "GETVAR") {
      const key = String(args[0] ?? "");
      return chatBasicLookupVariable(this.variables, key, "");
    }
    if (name === "SETVAR") {
      const key = String(args[0] ?? "").trim();
      if (!key) throw new Error("SETVAR requires a variable name.");
      this.variables[key] = args.length > 1 ? args[1] : "";
      return this.variables[key];
    }
    if (name === "ABS") return Math.abs(chatBasicToNumber(args[0]));
    if (name === "INT") return Math.trunc(chatBasicToNumber(args[0]));
    if (name === "ROUND") return Math.round(chatBasicToNumber(args[0]));
    if (name === "LEN") return String(args[0] ?? "").length;
    if (name === "VAL") return chatBasicToNumber(args[0]);
    if (name === "STR$" || name === "STR") return String(args[0] ?? "");
    if (name === "LEFT$" || name === "LEFT") return String(args[0] ?? "").slice(0, Math.max(0, chatBasicToNumber(args[1])));
    if (name === "RIGHT$" || name === "RIGHT") {
      const text = String(args[0] ?? "");
      return text.slice(Math.max(0, text.length - chatBasicToNumber(args[1])));
    }
    if (name === "MID$" || name === "MID") {
      const text = String(args[0] ?? "");
      const start = Math.max(1, chatBasicToNumber(args[1])) - 1;
      if (args.length > 2) return text.slice(start, start + Math.max(0, chatBasicToNumber(args[2])));
      return text.slice(start);
    }
    if (name === "UCASE$" || name === "UCASE") return String(args[0] ?? "").toUpperCase();
    if (name === "LCASE$" || name === "LCASE") return String(args[0] ?? "").toLowerCase();
    if (name === "RND") return Math.random();
    throw new Error(`Unsupported BASIC function in local fallback: ${name}`);
  }
}

class ChatConsoleBasicFallbackProgram {
  constructor(source, options) {
    this.source = String(source || "");
    this.options = options || {};
    this.variables = {};
    this.result = null;
    this.RESULT = null;
  }

  evaluate(expression) {
    return new ChatConsoleBasicExpressionParser(expression, this.variables).parse();
  }

  emit(value, newline = false) {
    if (this.options && typeof this.options.output === "function") {
      this.options.output(String(value ?? "") + (newline ? "\n" : ""));
      return;
    }
    const bindings = this.options?.bindings || {};
    if (typeof bindings.Print === "function") {
      bindings.Print(value);
    }
  }

  assign(target, expression) {
    const name = chatBasicVariableNameFromTarget(target);
    const value = this.evaluate(expression);
    this.variables[name] = value;
    this.result = value;
    this.RESULT = value;
    return value;
  }

  executeStatement(statement) {
    let text = chatBasicStripComment(chatBasicStripLineNumber(statement));
    if (!text) return true;
    if (/^(END|STOP)$/i.test(text)) return false;
    if (/^CLS$/i.test(text)) return true;

    const ifMatch = text.match(/^IF\s+(.+?)\s+THEN\s+(.+)$/i);
    if (ifMatch) {
      if (chatBasicTruthy(this.evaluate(ifMatch[1]))) return this.executeStatement(ifMatch[2]);
      return true;
    }

    const printMatch = text.match(/^(PRINT|\?)\s*(.*)$/i);
    if (printMatch) {
      const expressionText = printMatch[2] || "";
      if (!expressionText.trim()) {
        this.emit("", true);
        this.result = "";
        this.RESULT = "";
        return true;
      }
      const values = chatBasicSplitOutsideStrings(expressionText, [",", ";"]).filter((item) => item.length > 0).map((item) => this.evaluate(item));
      const output = values.map((value) => String(value ?? "")).join(" ");
      this.emit(output, true);
      this.result = values.length ? values[values.length - 1] : output;
      this.RESULT = this.result;
      return true;
    }

    const letMatch = text.match(/^(?:LET\s+)?([A-Za-z][A-Za-z0-9_]*\$?)\s*=\s*(.+)$/i);
    if (letMatch) {
      this.assign(letMatch[1], letMatch[2]);
      return true;
    }

    if (/^SETVAR\s*\(/i.test(text)) {
      this.result = this.evaluate(text);
      this.RESULT = this.result;
      return true;
    }

    this.result = this.evaluate(text);
    this.RESULT = this.result;
    return true;
  }

  run() {
    const sourceLines = this.source.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    for (const sourceLine of sourceLines) {
      const statements = chatBasicSplitOutsideStrings(sourceLine, [":"]);
      for (const statement of statements) {
        if (!this.executeStatement(statement)) return this;
      }
    }
    return this;
  }

  Run() {
    return this.run();
  }
}

async function chatRunBasic(runtime, source, stdoutState, variables) {
  const sharedVariables = chatBasicNormalizeVariables(variables);
  const bindings = {
    PutCh(value) {
      stdoutState.text = chatBasicAppendOutput(stdoutState.text, value);
    },
    putch(value) {
      stdoutState.text = chatBasicAppendOutput(stdoutState.text, value);
    },
    Print(value) {
      stdoutState.text += `${value ?? ""}\n`;
    },
    PRINT(value) {
      stdoutState.text += `${value ?? ""}\n`;
    },
    GETVAR(name) {
      return sharedVariables[String(name || "")] ?? "";
    },
    getvar(name) {
      return sharedVariables[String(name || "")] ?? "";
    },
    SETVAR(name, value) {
      sharedVariables[String(name || "")] = value;
      return value;
    },
    setvar(name, value) {
      sharedVariables[String(name || "")] = value;
      return value;
    },
  };
  const options = {
    bindings,
    output(value) {
      stdoutState.text = chatBasicAppendOutput(stdoutState.text, value);
    },
  };
  const program = runtime.Basic(`${runtime.fallback ? "" : chatBasicVariablePrelude(sharedVariables)}${String(source || "")}`, options);
  if (runtime.fallback && program && program.variables && typeof program.variables === "object") {
    Object.assign(program.variables, sharedVariables);
  }
  if (program && typeof program.run === "function") {
    const result = program.run();
    if (result && typeof result.then === "function") await result;
    return {program, variables: chatBasicExtractProgramVariables(program, sharedVariables)};
  }
  if (program && typeof program.Run === "function") {
    const result = program.Run();
    if (result && typeof result.then === "function") await result;
    return {program, variables: chatBasicExtractProgramVariables(program, sharedVariables)};
  }
  if (typeof program === "function") {
    const result = program();
    if (result && typeof result.then === "function") await result;
    return {program: {result}, variables: sharedVariables};
  }
  return {program, variables: chatBasicExtractProgramVariables(program, sharedVariables)};
}

self.onmessage = async (event) => {
  const request = event.data || {};
  const startedAt = Date.now();
  const outputParts = [];
  const stdoutState = {text: ""};
  let variables = chatBasicNormalizeVariables(request.shared_variables || {});

  try {
    const runtime = await chatLoadWwwBasic();
    const runResult = await chatRunBasic(runtime, request.source, stdoutState, variables);
    variables = chatBasicNormalizeVariables(runResult.variables || variables);
    const program = runResult.program;
    const value = program && Object.prototype.hasOwnProperty.call(program, "RESULT")
      ? program.RESULT
      : program && Object.prototype.hasOwnProperty.call(program, "result")
        ? program.result
        : chatBasicLastValue(stdoutState.text);

    if (stdoutState.text) outputParts.push({kind: "stdout", title: "stdout", content: stdoutState.text.trimEnd(), metadata: {}});
    if (value !== null && value !== undefined) outputParts.push({kind: "text", title: "Result", content: String(value), metadata: {}});
    if (!outputParts.length) outputParts.push({kind: "text", title: "BASIC", content: "BASIC cell completed.", metadata: {}});

    self.postMessage({
      id: request.id,
      ok: true,
      value,
      variables,
      output_parts: outputParts,
      error: null,
      duration_ms: chatBasicDuration(startedAt),
    });
  } catch (error) {
    if (stdoutState.text) outputParts.push({kind: "stdout", title: "stdout", content: stdoutState.text.trimEnd(), metadata: {}});
    outputParts.push({kind: "error", title: "BASIC error", content: chatBasicErrorMessage(error), metadata: {}});
    self.postMessage({
      id: request.id,
      ok: false,
      value: null,
      variables,
      output_parts: outputParts,
      error: chatBasicErrorMessage(error),
      duration_ms: chatBasicDuration(startedAt),
    });
  }
};
