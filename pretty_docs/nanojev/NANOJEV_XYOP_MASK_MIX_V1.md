# NanoJev X/Y/OP masked-information training v1

## Contract

**The only curriculum predictions are produced by the existing V3 sign/exponent/mantissa math head.** The previously installed `numeric_sidecar` tensors are kept in the existing checkpoint strictly for compatibility, but this Python runner never computes their answers, scores them, or includes their loss. Original V3 weights and AdamW moments are preserved. No output/head replacement and no new lineage.

The trainer supplies three independent out-of-band **input** channels, each with `(available, value)`, through a small learned additive adapter upstream of V3:

- `X`: `available=1, value=X/1_000_000` when exposed, otherwise `(0,0)`.
- `Y`: analogously.
- `OP`: `(1,1)` for `MAX`, or `(0,0)` if hidden. **The current Hawk–Dove curriculum contains only `MAX`; this patch does not claim to teach/reconstruct other operators.** Additional operator supervision would require genuinely varied operations.

Training rows may reveal X or Y as *assisted examples*, as explicitly chosen by the training mixture. The correct target is still used to calculate V3's original loss; the adapter never supplies an output prediction. During ALL development/probe/diagnose evaluations, the channels are `(0,0)` even if the training mixture exposes them. No sidecar results can pass a gate.

## Install (existing Main Computer patch utility)

The archive contains only complete new files rooted at `main_computer_test/`.

```text
python new_patch.py nanojev_hawk_dove_xyop_mask_mix_v1_patch.zip --dry-run
python new_patch.py nanojev_hawk_dove_xyop_mask_mix_v1_patch.zip
python -m unittest tests.test_nanojev_hawk_dove_xyop_mask_train -v
```

Stop current trainer before modifying the checkpoint. Use the same Python interpreter/environment as normal NanoJev training.

## Mixture CLI

`--xyop-mix TIER:SUBLEVEL=XYZ:WEIGHT[,XYZ:WEIGHT...]`

`TIER` is `T00`–`T08` or `*`. `SUBLEVEL` is the existing **scientific-notation mask integer** for that tier, or `*`. `XYZ` is exactly three 0/1 bits: show/hide X, Y, OP. Positive weights are relative; no normalization is necessary. Repeated `--xyop-mix` flags allow tier defaults and specific overrides. Resolution order: exact tier/sublevel, then tier wildcard, then global sublevel, then global wildcard. Unspecified cases default to `000:1` (fully hidden).

Examples:

- `000`: no supplied answers or OP.
- `100`: X supplied, Y and OP hidden.
- `010`: Y supplied, X and OP hidden.
- `001`: only OP supplied.
- `110`: X and Y supplied, OP hidden.
- `111`: all three supplied (training assistance only).

Training weights such as `000:4,100:2,010:2,001:1,110:1` give a 40%/20%/20%/10%/10% mixture respectively. Use `--mask-weight TIER:MASK=WEIGHT` (existing scientific trainer flag) to separately change **which** scientific sublevels are sampled; `--xyop-mix` changes **which information is visible** after each sublevel has been selected.

### Dry-run the resolved policy (no checkpoint changes)

```text
python -m tools.nanojev_hawk_dove_xyop_mask_train --xyop-mix T02:*=000:4,100:2,010:2,001:1,110:1 --xyop-mix T02:3=000:5,100:2,010:2,001:1 plan
```

### Extend current checkpoint without resetting V3

```text
python -m tools.nanojev_hawk_dove_xyop_mask_train upgrade
```

This creates a byte-identical backup named `latest.pre-xyop-masked-input.pt`, retains the existing numerical sidecar state **inert**, adds the trainable masked-input adapter and optimizer parameter IDs, and retains the step/RNG/optimizer moments. It does not train or run the sidecar.

### Measure actual V3 baseline (read only)

```text
python -m tools.nanojev_hawk_dove_xyop_mask_train probe --device cuda
```

Expected audit event: `xyop_v3_only_runtime` with `predictor=V3_ONLY`, `sidecar_prediction_disabled=true`, `sidecar_loss_disabled=true`, `eval_visibility=000`.

### Bounded trial on original lineage

```text
python -m tools.nanojev_hawk_dove_xyop_mask_train --xyop-mix T02:*=000:4,100:2,010:2,001:1,110:1 --xyop-mix T02:3=000:5,100:2,010:2,001:1 --additional-steps 3000 train --device cuda --chunk-steps 300 --diagnose-tier T02
```

`--additional-steps` measures relative to the current saved checkpoint. Training reports which tier/sublevel/visibility patterns it actually sampled. The model's real V3 predictions (sign/exponent/mantissa) still determine progression under the old gates; there is no automatic success claim.

## Safety and limitations

- Only **training** labels may appear in explicitly visible channels; no development/test values are provided to the model during evaluation.
- The existing scientific trainer still computes the original V3 X/Y loss for both outputs. No extra OP-loss is invented: this curriculum has only `MAX`.
- The input adapter is zero-initialized. At initialization `000` is behaviorally equivalent to the old V3 + numeric-adapter pathway. All-hidden input remains a zero additive contribution even after training.
- Sidecar is not uninstalled or deleted; do not use the old `nanojev_hawk_dove_numeric_sidecar_train probe` to judge model learning, because that older command substitutes calculator predictions.
- CUDA/Windows end-to-end validation must run locally. CPU tests verify the masked input boundaries, deterministic per-sublevel mix, gradient flow, checkpoint layout, and V3-only hooks.
