# NanoJev 21-bank expected-count: integer-retention training repair (v2)

This patch overlays the existing `tools/nanojev_hawk_dove_gaussian_joint_decoder_21_nested90_expected.py` and `tools/nanojev_hawk_dove_nested90_diagnostic.py`. It does not change the 21-bank geometry, numerical oracle, existing teacher, frozen Qwen, or donor checkpoints.

## Failure it addresses

The v1 expected-count curriculum optimized exclusively on fractional examples. The original integer-count examples were measured but never included in optimizer batches. In the observed run original development accuracy fell from 53.94% at step 0 to 1.62% by step 3,000, with negligible held-out expected-count improvement. The new v2 experiment does not resume those failing optimizer weights.

## Change

- Each optimization batch contains original integer **TRAIN** replay and independently drawn fractional TRAIN prompts. The default is 25% integer / 75% fractional (with `--views 32`, 8 + 24 examples). The nested-90% proportions are preserved **within the fractional stream**. The 90% mixture is **not** claimed for the entire blended optimizer batch.
- Original integer training rows are loaded through the existing, official read-only `prior.load_features(...,'train')` path. New expected-count rows continue through their dedicated certified capture. Neither developer benchmark nor TEST rows participate in gradients.
- All checkpoint identity fields include retention and gating parameters; v2 has its own schema and output directory, so old v1 checkpoints cannot be mistakenly resumed into this experiment. Initialize from the 21-bank v1 donor checkpoint, not the collapsed expected-count v1 `latest.pt`.
- Best-model promotion is **dual-domain**: original integer overall and Small-X accuracy must each be no more than 2 percentage points below frozen Stage 2 (configurable); held-out fractional development accuracy must **strictly exceed** the corresponding Stage 2 result. Missing evidence blocks promotion. A step-0 checkpoint can be preserved in `latest.pt` without being promoted.
- To avoid burning a long training budget during a collapse, a drop of more than 20 percentage points in original integer development accuracy terminates at the next probe with `JOINT_ORIGINAL_DEV_COLLAPSE_ABORT`, after saving that checkpoint and its report. A non-promotable `latest.pt` is for diagnostics and resume, **not** a recommended model.
- The frozen-feature audit measures both (a) exact collisions in Qwen-derived frozen feature vectors and (b) collisions in the *complete* downstream inputs (features **plus** the coarse estimate), because the latter are what the Gaussian banks consume. These are read-only checks, not a claim that all numerical precision is fixed.
- Existing magnitude-band reports and below-1 strict 5% relative accuracy remain in the probe. Bank interior coverage is now monitored against held-out fractional new development when available, instead of relying on original integer development only.
- The diagnostic monitor treats severe original-task forgetting as `FAIL`, and reads historical v1 and new v2 probe schemas. Historical missing below-1 metrics are warnings, not evidence the older training crashed.

## Install

Unzip from `main_computer_test/` into the corresponding repository paths, retaining `.md` documentation under `pretty_docs/`.

Stop v1 expected-count training before starting a new lineage. Prefer this short GPU diagnostic run:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21_nested90_expected `
    --device cuda `
    --relative-max 2000000 `
    --decoder-init-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_v1\latest.pt" `
    --mixture-train-per-tier 512 `
    --mixture-new-dev-per-tier 128 `
    --mixture-strict-unique `
    --retention-fraction 0.25 `
    --max-original-dev-drop 0.02 `
    --max-original-dev-collapse 0.20 `
    --additional-steps 1000 `
    --max-steps 30000000 `
    --chunk-steps 1000 `
    --views 32 `
    --gate-percent 40
```

Default isolated output: `C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_nested90_expected_v2_retention`. The v1 expected-count run and all donors stay unmodified.

Read-only monitor:

```powershell
& $py -m tools.nanojev_hawk_dove_nested90_diagnostic --mode monitor
```

For the original failed control, pass its directory explicitly:

```powershell
& $py -m tools.nanojev_hawk_dove_nested90_diagnostic `
    --mode monitor `
    --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_nested90_expected_v1"
```

### Interpreting the initial probe

- `joint_support.mixture.optimizer_streams` lists exact optimizer-batch stream counts. At `--views 32`, 24 fractional plus 8 integer examples is expected.
- `joint_promotion_gate.promotable:false` means **no** best checkpoint is promoted, even if new fractional accuracy rises. Inspect the `reason` field.
- `frozen_feature_resolution.new_dev` tests whether distinct decimal answers are distinguishable by the entire downstream numerical input (the frozen features and coarse estimate); actual aliases cannot be corrected just by replaying integer cases.
- `nested90_unseen_dev.magnitude_bands['7']` reports the below-1 population, absolute-floor accuracy, and strict relative 5% accuracy for nonzero X. These metrics answer different questions.
- `JOINT_ORIGINAL_DEV_COLLAPSE_ABORT` is an intentional fail-closed training stop, not a Python or CUDA crash; the last checkpoint and probe remain for analysis.

## Comparability and limitations

The existing expected-count v1 run is the no-replay control. Compare its step-indexed probes against the new retention run initialized from the same `21_v1/latest.pt` donor. The new run has a separate optimization seed stream, so comparison is informative but not a paired statistical trial. Repeating both configurations across seeds and comparing held-out accuracy is needed for stronger causal attribution.

This patch stops promoting regressions and introduces original-task replay. It does **not** prove improved generalization, eliminate source encoding collisions, or correct the frozen numeric adapter's float32 precision limit. CUDA performance and the real teacher data path still require the user's next live probe; automated smoke tests used mocked frozen-model loading.
