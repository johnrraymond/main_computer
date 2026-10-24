# Learned Gaussian Head Fusion V2

This is an exploratory **joined-head experiment**, not a correction to the reference Gaussian predictor. The three frozen inputs are:

1. The verified 2,000-bank numerical-input Gaussian reference (**not Qwen**).
2. The independently trained Pythia-70M Gaussian head.
3. The independently trained TinyStories-33M Gaussian head.

For **each** source, the script captures the complete X/Y density vectors (256 bins each), both density means and variances, both predicted locations, and 128 learned encoder features. The joined input has 3 × (128 + 6 + 512) = **1,938** dimensions at the standard settings. It does not include oracle labels or target densities.

The new head has three separate learned channel projections, a shared MLP, an **independent signed-log X/Y estimator**, and an explicit zero-target classifier. **There is no additive reference prediction, frozen pretrained fusion model, or final-layer zero initialization.** Z is derived from the fused X/Y. The 5% certification score is calculated using the existing exact-zero, all-masks-per-instance convention.

A separate reference-only head has the same architecture and training schedule but receives zeroed Pythia/TinyStories channels. This is a control: compare full fusion against it to test whether the additional sources contribute anything beyond a trained reference-only model. The report also measures input-channel ablations of the selected fusion model.

## Invocation

```text
python -m tools.nanojev_hawk_dove_gaussian_joined_fusion \
    --reference-checkpoint C:/Users/subsi/NanoJev/runs/hawk_dove_gaussian_2000_blind_matrix_v1_replay-1/best.pt \
    --duo-root C:/Users/subsi/NanoJev/runs/hawk_dove_gaussian_two_backbone_fresh_v1 \
    --output C:/Users/subsi/NanoJev/runs/hawk_dove_joined_fusion_v2 \
    --device cuda --steps 12000 --probe-every 250 --batch-size 128
```

To extend from 12,000 to 24,000 **cumulative** updates, use the identical paths, `--resume --steps 24000`. The restart restores model, AdamW, sample RNG, and Torch RNG. `best.pt` and `best_reference_only.pt` are selected from **trained checkpoints only**; `latest.pt` always reflects the last completed step.

Output files: `feature_cache.pt`, `latest.pt`, `best.pt`, `best_reference_only.pt`, `history.json`, `predictions_dev.jsonl`, `predictions_test.jsonl`, `config.json`, `matrix_split_manifest.json`, and `report.json`.

## Scientific restrictions

The original three heads learned on these TRAIN matrices and their best states were selected using DEV; prior TEST results have been inspected. These fusion outcomes are **exploratory, not new blind certification**. If fusion improves, new independently held-out payoff matrices with new frozen-activation captures are needed for a confirmatory test. No upstream head/checkpoint/cache is modified by this script.

The old `nanojev_hawk_dove_gaussian_fusion` script is intentionally untouched; its 402-feature additive-reference experiment remains available for comparison. Its checkpoints are **not compatible** with this new experiment.
