# G1-0079 — Quantity-Preserving Structured Learning and Prospective Audit

Research protocol; no G1/G2 certification or production changes.

## Why this experiment exists

On the G1-0078 *selection* cohort, the Oracle-Fabric cutover's
`continued_structured_64` reconstructed 25/32 complete examples but missed
one gold quantity (253/254), whereas the selected `fresh_staged_64`
reconstructed 21/32 and retained 254/254 quantities. The prior rule forbade
semantic-kind recall regression. That prior rejection was correctly applied.
The later 64-case audit also favored the excluded arm, but those results
MUST NOT be used to retroactively select it.

G1-0079 tests whether **preserving the control's quantity spans by an
inference-time constraint** allows gains in actor/action reconstruction without
relaxing the semantic-kind nonregression rule. A protection mechanism cannot
repair quantities already wrong in the frozen control: quantity predictions
are exactly **equal to** the original G1-0078 selected model, not guaranteed
to be correct.

## Competitors, predeclared before the new audit

1. `frozen_0078_selected`: exact G1-0078 selected model as control, independently
   loaded for cutover (`fresh_staged_64`) and legacy (`frozen_staged_joint_64`).
2. `historical_continued_64_unprotected`: published G1-0078 alternative, shown
   **diagnostically on selection only** and never eligible for promotion.
3. `pinned_historical_continued_64`: same G1-0078 continuation coefficients,
   but the control's quantity intervals are pinned before dynamic programming.
4. `pinned_structured_1x`: continue training the G1-0078 selected interface,
   with quantity spans pinned and an ordinary structured max-margin objective.
5. `pinned_quantity_margin_2x`: same training plus quantity-score retention
   distillation from the frozen control with coefficient 0.02.
6. `pinned_quantity_margin_4x`: same training with retention coefficient 0.06.

`1x`, `2x`, and `4x` are **different optimization objectives**: they do not
simply repeat the same loss under different labels. Quantities are pinned in
all three at training and inference. The extra quantity-score retention term
regularizes the otherwise dormant quantity channel to resist drift while the
other semantic object assignments are learned. Gold labels never create
inference-time pins. Pins come from the original model's predictions only.

Each learned model receives an independent trainable copy of the verified
inference-frozen discriminator. No G1 head or Qwen weights are updated.

## Reference and lineage gates

The runner requires:

- The original G1-0077 precommitted seal and its exact generator/checkpoint
  lineage.
- G1-0078 `report.json`, `cases.jsonl`, selected-arm identities, original
  dataset checksum, generator checksum, and paired case hash digest.
- G1-0076 learned parameters and G1-0074/G1-0073/G1-0075 predecessor reports.
- The same two immutable head checkpoints and frozen Qwen adapter.
- All seven canonical semantic span kinds; discriminator coefficient arrays
  must match their exact shape and SHA-256 digest.

The G1-0078 case hashes, G1-0077 prompts, and original curriculum prompts are
excluded from the new prospective generator's new corpus. The cohorts use the
existing author-annotated generator's `test` vocabulary: this is an in-distribution
prospective audit, not evidence of independent-generator transfer.

## Three-stage protocol

### A. Prepare: create and commit the prospective audit **before fitting**

From `main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_quantity_preserving_bakeoff_0079 prepare `
  --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
  --seal-0077 ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077 `
  --reliability-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-reconstruction-reliability-bakeoff-0001 `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-quantity-preserving-0079-seal `
  --seed 790079 --train 96 --selection 32 --audit 128
```

The output includes `train.jsonl`, `selection.jsonl`, `audit.prompts.jsonl`,
`audit.answers.SECRET.jsonl`, and `manifest.json`. SHA-256 commits all files,
especially the prospective answers.

### B. Fit/select using train/selection; predict audit from prompts alone

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_quantity_preserving_bakeoff_0079 fit-predict `
  --seal ..\runtime\reports\gamer-v1\oracle-fabric-g1-quantity-preserving-0079-seal `
  --seal-0077 ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077 `
  --reliability-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-reconstruction-reliability-bakeoff-0001 `
  --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
  --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 `
  --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 `
  --structural-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-structural-learning-bakeoff-0001 `
  --learned-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-learned-interface-bakeoff-0001 `
  --coverage-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-candidate-coverage-bakeoff-0001 `
  --discrimination-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-discrimination-trainability-bakeoff-0002 `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-quantity-preserving-0079-blind `
  --device cuda --epochs 12
```

This operation **never opens** `audit.answers.SECRET.jsonl`, even to hash it.
Only newly generated 96 TRAIN and 32 SELECTION labels enter the optimizer and
winner selection. All 128 audit examples are passed as `object_spans: []` into
the capture pipeline and predicted by the pre-selected model. The report
records trained numeric coefficients, selection results, per-arm recall and
exactness, and locked audit prediction hash. All outputs are immutable.

The original strict selection rule is retained: exact sets must not decrease,
F1 must not decrease, precision may drop by at most 0.005, **every required
semantic kind's recall must not decrease**, and some measured improvement on
SELECTION must exist. The protected runner-up is not auto-promoted from old
audit performance.

### C. Unseal and score the 128 new examples exactly once

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_quantity_preserving_bakeoff_0079 score `
  --seal ..\runtime\reports\gamer-v1\oracle-fabric-g1-quantity-preserving-0079-seal `
  --predictions ..\runtime\reports\gamer-v1\oracle-fabric-g1-quantity-preserving-0079-blind `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-quantity-preserving-0079-scored
```

The scoring step verifies exact prompt, answer and prediction commitments;
per-case quantity parity is mandatory. The output includes `report.json` and
`cases.jsonl`. A one-time `UNSEALED.json` marker prevents rescore with changed
selection. If scored results are used to design another model, this audit cohort
becomes historical evidence and must not be reused as a fresh test.

## Interpretation

An exact-set improvement on the committed audit, together with preserved
quantity predictions and nonregression on other kinds, supports this
interface under the author-generated input distribution. It does **not**
retroactively certify G1, and it does not unlock G2. Report number of exact
examples, semantic-type recall, F1, false admissions, and paired exact-case
gains/losses; avoid describing the quantity guard as accuracy improvement by
itself.

The frozen control remains authoritative for this experiment until selection
and the subsequent prospective audit are complete. No production code, head
weights, certification gates, or adapter files are changed.
