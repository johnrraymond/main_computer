# NanoJev HybridAgent v4.2 — distinct-instance shuffled controls

This updates the **v4 experiment**, not the original Gaussian checkpoint. It corrects teacher-gate false positives and repeated product tokens. Use a fresh run directory: checkpoint schema `nanojev.hawk-dove.hybrid-recovered-product-selfsft.v4.2` deliberately rejects resuming v4 checkpoint state produced by the earlier gate.

Historical DEV and TEST have already been inspected. Results remain exploratory, not new blind certification.

## Invariant

**HybridLink activation may begin only after teacher-supplied arithmetic thoughts make a measurable improvement in paired, held-out X/Y prediction over the untouched Gaussian source and over shuffled and zeroed versions of the same thoughts.** The teacher model is selected on DEV and is never certified on TEST during selection.

## Shuffled-thought control integrity

A one-row `.roll(1, 0)` is **not** a valid counterfactual when each numerical
instance has two presentation masks: it can swap two identical thought sequences.
The corrected control uses a deterministic **instance-level distinct-product
derangement**. Each case's masks share the same donor, the donor's four
numerical partial products differ from the recipient's, and all teacher-token
sequences must change. If there are too few distinct examples to establish
such a derangement, evaluation refuses instead of reporting a misleading tie.

The paired teacher gate constructs the donor mapping **once on the entire DEV
split** and slices already-shuffled tokens for each evaluation batch, so the
results are independent of `--eval-batch-size`, including odd sizes that split
a two-mask pair. `teacher_gate.json` records the method, the number of changed
presentations, and the distinct-instance guarantee. The model's separate
self-generated-feedback shuffled control uses the same guarded algorithm
within its inference batches. No teacher oracle values are used by the final
model; the control is an evaluation-only intervention.

This changes the meaning of teacher-gate evidence. The schema is bumped, and
a **fresh output directory** is required; do not resume v4.1 training states.
A teacher refusal remains valid if the correct products do not outperform the
reference and controls by the full paired threshold.

## Recovery and teacher-utility stages

1. Load the immutable Gaussian checkpoint and verify that the student readout reproduces its DEV density exactly before any training.
2. Bootstrap a numerical student with **four** typed, count-times-payoff product tokens. There are no sum/sign answers, and steps 5–8 no longer recycle the first four products. Teacher activation and subsequent self-conditioned curriculum are now capped at depths **1, 2, 4**. The memory module attends to the original context and growing latent-token prefix.
3. Evaluate correct product tokens, shuffled products, zeroed products, and the **original frozen Gaussian checkpoint** on the exact same DEV examples. The original reference is never replaced by the student’s current readout.
4. Group presentations into independent `(tier, matrix/count instance)` cases; both X/Y must be within 5% **on every mask**. Exact-zero targets require exact-zero predictions, matching the existing Gaussian evaluator.
5. For each tier and each of three controls, count correctly rescued versus regressed instances, compute an absolute paired accuracy gain, and record paired net wins by held-out payoff matrix. The default gate requires **at least two percentage points (`--min-teacher-gain 0.02`) in every tier against every control**, more rescued than regressed cases, and positive net gains on at least **two distinct DEV matrices** for each contrast. A gain of 0.000012, or correct and shuffled predictions tying, **fails**.
6. Select the teacher bootstrap checkpoint by (i) passes every gate, (ii) largest weakest paired improvement, (iii) average paired improvement. An earlier fully passing candidate cannot be displaced by a non-passing one.
7. **Only on PASS:** freeze the reasoning core while activating HybridLink on the four roles; then jointly train the core, HybridLink, Gaussian readout, and recurrent numerical update with self-generated tokens.

Failing teacher utility is a valid outcome. Do **not** lower the gate simply to launch latent training. A failure isolates the unresolved question: the model has not yet demonstrated it can use correctly supplied intermediate products to improve output over the source and corrupted controls.

## Install and run

```text
python new_patch.py nanojev_hybrid_v4_distinct_shuffle_patch.zip --dry-run
python new_patch.py nanojev_hybrid_v4_distinct_shuffle_patch.zip
```

Run using the project's existing Python interpreter (for example, `$py` in the existing Windows session). **Do not overwrite or resume the original v4 run**:

```text
python -m tools.nanojev_hawk_dove_gaussian_hybrid_agent_v4 --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --output "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_hybrid_agent_v4_distinct_shuffle" --device cuda --bootstrap-steps 4000 --warmup-steps 2000 --steps 12000 --probe-every 500 --batch-size 64
```

On a normal refusal, inspect `teacher_gate.json` and `teacher_best.pt`. `teacher_gate.json` now contains per-tier, per-control paired rescued/regressed counts, the original reference's untouched all-mask accuracy, and per-matrix net rescues. On a successful run, the final report keeps **teacher useful at bootstrap** and **self-generated latent causal gain** as two separate findings. The old v4 report should remain available unchanged in its old output directory.

## Tests and scope

The regression tests use the real `0.02` threshold for refusal and positive gate examples. End-to-end **training-lifecycle** fixtures explicitly simulate a passed gate so optimizer, source-integrity, and exact-resume behavior can be exercised on tiny random models; those smoke tests are **not evidence** that real products improved accuracy. No numerical CUDA accuracy improvement is claimed until the user's checkpoint is evaluated.
