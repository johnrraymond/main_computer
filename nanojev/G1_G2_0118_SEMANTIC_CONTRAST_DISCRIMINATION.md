# G1/G2-0118 — Semantic contrast discrimination laboratory

## Research question

0117 did not establish generalized semantic discrimination. Its selected baseline was 0115; on the 28 fresh cases it achieved 73.85% G1 F1, 52.86% G2 F1, zero strict graphs and zero of 28 expected changes. High raw candidate coverage is not proof of separability. 0118 asks whether candidate-conditioned readouts of frozen Qwen representations distinguish closely competing bindings when meaning changes, and which evidence surfaces support the distinction.

## Protocol

* Reuse the sealed 0117 development and fresh caches. Verify their parent lineage, prediction locks, hashes and coordinate alignment. The previously exposed fresh set is now diagnostic, never a new independent test. Compare actual 0116 unfiltered logit margins of correct edges against same-relation, same-kind competing endpoint pairs using gold endpoint coordinates **only in retrospective diagnostics**. Fit G1 candidate-admission readouts per kind using 0117 fit cases; choose surface and threshold on its base-disjoint validation cases. Report old diagnostic and exposed 0117 scores without selecting on them. Untrained kinds retain baseline selections. Candidate admission may yield overlapping spans; it is not a complete decoder.
* New controlled G2 task: four public candidate anchors, a public relation type, and all four possible directed pairings. Two are correct; their identities are private answers. The prompt and anchors are identical up to the binding instruction for direct/reverse pairs. Two named-assignment and three order-assignment constructions, four relations, four variants (direct, reverse, and their paraphrases). 32 fit, 16 validation, 32 fresh. Structures 0/1 fit, 2 validation, 3/4 fresh; the structure split holds across relations. There are eight fresh relation-structure groups but only two independent fresh instruction constructions. This is a deliberately controlled conditional diagnostic, **not** an operational full-graph test. Anchor coverage is measured separately with real G1 readers run on prompt alone; all output candidates are counted but precision cannot be established from focal anchors.
* Readouts: geometry, endpoint lexical, text-only hashed suffix unigram/bigram and cooccurrence controls; existing reader scores; original embedding and layers 7/14/21/28, S layers 7/14/21/28. For each layer test endpoint-only, intervening tokens, suffix after all anchors, and all eligible prompt context. Also test original and S multilayer contextual combinations. Frozen layer normalization and seeded random projection (up to 128 dimensions) are common across arms. Candidate-conditioned contextual pooling includes interactions between endpoint features, contextual tokens and relative candidate positions. This is not exhaustive evidence about the entire representation space.
* Fit regularized linear ranking on signed true-vs-false within-row and within-column margins, separately by relation. Choose best eligible surface by validation reversal-pair correctness, case correctness, then pairwise accuracy. Test a small nonlinear head on that surface; retain it only if validation improves. A single validation construction is intentionally a narrow first-pass selection set. Compare all frozen readers and fitted linear surfaces on the sealed fresh cohort; the retrospective best is not the preselected winner. Geometry and lexical controls are reported but not eligible for semantic readout selection.
* Ablations: zero all states, shuffle token states coherently, embedding-only substitution, replace S with original states, zero suffix states. The suffix condition does not alter earlier endpoint states. Because Qwen is causal, earlier endpoint-only states cannot access later binding cues; observed residual differences may reflect tokenization/position or earlier text. Ablations are fixed inference interventions, not retrained controls. Text-only control is not a complete model of shortcuts. No claim of semantic understanding from ablation collapse alone.
* Fresh answers are stored separately and prediction is guarded by the inherited Python audit hook (not an OS sandbox). The author module is imported in prepare, not prediction. Selection, features and prediction shards are hash-bound; score opens fresh answers only after verifying the prediction lock. Historical checksums use the inherited restricted checksum-only subprocess. New fresh prompts are checked against known immediate ancestor cohorts, not all conceivable training data. No backbone, S, existing reader, canonical policy or production changes; no G3 unlock.

## Run

From nanojev in the existing Python environment:

```
& $py -u -m tools.nanojev_gamer_v1_semantic_contrast_0118 audit
& $py -u -m tools.nanojev_gamer_v1_semantic_contrast_0118 selftest
& $py -u -m tools.nanojev_gamer_v1_semantic_contrast_0118 run --device cuda
```

Requires the completed 0117 artifacts and existing 0116/0115 lineage. Run invokes prepare, develop, predict, score separately. Feature capture is resumable and validated against its seal and file hashes. Research output: `runtime/reports/gamer-v1/g1-g2-semantic-contrast-0118`. Review `g1-forensics.json`, `development.json`, `selection.json`, `result.json`, and `RESEARCH_RECORD.md`. No manual JSON download or copying required.

## Decision criteria

The primary evidence is correct ranking of both alternatives in both meanings and preservation under paraphrase, not F1 or a forced one-to-one decoder. Ties receive half credit in pairwise accuracy and no credit for strict case/reversal correctness. Compare to text and geometry controls; if contextual/suffix readout improves independently, test operational graph integration in a separate experiment. If no tested readout distinguishes the alternatives, investigate richer numerical surfaces or localized reader training; a negative projection/probe does not prove the backbone lacks information. If G1 candidate recall improves at the cost of many extra candidates, do not confuse this with a precision improvement. Integration intentionally gated, no full-graph promotion in this diagnostic.

## Local verification

CPU selftest on actual 0116 Reader class with synthetic weights/representations: public anchor mapping, 80 distinct cases, disjoint structures, ranking and tie metrics, linear/nonlinear fitting, suffix endpoint invariance, zero ablation, G1 admission, answer guard. Full prepare/select/predict/score synthetic evidence fixture also passed; this does not establish scientific performance. Live Qwen/LoRA, tokenizer alignment, real runtime checkpoints and CUDA remain to be verified on operator machine.
