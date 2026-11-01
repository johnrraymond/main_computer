# MCEL V1 Compiler, Expert Layout, Runtime and Proof Contract

**Normative V1 target; not current implementation certification.** [MCEL V1 Product Contract](mcel-v1-product-contract.md) is authoritative. Current implementation and generated evidence are tracked independently in [MCEL Status and Roadmap](mcel-status-and-roadmap.md). Authoring surfaces are defined in [V1 Authoring Format](mcel-v1-authoring-format.md); permitted interface semantics in [V1 Vocabulary](mcel-v1-semantic-vocabulary.md).

## Dual-source normalization

```text
application.html + application.js
     → validate HTML5, official DSL, and package authority
     → resolve cross-file identities, types and region relationships
     → single canonical Application IR (operational graph + semantic hierarchy + interface intent)
     → expert abstract interface synthesis and pattern selection
     → geometry and responsive realization
     → bound functional runtime
     → independent functional, browser and human design acceptance
```

Inputs are compiled *together*. Neither file is executable UI output. The JavaScript DSL is a declarative authoring input, not an arbitrary runtime DOM builder. The HTML is meaningful structure, not a pre-rendered page whose CSS/DOM is reused as the finished presentation. Generated representations, state adapters and style sheets are compiler-owned and replaceable. Target IR evolution must be validated rather than assumed implemented in the current `mcel.application-ir.v1` schema.

## Hierarchy preservation

Each region has stable `id`, optional `parentId`, meaningful child `ordinal`, native HTML tag semantics, typed content reference, purpose, tasks, priority, preferences, and applicable relations. Preserve *logical* containment, meaning and reading order without treating it as a mandated grid or pane structure. Parent/child and ordering information must not be flattened or replaced with alphabetical region ordering. MCEL can visually reposition/switch patterns when the accepted design preserves its semantic and accessibility obligations. Illegal duplicate IDs, ancestry cycles or conflicting native semantics fail at compilation.

## Provenance and stable identities

Every normalized statement has precise origin (file/location/declaration and whether author-explicit, source-verified, candidate or policy). Record identities must come from typed application data and scoped keys; never synthesize a process/file identity from display text or array position. New design nodes retain links to semantic region and operational binding IDs. A model/state ref in HTML must resolve to exactly one legitimate JS declaration. Compiler normalization must make semantically equivalent inputs produce a stable fingerprint; excluded authored geometry/presentation hints cannot silently change the operational hash. Presentation provenance and executable authority remain distinguishable.

## Abstract interface synthesis

The design engine consumes the validated logical hierarchy, region purposes/tasks/content, explicit relationships, and operational facts. It builds an abstract interface tree, selects composable interaction patterns and data representations, and then evaluates feasible layouts. It must not branch on `file-manager`, `task-manager`, `code-editor`, or `calculator` application IDs to produce a special layout; the same semantic pattern policy applies everywhere. An `inspection` hint does not invent a selection/read relation; an `edit` task does not create a save effect. Unresolved meaning produces a reviewable diagnostic or an appropriately conservative candidate. No unreviewed app-specific fallback may qualify as V1 success.

**Hard constraints:** all declared and admitted capabilities remain reachable; identity/data/authority and semantic hierarchy are preserved; accessibility/interaction/focus order and authorized effect boundaries hold; no essential content disappears; no unintended horizontal overflow or conflicting scroll ownership; candidates are deterministic within their documented input/viewport/design-policy scope.

**Soft optimization objectives:** salience, usable visible information, task accessibility, meaningful grouping, density and declared placement/representation preferences. Soft preferences may be displaced by hard constraints, with traceable reasons and responsive alternatives. A plausible-looking design or a low-overflow score is not evidence of an appropriate application by itself.

## Geometry and responsive policy

MCEL chooses actual widgets, pane realization, grid/flex geometry, spacing, typography and breakpoints for each accepted semantic structure. Layout is responsive across supported host dimensions; acceptance must include 320, 390, 560, 1100 and 1272 CSS-pixel widths. The host/container measurements, not arbitrary assumptions about browser `vh`, determine available space. A document/workarea may retain consistent focus and logical subject while inspector/navigation panels become contextual or collapsible at small widths. MCEL must not require authors to supply pixel pane widths or media queries for standard composition.

The accepted plan is compiled/promoted using deterministic selection rules and recorded source/design evidence. Ordinary live data changes, refreshes and selections do not trigger an unbounded self-correcting layout search. Ordinary responsive policy and deliberate author refactor recompilation are different from speculative runtime redesign.

## Operational safety

Only the validated JavaScript operation/effect contracts confer permission. HTML regions, roles, tasks, relation names and preferences never grant read, write, kill, delete or privileged effects. Honor authority boundaries, risk scopes, input validation, read/result binding, effect receipts, cardinality, and denial paths; no command becomes functional solely because the layout engine displayed a button. The runtime must never silently substitute simulated data for real operations while reporting parity. In particular, Task Manager consequential process/server operations and Code Editor source writes remain governed by their existing authority controls.

## Lifecycle and determinism

State, selection, scroll, focus, modified/dirty documents, pending work, results and receipt identity remain intact across authorized refresh, view switching, viewport resize, and component realization changes. Recompilation after an author refactor must preserve unaffected semantic decisions where their inputs and constraints are unchanged, or explain why the design changed. A repeated compile with identical semantic sources and policy inputs produces equivalent normalized IR and accepted structural decisions. Versions beyond V1 must be capable of interpreting the shipped V1 meaning; aliases and explicit design-policy pins are deferred from authored applications for now.

## Accessibility

The chosen concrete widget patterns must meet WCAG-relevant semantic labeling, keyboard control, focus order, status announcement and error identification requirements. Native HTML landmarks contribute meaning but do not by themselves prove a complete accessible interaction implementation. MCEL chooses accessible grid/tree/dialog/editor patterns and verifies their keyboard behavior. Selection and focus are distinct concepts. Adaptive layouts must not strand commands or render a programmatically associated status invisible to assistive technology.

## Diagnostics

Compiler diagnostics must be precise and attributable to HTML or JS source: invalid `data-mcel-*` key, value, duplicate ID, broken cross-file reference, type mismatch, illegal cycle, contradictory semantic role, missing effect authority, unsupported schema or unmet synthesis constraint. Unsupported capability is not a silent success. A failed visual candidate returns a bounded failure report and preserves the previous accepted version, if available. The target must distinguish `not implemented`, `insufficient author semantics`, `invalid contract`, and `layout/proof failure` rather than forcing a generic fallback.

## Proof boundary

Full V1 certification requires: source/IR round-trip, no silent loss, correct operation/data parity, real hosted command outcomes, exact identity, authorization/receipts, lifecycle, responsive measurements, accessibility/keyboard/focus/scroll, deterministic reruns, meaningful refactor tests, and documented independent human design review. Test the same compiler/engine against all four [reference apps](mcel-v1-four-app-acceptance.md), not four manual layout profiles. An app cannot be declared successful merely because a compiler returns `pass`, because a screenshot looks good, or because there is no overflow. An unpromoted candidate must not displace a known-working app. Documentation itself certifies none of these behaviors.
