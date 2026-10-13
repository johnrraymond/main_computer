# Fast two-number LASSO — concurrent analysis

`nanojev_two_number_sparse_decoder_fast.py` runs **only analysis** against existing frozen-Qwen full-token activation records. It is independent of the original `nanojev_two_number_sparse_decoder.py`, which can keep running in the first terminal. The new runner never invokes model capture or writes to the original source directory.

## Run in a second PowerShell window

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_two_number_sparse_decoder_fast.py `
  --source-root "$env:USERPROFILE\NanoJev\runs\two_number_sparse_v1" `
  --output-root "$env:USERPROFILE\NanoJev\runs\two_number_sparse_fast_v1" `
  --analyze-only `
  --threads 2 `
  --layers 16,28
```

The original remains untouched in its original PowerShell window. Fast results are written to the **different output directory**, with independent checkpoints and model files:

- `fast_two_number_fit_curve.csv`
- `fast_two_number_predictions.csv`
- `fast_two_number_fit_failures.json`
- `fast_two_number_summary.json`
- `fast_two_number_models/`

The directory containing source records must already have a complete `manifest.json`, `model_identity.json` and `records/` tree. Analysis never writes to it. The program rejects source/output directories that overlap, and refuses capture-related flags.

### Resource control

The fast analysis automatically sets its **own** Windows process to `BELOW_NORMAL_PRIORITY_CLASS`. It cannot change the priority of the already running original. `--threads 2` caps thread-pool BLAS/OpenMP use by this fast process; it is not a hard overall CPU percentage or memory limit. The scikit-learn coordinate-descent fits themselves may run single-threaded, and two simultaneous analyses still share RAM, storage throughput, and CPU. On a memory-constrained machine, start with `--threads 1 --representations logbar` to reduce memory use while benchmarking. You can add `--normal-priority` to opt out of lower-priority scheduling.

A smaller comparative run is also possible by specifying e.g. `--alpha-ratios 0.03,0.015` or `--max-regimes 3`. This does *less scientific work* but changes no objective or feature input and does not overwrite the original analysis.

## Mathematical contract

No averaging, PCA or feature preselection. The same full prompt activation coordinates remain available to three independently fitted LASSO regressions. Two target-position hierarchies evaluate the signed, exact-zero, and magnitude-specific reconstruction of each number. Cache keys contain actual training rows and responses; LASSO starts from the previous penalty and verifies full-feature KKT conditions. Warnings, invalid fits and joint 5%-per-number pass rates remain visible in independent outputs.

The speedup depends on the dataset and hardware; running concurrently may reduce *total* throughput if the machine is saturated. The 4–10× speedup is an engineering target, not a verified guarantee.
