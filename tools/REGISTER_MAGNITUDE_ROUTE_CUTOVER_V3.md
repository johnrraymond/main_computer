# NanoJev: magnitude-route correction cutover v3

## Scope

This patch adds **one new experiment**, without changing v1.2, continuous v1, or residual v2. The original Qwen3-0.6B backbone, 38-head X/Y magnitude decoder, and operation classifier remain frozen. All former checkpoints stay untouched. V3 starts directly from the **v1.2 `latest.pt`** rather than a v2 checkpoint, to permit a matched comparison and to preserve the original decoder's magnitude experts.

Additional files, merged from the `main_computer_test/` ZIP root:

- `tools/nanojev_register_magnitude_route_cutover.py`
- `tools/REGISTER_MAGNITUDE_ROUTE_CUTOVER_V3.md`
- `tests/test_nanojev_register_magnitude_route_cutover.py`

Requires all previously installed experiment dependencies, especially `nanojev_register_continuous_residual_cutover.py` (v2), `nanojev_register_continuous_magnitude_cutover.py` (v1), `nanojev_register_operation_result_cutover.py` (v1.2), the source buffered decoder, and the cached register-stability benchmark. New file only; **do not replace or modify the previous trainers**.

## Why it is different

In v2, the trainable signed-asinh correction sees frozen Qwen features but not the decoded **sign / zero / magnitude-boundary logits**, nor the **12 signed local expert predictions**. Small corrections reduce local error while a severely misrouted X (e.g. true 135,000 decoded as 20,000) remains near 20,000.

V3 feeds the new corrector:

1. Frozen Qwen activation vector, normalized.
2. All **38 source logits** (`2 × 19`), including zero/sign, five hierarchical continuation bits, and twelve signed expert coordinates per operand.
3. An explicitly decoded 13-way categorical source prior for each operand: zero plus positive/negative versions of each of six magnitude regimes.
4. All thirteen candidate values in signed-asinh space, using source expert geometry (zero candidate plus twelve signed bucket candidates).

The trainable network has a shared MLP and three heads:

- `route`: adjusts the 13-way log prior per operand; supervised by true sign/zero/bucket labels, with larger loss weight for incorrect frozen routes.
- `gate`: selects whether to use the alternate route (target: 1 for wrong source route, 0 otherwise).
- `fine`: a continuous signed-asinh residual for within-route magnitude refinement.

The formula is:

```
source_asinh  = asinh(source_integer)
route_value   = sum(softmax(route_logit)[k] * candidate_asinh[k])
correction    = fine + tanh(gate_logit) * (route_value - source_asinh)
corrected     = sinh(source_asinh + correction)
```

All three output projections are initialized **exactly to zero**. Therefore the initial correction is zero **even though the route prior is not uniform**: step-zero predicted integer operands and calculated results reproduce v1.2 exactly. This is enforced during startup, and baseline checkpoints are saved at step zero before training.

The new heads can move `20,000 → 135,000` without the source's bucket clip and can also correct signs/zeros. The training path is continuous; only inference rounds final corrected values to integers.

### Route-specific supervision

The total training loss contains:

- v2 signed-asinh direct operand target and small residual-anchor losses;
- weighted 13-class cross entropy targeting **true route**, with `--wrong-route-weight` applied to mistakes made by the immutable source;
- an additional signed-asinh correction loss **restricted to wrong-routed operands**, weighted by `--jump-weight`;
- gate supervision, targeting 1 on wrong-route operands and 0 otherwise;
- optional exact-operation and candidate-result backpropagation in the `result` branch (absent in `direct`).

Training labels enter the **loss only**, never the forward pass or inference. All frozen source heads stay frozen.

## PowerShell launch

Extract into `C:\Users\subsi\main_computer`, maintaining `tools/` and `tests/` under the `main_computer_test/` wrapper. In your existing NanoJev Python environment:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py .\tools\nanojev_register_magnitude_route_cutover.py --self-test

$common = @(
  '--v3-root', "$env:USERPROFILE\NanoJev\runs\register_stability_v3",
  '--results-root', "$env:USERPROFILE\NanoJev\runs\register_magnitude_route_cutover_v3",
  '--source-checkpoint', "$env:USERPROFILE\NanoJev\runs\register_operation_result_cutover_v1_2\latest.pt",
  '--constraint-stage', 'continuity',
  '--batch-size', '16', '--buffer-capacity', '256',
  '--per-expert-batch', '16', '--replay-updates', '2',
  '--op-width', '96', '--route-width', '128',
  '--magnitude-weight', '1', '--anchor-weight', '0.01',
  '--route-weight', '0.35', '--wrong-route-weight', '5',
  '--jump-weight', '1', '--gate-weight', '0.1',
  '--result-weight', '0.12', '--candidate-weight', '0.12',
  '--warmup-steps', '200', '--steps', '3000',
  '--eval-every', '100', '--checkpoint-every', '100',
  '--report-every', '50', '--lr', '0.0003',
  '--device', 'cuda', '--dtype', 'float16', '--threads', '2'
)

# A: route correction with direct X/Y supervision
& $py .\tools\nanojev_register_magnitude_route_cutover.py @common --branch direct

# B: identical examples + numerical operation-result gradients
& $py .\tools\nanojev_register_magnitude_route_cutover.py @common --branch result
```

The two commands may run separately; each branch forks the **same source v1.2 checkpoint**, replay buffer, and generator RNG. They do not start from or write the v2 experiment's checkpoints. Running v3 will not interrupt ongoing v2 training, but concurrent CUDA workloads compete for GPU memory.

For continuation after interruption, use the original branch launch command with `--resume`, without changing other configuration flags. Increasing only `--steps` is supported. Checkpoint configurations, backbone revision, and source architecture are validated.

Offline evaluation (runs on the cached validation/test/control activations, without loading Qwen):

```powershell
& $py .\tools\nanojev_register_magnitude_route_cutover.py @common `
  --branch result --resume --eval-only --eval-checkpoint best-route
```

Prefer to defer `test` and `control` evaluation until the final model is selected.

## Output and success criteria

Branches write into distinct folders under `register_magnitude_route_cutover_v3/`:

- `latest.pt`: every checkpoint / final state
- `best.pt`: highest numerical-only result recovery
- `best_joint.pt`: highest 5% joint X/Y recovery
- `best_route.pt`: highest 5% operand recovery among operands **whose frozen source route was wrong** (the experiment-specific objective)

The startup check protects the source-equivalent step-zero model in all best checkpoints. Recorded fields include:

| Metric | Question answered |
|---|---|
| `source_wrong_route_operands`, `source_wrong_route_rows` | How many input operands the frozen decoder misroutes |
| `corrected_route_top1_accuracy` | Whether route classification predicts the true signed bucket |
| `corrected_route_accuracy_on_source_wrong` | Whether the new classifier fixes the frozen classifier's mistakes |
| `source_wrong_route_operand_5pct_recovery` | **Primary route-repair metric**: corrected X/Y accuracy for wrong-source-route operands |
| `source_wrong_route_operand_5pct_baseline` | Frozen decoder accuracy on precisely that same subset |
| `source_correct_route_operand_5pct_recovery` | Ensures good inherited predictions aren't corrupted |
| `severe_source_error_operands` | How many source values differ by ≥ ln(2) in signed-asinh distance |
| `severe_source_error_operand_5pct_recovery` | Whether large original magnitude mistakes are repaired |
| `mean_gate_source_wrong`, `mean_gate_source_correct` | Whether the jump gate activates preferentially on misroutes |
| `numeric_result_5pct_pass_rate`, `joint_xy_5pct_pass_rate` | Overall application-level outcomes |
| `boundary_snap_count`, `source_boundary_snap_count` | Whether old boundary snapping persists |

**Do not interpret fewer boundary snaps alone as recovered arithmetic accuracy.** Evaluate the corrected-route subset, numeric-result pass rate, joint X/Y, and source-correct-route preservation together. The original v1.2 checkpoint measured **19.57%** numeric-only result recovery and **5.86%** joint X/Y recovery on the fixed 512-row validation (465 numeric). These are baseline values, **not predictions of v3 performance**.

## Verification status

The new `--self-test`, a synthetic forced-route correction of 20,000 toward 135,000, an end-to-end mocked CLI test (both branches, resume, offline evaluate), source SHA256 preservation, and existing regression tests passed on CPU. This package **was not run for 3,000 CUDA steps**; full training must be performed on your workstation against its frozen-Qwen cache.
