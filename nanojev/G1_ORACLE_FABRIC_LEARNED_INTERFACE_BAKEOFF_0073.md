# G1-0073 — Competing Learned Admission and Whole-Object Interfaces

## Objective

The G1-0072 frozen decoder `action_rescue_2p00_cond0p50` reaches **28/80**
exact development sets for Oracle-Fabric G1, and **31/80** for legacy G1.
It leaves action candidates suppressed by null admission or conflicting spans.
This experiment tests whether three *different supervised learning objectives*
can extract better complete objects from **unchanged frozen G1 logits**.

This is not a retraining of Qwen, a G1 head, the deterministic numerical fabric,
or the G1–G7 couplers. Only small research-only interface weight vectors are
fitted. No checkpoint, repository configuration, gate, promotion, or G2 state
is modified by the runner. Input integrity and 0072 baseline parity are
mandatory.

## Competing hypotheses

| Arm | Distinct objective | Selection mechanism |
|---|---|---|
| `g1_0072_frozen_control` | No learning, exact G1-0072 selected inference | 0072 global interval DP |
| `pointwise_admission` | Balanced positive/negative logistic training on typed spans | Learned linear candidate correction + interval DP |
| `pairwise_competition` | Soft ranking against overlapping false span competitors | Learned linear ranking correction + interval DP |
| `structured_interval` | Loss-augmented, structured perceptron on complete non-overlapping interval assignments | Structured learned correction + interval DP |
| `pointwise_pairwise_ensemble` | Deterministic equal-weight combination of two independently fitted arms; no extra training | Interval DP |

All coefficients **start at zero** (an unchanged, identity-residual starting
interface) and are trained in deterministic fixed order without random weight
initialization. The same predeclared feature set is used across learners.
Features encode frozen null-versus-kind margin, conditional class confidence,
span extent and position, semantic type, baseline-eligibility indicator,
pre-existing 0072 score, and whether the class is top-ranked. The labels for
supervision identify typed full-span correctness; **labels are never supplied
in inference**.

New actor/action candidates must pass label-blind predeclared neural constraints;
all G1-0072 candidates and the exact selected control remain available. A
4,096-candidate bounded lattice uses deterministic neural ranking and a
fail-closed overflow on baseline candidates. Global non-overlapping interval DP
enforces the same maximum 128 predicted semantic objects.

## No hidden selection leakage

The prior 80 development cases have already been examined by earlier research.
For this bake-off alone, they are **hash-sorted and stratified by family** into:

- 48 for supervised interface fitting (24 encounter, 24 allocation)
- 16 for research-arm selection (8 encounter, 8 allocation)
- 16 for an audit **not used in fitting or selection** (8 encounter, 8 allocation)

The previously exposed 32 historical challenge cases are evaluated **after
selection**, never used for fitting or choosing arms. All full-80-DEV metrics
are **retrospective** because they include fitting and selection cases.
Neither this audit nor the historical challenge is independent certification.

Optional `--independent-dataset` accepts an additional canonical graph dataset;
only its DEV and CHALLENGE rows are assessed, with exact-prompt disjointness
verified against the original DEV and CHALLENGE. The user must independently
establish collection/generation provenance and distribution independence. Its
TEST split is never accessed. This optional input is not necessary to run the
core comparison.

## Execute

Apply ZIP at the `main_computer` repository root, then:

```powershell
cd nanojev
& $py -u -m tools.nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 `
  --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
  --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 `
  --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 `
  --complete-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-complete-object-bakeoff-0001 `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-learned-interface-bakeoff-0001 `
  --device cuda
```

**Output:** immutable `report.json` and `cases.jsonl`. The report contains all
arm weights, training/selection/audit/challenge metrics, paired object and
exact-case changes, checkpoint/source fingerprints, and flags recording that
G1-head weights and release gates were untouched. It chooses the 0072 control
unless an alternative raises exact-set accuracy or object F1 on the **selection
subset**, without reducing exact sets, F1, actor/action/quantity/outcome recall,
or precision beyond its predeclared tolerance. The winner is research-only.

## Interpretation and limits

The experiment compares three distinct fitted optimization objectives and one
unfitted ensemble, rather than assuming any one architecture will succeed.
The model-specific 0072 controls must reproduce **all** aggregate DEV and
historical CHALLENGE metrics exactly before the result is accepted. The 80%-DEV
and 70%-challenge exact-set thresholds remain architectural acceptance goals,
not retrospective experiment certification. G2 stays blocked; no production
promotion is authorized even if a learner wins this experimental bake-off.

If the learned interface improves the disjoint audit and genuinely new external
cases, the next step is to specify a reproducible G1 interface/coupler training
contract and conduct a prospective, sealed evaluation. If it fails, retain the
0072 control and revise G1 learning/supervision rather than indefinitely tuning
post hoc decoding thresholds.
