# Focused 1–1,000 numerical decoder: stable inverse-asinh repair

This is a **separate research run**, not an in-place replacement of prior checkpoints.

## Reproduced defects

1. The old `base + (sinh(encoded_prediction) - sinh(asinh(base)))` calculates the difference between million-scale float32 values before adding `base` back. Because `sinh(asinh(base))` is not an exact inverse in float32, near-one answers can incur errors of 0.25–0.375 even when the encoded prediction is exact. The repaired inference returns `sinh(encoded_prediction)` directly, retaining exact `base` at `delta == 0` and preserving its gradient.
2. `encoded_prediction.clamp(-20, 20)` returns a large but bounded number when saturated and **zero derivative** to the raw-number loss outside bounds. The repaired clamp has identical forward bounds but uses a straight-through derivative so the numerical objective can move a saturated coordinate back toward its target.

The training objective, 21 Gaussian banks, the balanced three-band dataset, and frozen Stage 2 remain unchanged. The repair is a controlled numerical-path experiment. It does **not** prove this model can learn the numerical mapping.

## Proof without Qwen or CUDA

```powershell
& $py -m tools.nanojev_hawk_dove_focus_1k_arithmetic_probe
```

## CUDA experiment A: original learning rate, stable decoding

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21_focus_1k_stable `
    --device cuda `
    --relative-max 2000000 `
    --decoder-init-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_v1\latest.pt" `
    --mixture-train-per-tier 900 `
    --mixture-new-dev-per-tier 180 `
    --learnability-train-per-tier 0 `
    --experiment-arm decoder-only `
    --retention-fraction 0 `
    --lr 0.0001 `
    --additional-steps 750 `
    --max-steps 30000000 `
    --chunk-steps 250 `
    --views 32 `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_focus_1k_stable_lr1e4"
```

## Optional CUDA experiment B: lower learning rate

**From exactly the same original 21-bank checkpoint** (not A's latest). Copy the above command, change `--lr 0.0001` to `--lr 0.00001` and output directory to `hawk_dove_focus_1k_stable_lr1e5`. A/B is a learning-rate test, not a different model.

## Read-only monitor

```powershell
& $py -m tools.nanojev_hawk_dove_focus_1k_stable_monitor `
    --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_focus_1k_stable_lr1e4"
```

In addition to prior 5%-relative accuracy, reports contain train/held-out median, p90, max and mean absolute numerical error per band, signed-asinh coordinate error, and fraction of predictions hitting the bound. MAE can rise while log-relative error falls, so assess held-out raw accuracy and error, not training loss alone.

## Interpretation

- **Saturation fraction high**: numerical outputs are hitting the safety clamp; the numerical gradient is now permitted to recover, but target-range improvement must be measured.
- **Median decreases, mean increases**: rare huge errors drive MAE; examine p90/max and corrected-case counts.
- **Both train and held-out errors decrease, and 5%-relative accuracy grows**: evidence of learning.
- **Train and held-out both fail after enough steps**: inspect the output information in the frozen bank distributions versus frozen Qwen feature vectors, and test alternative objectives, rather than generating more of the same samples.

No test split is used during training or monitoring. Full CUDA learning is not available in the packaging environment.
