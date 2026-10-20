# NanoJev numerical-adapter launcher fix v1.2

## Confirmed cause
`tools/nanojev_altreg_independent_math_qwen_precision_v2.py` imports
`nanojev_numerate_spine_cassette_smoke` and `nanojev_register_stability_v3_generate`
as bare sibling modules. `python -m tools.nanojev_hawk_dove_numeric_adapter_train`
does not automatically place `tools/` on `sys.path`. The earlier v1.1 fix
attempted to initialize this in the adapter, but the repeated traceback shows
this did not resolve the actual launch.

## Scope
Only `tools/resume-nanojev-hawk-dove-cached.ps1` changes. Its `Invoke-NanoJev`
helper adds `tools/` to `PYTHONPATH` for the child process, in a `try/finally`
that restores the previous environment. The fix works with either the original
v1 adapter or the v1.1 adapter. No changes to math head, cached trainer,
checkpoint, step, gates or provenance hashes.

## Install (from C:\Users\subsi\main_computer)
```powershell
Expand-Archive .\nanojev_hawk_dove_numeric_adapter_launcher_fix_v12.zip .\_numeric_adapter_launcher_fix -Force
Copy-Item .\_numeric_adapter_launcher_fix\main_computer_test\tools\resume-nanojev-hawk-dove-cached.ps1 .\tools\resume-nanojev-hawk-dove-cached.ps1 -Force
```

## Preflight the real bare sibling import
```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
$env:PYTHONPATH = "$(Resolve-Path .\tools)$([System.IO.Path]::PathSeparator)$env:PYTHONPATH"
& $py -c 'from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base; print("OP_IDS",len(base.OP_IDS))'
```
Expected: `OP_IDS 20`. This preflight changes the current shell's Python path;
the patched PowerShell runner independently manages the environment for child processes.

## Retry
```powershell
& .\tools\resume-nanojev-hawk-dove-cached.ps1 -Stage Upgrade -NumericAdapter -Python $py
```

## Tests
```powershell
& $py -m unittest discover -s tests -p test_nanojev_numeric_adapter_launcher_import_fix.py -v
```

The test uses a synthetic checkpoint only. It verifies the original unpatched
adapter can migrate via a child process when `tools/` is supplied on `PYTHONPATH`.
