# NanoJev: teacher representation and thought-only decoding laboratory

## Purpose

Separate two previously conflated questions without modifying any existing
HybridAgent trainer or trained checkpoint:

1. **Encoding:** Do the four visible count × payoff products survive conversion
   into learned 128-dimensional typed thought tokens, and can a decoder
   reconstruct those four products?
2. **Decoding:** Can the existing causal-attention architecture learn X/Y using
   *only* these four teacher tokens, without an original Gaussian anchor?

The result is a **teacher-forced diagnostic**. It does **not** demonstrate
HybridLink-generated or autonomously recursive reasoning.

## Source and data safety

The script loads `teacher_best.pt` from the v4.2 experiment through the
existing `nanojev_hawk_dove_hybrid_teacher_probe.load_experiment` verifier.
It confirms the original Gaussian SHA, teacher SHA, checkpoint/schema identity,
held-out matrix hashes, domain and data split. Source checkpoint and teacher
checkpoint bytes are verified unchanged on completion. Only a new output
folder is modified. The original HybridAgent and Gaussian trainer files remain
untouched.

Features are reconstructed from the visible prompt. Intermediate products are
calculated from the six visible numeric features and never taken from X/Y
answer annotations. The four products contain sufficient arithmetic information
to compute X and Y, but are **teacher-supplied inputs** in these two tests.

TRAIN fits, DEV selects; by default TEST is **not** evaluated. `--final-test`
evaluates the already-examined historical TEST once after selection, so the
results remain exploratory rather than a new oracle-blind certification.

## Controls and comparisons

The laboratory reports:

- Raw four-product reconstruction and a fixed lossless 128D token encoding.
- The original pretrained typed embeddings' TRAIN-fitted linear readout.
- The Gaussian density decoder's representational ceiling when supplied
  the correct normalized X/Y locations directly. This diagnoses a possible
  limitation of the density geometry, independent of the thought network.
- A frozen-typed-embedding product reconstructor and a jointly trainable
  typed-embedding + product reconstructor. Both use the same readout initialization.
- Three **separately trained**, parameter-matched causal-attention X/Y models:
  fixed lossless tokens, frozen pretrained typed tokens, and trainable typed
  tokens. They begin with **identical** attention and readout weights.
  They consume only the four teacher tokens, not the reference Gaussian hidden
  state, and share the frozen Gaussian basis geometry for numerical output.
- Correct, distinct-instance shuffled, and zero-token attention ablations,
  reporting strict all-mask paired rescues and regressions.

All learned arms are initialized independently of the original pretrained
Gaussian *answer* readout; the attention arms learn an answer decoder directly
from teacher tokens. The Gaussian basis geometry is retained but frozen.

The product-reconstruction stage trains only to reconstruct the four
**normalized** products, then sums them and evaluates X/Y in the original
output units. The attention stage trains on the original Gaussian density
objective plus normalized-location supervision. This measures whether a fresh
numerical readout can learn to use those tokens, rather than assessing a
pretrained readout's behavior under a previously untrained ablation.

The strict metric is both X and Y within 5% of their numeric targets, requiring
**all presentation masks** to pass for each (tier, matrix/count instance). True
zero targets use exact-zero error. We also report mean/max absolute X/Y error,
normalization-product reconstruction error, and zero-coordinate hits.

## Install

Install the patch at the `main_computer` repository root:

```text
python new_patch.py nanojev_hybrid_representation_lab_patch.zip --dry-run
python new_patch.py nanojev_hybrid_representation_lab_patch.zip
```

## Run

Use the established v4.2 failed bootstrap run, whose `teacher_best.pt` is
read-only:

```text
python -m tools.nanojev_hawk_dove_hybrid_representation_lab --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_hybrid_agent_v4_distinct_shuffle" --output "C:\Users\subsi\NanoJev\runs\hawk_dove_hybrid_representation_lab_v1" --device cuda --steps-reconstruction 2000 --steps-attention 6000 --probe-every 250 --batch-size 128
```

The trainer runs five independent arms and saves `latest.pt` and `best.pt`
under a separate directory for each arm, as well as `controls.json` and
`report.json` in the new output directory. A resumable interrupted run uses
`--resume` with the same identity/configuration flags. No output overwrite is
permitted by default.

A small test can use `--steps-reconstruction 5 --steps-attention 5 --device cpu`
and a **new** output directory. CPU smoke results are not certification.

## Interpreting the gap

- If fixed lossless tokens succeed but frozen typed embeddings fail, investigate
  typed input representation and recovery.
- If frozen typed product reconstruction is accurate but its attention model
  fails, investigate the attention/output training interface.
- If jointly trained typed tokens help substantially, the pretrained typed
  encoding and downstream decoder were not aligned.
- If every attention model fails even with lossless tokens, consider the
  output decoder geometry and training objective; inspect the Gaussian-oracle
  ceiling before claiming an attention-only bottleneck.
- If shuffled/zero tokens perform as well as correct teacher tokens, no
  causal numerical benefit from correct teacher information has been shown.

**No threshold is silently bypassed.** The laboratory reports experimental
results without automatically proceeding to HybridLink activation. Previous
teacher-utility gates and trainers remain unchanged.
