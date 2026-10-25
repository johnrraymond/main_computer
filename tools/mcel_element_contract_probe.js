/* MCEL Functional Acid: independent per-definition PUBLIC API probes.
 * Loaded only in the disposable Chromium test page. This does NOT render a
 * widget or certify behaviors described in interactionModel or proofFixtures.
 */
(function (global) {
  "use strict";

  function run() {
    const registry = global.McelElementRegistry;
    if (!registry || typeof registry.all !== "function") {
      throw new Error("MCEL public element registry unavailable");
    }
    const source = registry.all();
    const baselineIds = source.map(d => d.id);
    const results = [];
    for (const definition of source) {
      const id = definition.id;
      const probes = [];
      function check(name, predicate) {
        let status = "FAIL", detail = "";
        try {
          status = predicate() ? "PASS" : "FAIL";
          if (status === "FAIL") detail = "Assertion returned false";
        } catch (error) {
          detail = String(error && error.message || error);
        }
        probes.push({id: name, status, detail});
      }
      // Every operation below uses the actual registered library, not a mock.
      check("lookup-exact", () => {
        const found = registry.get(id);
        return found && found.id === id && found.kind === definition.kind &&
          found.family === definition.family && registry.get(id + "-nonexistent") === null;
      });
      check("family-filter", () => registry.byFamily(definition.family)
        .some(candidate => candidate.id === id) &&
        !registry.byFamily("__acid_unknown_family__").some(candidate => candidate.id === id));
      check("kind-filter", () => registry.byKind(definition.kind)
        .some(candidate => candidate.id === id));
      check("source-metadata-serializes", () => {
        const encoded = registry.serializeElement(id, {id: "acid-witness", label: "Witness"});
        return encoded.id === "acid-witness" && encoded.elementId === id &&
          encoded.kind === definition.kind && encoded.family === definition.family &&
          encoded.label === "Witness" && encoded.purpose === definition.purpose &&
          Array.isArray(encoded.layoutLaws) && encoded.fitPolicy === definition.fitPolicy &&
          encoded.proofPolicy === definition.proofPolicy &&
          JSON.stringify(encoded.actionPolicy) === JSON.stringify(definition.actionPolicy) &&
          JSON.stringify(encoded.stateModel) === JSON.stringify(definition.stateModel);
      });
      check("owned-props-and-children", () => {
        const child = (definition.allowedChildren || []).length
          ? {id: "acid-child", elementId: definition.allowedChildren[0]}
          : {id: "acid-child", type: "test"};
        const sourceProps = {id: "acid-instance", metadata: {source: "original"},
          children: [child]};
        const encoded = registry.serializeElement(id, sourceProps);
        encoded.props.metadata.source = "mutated";
        return sourceProps.metadata.source === "original" &&
          encoded.props.metadata.source === "mutated" &&
          encoded.children.length === 1 && encoded.children[0].id === "acid-child";
      });
      check("catalog-read-isolation", () => {
        // `all()` promises a detached serialization, so altering its result
        // cannot alter the live registered definition.
        const detached = registry.all().find(d => d.id === id);
        if (!detached) return false;
        detached.label = "poisoned";
        detached.riskPolicy.risk = "poisoned";
        const fresh = registry.get(id);
        return fresh.label === definition.label &&
          fresh.riskPolicy.risk === definition.riskPolicy.risk;
      });
      check("duplicate-registration-refusal", () => {
        const before = registry.all().length;
        let refused = false;
        try { registry.register({id, label: "malicious duplicate"}); }
        catch (error) { refused = /Duplicate MCEL element id/.test(String(error)); }
        return refused && registry.all().length === before && registry.get(id).label === definition.label;
      });
      check("unknown-id-refusal", () => {
        const unknownId = id + ".__acid_absent__";
        const encoded = registry.serializeElement(unknownId, {id: "unknown-instance"});
        return encoded.id === "unknown-instance" && encoded.elementId === "element.unknown" &&
          encoded.kind === "unknown" && registry.get(unknownId) === null;
      });
      const failed = probes.filter(probe => probe.status !== "PASS");
      // A semantic API success is NOT a functioning renderer/controller.
      const expectedWitnesses = ["render-or-model-consumption", "behavior-negative-controls"];
      if (Object.keys(definition.interactionModel || {}).length > 0) {
        expectedWitnesses.push("advertised-interaction-model");
      }
      if ((definition.allowedChildren || []).length > 0) {
        expectedWitnesses.push("composition-valid-and-invalid-children");
      }
      if ((definition.proofFixtures || []).length > 0) {
        expectedWitnesses.push("declared-proof-fixtures");
      }
      if ((definition.riskPolicy || {}).blocked || definition.proofPolicy !== "inspect-only") {
        expectedWitnesses.push("independent-forbidden-effect-refusal");
      }
      results.push({id, family: definition.family,
        status: failed.length ? "FAIL" : "PASS", probes,
        behavioralStatus: "UNVERIFIED", missingBehavioralWitnesses: expectedWitnesses,
        observedInterface: "McelElementRegistry (public API)",
        scope: "definition retrieval, encoding, isolation, and rejection; no renderer/controller proof"});
    }
    return {schema: "mcel.acid.element-contracts.v1", registryCount: source.length,
      probeCount: results.reduce((sum, item) => sum + item.probes.length, 0),
      sourceRosterStable: JSON.stringify(registry.all().map(d => d.id)) === JSON.stringify(baselineIds),
      elements: results};
  }
  global.McelAcidElementContracts = Object.freeze({run});
})(window);
