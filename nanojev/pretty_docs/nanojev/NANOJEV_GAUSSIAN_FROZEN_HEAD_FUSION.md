# Minority Report — learned fourth head on frozen Gaussian predictors

## Goal

Determine whether the learned representations of **three frozen, independently
trained Gaussian predictors** contain complementary information that lets a new,
trainable fusion head predict X and Y more accurately than any source alone.

The frozen predictors are (in fixed order) **numeric-only Gaussian reference**, **Pythia-70M's Gaussian head**, and **TinyStories-33M's Gaussian head**. The reference is **not Qwen**. Z is deterministically derived from the fused X and Y; Z is not a separate supervised output.

## Computation

For every existing TRAIN/DEV/TEST presentation, regenerate the frozen head
representations from verified best checkpoints and existing captured activations.
Each frozen head contributes its **128-wide learned second hidden layer** plus
its two normalized location proposals, two Gaussian density means, and two
density variances. These are concatenated into a 402-wide feature vector.
No ground-truth X or Y is included in these fusion features.

The learned fusion MLP uses LayerNorm over the hidden representations, followed
by a 128 × 128 GELU network and a final two-output X/Y correction. Its last
layer is zero-initialized so the initial fusion predictions equal the reference
density means. **The correction has no 0.05-amplitude cap**. Source model
parameters are frozen and never passed to an optimizer.

A second MLP, initialized from identical weights and trained on identical
instances, receives **only reference features** (the Pythia/TinyStories
features are masked to zero). This provides a trained reference-only control:
any gain over that control is stronger evidence of head complementarity than a
gain over the unmodified numerical reference alone.

Both MLPs train only on TRAIN; best checkpoints are selected by weakest-tier,
all-mask joint X/Y accuracy on DEV; TEST is evaluated after best DEV checkpoint
selection. Both predictors save independently keyed X/Y/Z for ensemble audits.

## Run

Use the NanoJev Python environment at your `main_computer` repository root.
No HuggingFace weights are loaded or downloaded. CUDA accelerates inference
and fusion training; CPU runs are supported.

```text
python -m tools.nanojev_hawk_dove_gaussian_fusion \
  --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" \
  --duo-root "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_two_backbone_fresh_v1" \
  --output "C:\Users\subsi\NanoJev\runs\hawk_dove_frozen_three_head_fusion_v1" \
  --device cuda --steps 6000 --probe-every 250 --batch-size 128
```

To continue from step 6,000 to cumulative step 12,000, repeat with `--resume
--steps 12000`. Optimizer, sampling state, RNG, and best DEV checkpoints are
restored. Learning rate stays constant; resuming cannot introduce a new cosine
learning-rate spike. Old sources and run outputs are neither overwritten nor
mutated. If source checkpoint or cache hashes change, continuation fails closed.

## Outputs

- `feature_cache.pt`: immutable source-derived representations for TRAIN/DEV/TEST.
- `best.pt`: DEV-selected three-head fusion weights.
- `best_reference_only.pt`: independently DEV-selected reference-only trained control.
- `latest.pt`: complete restart checkpoint, both models and both optimizers.
- `history.json`: DEV certification measures for fusion and control.
- `predictions_dev.jsonl`, `predictions_test.jsonl`: keyed fusion X/Y/Z predictions.
- `predictions_reference_only_dev.jsonl`, `predictions_reference_only_test.jsonl`: paired control predictions.
- `report.json`: per-tier/per-output DEV and TEST scores for all three source heads, fusion, and trained reference-only control, with gains and provenance.

## Scientific boundary

**This is an exploratory stacking experiment, not a newly blind certification.**
The three pretrained heads already saw the existing TRAIN payoff matrices,
their best checkpoints were selected using the existing DEV matrices, and the
old TEST set has already been inspected during prior experiments. The fourth
head's training on these existing TRAIN examples can also exploit source-head
in-sample behavior. Thus, a gain in this experiment motivates a new independent
meta-training/evaluation campaign with newly generated, matrix-disjoint
examples and fresh TinyStories/Pythia activations. Do not report this reused
TEST as a novel unobserved benchmark. Do not treat any observed gain as a
causal contribution from LM features without comparing to the trained
reference-only control.

The official 5%-relative-error certification uses strict zero handling and
requires **both X and Y** correct for **every mask** within each instance.

## Trained-checkpoint selection versus the step-zero reference

At `step=0`, the fusion MLP's final layer is zero-initialized. The output is
therefore the existing numeric-only Gaussian reference, **not** a trained fourth
head. The trainer reports `status=BASELINE_ONLY_UNTRAINED` and records that
score in its history, but does not allow it to become `best.pt` or
`best_reference_only.pt`.

Only a checkpoint after one or more optimizer updates can be selected as the
best fusion or best reference-only trained model. The first evaluated trained
candidate is accepted even if its DEV accuracy is worse than the reference.
Subsequent trained candidates are selected using the existing DEV gate, while
the reference remains a separate immutable comparison. `best_step` identifies
training steps actually applied to the **selected** fourth head;
`training_updates_completed` is the total optimizer updates performed.
Both are recorded in `report.json`, and each probe records its batch loss and
mean absolute output displacement from the reference. An unimproved model
must be reported as an unimproved **trained** model, not substituted silently
with the step-zero reference.

If `--resume` is used with an earlier v1 fusion output whose `best.pt` was
selected at step zero, the trainer converts the selection at the already
stored `latest.pt` update count, without repeating training. This recovers a
trained candidate from `latest.pt`, **not** the historical best trained
candidate (which v1 did not save if it never beat its step-zero baseline).
Continue training with a larger cumulative `--steps` to select among later
trained states. Existing frozen source checkpoints are never modified.
