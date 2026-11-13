/* MCEL semantic-presentation compatibility observer v1.
 * No DOM writes, style writes, layout operations, effect execution or label inference.
 * Reports exact source-to-projection joins only; an absent join is unknown, not a guess.
 */
var McelSemanticPresentationBridge = (() => {
  "use strict";
  const schema = "mcel.semantic-presentation-observation.v1";
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
  const str = (value) => typeof value === "string" ? value : "";
  const reference = (value) => str(value?.ref);
  const records = (value) => Array.isArray(value) ? value : [];
  const identity = (value) => str(value?.id);
  const attributes = ["data-mcel-node", "data-mcel-node-id", "data-mcel-control", "data-mcel-region", "data-intent", "data-mcel-state-projection", "data-mc-component-id"];
  const legacyAttributes = ["data-mc", "data-mc-type", "data-mc-kind"];

  function semanticIndex(ir) {
    if (ir?.schema === "mcel.application-ir.v1") {
      const surface = records(ir.surfaces)[0];
      const entries = [
        ...records(surface?.nodes).map(item => [item.id, "node", item]),
        ...records(surface?.interfaceIntent?.regions).map(item => [item.id, "region", item]),
        ...records(surface?.semanticSurface?.applicationShell?.regions).map(item => [item.id, "region", item]),
        ...records(ir.intents).map(item => [item.id, "intent", item]),
        ...records(ir.layouts).map(item => [item.id, "layout", item]),
        ...records(ir.states).map(item => [item.id, "state", item]),
        ...records(ir.capabilities).map(item => [item.id, "capability", item]),
        ...records(ir.effects).map(item => [item.id, "effect", item])
      ];
      const index = new Map();
      for (const [id, kind, item] of entries) {
        if (!str(id)) continue;
        if (!index.has(id)) index.set(id, []);
        index.get(id).push({kind, item});
      }
      return {status: "canonical-ir", appId: str(ir.application?.appId), schema: ir.schema, index};
    }
    if (ir?.contractVersion === "mcel.semantic-surface-ir.v1") {
      const index = new Map();
      for (const kind of ["nodes", "regions", "controls"]) {
        for (const item of records(ir.graph?.[kind])) {
          if (!identity(item)) continue;
          if (!index.has(item.id)) index.set(item.id, []);
          index.get(item.id).push({kind: kind.slice(0, -1), item});
        }
      }
      return {status: "canonical-surface-ir", appId: str(ir.surface?.id), schema: ir.contractVersion, index};
    }
    return {status: ir == null ? "missing-ir" : "unsupported-ir", appId: "", schema: str(ir?.schema || ir?.contractVersion), index: new Map()};
  }

  function observe(root) {
    if (!root || typeof root.querySelectorAll !== "function") return [];
    const query = [...attributes, ...legacyAttributes].map(attr => `[${attr}]`).join(",");
    const nodes = [...(typeof root.matches === "function" && root.matches(query) ? [root] : []), ...root.querySelectorAll(query)];
    return nodes.map((element, offset) => {
      const declared = {};
      for (const attr of attributes) {
        const value = element.getAttribute(attr);
        if (value !== null) declared[attr] = value;
      }
      const legacy = legacyAttributes.some(attr => element.hasAttribute(attr));
      return {position: offset, tag: str(element.tagName).toLowerCase(), attrs: declared,
        legacy, disabled: "disabled" in element ? Boolean(element.disabled) : null};
    });
  }

  function exactBindings(observation, index) {
    // Each observed identifier must match exactly one canonical record of the right kind.
    const accepted = [
      ["data-mcel-node", "node"], ["data-mcel-node-id", "node"],
      ["data-mcel-control", "control"], ["data-mcel-region", "region"],
      ["data-intent", "intent"], ["data-mcel-state-projection", "state"]
    ];
    const candidates = [];
    for (const [attribute, kind] of accepted) {
      const id = observation.attrs[attribute];
      if (!id) continue;
      const found = records(index.get(id)).filter(record => record.kind === kind);
      if (found.length) candidates.push({id, attribute, matches: found});
    }
    // Component IDs are evidence only when the canonical graph contains the exact ID.
    if (observation.attrs["data-mc-component-id"]) {
      const id = observation.attrs["data-mc-component-id"];
      const found = records(index.get(id));
      if (found.length) candidates.push({id, attribute: "data-mc-component-id", matches: found});
    }
    const matchedNodes = candidates.filter(item => item.matches[0]?.kind === "node");
    const matchedIntents = candidates.filter(item => item.matches[0]?.kind === "intent");
    const invalidNodeIntent = matchedNodes.length && matchedIntents.length &&
      matchedNodes.some(node => matchedIntents.some(intent =>
        reference(node.matches[0].item.intent) !== intent.id));
    const ambiguous = candidates.some(item => item.matches.length !== 1) || invalidNodeIntent ||
      candidates.some((item, i) => candidates.slice(i + 1).some(other =>
        other.id === item.id && other.matches[0]?.kind !== item.matches[0]?.kind));
    return {status: ambiguous ? "ambiguous" : candidates.length ? "exact" : "unmapped", candidates};
  }

  function observeCompatibility({ir = null, root = null, themeLaw = null, chromeLaw = null, theme = "", chrome = ""} = {}) {
    const semantic = semanticIndex(ir);
    const projected = observe(root);
    const observations = projected.map(item => {
      const binding = exactBindings(item, semantic.index);
      const refs = binding.status === "exact" ? binding.candidates.map(candidate => {
        const record = candidate.matches[0];
        const declared = record.item;
        const intentId = record.kind === "node" ? reference(declared.intent) : "";
        const intentEntries = records(semantic.index.get(intentId)).filter(entry => entry.kind === "intent");
        const intent = intentEntries.length === 1 ? intentEntries[0].item : null;
        return {
          id: candidate.id, kind: record.kind, bindingAttribute: candidate.attribute,
          nodeKind: str(declared.nodeKind || declared.type || declared.kind),
          region: str(declared.homeRegion || declared.actualRegion || reference(declared.region)),
          intentId: record.kind === "intent" ? candidate.id : intentId,
          operationKind: str((record.kind === "intent" ? declared : intent)?.operationKind),
          risk: str((record.kind === "intent" ? declared : intent)?.risk),
          enabledWhenDeclared: own(declared, "enabledWhen"),
          stateReads: records((record.kind === "intent" ? declared : intent)?.reads).map(reference).filter(Boolean),
          stateWrites: records((record.kind === "intent" ? declared : intent)?.viewWrites).map(reference).filter(Boolean),
          actionMeaning: null, // No canonical Start/Stop/Enable/etc classification in these fields.
          source: declared.source ? "canonical-ir-source-binding" : "canonical-ir-record"
        };
      }) : [];
      return {
        position: item.position, tag: item.tag, attributes: item.attrs,
        runtimeDisabled: item.disabled, legacyStyleAttributes: item.legacy,
        bindingStatus: binding.status, bindings: refs
      };
    });
    const selectedTheme = themeLaw?.normalizeTheme?.(theme) || str(theme);
    const selectedChrome = chromeLaw?.normalizeChrome?.(chrome) || str(chrome);
    const hasTheme = Array.isArray(themeLaw?.themes) && themeLaw.themes.includes(selectedTheme);
    const hasChrome = Array.isArray(chromeLaw?.chromes) && chromeLaw.chromes.includes(selectedChrome);
    const mapped = observations.filter(entry => entry.bindingStatus === "exact").length;
    const ambiguous = observations.filter(entry => entry.bindingStatus === "ambiguous").length;
    const legacy = observations.filter(entry => entry.legacyStyleAttributes).length;
    const boundIds = new Set(observations.flatMap(entry => entry.bindings.map(binding => binding.id)));
    const diagnostics = [];
    if (semantic.status === "missing-ir") diagnostics.push("Canonical semantic IR is not supplied; visible attributes alone cannot establish operation meaning.");
    if (semantic.status === "unsupported-ir") diagnostics.push("Source is not a recognized canonical MCEL IR schema.");
    if (!root) diagnostics.push("No mounted DOM was supplied; rendered-element binding cannot be verified.");
    if (ambiguous) diagnostics.push(`${ambiguous} rendered element(s) have ambiguous canonical bindings.`);
    if (legacy) diagnostics.push(`${legacy} element(s) expose legacy Style Law attributes; this does not prove modern semantic compatibility.`);
    if (semantic.index.size && !mapped) diagnostics.push("No rendered elements are proven bound to canonical records.");
    return {
      schema,
      source: {status: semantic.status, schema: semantic.schema, appId: semantic.appId, declaredRecords: semantic.index.size},
      presentation: {
        theme: {id: selectedTheme, status: hasTheme ? "catalog-available" : "catalog-unavailable", semanticMapping: "unresolved"},
        chrome: {id: selectedChrome, status: hasChrome ? "catalog-available" : "catalog-unavailable", semanticMapping: "unresolved"}
      },
      coverage: {observedElements: observations.length, exactlyBound: mapped, ambiguous,
        unbound: observations.length - mapped - ambiguous, legacyStyleElements: legacy,
        declaredRecordsUnobserved: [...semantic.index.keys()].filter(id => !boundIds.has(id)).length},
      elements: observations,
      declaredPreview: [...semantic.index.entries()].slice(0, 24).flatMap(([id, entries]) =>
        entries.map(entry => ({id, kind: entry.kind,
          operationKind: str(entry.item.operationKind), risk: str(entry.item.risk),
          nodeKind: str(entry.item.nodeKind || entry.item.kind),
          intentId: reference(entry.item.intent),
          actionMeaning: null}))),
      diagnostics
    };
  }


  // Read-only joins of declared semanticSurface selectors to a mounted source
  // root. Selectors and intent source names come from the canonical IR; no
  // CSS class/label/DOM-order interpretation creates an operation identity.
  function observePresentationTargets({ir = null, root = null} = {}) {
    const rejected = reason => ({status: "unresolved", reason, targets: [], diagnostics: [reason]});
    if (ir?.schema !== "mcel.application-ir.v1") return rejected("Canonical application IR required");
    const surfaces = records(ir.surfaces);
    if (surfaces.length !== 1 || !str(surfaces[0].root)) return rejected("Unique canonical surface required");
    const surface = surfaces[0];
    if (!root || typeof root.matches !== "function" || !root.matches(surface.root))
      return rejected("Mounted root does not match declared canonical surface root");
    const semantics = surface.semanticSurface;
    if (!semantics || !Array.isArray(semantics.controls) || !Array.isArray(semantics.regions))
      return rejected("Canonical semanticSurface controls and regions required");
    const targets = [];
    const diagnostics = [];
    const intents = records(ir.intents);
    for (const control of semantics.controls) {
      const id = str(control?.id), selector = str(control?.selector), sourceName = str(control?.intent);
      if (!id || !selector || !sourceName) {diagnostics.push("Incomplete canonical control binding");continue;}
      const matches = intents.filter(intent => intent.sourceName === sourceName);
      if (matches.length !== 1) {diagnostics.push(`Control ${id}: intent source binding not unique`);continue;}
      const intent = matches[0];
      const nodes = records(surface.nodes).filter(node => reference(node.intent) === intent.id);
      if (nodes.length !== 1 || nodes[0].nodeKind !== "control") {
        diagnostics.push(`Control ${id}: canonical node-to-intent binding not unique`);continue;
      }
      let elements;
      try {elements = [...(root.matches(selector) ? [root] : []), ...root.querySelectorAll(selector)];}
      catch (_) {diagnostics.push(`Control ${id}: invalid canonical selector`);continue;}
      if (!elements.length) {diagnostics.push(`Control ${id}: no mounted match`);continue;}
      for (const element of elements) targets.push({element, kind: "control", id,
        intentId: intent.id, nodeId: nodes[0].id, operationKind: str(intent.operationKind),
        risk: str(intent.risk), runtimeDisabled: Boolean(element.disabled || element.getAttribute?.("aria-disabled") === "true")});
    }
    for (const region of semantics.regions) {
      const id = str(region?.id), selector = str(region?.selector);
      if (!id || !selector) {diagnostics.push("Incomplete canonical region binding");continue;}
      let elements;
      try {elements = [...(root.matches(selector) ? [root] : []), ...root.querySelectorAll(selector)];}
      catch (_) {diagnostics.push(`Region ${id}: invalid canonical selector`);continue;}
      if (elements.length !== 1) {diagnostics.push(`Region ${id}: expected one mounted match; found ${elements.length}`);continue;}
      targets.push({element: elements[0], kind: "region", id, role: str(region.role)});
    }
    return {status: targets.length ? "source-bound" : "unresolved", reason: "", targets, diagnostics};
  }

  // Modern MCEL operational runtimes expose their canonical node and region
  // identities directly in the mounted DOM. The host has no legacy selector-
  // based semanticSurface and MUST NOT be matched by labels or CSS classes.
  function observeRuntimePresentationTargets({ir = null, root = null} = {}) {
    const rejected = (reason, diagnostics = [reason]) => ({status: "unresolved", reason,
      targets: [], diagnostics, expectedControls: 0, expectedRegions: 0});
    if (ir?.schema !== "mcel.application-ir.v1") return rejected("Canonical application IR required");
    const surfaces = records(ir.surfaces);
    if (surfaces.length !== 1 || !Array.isArray(surfaces[0].nodes) ||
        !Array.isArray(surfaces[0].interfaceIntent?.regions))
      return rejected("Unique operational semantic surface and regions required");
    if (!root?.querySelectorAll || !root.isConnected)
      return rejected("Mounted operational runtime root required");
    const surface = surfaces[0];
    const nodes = surface.nodes.filter(node => ["control", "form"].includes(node?.nodeKind));
    const regions = surface.interfaceIntent.regions;
    if (!nodes.length || !regions.length)
      return rejected("Canonical operational controls/forms and regions required");
    const byAttribute = (attribute) => {
      const index = new Map();
      for (const element of root.querySelectorAll(`[${attribute}]`)) {
        const id = element.getAttribute(attribute);
        if (!index.has(id)) index.set(id, []);
        index.get(id).push(element);
      }
      return index;
    };
    const renderedNodes = byAttribute("data-mcel-node");
    const renderedRegions = byAttribute("data-mcel-region");
    const intents = new Map(records(ir.intents).map(intent => [intent.id, intent]));
    const targets = [], diagnostics = [];
    for (const region of regions) {
      const hits = renderedRegions.get(region?.id) || [];
      if (hits.length !== 1) {
        diagnostics.push(`Region ${str(region?.id)}: expected one exact runtime binding; found ${hits.length}`);
        continue;
      }
      targets.push({kind: "region", id: region.id, role: str(region.purpose), element: hits[0]});
    }
    for (const node of nodes) {
      const hits = renderedNodes.get(node?.id) || [];
      if (hits.length !== 1 || hits[0].getAttribute("data-node-kind") !== node.nodeKind) {
        diagnostics.push(`Node ${str(node?.id)}: exact runtime binding missing, duplicated, or wrong kind`);
        continue;
      }
      const intentId = reference(node.intent), intent = intents.get(intentId);
      if (!intentId || !intent) {
        diagnostics.push(`Node ${str(node?.id)}: canonical intent binding missing`);
        continue;
      }
      targets.push({kind: "control", nodeKind: node.nodeKind, id: node.id,
        nodeId: node.id, intentId, operationKind: str(intent.operationKind),
        risk: str(intent.risk), element: hits[0],
        runtimeDisabled: Boolean(hits[0].querySelector(":disabled,[aria-disabled=true]"))});
    }
    return {status: diagnostics.length ? "unresolved" : "source-bound",
      reason: diagnostics[0] || "", targets, diagnostics,
      expectedControls: nodes.length, expectedRegions: regions.length};
  }

  return Object.freeze({schema, observeCompatibility, observePresentationTargets, observeRuntimePresentationTargets});
})();
if (typeof window !== "undefined") window.McelSemanticPresentationBridge = McelSemanticPresentationBridge;
if (typeof module !== "undefined" && module.exports) module.exports = McelSemanticPresentationBridge;
