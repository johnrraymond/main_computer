# MCEL Layout Frob Lab

A read-only, reproducible experiment comparing the *actual* MCEL workspace inference,
canonical layout tree, and adaptive control-grid engine with new bounded graded-hint
and constraint-candidate planners. It does not replace Task Manager.

## Run it

From the repository root, with Python, PyYAML, Node, Playwright, and Chromium:

```text
python tools/mcel_layout_frob.py
python tools/mcel_layout_frob.py --mode sweep --max-cases 128
python tools/mcel_layout_frob.py --mode sweep --max-cases 0
python tools/mcel_layout_frob.py --mode plan --no-browser
python -m unittest tests.test_mcel_layout_frob tests.test_mcel_layout_frob_crosscheck
```

`--max-cases 0` explicitly requests the potentially expensive full factorial run.
The default sweep is a deterministic pairwise-greedy covering sample. Override
`--widths`, `--fixtures`, `--strengths`, `--chromium-executable`, or `--output`.
The runner prints progress during browser testing.

The **single Frob command** runs the experiment and updates the expanded crosscheck
with matching evidence. Results appear under `runtime/reports/mcel-layout-frob/`:

- `report.json`: machine/engine matrix; candidate traces; soft/hard hint decisions;
  independent Chromium geometry and adaptive control-grid evidence; DSL compilation;
  fixture-only runtime safety checks; run provenance and evidence hashes.
- `crosscheck.json`: all 379 Task Manager obligations, preserving PASS, FAIL,
  UNVERIFIED, and NOT_IMPLEMENTED independently.
- `gallery.html` and PNG captures: inspect and compare generated browser layouts.
- `probe-application.js`: disposable generated MCEL DSL used for compiler proof.

The earlier separate checker can also ingest an actual Frob run:

```text
python tools/mcel_layout_frob_crosscheck.py --mode audit --frob-report runtime/reports/mcel-layout-frob/report.json
```

**A Frob run is not complete certification.** Bounded browser PASS is narrower
than entire-app acceptance. The source application, live data sources, worker and
AI event producers, destructive effects, human visual approval, and rollout/rollback
are *not* proven by fixture witnesses. Existing / proposed features remain distinct.

## Machine/engine boundaries

- `workspace_inference + workspace_greedy`: runs production `inferWorkspaceLayout`;
  the engine **does not support graded hints**.
- `canonical_tree + bounded_recursive`: chooses from a bounded grammar, and
  applies it through the production canonical evaluator. No graded hints.
- `canonical_tree + graded_hint` / `constraint_optimization`: new bounded
  planners select candidate grammar variants; **production canonical MCEL**
  performs the actual DOM layout and browser checks.
- `constraint_layout`: experimental discrete feasibility/selection bridge to
  the same canonical MCEL renderer; **not** a separate production constraint solver.
- `adaptive_control_grid`: real MCEL local-control contract, **not** a full
  application shell; reported as subtree-only.
- Unimplemented pairings are explicit in the 16-entry compatibility matrix;
  the runner never silently substitutes an engine.

The layout grammar here tests a six-region application shell. The catalog additionally
renders inert specimens of the 11 Task Manager semantic row contracts. This is
*not* a general app generator, live row binding, or all-mode presentation proof.

## Hint test

The experimental hints range `none → suggest → prefer → constrain → require`.
Hard contradictions are refused with original IDs; soft compromises are recorded.
Require obligations are checked against actual browser positions. Mobile columns
are measured at 390px, not inferred from the solver's own declaration.

## Truth and safety

- Planner scores are *not* browser evidence; a headless Chromium run is required.
- Evidence is bound to the exact design, crosscheck contract, resolver, viewport,
  browser version, screenshot hashes, and selection trace.
- Ten negative controls cover contradictory requirements, missing semantic refs,
  reused PIDs, clipping, backend timeout, unauthorized termination, absent providers,
  unscheduled dispatch, unsupported combinations, and forged geometry summaries.
- A fixture-only Python model demonstrates operation receipts, idempotency,
  provenance classification, port ownership with unknown states, and default
  AI redaction. It calls **no** real service or process mutator.
- Incomplete/unsupported obligations remain non-PASS even if all measured layouts
  look good. Human appearance judgment, live source parity, and operational
  authorization are separate future acceptance activities.
- Task Manager and Website Builder code are untouched.
