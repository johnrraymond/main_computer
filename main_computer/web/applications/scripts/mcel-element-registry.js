    (function (global) {
      "use strict";

      const REGISTRY_VERSION = "0.1.0";
      const registry = new Map();

      function clone(value) {
        return JSON.parse(JSON.stringify(value || null));
      }

      function normalizeList(value) {
        if (!value) return [];
        return Array.isArray(value) ? value.filter(Boolean) : [value].filter(Boolean);
      }

      function freezeDefinition(definition) {
        const id = String(definition?.id || "").trim();
        if (!id) throw new Error("MCEL element definition requires an id");
        const kind = String(definition.kind || "element").trim();
        const contract = Object.freeze({
          id,
          label: definition.label || id,
          kind,
          family: definition.family || "core",
          purpose: definition.purpose || "",
          allowedChildren: normalizeList(definition.allowedChildren),
          layoutLaws: normalizeList(definition.layoutLaws),
          fitPolicy: definition.fitPolicy || "content-fit",
          scrollPolicy: definition.scrollPolicy || "no-owned-scroll",
          actionPolicy: clone(definition.actionPolicy || {}),
          riskPolicy: clone(definition.riskPolicy || {risk: "none", proofPolicy: "inspect-only"}),
          proofPolicy: definition.proofPolicy || definition.riskPolicy?.proofPolicy || "inspect-only",
          serializationSchema: clone(definition.serializationSchema || {}),
          renderer: clone(definition.renderer || {}),
          decoderHints: normalizeList(definition.decoderHints),
          supersedes: normalizeList(definition.supersedes),
          stateModel: clone(definition.stateModel || {}),
          interactionModel: clone(definition.interactionModel || {}),
          accessibility: clone(definition.accessibility || {}),
          dataModel: clone(definition.dataModel || {}),
          migrationHints: clone(definition.migrationHints || {}),
          proofFixtures: normalizeList(definition.proofFixtures),
          presentationModes: normalizeList(definition.presentationModes),
          viewPatterns: normalizeList(definition.viewPatterns),
          densityModes: normalizeList(definition.densityModes),
          examples: normalizeList(definition.examples),
          version: definition.version || REGISTRY_VERSION
        });
        return contract;
      }

      function register(definition) {
        const contract = freezeDefinition(definition);
        if (registry.has(contract.id)) {
          throw new Error(`Duplicate MCEL element id: ${contract.id}`);
        }
        registry.set(contract.id, contract);
        return contract;
      }

      function registerMany(definitions) {
        normalizeList(definitions).forEach(register);
        return all();
      }

      function get(id) {
        return registry.get(id) || null;
      }

      function all() {
        return Array.from(registry.values()).map(clone);
      }

      function byFamily(family) {
        return all().filter((definition) => definition.family === family);
      }

      function byKind(kind) {
        return all().filter((definition) => definition.kind === kind);
      }

      function serializeElement(id, props = {}) {
        const definition = get(id);
        if (!definition) {
          return {
            id: props.id || id || "element.unknown",
            elementId: "element.unknown",
            kind: "unknown",
            label: props.label || "Unknown MCEL element",
            props,
            children: []
          };
        }
        return {
          id: props.id || `${definition.id.replace(/[^a-z0-9]+/gi, "-")}-${Math.random().toString(36).slice(2, 8)}`,
          elementId: definition.id,
          kind: definition.kind,
          family: definition.family,
          label: props.label || definition.label,
          purpose: props.purpose || definition.purpose,
          layoutLaws: definition.layoutLaws,
          fitPolicy: definition.fitPolicy,
          scrollPolicy: definition.scrollPolicy,
          actionPolicy: definition.actionPolicy,
          riskPolicy: props.riskPolicy || definition.riskPolicy,
          proofPolicy: props.proofPolicy || definition.proofPolicy,
          stateModel: definition.stateModel,
          interactionModel: definition.interactionModel,
          accessibility: definition.accessibility,
          dataModel: definition.dataModel,
          migrationHints: definition.migrationHints,
          props: clone(props),
          children: normalizeList(props.children)
        };
      }

      function evidencePacket() {
        const definitions = all();
        const families = {};
        definitions.forEach((definition) => {
          families[definition.family] = (families[definition.family] || 0) + 1;
        });
        return {
          version: REGISTRY_VERSION,
          elementCount: definitions.length,
          families,
          proofPolicies: Array.from(new Set(definitions.map((definition) => definition.proofPolicy))).sort(),
          riskFamilies: Array.from(new Set(definitions.map((definition) => definition.riskPolicy?.risk || "none"))).sort(),
          supersedes: Array.from(new Set(definitions.flatMap((definition) => definition.supersedes || []))).sort(),
          statefulElementCount: definitions.filter((definition) => definition.stateModel && Object.keys(definition.stateModel).length).length,
          interactiveElementCount: definitions.filter((definition) => definition.interactionModel && Object.keys(definition.interactionModel).length).length,
          generatedAt: new Date().toISOString()
        };
      }

      global.McelElementRegistry = {
        REGISTRY_VERSION,
        register,
        registerMany,
        get,
        all,
        byFamily,
        byKind,
        serializeElement,
        evidencePacket
      };
    })(window);
