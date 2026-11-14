# G2-0034 — Bounded Causal Graph-Failure Diagnosis

## Objective and scope

G2-0031 established that trainable occurrence-specific representations improve relationship F1. G2-0032 recovered additional relationships but did not reliably restore complete graphs. G2-0033 sometimes improved exact-graph reconstruction, while its chosen policies did **not** demonstrate consistent benefits from joint group/global cardinality. A new decoder is not justified until the pipeline's failure location is identified.

**G2-0034 is a frozen-model causal diagnostic, not another training or policy-selection experiment.** It asks separately whether a graph is unrecoverable from G1/G2's candidate inventory; whether learned relationship scores are adequate when correct edge counts are supplied counterfactually; and whether a decoder chooses the correct edge set from those scores. It cannot establish the causal impact of retraining any component, because none is retrained.

## Inputs, contracts, and lineage

G2-0034 reloads and verifies the unchanged G2-0030 calibrated controls, G2-0031 occurrence encoders, G2-0032 recovery heads, and G2-0033 structured set heads and frozen policies. It verifies G2-0033 report/ledger/policy/checkpoint hashes; regenerated cohort identity and case ordering; saved calibration, split and combined aggregates; promotion-gate outcomes; tensor keys/shapes/finite values; and unchanged parent outputs during live replay. A missing or altered parent is a hard error, not an excuse to accept new results.

The original G1, Qwen backbone, frozen G2 head, candidate inventory, original scorer, and trained G2-0031–0033 heads remain unchanged. New optimizer updates: **zero**. New model parameters: **zero**. New policy search: **none**. Sealed TEST and production remain untouched.

The existing G2-0033 independent and withheld-topology development cohorts (320 + 128) are replayed to verify the parent and diagnose already observed errors. A second, prompt-disjoint set (320 independent + 128 withheld topology) provides an independent check on whether the same failure mechanisms generalize. These new cohorts are not used to choose a model, score cutoff, ablation, checkpoint, or release policy.

## Predeclared, mutually distinguishable diagnostic stages

### 1. Candidate reachability

For every gold relationship, examine the fixed G2 candidate inventory. If the relationship is missing, distinguish:

- **G1 endpoint unavailable**: at least one authored endpoint has no exact predicted G1 anchor `(start, end, kind)`.
- **Endpoints present, edge absent**: both predicted endpoints exist but the complete relationship is absent from the fixed candidate inventory. This may reflect top-k, admissibility or candidate scoring; it is **not automatically attributable to top-k**.
- **Output budget**: even an all-correct candidate set would exceed the existing graph edge budget.

The candidate oracle outputs the gold relationships actually present in the inventory, subject to the existing output budget. Its graph F1 is an **offline upper bound conditional on the fixed inventory**; it is not executable inference. Report representability separately for crowded, small, independent, and unseen-topology cases.

### 2. Relationship ranking with counterfactual cardinalities

Hold the entire candidate inventory and frozen scorer fixed. For each of the three occurrence-graph seeds and its G2-0033 contextual-edge scorer, calculate:

- **Global gold-candidate-count oracle**: select the top-ranked edges when told the total number of *represented* gold candidate edges.
- **Per-source/relation gold-candidate-count oracle**: select the top-ranked edges separately within each predicted source/relation group when told how many correct candidate edges belong to each group.
- **Parent occurrence-score group oracle**: run the same per-group oracle on G2-0031 occurrence logits to separate old and newly learned ranking.

These tests consume gold labels and cardinalities **only after scoring** and are never attached to inference or used for calibration. Failure of a per-group oracle on a fully representable graph demonstrates insufficient within-group ordering at the supplied scores; successful oracle reconstruction paired with an incorrect actual prediction demonstrates a remaining decoder-selection failure. Their performance must not be presented as achievable production accuracy.

### 3. Decoder ablations on identical contextual-edge logits

Hold the trained G2-0033 edge logits, predicted group-count scores, graph-count prediction, and candidate graph fixed. Evaluate **four predeclared**, uncalibrated group/global potential settings:

| Diagnostic condition | Group weight | Global weight |
|---|---:|---:|
| `edge_only` | 0.0 | 0.0 |
| `group_only` | 0.7 | 0.0 |
| `global_only` | 0.0 | 0.30 |
| `joint` | 0.7 | 0.30 |

Every setting uses the same exact dynamic-programming graph decoder. The zero-weight condition isolates its contextual-edge energy from learned count potentials; other conditions isolate group, graph, and interaction effects. These comparisons are **diagnostic contrasts, not DEV-selected candidate policies**. The actual frozen G2-0033 policy remains a separate control, including its disabled/fallback seed 460331; disabled policies are never silently treated as new-model successes.

All G2-0026, G2-0030, G2-0031, G2-0032 and frozen G2-0033 controls are reported alongside the ablations and oracle ceilings, on the same cases. Measures include micro graph F1, precision, recall, false positives, missing edges, relationship ranking failures, exact-graph counts, and conditional exact-graph reconstruction on fully representable graphs.

## Failure attribution and error evidence

For each seed, assign an **exclusive primary failure location** in this order:

1. Output budget insufficient.
2. Missing gold candidate in fixed inventory.
3. Fully representable, but the G2-0033 scorer fails to produce the exact set even with oracle per-group gold counts.
4. Fully representable and correctly ranked with gold group counts, but the actual frozen G2-0033 decoder selects the wrong set.
5. Exact graph reconstructed.

This ordered classification is diagnostic rather than an assertion that upstream failures are the *only* defects in a graph: a graph can have simultaneous candidate, ranking and decoder errors. Report the counts and conditional exact rates so upstream failures are not miscounted as decoder failures. Record up to twelve highest-scoring omitted gold candidate edges per seed, their group ranks, and their strongest false competitors with scores. Do not store raw prompt text or model token states.

## Decision rule

G2-0034 **does not choose or promote** a G2-0035 intervention. Compare the predeclared three-stage failure counts across the G2-0033 replay and the new G2-0034 independent and withheld-topology cases. Then select the next *engineering target*, not a tuned policy:

- If missing endpoints dominate, G1 span/occurrence recall must be addressed.
- If endpoints exist but required edges are excluded, address G2 candidate construction and inventory admissibility.
- If the candidate inventory is sufficient but group-count oracles fail, improve occurrence-specific relationship discrimination.
- If group-count oracles reconstruct the graph but the decoder does not, address complete-set selection (including its learned count potentials).

The report provides explicit evidence for each category. No target is automatically selected by code, and no DEV result is promoted to a production decision.

## Run and immutable outputs

From the `nanojev` directory:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-causal-graph-diagnosis --device cuda
```

No training or calibration repeats; frozen backbone inference is needed to recapture and verify the **full** candidate inventory and learned scores, which G2-0033 did not persist in its ledger. The G2-0033 run's saved output must remain available at its existing location.

New immutable folder: `runtime/reports/gamer-v1/graph-g2-causal-graph-diagnosis-0034/`.

- `g2_causal_graph_diagnosis_report.json`: parent hashes, counterfactual/ablation metrics, split and cohort failure attribution, fixed protocol and limitations.
- `g2_causal_graph_diagnosis_per_case.jsonl.gz`: original frozen controls, oracle/ablation metrics, reachability causes and bounded error evidence. Deterministic gzip, no source prompts.

**This is a diagnosis, not certification.** A clean CPU unit test or successful DEV experiment is not proof of performance on sealed TEST or live tasks.
