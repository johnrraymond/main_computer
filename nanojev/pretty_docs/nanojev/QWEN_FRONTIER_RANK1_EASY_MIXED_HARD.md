# Qwen frontier: Rank-1 continuous glasses (A/B), cumulative three-stage experiment

The `tools.nanojev_qwen_lm_radius_ladder_frontier` command selects a restartable
**training phase**, not a second independent run of the historical 240-case
numeric-terminal benchmark. It retains the current ordered-acquisition and
balanced-random-sampling algorithms unchanged.

1. `--phase systematic` (existing, default): ordered 18 task/level cells,
   task-specific reference-loss plateau, independent original working/backup.
2. `--phase samples`: systematic balanced randomized acquisition across all
   configured cells, macro-reference-loss plateau, separate working/backup.
3. `--phase glasses`: freeze the **best** snapshot from Phase 2 and train both
   first-layer glass candidates in cumulative **Easy → Mixed → Hard (fallback)**.

### Glass schemes and handoff

| Scheme | Starts from | Training pool | Ends when |
|---|---|---|---|
| `easy` | Two new, zero-residual A/B networks on frozen Qwen | Original-Qwen failures in easy cells, plus successes for preservation | Validation plateaus or configured round budget |
| `mixed` | Individually selected `easy/best_A.pt` and `easy/best_B.pt`, including AdamW state | Balanced Easy + Hard failures, with deliberate replay of Easy-acquired corrections | Validation plateaus or configured round budget |
| `hard` | **Branches from Easy** A/B best checkpoints, never destroys Mixed | Hard failures with Easy replay | **Only if Mixed fails its success gate** (unless `--glass-force-hard`) |

The two Rank-1 A/B candidates **are not applied serially together**. A is the
ungated continuous residual; B is the learned soft-gated continuous residual.
A multi-layer glass stack is a *future* experiment. Neither head trains Qwen.

A mixed candidate satisfies the operational skip-Hard gate if, on the **fixed
validation deck**, it fixes at least one additional Hard problem relative to
its respective Easy predecessor, while retaining at least 95% of Easy correct
answers. Either A *or* B passing skips the Hard fallback by default. The gate
is a test-workflow decision **not a certification of generalization**. Use
`--stage audit` on genuinely fresh questions before treating the result as
transferable mathematics; if that independent result is inadequate,
`--glass-force-hard` can still run the fallback.

Easy/Hard are classified per *task/level cell* using frozen Qwen's actual greedy
accuracy on a seeded baseline deck (default 12 problems per cell; Easy >=75%).
Loss alone is not used as the classification criterion. Validation is a
separate fixed deck (default 4 per cell) and is used for glass checkpoint
selection. A third seed/index region is reserved for an independent fresh
audit. The fresh audit **excludes prompts identical to any training or
validation prompt**, which matters for finite Boolean/graph generators.

### Install

```powershell
python new_patch.py "C:\Users\subsi\Downloads\nanojev_qwen_frontier_rank1_cumulative_phases_patch.zip"
```

The ZIP is manifest-free and **supersedes** the earlier unified-phase/radius
visibility patch. You may install while the original Phase 1 process runs;
no current runtime checkpoints are modified. It relies on the already
installed `tools.nanojev_qwen_lm_deformation_smoke` **continuous** glass A/B
primitives, and does not overwrite that historical experiment.

### CLI: same entrypoint for all three phases

The parent checkpoints required for CUDA runs are:

```powershell
$phaseArgs = @(
    '--device', 'cuda',
    '--stage1-checkpoint', 'C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt',
    '--text-checkpoint', 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt',
    '--output', 'C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1'
)
```

The current `--phase systematic --stage run` command, and command without
`--phase`, still resume the ordered frontier from the original checkpoint.
Do not restart the ordered trainer just to install this patch.

After its `final.pt` exists, run Phase 2 using:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase samples --stage run @phaseArgs
```

After Phase 2 genuinely plateaus and produces its `glass_ready.pt`:

```powershell
# Stage 1 of Rank-1 glass: train Easy, A and B together
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase glasses --glass-scheme easy --stage run @phaseArgs

# Stage 2: each A/B resumes from its own best Easy checkpoint
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase glasses --glass-scheme mixed --stage run @phaseArgs

# Stage 3: automatically SKIPS if Mixed succeeded; branches from Easy otherwise
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase glasses --glass-scheme hard --stage run @phaseArgs
```

Reissuing a command resumes that scheme's checkpoint (or returns a completed
scheme's result). All results live in a **separate** default sibling workspace:
`qwen_lm_frontier_mixed_glasses_v1/` with `backbone_snapshot.pt`, the shared
baseline and classification, and independent `easy/`, `mixed/`, `hard/` stages.
Each scheme has `working.pt` (both A/B + both AdamW states), `previous.pt`,
`best_A.pt`, `best_B.pt`, `progress.json`, and `result.json` on completion.
Each paired training step is checkpointed; an interrupted evaluation can be
re-run from the last completed paired step. A stage's **best** checkpoint is
selected by net greedy correctness on the validation deck, with mixed success
prioritized to avoid selecting a higher-scoring but forgetting checkpoint.
Changing a pinned glass training policy after a run begins is refused rather
than quietly resetting training or corrupting its optimizer state.

You can inspect without loading Qwen or writing any checkpoints:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase glasses --glass-scheme mixed --stage status `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

After a scheme completes, run an explicit, separate fresh-case audit:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase glasses --glass-scheme mixed --stage audit @phaseArgs
```

This writes `mixed/fresh_audit.json` and measures per-group net accuracy,
original failures fixed, and original successes regressed. Fresh audit examples
are never used for glass fitting or best-checkpoint selection. The model is
never trained during audit; only a report is written.

Operational controls: `--glass-train-per-cell 12`,
`--glass-validation-per-cell 4`, `--glass-easy-threshold .75`,
`--glass-retention-floor .95`, `--glass-steps-per-round 12`,
`--glass-rounds 6`, `--glass-plateau-patience 3`, `--glass-width 192`,
`--glass-feature-layers 4`. These are pinned on first stage start.
Use `--samples-output` or `--glasses-output` only to point to the **same
existing output** for resume if a non-default workspace was chosen.

### Precision and limits

* The shared baseline scan uses actual frozen-Qwen greedy outputs and writes
  append-only `baseline.jsonl` progress every problem. It can take considerable
  wall-clock time; it is not a silent pre-run delay.
* The recurrent glass correction trainer targets the gold token at the first
  actual divergence. **Already corrected cases receive positive supervised
  retention**, not the old helper's zero-delta penalty. Correct original-Qwen
  examples provide intervention-minimizing replay; Mixed explicitly replays
  Easy corrections at every other training step.
* Validation plateaus are discrete correctness plateaus, not the Phase 2
  teacher-forced macro-loss plateau. Round-budget exhaustion is labeled as
  such; it is never mislabeled as demonstrated optimum.
* Only completed **Phase 2** checkpoints can seed rank-one glasses. Old 240
  baseline/deformation artifacts remain separate and unmodified.
* Classification and audit cover the mathematical *generators that exist*,
  not an unconstrained proof of mathematical reasoning, indefinite expansion
  of numerical radius, or exhaustive memorization.
* Unit/integration checks use synthetic/fake model outputs and small CPU
  tensors. **No actual CUDA accuracy, throughput or stability result is
  claimed before running these commands on the real checkpoints.**
