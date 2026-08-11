"use strict";

// Canonical MCEL app-authoring example.
// Calculator is the repo's real host-bound MCEL app. Use this file to learn
// the current mcel.defineApp authoring surface; Counter and Workbench are
// retained fixtures for compatibility/projection/proof paths, not canonical
// new-app examples.

const mcel = require("@mcel/app");

const APP_ID = "calculator";
const TITLE = "Calculator";
const TARGET_TRUTH_STATUS = "semantic-runtime-proven";

const CALCULATOR_SEMANTIC_SURFACE = {
  id: "calculator.semantic-surface.semantic-runtime-workspace",
  surfaceId: "calculator.surface.workspace",
  presentationAuthority: "existing-host-html",
  runtimeFacade: "MainComputerCalculatorRuntime",
  regions: [
    {
      id: "root",
      role: "application-root",
      selector: "#calculator-app"
    },
    {
      id: "shell",
      role: "calculation-shell",
      selector: ".calculator-shell"
    },
    {
      id: "mode-switch",
      role: "calculation-mode-selector",
      selector: ".calculator-mode-switch"
    },
    {
      id: "workspace",
      role: "primary-computation-workspace",
      selector: ".calculator-workspace",
      primary: true,
      surface: "workspace"
    },
    {
      id: "arithmetic",
      role: "deterministic-arithmetic-lane",
      selector: ".calculator-basic-pane"
    },
    {
      id: "graphing",
      role: "deterministic-graphing-lane",
      selector: "#calculator-graphing-panel"
    },
    {
      id: "mathics",
      role: "explicit-symbolic-lane",
      selector: "#calculator-mathics-panel"
    },
    {
      id: "result-qa",
      role: "result-question-lane",
      selector: "#calculator-qa-panel"
    },
    {
      id: "chat",
      role: "calculation-context-companion",
      selector: "#calculator-chat-panel"
    }
  ],
  controls: [
    {
      id: "mode-basic",
      selector: "#calculator-mode-basic",
      intent: "switchMode"
    },
    {
      id: "mode-graphing",
      selector: "#calculator-mode-graphing",
      intent: "switchMode"
    },
    {
      id: "enter-token",
      selector: "[data-calc-key]",
      intent: "enterToken"
    },
    {
      id: "clear-expression",
      selector: "[data-calc-action='clear']",
      intent: "clearExpression"
    },
    {
      id: "evaluate-expression",
      selector: "[data-calc-action='equals']",
      intent: "evaluateExpression"
    },
    {
      id: "ask-model-for-expression",
      selector: "#calculator-ask-model",
      intent: "askModelForExpression"
    },
    {
      id: "ask-model-for-graph-expression",
      selector: "#calculator-scientific-ask-model",
      intent: "askModelForGraphExpression"
    },
    {
      id: "draw-graph",
      selector: "#calculator-graph-draw",
      intent: "drawGraph"
    },
    {
      id: "reset-graph",
      selector: "#calculator-graph-reset",
      intent: "resetGraph"
    },
    {
      id: "ask-model-for-mathics-expression",
      selector: "#calculator-mathics-ask-model",
      intent: "askModelForMathicsExpression"
    },
    {
      id: "evaluate-mathics",
      selector: "#calculator-mathics-evaluate",
      intent: "evaluateMathics"
    },
    {
      id: "ask-result-question",
      selector: "#calculator-qa-ask",
      intent: "askResultQuestion"
    }
  ],
  forbiddenDefaultRegions: []
};

const CALCULATOR_LAYOUT_GRAMMAR = {
  id: "calculator.layout.semantic-runtime-workspace",
  rootSelector: ".calculator-shell",
  presentationAuthority: "existing-host-html",
  regions: [
    {
      id: "shell",
      selector: ".calculator-shell",
      layout: "grid",
      children: ["mode-switch", "workspace"]
    },
    {
      id: "mode-switch",
      selector: ".calculator-mode-switch"
    },
    {
      id: "workspace",
      selector: ".calculator-workspace",
      layout: "grid",
      children: ["arithmetic", "graphing", "mathics", "chat"]
    },
    {
      id: "arithmetic",
      selector: ".calculator-basic-pane",
      children: ["result-qa"]
    },
    {
      id: "result-qa",
      selector: "#calculator-qa-panel"
    },
    {
      id: "graphing",
      selector: "#calculator-graphing-panel"
    },
    {
      id: "mathics",
      selector: "#calculator-mathics-panel"
    },
    {
      id: "chat",
      selector: "#calculator-chat-panel"
    }
  ],
  constraints: [
    {
      id: "workspace-primary-nonzero",
      selector: ".calculator-workspace",
      minWidth: 420,
      minHeight: 320
    },
    {
      id: "mode-switch-visible",
      selector: ".calculator-mode-switch",
      minHeight: 44
    },
    {
      id: "arithmetic-lane-nonzero",
      selector: ".calculator-basic-pane",
      minWidth: 280,
      minHeight: 320
    },
    {
      id: "graphing-lane-nonzero",
      selector: "#calculator-graphing-panel",
      minWidth: 320,
      minHeight: 320
    },
    {
      id: "mathics-lane-nonzero",
      selector: "#calculator-mathics-panel",
      minWidth: 280,
      minHeight: 320
    },
    {
      id: "chat-companion-visible",
      selector: "#calculator-chat-panel",
      minHeight: 180
    }
  ]
};


function declareCalculator(app) {
  app.presentation.hostBound("workspace", {
    route: "/applications/calculator",
    root: "#calculator-app",
    presentationAuthority: "existing-host-html",
    runtimeFacade: "MainComputerCalculatorRuntime",
  });
  app.presentation.semanticSurface(CALCULATOR_SEMANTIC_SURFACE);
  app.layout.grammar(CALCULATOR_LAYOUT_GRAMMAR);

  app.state.rendererLocal("mode", app.field.string(), {initial: "basic"});
  app.state.rendererLocal("arithmetic-expression", app.field.string(), {initial: ""});
  app.state.derived("arithmetic-result", app.field.string(), {initial: ""});
  app.state.rendererLocal("unit-mode", app.field.string(), {initial: "auto"});
  app.state.derived("unit-result", app.field.record(), {
    initial: {ok: false, dimension: "", unit: "", display: ""},
  });
  app.state.rendererLocal("graph-expression", app.field.string(), {initial: "sin(x)"});
  app.state.rendererLocal("graph-range", app.field.record(), {
    initial: {xMin: -10, xMax: 10, yMin: -5, yMax: 5},
  });
  app.state.rendererLocal("mathics-expression", app.field.string(), {initial: ""});
  app.state.derived("mathics-result", app.field.string(), {initial: ""});
  app.state.derived("result-context", app.field.string(), {initial: ""});

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


module.exports = mcel.defineApp(
  {
    id: APP_ID,
    title: TITLE,
    semanticVersion: "1",
    targetTruthStatus: TARGET_TRUTH_STATUS,
  },
  ({application}) => application.hostBound(declareCalculator)
);
