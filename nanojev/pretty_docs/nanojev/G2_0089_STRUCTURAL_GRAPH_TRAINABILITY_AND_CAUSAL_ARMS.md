# G2-0089 — Gated structural relational-learning bakeoff

## Run

Apply the patch ZIP at the `main_computer` root. From `main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_structural_learning_0089 run --device cuda
```

Optional separate phases after `trainability` has passed:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_structural_learning_0089 bakeoff --device cuda
& $py -u -m tools.nanojev_gamer_v1_g2_structural_learning_0089 predict --device cuda
& $py -u -m tools.nanojev_gamer_v1_g2_structural_learning_0089 score
```

All commands use the preexisting `$py` variable, and do not create additional shell variables. Default output is `../runtime/reports/gamer-v1/oracle-fabric-g2-structural-learning-0089`. Run `run` only when that output directory does not already exist; reports and checkpoints are immutable. `trainability` does **not** begin the full bakeoff unless the 1-case and 8-case exact tests pass. The separate `bakeoff` command is for continuing an already successful trainability run, and `predict`/`score` allow separation of blind inference and one-shot scoring.

## Experimental contract

Parent neural checkpoint: G2-0087; frozen native G1-0082 composite interface. G2-0088's calibration/selection data fixes the **joint graph decoder**, before G2-0089 training starts. Original G2 gates are read from `cur.GATE_REQUIREMENTS['G2']` and are neither weakened nor replaced by edge-level F1.

**Phase 1 — Trainability.** Select exactly eight training-split cases whose gold binding edges are all reachable using G1-predicted objects, and whose gold edge sets can in principle be represented by the frozen 0088 joint decoder. Both requirements use gold *only for training-case selection*, never inference. Train disposable copies of G2-0087 to exactly reconstruct one case and, separately, all eight, under the real gold-blind native head and the frozen decoder. Log full-exact progress. If either test fails at the predeclared budget, stop with `EXACT_GRAPH_TRAINABILITY_BLOCKED`. A failure indicates that more diagnosis is necessary; it does not by itself prove architecture incapacity.

**Phase 2 — Three-arm controlled experiment.** Arm A is the untouched frozen G2-0087 baseline. Arm B trains a G2-only continuation with dense full-pair supervision (retaining G2-0087's within-kind hard negatives), a loss-augmented structural margin on the complete graph, and explicit counterfactual consistency. Encoder, G1, and all later-stage weights remain byte-identical. The decoder and candidate universe are the same in every arm. Arm C, a disposable localized weight edit, is **not performed** unless selective causal rescue has been demonstrated on the **current G2-0087 checkpoint**. The earlier G2-0086 observations concerned the older G2-0083 checkpoint and cannot authorize C. A non-eligible C must report its reason rather than training arbitrary weights. Arm B and eligible C both start from G2-0087, not from each other's results.

**Counterfactual supervision.** During training, use annotation-ID-aligned pair margins for renaming, role swapping and added-distractor prompts to preserve unchanged structural relationships; for action removal, supervise both changed graphs, without imposing a false invariance constraint. New training counterfactual prompts are excluded from fresh evaluation.

**Phase 3 — Arm selection.** Compare arms on the original, already exposed 80 development and 32 challenge cases, using exact graph count as the primary criterion and edge F1 only as a tiebreaker. This selection evidence is *not* independent certification. Record chosen arm, historical current-checkpoint G2 causal ablations, and current-checkpoint G1 retention. Do not call native `certify` from exposed metrics.

**Phase 4 — Sealed prospective inference.** After selection, commit 96 fresh development and 64 fresh challenge answers plus 8 counterfactual pairs. Exclude earlier corpus, G1-0082, G2-0083, G2-0085, G2-0087 and G2-0088 prompts. Run the predictor in a separate process that opens prompts, committed selection, frozen checkpoints, but **never** `answers.SECRET.jsonl`. Hash and lock blind predictions before a second process scores the answers. A second score attempt is rejected.

**Phase 5 — Gate report.** Recompute case-level exact binding sets and edge precision/recall/F1, all-gold-reachable stratification, trained versus frozen parent, zero-hidden and no-binding controls, historical original-gate evaluation, and counterfactual behavior. Passes are developmental evidence only; G2 certification, G3 training, and production promotion remain explicitly disabled, including for a hypothetical perfect prospective score. Same-generator fresh examples are not an independent distribution.

## Files written

- `trainability-selection.json`, `trainability.json`: 1/8-case progress and feasibility evidence.
- `overfit-1/`, `overfit-8/`: disposable proof-of-trainability G2 checkpoints, saved only upon exact success.
- `arm-B/` and, only if the causal gate permits it, `arm-C/`: experimental continuation checkpoints.
- `selection.json`, `historical.json`, `evaluation/`: winner and current-head historical/retention/ablation evidence.
- `seal/commit.json`, `seal/prompts.jsonl`, `seal/answers.SECRET.jsonl`: sealed prospective cohort.
- `blind/lock.json`, `blind/predictions.jsonl`: hash-locked gold-blind inference.
- `scored/report.json`, `scored/cases.jsonl`: one-shot developmental evaluation, no unlock.

## Operational limits

The local patch tests validate loss gradients, native G2 parameter authority, decoder compatibility, checkpoints, gate logic, tamper refusal, and a synthetic gold-blind score lifecycle. They do not substitute for the user's CUDA run with the real Qwen + G1-0082 + G2-0087 artifacts. A failed trainability run is a useful, terminal experimental result, not permission to relax the frozen decoder or change the threshold after seeing answers.
