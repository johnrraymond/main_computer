# MCEL Promise — Patch 7: Guarded Task Manager notebook laboratory

**Status:** bounded experimental host integration. The Task Manager promise is **INCOMPLETE**; this is not an approved replacement for its legacy notebook.

## Purpose

Resume the existing Task Manager as the primary application proving MCEL's promise. Attach the **shared MCEL tab renderer** to its actual host DOM under an explicit experimental opt-in. Mirror only read-only telemetry from the current Task Manager snapshot, preserve the working legacy notebook and operator controls, and prove refusal and exact rollback.

The checked-in `tools/fixtures/mcel_acid_tab_ir.json` is a **pinned independent generic IR witness**. Its embedded copy in `mcel-task-manager-tab-witness.js` is identical but **not** the missing original Phase 3 notebook DSL. The UI labels that limitation explicitly. Source-to-host continuity is still unverified and must not be claimed.

## Installation and execution

Apply the manifest-free Patch 7 ZIP with the repository's standard `new_patch.py` harness. Then, from the repository root, run:

```text
python tools/mcel_promise_gap_frob.py
python -m tools.mcel_task_manager_notebook_lab_acceptance
python -m pytest -q tests/test_mcel_task_manager_promise_frob.py tests/test_mcel_task_manager_notebook_lab.py
```

The first command writes `runtime/reports/mcel-task-manager-promise/report.json`, `barriers.md`, and a `lab-mount/` evidence directory. The dedicated second command writes `runtime/reports/mcel-task-manager-notebook-lab/report.json` plus screenshots. A `--no-browser` diagnostic retains `UNVERIFIED`, not PASS, for browser-dependent evidence. The Promise Frob continues to return `INCOMPLETE` even when the lab-mount acceptance passes.

To opt in manually in the normal Task Manager app, navigate to the canonical route:

```text
/applications/task-manager/server-processes?mcel_task_notebook=lab
```

The Task Manager router preserves that lab opt-in across its tab routes only, not across other applications. Without the exact opt-in, **no generated notebook is mounted**. Leaving Task Manager automatically removes the laboratory notebook. Its read-only bridge can also be unmounted from the browser console with `McelTaskManagerNotebookLab.unmount()`. The legacy tab DOM and its operational handlers remain unmodified.

## Contract boundary

`McelTaskManagerNotebookLab` is a narrow wrapper around public `McelTabWorkspace` and `McelViewTransition` APIs. It:

- requires exact `mcel_task_notebook=lab` opt-in and an existing Task Manager root and legacy fallback;
- accepts only validated IR with four known view identities, four declared snapshot sources, **no effects and no capabilities**;
- mounts a separate read-only tab workspace, without host tab/action handlers or URL synchronization;
- copies only `processes`, `all_processes`, `connections`, and `hardware` into the shared renderer; it does not copy server or schedule control data;
- receives successful and failed updates **after** Task Manager's existing refresh contract, and rejects stale sequence versions;
- leaves all machine control buttons, process-table actions, schedule operations, and AI controls with their original owners;
- preserves the existing notebook DOM byte-for-byte and restores it when the laboratory is unmounted.

The experimental CSS lifts monitoring ahead of the command rail at narrow widths **only while opt-in is active**. That is a controlled layout demonstration, **not** yet shared MCEL layout-engine authority.

## Evidence, limitations, and next barriers

Chromium's offline host-DOM acceptance exercises four widths (320, 390, 560, 1100), absence of side effects or network requests, missing opt-in refusal, malicious/effectful IR refusal, duplicate-mount refusal, read-only snapshot updates, keyboard tab navigation, out-of-order update refusal, failure state, and exact teardown. It uses the real app HTML/CSS and bundled scripts but **does not execute the production host's HTTP route**, its operational JavaScript, or actual telemetry requests. Therefore it cannot certify normal-route lifecycle integration, live telemetry parity, process control safety under live load, or appearance quality.

Next: recover the authentic Phase 3 notebook source; prove hosted-route lifecycle and telemetry parity with independent observation; then replace the lab-only responsive priority with actual shared-layout authority and test rollback. If broader generality needs a second application, use the existing File Manager. Website Builder stays untouched. Inventory and Work Orders remain historical benchmarks, not active development projects.
