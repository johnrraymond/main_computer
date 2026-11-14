# G2-0039 — Recovered-Occurrence Relationship Learning

## Objective and fixed facts

G2-0038 demonstrated real contextual G1 occurrence recovery at 79–86% admission
precision and improved edge F1 on independent and withheld-topology DEV graphs.
It did **not** improve exact-graph reconstruction, and relationship ranking
errors increased. That is sufficient evidence to intervene on downstream G2
relationship learning, not evidence that a retrained G2 will automatically
solve complete-graph accuracy.

## Intervention

The new TRAIN-only G2-0039 experiment loads and verifies G2-0038's exact
checkpoint, policies, ledger and report, along with the complete verified
G2-0030–0037 ancestor chain. The G2-0038 contextual acceptors and their
per-seed admission thresholds/budgets remain *frozen*.

For each fresh TRAIN case, G2-0039 obtains the original G1 graph and up to
three accepted/recovered graph variants using the frozen acceptors. Identical
expanded graphs are deduplicated. It creates full canonical **top-24** G2
relationship inventories using the same frozen G1/G2 parent heads and frozen
reference scorer. TRAIN labels select mixed hard-negative groups; no author
graph label participates in a DEV or production inference decision.

For each seed, a **copy of the pretrained G2-0031 occurrence-graph
relationship scorer** is fine-tuned for 240 optimizer steps on a mixture of
original and recovered-occurrence TRAIN graphs, with the original G2-0029
ranking/BCE loss and an additional 0.30-weighted penalty on false relationship
candidates touching a recovered node. This is genuine downstream G2
relationship-model training, not a new occurrence selector and not a change
to the G2-0032 or G2-0033 architectures.

The trained scorer is calibrated against its original frozen counterpart on
separate TRAIN-fit and independent TRAIN-audit graphs. A fixed blend grid
`[0, 0.25, 0.5, 0.75, 1.0]` controls how much learned G2 score replaces the
old G2-0031 score. The calibration accepts a blend only when it increases
aggregate relation F1 without losing exact graphs, increasing false positives
or increasing ranking failures. A separate TRAIN audit can veto it. The
choice is frozen before DEV. If all three fail, G2-0039 records a TRAIN-only
failed experiment, immutable weights and policy evidence, and skips the
expensive no-op development sweep.

## Development acceptance

With at least one TRAIN-safe policy, the experiment first replays the G2-0038
DEV cohort and checks the old contextual and baseline metrics case by case.
It also evaluates a completely fresh 320-graph independent DEV set and
128 withheld-topology graphs, disjoint from all previous cohorts and new
TRAIN data. Each graph gets three comparisons:

1. Original G1 and original G2 relationship scorer (**baseline**).
2. G2-0038 recovered G1 occurrences with the original G2 scorer (**contextual**).
3. The *same* G2-0038 recovered occurrences, now ranked by the fine-tuned
   G2 scorer at its TRAIN-selected blend (**retrained**).

The frozen G2-0032 group recovery and G2-0033 structured-set decoders remain
unaltered, including their original cardinality checkpoints and policies.

Acceptance requires, for **both decoders** on the same seed:

- Strictly more exactly correct graphs than G2-0038 contextual recovery.
- **Zero lost previously exact graphs**, measured case by case (not merely net
  exact-graph count).
- Non-regressing edge F1 and relationship-ranking failures.
- Non-regressing withheld-topology F1 and exact graph count.

The gate is diagnostic only: even if it passes, G2-0039 does not promote to
production or unseal TEST. If it fails, the model is not certified.

## Frozen authority and outputs

G1, frozen Qwen backbone, G2-0031 parent checkpoint, G2-0032 cardinality,
G2-0033 decoder, and G2-0038 acceptors are unchanged. Runtime equality and
requires-grad checks are performed before output and after DEV evaluation.
Only independent copies of G2-0031's occurrence-graph relationship heads are
optimized. Outputs use a new immutable directory and a new schema, with
cryptographic hashes for the new weights, policies and compressed case ledger.

The resulting train-only calibration may still reject all blends. There is
also an inherent ceiling from gold endpoints absent from G1's proposal catalog
and missing G2 inventory edges. Neither is cured merely by retraining a
relationship scorer.

## Execute

Apply the repository-root-relative patch over G2-0038 and run from the
`nanojev` directory:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-recovered-relationships --device cuda
```

Output directory:

`runtime/reports/gamer-v1/graph-g2-recovered-relationships-0039/`

Expected files:

- `g2_recovered_relationship_report.json`
- `g2_recovered_relationship_per_case.jsonl.gz`
- `g2_recovered_relationship_development.safetensors`
- `g2_recovered_relationship_frozen_policies.json`

No rollback archive or manifest is included. No CUDA experiment was run while
building this patch; validation covers local CPU code, regression tests, and
the actual G2-0038 checkpoint tensor layout.
