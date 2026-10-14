# NanoJev: operation decoder + executed-result supervision (v1.2 integrated cutover)

This is an **additive experiment**. It adds/replaces the independent cutover trainer, its tests, and this guide; it **does not modify** `nanojev_register_online_hierarchy_buffered.py`, the running 200,000-step process, or its checkpoints.

## Architecture

1. Initialize the existing 38-head numeric register decoder from a sealed **read-only snapshot** of the buffered experiment's `best.pt` (or `latest.pt`). Both decoder weights **and** the original training stream/replay bank contents are copied. A new optimizer is constructed. The source checkpoint SHA-256, original step, and new stream-fork seed are recorded in the new run.
2. Add a separate 20-class operation-template classifier from the same frozen Qwen3-0.6B hidden-state activations. These are the 20 existing v3 *training* operations. The four deliberate control-only operations (`written_average`, `algebra_balance`, `conditional_abs`, `multistep_square`) remain held out and are never trained. The model does **not** use the true operation during inference.
3. Map each predicted template to a canonical program: `ADD`, `SUB`, `RSUB`, `MUL`, `ABSDIFF`, `MAX`, `MIN`, `GT`, `IF_X_POS_Y_ELSE_X`, `TWICE_X_MINUS_Y`, or `DOUBLE_SUM`. Equivalent English/mathematical templates share the same canonical program. The reference executor also implements the four held-out programs for diagnostic scoring.
4. Compute the actual result by executing the selected program over the reconstructed X and Y numbers. There is **no neural result-prediction head**. The differentiable training version executes **every candidate program independently**, computes **a separate error for every candidate**, and uses softmax probabilities to weight the errors. This prevents incorrect candidate results from cancelling one another in an averaged output. It also directly supervises the true operation's executed error (oracle-operation training branch). Inference is strictly argmax + deterministic execution.
5. Supervision jointly includes the original 38-head loss, cross-entropy for operation classification, teacher-bucket executed-result loss (both oracle-operation and candidate-weighted), and a weaker fully differentiable *soft-routing* executed-result loss (again both oracle and candidate-weighted). Numeric loss uses target-anchored signed-asinh scaling. Boolean operations use BCE-with-logits on comparison margins normalized by the **true operands**, preventing saturation with reversed million-scale operands. The teacher-bucket signal is confined to training and diagnostics; predicted routing and the argmax operation are used during inference. The soft-routing term propagates result-error gradients into sign/bucket classifiers as well as magnitude experts.
6. Store checkpoints, replay buffers, optimizer and RNG state **only under the new results root**. `--resume` operates exclusively on its own `latest.pt`.

The v3 generator provides symbolic, code-like expressions (`abs`, `max`), written English, algebra, conditional and multi-step formulations. It is **not yet a comprehensive programming-language-code benchmark**. The current task distribution cannot substantiate broad code syntax transfer.

## Launch (PowerShell, from `C:\Users\subsi\main_computer`)

Keep the old run unchanged. The source checkpoint is copied at the instant this command initializes the new experiment. Use the `NanoJev` environment with PyTorch, Transformers and NumPy installed:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_register_operation_result_cutover.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_operation_result_cutover_v1_2" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --constraint-stage continuity `
  --continuity-weight 0 `
  --batch-size 16 `
  --buffer-capacity 256 `
  --per-expert-batch 16 `
  --expert-weight 2 `
  --replay-updates 2 `
  --op-width 96 `
  --op-weight 0.3 `
  --result-weight 0.12 `
  --routed-result-weight 0.02 `
  --steps 3000 `
  --eval-every 100 `
  --checkpoint-every 100 `
  --report-every 50 `
  --lr 0.0003 `
  --device cuda `
  --dtype float16 `
  --threads 2
```

The new step counter starts at 0 because the *new op/result training regime* starts at 0. The `source_step` remains recorded in every progress log. Source `best.pt` can continue to change as the original run progresses; it is never reloaded on `--resume`.

To continue this new experiment to 20,000 new steps, repeat the command with `--steps 20000 --resume`. The original read-only `--source-checkpoint` path must remain the same in the command, but its contents are not re-read. The new trainer's checkpoint retains the precise initialized weights and source SHA-256.

For a quick offline check without loading Qwen:

```powershell
& $py .\tools\nanojev_register_operation_result_cutover.py --self-test
& $py -m pytest -q .\tests\test_nanojev_register_operation_result_cutover.py
```

To evaluate the **new** `latest.pt` checkpoint on the full reserved validation, test and control capture splits, without loading Qwen or generating new samples, repeat the launch arguments (same config) with `--resume --eval-only`; output includes `op_cutover_final_evaluation` events. Evaluation on test should ideally be performed after checkpoint selection rather than repeatedly.

## Observable metrics

`op_cutover_validation` contains:

- `op_template_accuracy` on **supported** operation templates (the four held-out control-only templates are excluded from this denominator), plus `op_program_accuracy` for canonical equivalence.
- `result_5pct_pass_rate` computed from hard-routed X/Y and **predicted** operation; boolean results require exact 0/1 classification, numeric outputs use 5% relative tolerance with 1-unit absolute floor.
- `result_given_true_operation_pass_rate`, isolating operand errors; `result_given_true_operands_pass_rate`, isolating operation errors. These are **diagnostic oracles**, not inference scores.
- `x_5pct_pass_rate`, `y_5pct_pass_rate`, `joint_xy_5pct_pass_rate`, `true_bucket_expert_normalized_mae`, and per-register regime accuracy, comparable with the original 38-head validation set.
- Breakdown by operation family and eight example results. `unsupported_heldout_operation_rows` identifies control rows whose operation class was not available during training.

Operation semantics used by the deterministic executor:

| Template | Program |
| --- | --- |
| `sym_add`, `written_add`, `algebra_difference` | X + Y |
| `sym_sub`, `written_sub`, `algebra_left`, `algebra_balance` | X − Y |
| `sym_reverse_sub`, `written_difference`, `algebra_right` | Y − X |
| `sym_multiply`, `written_product` | X × Y |
| `sym_absdiff` | abs(X − Y) |
| `sym_max` | max(X,Y) |
| `conditional_min` | min(X,Y) |
| `written_greater`, `conditional_gt` | int(X > Y) |
| `conditional_positive` | Y if X > 0 else X |
| `multistep_double`, `multistep_difference` | 2X − Y |
| `multistep_combine` | 2(X + Y) |
| `written_average` (held out) | (X + Y)/2 |
| `multistep_square` (held out) | (X+Y)² |
| `conditional_abs` (held out) | 1 if abs(X) ≥ abs(Y), else 0; 1 means X |

**Explicit convention:** The v3 written phrase “Find the difference between Y and X” is treated as *ordered subtraction Y − X*, not absolute difference. This wording is linguistically ambiguous, so confirm or revise that label semantics in a subsequent benchmark revision before using the learned operation vocabulary as a universal natural-language operator grammar.

## Future full-head handoff

`predict_batch(network, frozen_feature_matrix, device)` returns structured dictionaries with fields:

```json
{"x":3847,"y":2916,"operation":"sym_multiply","program":"MUL","operation_probability":0.99,"result":11217852,"result_kind":"number"}
```

The JSON above shows the **schema with illustrative correct values**, **not** the output of a trained checkpoint or a claimed observed prediction. It can be passed to a future full LM head. The executor gives an exact result **given the extracted operands and operation**; its correctness still depends on those upstream extractions. Until the operation head is trained, operation accuracy and executed-result accuracy are unknown.

### Training safety

- Original benchmark validation, test and control captures are only read, never inserted into replay.
- Original buffered checkpoint is read into an immutable byte snapshot; old replay is cloned into the new run without any source mutation.
- Fresh generated numerical pairs inherit the original stream's reserved-pair set and seen-pair history, while deterministically forking its RNG, preventing reuse of the benchmark pairs, avoiding duplication of already-generated source pairs, and diverging from the simultaneously running source stream.
- This is **not** a Qwen fine-tune: frozen-backbone trainable parameter count remains zero.


## V1.2 integration fixes (relative to the v1 patch and v1.1 review)

- **Complete cutover, no external adapter or JSONL**: the concrete `nanojev_register_online_hierarchy_buffered.py` interfaces, saved 38-head weights, replay banks, generator, frozen Qwen activations, validation captures, and optimizer/checkpoint handling are wired in directly.
- **Non-cancelling operation loss**: every candidate is executed and scored separately against the reference result; incorrect candidate outputs cannot cancel each other in a weighted-average prediction. The direct requested-operation loss also backpropagates to X/Y even if the operation classifier is initially wrong.
- **20 trainable / 24 defined template operations**: all 24 actual v3 template names have deterministic semantics. Four v3 control-only templates remain absent from training, with their test rows explicitly counted. **`written_difference` is Y − X** because the benchmark says “difference between Y and X”; `algebra_difference` is X + Y from `Z − X = Y`.
- **Boolean results**: strict greater-than and magnitude comparison use integer 0/1 at inference. During training, normalized comparison logits receive BCE loss with target-dependent operand scaling instead of a result-scaled Huber loss; gradients remain nonzero for wrongly ordered ±1,000,000 inputs. Equality at the decision boundary remains a discontinuity at exact inference, so direct X/Y supervision stays enabled.
- **FP32 differentiable candidate evaluation, exact Python integer execution**: training uses FP32 arithmetic for practical GPU throughput on the benchmark's ±2M input range; the inference/reference executor computes integer operations in Python, avoiding fixed-width integer overflow for products/squares.
- **Isolated v1.2 results and schema**: `--results-root` defaults to `~/NanoJev/runs/register_operation_result_cutover_v1_2`. A preexisting `v1` checkpoint is deliberately not accepted as a `v1.2` resume; initialize once from the **buffered** `best.pt` instead.
- **Independent checkpoint selection**: `best.pt` tracks the best computed-result recovery (ties broken by template accuracy); `latest.pt` supports exact continuation. Existing buffered checkpoint files are only read and hashed, never modified.

### Important experimental limitations

The 20-way classifier predicts **surface templates**, not a freely generated arbitrary-length program. Some different templates map to a shared canonical arithmetic operation. The four held-out templates are intentionally *not* among the classifier's selectable labels; scoring on those rows measures out-of-vocabulary transfer only indirectly. Boolean results are scored as exact 0/1, numeric results by 5% or a 1-unit absolute floor. The result executor is exact **conditional on correct operand and operator extraction**; an executed result alone does not fix upstream extraction errors. Repeatedly using the same 512-row validation selection inflates peak best-checkpoint estimates, so reserve the test split for a final frozen-checkpoint audit.

### Tests

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_register_operation_result_cutover.py --self-test
& $py -m pytest -q .\tests\test_nanojev_register_operation_result_cutover.py .\tests\test_nanojev_operation_result_v12.py .\tests\test_nanojev_register_online_hierarchy_buffered.py
```

On the preparation environment, **82 tests passed**, including an isolated mocked frozen-backbone end-to-end cutover, checkpoint restore, and non-mutating source-checkpoint hash verification. Full Qwen/CUDA training is not simulated by those tests; the launch command is the GPU experiment.
