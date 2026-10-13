# `XXXXXX/YYYYYY` minimum-convergent-alpha search

This is a **separate**, additive analyzer for existing `register_template_screen_v2` frozen-Qwen captures. It **only fits the `xx` registration** (`XXXXXX` / `YYYYYY`); it never analyzes `ab`. The `A/B` experiment and the original register analyzer are unchanged.

The prior `xx` run converged at LASSO **alpha ratio `0.01`**, yielding approximately **53.81%** in-sample joint 5% recovery. At `0.002`, both registrations failed at the first `zero` head. We want the **weakest penalty for which every head in BOTH xx register hierarchies actually converges and passes the existing KKT checks**.

## Search algorithm

1. Read the previous `xx` `0.01` **saved fitted heads** and the prior success summary from `analysis_logged_alpha_001/`. Before using them, verify the same captured data root, paired representation, stage, layers, readout window, nonconstant feature indices, normalization of selected coordinates, per-head alpha maxima, and prior solver/KKT convergence. Reuse coefficients only to initialize the **same heads on the same captured xx data**. Never borrow coefficients from the bare two-number experiment or from `ab`.
2. Fit the **entire two-register hierarchy** at `0.005`. Its fit starts from the verified `0.01` solutions. If every head converges and passes KKT, checkpoint scores, per-operation predictions, nonzero coefficients, complete head models, and a JSON search summary; then fit `0.0025`.
3. Keep halving after whole-hierarchy successes, down to the configurable floor/maximum step count.
4. On the **first failed trial**, bracket between the last converged ratio and the failed ratio. Try their arithmetic midpoint; repeat for the configured number of bisections. All readout and coefficients from successful fits are retained, and failures are separately reported.
5. `xx_alpha_search_summary.json` reports the **lowest ratio observed to converge**. Note: solver convergence is not mathematically monotone in the penalty, so the boundary is a practical empirical bracket, not a proof that smaller penalties cannot be optimized by different solver settings. Improving in-sample recovery is measured but is NOT a substitute for solver convergence.

Each LASSO head always uses `alpha = alpha_ratio * alpha_max` for its own training subset and normalized target. There is no target coefficient count and no pooling or manual feature truncation. All 8,192 nonconstant suffix coordinates (given the previously observed configuration) remain eligible. A failed optimizer run is **not** counted as poor numerical recovery and is never added to the set of valid results.

## Command (Windows PowerShell)

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_xx_alpha_search.py `
  --output-root "$env:USERPROFILE\NanoJev\runs\register_template_screen_v2" `
  --baseline-summary "$env:USERPROFILE\NanoJev\runs\register_template_screen_v2\analysis_logged_alpha_001\register_template_summary.json" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_template_screen_v2\analysis_xx_alpha_search" `
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
  --heartbeat-seconds 30
```

The default `--baseline-summary` resolves to the shown `analysis_logged_alpha_001` file, so that argument can be omitted when using the same standard root.

**Crucial:** the baseline folder must still contain `models/xx/alpha_0p01_stage_5/heads.json`, `layout.json`, and the individual `.npz` head files. A summary file alone is not sufficient to warm-start. If those model files were moved, supply a matching baseline summary at the relocated folder; the script refuses to silently fit from an unproven baseline.

Each penalty emits `xx_alpha_trial_start`, position/head progress and heartbeats, then `xx_alpha_trial_pass` or `xx_alpha_trial_fail`. Failed trials are bracketed and the search continues. The summary JSON and attempt CSV are checkpointed after every trial. All successful fitted models are saved under distinct `models/alpha_*_stage_5` directories. The data used in the prior capture and the old analysis outputs are not modified.

This is still **in-sample numerical recovery**. It does not assess Qwen's arithmetic correctness, unseen pair generalization, or activation injection. Full CUDA verification has not been performed here.

### Resume after interruption

The search checkpoints the results after every **completed penalty trial**. Repeat the command above with `--resume` to replay only the saved success/failure decisions, load the most recent fully converged `xx` models as the warm start, and continue at the next untried penalty. The capture manifest, baseline path, readout, stage, layers and solver/search arguments must match; mismatches stop rather than silently merge two different experiments. Any single head currently running when interrupted must be recomputed. A fresh invocation into a nonempty search results root without `--resume` is rejected.
