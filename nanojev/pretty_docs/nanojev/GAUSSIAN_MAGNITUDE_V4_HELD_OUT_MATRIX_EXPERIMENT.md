# V4 Gaussian magnitude banks with restored continuous location learning

## Invariant

The output numerical value must be reconstructed from **only adjacent Gaussian magnitude bank weights**, driven by a TRAIN-supervised continuous **signed numerical location**, plus a separately learned exact-negative/zero/positive class. No inference input may use oracle X/Y. No high-magnitude bank may contribute probability outside the local neighborhood.

## Why this lineage exists

The original signed Gaussian learned two continuous numerical locations and achieved approximately 61% weakest DEV joint X/Y within 5%. Switching to gamma/chi-squared introduced separate sign/zero and nonuniform small-dense magnitude centers, but subsequent models lost the continuous signed-location supervision, and unrestricted bank softmax tails corrupted small numerical magnitudes. The V3 experiment peaked at only ~24.48% weakest DEV; these are research measurements, not acceptance.

V4 is a **new** checkpoint lineage, preserving existing V1/V2/V3. It combines:

- Gaussian (not gamma/chi-squared) kernels on unsigned magnitude only;
- 2,000 monotonically increasing magnitude bank centers on a power-biased logarithmic axis, with dense coverage below 1;
- an original-style **continuous signed numerical location predictor** and direct TRAIN signed-location MSE supervision;
- at most two active magnitude banks adjacent to the proposed absolute magnitude; their Gaussian mixture analytical mean determines the unsigned numerical output;
- an independent negative/EXACT-ZERO/positive class per output; zero is exactly 0, not a small Gaussian;
- 96 TRAIN / 24 DEV / 24 TEST disjoint payoff matrices, oracle-blind prompt features, T01B–T08, two masks, and the unchanged >=80% both X/Y within 5% **per tier**, aggregated over independent matrix/count cases and requiring every mask to pass.

### Numerical calculation

`loc_signed = tanh(continuous_head(features)) * max_magnitude`.

The proposal magnitude `abs(loc_signed)` activates only its left and right bracketing Gaussian bank centers. Weights are linear *in numerical magnitude*, and sum to one; the numerical mean is computed from the activated banks' analytical Gaussian means. In contrast to the V3 unrestricted softmax, a tiny amount of weight at a million-scale bank cannot corrupt a 1-scale answer. The basis contains normalized Gaussian bin masses for diagnostics and a modest TRAIN density penalty.

`numeric = categorical_sign * sum(active_bank_weights * gaussian_bank_means)`.

Training optimizes the original-style signed normalized-coordinate MSE and sign/zero CE as primary losses, with a small Gaussian density KL term. An independent unsigned log-magnitude gradient was deliberately removed: the test showed it can encourage a wrong-signed continuous location because it is insensitive to sign, despite the categorical sign head making the final output look correctly signed.

**Limitation:** recovering a signed **linear** location over ±5 million can still be numerically weak near zero. Dense magnitude bank spacing does not by itself make a continuous coordinate predictor accurate. This remains a hypothesis to evaluate, not a certified solution.

## Preflight and training

```shell
python -m pytest -q tests/test_nanojev_hawk_dove_gaussian_magnitude_blind_matrix.py
python -m tools.nanojev_hawk_dove_gaussian_magnitude_blind_matrix inspect-banks --banks 2000 --grid-bins 512 --min-mag 0.00001 --density-power 1.65
python -m tools.nanojev_hawk_dove_gaussian_magnitude_blind_matrix train --banks 2000 --grid-bins 512 --min-mag 0.00001 --density-power 1.65 --device cuda --steps 20000 --probe-every 500 --train-matrices 96 --dev-matrices 24 --test-matrices 24 --counts-per-matrix 16 --masks 2 --tolerance 0.05 --min-rate 0.80 --output C:\\Users\\subsi\\NanoJev\\runs\\hawk_dove_gaussian_magnitude_2000_blind_matrix_v4
```

Use a **new** output directory; checkpoints have a V4-specific schema and cannot reuse V1/V2/V3 files. TEST is evaluated only after the best DEV checkpoint is frozen. After inspecting TEST once, it is no longer a fresh holdout for follow-on tuning. To diagnose DEV without retraining:

```shell
python -m tools.nanojev_hawk_dove_gaussian_magnitude_blind_matrix diagnose --checkpoint C:\\Users\\subsi\\NanoJev\\runs\\hawk_dove_gaussian_magnitude_2000_blind_matrix_v4\\best.pt --device cuda --splits dev --output C:\\Users\\subsi\\NanoJev\\runs\\hawk_dove_gaussian_magnitude_v4_diagnosis
```

### Checks

1. Gaussian bank mass normalized and small-dense geometry unchanged.
2. Oracle local Gaussian population reconstructs from `1e-5` through millions, with no distant-bank weights.
3. Continuous predictor influences numerical outputs **through** local bank activations, and receives nonzero gradients.
4. Exact zero is categorically represented, with no zero Gaussian.
5. Small mixed-sign, mixed-magnitude dataset can actually fit within the acceptance tolerance.
6. CPU TRAIN → checkpoint → AUDIT → prediction → diagnosis reproduces the DEV gate and preserves oracle-blind inputs.

These are source/CPU smoke checks, **not** validation of 20,000-step CUDA performance or held-out certification. The historical Gaussian baseline remains the benchmark.
