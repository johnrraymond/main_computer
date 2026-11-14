# NanoJev Intel Iris Xe-only GPU smoke — v6

This is a **non-mutating, Intel-only** DirectML inference smoke of a fixed-seed
568,405-parameter two-layer proxy, close in size to an alternative 570,453-parameter
NanoJev numerical readout. **It is not the actual NanoJev trained head or Qwen backbone.**

The wrapper does **not import or use the CUDA PyTorch environment**, invoke any
NVIDIA compute kernels, or generate a CUDA benchmark. The underlying DirectML
library enumerates graphics adapters to choose Intel; enumeration is not compute.
CPU operations are used to create deterministic inputs and check numerical error.

### Install
Extract the ZIP at `C:\Users\subsi\main_computer`, preserving directories.
The test uses the existing isolated DirectML Python environment at
`C:\Users\subsi\.venv-nanojev-directml-smoke\Scripts\python.exe`.
Because this environment already exists, do **not** pass `-InstallDml`.

Run in PowerShell at the repo root:

```powershell
.\tools\run_nanojev_igpu_smoke.ps1
```

This targets confirmed DirectML adapter 1 (Intel Iris Xe). The script refuses a
non-Intel device even if a wrong adapter index is passed. If device order changes,
provide `-IntelDeviceIndex` explicitly, e.g. `-IntelDeviceIndex 0`, but only if
that index really is the Intel adapter. Alternatively, `-IntelDeviceIndex -1`
auto-discovers it by name.

### Direct invocation (same test, bypassing PowerShell)

```powershell
& 'C:\Users\subsi\.venv-nanojev-directml-smoke\Scripts\python.exe' `
  '.\tools\nanojev_igpu_smoke.py' --backend dml --dml-index 1 `
  --batches 1 64 --warmup 12 --steps 60 `
  --report '.\runtime\reports\nanojev-igpu-smoke\intel-dml.json'
```

### Result
The script prints numerical checks and inference latency/throughput for batches
1 and 64 and writes `runtime/reports/nanojev-igpu-smoke/intel-dml.json`.
No NVIDIA comparison is performed, no checkpoints or existing CUDA environment
are accessed or modified, and the prior `cuda.json` is neither read nor overwritten.

### Important backend fix
Use `torch.no_grad()` rather than `torch.inference_mode()` to avoid the known
DirectML version-counter error. See https://github.com/microsoft/DirectML/issues/602.

### Validation
CPU branch tested in an isolated environment; Windows hardware execution must
be verified on the user's machine. No silent fallback to CPU on DirectML failure.
