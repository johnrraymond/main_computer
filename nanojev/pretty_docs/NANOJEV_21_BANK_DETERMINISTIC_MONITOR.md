# 21-bank deterministic numerical monitor (read-only)

The existing Gaussian-bank trainer remains unchanged. This patch adds a **parameter-free** estimator to the existing `nanojev_hawk_dove_gaussian_bank_21_monitor` command, alongside its original bank-shape KL report. No numerical decoder is trained, optimized, or invoked; the inference is read-only on the existing bank checkpoint. Without `--deterministic-test`, the monitor remains lightweight and performs exactly its former JSON-probe inspection.

## Run

From `C:\Users\subsi\main_computer` with `$py` pointing to the NanoJev interpreter:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_bank_21_monitor `
    --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_bank_21_direct_v1" `
    --deterministic-test `
    --device cuda `
    --dtype float16 `
    --samples-per-band 6 `
    --show-cases 12
```

`--deterministic-test` loads the current `latest.pt`, its immutable source snapshots, the locally installed frozen Qwen feature model, and a deterministic sample from the **held-out DEV** set. It does **not** use the TEST split. Defaults select six samples in each 1–10, 10–100, 100–1,000 band, across all nine tiers: 162 total (6 × 3 × 9). Runtime may be significant because the frozen feature encoder must run again. Use `--samples-per-band 2` for a smaller smoke check. It uses a temporary capture cache *outside* the run directory and does not save modified checkpoints, gradients, optimizer updates, or evaluation datasets. Runs while the trainer is active inspect the saved checkpoint version present at loading time. Use `--source`, `--teacher`, or `--mixture-fixture` only when needed to identify files whose hash must match the saved checkpoint.

The output retains **all** original Gaussian KL and shape metrics and adds `deterministic_test`, containing summaries by X magnitude band, actual vs predicted case examples, and largest raw-X errors. Missing or conflicting evidence results in a recorded **abstention** rather than a manufactured answer. The estimator does not clamp answers to the training interval or hide large errors.

## Deterministic mechanism

1. Read every bank's `P[LHS]`, 129-bin `P[Gaussian]`, `P[RHS]`. Strong LHS/RHS votes constrain X's feasible range, using the bank's actual radius. Interior votes are switched on only when interior probability exceeds 0.5 and exceeds each overflow class; strong overflow requires a probability above 0.75. Report the full gate trace for selected cases.
2. Normalize each accepted interior Gaussian *conditional on being inside*. Compute its center, width, and its KL divergence to the ideal fixed-2-bin Gaussian centered at the bank's **own predicted** center, not at the true oracle answer. This is a *shape* test, not a hidden target-position test. A misshapen output may fail the shape gate (`shape KL > 6`).
3. Weight the accepted centers according to Gaussian shape, interior mass, and bin resolution. Combine via robust weighted median followed by a compatible weighted center. Report number of accepted scales, consensus count, shape mismatch, and center disagreement. Contradictory overflow bounds cause abstention. **There is no probabilistic independence assumption:** the 21 banks share frozen upstream features, so the law of large numbers does not justify dividing the disagreement by the square root of 21.
4. The resulting numerical estimate is **X**. The existing banks have only ever been trained on X; there is **no separately trained Y bank**. In this fixed-payoff Hawk–Dove benchmark, `X = a*HH + b*HD`, `Y = a*DH + b*DD`, and `a+b=1,000,000`. Thus, using the immutable payoff fixture, **Y is algebraically derived from estimated X**:

   `Y_hat = 1000000*DD + ((DH-DD)/(HH-HD)) * (X_hat-1000000*HD)`.

   If `HH == HD`, Y cannot be identified from X alone and the estimator refuses to invent it. This Y estimate assumes the fixed payoff fixture and the one-million-count constraint; it does not generalize to arbitrary independent X and Y tasks.
5. **Z** is the original benchmark's `ARGMAX` choice, `HAWK`/`DOVE`/`TIE`, from comparing estimated X and Y. The output also supplies `z_max = max(X_hat,Y_hat)` for users interested in the numerical maximum.

### Confidence and validation

The estimator reports a **heuristic**, explicitly uncalibrated uncertainty from bank width, center disagreement, and shape mismatch, with no free learned coefficients. `confidence_score_heuristic` is not a calibrated probability of correctness. The independent held-out labels are read **only after predictions have been constructed** to measure actual X/Y absolute and relative errors, the fraction within 5%, the Z/ARGMAX choice accuracy, and the empirical fraction whose true X lay within the uncertainty proxy. All predictions, outliers, and abstentions count in the overall coverage summary; accuracy is reported **conditional on issuing an estimate**, alongside estimate coverage.

**Upstream benchmark limitation:** the existing `011` frozen feature capture may expose the oracle Y to the frozen model, as acknowledged by its source. Our deterministic bank reconstructor does not read oracle targets, but the complete pipeline is **not independently proven to be blind to Y**. This should not be presented as independent, fully blind X/Y inference or as a separately learned Y bank. The estimator is a nontrainable diagnostic of the current 21-bank X representation for the fixed fixture.

### What will and won't improve

If the banks sharpen and center their Gaussians correctly, the X estimator's **held-out error** should decrease. There is no guarantee of monotonic improvement: an imperfect or conflicting narrow bank can dominate, overflow support may be absent, and shared-feature biases remain correlated. The monitor will show this honestly. Because existing `probe-step-*.json` files store aggregate shape metrics but **not complete 131-way outputs for each instance**, the deterministic estimates cannot be retrospectively reconstructed for earlier steps without their corresponding bank-weight checkpoints. Re-run `--deterministic-test` after each training checkpoint and preserve the JSON output if a longitudinal numerical-estimator curve is desired. No training changes are required.
