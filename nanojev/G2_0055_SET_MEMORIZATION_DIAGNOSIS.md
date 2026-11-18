# G2-0055 — Set-prediction memorization diagnosis

This is **not** a new bake-off or a production model. It continues the **saved** G2-0055 set-prediction head on the **same eight** fully reachable training graphs selected by the original G2-0055 memorization gate (first remaining training pair in each family/mode cell, after G2-0054's train-fit reservation). The G1 model, G2-0052 endpoint inventory, and G2-0048 occurrence encoder remain frozen. No probe or sealed-test example is used in training, stopping, or model selection.

## Execution

From `nanojev`, after applying the repository-root-relative patch:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0055-memorization-diagnosis --device cuda
```

Defaults: 1,200 additional optimizer steps maximum; diagnostic checkpoints every 80 steps; minibatch of two training graphs. The run stops as soon as it exactly reconstructs all eight. `--steps` and `--checkpoint-interval` are configurable. It does not overwrite the original G2-0055 run; the output is an immutable new directory `runtime/reports/gamer-v1/graph-g2-0055-memorization-diagnosis`.

**Optimizer qualification:** G2-0055 saved model weights but **not** AdamW moment estimates. This experiment starts from the exact saved set-predictor weights with a new optimizer; it is a weight continuation, not a bitwise replay of the original optimizer.

## Diagnostics / gate

* Baseline step 0, periodic checkpoints, and final checkpoint record **eight-graph exactness, false positives, false negatives, absolute cardinality error**, and **correct source, destination, and relation** assignments for matched gold triples.
* The separate compressed `g2_0055_memorization_prediction_diagnostics.jsonl.gz` ledger includes each graph's predicted edges, missing gold edges, spurious predicted edges, nearest competing triples, slot-to-triple Hungarian analysis, null classification, relation-vs-null margins, and source/destination assignments. Gold is consulted **only after** candidate construction or for training and post-prediction diagnostics.
* Best and last *development* model weights are saved in `g2_0055_memorization_checkpoints.safetensors`; never promoted to production.
* The report contains a SHA-256 for the prediction ledger, original parent G2-0055 artifacts, and frozen-model hashes. The experimental status is always research-only.

Interpretation: if the memorization error reaches zero, proceed to an independent holdout decision **separately**; if not, inspect the edge ledger to separate relation-class confusion from endpoint pairing, excess null slots, and cardinality/selection failures. Falling loss alone is **not** evidence that memorization succeeded.

## Validation boundary

The patch's integration tests exercise genuine `TripleSetPredictor` gradients and inference on CPU, verify the real uploaded G2-0055 CUDA checkpoint and hash contract, reproduce the original eight training prompts, and invoke the actual G2-0051 report writer with a separately hash-verified prediction ledger. The full CUDA run on the user's Main Computer remains unverified until executed there.
