# NanoJev Isolated Defect / Local Activation Reconstruction

**Scope:** The non-hybrid four-bank Hard EMP research program. The runner clones
its trained Hard EMP, never updating the original model, four heads, detector,
or campaign checkpoints. It is independent of the older Atlas census.

## Experiment

1. Grow a **small additive radius** (rather than doubling it). Generate a
   persistent deterministic TRAIN/DEV/TEST partition of **exact ordered arithmetic
   cards** using SHA-256. Only opposite-sign/zero-line arithmetic is admitted.
2. On each radius, train on TRAIN cards only. Accept the radius only when mean
   training cross-entropy is below `--loss-target`, exact TRAIN accuracy is 100%,
   exact DEV accuracy exceeds `--mastery-gate`, and the already accepted DEV
   retention subset exceeds `--retention-gate`. Earlier TRAIN cards are
   rehearsed as training samples. A stage failing the gate **does not advance**.
   TEST cards are evaluated after acceptance only, not used for fitting or gates.
3. Save a `mastered_snapshot.pt`, read-only DEV gate predictions and a
   **larger separate DEV audit** at **each accepted radius**. The audit is not
   optimized on and is not used to choose when training stops. It is used to
   find rare interior defects that a finite mastery probe missed. After each next stage, rescore the old DEV cards to find
   previously correct cases that have become wrong (retention defects).
4. Look for *isolated errors or compact error clusters* inside the trained
   radius. All eight direct `(m±1, δ±1)` neighboring coordinates must exist,
   but only **6 of 8 must be correct** by default (`--min-direct-correct 6`).
   Eight correct yields `isolated`; six or seven yields `local_cluster`.
   Fewer than six remains `neighbor_failure`. This acceptance only starts the
   diagnostic: activation reconstruction still requires enough correct controls
   across the local window and must pass independent held-out prediction tests.
   The neighborhood retains original operand sign/order.
5. Fit layer-by-layer local linear or quadratic **ridge** surfaces from correct
   neighbors only. Reserve `--holdouts` correct neighbors. Qualify candidate
   channels only if their held-out normalized prediction error is sufficiently
   low. A failure's activation deviation is standardized by the model's actual
   held-out residual error and a noise floor. Smoothness is **tested**, not
   assumed. Failed capture parity, missing controls or unlearnable surfaces
   block attribution without altering the arithmetic map.
6. At the failed case, replace qualified channel activations with the values
   predicted by the local surface, and evaluate the arithmetic prediction.
   The same hook is installed for a **no-op control**; if its prediction drifts,
   the intervention is invalidated. Use withheld correct controls to measure
   collateral damage with their own locally predicted channel values.

The predicted activation is *not* asserted to be a uniquely correct hidden
state. Single-channel repairs are evidence for local causal influence, not
proof of a single arithmetic neuron. Multiple configurations may implement
identical arithmetic. Large/non-smooth activation transitions can be genuine.

## Install

Apply the supplied `.zip` using `python .\new_patch.py` from the
`C:\Users\subsi\main_computer` project root. It creates only the runner,
tests, and this documentation file.

## Run

```powershell
& $py -m tools.nanojev_emp_isolated_defects `
  --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
  --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
  --hard-state-dir hard_emp_frontier_v2 `
  --output "C:\Users\subsi\NanoJev\runs\nanojev_isolated_defects_v1" `
  --device cuda `
  --start-radius 32 --radius-step 16 --max-radius 512 `
  --initial-delta 8 --delta-step 1 --max-delta 32 `
  --train-size 4096 --probe-size 4096 --audit-size 8192 --stage-steps 20000 `
  --loss-target 0.001 --mastery-gate 0.999 --retention-gate 0.999 `
  --rehearsal 512 --retention-cases 256 `
  --window 2 --min-direct-correct 6 --degree 2 --holdouts 6 `
  --max-holdout-nrmse 0.6 --anomaly-threshold 3 `
  --interventions 8 --max-events 16
```

The stage is **not** marked accepted if TRAIN memorization or held-out
mastery/retention checks fail. At that point, increase the stage training
budget or improve the training curriculum; do not silently lower thresholds.
Reusing the output path with changed parameters is rejected by the resume
configuration guard. Accepted stages resume from `latest.pt`. A stage that
fails mastery saves a **`pending.pt` with model, optimizer, total steps and RNG
state**. Re-run the exact same command to give it another `--stage-steps`
training updates *from the pending weights*, rather than restarting. It may
require multiple runs before passing. If the process is interrupted mid-stage,
up to one `--check-every` interval may need repeating. The independent audit
and TEST outputs are not used in any optimizer update. Tiny integer radii may
contain fewer unique cards per fold than the requested deck size; actual counts
are reported, and sampling is bounded rather than spinning on duplicates.

## Output

- `result.json`, `history.json`, `config.json`, `latest.pt`, `pending.pt` —
  progress or unmastered stop, stage history, verified configuration, committed
  accepted weights and recoverable in-progress training.
- `stage_NNN/mastered_snapshot.pt` — exact model and optimizer at the accepted
  radius, not just the final checkpoint.
- `stage_NNN/probe_predictions.jsonl.gz` — read-only DEV gate evidence.
- `stage_NNN/audit_predictions.jsonl.gz` — wider, read-only DEV error census;
  accepted-stage audits are rescored after later training to find new defects.
- `stage_NNN/untouched_test_predictions.jsonl.gz` — separate evaluation
  population, not optimized or used for acceptance.
- `stage_NNN/candidate_XXX/arithmetic_neighborhood.svg`,
  `local_predictions.jsonl.gz` — full 2D local arithmetic observations.
- `stage_NNN/candidate_XXX/activation_residual_heatmap.svg`,
  `activation_reconstruction.pt`, `report.json` — predicted vs observed local
  activations, held-out channel quality, ranked deviations, no-op parity, and
  counterfactual replacements with collateral-control outcomes.

**Interpretation limits:** All gates certify finite, sampled regions, not all
integer inputs of the radius. DEV controls are used for stopping and defect
selection; only TEST represents untouched verification. Full-layer activation
capture can change certain transformer fast paths; any prediction drift makes
that measurement invalid. The experiment does not change the physical neuron
count or assume all errors result from activation discontinuities.

## Revisit the completed radius-464 cluster (no retraining)

The strict-isolation original run finished all 31 radius stages and saved
`stage_027/mastered_snapshot.pt` plus two rejected candidate reports. To
reprocess those exact candidates **against their original radius-464 model**:

```powershell
& $py -m tools.nanojev_emp_isolated_defects `
  --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
  --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
  --hard-state-dir hard_emp_frontier_v2 `
  --output "C:\Users\subsi\NanoJev\runs\nanojev_isolated_defects_v1" `
  --device cuda `
  --start-radius 32 --radius-step 16 --max-radius 512 `
  --initial-delta 8 --delta-step 1 --max-delta 32 `
  --train-size 4096 --probe-size 4096 --audit-size 8192 --stage-steps 20000 `
  --loss-target 0.001 --mastery-gate 0.999 --retention-gate 0.999 `
  --rehearsal 512 --retention-cases 256 `
  --window 2 --degree 2 --holdouts 6 `
  --max-holdout-nrmse 0.6 --anomaly-threshold 3 `
  --interventions 8 --max-events 16 `
  --min-direct-correct 6 --revisit-stage 27
```

Replay results go under
`stage_027/relaxed_min_direct_6/candidate_XXX/`, with summary
`stage_027/relaxed_min_direct_6/result.json`. The old `candidate_XXX` reports,
`latest.pt`, `history.json`, all stage checkpoints, and existing Atlas data
are **unchanged**. `--revisit-stage` and `--min-direct-correct` are diagnostic
options omitted from the original training checkpoint's immutable config; no
training, optimizer updates, or new radius scans occur during replay. This
path is not a substitute for an old stage snapshot and refuses to fall back
to final/latest.pt. `--min-direct-correct 8` restores strict isolation.

A `local_cluster` status means the local *geometry* qualifies; it does not
mean a smooth reconstructable activation surface was found. Check
`reconstruction_valid`, `local_model_reason`, `capture_disagreements`,
`interventions`, and `repaired` in each new report.
