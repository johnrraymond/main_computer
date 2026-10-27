# MCEL Patch 3 — shared layout authority consumes semantic representations

Patch 3 moves column-width measurement, space redistribution, table/stacked
selection, reflow and layout CSS application **out of the experimental Frob**
and into the existing production-shipped `McelSemanticLayoutInference` engine.
The engine is still opt-in for registered collection hosts; this patch does
**not** enable the feature on an ordinary production application route.
Website Builder is unchanged.

## Architectural boundary

The existing Patch 1 semantic contract remains authoritative for original typed
values, missingness, identity, and presentation opt-ins. Patch 2.1 still renders
a collection with MCEL's registered `element.core.collection`,
`element.core.collection-row`, and `element.core.field` elements. Patch 3 changes
who owns layout decisions:

1. A host renders registered collection elements from its semantic projection.
2. The **shared engine** validates the source/projection/DOM linkage, rejects
   collisions and mismatches, and measures real visible browser text.
3. The engine distributes the width recovered by contextual compression, then
   sets grid tracks or stacked mode. On resize/re-render it reapplies the policy.
4. The host retains ownership of searches, typed sorts, exact inspection, and
   all source values. No source writes or application actions are authorized.
5. If the shared engine is unavailable or the contract cannot be validated,
   the mounting adapter refuses; there is **no Frob-local layout fallback**.

`main_computer/web/applications/scripts/mcel-semantic-layout-inference.js`
now exposes the following public methods on `McelSemanticLayoutInference`:

- `evaluateSemanticCollectionLayout(host, contract, registry)` returns a
  read-only candidate or a refusal reason.
- `applySemanticCollectionLayout(host, contract, registry, evaluation)` checks
  candidate freshness, then applies the measured layout to registered rows.
- `mountSemanticCollectionLayout(host, contract, registry, options)` performs
  initial validation and application and returns a `refresh()` / `unmount()`
  handle. Optional ResizeObserver/MutationObserver reflows run on the host.
- `clearSemanticCollectionLayout(host)` restores authored inline styles and
  collection layout attributes; does not rewrite source values.

Here, `host` is the actual collection panel containing one registered
`mcel-collection`, `contract` is the same `project_layout(...)` projection used
in the Frob, and `registry` is `window.McelElementRegistry`. This is an explicit
consumer interface: no application is silently opted in.

## Run

From the repository root:

```text
python tools/mcel_representation_layout_frob.py
python -m pytest -q tests/test_mcel_canonical_recursive_layout.py tests/test_mcel_full_application_layout_authority.py tests/test_mcel_representation_layout_contract.py tests/test_mcel_representation_layout_patch21.py tests/test_mcel_representation_layout_engine_patch3.py
python -m webbrowser runtime/reports/mcel-representation-layout/gallery.html
```

The Frob exercises 6 collections × 5 viewports × 2 presentation modes in a
real browser, plus two shrinker-disabled controls, multiple live resize paths,
inspector failure injection, and an independent **missing shared engine**
negative control. Browser PASS is impossible without the shared authority.

The Patch 3 boundary tests exercise unregistered elements, malformed semantic
requirements, identifier collisions, source/DOM tampering, stale candidate
refusal, evaluation purity, unchanged source values, and exact inline-style
rollback. Canonical layout-tree regressions continue to run unchanged.

The browser report declares `shared_engine_integrated: true` but
`production_integrated: false`. This distinction is intentional: the shared
application engine now owns this mechanism, but normal application routes do
not yet mount their own semantic collection contracts. The Frob does not certify
assistive-technology quality, subjective aesthetics, or every possible dataset.
