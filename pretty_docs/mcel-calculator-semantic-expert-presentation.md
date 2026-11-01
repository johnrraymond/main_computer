# MCEL semantic Calculator — computed workbench presentation candidate

Status: **implemented opt-in UI candidate, not production promotion or full MCEL V1 acceptance**.

The repository's existing, operational Calculator markup and design served as the reference: visible modes, a large expression display, a keypad, a prominent result, and context-appropriate graph/symbolic panels. The previous semantic candidate had the computational contracts but displayed a generic collection of forms with oversized status receipts. The presentation candidate repairs this discrepancy without copying the legacy Calculator DOM tree, CSS grid coordinates, app-specific CSS selectors, or hardcoded runtime command/authority.

## Mechanism

The official semantic HTML + JavaScript source in `tools/fixtures/mcel_calculator_expanded/` remains **unchanged**. Its compiled `mcel.application-ir.v1` remains unchanged. The host's explicitly opted-in `presentation: 'expert'` instructs the shared `mcel-semantic-operational-runtime.js` to use generic UI-pattern rules, and the reusable `mcel-semantic-expert.css` chooses sizes, relative emphasis, and responsive behavior from semantic purpose/priority.

- A `compute` task + `primary` priority + a one-field text `expression` form produces a computed expression workbench and generated keypad. The underlying calculation is **never** evaluated in the UI; pressing `=` invokes the same authorized intent as the form's submit control. Unrelated applications and tasks do not acquire that keypad.
- A bounded view-state enum with up to six choices produces accessible mode buttons on wide screens and retains the native select on narrow screens. The runtime continues using the canonical viewTransition, without mutating business state or authority.
- Primary results have large, legible output; supporting status is a collapsed activity summary. Mode-switch receipts remain in the runtime receipt history but do not spam the on-screen operational activity list.
- Numeric-series graphing continues to render using the shared, typed plot node. Graph range fields are grouped in Min/Max pairs.
- All roles, hierarchy, content refs, capabilities, effects, and operation results still originate in canonical IR. Layout can be replaced without changing the app's authority or behavior. The host remains opt-in, behind its previous local-only route.

## Tests and limitations

Run `python -m tools.mcel_calculator_expert_frob`, optionally with `--hosted-url http://127.0.0.1:8765/applications/mcel-semantic-calculator-expanded` on the real Windows server. The browser tests cover 320, 390, 560, 1100, and 1272 widths; keypad editing and actual Calculator-core computations; tab and select modes; graph sampling; and no unexpected horizontal overflow. Older expanded and Phase 2C tests remain required.

This is not a claim of universal layout intelligence or full Calculator parity. The older Calculator's rich integration and remaining specialized functions, real optional service availability, other application types, refactor stability, and human design acceptance remain separate acceptance gates. New general design rules must still be evaluated against File Manager, Task Manager, and Code Editor before broader promotion.
