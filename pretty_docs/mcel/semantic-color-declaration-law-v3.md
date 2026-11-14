# MCEL Semantic Color Law v3 — canonical declarations (Patch 1)

The v1 palette-backed resolver and v2 visual-state compiler remain the Theme authority. This patch defines their **canonical authoring contract**. It is additive: no existing MCEL application declares these colors yet, so adding v3 alone changes no rendering.

## Source contract

The canonical source can declare `color` on `surface.action`, `surface.form`, `surface.text`, `surface.plot`, and typed `surface.region` records. The DSL emits the declaration as part of the corresponding `mcel.application-ir.v1` node or region. No DOM label, CSS class, application ID, screen position, or palette value is allowed to supply missing meaning.

A declaration contains only the supported dimensions:

```js
color: {
  role: "action.inspect",          // required: existing Color Law role
  surface: "control",              // required: existing Color Law surface
  context: "toolbar",              // optional; default
  consequence: "routine",          // optional; unspecified
  selection: "off",                // optional; off
  operation: "idle"                // optional; idle
}
```

`McelLabStyleLaw.validateColorDeclaration(declaration,kind)` validates identity, values, kind/surface compatibility, cross-dimensional contradictions, and unknown fields. Physical colors, arbitrary CSS, and role names not in `semanticColorRoles` are refused. The DSL performs bounded syntactic validation; the Color Law's semantic validator is authoritative before runtime mount. `resolveDeclaredColor` and `compileDeclaredColor` are pure wrappers around the v2 resolver and native-state CSS compiler. Their outputs are deterministic for a given Theme palette and semantic state.

### Runtime-owned state

The application does not author pointer interactions. Native browser `:hover`, `:active`, `:focus-visible`, and system media preferences are compiled from the full v2 state model. The operational renderer supplies availability, selection, and operation status from its authoritative runtime. Consequence and purpose are independent; stop is not inferred to be destructive. Theme owns physical foreground, background, border, accent, selection, status, and focus-ring colors. Form owns shape and focus-ring width. Layout owns geometry.

### Deliberate migration boundaries

This patch does **not** make `color` mandatory in existing applications. App colors still on legacy CSS remain until migrated and checked by later patches. The permanent strict gate for forbidding app-owned physical colors is Patch 6 of the full migration plan. The shared Default Theme and Lab/standalone palette equivalence are Patch 3; no palette values are added to application source in v3.

## Acceptance

`tests/test_mcel_semantic_color_contract_v3.py` checks all eight existing Theme palettes, complete rest/hover/press/focus/disabled/selected/pending compositions, deterministic CSS, strict refusals, and color-only DSL authoring. Existing E/W/G and Evaluate consumers retain their accepted behavior.

## Noninteractive container semantics

Navigation collections are not navigation items. A container such as the
Calculator MODE region declares `surface.panel` with navigation context; its
renderer-created `role="group"` wrapper declares `navigation.group`. Only the
actual mode input and individual navigation buttons declare `navigation.item`.
Color Law treats panel and group roles as noninteractive and compiles a single
resting-state CSS treatment for them—never ancestor `:hover`, `:active` or
`:focus-visible` rules. Interactive roles are refused on semantic regions.
Operational intent coloring binds to the real `[data-intent]` control, not a
container wrapper. Hovering or clicking a child cannot recolor its parent
merely because CSS `:hover` matches ancestors of hovered descendants.
