# NanoJev: automatic parameter-group discovery for donor-free arithmetic repair

This lab asks whether a defective arithmetic checkpoint can **discover which components to modify** while constructing a targeted repair. It does **not** assume the five input tensors or the 288 coordinates found by the earlier donor-based experiments. It does **not** load the radius-480 snapshot, any donor weight differences, the donor-free repaired snapshot, an oracle mask, or any already repaired model. It starts only with the radius-464 pre-repair checkpoint, correctly labeled defect examples, and correct arithmetic controls.

## Run on the observed radius-464 defect

Install the accompanying `new_patch.py` ZIP, then run:

```powershell
& $py -m tools.nanojev_emp_autodiscover_repair `
  --run "C:\Users\subsi\NanoJev\runs\nanojev_isolated_defects_v1" `
  --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
  --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
  --hard-state-dir hard_emp_frontier_v2 `
  --output "C:\Users\subsi\NanoJev\runs\nanojev_autodiscovered_repair_v1" `
  --device cuda `
  --stage 27 `
  --search-steps 300 `
  --check-every 50 `
  --guard-radii "128,256,384,464" `
  --guard-per-radius 1024 `
  --max-test-regressions 0
```

For broader searches after a no-candidate outcome, increase `--search-steps` (e.g., 600), `--max-groups` (default 14) or `--max-candidate-elements` (default 100000), **and choose a new output directory** to avoid overwriting experiment provenance. Do not relax `--max-test-regressions` to force a result. A larger budget does not guarantee repair.

## What happens

1. Load the **pre-repair** checkpoint at `stage_027/mastered_snapshot.pt`. Reproduce the four known wrong answers and verify that the 26 local control problems are already correct. Only a defect CSV (`--defects a_b_target.csv` with columns `a,b,target`) can substitute explicit known errors; the CSV must contain valid arithmetic solutions.
2. Collect correct training cases and the correct local controls. Derive disjoint TRAIN, DEV and TEST arithmetic guard sets from the original run's saved sampler parameters; known local pairs are excluded from guards. Note that historical training may have already involved the program's hash TEST fold, so these are disjoint from *this repair search*, not necessarily untouched by all historical model development.
3. Compute **gradient signals for all named floating-point model parameters**, including input encodings, transformer layers, norms and output heads. Compare gradients for known errors against gradients for correct controls. Rank parameter groups using gradient magnitude, size normalization and control-gradient conflict. This ranking is heuristic and may not reveal the underlying causal fault uniquely.
4. Run **independently initialized** (zero-update) optimization attempts over progressively larger ranked group prefixes (`--expand 1,2,3,5,8,11,14` by default). Within a budget, freeze all excluded parameters *exactly*, preserve old correct-output distributions, penalize parameter drift, and train against the four errors. The best qualifying attempt is retained at each budget. A lower-budget success stops search.
5. Greedily revert the learned updates for one discovered group at a time; keep only removals that continue to fix all known defects and pass the TRAIN and DEV preservation gates. No later trained weights are used for the edits.
6. Select on known errors, original local controls, TRAIN and DEV **only**. Evaluate the TEST guard **once after selection**. If it shows more regressions than `--max-test-regressions`, refuse to export a repair. Otherwise export and round-trip verify `autodiscovered_repair.pt` in the output directory, leaving source snapshots untouched.

## Output and interpretation

- `EMP_AUTO_REPAIR_DISCOVERY`: all gradient-bearing ranked groups and attempted group counts.
- `EMP_AUTO_REPAIR_CHECK`: per-budget/per-checkpoint defect fixes and development regressions.
- `EMP_AUTO_REPAIR_RESULT`: status and retained group names.
- `discovery.json`: complete group-to-tensor mapping, scores and rank order.
- `budget_NN_checks.json`: preservation results for every attempted candidate budget.
- `result.json`: search results, attempted budgets, ablation log, TEST results and checkpoint hash.
- `autodiscovered_repair.pt`: written **only** if all gates pass.

`status: no_qualified_candidate` or `test_rejected` is an expected **scientific outcome**, not license to weaken acceptance criteria. A successful run demonstrates that the system *found editable parameter groups itself for this known defect*, not that it can discover defects from unlabeled input, uniquely identify causal parameters, or repair arbitrary unseen errors. Once this works for one cluster, evaluate independent defect clusters and seeds without using post-repair information for group discovery.
