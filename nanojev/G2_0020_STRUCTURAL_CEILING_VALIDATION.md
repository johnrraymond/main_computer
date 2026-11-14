# G2-0020 — Frozen structural ceiling and new-cohort validation

## Question

G2-0019's TRAIN-only `precision_guard` arm improved development F1 but did not
produce any completely correct 4x4 graph. Is the residual error mostly caused
by G1 endpoint loss, top-24 candidate truncation, relational discrimination,
context-induced score drift, or graph-wide false-positive accumulation?

This experiment makes no training changes. It is a **frozen diagnostic**, not a
certification run. It does not choose a model, threshold, or density router.

## Frozen protocol

- Reference: the **G2-0019 `precision_guard`** scorer, prelocked from the
  previously exposed G2-0019 development comparison.
- G1, rank head, G2-0016 baseline, G2-0019 training arms, backbone, and
  compatibility set remain unchanged. All checkpoints and historical score
  ledgers are hash-checked before evaluation.
- Source/relation candidate selection uses the frozen G2 rank head, with
  `train_gold=False`. No gold candidate injection, feature changes, or
  topology-specific inference rules are permitted.
- Threshold remains **0.75**; candidate ceilings are **top-24** and
  **top-48**. The graph contract's existing output-edge cap is unchanged.
- Compare **G2-0016 top-24**, **precision-guard top-24**,
  **precision-guard top-48 context restricted to original top-24 edges**, and
  **precision-guard full top-48**. The restricted arm isolates competitor
  context from newly eligible edges.
- First replay the **320 independent-development** and **128 previously
  exposed withheld-topology development** cases. Both G2-0016 and G2-0019
  top-24 scores must match the saved G2-0019 ledger exactly.
- Then apply the unchanged protocol to **320 freshly generated independent DEV**
  cases and **128 fresh topology DEV** cases (64 4x4, 32 4x2, 32 2x4). New seeds
  are frozen to 460020 and 460021. SHA-256 prompt exclusions are checked
  against the reconstructed G2-0001..0019 history and between both new cohorts.

## Attribution fields

Each case records separate counts for:

- G1-missing endpoints; compatibility/self exclusion; rank beyond top-48;
  candidate ranks 25–48; and top-24-reachable gold.
- Gold relationships *in* each inventory but below the fixed score threshold.
- Threshold-positive gold relationships dropped by the existing graph output cap.
- True positives / false positives at both inventory sizes and among newly
  included destinations; losses on the original core when changing context.
- Matched per-candidate score drift and gold/nongold decision flips when
  expanding competitors, independent of any change in candidate membership.
- Per-case exactness and aggregate F1, precision, recall by cohort, family,
  predicted-anchor density, and withheld topology.

Every loss partition is checked against an independent, frozen-rank candidate
oracle. Gold is consulted only in the retrospective attribution and metrics.

## Execution

Apply the patch at the **Main Computer repository root**. From
`C:\Users\subsi\main_computer\nanojev` run:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-structural-ceiling-validation --device cuda
```

The command consumes the already-created G2-0016–0019 artifacts (including
`graph-g2-inference-realistic-training-0019`) and writes a new immutable
`runtime/reports/gamer-v1/graph-g2-structural-ceiling-validation-0020`
directory containing:

- `g2_structural_ceiling_validation_report.json`
- `g2_structural_ceiling_validation_per_case.jsonl`

The report contains lineage hashes, fixed-protocol constants, cohort hashes,
score metrics, score-drift statistics, and additive stage-loss counts.

## Interpretation / nonpromotion

- Compare **top-48 oracle coverage gain** against **new correct predictions**
  and **new false positives**, taking account of score drift on shared candidates.
- If new candidate coverage is high but correct acceptances remain low,
  further increase in top-K alone is not an adequate fix.
- If the core changes substantially when competitors are added, score
  stability under changing context requires investigation before retraining.
- If 4x4 false positives remain numerous, inspect graph-level semantics and
  accumulated error rather than selecting a favorable threshold after the fact.

The *new* cases are disjoint, newly authored **development cases**, not a
sealed challenge or test set, and use the same synthetic generator family.
They are not independently sufficient to establish cross-domain generality,
certification, exact-graph acceptance, or production readiness. Results cannot
be used to tune the G2-0019 reference retrospectively. No G2 production
unlock, graph execution, or memory writes are authorized.

This patch is reversible: it adds this diagnostic module, its tests, and this
note, plus one CLI entrypoint modification. It changes no existing model or
inference implementation.
