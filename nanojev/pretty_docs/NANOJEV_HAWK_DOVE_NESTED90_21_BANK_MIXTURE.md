# NanoJev Hawk–Dove: 21-bank nested-90 training mixture

## Scope

This is **a new training lineage**, not an in-place change to the existing 21-bank experiment. It keeps the fixed zero-centered 21-bank Gaussian system, its 129 interior registers per bank, dedicated overflow buckets, shared decoder, and numerical-first gradient balancing. The original Stage 2 parent, original teacher SQLite, original development cases, and donor checkpoints are not modified.

Two new modules:

- `tools/nanojev_hawk_dove_nested90_mixture.py` constructs and verifies the mixture.
- `tools/nanojev_hawk_dove_gaussian_joint_decoder_21_nested90.py` trains the existing 21-bank system using the new mixture.

## Distribution actually generated

For **absolute oracle target X**, not the raw count `a`, the requested cumulative probabilities are

| Condition | Expected percent of presentations |
|---|---:|
| `abs(X) < 1_000_000` | 90.00% |
| `abs(X) < 100_000` | 81.00% |
| `abs(X) < 10_000` | 72.90% |
| `abs(X) < 1_000` | 65.61% |
| `abs(X) < 100` | 59.05% |
| `abs(X) < 10` | 53.14% |
| `abs(X) < 1` | 47.83% |

The generator first assigns quotas to the eight **mutually exclusive** bands, then chooses valid `a` values, sets `b = 1_000_000 - a`, obtains both numerical targets from the **original fixed-payoff oracle**, and certifies canonical T00–T08 prompts (including LogBar mutations). The same quota distribution is rendered in each tier. The trainer draws uniformly from this distribution per tier; it does **not** apply the old 50% magnitude-band balancing sampler, which would undo the requested distribution.

### Mathematical restriction — read before running

The original rules require **integer encounter counts and integral numerical targets**. Consequently, `abs(X) < 1` means **exactly `X == 0`**, not a continuum of tiny values. With fixed payoffs, `X = a * HAWK_HAWK_PAYOFF + (1_000_000 - a) * HAWK_DOVE_PAYOFF` is affine in `a`. Unless X is identically zero, there is **at most one distinct integer-count instance** for which X is zero. That instance might also be held out in the old dev/test split, in which case it cannot be used in training.

The new generator therefore:

1. Audits the available **distinct** oracle instances for each magnitude band, excluding all original development/test encounter counts to prevent leakage.
2. Fails closed when a requested band contains **no** valid non-held-out instance.
3. For nonempty but undersupplied bands, fills the exact quota with replacement, tries different certified LogBar mutation masks, **and explicitly reports the number of duplicated underlying numerical instances**. This delivers the requested prompt mixture, but **does not falsely claim these repeated small-X values are new numerical information**.
4. Offers `--mixture-strict-unique` to reject *any* duplicate-instance quota rather than perform replay. Under the present fixed-payoff integer rules, this will generally reject a meaningful `< 1` quota.
5. By default, **fails closed** if a requested exclusive magnitude band has *zero* valid inputs. Optionally use `--mixture-reallocate-empty-bands` to move only that impossible band's quota to the **nearest smaller reachable magnitude band** (or nearest larger band if no smaller one exists). The audit reports every transfer and the *actual* cumulative percentages; this fallback does **not** claim the original exact distribution was achieved.

A truly diverse sub-unit curriculum requires **changing the valid numerical problem space** (for example, explicitly allowing fractional encounter weights and revising the exact teacher oracle, compiler, and upstream parent), which is intentionally **not** part of this patch.

## Step 1 — audit without loading Qwen

From the repository root with the existing Python interpreter:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21_nested90 `
    --mixture-audit-only `
    --mixture-train-per-tier 512
```

The audit prints `feasible`, all eight numerical-band quotas, available distinct oracle counts, `unique_instance_shortfalls`, and `unreachable_bands`. If `feasible` is false, do **not** run the exact mixture. Inspect the report. You may opt in to the following auditable relaxation **only if you accept that its cumulative percentages will differ**:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21_nested90 `
    --mixture-audit-only `
    --mixture-reallocate-empty-bands `
    --mixture-train-per-tier 512
```

The report includes `distribution_exact`, `empty_band_transfers`, and `actual_cumulative_percent_below_threshold`. Never relabel or alter the oracle to fake a missing band. The payoff fixture must match the original teacher manifest SHA-256.

## Step 2 — run first 1,000 training steps

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21_nested90 `
    --device cuda `
    --relative-max 2000000 `
    --decoder-init-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_v1\latest.pt" `
    --mixture-train-per-tier 512 `
    --mixture-reallocate-empty-bands `
    --additional-steps 1000 `
    --max-steps 30000000 `
    --chunk-steps 1000 `
    --views 32 `
    --gate-percent 40
```

Omit `--mixture-reallocate-empty-bands` if the exact-mode audit reports `feasible: true` and you want strict fidelity. This initializes **all 21** banks and the full shared decoder from the existing 21-bank checkpoint, **resets the optimizer**, and saves to `NanoJev/runs/hawk_dove_gaussian_joint_decoder_21_nested90_v1`.

The default `--mixture-new-dev-per-tier 0` leaves the old frozen development benchmark as the evaluation baseline. You may request additional independent nested-mixture held-out instances using `--mixture-new-dev-per-tier N`, but it will **fail closed** if training and new development would have to share the only reachable small-X value. Do not contaminate development or test to force a target percentage.

`nested90-feasibility.json` records the numerical support, quotas, duplicates, and underlying original-train reuse. Probe JSON records old-dev and training performance separately, with optional new-dev performance. `--mixture-strict-unique` is a supported check, not a route around oracle arithmetic.

## Resume

Use the **same** source/checkpoint/mixture parameters and `--additional-steps` for the number of *new* steps. Runtime identity includes the exact generated-training content SHA-256, fixture SHA-256, quota report, and all mixture arguments. No path automatically overwrites the previous 21-bank or 16-bank checkpoints.

## Tests

```powershell
& $py -m unittest tests.test_nanojev_hawk_dove_nested90_mixture
```

The new tests check exact quota arithmetic, oracle-certified prompts in all nine tiers, explicit numerical replay, strict-unique and unsupported-range failures, and read-only 21-bank checkpoint import with mocked CPU training/resume. They do **not** demonstrate improved learning or real GPU throughput; those require a fresh run.
