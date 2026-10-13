# Twelve-point numerical reconstruction curve

Adds a new, independent runner on top of the existing `nanojev_three_representation_decoder.py`.
It uses the **same existing activation captures**; no Qwen run, CUDA, retraining, or holdout split.

The complexity sweep uses **12 effective-DF budgets**:

`16, 24, 32, 40, 48, 56, 64, 72, 80, 96, 112, 128`

It fits comparable ridge decoders for `decimal`, `logbar`, `paired`, and `shared_all`, at layers 16 and 28, and reports separate results for `shared_on_decimal`, `shared_on_logbar`, and `shared_on_paired`.

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_three_representation_decoder_sweep.py `
  --output-root "$env:USERPROFILE\NanoJev\runs\three_representation_decoder_sweep_v1"
```

The result directory contains:

- `fit_curve.csv`: 12 rows per decoder type per layer, with requested **and achieved** effective degrees of freedom, matrix rank, capping flag, MAE, RMSE, max absolute error, and R².
- `mae_curve_layer16.svg`, `mae_curve_layer28.svg`, `rmse_curve_layer16.svg`, `rmse_curve_layer28.svg`: directly viewable plots using achieved effective degrees of freedom on the horizontal axis.
- `predictions.csv`: actual values and reconstructions for all three independent and the shared decoder, at all 12 complexity levels.
- `decoder_models.npz`: saved coefficients for every independent and shared decoder at every complexity level.
- `summary.json`: dataset identity, 12 budgets, per-layer results at the highest budget, the bar-code collision floor, and interpretation notes.

The script reuses each dataset/layer's Gram eigendecomposition across all 12 complexity budgets to avoid redundantly fitting from scratch.

**Important limitation:** Log-Bar has only 119 distinct prompt codes for 253 numbers; therefore the centered Log-Bar activation matrix has rank at most 118. When requested complexity exceeds 99% of the measured rank, the ridge budget is capped to preserve numerical stability. `capacity_capped=true` is emitted, and plots use **achieved**, not requested, effective degrees of freedom. The 128 point remains included for comparison, but it cannot reach 128 effective degrees of freedom for the one-decimal Log-Bar encoding.

All fits are evaluated on the same numbers used for fitting. This experiment describes reconstruction *capacity*, not generalization to unseen numbers. In-sample error generally decreases as decoder capacity increases; differences between encodings at matched achieved effective degrees of freedom are the meaningful comparison.

To customize: `--df-budgets 8,16,24,32,48,64,80,96,112` and `--layers 0,8,16,24,28`.
