# NanoJev Qwen Continuous v1 — source-verified additive cutover

## Purpose

Take the **existing frozen Qwen3-0.6B precision-v2 decoder**, keep the zero-weight
coordinate objective and 16:3:1 X/Y:RESULT:FINAL training schedule, and run it
for consecutive 2,000-update segments with exact resume. This patch **adds** a
supervisor and tests; it does **not** modify the existing trainer.

Grounded in `main_computer_test-20261014-150224(1).zip`, particularly
`tools/nanojev_altreg_independent_math_qwen_precision_v2.py`. Confirmed trainer
interface includes `--resume`, `--additional-steps`, `--results-root`, exact
optimizer + RNG/stream restoration, `best-precision.pt`, and `best-asinh.pt`.

## Data safety and starting checkpoint

The default source is:

`%USERPROFILE%\NanoJev\runs\independent_altreg_math_qwen_weight0_control`

The default **new, independent** destination is:

`%USERPROFILE%\NanoJev\runs\independent_altreg_math_qwen_continuous_v1`

On first real execution, the supervisor copies the source's `best-precision.pt`
into the new destination as `latest.pt`, `best-precision.pt`, and
`best-asinh.pt`, plus its exact validation cache and available fork metadata.
The **source directory is never changed**. The copied checkpoint carries its
original optimizer, sample stream and RNG states. The original source's
`best-asinh.pt` remains in the source as a separate archived result; the new
lineage starts from the precision-selected state.

The campaign metadata, historical baseline snapshots, logs and new-best
archives live in a **third** directory alongside the new training destination:

`%USERPROFILE%\NanoJev\runs\independent_altreg_math_qwen_continuous_v1_continuous_v1`

## Prerequisites

Do not run the original Qwen precision-v2 trainer concurrently against the
same destination. The supervisor prevents a second supervisor, but the original
standalone trainer does not know about this lock. Confirm the current Qwen
weight-zero training has stopped before bootstrapping.

The original `tools/nanojev_altreg_independent_math_qwen_precision_v2.py`, the
Cassette and Register Stability dependencies, Torch, Transformers, and GPU runtime
remain required exactly as before. This patch does not install or upgrade them.

## Preflight: no writes

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_qwen_continuous_v1.py --dry-run
```

Check that it reports Qwen, `precision_weight: 0.0`, the expected parent SHA,
the best-precision source step and a `--resume` command inheriting the exact
saved width, learning rate, batch size, seed and holdout counts.

## Test the supervisor without CUDA

```powershell
& $py -m unittest discover -s .\tests -p test_nanojev_qwen_continuous_v1.py -v
```

The eight tests include the isolated-control bootstrap, simulated training for
*two complete cycles*, exact recovery of remaining steps after interruption,
config drift detection, safe locking and archiving.

## One real 2,000-update training segment

```powershell
& $py .\tools\nanojev_qwen_continuous_v1.py `
  --cycles 1 `
  --cycle-steps 2000 `
  --allow-model-download
```

Check the `qwen_continuous_cycle_complete` record and the new campaign journal.
Only that run's checkpoint and evaluations can verify CUDA compatibility.

## Long-running continuation

```powershell
& $py .\tools\nanojev_qwen_continuous_v1.py `
  --cycles 0 `
  --cycle-steps 2000 `
  --plateau-cycles 8 `
  --allow-model-download
```

`--cycles 0` continues until interrupted or eight consecutive cycles without a
new numerical-precision **validation** best. `--plateau-cycles 0` disables this
stop gate. It is safer to keep the default gate: expanding a failed fixed
architecture indefinitely consumes compute without demonstrating improvement.

If interrupted after 1,250 steps of a 2,000-step segment, the campaign journal
records the target and the next execution schedules **only 750 additional
updates** from `latest.pt`. If the trainer had reached the segment target before
the interruption, the supervisor verifies `test-last.json` and completes the
journal without rerunning training.

The existing frozen-backbone feature-capture and synthetic input stream are
unchanged. The stream persists its RNG/draw state in `latest.pt`; fresh draws do
not guarantee globally unique samples.

## Promotion and evaluation

The original trainer selects `best-precision.pt` lexicographically by operand
accuracy at 5%, 1%, 0.1% and negative log relative error, and separately tracks
`best-asinh.pt`. These are preserved and archived when improved. Each cycle
reports the saved step and best values. The original final test is still
computed per segment by the trainer, but **the supervisor does not optimize on
repeated final-test results**.

**Do not interpret an increment on a 128-example validation set as definitive
functional improvement.** All archived gains are candidate checkpoints until
larger, independent paired X/Y and RESULT evaluations confirm them. This patch
adds repeatability and preservation; it does not invent a statistical audit or
change the frozen representation.

## Status

```powershell
& $py .\tools\nanojev_qwen_continuous_v1.py --status
```

### Hard safety stops

- Refuses a non-Qwen or nonzero-precision-weight checkpoint.
- Refuses altered training configuration or a changed trainer SHA256 after the
  campaign has started.
- Refuses re-initialization over an existing output directory.
- Refuses an overlapping supervisor via an OS-managed lock that is automatically
  released after process termination.
- Refuses inconsistent active-cycle steps or missing final test completion.
- Writes metadata outside the trainer directory so trainer resume is unaffected.

Training the source weight-zero control directly with `--resume` is unnecessary
and would destroy the clean endpoint of that completed experiment. The new
isolated destination avoids this.
