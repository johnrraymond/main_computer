# TinyStories precision-v2 matched weight-zero experiment

The existing TinyStories precision-v2 trainer **already supports `--precision-weight 0`**. This cutover adds a guarded launcher only, not a new model or training objective.

The launcher creates independent control (`weight0_control`) and treatment (`weight001_treatment`) roots, both from `independent_altreg_math_tinystories_v1/best.pt` and its validation cache. It refuses to overwrite a nonempty arm root or resume if the parent checkpoint, cache, seed, sample counts, or weight differ.

Run from `C:\Users\subsi\main_computer` after merging `main_computer_test/` contents into that repo:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
$launcher = '.\tools\nanojev_tinystories_precision_matched.py'

# Print and validate fork identity without training.
& $py $launcher --arm control --dry-run
& $py $launcher --arm treatment --dry-run

# Run arms sequentially, separately from Qwen, to avoid GPU contention.
& $py $launcher --arm control --steps 10000 --device cuda --dtype float16 --allow-model-download
& $py $launcher --arm treatment --steps 10000 --device cuda --dtype float16 --allow-model-download
```

To continue a completed/partial arm for **another** 1,000 optimizer steps:

```powershell
& $py $launcher --arm control --resume --additional-steps 1000 --allow-model-download
```

IMPORTANT: `--steps 10000` means 10,000 *additional* optimizer steps after the common v1 checkpoint (not cumulative global step 10,000). Default matched seed 20261013; validation and test sample counts 128 each, every 100-step evaluation. The original TinyStories v2 lineage is left untouched and is **not** used as the common fork, because the existing precision-v2 checkpoints already have precision-weighted training history.

Compare identical `step` records in each arm's validation output, and compare `best-precision.pt`, `best-asinh.pt` and `latest.pt` only after checking their step numbers. The trainer's `precision_rank` is a composite metric and is **not** synonymous with joint X/Y accuracy. Use separate `original`/`reversed` joint_5pct, x_5pct, y_5pct and averaged results, plus held-out test evaluation, to establish benefit. Training may not improve accuracy.

Run independent tests:

```powershell
& $py -m pytest .\tests\test_nanojev_tinystories_precision_matched.py -q
```

### Launcher hotfix: fresh output must remain empty

The original launcher wrote `matched-arm.json` inside `weight0_control` before
invoking the trainer, causing `ALTREG_INDEPENDENT_RESULTS_ROOT_NOT_EMPTY`.
This version writes lineage sidecars under `.matched-lineage/control.json` or
`.matched-lineage/treatment.json`, outside both trainer output directories.

If you already attempted the defective launcher, its abandoned
`weight0_control/matched-arm.json` remains. **Verify that no checkpoints or
other training artifacts exist before deleting only that file:**

```powershell
$arm = "$env:USERPROFILE\NanoJev\runs\independent_altreg_math_tinystories_matched_v2\weight0_control"
Get-ChildItem -Force $arm
# ONLY if it contains matched-arm.json and nothing else:
Remove-Item -LiteralPath (Join-Path $arm 'matched-arm.json')
```

An empty existing results directory is accepted. Never delete a directory
containing checkpoints or other run evidence.
