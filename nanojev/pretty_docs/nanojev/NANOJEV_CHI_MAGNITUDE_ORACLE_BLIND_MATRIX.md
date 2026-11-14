# NanoJev: unsigned chi-squared/gamma magnitude-bank experiment

This is **a new, independent experimental lineage**, not a modification or
resume of an existing signed-linear Gaussian checkpoint. It reuses the current
`tools.nanojev_hawk_dove_gaussian_population_blind_matrix` dataset, strict
prompt feature extractor, matrix-disjoint train/DEV/TEST partition, and the
**minimum 80% joint X/Y within 5% on every T01B–T08 tier** requirement.
T00 is not evaluable without exposing the oracle: canonical T00 contains only
X/Y answers. T01B and T02B are explicit oracle-blinded variants.

## Geometry actually implemented

The `--banks 2000` centers cover **positive magnitudes** from `1e-5` to
`1.25 * 1,000,000 * payoff_bound` (5 million when `--payoff-bound 4`).
Bank index fraction `u` from 0 through 1 maps to:

```
log10(center) = log10(1e-5) + [log10(max_magnitude)-log10(1e-5)] * u^1.65
```

The exponent greater than one deliberately crowds centers into the small end
**even relative to uniform spacing per logarithmic decade**. In particular,
the 2,000-bank default has **1,195 centers below magnitude 1**, including 686
below 0.001, and far fewer bank centers per *absolute number* as the value
grows. CLI `--density-power` controls this bias. Its effect is visible in
`inspect-banks`. These counts reflect the default geometry, not learning.

Each center is the mean of a *real positive gamma distribution*, mathematically
a scaled chi-squared distribution. Its shape is
`alpha = 1.5 + 4096 * position_on_log_magnitude_axis^7`, and its effective
chi-squared degrees of freedom are `2*alpha`. The small-magnitude banks are
right-skewed (skewness `2/sqrt(alpha)`); larger banks become increasingly
Gaussian-like in **magnitude**, not in sign. The gamma probability mass in
nonuniform logarithmic output bins is calculated by differences of the gamma
CDF, not by drawing a Gaussian curve and labeling it chi-squared. Each row is
normalized, and the kernels contribute through **local two-neighbor bank
interpolation** to a mixture magnitude density; its KL discrepancy against gamma
reference targets contributes to training loss. Unlike V1, the bank activation
preserves the requested arithmetic magnitude *by construction*: an encoder
proposal of 1,000,000 decodes to 1,000,000 rather than about 1,068,000. This
also narrows large-magnitude gamma mixtures to the actual local bank geometry.

X and Y have **independent bank weights** on the shared magnitude geometry.
Sign is a separate three-class prediction: negative, **exact zero**, positive.
The numeric value is the learned sign multiplied by the gamma mixture's exact
analytical mean. This avoids precision loss from subtracting two large signed
linear-grid coordinates. A categorical zero produces the **number 0 exactly**;
it is not a small positive gamma value made to look like zero.

No oracle X/Y is fed into inference features. The trained model's encoder sees
only counts, visible payoff bindings, and the tier indicator. During TRAIN,
labels supervise signed class, logarithmic magnitude, and gamma density.
**This changes both representation and learning objective**, which means any
performance change is not an isolated causal estimate of center spacing alone.

## Bank inventory and representation-only control

```powershell
& $py -m tools.nanojev_hawk_dove_chi_magnitude_blind_matrix inspect-banks `
    --banks 2000 `
    --grid-bins 512 `
    --min-mag 0.00001 `
    --density-power 1.65 `
    --output "C:\Users\subsi\NanoJev\runs\chi_bank_geometry.json"
```

Prints the allocation per magnitude shell, gamma shapes and skewness, and
*ideal* bank-mean interpolations for 0, 0.00001, 0.001, 0.01, 0.1, 1, 1.5, 2,
10, 1,000, and 1,000,000. Those interpolation probes use a known reference
magnitude and are **representation upper bounds**, not learned predictions.
They are never called from model inference or gradient training.

The current matrix dataset uses integer payoff coefficients and integer
opponent counts, so its nonzero X/Y oracle magnitudes are integers (at least 1).
This new geometry *can* represent subunit magnitudes, but success on fractional
Hawk–Dove targets is not yet proven without a different dataset.

## Independent 2,000-bank CUDA training

```powershell
& $py -m tools.nanojev_hawk_dove_chi_magnitude_blind_matrix train `
    --banks 2000 `
    --device cuda `
    --steps 15000 `
    --probe-every 500 `
    --train-matrices 96 `
    --dev-matrices 24 `
    --test-matrices 24 `
    --counts-per-matrix 16 `
    --masks 2 `
    --tolerance 0.05 `
    --min-rate 0.80 `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_chi_magnitude_2000_blind_matrix_v2"
```

The training loop optimizes sign cross-entropy (with extra weight for zero),
Smooth L1 error of log10 magnitude, explicit log-proposal error, and gamma
density KL. This is **not** the old signed-grid density objective. All Gaussian
baseline checkpoints, history, and source remain unchanged. DEV chooses the
best checkpoint; TEST is evaluated only after promotion is frozen. Output path
must be fresh. The existing matrix split generator is deterministic and retains
the old seed, so the held-out matrices match the previous experiment.

The signed-grid total-variation metric and the new **unsigned gamma-density**
TV have different sample spaces and **must not be compared directly**.
The per-tier strict joint X/Y within-5% percentages *are* comparable.

## Frozen certification and diagnosis

```powershell
& $py -m tools.nanojev_hawk_dove_chi_magnitude_blind_matrix audit `
    --checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_chi_magnitude_2000_blind_matrix_v2\best.pt" `
    --device cuda `
    --splits dev,test `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_chi_magnitude_2000_blind_matrix_v2\audit.json"

& $py -m tools.nanojev_hawk_dove_chi_magnitude_blind_matrix diagnose `
    --checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_chi_magnitude_2000_blind_matrix_v2\best.pt" `
    --device cuda `
    --splits dev `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_chi_magnitude_2000_diagnosis_v1"
```

`diagnose` writes `diagnosis.json` plus `dev_cases.csv` with one row per
presentation: oracle X/Y, predicted X/Y, learned unsigned mixture magnitude,
sign-class choices, absolute and relative errors, near-zero buckets, and
joint within-5% success. It verifies dataset identity against the frozen
checkpoint and makes no parameter or checkpoint mutation. `--splits test`
intentionally exposes test errors to researchers, so do not subsequently
claim those same TEST cases constitute a fresh untouched holdout.

The original blind-matrix run achieved approximately **61% DEV minimum-tier
joint within-5%** and **58% TEST minimum-tier joint within-5%**, with a strong
loss of relative numerical accuracy near zero. This is the baseline to beat.
This patch implements a geometry hypothesis and does **not** claim that an
unrun 2,000-bank CUDA experiment will achieve 80%.

## Constraints and verification

- The gamma/chi-square shape is magnitude-dependent by *design*. A larger
  magnitude alone does not mathematically cause a chi-square to become normal:
  the shape parameter (degrees of freedom) also increases.
- 2,000 bank centers are shared, but each output has its own learned magnitude
  proposal. Exactly two neighboring gamma banks are active at any nonzero
  proposal; a bank population is not 2,000 independent neural networks.
- The discrete log-magnitude grid is a sampling instrument for density loss;
  the final numerical decode uses analytical gamma means, not a coarse bin
  center on the original signed number line.
- Source contracts and CPU smoke tests cover mass normalization, relative
  bank density, sign/zero separation, gradients, strict held-out audit and
  reproducible diagnostics. The user's CUDA training run is required to
  establish empirical generalization.

## V2 regression correction

V1's fixed 0.16-decade softmax activation window caused a ~6.8% arithmetic
bias even for an oracle-perfect magnitude proposal of 1,000,000. It also made
the corresponding gamma-mixture TV error ~0.855 against a gamma target centered
at the same value. V2 selects the two bank means bracketing the magnitude and
linearly interpolates their **arithmetic** means. Across reference targets from
1e-5 through 4e6, the new oracle-center regression requires <1% numeric error
and <0.05 gamma TV. These controls measure decoder capacity, **not whether the
neural encoder has learned arithmetic**. Checkpoints from V1 are incompatible
with V2 and cannot be resumed or audited with this source. Train into a fresh
output directory. The original DEV/TEST split and scoring rules are unchanged.
