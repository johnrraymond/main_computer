# G2-0022 — Competition-aware relationship learning (TRAIN-only, DEV-only evaluation)

## Objective

G2-0021 found a large gap between frozen-score threshold F1 and a perfect
ranking of the *same reachable candidates*. Conditional threshold calibration
transferred poorly, and correct relationships that trailed competing negatives
were especially unreliable. G2-0022 asks whether a **learned local competition
margin**, not another cutoff, improves recognition of multiple correct
source/relation destinations while preserving recall.

This is a controlled, bounded **training experiment**. It does not establish
cross-domain generality, authorize certification, or change G2 production.

## Fixed causal comparison

All three arms use the already frozen G2-0019 `precision_guard` checkpoint as
the starting reference, top-24 label-blind inference candidate generation,
complete candidate groups (8 sampled source/relation groups per TRAIN case),
identical deterministic training batches, a fixed 0.75 Bernoulli threshold,
identical output cap, and predicted G1 endpoints. No gold edge is injected into
inference candidates, and no model is trained on any DEV, challenge, or test
example. Multiple destinations can all be correct, so the loss **never** uses
an exclusive categorical softmax over destinations.

- `frozen_guard_0019`: unchanged parent checkpoint, not trained.
- `matched_finetune`: 320 steps (learning rate `6e-5`) of G2-0019's weighted
  Bernoulli + multi-positive pairwise + precision/recall guard objective,
  initialized from the frozen guard.
- `competition_aware`: exact same initialization, groups, base objective,
  optimizer, and training schedule as `matched_finetune`, **plus** weight 0.75
  of a within-source/relation hard-negative ranking objective. For each valid
  destination, a margin-1.25 softplus compares it to the three highest-scored
  *incorrect TRAIN destinations in its own group*. Positive destinations are
  never used as negatives against each other. Groups without a positive and
  negative receive zero additional ranking loss.

The extra hard-negative objective uses **TRAIN labels for loss only**. Candidate
construction and group sampling remain independent of labels. Negative mining
does not alter inference or G1/rank inputs.

## Lineage and evaluation

The experiment reconstructs and verifies G2-0016 through G2-0021 ancestry,
including full G2-0021 compressed candidate-ledger identity and file hashes.
The previous 448 G2-0021 development examples are replayed with the frozen
parent to detect score/label drift, but cannot select weights or thresholds.

The primary assessment uses **448 newly generated, prompt-disjoint DEV cases**
(320 independent, 128 heldout-topology DEV), with seeds `460025`, `460026`.
The topology set remains `4x4 / 4x2 / 2x4`. The cases are disjoint from the
reconstructed training history and prior development cohorts. No further
parameter, checkpoint, threshold, or policy selection occurs from any of these
results. The TRAIN seed is `460024`.

For all three arms report micro precision, recall, F1, false positives, gold
misses, exact graphs, graph density, prompt family, and topology. Additionally,
measure the number of reachable correct edges whose own source/relation group
contains a higher-scoring incorrect destination. These are *diagnostic* gates,
not automatic promotion or winner selection. A new score advantage that fails
to improve graph-level F1 does not count as success.

### Run

From `C:\Users\subsi\main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-competition-aware-learning --device cuda
```

Default immutable output:

`runtime/reports/gamer-v1/graph-g2-competition-aware-learning-0022/`

Output files:

- `g2_competition_aware_learning_report.json` — fixed protocol, ancestry,
  TRAIN counts, all three arms, conditions and exploratory diagnostic gates.
- `g2_competition_aware_learning_per_case.jsonl.gz` — full fixed-cutoff graph
  results, topology/density, competition counts, and prompt identities.
- `g2_competition_aware_learning_development.safetensors` — **only** the two
  G2-0022 experimental scorer branches, never the original checkpoints.

Existing output directories are refused. To repeat, supply `--output` with a
new directory. Changing `--steps` away from 320 is forbidden by contract.

### Tests

Run from the Main Computer repository root:

```powershell
python -m pytest -q nanojev/tests/test_nanojev_gamer_v1_g2_competition_aware_learning.py
```

These local unit/contract tests can run on CPU. Full training and validation
require the already existing CUDA model/runtime artifacts on the user's machine.

## Locked safety constraints

`sealed_test_used=false`, `challenge_or_test_split_used=false`,
`g2_certified=false`, `g2_production_unlocked=false`,
`production_promotion=false`, `certification_authorized=false`,
`graph_execution_authorized=false`, `memory_writes=false`,
`checkpoint_selection=false`, `model_selection=false`.
