# NanoJev — radius-255 zero-line transfer probe

## Question

After certifying the complete `[-127,+127]` flash-card world, how well does the *unchanged* model predict **new** `[-255,+255]` operand pairs that produce the same small signed residuals?

The probe is independent of training. It loads `rungs/radius_127_passed.pt` and configures the original `digit_tokens` neural backbone from the original `config.json`. It never loads an arithmetic executor, invokes an optimizer, or reads the existing TEST split. Python addition is used only to create answer keys for evaluation, never to produce model predictions.

## Run

From `main_computer` with the NanoJev Python environment:

```powershell
& $py -m tools.nanojev_zero_line_radius255_probe `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_radius255_probe_v1" `
    --device cuda `
    --eval-batch-size 256
```

`--output` must be a new separate directory. The runner refuses overwrite or writing anywhere inside/above the ladder source directory. It validates the pass-certified radius-127 checkpoint and verifies the file SHA-256 before and after inference.

## Exact evaluation population

1. **Known anchors**: all ordered operand pairs inside radius 127 whose sum is exactly `-2,-1,0,+1,+2`.
2. **Unseen shell**: *all* ordered operand pairs within radius 255 but outside radius 127 with the same five residuals. This is exhaustive for the specified diagonals, not a 2,048-card estimate. No radius-255 cards are used in training.
3. **Far-residual controls**: all radius-255-shell pairs with sum `-64,-32,-16,+16,+32,+64`. These are separate diagnostic controls, not equivalent-size replacements for each near-residual example.

Each group reports exact sign-and-digit accuracy, decoded-value error, false-zero predictions, accuracy by residual, accuracy by operand magnitude (128–159, 160–191, 192–223, 224–255), and by the sign of operand A.

The probe also measures whether both operand-order permutations are simultaneously correct and whether independently selected compositions in two different magnitude bands both predict the same true residual. The cross-band score is a behavioral pairwise rate, not proof of identical latent representations.

## Output

- `result.json`: structured metrics, configuration and source checkpoint SHA-256.
- `cards.csv`: every tested ordered pair, reference delta, model prediction, exactness, magnitude band and error.
- Console: one `ZERO_LINE_RADIUS255_PROBE=...` summary and read-only confirmation.

## Interpretation

Near-zero performance **on previously unseen magnitudes** is our test for transferable balance representation. A model could still learn heuristics that exploit the structured test distribution, so this probe provides evidence, not proof, of fully general arithmetic. Compare the five residuals separately, including positive, negative and exact zero; a constant-zero predictor cannot pass the positive/negative groups. Evaluate the far-residual controls to expose asymmetries and limitations.

The saved radius-127 model remains unchanged. TEST remains untouched. This does not certify HybridLink or the original Hawk–Dove X/Y computation.
