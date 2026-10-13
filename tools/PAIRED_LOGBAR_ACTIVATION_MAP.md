# Paired decimal + Log-Bar activation experiment

## Purpose

Test whether a **single combined prompt** yields clearer numerical organization in a frozen Qwen3-0.6B's hidden states than either decimal or Log-Bar notation alone. This is the same deterministic, nonuniform 253-number grid from -2,000 to 2,000 used in the preceding `nanojev_logbar_activation_map.py` experiment. No arithmetic prompts, gradient updates, or search are involved.

Prompts are exactly:

```text
Number: -2000, -|||||||.||||||
Number: -1, -.|||||||
Number: 0, .
Number: 1, .|||||||
Number: 2000, |||||||.||||||
```

The original one-decimal-place natural-log quantization is unchanged. **There are 253 distinct combined prompts**, even though the original Log-Bar-only encoding contained 119 distinct codes due to 134 collisions. All 253 paired prompts get a fresh forward pass; 253 decimal and 119 Log-Bar baselines are **read from the previous run** and never recomputed.

## Run

Unzip the patch in the repository; it adds `tools/nanojev_paired_logbar_activation_map.py` and its test, and requires the two prior scripts `tools/nanojev_decimal_number_activation_map.py` and `tools/nanojev_logbar_activation_map.py` to already exist.

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_paired_logbar_activation_map.py `
  --device cuda `
  --output-root "$env:USERPROFILE\NanoJev\runs\paired_logbar_activation_map_v1"
```

The script defaults to reusing the prior baseline at:

`$env:USERPROFILE\NanoJev\runs\logbar_activation_map_v1`

Change it with `--baseline-root` if necessary. The baseline folder **must contain its original `records/*.npz`, `manifest.json`, and `model_identity.json`**, not just the zipped CSV reports. It also must use the same model identity, decimal grid, and fractional precision. Missing or mismatched baseline produces a clear error rather than silently comparing incompatible activations.

Other options: `--prepare-only` (no model), `--limit 7` (offline smoke with matching limited baseline), `--resume` (restart interrupted run without replacing saved records), `--analyze-only` (rebuild CSV reports without loading the model), and `--self-test`.

## Captures

For each model hidden state, save five distinct views:

- `numeric_last`: final Log-Bar token, which can attend to the preceding decimal text;
- `numeric_mean`: mean of the two numerical spans, excluding the comma and spacing;
- `decimal_last` and `decimal_mean`: prefix-decimal span only (important **non-fusion control** in an autoregressive model);
- `bar_mean`: Log-Bar span in the context of its preceding decimal number.

The decimal-prefix vectors should not be treated as evidence of fusion: in a causal model, they cannot attend to later Log-Bar tokens. The last-bar and pooled vectors are the primary combined-format measurements. Spans are taken from the tokenizer's offset mapping; their token counts are recorded, and the script rejects overlapping or unaligned spans.

## Analysis

The new run writes:

- `records/paired_*.npz`: raw float32 activation vectors + strict tokenization and model provenance;
- `number_mapping.csv`: original numbers, Log-Bar codes, collision groups, actual combined prompts and token counts;
- `layer_metrics.csv`: decimal, Log-Bar, and paired metrics for matched, unique codes; plus paired/decimal metrics for the full 253-number cohort;
- `geometry_alignment.csv`: all-pair and **same-sign** Spearman correlations of centered-cosine distances in the full hidden vector space, on one representative integer per Log-Bar code;
- `headline_comparison.json`: compact side-by-side matched comparisons at layers 16 and final;
- `summary.json`: completion counts and important interpretation limitations.

`layer_metrics.csv` includes PC1 variance, correlation with signed log, correlation with sign, strongest single-neuron signed-log correlation, and within-positive and within-negative log-magnitude correlations from PC1/PC2.

**Caution:** A paired prompt explicitly reveals the decimal number and its logarithmic transformation. If it correlates more strongly with magnitude, that does not alone prove that both formats activate the same numerical mechanism. Tokenization, repeated information, sign, and prompt position remain alternative causes to test. The within-sign geometry comparison and decimal-prefix control help distinguish these explanations but do not eliminate them.

## Share results

```powershell
$root = "$env:USERPROFILE\NanoJev\runs\paired_logbar_activation_map_v1"
Compress-Archive -Path `
  "$root\summary.json", `
  "$root\headline_comparison.json", `
  "$root\layer_metrics.csv", `
  "$root\geometry_alignment.csv", `
  "$root\number_mapping.csv" `
  -DestinationPath "$root\paired_results_for_review.zip" -Force
```
