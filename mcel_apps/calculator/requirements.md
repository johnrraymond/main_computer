# Calculator Shadow MCEL Authority

This package is an authored-only shadow authority for the existing Calculator.
The durable presentation remains `main_computer/web/applications/apps/calculator.html`,
the stable root remains `#calculator-app`, and the live compatibility facade remains
`window.MainComputerCalculatorRuntime`. Generated contracts and normalized IR are
reconstructed in memory and are not checked into this package.

The detailed product and safety requirements remain in
`pretty_docs/mcel-calculator-requirements.md`.

```mcel-app
id: calculator
title: Calculator
status: shadow-specified
current_runtime_status: legacy-html-runtime
target_runtime_status: semantic-runtime-proven
dominant_object: Calculator expression and result context
primary_user_goal: Perform deterministic local calculation and bounded helper operations through stable HTML interfaces.
current_sources:
  - application.js
  - main_computer/web/applications/apps/calculator.html
  - main_computer/web/applications/styles/calculator.css
  - main_computer/web/applications/scripts/calculator.js
verification:
  - DSL compilation
  - deterministic contract projection
  - package-local shadow acceptance
```

```mcel-use-case
id: calculator.use-case.compute
app: calculator
status: specified
type: primary
primary_object: Calculator expression
user_goal: Evaluate arithmetic, graph, and symbolic expressions while preserving explicit helper boundaries.
acceptance: Local arithmetic and graph operations remain deterministic; provider and Mathics operations remain explicit capabilities.
```

```mcel-region
id: calculator.region.workspace
app: calculator
status: existing-host
region: Calculator workspace
role: primary
responsibility: Preserve the existing Calculator HTML, route, root selector, controls, results, and chat integration.
```

```mcel-requirement
id: calculator.requirement.stable-host
app: calculator
status: specified
type: architecture
aspect: source
object: Calculator presentation
requirement: The existing HTML and CSS remain presentation authority while MCEL semantics are compiled and projected in shadow.
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
status: shadow-specified
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
status: specified
requires:
  - application.js compiles to valid canonical IR
  - exactly eleven stable runtime intents are declared
  - model assistance, Mathics, and result Q&A are explicit capabilities
  - generated projection is deterministic
  - /applications/calculator and #calculator-app remain the host interfaces
  - normal viewport assembly loads calculator-core.js before calculator.js
```

```mcel-finding
id: calculator.finding.shadow-gaps
app: calculator
status: open
aspect: implementation
severity: info
problem: The shadow authority is not yet the live host-bound runtime projection and the handwritten semantic adapter remains active.
desired_behavior: Close browser observation, host-bound projection, promotion rehearsal, and legacy adapter retirement only after parity proof.
```
