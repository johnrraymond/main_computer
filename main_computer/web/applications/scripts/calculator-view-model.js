(function installMainComputerCalculatorViewModel(root, factory) {
  "use strict";

  const api = factory(function requireCalculatorCore() {
    if (root && root.MainComputerCalculatorCore) {
      return root.MainComputerCalculatorCore;
    }
    if (typeof require === "function") {
      return require("./calculator-core.js");
    }
    throw new Error("Calculator core is unavailable; load calculator-core.js before calculator-view-model.js");
  });
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.MainComputerCalculatorViewModel = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function buildMainComputerCalculatorViewModel(requireCalculatorCore) {
  "use strict";

  function coreApi() {
    return requireCalculatorCore();
  }

  function normalizeCalculatorResult(evaluation) {
    const result = evaluation && typeof evaluation === "object" ? evaluation : {};
    if (!result.expression) {
      return Object.freeze({
        ok: false,
        expression: "",
        result: "ready",
        code: "expression-required",
        error: "enter an expression"
      });
    }
    if (result.ok) {
      return Object.freeze(Object.assign({}, result, {
        result: String(result.value),
        statusText: "ready",
        code: ""
      }));
    }
    return Object.freeze(Object.assign({}, result, {
      result: result.error || "check expression",
      statusText: "error",
      code: "expression-invalid"
    }));
  }
  function classifyCalculatorRuntimeResult(evaluation) {
    const result = normalizeCalculatorResult(evaluation);
    if (result.code === "expression-required") return "expression-required";
    if (result.ok) return "ready";
    return "error";
  }
  function buildCalculatorStatusMessage(evaluation) {
    return normalizeCalculatorResult(evaluation).result;
  }
  function buildCalculatorVisibleResultModel(evaluation) {
    const result = normalizeCalculatorResult(evaluation);
    const expressionRequired = result.code === "expression-required";
    const displayExpression = result.ok
      ? String(result.value)
      : (expressionRequired ? "0" : result.expression || "");
    return Object.freeze({
      ok: result.ok === true,
      expression: result.expression || "",
      displayExpression,
      resultText: buildCalculatorStatusMessage(evaluation),
      statusText: result.statusText || (expressionRequired ? "ready" : "error"),
      code: result.code || "",
      runtimeResult: result
    });
  }
  function calculatorString(value) {
    if (value === null || typeof value === "undefined") return "";
    return String(value);
  }
  function buildCalculatorModeSwitchViewModel(mode, state = {}) {
    const normalizedMode = calculatorString(mode) === "graphing" ? "graphing" : "basic";
    const graphing = normalizedMode === "graphing";
    const expression = calculatorString(state.expression);
    const graphExpression = calculatorString(state.graphExpression);
    const statusText = "ready";
    return Object.freeze({
      ok: true,
      mode: normalizedMode,
      graphing,
      statusText,
      buttons: Object.freeze({
        basicActive: !graphing,
        graphingActive: graphing
      }),
      shell: Object.freeze({
        graphingActive: graphing,
        chatDocked: true,
        chatActive: false
      }),
      panels: Object.freeze({
        basicHidden: false,
        graphingHidden: !graphing,
        mathicsHidden: !graphing,
        chatHidden: false
      }),
      focusTarget: graphing ? "graphExpression" : "display",
      shouldDrawGraph: graphing,
      shouldMountChat: true,
      runtimeResult: Object.freeze({
        ok: true,
        mode: normalizedMode,
        expression,
        graphExpression,
        statusText
      })
    });
  }
  function buildCalculatorSessionContextSnapshot(state = {}) {
    const arithmetic = state.arithmetic || {};
    const graph = state.graph || {};
    const mathics = state.mathics || {};
    const qa = state.qa || {};
    const activeMode = calculatorString(state.activeMode || state.active_mode || "basic") || "basic";
    return Object.freeze({
      app: "calculator",
      target_kind: "calculator-session",
      target_id: "calculator",
      active_mode: activeMode,
      arithmetic: Object.freeze({
        expression: calculatorString(arithmetic.expression ?? state.arithmeticExpression),
        result: calculatorString(arithmetic.result ?? state.arithmeticResult),
        prompt: calculatorString(arithmetic.prompt ?? state.arithmeticPrompt)
      }),
      graph: Object.freeze({
        expression: calculatorString(graph.expression ?? state.graphExpression),
        x_min: calculatorString(graph.xMin ?? graph.x_min ?? state.graphXMin ?? state.graph_x_min),
        x_max: calculatorString(graph.xMax ?? graph.x_max ?? state.graphXMax ?? state.graph_x_max),
        y_min: calculatorString(graph.yMin ?? graph.y_min ?? state.graphYMin ?? state.graph_y_min),
        y_max: calculatorString(graph.yMax ?? graph.y_max ?? state.graphYMax ?? state.graph_y_max),
        status: calculatorString(graph.status ?? state.graphStatus)
      }),
      mathics: Object.freeze({
        prompt: calculatorString(mathics.prompt ?? state.mathicsPrompt),
        expression: calculatorString(mathics.expression ?? state.mathicsExpression),
        status: calculatorString(mathics.status ?? state.mathicsStatus)
      }),
      qa: Object.freeze({
        prompt: calculatorString(qa.prompt ?? state.qaPrompt),
        status: calculatorString(qa.status ?? state.qaStatus)
      }),
      allowed_tools: Object.freeze(["arithmetic", "scientific-graphing", "mathics", "calculator-qa"])
    });
  }
  function buildCalculatorResultQaContext(state = {}) {
    const arithmetic = state.arithmetic || {};
    const graph = state.graph || {};
    const graphRange = graph.range || {};
    const mathics = state.mathics || {};
    return Object.freeze({
      basic_expression: calculatorString(arithmetic.expression ?? state.basicExpression ?? state.arithmeticExpression),
      basic_result: calculatorString(arithmetic.result ?? state.basicResult ?? state.arithmeticResult),
      graph_expression: calculatorString(graph.expression ?? state.graphExpression),
      graph_status: calculatorString(graph.status ?? state.graphStatus),
      graph_range: Object.freeze({
        x_min: calculatorString(graphRange.xMin ?? graphRange.x_min ?? graph.xMin ?? graph.x_min ?? state.graphXMin ?? state.graph_x_min),
        x_max: calculatorString(graphRange.xMax ?? graphRange.x_max ?? graph.xMax ?? graph.x_max ?? state.graphXMax ?? state.graph_x_max),
        y_min: calculatorString(graphRange.yMin ?? graphRange.y_min ?? graph.yMin ?? graph.y_min ?? state.graphYMin ?? state.graph_y_min),
        y_max: calculatorString(graphRange.yMax ?? graphRange.y_max ?? graph.yMax ?? graph.y_max ?? state.graphYMax ?? state.graph_y_max)
      }),
      mathics_expression: calculatorString(mathics.expression ?? state.mathicsExpression),
      mathics_output: calculatorString(mathics.output ?? state.mathicsOutput)
    });
  }
  function buildCalculatorResultQaPendingViewModel() {
    return Object.freeze({
      qaStatusText: "asking model about results",
      answerText: "Asking about the current calculator context...",
      answerState: "ready"
    });
  }
  function buildCalculatorResultQaAnswerViewModel(question, data = {}) {
    const normalizedQuestion = calculatorString(question).trim();
    const answer = calculatorString(data && data.answer) || "(no answer returned)";
    return Object.freeze({
      ok: true,
      question: normalizedQuestion,
      answer,
      qaStatusText: "result Q&A answered",
      answerText: answer,
      answerState: "ready",
      runtimeResult: Object.freeze({
        ok: true,
        question: normalizedQuestion,
        answer,
        statusText: "ready"
      })
    });
  }
  function buildCalculatorResultQaErrorViewModel(question, error) {
    const normalizedQuestion = calculatorString(question).trim();
    const message = calculatorErrorMessage(error, "calculator Q&A failed");
    return Object.freeze({
      ok: false,
      question: normalizedQuestion,
      qaStatusText: message,
      answerText: message,
      answerState: "error",
      runtimeResult: Object.freeze({
        ok: false,
        question: normalizedQuestion,
        code: "result-qa-failed",
        error: message,
        statusText: "error"
      })
    });
  }
  function calculatorErrorMessage(error, fallbackMessage) {
    if (error && typeof error === "object" && error.message) {
      return calculatorString(error.message);
    }
    const text = calculatorString(error);
    return text || fallbackMessage;
  }
  function calculatorAssistedExpressionProfile(kind) {
    const normalizedKind = kind === "graph" ? "graph" : "arithmetic";
    if (normalizedKind === "graph") {
      return Object.freeze({
        kind: "graph",
        target: "graphExpression",
        statusPrefix: "f(x): ",
        missingExpressionMessage: "no graph expression returned",
        fallbackFailureMessage: "scientific model prompt failed"
      });
    }
    return Object.freeze({
      kind: "arithmetic",
      target: "arithmeticExpression",
      statusPrefix: "model expression: ",
      missingExpressionMessage: "no expression returned",
      fallbackFailureMessage: "model prompt failed"
    });
  }
  function buildCalculatorAssistedExpressionErrorViewModel(kind, error) {
    const profile = calculatorAssistedExpressionProfile(kind);
    const message = calculatorErrorMessage(error, profile.fallbackFailureMessage);
    return Object.freeze({
      ok: false,
      kind: profile.kind,
      target: profile.target,
      statusText: message,
      runtimeResult: Object.freeze({
        ok: false,
        code: "provider-request-failed",
        error: message
      })
    });
  }
  function buildCalculatorAssistedExpressionViewModel(kind, data = {}) {
    const profile = calculatorAssistedExpressionProfile(kind);
    const content = calculatorString(data && data.content);
    const expression = profile.kind === "graph"
      ? coreApi().extractCalculatorGraphExpression(content)
      : coreApi().extractCalculatorExpression(content);
    if (!expression) {
      return buildCalculatorAssistedExpressionErrorViewModel(
        profile.kind,
        new Error(profile.missingExpressionMessage)
      );
    }
    const statusText = `${profile.statusPrefix}${expression}`;
    return Object.freeze({
      ok: true,
      kind: profile.kind,
      target: profile.target,
      expression,
      expressionText: expression,
      statusText,
      runtimeResult: Object.freeze({
        ok: true,
        kind: profile.kind,
        expression,
        statusText
      })
    });
  }
  function buildCalculatorMathicsModelViewModel(data = {}) {
    const expression = calculatorString(data.expression);
    return Object.freeze({
      ok: true,
      expression,
      expressionText: expression,
      modelStatusText: `mathics expression: ${expression}`,
      focusExpression: true,
      runtimeResult: Object.freeze({
        ok: true,
        expression
      })
    });
  }
  function buildCalculatorMathicsModelErrorViewModel(error) {
    const message = calculatorErrorMessage(error, "mathics model prompt failed");
    return Object.freeze({
      ok: false,
      modelStatusText: message,
      runtimeResult: Object.freeze({
        ok: false,
        code: "provider-request-failed",
        error: message
      })
    });
  }
  function buildCalculatorMathicsEvaluationPendingViewModel() {
    return Object.freeze({
      evaluationStatusText: "evaluating Mathics expression",
      outputText: "Evaluating...",
      outputState: "ready"
    });
  }
  function buildCalculatorMathicsEvaluationViewModel(expression, data = {}) {
    const normalizedExpression = calculatorString(expression).trim();
    const output = calculatorString(data.output) || "(no result)";
    return Object.freeze({
      ok: true,
      expression: normalizedExpression,
      output,
      evaluationStatusText: "Mathics result ready",
      outputText: output,
      outputState: "ready",
      runtimeResult: Object.freeze({
        ok: true,
        expression: normalizedExpression,
        output,
        statusText: "ready"
      })
    });
  }
  function buildCalculatorMathicsEvaluationErrorViewModel(expression, error) {
    const normalizedExpression = calculatorString(expression).trim();
    const message = calculatorErrorMessage(error, "Mathics evaluation failed");
    return Object.freeze({
      ok: false,
      expression: normalizedExpression,
      evaluationStatusText: message,
      outputText: message,
      outputState: "error",
      runtimeResult: Object.freeze({
        ok: false,
        expression: normalizedExpression,
        code: "mathics-evaluation-failed",
        error: message,
        statusText: "error"
      })
    });
  }
  function buildCalculatorMathicsClearViewModel() {
    return Object.freeze({
      expressionText: "",
      outputText: "Mathics ready.",
      outputState: "ready",
      evaluationStatusText: "mathics evaluation ready",
      focusExpression: true
    });
  }
  function buildCalculatorGraphRenderModel(rawExpression, rawRange, viewport = {}) {
    const width = Math.max(1, Math.floor(Number(viewport && viewport.width) || 1));
    const height = Math.max(1, Math.floor(Number(viewport && viewport.height) || 1));
    try {
      const plot = coreApi().sampleCalculatorGraphExpression(rawExpression, rawRange, width);
      const range = plot.range;
      const toPx = (x) => (x - range.xMin) / (range.xMax - range.xMin) * width;
      const toPy = (y) => height - (y - range.yMin) / (range.yMax - range.yMin) * height;
      const gridLines = [];
      const axisLines = [];
      const curveSegments = [];
      const xStep = (range.xMax - range.xMin) / 10;
      const yStep = (range.yMax - range.yMin) / 10;
      for (let i = 0; i <= 10; i += 1) {
        const x = toPx(range.xMin + xStep * i);
        gridLines.push(Object.freeze({x1: x, y1: 0, x2: x, y2: height}));
        const y = toPy(range.yMin + yStep * i);
        gridLines.push(Object.freeze({x1: 0, y1: y, x2: width, y2: y}));
      }
      if (range.xMin <= 0 && range.xMax >= 0) {
        const axisX = toPx(0);
        axisLines.push(Object.freeze({x1: axisX, y1: 0, x2: axisX, y2: height}));
      }
      if (range.yMin <= 0 && range.yMax >= 0) {
        const axisY = toPy(0);
        axisLines.push(Object.freeze({x1: 0, y1: axisY, x2: width, y2: axisY}));
      }
      let activeSegment = [];
      for (const sample of plot.samples) {
        if (!sample.visible) {
          if (activeSegment.length) {
            curveSegments.push(Object.freeze(activeSegment));
            activeSegment = [];
          }
          continue;
        }
        activeSegment.push(Object.freeze({x: sample.px, y: toPy(sample.y)}));
      }
      if (activeSegment.length) {
        curveSegments.push(Object.freeze(activeSegment));
      }
      const statusText = `graphed ${plot.expression} | ${plot.finiteCount} visible samples`;
      const runtimeResult = Object.freeze({
        ok: true,
        expression: plot.expression,
        range,
        finiteCount: plot.finiteCount,
        statusText
      });
      return Object.freeze({
        ok: true,
        expression: plot.expression,
        width,
        height,
        range,
        finiteCount: plot.finiteCount,
        statusText,
        gridLines: Object.freeze(gridLines),
        axisLines: Object.freeze(axisLines),
        curveSegments: Object.freeze(curveSegments),
        runtimeResult
      });
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      let expression = "";
      try {
        expression = coreApi().normalizeGraphExpression(rawExpression);
      } catch (_ignored) {
        expression = calculatorString(rawExpression).trim();
      }
      const range = Object.freeze({
        xMin: Number(rawRange && rawRange.xMin),
        xMax: Number(rawRange && rawRange.xMax),
        yMin: Number(rawRange && rawRange.yMin),
        yMax: Number(rawRange && rawRange.yMax)
      });
      const statusText = `graph error: ${message}`;
      const runtimeResult = Object.freeze({
        ok: false,
        expression,
        range,
        statusText,
        code: /range|x min|x max|y min|y max|finite numbers/i.test(message) ? "graph-range-invalid" : "graph-expression-required",
        error: message
      });
      return Object.freeze({
        ok: false,
        expression,
        width,
        height,
        range,
        statusText,
        errorLabel: "Graph error",
        runtimeResult
      });
    }
  }

  return Object.freeze({
    schema: "main-computer-calculator-view-model-v1",
    version: "calculator-view-model-v1",
    normalizeCalculatorResult,
    classifyCalculatorRuntimeResult,
    buildCalculatorStatusMessage,
    buildCalculatorVisibleResultModel,
    buildCalculatorModeSwitchViewModel,
    buildCalculatorSessionContextSnapshot,
    buildCalculatorResultQaContext,
    buildCalculatorResultQaPendingViewModel,
    buildCalculatorResultQaAnswerViewModel,
    buildCalculatorResultQaErrorViewModel,
    buildCalculatorAssistedExpressionViewModel,
    buildCalculatorAssistedExpressionErrorViewModel,
    buildCalculatorMathicsModelViewModel,
    buildCalculatorMathicsModelErrorViewModel,
    buildCalculatorMathicsEvaluationPendingViewModel,
    buildCalculatorMathicsEvaluationViewModel,
    buildCalculatorMathicsEvaluationErrorViewModel,
    buildCalculatorMathicsClearViewModel,
    buildCalculatorGraphRenderModel
  });
});
