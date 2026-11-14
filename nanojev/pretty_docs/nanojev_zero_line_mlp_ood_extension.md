# NanoJev: MLP interval specialist OOD continuation

## Purpose

Measure whether the signed residual ±16–32 **isolated MLP specialist** continues to improve on its existing unseen-operand-magnitude OOD population after 3,000 training steps. This is a **separate experiment** from the Gaussian and incremental MLP bank harnesses. It does **not** train or evaluate routing for promotion.

## Run (Windows, existing `.venv-diagram` environment)

```powershell
& $py -m tools.nanojev_zero_line_mlp_ood_extension `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --mlp-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_bank_mlp_16_32_3000_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_bank_mlp_16_32_ood_extension_v1" `
    --interval "16:32" `
    --device cuda `
    --additional-steps 6000 `
    --probe-every 500 `
    --batch-size 128
```

This performs 6,000 **additional** specialist update steps from the learned 3,000-step bank weights, producing observations at +0, +500, +1000, ... +6000. Because the original incremental harness saved **weights but not optimizer state**, the extension starts with **fresh AdamW moments**; this is a warm start, not an optimizer-exact continuation of the original training. The underlying source checkpoint, original MLP specialist, and Gaussian bank remain immutable.

To restart after an interruption, run the identical command with `--resume` added. Restarting requires the same output directory and configuration; it uses `latest.pt` to recover bank, optimizer, sampler RNG, and metric history.

## Measurements and interpretation

`ZERO_MLP_OOD_PROBE` shows TRAIN, DEV and OOD exact accuracy at every checkpoint. The complete trajectory is in `result.json`. Fixed TRAIN/DEV and OOD sample populations, matching those of the source experiment, are reused. OOD is never used in loss, gradient updates, or checkpoint selection. These repeated OOD checks are diagnostic, not a replacement for final blind evaluation.

At baseline, the harness requires DEV and OOD accuracy to reproduce the original saved specialist's results exactly; it refuses to run otherwise. Failure usually indicates changed data parameters, network construction, or the wrong source directory. Checkpoint source SHA256 is verified before and after training.

Look at the late OOD trend (+3000, +4000, +5000, +6000), not just the final reading. A flat or regressing OOD curve while TRAIN/DEV remain near 100% supports a generalization ceiling under this **data distribution and architecture**, not a global impossibility result. If OOD continues climbing, the hypothesis that 3,000 steps was too early gains support.

## Tests

```bash
python -m pytest -q tests/test_nanojev_zero_line_mlp_ood_extension.py
```

All Markdown lives in `pretty_docs/` and no existing source file is replaced by this patch.
