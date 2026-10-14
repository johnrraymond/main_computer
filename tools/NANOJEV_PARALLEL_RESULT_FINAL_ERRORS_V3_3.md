# NanoJev FINAL v3.3 — Eleven independent error estimators

This patch **adds one experiment** without editing or replacing v1, v2, v3,
v3.1, or v3.2 files or checkpoints. Extract the ZIP using the ordinary
`main_computer_test/`-rooted cutover procedure. The runtime file to invoke is
`tools/nanojev_parallel_result_final_errors_v3_3.py`.

## Objective

v3.2's 11-way pairwise ranker briefly beat R0 on 465 validation problems but
did not demonstrate consistent improvement on larger test/control splits.
Even the full 55-pair ranker mostly chose R0 and R7. In v3.3 every RESULT is
assessed by its own small independent network, with its own optimizer.

For each numeric example, each head trains to predict:

* `log1p(abs(candidate - truth) / max(abs(truth), 1))` using SmoothL1;
* whether that candidate is within 5% using balanced BCE, as an **auxiliary**
  confidence task.

Each of the eleven heads receives the same frozen Qwen representation, the
inference-time predicted operation, the candidate-result vector and its
agreement statistics, plus five *candidate-specific* numerical features. No
true RESULT is included in inference features. All heads train on every
numeric example, including examples on which **all eleven candidates are
wrong**. None is privileged as `NONE`; the error selector always returns the
candidate whose predicted numerical error is smallest. Boolean operations
preserve the existing inferred-operation source-passthrough policy.

The v3.2 three R0/R7 comparators, full pairwise-11 ranker, R0, and v3.1
closest selector are **frozen comparison controls**. The v1–v3.1 RESULT
producers, operation probe, median, and Qwen backbone also remain frozen.
There is one Qwen capture per fresh training batch shared by all heads. Frozen
candidate proposals on replay examples are computed from the stored activations.

## Evaluation design

A new `--error-val-examples` argument defaults to **2048**, independently of
v3.2's inherited `--val-examples 512` setting. This leaves the immutable
ancestor checkpoint configuration unchanged.

* Even-positioned validation rows: fit one signed mean log-error correction
  (bounded to ±2) per head; also fit diagnostic source-retention thresholds.
* Odd-positioned validation rows: measure the new error-selection models and
  control policies, and choose `error_calibrated/best.pt` by mean log-error
  followed by 5% accuracy. These rows do not fit the calibrators or thresholds.
* Test/control: apply the validation-derived offsets and thresholds **without
  fitting or selecting anything on test/control**. Both evaluation reports
  contain raw, error-calibrated, within-5 auxiliary, previous pairwise, earlier
  closest, and R0 baselines.

The per-head diagnostics include raw/calibrated log-error MAE, RMSE, signed
bias, Pearson correlation with actual per-row error, predicted and actual
within-5 rates, and within-5 classifier Brier scores. Policy diagnostics
include nearest-candidate accuracy, numerical accuracy, regret, rescue/damage,
all-wrong accuracy, selected-register counts, and source-retention frontiers.

The default `--eval-only` evaluates **latest.pt**. Add `--eval-best` to evaluate
`error_calibrated/best.pt` instead, without modifying `latest.pt`.

## Commands (PowerShell in C:\Users\subsi\main_computer)

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_parallel_result_final_errors_v3_3.py --self-test
& $py -m pytest -q .\tests\test_nanojev_parallel_result_final_errors_v3_3.py
```

Fork the existing v3.2 step-4300 checkpoint, run 500 new steps (target 4800):

```powershell
& $py .\tools\nanojev_parallel_result_final_errors_v3_3.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --fork-v3-2-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_pairwise_v3_2" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_errors_v3_3" `
  --device cuda --dtype float16 --batch-size 16 `
  --replay-updates 2 --additional-steps 500
```

Resume for *another* 500 steps in the same v3.3 run:

```powershell
& $py .\tools\nanojev_parallel_result_final_errors_v3_3.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_errors_v3_3" `
  --device cuda --dtype float16 --batch-size 16 `
  --replay-updates 2 --additional-steps 500 --resume
```

Independent evaluation of the validation holdout, all test, and all control:

```powershell
& $py .\tools\nanojev_parallel_result_final_errors_v3_3.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_errors_v3_3" `
  --device cuda --dtype float16 --batch-size 16 `
  --replay-updates 2 --eval-only --eval-best
```

To evaluate the most recent checkpoint rather than the validation-best,
omit `--eval-best`. No `--fork-v3-2-root` is used on resume/evaluation.

## Outputs

* `latest.pt`: resumable complete checkpoint with eleven new models and optimizers,
  inherited frozen controls, replay stream, RNG state, and config.
* `error_calibrated/best.pt`: the validation-holdout-best aligned checkpoint.
* `validation-step-XXXXXX.json`: per-candidate and per-policy metrics using
  validation selection rows, plus calibration-only threshold frontiers.
* `evaluation-best-validation.json`, `evaluation-best-test.json`,
  `evaluation-best-control.json`: independent evaluation using the best state
  (`evaluation-{split}.json` for latest).
* Event names: `errors_forked_v3_2`, `errors_initialized`,
  `errors_train_progress`, `errors_validation`, `errors_best`,
  `errors_checkpoint`, `errors_complete`, and `errors_evaluation`.

Safety checks reject unsafe result roots, incompatible parent schemas,
incorrect configs, mixing fork with resume/eval, negative additional steps,
and target steps at or before the imported checkpoint. The target validation
happens *before* the v3.3 output directory or initial fork checkpoint is
created.

## Interpretation

The initial target is to outperform frozen R0 on both untouched test and
control in *numerical 5% accuracy and mean log1p relative error*, while
selecting at least some candidates besides R0/R7. The oracle remains a
truth-based diagnostic, not an inference procedure. If individual error
estimation is poor, the per-candidate MAE/bias/correlation identifies which
heads need better supervision before changing arbitration again.

The included tests use mocked CPU features and synthetic checkpoints, not
real-world CUDA training. They verify a full five-generation fork, resume,
validation-only calibration, independent evaluation, and parent SHA stability.
