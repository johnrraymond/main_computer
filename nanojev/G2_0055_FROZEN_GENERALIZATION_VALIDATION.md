# G2-0055 — frozen step-320 generalization evaluation

## Objective

Evaluate the exact-memorized step-320 **set-prediction** checkpoint without
further training, checkpoint selection, calibration, or threshold tuning.
This is an evaluation of transfer, not a new bake-off and not a model promotion.

## Run

From `main_computer/nanojev`, after applying the patch to the repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0055-frozen-generalization --device cuda
```

Output: `main_computer/runtime/reports/gamer-v1/graph-g2-0055-frozen-generalization`

## Evidence and controls

- Exact input: `graph-g2-0055-memorization-diagnosis/g2_0055_memorization_checkpoints.safetensors`, `best.*` tensors, saved at step 320. The parent report, policy, prior G2-0055 artifacts, auxiliary prediction ledger, and every hash are checked before capture.
- The selected checkpoint must have reproduced **8/8** training graphs with zero missing and zero extra edges. `best` and `last` weights must match because the parent run ended at step 320.
- No new optimizer is constructed; all G2 weights have `requires_grad=False`. Inference receives **no gold edge labels**. Its exact parameters are compared against snapshots after inference.
- The G1 and G2-0048 encoder remains frozen. G2-0052's label-blind candidate construction is reused.
- Cohorts: **16 train-fit graphs (8 pairs), 32 existing counterfactual probe graphs (16 pairs), and 32 fresh synthetic counterfactual probe graphs (16 pairs)**. Exactly 80 graphs, excluding the eight memorization training graphs. All prompt and pair IDs must match the regenerated immutable split.
- The train-fit split was previously used for calibration of older approaches; the fresh synthetic probes have been examined in G2-0053–55. **None of these 80 should be presented as a still-sealed independent TEST dataset.**
- Reports and prediction ledgers include exact graphs, exact pairs, missing/extra edges, family/mode breakdown, and full selected/missed/extra candidate indices.
- The shared G2-0051 report writer has an explicit opt-in `interface_inference_only` mode. It rejects positive optimizer steps on an evaluation and writes `new_optimizer_steps: 0`, `scorer_retraining_performed: false`. The default training extension contract is unchanged.
- No production promotion or certification is enabled, regardless of reported evaluation performance.

## Interpretation

This run determines whether the learned set predictor transfers to non-memorization graphs *in the same generated problem domain*. Strong results on these 80 examples would justify designing a new sealed evaluation; weak results would distinguish memorization from generalized relationship learning. It cannot certify unseen-domain performance by itself.
