# NanoJev: replay failure neighborhoods with activation maps

## Objective

For each first-failure event, **reproduce the recorded answers on the model
state that produced them** and map a dense local neighborhood with the **same
ordered signed operand branch**. Record exact correct and incorrect answers,
layer/channel activations, activation anomalies relative to nearby correct
controls, and selected single-channel ablation results. Keep the four-head and
Hard EMP campaigns untouched.

## Historical snapshot limitation

The original `nanojev_emp_first_failure_v1` completed before the per-ring
snapshot feature existed. Its `latest.pt` contains only the **final state**, not
the intermediate weights at which rings 2, 4, 5, 6, 9, 10, and 11 failed. A
final-state replay **cannot reconstruct historical activations**. Do not treat
matching predictions as proof of a historical activation replay. This tool
refuses a missing event-time snapshot unless you opt in to `--checkpoint-source
latest` and clearly marks that output **not historically reproduced**.

### Inspect old failures with the final checkpoint (no retraining)

```powershell
& $py -m tools.nanojev_emp_failure_neighborhood `
    --run "C:\Users\subsi\NanoJev\runs\nanojev_emp_first_failure_v1" `
    --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --hard-state-dir hard_emp_frontier_v2 `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_failure_neighborhood_final_v1" `
    --device cuda --ring all --checkpoint-source latest `
    --m-window 8 --delta-window 8 --controls 8 --ablate-top 8
```

Each `report.json` distinguishes `center_original` from `center_current` and
records the original and replay predictions for each archived failure and
correct control. If the final head has learned to solve a historical failure,
the report says so; it never calls that a historical repair mechanism.

### Capture true event-time states and reproduce them

Re-run `tools.nanojev_emp_first_failure` with **the same spiral arguments and a
new output directory**, such as
`C:\Users\subsi\NanoJev\runs\nanojev_emp_first_failure_snapshots_v2`.
The patched spiral saves `ring_XX/probe_snapshot.pt` **after memorizing the
current ring and before training on the next**, and it still saves the original
activations and errors. This is a separate clone; it does not reset or alter
the four-head campaign. A new run need not produce identical failures to the
old one; its recorded event and saved snapshot are internally paired.

Use the neighborhood command above with `--run` set to the new output and omit
`--checkpoint-source latest`. The default `event` mode loads that ring's exact
snapshot and verifies the archived failure plus every archived nearby correct
and failed neighbor. If **any** prediction differs, it stops with
`NEIGHBORHOOD_EVENT_SNAPSHOT_MISMATCH` instead of supplying a misleading map.

## Local geometry

The original failed pair `(a,b)` determines an anchor magnitude `m`, residual
`delta`, operand sign, and swap order. The grid spans `m ± --m-window` and
`delta ± --delta-window`, plus magnitude landmarks at 98% and 102% to sample
larger-magnitude scale neighborhoods. It uses the **same sign and swap** for
each pair, so the error map doesn't mix arithmetic regimes or operand
orientations. All gold results are recomputed as `a+b`.

Unlike the experiment's original sparse outward DEV anchors, the neighborhood
probe intentionally includes TRAIN/DEV/TEST magnitude families as **diagnostic
only**. `magnitude_fold` is retained with each case. No local probe case is fed
back to training, and apparent TRAIN-family success must not be called
out-of-distribution success.

The closest correct neighbors serve as activation baselines. Each layer/round
is recorded through the original forward hooks. A coordinate's anomaly is
`|activation - mean(correct neighbors)| / max(std(correct neighbors), .05)`;
top-ranked channels are drawn as their own local spatial maps. These are
**observational differences**; the separately recorded zero ablations measure
whether disabling a channel repairs the failed output and how many nearby
correct answers it damages. There is no claim of a universal math neuron.

## Files per selected ring

- `report.json` — original vs replayed predictions; whether historical
  reproduction was verified; top layer/channel anomalies; ablation results.
- `arithmetic_neighborhood.svg` — local correct/incorrect heat map, with failure
  coordinate outlined.
- `activation_neighborhood.svg` — aggregated activation anomaly heat map.
- `channel_*.svg` — individual high-ranked activation channels over the same grid.
- `all_local_predictions.jsonl.gz` — full operands, predictions, correctness,
  coordinates, and TRAIN/DEV/TEST fold for every local example.
- `local_activations.pt` — raw activation tensors for all local examples.

`summary.json` summarizes all investigated rings. The output is independent and
resume-safe against parameter drift. The original run and trained weights are
never written to.
