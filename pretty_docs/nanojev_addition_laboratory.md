# NanoJev Addition Laboratory v1

## Purpose

Isolate learned addition of two signed numerical thoughts from Hawk–Dove, Z, multiplication,
HybridAgent and external arithmetic. Compare (A) a continuous signed/log-magnitude input
encoding patterned after the existing HybridLink teacher product encoding against (B) a
trainable decimal position/sign encoding. Both use identically initialized, shared-weight
causal Transformer recurrences, the same numerical readouts, identical sample order, and
identical optimization budgets. This is a newly initialized laboratory backbone with the
same causal Transformer *architecture*, **not** a claim that the pretrained Hawk–Dove
backbone was loaded or certified.

There is no external addition executor or fixed arithmetic coefficient matrix. The
inference interface is exactly two signed integer inputs. The model predicts independent
sign, seven numerical output digits and an auxiliary continuous sum. Digits are assembled
into an integer only for decoding/evaluation (ordinary numeral decoding, **not** calculating
an answer from the operands). Output supervision uses the arithmetic sum on TRAIN only;
DEV targets score the learned predictions but do not update weights.

## Installation

Apply the contents of the ZIP rooted at `main_computer_test/` to the repository root;
the new `.md` belongs in `pretty_docs/`. It adds only these three files:

- `tools/nanojev_addition_lab.py`
- `tests/test_nanojev_addition_lab.py`
- `pretty_docs/nanojev_addition_laboratory.md`

Use your NanoJev PyTorch environment (for example `.venv-diagram`). No Gaussian reference
checkpoint or Hawk–Dove training artifacts are required and none is modified.

## Run both arms on CUDA

```text
python -m tools.nanojev_addition_lab --phase train --device cuda --output "C:\Users\subsi\NanoJev\runs\nanojev_addition_lab_v1" --steps 3000 --probe-every 250 --batch-size 256
```

Both arms are run sequentially (not concurrently on the GPU). For interrupted training,
rerun the **same command** with `--resume` and `--steps` equal to the desired total steps
per arm. `--resume --steps 5000` extends both arms to step 5,000 without resetting them.
Do not alter experiment configuration (training domain, width, random seed, etc.) on resume.

For a quick CPU smoke test:

```text
python -m tools.nanojev_addition_lab --output runs/addition_smoke --device cpu --steps 2 --probe-every 1 --width 16 --heads 4 --layers 1 --rounds 1 --train-size 60 --dev-size 12 --test-size 12 --batch-size 8 --eval-batch-size 12
python -m pytest tests/test_nanojev_addition_lab.py -q
```

## Dataset and interpretation

TRAIN: up to 1.6 million absolute per operand, with balanced **away / near / zero** cases.
Near cancellation means `0 < |A+B| <= 0.01*max(|A|,|B|)`; exact cancellation means
`A+B == 0`; the away class is the complement among nonzero sums. Inputs are integers.

DEV: 3,072 cases, evenly divided among six groups: each cancellation class in both
TRAIN-supported magnitudes (ID) and unseen high-magnitude 1.8–2.0 million (OOD).
Operand magnitudes belong to a deterministic pseudorandom train/dev/test partition:
no absolute operand value (or its negative) can occur in more than one split. Samples
are freshly generated from a fixed documented seed. OOD magnitude tests evaluate a
hard extrapolation not represented in TRAIN; failing there is a result, not a crash.

Each arm reports strict ±5% accuracy (exact equality when answer is zero) for decoded
digits and for its auxiliary continuous readout, by cancellation class and magnitude
band, plus exact integer accuracy and individual output digit accuracies. The evaluation
also probes commutativity, sign reversal, and self-cancellation on modified input
pairs, but an always-zero network can trivially pass these consistency checks;
**they are not a substitute for accurate sums**. Checkpoint
selection uses the **minimum** of the six DEV-group digit-output accuracy values, then
aggregate accuracy as the tie-breaker. The DEV research gate is >=80% in **every** group.
This is a laboratory target, **not** HybridLink certification.

`arms/<arm>/latest.pt`: deterministic resumable model, optimizer and RNG state.
`arms/<arm>/best.pt`: best DEV model; `arms/<arm>/result.json`: best DEV metrics.
`comparison.json`: side-by-side results and `TEST: null`.

Separate read-only DEV recheck:

```text
python -m tools.nanojev_addition_lab --phase dev --device cuda --output "C:\Users\subsi\NanoJev\runs\nanojev_addition_lab_v1"
```

TEST is not even generated during TRAIN or DEV. Only an explicit `--phase test
--authorize-test` invocation may construct TEST and only after the requested arm(s)
have passed their DEV gate. Do this once, after deciding on the final experiment. An existing `final_test.json`
blocks subsequent TEST runs to prevent accidental repeated holdout selection.

## Causal caveats

Input operands are visible to both representations. The first arm is newly initialized
with HybridLink-style sign/log/value numerical inputs; it does not transfer the
4,000-step model weights. The positional arm supplies decimal *representation* only,
not the addition algorithm; predicting correctly requires learning the sign, sum digits
and interactions (including carry). Both arms can in principle overfit, and no DEV
success should be called generalization without inspecting held-out strata and final
TEST. Hard exact-zero evaluation deliberately tests whether the neural output scheme
can produce exactly zero. No Z, learned operator shortcut, oracle products, symbolic
execution, or final-answer input is present in either inference path.
