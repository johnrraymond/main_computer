# MCEL Promise — Patch 6: existing Task Manager first

**Status:** diagnostic integration and barrier discovery; no application promotion.

The primary real-world application is the **existing Task Manager**, not Inventory or Work Orders. The existing **File Manager/File Explorer** is the second independent subject only if a structurally different app becomes necessary. Historical Inventory and Work Orders promise-gap packets stay unchanged as benchmark evidence, not work orders to create new apps. Website Builder is untouched.

## Command

From the Main Computer repository root:

```text
python tools/mcel_promise_gap_frob.py
python -m pytest -q tests/test_mcel_task_manager_promise_frob.py
```

Generated outputs:

```text
runtime/reports/mcel-task-manager-promise/report.json
runtime/reports/mcel-task-manager-promise/barriers.md
runtime/reports/mcel-task-manager-promise/screenshots/*.png
```

The default Promise Gap command now routes to the existing Task Manager Frob; use `python tools/mcel_promise_gap_frob.py --focus historical` to run the unchanged, pinned Inventory/Work Orders benchmark. The standalone `python tools/mcel_task_manager_promise_frob.py` is also supported.

The first command returns success when the diagnostic instrument completes without a detected failure, but the report remains `INCOMPLETE` until the promise is actually established. Use `--require-promise` for a strict nonzero exit unless the full promise is demonstrated. `--no-browser` explicitly records browser evidence as `UNVERIFIED`, not as a successful rendering. `--output` can move the disposable artifacts elsewhere.

## What it checks

1. The genuine Task Manager HTML, styles and scripts have all four notebook tab/panel identities and existing operator controls. The host script is **inspected, never executed**. The static browser reference uses original HTML/CSS and an explicit test-only visibility activation because the host normally controls when the app is displayed. It cannot claim live task behavior or aesthetic approval.
2. The original frozen Phase 3 notebook source (`mcel_experiments/task-manager-notebook/application.js`), if present, compiles through the public MCEL DSL, emits the shared renderer, and is tested with **only synthetic read-only data**. Supply an original alternative path with `--notebook-source` and `--notebook-fixture` when needed. Invalid existing source is a `FAIL`. A missing source is `UNVERIFIED` (the standard test export can omit `mcel_experiments/`). **Never fabricate a new notebook and call it the frozen source.**
3. The pinned independent checked-in `tools/fixtures/mcel_acid_tab_ir.json` is tested through the actual shared `McelTabWorkspace` runtime as a bounded renderer witness when the original notebook is absent. It is *not* an authored Task Manager or normal-route application proof.
4. Chromium tests keyboard navigation, tab/panel selection, invalid-selection refusal, synthetic data refresh, absence of operational effects and network requests, plus viewport containment at 320/390/560/1100 px. The Frob records geometry and unapproved screenshots.
5. The report prioritizes observed missing live-mount integration, snapshot parity, layout authority, monitoring-first presentation, governed operator actions, independent appearance review, and authoring economy. Dependencies and uncertainty are explicit.

## Observations from the supplied repository export

The older Task Manager still uses `setTaskNotebookTab` and a passive `TaskManagerMcel` enrichment adapter. The Phase 3 generator uses a distinct MCEL tab workspace renderer and separate CSS. Neither the existence of the generator nor the host adapter proves that the actual hosted app is generated from the DSL. The exported repository lacks the original `mcel_experiments/task-manager-notebook` source and read-only sample; that is an *evidence gap*, not proof that the user's working tree lacks them.

The detached existing HTML/CSS reference shows the monitoring notebook starting close to the bottom of a short 320/390 px viewport, because command/scheduling controls occupy the earlier sidebar/stack. This is a concrete monitoring-first design obstacle, not a claim of production runtime layout failure.

## Barrier-removal order

1. Recover the actual frozen source in the test environment, without replacing it with another candidate.
2. Establish a guarded normal-host MCEL notebook mount under a reversible, read-only experimental path. Retain the legacy Task Manager as the stable fallback and independently prove teardown.
3. Bridge live snapshots using a **general** source adapter; prove ordering, unavailable states, and no mutation authority. Never let tab selection call PID/server/schedule/AI actions.
4. Give the shared MCEL layout engine authority over the monitoring-first hierarchy, and test long-running responsive presentation and rollback. Use existing semantic compression and information-quality capabilities where applicable.
5. After real behavioral parity, independently review uncorrected design quality and contrasting valid variants; then measure actual authoring work. Only then consider retiring the old tab plumbing.

This is a **Promise Frob**, not a replacement app or a claim of production certification. It preserves prior evidence and reports what remains to be built.
