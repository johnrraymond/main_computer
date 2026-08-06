    function requireCalculatorCore() {
      const core = window.MainComputerCalculatorCore;
      if (!core) {
        throw new Error("Calculator core is unavailable; reload the Applications viewport");
      }
      return core;
    }

    function normalizeCalculatorExpression(value) {
      return requireCalculatorCore().normalizeCalculatorExpression(value);
    }

    function evaluateCalculatorArithmeticExpression(rawExpression) {
      return requireCalculatorCore().evaluateCalculatorArithmeticExpression(rawExpression);
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
      const core = requireCalculatorCore();
      const allowedNames = Object.keys(core.graphFunctions).concat(Object.keys(core.graphConstants), ["x"]).join("|");
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
        return {ok: false, expression: "", result: "ready", code: "expression-required", error: "enter an expression"};
      }
      if (result.ok) {
        calculatorResult.textContent = String(result.value);
        calculatorDisplay.value = String(result.value);
      } else {
        calculatorResult.textContent = result.error || "check expression";
      }
      return {
        ...result,
        result: calculatorResult.textContent,
        statusText: result.ok ? "ready" : "error",
        code: result.ok ? "" : "expression-invalid"
      };
    }
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
      return requireCalculatorCore().normalizeGraphExpression(value);
    }

    function tokenizeCalculatorGraphExpression(expression) {
      return requireCalculatorCore().tokenizeCalculatorGraphExpression(expression).tokens;
    }

    function compileGraphExpression(rawExpression) {
      return requireCalculatorCore().compileGraphExpression(rawExpression);
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
        return {
          ok: true,
          expression: normalizeGraphExpression(calculatorGraphExpression.value),
          range,
          finiteCount,
          statusText: calculatorGraphStatus.textContent
        };
      } catch (error) {
        canvasContext.fillStyle = "#ff8f70";
        canvasContext.font = "700 14px Arial, Helvetica, sans-serif";
        canvasContext.fillText("Graph error", 14, 28);
        calculatorGraphStatus.textContent = `graph error: ${error.message || error}`;
        return {
          ok: false,
          expression: normalizeGraphExpression(calculatorGraphExpression.value),
          range: {
            xMin: Number(calculatorGraphXMin.value),
            xMax: Number(calculatorGraphXMax.value),
            yMin: Number(calculatorGraphYMin.value),
            yMax: Number(calculatorGraphYMax.value)
          },
          statusText: calculatorGraphStatus.textContent,
          code: /range/i.test(error.message || "") ? "graph-range-invalid" : "graph-expression-required",
          error: error.message || String(error)
        };
      }
    }
    function resetCalculatorGraphView() {
      calculatorGraphXMin.value = "-10";
      calculatorGraphXMax.value = "10";
      calculatorGraphYMin.value = "-5";
      calculatorGraphYMax.value = "5";
      return drawCalculatorGraph();
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
        const evaluation = calculateExpression();
        return {
          ok: true,
          expression,
          result: calculatorResult.textContent,
          evaluation
        };
      } catch (error) {
        calculatorModelStatus.textContent = error.message || "model prompt failed";
        return {ok: false, code: "provider-request-failed", error: error.message || "model prompt failed"};
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
        return {ok: true, expression: data.expression || ""};
      } catch (error) {
        calculatorMathicsModelStatus.textContent = error.message || "mathics model prompt failed";
        return {ok: false, code: "provider-request-failed", error: error.message || "mathics model prompt failed"};
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
        return {
          ok: true,
          expression,
          output: data.result_text || "(no result)",
          statusText: "ready"
        };
      } catch (error) {
        calculatorMathicsEvaluationStatus.textContent = error.message || "Mathics evaluation failed";
        setCalculatorMathicsOutput(error.message || "Mathics evaluation failed", "error");
        return {
          ok: false,
          expression,
          code: "mathics-evaluation-failed",
          error: error.message || "Mathics evaluation failed",
          statusText: "error"
        };
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
        return {
          ok: true,
          question,
          answer: data.answer || "(no answer returned)",
          statusText: "ready"
        };
      } catch (error) {
        const message = error.message || "calculator Q&A failed";
        calculatorQaStatus.textContent = message;
        setCalculatorQaAnswer(message, "error");
        return {ok: false, question, code: "result-qa-failed", error: message, statusText: "error"};
      } finally {
        calculatorQaAsk.disabled = false;
      }
    }

    function calculatorSemanticAdapter() {
      const hostRuntime = window.McelHostBoundApplicationRuntime;
      const mount = hostRuntime && typeof hostRuntime.getMount === "function"
        ? hostRuntime.getMount("calculator")
        : null;
      if (!mount || mount.active !== true || typeof mount.invoke !== "function") {
        return null;
      }
      return {
        executeIntent(intentId, payload) {
          return Promise.resolve(mount.invoke(intentId, payload || {})).then((result) => {
            if (result && typeof result === "object" && Object.prototype.hasOwnProperty.call(result, "ok")) {
              return result;
            }
            return {ok: true, status: "pass", result};
          });
        }
      };
    }

    function executeCalculatorSemanticIntent(intentId, payload, fallback) {
      const adapter = calculatorSemanticAdapter();
      if (!adapter || typeof adapter.executeIntent !== "function") {
        try {
          return Promise.resolve(typeof fallback === "function" ? fallback() : null);
        } catch (error) {
          return Promise.reject(error);
        }
      }
      return adapter.executeIntent(intentId, payload || {}).then((execution) => {
        if (!execution?.ok && execution?.failure) {
          console.warn(
            `Calculator semantic intent ${intentId} ${execution.status}:`,
            execution.failure.message || execution.failure.failureClass
          );
        }
        return execution;
      });
    }

    function applyCalculatorToken(key) {
      if (calculatorDisplay.value === "0" && /\d/.test(key)) {
        calculatorDisplay.value = key;
      } else {
        calculatorDisplay.value += key;
      }
      calculatorResult.textContent = "ready";
      calculatorDisplay.focus();
      return {
        ok: true,
        expression: calculatorDisplay.value,
        result: calculatorResult.textContent,
        statusText: "ready"
      };
    }

    function clearCalculatorExpression() {
      calculatorDisplay.value = "0";
      calculatorResult.textContent = "ready";
      calculatorDisplay.focus();
      return {
        ok: true,
        expression: calculatorDisplay.value,
        result: calculatorResult.textContent,
        statusText: "ready"
      };
    }

    function calculatorGraphIntentPayload() {
      return {
        expression: calculatorGraphExpression.value,
        range: {
          xMin: Number(calculatorGraphXMin.value),
          xMax: Number(calculatorGraphXMax.value),
          yMin: Number(calculatorGraphYMin.value),
          yMax: Number(calculatorGraphYMax.value)
        }
      };
    }

    window.MainComputerCalculatorRuntime = Object.freeze({
      snapshot: calculatorEmbeddedChatContextSnapshot,
      switchMode({mode} = {}) {
        setCalculatorMode(mode);
        return {
          ok: true,
          mode,
          expression: calculatorDisplay.value,
          graphExpression: calculatorGraphExpression.value
        };
      },
      enterToken({token} = {}) {
        return applyCalculatorToken(String(token || ""));
      },
      clearExpression() {
        return clearCalculatorExpression();
      },
      evaluateExpression({expression} = {}) {
        if (typeof expression === "string") calculatorDisplay.value = expression;
        return calculateExpression();
      },
      drawGraph({expression, range} = {}) {
        if (typeof expression === "string") calculatorGraphExpression.value = expression;
        if (range && typeof range === "object") {
          if (Number.isFinite(Number(range.xMin))) calculatorGraphXMin.value = String(range.xMin);
          if (Number.isFinite(Number(range.xMax))) calculatorGraphXMax.value = String(range.xMax);
          if (Number.isFinite(Number(range.yMin))) calculatorGraphYMin.value = String(range.yMin);
          if (Number.isFinite(Number(range.yMax))) calculatorGraphYMax.value = String(range.yMax);
        }
        return drawCalculatorGraph();
      },
      resetGraph() {
        return resetCalculatorGraphView();
      },
      async askModelForExpression({prompt} = {}) {
        if (typeof prompt === "string") calculatorPrompt.value = prompt;
        return askCalculatorModel();
      },
      async askModelForGraphExpression({prompt} = {}) {
        if (typeof prompt === "string") calculatorScientificPrompt.value = prompt;
        return askScientificCalculatorModel();
      },
      async askModelForMathicsExpression({prompt} = {}) {
        if (typeof prompt === "string") calculatorMathicsPrompt.value = prompt;
        return askCalculatorMathicsModel();
      },
      async evaluateMathics({expression} = {}) {
        if (typeof expression === "string") calculatorMathicsExpression.value = expression;
        return evaluateCalculatorMathics();
      },
      async askResultQuestion({question} = {}) {
        if (typeof question === "string") calculatorQaPrompt.value = question;
        return askCalculatorQa();
      }
    });

    document.querySelectorAll("[data-calc-key]").forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.dataset.calcKey;
        executeCalculatorSemanticIntent(
          "enterToken",
          {token: key},
          () => applyCalculatorToken(key)
        );
      });
    });
    document.querySelectorAll("[data-calc-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.calcAction;
        if (action === "clear") {
          executeCalculatorSemanticIntent(
            "clearExpression",
            {},
            clearCalculatorExpression
          );
        } else if (action === "backspace") {
          calculatorDisplay.value = calculatorDisplay.value.slice(0, -1) || "0";
          calculatorResult.textContent = "ready";
          calculatorDisplay.focus();
        } else if (action === "equals") {
          executeCalculatorSemanticIntent(
            "evaluateExpression",
            {expression: calculatorDisplay.value},
            calculateExpression
          );
        }
      });
    });
    calculatorDisplay.addEventListener("input", () => {
      calculatorDisplay.value = normalizeCalculatorExpression(calculatorDisplay.value);
    });
    calculatorDisplay.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        executeCalculatorSemanticIntent(
          "evaluateExpression",
          {expression: calculatorDisplay.value},
          calculateExpression
        );
      }
    });
    calculatorModeBasic.addEventListener("click", () => executeCalculatorSemanticIntent(
      "switchMode",
      {mode: "basic"},
      () => setCalculatorMode("basic")
    ));
    calculatorModeGraphing.addEventListener("click", () => executeCalculatorSemanticIntent(
      "switchMode",
      {mode: "graphing"},
      () => setCalculatorMode("graphing")
    ));
    calculatorGraphDraw.addEventListener("click", () => executeCalculatorSemanticIntent(
      "drawGraph",
      calculatorGraphIntentPayload(),
      drawCalculatorGraph
    ));
    calculatorGraphReset.addEventListener("click", () => executeCalculatorSemanticIntent(
      "resetGraph",
      {},
      resetCalculatorGraphView
    ));
    calculatorGraphExpression.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        executeCalculatorSemanticIntent(
          "drawGraph",
          calculatorGraphIntentPayload(),
          drawCalculatorGraph
        );
      }
    });
    calculatorMathicsAskModel.addEventListener("click", () => executeCalculatorSemanticIntent(
      "askModelForMathicsExpression",
      {prompt: calculatorMathicsPrompt.value},
      askCalculatorMathicsModel
    ));
    calculatorMathicsEvaluate.addEventListener("click", () => executeCalculatorSemanticIntent(
      "evaluateMathics",
      {expression: calculatorMathicsExpression.value},
      evaluateCalculatorMathics
    ));
    calculatorMathicsClear.addEventListener("click", clearCalculatorMathics);
    calculatorMathicsPrompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        executeCalculatorSemanticIntent(
          "askModelForMathicsExpression",
          {prompt: calculatorMathicsPrompt.value},
          askCalculatorMathicsModel
        );
      }
    });
    calculatorMathicsExpression.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        executeCalculatorSemanticIntent(
          "evaluateMathics",
          {expression: calculatorMathicsExpression.value},
          evaluateCalculatorMathics
        );
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
    calculatorAskModel.addEventListener("click", () => executeCalculatorSemanticIntent(
      "askModelForExpression",
      {prompt: calculatorPrompt.value},
      askCalculatorModel
    ));
    calculatorScientificAskModel.addEventListener("click", () => executeCalculatorSemanticIntent(
      "askModelForGraphExpression",
      {prompt: calculatorScientificPrompt.value},
      askScientificCalculatorModel
    ));
    calculatorQaAsk?.addEventListener("click", () => executeCalculatorSemanticIntent(
      "askResultQuestion",
      {question: calculatorQaPrompt.value},
      askCalculatorQa
    ));
    calculatorPrompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        executeCalculatorSemanticIntent(
          "askModelForExpression",
          {prompt: calculatorPrompt.value},
          askCalculatorModel
        );
      }
    });
    calculatorScientificPrompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        executeCalculatorSemanticIntent(
          "askModelForGraphExpression",
          {prompt: calculatorScientificPrompt.value},
          askScientificCalculatorModel
        );
      }
    });
    calculatorQaPrompt?.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        executeCalculatorSemanticIntent(
          "askResultQuestion",
          {question: calculatorQaPrompt.value},
          askCalculatorQa
        );
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
        const graph = drawCalculatorGraph();
        return {
          ok: graph?.ok !== false,
          expression,
          statusText: calculatorScientificModelStatus.textContent,
          graph
        };
      } catch (error) {
        calculatorScientificModelStatus.textContent = error.message || "scientific model prompt failed";
        return {ok: false, code: "provider-request-failed", error: error.message || "scientific model prompt failed"};
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
