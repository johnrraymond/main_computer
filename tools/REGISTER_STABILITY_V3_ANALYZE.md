# Register Stability v3: XX-only operation-transfer LASSO analysis

This patch adds `tools/nanojev_register_stability_v3_analyze.py`. It **does not** replace the capture generator or change existing Qwen activation records. It consumes the 23,552 records from `nanojev_register_stability_v3_generate.py` with the original generator's manifest and stored layers 16 and 28.

## Required scripts

Install the prior v3 generator patch and XX binary alpha-search patch first. This script imports:

- `nanojev_register_stability_v3_generate.py`
- `nanojev_register_xx_alpha_search.py`
- `nanojev_register_template_screen.py`
- `nanojev_two_number_sparse_decoder_fast.py` and its established dependencies.

## Analyze while capture finishes

From `C:\Users\subsi\main_computer`, run:

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_stability_v3_analyze.py `
  --analyze-only `
  --wait-for-capture `
  --output-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3\analysis_xx_alpha_search" `
  --start-alpha-ratio 0.01 `
  --min-alpha-ratio 0.0001 `
  --max-halvings 6 `
  --bisect-steps 5 `
  --stage 5 `
  --layers 16,28 `
  --readout-scope suffix `
  --tail-tokens 4 `
  --solver-max-iter 10000 `
  --solver-retry-multiplier 3 `
  --kkt-iters 12 `
  --threads 8 `
  --heartbeat-seconds 30 `
  --progress-every-heads 5
```

`--prepare-only` reports capture completeness and train/val/test/control split sizes without fitting. It does not load Qwen.

## What is fitted

- Exactly **one registration:** `XXXXXX/YYYYYY`, using the **paired** decimal + Log-Bar encoding captured in v3.
- Independent position-1 and position-2 sign, zero, magnitude-router, and sign-conditioned regression heads (stage 5). These are fitted fresh. They **do not** inherit any activation coordinate indices or coefficient values from v2.
- A **single fit shared across 20 familiar instruction variants** in the train split. No operation label is fed to the regressors.
- Penalty ratio `alpha_ratio` is the fractional value of *each head's own* `alpha_max`; the number of coefficients is measured, never capped.
- Only the selected two Qwen layers, and the last N **unaveraged** token positions, are used in the first experiment.
- No validation, test, or control pair contributes to feature normalization, feature selection, or coefficient fitting.

## Convergence search

Start at `0.01` (no assumption of convergence on this expanded dataset), then try `0.005`, `0.0025`, etc. **Only after both full hierarchies converge** does the ratio decrease. Once any head fails solver convergence or full-feature KKT, retain the last success and bisect the bracket (`0.00375` between success `0.005` and failure `0.0025`, for example). The reported lowest converged ratio is a **solver-dependent empirical boundary**, not an accuracy optimum.

At each successful ratio, checkpoint the sparse models in `models/alpha_<ratio>_stage_<stage>/`, emit train and validation scores, and update `register_v3_alpha_search_summary.json` and `register_v3_alpha_trials.csv`. If the experiment is interrupted, rerun the *same command* with `--resume`; the last successful models are loaded and the already-completed ratios are skipped. When completed, held-out test and full-operation control results are scored **once** using the lowest converged ratio. A failed `0.01` start ends cleanly with a failure report; to try a stronger starting ratio, use a new `--results-root`.

### Outputs

- `register_v3_alpha_search_summary.json`: trial history, convergence/failures, train and validation scores, final scores on grouped train/val/test and full 24-operation control pairs.
- `register_v3_alpha_trials.csv`: one record per penalty trial.
- `models/...`: individually saved converged sparse heads with feature indices and scaling evidence.
- `predictions_test.csv` and `predictions_control.csv`: individual readbacks on unseen pairs, including the withheld operation variants.
- `design_cache/...`: disk-backed `(23552, 8192)` float32 suffix matrix (for 4 tokens/2 layers, about 772 MB decimal / 736 MiB). The **design cache is resumable** and can be reused on later analysis runs in that result directory; no CUDA inference is repeated.

### Scoring

A pair succeeds only when *both* registers pass their own relative-5%-error check, with numerical zero recovered exactly. The final summary includes per-operation and per-family accuracies; known-vs-held-out operation scores on 128 unseen control pairs; the proportion of those 128 pairs passing *all* 24 operation variants; and normalized readback drift as the operation changes.

This experiment tests **readability of registered numbers after operation text**. It does not yet test causal register injection or the model's arithmetic answer correctness.

## Important limits

The previous v2 `0.01` model must NOT be transferred: its training examples and activation geometry differ from v3. Only its hyperparameters and the method are inherited. The analyzer may be expensive to fit (approximately 16,032 training prompt observations by 8,192 candidate features at suffix length 4). The capture count is not a runtime estimate. The solver preserves its original LASSO objective and convergence certification; it does not declare a failed fit successful.

Offline tests: `python -m unittest discover -s tests -p 'test_nanojev_register_stability_v3_analyze.py' -v`
