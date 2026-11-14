# NanoJev sidecar: Python-only checkpoint metadata recovery (v2)

## Actual defect

The original scientific training loop builds a new checkpoint dictionary at
**every 300-step save**. Its dictionary omits `numeric_adapter_contract` and
`step_at_migration`, even though `numeric_adapter.*` weights and AdamW moments
are still saved in `decoder` and `optimizer`. After the first adapter training
chunk, the sidecar PowerShell launcher therefore sees a false "adapter missing"
condition. Simply reinstalling the launcher does not fix the saved checkpoint.

The Python-only sidecar wrapper now:

- Reports the state of the current checkpoint without making changes.
- Restores the missing adapter **metadata only**, after verifying the immutable
  original adapter-migration backup, manifest, module SHA, decoder tensor keys,
  optimizer structure and saved config. It makes a byte-exact copy of the
  current post-training checkpoint *before* restoring metadata.
- Keeps both adapter and sidecar contracts in every subsequent scientific
  checkpoint save by intercepting **only** the scientific trainer's
  `latest.tmp.pt` write. Unrelated `torch.save` calls, including cache files,
  are passed through unchanged. The scientific trainer source itself is not
  edited, so its recorded SHA remains stable.
- Adds `--additional-steps` to the existing Python module for bounded
  continuation without a shell launcher.

## Safety

Stop any live trainers before running recovery or upgrade commands. Run from
`C:\Users\subsi\main_computer` using your NanoJev Python interpreter. If
`python` points to a different environment, substitute
`C:\Users\subsi\NanoJev\.venv\Scripts\python.exe` for it.

Original V3 weights and optimizer moments are not replaced. No tier gates are
altered. Original numeric-adapter checkpoint backup and the scientific teacher
remain untouched. The trainer SHA lock is preserved. No test split is opened.

## Python commands

Read-only status (no Qwen load):

```text
python -m tools.nanojev_hawk_dove_numeric_sidecar_train --status-only probe
```

If status says `numeric_adapter_contract_present=false` and
`numeric_adapter_weights_present=true`, restore contract metadata from its
independently verified adapter migration proof:

```text
python -m tools.nanojev_hawk_dove_numeric_sidecar_train --repair-adapter-contract probe
```

Run training-only fitting and dev preflight, no checkpoint change:

```text
python -m tools.nanojev_hawk_dove_numeric_sidecar_train --preflight-only probe
```

Upgrade (one-time):

```text
python -m tools.nanojev_hawk_dove_numeric_sidecar_train --upgrade-only probe
```

Probe the current checkpoint with frozen Qwen:

```text
python -m tools.nanojev_hawk_dove_numeric_sidecar_train probe --device cuda
```

Only if needed, continue the existing lineage by a bounded 300 steps:

```text
python -m tools.nanojev_hawk_dove_numeric_sidecar_train --additional-steps 300 train --device cuda --chunk-steps 300
```

The CLI accepts the existing scientific trainer arguments such as `--teacher`,
`--source`, `--output`, and `--cache-memory-mb`. Omitted paths inherit the
existing defaults from `science.make_parser`.

## Tests

```text
python -m unittest tests.test_nanojev_hawk_dove_numeric_sidecar_train -v
```

Tests include all T00–T08 grammar masks, train-only numerical fitting, strict
reload after migration, dropped-metadata reproduction, independent proof-based
metadata restoration, optimizer/tensor identity, and preservation of both
contracts across a simulated scientific save. No CUDA runtime was available in
the artifact build environment.

## Important limitation

The learned sidecar uses the known Hawk–Dove payoff-tag grammar, and learns five
coefficients from T00–T02 training examples. Success on later tiers with that
same grammar does not establish generalized arithmetic-rule learning by Qwen.
