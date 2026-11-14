# Hawk–Dove T00 mask diagnosis: checkpoint identity repair

## Fault fixed

Prior `diagnose` shared the strict training-resume configuration check and raised
`HAWK_DOVE_MILLION_RESUME_CONFIG_DRIFT` **before loading the checkpoint**. The
saved checkpoint may have different values for training-only invocation settings,
such as `views`, `seed`, `lr`, `probe_per_tier`, or `trainer_sha256`. A read-only
diagnosis does not need to change those settings or rewrite the saved identity.

This patch keeps **training** resume validation strict, but lets **diagnose**
use the checkpoint's original saved identity. The diagnostic still verifies
`source_sha256`, `parent_sha256`, `teacher_sha256`, checkpoint schema, numerical
decoder ceiling, and the zero-Z-gradient contract. The loaded `latest.pt` must
contain the **exact same serialized config** as `config.json`. The checkpoint
and optimizer are never modified by `diagnose`.

If source, parent or teacher content changed, the diagnostic stops with
`HAWK_DOVE_MILLION_DIAGNOSE_PROVENANCE_DRIFT: <fields>`. The console result
includes `diagnostic_config_differences` listing the harmless invocation
settings that differed. We cannot know which settings caused your original
error without the saved `config.json`, so this field provides that evidence.

## Running on Windows PowerShell

After applying the source-only patch rooted at `main_computer_test/` into the
corresponding project source directory:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
$parent = "$HOME\NanoJev\runs\game_math_xy_v10_operand_audited\latest.pt"

& $py -m unittest tests.test_nanojev_hawk_dove_million -v

& $py -m tools.nanojev_hawk_dove_million_train diagnose `
    --parent $parent `
    --device cuda `
    --diagnose-tier T00
```

The report is written to:

```
$HOME\NanoJev\runs\hawk_dove_million_v1\diagnose-T00-step-XXXXXX.json
```

Read the `masks` entries **0, 1, 2, 3**, especially `joint_5pct`,
`x_5pct`, `y_5pct`, relative-error distributions, and failures. This is an
**exhaustive development-set** diagnosis over all T00 masks, not the 48-case
advancement probe. It does not touch the test split.

Do **not** change sampling weights before inspecting the actual diagnosis.
Once a weak mask is identified, existing `--mask-weight T00:3=4` remains
available for subsequent training, with independent `--tier-gate` settings
unchanged. The training objective is untouched.

## Verification

The suite of 15 tests exercises the existing curriculum, exhaustive diagnosis,
read-only checkpoint integrity, strict training-resume rejection, provenance
guards, mask weights, and compatibility with original trainer, independent-tier-
gate trainer, and previous mask-diagnostics trainer checkpoint identities.
Full CUDA evaluation of the user's checkpoint must be run on the user's host;
all checkpoint-resume integration checks here use a mock numerical head.
