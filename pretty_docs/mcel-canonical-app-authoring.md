# Canonical MCEL app authoring

Calculator is the canonical real MCEL app in this repository.

Use `mcel_apps/calculator/application.js` to understand the stable host-bound
shape for a real app: presentation binding, state, intents, capabilities,
scenarios, invariants, and proof. Use `mcel_apps/code-editor/application.js` to
understand the current static semantic/layout surface declaration pattern for a
host-bound workbench app. A new or ported app that targets semantic-runtime
conformance must combine both lessons instead of postponing semantic-surface and
layout-grammar declarations to a later backfill.

Counter and Workbench were retired as live repository reference fixtures after
their generic invariants moved to reusable DSL/package/conformance harnesses.
They are historical compatibility examples only, not product-migration targets
and not canonical new-app authoring examples.

## Use Calculator for new app authoring

A new real MCEL app should look like Calculator:

```js
module.exports = mcel.defineApp(
  {
    id: "my-app",
    title: "My App",
    semanticVersion: "1",
    targetTruthStatus: "semantic-runtime-proven",
  },
  ({application}) => application.hostBound((app) => {
    app.presentation.hostBound("workspace", {
      route: "/applications/my-app",
      root: "#my-app",
      presentationAuthority: "existing-host-html",
      runtimeFacade: "MainComputerMyAppRuntime",
    });

    app.presentation.semanticSurface({
      id: "my-app.semantic-surface",
      surfaceId: "my-app.primary-surface",
      presentationAuthority: "existing-host-html",
      runtimeFacade: "MainComputerMyAppRuntime",
      regions: [
        {id: "root", role: "application-root", selector: "#my-app"},
        {id: "primary-surface", role: "primary-work-surface", selector: "#my-app-main", primary: true},
      ],
      controls: [
        {id: "do-thing-control", selector: "[data-mcel-intent='do-thing']", intent: "doThing"},
      ],
      forbiddenDefaultRegions: [],
    });
    app.layout.grammar({
      id: "my-app.layout",
      rootSelector: "#my-app",
      regions: [
        {id: "root", selector: "#my-app", layout: "grid", children: ["primary-surface"]},
        {id: "primary-surface", selector: "#my-app-main"},
      ],
      constraints: [
        {id: "primary-surface-nonzero", selector: "#my-app-main", minWidth: 320, minHeight: 240},
      ],
    });

    app.state.rendererLocal("mode", app.field.string(), {initial: "default"});
    app.intent.interaction("do-thing", {
      runtimeMethod: "doThing",
      binding: "do-thing",
      lane: "local-ui",
      reads: ["mode"],
      writes: ["mode"],
    });
    app.scenario.example("does-the-thing", {
      intent: "do-thing",
      expect: {ok: true},
    });
    app.invariant.semantic("declared-behavior-stays-owned", {
      description: "The runtime shell binds declared behavior; it does not own app semantics.",
    });
    app.proof.semanticRuntimeProven();
  })
);
```

## Static surface declarations are part of authoring

The Code Editor backfill established the rule for future work: semantic-runtime
authoring includes static `app.presentation.semanticSurface(...)` and
`app.layout.grammar(...)` declarations before registry promotion. Those
declarations must name stable regions, controls, intent bindings, selectors,
primary surfaces, hidden/default-forbidden regions, and layout constraints in
the DSL source. The generated `mcel.application-surface-bundle.v1` is then
package/catalog output, not a hand-written file.

Use Code Editor's DSL surface declarations as the host-bound reference for this
part of the app authoring shape until the scaffold template produces the same
declarations for every new app.

## Do not learn app authoring from retired fixtures

Contract Counter and Contract Workbench were scaffolding/projection/proof
fixtures. Their checked-in package directories have been retired from the active
repository after their generic invariants moved to generated-template checks,
the reusable DSL/package harness, and real promoted apps.

They remain useful as historical compatibility vocabulary in older design notes,
but they must not be recreated as new-app examples or product-migration targets.
If a future compatibility edge needs a tiny reference fixture, it should be
introduced with a narrow test-only contract and should not become an app
authoring pattern.

## Rule of thumb

Use these files for these purposes:

```text
mcel_apps/calculator/application.js
  canonical modern host-bound behavior, state, intent, and facade example

mcel_apps/code-editor/application.js
  host-bound static semanticSurface/layoutGrammar bundle declaration example

former Contract Counter / Contract Workbench fixture paths
  historical compatibility vocabulary only; do not recreate them for new app authoring
```


## Fixture retirement status

Contract Counter and Contract Workbench are retired as live repository packages.
The active test shape should not require either fixture app by name. historical
fixture names may remain only in compatibility documentation, archived
migration notes, or tests that explicitly prove a former compatibility boundary.
Generic platform invariants belong in generated packages, the reusable
DSL/package conformance harness, or real apps with product value.
