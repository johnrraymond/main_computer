# NanoJev 21-bank nested-90 learnability harness

This is an **isolated research experiment**. It does not change the existing expected-count retention runner, its checkpoints, bank geometry, generator, or the frozen Stage 2 source.

## Questions the experiment answers

1. Can the 21-bank direct numerical decoder **fit a small, certified sample**?
2. Does held-out fractional numerical error decrease at the same time?
3. Is learning possible with the existing Gaussian bank distributions frozen, or do banks need to update?
4. Does the model genuinely solve subunit nonzero targets to 5% **relative** error, rather than merely predicting zero to within 0.05 **absolute** error?
5. Does gradient clipping or a large signed-asinh correction coincide with unstable progress?

Accuracy collapse in the original integer benchmark is **recorded, not an abort condition**. The only hard stops in the learning loop are invalid/nonfinite loss, gradients, or updated parameters, and integrity failures. Original integer performance is still independently reported and gated for **deployment best** promotion.

## Setup

Unpack these additions under the existing repository root (`main_computer_test/` in the ZIP corresponds to `main_computer/` on your workstation).

Initial donor example:

```
C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_v1\latest.pt
```

### Arm A — decoder alone, frozen banks

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21_learnability `
    --device cuda `
    --relative-max 2000000 `
    --decoder-init-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_v1\latest.pt" `
    --experiment-arm decoder-only `
    --learnability-train-per-tier 32 `
    --mixture-train-per-tier 512 `
    --mixture-new-dev-per-tier 128 `
    --mixture-strict-unique `
    --retention-fraction 0 `
    --additional-steps 3000 `
    --max-steps 30000000 `
    --chunk-steps 250 `
    --views 32 `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_learnability_decoder_only"
```

### Arm B — train all 21 banks and head

Use exactly the same arguments except `--experiment-arm joint` and a **different** output directory:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21_learnability `
    --device cuda `
    --relative-max 2000000 `
    --decoder-init-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_v1\latest.pt" `
    --experiment-arm joint `
    --learnability-train-per-tier 32 `
    --mixture-train-per-tier 512 `
    --mixture-new-dev-per-tier 128 `
    --mixture-strict-unique `
    --retention-fraction 0 `
    --additional-steps 3000 `
    --max-steps 30000000 `
    --chunk-steps 250 `
    --views 32 `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_learnability_joint"
```

Run the two arms one after the other to avoid competing over the same GPU. Both import the **same read-only donor** and use the same seeded curriculum. The minibatches are deterministic. The learning rate defaults to 1e-4 (vs 1e-3 in the old experiment).

### Monitor one or both completed runs (read-only)

```powershell
& $py -m tools.nanojev_hawk_dove_learnability_monitor `
    --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_learnability_decoder_only" `
    --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_learnability_joint"
```

It reads `probe-step-*.json` but never opens checkpoints or TEST cases. It reports training and held-out mean absolute signed-asinh error, 5% strict subunit relative accuracy, the zero-predictor baseline, original integer accuracy, and the fraction of training steps requiring gradient clipping.

## To test larger-sample generalization

After the controlled-fit comparison, run a **new experiment directory** with `--learnability-train-per-tier 0` to use all 512 generated fractional examples per tier. Do not change this or `--experiment-arm` on an existing run: they are part of checkpoint identity.

Optionally set `--retention-fraction 0.25` in another separate run to test whether original integer replay improves old-task retention at a cost to subunit learning. The nested-90 distribution remains unchanged *within the fractional stream*.

## Checkpoints and conclusions

`latest.pt` is saved every chunk and can be resumed with identical arguments except `--additional-steps`.

`research_best_new_dev.pt` is selected solely by **held-out signed-asinh error**, carries `deployment_eligible: false`, and must not be treated as a promoted production checkpoint. Normal `best_*` files still require the strict legacy dual-domain promotion gate.

A falling TRAIN error is evidence of fitting; falling held-out error is the generalization test. A transient drop in accuracy is not by itself a failure. The monitor labels endpoint comparisons rather than proving convergence.

The joint arm includes a bounded Gaussian-gradient bootstrap (<= 1% of numerical-head gradient norm) only while banks initially have exactly zero numerical gradient; otherwise the existing 25% numerical-bank gradient cap applies. This avoids a deadlock where the banks never update because of a zero-initialized decoder.

No pretrained Qwen weights, font files, existing checkpoints, or original source files are included in the patch.
