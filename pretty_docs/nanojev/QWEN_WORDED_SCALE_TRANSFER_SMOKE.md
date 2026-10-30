# Qwen worded-number and order-transfer smoke

This is a **read-only zero-training** probe of the trained/frozen Qwen backbone and optionally the existing original/refined corrective glass. It deliberately tests transfer well outside the signed-zero-line acquisition corpus. It uses the same `PROBLEM:` packet prefix and requires a single `ANSWER:` completion.

## Usage (PowerShell)

Existing `$glassArgs` from the deformation-glass run is accepted. Preview the exact fixed deck without loading Qwen:

```powershell
& $py -m tools.nanojev_qwen_scale_transfer_smoke --stage deck @glassArgs
```

Run raw frozen Qwen only:

```powershell
& $py -m tools.nanojev_qwen_scale_transfer_smoke --stage run @glassArgs --glass-mode off
```

Compare the same fixed baseline to its **already trained** refined glass (still no training or oracle at inference):

```powershell
& $py -m tools.nanojev_qwen_scale_transfer_smoke --stage run @glassArgs --glass-mode refined
```

Outputs are written under `C:\Users\<user>\NanoJev\runs\qwen_worded_order_transfer_smoke_v1\` by default, **never** inside the original baseline directory. Override via `--probe-output` as needed. Reports: `transfer_cases.jsonl`, `predictions.jsonl` (original and corrected outputs per case), `report.json` (overall and grouped scores, fixes/regressions). To compare another variant, use a distinct `--probe-output` so reports are not overwritten.

## Test taxonomy

- Common English short-scale word names from **hundred** through **centillion**: words → exact integers (moderate exponents), words → `coefficient * 10^exponent`, inverse word lookup.
- Arithmetic with *small coefficients and huge magnitude*: same-order subtraction, mixed-order normalization, cancellation to zero and ±small residuals, exact units.
- Very large powers (`10^100`, `10^303`, `10^1000`) assessed **symbolically**, not expanded as decimal digits.
- Graham's number is represented by its **symbolic recurrence**; no expansion is asked for.
- Gödel-style integer encodings use **explicitly defined** zigzag and Cantor pairing functions for small coordinate tuples. No standard meaning is asserted for the phrase “Gödel-space of the known universe.”
- “Mega-deca-quadrillion” is not assigned a universal exponent. One boundary case requires the model to say that it is undefined; a separate case expressly defines it **only for that question**.

## Interpretation

The suite contains 112 deterministic, separately labeled cases. `strict` requires the exact single-line packet. `normalized` allows comma-separated integer presentation or simple spacing/bracing variations in the power notation, while still requiring the same coefficient, exponent, and units. If the model emits a different but mathematically equivalent representation (e.g., `1 * 10^13` instead of `10 * 10^12`), the current canonical grader may mark it wrong; inspect `predictions.jsonl` before drawing conclusions. No results are claimed until the actual CUDA run executes.

This is a **transfer smoke**, not a numerically certified full-range arithmetic engine and not a training benchmark. Zero-line acquisition limits, representation shift, format adherence, and symbolic ambiguity are all possible sources of failure. Qwen and glass weights are never updated.
