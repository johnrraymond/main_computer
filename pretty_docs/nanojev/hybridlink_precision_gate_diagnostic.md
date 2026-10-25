# HybridLink precision gate audit

This read-only tool distinguishes a poor *zero classifier* from poor *nonzero numerical regression* without investing further GPU training time.

Run `python -m tools.nanojev_hawk_dove_hybridlink_precision_gate_diagnostic` with the same `--reference-checkpoint` and `--curriculum-output` arguments as the precision experiment, plus `--precision-output` pointing to the completed head-only experiment, `--device cuda`, and an optional `--report` JSON path.

The audit loads the DEV-selected `best.pt`, validates the original checkpoint hashes, and compares:

- `ungated_raw`: original regression with the zero classifier fully bypassed.
- `threshold_grid`: the DEV result at ten zero decision thresholds, including the count of false zero decisions and exact-/near-zero strict joint X/Y rates.
- `oracle_zero_gate_diagnostic_only`: a deliberately non-deployable **ceiling for repairing the zero gate alone**, which replaces predicted coordinates with zero only where the DEV target is known to be exactly zero. This uses DEV truths **only for diagnosis** and must not be used as an inference path, a trainable target leak, or evidence of autonomous success.

If near-zero nonzero strict accuracy remains zero even with the oracle zero gate, a classifier threshold change cannot fix the regression error. If the ungated regression passes near-zero cases that the gated model misses, the zero classifier is interfering. If both fail, the next experiment requires improved regression precision. The original 80% gate, frozen reference, frozen format checkpoint, and blind TEST evaluation boundary remain unchanged.
