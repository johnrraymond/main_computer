# NanoJev: Donor-Free Targeted Repair Lab

This is an independent experimental repair harness. It starts exclusively from the **radius-464 pre-repair checkpoint** and the **four known arithmetic mistakes**. It freezes all transformer, head and query parameters. Only the five `input.*` embedding and normalization tensors receive gradients. A later repaired checkpoint is neither required nor read.

## Run

```powershell
& $py -m tools.nanojev_emp_donor_free_repair `
  --run "C:\Users\subsi\NanoJev\runs\nanojev_isolated_defects_v1" `
  --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
  --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
  --hard-state-dir hard_emp_frontier_v2 `
  --output "C:\Users\subsi\NanoJev\runs\nanojev_donor_free_repair_v1" `
  --device cuda `
  --stage 27 `
  --modes baseline,sparse `
  --steps 1200 `
  --check-every 50 `
  --guard-radii 128,256,384,464 `
  --guard-per-radius 1024
```

Modes:

- `baseline`: optimize all 1,408 input-embedding parameters with local arithmetic and soft-output retention loss.
- `sparse`: independently initialize the same input parameters; add elementwise and embedding-row proximal shrinkage.
- `oracle`: *explicitly labeled oracle-coordinate control* using `--oracle-mask <path to active_delta.json>`. It reads which entries were selected by an earlier donor-based experiment **but never reads or loads their numerical repair values**. Do **not** mix this mode into the principal donor-free result.

`--defects` accepts a CSV of known wrong cases with `a,b,target`; the default is the original four −100 errors. The two default anchors generate the same 30-case local comparison used in previous experiments.

## Experimental controls

- All optimizer starts are independently reset to the **same old checkpoint**. No successful donor weights are used to propose updates.
- The four known failures and verified correct local neighborhood are **observed optimization evidence**, not blind tests.
- Previously correct TRAIN-bucket calculations (hash buckets 2–4) supply preservation supervision and old-model soft-logit distillation.
- A DEV-bucket (bucket 0) guard is inspected during candidate selection.
- A separate TEST-bucket (bucket 1) guard is touched **only once**, after DEV selection. All known/local cards are removed from both guards regardless of their source bucket.
- The five input tensor shapes are checked; every non-input parameter must match the old checkpoint *bit-for-bit*.
- A repair may be exported only if all four defects are correct, local previously correct calculations remain correct, configured TRAIN and DEV regression limits pass, and no previously correct TEST cases regress.
- Inference and export use the same checkpoint state. An exported artifact is reloaded and checked against the selection gates.

`config.json`, `initial.json`, `baseline_history.json`, `sparse_history.json`, and `result.json` capture experiments. The optional `donor_free_repair.pt` is written only after all gates pass. A completed run may instead correctly report `no_qualified_candidate` or `test_rejected` without exporting any checkpoint. Use a **new output directory** when changing hyperparameters.

### What this can establish

Success would show that a repair can be constructed from four arithmetic labels plus correct-case preservation data **without a later checkpoint donor**, within five preselected input tensors. It would not show that NanoJev autonomously discovers which tensors to edit, that the 288 donor-derived coordinate mask can be found without prior diagnosis, or that zero regressions generalize to all arithmetic.
