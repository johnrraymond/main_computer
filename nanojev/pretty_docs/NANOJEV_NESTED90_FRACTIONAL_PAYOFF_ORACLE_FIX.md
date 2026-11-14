# NanoJev 21-bank nested-90% oracle compatibility fix

## Failure repaired

Both `--mixture-audit-only` and normal training failed with
`ValueError: NESTED90_INTEGER_ORACLE_REQUIRED` when the original Hawk–Dove
payoff fixture had decimal coefficients. The old code mistakenly assumed that
all four payoff coefficients must be integers. It also truncated them to
integers when reporting the training bands, which could silently misreport
which oracle outputs were generated.

The original `tools.nanojev_hawk_dove_million` oracle supports decimal payoff
coefficients. Its current LogBar prompt renderer requires **the calculated X
and Y targets to be exact integers**, not the coefficients. The repair matches
that behavior: it enumerates possible integer encounter counts `a`, computes
both X and Y using `Decimal` arithmetic, accepts only exact-integral outputs
inside the certified numeric range, and reports bins from the real X oracle.
It never rounds targets, alters payoff coefficients, modifies the teacher,
or changes the nested-90% quotas.

## Install

This is a **delta patch** on top of the previously installed
`nanojev_hawk_dove_21bank_nested90_mixture_patch.zip`. Extract
`main_computer_test/tools/nanojev_hawk_dove_nested90_mixture.py` into
`main_computer/tools/` and the matching test file into `main_computer/tests/`.
The training runner, bank geometry, and existing checkpoints are unchanged.

## Audit before training

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21_nested90 `
    --mixture-audit-only `
    --mixture-reallocate-empty-bands `
    --mixture-train-per-tier 512
```

When the audit succeeds, run the **same training command you already used**.
If the audit now reports `feasible: false`, the remaining obstruction is a
real limitation of the integer-output oracle and the original dev/test
exclusions, rather than this fixture validation bug. Inspect
`unreachable_bands`, `empty_band_transfers`, `unique_instance_shortfalls`,
`distribution_exact` and the cumulative percentages before training.

## Tests

Run:

```powershell
& $py -m unittest discover -s tests -p "test_nanojev_hawk_dove_nested90_mixture.py" -v
```

New tests cover a real temporary SQLite teacher/fixture, direct audit and
prompt generation, fractional payoffs, exact-integer X/Y candidate filtering,
full T00–T08 certification, and fail-closed mismatched payoff-fixture hashes.
Existing 21-bank checkpoint import/resume tests are retained.

This patch does not claim improved training accuracy; that requires CUDA
training and development probes.
