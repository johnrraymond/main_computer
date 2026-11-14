# Raw-Qwen Accuracy Balance (after the loss plateau)

This is the **second convergence test**, not a replacement for the already-completed
systematic frontier and mixed loss-plateau runs. Loss convergence alone does
not establish that mathematical competency acquisition has balanced forgetting.

## Run

Use the identical original model checkpoint arguments and systematic output path,
but change `--phase samples` to `--phase balance`:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase balance --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

Reads frozen, selected checkpoint `qwen_lm_frontier_mixed_v1/glass_ready.pt`.
It creates its own `qwen_lm_frontier_mixed_balance_v1` directory with copies
of the parent, working and previous optimizer snapshots, best.pt, logs,
`competency_checks.jsonl`, `glass_hard_candidates.jsonl`,
`glass_replay_candidates.jsonl`, `pinned_cohort.jsonl` and, after verified balance,
`glass_ready.pt` / `glass_readiness.json`.

Run the **exact command again** after a graceful interruption to resume.
`--phase balance --stage status` reads the balance status without loading CUDA.
`--phase balance --stage preview` prints intended paths.

## What is actually measured

- Every result on the immutable pinned cohort and fixed anchors is evaluated
  against the same exact source and target on later checks.
- `gained` = wrong -> right; `lost` = right -> wrong. New cases never count
  as gains merely because Qwen solved them on first sight.
- A running pool of archived, *currently verified* misses is trained alongside
  correct-case replay and fresh frontier cases. Archived historical failures
  are always rechecked before being counted as failures.
- The native task generators are used for all nine classes. The two operand-
  scaled classes probe bands inside, at and beyond each L0/L1 boundary.
  Further checks introduce extra difficulty levels beyond L1, with native
  task scales where applicable; finite logic variants remain finite.
- `QWEN_BALANCE_CHECK` reports paired net growth, hardest-case archive size,
  frontiers explored and fixed-cohort score. The best raw backbone is promoted
  by **fixed-cohort exact accuracy**, not smoothed prediction loss.

## Accuracy-balance stopping rule

A successful check requires a configured minimum number of optimizer steps,
several checks into increasing difficulty levels, at least four consecutive
paired evaluations with nonpositive net change, and net gains no greater than
regressions in the recent window. This is **sampled evidence of balance**, not a
proof of a global capability limit. `--balance-max-steps` is a safety budget;
exhausting it reports BUDGET_LIMIT, *never* a false convergence.

Tunable optional flags:

- `--balance-check-every 250`
- `--balance-min-steps 1500`
- `--balance-window 4 --balance-patience 4`
- `--balance-anchors-per-cell 4 --balance-fresh-per-check 36`
- `--balance-replay-per-check 128`
- `--balance-expansion-every 4 --balance-max-extra-levels 3`
- `--balance-lr 3e-5 --balance-batch-size 4`
- `--balance-max-steps 0` (unlimited)

All balance training policy knobs are frozen on resume. Don't change them in
an existing workspace. Use a separate `--balance-output` for a changed policy.

## Glass stage gate

`--phase glasses` now insists on a completed Accuracy Balance handoff instead
of silently accepting the earlier loss-only checkpoint. The previous glass
experiment remains intact in its old output folder. **This patch does not
replace the glass trainer's separate task-level 75% classification scheme.**
That classifier still needs the individually tracked hard-case archive wired
into its actual training selection before any new glasses experiment claims
frontier expansion.
