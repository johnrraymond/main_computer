# GAMER G1-0076 — Candidate Discrimination and Trainability Bake-off

**Status:** Research experiment runner only; CUDA checkpoint results not yet executed.

## Why G1-0076 exists

G1-0074 established `structured_set_maxmargin` as the best frozen-interface
control (cutover 37/80 exact development sets, legacy 39/80). G1-0075
expanded the candidate lattice to the point that a **gold-guided** nonoverlap
oracle reached 80/80, but its actual inference arms did not improve the control.
The G1-0075 jointly trained learner reached zero exact training examples.

The expanded lattice averages several thousand candidates per prompt, most of
which are false. The expanded oracle is a **coverage upper bound**, not a
successful inference decoder. We must distinguish (a) absent candidate
coordinates, (b) inadequate discriminating features, (c) failed fitting and
(d) an uncompetitive inference selection policy.

## Experiment contract and control

- Both G1 heads, Qwen backbone, LoRA adapter and G1-0074 reference interface
  weights are **frozen**. Only separate experimental interface weights train.
- The exact G1-0075/0074 lineage is required: checkpoints, dataset SHA,
  G1-0073 lattice-source priors, model-specific candidate inventory and all
  reference control phase metrics must match. The full expanded oracle is
  also checked against the G1-0075 report.
- `structured_set_maxmargin` is the immutable control, not the failed G1-0075
  expanded decoder or joint learner.
- All new span proposals come from the existing G1-0075 frozen-logit candidate
  generator. Original candidates and their scores are never deleted.
- Inference functions do not accept oracle targets or gold labels. Gold may
  be read **only** for training losses, reachability diagnostics and reporting.
- The new scorer uses explicit, signed dyadic hidden initialization and an
  exactly-zero output residual at initialization. New candidates receive a
  fixed -3.0 per-token closed-by-default admission penalty; old scores are
  unchanged until interface training.

## Gate 1: Learnability on one hard example

Select the first previously inexact and fully reachable TRAIN example. Fit a
hard-negative discriminator against original and new candidates. It must
produce the **complete exact object set**, not just higher per-object F1. If
hard-negative fitting does not, try a distinct structured margin-training
stage. Report exact score and learning-loss history.

If neither fits, emit `TRAINABILITY_BLOCKED_NO_FULL_COMPETITION` with
`BLOCKED_1_CASE_TRAINABILITY`. Do **not** conduct full-cohort inference
selection or claim learner generalization.

## Gate 2: Learnability on eight hard examples

Repeat the objective using the first eight previously inexact, fully
reachable TRAIN cases. These include the one-case target; the learner must fit
**all eight**. If neither competitor fits, stop with
`BLOCKED_8_CASE_TRAINABILITY` and the diagnostics. Both gates use the *full*
G1-0075 expanded inventory so that an omitted gold candidate cannot be
mistaken for an optimizer defect. They are training memorization tests, not
holdout validation or proof of generalization.

## Conditional full-cohort competition

Only after **both** gates pass, fit on the 48 TRAIN examples. Evaluate and
select only on 16 SELECTION examples. Report another 16 AUDIT examples, the
previously inspected 32 historical CHALLENGE examples, and optionally a new
independently constructed disjoint dataset (`--independent-dataset`). Audit,
challenge and external examples are never fitting inputs and never used in
winner selection.

Competing inference treatments:

| Family | New candidates retained | Scoring behavior |
|---|---:|---|
| Exact control | None beyond G1-0074 lattice | Frozen structured-set max-margin |
| Closed/frozen candidate budget | 64, 256, 1024 | Freeze G1-0074 scores, penalize novel candidates by 3 per token |
| Hard-negative discriminator | 64, 256, 1024 | Nonlinear candidate admission learned from correctly typed whole spans and local score-hard alternatives |
| Staged structured learning | 64, 256, 1024 | Start from the trained discriminator, then loss-augmented global-set optimization |
| Type-balanced frozen budget | 256 | Seven canonical semantic-type quotas plus frozen-score spillover; conservative admission |
| Type-balanced staged budget | 256 | Same quotas, staged learned structured scoring |

A *budget* counts **additional** proposals, not total candidates. Every old
candidate survives. The 256 type-balanced strategy reserves up to 36 new
proposals per semantic kind (7 kinds, 256 // 7) before filling unused slots by frozen-score rank.
These quotas are predeclared and do not depend on class counts from labels.

Constrained budgets can make some gold candidates unreachable. Full-cohort
structured fitting therefore uses the best oracle-reachable TRAIN assignment
for each constrained lattice, and reports the missing gold count. **This is
not relaxed in Gate 1 or Gate 2.**

The report measures, by budget and model, mean proposal count and the exact
oracle-reachable assignment ceiling. This directly separates the benefit of
coverage from the ability to select correct structures.

## Selection and failure semantics

A competitor may replace `structured_set_maxmargin` only when SELECTION
exact sets do not decline, overall F1 does not decline, type-specific recall
for actor/action/quantity/outcome does not decline, precision declines by no
more than 0.005, and either exact sets or F1 strictly improves. A no-gain
outcome retains the control.

If one or both memorization gates fail, the output contains the trainability
ledger and oracle budget ceilings; **no** full model competition, selection,
or promotion occurs. This prevents a failed training implementation from
being misrepresented as evidence against structural learning in principle.

The previously inspected audit/challenge cohorts and this dataset do not
supply independent certification. Even a favorable G1-0076 result is only an
exploratory research result. The existing G1 certification gates remain
unchanged; G2 is blocked.

## Apply and execute

Extract the patch into the `main_computer` repo root, retaining
`nanojev/tools/`, `nanojev/tests/` and this Markdown file. From the
`main_computer\nanojev` directory, run:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076 `
  --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
  --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 `
  --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 `
  --structural-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-structural-learning-bakeoff-0001 `
  --learned-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-learned-interface-bakeoff-0001 `
  --coverage-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-candidate-coverage-bakeoff-0001 `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-discrimination-trainability-bakeoff-0001 `
  --device cuda
```

Optionally append `--independent-dataset <disjoint-jsonl-path>`. Do not add a
training or selection cohort that duplicates evaluation examples.

## Output

- `report.json`: model-by-model one/eight-case trainability gates, training
  losses and structured violations, constrained candidate oracle ceilings,
  performance across the TRAIN / SELECTION / AUDIT / CHALLENGE cohorts, paired
  gains/losses versus G1-0074, explicit status and provenance digests.
  When the full competition runs, portable numeric interface coefficients and
  their SHA-256 fingerprints are preserved **in the report**, not as a binary
  checkpoint blob.
- `cases.jsonl`: individual model/case exactness, recovered/lost correct
  objects, newly introduced/removed false objects for each competitor. If
  trainability is blocked, the file is empty because no full-cohort
  comparison is authorized.

All generated results stay under the `runtime/reports/` output path and are
immutable. No previous reports, G1 source checkpoints, production integration
or G2 state are modified.

## G1-0076 seven-kind type-index repair (2026-11-18)

The initial G1-0076 runner incorrectly sized its discriminator type offsets,
one-hot features, and balanced selection quotas for four kinds. The canonical
G1 `SPAN_KINDS` contract defines **seven**: actor, action, quantity,
condition, outcome, state, other. Live CUDA execution reached index 4 of a
four-element type offset tensor and failed before the trainability gate.
The repaired runner derives the sizes and quotas from `len(SPAN_KINDS)`,
fails closed on an invalid kind index, and tests all seven semantic kinds in
feature construction, inference, candidate budgeting, and fitting. This was
a **runner defect**, not a failed trainability test or a checkpoint regression.
Replace the initial G1-0076 runner and tests before executing the original
command; its lineage and certification gates are unchanged.
