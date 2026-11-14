# NanoJev Parallel RESULT Median Search v2

Additive extension to the existing `nanojev_parallel_result_register_search.py` v1. This patch **does not overwrite or modify v1**. The v1 script must already be installed in `tools/`.

## What it evaluates

For each numerical example, construct one candidate vector with precisely eight elements:

`[R0 source, R1 continuous, R2 scientific, R3 digits, R4 place, R5 ordinal, R6 bits, R7 residual]`.

The extension adds three reported RESULT candidates:

- `median_raw`: the literal **arithmetic median** of those eight results (for an even count, the average of the fourth and fifth sorted values). There are no trainable parameters and no oracle selection.
- `median_signed`: take the median in signed-asinh space, then invert the transform. This is a separate nontrainable control for dynamic-range sensitivity; it is **not** equivalent to the raw median of eight values.
- `median_regressed`: a trainable, zero-initialized correction to the raw median in signed-asinh RESULT space. Receives the *same captured frozen Qwen features*, predicted operation, all eight detached candidate results, candidate offsets from the median, MAD, and source-versus-median distance. It has its own optimizer and checkpoint. The initial correction is zero.

For predicted Boolean operations, all three median candidates pass through R0 unchanged. **The operation is inferred, not supplied from the true label**, at inference and evaluation. Ground truth is used only for the training loss and evaluation.

Median regression does not backpropagate into the original seven heads, the operation classifier, or Qwen. All eight pre-existing results remain independent competing candidates. No median candidate becomes the deployed RESULT; it is an experimental readout.

## Start from your already-trained 100-step v1 run

Install the ZIP with your usual snapshot-root cutover (`main_computer_test/`). Run these commands in the `main_computer` checkout:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py .\tools\nanojev_parallel_result_median_v2.py --self-test
& $py -m pytest -q .\tests\test_nanojev_parallel_result_median_v2.py

& $py .\tools\nanojev_parallel_result_median_v2.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --fork-v1-root "$env:USERPROFILE\NanoJev\runs\parallel_result_register_search_v1" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_median_search_v2" `
  --device cuda `
  --dtype float16 `
  --batch-size 16 `
  --replay-updates 2 `
  --steps 500
```

Here `--steps 500` means **500 total**, continuing from v1 step 100. The fork validates the **complete v1 config** against the current command, including source checkpoint hash, benchmark hash, backbone identity, and training hyperparameters; it carries forward all seven head weights, their optimizer states, operation probe, replay buffer, fresh-pair stream, and RNG states. The old v1 directory and checkpoints are never written to. The new median head begins untrained and is trained in subsequent shared cycles.

Important: the original v1 script uses default `--replay-batch 32`, `--replay-capacity 4096`, `--width 128`, `--seed 20261013`, `--lr .0003`. If you changed any v1 hyperparameter, pass the same value when forking or the fork will fail closed.

### Resume v2 (no `--fork-v1-root`)

```powershell
& $py .\tools\nanojev_parallel_result_median_v2.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_median_search_v2" `
  --device cuda `
  --dtype float16 `
  --batch-size 16 `
  --replay-updates 2 `
  --steps 3000 `
  --resume
```

### Evaluate on independent splits

```powershell
& $py .\tools\nanojev_parallel_result_median_v2.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_median_search_v2" `
  --device cuda `
  --dtype float16 `
  --batch-size 16 `
  --replay-updates 2 `
  --eval-only
```

`--eval-only` consumes the existing aligned v2 checkpoint and pre-captured benchmark features, without launching a new Qwen capture. It evaluates val, test and control. Do not use the test split for threshold selection or training decisions.

## Key events and output artifacts

- `median_forked_v1`: verified v1 checkpoint hash and inherited training step.
- `median_initialized`: all eight candidate identities, median head parameter budget, source SHA, and zero backbone trainable parameters.
- `median_train_progress`: loss per candidate head, plus `median_regressed` loss, shared capture and head update time.
- `median_validation`: `registers` includes R0–R7 plus `median_raw`, `median_signed`, and `median_regressed`, with the same numeric accuracy and asinh MAE calculation as v1.
- `median_stability`: raw-vs-R0 and regressed-vs-raw numeric accuracy differences, asinh median absolute deviation across proposals, and p50/p90/p99 per-candidate transformed numerical error versus truth for the source and three median candidates.
- `rescued_source_errors`: for each candidate, examples R0 missed which that candidate solves.
- `damaged_source_successes`: for each candidate, cases R0 solved which it gets wrong. A positive rescue count is not a safe promotion criterion.
- `oracle_numeric_5pct`: OR of correctness across all eleven candidates; uses the true answer for *evaluation only*, not deployable selection.
- `validation-step-NNNNNN.json`: detailed per-head results, overlaps, median statistics, and twelve example candidate vectors.
- `correctness-step-NNNNNN.npy`: Boolean matrix of **11** candidate hits in fixed order `[source, continuous, scientific, digits, place, ordinal, bits, residual, median_raw, median_signed, median_regressed]`.
- `latest.pt`: atomic v2 checkpoint containing all models, optimizers, RNG, replay, and stream state.
- `median_regressed/best.pt`: aligned median plus the seven candidate weights and operation probe at its best validation step.

## What constitutes a positive result

Compare raw and regressed median to frozen R0 on the **same 465 numeric validation cases** and report at least these: numeric RESULT within 5%, asinh MAE, p90 asinh error, rescued source errors, damaged source successes, and numerical oracle candidate union. Track separately whether median regression improves the raw median and whether it improves R0.

Do not infer that the median is accurate merely because candidate disagreement is smaller. A majority of inaccurate candidate heads can drive the median away from an accurate minority answer. No output is authorized to replace R0 based on this training experiment; a later calibrated FINAL gate would need held-out evidence of a real improvement.

## Verification scope

CPU self-test, unit tests, and mocked end-to-end continuation from v1 through v2/checkpoint/resume/eval have been run. Actual CUDA training must be run on the user's workstation. The v1 source script and both original checkpoint lineages remain unchanged.
