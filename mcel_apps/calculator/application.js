"use strict";

const mcel = require("@mcel/app");

const APP_ID = "calculator";
const TITLE = "Calculator";
const TARGET_TRUTH_STATUS = "semantic-runtime-proven";

const HOST_SURFACE = Object.freeze({
  route: "/applications/calculator",
  root: "#calculator-app",
  presentationAuthority: "existing-host-html",
  runtimeFacade: "MainComputerCalculatorRuntime",
});

const STATE_FIELDS = Object.freeze([
  ["mode", "renderer-local", "string", "basic"],
  ["arithmetic-expression", "renderer-local", "string", ""],
  ["arithmetic-result", "derived", "string", ""],
  ["graph-expression", "renderer-local", "string", "sin(x)"],
  ["graph-range", "renderer-local", "record", {xMin: -10, xMax: 10, yMin: -5, yMax: 5}],
  ["mathics-expression", "renderer-local", "string", ""],
  ["mathics-result", "derived", "string", ""],
  ["result-context", "derived", "string", ""],
]);

const CAPABILITY_LANES = Object.freeze([
  {
    name: "model-assistance",
    sourceName: "modelAssistance",
    risk: "external-read",
    description: "Request bounded expression assistance without granting calculator state mutation authority.",
    operations: [
      ["arithmetic-expression", "askModelForExpression"],
      ["graph-expression", "askModelForGraphExpression"],
      ["mathics-expression", "askModelForMathicsExpression"],
    ],
  },
  {
    name: "mathics",
    sourceName: "mathics",
    risk: "external-read",
    description: "Evaluate one bounded symbolic expression through the existing Calculator Mathics API.",
    operations: [["evaluate", "evaluateMathics"]],
  },
  {
    name: "result-qa",
    sourceName: "resultQa",
    risk: "external-read",
    description: "Ask a read-only contextual question about the currently visible result.",
    operations: [["ask", "askResultQuestion"]],
  },
]);

const INTENT_DEFINITIONS = Object.freeze([
  {
    name: "switch-mode",
    sourceName: "switchMode",
    runtimeMethod: "switchMode",
    binding: "switch-mode",
    label: "Switch calculator mode",
    lane: "local-ui",
    risk: "read-only",
    reads: ["mode"],
  },
  {
    name: "enter-token",
    sourceName: "enterToken",
    runtimeMethod: "enterToken",
    binding: "enter-token",
    label: "Enter an arithmetic token",
    lane: "local-arithmetic",
    risk: "read-only",
    reads: ["arithmetic-expression"],
  },
  {
    name: "clear-expression",
    sourceName: "clearExpression",
    runtimeMethod: "clearExpression",
    binding: "clear-expression",
    label: "Clear the arithmetic expression",
    lane: "local-arithmetic",
    risk: "read-only",
    reads: ["arithmetic-expression"],
  },
  {
    name: "evaluate-expression",
    sourceName: "evaluateExpression",
    runtimeMethod: "evaluateExpression",
    binding: "evaluate-expression",
    label: "Evaluate a deterministic arithmetic expression",
    lane: "local-arithmetic",
    risk: "read-only",
    reads: ["arithmetic-expression"],
  },
  {
    name: "draw-graph",
    sourceName: "drawGraph",
    runtimeMethod: "drawGraph",
    binding: "draw-graph",
    label: "Draw a deterministic graph",
    lane: "local-graph",
    risk: "read-only",
    reads: ["graph-expression", "graph-range"],
  },
  {
    name: "reset-graph",
    sourceName: "resetGraph",
    runtimeMethod: "resetGraph",
    binding: "reset-graph",
    label: "Reset graph ranges",
    lane: "local-graph",
    risk: "read-only",
    reads: ["graph-range"],
  },
  {
    name: "ask-model-for-expression",
    sourceName: "askModelForExpression",
    runtimeMethod: "askModelForExpression",
    binding: "ask-model-expression",
    label: "Ask a model for an arithmetic expression",
    lane: "model-arithmetic",
    risk: "external-read",
    reads: ["arithmetic-expression"],
    capability: "model-assistance",
  },
  {
    name: "ask-model-for-graph-expression",
    sourceName: "askModelForGraphExpression",
    runtimeMethod: "askModelForGraphExpression",
    binding: "ask-model-graph-expression",
    label: "Ask a model for a graph expression",
    lane: "model-graph",
    risk: "external-read",
    reads: ["graph-expression"],
    capability: "model-assistance",
  },
  {
    name: "ask-model-for-mathics-expression",
    sourceName: "askModelForMathicsExpression",
    runtimeMethod: "askModelForMathicsExpression",
    binding: "ask-model-mathics-expression",
    label: "Ask a model for a Mathics expression",
    lane: "model-mathics",
    risk: "external-read",
    reads: ["mathics-expression"],
    capability: "model-assistance",
  },
  {
    name: "evaluate-mathics",
    sourceName: "evaluateMathics",
    runtimeMethod: "evaluateMathics",
    binding: "evaluate-mathics",
    label: "Evaluate a symbolic Mathics expression",
    lane: "mathics",
    risk: "external-read",
    reads: ["mathics-expression"],
    capability: "mathics",
  },
  {
    name: "ask-result-question",
    sourceName: "askResultQuestion",
    runtimeMethod: "askResultQuestion",
    binding: "ask-result-question",
    label: "Ask a contextual result question",
    lane: "model-result-qa",
    risk: "external-read",
    reads: ["result-context"],
    capability: "result-qa",
  },
]);

const LAYOUT_ZONES = Object.freeze(["mode", "arithmetic", "graph", "mathics", "result-qa", "chat"]);
const RECEIPT_SUCCESS = Object.freeze([{kind: "claim.receipt-disposition", authority: "operation-receipt", expected: "completed"}]);

function appRef(kind, name) {
  return `${kind}:${APP_ID}.${name}`;
}

function ref(kind, name) {
  return {ref: appRef(kind, name)};
}

function stateRecord([name, authority, schemaKind, initial]) {
  return {
    id: appRef("state", name),
    kind: "state",
    sourceName: name.includes("-") ? name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()) : name,
    authority,
    schema: {kind: schemaKind},
    initial,
  };
}

function capabilityRecord(lane) {
  return {
    id: appRef("capability", lane.name),
    kind: "capability",
    sourceName: lane.sourceName,
    risk: lane.risk,
    description: lane.description,
    operations: lane.operations.map(([name, runtimeMethod]) => ({name, runtimeMethod, cancellable: false})),
  };
}

function effectId(intentName) {
  return appRef("effect", `${intentName}.request`);
}

function effectRecord(intent) {
  return {
    id: effectId(intent.name),
    kind: "effect",
    effectKind: "capability-request",
    owner: ref("intent", intent.name),
    risk: intent.risk,
    target: ref("capability", intent.capability),
    authority: ref("capability", intent.capability),
    cardinality: {minimum: 0, maximum: 1},
    allowedFinalDispositions: ["completed", "refused-before-attempt", "failed", "cancelled"],
    requiredEvidence: ["operation-receipt", "capability-response", "visible-outcome"],
    cleanupObligations: [],
  };
}

function intentRecord(intent) {
  const effectRefs = intent.capability ? [{ref: effectId(intent.name)}] : [];
  return {
    id: appRef("intent", intent.name),
    kind: "intent",
    sourceName: intent.sourceName,
    label: intent.label,
    operationKind: intent.capability ? "capability" : "interaction",
    lane: intent.lane,
    runtimeMethod: intent.runtimeMethod,
    executionBinding: `calculator-runtime.${intent.binding}`,
    cancellable: false,
    risk: intent.risk,
    input: [],
    reads: intent.reads.map((name) => ref("state", name)),
    writes: [],
    refusals: [],
    invariants: [],
    effectRefs,
    outcomes: ["completed", "refused", "failed"],
  };
}

function surfaceNode(intent) {
  return {
    id: appRef("surface-node", intent.name),
    kind: "surface-node",
    sourceName: intent.sourceName,
    label: intent.label,
    nodeKind: "control",
    intent: ref("intent", intent.name),
  };
}

function scenarioRecord(intent) {
  return {
    id: appRef("scenario", intent.name),
    kind: "scenario",
    intent: ref("intent", intent.name),
    steps: RECEIPT_SUCCESS,
  };
}

function buildApplicationIr() {
  const capabilityIntents = INTENT_DEFINITIONS.filter((intent) => intent.capability);
  const surfaceNodes = INTENT_DEFINITIONS.map(surfaceNode);

  return {
    schema: "mcel.application-ir.v1",
    application: {
      id: `app:${APP_ID}`,
      kind: "application",
      appId: APP_ID,
      semanticVersion: "1",
      title: TITLE,
      targetTruthStatus: TARGET_TRUTH_STATUS,
      authoringStatus: "dsl-authoritative",
    },
    models: [],
    states: STATE_FIELDS.map(stateRecord),
    derivations: [],
    intents: INTENT_DEFINITIONS.map(intentRecord),
    capabilities: CAPABILITY_LANES.map(capabilityRecord),
    effects: capabilityIntents.map(effectRecord),
    surfaces: [
      {
        id: appRef("surface", "workspace"),
        kind: "surface",
        sourceName: "CalculatorSurface",
        route: HOST_SURFACE.route,
        root: HOST_SURFACE.root,
        presentationAuthority: HOST_SURFACE.presentationAuthority,
        nodes: surfaceNodes,
      },
    ],
    layouts: [
      {
        id: appRef("layout", "workspace"),
        kind: "layout",
        surface: ref("surface", "workspace"),
        zones: LAYOUT_ZONES,
        orderedChildren: INTENT_DEFINITIONS.map((intent) => ref("surface-node", intent.name)),
      },
    ],
    scenarios: INTENT_DEFINITIONS.map(scenarioRecord),
    proof: {
      invariants: [],
      requiredAuthorities: ["visible-surface", "operation-receipt", "capability-response"],
      targetTruthStatus: TARGET_TRUTH_STATUS,
    },
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
