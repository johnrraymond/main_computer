# Numerical-value recovery from frozen activations (in-sample)

This test uses the three already-captured numerical representation datasets:
`logbar_activation_map_v1` (decimal and Log-Bar) and
`paired_logbar_activation_map_v1` (both in one prompt).
It **does not load Qwen, regenerate activations, create a holdout, or alter any model weights**.

At default layers 16 and 28, it fits three independent affine ridge decoders
and a fourth affine ridge decoder with one set of coefficients shared across
all three representations. Every decoder receives the identical effective
complexity budget (16 ridge effective degrees of freedom). The same 253
true numerical values are fitted and evaluated in-sample.

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_three_representation_decoder.py `
  --output-root "$env:USERPROFILE\NanoJev\runs\three_representation_decoder_v1"
```

Output files:

- `fit_metrics.csv`: per-representation and shared-decoder reconstruction MAE,
  RMSE, maximum absolute error, R², and decoder degrees of freedom.
- `predictions.csv`: per-number ground truth, reconstruction, and signed error.
- `decoder_models.npz`: the actual per-layer linear functions (1,024 coefficients
  and an intercept for each independent decoder and for the single shared decoder).
- `summary.json`: the same layer results and the unavoidable bar-only encoding
  collision error floor (best achievable per-code MAE and RMSE in this sample).

Use `--layers 0,8,16,24,28` to examine more layers; `--effective-df 32` to
allow a more flexible linear decoder. The supplied defaults intentionally
limit decoder flexibility rather than allowing a 1,024-variable model to
memorize 253 numeric targets.

**Interpretation:** A strong in-sample fit establishes only that a
complexity-limited linear function recovers the observed values from the
observed activations. It does not establish generalization to unknown
numbers or representation-independent numerical understanding. A bar code
that maps several integers to the same prompt is fundamentally lossy; the
bar-only decoder cannot distinguish them regardless of training quality.
