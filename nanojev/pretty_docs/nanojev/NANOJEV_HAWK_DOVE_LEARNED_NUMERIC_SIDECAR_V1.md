# NanoJev learned numerical sidecar: in-place scientific lineage

## Defect and repair

The prior `numeric_adapter` adds operand-derived vectors to V3's frozen parent
representation. V3's sign/exponent/mantissa output continues to overfit T02,
so improved input information does **not** guarantee a usable numerical result.

This patch preserves V3 and its original optimizer moments. It registers a
separate, five-parameter continuous `numeric_sidecar` in the *same* decoder
and AdamW parameter group, using only the existing teacher's **T00–T02 train**
examples to fit an initial numerical mapping. It uses float64 to retain
near-zero precision. Subsequent training uses the original scientific sampler,
cache and curriculum gates. The numeric sidecar is trainable; coefficients
are not hardcoded in code.

**Scope/limitation**: This is a **grammar-aware, fixed Hawk–Dove task**.
The parser understands the declared `ARGMAX`, `ADD`, `MUL`, payoff-token roles,
visible raw literals, and T08's documented prose layout. It does not establish
that Qwen learned generic arithmetic or learned the symbolic grammar. The
frozen Qwen and existing V3 are still computed and retained; the continuous
sidecar is authoritative for XY when enabled. Later-tier development scores
show transfer **inside this grammar**, not to new operators or payoff games.
The sealed test split is never opened by this patch.

## Install

Stop the current NanoJev trainer after a saved checkpoint. Install only
these four changed files from the archive under `main_computer_test/` into
the repository root. Do **not** overwrite the existing adapter Python module:
its own checkpoint contract SHA must remain unchanged.

```powershell
Expand-Archive .\nanojev_hawk_dove_learned_sidecar_v1_patch.zip .\_sidecar_fix -Force
Copy-Item .\_sidecar_fix\main_computer_test\* . -Recurse -Force
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest tests.test_nanojev_hawk_dove_numeric_sidecar_train -v
```

## Preflight (read-only)

```powershell
& .\tools\resume-nanojev-hawk-dove-cached.ps1 `
  -Stage SidecarPreflight -NumericAdapter -NumericSidecar -Python $py
```

Requires preflight `PASS`. Its `trained_tiers` must contain **only T00,T01,T02**.
Examine its `dev_by_tier` entries. The fixture's payloads must be valid and
within the installed grammar. No checkpoint modification occurs at this stage.

## Upgrade, in place

```powershell
& .\tools\resume-nanojev-hawk-dove-cached.ps1 `
  -Stage SidecarUpgrade -NumericAdapter -NumericSidecar -Python $py
```

Saves a byte-exact checkpoint backup at:
`latest.pre-numeric-sidecar.pt`, with SHA recorded in:
`numeric-sidecar-migration.json`.
Existing parameters, AdamW moments, step, focus and model config are preserved.
Adds exactly one new trainable parameter vector. The original
`latest.pre-numeric-adapter.pt` remains unchanged.
Re-running `SidecarUpgrade` checks the manifest and reuses the migration.

## Probe before long training

```powershell
& .\tools\resume-nanojev-hawk-dove-cached.ps1 `
  -Stage Probe -NumericAdapter -NumericSidecar -Python $py
```

Scores run over the original 48-development-presentation sampled probe per
tier; no gates are lowered. Check `first_failing` and gate percentages.
If the entire declared fixed grammar transfers, this can report all tiers
passing without another training step. That is **not** evidence that Qwen
learned new math or that the model would solve changed game payoffs.

Optional bounded training (only if the probe has a failing tier):

```powershell
& .\tools\resume-nanojev-hawk-dove-cached.ps1 `
  -Stage Train -NumericAdapter -NumericSidecar -Python $py `
  -AdditionalSteps 300 -ChunkSteps 300 -CacheMemoryMB 1024 -DiagnoseTier T02
```

Do **not** run an old entrypoint that does not accept `-NumericSidecar`
after upgrading. It cannot load the extra parameter vector. This patch
makes the supplied PowerShell entrypoint fail closed in that situation.

## Recovery

Stop all trainers. If sidecar-enabled training is clearly defective, do not
remove the sidecar key from a live checkpoint by hand. Restore the validated
`latest.pre-numeric-sidecar.pt` as `latest.pt` after independently copying
the upgraded checkpoint aside, then run the original `-NumericAdapter`
entrypoint. Any new steps after the sidecar upgrade will not be part of that
restored earlier checkpoint.

## Tests

- Strict parser against **every** T00–T08 scientific mask (including reversed
  multiplication, swapped addition, prose, and annotated literal variants).
- Oracle/target leakage guard across ARGMAX branches.
- Train-only fit, full dev probes, zero handling and differentiable readout.
- Adapter-upgraded checkpoint → sidecar checkpoint migration with independent
  backup, unchanged original V3 state and AdamW moments; strict optimizer reload.
- Integration hooks: numeric predictions used in probe and loss in training.

The CPU suite uses a synthetic teacher. CUDA/Qwen behavior and the real teacher's
measured results must be validated on the user's workstation.
