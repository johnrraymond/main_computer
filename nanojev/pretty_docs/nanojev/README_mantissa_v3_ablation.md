# NanoJev mantissa v3: six-mode, read-only ablation

This patch **adds one script and one test file**, without replacing the current
TinyStories v2 or v3 training scripts. The v3 patch must already be installed.

## Scope

Load v3 `latest.pt` and `best-joint.pt`, and the exact parent-v2 checkpoint
identified by v3's stored SHA-256. Reuse the v2 `validation-cache.pt` without
loading TinyStories or training anything. By default evaluate only validation;
`--splits test` is available for final, one-time confirmation after choosing a
hypothesis on validation.

All six modes use the inherited sign and v3's numerical reconstruction rules:

- `parent`: unmodified v2 numerical X/Y predictions.
- `full`: v3 exponent + mantissa corrections.
- `exponent_only`: v3 exponent correction, zero mantissa correction.
- `mantissa_only`: v3 mantissa correction, zero exponent correction.
- `oracle_exponent`: use the true decimal decade with the fractional log10
  magnitude produced by v3; **uses ground truth** and is not deployable.
- `oracle_mantissa`: use v3's normalized decade with the true fractional log10
  magnitude; **uses ground truth** and is not deployable.

Important: v3's exponent correction is **continuous** in log10 space, not an
integer exponent classifier. We normalize any decade carry in the `full`
logarithmic magnitude before replacing its components with an oracle value.
Values with inherited absolute magnitude below 1 keep their parent prediction,
matching v3; oracle results do not diagnose this path. The averaged orientation
is the arithmetic mean of the separately reconstructed original and reversed
numerical predictions. The current RESULT and FINAL branches are not touched.

## Windows PowerShell

From `C:\Users\subsi\main_computer` after extracting at repository root:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py .\tools\nanojev_altreg_independent_math_mantissa_v3_ablation.py --self-test
& $py -m pytest -q .\tests\test_nanojev_altreg_independent_math_mantissa_v3_ablation.py

& $py .\tools\nanojev_altreg_independent_math_mantissa_v3_ablation.py `
  --device cuda `
  --parent-root "$env:USERPROFILE\NanoJev\runs\independent_altreg_math_tinystories_precision_v2" `
  --parent-checkpoint latest `
  --results-root "$env:USERPROFILE\NanoJev\runs\independent_altreg_math_tinystories_mantissa_v3" `
  --checkpoints latest best-joint `
  --splits validation
```

Use `--device cpu` to evaluate without CUDA. If v3 was forked from
`best-precision` or `best-asinh` instead of `latest`, supply the actual
`--parent-checkpoint` (SHA-256 must match, or execution stops). The script does
not modify input checkpoints, cached feature captures, or optimizer state.

## Output

Default destination:
`%USERPROFILE%\NanoJev\runs\independent_altreg_math_tinystories_mantissa_v3_ablation`

- `mantissa_v3_ablation_predictions.csv`: every X/Y prediction for each
  checkpoint, orientation and mode, absolute-relative errors, within-5% flags,
  and a `full_vs_parent` category.
- `mantissa_v3_ablation_summary.json`: per-checkpoint/per-orientation 5% operand
  and joint accuracy, mean absolute relative error, and repaired/regressed/
  preserved-success/unchanged-failure counts.
- `mantissa_v3_ablation_manifest.json`: input checkpoint SHA-256 and v3 parent
  provenance.

No claims of improvement are made until the script is run on trained v3 weights.
