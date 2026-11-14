# Qwen mathematical frontier — unified, resumable phase selection

## Purpose

Use **one existing command**, `tools.nanojev_qwen_lm_radius_ladder_frontier`,
with `--phase systematic|samples|glasses`. This patch is additive to the
existing ordered acquisition implementation: no modification to its training
loop, checkpoint schema, pinned training policy, or default behavior. Running
the historical command without `--phase` still means `--phase systematic`.

| Phase | Meaning | Train? | Restartable? | Checkpoint/output |
|---|---|---|---|---|
| `systematic` (default) | Ordered per-task mathematical acquisition until individual loss plateau | Yes | Yes, existing `working.pt` and `previous.pt` | `qwen_lm_frontier_v1` |
| `samples` | Balanced, deterministic random sampling across every trained mathematical task/level until macro-averaged reference loss plateaus | Yes, after Phase 1 completes | Yes, its own independent optimizer, RNG, sample cursor and `working.pt` | `qwen_lm_frontier_mixed_v1` |
| `glasses` | Rank-1 continuous glass Easy -> Mixed -> conditional Hard | **Yes**, after samples | Yes, separate A/B optimizer and best checkpoints per scheme | `qwen_lm_frontier_mixed_glasses_v1/` |

The *historical* A/B trainer is left unchanged. Phase 3 uses a new
frontier-compatible rank-one driver and the existing continuous A/B network
primitives, with stage-specific checkpoints and a shared immutable frozen
mixed-frontier backbone. See
`pretty_docs/nanojev/QWEN_FRONTIER_RANK1_EASY_MIXED_HARD.md` for exact CLI
commands, policy, classification and the conditional hard fallback.

## Install and continue Phase 1

Install this manifest-free ZIP with the normal `new_patch.py`. The ZIP contains
the **updated existing Phase 1 entrypoint**, the existing mixed-phase engine,
tests, and this runbook. It does not contain a new Phase 1 trainer or touch
any runtime checkpoints.

If Phase 1 is already running, leave it running. If stopped, resume using the
same command you used before, with or without explicit `--phase systematic`:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase systematic --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --seed-working "C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1\working.pt" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

The existing run's LoRA, optimizer state, task index, RNG, plateau history,
and recovery behavior are unchanged. `--phase systematic` is strictly a
selector; it does not enter the pinned training policy.

Inspect without loading Qwen:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase systematic --stage status `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

## Start or resume Phase 2 using the SAME script

**Only after Phase 1 has completed all its configured task-level cells and
produced `final.pt`**:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase samples --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

Here `--output` **always identifies Phase 1's directory**, even when
`--phase samples` is selected. Phase 2 writes to the separate sibling
`qwen_lm_frontier_mixed_v1` by default. Override with `--samples-output` if
you already started a mixed run elsewhere; point at **that exact existing
mixed run directory** to resume. Never point both outputs at the same place.

Phase 2 verifies Phase 1's `final.pt` is genuinely complete before copying
it once into `parent_snapshot.pt`. It continues Phase 1's trained LoRA,
AdamW moments/steps, backbone lineage and RNG snapshot; it does **not**
restart Phase 1 or reimport the older numeric ladder checkpoint. Phase 1's
original checkpoint remains immutable. Re-executing the Phase 2 command
resumes its own `working.pt` (including its sampled-example position).

Phase 2's independent policy defaults match the original mixed-pass engine:
`--samples-seed 91733`, `--samples-batch-size 4`, `--samples-lr 5e-5`,
`--samples-check-every 250`, `--samples-reference-per-cell 16`,
`--samples-plateau-patience 6`, `--samples-min-steps 1000`, and
`--samples-greedy-cases 6`. The default 18-cell deck has 288 fixed
reference questions, and aggregate plateau cannot be declared solely from a
step budget.

Phase 1's `--seed`, `--check-every`, `--lr`, and `--greedy-cases`
**do not** modify Phase 2 settings: the corresponding `--samples-*` flags
must be used. Common `--device`, parent checkpoint paths, and `--max-seq`,
`--max-tokens`, `--max-new-tokens` apply to both phases. Phase 2 log/save
and budget knobs also have `--samples-*` forms. For a resumed Phase 2 run,
changing sampled task policy parameters will trigger a policy-drift error.

Inspect sampling progress without touching the model:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase samples --stage status `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

A graceful Ctrl+C commits the most recent completed optimizer step; on
restart, an interrupted full reference/greedy check can be replayed without
resampling different cases. Every 10 steps / 60 seconds by default, the
sampler saves an independent full snapshot with an atomic backup. If the
sample budget is reached, it pauses without claiming that the aggregate
objective plateaued.

## Phase 3: Rank-1 cumulative glass scheme

Use the same CLI with `--phase glasses --glass-scheme easy|mixed|hard`.
The stage order is **Easy → Mixed (continuation of Easy) → Hard (conditional
fallback, branched from Easy)**. Both A/B candidates are trained and evaluated;
there is no sequential stacking of A+B at this stage. Phase 3 refuses to
start before mixed sampling produces `glass_ready.pt` and genuine plateau
readiness. Every stage has an independent checkpoint and resume history, plus
an optional `--stage audit` on fresh generated examples. See the dedicated
Rank-1 runbook for all commands and acceptance details.

## Diagnostics, boundaries, and tests

The independent cross-task retention audit remains the appropriate method
to measure mathematical acquisition and forgetting. **Teacher-forced
macro-loss plateau is not accuracy plateau.** Random sampling spans the
existing task generators; it does not invent mathematical families that those
generators cannot express. Glasses should use remaining errors across the
full mathematical frontier, not the original 240-case historical deck.

CPU tests cover backward-compatible Phase 1 entrypoint, mixed sampling
selection, safe phase directory isolation, mixed-run resume/recovery, full
reference plateau, preflight refusal before ordered completion, and the
rank-one glass handoff and cumulative Easy/Mixed/Hard scheme. These tests use fake Qwen weights; live CUDA
accuracy and throughput still require the actual run.
