# NanoJev Gamer v1 — stage promotion and release gates

**Status: NOT RELEASED.** The existing v6 game-native bank is proof-only; the November 3 research LoRA trained numerical P0–P7, not *08 -> mathematical prompt. No game-native translator or integrated original-*08-prompt Wide inference has yet been demonstrated.

## Governing acceptance chain

`Pre-Stage 0 table factorization test -> Stage 0 verified games + paired targets -> Stage 1 *08-to-math Qwen LoRA -> Stage 1 promotion -> Stage 2 original-*08-prompt-to-Wide -> Stage 2 promotion -> Stage 3 shared-input dual-head certification`. The math text is a Stage 1 target, never a Stage 2 input.

Stage 1 must be completed FIRST; do not force numerical-head progress to compensate for an incorrect language-to-mathematics translation. The full **33 verified executable models** are the initial eligible training curriculum, with source-verified objectives selected per model. The 196 catalog-only games and 2 provisional pilots remain unverified/not supported. The eventual release support list may be a smaller certified subset, disclosed truthfully; the user-visible release cannot claim all trained games were mastered.

## Immediate pre-training gate — sparse numerical table factorization (revision 4)

Before the Stage 0 paired math-target bridge is finalized, execute [`GAMER_V1_SPARSE_NUMERICAL_TABLE_TEST.md`](GAMER_V1_SPARSE_NUMERICAL_TABLE_TEST.md). Attempt all 33 verified executable models and their source-approved objectives with numeric populated action-payoff, signed-calculation and weighted-outcome table families. **Do not assume that three families suffice**. Every represented objective must reconstruct with zero source-oracle mismatch, correct action/constraint/probability semantics and no hidden game-specific formula. Report unfitted objectives and the true compression cost. No neural training is part of this test; a passing table test precedes the broad prompt-pair generation and Stage 1 LoRA campaign.

## Gates — source / interpretation / calculation / composition

| Stage / gate | Required evidence | Passing condition |
|---|---|---|
| Stage 0 source | 33-model inventory, objective proofs, game/version/oracle hashes | 100% selected task gold and canonical expressions replay under source authority; reject unsupported game/math targets |
| Stage 0 paired corpus | Source scenario -> *08 human text -> canonical uncomputed expression; separately stored numeric gold | Rendered prompt semantically consistent with source, no target/gold/witness in input; all correlated renderings grouped before splitting |
| Stage 0 balance | Per-game residual derivations, mathematical meaning of zero, valid signed neighbors | Negative/exact-zero/positive and far cases wherever mathematically applicable; no fabricated boundaries/invalid scenarios |
| Stage 1 parser | Serialized model math prompts, invalid/unsupported rate, deterministic parser/schema | Every supported output follows schema or explicitly signals valid insufficient/unsupported; no silent repairs |
| Stage 1 semantics | Game/rule/operand/operator/constraint/action checks and source expression equivalence | Predeclared per-game/objective/*08/zero-line gates; mathematical equivalence, not merely text or direct numeric generation |
| Stage 1 retention | Fixed prior successful cases plus fresh scenarios, game-balanced sampler ledger | Previously certified translation objectives preserved under declared retention thresholds; no hidden weak game in aggregates |
| Stage 1 promotion | LoRA/base/tokenizer/corpus hashes, independent evaluation, true resume and best eligible checkpoint | Stage 1 *08-to-math translator promoted separately; no reliance on C0 46/64 numeric baseline |
| Stage 2 compatibility | Pinned ORIGINAL *08 prompt serialization, frozen Qwen+LoRA hidden-state tap, capture cache keys | Proven original *08 prompt -> frozen Qwen+LoRA features -> Wide train/eval equivalence; reject math-text-conditioned captures and stale reuse |
| Stage 2 numerical | Original-*08-conditioned predictions with exact, MAE, median and signed errors, zero/sign probes | Typed accuracy: strict exact for integers, predeclared numeric tolerance for real/rational, legal bounds for probabilities |
| Stage 2 decisions | Game-specific action mappings and legality validation | ≥95% exact for declared integer best-response tasks, 100% legal actions; no unvalidated rounding of general action IDs |
| Stage 3 pipeline | Entire fresh *08 input -> two parallel outputs: math text AND original-prompt Wide numeric result | ≥95% per declared supported family/form/band with task-specific target policy; separately report Stage 1 error propagation |
| Worst-cell and uncertainty | Per-game × objective × sign/scale × language-form confusion/errors | No masked zero-success supported cells; denominator, uncertainty and regression logs included |
| Reproducibility | Full checkpoint and optimizer/RNG/sampler lineage, interrupted-resume smoke | Resumed steps strictly advance; immutable previous checkpoints; no reinitialization presented as continuation |
| Product | Support allowlist, input/output contracts, base/LoRA/Wide hashes, no-oracle runtime tests | Callable Gamer and evidence bundle; unsupported states rejected; release manifest moved from `not_released` only after all selected gates pass |

The **95%** targets are prospective and apply to declared *supported* inference outputs at the specified evaluation cell granularity, with typed tolerances for non-integer tasks. They are **not** a substitute for complete mathematical-model correctness in Stage 1: its separate translation gates and required semantic checks must be set before training. Do not use a 64-case aggregate as proof of broad game capability. Report denominator, independent scenarios, confidence bounds and cross-seed variation.

## Training-to-release sequence

- **M0 — documentation and table-test cutover (this archive):** normative plan + test specification; sparse table reconstruction is **not yet run**; model is *not trained/released* by these files.
- **M1 — v6 table experiment, then corpus bridge:** first validate the three candidate sparse numerical table families on all 33 verified models (or report non-fitting games). Only then exercise source-derived 33-model objectives, zero-line definitions, paired *08/canonical-math prompts, scenario-split leakage tests, semantic equivalence proof and diagnostic fixtures.
- **M2 — Qwen LoRA prompt-to-math:** train one multi-game adapter, record all-game coverage, exact resume, independent Stage 1 diagnostic/probe, promote verified translator. No Wide training in M2.
- **M3 — Wide computation:** capture activations of the **original *08 prompts** using the frozen Stage-1 model; train/test numerical readout and action mapping against independent numeric gold. Do NOT encode gold or predicted math text for the numeric head.
- **M4 — release suite:** gold-free *08 whole-system tests, cross-game retention/transfer, checksum bundle, supported-game list and honest product claims.

### Official commands

Game oracle/proof checks currently exist, but a consolidated `tools.nanojev_gamer_v1` training command, *08-paired dataset bridge, canonical schema/parser, true Stage 1 resume, game-typed Wide head and end-to-end release probe are **not yet implemented** in the November 3 snapshot. The exact proposed shell interface appears in `GAMER_V1_PROMPT_TO_MATH_CURRICULUM.md`; never pass its flags to existing C0 tools and report a Gamer result. Existing `tools.nanojev_game_math_smoke` remains source proof only.

**Stop:** no large LoRA training before the source-pair/parsing/equivalence smoke works; no Stage 2 before a compatible Stage-1 promotion; no v1 shipment while the shared-*08-input translation and numerical output paths lack certified evidence. Preserve all historical T/HDB/C0 checkpoints intact.

## Architectural invariant — shared original prompt (revision 3)

**MUST:** Both training paths are conditioned on the same original *08 human-readable prompt. Qwen LoRA is supervised to emit an English-readable explicit mathematical prompt; `to_numerical` / Wide is supervised against the numerical oracle target using hidden activations from the frozen Qwen+LoRA processing the **original *08 prompt**. The mathematical prompt is a textual target/diagnostic only; it is **NEVER** an input, intermediate preprocessor, hidden-state capture source, teacher-forced substitute, or inference dependency for `to_numerical`. Training and inference must assert the original-prompt input fingerprint for Wide. Fail closed on a math-prompt-conditioned Wide input.
