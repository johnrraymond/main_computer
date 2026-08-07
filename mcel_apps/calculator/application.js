"use strict";

const mcel = require("@mcel/app");

const APP_ID = "calculator";
const TITLE = "Calculator";
const TARGET_TRUTH_STATUS = "semantic-runtime-proven";

const field = Object.freeze({
  string: () => ({kind: "string"}),
  record: () => ({kind: "record"}),
});

function appRef(kind, name) {
  return `${kind}:${APP_ID}.${name}`;
}

function ref(kind, name) {
  return {ref: appRef(kind, name)};
}

function camelCase(name) {
  return name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
}

function receiptSuccessClaim() {
  return {
    kind: "claim.receipt-disposition",
    authority: "operation-receipt",
    expected: "completed",
  };
}

function createCalculatorApplication(metadata) {
  let presentation = null;
  let proof = null;
  let zones = [];
  const states = [];
  const capabilities = [];
  const intents = [];
  const effects = [];
  const surfaceNodes = [];
  const scenarios = [];
  const proofInvariants = [];

  function stateRecord(name, authority, schema, initial) {
    const record = {
      id: appRef("state", name),
      kind: "state",
      sourceName: camelCase(name),
      authority,
      schema,
      initial,
    };
    states.push(record);
    return Object.freeze({id: record.id, name, record});
  }

  function capabilityRecord(name, options = {}) {
    const operations = [];
    const record = {
      id: appRef("capability", name),
      kind: "capability",
      sourceName: options.sourceName || camelCase(name),
      risk: options.risk || "external-read",
      description: options.description || "",
      operations,
    };
    capabilities.push(record);
    return Object.freeze({
      id: record.id,
      name,
      record,
      operation(operationName, runtimeMethod) {
        operations.push({name: operationName, runtimeMethod, cancellable: false});
        return this;
      },
    });
  }

  function effectRecord(intent, capability) {
    const record = {
      id: appRef("effect", `${intent.name}.request`),
      kind: "effect",
      effectKind: "capability-request",
      owner: ref("intent", intent.name),
      risk: intent.risk,
      target: ref("capability", capability.name),
      authority: ref("capability", capability.name),
      cardinality: {minimum: 0, maximum: 1},
      allowedFinalDispositions: ["completed", "refused-before-attempt", "failed", "cancelled"],
      requiredEvidence: ["operation-receipt", "capability-response", "visible-outcome"],
      cleanupObligations: [],
    };
    effects.push(record);
    return record;
  }

  function intentRecord(name, options, capability = null) {
    const effect = capability ? effectRecord({name, risk: options.risk || "external-read"}, capability) : null;
    const record = {
      id: appRef("intent", name),
      kind: "intent",
      sourceName: options.sourceName || camelCase(name),
      label: options.label,
      operationKind: capability ? "capability" : "interaction",
      lane: options.lane,
      runtimeMethod: options.runtimeMethod,
      executionBinding: `calculator-runtime.${options.binding}`,
      cancellable: false,
      risk: options.risk || (capability ? "external-read" : "read-only"),
      input: [],
      reads: (options.reads || []).map((stateName) => ref("state", stateName)),
      writes: (options.writes || []).map((stateName) => ref("state", stateName)),
      refusals: [],
      invariants: (options.invariants || []).map((invariantName) => ref("invariant", invariantName)),
      effectRefs: effect ? [{ref: effect.id}] : [],
      outcomes: ["completed", "refused", "failed"],
    };
    intents.push(record);
    surfaceNodes.push({
      id: appRef("surface-node", name),
      kind: "surface-node",
      sourceName: record.sourceName,
      label: record.label,
      nodeKind: "control",
      intent: ref("intent", name),
    });
    scenarios.push({
      id: appRef("scenario", name),
      kind: "scenario",
      intent: ref("intent", name),
      steps: [receiptSuccessClaim()],
    });
    return Object.freeze({id: record.id, name, record});
  }

  function scenarioRecord(name, options = {}) {
    const record = {
      id: appRef("scenario", name),
      kind: "scenario",
      sourceName: options.sourceName || camelCase(name),
      label: options.label || name,
      intent: options.intent ? ref("intent", options.intent) : undefined,
      given: options.given || {},
      expect: options.expect || {},
      steps: options.steps || [],
    };
    scenarios.push(record);
    return Object.freeze({id: record.id, name, record});
  }

  function invariantRecord(name, options = {}) {
    const record = {
      id: appRef("invariant", name),
      kind: "invariant",
      sourceName: options.sourceName || camelCase(name),
      label: options.label || name,
      description: options.description || "",
      examples: options.examples || [],
    };
    proofInvariants.push(record);
    return Object.freeze({id: record.id, name, record});
  }

  return {
    presentation: {
      hostBound(name, options) {
        presentation = {
          id: appRef("surface", name),
          kind: "surface",
          sourceName: options.sourceName || "CalculatorSurface",
          route: options.route,
          root: options.root,
          presentationAuthority: options.presentationAuthority,
        };
      },
    },
    state: {
      rendererLocal(name, schema, options = {}) {
        return stateRecord(name, "renderer-local", schema, options.initial);
      },
      derived(name, schema, options = {}) {
        return stateRecord(name, "derived", schema, options.initial);
      },
    },
    capability: {
      external: capabilityRecord,
    },
    intent: {
      interaction: intentRecord,
      capabilityRequest(name, capability, options) {
        return intentRecord(name, options, capability);
      },
    },
    scenario: {
      example: scenarioRecord,
    },
    invariant: {
      semantic: invariantRecord,
    },
    layout: {
      zones(zoneNames) {
        zones = [...zoneNames];
      },
    },
    proof: {
      semanticRuntimeProven(options = {}) {
        proof = {
          invariants: proofInvariants,
          requiredAuthorities: options.requiredAuthorities || [
            "visible-surface",
            "operation-receipt",
            "capability-response",
          ],
          targetTruthStatus: TARGET_TRUTH_STATUS,
        };
      },
    },
    toIr() {
      if (!presentation) throw new Error("Calculator presentation must be declared.");
      if (!proof) throw new Error("Calculator proof contract must be declared.");

      return {
        schema: "mcel.application-ir.v1",
        application: {
          id: `app:${APP_ID}`,
          kind: "application",
          appId: APP_ID,
          semanticVersion: String(metadata.semanticVersion || "1"),
          title: metadata.title,
          targetTruthStatus: metadata.targetTruthStatus || TARGET_TRUTH_STATUS,
          authoringStatus: "dsl-authoritative",
        },
        models: [],
        states,
        derivations: [],
        intents,
        capabilities,
        effects,
        surfaces: [{...presentation, nodes: surfaceNodes}],
        layouts: [
          {
            id: appRef("layout", "workspace"),
            kind: "layout",
            surface: ref("surface", "workspace"),
            zones,
            orderedChildren: surfaceNodes.map((node) => ({ref: node.id})),
          },
        ],
        scenarios,
        proof,
        migration: {
          state: "dsl-authoritative",
          sourceFamily: "official-vanilla-javascript-dsl",
          knownGaps: [],
        },
        provenance: {
          frontend: {id: "mcel.dsl.v1", version: "1", sourceFiles: []},
          nodeBindings: [],
        },
      };
    },
  };
}

function declareCalculator(app) {
  app.presentation.hostBound("workspace", {
    route: "/applications/calculator",
    root: "#calculator-app",
    presentationAuthority: "existing-host-html",
    runtimeFacade: "MainComputerCalculatorRuntime",
  });

  app.state.rendererLocal("mode", field.string(), {initial: "basic"});
  app.state.rendererLocal("arithmetic-expression", field.string(), {initial: ""});
  app.state.derived("arithmetic-result", field.string(), {initial: ""});
  app.state.rendererLocal("unit-mode", field.string(), {initial: "auto"});
  app.state.derived("unit-result", field.record(), {
    initial: {ok: false, dimension: "", unit: "", display: ""},
  });
  app.state.rendererLocal("graph-expression", field.string(), {initial: "sin(x)"});
  app.state.rendererLocal("graph-range", field.record(), {
    initial: {xMin: -10, xMax: 10, yMin: -5, yMax: 5},
  });
  app.state.rendererLocal("mathics-expression", field.string(), {initial: ""});
  app.state.derived("mathics-result", field.string(), {initial: ""});
  app.state.derived("result-context", field.string(), {initial: ""});

  const modelAssistance = app.capability
    .external("model-assistance", {
      sourceName: "modelAssistance",
      description: "Request bounded expression assistance without granting calculator state mutation authority.",
    })
    .operation("arithmetic-expression", "askModelForExpression")
    .operation("graph-expression", "askModelForGraphExpression")
    .operation("mathics-expression", "askModelForMathicsExpression");

  const mathics = app.capability
    .external("mathics", {
      description: "Evaluate one bounded symbolic expression through the existing Calculator Mathics API.",
    })
    .operation("evaluate", "evaluateMathics");

  const resultQa = app.capability
    .external("result-qa", {
      sourceName: "resultQa",
      description: "Ask a read-only contextual question about the currently visible result.",
    })
    .operation("ask", "askResultQuestion");

  app.intent.interaction("switch-mode", {
    sourceName: "switchMode",
    runtimeMethod: "switchMode",
    binding: "switch-mode",
    label: "Switch calculator mode",
    lane: "local-ui",
    reads: ["mode"],
  });
  app.intent.interaction("enter-token", {
    sourceName: "enterToken",
    runtimeMethod: "enterToken",
    binding: "enter-token",
    label: "Enter an arithmetic token",
    lane: "local-arithmetic",
    reads: ["arithmetic-expression"],
  });
  app.intent.interaction("clear-expression", {
    sourceName: "clearExpression",
    runtimeMethod: "clearExpression",
    binding: "clear-expression",
    label: "Clear the arithmetic expression",
    lane: "local-arithmetic",
    reads: ["arithmetic-expression"],
  });
  app.intent.interaction("evaluate-expression", {
    sourceName: "evaluateExpression",
    runtimeMethod: "evaluateExpression",
    binding: "evaluate-expression",
    label: "Evaluate a deterministic arithmetic or unit expression",
    lane: "local-arithmetic",
    reads: ["arithmetic-expression", "unit-mode"],
    invariants: ["compatible-units-normalize-before-result"],
  });
  app.intent.interaction("draw-graph", {
    sourceName: "drawGraph",
    runtimeMethod: "drawGraph",
    binding: "draw-graph",
    label: "Draw a deterministic graph",
    lane: "local-graph",
    reads: ["graph-expression", "graph-range"],
  });
  app.intent.interaction("reset-graph", {
    sourceName: "resetGraph",
    runtimeMethod: "resetGraph",
    binding: "reset-graph",
    label: "Reset graph ranges",
    lane: "local-graph",
    reads: ["graph-range"],
  });

  app.intent.capabilityRequest("ask-model-for-expression", modelAssistance, {
    sourceName: "askModelForExpression",
    runtimeMethod: "askModelForExpression",
    binding: "ask-model-expression",
    label: "Ask a model for an arithmetic expression",
    lane: "model-arithmetic",
    reads: ["arithmetic-expression"],
  });
  app.intent.capabilityRequest("ask-model-for-graph-expression", modelAssistance, {
    sourceName: "askModelForGraphExpression",
    runtimeMethod: "askModelForGraphExpression",
    binding: "ask-model-graph-expression",
    label: "Ask a model for a graph expression",
    lane: "model-graph",
    reads: ["graph-expression"],
  });
  app.intent.capabilityRequest("ask-model-for-mathics-expression", modelAssistance, {
    sourceName: "askModelForMathicsExpression",
    runtimeMethod: "askModelForMathicsExpression",
    binding: "ask-model-mathics-expression",
    label: "Ask a model for a Mathics expression",
    lane: "model-mathics",
    reads: ["mathics-expression"],
  });
  app.intent.capabilityRequest("evaluate-mathics", mathics, {
    sourceName: "evaluateMathics",
    runtimeMethod: "evaluateMathics",
    binding: "evaluate-mathics",
    label: "Evaluate a symbolic Mathics expression",
    lane: "mathics",
    reads: ["mathics-expression"],
  });
  app.intent.capabilityRequest("ask-result-question", resultQa, {
    sourceName: "askResultQuestion",
    runtimeMethod: "askResultQuestion",
    binding: "ask-result-question",
    label: "Ask a contextual result question",
    lane: "model-result-qa",
    reads: ["result-context"],
  });

  app.invariant.semantic("compatible-units-normalize-before-result", {
    label: "Compatible units normalize before result",
    description: "Compatible metric length and time quantities normalize deterministically before visible result shaping.",
    examples: ["3 m + 40 cm = 3.4 m", "2 min + 30 s = 2.5 min", "3 m + 2 s is rejected"],
  });

  app.scenario.example("metric-length-addition", {
    label: "Metric length addition",
    intent: "evaluate-expression",
    given: {unitMode: "auto", expression: "3 m + 40 cm"},
    expect: {ok: true, result: "3.4 m", dimension: "length"},
  });
  app.scenario.example("incompatible-unit-addition", {
    label: "Incompatible unit addition",
    intent: "evaluate-expression",
    given: {unitMode: "auto", expression: "3 m + 2 s"},
    expect: {ok: false, code: "unit-dimension-mismatch"},
  });
  app.scenario.example("metric-length-scalar-arithmetic", {
    label: "Metric length scalar arithmetic",
    intent: "evaluate-expression",
    given: {unitMode: "auto", expression: "2 * 3 m"},
    expect: {ok: true, result: "6 m", dimension: "length"},
  });
  app.scenario.example("metric-time-normalization", {
    label: "Metric time normalization",
    intent: "evaluate-expression",
    given: {unitMode: "auto", expression: "2 min + 30 s"},
    expect: {ok: true, result: "2.5 min", dimension: "time"},
  });
  app.scenario.example("same-dimension-unit-ratio", {
    label: "Same-dimension unit ratio",
    intent: "evaluate-expression",
    given: {unitMode: "auto", expression: "120 s / 2 min"},
    expect: {ok: true, result: "1"},
  });

  app.layout.zones(["mode", "arithmetic", "graph", "mathics", "result-qa", "chat"]);
  app.proof.semanticRuntimeProven();
}

function buildApplicationIr() {
  const app = createCalculatorApplication({
    id: APP_ID,
    title: TITLE,
    semanticVersion: "1",
    targetTruthStatus: TARGET_TRUTH_STATUS,
  });
  declareCalculator(app);
  return app.toIr();
}

module.exports = mcel.defineApp(
  {
    id: APP_ID,
    title: TITLE,
    semanticVersion: "1",
    targetTruthStatus: TARGET_TRUTH_STATUS,
  },
  ({ir}) => ir.application(buildApplicationIr())
);
