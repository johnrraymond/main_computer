    function normalizeCalculatorExpression(value) {
      return value.replace(/[xX]/g, "*").replace(/[^\d+\-*/%.() ]/g, "");
    }
    function evaluateCalculatorArithmeticExpression(rawExpression) {
      const expression = normalizeCalculatorExpression(String(rawExpression || "").trim());
      if (!expression) {
        return {ok: false, expression: "", error: "enter an expression"};
      }
      try {
        const value = Function(`"use strict"; return (${expression});`)();
        if (!Number.isFinite(value)) {
          return {ok: false, expression, error: "result is not finite"};
        }
        return {ok: true, expression, value};
      } catch {
        return {ok: false, expression, error: "check expression"};
      }
    }
    function extractCalculatorExpression(modelText) {
      const cleaned = String(modelText || "").replace(/```(?:javascript|js|text)?/gi, "").replace(/```/g, "");
      const candidates = cleaned.match(/[-+*/%().\d xX]+/g) || [];
      const scored = candidates
        .map((candidate) => normalizeCalculatorExpression(candidate).trim())
        .filter((candidate) => candidate.length > 0)
        .sort((left, right) => {
          const leftHasOperator = /[+\-*/%]/.test(left) ? 1 : 0;
          const rightHasOperator = /[+\-*/%]/.test(right) ? 1 : 0;
          return rightHasOperator - leftHasOperator || right.length - left.length;
        });
      return scored[0] || normalizeCalculatorExpression(cleaned).trim();
    }
    function extractCalculatorGraphExpression(modelText) {
      const cleaned = String(modelText || "")
        .replace(/```(?:javascript|js|text|math)?/gi, "")
        .replace(/```/g, "")
        .replace(/\bf\s*\(\s*x\s*\)\s*=/gi, "")
        .replace(/\by\s*=/gi, "")
        .toLowerCase();
      const allowedNames = Object.keys(calculatorGraphFunctions).concat(Object.keys(calculatorGraphConstants), ["x"]).join("|");
      const candidatePattern = new RegExp(`(?:${allowedNames}|[0-9.e+\\-*/%^(),\\s])+`, "g");
      const candidates = cleaned.match(candidatePattern) || [];
      const scored = candidates
        .map((candidate) => normalizeGraphExpression(candidate))
        .filter((candidate) => candidate && /^[a-z0-9+\-*/%^(),.]+$/.test(candidate))
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
    function calculateExpression() {
      const result = evaluateCalculatorArithmeticExpression(calculatorDisplay.value);
      if (!result.expression) {
        calculatorDisplay.value = "0";
        calculatorResult.textContent = "ready";
        return;
      }
      if (result.ok) {
        calculatorResult.textContent = String(result.value);
        calculatorDisplay.value = String(result.value);
      } else {
        calculatorResult.textContent = result.error || "check expression";
      }
    }
    const calculatorGraphFunctions = {
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
    };
    const calculatorGraphConstants = {pi: Math.PI, e: Math.E};
    let calculatorEmbeddedChatController = null;

    function calculatorEmbeddedChatContextSnapshot() {
      const graphing = calculatorModeGraphing?.classList?.contains("active") || false;
      return {
        app: "calculator",
        target_kind: "calculator-session",
        target_id: "calculator",
        active_mode: graphing ? "scientific-graphing" : "basic",
        arithmetic: {
          expression: calculatorDisplay?.value || "",
          result: calculatorResult?.textContent || "",
          prompt: calculatorPrompt?.value || ""
        },
        graph: {
          expression: calculatorGraphExpression?.value || "",
          x_min: calculatorGraphXMin?.value || "",
          x_max: calculatorGraphXMax?.value || "",
          y_min: calculatorGraphYMin?.value || "",
          y_max: calculatorGraphYMax?.value || "",
          status: calculatorGraphStatus?.textContent || ""
        },
        mathics: {
          prompt: calculatorMathicsPrompt?.value || "",
          expression: calculatorMathicsExpression?.value || "",
          status: calculatorMathicsEvaluationStatus?.textContent || ""
        },
        qa: {
          prompt: calculatorQaPrompt?.value || "",
          status: calculatorQaStatus?.textContent || ""
        },
        allowed_tools: ["arithmetic", "scientific-graphing", "mathics", "calculator-qa"]
      };
    }

    window.MainComputerCalculatorContext = {
      snapshot: calculatorEmbeddedChatContextSnapshot
    };

    function mountCalculatorEmbeddedChat() {
      if (!calculatorChatPanel) return null;
      if (calculatorEmbeddedChatController) return calculatorEmbeddedChatController;
      const api = window.MainComputerChatConsole || {};
      const mount = api.mountEmbedded || window.chatConsoleMountEmbedded;
      if (!mount) {
        if (typeof initChatConsoleApp === "function") initChatConsoleApp();
        if (typeof renderChatConsoleNotebook === "function") renderChatConsoleNotebook();
        return null;
      }
      calculatorEmbeddedChatController = mount(calculatorChatPanel, {
        embedId: "calculator",
        activeApp: "calculator",
        idPrefix: "calculator-chat",
        classPrefix: "calculator",
        title: "Calculator Chat",
        subtitle: "Embedded beside calculator tools with expression, graph, Mathics, and Q&A context.",
        notebookId: "calculator-chat-notebook",
        statusId: "calculator-chat-status",
        threadTitle: "Calculator Chat",
        targetKind: "calculator-session",
        targetId: "calculator",
        layout: "compact",
        showThreadRail: false,
        showCurrentThreadBar: true,
        getEmbeddedContext: calculatorEmbeddedChatContextSnapshot,
        buildThreadMetadata(context) {
          return {
            origin_app: "calculator",
            embedded_chat: true,
            linked_targets: [{
              app: "calculator",
              kind: "calculator-session",
              id: "calculator",
              path: "applications/calculator"
            }],
            calculator_active_mode: context?.active_mode || "basic"
          };
        },
        status(message) {
          if (calculatorModelStatus && message) calculatorModelStatus.dataset.chatStatus = message;
        }
      });
      return calculatorEmbeddedChatController;
    }

    function setCalculatorMode(mode) {
      const graphing = mode === "graphing";
      calculatorModeBasic.classList.toggle("active", !graphing);
      calculatorModeGraphing.classList.toggle("active", graphing);
      calculatorShell.classList.toggle("graphing-active", graphing);
      calculatorShell.classList.add("chat-docked");
      calculatorShell.classList.remove("chat-active");
      calculatorBasicPanel.hidden = false;
      calculatorGraphingPanel.hidden = !graphing;
      calculatorMathicsPanel.hidden = !graphing;
      if (calculatorChatPanel) calculatorChatPanel.hidden = false;
      calculatorResult.textContent = "ready";
      if (calculatorChatPanel) {
        mountCalculatorEmbeddedChat();
      }
      if (graphing) {
        calculatorGraphExpression.focus();
        setTimeout(drawCalculatorGraph, 0);
      } else {
        calculatorDisplay.focus();
      }
    }

    function normalizeGraphExpression(value) {
      return String(value || "").trim().replace(/\s+/g, "").toLowerCase();
    }
    function tokenizeCalculatorGraphExpression(expression) {
      const tokens = [];
      let index = 0;
      while (index < expression.length) {
        const char = expression[index];
        if (/\d|\./.test(char)) {
          const match = expression.slice(index).match(/^(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?/i);
          if (!match) throw new Error("unsupported number");
          tokens.push({type: "number", value: Number(match[0])});
          index += match[0].length;
        } else if (/[a-z]/.test(char)) {
          const match = expression.slice(index).match(/^[a-z]+/);
          const name = match[0];
          if (name !== "x" && !(name in calculatorGraphConstants) && !(name in calculatorGraphFunctions)) {
            throw new Error(`unsupported token: ${name}`);
          }
          tokens.push({type: "name", value: name});
          index += name.length;
        } else if ("+-*/%^(),".includes(char)) {
          tokens.push({type: char, value: char});
          index += 1;
        } else {
          throw new Error(`unsupported token: ${char}`);
        }
      }
      return tokens;
    }
    function compileGraphExpression(rawExpression) {
      const expression = normalizeGraphExpression(rawExpression);
      if (!expression) throw new Error("enter f(x) before graphing");
      const tokens = tokenizeCalculatorGraphExpression(expression);
      let position = 0;
      const peek = () => tokens[position];
      const take = (type) => {
        if (peek()?.type === type) {
          position += 1;
          return true;
        }
        return false;
      };
      const expect = (type) => {
        if (!take(type)) throw new Error(`expected ${type}`);
      };
      function parseExpression() {
        let node = parseTerm();
        while (peek()?.type === "+" || peek()?.type === "-") {
          const op = tokens[position++].type;
          const right = parseTerm();
          const left = node;
          node = (x) => op === "+" ? left(x) + right(x) : left(x) - right(x);
        }
        return node;
      }
      function parseTerm() {
        let node = parsePower();
        while (peek()?.type === "*" || peek()?.type === "/" || peek()?.type === "%") {
          const op = tokens[position++].type;
          const right = parsePower();
          const left = node;
          node = (x) => op === "*" ? left(x) * right(x) : op === "/" ? left(x) / right(x) : left(x) % right(x);
        }
        return node;
      }
      function parsePower() {
        const left = parseUnary();
        if (take("^")) {
          const right = parsePower();
          return (x) => Math.pow(left(x), right(x));
        }
        return left;
      }
      function parseUnary() {
        if (take("+")) return parseUnary();
        if (take("-")) {
          const node = parseUnary();
          return (x) => -node(x);
        }
        return parsePrimary();
      }
      function parsePrimary() {
        const token = peek();
        if (!token) throw new Error("incomplete expression");
        if (take("number")) {
          const value = token.value;
          if (!Number.isFinite(value)) throw new Error("invalid number");
          return () => value;
        }
        if (token.type === "name") {
          position += 1;
          const name = token.value;
          if (name === "x") return (x) => x;
          if (name in calculatorGraphConstants) return () => calculatorGraphConstants[name];
          expect("(");
          const args = [];
          if (!take(")")) {
            do {
              args.push(parseExpression());
            } while (take(","));
            expect(")");
          }
          const fn = calculatorGraphFunctions[name];
          return (x) => fn(...args.map((arg) => arg(x)));
        }
        if (take("(")) {
          const node = parseExpression();
          expect(")");
          return node;
        }
        throw new Error(`unexpected token: ${token.value}`);
      }
      const evaluator = parseExpression();
      if (position !== tokens.length) throw new Error(`unexpected token: ${tokens[position].value}`);
      return evaluator;
    }
    function parseGraphRange() {
      const range = {
        xMin: Number(calculatorGraphXMin.value),
        xMax: Number(calculatorGraphXMax.value),
        yMin: Number(calculatorGraphYMin.value),
        yMax: Number(calculatorGraphYMax.value)
      };
      if (!Object.values(range).every(Number.isFinite)) throw new Error("range values must be finite numbers");
      if (range.xMin >= range.xMax) throw new Error("x min must be less than x max");
      if (range.yMin >= range.yMax) throw new Error("y min must be less than y max");
      return range;
    }
    function drawCalculatorGraph() {
      const canvasContext = calculatorGraphCanvas.getContext("2d");
      const rect = calculatorGraphCanvas.getBoundingClientRect();
      const pixelRatio = window.devicePixelRatio || 1;
      const width = Math.max(320, Math.floor(rect.width || calculatorGraphCanvas.clientWidth || 720));
      const height = Math.max(260, Math.floor(rect.height || 320));
      calculatorGraphCanvas.width = Math.floor(width * pixelRatio);
      calculatorGraphCanvas.height = Math.floor(height * pixelRatio);
      canvasContext.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      canvasContext.clearRect(0, 0, width, height);
      canvasContext.fillStyle = "#010201";
      canvasContext.fillRect(0, 0, width, height);
      try {
        const evaluator = compileGraphExpression(calculatorGraphExpression.value);
        const range = parseGraphRange();
        const toPx = (x) => (x - range.xMin) / (range.xMax - range.xMin) * width;
        const toPy = (y) => height - (y - range.yMin) / (range.yMax - range.yMin) * height;
        canvasContext.lineWidth = 1;
        canvasContext.strokeStyle = "#26291e";
        canvasContext.beginPath();
        const xStep = (range.xMax - range.xMin) / 10;
        const yStep = (range.yMax - range.yMin) / 10;
        for (let i = 0; i <= 10; i += 1) {
          const x = toPx(range.xMin + xStep * i);
          canvasContext.moveTo(x, 0);
          canvasContext.lineTo(x, height);
          const y = toPy(range.yMin + yStep * i);
          canvasContext.moveTo(0, y);
          canvasContext.lineTo(width, y);
        }
        canvasContext.stroke();
        canvasContext.strokeStyle = "#4f493a";
        canvasContext.beginPath();
        if (range.xMin <= 0 && range.xMax >= 0) {
          const axisX = toPx(0);
          canvasContext.moveTo(axisX, 0);
          canvasContext.lineTo(axisX, height);
        }
        if (range.yMin <= 0 && range.yMax >= 0) {
          const axisY = toPy(0);
          canvasContext.moveTo(0, axisY);
          canvasContext.lineTo(width, axisY);
        }
        canvasContext.stroke();
        canvasContext.lineWidth = 2;
        canvasContext.strokeStyle = "#a7d86d";
        canvasContext.beginPath();
        let hasPoint = false;
        let finiteCount = 0;
        for (let px = 0; px <= width; px += 1) {
          const x = range.xMin + (px / width) * (range.xMax - range.xMin);
          const y = evaluator(x);
          if (!Number.isFinite(y) || y < range.yMin || y > range.yMax) {
            hasPoint = false;
            continue;
          }
          const py = toPy(y);
          if (hasPoint) canvasContext.lineTo(px, py);
          else canvasContext.moveTo(px, py);
          hasPoint = true;
          finiteCount += 1;
        }
        canvasContext.stroke();
        calculatorGraphStatus.textContent = `graphed ${normalizeGraphExpression(calculatorGraphExpression.value)} | ${finiteCount} visible samples`;
      } catch (error) {
        canvasContext.fillStyle = "#ff8f70";
        canvasContext.font = "700 14px Arial, Helvetica, sans-serif";
        canvasContext.fillText("Graph error", 14, 28);
        calculatorGraphStatus.textContent = `graph error: ${error.message || error}`;
      }
    }
    function resetCalculatorGraphView() {
      calculatorGraphXMin.value = "-10";
      calculatorGraphXMax.value = "10";
      calculatorGraphYMin.value = "-5";
      calculatorGraphYMax.value = "5";
      drawCalculatorGraph();
    }
    async function askCalculatorModel() {
      const problem = calculatorPrompt.value.trim();
      if (!problem) {
        calculatorModelStatus.textContent = "describe a problem first";
        calculatorPrompt.focus();
        return;
      }
      calculatorAskModel.disabled = true;
      calculatorModelStatus.textContent = "asking model";
      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            prompt: [
              "Translate this calculator word problem into one plain expression.",
              "For normal arithmetic, return only a basic arithmetic expression using digits, parentheses, decimal points, +, -, *, /, and %.",
              "Only return a graph expression using x when the user clearly asks for a graph or f(x).",
              "Do not solve or explain in prose.",
              `Problem: ${problem}`
            ].join("\n")
          })
        });
        if (!response.ok) {
          throw new Error(`model returned ${response.status}`);
        }
        const data = await response.json();
        const expression = extractCalculatorExpression(data.content || "");
        if (!expression) {
          throw new Error("no expression returned");
        }
        calculatorDisplay.value = expression;
        calculatorModelStatus.textContent = `model expression: ${expression}`;
        calculateExpression();
      } catch (error) {
        calculatorModelStatus.textContent = error.message || "model prompt failed";
      } finally {
        calculatorAskModel.disabled = false;
      }
    }
    function setCalculatorMathicsOutput(text, state = "ready") {
      calculatorMathicsOutput.textContent = text || "";
      calculatorMathicsOutput.classList.toggle("error", state === "error");
    }
    async function askCalculatorMathicsModel() {
      const prompt = calculatorMathicsPrompt.value.trim();
      if (!prompt) {
        calculatorMathicsModelStatus.textContent = "describe a symbolic request first";
        calculatorMathicsPrompt.focus();
        return;
      }
      calculatorMathicsAskModel.disabled = true;
      calculatorMathicsModelStatus.textContent = "asking model";
      try {
        const response = await fetch("/api/applications/calculator/mathics/ask", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({prompt})
        });
        const data = await response.json();
        if (!response.ok || data.ok === false) {
          throw new Error(data.error || `model returned ${response.status}`);
        }
        calculatorMathicsExpression.value = data.expression || "";
        calculatorMathicsModelStatus.textContent = `mathics expression: ${data.expression || ""}`;
        calculatorMathicsExpression.focus();
      } catch (error) {
        calculatorMathicsModelStatus.textContent = error.message || "mathics model prompt failed";
      } finally {
        calculatorMathicsAskModel.disabled = false;
      }
    }
    async function evaluateCalculatorMathics() {
      const expression = calculatorMathicsExpression.value.trim();
      if (!expression) {
        calculatorMathicsModelStatus.textContent = "enter a Mathics expression first";
        calculatorMathicsExpression.focus();
        return;
      }
      calculatorMathicsEvaluate.disabled = true;
      calculatorMathicsEvaluationStatus.textContent = "evaluating Mathics expression";
      setCalculatorMathicsOutput("Evaluating...", "ready");
      try {
        const response = await fetch("/api/applications/calculator/mathics/evaluate", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({expression})
        });
        const data = await response.json();
        if (!response.ok || data.ok === false) {
          const detail = data.detail ? ` ${data.detail}` : "";
          throw new Error(`${data.error || `Mathics returned ${response.status}`}${detail}`);
        }
        calculatorMathicsEvaluationStatus.textContent = "Mathics result ready";
        setCalculatorMathicsOutput(data.result_text || "(no result)", "ready");
      } catch (error) {
        calculatorMathicsEvaluationStatus.textContent = error.message || "Mathics evaluation failed";
        setCalculatorMathicsOutput(error.message || "Mathics evaluation failed", "error");
      } finally {
        calculatorMathicsEvaluate.disabled = false;
      }
    }
    function clearCalculatorMathics() {
      calculatorMathicsExpression.value = "";
      setCalculatorMathicsOutput("Mathics ready.", "ready");
      calculatorMathicsEvaluationStatus.textContent = "mathics evaluation ready";
      calculatorMathicsExpression.focus();
    }
    function calculatorQaContext() {
      return {
        basic_expression: calculatorDisplay?.value || "",
        basic_result: calculatorResult?.textContent || "",
        graph_expression: calculatorGraphExpression?.value || "",
        graph_status: calculatorGraphStatus?.textContent || "",
        graph_range: {
          x_min: calculatorGraphXMin?.value || "",
          x_max: calculatorGraphXMax?.value || "",
          y_min: calculatorGraphYMin?.value || "",
          y_max: calculatorGraphYMax?.value || ""
        },
        mathics_expression: calculatorMathicsExpression?.value || "",
        mathics_output: calculatorMathicsOutput?.textContent || ""
      };
    }
    function setCalculatorQaAnswer(text, state = "ready") {
      if (!calculatorQaAnswer) return;
      calculatorQaAnswer.textContent = text || "";
      calculatorQaAnswer.classList.toggle("error", state === "error");
    }
    async function askCalculatorQa() {
      if (!calculatorQaPrompt || !calculatorQaAsk || !calculatorQaStatus) return;
      const question = calculatorQaPrompt.value.trim();
      if (!question) {
        calculatorQaStatus.textContent = "ask a question first";
        calculatorQaPrompt.focus();
        return;
      }
      calculatorQaAsk.disabled = true;
      calculatorQaStatus.textContent = "asking model about results";
      setCalculatorQaAnswer("Asking about the current calculator context...", "ready");
      try {
        const response = await fetch("/api/applications/calculator/qa", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({question, context: calculatorQaContext()})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) {
          throw new Error(data.error || `calculator Q&A returned ${response.status}`);
        }
        calculatorQaStatus.textContent = "result Q&A answered";
        setCalculatorQaAnswer(data.answer || "(no answer returned)", "ready");
      } catch (error) {
        const message = error.message || "calculator Q&A failed";
        calculatorQaStatus.textContent = message;
        setCalculatorQaAnswer(message, "error");
      } finally {
        calculatorQaAsk.disabled = false;
      }
    }
    document.querySelectorAll("[data-calc-key]").forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.dataset.calcKey;
        if (calculatorDisplay.value === "0" && /\d/.test(key)) {
          calculatorDisplay.value = key;
        } else {
          calculatorDisplay.value += key;
        }
        calculatorResult.textContent = "ready";
        calculatorDisplay.focus();
      });
    });
    document.querySelectorAll("[data-calc-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.calcAction;
        if (action === "clear") {
          calculatorDisplay.value = "0";
          calculatorResult.textContent = "ready";
        } else if (action === "backspace") {
          calculatorDisplay.value = calculatorDisplay.value.slice(0, -1) || "0";
          calculatorResult.textContent = "ready";
        } else if (action === "equals") {
          calculateExpression();
        }
        calculatorDisplay.focus();
      });
    });
    calculatorDisplay.addEventListener("input", () => {
      calculatorDisplay.value = normalizeCalculatorExpression(calculatorDisplay.value);
    });
    calculatorDisplay.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        calculateExpression();
      }
    });
    calculatorModeBasic.addEventListener("click", () => setCalculatorMode("basic"));
    calculatorModeGraphing.addEventListener("click", () => setCalculatorMode("graphing"));
    calculatorGraphDraw.addEventListener("click", drawCalculatorGraph);
    calculatorGraphReset.addEventListener("click", resetCalculatorGraphView);
    calculatorGraphExpression.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        drawCalculatorGraph();
      }
    });
    calculatorMathicsAskModel.addEventListener("click", askCalculatorMathicsModel);
    calculatorMathicsEvaluate.addEventListener("click", evaluateCalculatorMathics);
    calculatorMathicsClear.addEventListener("click", clearCalculatorMathics);
    calculatorMathicsPrompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        askCalculatorMathicsModel();
      }
    });
    calculatorMathicsExpression.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        evaluateCalculatorMathics();
      }
    });
    document.querySelectorAll("[data-mathics-example]").forEach((button) => {
      button.addEventListener("click", () => {
        calculatorMathicsExpression.value = button.dataset.mathicsExample || "";
        calculatorMathicsModelStatus.textContent = "example loaded";
        calculatorMathicsExpression.focus();
      });
    });
    document.querySelectorAll("[data-calc-graph-token]").forEach((button) => {
      button.addEventListener("click", () => {
        insertCalculatorGraphText(button.dataset.calcGraphToken || "");
      });
    });
    document.querySelectorAll("[data-calc-graph-template]").forEach((button) => {
      button.addEventListener("click", () => {
        const template = button.dataset.calcGraphTemplate || "";
        insertCalculatorGraphText(template, template.endsWith("()") ? 1 : 2);
      });
    });
    document.querySelectorAll("[data-calc-graph-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.calcGraphAction;
        if (action === "clear") {
          calculatorGraphExpression.value = "";
        } else if (action === "backspace") {
          const start = calculatorGraphExpression.selectionStart ?? calculatorGraphExpression.value.length;
          const end = calculatorGraphExpression.selectionEnd ?? calculatorGraphExpression.value.length;
          if (start !== end) {
            calculatorGraphExpression.value = calculatorGraphExpression.value.slice(0, start) + calculatorGraphExpression.value.slice(end);
            calculatorGraphExpression.setSelectionRange(start, start);
          } else if (start > 0) {
            calculatorGraphExpression.value = calculatorGraphExpression.value.slice(0, start - 1) + calculatorGraphExpression.value.slice(start);
            calculatorGraphExpression.setSelectionRange(start - 1, start - 1);
          }
        }
        calculatorGraphExpression.focus();
      });
    });
    calculatorAskModel.addEventListener("click", askCalculatorModel);
    calculatorScientificAskModel.addEventListener("click", () => askScientificCalculatorModel());
    calculatorQaAsk?.addEventListener("click", askCalculatorQa);
    calculatorPrompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        askCalculatorModel();
      }
    });
    calculatorScientificPrompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        askScientificCalculatorModel();
      }
    });
    calculatorQaPrompt?.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        askCalculatorQa();
      }
    });
    async function askScientificCalculatorModel(options = {}) {
      const problem = (calculatorScientificPrompt.value.trim() || (options.useTopPrompt ? calculatorPrompt.value.trim() : ""));
      if (!problem) {
        calculatorScientificModelStatus.textContent = "describe a graph first";
        calculatorScientificPrompt.focus();
        return;
      }
      calculatorScientificAskModel.disabled = true;
      calculatorScientificModelStatus.textContent = "asking model";
      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            prompt: [
              "Translate this graphing calculator request into one plain f(x) expression.",
              "Return only the expression, with no prose.",
              "Allowed tokens are x, digits, parentheses, commas, decimal points, +, -, *, /, %, ^, pi, e, sin, cos, tan, asin, acos, atan, sqrt, abs, log, ln, exp, floor, ceil, round, min, and max.",
              "Preserve x as the variable. Do not convert x to multiplication.",
              "Strip prefixes such as f(x)= or y= from your final answer.",
              `Request: ${problem}`
            ].join()
          })
        });
        if (!response.ok) {
          throw new Error(`model returned ${response.status}`);
        }
        const data = await response.json();
        const expression = extractCalculatorGraphExpression(data.content || "");
        if (!expression) {
          throw new Error("no graph expression returned");
        }
        calculatorGraphExpression.value = expression;
        calculatorScientificModelStatus.textContent = `f(x): ${expression}`;
        drawCalculatorGraph();
      } catch (error) {
        calculatorScientificModelStatus.textContent = error.message || "scientific model prompt failed";
      } finally {
        calculatorScientificAskModel.disabled = false;
      }
    }
    function insertCalculatorGraphText(text, caretBack = 0) {
      const start = calculatorGraphExpression.selectionStart ?? calculatorGraphExpression.value.length;
      const end = calculatorGraphExpression.selectionEnd ?? calculatorGraphExpression.value.length;
      const before = calculatorGraphExpression.value.slice(0, start);
      const after = calculatorGraphExpression.value.slice(end);
      calculatorGraphExpression.value = `${before}${text}${after}`;
      const caret = start + text.length - caretBack;
      calculatorGraphExpression.focus();
      calculatorGraphExpression.setSelectionRange(caret, caret);
    }
