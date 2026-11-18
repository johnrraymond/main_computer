# G2-0054 — Controlled ranking and decoding bake-off

## Objective

G2-0052 made 31/32 counterfactual probe graphs structurally reachable, while
G2-0053 selected 0 exact graphs. G2-0053 retained only 6/32 exact candidate
graphs through top-512 ranking and overgenerated nearly 96 edges per graph.
This experiment isolates scorer discrimination from decoder calibration
without altering frozen G1, G2-0048, or G2-0052.

## Five arms and matched controls

Every learned arm starts from the **same immutable G2-0053 scorer checkpoint**.
Two scorer variants each receive **160 additional optimizer steps** by default,
using the same 80 training graphs and equal batch budgets. The match is between
the continued-ranker arms—not between the new arms and the original 240-step
G2-0053 historical checkpoint (which is reported separately).

| Arm | Scorer | Decoder | Question |
|---|---|---|---|
| A — control/fixed | Continue G2-0053 original loss and mining | Original zero-logit cutoff, top 512, max 96 | Does ordinary additional training solve the issue? |
| B — control/calibrated | Exactly the A checkpoint | Global stopping threshold calibrated on 16 reserved training-fit graphs | Does decoder calibration rescue the original scorer? |
| C — competition/fixed | Continue identical starting weights and optimizer budget with shared-endpoint hard negatives and pairwise ranking | Original zero-logit cutoff | Does the new ranking signal improve top-512 recall? |
| D — competition/calibrated | Exactly the C checkpoint | Same train-fit calibration procedure as B | Are both interventions required? |
| Oracle — coverage ceiling | Perfect knowledge of the gold edges | Exact intersection of gold with label-blind candidate inventory | Can the interface ever express the correct graph? AUDIT ONLY |

The oracle is not a learnable or deployable arm; it is gold-aware only after
the frozen, label-blind inventory is constructed. Positive/negative labels
are used for training in A/C and for calibration in B/D. At inference, A–D
receive only neural features and scores.

## Cohorts, data controls, and acceptance

- **Training:** 80 graphs; 40 counterfactual pairs.
- **Training-fit:** 16 graphs; 8 pairs balanced across allocation/encounter and role-swap/renaming. This is used to select calibration thresholds and the winning research arm. It is **not** an independent final test.
- **Historical counterfactual probes:** 32 graphs, already inspected; diagnostic only.
- **Fresh synthetic probes:** 32 graphs, generated after G2-0050–0052. They remain synthetic, not sealed final evaluation; no probe labels enter training or arm selection.
- **Oracle target:** 31/32 exact existing probes and 15/16 exact pairs, the G2-0052 representability ceiling.
- Report rank recall, graph exactness, missing and extra edges, complete pairs, per-family performance, calibrated thresholds, and the historical G2-0053 metrics.
- Never promote to production or certify G2 from this experiment. A proposed architecture requires an independent, uninspected held-out evaluation before promotion.

## Command

From `C:\Users\subsi\main_computer\nanojev` after applying the repository-root-relative ZIP:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-architectural-bakeoff --device cuda
```

The output is immutable at `runtime/reports/gamer-v1/graph-g2-architectural-bakeoff-0054`.
Research artifact names are `g2_architectural_bakeoff_report.json`,
`g2_architectural_bakeoff_per_case.jsonl.gz`,
`g2_0054_scoring_development.safetensors`, and `g2_0054_frozen_policy.json`.

## Engineering validation

The regression tests exercise the real CLI dispatch, G2-0053 parent artifact
fingerprints, equal-starting-weights training, shared-endpoint negative mining,
train-fit-only calibration, whole-cohort finalization (including both models
and oracle ceiling), and the actual G2-0051 capture/write callback with
0054-specific artifact names. CPU fixture execution verifies integration.
**No CUDA learning result is claimed prior to running the command above.**
