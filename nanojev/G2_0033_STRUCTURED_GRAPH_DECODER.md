# G2-0033 — Structured Complete-Graph Decoding

## Thesis and objective

G2-0031's occurrence-graph representation materially improved graph F1 but lost
formerly exact graphs. G2-0032's learned group/graph cardinality recovery further
improved F1, yet still failed the exact-graph gate: its additive decoder cannot
remove a false edge already selected by G2-0031. On the fresh G2-0032 448-case
DEV cohort, the original G2-0026 scorer reconstructed 23 exact graphs; the
best G2-0032 variant reconstructed 21.

**G2-0033 tests structured selection of a complete edge set.** It learns edge
scores, group edge-count preferences, and a graph-wide count preference, then
selects a joint set across groups. The chosen set is permitted to add AND remove
candidate edges relative to G2-0031 and G2-0032. It cannot synthesize edges
outside the fixed top-24-per-group candidate inventory, or recover mistakes in
frozen G1 spans.

## Learning and decoding

The only trainable module is `GraphSetEnergy`. For each of three separately
seeded models (`460331`, `460332`, `460333`), the module receives **label-blind**
features derived from G2-0031 occurrence-graph scores, the original G2-0026
reference scores, parent-selected candidate edges, group rank and context, and
graph size. It computes edge features, source/relation-group interactions, and
a graph-wide context. The model emits:

- Candidate-edge logits (which may reorder or reclassify original candidates).
- A categorical predicted edge count, 0–24, for each source/relation group.
- A graph-level predicted total of correct *candidate* edges.

The TRAIN objective includes weighted edge BCE, a soft graph-F1 surrogate,
source/relation-group cardinality cross entropy, graph-count regression, and
an exact-set energy margin comparing the complete labeled candidate graph
against the frozen parent selection and a greedy competing graph. Its gold
labels are read **only** by the TRAIN objective; they are never inputs to the
scorer or the decoder. Count targets describe represented candidate edges, not
unreachable gold edges.

The decoder maximizes the sum of selected edge logits plus learned group-count
potentials, with an optional whole-graph-count potential. A Viterbi dynamic
program considers every possible total cardinality up to the original
`MAX_EDGES` contract and chooses exactly one full graph. It is vectorized for
crowded graphs, uses no graph-specific answer count, and can select 0, 1, or
multiple edges per source/relation group. It is exact within this declared
energy and the existing candidate inventory; it is not proof that the selected
graph matches the authored ground truth.

## Controls, immutability, and data

G2-0032's three recovery strategies for each seed remain unmodified, as do the
G2-0031 occurrence-graph scorers, original G2-0026 scorer, and G2-0030/G2-0031
calibrated controls. All parent model weights and policies are reloaded from
saved immutable outputs, with parent report/ledger/policy/checkpoint hashes,
lineage, case identities and aggregate results checked. On replay of G2-0032's
448 DEV cases, all parent model predictions are required to match the previous
case ledger exactly.

The experiment uses **new**, prompt-disjoint cohorts:

- 300 TRAIN cases fit each of three independent `GraphSetEnergy` heads for
  320 optimizer updates. The entire label-blind candidate inventory, not
  sampled hard negatives alone, participates in the graph-set objective.
- 120 additional TRAIN cases choose among a **predeclared five-point**
  group/global energy-weight grid, with an F1 floor no more than 0.005 below
  the best corresponding G2-0032 recovery policy. If all candidates violate
  the TRAIN floor, the new decoder is disabled, and the frozen G2-0032 control
  remains as a visible fallback; a disabled decoder cannot pass promotion.
- 60 additional TRAIN cases audit the frozen policy, never revise it.
- 320 independent + 128 withheld-topology fresh DEV cases decide the result.
- The previous 448 G2-0032 DEV cases are replayed only for drift detection and
  comparison. They cannot fit weights, calibrate policies, or select a seed.

On the fresh 448-case DEV set, **after decoding**, each ledger record also
captures changed edges, missing/false selected edges, reference/occurrence/set
scores, prior decisions, and any true edge absent from the candidate inventory.
This diagnostic is evaluation-only: it is neither an inference input nor a
training/calibration source. It lets the next analysis distinguish ranking,
graph selection and unreachable-gold failures without re-running CUDA.

No sealed test data, certification, automatic production promotion, or game
graph executables are involved.

## Predeclared gates

**Every one of the three seeds must pass all conditions:**

1. Fresh DEV graph F1 is within **0.005** of the best corresponding G2-0032
   recovery policy and at least **0.02 above the strongest earlier non-
   occurrence control**.
2. Exact-graph rate matches or exceeds **both** the original G2-0026 and the
   best corresponding G2-0032 recovery policy on the same new 448 DEV cases.
3. Ranking failures do not exceed the frozen seed-matched G2-0031 scorer.
4. On new withheld-topology DEV, F1 remains within **0.005** of the strongest
   seed-matched G2-0032 recovery policy, with no ranking deterioration over
   the frozen G2-0031 scorer.
5. The selected structured policy is **enabled**; inherited fallbacks are
   nonqualifying controls.

Promotion, certification and sealed-test execution remain **disabled** even
if these DEV gates pass.

## Run

From `C:\Users\subsi\main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-structured-graph-decoder --device cuda
```

The experiment reads the G2-0030, G2-0031 and G2-0032 run folders (and their
previous ancestry) under `runtime/reports/gamer-v1/`. It does not repeat their
training. Immutable outputs are written to
`runtime/reports/gamer-v1/graph-g2-structured-graph-decoder-0033/`:

- `g2_structured_graph_decoder_report.json`
- `g2_structured_graph_decoder_per_case.jsonl.gz`
- `g2_structured_graph_decoder_frozen_policies.json`
- `g2_structured_graph_decoder_development.safetensors`

A fresh CUDA run is required to determine whether structured decoding clears
the DEV gates. Source tests do not establish any accuracy gain by themselves.
