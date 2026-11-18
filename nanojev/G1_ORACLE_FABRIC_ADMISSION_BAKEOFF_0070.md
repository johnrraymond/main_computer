# GAMER G1-0070: Frozen Oracle-Fabric Candidate Admission Bake-off

## Governing question

Can a **label-blind**, inference-only admission rule recover correct G1 objects from the already-frozen neural output, beyond the demonstrated `whole_linear` span decoder, **without trading away exact spans, actor/action recall, or precision**?

**No training or architectural promotion occurs.** Both heads, the frozen Qwen/LoRA source, and the preceding decoder report are hash-checked. The old system remains authoritative and G2 remains blocked.

## Established evidence — not proposed results

The previous G1 frozen decoder bake-off selected `whole_linear` on development for **both** checkpoints:

| Frozen checkpoint | Development exact | Development F1 | Previously examined challenge exact | Challenge F1 |
|---|---:|---:|---:|---:|
| Oracle-Fabric G1 | 21/80 | 96.283% | 13/32 | 96.842% |
| Legacy G1 | 19/80 | 96.572% | 9/32 | 96.653% |

The Oracle-Fabric development actor recall with `whole_linear` was **80.833%**, below the existing **82%** per-kind recall requirement. Development exact-set accuracy **26.25%** remains far below the 80% requirement. The development-only oracle-assisted eligible-candidate ceiling was **28/80** exact sets, while the oracle-assisted non-null-type ceiling was **71/80**. These oracle ceilings use gold spans and are **not valid deployable inference**.

This experiment asks whether relaxing the rejection of spans that lose to the null class can improve the actual inference results, and whether typed/whole-span decisions should be coupled. A high oracle ceiling **does not establish** that a label-blind admission rule exists.

## Experiment arms

`whole_linear` is the mandatory unchanged control, not the older `global` decoder. All other arms use exactly the same frozen logits, type labels, valid-span masks, and length-weighted global nonoverlap selection. They vary only admission/ranking:

| Arm | Controlled intervention |
|---|---|
| `whole_linear` | Original neural eligibility; complete-span length weighting; **control** |
| `rank_actor_action_0p5` | Reward previously eligible actor/action candidates; **no new admission** |
| `rescue_actor_0p5` | Admit a high-conditional-confidence actor with null deficit ≤ 0.5 using a small residual score |
| `rescue_action_0p5` | Same rescue applied only to actions |
| `rescue_actor_action_0p5` | Combined actor/action admission with a small residual score |
| `rescue_actor_action_1p0` | Wider actor/action admission at null deficit ≤ 1.0 |
| `rescue_all_0p5` | Admit any supported semantic kind with null deficit ≤ 0.5; broader-scope ablation |
| `joint_actor_action_0p5` | Combine type ranking bonus with null-offset admission at deficit ≤ 0.5 |
| `joint_actor_action_1p0` | Same combined mechanism with null deficit ≤ 1.0 |
| `runnerup_actor_prior_0p75` | Actor type-prior on near-tied actor/action classes, with **no new spans** |
| `joint_runnerup_actor_prior_0p75` | Joint null-offset admission and near-tied actor-type alternative |

The rescue-only variants give newly eligible candidates a deliberately *small* score (`0.2 × conditional non-null probability`). The joint variants instead shift the null-vs-type margin by the fixed allowance. Every candidate is computed without source text, oracle labels, or a gold span. The runner-up alternative uses an explicit, asymmetric actor prior and is an exploratory ablation, not a semantic truth.

The conditional probability is computed among the **non-null classes**, and rescued candidates must exceed a fixed 0.60 conditional-probability floor. All interventions are deterministic, predeclared, and testable. Neither parameter fitting nor per-case gold inspection occurs in inference.

## Run (from `main_computer\nanojev`)

Apply the changed-files-only ZIP at the **repository root** first, then run:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_admission_bakeoff_0070 --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 --decoder-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-decoder-bakeoff-0001 --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-admission-bakeoff-0001 --device cuda
```

The output directory must **not exist**. No prior report is overwritten.

The runner prints `G1_ADMISSION_DEV ...`, then `G1_ADMISSION_DEV_SELECTION` (choices frozen using development metrics only), then `G1_ADMISSION_CHALLENGE ...`, and finally `G1_ADMISSION_RESULT`.

## Outputs and interpretation

- `report.json`: all arms on each frozen checkpoint; exact sets, object precision/recall/F1, actor/action/quantity/outcome recall, per-arm candidate errors, new false admissions, removed false objects, paired exact-case discordance tests, gold-assisted oracle ceilings, and immutable hashes.
- `cases.jsonl`: model/split/prompt digest, baseline exactness, and **which** gold objects were recovered/lost and false objects added/removed by each intervention.

The research selector may return `whole_linear`: **no successful improvement** is a valid result. Another arm is selected only if development exact sets, F1, and every required kind's recall are nonregressing relative to `whole_linear`, precision loses less than one percentage point, and exact sets or F1 strictly improve. An arm overflowing the 128-object limit on any development example cannot be selected. Overflow examples are scored as failed predictions rather than silently omitted.

A paired exact-case discordance p-value is reported descriptively; multiple exploratory arms and an already reused development corpus preclude independent statistical certification. The challenge cohort was previously examined and is therefore **historical challenge**, not blind/sealed acceptance. It never changes the development-selected arm.

For each split, the oracle-only accounting partitions gold objects into (i) already eligible with the correct non-null class, (ii) correct non-null class but excluded by current admission, (iii) gold class not the strongest non-null class, and (iv) unsupported or unaligned spans. These are **diagnostic attribution counts**, not model predictions.

## Nonregression / safety gates

- The prior decoder report **must** identify the exact same dataset and both frozen checkpoint hashes and have selected `whole_linear` for each.
- The unmodified `whole_linear` per-object totals and exact cases must **exactly reproduce** the previous bake-off on both development and historical challenge; mismatch aborts.
- The original frozen-Qwen source adapter, dataset, checkpoint files and predecessor report must remain hash-stable throughout the run.
- Gold must not enter candidate admission, scoring, or selection. Gold is accessed for evaluation and oracle-only diagnostic ceilings only after all same-input neural predictions are computed.
- No learned weights, checkpoint promotion, graph-curriculum gates, existing production decoder files, or sealed tests are modified.
- `G1 DEVELOPMENT_GATE_BLOCKED` and no advancement to G2 remain unchanged irrespective of experimental results.

**Next decision:** A measured nonregressing inference-only improvement warrants a new, separately verified integration and independent acceptance. Failure to improve should drive oracle-guided redesign of candidate admission/default weighting for the **next full deterministic retraining campaign**, not another unexamined training continuation.
