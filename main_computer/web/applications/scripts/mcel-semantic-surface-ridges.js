var McelSemanticSurfaceRidges = (() => {
  "use strict";

  const contractVersion = "mcel.semantic-surface-ridges.v1";
  const attributePrefix = "data-mcel-";

  const attributes = Object.freeze({
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

  const ridgeKinds = Object.freeze([
    "surface",
    "node",
    "edge",
    "region",
    "control",
    "layout"
  ]);

  const requiredAttributes = Object.freeze({
    surface: Object.freeze([
      attributes.surfaceId,
      attributes.surfaceKind
    ]),
    node: Object.freeze([
      attributes.nodeId,
      attributes.nodeType,
      attributes.source,
      attributes.provenance
    ]),
    edge: Object.freeze([
      attributes.edgeId,
      attributes.edgeKind,
      attributes.edgeFrom,
      attributes.edgeTo,
      attributes.relation
    ]),
    region: Object.freeze([
      attributes.regionId
    ]),
    control: Object.freeze([
      attributes.controlId
    ]),
    layout: Object.freeze([])
  });

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value).trim();
  }

  function normalizeToken(value) {
    return safeString(value)
      .replace(/\s+/g, "-")
      .replace(/[^A-Za-z0-9_.:-]/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "");
  }

  function stableUnique(values) {
    const seen = new Set();
    const out = [];
    (values || []).forEach((value) => {
      const normalized = safeString(value);
      if (!normalized || seen.has(normalized)) return;
      seen.add(normalized);
      out.push(normalized);
    });
    return out;
  }

  function splitCsv(value) {
    return stableUnique(safeString(value).split(",").map((item) => item.trim()).filter(Boolean));
  }

  function readAttributes(input) {
    if (!input) return {};
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
    const attrs = readAttributes(input);
    if (attrs[attributes.nodeId] || attrs[attributes.nodeType]) return "node";
    if (attrs[attributes.edgeId] || attrs[attributes.edgeKind] || attrs[attributes.edgeFrom] || attrs[attributes.edgeTo]) return "edge";
    if (attrs[attributes.controlId] || attrs[attributes.controlAction] || attrs[attributes.controlReveals]) return "control";
    if (attrs[attributes.surfaceId] || attrs[attributes.surfaceKind]) return "surface";
    if (attrs[attributes.regionId] || attrs[attributes.regionRole]) return "region";
    if (
      attrs[attributes.layoutAnchorX] ||
      attrs[attributes.layoutRouteKind] ||
      attrs[attributes.layoutRegion]
    ) {
      return "layout";
    }
    return "unknown";
  }

  function requiredForKind(kind) {
    return requiredAttributes[kind] || Object.freeze([]);
  }

  function identityForKind(kind, attrs) {
    if (kind === "surface") return attrs[attributes.surfaceId] || "";
    if (kind === "node") return attrs[attributes.nodeId] || "";
    if (kind === "edge") return attrs[attributes.edgeId] || "";
    if (kind === "region") return attrs[attributes.regionId] || "";
    if (kind === "control") return attrs[attributes.controlId] || "";
    if (kind === "layout") {
      return attrs[attributes.layoutRegion] ||
        attrs[attributes.layoutRouteKind] ||
        attrs[attributes.layoutAnchorX] ||
        "layout";
    }
    return "";
  }

  function diagnostic(code, severity, message, detail = {}) {
    return Object.freeze({
      code,
      severity,
      message,
      detail: Object.freeze(Object.assign({}, detail))
    });
  }

  function validateRidgeRecord(record) {
    const attrs = readAttributes(record);
    const kind = classifyAttributes(attrs);
    const diagnostics = [];

    if (kind === "unknown") {
      diagnostics.push(diagnostic(
        "unknown-ridge-kind",
        "warning",
        "Record does not expose a recognized MCEL semantic surface ridge.",
        {attributes: Object.keys(attrs).sort()}
      ));
      return Object.freeze({
        kind,
        id: "",
        valid: false,
        diagnostics: Object.freeze(diagnostics)
      });
    }

    requiredForKind(kind).forEach((name) => {
      if (!safeString(attrs[name])) {
        diagnostics.push(diagnostic(
          "missing-required-ridge",
          "error",
          `Missing required ${kind} ridge attribute: ${name}`,
          {kind, attribute: name}
        ));
      }
    });

    if (kind === "edge") {
      const edgeKind = normalizeToken(attrs[attributes.edgeKind]);
      if (!edgeKind) {
        diagnostics.push(diagnostic(
          "invalid-edge-kind",
          "error",
          "Edge ridge has no stable edge kind.",
          {edgeId: attrs[attributes.edgeId] || ""}
        ));
      }
      if (attrs[attributes.edgeFrom] && attrs[attributes.edgeTo] && attrs[attributes.edgeFrom] === attrs[attributes.edgeTo]) {
        diagnostics.push(diagnostic(
          "self-edge-needs-explicit-relation",
          "warning",
          "Self edges are allowed only when their relation is explicit.",
          {edgeId: attrs[attributes.edgeId] || "", relation: attrs[attributes.relation] || ""}
        ));
      }
    }

    if (kind === "node") {
      const nodeId = normalizeToken(attrs[attributes.nodeId]);
      const nodeType = normalizeToken(attrs[attributes.nodeType]);
      if (!nodeId || !nodeType) {
        diagnostics.push(diagnostic(
          "invalid-node-identity",
          "error",
          "Node ridge must have stable node id and node type tokens.",
          {nodeId, nodeType}
        ));
      }
    }

    return Object.freeze({
      kind,
      id: identityForKind(kind, attrs),
      valid: !diagnostics.some((item) => item.severity === "error"),
      diagnostics: Object.freeze(diagnostics)
    });
  }

  function canonicalizeRidgeRecord(record) {
    const attrs = readAttributes(record);
    const kind = classifyAttributes(attrs);
    const id = identityForKind(kind, attrs);
    const data = {};
    Object.keys(attrs)
      .filter((name) => name.startsWith(attributePrefix) || name.startsWith("data-layout-"))
      .sort()
      .forEach((name) => {
        const value = safeString(attrs[name]);
        if (value) data[name] = value;
      });

    if (data[attributes.allowedInferences]) {
      data[attributes.allowedInferences] = splitCsv(data[attributes.allowedInferences]).join(",");
    }
    if (data[attributes.forbiddenInferences]) {
      data[attributes.forbiddenInferences] = splitCsv(data[attributes.forbiddenInferences]).join(",");
    }

    return Object.freeze({
      kind,
      id,
      attributes: Object.freeze(data)
    });
  }

  function canonicalizeRidgeRecords(records) {
    return Object.freeze(
      (records || [])
        .map(canonicalizeRidgeRecord)
        .filter((record) => record.kind !== "unknown")
        .sort((a, b) => `${a.kind}:${a.id}`.localeCompare(`${b.kind}:${b.id}`))
    );
  }

  function validateSurfaceRidges(records) {
    const canonical = canonicalizeRidgeRecords(records);
    const diagnostics = [];
    const seen = new Set();
    const nodeIds = new Set();

    canonical.forEach((record) => {
      validateRidgeRecord(record.attributes).diagnostics.forEach((item) => diagnostics.push(item));
      const key = `${record.kind}:${record.id}`;
      if (record.id && seen.has(key)) {
        diagnostics.push(diagnostic(
          "duplicate-ridge-id",
          "error",
          "Duplicate semantic ridge identity within the same surface.",
          {kind: record.kind, id: record.id}
        ));
      }
      seen.add(key);
      if (record.kind === "node") nodeIds.add(record.id);
    });

    canonical
      .filter((record) => record.kind === "edge")
      .forEach((edge) => {
        const from = edge.attributes[attributes.edgeFrom] || "";
        const to = edge.attributes[attributes.edgeTo] || "";
        if (from && !nodeIds.has(from)) {
          diagnostics.push(diagnostic(
            "edge-from-node-missing",
            "error",
            "Edge references a missing source node.",
            {edgeId: edge.id, from}
          ));
        }
        if (to && !nodeIds.has(to)) {
          diagnostics.push(diagnostic(
            "edge-to-node-missing",
            "error",
            "Edge references a missing target node.",
            {edgeId: edge.id, to}
          ));
        }
      });

    return Object.freeze({
      contractVersion,
      valid: !diagnostics.some((item) => item.severity === "error"),
      recordCount: canonical.length,
      records: canonical,
      diagnostics: Object.freeze(diagnostics)
    });
  }

  function semanticFingerprint(records) {
    const canonical = canonicalizeRidgeRecords(records).map((record) => ({
      kind: record.kind,
      id: record.id,
      attributes: record.attributes
    }));
    return JSON.stringify(canonical);
  }

  function buildNeutralDemoRidgeRecords() {
    return Object.freeze([
      Object.freeze({
        [attributes.surfaceId]: "surface.demo-neutral",
        [attributes.surfaceKind]: "semantic-workbench",
        [attributes.surfaceRole]: "round-trip-fixture",
        [attributes.surfaceContract]: contractVersion,
        [attributes.renderer]: "neutral-fixture"
      }),
      Object.freeze({
        [attributes.regionId]: "region.workbench",
        [attributes.regionRole]: "workbench"
      }),
      Object.freeze({
        [attributes.nodeId]: "Observation.A",
        [attributes.nodeType]: "observation",
        [attributes.nodeLabel]: "Observation A",
        [attributes.source]: "fixture",
        [attributes.provenance]: "fixture:observation-a",
        [attributes.symbol]: "○",
        [attributes.homeRegion]: "region.workbench",
        [attributes.actualRegion]: "region.workbench"
      }),
      Object.freeze({
        [attributes.nodeId]: "Hypothesis.B",
        [attributes.nodeType]: "hypothesis",
        [attributes.nodeLabel]: "Hypothesis B",
        [attributes.source]: "fixture",
        [attributes.provenance]: "fixture:hypothesis-b",
        [attributes.symbol]: "?",
        [attributes.homeRegion]: "region.workbench",
        [attributes.actualRegion]: "region.workbench"
      }),
      Object.freeze({
        [attributes.edgeId]: "EDGE.observation-supports-hypothesis",
        [attributes.edgeKind]: "SUPPORTS",
        [attributes.edgeFrom]: "Observation.A",
        [attributes.edgeTo]: "Hypothesis.B",
        [attributes.relation]: "evidence_for",
        [attributes.causalLink]: "false",
        [attributes.allowedInferences]: "support,comparison",
        [attributes.forbiddenInferences]: "identity,direct_causality"
      }),
      Object.freeze({
        [attributes.controlId]: "trace_evidence",
        [attributes.controlAction]: "trace",
        [attributes.controlReveals]: "SUPPORTS"
      })
    ]);
  }

  return Object.freeze({
    contractVersion,
    attributePrefix,
    attributes,
    ridgeKinds,
    requiredAttributes,
    normalizeToken,
    splitCsv,
    readAttributes,
    classifyAttributes,
    requiredForKind,
    validateRidgeRecord,
    canonicalizeRidgeRecord,
    canonicalizeRidgeRecords,
    validateSurfaceRidges,
    semanticFingerprint,
    buildNeutralDemoRidgeRecords
  });
})();

if (typeof window !== "undefined") {
  window.McelSemanticSurfaceRidges = McelSemanticSurfaceRidges;
}
