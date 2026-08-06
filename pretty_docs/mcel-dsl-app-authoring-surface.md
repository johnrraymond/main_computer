# MCEL DSL App Authoring Surface

## Current contract

This document is the current generic authoring contract for applications written with `mcel.dsl.v1`.
It describes what app authors edit, what MCEL derives, how browser/runtime surfaces are bound, and what must stay out of source control.

The short form is:

```text
durable authored app inputs
→ canonical MCEL Application IR
→ logical package projection
→ virtual browser/runtime assets
→ acceptance, observation, proof, rehearsal, and promotion
```

Compilation is not promotion. A compiled app can remain a candidate or shadow until its acceptance, browser observation, IR-native proof, repository binding, promotion rehearsal, and rollback evidence are fresh.

## Durable authored files

An MCEL DSL application keeps its durable source under `mcel_apps/<app-id>/`:

```text
mcel_apps/<app-id>/application.js
mcel_apps/<app-id>/mcel.app.json
mcel_apps/<app-id>/blueprint.json
mcel_apps/<app-id>/requirements.md
mcel_apps/<app-id>/tests/
```

Those files define the authoring surface.

`application.js` is the semantic source. It declares application identity, state, intents, transitions, effects, capabilities, acceptance scenarios, and proof-relevant relationships in the official vanilla JavaScript DSL.

`mcel.app.json` is the package manifest. It names the authoring status, package schema, contract paths, projection profile, presentation mode, evidence records, and promotion state. It may name generated contract paths as logical package paths, but those paths do not require checked-in files.

`blueprint.json` records product identity and framing used by package discovery and authoring tools.

`requirements.md` records the app-local product, safety, and evidence contract in the repository requirements language.

`tests/` contains authored package-local tests and acceptance bindings. It must not contain copied generated contracts or copied runtime packages.

## Derived files are not source

The following files are logical package outputs. They are reconstructed from the authored source and compact projection profile when needed:

```text
mcel.generated.json
generated/mcel.application.normalized.json
contracts/domain.js
contracts/intents.js
contracts/adapter.js
contracts/surface.js
contracts/layout.js
contracts/observation.js
contracts/acceptance.js
mcel.runtime.json
browser package catalog entries
candidate packages
proof input material
promotion plans and reports
```

These may exist in memory or beneath ignored disposable runtime/candidate/report locations. They are not durable app source and must not be checked in beside `application.js`.

A clean application package should not include:

```text
mcel_apps/<app-id>/contracts/
mcel_apps/<app-id>/generated/
mcel_apps/<app-id>/mcel.generated.json
main_computer/web/applications/mcel-packages/
main_computer/web/applications/scripts/mcel-application-package-catalog.js
```

If a physical build is required for inspection, deployment, or a proof harness, it belongs under ignored runtime output such as `runtime/build/mcel/...`.

## Presentation modes

An MCEL DSL app has one of two runtime presentation modes.

### `package-document`

Use `package-document` when the MCEL package owns its browser document.

```text
mcel_apps/<app-id>/src/index.html
mcel_apps/<app-id>/src/app.js
mcel_apps/<app-id>/src/app.css
```

The package document is authored source. Generated contracts remain derived. Counter and Workbench are examples of this style.

A `package-document` application appears in the MCEL package catalog as a browser-mountable package with its own package document and projected contract files.

### `host-bound`

Use `host-bound` when an existing Main Computer route and HTML/CSS remain the presentation authority.

```text
hostRoute: /applications/<route>
rootSelector: #existing-root
runtimeFacade: SomeStableRuntimeFacade
presentationAuthority: existing-host-html
```

The generated MCEL adapter mounts onto the existing root and delegates execution through the stable runtime facade. The authored HTML, CSS, route, selectors, and backend API contracts remain stable integration surfaces.

A host-bound application must not copy the existing HTML/CSS into `mcel_apps/<app-id>/src/`. It publishes a virtual runtime manifest and generated contracts, but it remains one visible application: the existing route with the generated MCEL layer attached.

Calculator is the reference host-bound application:

```text
hostRoute: /applications/calculator
rootSelector: #calculator-app
runtimeFacade: MainComputerCalculatorRuntime
authoring.status: dsl-authoritative
conformance.currentMode: semantic-runtime-proven
```

## Runtime facade

The runtime facade is the compatibility seam between generated MCEL semantics and existing browser behavior.

For a host-bound app, the facade should expose stable operations that map directly to DSL intents. It may manipulate DOM, local state, canvas, or explicit backend capabilities, but each effect must be declared and accounted for by the DSL and proof layers.

The facade is not semantic authority after promotion. It is execution plumbing. The DSL and generated adapter define the semantic meaning, while the facade preserves stable integration with the existing UI.

## Capabilities and effects

Local deterministic work must remain distinct from explicit capabilities.

Examples of local deterministic lanes:

```text
arithmetic parser/evaluator
graph parser/evaluator
mode switches
clear/reset UI actions
pure derived state
```

Examples of explicit capability lanes:

```text
model assistance
symbolic backend evaluation
result Q&A
streamed operations
filesystem, repository, or process effects
```

Every consequential effect needs declared ownership, runtime evidence, terminal disposition, and proof accounting before a promoted app can claim semantic-runtime proof.

## Evidence gates

The normal path from authored source to promoted app is:

```text
compile DSL
validate canonical IR
project logical package
run package acceptance
run browser observation
run IR-native proof
bind evidence to repository source
rehearse promotion
prove exact rollback
execute promotion
```

Each gate has a separate job:

| Gate | Purpose |
| --- | --- |
| DSL compilation | Build canonical IR from `application.js`. |
| Projection | Reconstruct generated contracts and runtime manifest. |
| Acceptance | Check app-declared contracts and scenario bindings. |
| Browser observation | Prove the projected app actually mounts and behaves in the browser. |
| IR-native proof | Close intent, effect, capability, and invariant accounting. |
| Candidate evidence | Bind compile/projection/acceptance/observation/proof to the same source. |
| Promotion rehearsal | Apply the planned authority change in a temporary workspace and roll it back exactly. |
| Promotion | Change live authority only after the rehearsal evidence is fresh. |

## Authority states

Use these terms consistently:

| State | Meaning |
| --- | --- |
| `legacy` | The app is not yet DSL-authoritative. |
| `dsl-shadow` | The DSL compiles and may mount for comparison, but live semantic authority has not moved. |
| `dsl-authoritative` | The DSL and generated adapter are the semantic authority for the app. |
| `semantic-runtime-proven` | The browser/runtime behavior has fresh evidence and closed proof accounting. |

An app may be browser-addressable while still `dsl-shadow`. Do not call it promoted until its manifest, evidence, and promotion state say so.

## Current examples

| App | Presentation mode | Authority | Notes |
| --- | --- | --- | --- |
| `contract-counter` | `package-document` | `dsl-authoritative` | Generated contracts and browser package are logical/virtual outputs. |
| `contract-workbench` | `package-document` | `dsl-authoritative` | Deterministic projector replaces the former checked-in compatibility snapshot. |
| `calculator` | `host-bound` | `dsl-authoritative` | Existing Calculator HTML/CSS remain presentation authority; generated adapter mounts onto `#calculator-app`. |

## Authoring checklist

For a new app or app rewrite, the authoring surface is complete when:

```text
application.js declares all stable states, intents, effects, capabilities, scenarios, and proof obligations
mcel.app.json names the authoring status, presentation mode, projection profile, contracts, and evidence
blueprint.json records the app identity and product framing
requirements.md records the app-local contract
existing HTML/CSS/route/facade are named when using host-bound mode
generated contracts and normalized IR are not checked in
acceptance, browser observation, IR-native proof, candidate evidence, promotion rehearsal, and rollback are executable
normal viewport mounting does not create source-tree files
```

