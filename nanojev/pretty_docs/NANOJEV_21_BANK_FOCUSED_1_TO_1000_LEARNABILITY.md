# NanoJev: focused learnability of values 1–1,000

## Why this variant exists

The earlier nested-90 learnability runner produced 512 valid numerical instances per tier but **selected only 32 for actual fitting**. Only approximately 2 instances per tier fell into each of the numerical bands 1–10, 10–100, and 100–1,000. This invalidated the intended test of numerical precision. The new experiment targets these three magnitude bands directly and does not reduce the fit set.

## Exact training contract

- All 21 zero-centered Gaussian banks, radii, registers, exact left/right overflow supervision and the shared numerical decoder remain unchanged.
- In the default experiment, **900 distinct oracle-verified numerical instances per tier** are used for fitting: 300 in `1 <= |X| < 10`, 300 in `10 <= |X| < 100`, and 300 in `100 <= |X| < 1000`.
- **180 different held-out numerical instances per tier**, 60 in each band, are used for development. Training and held-out counts are disjoint, and all nine T00–T08 prompt forms are independently certified by the existing exact Decimal Hawk–Dove oracle.
- Instances use fractional expected encounters, with the original payoff coefficients and encounter total unchanged. This is a research distribution, not a change to the original integer teacher.
- Every training optimizer minibatch draws from each of the three bands and cycles through the nine tiers. At default `--views 32`, each band occurs at least 9 times per batch.
- Default `--learnability-train-per-tier 0` means **use ALL focused generated examples**; any nonzero value fails closed. No implicit small subset, no original-task replay, no zero-heavy nested-90 fraction in the optimizer. This isolates learnability in the requested interval.
- Accuracy changes on the old integer dev set are **recorded, not a reason to abort exploratory training**. Non-finite loss/gradient/parameters abort. Promotion to deployable best remains protected and distinct from research-only checkpoints.
- Separate output and schema ensure neither the old joint decoder nor its learnability run is altered. `--decoder-init-checkpoint` imports the existing 21-bank checkpoint read-only, then resets the optimizer for the new experiment.

## Audit without loading the model

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21_focus_1k `
    --mixture-audit-only `
    --mixture-train-per-tier 900 `
    --mixture-new-dev-per-tier 180
```

If any magnitude band is impossible under the oracle, the audit says so and full training refuses to start.

## Run a learnability probe

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21_focus_1k `
    --device cuda `
    --relative-max 2000000 `
    --decoder-init-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_v1\latest.pt" `
    --mixture-train-per-tier 900 `
    --mixture-new-dev-per-tier 180 `
    --learnability-train-per-tier 0 `
    --experiment-arm decoder-only `
    --retention-fraction 0 `
    --additional-steps 3000 `
    --max-steps 30000000 `
    --chunk-steps 250 `
    --views 32
```

Re-run the identical command with a new `--additional-steps` value to continue from `latest.pt`. The maximum is a budget, not a requirement to train all those steps. Run directory: `~/NanoJev/runs/hawk_dove_learnability_focus_1k_v1/` (Windows resolves under the user's profile).

## Monitor numerical learnability

```powershell
& $py -m tools.nanojev_hawk_dove_focus_1k_monitor `
    --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_learnability_focus_1k_v1"
```

For **each of the three focused bands**, every probe includes training and held-out 5%-relative numerical accuracy, mean absolute numerical error, frozen Stage 2 and zero-predictor comparisons, and a T00–T08 breakdown. The read-only monitor reports whether train and held-out error improve together, whether 5%-tolerance accuracy improves, optimizer clipping, and the best held-out MAE. Ordinary signed-asinh error falling without improved numerical precision does not count as resolving the band.

Train and held-out sets deliberately share the same format and statistical distribution, but never their underlying expected numerical instances. The untouched original integer test split is never read during training or this monitor.
