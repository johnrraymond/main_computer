# NanoJev scientific v2: exact frozen-Qwen capture acceleration

## Why the original is slow

The scientific trainer originally calls `ex.forward(model,[prompt])` separately for each of the four training views on **every** optimizer step. `ex.forward` invokes `v3.full_capture`, including a full Qwen3-0.6B forward, and copies its frozen features GPU → CPU → GPU. The Qwen model, tokenizer and prompt are deterministic and frozen. Probes also re-execute the same Qwen inputs every 300 steps, across nine tiers.

At 6,000,000 additional steps this causes up to 24,000,000 repeated *training* Qwen forwards, plus evaluations. A 6-million-step run should be treated as potentially extremely long even with caching: the decoder optimizer and nine-tier probes still execute.

## What the cache changes

The new entrypoint `tools.nanojev_hawk_dove_cached_train` replaces the `ex.forward` frozen `full_capture` with exact float32 CPU feature reuse, and memoizes immutable SQLite training rows and token-window eligibility checks. All downstream parent projection, XY decoder, gamehead, losses, gradients, optimizer updates, selected training rows, advancement probes, tier gates and existing checkpoints still use the original scientific trainer. No scientific trainer source SHA is changed.

A cache key includes the complete **ordered prompt batch**, so evaluation batches are not incorrectly interchanged with training singletons (different CUDA padding/grouping may affect floating-point details). Cache files contain full float32 outputs, not reduced precision approximations, and are read with `torch.load(...,weights_only=True)`. Cache identity pins the source and teacher hashes, trainer SHA, and the source of the underlying Qwen-capture implementation. A separate `cached-execution-audit.json` records the cache wrapper source SHA; a drift fails closed.

The cache folder is `<run>/frozen-capture-cache-v1/`, created separately from `latest.pt`. RAM LRU is 1024 MiB by default; evicted items remain in the persistent disk cache. The first use of a prompt batch is still expensive. Later uses avoid Qwen entirely. Approximately every 10,000 forward calls the accelerator prints capture-cache, SQLite-row-cache, and tokenizer-eligibility hit/miss statistics. Disk cache can grow substantially as new tiers are explored.

## Installation

Install **only** the three provided new files (wrapper, PowerShell runner, tests) and this documentation. Do not overwrite `nanojev_hawk_dove_scientific_train.py`, and do not rerun build/cutover.

Wait until a regular `hawk_dove_million_xy_probe` appears (checkpoint saved), then stop the original trainer with Ctrl+C. Do not run two trainers on the same checkpoint simultaneously.

Run PowerShell from `C:\Users\subsi\main_computer`:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest tests.test_nanojev_hawk_dove_cached_train -v
& .\tools\resume-nanojev-hawk-dove-cached.ps1 `
  -Stage Train `
  -Python $py `
  -AdditionalSteps 6000000 `
  -ChunkSteps 300 `
  -CacheMemoryMB 1024 `
  -DiagnoseBoth
```

`AdditionalSteps` is relative to the actual checkpoint step at execution, not the step when the original six-million-step request began. To preserve the *original* total target, calculate `remaining = 6,068,400 - latest checkpoint step` and supply that as `-AdditionalSteps`.

Probe or diagnose without training:

```powershell
& .\tools\resume-nanojev-hawk-dove-cached.ps1 -Stage Probe -Python $py
& .\tools\resume-nanojev-hawk-dove-cached.ps1 -Stage Diagnose -Python $py -DiagnoseBoth
```

## Limits / measurement

CPU unit tests prove exact tensor reuse and identical decoder outputs/gradients for mocked frozen captures; real Qwen-on-CUDA throughput speedup has not been measured in this environment. This is an execution optimization, not an assurance of better T00 or T01 accuracy. Use Task Manager GPU's **CUDA** / **Compute_0** graph or `nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv -l 1` to measure compute utilization; a 0% 3D graph is not evidence of zero CUDA use.
