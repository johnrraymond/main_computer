# NanoJev: continuous **residual** magnitude cutover v2

## Objective

A complete, runnable, **additive** replacement experiment that preserves the numerical precision accumulated in operation/result v1.2 while permitting a continuous magnitude correction. Unlike the standalone continuous-magnitude v1, **the new decoder starts at EXACTLY the trained v1.2 predictions** on every input, with no bootstrap training and no deterioration at cutover. Two independent branches test whether executed-result gradients improve operand recovery beyond direct supervision alone.

This patch adds three files only:

- `tools/nanojev_register_continuous_residual_cutover.py`
- `tools/REGISTER_CONTINUOUS_RESIDUAL_CUTOVER.md`
- `tests/test_nanojev_register_continuous_residual_cutover.py`

It does not edit the original buffered trainer, operation/result v1.2, continuous v1, the generator, cached benchmark, or any previous checkpoints. Install by merging the patch's `main_computer_test/tools` and `main_computer_test/tests` folders into your existing repository's `tools/` and `tests/` folders.

The v1.2 operation/result cutover, continuous v1 cutover, and original NanoJev benchmark/trainer modules must already be installed. Only v1.2's `latest.pt` is loaded as the source; **continuous v1 checkpoints are not used as the source.**

## Architecture and exact initialization

Frozen Qwen activations → **fully frozen** v1.2 `ExecutableNumeracy` → existing 38-head hard-decoded integer operands and 20-way operation logits. Alongside those outputs a **new trainable residual MLP** (8192 → 128 → 2 by default) predicts additive corrections, initialized by setting the output projection's weights and biases to exactly zero.

The signed continuous transform uses the **actual v1.2 hard-decoded integers**, not an oracle bucket or a soft mixture:

\[
\widehat{x}=\sinh\!\left(\operatorname{asinh}(x_{\mathrm{v1.2}})+\Delta_x(h)\right),\qquad
\widehat{y}=\sinh\!\left(\operatorname{asinh}(y_{\mathrm{v1.2}})+\Delta_y(h)\right).
\]

The v1.2 operation head is unchanged, and the deterministic executor uses rounded corrected operands at inference. At step zero, `delta` is exactly zero, so **the corrected rounded operands, results, operation accuracy, and boundary snaps equal v1.2's** on all validation examples. An executable guard refuses to proceed if this parity is violated. `best.pt` and `best_joint.pt` are also seeded from step zero, so a worse first optimization checkpoint never displaces the source result.

Unlike a fixed-sign correction based on `log1p(abs(x))`, signed `asinh` permits the residual to correct through **zero, negative signs, and positive signs**. An operand clamped at 20,000 or 200,000 by the source is not clamped in the residual path. There is no hard-bucket coordinate clip for the trainable correction. Training arithmetic uses float64 before the existing operation's stable numerical loss; the hard rounding is **only** for inference/evaluation. The source's decoded operands are a non-differentiable, frozen baseline: gradients flow through the new residual, not back into the legacy decoder.

## A/B training design

Both branches are initialized from the SAME read-only v1.2 source checkpoint, copy the SAME replay bank and seen-pair set, and deterministically fork the SAME generated-example stream. They use identical architecture, optimizer configuration, and numerical supervision. Only the **result-loss contribution** differs:

- `direct`: Smooth L1 against the oracle signed-asinh correction + an L2 residual anchor. No executed-result gradient.
- `result`: The exact same direct and anchor losses **plus** oracle-operation and probability-weighted per-candidate executed-result errors from v1.2. Gradients flow into X/Y residuals; source operation logits remain frozen. Result-gradient contribution ramps over the first 200 steps by default.

Both use only training replay for learning. Validation/test/control representations remain evaluation-only. Separate roots `.../direct/` and `.../result/` prevent cross-branch overwrites. The initial checkpoint SHA256, backbone identity and operation catalog are checked and recorded. Resume restores residual weights, frozen source weights, optimizer state, replay, pair-generation state, CPU/CUDA RNG, and best records. A changed schema/configuration/backbone identity is rejected.

The new network is deliberately not initialized from continuous-v1's `best.pt` because doing so would reintroduce the numerical-regression loss observed in that experiment.

## Windows PowerShell: complete launch commands

Run from `C:\Users\subsi\main_computer` after applying the ZIP. Execute each branch separately (sequentially is acceptable). Each branch has an independent optimizer/checkpoint/replay clone; both produce matched examples even when started at different times.

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
$common = @(
  '--v3-root', "$env:USERPROFILE\NanoJev\runs\register_stability_v3",
  '--results-root', "$env:USERPROFILE\NanoJev\runs\register_continuous_residual_magnitude_v2",
  '--source-checkpoint', "$env:USERPROFILE\NanoJev\runs\register_operation_result_cutover_v1_2\latest.pt",
  '--constraint-stage', 'continuity',
  '--batch-size', '16', '--buffer-capacity', '256',
  '--per-expert-batch', '16', '--replay-updates', '2',
  '--op-width', '96', '--residual-width', '128',
  '--magnitude-weight', '1', '--anchor-weight', '0.01',
  '--result-weight', '0.12', '--candidate-weight', '0.12',
  '--warmup-steps', '200', '--steps', '3000',
  '--eval-every', '100', '--checkpoint-every', '100',
  '--report-every', '50', '--lr', '0.0003',
  '--device', 'cuda', '--dtype', 'float16', '--threads', '2'
)

# Branch A: direct numerical supervision only.
& $py .\tools\nanojev_register_continuous_residual_cutover.py @common --branch direct

# Branch B: same examples + operation-result backpropagation.
& $py .\tools\nanojev_register_continuous_residual_cutover.py @common --branch result
```

**Use `@common` in PowerShell** to splice the argument array. Do not paste the `--branch` options into the PowerShell expression without the leading Python executable and script path.

The trained **source** at v1.2 step 3,000 had 5.86% joint X/Y recovery and 19.57% numerical-result recovery on its fixed validation set. These are *baseline targets*, not new result claims.

## Inspection and continuation

Self-test (requires the installed source Python modules, but not GPU or cached benchmark):

```powershell
& $py .\tools\nanojev_register_continuous_residual_cutover.py --self-test
```

To continue either branch, run the identical launch command with `--resume`. You may increase `--steps` if necessary; **do not change other flags**. To evaluate one of the saved branch checkpoints offline:

```powershell
& $py .\tools\nanojev_register_continuous_residual_cutover.py @common --branch result --resume --eval-only --eval-checkpoint best
```

Valid checkpoint selectors: `latest`, `best` (highest numerical-only result pass), `best-joint` (highest joint X/Y recovery). Offline evaluation reads cached `val`, `test`, and `control` activations and does not perform live Qwen inference. Reserve evaluation on `test` and `control` until final model choice.

Results are written to:

- `register_continuous_residual_magnitude_v2/direct/{latest.pt,best.pt,best_joint.pt}`
- `register_continuous_residual_magnitude_v2/result/{latest.pt,best.pt,best_joint.pt}`

## What to compare at identical training steps

| Metric | Interpretation |
|---|---|
| `numeric_result_5pct_pass_rate` | Primary 465-row numerical result test; excludes easy Boolean answers |
| `joint_xy_5pct_pass_rate` | Both operands recovered within 5% |
| `x_5pct_pass_rate`, `y_5pct_pass_rate` | Separate register precision |
| `global_log_mae`, `signed_asinh_mae` | Magnitude/signed-magnitude errors |
| `boundary_snap_count` | How frequently a corrected operand equals an old hard boundary |
| `source_*` | Frozen source v1.2, using identical validation examples and operation predictions |
| `operation_template_accuracy`, `operation_program_accuracy` | Ensure operation recognition was preserved |
| `families` | Error broken down by symbolic/written/algebra/conditional/multistep |

**Success criteria:** At the same step, `result` should outperform `direct` on numerical-result recovery or X/Y extraction; either branch should outperform the frozen v1.2 source on at least one metric without unacceptable regressions elsewhere. Fewer boundary snaps alone are **not** sufficient evidence of numerical improvement.

## Local verification

The new code passed its self-test and a **100-test CPU suite** comprising its own tests, the v1 standalone continuous tests, v1.2 operation/result tests, and the source buffered-trainer regression tests. The mocked CLI test creates both actual branch checkpoint trees, trains, resumes, evaluates, and checks that source SHA256 is unchanged. **Real CUDA training was not executed in this environment**; that requires your local cached activations and Qwen model.
