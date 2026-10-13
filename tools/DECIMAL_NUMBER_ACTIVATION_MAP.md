# Decimal Number Activation Map v1

Standalone observational experiment using the existing frozen cassette model loader.
The six-chain search, arithmetic probes, and unary representations are **not** used.

## Probe set

213 decimal integers, symmetric about zero:

- -20..20 step 1
- +/-25..200 step 5
- +/-210..500 step 10
- +/-525..1000 step 25

For each integer n, send exactly `Number: {n}` as one forward inference with no generation.
Capture hidden states at every depth at the final number-overlapping token and the mean
of all number-overlapping tokens (including `-` for negatives). No weights are trained.

## Run

From `C:\Users\subsi\main_computer` in PowerShell:

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_decimal_number_activation_map.py --self-test

& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_decimal_number_activation_map.py `
  --device cuda --prepare-only

& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_decimal_number_activation_map.py `
  --device cuda `
  --output-root "$env:USERPROFILE\NanoJev\runs\decimal_number_activation_map_v1"
```

To resume an interrupted matching run, add `--resume`. Use `--analyze-only` to
recompute reports without reloading the LLM. To smoke-test on a balanced tiny grid,
add `--limit 9` **and use a different output directory**; the full grid is the default.

## Artifacts

- `manifest.json`: sealed ordered number bank and inference settings
- `model_identity.json`: model revision / device / dtype provenance
- `records/NNNN.npz`: per-number metadata plus float32 layer vectors of shape
  `[layers_including_embedding, 2_channels, hidden_size]`
- `numbers.csv`: inputs and token-span metadata for confound inspection
- `layer_metrics.csv`: per-layer correlations, PCA variance, nearest-neighbor
  numerical separation compared with all-pairs baseline
- `pca_coordinates.csv`: two PCA coordinates for every number at each layer/channel
- `summary.json`: array shape and output inventory

## Interpretation limits

Unsupervised neighborhoods can indicate distributed structure without an obvious
single-neuron correlation. Correlation, PCA clustering or tokenization-boundary
patterns are not proof of an arithmetic mechanism. Number prompts differ in length
and model-token segmentation; `numbers.csv` preserves that information. Future
experiments can add prompt paraphrases and deliberately held-out labels after a
baseline number map has been examined.
