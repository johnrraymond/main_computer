var McelSharedLayoutGrammar = (() => {
  "use strict";

  const contractVersion = "mcel.shared-layout-grammar.v1";
  const surfaceIrContractVersion = "mcel.semantic-surface-ir.v1";

  const irApi = (() => {
    if (typeof McelSemanticSurfaceIR !== "undefined") return McelSemanticSurfaceIR;
    if (typeof window !== "undefined" && window.McelSemanticSurfaceIR) return window.McelSemanticSurfaceIR;
    return null;
  })();

  const allowedRouteKinds = Object.freeze(["cubic", "orthogonal", "polyline", "straight"]);
  const defaultPorts = Object.freeze(["north", "south", "east", "west"]);

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value).trim();
  }

  function finiteNumberOrNull(value) {
    if (value === undefined || value === null || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function clone(value) {
    return Object.assign({}, value || {});
  }

  function freezeArray(items) {
    return Object.freeze([...(items || [])]);
  }

  function sortById(items) {
    return Object.freeze([...(items || [])].sort((a, b) => safeString(a.id).localeCompare(safeString(b.id))));
  }

  function diagnostic(code, severity, finding, detail) {
    return Object.freeze({
      code,
      severity,
      finding,
      detail: Object.freeze(detail || {})
    });
  }

  function canonicalSurfaceIR(surfaceIR) {
    const source = surfaceIR && surfaceIR.ir ? surfaceIR.ir : surfaceIR;
    if (irApi && typeof irApi.canonicalizeSurfaceIR === "function") return irApi.canonicalizeSurfaceIR(source);
    return source || {
      surface: {id: ""},
      graph: {nodes: [], edges: [], regions: [], controls: []},
      layout: {nodes: [], edges: [], controls: []}
    };
  }

  function normalizeViewport(viewport) {
    const source = viewport || {};
    return Object.freeze({
      width: finiteNumberOrNull(source.width),
      height: finiteNumberOrNull(source.height),
      safeMargin: finiteNumberOrNull(source.safeMargin) || 0
    });
  }

  function normalizeRegionBoundsRecord(record) {
    const source = record || {};
    const id = safeString(source.id);
    const x = finiteNumberOrNull(source.x);
    const y = finiteNumberOrNull(source.y);
    const width = finiteNumberOrNull(source.width);
    const height = finiteNumberOrNull(source.height);
    return Object.freeze({
      id,
      role: safeString(source.role),
      x,
      y,
      width,
      height,
      hasBounds: x !== null || y !== null || width !== null || height !== null
    });
  }

  function normalizeRegions(surface, options) {
    const regionRoleById = new Map((surface.graph && surface.graph.regions || []).map((region) => [region.id, region.role || ""]));
    const regionInputs = [];
    (surface.graph && surface.graph.regions || []).forEach((region) => regionInputs.push({id: region.id, role: region.role || ""}));
    (options.regions || []).forEach((region) => regionInputs.push(region));
    Object.entries(options.regionBounds || {}).forEach(([id, bounds]) => {
      regionInputs.push(Object.assign({id, role: regionRoleById.get(id) || ""}, bounds || {}));
    });

    const byId = new Map();
    regionInputs.forEach((record) => {
      const normalized = normalizeRegionBoundsRecord(Object.assign({role: regionRoleById.get(record.id) || record.role || ""}, record));
      if (!normalized.id) return;
      byId.set(normalized.id, normalized);
    });
    return sortById(Array.from(byId.values()));
  }

  function normalizeNodeLayout(record, nodeById) {
    const source = clone(record);
    const node = nodeById.get(safeString(source.id)) || {};
    return Object.freeze({
      id: safeString(source.id),
      anchorX: finiteNumberOrNull(source.anchorX),
      anchorY: finiteNumberOrNull(source.anchorY),
      width: finiteNumberOrNull(source.width),
      height: finiteNumberOrNull(source.height),
      z: finiteNumberOrNull(source.z),
      region: safeString(source.region) || safeString(node.homeRegion),
      homeRegion: safeString(node.homeRegion),
      actualRegion: safeString(node.actualRegion) || safeString(node.homeRegion),
      teleported: !!node.teleported
    });
  }

  function normalizeControlLayout(record) {
    const source = clone(record);
    return Object.freeze({
      id: safeString(source.id),
      anchorX: finiteNumberOrNull(source.anchorX),
      anchorY: finiteNumberOrNull(source.anchorY),
      width: finiteNumberOrNull(source.width),
      height: finiteNumberOrNull(source.height),
      z: finiteNumberOrNull(source.z)
    });
  }

  function normalizeEdgeLayout(record, edgeById) {
    const source = clone(record);
    const edge = edgeById.get(safeString(source.id)) || {};
    return Object.freeze({
      id: safeString(source.id),
      from: safeString(edge.from),
      to: safeString(edge.to),
      routeKind: safeString(source.routeKind),
      fromPort: safeString(source.fromPort),
      toPort: safeString(source.toPort),
      z: finiteNumberOrNull(source.z)
    });
  }

  function buildSharedLayoutGrammar(surfaceIR, options) {
    const opts = Object.assign({
      viewport: null,
      regions: [],
      regionBounds: {},
      nodePorts: {},
      allowedRouteKinds,
      allowCenterPorts: false,
      requireAllNodes: true,
      requireAllControls: true,
      requireAllEdges: true
    }, options || {});

    const surface = canonicalSurfaceIR(surfaceIR);
    const nodeById = new Map((surface.graph && surface.graph.nodes || []).map((node) => [node.id, node]));
    const edgeById = new Map((surface.graph && surface.graph.edges || []).map((edge) => [edge.id, edge]));

    const grammar = Object.freeze({
      contractVersion,
      surfaceIrContractVersion,
      surfaceId: safeString(surface.surface && surface.surface.id),
      viewport: normalizeViewport(opts.viewport),
      regions: normalizeRegions(surface, opts),
      nodes: sortById((surface.layout && surface.layout.nodes || []).map((layout) => normalizeNodeLayout(layout, nodeById))),
      edges: sortById((surface.layout && surface.layout.edges || []).map((layout) => normalizeEdgeLayout(layout, edgeById))),
      controls: sortById((surface.layout && surface.layout.controls || []).map((layout) => normalizeControlLayout(layout))),
      nodePorts: Object.freeze(Object.fromEntries(Object.entries(opts.nodePorts || {}).map(([id, ports]) => [
        id,
        Object.freeze([...(ports || defaultPorts)].map(safeString).filter(Boolean))
      ]))),
      policy: Object.freeze({
        allowedRouteKinds: Object.freeze([...(opts.allowedRouteKinds || allowedRouteKinds)]),
        allowCenterPorts: !!opts.allowCenterPorts,
        requireAllNodes: !!opts.requireAllNodes,
        requireAllControls: !!opts.requireAllControls,
        requireAllEdges: !!opts.requireAllEdges
      })
    });

    const validation = validateSharedLayoutGrammar(surface, grammar);
    return Object.freeze({
      grammar,
      valid: validation.valid,
      diagnostics: validation.diagnostics
    });
  }

  function idSet(items) {
    return new Set((items || []).map((item) => item.id).filter(Boolean));
  }

  function byId(items) {
    return new Map((items || []).map((item) => [item.id, item]));
  }

  function isCompleteBox(layout) {
    return layout && layout.anchorX !== null && layout.anchorY !== null && layout.width !== null && layout.height !== null;
  }

  function isPositiveBox(layout) {
    return isCompleteBox(layout) && layout.width > 0 && layout.height > 0;
  }

  function boxForLayout(layout) {
    if (!isCompleteBox(layout)) return null;
    return Object.freeze({
      id: layout.id,
      left: layout.anchorX - layout.width / 2,
      top: layout.anchorY - layout.height / 2,
      right: layout.anchorX + layout.width / 2,
      bottom: layout.anchorY + layout.height / 2,
      width: layout.width,
      height: layout.height
    });
  }

  function regionBox(region) {
    if (!region || region.x === null || region.y === null || region.width === null || region.height === null) return null;
    return Object.freeze({
      id: region.id,
      left: region.x,
      top: region.y,
      right: region.x + region.width,
      bottom: region.y + region.height,
      width: region.width,
      height: region.height
    });
  }

  function boxInside(outer, inner, margin) {
    if (!outer || !inner) return true;
    const m = margin || 0;
    return inner.left >= outer.left + m && inner.top >= outer.top + m && inner.right <= outer.right - m && inner.bottom <= outer.bottom - m;
  }

  function boxesOverlap(a, b, padding) {
    const pad = padding || 0;
    return a.left - pad < b.right && a.right + pad > b.left && a.top - pad < b.bottom && a.bottom + pad > b.top;
  }

  function validateViewport(grammar, diagnostics) {
    const viewport = grammar.viewport || {};
    if (viewport.width === null || viewport.width <= 0) {
      diagnostics.push(diagnostic("layout-viewport-width-invalid", "error", "Layout grammar viewport width must be positive.", {width: viewport.width}));
    }
    if (viewport.height === null || viewport.height <= 0) {
      diagnostics.push(diagnostic("layout-viewport-height-invalid", "error", "Layout grammar viewport height must be positive.", {height: viewport.height}));
    }
  }

  function validateRegions(grammar, diagnostics) {
    const viewport = grammar.viewport || {};
    const viewportBox = viewport.width && viewport.height ? {left: 0, top: 0, right: viewport.width, bottom: viewport.height} : null;
    (grammar.regions || []).forEach((region) => {
      if (region.hasBounds) {
        if (region.x === null || region.y === null || region.width === null || region.height === null) {
          diagnostics.push(diagnostic("layout-region-bounds-incomplete", "error", "Layout region bounds must declare x, y, width, and height together.", {regionId: region.id}));
          return;
        }
        if (region.width <= 0 || region.height <= 0) {
          diagnostics.push(diagnostic("layout-region-bounds-invalid", "error", "Layout region bounds must be positive.", {regionId: region.id, width: region.width, height: region.height}));
          return;
        }
        if (viewportBox && !boxInside(viewportBox, regionBox(region), 0)) {
          diagnostics.push(diagnostic("layout-region-outside-viewport", "error", "Layout region must stay inside the viewport.", {regionId: region.id}));
        }
      }
    });
  }

  function validateNodeLayouts(surface, grammar, diagnostics) {
    const graphNodeIds = idSet(surface.graph && surface.graph.nodes);
    const layoutNodeIds = idSet(grammar.nodes);
    const regionIds = idSet(surface.graph && surface.graph.regions);
    const regionById = byId(grammar.regions);
    const viewport = grammar.viewport || {};
    const viewportBox = viewport.width && viewport.height ? {left: 0, top: 0, right: viewport.width, bottom: viewport.height} : null;

    if (grammar.policy.requireAllNodes) {
      (surface.graph && surface.graph.nodes || []).forEach((node) => {
        if (!layoutNodeIds.has(node.id)) {
          diagnostics.push(diagnostic("layout-node-missing", "error", "Every semantic surface node must have a layout node record.", {nodeId: node.id}));
        }
      });
    }

    (grammar.nodes || []).forEach((layout) => {
      if (!layout.id) {
        diagnostics.push(diagnostic("layout-node-id-missing", "error", "Layout node is missing an id.", {}));
        return;
      }
      if (!graphNodeIds.has(layout.id)) {
        diagnostics.push(diagnostic("layout-node-orphan", "error", "Layout node references no semantic surface node.", {nodeId: layout.id}));
      }
      if (!isCompleteBox(layout)) {
        diagnostics.push(diagnostic("layout-node-box-incomplete", "error", "Layout node must declare anchorX, anchorY, width, and height.", {nodeId: layout.id}));
      } else if (!isPositiveBox(layout)) {
        diagnostics.push(diagnostic("layout-node-box-invalid", "error", "Layout node width and height must be positive.", {nodeId: layout.id, width: layout.width, height: layout.height}));
      }

      if (layout.region) {
        if (regionIds.size > 0 && !regionIds.has(layout.region)) {
          diagnostics.push(diagnostic("layout-node-region-missing", "error", "Layout node references a missing region.", {nodeId: layout.id, region: layout.region}));
        }
      } else {
        diagnostics.push(diagnostic("layout-node-region-missing", "error", "Layout node must declare a region.", {nodeId: layout.id}));
      }

      if (layout.actualRegion && layout.homeRegion && layout.actualRegion !== layout.homeRegion && !layout.teleported) {
        diagnostics.push(diagnostic("layout-node-actual-region-without-teleport", "error", "Node actual region may differ from home region only when teleported/stressed.", {nodeId: layout.id, homeRegion: layout.homeRegion, actualRegion: layout.actualRegion}));
      }

      const box = boxForLayout(layout);
      if (box && viewportBox && !boxInside(viewportBox, box, viewport.safeMargin || 0)) {
        diagnostics.push(diagnostic("layout-node-outside-viewport", "error", "Layout node must stay inside the viewport safe area.", {nodeId: layout.id}));
      }
      const containingRegion = regionById.get(layout.region);
      const containerBox = regionBox(containingRegion);
      if (box && containerBox && !boxInside(containerBox, box, 0)) {
        diagnostics.push(diagnostic("layout-node-outside-region", "error", "Layout node must stay inside its declared region bounds.", {nodeId: layout.id, region: layout.region}));
      }
    });

    const boxes = (grammar.nodes || []).map((layout) => ({layout, box: boxForLayout(layout)})).filter((item) => item.box);
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        if (boxesOverlap(boxes[i].box, boxes[j].box, 4)) {
          diagnostics.push(diagnostic("layout-node-collision", "error", "Visible layout node boxes must not collide.", {a: boxes[i].layout.id, b: boxes[j].layout.id}));
        }
      }
    }
  }

  function portsForNode(grammar, nodeId) {
    return freezeArray(grammar.nodePorts && grammar.nodePorts[nodeId] || defaultPorts);
  }

  function validateEdgeLayouts(surface, grammar, diagnostics) {
    const graphEdgeIds = idSet(surface.graph && surface.graph.edges);
    const layoutEdgeIds = idSet(grammar.edges);
    const nodeIds = idSet(surface.graph && surface.graph.nodes);
    const edgeById = byId(surface.graph && surface.graph.edges);

    if (grammar.policy.requireAllEdges) {
      (surface.graph && surface.graph.edges || []).forEach((edge) => {
        if (!layoutEdgeIds.has(edge.id)) {
          diagnostics.push(diagnostic("layout-edge-missing", "error", "Every semantic surface edge must have a layout edge record.", {edgeId: edge.id}));
        }
      });
    }

    (grammar.edges || []).forEach((layout) => {
      if (!layout.id) {
        diagnostics.push(diagnostic("layout-edge-id-missing", "error", "Layout edge is missing an id.", {}));
        return;
      }
      if (!graphEdgeIds.has(layout.id)) {
        diagnostics.push(diagnostic("layout-edge-orphan", "error", "Layout edge references no semantic surface edge.", {edgeId: layout.id}));
        return;
      }
      if (!layout.routeKind) {
        diagnostics.push(diagnostic("layout-edge-route-kind-missing", "error", "Layout edge must declare a route kind.", {edgeId: layout.id}));
      } else if (!grammar.policy.allowedRouteKinds.includes(layout.routeKind)) {
        diagnostics.push(diagnostic("layout-edge-route-kind-invalid", "error", "Layout edge route kind is not supported.", {edgeId: layout.id, routeKind: layout.routeKind}));
      }
      if (!layout.fromPort) {
        diagnostics.push(diagnostic("layout-edge-from-port-missing", "error", "Layout edge must declare a from-port.", {edgeId: layout.id}));
      }
      if (!layout.toPort) {
        diagnostics.push(diagnostic("layout-edge-to-port-missing", "error", "Layout edge must declare a to-port.", {edgeId: layout.id}));
      }
      if (!grammar.policy.allowCenterPorts) {
        if (layout.fromPort === "center") diagnostics.push(diagnostic("layout-edge-center-from-port-forbidden", "error", "Layout edge must not route from node center by default.", {edgeId: layout.id}));
        if (layout.toPort === "center") diagnostics.push(diagnostic("layout-edge-center-to-port-forbidden", "error", "Layout edge must not route to node center by default.", {edgeId: layout.id}));
      }
      const edge = edgeById.get(layout.id);
      if (edge) {
        if (!nodeIds.has(edge.from)) diagnostics.push(diagnostic("layout-edge-from-node-missing", "error", "Layout edge references a missing source node.", {edgeId: layout.id, from: edge.from}));
        if (!nodeIds.has(edge.to)) diagnostics.push(diagnostic("layout-edge-to-node-missing", "error", "Layout edge references a missing target node.", {edgeId: layout.id, to: edge.to}));
        if (layout.fromPort && !portsForNode(grammar, edge.from).includes(layout.fromPort)) {
          diagnostics.push(diagnostic("layout-edge-from-port-invalid", "error", "Layout edge from-port is not declared on the source node.", {edgeId: layout.id, from: edge.from, port: layout.fromPort}));
        }
        if (layout.toPort && !portsForNode(grammar, edge.to).includes(layout.toPort)) {
          diagnostics.push(diagnostic("layout-edge-to-port-invalid", "error", "Layout edge to-port is not declared on the target node.", {edgeId: layout.id, to: edge.to, port: layout.toPort}));
        }
      }
    });
  }

  function validateControlLayouts(surface, grammar, diagnostics) {
    const graphControlIds = idSet(surface.graph && surface.graph.controls);
    const layoutControlIds = idSet(grammar.controls);
    const viewport = grammar.viewport || {};
    const viewportBox = viewport.width && viewport.height ? {left: 0, top: 0, right: viewport.width, bottom: viewport.height} : null;

    if (grammar.policy.requireAllControls) {
      (surface.graph && surface.graph.controls || []).forEach((control) => {
        if (!layoutControlIds.has(control.id)) {
          diagnostics.push(diagnostic("layout-control-missing", "error", "Every semantic surface control must have a layout control record.", {controlId: control.id}));
        }
      });
    }

    (grammar.controls || []).forEach((layout) => {
      if (!layout.id) {
        diagnostics.push(diagnostic("layout-control-id-missing", "error", "Layout control is missing an id.", {}));
        return;
      }
      if (!graphControlIds.has(layout.id)) {
        diagnostics.push(diagnostic("layout-control-orphan", "error", "Layout control references no semantic surface control.", {controlId: layout.id}));
      }
      if (!isCompleteBox(layout)) {
        diagnostics.push(diagnostic("layout-control-box-incomplete", "error", "Layout control must declare anchorX, anchorY, width, and height.", {controlId: layout.id}));
      } else if (!isPositiveBox(layout)) {
        diagnostics.push(diagnostic("layout-control-box-invalid", "error", "Layout control width and height must be positive.", {controlId: layout.id, width: layout.width, height: layout.height}));
      }
      const box = boxForLayout(layout);
      if (box && viewportBox && !boxInside(viewportBox, box, viewport.safeMargin || 0)) {
        diagnostics.push(diagnostic("layout-control-outside-viewport", "error", "Layout control must stay inside the viewport safe area.", {controlId: layout.id}));
      }
    });

    const boxes = (grammar.controls || []).map((layout) => ({layout, box: boxForLayout(layout)})).filter((item) => item.box);
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        if (boxesOverlap(boxes[i].box, boxes[j].box, 2)) {
          diagnostics.push(diagnostic("layout-control-collision", "error", "Visible control boxes must not collide.", {a: boxes[i].layout.id, b: boxes[j].layout.id}));
        }
      }
    }
  }

  function validateSharedLayoutGrammar(surfaceIR, grammar) {
    const surface = canonicalSurfaceIR(surfaceIR);
    const subject = grammar || buildSharedLayoutGrammar(surface).grammar;
    const diagnostics = [];

    if (!subject.surfaceId) {
      diagnostics.push(diagnostic("layout-surface-id-missing", "error", "Shared layout grammar must identify a surface.", {}));
    }
    validateViewport(subject, diagnostics);
    validateRegions(subject, diagnostics);
    validateNodeLayouts(surface, subject, diagnostics);
    validateEdgeLayouts(surface, subject, diagnostics);
    validateControlLayouts(surface, subject, diagnostics);

    return Object.freeze({
      contractVersion,
      valid: diagnostics.filter((item) => item.severity === "error").length === 0,
      errorCount: diagnostics.filter((item) => item.severity === "error").length,
      warningCount: diagnostics.filter((item) => item.severity === "warning").length,
      diagnostics: Object.freeze(diagnostics)
    });
  }

  function canonicalizeLayoutGrammar(grammar) {
    const source = grammar && grammar.grammar ? grammar.grammar : grammar;
    const subject = source || {};
    return Object.freeze({
      contractVersion,
      surfaceIrContractVersion,
      surfaceId: safeString(subject.surfaceId),
      viewport: normalizeViewport(subject.viewport),
      regions: sortById((subject.regions || []).map((region) => normalizeRegionBoundsRecord(region))),
      nodes: sortById((subject.nodes || []).map((layout) => Object.freeze({
        id: safeString(layout.id),
        anchorX: finiteNumberOrNull(layout.anchorX),
        anchorY: finiteNumberOrNull(layout.anchorY),
        width: finiteNumberOrNull(layout.width),
        height: finiteNumberOrNull(layout.height),
        z: finiteNumberOrNull(layout.z),
        region: safeString(layout.region),
        homeRegion: safeString(layout.homeRegion),
        actualRegion: safeString(layout.actualRegion),
        teleported: !!layout.teleported
      }))),
      edges: sortById((subject.edges || []).map((layout) => Object.freeze({
        id: safeString(layout.id),
        from: safeString(layout.from),
        to: safeString(layout.to),
        routeKind: safeString(layout.routeKind),
        fromPort: safeString(layout.fromPort),
        toPort: safeString(layout.toPort),
        z: finiteNumberOrNull(layout.z)
      }))),
      controls: sortById((subject.controls || []).map((layout) => Object.freeze({
        id: safeString(layout.id),
        anchorX: finiteNumberOrNull(layout.anchorX),
        anchorY: finiteNumberOrNull(layout.anchorY),
        width: finiteNumberOrNull(layout.width),
        height: finiteNumberOrNull(layout.height),
        z: finiteNumberOrNull(layout.z)
      }))),
      nodePorts: Object.freeze(Object.fromEntries(Object.entries(subject.nodePorts || {}).sort().map(([id, ports]) => [
        id,
        Object.freeze([...(ports || [])].map(safeString).filter(Boolean).sort())
      ]))),
      policy: Object.freeze({
        allowedRouteKinds: Object.freeze([...(subject.policy && subject.policy.allowedRouteKinds || allowedRouteKinds)].map(safeString).filter(Boolean).sort()),
        allowCenterPorts: !!(subject.policy && subject.policy.allowCenterPorts),
        requireAllNodes: !!(subject.policy && subject.policy.requireAllNodes),
        requireAllControls: !!(subject.policy && subject.policy.requireAllControls),
        requireAllEdges: !!(subject.policy && subject.policy.requireAllEdges)
      })
    });
  }

  function layoutFingerprint(grammar) {
    return JSON.stringify(canonicalizeLayoutGrammar(grammar));
  }

  function buildNeutralDemoLayoutGrammar() {
    if (!irApi || typeof irApi.buildNeutralDemoSurfaceIR !== "function") {
      throw new Error("McelSemanticSurfaceIR is required to build the neutral demo layout grammar.");
    }
    const ir = irApi.buildNeutralDemoSurfaceIR();
    return buildSharedLayoutGrammar(ir, {
      viewport: {width: 900, height: 520, safeMargin: 24},
      regions: [
        {id: "region.workbench", role: "workbench", x: 60, y: 80, width: 780, height: 360}
      ],
      nodePorts: {
        "Observation.A": ["east", "south"],
        "Hypothesis.B": ["west", "north"]
      }
    });
  }

  return Object.freeze({
    contractVersion,
    surfaceIrContractVersion,
    allowedRouteKinds,
    defaultPorts,
    buildSharedLayoutGrammar,
    validateSharedLayoutGrammar,
    canonicalizeLayoutGrammar,
    layoutFingerprint,
    buildNeutralDemoLayoutGrammar,
    boxForLayout
  });
})();

if (typeof window !== "undefined") {
  window.McelSharedLayoutGrammar = McelSharedLayoutGrammar;
}
