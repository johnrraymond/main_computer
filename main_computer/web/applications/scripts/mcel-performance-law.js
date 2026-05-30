var McelLabPerformanceLaw = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const registry = typeof McelLabLawRegistry !== "undefined" ? McelLabLawRegistry : window.McelLabLawRegistry;
      const {attributes, contractVersion} = contract;

      const domain = Object.freeze({
        "id": "performance.security.budget.v1",
        "label": "Performance / Security Budget Law",
        "reportKind": "mcel-performance-law-report",
        "reads": [
                "performanceBudget",
                "securityPolicy",
                "renderMode",
                "hydration",
                "query"
        ],
        "required": [],
        "runtimeAttributes": [
                "performanceLaw",
                "securityLaw"
        ],
        "alwaysOn": false,
        "dependencyMode": "semantic-budget",
        "replaces": [
                "ad hoc Lighthouse cleanups",
                "bundle-budget plugins without semantics",
                "manual CSP threat notes",
                "performance as afterthought"
        ],
        "thesis": "Performance and security become source policies that constrain runtime generation, hydration, data, and user-content boundaries.",
        "migration": [
                "declare budgets on source surfaces",
                "map dangerous/user-content/networked regions",
                "prove generated runtime respects budgets",
                "emit evidence when budgets are only estimated"
        ],
        "proofObligations": [
                "critical/tiny budgets avoid eager hydration",
                "user-content surfaces are sandboxed or explicit",
                "networked regions declare data policy",
                "budget risk is runtime-only"
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

        const budget = attr(element, attributes.performanceBudget);
        const hydration = attr(element, attributes.hydration);
        const security = attr(element, attributes.securityPolicy);
        if (["tiny", "critical"].includes(budget) && ["eager", "interaction"].includes(hydration)) warnings.push("tiny/critical budget should avoid eager or interaction hydration");
        if (security === "user-content" && attr(element, attributes.renderMode) === "client") warnings.push("client-rendered user content should be sandboxed or explicitly trusted");

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
      window.McelLabPerformanceLaw = McelLabPerformanceLaw;
    }
