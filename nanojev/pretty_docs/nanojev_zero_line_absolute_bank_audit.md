# Zero-line bank: independent anti-oracle and generalization audit

The absolute-magnitude bank trainer is **not an inference-time oracle**: its
bank receives recurrent features calculated from the input pair; the supervised
label `A+B` is separate. But its nominal 95.22% OOD score is a stratified sample
of *held-out maximum magnitudes within the assigned 128–159 range*. It is not a
blind proof of arbitrary magnitude arithmetic, nor proof that a newly trained
bank is responsible for the base's original zero-line competence.

This read-only experiment checks these limitations **before** the next bank is
trained. It does not alter or re-train the source, specialist, or router; does
not inspect a separate TEST set; does not substitute an arithmetic executor
for network predictions.

## Command

```text
python -m tools.nanojev_zero_line_absolute_bank_audit --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" --bank-output "C:\Users\subsi\NanoJev\runs\nanojev_absolute_bank_128_159_v1" --output "C:\Users\subsi\NanoJev\runs\nanojev_absolute_bank_128_159_audit_v1" --band "128:159" --device cuda
```

The runner requires a promoted checkpoint from the original bank run and the
matching certified radius-127 source hash.

It exhaustively evaluates **all 36,736 ordered signed operand pairs** where
`128 <= max(abs(A),abs(B)) <= 159`, not just sampled / answer-balanced examples.
It also evaluates **all** pairs in the adjacent untrained `160..191` shell as a
diagnostic. Add `--skip-adjacent` for a shorter in-bank audit.

The report records:

- **Original backbone vs. new bank exact accuracy** on the same samples; counts
  for newly correct and newly wrong predictions. A high absolute bank accuracy
  without net gain over the base would not demonstrate much added competence.
- Full in-bank performance and **held-out maximum-magnitude subsets**, not a
  label-stratified score. The magnitude holdouts are determined with the same
  seed and policy used by training. Evaluating the entire shell also exposes
  all training examples: use the held-out subset to assess transfer, not the
  full-shell average alone.
- Per-magnitude, per-signed-residual group and positive/negative/zero scores.
- Real predictions under input swaps and simultaneous sign reversal, recording
  both prediction agreement and accuracy for both members of each pair.
- Counterfactual label-independence: altering evaluation targets while holding
  operands fixed cannot affect frozen features, original network logits, or
  the bank's predictions. This is an anti-oracle software-path test, not a
  cryptographic proof against every conceivable data leakage.
- SHA256 verification for original source and saved promoted specialist before
  and after the audit.

## What qualifies as genuine progress?

An objectively useful next-magnitude signal is **net improvement over the
unmodified frozen base**, including near-zero residuals and both orientations,
on pairs excluded from the bank's assigned training magnitudes. Evaluate the
adjacent shell without using that diagnostic for training or checkpoint
selection. For a conclusive claim about extrapolation, a later test must use a
new sealed magnitude range beyond the one repeatedly monitored.

`audit.json` is intentionally a diagnostic, **not** an alternative certification
or permission to claim promotion. The only training and deployment routing in
the earlier harness is by a hard-coded maximum of absolute input magnitudes;
this remains engineered routing rather than an end-to-end learned bank choice.
