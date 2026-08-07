    function requireCalculatorCore() {
      const core = window.MainComputerCalculatorCore;
      if (!core) {
        throw new Error("Calculator core is unavailable; reload the Applications viewport");
      }
      return core;
    }

    function requireCalculatorViewModel() {
      const viewModel = window.MainComputerCalculatorViewModel;
      if (!viewModel) {
        throw new Error("Calculator view model is unavailable; reload the Applications viewport");
      }
      return viewModel;
    }

    function requireCalculatorCapabilities() {
      const capabilities = window.MainComputerCalculatorCapabilities;
      if (!capabilities) {
        throw new Error("Calculator capability bridge is unavailable; reload the Applications viewport");
      }
      return capabilities;
    }

    function normalizeCalculatorExpression(value) {
      return requireCalculatorCore().normalizeCalculatorExpression(value);
    }

    function evaluateCalculatorArithmeticExpression(rawExpression) {
      return requireCalculatorCore().evaluateCalculatorExpression(rawExpression);
    }
    function calculateExpression() {
      const viewModel = requireCalculatorViewModel().buildCalculatorVisibleResultModel(
        evaluateCalculatorArithmeticExpression(calculatorDisplay.value)
      );
      calculatorDisplay.value = viewModel.displayExpression;
      calculatorResult.textContent = viewModel.resultText;
      return viewModel.runtimeResult;
    }
    let calculatorEmbeddedChatController = null;

    function calculatorEmbeddedChatContextSnapshot() {
      const graphing = calculatorModeGraphing?.classList?.contains("active") || false;
      return requireCalculatorViewModel().buildCalculatorSessionContextSnapshot({
        activeMode: graphing ? "scientific-graphing" : "basic",
        arithmeticExpression: calculatorDisplay?.value,
        arithmeticResult: calculatorResult?.textContent,
        arithmeticPrompt: calculatorPrompt?.value,
        graphExpression: calculatorGraphExpression?.value,
        graphXMin: calculatorGraphXMin?.value,
        graphXMax: calculatorGraphXMax?.value,
        graphYMin: calculatorGraphYMin?.value,
        graphYMax: calculatorGraphYMax?.value,
        graphStatus: calculatorGraphStatus?.textContent,
        mathicsPrompt: calculatorMathicsPrompt?.value,
        mathicsExpression: calculatorMathicsExpression?.value,
        mathicsStatus: calculatorMathicsEvaluationStatus?.textContent,
        qaPrompt: calculatorQaPrompt?.value,
        qaStatus: calculatorQaStatus?.textContent
      });
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

    function applyCalculatorModeSwitchViewModel(viewModel) {
      calculatorModeBasic.classList.toggle("active", !!viewModel.buttons?.basicActive);
      calculatorModeGraphing.classList.toggle("active", !!viewModel.buttons?.graphingActive);
      calculatorShell.classList.toggle("graphing-active", !!viewModel.shell?.graphingActive);
      calculatorShell.classList.toggle("chat-docked", viewModel.shell?.chatDocked !== false);
      calculatorShell.classList.toggle("chat-active", !!viewModel.shell?.chatActive);
      calculatorBasicPanel.hidden = !!viewModel.panels?.basicHidden;
      calculatorGraphingPanel.hidden = !!viewModel.panels?.graphingHidden;
      calculatorMathicsPanel.hidden = !!viewModel.panels?.mathicsHidden;
      if (calculatorChatPanel) calculatorChatPanel.hidden = !!viewModel.panels?.chatHidden;
      calculatorResult.textContent = viewModel.statusText || "ready";
      if (calculatorChatPanel && viewModel.shouldMountChat) {
        mountCalculatorEmbeddedChat();
      }
      if (viewModel.focusTarget === "graphExpression") {
        calculatorGraphExpression.focus();
      } else {
        calculatorDisplay.focus();
      }
      if (viewModel.shouldDrawGraph) {
        setTimeout(drawCalculatorGraph, 0);
      }
      return viewModel.runtimeResult || viewModel;
    }

    function setCalculatorMode(mode) {
      return applyCalculatorModeSwitchViewModel(
        requireCalculatorViewModel().buildCalculatorModeSwitchViewModel(mode, {
          expression: calculatorDisplay.value,
          graphExpression: calculatorGraphExpression.value
        })
      );
    }

    function drawCalculatorLineSet(canvasContext, lines) {
      for (const line of lines) {
        canvasContext.moveTo(line.x1, line.y1);
        canvasContext.lineTo(line.x2, line.y2);
      }
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

      const renderModel = requireCalculatorViewModel().buildCalculatorGraphRenderModel(
        calculatorGraphExpression.value,
        {
          xMin: calculatorGraphXMin.value,
          xMax: calculatorGraphXMax.value,
          yMin: calculatorGraphYMin.value,
          yMax: calculatorGraphYMax.value
        },
        {width, height}
      );

      if (!renderModel.ok) {
        canvasContext.fillStyle = "#ff8f70";
        canvasContext.font = "700 14px Arial, Helvetica, sans-serif";
        canvasContext.fillText(renderModel.errorLabel || "Graph error", 14, 28);
        calculatorGraphStatus.textContent = renderModel.statusText;
        return renderModel.runtimeResult;
      }

      canvasContext.lineWidth = 1;
      canvasContext.strokeStyle = "#26291e";
      canvasContext.beginPath();
      drawCalculatorLineSet(canvasContext, renderModel.gridLines);
      canvasContext.stroke();

      canvasContext.strokeStyle = "#4f493a";
      canvasContext.beginPath();
      drawCalculatorLineSet(canvasContext, renderModel.axisLines);
      canvasContext.stroke();

      canvasContext.lineWidth = 2;
      canvasContext.strokeStyle = "#a7d86d";
      canvasContext.beginPath();
      for (const segment of renderModel.curveSegments) {
        segment.forEach((point, index) => {
          if (index === 0) canvasContext.moveTo(point.x, point.y);
          else canvasContext.lineTo(point.x, point.y);
        });
      }
      canvasContext.stroke();

      calculatorGraphStatus.textContent = renderModel.statusText;
      return renderModel.runtimeResult;
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
        const viewModel = requireCalculatorViewModel().buildCalculatorAssistedExpressionViewModel(
          "arithmetic",
          await requireCalculatorCapabilities().askArithmeticModel(problem)
        );
        if (!viewModel.ok) {
          calculatorModelStatus.textContent = viewModel.statusText;
          return viewModel.runtimeResult;
        }
        calculatorDisplay.value = viewModel.expressionText;
        calculatorModelStatus.textContent = viewModel.statusText;
        const evaluation = calculateExpression();
        return {
          ...viewModel.runtimeResult,
          result: calculatorResult.textContent,
          evaluation
        };
      } catch (error) {
        const viewModel = requireCalculatorViewModel().buildCalculatorAssistedExpressionErrorViewModel("arithmetic", error);
        calculatorModelStatus.textContent = viewModel.statusText;
        return viewModel.runtimeResult;
      } finally {
        calculatorAskModel.disabled = false;
      }
    }
    function setCalculatorMathicsOutput(text, state = "ready") {
      calculatorMathicsOutput.textContent = text || "";
      calculatorMathicsOutput.classList.toggle("error", state === "error");
    }
    function applyCalculatorMathicsViewModel(viewModel) {
      if (Object.prototype.hasOwnProperty.call(viewModel, "expressionText")) {
        calculatorMathicsExpression.value = viewModel.expressionText;
      }
      if (viewModel.modelStatusText) calculatorMathicsModelStatus.textContent = viewModel.modelStatusText;
      if (viewModel.evaluationStatusText) calculatorMathicsEvaluationStatus.textContent = viewModel.evaluationStatusText;
      if (Object.prototype.hasOwnProperty.call(viewModel, "outputText")) {
        setCalculatorMathicsOutput(viewModel.outputText, viewModel.outputState);
      }
      if (viewModel.focusExpression) calculatorMathicsExpression.focus();
      return viewModel.runtimeResult || viewModel;
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
        const viewModel = requireCalculatorViewModel().buildCalculatorMathicsModelViewModel(
          await requireCalculatorCapabilities().askMathicsModel(prompt)
        );
        return applyCalculatorMathicsViewModel(viewModel);
      } catch (error) {
        const viewModel = requireCalculatorViewModel().buildCalculatorMathicsModelErrorViewModel(error);
        return applyCalculatorMathicsViewModel(viewModel);
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
      const pendingViewModel = requireCalculatorViewModel().buildCalculatorMathicsEvaluationPendingViewModel();
      applyCalculatorMathicsViewModel(pendingViewModel);
      try {
        const viewModel = requireCalculatorViewModel().buildCalculatorMathicsEvaluationViewModel(
          expression,
          await requireCalculatorCapabilities().evaluateMathics(expression)
        );
        return applyCalculatorMathicsViewModel(viewModel);
      } catch (error) {
        const viewModel = requireCalculatorViewModel().buildCalculatorMathicsEvaluationErrorViewModel(expression, error);
        return applyCalculatorMathicsViewModel(viewModel);
      } finally {
        calculatorMathicsEvaluate.disabled = false;
      }
    }
    function clearCalculatorMathics() {
      const viewModel = requireCalculatorViewModel().buildCalculatorMathicsClearViewModel();
      return applyCalculatorMathicsViewModel(viewModel);
    }
    function calculatorQaContext() {
      return requireCalculatorViewModel().buildCalculatorResultQaContext({
        arithmeticExpression: calculatorDisplay?.value,
        arithmeticResult: calculatorResult?.textContent,
        graphExpression: calculatorGraphExpression?.value,
        graphStatus: calculatorGraphStatus?.textContent,
        graphXMin: calculatorGraphXMin?.value,
        graphXMax: calculatorGraphXMax?.value,
        graphYMin: calculatorGraphYMin?.value,
        graphYMax: calculatorGraphYMax?.value,
        mathicsExpression: calculatorMathicsExpression?.value,
        mathicsOutput: calculatorMathicsOutput?.textContent
      });
    }
    function setCalculatorQaAnswer(text, state = "ready") {
      if (!calculatorQaAnswer) return;
      calculatorQaAnswer.textContent = text || "";
      calculatorQaAnswer.classList.toggle("error", state === "error");
    }
    function applyCalculatorQaViewModel(viewModel) {
      if (calculatorQaStatus && viewModel.qaStatusText) calculatorQaStatus.textContent = viewModel.qaStatusText;
      if (Object.prototype.hasOwnProperty.call(viewModel, "answerText")) {
        setCalculatorQaAnswer(viewModel.answerText, viewModel.answerState);
      }
      return viewModel.runtimeResult || viewModel;
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
      applyCalculatorQaViewModel(requireCalculatorViewModel().buildCalculatorResultQaPendingViewModel());
      try {
        const viewModel = requireCalculatorViewModel().buildCalculatorResultQaAnswerViewModel(
          question,
          await requireCalculatorCapabilities().askResultQuestion(question, calculatorQaContext())
        );
        return applyCalculatorQaViewModel(viewModel);
      } catch (error) {
        const viewModel = requireCalculatorViewModel().buildCalculatorResultQaErrorViewModel(question, error);
        return applyCalculatorQaViewModel(viewModel);
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

    function applyReadyCalculatorState(state, focusTarget = calculatorDisplay) {
      calculatorDisplay.value = state.expression || "0";
      calculatorResult.textContent = state.statusText || state.result || "ready";
      focusTarget?.focus?.();
      return state;
    }

    function applyCalculatorToken(key) {
      return applyReadyCalculatorState(
        requireCalculatorCore().appendCalculatorDisplayToken(calculatorDisplay.value, key)
      );
    }

    function clearCalculatorExpression() {
      return applyReadyCalculatorState(requireCalculatorCore().clearCalculatorDisplayExpression());
    }

    function backspaceCalculatorExpression() {
      return applyReadyCalculatorState(
        requireCalculatorCore().backspaceCalculatorDisplayExpression(calculatorDisplay.value)
      );
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
        return setCalculatorMode(mode);
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
          backspaceCalculatorExpression();
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
          applyCalculatorGraphEditState(requireCalculatorCore().clearCalculatorGraphExpression());
        } else if (action === "backspace") {
          applyCalculatorGraphEditState(requireCalculatorCore().backspaceCalculatorGraphExpression(
            calculatorGraphExpression.value,
            calculatorGraphExpression.selectionStart,
            calculatorGraphExpression.selectionEnd
          ));
        }
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
        const viewModel = requireCalculatorViewModel().buildCalculatorAssistedExpressionViewModel(
          "graph",
          await requireCalculatorCapabilities().askGraphModel(problem)
        );
        if (!viewModel.ok) {
          calculatorScientificModelStatus.textContent = viewModel.statusText;
          return viewModel.runtimeResult;
        }
        calculatorGraphExpression.value = viewModel.expressionText;
        calculatorScientificModelStatus.textContent = viewModel.statusText;
        const graph = drawCalculatorGraph();
        return {
          ...viewModel.runtimeResult,
          ok: graph?.ok !== false,
          graph
        };
      } catch (error) {
        const viewModel = requireCalculatorViewModel().buildCalculatorAssistedExpressionErrorViewModel("graph", error);
        calculatorScientificModelStatus.textContent = viewModel.statusText;
        return viewModel.runtimeResult;
      } finally {
        calculatorScientificAskModel.disabled = false;
      }
    }
    function applyCalculatorGraphEditState(state) {
      calculatorGraphExpression.value = state.expression || "";
      calculatorGraphExpression.focus();
      calculatorGraphExpression.setSelectionRange(state.selectionStart || 0, state.selectionEnd || state.selectionStart || 0);
      return state;
    }

    function insertCalculatorGraphText(text, caretBack = 0) {
      return applyCalculatorGraphEditState(requireCalculatorCore().insertCalculatorGraphText(
        calculatorGraphExpression.value,
        text,
        calculatorGraphExpression.selectionStart,
        calculatorGraphExpression.selectionEnd,
        caretBack
      ));
    }
