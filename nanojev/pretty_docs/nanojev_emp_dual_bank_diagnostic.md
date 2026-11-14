# Four-bank outward ladder stall diagnostic

## Purpose

This **read-only** probe determines why the four-bank outward ladder can halt on the Left.Left 80% DEV threshold while Right.Right remains strong. It does not train, update a checkpoint, tune the model, change the gate, access sealed TEST, or import any external arithmetic executor. The only output is a standalone JSON report **outside** the live campaign directory.

The probe intentionally compares **Left.Left to Left.Right** (the bank that actually drives Left.Left's `right_answer_boost`) **and** Left.Left to Right.Right (the independent bank presently ahead on ±8,192). Those relationships must not be conflated.

## Run from the Main Computer repository

Stop or finish the currently running trainer first, so the `latest.pt` checkpoint cannot change during inspection.

```powershell
& $py -m tools.nanojev_emp_dual_bank_diagnostic `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --radius 8192 `
    --compare-parent `
    --device cuda `
    --top 12
```

No `--resume`, `--stage-steps` or `--source-run` is required: this is not a training command.

The default report is at:

`C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1_diagnostic_8192.json`

Use `--report` to choose a different output location **outside** the campaign.

## Diagnosis emitted

- `EMP_DIAG_SUMMARY`: each of four banks' DEV A/B frontier and outer 5%-accuracy, scale bias/stddev, and tipping counts.
- `EMP_DIAG_PAIRED`: same-example correctness disagreements both between the lead banks and **within** each EMP. `right_lower_absolute_error` on the Left EMP describes how many examples would activate the existing answer-based Left.Left boost.
- `EMP_DIAG_HARD_GROUPS`: cohorts ranked by Right.Right–Left.Left accuracy gap. Cohorts are residual size (powers of two), operand size, decimal length, residual sign, and operand-sign relation. Disaggregated rates are descriptive; tiny groups are excluded from this console ranking but remain in the JSON.
- `EMP_DIAG_CONTROLS`: saved per-bank learning rates, train frequency, emphasis multipliers, and observed optimizer step counters. The **checkpoint** controls are used, not uncommitted `controls.json` changes.
- `EMP_DIAG_PARENT`: if `--compare-parent` is set, evaluate the accepted previous radius checkpoint and the current checkpoint on the **same** current-radius and previous-radius DEV cards. This separates loss of earlier capacity from failure to generalize outward. It makes additional forward passes but no optimizer updates.

The full JSON report also stores exact DEV cards where Right.Right was within 5% while Left.Left was wrong, with the four predictions side-by-side. The report never includes TEST examples.

## Interpreting results

1. If Left.Right rarely has a lower absolute error than Left.Left, increasing `right_answer_boost` further will rarely activate on representative DEV cards; inspect whether its TRAIN advantage is similarly scarce before any training change.
2. If Left.Left fails primarily by tipping (`prediction/target <= 0`), prioritize sign/cancellation behavior. If it remains correctly signed but `toward_zero` dominates, prioritize magnitude calibration.
3. If failure clusters in one residual or operand magnitude band, train or sample that band deliberately *using TRAIN cards*, rather than enlarging the whole stage budget blindly.
4. If the current checkpoint is weaker on the **old** radius than the accepted parent, the stalled regime includes forgetting/interference. If old-radius retention holds but new-radius accuracy is poor, the main issue is outward generalization.
5. Compare saved learning rates and optimizer counters before declaring a bank “stuck.” The snapshot shows whether its optimizer has been taking steps, not whether its gradients have been useful.

No result is a sealed TEST certificate or proof of the optimizer's underlying causal mechanism.
