# MCEL task-aware composition — implementation and acceptance

## Purpose

Turn independent semantic regions into usable task-aware compositions using
existing **registered MCEL components** and its **canonical recursive layout
engine**. No application-name switch, new layout engine, live service connection,
or app-specific CSS. Source semantics, effects, and graded hints remain their
existing authorities.

## Pipeline

`design.yaml` → existing semantic compression + state policy →
`mcel_task_composition.plan_task_composition()` → generated manifest → generic
runtime workspace group → existing canonical recursive layout → Frob screenshots.

The planner groups the six existing semantic region roles into three logical
units: `searchable-collection` (search + collection + contextual inspector),
`attention-and-feedback` (compact status + verified operational alerts), and
`optional-capabilities` (authorized disclosure). The group is a layout-only DOM
wrapper, **not** a new MCEL component or a duplicate search widget. All
registered MCEL component instances, bindings, author hint IDs, and semantic
fields remain intact. The old state-aware generator remains available for a
controlled comparison.

The runtime derives these task modes from actual fixture state and interactions:

- Browse: no query, selected record, or unresolved operation.
- Search: nonempty collection search query.
- Inspect: user has selected a semantic record; contextual inspector is visible.
- Monitor: operation running or its outcome is unverified/unknown.
- Recover: authoritative fixture explicitly marks a verified failure.

Safety precedence: verified failures and unresolved operations outrank searches
and selection. Presentation decisions cannot forge success, hide an unresolved
failure, enable destructive controls, or rewrite authoritative state.

User-facing labels prefer authored `display_label`/view titles, otherwise apply
conservative generic identifier humanization. The original semantic keys are
still present on actual generated field/record nodes, including in the
inspector. Field values are fixture observations, not fabricated live data.

## Run

From the Main Computer repo root:

```text
python tools/mcel_layout_frob.py --mode semantic
python tools/mcel_layout_frob.py --mode semantic-compare
python -m unittest tests.test_mcel_task_composition tests.test_mcel_presentation_planner tests.test_mcel_presentation_state
```

Open the generated `runtime/reports/mcel-layout-frob/presentation-comparison/comparison/gallery.html`.
The comparison uses the prior state-aware generator (before) and the new
task-aware nested composition (after), identical source YAML and synthetic
fixtures, and full-page PNGs for mobile, desktop, and operational states.
The gallery is **human review evidence**, never an automatic aesthetic PASS.

## Acceptance and scope

- All source rows and inspector fields stay accessible.
- Author `require` layout hints still pass in the existing canonical planner.
- Real DOM uses one searchable workspace containing search, collection, and
  conditional inspector; do not create a Task Manager-only layout template.
- Dynamic task traces reveal `browse`, `search`, `inspect`, `monitor`, `recover`.
- Canonical MCEL layout accepts nested groups; no overflow at 320, 390, 768,
  1440, or 1920 CSS pixels.
- Synthetic error/unknown actions retain visible status and never enable
  destructive effects.
- A one-view inventory holdout works without added recipes or layout code.
- Reuse Frob screenshot hashes, paired gallery, and human review checklist.

**Not proved:** live Task Manager / worker / AI telemetry integration, real
operational effects, subjective visual quality, or deployment parity. Keep live
Task Manager and Website Builder unchanged.
