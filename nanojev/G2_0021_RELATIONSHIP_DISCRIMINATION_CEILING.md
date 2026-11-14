# G2-0021 — Frozen relationship-discrimination ceiling

## Question

G2-0020 established that expanding the candidate inventory from top-24 to
 top-48 did not materially recover structural correctness. Determine whether
remaining G2 mistakes on *reachable* relationships are primarily (a) misplaced
frozen scores (true edges below false edges) or (b) an unsuitable decision
threshold (true edges already score ahead of false edges). Distinguish both from
G1 endpoint and candidate-inventory losses.

## Locked design

- Frozen G1, G2-0004 rank head, G2-0016 and G2-0019 scorers. Evaluate **only**
  G2-0019's previously prelocked `precision_guard` branch using the same
  top-24, label-blind per-source/per-relation candidate inventory as inference.
- Replay-check the complete G2-0020 report and per-case ledger, parent SHA
  hashes, reconstructed training/cohort identities, and the newly exposed
  G2-0020 top-24 precision-guard metrics. No backward mutation of any parent.
- Use G2-0020's **320 fresh independent DEV + 128 fresh topology DEV** cases as
  the *now-exposed* exploratory calibration split. The fixed 0.75 threshold is
  always retained as the unchanged control.
- Independently generate **320 new independent DEV + 128 new topology DEV**
  cases for *transfer assessment*, seeds `460022` and `460023`. They are
  SHA-256 disjoint from all reconstructed G2-0001..0020 prompts. The topology
  mix is the existing 4x4 / 4x2 / 2x4 pattern. The new prompts are not shown
  to the threshold-calibration function.
- All runs use the original G2 graph edge cap (`MAX_EDGES`), including
  deterministic lexical tie handling. No positive-label injection, scorer
  training, new parameter, inference router, production threshold selection,
  checkpoint selection, certification or execution permission.

## Measurements

On each group of cases, collect *every* frozen top-24 candidate and its logit,
label for retrospective scoring only, relationship type, prompt family,
predicted-G1 density, competitor-group size, and score gap to the strongest
alternative destination. The SHA-256-protected compressed per-case ledger
permits score-distribution inspection without rerunning the model.

1. **Ranking quality:** pairwise ROC AUC (ties count as half), average precision
   (equal-score tie blocks enter together), score quantiles for gold and
   nongold edges, and score-inverted positive/negative pair fraction. These
   use only reachable candidates and *cannot* diagnose G1 recall.
2. **Threshold quality:** exact best achievable global micro-F1 by enumerating
   the frozen distinct score cutoffs and applying the real graph-wide output
   cap. This is an explicitly **hindsight, label-informed ceiling**, not a
   deployable threshold. A separate compressed full precision/recall staircase
   and a 10%-recall-envelope summary make the frontier auditable.
3. **Oracle coverage:** with a hypothetically perfect ranking of the *same*
   candidate inventory, at most `min(gold candidates per case, MAX_EDGES)` true
   relationships can be returned. The gap from the achievable score threshold
   identifies ranking/discrimination losses; the gap from 100% recall includes
   upstream G1 and inventory losses. Neither oracle is used at inference.
4. **Conditions:** separate case-level `encounter`/`allocation` prompt family,
   predicted-G1 density, topology, and candidate-level **relationship type**,
   source/relation competitor count (solo/2–4/5–12/13–24), and score advantage
   or disadvantage versus the strongest competing destination. The
   candidate-only strata report conditional recall and must not be interpreted
   as full-graph recall or an implementable per-candidate policy.
5. **Transfer test:** on exposed G2-0020 DEV only, retrospectively derive
   hypothetical global, prompt-family, density, family×density, and
   relationship-type cutoffs. Sparsely populated strata fall back to the
   global cutoff (20 cases required; relationship-type strata also need
   20 positives and 20 negatives). Assess these *fixed before new cases are
   scored* cutoffs on new G2-0021 DEV. Compare with the unchanged 0.75 control.
   Conditional cutoffs are exploratory diagnostics, **not** modifications to
   the production decoder.

Use `ranking.roc_auc`, `ranking.average_precision`, `rank_vs_oracle_f1_gap`,
`best_bounded_threshold`, `fixed_075`, and `threshold_transfer` together;
no one metric alone establishes an architectural ceiling.

## Execution

From `C:\Users\subsi\main_computer\nanojev` after applying the repository-root
patch:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-relationship-discrimination-ceiling --device cuda
```

The command requires existing immutable G2-0016–0020 runtime reports and
weights, and creates:

`runtime/reports/gamer-v1/graph-g2-relationship-discrimination-ceiling-0021/`

- `g2_relationship_discrimination_ceiling_report.json` — ancestry, score
  separability, condition-level ceilings, policy-transfer comparisons, frozen
  experimental contract, and output hashes.
- `g2_relationship_discrimination_ceiling_per_case.jsonl.gz` — exhaustive
  candidate/score/gold-in-retrospect ledger, recomputable without CUDA.
- `g2_relationship_discrimination_ceiling_frontier.jsonl.gz` — score-tie-atomic
  threshold staircase, with exact graph edge cap, for both combined cohorts
  and their four component splits.

`--ceiling20-run`, `--train19-run`, `--output`, `--root`, and all normal lineage
path overrides are available. Output is immutable; an existing output path
fails rather than being overwritten.

## Interpretation

- High AUROC/AP and a strong transferable threshold advantage over 0.75:
  pursue a separate decoder/calibration study before changing the representation.
- Substantial gold/nongold overlap, poor within-type ranking, and a large
  best-threshold-versus-perfect-ranking F1 gap: the frozen relational scorer
  cannot be repaired by a threshold alone; investigate learned discrimination
  or context representation in a subsequent TRAIN-only experiment.
- Low perfect-ranking reachable recall: address G1 endpoint loss or frozen
  rank-candidate coverage; no relationship cutoff can create absent edges.
- A retrospective benefit that vanishes on the new DEV cohort is not evidence
  for deploying a family/density/relation policy.

The assessment cases remain development data from the same synthetic generator
family. No sealed/challenge/test data are touched. This cannot certify cross-
domain generalization, exact-graph acceptance, production readiness, or graph
memory writes.
