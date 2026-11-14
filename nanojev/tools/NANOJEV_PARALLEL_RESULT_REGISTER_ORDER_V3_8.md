# NanoJev v3.8 — alternating registration order, prompt-only cutover

## Exact experimental intervention

Previously, **every fresh training example** began:

```text
XXXXXX = 17, <same existing logbar formatting>
YYYYYY = -3, <same existing logbar formatting>
operation: XXXXXX - YYYYYY =
Answer:
```

With this cutover, the prompt for the *even-indexed* fresh example is identical,
while the *odd-indexed* example begins with the two registration lines reversed:

```text
YYYYYY = -3, <same existing logbar formatting>
XXXXXX = 17, <same existing logbar formatting>
operation: XXXXXX - YYYYYY =
Answer:
```

The actual program uses `number_repr()` for both numeric representations; this
example shows placeholders instead of rewriting that formatting. `XXXXXX` still
means A, `YYYYYY` still means B. Their operation semantics and supervised X/Y
numerical targets never change. Alternation is by **fresh-example index**, not
by optimizer-step index: 16-element batches contain eight examples in each order.
The index uses the generator's persisted `drawn` count, so it survives resume.

## What is unchanged

- Original frozen Qwen model, source checkpoint, numeric decoder architecture.
- All existing RESULT heads, FINAL heads, signed-asinh losses and parameter groups.
- **80/15/5** schedule (16 intermediary, 3 RESULT, 1 FINAL phase per 20 updates).
- Existing dataset, operation text, original benchmark captures and evaluation.
- All X/Y targets, optimizer states, replay records, stream state and RNG state
  restored from the selected original v3.7 checkpoint.
- Original v3.7 training script and its run files; this is a separate root.

This is *training-order augmentation*, not yet a dual-pass deterministic
arithmetic-average inference rule. The former can be tested without changing
the latter. Fixed-order validation stays directly comparable with v3.7. A
future paired evaluation using swapped prompt captures would measure order
invariance directly; the original benchmark alone does not establish that.

**Replay note:** The fork intentionally preserves the parent's replay buffer.
Those pre-fork activations were captured from the original ordering. Over time,
newly captured alternating examples replace them. We do not clear replay or
reset Adam; doing so would introduce additional experimental changes.

## Installation

Extract this zip into `C:\Users\subsi\main_computer`, keeping the enclosed
`main_computer_test/` directory structure; equivalently copy the new `tools/`
and `tests/` files into the corresponding project directories. Existing
`tools/nanojev_parallel_result_asinh_squared_v3_7.py` must already be installed.
No existing files are replaced by this patch.

## Self-test (no CUDA / Qwen download)

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_parallel_result_register_order_v3_8.py --self-test
```

Expected: `altreg_self_test_pass`.

## Fork and start alternating training (example: 5,000 extra steps)

Run from `C:\Users\subsi\main_computer` after the v3.7 trainer has saved a
checkpoint. Do **not** run the v3.7 and v3.8 CUDA jobs together if memory is
limited. The fork snapshots the checkpoint available **when the command runs**.

```powershell
& $py .\tools\nanojev_parallel_result_register_order_v3_8.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --fork-v3-7-root "$env:USERPROFILE\NanoJev\runs\parallel_result_asinh_squared_v3_7" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_register_order_v3_8" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --additional-steps 5000
```

Default fork checkpoint: `latest.pt` (the most recent optimizer/stream state).
For a deliberate restart from v3.7's best *combined intermediary* checkpoint,
add `--fork-v3-7-checkpoint asinh_intermediary/best.pt` when first forking.
The two choices are distinct controlled starting points. Do not use the same
v3.8 results directory for both.

All v3.7 configuration options affecting the experiment (e.g. replay capacity,
head widths, weights, seed) must match the selected v3.7 run; otherwise the
base trainer rejects the fork with `ASINH_CONFIG_DRIFT`. `additional-steps` is
relative to the chosen checkpoint step, never an absolute run total.

## Resume after the initial fork

```powershell
& $py .\tools\nanojev_parallel_result_register_order_v3_8.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_register_order_v3_8" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --resume --additional-steps 5000
```

For a long additional continuation, replace `5000` with `150000` if desired.
Do **not** pass `--fork-v3-7-root` on resume.

## Evaluate unchanged validation, test and control

```powershell
& $py .\tools\nanojev_parallel_result_register_order_v3_8.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_register_order_v3_8" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --eval-only --eval-best
```

## Reproducibility and safeguards

- `register-order-cutover.json`: source checkpoint path, SHA-256, inherited step,
  swap rule, preserved state, and benchmark validation order.
- `latest.pt`, `asinh_result/best.pt`, `asinh_intermediary/best.pt`,
  `asinh_final/best.pt`: checkpoint tracks copied from the parent at fork and
  subsequently maintained by the **unchanged** v3.7 training machinery.
- `run_config.json`: original v3.7 training configuration plus a cutover marker;
  ordinary v3.7 cannot silently resume this new lineage.
- Original v3.7 checkpoints and code are never rewritten.
- The fork fails on a reused destination path. All checkpointer state files are
  copied into a temporary staging directory before the new root is published.
- Test failures indicate a problem; no actual CUDA run was executed here.

## Verification performed

- 21 standalone unit tests (prompt identity, target preservation, order parity,
  safe fork and complete state retention).
- 8 existing v3.7 unit tests.
- End-to-end CPU integration with mocked Qwen capture: v3.7 parent fork,
  subsequent v3.8 alternating fork, train, resume, `--eval-only --eval-best`.
  The parent checkpoint SHA-256 was unchanged.
