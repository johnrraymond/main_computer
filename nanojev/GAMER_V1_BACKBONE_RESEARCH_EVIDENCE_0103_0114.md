# Gamer v1 — backbone research evidence ledger (0103–0114)

Recorded 2026-11-24 from actual operator reports through G1-0114 and the delivered G1-0115 source. See [current research guide](GAMER_V1_BACKBONE_RESEARCH_CURRENT.md) for decisions and [0115 protocol](G1_0115_INTEGRATED_SEMANTIC_RECONSTRUCTION_CERTIFICATION.md) for next execution. Results are research observations, not release certification. No new canonical model promoted; G3 locked.

## Why the investigation moved upstream: G2-0103–0109

G2-0103 raw-score graph reranking on reused fit cross-validation: 275 graphs, G1 exact 275, 6,832 TP, 731 FP, 455 FN, edge F1 92.013%, exact graphs 61. G2-0106 found residual errors were missing/spurious edges rather than incorrect types on positive pairs. G2-0107 found 452/455 missing edges below threshold, one capacity rejection and two wrong winning non-none types; 490/731 false edges had unanimous support from seven experts. Oracle veto of 510 false edges rescued only two missed true edges. This argues against assuming a decoder-only conflict fix will solve the graph problem.

G2-0108 joined frozen score records to source prompts. Among the inspected errors, all 342 false `refers_to` edges connected different strings and all 227 missed `refers_to` edges connected identical strings; 96/168 false `applies_to` edges were forward-quantity cases. The reranker lacked direct source text/embedding evidence. A retrospective lexical/geometry veto estimated removal of 438 false positives without losing true positives on that cohort. G2-0109 implemented the frozen intervention and reproduced the retrospective gain (F1 about 94.81%). But on 48 fresh challenge prompts (12 independent bases, four forms), the same author-gold rule rejected 48 valid edges across alias, pronoun and forward forms. G1 exact only 9/48. Thus the shortcut was not a general semantic rule and the upstream extractor became the next priority. Retain this negative result; do not promote the lexical veto as a universal rule.

## G1-0110 — frozen semantic fuzzing laboratory

200 independent bases x 12 forms = 2,400 prompts; discovery/validation/holdout assigned by base. Frozen G1, no training. 30,000-case expansion was proposed, not executed in the supplied evidence. The pilot shows systematic sensitivity to prompt transformations even when most individual objects are correct.

Strict exact: 1030/2400 (42.92%); correct typed exact spans: 67,791/69,312; precision 96.71%, recall 97.81%. 147/200 ordinary controls were perfect. Action boundaries and relevance dominate many errors; pronoun, forward, alias and prefix changes can damage otherwise correct objects. Annotation of explanatory notes may create extra-only disagreements. The pilot is synthetic and does not prove natural-language generalization.

## G1-0111 — historical S-first reference register

Reused historical `nanojev_code_sparse_register_k1000_s_first_train.py` and `nanojev_code_sparse_register_k1000_s_first_r2_full_head_train.py`. Earlier logarithmic activation-map and hybrid EMP numerical writer work are relevant historical precedents, not equivalent implementations. The S-first port prepends a learned numerical embedding; the dynamic path reads a first frozen backbone pass and applies R1 to S for a second pass. Original token span coordinates remain aligned. Frozen backbone and G1; 48 fit prompts from eight bases, 64 test prompts from eight disjoint bases. 96 updates per learning phase; 67,649 trainable parameters total. Only the reference/controller learns.

| Arm | Core /64 | Strict /64 | F1 | FN | FP | Unseen combination core /16 |
|---|---:|---:|---:|---:|---:|---:|
| original | 23 | 12 | 95.115% | 57 | 113 | 1 |
| zero_reference | 0 | 0 | 21.362% | 1447 | 504 | 0 |
| negative_reference | 24 | 12 | 95.397% | 54 | 106 | 1 |
| static_S | 35 | 17 | 95.927% | 40 | 102 | 5 |
| dynamic_S_R1 | 35 | 15 | 95.575% | 49 | 105 | 2 |

Static S reduced incomplete-core cases from 41 to 29 (29.3% net reduction). Static and dynamic have 26 shared core successes and nine unique successes each (44 combined). Including original gives 46/64 observed oracle core successes. This is not an operational selector. Negative reference in 0111 negates the dynamic reference, not a clean static `-S` control; 0112 supplies a static sign test. Zero reference adds a zero-valued token and is not absence. The writer provides leverage but the small sample does not establish the proposed holographic/logarithmic structure or an optimal numerical encoding.

## G1-0112 — backbone transmission and controllability

Model: Qwen/Qwen3-0.6B, float16 capture, embedding dimension 1024, embedding rows 151936; observed table RMS 0.0292028591, learned S RMS 0.0015019092 (5.143% of embedding RMS), S norm 0.0480610952. Probe and factorial cohorts are disjoint. Distribution probe uses sampled original-prompt positions and independent linear semantic-category readers. Selected observed measures:

| Tap | Original balanced category accuracy | Original effective rank | S balanced category accuracy | S effective rank |
|---|---:|---:|---:|---:|
| Embedding input | 94.68% | 34.50 | 94.68% | 34.50 |
| L7 | 99.46% | 44.59 | 98.83% | 45.72 |
| L14 | 99.72% | 45.91 | 99.48% | 44.77 |
| L21 | 99.79% | 48.58 | 99.66% | 47.11 |
| L28 | 98.63% | 16.08 | 98.64% | 14.98 |

Shuffled labels approximately 18–21%. Category decoding is not exact span extraction, relevance, relationship understanding or full distribution knowledge. Effective rank refers to the sampled representations and does not prove irreversible information loss.

Independent four-bit payloads encoded in reference vectors were recovered from original-prompt positions excluding the reference itself: 230/256 bits (89.84%) and 42/64 whole messages, versus 128/256 bits (50%) and 3/64 whole messages without payload. This is evidence of numerical transmission on the tested cohort, not maximal channel capacity or proof of semantic control. The payload is independent of words, not an oracle answer supplied to production.

Restricted local control: eight reference directions, four cases, correction norm about half S. Aggregate baseline deficit 15.0485, predicted corrected 11.7883, actual corrected 15.0248, random 18.9666. The linear correction did not reliably realize its predicted benefit. It is a negative result for the tested subspace/step size, not proof of uncontrollability. Static amplitude/direction probe on eight cases: zero F1 27.48%, S 96.94%, original 97.10%, -S 97.64%, orthogonal_1 97.64%, rotation_minus_45 97.83%. Matched direction controls weaken any claim of uniqueness of learned S. These are exploratory small-cohort results, not an amplitude selection gate.

Factorial: 72 training prompts (12 bases), 128 held-out prompts (16 bases), 24 microset steps then 96 fit steps for trainable arms. Qwen remains frozen. Microset losses before 0.69384; writer after 0.64777, reader 0.46309, joint 0.43330. None proves complete memorization.

| Arm | Core /128 | Strict /128 | F1 | FN | FP | Reachable /5392 |
|---|---:|---:|---:|---:|---:|---:|
| original | 44 | 24 | 95.539% | 127 | 225 | 5045 |
| A_frozen_S | 51 | 26 | 95.411% | 122 | 241 | 5023 |
| B_writer | 60 | 28 | 95.860% | 99 | 229 | 5083 |
| C_reader | 76 | 37 | 96.422% | 83 | 200 | 5135 |
| D_joint | 75 | 36 | 96.285% | 86 | 208 | 5087 |

Reader-only leads on core, strict, F1 and endpoint reachability. Relative to frozen S, writer +9 core (9 gains, 0 harms), reader +25 (28 gains, 3 harms), joint +24 (27 gains, 3 harms). The factorial interaction on core count is -10 relative to simple additivity; overlapping improvements and interference remain competing explanations. Reader-only removes 32/84 original core failures on net (38.1%). The original-no-S trained-reader arm was missing here; 0113 explicitly supplies it. Actual G2 prediction has not been evaluated with these new features.

## G1-0113 — multilayer semantic observability and reconstruction

Capture original and S-conditioned Qwen hidden states once; reader fitting does not load Qwen. 96 train/16 bases, 64 validation/8 bases, 192 test/24 bases. Two epochs, full-reader LR 0.0003, residual LR 0.002. Validation ranks core, strict exact, F1, then negative decode errors; checkpoint selection precedes test. Same full-reader architecture and initial G1 head across layers, but this initialization was trained for final-layer features and may favor that representation. Embedding-only is a control for the tested head/budget, not a proof that no other embedding-only architecture could do better. All metrics below use the composite decoder, not native decoding.

| Arm | Core /192 | Strict /192 | F1 | FN | FP | Reachable /7664 |
|---|---:|---:|---:|---:|---:|---:|
| S_L14 | 88 | 39 | 95.710% | 178 | 298 | 7091 |
| S_L14_21 | 111 | 89 | 98.228% | 112 | 82 | 7291 |
| S_L21 | 105 | 68 | 97.296% | 126 | 172 | 7274 |
| S_L28 | 117 | 54 | 97.210% | 123 | 185 | 7235 |
| S_L7 | 69 | 37 | 94.440% | 282 | 331 | 6813 |
| S_L7_14_21_28 | 99 | 78 | 97.591% | 120 | 145 | 7303 |
| S_frozen | 85 | 46 | 95.299% | 187 | 336 | 7122 |
| S_residual28 | 109 | 48 | 96.379% | 152 | 249 | 7214 |
| original_L14 | 71 | 51 | 97.172% | 162 | 148 | 7181 |
| original_L14_21 | 136 | 92 | 97.875% | 99 | 135 | 7335 |
| original_L21 | 106 | 82 | 97.819% | 128 | 111 | 7244 |
| original_L28 | 108 | 38 | 96.761% | 125 | 234 | 7273 |
| original_L7 | 97 | 54 | 95.823% | 143 | 323 | 7304 |
| original_L7_14_21_28 | 129 | 73 | 97.411% | 88 | 199 | 7428 |
| original_embedding | 9 | 1 | 79.371% | 1069 | 1228 | 5286 |
| original_frozen | 68 | 44 | 95.120% | 196 | 347 | 7131 |
| original_residual28 | 112 | 64 | 96.707% | 128 | 237 | 7305 |

Validation winner: `original_L7_14_21_28` (129 core, 73 strict, best endpoint reachability 7428). Retrospective core/strict leader: `original_L14_21` (136 core, 92 strict), reducing 124 original core failures to 56 and 543 object errors to 234. Highest object F1: `S_L14_21` (98.228%, 194 errors, but only 111 core and 89 strict). Distinguish these objectives. None is production-promoted.

| Transformation (24 cases each) | Original frozen core | Original L14+21 | Validation winner L7+14+21+28 |
|---|---:|---:|---:|
| control | 21 | 17 | 20 |
| alias | 5 | 17 | 20 |
| pronoun | 5 | 17 | 20 |
| forward | 2 | 17 | 10 |
| caps | 17 | 18 | 19 |
| prefix | 15 | 18 | 19 |
| alias+forward | 0 | 16 | 10 |
| pronoun+forward | 3 | 16 | 11 |

All-reader observed whole-reader core oracle: **192/192**. This means at least one of the 17 composite readers recovers every required object per case, extras allowed. It does not imply strict exact recovery, a gold-free selector, 100% G2 accuracy or independent generalization. 0114 measures additional ceilings and practical selection. S has different effects across readers; no matched S comparison has a base-cluster 95% interval establishing a positive core delta. Preserve S-conditioned readers for complementarity and the numerical transmission research, not as a mandatory input modification. Control regression (21 -> 17 for L14+21) requires attention. The full four-layer reader improves endpoint reachability by 297 (7131 -> 7428), not actual relationship accuracy.

## G1-0114 — completed, retrospective reconstruction

17 frozen readers, 88 policies. No training or Qwen execution. Validation selected `architecture_6` with 64/64 core, 39/64 strict, 1,856 TP, 0 FN, 28 FP. The original 0113 test cases are exposed development, not a new independent test.

| Policy (192 prompts) | TP | FN | FP | F1 | Core | Strict | Reachable edges / 7,664 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Original frozen G1 | 5,292 | 196 | 347 | 95.12% | 68 | 44 | 7,131 |
| Best single core reader, L14+21 | 5,389 | 99 | 135 | 97.88% | 136 | 92 | 7,335 |
| Validation-selected whole consensus | 5,451 | 37 | 102 | 98.74% | 156 | 89 | 7,556 |
| Validation-selected architecture_6 | 5,452 | 36 | 117 | 98.62% | 157 | 87 | 7,570 |
| Retrospective core leader cross_source_3 | 5,464 | 24 | 197 | 98.02% | 168 | 80 | 7,598 |

Architecture_6 reduced combined errors by 390/543 (71.82%) and incomplete cores by 89/124 (71.77%); missing objects fell 160/196 (81.63%). Precision 97.899%, recall 99.344%; endpoint reachability 98.774%, not actual G2 accuracy. Whole consensus is a serious comparator: fewer extras and better strict/F1, slightly lower core/endpoint accessibility. The retrospective best core rule sacrifices precision and cannot be promoted on the opened test.

All 5,488 gold objects occurred in the union, with 2,375 false candidate objects. Whole-reader core oracle 192/192; strict oracle 167/192. Compatible object union strict oracle 192/192. Agreement bins: 1–2 readers 0 true, 1,975 false; 3–14 readers 356 true, 389 false; 15–17 readers 5,132 true, 11 false. Four false objects had unanimous support. This supports a persistence/invariance hypothesis, not reader independence or guaranteed correctness. Alias, pronoun and their forward combinations produced 108/117 selected extras. Relevance and annotation review are necessary; no labels automatically changed.

## G1-0115 — integrated laboratory (pending)

The operator combined proposed experiments 0115–0118 into one automatic run. It performs old residual forensics, fixed reconstruction search, conditional candidate-admission calibration, reader reduction, policy sealing, fresh synthetic evaluation (default 3,008 prompts/256 bases), actual G2-0087 native binding comparisons and a measured research record. No Qwen or reader training. Fresh answers remain separate from prediction; no post-holdout tuning. See the [0115 protocol](G1_0115_INTEGRATED_SEMANTIC_RECONSTRUCTION_CERTIFICATION.md). The result is pending operator execution; do not infer new performance from implementation or fixture tests.

## Reproduction and provenance

All module invocations below are from `main_computer\nanojev` with the existing `$py`. For 0111–0115 the delivered source modules expose `audit` and `selftest`; the latter is a fixture smoke, not production certification. These commands document the completed sequence, not a request to rerun completed experiments. Preserve artifacts and avoid overwriting results.

| Experiment | Python module (`tools.` prefix) | Main invocation | Report directory relative to `runtime/reports/gamer-v1/` |
|---|---|---|---|
| 0110 | `nanojev_gamer_v1_g1_semantic_fuzz_lab_0110` | pilot frozen capture | `g1-semantic-fuzz-0110/pilot/` |
| 0111 | `nanojev_gamer_v1_g1_s_first_reference_0111` | `run --device cuda` | `g1-s-first-reference-0111/` |
| 0112 | `nanojev_gamer_v1_g1_backbone_transmission_0112` | `probe --device cuda`, `run --device cuda` | `g1-backbone-transmission-0112/probe/`, `run/` |
| 0113 | `nanojev_gamer_v1_g1_multilayer_reconstruction_0113` | `cache --device cuda`, `fit --device cuda` | `g1-multilayer-reconstruction-0113/fit/` (cache in parent) |
| 0114 | `nanojev_gamer_v1_g1_reader_forensics_0114` | `run --device cuda` | `g1-reader-forensics-0114/` |
| 0115 | `nanojev_gamer_v1_g1_integrated_reconstruction_0115` | `run --device cuda` | `g1-integrated-reconstruction-0115/` |

Recorded operator report SHA-256 (from console, not hash of chat paste):

- 0111 result: `d8cfe11a50091e6d5bb16e33d83cdc3b965a03dc5178782a16c5149b5acfd1ae`
- 0112 probe: `54379186454c6e509a6b6e7707140b4d5880eaf2b5cd03372bca3552e04cbe88`
- 0112 factorial: `45568213ef2849b2f6c81eb6bdc4a59f9e2814a74b686a076098262988708f21`
- 0113 result: `13ee08d5c1242abc59225b0623dc003f820fc8510b03e1af34280f1560c708f0`
- 0114 result: `cb623df48e52828e0ffe73ab62fc27e1a4a35681de43d902e88f8ffc9f5a5d8a`

0110 cohort seal: `51e5ab948bf5646516877026916cf11a83377a71452870a287986305a66f8301`. 0113 seal: `7557dc92fe65cfc5feb441f9d228eb0c87b65dbac01138b57602afd951d6c521`; cache manifest: `cb0314076077cc3b36267af47da70f995a4f7f0d4e590f2b4834574b52a2f7ec`. Preserve actual reports, not merely the values reproduced here.

Source material used for this ledger: operator-provided G2-0103, 0106–0109 JSON reports and G1-0110, 0111, 0112, 0113 and 0114 reports/console transcripts; delivered 0111–0115 source patches; repository documentation structure from the 2026-11-22 source snapshot. The 0110 command spelling has not been verified against its absent source patch here; its module and report path are historical references, not an asserted complete invocation. The latest local documentation after that snapshot is not available. These new documents do not overwrite any older normative or experiment record.


## G1-0115 interim evidence and conditional transition to G2 (2026-11-24)

The 0115 residual forensic count was 30 admission-rejected gold objects, 6 compatibility-rejected gold objects, 96 disjoint extras, 21 overlapping same-type boundary extras. 290 development trials retained the full 17-reader inventory. The selected architecture policy used threshold 0.18 and margin weighting. A JSON list/tuple endpoint serialization defect initially blocked downstream inference; the narrow repair retained the original development/answer seal and separately verified the corrected implementation. The operator confirmed selftest and resumed CUDA prediction. At the operator-reported first 83 cases, six independent bases were represented (five complete), not 83 independent structural problems.

| Route | G1 precision | G1 recall | G1 F1 | Core | Strict | FN | FP | G2 endpoint access | Historical 0087 G2 F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Original | 94.0284% | 96.9712% | 95.4771% | 45/83 | 29/83 | 60 | 122 | 93.5424% | 37.3714% |
| 0114 full | 98.6534% | 99.8486% | 99.2474% | 80/83 | 58/83 | 3 | 27 | 99.5894% | 38.3664% |
| 0115 selected | 96.0718% | 100% | 97.9965% | 83/83 | 37/83 | 0 | 81 | 100% | 38.2918% |

The reader union contained all 1,981 gold objects; 0115 rejected none. Historical 0087 with the 0115 endpoints had zero upstream missing relationships, 1,379 reachable missing relationships, and 774 extra edges. No direct conclusion about the advanced 0103 reranker follows from the 0087 result. The operator accepted the observed trend as a working premise, with an explicit obligation to revisit if downstream evidence contradicts it. Do not expend the remaining 2,925 predictions merely to complete a prechosen count before beginning G2. Preserve the fresh seal/shards and distinguish interim data from completed certification.

## G2-0116 prospective protocol (not a measured result)

Transfer the G1 research method to relationship extraction: 15 trainable matched cached readouts (embedding, original and S-conditioned L7/14/21/28, L14+21 and all four; two additional specialist and lexical arms), frozen seven 0095/0096 experts and historical 0087 baseline; complete binding graph reconstruction with typed pairs, independent reader votes, confidence/architecture/relationship weights, optional train-derived capacities and reader reduction. Evaluate original G1, 0114 and 0115 endpoints on the same new cases. After locking blind predictions, use exact gold endpoints to isolate downstream defects. Report unavailable gold, rejected gold, spurious edges, wrong types, invariant relations and per-base performance. Use a 12-base round-robin 48-case default across both generators to gain structural evidence early. No promotion or G3 authorization. See [G2-0116 protocol](G2_0116_MULTILAYER_RELATIONSHIP_RECONSTRUCTION.md).

Historical 0103 raw reranking (92.013% F1 on reused fit cross-validation, 61/275 exact) is not directly comparable with a fresh 0116 cohort. Its source module `nanojev_gamer_v1_g2_raw_graph_reranking_0103.py` is absent from the available 2026-11-22 snapshot; the differently named admission-stability 0103 runner does not implement it. The 0116 patch therefore evaluates the verified seven constituent heads directly and records the installed reranker source signature when available. The exact later router/reranker path remains an explicit missing comparator, not a claimed reproduction.
