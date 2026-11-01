# Expanded semantic Calculator candidate — opt-in integration experiment

Status: **expanded candidate only; NOT production promoted and NOT full Calculator parity**.

The candidate is served at `/applications/mcel-semantic-calculator-expanded` to
local clients after restarting the viewport server. The original
`/applications/calculator` and earlier
`/applications/mcel-semantic-calculator-candidate` remain unchanged.

## Authoritative authoring

`tools/fixtures/mcel_calculator_expanded/application.html` establishes semantic
HTML hierarchy, purpose, task, author priorities, and a typed mode-state visibility
relationship. `application.js` contains real MCEL DSL state, declared authority,
capabilities, results, inputs, and bindings. There is no authored layout tree,
DOM selector, arbitrary HTML execution, or application-specific rendering branch.
The compiler produces one canonical IR with both sources in provenance.

## Capabilities exercised

- Real Calculator core arithmetic including decimals and compatible metric unit
  operations; mismatched dimensions fail without publishing false results.
- Real Calculator core graph-expression parser and sampler. The generic MCEL
  numeric-series plot consumes typed x/y data and renders a responsive SVG.
- Bounded, locally controlled mode selection; panels follow their declared
  renderer-local enum value, not active data counts or visual heuristics.
- Optional Mathics evaluate/ask, arithmetic-model assistance, graph-model
  assistance, and contextual result questions use existing Calculator service
  APIs via an explicit least-authority capability port. They report failure if
  the corresponding service is unavailable. Browser tests simulate HTTP replies;
  they do NOT claim real backend service availability.
- Native typed form defaults provide a convenient graph axis range and a reset
  control where those defaults are defined.
- All semantic headings originate in the authored HTML and are rendered safely
  as text, without constructing a second HTML/CSS layout in the application.

## Security and stability

Only the two declared external-read capabilities are available. The port checks
app, intent, effect, risk, operation, arguments and expected types. It does not
provide shell execution, filesystem access or source writes. The shared runtime
validates results before updating derived state; competing operations targeting
one result state cannot overwrite the newer accepted response. State updates do
not remount the interface, and graph SVG nodes are retained until graph data
changes. Unmount discards pending result publication.

## Not proven / not yet at parity

The legacy Calculator still has keypad token entry, its own clear-expression and
graph-reset operation contracts, result-context/chat integrations, richer mode
interaction and other established presentation behaviors. The new candidate
supports ordinary keyboard expression input, typed input reset and the operations
listed above but is not a full functional or visual replacement for that app.
Human visual acceptance and live Windows hosted test are outstanding.

## Acceptance

```powershell
python -m tools.mcel_calculator_expanded_frob
python -m tools.mcel_calculator_expanded_frob --hosted-url http://127.0.0.1:8765/applications/mcel-semantic-calculator-expanded
```

Local Frob success is `PASS_EXPANDED_CANDIDATE_ONLY` and says nothing about
production promotion, full parity, the optional services, or an expert
layout-design engine. Earlier candidate tests and runtime/compiler tests remain
mandatory regression gates.
