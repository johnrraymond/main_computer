# G1/G2-0119 — Semantic addressability and contextual readout

## Motivation and scope

0117 and 0118 did not establish general semantic discrimination. In 0118, all four selected relation discriminators scored 50% pairwise accuracy and zero correct reversals on fresh conditional binding tasks. This does not prove that the frozen Qwen representation lacks the required information: the reader was limited to a fixed random projection and pooled context, and anchor token states cannot see later instructions in a causal model.

0119 isolates native conditional binding capability from the ability to address it numerically. It does not modify existing G1/G2, S, LoRA adapter, backbone, or production. G3 remains locked. The supplied anchors and relation type are conditional hypotheses, not operational extraction, and no true pair is supplied to prediction.

## Execution (from nanojev)

```
& $py -u -m tools.nanojev_gamer_v1_semantic_addressability_0119 audit
& $py -u -m tools.nanojev_gamer_v1_semantic_addressability_0119 selftest
& $py -u -m tools.nanojev_gamer_v1_semantic_addressability_0119 native --device cuda
& $py -u -m tools.nanojev_gamer_v1_semantic_addressability_0119 run --device cuda
```

The optional `native` command runs the first control independently. The full `run` includes it and reuses its cached results. `--register-epochs` defaults to 2 and is part of the seal identity; do not change it mid-experiment. `audit` requires the completed 0118 and its parent lineage. `run` uses separate prepare, native_predict, native_score, develop, predict and score subprocesses. The two prediction processes use the existing answer-file audit guard (Python-level, not an OS sandbox). Native retrospective scoring and fresh scoring verify completed prediction hashes before accessing answers. The predictor never invokes the author module. Cached stages and completed relation selections are reused only if their lineage matches.

## Design

- Exposed control: 32 original 0118 fresh prompts, now retrospective. The earliest report `native-exposed0118.json` measures frozen adapted Qwen directly on the failed cases.
- New fit: 64 prompts, four instruction constructions shared across four relation types.
- New validation: 32 prompts, two disjoint instruction constructions.
- New fresh: 48 prompts, three additional instruction constructions. Each relation/construction group has both directions and both paraphrases. Reversal pairs keep original anchor positions and strings fixed. Prompt hashes are checked against the 0118 authored population. This is an authored structural split, not proof of general novelty.
- Native choice: next-token A/B logit comparison between two complete candidate assignments, with both option orders; sign-corrected difference cancels fixed A/B preference. Native candidate: Yes/No logit margin for each of the four candidate statements. This is forced-choice likelihood, not free generation. No answer tokens are appended. A cue-deleted condition keeps the inventory but removes the assignment suffix; the query itself remains. Relation statements are fixed by the public relation type.
- Passive full-width readouts: original and S-conditioned hidden layers 0, 7, 14, 21, 28 as available, RMS-normalized without random projection; endpoint, candidate-conditioned suffix, and ordered suffix bins with exact repeated-mention alignment. The ordered representation retains coarse order, not every token separately. This tests a different pooling surface, not all possible decoders.
- Active text query: each candidate receives its own question after the full record, reading its final context at layers 0, 7, 14, 21, 28. S-conditioned version and a fixed random numeric suffix register version included.
- Active numeric register R: each candidate question receives the same learned numeric suffix vector within a relation. The frozen backbone performs a differentiable forward pass. Only the relation's R and linear readout train on correct-versus-competing margins. The fixed-R control trains the same readout with R held constant. Both start with the same random vector and zero head, training order and budget; epoch selection is on validation. Two epochs are an initial diagnostic budget, not a convergence claim. The report includes relative RMS register displacement and no-cue control.
- Geometry control and native no-cue controls help expose assignment-order bias. Native output logits are diagnostic controls, not eligible for numerical readout selection. Each relation independently selects a numerical surface using validation reversal success, then exact binding count, then pairwise accuracy. All candidate readouts are retained for retrospective scoring; retrospective best surfaces are not certified winners.

## Interpretation

If native choice reliably reverses but passive readouts do not, the deployed model has conditional competence that the passive interface has not captured. If active readouts recover this competence, investigate candidate-specific addressability. If learned R beats the matched fixed R and query-only controls on the frozen fresh cohort, register learning has evidence of additional utility. If native and numerical arms fail, the tested model and query methods are insufficient; do not infer that no possible decoder could recover meaning. If the native cue-deleted control succeeds unexpectedly, inspect corpus/option order and evaluation integrity. Require correct reversals in both directions and paraphrase consistency, not only aggregate pairwise accuracy. The experiment does not address G1 candidate coverage and does not establish operational graph competence even if every controlled binding is correct.

## Evidence and output

`../runtime/reports/gamer-v1/g1-g2-semantic-addressability-0119/` contains sealed cohorts, resumable native and feature caches, per-relation selection receipts, development.json, selection.json, prediction locks, result.json, and generated RESEARCH_RECORD.md. The console emits NATIVE_RESULT before readout fitting, NATIVE_VALIDATION, REGISTER (including register displacement), SELECTED, and RESULT_COHORT. The original research records are not edited.

Local verification: authored contrast pairing and cue deletion; actual 0118 anchor mapper with character-token fixture; full-width shape; native choice scoring and nonzero gradient to R with a tiny differentiable causal fixture; matched register fit; complete synthetic sealed selection/prediction/scoring and resume; Python answer-file guard. No real Qwen checkpoint, native tokenizer, CUDA, or live parent runtime artifacts were available in the patch-building environment. Runtime memory and convergence of the register fit remain unverified. Native label tokens must be distinct single-token A/B and Yes/No; the runtime fails explicitly otherwise.
