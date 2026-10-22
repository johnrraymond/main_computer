# NanoJev Independent Math: Mantissa v3 cutover

New file: `tools/nanojev_altreg_independent_math_tinystories_mantissa_v3.py`.
Existing v1/v2 trainer and checkpoint files are unchanged. This uses **only** the TinyStories v2 parent (step 34,400 when `latest` is selected). The frozen backbone and parent `MathHead` are never updated; only a small numerical correction model is trained. It reuses the exact held-out validation/test captures from the parent run. The correction is zero at initialization, so both orientations reproduce the inherited model before updates. Parent RESULT and FINAL predictions are deliberately kept unchanged to avoid claiming that they were retrained.

## Windows PowerShell

From `C:\Users\subsi\main_computer` after applying the ZIP rooted under `main_computer_test/`:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_altreg_independent_math_tinystories_mantissa_v3.py --self-test
& $py -m unittest discover -s .\tests -p '*mantissa_v3.py' -v

& $py .\tools\nanojev_altreg_independent_math_tinystories_mantissa_v3.py `
  --device cuda --dtype float16 `
  --parent-checkpoint latest `
  --steps 2000 --eval-every 100 --report-every 50 `
  --allow-model-download
```

To resume the same fork (only when parent v2 `latest.pt` has not changed):

```powershell
& $py .\tools\nanojev_altreg_independent_math_tinystories_mantissa_v3.py `
  --device cuda --dtype float16 --resume --steps 2000 `
  --eval-every 100 --report-every 50 --allow-model-download
```

Evaluation using stored paired captures (no transformer load):

```powershell
& $py .\tools\nanojev_altreg_independent_math_tinystories_mantissa_v3.py `
  --device cuda --eval-only
```

Defaults: parent `~/NanoJev/runs/independent_altreg_math_tinystories_precision_v2`, output `~/NanoJev/runs/independent_altreg_math_tinystories_mantissa_v3`.

## Objectives and selection

For each sample the frozen parent estimates X and Y. A separate head predicts *decimal-exponent correction*, *log-mantissa correction*, *sign* and *zero* diagnostics. The first two are applied as a bounded correction to the inherited numerical magnitude; the sign/zero heads receive explicit supervision but do not override the parent sign (the audit showed near-perfect sign correctness). The objective combines coordinate loss, sign/exponent/mantissa/zero supervision, and a log-relative precision term. The same weights and supervision apply to both register orientations, with semantically unchanged target X/Y.

The checkpoint promotion gate ranks **worst-orientation joint X/Y within 5%** first, then mean 5%-accurate X/Y across both orientations. A new fork therefore cannot select a checkpoint for doing well on original order while collapsing on reversed order. Test metrics are emitted separately, not used to select training checkpoints.

Outputs: `parent-baseline.json`, `latest.pt`, `best-joint.pt`, `validation-step-NNNNNNNN.json`, `test-last.json`. All under the new output folder. The parent config and checkpoint SHA-256 are included in the new checkpoint identity; any parent mutation invalidates resume.

## Limitations

This is an **experiment**, not a demonstrated precision improvement. The sign/zero diagnostic heads are not active at inference. The exponent/mantissa correction is a bounded residual on the parent's magnitude and therefore cannot repair an arbitrarily wrong initial parent magnitude. This does not yet introduce diverse natural-language paraphrases: it is an architecture/precision experiment across the two current paired layouts. Separate semantic generalization tests are necessary before calling the model layout independent. No trained RESULT/FINAL modification is claimed.
