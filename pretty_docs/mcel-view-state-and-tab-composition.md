# MCEL constrained view-state transitions and tab composition

**Scope:** Generic MCEL DSL and compiler rules. This is not a Task Manager adapter or a license to change installed applications.

## Typed local selection

For a renderer-local value chosen from a fixed set of string identities, an author may declare a state using `app.field.string().oneOf(ids)` and an `app.intent.interaction` with `viewState: {state, input, schema}`. MCEL compiles this into an `intent-input` (bounded string enum), `viewWrites`, and `viewTransition` (`transition.assign` from `input.read`, with `invalidInput: "refuse"`). The compiler validates the declared input and state types, one-target authority, enum equivalence, safe initial value, and absence of effects or canonical writes. It rejects a transition that asks for operational privileges, targets a non-renderer-local state, or lets an unknown value through.

These view transitions are **not** canonical mutation authority. They are not a shortcut around the existing write-set or effect-accounting rules. `McelViewTransition.apply` is a pure implementation of the bounded transition. It produces a new renderer-local state record and zero effects, or a refusal. A host or generated view must explicitly own publication and visual updates; this patch does not install that integration.

## Tab/panel composition

When a semantic surface declares `tabGroup`, the compiler validates the one-to-one relationship between semantic tabs, controls, and panels, including unique identities and selectors, the default selected tab, the renderer-local selection state and selection intent, and the tablist/tabpanel/keyboard accessibility contract. These requirements apply to *any* application's `tabGroup`, regardless of its app ID. Invalid declarations fail at compilation with `MCEL_IR_TAB_GROUP_COMPOSITION_INVALID`, not at runtime by accidental breakage.

**Not yet provided:** Generated tab DOM, ARIA state updates, keyboard handlers, layout styling, responsive behavior, route synchronization, or independently proven application interactions. These are the next library capabilities required before retiring old UI implementations.

The governing design objective remains minimal authoring effort, attractive defaults, expressive flexibility, and correctness through composition. A valid compiled graph establishes semantic constraints, not evidence of a polished generated web app.
