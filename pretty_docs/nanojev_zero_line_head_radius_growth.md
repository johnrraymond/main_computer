# NanoJev zero-line residual-radius growth: frozen-backbone head experiment

## Governing research question

Can the **already-certified** radius-127 digit-token recurrent backbone encode more signed numerical displacement than its trained sign/digit head can read out? The experiment expands the *residual radius*, not the memorized operand-square radius. It does not modify the backbone, the learned input representation, the original ladder, Hawk–Dove, HybridLink, or TEST.

The model sees only two signed inputs `(A,B)` and predicts `delta=A+B` with its existing neural sign and digit heads. Python integer addition is used only to construct training targets and evaluate outputs; no arithmetic executor runs inside model inference. The regression head remains frozen and is not part of this test.

## Two-dimensional sampling

- Operand magnitude: four TRAIN/DEV bands `128–159, 160–191, 192–223, 224–255`, plus four larger **untrained diagnostic** OOD bands `256–319, 320–383, 384–447, 448–511`.
- Signed residual: *every integer* `delta` from `−128` through `+128` in the full evaluation; the same fixed evaluation population is repeated before and during head training.
- Paired signs: both signs of `A` are evaluated independently. Every cell is `(delta, magnitude band, A sign)` and must be represented.
- TRAIN versus DEV: canonical absolute-magnitude operand families are assigned to one split only, so swapping `A` and `B`, or negating both, cannot put a member of the same family in the opposite split. Sampling is deterministic; DEV examples are never trained.
- OOD: operand magnitudes >255 are entirely absent from head training. They are reported as diagnostics, **not** a blind TEST result.
- Sampling is balanced across residuals and bands; default per-cell samples are 12 TRAIN, 6 DEV, and 6 OOD. Exact counts are saved to each result.

## Training stages

Train the **sign_head** and **digit_head only**, cumulatively using `|delta|<=16`, `<=32`, `<=64`, `<=128`. In each batch, one-third of the samples revisit `|delta|<=2`, one-third focus on the new outer half of the current radius, and one-third sample the entire currently exposed radius. This preserves a high teaching density around zero without hardcoding a zero rule. The complete learned radius-127 deck is used only for retention evaluation, not rehearsal targets.

Do not interpret successful head tuning as evidence that recurrent backbone weights learned a new invariant. Comparing a frozen backbone with readout-only changes specifically tests whether representations **already** contain the requisite information.

## Strict measured radius

For each snapshot, the reported `certified_radius` is the largest *contiguous* integer range `−R..+R` for which **every residual** and each of the eight `(band, A sign)` cells reaches the configured exact accuracy threshold (default 80%). Zero must pass in every cell. Missing or undersampled cells fail closed. Report DEV radius and OOD radius separately. Thus good performance only at ±16 cannot imply a 16-wide radius if ±7 fails.

The full previously mastered radius-127 deck is also evaluated at each snapshot. The default `--retention-floor 1.0` means a candidate cannot become `best.pt` unless the original deck remains exactly correct in full. `latest.pt` is diagnostic state and may not be promotion-worthy. Results preserve immutable initial probe, a time series and CSV.

All inference for the probe uses the unchanged model architecture. No router, arithmetic specialization, or executor.

## Install

Apply the ZIP at the root of Main Computer (`main_computer_test/tools/...` maps to `tools/...`; the same for `tests` and `pretty_docs`). Requires existing `tools.nanojev_addition_curriculum`, `tools.nanojev_zero_line_ladder`, and `tools.nanojev_zero_line_radius255_probe` from the prior patches.

## Read-only baseline sweep (recommended first)

```text
python -m tools.nanojev_zero_line_head_radius_growth --phase probe --ladder-output C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1 --output C:\Users\subsi\NanoJev\runs\nanojev_zero_line_radius_growth_baseline_v1 --device cuda --max-residual 128 --eval-batch-size 512
```

This records baseline exact accuracy by **every integer residual**, all four magnitude bands, and each sign of `A`, in both held-out DEV and out-of-magnitude diagnostic sets. It does not train or modify the checkpoint. It writes `result.json` and `config.json`.

## Head-only training (separate output directory)

```text
python -m tools.nanojev_zero_line_head_radius_growth --phase train --ladder-output C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1 --output C:\Users\subsi\NanoJev\runs\nanojev_zero_line_head_radius_growth_v1 --device cuda --max-residual 128 --steps-per-stage 750 --probe-every 150 --batch-size 128 --eval-batch-size 512
```

Expected stage logs: `ZERO_RADIUS_HEAD stage=±16 ... DEV_r=... OOD_r=... retain=...`, then ±32, ±64, ±128. It writes `initial_probe.json`, `result.json`, `radius_growth.csv`, `latest.pt`, and, only if the full retention gate passes, `best.pt`. Initial results are not promises of improvement. Frozen-backbone head training can fail if information required at higher residuals is not in the frozen features.

### Resume

```text
python -m tools.nanojev_zero_line_head_radius_growth --phase train --ladder-output C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1 --output C:\Users\subsi\NanoJev\runs\nanojev_zero_line_head_radius_growth_v1 --device cuda --max-residual 128 --steps-per-stage 750 --probe-every 150 --batch-size 128 --eval-batch-size 512 --resume
```

Resume requires the same config and original checkpoint hash. New configuration requires a new directory. `--phase probe --head-checkpoint <file>` can evaluate saved learned heads in an independent probe directory.

## Acceptance and interpretation

1. Record the pretraining exact accuracy map across the full residual range, especially both signs at 16, 32, 64, and 128.
2. Confirm a **measured** increase in DEV contiguous signed residual radius after head tuning *and* no loss of radius-127 mastery.
3. Confirm whether the OOD magnitude radius increases independently. A DEV-only increase is not evidence of extrapolation to greater operand magnitudes.
4. If neither DEV nor OOD expands while training fits TRAIN, suspect missing or entangled information in frozen backbone features; only then design a separate backbone-unfreeze intervention.
5. The original strict TEST boundary remains untouched. All stage probes use fixed development/diagnostic populations.

## Safeguards

- Refuses output overwrite and overlap with source run directory.
- Requires the passed/stable radius-127 checkpoint and verifies checkpoint file digest before and after work.
- Only output sign/digit head parameters receive gradients; immutable backbone parameters are checked at completion.
- Fixed stable split key prevents swapping or sign mirroring of the same magnitude pair across TRAIN and DEV.
- Deterministic seeded sampling and RNG-preserving checkpoints for resume.
- No external arithmetic execution at inference, no Z, no HybridLink changes, no original TEST reads.
