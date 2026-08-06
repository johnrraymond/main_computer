    (function installCalculatorCapabilities(global) {
      "use strict";

      const DEFAULT_HEADERS = Object.freeze({"Content-Type": "application/json"});

      function requireFetch(fetcher) {
        const resolved = fetcher || global.fetch;
        if (typeof resolved !== "function") {
          throw new Error("Calculator capability transport is unavailable");
        }
        return resolved.bind(global);
      }

      async function requestJson(path, payload, options = {}) {
        const fetcher = requireFetch(options.fetcher);
        const response = await fetcher(path, {
          method: "POST",
          headers: DEFAULT_HEADERS,
          body: JSON.stringify(payload || {})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) {
          const detail = data.detail ? ` ${data.detail}` : "";
          throw new Error(`${data.error || `request returned ${response.status}`}${detail}`);
        }
        return data;
      }

      function arithmeticModelPrompt(problem) {
        return [
          "Translate this calculator word problem into one plain expression.",
          "For normal arithmetic, return only a basic arithmetic expression using digits, parentheses, decimal points, +, -, *, /, and %.",
          "Only return a graph expression using x when the user clearly asks for a graph or f(x).",
          "Do not solve or explain in prose.",
          `Problem: ${problem}`
        ].join("\n");
      }

      function graphModelPrompt(problem) {
        return [
          "Translate this graphing calculator request into one plain f(x) expression.",
          "Return only the expression, with no prose.",
          "Allowed tokens are x, digits, parentheses, commas, decimal points, +, -, *, /, %, ^, pi, e, sin, cos, tan, asin, acos, atan, sqrt, abs, log, ln, exp, floor, ceil, round, min, and max.",
          "Preserve x as the variable. Do not convert x to multiplication.",
          "Strip prefixes such as f(x)= or y= from your final answer.",
          `Request: ${problem}`
        ].join("\n");
      }

      async function askArithmeticModel(problem, options = {}) {
        const data = await requestJson("/api/chat", {prompt: arithmeticModelPrompt(problem)}, options);
        return {ok: true, content: data.content || "", raw: data};
      }

      async function askGraphModel(problem, options = {}) {
        const data = await requestJson("/api/chat", {prompt: graphModelPrompt(problem)}, options);
        return {ok: true, content: data.content || "", raw: data};
      }

      async function askMathicsModel(prompt, options = {}) {
        const data = await requestJson("/api/applications/calculator/mathics/ask", {prompt}, options);
        return {ok: true, expression: data.expression || "", raw: data};
      }

      async function evaluateMathics(expression, options = {}) {
        const data = await requestJson("/api/applications/calculator/mathics/evaluate", {expression}, options);
        return {ok: true, output: data.result_text || "(no result)", raw: data};
      }

      async function askResultQuestion(question, context, options = {}) {
        const data = await requestJson("/api/applications/calculator/qa", {question, context}, options);
        return {ok: true, answer: data.answer || "(no answer returned)", raw: data};
      }

      const api = Object.freeze({
        schema: "main-computer-calculator-capabilities-v1",
        arithmeticModelPrompt,
        graphModelPrompt,
        askArithmeticModel,
        askGraphModel,
        askMathicsModel,
        evaluateMathics,
        askResultQuestion,
        requestJson
      });

      global.MainComputerCalculatorCapabilities = api;
      if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
      }
    })(typeof globalThis !== "undefined" ? globalThis : window);
