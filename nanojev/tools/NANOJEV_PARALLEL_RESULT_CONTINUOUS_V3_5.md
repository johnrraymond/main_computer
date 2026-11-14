# NanoJev v3.5 — Continuous RESULT optimization (bottom-heavy)

## Cutover and boundary

This is an **additive, independent** experiment. Copy the three files from the
ZIP, stripping the outer `main_computer_test/` prefix when applying them at
`C:\Users\subsi\main_computer`. It does **not** modify the v3.4 script,
`source-checkpoint`, prior run directories, or either model's checkpoint bytes.

v3.5 forks **v3.4's `intermediate_primary/best.pt`**, at step **5000** in the
reported CUDA run, not `latest.pt` at 5100. The command below requests 500
*additional* steps, targeting **5500**. An absolute `--steps` is an alternative,
not an additional-step count; a nonincreasing target is rejected before
creating an output directory.

## Objective hierarchy

The 20-step schedule is retained: 16 known-intermediary updates, three RESULT
updates, one FINAL update. The frozen Qwen encoder continues supplying one
shared activation capture and the replay minibatches.

1. **Intermediaries (80%)**: retain the existing 38-head structural X/Y
   supervision (sign, zero, route and local experts), plus a differentiable
   continuous *log-magnitude error* for each true-routed numerical expert;
   X and Y are normalized independently. The auxiliary operand decoder is
   still supervised for research, not promoted into FINAL.
2. **RESULT (15%)**: retain direct per-candidate supervision against true
   numerical RESULT. Extra continuous signed-asinh loss reinforces the direct
   continuous and residual RESULT heads. Digit, exponent and bit heads retain
   their known structural numeric labels (these are **not** 5%-pass labels).
   The hard executed RESULT from the refined X/Y remains the primary
   inference candidate R0.
3. **FINAL (5%)**: optimize all eleven estimators against *continuous*
   `log1p(relative-error)` targets, plus the difference between each
   candidate's error and R0's error. The old **within-5% classifier receives
   zero gradient** in v3.5. Its weights/outputs can remain for historical
   diagnostics and checkpoint compatibility only.

The main objective and checkpoint ranking are strictly:

```
mean(log1p(abs(predicted_RESULT - true_RESULT) / max(abs(true_RESULT), 1)))
```

computed on numerical rows, evaluated using the actual hard-decoded refined
R0 and a disjoint *odd-indexed* validation-selection partition. The
*even-indexed* validation partition calibrates FINAL's candidate-error
intercepts. Test/control are never used for training, calibration or checkpoint
selection. The metric is **lower is better**.

5% numerical accuracy remains a **diagnostic-only** report field. It does not
enter v3.5's training losses or primary/secondary best-checkpoint selection.

## Verify before running

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py .\tools\nanojev_parallel_result_continuous_v3_5.py --self-test
& $py -m pytest -q .\tests\test_nanojev_parallel_result_continuous_v3_5.py
```

## Train (fork from step 5000)

```powershell
& $py .\tools\nanojev_parallel_result_continuous_v3_5.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --fork-v3-4-root "$env:USERPROFILE\NanoJev\runs\parallel_result_bottom_heavy_v3_4" `
  --fork-parent-best `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_continuous_v3_5" `
  --device cuda `
  --dtype float16 `
  --batch-size 16 `
  --replay-updates 2 `
  --additional-steps 500
```

The run directory is independent from the parent. The imported parent state
is evaluated **before any v3.5 updates**, saved as
`validation-fork-baseline.json`, and retained as the initial best. A worse
trained checkpoint **cannot replace** the parent as best RESULT.

Each evaluation writes `validation-step-XXXXXX.json` with the continuous
source error, calibrated FINAL error, operand diagnostics, and per-candidate
continuous mean/median/P90/P99. Three distinct checkpoint paths are kept:

| Path | Selection criterion |
|---|---|
| `continuous_result/best.pt` | Lowest mean continuous **refined R0 RESULT** log error — **primary** |
| `continuous_final/best.pt` | Lowest continuous calibrated FINAL RESULT log error |
| `continuous_intermediary/best.pt` | Lowest sum of X/Y signed-asinh MAE |
| `latest.pt` | Most recent complete training step, even if metrics are worse |

All checkpoints are in the new v3.5 results directory. The original v3.4
`intermediate_primary/best.pt` is never changed.

## Resume / independent evaluation

Resuming after step 5500 for 500 more training steps (if desired):

```powershell
& $py .\tools\nanojev_parallel_result_continuous_v3_5.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_continuous_v3_5" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --resume --additional-steps 500
```

Evaluate validation, test, and control with the **validation-best** continuous
RESULT checkpoint (not necessarily `latest.pt`):

```powershell
& $py .\tools\nanojev_parallel_result_continuous_v3_5.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_continuous_v3_5" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --eval-only --eval-best
```

Outputs: `evaluation-best-validation.json`, `evaluation-best-test.json`, and
`evaluation-best-control.json`. Watch `policy.source.mean_log1p_relative_error`
first, then `continuous_quantiles.source`, `intermediaries.hierarchy_refined`,
`policy.error_calibrated`, and the frozen-operand reference
`intermediaries.original_frozen`.

## Current reference values (from your independent v3.4 evaluation)

| Split | Original frozen R0 | v3.4 step-5000 refined R0 |
|---|---:|---:|
| Validation holdout | 0.22635 | 0.20144 |
| Test | 0.30509 | 0.26792 |
| Control | 0.65138 | 0.63150 |

These are **mean log1p relative RESULT errors**, not 5% accuracy. The new
experiment is better only if its continuous RESULT error decreases on unseen
examples. Validation-only improvement is preliminary.

## Important limitation

As in v3.4, executed R0 uses a **hard operand decode**, so the end-to-end
RESULT calculation does **not** backpropagate through that discrete decode.
The base learns from continuous expert supervision; RESULT heads learn from
their own numerical RESULT labels. v3.5 thus aligns all trainable decision
losses and checkpoint selection to continuous targets without claiming
end-to-end differentiable execution. The 80/15/5 allocation counts training
phases, not measured total gradient magnitude.
