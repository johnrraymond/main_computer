# NanoJev nested-90 expected-count assumption diagnostic

## Scope

This adds a **read-only** diagnostic command for the *21-bank fractional expected-count* Hawk–Dove curriculum. It does not change 21-bank geometry, the training objective, checkpoint identity, optimizer, frozen Qwen, payoff fixture, or the integer-observation teacher. It augments the expected-count runner's checkpoint probes to expose subunit performance and frozen-feature precision. A read of original TEST example counts that was unnecessary for fractional expected inputs is eliminated from this runner's mixture-generation and audit paths.

### Before GPU training

```powershell
& $py -m tools.nanojev_hawk_dove_nested90_diagnostic `
    --mode preflight `
    --mixture-train-per-tier 512 `
    --mixture-new-dev-per-tier 128
```

The preflight uses the official teacher *config and original train/dev instances*, never the TEST split. It regenerates the exact proposed mixture under the unchanged fixture; checks all nine tiers for oracle-consistent prompts; verifies 90%-inside-90% quotas; verifies uniqueness, zero and subunit coverage, and disjoint train/new-dev numeric instances; then measures precision collisions in the frozen donor's **float32 numerical operand and visible-Y adapters** without loading Qwen.

A `warn` on **float32_role_plus_visible_y_subunit_uniqueness** is expected with the current fixture: 245 distinct subunit X values can collapse to dramatically fewer float32 numeric feature vectors. That warning does **not** establish that the frozen Qwen token stream loses the same distinctions; the actual training runner now measures the captured frozen feature tensor too.

**Subunit accuracy is measured in two ways:** the repository's 5%-tolerance metric divides error by `max(1, abs(X))`, so it grants an *absolute 0.05* tolerance when `|X|<1`; the new `strict_relative_accuracy_5pct_nonzero` separately measures true relative 5% error for nonzero subunit answers.

### During or after training

The unchanged training command remains:

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

On each existing checkpoint probe the patched runner **adds**, without re-running Qwen, `magnitude_bands_original_dev`, `magnitude_bands_train`, and `nested90_unseen_dev.magnitude_bands`, each with eight exclusive magnitude bands, a genuine subunit comparison, and counts of repaired and broken cases against Stage 2. The `nested90_unseen_dev.below_one_by_tier` field separates numerical learning across T00–T08. Its `joint_probe` log also prints `new_dev_below_one` and `new_dev_below_one_strict`.

Once frozen features have loaded, `nested90-feasibility.json` and each probe's `mixture_feasibility` include `frozen_feature_resolution`: the number of genuinely distinct frozen feature vectors for distinct subunit X answers, per prompt tier, on train and new development. Exact identical frozen features with different X are an unresolvable collision for the downstream banks and head.

Use the fast monitor anytime, including while the CUDA run is executing. It **does not load Qwen, open checkpoints, edit the run, or read TEST**:

```powershell
& $py -m tools.nanojev_hawk_dove_nested90_diagnostic --mode monitor
```

For a different run directory specify `--run-dir "C:\Users\subsi\NanoJev\runs\..."`. It reads completed atomic `probe-step-*.json` files. It reports missing or malformed probes, train-vs-heldout gaps, overall and subunit regression against Stage 2 on both distributions, numerical gradient availability, Gaussian auxiliary cap, per-bank original-dev coverage, frozen feature aliasing, and flat/declining unseen-dev accuracy across recent probes. It prints structured JSON with `pass`, `warn`, or `fail` per check, and aggregate status. `--fail-on-alert` returns nonzero for `warn` as well as `fail`, convenient for scripts or manual gating.

**Statuses and interpretation:**

- `pass`: measured invariant or threshold satisfied; not a guarantee that learning will work.
- `warn`: empirical risk or an expectation not yet proven (including zero probes, which is not a crash).
- `fail`: missing required held-out measurements, corrupt probe, invalid mixture, or violated hard invariant.
- Training accuracy and original development accuracy alone **cannot** establish generalization. Always include `--mixture-new-dev-per-tier 128` so newly held-out fractional cases are measured.
- The original integer-count development benchmark remains independent; do not replace it with synthetic new development when assessing transfer to the original task.
- The original final TEST set remains untouched by this diagnostic. No full CUDA train is claimed by these tests.

A combined preflight+monitor run is also available as the default mode or explicitly `--mode both`. You can set alert thresholds with `--max-generalization-gap`, `--max-small-gap`, `--allowed-regression`, `--gradient-ratio-limit`, `--plateau-probes`, and `--min-improvement`.

## Explicit residual limitation

The preflight does not test the frozen Qwen tokenizer or features without loading the model. Frozen-feature aliasing becomes measurable during the patched runner's feature-preparation stage; the monitor reads those measurements from the subsequent probe. The 245 distinct subunit targets are a **new expected-count task**, not 245 new integer observation cases. A successful synthetic benchmark does not imply improved transfer to the original integer benchmark.
