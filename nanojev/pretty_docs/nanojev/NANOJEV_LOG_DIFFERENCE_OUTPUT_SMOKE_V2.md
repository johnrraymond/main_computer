# NanoJev: learned two-component logarithmic-difference output smoke (v2)

## Question

Does learning two latent components that decode according to

\[
N=\frac12\operatorname{Log}(a^2-b^2)
\]

help NanoJev acquire correct *numerical answers* compared with an equally sized direct-output head, without giving it the answer as an input?

This is a focused research A/B. It is **not** a replacement for the unified T, HDB, Qwen, QW5, or C2 training frontiers, and no existing checkpoint is altered.

## Exact meaning of zero

- For a finite real answer `N=0`, the learned real-branch components must satisfy `a²-b²=1`.
- For **exact cancellation**, `a²-b²=0`, the logarithm has a singularity, `Re(N) -> -infinity`; this is a **null flag**, **not** an ordinary answer of zero.
- If `a²<b²`, principal analytic continuation yields `0.5 ln|a²-b²| + i*pi/2`. This is a branch-state diagnostic, not a real integer label.
- Real training therefore enforces `a²>b²`. Branch and null cases are independently tested.

The full complex logarithm does not restore information lost by taking the difference of squares; infinitely many `(a,b)` pairs decode to the same numerical output. The experiment measures whether this representation *helps learning*, not whether mathematical invertibility guarantees competence.

## Two mathematically distinct candidate decoders

The model emits two learned channels `u,v`. In the log-difference arm,

```
log|a| = S*u
D      = 0.025 + (1.50-0.025)*sigmoid(v)
log|b| = log|a| - D
N_hat  = log|a| + 0.5*log(1-exp(-2*D))
```

`S` is a fixed output scale selected at the beginning of the experiment. It is used to condition the numerical training objective, but the **decoded value itself is the unscaled answer**, exactly as proposed. `a` and `b` are stored in log magnitude space: `exp(log|a|)` is never materialized for large magnitudes. The positive gap guarantees a defined real training value and avoids NaN gradients during the smoke; **exact equality** is tested through the unrestricted reference decoder, not trained through an infinite loss.

The third matched arm, **log-cancellation**, learns the *logarithm of the tiny separation* explicitly:

```
log|a|  = S*(1.25 + softplus(u))
log_gap = -S*softplus(v)
gap     = exp(log_gap)          # mathematical definition, often unmaterializable
b       = a*exp(-gap)          # mathematical definition, often unmaterializable
N_hat   = log|a| + 0.5*log(1-exp(-2*gap))
```

When `log_gap < -14`, evaluate the correction in its asymptotic form
`0.5*(log(2)+log_gap)` (error less than approximately `4.2e-7` in log units), rather than exponentiating a vanishing separation. This preserves a learnable causal *gap* channel even when the underlying pair `a,b` becomes indistinguishable in float64. Such a candidate is explicitly **a log-gap-coordinate encoding**, not evidence that two ordinary floating-point values store the same information. It tests the user's near-cancellation mechanism more directly, while exposing the precision cost.

`log(1-exp(x))` uses a two-regime `log1p`/`expm1` implementation to retain precision near cancellation. Float64 is used for the logarithmic decode (while the trainable head and captured Qwen activations are float32). No ad hoc epsilon is added to a true zero. The exact singularity remains singular.

## Matched A/B protocol

- **Direct control:** two-channel MLP, same parameter count and initial weights as the proposed arm; numeric output `S*(u+v)/sqrt(2)`.
- **Log-difference candidate:** same MLP, outputs a positive and representable learned logarithmic component pair above.
- **Log-cancellation candidate:** same MLP, learns the logarithmic separation that could otherwise underflow. This tests whether cancellation really carries information through the second channel.
- **Source data:** authentic C2 tuples `(p,q,source_a,source_b)` from the uploaded version of `tools.nanojev_qwen_c2_compressive_train`. Their exact integer answer is `p*source_a-q*source_b`. The source operands are distinct from the candidate's learned `a,b`.
- **Training inputs:** either eight numeric unary features of those source operands (`--mode numeric`), or actual final non-padding-token hidden activations of an unchanged, frozen Qwen3-0.6B (`--mode qwen`). There are **no oracle products, precomputed answers, or labels in the input feature vector**.
- **Acquisition:** all seeded local-radius training samples participate in optimization; no held-out DEV/test split is imposed within the acquisition radius. Exact zero-line ties and positive/negative near ties are injected. A disjoint `R+1/R+2` outer shell probes how the acquired representation behaves when the radius grows.
- **Fairness:** the arms share inputs, data ordering, optimizer configuration, step count, batch indices, output scaling, network capacity, source deck, and initialization. No existing checkpoint is modified.
- **Diagnostics:** acquired and outside MAE/RMSE, rounded exact integer rate, exact-answer-zero results, signed-answer groups, paired per-example wins/losses, `b=0` ablation, shuffled-`b` ablation, learned `|b/a|`, null/branch/near-null invariants, source SHA-256 fingerprints, and full per-case CSV.

A lower MAE does **not** establish that the candidate learns arithmetic more generally. In particular, if `b` ablation or shuffled `b` barely changes the predictions, the network is primarily encoding the answer through `log|a|`, and the second component has not acquired a substantial independent role. The paired and multi-seed evidence must be examined before any wider integration.

## Windows PowerShell commands

Use the existing Python environment for Main Computer; do not install or change any dependencies solely to run the numeric smoke if PyTorch and NumPy are already present.

```powershell
# Standalone CPU smoke, no Qwen download or existing model mutation
& $py -m tools.nanojev_log_difference_smoke `
  --mode numeric --device cpu --radius 8 `
  --steps 500 --train-cap 640 --outer-count 256 `
  --output runtime/reports/nanojev-log-difference-smoke-v1

# Actual frozen Qwen hidden-state experiment (needs transformers/model weights)
& $py -m tools.nanojev_log_difference_smoke `
  --mode qwen --device cuda --model Qwen/Qwen3-0.6B `
  --radius 8 --steps 1500 --capture-batch 8 `
  --output runtime/reports/nanojev-log-difference-qwen-v1

# Deterministic re-runs to avoid relying on a single random seed
foreach ($seed in 42257,42258,42259) {
  & $py -m tools.nanojev_log_difference_smoke `
    --mode numeric --device cpu --radius 8 --steps 500 `
    --seed $seed --output "runtime/reports/nanojev-logdiff-seed-$seed"
}

# Automated source-level gates
& $py -m pytest tests/test_nanojev_log_difference_smoke.py -q
```

The Qwen mode **only captures frozen features**; it is not an end-to-end Qwen training run, and a CUDA/model-weight success cannot be certified from the CPU-only development environment.

## Acceptance and stop conditions

`status=PASS` certifies that the experiment completed and produced finite predictions with the exact mathematical preflight checks satisfied. `learning_status=ALL_IMPROVED` means all three arms improved on *acquired* examples from their own initial errors; it does **not** mean either is acceptably accurate or that log-difference wins.

To claim representation usefulness, require across multiple fixed seeds that the candidate beats the direct control on the same outside-radius examples **and** that shuffling/removing `b` meaningfully degrades its predictions. If these checks fail, report `NO_EVIDENCE_OF_USEFUL_TWO_COMPONENT_REPRESENTATION`, not success based on the algebra alone. Do not cut over NanoJev's numerical output authority without separate arithmetic-frontier, T/HDB retention, and real-host acceptance.

## Local CPU evidence (three seeds, 500 steps each)

The archived Python/PyTorch CPU run produced the following MAE on the disjoint outside-radius shell. All three arms improved on their acquired examples, but **none achieved exact arithmetic acquisition**.

| Seed | Direct control | Real log-difference | Near-null log-cancellation |
|---|---:|---:|---:|
| 42257 | 7.932 | 7.406 | 9.290 |
| 42258 | 7.663 | 7.456 | 9.094 |
| 42259 | 6.338 | 7.124 | 9.359 |
| Mean | 7.311 | 7.329 | 9.248 |

- The ordinary real log-difference arm wins two seeds and loses one; its mean is roughly equal to the control. Shuffling `b` changes MAE by only about `0.002–0.003`, so the second component adds little causal learning.
- The near-null arm's predictions depend strongly on its learned gap: shuffled-gap MAE increases by about `7–10`, depending on seed. That is genuine reliance on the gap coordinate, **but generalization is worse** in all three runs.
- The near-null arm reports 100% float64 unresolvable pairs for this configuration. This is not a conventional stored `(a,b)` numeric pair, but an exact *formal/logarithmic* latent specification until finite precision is imposed.

This is a useful falsifiable **smoke result, not success on the user's hypothesis**. It motivates investigating acquisition and representation constraints before integrating a different numeric output authority. The results do not establish a physical information theory or an advantage for complex analytic continuation.

## Known limits

This v2 experiment deliberately tests the *real-valued* output pathway. The complex principal branch is diagnosed, not trained as a separate output alphabet. The log-gap is bounded away from exact equality during finite-loss optimization; training exactly at a mathematical logarithmic singularity is not a finite differentiable objective. Source labels may exceed the representable precision of float32 for extremely large frontiers, so the smoke confines itself to the bounded C2 numeric data. The experiment does not reproduce or modify the user's other mathematical frontiers.
