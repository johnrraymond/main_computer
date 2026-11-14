# NanoJev Hawk–Dove T00 diagnosis — independent-head fix v3

## Root cause and correction

The previous diagnosis patch still computed `sha(args.parent)` and compared it to `config.json`'s `parent_sha256`. That rejected the current installation with:

`HAWK_DOVE_MILLION_DIAGNOSE_PROVENANCE_DRIFT: parent_sha256`

This is a **false dependency for read-only diagnosis**. The original runtime loader uses the historical parent only to initialize `decoder` and `gamehead` weights, but the Hawk–Dove `latest.pt` immediately replaces both of those modules' weights. The correct diagnosis construction is:

1. Read the existing `config.json` and `latest.pt` from the Hawk–Dove output directory.
2. Verify the embedded checkpoint config exactly matches the saved `config.json`, including the historical parent SHA.
3. Verify the **actual source checkpoint SHA** and **teacher database SHA** match the saved identity. Continue enforcing the decoder cap, schema and zero-Z-loss contract.
4. Initialize the **same frozen backbone** from `--source` using `nanojev_game_transform_experiment.load_current`, with the saved seed.
5. Load the `decoder` and `game_operator_head` state dictionaries **directly from `latest.pt`**, strictly.
6. Run the exhaustive held-out T00 mask analysis, without reading the parent file for model initialization or changing the optimizer, checkpoint, gating sidecars, or sampling policy.

`--parent` is **not needed** by `diagnose` now. When present, its SHA is recorded for information only: `parent_status.parent_used_for_diagnosis` is `false`. A changed/missing parent cannot block this evaluation, and the output truthfully reports the discrepancy.

**Train/probe/test** retain their original parent-dependent loader and strict training identity validation. No change to numerical objective, weights, sampler, gates, model architecture, or trainer loops.

## Apply patch

This source-only ZIP is rooted under `main_computer_test/`. Merge its contents into the corresponding live project paths (for example `main_computer_test/tools/...` → `C:\Users\subsi\main_computer\tools\...`). It contains only the changed trainer, updated tests, and this document. **Do not delete, replace, or rebuild** your training directory or database.

## Test and run

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
$parent = "$HOME\NanoJev\runs\game_math_xy_v10_operand_audited\latest.pt"

& $py -m unittest tests.test_nanojev_hawk_dove_million -v

& $py -m tools.nanojev_hawk_dove_million_train diagnose `
  --parent $parent `
  --device cuda `
  --diagnose-tier T00
```

`--parent` may be omitted entirely for `diagnose`, too. `--source` and `--teacher` must still point to the original source and teacher from the saved lineage. The result is saved to `~/NanoJev/runs/hawk_dove_million_v1/diagnose-T00-step-XXXXXX.json` and contains all development instances × all four T00 masks, X and Y accuracies, error metrics, example failures, and the parent status.

A parent-drift event does **not** prove that a subsequent **training** resume will succeed with the new parent. Training remains provenance-strict; the fix is limited to the read-only diagnostic.

## Verification performed

Tests include: changed parent after checkpoint creation; completely missing parent; byte-for-byte equivalent diagnostic results; unmodified checkpoint and tier-gate SHA after diagnosis; rejection of changed source; rejection of changed teacher identity; rejection of checkpoint/config mismatch; rejection of changed/missing parent for training; full mask 0–3 coverage; legacy checkpoint resumption; other existing Hawk–Dove unit tests.

All tests use a deterministic mock numerical head for loading/checkpoint integration. They do **not** claim execution on the user's CUDA machine. The implementation follows the source-backed model initialization contract of `nanojev_game_math_curriculum_v10_runtime.load_model`.
