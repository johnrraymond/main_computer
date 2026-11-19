# G1-0078 — Reconstruction Reliability / Sealed Failure Atlas

## Objective and evidence boundary

The prospective G1-0077 evaluation already scored 160 disjoint prompts once.
Its frozen cutover model reconstructed 108/160 exact sets, leaving 52 incomplete.
That cohort is now **historical evaluation evidence**. Its answers may be opened
for failure analysis but MUST NOT be used for fit, hyperparameter choice, model
selection, or a second claim of sealed generalization.

This patch **does not claim** that any of the 52 failures have been locally
analyzed at patch-creation time. The user's G1-0077 `seal`, `blind`, and `scored`
folders are required to produce the real case-by-case atlas.

## Two operations

### `diagnose` (no GPU required)

Revalidates the exact original G1-0077 prompt, precommitted answer, scored
report, frozen reference and prediction SHA-256 hashes. Also requires the
one-time `UNSEALED.json` record proving that scoring was completed.

Produces immutable `report.json` and `failures.jsonl`, with one row per
inexact example, including character spans and text. Classifies each missing
correct span by its *observed* relationship to selected predictions:

- Same boundaries, different semantic type;
- Overlapping span of the same type (candidate boundary problem);
- Overlapping span of a different type (candidate competition);
- No overlapping selected span.

These categories are **observed prediction relationships**, *not* proof that
candidate logits were unavailable.

### `bakeoff` (CUDA for the frozen Qwen+G1 heads)

First verifies G1-0077, G1-0076 and all frozen model lineages. Then re-captures
the **52 cutover failures only** for an *oracle-only candidate atlas*: each
missing object is attributed to a raw-logit coordinate deficit, filtering
from unrestricted proposals, exclusion from 256- or 64-candidate budgets, or
failure to select an available 64-budget candidate. The result goes to
`sealed_failure_candidates.jsonl`. **These cases never enter fitting or arm
selection.**

Generates additional, prompt-disjoint examples from the existing author-annotated
corpus generator using its `test` vocabulary. Excludes all original corpus
prompts, every G1-0077 sealed prompt, and duplicates within the new dataset.
Default cohorts: 96 TRAIN, 32 SELECTION, 64 AUDIT. Both encounter and allocation
families are balanced within each split.

Competing interfaces, trained independently for legacy and cutover frozen G1:

| Arm | Training intervention | New-candidate budget |
|---|---|---|
| `frozen_staged_joint_64` | Precisely unchanged G1-0076 research baseline | 64 |
| `continued_structured_64` | Continue structured-set training from G1-0076 learned coefficients | 64 |
| `continued_structured_256` | Continue structured-set training from the same coefficients | 256 |
| `fresh_staged_64` | New deterministic hard-negative stage, then structured-set training | 64 |
| `mined_staged_64` | Counterexample-guided discrimination, then structured-set training | 64 |

These are alternative *learned inference interfaces*. They do **not** retrain
Qwen/LoRA, the G1 span head, deterministic initial fabric, or downstream stages.

No oracle label is used to form candidate proposals, rank candidate budgets, or
perform inference. Only TRAIN annotations enter optimization; only SELECTION
annotations choose the arm. AUDIT annotations are consulted after arm selection.

A winner must not regress selection exact-case reconstruction, object F1,
precision beyond the predefined tolerance, or required actor/action/quantity
recall. If no alternative qualifies, retain frozen G1-0076.

Output is immutable: `report.json`, `cases.jsonl`, and
`sealed_failure_candidates.jsonl`. The numerical learned coefficients are
exported with their SHA-256 digests for every arm and model.

## Commands

Run from `main_computer\\nanojev` with the user's existing `$py` only.

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_reconstruction_reliability_0078 diagnose `
  --seal ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077 `
  --predictions ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077-blind `
  --scored ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077-scored `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-failure-atlas-0078
```

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_reconstruction_reliability_0078 bakeoff `
  --diagnostic-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-failure-atlas-0078 `
  --seal ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077 `
  --predictions ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077-blind `
  --scored ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077-scored `
  --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
  --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 `
  --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 `
  --structural-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-structural-learning-bakeoff-0001 `
  --learned-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-learned-interface-bakeoff-0001 `
  --coverage-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-candidate-coverage-bakeoff-0001 `
  --discrimination-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-discrimination-trainability-bakeoff-0002 `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-reconstruction-reliability-bakeoff-0001 `
  --device cuda
```

## Limits and interpretation

- **G1-0077 is permanently exposed for this research**; do not use it as a
  new sealed test or tune a winner to reproduce its 108/160 result.
- The new AUDIT examples are fresh and disjoint by exact prompt; they are not
  precommitted externally and still share the author generator. A future,
  separately committed blind evaluation is necessary for generalization.
- A high candidate-coordinate ceiling does not itself imply a decoder can
  choose the right complete assignment.
- Neither a small audit reaching 80% nor an improved research metric is
  authority to certify G1. Do not unlock G2 or promote production.
- If no candidate improves selection without regressions, the correct result
  is to retain the frozen G1-0076 interface and report the failed arms.

## G1-0078 frozen-start structured-training repair (November 18, 2026)

The first CUDA bake-off captured 52 previously sealed cutover failures and 96/32/64 new research cases, then failed on the first `continued_structured_64` optimizer step with `RuntimeError: element 0 of tensors does not require grad and does not have a grad_fn`. Root cause: the verified G1-0077 coefficient loader intentionally sets `model.eval().requires_grad_(False)` for sealed inference. G1-0078 passed this frozen instance to G1-0076's structured optimizer. Deepcopy preserved the disabled gradient flags, so the hinge scores could not backpropagate. This was a training-runner defect, not evidence of a G1 failure.

The repaired G1-0078 creates a **distinct trainable parameter copy per competing warm-start learner** while preserving the original verified G1-0076 interface frozen and unchanged for control predictions. The model is copied with `copy.deepcopy`, then `train()` and `requires_grad_(True)` are applied to the copy; it rejects parameter aliasing or non-leaf / nondifferentiable parameters. The 64-budget, 256-budget, and counterexample-mined warm starts use independent copies. The fresh-staged branch retains its own newly initialized learner. The verified original baseline is never passed to an optimizer.

Regression tests load coefficients through the actual sealed loader, verify all frozen parameters remain byte-for-byte identical after fitting, verify every learned competitor has independent trainable tensors, and force a structured hinge violation through `loss.backward()` to verify a genuine gradient step. Historical sealed evidence stays read-only; the candidate diagnostic is not a trainable input. The original failed bake-off did not reach writing its output folder: rerun the same command unless that folder already exists. No head/Qwen/LoRA weights, stage-certification gates, or G2 code are modified.
