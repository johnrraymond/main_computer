(function installMainComputerCalculatorCore(root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.MainComputerCalculatorCore = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function buildMainComputerCalculatorCore() {
  "use strict";

  const ARITHMETIC_GRAMMAR = "calculator-arithmetic-expression-v1";
  const GRAPH_GRAMMAR = "calculator-graph-expression-v1";
  const UNIT_GRAMMAR = "calculator-unit-expression-v1";
  const MAX_EXPRESSION_LENGTH = 4096;
  const MAX_TOKEN_COUNT = 1024;
  const MAX_PARSE_DEPTH = 64;

  class CalculatorExpressionError extends Error {
    constructor(code, message, position = -1) {
      super(message);
      this.name = "CalculatorExpressionError";
      this.code = code;
      this.position = Number.isInteger(position) ? position : -1;
    }
  }

  const calculatorGraphFunctions = Object.freeze({
    sin: Math.sin,
    cos: Math.cos,
    tan: Math.tan,
    asin: Math.asin,
    acos: Math.acos,
    atan: Math.atan,
    sqrt: Math.sqrt,
    abs: Math.abs,
    log: Math.log10,
    ln: Math.log,
    exp: Math.exp,
    floor: Math.floor,
    ceil: Math.ceil,
    round: Math.round,
    min: Math.min,
    max: Math.max
  });

  const calculatorGraphConstants = Object.freeze({
    pi: Math.PI,
    e: Math.E
  });

  const calculatorGraphFunctionArity = Object.freeze({
    sin: Object.freeze({min: 1, max: 1}),
    cos: Object.freeze({min: 1, max: 1}),
    tan: Object.freeze({min: 1, max: 1}),
    asin: Object.freeze({min: 1, max: 1}),
    acos: Object.freeze({min: 1, max: 1}),
    atan: Object.freeze({min: 1, max: 1}),
    sqrt: Object.freeze({min: 1, max: 1}),
    abs: Object.freeze({min: 1, max: 1}),
    log: Object.freeze({min: 1, max: 1}),
    ln: Object.freeze({min: 1, max: 1}),
    exp: Object.freeze({min: 1, max: 1}),
    floor: Object.freeze({min: 1, max: 1}),
    ceil: Object.freeze({min: 1, max: 1}),
    round: Object.freeze({min: 1, max: 1}),
    min: Object.freeze({min: 1, max: Number.POSITIVE_INFINITY}),
    max: Object.freeze({min: 1, max: Number.POSITIVE_INFINITY})
  });

  const calculatorUnitDefinitions = Object.freeze({
    mm: Object.freeze({dimension: "length", factor: 0.001, canonical: "m"}),
    cm: Object.freeze({dimension: "length", factor: 0.01, canonical: "m"}),
    m: Object.freeze({dimension: "length", factor: 1, canonical: "m"}),
    km: Object.freeze({dimension: "length", factor: 1000, canonical: "m"}),
    ms: Object.freeze({dimension: "time", factor: 0.001, canonical: "s"}),
    s: Object.freeze({dimension: "time", factor: 1, canonical: "s"}),
    min: Object.freeze({dimension: "time", factor: 60, canonical: "s"}),
    h: Object.freeze({dimension: "time", factor: 3600, canonical: "s"})
  });

  const calculatorUnitPattern = Object.freeze(
    Object.keys(calculatorUnitDefinitions).sort((left, right) => right.length - left.length)
  );

  function expressionText(value) {
    return String(value == null ? "" : value);
  }

  function normalizeCalculatorExpression(value) {
    const expression = expressionText(value).replace(/[xX]/g, "*");
    if (calculatorUnitExpressionLooksUnitAware(expression)) {
      return expression.replace(/[^\d+\-*/%.() A-Za-z]/g, "");
    }
    return expression.replace(/[^\d+\-*/%.() ]/g, "");
  }

  function normalizeCalculatorUnitSymbol(unit) {
    return expressionText(unit).trim().toLowerCase();
  }

  function formatCalculatorNumber(value) {
    if (!Number.isFinite(value)) return String(value);
    const rounded = Math.round((value + Number.EPSILON) * 1e12) / 1e12;
    return Number.isInteger(rounded) ? String(rounded) : String(rounded).replace(/\.?0+$/, "");
  }

  function calculatorUnitExpressionLooksUnitAware(rawExpression) {
    const expression = expressionText(rawExpression);
    if (!/[A-Za-z]/.test(expression)) return false;
    return new RegExp(`(^|[^A-Za-z])(?:${calculatorUnitPattern.join("|")})(?=$|[^A-Za-z])`, "i").test(expression);
  }

  function tokenizeCalculatorUnitExpression(rawExpression) {
    const expression = expressionText(rawExpression).trim();
    const tokens = [];
    let index = 0;
    while (index < expression.length) {
      const char = expression[index];
      if (/\s/.test(char)) {
        index += 1;
        continue;
      }
      const numeric = expression.slice(index).match(/^(?:\d+(?:\.\d*)?|\.\d+)/);
      if (numeric) {
        tokens.push(Object.freeze({
          type: "number",
          value: Number(numeric[0]),
          raw: numeric[0],
          position: index
        }));
        index += numeric[0].length;
        continue;
      }
      const unit = expression.slice(index).match(/^[A-Za-z]+/);
      if (unit) {
        const symbol = normalizeCalculatorUnitSymbol(unit[0]);
        const definition = calculatorUnitDefinitions[symbol];
        if (!definition) {
          throw new CalculatorExpressionError(
            "unit-unsupported",
            `unsupported unit: ${unit[0]}`,
            index
          );
        }
        tokens.push(Object.freeze({
          type: "unit",
          value: symbol,
          raw: unit[0],
          definition,
          position: index
        }));
        index += unit[0].length;
        continue;
      }
      if ("+-*/()".includes(char)) {
        tokens.push(Object.freeze({type: char, value: char, raw: char, position: index}));
        index += 1;
        continue;
      }
      throw new CalculatorExpressionError("unit-token-invalid", `unsupported token: ${char}`, index);
    }
    return Object.freeze(tokens);
  }

  function calculatorUnitScalar(value) {
    return Object.freeze({
      kind: "scalar",
      value,
      displayValue: formatCalculatorNumber(value)
    });
  }

  function calculatorUnitQuantity(canonicalValue, unit, dimension, factor, statusText = "unit quantity ready") {
    const value = canonicalValue / factor;
    const definition = calculatorUnitDefinitions[unit];
    return Object.freeze({
      kind: "unit",
      dimension,
      unit,
      canonicalUnit: definition ? definition.canonical : unit,
      canonicalValue,
      factor,
      value,
      displayValue: `${formatCalculatorNumber(value)} ${unit}`,
      statusText
    });
  }

  function calculatorUnitQuantityFromToken(numberToken, unitToken) {
    const numericValue = Number(numberToken.value);
    const definition = unitToken.definition;
    if (!Number.isFinite(numericValue)) {
      throw new CalculatorExpressionError("unit-number-invalid", "unit quantity is not finite", numberToken.position);
    }
    return calculatorUnitQuantity(
      numericValue * definition.factor,
      unitToken.value,
      definition.dimension,
      definition.factor
    );
  }

  function calculatorUnitOperatorPosition(operatorToken) {
    return operatorToken && Number.isInteger(operatorToken.position) ? operatorToken.position : -1;
  }

  function calculatorUnitDimensionMismatch(left, right, operatorToken) {
    throw new CalculatorExpressionError(
      "unit-dimension-mismatch",
      `cannot combine ${left.dimension || "scalar"} and ${right.dimension || "scalar"} units`,
      calculatorUnitOperatorPosition(operatorToken)
    );
  }

  function calculatorUnitScalarMismatch(operatorToken) {
    throw new CalculatorExpressionError(
      "unit-scalar-mismatch",
      "cannot combine unit and scalar values",
      calculatorUnitOperatorPosition(operatorToken)
    );
  }

  function calculatorUnitAdd(left, right, operatorToken) {
    if (left.kind === "scalar" && right.kind === "scalar") {
      return calculatorUnitScalar(
        operatorToken.type === "-" ? left.value - right.value : left.value + right.value
      );
    }
    if (left.kind !== "unit" || right.kind !== "unit") {
      calculatorUnitScalarMismatch(operatorToken);
    }
    if (left.dimension !== right.dimension) {
      calculatorUnitDimensionMismatch(left, right, operatorToken);
    }
    const canonicalValue = operatorToken.type === "-"
      ? left.canonicalValue - right.canonicalValue
      : left.canonicalValue + right.canonicalValue;
    const statusText = left.unit === right.unit ? "unit arithmetic complete" : "units normalized";
    return calculatorUnitQuantity(canonicalValue, left.unit, left.dimension, left.factor, statusText);
  }

  function calculatorUnitMultiply(left, right, operatorToken) {
    if (left.kind === "scalar" && right.kind === "scalar") {
      return calculatorUnitScalar(left.value * right.value);
    }
    if (left.kind === "unit" && right.kind === "scalar") {
      return calculatorUnitQuantity(
        left.canonicalValue * right.value,
        left.unit,
        left.dimension,
        left.factor,
        "unit scalar arithmetic complete"
      );
    }
    if (left.kind === "scalar" && right.kind === "unit") {
      return calculatorUnitQuantity(
        right.canonicalValue * left.value,
        right.unit,
        right.dimension,
        right.factor,
        "unit scalar arithmetic complete"
      );
    }
    throw new CalculatorExpressionError(
      "compound-unit-unsupported",
      "compound unit multiplication is not supported",
      calculatorUnitOperatorPosition(operatorToken)
    );
  }

  function calculatorUnitDivide(left, right, operatorToken) {
    const divisor = right.kind === "unit" ? right.canonicalValue : right.value;
    if (divisor === 0) {
      throw new CalculatorExpressionError("division-by-zero", "cannot divide by zero", calculatorUnitOperatorPosition(operatorToken));
    }
    if (left.kind === "scalar" && right.kind === "scalar") {
      return calculatorUnitScalar(left.value / right.value);
    }
    if (left.kind === "unit" && right.kind === "scalar") {
      return calculatorUnitQuantity(
        left.canonicalValue / right.value,
        left.unit,
        left.dimension,
        left.factor,
        "unit scalar arithmetic complete"
      );
    }
    if (left.kind === "unit" && right.kind === "unit") {
      if (left.dimension !== right.dimension) {
        throw new CalculatorExpressionError(
          "compound-unit-unsupported",
          "compound unit division is not supported",
          calculatorUnitOperatorPosition(operatorToken)
        );
      }
      return Object.freeze(Object.assign({}, calculatorUnitScalar(left.canonicalValue / right.canonicalValue), {
        statusText: "unit ratio complete"
      }));
    }
    throw new CalculatorExpressionError(
      "reciprocal-unit-unsupported",
      "reciprocal units are not supported",
      calculatorUnitOperatorPosition(operatorToken)
    );
  }

  function parseCalculatorUnitValue(tokens) {
    let cursor = 0;

    function peek() {
      return tokens[cursor] || null;
    }

    function consume(type = null) {
      const token = peek();
      if (!token || (type && token.type !== type)) return null;
      cursor += 1;
      return token;
    }

    function parsePrimary() {
      const token = peek();
      if (!token) {
        throw new CalculatorExpressionError("unit-expression-incomplete", "unit expression is incomplete", -1);
      }
      if (consume("(")) {
        const value = parseAdditive();
        if (!consume(")")) {
          throw new CalculatorExpressionError("unit-paren-unclosed", "missing closing parenthesis", token.position);
        }
        return value;
      }
      const numberToken = consume("number");
      if (numberToken) {
        const unitToken = consume("unit");
        if (unitToken) {
          return calculatorUnitQuantityFromToken(numberToken, unitToken);
        }
        return calculatorUnitScalar(numberToken.value);
      }
      if (token.type === "unit") {
        throw new CalculatorExpressionError("unit-quantity-invalid", "expected a number before the unit", token.position);
      }
      throw new CalculatorExpressionError("unit-expression-invalid", "expected a number or grouped unit expression", token.position);
    }

    function parseUnary() {
      const operator = peek();
      if (operator && (operator.type === "+" || operator.type === "-")) {
        consume(operator.type);
        const value = parseUnary();
        if (operator.type === "+") return value;
        if (value.kind === "unit") {
          return calculatorUnitQuantity(
            -value.canonicalValue,
            value.unit,
            value.dimension,
            value.factor,
            value.statusText
          );
        }
        return calculatorUnitScalar(-value.value);
      }
      return parsePrimary();
    }

    function parseMultiplicative() {
      let value = parseUnary();
      while (peek() && (peek().type === "*" || peek().type === "/")) {
        const operator = consume(peek().type);
        const right = parseUnary();
        value = operator.type === "*"
          ? calculatorUnitMultiply(value, right, operator)
          : calculatorUnitDivide(value, right, operator);
      }
      return value;
    }

    function parseAdditive() {
      let value = parseMultiplicative();
      while (peek() && (peek().type === "+" || peek().type === "-")) {
        const operator = consume(peek().type);
        const right = parseMultiplicative();
        value = calculatorUnitAdd(value, right, operator);
      }
      return value;
    }

    const value = parseAdditive();
    if (cursor !== tokens.length) {
      const token = peek();
      throw new CalculatorExpressionError("unit-expression-invalid", "unexpected unit expression token", token.position);
    }
    return value;
  }

  function buildCalculatorUnitResult(expression, normalizedExpression, rawExpression, value, tokenCount) {
    const base = {
      ok: true,
      expression,
      normalizedExpression,
      rawExpression,
      grammar: UNIT_GRAMMAR,
      parseStatus: "valid",
      parserCode: "",
      errorPosition: -1,
      tokenCount,
      resultKind: value.kind === "unit" ? "unit-quantity" : "unit-scalar",
      value: value.kind === "unit" ? value.value : value.value,
      displayValue: value.displayValue,
      statusText: value.statusText || "unit arithmetic complete"
    };
    if (value.kind === "unit") {
      return Object.freeze(Object.assign(base, {
        dimension: value.dimension,
        unit: value.unit,
        canonicalUnit: value.canonicalUnit,
        canonicalValue: value.canonicalValue
      }));
    }
    return Object.freeze(base);
  }

  function invalidUnitEvaluation(error, rawExpression, fallbackMessage) {
    const expression = expressionText(rawExpression).trim();
    const known = error instanceof CalculatorExpressionError;
    return Object.freeze({
      ok: false,
      expression,
      normalizedExpression: expression.replace(/\s+/g, ""),
      rawExpression: expression,
      grammar: UNIT_GRAMMAR,
      parseStatus: "invalid",
      parserCode: known ? error.code : "unit-expression-invalid",
      errorPosition: known ? error.position : -1,
      error: known ? error.message : fallbackMessage
    });
  }

  function evaluateCalculatorUnitExpression(rawExpression) {
    try {
      const expression = expressionText(rawExpression).trim();
      if (!expression) {
        throw new CalculatorExpressionError("expression-required", "enter an expression", -1);
      }
      const tokens = tokenizeCalculatorUnitExpression(expression);
      if (!tokens.length) {
        throw new CalculatorExpressionError("expression-required", "enter an expression", -1);
      }
      const value = parseCalculatorUnitValue(tokens);
      if (value.kind !== "unit" && !tokens.some((token) => token.type === "unit")) {
        throw new CalculatorExpressionError("unit-expression-invalid", "expected at least one unit", -1);
      }
      return buildCalculatorUnitResult(
        expression,
        tokens.map((token) => token.raw).join(""),
        expression,
        value,
        tokens.length
      );
    } catch (error) {
      return invalidUnitEvaluation(error, rawExpression, "check unit expression");
    }
  }

  function evaluateCalculatorExpression(rawExpression) {
    if (calculatorUnitExpressionLooksUnitAware(rawExpression)) {
      return evaluateCalculatorUnitExpression(rawExpression);
    }
    return evaluateCalculatorArithmeticExpression(rawExpression);
  }

  function calculatorReadyState(expression, extras = {}) {
    return Object.freeze(Object.assign({
      ok: true,
      expression: expressionText(expression) || "0",
      result: "ready",
      statusText: "ready"
    }, extras));
  }

  function appendCalculatorDisplayToken(expression, token) {
    const current = expressionText(expression) || "0";
    const key = expressionText(token);
    const nextExpression = current === "0" && /\d/.test(key) ? key : current + key;
    return calculatorReadyState(nextExpression, {token: key});
  }

  function clearCalculatorDisplayExpression() {
    return calculatorReadyState("0");
  }

  function backspaceCalculatorDisplayExpression(expression) {
    const current = expressionText(expression) || "0";
    const nextExpression = current.slice(0, -1) || "0";
    return calculatorReadyState(nextExpression);
  }

  function normalizedTextSelection(expression, selectionStart, selectionEnd) {
    const value = expressionText(expression);
    const fallback = value.length;
    const start = Number.isFinite(Number(selectionStart))
      ? Math.max(0, Math.min(value.length, Number(selectionStart)))
      : fallback;
    const end = Number.isFinite(Number(selectionEnd))
      ? Math.max(0, Math.min(value.length, Number(selectionEnd)))
      : start;
    return Object.freeze({
      value,
      start: Math.min(start, end),
      end: Math.max(start, end)
    });
  }

  function calculatorGraphEditState(expression, selectionStart, selectionEnd) {
    const selection = normalizedTextSelection(expression, selectionStart, selectionEnd);
    return Object.freeze({
      expression: selection.value,
      selectionStart: selection.start,
      selectionEnd: selection.end,
      statusText: "ready"
    });
  }

  function insertCalculatorGraphText(expression, text, selectionStart, selectionEnd, caretBack = 0) {
    const selection = normalizedTextSelection(expression, selectionStart, selectionEnd);
    const inserted = expressionText(text);
    const nextExpression = selection.value.slice(0, selection.start) + inserted + selection.value.slice(selection.end);
    const caret = Math.max(0, Math.min(nextExpression.length, selection.start + inserted.length - Number(caretBack || 0)));
    return Object.freeze({
      expression: nextExpression,
      selectionStart: caret,
      selectionEnd: caret,
      statusText: "ready",
      inserted
    });
  }

  function clearCalculatorGraphExpression() {
    return calculatorGraphEditState("", 0, 0);
  }

  function backspaceCalculatorGraphExpression(expression, selectionStart, selectionEnd) {
    const selection = normalizedTextSelection(expression, selectionStart, selectionEnd);
    if (selection.start !== selection.end) {
      return calculatorGraphEditState(
        selection.value.slice(0, selection.start) + selection.value.slice(selection.end),
        selection.start,
        selection.start
      );
    }
    if (selection.start <= 0) {
      return calculatorGraphEditState(selection.value, 0, 0);
    }
    const nextExpression = selection.value.slice(0, selection.start - 1) + selection.value.slice(selection.start);
    return calculatorGraphEditState(nextExpression, selection.start - 1, selection.start - 1);
  }

  function normalizeGraphExpression(value) {
    return expressionText(value).trim().replace(/\s+/g, "").toLowerCase();
  }

  function extractCalculatorExpression(modelText) {
    const cleaned = expressionText(modelText)
      .replace(/```(?:javascript|js|text)?/gi, "")
      .replace(/```/g, "");
    const candidates = cleaned.match(/[-+*/%().\d xX]+/g) || [];
    const scored = candidates
      .map((candidate) => normalizeCalculatorExpression(candidate).trim())
      .filter((candidate) => candidate.length > 0)
      .filter((candidate) => {
        try {
          parseCalculatorArithmeticExpression(candidate);
          return true;
        } catch {
          return false;
        }
      })
      .sort((left, right) => {
        const leftHasOperator = /[+\-*/%]/.test(left) ? 1 : 0;
        const rightHasOperator = /[+\-*/%]/.test(right) ? 1 : 0;
        return rightHasOperator - leftHasOperator || right.length - left.length;
      });
    if (scored[0]) return scored[0];

    const fallback = normalizeCalculatorExpression(cleaned).trim();
    try {
      parseCalculatorArithmeticExpression(fallback);
      return fallback;
    } catch {
      return "";
    }
  }

  function extractCalculatorGraphExpression(modelText) {
    const cleaned = expressionText(modelText)
      .replace(/```(?:javascript|js|text|math)?/gi, "")
      .replace(/```/g, "")
      .replace(/\bf\s*\(\s*x\s*\)\s*=/gi, "")
      .replace(/\by\s*=/gi, "")
      .toLowerCase();
    const allowedNames = Object.keys(calculatorGraphFunctions).concat(Object.keys(calculatorGraphConstants), ["x"]).join("|");
    const graphNumber = "(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:e[+\\-]?\\d+)?";
    const candidatePattern = new RegExp(`(?:\\b(?:${allowedNames})\\b|${graphNumber}|[+\\-*/%^(),\\s])+`, "g");
    const candidates = cleaned.match(candidatePattern) || [];
    const scored = candidates
      .map((candidate) => normalizeGraphExpression(candidate))
      .filter((candidate) => candidate && /^[a-z0-9+\-*/%^(),.]+$/.test(candidate))
      .filter((candidate) => (
        /\d/.test(candidate)
        || /\bx\b/.test(candidate)
        || Object.keys(calculatorGraphFunctions).some((name) => candidate.includes(`${name}(`))
        || Object.keys(calculatorGraphConstants).some((name) => new RegExp(`\\b${name}\\b`).test(candidate))
      ))
      .filter((candidate) => {
        try {
          tokenizeCalculatorGraphExpression(candidate);
          return true;
        } catch {
          return false;
        }
      })
      .sort((left, right) => {
        const leftHasX = /\bx\b/.test(left) ? 1 : 0;
        const rightHasX = /\bx\b/.test(right) ? 1 : 0;
        const leftHasFn = /[a-z]{2,}\(/.test(left) ? 1 : 0;
        const rightHasFn = /[a-z]{2,}\(/.test(right) ? 1 : 0;
        return rightHasX - leftHasX || rightHasFn - leftHasFn || right.length - left.length;
      });
    return scored[0] || "";
  }

  function prepareArithmeticExpression(rawExpression) {
    const raw = expressionText(rawExpression).trim();
    if (!raw) {
      throw new CalculatorExpressionError(
        "expression-required",
        "enter an expression",
        0
      );
    }

    const multiplied = raw.replace(/[xX]/g, "*");
    const unsupported = multiplied.match(/[^\d+\-*/%.()\s]/);
    if (unsupported) {
      throw new CalculatorExpressionError(
        "unsupported-token",
        `unsupported token: ${unsupported[0]}`,
        unsupported.index
      );
    }

    const expression = multiplied.replace(/\s+/g, "");
    if (!expression) {
      throw new CalculatorExpressionError(
        "expression-required",
        "enter an expression",
        0
      );
    }
    if (expression.length > MAX_EXPRESSION_LENGTH) {
      throw new CalculatorExpressionError(
        "expression-too-long",
        "expression is too long",
        MAX_EXPRESSION_LENGTH
      );
    }
    return Object.freeze({
      rawExpression: raw,
      expression,
      normalizedExpression: expression
    });
  }

  function tokenizeNumber(expression, index, allowExponent) {
    const pattern = allowExponent
      ? /^(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?/i
      : /^(?:\d+(?:\.\d*)?|\.\d+)/;
    const match = expression.slice(index).match(pattern);
    if (!match) {
      throw new CalculatorExpressionError(
        "unsupported-number",
        "unsupported number",
        index
      );
    }
    const value = Number(match[0]);
    if (!Number.isFinite(value)) {
      throw new CalculatorExpressionError(
        "invalid-number",
        "invalid number",
        index
      );
    }
    return Object.freeze({
      type: "number",
      value,
      lexeme: match[0],
      position: index
    });
  }

  function enforceTokenLimit(tokens, position) {
    if (tokens.length >= MAX_TOKEN_COUNT) {
      throw new CalculatorExpressionError(
        "too-many-tokens",
        "expression has too many tokens",
        position
      );
    }
  }

  function tokenizeCalculatorArithmeticExpression(rawExpression) {
    const prepared = prepareArithmeticExpression(rawExpression);
    const expression = prepared.expression;
    const tokens = [];
    let index = 0;

    while (index < expression.length) {
      enforceTokenLimit(tokens, index);
      const char = expression[index];
      if (/\d|\./.test(char)) {
        const token = tokenizeNumber(expression, index, false);
        tokens.push(token);
        index += token.lexeme.length;
        continue;
      }
      if ("+-*/%()".includes(char)) {
        tokens.push(Object.freeze({
          type: char,
          value: char,
          lexeme: char,
          position: index
        }));
        index += 1;
        continue;
      }
      throw new CalculatorExpressionError(
        "unsupported-token",
        `unsupported token: ${char}`,
        index
      );
    }

    return Object.freeze({
      ...prepared,
      grammar: ARITHMETIC_GRAMMAR,
      tokens: Object.freeze(tokens)
    });
  }

  function tokenizeCalculatorGraphExpression(rawExpression) {
    const expression = normalizeGraphExpression(rawExpression);
    if (!expression) {
      throw new CalculatorExpressionError(
        "expression-required",
        "enter f(x) before graphing",
        0
      );
    }
    if (expression.length > MAX_EXPRESSION_LENGTH) {
      throw new CalculatorExpressionError(
        "expression-too-long",
        "expression is too long",
        MAX_EXPRESSION_LENGTH
      );
    }

    const tokens = [];
    let index = 0;
    while (index < expression.length) {
      enforceTokenLimit(tokens, index);
      const char = expression[index];
      if (/\d|\./.test(char)) {
        const token = tokenizeNumber(expression, index, true);
        tokens.push(token);
        index += token.lexeme.length;
        continue;
      }
      if (/[a-z]/.test(char)) {
        const match = expression.slice(index).match(/^[a-z]+/);
        const name = match[0];
        if (
          name !== "x"
          && !(name in calculatorGraphConstants)
          && !(name in calculatorGraphFunctions)
        ) {
          throw new CalculatorExpressionError(
            "unsupported-name",
            `unsupported token: ${name}`,
            index
          );
        }
        tokens.push(Object.freeze({
          type: "name",
          value: name,
          lexeme: name,
          position: index
        }));
        index += name.length;
        continue;
      }
      if ("+-*/%^(),".includes(char)) {
        tokens.push(Object.freeze({
          type: char,
          value: char,
          lexeme: char,
          position: index
        }));
        index += 1;
        continue;
      }
      throw new CalculatorExpressionError(
        "unsupported-token",
        `unsupported token: ${char}`,
        index
      );
    }

    return Object.freeze({
      rawExpression: expressionText(rawExpression),
      expression,
      normalizedExpression: expression,
      grammar: GRAPH_GRAMMAR,
      tokens: Object.freeze(tokens)
    });
  }

  function buildParser(tokenized, options) {
    const tokens = tokenized.tokens;
    let position = 0;
    let depth = 0;

    function fail(code, message, token = tokens[position]) {
      const errorPosition = token ? token.position : tokenized.expression.length;
      throw new CalculatorExpressionError(code, message, errorPosition);
    }

    function withDepth(parse) {
      depth += 1;
      if (depth > MAX_PARSE_DEPTH) {
        fail("expression-too-deep", "expression is nested too deeply");
      }
      try {
        return parse();
      } finally {
        depth -= 1;
      }
    }

    function peek() {
      return tokens[position];
    }

    function take(type) {
      if (peek()?.type !== type) return null;
      const token = tokens[position];
      position += 1;
      return token;
    }

    function expect(type) {
      const token = take(type);
      if (!token) fail("expected-token", `expected ${type}`);
      return token;
    }

    function parseExpression() {
      let node = parseTerm();
      while (peek()?.type === "+" || peek()?.type === "-") {
        const operator = tokens[position++];
        const right = parseTerm();
        node = Object.freeze({
          type: "binary",
          operator: operator.type,
          left: node,
          right,
          position: operator.position
        });
      }
      return node;
    }

    function parseTerm() {
      let node = options.allowPower ? parsePower() : parseUnary();
      while (
        peek()?.type === "*"
        || peek()?.type === "/"
        || peek()?.type === "%"
      ) {
        const operator = tokens[position++];
        const right = options.allowPower ? parsePower() : parseUnary();
        node = Object.freeze({
          type: "binary",
          operator: operator.type,
          left: node,
          right,
          position: operator.position
        });
      }
      return node;
    }

    function parsePower() {
      const left = parseUnary();
      const operator = take("^");
      if (!operator) return left;
      return Object.freeze({
        type: "binary",
        operator: "^",
        left,
        right: withDepth(parsePower),
        position: operator.position
      });
    }

    function parseUnary() {
      const operator = take("+") || take("-");
      if (!operator) return parsePrimary();
      return Object.freeze({
        type: "unary",
        operator: operator.type,
        argument: withDepth(parseUnary),
        position: operator.position
      });
    }

    function parsePrimary() {
      return withDepth(() => {
        const token = peek();
        if (!token) fail("incomplete-expression", "incomplete expression");

        if (take("number")) {
          return Object.freeze({
            type: "number",
            value: token.value,
            lexeme: token.lexeme,
            position: token.position
          });
        }

        if (token.type === "name" && options.allowNames) {
          position += 1;
          const name = token.value;
          if (name === "x") {
            return Object.freeze({
              type: "variable",
              name,
              position: token.position
            });
          }
          if (name in calculatorGraphConstants) {
            return Object.freeze({
              type: "constant",
              name,
              value: calculatorGraphConstants[name],
              position: token.position
            });
          }

          expect("(");
          const args = [];
          if (!take(")")) {
            do {
              args.push(parseExpression());
            } while (take(","));
            expect(")");
          }
          const arity = calculatorGraphFunctionArity[name];
          if (args.length < arity.min || args.length > arity.max) {
            fail(
              "invalid-arity",
              `${name} expects ${
                arity.max === Number.POSITIVE_INFINITY
                  ? `at least ${arity.min}`
                  : arity.min
              } argument${arity.min === 1 ? "" : "s"}`,
              token
            );
          }
          return Object.freeze({
            type: "call",
            name,
            args: Object.freeze(args),
            position: token.position
          });
        }

        if (take("(")) {
          const node = parseExpression();
          expect(")");
          return node;
        }

        fail(
          "unexpected-token",
          `unexpected token: ${token.value}`,
          token
        );
      });
    }

    const ast = parseExpression();
    if (position !== tokens.length) {
      const token = tokens[position];
      fail(
        "unexpected-token",
        `unexpected token: ${token.value}`,
        token
      );
    }
    return Object.freeze(ast);
  }

  function parseCalculatorArithmeticExpression(rawExpression) {
    const tokenized = tokenizeCalculatorArithmeticExpression(rawExpression);
    const ast = buildParser(tokenized, {
      allowNames: false,
      allowPower: false
    });
    return Object.freeze({
      ...tokenized,
      ast,
      parseStatus: "valid",
      tokenCount: tokenized.tokens.length
    });
  }

  function parseCalculatorGraphExpression(rawExpression) {
    const tokenized = tokenizeCalculatorGraphExpression(rawExpression);
    const ast = buildParser(tokenized, {
      allowNames: true,
      allowPower: true
    });
    return Object.freeze({
      ...tokenized,
      ast,
      parseStatus: "valid",
      tokenCount: tokenized.tokens.length
    });
  }

  function evaluateAst(node, variables) {
    if (node.type === "number" || node.type === "constant") {
      return node.value;
    }
    if (node.type === "variable") {
      return variables[node.name];
    }
    if (node.type === "unary") {
      const value = evaluateAst(node.argument, variables);
      return node.operator === "-" ? -value : value;
    }
    if (node.type === "binary") {
      const left = evaluateAst(node.left, variables);
      const right = evaluateAst(node.right, variables);
      if (node.operator === "+") return left + right;
      if (node.operator === "-") return left - right;
      if (node.operator === "*") return left * right;
      if (node.operator === "/") return left / right;
      if (node.operator === "%") return left % right;
      if (node.operator === "^") return Math.pow(left, right);
    }
    if (node.type === "call") {
      const fn = calculatorGraphFunctions[node.name];
      return fn(...node.args.map((argument) => evaluateAst(argument, variables)));
    }
    throw new CalculatorExpressionError(
      "unsupported-node",
      `unsupported expression node: ${node.type}`,
      node.position
    );
  }

  function invalidEvaluation(error, rawExpression, fallbackMessage) {
    const expression = normalizeCalculatorExpression(
      expressionText(rawExpression).trim()
    ).replace(/\s+/g, "");
    const known = error instanceof CalculatorExpressionError;
    return Object.freeze({
      ok: false,
      expression,
      normalizedExpression: expression,
      rawExpression: expressionText(rawExpression).trim(),
      grammar: ARITHMETIC_GRAMMAR,
      parseStatus: "invalid",
      parserCode: known ? error.code : "expression-invalid",
      errorPosition: known ? error.position : -1,
      error: known ? error.message : fallbackMessage
    });
  }

  function evaluateCalculatorArithmeticExpression(rawExpression) {
    let parsed;
    try {
      parsed = parseCalculatorArithmeticExpression(rawExpression);
      const value = evaluateAst(parsed.ast, Object.freeze({}));
      if (!Number.isFinite(value)) {
        return Object.freeze({
          ok: false,
          expression: parsed.expression,
          normalizedExpression: parsed.normalizedExpression,
          rawExpression: parsed.rawExpression,
          grammar: parsed.grammar,
          parseStatus: "valid",
          parserCode: "result-not-finite",
          errorPosition: -1,
          tokenCount: parsed.tokenCount,
          error: "result is not finite"
        });
      }
      return Object.freeze({
        ok: true,
        expression: parsed.expression,
        normalizedExpression: parsed.normalizedExpression,
        rawExpression: parsed.rawExpression,
        grammar: parsed.grammar,
        parseStatus: parsed.parseStatus,
        parserCode: "",
        errorPosition: -1,
        tokenCount: parsed.tokenCount,
        value
      });
    } catch (error) {
      return invalidEvaluation(error, rawExpression, "check expression");
    }
  }

  function compileGraphExpression(rawExpression) {
    const parsed = parseCalculatorGraphExpression(rawExpression);
    const evaluator = function evaluateGraphAtX(x) {
      const numericX = Number(x);
      if (!Number.isFinite(numericX)) return Number.NaN;
      return evaluateAst(parsed.ast, Object.freeze({x: numericX}));
    };
    Object.defineProperties(evaluator, {
      expression: {value: parsed.expression, enumerable: true},
      normalizedExpression: {
        value: parsed.normalizedExpression,
        enumerable: true
      },
      grammar: {value: parsed.grammar, enumerable: true},
      parseStatus: {value: parsed.parseStatus, enumerable: true},
      tokenCount: {value: parsed.tokenCount, enumerable: true}
    });
    return Object.freeze(evaluator);
  }

  function evaluateGraphExpression(rawExpression, x) {
    const evaluator = compileGraphExpression(rawExpression);
    const value = evaluator(x);
    return Object.freeze({
      ok: Number.isFinite(value),
      expression: evaluator.expression,
      normalizedExpression: evaluator.normalizedExpression,
      grammar: evaluator.grammar,
      parseStatus: evaluator.parseStatus,
      tokenCount: evaluator.tokenCount,
      x: Number(x),
      value,
      error: Number.isFinite(value) ? "" : "result is not finite"
    });
  }

  function normalizeCalculatorGraphRange(rawRange = {}) {
    const range = Object.freeze({
      xMin: Number(rawRange && rawRange.xMin),
      xMax: Number(rawRange && rawRange.xMax),
      yMin: Number(rawRange && rawRange.yMin),
      yMax: Number(rawRange && rawRange.yMax)
    });
    if (!Object.values(range).every(Number.isFinite)) {
      throw new Error("range values must be finite numbers");
    }
    if (range.xMin >= range.xMax) {
      throw new Error("x min must be less than x max");
    }
    if (range.yMin >= range.yMax) {
      throw new Error("y min must be less than y max");
    }
    return range;
  }

  function sampleCalculatorGraphExpression(rawExpression, rawRange, pixelWidth) {
    const evaluator = compileGraphExpression(rawExpression);
    const range = normalizeCalculatorGraphRange(rawRange || {});
    const width = Math.max(1, Math.floor(Number(pixelWidth) || 1));
    const samples = [];
    let finiteCount = 0;
    for (let px = 0; px <= width; px += 1) {
      const x = range.xMin + (px / width) * (range.xMax - range.xMin);
      const y = evaluator(x);
      const visible = Number.isFinite(y) && y >= range.yMin && y <= range.yMax;
      if (visible) finiteCount += 1;
      samples.push(Object.freeze({px, x, y, visible}));
    }
    return Object.freeze({
      ok: true,
      expression: evaluator.expression,
      normalizedExpression: evaluator.normalizedExpression,
      grammar: evaluator.grammar,
      parseStatus: evaluator.parseStatus,
      tokenCount: evaluator.tokenCount,
      range,
      width,
      finiteCount,
      samples: Object.freeze(samples)
    });
  }

  return Object.freeze({
    schema: "main-computer-calculator-core-v1",
    version: "calculator-core-v1",
    arithmeticGrammar: ARITHMETIC_GRAMMAR,
    graphGrammar: GRAPH_GRAMMAR,
    unitGrammar: UNIT_GRAMMAR,
    limits: Object.freeze({
      maxExpressionLength: MAX_EXPRESSION_LENGTH,
      maxTokenCount: MAX_TOKEN_COUNT,
      maxParseDepth: MAX_PARSE_DEPTH
    }),
    graphFunctions: calculatorGraphFunctions,
    graphConstants: calculatorGraphConstants,
    normalizeCalculatorExpression,
    appendCalculatorDisplayToken,
    clearCalculatorDisplayExpression,
    backspaceCalculatorDisplayExpression,
    insertCalculatorGraphText,
    clearCalculatorGraphExpression,
    backspaceCalculatorGraphExpression,
    normalizeGraphExpression,
    extractCalculatorExpression,
    extractCalculatorGraphExpression,
    tokenizeCalculatorArithmeticExpression,
    tokenizeCalculatorGraphExpression,
    parseCalculatorArithmeticExpression,
    parseCalculatorGraphExpression,
    evaluateCalculatorUnitExpression,
    evaluateCalculatorExpression,
    evaluateCalculatorArithmeticExpression,
    compileGraphExpression,
    evaluateGraphExpression,
    normalizeCalculatorGraphRange,
    sampleCalculatorGraphExpression
  });
});
