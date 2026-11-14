# Qwen Mathematical Frontier — systematic randomized mixed pass

## Objective

After the ordered acquisition curriculum completes its task-specific plateaus,
continue acquiring mathematical competence using **one balanced, randomized
pass over every task-level cell**. Terminate only when the **whole frontier's
macro-averaged, fixed-reference teacher-forced loss** plateaus. Freeze the
strongest checkpoint for a *subsequent* A/B continuous-glass experiment.

This is a separate phase and a separate checkpoint directory. It does not
modify the existing ordered frontier trainer, numeric-terminal radius ladder,
finished source checkpoint, or either A/B glass trainer.

## Prerequisite and lineage

The ordered run must already have `final.pt` whose state says `COMPLETE`,
whose task index covers all of its configured tasks, and whose pending check
is false. `working.pt` from a run still in the middle of the curriculum is
**not** an acceptable mixed-pass seed. The new tool copies `final.pt` once
to `parent_snapshot.pt` in a sibling output directory and checks that the
original seed lineage is consistent when that seed file exists.

The training process continues the actual trained LoRA **and AdamW optimizer
state**, preserving the frozen Qwen parent weights and numerical-terminal
protocol. The mixed pass may use a new learning rate; the optimizer's
parameter state (moments, steps) is retained. Its original completed source
run is read-only. The A/B glass work remains separate.

## Systematic random sampling

For the default ordered curriculum (2 levels × 9 trainable families = **18
cells**):

- A randomized *permutation of all cells* is drawn for each 18-example epoch,
  deterministically from the run seed and epoch number.
- Every cell appears **exactly once per epoch**. Thus signed arithmetic,
  place value, operators, fractions/units, algebra, logic/graphs,
  coordinates/geometry, probability, and symbolic scaling receive equal
  representation at both trained levels, rather than appearing only by
  chance.
- Within each cell, a new deterministic pseudorandom large generator index
  yields a fresh supervised mathematical transformation. Generator modes
  cycle modulo 60 with a cell-specific seed offset, to cover the `%2`,
  `%3`, `%4`, and `%5` expression variants across epochs. This includes both
  positive and negative directed graph reachability templates and inner /
  outer boundary arithmetic patterns.
- Samples are computed on demand (no huge upfront error census, no finite
  240-problem pool). The sample cursor is saved for exact resume.
- The generator's mathematical *family* scope remains the existing nine
  families and configured levels. Random sampling does not by itself add
  new graph algorithms, conditional probability, advanced algebra, etc.

## Full-frontier loss and the end condition

Every 250 optimizer steps by default, evaluate a **stable, balanced reference
deck**: 16 questions for *each* task-level cell, **288 total** for the
default 18 cells. The 16-index run covers all five place-value scale modes,
all other documented index-modulo generator modes, and both positive and
negative graph-reachability examples. The reference set remains unchanged
from check to check and does not overlap the indexed acquisition stream.

Compute loss on each cell with the existing Qwen teacher-forced LM loss,
then macro-average the **18 cell means with equal weight**. This gives a
single, reproducible frontier-level optimization metric and a diagnostic
per-cell loss table. Use the existing corrected relative-or-absolute loss
improvement rule, with a 3-check moving average and 6 consecutive
non-improving full-reference checks, after a minimum 1,000 mixed steps.
Default threshold: minimum of 0.0005 absolute or 0.2% relative improvement.

The controller **never** claims a loss plateau due to hitting `--max-steps`;
that is a budget stop and resumes if a larger budget is supplied. There is
no 100%-accuracy requirement and no premature glass training.

During each full check, emit reference progress every three cells and greedy
progress every two cases so the run does not appear stalled.

Small greedy tests are interleaved during full-reference checks (default 6
fresh questions, rotating across cells), and observed incorrect completions
are appended to `mixed_discovered_errors.jsonl`. These are observations,
not a curated glass training dataset.

A reference-loss plateau is **not** proof that mathematical accuracy,
transfer, or retention has plateaued. The fixed reference is a monitoring
deck, not a DEV split or a certification; use the independent cross-task
retention / novel-question audit before making generalization claims.

## Installation

Install the **manifest-free ZIP** with the existing `new_patch.py`.
The patch adds only:

- `tools/nanojev_qwen_lm_frontier_mixed_pass.py`
- `tests/test_nanojev_qwen_lm_frontier_mixed_pass.py`
- `pretty_docs/nanojev/QWEN_MATHEMATICAL_FRONTIER_MIXED_PASS.md`

The ordered trainer need not be stopped to install this new file; this second
phase **will refuse to start until the ordered trainer has finished**.

## Run once the ordered phase finishes

```powershell
& $py -m tools.nanojev_qwen_lm_frontier_mixed_pass `
    --stage run --device cuda `
    --frontier-output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1" `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_mixed_v1"
```

This is the only training command. Repeating it with the same training
policy resumes from the independent mixed `working.pt`, **not** from the
original frontier `final.pt`. `--stage preview` shows the first balanced
sampling epoch after the source is complete; `--stage status` reads the
mixed checkpoint with no model load.

```powershell
& $py -m tools.nanojev_qwen_lm_frontier_mixed_pass `
    --stage status `
    --frontier-output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_mixed_v1"
```

`--max-steps N` is an optional diagnostic budget, *not* a forced plateau.
Changing `--save-every`, `--save-seconds`, `--log-every`, and `--max-steps`
is operational and does not change the pinned sampling/loss policy.
Changing `--seed`, learning rate, batch size, check cadence, case counts,
plateau thresholds, or the model's evaluation protocol will explicitly
refuse a resumed run rather than silently change what was being measured.

## Snapshots, reports, and glasses handoff

All outputs are in the **mixed** directory:

- `parent_snapshot.pt`: immutable copy of the completed ordered frontier.
- `working.pt`: full resumable LoRA, optimizer, RNG and sampling/plateau
  state, saved every 10 steps or 60 seconds by default.
- `previous.pt`: prior complete snapshot used if `working.pt` is damaged.
- `mixed_progress.json`, `mixed_history.jsonl`: state and per-check full
  macro/per-cell loss trends.
- `mixed_discovered_errors.jsonl`: append-only fresh greedy failures.
- `best.pt`: full checkpoint at the strongest **smoothed whole-frontier**
  reference loss (not necessarily the last step).
- **Only after genuine mixed-reference plateau:** `glass_ready.pt` (copy of
  `best.pt`) and `glass_readiness.json` with the selected step and loss.

`glass_ready.pt` contains a **new mixed-phase checkpoint schema**. It is
ready as a *source of frozen backbone/LoRA weights* for a future A/B glass
adapter. The old A/B trainer, which expects the original numeric-terminal
ladder `working.pt` schema, cannot consume it unchanged. This patch does not
attach, train, or select either glass.

These milestones say **mixed-loss saturation**, not an accuracy certificate.
A cross-task accuracy/retention audit should compare the original ordered
checkpoint to `glass_ready.pt` on new problems prior to glass training.

## Verification limits

CPU tests exercise deterministic balanced sampling, generator mode coverage,
full-checkpoint isolation, resume, source immutability, per-cell references,
recovery from damaged latest snapshots, plateau vs budget stops, and glass
handoff. They use a fake frozen parent and small LoRA tensors. They are **not**
a real Qwen/CUDA training result; no empirical learning outcomes are asserted.
