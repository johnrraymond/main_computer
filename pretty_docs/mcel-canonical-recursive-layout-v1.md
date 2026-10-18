# MCEL canonical recursive layout v1 — Patch 12

## Execution boundary

Patch 12 introduces an **opt-in canonical DSL/IR layout tree** and a **shared,
recursive, browser-executed structural-CSS controller**.  The former is semantic
source authority; the latter is a bounded candidate evaluator. Neither an HTML
`data-mcel-layout-*` annotation nor an application ID is consulted by the new
path. The existing workspace and two-region adaptive group controllers remain
compatible and unchanged for applications that have not migrated.

The official host-bound MCEL DSL accepts:

```javascript
app.layout.tree({
  schema: "mcel.layout-tree.v1",
  root: {
    id: "workspace", selector: "#demo-app", role: "group", maxColumns: 3,
    children: [
      {id: "navigation", selector: ".navigation", role: "region"},
      {id: "workbench", selector: ".workbench", role: "group", maxColumns: 2,
       children: [
         {id: "actions", selector: ".actions", role: "control-grid",
          maxColumns: 4, minTargetWidth: 44},
         {id: "results", selector: ".results", role: "region"}
       ]}
    ]
  }
});
```

The restricted official compiler emits the tree under the canonical
`layouts[0].layoutTree` field of `mcel.application-ir.v1`; it is bound to the
application's semantic fingerprint. The general `layout.define` API can also
supply `tree` through its options. The IR validator rejects invalid or duplicate
node IDs, unknown roles/fields, malformed dimensions, malformed child structure,
and unbounded depth/size. Each node is required to match one unique DOM element;
children of a declared group must be its direct DOM children. Structural drift
refuses evaluation instead of guessing an alternative selector.

## CSS ownership and candidate decisions

Roles: `group` allocates a container's direct semantic child regions,
`control-grid` allocates its direct actionable controls, and `region` is an
opaque owned surface whose controls must remain usable. `maxColumns` (1–8),
`minTargetWidth` (24–256 CSS px), `gap` (0–64 CSS px), and `atomicText` describe
constraints and preferences, **not pixel coordinates**. Absent fields take
bounded defaults. `data-mcel-text-fit="allow-intraword-wrap"` remains a
control-level policy for explicitly non-atomic content.

`McelSemanticLayoutInference.evaluateCanonicalLayoutTree(host, compiledIr)`:

- Searches combinations of recursive group and control-grid arrangements with a
  strict maximum of 192 candidate combinations by default (configurable up to
  512). For two-region groups, bounded proportional allocations are included.
- Renders each candidate in an inert, offscreen, temporary clone in the same
  CSS context. The live application DOM is unchanged by candidate evaluation.
- Requires hard constraints: computed CSS grid tracks, region and control
  containment, minimum button target width, atomic text fit, and the readability
  and target size of sibling regions. Candidates with unavailable required text
  measurements fail rather than passing vacuously.
- Ranks only **valid** candidates by measured container height, unused grid
  cells, total grid rows and a deterministic tie-breaker. The result includes
  `searchTruncated`; a bounded search is not a proof of global optimality.
- Returns `authoredCss`, `generatedCss` and every tested candidate's evidence;
  no source changes or repair authorizations are issued.

`applyCanonicalLayoutTree(host, evaluation)` re-trials the selected candidate,
changes only explicitly owned inline structural CSS (`display`,
`grid-template-columns`, `gap`, and atomic control text fit), and re-measures the
real DOM. If the independent `McelSelfDiagnosis` atomic-label collector is
loaded, it must pass. Otherwise the applied result is explicitly marked
`observed-only`, not independently certified. A failed live check rolls back.
`clearCanonicalLayoutTree(host)` restores pre-application inline styling.
`mountCanonicalLayoutTree` can optionally observe root width changes and reflow;
`unmountCanonicalLayoutTree` disconnects observation and restores styling.

## Demonstrated scope and unfinished authority

Executable tests compile a layout tree through the official DSL, reject invalid
IR, and pass the **normalized IR** to the browser. Browser tests cover the real
Calculator scientific keypad, an unfamiliar three-sibling dashboard, a
multi-level workspace with a nested control grid, a narrow unsatisfiable
constraint, read-only evaluation, responsive mount/unmount and exact DOM style
restoration. Existing Calculator CSS is not changed.

This stage **does not** migrate Calculator or Code Editor to live canonical-tree
execution, and it does not claim the package-host truth gate or a complete
accessibility/layout-quality certification. The legacy application HTML
attributes and CSS remain until the migration's own acceptance gate passes.

Remaining stages: package projection and lifecycle integration of the DSL tree,
semantic treatment of arbitrary non-grid layout behaviors, deterministic
cross-viewport/font-scale evaluations, runtime provenance/observability,
independent quality diagnostics, and cross-application package-host acceptance.
