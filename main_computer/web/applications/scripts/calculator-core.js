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

  function expressionText(value) {
    return String(value == null ? "" : value);
  }

  function normalizeCalculatorExpression(value) {
    return expressionText(value)
      .replace(/[xX]/g, "*")
      .replace(/[^\d+\-*/%.() ]/g, "");
  }

  function normalizeGraphExpression(value) {
    return expressionText(value).trim().replace(/\s+/g, "").toLowerCase();
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

  return Object.freeze({
    schema: "main-computer-calculator-core-v1",
    version: "calculator-core-v1",
    arithmeticGrammar: ARITHMETIC_GRAMMAR,
    graphGrammar: GRAPH_GRAMMAR,
    limits: Object.freeze({
      maxExpressionLength: MAX_EXPRESSION_LENGTH,
      maxTokenCount: MAX_TOKEN_COUNT,
      maxParseDepth: MAX_PARSE_DEPTH
    }),
    graphFunctions: calculatorGraphFunctions,
    graphConstants: calculatorGraphConstants,
    normalizeCalculatorExpression,
    normalizeGraphExpression,
    tokenizeCalculatorArithmeticExpression,
    tokenizeCalculatorGraphExpression,
    parseCalculatorArithmeticExpression,
    parseCalculatorGraphExpression,
    evaluateCalculatorArithmeticExpression,
    compileGraphExpression,
    evaluateGraphExpression
  });
});
