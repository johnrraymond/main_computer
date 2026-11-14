# Qwen Frontier Rank-1: One-sided baseline classification fix

## Symptom

After `GLASSES_BASELINE_PROGRESS={"tested":288,"total":288}`, an Easy-stage run raised `RuntimeError: GLASSES_NEED_BOTH_EASY_AND_HARD_CELLS`.

## Cause and correction

The frozen Qwen baseline is scored using **greedy-correctness** on 216 training and 72 validation cases from 18 task/level cells. The existing threshold is 0.75 correct **per cell**. All 18 cells can legitimately fall on the same side of this threshold; that is not a failed model load, a missing checkpoint, or license to invent failure cases. The former runner nevertheless demanded nonempty Easy and Hard sets.

This patch keeps the threshold, the row generation, the saved classification, frozen backbone, checkpoint policy, training and scoring unchanged. It permits zero cells in either group and emits `GLASSES_BASELINE_CLASSIFICATION`, with actual Easy/Hard cell counts and baseline-correct/error counts for both decks.

- **All Easy:** Easy trains on any **actual** residual errors among Easy cells. Mixed can continue cumulative correction, but it cannot claim a Hard-cell accuracy gain. Hard stage is explicitly skipped with `NO_HARD_CELLS_IN_BASELINE` even when forced.
- **All Hard:** Easy records its baseline/best unmodified heads (no invented Easy training errors). Mixed can train on genuine Hard errors and still reports regressions. The existing force behavior is preserved when real Hard cells exist.
- **No errors:** A stage with no matching genuine residual mistakes records `no_residual_training_errors` and does no corrective steps. No fake errors are created to keep the experiment busy.

The previously written `classification.json` and append-only `baseline.jsonl` remain valid. Repeating the original command reuses completed baseline records; it must not rescan the 288 questions.

## Command (unchanged)

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase glasses --glass-scheme easy --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

Look for `GLASSES_BASELINE_CLASSIFICATION` after the cached-deck progress; its `classification` is `split`, `all_easy`, or `all_hard`. Check `train_errors` and `validation_errors` to determine whether there is actual residual work. The trainer still must complete its rounds or record `no_residual_training_errors` before proceeding to Mixed. Do not interpret a zero-error subset as evidence that additional unseen mathematics is solved.

## Scope

Only `tools/nanojev_qwen_lm_frontier_rank1_glasses.py` changes in the executable code. The Phase 2 optimizer, `glass_ready.pt`, original `backbone_snapshot.pt` and all historical Mixed training data are untouched. CPU-only regression tests cover a cached 288-row census and fully mocked Easy → Mixed → Hard trajectories under both one-sided classifications. CUDA glass training is not certified by these CPU tests.
