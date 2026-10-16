# Qwen v3 attention -> RESULT/FINAL diagnostic

Read-only audit. Does **not** pause, overwrite, modify, resume, or retrain the continuing attention experiment. Uses existing attention-cache.pt, validation-cache.pt, and parent/v3 checkpoints; **never reloads the frozen HF backbone**.

## Install

Copy `tools/nanojev_altreg_v3_result_final_audit.py` and the test file to corresponding directories under the repository root. It imports the existing tools/nanojev_altreg_independent_math_qwen_precision_v2.py and tools/nanojev_altreg_math_v3_decoder_experiment.py.

Run, in `C:\Users\subsi\main_computer`:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m pytest -q .\tests\test_nanojev_altreg_v3_result_final_audit.py
& $py .\tools\nanojev_altreg_v3_result_final_audit.py --checkpoint best --split validation --device cpu
& $py .\tools\nanojev_altreg_v3_result_final_audit.py --checkpoint latest --split validation --device cpu
```

The output is one compact JSON event plus a full report containing per-example results, under `%USERPROFILE%\NanoJev\runs\independent_altreg_math_qwen_v3_result_final_audit\`. The default does **not** access the test set, reserving it for independent evaluation after model selection. Run `--split test` only if explicitly ready to use it.

## Interpretation

- `legacy_result`, `legacy_final`: old trained downstream heads driven by their native parent X/Y.
- `graft_result`, `graft_final`: same old downstream head weights, but with modern Qwen attention X/Y converted to the parent's normalized-asinh coordinates. This is a **distribution-shift intervention**, not a trained joint model.
- `oracle_xy_result`, `oracle_xy_final`: those same downstream heads with perfect X/Y supplied. If these remain poor, the bottleneck is downstream, not operand reconstruction.
- `executed_attention_xy`: exact deterministic execution of the **provided operation label** on predicted X/Y. This is a diagnostic ceiling for the current operand estimates; it is not a trained RESULT or FINAL head.
- `executed_parent_xy`: equivalent deterministic control for original parent X/Y.
- The `averaged` view is the arithmetic mean of original and reversed register predictions. For `executed_*` the operands are **averaged before execution**, especially crucial for multiplication, squares, predicates and branches.
- Numerical operations use relative 5%, 1%, 0.1% hit rates. Boolean operations use rounded exact-label accuracy. All report fields include numeric/boolean case counts, transformed MSE, and per-case output.

Do not equate validation results with independent generalization. Do not treat the deterministic operation oracle as learned arithmetic competence.
