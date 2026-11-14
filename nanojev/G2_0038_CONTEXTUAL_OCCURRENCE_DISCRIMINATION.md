# G2-0038 — Contextual Occurrence Discrimination with Hard Negatives

## Root cause to test

G2-0036 had only seven positive proposals in the occurrence-admission TRAIN
corpus. G2-0037 corrected the curriculum, generating 115 positive TRAIN
proposals from 96 positive-rich cases. Its strongest fixed TRAIN-fit policy
still achieved only 10 correct admissions versus 28 false admissions, or
26.3% precision. None of the three models passed the predeclared 75%
precision gate; all abstained, and downstream DEV graph predictions remained
identical to the frozen G2 baseline.

G2-0038 tests **representational sufficiency**, not another threshold search:
does a proposal discriminator given frozen contextual token representations
and competing-occurrence representations distinguish correct missed binding
occurrences better than an identically trained handcrafted-feature control?
The hypothesis is not assumed correct; a failed calibration must be reported
without another no-op full development evaluation.

## Intervention

Both acceptor types use the same TRAIN-only, positive-rich, three-way-disjoint
mining scheme, exactly as in G2-0037, but with a fresh deterministic TRAIN pool
and newly excluded G2-0037 DEV cases. The frozen G1 neural proposals, 256
candidate cap, baseline anchor set and maximum four added occurrences per graph
remain unchanged.

`feature_only` trains the existing G2-0036 occurrence MLP from its original
17+kind-count margin, position, overlap, selection and kind features.
`contextual` trains a separate network that also sees seven **label-blind,
fixed-dimensional** views of frozen Qwen token states:

- The two immediate preceding token states, the proposed occurrence's mean
  token state, and the two following token states (five slots, with validity
  masking; same extraction as the frozen G2-0031 occurrence-window contract).
- The difference between the candidate's center state and its nearest already
  selected G1 competitor, and the difference from its nearest proposed
  alternative.
- Eight structural features encoding competitor presence, overlap, nearest
  token distance and matching occurrence kind.

A single fixed-sign projection of each token state onto 48 dimensions uses
seed `460038`; it is not learned, does not use gold annotations, and is shared
between TRAIN, fit, audit and DEV. Frozen Qwen model weights are untouched.
The contextual head has a separate trainable context encoder and a joint
classification head; it is trained with balanced binary cross entropy plus a
0.4-weighted within-case hard-negative pair loss with 0.5 margin. The negative
paired with each TRAIN gold proposal is selected from same-kind, overlapping
or nearby non-gold G1 proposals. Only author-annotated TRAIN labels participate
in training, pair construction or selection of calibration thresholds.

## Training and acceptance boundary

Three seeds (460331, 460332, 460333), each with 320 optimizer steps per view,
are trained independently. Both arms use the same **fixed** threshold grid
(0.55, 0.65, 0.75, 0.85, 0.92, 0.97) and budgets (1, 2, 4). No DEV result
is used to pick a threshold or architecture.

For a policy to be eligible on TRAIN fit: at least three correct added
occurrences, 75% admission precision or greater, and at least one newly
reachable gold relationship. The winner must independently recover at least
two correct occurrences with 75% precision and newly reachable gold
relationships on disjoint TRAIN audit. Failed policies are saved with the
full fixed grid. A model without a safe policy returns the original G1 output.

Every seed reports **separate fit/audit top-one candidate accuracy and
hard-pair accuracy** for feature-only and contextual views. These probe
whether the extra contextual evidence improves ranking, even if no threshold
safely permits admissions.

### Crucial early-stop behavior

If *all three contextual models* fail either TRAIN-fit or TRAIN-audit safety,
G2-0038 writes immutable `TRAIN_CALIBRATION_UNSAFE_NO_DEV_EVALUATION` results.
It includes all six acceptors' checkpoints, their policies and full fit/audit
diagnostics, plus a valid empty case ledger. It does **not** waste time on
frozen-G2 graph evaluation whose result would only repeat the baseline.

If at least one contextual seed passes TRAIN-only safety, the experiment
replays the original 448 G2-0037 development cases (verifying baseline
case-level metrics), then evaluates 448 completely fresh development graphs:
320 independent plus 128 withheld-topology cases. Every occurrence decision
and every frozen G2 relationship decision precedes reading gold for scoring.

For each view and seed, report added true and false occurrences, represented
gold edges, fully representable graphs, graph F1, exact reconstruction,
relationship ranking failures, and withheld-topology F1. Development
acceptance requires at least two additional correct occurrences, at least
75% precision on the **natural DEV distribution**, increased represented gold
edges, and no degradation in F1, exact graphs, ranking failures or
withheld-topology F1 for **both** frozen G2-0032 group and G2-0033 structured
decoders. An improved acceptor's TRAIN performance by itself is not enough.

The fixed G2-0031 occurrence graph, G2-0032 group recovery and G2-0033 set
decoder networks are not trained or modified. G1 and backbone checkpoints are
unchanged, with runtime frozen-state checks even on early exit.

## Lineage, evidence, limitations

The command reconstructs and verifies the G2-0030–0036 ancestor chain, then
verifies G2-0037's saved checkpoint tensor names/shapes, policy, report hashes,
TRAIN pool SHA-256, fresh DEV cohort hashes and original per-case/aggregate
metrics. Saved results use a new schema and are immutable; the sealed test
split, production graph execution and model promotion are prohibited.

This is a TRAIN-only *intervention to test* whether frozen contextual evidence
allows better occurrence discrimination. The frozen proposal catalog still
omits some required gold occurrences; the downstream G2 relationship models
may not handle newly admitted occurrences. Enriching positive TRAIN cases may
not match natural DEV prevalence. Passing the safety gate does not establish
production viability or solve complete-graph reconstruction by itself.

## Execute

Apply the repository-root-relative patch ZIP on top of G2-0037. From the
`nanojev` directory, with the existing `$py` variable:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-contextual-occurrence --device cuda
```

Immutable output directory:

`runtime/reports/gamer-v1/graph-g2-contextual-occurrence-0038/`

Outputs:

- `g2_contextual_occurrence_report.json`
- `g2_contextual_occurrence_per_case.jsonl.gz`
- `g2_contextual_occurrence_development.safetensors`
- `g2_contextual_occurrence_frozen_policies.json`

If none of the three contextual policies passes TRAIN-only safety, the report
records that rejection and no DEV case evaluation is run. A failure is a
conclusive experiment result about the current evidence/model, not permission
to lower the 75% safety gate.
