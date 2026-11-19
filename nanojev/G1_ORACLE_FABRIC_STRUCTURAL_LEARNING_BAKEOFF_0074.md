# G1-0074 — Structural learning competition for complete-object reconstruction

## Decision being tested

G1-0072 achieved **28/80** exact DEV sets for Oracle-Fabric G1 and **31/80**
for legacy G1 using the *frozen* `action_rescue_2p00_cond0p50` decoder.
G1-0073 tested small learned interfaces: none displaced that decoder for
Oracle-Fabric; legacy pairwise competition improved 31/80 to 32/80
retrospectively, without improving its untouched-within-the-experiment audit
exact score. G1-0074 therefore changes the supervised objectives and feature
space rather than introducing another arbitrary admission threshold.

The scientific question is **which, if any, of three distinct interface-only
training objectives can reconstruct more complete nonoverlapping semantic
object sets from the same frozen neural logits, without harming any required
semantic kind or object F1?** This experiment **does not retrain** the G1 head,
Qwen, LoRA, deterministic fabric, or cross-stage couplers.

## Competition

| Arm | What is learned | Supervision and inference |
|---|---|---|
| `g1_0072_frozen_control` | Nothing | Bitwise-equivalent per-model G1-0072 selected decoder and its full case-wise parity with G1-0073 |
| `whole_span_boundary` | Deterministic nonlinear boundary/fragment feature readout | Balanced whole-action versus false-fragment logistic classification plus same-prompt contrasting-span pair loss; global nonoverlap selection at inference; actor/quantity/outcome scores unchanged |
| `joint_type_admission` | Two separate actor/action score corrections | Three-class softmax cross-entropy over **null/actor/action** at the same token span, with frozen neural score as prior; hard null-span examples; global nonoverlap selection at inference |
| `structured_set_maxmargin` | One whole-set scoring correction | Loss-augmented **global** interval Viterbi versus the maximum jointly realizable gold-candidate set; structured-hinge subgradient updates for whole-assignment errors |

All trainable research-interface coefficients start at **zero**, are CPU-trained
in a deterministic order, and are clipped to finite bounded values. The 32
fixed features are computed **only from frozen neural proposals**, with no
random weight initialization or training-derived inference features. They
include null-vs-type scores, span extent and start, anchor status, same-span
alternate type strength, left/right neighbors, two-fragment alternative
strength, and deterministic nonlinear boundary contrasts. Scores for outcomes
and quantities stay unchanged in every new arm. The existing 4,096-candidate
and 128-output bounds remain fail-closed. Only interface coefficients change
in memory; they are written to the *new experiment report*, not checkpoints.

All inference functions reject gold/object-label inputs. Gold labels are used
for supervised fitting, post-prediction evaluation, and a separately marked
**oracle-only upper bound**: the maximum simultaneously nonoverlapping gold
objects present in the candidate lattice. This oracle result uses labels and
must **never** be interpreted as inference performance.

## Fair comparison and sampling

Reuses **exactly the existing G1-0073 fixed family-stratified partition** of
80 DEV examples: 48 fit, 16 selection, 16 within-campaign audit. Its 32-case
historical challenge cohort remains previously inspected. Both frozen G1
checkpoints and the Qwen source adapter are fingerprinted. Each phase's G1-0072
control object counts, kind recall, and exact cases must reproduce the actual
G1-0073 report before the experimental output is accepted. A second complete
DEV parity check applies before output.

The arm is selected **on the 16 selection examples only**, requiring exact-set
and object-F1 nonregression, all four required semantic-kind recall values
nonregressing, no more than 0.5 percentage point precision loss, and positive
improvement in either exact sets or object F1. Otherwise the control is kept.
Legacy G1-0073's selected pairwise score is included in reports as a previous
comparison and is not misrepresented as G1-0072 baseline. After the choice,
results for the audit, challenge, and full 80 DEV (marked **retrospective**) are
reported for every contender. All paired object changes and false admissions
are included. Selection within an already explored dataset is exploratory;
**the audit and challenge are not fresh independent certification**.

Optional `--independent-dataset` supplies a separate canonical G1 graph
curriculum with disjoint DEV/CHALLENGE prompts; its TEST split is not evaluated.
The runner checks exact prompt disjointness, but **cannot establish provenance
or distribution independence**, which require an independently prepared
collection procedure. Until a genuinely independent dataset is available,
no arm may be certified or promoted regardless of its score.

## Run (PowerShell; from `main_computer\\nanojev`)

Apply the small patch ZIP at the `main_computer` repository root, then:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 `
  --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
  --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 `
  --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 `
  --learned-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-learned-interface-bakeoff-0001 `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-structural-learning-bakeoff-0001 `
  --device cuda
```

Optional independent cohort, after its provenance is separately established:

```powershell
  --independent-dataset ..\runtime\reports\gamer-v1\my-new-disjoint-graph-cohort.jsonl
```

If using the optional flag, add it before or after `--device cuda` on the same
PowerShell command; do not paste a standalone flag as a separate command.

Immutable output directory:

- `report.json`: full competitor metrics, per-phase selected arm, previous
  baseline comparisons, labeled oracle lattice ceilings, interface weights,
  individual training ledgers, cohort and checkpoint fingerprints, fail-closed
  safety flags.
- `cases.jsonl`: each arm's recovered/lost correct objects and introduced/
  removed false objects for each case, plus exact-set changes.

## Result interpretation and stopping rule

1. **Positive selection + audit + new independent cohort:** candidate for a
   broader, prospective sealed G1 test, not immediate certification.
2. **Positive selection but no audit/external transfer:** likely selection-fit
   instability; do not promote.
3. **No improvement:** retain G1-0072 for Oracle-Fabric, identify whether
   the fixed logit lattice lacks enough evidence and reconsider G1 supervisory
   objectives or the deterministic learned coupler design. Do not continue
   post-hoc threshold searching indefinitely.
4. **Structural oracle ceiling below target:** representational/candidate
   ceiling requires revising capture, supervision or head architecture. A
   high oracle ceiling alone does not prove ordinary inference can achieve it.

### Hard safety conditions

**No G1 or G2 weights mutated; no Qwen/LoRA changes; no production promotion;
no G2 unlock; no acceptance gate edits; no sealed test accessed.** A result
remains research-only even when it wins the selection cohort. Existing
output paths are never overwritten; input/checkpoint hashes are rechecked.
