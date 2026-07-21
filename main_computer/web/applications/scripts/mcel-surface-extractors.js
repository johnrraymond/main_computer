var McelSurfaceExtractors = (() => {
  "use strict";

  const contractVersion = "mcel.surface-extractors.v1";
  const surfaceIrContractVersion = "mcel.semantic-surface-ir.v1";
  const layoutContractVersion = "mcel.shared-layout-grammar.v1";

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

  const attributes = ridgeApi && ridgeApi.attributes ? ridgeApi.attributes : fallbackAttributes;
  const voidElements = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value).trim();
  }

  function safeBoolean(value) {
    const normalized = safeString(value).toLowerCase();
    return ["true", "1", "yes", "y", "authoritative"].includes(normalized);
  }

  function finiteNumberOrNull(value) {
    const text = safeString(value);
    if (!text) return null;
    const number = Number(text);
    return Number.isFinite(number) ? number : null;
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

  function diagnostic(code, severity, finding, detail) {
    return Object.freeze({
      code,
      severity,
      finding,
      detail: Object.freeze(detail || {})
    });
  }

  function decodeHtmlEntities(value) {
    const text = safeString(value);
    if (!text.includes("&")) return text;
    return text.replace(/&(#x[0-9a-fA-F]+|#[0-9]+|amp|lt|gt|quot|apos);/g, (match, entity) => {
      if (entity === "amp") return "&";
      if (entity === "lt") return "<";
      if (entity === "gt") return ">";
      if (entity === "quot") return "\"";
      if (entity === "apos") return "'";
      if (entity.startsWith("#x")) {
        const code = parseInt(entity.slice(2), 16);
        return Number.isFinite(code) ? String.fromCodePoint(code) : match;
      }
      if (entity.startsWith("#")) {
        const code = parseInt(entity.slice(1), 10);
        return Number.isFinite(code) ? String.fromCodePoint(code) : match;
      }
      return match;
    });
  }

  function parseAttributes(raw) {
    const attrs = {};
    const pattern = /([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
    let match;
    while ((match = pattern.exec(raw || ""))) {
      const key = safeString(match[1]).toLowerCase();
      if (!key) continue;
      const value = match[2] !== undefined ? match[2] : (match[3] !== undefined ? match[3] : (match[4] !== undefined ? match[4] : ""));
      attrs[key] = decodeHtmlEntities(value);
    }
    return Object.freeze(attrs);
  }

  function parseMarkupElements(markup) {
    const text = safeString(markup);
    const elements = [];
    const stack = [];
    const tagPattern = /<\/?([A-Za-z][A-Za-z0-9:_-]*)([^<>]*?)\/?>/g;
    let match;
    while ((match = tagPattern.exec(text))) {
      const rawToken = match[0];
      const tagName = safeString(match[1]).toLowerCase();
      if (!tagName) continue;
      if (rawToken.startsWith("</")) {
        for (let i = stack.length - 1; i >= 0; i -= 1) {
          const candidate = elements[stack[i]];
          stack.pop();
          if (candidate && candidate.tagName === tagName) break;
        }
        continue;
      }
      const attrs = parseAttributes(match[2] || "");
      const parentIndex = stack.length ? stack[stack.length - 1] : -1;
      const element = Object.freeze({
        index: elements.length,
        tagName,
        attrs,
        parentIndex,
        start: match.index,
        end: match.index + rawToken.length
      });
      elements.push(element);
      const selfClosing = rawToken.endsWith("/>") || voidElements.has(tagName);
      if (!selfClosing) stack.push(element.index);
    }
    return Object.freeze(elements);
  }

  function collectDomElements(root) {
    if (!root || typeof root.querySelectorAll !== "function") return null;
    const domNodes = [root, ...Array.from(root.querySelectorAll("*"))].filter(Boolean);
    const indexByNode = new Map(domNodes.map((node, index) => [node, index]));
    return Object.freeze(domNodes.map((node, index) => {
      const attrs = {};
      Array.from(node.attributes || []).forEach((attr) => {
        attrs[attr.name.toLowerCase()] = node.getAttribute(attr.name) || "";
      });
      const parentIndex = node.parentElement && indexByNode.has(node.parentElement) ? indexByNode.get(node.parentElement) : -1;
      return Object.freeze({
        index,
        tagName: safeString(node.tagName).toLowerCase(),
        attrs: Object.freeze(attrs),
        parentIndex,
        start: -1,
        end: -1
      });
    }));
  }

  function elementsFromInput(input) {
    const domElements = collectDomElements(input);
    if (domElements) return domElements;
    return parseMarkupElements(input);
  }

  function hasRidgeAttributes(attrs) {
    return Object.keys(attrs || {}).some((name) => name.startsWith("data-mcel-") || name.startsWith("data-layout-"));
  }

  function classify(attrs) {
    if (ridgeApi && typeof ridgeApi.classifyAttributes === "function") return ridgeApi.classifyAttributes(attrs);
    if (attrs[attributes.surfaceId]) return "surface";
    if (attrs[attributes.nodeId]) return "node";
    if (attrs[attributes.edgeId]) return "edge";
    if (attrs[attributes.regionId]) return "region";
    if (attrs[attributes.controlId]) return "control";
    return "";
  }

  function nearestSurfaceId(elements, element) {
    let cursor = element;
    while (cursor) {
      if (cursor.attrs && cursor.attrs[attributes.surfaceId]) return cursor.attrs[attributes.surfaceId];
      cursor = cursor.parentIndex >= 0 ? elements[cursor.parentIndex] : null;
    }
    return "";
  }

  function surfaceRecords(elements) {
    return Object.freeze(elements.filter((element) => element.attrs && element.attrs[attributes.surfaceId]).map((element) => Object.freeze({
      element,
      id: safeString(element.attrs[attributes.surfaceId]),
      kind: safeString(element.attrs[attributes.surfaceKind]),
      role: safeString(element.attrs[attributes.surfaceRole]),
      contract: safeString(element.attrs[attributes.surfaceContract]),
      renderer: safeString(element.attrs[attributes.renderer]),
      projection: safeString(element.attrs[attributes.projection]),
      authoritative: safeBoolean(element.attrs["data-mcel-authoritative"] || element.attrs["data-mcel-authority"])
    })));
  }

  function selectSurface(elements, options, diagnostics) {
    const opts = options || {};
    const surfaces = surfaceRecords(elements);
    if (opts.surfaceId) {
      const selected = surfaces.find((surface) => surface.id === opts.surfaceId);
      if (!selected) {
        diagnostics.push(diagnostic("surface-id-not-found", "error", "Requested rendered MCEL surface was not found.", {surfaceId: opts.surfaceId}));
        return null;
      }
      return selected;
    }

    const authoritative = surfaces.filter((surface) => surface.authoritative);
    if (authoritative.length === 1) return authoritative[0];
    if (authoritative.length > 1) {
      diagnostics.push(diagnostic("multiple-authoritative-surfaces", "error", "Rendered output exposes more than one authoritative MCEL surface.", {surfaceIds: authoritative.map((surface) => surface.id)}));
      return authoritative[0];
    }

    if (surfaces.length === 1) return surfaces[0];
    if (surfaces.length > 1) {
      diagnostics.push(diagnostic("ambiguous-surface-selection", "error", "Rendered output exposes multiple MCEL surfaces and none is authoritative.", {surfaceIds: surfaces.map((surface) => surface.id)}));
      return surfaces[0];
    }

    diagnostics.push(diagnostic("surface-root-missing", "error", "Rendered output does not expose a data-mcel-surface-id root.", {}));
    return null;
  }

  function relevantElementsForSurface(elements, selectedSurface) {
    if (!selectedSurface) {
      return Object.freeze(elements.filter((element) => hasRidgeAttributes(element.attrs)));
    }
    const surfaceId = selectedSurface.id;
    return Object.freeze(elements.filter((element) => hasRidgeAttributes(element.attrs) && nearestSurfaceId(elements, element) === surfaceId));
  }

  function withInferredSurfaceDefaults(records, selectedSurface, options) {
    if (!records.length && !selectedSurface) return records;
    const opts = options || {};
    const selectedAttrs = selectedSurface && selectedSurface.element ? selectedSurface.element.attrs : {};
    return Object.freeze(records.map((attrs, index) => {
      if (attrs[attributes.surfaceId]) return attrs;
      if (index !== 0 || selectedSurface) return attrs;
      const surfaceAttrs = {};
      surfaceAttrs[attributes.surfaceId] = opts.defaultSurfaceId || "surface.extracted";
      surfaceAttrs[attributes.surfaceKind] = opts.defaultSurfaceKind || "semantic-surface";
      return Object.freeze(Object.assign(surfaceAttrs, attrs));
    }));
  }

  function extractRidgeRecordsFromMarkup(input, options) {
    const opts = Object.assign({projection: "", surfaceId: ""}, options || {});
    const diagnostics = [];
    const elements = elementsFromInput(input);
    const selectedSurface = selectSurface(elements, opts, diagnostics);
    const relevant = relevantElementsForSurface(elements, selectedSurface);
    const records = withInferredSurfaceDefaults(
      relevant.map((element) => element.attrs).filter((attrs) => {
        const kind = classify(attrs);
        return ["surface", "region", "node", "edge", "control"].includes(kind);
      }),
      selectedSurface,
      opts
    );

    return Object.freeze({
      contractVersion,
      projection: opts.projection || "",
      selectedSurface: selectedSurface ? Object.freeze({
        id: selectedSurface.id,
        kind: selectedSurface.kind,
        role: selectedSurface.role,
        authoritative: selectedSurface.authoritative
      }) : null,
      recordCount: records.length,
      records: Object.freeze(records),
      diagnostics: Object.freeze(diagnostics)
    });
  }

  function inferViewport(selectedSurface, records, explicitViewport) {
    if (explicitViewport) return explicitViewport;
    const surfaceRecord = (records || []).find((record) => record && record[attributes.surfaceId]) || {};
    const attrs = selectedSurface && selectedSurface.element ? selectedSurface.element.attrs : surfaceRecord;
    return {
      width: finiteNumberOrNull(attrs["data-layout-viewport-width"] || attrs.width),
      height: finiteNumberOrNull(attrs["data-layout-viewport-height"] || attrs.height),
      safeMargin: finiteNumberOrNull(attrs["data-layout-safe-margin"]) || 0
    };
  }

  function inferRegionBounds(records) {
    const regions = [];
    (records || []).forEach((attrs) => {
      const id = safeString(attrs[attributes.regionId]);
      if (!id) return;
      regions.push(Object.freeze({
        id,
        role: safeString(attrs[attributes.regionRole]),
        x: finiteNumberOrNull(attrs["data-layout-x"] || attrs.x),
        y: finiteNumberOrNull(attrs["data-layout-y"] || attrs.y),
        width: finiteNumberOrNull(attrs["data-layout-region-width"] || attrs[attributes.layoutWidth] || attrs.width),
        height: finiteNumberOrNull(attrs["data-layout-region-height"] || attrs[attributes.layoutHeight] || attrs.height)
      }));
    });
    return Object.freeze(regions);
  }

  function inferNodePorts(records) {
    const ports = {};
    (records || []).forEach((attrs) => {
      const id = safeString(attrs[attributes.nodeId]);
      if (!id) return;
      const declared = splitCsv(attrs["data-layout-ports"]);
      const inPort = safeString(attrs["data-layout-port-in"]);
      const outPort = safeString(attrs["data-layout-port-out"]);
      const all = [...declared, inPort, outPort].filter(Boolean);
      if (all.length) ports[id] = Object.freeze([...new Set(all)].sort());
    });
    return Object.freeze(ports);
  }

  function buildSurfaceBundleFromMarkup(input, options) {
    const opts = options || {};
    if (!irApi || typeof irApi.buildSurfaceIRFromRidges !== "function") {
      throw new Error("McelSemanticSurfaceIR is required before extracting MCEL surfaces.");
    }
    if (!layoutApi || typeof layoutApi.buildSharedLayoutGrammar !== "function") {
      throw new Error("McelSharedLayoutGrammar is required before extracting MCEL layout grammar.");
    }

    const extraction = extractRidgeRecordsFromMarkup(input, opts);
    const irResult = irApi.buildSurfaceIRFromRidges(extraction.records, {
      requireSurface: true,
      defaultSurfaceId: opts.defaultSurfaceId,
      defaultSurfaceKind: opts.defaultSurfaceKind || "semantic-surface"
    });
    const surfaceIR = irResult.ir;

    const layoutOptions = Object.assign({}, opts.layout || {});
    layoutOptions.viewport = inferViewport(extraction.selectedSurface, extraction.records, layoutOptions.viewport || opts.viewport);
    layoutOptions.regions = inferRegionBounds(extraction.records).concat(layoutOptions.regions || []);
    layoutOptions.nodePorts = Object.assign({}, inferNodePorts(extraction.records), layoutOptions.nodePorts || {});

    const layoutResult = layoutApi.buildSharedLayoutGrammar(surfaceIR, layoutOptions);
    const validation = {
      surface: irApi.validateSurfaceIR(surfaceIR),
      layout: layoutApi.validateSharedLayoutGrammar(surfaceIR, layoutResult.grammar)
    };
    const diagnostics = [
      ...extraction.diagnostics,
      ...irResult.diagnostics,
      ...layoutResult.diagnostics
    ];

    return Object.freeze({
      contractVersion,
      surfaceIrContractVersion,
      layoutContractVersion,
      extraction,
      surfaceIR,
      layoutGrammar: layoutResult.grammar,
      validation: Object.freeze(validation),
      diagnostics: Object.freeze(diagnostics),
      valid: diagnostics.filter((item) => item.severity === "error").length === 0 && validation.surface.valid && validation.layout.valid
    });
  }

  function extractSemanticSurfaceFromHtml(htmlText, options) {
    return buildSurfaceBundleFromMarkup(htmlText, Object.assign({projection: "html"}, options || {})).surfaceIR;
  }

  function extractSemanticSurfaceFromSvg(svgText, options) {
    return buildSurfaceBundleFromMarkup(svgText, Object.assign({projection: "svg"}, options || {})).surfaceIR;
  }

  function extractLayoutGrammarFromHtml(htmlText, options) {
    return buildSurfaceBundleFromMarkup(htmlText, Object.assign({projection: "html"}, options || {})).layoutGrammar;
  }

  function extractLayoutGrammarFromSvg(svgText, options) {
    return buildSurfaceBundleFromMarkup(svgText, Object.assign({projection: "svg"}, options || {})).layoutGrammar;
  }

  function extractSurfaceBundleFromHtml(htmlText, options) {
    return buildSurfaceBundleFromMarkup(htmlText, Object.assign({projection: "html"}, options || {}));
  }

  function extractSurfaceBundleFromSvg(svgText, options) {
    return buildSurfaceBundleFromMarkup(svgText, Object.assign({projection: "svg"}, options || {}));
  }

  function canonicalExtractedSurfaceFingerprint(input, options) {
    const bundle = buildSurfaceBundleFromMarkup(input, options || {});
    const semantic = irApi && typeof irApi.semanticFingerprint === "function"
      ? irApi.semanticFingerprint(bundle.surfaceIR)
      : JSON.stringify(bundle.surfaceIR);
    const layout = layoutApi && typeof layoutApi.layoutFingerprint === "function"
      ? layoutApi.layoutFingerprint(bundle.layoutGrammar)
      : JSON.stringify(bundle.layoutGrammar);
    return JSON.stringify({semantic, layout});
  }

  return Object.freeze({
    contractVersion,
    surfaceIrContractVersion,
    layoutContractVersion,
    extractRidgeRecordsFromMarkup,
    extractSemanticSurfaceFromHtml,
    extractSemanticSurfaceFromSvg,
    extractLayoutGrammarFromHtml,
    extractLayoutGrammarFromSvg,
    extractSurfaceBundleFromHtml,
    extractSurfaceBundleFromSvg,
    buildSurfaceBundleFromMarkup,
    canonicalExtractedSurfaceFingerprint
  });
})();

if (typeof window !== "undefined") {
  window.McelSurfaceExtractors = McelSurfaceExtractors;
}
