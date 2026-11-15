# G2-0043 — Absolute edge scoring and joint graph selection bake-off

## Governing question

G2-0041 found that fixed `+24/-24` TRAIN-label-informed edge scores recover all 19 TRAIN-fit graphs whose full gold edge sets exist in the shortlisted inventory. G2-0042 separated edge ordering and magnitude: among those same 19 graphs, a within-group *ordering-only* oracle recovers 1, a preregistered global *calibration-only* oracle recovers 0, and their combination recovers 5. Pairwise and hard-negative ranking-head training reduces ranking inversions but produces no newly exact graphs. Consequently, a new attempt must train **both score order and usable score margins**; count-head adaptation must be tested as a separate intervention, not assumed necessary.

## Reproducible command

From `main_computer/nanojev` after applying the repository-root-relative patch:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-absolute-score-bakeoff --device cuda
```

Default immutable output: `runtime/reports/gamer-v1/graph-g2-absolute-score-bakeoff-0043/`.

The CLI inherits the parent G2-0040 arguments and accepts `--bakeoff40-run`, `--oracle41-run`, and `--rank42-run`. G2-0040 (repaired), G2-0041, and G2-0042 parent reports/checkpoints/ledgers are verified by hashes, schema, failure disposition, and frozen-policy contracts. Training, fitting, and natural TRAIN-audit rows are reproduced from the fixed parent G2-0040 cohort hashes.

## Fixed three-arm bake-off

All three challenger arms share identical G2-0040 recovered-occurrence candidates, the same source seed 460333, G2-0033 frozen feature representation and decoder policy, and unchanged-graph admission-aware routing. TRAIN-only labels supervise model weights; no oracle intervention can enter predictions.

1. **Absolute edge scores (`absolute`)** — Finetune the G2-0033 edge-readout directly; do not remap its scores to the old logit histogram. Count heads stay frozen. Loss combines edge BCE, within-group hardest-positive/hardest-negative margin, soft group-count consistency, and a hinge comparing the true edge set's energy to the actually decoded TRAIN set.
2. **Group-conditioned margins (`group_margin`)** — The same edge-readout, plus a separately learned affine score temperature/offset conditioned on each group's already available label-blind G2-0033 group features. Count heads remain frozen. The objective puts greater emphasis on matching the number of selected edges per group.
3. **Joint edge and count (`joint_count`)** — Train copies of the edge readout, G2-0033 group-count head and total-graph-count head together, with additional count-class cross entropy and total-count regression. The frozen G2-0032 count model is not changed. A structured hinge directly penalizes an incorrect decoded graph.

All non-head G2-0033 feature layers are frozen; every parent model and checkpoint remains untouched. Each arm gets 240 optimizer steps at learning rate `6e-4`, sampling one *shortlist-representable, occurrence-expanded* TRAIN graph plus one shortlist-representable unchanged control per step. Unreachable graphs are not taught impossible gold completions. A fixed three-point logit-bias grid `{-1,0,+1}` gives nine preregistered alternatives. This is a bounded calibration comparison, not an arbitrary post-hoc parameter search.

### Exact-graph selection policy

On the **same enriched TRAIN-fit 80-case cohort**, choose at most one candidate meeting every gate:

- At least one graph newly exact; zero formerly exact graphs lost.
- Relationship F1 no worse than its routed G2-0040 comparator.
- No increase in false edges or within-group ranking inversions.

The top TRAIN-fit eligible candidate (newly exact graphs first, then F1 lift, then lower false positives, with deterministic tie-breaking) faces **one independent natural-distribution TRAIN audit**. The audit can only veto; failure disables the policy rather than promoting a second-place candidate. No development graph is captured before the audit gate.

If accepted, fresh independent and unfamiliar-topology DEV and replay DEV are evaluated **only once** under the locked candidate; DEV may reject but cannot choose a different configuration. All cohorts must preserve exact graphs, F1, false-positive counts and ranking, and independent DEV must gain at least one exactly correct graph. No production promotion, sealed test, or model deployment occurs.

## Outputs and diagnosis when no arm wins

- `g2_absolute_score_bakeoff_report.json`: parent lineage, nine TRAIN-fit candidate metrics, winner/audit or rejection reasons, training-loss summaries and optional DEV gates.
- `g2_absolute_score_bakeoff_per_case.jsonl.gz`: individual TRAIN-fit cases for every candidate, any tested natural TRAIN audit, and DEV cases only if a winner passes audit; includes exact transitions and missing/false edge counts.
- `g2_absolute_score_bakeoff_development.safetensors`: copies of all three model variants (no parent overwritten).
- `g2_absolute_score_bakeoff_frozen_policies.json`: either the single accepted TRAIN policy or a disabled result with a concrete refusal reason.

The purpose is *full graph correctness*. Better loss or relationship F1 alone cannot produce a winner.
