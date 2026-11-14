# G2-0037 — Targeted TRAIN Curriculum for Selective Occurrence Recovery

## Causal target

G2-0036 trained its conservative occurrence acceptor with **7 positive proposals
among 12,138 training proposals**. All three seeds failed TRAIN admission
calibration and were correctly disabled. Its frozen G1 proposal catalog nevertheless
contained **192 additional gold binding endpoints** across 448 fresh development
cases that the selected G1 output omitted. These are different observations:
there is candidate availability, but the prior TRAIN examples did not expose
adequate missing-but-recoverable occurrences.

G2-0037 changes **the author-annotated TRAIN curriculum**, not G1 generation,
G1 decoder, G2 embeddings, G2 occurrence scorer, G2 completeness heads, or the
G2 structured-set decoder. It retains exactly the G2-0036 acceptor features,
network, optimizer, 320-step training, three seeds, 24-candidate relationship
shortlisting, 4-extra-occurrence maximum, 128-node limit, and TRAIN calibration
gates. The frozen parent weights must match the original saved SHA-256 values.

## Novel TRAIN corpus, not DEV borrowing

The original G2-0036 curriculum generated unfamiliar prompts with familiar
TRAIN action names, which yielded too few misses. G2-0037 introduces **TRAIN-only
synthetic surface names** generated with a separate fixed seed. These names are
excluded from all preexisting TRAIN, DEV, challenge, and test name pools. Their
mix with ordinary TRAIN names varies from 25% to 100%, across encounter and
allocation families and permitted TRAIN topologies only. The generator never
imports DEV vocabulary or re-labels DEV examples as TRAIN. It includes hard
negative cases with available false candidate proposals.

Three disjoint TRAIN candidate pools are generated **before any scoring**:

| Cohort | Maximum screened | Selected cases | Positive-rich cases | Minimum useful cases |
| --- | ---: | ---: | ---: | ---: |
| `train_0037` | 1,700 | 300 | 96 | 12 |
| `train_fit_0037` | 700 | 120 | 36 | 4 |
| `train_audit_0037` | 400 | 60 | 18 | 2 |

A positive-rich row must contain at least one missing gold binding occurrence
that exists in the frozen G1 neural proposal shortlist. “Useful” means adding a
missing endpoint can make a previously unreachable gold binding edge reachable
because its opposite endpoint is already selected. A true gold label is used
**only** for selecting author-annotated TRAIN cases and constructing their
supervision, not for selecting or scoring DEV predictions. The TRAIN miner may
replace an earlier positive-rich row with a useful one from the same TRAIN
partition. Positive-rich quotas are fixed, not adjusted against DEV results.

If a quota cannot be achieved within its capped pool, the experiment **fails
closed with the screened-count diagnostics**, instead of silently training on
another seven positives or weakening admission precision. Mining prints live
counts every 40 screened rows. Distinct provenance, prompt hashes, family
counts, positive totals, actionability and mined dataset hashes are saved.

## Training, calibration, and exact nonleakage contract

The three acceptor seeds use 320 optimizer steps each, on TRAIN-fitted positive
and negative proposals only. Calibration is performed exclusively on the
separate `train_fit_0037` partition, and the chosen policy must pass the
separate `train_audit_0037` partition. No TRAIN/fit/audit prompt may overlap
another or any previous training, DEV or new evaluation prompt. All candidate
features are label-blind frozen G1 proposals, margins and overlap indicators.

The G2-0036 admission policy contract is unchanged: thresholds
`0.55, 0.65, 0.75, 0.85, 0.92, 0.97`, budgets `1, 2, 4`, at least 3 true
new endpoints in fit, fit precision at least 75%, and positive newly reachable
edges. Independent audit must recover at least 2 true endpoints, reach at
least 75% precision and yield newly reachable edges. Failed policies abstain
and reuse the exact original G1 candidate set. G2-0037 additionally saves
**the entire fixed policy grid**, including metrics for rejected policies,
and explicitly records the audit outcome of each TRAIN-eligible policy winner.

These are empirical TRAIN safety gates. Mining intentionally enriches positive
examples, so a TRAIN precision pass **does not itself imply** any particular
precision on fresh development or production distributions. The paired DEV
comparison must establish actual generalization.

## Frozen evaluation and promotion decision

Before training, G2-0037 reconstructs all earlier G2 ancestry, and validates
G2-0036's immutable checkpoint, policy SHA-256, ledger, report, cohort hashes,
row identities, aggregated metrics and development gates. It replays the
original 448 G2-0036 development cases and creates a *different*, disjoint
448-case development cohort (320 independent and 128 withheld-topology cases).
A frozen baseline is recomputed per case and checked against G2-0036's record;
replay drift is an error.

Both G2-0032 group-recovery and G2-0033 structured-set decoders are evaluated
with exactly their frozen weights and existing policies. Every selected graph
is finalized **before the gold graph is inspected** for scoring. Metrics include
new true/false occurrence admissions, newly reachable and representable gold
edges, exact graph accuracy, graph F1, rank failures, and withheld-topology
performance. Positive candidate-recall gain without matching graph accuracy
is not sufficient. Development gates require positive represented-edge gain
and nonregression in graph F1, exact graphs, ranking failures, and unfamiliar
topology for each seed and frozen downstream decoder.

No DEV policy search, test/challenge split, production graph execution,
checkpoint promotion, or G2 certification is allowed. If all selectors abstain,
the result is an informative FAIL with complete TRAIN-grid audit evidence, not
an artificial regression-free PASS.

## Execute

Apply the ZIP at the repository root (the ZIP contains `nanojev/...` paths).
From inside the `nanojev` directory:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-occurrence-curriculum --device cuda
```

Outputs are immutable in:

`runtime/reports/gamer-v1/graph-g2-occurrence-curriculum-0037/`

- `g2_occurrence_curriculum_report.json`
- `g2_occurrence_curriculum_per_case.jsonl.gz`
- `g2_occurrence_curriculum_development.safetensors`
- `g2_occurrence_curriculum_frozen_policies.json`

All artifacts are development-only. The CUDA experiment must run to learn
whether positive-rich TRAIN data can establish safe admissions on genuinely
unfamiliar graphs.
