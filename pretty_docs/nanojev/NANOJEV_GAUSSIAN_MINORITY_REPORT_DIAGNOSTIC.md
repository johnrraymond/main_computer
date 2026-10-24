# NanoJev Gaussian Minority Report diagnostic — read-only

## Purpose and evidence boundary

Compare **three existing, DEV-selected Gaussian predictors**, not three fresh training runs:

1. **Reference** — original 2,000-bank Gaussian trained on explicit oracle-blind numerical bindings; **not Qwen**.
2. **Pythia-70M** — independently trained Gaussian head, explicit numerical bindings **plus** cached frozen Pythia activations.
3. **TinyStories-33M** — independently trained Gaussian head, explicit numerical bindings **plus** cached frozen TinyStories activations.

Qwen is **not** included. No backbone is loaded or fine-tuned, no training checkpoint is updated, and no repository/experiment-state file is changed. The diagnostic reconstructs each predictor from its own `best.pt`, never from `latest.pt`. It regenerates DEV and TEST predictions in memory from each verified checkpoint and activation cache. This avoids joining stale predictions by row position or accidentally using poorer continuation weights.

The input contract requires the original reference checkpoint's matrix-holdout identity to agree exactly with both duo head checkpoint identities, duo `config.json` and `matrix_split_manifest.json` files, and cache source/prompt hashes. A disagreement fails closed; do not suppress an identity check to force a comparison.

## Run

Run from the `main_computer` repository root using the Python interpreter in the NanoJev training environment (the same `$py` used for earlier experiments). The full command below uses CPU and does **not** use CUDA or any Transformers model download:

```text
python -m tools.nanojev_hawk_dove_gaussian_minority_report --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --duo-root "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_two_backbone_fresh_v1" --output "C:\Users\subsi\NanoJev\runs\hawk_dove_minority_report_v1.json" --device cpu --cpu-threads 4 --eval-batch-size 128
```

Use `--device cuda` only if desired; CPU is sufficient and avoids concurrent VRAM pressure. The `--output` path must not already exist: the report is never overwritten. Checkpoints, activation caches, histories, summaries and prediction files are all treated as read-only inputs.

## Measurements and interpretation

The JSON report includes:

- `independent_head_provenance`: each source's best checkpoint, SHA-256 and best step; the two LM heads also record cache SHA-256 and frozen model ID.
- `DEV` and `TEST` / `joint_failure_patterns`: **all eight** success/failure combinations, where bit order is `[reference, pythia, tinystories]`. `1` means both X and Y pass 5%-relative-error; exact oracle zero requires exactly zero prediction. Presentation-level and all-mask-per-instance rates are separate, including tier-specific tables.
- `X_failure_patterns` and `Y_failure_patterns`: same eight patterns for each scalar target, separately.
- `error_symmetry`: X-only/Y-only failures, signed error correlation and error-direction agreement, within and between models, plus exact-zero counts.
- `target_magnitude_and_mask_symmetry`: numerical accuracy by zero/magnitude range and disagreement between the two equivalent prompt-mask representations.
- `example_instances_by_joint_pattern`: up to eight concrete cases for each of the eight patterns, including both masks, exact X/Y oracle labels and each predictor's X/Y/Z output.
- `majority_Z`: majority votes on the three independently derived Z decisions, with **three-way votes reported as abstentions**, not covertly scored as ties. The `Z_rate` in each candidate is Z derived from its own (possibly averaged) X/Y predictions.
- `candidates`: each individual head, all-three mean/median, and all three pairwise means. These include per-tier **all-masks** instance accuracy, scalar X/Y and decision Z accuracy.
- `selected_consensus`: **one rule selected on DEV alone** by max weakest-tier joint accuracy, then mean-tier accuracy, then fixed candidate order, reported on untouched TEST. The report shows TEST accuracy and TEST changes relative to Pythia and the reference. All TEST rule scores are diagnostic comparisons, **not** a second selection procedure.
- Pairwise and three-model **oracle coverage** indicates how often *some* head was right. This is a counterfactual upper bound, not an implementable model selector and **not** accuracy achieved by averaging.

## Cautions

- These are 24 disjoint held-out payoff matrices per DEV and TEST in the standard configuration. Count/mask presentations from the same matrix are correlated; treat small differences as exploratory, not as strong statistical evidence.
- The reference head is a **numeric-only control**. The two independent LM-specific Gaussian heads use the same explicit numerical bindings, so better ensemble accuracy cannot automatically be attributed to a language model's numerical understanding.
- The output measures **error overlap and complementarity**, not just individual rank. A weak model contributes only if it gets some cases right that the stronger models fail, and a deployable consensus rule exploits this without relying on oracle labels at inference.
- No new training run or TEST tuning is performed. To compare an actual **three-backbone** Minority Report later, train a genuine Qwen-independent head and replace the reference source in a separately versioned experiment.
