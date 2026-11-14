# G2-0032 — Learned Graph-Completeness Intervention

## Objective

G2-0031 established the new leading architecture: trainable occurrence-specific
span representations with candidate-graph message passing. On the 448-case
G2-0031 DEV cohort, occurrence-graph seed `460331` reached graph F1 **0.6624**,
but only **7/448** exact complete graphs. The frozen G2-0026 scorer achieved
**26/448** exact graphs. Case-level failure inspection found that many of those
lost exact graphs had no false-positive edges and only 1–3 omissions.

**G2-0032 attempts to recover omitted correct candidate edges while preserving
G2-0031's relationship discrimination and most of its F1 improvement.** It
operates on the saved G2-0031 occurrence-graph models; no new backbone or G2
relational encoder training is performed.

## Independently learned completeness mechanisms

Three decode arms share two new *TRAIN-supervised* cardinality heads:

- **Group cardinality:** a neural categorical predictor of the number of
  positive candidate edges in a particular predicted source/relation group.
  Features derive from frozen G2-0031 scores, their reference scores, their
  separation within the group, the parent selection, and graph size.
- **Graph cardinality:** a neural regressor predicting the total number of
  correct edges represented in the predicted inventory using graph-pooled
  model-score statistics. It predicts an unrestricted nonnegative count, rather
  than assuming a game-specific number such as 12.

The decoder arms are: **group recovery** (recover when a group predicts more
true relationships than the original policy selected), **graph recovery**
(recover strongest omitted edges toward a predicted graph-wide count), and
**joint recovery** (require the group signal while respecting the predicted
graph total). All arms **only add** edges to the frozen G2-0031 selected graph;
they cannot remove existing edges, introduce missing candidates, use authored
labels during inference, or restrict a group to one valid destination. The
existing maximum-edge cap is retained.

For each of three G2-0031 occurrence-graph seeds (`460331`–`460333`), the
parent model and its own TRAIN-calibrated G2-0031 selection policy are fixed.
New trainable heads have independent seeded weights. An audit-able policy grid
optimizes **exact graph count** under a **micro F1 floor**: TRAIN-fit graph F1
may not fall more than 0.005 below the frozen occurrence-graph reference. If
no policy improves TRAIN exact graphs within that tolerance, the selected
policy is *disabled*, leaving the frozen parent selection unchanged. This is
not a guarantee of DEV nonregression; DEV evaluates that separately.

## Data / leakage protocol

- 240 **new** TRAIN cases fit cardinality heads; 320 optimizer updates, with
  identical examples and optimization rules for the three independent seeds.
- 120 additional, prompt-disjoint TRAIN cases select recovery policies.
- 60 additional TRAIN cases audit the frozen policies; this audit cannot alter
  model weights, selection policies, or seed/architecture selection.
- 320 new independent DEV cases and 128 new withheld-topology DEV cases are
  read **only after freezing both model weights and recovery policies**.
- The previous 448 G2-0031 DEV cases are replayed to verify **all** parent
  model decisions against their immutable G2-0031 ledger and weights. These
  cases cannot train or select anything.
- The saved G2-0031 checkpoint, policy, ledger, report, parent hashes and
  dataset identities must all match. The full G2-0020–0030 ancestry is
  verified using the existing parent harness before model fitting.

The three independently frozen parent occurrence-graph models, original G2-0026,
G2-0030 recalibrated G2-0026, G2-0031 recalibrated G2-0026, and G2-0030
predicate–argument model are retained as comparison controls. No gold label,
authored graph, or DEV target enters any forward or recovery inference path.

## Predeclared development gates

**Every seed** for at least one recovery arm must simultaneously achieve:

1. **Graph F1** on the 448 new DEV cases no more than **0.5 percentage points
   below its own frozen G2-0031 occurrence-graph model**, while remaining at
   least **2 percentage points above the best earlier non-occurrence control**.
2. **Exact-graph accuracy** at least as high as the strongest earlier control,
   including original G2-0026, while never declining against that seed's
   frozen G2-0031 occurrence-graph baseline.
3. **Ranking failures** no worse than the same seed's frozen G2-0031 model.
   The decoder does not change the scorer's logits; ranking should be identical.
4. On withheld topologies, F1 must remain within **0.5 percentage points**
   of the same seed's frozen occurrence-graph model; ranking cannot deteriorate.

These are *development* gates only. No DEV checkpoint selection, sealed-test
execution, certification, production activation, or automatic promotion occurs.
If group/graph count learning fails to restore exact graphs under these
constraints, the next justified intervention is graph-level training and/or
higher-quality upstream candidate generation—not another unbounded
post-hoc threshold search.

## Run

From `C:\Users\subsi\main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-graph-completeness --device cuda
```

The experiment requires the existing immutable G2-0029, G2-0030 and G2-0031
runs and all earlier verified parents. It **does not rerun G2-0031 training**.

Default output directory:

`runtime/reports/gamer-v1/graph-g2-graph-completeness-0032/`

Files:

- `g2_graph_completeness_report.json`
- `g2_graph_completeness_per_case.jsonl.gz`
- `g2_graph_completeness_development.safetensors`
- `g2_graph_completeness_frozen_policies.json`

## Limits

This is a structured recall intervention over an existing top-24 G2 candidate
inventory, not a new open-domain parser and not a replay of the executable
historical deterministic game solver. A missing candidate cannot be recovered.
No CUDA run has been performed in the patch-building environment; the
predeclared DEV assessment occurs only when the user executes the command.
