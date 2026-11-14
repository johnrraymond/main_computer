# Independent numerical reconstruction audit

Read-only evaluator for the **existing precision-v2** TinyStories, Qwen, and Pythia math heads. The frozen-backbone capture is reused from each run's `validation-cache.pt`. No model downloads, forward passes through the backbone, optimizer steps, edits to training results, or checkpoint mutations.

## Install

Extract the patch ZIP into `C:\Users\subsi\main_computer` so that `main_computer_test/tools/...` files merge with the local `tools/` directory by your normal patch workflow. Do not replace any existing trainer script. The patch contains only a new diagnostic, its test, and these instructions.

## Run (PowerShell, from `C:\Users\subsi\main_computer`)

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_independent_math_numeric_audit.py `
  --backbone tinystories `
  --results-root "$env:USERPROFILE\NanoJev\runs\independent_altreg_math_tinystories_precision_v2" `
  --output-root "$env:USERPROFILE\NanoJev\runs\independent_altreg_math_tinystories_numeric_audit" `
  --splits validation `
  --checkpoints fork best-precision best-asinh latest `
  --device cuda
```

The default audit destination is outside the training root. The tool refuses to write inside the training root and refuses a nonempty audit output directory unless `--overwrite` is passed. For a **final held-out test audit**, change `--splits validation` to `--splits test` and use a distinct output root. You can use `--splits validation test` when both are explicitly desired, but validation should drive hypothesis development and test should be reserved for final checking.

The same command works with `--backbone qwen` or `--backbone pythia` and their respective precision-v2 results roots; only evaluate checkpoints that exist. A non-precision-v2 run is deliberately rejected rather than silently mixing architectures.

## Files

- `predictions.csv` — one row per semantic example, order (`original`, `reversed`, `averaged`), and checkpoint. Includes true and decoded X/Y/RESULT/FINAL; relative errors; sign, decade exponent, mantissa; oracle single-component repair errors; swapped register pair counterfactual; and joint 5% accuracy. `averaged` is strictly diagnostic; it is **not** a recommended inference policy.
- `summary.json` — overall and per-operation/per-family aggregates for each checkpoint, split and order: operand and joint accuracy; sign/scale identification; sign/scale/mantissa oracle rescue counts; swap rescue counts; prediction diversity and signed-log target/prediction correlation.
- `manifest.json` — checkpoint step, source file, backbone, and split provenance.

**Reading rescue rates:** They are ground-truth-assisted ceilings, not achieved inference accuracies. An oracle correction for sign/decade/mantissa asks whether replacing *one* component with its true value would rescue a 5%-failed prediction. For zero, exponent/mantissa are undefined and are excluded. Overlapping repair classes are not assumed mutually exclusive. For numerical errors around zero, all tolerance denominators use `max(1, abs(truth))`, matching the original trainer.

**Caveat on RESULT:** `pred_result` and `pred_final` are the learned output heads, decoded using the trainer's exact `R_SCALE`. Their hit flags use the trainer's boolean rounding semantics; they do not represent executing the decoded operands through a separate arithmetic oracle. The audit preserves these outputs and their ground-truth targets for subsequent comparison.

## Run self-tests

```powershell
& $py -m unittest discover -s .\tests -p 'test_nanojev_independent_math_numeric_audit.py' -v
```

## Investigation order

1. Compare `best-precision` against `best-asinh` on the **same examples**, first in `summary.json`, then in `predictions.csv`.
2. Inspect rows where scale-oracle rescue is true but sign/mantissa oracle repair fails, and vice versa. Contrast original/reversed on each exact `example_id`.
3. Examine high-frequency predicted numerical values, signed-log correlation, and relative-to-target prediction diversity for collapse.
4. Check whether swapping X/Y simultaneously rescues failures. If not, avoid training a position-specific correction.
5. Only after validating a hypothesis, apply the *unchanged* diagnostics to a separately held-out test split.
