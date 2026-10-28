# NanoJev Natural Repair Activation Tomography

## Purpose

Compare the same 30 arithmetic cases at the saved mastered checkpoints from
stages 27 and 28. Stage 27 has four known `−100` errors; stage 28 fixes them.
Read the *existing* cross-transfer evidence (`natural_repair.csv`, `config.json`)
and verify the two snapshot SHA-256 values and exact predictions. No training.

## Run

```powershell
& $py -m tools.nanojev_emp_natural_repair_tomography `
    --run "C:\Users\subsi\NanoJev\runs\nanojev_isolated_defects_v1" `
    --cross-transfer "C:\Users\subsi\NanoJev\runs\nanojev_defect_cross_transfer_v1" `
    --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --hard-state-dir hard_emp_frontier_v2 `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_natural_repair_tomography_v1" `
    --device cuda --before-stage 27 --after-stage 28 `
    --top-per-layer 4 --controls 6
```

## Methods

1. Reproduce all 30 arithmetic predictions at *both* immutable snapshots.
2. Capture every channel for the six module/invocation combinations; forward
   hooks on exactly one module at a time provide consistent measurement topology.
3. Exclude all capture/prediction disagreements, preserving the disagreement
   record in `capture_drifts.json`.
4. For every layer and channel, subtract the mean checkpoint-related change on
   the 26 previously correct controls from the mean change on the four repaired
   cases. A robust control MAD provides a descriptive scale; these scores are
   **not statistical p-values**.
5. Select top channels for each repaired input by leave-one-repaired-case-out
   ranking. Intervene on the held-out case only *after* selecting the channels.
6. Forward transplant: replace one early-checkpoint channel activation with
   the corresponding later-checkpoint value. Reverse transplant: replace the
   later-checkpoint channel with the earlier value.
7. Compare forward repair, reverse regression, matched-control-delta edit, and
   collateral damage on the closest six unchanged correct cases.

## Outputs

- `config.json`: immutable checkpoint hashes and experiment parameters.
- `activations.pt`: paired, parity-verified activation vectors (read with trusted
  PyTorch only).
- `capture_drifts.json`: prediction disagreements induced by capture hooks.
- `channel_changes.csv`: all 64 channels per site, including raw and
  control-adjusted changes.
- `transplants.csv`: per-error, per-channel forward/reverse/control predictions.
- `result.json`: descriptive summary and limitations.

## Interpretation

Cross-checkpoint transplant values do not necessarily have equivalent semantics
when model weights change. A positive forward rescue and reverse regression
is stronger evidence of functional involvement than activation difference
alone, but does not uniquely identify a circuit. Results concern these four
error cards and are not population estimates. Shams are checked indirectly via
capture parity; baseline and intervention use identical hooks.
