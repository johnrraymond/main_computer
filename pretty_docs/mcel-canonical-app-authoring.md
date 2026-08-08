# Canonical MCEL app authoring

Calculator is the canonical real MCEL app in this repository.

Use `mcel_apps/calculator/application.js` to understand how new real MCEL apps
should be authored today. It uses the shared `mcel.defineApp` host-bound
authoring surface and declares app meaning through presentation, state, intents,
capabilities, scenarios, invariants, layout, and proof.

Counter and Workbench are valid MCEL reference fixtures, but they are not the
canonical new-app authoring examples.

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

## Do not learn app authoring from Counter

`mcel_apps/contract-counter/` is an explicit-package reference fixture. It exists
to test legacy import, generated contract projection, compatibility, evidence,
IR-native proof, promotion rehearsal, and promotion execution.

It is useful for MCEL internals. It is a bad starting point for a new real app.

## Do not learn app authoring from Workbench

`mcel_apps/contract-workbench/` is a profiled-package / authoring reference
fixture. It exists to test projection profiles, constrained-expression coverage,
rich evidence, IR-native proof, promotion rehearsal, and idempotent promotion
execution.

It is useful for MCEL internals. It is a bad starting point for a new real app.

## Rule of thumb

Use these files for these purposes:

```text
mcel_apps/calculator/application.js
  canonical modern MCEL app authoring example

mcel_apps/contract-counter/
  explicit-package compatibility/projection/proof fixture

mcel_apps/contract-workbench/
  profiled-package/projection-profile/proof fixture
```
