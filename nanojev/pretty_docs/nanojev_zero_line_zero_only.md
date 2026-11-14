# NanoJev: zero-only backbone scale-stability campaign

## Scientific question

Can the **existing certified digit-token backbone** learn the exact cancellation
relationship `A + (-A) = 0` across **operand magnitudes 0–2,000,000**, while
rejecting nearby nonzero inputs and retaining its preexisting exact arithmetic?

This is a **new, separately trained copy**, not a mutation of the original
certified checkpoint or its two accepted specialist banks / learned routers.
It trains the **recurrent transformer backbone** (plus norm, query, and position
parameters) rather than adding a specialist, routing rule, or arithmetic executor.
The input encoder and output heads are held fixed.

## Phase-1 supervision

* Positive examples are pairs `(A, -A)`. Require **canonical exact zero**:
  zero sign *and* all seven output digits zero.
* Negative controls are pairs `(A, -A + delta)`, with delta in
  `{-5, -2, -1, +1, +2, +5}`. These are used only for the classification
  **nonzero**, with nonzero sign and at least one nonzero output digit. Phase 1
  **does not** teach or certify an exact nonzero sum or its sign.
* Training uses independent uniform magnitude **bands**, operand swaps and
  reversed signs. Decimal edges and 2,000,000 are represented in deterministic
  assessment anchors.
* Rehearsal draws original `[-127,127] × [-127,127]` cards. Standard fully
  supervised sign and digit loss, plus frozen teacher distillation, preserve the
  certified system's original task. This rehearsal is the only general-arithmetic
  training in the campaign.
* Failure-driven sampler weights are calculated from **TRAIN-only** probes;
  they never use DEV or withheld TEST mistakes for new training examples.

## Dataset isolation and scope

* Distinct magnitudes are divided by stable SHA-256 of the **actual maximum
  absolute operand magnitude**. All sign and operand permutations of the same
  absolute-magnitude family go in the same split, beyond original radius 127.
* Because the source backbone already trained on the whole original 65,025-card
  deck, the radius-0–127 category is **historically exposed**, not a blind
  generalization test; its explicit purpose is retention.
* DEV magnitude families are repeatedly scored for checkpoint training progress.
* TEST families, including the special withheld **2,000,000** endpoint, are
  checked **only once**, after all DEV stability and full historical mastery
  checks pass. A failing TEST closes the campaign to further training and
  rejects promotion; start a clearly named new scientific campaign for later
  hypothesis changes, not another run exploiting the same test.
* The 13 bands and modest anchor counts are **sampled certification**, not a
  mathematical proof of universal cancellation across all integers.
* These are newly withheld *magnitudes* for this training run; prior broader
  experiments have already informed the research program, so this is not an
  externally blind final certification.

## Promotion rule (intentionally strict)

1. Every DEV band must show **100% canonical-zero exactness** and **100%
   nonzero rejection**, for `--stability-probes` consecutive evaluations.
2. The candidate must retain **all 65,025 original radius-127 ordered pairs**
   with exact sign and seven output digits (zero new errors).
3. A one-time TEST evaluation must pass both predicates in **every band**.
4. Only then is `promoted.pt` written. Failure, budget exhaustion, numerical
   instability or file/provenance drift **never promotes**.

### Plan (no model loading)

```powershell
& $py -m tools.nanojev_zero_line_zero_only --phase plan --device cpu
```

### Train on CUDA

```powershell
& $py -m tools.nanojev_zero_line_zero_only `
    --phase train `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_zero_only_v1" `
    --device cuda `
    --max-steps 30000 `
    --probe-every 250 `
    --batch-size 128 `
    --legacy-batch-size 128 `
    --legacy-weight 2 `
    --lr 0.00005
```

On an interrupted run, issue the **same command** with `--resume`. You can
increase `--max-steps` when resuming a budget-exhausted run; all other
algorithmic settings, source checkpoint SHA-256, and source-code hashes must
match. Do not use `--resume` after TEST was opened or after promotion. No
source checkpoint, bank, accepted router, or old certificate is overwritten.

### Outputs

* `source_baseline.json`: the original backbone's *pre-training* canonical-zero
  and near-nonzero rejection by band.
* `latest.pt`: model, optimizer, Python/Torch/CUDA RNG states, curriculum weights,
  progress and DEV streak. Atomic saves support resume.
* `best_dev.pt`: best sampled DEV-minimum model; **not certified** by itself.
* `result.json`: last evaluation, per-band exact-zero/rejection scores, train-only
  failure diagnostics, sampled historical rehearsal audit, and status.
* `promoted.pt`: exists **only** after DEV stability, exhaustive historical
  retention, and sealed TEST all pass.

Console lines are `ZERO_ONLY_PLAN`, `ZERO_ONLY_BASELINE`, `ZERO_ONLY_PROBE`,
`ZERO_ONLY_RETENTION_REFUSED`, `ZERO_ONLY_CERTIFICATION`, and
`ZERO_ONLY_SUMMARY`.

## Interpretation and limitations

* This teaches the **zero/nonzero decision**, not exact signed residuals and not
  5%-relative arithmetic. A nonzero false positive from the decoder counts as a
  failure on genuine zero. Any always-zero predictor will fail near misses.
* Trainable transformer weights may still overfit seen magnitude families.
  Withheld tests, neighboring digits and boundaries are intended to detect it.
* Success on sampled heldouts is **not evidence of mathematical universality**;
  add independent fresh adversarial magnitude families before making a broad
  scale-invariance claim.
* Failing to promote under the strict 65,025-pair requirement does not prove
  cancellation is unlearnable; the historical gate may be the binding constraint.
* Do not use this zero-only model as a replacement for the accepted production
  arithmetic chain until full certification and subsequent regression testing.
