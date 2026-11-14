# NanoJev v4 — Direct Mantissa Information-Recovery Probe

## Hypothesis

The existing v2 TinyStories numerical decoder already captures sign and approximate magnitude. Rather than train additive sign/exponent/mantissa corrections (v3), this experiment trains **a new, unconstrained mantissa-fraction predictor** directly from frozen captured features, parent operand coordinates, and semantic operation identity. The v2 parent remains frozen; its predicted sign and decimal exponent are retained.

Numerical reconstruction (for magnitudes >= 1) is `sign(parent) * 10 ** (floor(log10(abs(parent))) + predicted_fraction)`, where fraction is a sigmoid output in (0,1). This does **not** permit the new head to repair a wrong parent exponent or sign. Examples with parent magnitude < 1 keep their parent numerical output unchanged.

A second, diagnostic-only route substitutes the **true exponent** to measure mantissa recoverability under correct magnitude. This oracle is NEVER used in inference or checkpoint ranking. The training target is the true normalized mantissa (`log10(abs(truth)) % 1`); a smaller auxiliary fixed-exponent log-ratio loss is also used. No ground-truth component is provided as an input to the predictor.

## Files

- `tools/nanojev_altreg_independent_math_tinystories_mantissa_probe_v4.py`
- `tests/test_nanojev_altreg_independent_math_tinystories_mantissa_probe_v4.py`

## On Windows

Apply the `main_computer_test/` patch to `C:\Users\subsi\main_computer` and run from that directory:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_altreg_independent_math_tinystories_mantissa_probe_v4.py --self-test
& $py -m pytest -q .\tests\test_nanojev_altreg_independent_math_tinystories_mantissa_probe_v4.py
& $py .\tools\nanojev_altreg_independent_math_tinystories_mantissa_probe_v4.py `
  --device cuda --dtype float16 `
  --parent-checkpoint latest `
  --steps 2000 --eval-every 100 --report-every 50 `
  --allow-model-download
```

Use `--resume --steps 2000` to add steps later (same learning rate/batch size/parent SHA are required). Use `--eval-only` for a read-only held-out validation evaluation with an existing v4 checkpoint.

The parent is the v2 `latest.pt` at the default `independent_altreg_math_tinystories_precision_v2` path, expected at cumulative step 34,400. The v2 caches and checkpoints are read-only. New state is written exclusively to `~/NanoJev/runs/independent_altreg_math_tinystories_mantissa_probe_v4/`.

## Validation outputs

Every 100 steps logs `mantissa_v4_validation` with results for `parent`, `predicted`, and `oracle_exponent`, separately for original, reversed, and their numerical average. Reports include operand hits, joint hits, 5%-tolerance accuracy, mean absolute relative error, and for each input orientation the direct mantissa-fraction MAE. Selection of `best-joint.pt` prioritizes weaker orientation joint success, then weaker orientation operand accuracy, then averaged operand accuracy.

`latest.pt`, `best-joint.pt`, `validation-step-*.json`, and `test-last.json` are saved under the v4 directory. `test-last.json` is a final holdout evaluation, not used for checkpoint promotion. Neither v3 nor v2 optimizer/model files are changed.

**Interpretation:** If mantissa-fraction MAE improves and oracle-exponent reconstruction improves, the frozen features contain learnable significant-value information even if the parent exponent prevents high real-world numerical accuracy. If neither improves on held-out data, consider a different feature capture or representation instead of continuing the same supervision.

## Scope and limits

This is a diagnostic fork, not a deployable arithmetic engine. The operations, train split and prompt conventions are inherited from the existing precision-v2 trainer. Because decimal mantissa fractions jump at decade boundaries, results near powers of ten deserve per-example inspection; the oracle analysis is an upper-bound diagnostic, not an inference scheme.
