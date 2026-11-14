# NanoJev: Cross-transfer and natural repair of a compact defect cluster

The tool `tools.nanojev_emp_defect_cross_transfer` is a **read-only diagnostic**
for the completed competence-first isolated-defect spiral. It does not train
or change any of the ordinary EMP heads, Hard EMP, detector, saved optimizers,
or radius checkpoints.

## What it tests

The radius-464 snapshot contains two logged defects, `-456 + 443 = -13`
and `-457 + 444 = -13`, each erroneously predicted as `-113`.
Their two 5×5 neighborhoods overlap in a four-error cluster. The goal is to
test whether activation predictions derived from correct nearby computations
correct all four cluster errors, not merely the one case on which the original
seven single-site corrections were found.

The tool **discovers the actual incorrect cards by rescoring the union** of
both archived grids at the saved stage-27 checkpoint; it never hard-codes the
four answers. It then rescans the same cards at the separately saved stage-28
checkpoint to measure natural training repair and regression.

For each failed card and each of the same eight predetermined intervention
sites, it constructs two competing local activation predictors:

* A degree-2 two-coordinate ridge model using correct nearby `(m, delta)`
  cases; and
* A linear same-answer ridge model using correct cases with *identical*
  `delta`, bracketing the failure in magnitude.

Both methods are evaluated on the **same withheld correct constant-answer
cases**; these are excluded from **both** fits. Per-site models that fail
the normalized heldout error gate may be probed but their interventions
are labeled **exploratory/unqualified**, never validated reconstructions.
A no-op hook and all interventions use exactly the same *single-site hook*
inference path. The baseline prediction must match unhooked scoring.

At valid sites, tests include activation substitution at several strengths,
a reversed-direction perturbation, a same-size perturbation on a different
channel, and collateral tests on withheld correct answers. The tool reports a
joint target-vs-original-error output log-score margin derived from the EMP
sign and digit heads; it is **not** a calibrated probability or an indication
of unique causal identification.

## Install and run

From the `main_computer` repository after applying the patch:

```powershell
& $py -m tools.nanojev_emp_defect_cross_transfer `
  --run "C:\Users\subsi\NanoJev\runs\nanojev_isolated_defects_v1" `
  --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
  --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
  --hard-state-dir hard_emp_frontier_v2 `
  --output "C:\Users\subsi\NanoJev\runs\nanojev_defect_cross_transfer_v1" `
  --device cuda `
  --before-stage 27 `
  --after-stage 28 `
  --min-direct-correct 6 `
  --window 2 `
  --line-extent 12 `
  --holdouts 6 `
  --min-holdouts 4 `
  --max-nrmse 0.6 `
  --doses "0,0.25,0.5,1,1.5,2" `
  --controls 6
```

Inputs required in the isolated-defect run:

* `config.json` and `history.json`;
* `stage_027/mastered_snapshot.pt` and `stage_028/mastered_snapshot.pt`;
* `stage_027/relaxed_min_direct_6/candidate_*/report.json` from the
  previous diagnostic replay.

All stage snapshots must match the run's stored configuration and stage
numbers. Output `config.json` locks the relevant snapshot SHA-256 checksums
and diagnostic parameters. A mismatched rerun refuses to overwrite evidence;
use a new output directory to change settings.

## Outputs

* `result.json`: status, natural repair/regression counts and intervention totals.
* `natural_repair.csv`: identical arithmetic inputs replayed at both stages.
* `interventions.csv`: per-error × site × method validation, repairs and
  collateral damage.
* `failure_NNN.json`: fitted values, heldout errors, graded intervention
  responses, target/wrong-class margins, sham controls, and before/after
  activation values.

### Evidence boundaries

A successful intervention is a local causal perturbation of the model's
output. It is **not** proof that the substituted activation is the true
latent computation, nor does it identify a unique responsible neuron. A
reconstruction that does not predict withheld *same-answer* correct cases is
explicitly unqualified. Comparisons between checkpoints cannot distinguish
weight changes directly causing a particular error repair from correlated
training effects without additional controls.
