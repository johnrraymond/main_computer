# MCEL Promise Closure — Patch 03: Source-Driven Application Composition

2026-10-30 — A **complete generated presentation shell** around the already
source-bound working notebook, with opt-in safety and exact legacy rollback.
This is the next major *partial* closure of MCEL's promise. It does **not**
claim that every Task Manager operation has been semantically admitted.

## What is built

The same `mcel_experiments/task-manager-notebook/application.js` used in Patches
01–02 now declares `semanticSurface.applicationShell`. That public semantic
structure includes sidebar and main workspace regions, overview metrics,
monitoring filters, a refresh capability control, server-operation
placeholders, read-only schedules, notebook, CPU/GPU summaries, and AI brief.

The generic `McelSemanticApplicationShell` interprets the region graph,
creates the responsive layout and DOM, renders summaries and collections,
retains form state, binds a declared external-read effect to the existing
Patch 02 executor, and accepts the generated notebook as a named slot.
There are no Task Manager IDs, selectors, backend calls or CSS specifics inside
that generic renderer. The browser-delivered IR is regenerated from DSL with
source-hash continuity. The new `mcel_semantic_application_shell.py` validator
checks shell semantics and undeclared action references before bundling.

Task Manager's narrow host adapter remains in
`mcel-task-manager-notebook-bridge.js`. It supplies actual snapshot data,
initial form values, and updates the existing underlying filter inputs that
`refreshTaskManager()` already reads. Legacy Task Manager continues to own the
backend and non-admitted machine operations; MCEL owns the **generated visible
shell and its read-only interaction lifecycle**.

## Activation

Only `?mcel_task_notebook=application` opts into the full-app candidate.
The previous `?mcel_task_notebook=candidate` notebook-only mode and
`?mcel_task_notebook=lab` pinned witness are preserved without promotion.
Without the parameter, the existing full Task Manager renders normally.

The generated application shell is inserted as the only visible application
presentation. The intact legacy shell is hidden and made inert, but it
continues producing data. Unmount restores the exact legacy DOM identity and
its previous hidden/inert/accessibility attributes. No copying of the legacy
HTML structure occurs in the MCEL core.

## Operational authority

- **Admitted:** Refresh process data (`external-read`), with pending/failure/
  completion and verified newer publication receipt; generated process query,
  row limit, connection inclusion and auto-refresh state configure the
  existing read-side Task Manager producer.
- **Not admitted:** server start/stop/restart; schedule create/delete/run;
  process termination; AI request generation. Those cannot execute via the
  generated shell. Server actions are explicitly disabled. Schedules and AI
  output are read-only projections.
- The existing Patch 02 limitation remains: a newer publication can be
  correlated with the request in browser runtime but is not a cryptographically
  unique HTTP transaction receipt.

## Source and execution path

```
application.js (one authored semantic source)
  -> official DSL compiler -> canonical application IR
  -> shell contract check + source fingerprint
  -> mcel-task-manager-notebook-candidate-ir.js
  -> McelSemanticApplicationShell (general regions, fields, layout, slots)
       -> McelTabWorkspace (existing generated notebook)
       -> McelIntentEffectRuntime (checked read capability / receipt)
  -> Task Manager host adapter (real data and existing refresh)
  -> accepted snapshot publication + incremental UI reconciliation
```

## Verification

```
python -m tools.mcel_task_notebook_candidate_bundle --check
python -m pytest -q tests/test_mcel_promise_wave3.py tests/test_mcel_promise_wave2.py tests/test_mcel_promise_wave1.py tests/test_mcel_task_manager_notebook_dsl.py tests/test_mcel_view_transition_composition.py tests/test_mcel_task_manager_notebook_lab.py tests/test_mcel_dsl_compiler.py
python -m tools.mcel_promise_wave3_frob
python -m tools.mcel_promise_wave3_frob --host-url "http://127.0.0.1:8765/applications/task-manager/server-processes"
```

The offline Chromium test uses 320/390/560/1100/1272-pixel viewports,
real code but controlled producer-shaped data. It proves source identity,
seven declarative regions, generated controls and receipt, filter binding,
sidebar/main responsive reflow, schedules/overview/hardware rendering,
stable process row and schedule DOM during live updates, keyboard/tab state,
non-admitted operations disabled, legacy rollback and previous notebook-only
mode unchanged. A separate structurally independent Notes schema exercises the
same general shell engine without Task Manager code.

**Hosted proof** is BLOCKED until the real Windows localhost Task Manager is
exercised. A passing offline fixture must not be represented as a verified
local-host success.

## Remaining promise gaps

1. **Operational application completeness:** admit schedule/AI/server/process
   management capabilities individually, with scoped authority, confirmation,
   verified outcomes and recovery. Do not bypass the executor.
2. **General authoring economy:** prove the new semantic shell dramatically
   reduces work for a second existing real application, not only a constructed
   independent schema.
3. **Design intelligence:** conduct comparative, independently scored visual
   quality and theme tests, not just responsive geometry checks.
4. **Per-request backend receipts** and broader hosted browser certification.

This patch closes **whole-shell presentation composition**, not the entire
application-authoring promise. The authoritative legacy runtime remains
available until further proof justifies promotion.
