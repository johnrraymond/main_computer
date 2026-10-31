# MCEL Promise Closure — Patch 02: State → Intent → Capability → Receipt

2026-10-30 · Source-bound, opt-in, non-destructive operational slice.

## Objective

Patch 01 compiled a new Task Manager notebook DSL into a genuine browser-run MCEL
notebook and displayed real host snapshots. Patch 02 adds one **safe external-read**
operation declared in the same DSL: **Refresh process data**. MCEL creates the
button, owns pending/refused/failed/completed presentation, and requires an
observed, accepted snapshot publication before declaring completion.

This does not claim MCEL has generated the entire Task Manager, reconstructed the
historical Phase 3 source, or acquired server/scheduler/process mutation authority.

## Production path (candidate only)

```text
mcel_experiments/task-manager-notebook/application.js
  -> official DSL compiler and canonical application IR validator
  -> source-hash-checked generated browser artifact
  -> mcel-tab-workspace.js (generic generated action UI)
  -> mcel-intent-effect-runtime.js (generic checked capability dispatch)
  -> mcel-task-manager-notebook-bridge.js (explicit host adapter)
  -> existing refreshTaskManager() in terminal.js
  -> existing Task Manager HTTP snapshot producer
  -> existing bridge.publish() and generic semantic collections
  -> observed revision + accepted delivery receipt
  -> generated operation status and retained row identity
```

- **No new backend endpoint** or machine-changing operation.
- **No global automatic enablement:** `?mcel_task_notebook=candidate` is required.
- `?mcel_task_notebook=lab` remains pinned, read-only, no action button.
- Candidate requires the exact source-bound IR object and standard Task Manager root.
- The offline notebook preview has no host adapter and renders a disabled action.
- The original notebook remains intact and returns on route/unmount.
- Core MCEL runtime has no Task Manager selectors or HTTP URLs; only the host
  adapter knows the existing `refreshTaskManager` function.

## Evidence contract

The shared executor checks the IR control, intent, effect, capability, operation,
risk, cardinality, and receipt-evidence relationships. It will dispatch only
`external-read` capabilities injected at mount. Unrecognized actions, missing
adapter, malformed risk, and broken IR references refuse. One outstanding
request per executor is admitted; no implicit retries.

The actual adapter must complete. The executor samples a source version before
and after the call, and requires **a greater safe-integer version and an
accepted delivery receipt of that revision**. A call that merely dispatches
cannot generate a completed receipt. Failure, stale result and loss of
capability are distinct outcomes. Unmount invalidates pending attempts and
never writes a stale response into a new view.

**Current limitation:** The version/receipt is an accepted publication from the
shared Task Manager producer. An unrelated concurrent producer refresh could
also advance that revision, so it is not a cryptographically correlated
per-request HTTP transaction receipt. A future backend request identifier
would close that narrower provenance gap. A partially failed secondary stream
can still produce a completed refresh with `deliveryStatus=partial`, which must
not be interpreted as complete data parity.

## Tests

```
python -m tools.mcel_task_notebook_candidate_bundle --check
python -m tools.mcel_notebook_preview --check
python -m pytest -q tests/test_mcel_promise_wave2.py tests/test_mcel_promise_wave1.py tests/test_mcel_task_manager_notebook_dsl.py tests/test_mcel_view_transition_composition.py tests/test_mcel_task_manager_notebook_lab.py tests/test_mcel_dsl_compiler.py
python -m tools.mcel_promise_wave1_frob
python -m tools.mcel_promise_wave2_frob
python -m tools.mcel_promise_wave2_frob --host-url "http://127.0.0.1:8765/applications/task-manager/server-processes"
```

The offline Frob tests actual generated UI at 390/1100/1272 px, with a controlled
synthetic producer-shaped adapter: a real completed promise, snapshot update,
identity/focus continuity, stale result, backend exception, single flight,
unknown/missing capability refusal, rollback during pending, and inert legacy
lab. It does not contact a Task Manager server. Hosted proof is `BLOCKED`
without the user's running localhost host. Do not misreport offline as hosted.

## Next objective

Expand shared composition to the full Task Manager shell, including semantics,
authority boundaries, and global layout. Promote only after actual hosted
refresh and additional independent application/adaptor evidence.
