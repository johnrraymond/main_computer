var McelLabPlatformSpine = (() => {
  const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
  const registry = typeof McelLabLawRegistry !== "undefined" ? McelLabLawRegistry : window.McelLabLawRegistry;
  const {attributes, contractVersion} = contract;

  function now() {
    return new Date().toISOString();
  }

  function modules() {
    return [
      window.McelLabComponentLaw,
      window.McelLabStateLaw,
      window.McelLabDataLaw,
      window.McelLabFormLaw,
      window.McelLabActionLaw,
      window.McelLabRenderLaw,
      window.McelLabA11yLaw,
      window.McelLabPerformanceLaw
    ].filter(Boolean);
  }

  const obsoleteLibraryMap = Object.freeze([
    Object.freeze({
      family: "component frameworks",
      legacy: ["React", "Vue", "Svelte", "Angular component model", "Lit"],
      mcelAxis: "component.slots.props.v1",
      replacementClaim: "semantic components with source-owned identity, slots, props, generated runtime structure, proof, and clean serialization"
    }),
    Object.freeze({
      family: "state libraries",
      legacy: ["Redux Toolkit", "Zustand", "MobX", "XState-only state machines"],
      mcelAxis: "state.ownership.replay.v1",
      replacementClaim: "state ownership, scope, mutation authority, replayability, and runtime-only derived state proof"
    }),
    Object.freeze({
      family: "server-state libraries",
      legacy: ["TanStack Query", "SWR", "Apollo cache glue"],
      mcelAxis: "data.query.cache.sync.v1",
      replacementClaim: "query, cache, mutation, sync, freshness, offline, and error policies as semantic source law"
    }),
    Object.freeze({
      family: "form libraries",
      legacy: ["React Hook Form", "Formik", "custom validation glue"],
      mcelAxis: "form.validation.errors.v1",
      replacementClaim: "submit, validation, dirty state, error display, a11y, and serializer firewall as one proof surface"
    }),
    Object.freeze({
      family: "HTML action libraries",
      legacy: ["htmx", "imperative event handlers", "manual DOM swaps"],
      mcelAxis: "action.event.swap.v1",
      replacementClaim: "semantic actions, targets, event ownership, swap policy, and safe runtime mutation proof"
    }),
    Object.freeze({
      family: "meta-frameworks",
      legacy: ["Next.js", "Astro", "Nuxt/SvelteKit-style routing"],
      mcelAxis: "render.route.hydration.v1",
      replacementClaim: "route, render mode, hydration, islands, cache, edge/offline policy, and browser evidence"
    }),
    Object.freeze({
      family: "a11y/focus helpers",
      legacy: ["late ARIA lint passes", "manual focus trap helpers", "component-library assumptions"],
      mcelAxis: "a11y.focus.semantic.v1",
      replacementClaim: "accessibility and focus as runtime law tied to layout, scroll, generated decoration, and source meaning"
    }),
    Object.freeze({
      family: "performance/security tools",
      legacy: ["Lighthouse-only cleanup", "bundle budget plugins", "manual CSP notes"],
      mcelAxis: "performance.security.budget.v1",
      replacementClaim: "performance and security budgets as semantic constraints on rendering, hydration, data, and user-content boundaries"
    }),
    Object.freeze({
      family: "styling systems",
      legacy: ["Tailwind utility soup", "CSS-in-JS class machines", "design-token-only systems"],
      mcelAxis: "style.semantic-tokens.v1 + layout.overflow.scroll.v1",
      replacementClaim: "semantic design intent, generated tokens, layout law, overflow ownership, and browser geometry proof"
    }),
    Object.freeze({
      family: "browser tests/workbenches",
      legacy: ["Storybook", "Playwright-only assertions", "Cypress imperative tests"],
      mcelAxis: "mcel-workbench + browser semantic runner",
      replacementClaim: "scenarios, acid tests, browser observation, evidence packets, and semantic proof oracles"
    })
  ]);

  const axisOrder = Object.freeze([
    "source schema",
    "compiler validation",
    "runtime law",
    "serializer firewall",
    "editor controls",
    "command language",
    "graph/provenance",
    "browser observation",
    "a11y/focus proof",
    "performance/security proof",
    "acid tests",
    "evidence packet",
    "supervisor gate"
  ]);

  function sourceElements(root) {
    return [...(root?.querySelectorAll?.(`[${attributes.type}]`) || [])];
  }

  function applyPlatformLaws(root, options = {}) {
    const reports = modules()
      .filter((module) => typeof module.applyRuntimeLaw === "function")
      .map((module) => module.applyRuntimeLaw(root, {reason: options.reason || "platform-spine"}));
    return {
      kind: "mcel-platform-spine-apply-report",
      contractVersion,
      generatedAt: now(),
      moduleCount: reports.length,
      elementCount: sourceElements(root).length,
      failed: reports.some((report) => report.failed),
      reports
    };
  }

  function provePlatform(root, options = {}) {
    const reports = modules()
      .filter((module) => typeof module.proveRuntime === "function")
      .map((module) => module.proveRuntime(root, {reason: options.reason || "platform-spine"}));
    return {
      kind: "mcel-platform-spine-proof",
      contractVersion,
      generatedAt: now(),
      moduleCount: reports.length,
      failed: reports.some((report) => report.failed),
      passed: reports.every((report) => report.passed !== false),
      reports
    };
  }

  function buildSubsumptionLattice() {
    const lawPlans = modules()
      .filter((module) => typeof module.buildSubsumptionPlan === "function")
      .map((module) => module.buildSubsumptionPlan());
    return {
      kind: "mcel-subsumption-lattice",
      contractVersion,
      generatedAt: now(),
      doctrine: "MCEL is the Rust/Java layer over HTML, CSS, DOM, browser APIs, state, data, and network assembly.",
      zeroSharpEdges: [
        "meaning is source-owned",
        "machinery is runtime-owned",
        "observed browser facts are never serialized",
        "laws are explicit and registered",
        "proof claims produce evidence packets",
        "repair is separated from source mutation",
        "legacy frameworks are adapters until MCEL-native laws replace them"
      ],
      axisOrder: [...axisOrder],
      obsoleteLibraryMap: obsoleteLibraryMap.map((item) => ({...item})),
      lawPlans,
      lawRegistry: registry?.list ? registry.list() : []
    };
  }

  function buildAdoptionCase(options = {}) {
    const lattice = buildSubsumptionLattice();
    const lawPlans = Array.isArray(lattice.lawPlans) ? lattice.lawPlans : [];
    const registeredLaws = Array.isArray(lattice.lawRegistry) ? lattice.lawRegistry : [];
    const proofObligations = lawPlans.flatMap((plan) => Array.isArray(plan.proofObligations) ? plan.proofObligations : []);
    const comparisonStandard = [
      "A replacement is not better because MCEL says it is better.",
      "MCEL is better only when replacement claims map to laws, proof obligations, browser facts, evidence packets, and a supervisor gate."
    ];
    const gates = [
      {
        id: "concrete-prior-art",
        label: "Concrete prior-art families are named before replacement is claimed.",
        passed: lattice.obsoleteLibraryMap.length >= 8,
        evidence: lattice.obsoleteLibraryMap.map((item) => item.family)
      },
      {
        id: "law-backed-claims",
        label: "Replacement claims are backed by MCEL law plans.",
        passed: lawPlans.length >= 8,
        evidence: lawPlans.map((plan) => plan.domain || plan.label).filter(Boolean)
      },
      {
        id: "proof-obligations",
        label: "Each law family carries proof obligations instead of prose-only superiority.",
        passed: proofObligations.length >= lawPlans.length * 4,
        evidence: proofObligations
      },
      {
        id: "evidence-packet-and-supervisor",
        label: "Adoption depends on evidence packets and the supervisor gate.",
        passed: lattice.axisOrder.includes("evidence packet") && lattice.axisOrder.includes("supervisor gate"),
        evidence: lattice.axisOrder
      },
      {
        id: "source-runtime-separation",
        label: "The proof keeps source meaning separate from generated runtime machinery.",
        passed: lattice.zeroSharpEdges.includes("meaning is source-owned") && lattice.zeroSharpEdges.includes("machinery is runtime-owned"),
        evidence: lattice.zeroSharpEdges
      },
      {
        id: "registry-accountability",
        label: "Registered laws make the comparison machine-readable.",
        passed: registeredLaws.length >= lawPlans.length,
        evidence: registeredLaws.map((item) => item.id || item.label).filter(Boolean)
      }
    ];
    const passed = gates.every((gate) => gate.passed);
    return {
      kind: "mcel-adoption-case",
      contractVersion,
      generatedAt: now(),
      question: options.question || "Does MCEL make sense to use instead of scattered legacy UI libraries?",
      verdict: passed ? "adopt-mcel-when-proof-is-required" : "hold-until-proof-gates-pass",
      summary: passed
        ? "MCEL makes sense when the work needs source-owned semantics, runtime law, browser-observed evidence, and a machine-checkable replacement story for legacy library glue."
        : "MCEL adoption is not justified yet because at least one comparison gate lacks evidence.",
      comparisonStandard,
      proofCoverage: {
        obsoleteFamilyCount: lattice.obsoleteLibraryMap.length,
        lawPlanCount: lawPlans.length,
        registeredLawCount: registeredLaws.length,
        proofObligationCount: proofObligations.length,
        axisCount: lattice.axisOrder.length,
        passedGateCount: gates.filter((gate) => gate.passed).length,
        totalGateCount: gates.length
      },
      gates,
      replacementClaims: lattice.obsoleteLibraryMap.map((item) => ({
        family: item.family,
        legacy: item.legacy,
        mcelAxis: item.mcelAxis,
        replacementClaim: item.replacementClaim
      }))
    };
  }

  function buildAxisMatrix(feature = "platform-spine") {
    const lattice = buildSubsumptionLattice();
    return {
      kind: "mcel-platform-axis-matrix",
      feature,
      generatedAt: now(),
      axes: [...axisOrder],
      rows: lattice.obsoleteLibraryMap.map((item) => ({
        feature: item.family,
        oldLibraries: item.legacy,
        mcelAxis: item.mcelAxis,
        replacementClaim: item.replacementClaim,
        requiredProof: axisOrder
      }))
    };
  }

  return Object.freeze({
    obsoleteLibraryMap,
    axisOrder,
    modules,
    applyPlatformLaws,
    provePlatform,
    buildSubsumptionLattice,
    buildAdoptionCase,
    buildAxisMatrix
  });
})();

if (typeof window !== "undefined") {
  window.McelLabPlatformSpine = McelLabPlatformSpine;
}
