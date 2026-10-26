# MCEL Semantic Compression and Presentation Recipes — Experiment v1

## Objective

Generate a compact, useful interface from the same semantic source and graded layout hints **without** hand-authoring Task Manager layout code. This is an experimental, fixture-only generator; production data, authorization, host integration and human visual approval are explicitly outside its certification scope.

## Architecture

The existing MCEL semantic generator now calls `tools/mcel_presentation_planner.py` after validating authored semantic YAML and before rendering. The planner is a pure transformation: it uses the semantic **role** of each region, the identity/summary attributes of each row type and semantic field traits. It does not check the application ID, entity names or row IDs. It emits `presentation-plan.json` with explanations and all source fields.

Semantic compression keeps primary identity visible, selects up to two relevant comparable summary facts and defers the remaining fields to the selectable inspector. All source fields and declared actions are retained in the semantic manifest and all original registered MCEL component instances remain mounted. It never authorizes a consequential action.

The initial reusable presentation patterns are `comparable-collection`, `summary-strip`, `context-inspector`, `transient-activity`, `contextual-toolbar`, `advanced-disclosure`. Desktop collections use a responsive comparison **grid**; narrow viewports use compact rows. The planner selects a recipe by generic region semantics rather than constructing an application-specific HTML skeleton. The existing MCEL canonical layout engine owns spatial arrangement and continues to interpret authored graded layout hints.

## Commands

From repo root after installing patch on top of the existing semantic-generation and visual-gallery patches:

```text
python tools/mcel_layout_frob.py --mode semantic
python tools/mcel_layout_frob.py --mode semantic-compare
python tools/mcel_semantic_generate.py --mode verify --presentation baseline
python tools/mcel_semantic_generate.py --mode verify --presentation planned
python -m unittest tests.test_mcel_semantic_generation tests.test_mcel_presentation_planner
```

The new `semantic-compare` run creates these under `runtime/reports/mcel-layout-frob/presentation-comparison/`:

- `before/` — old uncompressed renderer, browser report, full-page PNGs, gallery;
- `after/` — planner renderer, manifest, plan, browser report, full-page PNGs, gallery;
- `comparison/` — 22 matched before-and-after full-page PNGs with shared Frob gallery and review CSV;
- `comparison-report.json` — separate machine evidence from human quality judgment.

## Gates and boundaries

- All source row fields preserved with an actual inspector access path, checked in Chromium against every row type.
- Registry components remain serializable and mounted; DSL compilation and all five responsive browser checks pass.
- Hard author layout constraints remain enforced by existing MCEL candidate selection.
- Destructive actions are disabled; no live effect calls, production Task Manager or Website Builder changes.
- Visual approval remains `UNREVIEWED`; lower page height is a useful metric but is not proof of attractiveness.
- A one-row Inventory Review holdout has been compiled and browser-verified, showing the same role-driven recipes run outside Task Manager. This is limited generalization, not arbitrary application-shape support: six semantic region roles are currently required by the existing shell grammar.

## Current limitations

This is a first-stage generic presentation planner, **not** a learned design model, unconstrained UI composer or production data adapter. It uses a small deterministic field-priority heuristic and six recipes. A future round should benchmark alternative semantic compression/pattern candidates on novel application shapes, richer fixtures and reviewer scores. The planner is not permitted to assert human aesthetic quality itself.
