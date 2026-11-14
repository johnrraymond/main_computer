# G2-0018 — Frozen competitor-context intervention

## Question

Does G2-0016 lose relational discrimination because the source/relation competitor
population at inference differs from the gold-conditioned hard-negative groups
encountered during TRAIN?

This laboratory **does not** retrain, certify, promote, execute graphs, select a
new threshold, or open sealed challenge/test splits. It replays the previously
exposed G2-0016 independent DEV (320 cases) and withheld-topology DEV (128).

## Frozen interventions

1. **Baseline top-24:** exact, hash-verified reproduction of `general_75` from
   G2-0017. Any per-case divergence aborts the experiment.
2. **Train-geometry proxy top-24:** for *each* of the same 24 candidate edges,
   retain that target and approximate G2-0016's competitor selection with the
   frozen-margin top six, up to five far, five near, and five deterministic
   remaining candidates. All candidate edges are scored. This is **not** an
   exact TRAIN replay: TRAIN samples groups and forces known positives using
   labels. Neither those labels nor their positive-count distribution are
   available to a legitimate inference intervention.
3. **Expanded top-48, core only:** evaluate the *original* top-24 candidate
   edges using their scores with up to 48 competitors. Candidate identity,
   features, threshold and the evaluation universe are identical. Changes in
   predictions isolate score/context drift (with the normal graph edge cap).
4. **Expanded top-48, full:** score all top-48 candidate edges. Compare with
   core-only to distinguish new candidate coverage from shifts caused by
   altered scores. The new candidates are ranked without gold injection.

The fixed G2-0016 selection threshold is `0.75`; the fixed sampling seed is
`460018`. Differences between the last two arms may also reflect the normal
2048-edge output cap; paired raw-score flips are reported separately to avoid
misattributing cap displacement to the model's attention mechanism.

## Run on the checkpoint-bearing Windows machine

From `C:\Users\subsi\main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-competitor-context-intervention --device cuda
```

Expected immutable output directory:
`runtime/reports/gamer-v1/graph-g2-competitor-context-intervention-0018/`

Outputs:
- `g2_competitor_context_intervention_report.json`: split, family, density,
  topology, candidate gain, predicted edge metrics and per-edge paired flip counts.
- `g2_competitor_context_intervention_per_case.jsonl`: one attribution row per
  exposed DEV case, including baseline/core/proxy/expanded differences.

The run refuses to overwrite an existing output, rejects any parent hash or
per-case replay drift, and fails closed if the frozen model differs from the
recorded checkpoint. A difference between the proxy and baseline is evidence
of *sensitivity to competitor context*, **not** proof that the gold-conditioned
training/inference mismatch alone caused the measured deployment failures.

To decide whether further training is warranted, analyze the paired score
flips and F1 changes by density, family and topology, especially 4×4.
Do not use the DEV results to retroactively certify G2 or pick a production
rule. A training change requires a new TRAIN-only revision and separate
prespecified evaluation.
