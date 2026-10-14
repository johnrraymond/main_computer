# Parallel RESULT FINAL arbitration — v3 additive cutover

## Contents

- `tools/nanojev_parallel_result_final_v3.py` — new v3 FINAL selector, fork / train / resume / eval.
- `tools/nanojev_parallel_result_median_v2.py` — the existing median-v2 trainer with a targeted CLI preflight fix. It now checks a fork's checkpoint step **before creating run configuration or checkpoints**, and supports `--additional-steps N` for intuitive continuation. No v2 checkpoint data or configuration schemas are changed.
- `tests/test_nanojev_parallel_result_final_v3.py` — shared-forward-pass, candidate ranking and gate, source retention, no-write-on-invalid-fork, full mocked fork/resume/evaluation.

Prerequisites already in your working tree:
- `tools/nanojev_parallel_result_register_search.py` (v1)
- `tools/nanojev_parallel_result_median_v2.py` (replace with the patched version in this ZIP)
- Existing NanoJev register/cassette tools, benchmark directory, source checkpoint, and the v2 `latest.pt`.

The ZIP is rooted at `main_computer_test/`. It should be cut over using your normal snapshot operator. Its files are additive except for the stated v2 CLI-only correction.

## Check

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_parallel_result_final_v3.py --self-test
& $py -m pytest -q .\tests\test_nanojev_parallel_result_final_v3.py
```

## Fork the current median-v2 checkpoint and train 500 NEW steps

Do **not** specify `--resume` on the initial fork. `--additional-steps` counts forward from the **actual** forked step, whether that is 2,600, 2,800, or later. All existing v1/v2 checkpoints stay untouched. The default freezes all 7 original RESULT heads, operation probe, and learned median, training **only FINAL** for a clean arbitration experiment. This shares one Qwen activation capture across the sampled examples and introduces no second backbone forward pass.

```powershell
& $py .\tools\nanojev_parallel_result_final_v3.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --fork-v2-root "$env:USERPROFILE\NanoJev\runs\parallel_result_median_search_v2_1" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_search_v3" `
  --device cuda `
  --dtype float16 `
  --batch-size 16 `
  --replay-updates 2 `
  --additional-steps 500
```

`--train-candidates` is an optional, separate experiment: while FINAL trains, it also continues training the seven old candidate heads and the median head using their inherited optimizer states. **Do not** turn it on when resuming a v3 run initialized without it: config drift is intentionally rejected. Start another v3 results root instead. The default frozen-candidates experiment is recommended first.

## Resume (existing v3 root)

Use the same arguments except **remove `--fork-v2-root` and add `--resume`**. To run 500 more steps from the latest v3 checkpoint, retain `--additional-steps 500`. For any v3 run, `--steps 3500` means an **absolute final step**, not 3,500 additional steps. No automatic overwrite/forking occurs.

```powershell
& $py .\tools\nanojev_parallel_result_final_v3.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_search_v3" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --additional-steps 500 --resume
```

## Evaluation

Evaluate the saved aligned `latest.pt` state with its **validation-calibrated** threshold on val/test/control (never fit the gate threshold on the test split):

```powershell
& $py .\tools\nanojev_parallel_result_final_v3.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_search_v3" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --eval-only
```

## Three supervised outputs in FINAL

`FINAL` receives:

1. Detached, frozen Qwen activation features; the predicted operation and confidence; the reference R0 result.
2. Eleven candidate RESULT values in signed-asinh coordinates: R0, continuous, scientific, decimal digits, place digits, ordinal, binary bits, residual, raw median, signed median, learned median.
3. Source-centered and median-centered candidate differences, absolute differences, nearest candidate disagreement, range, spread and dispersion.

`rank` learns a distribution over proposals, with an R0-preserving training target when R0 is already correct. When R0 is wrong and multiple alternatives are correct, the target distributes probability across those alternatives. `correct` independently predicts whether each result meets the numeric ±5% criterion. `gate` learns whether any correct candidate can rescue an incorrect R0. Training labels come from the ground truth, but the label is **never an inference input**.

FINAL is a **selector**, not an interpolator: it emits one candidate's actual RESULT without averaging away correct minorities. Boolean results pass through R0. Calibration fits an override threshold on validation that requires at least 99% source-hit retention (`--min-source-retention .99`, adjustable) **and no numeric 5% accuracy regression**. If no threshold improves R0 within these constraints, FINAL remains at source output (switch off). Validation-selected thresholds may still generalize poorly; separately evaluate test and control splits.

## Logs

- `final_initialized`, `final_train_progress`, `final_validation`, `final_checkpoint`, `final_complete`.
- `final_validation` reports source accuracy, unguarded selector, gated FINAL accuracy, eight-way/eleven-way oracle coverage, candidate switch distribution, rescues, damaged source successes, retention and calibrated gate threshold.
- `results_root/latest.pt`: complete resumable aligned v3 state; `results_root/final/best.pt`: best single aligned v3 selector evaluation snapshot with its threshold and all candidate weights required for reproduction.
- `validation-step-XXXXXX.json`: full diagnostics/examples; `correctness-step-XXXXXX.npy`: candidate+FINAL correctness by row.
- `--eval-only` prints val/test/control reports; calibration is never refit on test.

## v2 fork bug fix

Patched v2 accepts `--additional-steps 500` for the exact problem previously encountered (an inherited v1 checkpoint at 2,300 and accidentally specifying `--steps 500`). The script rejects impossible targets *before* persisting any v2 fork. If a run directory already has a v2 checkpoint, remove `--fork-v1-root`, add `--resume`, and use `--additional-steps 500`. It does not delete the earlier partially initialized directories; they can be resumed as-is.

## Boundaries

No CUDA Qwen run has been performed here. CPU self-test and integration tests use mocked activation capture. This code has no authority to change your deployed RESULT pathway, and `FINAL` output is experimental until held-out evaluation establishes a genuine improvement. Absolute decoded numerical values in these heads use the same pre-existing v1/v2 number-encoding conventions.
