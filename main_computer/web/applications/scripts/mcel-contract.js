    var McelLabContract = (() => {
      const attributes = Object.freeze({
        type: "data-mc",
        nullify: "mcel-nullify",
        kind: "data-mc-kind",
        flow: "data-mc-flow",
        rank: "data-mc-rank",
        state: "data-mc-state",
        density: "data-mc-density",
        words: "data-mc-words",
        connects: "data-mc-connects",
        sizePolicy: "data-mc-size-policy",
        overflowPolicy: "data-mc-overflow-policy",
        scrollPolicy: "data-mc-scroll-policy",
        componentName: "data-mc-component",
        componentKind: "data-mc-component-kind",
        slot: "data-mc-slot",
        propContract: "data-mc-prop-contract",
        stateOwner: "data-mc-state-owner",
        stateScope: "data-mc-state-scope",
        statePolicy: "data-mc-state-policy",
        query: "data-mc-query",
        cachePolicy: "data-mc-cache-policy",
        mutation: "data-mc-mutation",
        syncPolicy: "data-mc-sync-policy",
        submit: "data-mc-submit",
        validation: "data-mc-validation",
        dirtyPolicy: "data-mc-dirty-policy",
        errorPolicy: "data-mc-error-policy",
        action: "data-mc-action",
        target: "data-mc-target",
        swapPolicy: "data-mc-swap-policy",
        eventPolicy: "data-mc-event-policy",
        route: "data-mc-route",
        renderMode: "data-mc-render",
        hydration: "data-mc-hydration",
        islandPolicy: "data-mc-island",
        focusPolicy: "data-mc-focus-policy",
        a11yPolicy: "data-mc-a11y-policy",
        performanceBudget: "data-mc-performance-budget",
        securityPolicy: "data-mc-security-policy",
        enhanced: "data-mc-enhanced",
        generated: "data-mc-generated",
        part: "data-mc-part",
        neighborhood: "data-mc-neighborhood",
        computedDensity: "data-mc-density-computed",
        relation: "data-mc-relation",
        relationCount: "data-mc-relation-count",
        clusterSize: "data-mc-cluster-size",
        sourceIndex: "data-mc-source-index",
        editorSelected: "data-mc-editor-selected",
        theme: "data-mc-theme",
        styleLaw: "data-mc-style-law",
        flowAxis: "data-mc-flow-axis",
        fieldPressure: "data-mc-field-pressure",
        attention: "data-mc-attention",
        relationMode: "data-mc-relation-mode",
        layoutLaw: "data-mc-layout-law",
        overflowComputed: "data-mc-overflow-computed",
        scrollNeeded: "data-mc-scroll-needed",
        scrollOwner: "data-mc-scroll-owner",
        layoutPressure: "data-mc-layout-pressure",
        geometryProof: "data-mc-geometry-proof",
        keyboardScroll: "data-mc-keyboard-scroll",
        componentLaw: "data-mc-component-law",
        slotLaw: "data-mc-slot-law",
        stateLaw: "data-mc-state-law",
        dataLaw: "data-mc-data-law",
        formLaw: "data-mc-form-law",
        actionLaw: "data-mc-action-law",
        renderLaw: "data-mc-render-law",
        a11yLaw: "data-mc-a11y-law",
        focusLaw: "data-mc-focus-law",
        performanceLaw: "data-mc-performance-law",
        securityLaw: "data-mc-security-law",
        proofTier: "data-mc-proof-tier",
        semanticRisk: "data-mc-semantic-risk",
        dependencyMode: "data-mc-dependency-mode",
        hydrationProof: "data-mc-hydration-proof",
        dataFreshness: "data-mc-data-freshness",
        formValidity: "data-mc-form-validity",
        actionSafety: "data-mc-action-safety",
        artifactOwner: "data-mc-owner",
        artifactOrigin: "data-mc-origin",
        artifactReason: "data-mc-reason",
        contractVersion: "data-mc-contract-version"
      });

      const contractVersion = "mcel-lab.v0.11-ui-site-skeleton";

      const modes = Object.freeze(["source", "editor", "runtime", "diff", "stress", "a11y"]);

      const defaults = Object.freeze({
        type: "panel",
        kind: "signal",
        flow: "forward",
        rank: "secondary",
        state: "idle",
        density: "auto",
        sizePolicy: "adaptive",
        overflowPolicy: "contain",
        scrollPolicy: "external"
      });

      const runtimeOwnedAttributes = Object.freeze([
        attributes.enhanced,
        attributes.neighborhood,
        attributes.computedDensity,
        attributes.relation,
        attributes.relationCount,
        attributes.clusterSize,
        attributes.sourceIndex,
        attributes.editorSelected,
        attributes.theme,
        attributes.styleLaw,
        attributes.flowAxis,
        attributes.fieldPressure,
        attributes.attention,
        attributes.relationMode,
        attributes.layoutLaw,
        attributes.overflowComputed,
        attributes.scrollNeeded,
        attributes.scrollOwner,
        attributes.layoutPressure,
        attributes.geometryProof,
        attributes.keyboardScroll,
        attributes.componentLaw,
        attributes.slotLaw,
        attributes.stateLaw,
        attributes.dataLaw,
        attributes.formLaw,
        attributes.actionLaw,
        attributes.renderLaw,
        attributes.a11yLaw,
        attributes.focusLaw,
        attributes.performanceLaw,
        attributes.securityLaw,
        attributes.proofTier,
        attributes.semanticRisk,
        attributes.dependencyMode,
        attributes.hydrationProof,
        attributes.dataFreshness,
        attributes.formValidity,
        attributes.actionSafety,
        attributes.artifactOwner,
        attributes.artifactOrigin,
        attributes.artifactReason,
        attributes.contractVersion
      ]);

      const runtimeOwnedClasses = Object.freeze(["mc", "mcel-selected"]);

      const themes = Object.freeze([
        "theme-machine",
        "theme-local",
        "theme-saas",
        "theme-editorial",
        "theme-luxury",
        "theme-civic",
        "theme-accessible",
        "theme-debug"
      ]);

      const themeAliases = Object.freeze({
        "theme-basic": "theme-local",
        basic: "theme-local",
        local: "theme-local",
        "local-service": "theme-local",
        "small-business": "theme-local",
        machine: "theme-machine",
        original: "theme-machine",
        "original-mcel": "theme-machine",
        launch: "theme-saas",
        startup: "theme-saas",
        saas: "theme-saas",
        product: "theme-saas",
        "theme-article": "theme-editorial",
        article: "theme-editorial",
        editorial: "theme-editorial",
        magazine: "theme-editorial",
        "theme-premium": "theme-luxury",
        premium: "theme-luxury",
        luxury: "theme-luxury",
        portfolio: "theme-luxury",
        civic: "theme-civic",
        nonprofit: "theme-civic",
        public: "theme-civic",
        "theme-accessibility": "theme-accessible",
        accessibility: "theme-accessible",
        accessible: "theme-accessible",
        "high-contrast": "theme-accessible",
        debug: "theme-debug",
        wireframe: "theme-debug"
      });

      const layoutPolicies = Object.freeze({
        size: Object.freeze(["adaptive", "fixed", "fluid", "intrinsic"]),
        overflow: Object.freeze(["visible", "contain", "clip", "delegate", "paginate", "virtualize", "expand", "collapse"]),
        scroll: Object.freeze(["never", "auto", "required", "external", "child-only", "viewport-only"])
      });

      const platformPolicies = Object.freeze({
        componentKinds: Object.freeze(["component", "page", "layout", "island", "primitive", "adapter"]),
        slots: Object.freeze(["title", "body", "actions", "media", "meta", "fallback"]),
        stateOwners: Object.freeze(["none", "element", "view", "session", "server", "url", "worker"]),
        statePolicies: Object.freeze(["immutable", "local", "shared", "derived", "transactional", "replayable"]),
        cachePolicies: Object.freeze(["none", "network-only", "cache-first", "stale-while-revalidate", "content-hash", "session"]),
        syncPolicies: Object.freeze(["none", "manual", "on-visible", "background", "optimistic", "offline-first"]),
        validationPolicies: Object.freeze(["none", "schema", "native", "server", "hybrid"]),
        dirtyPolicies: Object.freeze(["none", "track", "warn", "block", "autosave"]),
        errorPolicies: Object.freeze(["silent", "inline", "summary", "inline-and-summary", "recoverable", "fatal"]),
        swapPolicies: Object.freeze(["none", "replace", "append", "prepend", "lawful-region", "morph", "stream"]),
        eventPolicies: Object.freeze(["none", "local", "delegated", "command", "transaction", "audited"]),
        renderModes: Object.freeze(["static", "server", "client", "island", "stream", "incremental", "edge", "offline"]),
        hydrationPolicies: Object.freeze(["none", "eager", "visible", "idle", "interaction", "islands", "progressive"]),
        islandPolicies: Object.freeze(["none", "static-shell", "interactive", "lazy", "critical", "portable"]),
        focusPolicies: Object.freeze(["auto", "preserve", "trap", "delegate", "restore", "none"]),
        a11yPolicies: Object.freeze(["auto", "strict", "decorative", "interactive", "landmark", "live-region"]),
        performanceBudgets: Object.freeze(["none", "tiny", "small", "medium", "large", "critical"]),
        securityPolicies: Object.freeze(["default", "sandboxed", "trusted", "user-content", "networked", "dangerous"])
      });

      const schema = Object.freeze({
        panel: Object.freeze({
          generatedParts: Object.freeze(["rail", "copy", "meta", "field"]),
          allowedKinds: Object.freeze(["signal", "work", "hero", "article", "proof"]),
          allowedFlows: Object.freeze(["forward", "reverse", "stack", "split"]),
          allowedRanks: Object.freeze(["primary", "secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"]),
          allowedSizePolicies: layoutPolicies.size,
          allowedOverflowPolicies: layoutPolicies.overflow,
          allowedScrollPolicies: layoutPolicies.scroll,
          allowedComponentKinds: platformPolicies.componentKinds,
          allowedStateOwners: platformPolicies.stateOwners,
          allowedStatePolicies: platformPolicies.statePolicies,
          allowedCachePolicies: platformPolicies.cachePolicies,
          allowedSyncPolicies: platformPolicies.syncPolicies,
          allowedValidationPolicies: platformPolicies.validationPolicies,
          allowedDirtyPolicies: platformPolicies.dirtyPolicies,
          allowedErrorPolicies: platformPolicies.errorPolicies,
          allowedSwapPolicies: platformPolicies.swapPolicies,
          allowedEventPolicies: platformPolicies.eventPolicies,
          allowedRenderModes: platformPolicies.renderModes,
          allowedHydrationPolicies: platformPolicies.hydrationPolicies,
          allowedIslandPolicies: platformPolicies.islandPolicies,
          allowedFocusPolicies: platformPolicies.focusPolicies,
          allowedA11yPolicies: platformPolicies.a11yPolicies,
          allowedPerformanceBudgets: platformPolicies.performanceBudgets,
          allowedSecurityPolicies: platformPolicies.securityPolicies
        }),
        feed: Object.freeze({
          generatedParts: Object.freeze(["rail", "meta", "field"]),
          allowedKinds: Object.freeze(["signal", "work", "article"]),
          allowedFlows: Object.freeze(["stack", "forward", "reverse"]),
          allowedRanks: Object.freeze(["primary", "secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"]),
          allowedSizePolicies: layoutPolicies.size,
          allowedOverflowPolicies: layoutPolicies.overflow,
          allowedScrollPolicies: layoutPolicies.scroll,
          allowedComponentKinds: platformPolicies.componentKinds,
          allowedStateOwners: platformPolicies.stateOwners,
          allowedStatePolicies: platformPolicies.statePolicies,
          allowedCachePolicies: platformPolicies.cachePolicies,
          allowedSyncPolicies: platformPolicies.syncPolicies,
          allowedValidationPolicies: platformPolicies.validationPolicies,
          allowedDirtyPolicies: platformPolicies.dirtyPolicies,
          allowedErrorPolicies: platformPolicies.errorPolicies,
          allowedSwapPolicies: platformPolicies.swapPolicies,
          allowedEventPolicies: platformPolicies.eventPolicies,
          allowedRenderModes: platformPolicies.renderModes,
          allowedHydrationPolicies: platformPolicies.hydrationPolicies,
          allowedIslandPolicies: platformPolicies.islandPolicies,
          allowedFocusPolicies: platformPolicies.focusPolicies,
          allowedA11yPolicies: platformPolicies.a11yPolicies,
          allowedPerformanceBudgets: platformPolicies.performanceBudgets,
          allowedSecurityPolicies: platformPolicies.securityPolicies
        }),
        "command-row": Object.freeze({
          generatedParts: Object.freeze(["rail", "copy", "field"]),
          allowedKinds: Object.freeze(["work", "signal"]),
          allowedFlows: Object.freeze(["forward", "reverse"]),
          allowedRanks: Object.freeze(["secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"]),
          allowedSizePolicies: layoutPolicies.size,
          allowedOverflowPolicies: layoutPolicies.overflow,
          allowedScrollPolicies: layoutPolicies.scroll,
          allowedComponentKinds: platformPolicies.componentKinds,
          allowedStateOwners: platformPolicies.stateOwners,
          allowedStatePolicies: platformPolicies.statePolicies,
          allowedCachePolicies: platformPolicies.cachePolicies,
          allowedSyncPolicies: platformPolicies.syncPolicies,
          allowedValidationPolicies: platformPolicies.validationPolicies,
          allowedDirtyPolicies: platformPolicies.dirtyPolicies,
          allowedErrorPolicies: platformPolicies.errorPolicies,
          allowedSwapPolicies: platformPolicies.swapPolicies,
          allowedEventPolicies: platformPolicies.eventPolicies,
          allowedRenderModes: platformPolicies.renderModes,
          allowedHydrationPolicies: platformPolicies.hydrationPolicies,
          allowedIslandPolicies: platformPolicies.islandPolicies,
          allowedFocusPolicies: platformPolicies.focusPolicies,
          allowedA11yPolicies: platformPolicies.a11yPolicies,
          allowedPerformanceBudgets: platformPolicies.performanceBudgets,
          allowedSecurityPolicies: platformPolicies.securityPolicies
        }),
        "proof-surface": Object.freeze({
          generatedParts: Object.freeze(["rail", "copy", "meta", "field"]),
          allowedKinds: Object.freeze(["proof", "signal", "work"]),
          allowedFlows: Object.freeze(["forward", "reverse", "split"]),
          allowedRanks: Object.freeze(["primary", "secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"]),
          allowedSizePolicies: layoutPolicies.size,
          allowedOverflowPolicies: layoutPolicies.overflow,
          allowedScrollPolicies: layoutPolicies.scroll,
          allowedComponentKinds: platformPolicies.componentKinds,
          allowedStateOwners: platformPolicies.stateOwners,
          allowedStatePolicies: platformPolicies.statePolicies,
          allowedCachePolicies: platformPolicies.cachePolicies,
          allowedSyncPolicies: platformPolicies.syncPolicies,
          allowedValidationPolicies: platformPolicies.validationPolicies,
          allowedDirtyPolicies: platformPolicies.dirtyPolicies,
          allowedErrorPolicies: platformPolicies.errorPolicies,
          allowedSwapPolicies: platformPolicies.swapPolicies,
          allowedEventPolicies: platformPolicies.eventPolicies,
          allowedRenderModes: platformPolicies.renderModes,
          allowedHydrationPolicies: platformPolicies.hydrationPolicies,
          allowedIslandPolicies: platformPolicies.islandPolicies,
          allowedFocusPolicies: platformPolicies.focusPolicies,
          allowedA11yPolicies: platformPolicies.a11yPolicies,
          allowedPerformanceBudgets: platformPolicies.performanceBudgets,
          allowedSecurityPolicies: platformPolicies.securityPolicies
        }),
        "smart-region": Object.freeze({
          generatedParts: Object.freeze(["rail", "meta", "field"]),
          allowedKinds: Object.freeze(["article", "signal", "work", "proof"]),
          allowedFlows: Object.freeze(["stack", "forward", "reverse", "split"]),
          allowedRanks: Object.freeze(["primary", "secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"]),
          allowedSizePolicies: layoutPolicies.size,
          allowedOverflowPolicies: layoutPolicies.overflow,
          allowedScrollPolicies: layoutPolicies.scroll,
          allowedComponentKinds: platformPolicies.componentKinds,
          allowedStateOwners: platformPolicies.stateOwners,
          allowedStatePolicies: platformPolicies.statePolicies,
          allowedCachePolicies: platformPolicies.cachePolicies,
          allowedSyncPolicies: platformPolicies.syncPolicies,
          allowedValidationPolicies: platformPolicies.validationPolicies,
          allowedDirtyPolicies: platformPolicies.dirtyPolicies,
          allowedErrorPolicies: platformPolicies.errorPolicies,
          allowedSwapPolicies: platformPolicies.swapPolicies,
          allowedEventPolicies: platformPolicies.eventPolicies,
          allowedRenderModes: platformPolicies.renderModes,
          allowedHydrationPolicies: platformPolicies.hydrationPolicies,
          allowedIslandPolicies: platformPolicies.islandPolicies,
          allowedFocusPolicies: platformPolicies.focusPolicies,
          allowedA11yPolicies: platformPolicies.a11yPolicies,
          allowedPerformanceBudgets: platformPolicies.performanceBudgets,
          allowedSecurityPolicies: platformPolicies.securityPolicies
        })
      });

      const defaultSource = `<main>
  <section>
    <p>Neighborhood Cluster · Open today</p>
    <h1>A useful local site from almost plain HTML.</h1>
    <p>MCEL turns simple semantic sections into a resilient product surface with layout, proof, state, actions, forms, and accessibility rules layered on top.</p>
    <p><a href="#join">Join the list</a></p>
  </section>

  <section>
    <h2>Neighborhood Cluster</h2>
    <article>
      <h3>Fresh daily</h3>
      <p>Simple source becomes a polished card without hand-authored wrapper soup.</p>
    </article>
    <article>
      <h3>Pickup + delivery</h3>
      <p>The layout law expands content instead of making each card a scroll trap.</p>
    </article>
    <article>
      <h3>Proof visible</h3>
      <p>Runtime facts are generated, inspected, and stripped back out on serialize.</p>
    </article>
  </section>

  <form id="join">
    <h2>Get the weekly market note.</h2>
    <label>Email <input name="email" type="email" required placeholder="you@example.com"></label>
    <button type="submit">Notify me</button>
  </form>

  <nav aria-label="Market details">
    <h2>Open 7am–7pm · 12th and Pine</h2>
    <p>One semantic command row, zero nested scrollbars.</p>
  </nav>
</main>`;

      const blockTemplates = Object.freeze({
        panel: `<section data-mc="panel" data-mc-kind="signal" data-mc-flow="forward" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="auto" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-words="semantic source html">
  <h2>New Panel</h2>
  <p>Clean MCEL source that can compile into runtime structure.</p>
</section>`,
        hero: `<section data-mc="panel" data-mc-kind="hero" data-mc-flow="split" data-mc-rank="primary" data-mc-state="live" data-mc-density="calm" data-mc-size-policy="fluid" data-mc-overflow-policy="expand" data-mc-scroll-policy="never" data-mc-words="hero proof surface">
  <h2>Hero Panel</h2>
  <p>A semantic hero panel without generated wrapper pollution.</p>
</section>`,
        signal: `<section data-mc="panel" data-mc-kind="signal" data-mc-flow="reverse" data-mc-rank="primary" data-mc-state="live" data-mc-density="dense" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-words="signal argument stream public">
  <h2>Signal Panel</h2>
  <p>A live signal block for public argument streams.</p>
</section>`,
        work: `<section data-mc="panel" data-mc-kind="work" data-mc-flow="forward" data-mc-rank="secondary" data-mc-state="idle" data-mc-density="calm" data-mc-size-policy="adaptive" data-mc-overflow-policy="delegate" data-mc-scroll-policy="external" data-mc-words="work compiler module">
  <h2>Work Panel</h2>
  <p>A work block for implementation progress.</p>
</section>`,
        feed: `<section data-mc="feed" data-mc-kind="signal" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="live" data-mc-density="dense" data-mc-size-policy="fixed" data-mc-overflow-policy="virtualize" data-mc-scroll-policy="required" data-mc-words="feed stream items">
  <h2>Feed</h2>
  <p>Container source for future feed item intelligence.</p>
</section>`,
        "command-row": `<section data-mc="command-row" data-mc-kind="work" data-mc-flow="forward" data-mc-rank="minor" data-mc-state="idle" data-mc-density="compressed" data-mc-size-policy="intrinsic" data-mc-overflow-policy="clip" data-mc-scroll-policy="never" data-mc-words="command action controls">
  <h2>Command Row</h2>
  <p>Semantic command strip source.</p>
</section>`,
        proof: `<section data-mc="proof-surface" data-mc-kind="proof" data-mc-flow="split" data-mc-rank="primary" data-mc-state="live" data-mc-density="auto" data-mc-size-policy="adaptive" data-mc-overflow-policy="paginate" data-mc-scroll-policy="child-only" data-mc-words="proof verification serializer">
  <h2>Proof Surface</h2>
  <p>Runtime and serializer claims should be proven here.</p>
</section>`,
        "smart-region": `<section data-mc="smart-region" data-mc-kind="article" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="auto" data-mc-size-policy="fluid" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-words="region neighborhood a11y">
  <h2>Smart Region</h2>
  <p>A general semantic region for neighborhood and accessibility tests.</p>
</section>`,
        component: `<section data-mc="panel" data-mc-kind="work" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="auto" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-component="SemanticComponent" data-mc-component-kind="component" data-mc-state-owner="element" data-mc-state-policy="local" data-mc-render="island" data-mc-hydration="interaction" data-mc-a11y-policy="strict" data-mc-performance-budget="small" data-mc-words="component slots props">
  <h2 data-mc-slot="title">Semantic Component</h2>
  <p data-mc-slot="body">Component structure, slots, props, state, events, a11y, and performance are source-level policies.</p>
</section>`,
        form: `<form data-mc="smart-region" data-mc-kind="work" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="auto" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-submit="profile.update" data-mc-validation="schema" data-mc-dirty-policy="warn" data-mc-error-policy="inline-and-summary" data-mc-a11y-policy="strict" data-mc-performance-budget="small" data-mc-words="form validation errors">
  <h2>Lawful Form</h2>
  <label>Name <input name="name" required></label>
  <button type="submit" data-mc-action="submit-form" data-mc-event-policy="audited">Save</button>
</form>`,
        route: `<section data-mc="proof-surface" data-mc-kind="proof" data-mc-flow="split" data-mc-rank="primary" data-mc-state="live" data-mc-density="auto" data-mc-size-policy="fluid" data-mc-overflow-policy="delegate" data-mc-scroll-policy="external" data-mc-route="/mcel/proof" data-mc-render="stream" data-mc-hydration="islands" data-mc-cache-policy="content-hash" data-mc-security-policy="trusted" data-mc-performance-budget="medium" data-mc-words="route render hydration">
  <h2>Semantic Route</h2>
  <p>Routing, rendering, hydration, caching, and security are source policies with runtime proof.</p>
</section>`
      });


      const editorCatalog = () => Object.freeze({
        schemaTypes: Object.freeze(Object.keys(schema)),
        blockTemplates: Object.freeze(Object.keys(blockTemplates)),
        slots: platformPolicies.slots,
        attributes: Object.freeze({...attributes}),
        policies: Object.freeze({
          layout: layoutPolicies,
          platform: platformPolicies
        }),
        defaultEnrichments: Object.freeze({
          regions: Object.freeze([
            Object.freeze({tag: "main", type: "smart-region", kind: "article", flow: "stack", rank: "primary", state: "live", density: "calm", sizePolicy: "fluid", overflowPolicy: "delegate", scrollPolicy: "external"}),
            Object.freeze({tag: "section", type: "panel", kind: "signal", flow: "forward", rank: "secondary", state: "idle", density: "auto", sizePolicy: "adaptive", overflowPolicy: "contain", scrollPolicy: "auto"}),
            Object.freeze({tag: "article", type: "panel", kind: "article", flow: "stack", rank: "secondary", state: "idle", density: "dense", sizePolicy: "adaptive", overflowPolicy: "contain", scrollPolicy: "auto"}),
            Object.freeze({tag: "form", type: "smart-region", kind: "work", flow: "stack", rank: "secondary", state: "draft", density: "auto", sizePolicy: "adaptive", overflowPolicy: "contain", scrollPolicy: "auto", validation: "native", dirtyPolicy: "warn", errorPolicy: "inline"}),
            Object.freeze({tag: "nav", type: "command-row", kind: "work", flow: "forward", rank: "minor", state: "idle", density: "compressed", sizePolicy: "intrinsic", overflowPolicy: "clip", scrollPolicy: "never"}),
            Object.freeze({tag: "aside", type: "panel", kind: "signal", flow: "stack", rank: "minor", state: "idle", density: "dense", sizePolicy: "adaptive", overflowPolicy: "contain", scrollPolicy: "auto"})
          ]),
          slots: Object.freeze([
            Object.freeze({selector: "h1,h2,h3,h4,h5,h6", slot: "title"}),
            Object.freeze({selector: "p", slot: "body"}),
            Object.freeze({selector: "figure,picture,img,video", slot: "media"})
          ]),
          actions: Object.freeze([
            Object.freeze({selector: "a[href]", attribute: attributes.action}),
            Object.freeze({selector: "button", attribute: attributes.action})
          ]),
          generatedParts: Object.freeze(["rail", "copy", "meta", "field"])
        }),
        nullifyAttribute: attributes.nullify,
        nullifiableEnrichments: Object.freeze({
          all: Object.freeze(["data-mc", "data-mc-kind", "data-mc-flow", "data-mc-rank", "data-mc-state", "data-mc-density", "data-mc-size-policy", "data-mc-overflow-policy", "data-mc-scroll-policy", "data-mc-slot", "data-mc-action", "data-mc-event-policy", "data-mc-submit", "data-mc-validation", "data-mc-dirty-policy", "data-mc-error-policy", "rail", "copy", "meta", "field"]),
          region: Object.freeze(["data-mc", "data-mc-kind", "data-mc-flow", "data-mc-rank", "data-mc-state", "data-mc-density", "data-mc-size-policy", "data-mc-overflow-policy", "data-mc-scroll-policy"]),
          traits: Object.freeze(["data-mc-kind", "data-mc-flow", "data-mc-rank", "data-mc-state", "data-mc-density", "data-mc-size-policy", "data-mc-overflow-policy", "data-mc-scroll-policy"]),
          slot: Object.freeze(["data-mc-slot"]),
          action: Object.freeze(["data-mc-action", "data-mc-event-policy"]),
          form: Object.freeze(["data-mc-submit", "data-mc-validation", "data-mc-dirty-policy", "data-mc-error-policy"]),
          generated: Object.freeze(["rail", "copy", "meta", "field"])
        })
      });

      return Object.freeze({
        attributes,
        modes,
        defaults,
        runtimeOwnedAttributes,
        runtimeOwnedClasses,
        themes,
        themeAliases,
        layoutPolicies,
        platformPolicies,
        contractVersion,
        schema,
        defaultSource,
        blockTemplates,
        editorCatalog
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabContract = McelLabContract;
    }
