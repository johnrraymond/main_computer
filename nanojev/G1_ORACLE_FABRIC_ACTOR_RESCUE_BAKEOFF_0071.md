# GAMER G1-0071: frozen actor rescue versus complete-object accuracy

## Objective and prior evidence

The Oracle-Fabric G1 cutover remains blocked. G1-0070 selected
`joint_actor_action_1p0` for *both* the frozen Oracle-Fabric and frozen legacy G1.
On the 80-case development cohort, Oracle-Fabric scored 26/80 complete sets,
96.849% object F1, and 98/120 actors (81.667% recall). The G1 development
actor requirement is at least 82%, requiring 99/120 actors. The earlier
`joint_runnerup_actor_prior_0p75` recovered 99/120 actors but reduced exact
sets to 23/80. That is a tradeoff to investigate, **not** an established fix.
The remaining complete-object requirement is at least 80% (64/80), far above
any demonstrated inference result. Passing actor recall alone is insufficient.

## Experimental contract

- Never train, write model weights, modify checkpoints, promote, or advance G2.
- Use identical frozen Qwen capture and immutable legacy/cutover G1 checkpoints.
- Compare *every* arm to the **actual selected** 0070 joint decoder, not the
  older `whole_linear` baseline.
- Pin 0070's `report.json` SHA-256, dataset SHA-256, both checkpoint SHA-256
  values and source adapter; enforce both baseline/earlier runner-up ablation
  metrics for *both* cohorts and both model versions.
- The model decoder accepts `span_logits` and `span_valid` only. It has no
  access to gold, prompt text, expected objects or oracles.
- Read gold labels only after all inference predictions are completed.
- Select a research rule from the development cohort alone. The challenge
  cohort has been inspected before; it is exploratory corroboration only.
  No sealed test is accessed and no new certification is authorized.
- Fail closed if a reference changes, output already exists, arm overflows,
  or inference parity with the earlier selected decoder drifts.

## Predeclared inference arms

| Family | Arms | Question |
|---|---|---|
| Controls | `joint_actor_action_1p0`, `joint_runnerup_actor_prior_0p75` | Exact parity with prior selected and prior runner-up ablation |
| Existing-actor prior | `actor_prior_0p10`, `actor_prior_0p25`, `actor_prior_0p50` | Can already eligible actors win contested spans without retyping actions? |
| Joint near-tie alternatives | `runnerup_joint_gap0p25_bonus0p25`, `runnerup_joint_gap0p50_bonus0p50`, `runnerup_joint_gap0p75_bonus0p75` | Can actor runner-up candidates compete under bounded non-null logit differences? |
| Guarded retyping | `guarded_swap_gap0p25`, `guarded_swap_gap0p50`, `guarded_swap_gap0p75` | Can one action be retyped per example without changing its span? |
| Nonoverlapping insertion | `gap_insert_actor_0p50`, `gap_insert_actor_1p00`, `gap_insert_actor_two_1p00` | Can additional actors be admitted only in otherwise empty intervals? |
| Actor null admission | `actor_admit_cond0p50`, `actor_admit_cond0p40` | Is the 0070 actor conditional-null cutoff too strict? |

All constants are fixed in source before examining current per-arm dev outcomes.
`gap_insert` does not remove any baseline output but can still introduce false
objects; `guarded_swap` does not change boundaries but can incorrectly change
semantic types. Other alternatives re-run the frozen interval decoder.

## Development-only research selection

To replace the selected control, a candidate must:

1. Achieve at least **82% actor recall**;
2. Preserve or improve control exact sets and object F1;
3. Preserve recall for actors, actions, quantities and outcomes;
4. Preserve precision within **0.5 percentage points**;
5. Produce no overflow and improve actor recall or exact sets.

Among eligible candidates the research selector prefers exact sets, then actor
recall, object F1, and precision. Otherwise it retains `joint_actor_action_1p0`.
This is **not** the official G1 certification gate. Even a winning arm cannot
promote G1 or unlock G2 without full independent acceptance.

The report includes every arm's object metrics, per-kind recalls, paired exact
case gains/losses, recovered/lost correct objects, introduced/removed false
objects and a gold-assisted *diagnostic* attribution of missed actors. Oracle
ceilings remain entirely separate from inference decisions.

## Run from `main_computer\\nanojev`

Apply the patch at repository root, preserving paths `nanojev/tools/...`,
`nanojev/tests/...`, and `nanojev/G1_ORACLE_FABRIC_ACTOR_RESCUE_BAKEOFF_0071.md`.
Then run:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_actor_rescue_bakeoff_0071 `
  --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
  --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 `
  --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 `
  --admission-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-admission-bakeoff-0001 `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-actor-rescue-bakeoff-0001 `
  --device cuda
```

Outputs:

- `report.json` — immutable evidence, lineage, both cohorts, all arms, dev-only
  selection, nonregression evidence, diagnostic-only oracle attribution.
- `cases.jsonl` — per-case changes and errors for both models; no prompt text.

An existing output directory is an error; use a new numbered output directory
for another run. The runner does **not** modify production decoding or any
preexisting report.

## Status

Runner tests and source reference validation are possible without CUDA/Qwen.
The actual checkpoint experiment must run in the user's local environment.
Until it does, the winning strategy and any generalization claim are unknown.
G1 cutover and downstream stages remain **BLOCKED**.
