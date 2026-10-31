# MCEL Promise Closure — Wave 0/1A: source-bound Task Manager notebook candidate

2026-10-30 · **Implementation with bounded evidence, not MCEL promise fulfillment**

## Purpose

Use the existing official vanilla-JS MCEL DSL, canonical IR compiler, notebook
projection, generic tab workspace, semantic process collection and live Task
Manager producer to instantiate **one real host notebook slot** from authored
semantics. Keep normal Task Manager unchanged, preserve the original operator
controls, and retain a reversible one-step fallback.

## Provenance, decisive distinction

The repository snapshot did **not** contain the historical Phase 3
`mcel_experiments/task-manager-notebook/application.js`. Its original contents
and identity have **not been recovered**. This patch creates a **new candidate**
from the documented public semantic contract and independently pinned witness.
It is an authored source file, not a historical recovery. It lives outside
`mcel_apps/`, is not a canonical packaged application, and is not promoted.
The fixture is entirely **synthetic**, not saved real machine telemetry.

Candidate authority is traceable:

```
mcel_experiments/task-manager-notebook/application.js
  -> main_computer/mcel_dsl_runtime.js  (official DSL builder)
  -> main_computer/mcel_dsl_compiler.py (canonical IR validation)
  -> tools/mcel_task_notebook_candidate_bundle.py (source hash + exact bundle derivation)
  -> main_computer/web/applications/scripts/mcel-task-manager-notebook-candidate-ir.js
  -> mcel-task-manager-notebook-bridge.js (candidate-only opt-in + read-only streams)
  -> mcel-tab-workspace.js (generic semantic tab workspace)
  -> mcel-semantic-collection-workspace.js (generic keyed row rendering)
  <- existing task-manager.js / terminal.js snapshot producer
```

The generated candidate IR delivery file is a static browser bundle required
by the current HTML include pipeline. **Do not edit it manually**. After
changing the authored source, regenerate and verify it:

```
python -m tools.mcel_task_notebook_candidate_bundle --write --check
python -m tools.mcel_task_notebook_candidate_bundle --check
```

The builder refuses bad/operational IR, includes source SHA-256 and compiler
fingerprints, and the browser test refuses a stale generated artifact.

## What actually changes

- Existing `?mcel_task_notebook=lab` stays a separate pinned-witness mirror.
- New `?mcel_task_notebook=candidate` uses compiler-derived *new authored* IR.
- The candidate creates its own generic tab/panel DOM inside the **original
  Task Manager notebook slot**, not another hand-authored tab interface.
- The retained legacy notebook is `hidden`, `inert`, `aria-hidden` in candidate
  mode; normal Task Manager, lab mode and operational controls are not modified.
  The old notebook stays in memory for instant rollback and continues to be
  updated by the existing host, which remains the data authority.
- On unmount or navigation, the candidate destroys its MCEL workspace and
  restores the legacy notebook's original attributes exactly.
- Only the local typed selection transition and read-only snapshot updates are
  permitted; no server, PID, scheduling or AI capability is introduced.
- `app-routing.js` preserves candidate opt-in across Task Manager's canonical
  tab routes only. Nothing persists the flag in storage.
- No changes to hover mechanics, generic MCEL runtime, production backend,
  or the existing Task Manager operator actions.

## Tested, not assumed

### Source and offline browser

```
python -m tools.mcel_task_notebook_candidate_bundle --check
python -m tools.mcel_notebook_preview --check
python -m pytest -q tests/test_mcel_promise_wave1.py tests/test_mcel_task_manager_notebook_dsl.py tests/test_mcel_view_transition_composition.py tests/test_mcel_task_manager_notebook_lab.py
python -m tools.mcel_promise_wave1_frob
```

The focused gate checks 320, 390, 560, 1100, 1272 pixels; source identity;
invalid/synthetic IR refusal; no duplicate accessible notebook; real repo
Task Manager host markup and generic MCEL runtime; stream delivery; semantic
PID rows; measured zero versus unavailable; reorder, addition, retirement,
value updates; stable DOM identity and focus; local tab selection; stale
revision refusal; single mounting; exact legacy fallback; absence of browser
errors, network fetches and operational effects; visible generated UI and
no horizontal document overflow. Browser data are deterministic producer-shaped
snapshots, not actual hosted HTTP responses.

The previous lab acceptance, semantic pipeline, and offline live-lifecycle
Frobs remain regression gates. The standalone Promise Gap report now
recognizes the *new* candidate source but continues to report overall promise
`INCOMPLETE` and does **not** certify historical source recovery.

### Live hosted check — **not run here**

When the real Main Computer Task Manager is running, use its actual local URL:

```
python -m tools.mcel_promise_wave1_frob --host-url "<your real Task Manager URL>"
```

The tool appends `mcel_task_notebook=candidate` and checks real HTTP 200,
original producer PID identity parity, actual refresh-version progression,
rendered identity parity, real delivery receipts, source hash, and browser
errors. Without a running host URL, `hosted=BLOCKED` is intentional and must
not be reported as PASS. No machine-changing operations are executed by the
Frob. The normal sidebar still contains legacy operator controls; do not
confuse candidate read-only presentation with disabling the original host's
separate operational controls.

## Authoring economy baseline

- New semantic candidate source: **215 authored lines**, including explicit
  tab/control/region declarations; the earlier documented 121-line baseline
  belongs to unavailable historical source and is not comparable evidence of
  reduction.
- Shared renderer, typed transition, semantic collection, layout CSS: reused,
  **no Task Manager special cases added to generic MCEL core**.
- Task Manager domain-specific data mapping: existing process-contract file,
  unchanged. Real Task Manager producer: unchanged.
- Browser-delivered candidate IR is generated and must not count as manual
  author work; separate domain semantics and composition scaffolding must still
  be reduced and measured against an equivalent correctly operating feature.

## Remaining promise gaps and next wave

1. **Hosted live producer parity not yet proven** for this candidate. Run the
   hosted gate on the developer's actual Task Manager instance.
2. **The full application is not semantically authoritative.** The generated
   notebook is the candidate's sole notebook UI, but sidebar, server controls,
   schedules and AI panels remain host-authored. Do not promote this to the
   claim that MCEL built the entire Task Manager.
3. **No operational intent/effect/receipt path is closed** by this patch.
   Select a safe read-only refresh/status capability as the next shared
   integration contract, with independently observable receipts.
4. **Shared layout does not yet govern the whole shell.** Parent hierarchy and
   sidebar remain the legacy layout; eventually demonstrate shared layout
   authority and attractive defaults with no Task Manager-specific core rule.
5. **Do not claim historical Phase 3 source recovery.** If the real historical
   source reappears, compare separately. Do not silently substitute this new
   authored source for the user's original decisions.
6. **Second-app generality and authoring-economy proof are still open.**

The next patch should extend the *same source-to-runtime boundary* to safe
operational read-intents and real hosted evidence, rather than building an
unrelated UI subsystem.
