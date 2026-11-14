# NanoJev: Numerical Completion and Cumulative Mathematical Competency Frontier

**Design specification:** v0.1 / 2026-10-30  
**Location:** `pretty_docs/nanojev/NUMERICAL_COMPLETION_AND_MATH_FRONTIER_TRAINING_PLAN.md`  
**Status:** Proposed integration and training scheme. This is **not** an assertion that the complete pipeline is implemented, trained, or certified.

## 0. Decision and objective

**Competency Zero is a complete numeric problem.** A packet containing nothing but a canonical numerical literal, for example `PROBLEM: 0` or `PROBLEM: -12`, is already a completed mathematical state. We do not teach the language model to write `ANSWER:` as a prerequisite for recognizing, terminating, or reading that state.

The first proof of the overall architecture is that it can **memorize this competency exactly**, including the *meaning* of completion and the correct value, and retain it while learning arithmetic reductions. Only then expand into an open, multidimensional mathematical frontier.

The intended end-to-end contract is:

```text
Input: PROBLEM: 0
    -> whole-payload numerical recognition
    -> completion (no arithmetic recurrence required)
    -> exact signed-integer readout Z = 0
    -> result { status: COMPLETE, kind: INTEGER, Z: 0 }

Input: PROBLEM: 10+(-10)
    -> NOT a numerical terminal
    -> Qwen numerical representation / HybridLink reduction
    -> optional corrective glass when necessary
    -> a terminal numerical state representing zero
    -> learned completion detection
    -> exact signed-integer readout Z = 0
```

The textual packet is a convenient *transport representation*. Once a typed numerical terminal is available, no `PROBLEM:`/`ANSWER:` text needs to be generated for completion itself. A UI may later format the result independently.

### Non-negotiable constraints

1. **No inference oracle.** Expected answers, teacher vectors, target token IDs, deck membership, and source-prompt IDs may guide training or offline grading; none may flow to runtime recognition, HybridLink feedback, glass routing, or mathematical readout.
2. **No artificial answer-prefix requirement.** `ANSWER:` is neither a mathematical skill nor a prerequisite for stopping. Support old checkpoints as explicitly labeled *legacy* experiments, not as compatible certificates.
3. **Train components against their own failure modes.** Detector correctness, stopping depth, numerical correctness, exact readout, and optional text formatting are separate metrics.
4. **Exact acquired memory.** Every mastered example must remain correct after accepted updates. Sampling is for diagnostics and triage; certification requires a complete audit of the explicitly defined finite acquired corpus.
5. **Funnel down.** Decompose large and worded quantities into small exact coefficients, scale/exponent, and unit/context; compute exactly and recompose without losing sign, order, scale, or units.
6. **Adaptive depth.** Continue training the current glass while sustainable correction is possible. Add a *new zero-initialized residual glass* only on reproducible fix-A/break-B conflicts that cannot be jointly recovered in the existing corrective structure.
7. **Checkpoint isolation.** Every changed terminal contract, detector label definition, glass input geometry, and numerical readout representation creates a new versioned run lineage. Never silently overwrite or recertify older experiments.

## 1. What exists and what does not (source-derived inventory)

The available code snapshot and recent numeric-terminal patch support the following **observations**; the proposed integrated system below is not already present.

| Component / file | Observed implementation | Needed integration |
|---|---|---|
| `tools/nanojev_qwen_lm_radius_ladder.py` (numeric-terminal patch) | Teaches one-hop `PROBLEM: expression` -> `PROBLEM: reduced expression` or `PROBLEM: integer`; its deterministic `numerical_completion` recognizes a canonical whole-packet integer. Optimizes Qwen q/v LoRA, using the frozen vocabulary LM head for token loss. | Provide typed terminal states and aligned training data to the HybridLink detector and numerical readout; the ladder currently does **not** train or run HybridLink or a glass. |
| `tools/nanojev_hybridlink_frozen_detector_cutover.py` | Trains a detector and real/complex numerical sidecars on frozen Qwen hidden states. Its `is_complete_state` currently treats `ANSWER:` as complete and `PROBLEM:` as incomplete, including numeric `PROBLEM:` packets. | Replace the old completion-label contract, retrain/revalidate the detector, and include terminal numeric `PROBLEM:` positives. |
| `tools/nanojev_hybridlink_latent_loop_cutover.py` | Runs a headless learned HybridLink latent-feedback loop and detector-based stopping; its chain reconstruction expects a terminal `ANSWER:`. | Accept typed numeric terminals and initial-state completion; no hidden `ANSWER:` dependency. |
| `tools/nanojev_hybridlink_t00_curriculum_repair.py` | Trains latent feedback against T00 and deeper completed chains with explicit stop-depth gates, using the older chain contract. | Port the training/evaluation chain contracts and remove prefix-conditioned stop targets. |
| `tools/nanojev_numeric_complex_contract.py` | Provides learned *continuous* direct and complex numerical estimates (`direct_number`, `complex_number`) through an invertible asinh/log-radius encoding, plus agreement diagnostics. | Do **not** treat approximate or mutually agreeing real outputs as an exact integer `Z` certificate; integrate an exact integer readout with separate verification. |
| `tools/nanojev_hybridlink_headless_t00.py` | Contains an earlier sign/digit numerical head and a distinct action head, useful as an exact-integer readout design precedent. | Reuse or redesign deliberately for the chosen integer range, and eliminate duplicate/conflicting completion responsibilities. |
| `tools/nanojev_qwen_lm_deformation_smoke.py`, `_refine.py` and the newer `tools/nanojev_qwen_lm_failure_mode_glass.py` patch | Correct frozen-Qwen hidden representations *before the frozen vocabulary LM head*, with multi-layer activation-keyed residuals in the failure-mode experiment. | Decide and implement the **actual glass attachment to HybridLink or its readout**. An LM-head glass does not automatically correct a headless latent-feedback system. |
| `tools/nanojev_qwen_scale_transfer_smoke.py` and `tools/nanojev_qwen_scale_transfer_answer_prefix_probe.py` | Diagnostic tests for worded magnitudes, conversions, symbolic scales, and coordinates; not training. | Graduate this deck into later acquisition only after Competency Zero and low-range exact retention. |

**Important compatibility finding:** The numeric-terminal ladder's `PROBLEM: 0` completion rule conflicts with the old learned detector's `ANSWER:`-positive labels **and** the old latent-loop chain termination requirement. These are concrete engineering tasks, not evidence of mathematical saturation.

**Meaning of `Z`:** Here `Z` means the exact signed integer answer, `Z ∈ ℤ`. In the existing numerical-complex contract, lowercase `z` denotes a *complex geometric encoding* whose radius encodes a real number. These are **different quantities**; the geometric `z` and the requested exact integer `Z` must not be conflated.

## 2. One canonical runtime and training contract

### 2.1 State representation

The proposed internal record is a **typed state**, not a mandatory generated answer string:

```text
MathState:
    source_kind: LITERAL | EXPRESSION | WORDED | SYMBOLIC | OTHER
    numerical_terminal: bool
    completion_status: COMPLETE | CONTINUE | STALLED | ABSTAIN
    numerical_kind: INTEGER | RATIONAL | DECIMAL | SCALED | SYMBOLIC | NONE
    Z: exact signed integer | null
    representation: canonical integer / coefficient+scale / symbolic term | null
    qwen_state: actual frozen-Qwen activation(s) | null
    hybridlink_state: actual generated latent activation | null
    detector_score: observed score | null
    glass_trace: applied layer/mode and correction norm | null
```

`Z` is defined only if `numerical_kind == INTEGER` and the result is exact. For fractions, worded scales, or Graham's number, retain a typed exact alternative instead of coercing it into a float or inventing an integer decimal expansion.

**Runtime inputs allowed:** input string/tokenization, actual Qwen activations, actual generated HybridLink states, learned model weights, registered correction parameters, and deterministic syntax/format rules. **Training-only inputs:** gold reduction steps, true terminal status, true `Z`, intermediate teacher states, source/target token sequences, and regression deck identifiers.

### 2.2 Fast path versus learned path

A deterministic whole-payload numeric-literal check can establish a **trusted positive fast path**. Separately, the *learned* `is_numerical` detector must acquire completion recognition from actual states, including states produced by HybridLink recurrence. The rule must not be counted as proof of learned competence.

```text
parse whole problem
  |-- exact integer literal? --> COMPLETE, Z = parsed integer
  |                              (zero recurrent steps, no answer-label generation)
  |
  `-- unfinished expression --> encode with frozen Qwen
                                   |
                                   v
                             HybridLink latent state
                                   |
                         [correction glass if needed]
                                   |
                         is_numerical / completion detector
                              |                 |
                         incomplete         complete
                              |                 |
                         next recurrence     exact Z readout
                              |                 |
                              `--- repeat     verified result
```

The detector should measure **whether the entire current state represents a completed numerical result**, not whether any digit occurs in the question, whether the state is unchanged, or whether the LM head is ready to emit `ANSWER:`. Stalling and completion are different outcomes. A positive detector result **never substitutes** for checking the head's numerical output.

### 2.3 Competing mathematical routes and arbitration

There may be multiple means to produce a candidate numerical result: immediate literal recognition; learned Qwen reduction; HybridLink latent computation; a memorization shortcut; or a corrected activation from one or more glasses. The controller must not silently select an incorrect candidate just because its detector fired first. Record the source, completion status, readout, and certification scope of each candidate. Treat conflicts and unsupported results as unresolved until an authorized deterministic check or successfully certified route resolves them. No inference-time gold label may arbitrate these candidates.

## 3. Training schemes and parameter ownership

| Scheme | Trainable parameters | Frozen parameters | Training supervision | End-to-end acceptance |
|---|---|---|---|---|
| **S0: literal-terminal baseline** | None for deterministic parser; **learned detector** in S1 | Existing Qwen/HybridLink/glasses | Canonical positive/negative terminal packets | `PROBLEM: 0`, `-12`, `123` complete; `10+(-10)`, `x+1`, worded scales do not falsely complete |
| **S1: learned `is_numerical` detector** | Completion-detector weights; optionally a distinct input adapter | Qwen acquisition checkpoint; separately controlled HybridLink | True terminal labels on numerical `PROBLEM:` states versus nonterminal expressions; actual generated latent examples; adversarial near-terminals | Perfect known-case detection, **zero early false stops** on acquired chains; detect before any unnecessary recurrence on literal cases |
| **S2: HybridLink numeric-state acquisition** | HybridLink link/feedback weights | Qwen, prior certified detector during isolated link training, prior glasses | Ordered one-hop reductions and full generated rollouts, numeric terminal labels; teacher states only during training | Correct stopping step on whole chains; zero false stops, no oracle or teacher vectors at inference |
| **S3: exact `Z` readout** | Dedicated integer sign-and-digits / chunk readout head and optional adapter | Qwen + certified link/detector during isolated readout training | Exact integer labels at terminal states, with explicit sign and place-value targets | Exact `Z`, sign and digits, including zero; no near-enough float, no incorrect integer rounding |
| **S4: glass-only corrective memorization** | Current zero-default activation-conditioned residual glass | Qwen base, Qwen LoRA acquisition checkpoint, and already certified upstream modules unless explicitly staged for isolation | Harvest actual incorrect greedy/latent cases; target state or output loss during training only | Every acquired failure corrected **and** every previously acquired case retained; no runtime oracle |
| **S5: joint integration / calibration** | Only declared small boundary adapters or the current glass, in explicit isolated phases | Certified components by default | Full-chain failure traces; component-specific labels and losses | End-to-end `PROBLEM: #` and reduction-to-`Z` with correct completion, exact value and retention |
| **S6: expanding competency frontier** | Current approved corrective parameters (and, in a separate Qwen acquisition campaign, LoRA before re-freeze) | Certified historical snapshots; all prior glass layers after escalation | New exact mathematical cases plus cumulative replay | Every acquired competence retained; expand only after explicit exact certification |

### 3.1 S0/S1: fix the `PROBLEM: #` family first

**Positive examples:** `PROBLEM: 0`, `PROBLEM: -1`, `PROBLEM: 7`, `PROBLEM: 1024`. **Negative examples:** `PROBLEM: 0+0`, `PROBLEM: 1+(-1)`, `PROBLEM: (0)`, `PROBLEM: x=0`, `PROBLEM: 10 trillion`, multi-line or malformed packets. Expansion to decimal/rational literal terminals is a **versioned grammar extension**, not an unreviewed change in the integer contract.

Training must include (a) lexical packet classification, (b) learned discrimination on frozen Qwen features, and (c) learned discrimination on **actual** post-HybridLink states. For each, report true positives, false positives, misses, and whether an immediate input literal was improperly forced through recurrence. Balance examples so that the detector cannot solve the task by matching `ANSWER:` or detecting digits anywhere.

**Prioritization:** Overweight this family during early corrective acquisition until complete; retain all nonterminal negatives during every detector update. A low scalar loss or high aggregate accuracy does **not** certify no false stops.

**Implementation prerequisite:** replace `is_complete_state` semantics and all `ANSWER:`-terminal chain assumptions with numeric-terminal supervision; keep prior models and results in legacy versioned directories.

### 3.2 S2: HybridLink receives the terminal contract

For `PROBLEM: 0`, stop immediately and expose `Z=0`. For `PROBLEM: 10+(-10)`, the first state must be *incomplete*, HybridLink or Qwen must make a genuine reduction, and completion occurs only after a numeric terminal is reached. At each step store actual generated states, detector decision, and final readout; do not feed the gold intermediate to the rollout.

Use a **T00-first suffix curriculum** followed by T01, T02, … and independently measured signed radii. Distinguish one-hop next-state correctness from **complete-chain** correctness. Generate novel deterministic examples for verification and reuse old failure decks for retention; avoid fixed DEV holdouts as a substitute for enumerating the acquired corpus. Latent feedback must beat an ablation/zero-latent control on actual reductions rather than merely match memorized teacher-state embeddings.

### 3.3 S3: exact numerical `Z` extraction

A correct stop is not a correct value. Train the numerical head on **real terminal activations**, not only textual teacher targets. Compare at least two readouts if available: the existing direct/complex *continuous diagnostic* and a deliberately **discrete exact integer head** (for example sign `{-1,0,+1}` plus place-value digits or bounded chunks). Validate zero, negatives, carry/borrow, leading zeros, sign convention, and all complete integer cases. The continuous branch may help shape features or detect disagreement, but agreement/near-integer estimates alone cannot certify exactness.

Explicitly record `Z_expected` **only in training/evaluation data** and `Z_predicted` in runtime traces. Require `Z_predicted == Z_expected` as integers at every acquired case. Larger-than-bounded integers need an extended exact representation—chunks, coefficients/exponents, or symbolic values—rather than silent overflow or conversion to binary floating point.

### 3.4 S4: glass correction, with a real attachment contract

Current LM-head glass corrections add a learned hidden-vector delta **before Qwen's frozen vocabulary LM head**. Headless HybridLink instead consumes/produces latent vectors and has a separate numerical head. We therefore must **choose and instrument** one or more legitimate correction ports before training; a glass trained at the LM head is not automatically a HybridLink glass.

Candidate ports (compare empirically, do not assume interchangeability):

- **Qwen-to-HybridLink boundary:** a glass corrects the frozen Qwen hidden representation before entering the link.
- **HybridLink-state boundary:** a glass corrects a generated latent state immediately before completion detection and/or subsequent feedback.
- **Terminal-to-math-head boundary:** a glass corrects the terminal latent state before the exact `Z` head.
- **Legacy vocabulary-head boundary:** the existing glass corrects the Qwen token-decoding path; valuable for LM generation diagnostics, **not** proof of headless completion.

Each port needs an explicit input tensor, consumer, dimensions, gate/activation rule, gradient path, checksum-protected frozen dependencies, and per-component causal ablation. **Routing inputs must be actual activations** (possibly multiple frozen Qwen layers), never answer labels, predicted-gold deltas, prompt IDs, or external gold scores. Training may use target output error and backpropagate *through frozen consumers* into glass parameters only.

Start with one zero-initialized glass. Correct known low-range failures fully before expanding. Re-audit all previously correct cases after each accepted update. Compare against no-glass and zeroed-glass controls; report mode activation counts, matched and unmatched failures, net fixes, and new regressions. Do **not** automatically introduce another glass because there are still errors.

### 3.5 S5: integrated validation and responsibility attribution

For each problem, record:

```text
case_id; input; contract_version; expected_Z (offline only);
preflight_terminal; qwen_state_hash; hybridlink_steps;
completion_probability; completion_step; false_stop;
glass_port; glass_layer; activation_mode; correction_norm;
readout_kind; predicted_Z; value_exact;
component_failure_class; retention_set; checkpoint_lineage
```

Assign failures to **input classification**, **reduction**, **completion timing**, **glass recognition/correction**, **exact readout**, or **unsupported format**, not a single vague “math wrong” category. If the detector correctly stops but readout is wrong, train/readjust the **readout**; do not blindly retrain the detector. If readout is correct but no completion signal fires, train the **detector/termination bridge**. Verify the final end-to-end result, not just individual head accuracies.

## 4. Adaptive acquisition and glass saturation controller

The controller owns a **cumulative acquired-case registry** and a causal **interference graph**. Nodes are exact cases or independently verified transformation competencies; directed edges record a reproducible *fix A -> break B* transition, including seed, checkpoint hashes, and attempted recovery.

For each correction candidate:

1. Load the last **certified** checkpoint and current active glass.
2. Evaluate the current failure frontier and complete acquired-case regression set.
3. Train the **responsible component** on actual failures with extra priority for `PROBLEM: #`, zero identities, sign, and termination.
4. Replay every known acquired case and record newly correct, newly wrong, and unchanged cases. Use all cases to **certify**, not just a sample.
5. If regressions occur, run targeted recovery and joint A/B replay. If both remain correct, keep the same glass and continue.
6. If alternating repairs **reproducibly** cannot retain both, record an interference edge; try correction-mode separation or other capacity adjustment *within the existing glass* before stacking.
7. **Escalate only when demonstrated interference persists.** Freeze the successful earlier glass, append a zero-initialized residual glass, and train it to resolve the conflict. The candidate layer is accepted only after full retention and observed improvement; otherwise revert to the certified stack.
8. Continue frontier expansion. No fixed epoch count, no generic 95% accuracy threshold, and no fixed “number of failures” serves as a layer-creation trigger.

**Promotion invariant:** for finite explicitly acquired corpus `M_n`, require `forall x in M_n: exact_end_to_end(x) == true` before accepting `M_{n+1}`. This is an empirical finite-domain certification—not a proof of universal generalization.

**Budget controls:** bounded retry budgets can stop an experiment to report **INCOMPLETE** or **INTERFERENCE_SUSPECTED**, but must not silently promote a checkpoint or automatically add glass depth. A genuine conflict is established from **repeated evidence**, not merely a timeout.

## 5. Frontier beyond Competency Zero

Treat the mathematical curriculum as a **dependency graph of distinct competencies**, with separate acquisition radius, retention registry, and failure attribution. Do not block unrelated mastered branches simply because another branch has an open frontier. Do not promote a composed competency until its own end-to-end test passes.

| Frontier branch | Initial exact skill | Outward expansion | Compositional use |
|---|---|---|---|
| Literal `Z` and completion | Integer literal -> immediate completion and exact signed `Z` | Larger signed integers; versioned rational/decimal literals | All other branches' termination |
| Zero-line and signed arithmetic | Cancellation, identity, one-hop addition | Larger operands, longer reduction chains, variable residual radii | Multi-step computation |
| Place value and worded magnitudes | Hundred, thousand, million; coefficient + `10^k` | Billion, trillion, named higher scales, arbitrary exponent notation | Large-number language -> precise computation |
| Arithmetic operators | Addition and inverse subtraction | Multiplication, exact division, modulo, precedence | Algebra and resource constraints |
| Fractions, proportions, units | Exact rational simplification and dimensional identity | Percentages, rates, mixed units, dimension checking | Quantitative reasoning |
| Ordering and algebra | Equality, inequalities, substitution | Linear equations, systems, constraints | Decisions and symbolic reasoning |
| Logic, sets, and graphs | Boolean identities and membership | Implication, quantifiers, graph reachability, combinatorics | Structured general reasoning |
| Coordinates and geometry | Signed coordinate readout and displacement | Distance, transforms, dimensional coordinates | Spatial reasoning |
| Probability and uncertainty | Exact finite probabilities, fractions | Conditional probability, expectation, calibrated uncertainty | Reasoning with incomplete information |
| Symbolic extreme scale | `10^1000`, named constants as symbols | Graham's number identities, recursive notations, user-defined scale vocabulary | Correct representation without impossible expansions |

The **funnel-down rule** applies to every compatible branch: separate a manageable coefficient, a magnitude/order and units/context, compute on exact primitives, and recompose without discarding metadata. Examples: `10 trillion - 3 trillion = (10-3)*10^12`; `10 trillion + 500 billion = (10 + 0.5)*10^12`. Do not force unknown coined magnitude words into a numeric meaning without a definition.

After primitive acquisition, add **composition tests** whose full solution was not memorized as a single item. Track `primitive known / composition failed` as a separate failure class; successful primitive memorization is a prerequisite but not a guarantee of general reasoning.

## 6. Exact gates, probes, and known experimental baselines

### Gate A — Competency Zero (must be first)

- All acquired canonical integer literal packets immediately complete, including `PROBLEM: 0` and signed variants.
- No false completion on unfinished expressions, including numeric-looking interiors (`0+0`, `1+(-1)`, nonterminal intermediate states).
- Learned detector must succeed on actual latent Qwen/HybridLink states **independently of** the deterministic fast path.
- Exact head returns `Z=0`, and correct signed values for all acquired literals.
- No `ANSWER:` target, generated gold, or supplied answer prefix is required at inference.

### Gate B — Numerical reductions through the low-range acquired corpus

- New numeric-terminal contract tested from fresh, compatible checkpoints; legacy `ANSWER:` certificates cannot be imported unchanged.
- Correct *one-hop* transition, correct *full-chain* completion and exact `Z`, with zero early/late stops on the acquired corpus.
- Full exhaustive T00 where finite and feasible. T01+ finite deterministic acquired decks audited completely, with seeded Monte Carlo probes exploring beyond them. Mark untested domains **OPEN**, not certified.
- Harvest all actual numeric-contract failures before glass-only correction; **the old 54 errors are a historical `ANSWER:`-contract inventory, not automatically the new inventory**.
- Zero regressions on the entire previously mastered corpus before certification.

### Gate C — Glass memorization proof

- Freeze and hash the selected Qwen backbone/LoRA, HybridLink, detector and head according to the chosen corrective-port training phase.
- Force the current glass to correct every acquired failure; verify the same cases from **unassisted runtime inputs**.
- Re-run full end-to-end retention; no regressions. If an impossible-to-resolve correction conflict emerges, demonstrate it reproducibly and only then test one new residual glass.
- Show a causal improvement over disabled-glass control; a correction parameter that never activates does not constitute learned competence.

### Gate D — First outward transfer

- Worded-number/scale deck begins with conversions at hundreds and thousands, progresses through millions/billions/trillions, then symbolic exponents, unit constraints, coordinates, and defined extreme magnitudes.
- Compare raw and format-conditioned diagnostics **only as diagnostics**, not as a new mathematical stopping requirement.
- Old measured 112-case smoke: **9/112** numerically normalized for frozen Qwen alone and **9/112** with existing refined LM glass; `ANSWER:`-prefixed diagnostic **23/112** both with and without that glass. These are *untrained transfer baselines*, not a corrective plateau.
- Broaden one competency at a time with exact retained acquisition, then test composition.

## 7. Implementation work packages (dependency order)

| ID | Deliverable | Source impact | Required test/proof |
|---|---|---|---|
| **P0** | Single written numeric-terminal/`Z` state schema, versioned labels and controller tracing | Shared contract module and fixtures | Typed `PROBLEM: 0` / `10+(-10)` / malformed-input tests; no oracle in inference |
| **P1** | Refit learned `is_numerical` supervision from `ANSWER:` to numeric terminal | Frozen detector cutover and caches/checkpoints | Positive and negative discrimination, no early false stops; stale checkpoint rejection |
| **P2** | Update HybridLink chain builder, stopping and zero-step initial terminal check | Latent loop and T00 curriculum repair | T00 and T01+ full generated rollouts; no text ANSWER dependency; ablation comparison |
| **P3** | Exact signed integer `Z` head and export contract | Numerical head / sign-and-digit decoder | `Z` exact for acquired bounded range; zero and sign; float-approximation cannot certify |
| **P4** | Explicit correction ports; failure harvesting, per-port glass training and causal audits | Qwen/HLink adapters, glass system | Before/after traces, frozen weight hashes, zero-regression replay |
| **P5** | Adaptive interference graph and zero-initialized glass stacking transaction | Controller, checkpoint registry | Reproducible A/B conflict replay; rollback if new layer damages retention |
| **P6** | Frontier registry + worded magnitudes and other mathematical families | Curriculum, exact verifiers, reporting | Acquired finite-domain 100% audits; open frontier declared separately |
| **P7** | End-to-end public smoke + report | Integration runner | One invocation reports terminal, stop depth, Z, correction causality, retained competencies, version hashes |

**Execution ordering:** implement/verify P0–P3 for `PROBLEM: #` **before** claiming an end-to-end memorization mechanism. P4 proves error correction and retention. P5 is activated by observed conflict, not a calendar. P6/P7 expand and operationalize the frontier.

## 8. Checkpoint lineage and report schema

Never modify the current `qwen_lm_zero_line_fork_v1` legacy experiment or silently re-use its training/certification. The newer `qwen_lm_numeric_terminal_v1` is a **separate campaign**; a certified legacy T00 seed may contribute weights through the explicit numeric bootstrap, never legacy certificates. The old LM glass experiment and its 54-case failure-mode patch likewise remain separate until actual numeric-contract reconciling and attachment-port testing are complete.

Every accepted checkpoint/report should include:

```json
{
  "contract_version": "numeric-terminal-v1",
  "parent_weight_hashes": {"qwen": "...", "hybridlink": "...", "detector": "...", "numeric_head": "..."},
  "glass_stack": [{"index": 0, "attachment_port": "...", "checksum": "..."}],
  "competency_registry_sha256": "...",
  "terminal_literal": {"tested": 0, "passed": 0, "false_stops": 0},
  "full_chain": {"tested": 0, "passed": 0, "early_stops": 0, "late_stops": 0},
  "integer_Z": {"tested": 0, "exact": 0},
  "retention": {"certified_scope": "finite acquired registry", "tested": 0, "regressions": 0},
  "interference": {"edges": [], "recoveries": [], "escalation_justification": null},
  "status": "INCOMPLETE"
}
```

The numbers above are placeholders, **not measured results**. Reports must distinguish `UNTESTED`, `INCOMPLETE`, `PROVISIONAL`, `CERTIFIED_FINITE`, and `REGRESSION`. Never label sampled success a full certification or move ahead after a failed end-to-end regression gate.

## 9. Immediate next action

**First engineering patch should be P0/P1, not another glass:** establish the shared `numeric-terminal-v1` schema and replace the learned detector's legacy `ANSWER:` completion labels, maintaining isolated versioned artifacts and tests. Then P2 integrates actual HybridLink termination. P3 proves exact `Z` output. Only after that do we use the existing glass to correct and memorize the actual numeric-contract low-range failures, and finally explore the wider frontier.

This specification is the **training scheme and dependency plan**. It is intentionally not a claim that P0–P7 have been implemented or that any new numeric-contract corpus has already reached 100% accuracy.
