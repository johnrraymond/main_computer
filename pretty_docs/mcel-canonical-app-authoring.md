# Canonical MCEL app authoring

Calculator is the canonical real MCEL app in this repository.

Use `mcel_apps/calculator/application.js` to understand the stable host-bound
shape for a real app: presentation binding, state, intents, capabilities,
scenarios, invariants, and proof. Use `mcel_apps/code-editor/application.js` to
understand the current static semantic/layout surface declaration pattern for a
host-bound workbench app. A new or ported app that targets semantic-runtime
conformance must combine both lessons instead of postponing semantic-surface and
layout-grammar declarations to a later backfill.

Counter and Workbench are valid MCEL reference fixtures, but they are not the
canonical new-app authoring examples. They are also not product migration
targets: future test upgrades should remove bespoke dependence on these
fixtures where a generic DSL/package/conformance harness can prove the same
platform behavior.

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

## Do not learn app authoring from Counter

`mcel_apps/contract-counter/` is an explicit-package reference fixture. It exists
to test legacy import, generated contract projection, compatibility, evidence,
IR-native proof, promotion rehearsal, and promotion execution.

It is useful for MCEL internals. It is a bad starting point for a new real app.
Do not spend product-migration effort making Contract Counter look like the
modern app examples solely to silence fixture debt. Upgrade tests toward the
generic application harness, generated template, or a real promoted app instead.

## Do not learn app authoring from Workbench

`mcel_apps/contract-workbench/` is a profiled-package / authoring reference
fixture. It exists to test projection profiles, constrained-expression coverage,
rich evidence, IR-native proof, promotion rehearsal, and idempotent promotion
execution.

It is useful for MCEL internals. It is a bad starting point for a new real app.
Do not spend product-migration effort making Contract Workbench look like the
modern app examples solely to silence fixture debt. Upgrade tests toward the
generic application harness, generated template, or a real promoted app instead.

## Rule of thumb

Use these files for these purposes:

```text
mcel_apps/calculator/application.js
  canonical modern host-bound behavior, state, intent, and facade example

mcel_apps/code-editor/application.js
  host-bound static semanticSurface/layoutGrammar bundle declaration example

mcel_apps/contract-counter/
  explicit-package compatibility/projection/proof fixture

mcel_apps/contract-workbench/
  profiled-package/projection-profile/proof fixture
```


## Fixture retirement target

Contract Counter and Contract Workbench are scaffolding/projection/proof
fixtures. They may remain in the repository while they protect compatibility
edges, but the long-term test shape should not require either fixture app by
name. When a fixture-backed test becomes platform-generic, prefer to move the
assertion to a generated package, a reusable DSL/package conformance harness, or
a real app with product value. Fixture-specific assertions should remain only
where the fixture's tiny domain is the thing being tested.
