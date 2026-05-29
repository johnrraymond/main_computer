    const McelLabContract = (() => {
      const attributes = Object.freeze({
        type: "data-mc",
        kind: "data-mc-kind",
        flow: "data-mc-flow",
        rank: "data-mc-rank",
        state: "data-mc-state",
        density: "data-mc-density",
        words: "data-mc-words",
        connects: "data-mc-connects",
        enhanced: "data-mc-enhanced",
        generated: "data-mc-generated",
        part: "data-mc-part",
        neighborhood: "data-mc-neighborhood",
        computedDensity: "data-mc-density-computed",
        relation: "data-mc-relation",
        relationCount: "data-mc-relation-count",
        clusterSize: "data-mc-cluster-size",
        sourceIndex: "data-mc-source-index",
        editorSelected: "data-mc-editor-selected"
      });

      const modes = Object.freeze(["source", "editor", "runtime", "diff", "stress", "a11y"]);

      const defaults = Object.freeze({
        type: "panel",
        kind: "signal",
        flow: "forward",
        rank: "secondary",
        state: "idle",
        density: "auto"
      });

      const runtimeOwnedAttributes = Object.freeze([
        attributes.enhanced,
        attributes.neighborhood,
        attributes.computedDensity,
        attributes.relation,
        attributes.relationCount,
        attributes.clusterSize,
        attributes.sourceIndex,
        attributes.editorSelected
      ]);

      const runtimeOwnedClasses = Object.freeze(["mc", "mcel-selected"]);

      const schema = Object.freeze({
        panel: Object.freeze({
          generatedParts: Object.freeze(["rail", "copy", "meta", "field"]),
          allowedKinds: Object.freeze(["signal", "work", "hero", "article", "proof"]),
          allowedFlows: Object.freeze(["forward", "reverse", "stack", "split"]),
          allowedRanks: Object.freeze(["primary", "secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"])
        }),
        feed: Object.freeze({
          generatedParts: Object.freeze(["rail", "meta", "field"]),
          allowedKinds: Object.freeze(["signal", "work", "article"]),
          allowedFlows: Object.freeze(["stack", "forward", "reverse"]),
          allowedRanks: Object.freeze(["primary", "secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"])
        }),
        "command-row": Object.freeze({
          generatedParts: Object.freeze(["rail", "copy", "field"]),
          allowedKinds: Object.freeze(["work", "signal"]),
          allowedFlows: Object.freeze(["forward", "reverse"]),
          allowedRanks: Object.freeze(["secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"])
        }),
        "proof-surface": Object.freeze({
          generatedParts: Object.freeze(["rail", "copy", "meta", "field"]),
          allowedKinds: Object.freeze(["proof", "signal", "work"]),
          allowedFlows: Object.freeze(["forward", "reverse", "split"]),
          allowedRanks: Object.freeze(["primary", "secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"])
        }),
        "smart-region": Object.freeze({
          generatedParts: Object.freeze(["rail", "meta", "field"]),
          allowedKinds: Object.freeze(["article", "signal", "work", "proof"]),
          allowedFlows: Object.freeze(["stack", "forward", "reverse", "split"]),
          allowedRanks: Object.freeze(["primary", "secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"])
        })
      });

      const defaultSource = `<section
  data-mc="panel"
  data-mc-kind="signal"
  data-mc-flow="reverse"
  data-mc-rank="primary"
  data-mc-state="live"
  data-mc-density="auto"
  data-mc-words="argument stream volatile public"
>
  <h2>Crank Files</h2>
  <p>Notes, arguments, and public signals.</p>
</section>

<section
  id="runtime-proof"
  data-mc="panel"
  data-mc-kind="work"
  data-mc-flow="forward"
  data-mc-rank="secondary"
  data-mc-state="idle"
  data-mc-density="calm"
  data-mc-words="compiler serializer repair"
>
  <h2>Runtime Proof</h2>
  <p>The generated rail, copy lane, metadata, and field are disposable runtime parts.</p>
</section>`;

      const blockTemplates = Object.freeze({
        panel: `<section data-mc="panel" data-mc-kind="signal" data-mc-flow="forward" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="auto" data-mc-words="semantic source html">
  <h2>New Panel</h2>
  <p>Clean MCEL source that can compile into runtime structure.</p>
</section>`,
        hero: `<section data-mc="panel" data-mc-kind="hero" data-mc-flow="split" data-mc-rank="primary" data-mc-state="live" data-mc-density="calm" data-mc-words="hero proof surface">
  <h2>Hero Panel</h2>
  <p>A semantic hero panel without generated wrapper pollution.</p>
</section>`,
        signal: `<section data-mc="panel" data-mc-kind="signal" data-mc-flow="reverse" data-mc-rank="primary" data-mc-state="live" data-mc-density="dense" data-mc-words="signal argument stream public">
  <h2>Signal Panel</h2>
  <p>A live signal block for public argument streams.</p>
</section>`,
        work: `<section data-mc="panel" data-mc-kind="work" data-mc-flow="forward" data-mc-rank="secondary" data-mc-state="idle" data-mc-density="calm" data-mc-words="work compiler module">
  <h2>Work Panel</h2>
  <p>A work block for implementation progress.</p>
</section>`,
        feed: `<section data-mc="feed" data-mc-kind="signal" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="live" data-mc-density="dense" data-mc-words="feed stream items">
  <h2>Feed</h2>
  <p>Container source for future feed item intelligence.</p>
</section>`,
        "command-row": `<section data-mc="command-row" data-mc-kind="work" data-mc-flow="forward" data-mc-rank="minor" data-mc-state="idle" data-mc-density="compressed" data-mc-words="command action controls">
  <h2>Command Row</h2>
  <p>Semantic command strip source.</p>
</section>`,
        proof: `<section data-mc="proof-surface" data-mc-kind="proof" data-mc-flow="split" data-mc-rank="primary" data-mc-state="live" data-mc-density="auto" data-mc-words="proof verification serializer">
  <h2>Proof Surface</h2>
  <p>Runtime and serializer claims should be proven here.</p>
</section>`,
        "smart-region": `<section data-mc="smart-region" data-mc-kind="article" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="auto" data-mc-words="region neighborhood a11y">
  <h2>Smart Region</h2>
  <p>A general semantic region for neighborhood and accessibility tests.</p>
</section>`
      });

      return Object.freeze({
        attributes,
        modes,
        defaults,
        runtimeOwnedAttributes,
        runtimeOwnedClasses,
        schema,
        defaultSource,
        blockTemplates
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabContract = McelLabContract;
    }
