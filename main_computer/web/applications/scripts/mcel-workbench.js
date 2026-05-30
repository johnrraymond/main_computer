var McelLabWorkbench = (() => {
  const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
  const scenarios = typeof McelLabScenarios !== "undefined" ? McelLabScenarios : window.McelLabScenarios;
  const platform = typeof McelLabPlatformSpine !== "undefined" ? McelLabPlatformSpine : window.McelLabPlatformSpine;
  const registry = typeof McelLabLawRegistry !== "undefined" ? McelLabLawRegistry : window.McelLabLawRegistry;
  const {contractVersion} = contract;

  function now() {
    return new Date().toISOString();
  }

  function scenarioBlueprints() {
    return [
      {
        id: "component-obsolescence",
        label: "Component Framework Obsolescence",
        proves: ["component identity", "slot mapping", "serializer cleanliness", "state boundary"]
      },
      {
        id: "state-data-obsolescence",
        label: "State/Data Library Obsolescence",
        proves: ["state ownership", "query cache policy", "mutation sync policy", "freshness evidence"]
      },
      {
        id: "form-action-obsolescence",
        label: "Form/Action Library Obsolescence",
        proves: ["submit contract", "validation policy", "event ownership", "lawful swap target"]
      },
      {
        id: "render-a11y-performance-obsolescence",
        label: "Meta-framework/A11y/Performance Obsolescence",
        proves: ["route policy", "hydration boundary", "strict a11y", "budget/security constraints"]
      }
    ];
  }

  function buildWorkbenchPlan() {
    const allScenarios = typeof scenarios?.all === "function" ? scenarios.all() : [];
    return {
      kind: "mcel-workbench-plan",
      contractVersion,
      generatedAt: now(),
      purpose: "Replace Storybook-style examples with executable MCEL scenarios that produce semantic evidence.",
      currentScenarioCount: allScenarios.length,
      requiredBlueprints: scenarioBlueprints(),
      lattice: platform?.buildSubsumptionLattice ? platform.buildSubsumptionLattice() : null,
      registry: registry?.list ? registry.list() : []
    };
  }

  function buildEvidenceChecklist() {
    return {
      kind: "mcel-workbench-evidence-checklist",
      generatedAt: now(),
      checks: [
        "source traits are explicit",
        "registered law applied",
        "runtime-only facts stripped by serializer",
        "browser observer can attach",
        "a11y/focus implication recorded",
        "performance/security budget recorded",
        "legacy-library replacement claim names a proof obligation",
        "supervisor gate can fail the claim"
      ]
    };
  }

  return Object.freeze({
    scenarioBlueprints,
    buildWorkbenchPlan,
    buildEvidenceChecklist
  });
})();

if (typeof window !== "undefined") {
  window.McelLabWorkbench = McelLabWorkbench;
}
