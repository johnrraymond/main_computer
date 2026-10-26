# Zero-line scale-invariance and residual-proportional accuracy test

## Objective

Test (read-only) whether the **existing frozen signed-addition network** can preserve near-cancellation with operands anywhere in `[-2_000_000,+2_000_000]`, while allowing absolute error to grow *with the residual*, not with operand magnitude.

This is not training, model promotion, or proof of all possible pairs. Earlier research has inspected related OOD examples, so new samples from this experiment are **exploratory**, not an untouched final blind certification set.

## Procedure

For an absolute residual `d` and input magnitude `m`, construct:

- `A = +m`, `B = d-m` and its operand swap (residual `+d`)
- `A = -m`, `B = m-d` and its operand swap (residual `-d`)
- for `d=0`, exact positive/negative balanced cancellation

Only valid pairs with `abs(A), abs(B) <= 2_000_000` appear. Thus the program tests both large nearly equal and opposed operands (e.g. `2_000_000 + (-1_999_999) = 1`) and ordinary same-sign sums (e.g. `2_000_000 + 2_000_000 = 4_000_000`). Tested maxima are stratified into 13 contiguous magnitude bands, including the original <=127 region, accepted 128–191 region, the examined 192–255 region, and entirely untrained high magnitudes up to two million.

Default absolute residuals:

- Near: `0,1,2,5,10,16,32,64`, tested with both signs.
- Further away: `128,256,512,1000,10000,100000,1000000,2000000,4000000`, tested with both signs where operand bounds permit.

The plan uses deterministic random anchors mixed with mandatory interval ends and decimal transitions, and evaluates swaps and simultaneous sign inversions. Anchor sample size is adjustable using `--sample-per-band`; **no randomness or arithmetic targets are passed to the network**.

## Correctness measurements

- The decoder predicts independent sign and seven base-10 result digits. Ground truth is calculated *after inference* and used only for evaluation.
- `delta=0` requires exact zero with canonical sign and all-zero digits; false-zero and missed-zero predictions are reported.
- Nonzero `|delta| <= 16`: exact signed integer results are mandatory (five percent of each magnitude is less than one integer).
- For other nonzero residuals: an answer passes at 5% relative error only when `20 * abs(predicted - actual) <= abs(actual)` and the predicted sign matches the actual sign. **No epsilon or operand-magnitude denominator** is used.
- The sampled-hypothesis flag requires complete correct zero and small signed residuals in all sampled bands and a configurable 5%-within-rate for wider near-residuals and larger residuals in each populated magnitude band (default 80% pass rate; this is a *sampled research criterion*, not an established safety or mathematical threshold).
- Reports also include numerical exact accuracy, signed accuracy, absolute error, mean relative error on nonzero outputs, per-residual and per-magnitude diagnostics, operand-swap consistency, and sign-inversion consistency.

The original backbone is evaluated alone. Optionally, supply all three previously accepted bank/router/repaired-bank directories to evaluate the **learned inference chain** on the same cards. The two bank integrations were certified only through magnitude 191: their results above 191 are explicitly extrapolation, not certification or magnitude-based routing. No rejected 192–223 candidate is used.

## Commands (Windows 11 / Python launcher)

Plan test cards, with no model loading:

```powershell
& $py -m tools.nanojev_zero_line_scale_invariance --phase plan --sample-per-band 32
```

Evaluate the certified backbone **and** the accepted learned chain:

```powershell
& $py -m tools.nanojev_zero_line_scale_invariance `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --prior-bank-output "C:\Users\subsi\NanoJev\runs\nanojev_absolute_bank_128_159_v1" `
    --prior-router-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_learned_router_128_159_v1" `
    --repair-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_cumulative_160_191_repair_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_scale_invariance_v1" `
    --device cuda `
    --sample-per-band 32 `
    --eval-batch-size 256
```

For backbone-only, omit **all three** optional prior/repair directories. To change the seed or card count, use a **fresh output directory**; the runner refuses to overwrite existing reports.

Results:

- `result.json`: grouped metrics by residual, sign, size band, near/far category, numerical operation, and combinations, plus pass/fail checks per model.
- `cards.csv`: every pair, true residual, decoded model predictions, signed/absolute errors, exact flags, and 5%-within flags.
- `config.json`: sampled populations, parameters, absolute source paths and source/checkpoint SHA-256 hashes.
- Console summary lines `ZERO_SCALE_MODEL`, `ZERO_SCALE_BAND`, and `ZERO_SCALE_SUMMARY` for comparisons.

## Immutability and no-oracle constraints

The script does not run an optimizer, perform backpropagation, update a checkpoint, choose weights using OOD, add input-magnitude routing rules, or substitute target arithmetic as an inference fallback. For source verification, it loads only previously certified/promotion-provenance-checked files and rehashes them after evaluation. The output directory must be separate from source directories and must be new or empty.

The `predict()` function accepts only input tensors and frozen models. It never receives `delta`, `A+B`, historical correctness flags, routing labels, or magnitude bands. Ground-truth arithmetic occurs exclusively in *data generation* and the subsequent evaluation function.

## Interpretation

1. If exact zero and small signed residuals survive through two million, the zero-line invariant has evidence of cross-scale generalization. It is still not a proof of universal invariance.
2. If larger residuals miss exact match but satisfy the 5%-relative rule consistently, growing absolute errors may be tolerable for the intended objective; inspect signs, quantiles, and local failure clusters.
3. If small residuals fail at large operands, the intended invariant has **not** generalized to that scale, regardless of ordinary addition performance. Do not treat large-operand precision loss as harmless near zero.
4. If only the learned chain behaves worse than the backbone at large magnitudes, routing/specialist extrapolation is the issue; the backbone alone may still carry a useful zero-line representation.

Neither case justifies automatically retraining the backbone before examining evidence, nor resuming the two-million autopilot until the invariant and error criterion have been evaluated.
