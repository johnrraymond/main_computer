# G1-0072 — Frozen complete-object reconstruction bake-off

Status: **research runner implemented; real model run pending**. No certification or production promotion.

## Question

Can the frozen G1 output scores reconstruct **more completely correct object sets** by changing only action-span completeness, competing span selection, and action admission, while preserving the actor recovery established by G1-0071?

This investigates the G1 output interface, not retraining, default-weight changes, or the separate G2 set predictor.

## Immutable controls

Two independently trained G1 checkpoints are compared against **their own, distinct G1-0071 selected decoders**:

| Model | Preselected G1-0071 control | Development exact sets | Actor recall | F1 |
|---|---|---:|---:|---:|
| Oracle-Fabric | `runnerup_joint_gap0p50_bonus0p50` | 26/80 | 99/120 (82.5%) | 96.894% |
| Legacy | `actor_prior_0p25` | 27/80 | 101/120 (84.17%) | 97.092% |

The runner verifies the exact prior checkpoint hashes, frozen dataset hash, LoRA source fingerprint, prior G1-0071 `report.json` hash, and the **previous result metrics** for both development and historical challenge. If any baseline has drifted, it fails without emitting the new report. No prior files are changed.

## Predeclared inference arms

The candidate catalogue contains 18 arms total, comprising the preselected G1-0071 control and ablations that test:

- **Complete-action geometry**: action-span width powers 1.10, 1.25, and 1.40, while all other kinds retain their original weighting.
- **Action-specific score and count effects**: 0.25/0.50 action bonuses, action-count penalties, and a combined width/penalty arm.
- **Further action admission**: null-deficit thresholds 1.25, 1.50, or 2.00, including action-conditional evidence requirements. These deliberately extend *beyond* the already tested G1-0070 joint-admission control (which admitted actions to a deficit of 1.0).
- **Action-type competition**: near-tied runner-up action type selection when the highest non-null candidate is an actor.
- **Joint action mechanisms**: width/admission or bonus/admission combined arms.
- **Fragment merging**: combine two adjacent action predictions only when a corresponding full-span **neural action option** is independently present and passes a fixed, label-blind score comparison.

All neural scores are frozen. Every arm consumes only logits, the valid-span mask, the prior decoder identity, and predeclared constants. No prompt text, expected span, or oracle answer enters the inference function. No random sampling is performed.

## Diagnostic analysis, not inference

After **all predictions are produced**, the runner compares each missed gold object to the three oracle-assisted ceilings:

1. **Eligible candidate**: the correct top non-null class was eligible, but was not selected by inference.
2. **Correct non-null type, not eligible**: a correct type was present at the gold span but rejected by candidate admission.
3. **Wrong top non-null type**: the gold span was supported but the highest-scoring non-null class was another type.
4. **Unsupported/unaligned**: the gold span could not be represented by the aligned candidate evidence under this diagnostic.

For missed actions it additionally counts fragmentation into two or more action spans, shortening, extension, shifts, and the absence of an overlapping action. Each example reports the minimum count of object additions/removals necessary for exact reconstruction (set symmetric difference), which is an **oracle-only error measure**, not a permissible inference-time optimization objective.

Oracle ceiling scores may be much higher than valid inference accuracy; they are not promoted to decoding arms.

## Choice and acceptance

The development cohort alone chooses a research decoder. To replace the G1-0071 control, an arm must:

- Increase exact development object sets (not merely object F1).
- Preserve or improve object F1.
- Lose no more than 0.5 percentage point of precision.
- Preserve or improve recall for **all four required types**: actor, action, quantity, outcome.
- Have no capacity overflow.

The best qualifying arm is chosen by exact-set count, then object F1, then precision, then predeclared order. If nothing qualifies, the **G1-0071 control wins by default**. Previously inspected challenge cases are evaluated *after* the development choice is fixed, as exploratory checks only. There is no independently sealed certification or automatic G2 unlock.

The original G1 acceptance gates remain unchanged: 80% exact development sets, 70% exact challenge sets, and 82% recall of each required kind. Only an independently validated full acceptance may advance the cutover.

## Run from `main_computer\nanojev`

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_complete_object_bakeoff_0072 `
    --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
    --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 `
    --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 `
    --actor-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-actor-rescue-bakeoff-0001 `
    --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-complete-object-bakeoff-0001 `
    --device cuda
```

Two immutable research artifacts are created: `report.json` (per-arm metrics, paired differences, oracle ceilings, complete-object and failure counts, source hashes, gate status) and `cases.jsonl` (per-case action failure attribution and changes for all arms). The output directory must not exist in advance.

## Verification scope

Run `python -m pytest -q tests/test_nanojev_gamer_v1_g1_*.py` to exercise the new experiment and adjacent G1 suites. Local CPU tests cover frozen G1-0071 decoder parity, whole-span preference, action admission beyond the former boundary, action-type runner-up competition, conservative merging, oracle-only attribution, development nonregression selection, immutable reference enforcement, and stop-before-challenge selection. **Local synthetic tests do not replace running the real Qwen checkpoints.**

## Decision after the run

- If complete-set accuracy improves without regressions, retain the decoder as an **experimental inference candidate**, not a production promotion.
- If the eligible ceiling is low despite oracle-correct types on gold spans, plan a trainable admission/interface correction for the deterministic-weight retraining campaign.
- If the frozen logits fail to represent the correct non-null type, revise stage oracle supervision or the next deterministic default-weight architecture.
- If the experiment cannot obtain a meaningful exact-set improvement, stop incremental decoder tuning and use the information-loss map to specify full-system retraining requirements.

**G1 is blocked. G2 remains blocked. The legacy release remains authoritative.**
