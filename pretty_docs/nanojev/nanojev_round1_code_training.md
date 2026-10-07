# NanoJev Round 1 — streaming code-head training

Round 1 trains a fresh NanoJev decision head to select the true next Qwen token
from finite plausible alternatives extracted from real `main_computer` Python and
JavaScript. The Qwen3-0.6B backbone is frozen.

The trainer is deliberately streaming. It does **not** tokenize the repository and
build a giant corpus before training. A cycle:

1. selects a small deterministic shard of source files;
2. tokenizes only those files and creates a few hundred next-token questions;
3. trains for about `CycleSeconds` wall-clock seconds;
4. evaluates the same fixed held-out dev probe;
5. prints training loss/error and dev loss/error;
6. saves the head, optimizer state, progress, and history.

The next cycle then moves to another source-file shard while the backbone remains
loaded on the GPU.

Default cycle telemetry includes:

- `train_mean_nll`
- `train_top1_error`
- `first_step_mean_nll`
- `last_step_mean_nll`
- `dev_mean_nll`
- `dev_top1_error`
- `delta_dev_nll`
- best cycle / best dev NLL
- checkpoint paths

Use:

```powershell
.\tools\run_nanojev_round1_code_training.ps1 -RestartPartial
```

Resume after an interruption at a completed cycle boundary:

```powershell
.\tools\run_nanojev_round1_code_training.ps1 -Resume
```

The default cycle target is 75 seconds. The exact wall time is longer by shard
construction and dev evaluation, but checkpoint/reporting happens after every
cycle rather than after a monolithic dataset or long training phase.
