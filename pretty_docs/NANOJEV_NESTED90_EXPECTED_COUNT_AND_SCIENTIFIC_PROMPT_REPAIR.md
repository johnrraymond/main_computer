# NanoJev 21-bank nested-90 curriculum — scientific annotation and expected-count repair

## Two independent defects

1. **Pre-body capture crash**: the old nested-90 prompt renderer used the legacy `value, LogBar` annotation by default, but the donor numeric adapter strictly expects `value, LogBar, scientific`. The old runner now explicitly renders scientific prompts and validates each with the unchanged donor adapter **before** loading Qwen. The existing integer-count teacher and old tests retain their legacy default.
2. **Numerical impoverishment**: under fixed integer encounter counts, `|X|<1` contains only X=0. Drawing 245 presentations from that band repeats zero. A *separate* expected-count curriculum now permits **fractional expected Hawk and Dove encounters** summing exactly to one million, with unchanged payoff bindings and exact Decimal arithmetic. This changes the training problem semantics from actual integer encounters to expected encounter totals; it does **not** falsely claim these are new integer-observation instances.

## New curriculum: distinct nonzero numerical targets

The module `tools.nanojev_hawk_dove_gaussian_joint_decoder_21_nested90_expected` uses `tools.nanojev_hawk_dove_nested90_expected` to generate distinct, never-repeated Decimal expected-count cases in eight exclusive bands. It uniformly samples from reachable intervals of oracle X within each exclusive magnitude band, inverts the fixed payoff equation to find expected-count a, quantizes a to 12 decimal places, recomputes **both** X and Y exactly, and rejects any candidate outside its requested band or duplicated across train/new-dev. `T00`–`T08` prompts preserve the game payoff operations; English prompts explicitly say *expected number of plays* rather than asserting that a fractional count occurred in an observed million encounters.

There is **no global change** to the original `nanojev_hawk_dove_million.py`, teacher SQLite DB, the donor numeric adapter file (whose SHA is part of checkpoint identity), the frozen Qwen model, or previously trained 21-bank weights. The new runner has its own schema, output directory, capture cache, trainer identity, and optimizer initialization. The existing 21-bank checkpoint is imported read-only via `--decoder-init-checkpoint`.

### Numerical encoder

The donor `numeric_adapter.encode` only permits integer operands and exact scientific integer annotations. The new expected-count runner therefore uses a **locally implemented, prompt-validated expected-count role encoding**, with the same 12 input roles and 1,000,000 scale as the existing donor adapter. It supplies the role tensor to the frozen decoder without modifying shared functions or monkeypatching globals. It does not feed X/Y oracle answers into the numeric operand adapter. The unchanged `'011'` XYOP visibility setting is inherited from the existing framework.

The continuous prompts use *raw exact decimal literals*, with no invented LogBar/scientific encodings for fractional values. All prompt tiers are checked against deterministic original templates, and Lisp expressions are evaluated exactly against the oracle. This is an intentional representation difference from the legacy LogBar mutation task. The model's actual token-length eligibility is checked during capture, before optimizing any step, although full tokenization requires the local Qwen runtime.

## Commands

Audit without a GPU:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21_nested90_expected `
    --mixture-audit-only `
    --mixture-train-per-tier 512 `
    --mixture-new-dev-per-tier 128 `
    --mixture-strict-unique
```

New experiment (does **not** resume the old crashed `nested90_v1` output):

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21_nested90_expected `
    --device cuda `
    --relative-max 2000000 `
    --decoder-init-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_v1\latest.pt" `
    --mixture-train-per-tier 512 `
    --mixture-new-dev-per-tier 128 `
    --mixture-strict-unique `
    --additional-steps 1000 `
    --max-steps 30000000 `
    --chunk-steps 1000 `
    --views 32 `
    --gate-percent 40
```

Output: `C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_nested90_expected_v1`.

The old integer-count command also passes the scientific-annotation preflight after this patch, but it still has the mathematically unavoidable sparse small-value support. Use the *expected* command for newly distinct sub-one targets.

## Verification boundary

Local synthetic-fixture audit: 512 distinct training instances per tier, including 245 distinct nonzero X values with `|X|<1`, 128 disjoint new-development instances per tier, 0 repeated numerical instances. End-to-end mocked training/resume, exact tier template/oracle certification, local pre-body capture, and Gaussian regression suites were exercised. **No full CUDA Qwen training was run in this environment.** Improvement over the original integer-count development benchmark is an empirical question, not established by synthetic audit.
