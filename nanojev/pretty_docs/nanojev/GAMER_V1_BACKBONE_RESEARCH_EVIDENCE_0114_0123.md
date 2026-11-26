# Gamer v1 — backbone research evidence ledger (0114–0123)

Record date: 2026-11-25 Pacific. This is a continuation of the 0103–0114 ledger, not a replacement of historical evidence. All figures below are research observations, not certification. Where possible they are transcribed directly from completed operator JSON; exact source files and digests are recorded in the retained assets register. Percentages are rounded, F1 is micro-averaged as reported, and counts of prompt variants must not be treated as independent base problems. The term fresh means held out in the named experiment, not necessarily independent of the generator or prior research.

## 0114–0117: high familiar-structure recovery, then semantic integrity failure

- 0114 frozen reader forensics: 192 prompts/24 bases, selected object policy 157/192 core-exact, 87/192 strict object-exact, F1 98.62%, TP 5,452, FN 36, FP 117. Endpoint reach 7,570/7,664. Complementarity is real on this population, but oracle coverage is not operational performance. No new backbone training.
- 0115 integrated reconstruction: the full 3,008-case campaign was suspended, not certified. Earlier interim results cannot substitute for a complete locked score. Its learned admission policy was retained as a research baseline and later used in 0116/0117; do not relabel these downstream trials as full 0115 acceptance.
- 0116 multilayer reconstruction: 48 prompts/12 bases, fresh generator-family cases. The validation-selected reduced integrated reader gives the following end-to-end relationship scores, separated by G1 input policy:

| G1 policy | G1 F1 | Reduced G2 F1 | Full new integrated G2 F1 | Reduced exact edgesets |
|---|---:|---:|---:|---:|
| original_frozen | 96.35% | 91.30% | 91.20% | 19/48 |
| 0114_full | 99.22% | 95.57% | 95.83% | 23/48 |
| 0115_selected | 98.92% | 95.72% | 95.91% | 24/48 |

Gold endpoints were a post-lock diagnostic, not a deployable input. On the same suite the reduced G2 reader with gold endpoints achieved 96.07%. The result motivated an integrity investigation rather than promotion.

The semantic integrity probe compared cached familiar cases against eight independently authored cases. With 0115 admission, G1 fell from 100% cached F1 to 87.23% authored F1; with 0114 it fell to 77.33%. The ablations showed dependence on learned activations, but dependence is not proof of semantic interpretation. Candidate structure and caps mattered on familiar cases. No direct answer leakage was established; exact prompt hash exclusion and Python-level file guards are limited safeguards, not a proof of novelty or an OS sandbox.

0117 joint reconstruction evaluated 28 author prompts (seven structures), including provisional implicit ownership and quantity annotations. The selected development policy remained baseline_0115. Joint admission did not rescue generalizable semantic graph reconstruction. All operational arms had zero strict graphs on this suite. The chosen baseline had no correct expected directional changes. Candidate inventory and gold-endpoint diagnostics showed available information did not reliably translate into correct admitted edges.

## 0118–0119: controlled semantic reversals

0118 holds endpoint inventory and relation type constant while changing the binding clause. On 32 fresh cases, 16 matched reversals, selected readers achieved 50% pairwise, 16/32 exact assignments and 0/16 complete reversals. All 15 frozen readers also scored 50% and zero reversals. Endpoint states before a later clause cannot causally encode that clause; suffix and other context surfaces were investigated. Only two fresh instruction constructions were shared across four relation types. Gold endpoints in this experiment are candidate anchors, not supplied true pairings.

0119 investigated 55 numerical surfaces including original/S endpoint, suffix and repeated-mention features, candidate queries and fixed/learned R. Selection was on development only; 32 exposed 0118 cases and 48 fresh 0119 cases were evaluated. Historical adapted Qwen was still the backbone.

| Fresh 0119 interface | Pairwise accuracy | Complete reversals |
|---|---:|---:|
| native_choice | 60.42% | 7/24 |
| native_candidate | 55.73% | 0/24 |
| selected | 55.73% | 3/24 |
| learnedR | 53.12% | 0/24 |
| fixedR_trained_head | 50.00% | 0/24 |
| native_choice_no_cue | 50.00% | 0/24 |
| query_21 | 63.02% | 2/24 |

The last row is a retrospective surface, not a preselected winner. Weak discrimination, order sensitivity and no-cue chance motivated direct investigation of the backbone. The previous high graph scores did not establish generalizable binding.

## 0120: backbone competence and historical adapter interference

Calibration completed on 16 prompts (eight reversal pairs). The full remaining 0120 four-condition generation experiment has not been supplied; later 0121/0122 native likelihood tests cover several of those populations but do not replace its generation comparison. The actual model receipt identifies Qwen/Qwen3-0.6B commit c1899de289a04d12100db370d81485cdf75e47ca, FP16 CUDA. Adapter-disabled logits matched bare exactly on the checked case; active adapter RMS logit difference was 3.4589467. PEFT reenabled 224 LoRA parameter flags upon leaving disable_adapter; the repair re-froze them and checked restored logits (max difference 0). No evidence of actual training in this failure. The prior bare prediction shards were reused.

| Condition | Exact symmetrized choices | Complete reversals | Candidate ranking | Exact generated | Expected A/B token mass |
|---|---:|---:|---:|---:|---:|
| bare | 16/16 | 8/8 | 87.50% | 8/16 | 51.90244% |
| bare_S | 14/16 | 6/8 | 87.50% | 9/16 | 96.80760% |
| adapted | 12/16 | 4/8 | 70.31% | 0/16 | 0.00630% |
| adapted_S | 12/16 | 5/8 | 70.31% | 0/16 | 0.03840% |

Bare choice accuracy is not ordinary question-answering accuracy: the bare model disagreed between option orders on all 16 cases. Its perfect score required symmetrization. The historical adapter lost four assignment decisions and severely suppressed the expected A/B token mass; ordinary generation also showed reversed directions and malformed answers. Strict parsing failures are not all semantic errors. The active adapter is a demonstrated interference source on this population, not proof that every form of LoRA is detrimental. S raises confidence without necessarily improving correctness.

## 0121: matched backbone-preserving acquisition

All six arms used newly initialized shared readers, same two-epoch graph fit budget, same cohort. The direct delta and new low-rank arm have different parameter counts; the readers are not identical to the prior ensembles. Native preservation checks use S when present, not R (R is extraction-only). The validation winner C failed broader preservation; H was comparator only. All six scored 50% on fresh conditional binding (32 prompts/16 reversals) with zero complete reversals.

| Arm | Validation G1 | Fresh G1 | Fresh G2 | Lost native correct | Gained native correct | Heldout preservation |
|---|---:|---:|---:|---:|---:|---:|
| A_bare_readers | 66.36% | 67.97% | 13.52% | 0 | 0 | PASS |
| B_bare_S | 65.86% | 66.77% | 12.85% | 1 | 6 | FAIL |
| C_bare_SR | 73.56% | 75.99% | 17.19% | 3 | 5 | FAIL |
| D_direct_q | 65.65% | 67.27% | 13.43% | 0 | 1 | PASS |
| E_new_low_rank_q | 66.36% | 67.97% | 13.52% | 1 | 0 | FAIL |
| H_historical_adapter | 60.71% | 61.87% | 13.86% | 29 | 10 | FAIL |

Bare readers improved G1 over the historical comparator with matched reader training. S/R gave the best fresh G1/G2, at the cost of native interference. Restricted direct and new low-rank updates offered no compelling acquisition benefit at this budget. The comparison did not establish converged performance.

## 0122: frozen-backbone contextual readout

All six arms use the same frozen original Qwen. Wider 192-dimensional readers, balanced hard negatives, candidate attention, explicit contrast loss, negative thresholds and up to six epochs. Native preservation remains scoped. Results are from `result-with-oracles.json`, with operational prediction lock retained separately. Note fresh graph populations differ between 0121 and 0122; the 0122 exposed0121_graph column permits a more directly comparable retrospective cohort (not a new blind test).

| Arm | Epoch selected/run | Fresh G1 | Fresh G2 | Gold-endpoint G2 | Conditional accuracy | Reversals | Native losses |
|---|---:|---:|---:|---:|---:|---:|---:|
| A_bare | 3/5 | 97.39% | 27.31% | 28.84% | 50.00% | 0/24 | 0 |
| B_fixed_R | 2/4 | 94.80% | 25.34% | 31.16% | 50.00% | 0/24 | 0 |
| C_learned_R | 6/6 | 93.54% | 33.08% | 38.69% | 50.00% | 0/24 | 0 |
| D_SR | 6/6 | 96.93% | 31.79% | 34.44% | 50.52% | 0/24 | 5 |
| E_attention | 4/6 | 96.38% | 28.97% | 31.97% | 50.52% | 0/24 | 0 |
| F_R_attention | 4/6 | 97.14% | 28.65% | 32.52% | 50.00% | 0/24 | 0 |

| Arm | Exposed 0121 graph G1/G2 | Exposed 0117 graph G1/G2 | Historical 192 graph G1/G2 |
|---|---:|---:|---:|
| A_bare | 95.76% / 25.19% | 81.61% / 38.53% | 95.62% / 24.17% |
| B_fixed_R | 94.99% / 25.41% | 77.93% / 32.84% | 94.09% / 21.61% |
| C_learned_R | 93.97% / 34.90% | 61.72% / 18.58% | 94.16% / 32.13% |
| D_SR | 95.47% / 32.71% | 71.74% / 30.62% | 96.53% / 31.17% |
| E_attention | 95.35% / 32.30% | 79.07% / 39.86% | 96.10% / 29.67% |
| F_R_attention | 95.78% / 28.05% | 70.80% / 37.50% | 96.35% / 28.71% |

A bare fresh graph: 1,368 required objects all mapped and correct top kind before admission, 1,325 TP, 43 FN, 28 FP; 16/48 exact object sets and 19/48 core-complete. G2 1,341/1,448 endpoints reachable, 656 TP, 792 FN, 2,700 FP. The remaining G1 admission deficit is localized on this cohort, not globally solved. C learned R leads G2 graph metrics, but its binding reversals remain zero. D S/R is the validation winner but fails preservation. All six have zero strict fresh graphs. F attention + R does not beat C in G2; attention adds parameters, so capacity is not isolated.

Fresh binding: native bare Qwen achieves 30/48 exact symmetrized choices and 9/24 complete reversals; no individual case is correct in both option orders. All six readers fail every complete reversal; the no-cue scores are also chance. A no-cue control at chance cannot validate an already chance-level reader. The no-S arms preserve the tested native interface. R's extraction effect is not evidence of improved native generation.

Contrast training loss, with loss = mean softplus(negative - positive) + 0.5 binary cross entropy, has zero-logit reference 1.5 ln(2) = 1.03972077. Mean per-epoch training contrast loss (not a fixed-checkpoint fit score):

| Arm | Epoch losses |
|---|---|
| A_bare | 1.049003, 1.067338, 1.065246, 1.053979, 1.048205 |
| B_fixed_R | 1.041679, 1.070623, 1.058332, 1.056855 |
| C_learned_R | 1.042820, 1.067158, 1.067385, 1.052174, 1.047458, 1.044638 |
| D_SR | 1.042243, 1.078614, 1.063586, 1.053052, 1.046856, 1.046604 |
| E_attention | 1.045152, 1.056158, 1.050832, 1.044287, 1.046116, 1.045243 |
| F_R_attention | 1.044848, 1.059147, 1.049014, 1.045512, 1.044488, 1.042394 |

The observed training losses cluster near or above the null reference. This is evidence that effective contrast learning has not been demonstrated; it does not prove zero gradients, exact feature equivalence, or an immutable backbone limitation. We must first test single-pair fitting without competing graph updates. No further broad bakeoff is warranted before that test.

## 0123: delivered experiment, no measured result yet

Newly initialized 0122 E attention reader, bare frozen Qwen, no S/R and no graph loss, balanced complete reversal updates. Sixteen distinct fit prompts, 120 holdout prompts; the one-pair fit is included in the eight-pair fit. All five gates and cue ablations specified in the current guide. Fixture passes reported on delivery; actual live CUDA unverified. Do not add a score here until the operator report exists. Even full gate passage establishes only elementary conditional ownership binding, not full graph extraction or the other relations.
