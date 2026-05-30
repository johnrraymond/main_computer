var McelLabComponentLaw = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const registry = typeof McelLabLawRegistry !== "undefined" ? McelLabLawRegistry : window.McelLabLawRegistry;
      const {attributes, contractVersion} = contract;

      const domain = Object.freeze({
        "id": "component.slots.props.v1",
        "label": "Component / Slot / Prop Law",
        "reportKind": "mcel-component-law-report",
        "reads": [
                "componentName",
                "componentKind",
                "slot",
                "propContract"
        ],
        "required": [],
        "runtimeAttributes": [
                "componentLaw",
                "slotLaw"
        ],
        "alwaysOn": true,
        "dependencyMode": "semantic-component",
        "replaces": [
                "React components",
                "Vue single-file components",
                "Svelte components",
                "Lit/Web Components"
        ],
        "thesis": "Components become semantic source contracts with provable slots, props, generated runtime ownership, and clean serialization.",
        "migration": [
                "wrap existing components as adapters",
                "promote slots and props into data-mc source traits",
                "prove generated structure before deleting legacy framework code"
        ],
        "proofObligations": [
                "component identity is source-owned",
                "slot names remain stable",
                "generated decorations remain runtime-owned",
                "serialized source does not retain runtime component facts"
        ]
});

      function now() {
        return new Date().toISOString();
      }

      function sourceElements(root) {
        return [...(root?.querySelectorAll?.(`[${attributes.type}]`) || [])];
      }

      function attr(element, name, fallback = "") {
        return String(element?.getAttribute?.(name) || fallback).trim();
      }

      function boolText(value) {
        return value ? "true" : "false";
      }

      function riskFor(signals) {
        if (signals.violations.length) return "high";
        if (signals.warnings.length) return "medium";
        if (signals.active) return "low";
        return "latent";
      }

      function computeElementLaw(element, index = 0, total = 1) {
        const signals = domain.reads.map((name) => {
          const attribute = attributes[name];
          return {
            name,
            attribute,
            value: attr(element, attribute)
          };
        });
        const activeSignals = signals.filter((signal) => signal.value);
        const missingRequired = domain.required.filter((name) => !attr(element, attributes[name]));
        const warnings = [];
        const violations = [];

        const slots = [...(element?.querySelectorAll?.(`[${attributes.slot}]`) || [])].map((node) => attr(node, attributes.slot));
        if (slots.length) signals.push({name: "slots", attribute: attributes.slot, value: slots.join(",")});
        if (activeSignals.length && !attr(element, attributes.componentName)) warnings.push("component law is active without a stable data-mc-component name");
        if (attr(element, attributes.componentKind) === "adapter" && !attr(element, attributes.propContract)) warnings.push("adapter components should publish a prop contract");

        missingRequired.forEach((name) => warnings.push(`${name} is recommended for ${domain.id}`));
        return {
          kind: "mcel-platform-law-element",
          domain: domain.id,
          label: domain.label,
          index,
          total,
          sourceIndex: attr(element, attributes.sourceIndex, String(index)),
          type: attr(element, attributes.type, "panel"),
          active: activeSignals.length > 0 || domain.alwaysOn,
          signals,
          activeSignals,
          missingRequired,
          warnings,
          violations,
          replaces: domain.replaces,
          runtimeAttributes: domain.runtimeAttributes,
          risk: riskFor({active: activeSignals.length > 0 || domain.alwaysOn, warnings, violations})
        };
      }

      function applyElementLaw(element, law, options = {}) {
        domain.runtimeAttributes.forEach((name) => {
          const attribute = attributes[name];
          if (attribute) element.setAttribute(attribute, law.active ? "true" : "latent");
        });
        if (attributes.proofTier) element.setAttribute(attributes.proofTier, "platform-spine");
        if (attributes.semanticRisk) element.setAttribute(attributes.semanticRisk, law.risk);
        if (attributes.dependencyMode) element.setAttribute(attributes.dependencyMode, domain.dependencyMode);

        if (attributes.slotLaw) element.setAttribute(attributes.slotLaw, law.signals.some((signal) => signal.name === "slots") ? "mapped" : "implicit");

        return law;
      }

      function applyRuntimeLaw(root, options = {}) {
        const elements = sourceElements(root);
        const reports = elements.map((element, index) => {
          const law = computeElementLaw(element, index, elements.length);
          applyElementLaw(element, law, options);
          return law;
        });
        return {
          kind: domain.reportKind,
          id: domain.id,
          label: domain.label,
          contractVersion,
          generatedAt: now(),
          elementCount: reports.length,
          activeCount: reports.filter((item) => item.active).length,
          failed: reports.some((item) => item.violations.length),
          warningCount: reports.reduce((sum, item) => sum + item.warnings.length, 0),
          replaces: domain.replaces,
          elements: reports
        };
      }

      function proveRuntime(root, options = {}) {
        const report = applyRuntimeLaw(root, options);
        const violations = report.elements.flatMap((item) => item.violations.map((message) => ({
          sourceIndex: item.sourceIndex,
          domain: item.domain,
          message
        })));
        return {
          kind: `${domain.reportKind}-proof`,
          id: domain.id,
          label: domain.label,
          generatedAt: now(),
          failed: violations.length > 0,
          passed: violations.length === 0,
          violations,
          warnings: report.elements.flatMap((item) => item.warnings),
          report
        };
      }

      function inspectElement(element, options = {}) {
        return computeElementLaw(element, Number(attr(element, attributes.sourceIndex, "0")), 1);
      }

      function buildSubsumptionPlan() {
        return {
          kind: "mcel-subsumption-plan",
          domain: domain.id,
          label: domain.label,
          replaces: domain.replaces,
          thesis: domain.thesis,
          migration: domain.migration,
          proofObligations: domain.proofObligations,
          axes: [
            "source trait",
            "compiler hook",
            "runtime law",
            "serializer firewall",
            "editor control",
            "command operation",
            "graph/provenance",
            "browser observation",
            "acid proof",
            "evidence packet"
          ]
        };
      }

      const descriptor = {
        id: domain.id,
        label: domain.label,
        version: "v1",
        reads: domain.reads.map((name) => attributes[name]).filter(Boolean),
        writesRuntimeOnly: domain.runtimeAttributes.map((name) => attributes[name]).filter(Boolean),
        sourcePollutionForbidden: true,
        compute: computeElementLaw,
        apply: applyRuntimeLaw,
        inspect: inspectElement,
        prove: proveRuntime,
        reportFor: applyRuntimeLaw
      };

      if (registry?.register) registry.register(descriptor);

      return Object.freeze({
        descriptor,
        domain,
        computeElementLaw,
        applyElementLaw,
        applyRuntimeLaw,
        proveRuntime,
        inspectElement,
        buildSubsumptionPlan
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabComponentLaw = McelLabComponentLaw;
    }
