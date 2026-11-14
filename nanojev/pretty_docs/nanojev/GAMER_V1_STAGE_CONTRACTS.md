# NanoJev Gamer v1 — stage, data and inference contracts

**Normative direction:** the SAME original `*08 human-readable game prompt` feeds frozen/promoted Qwen+LoRA, with TWO outputs: (A) Qwen text decoding targets an explicit uncomputed mathematical prompt; (B) `to_numerical` / Wide reads original-prompt hidden activations and targets a verified numeric answer. **Math text is never the numerical head input.**

## Stage 0 — Game-native oracle and matched task pair

The source-owned executable oracle defines game version/rules, parameters, state, legal action labels, requested objective, valid balance residual(s), canonical mathematical expression and independently computed result. For November 3, v6 verifies **33 executable models (14 matrix + 19 scalar)**; **196 catalog-only + 2 provisional** are not ground-truth training cases. A model is eligible for a given output only if the **specific** objective and scenario are independently verified; do not manufacture unsupported mixed equilibria or add unstated rules.

Each task row must identify:

- `scenario_group_id`, `game_id`, `game_version`, `oracle_source_sha`, `objective_id`, `math_family`, `difficulty_id`, `seed`;
- exact rational/integer operands and units, full rule/state inputs and legal action labels, source-derived residual equation(s), zero/negative/positive regime, absolute residual and operand scale;
- `human_prompt` in *08 style **without oracle gold or hidden witnesses**, deterministic `math_prompt_target` that is itself **uncomputed**, and `numeric_target` plus any action mapping held in the offline label store;
- `task_kind` (`integer`, `rational`, `real`, `probability`, `action_choice`, `unsupported`, `insufficient_information`), source-defined precision and admissibility constraints;
- split assignment grouped by scenario BEFORE generating paraphrases, transformations or residual-neighbor variants where correlated examples would leak across partitions.

The `math_prompt_target` must be derived from the actual oracle math expression, **not from its already computed numerical answer**. A renderer must be reversible/semantically checked against the source state. Preserve source proof traces offline only; runtime must have none.

## Proposed readable mathematical target — representation choice pending table test

**Revision 4 supersedes the earlier illustrative JSON-only target as the presumed user-facing training format.** We will first test the three sparse numerical table families in [`GAMER_V1_SPARSE_NUMERICAL_TABLE_TEST.md`](GAMER_V1_SPARSE_NUMERICAL_TABLE_TEST.md). A successful table-backed target contains ordinary explanatory English plus numerically populated cells, clear mathematical operations and the question to compute, **without the calculated answer**. The deterministic oracle-owned record may retain internal typed metadata for checking; that metadata is **not a mandate for Qwen to output a new JSON/macrolanguage**.

Example, source Hawk–Dove matrix, opponent selects Hawk:

| Your action | Payoff against Hawk |
|---|---:|
| Hawk | -2 |
| Dove | 0 |

“Compare the two payoffs against Hawk and identify the better action. Keep the comparison unsolved.” The source matrix, action names, output semantics and operation choice are preserved. This display is illustrative only; the table-test must prove that the **full** model and any mixed-equilibrium objectives can be reconstructed from shared tables before choosing the final target.

**Parser and mathematical equivalence:** independently check game/action identity, exact operands/signs, required fields, units, operation ordering, source expression and supported objective. Validate math equivalence under source-approved parameter variations rather than accepting one accidental numeric match. Empty or dashed required cells are invalid. Zero is a value, never a dash. The experimental table format must be deterministic and machine-checkable **without requiring the model's output to be a proprietary symbolic DSL**. Handle `unsupported` and `insufficient_information` explicitly.

## Stage 1 — Prompt-to-prompt Qwen LoRA (the ONLY initial neural training stage)

**Input:** source-verified *08 human-readable game situation, as a deterministic prompt with game identity/rules when available; no answer. **Target:** verified, uncomputed mathematical game prompt in readable English plus explicit numerical tables (final presentation subject to the table experiment). **Loss:** supervised next-token cross-entropy on the canonical completion, with the human prompt as context; never supervise completion with the final numerical result as the target. **Trainable:** a separate rank/config-pinned Qwen LoRA; base Qwen immutable. **No Wide optimization in this stage.**

Use the full eligible 33-model executable library, grouped by real mathematical operations and game IDs. Include near-zero (−/0/+), both signs, large operands/near-zero residual, far examples, rule variations, parameter changes and *08-level natural language **from the beginning**. Structured examples can supplement this, but may not replace *08. Each mathematical boundary is source-derived: action tie, equilibrium residual, binding constraint or valid marginal advantage. Some models have multiple necessary residuals or no applicable scalar tie; never falsify a zero-line for curriculum convenience. Preserve at least a recorded baseline distribution of all eligible models during failure-focus weighting.

**Promotion evidence (after the table test):** parseable, source-equivalent mathematical prompts; correct game identity/rules; complete and correct operands/operators/action semantics; source-equivalence of mathematical target; accuracy by game × objective × residual sign/band × operand scale × prompt form, plus cross-game and historical retention. Evaluate held-out scenarios with *08 prose. A 46/64 direct-generated number score from earlier C0 backbone SFT is NOT evidence of this translation competence. Checkpoint adapter, tokenizer/base hashes, full source/task/split fingerprints, optimizer/scheduler/scaler, RNG and sampler state for genuine resume. `best_adapter` is promotion-selected only with an explicit ledger and full probe replay.

## Stage 2 — Direct numerical Wide head after Stage 1 promotion

**Input authority: the SAME original *08 human-readable prompt supplied to Stage 1.** The Stage-2 interface is **original *08 prompt -> frozen promoted Qwen+LoRA -> pinned hidden-state tap -> `to_numerical` / C0 Wide readout**. No gold or model-generated math prompt is substituted as input, during training, probes, or inference. The numeric target comes from the independent offline source oracle. Pin and test the original-prompt capture/hidden-tap contract; prior C0 caches are not automatically compatible.

The existing Wide readout is LayerNorm -> compressive MLP -> 16-dimensional bottleneck -> corrected signed-log scalar regression, plus earlier-layer uncorrected scalar regression. It is **not** a text decoder or a general purpose autoencoder. Preserve side-by-side corrected/uncorrected values, numeric MAE, median and signed error, exact zeros, tiny signed residuals and large operands. Exact integer tasks require strictly correct integer outputs (no arbitrary epsilon); rational/real/probability tasks require predeclared tolerances and range checks. Action-choice tasks require a verified, legal mapping and categorical or otherwise justified typed decision mechanism; floating-point rounding of an arbitrary action index is **not** accepted as proof of correctness.

**Two separately reported evaluations are mandatory:** (A) **translation accuracy**: original *08 prompt -> uncomputed mathematical text vs oracle-authoritative math target; (B) **numerical accuracy**: SAME original *08 prompt -> frozen Qwen+LoRA hidden activations -> `to_numerical` vs offline oracle numeric target. Also report joint case-level success; a math-translation failure must be visible even if the numerical head happens to answer correctly. No gold-math or predicted-math numerical inputs are permitted. Capture cache keys include base, LoRA, tokenizer, original *08 serialization, game bank, task split, and hidden tap hashes. Preserve optimizer/RNG/sampler and strictly increasing global step on true resume; a new `--zero-line-init-dir` is a fresh C0 run.

## Stage 3 — Gamer inference

1. Accept a supported game/version, human-readable *08 scenario and requested result (with observable information only).
2. Encode the **original *08 prompt** once with frozen promoted Qwen+LoRA and obtain the pinned hidden activation used by `to_numerical`. Qwen may independently decode an explicit mathematical explanation for interpretation/diagnostics; the math text is not fed back into the encoder for numerical prediction.
3. Compute the typed numerical output via Wide from the **original-prompt activation only**; never re-encode the generated or gold math prompt as the head input.
4. Validate type, legal action mapping, precision and probability/constraint bounds. Return value or explicit unsupported/invalid status. No oracle, numerical gold, hidden witness or training-only label store is reachable by the runtime path.

If Stage 1's mathematical formulation is wrong, numerical coincidence must not mask the error in its diagnostic ledger. Source-independent end-to-end correctness is a separate release gate.

## Non-negotiable lineage and retention

Maintain independent corpus fingerprints and train/eval manifests; grouped split integrity; T00–T08/HDB00–HDB08 and nine math branches as independent transfer/retention suites; full fixed-case and fresh-seed probes; Stage-1 translation and Stage-2 original-prompt numerical error at case granularity. Replay previously certified cases after repairs. Preserve best eligible checkpoints independently of latest, do not mutate historical checkpoints, and never call loading an adapter while resetting the Wide encoder a training resume.

## Architectural invariant — shared original prompt (revision 3)

**MUST:** Both training paths are conditioned on the same original *08 human-readable prompt. Qwen LoRA is supervised to emit an English-readable explicit mathematical prompt; `to_numerical` / Wide is supervised against the numerical oracle target using hidden activations from the frozen Qwen+LoRA processing the **original *08 prompt**. The mathematical prompt is a textual target/diagnostic only; it is **NEVER** an input, intermediate preprocessor, hidden-state capture source, teacher-forced substitute, or inference dependency for `to_numerical`. Training and inference must assert the original-prompt input fingerprint for Wide. Fail closed on a math-prompt-conditioned Wide input.
