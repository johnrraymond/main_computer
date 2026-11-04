> **Gamer v1 training cutover (2026-11-03, revised):** Historical technical reference only. Governing Gamer v1 plan: [training cutover](GAMER_V1_TRAINING_CUTOVER.md), [stage contracts](GAMER_V1_STAGE_CONTRACTS.md), [*08 prompt-to-math curriculum](GAMER_V1_PROMPT_TO_MATH_CURRICULUM.md), and [release gates](GAMER_V1_RELEASE_GATES.md). **Stage 1 now trains human-readable *08 game prompts into canonical uncomputed mathematical prompts (NOT direct numerical answers); Stage 2 Wide computes numerical results later. Gamer v1 remains NOT RELEASED.**

# Unified Qwen mathematical frontier with T and HDB: v3

## The error corrected

The original joint runner, entered through `tools.nanojev_qwen_lm_radius_ladder
--stage run --frontier --frontier-hdb`, simultaneously trained `T00..T08`
canonical reductions and `HDB00..HDB08` Balanced Hawks/Doves *numeric*
problems, but it did **not** continue training the mathematical task families
already implemented in `tools.nanojev_qwen_lm_frontier_tasks`. Those domains
were not represented by the joint optimizer, evaluation corpus, exposure
ledger, or joint competency output. The T signed-result radius is not a
substitute for the operand-magnitude frontier or other mathematical domains.

## Complete training population

One Qwen LoRA optimizer continuously trains all three populations:

1. `T00..T08`, with expanding signed-result shells and bounded operands, plus
   fixed autonomous arithmetic-chain diagnostics.
2. `HDB00..HDB08`, exact numeric payoff balances at negative, zero, and
   positive crossing offsets, plus full balance-chain diagnostics.
3. All **nine existing mathematical generator families**:
   `signed_arithmetic`, `place_value`, `arithmetic_operators`,
   `fractions_units`, `algebra`, `logic_graphs`, `coordinates_geometry`,
   `probability`, `symbolic_scale`. The strict integer-terminal detector is a
   separate foundational preflight and is not a tenth generator to be trained
   with fabricated `ANSWER:` labels.

The mathematical family sampler retains the existing per-family semantics and
operand-magnitude `1024 * 2**level` policy. It does not incorrectly label
these generators as signed-result `T` radii. Default `--joint-math-levels 2`
retains L0 and L1; `--joint-math-eval-per-level 3` creates a stable evaluation
corpus per family and level. Some tasks have finite possible distinct
prompts: diagnostics count the **actual** unique cases instead of claiming
an unattainable requested denominator.

The minibatch includes at least one T, one HDB, and one native-mathematics
example on every optimizer step, including with the historical
`--batch-size 4`. The fourth row alternates among T-major, HDB-major, and
math-major steps; math-major is scheduled every third step. The old HDB
minimum/maximum share parameters control the choice between T-major and
HDB-major on the remaining steps; they no longer describe the share of *all*
minibatch examples. Individual HDB minibatches no longer always have all
three zero sides with batch size four; the signed/tie offsets rotate and
all three sides are still measured in the fixed diagnostic. The actual
exposure and optimizer-mode counters are reported.

## Install and run

Apply the provided **manifest-free** ZIP using `python new_patch.py` from
the Main Computer repository root. Keep the original training workspaces.

Preview the **whole** curriculum without loading a model:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
  --stage preview --frontier --frontier-hdb `
  --radii 0,1,2,3,4 --frontier-max-radius 64 --operand-limit 1024
```

For training, reuse the *exact same* source arguments, output paths, and
existing frozen parent checkpoints used in your joint run. Example:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
  --stage run --frontier --frontier-hdb --device cuda `
  --terminal-mode numeric `
  --stage1-checkpoint 'C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt' `
  --text-checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt' `
  --output 'C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1' `
  --radii 0,1,2,3,4 --frontier-max-radius 64 `
  --operand-limit 1024 --max-rung 8 --batch-size 4 `
  --joint-steps 2400 --joint-check-every 100 `
  --joint-math-levels 2 --joint-math-eval-per-level 3
```

**Important:** the example checkpoint paths and radius flags are illustrative;
your existing campaign may use different values. The joint parent and saved
policy are validated. **Do not change pinned geometry, source lineage, HDB
parameters, or joint diagnostic parameters just to match this example.**
For a completed historical 1200-step campaign, extend `--joint-steps` above
1200 to train the added families; rerunning at the already completed count
only measures a mathematical baseline and does not perform new updates.

## Backward-safe checkpoint migration

The default joint output name deliberately stays
`*_joint_t_hdb_numeric_frontier_v2` so an existing v2 session can continue.
The internal checkpoint schema is now `v3`. The migration accepts a v2
checkpoint **only** if the parent fingerprint, original T/HDB evaluation
fingerprint, and pinned policy match. A snapshot named
`pre_math_frontier_migration.pt` preserves the original **complete** v2
checkpoint (LoRA and optimizer). It then measures the native mathematical
baseline on the current adapter and appends mathematics state and ledgers.
The T/HDB optimizer history is not restarted; the math baseline is explicitly
labelled as taken at the migration optimizer step, not falsely labelled as
an initial/pre-training score. Changes in curriculum geometry are rejected;
**only an increasing total joint step budget** is an allowed resume change.

Existing completed v2 reports are preserved until the first new joint check.
Before that check, `--stage competency` exposes the just-measured math baseline
with `diagnostic_state=migration_baseline_only_no_math_training_yet`.

## Diagnostics and acceptance

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
  --stage competency --frontier --frontier-hdb `
  --output 'C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1' `
  --radii 0,1,2,3,4 --frontier-max-radius 64
```

This reads the **last completed scheduled checkpoint evaluation** (it does
not run new inference). Use the same workspace arguments used for training.
The combined `joint_frontier_report.json` includes `t`, `hdb`, `chains`,
`hdb_numeric_balance_chains`, `mathematics` by task and task+level,
`math_task_velocity`, `per_tier_learning_state`, and direct training exposures.
`math_eval_deck.json` records the fixed math corpus; the digest of its source
and target pairs is part of the saved checkpoint identity.

True acquisition is counted as transitions from wrong to right on fixed
unassisted greedy prompts. Regressions count independently, and net change
subtracts regressions. Success on this finite diagnostic is *not* exhaustive
mathematical mastery, nor an exact universal no-forgetting certificate.
The diagnostic budget is additional Qwen inference at every check.

## Tests and limits

The CPU test suite exercises all nine task families at two levels, disjoint
training/evaluation sources, nonzero exposures for all three populations,
native-family accuracy and acquisition bookkeeping, on-disk continuation,
immutable v2 migration backup, and rejection of incompatible resume policy.
It does not run Qwen CUDA learning on the host.

This patch does not change the frozen Qwen base or LM head; no glass,
HybridLink, numeric sidecar, external oracle at inference, or HDB strategy
classifier is introduced.
