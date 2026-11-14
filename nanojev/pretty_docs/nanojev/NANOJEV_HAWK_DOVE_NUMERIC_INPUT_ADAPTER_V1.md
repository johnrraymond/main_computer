# NanoJev scientific V3: in-place numerical input adapter (v1)

## Goal

Continue the **existing** `hawk_dove_million_scientific_v2` checkpoint, optimizer, masks, frozen Qwen captures, and advancement gates. Do **not** replace or modify the V3 math head's sign/exponent/mantissa components. No T02 payoff formula is embedded in code.

The preceding isolated numerical-fusion benchmark reached 100% T02 development accuracy with a **different learned affine output head**. That does **not** establish that the unchanged V3 head will reach the same score when given numbers upstream. This upgrade tests that missing link with a **bounded** trial.

## Files changed

- `tools/nanojev_hawk_dove_numeric_adapter_train.py` — opt-in adapter, validated operand roles, checkpoint migration, cached forward injection.
- `tools/resume-nanojev-hawk-dove-cached.ps1` — adds `-NumericAdapter` and `-Stage Upgrade` to existing run entrypoint; old mode remains available before migration.
- `tests/test_nanojev_hawk_dove_numeric_adapter_train.py` — CPU tests; no Qwen downloads or dataset labels used.

`tools/nanojev_hawk_dove_scientific_train.py`, `tools/nanojev_hawk_dove_cached_train.py`, `tools/nanojev_altreg_math_v3_decoder_experiment.py` and `tools/nanojev_game_transform_experiment.py` are **unchanged**; therefore existing trainer SHA identity and the frozen-capture cache remain valid.

## How it works

- Strip and **validate** the scientific/LogBar annotations, then read only count operands bound to the four payoff symbols in MUL nodes. T07/T08 natural-language Hawk/Dove count positions are supported. Supplied X/Y values in the ARGMAX branches are *never* routed through the adapter. Unknown or malformed expressions fail closed.
- Encode counts scaled by 1,000,000, plus presence flags, into a 12-dimensional vector. This is a feature representation, **not arithmetic**.
- Apply a small MLP with a **zero-initialized final projection** to the existing frozen parent feature vector `h`, immediately upstream of the unchanged V3 decoder. The first forward pass is bit-for-bit equivalent to the original. For T00, the adapter produces zero even after training, because no counts are present.
- Train the original V3 decoder and the adapter jointly using its **existing objective**, unchanged gates, and existing optimizer state. Only the adapter parameters are newly initialized; they are appended to the existing AdamW group. No warm start from the T02-only candidate's learned coefficients.
- The prior checkpoint is copied **verbatim** to `latest.pre-numeric-adapter.pt`, and the current `latest.pt` is updated atomically. Its original decoder tensors, optimizer moments, RNG, step and focus are preserved. `numeric-adapter-migration.json` records the SHA of the original backup. The `config.json` and existing data are not altered.
- Once upgraded, **always use the `-NumericAdapter` switch**. The old Python scientific/cached modules cannot load the extended decoder checkpoint; the PowerShell runner fails fast without the switch. To revert, **stop training first** and restore the pre-upgrade checkpoint manually after verifying its SHA. Reverting also discards the new adapter's subsequent training.

## Install

Stop all scientific Hawk–Dove training processes before updating the checkpoint. Unzip the delivered archive in `C:\Users\subsi\main_computer` and copy the `main_computer_test\` contents over the project root. Then:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest tests.test_nanojev_hawk_dove_numeric_adapter_train -v
```

## Stage 1: migrate (no optimizer steps)

```powershell
& .\tools\resume-nanojev-hawk-dove-cached.ps1 `
  -Stage Upgrade `
  -NumericAdapter `
  -Python $py
```

Expect `numeric_adapter_upgraded_in_place` or `numeric_adapter_migration_reused`. Check that `latest.pre-numeric-adapter.pt` and `numeric-adapter-migration.json` exist under the **same** scientific run root. This is **not** a new experiment lineage.

## Stage 2: probe zero-initialized adapter

```powershell
& .\tools\resume-nanojev-hawk-dove-cached.ps1 `
  -Stage Probe `
  -NumericAdapter `
  -Python $py
```

It should show the same baseline as immediately before migration; the adapter's initial output is exactly zero. This step does not train. If baseline predictions change, **do not start training** and report the output.

## Stage 3: bounded T02 trial (12,000 steps)

```powershell
& .\tools\resume-nanojev-hawk-dove-cached.ps1 `
  -Stage Train `
  -NumericAdapter `
  -Python $py `
  -AdditionalSteps 12000 `
  -ChunkSteps 300 `
  -CacheMemoryMB 1024 `
  -DiagnoseTier T02
```

Existing gates determine the actual focus and advancement. The trainer deliberately does **not** skip T01 if T01 regresses. For a first trial, **do not** invoke the legacy six-million-step loop. Watch T02 development accuracy versus its persisted `tier-gates.json` threshold. If the upgrade fails to improve T02 in this bounded window, analyze the run rather than assuming the new input adapter has solved the problem.

To continue after a successful probe, use the **same** runner and `-NumericAdapter` with new `-AdditionalSteps`, not a new run directory.

## Limitations and audit

- Real CUDA training against the full checkpoint has not been run here; tests use synthetic small V3 models and optimizer state.
- A zero-initialized adapter preserves the baseline but does **not** guarantee the unchanged V3 head can acquire accurate arithmetic in 12,000 steps. That is the point of the controlled trial.
- This only validates the existing Hawk–Dove symbolic grammar, not arbitrary arithmetic expressions or withheld payoff rules.
- The stage-`test` sealed split is not run by this adapter wrapper. Continue using development gates until choosing a final architecture.
