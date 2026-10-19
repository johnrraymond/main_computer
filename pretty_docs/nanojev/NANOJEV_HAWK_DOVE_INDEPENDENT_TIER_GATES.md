# Hawk–Dove million-count ladder: independent per-tier advancement gates

This incremental source patch upgrades `tools.nanojev_hawk_dove_million_train` from one global
`--gate-percent` to independent T00–T08 gates, **without changing the model, loss function,
training examples, database, parent checkpoint, or decoder**.

## Windows PowerShell: increase just T00 to 90%

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
$parent = "$HOME\NanoJev\runs\game_math_xy_v10_operand_audited\latest.pt"

# Stop the current running trainer before replacing its Python source.
# Resume the same lineage from its latest completed saved checkpoint.
& $py -m tools.nanojev_hawk_dove_million_train train `
  --parent $parent `
  --device cuda `
  --steps 5000000 `
  --chunk-steps 100 `
  --tier-gate T00=90
```

T00 now needs **at least 44 of 48** passing presentations to advance
(both X and Y predicted within 5% for each passing presentation).
T01–T08 remain at their previous 50% advancement gates unless configured otherwise.

Change arbitrary tiers independently:

```powershell
& $py -m tools.nanojev_hawk_dove_million_train train `
  --parent $parent `
  --device cuda `
  --steps 5000000 `
  --chunk-steps 100 `
  --tier-gate T00=90 `
  --tier-gate T01=85 `
  --tier-gate T02=75 `
  --tier-gate T08=95
```

The named gates are percentages from **greater than 0 through 100**;
decimals are permitted. Names are **T00, T01, ..., T08**, case-sensitive.
You may specify one or several `--tier-gate` flags. Duplicate tiers,
unknown tiers, non-finite values, and invalid percentages fail immediately.

## Persisted settings

The effective per-tier policy is saved alongside the existing isolated
checkpoint under:

`$HOME\NanoJev\runs\hawk_dove_million_v1\tier-gates.json`

Later `train`, `probe`, and `test` runs automatically reuse the saved gates
if no flags are passed. A later `--tier-gate T03=80` changes **only T03**;
T00=90 and any other saved overrides are retained.

`--gate-percent 60` explicitly **resets all nine** tier gates to 60%,
after which any `--tier-gate` overrides in that same command apply. The
original effective default remains 50% for a new lineage.

To inspect the policy, use PowerShell:

```powershell
Get-Content "$HOME\NanoJev\runs\hawk_dove_million_v1\tier-gates.json"
```

Every probe JSON includes `gate_percent_by_tier` and
`gate_fraction_by_tier`, and the trainer's terminal progress events include
`gate_percent` for the reported current focus. The 5%-relative-error
criterion for X and Y **does not change**: only the fraction of
presentations needed to *advance* changes. No changes are made to the
head's sign/exponent/mantissa training objective or Z-loss weight.

## Checkpoint continuity

For the **exact** v1 trainer from `nanojev_hawk_dove_million_training_patch_v1.zip`,
this patch accepts its known SHA-256 in `config.json` and `latest.pt`, retaining
the stored checkpoint identity. It does **not** reset or rewrite the
optimizer, model weights, RNG state, step number, parent checkpoint, or DB.
The gate policy is an independently persisted sidecar that is bound to the
stored lineage identity.

A different previously modified trainer version fails closed with the
original config-drift check. Apply this patch to the original v1 training
file, not an unknown divergent version. The new gate becomes active when
the restarted process resumes; **it cannot alter a currently running Python
process**. The prior training run should be stopped at a checkpoint; if
interrupted mid-chunk, resume starts from `latest.pt`.

## Verify

```powershell
& $py -m unittest tests.test_nanojev_hawk_dove_million -v

& $py -m tools.nanojev_hawk_dove_million_train probe `
  --parent $parent --device cuda
```

This source patch adds tests for independent thresholds, invalid overrides,
persisted policy updates, first-failing-tier selection, and a CPU-mock
**v1-checkpoint resume with the optimizer state intact**. Full CUDA training
is not executed in the patch build environment.
