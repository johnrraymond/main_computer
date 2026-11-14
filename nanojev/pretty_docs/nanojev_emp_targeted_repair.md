# NanoJev targeted defect repair — selective checkpoint-delta transplant

## Scope

This is the first executable **detect → diagnose → targeted repair → verify** experiment for the observed four-error cancellation cluster. It does **not** perform further training. It uses the measured, successfully learned *stage-27 → stage-28 backbone weight delta* as a candidate source of repair, then removes every parameter group/tensor that can be omitted while preserving the four local fixes and the 26 previously correct cases.

The resulting subset is **greedily locally irreducible**, not globally minimum and not evidence that each selected tensor is individually necessary under all other possible subsets. The old sign/digit readouts are *always* retained. The original training checkpoints and source artifacts are read-only. This does not synthesize a repair for a new defect unless a suitable learned donor checkpoint already exists.

## Run

```powershell
& $py -m tools.nanojev_emp_targeted_repair `
  --run "C:\Users\subsi\NanoJev\runs\nanojev_isolated_defects_v1" `
  --cross-transfer "C:\Users\subsi\NanoJev\runs\nanojev_defect_cross_transfer_v1" `
  --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
  --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
  --hard-state-dir hard_emp_frontier_v2 `
  --output "C:\Users\subsi\NanoJev\runs\nanojev_targeted_repair_v1" `
  --device cuda `
  --before-stage 27 `
  --after-stage 28 `
  --guard-radii "128,256,384,464" `
  --guard-per-radius 1024 `
  --max-guard-regressions 0
```

## Isolated-run configuration and seed

Guard cards use the seed and radius/delta schedule from the original
`nanojev_isolated_defects_v1/config.json` under `parameters`. The reader also
accepts a legacy flat configuration, but refuses absent seed/schedule fields.
**Do not replace the missing seed with a default**: the seed determines the
TRAIN/DEV/TEST bucket assignment. An interrupted guard-generation run may be
rerun with the same output path; no checkpoint or census needs to be changed.

## Gates and safety

1. Validate source snapshot and natural-repair evidence hashes; refuse any configuration drift.
2. Replay B0R0 and the previously verified B1R0 (all changed backbone tensors and old sign/digit heads) on all 30 exact local cards.
3. Starting from sufficient B1R0, greedily attempt to delete broad backbone component changes and then individual tensor changes. Retain deletion only when **4/4 fixes + 0/26 control damage** still hold. The learned parameters never change via optimization.
4. **After** candidate selection, evaluate baseline and targeted repair on a separate, deterministic TEST-bucket arithmetic guard at several already trained radii. Neither the guard cards nor their labels are used to choose any parameter subset. Reject the export if more than `--max-guard-regressions` previously correct guard predictions become incorrect. This is an additional finite acceptance sample, *not* an untouched prospective benchmark; the original training pipeline also evaluated TEST during radius advancement.
5. If certified, serialize a **new, standalone** `targeted_repair.pt`, reload it and revalidate local arithmetic. Checkpoint schema and provenance identify it as a targeted subset, not an original radius-stage snapshot. Do not overwrite any stage or deploy it automatically.

If the guard rejects, the tool writes the diagnostics but **does not write a repair checkpoint**. The `--max-guard-regressions` default is 0, and loosening it changes the safety standard.

## Artifacts

- `result.json`: status (`certified` or `guard_rejected`), four-defect repair gate, previously correct controls, guard results, selected changed tensor list, checkpoint SHA-256 if certified.
- `search.json` and `search_trials.jsonl`: entire greedy deletion record for review.
- `selected_tensors.csv`: changed tensor shapes/element counts and delta norms.
- `local_cases.csv` and `guard_cases.csv`: exact before/after predictions and correctness.
- `targeted_repair.pt` **only on certification**: model state plus source checkpoint SHA-256s and acceptance evidence. **Read-only standalone diagnostic, not an automatic model deployment.**

## Research limitations

This proves whether a partial *already-learned* parameter update is sufficient on the studied samples. It does not prove a globally minimal edit, a unique neuron, or general arithmetic correctness. Guard suites contain finite samples; failures elsewhere remain possible. A true new-defect repair without any learned donor checkpoint would require a separate constrained parameter-editing optimization and independent verification.
