var McelSemanticSurfaceIR = (() => {
  "use strict";

  const contractVersion = "mcel.semantic-surface-ir.v1";
  const ridgeContractVersion = "mcel.semantic-surface-ridges.v1";

  const fallbackAttributes = Object.freeze({
    surfaceId: "data-mcel-surface-id",
    surfaceKind: "data-mcel-surface-kind",
    surfaceRole: "data-mcel-surface-role",
    surfaceContract: "data-mcel-surface-contract",
    renderer: "data-mcel-renderer",
    projection: "data-mcel-projection",

    nodeId: "data-mcel-node-id",
    nodeType: "data-mcel-node-type",
    nodeLabel: "data-mcel-node-label",

    edgeId: "data-mcel-edge-id",
    edgeKind: "data-mcel-edge-kind",
    edgeFrom: "data-mcel-from",
    edgeTo: "data-mcel-to",
    relation: "data-mcel-relation",
    causalLink: "data-mcel-causal-link",
    allowedInferences: "data-mcel-allowed-inferences",
    forbiddenInferences: "data-mcel-forbidden-inferences",

    regionId: "data-mcel-region",
    regionRole: "data-mcel-region-role",

    controlId: "data-mcel-control",
    controlAction: "data-mcel-control-action",
    controlReveals: "data-mcel-reveals",

    source: "data-mcel-source",
    provenance: "data-mcel-provenance",
    symbol: "data-mcel-symbol",
    channel: "data-mcel-channel",
    signal: "data-mcel-signal",

    homeRegion: "data-mcel-home-region",
    actualRegion: "data-mcel-actual-region",
    teleported: "data-mcel-teleported",

    layoutAnchorX: "data-layout-anchor-x",
    layoutAnchorY: "data-layout-anchor-y",
    layoutWidth: "data-layout-width",
    layoutHeight: "data-layout-height",
    layoutZ: "data-layout-z",
    layoutRegion: "data-layout-region",
    layoutRouteKind: "data-layout-route-kind",
    layoutFromPort: "data-layout-from-port",
    layoutToPort: "data-layout-to-port"
  });

  const ridgeApi = (() => {
    if (typeof McelSemanticSurfaceRidges !== "undefined") return McelSemanticSurfaceRidges;
    if (typeof window !== "undefined" && window.McelSemanticSurfaceRidges) return window.McelSemanticSurfaceRidges;
    return null;
  })();

  const attributes = ridgeApi && ridgeApi.attributes ? ridgeApi.attributes : fallbackAttributes;

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value).trim();
  }

  function splitCsv(value) {
    const seen = new Set();
    const out = [];
    safeString(value).split(",").map((item) => item.trim()).filter(Boolean).forEach((item) => {
      if (seen.has(item)) return;
      seen.add(item);
      out.push(item);
    });
    return Object.freeze(out);
  }

  function safeBoolean(value) {
    const normalized = safeString(value).toLowerCase();
    if (["true", "1", "yes", "y"].includes(normalized)) return true;
    if (["false", "0", "no", "n", ""].includes(normalized)) return false;
    return false;
  }

  function finiteNumberOrNull(value) {
    const text = safeString(value);
    if (!text) return null;
    const number = Number(text);
    return Number.isFinite(number) ? number : null;
  }

  function cloneObject(value) {
    if (!value || typeof value !== "object") return {};
    return Object.assign({}, value);
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

  function readAttributes(input) {
    if (!input) return {};
    if (ridgeApi && typeof ridgeApi.readAttributes === "function") return ridgeApi.readAttributes(input);
    if (input.attributes && typeof input.getAttribute === "function") {
      const out = {};
      Array.from(input.attributes || []).forEach((attr) => {
        out[attr.name] = input.getAttribute(attr.name) || "";
      });
      return out;
    }
    if (input.attributes && typeof input.attributes === "object" && !Array.isArray(input.attributes)) {
      return Object.assign({}, input.attributes);
    }
    if (typeof input === "object") return Object.assign({}, input);
    return {};
  }

  function classifyAttributes(input) {
    if (ridgeApi && typeof ridgeApi.classifyAttributes === "function") return ridgeApi.classifyAttributes(input);
    const attrs = readAttributes(input);
    if (attrs[attributes.nodeId] || attrs[attributes.nodeType]) return "node";
    if (attrs[attributes.edgeId] || attrs[attributes.edgeKind] || attrs[attributes.edgeFrom] || attrs[attributes.edgeTo]) return "edge";
    if (attrs[attributes.controlId] || attrs[attributes.controlAction] || attrs[attributes.controlReveals]) return "control";
    if (attrs[attributes.surfaceId] || attrs[attributes.surfaceKind]) return "surface";
    if (attrs[attributes.regionId] || attrs[attributes.regionRole]) return "region";
    return "unknown";
  }

  function normalizeSurface(attrs) {
    return Object.freeze({
      id: safeString(attrs[attributes.surfaceId]),
      kind: safeString(attrs[attributes.surfaceKind]),
      role: safeString(attrs[attributes.surfaceRole]),
      contract: safeString(attrs[attributes.surfaceContract]),
      renderer: safeString(attrs[attributes.renderer]),
      projection: safeString(attrs[attributes.projection])
    });
  }

  function normalizeRegion(attrs) {
    return Object.freeze({
      id: safeString(attrs[attributes.regionId]),
      role: safeString(attrs[attributes.regionRole])
    });
  }

  function normalizeNode(attrs) {
    return Object.freeze({
      id: safeString(attrs[attributes.nodeId]),
      type: safeString(attrs[attributes.nodeType]),
      label: safeString(attrs[attributes.nodeLabel]),
      source: safeString(attrs[attributes.source]),
      provenance: safeString(attrs[attributes.provenance]),
      symbol: safeString(attrs[attributes.symbol]),
      channel: safeString(attrs[attributes.channel]),
      signal: safeString(attrs[attributes.signal]),
      homeRegion: safeString(attrs[attributes.homeRegion]),
      actualRegion: safeString(attrs[attributes.actualRegion]) || safeString(attrs[attributes.homeRegion]),
      teleported: safeBoolean(attrs[attributes.teleported])
    });
  }

  function normalizeEdge(attrs) {
    return Object.freeze({
      id: safeString(attrs[attributes.edgeId]),
      kind: safeString(attrs[attributes.edgeKind]),
      from: safeString(attrs[attributes.edgeFrom]),
      to: safeString(attrs[attributes.edgeTo]),
      relation: safeString(attrs[attributes.relation]),
      causalLink: safeBoolean(attrs[attributes.causalLink]),
      allowedInferences: splitCsv(attrs[attributes.allowedInferences]),
      forbiddenInferences: splitCsv(attrs[attributes.forbiddenInferences])
    });
  }

  function normalizeControl(attrs) {
    return Object.freeze({
      id: safeString(attrs[attributes.controlId]),
      action: safeString(attrs[attributes.controlAction]),
      reveals: safeString(attrs[attributes.controlReveals])
    });
  }

  function normalizeNodeLayout(node, attrs) {
    const layout = {
      id: node.id,
      anchorX: finiteNumberOrNull(attrs[attributes.layoutAnchorX]),
      anchorY: finiteNumberOrNull(attrs[attributes.layoutAnchorY]),
      width: finiteNumberOrNull(attrs[attributes.layoutWidth]),
      height: finiteNumberOrNull(attrs[attributes.layoutHeight]),
      z: finiteNumberOrNull(attrs[attributes.layoutZ]),
      region: safeString(attrs[attributes.layoutRegion]) || node.homeRegion
    };
    const hasLayout = layout.anchorX !== null || layout.anchorY !== null || layout.width !== null || layout.height !== null || layout.z !== null || !!layout.region;
    return hasLayout ? Object.freeze(layout) : null;
  }

  function normalizeEdgeLayout(edge, attrs) {
    const layout = {
      id: edge.id,
      routeKind: safeString(attrs[attributes.layoutRouteKind]),
      fromPort: safeString(attrs[attributes.layoutFromPort]),
      toPort: safeString(attrs[attributes.layoutToPort]),
      z: finiteNumberOrNull(attrs[attributes.layoutZ])
    };
    const hasLayout = !!layout.routeKind || !!layout.fromPort || !!layout.toPort || layout.z !== null;
    return hasLayout ? Object.freeze(layout) : null;
  }

  function emptyIR(surface) {
    return Object.freeze({
      contractVersion,
      ridgeContractVersion,
      surface: Object.freeze(surface || {id: "", kind: "", role: "", contract: "", renderer: "", projection: ""}),
      graph: Object.freeze({
        nodes: Object.freeze([]),
        edges: Object.freeze([]),
        regions: Object.freeze([]),
        controls: Object.freeze([])
      }),
      layout: Object.freeze({
        nodes: Object.freeze([]),
        edges: Object.freeze([]),
        controls: Object.freeze([])
      })
    });
  }

  function buildSurfaceIRFromRidges(records, options) {
    const opts = Object.assign({requireSurface: true}, options || {});
    const surfaces = [];
    const nodes = [];
    const edges = [];
    const regions = [];
    const controls = [];
    const nodeLayouts = [];
    const edgeLayouts = [];
    const controlLayouts = [];

    (records || []).forEach((record) => {
      const attrs = readAttributes(record);
      const kind = classifyAttributes(attrs);
      if (kind === "surface") {
        surfaces.push(normalizeSurface(attrs));
      } else if (kind === "region") {
        regions.push(normalizeRegion(attrs));
      } else if (kind === "node") {
        const node = normalizeNode(attrs);
        nodes.push(node);
        const layout = normalizeNodeLayout(node, attrs);
        if (layout) nodeLayouts.push(layout);
      } else if (kind === "edge") {
        const edge = normalizeEdge(attrs);
        edges.push(edge);
        const layout = normalizeEdgeLayout(edge, attrs);
        if (layout) edgeLayouts.push(layout);
      } else if (kind === "control") {
        const control = normalizeControl(attrs);
        controls.push(control);
        const layout = {
          id: control.id,
          anchorX: finiteNumberOrNull(attrs[attributes.layoutAnchorX]),
          anchorY: finiteNumberOrNull(attrs[attributes.layoutAnchorY]),
          width: finiteNumberOrNull(attrs[attributes.layoutWidth]),
          height: finiteNumberOrNull(attrs[attributes.layoutHeight]),
          z: finiteNumberOrNull(attrs[attributes.layoutZ])
        };
        if (layout.anchorX !== null || layout.anchorY !== null || layout.width !== null || layout.height !== null || layout.z !== null) {
          controlLayouts.push(Object.freeze(layout));
        }
      }
    });

    const surface = surfaces[0] || Object.freeze({
      id: opts.defaultSurfaceId || "",
      kind: opts.defaultSurfaceKind || "",
      role: opts.defaultSurfaceRole || "",
      contract: "",
      renderer: "",
      projection: ""
    });

    const ir = Object.freeze({
      contractVersion,
      ridgeContractVersion,
      surface,
      graph: Object.freeze({
        nodes: sortById(nodes),
        edges: sortById(edges),
        regions: sortById(regions),
        controls: sortById(controls)
      }),
      layout: Object.freeze({
        nodes: sortById(nodeLayouts),
        edges: sortById(edgeLayouts),
        controls: sortById(controlLayouts)
      })
    });

    const validation = validateSurfaceIR(ir, {requireSurface: opts.requireSurface});
    return Object.freeze({
      ir,
      valid: validation.valid,
      diagnostics: validation.diagnostics
    });
  }

  function idSet(items) {
    return new Set((items || []).map((item) => item.id).filter(Boolean));
  }

  function duplicateDiagnostics(items, kind) {
    const seen = new Set();
    const diagnostics = [];
    (items || []).forEach((item) => {
      if (!item.id) {
        diagnostics.push(diagnostic(`missing-${kind}-id`, "error", `Surface ${kind} is missing a stable id.`, {kind}));
        return;
      }
      if (seen.has(item.id)) {
        diagnostics.push(diagnostic(`duplicate-${kind}-id`, "error", `Duplicate ${kind} id in SemanticSurfaceIR.`, {id: item.id}));
      }
      seen.add(item.id);
    });
    return diagnostics;
  }

  function validateSurfaceIR(ir, options) {
    const opts = Object.assign({requireSurface: true}, options || {});
    const diagnostics = [];
    const subject = ir || emptyIR();

    if (opts.requireSurface) {
      if (!subject.surface || !subject.surface.id) {
        diagnostics.push(diagnostic("missing-surface-id", "error", "SemanticSurfaceIR must identify one surface.", {}));
      }
      if (!subject.surface || !subject.surface.kind) {
        diagnostics.push(diagnostic("missing-surface-kind", "error", "SemanticSurfaceIR must identify the surface kind.", {surfaceId: subject.surface && subject.surface.id || ""}));
      }
    }

    duplicateDiagnostics(subject.graph && subject.graph.nodes, "node").forEach((item) => diagnostics.push(item));
    duplicateDiagnostics(subject.graph && subject.graph.edges, "edge").forEach((item) => diagnostics.push(item));
    duplicateDiagnostics(subject.graph && subject.graph.regions, "region").forEach((item) => diagnostics.push(item));
    duplicateDiagnostics(subject.graph && subject.graph.controls, "control").forEach((item) => diagnostics.push(item));

    const nodes = idSet(subject.graph && subject.graph.nodes);
    const regions = idSet(subject.graph && subject.graph.regions);
    const controls = idSet(subject.graph && subject.graph.controls);

    (subject.graph && subject.graph.nodes || []).forEach((node) => {
      if (!node.type) diagnostics.push(diagnostic("missing-node-type", "error", "Surface node is missing a node type.", {nodeId: node.id}));
      if (!node.source) diagnostics.push(diagnostic("missing-node-source", "error", "Surface node is missing a source.", {nodeId: node.id}));
      if (!node.provenance) diagnostics.push(diagnostic("missing-node-provenance", "error", "Surface node is missing provenance.", {nodeId: node.id}));
      if (node.homeRegion && regions.size > 0 && !regions.has(node.homeRegion)) {
        diagnostics.push(diagnostic("node-home-region-missing", "error", "Surface node references a missing home region.", {nodeId: node.id, region: node.homeRegion}));
      }
      if (node.actualRegion && regions.size > 0 && !regions.has(node.actualRegion)) {
        diagnostics.push(diagnostic("node-actual-region-missing", "warning", "Surface node currently appears outside a declared region.", {nodeId: node.id, region: node.actualRegion}));
      }
    });

    (subject.graph && subject.graph.edges || []).forEach((edge) => {
      if (!edge.kind) diagnostics.push(diagnostic("missing-edge-kind", "error", "Surface edge is missing an edge kind.", {edgeId: edge.id}));
      if (!edge.from) diagnostics.push(diagnostic("missing-edge-from", "error", "Surface edge is missing a source node id.", {edgeId: edge.id}));
      if (!edge.to) diagnostics.push(diagnostic("missing-edge-to", "error", "Surface edge is missing a target node id.", {edgeId: edge.id}));
      if (!edge.relation) diagnostics.push(diagnostic("missing-edge-relation", "error", "Surface edge is missing a relation.", {edgeId: edge.id}));
      if (edge.from && !nodes.has(edge.from)) diagnostics.push(diagnostic("edge-from-node-missing", "error", "Surface edge references a missing source node.", {edgeId: edge.id, from: edge.from}));
      if (edge.to && !nodes.has(edge.to)) diagnostics.push(diagnostic("edge-to-node-missing", "error", "Surface edge references a missing target node.", {edgeId: edge.id, to: edge.to}));
    });

    (subject.layout && subject.layout.nodes || []).forEach((layout) => {
      if (layout.id && !nodes.has(layout.id)) {
        diagnostics.push(diagnostic("layout-node-missing", "error", "Layout references a missing surface node.", {nodeId: layout.id}));
      }
      if (layout.width !== null && layout.width <= 0) {
        diagnostics.push(diagnostic("layout-node-width-invalid", "error", "Layout node width must be positive.", {nodeId: layout.id, width: layout.width}));
      }
      if (layout.height !== null && layout.height <= 0) {
        diagnostics.push(diagnostic("layout-node-height-invalid", "error", "Layout node height must be positive.", {nodeId: layout.id, height: layout.height}));
      }
      if (layout.region && regions.size > 0 && !regions.has(layout.region)) {
        diagnostics.push(diagnostic("layout-region-missing", "error", "Layout references a missing region.", {nodeId: layout.id, region: layout.region}));
      }
    });

    (subject.layout && subject.layout.edges || []).forEach((layout) => {
      const edgeIds = idSet(subject.graph && subject.graph.edges);
      if (layout.id && !edgeIds.has(layout.id)) {
        diagnostics.push(diagnostic("layout-edge-missing", "error", "Layout references a missing surface edge.", {edgeId: layout.id}));
      }
      if ((layout.fromPort || layout.toPort) && !layout.routeKind) {
        diagnostics.push(diagnostic("layout-edge-route-kind-missing", "error", "Ported edge layout must declare a route kind.", {edgeId: layout.id}));
      }
    });

    (subject.layout && subject.layout.controls || []).forEach((layout) => {
      if (layout.id && !controls.has(layout.id)) {
        diagnostics.push(diagnostic("layout-control-missing", "error", "Layout references a missing surface control.", {controlId: layout.id}));
      }
    });

    return Object.freeze({
      contractVersion,
      valid: diagnostics.filter((item) => item.severity === "error").length === 0,
      errorCount: diagnostics.filter((item) => item.severity === "error").length,
      warningCount: diagnostics.filter((item) => item.severity === "warning").length,
      diagnostics: Object.freeze(diagnostics)
    });
  }

  function canonicalizeSurfaceIR(ir) {
    const source = ir && ir.ir ? ir.ir : ir;
    if (!source) return emptyIR();
    return Object.freeze({
      contractVersion,
      ridgeContractVersion,
      surface: Object.freeze(Object.assign({id: "", kind: "", role: "", contract: "", renderer: "", projection: ""}, source.surface || {})),
      graph: Object.freeze({
        nodes: sortById((source.graph && source.graph.nodes || []).map((node) => Object.freeze(Object.assign({}, node)))),
        edges: sortById((source.graph && source.graph.edges || []).map((edge) => Object.freeze(Object.assign({}, edge, {
          allowedInferences: Object.freeze([...(edge.allowedInferences || [])].sort()),
          forbiddenInferences: Object.freeze([...(edge.forbiddenInferences || [])].sort())
        })))),
        regions: sortById((source.graph && source.graph.regions || []).map((region) => Object.freeze(Object.assign({}, region)))),
        controls: sortById((source.graph && source.graph.controls || []).map((control) => Object.freeze(Object.assign({}, control))))
      }),
      layout: Object.freeze({
        nodes: sortById((source.layout && source.layout.nodes || []).map((layout) => Object.freeze(Object.assign({}, layout)))),
        edges: sortById((source.layout && source.layout.edges || []).map((layout) => Object.freeze(Object.assign({}, layout)))),
        controls: sortById((source.layout && source.layout.controls || []).map((layout) => Object.freeze(Object.assign({}, layout))))
      })
    });
  }

  function semanticFingerprint(ir) {
    return JSON.stringify(canonicalizeSurfaceIR(ir));
  }

  function exportSurfaceIRAsRidgeRecords(ir) {
    const canonical = canonicalizeSurfaceIR(ir);
    const records = [];
    records.push(Object.freeze({
      [attributes.surfaceId]: canonical.surface.id,
      [attributes.surfaceKind]: canonical.surface.kind,
      [attributes.surfaceRole]: canonical.surface.role,
      [attributes.surfaceContract]: canonical.surface.contract || ridgeContractVersion,
      [attributes.renderer]: canonical.surface.renderer,
      [attributes.projection]: canonical.surface.projection
    }));
    canonical.graph.regions.forEach((region) => {
      records.push(Object.freeze({
        [attributes.regionId]: region.id,
        [attributes.regionRole]: region.role
      }));
    });
    const nodeLayoutById = new Map(canonical.layout.nodes.map((layout) => [layout.id, layout]));
    canonical.graph.nodes.forEach((node) => {
      const layout = nodeLayoutById.get(node.id) || {};
      records.push(Object.freeze({
        [attributes.nodeId]: node.id,
        [attributes.nodeType]: node.type,
        [attributes.nodeLabel]: node.label,
        [attributes.source]: node.source,
        [attributes.provenance]: node.provenance,
        [attributes.symbol]: node.symbol,
        [attributes.channel]: node.channel,
        [attributes.signal]: node.signal,
        [attributes.homeRegion]: node.homeRegion,
        [attributes.actualRegion]: node.actualRegion,
        [attributes.teleported]: node.teleported ? "true" : "false",
        [attributes.layoutAnchorX]: layout.anchorX === null || layout.anchorX === undefined ? "" : layout.anchorX,
        [attributes.layoutAnchorY]: layout.anchorY === null || layout.anchorY === undefined ? "" : layout.anchorY,
        [attributes.layoutWidth]: layout.width === null || layout.width === undefined ? "" : layout.width,
        [attributes.layoutHeight]: layout.height === null || layout.height === undefined ? "" : layout.height,
        [attributes.layoutZ]: layout.z === null || layout.z === undefined ? "" : layout.z,
        [attributes.layoutRegion]: layout.region || node.homeRegion
      }));
    });
    const edgeLayoutById = new Map(canonical.layout.edges.map((layout) => [layout.id, layout]));
    canonical.graph.edges.forEach((edge) => {
      const layout = edgeLayoutById.get(edge.id) || {};
      records.push(Object.freeze({
        [attributes.edgeId]: edge.id,
        [attributes.edgeKind]: edge.kind,
        [attributes.edgeFrom]: edge.from,
        [attributes.edgeTo]: edge.to,
        [attributes.relation]: edge.relation,
        [attributes.causalLink]: edge.causalLink ? "true" : "false",
        [attributes.allowedInferences]: (edge.allowedInferences || []).join(","),
        [attributes.forbiddenInferences]: (edge.forbiddenInferences || []).join(","),
        [attributes.layoutRouteKind]: layout.routeKind || "",
        [attributes.layoutFromPort]: layout.fromPort || "",
        [attributes.layoutToPort]: layout.toPort || "",
        [attributes.layoutZ]: layout.z === null || layout.z === undefined ? "" : layout.z
      }));
    });
    const controlLayoutById = new Map(canonical.layout.controls.map((layout) => [layout.id, layout]));
    canonical.graph.controls.forEach((control) => {
      const layout = controlLayoutById.get(control.id) || {};
      records.push(Object.freeze({
        [attributes.controlId]: control.id,
        [attributes.controlAction]: control.action,
        [attributes.controlReveals]: control.reveals,
        [attributes.layoutAnchorX]: layout.anchorX === null || layout.anchorX === undefined ? "" : layout.anchorX,
        [attributes.layoutAnchorY]: layout.anchorY === null || layout.anchorY === undefined ? "" : layout.anchorY,
        [attributes.layoutWidth]: layout.width === null || layout.width === undefined ? "" : layout.width,
        [attributes.layoutHeight]: layout.height === null || layout.height === undefined ? "" : layout.height,
        [attributes.layoutZ]: layout.z === null || layout.z === undefined ? "" : layout.z
      }));
    });
    return Object.freeze(records);
  }

  function buildNeutralDemoSurfaceIR() {
    const records = [
      {
        [attributes.surfaceId]: "surface.demo-neutral",
        [attributes.surfaceKind]: "semantic-workbench",
        [attributes.surfaceRole]: "round-trip-fixture",
        [attributes.surfaceContract]: ridgeContractVersion,
        [attributes.renderer]: "neutral-ir-fixture"
      },
      {
        [attributes.regionId]: "region.workbench",
        [attributes.regionRole]: "workbench"
      },
      {
        [attributes.nodeId]: "Observation.A",
        [attributes.nodeType]: "observation",
        [attributes.nodeLabel]: "Observation A",
        [attributes.source]: "fixture",
        [attributes.provenance]: "fixture:observation-a",
        [attributes.symbol]: "○",
        [attributes.homeRegion]: "region.workbench",
        [attributes.actualRegion]: "region.workbench",
        [attributes.layoutAnchorX]: "280",
        [attributes.layoutAnchorY]: "240",
        [attributes.layoutWidth]: "180",
        [attributes.layoutHeight]: "80",
        [attributes.layoutRegion]: "region.workbench"
      },
      {
        [attributes.nodeId]: "Hypothesis.B",
        [attributes.nodeType]: "hypothesis",
        [attributes.nodeLabel]: "Hypothesis B",
        [attributes.source]: "fixture",
        [attributes.provenance]: "fixture:hypothesis-b",
        [attributes.symbol]: "?",
        [attributes.homeRegion]: "region.workbench",
        [attributes.actualRegion]: "region.workbench",
        [attributes.layoutAnchorX]: "620",
        [attributes.layoutAnchorY]: "240",
        [attributes.layoutWidth]: "180",
        [attributes.layoutHeight]: "80",
        [attributes.layoutRegion]: "region.workbench"
      },
      {
        [attributes.edgeId]: "EDGE.observation-supports-hypothesis",
        [attributes.edgeKind]: "SUPPORTS",
        [attributes.edgeFrom]: "Observation.A",
        [attributes.edgeTo]: "Hypothesis.B",
        [attributes.relation]: "evidence_for",
        [attributes.causalLink]: "false",
        [attributes.allowedInferences]: "support,comparison",
        [attributes.forbiddenInferences]: "identity,direct_causality",
        [attributes.layoutRouteKind]: "cubic",
        [attributes.layoutFromPort]: "east",
        [attributes.layoutToPort]: "west"
      },
      {
        [attributes.controlId]: "trace_evidence",
        [attributes.controlAction]: "trace",
        [attributes.controlReveals]: "SUPPORTS",
        [attributes.layoutAnchorX]: "460",
        [attributes.layoutAnchorY]: "360",
        [attributes.layoutWidth]: "140",
        [attributes.layoutHeight]: "38"
      }
    ];
    return buildSurfaceIRFromRidges(records).ir;
  }

  return Object.freeze({
    contractVersion,
    ridgeContractVersion,
    attributes,
    readAttributes,
    classifyAttributes,
    buildSurfaceIRFromRidges,
    validateSurfaceIR,
    canonicalizeSurfaceIR,
    semanticFingerprint,
    exportSurfaceIRAsRidgeRecords,
    buildNeutralDemoSurfaceIR
  });
})();

if (typeof window !== "undefined") {
  window.McelSemanticSurfaceIR = McelSemanticSurfaceIR;
}
