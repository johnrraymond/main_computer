# Gaussian fourth head: reference recovery, then joined-head learning

**Status:** exploratory, restartable, separate experiment. This does not modify the three frozen heads or the earlier fusion experiment.

The earlier *joined-from-random-signed-log* head failed to recover a readout already present in the reference Gaussian source features. The new script `tools.nanojev_hawk_dove_gaussian_staged_fusion` enforces this invariant:

> Before jointly optimizing the reference, Pythia and TinyStories features, a **trainable fourth-head readout** must reproduce the frozen numerical reference's 5%-joint DEV performance on exactly matched held-out cases. Do not proceed if recovery fails.

## Architecture and honesty about the readout

The frozen sources are numerical-input Gaussian reference, Pythia-70M Gaussian head, and TinyStories-33M Gaussian head. Each supplies 128 hidden activations, 2 location predictions, 2 density means, 2 variances, and 512 full density values, for 1,938 features total with 256 bins. Source weights never train.

The fourth head owns its own learned projections, joint network, a **single trainable 2-dimensional numerical output layer**, and a zero classifier. The numerical output layer receives both the trainable joint state and the reference density-mean feature as inputs. It does **not** invoke the frozen reference model at inference or add a separately frozen reference-output tensor. This is an **explicit trainable reference readout pathway**; it is intentional and should **not** be mistaken for a demonstration that the fourth head can infer the reference prediction without access to that reference output feature. The `reference-only` ablation retains only the reference channel.

**Stage 1 (teacher readout fit):** Extract features from verified source checkpoints. Using TRAIN only, fit the fourth head's affine numerical readout to the reference density means by float64 least squares. This is a supervised closed-form fit, **not a gradient-descent training epoch**; its fitted weights are part of the fourth head and remain trainable. Independently evaluate DEV on the strict 5%-relative X/Y, all-prompt-masks criterion. If a tier drops more than one of the standard 384 cases versus the verified reference, refuse joint training. Save immutable `recovered.pt` and recovery evidence upon success.

**Stage 2 (joint gradient training):** Clone the recovered head into a full three-source learner and a reference-only control. Both receive identical instances, supervised true numerical X/Y targets, AdamW learning rates and number of optimizer updates, but the control's other source channels are zero. The full model's joint branch and **the same recovered numerical readout** train on all three. Compare DEV after each `--probe-every` interval; retain the best learned fourth head *including Stage 1 as a valid, already fitted fallback*. If no improvement occurs, report `best_joint_step: 0`, `best_phase: RECOVERED_TEACHER` and completed Stage 2 updates separately. This is NOT an untrained random-initialization checkpoint: the supervised Stage 1 fit already ran.

The output is X/Y (linear-domain decoding) and derived Z. The zero classifier is trained only during Stage 2, since the reference readout does not produce exact-zero hits.

## Install and run

```text
python new_patch.py nanojev_gaussian_staged_fusion_recovery_patch.zip --dry-run
python new_patch.py nanojev_gaussian_staged_fusion_recovery_patch.zip
```

The previous joined-fusion run already captured the frozen 1,938-dimensional features. Reuse that cache rather than recomputing it:

```text
python -m tools.nanojev_hawk_dove_gaussian_staged_fusion \
  --reference-checkpoint "C:/Users/subsi/NanoJev/runs/hawk_dove_gaussian_2000_blind_matrix_v1_replay-1/best.pt" \
  --duo-root "C:/Users/subsi/NanoJev/runs/hawk_dove_gaussian_two_backbone_fresh_v1" \
  --reuse-feature-cache "C:/Users/subsi/NanoJev/runs/hawk_dove_joined_fusion_v2/feature_cache.pt" \
  --output "C:/Users/subsi/NanoJev/runs/hawk_dove_joined_fusion_staged_v1" \
  --device cuda --steps 12000 --probe-every 250 --batch-size 128
```

The reference/duo checkpoints and matrix hashes must match the reused cache. The script fails closed on mismatches and on inability to recover reference DEV accuracy. It makes its own resumable feature-cache copy.

To continue after a clean completion or interruption, repeat the command with `--resume` and a larger cumulative `--steps` target, for example `--steps 20000`. The cached features and model/optimizer/RNG states are reloaded and fingerprint-checked. The saved recovered checkpoint is never mutated.

## What to inspect

- `recovery_evidence.json`: fitted TRAIN rows, DEV gate, the verified baseline and student scores, and maximum teacher-versus-student numeric output difference.
- `recovered.pt`: independently fitted Stage 1 fourth-head weights, which must pass the gate.
- `latest.pt`: complete Stage 2 optimizer, RNG, and model state; supports restart.
- `best.pt`: DEV-selected fourth head, either the recovered readout or a later improved three-source version.
- `best_reference_only.pt`: independent reference-only trained control selected on DEV.
- `history.json`: joint optimizer steps and accuracy/loss for fusion/control.
- `report.json`: recovery proof, checkpoint-selection phase, DEV and TEST comparison, and completion counts.

### Evaluation limitations

Existing base heads were trained on TRAIN and selected on DEV, and the historical TEST set has been inspected. This is **not out-of-fold stacking** nor fresh blind certification. A performance gain should subsequently be validated on newly generated payoff matrices that neither the base heads nor the fusion-selection process has examined. Do not select models or weights based on TEST.

### Tests

```text
python -m pytest -q tests/test_nanojev_hawk_dove_gaussian_staged_fusion.py
```

The small fixture exercises teacher fitting, refusal on poor recovery, nonreference-source gradients, checkpoint preservation, and exact interrupted-versus-uninterrupted CPU restart. It does not prove GPU-level performance or a specific DEV score on the user's 2,000-bank checkpoint.
