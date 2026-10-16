# Independent AltReg v3 — residual identity initialization hotfix

## Problem found from real Qwen CUDA run

The first large campaign's `residual` candidate was not a true refinement at step 0.
Its final layer was zero-initialized, but the output applied `tanh(parent_xy + delta)`.
The frozen v2 parent's `xy_coord()` ALREADY applies `tanh`. Thus the v3 head
started at `tanh(parent_xy)` instead of at `parent_xy`. The observed Qwen step-zero
candidate MSE was 0.02253 versus 0.00919 for the parent; averaged operand 5%
accuracy was 1.17% versus 4.30%. This makes the legacy residual arm an unfair
"start from parent" test.

## Fix and compatibility rules

* `residual` computes `parent_xy + delta` without another tanh. The new head
  initializes `delta` to exactly zero, so its starting normalized coordinates
  match the parent exactly. Decoding still clamps to [-1,1] before sinh to
  preserve the representable numerical range.
* New residual checkpoints add `residual_semantics=identity-plus-delta-v1` to
  their config. Existing residual checkpoints cannot be resumed under the changed
  forward semantics and cannot be included in corrected comparisons.
* The corrected residual runs in its OWN directory:
  `.../independent_altreg_math_qwen_v3_residual_identity`.
  The earlier `..._v3_residual` is untouched.
* Factorized and attention arm code, checkpoint schema and default results roots
  are unchanged. Existing completed runs of those arms remain eligible.
* The updated campaign comparison writes `..._v3_comparison_identity`, so it
  does not overwrite the old results. Test output uses `..._v3_final_test_identity`.

## Install

Extract the ZIP rooted at `main_computer_test/` into the corresponding repo
files, preserving that root's relative file layout. This is a patch to the
ALREADY installed full v3 campaign, not a complete replacement.

Do not modify the files during an actively running full campaign. If it is still
running the **legacy residual** arm, interrupt it with Ctrl+C first; its saved
old checkpoints will remain intact. Once factorized and attention are finished,
only the corrected residual experiment needs to run. Otherwise, run all three
arms once using the new code and fresh result directories.

## Verify

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m pytest -q .\tests\test_nanojev_altreg_math_reconstruction_campaign_v3.py
& $py .\tools\nanojev_altreg_math_v3_decoder_experiment.py --self-test
```

## Rerun ONLY the corrected Qwen residual arm (when other arms are finished)

```powershell
& $py .\tools\nanojev_altreg_math_campaign_v3.py `
  --stage pilot --backbones qwen --arms residual `
  --device cuda --dtype float16 --steps 2000 --batch-size 16 `
  --eval-every 100 --report-every 50 --allow-model-download
```

The `altreg_decoder_v3_baseline` event should now show the *candidate* numerical
accuracy and coordinate MSE matching the *parent* (subject to ~float32 rounding)
for each original/reversed/averaged presentation. If they diverge, do not
proceed to result comparison; inspect actual model/caches.

After corrected residual and the original factorized and attention arms have
all finished their 2000-step budgets, run the matched comparison:

```powershell
& $py .\tools\nanojev_altreg_math_campaign_v3.py `
  --stage compare --backbones qwen --device cpu
```

Test-split comparison only after validation-based selection:

```powershell
& $py .\tools\nanojev_altreg_math_campaign_v3.py `
  --stage final-test --backbones qwen --device cpu
```

The already-produced checkpoint reconstruction audit and paired bootstrap
report are unaffected by this fix.

## Test scope

CPU unit/integration tests exercise the exact zero-delta identity, trainable
residuals beyond the unit boundary, isolated output roots, full toy training,
resume, all three arms and checkpoint comparisons. This hotfix has not been
validated against the user's live Qwen GPU/checkpoints inside this container.
