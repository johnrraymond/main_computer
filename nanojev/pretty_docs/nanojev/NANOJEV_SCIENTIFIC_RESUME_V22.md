# NanoJev scientific v2.2 checkpoint-step reader fix

This patch only replaces the **PowerShell orchestrator**. The checkpoint-bound
Python trainer and scientific teacher are unchanged. The existing scientific
checkpoint is reused; do **not** rebuild or recutover.

## Cause

In Windows PowerShell 5.1, embedded double quotes inside a Python `-c`
argument may be stripped while constructing the native command line. This
caused `map_location="cpu"` to be evaluated as `map_location=cpu` and raised
`NameError: name 'cpu' is not defined` before starting training.

## Fix

The runner now passes `cpu` and `step` as positional arguments to Python,
without embedding quoted literals in the `-c` source code. The Python code is
otherwise functionally identical: CPU-only `torch.load(...,weights_only=True)`
reads the saved integer checkpoint step.

## Apply

Copy `main_computer_test/tools/cutover-nanojev-hawk-dove-scientific.ps1`
to `C:\Users\subsi\main_computer\tools\cutover-nanojev-hawk-dove-scientific.ps1`.

## Retry

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& .\tools\cutover-nanojev-hawk-dove-scientific.ps1 `
  -Stage Train `
  -Python $py `
  -AdditionalSteps 6000000 `
  -ChunkSteps 300 `
  -DiagnoseBoth
```

The script uses CUDA for the actual training. Only the lightweight step read
uses CPU. Training progress and probes remain on the existing v2 lineage.
If the checkpoint is still at step 68,400, the absolute target becomes
6,068,400.

## Test scope

The checkpoint reader was verified against a temporary PyTorch checkpoint
using the exact quote-free `-c` expression. The patch was compared against
v2.1 to ensure no other code path changed. Windows PowerShell and CUDA execution
must be verified on the target machine.
