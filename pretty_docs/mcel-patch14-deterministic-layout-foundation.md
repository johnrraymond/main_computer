# Patch 14.0 — Deterministic Semantic Layout Foundations

**Scope:** Internal layout decision/metric separation, canonical names, pure selector, and replay tests. **Status:** Experimental foundation; no production promotion.

## Purpose

A content width or measurement change must not be mistaken for a change of structural layout identity. Patch 14.0 prepares MCEL for a layout continuity policy without enabling that policy prematurely. The canonical semantic application IR, v2 collection contract, element registry, and normal application interfaces remain unchanged.

## New pure module

`main_computer/web/applications/scripts/mcel-layout-continuity.js` exports `McelLayoutContinuity` globally in browsers and through CommonJS in Node.

- `stableName({concept,purpose,family,region,occurrence})` builds an unambiguous source-independent semantic layout name from explicit *semantic* identifiers. It never uses current row values, PIDs, memory readings, or measured pixels.
- `makeDecision(...)` creates a versioned serializable discrete decision and rejects non-finite state.
- `decisionKey(decision)` canonicalizes object member order using code-unit comparisons, and rejects measured-width/height keys inside a decision.
- `choose({candidates,incumbent,policy})` is a **pure, inactive** policy prototype. It rejects failed/unsafe candidates, quantizes the candidate's scalar quality into integer milli-units, favors an eligible incumbent within a specified epsilon, otherwise compares the candidate's lexicographic structural displacement from the incumbent, then quality and code-unit decision keys. The quality scalar is only a synthetic/test input at this stage: it does **not** validate a future browser measurement-quality objective.
- `replay(events,initialState,policy)` reproduces an ordered canonical observation log and emits serializable state and decision history.
- When search truncation is reported, an incumbent missing from evaluated candidates produces **INCOMPLETE**, not an unjustified replacement.

The selector has no DOM, timers, network, storage, locale-sensitive string comparisons, or model calls. The input is a **recorded sequence of observations at commit points**, not a log of raw ResizeObserver callback timing. Node replay compares the full serialized decision history.

### Current naming boundary

Portable collection decisions require an explicit `layout_link.semantic_layout_id` and an actual semantic property identifier (`semanticPropertyId` or `conceptRef`) for **every** field. Existing collections without these identifiers are marked `portable:false`, and their decisions have `name:null`. Temporary source-local keys are never presented as durable semantic identities. `sourceBindings` are recorded in the **metrics/evidence**, not the portable decision. Existing canonical-tree region IDs provide tree-plan names; cross-application equivalence of authored region names is not presumed.

## Read-only existing engine projections

`mcel-semantic-layout-inference.js` now exports:

- `separateSemanticCollectionLayout(evaluation,contract)`: provides `decision` (family, mode, visible/disclosed semantic field references) and `metrics` (measured widths, allocated tracks, source bindings) separately.
- `separateCanonicalTreeLayout(tree,selected)`: provides a discrete plan of node IDs, templates, gaps and areas, plus measured quality, failure and record evidence.

Successful evaluations also carry `decision` and `metrics` beside the unchanged legacy `plan`, `selected`, and `candidates` fields. The current applicator **still checks full plan and geometry freshness**, retains existing CSS behavior and continues to choose the same baseline layout. Removing the stale-geometry check without an atomic commit policy would weaken correctness. It is **not** done here.

The canonical-tree exact-quality tie-break now uses Unicode/JavaScript code-unit ordering rather than `localeCompare`, avoiding a locale-dependent tie in otherwise equal candidates.

## Golden compatibility

The original engine from `main_computer_test-20261028-001759.zip` and the updated engine were separately loaded in the same Chromium environment with identical generated MCEL collection fixtures at widths 320, 390, 560 and 1100.

At all four widths: the legacy `plan`, rendered grid track strings, field visibility, row count, layout mode and authority were **identical**. The four-width baseline outcomes are recorded in `tools/fixtures/mcel_patch14_0_legacy_golden.json`. Exact pixel measurements are dependent on font/browser environment and must not be treated as portable cross-browser constants.

The live Frob is `python -m tools.mcel_patch14_foundation_frob`. It also runs the relevant existing collection, layout inference and canonical-tree regression tests.

## Explicitly deferred to 14.1–14.5

- Production continuity selector, actual incumbent retention/hysteresis, bounded track growth/shrink boundaries, and stable row ordering.
- Commit-event scheduling independent of frame coalescing.
- Verified browser displacement and responsive stability thresholds.
- User profile persistence, aliases, migration, and hint priorities.
- Semantic versus blind candidate generation, independent judging, and held-out Frob generalization.

**Patch 14.0 does not claim visual stability has been solved.** It makes decision identities distinct from measurements, and provides a pure deterministic selector substrate on which stability can be implemented and measured without another renderer or application-specific layout policy.
