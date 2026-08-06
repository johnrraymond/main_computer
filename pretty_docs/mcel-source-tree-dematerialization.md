# MCEL Source-Tree De-Materialization

## Rule

A promoted MCEL DSL application has one durable authored representation under
`mcel_apps/<app>/`: its authoritative `application.js`, package metadata,
requirements, and authored tests. It may also bind to durable presentation files
outside `mcel_apps` when the app uses host-bound mode.

Generated compatibility contracts, normalized application records, browser
packages, catalogs, candidates, proof inputs, proof reports, and promotion
reports are not application source.

## Logical packages

Package discovery compiles a `dsl-authoritative` application and overlays its
deterministic generated files in memory. Validation, package fingerprinting,
runtime projection, acceptance, observation, proof, compatibility, and promotion
consume that logical package.

If stale generated files are present beside the source, package discovery
excludes them so they cannot regain authority:

```text
mcel_apps/<app>/contracts/
mcel_apps/<app>/generated/
mcel_apps/<app>/mcel.generated.json
```

The logical package still exposes the compatibility paths expected by the
runtime and proof tools:

```text
contracts/domain.js
contracts/intents.js
contracts/adapter.js
contracts/surface.js
contracts/layout.js
contracts/acceptance.js
contracts/observation.js
mcel.generated.json
generated/mcel.application.normalized.json   # only where the app's profile exposes it
mcel.runtime.json
```

These bytes are computed outputs, not durable application source.

## Presentation modes

The current generic authoring surface is defined in
`pretty_docs/mcel-dsl-app-authoring-surface.md`. Source-tree
de-materialization applies to both supported runtime modes.

### Package-document apps

`contract-counter` and `contract-workbench` are package-document apps. Their
authored package may include durable browser document files where those files
are actual source, but their generated contracts, normalized records, browser
catalog entries, and proof material remain logical outputs.

### Host-bound apps

`calculator` is a host-bound app. Its durable presentation source remains:

```text
main_computer/web/applications/apps/calculator.html
main_computer/web/applications/styles/calculator.css
main_computer/web/applications/scripts/calculator.js
main_computer/web/applications/scripts/calculator-core.js
```

Its durable semantic source is:

```text
mcel_apps/calculator/application.js
mcel_apps/calculator/mcel.app.json
mcel_apps/calculator/blueprint.json
mcel_apps/calculator/requirements.md
```

The generated Calculator adapter mounts onto `/applications/calculator` and
`#calculator-app` through `MainComputerCalculatorRuntime`. No Calculator
`src/` package document, copied HTML/CSS, generated contracts, or normalized
IR snapshots are checked into `mcel_apps/calculator`.

## Runtime build output

The explicit physical browser build remains available for proof, inspection,
and deployment checks. It writes beneath ignored runtime output:

```text
runtime/build/mcel/web/applications/mcel-packages/
runtime/build/mcel/web/applications/scripts/mcel-application-package-catalog.js
```

Normal viewport mounting uses virtual assets and does not call the physical
build helper. The physical build can be deleted and recreated without changing
the source tree.

## Evidence roots

Compiler candidates, acceptance reports, browser observation, app proof,
promotion rehearsal, and promotion execution reports remain evidence, not
source. They belong beneath ignored runtime report/state locations such as:

```text
runtime/reports/
runtime/state/
runtime/build/
```

## Current de-materialized app boundary

The current promoted DSL apps are:

```text
contract-counter
contract-workbench
calculator
```

All three reconstruct generated contracts from authored source and compact
projection code. Calculator additionally demonstrates the host-bound pattern:
the existing HTML/CSS are stable presentation authority, while the DSL and
generated host-bound adapter are semantic authority.

A clean checkout should contain no generated contract tree under any promoted app package and no checked-in browser projection under
`main_computer/web/applications/mcel-packages/`.

