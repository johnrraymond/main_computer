# Full-token LASSO penalty-path hierarchy (±2,000,000)

This corrects the first sparse decoder patch. **The independent variable is the
LASSO penalty, NOT a requested number of active coefficients.** The decoder
observes how many input coordinates survive each penalty and how accurately
those selected coordinates reconstruct the actual number.

## Scientific contract

For each prompt format independently (decimal, Log-Bar, paired), use the saved
Qwen3-0.6B token-by-layer hidden states, with **no token averaging**. Construct
a right-aligned, zero-padded positional feature matrix. No observed activation
is averaged; constant coordinates alone are omitted.

For every fitted sign, zero, routing, or expert regression head, minimize

    (1 / (2*N)) * ||y - b - X*beta||² + alpha * ||beta||_1

where the experimental knob is `alpha_ratio`, with
`alpha = alpha_ratio * alpha_max` and `alpha_max` being that head's smallest
intercept-only penalty. Thus `alpha_ratio=1` is strong shrinkage, and
`alpha_ratio=0.005` is much weaker shrinkage. Response targets are scaled within
each head; reported absolute `alpha` values are head-specific. All nonconstant
features are eligible for selection. The working-set solver checks LASSO
optimality against **all** candidate features and adds omitted violating
coordinates. It fails if that check does not converge.

The default path contains twelve penalty settings:

    1.0, 0.70, 0.50, 0.35, 0.25, 0.18, 0.12, 0.08, 0.05, 0.03, 0.015, 0.005

There is **no 117-coefficient budget or cap**. The earlier number 117 came from a
ridge effective-DF experiment; it is not an LASSO penalty and does not govern
this study.

Each prompt representation is fitted independently. The hierarchy begins with
sign / zero / near versus far and adds magnitude regimes only if needed. Each
regression head has its own LASSO fit and its own alpha, scaled from that head's
`alpha_max`. Total nonzero coefficient counts include all fitted heads.

The primary success criterion is that each nonzero sampled value must satisfy
`abs(predicted - actual)/abs(actual) <= 0.05`, and the zero probe must predict
exactly zero. The run also reports predicted and oracle routing separately.
All fitting and scoring is in-sample by request.

## Setup

The ZIP is rooted at `main_computer_test/`; merge its `tools/` and `tests/`
contents with `C:\Users\subsi\main_computer`. It replaces only the new
`nanojev_full_activation_sparse_hierarchy.py` script, its documentation, and
its tests. The original Qwen and previous experiment scripts remain untouched.

## Capture (once; skip if already completed)

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_full_activation_sparse_hierarchy.py `
  --device cuda `
  --capture-only `
  --output-root "$env:USERPROFILE\NanoJev\runs\full_token_sparse_hierarchy_v1"
```

Captures 1,253 unique Qwen prompts (507 decimal, 239 Log-Bar, 507 paired)
with all hidden states `[29, input_token_count, 1024]` per prompt. The
manifest and raw record formats remain compatible with the previous patch.
If interrupted, add `--resume --capture-only`.

## Sweep LASSO penalties (no new CUDA inference)

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_full_activation_sparse_hierarchy.py `
  --analyze-only `
  --layers 16,28 `
  --output-root "$env:USERPROFILE\NanoJev\runs\full_token_sparse_hierarchy_v1"
```

For a small diagnostic before the whole path, append
`--alpha-ratios 0.5,0.1,0.02`.

Change the penalty schedule explicitly with
`--alpha-ratios 1,0.75,0.5,0.25,0.1,0.05,0.025,0.01`. Lower values tend to
select more coefficients and demand more solving work. `--layers all` is
supported but can create very large design matrices; 16,28 remains the default.

## Results

- `lasso_penalty_curve.csv`: each penalty point and hierarchy stage, sign/zero,
  MAE, maximum relative error, 5% pass rate, predicted/oracle routing,
  total **actual nonzero coefficients** across all heads, minimum and maximum
  absolute head penalties, and convergence flag.
- `lasso_penalty_predictions.csv`: true and predicted numbers at the selected
  final stage for each penalty setting.
- `lasso_penalty_summary.json`: penalty schedule and all stage metrics.
- `sparse_models/<representation>/alpha_<ratio>_stage_<stage>/`: saved selected
  feature indices, weights, intercept, feature scaling, and actual per-head
  penalties. The token coordinates are indexed from the end of each prompt.

These result files use new names and do not overwrite the previous
`sparse_hierarchy_curve.csv` / `sparse_predictions.csv` outputs.

The key charts should plot **reconstruction error versus actual nonzero
coefficients**, with the penalty fraction labeled, for each representation.
This directly shows whether combining decimal and Log-Bar produces better
numerical recovery at a given sparsity level.

## Solver convergence and low-noise output (v3 fix)

The former `--sparsities 117` search repeatedly printed sklearn
`ConvergenceWarning` lines and could terminate the entire job with
`SPARSE_HEAD_KKT_NOT_CONVERGED`. The penalty-path version has a separate
runtime and **does not accept `--sparsities`**. This fix improves the penalty
sweep's behavior when the same numerical issues arise:

- Capture `ConvergenceWarning` locally for each LASSO fit; do not print
  scikit-learn stack paths for every failed iteration. Unrelated Python
  warnings remain visible.
- Retry an unconverged active-set solve using warm-start coefficients,
  initially 10,000 iterations and (by default) up to 30,000 on retry. The
  **L1 penalty never changes during a retry**.
- Check both the inner solver convergence and omitted-coordinate KKT
  violations. A head is only marked `kkt_passed` when both conditions pass.
- If a head still fails, mark that stage invalid, record the reason and
  diagnostic values in `lasso_penalty_failures.json`, and **continue to the
  next penalty** rather than abort the entire experiment.
- Emit one compact `lasso_penalty_progress` JSON event per penalty and
  representation. Valid stage measurements are saved in
  `lasso_penalty_curve.csv`. A failed stage never becomes a valid data point.
- Write output CSV and JSON files after each completed penalty, so already
  completed penalty results survive a later failure. A report containing
  any failures has `status: partial` and lists every failure explicitly.

To adjust solver effort, add any of:

```text
--solver-max-iter 10000
--solver-retry-multiplier 3
--kkt-iters 12
```

A reported KKT result is checked to the configured numerical tolerance, not
an exact symbolic certificate. This is an **in-sample** numerical reconstruction
experiment and does not claim unseen-number accuracy.
