# NanoJev progressive addition curriculum v2

Research objective: determine whether two neural input representations can **learn
atomic addition** and then **compose it on previously unseen operand pairs**.
This is a controlled experiment, not a HybridLink certification. It uses fresh
small models, not the trained Hawk–Dove backbone. Neither model receives the
sum, a scripted carry, or a calculator result at inference. Exact Python
addition appears only in dataset target construction.

## The decisive design correction

The old first stage exposed only 29 ordered TRAIN problems (16 unordered) and
required the model to generalize to 13 unseen problems before it had learned
the elementary addition table. This made its failure difficult to interpret.

The new `atomic` stage explicitly trains **all 100 ordered pairs** `a,b ∈ 0..9`,
including all one-digit carries. The diagnostic DEV view of atomic is the
**same exposed 100 pairs**. It is labeled `MASTERY_min`, not unseen DEV, and
requires **100% strict decoded accuracy** across both carry and no-carry
categories. Memorization is allowed at this atomic stage. There is **no
independent atomic TEST split** and the final TEST report skips atomic.

Every *subsequent* stage partitions complete operand pairs (both orders in the
same partition); the signed/large regimes also partition sign mirrors
together. Thus, the model must generalize beyond *specific combinations* of
operands used in each stage's TRAIN, and later stages retain earlier skills.
Only TEST requires explicit authorization and successful completion of all
stages; TEST data are not constructed during training.

## Stages and criteria

| Stage | Train concept | Evaluation |
| --- | --- | --- |
| `atomic` | All 100 ordered sums of digits 0..9, including carrying | **Exposed mastery**, 100% required; not independent DEV |
| `no_carry` | Positive two-digit addition without carries at units or hundreds | Novel two-digit operand pairs, ≥80% DEV |
| `carry` | Two-digit addition with unit carry, hundred carry, or both | Three novel-pair DEV categories, each ≥80% |
| `signed` | Negative + negative; mixed signs with and without zero result | Three novel-pair DEV categories, each ≥80% |
| `cancellation` | Magnitudes up to 9,999 with away/near/exact-zero answers | Three novel-pair DEV categories, each ≥80% |
| `generalization` | In-range up to 99,999 plus held-out OOD magnitudes 100,000..999,999 | Six in-range/out-of-range DEV groups, each ≥80% |

Retention: prior-stage gates must also remain satisfied at each advancement:
atomic stays at 100% exposed mastery, and other stage DEV categories stay at
≥80%. Replay draws old training examples; it does not supply answers at
inference. Digit outputs are decoded by assembling **predicted** digits, not
by adding the inputs. Strict relative accuracy requires exact zero when the
true sum is zero.

A full atomic addition table is finite and can be memorized. Passing atomic
alone is **not** evidence of learned general addition; only later composition
and out-of-range performance test that claim. Likewise, passing held-out
operand pairs alone does not constitute proof of a fully general arithmetic
algorithm. The test checks transfer across unseen combinations and magnitudes.

## Two comparison arms

- `compact`: value, sign, and log-magnitude encoded into two operand tokens.
- `digit_tokens`: 14 independent learned digit tokens, least significant
  position first, with sign/role/place embeddings. The same recurrent causal
  Transformer and answer heads are used in both arms with identical shared
  backbone/head initialization.

Both emit a digit/sign answer (strict-gated) and a continuous regression answer
(diagnostic only). A high regression score cannot substitute for the strict
digit output gate.

## Run (new v2 directory)

From the Main Computer repository root, using the NanoJev Python interpreter:

```text
python -m tools.nanojev_addition_curriculum --phase train --output "C:\Users\subsi\NanoJev\runs\nanojev_progressive_addition_v2" --device cuda --steps-per-stage 3000 --probe-every 250 --batch-size 128
```

Do **not** resume the earlier `nanojev_progressive_addition_v1`. Source and
schema have changed and the dataset and gate semantics are incompatible.
The harness automatically trains each arm until it first fails a stage or
passes all stages. If a stage needs more iterations, restart using the **same
v2 output**, with e.g. `--steps-per-stage 6000 --resume`; this is 6,000 TOTAL
steps at that stage, not 6,000 additional. Do not change source or
hyperparameters other than `--steps-per-stage` when resuming.

Output artifacts: `config.json`, `summary.json`, and `arms/<arm>/{latest.pt,
<stage>_best.pt,<stage>.json}`. A passed stage also gets an immutable
`<stage>_passed.pt`. At each probe, `TRAIN`, `MASTERY_min` (only atomic),
`DEV_min` (later stages), `retention_min`, and `DEV_reg` are reported.

If all DEV gates eventually pass, the separate one-time TEST phase is:

```text
python -m tools.nanojev_addition_curriculum --phase test --authorize-test --output "C:\Users\subsi\NanoJev\runs\nanojev_progressive_addition_v2" --device cuda --steps-per-stage 3000 --probe-every 250 --batch-size 128
```

Set options to match the configuration used for TRAIN; the harness fails
closed on configuration drift. Do **not** run TEST during tuning.

## Validation

```text
python -m pytest -q tests/test_nanojev_addition_curriculum.py
```

Tests cover complete exposed atomic domain, train/dev/test pair isolation,
finite population counts, classification of carry cases, signed/near/zero
coverage, architecture equivalence, backprop, exact decoding, fail-closed
gating, deterministic resume, config drift, stage transitions, and TEST
separation. The GPU decode test skips automatically when CUDA is absent.

Limitations: all models train from scratch; a failure at any stage cannot be
attributed specifically to the original Hawk–Dove backbone. Atomic's exposed
score is not a held-out result, and even the final OOD evaluation is bounded
to a specified integer range.
