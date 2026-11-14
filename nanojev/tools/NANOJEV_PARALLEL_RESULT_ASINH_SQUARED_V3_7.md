# NanoJev v3.7: fixed-scale signed-asinh squared RESULT regression

This is an **additive, independent** experiment rooted under `main_computer_test/`. It does not edit prior v1–v3.6 tools, source checkpoints, or run directories. It must be applied through the existing snapshot/patch cutover, then executed from `C:\Users\subsi\main_computer`.

## Why this cutover

v3.6 trained target-normalized MSE `(p-y)^2 / max(1, abs(y))^2`. This loss assigns every exact-zero prediction a value ≤ 1, regardless of the target magnitude, and its FINAL head squared already-squared error targets, leading to unstable, very large losses. The v3.6 step 5,200 FINAL measured 0.9964 on this defective MSE while almost never returning a 5%-correct answer. **Do not resume v3.6.**

v3.7 forks from the **v3.5 continuous RESULT validation-best** checkpoint (step 5,100 in the real experiment), retaining trained intermediary, RESULT, and candidate weights; refreshing incompatible FINAL output layers and resetting trainable Adam moments. Old source, v3.5, and v3.6 files are never mutated.

## Numerical objective

The primary supervised numerical quantity is squared distance in signed-asinh coordinates:

`asinh_sq(p,y;s) = [asinh(p/s) - asinh(y/s)]²`, `s > 0` fixed for the entire run.

The default scales are `--operand-asinh-scale 1.0` and `--result-asinh-scale 1.0`; these are declared units, **not target-dependent scales**. Optional explicit scales can be chosen from the **training distribution only** in a fresh run. They must remain fixed when resuming/evaluating, and are saved to the immutable run configuration. The objective is uncapped; high and low tails are recorded. Historical mean `log1p` relative error and 5% accuracy are reported but **never select a checkpoint**.

- **16 / 20 updates (80%)**: known X/Y direct supervision, 38-head true-route numerical magnitude experts trained with asinh-square; separate auxiliary coordinates trained likewise; categorical sign, zero, digits and routing remain appropriately classified.
- **3 / 20 updates (15%)**: continuous and residual RESULT, and trained median, use squared asinh numeric distance. Discrete-only RESULT decoders retain categorical losses; scientific/ordinal fractional-regression terms use MSE rather than smooth-L1.
- **1 / 20 updates (5%)**: FINAL learns bounded signed candidate advantage vs R0, not a twice-squared squared-error target:

  `target_i = tanh((asinh_sq(candidate_i,y;s)-asinh_sq(R0,y;s))/T)`, default `T=2`.

  The target lies in [-1,1]. Each estimator predicts bounded signed advantage with `tanh`, with R0 anchored to exactly zero at inference. FINAL switches only for predicted improvement better than `--switch-margin` (default 0.02); calibration offsets are estimated on the separate even-indexed validation partition and clipped to ±0.25. The odd-indexed validation partition selects checkpoints. **No target leakage** enters inference.

`FINAL` is a secondary learner and does not govern the primary best checkpoint. `Qwen/Qwen3-0.6B` remains frozen. The existing hard operand decoder still prevents RESULT gradients backpropagating into the operand hierarchy; the intermediate layer is directly supervised.

## Install and verify

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_parallel_result_asinh_squared_v3_7.py --self-test
& $py -m pytest -q .\tests\test_nanojev_parallel_result_asinh_squared_v3_7.py
```

## Fork a fresh v3.7 experiment, 500 steps

```powershell
& $py .\tools\nanojev_parallel_result_asinh_squared_v3_7.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --fork-v3-5-root "$env:USERPROFILE\NanoJev\runs\parallel_result_continuous_v3_5" `
  --fork-parent-best `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_asinh_squared_v3_7" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --additional-steps 500
```

This targets step **5,600** when forking from step 5,100. Do not supply `--fork-v3-5-root` on resume/evaluation. You may explicitly set `--operand-asinh-scale`, `--result-asinh-scale`, `--final-target-temperature`, `--switch-margin` on initial fork, but must supply **identical values** on subsequent commands. Changing the scale requires a **new run root and parent fork**. Defaulting to 1.0 makes direct comparisons within the lineage straightforward; alternative scales are separate experiments and have differently scaled numerical metrics.

## Resume (from latest.pt)

```powershell
& $py .\tools\nanojev_parallel_result_asinh_squared_v3_7.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_asinh_squared_v3_7" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --resume --additional-steps 500
```

## Evaluate the independent test and control using validation-best

```powershell
& $py .\tools\nanojev_parallel_result_asinh_squared_v3_7.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_asinh_squared_v3_7" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --eval-only --eval-best
```

## Checkpoints and acceptance criteria

- `latest.pt`: full optimizer/replay/phase state for continuation.
- `asinh_result/best.pt`: lowest **source (refined R0) mean signed-asinh squared RESULT error** on validation-selection half. A real parent baseline is evaluated and saved at fork before any new update.
- `asinh_final/best.pt`: lowest calibrated FINAL mean signed-asinh squared RESULT error (secondary).
- `asinh_intermediary/best.pt`: lowest combined X and Y signed-asinh MSE (secondary).
- `validation-fork-baseline.json`, `validation-step-*.json`, `evaluation-best-{validation,test,control}.json`: detailed diagnostics, candidate dispersion, near-zero rates, source-switch fractions, mean/median/P90/P99/max losses, and old log-relative and within-5% scores.

**Success** requires a reduction in the independent **test AND control** mean signed-asinh squared RESULT error against the preserved parent **under the same s=1 scale**. A lower validation loss alone does not establish generalization. The run also monitors near-zero collapse and FINAL's harm relative to R0. Do not promote FINAL merely for a lower bounded-regret prediction training loss.

## Limitations

This design uses *squared error in a transformed coordinate system*, not physical-unit squared error. The asinh transform attenuates enormous errors to keep optimization tractable; consequently results differ from both raw physical MSE and target-normalized MSE. The 80/15/5 proportion counts training **phases**, not precise FLOPs. Testing here uses CPU self-tests and mocked model/replay/checkpoint lineage; **real CUDA accuracy has not been established** by this patch.
