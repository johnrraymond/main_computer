# G2-0041 — Full causal oracle diagnosis of graph completion

## Governing question

G2-0040's repaired TRAIN-fit cohort contains 32 expanded graphs; for 19 of them the
post-admission G2 shortlist contains **every** required gold relationship. All nine
completion-bake-off configurations nevertheless produced zero newly exact graphs.
G2-0041 identifies which decisions prevent reconstruction **even when the
necessary edges are present**. It is a *diagnostic*, not a training or promotion
experiment.

## Inputs and immutable lineage

Run after the G2-0040 opportunity-enriched repair. Verify the exact G2-0040 v2
report, skipped-DEV/no-winner status, parent checkpoint keys/shapes, frozen
policy, and checksums. Reconstruct and verify the identical selected TRAIN,
TRAIN-fit, and naturally distributed TRAIN-audit cohorts. All G1/G2 inference,
contextual occurrence admission, relationship scoring, structured predictions,
and shortlisting are computed without reading the oracle labels. The diagnostic
callback begins before G2-0040 would fit the adapted G2-0033 arm; no training
step executes.

Exactly the existing 64/180 expanded TRAIN, 32/80 expanded TRAIN-fit, and
naturally distributed 80-case TRAIN audit are reused. No DEV gold is used for
diagnosis, training, or policy selection. G2-0031, G2-0032, G2-0033,
G2-0038, G2-0039 and the language backbone remain frozen. G2-0040's pretrained
weights are verified but **not loaded as a diagnostic training model**.

## The fixed-factor intervention matrix

All interventions act on the *same* frozen, label-blind G2 shortlist. They
never change which edges exist or how occurrences were admitted. Supplied
TRAIN gold is used only as a controlled counterfactual after inference.

| Arm | Edge order | Total relationship count | Per-group count |
|---|---|---|---|
| `routed` | Existing routed G2-0033 selection | Existing | Existing |
| `model_structured` | Learned G2-0033 logits | Learned | Learned |
| `oracle_total_count` | Learned | **Exact gold total** | Learned |
| `oracle_group_counts` | Learned | Derived from gold local counts | **Gold per group** |
| `oracle_edge_ranking` | **Gold positive edges ranked above negatives** | Learned | Learned |
| `oracle_total_and_group_counts` | Learned | **Gold** | **Gold** |
| `oracle_ranking_and_total` | **Gold** | **Gold** | Learned |
| `oracle_ranking_and_group_counts` | **Gold** | Derived | **Gold** |
| `oracle_all_factors` | **Gold** | **Gold** | **Gold** |
| `direct_gold_control` | Direct gold set | Gold | Gold |

Oracle total count is enforced as an exact integer *constraint*, not a finite
soft penalty; oracle group counts constrain the dynamic program to the correct
number from each source/relation group. Edge-ranking oracle gives every gold
edge a fixed high score and every non-gold edge a fixed low score. These are
not deployable mechanisms, and no gold-based score is ever evaluated on DEV.

`oracle_all_factors` must exactly reconstruct every shortlist-representable
TRAIN graph. Failure aborts the experiment as a candidate-alignment, decode,
capacity, or implementation defect. The direct-gold control independently
checks basic set alignment.

## Four levels of analysis

1. **Candidate reachability.** Enumerate missing gold endpoints and missing
gold edges. Distinguish admission/no-admission, newly represented anchors,
reachable expanded graphs, and unreachable expanded graphs. Unreachable cases
are never counted as oracle successes.
2. **Ranking and relationship assignment.** Count gold/negative pair inversions
within relation groups for both the original source scorer and G2-0033. Record
up to 24 concrete competing gold/negative pairs per case, predicted edge
scores, and false/missing relationships from the routed baseline.
3. **Cardinality.** Compare G2-0032's predicted total/group counts against
G2-0033's, and compare both to TRAIN gold. Intervene separately on exact total
count and exact source/relation group counts.
4. **Structured optimization.** Compare the existing routed selection with the
frozen G2-0033 Viterbi decoder and the full factor oracles. Both use exact
(non-beam) dynamic programming with the original grouping and group-count
energies. This distinguishes bad learned evidence from a selection/constraint
or candidate-alignment failure.

The report includes results for each of `train`, `train_fit`, and
`natural_train_audit`, with `repairable` and `rescued_repairable` slices.
For each intervention it records: number evaluated, exact graphs, new exact
graphs, previously exact graphs lost, missing edges, and false edges.
The case ledger contains immutable cohort/index/hash identifiers, topology,
count residuals, group-level ranking inversions, examples of wrong competition,
and the edge-level misses/false positives.

**Interpretation:** A single oracle succeeding is evidence that the corrected
factor is *sufficient in that case under the fixed other factors*; it does not
prove that this factor is the unique underlying training defect. When only
combined oracles succeed, both factors or their interaction matter. When no
single or paired intervention succeeds but the full oracle does, the failure
requires interactions across all tested factors. When even the full oracle
fails, stop and investigate the candidate alignment or decoder implementation.

## Acceptance / safety

This run never chooses a winner, recalibrates thresholds, saves trained
weights, updates production, or opens sealed tests. A success means an
**interpretable causal diagnosis**, not G2 certification. The diagnostic must
reproduce the G2-0040 parent TRAIN cohort identifiers and feasibility counts
exactly, verify its lineage, and preserve every loaded parent model. The frozen
G2-0040 callback asserts parent immutability before analysis begins.

No output path may preexist. The report is immutable and accompanied by a
GZIP JSONL case ledger with a SHA256 recorded in the report. If a prerequisite
contract or full-oracle invariant fails, no valid diagnostic report is written.

## Command

From `nanojev`, with the existing `$py` NanoJev interpreter variable:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-causal-oracles --device cuda
```

Outputs are created under
`runtime/reports/gamer-v1/graph-g2-causal-oracle-diagnosis-0041/`:

- `g2_causal_oracle_diagnosis_report.json`
- `g2_causal_oracle_diagnosis_per_case.jsonl.gz`

This command uses the preexisting
`graph-g2-completion-bakeoff-0040-repair` parent by default; use
`--bakeoff40-run` only to specify another immutable parent with the identical
v2 contract. Earlier stages retain their standard canonical defaults.

## Decision after the run

Do not precommit to another training architecture. On the 19 reachable TRAIN-fit
graphs and independent natural TRAIN audit, examine which oracle or smallest
oracle combination recovers exact graphs. This identifies whether to intervene
next on learned edge ranking, total-count estimation, group-count estimation,
structured decoding, or upstream candidate generation. Only then propose a
TRAIN-selected, independent-DEV-tested deployable bake-off.
