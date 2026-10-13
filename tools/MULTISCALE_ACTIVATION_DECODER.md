# Multiscale frozen-activation decoder: +/-2,000,000

## Purpose

Measure **in-sample** signed-integer reconstruction `x_hat = f(A(prompt))` from
all three representations: decimal alone, one-decimal natural-log bars alone,
and `Number: <decimal>, <logbar>` in one forward pass. Uses **117 effective
ridge degrees of freedom by default**, **not** 117 Qwen parameters and not
necessarily 117 nonzero decoder coefficients. Qwen weights remain frozen.
No validation split, nonlinear networks, or arithmetic tasks.

## Install

Merge `main_computer_test/tools/nanojev_multiscale_activation_decoder.py` into
`C:\Users\subsi\main_computer\tools\`. The existing previous scripts
`nanojev_decimal_number_activation_map.py`,
`nanojev_logbar_activation_map.py`, and
`nanojev_numerate_spine_cassette_smoke.py` must remain alongside it.

## Preparation (no model load)

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_multiscale_activation_decoder.py `
  --prepare-only
```

## Full run

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_multiscale_activation_decoder.py `
  --device cuda `
  --output-root "$env:USERPROFILE\NanoJev\runs\multiscale_activation_decoder_v1"
```

Full grid: 507 signed integers (253 positives, their 253 negatives, plus zero),
sampled in bands: 1–20 by 1; 25–200 by 5; 250–2000 by 50; 2500–20000 by
500; 25000–200000 by 5000; and 89 approximately 20,227-apart integers from
220000 through 2000000. Decimal and paired prompts stay unique. Natural-log
bars use `round(10*ln(1+abs(x)))` and are evaluated once per unique code:
507 decimal, 239 Log-Bar, 507 combined = **1,253 forward passes**.
The encoding of +2,000,000 is `||||||||||||||.|||||` and -2,000,000
adds a leading `-`.

Prompt examples:
```
Number: 2000000
Number: ||||||||||||||.|||||
Number: 2000000, ||||||||||||||.|||||
```

## Recovery

Capture is checkpointed as `.npz` files with manifest + model identity and
prompt checks. If interrupted, run the same command with `--resume`.
To recompute the decoder without loading Qwen, rerun with `--analyze-only`.
You can change `--effective-df 117` or `--layers 16,28` in analyze-only mode
without recapturing the model. Changing the sampling grid, prompt encoding,
model revision, device, or dtype requires a fresh output directory.

A small offline smoke is available with `--limit 11 --effective-df 4 --layers 1`
using a fake model in the unit tests. To run test code without CUDA, run:
```
python -m pytest -q tests/test_nanojev_multiscale_activation_decoder.py
```

## Outputs

* `manifest.json`, `model_identity.json`, `records/`: reproducible frozen
  activations with token-offset metadata.
* `number_mapping.csv`: value-to-code collisions and all three prompts.
* `fit_metrics.csv`: decoder effectiveness at layers 16 and 28 for three
  independent affine ridge decoders and one shared decoder; requested and
  achieved DF, rank, numerical MAE/RMSE/R², WAPE, median absolute percentage
  error, and sign accuracy.
* `band_metrics.csv`: MAE and median relative error within each magnitude band.
* `predictions.csv`: all reconstructed numbers and their errors.
* `decoder_models.npz`: fitted coefficients and intercepts.
* `summary.json`: main metrics and sample-specific bar-collision lower bound.

Important: In-sample reconstruction may memorize prompt identities. Log-Bar
is inherently lossy because distinct integers can share the same bar prompt;
the baseline floor depends on the newly sampled set. Do not compare absolute
MAE from this +/-2-million run directly against the older +/-2-thousand MAE;
use normalized metrics and per-band errors. The shared decoder has the same
weights and intercept for all representations.
