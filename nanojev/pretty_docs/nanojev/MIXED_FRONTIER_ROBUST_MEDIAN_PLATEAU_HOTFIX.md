# Mixed-frontier robust convergence hotfix

## Purpose

Resume the **same** `--phase samples --stage run` command, with the existing
mixed `working.pt`, optimizer, Qwen/LoRA weights, sampling order, reference
deck, configured 18 cells, frozen parent SHA, and best checkpoint preserved.
Only the mixed-phase stopping decision changes. This patch does not apply to
`--phase systematic`, `--phase glasses`, or any status/preview run.

**Important:** the installed mixed-frontier trainer source was generated
later than the available repository snapshot; this patch is intentionally a
conservative import-time adapter rather than a blind overwrite of the trainer.
It parses the installed mixed-pass source in memory and instruments its
unique `QWEN_MIXED_CHECK` event. It **does not require a stopping comparison
after that event in the same function**. The first revision of this hotfix
incorrectly required that shape and failed with
`ROBUST_PLATEAU_NO_UNIQUE_POST_CHECK_PATIENCE_GATE` on the user's installed
trainer. The revised implementation can handle a stop comparison before the
event, in a helper, or expressed with `state.get(...)`. It checks the actual
mutable patience state when the event runs, and fails closed if one cannot
be found unambiguously.
It never edits the trainer source or its checkpoints on disk.

## One-time install

```powershell
python new_patch.py "C:\Users\subsi\Downloads\nanojev_qwen_mixed_robust_plateau_resume_patch.zip"
```

The manifest-free ZIP supersedes the failed first revision and contains only an updated repository-local
`tools/__init__.py`, the compatibility helper
`tools/nanojev_qwen_mixed_robust_plateau.py`, tests, and this document.

## Resume: **identical command**

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase samples --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

At startup expect `QWEN_MIXED_ROBUST_PATCH={"status":"ACTIVE",...}` after the
mixed trainer loads. Each full 250-step reference check emits the original
`QWEN_MIXED_CHECK` plus `QWEN_MIXED_ROBUST_PLATEAU` metrics. If a different `ROBUST_PLATEAU_...` error appears, the actual installed
source or state is still incompatible. In that case supply
`tools/nanojev_qwen_lm_frontier_mixed_pass.py` so the adapter can be made
exact. Do not delete or reset the mixed run.

## Stopping rule

1. Use the existing 18-cell, equally weighted raw macro reference loss
   calculated by Qwen. The average across tasks is still the backbone
   learning objective. The normal best checkpoint-selection logic remains
   unchanged.
2. At each 250-step check, calculate arithmetic mean and median of 18 per-cell
   losses and the top-four fraction of total loss. These are diagnostics; the
   task median does **not** replace the aggregate training objective.
3. Calculate the rolling **median of five consecutive raw macro losses**
   (a 1,000-step span). Its rolling arithmetic mean is reported for comparison.
4. Reset patience only when this rolling median beats the previous best
   rolling median by **more than 2%**. Otherwise accumulate one stale check.
   After four consecutive stale checks (another 1,000 training steps), invoke
   the trainer's **existing** plateau-completion handoff. Require at least
   nine checks since this diagnostic became active.

This is an explicit, testable stopping criterion, not proof that optimization
has mathematically converged or the backbone has memorized the whole domain.
A transient difficult-task loss spike is less likely to create misleading
plateau oscillations. Truly sustained 2%-plus improvements reset the gate.

The helper writes only an independent checkpoint-safe sidecar file in the
mixed output directory:

`C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_mixed_v1\robust_plateau_history.json`

It uses atomic JSON replacement, checks for finite means, verifies that the
18-cell arithmetic mean matches the trainer's macro loss, refuses duplicated
or skipped reference intervals, and replays a check only once following a
restart. If `working.pt` lags the diagnostic sidecar after an interruption,
subsequent diagnostic records are automatically discarded and recomputed.
The original Qwen checkpoint and optimizer are never edited.

Because the earlier console loss history is not guaranteed to be stored in a
replayable checkpoint format, the new robust detector builds its **own fresh
five-check history** after installation. It does not backdate earlier
results or pretend to have evaluated checkpoints that the patch has not seen.

## Verification

14 CPU tests passed, including: spike rejection, median/mean arithmetic,
meaningful improvement resetting patience, crash/resume at the same step,
checkpoint file immutability, source-version refusal, AST future-import
safety, alternate patience-key handling, and replay of the real loss sequence
from the supplied October 30 mixed log. A separate end-to-end import integration test with a synthetic running
trainer also passed, including an old patience counter already near its stop
threshold and a stop comparison *before* the check event.

**Not tested here:** the user's currently installed generated mixed-pass source
or an actual CUDA run. Source-shape validation is therefore intentionally
fail-closed, with an actionable error rather than an unverified modification.

## Repair notes for this revision

The first revision was fail-closed and stopped during module import. Its
traceback happened **before** Qwen weights loaded and before any optimizer
steps, so the mixed training checkpoint was not modified by that failed
attempt. Install this replacement over the previous helper; keep the full
`--phase samples --stage run` command identical.

The new transformer also protects an existing old patience count (e.g. 3/4)
from triggering a premature transition before the first robust evaluation.
It recognizes threshold-gated `if`s anywhere in the mixed module, but leaves
the original finalization branch in control. The guard can only allow the
original finish path after the current process has seen robust convergence;
it will not trust a `reached` flag left by an interrupted prior process.
