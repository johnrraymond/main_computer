# NanoJev FINAL v3.1 — Closest-RESULT arbitration

This additive experiment replaces the **training objective** for FINAL, not the existing
v3 training script or checkpoint. It forks a saved `parallel_result_final_search_v3/latest.pt`
into a new directory. All earlier `latest.pt` and `best.pt` artifacts remain unchanged.

## Why v3.1

FINAL v3 selected R0 for all 512 validation rows before the retention gate. Its
ranking target treated every unanswerable-by-5% example as a source selection,
producing source-selection collapse. v3.1 trains to identify the **closest numerical
candidate** on every numeric example, including all-wrong examples.

Given true RESULT `y` and R0–R10 predictions `r[i]`, define

```
relative_error[i] = abs(r[i] - y) / max(abs(y), 1)
target_error[i]  = log1p(relative_error[i])
nearest_index    = argmin_i(relative_error[i])
```

- The model regresses the 11 target errors using robust smooth-L1.
- A second ranking loss rewards the nearest candidate. Non-source nearest
  targets are upweighted (`--minority-weight 3`, adjustable).
- No **NONE** class and no 5%-threshold target; even a 300%-wrong candidate can
  be the nearest available result.
- Exact ties prefer R0 because argmin uses the first register.
- Training features contain frozen Qwen activations, predicted operation, and
  all eleven RESULT proposals and disagreements. Labels never enter inference.
- During this first experiment R0–R10 are frozen. Optional `--train-candidates`
  changes this and should be run only as a *separate* lineage.
- Numeric FINAL unconditionally emits the candidate with the **lowest estimated
  error**. Boolean tasks pass through R0, which already has dedicated operation
  decoding.
- A retention-threshold frontier for 100%, 99%, 98%, 95%, and unrestricted
  source preservation is separately computed **only on validation** for research.
  It does not gate or silently replace the experimental FINAL output.

## Apply

Patch includes **three additive files**, rooted at `main_computer_test/`:

```
main_computer_test/tools/nanojev_parallel_result_final_closest_v3_1.py
main_computer_test/tools/NANOJEV_PARALLEL_RESULT_FINAL_CLOSEST_V3_1.md
main_computer_test/tests/test_nanojev_parallel_result_final_closest_v3_1.py
```

Apply the contents relative to your `main_computer` repository as in earlier
NanoJev cutovers. Existing v1/v2/v3 scripts must remain installed.

## Verify

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_parallel_result_final_closest_v3_1.py --self-test
& $py -m pytest -q .\tests\test_nanojev_parallel_result_final_closest_v3_1.py
```

## Fork from v3 step 3300; train 500 additional cycles

```powershell
& $py .\tools\nanojev_parallel_result_final_closest_v3_1.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --fork-v3-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_search_v3" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_closest_v3_1" `
  --device cuda `
  --dtype float16 `
  --batch-size 16 `
  --replay-updates 2 `
  --additional-steps 500
```

With a v3 checkpoint at step 3300, the target is 3800. Forking is valid **once**.
If interrupted after a successful fork, resume without the `--fork-v3-root` flag.

## Resume for an additional 500 cycles

Use all the same args except `--fork-v3-root`, then append `--resume` and
`--additional-steps 500`. `--additional-steps` is relative to the loaded checkpoint
and avoids the earlier absolute-step mistakes.

## Independent evaluation

```powershell
& $py .\tools\nanojev_parallel_result_final_closest_v3_1.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_closest_v3_1" `
  --device cuda --dtype float16 --eval-only
```

Test/control reports do not fit a retention threshold on their own labels.

## Main outputs

- `latest.pt`: resumable v3.1 optimizer and all inherited states.
- `closest/best.pt`: best checkpoint by **nearest-candidate accuracy** first,
  then mean log-relative error, then 5% numeric accuracy.
- `validation-step-NNNNNN.json`: includes
  `nearest.nearest_candidate_accuracy`,
  `nearest.nearest_candidate_accuracy_all_wrong`,
  `nearest.all_candidates_wrong`,
  `nearest.minority_nearest_opportunities`,
  `nearest.actual_minority_nearest_selected`,
  `nearest.improved_over_source_count`,
  `nearest.worsened_vs_source_count`,
  `final.numeric_5pct`, `source.numeric_5pct`, rescues and damage, candidate counts,
  and a validation-only retention frontier.
- `correctness-step-NNNNNN.npy`: boolean array of 11 original candidate 5% hits
  plus actual FINAL hits.

A high oracle union is not deployable FINAL accuracy. The primary question is
whether the head beats source on nearest-candidate selection and numerical error
on held-out examples. The 5% benchmark remains a separate reporting target.

## Validation status

CPU self-test and mocked v1→v2→v3→v3.1 training/fork/resume/evaluation tests
passed in the packaging environment. Actual local CUDA training and recovery
of minority predictions remain to be demonstrated on your workstation.
