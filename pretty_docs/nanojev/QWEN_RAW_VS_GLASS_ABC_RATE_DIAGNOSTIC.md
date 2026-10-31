# Qwen raw vs. Glass A/B/C information-rate diagnostic

This diagnostic is separate from production Accuracy Balance and never edits
its source checkpoint. The A/B/C names identify three different **glass**
branches. Raw Qwen is the common control.

- **Raw:** continue source Qwen LoRA training with its persisted optimizer.
- **Glass A:** ungated continuous residual, zero-initialized.
- **Glass B:** gated continuous residual, zero-initialized.
- **Glass C:** the existing `nanojev_qwen_lm_failure_mode_glass.FailureModeGlass`
  activation-local mode router and `train_one_token` optimizer, adapted to the
  four features already emitted by the frontier decoder. Each matched mode has
  its own correction vector. Unmatched activations receive exactly zero
  correction; ambiguous signatures demanding different gold tokens are
  reported instead of overwritten.

## Why v2

The earlier v1 run on a 74-problem evaluation found zero net raw acquisition,
negative net A/B acquisition, and very few initially incorrect evaluation
problems. v2 fixes important implementation deficiencies without silently
changing the user's accepted objective:

1. All branches start from the **same copied checkpoint** and use the **same
   disjoint training and evaluation decks**. A/B/C remain architecture-specific
   optimizers; do not interpret an optimizer step as an equal unit of compute.
2. Glass A/B cache frozen teacher-forced hidden features lazily and reuse them
   across optimization steps, rather than doing full greedy generation for
   every training update. **Feature capture time counts against the branch's
   training-time budget**. Frozen teacher-forced states are not the same as
   changing autoregressive paths, so the external scoring remains full greedy.
3. A/B replay uses a gold-token **classification-margin loss**, not solely
   the squared residual. Several gold positions are visited over the stream.
   Small corrections can still cause regressions; only greedy audits prove
   mathematical competency. There is no zero-regression hard gate.
4. C trains the **first wrong actual greedy token**, confirms correctness
   through subsequent greedy decoding, and handles a second wrong token when
   budget permits. It reports local modes and token optimizer steps.
5. The default evaluation is larger and expands problem difficulty further.
   A warning is emitted when initial evaluation errors are too few for a
   meaningful information-rate comparison. The deliberately enriched archive
   cohort is reported separately from new generated problems.
6. Every branch has an independent resumable checkpoint. Only the source
   snapshot is shared. The A/B frozen-feature cache and C local vectors are
   checkpointed. A new v2 output directory prevents accidental resumption of
   the incompatible v1 experiment.

## Quick preview (no GPU model loading)

```powershell
& $py -m tools.nanojev_qwen_lm_frontier_rate_diagnostic `
    --stage preview --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt"
```

## Full calibration

Pause any competing GPU training before running the experiment, then:

```powershell
& $py -m tools.nanojev_qwen_lm_frontier_rate_diagnostic `
    --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --budget-seconds 120 --check-seconds 40
```

Output: `C:\Users\subsi\NanoJev\runs\qwen_lm_rate_comparison_abc_v2`.
New default evaluation per task/level: four original-level and six expanded
level cases (plus 32 reserved archive evaluation cases when available).
`--extra-levels 2` applies level expansion to each task's native generator;
this is not necessarily a literal operand-radius change for all tasks.

Use `--source-checkpoint` and `--balance-output` explicitly if the desired
checkpoint and archive do not follow the default workspace layout.
On restart, use the *identical command* to resume; policy/lineage changes
require a new `--output` directory. The old v1 output is not overwritten.

## Score interpretation

Each branch logs `QWEN_RATE_CHECK`, with `gained` (wrong→right), `lost`
(right→wrong), `net`, `net_per_gpu_hour_reserved` on *the same fixed evaluation
cases*. Branch C also logs `local_modes` and `local_token_steps`; A/B log
`cached_sites`. Final report: `report.json`, `QWEN_RATE_DONE`.

`net / training_seconds` is a **sampled rate**, not a population information
theory quantity or a proof of a structural advantage. Each candidate shares
case IDs and time budgets; greedy evaluation and checkpoint I/O are excluded
from this reported **training** time. The separate `total_branch_wall_seconds`
includes per-branch scoring and overhead, which can be especially important
for activation-local inference.

Raw training is not automatically stopped or switched by this diagnostic.
Interpret comparisons using actual net gains and losses, attention to the
harder fresh frontier, sufficient evaluation headroom, and repeated runs from
more than one raw-Qwen source checkpoint.

## Known limitations

- Cache-building overhead is charged; however checkpoint I/O and full greedy
  evaluations are not charged to `net_per_gpu_hour_reserved` (only to the
  reported total branch wall time).
- The strict local activation threshold may reject similar but distinct
  problems, restricting generalization. High accuracy on exact archived
  examples is not evidence of general mathematical competence.
- A/B gradients are learned on teacher-forced frozen features, but greedy
  evaluation uses the glass-altered autoregressive trajectory.
- It does not yet benchmark an integrated *sequential stack* of many glasses.
- Real CUDA performance must be measured on the target machine. CPU unit
  tests verify logic, not throughput or statistical significance.
