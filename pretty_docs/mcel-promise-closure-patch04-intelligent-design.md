# MCEL Promise Closure — Patch 04: Shared Intelligent Design and Authoring Economy

Date: 2026-10-30. Status: **implemented, offline mechanical checks passing; final promise gate incomplete**.

## What it changes

Patch 04 adds one general presentation planner to `McelSemanticApplicationShell`.
The planner reads only the public canonical IR's semantic region `kind`, `zone`,
`items`, `columns`, `fields` and `actions`. It derives region roles, prominence,
semantic complexity and sidebar demand. It contains **no Task Manager names,
control IDs, selectors or effect permissions**. It does not introduce a new DSL,
compiler, IR, or application-specific copy of the visible shell.

The shell now has two distinct visual treatments from the *identical* source:

- **Operational console:** compact, dark, monitoring-first, sidebar at left,
  tight telemetry and operational scan density.
- **Analytical workspace:** spacious, light, content first, main pane at left,
  auxiliary rail on right, increased reading scale, different table density,
  panel hierarchy and accessible form presentation.

The presentation switch is generic, available in the generated heading, and
preserves the original DOM nodes, live process rows, selections, focus, input
state and checked-effect runtime. A host can also request the analytical mode
with `?mcel_task_notebook=application&mcel_design=analytical` (or the operational
mode with `mcel_design=operational`). This URL parameter chooses *only*
presentation; it does not admit capabilities. Other candidate and lab modes are
unchanged. No application-owned source or server operation changes are necessary
to obtain the second design.

## Reproducible evidence

```
python -m pytest -q tests/test_mcel_promise_wave4.py
python -m tools.mcel_promise_wave4_frob
python -m tools.mcel_promise_wave4_frob --host-url "http://127.0.0.1:8765/applications/task-manager/server-processes"
```

Wave 4 runs ten browser cases (two treatments × 320, 390, 560, 1100 and
1272 px), captures four comparison screenshots, tests generated Refresh and
receipts, continuous process/schedule DOM identity, input and keyboard-focus
preservation across live updates and mid-session design switching, explicit
non-admission of machine actions, and absence of viewport horizontal overflow.
It also tests a *different* semantic application (Field Journal) using the
same public shell, plus a real 1,600-character unbroken data value in narrow
conditions. Hosted mode checks real source/render process parity and Refresh
in both treatments without allowing destructive operations.

Reports are stored in `runtime/reports/mcel-promise-wave4/report.json` and PNGs.
The Frob returns nonzero if any requested hosted test fails, never silently
converting a failed hosted run to a PASS.

## Authorship and provenance

No second `application.js` or domain adapter is required to obtain the second
presentation: the number of *additional* app-specific semantic definitions,
CSS patches and adapters is zero. The report counts declared shell regions,
fields, read bindings and actions as a reproducible, bounded structural metric.
It **does not** infer hours saved or a general reduction in author decision cost
from source-line counts. Independent, equivalently scoped measurement of manual
labor and corrective iterations is still required to satisfy the original
Wave 4 authoring-economy gate.

**Export limitation:** `main_computer_test-20261030-133759.zip` includes the
browser-delivered candidate IR but omits the author-owned
`mcel_experiments/task-manager-notebook/application.js`. The Patch 04 Frob
reports source-bound rebuilding as **BLOCKED**, rather than generating a new
source or falsely marking the historic Phase 3 source recovered. On the user's
full working tree, this gate will run the existing canonical build/check.

**Aesthetic review:** Browser geometries, screenshots and regression checks
are not a substitute for independently judged aesthetics. Human visual review
remains **UNVERIFIED** until explicitly conducted and approved.

## Preserved contracts and limitations

- Same single semantic IR, `mcel.semantic-application-shell.v1`, official DSL,
  existing renderer, exact legacy rollback, original four notebook datasets.
- Same externally authorized read-side Refresh, pending/completed/failed/refused
  state and receipt validation; no newly admitted server/scheduling/AI actions.
- Same legacy source producer and source-owned host adapter; no backend mutation.
- Existing hover/scroll geometry and previous Wave 1–3 tests are regressions.
- Presentation planner assigns generic roles without claiming to be a full
  autonomous aesthetic designer. In particular, the two styles are curated
  general defaults; future semantic diversity is the Wave 05 falsification
  gate, not a demonstrated general design theorem.

## Gates

| Gate | Meaning | Expected current status |
|---|---|---|
| Offline dual-design/browser mechanics | 2 profiles × 5 widths + lifecycle + reuse + stress | PASS if test succeeds |
| Hosted operation and parity | Real Task Manager data + receipt in both designs | BLOCKED until Windows run |
| Source-authority rebuilding | Exact authored `application.js` -> IR -> browser artifact | BLOCKED in this exported snapshot |
| Human visual quality | Independent coherent/aesthetic review | UNVERIFIED |
| Authoring economy | Compared equivalent manual decisions, correction iterations and labor | PARTIAL; controlled second-design incremental effort measured |
| Original full Wave 4 promise | All preceding gates | INCOMPLETE pending missing evidence |

The patch is a useful implementation increment without misclassifying mechanical
quality checks as full fulfillment of MCEL's original product promise.
