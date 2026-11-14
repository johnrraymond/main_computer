# G2-0035 — Candidate-Coverage Intervention

## Objective

G2-0034 established that missing G1 occurrence endpoints and incomplete
G2 destination inventories impose separate hard ceilings on exact graph
reconstruction. G2-0035 intervenes on those two availability mechanisms,
**without retraining or changing the original G1, G2-0031, G2-0032, or
G2-0033 checkpoints or the existing selection policies**. This is a bounded
inference experiment, not G1 production promotion.

## Four predeclared, label-blind arms

| Arm | G1 occurrence proposals | Destination candidates per source/relation |
| --- | --- | --- |
| `baseline` | Frozen G1-0030 selection | Top 24 |
| `endpoint_recovery` | Frozen selection plus at most 16 extra scored neural spans | Top 24 |
| `relationship_expansion` | Frozen G1-0030 selection | Top 48 |
| `joint_coverage` | Frozen selection plus at most 16 extra scored neural spans | Top 48 |

Extra G1 spans must come from the **actual frozen G1 neural proposal
catalog** computed from the same prompt and same checkpoint (confidence
threshold 0.5), with positive neural margin and a valid token-to-character
mapping. The existing G1 spans remain present; alternatives may overlap.
Proposal selection is deterministic, by margin and span width, with stable
tie-breaking. No template parsing, lexical rules, game-family rules,
gold spans, or author graph objects are allowed as inference input.

Top-48 expansion uses the original source/relation constraints and frozen
pair ranking. **G2-0023 also has a strict top-24 competition-context limit.**
The wide-inventory scoring adapter therefore evaluates original ranks 1–24
together, preserving their legacy reference logits exactly. Additional ranks
25–48 are scored in deterministic windows, each with up to 12 tail candidates
and the 12 highest-ranked original candidates as competitors. Every frozen
G2-0023 call sees at most 24 candidates per group. The adapter does not
retrain or modify the scorer, and these bounded-context tail scores **must not
be interpreted as full 48-way contextual competition**.

The G2-0033 decoder accepts **at most 24** candidates per
source/relation group: score all 48 with the frozen occurrence-graph scorer,
then create a label-blind **24-candidate shortlist** by consensus ranking
across its three frozen seeds. Both *pre-shortlist* recovery and *post-shortlist*
recoverability are recorded. Neither the frozen cardinality head nor its
25 count classes is modified. No gold-forced proposals or relaxation of
the graph output limit is permitted. A longer candidate inventory or its learned shortlist can still displace some
original top-24 edges when additional G1 nodes compete; the report records
new *and displaced* candidates, including their gold status in the
post-inference diagnostic.

The underlying inference controls remain the frozen G2-0031 occurrence
scorer, frozen G2-0032 group-completeness recovery policies, and frozen
G2-0033 structured-set policies, for the same three seeds (460331–460333).

## Causal comparisons

The report separates:

1. G1 endpoint recovery: changes in exact-span-aligned gold endpoints.
2. Relationship proposal recall: gold edges available between existing
   G1 endpoints but omitted by the original source/relation top 24.
3. Interactions: improvements and displacements when both interventions
   operate together.
4. Downstream outcomes: F1, precision, recall, exact-graph count,
   relationship ranking failures, and performance on unfamiliar graph
   topologies for *both* preselected downstream decoding controls.

The candidate-inventory oracle is a diagnostic ceiling, **not** a
selection policy. All four arms execute their frozen inference before
gold is consulted for diagnostic metrics.

## Data and acceptance

Reconstruct, cryptographically validate, and case-identity-check the
full G2-0034 lineage. Replay the two G2-0034 cohorts (320 independent
+ 128 topology cases), confirming the original G2-0033 per-case
predictions exactly. Generate disjoint new G2-0035 development cases:
320 independent and 128 withheld-topology cases. Seed: 460535.

For a candidate-coverage arm to pass the *diagnostic* gate, it must
increase represented gold edges and fully representable graphs and avoid
loss in fresh F1, exact graphs, ranking failure rate, withheld-topology
F1 and withheld-topology exact graphs **on both the G2-0032 group and
G2-0033 structured frozen decoders in all three seeds**. The gate does
not select a deployable policy. There is no training, dev label tuning,
sealed test use, production promotion, or model certification in G2-0035.

## Execution

From `nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-candidate-coverage --device cuda
```

Immutable outputs:

`runtime/reports/gamer-v1/graph-g2-candidate-coverage-0035/`

- `g2_candidate_coverage_report.json`
- `g2_candidate_coverage_per_case.jsonl.gz`

No new weight file or calibration policy is emitted. Inspect the
G2-0035 result before deciding whether actual TRAIN-only G1 recovery
learning, wider relationship proposal learning, or the G2 discrimination
learner should be the next experiment.
