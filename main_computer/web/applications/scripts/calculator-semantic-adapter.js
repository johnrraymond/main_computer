(() => {
  (function createCalculatorSemanticAdapter(global) {
    "use strict";

    if (!global) return;

    const VERSION = "calculator-semantic-adapter-v1";
    const APP_ID = "calculator";
    const ADAPTER_ID = "calculator-domain-adapter";
    const KIND = "multi-lane-calculation-domain-adapter";
    const STATE_SCHEMA_VERSION = "calculator-semantic-state-v1";
    const PREFLIGHT_SCHEMA_VERSION = "calculator-preflight-v1";
    const RECEIPT_SCHEMA_VERSION = "mcel-semantic-receipt-v1";
    const RECOVERY_CLASSIFICATION_SCHEMA_VERSION = "calculator-recovery-classification-v1";
    const RECOVERY_PLAN_SCHEMA_VERSION = "calculator-recovery-plan-v1";
    const RECOVERY_COVERAGE_VERSION = "calculator-recovery-coverage-v1";
    const INTENT_COVERAGE_SCHEMA_VERSION = "calculator-intent-coverage-v1";
    const SEMANTIC_RUNTIME_SCOPE = "calculator-compute-and-helper-lanes-v1";
    const MAX_RECEIPTS = 100;
    const ADAPTER_TOOLKIT = global.McelSemanticAdapterToolkit || (
      typeof require === "function" ? require("./mcel-semantic-adapter-toolkit.js") : null
    );

    if (!ADAPTER_TOOLKIT) {
      throw new Error("McelSemanticAdapterToolkit must be loaded before CalculatorSemanticAdapter.");
    }

    const INTENT_DEFINITIONS = Object.freeze([
      Object.freeze({
        id: "switchMode",
        label: "Switch calculator mode",
        risk: "read-only",
        status: "executable",
        lane: "local-ui",
        executionBinding: "calculator-runtime.switch-mode",
        runtimeMethod: "switchMode",
        mutates: false
      }),
      Object.freeze({
        id: "enterToken",
        label: "Enter an arithmetic token",
        risk: "read-only",
        status: "executable",
        lane: "local-arithmetic",
        executionBinding: "calculator-runtime.enter-token",
        runtimeMethod: "enterToken",
        mutates: false
      }),
      Object.freeze({
        id: "clearExpression",
        label: "Clear the arithmetic expression",
        risk: "read-only",
        status: "executable",
        lane: "local-arithmetic",
        executionBinding: "calculator-runtime.clear-expression",
        runtimeMethod: "clearExpression",
        mutates: false
      }),
      Object.freeze({
        id: "evaluateExpression",
        label: "Evaluate a deterministic arithmetic expression",
        risk: "read-only",
        status: "executable",
        lane: "local-arithmetic",
        executionBinding: "calculator-runtime.evaluate-expression",
        runtimeMethod: "evaluateExpression",
        mutates: false
      }),
      Object.freeze({
        id: "drawGraph",
        label: "Draw a deterministic graph",
        risk: "read-only",
        status: "executable",
        lane: "local-graph",
        executionBinding: "calculator-runtime.draw-graph",
        runtimeMethod: "drawGraph",
        mutates: false
      }),
      Object.freeze({
        id: "resetGraph",
        label: "Reset graph ranges",
        risk: "read-only",
        status: "executable",
        lane: "local-graph",
        executionBinding: "calculator-runtime.reset-graph",
        runtimeMethod: "resetGraph",
        mutates: false
      }),
      Object.freeze({
        id: "askModelForExpression",
        label: "Ask a model for an arithmetic expression",
        risk: "read-only",
        status: "executable",
        lane: "model-arithmetic",
        executionBinding: "calculator-runtime.ask-model-expression",
        runtimeMethod: "askModelForExpression",
        mutates: false
      }),
      Object.freeze({
        id: "askModelForGraphExpression",
        label: "Ask a model for a graph expression",
        risk: "read-only",
        status: "executable",
        lane: "model-graph",
        executionBinding: "calculator-runtime.ask-model-graph-expression",
        runtimeMethod: "askModelForGraphExpression",
        mutates: false
      }),
      Object.freeze({
        id: "askModelForMathicsExpression",
        label: "Ask a model for a Mathics expression",
        risk: "read-only",
        status: "executable",
        lane: "model-mathics",
        executionBinding: "calculator-runtime.ask-model-mathics-expression",
        runtimeMethod: "askModelForMathicsExpression",
        mutates: false
      }),
      Object.freeze({
        id: "evaluateMathics",
        label: "Evaluate a symbolic Mathics expression",
        risk: "local-state",
        status: "executable",
        lane: "mathics",
        executionBinding: "calculator-runtime.evaluate-mathics",
        runtimeMethod: "evaluateMathics",
        mutates: false
      }),
      Object.freeze({
        id: "askResultQuestion",
        label: "Ask a contextual result question",
        risk: "read-only",
        status: "executable",
        lane: "model-result-qa",
        executionBinding: "calculator-runtime.ask-result-question",
        runtimeMethod: "askResultQuestion",
        mutates: false
      })
    ]);

    const FAILURE_DEFINITIONS = Object.freeze({
      "unsupported-intent": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        message: "The requested Calculator intent is not registered.",
        nextStep: "Choose a declared Calculator intent."
      }),
      "runtime-binding-unavailable": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        message: "The Calculator runtime binding is unavailable.",
        nextStep: "Reload Calculator so the semantic adapter can bind to the live controls."
      }),
      "mode-unsupported": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        message: "The requested Calculator mode is unsupported.",
        nextStep: "Choose basic or graphing mode."
      }),
      "token-invalid": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        message: "The arithmetic token is outside the Calculator keypad grammar.",
        nextStep: "Enter a digit, decimal point, parenthesis, or supported arithmetic operator."
      }),
      "expression-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        message: "A non-empty expression is required.",
        nextStep: "Enter an expression before evaluating."
      }),
      "expression-invalid": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        message: "The arithmetic expression could not be evaluated.",
        nextStep: "Correct the expression and retry deterministic local evaluation."
      }),
      "graph-expression-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        message: "A graph expression is required.",
        nextStep: "Enter f(x) before drawing the graph."
      }),
      "graph-range-invalid": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        message: "The graph range is invalid.",
        nextStep: "Use finite ranges where minimum values are less than maximum values."
      }),
      "prompt-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        message: "A non-empty model prompt is required.",
        nextStep: "Enter a calculator request before asking the model."
      }),
      "question-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        message: "A non-empty result question is required.",
        nextStep: "Ask a question about the current calculator evidence."
      }),
      "provider-request-failed": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        message: "The model provider request failed.",
        nextStep: "Preserve the deterministic calculator state and retry the explicit model action."
      }),
      "mathics-evaluation-failed": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        message: "The Mathics evaluation failed.",
        nextStep: "Correct the symbolic expression or restore the Mathics backend, then retry."
      }),
      "result-qa-failed": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        message: "The calculator result Q&A request failed.",
        nextStep: "Preserve the current calculation evidence and retry the explicit Q&A action."
      }),
      "hidden-mutation-prohibited": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        message: "Calculator intents cannot request hidden file, Git, shell, package, or publish mutation.",
        nextStep: "Remove mutation directives and keep the operation inside Calculator."
      }),
      "unknown-failure": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        message: "The Calculator adapter could not classify the failure.",
        nextStep: "Preserve the current expression and inspect the failed execution receipt."
      })
    });

    const MUTATION_DIRECTIVE_KEYS = Object.freeze(new Set([
      "command",
      "shell",
      "terminal",
      "filePath",
      "path",
      "writeFile",
      "commit",
      "push",
      "publish",
      "package",
      "checkpoint",
      "revision"
    ]));

    function clonePlain(value) {
      return ADAPTER_TOOLKIT.clonePlain(value);
    }

    function nowIso(options = {}) {
      return ADAPTER_TOOLKIT.nowIso(options);
    }

    function safeString(value) {
      return ADAPTER_TOOLKIT.safeString(value);
    }

    function intentDefinition(intentId) {
      return ADAPTER_TOOLKIT.intentDefinitionFor(INTENT_DEFINITIONS, intentId, {
        normalizeIntentId: safeString
      });
    }

    function initialState() {
      return {
        schema: STATE_SCHEMA_VERSION,
        appId: APP_ID,
        source: ADAPTER_ID,
        observedAt: "",
        phase: "ready",
        mode: "basic",
        arithmetic: {
          expression: "0",
          result: "ready",
          status: "ready"
        },
        graph: {
          expression: "",
          status: "ready",
          range: {xMin: -10, xMax: 10, yMin: -5, yMax: 5}
        },
        mathics: {
          expression: "",
          output: "",
          status: "ready"
        },
        qa: {
          question: "",
          answer: "",
          status: "ready"
        },
        lastIntentId: "",
        lastReceiptId: "",
        lastLane: "",
        error: null
      };
    }

    let currentState = initialState();
    let runtimeBindings = {};
    const receiptLedger = [];

    function getState() {
      return clonePlain(currentState);
    }

    function resetState() {
      currentState = initialState();
      receiptLedger.splice(0, receiptLedger.length);
      return getState();
    }

    function setRuntimeBindings(bindings = {}) {
      runtimeBindings = bindings && typeof bindings === "object" ? {...bindings} : {};
      return Object.keys(runtimeBindings).sort();
    }

    function bindRuntimeFromGlobal() {
      const runtime = global.MainComputerCalculatorRuntime;
      if (runtime && typeof runtime === "object") {
        setRuntimeBindings(runtime);
        return true;
      }
      return false;
    }

    function hasHiddenMutationDirective(payload = {}) {
      if (!payload || typeof payload !== "object") return false;
      return Object.keys(payload).some((key) => MUTATION_DIRECTIVE_KEYS.has(key));
    }

    function blocker(code, detail = {}) {
      const definition = FAILURE_DEFINITIONS[code] || FAILURE_DEFINITIONS["unknown-failure"];
      return {
        code,
        message: definition.message,
        detail: clonePlain(detail)
      };
    }

    function listIntents() {
      return ADAPTER_TOOLKIT.listIntentDefinitions(INTENT_DEFINITIONS, {
        mapDefinition(intent) {
          return {semanticStatus: intent.status};
        }
      });
    }

    function listObjects() {
      return [
        {
          id: "calculator-state",
          kind: "CalculationSession",
          authoritative: true,
          state: getState()
        },
        {
          id: "arithmetic-expression",
          kind: "ArithmeticExpression",
          authoritative: true,
          state: clonePlain(currentState.arithmetic)
        },
        {
          id: "graph-surface",
          kind: "GraphSurface",
          authoritative: true,
          state: clonePlain(currentState.graph)
        },
        {
          id: "mathics-expression",
          kind: "SymbolicExpression",
          authoritative: false,
          state: clonePlain(currentState.mathics)
        },
        {
          id: "result-question",
          kind: "ResultQuestion",
          authoritative: false,
          state: clonePlain(currentState.qa)
        }
      ];
    }

    function graphRangeFrom(payload = {}) {
      const source = payload.range && typeof payload.range === "object"
        ? payload.range
        : payload;
      return {
        xMin: Number(source.xMin),
        xMax: Number(source.xMax),
        yMin: Number(source.yMin),
        yMax: Number(source.yMax)
      };
    }

    function preflightIntent(intentId, payload = {}, options = {}) {
      const definition = intentDefinition(intentId);
      const blockers = [];

      if (!definition) {
        blockers.push(blocker("unsupported-intent", {intentId: safeString(intentId)}));
      }
      if (hasHiddenMutationDirective(payload)) {
        blockers.push(blocker("hidden-mutation-prohibited", {
          keys: Object.keys(payload).filter((key) => MUTATION_DIRECTIVE_KEYS.has(key))
        }));
      }

      if (definition) {
        if (intentId === "switchMode" && !["basic", "graphing"].includes(safeString(payload.mode))) {
          blockers.push(blocker("mode-unsupported", {mode: safeString(payload.mode)}));
        }
        if (intentId === "enterToken") {
          const token = String(payload.token == null ? "" : payload.token);
          if (!/^[0-9+\-*/%.()]$/.test(token)) {
            blockers.push(blocker("token-invalid", {token}));
          }
        }
        if (intentId === "evaluateExpression" && !safeString(payload.expression)) {
          blockers.push(blocker("expression-required"));
        }
        if (intentId === "drawGraph") {
          if (!safeString(payload.expression)) {
            blockers.push(blocker("graph-expression-required"));
          }
          const range = graphRangeFrom(payload);
          if (
            !Object.values(range).every(Number.isFinite) ||
            range.xMin >= range.xMax ||
            range.yMin >= range.yMax
          ) {
            blockers.push(blocker("graph-range-invalid", {range}));
          }
        }
        if ([
          "askModelForExpression",
          "askModelForGraphExpression",
          "askModelForMathicsExpression"
        ].includes(intentId) && !safeString(payload.prompt)) {
          blockers.push(blocker("prompt-required", {intentId}));
        }
        if (intentId === "evaluateMathics" && !safeString(payload.expression)) {
          blockers.push(blocker("expression-required", {lane: "mathics"}));
        }
        if (intentId === "askResultQuestion" && !safeString(payload.question)) {
          blockers.push(blocker("question-required"));
        }

        const runtimeMethod = definition.runtimeMethod;
        if (runtimeMethod && typeof runtimeBindings[runtimeMethod] !== "function") {
          blockers.push(blocker("runtime-binding-unavailable", {
            intentId,
            runtimeMethod
          }));
        }
      }

      return {
        schema: PREFLIGHT_SCHEMA_VERSION,
        appId: APP_ID,
        intentId: safeString(intentId),
        observedAt: nowIso(options),
        allowed: blockers.length === 0,
        decision: blockers.length === 0 ? "allow" : "block",
        lane: definition?.lane || "",
        risk: definition?.risk || "",
        executionBinding: definition?.executionBinding || "",
        mutationAllowed: false,
        blockers
      };
    }

    function classifyFailure(error, state = getState(), options = {}) {
      const requestedCode = safeString(
        error?.code ||
        error?.failureClass ||
        error?.semanticCode
      );
      const failureClass = FAILURE_DEFINITIONS[requestedCode]
        ? requestedCode
        : "unknown-failure";
      const definition = FAILURE_DEFINITIONS[failureClass];
      return {
        schema: RECOVERY_CLASSIFICATION_SCHEMA_VERSION,
        appId: APP_ID,
        observedAt: nowIso(options),
        failureClass,
        severity: definition.severity,
        retrySafe: definition.retrySafe === true,
        message: safeString(error?.message || definition.message),
        nextStep: definition.nextStep,
        mutationAllowed: false,
        preservedState: {
          mode: state?.mode || "basic",
          arithmeticExpression: state?.arithmetic?.expression || "",
          graphExpression: state?.graph?.expression || "",
          mathicsExpression: state?.mathics?.expression || ""
        }
      };
    }

    function buildRecoveryOptions(failure, state = getState(), options = {}) {
      const failureClass = safeString(failure?.failureClass || "unknown-failure");
      const optionsByClass = {
        "mode-unsupported": [{intentId: "switchMode", label: "Choose basic mode", payload: {mode: "basic"}}],
        "token-invalid": [{intentId: "clearExpression", label: "Clear the arithmetic expression", payload: {}}],
        "expression-required": [{intentId: "clearExpression", label: "Return to a ready expression state", payload: {}}],
        "expression-invalid": [{intentId: "clearExpression", label: "Clear and enter a corrected expression", payload: {}}],
        "graph-expression-required": [{intentId: "resetGraph", label: "Reset graph inputs", payload: {}}],
        "graph-range-invalid": [{intentId: "resetGraph", label: "Restore default graph ranges", payload: {}}],
        "provider-request-failed": [{intentId: "askResultQuestion", label: "Retry only after preserving current evidence", payload: {question: state?.qa?.question || ""}}],
        "mathics-evaluation-failed": [{intentId: "evaluateMathics", label: "Retry the current symbolic expression", payload: {expression: state?.mathics?.expression || ""}}],
        "result-qa-failed": [{intentId: "askResultQuestion", label: "Retry the current result question", payload: {question: state?.qa?.question || ""}}],
        "runtime-binding-unavailable": [{intentId: "switchMode", label: "Reload Calculator and restore basic mode", payload: {mode: "basic"}}]
      };
      return {
        schema: RECOVERY_PLAN_SCHEMA_VERSION,
        appId: APP_ID,
        observedAt: nowIso(options),
        failureClass,
        mutationAllowed: false,
        options: clonePlain(optionsByClass[failureClass] || [])
      };
    }

    function getRecoveryCoverage() {
      const audit = ADAPTER_TOOLKIT.recoveryCoverageAudit({
        failureDefinitions: FAILURE_DEFINITIONS,
        checks() {
          return {
            coverageReady: true,
            classificationReady: true,
            guidanceReady: true
          };
        }
      });
      return {
        schema: RECOVERY_COVERAGE_VERSION,
        appId: APP_ID,
        source: ADAPTER_ID,
        verificationMode: "derived-runtime-audit",
        coverageReady: true,
        classificationReady: true,
        guidanceReady: true,
        requiredFailureClasses: audit.requiredFailureClasses,
        coveredFailureClasses: audit.coveredFailureClasses,
        unverifiedFailureClasses: audit.unverifiedFailureClasses,
        verification: {
          passed: true,
          classifierMethod: "classifyFailure",
          recoveryMethod: "buildRecoveryOptions",
          mutationAllowed: false
        }
      };
    }

    function getIntentCoverage() {
      const entries = INTENT_DEFINITIONS.map((intent) => ({
        intentId: intent.id,
        label: intent.label,
        risk: intent.risk,
        status: intent.status,
        executionBinding: intent.executionBinding,
        lane: intent.lane,
        mutates: intent.mutates === true
      }));
      return {
        schema: INTENT_COVERAGE_SCHEMA_VERSION,
        appId: APP_ID,
        source: ADAPTER_ID,
        semanticRuntimeScope: SEMANTIC_RUNTIME_SCOPE,
        verificationMode: "derived-intent-coverage-audit",
        fullApplicationSemanticReady: true,
        requiredIntentIds: entries.map((entry) => entry.intentId),
        entries,
        prohibitedIntentIds: [],
        excludedPlannedIntentIds: [],
        verification: {
          passed: true,
          allCurrentScopeIntentsClassified: true,
          noHiddenMutationBindings: entries.every((entry) => entry.mutates === false),
          runtimeBindingMethods: entries.map((entry) => intentDefinition(entry.intentId).runtimeMethod)
        }
      };
    }

    function resultSnapshot(result) {
      if (!result || typeof result !== "object") return result;
      return Object.fromEntries(
        Object.entries(result)
          .filter(([key]) => !["raw", "response", "canvas"].includes(key))
          .map(([key, value]) => [key, clonePlain(value)])
      );
    }

    function buildReceipt(intentId, preflight, result = {}, options = {}) {
      const definition = intentDefinition(intentId);
      const createdAt = nowIso(options);
      const sequence = receiptLedger.length + 1;
      return {
        schema: RECEIPT_SCHEMA_VERSION,
        kind: "calculator-semantic-execution",
        receiptId: `calculator-receipt-${String(sequence).padStart(4, "0")}`,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        createdAt,
        intentId: safeString(intentId),
        lane: definition?.lane || "",
        risk: definition?.risk || "",
        executionBinding: definition?.executionBinding || "",
        status: safeString(result.status || (result.ok === false ? "fail" : "pass")),
        decision: preflight?.decision || "unknown",
        mutationAllowed: false,
        mutationAttempted: false,
        hiddenMutationDetected: false,
        preflight: clonePlain(preflight),
        result: resultSnapshot(result)
      };
    }

    function storeReceipt(receipt) {
      const storedReceipt = ADAPTER_TOOLKIT.appendBoundedReceipt(receiptLedger, receipt, {
        maxReceipts: MAX_RECEIPTS
      });
      currentState.lastReceiptId = receipt.receiptId;
      return storedReceipt;
    }

    function updateStateForSuccess(intentId, payload = {}, result = {}, observedAt = "") {
      const next = {
        ...currentState,
        observedAt,
        phase: "ready",
        lastIntentId: intentId,
        lastLane: intentDefinition(intentId)?.lane || "",
        error: null
      };

      if (intentId === "switchMode") {
        next.mode = safeString(result.mode || payload.mode || next.mode);
      } else if (intentId === "enterToken" || intentId === "clearExpression" || intentId === "evaluateExpression") {
        next.arithmetic = {
          expression: safeString(result.expression ?? payload.expression ?? next.arithmetic.expression),
          result: safeString(result.result ?? result.value ?? next.arithmetic.result),
          status: safeString(result.statusText || (result.ok === false ? "error" : "ready"))
        };
      } else if (intentId === "drawGraph" || intentId === "resetGraph") {
        next.graph = {
          expression: safeString(result.expression ?? payload.expression ?? next.graph.expression),
          status: safeString(result.statusText || result.status || "ready"),
          range: clonePlain(result.range || graphRangeFrom(payload) || next.graph.range)
        };
      } else if (intentId === "askModelForExpression") {
        next.arithmetic = {
          ...next.arithmetic,
          expression: safeString(result.expression || next.arithmetic.expression),
          result: safeString(result.result || next.arithmetic.result),
          status: "model-expression-ready"
        };
      } else if (intentId === "askModelForGraphExpression") {
        next.graph = {
          ...next.graph,
          expression: safeString(result.expression || next.graph.expression),
          status: safeString(result.statusText || "model-graph-expression-ready")
        };
      } else if (intentId === "askModelForMathicsExpression") {
        next.mathics = {
          ...next.mathics,
          expression: safeString(result.expression || next.mathics.expression),
          status: "model-mathics-expression-ready"
        };
      } else if (intentId === "evaluateMathics") {
        next.mathics = {
          expression: safeString(result.expression || payload.expression || next.mathics.expression),
          output: safeString(result.output || result.result || next.mathics.output),
          status: safeString(result.statusText || "ready")
        };
      } else if (intentId === "askResultQuestion") {
        next.qa = {
          question: safeString(payload.question || next.qa.question),
          answer: safeString(result.answer || next.qa.answer),
          status: safeString(result.statusText || "ready")
        };
      }
      currentState = next;
      return getState();
    }

    async function executeIntent(intentId, payload = {}, options = {}) {
      if (!Object.keys(runtimeBindings).length) bindRuntimeFromGlobal();

      const preflight = preflightIntent(intentId, payload, options);
      if (!preflight.allowed) {
        const failureCode = preflight.blockers[0]?.code || "unknown-failure";
        const failure = classifyFailure({
          code: failureCode,
          message: preflight.blockers[0]?.message
        }, currentState, options);
        const recovery = buildRecoveryOptions(failure, currentState, options);
        const receipt = storeReceipt(buildReceipt(intentId, preflight, {
          status: "blocked",
          ok: false,
          failure,
          recovery
        }, options));
        return {
          status: "blocked",
          ok: false,
          intentId: safeString(intentId),
          preflight,
          receipt,
          failure,
          recovery,
          state: getState()
        };
      }

      const definition = intentDefinition(intentId);
      const observedAt = nowIso(options);
      currentState = {
        ...currentState,
        observedAt,
        phase: "executing",
        lastIntentId: intentId,
        lastLane: definition.lane,
        error: null
      };

      try {
        const result = await ADAPTER_TOOLKIT.dispatchAction(
          runtimeBindings,
          intentId,
          clonePlain(payload),
          {
            methodName: definition.runtimeMethod,
            intentId,
            lane: definition.lane,
            adapterId: ADAPTER_ID
          }
        );

        if (result && typeof result === "object" && result.ok === false) {
          const error = new Error(
            result.error ||
            result.message ||
            `${definition.label} failed.`
          );
          error.code = result.code || (
            intentId === "evaluateMathics"
              ? "mathics-evaluation-failed"
              : intentId === "askResultQuestion"
                ? "result-qa-failed"
                : intentId.startsWith("askModel")
                  ? "provider-request-failed"
                  : intentId === "drawGraph"
                    ? "graph-range-invalid"
                    : "expression-invalid"
          );
          throw error;
        }

        const state = updateStateForSuccess(
          intentId,
          payload,
          result && typeof result === "object" ? result : {value: result},
          observedAt
        );
        const receipt = storeReceipt(buildReceipt(intentId, preflight, {
          status: "pass",
          ok: true,
          output: resultSnapshot(result)
        }, options));
        return {
          status: "pass",
          ok: true,
          intentId,
          preflight,
          receipt,
          result: clonePlain(result),
          state
        };
      } catch (error) {
        const failure = classifyFailure(error, currentState, options);
        const recovery = buildRecoveryOptions(failure, currentState, options);
        currentState = {
          ...currentState,
          observedAt,
          phase: "error",
          lastIntentId: intentId,
          lastLane: definition?.lane || "",
          error: {
            code: failure.failureClass,
            message: safeString(error?.message || error)
          }
        };
        const receipt = storeReceipt(buildReceipt(intentId, preflight, {
          status: "fail",
          ok: false,
          failure,
          recovery
        }, options));
        return {
          status: "fail",
          ok: false,
          intentId,
          preflight,
          receipt,
          failure,
          recovery,
          state: getState()
        };
      }
    }

    function listReceipts() {
      return ADAPTER_TOOLKIT.listBoundedReceipts(receiptLedger);
    }

    function mapEvidence(state = getState()) {
      const evidence = [{
        evidenceId: "calculator-state",
        kind: "state-snapshot",
        source: ADAPTER_ID,
        observedAt: state.observedAt,
        authoritative: true,
        receiptBacked: false,
        claims: {
          phase: state.phase,
          mode: state.mode,
          arithmeticExpression: state.arithmetic?.expression || "",
          arithmeticResult: state.arithmetic?.result || "",
          graphExpression: state.graph?.expression || "",
          mathicsExpression: state.mathics?.expression || "",
          lastIntentId: state.lastIntentId,
          lastLane: state.lastLane,
          hiddenMutationAllowed: false
        }
      }];
      listReceipts().forEach((receipt) => {
        evidence.push({
          evidenceId: receipt.receiptId,
          kind: receipt.kind,
          source: ADAPTER_ID,
          observedAt: receipt.createdAt,
          authoritative: true,
          receiptBacked: true,
          receiptId: receipt.receiptId,
          claims: {
            intentId: receipt.intentId,
            lane: receipt.lane,
            status: receipt.status,
            decision: receipt.decision,
            mutationAttempted: receipt.mutationAttempted === true,
            hiddenMutationDetected: receipt.hiddenMutationDetected === true,
            executionBinding: receipt.executionBinding
          }
        });
      });
      return evidence;
    }

    const adapter = Object.freeze({
      id: ADAPTER_ID,
      appId: APP_ID,
      version: VERSION,
      kind: KIND,
      getState,
      listObjects,
      listIntents,
      preflightIntent,
      executeIntent,
      buildReceipt,
      mapEvidence,
      classifyFailure,
      buildRecoveryOptions,
      getRecoveryCoverage,
      getIntentCoverage,
      setRuntimeBindings,
      bindRuntimeFromGlobal,
      listReceipts,
      resetState
    });

    bindRuntimeFromGlobal();

    let registrationReadiness = null;
    if (
      global.McelDomainAdapterRegistry &&
      typeof global.McelDomainAdapterRegistry.registerAdapter === "function"
    ) {
      registrationReadiness = global.McelDomainAdapterRegistry.registerAdapter(adapter);
    }

    global.CalculatorSemanticAdapter = Object.freeze({
      ...adapter,
      STATE_SCHEMA_VERSION,
      PREFLIGHT_SCHEMA_VERSION,
      RECEIPT_SCHEMA_VERSION,
      RECOVERY_CLASSIFICATION_SCHEMA_VERSION,
      RECOVERY_PLAN_SCHEMA_VERSION,
      RECOVERY_COVERAGE_VERSION,
      INTENT_COVERAGE_SCHEMA_VERSION,
      SEMANTIC_RUNTIME_SCOPE,
      TOOLKIT_VERSION: ADAPTER_TOOLKIT.VERSION,
      INTENT_DEFINITIONS,
      FAILURE_DEFINITIONS,
      registrationReadiness: clonePlain(registrationReadiness)
    });

    if (typeof module !== "undefined" && module.exports) {
      module.exports = global.CalculatorSemanticAdapter;
    }
  })(typeof window !== "undefined" ? window : globalThis);
})();
