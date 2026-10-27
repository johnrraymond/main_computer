# Patch 8 — Task Manager Semantic Collection Pipeline

## Objective

Demonstrate that the **existing** Task Manager's real read-only process snapshot can be interpreted and presented by general MCEL machinery rather than the tab workspace's raw `Object.keys()` fallback. No new application is commissioned. This does not change the Task Manager operational controls or default UI and does not promote the Phase 3 pinned IR witness.

## Architectural boundary

In explicit `?mcel_task_notebook=lab` mode:

1. The existing Task Manager refresh publishes its authoritative snapshot. It is not mutated, shortened or used to authorize any operation.
2. `McelTaskManagerProcessContract` declares field provenance, derived role precedence, numerical memory units, long command line display shortening, and presentation tiers. This is application-level metadata, not Task Manager logic embedded in the layout engine.
3. `McelSemanticCollectionWorkspace` projects the read-only snapshot to semantic records and renders *registered* `mcel-collection` / `mcel-row` / `mcel-field` elements. It retains exact original source fields for keyboard/click inspection.
4. `McelSemanticLayoutInference.mountSemanticCollectionLayout` validates the rendered collection, measures text, chooses visible fields according to declared primary/secondary/detail priorities, allocates their widths, and discloses lower-priority fields via the accessible inspector. The legacy no-tier allocation is unchanged.
5. `McelTabWorkspace` consumes the collection through optional `collections[dataSource]`; its generic raw table path remains available for other data sources and as a negative-control baseline.

Identity (PID, process name) is primary. Derived process role, typed memory and status are secondary. Full command line is shown only when capacity permits; exact command and original timestamp remain available via row inspection. The priority/disclosure mechanism works for any collection whose author supplies the same *generic* semantic schema.

## Verification

From repository root:

```powershell
python -m tools.mcel_task_manager_semantic_pipeline_acceptance
python -m tools.mcel_task_manager_notebook_lab_acceptance
python tools/mcel_promise_gap_frob.py
```

The first command runs the browser at 320, 390, 560, 1100 px with 22 realistic synthetic process records. It checks equality of 22 identities, exact source and command inspection, role precedence, memory units, responsive containment, declared layout authority, held-back/detail fields, absent/invalid identity refusal, stale updates, original control preservation, no requests, and reversible teardown. It also checks a non-process collection schema to prevent accidental domain-specific projector logic.

The second is Patch 7 rollback and guarded-mount regression. The third includes the new semantic slice in the Task Manager promise report and **does not** claim complete promise fulfillment.

### Limitations

- The 22-row browser acceptance is **synthetic**. Actual hosted telemetry parity and the lifecycle of a live HTTP route must be checked locally.
- The source-to-host continuity of the missing original Phase 3 DSL is not established; the notebook still uses the pinned witness.
- The *collection* now uses shared layout authority. The outer notebook/sidebar/control hierarchy still relies on the lab CSS and needs separate integration.
- Only process collections have declared semantic metadata in this patch. Other notebook data sources continue using the generic read-only renderer.
- No authority to kill, restart, schedule, or otherwise modify the computer is given to the MCEL notebook. Legacy controls remain intact.
- Beauty, opposing designs, accessibility beyond bounded interaction, and authoring economy are not certified.

## Negative controls

- Without the semantic contract, the existing generic fallback exposes raw object field names, including redundant machine flags and preview columns.
- An invalid identity field is rejected before rendering.
- A stale snapshot version is rejected.
- The experimental mount cannot be made without explicit opt-in; unmount restores the legacy notebook exactly.

Do not substitute helper compilation or screenshot similarity for live independent application and effects proof.
