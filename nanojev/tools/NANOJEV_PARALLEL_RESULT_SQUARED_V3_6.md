# NanoJev v3.6 — Bottom-heavy normalized squared-error cutover

## Scope

Additive experiment. Adds `tools/nanojev_parallel_result_squared_v3_6.py` and a new test file. **Never edits or overwrites v1–v3.5 scripts or their checkpoints.** Forks from the chosen v3.5 checkpoint and writes to `parallel_result_squared_v3_6/` (new directory). Backbone Qwen stays frozen. Operation probe and historical comparison controllers stay frozen. RESULT heads and intermediary decoders are trainable on their designated phases.

## Objective

For each numeric example with physical predicted RESULT `p` and truth `t`, the primary score is

`normalized_squared_error = ((p-t) / max(1, abs(t)))**2`.

For a set of numeric examples, `MSE = mean(normalized_squared_error)` and `normalized_RMSE = sqrt(MSE)`. **No log, cap, winsorization, silent trimming, or 5% threshold in the selected score.** Boolean-result examples are excluded from numeric MSE and continue to be assessed separately. Uses float64 for physical squared arithmetic and reports mean, median, P90, P99, and maximum squared errors to expose tails. Historical mean log1p error and numeric within-5% statistics remain diagnostics only.

The training cycle remains **16 intermediary / 3 RESULT / 1 FINAL phases**, and all examples are known-target supervised.

- **Intermediaries (80%)**: 38-head X/Y model gets structural BCE routing/sign/zero penalties and *normalized physical squared magnitude error* in the truth-selected expert. Separate auxiliary operand readout gets physical normalized MSE with structural classifiers. Expert magnitude terms no longer use smooth-L1. X and Y errors are independently measured, with equal numerical supervision weight. No gradient traverses the hard executor.
- **RESULT (15%)**: continuous and residual RESULT heads train against physical normalized MSE, while structural sign/zero/Boolean losses remain. The learned median residual uses normalized MSE. Digit, bit, ordinal, and scientific discrete heads retain categorical losses because argmax decoding has no differentiable numerical gradient.
- **FINAL (5%)**: eleven candidate error heads regress normalized squared error (and relative *squared*-error differences versus R0). The auxiliary 5% classifier receives no gradient. Inference selects the lowest estimated squared error. FINAL training is independent from the frozen and trainable RESULT paths.

**Gradient stability**: uses float64 physical loss math; uses float64 norms to clip optimizer parameter gradients safely without clipping numerical squared-error targets. Non-finite loss or gradient raises a visible error and does not silently continue. Result-coordinate saturation uses straight-through clamping to preserve a corrective gradient. Extreme outputs can nevertheless dominate true squared loss: review tail metrics and stability logs. No automatic learning-rate schedule is implied.

## Cutover

Apply the patch ZIP at the repository root so that ZIP `main_computer_test/tools/...` maps to your `tools/...` under the cutover mechanism already in use. Ensure **v1–v3.5 scripts are already present**; this additive patch includes no dependency copies.

PowerShell at `C:\Users\subsi\main_computer`:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py .\tools\nanojev_parallel_result_squared_v3_6.py --self-test
& $py -m pytest -q .\tests\test_nanojev_parallel_result_squared_v3_6.py

& $py .\tools\nanojev_parallel_result_squared_v3_6.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --fork-v3-5-root "$env:USERPROFILE\NanoJev\runs\parallel_result_continuous_v3_5" `
  --fork-parent-best `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_squared_v3_6" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --additional-steps 500
```

`--fork-parent-best` imports `continuous_result/best.pt` (historically v3.5 step 5100); if omitted it imports `latest.pt` (historically v3.5 step 5500). **v3.6 never modifies either file.** Importing the best at 5100 and adding 500 steps targets **step 5600**. It computes a new parent baseline by re-evaluating the inherited model using the new normalized MSE score on the *validation selection* partition. v3.5 log-error cannot be used as the starting MSE score. On a fork it retains all learned model weights and replay state but clears trainable Adam moments, because the MSE gradients belong to a different objective.

## Resume and independent evaluation

```powershell
& $py .\tools\nanojev_parallel_result_squared_v3_6.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_squared_v3_6" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --eval-only --eval-best
```

Then to extend the latest trajectory while retaining the best MSE checkpoint:

```powershell
& $py .\tools\nanojev_parallel_result_squared_v3_6.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_squared_v3_6" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --resume --additional-steps 500
```

## Checkpoint and evaluation contract

- `latest.pt` — latest full model/optimizer/replay state.
- `squared_result/best.pt` — lowest validation-selection **refined R0 normalized MSE**, including parent baseline.
- `squared_final/best.pt` — lowest validation-selection calibrated FINAL normalized MSE.
- `squared_intermediary/best.pt` — lowest combined X/Y normalized MSE.
- `validation-fork-baseline.json` and `validation-step-*.json` — objective-tracked validation reports.
- `evaluation-best-validation.json`, `evaluation-best-test.json`, `evaluation-best-control.json` — independent best-checkpoint evaluation reports. Only validation (even indices) calibrates FINAL; its disjoint odd indices choose best; test and control do not influence optimizer or best selection.

Read `policy.source.mean_normalized_squared_error` for primary continuous RESULT MSE and `policy.source.rmse_normalized_error` for its square root; `policy.error_calibrated.mean_normalized_squared_error` for FINAL. Under `intermediaries.hierarchy_refined`, inspect `x_normalized_mse`, `y_normalized_mse`, and `executed_result_numeric_mse`. Five-percent metrics and historical log metrics are for comparisons only.

**No CUDA results are pre-claimed.** The included tests cover CPU self-test, differentiable gradients, parent fork/resume/checkpoint selection/evaluation, mock 16:3:1 training, and parent file immutability. MSE outliers are inherently more influential than under a log objective; inspect the full error distribution before a long uninterrupted run.
