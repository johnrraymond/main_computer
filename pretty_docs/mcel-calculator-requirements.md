# MCEL Calculator Requirements

## Status

This is the documentation-first requirements contract for the Calculator app.

The current implementation already has a calculator route, a rich calculator DOM, arithmetic input/result controls, scientific graphing controls, a graph canvas, Mathics symbolic panels, result Q&A, an embedded calculator chat panel, a calculator MCEL domain pack, and a planner entry that marks Calculator as domain-ready. It does **not** yet have a dedicated Calculator semantic adapter registered with the MCEL domain-adapter registry.

So this document must be read as:

```text
current: domain-ready calculator planner + domain pack
planned: full Calculator semantic runtime for deterministic local compute, graphing, symbolic evaluation, result explanation, and layout ownership
```

The purpose of this document is to make Calculator requirements stable enough that MCEL Lab can later parse them, compare them with the live app, generate finding candidates, and drive code/test updates without relying on loose prose. Calculator should be the small reference app for proving a complete MCEL semantic runtime because its core workflows are deterministic, low-risk, and easy to verify.

## Roadmap use case: compare monthly costs

This use case is the roadmap for the rest of this document. It forces Calculator to behave like a complete mathematical workbench instead of a single expression box, while keeping the app bounded, deterministic, and safe.

A user wants to compare two pricing options:

```text
Option A = $18/month flat
Option B = $10/month + $0.08 per use
```

The Calculator should help the user find the break-even point, compare values at a few usage levels, plot both formulas, and explain the result in plain language. The deterministic calculation remains authoritative; model help may explain the result but must not change it.

```mcel-use-case
id: calculator.use-case.compare-monthly-costs
app: calculator
status: draft
type: roadmap-use-case
primary_object: CalculationScenario
user_goal: >
  Compare two monthly pricing formulas, identify the break-even point, inspect
  sample values, plot the relationship, and explain the result without leaving
  Calculator.
scenario:
  option_a: "A(x) = 18"
  option_b: "B(x) = 10 + 0.08x"
  break_even_equation: "18 = 10 + 0.08x"
  expected_break_even: "x = 100"
requires:
  - named formulas or a visible comparison workspace
  - deterministic expression evaluation
  - sample-value comparison for x = 25, 50, 100, and 200
  - graphing both formulas on the same canvas
  - result history or comparison evidence
  - plain-language explanation tied to the deterministic result
  - parse/evaluation status visible near the result
acceptance:
  - The break-even calculation returns x = 100.
  - The comparison shows Option B cheaper below 100 uses.
  - The comparison shows both options equal at 100 uses.
  - The comparison shows Option A cheaper above 100 uses.
  - The graph shows both formulas crossing at x = 100.
  - The explanation matches the deterministic result.
  - The explanation cannot override or silently change the computed answer.
  - No files, Git state, remotes, packages, or shell commands are changed.
layout_implications:
  - expression/comparison workspace must remain visually primary
  - graph canvas must be owned output, not an unrelated decoration
  - explanation must sit near result evidence but remain secondary
  - history/comparison state must be visible enough to support reasoning
  - advanced symbolic/model lanes must not obscure the core calculation path
```

```mcel-app
id: calculator
title: Calculator
status: specified
current_runtime_status: domain-ready-planner-plus-domain-pack
current_semantic_runtime_scope: none
target_runtime_status: full-application-semantic-runtime
dominant_object: CalculationSession
primary_user_goal: >
  Enter arithmetic expressions, inspect results, draw graphs, run explicit
  symbolic evaluations, and ask contextual questions without hidden filesystem,
  remote-sync, or command-execution side effects.
current_sources:
  - main_computer/web/applications/apps/calculator.html
  - main_computer/web/applications/scripts/calculator.js
  - main_computer/web/applications/scripts/dom-bindings/calculator.js
  - main_computer/web/applications/scripts/mcel-supercut-packs-calculator.js
  - main_computer/web/applications/styles/calculator.css
  - main_computer/web/applications/scripts/mcel-specimen-planner.js
planned_adapter:
  - main_computer/web/applications/scripts/calculator-semantic-adapter.js
verification:
  - tests/test_viewport_app_routes.py
  - tests/test_viewport_applications_static_core.py
  - tests/test_mcel_documentation.py
```

## Product law

Calculator is not a terminal, notebook, or hidden code runner. It is a deterministic local compute surface with explicit helper lanes.

Its core law is:

```text
Arithmetic and graphing are local and deterministic.
Model help proposes expressions or explanations; it does not silently mutate state.
Mathics symbolic evaluation is an explicit user action with bounded receipt.
No calculator save, graph, ask, or evaluate action writes project files, pushes remotes, installs packages, or runs arbitrary commands.
```

```mcel-requirement
id: calculator.compute.local-deterministic
app: calculator
status: specified
type: product-law
aspect: compute
object: CalculationSession
requirement: >
  Basic arithmetic and graphing must be treated as local deterministic
  calculations. The same expression and range inputs should produce the same
  result or the same validation error without hidden network, file, or remote
  mutation side effects.
current_state: >
  The current frontend has local arithmetic evaluation and local graph rendering
  controls. Model and Mathics helper lanes are separate explicit actions.
acceptance:
  - Basic expression evaluation works without contacting a model provider.
  - Graph drawing works from the expression and axis-range fields.
  - Invalid expressions show a visible error instead of mutating unrelated UI state.
  - Repeating the same valid expression produces the same displayed result.
  - Local evaluate and draw actions do not write files, push remotes, or run shell commands.
```

```mcel-requirement
id: calculator.expression.sanitized-parser
app: calculator
status: specified
type: safety-law
aspect: compute
object: ArithmeticExpression
requirement: >
  Arithmetic expression evaluation must accept only the documented calculator
  expression grammar. The target implementation should be parser-owned rather
  than general JavaScript execution, even when the current sanitizer limits the
  character set before evaluation.
current_state: >
  The current frontend normalizes arithmetic input to digits, operators,
  parentheses, decimal points, percentage, and spaces before evaluating.
target_state: >
  A Calculator semantic adapter should expose the grammar, parse result,
  validation failures, and final numeric result as evidence.
acceptance:
  - Letters and unsupported tokens are rejected or stripped before evaluation.
  - Non-finite results surface a readable error.
  - The parser reports the normalized expression used for the result.
  - Arbitrary JavaScript names, member access, assignment, imports, and command execution are impossible.
```

```mcel-requirement
id: calculator.graph.canvas-owned-output
app: calculator
status: specified
type: layout-law
aspect: layout
object: GraphSurface
requirement: >
  The graph canvas is the primary output surface for graphing mode. It owns its
  drawing area and resize behavior, but it must not become a scroll owner for
  the entire app or hide expression, range, and status controls.
current_state: >
  The live app has calculator-graph-canvas, calculator-graph-expression,
  axis-range fields, graph draw/reset controls, and a graph status surface.
acceptance:
  - The graph canvas remains visible in graphing mode.
  - Axis-range controls remain visually associated with the graph.
  - Graph status remains near the canvas.
  - Graph rendering does not push the mode toolbar or result status offscreen.
  - MCEL Lab can classify the canvas as element.compute.graph-surface.
```

```mcel-requirement
id: calculator.model-help.non-mutating
app: calculator
status: specified
type: safety-law
aspect: actions
object: ModelSuggestion
requirement: >
  Model-assisted word-problem and graph-prompt actions may propose expressions,
  but they must not silently evaluate, persist, send remote sync, or rewrite
  calculator state without the user's visible action boundary.
current_state: >
  The live frontend has explicit Ask model controls for arithmetic prompts and
  graph prompts. The result is used to populate calculator fields.
acceptance:
  - Model ask buttons are visually separate from local evaluate/draw buttons.
  - A failed model response leaves the previous calculation state understandable.
  - Model output is constrained to expression suggestion or explanation context.
  - No model ask action writes files or performs remote Git operations.
```

```mcel-requirement
id: calculator.mathics.explicit-evaluate
app: calculator
status: specified
type: execution-boundary
aspect: actions
object: SymbolicExpression
requirement: >
  Mathics symbolic evaluation is allowed only as an explicit Calculator action
  with visible input, visible status, and visible output or error. It must be
  documented as a separate backend evaluation lane, not confused with local
  arithmetic or arbitrary terminal execution.
current_state: >
  The live app exposes /api/applications/calculator/mathics/ask and
  /api/applications/calculator/mathics/evaluate endpoints, with tests for blank
  input validation and model-assisted expression generation.
acceptance:
  - Blank Mathics expressions are rejected with a validation error.
  - Evaluate Mathics is not triggered by graph draw, basic equals, or Q&A.
  - The output panel shows success, error, and diagnostics clearly.
  - Mathics evaluation has a bounded request/timeout contract.
  - The UI never presents Mathics as unrestricted shell execution.
```

```mcel-requirement
id: calculator.qa.context-readonly
app: calculator
status: specified
type: product-law
aspect: evidence
object: ResultQuestion
requirement: >
  Calculator Q&A may read the current arithmetic, graph, and Mathics context to
  explain results. It must be read-only with respect to calculation state unless
  a separate explicit action copies a suggestion into an input field.
current_state: >
  The live app exposes a calculator result Q&A panel and an endpoint that
  validates blank questions and sends calculator context to the model provider.
acceptance:
  - Blank questions are rejected.
  - Q&A receives current calculator context.
  - Q&A output appears in the Q&A answer region.
  - Q&A does not alter the arithmetic expression, graph expression, or Mathics expression by default.
```

```mcel-requirement
id: calculator.evidence.near-result
app: calculator
status: specified
type: product-law
aspect: evidence
object: CalculationResult
requirement: >
  Every calculation result must have nearby evidence explaining which expression,
  mode, and inputs produced it. Errors must be first-class result states, not
  hidden console-only failures.
current_state: >
  The current app has calculator-result and calculator-graph-status surfaces,
  plus Mathics evaluation status/output surfaces.
acceptance:
  - Basic result status is adjacent to the expression display.
  - Graph status is adjacent to the graph canvas.
  - Mathics status and output are adjacent to the symbolic input.
  - Errors preserve enough context to retry or correct input.
```

```mcel-requirement
id: calculator.no-persistence-side-effects
app: calculator
status: specified
type: safety-law
aspect: actions
object: CalculationSession
requirement: >
  Calculator mode switching, local evaluation, graphing, model prompts, Q&A,
  chat, and Mathics evaluation must not save project files, create checkpoints,
  commit Git history, push remotes, install dependencies, or mutate unrelated
  app state as implicit side effects.
acceptance:
  - No Calculator primary action writes repository files.
  - No Calculator primary action creates Git commits.
  - No Calculator primary action pushes or pulls remotes.
  - Any future export/share/save feature is advanced, explicit, and receipted.
```

```mcel-requirement
id: calculator.layout.primary-surface
app: calculator
status: specified
type: layout-law
aspect: layout
object: CalculatorWorkspace
requirement: >
  Calculator layout should keep the active compute mode visually dominant while
  keeping helper surfaces secondary. Arithmetic, graphing, Mathics, Q&A, and chat
  must not compete as equal primary surfaces at the same time on constrained viewports.
current_state: >
  The live shell contains a mode toolbar, basic arithmetic pane, scientific
  graphing panel, Mathics panel, Q&A panel, and embedded chat panel.
acceptance:
  - Active mode has the clearest primary surface.
  - Helper panels are visually secondary or collapsible when space is limited.
  - Result/status evidence remains visible when helper panels expand.
  - MCEL Lab can infer primary, inspector, evidence, and notebook/helper regions.
```

```mcel-requirement
id: calculator.accessibility.visible-control-names
app: calculator
status: specified
type: accessibility-law
aspect: a11y
object: CalculatorControl
requirement: >
  Calculator controls must expose clear names and roles so MCEL Lab, assistive
  technology, and acceptance tests can identify mode switches, digit/operator
  controls, evaluate actions, graph controls, symbolic actions, and Q&A actions.
current_state: >
  The live DOM includes aria labels, data-mc-widget labels, data-mc-component
  labels, and button text across the calculator surface.
acceptance:
  - Mode buttons have clear labels.
  - Expression inputs have clear labels.
  - Digit and operator buttons are identifiable.
  - Graph draw/reset controls are identifiable.
  - Mathics and Q&A actions are identifiable.
```

## Layout regions

```mcel-region
id: calculator.region.mode-toolbar
app: calculator
status: specified
region: mode-switcher-toolbar
role: mode-switcher
responsibility: >
  Own mode selection between arithmetic and scientific/graphing surfaces without
  evaluating expressions or hiding the user's current calculation context.
layout_zone: actions
object: CalculationMode
contains:
  - Basic mode action
  - Scientific Graphing mode action
layout_laws:
  - remains above mode-specific compute surfaces
  - never triggers hidden evaluation while switching modes
  - preserves the user's current expression state when practical
```

```mcel-region
id: calculator.region.arithmetic-panel
app: calculator
status: specified
region: primary-calculation-surface
role: primary-work-surface
responsibility: >
  Own the ordinary arithmetic workflow by keeping expression input, local
  actions, and deterministic result evidence visually connected.
layout_zone: primary
object: ArithmeticExpression
contains:
  - arithmetic expression display
  - numeric and operator keypad
  - local result status
  - result Q&A entry point
layout_laws:
  - expression, keypad, and result status stay visually connected
  - local equals remains distinct from model ask and Mathics evaluate
  - invalid input reports through the result status
```

```mcel-region
id: calculator.region.expression-display
app: calculator
status: specified
region: expression-input-display
role: input-display
responsibility: >
  Show the current arithmetic expression as authoritative calculator input,
  separate from graph output, Mathics prompts, and model prose.
layout_zone: primary
object: ArithmeticExpression
contains:
  - calculator-display
  - normalized arithmetic expression
layout_laws:
  - represents the current arithmetic input
  - does not display model prose
  - remains readable when the keypad or helper panels expand
```

```mcel-region
id: calculator.region.keypad
app: calculator
status: specified
region: deterministic-input-grid
role: action-grid
responsibility: >
  Provide local digit, operator, edit, and equals actions that mutate only the
  current arithmetic expression and deterministic result state.
layout_zone: actions
object: ArithmeticExpression
contains:
  - digit-entry controls
  - operator-entry controls
  - clear and backspace controls
  - evaluate expression control
layout_laws:
  - digit and operator controls are grouped as local calculator actions
  - equals is the local arithmetic evaluator
  - clear/backspace mutate only the arithmetic expression
```

```mcel-region
id: calculator.region.result-status
app: calculator
status: specified
region: result-evidence-status
role: evidence-status
responsibility: >
  Show success, error, graph, and symbolic evaluation status near the calculator
  surface that produced the evidence.
layout_zone: evidence
object: CalculationResult
contains:
  - calculator-result
  - calculator-graph-status
  - Mathics evaluation status
layout_laws:
  - success and error are both visible result states
  - status remains near the surface that produced it
  - status text is not replaced by unrelated model prose
```

```mcel-region
id: calculator.region.graphing-panel
app: calculator
status: specified
region: graphing-workspace
role: primary-visual-output
responsibility: >
  Own graph expression entry, visible range controls, plotting actions, and
  graph-specific status for deterministic visualization.
layout_zone: primary
object: GraphSurface
contains:
  - graph expression input
  - x/y range inputs
  - graph canvas
  - graph draw/reset controls
  - scientific function keypad
layout_laws:
  - graph expression, range fields, and canvas remain a single unit
  - canvas owns drawing, not app-level scroll
  - graph status remains visible after draw or reset
```

```mcel-region
id: calculator.region.graph-canvas
app: calculator
status: specified
region: primary-visualization-canvas
role: canvas-output
responsibility: >
  Render deterministic graph output for the current expression or comparison
  scenario without becoming an editable source of truth.
layout_zone: primary
object: GraphSurface
contains:
  - calculator-graph-canvas
layout_laws:
  - renders the current graph expression and ranges
  - does not capture unrelated app interactions
  - exposes enough evidence for MCEL to classify it as a graph surface
```

```mcel-region
id: calculator.region.scientific-keypad
app: calculator
status: specified
region: scientific-function-input-grid
role: function-action-grid
responsibility: >
  Provide graph and scientific function tokens as local expression-building
  controls rather than hidden evaluation or model actions.
layout_zone: actions
object: GraphExpression
contains:
  - graph token controls
  - function template controls
  - graph draw/reset actions
layout_laws:
  - inserts graph tokens into the graph expression, not the arithmetic expression
  - function controls are local expression-construction actions
  - draw/reset remain distinct from model graph ask
```

```mcel-region
id: calculator.region.mathics-panel
app: calculator
status: specified
region: symbolic-evaluation-inspector
role: symbolic-inspector
responsibility: >
  Expose explicit symbolic-evaluation prompts, generated expressions, backend
  status, and results without replacing the local arithmetic answer.
layout_zone: inspector
object: SymbolicExpression
contains:
  - Mathics prompt
  - generated Mathics expression
  - evaluate Mathics action
  - symbolic output
  - examples
layout_laws:
  - symbolic evaluation is explicit
  - Mathics output does not replace local arithmetic result
  - diagnostics are shown as evidence, not hidden in the console
```

```mcel-region
id: calculator.region.qa-panel
app: calculator
status: specified
region: result-explanation-inspector
role: result-explainer
responsibility: >
  Let the user ask explanatory questions about current calculator evidence while
  keeping deterministic math output authoritative.
layout_zone: inspector
object: ResultQuestion
contains:
  - result question prompt
  - ask button
  - result answer output
layout_laws:
  - reads current calculator context
  - answers appear in the Q&A answer region
  - does not mutate calculator inputs by default
```

```mcel-region
id: calculator.region.chat-panel
app: calculator
status: specified
region: advanced-helper-companion
role: helper-notebook
responsibility: >
  Host optional calculator conversation support as an advanced companion that
  may explain or suggest but must not silently mutate results.
layout_zone: advanced
object: CalculatorConversation
contains:
  - embedded calculator chat notebook
  - calculator context bridge
layout_laws:
  - chat is secondary to the active calculation surface
  - chat context is scoped to the calculator session
  - chat does not become the primary source of calculator truth
```

## Semantic intents

```mcel-intent
id: calculator.intent.switch-mode
app: calculator
status: specified
intent: switchMode
object: CalculationMode
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
requires:
  - requested mode is supported
produces:
  - active mode changes
  - visible compute surface updates
evidence:
  - previous mode
  - next mode
  - preserved expression state
```

```mcel-intent
id: calculator.intent.enter-token
app: calculator
status: specified
intent: enterToken
object: ArithmeticExpression
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
requires:
  - token belongs to the arithmetic keypad grammar
produces:
  - arithmetic expression display updates
evidence:
  - token
  - previous expression
  - next expression
```

```mcel-intent
id: calculator.intent.clear-expression
app: calculator
status: specified
intent: clearExpression
object: ArithmeticExpression
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
requires:
  - arithmetic panel is mounted
produces:
  - arithmetic expression resets to empty or zero state
  - arithmetic result returns to a ready state
evidence:
  - previous expression
  - reset state
```

```mcel-intent
id: calculator.intent.evaluate-expression
app: calculator
status: specified
intent: evaluateExpression
object: ArithmeticExpression
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
requires:
  - arithmetic expression is present
  - expression passes calculator grammar validation
produces:
  - result status updates with numeric value or validation error
evidence:
  - raw expression
  - normalized expression
  - parse status
  - result or error
```

```mcel-intent
id: calculator.intent.draw-graph
app: calculator
status: specified
intent: drawGraph
object: GraphExpression
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
requires:
  - graph expression is present
  - axis ranges parse to finite bounds
produces:
  - graph canvas redraws
  - graph status updates
evidence:
  - graph expression
  - x range
  - y range
  - draw status
```

```mcel-intent
id: calculator.intent.reset-graph
app: calculator
status: specified
intent: resetGraph
object: GraphSurface
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
requires:
  - graphing panel is mounted
produces:
  - graph expression and ranges return to default graphing state
  - graph canvas redraws or clears according to reset law
evidence:
  - previous graph settings
  - reset graph settings
```

```mcel-intent
id: calculator.intent.ask-model-expression
app: calculator
status: specified
intent: askModelForExpression
object: ModelSuggestion
current_adapter_status: not-registered
target_adapter_status: executable
adapter_boundary: provider-boundary
risk: read-only
requires:
  - user supplied a word problem
  - model provider is available
produces:
  - suggested arithmetic expression is returned
  - arithmetic expression may be populated visibly
evidence:
  - user prompt
  - provider status
  - suggested expression
  - parse validation
```

```mcel-intent
id: calculator.intent.ask-model-graph
app: calculator
status: specified
intent: askModelForGraphExpression
object: ModelSuggestion
current_adapter_status: not-registered
target_adapter_status: executable
adapter_boundary: provider-boundary
risk: read-only
requires:
  - user supplied a graph description
  - model provider is available
produces:
  - suggested graph expression is returned
  - graph expression may be populated visibly
evidence:
  - user prompt
  - provider status
  - suggested graph expression
  - graph grammar validation
```

```mcel-intent
id: calculator.intent.evaluate-mathics
app: calculator
status: specified
intent: evaluateMathics
object: SymbolicExpression
current_adapter_status: not-registered
target_adapter_status: executable
adapter_boundary: backend-boundary
risk: local-state
requires:
  - Mathics expression is non-empty
  - backend evaluator is available or can return diagnostics
produces:
  - Mathics output or error appears in the Mathics output region
evidence:
  - expression
  - timeout
  - result text
  - error and diagnostics when evaluation fails
```

```mcel-intent
id: calculator.intent.ask-qa
app: calculator
status: specified
intent: askResultQuestion
object: ResultQuestion
current_adapter_status: not-registered
target_adapter_status: executable
adapter_boundary: provider-boundary
risk: read-only
requires:
  - user supplied a non-empty question
  - calculator context snapshot is available
produces:
  - answer appears in the Q&A answer region
evidence:
  - question
  - context fields used
  - provider status
  - answer or error
```

## Acceptance and findings

```mcel-acceptance
id: calculator.acceptance.full-semantic-runtime
app: calculator
status: planned
target: fullApplicationSemanticReady
requires:
  - Calculator semantic adapter is registered in the MCEL domain-adapter registry.
  - evaluateExpression, drawGraph, resetGraph, switchMode, enterToken, clearExpression, evaluateMathics, and askResultQuestion have explicit readiness classifications.
  - Runtime state exposes current mode, arithmetic expression, arithmetic result, graph expression, graph ranges, graph status, Mathics status, and Q&A status.
  - Receipts distinguish local deterministic compute, model-provider calls, and bounded backend symbolic evaluation.
  - MCEL truth gate should eventually report fullApplicationSemanticReady for Calculator before Calculator becomes the reference complete app.
```

```mcel-acceptance
id: calculator.acceptance.layout-inference
app: calculator
status: planned
target: mcel-lab-layout-inference
requires:
  - MCEL Lab infers mode-toolbar, arithmetic-panel, keypad, result-status, graphing-panel, graph-canvas, Mathics panel, Q&A panel, and chat panel.
  - Graph canvas is classified as canvas-output/graph-surface.
  - Keypad controls are classified as local safe actions.
  - Model ask and Mathics evaluate controls are classified as explicit helper/backend boundaries.
  - Helper panels do not erase the active primary compute surface.
```

```mcel-acceptance
id: calculator.acceptance.no-hidden-mutation
app: calculator
status: specified
target: safety
requires:
  - Basic evaluate does not use model provider calls.
  - Graph draw does not use model provider calls.
  - Model ask does not write files or push remotes.
  - Mathics evaluate does not present itself as terminal execution.
  - Calculator actions do not create Git commits or revision checkpoints.
```

```mcel-finding
id: calculator.finding.no-domain-adapter
app: calculator
status: open
aspect: semantic-runtime
severity: medium
problem: >
  Calculator has a domain-ready planner entry and calculator MCEL domain pack,
  but no dedicated Calculator semantic adapter is registered with the MCEL
  domain-adapter registry.
desired_behavior: >
  Add a small Calculator semantic adapter that derives state, classifies intents,
  executes deterministic local actions, records receipts, and lets the truth
  gate evaluate fullApplicationSemanticReady.
source_candidates:
  - main_computer/web/applications/scripts/calculator.js
  - main_computer/web/applications/scripts/calculator-semantic-adapter.js
  - main_computer/web/applications/scripts/mcel-domain-adapter-registry.js
required_checks:
  - adapter reports runtimeCoreReady
  - adapter reports fullApplicationSemanticReady only after all calculator intents are covered
  - evaluateExpression and drawGraph receipts include input and result evidence
```

```mcel-finding
id: calculator.finding.parser-eval-boundary
app: calculator
status: open
aspect: compute
severity: medium
problem: >
  The current arithmetic evaluation path normalizes allowed characters before
  evaluation, but the requirements should push the target toward a parser-owned
  calculator grammar rather than general JavaScript expression execution.
desired_behavior: >
  Expose a parser/validator result through the future Calculator semantic
  adapter so MCEL can prove which grammar was accepted and why unsafe tokens
  cannot execute.
source_candidates:
  - main_computer/web/applications/scripts/calculator.js
required_checks:
  - unsupported identifiers are rejected
  - finite-result checks remain visible
  - normalized expression is recorded in the receipt
```

```mcel-finding
id: calculator.finding.layout-ownership-to-extract
app: calculator
status: open
aspect: layout
severity: low
problem: >
  Calculator is visually rich enough to teach MCEL layout ownership, but the
  region language is still embedded in DOM conventions and documentation rather
  than a parsed shared layout grammar.
desired_behavior: >
  Use Calculator as a small app to extract common MCEL layout roles: mode
  toolbar, primary work surface, action grid, canvas output, evidence status,
  inspector/helper panel, and advanced/notebook helper.
source_candidates:
  - pretty_docs/mcel-calculator-requirements.md
  - main_computer/web/applications/apps/calculator.html
  - main_computer/web/applications/styles/calculator.css
required_checks:
  - MCEL Lab selects calculator controls and reports their inferred region
  - Graph canvas is not treated like a normal form field
  - Chat/helper panel is not treated as the calculator source of truth
```


## Runtime diagnosis contract

```mcel-runtime-check
id: calculator.runtime-check.default-primary-workspace
app: calculator
status: specified
mode: default
contract: calculator.contract.default.app-health
check: primary-surface
severity: critical
primary_surface_id: calculator.surface.workspace
host_selector: ".calculator-workspace"
editor_selector: ".calculator-workspace"
min_width: 420
min_height: 320
observes:
  - ".calculator-workspace"
expects:
  - Calculator workspace is visible and large enough for the active mode.
  - The primary calculator surface is not collapsed by surrounding app chrome.
failure_message: Calculator default mode must expose a usable workspace.
next_probe: layout.ownerProbe
source_binding: calculator.binding.route-and-ui
test_binding: calculator.test.route-checks
```

```mcel-runtime-check
id: calculator.runtime-check.default-required-regions
app: calculator
status: specified
mode: default
contract: calculator.contract.default.app-health
check: required-regions-visible
severity: critical
observes:
  - "#calculator-app"
  - ".calculator-shell"
  - ".calculator-mode-switch"
  - ".calculator-workspace"
  - "#calculator-display"
required_regions:
  - calculator.region.root | #calculator-app | Calculator app root
  - calculator.region.shell | .calculator-shell | Calculator shell
  - calculator.region.mode-switch | .calculator-mode-switch | Calculator mode switch
  - calculator.region.workspace | .calculator-workspace | Calculator workspace
  - calculator.region.display | #calculator-display | Calculator display
expects:
  - Calculator app root is visible.
  - Mode switch remains visible.
  - Calculator workspace and display remain visible.
failure_message: Calculator default mode must preserve root, controls, workspace, and display.
next_probe: layout.baseline
source_binding: calculator.binding.route-and-ui
test_binding: calculator.test.route-checks
```

```mcel-runtime-check
id: calculator.runtime-check.default-overlay-policy
app: calculator
status: specified
mode: default
contract: calculator.contract.default.app-health
check: overlay-policy
severity: warning
observes:
  - "#mc-widget-editor-root"
  - "[data-mcel-proof-surface]"
  - ".floating-tab"
  - ".side-tab"
expects:
  - MCEL/widget/proof overlays are not visible while the calculator is in default mode.
forbids:
  - shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay
  - shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface
  - shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab
failure_message: Calculator default mode should not be covered by diagnostic overlays.
next_probe: overlay.detector
source_binding: calculator.binding.route-and-ui
test_binding: calculator.test.route-checks
```
