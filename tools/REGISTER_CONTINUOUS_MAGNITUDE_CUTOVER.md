# NanoJev: continuous magnitude cutover v1

## Purpose and provenance

Run a **new, additive experiment** from the completed operation/result v1.2 checkpoint, replacing numerical magnitude *inference* with one continuous global-log decoder per operand. It **does not change** `nanojev_register_operation_result_cutover.py`, the original 38-head buffered trainer, their checkpoints, or the Qwen backbone. Existing operation recognition and deterministic execution are preserved.

This patch adds only:

- `tools/nanojev_register_continuous_magnitude_cutover.py`
- `tools/REGISTER_CONTINUOUS_MAGNITUDE_CUTOVER.md`
- `tests/test_nanojev_register_continuous_magnitude_cutover.py`

**Installation prerequisite:** The v1.2 operation/result cutover and the original buffered/benchmark tools must already be installed in the repo. Copy `main_computer_test/tools` and `main_computer_test/tests` from this patch into the corresponding repo folders, leaving all old files unchanged.

## Architecture

- **Backbone:** Qwen3-0.6B frozen, identical weights/capture identity to the source.
- **Frozen source reference:** The entire v1.2 `ExecutableNumeracy` module, including its 38-head bucket decoder and trained 20-way operation classifier. It supplies *only* the operation prediction and matched legacy evaluation; no source weights train or change. All source parameters have `requires_grad=False`.
- **New continuous magnitude head:** One MLP produces two unbounded raw coordinates, one per register. For each `raw`, calculate `log_magnitude = softplus(raw)` and `magnitude = expm1(log_magnitude)`. There is **no bucket selection, coordinate clipping, hard magnitude cap, or auxiliary bucket loss** in the new reconstruction path. Float64 exponentiation increases numeric headroom for the existing ±2M benchmark range.
- **Sign/zero:** Two trained classifiers per register, initialized by copying the source 38-head classifier's zero and sign weights. During training, their sigmoid probabilities provide a differentiable signed magnitude; hard zero/sign selection occurs only at inference.
- **Training:** Copy the v1.2 replay buffer, its generator state, seen-pair set, and all source weights from one read-only checkpoint snapshot. Fork the RNG deterministically to avoid duplicating concurrent generated pairs. Run optional replay-only bootstrap training with direct global-log and sign/zero labels (default 200 optimizer updates). Then begin new numbered training steps from zero with direct global-log supervision plus executed-result loss. Operation logits remain frozen. Result loss uses the already-tested independent candidate-operation losses, so incorrect operation results cannot cancel through a weighted average. A 200-step linear result-loss ramp is the default.
- **Loss:** Direct Smooth L1 on true `log1p(abs(x))` and `log1p(abs(y))`, supervised zero/sign BCE, oracle-operation executed-result loss, and probability-weighted *per-candidate* executed-result loss. Candidate and oracle gradients go through the new continuous magnitude head. No oracle operands, magnitude buckets, or operation names are used in inference.
- **Checkpoints:** Only the new `--results-root` contains `latest.pt` (exact resume), `best.pt` (numeric-only executed-result score) and `best_joint.pt` (joint 5% X/Y score). Both best checkpoints are preserved independently. Schema `nanojev.continuous-magnitude-cutover.v1` intentionally rejects a v1.2 checkpoint as a resume; import it with `--source-checkpoint` on the initial launch.

**Tradeoff:** The continuous magnitude head is new and does **not** magically inherit the source's old magnitude experts. The replay bootstrap trains it on copied *training-only* activations; initial validation may be worse than the source. Every validation record includes a matched old-source baseline on **exactly the same examples and frozen operation predictions**. We are testing whether this architecture can overtake the source under controlled conditions, not assuming that it already has.

## Initial Windows PowerShell launch

From `C:\Users\subsi\main_computer` (with the existing v1.2 files installed):

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py .\tools\nanojev_register_continuous_magnitude_cutover.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_continuous_magnitude_cutover_v1" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_operation_result_cutover_v1_2\latest.pt" `
  --constraint-stage continuity `
  --batch-size 16 `
  --buffer-capacity 256 `
  --per-expert-batch 16 `
  --replay-updates 2 `
  --op-width 96 `
  --continuous-width 128 `
  --bootstrap-updates 200 `
  --magnitude-weight 1 `
  --sign-weight 0.1 `
  --result-weight 0.12 `
  --candidate-weight 0.12 `
  --warmup-steps 200 `
  --steps 3000 `
  --eval-every 100 `
  --checkpoint-every 100 `
  --report-every 50 `
  --lr 0.0003 `
  --device cuda `
  --dtype float16 `
  --threads 2
```

The source `latest.pt` is the completed step-3,000 v1.2 checkpoint, which had the best observed joint X/Y recovery of that run. For a result-optimized source, substitute v1.2 `best.pt` (step 700), and use a *different* new results directory so the experiments are not confused.

The script records the source checkpoint's SHA-256, schema, step, source seen-pair count, RNG fork seed, and source score. The old model and the imported operation classifier are frozen; the trainable parameters are the new continuous magnitude and sign/zero layers only.

## Continue an interrupted run

Use the identical arguments, increasing **only** `--steps` and adding `--resume`. The source path must remain spelled the same in the configuration, but the source checkpoint file is **not** re-read or modified during resume. The new optimizer, replay, number generator, and random states come from the cutover's own `latest.pt`.

To evaluate without Qwen inference / training, repeat the launch args with `--resume --eval-only`. Select a checkpoint using `--eval-checkpoint latest` (default), `--eval-checkpoint best`, or `--eval-checkpoint best-joint`. This reads the existing saved activation captures for `val`, `test`, and `control`. Reserve `test` and `control` for a final once-only assessment after model selection when comparing approaches fairly.

## A/B to determine whether result gradients help

An A/B control should **fork the same source checkpoint** into a separate results root with the same seeds and bootstrap settings but without result gradients:

```powershell
# Use the SAME base launch args, but change these parameters:
--results-root "$env:USERPROFILE\NanoJev\runs\register_continuous_magnitude_direct_only_v1" `
--result-weight 0 `
--candidate-weight 0
```

In the original run the new magnitude path receives both direct operand and result loss; in this control it receives only direct operand and sign/zero losses. Compare numeric-only result, joint X/Y 5% recovery, median log-magnitude error, and boundary-snapping metrics at matched new steps.

## Metrics

- `numeric_result_5pct_pass_rate`: primary score on **non-boolean** problems (no benefit from easy Boolean comparisons).
- `result_5pct_pass_rate`: mixed numeric + boolean score.
- `joint_xy_5pct_pass_rate`, `x_5pct_pass_rate`, `y_5pct_pass_rate`: numerical register precision.
- `global_log_mae`, `global_log_median_error`: error of the unrounded continuous log magnitude against the global log target. `continuous_decoded_log_mae` and `source_global_log_mae` compare rounded numerical output fairly on the same examples, not bucket-normalized coordinates.
- `source_*`: exact same validation batch with old v1.2 bucketed decode and **the same operation logits**. The source's own post-cutover training is not silently substituted.
- `source_boundary_snap_count`, `continuous_boundary_snap_count`: how often decoded numbers hit one of the five old magnitude thresholds. A lower snap count does not by itself prove better magnitude accuracy; compare with numeric error.
- `operation_template_accuracy` and `operation_program_accuracy`: verify the source operation classifier remains intact. Twenty source templates are selectable; four other benchmark operations are held out of the classifier and never trained.
- `result_given_true_operation_pass_rate`, `result_given_true_operands_pass_rate`: separate operation and operand errors. These diagnostic oracles do not enter inference.

Inference returns the same structure as v1.2 (`x`, `y`, `operation`, `program`, `operation_probability`, `result`, `result_kind`) through `predict_batch`. Executed arithmetic is deterministic given the extracted values. **This experiment is not a general arbitrary-language mathematical parser.**

## Checks

```powershell
& $py .\tools\nanojev_register_continuous_magnitude_cutover.py --self-test
& $py -m pytest -q `
  .\tests\test_nanojev_register_continuous_magnitude_cutover.py `
  .\tests\test_nanojev_register_operation_result_cutover.py `
  .\tests\test_nanojev_operation_result_v12.py `
  .\tests\test_nanojev_register_online_hierarchy_buffered.py
```

The preparation environment ran **91 passing tests**, including positive gradients through old bucket boundaries, read-only source import from an actual v1.2-shaped checkpoint, exact resume, a mocked end-to-end train/resume/eval command, and the existing v1.2/buffered regressions. No full Qwen/CUDA training run has been executed in this environment.
