# NanoJev 21-bank deterministic hard-gate ablation

The Gaussian-bank monitor already evaluates a frozen checkpoint without training another decoder. Its baseline estimator treats sufficiently strong LHS/RHS bank predictions as **hard bounds**. A confidently wrong overflow prediction from one bank can therefore eliminate good interior center estimates from other banks.

This addition does **not** replace, clip, or hide the baseline estimator's predictions. It adds an optional, fully read-only paired control:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_bank_21_monitor `
  --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_bank_21_direct_v1" `
  --deterministic-test `
  --gate-ablation `
  --device cuda `
  --samples-per-band 6 `
  --show-cases 12
```

- **Original (hard)**: the unchanged bank gates and hard overflow exclusions; all original metrics and sample cases remain present under `deterministic_test`.
- **Shadow (ignore hard bounds)**: identical bank distributions, interior on/off thresholds, Gaussian centers, shape scores, weights, robust weighted mean, and uncertainty calculation. It ignores only hard LHS/RHS bounds when deciding which interior estimates are viable. It retains and reports the conflicting overflow evidence. This is a diagnostic ablation, **not** a proposed production decision rule.
- `gate_ablation.strict_original` and `gate_ablation.shadow_without_hard_bounds` report full sample counts, accuracy, errors, and abstentions. `shadow_by_x_band` preserves each magnitude range.
- `strict_overflow_bounds_contradicted_by_oracle` is evaluated **after** inference, solely for debugging; it counts cases in which a hard bound would exclude the true X. Truth never enters the inference or bank selection.
- The paired counters `shadow_absolute_error_improved`, `shadow_absolute_error_worsened`, and `shadow_error_tied` count all cases; abstentions count as errors with infinite absolute error for this paired ordering. These counters add up to `paired_cases`.
- The opt-in adds no trainable parameters, performs zero gradient updates, and leaves the original run files and checkpoint unchanged.

Important limitations remain unchanged: the legacy upstream feature path may expose the oracle Y via the `011` visibility channel. In this fixed fixture, Y is algebraically derived from estimated X, not independently inferred by its own banks; easy Z accuracy is not evidence of general game-solving. Uncertainty scores are heuristics, not calibrated probabilities.

This comparison identifies whether **hard-bound exclusion** is causing avoidable failures, without implying that removal of bounds must improve the result. If the shadow is worse, that is reported with the same rigor as improvement. The proper fix, if evidence supports it, would need to weigh overflow votes against competing interior evidence rather than quietly ignoring them.
