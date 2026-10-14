# NanoJev FINAL v3.2 — one shared capture, four parallel arbitration experiments

This is an additive fork of the **FINAL closest v3.1** lineage. It does **not** modify
`parallel_result_final_closest_v3_1/latest.pt`, the earlier parent checkpoints, or
the established source decoder. Apply the ZIP at the `main_computer_test/` snapshot
root using the normal `main_computer` cutover procedure.

## Problem addressed

v3.1 learned to switch between R0 and R7 but degraded numerical accuracy and
continuous error on the 465 held-out numerical examples. At step 3,500 it
improved 46 examples and worsened 73; mean log1p(relative error) was ~0.2477
versus ~0.2388 for R0. The next test is not another 11-way softmax with the
same supervision: it is a controlled direct comparison and independent
all-pairs ranker.

## One-cycle architecture

The frozen Qwen backbone is captured **once** per fresh minibatch, and the
existing replay buffer supplies the same examples to all four trainable heads.
The imported R0–R10 generators, operation probe, median regressor, old FINAL,
and v3.1 closest-selector remain frozen. Only the four v3.2 arbiters learn:

| Name | Inputs | Decision |
| --- | --- | --- |
| `numeric_only` | Signed-asinh RESULT values, differences from R0 and median, nearest-neighbor gaps, spread | Binary: R7 vs R0 |
| `backbone_only` | Frozen Qwen activations and the predicted operation | Binary: R7 vs R0 |
| `combined` | Both inputs, same predicted operation | Binary: R7 vs R0 |
| `pairwise_11` | Combined features | 55 independent learned pairwise comparisons among eleven RESULTS; aggregate votes into a candidate ranking |

All four models are trained from ground-truth numerical distances on all
numerical samples, including samples where **none** of the eleven candidates
is within 5% of the truth. Boolean operations remain source-passthrough,
determined **at inference by the predicted operation**, never by the true label.

Binary training uses a balanced comparison loss plus regression of the signed
difference in log1p(relative error). Pairwise training uses 55 balanced binary
losses, omits ties from pairwise BCE, and also supervises the aggregated
winning candidate. All inputs to the models are computed from frozen inference
features and predicted candidates; the target RESULT never enters the forward
features.

## Commands

Run from `C:\Users\subsi\main_computer` after extracting the patch:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py .\tools\nanojev_parallel_result_final_pairwise_v3_2.py --self-test
& $py -m pytest -q .\tests\test_nanojev_parallel_result_final_pairwise_v3_2.py
```

Fork from your existing v3.1 checkpoint (reported at step 3,800), and run 500
**additional** cycles, targeting step 4,300:

```powershell
& $py .\tools\nanojev_parallel_result_final_pairwise_v3_2.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --fork-v3-1-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_closest_v3_1" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_pairwise_v3_2" `
  --device cuda `
  --dtype float16 `
  --batch-size 16 `
  --replay-updates 2 `
  --additional-steps 500
```

If `latest.pt` already exists in the v3.2 output folder, remove the
`--fork-v3-1-root` argument and use `--resume` instead:

```powershell
& $py .\tools\nanojev_parallel_result_final_pairwise_v3_2.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_pairwise_v3_2" `
  --device cuda --dtype float16 --batch-size 16 `
  --replay-updates 2 --additional-steps 500 --resume
```

`--additional-steps 500` is relative to the saved step; don't use `--steps 500`
to fork a lineage already at step 3,800. A target-step preflight fails **before**
creating the new run directory.

## Evaluation after the run

```powershell
& $py .\tools\nanojev_parallel_result_final_pairwise_v3_2.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_pairwise_v3_2" `
  --device cuda --dtype float16 --batch-size 16 `
  --replay-updates 2 --eval-only
```

The `pairwise_evaluation` events evaluate **validation, test, and control**.
The validation split determines threshold frontiers (source retention 100%,
99%, 98%, 95%, and unrestricted). Those **fixed validation-derived** thresholds
are applied to test/control and reported separately under
`calibrated_using_validation_thresholds`. Test/control outcomes never tune the
thresholds or checkpoint selection.

## Output

* `latest.pt` — resumable, aligned checkpoint holding four models, optimizers,
  replay, and all inherited frozen head states.
* `<model>/best.pt` — independently chosen checkpoint per new model, selected
  on validation **mean log1p relative error**, with 5% accuracy as a tiebreak.
* `validation-step-XXXXXX.json` — per-model metrics, 55-pair ranking performance,
  three ablations, binary confusion matrices, source-vs-residual comparison,
  rescue/damage, means, numerical regret, nearest candidate success, all-wrong
  nearest accuracy, per-operation scores, and validation-only retention curves.

## Success criteria

The source checkpoint remains the hard control. A candidate must demonstrate
one or both of:

* Lower mean log1p(relative error) and lower numerical regret against R0 on a
  **held-out test** split.
* Increased numerical RESULT accuracy at the existing 5% threshold, without
  hiding damage to previously correct source answers.

A binary model must beat **always R0** on the actual 2-way numerical error,
not merely balanced classification accuracy. For `pairwise_11`, report success
on both the full eleven-way nearest choice and the cases where all candidates
are wrong, and compare against `closest_v3_1` and `always_residual` controls.

Unrestricted ungated decisions are research outputs only. The calibration
frontiers are diagnostic; **nothing is automatically deployed or merged**.
Validation is repeatedly examined during training, so validation-best results
are optimistic; the independent test split is the decisive estimate.

## Verification scope

Automated CPU tests use mocked Qwen capture and a synthetic checkpoint chain
(v1 → v2 → v3 → v3.1 → v3.2). They check new gradients and supervision,
feature ablations, Boolean passthrough, error diagnostics, all-pairs voting,
failed-preflight cleanliness, fork/resume/evaluation, and preserved parent
checkpoint hashes. They do **not** establish real CUDA convergence.
