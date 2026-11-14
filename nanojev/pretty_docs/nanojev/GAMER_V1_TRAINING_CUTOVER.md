# NanoJev Gamer v1 — governing training-system cutover

**Status (2026-11-03, revision 4): sparse-table factorization TEST NEXT; not yet run. Prompt-to-math bridge NOT IMPLEMENTED; Gamer v1 NOT RELEASED.**

## Immediate next experiment: numerical factor tables (revision 4)

**Before implementing large-scale prompt-to-math LoRA training, run the read-only sparse numerical-table factorization test specified in [`GAMER_V1_SPARSE_NUMERICAL_TABLE_TEST.md`](GAMER_V1_SPARSE_NUMERICAL_TABLE_TEST.md).** Attempt all 33 source-verified models and their supported objectives, starting with three **candidate**, not pre-certified, table families: action payoff factors, signed arithmetic factors and weighted outcome/transition factors. Use populated numeric cells and `–` for genuinely inapplicable fields; preserve names, signs, constraints and operation order. Reconstruct game mathematics exactly, compare to the source oracle and measure the true table/rule storage cost versus the naive representation. Report non-fitting games rather than inventing per-game hidden formulas. This test does not train LoRA or Wide, and its proposed table layout is not yet the final canonical math-prompt target.

**Critical invariant remains unchanged:** both learned branches process the same original *08 prompt. A populated table/math prompt is a potential Qwen text target, **never** an input to `to_numerical`.

## Architectural decision — interpret first, calculate second

Gamer v1 has two parallel supervised objectives sharing the SAME original *08 prompt as input:

```
                              *08 human-readable game situation
                                        |
                             frozen/promoted Qwen+LoRA
                                        |
                          original-prompt hidden activations
                                  /             \
                [Stage 1 LoRA supervision]  [Stage 2 to_numerical / Wide]
                math prompt text TARGET      numeric answer TARGET
                (uncomputed; diagnostic)    (oracle verified)

The generated math prompt is never fed into the numerical head.
```

**Stage 1's target is NOT the numerical answer.** It is an executable-equivalent canonical *math prompt*: the correct game, given state and parameters, mathematical objective or residual equation, operands and legal actions, and requested output semantics. Qwen learns to recognize and formulate the mathematics from the human-readable *08 narrative; it is not required to solve that arithmetic during Stage 1. Supervised target bytes may be used in the training completion but are NEVER present in an inference input. Stage 2 learns numerical prediction from the original *08 prompt activations produced by frozen Qwen+LoRA. It must NEVER read predicted or gold mathematical-prompt text. Evaluate translation errors and numerical errors separately on matched original prompts; the two heads are parallel outputs, not a chained text-to-number pipeline.

The existing C0 **Wide compressive numerical readout** is a numerical predictor with a narrow bottleneck and corrected/uncorrected readouts; it is not a proven general-purpose mathematical autoencoder. Its compatibility with *08-original-prompt-conditioned activations is a new engineering and experimental gate. Existing P0–P7 results and the 46/64 LoRA evaluation do not certify either stage of this new system.

## Game library and mathematical breadth

Use the **full game catalog as the curriculum registry**, not three hand-chosen generic games. The November 3 v6 inventory comprises **231 entries**: **33 source-verified executable game models** (14 matrix and 19 scalar), **196 catalog-only** and **2 provisional pilots**. All 33 verified models are eligible for Stage 0 candidate generation and initial Stage 1 training, subject to *task-level oracle, rule, parameter and narrative verification*. The other 198 are a governed expansion backlog, not supervised gold. A game being executable does not automatically make every conceivable objective, strategy or story form certified.

The existing V9/V10 game-math and v6 native-objective machinery must be inventoried and reused; it must not be displaced by a new three-task prompt generator. Game mathematical diversity includes matrix payoffs, incentive differences, equilibrium/ratios, sequential value, Bayesian information, allocation/constraints, timing, population, adversarial security and coalition-style computation **when verified executable objectives exist**. Taxonomy-wide families are not proof that a given catalog-only game has an executable oracle.

The earlier three classes (*matrix best response, mixed probability, scalar game*) are **output contracts**, NOT sufficient curriculum coverage. Train across every verified, renderable mathematical objective in the 33 models and certify per-game/per-objective competence. A release may declare a smaller supported game allowlist if justified by passing gates; it must accurately disclose which verified models remain training-only/uncertified. V1 is not blocked on building or mastering all 231 catalog entries.

## Zero-line as the curriculum coordinate

For each game objective, derive its actual mathematical balance equation from the verified source, not an invented universal payoff-difference formula. Define a signed *residual* `R_g(state, actions, parameters)` whose zero has the specified game-native meaning: payoff tie, no profitable deviation, binding constraint, indifference, equilibrium condition, zero marginal advantage, etc. Some tasks have multiple constraints or vectors of residuals; record all required conditions and the applicable decision rule. Never equate a numerically zero payoff with a zero decision advantage without proof.

Generate reproducible matched groups around each valid boundary: **negative / exact zero / positive**, nearest representable signed neighbors, increasingly distant residuals, large operands with small residuals, scale changes, natural-language paraphrases and ordinary far-from-zero examples. Preserve source validity when perturbing inputs (e.g., probability constraints, legal choices, feasible budgets). Record mathematical regime independent of wording and separate near-zero difficulty from operand magnitude. For non-integral values, use exact rational oracle values and a versioned epsilon/precision policy rather than a fake universal ±1.

The central Stage 1 hypothesis is that translation of diverse *08 stories into a shared canonical mathematical language transfers across games and numeric scales. Test that by cross-game and cross-form evaluation, not by assuming it is true.

## *08-level human-readable prompts from the beginning

Stage 1 operates on `*08` human-readable problem formulations (T08/HDB08-style), not the earlier direct numerical P0–P7 prompts with a narrative wrapper. The user-facing problem may require reading rules, conditions, irrelevant details and implicit relationships. It must provide sufficient information to derive the intended mathematical model; underdetermined tasks have an explicit `insufficient_information` or `unsupported` target rather than hallucinated operands.

Each source scenario has a source-verified canonical mathematical representation and multiple rendered natural-language prompts. Train on the human-readable -> canonical-math mapping from the first Stage 1 batch; use structured/easier renderings as auxiliaries, not as a substitute for *08. Keep equivalent renderings of the same scenario in the same partition. No answer or witness is present in the input.

The mathematical target is a **constrained, deterministic serialization** (see `GAMER_V1_STAGE_CONTRACTS.md` and `GAMER_V1_PROMPT_TO_MATH_CURRICULUM.md`), not an unconstrained explanation, a completed numerical answer, or an implicit model state. Stage 1 diagnostics must validate parsing, exact rule/operand/action extraction, semantic mathematical equivalence, target/answer leakage and cross-form consistency. A canonical text match alone is not enough; a narrative sounding correct is not proof of executable equivalence.

## Single-source cutover and status of prior experiments

These four documents and `GAMER_V1_CUTOVER_MANIFEST.json` are normative: `GAMER_V1_TRAINING_CUTOVER.md`, `GAMER_V1_STAGE_CONTRACTS.md`, `GAMER_V1_PROMPT_TO_MATH_CURRICULUM.md`, and `GAMER_V1_RELEASE_GATES.md`. Older T/HDB, numeric-terminal, HybridLink, C0, game transformation and zero-line SFT documentation remains valuable historical evidence and comparison/retention suites, not an alternative Gamer v1 launch plan. No old checkpoints or reports are overwritten.

The November 3 research seed at `runtime/reports/qwen-c0-adaptive-lora-v1/backbone_phase/best_adapter` trained on 1,502 **non-game-native numerical prompts** (rank-16, 2,000 steps) and reached **46/64 exact (71.875%)** on a small direct-number generation gate versus base 0/64 on that particular interface. The C0 Wide run acquired **0 of 5** tested magnitude bands at radius zero. Neither result measures *08 prompt -> canonical math translation. Reusing that adapter is an optional, fingerprinted A/B seed, never a claimed Gamer training continuation or mandatory default.

## Ordered implementation / training phases

1. **M0 documentation cutover:** this versioned specification; historical records remain unchanged apart from cutover banners.
2. **M1 game corpus + parser:** source-verified 33-model inventory; derive each valid residual and canonical objective; deterministic *08 paraphrases paired to exact canonical math; grouped splits; separately sealed certification pool; oracle/semantic replays. Reject source-invalid/underdetermined scenarios.
3. **M2 Stage 1 LoRA:** train only the Qwen translation adapter, all eligible verified games interleaved, balanced near/at/across zero and matched far cases; per-game/per-residual/per-form diagnostics; true resume state and immutable promotions; compare fresh Qwen versus imported historical LoRA seed.
4. **M3 Stage 1 certification:** demonstrate correct mathematical formulation of unseen game scenarios, especially exact zero and both signed neighbors at *08 wording; verify rule/operand/operator/action/constraint fidelity and cross-game retention. Do not promote solely because token loss fell or a 64-case numeric gate improved.
5. **M4 Stage 2 `to_numerical` / Wide:** freeze promoted LoRA; pin the interface `original *08 prompt -> frozen Qwen+LoRA activation -> Wide readout`. Train numerical targets from the source oracle while feeding ONLY original *08 prompts. Compare base and promoted LoRA activation quality on the same original prompts; preserve numeric regressor diagnostics and add typed decision output where required.
6. **M5 release certification:** evaluate both parallel paths on the SAME original *08 input: `*08 -> math text` and `*08 -> Qwen activations -> Wide -> typed result` for an explicitly declared supported game allowlist; certify error/retention/legality and package artifacts with hashes and reproducible probes.

Stage 1 FIRST. Stage 2 is not quietly run in the same training invocation before Stage 1 promotion. No generated mathematical text is an input to Stage 2. The game library is the training substrate and zero-line is the balanced difficulty coordinate; *08-to-math translation is Stage 1's **objective function**.

## Commands and instrumentation

The intended unified CLI is specified in `GAMER_V1_PROMPT_TO_MATH_CURRICULUM.md`. Its commands are **proposed interfaces only — NOT implemented in the November 3 snapshot**. Existing commands such as `tools.nanojev_qwen_c0_compressive_train --adapter ... --zero-line-hard-train` train a fresh C0 encoder from `--zero-line-init-dir`, not a new Stage 1 translation adapter and not a true resume. Do not describe them as Gamer training.

**Immediate engineering gate:** prove one full gold-free *08 -> serialized math -> parser -> oracle-equivalence smoke for each supported mathematical objective before starting the large Stage 1 LoRA campaign. Then implement and test Stage 1 training, its probes and exact resumability. No model release is implied by this documentation patch.

## Architectural invariant — shared original prompt (revision 3)

**MUST:** Both training paths are conditioned on the same original *08 human-readable prompt. Qwen LoRA is supervised to emit an English-readable explicit mathematical prompt; `to_numerical` / Wide is supervised against the numerical oracle target using hidden activations from the frozen Qwen+LoRA processing the **original *08 prompt**. The mathematical prompt is a textual target/diagnostic only; it is **NEVER** an input, intermediate preprocessor, hidden-state capture source, teacher-forced substitute, or inference dependency for `to_numerical`. Training and inference must assert the original-prompt input fingerprint for Wide. Fail closed on a math-prompt-conditioned Wide input.
