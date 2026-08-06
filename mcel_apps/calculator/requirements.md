# Calculator Host-Bound DSL Authority

This package is the authored MCEL semantic authority for Calculator.

The durable presentation remains the existing host surface:

```text
main_computer/web/applications/apps/calculator.html
main_computer/web/applications/styles/calculator.css
main_computer/web/applications/scripts/calculator.js
main_computer/web/applications/scripts/calculator-core.js
```

The stable route/root/facade remain:

```text
/applications/calculator
#calculator-app
window.MainComputerCalculatorRuntime
```

Generated contracts, normalized IR, runtime manifests, browser package entries,
candidate evidence, and promotion reports are reconstructed from this package
and compact projection code. They are not checked into `mcel_apps/calculator`.

The generic authoring-surface contract is documented in
`pretty_docs/mcel-dsl-app-authoring-surface.md`. The detailed Calculator product
and safety requirements remain in `pretty_docs/mcel-calculator-requirements.md`.

```mcel-app
id: calculator
title: Calculator
status: semantic-runtime-proven
current_runtime_status: dsl-authoritative-host-bound
target_runtime_status: semantic-runtime-proven
dominant_object: Calculator expression and result context
primary_user_goal: Perform deterministic local calculation and bounded helper operations through stable HTML interfaces.
current_sources:
  - application.js
  - mcel.app.json
  - blueprint.json
  - requirements.md
  - main_computer/web/applications/apps/calculator.html
  - main_computer/web/applications/styles/calculator.css
  - main_computer/web/applications/scripts/calculator-core.js
  - main_computer/web/applications/scripts/calculator.js
verification:
  - DSL compilation
  - deterministic contract projection
  - package-local acceptance
  - host-bound virtual runtime mount
  - generated-adapter browser parity
  - IR-native proof
  - promotion rehearsal with exact rollback
  - authority finalization
```

```mcel-use-case
id: calculator.use-case.compute
app: calculator
status: verified
type: primary
primary_object: Calculator expression
user_goal: Evaluate arithmetic, graph, and symbolic expressions while preserving explicit helper boundaries.
acceptance: Local arithmetic and graph operations remain deterministic; provider and Mathics operations remain explicit capabilities.
```

```mcel-region
id: calculator.region.workspace
app: calculator
status: verified
region: Calculator workspace
role: primary
responsibility: Preserve the existing Calculator HTML, route, root selector, controls, results, and chat integration while the generated MCEL adapter supplies semantic authority.
```

```mcel-requirement
id: calculator.requirement.stable-host
app: calculator
status: verified
type: architecture
aspect: source
object: Calculator presentation
requirement: The existing HTML and CSS remain presentation authority while MCEL semantics are compiled, projected in memory, and mounted through the stable runtime facade.
acceptance: No Calculator HTML, CSS, generated contracts, or normalized IR snapshot is copied into the authored package.
```

```mcel-requirement
id: calculator.requirement.deterministic-local-core
app: calculator
status: verified
type: safety
aspect: actions
object: Local Calculator compute
requirement: Arithmetic and graph evaluation use the bounded deterministic Calculator core rather than eval or Function construction.
acceptance: Unsupported identifiers and arbitrary JavaScript are refused before execution.
```

```mcel-intent
id: calculator.intent.runtime-facade
app: calculator
status: verified
intent: eleven stable Calculator operations
risk: mixed
requires:
  - MainComputerCalculatorRuntime
  - explicit local or capability lane
produces:
  - one classified operation result
  - no claimed canonical write
```

```mcel-acceptance
id: calculator.acceptance.shadow-authority
app: calculator
status: verified
requires:
  - application.js compiles to valid canonical IR
  - exactly eleven stable runtime intents are declared
  - model assistance, Mathics, and result Q&A are explicit capabilities
  - generated projection is deterministic
  - /applications/calculator and #calculator-app remain the host interfaces
  - normal viewport assembly loads calculator-core.js before calculator.js
  - the virtual package catalog exposes one host-bound Calculator record
  - the generated adapter invokes all declared operations through MainComputerCalculatorRuntime
  - browser parity evidence observes all intents
  - promotion rehearsal restores the pre-promotion package exactly
```

```mcel-finding
id: calculator.finding.authority-finalized
app: calculator
status: closed
aspect: implementation
severity: info
problem: The former shadow and handwritten semantic-adapter bridge has been retired.
desired_behavior: Keep Calculator DSL-authoritative, host-bound, browser-observed, and free of checked-in generated artifacts.
```

