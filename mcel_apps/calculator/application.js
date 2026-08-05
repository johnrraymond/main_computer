"use strict";

const mcel = require("@mcel/app");

// Shadow Calculator authority. The existing Calculator HTML, CSS, route, and
// runtime facade remain live while this IR is compiled and projected in
// isolation. Generated compatibility files are reconstructed in memory.
const portableApplication = {
  "application": {
    "appId": "calculator",
    "authoringStatus": "dual-authored",
    "id": "app:calculator",
    "kind": "application",
    "semanticVersion": "1",
    "targetTruthStatus": "semantic-runtime-proven",
    "title": "Calculator"
  },
  "capabilities": [
    {
      "description": "Request bounded expression assistance without granting calculator state mutation authority.",
      "id": "capability:calculator.model-assistance",
      "kind": "capability",
      "operations": [
        {
          "cancellable": false,
          "name": "arithmetic-expression",
          "runtimeMethod": "askModelForExpression"
        },
        {
          "cancellable": false,
          "name": "graph-expression",
          "runtimeMethod": "askModelForGraphExpression"
        },
        {
          "cancellable": false,
          "name": "mathics-expression",
          "runtimeMethod": "askModelForMathicsExpression"
        }
      ],
      "risk": "external-read",
      "sourceName": "modelAssistance"
    },
    {
      "description": "Evaluate one bounded symbolic expression through the existing Calculator Mathics API.",
      "id": "capability:calculator.mathics",
      "kind": "capability",
      "operations": [
        {
          "cancellable": false,
          "name": "evaluate",
          "runtimeMethod": "evaluateMathics"
        }
      ],
      "risk": "external-read",
      "sourceName": "mathics"
    },
    {
      "description": "Ask a read-only contextual question about the currently visible result.",
      "id": "capability:calculator.result-qa",
      "kind": "capability",
      "operations": [
        {
          "cancellable": false,
          "name": "ask",
          "runtimeMethod": "askResultQuestion"
        }
      ],
      "risk": "external-read",
      "sourceName": "resultQa"
    }
  ],
  "derivations": [],
  "effects": [
    {
      "allowedFinalDispositions": [
        "completed",
        "refused-before-attempt",
        "failed",
        "cancelled"
      ],
      "authority": {
        "ref": "capability:calculator.model-assistance"
      },
      "cardinality": {
        "maximum": 1,
        "minimum": 0
      },
      "cleanupObligations": [],
      "effectKind": "capability-request",
      "id": "effect:calculator.ask-model-for-expression.request",
      "kind": "effect",
      "owner": {
        "ref": "intent:calculator.ask-model-for-expression"
      },
      "requiredEvidence": [
        "operation-receipt",
        "capability-response",
        "visible-outcome"
      ],
      "risk": "external-read",
      "target": {
        "ref": "capability:calculator.model-assistance"
      }
    },
    {
      "allowedFinalDispositions": [
        "completed",
        "refused-before-attempt",
        "failed",
        "cancelled"
      ],
      "authority": {
        "ref": "capability:calculator.model-assistance"
      },
      "cardinality": {
        "maximum": 1,
        "minimum": 0
      },
      "cleanupObligations": [],
      "effectKind": "capability-request",
      "id": "effect:calculator.ask-model-for-graph-expression.request",
      "kind": "effect",
      "owner": {
        "ref": "intent:calculator.ask-model-for-graph-expression"
      },
      "requiredEvidence": [
        "operation-receipt",
        "capability-response",
        "visible-outcome"
      ],
      "risk": "external-read",
      "target": {
        "ref": "capability:calculator.model-assistance"
      }
    },
    {
      "allowedFinalDispositions": [
        "completed",
        "refused-before-attempt",
        "failed",
        "cancelled"
      ],
      "authority": {
        "ref": "capability:calculator.model-assistance"
      },
      "cardinality": {
        "maximum": 1,
        "minimum": 0
      },
      "cleanupObligations": [],
      "effectKind": "capability-request",
      "id": "effect:calculator.ask-model-for-mathics-expression.request",
      "kind": "effect",
      "owner": {
        "ref": "intent:calculator.ask-model-for-mathics-expression"
      },
      "requiredEvidence": [
        "operation-receipt",
        "capability-response",
        "visible-outcome"
      ],
      "risk": "external-read",
      "target": {
        "ref": "capability:calculator.model-assistance"
      }
    },
    {
      "allowedFinalDispositions": [
        "completed",
        "refused-before-attempt",
        "failed",
        "cancelled"
      ],
      "authority": {
        "ref": "capability:calculator.mathics"
      },
      "cardinality": {
        "maximum": 1,
        "minimum": 0
      },
      "cleanupObligations": [],
      "effectKind": "capability-request",
      "id": "effect:calculator.evaluate-mathics.request",
      "kind": "effect",
      "owner": {
        "ref": "intent:calculator.evaluate-mathics"
      },
      "requiredEvidence": [
        "operation-receipt",
        "capability-response",
        "visible-outcome"
      ],
      "risk": "external-read",
      "target": {
        "ref": "capability:calculator.mathics"
      }
    },
    {
      "allowedFinalDispositions": [
        "completed",
        "refused-before-attempt",
        "failed",
        "cancelled"
      ],
      "authority": {
        "ref": "capability:calculator.result-qa"
      },
      "cardinality": {
        "maximum": 1,
        "minimum": 0
      },
      "cleanupObligations": [],
      "effectKind": "capability-request",
      "id": "effect:calculator.ask-result-question.request",
      "kind": "effect",
      "owner": {
        "ref": "intent:calculator.ask-result-question"
      },
      "requiredEvidence": [
        "operation-receipt",
        "capability-response",
        "visible-outcome"
      ],
      "risk": "external-read",
      "target": {
        "ref": "capability:calculator.result-qa"
      }
    }
  ],
  "intents": [
    {
      "cancellable": false,
      "effectRefs": [],
      "executionBinding": "calculator-runtime.switch-mode",
      "id": "intent:calculator.switch-mode",
      "input": [],
      "invariants": [],
      "kind": "intent",
      "label": "Switch calculator mode",
      "lane": "local-ui",
      "operationKind": "interaction",
      "outcomes": [
        "completed",
        "refused",
        "failed"
      ],
      "reads": [
        {
          "ref": "state:calculator.mode"
        }
      ],
      "refusals": [],
      "risk": "read-only",
      "runtimeMethod": "switchMode",
      "sourceName": "switchMode",
      "writes": []
    },
    {
      "cancellable": false,
      "effectRefs": [],
      "executionBinding": "calculator-runtime.enter-token",
      "id": "intent:calculator.enter-token",
      "input": [],
      "invariants": [],
      "kind": "intent",
      "label": "Enter an arithmetic token",
      "lane": "local-arithmetic",
      "operationKind": "interaction",
      "outcomes": [
        "completed",
        "refused",
        "failed"
      ],
      "reads": [
        {
          "ref": "state:calculator.arithmetic-expression"
        }
      ],
      "refusals": [],
      "risk": "read-only",
      "runtimeMethod": "enterToken",
      "sourceName": "enterToken",
      "writes": []
    },
    {
      "cancellable": false,
      "effectRefs": [],
      "executionBinding": "calculator-runtime.clear-expression",
      "id": "intent:calculator.clear-expression",
      "input": [],
      "invariants": [],
      "kind": "intent",
      "label": "Clear the arithmetic expression",
      "lane": "local-arithmetic",
      "operationKind": "interaction",
      "outcomes": [
        "completed",
        "refused",
        "failed"
      ],
      "reads": [
        {
          "ref": "state:calculator.arithmetic-expression"
        }
      ],
      "refusals": [],
      "risk": "read-only",
      "runtimeMethod": "clearExpression",
      "sourceName": "clearExpression",
      "writes": []
    },
    {
      "cancellable": false,
      "effectRefs": [],
      "executionBinding": "calculator-runtime.evaluate-expression",
      "id": "intent:calculator.evaluate-expression",
      "input": [],
      "invariants": [],
      "kind": "intent",
      "label": "Evaluate a deterministic arithmetic expression",
      "lane": "local-arithmetic",
      "operationKind": "interaction",
      "outcomes": [
        "completed",
        "refused",
        "failed"
      ],
      "reads": [
        {
          "ref": "state:calculator.arithmetic-expression"
        }
      ],
      "refusals": [],
      "risk": "read-only",
      "runtimeMethod": "evaluateExpression",
      "sourceName": "evaluateExpression",
      "writes": []
    },
    {
      "cancellable": false,
      "effectRefs": [],
      "executionBinding": "calculator-runtime.draw-graph",
      "id": "intent:calculator.draw-graph",
      "input": [],
      "invariants": [],
      "kind": "intent",
      "label": "Draw a deterministic graph",
      "lane": "local-graph",
      "operationKind": "interaction",
      "outcomes": [
        "completed",
        "refused",
        "failed"
      ],
      "reads": [
        {
          "ref": "state:calculator.graph-expression"
        },
        {
          "ref": "state:calculator.graph-range"
        }
      ],
      "refusals": [],
      "risk": "read-only",
      "runtimeMethod": "drawGraph",
      "sourceName": "drawGraph",
      "writes": []
    },
    {
      "cancellable": false,
      "effectRefs": [],
      "executionBinding": "calculator-runtime.reset-graph",
      "id": "intent:calculator.reset-graph",
      "input": [],
      "invariants": [],
      "kind": "intent",
      "label": "Reset graph ranges",
      "lane": "local-graph",
      "operationKind": "interaction",
      "outcomes": [
        "completed",
        "refused",
        "failed"
      ],
      "reads": [
        {
          "ref": "state:calculator.graph-range"
        }
      ],
      "refusals": [],
      "risk": "read-only",
      "runtimeMethod": "resetGraph",
      "sourceName": "resetGraph",
      "writes": []
    },
    {
      "cancellable": false,
      "effectRefs": [
        {
          "ref": "effect:calculator.ask-model-for-expression.request"
        }
      ],
      "executionBinding": "calculator-runtime.ask-model-expression",
      "id": "intent:calculator.ask-model-for-expression",
      "input": [],
      "invariants": [],
      "kind": "intent",
      "label": "Ask a model for an arithmetic expression",
      "lane": "model-arithmetic",
      "operationKind": "capability",
      "outcomes": [
        "completed",
        "refused",
        "failed"
      ],
      "reads": [
        {
          "ref": "state:calculator.arithmetic-expression"
        }
      ],
      "refusals": [],
      "risk": "external-read",
      "runtimeMethod": "askModelForExpression",
      "sourceName": "askModelForExpression",
      "writes": []
    },
    {
      "cancellable": false,
      "effectRefs": [
        {
          "ref": "effect:calculator.ask-model-for-graph-expression.request"
        }
      ],
      "executionBinding": "calculator-runtime.ask-model-graph-expression",
      "id": "intent:calculator.ask-model-for-graph-expression",
      "input": [],
      "invariants": [],
      "kind": "intent",
      "label": "Ask a model for a graph expression",
      "lane": "model-graph",
      "operationKind": "capability",
      "outcomes": [
        "completed",
        "refused",
        "failed"
      ],
      "reads": [
        {
          "ref": "state:calculator.graph-expression"
        }
      ],
      "refusals": [],
      "risk": "external-read",
      "runtimeMethod": "askModelForGraphExpression",
      "sourceName": "askModelForGraphExpression",
      "writes": []
    },
    {
      "cancellable": false,
      "effectRefs": [
        {
          "ref": "effect:calculator.ask-model-for-mathics-expression.request"
        }
      ],
      "executionBinding": "calculator-runtime.ask-model-mathics-expression",
      "id": "intent:calculator.ask-model-for-mathics-expression",
      "input": [],
      "invariants": [],
      "kind": "intent",
      "label": "Ask a model for a Mathics expression",
      "lane": "model-mathics",
      "operationKind": "capability",
      "outcomes": [
        "completed",
        "refused",
        "failed"
      ],
      "reads": [
        {
          "ref": "state:calculator.mathics-expression"
        }
      ],
      "refusals": [],
      "risk": "external-read",
      "runtimeMethod": "askModelForMathicsExpression",
      "sourceName": "askModelForMathicsExpression",
      "writes": []
    },
    {
      "cancellable": false,
      "effectRefs": [
        {
          "ref": "effect:calculator.evaluate-mathics.request"
        }
      ],
      "executionBinding": "calculator-runtime.evaluate-mathics",
      "id": "intent:calculator.evaluate-mathics",
      "input": [],
      "invariants": [],
      "kind": "intent",
      "label": "Evaluate a symbolic Mathics expression",
      "lane": "mathics",
      "operationKind": "capability",
      "outcomes": [
        "completed",
        "refused",
        "failed"
      ],
      "reads": [
        {
          "ref": "state:calculator.mathics-expression"
        }
      ],
      "refusals": [],
      "risk": "external-read",
      "runtimeMethod": "evaluateMathics",
      "sourceName": "evaluateMathics",
      "writes": []
    },
    {
      "cancellable": false,
      "effectRefs": [
        {
          "ref": "effect:calculator.ask-result-question.request"
        }
      ],
      "executionBinding": "calculator-runtime.ask-result-question",
      "id": "intent:calculator.ask-result-question",
      "input": [],
      "invariants": [],
      "kind": "intent",
      "label": "Ask a contextual result question",
      "lane": "model-result-qa",
      "operationKind": "capability",
      "outcomes": [
        "completed",
        "refused",
        "failed"
      ],
      "reads": [
        {
          "ref": "state:calculator.result-context"
        }
      ],
      "refusals": [],
      "risk": "external-read",
      "runtimeMethod": "askResultQuestion",
      "sourceName": "askResultQuestion",
      "writes": []
    }
  ],
  "layouts": [
    {
      "id": "layout:calculator.workspace",
      "kind": "layout",
      "orderedChildren": [
        {
          "ref": "surface-node:calculator.switch-mode"
        },
        {
          "ref": "surface-node:calculator.enter-token"
        },
        {
          "ref": "surface-node:calculator.clear-expression"
        },
        {
          "ref": "surface-node:calculator.evaluate-expression"
        },
        {
          "ref": "surface-node:calculator.draw-graph"
        },
        {
          "ref": "surface-node:calculator.reset-graph"
        },
        {
          "ref": "surface-node:calculator.ask-model-for-expression"
        },
        {
          "ref": "surface-node:calculator.ask-model-for-graph-expression"
        },
        {
          "ref": "surface-node:calculator.ask-model-for-mathics-expression"
        },
        {
          "ref": "surface-node:calculator.evaluate-mathics"
        },
        {
          "ref": "surface-node:calculator.ask-result-question"
        }
      ],
      "surface": {
        "ref": "surface:calculator.workspace"
      },
      "zones": [
        "mode",
        "arithmetic",
        "graph",
        "mathics",
        "result-qa",
        "chat"
      ]
    }
  ],
  "migration": {
    "knownGaps": [
      "browser-observation-not-yet-bound",
      "candidate-not-promoted",
      "host-bound-runtime-projection-not-yet-active",
      "legacy-calculator-semantic-adapter-remains-live"
    ],
    "sourceFamily": "official-vanilla-javascript-dsl",
    "state": "dual-authored"
  },
  "models": [],
  "proof": {
    "invariants": [],
    "requiredAuthorities": [
      "visible-surface",
      "operation-receipt",
      "capability-response"
    ],
    "targetTruthStatus": "semantic-runtime-proven"
  },
  "provenance": {
    "frontend": {
      "id": "mcel.dsl.v1",
      "sourceFiles": [],
      "version": "1"
    },
    "nodeBindings": []
  },
  "scenarios": [
    {
      "id": "scenario:calculator.switch-mode",
      "intent": {
        "ref": "intent:calculator.switch-mode"
      },
      "kind": "scenario",
      "steps": [
        {
          "authority": "operation-receipt",
          "expected": "completed",
          "kind": "claim.receipt-disposition"
        }
      ]
    },
    {
      "id": "scenario:calculator.enter-token",
      "intent": {
        "ref": "intent:calculator.enter-token"
      },
      "kind": "scenario",
      "steps": [
        {
          "authority": "operation-receipt",
          "expected": "completed",
          "kind": "claim.receipt-disposition"
        }
      ]
    },
    {
      "id": "scenario:calculator.clear-expression",
      "intent": {
        "ref": "intent:calculator.clear-expression"
      },
      "kind": "scenario",
      "steps": [
        {
          "authority": "operation-receipt",
          "expected": "completed",
          "kind": "claim.receipt-disposition"
        }
      ]
    },
    {
      "id": "scenario:calculator.evaluate-expression",
      "intent": {
        "ref": "intent:calculator.evaluate-expression"
      },
      "kind": "scenario",
      "steps": [
        {
          "authority": "operation-receipt",
          "expected": "completed",
          "kind": "claim.receipt-disposition"
        }
      ]
    },
    {
      "id": "scenario:calculator.draw-graph",
      "intent": {
        "ref": "intent:calculator.draw-graph"
      },
      "kind": "scenario",
      "steps": [
        {
          "authority": "operation-receipt",
          "expected": "completed",
          "kind": "claim.receipt-disposition"
        }
      ]
    },
    {
      "id": "scenario:calculator.reset-graph",
      "intent": {
        "ref": "intent:calculator.reset-graph"
      },
      "kind": "scenario",
      "steps": [
        {
          "authority": "operation-receipt",
          "expected": "completed",
          "kind": "claim.receipt-disposition"
        }
      ]
    },
    {
      "id": "scenario:calculator.ask-model-for-expression",
      "intent": {
        "ref": "intent:calculator.ask-model-for-expression"
      },
      "kind": "scenario",
      "steps": [
        {
          "authority": "operation-receipt",
          "expected": "completed",
          "kind": "claim.receipt-disposition"
        }
      ]
    },
    {
      "id": "scenario:calculator.ask-model-for-graph-expression",
      "intent": {
        "ref": "intent:calculator.ask-model-for-graph-expression"
      },
      "kind": "scenario",
      "steps": [
        {
          "authority": "operation-receipt",
          "expected": "completed",
          "kind": "claim.receipt-disposition"
        }
      ]
    },
    {
      "id": "scenario:calculator.ask-model-for-mathics-expression",
      "intent": {
        "ref": "intent:calculator.ask-model-for-mathics-expression"
      },
      "kind": "scenario",
      "steps": [
        {
          "authority": "operation-receipt",
          "expected": "completed",
          "kind": "claim.receipt-disposition"
        }
      ]
    },
    {
      "id": "scenario:calculator.evaluate-mathics",
      "intent": {
        "ref": "intent:calculator.evaluate-mathics"
      },
      "kind": "scenario",
      "steps": [
        {
          "authority": "operation-receipt",
          "expected": "completed",
          "kind": "claim.receipt-disposition"
        }
      ]
    },
    {
      "id": "scenario:calculator.ask-result-question",
      "intent": {
        "ref": "intent:calculator.ask-result-question"
      },
      "kind": "scenario",
      "steps": [
        {
          "authority": "operation-receipt",
          "expected": "completed",
          "kind": "claim.receipt-disposition"
        }
      ]
    }
  ],
  "schema": "mcel.application-ir.v1",
  "states": [
    {
      "authority": "renderer-local",
      "id": "state:calculator.mode",
      "initial": "basic",
      "kind": "state",
      "schema": {
        "kind": "string"
      },
      "sourceName": "mode"
    },
    {
      "authority": "renderer-local",
      "id": "state:calculator.arithmetic-expression",
      "initial": "",
      "kind": "state",
      "schema": {
        "kind": "string"
      },
      "sourceName": "arithmeticExpression"
    },
    {
      "authority": "derived",
      "id": "state:calculator.arithmetic-result",
      "initial": "",
      "kind": "state",
      "schema": {
        "kind": "string"
      },
      "sourceName": "arithmeticResult"
    },
    {
      "authority": "renderer-local",
      "id": "state:calculator.graph-expression",
      "initial": "sin(x)",
      "kind": "state",
      "schema": {
        "kind": "string"
      },
      "sourceName": "graphExpression"
    },
    {
      "authority": "renderer-local",
      "id": "state:calculator.graph-range",
      "initial": {
        "xMax": 10,
        "xMin": -10,
        "yMax": 5,
        "yMin": -5
      },
      "kind": "state",
      "schema": {
        "kind": "record"
      },
      "sourceName": "graphRange"
    },
    {
      "authority": "renderer-local",
      "id": "state:calculator.mathics-expression",
      "initial": "",
      "kind": "state",
      "schema": {
        "kind": "string"
      },
      "sourceName": "mathicsExpression"
    },
    {
      "authority": "derived",
      "id": "state:calculator.mathics-result",
      "initial": "",
      "kind": "state",
      "schema": {
        "kind": "string"
      },
      "sourceName": "mathicsResult"
    },
    {
      "authority": "derived",
      "id": "state:calculator.result-context",
      "initial": "",
      "kind": "state",
      "schema": {
        "kind": "string"
      },
      "sourceName": "resultContext"
    }
  ],
  "surfaces": [
    {
      "id": "surface:calculator.workspace",
      "kind": "surface",
      "nodes": [
        {
          "id": "surface-node:calculator.switch-mode",
          "intent": {
            "ref": "intent:calculator.switch-mode"
          },
          "kind": "surface-node",
          "label": "Switch calculator mode",
          "nodeKind": "control",
          "sourceName": "switchMode"
        },
        {
          "id": "surface-node:calculator.enter-token",
          "intent": {
            "ref": "intent:calculator.enter-token"
          },
          "kind": "surface-node",
          "label": "Enter an arithmetic token",
          "nodeKind": "control",
          "sourceName": "enterToken"
        },
        {
          "id": "surface-node:calculator.clear-expression",
          "intent": {
            "ref": "intent:calculator.clear-expression"
          },
          "kind": "surface-node",
          "label": "Clear the arithmetic expression",
          "nodeKind": "control",
          "sourceName": "clearExpression"
        },
        {
          "id": "surface-node:calculator.evaluate-expression",
          "intent": {
            "ref": "intent:calculator.evaluate-expression"
          },
          "kind": "surface-node",
          "label": "Evaluate a deterministic arithmetic expression",
          "nodeKind": "control",
          "sourceName": "evaluateExpression"
        },
        {
          "id": "surface-node:calculator.draw-graph",
          "intent": {
            "ref": "intent:calculator.draw-graph"
          },
          "kind": "surface-node",
          "label": "Draw a deterministic graph",
          "nodeKind": "control",
          "sourceName": "drawGraph"
        },
        {
          "id": "surface-node:calculator.reset-graph",
          "intent": {
            "ref": "intent:calculator.reset-graph"
          },
          "kind": "surface-node",
          "label": "Reset graph ranges",
          "nodeKind": "control",
          "sourceName": "resetGraph"
        },
        {
          "id": "surface-node:calculator.ask-model-for-expression",
          "intent": {
            "ref": "intent:calculator.ask-model-for-expression"
          },
          "kind": "surface-node",
          "label": "Ask a model for an arithmetic expression",
          "nodeKind": "control",
          "sourceName": "askModelForExpression"
        },
        {
          "id": "surface-node:calculator.ask-model-for-graph-expression",
          "intent": {
            "ref": "intent:calculator.ask-model-for-graph-expression"
          },
          "kind": "surface-node",
          "label": "Ask a model for a graph expression",
          "nodeKind": "control",
          "sourceName": "askModelForGraphExpression"
        },
        {
          "id": "surface-node:calculator.ask-model-for-mathics-expression",
          "intent": {
            "ref": "intent:calculator.ask-model-for-mathics-expression"
          },
          "kind": "surface-node",
          "label": "Ask a model for a Mathics expression",
          "nodeKind": "control",
          "sourceName": "askModelForMathicsExpression"
        },
        {
          "id": "surface-node:calculator.evaluate-mathics",
          "intent": {
            "ref": "intent:calculator.evaluate-mathics"
          },
          "kind": "surface-node",
          "label": "Evaluate a symbolic Mathics expression",
          "nodeKind": "control",
          "sourceName": "evaluateMathics"
        },
        {
          "id": "surface-node:calculator.ask-result-question",
          "intent": {
            "ref": "intent:calculator.ask-result-question"
          },
          "kind": "surface-node",
          "label": "Ask a contextual result question",
          "nodeKind": "control",
          "sourceName": "askResultQuestion"
        }
      ],
      "presentationAuthority": "existing-host-html",
      "root": "#calculator-app",
      "route": "/applications/calculator",
      "sourceName": "CalculatorSurface"
    }
  ]
};

module.exports = mcel.defineApp(
  {
    id: "calculator",
    title: "Calculator",
    semanticVersion: "1",
    targetTruthStatus: "semantic-runtime-proven"
  },
  ({ir}) => ir.application(portableApplication)
);
