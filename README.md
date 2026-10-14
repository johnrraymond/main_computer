# NanoJev selective cumulative-magnitude guard v3

This is an **additive** branch. Merge the contents of `main_computer_test/` into the repository root; no v1/v2 file is replaced. No existing checkpoint is overwritten.

## Why v3

V2's near-zero initial correction did not constrain later drift. V3 applies `base * exp(acceptance * delta)` with sigmoid acceptance initialized at about 0.00091. The acceptance gate is explicitly supervised by whether the **proposed** correction reduces operand error relative to the unchanged source prediction. Correction residuals are trained against a clipped log ratio to gold operands for matching-sign, nonzero cases. A preservation penalty protects already-accurate source predictions.

**Promotion:** `best.pt` is written only when the corrected joint X/Y 5%-pass rate is strictly greater than source-decoder joint X/Y rate on the same validation batch. `latest.pt` is always saved for resumability; a missing `best.pt` means **not promoted**. The source checkpoint is read-only and the inherited registers are frozen by default. The existing operation and result losses remain active.

## Commands (Windows PowerShell, repository root)

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_register_cumulative_magnitude_selective_guard_v3.py --self-test
& $py -m pytest -q .\tests\test_nanojev_register_cumulative_magnitude_selective_guard_v3.py
& $py .\tools\nanojev_register_cumulative_magnitude_selective_guard_v3.py `
  --source-checkpoint 'C:\Users\subsi\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt' `
  --results-root 'C:\Users\subsi\NanoJev\runs\register_cumulative_magnitude_selective_v3' `
  --device cuda --steps 3000
```

To resume use identical flags and `--resume`; `--steps` may be increased only if the inherited config permits it. Run `--eval-only --resume` against a completed v3 checkpoint for evaluation.

## Guard boundaries

The acceptance gate is learned, not a mathematical guarantee of non-regression for every sample. The strict promotion criterion protects the promoted *checkpoint*, not `latest.pt`. The new v3 code has not been tested on the user's remote CUDA checkpoint.
