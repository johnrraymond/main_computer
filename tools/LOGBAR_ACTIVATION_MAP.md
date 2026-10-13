# Decimal ↔ logarithmic bars: frozen-model activation mapping

A minimal, independent observational probe using the existing frozen cassette Qwen3-0.6B loader and the numeric-token pooling routines of `nanojev_decimal_number_activation_map.py`. Neither six-chain search nor arithmetic questions nor training are used.

## Transform

For signed integer `n` in [-2000,2000], compute `q = round(10*ln(1+abs(n)))` (half-up for positives). Represent `q//10` as unary bars before `.` and `q%10` as bars after `.`, prefixed with `-` only if `n<0`:

- 2000 -> `|||||||.||||||`
- -2000 -> `-|||||||.||||||`
- 0 -> `.`
- 1 -> `.|||||||`
- 1000 -> `||||||.|||||||||`

The step is **0.1 natural-log unit**, not an ordinary decimal place of the original number. Reconstructed magnitude `exp(q/10)-1` is approximate. By design 253 spaced original decimal samples produce only **119 distinct log-bar prompts**; every collision is recorded and each unique code is run just once. This is not evidence of arithmetic ability.

For exact distinct codes on the selected 253-point grid, use `--fractional-digits 2`, where `:` separates hundredths: 2000 -> `|||||||.||||||:`. That makes 253 distinct log-bar codes, but increases code complexity. Use a separate output root.

## Commands (from main_computer root on Windows)

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' .\tools\nanojev_logbar_activation_map.py --self-test
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' .\tools\nanojev_logbar_activation_map.py --prepare-only
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_logbar_activation_map.py `
  --device cuda `
  --output-root "$env:USERPROFILE\NanoJev\runs\logbar_activation_map_v1"
```

Optional: `--fractional-digits 2 --output-root "$env:USERPROFILE\NanoJev\runs\logbar_activation_map_2digits"`.

The 253-point sampling grid is symmetric: 1–20 step 1; 25–200 step 5; 210–500 step 10; 525–1000 step 25; 1050–2000 step 50, and negatives plus zero. There are 253 decimal prompts plus 119 unique log-bar prompts in the default paired experiment, for 372 model passes. Raw per-prompt hidden vectors are stored at all model layers, separately for the final represented token and mean across represented tokens. Restart matching runs with `--resume` or analyze saved vectors without model load with `--analyze-only`.

## Outputs

- `manifest.json`, `model_identity.json`: grid, precision, model provenance and invariant settings.
- `records/decimal_*.npz`, `records/logbar_*.npz`: raw per-prompt hidden-state arrays and token spans.
- `number_mapping.csv`: original value, assigned code, collision group size, reconstructed value/error and token lengths.
- `layer_metrics.csv`: per-depth signed-log correlation with PCs and strongest individual neuron, separately for decimal and distinct log-bar codes.
- `pca_coordinates.csv`: unsupervised coordinates for each distinct decimal/log-bar prompt.
- `summary.json`: experiment sizes and explicit limits of interpretation.

**Interpretation:** If activations track the log-bar magnitude, that is partially expected because the logarithm has been provided in the prompt. Compare directly with decimal baselines, account for minus sign, bar count, and tokenizer segmentation. Code collisions are irreversible without extra information. This test does not establish that Qwen has learned an abstract magnitude variable independent of prompt format.
