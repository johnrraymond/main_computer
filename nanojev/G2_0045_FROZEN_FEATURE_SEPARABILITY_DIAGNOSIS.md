# G2-0045 — Frozen-feature separability versus loss interference

## Objective and provenance

G2-0044 demonstrated that a direct, per-candidate logit vector could fit the selected reachable TRAIN graph in five steps, while the G2-0043 linear-head variants failed to memorize its 57 gold edges in 480 steps. The result did not establish whether the shared frozen representation was linearly separable, whether a nonlinear readout could learn the same evidence, or whether the composite G2-0043 training objective interfered with simpler edge classification.

G2-0045 is a **diagnostic**, not a new G2 candidate or route to promotion. It verifies G2-0040 through G2-0044 lineage and the complete immutable G2-0044 products before capturing any new data. G1, the parent G2-0033 encoder and readouts, the G2-0039 scorer, count heads, decoder and existing production policy remain frozen. The source G2-0040 TRAIN-capture and fixed TRAIN-fit/natural TRAIN-audit cohorts are reused. DEV evaluation and sealed-test access are excluded.

## Predeclared cases

Choose the **simplest reachable admitted TRAIN graph** by increasing number of gold relationships, then shortlist size, then prompt SHA256. Retain the **exact G2-0044 single-graph TRAIN case** as the hard comparator. The two cases must differ. Choose six additional reachable disjoint TRAIN graphs, deterministically by prompt SHA256, to measure transfer. The 80 TRAIN-fit graphs and the 80 natural TRAIN-audit graphs are measured, not trained on or used for model selection. All case selections are made before any optimization or evaluation of the proposed learners. Gold is used only to establish TRAIN reachability, determine the complexity ordering and supervise/measure TRAIN examples.

For **each** selected case, the known +24/-24 gold-score oracle must reproduce the exact graph through the **real G2-0033 dynamic-programming decoder**. A direct independently trainable per-edge BCE control is also run on both selected cases. If the decoder/oracle alignment fails, refuse; if direct BCE fails within its bounded 220-step budget, report that finding rather than claiming representational impossibility.

## Identical frozen features and controlled learners

For each candidate edge, compute exactly the parent G2-0033 edge interaction vector **h** (48 dimensions) and its additive pre-existing score prior. Verify that adding the parent's frozen edge readout reconstructs the captured parent edge logits. Any mismatch refuses the experiment.

Two shared readouts see the **same h and prior** and start at the **same parent edge scores**:

- `linear`: fine-tune a copy of the G2-0033 48→1 linear edge readout; all upstream encoding and group/total count outputs are frozen.
- `nonlinear`: retain the *frozen* parent linear readout and learn an additive 48→96→1 GELU correction, initially zero. Only the nonlinear correction is updated.

Independent logits, which do **not** share edge features across candidate edges, are the capacity-unconstrained *diagnostic control*, not a deployable model.

## Loss-isolation matrix

For each readout and each of the two TRAIN graphs, train independently from the same parent state under six predeclared objectives:

| Objective | Components |
|---|---|
| `bce` | Weighted binary cross-entropy alone |
| `margin` | Per-group hardest-negative softplus margin alone |
| `structured` | Decoder-aligned gold-versus-actual-decoded-graph energy margin alone |
| `bce_margin` | BCE + 1.1× margin |
| `bce_structured` | BCE + 1.5× structured margin |
| `original` | BCE + 1.1× margin + 0.45× soft group-count penalty + 1.5× structured margin |

All objectives use **TRAIN labels exclusively** and the same fixed parent group/graph-count predictions, G2-0033 set decoder and shortlist. The count and cardinality outputs never train. This intentionally isolates **readout capacity** from **loss interference**; it is not the G2-0043 joint count-model bake-off.

Default budgets: 420 optimizer steps on the simple graph and 420 on the hard graph, with exact decoding at step 0, step 1 and every 35 steps. Record every first exact checkpoint, whether exactness persists, per-group ranking inversions, missing and extra edges, worst score separation, gradient norm and individual loss terms. When two successive checkpoints reproduce the complete graph, stop that run. Store diagnostic readout weights for reproducibility, but never enable them as an inference policy.

Also report endpoint performance on six unseen TRAIN examples, TRAIN-fit (80) and natural TRAIN-audit (80), without using these data for optimizer steps, hyperparameter tuning, model selection or promotion. Absence of an exact graph **within the tested bounds** is not evidence of mathematical inseparability.

## Interpretation

- **Direct scores fit, linear BCE fits:** shared frozen features support the demonstrated graph; investigate loss interference if the original composite objective fails.
- **Direct scores fit, linear fails, nonlinear BCE fits:** increased readout capacity resolves the tested case under the tested budget. This is evidence for an expressivity or optimization bottleneck in the linear head, not a proof of general nonlinear adequacy.
- **Direct scores fit, neither shared head fits:** the frozen representation, optimizer or budgets still obstruct learning. The feature-identifiability test in G2-0044 ruled out *exact opposing-label collisions* on its sampled cases, not every possible separability failure.
- **Simple graph fits but hard graph fails:** difficulty/complexity dependence is directly observed. Assess the scores and margins on the 57-gold-edge hard case without treating it as an unseen generalization test.
- **Losses conflict:** compare exact checkpoint and individual gradient/loss trajectories on **the identical frozen features and initial scores**. Do not infer that merely reducing a scalar loss implies correct complete graphs.

The program reports evidence and constrained conclusions. It has no DEV gate, G2 certification, model-selection policy or production update.

## Run

From the `nanojev` working directory after applying the patch at the repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-separability-diagnosis --device cuda
```

To change bounded training budgets, provide `--simple-steps` and/or `--hard-steps` (1–4000) **and a fresh --output** location. The default output is immutable and cannot be overwritten:

`runtime/reports/gamer-v1/graph-g2-separability-diagnosis-0045/`

Artifacts:

- `g2_separability_diagnosis_report.json`
- `g2_separability_diagnosis_per_case.jsonl.gz`
- `g2_separability_diagnosis_development.safetensors` (diagnostic weights only)
- `g2_separability_diagnosis_frozen_policies.json` (explicitly disabled)

## Acceptance

The experiment is complete only after source-lineage SHA checks, exact decoder controls, fresh disjoint TRAIN selection, frozen-feature/parent-logit equality, bounded training, exact graph reports for every arm, unchanged frozen parent state and independently hashed immutable artifacts. Running G2-0045 does not certify or promote G2; it does not expose DEV or sealed TEST labels.
