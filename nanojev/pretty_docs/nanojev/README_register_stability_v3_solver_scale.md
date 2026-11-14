# Register Stability v3 — staged sparse-regression correction

This patch modifies only `tools/nanojev_register_stability_v3_analyze.py` and its tests. Apply from `main_computer_test/` to the `main_computer` repository root (so `main_computer_test/tools/foo.py` replaces `tools/foo.py`). It does not alter the generator or the 23,552 already captured Qwen activation records.

## Root cause addressed

The previous v3 initial `alpha_ratio=0.01` attempt sent 16,032 training observations × 8,192 activation coordinates into its first rare-zero classification head. Only 104 train observations were zeros for XXXXXX. That solve ran for approximately 88 minutes without producing a convergent head. This patch reduces the initial regression workload without dropping any activation coordinate or changing the LASSO objective.

## New settings

- `--fit-ops-per-pair {2,4,8}` (default 2). Each training numeric pair contributes the requested number of **different operation families**, chosen deterministically, while validation/test/control and training evaluations use ALL available prompts. The 8 mode is the original full training workload.
- `--zero-negative-ratio 8` (default). The zero heads always use **all** rare zero training examples (across their eight operation variants) and up to eight nonzero examples per zero. Negative examples are selected reproducibly across operation families. This is a deliberately balanced training objective; zero prevalence, zero recall, and false-zero rate are reported separately on the full, unbalanced validation/test/control splits.
- `--fit-selection-seed 20261013` determines reproducible operation and negative sampling.
- Initial `--start-alpha-ratio` default increased from 0.01 to 0.03 to bootstrap a convergent hierarchy before the existing halving-and-bisection search. The original search progression, head definitions, full-feature KKT checks, and checkpointing are unchanged.
- Each failed head now emits `register_v3_head_failure_diagnostics` with the existing solver diagnostics.

All 8,192 readout coordinates remain eligible and the two XX register hierarchies learn entirely new weights. Training-only feature standardization is still computed on the entire `train` split; no val/test/control observations influence scaling, filtering or model fitting.

## Initial staged run (PowerShell, from C:\\Users\\subsi\\main_computer)

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_stability_v3_analyze.py `
  --analyze-only `
  --output-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3\analysis_xx_staged_2ops" `
  --start-alpha-ratio 0.03 `
  --min-alpha-ratio 0.0001 `
  --max-halvings 6 `
  --bisect-steps 5 `
  --fit-ops-per-pair 2 `
  --zero-negative-ratio 8 `
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

The previous `analysis_xx_alpha_search` directory is untouched. The corrected runner recognizes the existing `design_cache` within a results root but does **not** reuse a model trained on a different sampling configuration. A fresh results directory is required when switching between 2, 4 and 8 operations per pair. Use `--resume` only for an interrupted run with otherwise identical settings and results directory.

If the two-operation stage converges and validation results justify more fitting, repeat with `--fit-ops-per-pair 4` and then `8`, assigning a **new** results root to each. Each separate stage fits new weights; stages are comparisons of sample complexity, not cross-stage warm starts.

## Verification

Offline suite: 50 pytest cases plus four subtests against the extracted repository snapshot. One additional real scikit-learn 7-head masked fit on synthetic activations passed all KKT checks. The complete 23,552-activation regression has **not** been executed locally; numerical success and unseen-operation accuracy remain experimental results to be measured.
