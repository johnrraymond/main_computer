# NanoJev 5%-of-actual hierarchical decoder

## Objective

Given one of three prompts (decimal / natural-log-bar / paired), recover the
original signed integer from **frozen Qwen3-0.6B activations**:

    x_hat = f(hidden_states(w))

For **every nonzero** captured sample, require `abs(x_hat - x) / abs(x) <= 0.05`.
The true zero must be classified as zero. This is **not** an aggregate/WAPE test.
It is an **in-sample** test only: fitting and scoring occur on the same 507
numbers. It cannot demonstrate behavior on unseen numbers.

## Install

Apply this patch at the `main_computer_test/` repository-relative root,
placing the two added files in `tools/` and `tests/`. Keep the existing
`nanojev_multiscale_activation_decoder.py`, `nanojev_decimal_number_activation_map.py`
and its related scripts. Do not replace the old capture, which this reads.

## Run without CUDA or model loading

From PowerShell in `C:\Users\subsi\main_computer`:

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_hierarchical_activation_decoder.py `
  --source-root "$env:USERPROFILE\NanoJev\runs\multiscale_activation_decoder_v1" `
  --output-root "$env:USERPROFILE\NanoJev\runs\multiscale_hierarchy_5pct_v1" `
  --effective-df 117
```

To run offline script checks:

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  -m pytest .\tests\test_nanojev_hierarchical_activation_decoder.py -q
```

## Architecture and escalation

Every head reads only the previously captured `numeric_mean` activations.
The model is frozen. At layers 16 and 28 separately, for all three input
encodings, the experiment fits:

1. A learned zero-vs-nonzero ridge classifier (zero is never inferred by
   dividing by zero).
2. A learned negative-vs-positive ridge classifier.
3. A learned ordered binary magnitude router. The first boundary is 200.
4. Sign-conditioned specialist regressors for the selected magnitude regime;
   direct absolute-value regression in the smallest band, log1p-magnitude
   regression in larger bands, followed by reconstruction and a regime-bound
   clipping rule.

Stages are examined in sequence, stopping **only if every one of the 507
probe values satisfies its individual criterion**, including zero:

| Magnitude regime count | Boundaries on absolute magnitude |
|---|---|
| 2 | 200 |
| 3 | 200, 20,000 |
| 4 | 200, 20,000, 200,000 |
| 5 | 200, 2,000, 20,000, 200,000 |
| 6 | 20, 200, 2,000, 20,000, 200,000 |

If six magnitude regimes do not satisfy the test, the script **reports failure
instead of claiming a 5% success**. More levels would require a justified new
patch rather than creating arbitrarily tiny groups until they can be memorized.

For each stage, both paths are measured:

- **Actual (predicted) routing**: zero, sign, and regime are all inferred from
  activations. Only this path determines success.
- **Oracle routing**: the correct sign and regime are supplied for *diagnosis
  only*, isolating regression error from routing error. It is not the reported
  system performance.

Each fitted head requests up to **117 effective degrees of freedom**, capped
below its available rank. This is **not** 117 total coefficients or 117 total
DF for the whole hierarchy. The output reports the actual DF of each head
and **total effective DF for each hierarchy**. More specialists increase the
overall model complexity; comparison to the prior 117-DF flat decoder must
be interpreted accordingly.

## Outputs

- `hierarchy_summary.json`: actual-vs-oracle metrics, progress through
  regimes, per-magnitude-band 5% hit rate, effective DF and final statuses;
  also reproduces the previous flat 117-DF baseline using the same matrices.
- `hierarchy_predictions.csv`: every x, x_hat, exact per-x relative error,
  sign prediction, magnitude routing, oracle prediction and 5% pass/fail.
- `hierarchy_heads.csv`: fit sizes, effective DF, rank and regularization for
  every component.
- `hierarchy_models.npz`: all fitted intercepts and coefficients.

`logbar_feasibility` in the JSON checks whether **any** decoder could place
all members of each quantized Log-Bar collision group inside their 5% bands.
For the selected 507 probes, all 239 codes have nonempty 5%-acceptance
intersections, but that does not imply a particular learned decoder will find
those intersections or that the result generalizes.

## Interpretation

The 5% accuracy condition is per real x, not 5% of the full ±2M range,
not WAPE, and not a median. Small numbers count equally as individual pass/fail
probes. Opposite sign always fails the criterion; zero must be exactly zero.

Use `oracle` vs `predicted` scores and band metrics to determine whether
remaining errors come from sign, routing, or magnitude regression. Full-dataset
in-sample success cannot by itself establish unseen-number generalization.
