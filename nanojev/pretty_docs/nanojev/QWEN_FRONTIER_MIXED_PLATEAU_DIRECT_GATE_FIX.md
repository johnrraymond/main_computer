# Qwen mathematical frontier: Phase 2 robust plateau direct-gate fix (V3)

## Summary

The previous V2 in-memory import hook reports robust medians, but the actual
Phase 2 trainer uses **`if plateau['plateau']`** from `_plateau_update()`, not
`if state['patience'] >= ...`. The import hook therefore could not reliably
control the true finalization decision. An apparently healthy
`QWEN_MIXED_ROBUST_PATCH=...ACTIVE` log only confirmed import, **not**
correct stopping behavior.

This V3 patch replaces the import hook with a native, **explicit** evaluator
call in `tools/nanojev_qwen_lm_frontier_mixed_pass.py`. It changes only the
criterion that sets `state['status']='PLATEAU'`. The original training,
sampling, loss calculation, saved weights, optimizer, RNG, source lineage,
separate best checkpoint, and `_finalize()` implementation remain unchanged.

It also replaces the `tools/__init__.py` import-hook installation with a clean
module docstring. This avoids duplicate metric processing and source
rewriting. The previous V1 sidecar is read and upgraded to V2 **in place**.

## Install

Stop the current CUDA training with Ctrl+C and allow its checkpoint handling
to finish. Do not run two trainers pointing at the same output concurrently.

```powershell
python new_patch.py "C:\Users\subsi\Downloads\nanojev_qwen_mixed_direct_robust_plateau_fix_v3.zip"
```

Reissue the existing command **unchanged**:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase samples --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

The **first evaluation after resume** reconstructs the prior robust-check
history using `qwen_lm_frontier_mixed_v1/mixed_history.jsonl` and reconciles
it with `robust_plateau_history.json` (if present). It counts earlier full
fixed-deck evaluations, not just the 6,500 / 6,750 / 7,000 evaluations from
V2. It refuses inconsistent parent lineage or missing/inconsistent check
intervals rather than silently resetting the stopping window.

## Decision rule

For every 250-step full reference evaluation of all 18 task/level cells:

- Compute the **arithmetic mean** of all task losses; this remains the training
  objective and the best-checkpoint selection criterion.
- Compute the task median and four-largest-task loss fraction as diagnostics.
- Form the rolling **median of the latest five macro means**.
- Reset the robust stale counter only when that rolling median improves by
  more than **2%** relative to the robust best median; otherwise increment it.
- Finalize after **four successive non-meaningful improvements** after the
  first full five-check window, and after the original `--min-steps`.

`QWEN_MIXED_ROBUST_PLATEAU` now contains actual stopping state. The
`QWEN_MIXED_CHECK` event also reports both `legacy_patience` and robust
`patience` explicitly. The old `_plateau_update` Boolean is diagnostic only.

**Important:** The historical data may already satisfy the robust criterion.
If so, the next full evaluation can finalize promptly; this is intentional
and does not truncate or reset the already-acquired weights. The existing
`best.pt` is copied to `glass_ready.pt`, with the same parent and best-step
checks as before.

## Verification and boundaries

Tests simulate the actual runner and optimizer checkpoint/restore behavior
entirely on CPU. They also exercise legacy-plateau suppression, V1 sidecar
migration, historical backfill, interrupted-check replays, loss-vector
validation, mean/median divergence, and the final `glass_ready.pt` lineage.
CUDA throughput/accuracy have **not** been measured by these tests. No files
under `C:\Users\subsi\NanoJev\runs\` are packaged or replaced.
