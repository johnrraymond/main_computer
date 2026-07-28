var McelCalculatorSurface = (() => {
  "use strict";

  const contractVersion = "mcel.calculator-surface.v1";
  const surfaceId = "calculator.surface.workspace";
  const surfaceContract = "calculator.contract.semantic-surface-v1";
  const runtimeContractId = "calculator.contract.default.app-health";
  const channel = "CALCULATOR";

  const extractorsApi = (() => {
    if (typeof McelSurfaceExtractors !== "undefined") return McelSurfaceExtractors;
    if (typeof window !== "undefined" && window.McelSurfaceExtractors) return window.McelSurfaceExtractors;
    return null;
  })();

  const fitContractApi = (() => {
    if (typeof McelSurfaceFitContract !== "undefined") return McelSurfaceFitContract;
    if (typeof window !== "undefined" && window.McelSurfaceFitContract) return window.McelSurfaceFitContract;
    if (typeof window !== "undefined" && window.MCEL?.surfaceFitContract) return window.MCEL.surfaceFitContract;
    return null;
  })();

  function setAttrs(element, attrs) {
    if (!element || !attrs) return element;
    if (typeof element.setAttribute !== "function" && typeof element.forEach === "function") {
      element.forEach((item) => setAttrs(item, attrs));
      return element;
    }
    if (typeof element.setAttribute !== "function") return element;
    Object.entries(attrs).forEach(([name, value]) => {
      if (value === undefined || value === null || value === "") return;
      element.setAttribute(name, String(value));
    });
    return element;
  }

  function applyFitPolicy(element, policy, options = {}) {
    if (fitContractApi && typeof fitContractApi.applyFitPolicy === "function") {
      try {
        return fitContractApi.applyFitPolicy(element, policy, options);
      } catch {}
    }
    const attrs = {"data-mcel-fit-policy": policy};
    if (options.readable) attrs["data-mcel-readable"] = "true";
    if (options.visualOwner) attrs["data-mcel-visual-owner"] = options.visualOwner;
    if (options.fitRole) attrs["data-mcel-fit-role"] = options.fitRole;
    if (options.required !== undefined) attrs["data-mcel-fit-required"] = options.required ? "true" : "false";
    return setAttrs(element, attrs);
  }

  function staticSurfaceAttrs() {
    return Object.freeze({
      "data-mcel-surface-id": surfaceId,
      "data-mcel-surface-kind": "application-surface",
      "data-mcel-surface-role": "multi-lane-computation-workbench",
      "data-mcel-surface-contract": surfaceContract,
      "data-mcel-authoritative": "true",
      "data-mcel-renderer": "calculator.runtime-dom",
      "data-mcel-projection": "html",
      "data-layout-viewport-width": "1440",
      "data-layout-viewport-height": "900",
      "data-layout-safe-margin": "16",
      "data-mcel-visual-owner": surfaceId,
      "data-mcel-layout-zone": surfaceId
    });
  }

  function regionAttrs(id, role, x, y, width, height) {
    const regionId = `calculator.region.${id}`;
    return Object.freeze({
      "data-mcel-region": regionId,
      "data-mcel-region-role": role,
      "data-layout-x": x,
      "data-layout-y": y,
      "data-layout-region-width": width,
      "data-layout-region-height": height,
      "data-mcel-visual-owner": regionId,
      "data-mcel-zone": regionId
    });
  }

  function nodeAttrs(id, type, label, region, layout, symbol) {
    const nodeId = `calculator.node.${id}`;
    const regionId = `calculator.region.${region}`;
    return Object.freeze({
      "data-mcel-node-id": nodeId,
      "data-mcel-node-type": type,
      "data-mcel-node-label": label,
      "data-mcel-channel": channel,
      "data-mcel-source": "calculator.runtime-dom",
      "data-mcel-provenance": "patch:31-calculator-semantic-surface",
      "data-mcel-symbol": symbol,
      "data-mcel-home-region": regionId,
      "data-mcel-actual-region": regionId,
      "data-mcel-teleported": "false",
      "data-layout-anchor-x": layout.x,
      "data-layout-anchor-y": layout.y,
      "data-layout-width": layout.width,
      "data-layout-height": layout.height,
      "data-layout-z": layout.z || 2,
      "data-layout-region": regionId,
      "data-layout-ports": "north,south,east,west"
    });
  }

  function edgeAttrs(id, kind, from, to, relation, routeKind, fromPort, toPort, z = 1) {
    return Object.freeze({
      "data-mcel-edge-id": `calculator.edge.${id}`,
      "data-mcel-edge-kind": kind,
      "data-mcel-from": `calculator.node.${from}`,
      "data-mcel-to": `calculator.node.${to}`,
      "data-mcel-relation": relation,
      "data-mcel-causal-link": "false",
      "data-mcel-allowed-inferences": "calculation_lane,derived_evidence,explicit_helper_context",
      "data-mcel-forbidden-inferences": "filesystem_mutation,repository_mutation,shell_execution,provider_fallback",
      "data-layout-route-kind": routeKind || "orthogonal",
      "data-layout-from-port": fromPort || "east",
      "data-layout-to-port": toPort || "west",
      "data-layout-z": z
    });
  }

  function controlAttrs(id, action, reveals, layout) {
    return Object.freeze({
      "data-mcel-control": `calculator.control.${id}`,
      "data-mcel-control-action": action,
      "data-mcel-reveals": reveals ? `calculator.node.${reveals}` : "",
      "data-layout-anchor-x": layout.x,
      "data-layout-anchor-y": layout.y,
      "data-layout-width": layout.width,
      "data-layout-height": layout.height,
      "data-layout-z": layout.z || 5
    });
  }

  function staticRegionRecords() {
    return Object.freeze([
      regionAttrs("mode-switch", "calculation-mode-selector", 16, 16, 1408, 56),
      regionAttrs("arithmetic", "deterministic-arithmetic-lane", 16, 88, 320, 520),
      regionAttrs("graphing", "deterministic-graphing-lane", 352, 88, 560, 520),
      regionAttrs("mathics", "explicit-symbolic-lane", 928, 88, 496, 520),
      regionAttrs("chat", "calculation-context-companion", 16, 624, 1408, 260)
    ]);
  }

  function staticNodeRecords() {
    return Object.freeze([
      nodeAttrs("mode-state", "calculation_mode", "Active calculation mode", "mode-switch", {x: 184, y: 44, width: 248, height: 36, z: 3}, "⇄"),
      nodeAttrs("arithmetic-lane", "deterministic_arithmetic_lane", "Deterministic arithmetic lane", "arithmetic", {x: 176, y: 176, width: 248, height: 72, z: 3}, "="),
      nodeAttrs("result-question-lane", "result_question_lane", "Result question lane", "arithmetic", {x: 176, y: 500, width: 248, height: 72, z: 3}, "?"),
      nodeAttrs("graphing-lane", "deterministic_graph_lane", "Deterministic graphing lane", "graphing", {x: 632, y: 176, width: 360, height: 72, z: 3}, "⌁"),
      nodeAttrs("mathics-lane", "explicit_symbolic_lane", "Explicit Mathics lane", "mathics", {x: 1176, y: 176, width: 320, height: 72, z: 3}, "∫"),
      nodeAttrs("model-helper-lane", "explicit_model_helper_lane", "Explicit model helper lane", "chat", {x: 440, y: 714, width: 360, height: 72, z: 3}, "✦"),
      nodeAttrs("chat-context", "calculation_context", "Calculation chat context", "chat", {x: 1000, y: 714, width: 360, height: 72, z: 3}, "◫")
    ]);
  }

  function staticEdgeRecords() {
    return Object.freeze([
      edgeAttrs("mode-selects-arithmetic", "SELECTS", "mode-state", "arithmetic-lane", "mode_selects_arithmetic_lane", "orthogonal", "south", "north"),
      edgeAttrs("mode-selects-graphing", "SELECTS", "mode-state", "graphing-lane", "mode_selects_graphing_lane", "orthogonal", "south", "north"),
      edgeAttrs("arithmetic-grounds-question", "GROUNDS", "arithmetic-lane", "result-question-lane", "deterministic_result_grounds_question_context", "straight", "south", "north"),
      edgeAttrs("arithmetic-informs-helper", "INFORMS", "arithmetic-lane", "model-helper-lane", "arithmetic_state_informs_explicit_helper", "orthogonal", "south", "north"),
      edgeAttrs("graphing-informs-helper", "INFORMS", "graphing-lane", "model-helper-lane", "graph_state_informs_explicit_helper", "orthogonal", "south", "north"),
      edgeAttrs("mathics-informs-helper", "INFORMS", "mathics-lane", "model-helper-lane", "symbolic_state_informs_explicit_helper", "orthogonal", "south", "north"),
      edgeAttrs("helper-supports-chat", "SUPPORTS", "model-helper-lane", "chat-context", "explicit_helper_evidence_supports_chat_context", "straight", "east", "west")
    ]);
  }

  function staticControlRecords() {
    return Object.freeze([
      controlAttrs("mode-basic", "switch_to_basic_mode", "arithmetic-lane", {x: 112, y: 44, width: 160, height: 34}),
      controlAttrs("mode-graphing", "switch_to_graphing_mode", "graphing-lane", {x: 288, y: 44, width: 160, height: 34}),
      controlAttrs("evaluate-expression", "evaluate_deterministic_expression", "arithmetic-lane", {x: 176, y: 392, width: 248, height: 42}),
      controlAttrs("ask-expression-model", "request_explicit_expression_helper", "model-helper-lane", {x: 176, y: 128, width: 180, height: 36}),
      controlAttrs("ask-result-question", "ask_question_about_visible_result", "result-question-lane", {x: 176, y: 560, width: 180, height: 36}),
      controlAttrs("ask-graph-model", "request_explicit_graph_helper", "model-helper-lane", {x: 520, y: 128, width: 180, height: 36}),
      controlAttrs("draw-graph", "draw_deterministic_graph", "graphing-lane", {x: 568, y: 520, width: 140, height: 36}),
      controlAttrs("reset-graph", "reset_graph_state", "graphing-lane", {x: 724, y: 520, width: 140, height: 36}),
      controlAttrs("ask-mathics-model", "request_explicit_mathics_helper", "model-helper-lane", {x: 1088, y: 128, width: 180, height: 36}),
      controlAttrs("evaluate-mathics", "evaluate_explicit_mathics_expression", "mathics-lane", {x: 1176, y: 520, width: 180, height: 36})
    ]);
  }

  function buildStaticSurfaceRidgeRecords() {
    return Object.freeze([
      staticSurfaceAttrs(),
      ...staticRegionRecords(),
      ...staticNodeRecords(),
      ...staticEdgeRecords(),
      ...staticControlRecords()
    ]);
  }

  function ensureSemanticCarrier(app) {
    if (!app || typeof app.querySelector !== "function") return null;
    let carrier = app.querySelector("#calculator-mcel-surface-carriers");
    if (!carrier && typeof document !== "undefined" && typeof document.createElement === "function") {
      carrier = document.createElement("div");
      carrier.id = "calculator-mcel-surface-carriers";
      carrier.hidden = true;
      carrier.setAttribute("aria-hidden", "true");
      app.appendChild(carrier);
    }
    return carrier;
  }

  function writeCarrierRecord(carrier, attrs, index, kind) {
    if (!carrier || !attrs) return null;
    const doc = carrier.ownerDocument || (typeof document !== "undefined" ? document : null);
    if (!doc?.createElement) return null;
    let node = carrier.querySelector(`[data-calculator-mcel-carrier="${kind}.${index}"]`);
    if (!node) {
      node = doc.createElement("i");
      node.hidden = true;
      node.setAttribute("data-calculator-mcel-carrier", `${kind}.${index}`);
      carrier.appendChild(node);
    }
    setAttrs(node, attrs);
    return node;
  }

  function applyStaticSurfaceRidges(root) {
    const scope = root || (typeof document !== "undefined" ? document : null);
    if (!scope || typeof scope.querySelector !== "function") return null;
    const app = scope.querySelector("#calculator-app");
    if (!app) return null;

    setAttrs(app, staticSurfaceAttrs());

    const regionMappings = [
      [".calculator-mode-switch", staticRegionRecords()[0]],
      [".calculator-basic-pane", staticRegionRecords()[1]],
      ["#calculator-graphing-panel", staticRegionRecords()[2]],
      ["#calculator-mathics-panel", staticRegionRecords()[3]],
      ["#calculator-chat-panel", staticRegionRecords()[4]]
    ];
    regionMappings.forEach(([selector, attrs]) => setAttrs(scope.querySelector(selector), attrs));

    const controlMappings = [
      ["#calculator-mode-basic", staticControlRecords()[0]],
      ["#calculator-mode-graphing", staticControlRecords()[1]],
      ["[data-calc-action=\"equals\"]", staticControlRecords()[2]],
      ["#calculator-ask-model", staticControlRecords()[3]],
      ["#calculator-qa-ask", staticControlRecords()[4]],
      ["#calculator-scientific-ask-model", staticControlRecords()[5]],
      ["#calculator-graph-draw", staticControlRecords()[6]],
      ["#calculator-graph-reset", staticControlRecords()[7]],
      ["#calculator-mathics-ask-model", staticControlRecords()[8]],
      ["#calculator-mathics-evaluate", staticControlRecords()[9]]
    ];
    controlMappings.forEach(([selector, attrs]) => setAttrs(scope.querySelector(selector), attrs));

    const carrier = ensureSemanticCarrier(app);
    staticNodeRecords().forEach((attrs, index) => writeCarrierRecord(carrier, attrs, index, "node"));
    staticEdgeRecords().forEach((attrs, index) => writeCarrierRecord(carrier, attrs, index, "edge"));

    [
      "#calculator-result",
      "#calculator-model-status",
      "#calculator-qa-status",
      "#calculator-qa-answer",
      "#calculator-scientific-model-status",
      "#calculator-graph-status",
      "#calculator-mathics-model-status",
      "#calculator-mathics-evaluation-status"
    ].forEach((selector) => applyFitPolicy(scope.querySelector(selector), "wrap", {
      readable: true,
      visualOwner: surfaceId,
      fitRole: "calculator-dynamic-text",
      required: true
    }));

    [
      "#calculator-mathics-output",
      "#calculator-chat-notebook"
    ].forEach((selector) => applyFitPolicy(scope.querySelector(selector), "scroll", {
      readable: true,
      visualOwner: surfaceId,
      fitRole: "calculator-dynamic-scroll-output",
      required: true
    }));

    applyFitPolicy(scope.querySelector("#calculator-graph-canvas"), "decorative", {
      visualOwner: surfaceId,
      fitRole: "calculator-derived-graph-output",
      required: false
    });

    [
      ".calculator-mode-switch",
      ".calculator-basic-pane",
      "#calculator-graphing-panel",
      "#calculator-mathics-panel",
      "#calculator-chat-panel"
    ].forEach((selector) => applyFitPolicy(scope.querySelector(selector), "scroll", {
      fitRole: "calculator-semantic-region",
      required: true
    }));

    return app;
  }

  function extractCurrentSurface(root) {
    if (!extractorsApi || typeof extractorsApi.extractSurfaceBundleFromHtml !== "function") {
      return Object.freeze({
        valid: false,
        diagnostics: Object.freeze([{code: "surface-extractors-missing", severity: "error", finding: "McelSurfaceExtractors is not loaded."}])
      });
    }
    const scope = root || (typeof document !== "undefined" ? document : null);
    const app = scope && typeof scope.querySelector === "function" ? scope.querySelector("#calculator-app") : null;
    const html = app && app.outerHTML ? app.outerHTML : "";
    if (!html) {
      return Object.freeze({
        valid: false,
        diagnostics: Object.freeze([{code: "calculator-surface-missing", severity: "error", finding: "Calculator surface is not mounted."}])
      });
    }
    return extractorsApi.extractSurfaceBundleFromHtml(html, {surfaceId});
  }

  return Object.freeze({
    contractVersion,
    surfaceId,
    surfaceContract,
    runtimeContractId,
    buildStaticSurfaceRidgeRecords,
    staticSurfaceAttrs,
    staticRegionRecords,
    staticNodeRecords,
    staticEdgeRecords,
    staticControlRecords,
    applyStaticSurfaceRidges,
    extractCurrentSurface
  });
})();

if (typeof window !== "undefined") {
  window.McelCalculatorSurface = McelCalculatorSurface;
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => McelCalculatorSurface.applyStaticSurfaceRidges(document), {once: true});
    } else {
      McelCalculatorSurface.applyStaticSurfaceRidges(document);
    }
  }
}
