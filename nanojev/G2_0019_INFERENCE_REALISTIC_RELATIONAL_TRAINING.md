# G2-0019 — Inference-realistic relational discrimination (development only)

## Why this experiment exists

G2-0017 exposed a large gap between G1 endpoint reachability and correct G2 binding selection. G2-0018 showed that the frozen G2-0016 scorer's logits change with competitor composition, but neither shrinking the context nor expanding top-24 to top-48 improved overall F1 substantially. A diagnosis of context sensitivity is not evidence that changing context alone will repair the learned scorer.

G2-0016 training used `train_gold=True`, which force-added TRAIN gold candidates and then selected gold-conditioned hard-negative groups. G2-0016 inference, by contrast, ranks up to 24 eligible destinations per source/relation using **only frozen G1/rank outputs**. G2-0019 isolates the training-distribution mismatch from an additional change to the objective.

## Fixed experiment

All three arms use the **same top-24 inference candidate rule** and the already-selected **0.75 threshold**, with independent Bernoulli predictions (multiple correct destinations allowed). No threshold sweep, inference router, test/challenge use, graph execution or production promotion.

- `baseline_0016`: untouched, hashed G2-0016 `general_75` scorer.
- `matched_context`: copied from that exact frozen baseline, trained for 320 steps on complete top-24 inference groups. No gold-injected inventory or gold-conditioned group selection. The loss matches G2-0016's weighted BCE + multi-positive pairwise ranking.
- `precision_guard`: identical initialization, batches, optimizer, training data, and group selection as `matched_context`, plus fixed-margin penalties for false positives and positive recall at the predeclared 0.75 cutoff.

Both arms use TRAIN labels **only to compute loss**. Training examples consist of the original TRAIN corpus and G2-0016 TRAIN augmentation. Withheld graph topologies `4x4`, `4x2`, `2x4` remain entirely excluded from training. All frozen G1, rank, backbone, and earlier G2 checkpoints remain unchanged. Training is **fine-tuning of two new development scorers**, not production modification.

Every sampled source/relation group contains its **full** rank-chosen top-24 inventory. Deterministic geometry-only group sampling retains 8 groups per case (where available), stratified by competition size, independently of labels. Training samples alternate between `encounter` and `allocation` families. The two trainable models receive the same sampled groups and labels.

## Evaluation and interpretation

Replay the exact frozen G2-0016 baseline on:

- 160 G2-0016 calibration cases (previously exposed).
- 320 G2-0016 independent-development cases (also exposed by G2-0017/0018).
- 128 withheld-topology **development** cases (also exposed by G2-0017/0018).

Compare fixed-0.75 F1, precision, recall, false positives, missed bindings and exact matches; stratify by family, predicted-node density and withheld topology. The fixed reporting gates separately ask whether F1 improves, false positives decrease, and recall remains within 0.02 of the frozen baseline. The diagnostic does **not** choose or promote a checkpoint, and none of these reused splits constitutes an untouched test or certification result. A future clean, newly sealed evaluation would be required for any certification proposal.

The two-arm comparison permits bounded inference about the training-condition change and the incremental precision objective, not proof of universal relational generality.

## Launch from `C:\Users\subsi\main_computer\nanojev`

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-inference-realistic-training --device cuda
```

The command requires all prior frozen G2 runs through `graph-g2-competitor-context-intervention-0018`, with valid reports, per-case ledgers and weights. Lineage checks fail closed on drift. Results are written to the new, immutable run directory:

`runtime/reports/gamer-v1/graph-g2-inference-realistic-training-0019`

To rerun, choose a **new** output directory using `--output`; the script refuses to overwrite any existing run. Training steps are fixed at 320; other values are rejected rather than quietly changing the comparison protocol.

### Tests

```powershell
python -m pytest -q nanojev/tests/test_nanojev_gamer_v1_g2_inference_realistic_training.py nanojev/tests/test_nanojev_gamer_v1_g2_competitor_context_intervention.py nanojev/tests/test_nanojev_gamer_v1_g2_architectural_ceiling_diagnosis.py nanojev/tests/test_nanojev_gamer_v1_g2_general_relational_discrimination.py
```

Run from the Main Computer repository root. Only a CPU-based test run is possible in this patch authoring environment; the full CUDA training/evaluation and runtime artifact verification must run on the user's machine.

## Gates

`g2_certified=false`; `g2_production_unlocked=false`; `production_promotion=false`; `certification_authorized=false`; `graph_execution_authorized=false`; `memory_writes=false`.
