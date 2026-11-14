# NanoJev exhaustive five-tensor donor repair analysis

This is **Experiment 1: find the smallest sufficient subset** of the five input-encoding tensor updates already certified by `tools.nanojev_emp_targeted_repair`.

It is **not donor-free repair** and does not train or update any of the original checkpoints. The stage-464 and stage-480 weights and fixed four-failure/26-control deck are sourced and hash-verified from the saved run. The existing targeted-repair checkpoint must have `status: certified` and contain exactly five `input.*` donor-updated tensors.

## Run (PowerShell)

```powershell
& $py -m tools.nanojev_emp_repair_exhaustive `
    --targeted "C:\Users\subsi\NanoJev\runs\nanojev_targeted_repair_v1" `
    --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --hard-state-dir hard_emp_frontier_v2 `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_targeted_repair_exhaustive_v1" `
    --device cuda `
    --holdout-radii "160,224,320,448" `
    --holdout-per-radius 1024 `
    --max-guard-regressions 0
```

## Gates and search

- Test **all 32 subsets**, including no updates and all five updates. The original pre-repair sign/digit readouts remain fixed for every subset.
- Require **all four** known defects repaired and **zero** regressions among the 26 previously correct local controls.
- Test every locally sufficient tensor subset on the same deterministic, previously used TEST-bucket arithmetic guard (previous run: 3,388 cases). A subset counts as screening-qualified only when the guard has no more than `--max-guard-regressions` regressions.
- Choose the qualified subset with the **fewest donor-updated tensors**, breaking ties by changed-element count and local gold log score. Thus the selected tensor subset is **globally smallest in tensor count among these five donor tensor updates**, not a universal repair optimum.
- By default, greedily remove unnecessary **embedding-row updates** and then unnecessary **shared-width dimension updates**, checking both local correctness and the screening guard after each successful proposed deletion. `--no-refine-rows` and `--no-refine-dimensions` skip these phases. Fine-grain pruning is greedy, not globally optimal; all remaining parameter values must equal either the exact old value or the exact donor value.
- Only after selecting and refining the winner, run a **separate post-selection guard** using different arithmetic radii: `160,224,320,448` by default. Input pairs in the original 30-case deck and screening guard are excluded from this audit. If any new regression appears, **do not export**; the holdout is not used to pick a replacement candidate.
- No gradient updates, no alternate donor, no readout modifications, no changes to the source checkpoints.

## Outputs

- `combinations.csv`: exactly 32 rows including mask, selected tensors, repairs, local-control damage, and screening guard outcomes.
- `combinations.json`: the full matrix and qualified-subset count.
- `refinement.csv`: each attempted row/dimension deletion and whether it passed both gates.
- `active_delta.json`: exact active embedding rows and dimensions for retained donor edits.
- `local_cases.csv` and `final_holdout_cases.csv`: casewise results for the winner, including the untouched post-selection audit.
- `result.json`: tensor-level optimum, number of remaining updated elements, and independent post-selection audit.
- `exhaustive_repair.pt`: **only** when all gates pass, with SHA-256 provenance of both snapshots and original certified donor repair.

The exact original screening cards are checked against the original certified `guard_cases.csv` before searching; card drift aborts the experiment. The original screening guard has been used before in prior NanoJev research. The extra audit has no overlap with this experiment's screening population, but neither finite population is universal correctness proof. The program's optimizer and model weights are never changed; the exported checkpoint is a new artifact.
