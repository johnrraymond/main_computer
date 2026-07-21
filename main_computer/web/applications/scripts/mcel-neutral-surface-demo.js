var McelNeutralSurfaceDemo = (() => {
  "use strict";

  const contractVersion = "mcel.neutral-surface-demo.v1";
  const rendererInterfaceContractVersion = "mcel.surface-renderer-interface.v1";
  const roundTripContractVersion = "mcel.surface-roundtrip.v1";

  const irApi = (() => {
    if (typeof McelSemanticSurfaceIR !== "undefined") return McelSemanticSurfaceIR;
    if (typeof window !== "undefined" && window.McelSemanticSurfaceIR) return window.McelSemanticSurfaceIR;
    return null;
  })();

  const layoutApi = (() => {
    if (typeof McelSharedLayoutGrammar !== "undefined") return McelSharedLayoutGrammar;
    if (typeof window !== "undefined" && window.McelSharedLayoutGrammar) return window.McelSharedLayoutGrammar;
    return null;
  })();

  const rendererApi = (() => {
    if (typeof McelSurfaceRendererInterface !== "undefined") return McelSurfaceRendererInterface;
    if (typeof window !== "undefined" && window.McelSurfaceRendererInterface) return window.McelSurfaceRendererInterface;
    return null;
  })();

  const roundTripApi = (() => {
    if (typeof McelSurfaceRoundTrip !== "undefined") return McelSurfaceRoundTrip;
    if (typeof window !== "undefined" && window.McelSurfaceRoundTrip) return window.McelSurfaceRoundTrip;
    return null;
  })();

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value).trim();
  }

  function esc(value) {
    return safeString(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;"
    })[char]);
  }

  function attrs(values) {
    return Object.entries(values || {})
      .filter(([, value]) => value !== undefined && value !== null && safeString(value) !== "")
      .map(([key, value]) => `${key}="${esc(value)}"`)
      .join(" ");
  }

  function asArray(value) {
    return Object.freeze([...(value || [])]);
  }

  function byId(items) {
    return new Map(asArray(items).map((item) => [item.id, item]));
  }

  function dependencyDiagnostics() {
    const diagnostics = [];
    if (!irApi || typeof irApi.buildNeutralDemoSurfaceIR !== "function") {
      diagnostics.push({
        code: "neutral-demo-missing-semantic-surface-ir",
        severity: "error",
        finding: "Neutral surface demo requires McelSemanticSurfaceIR."
      });
    }
    if (!layoutApi || typeof layoutApi.buildNeutralDemoLayoutGrammar !== "function") {
      diagnostics.push({
        code: "neutral-demo-missing-shared-layout-grammar",
        severity: "error",
        finding: "Neutral surface demo requires McelSharedLayoutGrammar."
      });
    }
    if (!rendererApi || typeof rendererApi.renderWithRenderer !== "function") {
      diagnostics.push({
        code: "neutral-demo-missing-renderer-interface",
        severity: "error",
        finding: "Neutral surface demo requires McelSurfaceRendererInterface."
      });
    }
    if (!roundTripApi || typeof roundTripApi.verifyHtmlAndSvgAgree !== "function") {
      diagnostics.push({
        code: "neutral-demo-missing-roundtrip-api",
        severity: "error",
        finding: "Neutral surface demo requires McelSurfaceRoundTrip."
      });
    }
    return Object.freeze(diagnostics);
  }

  function createNeutralDemoSurfaceIR() {
    if (!irApi || typeof irApi.buildNeutralDemoSurfaceIR !== "function") {
      throw new Error("McelSemanticSurfaceIR is required before creating the neutral demo surface.");
    }
    return irApi.buildNeutralDemoSurfaceIR();
  }

  function createNeutralDemoLayoutGrammar() {
    if (!layoutApi || typeof layoutApi.buildNeutralDemoLayoutGrammar !== "function") {
      throw new Error("McelSharedLayoutGrammar is required before creating the neutral demo layout grammar.");
    }
    const result = layoutApi.buildNeutralDemoLayoutGrammar();
    return result && result.grammar ? result.grammar : result;
  }

  function unwrapLayoutGrammar(value) {
    const source = value || createNeutralDemoLayoutGrammar();
    return source && source.grammar ? source.grammar : source;
  }

  function htmlProfile() {
    return Object.freeze({
      id: "mcel.neutral-demo.html-renderer.v1",
      label: "MCEL neutral demo HTML renderer",
      version: "1",
      surfaceKinds: ["html"],
      defaultSurfaceKind: "html",
      capabilities: ["neutral-demo", "semantic-ridges", "layout-ridges", "round-trip-fixture"]
    });
  }

  function svgProfile() {
    return Object.freeze({
      id: "mcel.neutral-demo.svg-renderer.v1",
      label: "MCEL neutral demo SVG renderer",
      version: "1",
      surfaceKinds: ["svg"],
      defaultSurfaceKind: "svg",
      capabilities: ["neutral-demo", "semantic-ridges", "layout-ridges", "round-trip-fixture"]
    });
  }

  function surfaceAttrs(surfaceIR, layoutGrammar, profile, projection) {
    const surface = surfaceIR.surface || {};
    const viewport = layoutGrammar.viewport || {};
    return attrs({
      "data-mcel-surface-id": surface.id,
      "data-mcel-surface-kind": surface.kind,
      "data-mcel-surface-role": surface.role,
      "data-mcel-surface-contract": surface.contract,
      "data-mcel-authoritative": "true",
      "data-mcel-renderer": profile.id,
      "data-mcel-projection": projection,
      "data-layout-viewport-width": viewport.width,
      "data-layout-viewport-height": viewport.height,
      "data-layout-safe-margin": viewport.safeMargin
    });
  }

  function regionAttrs(region) {
    return attrs({
      "data-mcel-region": region.id,
      "data-mcel-region-role": region.role,
      "data-layout-x": region.x,
      "data-layout-y": region.y,
      "data-layout-region-width": region.width,
      "data-layout-region-height": region.height
    });
  }

  function nodeAttrs(node, layout, ports) {
    return attrs({
      "data-mcel-node-id": node.id,
      "data-mcel-node-type": node.type,
      "data-mcel-node-label": node.label,
      "data-mcel-source": node.source,
      "data-mcel-provenance": node.provenance,
      "data-mcel-symbol": node.symbol,
      "data-mcel-home-region": node.homeRegion,
      "data-mcel-actual-region": node.actualRegion,
      "data-mcel-teleported": node.teleported ? "true" : "false",
      "data-layout-anchor-x": layout.anchorX,
      "data-layout-anchor-y": layout.anchorY,
      "data-layout-width": layout.width,
      "data-layout-height": layout.height,
      "data-layout-z": layout.z,
      "data-layout-region": layout.region,
      "data-layout-ports": asArray(ports).join(",")
    });
  }

  function edgeAttrs(edge, layout) {
    return attrs({
      "data-mcel-edge-id": edge.id,
      "data-mcel-edge-kind": edge.kind,
      "data-mcel-from": edge.from,
      "data-mcel-to": edge.to,
      "data-mcel-relation": edge.relation,
      "data-mcel-causal-link": edge.causalLink ? "true" : "false",
      "data-mcel-allowed-inferences": asArray(edge.allowedInferences).join(","),
      "data-mcel-forbidden-inferences": asArray(edge.forbiddenInferences).join(","),
      "data-layout-route-kind": layout.routeKind,
      "data-layout-from-port": layout.fromPort,
      "data-layout-to-port": layout.toPort,
      "data-layout-z": layout.z
    });
  }

  function controlAttrs(control, layout) {
    return attrs({
      "data-mcel-control": control.id,
      "data-mcel-control-action": control.action,
      "data-mcel-reveals": control.reveals,
      "data-layout-anchor-x": layout.anchorX,
      "data-layout-anchor-y": layout.anchorY,
      "data-layout-width": layout.width,
      "data-layout-height": layout.height,
      "data-layout-z": layout.z
    });
  }

  function normalizeInput(request) {
    const settings = request || {};
    return Object.freeze({
      surfaceIR: settings.surfaceIR || settings.expectedSurfaceIr || settings.expectedSurfaceIR || createNeutralDemoSurfaceIR(),
      layoutGrammar: unwrapLayoutGrammar(settings.layoutGrammar || settings.expectedLayoutGrammar || createNeutralDemoLayoutGrammar()),
      profile: settings.profile || htmlProfile(),
      projection: safeString(settings.surfaceKind || settings.projection || "html") || "html"
    });
  }

  function renderNeutralDemoHtml(request) {
    const input = normalizeInput(Object.assign({surfaceKind: "html", profile: htmlProfile()}, request || {}));
    const surfaceIR = input.surfaceIR;
    const layoutGrammar = input.layoutGrammar;
    const nodeLayout = byId(layoutGrammar.nodes);
    const edgeLayout = byId(layoutGrammar.edges);
    const controlLayout = byId(layoutGrammar.controls);

    const regions = asArray(layoutGrammar.regions).map((region) => (
      `<section class="mcel-neutral-region" style="left:${region.x}px;top:${region.y}px;width:${region.width}px;height:${region.height}px" ${regionAttrs(region)}>` +
      `<strong>${esc(region.role)}</strong></section>`
    )).join("");

    const nodes = asArray(surfaceIR.graph.nodes).map((node) => {
      const layout = nodeLayout.get(node.id) || {};
      const ports = layoutGrammar.nodePorts && layoutGrammar.nodePorts[node.id] || [];
      return `<article class="mcel-neutral-node type-${esc(node.type)}" style="left:${layout.anchorX}px;top:${layout.anchorY}px;width:${layout.width}px;height:${layout.height}px" ${nodeAttrs(node, layout, ports)}><span class="symbol">${esc(node.symbol)}</span><strong>${esc(node.label)}</strong><small>${esc(node.type)}</small></article>`;
    }).join("");

    const edges = asArray(surfaceIR.graph.edges).map((edge) => {
      const layout = edgeLayout.get(edge.id) || {};
      return `<i class="mcel-neutral-edge" data-mcel-flow-id="FLOW.${esc(edge.id)}" data-mcel-flow-kind="semantic_relation" ${edgeAttrs(edge, layout)}></i>`;
    }).join("");

    const controls = asArray(surfaceIR.graph.controls).map((control) => {
      const layout = controlLayout.get(control.id) || {};
      return `<button class="mcel-neutral-control" style="left:${layout.anchorX}px;top:${layout.anchorY}px;width:${layout.width}px;height:${layout.height}px" ${controlAttrs(control, layout)}>${esc(control.action)}</button>`;
    }).join("");

    return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>MCEL Neutral Surface Demo</title><style>:root{color-scheme:dark}body{margin:0;background:#05070d;color:#eef3ff;font-family:system-ui,sans-serif}.mcel-neutral-surface{position:relative;overflow:hidden}.mcel-neutral-region{position:absolute;border:1px solid rgba(170,190,255,.35);border-radius:18px;padding:12px;color:#9aa7c7}.mcel-neutral-node,.mcel-neutral-control{position:absolute;transform:translate(-50%,-50%);box-sizing:border-box}.mcel-neutral-node{display:grid;place-items:center;border:1px solid #8fc7ff;border-radius:16px;background:#101828}.mcel-neutral-node .symbol{font-size:24px}.mcel-neutral-edge{position:absolute;width:1px;height:1px;overflow:hidden}.mcel-neutral-control{border:1px solid #f0c66b;border-radius:999px;background:#20170b;color:#fff6d6}</style></head><body><main class="mcel-neutral-surface" style="width:${layoutGrammar.viewport.width}px;height:${layoutGrammar.viewport.height}px" ${surfaceAttrs(surfaceIR, layoutGrammar, input.profile, "html")}><h1>MCEL Neutral Surface Demo</h1>${regions}<section data-mcel-layer="edge-layer">${edges}</section><section data-mcel-layer="node-layer">${nodes}</section><nav data-mcel-layer="controls">${controls}</nav></main></body></html>`;
  }

  function portPoint(nodeLayout, port) {
    const x = Number(nodeLayout.anchorX || 0);
    const y = Number(nodeLayout.anchorY || 0);
    const w = Number(nodeLayout.width || 0);
    const h = Number(nodeLayout.height || 0);
    if (port === "east") return [x + w / 2, y];
    if (port === "west") return [x - w / 2, y];
    if (port === "north") return [x, y - h / 2];
    if (port === "south") return [x, y + h / 2];
    return [x, y];
  }

  function edgePath(edgeLayout, nodeLayout) {
    const from = nodeLayout.get(edgeLayout.from) || {};
    const to = nodeLayout.get(edgeLayout.to) || {};
    const [x1, y1] = portPoint(from, edgeLayout.fromPort);
    const [x2, y2] = portPoint(to, edgeLayout.toPort);
    const dx = Math.max(48, Math.abs(x2 - x1) * 0.35);
    return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
  }

  function renderNeutralDemoSvg(request) {
    const input = normalizeInput(Object.assign({surfaceKind: "svg", profile: svgProfile()}, request || {}));
    const surfaceIR = input.surfaceIR;
    const layoutGrammar = input.layoutGrammar;
    const nodeLayout = byId(layoutGrammar.nodes);
    const edgeLayout = byId(layoutGrammar.edges);
    const controlLayout = byId(layoutGrammar.controls);

    const regions = asArray(layoutGrammar.regions).map((region) => (
      `<rect x="${region.x}" y="${region.y}" width="${region.width}" height="${region.height}" rx="18" fill="rgba(255,255,255,.025)" stroke="rgba(170,190,255,.35)" ${regionAttrs(region)}/>`
    )).join("");

    const edges = asArray(surfaceIR.graph.edges).map((edge) => {
      const layout = edgeLayout.get(edge.id) || {};
      return `<path d="${edgePath(layout, nodeLayout)}" fill="none" stroke="#f0c66b" stroke-width="3" data-mcel-flow-id="FLOW.${esc(edge.id)}" data-mcel-flow-kind="semantic_relation" ${edgeAttrs(edge, layout)}/>`;
    }).join("");

    const nodes = asArray(surfaceIR.graph.nodes).map((node) => {
      const layout = nodeLayout.get(node.id) || {};
      const ports = layoutGrammar.nodePorts && layoutGrammar.nodePorts[node.id] || [];
      const x = Number(layout.anchorX || 0);
      const y = Number(layout.anchorY || 0);
      const w = Number(layout.width || 0);
      const h = Number(layout.height || 0);
      return `<g transform="translate(${x} ${y})" ${nodeAttrs(node, layout, ports)}><rect x="${-w / 2}" y="${-h / 2}" width="${w}" height="${h}" rx="16" fill="#101828" stroke="#8fc7ff"/><text x="0" y="-6" text-anchor="middle" fill="#eef3ff" font-size="24">${esc(node.symbol)}</text><text x="0" y="20" text-anchor="middle" fill="#eef3ff" font-size="14">${esc(node.label)}</text></g>`;
    }).join("");

    const controls = asArray(surfaceIR.graph.controls).map((control) => {
      const layout = controlLayout.get(control.id) || {};
      const x = Number(layout.anchorX || 0);
      const y = Number(layout.anchorY || 0);
      const w = Number(layout.width || 0);
      const h = Number(layout.height || 0);
      return `<g transform="translate(${x} ${y})" ${controlAttrs(control, layout)}><rect x="${-w / 2}" y="${-h / 2}" width="${w}" height="${h}" rx="19" fill="#20170b" stroke="#f0c66b"/><text x="0" y="4" text-anchor="middle" fill="#fff6d6" font-size="12">${esc(control.action)}</text></g>`;
    }).join("");

    return `<svg xmlns="http://www.w3.org/2000/svg" width="${layoutGrammar.viewport.width}" height="${layoutGrammar.viewport.height}" viewBox="0 0 ${layoutGrammar.viewport.width} ${layoutGrammar.viewport.height}" ${surfaceAttrs(surfaceIR, layoutGrammar, input.profile, "svg")}><title>MCEL Neutral Surface Demo</title><rect width="100%" height="100%" fill="#05070d"/><text x="32" y="48" fill="#eef3ff" font-size="24">MCEL Neutral Surface Demo</text>${regions}<g data-mcel-layer="edge-layer">${edges}</g><g data-mcel-layer="node-layer">${nodes}</g><g data-mcel-layer="controls">${controls}</g></svg>`;
  }

  function htmlRenderer() {
    return Object.freeze({
      profile: htmlProfile(),
      render(request) {
        return renderNeutralDemoHtml(Object.assign({}, request || {}, {profile: htmlProfile(), surfaceKind: "html"}));
      }
    });
  }

  function svgRenderer() {
    return Object.freeze({
      profile: svgProfile(),
      render(request) {
        return renderNeutralDemoSvg(Object.assign({}, request || {}, {profile: svgProfile(), surfaceKind: "svg"}));
      }
    });
  }

  function renderNeutralDemoPair(options) {
    const settings = options || {};
    if (!rendererApi || typeof rendererApi.renderWithRenderer !== "function") {
      throw new Error("McelSurfaceRendererInterface is required before rendering the neutral demo pair.");
    }
    const surfaceIR = settings.surfaceIR || createNeutralDemoSurfaceIR();
    const layoutGrammar = settings.layoutGrammar || createNeutralDemoLayoutGrammar();
    const request = Object.freeze({
      surfaceIR,
      layoutGrammar,
      verifyOutput: settings.verifyOutput !== false
    });
    const htmlResult = rendererApi.renderWithRenderer(htmlRenderer(), Object.assign({}, request, {surfaceKind: "html"}));
    const svgResult = rendererApi.renderWithRenderer(svgRenderer(), Object.assign({}, request, {surfaceKind: "svg"}));
    const agreement = rendererApi.verifyRendererPairAgreement(htmlResult, svgResult);
    return Object.freeze({
      contractVersion,
      surfaceIR,
      layoutGrammar,
      htmlResult,
      svgResult,
      agreement,
      valid: htmlResult.valid && svgResult.valid && agreement.valid,
      status: htmlResult.valid && svgResult.valid && agreement.valid ? "pass" : "fail"
    });
  }

  function verifyNeutralDemoRoundTrip(options) {
    return renderNeutralDemoPair(options || {});
  }

  return Object.freeze({
    contractVersion,
    rendererInterfaceContractVersion,
    roundTripContractVersion,
    createNeutralDemoSurfaceIR,
    createNeutralDemoLayoutGrammar,
    htmlProfile,
    svgProfile,
    renderNeutralDemoHtml,
    renderNeutralDemoSvg,
    htmlRenderer,
    svgRenderer,
    renderNeutralDemoPair,
    verifyNeutralDemoRoundTrip,
    dependencyDiagnostics
  });
})();

if (typeof window !== "undefined") {
  window.McelNeutralSurfaceDemo = McelNeutralSurfaceDemo;
}
