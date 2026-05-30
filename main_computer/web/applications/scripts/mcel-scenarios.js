    var McelLabScenarios = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const scenarios = Object.freeze([
        Object.freeze({
          id: "round-trip",
          label: "Round Trip Proof",
          mode: "diff",
          description: "Clean source compiles, serializes, and recompiles without generated-part leakage.",
          source: contract.defaultSource
        }),
        Object.freeze({
          id: "neighborhood",
          label: "Neighborhood Cluster",
          mode: "runtime",
          description: "Three adjacent smart widgets compute cluster-start, cluster-middle, and cluster-end.",
          source: `<section data-mc="panel" data-mc-kind="signal" data-mc-flow="forward" data-mc-rank="primary" data-mc-state="live" data-mc-density="auto" data-mc-words="cluster start signal">
  <h2>Cluster Start</h2>
  <p>The first smart element should know it starts a cluster.</p>
</section>
<section data-mc="panel" data-mc-kind="work" data-mc-flow="reverse" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="auto" data-mc-words="cluster middle work">
  <h2>Cluster Middle</h2>
  <p>The second smart element should know it sits inside the cluster.</p>
</section>
<section data-mc="proof-surface" data-mc-kind="proof" data-mc-flow="split" data-mc-rank="secondary" data-mc-state="idle" data-mc-density="auto" data-mc-words="cluster end proof">
  <h2>Cluster End</h2>
  <p>The final smart element should know it closes the cluster.</p>
</section>`
        }),
        Object.freeze({
          id: "relation",
          label: "Relation Hooks",
          mode: "runtime",
          description: "data-mc-connects resolves a relation without hardcoding layout.",
          source: `<section id="alpha-signal" data-mc="panel" data-mc-kind="signal" data-mc-flow="forward" data-mc-rank="primary" data-mc-state="live" data-mc-density="auto" data-mc-words="alpha signal relation">
  <h2>Alpha Signal</h2>
  <p>This source element is the relation target.</p>
</section>
<section data-mc="proof-surface" data-mc-kind="proof" data-mc-flow="split" data-mc-rank="secondary" data-mc-state="live" data-mc-density="auto" data-mc-words="proof relation resolved" data-mc-connects="alpha-signal">
  <h2>Related Proof</h2>
  <p>This source element resolves its connection through semantic attributes.</p>
</section>`
        }),
        Object.freeze({
          id: "dumb-dom",
          label: "Dumb DOM Containment",
          mode: "source",
          description: "Smart widgets survive inside ordinary HTML without requiring a framework island.",
          source: `<article>
  <header>
    <h1>Ordinary Article</h1>
    <p>This wrapper is not an MCEL widget.</p>
  </header>
  <section data-mc="panel" data-mc-kind="article" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="auto" data-mc-words="ordinary html semantic island">
    <h2>Semantic Island</h2>
    <p>The compiler should enhance only this source element and leave the article wrapper alone.</p>
  </section>
</article>`
        }),
        Object.freeze({
          id: "a11y",
          label: "A11y Guard",
          mode: "a11y",
          description: "Generated decoration stays hidden while source headings label smart regions.",
          source: `<section data-mc="smart-region" data-mc-kind="article" data-mc-flow="stack" data-mc-rank="primary" data-mc-state="live" data-mc-density="auto" data-mc-words="accessibility reading order label">
  <h2>Accessible Smart Region</h2>
  <p>Generated rails and fields should not pollute the reading order.</p>
</section>`
        }),
        Object.freeze({
          id: "layout-overflow-proof",
          label: "Layout Overflow Proof",
          mode: "runtime",
          description: "Scroll and overflow policies compile into runtime-only geometry proof facts that serialize away.",
          source: `<section data-mc="panel" data-mc-kind="proof" data-mc-flow="stack" data-mc-rank="primary" data-mc-state="live" data-mc-density="dense" data-mc-size-policy="fixed" data-mc-overflow-policy="clip" data-mc-scroll-policy="never" data-mc-words="no internal scrollbar geometry proof">
  <h2>No Internal Scrollbar</h2>
  <p>This source says internal scrollbars are illegal. The runtime may clip or escalate, but source cleanup must not retain observed geometry.</p>
  <p>Extra copy creates layout pressure so the observer, law registry, a11y report, graph, evidence packet, and acid tests all have something to prove.</p>
</section>
<section data-mc="panel" data-mc-kind="work" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="comfortable" data-mc-size-policy="adaptive" data-mc-overflow-policy="delegate" data-mc-scroll-policy="external" data-mc-words="delegate scroll owner parent">
  <h2>Delegated Overflow</h2>
  <p>This source delegates scrolling to the surrounding surface rather than creating a nested trap.</p>
</section>`
        }),
        Object.freeze({
          id: "platform-spine-subsumption",
          label: "Platform Spine Subsumption",
          mode: "runtime",
          description: "One source surface activates component, state, data, form, action, render, accessibility, performance, and security laws to prove MCEL can replace framework stacks.",
          source: `<section data-mc="panel" data-mc-kind="work" data-mc-flow="stack" data-mc-rank="primary" data-mc-state="live" data-mc-density="auto" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-component="ObsoleteFrameworkPanel" data-mc-component-kind="component" data-mc-state-owner="view" data-mc-state-scope="framework-replacement" data-mc-state-policy="replayable" data-mc-query="frameworks.status" data-mc-cache-policy="stale-while-revalidate" data-mc-mutation="frameworks.replace" data-mc-sync-policy="optimistic" data-mc-action="replace-legacy-framework" data-mc-target="obsolete-stack" data-mc-swap-policy="lawful-region" data-mc-event-policy="audited" data-mc-route="/mcel/subsumption" data-mc-render="island" data-mc-hydration="visible" data-mc-island="interactive" data-mc-focus-policy="preserve" data-mc-a11y-policy="strict" data-mc-performance-budget="small" data-mc-security-policy="trusted" data-mc-words="rust java cplusplus powers without sharp edges">
  <h2 data-mc-slot="title">Framework Replacement Surface</h2>
  <p data-mc-slot="body">This single semantic surface names the jobs usually delegated to React, Next, Tailwind, TanStack Query, form libraries, htmx, Storybook, and Playwright-only tests.</p>
  <button type="button" data-mc-action="replace-legacy-framework" data-mc-event-policy="audited">Prove replacement</button>
</section>
<form data-mc="smart-region" data-mc-kind="work" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="calm" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-submit="framework.audit" data-mc-validation="schema" data-mc-dirty-policy="warn" data-mc-error-policy="inline-and-summary" data-mc-a11y-policy="strict" data-mc-performance-budget="small" data-mc-words="form validation dirty error proof">
  <h2>Library-Killing Form</h2>
  <label>Claim <input name="claim" required value="MCEL replaces glue libraries"></label>
</form>`
        }),
        Object.freeze({
          id: "invalid-schema",
          label: "Schema Normalization",
          mode: "stress",
          description: "Invalid semantic traits normalize to safe defaults instead of crashing the runtime.",
          source: `<section data-mc="unknown-widget" data-mc-kind="nonsense" data-mc-flow="sideways" data-mc-rank="loud" data-mc-state="exploding" data-mc-density="wild">
  <h2>Malformed Source</h2>
  <p>The schema should normalize this source into a safe panel.</p>
</section>`
        })
      ]);

      function all() {
        return scenarios.map((scenario) => ({...scenario}));
      }

      function byId(id) {
        return all().find((scenario) => scenario.id === id) || all()[0];
      }

      return Object.freeze({all, byId});
    })();

    if (typeof window !== "undefined") {
      window.McelLabScenarios = McelLabScenarios;
    }
